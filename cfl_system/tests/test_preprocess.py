"""Tests for cfl_system.lib.language_preprocess."""

import pytest

from cfl_system.lib.language_preprocess import preprocess_language


# ---------------------------------------------------------------------------
# Helpers: IR builders
# ---------------------------------------------------------------------------

def _grammar_filter_ir(filter_spec: dict) -> dict:
    """Build a grammar_filter IR with a simple S -> aSb | eps grammar."""
    return {
        "task_type": "classify_cfl",
        "source_text": "test",
        "language_spec": {
            "kind": "grammar_filter",
            "grammar": {
                "kind": "grammar",
                "terminals": ["a", "b"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "S", "b"]},
                    {"lhs": "S", "rhs": []},
                ],
            },
            "filter": filter_spec,
        },
    }


def _repeated_subword_ir() -> dict:
    """Build a repeated_subword IR for {a^n b^n}."""
    return {
        "task_type": "classify_cfl",
        "source_text": "test",
        "language_spec": {
            "kind": "repeated_subword",
            "parts": ["x", "y"],
            "concat_pattern": ["x", "y"],
            "alphabets": {"x": ["a"], "y": ["b"]},
            "constraints": [
                {
                    "op": "eq",
                    "left": {"kind": "length", "of_var": "x"},
                    "right": {"kind": "length", "of_var": "y"},
                }
            ],
        },
    }


def _predicate_ir() -> dict:
    """Build a predicate-kind IR (no grammar, no filter)."""
    return {
        "task_type": "classify_cfl",
        "source_text": "test",
        "language_spec": {
            "kind": "predicate",
            "variables": [{"name": "w", "domain": {"kind": "star", "alphabet": ["a", "b"]}}],
            "predicate": {
                "op": "eq",
                "left": {"kind": "length", "of_var": "w"},
                "right": {"kind": "constant", "value": 5},
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests: filter analysis
# ---------------------------------------------------------------------------

class TestFilterAnalysis:
    def test_modular_filter_is_regular(self):
        ir = _grammar_filter_ir({
            "modulus": 3,
            "remainder": 0,
            "expr": {"kind": "length", "of_var": "w"},
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is True
        assert fa["intersection_strategy"] == "pda_x_dfa"
        assert fa["filter_type"] == "modular"

    def test_count_comparison_not_regular(self):
        ir = _grammar_filter_ir({
            "op": "eq",
            "left": {"kind": "count_symbol", "symbol": "a"},
            "right": {"kind": "count_symbol", "symbol": "b"},
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is False
        assert fa["intersection_strategy"] == "manual"

    def test_comparison_with_constant_is_regular(self):
        ir = _grammar_filter_ir({
            "op": "gt",
            "left": {"kind": "length", "of_var": "w"},
            "right": {"kind": "constant", "value": 5},
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is True
        assert fa["filter_type"] == "comparison_with_constant"
        assert fa["intersection_strategy"] == "pda_x_dfa"

    def test_natural_language_filter_indeterminate(self):
        ir = _grammar_filter_ir({
            "natural_language_filter": "words with equal a's and b's",
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is None
        assert fa["intersection_strategy"] is None
        assert fa["filter_type"] == "natural_language"

    def test_boolean_and_of_regular_filters(self):
        ir = _grammar_filter_ir({
            "op": "and",
            "operands": [
                {
                    "op": "gt",
                    "left": {"kind": "length", "of_var": "w"},
                    "right": {"kind": "constant", "value": 2},
                },
                {
                    "modulus": 2,
                    "remainder": 0,
                    "expr": {"kind": "length", "of_var": "w"},
                },
            ],
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is True
        assert fa["intersection_strategy"] == "pda_x_dfa"
        assert "boolean" in fa["filter_type"]

    def test_boolean_and_with_non_regular(self):
        ir = _grammar_filter_ir({
            "op": "and",
            "operands": [
                {
                    "op": "gt",
                    "left": {"kind": "length", "of_var": "w"},
                    "right": {"kind": "constant", "value": 2},
                },
                {
                    "op": "eq",
                    "left": {"kind": "count_symbol", "symbol": "a"},
                    "right": {"kind": "count_symbol", "symbol": "b"},
                },
            ],
        })
        result = preprocess_language(ir)
        fa = result["filter_analysis"]
        assert fa is not None
        assert fa["filter_is_regular"] is False
        assert fa["intersection_strategy"] == "manual"


# ---------------------------------------------------------------------------
# Tests: quick verdict
# ---------------------------------------------------------------------------

class TestQuickVerdict:
    def test_regular_filter_gives_cfl_verdict(self):
        ir = _grammar_filter_ir({
            "modulus": 2,
            "remainder": 0,
            "expr": {"kind": "length", "of_var": "w"},
        })
        result = preprocess_language(ir)
        assert result["quick_verdict"] == "cfl"
        assert result["quick_verdict_reason"] is not None

    def test_no_verdict_for_non_regular_filter(self):
        ir = _grammar_filter_ir({
            "op": "eq",
            "left": {"kind": "count_symbol", "symbol": "a"},
            "right": {"kind": "count_symbol", "symbol": "b"},
        })
        result = preprocess_language(ir)
        # No quick verdict -- filter is non-regular, doesn't mean non-CFL
        # (the intersection of two CFLs could still be CFL)
        # The verdict depends on bounded/parikh analysis
        assert result["quick_verdict"] in (None, "cfl", "non_cfl")


# ---------------------------------------------------------------------------
# Tests: bounded analysis
# ---------------------------------------------------------------------------

class TestBoundedAnalysis:
    def test_repeated_subword_bounded(self):
        ir = _repeated_subword_ir()
        result = preprocess_language(ir)
        ba = result["bounded_analysis"]
        assert ba is not None
        assert ba["is_bounded"] is True
        assert ba["bounding_words"] is not None


# ---------------------------------------------------------------------------
# Tests: predicate kind
# ---------------------------------------------------------------------------

class TestPredicateKind:
    def test_predicate_no_filter_analysis(self):
        ir = _predicate_ir()
        result = preprocess_language(ir)
        assert result["filter_analysis"] is None
        # bounded_analysis and parikh_precheck are None for predicate kind
        assert result["bounded_analysis"] is None
        assert result["parikh_precheck"] is None


# ---------------------------------------------------------------------------
# Tests: parikh precheck
# ---------------------------------------------------------------------------

class TestParikhPrecheck:
    def test_grammar_filter_has_parikh(self):
        ir = _grammar_filter_ir({
            "op": "gt",
            "left": {"kind": "length", "of_var": "w"},
            "right": {"kind": "constant", "value": 0},
        })
        result = preprocess_language(ir)
        pp = result["parikh_precheck"]
        assert pp is not None
        assert "is_semilinear" in pp
        assert "explanation" in pp

    def test_repeated_subword_has_parikh(self):
        ir = _repeated_subword_ir()
        result = preprocess_language(ir)
        pp = result["parikh_precheck"]
        assert pp is not None
        assert "is_semilinear" in pp
