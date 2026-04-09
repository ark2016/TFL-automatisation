"""
Regression tests:
  - Finding 2: for_all_k must be True, not just present.
  - Round 7 Finding 2: verify_marker_claim must read artifacts.ll_grammar.
"""
from __future__ import annotations

import pytest

from ll_system.lib.claim_verifier import (
    verify_substitution_claim,
    verify_prefix_classes_claim,
    verify_ll_claim,
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


# ---------------------------------------------------------------------------
# Regression: Round 7 Finding 2 — verify_marker_claim must read artifacts.ll_grammar
# ---------------------------------------------------------------------------

_VALID_GRAMMAR = {
    "nonterminals": ["S"],
    "terminals": ["a"],
    "start": "S",
    "rules": [{"lhs": "S", "rhs": ["a"]}],
}

_BROKEN_GRAMMAR = {"broken": True}


class TestMarkerAnalyzerReadsArtifactsGrammar:
    """Regression Round 7 Finding 2: verify_ll_claim dispatches marker_detection.
    The prompt places grammar in artifacts.ll_grammar, not in proof_sketch.grammar.
    Before the fix, verify_marker_claim only read proof_sketch['grammar'],
    so a broken artifacts.ll_grammar was silently ignored and the claim was 'verified'.
    """

    def _marker_agent_result(self, artifacts: dict, proof_grammar: dict | None = None) -> dict:
        ps: dict = {
            "method": "marker_detection",
            "marker_symbol": "a",
            "marker_description": "unique prefix marker",
            "ll_usage": "start of S",
            "suggested_k": 1,
        }
        if proof_grammar is not None:
            ps["grammar"] = proof_grammar
        return {
            "agent_name": "marker_analyzer",
            "verdict": "ll",
            "confidence": 0.9,
            "proof_sketch": ps,
            "artifacts": artifacts,
        }

    def test_broken_artifacts_grammar_not_verified(self):
        """Regression: artifacts.ll_grammar={'broken': True} must prevent verification."""
        agent_result = self._marker_agent_result(
            artifacts={"ll_grammar": _BROKEN_GRAMMAR}
        )
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] != "verified", (
            "A claim with a broken artifacts.ll_grammar must not be verified. "
            "Before the fix, verify_marker_claim ignored artifacts entirely."
        )

    def test_valid_artifacts_grammar_verified(self):
        """A valid grammar in artifacts.ll_grammar must allow verification."""
        agent_result = self._marker_agent_result(
            artifacts={"ll_grammar": _VALID_GRAMMAR}
        )
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] == "verified"

    def test_proof_sketch_grammar_still_works(self):
        """If grammar is already in proof_sketch, artifacts injection is a no-op."""
        agent_result = self._marker_agent_result(
            artifacts={},
            proof_grammar=_VALID_GRAMMAR,
        )
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] == "verified"

    def test_no_grammar_anywhere_still_verified_on_marker_and_explanation(self):
        """Grammar is optional in marker_detection — marker + explanation alone suffice."""
        agent_result = self._marker_agent_result(artifacts={})
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        # Grammar check is skipped (optional), so marker+explanation checks still pass
        assert result["verification_status"] == "verified"
