"""Unit tests for ``UsageTracker`` (agent_system/lib/llm_client.py): per-model / per-agent aggregation, structured-output vs fallback call
counts, and cost estimation -- exercised directly (no API/FakeAnthropic
needed) since :meth:`UsageTracker.record` takes plain values."""

from __future__ import annotations

from types import SimpleNamespace

from agent_system.lib.llm_client import UsageTracker


def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0):
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_creation,
    )


def test_empty_tracker():
    d = UsageTracker().as_dict()
    assert d["calls"] == 0
    assert d["total_tokens"] == 0
    assert d["estimated_cost_usd"] is None
    assert d["by_model"] == {}
    assert d["per_agent"] == {}
    assert d["structured_output_calls"] == 0
    assert d["extraction_fallback_calls"] == 0


def test_record_none_usage_is_a_noop():
    t = UsageTracker()
    t.record("claude-sonnet-5-5", None, agent="foo", used_structured_output=True)
    d = t.as_dict()
    assert d["calls"] == 0
    assert "foo" not in d["per_agent"]


def test_record_accumulates_by_model_and_agent():
    t = UsageTracker()
    t.record("claude-sonnet-5-5", _usage(100, 50), agent="reasoning",
              used_structured_output=True)
    t.record("claude-sonnet-5-5", _usage(200, 80), agent="reasoning",
              used_structured_output=True)
    t.record("claude-haiku-4-5", _usage(30, 10), agent="repair",
              used_structured_output=False)

    d = t.as_dict()
    assert d["calls"] == 3
    assert d["input_tokens"] == 330
    assert d["output_tokens"] == 140
    assert d["total_tokens"] == 470

    assert d["by_model"]["claude-sonnet-5-5"]["calls"] == 2
    assert d["by_model"]["claude-haiku-4-5"]["calls"] == 1

    assert d["per_agent"]["reasoning"]["calls"] == 2
    assert d["per_agent"]["reasoning"]["total_tokens"] == 430
    assert d["per_agent"]["repair"]["calls"] == 1

    assert d["structured_output_calls"] == 2
    assert d["extraction_fallback_calls"] == 1


def test_record_without_agent_falls_under_unknown():
    t = UsageTracker()
    t.record("claude-sonnet-5-5", _usage(10, 5))
    d = t.as_dict()
    assert d["per_agent"]["unknown"]["calls"] == 1


def test_cost_known_only_when_every_used_model_is_priced():
    t = UsageTracker()
    t.record("claude-haiku-4-5", _usage(1_000_000, 0))  # priced: $1.00
    d = t.as_dict()
    assert d["estimated_cost_usd"] == 1.0

    t.record("claude-some-unpriced-model", _usage(1_000_000, 0))
    d = t.as_dict()
    assert d["estimated_cost_usd"] is None


def test_summary_line_mentions_calls_and_tokens():
    t = UsageTracker()
    t.record("claude-haiku-4-5", _usage(100, 50), used_structured_output=True)
    line = t.summary_line()
    assert "calls=1" in line
    assert "total_tokens=150" in line
