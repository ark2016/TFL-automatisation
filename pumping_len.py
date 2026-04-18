"""
Поиск минимальной длины накачки для регулярного языка.

Пайплайн:
  regex (строка) -> AST -> NFA (Thompson) -> DFA (subset) -> min DFA (Hopcroft)
  -> поиск минимальной длины накачки через BFS по парам (state, visited_set).

Минимальная длина накачки p определяется как длина кратчайшего слова w из L,
такого что путь по w в минимальном ДКА содержит хотя бы одно повторное состояние
(т.е. хотя бы один цикл). Это и есть минимальное p, для которого выполняется
лемма о накачке.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from collections import deque, defaultdict

# ============================================================
# 1. Парсер регулярного выражения
# ============================================================
# Грамматика:
#   regex     := union
#   union     := concat ('|' concat)*
#   concat    := star+
#   star      := atom ('*')*
#   atom      := SYMBOL | '(' regex ')' | 'ε'
# Поддерживаются символы a..z, 0..9. Конкатенация неявная.

@dataclass
class Node:
    kind: str                 # 'sym' | 'eps' | 'concat' | 'union' | 'star'
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
        if c is not None and (c.isalnum()):
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
# 5. Поиск минимальной длины накачки
# ============================================================
#
# Минимальная p = длина кратчайшего слова w ∈ L, такого что путь по w в min-DFA
# содержит повторное состояние (т.е. цикл).
#
# Важный трюк: нужно исключить из поиска пути, ведущие в "мёртвую ловушку"
# (состояние, из которого нельзя попасть в accept). Иначе BFS найдёт бесполезные циклы.

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


def min_pumping_length(dfa: DFA) -> tuple[int, str]:
    """
    Возвращает (p, пример_слова) — минимальную длину накачки и пример слова
    длины p с циклом на пути.

    BFS по состояниям (node, has_cycle_in_prefix). Как только мы впервые
    посещаем состояние дважды — отмечаем, что цикл был, и продолжаем.
    Принимаем, когда дошли до accept с has_cycle=True.
    """
    live = live_states(dfa)
    if dfa.start not in live:
        raise ValueError("Язык пуст — лемма о накачке неприменима.")

    # BFS по (текущее состояние, множество_посещённых, флаг_цикла)
    # Чтобы ограничить экспоненциальный взрыв, используем оптимизацию:
    # на каждом шаге храним (state, has_cycle) и отдельно отслеживаем "был ли этот
    # состояние посещено в текущем префиксе". Реально BFS по (state, path_states_set, flag)
    # всё равно компактен, т.к. длины слов ограничены ~ числом состояний.
    #
    # Проще: BFS по конфигурациям (state, frozenset_visited). Принимаем, когда
    # state ∈ accepts И при последнем переходе пришли в состояние, уже бывшее в visited
    # (или когда в visited есть дубликат из-за прошлого шага).
    #
    # Оптимизация: вместо полного множества храним (state, has_cycle_flag).
    # Но тогда теряем информацию о том, какие состояния посещались. Для корректности
    # храним полный frozenset, но только до размера ≤ num_states (после чего любой
    # переход даёт цикл). Это ограничивает пространство состояний.

    # Состояние BFS: (current_state, visited_set_or_None, has_cycle)
    # Когда has_cycle=True, visited уже не нужно отслеживать.
    InitState = (dfa.start, frozenset([dfa.start]), False)
    # parent: для восстановления слова
    parent = {InitState: (None, None)}  # state -> (prev_state_key, symbol)
    queue = deque([InitState])

    while queue:
        key = queue.popleft()
        cur, visited, has_cycle = key
        if has_cycle and cur in dfa.accepts:
            # Восстанавливаем слово
            word = []
            k = key
            while parent[k][0] is not None:
                prev_k, sym = parent[k]
                word.append(sym)
                k = prev_k
            word.reverse()
            return len(word), ''.join(word)

        for sym in dfa.alphabet:
            nxt = dfa.trans[cur][sym]
            if nxt not in live:
                continue
            if has_cycle:
                new_key = (nxt, None, True)
            else:
                if nxt in visited:
                    new_key = (nxt, None, True)
                else:
                    new_visited = visited | {nxt}
                    new_key = (nxt, new_visited, False)
            if new_key not in parent:
                parent[new_key] = (key, sym)
                queue.append(new_key)

    # Если не нашли — язык конечен, накачка бесконечно велика (формально L конечен
    # ⇒ можно взять p больше длины самого длинного слова).
    # Но у нас язык бесконечен (иначе BFS нашёл бы цикл, т.к. бесконечный регулярный
    # язык имеет цикл в min-DFA, достижимый из start и ведущий в accept).
    raise ValueError("Цикл, ведущий в accept, не найден — язык конечен.")


# ============================================================
# 6. Прогон
# ============================================================

def analyze(regex: str, alphabet: list[str], label: str = ""):
    print(f"\n{'='*60}")
    print(f"Язык {label}: {regex}")
    print('='*60)

    tree = Parser(regex).parse()
    nfa = build_nfa(tree)
    print(f"NFA: {nfa.num_states} состояний")

    dfa = nfa_to_dfa(nfa, alphabet)
    print(f"DFA (subset): {dfa.num_states} состояний, accepts={sorted(dfa.accepts)}")

    mdfa = minimize_dfa(dfa)
    print(f"Минимальный DFA: {mdfa.num_states} состояний, accepts={sorted(mdfa.accepts)}")

    # Выводим таблицу переходов
    print("Таблица переходов min-DFA:")
    header = "  state | " + " | ".join(f" {s} " for s in mdfa.alphabet) + " | accept"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in range(mdfa.num_states):
        row = f"  {'*' if s == mdfa.start else ' '}{s:>4} | "
        row += " | ".join(f" {mdfa.trans[s][sym]} " for sym in mdfa.alphabet)
        row += f" |   {'+' if s in mdfa.accepts else ' '}"
        print(row)

    try:
        p, example = min_pumping_length(mdfa)
        print(f"\n>>> Минимальная длина накачки p = {p}")
        print(f">>> Пример слова длины {p} с циклом: {example!r}")
    except ValueError as e:
        print(f"\n>>> {e}")


if __name__ == '__main__':
    # Задача: ((a|b)*bb(a|b)(a|b)) | (b(abaa)*|abb*)*
    regex_full = '((a|b)*bb(a|b)(a|b))|(b(abaa)*|abb*)*'
    analyze(regex_full, ['a', 'b'], 'полный')

    # Разберём по частям для наглядности
    analyze('(a|b)*bb(a|b)(a|b)', ['a', 'b'], 'часть A')
    analyze('(b(abaa)*|abb*)*', ['a', 'b'], 'часть B')