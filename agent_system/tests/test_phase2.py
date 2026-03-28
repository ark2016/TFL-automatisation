"""Tests for Phase 2 modules."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.hypothesis_module import analyze_hypothesis
from lib.dfa_builder import build_dfa_from_regex, minimize_dfa
from lib.dfa_runner import run_dfa
from lib.congruence import compute_nerode_classes, estimate_index
from lib.grammar_utils import (
    is_right_linear,
    is_left_linear,
    has_nested_recursion,
    generate_words,
    cyk_parse,
    grammar_to_dfa,
)


# ── hypothesis_module ──────────────────────────────────────────────────────

class TestHypothesisModule(unittest.TestCase):

    def test_predicate_count_eq(self):
        """IR with |w|_a = |w|_b -> hypothesis='non_regular'."""
        ir = {
            "task_type": "classify",
            "source_text": "|w|_a = |w|_b",
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b"],
                "variable": "w",
                "predicate": {
                    "op": "eq",
                    "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                    "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
                },
            },
        }
        result = analyze_hypothesis(ir)
        self.assertEqual(result["hypothesis"], "non_regular")

    def test_predicate_modular(self):
        """IR with |w| mod 2 = 0 -> hypothesis='regular'."""
        ir = {
            "task_type": "classify",
            "source_text": "|w| mod 2 = 0",
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b"],
                "variable": "w",
                "predicate": {
                    "expr": {"kind": "length", "of_var": "w"},
                    "modulus": 2,
                    "remainder": 0,
                },
            },
        }
        result = analyze_hypothesis(ir)
        self.assertEqual(result["hypothesis"], "regular")

    def test_grammar_right_linear(self):
        """Grammar S -> aS | b -> hypothesis='regular'."""
        ir = {
            "task_type": "classify",
            "source_text": "S -> aS | b",
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a", "b"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "S"]},
                    {"lhs": "S", "rhs": ["b"]},
                ],
            },
        }
        result = analyze_hypothesis(ir)
        self.assertEqual(result["hypothesis"], "regular")

    def test_grammar_nested(self):
        """Grammar S -> aSb | eps -> hypothesis='non_regular'."""
        ir = {
            "task_type": "classify",
            "source_text": "S -> aSb | eps",
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a", "b"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "S", "b"]},
                    {"lhs": "S", "rhs": []},
                ],
            },
        }
        result = analyze_hypothesis(ir)
        self.assertEqual(result["hypothesis"], "non_regular")

    def test_regex_no_backref(self):
        """Regex without backreferences -> hypothesis='regular'."""
        ir = {
            "task_type": "classify",
            "source_text": "(a|b)*",
            "language_spec": {
                "kind": "regex",
                "pattern": "(a|b)*",
                "has_backreferences": False,
            },
        }
        result = analyze_hypothesis(ir)
        self.assertEqual(result["hypothesis"], "regular")


# ── dfa_builder ────────────────────────────────────────────────────────────

class TestDfaBuilder(unittest.TestCase):

    def test_simple_star(self):
        """build_dfa_from_regex('a*') accepts '', 'a', 'aa'; rejects 'b'."""
        dfa = build_dfa_from_regex("a*")
        self.assertTrue(run_dfa(dfa, ""))
        self.assertTrue(run_dfa(dfa, "a"))
        self.assertTrue(run_dfa(dfa, "aa"))
        self.assertFalse(run_dfa(dfa, "b"))

    def test_alternation(self):
        """build_dfa_from_regex('a|b') accepts 'a', 'b'; rejects '', 'ab'."""
        dfa = build_dfa_from_regex("a|b")
        self.assertTrue(run_dfa(dfa, "a"))
        self.assertTrue(run_dfa(dfa, "b"))
        self.assertFalse(run_dfa(dfa, ""))
        self.assertFalse(run_dfa(dfa, "ab"))

    def test_concat_star(self):
        """build_dfa_from_regex('(a|b)*abb') accepts 'abb', 'aabb', 'babb'; rejects '', 'ab'."""
        dfa = build_dfa_from_regex("(a|b)*abb")
        self.assertTrue(run_dfa(dfa, "abb"))
        self.assertTrue(run_dfa(dfa, "aabb"))
        self.assertTrue(run_dfa(dfa, "babb"))
        self.assertFalse(run_dfa(dfa, ""))
        self.assertFalse(run_dfa(dfa, "ab"))

    def test_minimize_idempotent(self):
        """Minimizing an already-minimized DFA should not change state count."""
        dfa = build_dfa_from_regex("a*")
        minimized_once = minimize_dfa(dfa)
        minimized_twice = minimize_dfa(minimized_once)
        self.assertEqual(len(minimized_once["states"]), len(minimized_twice["states"]))


# ── congruence ─────────────────────────────────────────────────────────────

class TestCongruence(unittest.TestCase):

    def test_finite_language(self):
        """Oracle for 'ends with b' should have finite index."""
        oracle = lambda w: len(w) > 0 and w[-1] == "b"  # noqa: E731
        result = estimate_index(oracle, ["a", "b"], max_depth=6)
        self.assertIsInstance(result["estimated_index"], int)

    def test_infinite_language(self):
        """Oracle for {a^n b^n} should estimate infinite index."""
        def oracle(w):
            if not w:
                return True
            n = 0
            for ch in w:
                if ch == "a":
                    n += 1
                else:
                    break
            return w == "a" * n + "b" * n

        result = estimate_index(oracle, ["a", "b"], max_depth=6)
        self.assertEqual(result["estimated_index"], "infinite")

    def test_classes_structure(self):
        """compute_nerode_classes should return dict with expected keys."""
        oracle = lambda w: len(w) > 0 and w[-1] == "b"  # noqa: E731
        result = compute_nerode_classes(oracle, ["a", "b"], max_depth=4)
        self.assertIn("classes", result)
        self.assertIn("num_classes", result)
        self.assertIn("growth_pattern", result)
        self.assertIsInstance(result["classes"], list)
        self.assertIsInstance(result["num_classes"], int)
        self.assertIsInstance(result["growth_pattern"], list)


# ── grammar_utils ──────────────────────────────────────────────────────────

class TestGrammarUtils(unittest.TestCase):

    def _right_linear_grammar(self):
        """S -> aS | b"""
        return {
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S"]},
                {"lhs": "S", "rhs": ["b"]},
            ],
        }

    def _anbn_grammar(self):
        """S -> aSb | eps"""
        return {
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        }

    def _left_linear_grammar(self):
        """S -> Sa | b"""
        return {
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["S", "a"]},
                {"lhs": "S", "rhs": ["b"]},
            ],
        }

    def _two_nt_right_linear_grammar(self):
        """S -> aA | b, A -> bS | a"""
        return {
            "terminals": ["a", "b"],
            "nonterminals": ["S", "A"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "A"]},
                {"lhs": "S", "rhs": ["b"]},
                {"lhs": "A", "rhs": ["b", "S"]},
                {"lhs": "A", "rhs": ["a"]},
            ],
        }

    def test_right_linear(self):
        """Grammar S -> aS | b is right-linear."""
        self.assertTrue(is_right_linear(self._right_linear_grammar()))

    def test_not_right_linear(self):
        """Grammar S -> aSb | eps is NOT right-linear."""
        self.assertFalse(is_right_linear(self._anbn_grammar()))

    def test_left_linear(self):
        """Grammar S -> Sa | b is left-linear."""
        self.assertTrue(is_left_linear(self._left_linear_grammar()))

    def test_nested_recursion(self):
        """Grammar S -> aSb | eps has nested recursion."""
        self.assertTrue(has_nested_recursion(self._anbn_grammar()))

    def test_generate_words_anbn(self):
        """Grammar S -> aSb | eps generates '', 'ab', 'aabb', 'aaabbb' up to max_len=6."""
        words = generate_words(self._anbn_grammar(), max_len=6)
        self.assertIn("", words)
        self.assertIn("ab", words)
        self.assertIn("aabb", words)
        self.assertIn("aaabbb", words)

    def test_cyk_parse(self):
        """CYK parse on S -> aSb | eps."""
        grammar = self._anbn_grammar()
        self.assertTrue(cyk_parse(grammar, "aabb"))
        self.assertTrue(cyk_parse(grammar, "ab"))
        self.assertFalse(cyk_parse(grammar, "aba"))

    def test_grammar_to_dfa_right_linear(self):
        """Grammar S -> aA | b, A -> bS | a -> grammar_to_dfa returns valid DFA."""
        grammar = self._two_nt_right_linear_grammar()
        dfa = grammar_to_dfa(grammar)
        self.assertIsNotNone(dfa)
        self.assertIn("states", dfa)
        self.assertIn("alphabet", dfa)
        self.assertIn("transitions", dfa)
        self.assertIn("start", dfa)
        self.assertIn("accept", dfa)

    def test_grammar_to_dfa_non_linear(self):
        """Grammar S -> aSb | eps -> grammar_to_dfa returns None."""
        dfa = grammar_to_dfa(self._anbn_grammar())
        self.assertIsNone(dfa)


if __name__ == "__main__":
    unittest.main()
