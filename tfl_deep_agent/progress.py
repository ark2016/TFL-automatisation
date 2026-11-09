"""Structured run events for the TFL Lab live view: ``<run_dir>/progress.jsonl``.

One JSON object per line, appended by the agent process (``hitl._segment``
attaches a :class:`ProgressEmitter` to the run's budget and evidence ledgers)
and only read by ``ui_server/deep_routes.py``, which polls the file by line
index (``?after=N``, the same contract as the pipelines' ``progress.jsonl``).
A HITL resume runs in a new process and appends to the same file, so the file
is the whole history of the run over all its segments.

Every line has ``event``, ``ts`` (local ISO time), ``run_id`` and ``segment``
(1-based index of the process segment). Lines written while a budget is
attached also carry the cumulative ``usage`` (``calls``, ``input_tokens``,
``output_tokens``, ``estimated_cost_usd``: the server's generic usage shape)
and ``budget`` (``spent_usd``, ``limit_usd``, ``reserve_usd``, ``wrap_up``,
``exhausted``).

Events:

* ``segment_start`` -- ``mode``, ``template``, ``resumed_with`` (the review
  that resumed this segment, or ``None``), ``hitl`` (the points);
* ``todos`` -- ``todos`` (the plan from ``write_todos``: ``content`` /
  ``status``), ``role``;
* ``tool_call`` -- one tool call from the evidence ledger: ``id`` (the
  evidence id a report cites), ``tool``, ``ok``, ``trust`` (set by code from the
  tool result, never by the model), ``trust_reason``, ``error``,
  ``error_kind``, ``role``, ``scope`` (budget node path), ``model``,
  ``parent_call``, ``tool_call_id``, ``elapsed_s``, ``args_preview`` and, for
  ``task``, ``subagent_type``;
* ``model_call`` -- one charged model call: ``node``, ``role``, ``model``,
  ``usd``, tokens, ``outcome``;
* ``subagent_start`` / ``subagent_end`` -- a budget child scope (``node``,
  ``kind``: subagent / branch / workflow ..., ``allocated_usd`` /
  ``returned_usd``): the subagent / workflow tree;
* ``budget_wrap_up`` / ``budget_refused`` / ``subagent_refused`` -- the budget
  middleware's signals;
* orchestration events as the P4 context emits them (``spawn_start``,
  ``spawn_end``, ``workflow_start``, ``workflow_step_start``, ...);
* ``hitl_awaiting`` -- the run stops for a human: ``point``
  (formalization / report / execute / budget), ``interrupt_id``, ``card`` (the
  text of ``hitl.md``), ``draft`` (HITL 2: verdict / trust / cap of the draft);
* ``hitl_decision`` -- a decision on a stop: ``point``, ``decision``
  (approved / edited / rejected / auto_approved; at the budget stop extended /
  declined, with ``add_usd`` and ``new_limit_usd``), ``source``, ``comment``,
  ``edited_fields``, ``interrupt_id``;
* ``user_message`` -- a human's follow-up to a finished run: ``text``;
* ``assistant_message`` -- the agent's text answer to a follow-up: ``text``, and
  ``truncated: true`` when it was cut to the limit;
* ``followup_unanswered`` -- a follow-up ended with no model call, because the human
  did not extend the budget; the report stays: ``text``, the chat's note;
* ``followup_abandoned`` -- a follow-up did not finish: ``status`` (the run is back
  to it, with the previous report) and ``error`` (why; ``null``: it was cancelled);
* ``segment_end`` -- the end signal of a segment: ``status`` of the segment
  (``awaiting_human`` or the final status) and ``error``;
* ``done`` -- a final report state to display: ``status`` (submitted / incomplete /
  budget_exhausted / error), ``verdict``, ``trust``, ``confidence_cap``, ``error``.
  It is not the end signal: that is ``segment_end``, which comes right before it.
  After a follow-up it comes only when the report changed or the status is not
  submitted.

The emitter never raises into the run: a failed write or a broken record is
dropped (the agent's accounting and the evidence ledger stay authoritative).
Pure stdlib.
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .run_store import MAX_MESSAGE_CHARS

PROGRESS_FILE = "progress.jsonl"
ARGS_PREVIEW_CHARS = 300
CARD_CHARS = 20_000

#: budget ledger event -> progress event
_BUDGET_EVENTS = {
    "allocate": "subagent_start",
    "close": "subagent_end",
    "wrap_up": "budget_wrap_up",
    "refused": "budget_refused",
    "subagent_refused": "subagent_refused",
}


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _preview(obj: Any, limit: int = ARGS_PREVIEW_CHARS) -> str | None:
    if obj is None:
        return None
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ProgressEmitter:
    """Appends events to ``<run_dir>/progress.jsonl`` (thread-safe)."""

    def __init__(self, run_dir: str | Path, *, run_id: str | None = None, segment: int | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / PROGRESS_FILE
        self.run_id = run_id
        self.segment = segment
        self.budget: Any = None
        self.evidence: Any = None
        self._lock = threading.Lock()
        self._listeners: list[tuple[Any, Any]] = []

    # -- wiring ----------------------------------------------------------------

    def attach(self, *, budget: Any = None, evidence: Any = None) -> ProgressEmitter:
        """Listen to a ``BudgetLedger`` and an ``EvidenceLedger`` (their
        ``listeners`` lists)."""
        if budget is not None:
            self.budget = budget
            self._listen(budget, self._on_budget)
        if evidence is not None:
            self.evidence = evidence
            self._listen(evidence, self._on_evidence)
        return self

    def _listen(self, owner: Any, fn: Any) -> None:
        listeners = getattr(owner, "listeners", None)
        if isinstance(listeners, list):
            listeners.append(fn)
            self._listeners.append((listeners, fn))

    def detach(self) -> None:
        for listeners, fn in self._listeners:
            try:
                listeners.remove(fn)
            except ValueError:
                pass
        self._listeners.clear()

    # -- writing ---------------------------------------------------------------

    def usage(self) -> dict[str, Any] | None:
        """Cumulative usage of the run (the server's generic usage shape)."""
        b = self.budget
        if b is None:
            return None
        try:
            with b.lock:
                records = list(b.records)
                spent = float(b.spent_usd)
            return {
                "calls": len(records),
                "input_tokens": sum(int(r.input_tokens or 0) + int(r.cache_read_tokens or 0)
                                    + int(r.cache_write_tokens or 0) for r in records),
                "output_tokens": sum(int(r.output_tokens or 0) for r in records),
                "estimated_cost_usd": round(spent, 6),
            }
        except Exception:  # noqa: BLE001 - a view, never an error
            return None

    def budget_view(self) -> dict[str, Any] | None:
        b = self.budget
        if b is None:
            return None
        try:
            root = b.root
            return {"spent_usd": round(float(b.spent_usd), 6), "limit_usd": float(root.limit),
                    "reserve_usd": float(root.reserve), "wrap_up": bool(root.in_wrap_up()),
                    "exhausted": bool(b.exhausted)}
        except Exception:  # noqa: BLE001
            return None

    def emit(self, event: str, **fields: Any) -> dict[str, Any] | None:
        """Append one event; returns it (``None`` when it could not be written)."""
        line: dict[str, Any] = {"event": event, "ts": _now(), "run_id": self.run_id, "segment": self.segment}
        line.update(fields)
        usage = self.usage()
        if usage is not None:
            line["usage"] = usage
            line["budget"] = self.budget_view()
        try:
            text = json.dumps(line, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return None
        try:
            with self._lock:
                self.run_dir.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(text + "\n")
                    fh.flush()
        except OSError:
            return None
        return line

    # -- listeners -------------------------------------------------------------

    def _on_budget(self, ev: Mapping[str, Any]) -> None:
        ev = dict(ev)
        kind = ev.pop("event", None)
        ev.pop("ts", None)
        if kind == "call":
            self.emit("model_call", **ev)
        elif kind in _BUDGET_EVENTS:
            name = _BUDGET_EVENTS[kind]
            if kind == "allocate":
                ev["allocated_usd"] = ev.pop("usd", None)
            self.emit(name, **ev)
        elif isinstance(kind, str) and kind:
            self.emit(kind, **ev)  # orchestration events (spawn_* / workflow_*)

    def _on_evidence(self, ev: Mapping[str, Any]) -> None:
        if ev.get("event") != "evidence":
            return
        rec = None
        try:
            rec = self.evidence.get(ev.get("id")) if self.evidence is not None else None
        except Exception:  # noqa: BLE001
            rec = None
        args = _get(rec, "args") if rec is not None else None
        tool = ev.get("tool")
        if tool == "write_todos":
            todos = args.get("todos") if isinstance(args, Mapping) else None
            if isinstance(todos, list):
                self.emit("todos", todos=[
                    {"content": str(_get(t, "content", "")), "status": str(_get(t, "status", "pending"))}
                    for t in todos], role=ev.get("role"), tool_call_id=ev.get("tool_call_id"))
        fields = {k: ev.get(k) for k in ("id", "tool", "ok", "trust", "trust_reason", "error", "error_kind",
                                          "role", "scope", "model", "tool_call_id", "status", "cost_usd")}
        fields["parent_call"] = _get(rec, "parent_call") if rec is not None else None
        fields["elapsed_s"] = _get(rec, "elapsed_s") if rec is not None else None
        fields["args_preview"] = _preview(args)
        if tool == "task" and isinstance(args, Mapping):
            fields["subagent_type"] = args.get("subagent_type")
        self.emit("tool_call", **fields)

    # -- runner hooks (hitl._segment) -------------------------------------------

    def segment_start(self, run: Mapping[str, Any], segment: Mapping[str, Any]) -> None:
        resumed = segment.get("resumed_with")
        self.emit("segment_start", mode=run.get("mode"), template=run.get("template"),
                  hitl=run.get("hitl"), resumed_with=dict(resumed) if isinstance(resumed, Mapping) else None)
        if isinstance(resumed, Mapping):
            self.hitl_decision(resumed)

    def hitl_awaiting(self, info: Any, run: Mapping[str, Any] | None = None) -> None:
        card = str(_get(info, "card") or "")
        self.emit("hitl_awaiting", point=_get(info, "point"), interrupt_id=_get(info, "interrupt_id"),
                  card=card[:CARD_CHARS], draft=(run or {}).get("draft") if _get(info, "point") == "report" else None)

    def hitl_decision(self, review: Mapping[str, Any]) -> None:
        money = {k: review[k] for k in ("add_usd", "new_limit_usd") if k in review}  # the budget stop
        self.emit("hitl_decision", point=review.get("point"), decision=review.get("decision"),
                  source=review.get("source"), comment=review.get("comment"),
                  edited_fields=list(review.get("edited_fields") or []), interrupt_id=review.get("interrupt_id"),
                  **money)

    def user_message(self, text: str) -> None:
        """The human's follow-up message: a user bubble in the chat."""
        self.emit("user_message", text=str(text)[:MAX_MESSAGE_CHARS])

    def assistant_message(self, text: str) -> None:
        """The agent's text answer to a follow-up, cut to the limit (``truncated``)."""
        text = str(text)
        if len(text) > MAX_MESSAGE_CHARS:
            self.emit("assistant_message", text=text[:MAX_MESSAGE_CHARS], truncated=True)
        else:
            self.emit("assistant_message", text=text)

    def segment_end(self, status: str | None, error: str | None = None) -> None:
        self.emit("segment_end", status=status, error=error)

    def done(self, result: Mapping[str, Any] | None, error: str | None = None) -> None:
        result = result or {}
        report = result.get("report") if isinstance(result.get("report"), Mapping) else {}
        status = result.get("status")
        self.segment_end(status, error)
        self.emit("done", status=status, verdict=report.get("verdict"),
                  language_class=report.get("language_class"), trust=result.get("trust"),
                  confidence_cap=result.get("confidence_cap"), error=error)


class _NullEmitter:
    """Stands in when the emitter cannot be built (never breaks a run)."""

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


def open_emitter(run_dir: str | Path, run: Mapping[str, Any], segment: Mapping[str, Any], *,
                 budget: Any = None, evidence: Any = None) -> Any:
    """An emitter for one process segment of ``run``, attached to its ledgers,
    with ``segment_start`` already written. Never raises."""
    try:
        em = ProgressEmitter(run_dir, run_id=run.get("run_id"), segment=len(run.get("segments") or []) or None)
        em.attach(budget=budget, evidence=evidence)
        em.segment_start(run, segment)
        return em
    except Exception:  # noqa: BLE001
        return _NullEmitter()


def read_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """Every complete event line of ``<run_dir>/progress.jsonl`` (tests, tools)."""
    p = Path(run_dir) / PROGRESS_FILE
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


__all__ = ["PROGRESS_FILE", "ProgressEmitter", "open_emitter", "read_events"]
