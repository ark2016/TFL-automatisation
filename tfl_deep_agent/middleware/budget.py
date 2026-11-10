"""Money for one task: a hierarchical ledger, a per-day spend log and the
``BudgetMiddleware`` that checks every model call before it is made.

Rules (``docs/architecture.html``, "Бюджет и настройки"; ``PLAN.md``):

* **Hierarchy.** :class:`BudgetLedger` has a root :class:`BudgetNode` for the
  task (``per_task_usd``). A subagent or a workflow branch gets a child node
  via :meth:`BudgetNode.allocate`; the allocation is taken out of the parent's
  remainder at once (a promise other siblings cannot spend), it is capped by
  ``per_subagent_max_usd`` for subagents and branches, and it can never use the
  parent's ``reserve_for_report_usd``. A child can never spend more than its
  allocation, nor more than any ancestor still has (an ancestor that overran
  because of a bad estimate shrinks every child under it).
* **max_tokens from the remaining budget** (user decision 2026-11-08). Every
  budgeted call gets ``max_tokens = min(ceiling, floor((room - worst-case
  input cost) / output price))`` (``config.max_tokens_within``). The ceiling
  is what the request would send anyway (the role's ``config.MAX_TOKENS`` /
  ``MAX_TOKENS_PER_ROLE``, clamped to the model's cap, or a lower value the
  request sets, e.g. the summary's 8192). ``room`` is what the scope has
  free above its reserve; in wrap-up (below) it includes the reserve. Every
  call is also capped so that one more call at the floor stays payable with
  the grown prompt (this call's output plus :data:`NEXT_CALL_MARGIN_TOKENS`):
  in normal mode that keeps the report call payable when the prompt is long
  (the fixed reserve alone may not cover a long prompt), in wrap-up it keeps
  the report call payable after a turn that was not the report. Only the last
  call that fits gets everything left. The
  floor is :data:`MIN_OUTPUT_TOKENS` (4096, or the ceiling when that is
  lower): below it the call is refused. The lowered value reaches the model
  through ``request.model_settings["max_tokens"]``.
* **Check before the call.** The worst case of a call is
  ``config.worst_case_usd(model, input_tokens, max_tokens)``: every prompt
  token at the dearer of the input / cache-write rates plus ``max_tokens``
  output tokens. The worst case is reserved (in flight) while the call runs,
  so parallel branches cannot over-commit; it is replaced by the actual cost
  when the call returns. The cap and the reservation are taken under one
  ledger lock, so a parallel sibling cannot take the money in between.
* **Refusal.** A call whose worst case does not fit even at the floor is not
  made. The middleware returns an ``AIMessage`` with the numbers instead; it
  has no tool calls, so the agent loop ends and the renderer builds a partial
  report with status ``budget_exhausted`` (``BudgetNode.state == "exhausted"``).
* **Actual usage.** Charged from ``AIMessage.usage_metadata`` with cache
  tokens priced separately. langchain-anthropic reports ``input_tokens`` as
  the whole prompt (input + cache read + cache write; DEV_NOTES "P0"), so the
  plain input is ``input_tokens - cache_read - cache_creation``.
* **Wrap-up.** When the next call no longer fits without touching the node's
  reserve (not even at the floor), or no longer leaves a floor-sized report
  call payable, the node switches to wrap-up: the call may
  use the reserve (its ``max_tokens`` is derived from the remainder with the
  reserve; this is the report call), an
  instruction to submit the report is appended to every further request, and
  the spawning tools (``task``, ``spawn_subagent``, ``run_workflow``) are
  refused. New allocations are refused as well.
* **Per day.** :class:`DailySpendLog` is an append-only JSONL file in the runs
  directory with one line per charged call. A task cannot start when its
  ``per_task_usd`` does not fit in what is left of the day, and every call is
  also checked against the day's remainder (two tasks may run at once).

Wiring (``agent.default_middleware`` calls :func:`make_middleware`): the run
builds a ledger, then builds the agent inside :func:`use_budget`::

    ledger = BudgetLedger.for_task(settings, task_id=run_id)
    with use_budget(ledger):              # scripted runs: use_budget(ledger, missing_usage="zero")
        agent = create_tfl_agent(...)

Outside ``use_budget`` (and without ``node=``) :func:`make_middleware` returns
``None``: no ledger, no budget middleware, and nothing is written to the
runs-directory spend log.

Pure-stdlib except the middleware class, which needs the ``[deep]`` extra
(``langchain``); the ledger and the log import without it.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import datetime as _dt
import io
import json
import math
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from .. import config
from ..run_store import UNANSWERED_REPLY

try:  # the [deep] extra; the ledger works without it
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware
    _HAVE_LANGCHAIN = True
except ImportError:  # pragma: no cover - exercised only without [deep]
    _AgentMiddleware = object  # type: ignore[assignment,misc]
    _HAVE_LANGCHAIN = False

EPS = 1e-9


def _clean(x: float) -> float:
    """Drop float residue (and never go negative) after subtracting reservations."""
    return 0.0 if x < EPS else x

# Tools that create subagents or workflows; refused in wrap-up mode.
DEFAULT_SPAWNING_TOOLS: frozenset[str] = frozenset({"task", "spawn_subagent", "run_workflow"})

#: Smallest max_tokens a budgeted call is lowered to; below it the call is
#: refused. Adaptive thinking (``thinking: {"type": "adaptive"}``, every 5.x
#: model) has no budget parameter and no API minimum besides max_tokens >= 1,
#: but thinking tokens count toward max_tokens, and the manual-thinking
#: minimum ``budget_tokens >= 1024 < max_tokens`` (anthropic SDK,
#: ``ThinkingConfigEnabledParam``) shows how much reasoning the API treats as
#: the least useful amount. 4096 leaves room for ~1k-3k thinking tokens plus a
#: tool call or a short answer. ``orchestration/spawn.py`` uses the same value.
MIN_OUTPUT_TOKENS = 4096

#: Prompt growth assumed between a call and the next one besides the call's own
#: output: the tool result and the wrap-up note. Used to keep one more call at
#: the floor payable (``BudgetMiddleware._cap_leaving_next``).
NEXT_CALL_MARGIN_TOKENS = 2048

# Kinds of child allocations capped by budget.per_subagent_max_usd.
CAPPED_KINDS: frozenset[str] = frozenset({"subagent", "branch"})

SPEND_LOG_NAME = "deep_spend_log.jsonl"
RUNS_DIR_ENV = "TFL_DEEP_RUNS_DIR"


# ---------------------------------------------------------------------------
# Errors and records
# ---------------------------------------------------------------------------

class BudgetExceeded(RuntimeError):
    """An allocation or a call that does not fit. ``details`` holds the numbers."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class DailyBudgetExceeded(BudgetExceeded):
    """A new task whose ``per_task_usd`` does not fit in the day's remainder."""


@dataclass
class CallRecord:
    """One charged model call."""

    node: str
    role: str | None
    model: str
    usd: float
    worst_case_usd: float
    input_tokens: int = 0          # plain input (cache excluded)
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_input_tokens: int = 0
    max_tokens: int = 0
    usage_missing: bool = False    # no usage metadata: charged by policy
    outcome: str = "ok"            # ok | refusal (served, then raised) | cancelled | summary
    ts: str = field(default_factory=lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reservation:
    node: BudgetNode
    usd: float
    wrap_up: bool
    released: bool = False


@dataclass
class Admission:
    """Result of :meth:`BudgetNode.admit`: a reservation, or a refusal."""

    ok: bool
    worst_case_usd: float
    available_usd: float
    reserve_usd: float
    wrap_up: bool
    entered_wrap_up: bool = False
    reservation: Reservation | None = None
    status: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-day spend log
# ---------------------------------------------------------------------------

def default_runs_dir() -> Path:
    """``$TFL_DEEP_RUNS_DIR`` or the TFL Lab runs directory ``<repo>/.tfl_lab_runs``
    (gitignored), so the CLI and the UI share one day's log."""
    env = os.environ.get(RUNS_DIR_ENV, "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / ".tfl_lab_runs"


def _today() -> str:
    return _dt.date.today().isoformat()


class DailySpendLog:
    """Append-only JSONL log of charged calls: ``{"day", "ts", "task_id",
    "usd", ...}`` per line. Days are local calendar days. Unreadable lines are
    skipped and counted in :attr:`bad_lines` (the file is never rewritten)."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, clock: Callable[[], str] = _today) -> None:
        self.path = Path(path) if path is not None else default_runs_dir() / SPEND_LOG_NAME
        self._clock = clock
        self._lock = threading.Lock()
        self.bad_lines = 0
        self._cache_key: tuple[int, int] | None = None
        self._cache_entries: list[dict[str, Any]] = []

    def today(self) -> str:
        return self._clock()

    def append(self, entry: Mapping[str, Any]) -> None:
        line = dict(entry)
        line.setdefault("day", self.today())
        line.setdefault("ts", _dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        text = json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                st = self.path.stat()
            except FileNotFoundError:
                self._cache_key, self._cache_entries = None, []
                return []
            key = (st.st_size, st.st_mtime_ns)
            if key == self._cache_key:
                return list(self._cache_entries)
            out: list[dict[str, Any]] = []
            bad = 0
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                        usd = float(obj["usd"])
                        if not math.isfinite(usd) or not isinstance(obj.get("day"), str):
                            raise ValueError
                    except (ValueError, KeyError, TypeError):
                        bad += 1
                        continue
                    out.append(obj)
            self.bad_lines = bad
            self._cache_key, self._cache_entries = key, out
            return list(out)

    def day_total(self, day: str | None = None) -> float:
        day = day or self.today()
        return sum(float(e["usd"]) for e in self.entries() if e.get("day") == day)

    def task_total(self, task_id: str) -> float:
        """Everything charged to ``task_id`` on any day (resume after HITL)."""
        return sum(float(e["usd"]) for e in self.entries() if e.get("task_id") == task_id)

    def remaining(self, per_day_usd: float, day: str | None = None) -> float:
        return per_day_usd - self.day_total(day)

    def check_can_start(self, per_task_usd: float, per_day_usd: float) -> float:
        """Raise :class:`DailyBudgetExceeded` when a new task's ``per_task_usd``
        does not fit in the day's remainder; return the remainder otherwise."""
        spent = self.day_total()
        left = per_day_usd - spent
        if per_task_usd > left + EPS:
            raise DailyBudgetExceeded(
                f"daily budget: today ({self.today()}) ${spent:.4f} of ${per_day_usd:.2f} is spent, "
                f"${max(left, 0):.4f} is left, and a new task needs per_task_usd ${per_task_usd:.2f}",
                day=self.today(), spent_usd=spent, per_day_usd=per_day_usd, per_task_usd=per_task_usd,
                remaining_usd=left,
            )
        return left


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class BudgetNode:
    """One budget scope: the task, a subagent or a workflow branch.

    ``spent`` and ``inflight`` are subtree totals (a child's charges and
    reservations are added to every ancestor). Use :meth:`available` for the
    money this node can still put into one call or allocation."""

    def __init__(self, ledger: BudgetLedger, name: str, kind: str, limit: float,
                 reserve: float, parent: BudgetNode | None) -> None:
        self.ledger = ledger
        self.name = name
        self.kind = kind
        self.limit = float(limit)
        self.reserve = float(reserve)
        self.parent = parent
        self.children: list[BudgetNode] = []
        self.spent = 0.0
        self.inflight = 0.0
        self.closed = False
        self.wrap_up = False
        self.exhausted = False
        self.calls = 0
        self.refusals = 0

    # -- identity -----------------------------------------------------------

    @property
    def path(self) -> str:
        return self.name if self.parent is None else f"{self.parent.path}/{self.name}"

    @property
    def depth(self) -> int:
        return 0 if self.parent is None else self.parent.depth + 1

    def ancestors(self) -> list[BudgetNode]:
        out, n = [], self.parent
        while n is not None:
            out.append(n)
            n = n.parent
        return out

    @property
    def state(self) -> Literal["normal", "wrap_up", "exhausted"]:
        if self.exhausted:
            return "exhausted"
        return "wrap_up" if self.wrap_up else "normal"

    def in_wrap_up(self) -> bool:
        """This node or an ancestor is in wrap-up or exhausted."""
        return any(n.wrap_up or n.exhausted for n in (self, *self.ancestors()))

    # -- arithmetic (call with the ledger lock held) ------------------------

    def _used(self) -> float:
        return self.spent + self.inflight

    def _leftover(self) -> float:
        return max(0.0, self.limit - self._used())

    def _own(self) -> float:
        promised = sum(c._leftover() for c in self.children if not c.closed)
        return self.limit - self._used() - promised

    def _available(self) -> float:
        avail = self._own()
        child = self
        for anc in self.ancestors():
            avail = min(avail, anc._own() + child._leftover())
            child = anc
        day = self.ledger._day_remaining()
        if day is not None:
            avail = min(avail, day)
        return avail

    def available(self) -> float:
        with self.ledger.lock:
            return self._available()

    # -- allocation ---------------------------------------------------------

    def allocate(self, name: str, amount_usd: float, *, kind: str = "subagent",
                 reserve_usd: float = 0.0) -> BudgetNode:
        """A child scope with ``amount_usd`` taken from this node's remainder.
        Raises :class:`BudgetExceeded` (with numbers) when it does not fit,
        exceeds ``per_subagent_max_usd`` (kinds ``subagent`` / ``branch``), or
        this node is in wrap-up; ``ValueError`` on a bad amount."""
        amount = float(amount_usd)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"allocation must be a positive amount, got {amount_usd!r}")
        if reserve_usd < 0 or reserve_usd >= amount:
            raise ValueError(f"child reserve must be in [0, {amount}), got {reserve_usd}")
        with self.ledger.lock:
            if self.closed:
                raise BudgetExceeded(f"budget scope {self.path} is closed", node=self.path)
            if self.in_wrap_up():
                raise BudgetExceeded(
                    f"budget scope {self.path} is in wrap-up mode: no new subagents or workflows; "
                    f"submit the report", node=self.path, state=self.state)
            cap = self.ledger.per_child_max_usd
            if kind in CAPPED_KINDS and cap is not None and amount > cap + EPS:
                raise BudgetExceeded(
                    f"allocation ${amount:.4f} for {kind} {name!r} exceeds per_subagent_max_usd ${cap:.2f}",
                    node=self.path, requested_usd=amount, cap_usd=cap)
            free = self._available() - self.reserve
            if amount > free + EPS:
                raise BudgetExceeded(
                    f"allocation ${amount:.4f} for {kind} {name!r} does not fit: {self.path} has "
                    f"${max(free, 0):.4f} free (limit ${self.limit:.4f}, spent ${self.spent:.4f}, "
                    f"in flight ${self.inflight:.4f}, reserve ${self.reserve:.4f})",
                    node=self.path, requested_usd=amount, free_usd=free, **self._numbers())
            names = {c.name for c in self.children}
            unique, i = name, 2
            while unique in names:
                unique, i = f"{name}#{i}", i + 1
            child = BudgetNode(self.ledger, unique, kind, amount, reserve_usd, self)
            self.children.append(child)
        self.ledger._emit({"event": "allocate", "node": child.path, "kind": kind, "usd": amount})
        return child

    def close(self) -> float:
        """Close the scope; its unspent allocation returns to the parent.
        Returns what came back."""
        with self.ledger.lock:
            if self.closed:
                return 0.0
            back = self._leftover()
            self.closed = True
            for c in self.children:
                if not c.closed:
                    c.close()
        self.ledger._emit({"event": "close", "node": self.path, "returned_usd": back})
        return back

    # -- calls ----------------------------------------------------------------

    def admit(self, worst_case_usd: float) -> Admission:
        """Reserve ``worst_case_usd`` for one call, entering wrap-up when the
        call does not fit without the reserve. ``ok=False`` means refuse."""
        with self.ledger.lock:
            adm = self._admit_locked(worst_case_usd)
        self._emit_admission(adm)
        return adm

    def would_enter_wrap_up_locked(self, worst_case_usd: float, *, enter_wrap_up: bool = False) -> bool:
        """Whether a call with this worst case starts wrap-up here (ledger lock held):
        not in wrap-up yet, and the caller says so or it does not fit above the reserve."""
        return not self.in_wrap_up() and (enter_wrap_up or float(worst_case_usd) > self._available() - self.reserve + EPS)

    def _admit_locked(self, worst_case_usd: float, *, enter_wrap_up: bool = False) -> Admission:
        """:meth:`admit` without the listener events (call with the ledger
        lock held, then :meth:`_emit_admission` after releasing it).
        ``enter_wrap_up``: the caller found that a normal call no longer leaves
        the report call payable (``BudgetMiddleware.derive``), so this call
        enters wrap-up even when its worst case fits above the reserve."""
        x = float(worst_case_usd)
        entered = False
        if self.closed:
            avail = 0.0
            ok = False
        else:
            avail = self._available()
            if self.would_enter_wrap_up_locked(x, enter_wrap_up=enter_wrap_up):
                self.wrap_up = True
                entered = True
            wrap = self.in_wrap_up()
            limit = avail if wrap else avail - self.reserve
            ok = x <= limit + EPS
        wrap = self.in_wrap_up() or self.closed
        res = None
        if ok:
            res = Reservation(self, x, wrap)
            for n in (self, *self.ancestors()):
                n.inflight += x
        else:
            self.exhausted = True
            self.refusals += 1
        status = self._status_locked()
        return Admission(ok=ok, worst_case_usd=x, available_usd=avail, reserve_usd=self.reserve,
                         wrap_up=wrap, entered_wrap_up=entered, reservation=res, status=status)

    def _emit_admission(self, adm: Admission) -> None:
        if adm.entered_wrap_up:
            self.ledger._emit({"event": "wrap_up", "node": self.path, "available_usd": adm.available_usd,
                               "worst_case_usd": adm.worst_case_usd, "reserve_usd": self.reserve})
        if not adm.ok:
            self.ledger._emit({"event": "refused", "node": self.path, "available_usd": adm.available_usd,
                               "worst_case_usd": adm.worst_case_usd})

    def release(self, res: Reservation) -> None:
        """Drop a reservation without charging (the call raised)."""
        with self.ledger.lock:
            if res.released:
                return
            res.released = True
            for n in (res.node, *res.node.ancestors()):
                n.inflight = _clean(n.inflight - res.usd)

    def settle(self, res: Reservation, record: CallRecord) -> None:
        """Replace a reservation by the actual cost in ``record``."""
        with self.ledger.lock:
            if not res.released:
                res.released = True
                for n in (res.node, *res.node.ancestors()):
                    n.inflight = _clean(n.inflight - res.usd)
            self._charge_locked(record)
        self.ledger._after_charge(record)

    def charge(self, record: CallRecord) -> None:
        """Charge a call that was not reserved through :meth:`admit`."""
        with self.ledger.lock:
            self._charge_locked(record)
        self.ledger._after_charge(record)

    def _charge_locked(self, record: CallRecord) -> None:
        for n in (self, *self.ancestors()):
            n.spent += record.usd
        self.calls += 1
        self.ledger.records.append(record)

    # -- reporting ------------------------------------------------------------

    def _numbers(self) -> dict[str, float]:
        return {"limit_usd": self.limit, "spent_usd": self.spent, "inflight_usd": self.inflight,
                "reserve_usd": self.reserve}

    def _status_locked(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "node": self.path, "kind": self.kind, "state": self.state, "closed": self.closed,
            **self._numbers(), "available_usd": self._available(), "calls": self.calls,
            "refusals": self.refusals,
        }
        day = self.ledger._day_remaining()
        if day is not None:
            out["day_remaining_usd"] = day
        if self.children:
            out["children"] = [c._status_locked() for c in self.children]
        return out

    def status(self) -> dict[str, Any]:
        """Numbers for ``budget_status`` / the UI budget bar."""
        with self.ledger.lock:
            return self._status_locked()


class BudgetLedger:
    """The budget tree of one task plus its link to the per-day log."""

    def __init__(
        self,
        per_task_usd: float,
        *,
        reserve_for_report_usd: float = 0.0,
        per_subagent_max_usd: float | None = None,
        per_day_usd: float | None = None,
        spend_log: DailySpendLog | None = None,
        task_id: str | None = None,
        already_spent_usd: float = 0.0,
        listeners: Iterable[Callable[[dict[str, Any]], None]] = (),
    ) -> None:
        if per_task_usd <= 0:
            raise ValueError("per_task_usd must be > 0")
        self.lock = threading.RLock()
        self.task_id = task_id or time.strftime("task-%Y%m%d-%H%M%S")
        self.per_child_max_usd = per_subagent_max_usd
        self.per_day_usd = per_day_usd
        self.spend_log = spend_log
        self.records: list[CallRecord] = []
        self.listeners = list(listeners)
        self.root = BudgetNode(self, "task", "task", per_task_usd, reserve_for_report_usd, None)
        self.root.spent = float(already_spent_usd)

    @classmethod
    def for_task(
        cls,
        settings: Any,
        *,
        task_id: str | None = None,
        spend_log: DailySpendLog | None = None,
        check_day: bool = True,
        resume: bool = False,
        listeners: Iterable[Callable[[dict[str, Any]], None]] = (),
    ) -> BudgetLedger:
        """A ledger from ``settings.budget`` (a :class:`tfl_deep_agent.settings.Settings`).

        ``check_day`` refuses to start a new task whose ``per_task_usd`` does not
        fit in the day's remainder (:class:`DailyBudgetExceeded`). ``resume``
        (HITL resume in a new process) skips that check and starts the root at
        what the log already holds for ``task_id``."""
        b = settings.budget
        log = spend_log if spend_log is not None else DailySpendLog()
        already = 0.0
        if resume:
            if not task_id:
                raise ValueError("resume needs the task_id of the run being resumed")
            already = log.task_total(task_id)
        elif check_day:
            log.check_can_start(b.per_task_usd, b.per_day_usd)
        return cls(
            b.per_task_usd,
            reserve_for_report_usd=b.report_reserve_usd,  # at most a quarter of per_task_usd
            per_subagent_max_usd=b.per_subagent_max_usd,
            per_day_usd=b.per_day_usd,
            spend_log=log,
            task_id=task_id,
            already_spent_usd=already,
            listeners=listeners,
        )

    def _day_remaining(self) -> float | None:
        if self.spend_log is None or self.per_day_usd is None:
            return None
        return self.per_day_usd - self.spend_log.day_total() - self.root.inflight

    def _after_charge(self, record: CallRecord) -> None:
        if self.spend_log is not None:
            self.spend_log.append({"task_id": self.task_id, **record.to_dict()})
        self._emit({"event": "call", **record.to_dict()})

    def raise_limit(self, new_limit: float) -> float:
        """Raise the task's limit to ``new_limit``. It only ever grows, so applying the
        same answer again (a re-executed node, a new process) changes nothing. Returns
        the limit now."""
        with self.lock:
            before = self.root.limit
            limit = self.root.limit = max(before, float(new_limit))
        if limit > before:
            self._emit({"event": "limit_raised", "node": self.root.path, "previous_usd": before, "limit_usd": limit})
        return limit

    def raise_day_limit(self, new_limit: float) -> float | None:
        """Raise the day's limit (``per_day_usd``) of this ledger to ``new_limit``; like
        :meth:`raise_limit` it only grows. ``None`` and no change for a ledger with no day
        limit. The settings' ``per_day_usd`` is not touched."""
        with self.lock:
            before = self.per_day_usd
            if before is None:
                return None
            limit = self.per_day_usd = max(before, float(new_limit))
        if limit > before:
            self._emit({"event": "day_limit_raised", "previous_usd": before, "limit_usd": limit})
        return limit

    def _emit(self, event: dict[str, Any]) -> None:
        for fn in self.listeners:
            try:
                fn(dict(event))
            except Exception:  # a broken UI listener never breaks accounting
                pass

    @property
    def spent_usd(self) -> float:
        return self.root.spent

    @property
    def exhausted(self) -> bool:
        """Some call in the tree was refused (renderer: ``budget_exhausted``)."""
        with self.lock:
            stack = [self.root]
            while stack:
                n = stack.pop()
                if n.exhausted:
                    return True
                stack.extend(n.children)
            return False

    def summary(self) -> dict[str, Any]:
        """Totals, plus cost by role and by model."""
        with self.lock:
            by_role: dict[str, float] = {}
            by_model: dict[str, float] = {}
            for r in self.records:
                by_role[r.role or "?"] = by_role.get(r.role or "?", 0.0) + r.usd
                by_model[r.model] = by_model.get(r.model, 0.0) + r.usd
            return {
                "task_id": self.task_id,
                "spent_usd": self.root.spent,
                "limit_usd": self.root.limit,
                "calls": len(self.records),
                "exhausted": self.exhausted,
                "by_role": by_role,
                "by_model": by_model,
                "tree": self.root._status_locked(),
            }


# ---------------------------------------------------------------------------
# Token estimate (local, no API call)
# ---------------------------------------------------------------------------

# Conservative constants for the worst case. A count_tokens API call is a
# network call, so the estimate is local; BudgetMiddleware also scales it up by
# the largest actual/estimate ratio it has seen (never down).
ASCII_CHARS_PER_TOKEN = 3.0
NON_ASCII_CHARS_PER_TOKEN = 1.5      # Cyrillic, math symbols
TOKENS_PER_MESSAGE = 6
FIXED_REQUEST_TOKENS = 600           # tool-use system preamble, thinking config, ...
IMAGE_TOKENS = 7000                  # above one high-resolution image
PDF_TOKENS_PER_PAGE = 6000           # page text + page image
PDF_UNKNOWN_PAGES = 20               # settings.intake.max_pdf_pages default
_OPAQUE_KEYS = frozenset({"data", "base64", "url", "source", "file_id", "cache_control", "id", "type",
                          "mime_type", "media_type", "index"})


def text_tokens(text: str) -> int:
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_n = len(text) - non_ascii
    return math.ceil(ascii_n / ASCII_CHARS_PER_TOKEN + non_ascii / NON_ASCII_CHARS_PER_TOKEN)


def _pdf_pages(block: Mapping[str, Any]) -> int:
    data = block.get("base64") or block.get("data")
    src = block.get("source")
    if data is None and isinstance(src, Mapping):
        data = src.get("data")
    if isinstance(data, str):
        try:
            raw = base64.b64decode(data, validate=False)
            from pypdf import PdfReader  # [deep] extra
            return max(1, len(PdfReader(io.BytesIO(raw)).pages))
        except Exception:  # not base64, not a PDF, or pypdf missing
            pass
    return PDF_UNKNOWN_PAGES


def _is_pdf(block: Mapping[str, Any]) -> bool:
    t = block.get("type")
    mime = block.get("mime_type") or block.get("media_type")
    src = block.get("source")
    if not mime and isinstance(src, Mapping):
        mime = src.get("media_type")
    return t in ("document", "file") and (mime in (None, "application/pdf"))


def _value_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return text_tokens(value)
    if isinstance(value, Mapping):
        t = value.get("type")
        if t in ("image", "image_url", "input_image"):
            return IMAGE_TOKENS
        if _is_pdf(value):
            return _pdf_pages(value) * PDF_TOKENS_PER_PAGE
        src = value.get("source")
        total = 0
        if isinstance(src, Mapping) and src.get("type") == "text":  # plain-text document block
            total += _value_tokens(src.get("data"))
        elif t in ("document", "file"):  # another base64 file: its decoded size, at the dearest rate
            data = value.get("base64") or value.get("data") or (src.get("data") if isinstance(src, Mapping) else None)
            if isinstance(data, str):
                total += math.ceil(len(data) * 0.75 / NON_ASCII_CHARS_PER_TOKEN)
        for k, v in value.items():
            if k in _OPAQUE_KEYS:
                continue
            total += text_tokens(str(k)) + _value_tokens(v)
        return total
    if isinstance(value, (list, tuple)):
        return sum(_value_tokens(v) for v in value)
    if isinstance(value, (int, float, bool)):
        return 1
    return text_tokens(str(value))


def _message_tokens(msg: Any) -> int:
    content = getattr(msg, "content", msg)
    n = TOKENS_PER_MESSAGE + _value_tokens(content)
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls and not isinstance(content, list):
        n += text_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return n


def _tool_tokens(tool: Any) -> int:
    if isinstance(tool, Mapping):
        schema: Any = tool
    else:
        try:
            from langchain_core.utils.function_calling import convert_to_openai_tool
            schema = convert_to_openai_tool(tool)
        except Exception:
            schema = {"name": getattr(tool, "name", ""), "description": getattr(tool, "description", "")}
    return text_tokens(json.dumps(schema, ensure_ascii=False, default=str))


def estimate_input_tokens(request: Any) -> int:
    """Upper-bound estimate of a request's prompt tokens: system message,
    messages (images and PDF pages at fixed worst-case costs), tool schemas."""
    n = FIXED_REQUEST_TOKENS
    sysmsg = getattr(request, "system_message", None)
    if sysmsg is not None:
        n += _message_tokens(sysmsg)
    for m in getattr(request, "messages", None) or ():
        n += _message_tokens(m)
    for t in getattr(request, "tools", None) or ():
        n += _tool_tokens(t)
    return n


# ---------------------------------------------------------------------------
# Usage -> cost
# ---------------------------------------------------------------------------

def usage_counts(usage_metadata: Mapping[str, Any] | None) -> dict[str, int] | None:
    """langchain ``usage_metadata`` -> the four Anthropic buckets, with the
    cache tokens taken out of ``input_tokens`` (which is the whole prompt)."""
    if not usage_metadata:
        return None
    total_in = int(usage_metadata.get("input_tokens") or 0)
    out = int(usage_metadata.get("output_tokens") or 0)
    details = usage_metadata.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    cache_write = int(details.get("cache_creation") or 0)
    if not cache_write:
        cache_write = sum(int(v or 0) for k, v in details.items() if str(k).startswith("ephemeral_"))
    return {
        "input_tokens": max(0, total_in - cache_read - cache_write),
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


def usage_cost_usd(model: str, counts: Mapping[str, int]) -> float:
    """USD for the four buckets at ``config`` prices (Haiku 5.5's long-prompt
    card above the threshold). Raises :class:`config.UnknownModelError`."""
    config.pricing_key(model)
    cost = config.estimate_cost_usd(model, SimpleNamespace(**counts))
    if cost is None:
        raise config.UnknownModelError(f"no complete price card for {model!r}")
    return float(cost)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _priced(model: Any) -> str | None:
    if not isinstance(model, str) or not model.strip():
        return None
    try:
        config.pricing_key(model)
    except config.UnknownModelError:
        return None
    return model


def _last_ai(response: Any) -> Any:
    from langchain_core.messages import AIMessage

    if isinstance(response, AIMessage):
        return response
    inner = getattr(response, "model_response", None)  # ExtendedModelResponse
    if inner is not None:
        response = inner
    for m in reversed(getattr(response, "result", None) or []):
        if isinstance(m, AIMessage):
            return m
    return None


#: what the model call being charged is ("ok": an agent turn; "summary": set
#: by :func:`budgeted_summarizer`), recorded as ``CallRecord.outcome``.
_CALL_KIND: contextvars.ContextVar[str] = contextvars.ContextVar("tfl_deep_call_kind", default="ok")


def _served_message(exc: BaseException) -> Any:
    """The response message an exception raised after the call carries
    (``ModelRefusalError.raw_message``, ``StructuredOutputError.ai_message``),
    or ``None``."""
    from langchain_core.messages import AIMessage

    for attr in ("raw_message", "ai_message"):
        msg = getattr(exc, attr, None)
        if isinstance(msg, AIMessage):
            return msg
    return None


@dataclass
class CallEstimate:
    model: str
    input_tokens: int
    max_tokens: int
    worst_case_usd: float


WRAP_UP_INSTRUCTION_CHILD = (
    "BUDGET WRAP-UP. Only ${available:.4f} is left for this scope (limit ${limit:.2f}, spent ${spent:.4f}). "
    "Stop exploring now and return your final answer to the caller with what is already established, "
    "marking unfinished parts as not verified. New subagents and workflows ({tools}) are disabled."
)

WRAP_UP_INSTRUCTION = (
    "BUDGET WRAP-UP. Only ${available:.4f} is left for this scope (limit ${limit:.2f}, spent ${spent:.4f}, "
    "reserve for the report ${reserve:.2f}). Stop exploring now: submit the report with what is already "
    "established (submit_report), marking unfinished parts as not verified. New subagents and workflows "
    "({tools}) are disabled."
)

#: the reply of a follow-up that made no model call; later turns read it in the conversation
UNANSWERED_TEXT = "(No answer to this message: the human did not extend the budget. The report stands.)"

REFUSAL_TEXT = (
    "BUDGET: model call refused before it was made. Worst case ${worst:.4f} "
    "({model}: ~{input_tokens} input tokens + max_tokens {max_tokens}) does not fit in ${available:.4f} "
    "available for {node} (limit ${limit:.2f}, spent ${spent:.4f}, in flight ${inflight:.4f}, "
    "reserve ${reserve:.2f}{day}). No further model calls are made in this scope; the run ends with "
    "status budget_exhausted and a partial report is produced from the evidence collected so far."
)


@dataclass(frozen=True)
class BudgetLow:
    """The root scope's money when its next call would enter wrap-up or be refused."""

    spent_usd: float
    limit_usd: float
    available_usd: float
    reserve_usd: float
    day_left_usd: float | None  # None: a run with no day log (mock)
    #: what is short: ``"task"`` (the task's limit) or ``"day"`` (the day's remainder; only asked
    #: for a follow-up of an accepted report, ``skip_call_when_low``, where wrap-up must never run)
    binds: str = "task"
    per_day_usd: float | None = None  # the day limit; set when ``binds == "day"``


class BudgetMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Checks every model call of one agent against one :class:`BudgetNode`.

    Place it inside any middleware that swaps ``request.model`` (it prices the
    model it sees). ``model`` / ``max_tokens`` are fallbacks for chat models
    that carry neither (fake models in tests); a ``ChatAnthropic`` reports both.

    ``missing_usage``: a response without ``usage_metadata`` is charged its
    worst case (``"worst_case"``, default, safe for money) or nothing
    (``"zero"``, scripted fake-model runs).

    ``fit_max_tokens`` (default on; user decision 2026-11-08): every call's
    ``max_tokens`` is ``min(ceiling, what the scope can pay for)`` and a call
    that cannot get ``min_output_tokens`` is refused (module docstring).
    ``False`` keeps the fixed ceiling (the call is refused when the ceiling's
    worst case does not fit); only tests that pin that arithmetic use it.

    ``request_budget_extension`` (the main agent of a run with the budget stop, off
    by default): called with a :class:`BudgetLow` before a call of the root scope
    would enter wrap-up or be refused, with nothing reserved yet. It may raise
    LangGraph's ``GraphInterrupt`` (``budget_stop.request_budget_extension``); what
    it returns is the task's new absolute limit, or ``None`` to finish with what is
    left (:meth:`BudgetLedger.raise_limit`).

    ``skip_call_when_low`` (a follow-up on a run with an accepted report, off by default;
    ``budget_stop.declining_skips_the_call``): when, after that question, such a call still
    would enter wrap-up or be refused, no model is called and nothing is reserved or
    charged; the agent's reply is a text marked ``run_store.UNANSWERED_REPLY``, so its
    segment ends.
    """

    def __init__(
        self,
        node: BudgetNode,
        *,
        role: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        token_counter: Callable[[Any], int] | None = None,
        missing_usage: Literal["worst_case", "zero"] = "worst_case",
        spawning_tools: Iterable[str] = DEFAULT_SPAWNING_TOOLS,
        fit_max_tokens: bool = True,
        min_output_tokens: int = MIN_OUTPUT_TOKENS,
    ) -> None:
        if not _HAVE_LANGCHAIN:  # pragma: no cover
            raise ImportError("BudgetMiddleware needs the [deep] extra (langchain)")
        super().__init__()
        if missing_usage not in ("worst_case", "zero"):
            raise ValueError(f"missing_usage must be 'worst_case' or 'zero', got {missing_usage!r}")
        if model is not None:
            config.pricing_key(config.normalize_model(model))
        self._base_node = node
        self.role = role
        self.model = config.normalize_model(model) if model else None
        self.max_tokens = max_tokens
        self.token_counter = token_counter or estimate_input_tokens
        self.missing_usage = missing_usage
        self.spawning_tools = frozenset(spawning_tools)
        self.fit_max_tokens = bool(fit_max_tokens)
        if int(min_output_tokens) < 1:
            raise ValueError(f"min_output_tokens must be >= 1, got {min_output_tokens!r}")
        self.min_output_tokens = int(min_output_tokens)
        #: the budget stop's callback; the main agent's build sets it, a subagent's never asks
        self.request_budget_extension: Callable[[BudgetLow], float | tuple[str, float] | None] | None = None
        self.skip_call_when_low = False
        self._scale = 1.0
        self._scale_lock = threading.Lock()
        self.tools = []

    # -- scope ----------------------------------------------------------------

    @property
    def node(self) -> BudgetNode:
        """The scope charged by the current call: the innermost active
        subagent scope (:func:`active_scope`, set by
        :class:`SubagentBudgetMiddleware` around a ``task`` call) when it lies
        under this middleware's own node, else the node it was built with.
        So one subagent graph, built once, charges a fresh allocation on every
        invocation, and parallel invocations (each in its own context) charge
        their own."""
        act = _ACTIVE_SCOPE.get()
        base = self._base_node
        if act is not None and act is not base and act.ledger is base.ledger and base in act.ancestors():
            return act
        return base

    @node.setter
    def node(self, value: BudgetNode) -> None:
        self._base_node = value

    # -- estimate -----------------------------------------------------------

    def _model_id(self, request: Any) -> str:
        m = request.model
        for attr in ("model", "model_name"):
            mid = _priced(getattr(m, attr, None))
            if mid:
                return mid
        if self.model:
            return self.model
        return config.resolve_model(role=self.role)

    def _max_tokens(self, request: Any, model: str) -> int:
        settings = getattr(request, "model_settings", None) or {}
        for v in (settings.get("max_tokens"), getattr(request.model, "max_tokens", None), self.max_tokens):
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                return v
        return config.resolve_max_tokens(role=self.role, model=model)

    def estimate(self, request: Any) -> CallEstimate:
        model = self._model_id(request)
        raw = int(self.token_counter(request))
        with self._scale_lock:
            scaled = math.ceil(raw * self._scale)
        max_tokens = self._max_tokens(request, model)
        return CallEstimate(model, scaled, max_tokens, config.worst_case_usd(model, scaled, max_tokens))

    def floor_tokens(self, ceiling: int) -> int:
        """The least max_tokens a call with this ``ceiling`` may be lowered to."""
        return max(1, min(self.min_output_tokens, int(ceiling)))

    def _with_max_tokens(self, est: CallEstimate, n: int) -> CallEstimate:
        return CallEstimate(est.model, est.input_tokens, n, config.worst_case_usd(est.model, est.input_tokens, n))

    def _cap_leaving_next(self, est: CallEstimate, budget_usd: float) -> int:
        """The largest ``max_tokens`` c (at most ``est.max_tokens``) such that
        this call's worst case AND one more call at the floor fit in
        ``budget_usd``, the next prompt being this one plus c output tokens
        plus :data:`NEXT_CALL_MARGIN_TOKENS` (the tool result, the wrap-up
        note). ``0`` when not even c = 0 leaves that. Prices may step up with
        the prompt length (``config.worst_case_usd``), so the search is a
        bisection over a monotone check, not a closed formula."""
        floor = self.floor_tokens(est.max_tokens)
        n, model = est.input_tokens, est.model

        def fits(c: int) -> bool:
            return (config.worst_case_usd(model, n, c)
                    + config.worst_case_usd(model, n + c + NEXT_CALL_MARGIN_TOKENS, floor)) <= budget_usd + EPS

        if not fits(0):
            return 0
        lo, hi = 0, int(est.max_tokens)
        if fits(hi):
            return hi
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if fits(mid) else (lo, mid)
        return lo

    def derive(self, est: CallEstimate, node: BudgetNode) -> tuple[CallEstimate, bool]:
        """``(estimate, enter_wrap_up)``: the estimate this call is admitted
        with (ledger lock held).

        * Normal mode: ``max_tokens`` is the most that both stays above the
          reserve and leaves one more call at the floor payable with the grown
          prompt (:meth:`_cap_leaving_next`), so the reserve is never too small
          for the report call's prompt (a long prompt on Opus would otherwise
          leave the report call below the floor).
        * When that is below the floor, the call enters wrap-up
          (``enter_wrap_up`` true). In wrap-up every call is capped so that one
          more floor-sized call still fits (a report that has to be resent, or
          a turn that was not the report, leaves the report call payable); only
          when not even that fits does the call get all that is left (the last
          call).
        * When nothing fits, the estimate at the floor, which
          :meth:`BudgetNode.admit` then refuses."""
        avail = node._available()
        wrap = node.in_wrap_up()
        floor = self.floor_tokens(est.max_tokens)
        if not wrap:
            above = config.max_tokens_within(est.model, est.input_tokens, avail - node.reserve, est.max_tokens)
            n = min(above, self._cap_leaving_next(est, avail))
            if n >= floor:
                return (est if n == est.max_tokens else self._with_max_tokens(est, n)), False
        n = self._cap_leaving_next(est, avail)
        if n < floor:
            n = config.max_tokens_within(est.model, est.input_tokens, avail, est.max_tokens)
        if n >= floor:
            return (est if n == est.max_tokens else self._with_max_tokens(est, n)), not wrap
        return self._with_max_tokens(est, floor), False

    # -- shared pre / post ----------------------------------------------------

    def _budget_low_locked(self, est: CallEstimate, node: BudgetNode) -> BudgetLow | None:
        """The numbers of ``node`` when a call with ``est`` would enter wrap-up or be
        refused (ledger lock held, nothing reserved), else ``None``. When the day's
        remainder is what binds, raising the task's limit buys nothing: the numbers then
        ask for the day limit (``binds == "day"``) in a follow-up of an accepted report
        (``skip_call_when_low``: user decision 2026-11-10, CA-015), else ``None`` and the
        old wrap-up behaviour applies."""
        if node.closed:
            return None
        probe, enter = self.derive(est, node) if self.fit_max_tokens else (est, False)
        if not node.would_enter_wrap_up_locked(probe.worst_case_usd, enter_wrap_up=enter):
            return None
        day = node.ledger._day_remaining()
        if day is not None and day <= node._own() + EPS:
            if not self.skip_call_when_low:
                return None
            return BudgetLow(spent_usd=node.spent, limit_usd=node.limit, available_usd=max(node._available(), 0.0),
                             reserve_usd=node.reserve, day_left_usd=max(day, 0.0), binds="day",
                             per_day_usd=node.ledger.per_day_usd)
        return BudgetLow(spent_usd=node.spent, limit_usd=node.limit, available_usd=max(node._available(), 0.0),
                         reserve_usd=node.reserve, day_left_usd=None if day is None else max(day, 0.0))

    def _is_main_call(self, node: BudgetNode) -> bool:
        """The main agent's own call on the root scope: never a summarization (it is a
        step inside another node) or a subagent."""
        return node is self._base_node and node.parent is None and _CALL_KIND.get() != "summary"

    def _skips_the_call(self, est: CallEstimate, node: BudgetNode) -> bool:
        if not self.skip_call_when_low or not self._is_main_call(node):
            return False
        with node.ledger.lock:
            return self._budget_low_locked(est, node) is not None

    def _ask_for_budget(self, est: CallEstimate, node: BudgetNode) -> None:
        """The budget stop."""
        if self.request_budget_extension is None or not self._is_main_call(node):
            return
        with node.ledger.lock:
            low = self._budget_low_locked(est, node)
        if low is None:
            return
        answer = self.request_budget_extension(low)  # may raise GraphInterrupt: nothing is reserved yet
        if answer is None:
            return
        # (kind, new absolute limit); a bare number is the limit of what was short (low.binds)
        kind, new_limit = answer if isinstance(answer, tuple) else (low.binds or "task", answer)
        (node.ledger.raise_day_limit if kind == "day" else node.ledger.raise_limit)(new_limit)

    def _before(self, request: Any) -> tuple[Any, CallEstimate, Admission | None]:
        """What the hook goes on with; the admission is ``None`` when the call is skipped."""
        est = self.estimate(request)
        ceiling = est.max_tokens
        node = self.node
        enter = False
        self._ask_for_budget(est, node)  # asks at most once in one execution of the node
        if self._skips_the_call(est, node):
            return request, est, None
        with node.ledger.lock:  # cap and reservation in one step; listeners run after the lock
            if self.fit_max_tokens:
                est, enter = self.derive(est, node)
            adm = node._admit_locked(est.worst_case_usd, enter_wrap_up=enter)
        node._emit_admission(adm)
        if adm.ok and est.max_tokens != ceiling:
            request = request.override(
                model_settings={**(request.model_settings or {}), "max_tokens": est.max_tokens})
        if adm.ok and adm.wrap_up:
            request = self._with_wrap_up(request, adm)
        return request, est, adm

    def _with_wrap_up(self, request: Any, adm: Admission) -> Any:
        from langchain_core.messages import HumanMessage

        st = adm.status
        template = WRAP_UP_INSTRUCTION if self.node.parent is None else WRAP_UP_INSTRUCTION_CHILD
        text = template.format(
            available=max(adm.available_usd, 0.0), limit=st["limit_usd"], spent=st["spent_usd"],
            reserve=adm.reserve_usd, tools=", ".join(sorted(self.spawning_tools)))
        msg = HumanMessage(content=text, additional_kwargs={"tfl_budget": "wrap_up"})
        return request.override(messages=[*request.messages, msg])

    def _unanswered(self) -> Any:
        from langchain.agents.middleware.types import ModelResponse
        from langchain_core.messages import AIMessage

        return ModelResponse(result=[AIMessage(content=UNANSWERED_TEXT,
                                               additional_kwargs={"tfl_budget": UNANSWERED_REPLY})])

    def _refusal(self, est: CallEstimate, adm: Admission) -> Any:
        from langchain.agents.middleware.types import ModelResponse
        from langchain_core.messages import AIMessage

        st = adm.status
        day = st.get("day_remaining_usd")
        numbers = {
            "worst_case_usd": round(est.worst_case_usd, 6), "model": est.model,
            "input_tokens": est.input_tokens, "max_tokens": est.max_tokens,
            "available_usd": round(adm.available_usd, 6), "node": st["node"],
            "limit_usd": st["limit_usd"], "spent_usd": round(st["spent_usd"], 6),
            "inflight_usd": round(st["inflight_usd"], 6), "reserve_usd": adm.reserve_usd,
        }
        if day is not None:
            numbers["day_remaining_usd"] = round(day, 6)
        text = REFUSAL_TEXT.format(
            worst=est.worst_case_usd, model=est.model, input_tokens=est.input_tokens,
            max_tokens=est.max_tokens, available=max(adm.available_usd, 0.0), node=st["node"],
            limit=st["limit_usd"], spent=st["spent_usd"], inflight=st["inflight_usd"],
            reserve=adm.reserve_usd, day="" if day is None else f", day remaining ${max(day, 0.0):.4f}")
        msg = AIMessage(
            content=text,
            additional_kwargs={"tfl_budget": "refused"},
            response_metadata={"stop_reason": "budget_exhausted", "tfl_budget": numbers},
        )
        return ModelResponse(result=[msg])

    def _after(self, response: Any, est: CallEstimate, adm: Admission, *, outcome: str | None = None) -> None:
        assert adm.reservation is not None
        outcome = outcome or _CALL_KIND.get()
        ai = _last_ai(response)
        counts = usage_counts(getattr(ai, "usage_metadata", None)) if ai is not None else None
        model = est.model
        if ai is not None:
            meta = getattr(ai, "response_metadata", None) or {}
            model = _priced(meta.get("model_name")) or _priced(meta.get("model")) or est.model
        if counts is None:
            usd = est.worst_case_usd if self.missing_usage == "worst_case" else 0.0
            record = CallRecord(node=self.node.path, role=self.role, model=model, usd=usd,
                                worst_case_usd=est.worst_case_usd, estimated_input_tokens=est.input_tokens,
                                max_tokens=est.max_tokens, usage_missing=True, outcome=outcome)
        else:
            usd = usage_cost_usd(model, counts)
            prompt = (counts["input_tokens"] + counts["cache_read_input_tokens"]
                      + counts["cache_creation_input_tokens"])
            if est.input_tokens > 0 and prompt > est.input_tokens:
                with self._scale_lock:
                    self._scale = min(4.0, max(self._scale, self._scale * prompt / est.input_tokens))
            record = CallRecord(
                node=self.node.path, role=self.role, model=model, usd=usd, worst_case_usd=est.worst_case_usd,
                input_tokens=counts["input_tokens"], output_tokens=counts["output_tokens"],
                cache_read_tokens=counts["cache_read_input_tokens"],
                cache_write_tokens=counts["cache_creation_input_tokens"],
                estimated_input_tokens=est.input_tokens, max_tokens=est.max_tokens, outcome=outcome)
        self.node.settle(adm.reservation, record)

    def _on_error(self, exc: BaseException, est: CallEstimate, adm: Admission) -> None:
        """Settle a call whose handler raised.

        * The exception carries the served response (``raw_message`` /
          ``ai_message``, e.g. ``ModelRefusalError`` raised by deepagents'
          ``ModelRefusalMiddleware`` inside this one after the call returned):
          charge its usage (worst case without usage, per ``missing_usage``).
        * Cancellation (``CancelledError``, ``KeyboardInterrupt``, ... -- not
          an ``Exception``): the request may have been sent and billed, so the
          worst case is charged and logged (safe for money; a call cancelled
          while still waiting for a semaphore slot is over-counted).
        * Any other error (an API error raised before a response): released,
          nothing charged."""
        assert adm.reservation is not None
        served = _served_message(exc)
        if served is not None:
            self._after(served, est, adm, outcome="refusal")
            return
        if not isinstance(exc, Exception):
            record = CallRecord(node=self.node.path, role=self.role, model=est.model, usd=est.worst_case_usd,
                                worst_case_usd=est.worst_case_usd, estimated_input_tokens=est.input_tokens,
                                max_tokens=est.max_tokens, usage_missing=True, outcome="cancelled")
            self.node.settle(adm.reservation, record)
            return
        self.node.release(adm.reservation)

    # -- langchain hooks ------------------------------------------------------

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        request, est, adm = self._before(request)
        if adm is None:
            return self._unanswered()
        if not adm.ok:
            return self._refusal(est, adm)
        try:
            response = handler(request)
        except BaseException as exc:
            self._on_error(exc, est, adm)
            raise
        self._after(response, est, adm)
        return response

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        request, est, adm = self._before(request)
        if adm is None:
            return self._unanswered()
        if not adm.ok:
            return self._refusal(est, adm)
        try:
            response = await handler(request)
        except BaseException as exc:
            self._on_error(exc, est, adm)
            raise
        self._after(response, est, adm)
        return response

    def _blocked_tool(self, request: Any) -> Any:
        call = request.tool_call
        name = call.get("name") if isinstance(call, Mapping) else getattr(call, "name", None)
        if name not in self.spawning_tools or not self.node.in_wrap_up():
            return None
        from langchain_core.messages import ToolMessage

        st = self.node.status()
        return ToolMessage(
            content=(f"Refused: the budget is in wrap-up mode ({st['node']}: ${max(st['available_usd'], 0):.4f} "
                     f"left, reserve ${st['reserve_usd']:.2f}). {name} is disabled; submit the report now."),
            tool_call_id=call.get("id") if isinstance(call, Mapping) else getattr(call, "id", ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        blocked = self._blocked_tool(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        blocked = self._blocked_tool(request)
        return blocked if blocked is not None else await handler(request)


# ---------------------------------------------------------------------------
# Subagent scopes: one allocation per `task` call
# ---------------------------------------------------------------------------

#: The budget scope of the subagent invocation running in this context (set
#: around a ``task`` tool call; ``None`` on the main thread).
_ACTIVE_SCOPE: contextvars.ContextVar[BudgetNode | None] = contextvars.ContextVar(
    "tfl_deep_budget_scope", default=None)


def active_scope() -> BudgetNode | None:
    """The innermost subagent scope of the current context, if any."""
    return _ACTIVE_SCOPE.get()


@contextlib.contextmanager
def enter_scope(node: BudgetNode) -> Iterator[BudgetNode]:
    """Run the block with ``node`` as the active scope: every
    :class:`BudgetMiddleware` whose own node is an ancestor of ``node``
    charges ``node`` instead (see :attr:`BudgetMiddleware.node`)."""
    token = _ACTIVE_SCOPE.set(node)
    try:
        yield node
    finally:
        _ACTIVE_SCOPE.reset(token)


SUBAGENT_REFUSED_TEXT = (
    "Refused: no budget for a new {tool} call. {reason} Continue with what is already established "
    "or submit the report (submit_report)."
)


class SubagentBudgetMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Gives each subagent invocation its own allocation (architecture.html,
    "Иерархия": a child gets a piece of the parent's remainder and cannot spend
    more than that).

    Wraps the tool calls named in ``scoped_tools`` (default: deepagents'
    ``task``). Before the call it allocates ``min(per_subagent_max_usd, what
    the parent has free above its reserve)`` from the current scope (the active
    one, else ``node``), runs the tool with that child as the active scope, and
    closes the child afterwards, so the unspent part returns to the parent.
    The subagent's own :class:`BudgetMiddleware` (built over an ancestor of the
    child) then charges, wraps up and refuses within the child.

    A call that gets no allocation (wrap-up, nothing free, less than
    ``min_allocation_usd``) is not run: the model gets an error ToolMessage
    with the numbers. Place it after (inside) the agent's own
    :class:`BudgetMiddleware`, which already refuses the spawning tools in
    wrap-up with its own message.

    ``child_reserve_usd``: the child's reserve for its final answer; default
    ``min(the root's reserve_for_report_usd, a quarter of the allocation)``.
    """

    def __init__(
        self,
        node: BudgetNode,
        *,
        scoped_tools: Iterable[str] = ("task",),
        max_allocation_usd: float | None = None,
        child_reserve_usd: float | None = None,
        min_allocation_usd: float = 0.001,
    ) -> None:
        if not _HAVE_LANGCHAIN:  # pragma: no cover
            raise ImportError("SubagentBudgetMiddleware needs the [deep] extra (langchain)")
        super().__init__()
        self.node = node
        self.scoped_tools = frozenset(scoped_tools)
        self.max_allocation_usd = max_allocation_usd
        self.child_reserve_usd = child_reserve_usd
        self.min_allocation_usd = float(min_allocation_usd)
        self.tools = []

    def _parent(self) -> BudgetNode:
        act = _ACTIVE_SCOPE.get()
        if act is not None and act.ledger is self.node.ledger and (
                act is self.node or self.node in act.ancestors()):
            return act
        return self.node

    def _amount(self, parent: BudgetNode) -> tuple[float, float]:
        led = parent.ledger
        with led.lock:
            free = parent._available() - parent.reserve
        caps = [free]
        if led.per_child_max_usd is not None:
            caps.append(led.per_child_max_usd)
        if self.max_allocation_usd is not None:
            caps.append(self.max_allocation_usd)
        return min(caps), free

    def _child_reserve(self, amount: float) -> float:
        want = self.child_reserve_usd
        if want is None:
            want = min(self.node.ledger.root.reserve, amount / 4)
        return max(0.0, min(float(want), amount / 2))

    @staticmethod
    def _call_info(request: Any) -> tuple[str | None, str, str]:
        call = request.tool_call
        get = call.get if isinstance(call, Mapping) else (lambda k, d=None: getattr(call, k, d))
        args = get("args") or {}
        label = (args.get("subagent_type") or args.get("name") or get("name") or "subagent") \
            if isinstance(args, Mapping) else (get("name") or "subagent")
        return get("name"), str(get("id") or ""), str(label)

    def _open(self, request: Any) -> tuple[BudgetNode | None, Any]:
        """``(child, None)`` or ``(None, refusal ToolMessage)``."""
        from langchain_core.messages import ToolMessage

        name, call_id, label = self._call_info(request)
        parent = self._parent()
        amount, free = self._amount(parent)
        reason = None
        child = None
        if amount < self.min_allocation_usd - EPS:
            st = parent.status()
            reason = (f"{st['node']} has ${max(free, 0.0):.4f} free above its reserve "
                      f"${st['reserve_usd']:.2f} (limit ${st['limit_usd']:.2f}, spent ${st['spent_usd']:.4f}).")
        else:
            try:
                child = parent.allocate(label, amount, kind="subagent",
                                        reserve_usd=self._child_reserve(amount))
            except BudgetExceeded as exc:
                reason = str(exc)
        if child is not None:
            return child, None
        with parent.ledger.lock:
            parent.refusals += 1  # an allocation refusal; it does not make the scope exhausted
        parent.ledger._emit({"event": "subagent_refused", "node": parent.path, "tool": name,
                             "subagent": label, "reason": reason})
        return None, ToolMessage(
            content=SUBAGENT_REFUSED_TEXT.format(tool=name, reason=reason),
            tool_call_id=call_id, name=name, status="error",
            additional_kwargs={"tfl_budget": "subagent_refused"})

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        name, _, _ = self._call_info(request)
        if name not in self.scoped_tools:
            return handler(request)
        child, refusal = self._open(request)
        if child is None:
            return refusal
        try:
            with enter_scope(child):
                return handler(request)
        finally:
            child.close()

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        name, _, _ = self._call_info(request)
        if name not in self.scoped_tools:
            return await handler(request)
        child, refusal = self._open(request)
        if child is None:
            return refusal
        try:
            with enter_scope(child):
                return await handler(request)
        finally:
            child.close()


# ---------------------------------------------------------------------------
# Hook for agent.default_middleware
# ---------------------------------------------------------------------------

_CURRENT: contextvars.ContextVar[tuple[BudgetNode, dict[str, Any]] | None] = contextvars.ContextVar(
    "tfl_deep_budget", default=None)


@contextlib.contextmanager
def use_budget(target: BudgetLedger | BudgetNode, **middleware_kwargs: Any) -> Iterator[BudgetNode]:
    """Make ``target`` (a ledger's root or any node) the scope that
    :func:`make_middleware` charges while agents are built inside the block.
    ``middleware_kwargs`` go to every :class:`BudgetMiddleware` made there."""
    node = target.root if isinstance(target, BudgetLedger) else target
    token = _CURRENT.set((node, dict(middleware_kwargs)))
    try:
        yield node
    finally:
        _CURRENT.reset(token)


@contextlib.contextmanager
def no_budget() -> Iterator[None]:
    """Build agents inside the block without any budget middleware, even
    within an outer :func:`use_budget` (``create_tfl_agent(budget=False)``)."""
    token = _CURRENT.set(None)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_budget() -> BudgetNode | None:
    cur = _CURRENT.get()
    return cur[0] if cur else None


def _role_model(settings: Any, role: str | None) -> str | None:
    """The role's model from settings (a Settings object or its dict), through
    ``config.resolve_model`` so ``TFL_MODEL_OVERRIDE`` still wins."""
    if not role:
        return None
    roles: Any = None
    if isinstance(settings, Mapping):
        roles = (settings.get("models") or {}).get("roles")
    elif settings is not None:
        roles = getattr(getattr(settings, "models", None), "roles", None)
    requested = roles.get(role) if isinstance(roles, Mapping) else None
    return config.resolve_model(role=role, requested=requested)


def make_middleware(settings: Any = None, *, role: str | None = None, node: BudgetNode | None = None,
                    **kwargs: Any) -> BudgetMiddleware | None:
    """A :class:`BudgetMiddleware` for one agent (``role``) charging ``node``,
    or the scope set by :func:`use_budget`; ``None`` when there is neither."""
    cur = _CURRENT.get()
    if node is None:
        if cur is None:
            return None
        node = cur[0]
    merged = {**(cur[1] if cur else {}), **kwargs}
    merged.setdefault("model", _role_model(settings, role))
    return BudgetMiddleware(node, role=role, **merged)


def make_subagent_middleware(node: BudgetNode | None = None, **kwargs: Any) -> SubagentBudgetMiddleware | None:
    """A :class:`SubagentBudgetMiddleware` over ``node`` or the
    :func:`use_budget` scope; ``None`` when there is neither (no ledger, no
    allocations)."""
    if node is None:
        node = current_budget()
        if node is None:
            return None
    return SubagentBudgetMiddleware(node, **kwargs)


# ---------------------------------------------------------------------------
# Summarization through the budget
# ---------------------------------------------------------------------------

#: max_tokens of one summarization call (the summary is short; a lower ceiling
#: keeps its worst case, which the budget reserves, small).
SUMMARY_MAX_TOKENS = 8192

SUMMARY_REFUSED_TEXT = (
    "[No summary: the summarization call was refused by the budget ({node}: worst case ${worst:.4f}, "
    "available ${available:.4f}). The earlier messages are not summarized; their full text was saved to "
    "the conversation history file named below and can be read with read_file. Submit the report soon.]")


def _wrapped(mw: Any, inner: Callable[[Any], Any]) -> Callable[[Any], Any]:
    return lambda req: mw.wrap_model_call(req, inner)


def _awrapped(mw: Any, inner: Callable[[Any], Any]) -> Callable[[Any], Any]:
    return lambda req: mw.awrap_model_call(req, inner)


def budgeted_summarizer(model: Any, budget_mw: BudgetMiddleware, *, wrappers: Sequence[Any] = (),
                        summary_prompt: str | None = None, max_tokens: int = SUMMARY_MAX_TOKENS) -> Any:
    """A ``summarizer`` for deepagents' ``SummarizationMiddleware`` whose model
    call goes through ``budget_mw`` (and then ``wrappers``, e.g. the
    concurrency middleware), so a summary is admitted, reserved and charged
    like any other call of the agent. deepagents' own summarization calls the
    model directly from its ``wrap_model_call``, which runs outside (before)
    every user middleware, so without this a summary is never charged.

    The prompt is the one deepagents would send (``summary_prompt``, default
    ``DEEPAGENTS_DEFAULT_SUMMARY_PROMPT``, over the XML buffer string of the
    messages). A refused call returns :data:`SUMMARY_REFUSED_TEXT` instead of a
    summary; no model call is made. Returns a ``RunnableLambda`` (sync and
    async) mapping the messages to summarize to the summary text."""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import HumanMessage, get_buffer_string
    from langchain_core.runnables import RunnableLambda

    if summary_prompt is None:
        from deepagents.middleware.summarization import (
            DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        )

        summary_prompt = DEEPAGENTS_DEFAULT_SUMMARY_PROMPT
    chain = [budget_mw, *wrappers]

    def _request(messages: Sequence[Any]) -> Any:
        text = summary_prompt.format(messages=get_buffer_string(list(messages), format="xml")).rstrip()
        return ModelRequest(model=model, messages=[HumanMessage(content=text)], tools=[],
                            model_settings={"max_tokens": int(max_tokens)})

    def _bound(request: Any) -> Any:
        settings = dict(request.model_settings or {})
        return request.model.bind(**settings) if settings else request.model

    def _messages(request: Any) -> list[Any]:
        # the budget's wrap-up note is for the agent, not for the summary writer
        return [m for m in request.messages
                if (getattr(m, "additional_kwargs", None) or {}).get("tfl_budget") != "wrap_up"]

    def _text(response: Any) -> str:
        ai = _last_ai(response)
        if ai is None:
            return ""
        if (getattr(ai, "additional_kwargs", None) or {}).get("tfl_budget") == "refused":
            n = (getattr(ai, "response_metadata", None) or {}).get("tfl_budget") or {}
            return SUMMARY_REFUSED_TEXT.format(node=n.get("node", "?"), worst=float(n.get("worst_case_usd", 0.0)),
                                               available=max(float(n.get("available_usd", 0.0)), 0.0))
        return (ai.text or "").strip()

    def _sync(messages: Sequence[Any]) -> str:
        def call(request: Any) -> Any:
            return ModelResponse(result=[_bound(request).invoke(_messages(request))])

        handler: Callable[[Any], Any] = call
        for mw in reversed(chain):
            handler = _wrapped(mw, handler)
        token = _CALL_KIND.set("summary")
        try:
            return _text(handler(_request(messages)))
        finally:
            _CALL_KIND.reset(token)

    async def _async(messages: Sequence[Any]) -> str:
        async def call(request: Any) -> Any:
            return ModelResponse(result=[await _bound(request).ainvoke(_messages(request))])

        handler: Callable[[Any], Any] = call
        for mw in reversed(chain):
            handler = _awrapped(mw, handler)
        token = _CALL_KIND.set("summary")
        try:
            return _text(await handler(_request(messages)))
        finally:
            _CALL_KIND.reset(token)

    return RunnableLambda(_sync, afunc=_async, name="tfl_budgeted_summarizer")


__all__: Sequence[str] = (
    "BudgetLow",
    "DEFAULT_SPAWNING_TOOLS",
    "MIN_OUTPUT_TOKENS",
    "SUMMARY_MAX_TOKENS",
    "Admission",
    "BudgetExceeded",
    "BudgetLedger",
    "BudgetMiddleware",
    "BudgetNode",
    "CallEstimate",
    "CallRecord",
    "DailyBudgetExceeded",
    "DailySpendLog",
    "Reservation",
    "SubagentBudgetMiddleware",
    "active_scope",
    "budgeted_summarizer",
    "current_budget",
    "default_runs_dir",
    "enter_scope",
    "estimate_input_tokens",
    "make_middleware",
    "make_subagent_middleware",
    "no_budget",
    "text_tokens",
    "usage_cost_usd",
    "usage_counts",
    "use_budget",
)
