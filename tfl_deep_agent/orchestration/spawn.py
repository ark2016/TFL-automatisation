"""``spawn_subagent`` and ``budget_status``: ad-hoc subagents the agent creates itself (P4).

architecture.html, "Динамические субагенты и workflow"::

    spawn_subagent(name="nerode-classes", instructions="...", model="sonnet",
                   tools=["sample_words", "check_membership", "nerode_probe", "get_theorem"],
                   budget_usd=0.40, output_schema={...}, reason="...")

Under the hood: ``langchain.agents.create_agent`` with

* the model the policy admits (:mod:`.model_policy`: ``settings.models.allowed``,
  ``opus_needs_reason``, ``TFL_MODEL_OVERRIDE``), built by the run's
  ``model_factory(role_key, model_id)`` with ``role_key = "spawn:<name>"``;
* a whitelisted subset of the P1 tools (:data:`CHILD_TOOL_GROUPS`; never
  ``submit_report`` or the sandbox's ``execute``). The orchestration tools
  (``spawn_subagent`` / ``run_workflow``) are given only when named in
  ``tools`` and only while the child's depth stays below
  ``limits.max_spawn_depth``; ``budget_status`` may always be named;
* a child budget allocation carved from the caller's scope
  (``BudgetNode.allocate(kind="subagent")``: at most ``per_subagent_max_usd``,
  never the caller's reserve); the child's own :class:`BudgetMiddleware`
  charges it, lowers ``max_tokens`` to what fits (``fit_max_tokens``, floor
  :data:`MIN_OUTPUT_TOKENS`) and refuses past it; the unspent part returns to
  the caller when the child ends;
* the run's call limits (``agent.call_limit_middleware``, shared counters),
  the concurrency middleware (process-wide semaphore + the run's
  ``max_parallel``) and the :class:`EvidenceMiddleware` over the run's evidence
  ledger, so every oracle call inside the child is recorded under a run-wide id
  that the report can cite (``parent_call`` = the spawning call's id);
* depth tracking: the main agent is depth 0; a child runs at its caller's depth
  + 1, which may not exceed ``limits.max_spawn_depth``.

Every refusal is a ``ToolResult`` failure (``ok: false``) whose ``error`` lists
each problem and how to fix it, so the model can correct the call. A
subagent's answer is never evidence (``trust: not_verified``); the result lists
the evidence ids of the oracle calls the child made.

Wiring (the integration step, ``agent.py``)::

    ctx = OrchestrationContext.create(cfg, model_factory=factory, budget=node, evidence=ledger)
    graph = create_tfl_agent(cfg, ..., extra_tools=make_orchestration_tools(ctx))
    ctx.limits = graph.tfl_limits      # children count against the run's limits

Children are built when the tool is called, so binding ``limits`` after the
build is fine. Without ``limits`` the context makes its own
``RunLimits.from_settings`` (shared by its whole tree).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from ..middleware import budget as budget_mod
from ..tools import base as tools_base
from . import model_policy

SPAWN_TOOL = "spawn_subagent"
WORKFLOW_TOOL = "run_workflow"
STATUS_TOOL = "budget_status"
ORCHESTRATION_TOOLS: tuple[str, ...] = (SPAWN_TOOL, WORKFLOW_TOOL, STATUS_TOOL)
#: orchestration tools that create agents (depth-limited; refused in budget wrap-up)
SPAWNING_TOOLS: frozenset[str] = frozenset({SPAWN_TOOL, WORKFLOW_TOOL})

#: P1 tool groups an ad-hoc agent may draw from. Not "report" (submit_report
#: belongs to the main agent) and not the sandbox (coder role only).
CHILD_TOOL_GROUPS: tuple[str, ...] = ("ir", "words", "reg", "cfl", "dcfl", "ll", "theory", "lean")

NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"
_NAME_RE = re.compile(NAME_PATTERN)
MAX_TOOLS = 40
MAX_INSTRUCTIONS_CHARS = 40_000
MAX_SCHEMA_CHARS = 20_000
MAX_REASON_CHARS = 2_000
#: the child's answer as returned to the caller (the full text stays in the child's messages)
MAX_ANSWER_CHARS = 12_000
#: max_tokens floor of an ad-hoc call when its budget lowers max_tokens
MIN_OUTPUT_TOKENS = 4096
#: name of the tool through which a child returns its structured answer
STRUCTURED_TOOL_NAME = "final_answer"

ModelFactory = Callable[[str, str], Any]

NOT_EVIDENCE = ("a subagent's answer is not evidence; cite the evidence ids of the oracle calls it made "
                "(listed under 'evidence')")

#: System prompt of an ad-hoc subagent; the caller's instructions come as the
#: first user message. Generic rules only (no theory text: the child reads
#: theory through get_theorem / the skills of the main agent).
ADHOC_SYSTEM_PROMPT = """\
You are an ad-hoc subagent of the TFL Deep Agent (Theory of Formal Languages tasks), created by
another agent for one narrow subtask. Your caller's instructions are in the first user message.

Rules:
- Do only the subtask. Use the tools you were given; you have no others.
- Every concrete claim (a word is or is not in the language, a decomposition, a construction, a
  table) must come from a tool call, not from your own judgement. Say plainly what you could not
  check.
- Theory statements only through get_theorem / list_theorems when you have them; never state a
  lemma from memory.
- You never decide the final verdict of the task and you never call anything "verified": trust
  levels are assigned by code from the tool results.
- When your budget or call limit says to wrap up, return what you have at once.
- Finish with your answer to the caller as plain text (or through the final_answer tool when you
  were given an output schema). Name the tool calls your answer rests on.
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OrchestrationError(ValueError):
    """A spawn or workflow request that is refused. ``problems`` lists each
    issue; ``str(exc)`` is the model-readable message; ``data`` holds numbers."""

    def __init__(self, tool: str, problems: Sequence[str], *, hint: str | None = None,
                 data: Mapping[str, Any] | None = None) -> None:
        self.tool = tool
        self.problems = [p for p in problems if p]
        self.hint = hint
        self.data = dict(data or {})
        lines = [f"{tool} refused ({len(self.problems)} problem{'s' if len(self.problems) != 1 else ''}):"]
        lines += [f"- {p}" for p in self.problems]
        lines.append(hint or "Fix these and call again, or continue without it.")
        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def _settings_dict(settings: Any) -> dict[str, Any]:
    from ..agent import settings_dict

    return settings_dict(settings)


def _section(cfg: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = cfg.get(name) or {}
    return value if isinstance(value, Mapping) else {}


@dataclasses.dataclass
class OrchestrationContext:
    """What the orchestration tools of one agent need: the run's settings,
    model factory, budget scope, evidence ledger and limits, plus the depth of
    the agent that holds the tools (main = 0). Children share everything but
    the depth (:meth:`child`)."""

    settings: dict[str, Any]
    model_factory: ModelFactory
    budget: Any = None                       # BudgetNode | None (no budget: scripted runs only)
    evidence: Any = None                     # EvidenceLedger | None
    limits: Any = None                       # agent.RunLimits (shared by the tree)
    depth: int = 0
    budget_options: dict[str, Any] = dataclasses.field(default_factory=dict)
    min_output_tokens: int = MIN_OUTPUT_TOKENS
    listeners: list[Callable[[dict[str, Any]], None]] = dataclasses.field(default_factory=list)
    history: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    _lock: Any = dataclasses.field(default_factory=threading.Lock, repr=False)

    @classmethod
    def create(cls, settings: Any = None, *, model_factory: ModelFactory, budget: Any = None,
               evidence: Any = None, limits: Any = None, depth: int = 0,
               budget_options: Mapping[str, Any] | None = None,
               listeners: Iterable[Callable[[dict[str, Any]], None]] = ()) -> OrchestrationContext:
        """``budget``: a ``BudgetLedger`` / ``BudgetNode``, ``None`` = the
        enclosing ``budget.use_budget`` scope, ``False`` = none (scripted
        models only). ``evidence``: an ``EvidenceLedger``, ``None`` = the
        enclosing ``use_evidence`` ledger, ``False`` = none."""
        from ..agent import RunLimits, is_live_factory
        from ..evidence.ledger import current_evidence

        if model_factory is None:
            raise TypeError("OrchestrationContext needs an explicit model_factory")
        cfg = _settings_dict(settings)
        opts: dict[str, Any] = {}
        if budget is None:
            budget = budget_mod.current_budget()
            cur = budget_mod._CURRENT.get()
            if cur is not None:
                opts.update(cur[1])
        elif budget is False:
            budget = None
        if isinstance(budget, budget_mod.BudgetLedger):
            budget = budget.root
        if budget is not None and not isinstance(budget, budget_mod.BudgetNode):
            raise TypeError(f"budget must be a BudgetLedger, a BudgetNode, None or False, "
                            f"not {type(budget).__name__}")
        if budget is None and is_live_factory(model_factory):
            raise ValueError("a live model factory needs a budget scope for spawned agents")
        if evidence is None:
            evidence = current_evidence()
        elif evidence is False:
            evidence = None
        opts.update(budget_options or {})
        return cls(settings=cfg, model_factory=model_factory, budget=budget, evidence=evidence,
                   limits=limits if limits is not None else RunLimits.from_settings(cfg),
                   depth=int(depth), budget_options=opts, listeners=list(listeners))

    # -- limits -----------------------------------------------------------------

    @property
    def max_spawn_depth(self) -> int:
        if self.limits is not None:
            return int(self.limits.max_spawn_depth)
        return int(_section(self.settings, "limits").get("max_spawn_depth", 2))

    @property
    def max_parallel(self) -> int:
        if self.limits is not None:
            return int(self.limits.max_parallel)
        return int(_section(self.settings, "limits").get("max_parallel", 4))

    @property
    def max_model_calls(self) -> int:
        return int(_section(self.settings, "limits").get("max_model_calls", 80))

    @property
    def per_subagent_max_usd(self) -> float | None:
        if self.budget is not None and self.budget.ledger.per_child_max_usd is not None:
            return float(self.budget.ledger.per_child_max_usd)
        v = _section(self.settings, "budget").get("per_subagent_max_usd")
        return float(v) if v is not None else None

    def can_spawn(self) -> bool:
        """An agent at this depth may create a child (child depth <= max)."""
        return self.depth + 1 <= self.max_spawn_depth

    # -- budget scope -----------------------------------------------------------

    def scope(self) -> Any:
        """The budget node the current call runs in: the active subagent
        scope when it lies under this context's node (a fixed role or a
        child that holds the tools), else the context's node."""
        base = self.budget
        if base is None:
            return None
        act = budget_mod.active_scope()
        if act is not None and act.ledger is base.ledger and (act is base or base in act.ancestors()):
            return act
        return base

    def free_usd(self, scope: Any = None) -> float | None:
        """What a new allocation may take now: the scope's available money
        above its reserve (``None`` without a budget)."""
        scope = scope if scope is not None else self.scope()
        if scope is None:
            return None
        with scope.ledger.lock:
            return max(0.0, scope._available() - scope.reserve)

    def child_reserve(self, amount: float) -> float:
        """The reserve of a child allocation for its final answer (as
        ``SubagentBudgetMiddleware``): ``min(root reserve, amount / 4)``."""
        root_reserve = self.budget.ledger.root.reserve if self.budget is not None else 0.0
        return max(0.0, min(root_reserve, amount / 4, amount / 2))

    # -- tree ---------------------------------------------------------------------

    def child(self, budget: Any = None) -> OrchestrationContext:
        """The context of a child agent: one level deeper, charging ``budget``."""
        return dataclasses.replace(self, depth=self.depth + 1,
                                   budget=budget if budget is not None else self.budget)

    def emit(self, event: dict[str, Any]) -> None:
        """Log an orchestration event (``history``), tell the listeners and the
        budget ledger's listeners (the UI's live view)."""
        ev = {"ts": round(time.time(), 3), "depth": self.depth, **event}
        with self._lock:
            self.history.append(ev)
        for fn in self.listeners:
            try:
                fn(dict(ev))
            except Exception:  # noqa: BLE001, S110 - a broken listener never breaks a run
                pass
        if self.budget is not None:
            try:
                self.budget.ledger._emit({"event": "orchestration", **ev})
            except Exception:  # noqa: BLE001, S110
                pass


# ---------------------------------------------------------------------------
# Tools of a child
# ---------------------------------------------------------------------------

def whitelisted_tool_names() -> list[str]:
    """Registered P1 tools an ad-hoc agent may get (groups loaded on demand)."""
    tools_base.load_groups([g for g in CHILD_TOOL_GROUPS if g in tools_base.GROUP_MODULES])
    return [s.name for s in tools_base.REGISTRY.specs(groups=CHILD_TOOL_GROUPS)]


def _groups_text() -> str:
    tools_base.load_groups([g for g in CHILD_TOOL_GROUPS if g in tools_base.GROUP_MODULES])
    parts = []
    for g in CHILD_TOOL_GROUPS:
        names = [s.name for s in tools_base.REGISTRY.specs(groups=[g])]
        if names:
            parts.append(f"{g}: {', '.join(names)}")
    return "; ".join(parts)


def resolve_tool_names(requested: Sequence[str], *, ctx: OrchestrationContext, what: str,
                       allow_spawning: bool = True) -> tuple[list[str], list[str]]:
    """``(tool names, problems)``. Entries are tool names or ``group:<name>``
    (every tool of a whitelisted group). ``allow_spawning`` False forbids
    ``spawn_subagent`` / ``run_workflow`` (workflow steps)."""
    allowed = set(whitelisted_tool_names())
    out: list[str] = []
    problems: list[str] = []
    unknown: list[str] = []
    for raw in requested:
        name = str(raw).strip()
        if name.startswith("group:"):
            g = name[len("group:"):]
            if g not in CHILD_TOOL_GROUPS:
                problems.append(f"{what}: tool group {g!r} is not available to subagents; "
                                f"groups: {', '.join(CHILD_TOOL_GROUPS)}")
                continue
            for s in tools_base.REGISTRY.specs(groups=[g]):
                if s.name not in out:
                    out.append(s.name)
            continue
        if name in SPAWNING_TOOLS:
            if not allow_spawning:
                problems.append(f"{what}: {name} is not available inside workflow steps")
            elif ctx.depth + 2 > ctx.max_spawn_depth:
                problems.append(
                    f"{what}: {name} cannot be given: the child would run at depth {ctx.depth + 1} and "
                    f"limits.max_spawn_depth = {ctx.max_spawn_depth}, so it could not spawn anything")
            elif name not in out:
                out.append(name)
            continue
        if name == STATUS_TOOL:
            if name not in out:
                out.append(name)
            continue
        if name not in allowed:
            unknown.append(name)
            continue
        if name not in out:
            out.append(name)
    if unknown:
        hint = ""
        if any(u in ("submit_report", "execute", "task") for u in unknown):
            hint = " (submit_report, execute and task are never given to ad-hoc agents)"
        problems.append(f"{what}: unknown or not allowed tool(s) {sorted(unknown)}{hint}. Available: "
                        f"{_groups_text()}; also budget_status, and group:<name> for a whole group.")
    if len(out) > MAX_TOOLS:
        problems.append(f"{what}: {len(out)} tools requested, at most {MAX_TOOLS}")
    return out, problems


def build_tools(names: Sequence[str], child_ctx: OrchestrationContext) -> list[Any]:
    """LangChain tools for ``names``: P1 tools from the registry, the
    orchestration tools bound to ``child_ctx``."""
    orch = {t.name: t for t in make_orchestration_tools(child_ctx,
                                                        names=[n for n in names if n in ORCHESTRATION_TOOLS])}
    out = []
    for n in names:
        out.append(orch[n] if n in orch else tools_base.REGISTRY.as_langchain_tool(n))
    return out


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

def check_output_schema(schema: Any, *, what: str) -> list[str]:
    """Problems with an ``output_schema`` (a JSON schema of an object)."""
    if schema is None:
        return []
    if not isinstance(schema, Mapping):
        return [f"{what}: output_schema must be a JSON schema object"]
    problems = []
    size = len(json.dumps(schema, default=str))
    if size > MAX_SCHEMA_CHARS:
        problems.append(f"{what}: output_schema is {size} characters, at most {MAX_SCHEMA_CHARS}")
    if schema.get("type", "object") != "object":
        problems.append(f"{what}: output_schema must describe an object (\"type\": \"object\")")
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema ships with the [deep] stack
        return problems
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.SchemaError as exc:
        problems.append(f"{what}: output_schema is not a valid JSON schema: {exc.message}")
    return problems


def _response_format(schema: Mapping[str, Any] | None) -> Any:
    if schema is None:
        return None
    from langchain.agents.structured_output import ToolStrategy

    spec = copy.deepcopy(dict(schema))
    spec.setdefault("type", "object")
    spec["title"] = STRUCTURED_TOOL_NAME
    spec.setdefault("description", "Return your final structured answer to the caller.")
    return ToolStrategy(spec)


def validate_structured(value: Any, schema: Mapping[str, Any] | None) -> list[str]:
    """Errors of a structured answer against its schema (empty = valid)."""
    if schema is None:
        return []
    try:
        import jsonschema
    except ImportError:  # pragma: no cover
        return []
    validator = jsonschema.validators.validator_for(schema)(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))][:20]


# ---------------------------------------------------------------------------
# Building and running one child
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ChildSpec:
    """One ad-hoc agent to build: a spawned subagent or a workflow step."""

    label: str                       # spawn name or step id (budget node name, events)
    role_key: str                    # model_factory key / budget + evidence role ("spawn:x", "workflow:y")
    system_prompt: str
    task: str                        # the first user message
    choice: model_policy.ModelChoice
    tool_names: list[str]
    output_schema: dict[str, Any] | None = None
    kind: str = "subagent"           # budget node kind: subagent | branch


def estimate_first_call_tokens(spec: ChildSpec, tools: Sequence[Any], *, extra_chars: int = 0) -> int:
    """The local input-token estimate of the child's first call (the same
    heuristic the budget middleware uses)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    all_tools = list(tools)
    if spec.output_schema is not None:
        all_tools.append({"name": STRUCTURED_TOOL_NAME, "parameters": spec.output_schema})
    req = SimpleNamespace(system_message=SystemMessage(spec.system_prompt),
                          messages=[HumanMessage(spec.task + "x" * max(0, extra_chars))], tools=all_tools)
    return budget_mod.estimate_input_tokens(req)


def call_floor_usd(model: str, input_tokens: int, *, min_output_tokens: int = MIN_OUTPUT_TOKENS) -> float:
    """Worst case of the cheapest call the budget would still make for
    ``model`` (``max_tokens`` lowered to the floor)."""
    return config.worst_case_usd(model, input_tokens, min_output_tokens)


def child_middleware(ctx: OrchestrationContext, spec: ChildSpec, node: Any) -> list[Any]:
    """Call limits, budget, concurrency and evidence middleware of a child."""
    from ..agent import call_limit_middleware
    from ..middleware.concurrency import ConcurrencyMiddleware

    mw: list[Any] = []
    if ctx.limits is not None:
        mw.append(call_limit_middleware(ctx.limits, role=spec.role_key))
    if node is not None:
        opts = {"fit_max_tokens": True, "min_output_tokens": ctx.min_output_tokens, **ctx.budget_options}
        mw.append(budget_mod.BudgetMiddleware(node, role=spec.role_key, model=spec.choice.effective_model,
                                              **opts))
    mw.append(ConcurrencyMiddleware(run_semaphore=getattr(ctx.limits, "semaphore", None)))
    if ctx.evidence is not None:
        from ..middleware.evidence import EvidenceMiddleware

        mw.append(EvidenceMiddleware(ctx.evidence, role=spec.role_key, model=spec.choice.effective_model,
                                     budget_node=node))
    return mw


def build_child_agent(ctx: OrchestrationContext, spec: ChildSpec, node: Any) -> Any:
    """The ``create_agent`` graph of one child (not run)."""
    from langchain.agents import create_agent

    model = ctx.model_factory(spec.role_key, spec.choice.effective_model)
    tools = build_tools(spec.tool_names, ctx.child(budget=node))
    graph_name = re.sub(r"[^A-Za-z0-9_-]", "_", spec.role_key)[:64]
    return create_agent(model, tools, system_prompt=spec.system_prompt,
                        middleware=child_middleware(ctx, spec, node),
                        response_format=_response_format(spec.output_schema), name=graph_name)


def _text_of(msg: Any) -> str:
    text = getattr(msg, "text", None)  # a str (a callable str subclass in langchain-core 1.x)
    if isinstance(text, str) and text:
        return str(text)
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, Mapping) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - 60] + f"\n[... {len(text) - limit + 60} characters cut]", True


def summarize_child_state(state: Mapping[str, Any], spec: ChildSpec) -> dict[str, Any]:
    """Status, answer, structured answer and evidence ids of a finished child."""
    from langchain_core.messages import AIMessage, ToolMessage

    messages = list(state.get("messages") or [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    meta = (last_ai.response_metadata or {}) if last_ai is not None else {}
    stop = meta.get("stop_reason")
    status = "ok"
    if stop == "budget_exhausted":
        status = "budget_exhausted"
    elif stop == "call_limit":
        status = "call_limit"
    answer = ""
    if last_ai is not None:
        answer = _text_of(last_ai)
    out: dict[str, Any] = {"status": status}
    if spec.output_schema is not None:
        structured = state.get("structured_response")
        if structured is None:
            out["structured"] = None
            out["structured_errors"] = ["the subagent did not return the structured answer (final_answer)"]
        else:
            if isinstance(structured, BaseModel):
                structured = structured.model_dump(mode="json")
            out["structured"] = tools_base.to_jsonable(structured)
            errs = validate_structured(out["structured"], spec.output_schema)
            if errs:
                out["structured_errors"] = errs
        if not answer:
            answer = "(structured answer)" if structured is not None else ""
    answer, cut = _truncate(answer, MAX_ANSWER_CHARS)
    out["answer"] = answer
    if cut:
        out["answer_truncated"] = True
    tool_calls = [m for m in messages if isinstance(m, ToolMessage)]
    out["tool_calls"] = len(tool_calls)
    records = state.get("tfl_evidence") or []
    out["evidence"] = [
        {"id": r.get("id"), "tool": r.get("tool"), "ok": r.get("ok"), "trust": r.get("trust")}
        for r in records if isinstance(r, Mapping) and r.get("has_tool_result")
    ]
    if records:
        out["non_evidence_calls"] = sum(1 for r in records if isinstance(r, Mapping)
                                        and not r.get("has_tool_result"))
    return out


def _is_bubble_up(exc: BaseException) -> bool:
    try:
        from langgraph.errors import GraphBubbleUp
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, GraphBubbleUp)


def run_child(ctx: OrchestrationContext, spec: ChildSpec, node: Any, *,
              recursion_limit: int | None = None) -> dict[str, Any]:
    """Build and run one child inside its budget scope; never raises for a
    child failure (interrupts and cancellations propagate). Closes ``node``."""
    from langchain_core.messages import HumanMessage

    t0 = time.monotonic()
    limit = recursion_limit or 2 * ctx.max_model_calls + 10
    scope_cm = budget_mod.enter_scope(node) if node is not None else contextlib.nullcontext()
    try:
        with scope_cm:
            try:
                graph = build_child_agent(ctx, spec, node)
                state = graph.invoke({"messages": [HumanMessage(spec.task)]}, {"recursion_limit": limit})
                result = summarize_child_state(state, spec)
            except Exception as exc:
                if _is_bubble_up(exc):
                    raise
                kind = type(exc).__name__
                result = {"status": "error", "error": f"{kind}: {str(exc)[:1500]}", "answer": "",
                          "evidence": []}
                if kind == "GraphRecursionError":
                    result["error"] = (f"the subagent hit the step limit ({limit} graph steps) before "
                                       "answering")
    finally:
        budget = None
        if node is not None:
            st = node.status()
            returned = node.close()
            budget = {"node": st["node"], "allocated_usd": round(st["limit_usd"], 6),
                      "spent_usd": round(st["spent_usd"], 6), "returned_usd": round(returned, 6),
                      "calls": st["calls"], "refusals": st["refusals"], "state": st["state"]}
    result["budget"] = budget
    result["elapsed_s"] = round(time.monotonic() - t0, 3)
    return result


# ---------------------------------------------------------------------------
# spawn_subagent
# ---------------------------------------------------------------------------

class SpawnArgs(BaseModel):
    """Arguments of ``spawn_subagent``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="short id of the subagent: lowercase letters, digits, '-' or '_' "
                                  "(budget scope and log label), e.g. 'nerode-classes'")
    instructions: str = Field(min_length=1, max_length=MAX_INSTRUCTIONS_CHARS,
                              description="the full subtask: everything the subagent needs (IR, words, what "
                                          "to return); it sees nothing of your context")
    model: str = Field(description="haiku | sonnet | opus (only tiers allowed by settings)")
    tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS,
                             description="tool names from the whitelist, or 'group:<name>'; empty = no tools")
    budget_usd: float = Field(gt=0, description="money for this subagent, taken from your remainder "
                                                "(at most per_subagent_max_usd)")
    output_schema: dict[str, Any] | None = Field(
        default=None, description="optional JSON schema (an object) of a structured answer")
    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS,
                               description="why this model (required for opus); logged")


def prepare_spawn(ctx: OrchestrationContext, args: SpawnArgs) -> tuple[ChildSpec, float, Any]:
    """Validate a spawn request and allocate its budget:
    ``(spec, budget_usd, node)``. Raises :class:`OrchestrationError`."""
    what = f"subagent {args.name!r}"
    problems: list[str] = []
    data: dict[str, Any] = {}
    if not _NAME_RE.match(args.name):
        problems.append(f"name {args.name!r} must match {NAME_PATTERN} (lowercase, digits, '-', '_')")
    if not ctx.can_spawn():
        problems.append(
            f"spawn depth exceeded: this agent runs at depth {ctx.depth}, a child would be at depth "
            f"{ctx.depth + 1}, and limits.max_spawn_depth = {ctx.max_spawn_depth}. Do the work yourself "
            "or return to your caller")
    choice = None
    try:
        choice = model_policy.resolve_model_choice(ctx.settings, args.model, args.reason, what=what)
    except model_policy.ModelPolicyError as exc:
        problems.append(str(exc))
    names, tool_problems = resolve_tool_names(args.tools, ctx=ctx, what=what)
    problems += tool_problems
    problems += check_output_schema(args.output_schema, what=what)
    scope = ctx.scope()
    budget = float(args.budget_usd)
    if scope is not None:
        if scope.in_wrap_up():
            problems.append(f"the budget scope {scope.path} is in wrap-up: no new subagents; "
                            "finish with what is established")
        cap = ctx.per_subagent_max_usd
        free = ctx.free_usd(scope) or 0.0
        data.update(free_usd=round(free, 6), per_subagent_max_usd=cap, requested_usd=budget)
        if cap is not None and budget > cap + budget_mod.EPS:
            problems.append(f"budget_usd ${budget:.4f} exceeds budget.per_subagent_max_usd ${cap:.2f}")
        if budget > free + budget_mod.EPS:
            problems.append(f"budget_usd ${budget:.4f} does not fit: {scope.path} has ${free:.4f} free above "
                            f"its reserve ${scope.reserve:.2f}")
    if problems or choice is None:
        raise OrchestrationError(SPAWN_TOOL, problems, data=data, hint=_spawn_hint(ctx, scope))
    spec = ChildSpec(label=args.name, role_key=f"spawn:{args.name}", system_prompt=ADHOC_SYSTEM_PROMPT,
                     task=args.instructions, choice=choice, tool_names=names,
                     output_schema=dict(args.output_schema) if args.output_schema is not None else None,
                     kind="subagent")
    node = None
    if scope is not None:
        reserve = ctx.child_reserve(budget)
        tokens = estimate_first_call_tokens(spec, build_tools(names, ctx.child()))
        floor = call_floor_usd(choice.effective_model, tokens, min_output_tokens=ctx.min_output_tokens)
        if floor > budget - reserve + budget_mod.EPS:
            raise OrchestrationError(SPAWN_TOOL, [(
                f"budget_usd ${budget:.4f} cannot pay even one {choice.effective_tier or choice.effective_model} "
                f"call: ~{tokens} input tokens + {ctx.min_output_tokens} output tokens cost up to ${floor:.4f}, "
                f"plus the child's reserve ${reserve:.4f} -> at least ${floor + reserve:.4f}")],
                data={**data, "floor_usd": round(floor + reserve, 6)}, hint=_spawn_hint(ctx, scope))
        try:
            node = scope.allocate(f"spawn:{args.name}", budget, kind="subagent", reserve_usd=reserve)
        except budget_mod.BudgetExceeded as exc:
            raise OrchestrationError(SPAWN_TOOL, [str(exc)], data=data, hint=_spawn_hint(ctx, scope)) from None
    return spec, budget, node


def _spawn_hint(ctx: OrchestrationContext, scope: Any) -> str:
    parts = []
    if scope is not None:
        free = ctx.free_usd(scope) or 0.0
        cap = ctx.per_subagent_max_usd
        most = min(free, cap) if cap is not None else free
        parts.append(f"now at most ${most:.4f} per subagent")
    parts.append(f"allowed tiers: {', '.join(model_policy.allowed_tiers(ctx.settings)) or 'none'}")
    parts.append(f"depth {ctx.depth} of max {ctx.max_spawn_depth}")
    return "Fix these and call again (" + "; ".join(parts) + "), or continue without a subagent."


def spawn_subagent(ctx: OrchestrationContext, args: SpawnArgs | Mapping[str, Any]) -> tools_base.ToolResult:
    """Run one ad-hoc subagent; a ``ToolResult`` (never raises for a refusal)."""
    t0 = time.monotonic()
    if not isinstance(args, SpawnArgs):
        try:
            args = SpawnArgs.model_validate(dict(args))
        except ValidationError as exc:
            return _failure(SPAWN_TOOL, f"{SPAWN_TOOL} refused: invalid arguments: "
                                        f"{tools_base._validation_message(exc)}", kind="invalid_input")
    try:
        spec, budget, node = prepare_spawn(ctx, args)
    except OrchestrationError as exc:
        ctx.emit({"event": "spawn_refused", "name": args.name, "problems": exc.problems})
        return _failure(SPAWN_TOOL, str(exc), data={"refused": True, "problems": exc.problems, **exc.data})
    ctx.emit({"event": "spawn_start", "name": args.name, "model": spec.choice.to_dict(),
              "budget_usd": budget, "tools": spec.tool_names, "child_depth": ctx.depth + 1})
    result = run_child(ctx, spec, node)
    data = {"name": args.name, **result, "model": spec.choice.to_dict(), "tools": spec.tool_names,
            "depth": ctx.depth + 1, "note": NOT_EVIDENCE}
    ctx.emit({"event": "spawn_end", "name": args.name, "status": result["status"],
              "model": spec.choice.effective_model, "spent_usd": (result.get("budget") or {}).get("spent_usd")})
    res = tools_base.ToolResult.success(data, trust="not_verified", reason=NOT_EVIDENCE, tool=SPAWN_TOOL)
    return res.model_copy(update={"elapsed_s": round(time.monotonic() - t0, 4)})


def _failure(tool: str, message: str, *, kind: str = "invalid_input", data: Any = None) -> tools_base.ToolResult:
    return tools_base.ToolResult.failure(message, kind=kind, data=data, tool=tool,  # type: ignore[arg-type]
                                         reason=f"{tool} was refused; nothing was run")


# ---------------------------------------------------------------------------
# budget_status
# ---------------------------------------------------------------------------

class BudgetStatusArgs(BaseModel):
    """Arguments of ``budget_status``."""

    model_config = ConfigDict(extra="forbid")

    detail: bool = Field(default=False, description="include the per-subagent budget tree")


def _compact_status(st: Mapping[str, Any], *, children: bool) -> dict[str, Any]:
    out = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in st.items() if k != "children"}
    if children and st.get("children"):
        out["children"] = [_compact_status(c, children=True) for c in st["children"]]
    return out


def budget_status(ctx: OrchestrationContext, args: BudgetStatusArgs | Mapping[str, Any] | None = None
                  ) -> tools_base.ToolResult:
    """The caller's budget scope, limits, depth and per-model call costs."""
    if not isinstance(args, BudgetStatusArgs):
        args = BudgetStatusArgs.model_validate(dict(args or {}))
    scope = ctx.scope()
    data: dict[str, Any] = {}
    if scope is None:
        data["budget"] = None
        data["budget_note"] = "no budget ledger in this run (scripted / test run)"
    else:
        free = ctx.free_usd(scope) or 0.0
        cap = ctx.per_subagent_max_usd
        data["budget"] = _compact_status(scope.status(), children=args.detail)
        data["free_for_new_subagent_usd"] = round(min(free, cap) if cap is not None else free, 6)
        data["per_subagent_max_usd"] = cap
        data["wrap_up"] = scope.in_wrap_up()
        data["task_spent_usd"] = round(scope.ledger.spent_usd, 6)
        data["task_limit_usd"] = scope.ledger.root.limit
    data["depth"] = ctx.depth
    data["max_spawn_depth"] = ctx.max_spawn_depth
    data["can_spawn"] = ctx.can_spawn() and not (scope is not None and scope.in_wrap_up())
    data["max_parallel"] = ctx.max_parallel
    if ctx.limits is not None:
        data["limits"] = ctx.limits.status()
    data["models"] = model_policy.model_cost_table(ctx.settings, min_output_tokens=ctx.min_output_tokens)
    data["opus_needs_reason"] = model_policy.opus_needs_reason(ctx.settings)
    data["note"] = ("call_worst_case_usd is reserved before a call at the full max_tokens; ad-hoc agents lower "
                    "max_tokens to what their budget fits, down to call_floor_usd")
    return tools_base.ToolResult.success(data, trust="not_verified",
                                         reason="budget numbers; not evidence about the task", tool=STATUS_TOOL)


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

def _spawn_description(ctx: OrchestrationContext) -> str:
    tiers = "; ".join(f"{t}: {model_policy.TIER_GUIDE[t]}" for t in model_policy.allowed_tiers(ctx.settings))
    cap = ctx.per_subagent_max_usd
    return (
        "Create an ad-hoc subagent for one narrow subtask and wait for its answer. It gets its own context "
        "(only your instructions), the model you pick, the tools you list (from the TFL tool whitelist; "
        "'group:<name>' for a whole group) and budget_usd carved from your remainder"
        + (f" (at most ${cap:.2f})" if cap is not None else "") + ". "
        f"Models: {tiers}. "
        + ("opus needs `reason`. " if model_policy.opus_needs_reason(ctx.settings) else "")
        + "Orchestration tools are not given unless listed, and depth is limited "
          f"(limits.max_spawn_depth = {ctx.max_spawn_depth}). The answer is NOT evidence: cite the evidence "
          "ids of its oracle calls (returned under 'evidence'). Use budget_status first to see what is free. "
          "Prefer the fixed roles (task tool) for the standard path.")


def make_orchestration_tools(ctx: OrchestrationContext, *, names: Iterable[str] | None = None) -> list[Any]:
    """``spawn_subagent``, ``run_workflow`` and ``budget_status`` bound to
    ``ctx`` (or the subset ``names``). Each returns ``(content, artifact)``:
    content is the ``ToolResult`` JSON the model reads, the artifact its dump."""
    from langchain_core.tools import StructuredTool

    from . import workflow as workflow_mod

    wanted = list(names) if names is not None else list(ORCHESTRATION_TOOLS)
    unknown = [n for n in wanted if n not in ORCHESTRATION_TOOLS]
    if unknown:
        raise ValueError(f"unknown orchestration tool(s) {unknown}; known: {list(ORCHESTRATION_TOOLS)}")
    max_chars = tools_base.REGISTRY.limits.max_content_chars

    def as_tool(name: str, schema: type[BaseModel], run: Callable[[Any], tools_base.ToolResult],
                description: str) -> Any:
        def _sync(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            try:
                res = run(kwargs)
            except Exception as exc:
                if _is_bubble_up(exc):
                    raise
                res = tools_base.ToolResult.failure(f"{type(exc).__name__}: {str(exc)[:1500]}",
                                                    kind="internal", tool=name)
            return res.to_content(max_chars), res.model_dump(mode="json")

        async def _async(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            # the child graphs run sync in a worker thread (context variables are copied)
            return await asyncio.to_thread(_sync, **kwargs)

        def _on_validation_error(exc: Exception) -> str:
            msg = tools_base._validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
            return tools_base.ToolResult.failure(f"{name} refused: invalid arguments: {msg}",
                                                 kind="invalid_input", tool=name).to_content(max_chars)

        _sync.__name__ = name
        return StructuredTool.from_function(
            func=_sync, coroutine=_async, name=name, description=description, args_schema=schema,
            infer_schema=False, response_format="content_and_artifact",
            handle_validation_error=_on_validation_error,
            metadata={"tfl_group": "orchestration", "tfl_max_trust": "not_verified", "tfl_depth": ctx.depth})

    builders = {
        SPAWN_TOOL: lambda: as_tool(SPAWN_TOOL, SpawnArgs, lambda kw: spawn_subagent(ctx, kw),
                                    _spawn_description(ctx)),
        WORKFLOW_TOOL: lambda: as_tool(WORKFLOW_TOOL, workflow_mod.WorkflowArgs,
                                       lambda kw: workflow_mod.run_workflow(ctx, kw),
                                       workflow_mod.workflow_description(ctx)),
        STATUS_TOOL: lambda: as_tool(STATUS_TOOL, BudgetStatusArgs, lambda kw: budget_status(ctx, kw),
                                     "Your budget scope now: limit, spent, in flight, reserve, what a new "
                                     "subagent or workflow may take, wrap-up state, run limits, your spawn "
                                     "depth, and the worst-case cost of one call per allowed model. Free."),
    }
    return [builders[n]() for n in wanted]


__all__ = [
    "ADHOC_SYSTEM_PROMPT",
    "CHILD_TOOL_GROUPS",
    "MIN_OUTPUT_TOKENS",
    "ORCHESTRATION_TOOLS",
    "SPAWNING_TOOLS",
    "SPAWN_TOOL",
    "STATUS_TOOL",
    "WORKFLOW_TOOL",
    "BudgetStatusArgs",
    "ChildSpec",
    "OrchestrationContext",
    "OrchestrationError",
    "SpawnArgs",
    "budget_status",
    "build_child_agent",
    "build_tools",
    "call_floor_usd",
    "check_output_schema",
    "estimate_first_call_tokens",
    "make_orchestration_tools",
    "prepare_spawn",
    "resolve_tool_names",
    "run_child",
    "spawn_subagent",
    "summarize_child_state",
    "validate_structured",
    "whitelisted_tool_names",
]
