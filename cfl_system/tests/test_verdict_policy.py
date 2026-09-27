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
            # cfg_builder DID run (status=success) and produced a grammar —
            # it's the oracle_test that found that grammar wrong
            # (grammar_incorrect/refuted). A reviewer-finding fix means
            # oracle_test trust is only ever attributed to an agent that
            # actually ran to success with a usable artifact, so the
            # fixture must reflect that (not just "any agent present").
            agent_results={"cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}}},
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
            agent_results={"cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}}},
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
        #
        # docs/VERDICT_POLICY.md R3 (post-R3' revision): "bounded_pass vs
        # well_formed" no longer resolves the dispute by rank — with no
        # witness words to cross-check (pumping_cfl's evidence has none),
        # R3' cannot run either, so this stays inconclusive.
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}},
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
        # No longer resolved by rank comparison — bounded_pass no longer
        # automatically beats well_formed (docs/VERDICT_POLICY.md R3 fix).
        assert gate["reasoning_output"]["verdict"] is None
        assert vg["contradiction_details"]["constructive"]["trust"] == "bounded_pass"
        assert vg["contradiction_details"]["destructive"]["trust"] == "well_formed"

    def test_contradiction_inconclusive_when_trust_tied(self):
        """R3: at equal trust, contradiction resolves to inconclusive, not a
        coin-flip verdict."""
        # docs/VERDICT_POLICY.md R1 fix: destructive trust requires the
        # agent's own verdict to be non_cfl (see comment above).
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}},
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
            agent_results={"cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}}},
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
            agent_results={"cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}}},
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
                "cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}},
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
                "cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}},
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

class TestR3PrimeCrossCheck:
    """docs/VERDICT_POLICY.md R3' — the deterministic cross-check attempted
    before an unresolved R3 contradiction: destructive witness words
    (reused from claim_verifier's step-2 checks, not re-derived) run
    through the language oracle AND the constructive artifact."""

    # a^n b^n c^n via a counting predicate — genuinely not CFL (same fixture
    # style as cfl_system/tests/test_claim_verifier.py).
    _IR_ANBNCN = {
        "language_spec": {
            "kind": "predicate",
            "alphabet": ["a", "b", "c"],
            "variable": "w",
            "predicate": {
                "op": "and",
                "operands": [
                    {
                        "op": "eq",
                        "left": {"kind": "count_symbol", "in_var": "w", "symbol": "a"},
                        "right": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                    },
                    {
                        "op": "eq",
                        "left": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                        "right": {"kind": "count_symbol", "in_var": "w", "symbol": "c"},
                    },
                ],
            },
        },
    }

    # Wrong grammar: a*b*c* (regular, definitely CFL, but over-generates —
    # it accepts every word count-mismatched, not just a^n b^n c^n). A
    # small sample-based oracle_test can easily miss this (bounded_pass).
    _WRONG_GRAMMAR = {
        "start": "S",
        "nonterminals": ["S", "A", "B", "C"],
        "terminals": ["a", "b", "c"],
        "rules": [
            {"lhs": "S", "rhs": ["A", "B", "C"]},
            {"lhs": "A", "rhs": ["a", "A"]},
            {"lhs": "A", "rhs": []},
            {"lhs": "B", "rhs": ["b", "B"]},
            {"lhs": "B", "rhs": []},
            {"lhs": "C", "rhs": ["c", "C"]},
            {"lhs": "C", "rhs": []},
        ],
    }

    @staticmethod
    def _exact_word_grammar(word: str) -> dict:
        """A CFG generating exactly {word} (a single unbranching chain) —
        used where the test only needs the artifact to agree with the
        oracle on the handful of witness words actually cross-checked."""
        nonterminals = ["S"] + [f"A{i}" for i in range(1, len(word))]
        rules = []
        for i, ch in enumerate(word):
            lhs = nonterminals[i]
            if i + 1 < len(nonterminals):
                rules.append({"lhs": lhs, "rhs": [ch, nonterminals[i + 1]]})
            else:
                rules.append({"lhs": lhs, "rhs": [ch]})
        return {
            "start": "S",
            "nonterminals": nonterminals,
            "terminals": sorted(set(word)),
            "rules": rules,
        }

    _OGDEN_WITNESSES = [
        {"word": "aaabbbccc", "expected_in_l": True, "source": "word_instances[p=3]"},
        {"word": "aabbbccc", "expected_in_l": False, "source": "ogden p=3 split v='a' x='' i=0"},
    ]

    def test_wwvvR_style_cross_check_refutes_bad_grammar(self):
        """The live-run precedent this rule fixes (docs/VERDICT_POLICY.md R3,
        'wwvvR on Haiku 2026-09-27'): a sample-based bounded_pass grammar and
        a well_formed destructive proof used to resolve by rank (bounded_pass
        wins) — wrongly, when the grammar over-generates. R3' catches it:
        ogden's own witness word (∉ L per the oracle) is accepted by the
        grammar -> the artifact is refuted -> non_cfl, not cfl."""
        state = _base_state(
            retry_round=0,
            ir=self._IR_ANBNCN,
            agent_results={
                "cfg_builder": {"status": "success", "grammar": self._WRONG_GRAMMAR, "evidence": {}},
                "ogden": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "ogden": {
                    "trust": "well_formed",
                    "verification_status": "well_formed",
                    "details": {"destructive_witnesses": self._OGDEN_WITNESSES},
                },
            },
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        cc = vg["contradiction_details"]["cross_check"]
        assert cc["performed"] is True
        assert cc["constructive_refuted_agents"] == ["cfg_builder"]
        assert cc["destructive_refuted"] is False
        assert vg["contradiction"] is False
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert gate["reasoning_output"]["confidence"] <= 0.60
        assert gate["trust"]["cfg_builder"] == "refuted"

    def test_both_sides_survive_cross_check_stays_inconclusive(self):
        """R3' runs and finds no counterexample on either side (a grammar
        that happens to agree with the oracle on these specific witnesses) —
        the contradiction is NOT resolved. docs/VERDICT_POLICY.md R3 (post-
        R3' revision): 'bounded_pass vs well_formed' no longer settles this
        by rank -> stays inconclusive, confidence <= 0.50."""
        state = _base_state(
            retry_round=0,
            ir=self._IR_ANBNCN,
            agent_results={
                "cfg_builder": {"status": "success", "grammar": self._exact_word_grammar("aaabbbccc"), "evidence": {}},
                "ogden": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "ogden": {
                    "trust": "well_formed",
                    "verification_status": "well_formed",
                    "details": {"destructive_witnesses": self._OGDEN_WITNESSES},
                },
            },
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        cc = vg["contradiction_details"]["cross_check"]
        assert cc["performed"] is True
        assert cc["constructive_refuted_agents"] == []
        assert cc["destructive_refuted"] is False
        assert vg["contradiction"] is True
        assert gate["reasoning_output"]["verdict"] is None
        assert gate["reasoning_output"]["confidence"] <= 0.50

    def test_verified_side_wins_unresolved_contradiction(self):
        """No witnesses at all -> R3' cannot run -> falls back to the plain
        R3 rule: only a `verified` side wins an unresolved contradiction,
        capped at 0.85 (not the normal 0.98 'verified' ceiling)."""
        state = _base_state(
            retry_round=0,
            agent_results={
                "cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}},
                "pumping_cfl": {"status": "success", "verdict": "non_cfl", "evidence": {}},
            },
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={
                "pumping_cfl": {"trust": "verified"},
            },
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.99},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert vg["contradiction"] is True
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert gate["reasoning_output"]["confidence"] == 0.85


# ---------------------------------------------------------------------------
# R4': retry budget exhausted -> gate picks the strongest admissible basis
# instead of defaulting straight to inconclusive (docs/VERDICT_POLICY.md R4').
# Precedent: live-run eval 2026-09-27, cfl-07/cfl-12 ended `failure 0.0`
# despite a well_formed destructive proof on record.
# ---------------------------------------------------------------------------

class TestR4PrimeStrongestAdmissibleBasis:
    def test_destructive_well_formed_wins_when_constructive_has_no_artifact(self):
        """reasoning proposes done/cfl with no artifact at all; retries are
        exhausted; ogden is well_formed and argues non_cfl -> non_cfl 0.60,
        not inconclusive and not `failure`."""
        state = _base_state(
            retry_round=MAX_RETRIES,
            agent_results={"ogden": {"status": "success", "verdict": "non_cfl", "evidence": {}}},
            oracle_test_result={},
            claim_verification={
                "ogden": {"trust": "well_formed", "verification_status": "well_formed"},
            },
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert gate["reasoning_output"]["confidence"] <= 0.60
        assert any("retry budget exhausted" in d and "strongest admissible basis" in d for d in vg["downgrades"])
        # R1/R3 must still hold: this is a real destructive claim, not a
        # constructive-failure inference.
        assert vg["basis_trust"] == "well_formed"

    def test_no_destructive_evidence_stays_inconclusive_not_failure(self):
        """Same exhausted-budget situation, but with no destructive claim of
        any kind on record -> inconclusive (verdict None, confidence <=
        0.40), never `failure`."""
        state = _base_state(
            retry_round=MAX_RETRIES,
            agent_results={"cfg_builder": {"status": "failure", "evidence": None}},
            oracle_test_result={"status": "not_applicable"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] is None
        assert gate["reasoning_output"]["confidence"] <= 0.40
        assert any("strongest admissible basis: inconclusive" in d for d in vg["downgrades"])

    def test_constructive_bounded_pass_rescues_unsupported_destructive_proposal(self):
        """Symmetric case: reasoning proposes done/non_cfl without adequate
        destructive evidence, retries exhausted, but a constructive artifact
        clears bounded_pass -> the gate falls back to cfl, not inconclusive."""
        state = _base_state(
            retry_round=MAX_RETRIES,
            agent_results={"cfg_builder": {"status": "success", "grammar": {"start": "S", "rules": []}, "evidence": {}}},
            oracle_test_result={"status": "pass", "trust": "bounded_pass"},
            claim_verification={},
            reasoning_output={"action": "done", "verdict": "non_cfl", "confidence": 0.9},
        )
        gate = apply_verdict_gate(state)
        vg = gate["verdict_gate"]
        assert gate["reasoning_output"]["action"] == "done"
        assert gate["reasoning_output"]["verdict"] == "cfl"
        assert gate["reasoning_output"]["confidence"] <= 0.85
        assert any("strongest admissible basis" in d and "-> cfl" in d for d in vg["downgrades"])


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


# ---------------------------------------------------------------------------
# Reviewer finding (blocker): verify_closure_claim's nested pumping/Ogden
# witnesses were computed against the L ∩ R oracle, so `expected_in_l=False`
# really meant "not in L ∩ R" — not "not in L". _cross_check_r3prime then
# tested them against the plain L oracle, so a pumped word that had left R
# but was still in L would look like a false claim by a CORRECT destructive
# proof and get it wrongly refuted, handing the verdict to an over-generating
# grammar. End-to-end regression: real verify_agent_claims output on a
# closure_reduction proof, then apply_verdict_gate, on the exact language
# from the reviewer's repro (L = (Σ* \ a*b*c*) ∪ {a^n b^n c^n}, R = a*b*c*).
# ---------------------------------------------------------------------------

import re as _re
from unittest.mock import patch

from cfl_system.lib.claim_verifier import verify_agent_claims


def _anbncn_union_oracle(word: str) -> bool:
    """L = (Sigma* minus a*b*c*) union {a^n b^n c^n} — reviewer's repro language."""
    m = _re.fullmatch(r"(a*)(b*)(c*)", word)
    if m is None:
        return True  # doesn't even match a*b*c* -> outside R -> in L
    na, nb, nc = len(m.group(1)), len(m.group(2)), len(m.group(3))
    return na == nb == nc


# An over-generating "wrong but sample-passing" grammar: pure a*b*c* with no
# constraint that the three counts agree (accepts e.g. "aabbbccc", which is
# NOT in the target language). Exactly the failure mode described in
# docs/VERDICT_POLICY.md R3 (bounded_pass grammar, over-generates).
_OVERGENERATING_ANBNCN_GRAMMAR = {
    "start": "S",
    "nonterminals": ["S", "A", "B", "C"],
    "terminals": ["a", "b", "c"],
    "rules": [
        {"lhs": "S", "rhs": ["A", "B", "C"]},
        {"lhs": "A", "rhs": ["a", "A"]}, {"lhs": "A", "rhs": []},
        {"lhs": "B", "rhs": ["b", "B"]}, {"lhs": "B", "rhs": []},
        {"lhs": "C", "rhs": ["c", "C"]}, {"lhs": "C", "rhs": []},
    ],
}

_ANBNCN_CLOSURE_EVIDENCE = {
    "regular_language_regex": "a*b*c*",
    "intersection_description": "L ∩ R = {a^n b^n c^n} (the equal-count branch of L already lies in R).",
    "intersection_examples": ["aaabbbccc", "abc", "aabbcc"],
    "intersection_non_examples": ["aabbbccc", "aaabbccc"],
    "intersection_not_cfl_proof": {
        "method": "pumping",
        "word_chosen": "a^(3p) b^(3p) c^(3p)",
        "word_instances": {"3": "aaabbbccc"},
        "cases": [{"case": "standard a^n b^n c^n pumping", "why_not_in_L": "breaks n=n=n"}],
        "all_cases_covered": True,
    },
}


class TestR3PrimeNestedWitnessLeavingRNoLongerFalseRefutes:
    """docs/VERDICT_POLICY.md R3'/closure_reduction fix (reviewer finding)."""

    def _run(self, extra_agent_results: dict | None = None) -> dict:
        agent_output = {
            "agent": "closure_reduction", "status": "success",
            "evidence": _ANBNCN_CLOSURE_EVIDENCE,
        }
        # The custom union-language oracle isn't expressible in the IR
        # predicate mini-language (no "not regex" combinator), so both the
        # claim_verifier's oracle build and the orchestrator's cross-check
        # oracle build are pointed at the same hand-written oracle — the
        # real `verify_agent_claims` -> `apply_verdict_gate` code paths
        # still run unmodified, only the membership function is supplied
        # directly instead of parsed from a language_spec kind.
        with patch(
            "cfl_system.lib.claim_verifier._get_oracle", return_value=_anbncn_union_oracle,
        ), patch(
            "cfl_system.orchestrator.cfl_oracle_from_ir", return_value=_anbncn_union_oracle,
        ):
            claim_verification = {"closure_reduction": verify_agent_claims(agent_output, {})}
            agent_results = {
                "cfg_builder": {
                    "status": "success", "grammar": _OVERGENERATING_ANBNCN_GRAMMAR, "evidence": {},
                },
                "closure_reduction": {
                    "status": "success", "verdict": "non_cfl", "evidence": _ANBNCN_CLOSURE_EVIDENCE,
                },
            }
            if extra_agent_results:
                agent_results.update(extra_agent_results)
            state = _base_state(
                retry_round=0,
                ir={},
                agent_results=agent_results,
                oracle_test_result={"status": "pass", "trust": "bounded_pass"},
                claim_verification=claim_verification,
                # The wrong grammar's sample pass convinced reasoning to
                # propose "cfl" at high confidence — the live-run precedent
                # this rule fixes (docs/VERDICT_POLICY.md R3).
                reasoning_output={"action": "done", "verdict": "cfl", "confidence": 0.85},
            )
            return apply_verdict_gate(state)

    def test_witnesses_leaving_R_are_filtered_not_reused(self):
        """Finding-1 fix, checked directly: no destructive witness reused by
        the cross-check should claim a membership fact about a word outside
        R — those were exactly the ones computed against L ∩ R, not L."""
        agent_output = {
            "agent": "closure_reduction", "status": "success",
            "evidence": _ANBNCN_CLOSURE_EVIDENCE,
        }
        with patch(
            "cfl_system.lib.claim_verifier._get_oracle", return_value=_anbncn_union_oracle,
        ):
            result = verify_agent_claims(agent_output, {})
        witnesses = result["details"]["destructive_witnesses"]
        assert witnesses  # the fixture does produce witnesses
        r_pattern = _re.compile("a*b*c*")
        assert all(r_pattern.fullmatch(w["word"]) for w in witnesses)

    def test_overgenerating_grammar_refuted_verdict_stays_non_cfl(self):
        gate = self._run()
        vg = gate["verdict_gate"]
        cc = vg["contradiction_details"]["cross_check"]
        assert cc["performed"] is True
        assert cc["constructive_refuted_agents"] == ["cfg_builder"]
        assert cc["destructive_refuted"] is False
        assert vg["contradiction"] is False
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert gate["trust"]["cfg_builder"] == "refuted"

    def test_with_failed_pda_builder_present_still_resolves(self):
        """docs/VERDICT_POLICY.md R3'/_collect_agent_trust fix (reviewer
        finding): a pda_builder that errored out must not borrow cfg_builder's
        oracle_test trust and must not block the contradiction from
        resolving once cfg_builder is refuted by the cross-check."""
        gate = self._run(extra_agent_results={
            "pda_builder": {"status": "agent_error", "errors": ["PDA build failed"]},
        })
        vg = gate["verdict_gate"]
        assert vg["contradiction"] is False
        assert gate["reasoning_output"]["verdict"] == "non_cfl"
        assert "pda_builder" not in gate["trust"]
