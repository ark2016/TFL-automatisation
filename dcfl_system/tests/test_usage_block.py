"""``result["usage"]`` (TODO.md §3) -- present in every ``run_pipeline``
result (even with no live agent_runner), and consistent with the number of
calls a ``LiveRunner`` actually made through the shared ``FakeAnthropic``
test double."""

from __future__ import annotations

import json
import time as _real_time_module
from pathlib import Path

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn
from dcfl_system.orchestrator import LiveRunner, MockRunner, run_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"

_REAL_SLEEP = _real_time_module.sleep


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)


def test_usage_block_present_and_zero_without_agent_runner():
    ir = json.loads((EXAMPLES_DIR / "task_wvaavRwR.json").read_text(encoding="utf-8"))
    mock = MockRunner(str(MOCK_DIR), ir["task_id"])

    result = run_pipeline(ir, mock_runner=mock)

    assert "usage" in result
    usage = result["usage"]
    assert usage["calls"] == 0
    assert usage["total_tokens"] == 0
    assert usage["structured_output_calls"] == 0
    assert usage["extraction_fallback_calls"] == 0
    assert usage["estimated_cost_usd"] is None
    assert usage["per_agent"] == {}


def test_usage_tracker_consistent_with_calls_made():
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    runner.client = FakeAnthropic([
        text_turn('{"status": "success", "verdict": "dcfl"}',
                  input_tokens=120, output_tokens=40),
    ])

    out = runner.run_agent("stack_strategy", {"x": 1})
    assert out["status"] == "success"

    usage = runner.usage_tracker.as_dict()
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 40
    assert usage["total_tokens"] == 160
    # stack_strategy has a closed output schema (agent_output_schema.py) --
    # the one call should go out with structured outputs, not the fallback.
    assert usage["structured_output_calls"] == 1
    assert usage["extraction_fallback_calls"] == 0
    assert usage["per_agent"]["stack_strategy"]["calls"] == 1
    assert usage["by_model"]["claude-sonnet-5"]["calls"] == 1


def test_usage_tracker_counts_haiku_repair_as_fallback():
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    # First call: unparseable JSON -> triggers the Haiku repair path.
    runner.client = FakeAnthropic([
        text_turn("not valid json at all"),
        text_turn('{"status": "success", "verdict": "dcfl"}', model="claude-haiku-4-5"),
    ])

    out = runner.run_agent("stack_strategy", {"x": 1})
    assert out is not None

    usage = runner.usage_tracker.as_dict()
    # Main attempt (structured output) + Haiku repair (never structured).
    assert usage["calls"] == 2
    assert usage["structured_output_calls"] == 1
    assert usage["extraction_fallback_calls"] == 1
    assert usage["per_agent"]["stack_strategy"]["calls"] == 2
