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

    def test_make_result_has_status_alias(self):
        """Regression Fix 4: result must carry both 'verification_status' and 'status'."""
        result = _make_result("a", "verified", 3, 3, [])
        assert "status" in result, "reasoning/formalizer prompts read 'status', not 'verification_status'"
        assert result["status"] == result["verification_status"]


# ---------------------------------------------------------------------------
# Regression: Fix 1a — grammar_builder uses 'll_grammar' in proof_sketch
# ---------------------------------------------------------------------------


class TestGrammarBuilderLlGrammarKey:
    """Verify verify_ll_grammar_claim accepts 'll_grammar' as well as 'grammar'."""

    def _make_claim(self, grammar_key: str) -> dict:
        return {
            "method": "ll_grammar_construction",
            "k": 1,
            grammar_key: GRAMMAR_LL1,
        }

    def test_grammar_key_verified(self):
        result = verify_ll_grammar_claim(self._make_claim("grammar"), IR_SIMPLE)
        assert result["verification_status"] != "error"

    def test_ll_grammar_key_also_verified(self):
        """Regression: prompt writes 'll_grammar', verifier was reading only 'grammar'."""
        result = verify_ll_grammar_claim(self._make_claim("ll_grammar"), IR_SIMPLE)
        assert result["verification_status"] != "inconclusive" or result["checks_passed"] > 0
        # Must not report "No grammar provided"
        assert not any("No grammar" in i for i in result["issues"])

    def test_ll_grammar_key_checks_passed(self):
        result = verify_ll_grammar_claim(self._make_claim("ll_grammar"), IR_SIMPLE)
        assert result["checks_passed"] >= 1

    def test_neither_key_gives_inconclusive(self):
        result = verify_ll_grammar_claim({"method": "ll_grammar_construction", "k": 1}, IR_SIMPLE)
        assert any("No grammar" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Regression: Fix 1b — grammar_transformer uses 'transformation_log' and 'conflicts'
# ---------------------------------------------------------------------------


class TestGrammarTransformerPromptFieldNames:
    """Verify verify_grammar_transformation_claim accepts prompt-shaped output."""

    BASE = {
        "method": "grammar_transformation",
        "k": 1,
        "original_grammar": GRAMMAR_LL1,
        "transformed_grammar": GRAMMAR_LL1,
    }

    def test_old_field_names_still_work(self):
        ps = {**self.BASE, "transformation_steps": ["step A"], "conflicts_remaining": []}
        result = verify_grammar_transformation_claim(ps, IR_SIMPLE)
        assert not any("transformation_steps" in i or "transformation_log" in i for i in result["issues"])

    def test_transformation_log_accepted(self):
        """Regression: prompt writes 'transformation_log', verifier was reading 'transformation_steps'."""
        ps = {
            **self.BASE,
            "transformation_log": [
                {
                    "step": "eliminate_left_recursion",
                    "input_rules": [],
                    "output_rules": [],
                    "explanation": "applied LR elimination",
                }
            ],
            "conflicts": [],
        }
        result = verify_grammar_transformation_claim(ps, IR_SIMPLE)
        assert not any("No 'transformation_steps'" in i for i in result["issues"])

    def test_conflicts_key_accepted(self):
        """Regression: prompt writes 'conflicts' (empty list), verifier was reading 'conflicts_remaining'."""
        ps = {**self.BASE, "transformation_log": ["step A"], "conflicts": []}
        result = verify_grammar_transformation_claim(ps, IR_SIMPLE)
        assert not any("non-empty" in i for i in result["issues"])

    def test_non_empty_conflicts_still_fails(self):
        ps = {**self.BASE, "transformation_log": ["step A"], "conflicts": [{"nt": "S"}]}
        result = verify_grammar_transformation_claim(ps, IR_SIMPLE)
        assert any("non-empty" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Regression: Fix 2 — substitution uses 'lookahead_v' and 'why_not_ll'
# ---------------------------------------------------------------------------


class TestSubstitutionPromptFieldNames:
    """Verify verify_substitution_claim accepts prompt-shaped witness."""

    BASE_WITNESS_OLD = {
        "k": "k (arbitrary)",
        "w1": "a^n",
        "lookahead": "a^k",
        "suffix_1": "b^n",
        "suffix_2": "c^n",
        "why_not_in_L": "incompatible continuations",
    }

    BASE_WITNESS_NEW = {
        "k": "k (arbitrary)",
        "w1": "a^n",
        "lookahead_v": "a^k",
        "suffix_1": "b^n",
        "suffix_2": "c^n",
        "why_not_ll": "incompatible continuations",
    }

    def _make_proof(self, witness: dict) -> dict:
        return {"method": "substitution", "for_all_k": True, "witness": witness}

    def test_old_field_names_still_work(self):
        result = verify_substitution_claim(self._make_proof(self.BASE_WITNESS_OLD), IR_SIMPLE)
        assert result["verification_status"] == "verified"

    def test_lookahead_v_accepted(self):
        """Regression: prompt writes 'lookahead_v', verifier required 'lookahead'."""
        result = verify_substitution_claim(self._make_proof(self.BASE_WITNESS_NEW), IR_SIMPLE)
        assert result["verification_status"] == "verified", result["issues"]

    def test_why_not_ll_accepted(self):
        """Regression: prompt writes 'why_not_ll', verifier required 'why_not_in_L'."""
        result = verify_substitution_claim(self._make_proof(self.BASE_WITNESS_NEW), IR_SIMPLE)
        assert not any("why_not_in_L" in i for i in result["issues"])

    def test_prompt_shaped_payload_fully_verified(self):
        """End-to-end: a payload shaped exactly like the prompt output must be verified."""
        prompt_payload = {
            "method": "substitution",
            "for_all_k": True,
            "witness": {
                "k": "k (arbitrary)",
                "n": "k + 1",
                "w1": "a^{n-k}",
                "lookahead_v": "a^k",
                "suffix_1": "b^n",
                "suffix_2": "c^n",
                "word_1": "a^n b^n",
                "word_2": "a^n c^n",
                "word_1_in_L": True,
                "word_2_in_L": True,
                "why_not_ll": "После прочтения w₁ парсер в одном состоянии стека для обоих слов.",
            },
            "proof_explanation": "full formal proof",
        }
        result = verify_substitution_claim(prompt_payload, IR_SIMPLE)
        assert result["verification_status"] == "verified", result["issues"]


# ---------------------------------------------------------------------------
# Regression: Fix 3 — marker uses 'marker_symbol', 'marker_description', 'suggested_k'
# ---------------------------------------------------------------------------


class TestMarkerPromptFieldNames:
    """Verify verify_marker_claim accepts prompt-shaped proof_sketch."""

    def test_old_field_names_still_work(self):
        ps = {
            "method": "marker_detection",
            "marker": "#",
            "explanation": "The # separates left and right halves",
            "k": 1,
        }
        result = verify_marker_claim(ps, IR_SIMPLE)
        assert result["checks_passed"] >= 2

    def test_marker_symbol_key_accepted(self):
        """Regression: prompt writes 'marker_symbol', verifier was reading 'marker'."""
        ps = {
            "method": "marker_detection",
            "marker_symbol": "c",
            "marker_description": "The letter c separates the two subwords.",
            "suggested_k": 1,
        }
        result = verify_marker_claim(ps, IR_SIMPLE)
        assert not any("No 'marker'" in i for i in result["issues"]), result["issues"]

    def test_marker_description_as_explanation(self):
        """Regression: prompt writes 'marker_description', verifier read 'explanation'."""
        ps = {
            "method": "marker_detection",
            "marker_symbol": "#",
            "marker_description": "hash separates halves",
            "suggested_k": 1,
        }
        result = verify_marker_claim(ps, IR_SIMPLE)
        assert not any("explanation" in i.lower() for i in result["issues"])

    def test_ll_usage_as_explanation(self):
        """Regression: prompt also writes 'll_usage' — accept as explanation fallback."""
        ps = {
            "method": "marker_detection",
            "marker_symbol": "#",
            "ll_usage": "parser uses # to choose rule",
            "suggested_k": 1,
        }
        result = verify_marker_claim(ps, IR_SIMPLE)
        assert not any("explanation" in i.lower() for i in result["issues"])

    def test_suggested_k_used_for_oracle(self):
        """Regression: prompt writes 'suggested_k', verifier read 'k' for check_ll_k."""
        ps = {
            "method": "marker_detection",
            "marker_symbol": "c",
            "marker_description": "unique separator",
            "suggested_k": 1,
            "grammar": GRAMMAR_LL1,
        }
        result = verify_marker_claim(ps, IR_SIMPLE)
        # With grammar + suggested_k, oracle should run and pass for GRAMMAR_LL1
        assert result["checks_passed"] >= 3

    def test_prompt_shaped_payload_checks_pass(self):
        """End-to-end: exact prompt output shape must score checks."""
        prompt_payload = {
            "method": "marker_detection",
            "marker_found": True,
            "marker_symbol": "c",
            "marker_type": "unique_separator",
            "marker_position": "center",
            "marker_description": "The letter c uniquely separates the two halves.",
            "ll_usage": "An LL(1) parser can use the presence of 'c' to decide branching.",
            "suggested_k": 1,
        }
        result = verify_marker_claim(prompt_payload, IR_SIMPLE)
        assert result["checks_passed"] >= 2, result["issues"]
