"""``result["usage"]`` (TODO.md §3) -- present in every ``run_pipeline``
result (even with no live agent_runner), and consistent with the number of
calls a ``LiveRunner`` actually made through the shared ``FakeAnthropic``
test double."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn
from ll_system.orchestrator import LiveRunner, MockRunner, run_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)


def test_usage_block_present_and_zero_without_agent_runner():
    ir = json.loads((EXAMPLES_DIR / "format1_anbn_union_ancn.json").read_text(encoding="utf-8"))
    mock = MockRunner(str(MOCK_DIR), "anbn_ancn")

    result = run_pipeline(ir, mock_runner=mock)

    assert "usage" in result
    usage = result["usage"]
    assert usage["calls"] == 0
    assert usage["total_tokens"] == 0
    assert usage["structured_output_calls"] == 0
    assert usage["extraction_fallback_calls"] == 0
    assert usage["estimated_cost_usd"] is None
    assert usage["per_agent"] == {}


_MARKER_ANALYZER_PROOF_SKETCH = (
    '{"method": "marker_detection", "marker_found": true, "marker_symbol": "c", '
    '"marker_type": "explicit", "marker_position": "after prefix", '
    '"marker_description": "...", "ll_usage": "...", "suggested_k": 1}'
)
_MARKER_ANALYZER_ARTIFACTS = (
    '{"counterexample_words": [], "first_follow_table": null, "ll_grammar": null}'
)


def test_usage_tracker_consistent_with_calls_made():
    # `marker_analyzer` has a real `_FIELD_SCHEMAS` entry
    # (agent_output_schema.py) -- unlike `ll_grammar_builder`, which is
    # `_DYNAMIC_KEY_EXEMPT` (its `artifacts.first_follow_table` is keyed by
    # the grammar's own nonterminal symbols, which `output_config.format`
    # can't express) and so never gets structured outputs at all.
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    runner.client = FakeAnthropic([
        text_turn(
            '{"agent_name": "marker_analyzer", "verdict": "ll", "confidence": 0.8, '
            f'"proof_sketch": {_MARKER_ANALYZER_PROOF_SKETCH}, '
            f'"artifacts": {_MARKER_ANALYZER_ARTIFACTS}, "errors": []}}',
            input_tokens=150, output_tokens=60,
        ),
    ])

    out = runner.run_agent("marker_analyzer", {"x": 1})
    assert out is not None

    usage = runner.usage_tracker.as_dict()
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 60
    assert usage["total_tokens"] == 210
    assert usage["structured_output_calls"] == 1
    assert usage["extraction_fallback_calls"] == 0
    assert usage["per_agent"]["marker_analyzer"]["calls"] == 1
    assert usage["by_model"]["claude-sonnet-5"]["calls"] == 1


def test_usage_tracker_counts_haiku_repair_as_fallback():
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    runner.client = FakeAnthropic([
        text_turn("not valid json at all"),
        text_turn(
            '{"agent_name": "marker_analyzer", "verdict": "ll", "confidence": 0.8, '
            f'"proof_sketch": {_MARKER_ANALYZER_PROOF_SKETCH}, '
            f'"artifacts": {_MARKER_ANALYZER_ARTIFACTS}, "errors": []}}',
            model="claude-haiku-4-5",
        ),
    ])

    out = runner.run_agent("marker_analyzer", {"x": 1})
    assert out is not None

    usage = runner.usage_tracker.as_dict()
    assert usage["calls"] == 2
    assert usage["structured_output_calls"] == 1
    assert usage["extraction_fallback_calls"] == 1
    assert usage["per_agent"]["marker_analyzer"]["calls"] == 2
