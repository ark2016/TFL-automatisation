"""Pin the outgoing /v1/messages request built by models.make_chat_model.

Every request goes into an httpx2.MockTransport (``wire`` fixture in
conftest.py) with an unroutable base URL and the fake key "test"; nothing
reaches the network. The expected shape is the one the four pipelines send
(agent_system.lib.llm_client.AnthropicClient.build_request_kwargs).
"""

from __future__ import annotations

import asyncio
import base64

import pytest

pytest.importorskip("langchain_anthropic")

from langchain_core.messages import HumanMessage, SystemMessage

from agent_system.lib.llm_client import AnthropicClient
from tfl_deep_agent import config
from tfl_deep_agent.models import chat_model_params, make_chat_model

from .conftest import MOCK_BASE_URL, text_reply_events

PDF_B64 = base64.b64encode(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n").decode()
PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


def _invoke(llm, content="hi", system="You are a test."):
    return llm.invoke([SystemMessage(system), HumanMessage(content=content)])


# ---------------------------------------------------------------------------
# Thinking / effort / max_tokens / fallback
# ---------------------------------------------------------------------------

def test_opus_main_request_shape(wire):
    llm = make_chat_model(role="main", api_key="test")
    reply = _invoke(llm)
    req = wire.last
    assert req.method == "POST"
    assert req.url == f"{MOCK_BASE_URL}/v1/messages"
    assert req.headers["x-api-key"] == "test"
    b = req.body
    assert b["model"] == "claude-opus-5-5"
    assert b["max_tokens"] == 64000
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "high"}
    assert b["stream"] is True
    assert "temperature" not in b and "top_k" not in b and "top_p" not in b
    assert "effort" not in b and "reasoning_effort" not in b
    # server-side refusal fallback, as AnthropicClient sends it for Opus 5.x
    assert req.headers["anthropic-beta"] == FALLBACK_BETA
    assert b["fallbacks"] == "default"
    assert b["system"] == "You are a test."
    assert b["messages"] == [{"role": "user", "content": "hi"}]
    assert reply.text == "ok"


@pytest.mark.parametrize("role,effort", [("intake", "medium"), ("ir_builder", "medium"),
                                         ("coder", "medium")])
def test_sonnet_roles_have_medium_effort_and_no_fallback(wire, role, effort):
    _invoke(make_chat_model(role=role, api_key="test"))
    b, h = wire.last.body, wire.last.headers
    assert b["model"] == "claude-sonnet-5-5"
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": effort}
    assert "temperature" not in b
    assert "fallbacks" not in b
    assert "anthropic-beta" not in h


def test_haiku_55_is_a_thinking_model(wire):
    _invoke(make_chat_model("haiku", role=None, effort="low", api_key="test"))
    b = wire.last.body
    assert b["model"] == "claude-haiku-5-5"
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "low"}
    assert "temperature" not in b


def test_legacy_haiku_45_gets_temperature_no_thinking_and_clamped_max_tokens(wire):
    _invoke(make_chat_model("claude-haiku-4-5", role="lean", api_key="test"))
    b = wire.last.body
    assert b["model"] == "claude-haiku-4-5"
    assert b["max_tokens"] == 64000            # lean role asks 128000; Haiku 4.5 cap
    assert b["temperature"] == 0.0
    assert "thinking" not in b
    assert "output_config" not in b
    assert "anthropic-beta" not in wire.last.headers


def test_lean_role_max_tokens_and_explicit_overrides(wire):
    _invoke(make_chat_model(role="lean", api_key="test"))
    assert wire.last.body["max_tokens"] == 128000
    _invoke(make_chat_model("sonnet", role="prover", max_tokens=1234, effort="xhigh", api_key="test"))
    b = wire.last.body
    assert (b["model"], b["max_tokens"], b["output_config"]) == ("claude-sonnet-5-5", 1234, {"effort": "xhigh"})


def test_model_override_env_forces_every_role(wire, monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-5-5")
    for role, requested in (("main", None), ("critic", "opus"), (None, "claude-sonnet-5-5")):
        _invoke(make_chat_model(requested, role=role, api_key="test"))
        assert wire.last.body["model"] == "claude-haiku-5-5"
        assert "fallbacks" not in wire.last.body


@pytest.mark.parametrize("model,effort", [
    ("claude-opus-5-5", "high"), ("claude-sonnet-5-5", "medium"),
    ("claude-haiku-5-5", "low"), ("claude-haiku-4-5", "high"),
])
def test_body_matches_pipeline_build_request_kwargs(wire, model, effort):
    """Field for field, the wire body carries what the pipelines send."""
    expected = AnthropicClient(
        default_effort=config.DEFAULT_EFFORT, refusal_fallback=config.REFUSAL_FALLBACK,
    ).build_request_kwargs(model, 50000, "S", "U", effort=effort,
                           temperature=config.TEMPERATURE, cache_system=False)
    _invoke(make_chat_model(model, role=None, max_tokens=50000, effort=effort, api_key="test"),
            content="U", system="S")
    b, h = wire.last.body, wire.last.headers
    for key in ("model", "max_tokens", "thinking", "output_config", "system", "messages"):
        assert b.get(key) == expected.get(key), key
    for key, value in expected.get("extra_body", {}).items():
        assert b[key] == value, key
    for key, value in expected.get("extra_headers", {}).items():
        assert h[key.lower()] == value, key
    if "extra_headers" not in expected:
        assert "anthropic-beta" not in h


def test_chat_model_params_builds_no_client():
    p = chat_model_params(role="main")
    assert p["model"] == "claude-opus-5-5"
    assert p["thinking"] == {"type": "adaptive"}
    assert p["effort"] == "high"
    assert p["streaming"] is True
    assert p["model_kwargs"] == {
        "extra_headers": {"anthropic-beta": FALLBACK_BETA},
        "extra_body": {"fallbacks": "default"},
    }


# ---------------------------------------------------------------------------
# PDF and image content blocks
# ---------------------------------------------------------------------------

def _pdf_block(blocks):
    docs = [x for x in blocks if x.get("type") == "document"]
    assert len(docs) == 1, blocks
    return docs[0]


def test_pdf_document_block_standard_format(wire):
    content = [{"type": "text", "text": "Transcribe the task."},
               {"type": "file", "base64": PDF_B64, "mime_type": "application/pdf"}]
    _invoke(make_chat_model(role="intake", api_key="test"), content=content)
    blocks = wire.last.body["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "Transcribe the task."}
    assert _pdf_block(blocks)["source"] == {
        "type": "base64", "media_type": "application/pdf", "data": PDF_B64,
    }


def test_pdf_document_block_native_anthropic_format_passes_through(wire):
    native = {"type": "document",
              "source": {"type": "base64", "media_type": "application/pdf", "data": PDF_B64}}
    _invoke(make_chat_model(role="intake", api_key="test"),
            content=[native, {"type": "text", "text": "x"}])
    assert _pdf_block(wire.last.body["messages"][0]["content"]) == native


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp"])
def test_image_block_standard_format(wire, mime):
    content = [{"type": "image", "base64": PNG_B64, "mime_type": mime},
               {"type": "text", "text": "What does the picture say?"}]
    _invoke(make_chat_model(role="intake", api_key="test"), content=content)
    blocks = wire.last.body["messages"][0]["content"]
    images = [x for x in blocks if x.get("type") == "image"]
    assert images == [{"type": "image",
                       "source": {"type": "base64", "media_type": mime, "data": PNG_B64}}]


def test_image_block_native_anthropic_format_passes_through(wire):
    native = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}}
    _invoke(make_chat_model(role="intake", api_key="test"), content=[native])
    assert wire.last.body["messages"][0]["content"] == [native]


# ---------------------------------------------------------------------------
# Response side and the async path
# ---------------------------------------------------------------------------

def test_streamed_reply_parsing_thinking_and_usage(wire):
    wire.reply_events = text_reply_events(
        "answer", thinking="hmm",
        usage={"input_tokens": 100, "output_tokens": 40,
               "cache_read_input_tokens": 30, "cache_creation_input_tokens": 20},
    )
    reply = _invoke(make_chat_model(role="main", api_key="test"))
    assert reply.text == "answer"
    kinds = [b["type"] for b in reply.content if isinstance(b, dict)]
    assert kinds == ["thinking", "text"]
    um = reply.usage_metadata
    assert um["output_tokens"] == 40
    assert um["input_token_details"]["cache_read"] == 30
    assert um["input_token_details"]["cache_creation"] == 20
    # langchain counts input_tokens as prompt total (input + cache read + cache write)
    assert um["input_tokens"] == 150
    assert reply.response_metadata["stop_reason"] == "end_turn"


def test_async_path_uses_the_same_request_shape(wire):
    llm = make_chat_model(role="main", api_key="test")
    reply = asyncio.run(llm.ainvoke([HumanMessage(content=[
        {"type": "text", "text": "hi"},
        {"type": "file", "base64": PDF_B64, "mime_type": "application/pdf"},
    ])]))
    assert reply.text == "ok"
    b = wire.last.body
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "high"}
    assert b["fallbacks"] == "default"
    assert _pdf_block(b["messages"][0]["content"])["source"]["data"] == PDF_B64


# ---------------------------------------------------------------------------
# The path the agent actually uses: deepagents' graph with tools bound
# ---------------------------------------------------------------------------

def test_deep_agent_graph_keeps_the_request_shape(wire):
    """create_deep_agent(model=make_chat_model(...)) adds tools and prompt
    caching to the body; thinking / effort / refusal fallback must survive."""
    deepagents = pytest.importorskip("deepagents")
    agent = deepagents.create_deep_agent(
        model=make_chat_model(role="main", api_key="test"), system_prompt="S",
    )
    agent.invoke({"messages": [HumanMessage("hi")]})
    req = wire.last
    b = req.body
    assert b["model"] == "claude-opus-5-5"
    assert b["max_tokens"] == 64000
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "high"}
    assert b["stream"] is True
    assert "temperature" not in b
    assert req.headers["anthropic-beta"] == FALLBACK_BETA
    assert b["fallbacks"] == "default"
    # deepagents binds its built-in tools (todos / filesystem / task) ...
    names = {t["name"] for t in b["tools"]}
    assert {"read_file", "write_file", "task"} <= names, names
    # ... and turns on prompt caching: the system prompt becomes a list of text
    # blocks whose last one carries the breakpoint, plus a top-level cache_control.
    system = b["system"]
    assert isinstance(system, list) and system, system
    assert all(blk["type"] == "text" for blk in system)
    assert system[0]["text"].startswith("S")
    assert system[-1]["cache_control"]["type"] == "ephemeral"
    assert b["cache_control"]["type"] == "ephemeral"
    assert b["messages"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# chat_kwargs cannot replace the resolved request fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"model": "claude-opus-5-5"},
    {"model_name": "claude-opus-5-5"},
    {"max_tokens_to_sample": 999999},
    {"reasoning_effort": "max"},
    {"thinking": {"type": "disabled"}},
    {"output_config": {"effort": "max"}},
    {"model_kwargs": {}},
    {"betas": ["x"]},
    {"streaming": False},
    {"disable_streaming": True},
    {"top_k": 5},
    {"top_p": 0.5},
])
def test_chat_kwargs_cannot_override_resolved_fields(kwargs, monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-5-5")
    with pytest.raises(TypeError, match="would override the resolved request fields"):
        make_chat_model(role="main", api_key="test", **kwargs)


def test_named_max_tokens_is_still_resolved_and_clamped(monkeypatch):
    # max_tokens / effort / temperature are make_chat_model's own parameters,
    # so they go through config.resolve_* rather than chat_kwargs.
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-4-5")
    llm = make_chat_model(role="main", max_tokens=999999, api_key="test")
    assert llm.max_tokens == 64000
    with pytest.raises(ValueError, match="unknown effort"):
        make_chat_model(role="main", effort="extreme", api_key="test")


def test_override_holds_and_harmless_chat_kwargs_pass_through(wire, monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-5-5")
    llm = make_chat_model("claude-opus-5-5", role="main", api_key="test",
                          max_retries=0, default_request_timeout=5)
    assert llm.max_retries == 0
    _invoke(llm)
    b = wire.last.body
    assert b["model"] == "claude-haiku-5-5"
    assert b["max_tokens"] == 64000
    assert "fallbacks" not in b


def test_unpriced_model_is_refused_before_a_client_is_built():
    with pytest.raises(config.UnknownModelError):
        make_chat_model("claude-opus-4-1", role="main", api_key="test")
