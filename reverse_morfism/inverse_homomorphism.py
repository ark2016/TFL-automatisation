"""
Универсальный алгоритм вычисления обратного гомоморфизма для регулярных языков.

Теоретическая основа:
    Дан регулярный язык L ⊆ Σ* и гомоморфизм h: Δ → Σ*.
    Требуется построить автомат, распознающий язык:

        h⁻¹(L) = {w ∈ Δ* | h(w) ∈ L}

    Теорема (Замкнутость): Класс регулярных языков замкнут относительно
    операции обратного гомоморфизма.

Алгоритм:
    1. Построить тотальный ДКА M = (Q, Σ, δ, q₀, F) для языка L
    2. Построить новый ДКА M' = (Q, Δ, δ', q₀, F), где:
       δ'(q, a) = δ̂(q, h(a))  — расширенная функция переходов
    3. Минимизировать результат
"""

from typing import Dict, Set, List, Tuple, Optional, FrozenSet
from dataclasses import dataclass, field
from collections import deque
import re


# =============================================================================
# Структуры данных для автоматов
# =============================================================================

@dataclass
class NFA:
    """
    Недетерминированный конечный автомат (ε-NFA).

    Формально: M = (Q, Σ, δ, q₀, F), где:
        - Q: множество состояний
        - Σ: алфавит (включая ε = '')
        - δ: Q × (Σ ∪ {ε}) → P(Q) — функция переходов
        - q₀: начальное состояние
        - F: множество финальных состояний
    """
    states: Set[int]
    alphabet: Set[str]
    transitions: Dict[Tuple[int, str], Set[int]]  # (state, symbol) → {states}
    start_state: int
    accept_states: Set[int]

    def get_transitions(self, state: int, symbol: str) -> Set[int]:
        """Получить множество состояний по переходу δ(q, a)."""
        return self.transitions.get((state, symbol), set())


@dataclass
class DFA:
    """
    Детерминированный конечный автомат (ДКА).

    Формально: M = (Q, Σ, δ, q₀, F), где:
        - Q: множество состояний
        - Σ: алфавит
        - δ: Q × Σ → Q — тотальная функция переходов
        - q₀: начальное состояние
        - F: множество финальных состояний
    """
    states: Set[int]
    alphabet: Set[str]
    transitions: Dict[Tuple[int, str], int]  # (state, symbol) → state
    start_state: int
    accept_states: Set[int]

    def get_transition(self, state: int, symbol: str) -> Optional[int]:
        """Получить состояние по переходу δ(q, a)."""
        return self.transitions.get((state, symbol))

    def extended_transition(self, state: int, word: str) -> int:
        """
        Расширенная функция переходов δ̂: Q × Σ* → Q

        Определение (индуктивное):
            δ̂(q, ε) = q
            δ̂(q, wa) = δ(δ̂(q, w), a)  для w ∈ Σ*, a ∈ Σ

        Свойство: δ̂(q, w₁w₂) = δ̂(δ̂(q, w₁), w₂)
        """
        current = state
        for symbol in word:
            next_state = self.get_transition(current, symbol)
            if next_state is None:
                raise ValueError(f"ДКА не тотален: нет перехода δ({current}, '{symbol}')")
            current = next_state
        return current

    def accepts(self, word: str) -> bool:
        """Проверить, принимает ли автомат слово w."""
        try:
            final_state = self.extended_transition(self.start_state, word)
            return final_state in self.accept_states
        except ValueError:
            return False


# =============================================================================
# Этап 1: Построение NFA из регулярного выражения (Thompson Construction)
# =============================================================================

class ThompsonConstruction:
    """
    Построение ε-NFA из регулярного выражения методом Томпсона.

    Поддерживаемый синтаксис:
        - a, b, c, ... — одиночные символы
        - ε или eps — пустое слово
        - ∅ или empty — пустой язык
        - (R) — группировка
        - R* — замыкание Клини (ноль или более повторений)
        - R+ — положительное замыкание (одно или более)
        - R? — опциональность (ноль или одно вхождение)
        - RS — конкатенация
        - R|S — объединение (альтернатива)

    Свойства конструкции Томпсона:
        - Ровно одно начальное состояние (без входящих рёбер)
        - Ровно одно финальное состояние (без исходящих рёбер)
        - Не более 2|r| состояний, где |r| — длина регулярного выражения
        - Каждое состояние имеет ≤2 исходящих ε-перехода или 1 переход по символу
    """

    def __init__(self):
        self.state_counter = 0

    def new_state(self) -> int:
        """Создать новое уникальное состояние."""
        state = self.state_counter
        self.state_counter += 1
        return state

    def build(self, regex: str) -> NFA:
        """
        Построить ε-NFA из регулярного выражения.

        Args:
            regex: Регулярное выражение в инфиксной нотации

        Returns:
            NFA: Недетерминированный конечный автомат
        """
        self.state_counter = 0
        tokens = self._tokenize(regex)
        postfix = self._to_postfix(tokens)
        return self._build_from_postfix(postfix)

    def _tokenize(self, regex: str) -> List[str]:
        """
        Токенизация регулярного выражения.

        Добавляет явные операторы конкатенации '.' между:
            - символом и символом: ab → a.b
            - символом и '(': a( → a.(
            - ')' и символом: )a → ).a
            - ')' и '(': )( → ).(
            - '*', '+', '?' и символом: a*b → a*.b
            - '*', '+', '?' и '(': a*( → a*.(
        """
        tokens = []
        i = 0

        while i < len(regex):
            char = regex[i]

            # Проверка на специальные последовательности
            if regex[i:i+3] == 'eps':
                tokens.append('ε')
                i += 3
            elif regex[i:i+5] == 'empty':
                tokens.append('∅')
                i += 5
            elif char in 'εϵ':  # Различные варианты написания эпсилон
                tokens.append('ε')
                i += 1
            elif char == '∅':
                tokens.append('∅')
                i += 1
            elif char == '\\' and i + 1 < len(regex):
                # Экранирование специальных символов
                tokens.append(regex[i + 1])
                i += 2
            elif char in '()|*+?':
                tokens.append(char)
                i += 1
            elif char.isspace():
                i += 1
            else:
                tokens.append(char)
                i += 1

        # Добавление явных операторов конкатенации
        result = []
        for i, token in enumerate(tokens):
            result.append(token)
            if i + 1 < len(tokens):
                next_token = tokens[i + 1]
                # После операнда или закрывающих символов
                if token not in '(|' and next_token not in ')|*+?':
                    result.append('.')  # Оператор конкатенации

        return result

    def _to_postfix(self, tokens: List[str]) -> List[str]:
        """
        Преобразование из инфиксной в постфиксную нотацию (алгоритм Shunting Yard).

        Приоритеты операторов (от высокого к низкому):
            1. * + ? (унарные, постфиксные)
            2. . (конкатенация)
            3. | (объединение)
        """
        output = []
        operator_stack = []

        # Приоритеты: чем выше число, тем выше приоритет
        precedence = {'|': 1, '.': 2, '*': 3, '+': 3, '?': 3}

        for token in tokens:
            if token == '(':
                operator_stack.append(token)
            elif token == ')':
                while operator_stack and operator_stack[-1] != '(':
                    output.append(operator_stack.pop())
                if operator_stack:
                    operator_stack.pop()  # Удалить '('
            elif token in precedence:
                while (operator_stack and
                       operator_stack[-1] != '(' and
                       operator_stack[-1] in precedence and
                       precedence[operator_stack[-1]] >= precedence[token]):
                    output.append(operator_stack.pop())
                operator_stack.append(token)
            else:
                # Операнд (символ алфавита, ε или ∅)
                output.append(token)

        while operator_stack:
            output.append(operator_stack.pop())

        return output

    def _build_from_postfix(self, postfix: List[str]) -> NFA:
        """
        Построение NFA из постфиксной записи методом стека.

        Базовые случаи:
            - Символ a: создаёт 2 состояния с переходом по a
            - ε: создаёт 2 состояния с ε-переходом
            - ∅: создаёт 2 состояния без переходов

        Индуктивные случаи (композиция автоматов):
            - R|S: объединение — добавляем новые начальное и конечное состояния
            - RS: конкатенация — соединяем конец R с началом S
            - R*: замыкание — добавляем петлю и обход
        """
        stack: List[Tuple[int, int, Set[int], Set[str], Dict]] = []
        # Элемент стека: (start, accept, states, alphabet, transitions)

        all_states = set()
        all_transitions = {}
        alphabet = set()

        for token in postfix:
            if token == '.':  # Конкатенация RS
                # Формула: L(RS) = {uv | u ∈ L(R), v ∈ L(S)}
                s2, a2, _, _, _ = stack.pop()
                s1, a1, _, _, _ = stack.pop()
                # Соединяем accept состояние R с start состоянием S ε-переходом
                all_transitions.setdefault((a1, ''), set()).add(s2)
                stack.append((s1, a2, set(), set(), {}))

            elif token == '|':  # Объединение R|S
                # Формула: L(R|S) = L(R) ∪ L(S)
                s2, a2, _, _, _ = stack.pop()
                s1, a1, _, _, _ = stack.pop()

                # Новые начальное и конечное состояния
                new_start = self.new_state()
                new_accept = self.new_state()
                all_states.add(new_start)
                all_states.add(new_accept)

                # ε-переходы от нового начала к началам R и S
                all_transitions.setdefault((new_start, ''), set()).update({s1, s2})
                # ε-переходы от концов R и S к новому концу
                all_transitions.setdefault((a1, ''), set()).add(new_accept)
                all_transitions.setdefault((a2, ''), set()).add(new_accept)

                stack.append((new_start, new_accept, set(), set(), {}))

            elif token == '*':  # Замыкание Клини R*
                # Формула: L(R*) = {ε} ∪ L(R) ∪ L(RR) ∪ L(RRR) ∪ ...
                s, a, _, _, _ = stack.pop()

                new_start = self.new_state()
                new_accept = self.new_state()
                all_states.add(new_start)
                all_states.add(new_accept)

                # ε-переход: новый старт → старый старт (для вхождения в R)
                all_transitions.setdefault((new_start, ''), set()).add(s)
                # ε-переход: новый старт → новый accept (для принятия ε)
                all_transitions.setdefault((new_start, ''), set()).add(new_accept)
                # ε-переход: старый accept → старый start (петля для повторений)
                all_transitions.setdefault((a, ''), set()).add(s)
                # ε-переход: старый accept → новый accept (выход)
                all_transitions.setdefault((a, ''), set()).add(new_accept)

                stack.append((new_start, new_accept, set(), set(), {}))

            elif token == '+':  # Положительное замыкание R+
                # Формула: L(R+) = L(R) · L(R*) = L(R) ∪ L(RR) ∪ L(RRR) ∪ ...
                # Эквивалентно: R+ = RR*
                s, a, _, _, _ = stack.pop()

                new_start = self.new_state()
                new_accept = self.new_state()
                all_states.add(new_start)
                all_states.add(new_accept)

                # ε-переход: новый старт → старый старт
                all_transitions.setdefault((new_start, ''), set()).add(s)
                # ε-переход: старый accept → старый start (петля)
                all_transitions.setdefault((a, ''), set()).add(s)
                # ε-переход: старый accept → новый accept
                all_transitions.setdefault((a, ''), set()).add(new_accept)

                stack.append((new_start, new_accept, set(), set(), {}))

            elif token == '?':  # Опциональность R?
                # Формула: L(R?) = {ε} ∪ L(R)
                s, a, _, _, _ = stack.pop()

                new_start = self.new_state()
                new_accept = self.new_state()
                all_states.add(new_start)
                all_states.add(new_accept)

                # ε-переход: новый старт → старый старт
                all_transitions.setdefault((new_start, ''), set()).add(s)
                # ε-переход: новый старт → новый accept (для ε)
                all_transitions.setdefault((new_start, ''), set()).add(new_accept)
                # ε-переход: старый accept → новый accept
                all_transitions.setdefault((a, ''), set()).add(new_accept)

                stack.append((new_start, new_accept, set(), set(), {}))

            elif token == 'ε':  # Пустое слово
                # Формула: L(ε) = {ε}
                start = self.new_state()
                accept = self.new_state()
                all_states.add(start)
                all_states.add(accept)
                # ε-переход от start к accept
                all_transitions.setdefault((start, ''), set()).add(accept)
                stack.append((start, accept, set(), set(), {}))

            elif token == '∅':  # Пустой язык
                # Формула: L(∅) = {} (пустое множество)
                start = self.new_state()
                accept = self.new_state()
                all_states.add(start)
                all_states.add(accept)
                # Нет переходов — язык пуст
                stack.append((start, accept, set(), set(), {}))

            else:  # Одиночный символ
                # Формула: L(a) = {a}
                start = self.new_state()
                accept = self.new_state()
                all_states.add(start)
                all_states.add(accept)
                alphabet.add(token)
                # Переход по символу от start к accept
                all_transitions.setdefault((start, token), set()).add(accept)
                stack.append((start, accept, set(), set(), {}))

        if not stack:
            # Пустое выражение — принимает только ε
            start = self.new_state()
            accept = self.new_state()
            all_states.add(start)
            all_states.add(accept)
            all_transitions.setdefault((start, ''), set()).add(accept)
            return NFA(all_states, alphabet, all_transitions, start, {accept})

        final_start, final_accept, _, _, _ = stack.pop()
        all_states.add(final_start)
        all_states.add(final_accept)

        return NFA(all_states, alphabet, all_transitions, final_start, {final_accept})


# =============================================================================
# Этап 2: Преобразование NFA в DFA (Subset Construction)
# =============================================================================

class SubsetConstruction:
    """
    Преобразование ε-NFA в эквивалентный DFA методом подмножеств.

    Теорема: Для любого NFA N существует DFA D такой, что L(D) = L(N).

    Идея: Состояния DFA — это подмножества состояний NFA.
    Переход δ_DFA(S, a) = ε-closure(⋃_{q∈S} δ_NFA(q, a))

    Сложность: O(2^n · |Σ|) в худшем случае, где n = |Q_NFA|
    """

    @staticmethod
    def epsilon_closure(nfa: NFA, states: Set[int]) -> FrozenSet[int]:
        """
        Вычисление ε-замыкания множества состояний.

        Определение: ε-closure(S) — множество всех состояний, достижимых
        из S по любому количеству ε-переходов (включая 0).

        Формула: ε-closure(S) = S ∪ ⋃_{q∈S, p∈δ(q,ε)} ε-closure({p})

        Args:
            nfa: Исходный NFA
            states: Множество состояний для вычисления замыкания

        Returns:
            FrozenSet[int]: ε-замыкание (неизменяемое для использования в словарях)
        """
        closure = set(states)
        stack = list(states)

        while stack:
            state = stack.pop()
            # Получаем все состояния, достижимые по ε-переходу
            epsilon_transitions = nfa.get_transitions(state, '')
            for next_state in epsilon_transitions:
                if next_state not in closure:
                    closure.add(next_state)
                    stack.append(next_state)

        return frozenset(closure)

    @staticmethod
    def convert(nfa: NFA) -> DFA:
        """
        Преобразование NFA в DFA методом подмножеств.

        Алгоритм:
            1. Начальное состояние DFA = ε-closure({q₀})
            2. Для каждого нового состояния S и символа a:
               δ_DFA(S, a) = ε-closure(move(S, a))
               где move(S, a) = ⋃_{q∈S} δ_NFA(q, a)
            3. S ∈ F_DFA ⟺ S ∩ F_NFA ≠ ∅

        Returns:
            DFA: Эквивалентный детерминированный автомат
        """
        # Начальное состояние DFA — ε-замыкание начального состояния NFA
        start_closure = SubsetConstruction.epsilon_closure(nfa, {nfa.start_state})

        # Отображение: подмножество → номер состояния в DFA
        state_map: Dict[FrozenSet[int], int] = {start_closure: 0}
        dfa_states = {0}
        dfa_transitions = {}
        dfa_accept = set()

        # Проверяем, является ли начальное состояние принимающим
        if start_closure & nfa.accept_states:
            dfa_accept.add(0)

        # BFS по подмножествам
        queue = deque([start_closure])
        state_counter = 1

        while queue:
            current_subset = queue.popleft()
            current_dfa_state = state_map[current_subset]

            # Для каждого символа алфавита вычисляем переход
            for symbol in nfa.alphabet:
                # move(S, a) = ⋃_{q∈S} δ_NFA(q, a)
                move_result = set()
                for nfa_state in current_subset:
                    move_result.update(nfa.get_transitions(nfa_state, symbol))

                if not move_result:
                    continue  # Нет перехода — оставляем неопределённым (добавим sink позже)

                # Вычисляем ε-замыкание результата
                next_closure = SubsetConstruction.epsilon_closure(nfa, move_result)

                # Добавляем новое состояние, если ещё не видели
                if next_closure not in state_map:
                    state_map[next_closure] = state_counter
                    dfa_states.add(state_counter)
                    queue.append(next_closure)

                    # Проверяем, является ли принимающим
                    if next_closure & nfa.accept_states:
                        dfa_accept.add(state_counter)

                    state_counter += 1

                # Добавляем переход
                dfa_transitions[(current_dfa_state, symbol)] = state_map[next_closure]

        return DFA(dfa_states, nfa.alphabet, dfa_transitions, 0, dfa_accept)


# =============================================================================
# Этап 3: Приведение DFA к тотальному виду
# =============================================================================

def make_total_dfa(dfa: DFA) -> DFA:
    """
    Приведение DFA к тотальному виду добавлением sink state.

    Тотальный DFA: функция δ: Q × Σ → Q определена для всех пар (q, a).

    Процедура:
        1. Добавить мёртвое состояние q_sink ∉ F
        2. Для всех неопределённых переходов: δ(q, a) = q_sink
        3. Добавить самопетли: δ(q_sink, a) = q_sink для всех a ∈ Σ

    Args:
        dfa: Исходный (возможно, частичный) DFA

    Returns:
        DFA: Тотальный DFA
    """
    # Находим номер для sink state
    sink_state = max(dfa.states) + 1 if dfa.states else 0
    need_sink = False

    new_transitions = dict(dfa.transitions)
    new_states = set(dfa.states)

    # Проверяем все пары (состояние, символ)
    for state in dfa.states:
        for symbol in dfa.alphabet:
            if (state, symbol) not in new_transitions:
                new_transitions[(state, symbol)] = sink_state
                need_sink = True

    # Если нужен sink state, добавляем его и самопетли
    if need_sink:
        new_states.add(sink_state)
        for symbol in dfa.alphabet:
            new_transitions[(sink_state, symbol)] = sink_state

    return DFA(new_states, dfa.alphabet, new_transitions, dfa.start_state, dfa.accept_states)


# =============================================================================
# Этап 4: Построение DFA для h⁻¹(L) (Обратный гомоморфизм)
# =============================================================================

@dataclass
class Homomorphism:
    """
    Гомоморфизм h: Δ → Σ*

    Определение: h — гомоморфизм, если h(uv) = h(u)h(v) для всех u, v ∈ Δ*.

    Следствие: Гомоморфизм полностью определяется образами букв:
        h(a₁a₂...aₙ) = h(a₁)h(a₂)...h(aₙ)

    Классификация:
        - Нестирающий: ∀a ∈ Δ: h(a) ≠ ε (все образы непустые)
        - Стирающий: ∃a ∈ Δ: h(a) = ε (некоторые образы пустые)

    Атрибуты:
        mapping: Dict[str, str] — образы букв {a ↦ h(a) | a ∈ Δ}
    """
    mapping: Dict[str, str]

    @property
    def domain(self) -> Set[str]:
        """Область определения Δ."""
        return set(self.mapping.keys())

    @property
    def codomain_alphabet(self) -> Set[str]:
        """Алфавит кодомена Σ (символы, встречающиеся в образах)."""
        alphabet = set()
        for image in self.mapping.values():
            alphabet.update(image)
        return alphabet

    def apply(self, word: str) -> str:
        """
        Применить гомоморфизм к слову.

        Формула: h(a₁a₂...aₙ) = h(a₁)h(a₂)...h(aₙ)
        """
        result = []
        for char in word:
            if char not in self.mapping:
                raise ValueError(f"Символ '{char}' не в области определения гомоморфизма")
            result.append(self.mapping[char])
        return ''.join(result)

    def is_erasing(self) -> bool:
        """Проверить, является ли гомоморфизм стирающим."""
        return any(image == '' for image in self.mapping.values())

    def __str__(self) -> str:
        items = [f"h({k}) = '{v}'" if v else f"h({k}) = ε"
                 for k, v in sorted(self.mapping.items())]
        return "Гомоморфизм: " + ", ".join(items)


def apply_inverse_homomorphism(dfa: DFA, h: Homomorphism) -> DFA:
    """
    Построение DFA для h⁻¹(L) — прообраза языка L относительно гомоморфизма h.

    Теорема: Если L регулярен и h: Δ → Σ* — гомоморфизм, то h⁻¹(L) регулярен.

    Определение прообраза:
        h⁻¹(L) = {w ∈ Δ* | h(w) ∈ L}

    Конструкция нового DFA M' = (Q, Δ, δ', q₀, F):
        - Множество состояний Q — то же, что у исходного DFA M
        - Алфавит — область определения гомоморфизма Δ
        - Начальное состояние q₀ — то же
        - Финальные состояния F — те же
        - Функция переходов:

            δ'(q, a) = δ̂(q, h(a))

        где δ̂ — расширенная функция переходов:
            δ̂(q, ε) = q
            δ̂(q, wa) = δ(δ̂(q, w), a)

    Частный случай (стирающий гомоморфизм):
        Если h(a) = ε, то δ'(q, a) = δ̂(q, ε) = q (самопетля)

    Args:
        dfa: Тотальный DFA для языка L
        h: Гомоморфизм h: Δ → Σ*

    Returns:
        DFA: Автомат для языка h⁻¹(L)
    """
    # Новый алфавит — область определения гомоморфизма
    new_alphabet = h.domain
    new_transitions = {}

    # Для каждого состояния q и символа a ∈ Δ вычисляем δ'(q, a) = δ̂(q, h(a))
    for state in dfa.states:
        for symbol in new_alphabet:
            # Получаем образ символа h(a)
            image = h.mapping[symbol]

            # Вычисляем δ̂(q, h(a)) — расширенную функцию переходов
            # Последовательно применяем переходы для каждого символа в h(a)
            final_state = dfa.extended_transition(state, image)

            new_transitions[(state, symbol)] = final_state

    return DFA(
        states=dfa.states.copy(),
        alphabet=new_alphabet,
        transitions=new_transitions,
        start_state=dfa.start_state,
        accept_states=dfa.accept_states.copy()
    )


def _extend_dfa_alphabet(dfa: DFA, new_symbols: Set[str]) -> DFA:
    """
    Расширить алфавит DFA новыми символами.

    Все новые символы ведут в sink state из любого состояния.
    Это нужно когда образы гомоморфизма содержат символы вне алфавита DFA.

    Args:
        dfa: Исходный DFA
        new_symbols: Множество новых символов для добавления

    Returns:
        DFA: DFA с расширенным алфавитом
    """
    if not new_symbols:
        return dfa

    # Находим или создаём sink state
    sink_state = max(dfa.states) + 1 if dfa.states else 0

    new_states = set(dfa.states)
    new_states.add(sink_state)

    new_alphabet = dfa.alphabet | new_symbols
    new_transitions = dict(dfa.transitions)

    # Все новые символы ведут в sink из всех состояний
    for state in dfa.states:
        for symbol in new_symbols:
            new_transitions[(state, symbol)] = sink_state

    # Sink state имеет самопетли по всем символам
    for symbol in new_alphabet:
        new_transitions[(sink_state, symbol)] = sink_state

    return DFA(new_states, new_alphabet, new_transitions, dfa.start_state, dfa.accept_states.copy())


# =============================================================================
# Этап 5: Оптимизация — удаление недостижимых и минимизация
# =============================================================================

def remove_unreachable_states(dfa: DFA) -> DFA:
    """
    Удаление недостижимых состояний из DFA.

    Состояние q достижимо, если ∃w ∈ Σ*: δ̂(q₀, w) = q.

    Алгоритм: BFS из начального состояния.

    Args:
        dfa: Исходный DFA

    Returns:
        DFA: DFA без недостижимых состояний
    """
    # BFS для нахождения достижимых состояний
    reachable = {dfa.start_state}
    queue = deque([dfa.start_state])

    while queue:
        state = queue.popleft()
        for symbol in dfa.alphabet:
            next_state = dfa.get_transition(state, symbol)
            if next_state is not None and next_state not in reachable:
                reachable.add(next_state)
                queue.append(next_state)

    # Фильтруем состояния и переходы
    new_transitions = {
        (s, a): t for (s, a), t in dfa.transitions.items()
        if s in reachable and t in reachable
    }

    return DFA(
        states=reachable,
        alphabet=dfa.alphabet,
        transitions=new_transitions,
        start_state=dfa.start_state,
        accept_states=dfa.accept_states & reachable
    )


def minimize_dfa(dfa: DFA) -> DFA:
    """
    Минимизация DFA алгоритмом разбиения (упрощённый Хопкрофт).

    Теорема: Для любого DFA существует единственный (с точностью до изоморфизма)
    минимальный DFA, распознающий тот же язык.

    Два состояния p и q эквивалентны (p ≡ q), если:
        ∀w ∈ Σ*: (δ̂(p, w) ∈ F ⟺ δ̂(q, w) ∈ F)

    Алгоритм разбиения:
        1. Начальное разбиение: π = {F, Q \\ F}
        2. Итеративное уточнение: разделяем блоки, различимые по переходам
        3. Каждый блок финального разбиения — состояние минимального DFA

    Сложность: O(n² · |Σ|), где n = |Q|
    (Алгоритм Хопкрофта даёт O(n log n · |Σ|), но сложнее в реализации)

    Args:
        dfa: DFA для минимизации (должен быть тотальным, без недостижимых)

    Returns:
        DFA: Минимальный эквивалентный DFA
    """
    # Сначала удаляем недостижимые состояния
    dfa = remove_unreachable_states(dfa)

    if not dfa.states:
        # Пустой автомат
        return DFA({0}, dfa.alphabet, {}, 0, set())

    # Начальное разбиение: финальные и нефинальные состояния
    non_accept = dfa.states - dfa.accept_states

    if not dfa.accept_states:
        partition = [non_accept] if non_accept else []
    elif not non_accept:
        partition = [dfa.accept_states]
    else:
        partition = [dfa.accept_states, non_accept]

    # Создаём отображение: состояние → индекс блока
    def get_partition_map():
        state_to_block = {}
        for i, block in enumerate(partition):
            for state in block:
                state_to_block[state] = i
        return state_to_block

    # Итеративное уточнение разбиения
    changed = True
    while changed:
        changed = False
        state_to_block = get_partition_map()
        new_partition = []

        for block in partition:
            if len(block) <= 1:
                new_partition.append(block)
                continue

            # Пытаемся разделить блок
            # Группируем состояния по сигнатуре (кортеж индексов блоков переходов)
            signatures = {}
            for state in block:
                sig = tuple(
                    state_to_block.get(dfa.get_transition(state, a), -1)
                    for a in sorted(dfa.alphabet)
                )
                if sig not in signatures:
                    signatures[sig] = set()
                signatures[sig].add(state)

            # Если получилось более одной группы — блок разделён
            if len(signatures) > 1:
                changed = True
                new_partition.extend(signatures.values())
            else:
                new_partition.append(block)

        partition = new_partition

    # Строим минимальный DFA
    # Находим блок, содержащий начальное состояние
    state_to_block = get_partition_map()

    # Перенумеруем блоки так, чтобы начальный был 0
    start_block = state_to_block[dfa.start_state]
    block_map = {start_block: 0}
    next_id = 1
    for i in range(len(partition)):
        if i != start_block:
            block_map[i] = next_id
            next_id += 1

    # Строим переходы минимального DFA
    min_states = set(range(len(partition)))
    min_transitions = {}
    min_accept = set()

    for i, block in enumerate(partition):
        new_state = block_map[i]
        representative = next(iter(block))  # Берём любой элемент блока

        # Проверяем, является ли блок принимающим
        if representative in dfa.accept_states:
            min_accept.add(new_state)

        # Строим переходы
        for symbol in dfa.alphabet:
            target = dfa.get_transition(representative, symbol)
            if target is not None:
                target_block = state_to_block[target]
                min_transitions[(new_state, symbol)] = block_map[target_block]

    return DFA(min_states, dfa.alphabet, min_transitions, 0, min_accept)


# =============================================================================
# Преобразование DFA в регулярное выражение (State Elimination)
# =============================================================================

def dfa_to_regex(dfa: DFA) -> str:
    """
    Преобразование DFA в регулярное выражение методом исключения состояний.

    Алгоритм State Elimination:
        1. Создать GNFA (Generalized NFA) с рёбрами-регулярками
        2. Добавить новое начальное состояние q_start с ε-переходом
        3. Добавить новое конечное состояние q_accept с ε-переходами
        4. Последовательно исключать состояния, обновляя рёбра

    При исключении состояния q_rip:
        Для всех пар (q_i, q_j) обновляем:
        R(q_i, q_j) := R(q_i, q_j) | R(q_i, q_rip) · R(q_rip, q_rip)* · R(q_rip, q_j)

    Оптимизация: dead states (не лежащие на пути start -> accept) исключаются
    первыми, что предотвращает экспоненциальный рост промежуточных регулярок.

    Args:
        dfa: DFA для преобразования

    Returns:
        str: Регулярное выражение для языка L(dfa)
    """
    if not dfa.states:
        return "{}"

    if not dfa.accept_states:
        return "{}"

    # Специальный случай: только начальное состояние и оно принимающее
    if len(dfa.states) == 1 and dfa.start_state in dfa.accept_states:
        loops = []
        for symbol in sorted(dfa.alphabet):
            if dfa.get_transition(dfa.start_state, symbol) == dfa.start_state:
                loops.append(symbol)
        if not loops:
            return "eps"
        elif len(loops) == 1:
            return f"{loops[0]}*"
        else:
            return f"({'+'.join(loops)})*"

    # --- Оптимизация: определяем порядок исключения состояний ---
    # Сначала исключаем dead states (не ведущие к accept) — они дают None
    # и не раздувают промежуточные регулярки.

    # 1. Находим состояния, из которых достижим accept (обратный BFS)
    reverse_adj: Dict[int, Set[int]] = {s: set() for s in dfa.states}
    for (src, sym), dst in dfa.transitions.items():
        if dst is not None:
            reverse_adj[dst].add(src)

    can_reach_accept: Set[int] = set()
    queue_back = list(dfa.accept_states)
    can_reach_accept.update(dfa.accept_states)
    while queue_back:
        s = queue_back.pop()
        for prev in reverse_adj.get(s, set()):
            if prev not in can_reach_accept:
                can_reach_accept.add(prev)
                queue_back.append(prev)

    # 2. Находим состояния, достижимые из start (прямой BFS)
    reachable_from_start: Set[int] = set()
    queue_fwd = [dfa.start_state]
    reachable_from_start.add(dfa.start_state)
    while queue_fwd:
        s = queue_fwd.pop()
        for sym in dfa.alphabet:
            nxt = dfa.get_transition(s, sym)
            if nxt is not None and nxt not in reachable_from_start:
                reachable_from_start.add(nxt)
                queue_fwd.append(nxt)

    # Живые состояния: достижимы из start И ведут к accept
    live_states = reachable_from_start & can_reach_accept
    dead_states = set(dfa.states) - live_states

    # Порядок исключения: сначала dead, потом live (кроме start и accept)
    states = sorted(dfa.states)
    n = len(states)
    state_idx = {s: i for i, s in enumerate(states)}

    elimination_order = []
    for s in states:
        if s in dead_states:
            elimination_order.append(state_idx[s])
    for s in states:
        if s in live_states:
            elimination_order.append(state_idx[s])

    # --- Строим матрицу GNFA ---
    # Индексы: 0..n-1 — состояния DFA, n — новый start, n+1 — новый accept
    R = [[None for _ in range(n + 2)] for _ in range(n + 2)]

    for i, src in enumerate(states):
        transitions_to: Dict[int, List[str]] = {}
        for symbol in dfa.alphabet:
            dst = dfa.get_transition(src, symbol)
            if dst is not None:
                j = state_idx[dst]
                if j not in transitions_to:
                    transitions_to[j] = []
                transitions_to[j].append(symbol)

        for j, symbols in transitions_to.items():
            if len(symbols) == 1:
                R[i][j] = symbols[0]
            else:
                R[i][j] = f"({'+'.join(sorted(symbols))})"

    # Новое начальное состояние (индекс n)
    start_idx = state_idx[dfa.start_state]
    R[n][start_idx] = "eps"

    # Новое конечное состояние (индекс n+1)
    for accept in dfa.accept_states:
        acc_idx = state_idx[accept]
        R[acc_idx][n + 1] = "eps"

    # --- Исключаем состояния в оптимальном порядке ---
    eliminated = set()
    for rip in elimination_order:
        eliminated.add(rip)
        for i in range(n + 2):
            if i in eliminated:
                continue
            for j in range(n + 2):
                if j in eliminated:
                    continue

                # R[i][j] := R[i][j] | R[i][rip] . R[rip][rip]* . R[rip][j]
                r_i_rip = R[i][rip]
                r_rip_j = R[rip][j]

                if r_i_rip is None or r_rip_j is None:
                    continue

                r_rip_rip = R[rip][rip]
                path_through_rip = _concat_regex(
                    r_i_rip,
                    _concat_regex(_star_regex(r_rip_rip), r_rip_j)
                )

                R[i][j] = _union_regex(R[i][j], path_through_rip)

    result = R[n][n + 1]

    if result is None:
        return "{}"

    return _simplify_regex(result)


class RegexTooLongError(Exception):
    """Raised when regex becomes too long during construction."""
    pass


MAX_REGEX_LENGTH = 10000  # Maximum length of intermediate regex


def _union_regex(r1: Optional[str], r2: Optional[str]) -> Optional[str]:
    """Объединение двух регулярок: r1 | r2"""
    if r1 is None:
        return r2
    if r2 is None:
        return r1
    if r1 == r2:
        return r1
    if r1 == "{}":
        return r2
    if r2 == "{}":
        return r1
    if r1 == "eps" and r2.endswith("*"):
        return r2  # eps | r* = r*
    if r2 == "eps" and r1.endswith("*"):
        return r1
    result = f"({r1}+{r2})"
    if len(result) > MAX_REGEX_LENGTH:
        raise RegexTooLongError(f"Regex too long: {len(result)} chars")
    return result


def _concat_regex(r1: Optional[str], r2: Optional[str]) -> Optional[str]:
    """Конкатенация двух регулярок: r1 . r2"""
    if r1 is None or r2 is None:
        return None
    if r1 == "{}" or r2 == "{}":
        return "{}"
    if r1 == "eps":
        return r2
    if r2 == "eps":
        return r1
    result = f"{r1}{r2}"
    if len(result) > MAX_REGEX_LENGTH:
        raise RegexTooLongError(f"Regex too long: {len(result)} chars")
    return result


def _star_regex(r: Optional[str]) -> str:
    """Замыкание Клини: r*"""
    if r is None or r == "{}" or r == "eps":
        return "eps"
    if r.endswith("*"):
        return r  # (r*)* = r*
    if len(r) == 1:
        return f"{r}*"
    return f"({r})*"


def _simplify_regex(r: str) -> str:
    """Упрощение регулярного выражения."""
    if not r:
        return "{}"

    import re as re_mod

    # eps concatenation cleanup
    r = r.replace("epseps", "eps")

    # (eps)* = eps
    r = r.replace("(eps)*", "eps")

    # Убираем двойные скобки: ((X)) -> (X) когда X не содержит скобок
    prev = None
    while prev != r:
        prev = r
        r = re_mod.sub(r'\(\(([^()]+)\)\)', r'(\1)', r)

    # (a) -> a для одиночных символов
    r = re_mod.sub(r'\(([a-zA-Z0-9])\)', r'\1', r)

    return r


# =============================================================================
# Главный класс — объединяет все этапы
# =============================================================================

class InverseHomomorphismSolver:
    """
    Решатель задач на построение прообраза регулярного языка относительно гомоморфизмов.

    Использование:
        solver = InverseHomomorphismSolver()

        # Задаём язык регулярным выражением
        solver.set_language("((aba)*bb|aa)*")

        # Задаём гомоморфизмы
        solver.add_homomorphism({'a': 'baa', 'b': 'aaa', 'c': 'a', 'd': 'ab'})

        # Или несколько гомоморфизмов для композиции: (h₂ ∘ h₁)⁻¹(L) = h₁⁻¹(h₂⁻¹(L))
        solver.add_homomorphism({'x': 'ab', 'y': 'cd'})

        # Строим прообраз
        result_dfa = solver.solve()
    """

    def __init__(self):
        self.regex: Optional[str] = None
        self.original_dfa: Optional[DFA] = None
        self.homomorphisms: List[Homomorphism] = []
        self.intermediate_dfas: List[DFA] = []
        self.result_dfa: Optional[DFA] = None

    def set_language(self, regex: str) -> 'InverseHomomorphismSolver':
        """
        Задать исходный язык L регулярным выражением.

        Args:
            regex: Регулярное выражение для языка L

        Returns:
            self для цепочки вызовов
        """
        self.regex = regex
        self.original_dfa = None
        self.result_dfa = None
        return self

    def set_dfa(self, dfa: DFA) -> 'InverseHomomorphismSolver':
        """
        Задать исходный язык L напрямую через DFA.

        Args:
            dfa: DFA для языка L

        Returns:
            self для цепочки вызовов
        """
        self.regex = None
        self.original_dfa = dfa
        self.result_dfa = None
        return self

    def add_homomorphism(self, mapping: Dict[str, str]) -> 'InverseHomomorphismSolver':
        """
        Добавить гомоморфизм для вычисления прообраза.

        При добавлении нескольких гомоморфизмов применяется правило композиции:
            (hₙ ∘ ... ∘ h₂ ∘ h₁)⁻¹(L) = h₁⁻¹(h₂⁻¹(...hₙ⁻¹(L)...))

        То есть гомоморфизмы применяются в обратном порядке добавления.

        Args:
            mapping: Словарь {символ → образ} для гомоморфизма

        Returns:
            self для цепочки вызовов
        """
        self.homomorphisms.append(Homomorphism(mapping))
        self.result_dfa = None
        return self

    def clear_homomorphisms(self) -> 'InverseHomomorphismSolver':
        """Очистить список гомоморфизмов."""
        self.homomorphisms.clear()
        self.result_dfa = None
        return self

    def solve(self, minimize: bool = True, verbose: bool = False) -> DFA:
        """
        Построить DFA для прообраза языка.

        Алгоритм:
            1. Построить DFA для L из регулярного выражения (если нужно)
            2. Сделать DFA тотальным
            3. Последовательно применить обратные гомоморфизмы
            4. Минимизировать результат

        Args:
            minimize: Минимизировать результат (по умолчанию True)
            verbose: Выводить промежуточные результаты

        Returns:
            DFA: Минимальный DFA для h⁻¹(L)
        """
        self.intermediate_dfas.clear()

        # Этап 1: Получение DFA для исходного языка
        if self.original_dfa is None:
            if self.regex is None:
                raise ValueError("Не задан ни DFA, ни регулярное выражение для языка L")

            if verbose:
                print(f"=== Этап 1: Построение DFA для языка L ===")
                print(f"Регулярное выражение: {self.regex}")

            # Thompson construction: regex → ε-NFA
            thompson = ThompsonConstruction()
            nfa = thompson.build(self.regex)

            if verbose:
                print(f"ε-NFA: {len(nfa.states)} состояний")

            # Subset construction: ε-NFA → DFA
            dfa = SubsetConstruction.convert(nfa)

            if verbose:
                print(f"DFA (после subset construction): {len(dfa.states)} состояний")

            self.original_dfa = dfa

        # Этап 2: Приведение к тотальному DFA
        current_dfa = make_total_dfa(self.original_dfa)
        self.intermediate_dfas.append(current_dfa)

        if verbose:
            print(f"Тотальный DFA: {len(current_dfa.states)} состояний")
            print(f"Алфавит: {current_dfa.alphabet}")

        # Этап 3: Применение обратных гомоморфизмов
        # Формула композиции: (h₁ ∘ h₂ ∘ ... ∘ hₙ)⁻¹(L) = hₙ⁻¹(...h₂⁻¹(h₁⁻¹(L))...)
        # Гомоморфизмы добавляются в порядке: h₁ имеет кодомен = алфавит L,
        # h₂ имеет кодомен = домен h₁, и т.д.
        # Применяем в прямом порядке добавления
        for i, h in enumerate(self.homomorphisms):
            if verbose:
                print(f"\n=== Этап 3.{i+1}: Применение обратного гомоморфизма ===")
                print(f"{h}")
                print(f"Тип: {'стирающий' if h.is_erasing() else 'нестирающий'}")

            # Проверяем совместимость алфавитов
            # Если образы содержат символы вне алфавита DFA, расширяем DFA
            codomain = h.codomain_alphabet
            if codomain and not codomain.issubset(current_dfa.alphabet):
                # Расширяем алфавит DFA — все новые символы ведут в sink state
                missing = codomain - current_dfa.alphabet
                if verbose:
                    print(f"  Расширение алфавита DFA на {missing}")
                current_dfa = _extend_dfa_alphabet(current_dfa, missing)

            # Применяем обратный гомоморфизм
            current_dfa = apply_inverse_homomorphism(current_dfa, h)
            self.intermediate_dfas.append(current_dfa)

            if verbose:
                print(f"DFA после h⁻¹: {len(current_dfa.states)} состояний")
                print(f"Новый алфавит: {current_dfa.alphabet}")

        # Этап 4: Минимизация
        if minimize:
            if verbose:
                print(f"\n=== Этап 4: Минимизация ===")

            result = minimize_dfa(current_dfa)

            if verbose:
                print(f"Минимальный DFA: {len(result.states)} состояний")
        else:
            result = remove_unreachable_states(current_dfa)

        self.result_dfa = result
        return result

    def verify(self, test_words: List[str]) -> List[Tuple[str, bool, bool]]:
        """
        Проверить корректность построенного автомата на тестовых словах.

        Для каждого слова w проверяется:
            w ∈ L(M') ⟺ h(w) ∈ L(M)

        Args:
            test_words: Список слов для проверки

        Returns:
            Список кортежей (word, in_preimage, in_original)
        """
        if self.result_dfa is None:
            raise ValueError("Сначала вызовите solve()")

        if self.original_dfa is None:
            raise ValueError("Нет исходного DFA для проверки")

        results = []

        # Композиция всех гомоморфизмов
        def apply_all_homomorphisms(word: str) -> str:
            result = word
            for h in self.homomorphisms:
                result = h.apply(result)
            return result

        original_total = make_total_dfa(self.original_dfa)

        for word in test_words:
            try:
                # Проверяем принадлежность прообразу
                in_preimage = self.result_dfa.accepts(word)

                # Проверяем h(w) ∈ L
                h_word = apply_all_homomorphisms(word)
                in_original = original_total.accepts(h_word)

                results.append((word, in_preimage, in_original))

            except (ValueError, KeyError) as e:
                results.append((word, False, False))

        return results


# =============================================================================
# Вспомогательные функции для вывода
# =============================================================================

def dfa_to_table(dfa: DFA) -> str:
    """Форматирование DFA в виде таблицы переходов."""
    lines = []

    # Заголовок
    symbols = sorted(dfa.alphabet)
    header = "State\t" + "\t".join(symbols)
    lines.append(header)
    lines.append("-" * (8 + 8 * len(symbols)))

    # Состояния
    for state in sorted(dfa.states):
        prefix = ""
        if state == dfa.start_state:
            prefix += ">"
        if state in dfa.accept_states:
            prefix += "*"
        prefix = prefix.ljust(2)

        row = f"{prefix}q{state}\t"
        transitions = []
        for symbol in symbols:
            target = dfa.get_transition(state, symbol)
            transitions.append(f"q{target}" if target is not None else "-")
        row += "\t".join(transitions)
        lines.append(row)

    return "\n".join(lines)


def dfa_to_dot(dfa: DFA, name: str = "DFA") -> str:
    """Экспорт DFA в формат DOT для визуализации через Graphviz."""
    lines = [f'digraph {name} {{']
    lines.append('    rankdir=LR;')
    lines.append('    node [shape=circle];')

    # Невидимый узел для стрелки к начальному состоянию
    lines.append('    __start__ [shape=none, label=""];')
    lines.append(f'    __start__ -> q{dfa.start_state};')

    # Финальные состояния — двойной круг
    for state in dfa.accept_states:
        lines.append(f'    q{state} [shape=doublecircle];')

    # Переходы
    # Группируем переходы между одинаковыми парами состояний
    edge_labels = {}
    for (src, symbol), dst in dfa.transitions.items():
        key = (src, dst)
        if key not in edge_labels:
            edge_labels[key] = []
        edge_labels[key].append(symbol)

    for (src, dst), symbols in edge_labels.items():
        label = ",".join(sorted(symbols))
        lines.append(f'    q{src} -> q{dst} [label="{label}"];')

    lines.append('}')
    return "\n".join(lines)


# =============================================================================
# Примеры использования
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("ПРИМЕР 1: Базовый обратный гомоморфизм")
    print("=" * 70)

    # Задача: L = ((aba)*bb | aa)*, h(a) = baa, h(b) = aaa, h(c) = a, h(d) = ab
    # Найти h⁻¹(L)

    solver = InverseHomomorphismSolver()
    solver.set_language("((aba)*bb|aa)*")
    solver.add_homomorphism({
        'a': 'baa',
        'b': 'aaa',
        'c': 'a',
        'd': 'ab'
    })

    result = solver.solve(verbose=True)

    print("\n=== Результат ===")
    print(f"Минимальный DFA для h⁻¹(L): {len(result.states)} состояний")
    print("\nТаблица переходов:")
    print(dfa_to_table(result))

    # Проверка на тестовых словах
    print("\n=== Проверка корректности ===")
    test_words = ['', 'a', 'b', 'c', 'd', 'aa', 'ab', 'cc', 'dd', 'abcd']

    for word, in_preimage, in_original in solver.verify(test_words):
        h_word = solver.homomorphisms[0].apply(word) if word else ''
        status = "✓" if in_preimage == in_original else "✗"
        print(f"{status} w='{word}' → h(w)='{h_word}': "
              f"w∈h⁻¹(L)={in_preimage}, h(w)∈L={in_original}")

    print("\n" + "=" * 70)
    print("ПРИМЕР 2: Стирающий гомоморфизм")
    print("=" * 70)

    # L = (ab)*, h(x) = ab, h(y) = ε (стирающий)
    solver2 = InverseHomomorphismSolver()
    solver2.set_language("(ab)*")
    solver2.add_homomorphism({
        'x': 'ab',
        'y': ''  # ε — стирающий
    })

    result2 = solver2.solve(verbose=True)

    print("\n=== Результат ===")
    print(dfa_to_table(result2))

    # Слово 'xyy' должно приниматься, т.к. h(xyy) = ab·ε·ε = ab ∈ L
    print("\nПроверка:")
    for word in ['', 'x', 'y', 'xy', 'yx', 'xx', 'yyy', 'xyy', 'xyx']:
        h_word = solver2.homomorphisms[0].apply(word)
        accepts = result2.accepts(word)
        print(f"  w='{word}' → h(w)='{h_word}': принимается={accepts}")

    print("\n" + "=" * 70)
    print("ПРИМЕР 3: Композиция гомоморфизмов")
    print("=" * 70)

    # Демонстрация: (h₂ ∘ h₁)⁻¹(L) = h₁⁻¹(h₂⁻¹(L))
    # L = a*, h₁(x) = ab, h₁(y) = ba, h₂(a) = 01, h₂(b) = 10

    solver3 = InverseHomomorphismSolver()
    solver3.set_language("(01)*")  # L над алфавитом {0, 1}

    # h₂: {a, b} → {0, 1}*
    solver3.add_homomorphism({'a': '01', 'b': '10'})

    # h₁: {x, y} → {a, b}*
    solver3.add_homomorphism({'x': 'ab', 'y': 'ba'})

    result3 = solver3.solve(verbose=True)

    print("\n=== Результат ===")
    print(f"(h₂ ∘ h₁)⁻¹(L) имеет {len(result3.states)} состояний")
    print(dfa_to_table(result3))

    print("\n" + "=" * 70)
    print("Графическое представление (DOT формат для Graphviz)")
    print("=" * 70)
    print(dfa_to_dot(result, "InverseHomomorphism"))
