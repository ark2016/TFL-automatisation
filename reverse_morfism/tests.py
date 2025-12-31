"""
Тесты для алгоритма обратного гомоморфизма.

Запуск: python tests.py
"""

import unittest
from inverse_homomorphism import (
    ThompsonConstruction,
    SubsetConstruction,
    DFA,
    NFA,
    Homomorphism,
    InverseHomomorphismSolver,
    make_total_dfa,
    minimize_dfa,
    apply_inverse_homomorphism
)


class TestThompsonConstruction(unittest.TestCase):
    """Тесты построения NFA из регулярного выражения."""

    def setUp(self):
        self.thompson = ThompsonConstruction()

    def test_single_symbol(self):
        """L = {a}"""
        nfa = self.thompson.build("a")
        self.assertEqual(len(nfa.states), 2)
        self.assertEqual(nfa.alphabet, {'a'})

    def test_concatenation(self):
        """L = {ab}"""
        nfa = self.thompson.build("ab")
        self.assertEqual(nfa.alphabet, {'a', 'b'})

    def test_union(self):
        """L = {a, b}"""
        nfa = self.thompson.build("a|b")
        self.assertEqual(nfa.alphabet, {'a', 'b'})

    def test_kleene_star(self):
        """L = a*"""
        nfa = self.thompson.build("a*")
        self.assertEqual(nfa.alphabet, {'a'})

    def test_complex_expression(self):
        """L = (a|b)*abb"""
        nfa = self.thompson.build("(a|b)*abb")
        self.assertEqual(nfa.alphabet, {'a', 'b'})


class TestSubsetConstruction(unittest.TestCase):
    """Тесты преобразования NFA в DFA."""

    def test_simple_nfa(self):
        """Простой NFA для языка L = {a}"""
        thompson = ThompsonConstruction()
        nfa = thompson.build("a")
        dfa = SubsetConstruction.convert(nfa)

        self.assertIsInstance(dfa, DFA)
        self.assertTrue(dfa.accepts("a"))
        self.assertFalse(dfa.accepts(""))
        self.assertFalse(dfa.accepts("aa"))

    def test_kleene_star(self):
        """L = a* = {ε, a, aa, aaa, ...}"""
        thompson = ThompsonConstruction()
        nfa = thompson.build("a*")
        dfa = SubsetConstruction.convert(nfa)
        dfa = make_total_dfa(dfa)

        self.assertTrue(dfa.accepts(""))
        self.assertTrue(dfa.accepts("a"))
        self.assertTrue(dfa.accepts("aa"))
        self.assertTrue(dfa.accepts("aaa"))

    def test_union(self):
        """L = a|b = {a, b}"""
        thompson = ThompsonConstruction()
        nfa = thompson.build("a|b")
        dfa = SubsetConstruction.convert(nfa)
        dfa = make_total_dfa(dfa)

        self.assertTrue(dfa.accepts("a"))
        self.assertTrue(dfa.accepts("b"))
        self.assertFalse(dfa.accepts(""))
        self.assertFalse(dfa.accepts("ab"))


class TestHomomorphism(unittest.TestCase):
    """Тесты для гомоморфизма."""

    def test_basic_homomorphism(self):
        """h(a) = 01, h(b) = 10"""
        h = Homomorphism({'a': '01', 'b': '10'})

        self.assertEqual(h.apply(""), "")
        self.assertEqual(h.apply("a"), "01")
        self.assertEqual(h.apply("b"), "10")
        self.assertEqual(h.apply("ab"), "0110")
        self.assertEqual(h.apply("aab"), "010110")

    def test_erasing_homomorphism(self):
        """h(a) = x, h(b) = ε"""
        h = Homomorphism({'a': 'x', 'b': ''})

        self.assertTrue(h.is_erasing())
        self.assertEqual(h.apply("a"), "x")
        self.assertEqual(h.apply("b"), "")
        self.assertEqual(h.apply("ab"), "x")
        self.assertEqual(h.apply("abba"), "xx")

    def test_domain_and_codomain(self):
        """Проверка области определения и кодомена."""
        h = Homomorphism({'a': 'xy', 'b': 'z'})

        self.assertEqual(h.domain, {'a', 'b'})
        self.assertEqual(h.codomain_alphabet, {'x', 'y', 'z'})


class TestInverseHomomorphism(unittest.TestCase):
    """Тесты для обратного гомоморфизма."""

    def test_basic_inverse(self):
        """
        L = (ab)*, h(x) = ab, h(y) = ba

        h⁻¹(L) должен принимать слова w такие, что h(w) ∈ (ab)*
        - x: h(x) = ab ∈ L ✓
        - y: h(y) = ba ∉ L ✗
        - xx: h(xx) = abab ∈ L ✓
        - xy: h(xy) = abba ∉ L ✗
        """
        solver = InverseHomomorphismSolver()
        solver.set_language("(ab)*")
        solver.add_homomorphism({'x': 'ab', 'y': 'ba'})
        dfa = solver.solve()

        # Проверяем корректность
        h = Homomorphism({'x': 'ab', 'y': 'ba'})

        test_cases = [
            ("", True),    # h("") = "" ∈ (ab)*
            ("x", True),   # h("x") = "ab" ∈ (ab)*
            ("y", False),  # h("y") = "ba" ∉ (ab)*
            ("xx", True),  # h("xx") = "abab" ∈ (ab)*
            ("xy", False), # h("xy") = "abba" ∉ (ab)*
            ("yx", False), # h("yx") = "baab" ∉ (ab)*
            ("xxx", True), # h("xxx") = "ababab" ∈ (ab)*
        ]

        for word, expected in test_cases:
            result = dfa.accepts(word)
            h_word = h.apply(word)
            self.assertEqual(
                result, expected,
                f"w='{word}', h(w)='{h_word}': ожидалось {expected}, получено {result}"
            )

    def test_erasing_inverse(self):
        """
        L = a*, h(x) = a, h(y) = ε

        h⁻¹(L) должен принимать слова w такие, что h(w) ∈ a*
        Поскольку h(y) = ε, символ y можно вставлять в любое место
        """
        solver = InverseHomomorphismSolver()
        solver.set_language("a*")
        solver.add_homomorphism({'x': 'a', 'y': ''})
        dfa = solver.solve()

        # Все слова из {x, y}* где h(w) = a* должны приниматься
        test_cases = [
            ("", True),     # h("") = "" ∈ a*
            ("x", True),    # h("x") = "a" ∈ a*
            ("y", True),    # h("y") = "" ∈ a*
            ("xx", True),   # h("xx") = "aa" ∈ a*
            ("xy", True),   # h("xy") = "a" ∈ a*
            ("yx", True),   # h("yx") = "a" ∈ a*
            ("yy", True),   # h("yy") = "" ∈ a*
            ("xyxyx", True),  # h = "aaa" ∈ a*
            ("yyyy", True),   # h = "" ∈ a*
        ]

        for word, expected in test_cases:
            result = dfa.accepts(word)
            self.assertEqual(result, expected, f"Слово '{word}': ожидалось {expected}")

    def test_complex_language(self):
        """
        L = (ab|ba)*, h(x) = ab, h(y) = ba
        """
        solver = InverseHomomorphismSolver()
        solver.set_language("(ab|ba)*")
        solver.add_homomorphism({'x': 'ab', 'y': 'ba'})
        dfa = solver.solve()

        test_cases = [
            ("", True),      # ε ∈ L
            ("x", True),     # ab ∈ L
            ("y", True),     # ba ∈ L
            ("xx", True),    # abab ∈ L
            ("xy", True),    # abba ∈ L
            ("yx", True),    # baab ∈ L
            ("yy", True),    # baba ∈ L
        ]

        for word, expected in test_cases:
            result = dfa.accepts(word)
            self.assertEqual(result, expected, f"Слово '{word}'")

    def test_composition(self):
        """
        Тест композиции гомоморфизмов.

        L = (01)*, h₁: a→01, b→10, h₂: x→a, y→b

        (h₁ ∘ h₂)⁻¹(L) = h₂⁻¹(h₁⁻¹(L))

        h₂(x) = a, h₁(a) = 01 ∈ L ✓
        h₂(y) = b, h₁(b) = 10 ∉ L ✗
        h₂(xx) = aa, h₁(aa) = 0101 ∈ L ✓
        """
        solver = InverseHomomorphismSolver()
        solver.set_language("(01)*")
        solver.add_homomorphism({'a': '01', 'b': '10'})  # h₁
        solver.add_homomorphism({'x': 'a', 'y': 'b'})    # h₂
        dfa = solver.solve()

        test_cases = [
            ("", True),     # h₁(h₂("")) = "" ∈ L
            ("x", True),    # h₁(h₂("x")) = h₁("a") = "01" ∈ L
            ("y", False),   # h₁(h₂("y")) = h₁("b") = "10" ∉ L
            ("xx", True),   # h₁(h₂("xx")) = h₁("aa") = "0101" ∈ L
            ("xy", False),  # h₁(h₂("xy")) = h₁("ab") = "0110" ∉ L
        ]

        for word, expected in test_cases:
            result = dfa.accepts(word)
            self.assertEqual(result, expected, f"Слово '{word}'")


class TestMinimization(unittest.TestCase):
    """Тесты минимизации DFA."""

    def test_minimize_redundant_states(self):
        """Проверка удаления эквивалентных состояний."""
        # Создаём DFA с избыточными состояниями
        # L = a* с лишним состоянием
        dfa = DFA(
            states={0, 1, 2},  # 1 и 2 эквивалентны
            alphabet={'a'},
            transitions={
                (0, 'a'): 1,
                (1, 'a'): 2,
                (2, 'a'): 1,  # эквивалентно состоянию 1
            },
            start_state=0,
            accept_states={0, 1, 2}
        )

        dfa = make_total_dfa(dfa)
        min_dfa = minimize_dfa(dfa)

        # Минимальный DFA для a* имеет 1-2 состояния (в зависимости от sink)
        self.assertLessEqual(len(min_dfa.states), 2)

        # Проверяем эквивалентность языков
        for word in ["", "a", "aa", "aaa"]:
            self.assertEqual(dfa.accepts(word), min_dfa.accepts(word), f"Слово '{word}'")


class TestEdgeCases(unittest.TestCase):
    """Тесты граничных случаев."""

    def test_empty_language(self):
        """L = ∅ (пустой язык)"""
        solver = InverseHomomorphismSolver()
        solver.set_language("empty")
        solver.add_homomorphism({'a': 'x', 'b': 'y'})
        dfa = solver.solve()

        # h⁻¹(∅) = ∅
        self.assertFalse(dfa.accepts(""))
        self.assertFalse(dfa.accepts("a"))
        self.assertFalse(dfa.accepts("ab"))

    def test_epsilon_only_language(self):
        """L = {ε}"""
        solver = InverseHomomorphismSolver()
        solver.set_language("eps")
        solver.add_homomorphism({'a': 'x', 'b': ''})
        dfa = solver.solve()

        # h⁻¹({ε}) содержит слова w с h(w) = ε
        # h(b) = ε, поэтому b* ⊆ h⁻¹({ε})
        self.assertTrue(dfa.accepts(""))      # h("") = "" = ε
        self.assertTrue(dfa.accepts("b"))     # h("b") = "" = ε
        self.assertTrue(dfa.accepts("bb"))    # h("bb") = "" = ε
        self.assertFalse(dfa.accepts("a"))    # h("a") = "x" ≠ ε
        self.assertFalse(dfa.accepts("ab"))   # h("ab") = "x" ≠ ε

    def test_single_letter_alphabet(self):
        """Алфавит из одной буквы."""
        solver = InverseHomomorphismSolver()
        solver.set_language("a*")
        solver.add_homomorphism({'x': 'aa'})
        dfa = solver.solve()

        # h(x) = aa, h(xx) = aaaa, ...
        # Все принимаются, т.к. a* принимает aa, aaaa, ...
        self.assertTrue(dfa.accepts(""))
        self.assertTrue(dfa.accepts("x"))
        self.assertTrue(dfa.accepts("xx"))


class TestVerification(unittest.TestCase):
    """Тесты функции верификации."""

    def test_verify_correctness(self):
        """Автоматическая проверка свойства h⁻¹(L)."""
        solver = InverseHomomorphismSolver()
        solver.set_language("(ab)*")
        solver.add_homomorphism({'x': 'ab', 'y': 'ba'})
        solver.solve()

        results = solver.verify(['', 'x', 'y', 'xx', 'xy'])

        for word, in_preimage, in_original in results:
            # Ключевое свойство: w ∈ h⁻¹(L) ⟺ h(w) ∈ L
            self.assertEqual(
                in_preimage, in_original,
                f"Нарушено свойство для слова '{word}'"
            )


if __name__ == "__main__":
    print("=" * 70)
    print("ЗАПУСК ТЕСТОВ ДЛЯ АЛГОРИТМА ОБРАТНОГО ГОМОМОРФИЗМА")
    print("=" * 70)

    unittest.main(verbosity=2)
