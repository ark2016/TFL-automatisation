"""
Regression tests for Finding 2: for_all_k must be True, not just present.
"""
from __future__ import annotations

import pytest

from ll_system.lib.claim_verifier import (
    verify_substitution_claim,
    verify_prefix_classes_claim,
)

IR_SIMPLE = {
    "task_type": "ll_check_grammar",
    "source_text": "test",
    "grammar": {
        "nonterminals": ["S"],
        "terminals": ["a"],
        "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a"]}],
    },
}

BASE_WITNESS = {
    "k": "arbitrary", "w1": "a^n",
    "lookahead": "a^k", "suffix_1": "b^n", "suffix_2": "c^n",
    "why_not_in_L": "incompatible continuations",
}


class TestForAllKMustBeTrue:
    """Regression Finding 2: verifiers accepted for_all_k: false as a valid proof."""

    def test_substitution_for_all_k_true_verified(self):
        ps = {
            "method": "substitution", "for_all_k": True,
            "witness": BASE_WITNESS,
        }
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] == "verified"

    def test_substitution_for_all_k_false_not_verified(self):
        """Regression: for_all_k: False was previously accepted as verified."""
        ps = {
            "method": "substitution", "for_all_k": False,
            "witness": BASE_WITNESS,
        }
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified", (
            "A proof valid only for a fixed k must not be verified"
        )
        assert any("for_all_k" in i for i in result["issues"])

    def test_substitution_for_all_k_missing_not_verified(self):
        """Missing for_all_k field must also fail."""
        ps = {"method": "substitution", "witness": BASE_WITNESS}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"
        assert any("for_all_k" in i for i in result["issues"])

    def test_substitution_for_all_k_none_not_verified(self):
        ps = {"method": "substitution", "for_all_k": None, "witness": BASE_WITNESS}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"

    def test_prefix_classes_for_all_k_true_verified(self):
        ps = {
            "method": "prefix_classes",
            "for_all_k": True,
            "prefix_family": {"description": "u_n = a^n", "parametrization": "a^n"},
            "distinguishability_argument": {"why_distinguishable": "different completions"},
        }
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] == "verified"

    def test_prefix_classes_for_all_k_false_not_verified(self):
        """Regression: prefix_classes with for_all_k: False was accepted as verified."""
        ps = {
            "method": "prefix_classes",
            "for_all_k": False,
            "prefix_family": {"description": "u_n = a^n", "parametrization": "a^n"},
            "distinguishability_argument": {"why_distinguishable": "different completions"},
        }
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified", (
            "prefix_classes proof with for_all_k: False must not be verified"
        )
        assert any("for_all_k" in i for i in result["issues"])

    def test_prefix_classes_for_all_k_missing_not_verified(self):
        ps = {
            "method": "prefix_classes",
            "prefix_family": {"description": "u_n = a^n", "parametrization": "a^n"},
            "distinguishability_argument": {"why_distinguishable": "different completions"},
        }
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"
        assert any("for_all_k" in i for i in result["issues"])
