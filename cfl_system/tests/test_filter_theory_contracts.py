"""Regression cases for closure directions and filter degeneracies."""

import pytest

from cfl_system.lib.language_preprocess import _analyze_filter, preprocess_language


def count(symbol):
    return {"kind": "count_symbol", "symbol": symbol, "in_var": "w"}


def comparison(left, right, op="eq"):
    return {"op": op, "left": left, "right": right}


@pytest.mark.parametrize("op", ["eq", "ne", "lt", "le", "gt", "ge"])
def test_identical_counts_are_constant_predicates(op):
    assert _analyze_filter(comparison(count("a"), count("a"), op), {"a", "b"})["filter_is_regular"] is True


@pytest.mark.parametrize("op", ["and", "or"])
def test_nonregularity_does_not_propagate_through_boolean_operations(op):
    equal = comparison(count("a"), count("b"))
    predicate = {"op": op, "operands": [equal, {"op": "not", "operands": [equal]}]}
    # Both combinations are regular; a conservative analyser need not simplify them.
    assert _analyze_filter(predicate, {"a", "b"})["filter_is_regular"] is not False


def test_distinct_counts_require_both_letters_in_alphabet():
    predicate = comparison(count("a"), count("b"))
    assert _analyze_filter(predicate, {"a", "b"})["filter_is_regular"] is False
    assert _analyze_filter(predicate, {"a"})["filter_is_regular"] is True
    assert _analyze_filter(predicate)["filter_is_regular"] is None


@pytest.mark.parametrize("op", ["eq", "ne", "lt", "le", "gt", "ge"])
def test_length_versus_letter_count_is_regular(op):
    predicate = comparison({"kind": "length", "of_var": "w"}, count("a"), op)
    assert _analyze_filter(predicate, {"a", "b"})["filter_is_regular"] is True


@pytest.mark.parametrize("modulus", [0, -2, "3", True])
def test_invalid_modulus_does_not_certify_regular_filter(modulus):
    predicate = {"modulus": modulus, "remainder": 0, "expr": count("a")}
    assert _analyze_filter(predicate)["filter_is_regular"] is None


@pytest.mark.parametrize("expr", [
    {"kind": "unimplemented_expression"},
    {"kind": "count_symbol", "symbol": "a", "in_var": "unbound"},
    {"kind": "length"},
])
def test_unsupported_modular_expression_is_unknown(expr):
    assert _analyze_filter({"modulus": 3, "remainder": 0, "expr": expr})["filter_is_regular"] is None


def test_valid_modular_count_is_regular():
    assert _analyze_filter({"modulus": 3, "remainder": 0, "expr": count("a")})["filter_is_regular"] is True


def test_multiple_negation_operands_are_not_silently_ignored():
    predicate = {"op": "not", "operands": [comparison(count("a"), count("a"))] * 2}
    assert _analyze_filter(predicate)["filter_is_regular"] is None


@pytest.mark.parametrize("left", [None, [], "count_a"])
def test_malformed_expression_is_unknown(left):
    assert _analyze_filter(comparison(left, {"kind": "constant", "value": 2}))["filter_is_regular"] is None


@pytest.mark.parametrize("change", [{"start": "X"}, {"rules": [{"lhs": "SS", "rhs": ["a"]}]}])
def test_regular_filter_does_not_certify_invalid_cfg(change):
    grammar = {
        "terminals": ["a"], "nonterminals": ["S"], "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a"]}], **change,
    }
    ir = {"language_spec": {
        "kind": "grammar_filter", "grammar": grammar,
        "filter": comparison({"kind": "length", "of_var": "w"}, {"kind": "constant", "value": 1}),
    }}
    assert preprocess_language(ir)["quick_verdict"] is None
