"""
E2E tests for the DCFL pipeline orchestrator using mock data.

Tests run_pipeline with MockRunner for all 4 exam tasks,
verifying verdicts, confidence, agent results, and edge cases.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from dcfl_system.orchestrator import (
    run_pipeline, MockRunner, DCFL_SPECIALIST_NAMES, collect_specialists_node,
    retry_planner_node, MAX_CALLS_PER_AGENT,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"

# (ir_filename, expected_verdict, expected_confidence)
#
# docs/THEORY.md Part II §1.6-1.8 (round 2): all three exam languages below
# are actually non-DCFL; the round-1 reference verdicts ("dcfl 0.92" / "dcfl
# 0.88" / the incomplete "non_dcfl 0.5") were wrong or incomplete:
#
# - task_wvaavRwR (§1.6): stack_strategy now fails ('aa' is not a true phase
#   separator — it also occurs inside w and inside v), dcfl_pumping succeeds
#   with a full Yu two-word pumping proof (confidence 0.9) -> reasoning
#   picks dcfl_pumping as primary_evidence, verdict=non_dcfl.
# - task_u1au2_u3au4 (§1.7): stack_strategy fails (no fixed separating
#   occurrence of 'a'), shallit succeeds via Theorem 4.7.4 on the derived
#   language L2 = {u3au4 | |u3|>=|u4|} (all Nerode classes finite,
#   confidence 0.9) -> reasoning picks shallit as primary_evidence.
# - task_anb_cnbn (§1.8): inh_ambiguity is corrected to not_applicable (the
#   two branches c^n / b^n are disjoint for n>=1 and each individually
#   unambiguous, so essential ambiguity is not established), and
#   dcfl_pumping now succeeds with a complete Yu pumping proof (confidence
#   0.9) instead of the old incomplete "uncertain 0.5" attempt.
#
# In all three cases the *verdict* below is copied verbatim from each task's
# `*_reasoning.json` mock (MockRunner finds and returns that file directly,
# so the reasoning node never falls back to `_fallback_reasoning` in these
# parametrized tests). See `test_fallback_reasoning_matches_specialist_mocks`
# below for a check that does exercise `_fallback_reasoning` directly against
# the specialist mocks (with the reasoning mock unavailable).
#
# *Confidence* is NOT copied verbatim any more: docs/VERDICT_POLICY.md §2-3
# (orchestrator.py's `_apply_verdict_gate`) caps confidence at the trust the
# deterministic oracle assigned to the winning specialist's artifact, never
# at the LLM's own self-reported 0.9. shallit's proof_sketch fields
# (distinguishing_suffix etc.) for task_u1au2_u3au4 and task_anb_cnbn's
# dcfl_pumping-style free-text math prose are not clean instantiable
# patterns / carry no `word_instances`, so `oracle_verifier`'s step-2
# semantic check (§4) never fires and trust stays at the structural-only
# `well_formed` tier -> confidence cap 0.55 (§2). task_wvaavRwR's
# dcfl_pumping mock DOES carry a mandatory `word_instances` (docs/VERDICT_
# POLICY.md §4 dcfl/dcfl_pumping) with concrete literal words at n = p + 1
# for p in {2, 3}, and a real set_builder oracle is available for this
# task -> the semantic check runs conditions (1)/(2)'s brute force and
# closes for both p -- but closure at a small, fixed p in {2, 3} is NOT
# exhaustive evidence for Yu's lemma (backlog review, BLOCKER fix: this is
# reproducibly reachable for an ACTUALLY-DCFL language too, e.g. dozens of
# word_instances pairs for task_grammar_aSSb close the same way), so trust
# stays at `well_formed` -> confidence cap 0.55, same as the other two
# dcfl_pumping-based tasks below. See dcfl_system/tests/test_verdict_policy.py
# for more cases that exercise the step-2 oracle path (well_formed / refuted)
# directly.
TASKS = [
    ("task_wvaavRwR", "non_dcfl", 0.55),
    ("task_u1au2_u3au4", "non_dcfl", 0.55),
    ("task_anb_cnbn", "non_dcfl", 0.55),
    # task_grammar_aSSb (dcfl_exam_04, docs/THEORY.md §1.10): the language IS
    # DCFL (status established by direct construction, not just an
    # unverified word-level "стратегия") — a profile NPDA is proven
    # height-deterministic (rhpda, [NS, Def. 2]) and determinized via [NS,
    # Thm 4], giving a machine-built DPDA certificate (345 states, 5835
    # transitions, dcfl_system/examples/certificates/grammar_aSSb_dpda.json)
    # inlined into the stack_strategy mock's `proof_sketch.dpda` (R2':
    # "конструктивный сертификат для DCFL"). oracle_verifier checks it in two
    # steps: (a) `check_determinism` -- a complete syntactic check, clean on
    # this certificate; (b) simulation (`dpda_accepts`, via
    # cfl_system.lib.pda_simulator) against the TASK's OWN grammar oracle
    # (`build_grammar_membership_oracle`, CYK on the grammar's CNF form --
    # word_sampler.sample_from_grammar already supports `input_format:
    # "grammar"`, generating ~59 distinct words up to length 10 for this
    # task's BFS-derivable language) on >= 30 words -> trust `bounded_pass`,
    # confidence capped at 0.85 (VERDICT_POLICY.md §2), not the 0.55 the old
    # (pre-§1.10, pre-certificate) mock used to get away with on a bare
    # word-level claim. The other four specialists are correctly
    # not_applicable here (§1.10 "Замечания": Yu's pumping lemma can't find a
    # counterexample, Shallit's theorem needs a finite dead class but this
    # language's dead class is infinite, grammar ambiguity says nothing
    # about the language's own (un)ambiguity, and no closure-property
    # reduction was needed) -- stack_strategy is the sole dominant
    # specialist, on both the reasoning-mock and the `_fallback_reasoning`
    # path (see TASKS below).
    ("task_grammar_aSSb", "dcfl", 0.85),
]


def _load_ir(task_filename: str) -> dict:
    path = EXAMPLES_DIR / f"{task_filename}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _run_task(task_filename: str) -> dict:
    """Run the (deterministic, mock-backed) pipeline once per task and reuse
    the result across every test that just reads it (read-only in this
    module -- see the individual tests). task_grammar_aSSb's certificate
    DPDA (345 states, 5835 transitions) makes a fresh run take ~25-30s, and
    without this cache ~9 tests below each paid that cost separately."""
    ir = _load_ir(task_filename)
    mock = MockRunner(str(MOCK_DIR), ir["task_id"])
    return run_pipeline(ir, mock_runner=mock)


# ---------------------------------------------------------------------------
# Parametrized E2E tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_verdict_correct(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    assert result["verdict"] == expected_verdict, (
        f"Expected verdict={expected_verdict}, got {result['verdict']}"
    )


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_confidence_correct(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    assert result["confidence"] == pytest.approx(expected_confidence, abs=0.01), (
        f"Expected confidence={expected_confidence}, got {result['confidence']}"
    )


class _NoReasoningMockRunner(MockRunner):
    """MockRunner that pretends no reasoning mock exists, forcing the
    orchestrator's reasoning node to fall back to `_fallback_reasoning`
    (heuristic consolidation over the specialist mock outputs)."""

    def run_agent(self, agent_name: str, input_data: dict | None = None):
        if agent_name == "reasoning":
            return None
        return super().run_agent(agent_name, input_data)


@pytest.mark.parametrize(
    "task_filename,expected_verdict,expected_confidence",
    TASKS,  # all four exam tasks now have a single dominant specialist mock
)
def test_fallback_reasoning_matches_specialist_mocks(
    task_filename, expected_verdict, expected_confidence
):
    """With the reasoning mock unavailable, `_fallback_reasoning` must pick
    the highest-confidence specialist verdict from the specialist mocks
    directly, reproducing the same non_dcfl/0.9 result as the reasoning mock."""
    ir = _load_ir(task_filename)
    mock = _NoReasoningMockRunner(str(MOCK_DIR), ir["task_id"])
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == expected_verdict, (
        f"Fallback reasoning: expected verdict={expected_verdict}, got {result['verdict']}"
    )
    assert result["confidence"] == pytest.approx(expected_confidence, abs=0.01), (
        f"Fallback reasoning: expected confidence={expected_confidence}, got {result['confidence']}"
    )


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_all_specialists_present(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    agents_used = set(result.get("agents_used", []))
    for name in DCFL_SPECIALIST_NAMES:
        assert name in agents_used, f"Missing specialist {name} in agents_used"


# ---------------------------------------------------------------------------
# LLM-path (mock with top-level `action`) vs fallback-path (no reasoning mock)
#
# root TODO.md §5: every `*_reasoning.json` mock now carries a top-level
# `action` (done/retry/invert per reasoning_agent.md's contract), so
# `reasoning_agent_node` takes the actual LLM-response branch (parses the
# mock's own verdict/confidence/summary) instead of always falling through to
# `_fallback_reasoning`. These tests pin down that collect_specialists_node
# (fan-in) and the verdict gate both run correctly on *each* of the two
# branches, and that the two branches are observably different paths (not
# just coincidentally equal outputs).
# ---------------------------------------------------------------------------

_FALLBACK_MARKER = ["Fallback reasoning (no LLM available)"]


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS[:3])
def test_llm_path_reasoning_mock_is_used_directly(
    task_filename, expected_verdict, expected_confidence
):
    """With a normal MockRunner, the reasoning mock (action="done") is
    consumed directly by reasoning_agent_node — the result must carry that
    mock's own summary/primary_evidence, not the generic fallback text, and
    fan-in (agent_results/specialist_outputs) plus the verdict gate must
    still have produced the expected gated verdict/confidence."""
    result = _run_task(task_filename)
    task_id = _load_ir(task_filename)["task_id"]
    reasoning_mock = json.loads(
        (MOCK_DIR / f"{task_id}_reasoning.json").read_text(encoding="utf-8")
    )
    assert reasoning_mock["action"] == "done"
    assert result["hints_for_human"] != _FALLBACK_MARKER
    assert result["reasoning_summary"] == reasoning_mock["summary"]
    assert result["primary_evidence"] == reasoning_mock["primary_evidence"]
    # collect_specialists_node's fan-in populated all 5 specialists for the
    # verdict gate to reason over.
    assert set(result["specialist_outputs"]) == set(DCFL_SPECIALIST_NAMES)
    # The verdict gate (docs/VERDICT_POLICY.md) ran and, per the module
    # docstring above, capped confidence below the mock's own 0.9 down to
    # the structural-only trust ceiling — it did not just copy the mock.
    assert result["verdict"] == expected_verdict
    assert result["confidence"] == pytest.approx(expected_confidence, abs=0.01)
    assert result["confidence"] != pytest.approx(reasoning_mock["confidence"], abs=0.01)


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS[:3])
def test_fallback_path_is_actually_taken_without_reasoning_mock(
    task_filename, expected_verdict, expected_confidence
):
    """With the reasoning mock unavailable (`_NoReasoningMockRunner`),
    `_fallback_reasoning` runs instead — confirmed by its distinctive
    `hints_for_human` marker — and still reaches the same gated
    verdict/confidence as the LLM path via fan-in over the specialist mocks."""
    ir = _load_ir(task_filename)
    mock = _NoReasoningMockRunner(str(MOCK_DIR), ir["task_id"])
    result = run_pipeline(ir, mock_runner=mock)
    assert result["hints_for_human"] == _FALLBACK_MARKER
    assert set(result["specialist_outputs"]) == set(DCFL_SPECIALIST_NAMES)
    assert result["verdict"] == expected_verdict
    assert result["confidence"] == pytest.approx(expected_confidence, abs=0.01)


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_result_has_required_keys(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    required_keys = {
        "task", "source_text", "verdict", "confidence",
        "hypothesis", "preprocess", "agents_used",
        "specialist_outputs", "errors", "retries",
    }
    missing = required_keys - set(result.keys())
    assert not missing, f"Missing keys: {missing}"


# ---------------------------------------------------------------------------
# Preprocess and hypothesis checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_preprocess_has_expected_keys(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    preprocess = result.get("preprocess")
    assert preprocess is not None, "preprocess should be populated"
    assert "patterns" in preprocess
    assert "closure" in preprocess
    assert "sample_words" in preprocess


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_hypothesis_populated(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    hypothesis = result.get("hypothesis")
    assert hypothesis is not None, "hypothesis should be populated"
    assert isinstance(hypothesis, dict)


# ---------------------------------------------------------------------------
# Invalid IR
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# collect_specialists_node: agent_error on retry must not erase a valid
# result from an earlier round (root TODO.md §2)
# ---------------------------------------------------------------------------

def test_collect_specialists_keeps_previous_round_result_on_agent_error():
    """A retry round where `stack_strategy` comes back as `agent_error`
    (specialist_outputs entry `None`) must keep the round-0 valid result
    already in `agent_results`, not pop it."""
    prior_result = {"status": "success", "verdict": "dcfl", "confidence": 0.8}
    state = {
        "agent_results": {"stack_strategy": prior_result, "shallit": {"status": "fail"}},
        "evidence": {},
        "dispatch": {"stack_strategy": True},
        "specialist_outputs": [("stack_strategy", None)],
    }
    out = collect_specialists_node(state)
    assert out["agent_results"]["stack_strategy"] == prior_result
    assert out["evidence"]["stack_strategy"] == prior_result


def test_collect_specialists_still_drops_agent_never_seen_before():
    """If an agent has never produced a result and errors again, there is
    nothing to keep — it stays absent (not a regression from the fix)."""
    state = {
        "agent_results": {},
        "evidence": {},
        "dispatch": {"stack_strategy": True},
        "specialist_outputs": [("stack_strategy", None)],
    }
    out = collect_specialists_node(state)
    assert "stack_strategy" not in out["agent_results"]
    assert "stack_strategy" not in out["evidence"]


def test_invalid_ir_early_failure():
    """Pipeline with invalid IR should fail early with errors."""
    bad_ir = {"not_a_valid": "ir"}
    result = run_pipeline(bad_ir)
    assert result["verdict"] in ("failure", "inconclusive")
    assert len(result.get("errors", [])) > 0


def test_none_ir_early_failure():
    """Pipeline with empty dict should produce errors."""
    result = run_pipeline({})
    assert result["verdict"] in ("failure", "inconclusive")
    assert len(result.get("errors", [])) > 0


class TestRetryPlannerNodeRespectsCallCap:
    """Cost ceiling (config.MAX_CALLS_PER_AGENT): ``retry_planner_node``
    must not hand a fully-capped agent back for another wasted graph
    round -- the terminal case (``needs_retry`` False) once every proposed
    agent is capped, and each exclusion recorded for
    ``verdict_gate.downgrades``."""

    def _capped(self, agent: str) -> list[tuple[str, dict]]:
        return [(agent, {"agent": agent, "status": "success"})] * MAX_CALLS_PER_AGENT

    def test_all_proposed_agents_capped_ends_retries_with_a_downgrade_note(self):
        state = {
            "reasoning": {
                "retry_plan": {"agents_to_retry": ["stack_strategy"], "hints": {}},
            },
            "agent_results": {},
            "oracle_verification": {},
            "retry_count": 0,
            "specialist_outputs": self._capped("stack_strategy"),
            "verbose": False,
        }
        result = retry_planner_node(state)

        assert result["agents_to_retry"] == []
        assert result["retry_plan"]["needs_retry"] is False
        assert "call_cap_notes" in result
        notes = " ".join(result["call_cap_notes"])
        assert "stack_strategy" in notes and "call cap reached" in notes

    def test_one_of_two_proposed_agents_capped_keeps_the_other(self):
        state = {
            "reasoning": {
                "retry_plan": {
                    "agents_to_retry": ["stack_strategy", "shallit"], "hints": {},
                },
            },
            "agent_results": {},
            "oracle_verification": {},
            "retry_count": 0,
            "specialist_outputs": self._capped("stack_strategy"),
            "verbose": False,
        }
        result = retry_planner_node(state)

        assert result["agents_to_retry"] == ["shallit"]
        assert result["retry_plan"]["needs_retry"] is True
        assert "call_cap_notes" in result
        assert any("stack_strategy" in n for n in result["call_cap_notes"])
