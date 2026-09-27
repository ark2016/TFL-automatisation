"""
LLM client — Anthropic API wrapper for TFL Agent System.

Reads system prompts from prompts/, sends structured requests to Claude,
and parses JSON responses. Model config lives in config.py.

This module also hosts the *shared* Anthropic call machinery used by every
pipeline (TODO.md §3): request-kwargs building (adaptive thinking + effort,
temperature only for legacy models, server-side refusal fallback), a
streamed call with typed retry/backoff, a process-wide concurrency
semaphore, and usage/cost tracking. ``cfl_system`` / ``dcfl_system`` /
``ll_system``'s ``orchestrator.LiveRunner`` are thin wrappers around
``AnthropicClient`` — see their ``run_agent`` for how prompts/JSON
parsing/Haiku-repair stay pipeline-specific while the API call itself goes
through this one place.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import anthropic

# Import config (with fallback defaults if missing)
try:
    from agent_system.config import (
        MODELS as _CFG_MODELS, MAX_TOKENS as _CFG_MAX_TOKENS, TEMPERATURE as _CFG_TEMP,
        EFFORT as _CFG_EFFORT, DEFAULT_EFFORT as _CFG_DEFAULT_EFFORT,
        REFUSAL_FALLBACK as _CFG_REFUSAL_FALLBACK,
    )
except ImportError:
    _CFG_MODELS = {}
    _CFG_MAX_TOKENS = 64000
    _CFG_TEMP = 0.0
    _CFG_EFFORT = {}
    _CFG_DEFAULT_EFFORT = "high"
    _CFG_REFUSAL_FALLBACK = True

# Legacy models — Haiku 4.5 and anything before the 4.6 family — take
# sampling parameters and have no adaptive thinking / effort. Opus/Sonnet
# 4.6+ and every 5.x model run adaptive thinking steered by `effort`, and
# Opus 4.7+, Sonnet 5 and Opus 5.x reject `temperature` with a 400.
_LEGACY_MODEL_RE = re.compile(r"^claude-3|haiku|-4(-[015])?(-\d{8})?$")
# Models that get the server-side refusal fallback (see REFUSAL_FALLBACK).
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5")


def _is_adaptive_model(model: str) -> bool:
    """Whether `model` runs adaptive thinking (and rejects `temperature`)."""
    return bool(model) and not _LEGACY_MODEL_RE.search(model)


def _response_text(message: Any) -> str:
    """Concatenate the text blocks of a response.

    With adaptive thinking the response may start with `thinking` blocks, so
    content[0] is not necessarily the answer — read blocks by type.
    """
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


def _extract_answer_text(final_msg: Any, stream: Any = None) -> str:
    """Read the answer text of a completed streamed message, by block type.

    Adaptive-thinking models may emit `thinking` blocks before the answer —
    ``content[0]`` is not necessarily it — so this reads ``final_msg.content``
    and keeps only ``type == "text"`` blocks (mirrors ``_response_text``).
    Falls back to draining ``stream.text_stream`` (which already yields only
    text deltas) when `final_msg` carries no usable ``content`` — covers a
    test double built around ``text_stream`` instead of a full message.
    """
    content = getattr(final_msg, "content", None) or []
    text = "".join(
        getattr(block, "text", "") for block in content
        if getattr(block, "type", None) == "text"
    )
    if text:
        return text
    if stream is not None:
        try:
            return "".join(stream.text_stream)
        except TypeError:
            return ""
    return ""


# Prompt file name → agent name (some have _agent suffix in file)
_PROMPT_ALIASES: dict[str, str] = {
    "pumping": "pumping_agent",
    "nerode": "nerode_agent",
    "closure": "closure_agent",
    "reasoning": "reasoning_agent",
    "grammar": "grammar_analyzer",
}


def _load_env() -> None:
    """Load .env file from agent_system/ root if it exists."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class _AgentAPIError(Exception):
    """Non-fatal Anthropic API error (network hiccup, overloaded, 5xx, ...).

    Wrapped into an ``agent_error`` dict by ``run_agent``. Never retried
    with the "your JSON was invalid" prompt -- the problem isn't the JSON,
    a second call would just hit the same error again.
    """


class _AgentRefusal(Exception):
    """The model declined the request (``stop_reason == "refusal"``).

    Wrapped into an ``agent_error`` dict. ``run_agent`` does not attempt a
    second call after this -- a "please output only JSON" retry cannot
    un-refuse a safety decline.
    """

    def __init__(self, category: str | None, explanation: str | None = None) -> None:
        self.category = category
        self.explanation = explanation
        msg = f"model refused the request (category={category})"
        if explanation:
            msg += f": {explanation}"
        super().__init__(msg)


def build_agent_output_schema(required_keys: Any) -> dict[str, Any]:
    """Build the ``output_config.format`` JSON schema for one agent's
    ``AgentOutput`` envelope (TODO.md §3 M).

    ``required_keys`` is the *exhaustive* set of top-level keys the agent's
    own prompt contract ("## Output Format") declares — every one of them
    becomes a required property with an unconstrained (``{}``) subschema,
    so nested proof/evidence content stays completely free-form: only the
    envelope shape is fixed, not what goes inside it. The schema closes the
    object (``additionalProperties: False``) because the API requires
    every object schema to set it — see the ``claude-api`` skill's
    structured-outputs notes ("additionalProperties: false required for
    all objects; anything else is not supported"). A **closed** schema
    constrains generation, so ``required_keys`` must be exhaustive: leaving
    out a key the prompt actually uses would silently make the model drop
    it, not just fail validation.
    """
    keys = sorted(set(required_keys))
    return {
        "type": "object",
        "properties": {key: {} for key in keys},
        "required": keys,
        "additionalProperties": False,
    }


# 400s that mean "the API rejected output_config itself" (schema too
# complex, a schema-shaped mistake, or — future-proofing — a model/platform
# that does not support structured outputs) rather than some unrelated bad
# request (malformed IR, expired key, ...). Matched case-insensitively
# against the exception's own message; conservative on purpose — a
# genuinely unrelated 400 must still propagate as a real FatalAPIError
# instead of being silently swallowed as a "just retry without the schema"
# case (TODO.md §3 M).
_SCHEMA_REJECTION_MARKERS = (
    "output_config", "json_schema", "additionalproperties", "output format",
    "structured output",
)


def _looks_like_schema_rejection(exc: "FatalAPIError") -> bool:
    """Whether `exc` looks like the API rejecting ``output_config.format``
    itself, not an unrelated 400 (dead key, bad IR, ...). Only a 400 is
    ever a candidate — 401/403/404 are never about the request body."""
    if exc.status_code != 400:
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _SCHEMA_REJECTION_MARKERS)


# Per-process memory of which models have already had their structured-output
# request rejected (TODO.md §3 M): the untested assumption that an untyped
# `{}` subschema is accepted by every model this codebase uses means the
# FIRST call to a model that turns out not to support it always pays for one
# doomed 400 (schema attached, rejected, then :meth:`AnthropicClient.call`
# retries once without it) — every call after that would pay it again unless
# something remembers. This does, for the rest of the process.
_schema_rejected_models: set[str] = set()
_schema_rejected_models_lock = threading.Lock()


def _model_schema_known_rejected(model: str) -> bool:
    with _schema_rejected_models_lock:
        return model in _schema_rejected_models


def _remember_schema_rejected(model: str) -> None:
    with _schema_rejected_models_lock:
        _schema_rejected_models.add(model)


def _extract_json(text: str) -> dict | None:
    """Extract first JSON object from LLM response text (back-compat wrapper)."""
    parsed, _ = extract_json_with_error(text)
    return parsed


def extract_json(text: str) -> dict | None:
    """Extract first JSON object from LLM response text.

    Handles raw JSON, JSON inside ```json ... ``` fences, and JSON embedded
    in prose (first-brace to last-brace).
    """
    parsed, _ = extract_json_with_error(text)
    return parsed


def extract_json_with_error(text: str) -> tuple[dict | None, str | None]:
    """Extract a JSON object and return ``(parsed, error_detail)``.

    Shared by every pipeline's LiveRunner/LLMRunner (TODO.md §3 — the four
    copies had drifted: dcfl's retry error was a bare ``length=N`` with no
    position, unlike cfl/ll). ``error_detail`` is ``None`` on success,
    otherwise a message with the parse position and ±40 characters of
    context, meant to be fed back to the model on a JSON retry. Tries three
    strategies in order:
      1) whole text as JSON
      2) content of a ```json fenced block
      3) substring between first '{' and last '}'
    """
    text = text.strip()
    if not text:
        return None, "response was empty"

    last_err: json.JSONDecodeError | None = None
    last_strategy: str = ""
    fence_match = None
    first_brace = -1
    last_brace = -1

    # Strategy 1: whole text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
        return None, f"parsed as JSON but top-level is {type(obj).__name__}, not object"
    except json.JSONDecodeError as e:
        last_err, last_strategy = e, "whole text"

    # Strategy 2: fenced code block
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj, None
            return None, f"fenced block parsed but top-level is {type(obj).__name__}"
        except json.JSONDecodeError as e:
            last_err, last_strategy = e, "fenced block"

    # Strategy 3: first-brace to last-brace
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        substring = text[first_brace:last_brace + 1]
        try:
            obj = json.loads(substring)
            if isinstance(obj, dict):
                return obj, None
            return None, f"brace-substring parsed but top-level is {type(obj).__name__}"
        except json.JSONDecodeError as e:
            last_err, last_strategy = e, "brace substring"

    if last_err is not None:
        msg = last_err.msg
        line = last_err.lineno
        col = last_err.colno
        pos = last_err.pos

        # Show ~40 chars around the failure point to help the LLM locate it
        src = text
        if last_strategy == "fenced block" and fence_match:
            src = fence_match.group(1)
        elif last_strategy == "brace substring" and first_brace != -1:
            src = text[first_brace:last_brace + 1]

        start = max(0, pos - 40)
        end = min(len(src), pos + 40)
        context = src[start:end].replace("\n", "\\n")
        pointer_offset = pos - start
        pointer = " " * pointer_offset + "^"

        return None, (
            f"{msg} at line {line} column {col} (char {pos}) "
            f"— tried strategy: {last_strategy}. "
            f"Context around failure:\n  {context}\n  {pointer}"
        )

    return None, "no JSON object found in response (no '{' / '}' delimiters)"


# ---------------------------------------------------------------------------
# Typed API errors (TODO.md §2/§3)
# ---------------------------------------------------------------------------

class FatalAPIError(Exception):
    """Non-retryable API error: 400/401/403/404 (BadRequest / Authentication /
    PermissionDenied / NotFound). A malformed request, a dead/invalid key,
    no access to the model, or an unknown model ID — retrying or
    JSON-repairing the (nonexistent) response cannot help.

    ``original`` carries the underlying ``anthropic.APIStatusError`` (or
    whatever the SDK raised) so a caller that needs the exact original type
    for backward compatibility can ``raise exc.original`` instead.
    """

    def __init__(self, message: str, *, status_code: int | None = None,
                 original: Exception | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.original = original


class RetryableAPIError(Exception):
    """Retryable API error: 429, 529/overloaded, other 5xx,
    ``anthropic.APIConnectionError``, or a stream that broke mid-response.

    Raised by :meth:`AnthropicClient.call` only after its own backoff/retry
    budget is exhausted — callers turn it into a per-agent ``agent_error``
    dict instead of crashing the pipeline.
    """

    def __init__(self, message: str, *, status_code: int | None = None,
                 original: Exception | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.original = original


_FATAL_EXCEPTION_TYPES = (
    anthropic.BadRequestError,        # 400
    anthropic.AuthenticationError,    # 401
    anthropic.PermissionDeniedError,  # 403
    anthropic.NotFoundError,          # 404
)


def _classify_api_exception(exc: Exception) -> FatalAPIError | RetryableAPIError:
    """Classify an exception raised while calling the Anthropic API.

    Fatal (400/401/403/404): retrying cannot help — a caller that wants the
    original exception's own type back can ``raise classified.original``.
    Everything else (429, 5xx incl. 529/overloaded, ``APIConnectionError``,
    a stream that broke mid-response, or any other unexpected error) is
    retryable.
    """
    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, _FATAL_EXCEPTION_TYPES):
        return FatalAPIError(str(exc), status_code=status_code, original=exc)
    return RetryableAPIError(str(exc), status_code=status_code, original=exc)


# ---------------------------------------------------------------------------
# Concurrency (shared across every pipeline's runner in this process)
# ---------------------------------------------------------------------------

def _resolve_max_concurrency() -> int:
    raw = os.environ.get("TFL_MAX_CONCURRENCY", "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return 4


_concurrency_semaphore: threading.Semaphore | None = None
_concurrency_lock = threading.Lock()


def get_concurrency_semaphore() -> threading.Semaphore:
    """Process-wide semaphore bounding concurrent Anthropic calls across
    every pipeline's LiveRunner/LLMRunner (``TFL_MAX_CONCURRENCY``, default
    4 — LangGraph's ``Send`` fan-out can otherwise dispatch every specialist
    agent's API call at once). Lazily created so a caller that sets the env
    var before the first call still sees it take effect.
    """
    global _concurrency_semaphore
    if _concurrency_semaphore is None:
        with _concurrency_lock:
            if _concurrency_semaphore is None:
                _concurrency_semaphore = threading.Semaphore(_resolve_max_concurrency())
    return _concurrency_semaphore


def _sleep_with_backoff(attempt: int, base: float = 1.0, cap: float = 20.0) -> None:
    """Exponential backoff with jitter before retry ``attempt`` (1-based)."""
    delay = min(base * (2 ** (attempt - 1)), cap)
    delay += random.uniform(0, delay * 0.25)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Pricing & usage tracking (TODO.md §3)
# ---------------------------------------------------------------------------

# USD per million tokens. UPDATE THIS TABLE BY HAND when prices change —
# nothing here is fetched live. ``None`` means "not confirmed — don't guess"
# (docs/TFL_LAB or a pricing-page check should fill it in, not a re-guess).
# Known values below are the first-party Anthropic API rates as of the
# 2026-06-24 pricing snapshot.
MODEL_PRICING: dict[str, dict[str, float | None]] = {
    "claude-opus-5-5":  {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": None},
    "claude-sonnet-5":  {"input": 2.00, "output": 10.00, "cache_read": None, "cache_write": None},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00,  "cache_read": None, "cache_write": None},
}


def estimate_cost_usd(model: str, usage: Any) -> float | None:
    """Estimate the USD cost of `usage` (an SDK ``Usage`` object, or
    anything duck-typed the same way, e.g. :class:`UsageTotals`) using
    :data:`MODEL_PRICING`. Returns ``None`` when the model is unknown, or
    when a token bucket that was actually used (non-zero) has no confirmed
    price — never fabricates a number for missing pricing."""
    if usage is None:
        return None
    prices = MODEL_PRICING.get(model)
    if not prices:
        return None
    buckets = (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_write"),
    )
    total = 0.0
    for attr, price_key in buckets:
        n = getattr(usage, attr, 0) or 0
        if not n:
            continue
        price = prices.get(price_key)
        if price is None:
            return None
        total += n / 1_000_000 * price
    return total


@dataclasses.dataclass
class UsageTotals:
    """Accumulated token usage for one model within a :class:`UsageTracker`."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0


def _totals_to_dict(model: str, t: "UsageTotals") -> dict:
    return {
        "calls": t.calls,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "cache_read_input_tokens": t.cache_read_input_tokens,
        "cache_creation_input_tokens": t.cache_creation_input_tokens,
        "estimated_cost_usd": estimate_cost_usd(model, t),
    }


class UsageTracker:
    """Accumulates token usage (and, where :data:`MODEL_PRICING` has
    confirmed prices, estimated cost) across every LLM call made by one
    pipeline run — feeds the ``usage`` block of the result JSON and the
    ``--verbose`` summary line (TODO.md §3).

    Also tracks, per agent name, the same token/cost breakdown
    (``per_agent`` in :meth:`as_dict`), and how many calls actually went
    out with structured outputs (``output_config.format``) versus fell
    back to the legacy "extract JSON from prose" / Haiku-repair path
    (``structured_output_calls`` / ``extraction_fallback_calls``) — a
    caller passes ``used_structured_output=True/False`` to :meth:`record`
    when it knows which; ``None`` (the default) means "not applicable /
    not tracked for this call" and is not counted either way.

    Thread-safe: LangGraph's ``Send`` fan-out can run several specialist
    agents' calls concurrently, so :meth:`record` takes a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: dict[str, UsageTotals] = {}
        self._by_agent: dict[str, dict[str, UsageTotals]] = {}
        self._structured_output_calls = 0
        self._extraction_fallback_calls = 0

    def record(
        self, model: str | None, usage: Any, *,
        agent: str | None = None, used_structured_output: bool | None = None,
    ) -> None:
        """Add one call's `usage` (an SDK ``Usage`` object) to the totals
        for `model` (and, when given, for `agent`). A no-op when `usage`
        is ``None`` (e.g. a call that errored before any usage was
        billed). ``used_structured_output`` (TODO.md §3): pass ``True``/
        ``False`` when the caller knows whether this call actually used
        structured outputs, to feed ``structured_output_calls`` /
        ``extraction_fallback_calls``; leave it ``None`` (default) for a
        call where that distinction does not apply (e.g. the quick
        validator)."""
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        model_key = model or "unknown"
        agent_key = agent or "unknown"
        with self._lock:
            totals = self._by_model.setdefault(model_key, UsageTotals())
            totals.input_tokens += input_tokens
            totals.output_tokens += output_tokens
            totals.cache_read_input_tokens += cache_read
            totals.cache_creation_input_tokens += cache_creation
            totals.calls += 1

            agent_totals = self._by_agent.setdefault(agent_key, {}).setdefault(
                model_key, UsageTotals(),
            )
            agent_totals.input_tokens += input_tokens
            agent_totals.output_tokens += output_tokens
            agent_totals.cache_read_input_tokens += cache_read
            agent_totals.cache_creation_input_tokens += cache_creation
            agent_totals.calls += 1

            if used_structured_output is True:
                self._structured_output_calls += 1
            elif used_structured_output is False:
                self._extraction_fallback_calls += 1

    def as_dict(self) -> dict:
        """JSON-serializable summary — the pipeline result's ``usage`` block.

        Additive over the original shape: ``by_model``/``total_tokens``/
        ``estimated_cost_usd`` are unchanged; ``calls``, the four raw
        token buckets, ``per_agent`` and the structured-output counters
        are new top-level fields.
        """
        with self._lock:
            snapshot = {model: dataclasses.replace(t) for model, t in self._by_model.items()}
            agent_snapshot = {
                agent: {model: dataclasses.replace(t) for model, t in models.items()}
                for agent, models in self._by_agent.items()
            }
            structured_output_calls = self._structured_output_calls
            extraction_fallback_calls = self._extraction_fallback_calls

        by_model: dict[str, dict] = {}
        input_tokens = output_tokens = 0
        cache_read_input_tokens = cache_creation_input_tokens = 0
        total_calls = 0
        total_tokens = 0
        total_cost = 0.0
        cost_known = bool(snapshot)
        for model, t in snapshot.items():
            by_model[model] = _totals_to_dict(model, t)
            input_tokens += t.input_tokens
            output_tokens += t.output_tokens
            cache_read_input_tokens += t.cache_read_input_tokens
            cache_creation_input_tokens += t.cache_creation_input_tokens
            total_calls += t.calls
            total_tokens += t.input_tokens + t.output_tokens
            cost = by_model[model]["estimated_cost_usd"]
            if cost is None:
                cost_known = False
            else:
                total_cost += cost

        per_agent: dict[str, dict] = {}
        for agent, models in agent_snapshot.items():
            agent_by_model: dict[str, dict] = {}
            agent_calls = 0
            agent_total_tokens = 0
            agent_cost = 0.0
            agent_cost_known = bool(models)
            for model, t in models.items():
                agent_by_model[model] = _totals_to_dict(model, t)
                agent_calls += t.calls
                agent_total_tokens += t.input_tokens + t.output_tokens
                cost = agent_by_model[model]["estimated_cost_usd"]
                if cost is None:
                    agent_cost_known = False
                else:
                    agent_cost += cost
            per_agent[agent] = {
                "calls": agent_calls,
                "total_tokens": agent_total_tokens,
                "estimated_cost_usd": round(agent_cost, 6) if agent_cost_known else None,
                "by_model": agent_by_model,
            }

        return {
            "by_model": by_model,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 6) if cost_known else None,
            "calls": total_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "per_agent": per_agent,
            "structured_output_calls": structured_output_calls,
            "extraction_fallback_calls": extraction_fallback_calls,
        }

    def summary_line(self) -> str:
        """One-line summary for ``--verbose`` CLI output."""
        d = self.as_dict()
        cost = d["estimated_cost_usd"]
        cost_str = (
            f"${cost:.4f}" if cost is not None
            else "unknown (pricing not confirmed for one or more models)"
        )
        so_total = d["structured_output_calls"] + d["extraction_fallback_calls"]
        so_str = (
            f" structured_output={d['structured_output_calls']}/{so_total}"
            if so_total else ""
        )
        return (
            f"[usage] calls={d['calls']} total_tokens={d['total_tokens']} "
            f"estimated_cost≈{cost_str}{so_str}"
        )


# ---------------------------------------------------------------------------
# Shared Anthropic call machinery (TODO.md §3)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CallResult:
    """The outcome of one :meth:`AnthropicClient.call`."""

    text: str
    stop_reason: str | None
    model: str | None
    usage: Any
    refusal_category: str | None = None
    refusal_explanation: str | None = None
    # Whether this call actually went out with output_config.format set —
    # False either because the caller passed no schema, or because the
    # request was rejected with it and this is the post-fallback retry
    # (TODO.md §3 M). Callers that want to skip legacy prose-extraction
    # when it's safe to trust the text as pure JSON can check this instead
    # of re-deriving it.
    used_structured_output: bool = False


class AnthropicClient:
    """Shared call machinery for every pipeline's LiveRunner/LLMRunner.

    One place for: request-kwargs building (adaptive thinking + effort;
    `temperature` only for legacy models; server-side refusal fallback
    headers), a streamed call that reads the answer by content-block type,
    `stop_reason` handling, typed retry/backoff on
    :class:`RetryableAPIError` (3 attempts, jittered exponential — fatal
    errors are never retried), the process-wide concurrency semaphore, and
    usage tracking.

    This class does not own an ``anthropic.Anthropic`` client itself — the
    caller passes one to :meth:`call` on every invocation, so a runner can
    swap ``self.client`` (as the test doubles do) and have it take effect
    immediately.
    """

    def __init__(
        self,
        *,
        default_effort: str = "high",
        refusal_fallback: bool = True,
        max_retries: int = 3,
        usage_tracker: UsageTracker | None = None,
        backoff_base: float = 1.0,
        backoff_cap: float = 20.0,
    ) -> None:
        self.default_effort = default_effort
        self.refusal_fallback = refusal_fallback
        self.max_retries = max_retries
        self.usage_tracker = usage_tracker if usage_tracker is not None else UsageTracker()
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    @staticmethod
    def is_adaptive_model(model: str) -> bool:
        return _is_adaptive_model(model)

    def build_request_kwargs(
        self, model: str, max_tokens: int, system: str, user: str,
        *, effort: str | None = None, temperature: float | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build kwargs for ``messages.stream`` / ``messages.create``.

        Thinking models get adaptive thinking + an explicit effort (Opus 5.5
        would silently default to "medium"); legacy models get `temperature`.
        ``output_schema`` (TODO.md §3 M), when given, requests structured
        outputs (``output_config.format``) instead of the "extract JSON from
        prose" heuristic — verified against the Anthropic Python SDK /
        ``claude-api`` skill: GA, no beta header, works on every model this
        codebase uses (Opus 5.5, Sonnet 5, Haiku 4.5).
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        output_format = (
            {"format": {"type": "json_schema", "schema": output_schema}}
            if output_schema is not None else None
        )
        if self.is_adaptive_model(model):
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effort or self.default_effort}
            if output_format is not None:
                kwargs["output_config"].update(output_format)
            if self.refusal_fallback and model.startswith(_FALLBACK_MODEL_PREFIXES):
                kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
                kwargs["extra_body"] = {"fallbacks": "default"}
        else:
            kwargs["temperature"] = 0.0 if temperature is None else temperature
            if output_format is not None:
                kwargs["output_config"] = output_format
        return kwargs

    def call(
        self, client: Any, *, model: str, max_tokens: int, system: str, user: str,
        effort: str | None = None, temperature: float | None = None,
        output_schema: dict[str, Any] | None = None, agent: str | None = None,
    ) -> CallResult:
        """Make one logical (possibly retried) streamed call through `client`.

        ``output_schema`` (TODO.md §3 M): when given, the request asks for
        structured outputs (``output_config.format``) so the response is
        guaranteed-valid JSON instead of relying on prose extraction. If
        `model` already had a schema rejected earlier THIS PROCESS
        (:func:`_model_schema_known_rejected`), the schema is dropped up
        front — no point paying for a doomed request again. Otherwise, if
        the API rejects the request specifically because of
        ``output_config`` (:func:`_looks_like_schema_rejection` — e.g. a
        model/platform that doesn't support it), this transparently retries
        once *without* the schema — structured outputs unavailable falls
        back to the legacy extraction path, it does not become a hard
        failure — and remembers it for `model` so later calls skip straight
        to the fallback. Any other fatal error (or a schema-rejection retry
        that fails again) propagates as usual.

        Raises:
            FatalAPIError: 400/401/403/404 — never retried (past the one
                schema-rejection fallback above). ``.original`` carries the
                underlying SDK exception.
            RetryableAPIError: every attempt (``self.max_retries``, default
                3, exponential backoff + jitter) hit a retryable error.
        """
        if output_schema is not None and _model_schema_known_rejected(model):
            output_schema = None
        try:
            return self._call_once(
                client, model=model, max_tokens=max_tokens, system=system, user=user,
                effort=effort, temperature=temperature, output_schema=output_schema,
                agent=agent,
            )
        except FatalAPIError as exc:
            if output_schema is not None and _looks_like_schema_rejection(exc):
                _remember_schema_rejected(model)
                return self._call_once(
                    client, model=model, max_tokens=max_tokens, system=system, user=user,
                    effort=effort, temperature=temperature, output_schema=None,
                    agent=agent,
                )
            raise

    def _call_once(
        self, client: Any, *, model: str, max_tokens: int, system: str, user: str,
        effort: str | None = None, temperature: float | None = None,
        output_schema: dict[str, Any] | None = None, agent: str | None = None,
    ) -> CallResult:
        """One logical (possibly retried-on-transient-error) streamed call —
        no structured-output fallback here, :meth:`call` owns that."""
        kwargs = self.build_request_kwargs(
            model, max_tokens, system, user, effort=effort, temperature=temperature,
            output_schema=output_schema,
        )
        classified: FatalAPIError | RetryableAPIError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with get_concurrency_semaphore():
                    with client.messages.stream(**kwargs) as stream:
                        final_msg = stream.get_final_message()
                        text = _extract_answer_text(final_msg, stream)
            except Exception as exc:  # noqa: BLE001 — classified immediately below
                classified = _classify_api_exception(exc)
                if isinstance(classified, FatalAPIError):
                    raise classified from exc
                if attempt >= self.max_retries:
                    raise classified from exc
                _sleep_with_backoff(attempt, self.backoff_base, self.backoff_cap)
                continue

            usage = getattr(final_msg, "usage", None)
            result_model = getattr(final_msg, "model", None) or model
            self.usage_tracker.record(
                result_model, usage, agent=agent,
                used_structured_output=output_schema is not None,
            )

            stop_reason = getattr(final_msg, "stop_reason", None)
            refusal_category = refusal_explanation = None
            if stop_reason == "refusal":
                details = getattr(final_msg, "stop_details", None)
                refusal_category = getattr(details, "category", None) if details else None
                refusal_explanation = getattr(details, "explanation", None) if details else None

            return CallResult(
                text=text, stop_reason=stop_reason, model=result_model, usage=usage,
                refusal_category=refusal_category, refusal_explanation=refusal_explanation,
                used_structured_output=output_schema is not None,
            )

        # Unreachable (the loop always returns or raises), but keeps type
        # checkers happy and fails loudly instead of returning None.
        raise classified if classified is not None else RetryableAPIError("exhausted retries")


class LLMRunner:
    """Run LLM agents via the Anthropic API.

    Reads system prompts from the prompts/ directory, sends input_data
    as a user message, and parses structured JSON from the response.
    """

    def __init__(
        self,
        prompt_dir: str | Path | None = None,
        model_map: dict[str, str] | None = None,
        api_key: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        effort_map: dict[str, str] | None = None,
        verbose: bool = False,
    ) -> None:
        _load_env()
        self.verbose = verbose

        self.prompt_dir = Path(
            prompt_dir
            or Path(__file__).resolve().parent.parent / "prompts"
        )
        self.model_map = model_map or dict(_CFG_MODELS)
        self.max_tokens = max_tokens if max_tokens is not None else _CFG_MAX_TOKENS
        self.temperature = temperature if temperature is not None else _CFG_TEMP
        self.effort_map = effort_map or dict(_CFG_EFFORT)
        self.default_effort = _CFG_DEFAULT_EFFORT
        self.refusal_fallback = _CFG_REFUSAL_FALLBACK

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. "
                "Put it in agent_system/.env or export it."
            )

        try:
            import anthropic as _anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        self._client = _anthropic.Anthropic(api_key=resolved_key)
        # Fatal: a dead/invalid key, no access to the model, or an unknown
        # model ID -- retrying or JSON-repairing the (nonexistent) response
        # cannot help, so run_agent lets these propagate instead of
        # degrading to a per-agent agent_error dict (TODO.md §2).
        self._fatal_exceptions: tuple[type[Exception], ...] = (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        )

        # Shared call machinery (TODO.md §3): kwargs building, streaming,
        # retry/backoff, concurrency semaphore, usage tracking.
        self.usage_tracker = UsageTracker()
        self._shared = AnthropicClient(
            default_effort=self.default_effort,
            refusal_fallback=self.refusal_fallback,
            usage_tracker=self.usage_tracker,
        )

    # ---- prompt resolution ------------------------------------------------

    def _resolve_prompt_name(self, agent_name: str) -> str:
        """Map agent_name to prompt file name (without .md)."""
        return _PROMPT_ALIASES.get(agent_name, agent_name)

    def _load_prompt(self, agent_name: str) -> str:
        """Load system prompt for an agent."""
        prompt_name = self._resolve_prompt_name(agent_name)
        path = self.prompt_dir / f"{prompt_name}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {path}"
            )
        return path.read_text(encoding="utf-8")

    def _get_model(self, agent_name: str) -> str:
        """Get model ID for an agent. Checks: env override → model_map → default.

        TFL_MODEL_OVERRIDE forces every agent onto one model (cheap live test
        runs, e.g. claude-haiku-4-5); TFL_MODEL_FAST / TFL_MODEL_DEEP override
        the fast / deep tier only.
        """
        override = os.environ.get("TFL_MODEL_OVERRIDE", "").strip()
        if override:
            return override
        prompt_name = self._resolve_prompt_name(agent_name)
        # Env overrides (TFL_MODEL_DEEP, TFL_MODEL_FAST)
        if prompt_name in ("input_parser", "classifier"):
            env = os.environ.get("TFL_MODEL_FAST")
            if env:
                return env
        else:
            env = os.environ.get("TFL_MODEL_DEEP")
            if env:
                return env
        return self.model_map.get(
            prompt_name, self.model_map.get(agent_name, "claude-sonnet-5")
        )

    def _get_effort(self, agent_name: str) -> str:
        """Get effort level for an agent: effort_map → DEFAULT_EFFORT."""
        prompt_name = self._resolve_prompt_name(agent_name)
        return self.effort_map.get(
            prompt_name, self.effort_map.get(agent_name, self.default_effort)
        )

    def _build_request_kwargs(
        self, model: str, max_tokens: int, system: str, user: str,
        effort: str | None = None, temperature: float | None = None,
    ) -> dict[str, Any]:
        """Build kwargs for messages.stream / messages.create.

        Thinking models get adaptive thinking + an explicit effort (Opus 5.5
        would silently default to "medium"); legacy models get temperature.
        """
        return self._shared.build_request_kwargs(
            model, max_tokens, system, user,
            effort=effort,
            temperature=self.temperature if temperature is None else temperature,
        )

    def _stream_text(
        self, model: str, system: str, user: str, effort: str,
        output_schema: dict[str, Any] | None = None, agent_name: str | None = None,
    ) -> str:
        """Make one streamed API call (through the shared client) and return
        the answer text.

        ``output_schema`` (TODO.md §3 M): requests structured outputs for
        this call; unavailable for the model/request falls back to a plain
        call automatically (:meth:`AnthropicClient.call`).

        Raises:
            _AgentAPIError: a non-fatal API error, after the shared client's
                own retry/backoff budget (network, overloaded, ...) is spent
                -- also raised for a 400 BadRequest that isn't a schema
                rejection (:meth:`AnthropicClient.call` already retries
                that once without the schema): a malformed *request* for
                this one agent (prompt too long, a rejected refusal-fallback
                header, ...) should not abort the whole pipeline run.
            _AgentRefusal: the model declined the request.
            anthropic.AuthenticationError / PermissionDeniedError /
                NotFoundError: propagated as-is (not wrapped) so the caller
                fails fast instead of retrying a dead key / missing access.
        """
        t0 = time.monotonic()
        try:
            result = self._shared.call(
                self._client, model=model, max_tokens=self.max_tokens,
                system=system, user=user, effort=effort, output_schema=output_schema,
                agent=agent_name,
            )
        except FatalAPIError as exc:
            if exc.status_code == 400:
                print(f"[LLM] {model} 400 BadRequest: {exc}", file=sys.stderr)
                raise _AgentAPIError(str(exc)) from exc
            raise (exc.original if exc.original is not None else exc)
        except RetryableAPIError as exc:
            print(f"[LLM] API error: {exc}", file=sys.stderr)
            raise _AgentAPIError(str(exc)) from exc

        elapsed = time.monotonic() - t0
        if self.verbose:
            tokens_in = result.usage.input_tokens if result.usage else 0
            tokens_out = result.usage.output_tokens if result.usage else 0
            so_str = " so=yes" if result.used_structured_output else " so=no"
            print(
                f"[{agent_name or 'LLM'}] model={result.model} effort={effort} "
                f"tokens_in={tokens_in} tokens_out={tokens_out} time={elapsed:.1f}s{so_str}",
                file=sys.stderr, flush=True,
            )

        if result.stop_reason == "refusal":
            print(
                f"[LLM] {model} refused the request (category={result.refusal_category})",
                file=sys.stderr,
            )
            raise _AgentRefusal(result.refusal_category, result.refusal_explanation)
        if result.stop_reason == "max_tokens":
            print(f"[LLM] {model} hit max_tokens={self.max_tokens}; output truncated", file=sys.stderr)
        return result.text

    # ---- main API ---------------------------------------------------------

    # Agents that return plain text (not JSON)
    _RAW_TEXT_AGENTS = frozenset({"formalizer"})

    def run_agent(self, agent_name: str, input_data: Any = None) -> dict:
        """Call an LLM agent and return parsed output.

        For most agents: parses JSON from the response.
        For formalizer: returns raw text (Lean 4 code) wrapped in evidence.

        Never returns ``None``: an API error, a refusal, or a response that
        is still not valid JSON after one retry all come back as an
        ``agent_error`` dict (§5.3 contract) instead of silently vanishing
        (TODO.md §2) -- an ``anthropic.AuthenticationError`` /
        ``PermissionDeniedError`` / ``NotFoundError`` is the one exception:
        those propagate so the caller fails fast rather than retrying a
        dead key.
        """
        system_prompt = self._load_prompt(agent_name)
        model = self._get_model(agent_name)
        effort = self._get_effort(agent_name)
        # Structured outputs (TODO.md §3 M): None for agents with no closed
        # top-level contract (input_parser's shape depends on IR `kind`) or
        # that return raw text (formalizer) — those keep the legacy
        # "extract JSON from prose" path.
        from agent_system.lib.agent_output_schema import schema_for
        output_schema = schema_for(self._resolve_prompt_name(agent_name))

        # Student notes are kept in exactly ONE place: appended to the
        # system prompt as a clearly-labelled "## Student Notes" section,
        # then stripped from input_data before it is serialized into the
        # user JSON. Leaving them in both places would show the agent the
        # same free-form (untrusted) text twice -- once flagged as student
        # commentary, once folded into the "objective" evidence blob --
        # which doubles its apparent weight and blurs which copy the agent
        # should treat as authoritative.
        student_notes = ""
        if isinstance(input_data, dict) and input_data.get("student_notes"):
            student_notes = input_data["student_notes"]
            input_data = {k: v for k, v in input_data.items() if k != "student_notes"}

        if isinstance(input_data, (dict, list)):
            user_msg = json.dumps(input_data, indent=2, ensure_ascii=False)
        elif input_data is not None:
            user_msg = str(input_data)
        else:
            user_msg = "{}"

        if student_notes:
            system_prompt += (
                "\n\n## Student Notes\n\n"
                "The student provided the following comments, ideas, or partial "
                "solutions. Take these into account — they may contain useful "
                "insights, hypotheses to verify, or mistakes to address:\n\n"
                f"{student_notes}\n"
            )

        # Formalizer: return raw text, not JSON
        if agent_name in self._RAW_TEXT_AGENTS:
            try:
                raw = self._call_raw(system_prompt, user_msg, model, effort, agent_name=agent_name)
            except (_AgentAPIError, _AgentRefusal) as exc:
                return self._error_output(agent_name, str(exc))
            # Strip markdown fences if present
            code = raw.strip()
            if code.startswith("```"):
                lines = code.split("\n")
                lines = lines[1:]  # remove opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                code = "\n".join(lines)
            return {
                "module": agent_name,
                "status": "success",
                "evidence": {"lean_code": code, "output": code},
                "confidence": 0.8,
                "errors": [],
            }

        # Standard JSON agents
        try:
            parsed = self._call_and_parse(
                system_prompt, user_msg, model, effort, output_schema, agent_name=agent_name,
            )
        except _AgentRefusal as exc:
            # A safety-classifier decline is not a parse problem -- a
            # second call asking for "valid JSON only" cannot un-refuse it.
            return self._error_output(agent_name, str(exc))
        except _AgentAPIError as exc:
            return self._error_output(agent_name, str(exc))

        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        # Retry once with explicit JSON instruction. Only reached for an
        # actual parse failure (no exception raised above) -- a refusal or
        # an API error already returned above without this second call.
        retry_msg = (
            f"{user_msg}\n\n"
            "IMPORTANT: Your previous response was not valid JSON. "
            "Please respond with ONLY a valid JSON object, no other text."
        )
        try:
            parsed = self._call_and_parse(
                system_prompt, retry_msg, model, effort, output_schema, agent_name=agent_name,
            )
        except _AgentRefusal as exc:
            return self._error_output(agent_name, str(exc))
        except _AgentAPIError as exc:
            return self._error_output(agent_name, str(exc))

        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        return self._error_output(agent_name, "response was not valid JSON after one retry")

    def _call_raw(
        self, system: str, user: str, model: str, effort: str | None = None,
        agent_name: str | None = None,
    ) -> str:
        """Make one API call and return raw response text."""
        return self._stream_text(
            model, system, user, effort or self.default_effort, agent_name=agent_name,
        )

    def _call_and_parse(
        self, system: str, user: str, model: str, effort: str | None = None,
        output_schema: dict[str, Any] | None = None, agent_name: str | None = None,
    ) -> dict | None:
        """Make one API call and try to parse JSON from response.

        With ``output_schema`` the response is guaranteed-valid JSON
        (TODO.md §3 M) -- ``extract_json`` still runs, but its first
        strategy (whole text as JSON) is the only one that ever fires.
        """
        text = self._stream_text(
            model, system, user, effort or self.default_effort, output_schema,
            agent_name=agent_name,
        )
        return extract_json(text)

    def quick_validate(self, question: str, data: Any) -> str:
        """Fast validation / sanity check via Haiku.

        Returns a short natural-language assessment (1-3 sentences).
        Used for intermediate progress output.
        """
        model = self.model_map.get("validator", "claude-haiku-4-5")
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            data_str = str(data)

        try:
            kwargs = self._shared.build_request_kwargs(
                model, 256,
                system="You are a formal language theory expert. Answer in 1-2 sentences, in Russian.",
                user=f"{question}\n\nДанные:\n{data_str[:2000]}",
                effort="low", temperature=0.0,
            )
            with get_concurrency_semaphore():
                response = self._client.messages.create(**kwargs)
            self.usage_tracker.record(
                getattr(response, "model", model), getattr(response, "usage", None),
                agent="validator",
            )
            return _response_text(response).strip()
        except Exception as exc:
            return f"(validation error: {exc})"

    @staticmethod
    def _error_output(agent_name: str, detail: str) -> dict:
        """``agent_error`` dict (§5.3 contract) for an API error, a
        refusal, or an unparseable response -- never a silent ``None``."""
        return {
            "module": agent_name,
            "status": "agent_error",
            "evidence": {},
            "confidence": 0.0,
            "errors": [detail],
        }

    @staticmethod
    def _wrap_output(agent_name: str, parsed: dict) -> dict:
        """Wrap parsed LLM output in §5.3 contract format."""
        # If already wrapped, return as-is
        if "module" in parsed and "status" in parsed:
            return parsed
        return {
            "module": agent_name,
            "status": parsed.get("status", "success"),
            "evidence": parsed,
            "confidence": parsed.get("confidence", 0.8),
            "errors": parsed.get("errors", []),
        }
