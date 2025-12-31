"""
Удобный интерфейс для решения задач на обратный гомоморфизм.

Использование:
    python solver.py

Или в коде:
    from solver import solve_inverse_homomorphism

    result = solve_inverse_homomorphism(
        regex="(ab)*",
        homomorphisms=[
            {'x': 'ab', 'y': 'ba'},
            {'a': '01', 'b': '10'}
        ]
    )
"""

from inverse_homomorphism import (
    InverseHomomorphismSolver,
    DFA,
    Homomorphism,
    dfa_to_table,
    dfa_to_dot
)
from typing import Dict, List, Optional, Tuple
import json


def solve_inverse_homomorphism(
    regex: str,
    homomorphisms: List[Dict[str, str]],
    minimize: bool = True,
    verbose: bool = False
) -> DFA:
    """
    Решить задачу на построение прообраза языка относительно гомоморфизма(ов).

    Задача: Дан язык L (регулярным выражением) и гомоморфизм(ы) h.
    Найти: h⁻¹(L) = {w | h(w) ∈ L}

    При нескольких гомоморфизмах [h₁, h₂, ..., hₙ] вычисляется:
        (h₁ ∘ h₂ ∘ ... ∘ hₙ)⁻¹(L) = hₙ⁻¹(...h₂⁻¹(h₁⁻¹(L))...)

    Гомоморфизмы добавляются в порядке близости к L:
        - h₁ имеет кодомен = алфавит L
        - h₂ имеет кодомен = домен h₁
        - и т.д.

    Args:
        regex: Регулярное выражение для языка L
        homomorphisms: Список гомоморфизмов в виде словарей {символ: образ}
        minimize: Минимизировать результат (по умолчанию True)
        verbose: Выводить промежуточные шаги

    Returns:
        DFA: Минимальный DFA для прообраза

    Примеры:
        # Один гомоморфизм
        >>> dfa = solve_inverse_homomorphism(
        ...     regex="(ab)*",
        ...     homomorphisms=[{'x': 'aab', 'y': 'b'}]
        ... )

        # Композиция гомоморфизмов
        >>> dfa = solve_inverse_homomorphism(
        ...     regex="(01)*",
        ...     homomorphisms=[
        ...         {'a': '01', 'b': '10'},  # h₁
        ...         {'x': 'ab', 'y': 'ba'}   # h₂ (применяется первым к L)
        ...     ]
        ... )
    """
    solver = InverseHomomorphismSolver()
    solver.set_language(regex)

    for h_mapping in homomorphisms:
        solver.add_homomorphism(h_mapping)

    return solver.solve(minimize=minimize, verbose=verbose)


def format_solution(
    regex: str,
    homomorphisms: List[Dict[str, str]],
    dfa: DFA,
    test_words: Optional[List[str]] = None
) -> str:
    """
    Форматировать решение для вывода.

    Args:
        regex: Исходное регулярное выражение
        homomorphisms: Список гомоморфизмов
        dfa: Результирующий DFA
        test_words: Тестовые слова для проверки

    Returns:
        str: Отформатированное решение
    """
    lines = []

    lines.append("=" * 70)
    lines.append("РЕШЕНИЕ ЗАДАЧИ НА ОБРАТНЫЙ ГОМОМОРФИЗМ")
    lines.append("=" * 70)

    # Условие задачи
    lines.append("\n### Дано:")
    lines.append(f"  Язык L задан регулярным выражением: {regex}")

    for i, h_mapping in enumerate(homomorphisms, 1):
        if len(homomorphisms) > 1:
            lines.append(f"\n  Гомоморфизм h{i}:")
        else:
            lines.append(f"\n  Гомоморфизм h:")

        for symbol, image in sorted(h_mapping.items()):
            if image == '':
                lines.append(f"    h({symbol}) = ε")
            else:
                lines.append(f"    h({symbol}) = {image}")

    lines.append("\n### Найти:")
    if len(homomorphisms) == 1:
        lines.append("  h⁻¹(L) = {w | h(w) ∈ L}")
    else:
        h_names = " ∘ ".join(f"h{i}" for i in range(len(homomorphisms), 0, -1))
        lines.append(f"  ({h_names})⁻¹(L)")

    # Результат
    lines.append("\n" + "=" * 70)
    lines.append("РЕЗУЛЬТАТ")
    lines.append("=" * 70)

    lines.append(f"\nМинимальный DFA имеет {len(dfa.states)} состояний")
    lines.append(f"Алфавит: {{{', '.join(sorted(dfa.alphabet))}}}")
    lines.append(f"Начальное состояние: q{dfa.start_state}")
    lines.append(f"Финальные состояния: {{{', '.join(f'q{s}' for s in sorted(dfa.accept_states))}}}")

    lines.append("\nТаблица переходов:")
    lines.append(dfa_to_table(dfa))

    # Проверка на тестовых словах
    if test_words:
        lines.append("\n" + "=" * 70)
        lines.append("ПРОВЕРКА")
        lines.append("=" * 70)

        # Функция применения всех гомоморфизмов
        def apply_all(word: str) -> str:
            result = word
            for h_mapping in homomorphisms:
                h = Homomorphism(h_mapping)
                result = h.apply(result)
            return result

        lines.append("\nПроверка на тестовых словах:")
        lines.append("  w\t\th(w)\t\tw ∈ h⁻¹(L)\tстатус")
        lines.append("  " + "-" * 50)

        for word in test_words:
            try:
                h_word = apply_all(word)
                accepts = dfa.accepts(word)
                status = "✓" if accepts else "✗"
                display_word = word if word else "ε"
                display_h_word = h_word if h_word else "ε"
                lines.append(f"  {display_word}\t\t{display_h_word}\t\t{accepts}\t\t{status}")
            except (ValueError, KeyError) as e:
                lines.append(f"  {word}\t\t[ошибка]\t\t-\t\t✗")

    return "\n".join(lines)


def interactive_mode():
    """Интерактивный режим для решения задач."""
    print("=" * 70)
    print("РЕШАТЕЛЬ ЗАДАЧ НА ОБРАТНЫЙ ГОМОМОРФИЗМ")
    print("=" * 70)
    print("\nВведите данные задачи или 'exit' для выхода.\n")

    while True:
        try:
            # Ввод регулярного выражения
            print("Регулярное выражение для языка L:")
            print("  (поддерживаются: |, *, +, ?, (), символы, eps/ε, empty/∅)")
            regex = input("> ").strip()

            if regex.lower() == 'exit':
                break

            if not regex:
                print("Ошибка: регулярное выражение не может быть пустым\n")
                continue

            # Ввод гомоморфизмов
            homomorphisms = []
            print("\nВведите гомоморфизм(ы) в формате: a=образ b=образ ...")
            print("  (используйте eps или пустую строку для ε)")
            print("  (для нескольких гомоморфизмов вводите по одному, пустая строка для завершения)")

            while True:
                h_input = input(f"Гомоморфизм {len(homomorphisms) + 1}> ").strip()

                if not h_input:
                    if not homomorphisms:
                        print("Ошибка: нужен хотя бы один гомоморфизм")
                        continue
                    break

                # Парсим ввод вида "a=baa b=aaa c=a"
                h_mapping = {}
                try:
                    pairs = h_input.split()
                    for pair in pairs:
                        if '=' not in pair:
                            raise ValueError(f"Неверный формат: {pair}")
                        symbol, image = pair.split('=', 1)
                        if len(symbol) != 1:
                            raise ValueError(f"Символ должен быть одной буквой: {symbol}")
                        if image.lower() == 'eps':
                            image = ''
                        h_mapping[symbol] = image

                    if h_mapping:
                        homomorphisms.append(h_mapping)
                        print(f"  Добавлен: {h_mapping}")

                except ValueError as e:
                    print(f"Ошибка парсинга: {e}")
                    continue

            # Решаем задачу
            print("\nРешение...")
            try:
                dfa = solve_inverse_homomorphism(regex, homomorphisms, verbose=True)

                # Вывод результата
                print("\n" + format_solution(regex, homomorphisms, dfa))

                # Предложение проверить слова
                print("\nХотите проверить слова? Введите слова через пробел или 'skip':")
                test_input = input("> ").strip()

                if test_input.lower() != 'skip' and test_input:
                    test_words = test_input.split()
                    # Добавляем пустое слово если указано eps
                    test_words = ['' if w.lower() == 'eps' else w for w in test_words]

                    print("\nПроверка:")
                    def apply_all(word: str) -> str:
                        result = word
                        for h_mapping in homomorphisms:
                            h = Homomorphism(h_mapping)
                            result = h.apply(result)
                        return result

                    for word in test_words:
                        try:
                            h_word = apply_all(word)
                            accepts = dfa.accepts(word)
                            display_word = word if word else "ε"
                            display_h_word = h_word if h_word else "ε"
                            status = "✓ принимается" if accepts else "✗ отвергается"
                            print(f"  w='{display_word}' → h(w)='{display_h_word}': {status}")
                        except Exception as e:
                            print(f"  w='{word}': ошибка - {e}")

                # Сохранение в DOT
                print("\nСохранить в DOT формат? (имя_файла.dot или 'skip'):")
                dot_input = input("> ").strip()

                if dot_input.lower() != 'skip' and dot_input:
                    if not dot_input.endswith('.dot'):
                        dot_input += '.dot'
                    with open(dot_input, 'w', encoding='utf-8') as f:
                        f.write(dfa_to_dot(dfa, "InverseHomomorphism"))
                    print(f"  Сохранено в {dot_input}")
                    print(f"  Для визуализации: dot -Tpng {dot_input} -o automaton.png")

            except Exception as e:
                print(f"\nОшибка при решении: {e}")
                import traceback
                traceback.print_exc()

            print("\n" + "-" * 70 + "\n")

        except KeyboardInterrupt:
            print("\n\nВыход...")
            break
        except EOFError:
            break

    print("До свидания!")


# =============================================================================
# Примеры типовых задач
# =============================================================================

def example_tasks():
    """Демонстрация решения типовых задач."""

    print("=" * 70)
    print("ТИПОВЫЕ ЗАДАЧИ НА ОБРАТНЫЙ ГОМОМОРФИЗМ")
    print("=" * 70)

    # Задача 1: Простой случай
    print("\n### Задача 1: Простой гомоморфизм ###")
    print("L = (ab)*, h(x) = ab, h(y) = ba")
    print("Найти h⁻¹(L)")

    dfa1 = solve_inverse_homomorphism(
        regex="(ab)*",
        homomorphisms=[{'x': 'ab', 'y': 'ba'}]
    )

    print(f"\nРезультат: {len(dfa1.states)} состояний")
    print(dfa_to_table(dfa1))

    # Проверка
    print("\nПроверка:")
    for w in ['', 'x', 'y', 'xx', 'xy', 'yx', 'xxx']:
        h_w = Homomorphism({'x': 'ab', 'y': 'ba'}).apply(w)
        print(f"  w='{w}' → h(w)='{h_w}': принимается={dfa1.accepts(w)}")

    # Задача 2: Стирающий гомоморфизм
    print("\n" + "-" * 70)
    print("\n### Задача 2: Стирающий гомоморфизм ###")
    print("L = a*b*, h(x) = a, h(y) = b, h(z) = ε")
    print("Найти h⁻¹(L)")

    dfa2 = solve_inverse_homomorphism(
        regex="a*b*",
        homomorphisms=[{'x': 'a', 'y': 'b', 'z': ''}]
    )

    print(f"\nРезультат: {len(dfa2.states)} состояний")
    print(dfa_to_table(dfa2))

    print("\nПроверка (z — стирающий символ):")
    h2 = Homomorphism({'x': 'a', 'y': 'b', 'z': ''})
    for w in ['', 'x', 'y', 'z', 'xz', 'zx', 'zy', 'xyz', 'xzy', 'zzz', 'xyzyx']:
        h_w = h2.apply(w)
        display_h = h_w if h_w else 'ε'
        print(f"  w='{w}' → h(w)='{display_h}': принимается={dfa2.accepts(w)}")

    # Задача 3: Сложное регулярное выражение
    print("\n" + "-" * 70)
    print("\n### Задача 3: Сложный язык ###")
    print("L = ((aba)*bb | aa)*, h(a)=baa, h(b)=aaa, h(c)=a, h(d)=ab")
    print("Найти h⁻¹(L)")

    dfa3 = solve_inverse_homomorphism(
        regex="((aba)*bb|aa)*",
        homomorphisms=[{'a': 'baa', 'b': 'aaa', 'c': 'a', 'd': 'ab'}],
        verbose=False
    )

    print(f"\nРезультат: {len(dfa3.states)} состояний")
    print(dfa_to_table(dfa3))

    # Задача 4: Композиция гомоморфизмов
    print("\n" + "-" * 70)
    print("\n### Задача 4: Композиция гомоморфизмов ###")
    print("L = (01)* над алфавитом {0,1}")
    print("h₁: a→01, b→10 (преобразует {a,b}* → {0,1}*)")
    print("h₂: x→ab, y→ba (преобразует {x,y}* → {a,b}*)")
    print("Найти (h₁ ∘ h₂)⁻¹(L) = h₂⁻¹(h₁⁻¹(L))")

    dfa4 = solve_inverse_homomorphism(
        regex="(01)*",
        homomorphisms=[
            {'a': '01', 'b': '10'},  # h₁ применяется вторым (ближе к L)
            {'x': 'ab', 'y': 'ba'}   # h₂ применяется первым (внешний)
        ],
        verbose=False
    )

    print(f"\nРезультат: {len(dfa4.states)} состояний")
    print(dfa_to_table(dfa4))

    print("\nПроверка:")
    h1 = Homomorphism({'a': '01', 'b': '10'})
    h2 = Homomorphism({'x': 'ab', 'y': 'ba'})

    for w in ['', 'x', 'y', 'xx', 'xy', 'yx', 'yy']:
        h2_w = h2.apply(w)
        h1_h2_w = h1.apply(h2_w)
        print(f"  w='{w}' → h₂(w)='{h2_w}' → h₁(h₂(w))='{h1_h2_w}': принимается={dfa4.accepts(w)}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--examples':
        example_tasks()
    else:
        interactive_mode()
