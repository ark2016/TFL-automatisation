"""Claude Haiku 5.5 (``claude-haiku-5-5``) in the shared client (mock only).

Haiku 5.5 is a thinking model: adaptive thinking + an explicit effort, never
``temperature`` / ``top_p`` / ``top_k`` (400), no server-side refusal fallback,
no 64K output clamp (128K), two rate cards split at a 100K-token prompt, and a
response that may start with ``thinking`` blocks (never read ``content[0]``).
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from agent_system import config as agent_config
from agent_system.lib.llm_client import (
    LONG_PROMPT_THRESHOLD_TOKENS, MODEL_MAX_OUTPUT_TOKENS, MODEL_PRICING,
    MODEL_PRICING_LONG_PROMPT, AnthropicClient, LLMRunner, UsageTracker,
    clamp_max_tokens, estimate_cost_usd,
)
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn

HAIKU = "claude-haiku-5-5"


def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
    )


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("schema", [None, {"type": "object", "properties": {}}])
def test_request_has_adaptive_thinking_and_effort_and_no_sampling(schema):
    kw = AnthropicClient().build_request_kwargs(
        HAIKU, 4000, "sys", "u", effort="low", temperature=0.0, output_schema=schema,
    )
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "low"
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kw
    # no extra_body at all: neither a temperature nor `fallbacks`
    assert "extra_body" not in kw
    assert "extra_headers" not in kw
    assert "fallbacks" not in json.dumps(kw)
    assert kw["messages"][-1]["role"] == "user"     # no assistant prefill


def test_default_effort_is_explicit_when_none_given():
    kw = AnthropicClient(default_effort="medium").build_request_kwargs(HAIKU, 4000, "sys", "u")
    assert kw["output_config"]["effort"] == "medium"


def test_refusal_fallback_never_applies_to_haiku_5_5():
    kw = AnthropicClient(refusal_fallback=True).build_request_kwargs(HAIKU, 4000, "s", "u")
    assert "extra_body" not in kw and "extra_headers" not in kw
    # while Opus 5.x still opts in
    opus = AnthropicClient(refusal_fallback=True).build_request_kwargs("claude-opus-5-5", 4000, "s", "u")
    assert opus["extra_body"] == {"fallbacks": "default"}


def test_haiku_4_5_keeps_the_legacy_temperature_path():
    kw = AnthropicClient().build_request_kwargs("claude-haiku-4-5", 4000, "s", "u", temperature=0.2)
    assert kw["extra_body"] == {"temperature": 0.2}
    assert "thinking" not in kw


@pytest.mark.parametrize("module", ["cfl_system", "dcfl_system", "ll_system"])
def test_pipeline_runners_build_the_same_haiku_5_5_request(module):
    runner = importlib.import_module(f"{module}.orchestrator").LiveRunner(api_key="sk-ant-test-not-used")
    kw = runner._build_request_kwargs(HAIKU, 4000, 0.0, "sys", "u", effort="low")
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k", "extra_body", "extra_headers"} & set(kw)


def test_no_64k_clamp_for_haiku_5_5():
    assert HAIKU not in MODEL_MAX_OUTPUT_TOKENS
    assert clamp_max_tokens(HAIKU, 128000) == 128000
    assert clamp_max_tokens("claude-haiku-4-5", 128000) == 64000


@pytest.mark.parametrize("module", ["agent_system", "cfl_system", "dcfl_system", "ll_system"])
def test_configs_use_haiku_5_5_with_explicit_low_effort(module):
    cfg = importlib.import_module(f"{module}.config")
    assert "claude-haiku-4-5" not in getattr(cfg, "MODELS").values()
    if module == "agent_system":
        for role in ("validator", "summarizer"):
            assert cfg.MODELS[role] == HAIKU
            assert cfg.EFFORT[role] == "low"
    else:
        assert cfg.JSON_REPAIR_MODEL == HAIKU
        assert cfg.JSON_REPAIR_EFFORT == "low"


@pytest.mark.parametrize("module", ["cfl_system", "dcfl_system", "ll_system"])
def test_json_repair_call_is_haiku_5_5_low_effort_with_room_for_thinking(module):
    runner = importlib.import_module(f"{module}.orchestrator").LiveRunner(api_key="sk-ant-test-not-used")
    client = FakeAnthropic([text_turn('{"a": 1}', model=HAIKU, thinking="hmm")])
    runner.client = client
    runner._repair_json_with_haiku("agent", '{"a": 1,', False)
    kw = client.create_calls[0]
    assert kw["model"] == HAIKU
    assert kw["output_config"]["effort"] == "low"
    assert "extra_body" not in kw and "temperature" not in kw
    assert kw["max_tokens"] >= 4000     # not sized for the bare answer only


# ---------------------------------------------------------------------------
# Reading the answer by block type (a response may start with thinking)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["cfl_system", "dcfl_system", "ll_system"])
def test_json_repair_reads_text_after_a_leading_thinking_block(module):
    runner = importlib.import_module(f"{module}.orchestrator").LiveRunner(api_key="sk-ant-test-not-used")
    runner.client = FakeAnthropic([text_turn('{"fixed": true}', model=HAIKU, thinking="hmm")])
    assert runner._repair_json_with_haiku("agent", '{"fixed": true,', False) == {"fixed": True}


def test_quick_validate_reads_text_after_a_leading_thinking_block():
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    runner._client = FakeAnthropic([text_turn("Looks consistent.", model=HAIKU, thinking="step 1")])
    assert runner.quick_validate("ok?", {"a": 1}) == "Looks consistent."
    kw = runner._client.create_calls[0]
    assert kw["model"] == agent_config.MODELS["validator"] == HAIKU
    assert kw["max_tokens"] >= 1024 and kw["output_config"]["effort"] == "low"


def test_pumping_lemma_client_reads_text_by_type():
    from pumping_lemma.llm.client import LLMClient

    resp = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="answer"),
    ])
    client = LLMClient.__new__(LLMClient)
    client.model = HAIKU
    client._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    assert client.complete("p") == "answer"


# ---------------------------------------------------------------------------
# Pricing: two rate cards split at a 100K-token prompt
# ---------------------------------------------------------------------------

def test_both_rate_cards_are_registered():
    assert MODEL_PRICING[HAIKU] == {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125}
    assert MODEL_PRICING_LONG_PROMPT[HAIKU] == {
        "input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625}
    assert LONG_PROMPT_THRESHOLD_TOKENS == 100_000
    assert MODEL_PRICING["claude-haiku-4-5"]["input"] == 1.00      # old results stay priceable


def test_card_switches_above_100k_prompt_tokens():
    at = _usage(input_tokens=100_000, output_tokens=1_000_000)
    over = _usage(input_tokens=100_001, output_tokens=1_000_000)
    assert estimate_cost_usd(HAIKU, at) == pytest.approx(100_000 / 1e6 * 0.10 + 0.50)
    assert estimate_cost_usd(HAIKU, over) == pytest.approx(100_001 / 1e6 * 0.50 + 2.50)


def test_prompt_length_counts_cache_tokens():
    # 10K uncached + 95K cache read = 105K prompt -> long card for every bucket
    u = _usage(input_tokens=10_000, output_tokens=1_000_000, cache_read=95_000)
    assert estimate_cost_usd(HAIKU, u) == pytest.approx(0.01 * 0.50 + 0.095 * 0.05 + 2.50)


def test_tracker_prices_each_call_on_its_own_card():
    t = UsageTracker()
    t.record(HAIKU, _usage(input_tokens=50_000, output_tokens=10_000), agent="a")        # short card
    t.record(HAIKU, _usage(input_tokens=200_000, output_tokens=10_000), agent="a")       # long card
    expected = (0.05 * 0.10 + 0.01 * 0.50) + (0.2 * 0.50 + 0.01 * 2.50)
    d = t.as_dict()
    assert d["estimated_cost_usd"] == pytest.approx(expected)
    assert d["per_agent"]["a"]["estimated_cost_usd"] == pytest.approx(expected)
    # the dated id the API reports still resolves to both cards
    t2 = UsageTracker()
    t2.record("claude-haiku-5-5-20260101", _usage(input_tokens=200_000))
    assert t2.as_dict()["estimated_cost_usd"] == pytest.approx(0.2 * 0.50)


def test_single_card_models_are_unaffected_by_prompt_length():
    u = _usage(input_tokens=500_000, output_tokens=1_000_000)
    assert estimate_cost_usd("claude-haiku-4-5", u) == pytest.approx(0.5 * 1.00 + 5.00)
    assert estimate_cost_usd("claude-sonnet-5-5", u) == pytest.approx(0.5 * 2.00 + 10.00)
