"""
Поиск минимальной длины накачки для регулярного языка, заданного регулярным
выражением (лемма о накачке для регулярных языков в форме Сипсера).

Пайплайн:
  regex (строка) -> AST -> NFA (Thompson) -> DFA (subset) -> min DFA (Хопкрофт)
  -> точный поиск p_min по определению (см. ``compute_min_pumping_length``).

Определение (Сипсер). p валидно для L, если для всякого w ∈ L с |w| ≥ p
существует разбиение w = xyz, |xy| ≤ p, |y| ≥ 1, такое что xyⁱz ∈ L для
всех i ≥ 0. p_min(L) — наименьшее такое p.

Важно: "в первых p символах слова есть повторное состояние" — ДОСТАТОЧНОЕ,
но не необходимое условие накачиваемости конкретного слова (см. пример
L = a⁺, w = aa, x = ε, y = a, z = a в ТЗ), поэтому наивная эвристика
«кратчайшее слово с повтором состояния» даёт неверный p_min (например,
2 вместо 5 для a*|bbbb). Здесь p_min ищется точно, разбором булевой
комбинации регулярных условий (см. раздел 5), а не эвристикой.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from collections import deque, defaultdict
import argparse
import json

# ============================================================
# 1. Парсер регулярного выражения
# ============================================================
# Грамматика:
#   regex     := union
#   union     := concat ('|' concat)*
#   concat    := star+
#   star      := atom ('*')*
#   atom      := SYMBOL | '(' regex ')' | EPS | EMPTY
#
# Поддерживаются символы ASCII a..z, 0..9 (проверяется по ``isalnum()`` и
# ``isascii()``, так что EPS/EMPTY ниже не конфликтуют с алфавитом символов).
# Конкатенация неявная (без явного оператора).
#
# EPS (язык {ε}) записывается как символ 'ε' либо как пустые скобки '()'
# (пустая конкатенация внутри скобок — то же самое: `parse_concat` возвращает
# `Node('eps')`, когда между '(' и ')' ничего нет). Выбраны оба варианта,
# так как 'ε' удобен в коде/тестах, а '()' — в CLI, где ввести юникод-символ
# не всегда удобно.
#
# EMPTY (язык ∅, пустой язык) записывается как символ '∅' (U+2205). В отличие
# от EPS, для ∅ нет ASCII-альтернативы: язык ∅ не выразим комбинацией
# */|/конкатенации без явного пустого атома (нет оператора дополнения или
# пересечения), поэтому символ обязателен.

@dataclass
class Node:
    kind: str                 # 'sym' | 'eps' | 'empty' | 'concat' | 'union' | 'star'
    sym: Optional[str] = None
    left: Optional['Node'] = None
    right: Optional['Node'] = None
    child: Optional['Node'] = None


class Parser:
    def __init__(self, s: str):
        # Убираем пробелы
        self.s = s.replace(' ', '')
        self.i = 0

    def peek(self) -> Optional[str]:
        return self.s[self.i] if self.i < len(self.s) else None

    def eat(self, c: str):
        assert self.peek() == c, f"Ожидался {c!r}, найдено {self.peek()!r} в позиции {self.i}"
        self.i += 1

    def parse(self) -> Node:
        node = self.parse_union()
        assert self.i == len(self.s), f"Остался хвост: {self.s[self.i:]!r}"
        return node

    def parse_union(self) -> Node:
        left = self.parse_concat()
        while self.peek() == '|':
            self.eat('|')
            right = self.parse_concat()
            left = Node('union', left=left, right=right)
        return left

    def parse_concat(self) -> Node:
        # Читаем одну или несколько star; если ничего — ε
        nodes = []
        while self.peek() is not None and self.peek() not in ')|':
            nodes.append(self.parse_star())
        if not nodes:
            return Node('eps')
        result = nodes[0]
        for n in nodes[1:]:
            result = Node('concat', left=result, right=n)
        return result

    def parse_star(self) -> Node:
        atom = self.parse_atom()
        while self.peek() == '*':
            self.eat('*')
            atom = Node('star', child=atom)
        return atom

    def parse_atom(self) -> Node:
        c = self.peek()
        if c == '(':
            self.eat('(')
            inner = self.parse_union()
            self.eat(')')
            return inner
        if c == 'ε':
            self.i += 1
            return Node('eps')
        if c == '∅':
            self.i += 1
            return Node('empty')
        if c is not None and c.isascii() and c.isalnum():
            self.i += 1
            return Node('sym', sym=c)
        raise ValueError(f"Неожиданный символ {c!r} в позиции {self.i}")


# ============================================================
# 2. NFA (конструкция Томпсона)
# ============================================================

EPS = ''  # пустой переход

@dataclass
class NFA:
    start: int
    accept: int
    # transitions[state][symbol] = set(states); symbol == EPS для ε-перехода
    trans: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    num_states: int = 0

    def new_state(self) -> int:
        s = self.num_states
        self.num_states += 1
        return s

    def add(self, src: int, sym: str, dst: int):
        self.trans[src][sym].add(dst)


def build_nfa(node: Node) -> NFA:
    nfa = NFA(start=-1, accept=-1)

    def build(n: Node) -> tuple[int, int]:
        if n.kind == 'sym':
            s, t = nfa.new_state(), nfa.new_state()
            nfa.add(s, n.sym, t)
            return s, t
        if n.kind == 'eps':
            s, t = nfa.new_state(), nfa.new_state()
            nfa.add(s, EPS, t)
            return s, t
        if n.kind == 'empty':
            # Язык ∅: два несвязанных состояния — путь из s в t не существует,
            # поэтому любая конструкция (concat/union/star) вокруг корректно
            # ведёт себя как с обычным ∅ (star(∅) = {ε}, ∅·X = ∅, ∅|X = X).
            s, t = nfa.new_state(), nfa.new_state()
            return s, t
        if n.kind == 'concat':
            s1, t1 = build(n.left)
            s2, t2 = build(n.right)
            nfa.add(t1, EPS, s2)
            return s1, t2
        if n.kind == 'union':
            s1, t1 = build(n.left)
            s2, t2 = build(n.right)
            s, t = nfa.new_state(), nfa.new_state()
            nfa.add(s, EPS, s1); nfa.add(s, EPS, s2)
            nfa.add(t1, EPS, t); nfa.add(t2, EPS, t)
            return s, t
        if n.kind == 'star':
            s1, t1 = build(n.child)
            s, t = nfa.new_state(), nfa.new_state()
            nfa.add(s, EPS, s1); nfa.add(s, EPS, t)
            nfa.add(t1, EPS, s1); nfa.add(t1, EPS, t)
            return s, t
        raise ValueError(f"Unknown node {n.kind}")

    s, t = build(node)
    nfa.start, nfa.accept = s, t
    return nfa


# ============================================================
# 3. Детерминизация (subset construction)
# ============================================================

def eps_closure(nfa: NFA, states: frozenset[int]) -> frozenset[int]:
    stack = list(states)
    result = set(states)
    while stack:
        s = stack.pop()
        for nxt in nfa.trans[s].get(EPS, ()):
            if nxt not in result:
                result.add(nxt)
                stack.append(nxt)
    return frozenset(result)


def move(nfa: NFA, states: frozenset[int], sym: str) -> frozenset[int]:
    result = set()
    for s in states:
        result |= nfa.trans[s].get(sym, set())
    return frozenset(result)


@dataclass
class DFA:
    start: int
    accepts: set[int]
    # trans[state][symbol] = state  (ПОЛНЫЙ автомат: добавляем мёртвое состояние для недостающих переходов)
    trans: dict = field(default_factory=dict)
    num_states: int = 0
    alphabet: list = field(default_factory=list)


def nfa_to_dfa(nfa: NFA, alphabet: list[str]) -> DFA:
    start_set = eps_closure(nfa, frozenset([nfa.start]))
    state_id = {start_set: 0}
    states = [start_set]
    trans = {0: {}}
    queue = deque([start_set])

    while queue:
        cur = queue.popleft()
        cur_id = state_id[cur]
        for sym in alphabet:
            nxt = eps_closure(nfa, move(nfa, cur, sym))
            if not nxt:
                continue  # без перехода (добавим мёртвое состояние позже)
            if nxt not in state_id:
                state_id[nxt] = len(states)
                states.append(nxt)
                trans[state_id[nxt]] = {}
                queue.append(nxt)
            trans[cur_id][sym] = state_id[nxt]

    # Добавляем мёртвое состояние для полноты (нужно для минимизации Хопкрофта)
    dead = len(states)
    has_dead = False
    for sid in range(len(states)):
        for sym in alphabet:
            if sym not in trans[sid]:
                trans[sid][sym] = dead
                has_dead = True
    if has_dead:
        trans[dead] = {sym: dead for sym in alphabet}

    accepts = {i for i, st in enumerate(states) if nfa.accept in st}
    dfa = DFA(start=0, accepts=accepts, trans=trans,
              num_states=len(states) + (1 if has_dead else 0),
              alphabet=alphabet)
    return dfa


# ============================================================
# 4. Минимизация (алгоритм Хопкрофта, упрощённая версия через разбиение)
# ============================================================

def minimize_dfa(dfa: DFA) -> DFA:
    # Сначала отбрасываем недостижимые
    reachable = set()
    q = deque([dfa.start])
    reachable.add(dfa.start)
    while q:
        s = q.popleft()
        for sym in dfa.alphabet:
            t = dfa.trans[s][sym]
            if t not in reachable:
                reachable.add(t)
                q.append(t)

    # Инициализация разбиения: принимающие / непринимающие (только среди достижимых)
    accepts = dfa.accepts & reachable
    non_accepts = reachable - accepts
    partition = []
    if accepts:
        partition.append(set(accepts))
    if non_accepts:
        partition.append(set(non_accepts))

    # Разбиение до стабилизации
    changed = True
    while changed:
        changed = False
        new_partition = []
        for block in partition:
            # Группируем состояния блока по сигнатуре (блок назначения для каждого символа)
            def block_of(state):
                for i, b in enumerate(partition):
                    if state in b:
                        return i
                return -1  # не должно случиться для достижимых

            sig_groups = defaultdict(set)
            for s in block:
                sig = tuple(block_of(dfa.trans[s][sym]) for sym in dfa.alphabet)
                sig_groups[sig].add(s)
            if len(sig_groups) > 1:
                changed = True
                new_partition.extend(sig_groups.values())
            else:
                new_partition.append(block)
        partition = new_partition

    # Перенумерация: представитель блока = минимальный номер; стартовый блок первым
    def block_idx(state):
        for i, b in enumerate(partition):
            if state in b:
                return i
        return -1

    start_blk = block_idx(dfa.start)
    # Перенумеруем так, чтобы стартовый блок был 0
    order = [start_blk] + [i for i in range(len(partition)) if i != start_blk]
    remap = {old: new for new, old in enumerate(order)}

    new_trans = {}
    new_accepts = set()
    for blk_idx, block in enumerate(partition):
        new_id = remap[blk_idx]
        rep = next(iter(block))
        new_trans[new_id] = {sym: remap[block_idx(dfa.trans[rep][sym])] for sym in dfa.alphabet}
        if block & dfa.accepts:
            new_accepts.add(new_id)

    return DFA(start=0, accepts=new_accepts, trans=new_trans,
               num_states=len(partition), alphabet=dfa.alphabet)


# ============================================================
# 4.5. Вспомогательные функции над DFA
# ============================================================

def collect_symbols(node: Node, acc: Optional[set[str]] = None) -> set[str]:
    """Множество символов алфавита, реально используемых в AST (без ε/∅)."""
    if acc is None:
        acc = set()
    if node.kind == 'sym':
        acc.add(node.sym)
    elif node.kind in ('eps', 'empty'):
        pass
    elif node.kind in ('concat', 'union'):
        collect_symbols(node.left, acc)
        collect_symbols(node.right, acc)
    elif node.kind == 'star':
        collect_symbols(node.child, acc)
    else:
        raise ValueError(f"Unknown node {node.kind}")
    return acc


def regex_to_min_dfa(regex: str, alphabet: Optional[list[str]] = None) -> DFA:
    """Полный пайплайн: regex -> AST -> NFA -> DFA -> минимальный DFA.

    Алфавит по умолчанию — символы, реально встречающиеся в regex (список
    ``alphabet`` можно передать явно, например чтобы сравнивать два языка
    над общим алфавитом).
    """
    tree = Parser(regex).parse()
    if alphabet is None:
        alphabet = sorted(collect_symbols(tree))
    nfa = build_nfa(tree)
    dfa = nfa_to_dfa(nfa, alphabet)
    return minimize_dfa(dfa)


def apply_string(dfa: DFA, state: int, s: str) -> Optional[int]:
    """δ*(state, s) — состояние после чтения строки s из state.

    Возвращает None, если s содержит символ вне алфавита dfa (для всех
    остальных случаев автомат тотален — переход определён всегда, включая
    мёртвое состояние). Внутри модуля вызывается только со строками над
    dfa.alphabet, где None невозможен; публичная ``dfa_accepts`` допускает
    произвольные слова.
    """
    for sym in s:
        row = dfa.trans.get(state)
        if row is None or sym not in row:
            return None
        state = row[sym]
    return state


def dfa_accepts(dfa: DFA, word: str) -> bool:
    """word ∈ L(dfa)? (слово с символом вне алфавита — всегда не в языке)."""
    state = apply_string(dfa, dfa.start, word)
    return state is not None and state in dfa.accepts


def live_states(dfa: DFA) -> set[int]:
    """Состояния, из которых достижимо принимающее."""
    # Обратный граф
    rev = defaultdict(set)
    for s in range(dfa.num_states):
        if s not in dfa.trans:
            continue
        for sym in dfa.alphabet:
            rev[dfa.trans[s][sym]].add(s)
    live = set()
    q = deque(dfa.accepts)
    live |= dfa.accepts
    while q:
        s = q.popleft()
        for p in rev[s]:
            if p not in live:
                live.add(p)
                q.append(p)
    return live


def _enumerate_live_words(dfa: DFA, length: int, live: set[int]):
    """Все u ∈ Σ^length, достижимые из start и не проходящие через
    состояние, из которого accept уже недостижим (такое u не может
    продолжиться никаким z до слова из L — оно не участвует в поиске).

    Возвращает список (u_symbols, state_trace), где state_trace — кортеж
    s_0..s_p состояний вдоль пути (s_0 = dfa.start), в порядке символов
    алфавита (важно для детерминированного выбора свидетеля).
    """
    results: list[tuple[tuple[str, ...], tuple[int, ...]]] = []

    def dfs(state: int, syms: list[str], states: list[int]):
        if len(syms) == length:
            results.append((tuple(syms), tuple(states)))
            return
        for sym in dfa.alphabet:
            nxt = dfa.trans[state][sym]
            if nxt in live:
                dfs(nxt, syms + [sym], states + [nxt])

    dfs(dfa.start, [], [dfa.start])
    return results


def _orbit(dfa: DFA, state: int, y: str) -> set[int]:
    """S = {δ(state, yⁱ) : i ≥ 0} — конечная орбита состояния под накачкой y.

    Считается до первого повтора: последовательность state, δ(state,y),
    δ(state,y²), ... детерминированно рано или поздно зацикливается (не
    более num_states шагов), и все посещённые состояния до первого повтора
    — это и есть искомое множество S.
    """
    seen: set[int] = set()
    cur = state
    while cur not in seen:
        seen.add(cur)
        cur = apply_string(dfa, cur, y)
    return seen


def _splits_r_sets(dfa: DFA, u_syms: tuple[str, ...], u_states: tuple[int, ...]) -> list[frozenset[int]]:
    """Для префикса u (длины p, state_trace u_states длины p+1) —
    список R_split = {δ(t, y′) : t ∈ S} по всем разбиениям u = x·y·y′,
    |y| ≥ 1 (см. модуль docstring и TODO.md §4 для вывода формулы).
    """
    p = len(u_syms)
    splits: list[frozenset[int]] = []
    for i in range(p):            # |x| = i
        for j in range(i + 1, p + 1):   # |xy| = j, |y| = j - i ≥ 1
            y = ''.join(u_syms[i:j])
            y_prime = ''.join(u_syms[j:p])
            s_x = u_states[i]
            S = _orbit(dfa, s_x, y)
            R = frozenset(apply_string(dfa, t, y_prime) for t in S)
            splits.append(R)
    return splits


def _shortest_bad_suffix(dfa: DFA, s_p: int, splits_r: list[frozenset[int]]) -> Optional[str]:
    """Кратчайшее z, такое что u·z плохое для данного u (s_p = δ(q0,u)):

        z ∈ Lang(s_p)  ∩  ⋂_разбиений ⋃_{t∈R_split} complement(Lang(t))

    т.е. u·z ∈ L, и для каждого разбиения найдётся t ∈ R_split с
    t·z ∉ F (иначе это разбиение "спасло" бы слово накачкой).

    Реализовано как BFS по продукт-автомату: один трек для s_p (условие
    "u·z ∈ L") плюс по одному треку на каждое различное состояние,
    встречающееся хоть в одном R_split (условие на разбиения). Раз все
    треки — копии одного и того же DFA, читающие один и тот же z,
    пространство состояний конечно (≤ num_states^(число треков)), и BFS
    гарантированно завершается и находит кратчайший z, если он есть.
    """
    tracked = sorted(set().union(*splits_r)) if splits_r else []

    def ok(main_state: int, r_of: dict) -> bool:
        if main_state not in dfa.accepts:
            return False
        for R in splits_r:
            if not any(r_of[t] not in dfa.accepts for t in R):
                return False
        return True

    init_r = {t: t for t in tracked}
    if ok(s_p, init_r):
        return ""

    start_key = (s_p, tuple(init_r[t] for t in tracked))
    seen = {start_key}
    queue = deque([(start_key, "")])
    while queue:
        (main_state, r_vec), z = queue.popleft()
        for sym in dfa.alphabet:
            new_main = dfa.trans[main_state][sym]
            new_r_vec = tuple(dfa.trans[r][sym] for r in r_vec)
            new_z = z + sym
            r_of = dict(zip(tracked, new_r_vec))
            if ok(new_main, r_of):
                return new_z
            key = (new_main, new_r_vec)
            if key not in seen:
                seen.add(key)
                queue.append((key, new_z))
    return None


def find_bad_word(dfa: DFA, p: int, live: Optional[set[int]] = None) -> Optional[tuple[str, str]]:
    """Ищет «плохое» слово w = u·z длины p + |z|, |u| = p, показывающее, что
    p невалидно: u·z ∈ L, но ни одно разбиение u = x·y·y′ (|y| ≥ 1) не
    накачивает его (см. docstring модуля и TODO.md §4).

    Возвращает (u, z) с наименьшим |z| (среди всех u длины p, для которых
    вообще нашлось плохое z; при равенстве |z| — лексикографически меньшее
    (u, z), для детерминированности), либо None, если p валидно.
    """
    if live is None:
        live = live_states(dfa)
    best: Optional[tuple[str, str]] = None
    for u_syms, u_states in _enumerate_live_words(dfa, p, live):
        splits_r = _splits_r_sets(dfa, u_syms, u_states)
        z = _shortest_bad_suffix(dfa, u_states[-1], splits_r)
        if z is None:
            continue
        u = ''.join(u_syms)
        if best is None or (len(z), u, z) < (len(best[1]), best[0], best[1]):
            best = (u, z)
    return best


@dataclass
class Witness:
    """Свидетель невалидности p = p_min - 1: слово w = u + z ∈ L длины p_min-1
    + |z|, для которого ни одно разбиение u = x·y·y′ не накачивает w."""
    p: int
    u: str
    z: str

    @property
    def word(self) -> str:
        return self.u + self.z

    def explain(self) -> str:
        return (
            f"p={self.p} невалидно: слово {self.word!r} принадлежит L "
            f"(u={self.u!r} — префикс длины {self.p}, z={self.z!r} — остаток), "
            f"но для КАЖДОГО разбиения u = x·y·y′ (|y| ≥ 1) хотя бы одна "
            f"итерация накачки y уводит из L (либо само xy⁰y′z ∉ L). "
            f"Значит, p_min = {self.p + 1}."
        )


@dataclass
class PumpingResult:
    p_min: int
    language_empty: bool
    witness: Optional[Witness]


def compute_min_pumping_length(dfa: DFA) -> PumpingResult:
    """Точный p_min(L(dfa)) по определению Сипсера (см. docstring модуля).

    Перебирает p = 0, 1, 2, ... и для каждого точно проверяет валидность
    (см. ``find_bad_word``). p = n = num_states всегда валидно (для любого
    слова длины ≥ n состояние повторяется в первых n символах — это и даёт
    рабочее разбиение, значит переборный поиск заведомо завершается не
    позже n итераций); поэтому цикл конечен и для бесконечных, и для
    конечных языков — исключений не бросает.
    """
    live = live_states(dfa)
    n = dfa.num_states
    last_bad: Optional[tuple[int, str, str]] = None
    for p in range(n + 1):
        bad = find_bad_word(dfa, p, live=live)
        if bad is None:
            witness = None
            if last_bad is not None:
                witness = Witness(p=last_bad[0], u=last_bad[1], z=last_bad[2])
            return PumpingResult(p_min=p, language_empty=(p == 0), witness=witness)
        last_bad = (p, bad[0], bad[1])
    raise AssertionError(
        f"p_min не найдено в пределах p ≤ n={n} — не должно происходить "
        "(p=n доказуемо валидно для любого DFA)."
    )


def min_pumping_length(regex: str, alphabet: Optional[list[str]] = None) -> PumpingResult:
    """Удобная обёртка: regex -> min DFA -> p_min с полным разбором."""
    dfa = regex_to_min_dfa(regex, alphabet)
    return compute_min_pumping_length(dfa)


# ============================================================
# 6. CLI
# ============================================================

def _print_dfa_table(dfa: DFA) -> None:
    header = "  state | " + " | ".join(f" {s} " for s in dfa.alphabet) + " | accept"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in range(dfa.num_states):
        row = f"  {'*' if s == dfa.start else ' '}{s:>4} | "
        row += " | ".join(f" {dfa.trans[s][sym]} " for sym in dfa.alphabet)
        row += f" |   {'+' if s in dfa.accepts else ' '}"
        print(row)


def main(argv: Optional[list[str]] = None) -> int:
    import sys
    # Регэксп/вывод может содержать символы вне текущей консольной кодовой
    # страницы (например ∅/ε на Windows с cp1251) — переключаемся на UTF-8
    # с заменой нераспечатываемых символов, чтобы CLI не падал на выводе.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="pumping_len.py",
        description="Минимальная длина накачки p_min(L) для регулярного языка, "
                     "заданного регулярным выражением (лемма о накачке, форма Сипсера).",
    )
    parser.add_argument("regex", help="регулярное выражение: символы a-z/0-9, | * (), "
                                       "ε или () для {ε}, ∅ для пустого языка")
    parser.add_argument("--witness", action="store_true",
                         help="напечатать свидетеля невалидности p_min - 1")
    parser.add_argument("--table", action="store_true",
                         help="напечатать таблицу переходов минимального DFA")
    parser.add_argument("--json", action="store_true",
                         help="машиночитаемый вывод (JSON)")
    args = parser.parse_args(argv)

    dfa = regex_to_min_dfa(args.regex)
    result = compute_min_pumping_length(dfa)

    if args.json:
        payload = {
            "regex": args.regex,
            "p_min": result.p_min,
            "language_empty": result.language_empty,
        }
        if result.witness is not None:
            payload["witness"] = {
                "p": result.witness.p,
                "u": result.witness.u,
                "z": result.witness.z,
                "word": result.witness.word,
                "explanation": result.witness.explain(),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.table:
        _print_dfa_table(dfa)
        print()
    print(f"p_min({args.regex!r}) = {result.p_min}")
    if result.language_empty:
        print("Язык пуст: лемма выполняется тривиально для любого p (в т.ч. p=0).")
    if args.witness:
        if result.witness is None:
            print("Свидетель не нужен: p_min - 1 не существует (p_min = 0).")
        else:
            print(result.witness.explain())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())