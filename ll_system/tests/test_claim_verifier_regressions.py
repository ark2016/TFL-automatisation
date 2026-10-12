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

# No word oracle can be built for a natural-language spec, so step 2 leaves
# claims at well_formed (explicit-grammar tasks: test_word_oracle_grammar.py).
IR_SIMPLE = {
    "task_type": "ll_check_language",
    "source_text": "test",
    "language_spec": {"kind": "natural", "description": "test language"},
}

BASE_BRANCH_WORDS = {
    "common_prefix": "a^j", "word_1": "a^n b^n", "word_2": "a^n c^n",
    "lookahead_equal_because": "both still inside the a-block for n > k",
}
BASE_SUBSTITUTION_PS = {
    "method": "substitution",
    "branch_words": BASE_BRANCH_WORDS,
    "common_form_argument": "both derivations pass through a common sentential form a^j · delta",
    "deciding_nonterminal_argument": "a unique X_t* produces b^n in one run and c^n in the other",
    "pigeonhole_argument": "finitely many pairs (X_t*, s), infinitely many n -> two share a pair",
}

BASE_PREFIX_CLASSES_PS = {
    "method": "prefix_classes",
    "theorem": "Shallit 4.7.4 -> not DCFL -> not LL",
    "dead_class_finite": "D = empty: every prefix extends into a palindrome",
    "distinguishing_suffix": "w = b a^N b u^R, N = 2|uv|",
    "separation_argument": "exactly one of uw, vw is a palindrome for u != v",
    "conclusion": "all Nerode classes are singletons -> not DCFL -> not LL for any k",
}


class TestForAllKMustBeTrue:
    """Regression Finding 2: verifiers accepted for_all_k: false as a valid proof."""

    def test_substitution_for_all_k_true_verified(self):
        ps = {**BASE_SUBSTITUTION_PS, "for_all_k": True, "proof_explanation": "full proof"}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1: structural pass, no oracle, result["issues"]

    def test_substitution_for_all_k_false_not_verified(self):
        """Regression: for_all_k: False was previously accepted as verified."""
        ps = {**BASE_SUBSTITUTION_PS, "for_all_k": False, "proof_explanation": "full proof"}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified", (
            "A proof valid only for a fixed k must not be verified"
        )
        assert any("for_all_k" in i for i in result["issues"])

    def test_substitution_for_all_k_missing_not_verified(self):
        """Missing for_all_k field must also fail."""
        ps = {**BASE_SUBSTITUTION_PS, "proof_explanation": "full proof"}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"
        assert any("for_all_k" in i for i in result["issues"])

    def test_substitution_for_all_k_none_not_verified(self):
        ps = {**BASE_SUBSTITUTION_PS, "for_all_k": None, "proof_explanation": "full proof"}
        result = verify_substitution_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"

    def test_prefix_classes_for_all_k_true_verified(self):
        ps = {**BASE_PREFIX_CLASSES_PS, "for_all_k": True, "proof_explanation": "full proof"}
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1: structural pass, no oracle, result["issues"]

    def test_prefix_classes_for_all_k_false_not_verified(self):
        """Regression: prefix_classes with for_all_k: False was accepted as verified."""
        ps = {**BASE_PREFIX_CLASSES_PS, "for_all_k": False, "proof_explanation": "full proof"}
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] != "verified", (
            "prefix_classes proof with for_all_k: False must not be verified"
        )
        assert any("for_all_k" in i for i in result["issues"])

    def test_prefix_classes_for_all_k_missing_not_verified(self):
        ps = {**BASE_PREFIX_CLASSES_PS, "proof_explanation": "full proof"}
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
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1: structural pass, no oracle

    def test_proof_sketch_grammar_still_works(self):
        """If grammar is already in proof_sketch, artifacts injection is a no-op."""
        agent_result = self._marker_agent_result(
            artifacts={},
            proof_grammar=_VALID_GRAMMAR,
        )
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1: structural pass, no oracle

    def test_no_grammar_anywhere_still_verified_on_marker_and_explanation(self):
        """Grammar is optional in marker_detection — marker + explanation alone suffice."""
        agent_result = self._marker_agent_result(artifacts={})
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        # Grammar check is skipped (optional), so marker+explanation checks still pass
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1: structural pass, no oracle
