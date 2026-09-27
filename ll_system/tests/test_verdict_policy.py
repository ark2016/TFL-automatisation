"""Tests for docs/VERDICT_POLICY.md §6 scenarios, applied to ll_system.

Covers TODO §1 ("честность вердиктов") end to end: claim_verifier's trust
taxonomy (docs/VERDICT_POLICY.md §1) feeding orchestrator.assemble_result_node's
R1-R5 gate (§3), plus the two named TODO §1 sub-items:
  (a) the regularity shortcut reads regularity_confidence/regularity_reason
      (not confidence/reason) and never raises a less-confident heuristic to 0.95;
  (b) Format 2's given grammar is itself tested by the first/follow oracle.
"""
from __future__ import annotations

import pytest

from ll_system.orchestrator import assemble_result_node, preprocess_node, first_follow_oracle_node
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
