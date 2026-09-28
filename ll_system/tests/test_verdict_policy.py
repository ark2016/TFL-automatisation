"""Tests for docs/VERDICT_POLICY.md §6 scenarios, applied to ll_system.

Covers TODO §1 ("честность вердиктов") end to end: claim_verifier's trust
taxonomy (docs/VERDICT_POLICY.md §1) feeding orchestrator.assemble_result_node's
R1-R5 gate (§3), plus the two named TODO §1 sub-items:
  (a) the regularity shortcut reads regularity_confidence/regularity_reason
      (not confidence/reason) and never raises a less-confident heuristic to 0.95;
  (b) Format 2's given grammar is itself tested by the first/follow oracle.

Reviewer round 4 (VERDICT_POLICY R4/R7/§4) adds:
  1. The gate now runs right after reasoning (verdict_gate_node / apply_verdict_gate),
     BEFORE the retry/done decision -- a refuted constructive artifact must
     actually retry (budget permitting), not just report inconclusive.
  2. A gate-triggered (or reasoning-proposed) retry_plan carries per-agent
     trust + counterexamples in `hints` (R7).
  3. claim_verifier's substitution step 2 (FIRST_k equality of the
     remainders, not "common prefix >= n - k"; refutation ONLY via the oracle).
"""
from __future__ import annotations

import pytest

from ll_system.orchestrator import (
    MAX_RETRIES,
    apply_verdict_gate,
    assemble_result_node,
    preprocess_node,
    first_follow_oracle_node,
    verdict_gate_node,
)
from ll_system.lib.claim_verifier import verify_substitution_claim
from ll_system.lib.preprocess import compute_preprocess_hints


IR_FORMAT1 = {
    "task_type": "ll_check_language",
    "source_text": "test",
    "language_spec": {"kind": "natural", "description": "test"},
}


def _state(**overrides) -> dict:
    base = {
        "ir": IR_FORMAT1,
        "input_format": 1,
        "agent_results": {},
        "reasoning_output": {},
        "first_follow_result": {},
        "claim_verification": {},
        "preprocess_hints": {},
        "classifier_output": {},
        "errors": [],
        "log": [],
        "retry_round": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# §6.1 — constructive failure is not destructive evidence (R1)
# ---------------------------------------------------------------------------

class TestScenario1ConstructiveFailureIsNotDestructiveEvidence:
    """A refuted constructive artifact + no destructive proof, yet reasoning
    proposes done/not_ll (the task_grammar_filter_49 precedent, R1) ->
    inconclusive, confidence <= 0.40, downgrades non-empty."""

    def test_refuted_constructive_only_downgrades_to_uncertain(self):
        state = _state(
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.8,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "refuted", "verification_status": "refuted"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "not_ll",
                "confidence": 0.92,
                "primary_agent": None,
                "summary": "inverted hypothesis after grammar construction failed",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] == "uncertain"
        assert result["confidence"] <= 0.40
        gate = result["verdict_gate"]
        assert gate["downgrades"], "a downgrade must be logged (docs/VERDICT_POLICY.md R1)"
        assert gate["confidence_cap"] <= 0.40


# ---------------------------------------------------------------------------
# §6.2 — contradiction (R3)
# ---------------------------------------------------------------------------

class TestScenario2Contradiction:
    """Constructive bounded_pass + destructive well_formed at the same time ->
    contradiction: true, confidence <= 0.50, verdict goes to the stronger side."""

    def test_contradiction_detected_and_capped(self):
        state = _state(
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
                "substitution_agent": {
                    "verdict": "not_ll",
                    "confidence": 0.85,
                    "proof_sketch": {"method": "substitution", "for_all_k": True},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "bounded_pass", "verification_status": "bounded_pass"},
                "substitution_agent": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.9,
                "primary_agent": "ll_grammar_builder",
                "summary": "test",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        gate = result["verdict_gate"]
        assert gate["contradiction"] is True
        assert result["confidence"] <= 0.50
        # bounded_pass (rank 2) outranks well_formed (rank 1) -> constructive side wins
        assert result["verdict"] == "ll"

    def test_contradiction_tie_is_uncertain(self):
        state = _state(
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
                "substitution_agent": {
                    "verdict": "not_ll",
                    "confidence": 0.85,
                    "proof_sketch": {"method": "substitution", "for_all_k": True},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "well_formed", "verification_status": "well_formed"},
                "substitution_agent": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.9,
                "primary_agent": "ll_grammar_builder",
                "summary": "test",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        # neither side reaches has_constructive's bounded_pass floor, so this
        # is NOT a contradiction — it falls through to the plain R2 well_formed
        # ceiling for the constructive verdict instead.
        assert result["verdict_gate"]["contradiction"] is False
        assert result["confidence"] <= 0.60


# ---------------------------------------------------------------------------
# §6.3 — a full LL(k)-table test (Format 3) is "verified", confidence <= 0.98
# ---------------------------------------------------------------------------

class TestScenario3VerifiedFullTest:
    def _format3_state(self, ff: dict) -> dict:
        return {
            "ir": {"task_type": "ll_check_grammar", "source_text": "t", "grammar": {}, "k": None},
            "input_format": 3,
            "reasoning_output": {},
            "first_follow_result": ff,
            "errors": [],
            "log": [],
            "retry_round": 0,
        }

    def test_format3_ll_confidence_is_098(self):
        ff = {"is_ll_k": True, "found": True, "min_k": 1, "k": 1, "conflicts": []}
        out = assemble_result_node(self._format3_state(ff))
        result = out["result"]
        assert result["verdict"] == "ll"
        assert result["confidence"] == 0.98
        assert result["verdict_gate"]["basis"][0]["trust"] == "verified"

    def test_format3_confidence_never_reaches_10(self):
        ff = {"is_ll_k": True, "found": True, "min_k": 1, "k": 1, "conflicts": []}
        out = assemble_result_node(self._format3_state(ff))
        assert out["result"]["confidence"] < 1.0

    def test_format3_certificate_is_also_098_not_10(self):
        ff = {
            "is_ll_k": None, "found": False, "min_k": None, "strong_k": None,
            "max_k_checked": 0, "max_k_decided": 0, "undetermined": False,
            "certificate": {"type": "left_recursion", "detail": "..."},
            "ll_conflicts": [], "conflicts": [],
        }
        out = assemble_result_node(self._format3_state(ff))
        result = out["result"]
        assert result["verdict"] == "not_ll"
        assert result["confidence"] == 0.98


# ---------------------------------------------------------------------------
# §6.4 — only a well_formed proof is admissible but capped at 0.60
# ---------------------------------------------------------------------------

class TestScenario4WellFormedIsAdmissibleButCapped:
    def test_well_formed_destructive_capped_at_060(self):
        state = _state(
            agent_results={
                "substitution_agent": {
                    "verdict": "not_ll",
                    "confidence": 0.95,
                    "proof_sketch": {"method": "substitution", "for_all_k": True},
                },
            },
            claim_verification={
                "substitution_agent": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "not_ll",
                "confidence": 0.95,
                "primary_agent": "substitution_agent",
                "summary": "test",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] == "not_ll"
        assert result["confidence"] <= 0.60
        assert result["verdict_gate"]["confidence_cap"] == 0.60

    def test_oracle_checked_destructive_raises_cap_to_085(self):
        state = _state(
            agent_results={
                "substitution_agent": {
                    "verdict": "not_ll",
                    "confidence": 0.95,
                    "proof_sketch": {"method": "substitution", "for_all_k": True},
                },
            },
            claim_verification={
                "substitution_agent": {"trust": "bounded_pass", "verification_status": "bounded_pass"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "not_ll",
                "confidence": 0.95,
                "primary_agent": "substitution_agent",
                "summary": "test",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] == "not_ll"
        assert result["confidence"] <= 0.85
        assert result["verdict_gate"]["confidence_cap"] == 0.85

    def test_well_formed_constructive_downgrades_to_uncertain(self):
        """docs/VERDICT_POLICY.md R2 fix (reviewer finding, verdict-gate
        branch): a constructive verdict needs trust >= bounded_pass, same
        threshold as every other system's constructive direction. A bare
        well_formed constructive claim (no equivalence oracle ran at all)
        must NOT stand as an accepted 'll' verdict at any confidence — it
        downgrades to 'uncertain' <= 0.40, not 'll' <= 0.60 (that used to be
        the bug this test locked in; see VERDICT_POLICY.md §2)."""
        state = _state(
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.9,
                "primary_agent": "ll_grammar_builder",
                "summary": "test",
            },
        )
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] == "uncertain"
        assert result["confidence"] <= 0.40
        assert result["verdict_gate"]["downgrades"]


# ---------------------------------------------------------------------------
# TODO §1 (a) — regularity shortcut reads the right keys and caps the heuristic
# ---------------------------------------------------------------------------

class TestRegularityShortcutHonorsHeuristicConfidence:
    def test_bounded_set_builder_heuristic_confidence_not_095(self):
        ir = {
            "task_type": "ll_check_language",
            "source_text": "t",
            "language_spec": {
                "kind": "set_builder",
                "alphabet": ["a"],
                "variables": [{"name": "n", "domain": {"type": "bounded", "max": 3}}],
                "template": ["a^n"],
            },
        }
        hints = compute_preprocess_hints(ir)
        assert hints["regularity_confidence"] < 0.95  # "trivial_constraint" heuristic (0.75)
        out = preprocess_node({"ir": ir, "log": []})
        result = out["result"]
        # "trivial_constraint" is a heuristic (well_formed trust, §1), so it is
        # additionally capped at well_formed's 0.60 ceiling (§2) — lower than
        # both the heuristic's own 0.75 and the 0.95 hard ceiling.
        assert result["confidence"] == 0.60
        assert result["confidence"] < hints["regularity_confidence"]

    def test_regex_kind_reaches_095(self):
        ir = {
            "task_type": "ll_check_language",
            "source_text": "t",
            "language_spec": {"kind": "regex", "pattern": "a*", "has_backreferences": False},
        }
        out = preprocess_node({"ir": ir, "log": []})
        result = out["result"]
        # min(0.95 hard ceiling, 0.98 verified cap, 1.0 heuristic confidence) == 0.95
        assert result["confidence"] == 0.95

    def test_regularity_shortcut_reads_correct_keys(self):
        """Regression: the shortcut used to read hints['reason']/['confidence'],
        which compute_preprocess_hints never sets (always None/default)."""
        ir = {
            "task_type": "ll_check_language",
            "source_text": "t",
            "language_spec": {"kind": "regex", "pattern": "a*", "has_backreferences": False},
        }
        out = preprocess_node({"ir": ir, "log": []})
        proof_details = out["result"]["proof"]["details"]
        assert proof_details["reason"] == "Language is specified as a regular expression"


# ---------------------------------------------------------------------------
# TODO §1 (b) — Format 2's given grammar reaches the oracle
# ---------------------------------------------------------------------------

class TestFormat2GivenGrammarReachesOracle:
    _GIVEN_GRAMMAR = {
        "nonterminals": ["S"], "terminals": ["a"], "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a"]}],
    }

    def test_given_grammar_tested_when_no_agent_grammar(self):
        state = {
            "ir": {
                "task_type": "ll_check_grammar_lang",
                "source_text": "t",
                "language_spec": {"kind": "grammar", **self._GIVEN_GRAMMAR},
            },
            "input_format": 2,
            "agent_results": {},
            "log": [],
        }
        out = first_follow_oracle_node(state)
        ff = out["first_follow_result"]
        assert ff.get("found") is True
        assert ff.get("grammar_source") == "given_grammar"

    def test_given_grammar_tried_before_agent_grammar(self):
        state = {
            "ir": {
                "task_type": "ll_check_grammar_lang",
                "source_text": "t",
                "language_spec": {"kind": "grammar", **self._GIVEN_GRAMMAR},
            },
            "input_format": 2,
            "agent_results": {
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "proof_sketch": {
                        "method": "ll_grammar_construction", "k": 1,
                        "grammar": self._GIVEN_GRAMMAR,
                    },
                },
            },
            "log": [],
        }
        out = first_follow_oracle_node(state)
        ff = out["first_follow_result"]
        assert ff.get("grammar_source") == "given_grammar"

    def test_format1_has_no_given_grammar_candidate(self):
        """Format 1 has no top-level grammar to prioritize — only agent grammars."""
        state = {
            "ir": {
                "task_type": "ll_check_language",
                "source_text": "t",
                "language_spec": {"kind": "set_builder", "alphabet": ["a"], "variables": [], "template": ["a"]},
            },
            "input_format": 1,
            "agent_results": {
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "proof_sketch": {
                        "method": "ll_grammar_construction", "k": 1,
                        "grammar": self._GIVEN_GRAMMAR,
                    },
                },
            },
            "log": [],
        }
        out = first_follow_oracle_node(state)
        ff = out["first_follow_result"]
        assert ff.get("grammar_source") == "ll_grammar_builder"


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R2 fix (reviewer finding): first_follow_oracle
# finding a candidate grammar LL(k) must not force bounded_pass regardless
# of whether that candidate grammar is actually equivalent to the task
# language -- a refuted grammar_source must forbid the constructive verdict,
# and a well_formed one must not be silently upgraded past its own trust.
# ---------------------------------------------------------------------------

class TestFirstFollowOracleDoesNotOverrideRefutedGrammar:
    def _state_with_ff_found(self, grammar_source_trust: str) -> dict:
        return _state(
            input_format=1,
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {
                    "trust": grammar_source_trust,
                    "verification_status": grammar_source_trust,
                },
            },
            first_follow_result={
                "found": True, "min_k": 1, "k": 1,
                "grammar_source": "ll_grammar_builder",
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.85,
                "primary_agent": "first_follow_oracle",
                "summary": "test",
            },
        )

    def test_refuted_grammar_source_forbids_ll_verdict(self):
        state = self._state_with_ff_found("refuted")
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] != "ll", (
            "grammar_source's own claim was refuted (not equivalent to the task "
            "language / foreign terminals) -- the LL(k)-table pass on that wrong "
            "grammar must not resurrect a 'll' verdict at bounded_pass"
        )
        assert result["confidence"] <= 0.40

    def test_well_formed_grammar_source_does_not_reach_bounded_pass(self):
        state = self._state_with_ff_found("well_formed")
        out = assemble_result_node(state)
        result = out["result"]
        # well_formed (no equivalence oracle available) must not be silently
        # promoted to bounded_pass just because the LL(k) table happened to
        # confirm the CANDIDATE grammar's own LL(k)-ness.
        assert result["verdict"] != "ll" or result["confidence"] <= 0.60

    def test_bounded_pass_grammar_source_supports_ll_at_085(self):
        state = self._state_with_ff_found("bounded_pass")
        out = assemble_result_node(state)
        result = out["result"]
        assert result["verdict"] == "ll"
        assert result["confidence"] <= 0.85


# ---------------------------------------------------------------------------
# Reviewer round 4, item 1 (R4) — the gate runs right after reasoning, not
# only inside assemble_result_node: a refuted constructive artifact must
# trigger an actual retry when budget remains, and only fall back to
# inconclusive once the budget is exhausted.
# ---------------------------------------------------------------------------

class TestGateRunsRightAfterReasoning:
    def _state_refuted_ll(self, retry_round: int) -> dict:
        return _state(
            retry_round=retry_round,
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "refuted", "verification_status": "refuted"},
            },
            first_follow_result={
                "found": True, "min_k": 1, "k": 1,
                "grammar_source": "ll_grammar_builder",
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.9,
                "primary_agent": "first_follow_oracle",
                "summary": "test",
            },
        )

    def test_retries_when_budget_available(self):
        """ll_grammar_builder refuted + ff found -> the gate must ask for a
        retry (not immediately settle for inconclusive) while budget remains."""
        state = self._state_refuted_ll(retry_round=0)
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "retry"
        assert gate["verdict_gate"]["downgrades"]
        plan = gate["reasoning_output"]["retry_plan"]
        assert "ll_grammar_builder" in plan["agents_to_retry"]

    def test_inconclusive_once_budget_exhausted(self):
        state = self._state_refuted_ll(retry_round=MAX_RETRIES)
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "uncertain"
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert gate["verdict_gate"]["downgrades"]

    def test_full_node_downgrades_and_logs(self):
        state = self._state_refuted_ll(retry_round=MAX_RETRIES)
        result = verdict_gate_node(state)
        assert result["reasoning_output"]["verdict"] == "uncertain"
        assert result["reasoning_output"]["confidence"] <= 0.40
        assert result["verdict_gate"]["downgrades"]


# ---------------------------------------------------------------------------
# Regression: reasoning itself proposes action="retry" (not a gate-triggered
# downgrade), but the retry budget is already exhausted (retry_round >=
# MAX_RETRIES). decide_retry forces "done" in that case regardless of the
# raw `action`, so the gate must fall through to the normal done/verdict
# gating (R1-R3 + confidence caps) instead of returning the ungated
# verdict/confidence unchanged (which would then leak straight through
# assemble_result_node once decide_retry routes to formalize_node).
# ---------------------------------------------------------------------------

class TestReasoningProposedRetryAtExhaustedBudget:
    def _state_reasoning_retry(self, verdict: str, agent_verdict: str) -> dict:
        """A refuted constructive artifact (ll_grammar_builder) plus a
        reasoning output that itself asks for a retry with a confident
        verdict already attached -- as an LLM output plausibly would."""
        return _state(
            retry_round=MAX_RETRIES,
            agent_results={
                "ll_grammar_builder": {
                    "verdict": agent_verdict,
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {"trust": "refuted", "verification_status": "refuted"},
            },
            reasoning_output={
                "action": "retry",
                "verdict": verdict,
                "confidence": 0.95,
                "primary_agent": "ll_grammar_builder",
                "summary": "test",
            },
        )

    def test_retry_ll_at_exhausted_budget_downgrades_to_uncertain(self):
        state = self._state_reasoning_retry(verdict="ll", agent_verdict="ll")
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "uncertain"
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert gate["verdict_gate"]["downgrades"]

    def test_retry_not_ll_at_exhausted_budget_downgrades_to_uncertain(self):
        state = self._state_reasoning_retry(verdict="not_ll", agent_verdict="not_ll")
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "uncertain"
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert gate["verdict_gate"]["downgrades"]

    def test_retry_with_budget_left_still_returns_retry_ungated(self):
        """Sanity check: with budget remaining, reasoning's own retry
        proposal is still honored as-is (not forced through done gating)."""
        state = self._state_reasoning_retry(verdict="ll", agent_verdict="ll")
        state["retry_round"] = 0
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "retry"


# ---------------------------------------------------------------------------
# Reviewer round 4, item 2 (R7) — retry_plan carries per-agent trust and
# counterexamples in `hints`.
# ---------------------------------------------------------------------------

class TestRetryPlanCarriesTrustAndCounterexamples:
    def test_gate_triggered_retry_hints_include_mismatched_words(self):
        state = _state(
            retry_round=0,
            agent_results={
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "grammar": {}},
                },
            },
            claim_verification={
                "ll_grammar_builder": {
                    "trust": "refuted",
                    "verification_status": "refuted",
                    "issues": ["grammar does not generate the task language"],
                    "details": {
                        "language_equivalence": {"mismatches": ["ab", "aabb"]},
                    },
                },
            },
            reasoning_output={
                "action": "done",
                "verdict": "ll",
                "confidence": 0.9,
                "primary_agent": "ll_grammar_builder",
                "summary": "test",
            },
        )
        gate = apply_verdict_gate(state)
        plan = gate["reasoning_output"]["retry_plan"]
        hint = plan["hints"]["ll_grammar_builder"]
        assert hint["trust"] == "refuted"
        assert "ab" in hint["counterexamples"]
        assert "aabb" in hint["counterexamples"]

    def test_reasoning_proposed_retry_also_gets_hints_attached(self):
        """Even when reasoning itself already proposed a retry (not a
        gate-triggered downgrade), the gate fills in missing hints (R7) so
        the next round's specialist prompt sees the counterexamples too."""
        state = _state(
            retry_round=0,
            claim_verification={
                "substitution_agent": {
                    "trust": "refuted",
                    "verification_status": "refuted",
                    "details": {
                        "branch_words_instantiation": {
                            "checked": [
                                {
                                    "k": 1, "word_1": "aab", "word_1_in_l": False,
                                    "word_2": "aac", "word_2_in_l": True,
                                },
                            ],
                        },
                    },
                },
            },
            reasoning_output={
                "action": "retry",
                "verdict": None,
                "confidence": 0.2,
                "retry_plan": {"agents_to_retry": ["substitution_agent"]},
                "summary": "test",
            },
        )
        gate = apply_verdict_gate(state)
        hint = gate["reasoning_output"]["retry_plan"]["hints"]["substitution_agent"]
        assert hint["trust"] == "refuted"
        assert "aab" in hint["counterexamples"]


# ---------------------------------------------------------------------------
# Reviewer round 4, item 3 — claim_verifier substitution step 2: bounded_pass
# requires oracle membership AND FIRST_k equality of the remainders after the
# common prefix (not "common prefix >= n - k"); refutation is ONLY an oracle
# counterexample; a short common run is well_formed, not refuted.
# ---------------------------------------------------------------------------

_ANBN_ANCN_IR = {
    "task_type": "ll_check_language",
    "source_text": "test",
    "language_spec": {
        "kind": "grammar",
        "nonterminals": ["S", "A", "B"],
        "terminals": ["a", "b", "c"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["A"]},
            {"lhs": "S", "rhs": ["B"]},
            {"lhs": "A", "rhs": ["a", "A", "b"]},
            {"lhs": "A", "rhs": ["a", "b"]},
            {"lhs": "B", "rhs": ["a", "B", "c"]},
            {"lhs": "B", "rhs": ["a", "c"]},
        ],
    },
}

# Trivial L = (a|b|c)* -- used for the "short common prefix" scenario, where
# both instantiated words are (trivially) in L but diverge immediately, well
# before the claimed branch point at n - k.
_ALL_STRINGS_IR = {
    "task_type": "ll_check_language",
    "source_text": "test",
    "language_spec": {
        "kind": "grammar",
        "nonterminals": ["S"],
        "terminals": ["a", "b", "c"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S"]},
            {"lhs": "S", "rhs": ["b", "S"]},
            {"lhs": "S", "rhs": ["c", "S"]},
            {"lhs": "S", "rhs": []},
        ],
    },
}


def _substitution_proof_sketch(word_1: str, word_2: str) -> dict:
    return {
        "method": "substitution",
        "for_all_k": True,
        "branch_words": {
            "common_prefix": "a^j",
            "word_1": word_1,
            "word_2": word_2,
            "lookahead_equal_because": "shared a-run",
        },
        "common_form_argument": "x",
        "deciding_nonterminal_argument": "x",
        "pigeonhole_argument": "x",
        "proof_explanation": "x",
    }


class TestSubstitutionStep2FirstKEquality:
    def test_correct_branch_words_give_bounded_pass(self):
        """word_1/word_2 instantiated at n = k+2 for k in {1, 2}: both in L,
        and FIRST_k of the remainders after the branch point are equal."""
        out = verify_substitution_claim(
            _substitution_proof_sketch("a^n b^n", "a^n c^n"), _ANBN_ANCN_IR,
        )
        assert out["trust"] == "bounded_pass"

    def test_word_not_in_language_is_refuted(self):
        """A branch_words word that the oracle rejects refutes the claim --
        the only refutation trigger this step recognizes."""
        out = verify_substitution_claim(
            _substitution_proof_sketch("a^n b^n", "a^n c^(n+1)"), _ANBN_ANCN_IR,
        )
        assert out["trust"] == "refuted"

    def test_short_common_prefix_is_well_formed_not_refuted(self):
        """Both words are (trivially) in L, but they diverge immediately --
        far short of the claimed branch point at n - k. Insufficient data to
        confirm the FIRST_k-equality claim is well_formed, never refuted."""
        out = verify_substitution_claim(
            _substitution_proof_sketch("a^n b^n", "b^n a^n"), _ALL_STRINGS_IR,
        )
        assert out["trust"] == "well_formed"

    def test_constant_tail_confirmed_only_at_one_k_is_not_bounded_pass(self):
        """'a^3 b^1'/'a^3 c^1': the tail is a constant unrelated to n, and
        the literal-prefix check only happens to confirm at k=1 (at k=2 the
        window straddles the divergence and 'ab' != 'ac'). Requiring *all*
        instantiated k (not just any) means this must never reach
        bounded_pass -- it must not stay 'll' at full confidence through the
        gate either way it resolves (well_formed or refuted, never
        bounded_pass)."""
        out = verify_substitution_claim(
            _substitution_proof_sketch("a^3 b^1", "a^3 c^1"), _ANBN_ANCN_IR,
        )
        assert out["trust"] != "bounded_pass"

    def test_tail_not_depending_on_n_is_never_bounded_pass_even_on_sigma_star(self):
        """'a^n b^1'/'a^n c^1' on Sigma* (regular, LL(1)): both instantiated
        words are trivially in L (everything is), and the literal window
        happens to fall entirely inside the shared a-run for every k, so the
        old any-k / literal-prefix-only check wrongly reached bounded_pass
        (docs/VERDICT_POLICY.md §4). The tail ('b^1'/'c^1') does not depend
        on n at all, so this can never be a valid branch-point witness --
        must stay well_formed."""
        out = verify_substitution_claim(
            _substitution_proof_sketch("a^n b^1", "a^n c^1"), _ALL_STRINGS_IR,
        )
        assert out["trust"] == "well_formed"
