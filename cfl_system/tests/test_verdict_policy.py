"""Tests for the verdict-gate policy (docs/VERDICT_POLICY.md), §6 scenarios.

Scenario numbering below matches VERDICT_POLICY.md §6, adapted to the CFL
system's own agents/trust sources:

1. constructive agent refuted (oracle_test grammar_incorrect), no destructive
   evidence, reasoning proposes done/<destructive> -> inconclusive,
   confidence <= 0.40, downgrades non-empty (the grammar_filter_49 pattern
   before the fix, see docs/THEORY.md §2.3).
2. constructive bounded_pass + destructive well_formed -> contradiction: true,
   confidence <= 0.50.
3. full oracle pass (bounded_pass — the CFL system's claim_verifier never
   reaches the "verified" trust level, so its confidence ceiling is 0.85, not
   0.98) -> confidence allowed up to that ceiling, not silently higher.
4. only well_formed destructive proof (no constructive artifact at all) ->
   verdict allowed, confidence <= 0.60.

Each scenario drives `apply_verdict_gate` (and, for #1, the full
`verdict_gate_node`) directly against a hand-built state dict — this is the
"or monkeypatch" option the spec allows in place of on-disk mock fixtures,
since the gate is a pure function of `state`.
"""

from __future__ import annotations

from cfl_system.orchestrator import (
    MAX_RETRIES,
    apply_verdict_gate,
    verdict_gate_node,
)


def _base_state(**overrides) -> dict:
    state: dict = {
        "retry_round": 0,
        "agent_results": {},
        "oracle_test_result": {},
        "claim_verification": {},
        "reasoning_output": {},
        "verbose": False,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Scenario 1: constructive refuted, destructive evidence absent -> inconclusive
# ---------------------------------------------------------------------------

class TestScenario1ConstructiveFailureOnly:
    def test_downgrades_to_inconclusive_when_budget_exhausted(self):
        state = _base_state(
            retry_round=MAX_RETRIES,  # no budget left -> must resolve now
            agent_results={"cfg_builder": {"status": "failure", "evidence": None}},
            oracle_test_result={"status": "grammar_incorrect", "trust": "refuted"},
            claim_verification={},  # no destructive agent produced anything
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.92},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["verdict"] is None
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert gate["verdict_gate"]["downgrades"]
        # VERDICT_POLICY.md R1: "constructive_failure_only" — the reasoning
        # agent inferred a destructive verdict from the constructive agent's
        # failure alone, which R1 explicitly says is not evidence.
        assert gate["verdict_gate"]["basis_note"] == "constructive_failure_only"
        assert gate["verdict_gate"]["proof_verified"] is False

    def test_retries_instead_when_budget_available(self):
        """R1/R4: with retry budget left, the gate asks for a retry rather
        than committing to an unearned verdict."""
        state = _base_state(
            retry_round=0,
            agent_results={"cfg_builder": {"status": "failure", "evidence": None}},
            oracle_test_result={"status": "grammar_incorrect", "trust": "refuted"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.92},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "retry"
        assert gate["verdict_gate"]["downgrades"]

    def test_full_node_matches_grammar_filter_49_pattern(self):
        """End-to-end through verdict_gate_node: constructive fails, reasoning
        (wrongly) claims a destructive verdict anyway, no retry budget left."""
        state = _base_state(
            retry_round=MAX_RETRIES,
            agent_results={"cfg_builder": {"status": "failure", "evidence": None}},
            oracle_test_result={"status": "not_applicable"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.9},
        )
        result = verdict_gate_node(state)
        assert result["reasoning_output"]["verdict"] is None
        assert result["reasoning_output"]["confidence"] <= 0.40
        assert result["verdict_gate"]["downgrades"]


# ---------------------------------------------------------------------------
# Scenario 2: contradiction (constructive bounded_pass + destructive well_formed)
# ---------------------------------------------------------------------------

class TestScenario2Contradiction:
    def test_contradiction_flagged_and_confidence_capped(self):
        # docs/VERDICT_POLICY.md R1 fix: destructive trust only counts for an
        # agent whose OWN verdict argues non_cfl (status=="success" and
        # verdict=="non_cfl") — agent_results must carry that, not just
        # claim_verification's structural trust, or the contradiction is
        # never even considered (see cfl_system.orchestrator
        # ._destructive_agent_argues_non_cfl).
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"grammar": {"start": "S", "rules": []}},
                "pumping_cfl": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "pumping_cfl": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert vg["contradiction"] is True
        assert gate["reasoning_output"]["confidence"] <= 0.50
        # Verdict resolves to the stronger side (constructive: bounded_pass > well_formed)
        assert gate["reasoning_output"]["verdict"] == "cfl"

    def test_contradiction_inconclusive_when_trust_tied(self):
        """R3: at equal trust, contradiction resolves to inconclusive, not a
        coin-flip verdict."""
        # docs/VERDICT_POLICY.md R1 fix: destructive trust requires the
        # agent's own verdict to be non_cfl (see comment above).
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"grammar": {"start": "S", "rules": []}},
                "pumping_cfl": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "pumping_cfl": {"trust": "bounded_pass"},
            },
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert vg["contradiction"] is True
        assert gate["reasoning_output"]["verdict"] is None
        assert gate["reasoning_output"]["confidence"] <= 0.50


# ---------------------------------------------------------------------------
# Scenario 3: bounded_pass ceiling (0.85) — the CFL system's own "full" check
# ---------------------------------------------------------------------------

class TestScenario3BoundedPassCeiling:
    def test_confidence_capped_at_0_85_not_higher(self):
        state = _base_state(
            retry_round=0,
            agent_results={"cfg_builder": {"grammar": {"start": "S", "rules": []}}},
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.99},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["confidence"] <= 0.85
        assert gate["reasoning_output"]["confidence"] > 0.5
        assert gate["verdict_gate"]["confidence_cap"] == 0.85
        # claim_verifier never reaches the "verified" trust level in this
        # system, so the deterministic proof_verified flag stays False even
        # though the constructive artifact was checked and passed.
        assert gate["verdict_gate"]["proof_verified"] is False

    def test_confidence_not_raised_above_llm_proposal(self):
        """The cap only ever lowers confidence, never raises a cautious LLM's
        own (lower) number — VERDICT_POLICY.md §2: 'самооценка ... может
        только понизить [confidence]'."""
        state = _base_state(
            retry_round=0,
            agent_results={"cfg_builder": {"grammar": {"start": "S", "rules": []}}},
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.5},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["confidence"] == 0.5


# ---------------------------------------------------------------------------
# Scenario 4: only well_formed destructive proof -> verdict allowed, cap 0.60
# ---------------------------------------------------------------------------

class TestScenario4WellFormedCeiling:
    def test_verdict_allowed_confidence_capped_at_0_60(self):
        # docs/VERDICT_POLICY.md R1 fix: agent_results must carry pumping_cfl's
        # own non_cfl verdict for it to count as destructive evidence at all
        # (see cfl_system.orchestrator._destructive_agent_argues_non_cfl).
        state = _base_state(
            retry_round=0,
            agent_results={"pumping_cfl": {"status": "success", "verdict": "non_cfl", "evidence": {}}},
            oracle_test_result={},
            claim_verification={
                "pumping_cfl": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.85},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert gate["reasoning_output"]["confidence"] <= 0.60
        assert gate["verdict_gate"]["contradiction"] is False
        assert gate["verdict_gate"]["proof_verified"] is False


# ---------------------------------------------------------------------------
# R1 hole (reviewer finding, verdict-gate branch): a well-formed but PRO-CFL
# decomposition claim must never be read as destructive evidence for
# non_cfl just because its structure passed verification. Precedent: the
# grammar_filter_49 pattern PLUS a decomposition agent that argues cfl
# (schema: decomposition verdict in {"cfl", null}) — budget exhausted, no
# actual destructive claim anywhere -> inconclusive, confidence <= 0.40.
# ---------------------------------------------------------------------------

class TestR1DecompositionDoesNotCountAsDestructive:
    def test_pro_cfl_decomposition_does_not_make_non_cfl_evidence(self):
        state = _base_state(
            retry_round=MAX_RETRIES,  # no budget left -> must resolve now
            agent_results={
                "cfg_builder": {"status": "failure", "evidence": None},
                "decomposition": {
                    "status": "success", "verdict": "cfl",
                    "evidence": {
                        "components": [{"name": "L1", "is_cfl": True}],
                        "operation": "union",
                    },
                },
            },
            oracle_test_result={"status": "grammar_incorrect", "trust": "refuted"},
            claim_verification={
                "decomposition": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.92},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["verdict"] is None
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert gate["verdict_gate"]["downgrades"]
        assert gate["verdict_gate"]["basis_note"] == "constructive_failure_only"

    def test_pro_cfl_decomposition_does_not_cause_false_contradiction(self):
        """Reverse side of the same bug: a genuinely-correct cfl verdict
        (oracle pass) must not get cut down to contradiction 0.50 just
        because a pro-CFL decomposition also happens to be well_formed."""
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"grammar": {"start": "S", "rules": []}},
                "decomposition": {
                    "status": "success", "verdict": "cfl",
                    "evidence": {
                        "components": [{"name": "L1", "is_cfl": True}],
                        "operation": "union",
                    },
                },
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "decomposition": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        assert gate["verdict_gate"]["contradiction"] is False
        assert gate["reasoning_output"]["verdict"] == "cfl"
        assert gate["reasoning_output"]["confidence"] <= 0.85


# ---------------------------------------------------------------------------
# R1: a refuted agent elsewhere must not veto a different, valid destructive
# claim (regression test for the cross-agent trust-collection bug found while
# implementing this policy).
# ---------------------------------------------------------------------------

class TestRefutedAgentDoesNotVetoOthers:
    def test_one_refuted_destructive_agent_does_not_block_another(self):
        # docs/VERDICT_POLICY.md R1 fix: both agents' own verdicts must argue
        # non_cfl for their trust to be considered destructive evidence at
        # all (see cfl_system.orchestrator._destructive_agent_argues_non_cfl).
        state = _base_state(
            retry_round=0,
            agent_results={
                "ogden": {"status": "success", "verdict": "non_cfl", "evidence": {}},
                "pumping_cfl": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={},
            claim_verification={
                "ogden": {"trust": "refuted"},
                "pumping_cfl": {"trust": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.7},
        )
        gate = apply_verdict_gate(state)
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert not gate["verdict_gate"]["downgrades"]


# ---------------------------------------------------------------------------
# R7: retry planner input carries trust (docs/VERDICT_POLICY.md R7)
# ---------------------------------------------------------------------------

class TestRetryPlannerReceivesTrust:
    def test_run_retry_planner_node_forwards_trust(self):
        from cfl_system.orchestrator import run_retry_planner_node

        captured: dict = {}

        class _Runner:
            def run_agent(self, agent_name, input_data=None):
                if agent_name == "retry_planner":
                    captured["input"] = input_data
                return {"agents_to_retry": [], "hints": {}}

        state = _base_state(
            mock_runner=_Runner(),
            agent_runner=None,
            evidence={},
            reasoning_output={"action": "retry", "verdict": None},
            trust={"pumping_cfl": "refuted", "cfg_builder": "not_verified"},
        )
        run_retry_planner_node(state)
        assert captured["input"]["reasoning_output"]["trust"] == {
            "pumping_cfl": "refuted",
            "cfg_builder": "not_verified",
        }
