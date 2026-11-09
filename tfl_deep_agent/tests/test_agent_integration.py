"""P2 integration: settings + one budget ledger + concurrency + prompt caching
+ skills, wired by ``create_tfl_agent``.

Every model here is a ``ScriptedChatModel`` (``agent.scripted_model_factory``)
or, for the prompt-caching / request-shape test, a real ``ChatAnthropic``
whose HTTP goes into the ``wire`` fixture's ``httpx2.MockTransport`` (fake key
"test", unroutable base URL). Nothing reaches the network or the API.

Money numbers in the over-budget / wrap-up tests come from scripted
``usage`` and the config prices (``config.worst_case_usd``,
``budget.usage_cost_usd``): the thresholds are computed from them, not
hard-coded, so a price change does not silently turn a test into a no-op.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepagents")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from tfl_deep_agent import agent as A  # noqa: E402
from tfl_deep_agent import cli  # noqa: E402
from tfl_deep_agent import config  # noqa: E402
from tfl_deep_agent.middleware import budget as B  # noqa: E402
from tfl_deep_agent.middleware import concurrency as C  # noqa: E402

from .conftest import sse_body  # noqa: E402,F401  (documents where the SSE shape comes from)

PKG = Path(A.__file__).resolve().parent
MOCK = PKG / "examples" / "mock"
SETTINGS_EXAMPLE = PKG / "settings.example.json"

#: role key -> subagent name (the budget node of a `task` call is named after it)
SUBAGENT_NAME = {"ir_builder": "ir-builder", "prover": "prover", "critic": "critic",
                 "general": "general-purpose"}


@pytest.fixture(autouse=True)
def _no_model_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(d))
    return d


def example_settings() -> dict[str, Any]:
    return json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))


def ledger(per_task: float = 5.0, *, reserve: float = 0.3, per_subagent: float | None = 2.0) -> B.BudgetLedger:
    return B.BudgetLedger(per_task, reserve_for_report_usd=reserve, per_subagent_max_usd=per_subagent,
                          spend_log=None)


def run(script: Any, *, settings: Any = None, budget: Any = None, **kw: Any):
    factory = A.scripted_model_factory(script)
    graph = A.create_tfl_agent(settings if settings is not None else example_settings(),
                               model_factory=factory,
                               template=factory.script.get("template") or "full_proof",
                               budget=budget, **kw)
    state = A.run_task(graph, factory.script["task"])
    return graph, state, factory


def nodes(led: B.BudgetLedger) -> dict[str, B.BudgetNode]:
    out, stack = {}, [led.root]
    while stack:
        n = stack.pop()
        out[n.path] = n
        stack.extend(n.children)
    return out


def tool_messages(state: dict[str, Any]) -> dict[str, ToolMessage]:
    return {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}


# ---------------------------------------------------------------------------
# End to end on REG / CFL / LL with the default middleware and one ledger
# ---------------------------------------------------------------------------

E2E = [
    ("reg_regex_regular.json", "reg", {"ir_builder", "critic"}),
    ("cfl_anbmcm.json", "cfl", {"ir_builder", "prover"}),
    ("ll_simple_ll1.json", "ll", {"ir_builder"}),
]


@pytest.mark.parametrize("name, cls, subagent_roles", E2E, ids=[c for _, c, _ in E2E])
def test_e2e_reaches_submit_report_with_one_shared_ledger(name, cls, subagent_roles):
    led = ledger()
    graph, state, factory = run(MOCK / name, budget=led)
    result = A.collect_result(state, ledger=led)

    assert result["status"] == "submitted", result.get("reason")
    assert result["report"]["language_class"] == cls
    assert factory.unused_replies() == {}
    assert graph.tfl_budget is led.root

    # every model call of every agent is in the one ledger, and nothing else is
    calls_made = {role: m.calls_made for role, m in factory.models.items() if m.calls_made}
    charged: dict[str, int] = {}
    for r in led.records:
        charged[r.role] = charged.get(r.role, 0) + 1
    assert charged == calls_made
    assert set(charged) == {"main", *subagent_roles}
    assert led.spent_usd == pytest.approx(sum(r.usd for r in led.records))
    assert 0 < led.spent_usd < 5.0 and not led.exhausted
    assert result["budget_refusals"] == []

    # each subagent call is charged to its own allocation under the task root
    tree = nodes(led)
    for r in led.records:
        if r.role == "main":
            assert r.node == "task"
        else:
            assert r.node.startswith(f"task/{SUBAGENT_NAME[r.role]}"), r.node
    children = led.root.children
    assert {c.name.split("#")[0] for c in children} == {SUBAGENT_NAME[r] for r in subagent_roles}
    for child in children:
        assert child.kind == "subagent" and child.closed
        assert child.limit == pytest.approx(2.0)  # per_subagent_max_usd
        assert child.spent == pytest.approx(sum(r.usd for r in led.records if r.node == child.path))
        assert child.inflight == 0
    assert led.root.inflight == 0
    assert tree["task"].calls == charged["main"]


def test_two_task_calls_in_the_cfl_script_get_two_allocations():
    led = ledger()
    _graph, _state, _factory = run(MOCK / "cfl_anbmcm.json", budget=led)
    assert sorted(c.name for c in led.root.children) == ["ir-builder", "prover"]


def test_run_inside_use_budget_charges_that_ledger():
    led = ledger()
    with B.use_budget(led, missing_usage="zero"):
        graph, state, _factory = run(MOCK / "ll_simple_ll1.json")
    assert graph.tfl_budget is led.root
    assert A.collect_result(state)["status"] == "submitted"
    assert {r.role for r in led.records} == {"main", "ir_builder"}
    assert any(r.node == "task/ir-builder" for r in led.records)


def test_without_a_budget_argument_the_settings_budget_applies():
    settings = example_settings()
    settings["budget"].update({"per_task_usd": 3.0, "per_subagent_max_usd": 1.5})
    graph, state, _factory = run(MOCK / "ll_simple_ll1.json", settings=settings)
    node = graph.tfl_budget
    assert isinstance(node, B.BudgetNode) and node.parent is None
    assert node.limit == pytest.approx(3.0)
    assert node.ledger.per_child_max_usd == pytest.approx(1.5)
    assert node.ledger.spend_log is None  # building / running never writes the per-day log
    assert A.collect_result(state)["status"] == "submitted"
    assert node.calls > 0 and node.children and node.children[0].limit == pytest.approx(1.5)


def test_budget_false_builds_without_budget_middleware():
    led = ledger()
    with B.use_budget(led):  # even inside an outer scope
        graph, state, _factory = run(MOCK / "ll_simple_ll1.json", budget=False)
    assert graph.tfl_budget is None
    assert A.collect_result(state)["status"] == "submitted"
    assert led.records == []


def test_bad_budget_argument_and_bad_settings_fail_closed():
    from tfl_deep_agent.settings import SettingsError

    with pytest.raises(TypeError, match="budget must be"):
        A.create_tfl_agent(example_settings(), model_factory=A.scripted_model_factory(MOCK / "ll_simple_ll1.json"),
                           budget=5.0)
    broken = example_settings()
    broken["budget"]["reserve_for_report_usd"] = -1.0  # money must be >= 0
    with pytest.raises(SettingsError):
        A.create_tfl_agent(broken, model_factory=A.scripted_model_factory(MOCK / "ll_simple_ll1.json"))


def test_settings_object_is_accepted():
    from tfl_deep_agent.settings import validate_settings

    led = ledger()
    _graph, state, factory = run(MOCK / "ll_simple_ll1.json", settings=validate_settings(example_settings()),
                                 budget=led)
    assert A.collect_result(state)["status"] == "submitted"
    assert factory.models["main"].model == config.OPUS


def test_async_run_charges_subagent_scopes():
    led = ledger()
    factory = A.scripted_model_factory(MOCK / "ll_simple_ll1.json")
    graph = A.create_tfl_agent(example_settings(), model_factory=factory, template="short_verdict", budget=led)
    state = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=factory.script["task"])]}))
    assert A.collect_result(state)["status"] == "submitted"
    sub = [r for r in led.records if r.role == "ir_builder"]
    assert sub and all(r.node == "task/ir-builder" for r in sub)
    assert led.root.children[0].closed and led.root.inflight == 0


def _sub_reply(text: str) -> dict[str, Any]:
    return {"content": text}


def _task_call(subagent: str, call_id: str, description: str = "check the language") -> dict[str, Any]:
    return {"name": "task", "id": call_id, "args": {"subagent_type": subagent, "description": description}}


def _report_call(**over: Any) -> dict[str, Any]:
    args = {"verdict": "unknown", "language_class": "reg", "method": "not settled",
            "steps": [{"claim": "no oracle result", "justification": "", "evidence_ids": []}]}
    args.update(over)
    return {"tool_calls": [{"name": "submit_report", "id": "call-report", "args": args}]}


TASK = "Является ли язык (ab)*a | b регулярным?"


def test_parallel_task_calls_get_separate_allocations():
    script = {"task": TASK, "defs": {}, "models": {
        "main": [{"tool_calls": [_task_call("ir-builder", "t1"), _task_call("critic", "t2")]}, _report_call()],
        "ir_builder": [_sub_reply("IR: ok")],
        "critic": [_sub_reply("no defect found")],
    }}
    # 3.30 - 0.30 reserve leaves 3.00: the first allocation gets the 2.00 cap; a
    # second one made while the first is still open gets only what is left
    # (about 1.00), while one made after the first closed would get 2.00 again
    led = ledger(per_task=3.3, reserve=0.3, per_subagent=2.0)
    settings = example_settings()
    settings["models"]["roles"].update({"ir_builder": config.HAIKU, "critic": config.HAIKU})
    seen: list[tuple[str, float]] = []
    both_open = threading.Barrier(2, timeout=10)
    overlapped: list[bool] = []

    def on_event(ev: dict[str, Any]) -> None:
        if ev["event"] != "allocate":
            return
        seen.append((ev["node"], ev.get("usd", 0.0)))
        try:  # each task call waits here, after allocating, until the other has allocated too
            both_open.wait()
            overlapped.append(True)
        except threading.BrokenBarrierError:
            overlapped.append(False)

    led.listeners.append(on_event)
    _graph, state, _factory = run(script, settings=settings, budget=led)
    assert A.collect_result(state, ledger=led)["status"] == "submitted"
    by_node = {r.node: r.role for r in led.records if r.role != "main"}
    assert by_node == {"task/ir-builder": "ir_builder", "task/critic": "critic"}
    assert overlapped == [True, True]  # the two task calls really ran at the same time
    assert sorted(n for n, _ in seen) == ["task/critic", "task/ir-builder"]
    main_spent = led.records[0].usd
    amounts = sorted(usd for _, usd in seen)
    assert amounts[1] == pytest.approx(2.0)
    assert amounts[0] == pytest.approx(3.0 - 2.0 - main_spent)  # not 2.00: the first was still open
    assert all(c.closed for c in led.root.children) and led.root.inflight == 0


def test_allocation_is_capped_by_what_the_parent_has_free():
    script = {"task": TASK, "defs": {}, "models": {
        "main": [{"tool_calls": [_task_call("ir-builder", "t1")]}, _report_call()],
        "ir_builder": [_sub_reply("IR: ok")],
    }}
    led = ledger(per_task=3.0, reserve=0.3, per_subagent=None)
    _graph, _state, _factory = run(script, budget=led)
    child = led.root.children[0]
    # everything free above the reserve, minus the main's first call already charged
    assert child.limit == pytest.approx(3.0 - 0.3 - led.records[0].usd)
    assert child.reserve == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Concurrency: every agent's calls hold the shared semaphore
# ---------------------------------------------------------------------------

class CountingSemaphore:
    def __init__(self) -> None:
        self._sem = threading.Semaphore(4)
        self.acquired = 0

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        ok = self._sem.acquire(blocking, timeout) if timeout is not None else self._sem.acquire(blocking)
        if ok:
            self.acquired += 1
        return ok

    def release(self) -> None:
        self._sem.release()


def test_every_model_call_holds_the_shared_semaphore(monkeypatch):
    sem = CountingSemaphore()
    monkeypatch.setattr(C, "get_concurrency_semaphore", lambda: sem)
    led = ledger()
    _graph, state, factory = run(MOCK / "reg_regex_regular.json", budget=led)
    assert A.collect_result(state)["status"] == "submitted"
    total_calls = sum(m.calls_made for m in factory.models.values())
    assert sem.acquired == total_calls == len(led.records)


# ---------------------------------------------------------------------------
# Skills load for the roles that have them
# ---------------------------------------------------------------------------

def test_skills_are_listed_in_the_prompts_of_the_skill_roles():
    _graph, _state, factory = run(MOCK / "reg_regex_regular.json", budget=False)
    for role in ("main", "ir_builder", "critic"):
        system = factory.models[role].received[0][0]
        assert isinstance(system, SystemMessage)
        text = system.text
        for cls in ("reg", "cfl", "dcfl", "ll"):
            assert f"/skills/{cls}/SKILL.md" in text, (role, cls)
    script = {"task": TASK, "defs": {}, "models": {
        "main": [{"tool_calls": [_task_call("general-purpose", "g1")]}, _report_call()],
        "general": [_sub_reply("done")],
    }}
    _graph, _state, factory = run(script, budget=False)
    assert "/skills/reg/SKILL.md" not in factory.models["general"].received[0][0].text


def test_skill_files_are_readable_through_the_agent():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [{"name": "read_file", "id": f"r-{c}", "args": {"file_path": f"/skills/{c}/SKILL.md"}}
                        for c in ("reg", "cfl", "dcfl", "ll")]},
        _report_call(),
    ]}}
    _graph, state, _factory = run(script, budget=False)
    msgs = tool_messages(state)
    for c in ("reg", "cfl", "dcfl", "ll"):
        assert f"name: {c}" in str(msgs[f"r-{c}"].content), c


# ---------------------------------------------------------------------------
# Over budget: refusal, the message the agent gets, the partial report
# ---------------------------------------------------------------------------

DFA = {"name": "regex_to_dfa", "id": "ev-dfa", "args": {"regex": "(ab)*a|b"}}


def _opus_usd(output_tokens: int, input_tokens: int = 100) -> float:
    return B.usage_cost_usd(config.OPUS, {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})


def test_main_call_over_budget_is_refused_and_a_partial_report_is_built():
    # call 1 fits (max_tokens derived from the $0.70 above the reserve), its actual usage
    # eats most of the task budget, so call 2 cannot get even the 4096-token floor
    # with the reserve
    floor = config.worst_case_usd(config.OPUS, 1, B.MIN_OUTPUT_TOKENS)
    per_task = 0.9
    spend = 42_000
    assert _opus_usd(spend) > per_task - floor  # call 2 cannot fit after it
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [DFA], "usage": {"input_tokens": 100, "output_tokens": spend}},
        _report_call(),  # never requested: the call is refused before the model
    ]}}
    led = ledger(per_task=per_task, reserve=0.2)
    _graph, state, factory = run(script, budget=led)
    result = A.collect_result(state, ledger=led)

    assert factory.models["main"].calls_made == 1
    assert led.exhausted and led.root.state == "exhausted"
    last = state["messages"][-1]
    assert isinstance(last, AIMessage) and not last.tool_calls
    assert last.response_metadata["stop_reason"] == "budget_exhausted"
    assert "BUDGET: model call refused" in last.text

    assert result["status"] == "budget_exhausted"
    assert result["report"] is None and result["confidence_cap"] == 0.40
    assert result["budget"]["worst_case_usd"] > result["budget"]["available_usd"]
    assert result["budget_refusals"] == [{"node": "task", "kind": "task", "refusals": 1, "state": "exhausted"}]
    partial = result["partial_report"]
    assert partial["status"] == "budget_exhausted"
    assert partial["verdict"] == "not established" and partial["trust"] == "not_verified"
    assert [(e["id"], e["tool"], e["trust"]) for e in partial["established"]] == [
        ("ev-dfa", "regex_to_dfa", "verified")]  # what the tools established keeps its own trust
    assert "BUDGET: model call refused" in partial["last_message"]


def test_subagent_refusal_reaches_the_main_agent_which_still_reports():
    # the subagent's allocation (per_subagent_max_usd) is below one Sonnet call at the
    # 4096-token floor (max_tokens is derived from the allocation, 2026-11-08)
    sonnet_floor = config.worst_case_usd(config.SONNET, 1, B.MIN_OUTPUT_TOKENS)
    script = {"task": TASK, "defs": {}, "models": {
        "main": [
            {"tool_calls": [_task_call("ir-builder", "t1")]},
            {**_report_call(), "expect": {"last_message_contains": "BUDGET: model call refused"}},
        ],
        "ir_builder": [_sub_reply("never requested")],
    }}
    led = ledger(per_task=5.0, per_subagent=sonnet_floor / 2)
    _graph, state, factory = run(script, budget=led)
    result = A.collect_result(state, ledger=led)

    assert factory.models["ir_builder"].calls_made == 0
    task_msg = tool_messages(state)["t1"]
    assert "BUDGET: model call refused" in str(task_msg.content)
    assert "task/ir-builder" in str(task_msg.content)
    assert result["status"] == "submitted"  # the main agent got the refusal and reported
    assert led.exhausted
    assert result["budget_refusals"] == [
        {"node": "task/ir-builder", "kind": "subagent", "refusals": 1, "state": "exhausted"}]
    # the main scope itself is not exhausted: the money of the closed child came back
    assert led.root.state == "normal" and led.root.children[0].closed


def test_subagent_refusal_without_a_report_is_budget_exhausted():
    sonnet_floor = config.worst_case_usd(config.SONNET, 1, B.MIN_OUTPUT_TOKENS)
    script = {"task": TASK, "defs": {}, "models": {
        "main": [{"tool_calls": [DFA, _task_call("ir-builder", "t1")]}, {"content": "I stop here."}],
        "ir_builder": [_sub_reply("never requested")],
    }}
    led = ledger(per_task=5.0, per_subagent=sonnet_floor / 2)
    _graph, state, _factory = run(script, budget=led)
    without_ledger = A.collect_result(state)
    result = A.collect_result(state, ledger=led)
    assert without_ledger["status"] == "incomplete"
    assert result["status"] == "budget_exhausted"
    partial = result["partial_report"]
    assert partial["status"] == "budget_exhausted"
    assert [s["subagent"] for s in partial["subagents"]] == ["ir-builder"]
    assert "BUDGET: model call refused" in partial["subagents"][0]["answer"]
    assert [e["id"] for e in partial["established"]] == ["ev-dfa"]
    assert partial["last_message"] == "I stop here."


def test_no_free_money_for_a_subagent_gives_an_error_tool_message():
    # the main scope is in normal state, but nothing is free above its reserve
    script = {"task": TASK, "defs": {}, "models": {
        "main": [
            {"tool_calls": [_task_call("ir-builder", "t1")]},
            _report_call(),
        ],
        "ir_builder": [_sub_reply("never requested")],
    }}
    led = ledger(per_task=5.0, reserve=0.3, per_subagent=2.0)
    mw = B.SubagentBudgetMiddleware(led.root, min_allocation_usd=4.9)  # more than is ever free
    factory = A.scripted_model_factory(script)
    graph = A.create_tfl_agent(
        example_settings(), model_factory=factory, budget=led,
        middleware=[B.BudgetMiddleware(led.root, role="main"), mw, C.ConcurrencyMiddleware()])
    state = A.run_task(graph, TASK)
    msg = tool_messages(state)["t1"]
    assert msg.status == "error" and "Refused: no budget for a new task call" in str(msg.content)
    assert factory.models["ir_builder"].calls_made == 0
    assert led.root.children == [] and led.root.refusals == 1 and not led.exhausted
    assert A.collect_result(state, ledger=led)["status"] == "submitted"


# ---------------------------------------------------------------------------
# Wrap-up at the reserve
# ---------------------------------------------------------------------------

def test_wrap_up_at_reserve_blocks_subagents_and_the_agent_submits():
    reserve = 0.5
    spend = 50_000
    # after call 1 only $0.05 is left above the reserve, less than the 4096-token floor
    # ($0.08+ on Opus): call 2 fits only with the reserve
    per_task = reserve + _opus_usd(spend) + 0.05
    script = {"task": TASK, "defs": {}, "models": {
        "main": [
            {"tool_calls": [DFA], "usage": {"input_tokens": 100, "output_tokens": spend}},
            {"tool_calls": [_task_call("ir-builder", "t1")], "usage": {"input_tokens": 100, "output_tokens": 50},
             "expect": {"last_message_contains": "BUDGET WRAP-UP"}},
            {**_report_call(verdict="regular", method="regex -> DFA",
                            steps=[{"claim": "DFA built", "justification": "", "evidence_ids": ["ev-dfa"]}]),
             "usage": {"input_tokens": 100, "output_tokens": 50},
             "expect": {"last_message_contains": "submit_report"}},
        ],
        "ir_builder": [_sub_reply("never requested")],
    }}
    led = ledger(per_task=per_task, reserve=reserve)
    events: list[dict[str, Any]] = []
    led.listeners.append(events.append)
    _graph, state, factory = run(script, budget=led)
    result = A.collect_result(state, ledger=led)

    assert [e["event"] for e in events].count("wrap_up") == 1
    assert led.root.state == "wrap_up" and not led.exhausted
    # the wrap-up note went to the model's request ...
    second_request = factory.models["main"].received[1]
    assert "BUDGET WRAP-UP" in second_request[-1].text and "submit_report" in second_request[-1].text
    # ... but not into the stored conversation
    assert not any("BUDGET WRAP-UP" in str(m.content) for m in state["messages"])
    # the spawning tool was refused without allocating or calling the subagent
    task_msg = tool_messages(state)["t1"]
    assert task_msg.status == "error" and "wrap-up" in str(task_msg.content)
    assert factory.models["ir_builder"].calls_made == 0 and led.root.children == []
    # the report cites only regex -> DFA, which is not checked against the task: R2
    assert result["status"] == "submitted" and result["trust"] == "not_verified"
    assert any(g.startswith("R2") for g in result["verdict_gate"])
    assert led.spent_usd <= per_task


# ---------------------------------------------------------------------------
# CLI: partial report on disk
# ---------------------------------------------------------------------------

def test_cli_budget_exhausted_writes_the_partial_report(tmp_path, runs_dir, capsys):
    # as in test_main_call_over_budget_...: call 2 cannot get the 4096-token floor
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [DFA], "usage": {"input_tokens": 100, "output_tokens": 42_000}},
        _report_call(),
    ]}}
    spath = tmp_path / "script.json"
    spath.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    settings = example_settings()
    settings["budget"].update({"per_task_usd": 0.9, "reserve_for_report_usd": 0.2})
    setpath = tmp_path / "settings.json"
    setpath.write_text(json.dumps(settings), encoding="utf-8")
    out = tmp_path / "out"
    rc = cli.main(["--mock-script", str(spath), "--save", str(out), "--settings", str(setpath),
                   "--auto-approve"])
    assert rc == 1
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "budget_exhausted"
    assert data["partial_report"]["established"][0]["id"] == "ev-dfa"
    assert data["budget_refusals"][0]["node"] == "task"
    md = (out / "result.md").read_text(encoding="utf-8")
    assert "отчёт частичный" in md and "ev-dfa" in md  # the P3 partial template
    assert not (runs_dir / B.SPEND_LOG_NAME).exists()


# ---------------------------------------------------------------------------
# Prompt caching + budget on the real ChatAnthropic request path (mock wire)
# ---------------------------------------------------------------------------

def _tool_use_events(name: str, args: dict[str, Any], call_id: str, *, model: str,
                     usage: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"type": "message_start",
         "message": {"id": f"msg_{call_id}", "type": "message", "role": "assistant", "model": model,
                     "content": [], "stop_reason": None, "stop_sequence": None,
                     "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": usage},
        {"type": "message_stop"},
    ]


class _ReplySequence:
    """``Wire.reply_events`` that hands out one scripted reply per request."""

    def __init__(self, replies: list[list[dict[str, Any]]]) -> None:
        self.replies = list(replies)

    def __iter__(self):
        assert self.replies, "the agent made more requests than scripted"
        return iter(self.replies.pop(0))


def test_prompt_caching_and_budget_on_the_chat_anthropic_path(wire):
    from tfl_deep_agent.models import make_chat_model

    from .conftest import text_reply_events

    usage = {"input_tokens": 2000, "output_tokens": 50, "cache_read_input_tokens": 1500,
             "cache_creation_input_tokens": 300}
    report = {"verdict": "unknown", "language_class": "reg", "method": "not settled",
              "steps": [{"claim": "nothing checked", "justification": "", "evidence_ids": []}]}
    wire.reply_events = _ReplySequence([
        _tool_use_events("task", {"subagent_type": "general-purpose", "description": "summarise"}, "toolu_1",
                         model=config.OPUS, usage=usage),
        text_reply_events("summary: nothing", model=config.HAIKU, usage=usage),
        _tool_use_events("submit_report", report, "toolu_2", model=config.OPUS, usage=usage),
    ])

    def factory(role: str, model_id: str):
        return make_chat_model(model_id, role=role if role in config.MODELS else None, api_key="test")

    led = ledger()
    graph = A.create_tfl_agent(example_settings(), model_factory=factory, budget=led)
    state = A.run_task(graph, TASK)
    result = A.collect_result(state, ledger=led)
    assert result["status"] == "submitted"

    bodies = [r.body for r in wire.requests]
    assert [b["model"] for b in bodies] == [config.OPUS, config.HAIKU, config.OPUS]
    for b in bodies:
        # AnthropicPromptCachingMiddleware (added by deepagents to the main and every subagent stack)
        system_blocks = b["system"] if isinstance(b["system"], list) else []
        assert b.get("cache_control") == {"type": "ephemeral", "ttl": "5m"}, json.dumps(b)[:2000]
        assert system_blocks and system_blocks[-1].get("cache_control") == {"type": "ephemeral", "ttl": "5m"}
        assert b["tools"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "5m"}
    # each call charged once, the subagent's to its own allocation, cache tokens priced
    assert [(r.node, r.role, r.model) for r in led.records] == [
        ("task", "main", config.OPUS), ("task/general-purpose", "general", config.HAIKU),
        ("task", "main", config.OPUS)]
    for r in led.records:
        # the API's input_tokens excludes the cache tokens; langchain-anthropic folds them into
        # usage_metadata.input_tokens and the budget takes them out again (DEV_NOTES "P0")
        assert (r.input_tokens, r.cache_read_tokens, r.cache_write_tokens) == (2000, 1500, 300)
        assert r.usd == pytest.approx(B.usage_cost_usd(r.model, {
            "input_tokens": 2000, "output_tokens": 50, "cache_read_input_tokens": 1500,
            "cache_creation_input_tokens": 300}))
