"""pytest configuration for tfl_deep_agent tests.

The root ``conftest.py`` strips ``ANTHROPIC_API_KEY`` and replaces
``anthropic.Anthropic`` / ``anthropic.AsyncAnthropic`` with a stub whose
``messages.create`` / ``messages.stream`` raise. langchain-anthropic does not
use those two names: ``ChatAnthropic._client`` calls ``anthropic.Client(...)``
and ``_async_client`` calls ``anthropic.AsyncClient(...)``, which are separate
module attributes (aliases bound at import time), so the root stub alone does
NOT cover it. ``guard.py`` points the two aliases at the same stub for the
session, and strips the key again before every test (code that ran earlier in
the process, e.g. ``LLMRunner.__init__`` -> ``llm_client._load_env()``, may
have copied it back from the root ``.env``). ``test_network_guard.py``
proves both.

``TFL_ALLOW_LIVE=1`` disables them, as it does the root fixture.

Request-shape tests that must see a real request on the wire use the
``wire`` fixture below: it swaps in the real SDK classes for one test, with an
``httpx2.MockTransport`` and an unroutable base URL, so nothing can leave the
machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from .guard import (  # noqa: F401  (autouse fixtures)
    _block_langchain_anthropic_clients,
    _no_live_anthropic_key,
)


# ---------------------------------------------------------------------------
# Wire capture for request-shape tests
# ---------------------------------------------------------------------------

MOCK_BASE_URL = "http://anthropic.mock.invalid"


def sse_body(events: list[dict[str, Any]]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def text_reply_events(
    text: str = "ok", *, model: str = "claude-opus-5-5", thinking: str | None = None,
    usage: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """A minimal streamed ``/v1/messages`` reply (optionally with a thinking
    block first). The final usage travels in ``message_delta``, as on the API."""
    usage = usage or {"input_tokens": 12, "output_tokens": 7,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    events: list[dict[str, Any]] = [{
        "type": "message_start",
        "message": {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 1}},
    }]
    idx = 0
    if thinking is not None:
        events += [
            {"type": "content_block_start", "index": idx,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": idx,
             "delta": {"type": "thinking_delta", "thinking": thinking}},
            {"type": "content_block_delta", "index": idx,
             "delta": {"type": "signature_delta", "signature": "sig"}},
            {"type": "content_block_stop", "index": idx},
        ]
        idx += 1
    events += [
        {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": idx},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": usage},
        {"type": "message_stop"},
    ]
    return events


@dataclass
class CapturedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class Wire:
    """Recorded requests plus the reply the mock transport sends back."""

    requests: list[CapturedRequest] = field(default_factory=list)
    reply_events: list[dict[str, Any]] = field(default_factory=text_reply_events)

    @property
    def last(self) -> CapturedRequest:
        assert self.requests, "no request reached the mock transport"
        return self.requests[-1]

    def handle(self, request):
        import httpx2

        self.requests.append(CapturedRequest(
            method=request.method,
            url=str(request.url),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=json.loads(request.content or b"{}"),
        ))
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"},
            text=sse_body(self.reply_events),
        )


@pytest.fixture
def wire(monkeypatch):
    """Route ChatAnthropic through the REAL anthropic SDK client classes into an
    ``httpx2.MockTransport``. ``MOCK_BASE_URL`` is unroutable (``.invalid``),
    so even a missed transport patch could not reach api.anthropic.com. Build
    the chat model inside the test, after this fixture has run."""
    pytest.importorskip("langchain_anthropic")
    import anthropic
    import anthropic._client as real
    import httpx2
    import langchain_anthropic.chat_models as cm

    w = Wire()
    transport = httpx2.MockTransport(w.handle)

    async def _ahandle(request):
        return w.handle(request)

    async_transport = httpx2.MockTransport(_ahandle)

    def _sync_http(**kw):
        kw.pop("anthropic_proxy", None)
        kw["base_url"] = MOCK_BASE_URL
        return httpx2.Client(transport=transport, **kw)

    def _async_http(**kw):
        kw.pop("anthropic_proxy", None)
        kw["base_url"] = MOCK_BASE_URL
        return httpx2.AsyncClient(transport=async_transport, **kw)

    # ChatAnthropic reads its base URL at construction; the SDK client then
    # stamps it on every request (the httpx2 client's base_url is not used).
    monkeypatch.setenv("ANTHROPIC_API_URL", MOCK_BASE_URL)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("LANGSMITH_GATEWAY", raising=False)
    monkeypatch.setattr(anthropic, "Client", real.Anthropic)
    monkeypatch.setattr(anthropic, "AsyncClient", real.AsyncAnthropic)
    monkeypatch.setattr(cm, "_get_default_httpx_client", _sync_http)
    monkeypatch.setattr(cm, "_get_default_async_httpx_client", _async_http)
    return w
