"""
Универсальный алгоритм вычисления обратного гомоморфизма для регулярных языков.

Основные компоненты:
    - InverseHomomorphismSolver: Главный класс для решения задач
    - solve_inverse_homomorphism: Функция для быстрого решения
    - DFA, NFA, Homomorphism: Структуры данных

Использование:
    from reverse_morfism import solve_inverse_homomorphism

    # Один гомоморфизм
    dfa = solve_inverse_homomorphism(
        regex="(ab)*",
        homomorphisms=[{'x': 'ab', 'y': 'ba'}]
    )

    # Проверка слова
    print(dfa.accepts("xx"))  # True, т.к. h(xx) = abab ∈ (ab)*

Теория:
    h⁻¹(L) = {w ∈ Δ* | h(w) ∈ L}

    Конструкция DFA M' для h⁻¹(L):
        δ'(q, a) = δ̂(q, h(a))
    где δ̂ — расширенная функция переходов исходного автомата.
"""

from .inverse_homomorphism import (
    # Основные классы
    InverseHomomorphismSolver,
    DFA,
    NFA,
    Homomorphism,

    # Вспомогательные функции
    ThompsonConstruction,
    SubsetConstruction,
    make_total_dfa,
    minimize_dfa,
    apply_inverse_homomorphism,
    remove_unreachable_states,

    # Форматирование вывода
    dfa_to_table,
    dfa_to_dot,
    dfa_to_regex,
)

from .solver import (
    solve_inverse_homomorphism,
    format_solution,
)

__all__ = [
    # Основные
    'InverseHomomorphismSolver',
    'solve_inverse_homomorphism',
    'DFA',
    'NFA',
    'Homomorphism',

    # Построение автоматов
    'ThompsonConstruction',
    'SubsetConstruction',

    # Преобразования
    'make_total_dfa',
    'minimize_dfa',
    'apply_inverse_homomorphism',
    'remove_unreachable_states',

    # Вывод
    'dfa_to_table',
    'dfa_to_dot',
    'dfa_to_regex',
    'format_solution',
]

__version__ = '1.0.0'
