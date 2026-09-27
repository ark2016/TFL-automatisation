"""E2E test for docs/VERDICT_POLICY.md R2' (constructive DCFL certificate):
a genuinely DCFL language ({a^n b^n c^m | n,m>=1}) whose stack_strategy agent
supplies a real, verified DPDA should reach verdict `dcfl` with confidence
capped at 0.85 (bounded_pass), through the *unmodified* verdict gate in
dcfl_system/orchestrator.py -- this test does not touch the gate itself, it
only exercises it via run_pipeline + MockRunner, the same way
test_orchestrator.py's exam_01-04 parametrized tests do.
"""
from __future__ import annotations

import json
from pathlib import Path

from dcfl_system.orchestrator import run_pipeline, MockRunner

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"


def _run() -> dict:
    ir = json.loads((EXAMPLES_DIR / "task_anbncm.json").read_text(encoding="utf-8"))
    mock = MockRunner(str(MOCK_DIR), ir["task_id"])
    return run_pipeline(ir, mock_runner=mock)


def test_anbncm_verdict_is_dcfl():
    result = _run()
    assert result["verdict"] == "dcfl", result.get("verdict_gate")


def test_anbncm_confidence_capped_at_bounded_pass():
    """reasoning's own confidence (0.9, see the mock) must be capped at
    bounded_pass's 0.85 ceiling (docs/VERDICT_POLICY.md §2), not passed
    through -- the gate this test exercises is dcfl_system/orchestrator.py's
    unmodified _apply_verdict_gate."""
    result = _run()
    assert result["confidence"] == 0.85, result.get("verdict_gate")


def test_anbncm_verdict_gate_basis_is_stack_strategy_bounded_pass():
    result = _run()
    gate = result.get("verdict_gate", {})
    assert gate.get("contradiction") is False
    basis = gate.get("basis", [])
    assert any(
        b.get("agent") == "stack_strategy" and b.get("trust") == "bounded_pass"
        for b in basis
    ), basis


def test_anbncm_stack_strategy_oracle_trust_is_bounded_pass():
    result = _run()
    oracle_verification = result.get("oracle_verification", {})
    entry = oracle_verification.get("stack_strategy", {})
    assert entry.get("trust") == "bounded_pass", entry
    assert entry.get("details", {}).get("determinism") == "verified", entry
