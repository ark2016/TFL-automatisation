"""LiveRunner request building for current models: adaptive thinking + effort
instead of temperature, server-side refusal fallback, refusal handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cfl_system.config import EFFORT, MODELS
from cfl_system.orchestrator import LiveRunner


@pytest.fixture
def runner():
    return LiveRunner(api_key="sk-ant-test-not-used")


@pytest.mark.parametrize("model, adaptive", [
    ("claude-opus-5-5", True),
    ("claude-sonnet-5", True),
    ("claude-opus-4-7", True),
    ("claude-sonnet-4-6", True),
    ("claude-haiku-4-5", False),
    ("claude-haiku-4-5-20251001", False),
    ("claude-opus-4-5", False),
    ("claude-sonnet-4-20250514", False),
])
def test_is_adaptive_model(model, adaptive):
    assert LiveRunner._is_adaptive_model(model) is adaptive


def test_every_agent_has_effort():
    assert set(EFFORT) == set(MODELS)


def test_opus_request_uses_effort_and_fallback(runner):
    kw = runner._build_request_kwargs("claude-opus-5-5", 64000, 0.2, "sys", "u", effort="high")
    assert "temperature" not in kw
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    assert kw["extra_body"] == {"fallbacks": "default"}
    assert kw["extra_headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"


def test_sonnet_request_has_no_fallback(runner):
    kw = runner._build_request_kwargs("claude-sonnet-5", 64000, 0.0, "sys", "u", effort="medium")
    assert kw["output_config"] == {"effort": "medium"}
    assert "extra_body" not in kw and "temperature" not in kw


def test_haiku_request_keeps_temperature(runner):
    kw = runner._build_request_kwargs("claude-haiku-4-5", 800, 0.0, "sys", "u")
    assert kw["temperature"] == 0.0
    assert "thinking" not in kw and "output_config" not in kw


def test_refusal_returns_agent_error_without_retry(runner):
    final = SimpleNamespace(
        stop_reason="refusal", model="claude-opus-5-5", usage=None,
        stop_details=SimpleNamespace(category="bio", explanation=None),
    )
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.text_stream = iter([])
    stream.get_final_message.return_value = final
    runner.client = MagicMock()
    runner.client.messages.stream.return_value = stream

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert "refusal" in out["errors"][0] and "bio" in out["errors"][0]
    assert runner.client.messages.stream.call_count == 1
    runner.client.messages.create.assert_not_called()  # no Haiku repair


def test_model_override_env_forces_haiku(monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-4-5")
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    final = SimpleNamespace(stop_reason="end_turn", model="claude-haiku-4-5", usage=None)
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.text_stream = iter(['{"status": "success"}'])
    stream.get_final_message.return_value = final
    runner.client = MagicMock()
    runner.client.messages.stream.return_value = stream

    assert runner.run_agent("classifier", {}) == {"status": "success"}

    kwargs = runner.client.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    # Legacy models still get `temperature`; `output_config` is present too
    # now, but only carries `format` (structured outputs, TODO.md §3 M) —
    # classifier has a closed contract, no `effort` key belongs on Haiku.
    assert "temperature" in kwargs
    assert "effort" not in kwargs["output_config"]
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
