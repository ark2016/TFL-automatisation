"""Append-only evidence ledger written by EvidenceMiddleware; the agent cannot edit it (P3).

One :class:`EvidenceLedger` per run. ``middleware/evidence.py`` appends one
:class:`EvidenceRecord` per tool call -- in the main agent, in every subagent
and (P4) in every workflow branch -- so the report can cite results by id and
the trust of every claim is computed by code from what the tools returned
(``evidence/policy.py``), never from what the model says about them.

Where it lives:

* in memory (``records()``, ``get(id)``), shared by every middleware of the run;
* ``run_dir/evidence.jsonl`` when the ledger has a path: one JSON object per
  line, appended and flushed under a lock. The agent's file tools work on the
  virtual ``StateBackend`` (``agent.make_backend``), not on the run directory,
  so the model has no way to write to this file;
* a compact copy of each record in the agent state (``tfl_evidence``, written by
  the middleware), which the checkpointer persists with the run.

Integrity: every line carries ``prev`` (the previous line's ``digest``) and
``digest`` (sha256 of the canonical JSON of the record without ``digest``), a
hash chain. :meth:`EvidenceLedger.verify` recomputes it, so an edited, removed
or reordered line is detected. It is a tamper *check* for the human and the
renderer, not a security boundary (whoever can write the file can rebuild the
chain).

Ids: the evidence id of a call is its ``tool_call_id`` when that is unique in
the ledger (the model sees its own tool-call ids, and the P2 reports cite them);
otherwise (an empty id, or a reused id -- scripted models in two subagents,
a resumed run) ``<tool_call_id or "call">~<seq>``. The middleware tells the
model the id when it differs from the tool-call id.

Re-opening a ledger (:meth:`EvidenceLedger.open` on an existing file, e.g. a
HITL resume in a new process) loads the records, so ids stay unique and the
chain continues. A torn last line (a crash mid-write) is skipped and reported
by :meth:`verify`; the next append starts on a new line, so the fragment never
swallows a new record.

``parent_call``: the ``tool_call_id`` of the spawning call (a ``task`` call)
whose subagent made this call, set by the middleware (``middleware/evidence.py``
keeps it in a context variable while the spawning call runs); ``None`` in the
main agent. HITL 1 uses it to take the IR of exactly the ir-builder call under
review.

Pure stdlib + pydantic-free: imports without the ``[deep]`` extra.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import hashlib
import json
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .rules import (  # noqa: F401 - re-exported: the predicates live in rules.py
    canonical_json,
    entry_fingerprint,
    ir_admissible,
    ir_fingerprint,
    lean_proof_side,
)

EVIDENCE_FILE = "evidence.jsonl"
SCHEMA_VERSION = 1

#: ``data`` / ``args`` larger than this (JSON characters) are stored as a
#: preview; the digest always covers the full result.
MAX_DATA_CHARS = 200_000
MAX_ARGS_CHARS = 50_000
PREVIEW_CHARS = 4_000

#: Fields of a record kept in the agent state (``tfl_evidence``): everything
#: the per-claim trust and the UI need, without ``args`` / ``data``.
STATE_FIELDS: tuple[str, ...] = (
    "id", "seq", "tool", "tool_call_id", "ok", "trust", "trust_reason", "bounds", "error",
    "error_kind", "has_tool_result", "status", "role", "scope", "model", "turn_usd", "cost_usd",
    "result_digest", "ts",
)

GENESIS = "0" * 64


def digest_of(obj: Any) -> str:
    """sha256 hex of :func:`canonical_json` (a ``str`` is hashed as is)."""
    text = obj if isinstance(obj, str) else canonical_json(obj)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonable(obj: Any) -> Any:
    """Plain JSON for ``obj`` (the tool layer's converter when it imports)."""
    try:
        from ..tools.base import to_jsonable
    except Exception:  # pragma: no cover - the tool layer always imports in the package
        return json.loads(canonical_json(obj))
    return to_jsonable(obj)


def capped(obj: Any, limit: int) -> tuple[Any, bool]:
    """``(obj, False)`` when its JSON fits in ``limit`` characters, else a
    preview object and ``True``."""
    text = canonical_json(obj)
    if len(text) <= limit:
        return obj, False
    return {"truncated": True, "chars": len(text), "preview": text[:PREVIEW_CHARS],
            "note": "stored shortened in the evidence ledger; result_digest covers the full value"}, True


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass
class EvidenceRecord:
    """One tool call. ``trust`` is set by code from the tool's own
    ``ToolResult`` (``not_verified`` for a call without one: a subagent's
    answer, a built-in tool, a failure); the model never sets it."""

    id: str
    seq: int
    tool: str
    tool_call_id: str = ""
    args: Any = None
    ok: bool = False
    trust: str = "not_verified"
    trust_reason: str = ""
    bounds: Any = None
    error: str | None = None
    error_kind: str | None = None
    data: Any = None
    has_tool_result: bool = False
    status: str = "success"            # ToolMessage.status: success | error | exception
    role: str | None = None            # the agent that made the call (main, prover, ...)
    scope: str | None = None           # the budget node path the call ran in (task/prover, ...)
    model: str | None = None           # the model of the turn that requested the call
    turn_usd: float | None = None      # cost of that model call (shared by its parallel tool calls)
    cost_usd: float | None = None      # model spend caused by the call itself (a subagent's allocation)
    parent_call: str | None = None     # tool_call_id of the spawning call (task, ...) this call ran inside
    result_digest: str = ""
    args_digest: str = ""
    truncated: list[str] = field(default_factory=list)
    elapsed_s: float | None = None
    ts: str = field(default_factory=_now)
    v: int = SCHEMA_VERSION
    prev: str = ""
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def state_view(self) -> dict[str, Any]:
        """The compact copy kept in the agent state."""
        d = self.to_dict()
        return {k: d[k] for k in STATE_FIELDS}

    def gate_entry(self) -> dict[str, Any]:
        """The shape ``agent.verdict_gate`` reads (``id``, ``tool``, ``ok``,
        ``trust``, ``bounds``, ``data`` as a mapping, ``args``)."""
        return {"id": self.id, "tool": self.tool, "ok": self.ok, "trust": self.trust,
                "trust_reason": self.trust_reason, "bounds": self.bounds, "error": self.error,
                "data": self.data if isinstance(self.data, Mapping) else {},
                "args": self.args if isinstance(self.args, Mapping) else {}}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> EvidenceRecord:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def record_digest(d: Mapping[str, Any]) -> str:
    """The chain digest of a record dict (all fields but ``digest``)."""
    return digest_of({k: v for k, v in d.items() if k != "digest"})


def cited_evidence_ids(report: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``[(id, where)]`` of every evidence id the report cites: ``evidence_ids``,
    ``claims[i].evidence_ids``, ``steps[i].evidence_ids``, ``lean.evidence_ids``."""
    out: list[tuple[str, str]] = []

    def add(ids: Any, where: str) -> None:
        for i in ids or []:
            if isinstance(i, str) and i.strip():
                out.append((i.strip(), where))

    add(report.get("evidence_ids"), "evidence_ids")
    for n, claim in enumerate(report.get("claims") or []):
        if isinstance(claim, Mapping):
            add(claim.get("evidence_ids"), f"claims[{n}]")
    for n, step in enumerate(report.get("steps") or []):
        if isinstance(step, Mapping):
            add(step.get("evidence_ids"), f"steps[{n}]")
    lean = report.get("lean")
    if isinstance(lean, Mapping):
        add(lean.get("evidence_ids"), "lean")
    return out


class EvidenceLedger:
    """The evidence of one run. Thread-safe; ``append`` is the only way in."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *,
                 run_id: str | None = None,
                 listeners: Iterable[Callable[[dict[str, Any]], None]] = ()) -> None:
        self.path = Path(path) if path is not None else None
        self.run_id = run_id
        self.lock = threading.RLock()
        self.listeners = list(listeners)
        self._records: list[EvidenceRecord] = []
        self._by_id: dict[str, EvidenceRecord] = {}
        self._load_problems: list[str] = []
        #: the file does not end with a newline (a torn last line): the next
        #: append starts a new line first, so the fragment stays on its own
        self._needs_newline = False
        if self.path is not None and self.path.exists():
            self._load()

    # -- construction ---------------------------------------------------------

    @classmethod
    def open(cls, run_dir: str | os.PathLike[str], **kwargs: Any) -> EvidenceLedger:
        """The ledger at ``run_dir/evidence.jsonl`` (created on the first
        append; an existing file is loaded and continued)."""
        d = Path(run_dir)
        return cls(d / EVIDENCE_FILE, **kwargs)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> EvidenceLedger:
        """Read an existing ledger file (``run_dir`` or the ``.jsonl`` itself)."""
        p = Path(path)
        if p.is_dir():
            p = p / EVIDENCE_FILE
        if not p.exists():
            raise FileNotFoundError(p)
        return cls(p)

    def _load(self) -> None:
        assert self.path is not None
        text = self.path.read_text(encoding="utf-8")
        self._needs_newline = bool(text) and not text.endswith("\n")
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                rec = EvidenceRecord.from_dict(d)
            except (ValueError, TypeError) as exc:
                self._load_problems.append(f"line {i}: unreadable ({type(exc).__name__})")
                continue
            if rec.id in self._by_id:
                self._load_problems.append(f"line {i}: duplicate id {rec.id!r}")
                continue
            self._records.append(rec)
            self._by_id[rec.id] = rec

    # -- reading --------------------------------------------------------------

    def __len__(self) -> int:
        with self.lock:
            return len(self._records)

    def __contains__(self, evidence_id: object) -> bool:
        with self.lock:
            return evidence_id in self._by_id

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self.lock:
            return self._by_id.get(evidence_id)

    def records(self) -> list[EvidenceRecord]:
        with self.lock:
            return list(self._records)

    def by_id(self) -> dict[str, EvidenceRecord]:
        with self.lock:
            return dict(self._by_id)

    def gate_entries(self) -> list[dict[str, Any]]:
        """Every record backed by a ToolResult, in the shape of ``agent.verdict_gate``."""
        return [r.gate_entry() for r in self.records() if r.has_tool_result]

    def state_view(self) -> list[dict[str, Any]]:
        return [r.state_view() for r in self.records()]

    # -- writing --------------------------------------------------------------

    def _new_id(self, tool_call_id: str, seq: int) -> str:
        if tool_call_id and tool_call_id not in self._by_id:
            return tool_call_id
        base = tool_call_id or "call"
        cand = f"{base}~{seq}"
        n = seq
        while cand in self._by_id:  # pragma: no cover - only with hand-made ids
            n += 1
            cand = f"{base}~{n}"
        return cand

    def append(self, *, tool: str, tool_call_id: str = "", args: Any = None,
               result: Mapping[str, Any] | None = None, content: Any = None,
               status: str = "success", trust: str = "not_verified", trust_reason: str = "",
               has_tool_result: bool = False, **meta: Any) -> EvidenceRecord:
        """Record one tool call and return the record (with its id).

        ``result`` is the validated ``ToolResult`` dump when the tool produced
        one (then ``ok`` / ``bounds`` / ``error`` / ``data`` come from it);
        ``content`` is what the model read (hashed when there is no result).
        ``trust`` / ``trust_reason`` are decided by the caller (the
        middleware), from the result; ``meta`` fills the remaining fields
        (``role``, ``scope``, ``model``, ``turn_usd``, ``cost_usd``,
        ``elapsed_s``, ``ok``, ``error``, ``error_kind``)."""
        args_j = _jsonable(args) if args is not None else None
        res_j = _jsonable(dict(result)) if result is not None else None
        result_digest = digest_of(res_j if res_j is not None else {"content": _jsonable(content)})
        args_digest = digest_of(args_j) if args_j is not None else ""
        truncated: list[str] = []
        args_store, cut = capped(args_j, MAX_ARGS_CHARS) if args_j is not None else (None, False)
        if cut:
            truncated.append("args")
        fields: dict[str, Any] = {}
        if res_j is not None:
            fields.update(ok=bool(res_j.get("ok")), bounds=res_j.get("bounds"),
                          error=res_j.get("error"), error_kind=res_j.get("error_kind"),
                          elapsed_s=res_j.get("elapsed_s"))
            data, cut = capped(res_j.get("data"), MAX_DATA_CHARS)
            if cut:
                truncated.append("data")
            fields["data"] = data
        fields.update({k: v for k, v in meta.items() if v is not None or k not in fields})
        known = EvidenceRecord.__dataclass_fields__  # type: ignore[attr-defined]
        unknown = [k for k in fields if k not in known]
        if unknown:
            raise TypeError(f"unknown evidence field(s) {unknown}")
        with self.lock:
            seq = len(self._records) + 1
            rec = EvidenceRecord(
                id=self._new_id(str(tool_call_id or ""), seq), seq=seq, tool=tool,
                tool_call_id=str(tool_call_id or ""), args=args_store, status=status, trust=trust,
                trust_reason=trust_reason, has_tool_result=has_tool_result,
                result_digest=result_digest, args_digest=args_digest, truncated=truncated, **fields)
            rec.prev = self._records[-1].digest if self._records else GENESIS
            rec.digest = record_digest(rec.to_dict())
            self._write(rec)
            self._records.append(rec)
            self._by_id[rec.id] = rec
        self._emit({"event": "evidence", **rec.state_view()})
        return rec

    def _write(self, rec: EvidenceRecord) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            if self._needs_newline:  # a torn last line: never glue the new record to it
                fh.write("\n")
            fh.write(canonical_json(rec.to_dict()) + "\n")
            fh.flush()
        self._needs_newline = False

    def _emit(self, event: dict[str, Any]) -> None:
        for fn in self.listeners:
            try:
                fn(dict(event))
            except Exception:  # a broken UI listener never breaks the ledger
                pass

    # -- integrity ------------------------------------------------------------

    def verify(self) -> list[str]:
        """Problems with the hash chain (``[]`` = intact). Reads the file when
        the ledger has one (what a reader of ``evidence.jsonl`` sees),
        otherwise checks the in-memory records."""
        problems: list[str] = []
        if self.path is not None and self.path.exists():
            dicts: list[dict[str, Any]] = []
            for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    dicts.append(json.loads(line))
                except ValueError:
                    problems.append(f"line {i}: unreadable")
        else:
            dicts = [r.to_dict() for r in self.records()]
        prev = GENESIS
        for d in dicts:
            rid = d.get("id")
            if d.get("prev") != prev:
                problems.append(f"{rid}: chain broken (prev does not match the previous record)")
            if d.get("digest") != record_digest(d):
                problems.append(f"{rid}: digest mismatch (the record was changed)")
            prev = d.get("digest") or ""
        return problems


# ---------------------------------------------------------------------------
# Run scope (like budget.use_budget): the ledger the middleware built inside
# the block writes to
# ---------------------------------------------------------------------------

_CURRENT: contextvars.ContextVar[EvidenceLedger | None] = contextvars.ContextVar(
    "tfl_deep_evidence", default=None)


@contextlib.contextmanager
def use_evidence(ledger: EvidenceLedger) -> Iterator[EvidenceLedger]:
    """Make ``ledger`` the one ``middleware.evidence.make_middleware`` (and so
    ``agent.default_middleware``) records into while agents are built inside
    the block. Read at build time: the agent keeps writing to it afterwards."""
    token = _CURRENT.set(ledger)
    try:
        yield ledger
    finally:
        _CURRENT.reset(token)


def current_evidence() -> EvidenceLedger | None:
    return _CURRENT.get()
