"""Rejected REG proposals must stay inconclusive in every public output."""

import pytest

from agent_system.formalize import apply_gate
from agent_system.graph import assemble_result_node
from agent_system.lib.progress import extract_verdict
from agent_system.lib.renderer import render_html, render_markdown
from agent_system.orchestrator import _result_verdict
from tfl_eval.runners import extract


def _state(verdict="non_regular", action="proceed_to_formalizer", confidence=0.99):
    reasoning = {"evidence": {
        "verdict": verdict, "action": action, "confidence": confidence,
    }}
    return {
        "evidence": {
            "reasoning": reasoning,
            "hypothesis": {"hypothesis": "non_regular"},
        },
        "reasoning_output": reasoning,
        "hypothesis": {"hypothesis": "non_regular", "confidence": 0.99},
        "errors": [],
    }


def _assert_inconclusive_outputs(result):
    assert _result_verdict(result) is None
    assert extract("reg", result)["verdict"] is None
    assert extract_verdict(result)[0] is None
    html = render_html(result)
    assert '<span class="s-badge s-unknown">не определено</span>' in html
    assert '<span class="s-badge s-nonreg">' not in html
    assert '<span class="s-badge s-reg">' not in html
    final_section = render_markdown(result).split("## Итог", 1)[1]
    assert "**Вердикт:** не определено" in final_section


@pytest.mark.parametrize("case", ["unsupported", "refuted", "contradiction", "escalated"])
def test_gate_rejection_survives_export_and_rendering(case):
    state = _state(action="escalate" if case == "escalated" else "proceed_to_formalizer")
    if case == "refuted":
        state["test_result"] = {
            "status": "fail",
            "counterexample": {"word": "a", "oracle_says": True, "automaton_says": False},
        }
    elif case == "contradiction":
        state["test_result"] = {"status": "pass", "tested": 255}
        state["evidence"]["pumping_verification"] = {"trust": "well_formed"}
    result = assemble_result_node(state)["result"]
    assert result["status"] == "partial"
    assert result["verdict"] is None
    assert result["confidence"] <= (0.5 if case == "contradiction" else 0.4)
    _assert_inconclusive_outputs(result)


@pytest.mark.parametrize("status", ["partial", "failure"])
@pytest.mark.parametrize("has_public_verdict", [False, True])
def test_legacy_rejected_result_does_not_revive_reasoning_or_hypothesis(status, has_public_verdict):
    result = {"status": status, "confidence": 0.4, "evidence": _state()["evidence"]}
    if has_public_verdict:
        result["verdict"] = "non_regular"
    _assert_inconclusive_outputs(result)


def test_explicit_null_prevents_legacy_fallback():
    result = {"status": "success", "verdict": None, "confidence": 0.4,
              "evidence": _state()["evidence"]}
    _assert_inconclusive_outputs(result)


def test_explicit_accepted_verdict_precedes_old_reasoning():
    result = {"status": "success", "verdict": "regular", "confidence": 0.85,
              "evidence": _state()["evidence"]}
    assert _result_verdict(result) == "regular"
    assert extract("reg", result)["verdict"] == "regular"
    assert extract_verdict(result)[0] == "regular"
    assert '<span class="s-badge s-reg">регулярен</span>' in render_html(result)
    assert "**Вердикт:** regular" in render_markdown(result).split("## Итог", 1)[1]


def test_accepted_legacy_result_still_reads_reasoning():
    result = {"status": "success", "confidence": 0.55, "evidence": _state()["evidence"]}
    assert _result_verdict(result) == "non_regular"
    assert extract("reg", result)["verdict"] == "non_regular"


@pytest.mark.parametrize("confidence,expected", [(0.99, 0.4), (0, 0), (0.3, 0.3),
                                                  (None, 0.4), ("invalid", 0.4),
                                                  (float("nan"), 0.4)])
def test_escalation_caps_confidence_without_discarding_zero(confidence, expected):
    result = assemble_result_node(_state(action="escalate", confidence=confidence))["result"]
    assert result["confidence"] == expected
    assert result["verdict_gate"]["confidence_cap"] == expected
    assert result["verdict_gate"]["downgrades"]
    assert result["verdict"] is None


def test_constructive_fallback_replaces_stale_hypothesis():
    result = assemble_result_node({
        "test_result": {"status": "pass", "tested": 255},
        "evidence": {"hypothesis": {"hypothesis": "non_regular"}},
    })["result"]
    assert result["status"] == "success"
    assert result["verdict"] == "regular"
    assert _result_verdict(result) == "regular"


@pytest.mark.parametrize("direction", ["regular", "non_regular"])
def test_lean_result_replaces_null_and_is_visible_to_all_consumers(direction):
    result = assemble_result_node(_state())["result"]
    assert result["verdict"] is None
    assert apply_gate(result, {"status": "proved", "direction": direction})
    assert result["status"] == "success"
    assert result["verdict"] == direction
    assert _result_verdict(result) == direction
    assert extract("reg", result)["verdict"] == direction
    assert extract_verdict(result)[0] == direction


def test_failed_formalization_does_not_restore_rejected_candidate():
    result = assemble_result_node(_state())["result"]
    assert not apply_gate(result, {"status": "error", "direction": "non_regular"})
    _assert_inconclusive_outputs(result)


@pytest.mark.parametrize("verdict", ["uncertain", "unknown", "non_cfl"])
def test_unrecognized_proposal_without_evidence_is_inconclusive(verdict):
    result = assemble_result_node(_state(verdict=verdict))["result"]
    assert result["status"] == "partial"
    assert result["confidence"] <= 0.4
    assert result["verdict"] is None
    _assert_inconclusive_outputs(result)
