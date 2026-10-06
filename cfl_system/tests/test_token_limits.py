"""Output-token ceilings: cfg_builder and the Haiku JSON-repair call."""

from __future__ import annotations

from cfl_system import config
from cfl_system.orchestrator import LiveRunner


class _Messages:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("Block", (), {"text": '{"a": 1}'})()
        return type("Response", (), {
            "content": [block],
            "usage": type("Usage", (), {"input_tokens": 1, "output_tokens": 1})(),
        })()


class _Client:
    def __init__(self):
        self.messages = _Messages()


def test_cfg_builder_has_the_model_maximum_output_budget():
    assert config.MAX_TOKENS_PER_AGENT["cfg_builder"] == 128000
    assert config.MAX_TOKENS == 64000  # everyone else keeps the default


def test_json_repair_budget_covers_a_large_truncated_output():
    assert config.JSON_REPAIR_MAX_TOKENS == 32000


def test_repair_call_uses_the_large_budget_and_an_explicit_timeout():
    runner = LiveRunner(api_key="fake-key-for-unit-test")
    runner.client = _Client()
    huge = "{" + '"x": "' + "y" * 200_000 + '"'
    assert runner._repair_json_with_haiku("cfg_builder", huge, was_truncated=True) == {"a": 1}
    (call,) = runner.client.messages.calls
    assert call["max_tokens"] == config.JSON_REPAIR_MAX_TOKENS
    # an explicit timeout is what lets the SDK accept a non-streaming call
    # with max_tokens > ~21333
    assert call["timeout"] == config.JSON_REPAIR_TIMEOUT_S


def test_repair_budget_still_scales_down_for_short_inputs():
    runner = LiveRunner(api_key="fake-key-for-unit-test")
    runner.client = _Client()
    runner._repair_json_with_haiku("cfg_builder", '{"a": 1,}', was_truncated=False)
    assert runner.client.messages.calls[0]["max_tokens"] < 3000
