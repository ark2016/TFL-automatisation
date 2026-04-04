"""Tests for cfl_system.lib.cfl_oracle_test module."""

from __future__ import annotations

import pytest

from cfl_system.lib.cfl_oracle_test import (
    oracle_test,
    oracle_test_grammar,
    oracle_test_pda,
)


# ---------------------------------------------------------------------------
# Fixtures: IR and grammars for {a^n b^n | n >= 0}
# ---------------------------------------------------------------------------

ANBN_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{a^n b^n | n >= 0}",
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

# Correct grammar for {a^n b^n}
CORRECT_GRAMMAR = {
    "terminals": ["a", "b"],
    "nonterminals": ["S"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

# Overly permissive grammar: generates a* (accepts too many words)
OVERPERMISSIVE_GRAMMAR = {
    "terminals": ["a", "b"],
    "nonterminals": ["S"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S"]},
        {"lhs": "S", "rhs": ["b", "S"]},
        {"lhs": "S", "rhs": []},
    ],
}

# Too restrictive grammar: only generates "ab" and epsilon
RESTRICTIVE_GRAMMAR = {
    "terminals": ["a", "b"],
    "nonterminals": ["S"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

# Correct PDA for {a^n b^n} via empty stack
CORRECT_PDA = {
    "states": ["q0", "q1"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z", "A"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "empty_stack",
    "transitions": [
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["Z", "A"]},
        {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        # Pop bottom marker after matching
        {"from": "q1", "input": None, "stack_top": "Z", "to": "q1", "push": []},
        # Accept empty word: pop Z immediately
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q0", "push": []},
    ],
}

# Incorrect PDA: accepts everything (always empties stack immediately)
INCORRECT_PDA = {
    "states": ["q0"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "empty_stack",
    "transitions": [
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": []},
        {"from": "q0", "input": "b", "stack_top": "Z", "to": "q0", "push": []},
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q0", "push": []},
    ],
}


# ---------------------------------------------------------------------------
# Tests for oracle_test_grammar
# ---------------------------------------------------------------------------


class TestOracleTestGrammar:
    def test_correct_grammar_passes(self):
        """Correct grammar for {a^n b^n} should pass with no counterexamples."""
        result = oracle_test_grammar(CORRECT_GRAMMAR, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "pass"
        assert result["counterexamples"] == []
        assert result["positive_checked"] > 0
        assert result["positive_passed"] == result["positive_checked"]
        assert result["negative_passed"] == result["negative_checked"]

    def test_overpermissive_grammar_has_false_positives(self):
        """Grammar that accepts too many words should produce false_positive counterexamples."""
        result = oracle_test_grammar(OVERPERMISSIVE_GRAMMAR, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "grammar_incorrect"
        fp = [ce for ce in result["counterexamples"] if ce["type"] == "false_positive"]
        assert len(fp) > 0

    def test_restrictive_grammar_has_false_negatives(self):
        """Grammar that misses words should produce false_negative counterexamples."""
        result = oracle_test_grammar(RESTRICTIVE_GRAMMAR, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "grammar_incorrect"
        fn = [ce for ce in result["counterexamples"] if ce["type"] == "false_negative"]
        assert len(fn) > 0

    def test_error_on_invalid_grammar(self):
        """Invalid grammar (missing required keys) should return error status."""
        bad_grammar = {"rules": []}
        result = oracle_test_grammar(bad_grammar, ANBN_IR, max_words=30, max_length=10)
        assert result["status"] == "error"
        assert result["details"] != ""


# ---------------------------------------------------------------------------
# Tests for oracle_test_pda
# ---------------------------------------------------------------------------


class TestOracleTestPda:
    def test_correct_pda_passes(self):
        """Correct PDA for {a^n b^n} should pass."""
        result = oracle_test_pda(CORRECT_PDA, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "pass"
        assert result["counterexamples"] == []

    def test_incorrect_pda_fails(self):
        """PDA that accepts everything should produce counterexamples."""
        result = oracle_test_pda(INCORRECT_PDA, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "grammar_incorrect"
        assert len(result["counterexamples"]) > 0


# ---------------------------------------------------------------------------
# Tests for oracle_test (dispatch)
# ---------------------------------------------------------------------------


class TestOracleTestDispatch:
    def test_dispatch_grammar_key(self):
        """evidence with 'grammar' key dispatches to grammar test."""
        evidence = {"grammar": CORRECT_GRAMMAR}
        result = oracle_test(evidence, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "pass"

    def test_dispatch_pda_key(self):
        """evidence with 'pda' key dispatches to PDA test."""
        evidence = {"pda": CORRECT_PDA}
        result = oracle_test(evidence, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "pass"

    def test_dispatch_both_keys(self):
        """evidence with both 'grammar' and 'pda' merges results."""
        evidence = {"grammar": CORRECT_GRAMMAR, "pda": CORRECT_PDA}
        result = oracle_test(evidence, ANBN_IR, max_words=60, max_length=20)
        assert result["status"] == "pass"
        # Merged result should have doubled counters
        assert result["positive_checked"] > 0
        assert "Grammar:" in result["details"]
        assert "PDA:" in result["details"]

    def test_dispatch_no_keys_errors(self):
        """evidence with neither key should return error."""
        result = oracle_test({}, ANBN_IR)
        assert result["status"] == "error"
        assert "neither" in result["details"]

    def test_counterexample_structure(self):
        """Counterexamples should have word, type, and description fields."""
        result = oracle_test_grammar(OVERPERMISSIVE_GRAMMAR, ANBN_IR, max_words=60, max_length=20)
        assert len(result["counterexamples"]) > 0
        ce = result["counterexamples"][0]
        assert "word" in ce
        assert "type" in ce
        assert ce["type"] in ("false_negative", "false_positive")
        assert "description" in ce
