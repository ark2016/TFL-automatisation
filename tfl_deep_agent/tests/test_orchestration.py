"""P4 orchestration: model_policy, spawn_subagent, run_workflow, budget_status.

Scripted chat models only (``agent.scripted_model_class``); nothing reaches the API.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("langchain.agents")
pytest.importorskip("langgraph")

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, ToolMessage

from tfl_deep_agent import config
from tfl_deep_agent import settings as ST
from tfl_deep_agent.agent import RunLimits, scripted_model_class
from tfl_deep_agent.evidence.ledger import EvidenceLedger
from tfl_deep_agent.middleware import budget as B
from tfl_deep_agent.middleware.evidence import EvidenceMiddleware
from tfl_deep_agent.orchestration import model_policy as MP
from tfl_deep_agent.orchestration import spawn as S
from tfl_deep_agent.orchestration import workflow as W

OPUS, SONNET, HAIKU = config.OPUS, config.SONNET, config.HAIKU
OPUS_REASON = "criticism of three proofs needs the strongest model"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: per role_key: a Barrier every first call of that key waits on (parallelism proof)
BARRIERS: dict[str, threading.Barrier] = {}


def _barrier_class():
    base = scripted_model_class()

    class BarrierModel(base):  # type: ignore[misc,valid-type]
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            b = BARRIERS.get(self.role)
            if b is not None and self.calls_made == 0:
                b.wait()
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kw)

    return BarrierModel


_BARRIER_CLS = None


class Factory:
    """``model_factory`` keyed by role_key; records what it built."""

    def __init__(self, replies: dict[str, list[dict]] | None = None, default: str = "ok") -> None:
        global _BARRIER_CLS
        if _BARRIER_CLS is None:
            _BARRIER_CLS = _barrier_class()
        self.replies = replies or {}
        self.default = default
        self.built: list[tuple[str, str]] = []
        self.models: dict[str, object] = {}
        self.lock = threading.Lock()

    def __call__(self, role_key: str, model_id: str):
        with self.lock:
            self.built.append((role_key, model_id))
        replies = self.replies.get(role_key) or [{"content": f"{self.default} from {role_key}"}]
        m = _BARRIER_CLS(model=model_id, role=role_key, replies=replies)
        self.models[role_key] = m
        return m


def settings_with(**sections):
    cfg = json.loads(json.dumps(ST.DEFAULTS))
    for name, values in sections.items():
        cfg[name].update(values)
    return ST.validate_settings(cfg).to_dict()


NO_OPUS = settings_with(models={"allowed": [HAIKU, SONNET], "roles": dict.fromkeys(config.MODELS, SONNET)})


def ledger(per_task=5.0, reserve=0.3, cap=2.0):
    return B.BudgetLedger(per_task, reserve_for_report_usd=reserve, per_subagent_max_usd=cap)


def make_ctx(factory=None, *, cfg=None, led=None, evidence=False, depth=0, limits=None):
    led = led if led is not None else ledger()
    return S.OrchestrationContext.create(cfg or ST.DEFAULTS, model_factory=factory or Factory(),
                                         budget=led, evidence=evidence, depth=depth, limits=limits)


def three_plus_judge(judge_model="opus", reason=OPUS_REASON, **extra):
    judge = {"id": "judge", "role": "critic", "model": judge_model, "task": "pick the correct proof",
             "depends_on": ["pump", "ogden", "parikh"], **extra}
    if reason is not None:
        judge["reason"] = reason
    return [
        {"id": "pump", "role": "prover", "model": "sonnet", "task": "proof via the pumping lemma"},
        {"id": "ogden", "role": "prover", "model": "sonnet", "task": "proof via Ogden's lemma"},
        {"id": "parikh", "role": "prover", "model": "haiku", "task": "does Parikh decide it?"},
        judge,
    ]


def error_of(res) -> str:
    assert not res.ok, res
    return res.error


# ---------------------------------------------------------------------------
# model_policy
# ---------------------------------------------------------------------------

def test_tiers_map_to_config_ids():
    assert MP.TIERS == {"haiku": HAIKU, "sonnet": SONNET, "opus": OPUS}
    for tier, mid in MP.TIERS.items():
        assert MP.tier_of(tier) == tier and MP.tier_of(mid) == tier
    assert MP.tier_of("gpt-4") is None


@pytest.mark.parametrize("pick", ["sonnet", "SONNET", SONNET, " sonnet "])
def test_allowed_pick_resolves(pick):
    c = MP.resolve_model_choice(ST.DEFAULTS, pick)
    assert (c.tier, c.model, c.effective_model, c.overridden) == ("sonnet", SONNET, SONNET, False)


def test_disallowed_model_is_refused_with_the_allowed_tiers():
    with pytest.raises(MP.ModelPolicyError) as ei:
        MP.resolve_model_choice(NO_OPUS, "opus", OPUS_REASON, what="subagent 'x'")
    msg = str(ei.value)
    assert "subagent 'x'" in msg and "'opus'" in msg and "not allowed by settings.models.allowed" in msg
    assert "haiku" in msg and "sonnet" in msg
    assert MP.allowed_tiers(NO_OPUS) == ["haiku", "sonnet"]


def test_unknown_model_is_refused():
    with pytest.raises(MP.ModelPolicyError, match="unknown model 'gpt-5'"):
        MP.resolve_model_choice(ST.DEFAULTS, "gpt-5")
    with pytest.raises(MP.ModelPolicyError, match="no model given"):
        MP.resolve_model_choice(ST.DEFAULTS, "")


@pytest.mark.parametrize("reason", [None, "", "   ", "because"])
def test_opus_needs_a_reason(reason):
    with pytest.raises(MP.ModelPolicyError, match="opus needs a `reason`"):
        MP.resolve_model_choice(ST.DEFAULTS, "opus", reason)


def test_opus_with_reason_and_reason_switch():
    c = MP.resolve_model_choice(ST.DEFAULTS, "opus", "  " + OPUS_REASON + " ")
    assert c.model == OPUS and c.reason == OPUS_REASON
    relaxed = settings_with(models={"opus_needs_reason": False})
    assert MP.resolve_model_choice(relaxed, "opus").model == OPUS


def test_model_override_forces_the_built_model(monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", HAIKU)
    c = MP.resolve_model_choice(ST.DEFAULTS, "opus", OPUS_REASON)
    assert (c.model, c.effective_model, c.overridden, c.effective_tier) == (OPUS, HAIKU, True, "haiku")
    # the pick is still checked against the policy first
    with pytest.raises(MP.ModelPolicyError):
        MP.resolve_model_choice(NO_OPUS, "opus", OPUS_REASON)
    with pytest.raises(MP.ModelPolicyError, match="reason"):
        MP.resolve_model_choice(ST.DEFAULTS, "opus")


def test_cost_table_lists_allowed_tiers():
    rows = MP.model_cost_table(NO_OPUS)
    assert [r["tier"] for r in rows] == ["haiku", "sonnet"]
    for r in rows:
        assert 0 < r["call_floor_usd"] < r["call_worst_case_usd"]


# ---------------------------------------------------------------------------
# spawn_subagent
# ---------------------------------------------------------------------------

def test_spawn_runs_in_its_own_allocation_and_returns_the_rest():
    f = Factory({"spawn:helper": [{"tool_calls": [{"name": "list_theorems", "args": {}}]},
                                  {"content": "two theorems apply"}]})
    led = ledger()
    ev = EvidenceLedger()
    ctx = make_ctx(f, led=led, evidence=ev)
    res = S.spawn_subagent(ctx, {"name": "helper", "instructions": "list the theorems", "model": "sonnet",
                                 "tools": ["list_theorems"], "budget_usd": 0.5, "reason": "cheap listing"})
    assert res.ok and res.trust == "not_verified"
    d = res.data
    assert d["status"] == "ok" and d["answer"] == "two theorems apply" and d["depth"] == 1
    assert d["model"]["effective_model"] == SONNET and d["model"]["reason"] == "cheap listing"
    assert f.built == [("spawn:helper", SONNET)]
    assert f.models["spawn:helper"].bound_tools == ["list_theorems"]
    b = d["budget"]
    assert b["node"] == "task/spawn:helper" and b["allocated_usd"] == 0.5 and b["calls"] == 2
    assert b["spent_usd"] > 0 and abs(b["spent_usd"] + b["returned_usd"] - 0.5) < 1e-6
    child = led.root.children[0]
    assert child.closed and child.kind == "subagent"
    assert abs(led.spent_usd - b["spent_usd"]) < 1e-9
    assert all(r.node == "task/spawn:helper" and r.role == "spawn:helper" for r in led.records)
    # the child's oracle call is in the run's evidence ledger, under the child's scope
    [rec] = ev.records()
    assert (rec.tool, rec.role, rec.scope) == ("list_theorems", "spawn:helper", "task/spawn:helper")
    assert d["evidence"] == [{"id": rec.id, "tool": "list_theorems", "ok": True, "trust": rec.trust}]
    assert ctx.history[0]["event"] == "spawn_start" and ctx.history[-1]["event"] == "spawn_end"


def test_spawn_refuses_a_disallowed_model_and_runs_nothing():
    f = Factory()
    led = ledger()
    ctx = make_ctx(f, cfg=NO_OPUS, led=led)
    res = S.spawn_subagent(ctx, {"name": "x", "instructions": "do it", "model": "opus", "reason": OPUS_REASON,
                                 "budget_usd": 0.5})
    msg = error_of(res)
    assert msg.startswith("spawn_subagent refused") and "not allowed by settings.models.allowed" in msg
    assert "haiku" in msg and res.data["refused"] is True
    assert f.built == [] and led.root.children == [] and led.spent_usd == 0


def test_spawn_lists_every_problem_at_once():
    res = S.spawn_subagent(make_ctx(), {"name": "Bad Name", "instructions": "x", "model": "opus",
                                        "tools": ["nope", "submit_report", "execute"], "budget_usd": 3.0})
    problems = res.data["problems"]
    assert len(problems) == 4
    text = error_of(res)
    assert "name 'Bad Name'" in text and "opus needs a `reason`" in text
    assert "['execute', 'nope', 'submit_report']" in text and "never given to ad-hoc agents" in text
    assert "exceeds budget.per_subagent_max_usd $2.00" in text
    assert "Fix these and call again" in text


def test_spawn_refuses_excessive_depth():
    cfg = settings_with(limits={"max_spawn_depth": 2})
    f = Factory()
    ctx = make_ctx(f, cfg=cfg, depth=2)
    res = S.spawn_subagent(ctx, {"name": "deep", "instructions": "x", "model": "haiku", "budget_usd": 0.1})
    msg = error_of(res)
    assert "spawn depth exceeded" in msg and "depth 2" in msg and "max_spawn_depth = 2" in msg
    assert f.built == []


def test_orchestration_tools_are_not_given_by_default_and_depth_limits_the_grant():
    cfg = settings_with(limits={"max_spawn_depth": 1})
    # a child at depth 1 could not spawn: granting spawn_subagent is refused
    res = S.spawn_subagent(make_ctx(cfg=cfg), {"name": "x", "instructions": "x", "model": "haiku",
                                                "tools": ["spawn_subagent"], "budget_usd": 0.1})
    assert "spawn_subagent cannot be given" in error_of(res)
    # by default the child has no orchestration tools
    f = Factory()
    res = S.spawn_subagent(make_ctx(f), {"name": "plain", "instructions": "x", "model": "haiku",
                                         "tools": ["group:theory"], "budget_usd": 0.1})
    assert res.ok
    assert f.models["spawn:plain"].bound_tools == ["list_theorems", "get_theorem"]


def test_nested_spawn_inside_the_depth_limit_and_refused_past_it():
    inner_call = {"name": "spawn_subagent", "args": {"name": "inner", "instructions": "go deeper",
                                                     "model": "haiku", "tools": ["budget_status"],
                                                     "budget_usd": 0.2}}
    f = Factory({
        "spawn:outer": [{"tool_calls": [inner_call]}, {"content": "outer done"}],
        "spawn:inner": [{"tool_calls": [{"name": "budget_status", "args": {}}]}, {"content": "inner done"}],
    })
    led = ledger()
    ctx = make_ctx(f, led=led)       # max_spawn_depth 2 (defaults)
    res = S.spawn_subagent(ctx, {"name": "outer", "instructions": "delegate", "model": "haiku",
                                 "tools": ["spawn_subagent"], "budget_usd": 0.5})
    assert res.ok and res.data["answer"] == "outer done"
    outer = led.root.children[0]
    assert outer.name == "spawn:outer" and outer.children[0].name == "spawn:inner"
    assert outer.children[0].path == "task/spawn:outer/spawn:inner"
    # the inner child (depth 2) saw itself at depth 2 with no further spawning
    tool_msgs = [m for m in f.models["spawn:inner"].received[-1] if isinstance(m, ToolMessage)]
    status = json.loads(tool_msgs[-1].content)["data"]
    assert status["depth"] == 2 and status["can_spawn"] is False
    assert status["budget"]["node"] == "task/spawn:outer/spawn:inner"
    # a depth-2 agent asking for a child is refused with the depth message
    ctx2 = make_ctx(depth=2)
    assert "spawn depth exceeded" in error_of(S.spawn_subagent(
        ctx2, {"name": "z", "instructions": "x", "model": "haiku", "budget_usd": 0.1}))


def test_spawn_budget_must_fit_the_free_remainder():
    led = ledger(per_task=1.0, reserve=0.3, cap=2.0)
    res = S.spawn_subagent(make_ctx(led=led), {"name": "x", "instructions": "x", "model": "haiku",
                                               "budget_usd": 0.8})
    msg = error_of(res)
    assert "does not fit" in msg and "$0.7000 free" in msg and "reserve $0.30" in msg
    assert res.data["free_usd"] == pytest.approx(0.7)


def test_spawn_budget_below_one_call_is_refused():
    res = S.spawn_subagent(make_ctx(), {"name": "x", "instructions": "x" * 3000, "model": "opus",
                                        "reason": OPUS_REASON, "budget_usd": 0.02})
    msg = error_of(res)
    assert "cannot pay even one opus call" in msg and "4096 output tokens" in msg
    assert res.data["floor_usd"] > 0.08


def test_spawn_is_refused_in_wrap_up():
    led = ledger()
    led.root.wrap_up = True
    res = S.spawn_subagent(make_ctx(led=led), {"name": "x", "instructions": "x", "model": "haiku",
                                               "budget_usd": 0.1})
    assert "wrap-up" in error_of(res)


def test_child_that_runs_out_of_budget_is_reported_and_closed():
    loop = [{"tool_calls": [{"name": "list_theorems", "args": {}}],
             "usage": {"input_tokens": 1000, "output_tokens": 4000}}] * 40
    f = Factory({"spawn:spender": loop})
    led = ledger()
    res = S.spawn_subagent(make_ctx(f, led=led), {"name": "spender", "instructions": "x", "model": "sonnet",
                                                  "tools": ["list_theorems"], "budget_usd": 0.5})
    assert res.ok
    d = res.data
    assert d["status"] == "budget_exhausted" and "BUDGET" in d["answer"]
    assert d["budget"]["spent_usd"] <= 0.5 + 1e-9
    assert led.root.children[0].closed
    assert led.spent_usd <= 0.5 + 1e-9


def test_failed_child_is_a_result_and_its_budget_is_closed():
    # calls a tool it was not given: the scripted model raises ScriptError inside the child
    f = Factory({"spawn:broken": [{"tool_calls": [{"name": "not_bound_tool", "args": {}}]}]})
    led = ledger()
    res = S.spawn_subagent(make_ctx(f, led=led), {"name": "broken", "instructions": "x", "model": "haiku",
                                                  "budget_usd": 0.1})
    assert res.ok and res.data["status"] == "error" and "ScriptError" in res.data["error"]
    assert led.root.children[0].closed


SCHEMA = {"type": "object", "properties": {"accepted": {"type": "array", "items": {"type": "string"}},
                                           "count": {"type": "integer"}},
          "required": ["accepted", "count"]}


def test_structured_output_through_final_answer():
    f = Factory({"spawn:words": [{"tool_calls": [{"name": "final_answer",
                                                  "args": {"accepted": ["ab", "aabb"], "count": 2}}]}]})
    res = S.spawn_subagent(make_ctx(f), {"name": "words", "instructions": "x", "model": "haiku",
                                         "budget_usd": 0.1, "output_schema": SCHEMA})
    assert res.ok, res.error
    assert res.data["structured"] == {"accepted": ["ab", "aabb"], "count": 2}
    assert "structured_errors" not in res.data
    assert "final_answer" in f.models["spawn:words"].bound_tools


def test_structured_output_is_validated():
    f = Factory({"spawn:words": [{"tool_calls": [{"name": "final_answer", "args": {"accepted": "ab"}}]}]})
    res = S.spawn_subagent(make_ctx(f), {"name": "words", "instructions": "x", "model": "haiku",
                                         "budget_usd": 0.1, "output_schema": SCHEMA})
    errs = res.data["structured_errors"]
    assert any("count" in e for e in errs) and any("accepted" in e for e in errs)


def test_invalid_output_schema_is_refused():
    res = S.spawn_subagent(make_ctx(), {"name": "x", "instructions": "x", "model": "haiku", "budget_usd": 0.1,
                                        "output_schema": {"type": "array"}})
    assert "must describe an object" in error_of(res)
    res = S.spawn_subagent(make_ctx(), {"name": "x", "instructions": "x", "model": "haiku", "budget_usd": 0.1,
                                        "output_schema": {"type": "object", "properties": 5}})
    assert "not a valid JSON schema" in error_of(res)


def test_spawn_under_model_override_builds_the_override(monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", HAIKU)
    f = Factory()
    res = S.spawn_subagent(make_ctx(f), {"name": "x", "instructions": "x", "model": "opus",
                                         "reason": OPUS_REASON, "budget_usd": 0.2})
    assert res.ok
    assert f.built == [("spawn:x", HAIKU)] and res.data["model"]["overridden"] is True


def test_child_calls_count_against_the_run_limits():
    limits = RunLimits(max_model_calls=30, max_tool_calls=300, max_parallel=4, max_spawn_depth=2)
    f = Factory({"spawn:x": [{"tool_calls": [{"name": "list_theorems", "args": {}}]}, {"content": "done"}]})
    res = S.spawn_subagent(make_ctx(f, limits=limits), {"name": "x", "instructions": "x", "model": "haiku",
                                                        "tools": ["list_theorems"], "budget_usd": 0.1})
    assert res.ok
    st = limits.status()
    assert st["model_calls"] == 2 and st["tool_calls"] == 1


def test_live_factory_needs_a_budget():
    def live(role_key, model_id):  # pragma: no cover - never called
        raise AssertionError

    live.tfl_live = True
    with pytest.raises(ValueError, match="needs a budget"):
        S.OrchestrationContext.create(ST.DEFAULTS, model_factory=live, budget=False)


# ---------------------------------------------------------------------------
# budget_status
# ---------------------------------------------------------------------------

def test_budget_status_reports_the_scope_limits_and_models():
    led = ledger(per_task=5.0, reserve=0.3, cap=2.0)
    res = S.budget_status(make_ctx(led=led, limits=RunLimits.from_settings(ST.DEFAULTS)))
    assert res.ok and res.trust == "not_verified"
    d = res.data
    assert d["budget"]["node"] == "task" and d["budget"]["limit_usd"] == 5.0
    assert d["free_for_new_subagent_usd"] == 2.0 and d["per_subagent_max_usd"] == 2.0
    assert d["wrap_up"] is False and d["can_spawn"] is True and d["depth"] == 0
    assert d["max_spawn_depth"] == 2 and d["max_parallel"] == 4 and d["opus_needs_reason"] is True
    assert [m["tier"] for m in d["models"]] == ["haiku", "sonnet", "opus"]
    assert d["limits"]["max_model_calls"] == 80
    led.root.wrap_up = True
    assert S.budget_status(make_ctx(led=led)).data["can_spawn"] is False


def test_budget_status_without_a_ledger():
    ctx = S.OrchestrationContext.create(ST.DEFAULTS, model_factory=Factory(), budget=False, evidence=False)
    d = S.budget_status(ctx).data
    assert d["budget"] is None and "no budget ledger" in d["budget_note"]


# ---------------------------------------------------------------------------
# run_workflow: acceptance (3 parallel branches + judge within budget)
# ---------------------------------------------------------------------------

def test_three_parallel_branches_and_a_judge_stay_in_budget():
    replies = {
        "workflow:pump": [{"tool_calls": [{"name": "list_theorems", "args": {}}]},
                          {"content": "pump: proof A"}],
        "workflow:ogden": [{"content": "ogden: proof B"}],
        "workflow:parikh": [{"content": "parikh: Parikh does not decide it"}],
        "workflow:judge": [{"content": "judge: proof A is correct",
                            "expect": {"last_message_contains": "proof B"}}],
    }
    shared = threading.Barrier(3, timeout=20)  # the three first calls must be in flight at once
    for key in ("workflow:pump", "workflow:ogden", "workflow:parikh"):
        BARRIERS[key] = shared
    try:
        f = Factory(replies)
        led = ledger()
        ev = EvidenceLedger()
        ctx = make_ctx(f, led=led, evidence=ev, limits=RunLimits.from_settings(ST.DEFAULTS))
        main_model = f("main", SONNET)
        main_model.replies = [
            {"tool_calls": [{"name": "run_workflow", "id": "call_wf",
                             "args": {"name": "cfl-proofs", "steps": three_plus_judge(), "budget_usd": 2.0}}]},
            {"content": "main: done"},
        ]
        main = create_agent(main_model, S.make_orchestration_tools(ctx), middleware=[
            B.BudgetMiddleware(led.root, role="main"),
            EvidenceMiddleware(ev, role="main", budget_node=led.root)])
        state = main.invoke({"messages": [HumanMessage("solve")]})
    finally:
        for key in ("workflow:pump", "workflow:ogden", "workflow:parikh"):
            BARRIERS.pop(key, None)

    assert state["messages"][-1].content == "main: done"
    tool_msg = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    payload = json.loads(tool_msg.content)
    assert payload["ok"] is True, payload.get("error")
    d = payload["data"]
    assert d["status"] == "ok" and d["layers"] == [["pump", "ogden", "parikh"], ["judge"]]
    assert d["final_steps"] == ["judge"]
    by_id = {s["id"]: s for s in d["steps"]}
    assert [s["id"] for s in d["steps"]] == ["pump", "ogden", "parikh", "judge"]
    assert by_id["judge"]["answer"] == "judge: proof A is correct"
    assert by_id["judge"]["model"]["effective_model"] == OPUS
    assert by_id["judge"]["model"]["reason"] == OPUS_REASON
    assert by_id["parikh"]["model"]["effective_model"] == HAIKU
    # models built as planned
    assert set(f.built) >= {("workflow:pump", SONNET), ("workflow:ogden", SONNET),
                            ("workflow:parikh", HAIKU), ("workflow:judge", OPUS)}
    # the judge saw the three answers and their evidence ids
    judge_input = f.models["workflow:judge"].received[0][-1].content
    for text in ("pump: proof A", "ogden: proof B", "Parikh does not decide it", "evidence:"):
        assert text in judge_input
    # budget: inside 2.00, every branch in its own allocation under the workflow node, all closed
    wf = led.root.children[0]
    assert wf.name == "workflow:cfl-proofs" and wf.kind == "workflow" and wf.closed
    assert [c.name for c in wf.children] == ["pump", "ogden", "parikh", "judge"]
    assert all(c.kind == "branch" and c.closed for c in wf.children)
    assert d["budget"]["spent_usd"] <= 2.0 and abs(wf.spent - d["budget"]["spent_usd"]) < 1e-6
    for s in d["steps"]:
        assert s["budget"]["spent_usd"] <= s["budget"]["allocated_usd"] + 1e-9
    assert d["estimate"]["estimated_cost_usd"] <= 2.0
    assert sum(s["budget"]["spent_usd"] for s in d["steps"]) == pytest.approx(d["budget"]["spent_usd"])
    assert led.spent_usd < 5.0 and not led.exhausted
    # evidence: the step's oracle call is recorded under its branch scope, linked to run_workflow
    rec = next(r for r in ev.records() if r.tool == "list_theorems")
    assert rec.scope == "task/workflow:cfl-proofs/pump" and rec.parent_call == "call_wf"
    assert by_id["pump"]["evidence"][0]["id"] == rec.id
    wf_rec = next(r for r in ev.records() if r.tool == "run_workflow")
    assert wf_rec.trust == "not_verified" and wf_rec.role == "main"
    events = [e["event"] for e in ctx.history]
    assert events[0] == "workflow_start" and events[-1] == "workflow_end"
    assert events.count("workflow_step_end") == 4


def test_dry_run_checks_and_estimates_without_running():
    f = Factory()
    led = ledger()
    res = W.run_workflow(make_ctx(f, led=led), {"steps": three_plus_judge(), "budget_usd": 2.0,
                                                 "dry_run": True})
    assert res.ok and res.data["dry_run"] is True
    plan = res.data["plan"]
    assert plan["width"] == 3 and plan["layers"] == [["pump", "ogden", "parikh"], ["judge"]]
    assert 0 < plan["min_cost_usd"] < plan["estimated_cost_usd"] <= 2.0
    assert [s["budget_usd"] for s in plan["steps"]] == [0.5] * 4
    assert f.built == [] and led.root.children == []


def test_explicit_step_budgets_and_shares():
    steps = three_plus_judge()
    steps[3]["budget_usd"] = 1.0
    res = W.run_workflow(make_ctx(), {"steps": steps, "budget_usd": 1.9, "dry_run": True})
    budgets = {s["id"]: s["budget_usd"] for s in res.data["plan"]["steps"]}
    assert budgets == {"pump": 0.3, "ogden": 0.3, "parikh": 0.3, "judge": 1.0}


def test_failed_dependency_skips_the_dependent_unless_it_asks_to_run():
    broken = {"workflow:a": [{"tool_calls": [{"name": "not_bound", "args": {}}]}]}
    steps = [{"id": "a", "model": "haiku", "task": "x"}, {"id": "b", "model": "haiku", "task": "y"},
             {"id": "c", "model": "haiku", "task": "z", "depends_on": ["a", "b"]}]
    res = W.run_workflow(make_ctx(Factory(broken)), {"steps": steps, "budget_usd": 0.6})
    by_id = {s["id"]: s for s in res.data["steps"]}
    assert by_id["a"]["status"] == "error" and by_id["b"]["status"] == "ok"
    assert by_id["c"]["status"] == "skipped" and "['a']" in by_id["c"]["error"]
    assert res.data["status"] == "partial"
    steps[2]["on_dependency_failure"] = "run"
    f = Factory(broken)
    res = W.run_workflow(make_ctx(f), {"steps": steps, "budget_usd": 0.6})
    by_id = {s["id"]: s for s in res.data["steps"]}
    assert by_id["c"]["status"] == "ok"
    assert "status error" in f.models["workflow:c"].received[0][-1].content


def test_role_step_without_model_uses_the_role_model_and_tools():
    f = Factory()
    steps = [{"id": "build", "role": "ir-builder", "task": "make the IR"}]
    res = W.run_workflow(make_ctx(f), {"steps": steps, "budget_usd": 0.5})
    assert res.ok and res.data["steps"][0]["status"] == "ok"
    assert f.built == [("workflow:build", SONNET)]
    step = res.data["steps"][0]
    assert step["role"] == "ir_builder" and step["model"]["reason"].startswith("role default")
    assert "validate_ir" in f.models["workflow:build"].bound_tools
    assert "submit_report" not in f.models["workflow:build"].bound_tools


def test_workflow_async_tool_call():
    f = Factory()
    ctx = make_ctx(f)
    [tool] = S.make_orchestration_tools(ctx, names=["run_workflow"])
    steps = [{"id": "a", "model": "haiku", "task": "x"}, {"id": "b", "model": "haiku", "task": "y"}]
    msg = asyncio.run(tool.ainvoke({"type": "tool_call", "id": "c1", "name": "run_workflow",
                                    "args": {"steps": steps, "budget_usd": 0.4}}))
    assert json.loads(msg.content)["data"]["status"] == "ok"
    assert msg.artifact["tool"] == "run_workflow" and msg.artifact["trust"] == "not_verified"


# ---------------------------------------------------------------------------
# run_workflow: refusals
# ---------------------------------------------------------------------------

def refused(ctx, steps, budget=2.0, **kw):
    f = ctx.model_factory
    res = W.run_workflow(ctx, {"steps": steps, "budget_usd": budget, **kw})
    assert not res.ok, res.data
    assert res.error.startswith("run_workflow refused")
    assert res.data["refused"] is True
    if isinstance(f, Factory):
        assert f.built == []   # nothing ran
    if ctx.budget is not None:
        assert ctx.budget.children == [] and ctx.budget.spent == 0
    return res.error


def test_refuses_a_disallowed_model():
    msg = refused(make_ctx(cfg=NO_OPUS), three_plus_judge())
    assert "step 'judge'" in msg and "not allowed by settings.models.allowed" in msg


def test_refuses_opus_without_reason():
    msg = refused(make_ctx(), three_plus_judge(reason=None))
    assert "step 'judge': opus needs a `reason`" in msg


def test_refuses_opus_role_defaults_without_reason():
    """Leaving `model` out does not bypass opus_needs_reason: critic defaults to opus."""
    assert config.MODELS["critic"] == OPUS
    steps = [{"id": f"c{i}", "role": "critic", "task": "criticise"} for i in range(3)]
    msg = refused(make_ctx(), steps)
    for i in range(3):
        assert f"step 'c{i}': role 'critic' defaults to opus" in msg and "needs a `reason`" in msg
    short = [{**s, "reason": "why"} for s in steps]
    assert "defaults to opus" in refused(make_ctx(), short)
    # with a reason the default is accepted and the reason is logged
    ok = [{**s, "reason": OPUS_REASON} for s in steps]
    res = W.run_workflow(make_ctx(), {"steps": ok, "budget_usd": 1.0, "dry_run": True})
    assert res.ok, res.error
    plan = {s["id"]: s for s in res.data["plan"]["steps"]}
    assert all(plan[f"c{i}"]["model"]["model"] == OPUS for i in range(3))
    assert plan["c0"]["model"]["reason"].startswith(OPUS_REASON)
    assert "settings.models.roles.critic" in plan["c0"]["model"]["reason"]
    # a non-opus role default needs no reason; switching the rule off lifts it
    assert W.run_workflow(make_ctx(), {"steps": [{"id": "b", "role": "ir_builder", "task": "x"}],
                                       "budget_usd": 0.5, "dry_run": True}).ok
    relaxed = settings_with(models={"opus_needs_reason": False})
    assert W.run_workflow(make_ctx(cfg=relaxed), {"steps": steps, "budget_usd": 1.0, "dry_run": True}).ok


def test_refuses_a_cycle_and_prints_it():
    steps = [{"id": "a", "model": "haiku", "task": "x", "depends_on": ["c"]},
             {"id": "b", "model": "haiku", "task": "y", "depends_on": ["a"]},
             {"id": "c", "model": "haiku", "task": "z", "depends_on": ["b"]},
             {"id": "d", "model": "haiku", "task": "w"}]
    msg = refused(make_ctx(), steps)
    assert "dependency cycle" in msg
    assert any(c in msg for c in ("a -> c -> b -> a", "c -> b -> a -> c", "b -> a -> c -> b"))


def test_refuses_self_dependency_unknown_and_duplicate_ids():
    steps = [{"id": "a", "model": "haiku", "task": "x", "depends_on": ["a"]},
             {"id": "a", "model": "haiku", "task": "y"},
             {"id": "b", "model": "haiku", "task": "z", "depends_on": ["ghost"]}]
    msg = refused(make_ctx(), steps)
    assert "duplicate step id(s) ['a']" in msg
    assert "step 'a' depends on itself" in msg
    assert "step 'b' depends on unknown step(s) ['ghost']" in msg


def test_refuses_more_parallel_steps_than_max_parallel():
    cfg = settings_with(limits={"max_parallel": 2})
    msg = refused(make_ctx(cfg=cfg), three_plus_judge())
    assert "3 steps would run in parallel (pump, ogden, parikh)" in msg and "max_parallel = 2" in msg


def test_refuses_excessive_depth():
    msg = refused(make_ctx(depth=2), three_plus_judge())
    assert "spawn depth exceeded" in msg and "max_spawn_depth = 2" in msg
    cfg = settings_with(limits={"max_spawn_depth": 0})
    assert "spawn depth exceeded" in refused(make_ctx(cfg=cfg), three_plus_judge())


def test_refuses_an_over_budget_plan():
    # the floor of one call per step does not fit
    msg = refused(make_ctx(), three_plus_judge(), budget=0.2)
    assert "the plan needs at least $" in msg and "more than budget_usd $0.2000" in msg
    assert "step 'judge': its budget $0.0500 cannot pay one opus call" in msg
    # explicit step budgets above the plan budget, and above the per-branch cap
    steps = three_plus_judge()
    steps[0]["budget_usd"] = 1.5
    steps[1]["budget_usd"] = 2.5
    msg = refused(make_ctx(), steps, budget=3.0)
    assert "add up to $4.0000, more than budget_usd $3.0000" in msg
    assert "step 'ogden': budget_usd $2.5000 exceeds budget.per_subagent_max_usd $2.00" in msg
    # the workflow budget must fit the caller's free remainder
    msg = refused(make_ctx(led=ledger(per_task=1.0, reserve=0.3)), three_plus_judge(), budget=0.9)
    assert "budget_usd $0.9000 does not fit: task has $0.7000 free" in msg


def test_refuses_in_wrap_up():
    led = ledger()
    led.root.wrap_up = True
    assert "wrap-up" in refused(make_ctx(led=led), three_plus_judge())


def test_refuses_bad_roles_tools_and_missing_models():
    steps = [{"id": "a", "role": "coder", "task": "x"},
             {"id": "b", "task": "y"},
             {"id": "c", "model": "haiku", "task": "z", "tools": ["spawn_subagent", "submit_report"]}]
    msg = refused(make_ctx(), steps)
    assert "step 'a': unknown or unavailable role 'coder'" in msg
    assert "step 'b': give a model" in msg
    assert "step 'c': spawn_subagent is not available inside workflow steps" in msg
    assert "step 'c': unknown or not allowed tool(s) ['submit_report']" in msg


def test_all_problems_are_listed_together():
    cfg = settings_with(models={"allowed": [HAIKU, SONNET], "roles": dict.fromkeys(config.MODELS, SONNET)},
                        limits={"max_parallel": 2})
    steps = three_plus_judge()
    steps[0]["depends_on"] = ["judge"]  # judge -> pump -> judge
    res = W.run_workflow(make_ctx(cfg=cfg), {"steps": steps, "budget_usd": 2.0})
    problems = res.data["problems"]
    assert any("cycle" in p for p in problems)
    assert any("not allowed" in p for p in problems)
    assert "Fix the plan and call again" in res.error and "dry_run: true" in res.error


def test_invalid_arguments_get_a_readable_message():
    [tool] = S.make_orchestration_tools(make_ctx(), names=["run_workflow"])
    msg = tool.invoke({"type": "tool_call", "id": "c1", "name": "run_workflow",
                       "args": {"steps": [], "budget_usd": -1}})
    text = msg.content
    assert "run_workflow refused: invalid arguments" in text and "steps" in text and "budget_usd" in text
    [spawn] = S.make_orchestration_tools(make_ctx(), names=["spawn_subagent"])
    msg = spawn.invoke({"type": "tool_call", "id": "c2", "name": "spawn_subagent",
                        "args": {"name": "x", "model": "haiku", "budget_usd": 0.1, "colour": "red"}})
    assert "spawn_subagent refused: invalid arguments" in msg.content and "instructions" in msg.content


def test_tool_set_and_descriptions():
    tools = S.make_orchestration_tools(make_ctx())
    assert [t.name for t in tools] == ["spawn_subagent", "run_workflow", "budget_status"]
    spawn, wf, status = tools
    assert "NOT evidence" in spawn.description and "opus needs `reason`" in spawn.description
    assert "max_parallel" in wf.description and "dry_run" in wf.description
    assert "free" in status.description.lower()
    assert all(t.metadata["tfl_group"] == "orchestration" for t in tools)
    with pytest.raises(ValueError, match="unknown orchestration tool"):
        S.make_orchestration_tools(make_ctx(), names=["task"])


# ---------------------------------------------------------------------------
# inside agents: checkpointer, async loop, create_tfl_agent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("use_async", [False, True])
def test_spawn_and_workflow_in_one_turn_with_a_checkpointer(use_async):
    from langgraph.checkpoint.memory import InMemorySaver

    steps = [{"id": "a", "model": "haiku", "task": "x"}, {"id": "b", "model": "haiku", "task": "y"},
             {"id": "c", "model": "haiku", "task": "z", "depends_on": ["a", "b"]}]
    f = Factory({"main": [{"tool_calls": [
        {"name": "run_workflow", "id": "w1", "args": {"steps": steps, "budget_usd": 0.6}},
        {"name": "spawn_subagent", "id": "s1",
         "args": {"name": "s", "instructions": "q", "model": "haiku", "budget_usd": 0.1}}]},
        {"content": "fin"}]})
    led = ledger()
    ctx = make_ctx(f, led=led)
    main = create_agent(f("main", HAIKU), S.make_orchestration_tools(ctx),
                        middleware=[B.BudgetMiddleware(led.root, role="main")], checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}
    inp = {"messages": [HumanMessage("go")]}
    state = asyncio.run(main.ainvoke(inp, cfg)) if use_async else main.invoke(inp, cfg)
    results = {m.name: json.loads(m.content) for m in state["messages"] if isinstance(m, ToolMessage)}
    assert results["run_workflow"]["data"]["status"] == "ok"
    assert results["spawn_subagent"]["data"]["status"] == "ok"
    assert state["messages"][-1].content == "fin"
    assert sorted((c.name, c.closed) for c in led.root.children) == [("spawn:s", True),
                                                                      ("workflow:workflow", True)]


def test_tools_work_inside_create_tfl_agent_with_the_run_limits():
    from tfl_deep_agent.agent import create_tfl_agent, run_task, scripted_model_factory

    scripted = scripted_model_factory({"models": {"main": [
        {"tool_calls": [{"name": "budget_status", "args": {}}]},
        {"tool_calls": [{"name": "spawn_subagent",
                         "args": {"name": "s", "instructions": "q", "model": "haiku", "budget_usd": 0.1}}]},
        {"content": "no report"}]}})
    adhoc = Factory()

    def factory(role_key, model_id):
        return adhoc(role_key, model_id) if role_key.startswith("spawn:") else scripted(role_key, model_id)

    led = ledger()
    ctx = make_ctx(factory, led=led)
    graph = create_tfl_agent(ST.DEFAULTS, model_factory=factory, budget=led,
                             budget_options={"missing_usage": "zero"},
                             extra_tools=S.make_orchestration_tools(ctx), hitl=False)
    ctx.limits = graph.tfl_limits      # children count against the run's limits (bound after the build)
    state = run_task(graph, "task")
    results = {m.name: json.loads(m.content) for m in state["messages"] if isinstance(m, ToolMessage)}
    assert results["budget_status"]["ok"] and results["spawn_subagent"]["data"]["status"] == "ok"
    assert {"spawn_subagent", "run_workflow", "budget_status", "task"} <= set(scripted.models["main"].bound_tools)
    assert graph.tfl_limits.status()["model_calls"] == 4    # 3 main + 1 child
    assert [c.name for c in led.root.children] == ["spawn:s"]
