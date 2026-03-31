"""Smoke tests for all Phase 1 modules."""

import unittest

from agent_system.lib.ir_schema import validate_ir
from agent_system.lib.oracle import oracle_from_ir
from agent_system.lib.word_generator import generate_test_words
from agent_system.lib.dfa_runner import run_dfa, validate_dfa
from agent_system.lib.oracle_test import oracle_test


# ── ir_schema ───────────────────────────────────────────────────────────────

class TestValidateIR(unittest.TestCase):

    def test_valid_predicate_ir(self):
        ir = {
            "task_type": "classify",
            "source_text": "test language",
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
        errors = validate_ir(ir)
        self.assertEqual(errors, [])

    def test_valid_grammar_ir(self):
        ir = {
            "task_type": "prove_non_regular",
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
        errors = validate_ir(ir)
        self.assertEqual(errors, [])

    def test_missing_task_type(self):
        ir = {"source_text": "hello"}
        errors = validate_ir(ir)
        self.assertTrue(len(errors) > 0)
        self.assertIn("task_type", errors[0])

    def test_invalid_task_type(self):
        ir = {"task_type": "fly_to_moon", "source_text": "hello"}
        errors = validate_ir(ir)
        self.assertTrue(len(errors) > 0)
        self.assertIn("task_type", errors[0])


# ── oracle ──────────────────────────────────────────────────────────────────

class TestOracle(unittest.TestCase):

    def test_predicate_count_equal(self):
        """Language: {w in {a,b}* | |w|_a = |w|_b}"""
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
        oracle = oracle_from_ir(ir)
        self.assertTrue(oracle("ab"))
        self.assertFalse(oracle("aab"))
        self.assertTrue(oracle(""))

    def test_predicate_modular(self):
        """Language: {w in {a,b}* | |w| mod 2 = 0}"""
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
        oracle = oracle_from_ir(ir)
        self.assertTrue(oracle(""))
        self.assertTrue(oracle("ab"))
        self.assertFalse(oracle("a"))

    def test_grammar_anbn(self):
        """Grammar S -> aSb | eps generates {a^n b^n}."""
        ir = {
            "task_type": "prove_non_regular",
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
        oracle = oracle_from_ir(ir)
        self.assertTrue(oracle(""))
        self.assertTrue(oracle("ab"))
        self.assertTrue(oracle("aabb"))
        self.assertFalse(oracle("aba"))

    def test_grammar_oracle_long_unit_chain(self):
        """Grammar with 40 unit rules: S -> A1, A1 -> A2, ..., A39 -> A40, A40 -> a"""
        nts = ["S"] + [f"A{i}" for i in range(1, 41)]
        rules = [{"lhs": nts[i], "rhs": [nts[i+1]]} for i in range(40)]
        rules.append({"lhs": "A40", "rhs": ["a"]})
        ir = {
            "task_type": "classify",
            "source_text": "long unit chain",
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a"],
                "nonterminals": nts,
                "start": "S",
                "rules": rules,
            },
        }
        oracle = oracle_from_ir(ir)
        self.assertTrue(oracle("a"))


# ── word_generator ──────────────────────────────────────────────────────────

class TestWordGenerator(unittest.TestCase):

    def test_exhaustive_k_count(self):
        """exhaustive_k with alphabet=[a,b], max=3 -> 1+2+4+8 = 15 words."""
        words = generate_test_words(
            alphabet=["a", "b"],
            strategies=["exhaustive_k"],
            max_exhaustive=3,
        )
        self.assertEqual(len(words), 15)

    def test_random_long_count(self):
        """random_long returns the requested number of words."""
        words = generate_test_words(
            alphabet=["a", "b"],
            strategies=["random_long"],
            seed=0,
        )
        # default count in generate_random_long is 50
        self.assertEqual(len(words), 50)


# ── dfa_runner ──────────────────────────────────────────────────────────────

class TestDfaRunner(unittest.TestCase):

    def _ends_with_b_dfa(self) -> dict:
        return {
            "states": ["q0", "q1"],
            "alphabet": ["a", "b"],
            "start": "q0",
            "accept": ["q1"],
            "transitions": {
                "q0": {"a": "q0", "b": "q1"},
                "q1": {"a": "q0", "b": "q1"},
            },
        }

    def test_run_dfa_accepts_b(self):
        dfa = self._ends_with_b_dfa()
        self.assertFalse(run_dfa(dfa, ""))
        self.assertTrue(run_dfa(dfa, "b"))
        self.assertTrue(run_dfa(dfa, "ab"))
        self.assertFalse(run_dfa(dfa, "ba"))

    def test_validate_dfa_missing_transition(self):
        dfa = {
            "states": ["q0", "q1"],
            "alphabet": ["a", "b"],
            "start": "q0",
            "accept": ["q1"],
            "transitions": {
                "q0": {"a": "q0"},  # missing b
                "q1": {"a": "q0", "b": "q1"},
            },
        }
        errors = validate_dfa(dfa)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("Missing transition" in e for e in errors))


# ── oracle_test ─────────────────────────────────────────────────────────────

class TestOracleTest(unittest.TestCase):

    def test_mismatch_finds_counterexample(self):
        """DFA for 'ends with b' vs oracle that always accepts -> should fail."""
        dfa = {
            "states": ["q0", "q1"],
            "alphabet": ["a", "b"],
            "start": "q0",
            "accept": ["q1"],
            "transitions": {
                "q0": {"a": "q0", "b": "q1"},
                "q1": {"a": "q0", "b": "q1"},
            },
        }
        # Oracle: |w|_b mod 1 = 0 -- always true (everything mod 1 is 0)
        oracle_fn = lambda w: True  # noqa: E731
        result = oracle_test(oracle_fn, dfa, alphabet=["a", "b"], max_exhaustive=3)
        self.assertEqual(result["status"], "fail")
        self.assertIsNotNone(result["counterexample"])

    def test_match_passes(self):
        """DFA for even length + matching oracle -> should pass."""
        dfa = {
            "states": ["q0", "q1"],
            "alphabet": ["a", "b"],
            "start": "q0",
            "accept": ["q0"],
            "transitions": {
                "q0": {"a": "q1", "b": "q1"},
                "q1": {"a": "q0", "b": "q0"},
            },
        }
        oracle_fn = lambda w: len(w) % 2 == 0  # noqa: E731
        result = oracle_test(oracle_fn, dfa, alphabet=["a", "b"], max_exhaustive=4)
        self.assertEqual(result["status"], "pass")
        self.assertIsNone(result["counterexample"])


if __name__ == "__main__":
    unittest.main()
