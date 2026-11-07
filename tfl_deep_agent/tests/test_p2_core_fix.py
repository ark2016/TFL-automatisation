"""P2 core FIX: the verdict gate, run limits, budget accounting of
summaries / refusals / cancellations, the tool filter, the report gate and
the library defaults.

Every model is a ``ScriptedChatModel``; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepagents")

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tfl_deep_agent import agent as A
from tfl_deep_agent import config
from tfl_deep_agent.middleware import budget as B
from tfl_deep_agent.middleware import concurrency as C

PKG = Path(A.__file__).resolve().parent
MOCK = PKG / "examples" / "mock"
SETTINGS_EXAMPLE = PKG / "settings.example.json"
TASK = "Является ли язык (ab)*a | b регулярным?"
DFA = {"name": "regex_to_dfa", "id": "ev-dfa", "args": {"regex": "(ab)*a|b"}}


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


def run(script: Any, *, settings: Any = None, budget: Any = None, factory: Any = None, **kw: Any):
    factory = factory or A.scripted_model_factory(script)
    graph = A.create_tfl_agent(settings if settings is not None else example_settings(),
                               model_factory=factory, template=factory.script.get("template") or "full_proof",
                               budget=budget, **kw)
    state = A.run_task(graph, factory.script["task"])
    return graph, state, factory


def report_call(call_id: str = "call-report", **over: Any) -> dict[str, Any]:
    args = {"verdict": "unknown", "language_class": "reg", "method": "not settled",
            "steps": [{"claim": "no oracle result", "justification": "", "evidence_ids": []}]}
    args.update(over)
    return {"name": "submit_report", "id": call_id, "args": args}


def tool_messages(state: dict[str, Any]) -> dict[str, ToolMessage]:
    return {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}


# ---------------------------------------------------------------------------
# Verdict gate (unit): synthetic main-thread results
# ---------------------------------------------------------------------------

IR_A = {"task_type": "x", "n": 1}
IR_B = {"task_type": "x", "n": 2}


def ev(i: str, tool: str, trust: str, *, data: dict[str, Any] | None = None, args: dict[str, Any] | None = None,
       ok: bool = True) -> dict[str, Any]:
    return {"id": i, "tool": tool, "trust": trust, "ok": ok, "data": data or {}, "args": args or {}}


def rep(verdict: str, cls: str, ids: list[str], **over: Any) -> dict[str, Any]:
    return {"verdict": verdict, "language_class": cls, "evidence_ids": ids,
            "steps": [{"claim": "c", "evidence_ids": []}], **over}


def gate(report: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    cited = [*report.get("evidence_ids", []), *(i for s in report["steps"] for i in s.get("evidence_ids", []))]
    return A.verdict_gate(report, entries, cited)


@pytest.mark.parametrize("verdict, answer, direction", [
    ("regular", None, "constructive"), ("CFL", None, "constructive"), ("LL(1)", None, "constructive"),
    ("not regular", None, "destructive"), ("non-CFL", None, "destructive"), ("nonregular", None, "destructive"),
    ("не КС", None, "destructive"), ("нерегулярен", None, "destructive"),
    ("not LL(k) for k ≤ 3; unknown for larger k", None, "destructive"),
    ("unknown", None, "inconclusive"), ("null", None, "inconclusive"), ("partial", None, "inconclusive"),
    ("uncertain", None, "inconclusive"),
    ("CFL but not DCFL", None, "inconclusive"),          # a negation inside: unclear without answer
    ("CFL but not DCFL", "no", "destructive"),
    ("regular", "no", "inconclusive"),                   # text and answer disagree
    ("regular", "yes", "constructive"),
])
def test_verdict_direction(verdict, answer, direction):
    assert A.verdict_direction(verdict, answer)[0] == direction


def test_cherry_picked_exact_result_does_not_raise_the_verdict():
    # cyk is exact but about the agent's grammar and one word: not a basis (R2)
    out = gate(rep("CFL", "cfl", ["ev-cyk"]),
               [ev("ev-cyk", "cyk", "verified"),
                ev("ev-gvi", "grammar_vs_ir", "bounded_pass", args={"grammar": {"g": 1}, "ir": IR_A})])
    assert out["trust"] == "bounded_pass" and out["confidence_cap"] == 0.85
    assert out["basis"] == ["ev-gvi"]  # code picks the check against the task, cited or not


def test_only_task_free_results_give_no_constructive_basis():
    out = gate(rep("regular", "reg", ["ev-dfa"]), [ev("ev-dfa", "regex_to_dfa", "verified")])
    assert out["trust"] == "not_verified" and out["confidence_cap"] == 0.40
    assert out["verdict_token"] == "partial"
    assert out["verdict_gate"][0].startswith("R2")


def test_cited_refuted_artifact_blocks_the_constructive_verdict():
    out = gate(rep("CFL", "cfl", ["ev-bad", "ev-good"]),
               [ev("ev-bad", "grammar_vs_ir", "refuted", args={"grammar": {"g": 1}, "ir": IR_A}),
                ev("ev-good", "grammar_vs_ir", "bounded_pass", args={"grammar": {"g": 2}, "ir": IR_A})])
    assert out["trust"] == "not_verified" and out["confidence_cap"] == 0.40
    assert out["verdict_token"] is None  # cfl inconclusive token: null
    assert "refutes" in out["verdict_gate"][0]


def test_uncited_refutation_drops_the_same_artifact():
    g = {"grammar": {"g": 1}, "ir": IR_A}
    out = gate(rep("CFL", "cfl", ["ev-pass"]),
               [ev("ev-pass", "grammar_vs_ir", "bounded_pass", args=g),
                ev("ev-fail", "grammar_vs_ir", "refuted", args={**g, "sample_max_len": 12})])
    assert out["trust"] == "not_verified"
    assert any("dropped" in x for x in out["verdict_gate"])


def test_fake_id_lowers_the_trust_one_level():
    out = gate(rep("CFL", "cfl", ["ev-gvi", "ev-bogus"]),
               [ev("ev-gvi", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_A})])
    assert out["trust"] == "well_formed" and out["confidence_cap"] == 0.55
    assert any("ev-bogus" in x for x in out["verdict_gate"])


def test_report_fingerprint_excludes_results_for_another_ir():
    from tfl_deep_agent.tools.ir import ir_fingerprint

    entries = [ev("ev-a", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_B})]
    out = gate(rep("CFL", "cfl", ["ev-a"], ir_fingerprint=ir_fingerprint(IR_A)), entries)
    assert out["trust"] == "not_verified" and out["excluded_ir_mismatch"] == ["ev-a"]
    same = gate(rep("CFL", "cfl", ["ev-a"], ir_fingerprint=ir_fingerprint(IR_B)), entries)
    assert same["trust"] == "bounded_pass"


def test_basis_over_several_irs_without_a_report_fingerprint_is_not_admissible():
    out = gate(rep("CFL", "cfl", []),
               [ev("ev-a", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_A}),
                ev("ev-b", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_B})])
    assert out["trust"] == "not_verified" and "different IRs" in out["verdict_gate"][0]


def test_destructive_verdict_needs_destructive_evidence():
    out = gate(rep("not regular", "reg", []), [ev("ev-dfa", "regex_to_dfa", "verified")])
    assert out["trust"] == "not_verified" and out["verdict_gate"][0].startswith("R1")
    assert out["verdict_token"] == "partial"
    ok = gate(rep("not regular", "reg", ["ev-n"]),
              [ev("ev-n", "nerode_probe", "bounded_pass", data={"cls": "reg", "pairs_checked": 3})])
    assert ok["trust"] == "bounded_pass" and ok["verdict_token"] == "non_regular"


def test_contradiction_without_a_verified_side_is_inconclusive():
    out = gate(rep("CFL", "cfl", ["ev-gvi"]),
               [ev("ev-gvi", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_A}),
                ev("ev-pump", "pump_check_cfl", "well_formed", data={"mode": "claim"}, args={"ir": IR_A})])
    assert out["contradiction"] is True and out["confidence_cap"] == 0.50
    assert out["verdict_token"] is None


def test_verified_side_keeps_the_verdict_against_a_contradiction_at_085():
    out = gate(rep("CFL", "cfl", []),
               [ev("ev-in", "input_grammar_cfg", "verified", args={"ir": IR_A}),
                ev("ev-pump", "pump_check_cfl", "well_formed", data={"mode": "claim"}, args={"ir": IR_A})])
    assert out["trust"] == "verified" and out["confidence_cap"] == 0.85 and out["contradiction"]


def test_cited_refuted_supporting_result_caps_at_050():
    out = gate(rep("CFL", "cfl", ["ev-gvi", "ev-cyk"]),
               [ev("ev-gvi", "grammar_vs_ir", "bounded_pass", args={"grammar": {}, "ir": IR_A}),
                ev("ev-cyk", "cyk", "refuted")])
    assert out["confidence_cap"] == 0.50 and out["contradiction"]


def test_inconclusive_verdict_is_capped_at_040():
    out = gate(rep("unknown", "ll", []), [])
    assert out["trust"] == "not_verified" and out["confidence_cap"] == 0.40
    assert out["verdict_token"] == "uncertain"


LL_IR = {"task_type": "ll_check_grammar", "question": "is_ll_k", "k": 1}


def ll_ev(i: str, trust: str, *, source: str = "ir.grammar", mode: str = "check", k: int = 1,
          **data: Any) -> dict[str, Any]:
    args = {"ir": LL_IR} if source.startswith("ir") else {"grammar": {"g": 1}}
    return ev(i, "ll_table", trust, data={"grammar_source": source, "mode": mode, "k": k, **data}, args=args)


def test_ll_conflict_on_the_task_grammar_is_a_destructive_basis():
    out = gate(rep("not LL(1)", "ll", ["ev-t"]), [ll_ev("ev-t", "refuted")])
    assert out["trust"] == "verified" and out["verdict_token"] == "not_ll"
    # the same result refutes an "LL(1)" verdict
    bad = gate(rep("LL(1)", "ll", ["ev-t"]), [ll_ev("ev-t", "refuted")])
    assert bad["trust"] == "not_verified"


def test_ll_bounded_search_supports_not_ll_for_any_k_only_at_085():
    out = gate(rep("not LL(k) for any k", "ll", []),
               [ll_ev("ev-m", "bounded_pass", mode="min_k", max_k_checked=3)])
    assert out["trust"] == "bounded_pass" and out["confidence_cap"] == 0.85
    cert = gate(rep("not LL(k) for any k", "ll", []),
                [ll_ev("ev-m", "verified", mode="min_k", certificate={"left_recursive": ["S"]})])
    assert cert["trust"] == "verified"


def test_ll_grammar_written_by_the_agent_needs_an_equivalence_check():
    out = gate(rep("LL(1)", "ll", ["ev-t"]), [ll_ev("ev-t", "verified", source="grammar")])
    assert out["trust"] == "not_verified" and "well_formed" in out["verdict_gate"][0]
    eq = ev("ev-eq", "grammar_vs_ir", "bounded_pass", args={"grammar": {"g": 1}, "ir": LL_IR, "cls": "ll"})
    with_eq = gate(rep("LL(1)", "ll", ["ev-t"]), [ll_ev("ev-t", "verified", source="grammar"), eq])
    assert with_eq["trust"] == "bounded_pass"


def test_ll_grammar_of_a_language_question_is_no_destructive_evidence():
    out = gate(rep("not LL(1)", "ll", []), [ll_ev("ev-t", "refuted", source="ir.language_spec")])
    assert out["trust"] == "not_verified" and out["verdict_gate"][0].startswith("R1")


# ---------------------------------------------------------------------------
# Verdict gate end to end
# ---------------------------------------------------------------------------

def test_ll_script_on_a_grammar_outside_the_ir_is_not_verified():
    script = json.loads((MOCK / "ll_simple_ll1.json").read_text(encoding="utf-8"))
    for reply in script["models"]["main"]:
        for tc in reply.get("tool_calls") or []:
            if tc["name"] in ("first_follow", "ll_table", "ll_parse"):
                tc["args"] = {"grammar": {"$def": "grammar"}, **{k: v for k, v in tc["args"].items() if k != "ir"}}
    _graph, state, _factory = run(script, budget=False)
    result = A.collect_result(state)
    assert result["status"] == "submitted"
    assert result["trust"] == "not_verified" and result["confidence_cap"] == 0.40
    assert result["verdict_token"] == "uncertain"


def test_answer_field_is_part_of_the_report():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [DFA]},
        {"tool_calls": [report_call(verdict="regular", answer="no", evidence_ids=["ev-dfa"])]},
    ]}}
    _graph, state, _factory = run(script, budget=False)
    result = A.collect_result(state)
    assert result["report"]["answer"] == "no"
    assert result["verdict_direction"] == "inconclusive" and result["confidence_cap"] == 0.40


# ---------------------------------------------------------------------------
# Report gate: submit_report together with other tool calls
# ---------------------------------------------------------------------------

def test_report_sent_with_another_tool_call_still_ends_the_run():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [report_call(), {"name": "list_theorems", "id": "th", "args": {}}]},
        {"content": "never requested"},
    ]}}
    _graph, state, factory = run(script, budget=False)
    assert A.collect_result(state)["status"] == "submitted"
    assert factory.models["main"].calls_made == 1
    assert A.report_accepted_last_turn(state["messages"])


# ---------------------------------------------------------------------------
# Tool filter: an excluded tool is not dispatched even when called
# ---------------------------------------------------------------------------

def test_execute_called_anyway_is_refused_without_running():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [{"name": "execute", "id": "x1", "args": {"command": "echo pwned"}}]},
        {"tool_calls": [report_call()]},
    ]}}
    base = A.scripted_model_factory(script)

    def factory(role: str, model_id: str) -> Any:
        m = base(role, model_id)
        m.strict_tools = False  # a hallucinated / injected call to an unbound tool
        return m

    factory.script = base.script  # type: ignore[attr-defined]
    _graph, state, _ = run(script, budget=False, factory=factory)
    msg = tool_messages(state)["x1"]
    assert msg.status == "error" and "not available" in str(msg.content)
    assert "pwned" not in str(msg.content)


# ---------------------------------------------------------------------------
# Run limits (settings.limits)
# ---------------------------------------------------------------------------

def _limits(**over: Any) -> dict[str, Any]:
    s = example_settings()
    s["limits"].update(over)
    return s


def test_max_model_calls_stops_the_run():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [{"name": "list_theorems", "id": f"th{i}", "args": {}}]} for i in range(5)]}}
    graph, state, factory = run(script, settings=_limits(max_model_calls=3), budget=False)
    assert factory.models["main"].calls_made == 3
    result = A.collect_result(state)
    assert result["status"] == "incomplete" and "max_model_calls" in result["reason"]
    assert graph.tfl_limits.refused_model_calls == 1
    last = [m for m in state["messages"] if isinstance(m, AIMessage)][-1]
    assert last.response_metadata["stop_reason"] == A.CALL_LIMIT_STOP
    # the last allowed call carried the wrap-up note
    assert "CALL LIMIT WRAP-UP" in factory.models["main"].received[2][-1].text


def test_max_tool_calls_refuses_tools_but_never_the_report():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [{"name": "list_theorems", "id": "th1", "args": {}},
                        {"name": "list_theorems", "id": "th2", "args": {}}]},
        {"tool_calls": [report_call()]},
    ]}}
    graph, state, _ = run(script, settings=_limits(max_tool_calls=1), budget=False)
    msgs = tool_messages(state)
    statuses = sorted(msgs[i].status for i in ("th1", "th2"))
    assert statuses == ["error", "success"]
    assert A.collect_result(state)["status"] == "submitted"
    assert graph.tfl_limits.refused_tool_calls == 1


def test_spawn_depth_zero_refuses_task():
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [{"name": "task", "id": "t1", "args": {"subagent_type": "critic", "description": "x"}}]},
        {"tool_calls": [report_call()]},
    ], "critic": [{"content": "never requested"}]}}
    _graph, state, factory = run(script, settings=_limits(max_spawn_depth=0), budget=False)
    assert tool_messages(state)["t1"].status == "error"
    assert factory.models["critic"].calls_made == 0


def test_max_parallel_semaphore_reaches_every_concurrency_middleware():
    graph, _state, _ = run(MOCK / "ll_simple_ll1.json", settings=_limits(max_parallel=1), budget=False)
    sem = graph.tfl_limits.semaphore
    mws = A.default_middleware(A.settings_dict(_limits(max_parallel=1)), "critic", run_semaphore=sem)
    conc = [m for m in mws if isinstance(m, C.ConcurrencyMiddleware)]
    assert conc and conc[0].run_semaphore is sem
    # a held run slot blocks a second call of the same run
    assert sem.acquire(blocking=False)
    try:
        assert not sem.acquire(blocking=False)
    finally:
        sem.release()


def test_concurrency_middleware_holds_the_run_slot_and_the_shared_one():
    shared = threading.Semaphore(2)
    run_sem = threading.BoundedSemaphore(1)
    mw = C.ConcurrencyMiddleware(semaphore_factory=lambda: shared, run_semaphore=run_sem)
    seen = []

    def handler(_req):
        seen.append((run_sem.acquire(blocking=False), shared._value))
        return "ok"

    assert mw.wrap_model_call(object(), handler) == "ok"
    assert seen == [(False, 1)]  # run slot taken, one shared slot taken
    assert run_sem.acquire(blocking=False)
    run_sem.release()

    async def go():
        return await mw.awrap_model_call(object(), _async_handler)

    async def _async_handler(_req):
        return run_sem.acquire(blocking=False)

    assert asyncio.run(go()) is False


def test_system_prompt_says_the_limits_are_enforced():
    text = A.main_system_prompt(_limits(max_spawn_depth=0))
    assert "enforced by code" in text and "subagents are disabled" in text
    assert "max_spawn_depth = 2" not in A.main_system_prompt(example_settings())


# ---------------------------------------------------------------------------
# Budget: refusals, cancellations, summaries, the lean role
# ---------------------------------------------------------------------------

def _request(model_id: str = config.HAIKU, max_tokens: int | None = None) -> ModelRequest:
    m = A.scripted_model_class()(model=model_id, role="main", replies=[])
    return ModelRequest(model=m, messages=[HumanMessage(content="hello")], tools=[],
                        model_settings={"max_tokens": max_tokens} if max_tokens else {})


def test_served_refusal_is_charged_with_its_usage():
    from langchain_core.exceptions import ModelRefusalError

    led = ledger()
    mw = B.BudgetMiddleware(led.root, role="main", model=config.HAIKU)
    raw = AIMessage(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500})

    def handler(_req):
        raise ModelRefusalError("refused", raw_message=raw)

    with pytest.raises(ModelRefusalError):
        mw.wrap_model_call(_request(), handler)
    assert len(led.records) == 1 and led.records[0].outcome == "refusal"
    assert led.records[0].output_tokens == 500 and led.root.inflight == 0
    assert led.records[0].usd == pytest.approx(B.usage_cost_usd(config.HAIKU, B.usage_counts(raw.usage_metadata)))


def test_cancelled_call_is_charged_its_worst_case():
    led = ledger()
    mw = B.BudgetMiddleware(led.root, role="main", model=config.HAIKU)

    async def handler(_req):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(mw.awrap_model_call(_request(), handler))
    rec = led.records[0]
    assert rec.outcome == "cancelled" and rec.usage_missing and rec.usd == pytest.approx(rec.worst_case_usd)
    assert led.root.inflight == 0


def test_api_error_before_a_response_is_released():
    led = ledger()
    mw = B.BudgetMiddleware(led.root, role="main", model=config.HAIKU)

    def handler(_req):
        raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError):
        mw.wrap_model_call(_request(), handler)
    assert led.records == [] and led.root.inflight == 0 and led.root.spent == 0


def _summary_model(text: str = "SUMMARY") -> Any:
    return A.scripted_model_class()(model=config.HAIKU, role="main", replies=[{"content": text}])


def test_budgeted_summarizer_charges_the_summary_call():
    led = ledger()
    mw = B.BudgetMiddleware(led.root, role="main", model=config.HAIKU)
    model = _summary_model()
    summarizer = B.budgeted_summarizer(model, mw, wrappers=[C.ConcurrencyMiddleware()])
    out = summarizer.invoke([HumanMessage(content="first"), AIMessage(content="second")])
    assert out == "SUMMARY" and model.calls_made == 1
    assert [r.outcome for r in led.records] == ["summary"] and led.records[0].max_tokens == B.SUMMARY_MAX_TOKENS
    assert "first" in str(model.received[0][-1].content)  # deepagents' summary prompt over the messages
    out = asyncio.run(B.budgeted_summarizer(_summary_model("ASYNC"), mw).ainvoke([HumanMessage(content="x")]))
    assert out == "ASYNC" and len(led.records) == 2


def test_budgeted_summarizer_refused_makes_no_call():
    led = ledger(per_task=0.01, reserve=0.0)
    mw = B.BudgetMiddleware(led.root, role="main", model=config.OPUS)
    model = A.scripted_model_class()(model=config.OPUS, role="main", replies=[{"content": "never"}])
    out = B.budgeted_summarizer(model, mw).invoke([HumanMessage(content="first")])
    assert out.startswith("[No summary") and model.calls_made == 0 and led.records == []


def test_every_budgeted_stack_gets_the_budgeted_summarization(monkeypatch):
    import deepagents.middleware.summarization as S

    made: list[Any] = []
    real = S.create_summarization_middleware

    def spy(model, backend, **kw):
        mw = real(model, backend, **kw)
        made.append((model.role, kw.get("summarizer"), mw))
        return mw

    monkeypatch.setattr(S, "create_summarization_middleware", spy)
    led = ledger()
    _graph, _state, factory = run(MOCK / "reg_regex_regular.json", budget=led)
    roles = {r for r, s, _ in made if s is not None}
    assert {"main", "ir_builder", "critic", "prover", "general"} <= roles
    assert all(mw.name == "SummarizationMiddleware" for _, _, mw in made)  # replaces deepagents' one by name
    # the main stack's summarizer charges the run's ledger
    main_mw = next(mw for r, _, mw in made if r == "main")
    n = len(led.records)
    factory.models["main"].replies.append({"content": "SUMMARY"})
    assert main_mw._create_summary([HumanMessage(content="a"), AIMessage(content="b")]) == "SUMMARY"
    assert len(led.records) == n + 1 and led.records[-1].outcome == "summary"


def test_lean_role_fits_max_tokens_into_the_default_allocation():
    led = ledger(per_task=5.0, per_subagent=2.0)
    child = led.root.allocate("lean", 2.0, reserve_usd=0.3)
    mw = B.make_middleware(example_settings(), role="lean", node=child)
    assert mw.fit_max_tokens is True
    seen: list[int] = []

    def handler(req):
        seen.append(req.model_settings["max_tokens"])
        return ModelResponse(result=[AIMessage(content="ok", usage_metadata={
            "input_tokens": 10, "output_tokens": 10, "total_tokens": 20})])

    out = mw.wrap_model_call(_request(config.OPUS, max_tokens=128_000), handler)
    assert seen and seen[0] < 128_000
    assert (out.result[0].additional_kwargs or {}).get("tfl_budget") != "refused"
    assert B.make_middleware(example_settings(), role="prover", node=child).fit_max_tokens is False


# ---------------------------------------------------------------------------
# Wrap-up followed by text: budget_exhausted
# ---------------------------------------------------------------------------

def test_text_answer_after_wrap_up_is_budget_exhausted():
    worst = config.worst_case_usd(config.OPUS, 1, config.MAX_TOKENS)
    spend = 50_000
    reserve = 0.5
    per_task = worst + reserve + \
        B.usage_cost_usd(config.OPUS, {"input_tokens": 100, "output_tokens": spend, "cache_read_input_tokens": 0,
                                       "cache_creation_input_tokens": 0}) - 0.05
    script = {"task": TASK, "defs": {}, "models": {"main": [
        {"tool_calls": [DFA], "usage": {"input_tokens": 100, "output_tokens": spend}},
        {"content": "Язык регулярен, отчёт не нужен.", "usage": {"input_tokens": 100, "output_tokens": 50},
         "expect": {"last_message_contains": "BUDGET WRAP-UP"}},
    ]}}
    led = ledger(per_task=per_task, reserve=reserve)
    _graph, state, _ = run(script, budget=led)
    assert led.root.state == "wrap_up" and not led.exhausted
    result = A.collect_result(state, ledger=led)
    assert result["status"] == "budget_exhausted" and "wrap-up" in result["reason"]
    assert result["partial_report"]["verdict"] == "not established"


# ---------------------------------------------------------------------------
# Library defaults
# ---------------------------------------------------------------------------

def test_create_tfl_agent_needs_an_explicit_factory():
    with pytest.raises(TypeError, match="explicit model_factory"):
        A.create_tfl_agent(example_settings())


def _live_scripted(script: Any) -> Any:
    base = A.scripted_model_factory(script)

    def factory(role: str, model_id: str) -> Any:
        return base(role, model_id)

    factory.tfl_live = True  # type: ignore[attr-defined]
    factory.script = base.script  # type: ignore[attr-defined]
    return factory


def test_live_factory_without_budget_uses_the_per_day_ledger(runs_dir):
    assert A.is_live_factory(A.default_model_factory)
    factory = _live_scripted(MOCK / "ll_simple_ll1.json")
    graph = A.create_tfl_agent(example_settings(), model_factory=factory)
    led = graph.tfl_budget.ledger
    assert led.spend_log is not None and led.per_day_usd == pytest.approx(20.0)
    with pytest.raises(ValueError, match="live model factory needs a budget"):
        A.create_tfl_agent(example_settings(), model_factory=factory, budget=False)


def test_live_factory_inside_use_budget_keeps_that_scope():
    factory = _live_scripted(MOCK / "ll_simple_ll1.json")
    led = ledger()
    with B.use_budget(led):
        graph = A.create_tfl_agent(example_settings(), model_factory=factory)
    assert graph.tfl_budget is led.root


def test_scripted_factory_without_budget_keeps_the_in_memory_ledger():
    graph = A.create_tfl_agent(copy.deepcopy(example_settings()),
                               model_factory=A.scripted_model_factory(MOCK / "ll_simple_ll1.json"))
    assert graph.tfl_budget.ledger.spend_log is None


# ---------------------------------------------------------------------------
# Prompts and the DCFL skill wording
# ---------------------------------------------------------------------------

def _text(rel: str) -> str:
    return " ".join((PKG / rel).read_text(encoding="utf-8").split())


def test_dcfl_shallit_needs_finite_classes_not_separated_pairs():
    traps = _text("skills/dcfl/traps.md")
    skill = _text("skills/dcfl/SKILL.md")
    assert "that is, every pair u ≠ v separated" not in traps
    assert "every** Nerode class finite" in traps
    assert "for **arbitrary** u ≠ v" not in skill
    assert "sufficient but not necessary" in skill


def test_dcfl09_is_a_not_dcfl_reduction_not_a_not_cfl_one():
    skill = _text("skills/dcfl/SKILL.md")
    first = skill.split("Same pattern: if op(L) is not CFL", 1)[1].split("A second pattern", 1)[0]
    assert "dcfl-09" not in first
    second = skill.split("A second pattern", 1)[1].split("Non-closure under ∪", 1)[0]
    assert "dcfl-09" in second and "CFL but not DCFL" in second


def test_prompts_allow_verbatim_skill_theory_and_name_the_tokens():
    main = _text("prompts/main.md")
    assert "/skills/<class>/theory.md" in main and "`answer`" in main
    for token in ("`non_regular`", "`partial`", "`non_cfl`", "`null`", "`non_dcfl`", "`not_ll`", "`uncertain`"):
        assert token in main, token
    assert "can only lower the ceiling" in main
    for role in ("prover", "critic"):
        assert "/skills/<class>/theory.md" in _text(f"prompts/{role}.md"), role
