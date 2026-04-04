"""Tests for the claim verifier module."""

from __future__ import annotations

import pytest

from cfl_system.lib.claim_verifier import (
    verify_agent_claims,
    verify_closure_claim,
    verify_decomposition_claim,
    verify_parikh_claim,
    verify_pumping_claim,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A simple grammar for a^n b^n (n >= 0): S -> a S b | eps
_ANBN_GRAMMAR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

_IR_ANBN = {
    "language_spec": {
        "kind": "grammar",
        **_ANBN_GRAMMAR,
    },
}

# Dummy IR that won't build an oracle (for tests that don't need one)
_IR_DUMMY = {"language_spec": {"kind": "predicate", "alphabet": ["a"], "variable": "w",
                                "predicate": {"op": "eq",
                                              "left": {"kind": "length", "of_var": "w"},
                                              "right": {"kind": "constant", "value": 0}}}}


# ---------------------------------------------------------------------------
# verify_pumping_claim
# ---------------------------------------------------------------------------

class TestVerifyPumpingClaim:
    def test_valid_proof_verified(self):
        evidence = {
            "word_chosen": "aabb",
            "cases": [
                {"case": "v in a*, x in a*", "why_not_in_L": "more a's than b's"},
                {"case": "v in a*, x in b*", "why_not_in_L": "count mismatch"},
            ],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "verified"
        assert result["checks_passed"] == result["checks_total"]
        assert result["issues"] == []

    def test_word_not_in_language_refuted(self):
        evidence = {
            "word_chosen": "aab",  # not in a^n b^n
            "cases": [{"case": "any", "why_not_in_L": "reason"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "refuted"
        assert any("NOT in L" in i for i in result["issues"])

    def test_missing_cases_inconclusive(self):
        evidence = {
            "word_chosen": "aabb",
            "cases": [],
            "all_cases_covered": False,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "inconclusive"
        assert any("No cases" in i for i in result["issues"])

    def test_parametric_word_skips_oracle(self):
        """Parametric words like 'a^n b^n' should not be oracle-checked."""
        evidence = {
            "word_chosen": "a^p b^p",
            "cases": [{"case": "general", "why_not_in_L": "mismatch"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        # Should still pass structural checks
        assert result["verification_status"] == "verified"

    def test_case_missing_fields(self):
        evidence = {
            "word_chosen": "aabb",
            "cases": [{"description": "incomplete case"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "inconclusive"
        assert any("missing" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_closure_claim
# ---------------------------------------------------------------------------

class TestVerifyClosureClaim:
    def test_valid_claim_verified(self):
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "L ∩ R = {a^n b^n}",
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "v in a*", "why_not_in_L": "mismatch"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "verified"
        assert result["issues"] == []

    def test_invalid_regex_reported(self):
        evidence = {
            "regular_language_regex": "[invalid((",
            "intersection_description": "some description",
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert any("invalid" in i for i in result["issues"])

    def test_missing_intersection_proof(self):
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "some desc",
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "inconclusive"
        assert any("No proof" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_decomposition_claim
# ---------------------------------------------------------------------------

class TestVerifyDecompositionClaim:
    def test_valid_concat_verified(self):
        evidence = {
            "components": [
                {"name": "L1", "is_cfl": True},
                {"name": "L2", "is_cfl": True},
            ],
            "operation": "concatenation",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "verified"
        assert result["issues"] == []

    def test_non_cfl_closed_operation(self):
        evidence = {
            "components": [
                {"name": "L1", "is_cfl": True},
                {"name": "L2", "is_cfl": True},
            ],
            "operation": "intersection",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "inconclusive"
        assert any("may not preserve CFL" in i for i in result["issues"])

    def test_no_components(self):
        evidence = {"operation": "union"}
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert any("No components" in i for i in result["issues"])

    def test_component_not_claimed_cfl(self):
        evidence = {
            "components": [{"name": "L1", "is_cfl": False}],
            "operation": "union",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert any("not claimed CFL" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_parikh_claim
# ---------------------------------------------------------------------------

class TestVerifyParikhClaim:
    def test_not_semilinear_correct_conclusion(self):
        evidence = {
            "conclusion": "Language is not CFL (non-semilinear Parikh image)",
            "is_semilinear": False,
        }
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "verified"
        assert result["issues"] == []

    def test_semilinear_undetermined(self):
        evidence = {"conclusion": "unknown"}
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "inconclusive"
        assert any("Semilinearity" in i for i in result["issues"])

    def test_not_semilinear_wrong_conclusion(self):
        evidence = {
            "conclusion": "Language is CFL",
            "is_semilinear": False,
        }
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert any("doesn't say not CFL" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_agent_claims (dispatcher)
# ---------------------------------------------------------------------------

class TestVerifyAgentClaims:
    def test_dispatch_pumping(self):
        agent_output = {
            "agent": "pumping_cfl",
            "status": "success",
            "evidence": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        assert result["agent"] == "pumping_cfl"
        assert result["verification_status"] == "verified"

    def test_failed_agent_inconclusive(self):
        agent_output = {
            "agent": "pumping_cfl",
            "status": "error",
            "evidence": {},
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        assert result["verification_status"] == "inconclusive"
        assert any("error" in i for i in result["issues"])

    def test_unknown_agent_inconclusive(self):
        agent_output = {
            "agent": "cfg_builder",
            "status": "success",
            "evidence": {},
        }
        result = verify_agent_claims(agent_output, _IR_DUMMY)
        assert result["verification_status"] == "inconclusive"
        assert any("No specific verifier" in i for i in result["issues"])

    def test_ogden_dispatches_to_pumping(self):
        agent_output = {
            "agent": "ogden",
            "status": "success",
            "evidence": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        # ogden reuses pumping verifier
        assert result["agent"] == "pumping_cfl"
        assert result["verification_status"] == "verified"
