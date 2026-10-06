"""LLMRunner request building for current models: adaptive thinking + effort
instead of temperature, streaming, text read by block type, refusal handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from agent_system.config import EFFORT, MODELS
from agent_system.lib import llm_client
from agent_system.lib.llm_client import LLMRunner, _is_adaptive_model


def _api_error(cls, status_code: int):
    """Build a real anthropic.<cls> the way the SDK would raise it, for a
    FakeAnthropic-style ``side_effect`` on ``runner._client.messages.stream``."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request)
    return cls("boom", response=response, body=None)


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
    ("claude-sonnet-5-5", True),
    ("claude-sonnet-5", True),  # legacy, still available
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


def test_request_params_with_output_schema(runner):
    """output_schema (TODO.md §3 M) adds output_config.format on both
    adaptive and legacy models, without disturbing the rest of the kwargs."""
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    kw = runner._shared.build_request_kwargs(
        "claude-opus-5-5", 64000, "sys", "u", effort="high", output_schema=schema,
    )
    assert kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": schema}

    kw = runner._shared.build_request_kwargs(
        "claude-haiku-4-5", 256, "sys", "u", effort="low", temperature=0.0, output_schema=schema,
    )
    assert kw["temperature"] == 0.0
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": schema}}


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
    # Structured outputs (TODO.md §3 M): classifier has a closed contract,
    # so every call also carries output_config.format alongside effort.
    assert kwargs["output_config"]["effort"] == EFFORT["classifier"]
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kwargs


def test_refusal_returns_agent_error_with_one_call(runner):
    """A safety-classifier decline (stop_reason=refusal) becomes an
    agent_error dict, not a silent None (TODO.md §2) -- and run_agent must
    not issue a second "please output valid JSON" call, since a refusal is
    not a parsing problem."""
    message = SimpleNamespace(
        stop_reason="refusal", content=[],
        stop_details=SimpleNamespace(category="bio", explanation=None),
    )
    _mock_stream(runner, message)

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "agent_error"
    assert out["module"] == "classifier"
    assert "bio" in out["errors"][0]
    assert runner._client.messages.stream.call_count == 1


def test_api_error_returns_agent_error_after_retries_exhausted(runner, monkeypatch):
    """A transient API error (network, overloaded, ...) is retried by the
    shared client's backoff loop (TODO.md §3) and, once that budget is
    exhausted, becomes an agent_error dict recorded in state["errors"] by
    graph.py -- instead of None disappearing silently (TODO.md §2) -- and
    is not retried with a JSON-only instruction, since the problem isn't
    the JSON."""
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)
    runner._client = MagicMock()
    runner._client.messages.stream.side_effect = RuntimeError("connection reset")

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "agent_error"
    assert "connection reset" in out["errors"][0]
    # The shared client's backoff loop retries a non-fatal error up to its
    # max_retries budget (default 3) before giving up -- exactly one logical
    # run_agent call, but several underlying stream() attempts.
    assert runner._client.messages.stream.call_count == 3


@pytest.mark.parametrize("exc_cls, status", [
    (anthropic.AuthenticationError, 401),
    (anthropic.PermissionDeniedError, 403),
    (anthropic.NotFoundError, 404),
])
def test_fatal_api_errors_propagate_without_retry(runner, exc_cls, status):
    """A dead key / no access / unknown model can't be fixed by retrying or
    JSON-repairing -- run_agent must let it propagate so the pipeline fails
    fast instead of silently degrading (TODO.md §2)."""
    runner._client = MagicMock()
    runner._client.messages.stream.side_effect = _api_error(exc_cls, status)

    with pytest.raises(exc_cls):
        runner.run_agent("classifier", {"language": "a*"})

    assert runner._client.messages.stream.call_count == 1


def test_student_notes_appear_only_once(runner):
    """student_notes must not be duplicated in both the system prompt and
    the user JSON (TODO.md §3) -- kept only in the system prompt's labelled
    section, stripped from the serialized input_data."""
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"verdict": "regular"}')],
    )
    _mock_stream(runner, message)

    note = "I think this language is regular because..."
    runner.run_agent("classifier", {"language": "a*", "student_notes": note})

    kwargs = runner._client.messages.stream.call_args.kwargs
    user_content = kwargs["messages"][0]["content"]
    assert kwargs["system"].count(note) == 1
    assert note not in user_content
    assert "student_notes" not in user_content


def test_student_notes_nested_in_ir_appear_only_once(runner):
    """The IR dict embedded in the input carries student_notes too; that
    copy must be stripped from the user JSON as well."""
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"verdict": "regular"}')],
    )
    _mock_stream(runner, message)

    note = "maybe the language is context-free"
    runner.run_agent("classifier", {"ir": {"language_spec": {}, "student_notes": note}})

    kwargs = runner._client.messages.stream.call_args.kwargs
    user_content = kwargs["messages"][0]["content"]
    assert kwargs["system"].count(note) == 1
    assert note not in user_content
    assert "language_spec" in user_content


def test_model_override_env(monkeypatch, runner):
    monkeypatch.setenv("TFL_MODEL_DEEP", "claude-opus-5-5")
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-4-5")
    assert runner._get_model("reasoning_agent") == "claude-haiku-4-5"
    assert runner._get_model("classifier") == "claude-haiku-4-5"


def test_sonnet_5_5_request_params(runner):
    """Sonnet 5.5 (claude-sonnet-5-5) is an adaptive-thinking model: adaptive
    thinking + explicit effort (+ structured-output format), no temperature,
    no budget_tokens, no server-side refusal fallback (Opus/Fable only)."""
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    kw = runner._shared.build_request_kwargs(
        "claude-sonnet-5-5", 64000, "sys", "u", effort="medium", temperature=0.0, output_schema=schema,
    )
    assert kw["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in kw["thinking"]
    assert kw["output_config"] == {
        "effort": "medium", "format": {"type": "json_schema", "schema": schema},
    }
    assert "temperature" not in kw
    assert "extra_body" not in kw and "extra_headers" not in kw
    assert kw["model"] == "claude-sonnet-5-5"


def test_sonnet_5_5_is_not_legacy_and_priced():
    from agent_system.lib.llm_client import MODEL_PRICING
    assert _is_adaptive_model("claude-sonnet-5-5")
    assert _is_adaptive_model("claude-sonnet-5-5-20260928")
    assert MODEL_PRICING["claude-sonnet-5-5"]["input"] == 2.00
    assert MODEL_PRICING["claude-sonnet-5-5"]["output"] == 10.00
    assert "claude-sonnet-5" in MODEL_PRICING  # legacy entry kept


def test_default_stack_uses_sonnet_5_5_everywhere():
    """All four projects moved from Sonnet 5 to Sonnet 5.5 together."""
    import importlib
    for pkg in ("agent_system", "cfl_system", "dcfl_system", "ll_system"):
        models = importlib.import_module(f"{pkg}.config").MODELS
        assert "claude-sonnet-5" not in models.values(), pkg
        assert "claude-sonnet-5-5" in models.values(), pkg
