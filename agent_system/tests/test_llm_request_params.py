"""LLMRunner request building for current models: adaptive thinking + effort
instead of temperature, streaming, text read by block type, refusal handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_system.config import EFFORT, MODELS
from agent_system.lib.llm_client import LLMRunner, _is_adaptive_model


@pytest.fixture
def runner():
    return LLMRunner(api_key="sk-ant-test-not-used")


def _mock_stream(runner, message):
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.get_final_message.return_value = message
    runner._client = MagicMock()
    runner._client.messages.stream.return_value = stream


@pytest.mark.parametrize("model, adaptive", [
    ("claude-opus-5-5", True),
    ("claude-sonnet-5", True),
    ("claude-haiku-4-5", False),
    ("claude-opus-4-5", False),
])
def test_is_adaptive_model(model, adaptive):
    assert _is_adaptive_model(model) is adaptive


def test_every_llm_agent_has_effort():
    llm_agents = {a for a, m in MODELS.items() if "haiku" not in m}
    assert llm_agents <= set(EFFORT)


def test_request_params(runner):
    kw = runner._build_request_kwargs("claude-opus-5-5", 64000, "sys", "u", effort="high")
    assert "temperature" not in kw
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    assert kw["extra_body"] == {"fallbacks": "default"}

    kw = runner._build_request_kwargs("claude-haiku-4-5", 256, "sys", "u", effort="low", temperature=0.0)
    assert kw["temperature"] == 0.0 and "output_config" not in kw


def test_json_read_past_thinking_block(runner):
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text='{"verdict": "regular", "confidence": 0.9}'),
        ],
    )
    _mock_stream(runner, message)

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["evidence"]["verdict"] == "regular"
    kwargs = runner._client.messages.stream.call_args.kwargs
    assert kwargs["output_config"] == {"effort": EFFORT["classifier"]}
    assert "temperature" not in kwargs


def test_refusal_returns_none(runner):
    message = SimpleNamespace(
        stop_reason="refusal", content=[],
        stop_details=SimpleNamespace(category="bio", explanation=None),
    )
    _mock_stream(runner, message)

    assert runner.run_agent("classifier", {"language": "a*"}) is None


def test_model_override_env(monkeypatch, runner):
    monkeypatch.setenv("TFL_MODEL_DEEP", "claude-opus-5-5")
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-4-5")
    assert runner._get_model("reasoning_agent") == "claude-haiku-4-5"
    assert runner._get_model("classifier") == "claude-haiku-4-5"
