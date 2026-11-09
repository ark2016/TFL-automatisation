"""EvidenceMiddleware: records every tool call into the evidence ledger (P3).

``wrap_tool_call`` / ``awrap_tool_call`` around each tool call of the agent it
is installed in. ``agent.default_middleware`` installs one in the main agent and
in every fixed role (and P4 builds its dynamic agents with the same hook), all
writing to the one :class:`~tfl_deep_agent.evidence.ledger.EvidenceLedger` of
the run, so the calls made **inside subagents** are recorded too, under run-wide
ids.

For each call the ledger gets: the evidence id, tool name, tool-call id,
arguments, a digest of the result (and the result itself, size-capped),
``trust`` / ``trust_reason`` / ``bounds`` taken from the tool's own
``ToolResult``, the role and budget scope that made the call, the model of the
turn that requested it, the cost of that turn and (for a ``task`` call) the
subagent's spend, a timestamp, and ``parent_call``: the ``tool_call_id`` of
the spawning call (``task``) the subagent runs for, so the records of one
ir-builder call are told apart from an earlier or a parallel one (HITL 1).

Trust is set by code only: a call counts as evidence when it is a registered
TFL tool (``tools.base.REGISTRY``) whose ToolMessage artifact validates as a
``ToolResult`` for that tool; its trust is clamped to the tool's ceiling once
more. Anything else -- a subagent's answer (``task``), a built-in tool
(``read_file``, ``write_todos``), a validation error, a failed or raised call --
is recorded with ``not_verified``.

State: the compact record (``EvidenceRecord.state_view``) is added to the agent
state key ``tfl_evidence`` through a ``Command`` update; the reducer
(:func:`merge_evidence`) merges by id, so the subagent's list coming back
through deepagents' ``task`` Command does not duplicate records.

The id is the tool-call id when it is unique in the ledger; otherwise the
ledger makes one (``<id>~<seq>``) and the middleware adds ``"evidence_id"`` to
the ToolMessage content (a JSON object gets the key, other text a prefix line),
so the model can cite it. ``additional_kwargs["tfl_evidence_id"]`` always holds it.

Wiring: build the agent inside :func:`tfl_deep_agent.evidence.ledger.use_evidence`
(like ``budget.use_budget``)::

    ledger = EvidenceLedger.open(run_dir)
    with use_evidence(ledger):
        agent = create_tfl_agent(...)

Outside such a block (and without ``ledger=``) :func:`make_middleware` returns
``None``: nothing is recorded.

Not recorded (they never reach this middleware, which sits inside them): a
tool call refused by the run's call limit (``TFLCallLimit``), a spawning tool
refused in budget wrap-up (``BudgetMiddleware``), a ``task`` call that got no
allocation (``SubagentBudgetMiddleware``). Those are refusals, not results.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Annotated, Any

from ..evidence.ledger import EvidenceLedger, EvidenceRecord, current_evidence

#: The ``tool_call_id`` of the spawning call (``task``, ...) being run, set by
#: the middleware of the agent that made it for the duration of the call. The
#: subagent's own middleware records it as ``parent_call``, so the records of
#: one subagent call can be told apart from those of an earlier (or a
#: parallel) call of the same role. Context variables follow the subagent run
#: (same thread, a ``copy_context`` thread of a parallel tool node, or an
#: awaited coroutine).
_PARENT_CALL: contextvars.ContextVar[str | None] = contextvars.ContextVar("tfl_deep_parent_call", default=None)


def current_parent_call() -> str | None:
    """The spawning call the current tool call runs inside (``None`` in the main agent)."""
    return _PARENT_CALL.get()

try:  # the [deep] extra; the ledger and the reducer work without it
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware
    from langchain.agents.middleware.types import AgentState as _AgentState
    _HAVE_LANGCHAIN = True
except ImportError:  # pragma: no cover - exercised only without [deep]
    _AgentMiddleware = object  # type: ignore[assignment,misc]
    _AgentState = dict  # type: ignore[assignment,misc]
    _HAVE_LANGCHAIN = False

from typing import NotRequired

STATE_KEY = "tfl_evidence"

#: Tools whose own spend (a subagent run) is measured as ``cost_usd``.
SPAWNING_TOOLS: frozenset[str] = frozenset({"task", "spawn_subagent", "run_workflow"})


def merge_evidence(left: Sequence[Mapping[str, Any]] | None,
                   right: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """State reducer for ``tfl_evidence``: append by id, first copy wins,
    order kept. A subagent returns the list it inherited plus its own
    records; merging by id keeps every record once."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seq in (left or [], right or []):
        if isinstance(seq, Mapping):  # a single record
            seq = [seq]
        for rec in seq:
            if not isinstance(rec, Mapping):
                continue
            rid = str(rec.get("id") or "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append(dict(rec))
    return out


class EvidenceState(_AgentState):  # type: ignore[misc,valid-type]
    tfl_evidence: NotRequired[Annotated[list[dict[str, Any]], merge_evidence]]


# ---------------------------------------------------------------------------
# Reading the call and its result
# ---------------------------------------------------------------------------

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _messages_of(state: Any) -> list[Any]:
    msgs = _get(state, "messages", None)
    return list(msgs) if isinstance(msgs, (list, tuple)) else []


def _tool_message_of(result: Any, call_id: str) -> Any:
    """The ToolMessage answering ``call_id`` in a handler result (a
    ToolMessage, or a Command whose update carries it)."""
    from langchain_core.messages import ToolMessage

    if isinstance(result, ToolMessage):
        return result
    update = getattr(result, "update", None)
    msgs = _get(update, "messages", None) if isinstance(update, Mapping) else None
    for m in msgs or []:
        if isinstance(m, ToolMessage) and (not call_id or m.tool_call_id == call_id):
            return m
    return None


def tool_result_of(tool: str, msg: Any) -> tuple[dict[str, Any] | None, str]:
    """``(ToolResult dump, why)`` when ``msg`` carries a valid ToolResult of
    the registered TFL tool ``tool``, else ``(None, why not)``. The trust is
    clamped to the tool's ceiling again (it already was in
    ``ToolRegistry.invoke``; this guards a tool built some other way)."""
    from ..tools.base import REGISTRY, ToolResult

    art = getattr(msg, "artifact", None)
    if not isinstance(art, Mapping) or "trust" not in art:
        return None, "no ToolResult (a subagent answer, a built-in tool or a validation error)"
    if tool not in REGISTRY:
        return None, f"{tool} is not a registered TFL tool: its artifact is not evidence"
    try:
        res = ToolResult.model_validate(dict(art))
    except Exception as exc:  # noqa: BLE001 - any invalid artifact is just not evidence
        return None, f"the artifact is not a valid ToolResult ({type(exc).__name__})"
    if res.tool and res.tool != tool:
        return None, f"the artifact belongs to {res.tool}, not {tool}"
    spec = REGISTRY.get(tool)
    clamped = REGISTRY._apply_ceiling(spec, res)
    return clamped.model_dump(mode="json"), ""


def _with_evidence_id(msg: Any, evidence_id: str) -> Any:
    """A copy of ``msg`` that names its evidence id: always in
    ``additional_kwargs``; in the content too when it differs from the
    tool-call id (otherwise the model already knows it)."""
    import json

    extra = {**(msg.additional_kwargs or {}), "tfl_evidence_id": evidence_id}
    update: dict[str, Any] = {"additional_kwargs": extra}
    if evidence_id != msg.tool_call_id:
        content = msg.content
        if isinstance(content, str):
            try:
                obj = json.loads(content)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                update["content"] = json.dumps({"evidence_id": evidence_id, **obj},
                                               ensure_ascii=False, separators=(",", ":"))
            else:
                update["content"] = f"[evidence_id: {evidence_id}]\n{content}"
        elif isinstance(content, list):
            update["content"] = [{"type": "text", "text": f"[evidence_id: {evidence_id}]"}, *content]
    return msg.model_copy(update=update)


# ---------------------------------------------------------------------------
# The middleware
# ---------------------------------------------------------------------------

class EvidenceMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Records each tool call of one agent into ``ledger`` and the agent
    state. ``role`` names the agent (``main``, ``prover``, ...), ``model`` its
    configured model (the turn's ``response_metadata`` model wins when it is
    there). ``budget_node`` (default: the ``budget.use_budget`` scope at build
    time) is used to find the budget scope, turn cost and subagent spend."""

    state_schema = EvidenceState

    def __init__(self, ledger: EvidenceLedger, *, role: str | None = None, model: str | None = None,
                 budget_node: Any = None, spawning_tools: Iterable[str] = SPAWNING_TOOLS) -> None:
        if not _HAVE_LANGCHAIN:  # pragma: no cover
            raise ImportError("EvidenceMiddleware needs the [deep] extra (langchain)")
        super().__init__()
        self.ledger = ledger
        self.role = role
        self.model = model
        self.spawning_tools = frozenset(spawning_tools)
        if budget_node is None:
            from . import budget as budget_mod
            budget_node = budget_mod.current_budget()
        self.budget_node = budget_node
        self.tools = []

    @property
    def name(self) -> str:
        return "EvidenceMiddleware"

    # -- budget context ------------------------------------------------------

    def _scope(self) -> Any:
        """The budget node this call runs in (the active subagent scope when
        it lies under this middleware's node, as ``BudgetMiddleware.node``)."""
        if self.budget_node is None:
            return None
        from . import budget as budget_mod

        act = budget_mod.active_scope()
        if act is not None and act.ledger is self.budget_node.ledger and (
                act is self.budget_node or self.budget_node in act.ancestors()):
            return act
        return self.budget_node

    def _spawned(self, name: str, scope: Any) -> Any:
        """The fresh allocation a spawning tool runs in
        (``SubagentBudgetMiddleware`` opened it just outside this middleware),
        or ``None``."""
        if name not in self.spawning_tools or scope is None or scope.parent is None:
            return None
        if scope.kind != "subagent" or scope.calls or scope.spent or scope.children:
            return None
        return scope

    def _turn(self, call_id: str, state: Any, caller: Any) -> tuple[str | None, float | None]:
        """``(model, cost)`` of the model call that requested ``call_id``."""
        from langchain_core.messages import AIMessage

        model = None
        for m in reversed(_messages_of(state)):
            if isinstance(m, AIMessage) and any((tc.get("id") or "") == call_id for tc in m.tool_calls or []):
                meta = m.response_metadata or {}
                model = meta.get("model_name") or meta.get("model")
                break
        cost = None
        if caller is not None:
            led = caller.ledger
            with led.lock:
                for rec in reversed(led.records):
                    if rec.node == caller.path and (self.role is None or rec.role == self.role) \
                            and rec.outcome != "summary":
                        cost = round(rec.usd, 6)
                        model = model or rec.model
                        break
        return model or self.model, cost

    # -- recording -------------------------------------------------------------

    def _start(self, request: Any) -> dict[str, Any]:
        call = request.tool_call
        name = str(_get(call, "name") or "")
        call_id = str(_get(call, "id") or "")
        scope = self._scope()
        spawned = self._spawned(name, scope)
        caller = spawned.parent if spawned is not None else scope
        model, turn_usd = self._turn(call_id, request.state, caller)
        return {"name": name, "call_id": call_id, "args": _get(call, "args"), "scope": scope,
                "spawned": spawned, "model": model, "turn_usd": turn_usd, "t0": time.monotonic(),
                "parent_call": _PARENT_CALL.get()}

    def _enter(self, ctx: dict[str, Any]) -> Any:
        """While a spawning call runs, its id is the subagent's parent call."""
        if ctx["name"] in self.spawning_tools and ctx["call_id"]:
            return _PARENT_CALL.set(ctx["call_id"])
        return None

    @staticmethod
    def _leave(token: Any) -> None:
        if token is not None:
            try:
                _PARENT_CALL.reset(token)
            except ValueError:  # pragma: no cover - reset from another context: leave it
                pass

    def _record(self, ctx: dict[str, Any], msg: Any, *, error: BaseException | None = None) -> EvidenceRecord:
        name = ctx["name"]
        result, why = (tool_result_of(name, msg) if msg is not None
                       else (None, "the call raised; not evidence either way"))
        status = str(getattr(msg, "status", None) or ("error" if error is not None else "success"))
        meta: dict[str, Any] = {
            "role": self.role, "model": ctx["model"], "turn_usd": ctx["turn_usd"],
            "parent_call": ctx.get("parent_call"),
            "scope": (ctx["spawned"].parent.path if ctx["spawned"] is not None
                      else getattr(ctx["scope"], "path", None)),
            "cost_usd": (round(ctx["spawned"].spent, 6) if ctx["spawned"] is not None
                         else (0.0 if name not in self.spawning_tools else None)),
        }
        if result is not None:
            trust, reason = result["trust"], result.get("trust_reason") or ""
            if status == "error" and result.get("ok"):  # pragma: no cover - a tool never does this
                trust, reason = "not_verified", "the ToolMessage is an error"
        else:
            trust, reason = "not_verified", why
            meta["ok"] = error is None and status != "error"
            if error is not None:
                meta.update(error=f"{type(error).__name__}: {error}"[:2000], error_kind="raised")
            elif status == "error":
                meta.update(error=str(getattr(msg, "content", ""))[:2000], error_kind="tool_error")
        if result is None or result.get("elapsed_s") is None:
            meta["elapsed_s"] = round(time.monotonic() - ctx["t0"], 4)
        return self.ledger.append(
            tool=name, tool_call_id=ctx["call_id"], args=ctx["args"], result=result,
            content=getattr(msg, "content", None), status=status, trust=trust, trust_reason=reason,
            has_tool_result=result is not None, **meta)

    def _finish(self, ctx: dict[str, Any], out: Any) -> Any:
        from langgraph.types import Command

        msg = _tool_message_of(out, ctx["call_id"])
        rec = self._record(ctx, msg)
        view = rec.state_view()
        if msg is None:  # nothing to attach the record to: the ledger has it
            return out
        tagged = _with_evidence_id(msg, rec.id)
        if isinstance(out, Command):
            update = out.update
            if not isinstance(update, Mapping):  # a list-of-tuples update: leave it alone
                return out
            msgs = [tagged if m is msg else m for m in (update.get("messages") or [])]
            new_update = {**update, "messages": msgs,
                          STATE_KEY: merge_evidence(update.get(STATE_KEY), [view])}
            return Command(graph=out.graph, update=new_update, resume=out.resume, goto=out.goto)
        return Command(update={"messages": [tagged], STATE_KEY: [view]})

    def _failed(self, ctx: dict[str, Any], exc: BaseException) -> None:
        from langgraph.errors import GraphBubbleUp

        if isinstance(exc, GraphBubbleUp) or not isinstance(exc, Exception):
            return  # an interrupt (HITL) or a cancellation: the call has not happened yet
        self._record(ctx, None, error=exc)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        ctx = self._start(request)
        token = self._enter(ctx)
        try:
            out = handler(request)
        except BaseException as exc:
            self._failed(ctx, exc)
            raise
        finally:
            self._leave(token)
        return self._finish(ctx, out)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        ctx = self._start(request)
        token = self._enter(ctx)
        try:
            out = await handler(request)
        except BaseException as exc:
            self._failed(ctx, exc)
            raise
        finally:
            self._leave(token)
        return self._finish(ctx, out)


# ---------------------------------------------------------------------------
# Hook for agent.default_middleware
# ---------------------------------------------------------------------------

def _role_model(settings: Any, role: str | None) -> str | None:
    if not role:
        return None
    try:
        from ..agent import resolve_role_model

        if isinstance(settings, Mapping):
            return resolve_role_model(settings, role)
    except Exception:  # noqa: BLE001 - the model name is informational here
        pass
    from . import budget as budget_mod

    try:
        return budget_mod._role_model(settings, role)
    except Exception:  # noqa: BLE001
        return None


def make_middleware(settings: Any = None, *, role: str | None = None,
                    ledger: EvidenceLedger | None = None, **kwargs: Any) -> EvidenceMiddleware | None:
    """An :class:`EvidenceMiddleware` for one agent writing to ``ledger`` or
    the :func:`~tfl_deep_agent.evidence.ledger.use_evidence` ledger; ``None``
    when there is neither."""
    if ledger is None:
        ledger = current_evidence()
        if ledger is None:
            return None
    kwargs.setdefault("model", _role_model(settings, role))
    return EvidenceMiddleware(ledger, role=role, **kwargs)


def state_records(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The compact evidence records of a final agent state."""
    return list(state.get(STATE_KEY) or [])


__all__ = [
    "SPAWNING_TOOLS",
    "STATE_KEY",
    "EvidenceMiddleware",
    "EvidenceState",
    "current_parent_call",
    "make_middleware",
    "merge_evidence",
    "state_records",
    "tool_result_of",
]
