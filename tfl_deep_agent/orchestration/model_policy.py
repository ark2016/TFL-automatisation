"""Model policy for ad-hoc agents (P4): ``spawn_subagent`` and ``run_workflow`` steps.

The main agent picks a model for every subagent and workflow step it creates
(architecture.html, "Как агент выбирает модель"). This module decides whether
that pick is admissible and which model is actually used:

* the pick is a tier (``haiku`` / ``sonnet`` / ``opus``) or a full model id;
  anything else is refused with the list of admissible tiers;
* it must be in ``settings.models.allowed`` (compared by price card, so a dated
  snapshot id matches its alias);
* with ``settings.models.opus_needs_reason`` an Opus pick needs a non-empty
  ``reason`` (logged: it goes into the tool result and the evidence ledger,
  which records the call's arguments);
* ``TFL_MODEL_OVERRIDE`` (the shared cheap-test switch) then forces the model
  that is really built, as it does for the fixed roles. The pick is still
  checked first, so a plan that is refused in a real run is refused in an
  override run too; the result says ``overridden: true``.

Errors are :class:`ModelPolicyError` with a message written for the model that
made the pick (what was wrong, what is allowed, how to fix it).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from .. import config

#: Tier name -> model id (config.MODEL_ALIASES, in price order).
TIERS: dict[str, str] = {"haiku": config.HAIKU, "sonnet": config.SONNET, "opus": config.OPUS}

#: One line per tier for tool descriptions and refusals (architecture.html,
#: "Как агент выбирает модель").
TIER_GUIDE: dict[str, str] = {
    "haiku": "mechanics: enumerating cases, format checks, bulk oracle calls, draft hypotheses",
    "sonnet": "transcription, IR, sandbox code, standard proofs by a known scheme",
    "opus": "non-standard theory, criticism, Lean (needs `reason` when opus_needs_reason is on)",
}

#: Shortest reason accepted for an Opus pick (a word or two, not "x").
MIN_REASON_CHARS = 8


class ModelPolicyError(ValueError):
    """A model pick the policy refuses. ``str(exc)`` is the model-readable message."""


@dataclasses.dataclass(frozen=True)
class ModelChoice:
    """The outcome of :func:`resolve_model_choice`."""

    requested: str           # what the agent asked for, as given
    tier: str | None         # haiku / sonnet / opus of the requested model (None for an unknown family)
    model: str               # the model id the agent picked (normalized)
    effective_model: str     # the model really built (TFL_MODEL_OVERRIDE may differ)
    overridden: bool
    reason: str | None

    @property
    def effective_tier(self) -> str | None:
        return tier_of(self.effective_model)

    def to_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "tier": self.tier, "model": self.model,
                "effective_model": self.effective_model, "effective_tier": self.effective_tier,
                "overridden": self.overridden, "reason": self.reason}


def _models_section(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        models = settings.get("models") or {}
        return models if isinstance(models, Mapping) else {}
    models = getattr(settings, "models", None)
    if models is None:
        return {}
    dump = getattr(models, "model_dump", None)
    return dump() if callable(dump) else {}


def tier_of(model: str | None) -> str | None:
    """``haiku`` / ``sonnet`` / ``opus`` for a model id or alias, else ``None``."""
    if not model:
        return None
    try:
        key = config.pricing_key(config.normalize_model(model))
    except config.UnknownModelError:
        return None
    for tier, mid in TIERS.items():
        if config.pricing_key(mid) == key:
            return tier
    for tier in TIERS:
        if tier in key:
            return tier
    return None


def allowed_models(settings: Any) -> list[str]:
    """``settings.models.allowed`` as model ids (every known model when the list is absent)."""
    allowed = _models_section(settings).get("allowed")
    if not allowed:
        return list(TIERS.values())
    return [config.normalize_model(m) for m in allowed]


def allowed_tiers(settings: Any) -> list[str]:
    """The tiers the agent may pick, in price order."""
    keys = {config.pricing_key(m) for m in allowed_models(settings) if _priced(m)}
    return [t for t, mid in TIERS.items() if config.pricing_key(mid) in keys]


def opus_needs_reason(settings: Any) -> bool:
    return bool(_models_section(settings).get("opus_needs_reason", True))


def _priced(model: str) -> bool:
    try:
        config.pricing_key(model)
    except config.UnknownModelError:
        return False
    return True


def _allowed_text(settings: Any) -> str:
    tiers = allowed_tiers(settings)
    return ", ".join(f"{t} ({TIER_GUIDE[t].split(':')[0]})" for t in tiers) or "none"


def resolve_model_choice(settings: Any, requested: Any, reason: str | None = None, *,
                         what: str = "subagent") -> ModelChoice:
    """Check an ad-hoc model pick and return the model to build.

    ``what`` names the thing in the error message (``subagent 'nerode'``,
    ``workflow step 'pump'``). Raises :class:`ModelPolicyError`."""
    if not isinstance(requested, str) or not requested.strip():
        raise ModelPolicyError(
            f"{what}: no model given. Pick one of the allowed tiers: {_allowed_text(settings)}.")
    raw = requested.strip()
    model = config.normalize_model(raw)
    if not _priced(model):
        raise ModelPolicyError(
            f"{what}: unknown model {raw!r}. Use a tier name: {', '.join(allowed_tiers(settings)) or 'none'} "
            f"(or the full id of an allowed model).")
    key = config.pricing_key(model)
    allowed = allowed_models(settings)
    if key not in {config.pricing_key(m) for m in allowed if _priced(m)}:
        tier = tier_of(model) or model
        raise ModelPolicyError(
            f"{what}: model {tier!r} ({model}) is not allowed by settings.models.allowed. "
            f"Allowed tiers: {_allowed_text(settings)}. Pick one of those.")
    tier = tier_of(model)
    why = reason.strip() if isinstance(reason, str) else None
    if tier == "opus" and opus_needs_reason(settings) and (not why or len(why) < MIN_REASON_CHARS):
        raise ModelPolicyError(
            f"{what}: opus needs a `reason` (settings.models.opus_needs_reason is on; at least "
            f"{MIN_REASON_CHARS} characters saying why haiku / sonnet would not do). Give a reason or pick "
            f"a cheaper tier: {', '.join(t for t in allowed_tiers(settings) if t != 'opus') or 'none'}.")
    override = config.model_override()
    try:
        effective = config.resolve_model(requested=model) if not override else config.resolve_model()
    except config.UnknownModelError as exc:  # an unpriced TFL_MODEL_OVERRIDE
        raise ModelPolicyError(f"{what}: TFL_MODEL_OVERRIDE is unusable: {exc}") from None
    return ModelChoice(requested=raw, tier=tier, model=model, effective_model=effective,
                       overridden=bool(override) and config.pricing_key(effective) != key,
                       reason=why or None)


def model_cost_table(settings: Any, *, input_tokens: int = 4000,
                     max_tokens: int | None = None, min_output_tokens: int = 4096) -> list[dict[str, Any]]:
    """Worst-case USD of one call per allowed tier (for ``budget_status`` and
    tool descriptions): at the default ``max_tokens`` and at the floor
    ``min_output_tokens`` that ad-hoc agents may be lowered to."""
    out = []
    for tier in allowed_tiers(settings):
        mid = TIERS[tier]
        full = config.resolve_max_tokens(model=mid, requested=max_tokens)
        out.append({
            "tier": tier, "model": mid, "use_for": TIER_GUIDE[tier],
            "call_worst_case_usd": round(config.worst_case_usd(mid, input_tokens, full), 4),
            "call_floor_usd": round(config.worst_case_usd(mid, input_tokens, min_output_tokens), 4),
            "assumed_input_tokens": input_tokens, "max_tokens": full,
        })
    return out


__all__ = [
    "MIN_REASON_CHARS",
    "TIERS",
    "TIER_GUIDE",
    "ModelChoice",
    "ModelPolicyError",
    "allowed_models",
    "allowed_tiers",
    "model_cost_table",
    "opus_needs_reason",
    "resolve_model_choice",
    "tier_of",
]
