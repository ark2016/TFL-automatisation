"""
E2E tests for the DCFL pipeline orchestrator using mock data.

Tests run_pipeline with MockRunner for all 4 exam tasks,
verifying verdicts, confidence, agent results, and edge cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcfl_system.orchestrator import run_pipeline, MockRunner, DCFL_SPECIALIST_NAMES

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"

# (ir_filename, expected_verdict, expected_confidence)
TASKS = [
    ("task_wvaavRwR", "dcfl", 0.92),
    ("task_u1au2_u3au4", "dcfl", 0.88),
    ("task_anb_cnbn", "non_dcfl", 0.90),
    ("task_grammar_aSSb", "dcfl", 0.55),
]


def _load_ir(task_filename: str) -> dict:
    path = EXAMPLES_DIR / f"{task_filename}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_task(task_filename: str) -> dict:
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


@pytest.mark.parametrize("task_filename,expected_verdict,expected_confidence", TASKS)
def test_all_specialists_present(task_filename, expected_verdict, expected_confidence):
    result = _run_task(task_filename)
    agents_used = set(result.get("agents_used", []))
    for name in DCFL_SPECIALIST_NAMES:
        assert name in agents_used, f"Missing specialist {name} in agents_used"


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
