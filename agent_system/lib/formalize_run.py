"""Separate Lean formalization entry over a finished run (docs/VERDICT_POLICY.md R-Lean).

The pipelines (`agent_system` / `cfl_system` / `dcfl_system`) can run the Lean
step *inside* a run (opt-in, ``--formalize``).  This module is the second,
independent entry: given a run directory that already holds
``<stem>_result.json`` and ``input.json`` it

1. renders the theorem statement from the IR (``lean_ir.render_statement*`` --
   code, never the LLM) for the result's verdict (or ``--direction``);
2. runs the prover loop: attempt 1 on ``first_model`` (default Opus 5.5), then
   up to ``retries`` corrections (default 2) on ``retry_model`` (Sonnet 5.5)
   that get the previous body and the Lean errors back; ``max_tokens`` is the
   API maximum (128000) for both.  A response cut off at ``max_tokens`` without
   a proof body earns exactly one more Sonnet attempt with the instruction to
   output ONLY the proof body, then the loop stops;
3. compiles every body with ``compose_lean_file`` / ``check_lean_file`` (the
   barrier in ``type_check`` is not touched here);
4. appends the ``formalization`` block to ``<stem>_result.json`` (and re-renders
   md/html through the system's renderer); a ``proved`` block updates
   verdict/confidence by the system's own R-Lean gate.

Progress and cost go through :class:`agent_system.lib.progress.ProgressWriter`
(``progress.jsonl`` / ``partial_result.json`` in the run directory, cumulative
usage continued from the finished run).  There is no run timeout: the caller
cancels by killing the process, ``result.json`` is only replaced (atomically) at
the very end.

This module is system-agnostic.  Everything system-specific (statement
renderer, gate, plan, renderer, config defaults) comes in a
:class:`SystemSpec` built by each system's thin ``formalize.py`` wrapper --
``agent_system`` must never import ``cfl_system`` / ``dcfl_system``.

Idempotent: a run whose block is already ``proved`` is left untouched (no API
call) unless ``force``; re-running over a failed block replaces it, and the
gate is always applied to the *pre-Lean* values kept in ``block["baseline"]``,
so a verdict never gets two Lean basis entries.  A forced re-run over a proved
block that does not end ``proved`` again keeps the old block, verdict and
confidence untouched (``kept_proved`` in the summary / progress event).
"""

from __future__ import annotations

import argparse
import copy
import html as _html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .llm_client import (
    MODEL_PRICING,
    AnthropicClient,
    FatalAPIError,
    RetryableAPIError,
    UsageTracker,
    build_agent_output_schema,
    estimate_cost_usd,
    extract_json,
    schema_string,
    schema_string_array,
)
from .progress import ProgressWriter
from .type_check import check_lean_file, compose_lean_file, is_docker_available

# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

#: API maximum output for Opus 5.5 / Sonnet 5.5 (decision 2026-09-29: 128000
#: for both attempts).  Models with a lower ceiling are clamped, see
#: :data:`MODEL_MAX_OUTPUT`.
DEFAULT_MAX_TOKENS = 128000
DEFAULT_RETRIES = 2

#: Output ceilings of models that cannot take 128000 (TFL_MODEL_OVERRIDE=haiku
#: live checks would get a 400 otherwise).
MODEL_MAX_OUTPUT: dict[str, int] = {"claude-haiku-4-5": 64000}

BODY_ONLY_INSTRUCTION = (
    "Your previous response was cut off by the output limit before the answer was "
    "complete, so no proof body reached the checker. Output ONLY the proof body: "
    'a JSON object {"proof_body": "...", "lemmas_used": [], "notes": ""} with the '
    "complete tactic block, no long deliberation and no commentary. Prefer the "
    "shortest complete proof that follows the plan."
)

_TERMINAL_STATUSES = ("has_sorry", "timeout", "unavailable")

# Rough per-call output-token assumptions for the pre-run estimate (adaptive
# thinking counts as output).  Only the *maximum* is a hard bound.
_EXPECTED_OUT_FIRST = 25_000
_EXPECTED_OUT_RETRY = 12_000
_CHARS_PER_TOKEN = 3.0

_ABSENT = {"__absent__": True}


def proof_body_schema() -> dict:
    """``output_config.format`` schema for the proof-body contract shared by
    every system's Lean formalizer prompt: ``{proof_body, lemmas_used, notes}``."""
    return build_agent_output_schema({
        "proof_body": schema_string(),
        "lemmas_used": schema_string_array(),
        "notes": schema_string(),
    })


# ---------------------------------------------------------------------------
# System spec (built by <system>/formalize.py)
# ---------------------------------------------------------------------------

@dataclass
class SystemSpec:
    """Everything system-specific the shared loop needs."""

    system: str                                   # "reg" | "cfl" | "dcfl"
    agent: str                                    # config role of the first attempt
    retry_agent: str                              # config role of the corrections
    prompt_path: Path                             # system prompt of the Lean formalizer
    models: Mapping[str, str]
    efforts: Mapping[str, str]
    temperatures: Mapping[str, float]
    lean_timeout: int
    directions: tuple[str, str]                   # (positive, negative) verdict values
    #: (ir, direction) -> (LeanStatement | None, reason | None)
    render_statement: Callable[[dict, str], tuple[Any, str | None]]
    #: (result, ir) -> (direction | None, source)  -- source in verdict/hypothesis
    pick_direction: Callable[[dict, dict], tuple[str | None, str]]
    #: (result, direction) -> plan for the agent (str or dict)
    build_plan: Callable[[dict, str], Any]
    #: mutates *result* in place when the block is ``proved``; returns whether it did
    apply_gate: Callable[[dict, dict], bool]
    #: (result, path, fmt) -- the system's ``render_to_file``
    render_file: Callable[[dict, str, str], None]
    #: dotted paths of the result that the gate may change (restored from the
    #: baseline before every gate application)
    baseline_paths: Sequence[str]
    #: where the block lives in the result, e.g. ("formalization",) or ("evidence", "formalization")
    block_path: tuple[str, ...] = ("formalization",)
    available_lemmas: Sequence[str] = ()
    output_schema: dict | None = field(default_factory=proof_body_schema)
    default_retries: int = DEFAULT_RETRIES
    default_max_tokens: int = DEFAULT_MAX_TOKENS
    #: optional post-gate hook (e.g. recompute the top-level verdict)
    refresh: Callable[[dict], None] | None = None

    @property
    def default_first_model(self) -> str:
        return self.models.get(self.agent, "claude-opus-5-5")

    @property
    def default_retry_model(self) -> str:
        return self.models.get(self.retry_agent, "claude-sonnet-5-5")


@dataclass
class FormalizeSettings:
    first_model: str
    retry_model: str
    retries: int = DEFAULT_RETRIES
    max_tokens: int = DEFAULT_MAX_TOKENS

    @classmethod
    def defaults(cls, spec: SystemSpec) -> "FormalizeSettings":
        s = cls(spec.default_first_model, spec.default_retry_model,
                spec.default_retries, spec.default_max_tokens)
        return s.with_env_override()

    def with_env_override(self) -> "FormalizeSettings":
        """``TFL_MODEL_OVERRIDE`` forces every call onto one model (cheap live
        checks, e.g. claude-haiku-4-5), exactly as in the pipelines."""
        override = os.environ.get("TFL_MODEL_OVERRIDE", "").strip()
        if override:
            return FormalizeSettings(override, override, self.retries, self.max_tokens)
        return self

    def max_tokens_for(self, model: str) -> int:
        return min(self.max_tokens, MODEL_MAX_OUTPUT.get(model, self.max_tokens))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_path(obj: Any, path: Sequence[str]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _set_path(obj: dict, path: Sequence[str], value: Any) -> None:
    cur = obj
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _del_path(obj: dict, path: Sequence[str]) -> None:
    cur: Any = obj
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return
        cur = cur[key]
    if isinstance(cur, dict):
        cur.pop(path[-1], None)


def snapshot_baseline(result: dict, paths: Sequence[str]) -> dict:
    """Pre-Lean values of the result paths a gate may touch."""
    out: dict[str, Any] = {}
    for dotted in paths:
        parts = dotted.split(".")
        cur: Any = result
        present = True
        for key in parts:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                present = False
                break
        out[dotted] = copy.deepcopy(cur) if present else dict(_ABSENT)
    return out


def restore_baseline(result: dict, baseline: Mapping[str, Any]) -> None:
    for dotted, value in baseline.items():
        parts = dotted.split(".")
        if value == _ABSENT:
            _del_path(result, parts)
        else:
            _set_path(result, parts, copy.deepcopy(value))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader holds the file open
            time.sleep(0.03 * (attempt + 1))
    path.write_text(text, encoding="utf-8")


def find_result_file(run_dir: Path, stem: str | None = None) -> tuple[Path, str]:
    """``(<stem>_result.json, stem)`` of a run directory."""
    if stem:
        p = run_dir / f"{stem}_result.json"
        if not p.is_file():
            raise FileNotFoundError(f"{p} not found")
        return p, stem
    if (run_dir / "input_result.json").is_file():
        return run_dir / "input_result.json", "input"
    found = sorted(run_dir.glob("*_result.json"))
    if len(found) != 1:
        raise FileNotFoundError(
            f"expected exactly one *_result.json in {run_dir}, found {len(found)}; pass --stem")
    return found[0], found[0].name[: -len("_result.json")]


def _statement_snapshot(statement: Any) -> dict:
    def field_of(name: str, default: Any = None) -> Any:
        if isinstance(statement, dict):
            return statement.get(name, default)
        return getattr(statement, name, default)

    imports = field_of("imports")
    return {
        "imports": list(imports) if imports is not None else None,
        "alphabet_decl": field_of("alphabet_decl"),
        "language_decl": field_of("language_decl"),
        "theorem_decl": field_of("theorem_decl"),
        "name": field_of("name") or "tfl_main",
    }


def _sum_usage(tracker: UsageTracker) -> dict:
    d = tracker.as_dict()
    return {
        "calls": d["calls"],
        "input_tokens": d["input_tokens"],
        "output_tokens": d["output_tokens"],
        "estimated_cost_usd": d["estimated_cost_usd"] if d["calls"] else 0.0,
    }


# ---------------------------------------------------------------------------
# Proof-body sources: live API / scripted mock
# ---------------------------------------------------------------------------

@dataclass
class Proposal:
    """One prover call, parsed."""

    body: str | None = None
    gave_up: bool = False           # explicit empty proof_body
    truncated: bool = False         # stop_reason == max_tokens
    error: str | None = None        # API failure / refusal (nothing usable came back)
    notes: str | None = None
    stop_reason: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    salvaged: bool = False          # body cut out of a truncated JSON reply


_BODY_RE = re.compile(r'"proof_body"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)


def parse_proposal_text(text: str) -> tuple[str | None, bool, str | None, bool]:
    """``(body, gave_up, notes, salvaged)`` from a prover reply.

    ``body is None`` -> nothing usable.  ``gave_up`` -> explicit empty
    ``proof_body`` (an honest "no route").  A truncated JSON whose
    ``proof_body`` string is already closed still yields the body
    (``salvaged``); a body cut mid-string never does.
    """
    parsed = extract_json(text or "")
    if isinstance(parsed, dict):
        src = parsed
        ev = parsed.get("evidence")
        if "proof_body" not in src and isinstance(ev, dict):
            src = ev
        body = src.get("proof_body")
        if isinstance(body, str):
            notes = src.get("notes")
            return body, not body.strip(), (str(notes) if notes else None), False
    m = _BODY_RE.search(text or "")
    if m:
        try:
            body = json.loads('"' + m.group(1) + '"')
        except ValueError:
            body = None
        if isinstance(body, str) and body.strip():
            return body, False, None, True
    return None, False, None, False


class LiveSource:
    """Prover calls through the shared :class:`AnthropicClient`."""

    def __init__(self, client: Any, tracker: UsageTracker, *, refusal_fallback: bool = True):
        self.client = client
        self.tracker = tracker
        self.shared = AnthropicClient(usage_tracker=tracker, refusal_fallback=refusal_fallback)

    def propose(self, *, agent: str, model: str, effort: str, max_tokens: int,
                system: str, user: str, temperature: float, schema: dict | None,
                attempt: int) -> Proposal:
        try:
            res = self.shared.call(
                self.client, model=model, max_tokens=max_tokens, system=system, user=user,
                effort=effort, temperature=temperature, output_schema=schema, agent=agent,
            )
        except FatalAPIError as exc:
            if exc.status_code == 400:      # a malformed request for this call only
                return Proposal(error=f"API error: {exc}", model=model)
            raise (exc.original if exc.original is not None else exc)
        except RetryableAPIError as exc:
            return Proposal(error=f"API error: {exc}", model=model)

        usage = res.usage
        prop = Proposal(
            stop_reason=res.stop_reason, model=res.model or model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cost_usd=estimate_cost_usd(res.model or model, usage),
            truncated=res.stop_reason == "max_tokens",
        )
        if res.stop_reason == "refusal":
            prop.error = f"model refused (category={res.refusal_category})"
            return prop
        prop.body, prop.gave_up, prop.notes, prop.salvaged = parse_proposal_text(res.text)
        return prop


class MockSource:
    """Scripted prover outputs from ``<stem>_<agent>_output.json`` /
    ``<agent>_output.json`` in a directory (a dict, or a list -- one entry per
    attempt, the last one repeated).  Free: no API, no usage.  An entry may
    carry ``"stop_reason": "max_tokens"`` to simulate a truncated reply."""

    def __init__(self, directory: str | Path, stem: str, agents: Sequence[str]):
        self.entries: list[dict] = []
        d = Path(directory)
        for agent in agents:
            for name in (f"{stem}_{agent}_output.json", f"{agent}_output.json"):
                p = d / name
                if p.is_file():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    self.entries = data if isinstance(data, list) else [data]
                    return

    def propose(self, *, attempt: int, model: str, **_: Any) -> Proposal:
        if not self.entries:
            return Proposal(error="mock: no lean formalizer output file found", model=model)
        entry = self.entries[min(attempt, len(self.entries)) - 1]
        stop = entry.get("stop_reason")
        src = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else entry
        body = src.get("proof_body")
        prop = Proposal(model=model, stop_reason=stop, truncated=stop == "max_tokens",
                        notes=src.get("notes"))
        if isinstance(body, str):
            prop.body, prop.gave_up = body, not body.strip()
        return prop


def make_live_client() -> Any:
    """``anthropic.Anthropic`` from ANTHROPIC_API_KEY (.env honoured like the
    pipelines' runners)."""
    from . import llm_client
    llm_client._load_env()  # noqa: SLF001
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (put it in .env or export it)")
    import anthropic
    return anthropic.Anthropic(api_key=key)


# ---------------------------------------------------------------------------
# Preparation shared by the run and the estimate
# ---------------------------------------------------------------------------

@dataclass
class Prepared:
    run_dir: Path
    result_path: Path
    stem: str
    result: dict
    ir: dict
    direction: str | None
    direction_source: str
    statement: Any
    snapshot: dict | None
    plan: Any
    reason: str | None = None       # why not formalizable


def prepare(run_dir: str | Path, spec: SystemSpec, *, stem: str | None = None,
            direction: str | None = None) -> Prepared:
    run_dir = Path(run_dir)
    result_path, stem = find_result_file(run_dir, stem)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    ir_path = run_dir / "input.json"
    if not ir_path.is_file():
        raise FileNotFoundError(f"{ir_path} not found")
    ir = json.loads(ir_path.read_text(encoding="utf-8"))

    source = "cli"
    if direction is None:
        direction, source = spec.pick_direction(result, ir)
    elif direction not in spec.directions:
        raise ValueError(f"--direction must be one of {spec.directions}, got {direction!r}")

    prep = Prepared(run_dir, result_path, stem, result, ir, direction, source,
                    None, None, None)
    if direction is None:
        prep.reason = "neither the verdict nor the hypothesis names a direction to prove"
        return prep
    try:
        statement, reason = spec.render_statement(ir, direction)
    except Exception as exc:  # noqa: BLE001 -- contract owned by lean_ir
        statement, reason = None, f"render_statement raised: {exc}"
    if statement is None:
        prep.reason = reason or "render_statement returned no statement for this ir/direction"
        return prep
    prep.statement = statement
    prep.snapshot = _statement_snapshot(statement)
    prep.plan = spec.build_plan(result, direction)
    return prep


def _agent_input(prep: Prepared, spec: SystemSpec) -> dict:
    return {
        "statement": prep.snapshot,
        "plan": prep.plan,
        "available_lemmas": list(spec.available_lemmas),
    }


def _model_price(model: str) -> dict:
    from .llm_client import _normalize_model_id  # noqa: PLC0415
    return MODEL_PRICING.get(_normalize_model_id(model)) or {}


def estimate_cost(run_dir: str | Path, spec: SystemSpec, settings: FormalizeSettings | None = None,
                  *, stem: str | None = None, direction: str | None = None) -> dict:
    """Pre-run cost estimate; no API, no Docker, no writes.

    ``expected_usd``: every allowed attempt at typical size (25k/12k output
    tokens for the first / a correction attempt); ``min_usd``: the first attempt
    only; ``max_usd``: every attempt plus the body-only attempt at the full
    ``max_tokens`` -- the hard ceiling.
    """
    settings = settings or FormalizeSettings.defaults(spec)
    prep = prepare(run_dir, spec, stem=stem, direction=direction)
    if prep.snapshot is None:
        return {"formalizable": False, "reason": prep.reason, "direction": prep.direction,
                "min_usd": 0.0, "expected_usd": 0.0, "max_usd": 0.0, "calls_max": 0,
                "first_model": settings.first_model, "retry_model": settings.retry_model}
    system_chars = len(spec.prompt_path.read_text(encoding="utf-8"))
    user_chars = len(json.dumps(_agent_input(prep, spec), ensure_ascii=False))
    in_first = int((system_chars + user_chars) / _CHARS_PER_TOKEN)
    in_retry = in_first + 3000          # errors + previous body

    def usd(model: str, tin: int, tout: int) -> float:
        p = _model_price(model)
        return (tin * (p.get("input") or 0.0) + tout * (p.get("output") or 0.0)) / 1_000_000

    n_retry = max(0, settings.retries)
    first_max = settings.max_tokens_for(settings.first_model)
    retry_max = settings.max_tokens_for(settings.retry_model)
    min_usd = usd(settings.first_model, in_first, _EXPECTED_OUT_FIRST)
    expected = min_usd + n_retry * usd(settings.retry_model, in_retry, _EXPECTED_OUT_RETRY)
    max_usd = (usd(settings.first_model, in_first, first_max)
               + (n_retry + 1) * usd(settings.retry_model, in_retry, retry_max))
    return {
        "formalizable": True,
        "direction": prep.direction,
        "direction_source": prep.direction_source,
        "first_model": settings.first_model,
        "retry_model": settings.retry_model,
        "retries": settings.retries,
        "max_tokens": settings.max_tokens,
        "calls_max": 1 + n_retry + 1,   # attempts + the one body-only attempt
        "input_tokens_est": in_first,
        "min_usd": round(min_usd, 4),
        "expected_usd": round(expected, 4),
        "max_usd": round(max_usd, 4),
        "assumptions": (f"typical output {_EXPECTED_OUT_FIRST}/{_EXPECTED_OUT_RETRY} tokens "
                        "(first/correction, thinking included); max = every call at max_tokens"),
    }


# ---------------------------------------------------------------------------
# Rendering of the block into md / html
# ---------------------------------------------------------------------------

def _fmt_cost(usage: Mapping | None) -> str:
    if not usage:
        return "-"
    cost = usage.get("estimated_cost_usd")
    return f"${cost:.4f}" if isinstance(cost, (int, float)) else "n/a"


def formalization_markdown(block: Mapping) -> str:
    lines = ["", "## Lean 4 formalization (R-Lean)", ""]
    lines.append(f"- status: **{block.get('status')}**")
    lines.append(f"- direction: `{block.get('direction')}`")
    if block.get("reason"):
        lines.append(f"- reason: {block['reason']}")
    models = block.get("models") or {}
    if models:
        lines.append(f"- models: first `{models.get('first')}`, corrections `{models.get('retry')}`")
    lines.append(f"- attempts: {len(block.get('attempts') or [])}, cost: {_fmt_cost(block.get('usage'))}")
    if block.get("axioms"):
        lines.append(f"- axioms: {', '.join(map(str, block['axioms']))}")
    stmt = block.get("statement") or {}
    if stmt.get("theorem_decl"):
        lines += ["", "Statement (generated from the IR):", "", "```lean"]
        for part in (*(stmt.get("imports") or []), stmt.get("alphabet_decl"),
                     stmt.get("language_decl"), stmt.get("theorem_decl")):
            if part:
                lines.append(str(part))
        lines.append("```")
    if block.get("proof_body"):
        lines += ["", "Proof body:", "", "```lean", str(block["proof_body"]), "```"]
    atts = block.get("attempts") or []
    if atts:
        lines += ["", "| # | model | result | tokens in/out | cost |", "|---|---|---|---|---|"]
        for a in atts:
            cost = a.get("cost_usd")
            lines.append(
                f"| {a.get('attempt')} | {a.get('model') or '-'} | {a.get('status')} | "
                f"{a.get('input_tokens', 0)}/{a.get('output_tokens', 0)} | "
                f"{'$%.4f' % cost if isinstance(cost, (int, float)) else '-'} |")
    if block.get("errors"):
        lines += ["", "Last errors:", ""]
        for e in block["errors"][:8]:
            text = e.get("data") if isinstance(e, dict) else e
            lines.append(f"- {str(text)[:300]}")
    lines.append("")
    return "\n".join(lines)


def formalization_html(block: Mapping) -> str:
    esc = _html.escape
    stmt = block.get("statement") or {}
    parts = [
        '<section class="tfl-lean-formalization" style="max-width:960px;margin:24px auto;'
        'padding:16px 20px;border:1px solid #8884;border-radius:8px;font-family:system-ui,sans-serif">',
        '<h2 style="margin-top:0">Lean 4 formalization (R-Lean)</h2>',
        f'<p>status: <b>{esc(str(block.get("status")))}</b> &middot; direction: '
        f'<code>{esc(str(block.get("direction")))}</code> &middot; attempts: '
        f'{len(block.get("attempts") or [])} &middot; cost: {esc(_fmt_cost(block.get("usage")))}</p>',
    ]
    if block.get("reason"):
        parts.append(f'<p>reason: {esc(str(block["reason"]))}</p>')
    if stmt.get("theorem_decl"):
        code = "\n".join(str(p) for p in (*(stmt.get("imports") or []), stmt.get("alphabet_decl"),
                                          stmt.get("language_decl"), stmt.get("theorem_decl")) if p)
        parts.append(f'<h3>Statement (generated from the IR)</h3><pre style="overflow:auto">'
                     f'<code>{esc(code)}</code></pre>')
    if block.get("proof_body"):
        parts.append(f'<h3>Proof body</h3><pre style="overflow:auto"><code>'
                     f'{esc(str(block["proof_body"]))}</code></pre>')
    atts = block.get("attempts") or []
    if atts:
        rows = "".join(
            f'<tr><td>{esc(str(a.get("attempt")))}</td><td>{esc(str(a.get("model") or "-"))}</td>'
            f'<td>{esc(str(a.get("status")))}</td>'
            f'<td>{a.get("input_tokens", 0)}/{a.get("output_tokens", 0)}</td></tr>' for a in atts)
        parts.append('<table style="border-collapse:collapse"><thead><tr><th>#</th><th>model</th>'
                     '<th>result</th><th>tokens in/out</th></tr></thead><tbody>' + rows + '</tbody></table>')
    parts.append("</section>")
    return "\n".join(parts)


def _inject_html(page: str, fragment: str) -> str:
    idx = page.lower().rfind("</body>")
    if idx == -1:
        return page + "\n" + fragment
    return page[:idx] + fragment + "\n" + page[idx:]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_formalization(
    run_dir: str | Path,
    spec: SystemSpec,
    *,
    settings: FormalizeSettings | None = None,
    direction: str | None = None,
    stem: str | None = None,
    client: Any = None,
    source: Any = None,
    force: bool = False,
    verbose: bool = False,
    progress: bool = True,
) -> dict:
    """Formalize the finished run in *run_dir*.  See the module docstring.

    *client* (an ``anthropic.Anthropic``-shaped object) or *source* (a
    prover-call object, e.g. :class:`MockSource`) supplies the model; exactly
    one is needed unless the run turns out not to be formalizable.

    Returns a summary dict: ``{"status", "direction", "proved", "skipped",
    "attempts", "usage", "files", "reason"}``.  Raises on a broken run dir or a
    fatal API error (after an ``error`` progress event).
    """
    settings = (settings or FormalizeSettings.defaults(spec))
    prep = prepare(run_dir, spec, stem=stem, direction=direction)
    result = prep.result
    old_block = _get_path(result, spec.block_path)
    old_block = old_block if isinstance(old_block, dict) else None

    if old_block and old_block.get("status") == "proved":
        if not force or not old_block.get("baseline"):
            return {"status": "proved", "direction": old_block.get("direction"), "proved": True,
                    "skipped": True, "attempts": len(old_block.get("attempts") or []),
                    "usage": old_block.get("usage"), "files": [],
                    "reason": "already proved; nothing to do (use --force to re-run)"}

    def log(msg: str) -> None:
        if verbose:
            print(f"  [{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

    tracker = UsageTracker()
    pw = ProgressWriter(prep.run_dir, system=spec.system, stem=prep.stem,
                        tracker=tracker, append=True) if progress else None

    def emit(event: str, node: str, payload: dict) -> None:
        if pw is not None:
            pw.emit(event, node, payload)

    try:
        block = _run_loop(prep, spec, settings, tracker, pw, emit, log,
                          client=client, source=source, old_block=old_block)
        if old_block and old_block.get("status") == "proved" and block.get("status") != "proved":
            # A forced re-run over a machine-checked proof that did not prove it
            # again must never cost the standing proof: keep the result files as
            # they are (block, verdict, confidence) and only report the attempt.
            summary = _keep_proved(prep, block, old_block, pw, tracker)
        else:
            summary = _finish(prep, spec, block, old_block, pw, tracker)
    except BaseException as exc:
        if pw is not None:
            pw.error(f"{type(exc).__name__}: {exc}", node="formalize", exc_type=type(exc).__name__)
        raise
    return summary


def _run_loop(prep: Prepared, spec: SystemSpec, settings: FormalizeSettings,
              tracker: UsageTracker, pw: ProgressWriter | None, emit: Callable, log: Callable,
              *, client: Any, source: Any, old_block: dict | None) -> dict:
    started = _now_iso()
    base_block: dict[str, Any] = {
        "status": "running",
        "direction": prep.direction,
        "direction_source": prep.direction_source,
        "mode": "separate",
        "models": {"first": settings.first_model, "retry": settings.retry_model},
        "settings": {"retries": settings.retries, "max_tokens": settings.max_tokens},
        "started_at": started,
    }
    emit("node_start", "formalize", {"direction": prep.direction,
                                     "first_model": settings.first_model,
                                     "retry_model": settings.retry_model,
                                     "retries": settings.retries})

    if prep.snapshot is None:
        return {**base_block, "status": "not_formalizable", "reason": prep.reason,
                "attempts": [], "errors": [], "axioms": [], "elapsed": 0.0,
                "finished_at": _now_iso()}

    base_block["statement"] = prep.snapshot

    if source is None:
        if client is None:
            raise RuntimeError("run_formalization needs a client or a source")
        # Same guard as the pipelines: a live call without Docker + the image
        # could never be checked, do not spend it.
        if not is_docker_available():
            return {**base_block, "status": "unavailable", "proof_body": None, "attempts": [],
                    "errors": [], "axioms": [], "elapsed": 0.0, "finished_at": _now_iso(),
                    "reason": "Docker with the tfl-lean4 image is not available; "
                              "the prover was not called"}
        source = LiveSource(client, tracker)

    system_prompt = spec.prompt_path.read_text(encoding="utf-8")
    base_input = _agent_input(prep, spec)

    attempts: list[dict] = []
    errors: list = []
    proof_body: str | None = None
    lean_code: str | None = None
    axioms: list = []
    status = "error"
    elapsed_total = 0.0
    max_attempts = 1 + max(0, settings.retries)
    role = "first"
    attempt_no = 0

    while True:
        attempt_no += 1
        model = settings.first_model if role == "first" else settings.retry_model
        agent = spec.agent if role == "first" else spec.retry_agent
        effort = spec.efforts.get(agent, "high")
        if role == "body_only":
            effort = "medium"
        agent_input = dict(base_input)
        if errors and role != "first":
            agent_input["errors"] = errors
            agent_input["previous_proof_body"] = proof_body
        if role == "body_only":
            agent_input["instruction"] = BODY_ONLY_INSTRUCTION
        user = json.dumps(agent_input, indent=2, ensure_ascii=False)

        log(f"prover attempt {attempt_no} ({role}) on {model}")
        emit("llm_call", agent, {"phase": "start", "agent": agent, "model": model,
                                 "attempt": attempt_no, "role": role})
        prop: Proposal = source.propose(
            agent=agent, model=model, effort=effort,
            max_tokens=settings.max_tokens_for(model), system=system_prompt, user=user,
            temperature=float(spec.temperatures.get(agent, 0.0)),
            schema=spec.output_schema, attempt=attempt_no,
        )
        rec: dict[str, Any] = {
            "attempt": attempt_no, "role": role, "model": prop.model or model,
            "stop_reason": prop.stop_reason, "input_tokens": prop.input_tokens,
            "output_tokens": prop.output_tokens, "cost_usd": prop.cost_usd,
        }
        if prop.notes:
            rec["notes"] = prop.notes
        attempts.append(rec)

        def snapshot_partial() -> None:
            if pw is not None:
                partial = {**base_block, "attempts": list(attempts), "errors": errors,
                           "proof_body": proof_body, "usage": _sum_usage(tracker)}
                pw.node_done(f"formalize_attempt_{attempt_no}", {"formalization": partial})

        # -- nothing usable came back ------------------------------------
        if prop.error:
            status, errors = "error", [prop.error]
            rec.update(status="api_error", errors=errors)
            snapshot_partial()
            break
        if prop.body is None and prop.truncated:
            rec.update(status="truncated",
                       errors=[f"response cut off at max_tokens={settings.max_tokens_for(model)} "
                               "without a proof body"])
            status = "error"
            if role == "body_only":
                errors = rec["errors"]
                snapshot_partial()
                break
            errors = rec["errors"]
            snapshot_partial()
            role = "body_only"      # exactly one body-only attempt, then stop
            continue
        if prop.gave_up:
            status = "error"
            errors = [f"prover gave up: {prop.notes}" if prop.notes else "prover gave up (empty proof_body)"]
            rec.update(status="gave_up", errors=errors)
            snapshot_partial()
            break
        if not prop.body:
            status = "error"
            errors = ["prover output had no usable proof_body (not the requested JSON?)"]
            rec.update(status="no_proof_body", errors=errors)
            snapshot_partial()
            if role == "body_only" or attempt_no >= max_attempts:
                break
            role = "retry"
            continue

        # -- compile ------------------------------------------------------
        proof_body = prop.body
        lean_code = compose_lean_file(prep.statement, proof_body)
        tc = check_lean_file(lean_code, timeout=spec.lean_timeout,
                             theorem_name=prep.snapshot["name"])
        elapsed_total += tc.get("elapsed", 0.0) or 0.0
        status = tc.get("status", "error")
        errors = tc.get("errors", [])
        axioms = tc.get("axioms", [])
        rec.update(status=status, errors=errors)
        if prop.salvaged:
            rec["salvaged"] = True
        log(f"check_lean_file: {status}")
        if status == "proved":
            errors = []
            snapshot_partial()
            break
        snapshot_partial()
        if status in _TERMINAL_STATUSES or role == "body_only" or attempt_no >= max_attempts:
            break
        role = "retry"

    return {
        **base_block,
        "status": status,
        "proof_body": proof_body,
        "lean_code": lean_code,
        "attempts": attempts,
        "errors": errors,
        "axioms": axioms,
        "elapsed": round(elapsed_total, 2),
        "finished_at": _now_iso(),
    }


def _keep_proved(prep: Prepared, block: dict, old_block: dict,
                 pw: ProgressWriter | None, tracker: UsageTracker) -> dict:
    """A forced re-run over a ``proved`` block that ended in anything else:
    nothing in ``<stem>_result.*`` is touched.  The attempt is only reported
    (progress events, cost, summary)."""
    result = prep.result
    usage = _sum_usage(tracker)
    reason = (f"re-run ended {block.get('status')!r}; the earlier machine-checked "
              "proof was kept unchanged")
    if pw is not None:
        pw.merge({"formalization": old_block})      # the snapshot no longer shows the failed attempt
        pw.emit("formalization", "formalize", {
            "status": "proved", "proved": True, "direction": old_block.get("direction"),
            "attempts": len(block.get("attempts") or []), "elapsed": block.get("elapsed"),
            "reason": reason, "rerun_status": block.get("status"), "kept_proved": True,
            "errors_count": len(block.get("errors") or []),
        })
        pw.emit("node_done", "formalize", {"status": block.get("status"), "gated": False,
                                           "kept_proved": True})
        pw.done(result, files=[])
    return {
        "status": "proved",
        "direction": old_block.get("direction"),
        "proved": True,
        "skipped": False,
        "kept_proved": True,
        "rerun_status": block.get("status"),
        "attempts": len(block.get("attempts") or []),
        "usage": usage,
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "files": [],
        "reason": reason,
    }


def _finish(prep: Prepared, spec: SystemSpec, block: dict, old_block: dict | None,
            pw: ProgressWriter | None, tracker: UsageTracker) -> dict:
    result = prep.result

    # Pre-Lean values: the ones a previous separate run saved, else the current
    # ones (a non-proved block never touched the result).
    baseline = (old_block or {}).get("baseline") if old_block else None
    if not baseline:
        baseline = snapshot_baseline(result, spec.baseline_paths)
    restore_baseline(result, baseline)
    block["baseline"] = baseline
    block["usage"] = _sum_usage(tracker)

    proved = block.get("status") == "proved"
    gated = spec.apply_gate(result, block) if proved else False
    if gated and spec.refresh is not None:
        spec.refresh(result)
    _set_path(result, spec.block_path, block)

    # Files: JSON atomically, md/html re-rendered by the system's renderer with
    # the Lean section appended.
    files: list[str] = []
    _atomic_write(prep.result_path, json.dumps(result, indent=2, ensure_ascii=False))
    files.append(prep.result_path.name)
    for fmt in ("md", "html"):
        path = prep.run_dir / f"{prep.stem}_result.{fmt}"
        try:
            spec.render_file(result, str(path), fmt)
            text = path.read_text(encoding="utf-8")
            if fmt == "md":
                text = text.rstrip("\n") + "\n" + formalization_markdown(block)
            else:
                text = _inject_html(text, formalization_html(block))
            _atomic_write(path, text)
            files.append(path.name)
        except Exception as exc:  # noqa: BLE001 -- a renderer bug must not lose the JSON
            print(f"Renderer ({fmt}) failed: {exc}", file=sys.stderr)

    if pw is not None:
        pw.merge({"formalization": block})          # the snapshot no longer says "running"
        pw.formalization({
            "status": block.get("status"), "direction": block.get("direction"),
            "attempts": len(block.get("attempts") or []), "elapsed": block.get("elapsed"),
            "proved": proved, "errors": block.get("errors") or [],
        }, node="formalize")
        if gated:
            pw.verdict(result.get("verdict"), result.get("confidence"),
                       status=result.get("status"), final=True, node="formalize")
        pw.emit("node_done", "formalize", {"status": block.get("status"), "gated": gated})
        pw.done(result, files=files)

    return {
        "status": block.get("status"),
        "direction": block.get("direction"),
        "proved": proved,
        "skipped": False,
        "attempts": len(block.get("attempts") or []),
        "usage": block.get("usage"),
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "files": files,
        "reason": block.get("reason"),
    }


# ---------------------------------------------------------------------------
# CLI (shared by the three thin wrappers)
# ---------------------------------------------------------------------------

def build_parser(spec: SystemSpec, prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog,
        description="Lean 4 formalization of a finished run (R-Lean): renders the statement "
                    "from input.json, runs the prover loop, appends the formalization block "
                    "to <stem>_result.{json,md,html}.")
    p.add_argument("run_dir", help="run directory with <stem>_result.json and input.json")
    p.add_argument("--stem", default=None, help="result stem (default: input / the only *_result.json)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="call the Anthropic API (spends money)")
    mode.add_argument("--mock", metavar="DIR", default=None,
                      help="scripted prover outputs (<stem>_<agent>_output.json) instead of the API")
    p.add_argument("--estimate", action="store_true",
                   help="print the cost estimate as JSON and exit (no API, no Docker, no writes)")
    p.add_argument("--first-model", default=None,
                   help=f"model of the first attempt (default {spec.default_first_model})")
    p.add_argument("--retry-model", default=None,
                   help=f"model of the corrections (default {spec.default_retry_model})")
    p.add_argument("--retries", type=int, default=None,
                   help=f"number of corrections after the first attempt (default {spec.default_retries})")
    p.add_argument("--max-tokens", type=int, default=None,
                   help=f"output limit per call (default {spec.default_max_tokens})")
    p.add_argument("--direction", default=None, choices=list(spec.directions),
                   help="direction to prove (default: the result's verdict, else the hypothesis)")
    p.add_argument("--force", action="store_true", help="re-run over an already proved block")
    p.add_argument("--no-progress", action="store_true", help="do not write progress.jsonl / partial_result.json")
    p.add_argument("--verbose", action="store_true")
    return p


def cli_main(spec: SystemSpec, argv: Sequence[str] | None = None, prog: str = "formalize") -> int:
    args = build_parser(spec, prog).parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    defaults = FormalizeSettings.defaults(spec)
    override = bool(os.environ.get("TFL_MODEL_OVERRIDE", "").strip())
    settings = FormalizeSettings(
        defaults.first_model if override or not args.first_model else args.first_model,
        defaults.retry_model if override or not args.retry_model else args.retry_model,
        defaults.retries if args.retries is None else max(0, args.retries),
        defaults.max_tokens if args.max_tokens is None else args.max_tokens,
    )

    try:
        if args.estimate:
            est = estimate_cost(args.run_dir, spec, settings, stem=args.stem, direction=args.direction)
            print(json.dumps(est, indent=2, ensure_ascii=False))
            return 0

        if not args.live and not args.mock:
            print("Error: choose --live (API, spends money), --mock DIR, or --estimate",
                  file=sys.stderr)
            return 2
        client = source = None
        if args.mock:
            run_dir = Path(args.run_dir)
            stem = find_result_file(run_dir, args.stem)[1]
            source = MockSource(args.mock, stem, (spec.agent, spec.retry_agent))
        else:
            client = make_live_client()
        summary = run_formalization(
            args.run_dir, spec, settings=settings, direction=args.direction, stem=args.stem,
            client=client, source=source, force=args.force, verbose=args.verbose,
            progress=not args.no_progress,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
