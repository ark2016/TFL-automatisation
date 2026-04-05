"""Tests for cfl_system.lib.cfl_oracle."""

from __future__ import annotations

import pytest

from cfl_system.lib.cfl_oracle import (
    cfl_oracle_from_ir,
    grammar_oracle,
    pda_oracle,
    predicate_oracle,
)


# ---------------------------------------------------------------------------
# Fixtures: grammars and PDA definitions
# ---------------------------------------------------------------------------

def _anbn_grammar() -> dict:
    """Grammar for {a^n b^n | n >= 1}."""
    return {
        "kind": "grammar",
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": ["a", "b"]},
        ],
    }


def _anbn_grammar_with_epsilon() -> dict:
    """Grammar for {a^n b^n | n >= 0} (includes empty word)."""
    return {
        "kind": "grammar",
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    }


def _anbn_pda() -> dict:
    """PDA for {a^n b^n | n >= 1}."""
    return {
        "states": ["q0", "q1", "q2"],
        "input_alphabet": ["a", "b"],
        "stack_alphabet": ["Z", "A"],
        "start_state": "q0",
        "start_stack": "Z",
        "accept_mode": "final_state",
        "accept_states": ["q2"],
        "transitions": [
            {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["Z", "A"]},
            {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
            {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
            {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
            {"from": "q1", "input": None, "stack_top": "Z", "to": "q2", "push": []},
        ],
    }


# ---------------------------------------------------------------------------
# Tests: grammar_oracle
# ---------------------------------------------------------------------------

class TestGrammarOracle:
    def test_anbn_accepts_aabb(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("aabb") is True

    def test_anbn_rejects_aab(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("aab") is False

    def test_anbn_accepts_ab(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("ab") is True

    def test_anbn_rejects_empty(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("") is False

    def test_anbn_with_epsilon_accepts_empty(self):
        oracle = grammar_oracle(_anbn_grammar_with_epsilon())
        assert oracle("") is True

    def test_anbn_rejects_ba(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("ba") is False

    def test_anbn_accepts_aaabbb(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("aaabbb") is True


# ---------------------------------------------------------------------------
# Tests: pda_oracle
# ---------------------------------------------------------------------------

class TestPdaOracle:
    def test_pda_accepts_aabb(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("aabb") is True

    def test_pda_rejects_aab(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("aab") is False

    def test_pda_accepts_ab(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("ab") is True

    def test_pda_rejects_empty(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("") is False


# ---------------------------------------------------------------------------
# Tests: predicate_oracle
# ---------------------------------------------------------------------------

class TestPredicateOracle:
    def test_predicate_count_eq(self):
        """Predicate: |a| == |b|."""
        spec = {
            "kind": "predicate",
            "alphabet": ["a", "b"],
            "variable": "w",
            "predicate": {
                "op": "eq",
                "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
            },
        }
        oracle = predicate_oracle(spec)
        assert oracle("aabb") is True
        assert oracle("abab") is True
        assert oracle("aab") is False
        assert oracle("") is True  # 0 == 0

    def test_predicate_length_even(self):
        """Predicate: |w| mod 2 == 0."""
        spec = {
            "kind": "predicate",
            "alphabet": ["a", "b"],
            "variable": "w",
            "predicate": {
                "modulus": 2,
                "remainder": 0,
                "expr": {"kind": "length", "of_var": "w"},
            },
        }
        oracle = predicate_oracle(spec)
        assert oracle("aa") is True
        assert oracle("a") is False
        assert oracle("") is True


# ---------------------------------------------------------------------------
# Tests: repeated_subword oracle
# ---------------------------------------------------------------------------

class TestRepeatedSubwordOracle:
    def test_w1w2w1w3_accepts(self):
        """Pattern w1 w2 w1 w3 — word 'ab_c_ab_d' = ab+c+ab+d."""
        spec = {
            "kind": "repeated_subword",
            "parts": ["w1", "w2", "w3"],
            "concat_pattern": ["w1", "w2", "w1", "w3"],
            "alphabets": {
                "w1": ["a", "b"],
                "w2": ["c"],
                "w3": ["d"],
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        # w1="ab", w2="c", w3="d" => "ab"+"c"+"ab"+"d" = "abcabd"
        assert oracle("abcabd") is True

    def test_w1w2w1w3_rejects_mismatch(self):
        """w1 repeats must match — different values should fail."""
        spec = {
            "kind": "repeated_subword",
            "parts": ["w1", "w2", "w3"],
            "concat_pattern": ["w1", "w2", "w1", "w3"],
            "alphabets": {
                "w1": ["a", "b"],
                "w2": ["c"],
                "w3": ["d"],
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        # "abcbad" — no way to make w1 appear twice identically
        assert oracle("abcbad") is False

    def test_repeated_subword_empty_parts(self):
        """Parts can be empty strings."""
        spec = {
            "kind": "repeated_subword",
            "parts": ["w1", "w2"],
            "concat_pattern": ["w1", "w2"],
            "alphabets": {
                "w1": ["a"],
                "w2": ["a"],
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("") is True  # w1="" w2=""
        assert oracle("a") is True  # w1="a" w2="" or w1="" w2="a"


# ---------------------------------------------------------------------------
# Tests: exists_decomposition oracle
# ---------------------------------------------------------------------------

class TestExistsDecompositionOracle:
    def test_ww_accepts(self):
        """Language {ww | w in {a,b}*}."""
        spec = {
            "kind": "exists_decomposition",
            "parts": ["x"],
            "concat_pattern": ["x", "x"],
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("abab") is True
        assert oracle("aa") is True
        assert oracle("") is True  # x=""

    def test_ww_rejects(self):
        spec = {
            "kind": "exists_decomposition",
            "parts": ["x"],
            "concat_pattern": ["x", "x"],
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("aba") is False
        assert oracle("abc") is False

    def test_ww_rev_accepts(self):
        """Language {w w^R | w in {a,b}*} — even-length palindromes."""
        spec = {
            "kind": "exists_decomposition",
            "parts": ["x"],
            "concat_pattern": ["x", "rev(x)"],
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("abba") is True  # x="ab", rev="ba"
        assert oracle("") is True
        assert oracle("aa") is True

    def test_ww_rev_rejects(self):
        spec = {
            "kind": "exists_decomposition",
            "parts": ["x"],
            "concat_pattern": ["x", "rev(x)"],
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("abab") is False
        assert oracle("abc") is False

    def test_wvvr_decomposition(self):
        """Language {w v v^R | w,v in {a,b}*}."""
        spec = {
            "kind": "exists_decomposition",
            "parts": ["w", "v"],
            "concat_pattern": ["w", "v", "rev(v)"],
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        # w="a", v="b" => "a"+"b"+"b" = "abb"
        assert oracle("abb") is True
        # w="", v="ab" => ""+"ab"+"ba" = "abba"
        assert oracle("abba") is True
        # w="x", v="" => "x"+""+"" = "x"  (any single char)
        assert oracle("a") is True


# ---------------------------------------------------------------------------
# Tests: grammar_filter oracle
# ---------------------------------------------------------------------------

class TestGrammarFilterOracle:
    def test_anbn_with_count_filter(self):
        """Grammar {a^n b^n} filtered by |a| == |b| (always true for this grammar)."""
        spec = {
            "kind": "grammar_filter",
            "grammar": _anbn_grammar(),
            "filter": {
                "op": "eq",
                "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("aabb") is True
        assert oracle("ab") is True
        assert oracle("aab") is False  # not in grammar

    def test_grammar_filter_rejects_by_filter(self):
        """Grammar accepts but filter rejects."""
        # Grammar for a*b* (all strings of a's followed by b's)
        grammar = {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S", "A", "B"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["A", "B"]},
                {"lhs": "A", "rhs": ["a", "A"]},
                {"lhs": "A", "rhs": []},
                {"lhs": "B", "rhs": ["b", "B"]},
                {"lhs": "B", "rhs": []},
            ],
        }
        # Filter: |a| > |b|
        spec = {
            "kind": "grammar_filter",
            "grammar": grammar,
            "filter": {
                "op": "gt",
                "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("aab") is True   # |a|=2 > |b|=1
        assert oracle("ab") is False   # |a|=1 not > |b|=1
        assert oracle("abb") is False  # |a|=1 not > |b|=2

    def test_natural_language_filter_accepts_grammar(self):
        """NL filters cannot be evaluated — oracle accepts what the grammar generates."""
        spec = {
            "kind": "grammar_filter",
            "grammar": _anbn_grammar(),
            "filter": {
                "kind": "natural_language_filter",
                "description": "word must be pretty",
            },
        }
        ir = {"language_spec": spec}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("ab") is True   # in grammar a^n b^n
        assert oracle("aabb") is True
        assert oracle("aab") is False  # not in grammar


# ---------------------------------------------------------------------------
# Tests: cfl_oracle_from_ir dispatch
# ---------------------------------------------------------------------------

class TestCflOracleFromIR:
    def test_grammar_kind_dispatch(self):
        ir = {"language_spec": _anbn_grammar()}
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("aabb") is True
        assert oracle("aab") is False

    def test_predicate_kind_dispatch(self):
        ir = {
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b"],
                "variable": "w",
                "predicate": {
                    "op": "eq",
                    "left": {"kind": "length", "of_var": "w"},
                    "right": {"kind": "constant", "value": 3},
                },
            },
        }
        oracle = cfl_oracle_from_ir(ir)
        assert oracle("abc") is True
        assert oracle("ab") is False

    def test_missing_language_spec_raises(self):
        with pytest.raises(ValueError, match="no language_spec"):
            cfl_oracle_from_ir({})

    def test_unsupported_kind_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            cfl_oracle_from_ir({"language_spec": {"kind": "unknown_kind"}})


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_word_grammar(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("") is False

    def test_single_char_grammar(self):
        oracle = grammar_oracle(_anbn_grammar())
        assert oracle("a") is False
        assert oracle("b") is False

    def test_empty_word_pda(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("") is False

    def test_single_char_pda(self):
        oracle = pda_oracle(_anbn_pda())
        assert oracle("a") is False
        assert oracle("b") is False
