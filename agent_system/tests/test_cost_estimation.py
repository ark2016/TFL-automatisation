"""``MODEL_PRICING`` / ``estimate_cost_usd`` (TODO.md §3 M item 2).

Pricing (USD per 1M tokens, 2026-06-24 snapshot -- update by hand when
prices change): claude-haiku-4-5 1.00 / 5.00, claude-sonnet-5 2.00 / 10.00,
claude-opus-5-5 4.00 / 20.00; cache reads ~0.1x input (Opus 5.5 is the
documented exception at a flat $0.20/MTok, not 0.1x its own $4.00 input
rate), cache writes ~1.25x input for all three.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent_system.lib.llm_client import (
    AnthropicClient, MODEL_PRICING, UsageTracker, estimate_cost_usd,
)
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn


def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    from types import SimpleNamespace
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
    )


# ---------------------------------------------------------------------------
# Pricing table itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model,expected", [
    ("claude-haiku-4-5", {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25}),
    ("claude-sonnet-5", {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50}),
    ("claude-opus-5-5", {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}),
])
def test_model_pricing_table_matches_the_2026_06_24_snapshot(model, expected):
    assert MODEL_PRICING[model] == expected


# ---------------------------------------------------------------------------
# estimate_cost_usd -- pure math
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model,expected_per_call_usd", [
    # 1M input + 1M output + 1M cache_read + 1M cache_write tokens ->
    # input + output + cache_read + cache_write price, summed.
    ("claude-haiku-4-5", 1.00 + 5.00 + 0.10 + 1.25),
    ("claude-sonnet-5", 2.00 + 10.00 + 0.20 + 2.50),
    ("claude-opus-5-5", 4.00 + 20.00 + 0.20 + 5.00),
])
def test_estimate_cost_usd_known_models(model, expected_per_call_usd):
    usage = _usage(input_tokens=1_000_000, output_tokens=1_000_000,
                    cache_read=1_000_000, cache_write=1_000_000)
    cost = estimate_cost_usd(model, usage)
    assert cost == pytest.approx(expected_per_call_usd)


def test_estimate_cost_usd_scales_linearly_with_tokens():
    usage = _usage(input_tokens=500_000, output_tokens=250_000)
    cost = estimate_cost_usd("claude-sonnet-5", usage)
    assert cost == pytest.approx(0.5 * 2.00 + 0.25 * 10.00)


@pytest.mark.parametrize("dated_model,base_model", [
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ("claude-sonnet-5-20260115", "claude-sonnet-5"),
    ("claude-opus-5-5-20260301", "claude-opus-5-5"),
])
def test_estimate_cost_usd_normalizes_a_dated_snapshot_id(dated_model, base_model):
    """The live API's response names the exact dated snapshot it ran
    (``final_msg.model``), not the bare alias the request asked for --
    without normalization this silently priced every real live call at
    `None` forever (`MODEL_PRICING` only has the bare aliases)."""
    usage = _usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost_usd(dated_model, usage) == estimate_cost_usd(base_model, usage)
    assert estimate_cost_usd(dated_model, usage) is not None


def test_estimate_cost_usd_unknown_model_is_none():
    usage = _usage(input_tokens=1000, output_tokens=1000)
    assert estimate_cost_usd("claude-opus-4-8", usage) is None
    assert estimate_cost_usd("totally-made-up-model", usage) is None


def test_estimate_cost_usd_none_usage_is_none():
    assert estimate_cost_usd("claude-sonnet-5", None) is None


def test_estimate_cost_usd_unconfirmed_bucket_price_is_none_not_a_guess():
    """A model with a `None` price for a bucket that's actually non-zero
    must return `None` for the whole estimate -- never silently price it
    at 0 or fabricate a number."""
    partial_pricing = {"input": 2.00, "output": 10.00, "cache_read": None, "cache_write": None}
    original = dict(MODEL_PRICING)
    MODEL_PRICING["_test_partial_model"] = partial_pricing
    try:
        # Only confirmed buckets used -> a real number.
        assert estimate_cost_usd(
            "_test_partial_model", _usage(input_tokens=1_000_000, output_tokens=1_000_000),
        ) == pytest.approx(12.00)
        # A cache_read bucket that's actually used but unpriced -> None.
        assert estimate_cost_usd(
            "_test_partial_model",
            _usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read=1),
        ) is None
    finally:
        MODEL_PRICING.clear()
        MODEL_PRICING.update(original)


# ---------------------------------------------------------------------------
# UsageTracker.as_dict()["estimated_cost_usd"] -- the aggregated form
# ---------------------------------------------------------------------------

def test_usage_tracker_estimated_cost_usd_sums_across_calls():
    tracker = UsageTracker()
    tracker.record("claude-haiku-4-5", _usage(input_tokens=1_000_000, output_tokens=1_000_000))
    tracker.record("claude-sonnet-5", _usage(input_tokens=1_000_000, output_tokens=1_000_000))

    d = tracker.as_dict()
    assert d["estimated_cost_usd"] == pytest.approx((1.00 + 5.00) + (2.00 + 10.00))
    assert d["by_model"]["claude-haiku-4-5"]["estimated_cost_usd"] == pytest.approx(1.00 + 5.00)
    assert d["by_model"]["claude-sonnet-5"]["estimated_cost_usd"] == pytest.approx(2.00 + 10.00)


def test_usage_tracker_estimated_cost_usd_none_when_any_model_unpriced():
    tracker = UsageTracker()
    tracker.record("claude-sonnet-5", _usage(input_tokens=1000, output_tokens=1000))
    tracker.record("claude-opus-4-8", _usage(input_tokens=1000, output_tokens=1000))  # unpriced

    d = tracker.as_dict()
    assert d["estimated_cost_usd"] is None
    # ...but the priced model's own by_model entry still gets a number.
    assert d["by_model"]["claude-sonnet-5"]["estimated_cost_usd"] is not None
    assert d["by_model"]["claude-opus-4-8"]["estimated_cost_usd"] is None


# ---------------------------------------------------------------------------
# End-to-end through FakeAnthropic -> AnthropicClient.call -> UsageTracker
# ---------------------------------------------------------------------------

def test_cache_aware_cost_flows_through_a_real_call():
    """A call whose usage includes cache_read/cache_creation tokens (a
    prompt-caching hit/write) prices those buckets too, not just plain
    input/output -- exercised through the same `AnthropicClient.call` /
    `FakeAnthropic` path every pipeline's LiveRunner/LLMRunner uses."""
    tracker = UsageTracker()
    client = AnthropicClient(default_effort="high", refusal_fallback=False, usage_tracker=tracker)
    fake = FakeAnthropic([
        text_turn(
            '{"status": "success"}',
            model="claude-haiku-4-5",
            input_tokens=1000, output_tokens=200,
            cache_read_input_tokens=500_000, cache_creation_input_tokens=100_000,
        ),
    ])

    client.call(fake, model="claude-haiku-4-5", max_tokens=1000, system="sys", user="u")

    d = tracker.as_dict()
    expected = (
        1000 / 1_000_000 * 1.00
        + 200 / 1_000_000 * 5.00
        + 500_000 / 1_000_000 * 0.10
        + 100_000 / 1_000_000 * 1.25
    )
    assert d["estimated_cost_usd"] == pytest.approx(expected)
    assert d["cache_read_input_tokens"] == 500_000
    assert d["cache_creation_input_tokens"] == 100_000
