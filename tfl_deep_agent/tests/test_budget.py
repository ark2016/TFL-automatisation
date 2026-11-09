"""middleware/budget.py (ledger, daily log, BudgetMiddleware) and
middleware/concurrency.py. Scripted chat models only; nothing reaches the API."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import time

import pytest

from tfl_deep_agent import config
from tfl_deep_agent import settings as S
from tfl_deep_agent.middleware import budget as B

pytest.importorskip("langchain.agents")

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from tfl_deep_agent.middleware.concurrency import (
    ConcurrencyMiddleware,
    acquire_async,
)
from tfl_deep_agent.middleware.concurrency import make_middleware as concurrency_hook

OPUS, SONNET, HAIKU = config.OPUS, config.SONNET, config.HAIKU


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class Scripted(BaseChatModel):
    """Replays ``replies`` in order and records what it was called with."""

    replies: list
    model: str | None = None
    max_tokens: int | None = None
    calls: list = []
    delay: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kw):
        kw.pop("tool_choice", None)
        return self.bind(**kw) if kw else self   # model_settings (max_tokens) reach _generate

    def _next(self, messages, kw):
        self.calls.append({"messages": list(messages), "kwargs": dict(kw)})
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, BaseException):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        if self.delay:
            time.sleep(self.delay)
        return self._next(messages, kw)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._next(messages, kw)


def usage(total_in=1000, out=200, cache_read=0, cache_write=0):
    return {"input_tokens": total_in, "output_tokens": out, "total_tokens": total_in + out,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write}}


def ai(text="done", tool_calls=None, **u):
    return AIMessage(content=text, tool_calls=tool_calls or [], usage_metadata=usage(**u) if u is not None else None)


@tool
def spawn_subagent(name: str) -> str:
    """Spawn an ad-hoc subagent."""
    return f"spawned {name}"


@tool
def check_membership(word: str) -> str:
    """Membership oracle."""
    return f"{word}: yes"


def make_agent(chat, node, *, tools=(spawn_subagent, check_membership), **mw):
    return create_agent(chat, tools=list(tools), middleware=[B.BudgetMiddleware(node, role="main", **mw)])


def leave_next(model, n, budget, ceiling, floor=B.MIN_OUTPUT_TOKENS):
    """The largest c <= ceiling with: this call (n in, c out) plus one more call at the
    floor whose prompt grew by c + NEXT_CALL_MARGIN_TOKENS fits in ``budget`` (0: none)."""
    def fits(c):
        return (config.worst_case_usd(model, n, c)
                + config.worst_case_usd(model, n + c + B.NEXT_CALL_MARGIN_TOKENS, floor)) <= budget + 1e-9
    if not fits(0):
        return 0
    c = ceiling
    while not fits(c):  # a plain downward scan, independent of the middleware's bisection
        c -= 1
    return c


def normal_cap(model, n, avail, reserve, ceiling):
    """A normal call's derived max_tokens: above the reserve AND leaving the next floor call payable."""
    return min(config.max_tokens_within(model, n, avail - reserve, ceiling), leave_next(model, n, avail, ceiling))


def opus_cost(plain=0, out=0, cr=0, cw=0):
    c = config.MODEL_PRICING[OPUS]
    return (plain * c["input"] + out * c["output"] + cr * c["cache_read"] + cw * c["cache_write"]) / 1e6


class FakeRequest:
    def __init__(self, messages, tools=(), system=None):
        self.messages = messages
        self.tools = list(tools)
        self.system_message = SystemMessage(content=system) if system else None


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

def test_allocation_comes_out_of_the_parent_and_is_capped():
    led = B.BudgetLedger(5.0, reserve_for_report_usd=0.3, per_subagent_max_usd=2.0)
    root = led.root
    a = root.allocate("a", 2.0)
    assert root.available() == pytest.approx(3.0)
    assert a.available() == pytest.approx(2.0)
    with pytest.raises(B.BudgetExceeded, match="per_subagent_max_usd"):
        root.allocate("b", 2.5)
    b = root.allocate("b", 2.0)
    # 1.0 left, 0.3 of it is the reserve: an allocation may not touch it
    with pytest.raises(B.BudgetExceeded, match="does not fit") as exc:
        root.allocate("c", 0.8)
    assert exc.value.details["free_usd"] == pytest.approx(0.7)
    root.allocate("c", 0.7)
    # workflow containers are not capped by per_subagent_max_usd, only by what is free
    led2 = B.BudgetLedger(5.0, per_subagent_max_usd=1.0)
    wf = led2.root.allocate("wf", 3.0, kind="workflow")
    assert wf.allocate("br1", 1.0, kind="branch").path == "task/wf/br1"
    with pytest.raises(B.BudgetExceeded, match="per_subagent_max_usd"):
        wf.allocate("br2", 1.5, kind="branch")
    assert {a.depth, b.depth, wf.depth} == {1}
    with pytest.raises(ValueError):
        root.allocate("z", 0)
    with pytest.raises(ValueError):
        root.allocate("z", float("nan"))


def test_duplicate_child_names_are_made_unique():
    led = B.BudgetLedger(5.0)
    assert [led.root.allocate("x", 0.1).name for _ in range(3)] == ["x", "x#2", "x#3"]


def test_child_spends_within_its_allocation_and_close_returns_the_rest():
    led = B.BudgetLedger(5.0)
    child = led.root.allocate("sub", 1.0)
    adm = child.admit(0.4)
    assert adm.ok
    assert led.root.available() == pytest.approx(4.0)      # the promise stays reserved
    child.settle(adm.reservation, B.CallRecord("task/sub", "prover", OPUS, 0.25, 0.4))
    assert child.available() == pytest.approx(0.75)
    assert led.root.spent == pytest.approx(0.25)
    assert led.root.available() == pytest.approx(4.0)
    assert not child.admit(0.8).ok                          # over the allocation, parent has money
    assert child.state == "exhausted"
    assert child.close() == pytest.approx(0.75)
    assert led.root.available() == pytest.approx(4.75)
    assert not child.admit(0.01).ok                         # closed scope
    with pytest.raises(B.BudgetExceeded, match="closed"):
        child.allocate("x", 0.1)


def test_sibling_promises_are_respected():
    led = B.BudgetLedger(2.0)
    a = led.root.allocate("a", 1.0)
    b = led.root.allocate("b", 1.0)
    assert led.root.available() == pytest.approx(0.0)
    assert a.admit(1.0).ok
    assert b.admit(1.0).ok
    assert not led.root.admit(0.01).ok


def test_ancestor_overrun_shrinks_children():
    led = B.BudgetLedger(2.0)
    child = led.root.allocate("sub", 1.0)
    # the root overran its own share (actual > estimate): 1.0 of own money, 1.3 charged
    led.root.charge(B.CallRecord("task", "main", OPUS, 1.3, 1.0))
    assert led.root.available() == pytest.approx(-0.3)
    assert child.available() == pytest.approx(0.7)
    assert not child.admit(0.8).ok


def test_inflight_reservations_block_parallel_overcommit():
    led = B.BudgetLedger(1.0)
    first = led.root.admit(0.6)
    assert first.ok and led.root.inflight == pytest.approx(0.6)
    assert not led.root.admit(0.6).ok
    led.root.release(first.reservation)
    led.root.release(first.reservation)                     # idempotent
    assert led.root.inflight == 0.0
    assert led.root.available() == pytest.approx(1.0)


def test_wrap_up_uses_the_reserve_then_refuses():
    events = []
    led = B.BudgetLedger(1.0, reserve_for_report_usd=0.3, listeners=[events.append])
    root = led.root
    ok = root.admit(0.6)                                    # 0.6 <= 1.0 - 0.3
    assert ok.ok and not ok.wrap_up
    root.settle(ok.reservation, B.CallRecord("task", "main", OPUS, 0.6, 0.6))
    adm = root.admit(0.2)                                   # 0.2 > 0.4 - 0.3: wrap-up, fits in 0.4
    assert adm.ok and adm.wrap_up and adm.entered_wrap_up
    assert root.state == "wrap_up" and root.in_wrap_up()
    root.settle(adm.reservation, B.CallRecord("task", "main", OPUS, 0.2, 0.2))
    with pytest.raises(B.BudgetExceeded, match="wrap-up"):
        root.allocate("late", 0.05)
    refused = root.admit(0.3)
    assert not refused.ok
    assert root.state == "exhausted" and led.exhausted
    kinds = [e["event"] for e in events]
    assert kinds.count("wrap_up") == 1 and "refused" in kinds and kinds.count("call") == 2


def test_listener_errors_do_not_break_accounting():
    def boom(_e):
        raise RuntimeError("ui gone")

    led = B.BudgetLedger(1.0, listeners=[boom])
    adm = led.root.admit(0.1)
    led.root.settle(adm.reservation, B.CallRecord("task", "main", OPUS, 0.05, 0.1))
    assert led.spent_usd == pytest.approx(0.05)


def test_summary_by_role_and_model():
    led = B.BudgetLedger(5.0)
    sub = led.root.allocate("critic", 1.0)
    led.root.charge(B.CallRecord("task", "main", OPUS, 0.5, 1.0))
    sub.charge(B.CallRecord("task/critic", "critic", SONNET, 0.2, 0.3))
    sub.charge(B.CallRecord("task/critic", "critic", HAIKU, 0.01, 0.1))
    s = led.summary()
    assert s["spent_usd"] == pytest.approx(0.71)
    assert s["by_role"] == pytest.approx({"main": 0.5, "critic": 0.21})
    assert s["by_model"] == pytest.approx({OPUS: 0.5, SONNET: 0.2, HAIKU: 0.01})
    assert s["tree"]["children"][0]["node"] == "task/critic"
    assert s["calls"] == 3 and s["exhausted"] is False


# ---------------------------------------------------------------------------
# per-day spend log
# ---------------------------------------------------------------------------

class Clock:
    def __init__(self, day="2026-11-07"):
        self.day = day

    def __call__(self):
        return self.day


def test_daily_log_totals_by_day_and_task(tmp_path):
    clock = Clock()
    log = B.DailySpendLog(tmp_path / "runs" / B.SPEND_LOG_NAME, clock=clock)
    assert log.day_total() == 0.0                           # no file yet
    log.append({"task_id": "t1", "usd": 1.5})
    log.append({"task_id": "t2", "usd": 2.0})
    clock.day = "2026-11-08"
    log.append({"task_id": "t1", "usd": 0.25})
    assert log.day_total("2026-11-07") == pytest.approx(3.5)
    assert log.day_total() == pytest.approx(0.25)
    assert log.task_total("t1") == pytest.approx(1.75)
    lines = (tmp_path / "runs" / B.SPEND_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert all(json.loads(x)["ts"] for x in lines)


def test_daily_log_skips_bad_lines(tmp_path):
    p = tmp_path / B.SPEND_LOG_NAME
    p.write_text('{"day": "2026-11-07", "usd": 1}\nnot json\n{"day": "2026-11-07"}\n'
                 '{"day": "2026-11-07", "usd": "NaN"}\n\n', encoding="utf-8")
    log = B.DailySpendLog(p, clock=Clock())
    assert log.day_total() == 1.0
    assert log.bad_lines == 3


def test_new_task_must_fit_in_the_day(tmp_path):
    log = B.DailySpendLog(tmp_path / B.SPEND_LOG_NAME, clock=Clock())
    log.append({"task_id": "old", "usd": 16.0})
    st = S.default_settings()
    with pytest.raises(B.DailyBudgetExceeded) as exc:
        B.BudgetLedger.for_task(st, spend_log=log)
    assert exc.value.details["remaining_usd"] == pytest.approx(4.0)
    assert "$10.00" in str(exc.value)
    small = S.apply_overrides(st, {"budget": {"per_task_usd": 4.0}})
    led = B.BudgetLedger.for_task(small, spend_log=log, task_id="new")
    assert led.root.limit == 4.0 and led.root.reserve == 1.0 and led.per_child_max_usd == 2.0


@pytest.mark.parametrize("per_task, reserve, kept", [
    (10.0, 1.0, 1.0),     # the defaults: the full reserve
    (4.0, 1.0, 1.0),      # a quarter of 4.00 is exactly the reserve
    (1.0, 1.0, 0.25),     # a $1 live smoke run keeps a quarter, not the whole budget
    (0.5, 0.3, 0.125),
    (10.0, 12.0, 2.5),    # a reserve above the task budget is no longer an error
])
def test_the_task_ledger_keeps_at_most_a_quarter_of_the_task_as_reserve(tmp_path, per_task, reserve, kept):
    """User decision 2026-11-08: the report reserve is min(reserve_for_report_usd, per_task_usd / 4),
    the rule fixed subagents already follow, so a small task budget still has room to work."""
    st = S.apply_overrides(S.default_settings(),
                           {"budget": {"per_task_usd": per_task, "reserve_for_report_usd": reserve}})
    assert st.budget.report_reserve_usd == pytest.approx(kept)
    led = B.BudgetLedger.for_task(st, spend_log=B.DailySpendLog(tmp_path / B.SPEND_LOG_NAME, clock=Clock()))
    assert led.root.reserve == pytest.approx(kept)


def test_calls_are_logged_and_checked_against_the_day(tmp_path):
    log = B.DailySpendLog(tmp_path / B.SPEND_LOG_NAME, clock=Clock())
    st = S.default_settings()
    led = B.BudgetLedger.for_task(st, spend_log=log, task_id="t")
    adm = led.root.admit(0.5)
    led.root.settle(adm.reservation, B.CallRecord("task", "main", OPUS, 0.4, 0.5))
    entry = json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["task_id"] == "t" and entry["usd"] == 0.4 and entry["model"] == OPUS
    # another process spends most of the day meanwhile
    log.append({"task_id": "other", "usd": 18.0})
    assert led.root.available() == pytest.approx(20.0 - 18.4)
    assert not led.root.admit(1.7).ok


def test_resume_starts_from_what_the_log_holds(tmp_path):
    log = B.DailySpendLog(tmp_path / B.SPEND_LOG_NAME, clock=Clock())
    log.append({"task_id": "t", "usd": 1.25})
    log.append({"task_id": "x", "usd": 17.0})              # the day is nearly used up
    led = B.BudgetLedger.for_task(S.default_settings(), spend_log=log, task_id="t", resume=True)
    assert led.spent_usd == pytest.approx(1.25)
    with pytest.raises(ValueError):
        B.BudgetLedger.for_task(S.default_settings(), spend_log=log, resume=True)


def test_default_runs_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(tmp_path))
    assert B.default_runs_dir() == tmp_path
    assert B.DailySpendLog().path == tmp_path / B.SPEND_LOG_NAME
    monkeypatch.delenv(B.RUNS_DIR_ENV)
    assert B.default_runs_dir().name == ".tfl_lab_runs"


# ---------------------------------------------------------------------------
# token estimate and usage pricing
# ---------------------------------------------------------------------------

def test_text_tokens_are_conservative_for_cyrillic():
    assert B.text_tokens("") == 0
    assert B.text_tokens("a" * 30) == 10
    assert B.text_tokens("я" * 30) == 20


def _pdf_b64(pages: int) -> str:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return base64.b64encode(buf.getvalue()).decode()


def test_estimate_counts_images_pdf_pages_and_tools():
    pytest.importorskip("pypdf")
    base = B.estimate_input_tokens(FakeRequest([HumanMessage(content="x")]))
    img = B.estimate_input_tokens(FakeRequest([HumanMessage(content=[
        {"type": "image", "base64": "A" * 400_000, "mime_type": "image/png"}])]))
    assert img - base == pytest.approx(B.IMAGE_TOKENS, abs=5)   # base64 is not counted as text
    pdf = B.estimate_input_tokens(FakeRequest([HumanMessage(content=[
        {"type": "file", "base64": _pdf_b64(3), "mime_type": "application/pdf"}])]))
    assert pdf - base == pytest.approx(3 * B.PDF_TOKENS_PER_PAGE, abs=5)
    native = B.estimate_input_tokens(FakeRequest([HumanMessage(content=[
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": "!!not base64!!"}}])]))
    assert native - base == pytest.approx(B.PDF_UNKNOWN_PAGES * B.PDF_TOKENS_PER_PAGE, abs=5)
    with_tools = B.estimate_input_tokens(FakeRequest([HumanMessage(content="x")], tools=[check_membership],
                                                     system="S" * 300))
    assert with_tools > base + 100


def test_usage_counts_take_cache_out_of_input():
    c = B.usage_counts(usage(total_in=1000, out=50, cache_read=600, cache_write=100))
    assert c == {"input_tokens": 300, "output_tokens": 50, "cache_read_input_tokens": 600,
                 "cache_creation_input_tokens": 100}
    assert B.usage_counts(None) is None
    eph = B.usage_counts({"input_tokens": 500, "output_tokens": 1,
                          "input_token_details": {"ephemeral_5m_input_tokens": 200}})
    assert eph["cache_creation_input_tokens"] == 200 and eph["input_tokens"] == 300


def test_usage_cost_prices_cache_separately_and_haiku_long_prompt():
    c = B.usage_counts(usage(total_in=1000, out=50, cache_read=600, cache_write=100))
    assert B.usage_cost_usd(OPUS, c) == pytest.approx(opus_cost(300, 50, 600, 100))
    long = B.usage_counts(usage(total_in=150_000, out=1000))
    hl = config.MODEL_PRICING_LONG_PROMPT[HAIKU]
    assert B.usage_cost_usd(HAIKU, long) == pytest.approx((150_000 * hl["input"] + 1000 * hl["output"]) / 1e6)
    with pytest.raises(config.UnknownModelError):
        B.usage_cost_usd("gpt-4", c)


# ---------------------------------------------------------------------------
# BudgetMiddleware in a real create_agent graph
# ---------------------------------------------------------------------------

def test_call_that_cannot_get_the_floor_is_refused_without_calling_the_model():
    # Opus: even the 4096-token floor costs > $0.08, more than the $0.05 task budget
    model = Scripted(replies=[ai("never")], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(0.05, reserve_for_report_usd=0.01)
    out = make_agent(model, led.root).invoke({"messages": [HumanMessage(content="L = {a^n b^n}")]})
    assert model.calls == []
    last = out["messages"][-1]
    assert isinstance(last, AIMessage) and not last.tool_calls
    assert "refused before it was made" in last.content and "budget_exhausted" in last.content
    nums = last.response_metadata["tfl_budget"]
    assert nums["max_tokens"] == B.MIN_OUTPUT_TOKENS == 4096 and nums["model"] == OPUS
    assert nums["worst_case_usd"] > 0.08 and nums["available_usd"] == pytest.approx(0.05)
    assert f"${nums['worst_case_usd']:.4f}" in last.content and "max_tokens 4096" in last.content
    assert last.response_metadata["stop_reason"] == "budget_exhausted"
    assert led.exhausted and led.spent_usd == 0.0 and led.root.inflight == 0.0


def test_fixed_ceiling_is_refused_when_deriving_is_off():
    # fit_max_tokens=False keeps the old arithmetic: 64000 on Opus ($1.28+) > the $1.00 budget
    model = Scripted(replies=[ai("never")], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(1.0, reserve_for_report_usd=0.3)
    out = make_agent(model, led.root, fit_max_tokens=False).invoke({"messages": [HumanMessage(content="go")]})
    assert model.calls == []
    nums = out["messages"][-1].response_metadata["tfl_budget"]
    assert nums["max_tokens"] == 64000 and nums["worst_case_usd"] > 1.28 and led.exhausted


def test_opus_main_in_a_1_50_task_is_not_exhausted_after_two_calls():
    """Before the 2026-11-08 decision every Opus main call reserved its full
    64000 ceiling ($1.28+): a $1.50 task went to wrap-up on the first call and
    could not afford a second one. With the derived cap the calls go on."""
    calls = [ai("", tool_calls=[{"name": "check_membership", "args": {"word": f"w{i}"}, "id": f"c{i}"}],
                total_in=4000, out=1500) for i in range(4)]
    model = Scripted(replies=[*calls, ai("done", total_in=4000, out=500)], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(1.50, reserve_for_report_usd=0.30)
    out = make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    assert out["messages"][-1].content == "done" and len(model.calls) == 5
    assert not led.exhausted and led.root.state == "normal"
    assert not any("BUDGET WRAP-UP" in str(c["messages"][-1].content) for c in model.calls)
    sent = [c["kwargs"]["max_tokens"] for c in model.calls]
    assert all(B.MIN_OUTPUT_TOKENS <= n < 64000 for n in sent)
    assert sent == sorted(sent, reverse=True)          # the cap shrinks with the remainder
    assert [r.max_tokens for r in led.records] == sent
    assert all(r.worst_case_usd <= 1.20 + 1e-9 for r in led.records)  # never into the reserve
    assert led.spent_usd < 1.50 - 0.30


def test_max_tokens_is_derived_from_the_remaining_budget():
    model = Scripted(replies=[ai("done", total_in=100, out=10)], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(1.0, reserve_for_report_usd=0.3)
    make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    sent = model.calls[0]["kwargs"]["max_tokens"]
    est_in = led.records[0].estimated_input_tokens
    assert sent == config.max_tokens_within(OPUS, est_in, 0.7, 64000)
    assert 4096 <= sent < 64000 and led.records[0].max_tokens == sent
    assert config.worst_case_usd(OPUS, est_in, sent) <= 0.7 + 1e-9
    assert config.worst_case_usd(OPUS, est_in, sent + 1) > 0.7
    assert led.root.state == "normal"
    # a scope that can pay for the ceiling sends the ceiling and no override
    big = Scripted(replies=[ai("done", total_in=100, out=10)], model=OPUS, max_tokens=64000)
    make_agent(big, B.BudgetLedger(10.0, reserve_for_report_usd=0.3).root).invoke(
        {"messages": [HumanMessage(content="go")]})
    assert "max_tokens" not in big.calls[0]["kwargs"]
    # below min_output_tokens it refuses
    tiny = B.BudgetLedger(0.01)
    model2 = Scripted(replies=[ai("x")], model=OPUS, max_tokens=64000)
    out = make_agent(model2, tiny.root).invoke({"messages": [HumanMessage(content="go")]})
    assert model2.calls == [] and "refused" in out["messages"][-1].content
    # a ceiling below the floor is its own floor: sent as is when it fits, never lowered further
    small = Scripted(replies=[ai("done", total_in=10, out=10)], model=OPUS, max_tokens=1000)
    make_agent(small, B.BudgetLedger(0.03).root).invoke({"messages": [HumanMessage(content="go")]})
    assert small.calls and "max_tokens" not in small.calls[0]["kwargs"]
    small2 = Scripted(replies=[ai("x")], model=OPUS, max_tokens=1000)
    out = make_agent(small2, B.BudgetLedger(0.015).root).invoke({"messages": [HumanMessage(content="go")]})
    assert small2.calls == [] and out["messages"][-1].response_metadata["tfl_budget"]["max_tokens"] == 1000


def test_the_reserve_stays_for_the_report_call():
    """Normal calls are capped by what is free ABOVE the reserve; the call
    that no longer gets the floor there enters wrap-up and is capped by the
    remainder WITH the reserve, so the report can still be submitted."""

    @tool
    def submit_report(verdict: str) -> str:
        """Submit the report."""
        return f"report: {verdict}"

    model = Scripted(replies=[
        ai("", tool_calls=[{"name": "check_membership", "args": {"word": "ab"}, "id": "c1"}],
           total_in=1000, out=8000),
        ai("", tool_calls=[{"name": "submit_report", "args": {"verdict": "regular"}, "id": "r1"}],
           total_in=1200, out=3000),
        ai("submitted", total_in=1300, out=10),
    ], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(0.50, reserve_for_report_usd=0.30)
    out = make_agent(model, led.root, tools=(check_membership, submit_report)).invoke(
        {"messages": [HumanMessage(content="go")]})
    first, second = (c["kwargs"]["max_tokens"] for c in model.calls[:2])
    r1, r2 = led.records[:2]
    # call 1: only the $0.20 above the reserve (and the next floor call stays payable)
    assert first == normal_cap(OPUS, r1.estimated_input_tokens, 0.50, 0.30, 64000)
    assert r1.worst_case_usd <= 0.20 + 1e-9
    # call 2: < floor above the reserve -> wrap-up; capped by the remainder with the reserve,
    # leaving one more floor call payable when it can, else everything (the last call)
    avail = 0.50 - r1.usd
    room_above = avail - 0.30
    assert config.max_tokens_within(OPUS, r2.estimated_input_tokens, room_above, 64000) < B.MIN_OUTPUT_TOKENS
    keep = leave_next(OPUS, r2.estimated_input_tokens, avail, 64000)
    assert second == (keep if keep >= B.MIN_OUTPUT_TOKENS
                      else config.max_tokens_within(OPUS, r2.estimated_input_tokens, avail, 64000))
    assert second >= B.MIN_OUTPUT_TOKENS and r2.worst_case_usd > room_above
    assert "BUDGET WRAP-UP" in model.calls[1]["messages"][-1].content
    tool_msgs = {m.tool_call_id: m for m in out["messages"] if isinstance(m, ToolMessage)}
    assert tool_msgs["r1"].content == "report: regular"
    assert led.root.state == "wrap_up" and led.spent_usd <= 0.50


class UsesItsCap(Scripted):
    """A scripted model that always produces its whole max_tokens (the worst case)."""

    def _next(self, messages, kw):
        result = super()._next(messages, kw)
        msg = result.generations[0].message
        cap = kw.get("max_tokens") or self.max_tokens
        msg = msg.model_copy(update={"usage_metadata": usage(total_in=2000, out=int(cap))})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_two_wrap_up_turns_still_reach_the_report():
    """The first wrap-up turn is not the report (it calls check_membership) and uses its whole
    cap; the reserve is held back for one more floor-sized call, so the report turn after it is
    admitted rather than refused (budget_exhausted without a report)."""

    @tool
    def submit_report(verdict: str) -> str:
        """Submit the report."""
        return f"report: {verdict}"

    model = UsesItsCap(replies=[
        ai("", tool_calls=[{"name": "check_membership", "args": {"word": "ab"}, "id": "c1"}]),
        ai("", tool_calls=[{"name": "submit_report", "args": {"verdict": "regular"}, "id": "r1"}]),
        ai("submitted"),
    ], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(0.45, reserve_for_report_usd=0.38)
    out = make_agent(model, led.root, tools=(check_membership, submit_report)).invoke(
        {"messages": [HumanMessage(content="go")]})
    r1, r2 = led.records[:2]
    # call 1 is already in wrap-up (< floor above the reserve) and is NOT given all $0.45
    assert "BUDGET WRAP-UP" in model.calls[0]["messages"][-1].content
    assert r1.max_tokens < config.max_tokens_within(OPUS, r1.estimated_input_tokens, 0.45, 64000)
    assert r1.max_tokens == leave_next(OPUS, r1.estimated_input_tokens, 0.45, 64000) >= B.MIN_OUTPUT_TOKENS
    # call 2, the report, still gets at least the floor
    assert r2.outcome == "ok" and r2.max_tokens >= B.MIN_OUTPUT_TOKENS
    tool_msgs = {m.tool_call_id: m for m in out["messages"] if isinstance(m, ToolMessage)}
    assert tool_msgs["c1"].content == "ab: yes" and tool_msgs["r1"].content == "report: regular"
    assert led.spent_usd <= 0.45 + 1e-9


def test_a_long_prompt_still_leaves_the_report_call_payable():
    """Opus, an 80k-token late-run prompt: its input alone costs more than the $0.30 reserve.
    The normal call is capped so that a floor-sized call with the grown prompt still fits
    after it; the next call (prompt + this output + margin) is admitted at the floor or more."""
    led = B.BudgetLedger(1.20, reserve_for_report_usd=0.30)
    mw = B.BudgetMiddleware(led.root, role="main", model=OPUS, max_tokens=64000)
    n = 80_000
    assert config.worst_case_usd(OPUS, n, 0) > 0.30                 # the reserve alone cannot pay the prompt
    est = B.CallEstimate(OPUS, n, 64000, config.worst_case_usd(OPUS, n, 64000))
    with led.lock:
        first, enter = mw.derive(est, led.root)
        adm = led.root._admit_locked(first.worst_case_usd, enter_wrap_up=enter)
    assert adm.ok and not enter and first.max_tokens >= B.MIN_OUTPUT_TOKENS
    # the model uses its whole cap: the worst case is charged
    led.root.settle(adm.reservation, B.CallRecord(node="task", role="main", model=OPUS,
                                                  usd=first.worst_case_usd, worst_case_usd=first.worst_case_usd))
    n2 = n + first.max_tokens + B.NEXT_CALL_MARGIN_TOKENS
    nxt = B.CallEstimate(OPUS, n2, 64000, config.worst_case_usd(OPUS, n2, 64000))
    with led.lock:
        report, _ = mw.derive(nxt, led.root)
        adm2 = led.root._admit_locked(report.worst_case_usd)
    assert adm2.ok and report.max_tokens >= B.MIN_OUTPUT_TOKENS
    assert led.root.in_wrap_up()


def test_child_scope_caps_come_from_its_own_allocation():
    led = B.BudgetLedger(10.0, reserve_for_report_usd=0.3)
    child = led.root.allocate("sub", 0.40, reserve_usd=0.10)
    model = Scripted(replies=[ai("answer", total_in=100, out=10)], model=OPUS, max_tokens=64000)
    mw = B.BudgetMiddleware(led.root, role="prover")  # built over the root, charged to the active scope
    graph = create_agent(model, tools=[check_membership], middleware=[mw])
    with B.enter_scope(child):
        graph.invoke({"messages": [HumanMessage(content="go")]})
    sent = model.calls[0]["kwargs"]["max_tokens"]
    rec = led.records[0]
    assert rec.node == "task/sub"
    assert sent == normal_cap(OPUS, rec.estimated_input_tokens, 0.40, 0.10, 64000) < 64000
    # the root itself could have paid for the full ceiling
    root_model = Scripted(replies=[ai("answer", total_in=100, out=10)], model=OPUS, max_tokens=64000)
    create_agent(root_model, tools=[check_membership], middleware=[mw]).invoke(
        {"messages": [HumanMessage(content="go")]})
    assert "max_tokens" not in root_model.calls[0]["kwargs"]


def test_parallel_branches_get_caps_from_their_own_allocations():
    led = B.BudgetLedger(10.0, reserve_for_report_usd=0.3, per_subagent_max_usd=2.0)
    wf = led.root.allocate("workflow:w", 1.0, kind="workflow")
    branches = [wf.allocate(f"b{i}", 0.30, kind="branch", reserve_usd=0.05) for i in range(3)]
    results: dict[str, int] = {}

    def run(node):
        model = Scripted(replies=[ai("ok", total_in=100, out=10)], model=OPUS, max_tokens=64000, delay=0.05)
        create_agent(model, tools=[], middleware=[B.BudgetMiddleware(node, role="workflow:x")]).invoke(
            {"messages": [HumanMessage(content="go")]})
        results[node.name] = model.calls[0]["kwargs"]["max_tokens"]

    threads = [threading.Thread(target=run, args=(b,)) for b in branches]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3
    for rec in led.records:
        assert results[rec.node.rsplit("/", 1)[1]] == normal_cap(
            OPUS, rec.estimated_input_tokens, 0.30, 0.05, 64000)
    assert not led.exhausted and led.root.inflight == 0.0


def test_actual_usage_is_charged_with_cache_tokens():
    model = Scripted(replies=[
        ai("", tool_calls=[{"name": "check_membership", "args": {"word": "ab"}, "id": "c1"}],
           total_in=2000, out=300, cache_read=1500, cache_write=200),
        ai("done", total_in=2500, out=100, cache_read=1800, cache_write=0),
    ], model=OPUS, max_tokens=2000)
    led = B.BudgetLedger(5.0)
    out = make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    assert out["messages"][-1].content == "done" and len(model.calls) == 2
    expected = opus_cost(300, 300, 1500, 200) + opus_cost(700, 100, 1800, 0)
    assert led.spent_usd == pytest.approx(expected)
    r = led.records[0]
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens) == (300, 300, 1500, 200)
    assert r.role == "main" and r.max_tokens == 2000 and r.worst_case_usd >= r.usd
    assert led.root.inflight == 0.0


def test_response_model_name_is_used_for_pricing():
    reply = ai("done", total_in=1000, out=100)
    reply.response_metadata = {"model_name": "claude-sonnet-5-5-20260928"}
    model = Scripted(replies=[reply], model=OPUS, max_tokens=1000)
    led = B.BudgetLedger(5.0)
    make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    assert led.records[0].model == "claude-sonnet-5-5-20260928"
    sc = config.MODEL_PRICING[SONNET]
    assert led.spent_usd == pytest.approx((1000 * sc["input"] + 100 * sc["output"]) / 1e6)


def test_wrap_up_injects_the_instruction_and_blocks_spawning_tools():
    # budget 0.10, reserve 0.099: $0.001 above the reserve does not pay for the 4096-token
    # floor on Haiku ($0.002+), so the first call enters wrap-up and may use the reserve
    model = Scripted(replies=[
        ai("", tool_calls=[{"name": "spawn_subagent", "args": {"name": "x"}, "id": "s1"},
                           {"name": "check_membership", "args": {"word": "ab"}, "id": "m1"}],
           total_in=100, out=50),
        ai("report submitted", total_in=200, out=50),
    ], model=HAIKU, max_tokens=60000)
    led = B.BudgetLedger(0.10, reserve_for_report_usd=0.099)
    out = make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    # first call already did not fit without the reserve -> wrap-up from the start
    first = model.calls[0]["messages"]
    assert "BUDGET WRAP-UP" in first[-1].content and "submit_report" in first[-1].content
    assert led.root.state == "wrap_up"
    tool_msgs = {m.tool_call_id: m for m in out["messages"] if isinstance(m, ToolMessage)}
    assert tool_msgs["s1"].status == "error" and "wrap-up" in tool_msgs["s1"].content
    assert tool_msgs["m1"].content == "ab: yes"            # other tools still run
    assert out["messages"][-1].content == "report submitted"
    # the instruction is per request only, never stored in the conversation
    assert not any("BUDGET WRAP-UP" in str(m.content) for m in out["messages"])


def test_child_scope_gets_the_child_instruction():
    led = B.BudgetLedger(1.0)
    child = led.root.allocate("sub", 0.05, reserve_usd=0.049)  # < the Haiku floor above the reserve
    model = Scripted(replies=[ai("answer", total_in=10, out=10)], model=HAIKU, max_tokens=80_000)
    make_agent(model, child).invoke({"messages": [HumanMessage(content="go")]})
    assert "return your final answer to the caller" in model.calls[0]["messages"][-1].content


def test_missing_usage_is_charged_at_worst_case_or_zero():
    def run(policy):
        model = Scripted(replies=[AIMessage(content="done")], model=SONNET, max_tokens=1000)
        led = B.BudgetLedger(5.0)
        make_agent(model, led.root, missing_usage=policy).invoke({"messages": [HumanMessage(content="go")]})
        return led

    led = run("worst_case")
    assert led.records[0].usage_missing and led.spent_usd == pytest.approx(led.records[0].worst_case_usd)
    assert led.spent_usd > 1000 * 10 / 1e6
    assert run("zero").spent_usd == 0.0
    with pytest.raises(ValueError):
        B.BudgetMiddleware(B.BudgetLedger(1.0).root, missing_usage="maybe")


def test_fit_max_tokens_lowers_the_ceiling_when_enabled():
    model = Scripted(replies=[ai("done", total_in=100, out=10)], model=OPUS, max_tokens=64000)
    led = B.BudgetLedger(1.0, reserve_for_report_usd=0.3)
    make_agent(model, led.root, fit_max_tokens=True).invoke({"messages": [HumanMessage(content="go")]})
    sent = model.calls[0]["kwargs"]["max_tokens"]
    assert 4096 <= sent < 64000
    assert config.worst_case_usd(OPUS, led.records[0].estimated_input_tokens, sent) <= 0.7 + 1e-9
    assert led.root.state == "normal"
    # below min_output_tokens it refuses as usual
    tiny = B.BudgetLedger(0.01)
    model2 = Scripted(replies=[ai("x")], model=OPUS, max_tokens=64000)
    out = make_agent(model2, tiny.root, fit_max_tokens=True).invoke({"messages": [HumanMessage(content="go")]})
    assert model2.calls == [] and "refused" in out["messages"][-1].content


def test_a_failing_call_releases_its_reservation():
    model = Scripted(replies=[RuntimeError("API down")], model=HAIKU, max_tokens=1000)
    led = B.BudgetLedger(1.0)
    with pytest.raises(RuntimeError, match="API down"):
        make_agent(model, led.root).invoke({"messages": [HumanMessage(content="go")]})
    assert led.root.inflight == 0.0 and led.spent_usd == 0.0


def test_async_path():
    model = Scripted(replies=[
        ai("", tool_calls=[{"name": "check_membership", "args": {"word": "a"}, "id": "c1"}], total_in=50, out=5),
        ai("done", total_in=60, out=5),
    ], model=HAIKU, max_tokens=1000)
    led = B.BudgetLedger(1.0)
    out = asyncio.run(make_agent(model, led.root).ainvoke({"messages": [HumanMessage(content="go")]}))
    assert out["messages"][-1].content == "done"
    assert len(led.records) == 2 and led.root.inflight == 0.0


def test_async_refusal_and_blocked_tool():
    led = B.BudgetLedger(0.001)
    model = Scripted(replies=[ai("x")], model=OPUS, max_tokens=64000)
    out = asyncio.run(make_agent(model, led.root).ainvoke({"messages": [HumanMessage(content="go")]}))
    assert model.calls == [] and out["messages"][-1].response_metadata["stop_reason"] == "budget_exhausted"

    led2 = B.BudgetLedger(0.10, reserve_for_report_usd=0.099)
    model2 = Scripted(replies=[
        ai("", tool_calls=[{"name": "spawn_subagent", "args": {"name": "x"}, "id": "s1"}], total_in=10, out=5),
        ai("report", total_in=10, out=5),
    ], model=HAIKU, max_tokens=60000)
    out2 = asyncio.run(make_agent(model2, led2.root).ainvoke({"messages": [HumanMessage(content="go")]}))
    blocked = [m for m in out2["messages"] if isinstance(m, ToolMessage)][0]
    assert blocked.status == "error" and "wrap-up" in blocked.content


def test_estimate_scales_up_after_an_underestimate():
    model = Scripted(replies=[
        ai("", tool_calls=[{"name": "check_membership", "args": {"word": "a"}, "id": "c1"}],
           total_in=50_000, out=5),
        ai("done", total_in=50_000, out=5),
    ], model=HAIKU, max_tokens=1000)
    led = B.BudgetLedger(1.0)
    mw = B.BudgetMiddleware(led.root, role="main")
    create_agent(model, tools=[check_membership], middleware=[mw]).invoke(
        {"messages": [HumanMessage(content="go")]})
    first, second = led.records
    assert mw._scale > 1.0
    assert second.estimated_input_tokens > first.estimated_input_tokens * 2


def test_fallback_model_and_max_tokens_for_models_without_them():
    model = Scripted(replies=[ai("done", total_in=10, out=10)])
    led = B.BudgetLedger(5.0)
    make_agent(model, led.root, model="sonnet", max_tokens=123).invoke({"messages": [HumanMessage(content="go")]})
    assert led.records[0].model == SONNET and led.records[0].max_tokens == 123
    # no fallback: role -> config defaults (main: Opus, 64000)
    led2 = B.BudgetLedger(5.0)
    make_agent(Scripted(replies=[ai("d", total_in=1, out=1)]), led2.root).invoke(
        {"messages": [HumanMessage(content="go")]})
    assert (led2.records[0].model, led2.records[0].max_tokens) == (OPUS, config.MAX_TOKENS)


def test_unpriced_fallback_model_fails_closed():
    with pytest.raises(config.UnknownModelError):
        B.BudgetMiddleware(B.BudgetLedger(1.0).root, model="claude-opus-9")


def test_model_override_env_is_priced(monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", HAIKU)
    led = B.BudgetLedger(5.0)
    make_agent(Scripted(replies=[ai("d", total_in=1, out=1)]), led.root).invoke(
        {"messages": [HumanMessage(content="go")]})
    assert led.records[0].model == HAIKU


def test_parallel_branches_share_one_parent_budget():
    """Three scopes under one workflow allocation, run in threads: the sum of
    the actual spend never exceeds the allocation."""
    led = B.BudgetLedger(5.0, per_subagent_max_usd=2.0)
    wf = led.root.allocate("wf", 0.3, kind="workflow")
    branches = [wf.allocate(f"b{i}", 0.1, kind="branch") for i in range(3)]
    results = []

    def run(node):
        model = Scripted(replies=[ai("ok", total_in=100, out=100)], model=HAIKU, max_tokens=150_000, delay=0.01)
        out = make_agent(model, node).invoke({"messages": [HumanMessage(content="go")]})
        results.append(out["messages"][-1].content)

    ts = [threading.Thread(target=run, args=(b,)) for b in branches]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == ["ok"] * 3
    assert led.spent_usd <= 0.3 and led.root.inflight == 0.0
    assert all(b.spent <= b.limit for b in branches)


# ---------------------------------------------------------------------------
# ConcurrencyMiddleware
# ---------------------------------------------------------------------------

def test_default_semaphore_is_the_pipelines_one():
    from agent_system.lib import llm_client

    assert ConcurrencyMiddleware().semaphore is llm_client.get_concurrency_semaphore()


def test_sync_calls_are_bounded():
    sem = threading.Semaphore(1)
    mw = ConcurrencyMiddleware(sem)
    active, peak, lock = [0], [0], threading.Lock()

    def handler(_req):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return "r"

    ts = [threading.Thread(target=mw.wrap_model_call, args=(None, handler)) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1
    assert sem.acquire(blocking=False)                       # released afterwards


def test_async_calls_are_bounded_and_do_not_block_the_loop():
    sem = threading.Semaphore(2)
    mw = ConcurrencyMiddleware(sem)
    active, peak, ticks = [0], [0], [0]

    async def handler(_req):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.02)
        active[0] -= 1
        return "r"

    async def ticker():
        for _ in range(10):
            ticks[0] += 1
            await asyncio.sleep(0.005)

    async def main():
        await asyncio.gather(ticker(), *(mw.awrap_model_call(None, handler) for _ in range(6)))

    asyncio.run(main())
    assert peak[0] == 2 and ticks[0] == 10
    assert sem.acquire(blocking=False) and sem.acquire(blocking=False)


def test_async_waiter_cancellation_leaks_no_slot():
    sem = threading.Semaphore(1)

    async def main():
        sem.acquire()
        waiter = asyncio.ensure_future(acquire_async(sem))
        await asyncio.sleep(0.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        sem.release()

    asyncio.run(main())
    assert sem.acquire(blocking=False)


def test_handler_errors_release_the_slot():
    sem = threading.Semaphore(1)
    mw = ConcurrencyMiddleware(sem)

    def bad(_req):
        raise RuntimeError("x")

    async def abad(_req):
        raise RuntimeError("y")

    with pytest.raises(RuntimeError):
        mw.wrap_model_call(None, bad)
    with pytest.raises(RuntimeError):
        asyncio.run(mw.awrap_model_call(None, abad))
    assert sem.acquire(blocking=False)
    with pytest.raises(ValueError):
        ConcurrencyMiddleware(sem, semaphore_factory=lambda: sem)


def test_both_middlewares_in_one_agent():
    model = Scripted(replies=[ai("done", total_in=10, out=10)], model=HAIKU, max_tokens=100)
    led = B.BudgetLedger(1.0)
    agent = create_agent(model, tools=[check_membership], middleware=[
        B.BudgetMiddleware(led.root, role="main"), ConcurrencyMiddleware(threading.Semaphore(1))])
    assert agent.invoke({"messages": [HumanMessage(content="go")]})["messages"][-1].content == "done"
    assert len(led.records) == 1


# ---------------------------------------------------------------------------
# make_middleware hooks (agent.default_middleware)
# ---------------------------------------------------------------------------

def test_budget_hook_needs_a_scope():
    assert B.make_middleware(S.default_settings(), role="main") is None
    assert B.current_budget() is None
    led = B.BudgetLedger(1.0)
    with B.use_budget(led, missing_usage="zero") as node:
        assert node is led.root and B.current_budget() is led.root
        mw = B.make_middleware(S.default_settings().to_dict(), role="critic")
        assert isinstance(mw, B.BudgetMiddleware)
        assert (mw.node, mw.role, mw.missing_usage, mw.model) == (led.root, "critic", "zero", OPUS)
        child = led.root.allocate("sub", 0.5)
        with B.use_budget(child):
            assert B.make_middleware(None, role="prover").node is child
        assert B.make_middleware(None, role="x", node=child).node is child
    assert B.current_budget() is None


@pytest.mark.parametrize("role", [*config.MODELS, "general", "spawn:helper", "workflow:step"])
def test_every_hooked_role_derives_max_tokens(role):
    led = B.BudgetLedger(1.0)
    with B.use_budget(led):
        mw = B.make_middleware(S.default_settings(), role=role)
    assert mw.fit_max_tokens is True and mw.min_output_tokens == B.MIN_OUTPUT_TOKENS
    from tfl_deep_agent.orchestration import spawn

    assert spawn.MIN_OUTPUT_TOKENS == B.MIN_OUTPUT_TOKENS  # one floor for fixed roles and ad-hoc children


def test_derived_max_tokens_reaches_the_wire_with_adaptive_thinking(wire):
    """The lowered cap goes into the request body as max_tokens; the
    request keeps adaptive thinking, the role's effort and the refusal
    fallback (adaptive thinking has no budget_tokens, so any max_tokens >= 1
    is a valid request; the floor is ours)."""
    pytest.importorskip("langchain_anthropic")
    from tfl_deep_agent.models import make_chat_model

    led = B.BudgetLedger(1.50, reserve_for_report_usd=0.30)
    graph = create_agent(make_chat_model(role="main", api_key="test"), tools=[check_membership],
                         middleware=[B.BudgetMiddleware(led.root, role="main")])
    graph.invoke({"messages": [HumanMessage(content="hi")]})
    b = wire.last.body
    rec = led.records[0]
    assert b["model"] == OPUS and b["max_tokens"] == rec.max_tokens
    assert B.MIN_OUTPUT_TOKENS <= b["max_tokens"] < config.MAX_TOKENS
    assert b["max_tokens"] == normal_cap(OPUS, rec.estimated_input_tokens, 1.50, 0.30, config.MAX_TOKENS)
    assert b["thinking"] == {"type": "adaptive"} and "budget_tokens" not in b["thinking"]
    assert b["output_config"] == {"effort": "high"} and b["stream"] is True
    assert "temperature" not in b and b["fallbacks"] == "default"
    assert rec.usd > 0 and not led.exhausted


def test_budget_hook_role_model_follows_settings_and_override(monkeypatch):
    st = S.validate_settings({"models": {"roles": {"critic": "sonnet"}}})
    led = B.BudgetLedger(1.0)
    with B.use_budget(led):
        assert B.make_middleware(st, role="critic").model == SONNET
        monkeypatch.setenv("TFL_MODEL_OVERRIDE", "haiku")
        assert B.make_middleware(st, role="critic").model == HAIKU


def test_hooked_agent_charges_the_scope():
    led = B.BudgetLedger(1.0)
    with B.use_budget(led):
        mws = [B.make_middleware(S.default_settings(), role="main"),
               concurrency_hook(None, role="main")]
    agent = create_agent(Scripted(replies=[ai("done", total_in=10, out=10)], model=HAIKU, max_tokens=100),
                         tools=[check_membership], middleware=mws)
    agent.invoke({"messages": [HumanMessage(content="go")]})
    assert len(led.records) == 1 and led.records[0].role == "main"
    assert isinstance(mws[1], ConcurrencyMiddleware)
