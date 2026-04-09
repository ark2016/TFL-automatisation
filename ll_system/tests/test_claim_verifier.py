"""Tests for claim_verifier.py"""
from __future__ import annotations

import pytest
from ll_system.lib.claim_verifier import (
    verify_ll_claim,
    verify_ll_grammar_claim,
    verify_substitution_claim,
    verify_grammar_transformation_claim,
    verify_marker_claim,
    _make_result,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

GRAMMAR_LL1 = {
    "nonterminals": ["S", "A", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A", "b"]},
        {"lhs": "S", "rhs": ["b", "B", "a"]},
        {"lhs": "A", "rhs": ["a", "A"]},
        {"lhs": "A", "rhs": []},
        {"lhs": "B", "rhs": ["b", "B"]},
        {"lhs": "B", "rhs": []},
    ],
}

IR_SIMPLE = {
    "task_type": "ll_check_grammar",
    "source_text": "test",
    "grammar": GRAMMAR_LL1,
    "question": "is_ll_k",
    "k": None,
}

# Valid LL grammar claim
AGENT_LL_CLAIM = {
    "agent_name": "ll_grammar_builder",
    "verdict": "ll",
    "confidence": 0.9,
    "proof_sketch": {
        "method": "ll_grammar_construction",
        "k": 1,
        "grammar": GRAMMAR_LL1,
        "explanation": "Grammar is LL(1)",
    },
    "artifacts": {},
}

# Valid substitution claim (not_ll)
AGENT_NOT_LL_CLAIM = {
    "agent_name": "substitution_agent",
    "verdict": "not_ll",
    "confidence": 0.95,
    "proof_sketch": {
        "method": "substitution",
        "for_all_k": True,
        "k": "k (arbitrary)",
        "witness": {
            "k": "k (arbitrary)",
            "w1": "a^{n+k}",
            "lookahead": "b^k",
            "suffix_1": "b^{n}",
            "suffix_2": "c^{n}",
            "substitution_result": "a^{n+k} b^{k} c^{n}",
            "why_not_in_L": "not in {aⁿbⁿ} ∪ {aⁿcⁿ}",
        },
    },
    "artifacts": {"counterexample_words": ["a^{n+k} b^{k} c^{n}"]},
}

# Uncertain claim
AGENT_UNCERTAIN = {
    "agent_name": "ambiguity_detector",
    "verdict": "uncertain",
    "confidence": 0.3,
    "proof_sketch": None,
    "artifacts": {},
}

# Grammar with no nonterminals field (invalid)
GRAMMAR_MISSING_FIELD = {
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [{"lhs": "S", "rhs": ["a"]}],
}

# Valid transformation claim
AGENT_TRANSFORMATION_CLAIM = {
    "agent_name": "grammar_transformer",
    "verdict": "ll",
    "confidence": 0.85,
    "proof_sketch": {
        "method": "grammar_transformation",
        "k": 1,
        "original_grammar": {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["S", "a"]},
                {"lhs": "S", "rhs": ["a"]},
            ],
        },
        "transformed_grammar": GRAMMAR_LL1,
        "transformation_steps": [
            "Eliminate left recursion in S",
            "Factor common prefix",
        ],
        "conflicts_remaining": [],
    },
    "artifacts": {},
}

# Valid marker detection claim
AGENT_MARKER_CLAIM = {
    "agent_name": "marker_detector",
    "verdict": "ll",
    "confidence": 0.88,
    "proof_sketch": {
        "method": "marker_detection",
        "marker": "#",
        "k": 1,
        "grammar": GRAMMAR_LL1,
        "explanation": "The # marker unambiguously separates the two halves",
    },
    "artifacts": {},
}


# ---------------------------------------------------------------------------
# Helper: verify result dict has correct shape
# ---------------------------------------------------------------------------

RESULT_KEYS = {"agent", "verification_status", "checks_passed", "checks_total", "issues", "details"}
VALID_STATUSES = {"verified", "refuted", "inconclusive", "error"}


def _assert_result_shape(result: dict) -> None:
    assert RESULT_KEYS.issubset(result.keys()), f"Missing keys: {RESULT_KEYS - result.keys()}"
    assert result["verification_status"] in VALID_STATUSES
    assert isinstance(result["checks_passed"], int)
    assert isinstance(result["checks_total"], int)
    assert isinstance(result["issues"], list)
    assert isinstance(result["details"], dict)


# ---------------------------------------------------------------------------
# Tests: verify_ll_claim dispatcher
# ---------------------------------------------------------------------------

class TestVerifyLlClaim:
    def test_uncertain_verdict_is_inconclusive(self):
        result = verify_ll_claim(AGENT_UNCERTAIN, IR_SIMPLE)
        assert result["verification_status"] == "inconclusive"

    def test_uncertain_result_shape(self):
        result = verify_ll_claim(AGENT_UNCERTAIN, IR_SIMPLE)
        _assert_result_shape(result)

    def test_no_proof_sketch_is_inconclusive(self):
        agent_result = {
            "agent_name": "test_agent",
            "verdict": "ll",
            "confidence": 0.5,
            "proof_sketch": None,
            "artifacts": {},
        }
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] == "inconclusive"

    def test_no_proof_sketch_result_shape(self):
        agent_result = {
            "agent_name": "test_agent",
            "verdict": "ll",
            "confidence": 0.5,
            "proof_sketch": None,
            "artifacts": {},
        }
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        _assert_result_shape(result)

    def test_unknown_method_is_inconclusive(self):
        agent_result = {
            "agent_name": "mystery_agent",
            "verdict": "ll",
            "confidence": 0.5,
            "proof_sketch": {"method": "totally_unknown_method"},
            "artifacts": {},
        }
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] == "inconclusive"

    def test_ll_grammar_claim_status_verified_or_inconclusive(self):
        result = verify_ll_claim(AGENT_LL_CLAIM, IR_SIMPLE)
        # If table builder is available, verified; otherwise inconclusive
        assert result["verification_status"] in ("verified", "inconclusive")

    def test_ll_grammar_claim_result_shape(self):
        result = verify_ll_claim(AGENT_LL_CLAIM, IR_SIMPLE)
        _assert_result_shape(result)

    def test_not_ll_claim_not_error(self):
        result = verify_ll_claim(AGENT_NOT_LL_CLAIM, IR_SIMPLE)
        assert result["verification_status"] != "error"

    def test_not_ll_claim_checks_total_positive(self):
        result = verify_ll_claim(AGENT_NOT_LL_CLAIM, IR_SIMPLE)
        assert result["checks_total"] > 0

    def test_not_ll_claim_result_shape(self):
        result = verify_ll_claim(AGENT_NOT_LL_CLAIM, IR_SIMPLE)
        _assert_result_shape(result)

    def test_transformation_claim_dispatches(self):
        result = verify_ll_claim(AGENT_TRANSFORMATION_CLAIM, IR_SIMPLE)
        _assert_result_shape(result)

    def test_marker_claim_dispatches(self):
        result = verify_ll_claim(AGENT_MARKER_CLAIM, IR_SIMPLE)
        _assert_result_shape(result)


# ---------------------------------------------------------------------------
# Tests: verify_ll_grammar_claim
# ---------------------------------------------------------------------------

class TestVerifyLlGrammarClaim:
    def test_valid_claim_passes_structural_checks(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": 1,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        _assert_result_shape(result)
        # At least structural checks should pass (grammar present, fields OK, k OK)
        assert result["checks_passed"] >= 3

    def test_missing_grammar_raises_issue(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": 1,
            "grammar": None,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert any("grammar" in i.lower() for i in result["issues"])

    def test_grammar_missing_nonterminals_raises_issue(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": 1,
            "grammar": GRAMMAR_MISSING_FIELD,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert any("nonterminals" in i for i in result["issues"])

    def test_k_zero_raises_issue(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": 0,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert any("k" in i.lower() for i in result["issues"])

    def test_k_negative_raises_issue(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": -1,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert any("k" in i.lower() for i in result["issues"])

    def test_k_none_raises_issue(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": None,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert any("k" in i.lower() for i in result["issues"])

    def test_result_has_all_keys(self):
        proof = {
            "method": "ll_grammar_construction",
            "k": 1,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_ll_grammar_claim(proof, IR_SIMPLE)
        assert RESULT_KEYS.issubset(result.keys())


# ---------------------------------------------------------------------------
# Tests: verify_substitution_claim
# ---------------------------------------------------------------------------

class TestVerifySubstitutionClaim:
    def test_complete_witness_passes_checks(self):
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert result["checks_passed"] > 0

    def test_complete_witness_result_shape(self):
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        _assert_result_shape(result)

    def test_missing_witness_raises_issue(self):
        proof = {
            "method": "substitution",
            "for_all_k": True,
            "k": "k",
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert len(result["issues"]) > 0

    def test_missing_why_not_in_L_raises_issue(self):
        proof = {
            "method": "substitution",
            "for_all_k": True,
            "k": "k",
            "witness": {
                "k": "k",
                "w1": "a^n",
                "lookahead": "b^k",
                "suffix_1": "b^n",
                "suffix_2": "c^n",
                # missing why_not_in_L
            },
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("why_not_in_L" in i for i in result["issues"])

    def test_missing_for_all_k_raises_issue(self):
        proof = {
            "method": "substitution",
            "k": "k",
            "witness": {
                "k": "k",
                "w1": "a^n",
                "lookahead": "b^k",
                "suffix_1": "b^n",
                "suffix_2": "c^n",
                "why_not_in_L": "contradiction",
            },
            # missing for_all_k
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("for_all_k" in i for i in result["issues"])

    def test_wrong_method_raises_issue(self):
        proof = dict(AGENT_NOT_LL_CLAIM["proof_sketch"])
        proof["method"] = "pumping"
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("substitution" in i.lower() for i in result["issues"])

    def test_result_has_all_keys(self):
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert RESULT_KEYS.issubset(result.keys())

    def test_checks_total_positive(self):
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert result["checks_total"] > 0


# ---------------------------------------------------------------------------
# Tests: verify_grammar_transformation_claim
# ---------------------------------------------------------------------------

class TestVerifyGrammarTransformationClaim:
    def test_valid_claim_result_shape(self):
        proof = AGENT_TRANSFORMATION_CLAIM["proof_sketch"]
        result = verify_grammar_transformation_claim(proof, IR_SIMPLE)
        _assert_result_shape(result)

    def test_missing_transformed_grammar_raises_issue(self):
        proof = {
            "method": "grammar_transformation",
            "k": 1,
            "transformation_steps": ["step1"],
            "conflicts_remaining": [],
        }
        result = verify_grammar_transformation_claim(proof, IR_SIMPLE)
        assert any("transformed_grammar" in i for i in result["issues"])

    def test_non_empty_conflicts_remaining_raises_issue(self):
        proof = {
            "method": "grammar_transformation",
            "k": 1,
            "transformed_grammar": GRAMMAR_LL1,
            "transformation_steps": ["step1"],
            "conflicts_remaining": [{"conflict": "first_first"}],
        }
        result = verify_grammar_transformation_claim(proof, IR_SIMPLE)
        assert any("conflicts_remaining" in i for i in result["issues"])

    def test_checks_passed_positive_for_valid_claim(self):
        proof = AGENT_TRANSFORMATION_CLAIM["proof_sketch"]
        result = verify_grammar_transformation_claim(proof, IR_SIMPLE)
        assert result["checks_passed"] > 0


# ---------------------------------------------------------------------------
# Tests: verify_marker_claim
# ---------------------------------------------------------------------------

class TestVerifyMarkerClaim:
    def test_valid_claim_result_shape(self):
        proof = AGENT_MARKER_CLAIM["proof_sketch"]
        result = verify_marker_claim(proof, IR_SIMPLE)
        _assert_result_shape(result)

    def test_missing_marker_raises_issue(self):
        proof = {
            "method": "marker_detection",
            "k": 1,
            "explanation": "There is a marker",
        }
        result = verify_marker_claim(proof, IR_SIMPLE)
        assert any("marker" in i.lower() for i in result["issues"])

    def test_missing_explanation_raises_issue(self):
        proof = {
            "method": "marker_detection",
            "marker": "#",
            "k": 1,
        }
        result = verify_marker_claim(proof, IR_SIMPLE)
        assert any("explanation" in i.lower() for i in result["issues"])

    def test_checks_passed_positive_for_valid_claim(self):
        proof = AGENT_MARKER_CLAIM["proof_sketch"]
        result = verify_marker_claim(proof, IR_SIMPLE)
        assert result["checks_passed"] > 0


# ---------------------------------------------------------------------------
# Tests: _make_result helper
# ---------------------------------------------------------------------------

class TestMakeResult:
    def test_make_result_has_all_keys(self):
        result = _make_result("agent_x", "verified", 3, 3, [])
        assert RESULT_KEYS.issubset(result.keys())

    def test_make_result_default_details(self):
        result = _make_result("agent_x", "verified", 3, 3, [])
        assert result["details"] == {}

    def test_make_result_custom_details(self):
        result = _make_result("agent_x", "verified", 3, 3, [], details={"foo": "bar"})
        assert result["details"] == {"foo": "bar"}

    def test_make_result_status_preserved(self):
        for status in ("verified", "refuted", "inconclusive", "error"):
            result = _make_result("a", status, 0, 0, [])
            assert result["verification_status"] == status
