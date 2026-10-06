"""Tests for claim_verifier.py"""
from __future__ import annotations

import pytest
from ll_system.lib import claim_verifier as cv
from ll_system.lib.claim_verifier import (
    verify_ll_claim,
    verify_ll_grammar_claim,
    verify_substitution_claim,
    verify_grammar_transformation_claim,
    verify_marker_claim,
    verify_prefix_classes_claim,
    verify_essential_ambiguity_claim,
    _make_result,
    _task_language_equivalence_trust,
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


def test_pruned_generator_does_not_refute_equivalent_grammars():
    candidate = {
        "nonterminals": ["S", "A"], "terminals": ["a"], "start": "S",
        "rules": [{"lhs": "S", "rhs": ["A"] * 30}, {"lhs": "A", "rhs": []}],
    }
    target = {
        "kind": "grammar", "nonterminals": ["S"], "terminals": ["a"], "start": "S",
        "rules": [{"lhs": "S", "rhs": []}],
    }
    trust, details = _task_language_equivalence_trust(
        candidate, {"language_spec": target}, max_len=1,
    )
    assert trust == "bounded_pass"


def test_word_oracle_checks_missing_candidates_by_exact_membership(monkeypatch):
    candidate = {
        "nonterminals": ["S", "A"], "terminals": ["a"], "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["A"] * 30}, {"lhs": "S", "rhs": ["a"]},
            {"lhs": "A", "rhs": []},
        ],
    }
    monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: lambda word: word in {"", "a"})
    trust, details = _task_language_equivalence_trust(
        candidate, {"language_spec": {"kind": "predicate", "alphabet": ["a"]}}, max_len=1,
    )
    assert trust == "bounded_pass"


@pytest.mark.parametrize("unknown_word", ["a", ""])
def test_unknown_oracle_membership_prevents_equivalence_pass(monkeypatch, unknown_word):
    candidate = {
        "nonterminals": ["S"], "terminals": ["a"], "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a"]}],
    }
    def oracle(word):
        return None if word == unknown_word else word == "a"
    monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: oracle)
    trust, details = _task_language_equivalence_trust(
        candidate, {"language_spec": {"kind": "predicate", "alphabet": ["a"]}}, max_len=1,
    )
    assert trust is None


IR_SIMPLE = {
    "task_type": "ll_check_grammar",
    "source_text": "test",
    "grammar": GRAMMAR_LL1,
    "question": "is_ll_k",
    "k": None,
}


def test_unknown_oracle_does_not_refute_branch_templates(monkeypatch):
    monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: lambda word: None)
    trust, details = cv._verify_branch_words_by_oracle(
        {"word_1": "a^n b^n", "word_2": "a^n c^n"}, {},
    )
    assert trust is None
    assert all(item["membership"] == "unknown" for item in details["checked"])


def test_unknown_prefix_pair_membership_is_not_refuted(monkeypatch):
    monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: lambda word: None)
    trust, _ = cv._semantic_check_prefix_classes(
        {"distinguishing_suffix": "b", "representative_pairs": [{"u": "a", "v": "b"}]},
        {"language_spec": {"kind": "set_builder", "alphabet": ["a", "b"]}},
    )
    assert trust is None


def test_empty_suffix_can_distinguish_prefix_pair(monkeypatch):
    monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: lambda word: word == "a")
    monkeypatch.setattr(cv, "_check_dead_class_finite_ll", lambda ir: (True, []))
    trust, _ = cv._semantic_check_prefix_classes(
        {"distinguishing_suffix": "", "representative_pairs": [{"u": "a", "v": "b"}]},
        {"language_spec": {"kind": "set_builder", "alphabet": ["a", "b"]}},
    )
    assert trust == "bounded_pass"

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

# Valid substitution claim (not_ll) — new contract (docs/THEORY.md §3.3 (C))
AGENT_NOT_LL_CLAIM = {
    "agent_name": "substitution_agent",
    "verdict": "not_ll",
    "confidence": 0.95,
    "proof_sketch": {
        "method": "substitution",
        "for_all_k": True,
        "branch_words": {
            "common_prefix": "a^j, n-k < j <= n",
            "word_1": "a^n b^n",
            "word_2": "a^n c^n",
            "lookahead_equal_because": "both words are still inside the a-block for n > k",
        },
        "common_form_argument": "both derivations pass through a common sentential form a^j · delta",
        "deciding_nonterminal_argument": "a unique X_t* produces b^n in one run and c^n in the other",
        "pigeonhole_argument": "finitely many pairs (X_t*, s), infinitely many n -> two share a pair",
        "proof_explanation": "substituting X_t*'s subderivation for n' into the n-run gives a^n b^n' not in L — contradiction",
    },
    "artifacts": {"counterexample_words": ["a^{k+1} b^{k+1}", "a^{k+1} c^{k+1}"]},
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
# docs/VERDICT_POLICY.md §1 trust taxonomy: a structural-only pass is
# "well_formed", not "verified" — "verified" is reserved for a deterministic
# *complete* check, "bounded_pass" for a deterministic *approximate* one.
VALID_STATUSES = {"verified", "bounded_pass", "well_formed", "not_verified", "refuted", "error"}


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
        # docs/VERDICT_POLICY.md §1: verification impossible -> not_verified.
        result = verify_ll_claim(AGENT_UNCERTAIN, IR_SIMPLE)
        assert result["verification_status"] == "not_verified"

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
        # docs/VERDICT_POLICY.md §1: no proof_sketch -> not_verified.
        assert result["verification_status"] == "not_verified"

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
        # docs/VERDICT_POLICY.md §1: unknown method -> not_verified.
        assert result["verification_status"] == "not_verified"

    def test_ll_grammar_claim_status_verified_or_inconclusive(self):
        result = verify_ll_claim(AGENT_LL_CLAIM, IR_SIMPLE)
        # docs/VERDICT_POLICY.md §1/§2: IR_SIMPLE has no language_spec, so the
        # language-equivalence oracle is unavailable and a structural pass
        # (even with check_ll_k confirming LL(k)) stays well_formed, not
        # verified/bounded_pass.
        assert result["verification_status"] in ("well_formed", "not_verified")

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

    def test_complete_witness_verified(self):
        # docs/VERDICT_POLICY.md §1: structural pass with no oracle available
        # (IR_SIMPLE has no language_spec) is well_formed, not verified.
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]

    def test_complete_witness_result_shape(self):
        proof = AGENT_NOT_LL_CLAIM["proof_sketch"]
        result = verify_substitution_claim(proof, IR_SIMPLE)
        _assert_result_shape(result)

    def test_missing_branch_words_raises_issue(self):
        proof = {
            "method": "substitution",
            "for_all_k": True,
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("branch_words" in i for i in result["issues"])

    def test_missing_proof_explanation_raises_issue(self):
        proof = {
            k: v for k, v in AGENT_NOT_LL_CLAIM["proof_sketch"].items()
            if k != "proof_explanation"
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("proof_explanation" in i for i in result["issues"])

    def test_missing_pigeonhole_argument_raises_issue(self):
        proof = {
            k: v for k, v in AGENT_NOT_LL_CLAIM["proof_sketch"].items()
            if k != "pigeonhole_argument"
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("pigeonhole_argument" in i for i in result["issues"])

    def test_missing_for_all_k_raises_issue(self):
        proof = {
            k: v for k, v in AGENT_NOT_LL_CLAIM["proof_sketch"].items()
            if k != "for_all_k"
        }
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("for_all_k" in i for i in result["issues"])

    def test_wrong_method_raises_issue(self):
        proof = dict(AGENT_NOT_LL_CLAIM["proof_sketch"])
        proof["method"] = "pumping"
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("substitution" in i.lower() for i in result["issues"])

    def test_old_contract_fields_reported_obsolete(self):
        """Regression: pre-revision fields (witness/w1/lookahead/...) must be flagged obsolete."""
        proof = dict(AGENT_NOT_LL_CLAIM["proof_sketch"])
        proof["witness"] = {"w1": "a^n", "lookahead": "b^k", "why_not_in_L": "old contract"}
        result = verify_substitution_claim(proof, IR_SIMPLE)
        assert any("obsolete" in i for i in result["issues"])
        # Structural checks on the new contract still pass despite the obsolete leftover
        # (docs/VERDICT_POLICY.md §1: structural pass, no oracle -> well_formed).
        assert result["verification_status"] == "well_formed", result["issues"]

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
# Regression: substitution contract is the "branch-point" argument
# (docs/THEORY.md §3.3 (C)), not the old lookahead/witness shape.
# ---------------------------------------------------------------------------


class TestSubstitutionPromptFieldNames:
    """Verify verify_substitution_claim accepts the new branch-point contract
    and flags the pre-revision ('parser configuration depends only on
    lookahead') shape as obsolete rather than silently accepting it."""

    def _make_proof(self, **overrides) -> dict:
        proof = {
            "method": "substitution",
            "for_all_k": True,
            "branch_words": {
                "common_prefix": "a^j",
                "word_1": "a^n b^n",
                "word_2": "a^n c^n",
                "lookahead_equal_because": "both inside the a-block for n > k",
            },
            "common_form_argument": "common sentential form a^j · delta",
            "deciding_nonterminal_argument": "unique X_t* branches into b^n vs c^n",
            "pigeonhole_argument": "finitely many (X_t*, s) pairs, infinitely many n",
            "proof_explanation": "substitution yields a^n b^n' not in L — contradiction",
        }
        proof.update(overrides)
        return proof

    def test_new_contract_fully_verified(self):
        # docs/VERDICT_POLICY.md §1: structural pass, no oracle -> well_formed.
        result = verify_substitution_claim(self._make_proof(), IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]

    def test_old_witness_shape_alone_is_not_verified(self):
        """Regression: the pre-revision witness/lookahead/why_not_ll shape must
        NOT be silently accepted as a valid proof — it lacks the new contract's
        branch_words/common_form/deciding_nonterminal/pigeonhole fields."""
        old_shaped_proof = {
            "method": "substitution",
            "for_all_k": True,
            "witness": {
                "k": "k (arbitrary)",
                "w1": "a^{n-k}",
                "lookahead_v": "a^k",
                "suffix_1": "b^n",
                "suffix_2": "c^n",
                "why_not_ll": "parser configuration depends only on lookahead",
            },
        }
        result = verify_substitution_claim(old_shaped_proof, IR_SIMPLE)
        assert result["verification_status"] != "verified"
        assert any("obsolete" in i for i in result["issues"])
        assert any("branch_words" in i for i in result["issues"])

    def test_prompt_shaped_payload_fully_verified(self):
        """End-to-end: a payload shaped exactly like the new prompt output must pass
        structurally (docs/VERDICT_POLICY.md §1: well_formed, no oracle available)."""
        prompt_payload = self._make_proof(
            branch_words={
                "common_prefix": "a^j, n-k < j <= n",
                "word_1": "a^n b^n",
                "word_2": "a^n c^n",
                "lookahead_equal_because": "n > k keeps both lookaheads inside the a-block: a^k",
            },
            proof_explanation="Полное доказательство на русском.",
        )
        result = verify_substitution_claim(prompt_payload, IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]


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


# ---------------------------------------------------------------------------
# Regression: Finding 3 — prefix_classes and essential_ambiguity verifiers
# ---------------------------------------------------------------------------


class TestVerifyPrefixClassesClaim:
    """Regression: prefix_classes_agent was falling through to 'No verifier' inconclusive.

    Contract is Theorem 4.7.4 [Sh] (docs/THEORY.md §1.2, §3.3 (A)): all Myhill–Nerode
    classes finite ⇒ not DCFL ⇒ not LL. The old "LL Nerode theorem" (finitely many
    k-equivalence classes of prefixes) is false and must not be accepted.
    """

    VALID_PS = {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 → not DCFL → not LL",
        "for_all_k": True,
        "dead_class_finite": "D = ∅: every prefix extends into a palindrome ww^R ∈ L",
        "distinguishing_suffix": "w = b · a^N · b · u^R, N = 2|uv|",
        "separation_argument": "exactly one of uw, vw is a palindrome for u != v",
        "conclusion": "All Nerode classes are singletons (finite) ⇒ not DCFL ⇒ not LL(k) for any k",
        "proof_explanation": "Full formal proof.",
    }

    def test_valid_claim_verified(self):
        result = verify_prefix_classes_claim(self.VALID_PS, IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]  # docs/VERDICT_POLICY.md §1: no semantic check for prefix_classes/essential_ambiguity yet

    def test_checks_passed_all_eight(self):
        result = verify_prefix_classes_claim(self.VALID_PS, IR_SIMPLE)
        assert result["checks_passed"] == 8

    def test_missing_for_all_k(self):
        ps = {**self.VALID_PS}
        del ps["for_all_k"]
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert any("for_all_k" in i for i in result["issues"])

    def test_missing_dead_class_finite(self):
        """dead_class_finite is mandatory — Theorem 4.7.4 is vacuous without it."""
        ps = {**self.VALID_PS}
        del ps["dead_class_finite"]
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert any("dead_class_finite" in i for i in result["issues"])

    def test_missing_distinguishing_suffix(self):
        ps = {**self.VALID_PS}
        del ps["distinguishing_suffix"]
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert any("distinguishing_suffix" in i for i in result["issues"])

    def test_empty_separation_argument(self):
        ps = {**self.VALID_PS, "separation_argument": ""}
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert any("separation_argument" in i for i in result["issues"])

    def test_dispatched_via_verify_ll_claim(self):
        """Regression: verify_ll_claim was returning 'No verifier for method prefix_classes'."""
        agent_result = {
            "agent_name": "prefix_classes_agent",
            "verdict": "not_ll",
            "confidence": 0.88,
            "proof_sketch": self.VALID_PS,
            "artifacts": {},
        }
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert "No verifier" not in " ".join(result.get("issues", []))
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1

    def test_old_ll_nerode_theorem_shape_not_verified(self):
        """Regression: the false 'LL Nerode theorem' (k-distinguishable prefixes) shape
        must not be silently accepted; old fields are reported obsolete."""
        old_shaped_ps = {
            "method": "prefix_classes",
            "for_all_k": True,
            "prefix_family": {
                "parametrization": "u_n = a^{n+k}",
                "description": "Family of prefixes u_n = a^{n+k}",
            },
            "distinguishability_argument": {
                "why_distinguishable": "u_n and u_m require different completions for n != m",
            },
        }
        result = verify_prefix_classes_claim(old_shaped_ps, IR_SIMPLE)
        assert result["verification_status"] != "verified"
        assert any("obsolete" in i for i in result["issues"])
        assert any("dead_class_finite" in i for i in result["issues"])

    def test_prompt_shaped_payload(self):
        """End-to-end: payload exactly as prompt outputs must be verified."""
        ps = {
            "method": "prefix_classes",
            "theorem": "Shallit 4.7.4 → not DCFL → not LL",
            "dead_class_finite": "D = ∅: любой префикс x ∈ {a,b}* продолжается до палиндрома x·xᴿ ∈ L.",
            "distinguishing_suffix": "Для различных u, v положим N = 2|uv|, w = b·aᴺ·b·uᴿ.",
            "separation_argument": "u·w — палиндром; v·w — палиндром лишь при u = v, что противоречит u != v.",
            "for_all_k": True,
            "conclusion": "Все классы Нероуда одноэлементны ⇒ по т. 4.7.4 L ∉ DCFL ⇒ L не LL(k) ни для какого k.",
            "proof_explanation": "Полное доказательство на русском.",
        }
        result = verify_prefix_classes_claim(ps, IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]  # docs/VERDICT_POLICY.md §1


class TestVerifyEssentialAmbiguityClaim:
    """Regression: ambiguity_detector was falling through to 'No verifier' inconclusive."""

    VALID_PS = {
        "method": "essential_ambiguity",
        "essentially_ambiguous": True,
        "witness_word": "a^n b^n c^n",
        "two_parse_structures": [
            {
                "structure_id": 1,
                "description": "i=j branch",
                "derivation_sketch": "S => A c^n => a^n b^n c^n",
            },
            {
                "structure_id": 2,
                "description": "j=k branch",
                "derivation_sketch": "S => a^n B => a^n b^n c^n",
            },
        ],
        "why_every_grammar_ambiguous": "Every grammar generates two parse trees for the witness word.",
        "proof_explanation": "Full proof.",
        "ogden_used": False,
    }

    def test_valid_claim_verified(self):
        result = verify_essential_ambiguity_claim(self.VALID_PS, IR_SIMPLE)
        assert result["verification_status"] == "well_formed", result["issues"]  # docs/VERDICT_POLICY.md §1

    def test_checks_passed_all_five(self):
        result = verify_essential_ambiguity_claim(self.VALID_PS, IR_SIMPLE)
        assert result["checks_passed"] == 5

    def test_essentially_ambiguous_false_fails(self):
        ps = {**self.VALID_PS, "essentially_ambiguous": False}
        result = verify_essential_ambiguity_claim(ps, IR_SIMPLE)
        assert any("essentially_ambiguous" in i for i in result["issues"])

    def test_empty_witness_word_fails(self):
        ps = {**self.VALID_PS, "witness_word": ""}
        result = verify_essential_ambiguity_claim(ps, IR_SIMPLE)
        assert any("witness_word" in i for i in result["issues"])

    def test_only_one_parse_structure_fails(self):
        ps = {**self.VALID_PS, "two_parse_structures": [self.VALID_PS["two_parse_structures"][0]]}
        result = verify_essential_ambiguity_claim(ps, IR_SIMPLE)
        assert any("two_parse_structures" in i or ">= 2" in i for i in result["issues"])

    def test_empty_why_fails(self):
        ps = {**self.VALID_PS, "why_every_grammar_ambiguous": ""}
        result = verify_essential_ambiguity_claim(ps, IR_SIMPLE)
        assert any("why_every_grammar_ambiguous" in i for i in result["issues"])

    def test_dispatched_via_verify_ll_claim(self):
        """Regression: verify_ll_claim was returning 'No verifier for method essential_ambiguity'."""
        agent_result = {
            "agent_name": "ambiguity_detector",
            "verdict": "not_ll",
            "confidence": 0.85,
            "proof_sketch": self.VALID_PS,
            "artifacts": {},
        }
        result = verify_ll_claim(agent_result, IR_SIMPLE)
        assert "No verifier" not in " ".join(result.get("issues", []))
        assert result["verification_status"] == "well_formed"  # docs/VERDICT_POLICY.md §1


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R2/§4 fix (reviewer finding): Format 1 language
# equivalence must check BOTH directions, not just L(G) subseteq L.
# ---------------------------------------------------------------------------

_ANBN_IR = {
    "task_type": "ll_check_language",
    "source_text": "{a^n b^n | n >= 0}",
    "language_spec": {
        "kind": "predicate",
        "alphabet": ["a", "b"],
        "variable": "w",
        "predicate": {
            "op": "and",
            "args": [
                {"op": "matches_regex", "args": {"var": "w", "pattern": "^a*b*$"}},
                {"op": "count_eq", "args": {"var": "w", "chars": ["a"], "chars2": ["b"]}},
            ],
        },
    },
    "alphabet": ["a", "b"],
}


def _anbn_oracle(word: str) -> bool:
    i = 0
    n = len(word)
    while i < n and word[i] == "a":
        i += 1
    if any(ch != "b" for ch in word[i:]):
        return False
    return i == len(word) - i


class TestTaskLanguageEquivalenceChecksBothDirections:
    """S -> ab only generates {'ab'} subset {a^n b^n} -- checking only
    L(G) subseteq L (the pre-fix behaviour) would falsely pass this as
    bounded_pass; a real equivalence check must also verify L subseteq L(G)
    and refute it."""

    STRICT_SUBSET_GRAMMAR = {
        "nonterminals": ["S"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a", "b"]}],
    }

    EQUIVALENT_GRAMMAR = {
        "nonterminals": ["S"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    }

    def test_strict_subset_grammar_is_refuted_not_bounded_pass(self, monkeypatch):
        monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: _anbn_oracle)
        trust, details = _task_language_equivalence_trust(self.STRICT_SUBSET_GRAMMAR, _ANBN_IR)
        assert trust == "refuted", (
            "S -> ab only covers n=1 of {a^n b^n} -- inclusion-only checking "
            "used to wrongly certify this as bounded_pass"
        )
        assert details.get("missing_from_grammar")

    def test_actually_equivalent_grammar_is_bounded_pass(self, monkeypatch):
        monkeypatch.setattr(cv, "_try_word_oracle", lambda ir: _anbn_oracle)
        trust, _details = _task_language_equivalence_trust(self.EQUIVALENT_GRAMMAR, _ANBN_IR)
        assert trust == "bounded_pass"
