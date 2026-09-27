"""Shared ``FakeAnthropic`` test double for run_agent / LLMRunner tests
(TODO.md §5 M).

Every pipeline's live agent runner --
``agent_system.lib.llm_client.LLMRunner`` and
``{cfl,dcfl,ll}_system.orchestrator.LiveRunner`` (thin wrappers around the
same ``agent_system.lib.llm_client.AnthropicClient``) -- talks to an
``anthropic.Anthropic``-shaped client through exactly two call sites:

- ``client.messages.stream(**kwargs)`` -- a context manager, used for every
  "real" agent call (main attempt and JSON-retry attempts alike);
- ``client.messages.create(**kwargs)`` -- a plain call, used only by the
  Haiku JSON-repair helper in cfl/dcfl/ll's ``LiveRunner``
  (``agent_system``'s ``LLMRunner`` has no Haiku-repair step -- its retry
  re-asks the *same* model via ``.stream()``).

``FakeAnthropic`` is a scripted stand-in for both: build it from a list of
turns (``text_turn`` / ``refusal_turn`` / ``raises_turn`` /
``stream_breaks_turn``) and it hands one out per call to ``.stream()`` /
``.create()``, in the order given, recording every call's kwargs for
assertions.

Install it *inside a test* (e.g. via plain attribute assignment on
``runner._client`` / ``runner.client``, matching the existing
``test_llm_request_params.py`` / ``test_request_params.py`` pattern) --
never at import/module level, so the suite stays compatible with the root
``conftest.py`` live-call blocker (it replaces ``anthropic.Anthropic``
itself for the whole session; this module never touches that class).
"""

from __future__ import annotations

import dataclasses
import threading
import time
from types import SimpleNamespace
from typing import Any


# ---------------------------------------------------------------------------
# Turns -- one per call to .stream() / .create()
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Turn:
    kind: str  # "message" | "raises"
    text: str = ""
    stop_reason: str = "end_turn"
    model: str = "claude-sonnet-5"
    thinking: str | None = None
    refusal_category: str | None = None
    refusal_explanation: str | None = None
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    exc: Exception | None = None
    # Raise `exc` only once the caller reads the response (get_final_message
    # / text_stream / .create()'s return), not when .stream() is invoked --
    # simulates a stream that opens fine and then breaks mid-response.
    break_on_read: bool = False
    # Optional artificial delay (seconds), held while "in flight" (i.e.
    # while a concurrency semaphore around the call would still be held) --
    # for tests that assert on bounded concurrency.
    delay: float = 0.0


def text_turn(
    text: str,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5",
    thinking: str | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    delay: float = 0.0,
) -> _Turn:
    """A normal (or truncated) successful response.

    Pass ``thinking="..."`` to emit a ``thinking`` content block before the
    text block (adaptive-thinking models put reasoning there --
    ``content[0]`` is not necessarily the answer, see
    ``llm_client._extract_answer_text``). Pass ``stop_reason="max_tokens"``
    for a response truncated mid-JSON. ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` feed ``usage`` for cost-estimation
    tests (``estimate_cost_usd`` / ``UsageTracker`` -- TODO.md §3 M's
    pricing table); both default to 0 (no caching), matching every other
    existing test that doesn't care about them.
    """
    return _Turn(
        kind="message", text=text, stop_reason=stop_reason, model=model,
        thinking=thinking, input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        delay=delay,
    )


def refusal_turn(
    *, category: str | None = "bio", explanation: str | None = None,
    model: str = "claude-sonnet-5",
) -> _Turn:
    """A server-side safety decline (``stop_reason == "refusal"``)."""
    return _Turn(
        kind="message", stop_reason="refusal", model=model,
        refusal_category=category, refusal_explanation=explanation,
    )


def raises_turn(exc: Exception, *, delay: float = 0.0) -> _Turn:
    """The call to ``.stream()`` / ``.create()`` itself raises ``exc`` --
    e.g. a fatal 4xx (never retried, see :func:`fatal_error`) or a
    transient error meant to be consumed by
    ``AnthropicClient.call``'s own retry/backoff loop (see
    :func:`retryable_error`)."""
    return _Turn(kind="raises", exc=exc, delay=delay)


def stream_breaks_turn(exc: Exception, *, delay: float = 0.0) -> _Turn:
    """``.stream()`` opens fine (the context manager enters), but reading
    the response -- ``get_final_message()`` / ``text_stream`` /
    ``.create()``'s return for a repair call -- raises ``exc``: a
    connection that drops mid-response rather than failing to connect."""
    return _Turn(kind="raises", exc=exc, break_on_read=True, delay=delay)


# ---------------------------------------------------------------------------
# Convenience builders for real anthropic exception types
# ---------------------------------------------------------------------------

_FATAL_STATUS_TO_CLS: dict[int, str] = {
    400: "BadRequestError",
    401: "AuthenticationError",
    403: "PermissionDeniedError",
    404: "NotFoundError",
}


def fatal_error(status_code: int = 400, message: str = "boom") -> Exception:
    """A real ``anthropic.<Error>`` the SDK would raise for `status_code`
    (400/401/403/404 -- classified ``FatalAPIError``, never retried)."""
    import anthropic
    import httpx

    cls_name = _FATAL_STATUS_TO_CLS.get(status_code)
    if cls_name is None:
        raise ValueError(f"not a fatal status code: {status_code}")
    cls = getattr(anthropic, cls_name)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request)
    return cls(message, response=response, body=None)


def retryable_error(message: str = "connection reset") -> Exception:
    """A plain exception classified ``RetryableAPIError`` -- anything that
    is not one of the four fatal SDK exception types (network hiccup,
    overloaded, a broken stream, ...) is retried by
    ``AnthropicClient.call``'s own backoff loop."""
    return RuntimeError(message)


# ---------------------------------------------------------------------------
# FakeAnthropic
# ---------------------------------------------------------------------------

def _build_message(turn: _Turn) -> SimpleNamespace:
    content: list[SimpleNamespace] = []
    if turn.thinking is not None:
        content.append(SimpleNamespace(type="thinking", thinking=turn.thinking))
    stop_details = None
    if turn.stop_reason == "refusal":
        stop_details = SimpleNamespace(
            category=turn.refusal_category, explanation=turn.refusal_explanation,
        )
    else:
        content.append(SimpleNamespace(type="text", text=turn.text))
    usage = SimpleNamespace(
        input_tokens=turn.input_tokens, output_tokens=turn.output_tokens,
        cache_read_input_tokens=turn.cache_read_input_tokens,
        cache_creation_input_tokens=turn.cache_creation_input_tokens,
    )
    return SimpleNamespace(
        content=content, stop_reason=turn.stop_reason, model=turn.model,
        usage=usage, stop_details=stop_details,
    )


class _FakeStream:
    """Context manager returned by ``FakeAnthropic.stream()``."""

    def __init__(self, turn: _Turn, on_exit):
        self._turn = turn
        self._on_exit = on_exit

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._on_exit()
        return False

    def get_final_message(self):
        if self._turn.delay:
            time.sleep(self._turn.delay)
        if self._turn.kind == "raises" and self._turn.break_on_read:
            raise self._turn.exc
        return _build_message(self._turn)

    @property
    def text_stream(self):
        msg = _build_message(self._turn)
        return iter(
            block.text for block in msg.content
            if getattr(block, "type", None) == "text"
        )


class FakeAnthropic:
    """Scripted drop-in for ``anthropic.Anthropic`` (TODO.md §5 M).

    ``turns`` is consumed, in order, one per ``.stream()`` / ``.create()``
    call -- both call sites share the same queue, matching how a single
    ``run_agent`` invocation can make several real calls in sequence (main
    attempt, JSON-retry attempt, Haiku-repair attempt). Raises
    ``AssertionError`` if a test's script runs out before the code under
    test stops calling -- a scripted test failing loud beats it silently
    reusing the last turn and hiding a bug.

    ``stream_calls`` / ``create_calls`` record each call's kwargs (for
    assertions on model/effort/messages sent). ``peak_concurrent_calls`` is
    the highest number of ``.stream()``/``.create()`` calls this instance
    ever had open at once -- combine with a ``delay=`` on a turn to assert
    a concurrency semaphore actually bounds parallel calls.
    """

    def __init__(self, turns: list[_Turn]):
        self._turns = list(turns)
        self._i = 0
        self._lock = threading.Lock()
        self.stream_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.concurrent_calls = 0
        self.peak_concurrent_calls = 0
        # `client.messages.stream(...)` / `client.messages.create(...)`
        self.messages = self

    def _pop(self) -> _Turn:
        with self._lock:
            if self._i >= len(self._turns):
                raise AssertionError(
                    f"FakeAnthropic: no scripted turn left for call #{self._i + 1} "
                    f"({len(self._turns)} turn(s) scripted)"
                )
            turn = self._turns[self._i]
            self._i += 1
            return turn

    def _enter(self) -> None:
        with self._lock:
            self.concurrent_calls += 1
            self.peak_concurrent_calls = max(
                self.peak_concurrent_calls, self.concurrent_calls,
            )

    def _exit(self) -> None:
        with self._lock:
            self.concurrent_calls -= 1

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        turn = self._pop()
        self._enter()
        if turn.kind == "raises" and not turn.break_on_read:
            self._exit()
            if turn.delay:
                time.sleep(turn.delay)
            raise turn.exc
        return _FakeStream(turn, self._exit)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        turn = self._pop()
        self._enter()
        try:
            if turn.delay:
                time.sleep(turn.delay)
            if turn.kind == "raises":
                raise turn.exc
            return _build_message(turn)
        finally:
            self._exit()
