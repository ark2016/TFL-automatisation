"""LiveRunner request building for current models: adaptive thinking + effort
instead of temperature, server-side refusal fallback, refusal handling."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dcfl_system.config import EFFORT, MODELS
from dcfl_system.orchestrator import LiveRunner


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


# ---------------------------------------------------------------------------
# Haiku repair after max_tokens (root TODO.md §2): the repaired output must
# be flagged as untrustworthy, not returned as a normal verdict.
# ---------------------------------------------------------------------------

def test_haiku_repair_after_max_tokens_is_flagged_inconclusive(runner):
    """The specialist stream hits max_tokens with unparseable partial JSON;
    Haiku's repair call returns valid-looking JSON with a confident verdict.
    The repaired result must still be marked `_truncated`/`_repaired`,
    downgraded to status `inconclusive`, and capped at confidence <= 0.40 —
    it must NOT pass through the specialist's own high confidence."""
    truncated_final = SimpleNamespace(
        stop_reason="max_tokens", model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.text_stream = iter(['{"agent_name": "stack_strategy", "status": "succ'])  # cut off
    stream.get_final_message.return_value = truncated_final

    repair_response = SimpleNamespace(content=[
        SimpleNamespace(text=json.dumps({
            "agent_name": "stack_strategy", "status": "success",
            "verdict": "dcfl", "confidence": 0.9, "proof_sketch": {}, "errors": [],
        }))
    ])

    runner.client = MagicMock()
    runner.client.messages.stream.return_value = stream
    runner.client.messages.create.return_value = repair_response

    out = runner.run_agent("stack_strategy", {"x": 1})

    assert out["_truncated"] is True
    assert out["_repaired"] is True
    assert out["status"] == "inconclusive"
    assert out["confidence"] <= 0.40
    # the repair call did receive the truncation note in the user message
    repair_kwargs = runner.client.messages.create.call_args.kwargs
    user_content = repair_kwargs["messages"][0]["content"]
    assert "truncated" in user_content.lower()


def test_haiku_repair_without_truncation_is_not_flagged(runner):
    """A JSON parse failure that is NOT a max_tokens truncation (e.g. the
    model wrapped valid JSON in prose) still goes through Haiku repair, but
    must not get the truncation penalty — only an actual max_tokens cutoff
    is untrustworthy in the way this flag means."""
    final = SimpleNamespace(
        stop_reason="end_turn", model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.text_stream = iter(['Sure, here you go:\n{"agent_name": "stack_strategy", "status": "success", "verdict": "dcfl", "confidence": 0.9, "proof_sketch": {}, "errors": []}\nHope that helps!'])
    stream.get_final_message.return_value = final

    runner.client = MagicMock()
    runner.client.messages.stream.return_value = stream

    out = runner.run_agent("stack_strategy", {"x": 1})

    # _extract_json should already recover the embedded object without
    # needing Haiku repair at all, and it must be untouched by the
    # truncation flags.
    assert "_truncated" not in out
    assert "_repaired" not in out
    assert out["status"] == "success"
    assert out["confidence"] == 0.9
