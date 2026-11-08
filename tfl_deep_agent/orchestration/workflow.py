"""``run_workflow``: a JSON plan of steps compiled to a LangGraph ``StateGraph`` with ``Send`` fan-out (P4).

architecture.html, "run_workflow: параллельный план"::

    run_workflow(steps=[
      {"id": "pump",   "role": "prover", "model": "sonnet", "task": "..."},
      {"id": "ogden",  "role": "prover", "model": "sonnet", "task": "..."},
      {"id": "parikh", "role": "prover", "model": "haiku",  "task": "..."},
      {"id": "judge",  "role": "critic", "model": "opus", "depends_on": ["pump", "ogden", "parikh"],
       "task": "...", "reason": "..."}
    ], budget_usd=2.0)

Before anything runs, :func:`validate_workflow` checks the whole plan and lists
**every** problem at once (the model fixes them in one go):

* unique step ids, ``depends_on`` naming known ids, no self-dependency, and no
  cycle (the cycle is printed: ``a -> b -> a``);
* each step's model through :mod:`.model_policy` (allowed tiers, the Opus
  ``reason``); a step with a ``role`` and no ``model`` uses the role's model
  from ``settings.models.roles`` (as a ``task`` call would);
* tools from the ad-hoc whitelist (default: the role's tools); no
  ``spawn_subagent`` / ``run_workflow`` inside steps;
* the spawn depth (steps run one level below the caller) and the parallel
  width: the steps run layer by layer (a step's layer is the longest
  dependency path to it), and no layer may hold more than ``limits.max_parallel``
  steps;
* money: every step gets a branch budget (its ``budget_usd``, else an even
  share of what the explicit ones leave, capped by ``per_subagent_max_usd``);
  each must pay at least one call of its model (input estimate incl. room for
  the dependencies' answers, ``max_tokens`` at the floor) plus its reserve; the
  estimated cost (the sum of the branch budgets, an upper bound of the spend)
  must fit ``budget_usd``, and ``budget_usd`` the caller's free remainder.

Execution: one ``workflow`` allocation from the caller's scope; a ``dispatch``
node whose conditional edge returns one ``Send("step", ...)`` per ready step
(every dependency finished), the ``step`` node runs the step as an ad-hoc
agent (:func:`.spawn.run_child`) in its own ``branch`` allocation, and goes
back to ``dispatch`` until nothing is left. Steps of one layer run in
parallel (LangGraph supersteps, ``max_concurrency = limits.max_parallel``). A
step whose dependency did not finish ``ok`` is skipped unless it says
``"on_dependency_failure": "run"``. The result lists every step with status,
answer, model, cost and the evidence ids of its oracle calls.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from ..middleware import budget as budget_mod
from ..tools import base as tools_base
from . import model_policy
from .spawn import (
    ADHOC_SYSTEM_PROMPT,
    NAME_PATTERN,
    NOT_EVIDENCE,
    WORKFLOW_TOOL,
    ChildSpec,
    OrchestrationContext,
    OrchestrationError,
    _failure,
    build_tools,
    call_floor_usd,
    check_output_schema,
    estimate_first_call_tokens,
    resolve_tool_names,
    run_child,
)

_NAME_RE = re.compile(NAME_PATTERN)
MAX_STEPS = 16
MAX_STEP_TASK_CHARS = 20_000
#: characters of each dependency's answer passed to a dependent step
DEP_ANSWER_CHARS = 6_000
#: fixed roles a step may take (intake needs the /input file, coder the sandbox)
WORKFLOW_ROLES: tuple[str, ...] = ("ir_builder", "prover", "critic", "general", "lean")

STEP_FOOTER = """

## Running as a workflow step
You run as step {step_id!r} of the workflow {workflow!r}, as a separate agent. Here you have no file
tools (the /skills/ files are not readable in this step) and no subagents: take theory statements only
from get_theorem / list_theorems, if you have them. Do only your step's task and finish with your
answer as plain text (or through final_answer when an output schema is given).
"""


class StepSpec(BaseModel):
    """One step of a ``run_workflow`` plan."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="unique step id: lowercase letters, digits, '-' or '_'")
    task: str = Field(min_length=1, max_length=MAX_STEP_TASK_CHARS,
                      description="the step's full task; the answers of its dependencies are appended")
    role: str | None = Field(default=None, description="a fixed role whose prompt and tools the step uses: "
                                                       + ", ".join(WORKFLOW_ROLES))
    model: str | None = Field(default=None, description="haiku | sonnet | opus; default: the role's model")
    tools: list[str] | None = Field(default=None, description="tool names or 'group:<name>'; default: the role's")
    depends_on: list[str] = Field(default_factory=list, description="ids of steps that must finish first")
    budget_usd: float | None = Field(default=None, gt=0, description="this step's budget; default: an even share")
    reason: str | None = Field(default=None, max_length=2000,
                               description="why this model (required for opus, also as the role's default)")
    output_schema: dict[str, Any] | None = Field(default=None, description="optional JSON schema of the answer")
    on_dependency_failure: Literal["skip", "run"] = Field(
        default="skip", description="'skip' (default) or 'run' when a dependency did not finish ok")


class WorkflowArgs(BaseModel):
    """Arguments of ``run_workflow``."""

    model_config = ConfigDict(extra="forbid")

    steps: list[StepSpec] = Field(min_length=1, max_length=MAX_STEPS)
    budget_usd: float = Field(gt=0, description="money for the whole workflow, taken from your remainder")
    name: str = Field(default="workflow", description="label of the workflow (budget scope, log)")
    dry_run: bool = Field(default=False, description="only validate and estimate; run nothing")


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PlannedStep:
    spec: StepSpec
    child: ChildSpec
    role_key: str | None
    layer: int
    budget_usd: float
    floor_usd: float            # one call at the floor + the branch's reserve
    input_tokens: int

    def summary(self) -> dict[str, Any]:
        return {"id": self.spec.id, "role": self.role_key, "layer": self.layer,
                "depends_on": list(self.spec.depends_on), "model": self.child.choice.to_dict(),
                "tools": list(self.child.tool_names), "budget_usd": round(self.budget_usd, 6),
                "min_budget_usd": round(self.floor_usd, 6)}


@dataclasses.dataclass
class Plan:
    name: str
    steps: dict[str, PlannedStep]
    order: list[str]
    layers: list[list[str]]
    budget_usd: float

    @property
    def width(self) -> int:
        return max((len(layer) for layer in self.layers), default=0)

    @property
    def estimated_cost_usd(self) -> float:
        return sum(s.budget_usd for s in self.steps.values())

    @property
    def min_cost_usd(self) -> float:
        return sum(s.floor_usd for s in self.steps.values())

    def summary(self) -> dict[str, Any]:
        sinks = [i for i in self.order if not any(i in s.spec.depends_on for s in self.steps.values())]
        return {"name": self.name, "budget_usd": self.budget_usd, "order": list(self.order),
                "layers": [list(layer) for layer in self.layers], "width": self.width,
                "estimated_cost_usd": round(self.estimated_cost_usd, 6),
                "min_cost_usd": round(self.min_cost_usd, 6), "final_steps": sinks,
                "steps": [self.steps[i].summary() for i in self.order]}


def _role_key(role: str | None) -> tuple[str | None, str | None]:
    """``(role key, problem)`` for a step's ``role`` (key or subagent name)."""
    from ..roles import ROLE_BY_NAME, ROLES

    if role is None:
        return None, None
    key = role.strip()
    key = ROLE_BY_NAME.get(key, key).replace("-", "_")
    if key not in ROLES or key not in WORKFLOW_ROLES:
        return None, (f"unknown or unavailable role {role!r}; workflow steps may use: "
                      f"{', '.join(WORKFLOW_ROLES)} (intake and coder need files / the sandbox: use task)")
    return key, None


def _role_default_choice(ctx: OrchestrationContext, role_key: str, reason: str | None = None, *,
                         what: str = "workflow step") -> model_policy.ModelChoice:
    """The model a fixed role gets from settings, for a step that names a
    role and no model. Leaving the model out is still the agent's pick of a
    parallel branch: when the configured model is Opus and
    ``opus_needs_reason`` is on, the step needs a ``reason`` exactly as an
    explicit ``model: "opus"`` does (PLAN: Opus needs a logged reason), so a
    plan cannot start several Opus branches by omitting ``model``.
    ``effective`` has ``TFL_MODEL_OVERRIDE`` applied, ``picked`` is the
    configured one (the reason rule looks at the configured one)."""
    from ..agent import ModelNotAllowedError, resolve_role_model
    from ..roles import role_model_request

    roles_cfg = (ctx.settings.get("models") or {}).get("roles") or {}
    try:
        effective = resolve_role_model(ctx.settings, role_key)
    except (ModelNotAllowedError, config.UnknownModelError) as exc:
        raise model_policy.ModelPolicyError(f"role {role_key!r}: {exc}") from None
    picked = effective
    if config.model_override():
        configured = role_model_request(roles_cfg, role_key)
        if configured:
            picked = config.normalize_model(configured)
    overridden = config.pricing_key(picked) != config.pricing_key(effective)
    tier = model_policy.tier_of(picked)
    why = reason.strip() if isinstance(reason, str) else ""
    if (tier == "opus" and model_policy.opus_needs_reason(ctx.settings)
            and len(why) < model_policy.MIN_REASON_CHARS):
        cheaper = ", ".join(t for t in model_policy.allowed_tiers(ctx.settings) if t != "opus") or "none"
        raise model_policy.ModelPolicyError(
            f"{what}: role {role_key!r} defaults to opus (settings.models.roles.{role_key}), and opus needs "
            f"a `reason` (settings.models.opus_needs_reason is on; at least {model_policy.MIN_REASON_CHARS} "
            f"characters saying why haiku / sonnet would not do). Give a reason or set `model` to a cheaper "
            f"tier: {cheaper}.")
    default_note = f"role default: settings.models.roles.{role_key}"
    return model_policy.ModelChoice(
        requested=f"role default ({role_key})", tier=tier, model=picked,
        effective_model=effective, overridden=overridden,
        reason=f"{why} ({default_note})" if why else default_note)


def _find_cycle(nodes: Sequence[str], edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """One dependency cycle among ``nodes`` (``edges[x]`` = what x depends on), as a closed path."""
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = 1
        stack.append(n)
        for d in edges.get(n, ()):
            if color.get(d) == 1:
                return [*stack[stack.index(d):], d]
            if color.get(d) is None:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        color[n] = 2
        return None

    for n in nodes:
        if color.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


def _layers(ids: Sequence[str], deps: Mapping[str, Sequence[str]]) -> tuple[list[list[str]], list[str]]:
    """Longest-path layers (Kahn) and the ids left over (on or behind a cycle)."""
    layer_of: dict[str, int] = {}
    remaining = list(ids)
    progress = True
    while remaining and progress:
        progress = False
        for i in list(remaining):
            if all(d in layer_of for d in deps.get(i, ())):
                layer_of[i] = 1 + max((layer_of[d] for d in deps.get(i, ())), default=-1)
                remaining.remove(i)
                progress = True
    layers: list[list[str]] = []
    for i in ids:
        if i in layer_of:
            while len(layers) <= layer_of[i]:
                layers.append([])
            layers[layer_of[i]].append(i)
    return layers, remaining


def _need_usd(floor: float, ctx: OrchestrationContext) -> float:
    """Smallest branch budget that pays ``floor`` above its own reserve
    (``reserve = min(root reserve, budget / 4)``)."""
    root_reserve = ctx.budget.ledger.root.reserve if ctx.budget is not None else 0.0
    return min(floor + root_reserve, floor * 4 / 3) if floor > 0 else 0.0


def validate_workflow(ctx: OrchestrationContext, args: WorkflowArgs) -> Plan:
    """Check a plan (see the module docstring) and return it with the
    branch budgets; raises :class:`OrchestrationError` listing every problem."""
    problems: list[str] = []
    data: dict[str, Any] = {}
    if not _NAME_RE.match(args.name):
        problems.append(f"workflow name {args.name!r} must match {NAME_PATTERN}")
    if not ctx.can_spawn():
        problems.append(
            f"spawn depth exceeded: this agent runs at depth {ctx.depth}, the steps would run at depth "
            f"{ctx.depth + 1}, and limits.max_spawn_depth = {ctx.max_spawn_depth}")
    scope = ctx.scope()
    if scope is not None and scope.in_wrap_up():
        problems.append(f"the budget scope {scope.path} is in wrap-up: no new workflows; finish with what is "
                        "established")

    # -- ids and dependencies ---------------------------------------------------
    ids = [s.id for s in args.steps]
    seen: set[str] = set()
    dups = sorted({i for i in ids if i in seen or seen.add(i)})  # type: ignore[func-returns-value]
    if dups:
        problems.append(f"duplicate step id(s) {dups}: every step needs its own id")
    for i in ids:
        if not _NAME_RE.match(i):
            problems.append(f"step id {i!r} must match {NAME_PATTERN}")
    known = set(ids)
    deps: dict[str, list[str]] = {}
    for s in args.steps:
        unknown = [d for d in s.depends_on if d not in known]
        if unknown:
            problems.append(f"step {s.id!r} depends on unknown step(s) {unknown}; known ids: {sorted(known)}")
        if s.id in s.depends_on:
            problems.append(f"step {s.id!r} depends on itself")
        deps.setdefault(s.id, [])
        deps[s.id] = sorted({*deps[s.id], *(d for d in s.depends_on if d in known and d != s.id)})
    unique_ids = list(dict.fromkeys(ids))
    layers, cyclic = _layers(unique_ids, deps)
    if cyclic:
        cycle = _find_cycle(cyclic, deps)
        if cycle:
            problems.append("the plan has a dependency cycle: " + " -> ".join(cycle)
                            + " (a step cannot wait for itself; make it a DAG)")
        else:  # pragma: no cover - leftovers always contain a cycle
            problems.append(f"steps {sorted(cyclic)} can never start (cyclic dependencies)")
    width = max((len(layer) for layer in layers), default=0)
    if width > ctx.max_parallel:
        wide = next(layer for layer in layers if len(layer) == width)
        problems.append(
            f"{width} steps would run in parallel ({', '.join(wide)}), but limits.max_parallel = "
            f"{ctx.max_parallel}; chain some of them with depends_on or split the plan")

    # -- per step: role, model, tools, schema ---------------------------------
    from ..roles import ROLES, role_tools

    planned_parts: dict[str, tuple[StepSpec, ChildSpec, str | None, list[Any]]] = {}
    for s in args.steps:
        what = f"step {s.id!r}"
        role_key, role_problem = _role_key(s.role)
        if role_problem:
            problems.append(f"{what}: {role_problem}")
        choice = None
        try:
            if s.model is not None:
                choice = model_policy.resolve_model_choice(ctx.settings, s.model, s.reason, what=what)
            elif role_key is not None:
                choice = _role_default_choice(ctx, role_key, s.reason, what=what)
            elif not role_problem:
                problems.append(f"{what}: give a model ({', '.join(model_policy.allowed_tiers(ctx.settings))}) "
                                "or a role")
        except model_policy.ModelPolicyError as exc:
            problems.append(str(exc))
        if s.tools is not None:
            names, tool_problems = resolve_tool_names(s.tools, ctx=ctx, what=what, allow_spawning=False)
            problems += tool_problems
        elif role_key is not None:
            names = [t.name for t in role_tools(ROLES[role_key])]
        else:
            names = []
        problems += check_output_schema(s.output_schema, what=what)
        if choice is None or role_problem:
            continue
        prompt = (ROLES[role_key].system_prompt() if role_key else ADHOC_SYSTEM_PROMPT) \
            + STEP_FOOTER.format(step_id=s.id, workflow=args.name)
        child = ChildSpec(label=s.id, role_key=f"workflow:{s.id}", system_prompt=prompt, task=s.task,
                          choice=choice, tool_names=names,
                          output_schema=dict(s.output_schema) if s.output_schema is not None else None,
                          kind="branch")
        planned_parts[s.id] = (s, child, role_key, names)

    # -- money ---------------------------------------------------------------------
    budget = float(args.budget_usd)
    cap = ctx.per_subagent_max_usd
    explicit = {s.id: float(s.budget_usd) for s in args.steps if s.budget_usd is not None}
    for sid, b in explicit.items():
        if cap is not None and b > cap + budget_mod.EPS:
            problems.append(f"step {sid!r}: budget_usd ${b:.4f} exceeds budget.per_subagent_max_usd ${cap:.2f}")
    explicit_sum = sum(explicit.values())
    n_unassigned = len(args.steps) - len(explicit)
    share = (budget - explicit_sum) / n_unassigned if n_unassigned else 0.0
    if cap is not None:
        share = min(share, cap)
    if explicit_sum > budget + budget_mod.EPS:
        problems.append(f"the step budgets add up to ${explicit_sum:.4f}, more than budget_usd ${budget:.4f}")
    layer_of = {i: n for n, layer in enumerate(layers) for i in layer}
    steps: dict[str, PlannedStep] = {}
    floors: dict[str, float] = {}
    child_ctx = ctx.child()
    for sid, (s, child, role_key, names) in planned_parts.items():
        tokens = estimate_first_call_tokens(child, build_tools(names, child_ctx),
                                            extra_chars=(DEP_ANSWER_CHARS + 400) * len(s.depends_on))
        floor = _need_usd(call_floor_usd(child.choice.effective_model, tokens,
                                         min_output_tokens=ctx.min_output_tokens), ctx) \
            if ctx.budget is not None else 0.0
        floors[sid] = floor
        b = explicit.get(sid, share)
        steps[sid] = PlannedStep(spec=s, child=child, role_key=role_key, layer=layer_of.get(sid, -1),
                                 budget_usd=b, floor_usd=floor, input_tokens=tokens)
    if ctx.budget is not None:
        min_total = sum(floors.values())
        for sid in ids:
            st = steps.get(sid)
            if st is None:
                continue
            if st.budget_usd + budget_mod.EPS < st.floor_usd:
                tier = st.child.choice.effective_tier or st.child.choice.effective_model
                problems.append(
                    f"step {sid!r}: its budget ${max(st.budget_usd, 0):.4f} cannot pay one {tier} call "
                    f"(~{st.input_tokens} input tokens + {ctx.min_output_tokens} output tokens + its reserve: "
                    f"at least ${st.floor_usd:.4f})")
        if len(steps) == len(args.steps) and min_total > budget + budget_mod.EPS:
            problems.append(f"the plan needs at least ${min_total:.4f} (one call per step at the floor), more "
                            f"than budget_usd ${budget:.4f}; use cheaper models or fewer steps, or raise "
                            "budget_usd")
        free = ctx.free_usd(scope) or 0.0
        data.update(free_usd=round(free, 6), requested_usd=budget, min_cost_usd=round(min_total, 6),
                    estimated_cost_usd=round(sum(s.budget_usd for s in steps.values()), 6))
        if budget > free + budget_mod.EPS:
            problems.append(f"budget_usd ${budget:.4f} does not fit: {scope.path} has ${free:.4f} free above its "
                            f"reserve ${scope.reserve:.2f}")
    if problems:
        raise OrchestrationError(WORKFLOW_TOOL, problems, data=data, hint=_hint(ctx, scope))
    order = [i for layer in layers for i in layer]
    return Plan(name=args.name, steps=steps, order=order, layers=layers, budget_usd=budget)


def _hint(ctx: OrchestrationContext, scope: Any) -> str:
    parts = [f"allowed tiers: {', '.join(model_policy.allowed_tiers(ctx.settings)) or 'none'}",
             f"max_parallel {ctx.max_parallel}", f"depth {ctx.depth} of max {ctx.max_spawn_depth}"]
    if scope is not None:
        parts.insert(0, f"${ctx.free_usd(scope) or 0.0:.4f} free now")
    return ("Fix the plan and call again (" + "; ".join(parts) + "); dry_run: true checks a plan without "
            "running it.")


# ---------------------------------------------------------------------------
# Compile and run
# ---------------------------------------------------------------------------

def _merge(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class WorkflowState(TypedDict):
    results: Annotated[dict[str, Any], _merge]


def _dep_block(step: PlannedStep, results: Mapping[str, Any]) -> str:
    if not step.spec.depends_on:
        return ""
    parts = ["\n\n## Results of the steps this one depends on",
             "(their answers are not evidence by themselves; the evidence ids are the oracle calls they made)"]
    for d in step.spec.depends_on:
        r = results.get(d) or {}
        answer = str(r.get("answer") or "")
        if len(answer) > DEP_ANSWER_CHARS:
            answer = answer[:DEP_ANSWER_CHARS - 40] + "\n[... cut]"
        ev = ", ".join(f"{e['id']} ({e.get('tool')}, {e.get('trust')})" for e in r.get("evidence") or []) or "none"
        parts.append(f"\n### step {d}: status {r.get('status', '?')}, model "
                     f"{(r.get('model') or {}).get('effective_tier') or '?'}")
        if r.get("error"):
            parts.append(f"error: {r['error']}")
        if r.get("structured") is not None:
            import json

            parts.append("structured answer: " + json.dumps(r["structured"], ensure_ascii=False)[:DEP_ANSWER_CHARS])
        parts.append(answer or "(no answer)")
        parts.append(f"evidence: {ev}")
    return "\n".join(parts)


def compile_workflow(ctx: OrchestrationContext, plan: Plan, wf_node: Any) -> Any:
    """The LangGraph ``StateGraph`` of a validated plan (compiled, not run)."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    from ..middleware import evidence as evidence_mod

    # the evidence parent call of the caller (run_workflow's tool-call id): set again in
    # each step, so the steps' records point at it even if a runner drops context variables
    parent_call = evidence_mod.current_parent_call()

    def dispatch(state: WorkflowState) -> dict[str, Any]:
        return {}

    def route(state: WorkflowState) -> Any:
        done = state.get("results") or {}
        ready = [i for i in plan.order
                 if i not in done and all(d in done for d in plan.steps[i].spec.depends_on)]
        if not ready:
            return END
        return [Send("step", {"step_id": i, "results": dict(done)}) for i in ready[:ctx.max_parallel]]

    def step(payload: dict[str, Any]) -> dict[str, Any]:
        sid = payload["step_id"]
        token = evidence_mod._PARENT_CALL.set(parent_call) if parent_call else None
        try:
            return {"results": {sid: _run_step(ctx, plan, sid, payload["results"], wf_node)}}
        finally:
            if token is not None:
                evidence_mod._PARENT_CALL.reset(token)

    g = StateGraph(WorkflowState)
    g.add_node("dispatch", dispatch)
    g.add_node("step", step)
    g.add_edge(START, "dispatch")
    g.add_conditional_edges("dispatch", route, ["step", END])
    g.add_edge("step", "dispatch")
    return g.compile(name=f"workflow:{plan.name}")


def _run_step(ctx: OrchestrationContext, plan: Plan, sid: str, results: Mapping[str, Any],
              wf_node: Any) -> dict[str, Any]:
    st = plan.steps[sid]
    base = {"id": sid, "role": st.role_key, "layer": st.layer, "model": st.child.choice.to_dict(),
            "tools": list(st.child.tool_names), "budget_usd": round(st.budget_usd, 6)}
    failed = [d for d in st.spec.depends_on if (results.get(d) or {}).get("status") != "ok"]
    if failed and st.spec.on_dependency_failure == "skip":
        ctx.emit({"event": "workflow_step_skipped", "workflow": plan.name, "step": sid, "failed_deps": failed})
        return {**base, "status": "skipped", "answer": "", "evidence": [], "budget": None,
                "error": f"skipped: dependency step(s) {failed} did not finish ok"}
    child = dataclasses.replace(st.child, task=st.child.task + _dep_block(st, results))
    node = None
    if wf_node is not None:
        try:
            node = wf_node.allocate(sid, st.budget_usd, kind="branch", reserve_usd=ctx.child_reserve(st.budget_usd))
        except budget_mod.BudgetExceeded as exc:
            ctx.emit({"event": "workflow_step_refused", "workflow": plan.name, "step": sid, "reason": str(exc)})
            return {**base, "status": "refused_budget", "answer": "", "evidence": [], "budget": None,
                    "error": str(exc)}
    ctx.emit({"event": "workflow_step_start", "workflow": plan.name, "step": sid,
              "model": st.child.choice.effective_model, "budget_usd": st.budget_usd})
    res = run_child(ctx, child, node)
    ctx.emit({"event": "workflow_step_end", "workflow": plan.name, "step": sid, "status": res.get("status"),
              "model": st.child.choice.effective_model, "spent_usd": (res.get("budget") or {}).get("spent_usd")})
    return {**base, **res}


def execute_plan(ctx: OrchestrationContext, plan: Plan) -> dict[str, Any]:
    """Allocate the workflow budget, run the compiled graph, return the results."""
    scope = ctx.scope()
    wf_node = None
    if scope is not None:
        try:
            wf_node = scope.allocate(f"workflow:{plan.name}", plan.budget_usd, kind="workflow")
        except budget_mod.BudgetExceeded as exc:
            raise OrchestrationError(WORKFLOW_TOOL, [str(exc)], hint=_hint(ctx, scope)) from None
    t0 = time.monotonic()
    ctx.emit({"event": "workflow_start", "workflow": plan.name, "budget_usd": plan.budget_usd,
              "layers": plan.layers,
              "steps": [{"id": i, "model": plan.steps[i].child.choice.to_dict(),
                         "budget_usd": plan.steps[i].budget_usd} for i in plan.order]})
    try:
        graph = compile_workflow(ctx, plan, wf_node)
        state = graph.invoke({"results": {}}, {"recursion_limit": 4 * len(plan.steps) + 2 * len(plan.layers) + 10,
                                               "max_concurrency": ctx.max_parallel})
        results = state.get("results") or {}
    finally:
        budget = None
        if wf_node is not None:
            st = wf_node.status()
            returned = wf_node.close()
            budget = {"node": st["node"], "allocated_usd": round(st["limit_usd"], 6),
                      "spent_usd": round(st["spent_usd"], 6), "returned_usd": round(returned, 6)}
    steps = [results.get(i) or {"id": i, "status": "not_run"} for i in plan.order]
    ok = sum(1 for s in steps if s.get("status") == "ok")
    status = "ok" if ok == len(steps) else ("failed" if ok == 0 else "partial")
    out = {"name": plan.name, "status": status, "steps": steps, "layers": plan.layers,
           "final_steps": plan.summary()["final_steps"], "budget": budget,
           "estimate": {"estimated_cost_usd": round(plan.estimated_cost_usd, 6),
                        "min_cost_usd": round(plan.min_cost_usd, 6), "width": plan.width},
           "elapsed_s": round(time.monotonic() - t0, 3), "note": NOT_EVIDENCE}
    ctx.emit({"event": "workflow_end", "workflow": plan.name, "status": status,
              "spent_usd": (budget or {}).get("spent_usd")})
    return out


def run_workflow(ctx: OrchestrationContext, args: WorkflowArgs | Mapping[str, Any]) -> tools_base.ToolResult:
    """Validate, compile and run a plan; a ``ToolResult`` (never raises for a refusal)."""
    t0 = time.monotonic()
    if not isinstance(args, WorkflowArgs):
        try:
            args = WorkflowArgs.model_validate(dict(args))
        except ValidationError as exc:
            return _failure(WORKFLOW_TOOL, f"{WORKFLOW_TOOL} refused: invalid arguments: "
                                           f"{tools_base._validation_message(exc)}")
    try:
        plan = validate_workflow(ctx, args)
        if args.dry_run:
            return tools_base.ToolResult.success(
                {"dry_run": True, "valid": True, "plan": plan.summary()}, trust="not_verified",
                reason="a plan check; nothing was run", tool=WORKFLOW_TOOL)
        data = execute_plan(ctx, plan)
    except OrchestrationError as exc:
        ctx.emit({"event": "workflow_refused", "workflow": args.name, "problems": exc.problems})
        return _failure(WORKFLOW_TOOL, str(exc), data={"refused": True, "problems": exc.problems, **exc.data})
    res = tools_base.ToolResult.success(data, trust="not_verified", reason=NOT_EVIDENCE, tool=WORKFLOW_TOOL)
    return res.model_copy(update={"elapsed_s": round(time.monotonic() - t0, 4)})


def workflow_description(ctx: OrchestrationContext) -> str:
    tiers = ", ".join(model_policy.allowed_tiers(ctx.settings))
    cap = ctx.per_subagent_max_usd
    return (
        "Run a plan of steps as parallel ad-hoc agents: steps without mutual dependencies run at the same "
        "time, a step with depends_on gets the answers of those steps appended to its task (e.g. three "
        "proof attempts and a judge). Each step: id, task, optional role "
        f"({', '.join(WORKFLOW_ROLES)}: its prompt and tools), model ({tiers}; default: the role's), tools, "
        "depends_on, budget_usd, reason (required for opus, also when opus is the role's default model), "
        "output_schema. Checked before anything runs: unique ids, known dependencies, no cycle, allowed "
        "models, at most "
        f"{ctx.max_parallel} steps in parallel (limits.max_parallel), spawn depth "
        f"(max {ctx.max_spawn_depth}), and that the cost estimate (sum of the step budgets"
        + (f", each at most ${cap:.2f}" if cap is not None else "")
        + ") fits budget_usd and your free remainder. A refusal lists every problem; dry_run: true only "
          "checks. Answers are NOT evidence: cite the evidence ids each step returns. An IR built by a "
          "step is not reviewed at HITL 1: call validate_ir on it yourself before you rely on it.")


__all__ = [
    "DEP_ANSWER_CHARS",
    "MAX_STEPS",
    "WORKFLOW_ROLES",
    "Plan",
    "PlannedStep",
    "StepSpec",
    "WorkflowArgs",
    "WorkflowState",
    "compile_workflow",
    "execute_plan",
    "run_workflow",
    "validate_workflow",
    "workflow_description",
]
