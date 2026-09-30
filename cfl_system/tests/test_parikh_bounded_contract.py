"""Regressions for finite-image diagnostics and exact sorted-bound certificates."""

import pytest

from cfl_system.lib.parikh import analyze_parikh, check_semilinearity
from cfl_system.lib.stratification import (
    _decompose_word,
    check_stratification,
    is_bounded_language,
)


def grammar(rules, terminals=("a", "b"), nonterminals=("S",)):
    return {
        "kind": "grammar",
        "terminals": list(terminals),
        "nonterminals": list(nonterminals),
        "start": "S",
        "rules": [{"lhs": lhs, "rhs": list(rhs)} for lhs, rhs in rules],
    }


def bounded(spec):
    return is_bounded_language({"language_spec": spec})


@pytest.mark.parametrize("vectors,dimension", [
    ({(0,), (1,), (4,), (9,)}, 1),
    ({(0, 2), (1, 1), (2, 0)}, 2),
    ({(0, 0), (2, 2), (4, 4)}, 2),
    ({()}, 0),
    (set(), 2),
])
def test_finite_representation_is_exact_singleton_union(vectors, dimension):
    result = check_semilinearity(vectors, dimension)
    assert result["is_semilinear"] is None
    assert result["sample_is_semilinear"] is True
    assert {linear["base"] for linear in result["linear_sets"]} == vectors
    assert all(linear["periods"] == [] for linear in result["linear_sets"])
    assert result["evidence_scope"] == "finite_sample"


@pytest.mark.parametrize("vectors,dimension", [({(-1,)}, 1), ({(1, 2)}, 1)])
def test_invalid_parikh_domain_rejected(vectors, dimension):
    with pytest.raises(ValueError):
        check_semilinearity(vectors, dimension)


def test_finite_square_lengths_cfg_certified_by_theorem_only():
    spec = grammar([("S", "a" * n) for n in (0, 1, 4, 9)], terminals=("a",))
    result = analyze_parikh({"language_spec": spec})
    assert result["vectors_sampled"] == [(0,), (1,), (4,), (9,)]
    assert result["looks_semilinear"] is False
    assert result["is_semilinear"] is True
    assert result["evidence_scope"] == "grammar_theorem"
    assert result["sample_provenance"]["membership_verified"] is True
    assert result["conclusion"] is None


def test_no_sample_does_not_mean_empty_full_language():
    spec = grammar([("S", "a" * 20)], terminals=("a",))
    parikh = analyze_parikh({"language_spec": spec})
    assert parikh["vectors_sampled"] == []
    assert parikh["is_semilinear"] is True  # Explicit CFG theorem, no inference from []
    stratification = check_stratification(spec, ["a"], max_length=10)
    assert stratification["exponent_vectors"] == []
    assert stratification["is_cfl"] is None
    assert stratification["is_stratified"] is None


def test_ignored_filter_exposes_candidate_provenance():
    spec = {
        "kind": "grammar_filter",
        "grammar": grammar([("S", "aS"), ("S", "")], terminals=("a",)),
        "filter": {
            "op": "eq",
            "left": {"kind": "length", "of_var": "w"},
            "right": {"kind": "constant", "value": 2},
        },
    }
    result = analyze_parikh({"language_spec": spec})
    assert (0,) in result["vectors_sampled"]  # This candidate violates the filter.
    assert result["sample_provenance"]["kind"] == "candidate_superset"
    assert result["sample_provenance"]["membership_verified"] is False
    assert result["is_semilinear"] is None
    assert result["conclusion"] is None


def test_ignored_repeated_constraint_exposes_candidate_provenance():
    spec = {
        "kind": "repeated_subword",
        "parts": ["x"],
        "concat_pattern": ["x", "x"],
        "alphabets": {"x": ["a"]},
        "constraints": [{
            "op": "lt",
            "left": {"kind": "length", "of_var": "x"},
            "right": {"kind": "constant", "value": 1},
        }],
    }
    result = analyze_parikh({"language_spec": spec})
    assert (2,) in result["vectors_sampled"]
    assert result["sample_provenance"]["kind"] == "candidate_superset"
    assert result["is_semilinear"] is None
    assert result["conclusion"] is None


@pytest.mark.parametrize("spec", [
    {"kind": "grammar", "terminals": ["a"]},
    grammar([("X", "a")], terminals=("a",)),
    grammar([("S", "z")], terminals=("a",)),
    grammar([("S", "a")], terminals=("a",), nonterminals=("a", "S")),
])
def test_invalid_cfg_cannot_trigger_theorem_or_bounded_certificate(spec):
    assert analyze_parikh({"language_spec": spec})["is_semilinear"] is None
    assert bounded(spec)["is_bounded"] is None


def test_local_rhs_order_does_not_certify_sigma_star_as_sorted():
    spec = grammar([("S", "aS"), ("S", "bS"), ("S", "")])
    result = bounded(spec)
    assert result["is_bounded"] is None
    assert result["bounding_words"] is None
    assert result["sorted_template_inclusion"] is False


def test_sorted_bound_uses_nested_nullable_unit_cycle_relations():
    spec = grammar([
        ("S", ["A"]), ("A", ["S"]),
        ("A", ["a", "A", "b"]), ("A", []),
        ("U", ["b", "a"]),  # Unreachable unsorted rule must not refute inclusion.
    ], nonterminals=("S", "A", "U"))
    result = bounded(spec)
    assert result["is_bounded"] is True
    assert result["bounding_words"] == ["a", "b"]
    assert result["evidence_scope"] == "cfg_dfa_fixed_point"


def test_unsorted_finite_language_only_refutes_the_proposed_template():
    # {ba} is bounded (by (ba)*), even though it is not contained in a*b*.
    result = bounded(grammar([("S", "ba")]))
    assert result["is_bounded"] is None
    assert result["sorted_template_inclusion"] is False


def test_empty_alphabet_grammar_has_exact_epsilon_bound():
    result = bounded(grammar([("S", "")], terminals=()))
    assert result["is_bounded"] is True
    assert result["bounding_words"] == []


@pytest.mark.parametrize("word,bounds,expected", [
    ("ab", ["a", "ab"], (0, 1)),
    ("aab", ["a", "ab"], (1, 1)),
    ("ab", ["", "a", "ab"], (0, 0, 1)),
    ("", ["", "ab"], (0, 0)),
])
def test_decomposition_backtracks_over_overlapping_bounds(word, bounds, expected):
    assert _decompose_word(word, bounds) == expected


def test_ambiguous_exponent_sample_does_not_claim_complete_preimage():
    result = check_stratification(grammar([("S", "aaa")], terminals=("a",)), ["a", "aa"])
    assert result["exponent_vectors"] == [(3, 0)]
    assert result["exponent_scope"] == "one_decomposition_per_sampled_word"
    assert result["is_stratified"] is None
    assert result["is_cfl"] is None
