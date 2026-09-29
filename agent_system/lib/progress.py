"""Run progress contract shared by every pipeline (REG / CFL / DCFL / LL).

A pipeline that is given a :class:`ProgressWriter` writes its intermediate
results into the run directory *while it runs*, so a UI (TFL Lab) can show
the parts as they appear, and a crashed / cancelled run still leaves what it
managed to compute.

Files in ``run_dir``
--------------------
``progress.jsonl``
    One JSON object per line, one line per event::

        {"ts": "2026-09-29T10:00:00.123+00:00",
         "event": "node_start" | "node_done" | "llm_call" | "verdict"
                  | "formalization" | "done" | "error",
         "node": "run_reasoning_node",          # "" when not node-bound
         "payload": {...},                      # event-specific, small
         "usage": {"calls": 3, "input_tokens": 1200, "output_tokens": 800,
                   "estimated_cost_usd": 0.0421}}   # CUMULATIVE for the run

    Payloads:

    * ``node_start``: ``{}``; specialist fan-out adds ``{"agent": name}``.
    * ``node_done``: ``{"elapsed_s", "updated": [state keys], "summary": {...}}``.
    * ``llm_call``: ``{"phase": "start", "agent"}`` right before a live call,
      ``{"phase": "done", "agent", "model", "input_tokens", "output_tokens",
      "estimated_cost_usd"}`` after it (that call's own numbers; ``usage`` on
      the event line is the running total).
    * ``verdict``: ``{"verdict", "confidence", "status", "final": bool}`` --
      ``final=False`` after each reasoning round, ``True`` once for the
      assembled result.
    * ``formalization``: ``{"status", "direction", "attempts", ...}`` from the
      Lean step (whether run in the pipeline or as the separate entry).
    * ``done``: ``{"verdict", "status", "confidence", "files": [...]}`` --
      written by the CLI after the final ``<stem>_result.*`` are on disk.
    * ``error``: ``{"message", "type"}`` (``node`` set when a node raised).

    ``usage.estimated_cost_usd`` is ``0.0`` when nothing was called (mock),
    ``None`` only when a live model has no confirmed price.

``partial_result.json``
    Current snapshot, atomically replaced after every ``node_done``::

        {"system", "stem", "status": "running" | "done" | "error",
         "updated_at", "last_node", "nodes_done": [names in order],
         "verdict", "confidence", "usage": {...},
         "state": {<json-safe pipeline state accumulated so far>},
         "result": {...}   # only once the final result exists}

The final ``<stem>_result.{json,md,html}`` are written exactly as before.

Every method of :class:`ProgressWriter` swallows its own I/O errors: progress
reporting must never break a run.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROGRESS_FILENAME = "progress.jsonl"
PARTIAL_FILENAME = "partial_result.json"

EVENTS = ("node_start", "node_done", "llm_call", "verdict",
          "formalization", "done", "error")

# State keys that are not data (runners, callables, the writer itself).
_SKIP_STATE_KEYS = frozenset({
    "progress", "mock_runner", "agent_runner", "oracle_fn", "verbose",
    "_specialist_name",
})
# LangGraph reducers (operator.add) -- node updates are appended, not replaced.
_ACCUMULATING_KEYS = frozenset({"specialist_outputs", "errors", "call_cap_notes"})

_ZERO_USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
               "estimated_cost_usd": 0.0}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def jsonable(obj: Any, _depth: int = 0) -> Any:
    """Best-effort JSON-safe copy: callables/unknown objects are dropped
    (``None``) or stringified, sets/tuples become lists."""
    if _depth > 40:
        return None
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if callable(v) and not isinstance(v, type):
                continue
            out[str(k)] = jsonable(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v, _depth + 1) for v in obj]
    if callable(obj):
        return None
    return str(obj)


def _dig_verdict(d: Any) -> tuple[Any, Any]:
    """(verdict, confidence) from an agent/result dict, tolerating the
    ``{"evidence": {...}}`` wrapper the agents use."""
    if not isinstance(d, dict):
        return None, None
    ev = d.get("evidence")
    inner = ev if isinstance(ev, dict) else {}
    verdict = d.get("verdict") or inner.get("verdict")
    conf = d.get("confidence")
    if conf is None:
        conf = inner.get("confidence")
    return verdict, conf


def extract_verdict(result: Any) -> tuple[Any, Any]:
    """(verdict, confidence) of an assembled pipeline result, any system.

    Mirrors ``agent_system.orchestrator._result_verdict``: an unresolved
    contradiction (partial + ``verdict_gate.contradiction``) yields no
    verdict."""
    if not isinstance(result, dict):
        return None, None
    ev = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    gate = ev.get("verdict_gate") or result.get("verdict_gate") or {}
    if (result.get("status") == "partial" and isinstance(gate, dict)
            and gate.get("contradiction")):
        return None, result.get("confidence")
    verdict, conf = _dig_verdict(result)
    if verdict is None:
        verdict, conf2 = _dig_verdict(ev.get("reasoning"))
        conf = conf if conf is not None else conf2
    if verdict is None:
        hyp = ev.get("hypothesis")
        if isinstance(hyp, dict):
            verdict = hyp.get("hypothesis")
    return verdict, conf


def _summary(update: dict) -> dict:
    """Tiny scalar digest of a node's state update (for the event line)."""
    out: dict[str, Any] = {}
    for key, value in update.items():
        if key in _SKIP_STATE_KEYS or not isinstance(value, dict):
            if key in ("retry_round", "inversions_done", "oracle_ok", "lang_kind") \
                    and isinstance(value, (bool, int, str)):
                out[key] = value
            continue
        digest = {k: value[k] for k in ("status", "verdict", "confidence", "hypothesis")
                  if isinstance(value.get(k), (str, int, float, bool))}
        if digest:
            out[key] = digest
    return out


# ---------------------------------------------------------------------------
# ProgressWriter
# ---------------------------------------------------------------------------

class ProgressWriter:
    """Append-only event log + atomic partial snapshot for one run.

    Thread-safe (specialists of one pipeline run in parallel). ``tracker`` is
    the run's :class:`agent_system.lib.llm_client.UsageTracker`; when attached
    (:meth:`attach_tracker`) every finished LLM call becomes an ``llm_call``
    event and every event carries the cumulative usage.

    ``append=True`` continues an existing ``progress.jsonl`` (used by the
    separate formalization entry over a finished run): the cumulative usage
    then starts from the last recorded value instead of zero.
    """

    def __init__(self, run_dir: str | Path, *, system: str = "", stem: str = "",
                 tracker: Any = None, append: bool = False) -> None:
        self.run_dir = Path(run_dir)
        self.system = system
        self.stem = stem
        self._lock = threading.RLock()
        self._tracker: Any = None
        self._listener: Callable[[dict], None] | None = None
        self._usage_base = dict(_ZERO_USAGE)
        self._acc: dict[str, Any] = {}
        self._nodes_done: list[str] = []
        self._last_node = ""
        self._verdict: tuple[Any, Any] = (None, None)
        self._result: dict | None = None
        self._status = "running"
        self._last_usage = dict(_ZERO_USAGE)
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if append:
                self._usage_base = self._last_recorded_usage()
                self._last_usage = dict(self._usage_base)
                prev = read_partial(self.run_dir)
                if isinstance(prev, dict):
                    self._acc = dict(prev.get("state") or {})
                    self._nodes_done = list(prev.get("nodes_done") or [])
                    self._result = prev.get("result")
            else:
                self.progress_path.write_text("", encoding="utf-8")
        except OSError:
            pass
        if tracker is not None:
            self.attach_tracker(tracker)

    # -- paths ---------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    @property
    def progress_path(self) -> Path:
        return self.run_dir / PROGRESS_FILENAME

    @property
    def partial_path(self) -> Path:
        return self.run_dir / PARTIAL_FILENAME

    @classmethod
    def for_cli(cls, save_dir: str | None, enabled: bool | None, *,
                system: str, stem: str = "") -> "ProgressWriter | None":
        """CLI helper: ``--progress`` (``enabled=None``) defaults to on iff
        ``--save DIR`` was given; ``--no-progress`` (``False``) turns it off."""
        if not save_dir or enabled is False:
            return None
        return cls(save_dir, system=system, stem=stem)

    # -- usage ---------------------------------------------------------------

    def attach_tracker(self, tracker: Any) -> None:
        """Watch `tracker`: one ``llm_call`` event per recorded call."""
        self.detach_tracker()
        add = getattr(tracker, "add_listener", None)
        if add is None:
            self._tracker = tracker
            return
        self._tracker = tracker
        self._listener = self._on_llm_call
        add(self._listener)

    def detach_tracker(self) -> None:
        tracker, listener = self._tracker, self._listener
        self._listener = None
        if tracker is not None and listener is not None:
            remove = getattr(tracker, "remove_listener", None)
            if remove is not None:
                try:
                    remove(listener)
                except Exception:  # noqa: BLE001
                    pass

    def usage_snapshot(self) -> dict:
        """Cumulative usage (base from an appended run + tracker totals)."""
        cur = dict(_ZERO_USAGE)
        tracker = self._tracker
        if tracker is not None:
            try:
                d = tracker.as_dict()
                cur = {
                    "calls": int(d.get("calls") or 0),
                    "input_tokens": int(d.get("input_tokens") or 0),
                    "output_tokens": int(d.get("output_tokens") or 0),
                    "estimated_cost_usd": d.get("estimated_cost_usd"),
                }
                if cur["calls"] == 0 and cur["estimated_cost_usd"] is None:
                    cur["estimated_cost_usd"] = 0.0
            except Exception:  # noqa: BLE001
                cur = dict(self._last_usage)
        base = self._usage_base
        cost = cur["estimated_cost_usd"]
        base_cost = base.get("estimated_cost_usd")
        total_cost = None if (cost is None or base_cost is None) else round(cost + base_cost, 6)
        out = {
            "calls": cur["calls"] + int(base.get("calls") or 0),
            "input_tokens": cur["input_tokens"] + int(base.get("input_tokens") or 0),
            "output_tokens": cur["output_tokens"] + int(base.get("output_tokens") or 0),
            "estimated_cost_usd": total_cost,
        }
        return out

    def _last_recorded_usage(self) -> dict:
        events = read_events(self.run_dir)
        for ev in reversed(events):
            u = ev.get("usage")
            if isinstance(u, dict):
                return {**_ZERO_USAGE, **u}
        return dict(_ZERO_USAGE)

    def _on_llm_call(self, call: dict) -> None:
        try:
            self.emit("llm_call", call.get("agent") or "", {"phase": "done", **call})
        except Exception:  # noqa: BLE001
            pass

    # -- events --------------------------------------------------------------

    def emit(self, event: str, node: str = "", payload: dict | None = None) -> dict | None:
        """Append one event line; returns the event dict (None on I/O error)."""
        with self._lock:
            try:
                usage = self.usage_snapshot()
                self._last_usage = usage
                ev = {"ts": _now(), "event": event, "node": node or "",
                      "payload": jsonable(payload or {}), "usage": usage}
                with open(self.progress_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                return ev
            except Exception:  # noqa: BLE001
                return None

    def node_start(self, node: str, payload: dict | None = None) -> None:
        self.emit("node_start", node, payload)

    def node_done(self, node: str, update: Any = None, *, elapsed: float | None = None,
                  payload: dict | None = None) -> None:
        """Record a finished node: event + merge its update + snapshot."""
        try:
            upd = update if isinstance(update, dict) else {}
            body: dict[str, Any] = {}
            if elapsed is not None:
                body["elapsed_s"] = round(elapsed, 3)
            body["updated"] = [k for k in upd if k not in _SKIP_STATE_KEYS]
            body["summary"] = _summary(upd)
            if payload:
                body.update(payload)
            with self._lock:
                self.emit("node_done", node, body)
                self.merge(upd)
                self._nodes_done.append(node)
                self._last_node = node
                self.write_partial()
        except Exception:  # noqa: BLE001
            pass

    def llm_start(self, agent: str) -> None:
        self.emit("llm_call", agent, {"phase": "start", "agent": agent})

    def verdict(self, verdict: Any, confidence: Any = None, *, status: str | None = None,
                final: bool = False, node: str = "") -> None:
        with self._lock:
            self._verdict = (verdict, confidence)
            self.emit("verdict", node, {"verdict": verdict, "confidence": confidence,
                                        "status": status, "final": final})

    def formalization(self, info: Any, node: str = "formalize_node") -> None:
        """`info`: the formalization block (or any dict); only its small
        scalar fields go on the event line, the full block lives in the
        result / snapshot."""
        try:
            keys = ("status", "direction", "attempts", "sorry_count", "proved",
                    "elapsed", "model", "reason", "errors_count")
            src = info if isinstance(info, dict) else {}
            body = {k: src[k] for k in keys if k in src}
            if isinstance(src.get("errors"), list):
                body["errors_count"] = len(src["errors"])
            self.emit("formalization", node, body)
        except Exception:  # noqa: BLE001
            pass

    def error(self, message: str, *, node: str = "", exc_type: str = "") -> None:
        with self._lock:
            self.emit("error", node, {"message": str(message)[:2000], "type": exc_type})
            self._status = "error"
            self.write_partial()

    def done(self, result: dict | None = None, *, files: list[str] | None = None) -> None:
        """Final event; call it AFTER the final ``<stem>_result.*`` are on
        disk. Also stamps the snapshot ``status: "done"`` with the result."""
        try:
            verdict, conf = extract_verdict(result)
            with self._lock:
                if result is not None:
                    self._result = jsonable(result)
                self._status = "done"
                self._verdict = (verdict if verdict is not None else self._verdict[0],
                                 conf if conf is not None else self._verdict[1])
                self.emit("done", "", {
                    "verdict": verdict, "confidence": conf,
                    "status": (result or {}).get("status") if isinstance(result, dict) else None,
                    "files": list(files or []),
                })
                self.write_partial()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.detach_tracker()

    # -- snapshot ------------------------------------------------------------

    def merge(self, update: dict) -> None:
        """Fold a node's state update into the accumulated snapshot state."""
        with self._lock:
            for key, value in update.items():
                if key in _SKIP_STATE_KEYS:
                    continue
                safe = jsonable(value)
                if key == "result" and isinstance(safe, dict) and safe:
                    self._result = safe
                    v, c = extract_verdict(safe)
                    if v is not None or c is not None:
                        self._verdict = (v, c)
                    continue
                if key in _ACCUMULATING_KEYS and isinstance(safe, list):
                    cur = self._acc.get(key)
                    self._acc[key] = (cur if isinstance(cur, list) else []) + safe
                else:
                    self._acc[key] = safe
                if key == "reasoning_output":
                    v, c = _dig_verdict(safe)
                    if v is not None:
                        self._verdict = (v, c)

    def snapshot(self) -> dict:
        with self._lock:
            snap: dict[str, Any] = {
                "system": self.system,
                "stem": self.stem,
                "status": self._status,
                "updated_at": _now(),
                "last_node": self._last_node,
                "nodes_done": list(self._nodes_done),
                "verdict": self._verdict[0],
                "confidence": self._verdict[1],
                "usage": self.usage_snapshot(),
                "state": self._acc,
            }
            if self._result is not None:
                snap["result"] = self._result
            return snap

    def write_partial(self) -> None:
        """Atomically replace ``partial_result.json`` (tmp file + replace;
        Windows readers holding the file open get a few retries)."""
        with self._lock:
            try:
                text = json.dumps(self.snapshot(), ensure_ascii=False)
                tmp = self.partial_path.with_name(PARTIAL_FILENAME + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                for attempt in range(8):
                    try:
                        os.replace(tmp, self.partial_path)
                        return
                    except PermissionError:
                        time.sleep(0.02 * (attempt + 1))
                self.partial_path.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Readers (for the server / tests / the separate formalization entry)
# ---------------------------------------------------------------------------

def read_events(run_dir: str | Path, *, since: int = 0) -> list[dict]:
    """Events of ``run_dir/progress.jsonl`` from line index `since`; a torn
    last line (writer mid-append) is skipped."""
    path = Path(run_dir) / PROGRESS_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[since:]:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def read_partial(run_dir: str | Path) -> dict | None:
    try:
        data = json.loads((Path(run_dir) / PARTIAL_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Graph instrumentation (shared by the four pipelines)
# ---------------------------------------------------------------------------

def instrument_node(name: str, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a LangGraph node: ``node_start`` before, ``node_done`` (+ partial
    snapshot) after, ``error`` if it raises. A no-op passthrough when the
    state carries no ``progress`` writer, so tests and library callers are
    unaffected. Register nodes as ``graph.add_node(n, instrument_node(n, fn))``.
    """
    @functools.wraps(fn)
    def wrapper(state: Any) -> Any:
        pw = state.get("progress") if hasattr(state, "get") else None
        if pw is None:
            return fn(state)
        start_payload: dict[str, Any] = {}
        agent = state.get("_specialist_name") if hasattr(state, "get") else None
        if agent:
            start_payload["agent"] = agent
        pw.node_start(name, start_payload)
        t0 = time.monotonic()
        try:
            update = fn(state)
        except Exception as exc:  # noqa: BLE001
            pw.error(f"{type(exc).__name__}: {exc}", node=name, exc_type=type(exc).__name__)
            raise
        pw.node_done(name, update, elapsed=time.monotonic() - t0,
                     payload=start_payload or None)
        if isinstance(update, dict) and isinstance(update.get("reasoning_output"), dict):
            v, c = _dig_verdict(update["reasoning_output"])
            if v is not None:
                pw.verdict(v, c, node=name, final=False)
        if isinstance(update, dict) and isinstance(update.get("formalization"), dict):
            pw.formalization(update["formalization"], node=name)
        return update

    return wrapper


def announce_start(state: Any, agent_name: str) -> None:
    """``llm_call`` phase=start hook for a live (non-mock) agent call."""
    pw = state.get("progress") if hasattr(state, "get") else None
    if pw is not None:
        pw.llm_start(agent_name)


def finish_pipeline(progress: "ProgressWriter | None", result: dict) -> None:
    """Called by ``run_pipeline`` once the graph returned: final verdict
    event + final snapshot fields, then detach from the usage tracker's
    listeners is left to :meth:`ProgressWriter.done` (CLI) -- library callers
    that never call ``done`` keep a harmless listener on their own tracker."""
    if progress is None:
        return
    try:
        v, c = extract_verdict(result)
        with progress._lock:  # noqa: SLF001
            progress._result = jsonable(result)  # noqa: SLF001
            progress._verdict = (v, c)  # noqa: SLF001
            progress.verdict(v, c, status=result.get("status") if isinstance(result, dict) else None,
                             final=True)
            progress.write_partial()
    except Exception:  # noqa: BLE001
        pass
