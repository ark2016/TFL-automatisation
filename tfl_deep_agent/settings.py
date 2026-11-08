"""Run settings for the deep agent: budgets, allowed models, the model per fixed
role, HITL points, limits and intake caps.

Schema and defaults: ``PLAN.md`` ("Resolved decisions") and
``docs/architecture.html`` ("Бюджет и настройки"). The user-edited file is
``tfl_deep_agent/settings.json`` (gitignored; ``TFL_DEEP_SETTINGS`` points at
another path). The repository ships ``settings.example.json``, which equals
:data:`DEFAULTS` (pinned by ``tests/test_settings.py``).

Loading rules:

* no file -> :data:`DEFAULTS`;
* a file may hold only some keys; they are merged over the defaults
  (nested objects key by key, lists replaced as a whole);
* unknown keys, wrong types and inconsistent values raise
  :class:`SettingsError` with every problem listed. A broken settings file
  never falls back to defaults silently: it holds money limits;
* CLI / UI overrides (:func:`apply_overrides`, :func:`parse_override_pairs`)
  are merged the same way and validated again.

Model ids may be given as aliases (``haiku`` / ``sonnet`` / ``opus``); they are
stored as full ids. Every id must have a price card in
``agent_system/lib/llm_client.py`` (``config.pricing_key``): the budget could
neither reserve nor charge for an unpriced model.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from . import config

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = PACKAGE_DIR / "settings.json"
EXAMPLE_SETTINGS_PATH = PACKAGE_DIR / "settings.example.json"
SETTINGS_PATH_ENV = "TFL_DEEP_SETTINGS"

# PLAN.md / architecture.html defaults. settings.example.json is this dict.
DEFAULTS: dict[str, Any] = {
    "budget": {
        "per_task_usd": 10.00,  # user decision 2026-11-08: one run is limited to ~$10
        "per_day_usd": 20.00,
        "per_subagent_max_usd": 2.00,
        "reserve_for_report_usd": 1.00,  # user decision 2026-11-08 (was 0.30)
    },
    "models": {
        "allowed": [config.HAIKU, config.SONNET, config.OPUS],
        "roles": dict(config.MODELS),
        "opus_needs_reason": True,
    },
    "limits": {"max_model_calls": 80, "max_tool_calls": 300, "max_parallel": 4, "max_spawn_depth": 2},
    "hitl": {"formalization": True, "report": True, "execute": False},
    "intake": {"max_pdf_pages": 20, "max_file_mb": 10},
}

#: Roles settings.models.roles may configure: the config.MODELS roles plus
#: "general" (deepagents' general-purpose subagent; not in config.MODELS, so
#: its code default is Haiku, or the cheapest allowed model when Haiku is not
#: allowed). DEFAULTS keep only the config.MODELS roles.
KNOWN_ROLES: tuple[str, ...] = (*config.MODELS, "general")


class SettingsError(ValueError):
    """Settings that cannot be used: unreadable JSON, unknown keys, wrong types
    or inconsistent values. ``problems`` lists every issue found."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [message])
        super().__init__(message if not problems else message + ":\n  - " + "\n  - ".join(self.problems))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


def _money(x: float) -> float:
    if not math.isfinite(x):
        raise ValueError("must be a finite number")
    if x < 0:
        raise ValueError("must be >= 0")
    return float(x)


class BudgetSettings(_Strict):
    per_task_usd: float = Field(description="hard ceiling for one task (USD)")
    per_day_usd: float = Field(description="sum over every task started on one local calendar day (USD)")
    per_subagent_max_usd: float = Field(description="largest allocation for one subagent or workflow branch (USD)")
    reserve_for_report_usd: float = Field(description="untouchable remainder kept for the report (USD)")

    @field_validator("*")
    @classmethod
    def _check_money(cls, v: float) -> float:
        return _money(v)

    @model_validator(mode="after")
    def _consistent(self) -> BudgetSettings:
        problems = []
        if self.per_task_usd <= 0:
            problems.append("budget.per_task_usd must be > 0")
        if self.per_task_usd > self.per_day_usd:
            problems.append(
                f"budget.per_task_usd ({self.per_task_usd}) is larger than budget.per_day_usd ({self.per_day_usd})")
        # reserve_for_report_usd is not checked against per_task_usd: the ledger uses
        # report_reserve_usd, which never exceeds a quarter of the task budget
        # (user decision 2026-11-08, the rule fixed subagents already follow).
        # per_subagent_max_usd above per_task_usd is allowed (a UI override may lower
        # only the task budget): an allocation can never exceed the parent's
        # remainder anyway, so the cap is effectively min(cap, what is left).
        if self.per_subagent_max_usd <= 0:
            problems.append("budget.per_subagent_max_usd must be > 0")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def report_reserve_usd(self) -> float:
        """The reserve the task ledger keeps: see :func:`report_reserve`."""
        return report_reserve(self.per_task_usd, self.reserve_for_report_usd)


def report_reserve(per_task_usd: float, reserve_for_report_usd: float) -> float:
    """The report reserve a task actually keeps: ``reserve_for_report_usd``, but at
    most a quarter of ``per_task_usd``, so a small task budget (for example $1 with
    the $1.00 default reserve, which leaves $0.25) still has room to work. This is
    the rule fixed subagents already follow for their own reserve
    (``SubagentBudgetMiddleware``). User decision 2026-11-08."""
    return min(float(reserve_for_report_usd), float(per_task_usd) / 4.0)


def _model_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model id must be a non-empty string, got {value!r}")
    model = config.normalize_model(value)
    try:
        config.pricing_key(model)
    except config.UnknownModelError as exc:
        raise ValueError(str(exc)) from None
    return model


class ModelSettings(_Strict):
    allowed: list[str] = Field(description="models the agent may pick (ids or haiku / sonnet / opus)")
    roles: dict[str, str] = Field(description="model per fixed role; roles not listed use config.MODELS")
    opus_needs_reason: bool = Field(description="an Opus pick needs a logged `reason`")

    @field_validator("allowed", mode="before")
    @classmethod
    def _allowed_ids(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("models.allowed must be a list of model ids")
        out: list[str] = []
        for item in v:
            mid = _model_id(item)
            if mid not in out:
                out.append(mid)
        if not out:
            raise ValueError("models.allowed must name at least one model")
        return out

    @field_validator("roles", mode="before")
    @classmethod
    def _role_ids(cls, v: Any) -> dict[str, str]:
        if not isinstance(v, Mapping):
            raise ValueError("models.roles must be an object {role: model}")
        unknown = sorted(set(v) - set(KNOWN_ROLES))
        if unknown:
            raise ValueError(f"unknown role(s) {unknown}; known roles: {list(KNOWN_ROLES)}")
        return {role: _model_id(model) for role, model in v.items()}

    @model_validator(mode="after")
    def _roles_allowed(self) -> ModelSettings:
        bad = {r: m for r, m in self.roles.items() if m not in self.allowed}
        if bad:
            raise ValueError(f"models.roles use model(s) not in models.allowed {self.allowed}: {bad}")
        return self


class LimitSettings(_Strict):
    max_model_calls: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    max_parallel: int = Field(ge=1)
    max_spawn_depth: int = Field(ge=0)


class HitlSettings(_Strict):
    formalization: bool = Field(description="HITL 1: confirm transcription + IR")
    report: bool = Field(description="HITL 2: approve the report on submit_report")
    execute: bool = Field(description="confirm every sandbox `execute`")


class IntakeSettings(_Strict):
    max_pdf_pages: int = Field(ge=1)
    max_file_mb: float = Field(gt=0)


class Settings(_Strict):
    budget: BudgetSettings
    models: ModelSettings
    limits: LimitSettings
    hitl: HitlSettings
    intake: IntakeSettings

    # -- helpers used by the agent / budget / model policy -----------------

    def model_for_role(self, role: str | None) -> str:
        """Model for a fixed role: ``TFL_MODEL_OVERRIDE`` > ``models.roles`` >
        ``config.MODELS`` > ``config.DEFAULT_MODEL`` (via ``config.resolve_model``,
        which fails closed on an unpriced id). The override is the cheap test
        switch shared with the pipelines and wins over ``models.allowed``."""
        requested = self.models.roles.get(role) if role else None
        return config.resolve_model(role=role, requested=requested)

    def is_allowed(self, model: str) -> bool:
        """Whether ``model`` (id or alias) is in ``models.allowed``."""
        return config.normalize_model(model) in self.models.allowed

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Merge / validate
# ---------------------------------------------------------------------------

def deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """``base`` with ``overrides`` merged in: nested mappings merge key by key,
    everything else (lists included) is replaced. Neither input is changed."""
    out = copy.deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _problems(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "")
        msg = msg.removeprefix("Value error, ")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


def validate_settings(data: Mapping[str, Any], *, merge_defaults: bool = True) -> Settings:
    """Validate a settings mapping (merged over :data:`DEFAULTS` unless
    ``merge_defaults`` is false). Raises :class:`SettingsError`."""
    if not isinstance(data, Mapping):
        raise SettingsError(f"settings must be a JSON object, got {type(data).__name__}")
    merged = deep_merge(DEFAULTS, data) if merge_defaults else dict(data)
    try:
        return Settings.model_validate(merged)
    except ValidationError as exc:
        raise SettingsError("invalid settings", _problems(exc)) from None


def default_settings() -> Settings:
    return validate_settings(DEFAULTS, merge_defaults=False)


def settings_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Explicit ``path`` > ``$TFL_DEEP_SETTINGS`` > ``tfl_deep_agent/settings.json``."""
    if path is not None:
        return Path(path)
    env = os.environ.get(SETTINGS_PATH_ENV, "").strip()
    return Path(env) if env else DEFAULT_SETTINGS_PATH


def load_settings(
    path: str | os.PathLike[str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Settings from ``path`` (see :func:`settings_path`) merged over the
    defaults, then ``overrides`` (CLI / UI) merged on top. A missing file means
    the defaults; an unreadable or invalid one raises :class:`SettingsError`."""
    p = settings_path(path)
    data: dict[str, Any] = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettingsError(f"cannot read settings file {p}: {exc}") from None
        if not isinstance(data, dict):
            raise SettingsError(f"settings file {p} must hold a JSON object")
    try:
        settings = validate_settings(data)
    except SettingsError as exc:
        raise SettingsError(f"invalid settings file {p}", exc.problems) from None
    if overrides:
        settings = apply_overrides(settings, overrides)
    return settings


def apply_overrides(settings: Settings, overrides: Mapping[str, Any]) -> Settings:
    """A new :class:`Settings` with ``overrides`` (a nested mapping, as sent by
    the UI's ``settings_override``) merged in and validated again."""
    try:
        return validate_settings(deep_merge(settings.to_dict(), overrides), merge_defaults=False)
    except SettingsError as exc:
        raise SettingsError("invalid settings override", exc.problems) from None


def parse_override_pairs(pairs: Iterable[str]) -> dict[str, Any]:
    """CLI form ``section.key=value`` (``budget.per_task_usd=3``,
    ``models.allowed=["haiku","sonnet"]``, ``hitl.execute=true``) -> nested
    mapping. A value is parsed as JSON when it is valid JSON, otherwise taken as
    a string (``models.roles.main=sonnet``)."""
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key or any(not part for part in key.split(".")):
            raise SettingsError(f"override {pair!r} is not of the form section.key=value")
        raw = raw.strip()
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            nxt = node.setdefault(part, {})
            if not isinstance(nxt, dict):
                raise SettingsError(f"override {pair!r} conflicts with an earlier override of {part!r}")
            node = nxt
        node[parts[-1]] = value
    return out


def save_settings(settings: Settings | Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
    """Validate and write the full settings atomically (temp file + replace).
    Returns the path written."""
    if not isinstance(settings, Settings):
        settings = validate_settings(settings)
    p = settings_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def settings_json_schema() -> dict[str, Any]:
    """JSON schema of :class:`Settings` (for the TFL Lab settings form)."""
    return Settings.model_json_schema()
