"""``result["usage"]`` (TODO.md §3) -- present in every ``graph.run_pipeline``
result (even with no live agent_runner), and consistent with the number of
calls an ``LLMRunner`` actually made through the shared ``FakeAnthropic``
test double."""

from __future__ import annotations

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.graph import run_pipeline
from agent_system.lib.llm_client import LLMRunner
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)


_SIMPLE_IR = {
    "task_id": "usage-test",
    "task_type": "membership",
    "source_text": "L = {a^n b^n}",
}


def test_usage_block_present_and_zero_without_agent_runner():
    result = run_pipeline(_SIMPLE_IR, mock_runner=None, agent_runner=None, verbose=False)

    assert "usage" in result
    usage = result["usage"]
    assert usage["calls"] == 0
    assert usage["total_tokens"] == 0
    assert usage["structured_output_calls"] == 0
    assert usage["extraction_fallback_calls"] == 0
    assert usage["estimated_cost_usd"] is None
    assert usage["per_agent"] == {}


def test_usage_tracker_consistent_with_calls_made():
    # `pumping` (-> `pumping_agent`) has a real `_FIELD_SCHEMAS` entry
    # (agent_output_schema.py) -- unlike `closure` (-> `closure_agent`),
    # which is `_DYNAMIC_KEY_EXEMPT` (its `details` field is keyed by a
    # homomorphism's own alphabet symbols for 2 of its 4 methods, which
    # `output_config.format` can't express) and so never gets structured
    # outputs at all.
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    runner._client = FakeAnthropic([
        text_turn(
            '{"module": "pumping_agent", "status": "success", "confidence": 0.8, '
            '"proof": {"word_choice": {"word": "a^n b^n", "word_parameterized": true, '
            '"parameter": "n", "membership_argument": "..."}, "length_argument": "...", '
            '"cut_analysis": {"method": "...", "argument": "...", "cases": []}, '
            '"pump_value": 1, "conclusion": "..."}, "errors": []}',
            input_tokens=90, output_tokens=30,
        ),
    ])

    out = runner.run_agent("pumping", {"x": 1})
    assert out["status"] == "success"

    usage = runner.usage_tracker.as_dict()
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 90
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 120
    assert usage["structured_output_calls"] == 1
    assert usage["extraction_fallback_calls"] == 0
    assert usage["per_agent"]["pumping"]["calls"] == 1
    assert usage["by_model"]["claude-sonnet-5-5"]["calls"] == 1


def test_usage_tracker_counts_retry_as_fallback_when_no_schema():
    # `input_parser` has no fixed output contract (agent_output_schema.py's
    # `_NO_FIXED_CONTRACT`) -- both calls (main + JSON-retry) go out without
    # structured outputs, exercising the legacy "extract JSON from prose"
    # retry path.
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    runner._client = FakeAnthropic([
        text_turn("not valid json at all"),
        text_turn('{"module": "input_parser", "status": "success", "ir": {}}'),
    ])

    out = runner.run_agent("input_parser", {"x": 1})
    assert out is not None

    usage = runner.usage_tracker.as_dict()
    assert usage["calls"] == 2
    assert usage["structured_output_calls"] == 0
    assert usage["extraction_fallback_calls"] == 2
    assert usage["per_agent"]["input_parser"]["calls"] == 2
