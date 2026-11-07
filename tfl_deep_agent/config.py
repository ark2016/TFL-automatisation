"""
TFL Deep Agent — configuration.

Model ids, per-role effort / max_tokens and prices, kept in sync with the four
pipelines' ``config.py`` files (``agent_system``, ``cfl_system``,
``dcfl_system``, ``ll_system``) and with ``agent_system/lib/llm_client.py``.

Budgets, the list of allowed models and the per-role model choice that a user
edits live in ``settings.json`` (``settings.py``, P2); the values here are the
code defaults those settings fall back to.
"""

from __future__ import annotations

import os

# Prices are NOT copied here: the single pricing table for every project is
# agent_system/lib/llm_client.py (MODEL_PRICING; the four pipeline config.py
# files hold none). Re-exported so callers (BudgetMiddleware, the renderer)
# import them from one place, and tests/test_config.py pins that they stay the
# same objects.
from agent_system.lib.llm_client import (  # noqa: F401  (re-exports)
    LONG_PROMPT_THRESHOLD_TOKENS,
    MODEL_MAX_OUTPUT_TOKENS,
    MODEL_PRICING,
    MODEL_PRICING_LONG_PROMPT,
    _normalize_model_id,
    clamp_max_tokens,
    estimate_cost_usd,
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
# Haiku 5.5 is a thinking model like the others: adaptive thinking + effort,
# no temperature. Haiku 4.5 still works but takes the legacy temperature path.

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

# Short names the agent may use when it picks a model (spawn_subagent,
# run_workflow): {"model": "sonnet"}.
MODEL_ALIASES: dict[str, str] = {
    "opus": OPUS,
    "sonnet": SONNET,
    "haiku": HAIKU,
}

# Default model per fixed role (architecture.html, "Готовые роли"). Keys match
# settings.json ``models.roles``; "lean" is the lean-formalizer subagent.
MODELS: dict[str, str] = {
    "main":       OPUS,     # plan, hypothesis, choice of methods and models, report
    "intake":     SONNET,   # transcribe PDF / image into the task text (vision)
    "ir_builder": SONNET,   # task text -> IR that passes validate_ir
    "prover":     OPUS,     # proof, every concrete step checked by an oracle
    "critic":     OPUS,     # clean-context search for an error in the proof
    "lean":       OPUS,     # Lean 4 proof body for a fixed statement
    "coder":      SONNET,   # Python in the Docker sandbox
}
DEFAULT_MODEL = OPUS

# Reasoning depth per role (``output_config.effort``). Opus 5.5 always runs
# adaptive thinking and defaults to "medium" when effort is omitted, so every
# role gets an explicit value, with the pipelines' convention: proof-producing
# roles at "high", structured parsing / transcription at "medium".
# Levels: low | medium | high | xhigh | max. Roles not listed (ad-hoc
# subagents) use DEFAULT_EFFORT unless the agent passes one.
EFFORT: dict[str, str] = {
    "main":       "high",
    "intake":     "medium",
    "ir_builder": "medium",
    "prover":     "high",
    "critic":     "high",
    "lean":       "high",
    "coder":      "medium",
}
DEFAULT_EFFORT = "high"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Server-side refusal fallback (beta server-side-fallback-2026-07-01), same as
# the pipelines: an Opus 5.x request declined by a safety classifier is re-run
# on the model Anthropic recommends for that category.
REFUSAL_FALLBACK = True

# Max tokens per call. Thinking tokens count toward this limit, so it must
# cover reasoning + the answer. Calls are streamed (models.make_chat_model sets
# streaming=True), so a large ceiling does not hit the SDK's non-streaming
# guard; you pay only for tokens actually generated. NOTE for P2: the
# BudgetMiddleware worst-case check counts max_tokens in full, so a 64000
# ceiling on Opus reserves $1.28 per call before the call is made -- more than
# settings.example.json's reserve_for_report_usd, and the lean role's 128000
# ($2.56) is more than per_subagent_max_usd. OPEN user decision before P2
# (DEV_NOTES.md, "P0 fix"); tests/test_config.py pins the conflict (xfail).
MAX_TOKENS = 64000

# Per-role overrides (128000 is the API maximum for Opus / Sonnet 5.5, the
# value the pipelines use for the Lean formalization entry).
MAX_TOKENS_PER_ROLE: dict[str, int] = {"lean": 128000}

# Temperature -- only sent to legacy models (Haiku 4.5). Adaptive-thinking
# models (Opus 5.x, Sonnet 5 / 5.5, Haiku 5.5) reject sampling parameters.
TEMPERATURE = 0.0

# Seconds allowed for one `lake env lean` check (cfl_system / dcfl_system
# value: their statements import langlib on top of Mathlib).
LEAN_TIMEOUT = 180


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def model_override() -> str:
    """``TFL_MODEL_OVERRIDE`` (stripped) or ``""``. When set it forces every
    role and every ad-hoc subagent onto that one model -- the cheap live-test
    switch shared with the four pipelines (``TFL_MODEL_OVERRIDE=claude-haiku-5-5``)."""
    return os.environ.get("TFL_MODEL_OVERRIDE", "").strip()


class UnknownModelError(ValueError):
    """A model id with no ``MODEL_PRICING`` card. Raised instead of letting an
    unpriced id (a typo, a retired model) reach the API: the budget could
    neither reserve nor charge for it."""


def normalize_model(model: str) -> str:
    """Map a short alias (``haiku`` / ``sonnet`` / ``opus``) to its model id;
    any other string is returned stripped and unchanged."""
    key = model.strip()
    return MODEL_ALIASES.get(key.lower(), key)


def pricing_key(model: str) -> str:
    """The ``MODEL_PRICING`` key for ``model`` (a dated snapshot id such as
    ``claude-haiku-4-5-20251001`` maps to its bare alias, as in
    ``estimate_cost_usd``). Raises :class:`UnknownModelError` when there is no
    price card."""
    key = _normalize_model_id(model)
    if key not in MODEL_PRICING:
        raise UnknownModelError(
            f"model {model!r} has no price card in MODEL_PRICING "
            f"(agent_system/lib/llm_client.py); known: {sorted(MODEL_PRICING)}"
        )
    return key


def resolve_model(role: str | None = None, requested: str | None = None) -> str:
    """Model id for one call: ``TFL_MODEL_OVERRIDE`` > ``requested`` (an id or
    alias) > ``MODELS[role]`` > ``DEFAULT_MODEL``.

    Fails closed: an id without a ``MODEL_PRICING`` card (after the
    dated-suffix normalization) raises :class:`UnknownModelError`, whether it
    came from the override or from ``requested``. The returned id is the one
    sent on the wire (a dated snapshot id is kept as given)."""
    override = model_override()
    if override:
        model = normalize_model(override)
    elif requested:
        model = normalize_model(requested)
    elif role and role in MODELS:
        model = MODELS[role]
    else:
        model = DEFAULT_MODEL
    pricing_key(model)
    return model


def worst_case_usd(model: str, input_tokens: int, max_tokens: int) -> float:
    """Upper bound on the cost of one call before it is made: every prompt
    token at the dearer of the input / cache-write rates (deepagents marks the
    prompt with ``cache_control``, so it may be written to the cache) plus
    ``max_tokens`` output tokens. Haiku 5.5's long-prompt card applies when
    ``input_tokens`` exceeds ``LONG_PROMPT_THRESHOLD_TOKENS``. Raises
    :class:`UnknownModelError` for an unpriced model, never returns ``None``."""
    if input_tokens < 0 or max_tokens < 0:
        raise ValueError("token counts must be non-negative")
    key = pricing_key(model)
    card = MODEL_PRICING[key]
    if input_tokens > LONG_PROMPT_THRESHOLD_TOKENS and key in MODEL_PRICING_LONG_PROMPT:
        card = MODEL_PRICING_LONG_PROMPT[key]
    rates = [card.get("input"), card.get("cache_write"), card.get("output")]
    if any(r is None for r in rates):
        raise UnknownModelError(f"model {model!r} has an incomplete price card: {card}")
    in_rate = max(card["input"], card["cache_write"])
    return (input_tokens * in_rate + max_tokens * card["output"]) / 1_000_000


def resolve_effort(role: str | None = None, requested: str | None = None) -> str:
    """Effort for one call: ``requested`` > ``EFFORT[role]`` > ``DEFAULT_EFFORT``."""
    effort = requested or (EFFORT.get(role, DEFAULT_EFFORT) if role else DEFAULT_EFFORT)
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"unknown effort {effort!r}; expected one of {EFFORT_LEVELS}")
    return effort


def resolve_max_tokens(
    role: str | None = None, model: str | None = None, requested: int | None = None,
) -> int:
    """``requested`` > ``MAX_TOKENS_PER_ROLE[role]`` > ``MAX_TOKENS``, clamped to
    the model's known output cap (``clamp_max_tokens``)."""
    if requested is not None:
        value = int(requested)
    else:
        value = MAX_TOKENS_PER_ROLE.get(role, MAX_TOKENS) if role else MAX_TOKENS
    if value <= 0:
        raise ValueError(f"max_tokens must be positive, got {value}")
    return clamp_max_tokens(model, value) if model else value
