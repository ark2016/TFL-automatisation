"""Follow-up messages to a finished run (followup.py) and the budget stop
(budget_stop.py). Scripted models only. Each call builds its own agent on the run's
SQLite checkpoints, as a new process does; test_cli_* use real processes."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from tfl_deep_agent import agent as A  # noqa: E402
from tfl_deep_agent import budget_stop as BS  # noqa: E402
from tfl_deep_agent import followup, hitl, run_store  # noqa: E402
from tfl_deep_agent.evidence.ledger import EvidenceLedger  # noqa: E402
from tfl_deep_agent.middleware import budget as B  # noqa: E402
from tfl_deep_agent.progress import ProgressEmitter  # noqa: E402
from tfl_deep_agent.tools.ir import ir_fingerprint  # noqa: E402

from . import hitl_scripts as H  # noqa: E402

ANSWER = "Потому что ни одна ячейка таблицы не содержит двух правил."
QUESTION = "Почему грамматика LL(1)?"
TASK = H.base_script()["task"]
APPROVE = hitl.parse_decision("approve")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(tmp_path / "runs"))


def start(tmp_path, script, *, name="run", settings=None, **kw):
    return hitl.start_run(tmp_path / name, TASK, settings=settings or H.example_settings(), mock_script=script, **kw)


def finished(tmp_path, *extra_replies, name="run"):
    """A run reviewed by a human up to its accepted report, with ``extra_replies``
    of the main agent left for its follow-ups."""
    script = H.base_script()
    script["models"]["main"] += list(extra_replies)
    out = start(tmp_path, script, name=name)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.status == "submitted", out.result
    return out.run_dir


def events(run_dir) -> list[dict]:
    return [json.loads(x) for x in (run_dir / "progress.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def kinds(run_dir) -> list[str]:
    return [e["event"] for e in events(run_dir)]


def humans(run_dir) -> list[str]:
    return [str(m.content) for m in H.thread_messages(run_dir) if isinstance(m, HumanMessage)]


def report_bytes(run_dir) -> dict[str, bytes]:
    return {name: (run_dir / name).read_bytes() for name in run_store.RESULT_FILES}


def ai_tail(run_dir) -> AIMessage:
    return [m for m in H.thread_messages(run_dir) if isinstance(m, AIMessage)][-1]


# ---------------------------------------------------------------------------
# A text reply
# ---------------------------------------------------------------------------

def test_a_text_reply_keeps_the_report_and_the_status(tmp_path):
    d = finished(tmp_path, {"content": ANSWER})
    before = hitl.load_run(d)
    files = report_bytes(d)
    out = hitl.send_followup(d, QUESTION)
    assert out.status == "submitted" and out.exit_code == hitl.EXIT_SUBMITTED
    assert out.reply == ANSWER and out.followup_error is None
    run = hitl.load_run(d)
    assert "followup_in_progress" not in run and run["status"] == "submitted"
    assert run["report_revision"] == before["report_revision"]
    assert run["result"]["report_digest"] == before["result"]["report_digest"]
    assert run["stable_checkpoint_id"] != before["stable_checkpoint_id"]
    assert (d / run_store.REPORT_HISTORY_DIR / "1" / "result.md").read_bytes() == files["result.md"]
    assert QUESTION in humans(d)
    # the same report: a message and its answer in the chat, no second final card
    tail = events(d)[-4:]
    assert [e["event"] for e in tail if e["event"] in ("user_message", "assistant_message", "done")] == \
        ["user_message", "assistant_message"]
    said = {e["event"]: e.get("text") for e in events(d) if e["event"] in ("user_message", "assistant_message")}
    assert said == {"user_message": QUESTION, "assistant_message": ANSWER}
    assert kinds(d).count("done") == 1


def test_the_second_message_sees_the_first_exchange(tmp_path):
    d = finished(tmp_path, {"content": "Ответ один."}, {"content": "Ответ два."})
    hitl.send_followup(d, "Вопрос один?")
    assert hitl.send_followup(d, "Вопрос два?").reply == "Ответ два."
    assert [h for h in humans(d) if h.startswith("Вопрос")] == ["Вопрос один?", "Вопрос два?"]
    assert sorted(p.name for p in (d / run_store.REPORT_HISTORY_DIR).iterdir()) == ["1", "2"]


def test_a_run_without_a_report_can_be_continued_and_says_so_again(tmp_path):
    script = {"task": TASK, "template": "short_verdict",
              "models": {"main": [{"content": "Не могу решить."}, {"content": "Всё ещё не могу."}]}}
    out = start(tmp_path, script, auto_approve=True)
    assert out.status == "incomplete"
    out = hitl.send_followup(out.run_dir, "Попробуй ещё раз")
    assert out.status == "incomplete" and out.reply == "Всё ещё не могу."
    assert kinds(out.run_dir).count("done") == 2  # not submitted: the card says so again


# ---------------------------------------------------------------------------
# A new report
# ---------------------------------------------------------------------------

def test_a_new_report_replaces_the_old_one_through_hitl_2(tmp_path):
    new = H.report_call(H.base_script(), call_id="call-report-2", summary="Отчёт после вопроса в чате.")
    d = finished(tmp_path, new)
    old = report_bytes(d)
    out = hitl.send_followup(d, "Перепиши сводку")
    assert out.status == hitl.STATUS_AWAITING and out.pending.point == hitl.REPORT
    run = hitl.load_run(d)
    assert run["followup_in_progress"]["previous_status"] == "submitted"  # survives the stop
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted"
    run = hitl.load_run(d)
    assert "followup_in_progress" not in run and run["report_revision"] == 2
    assert report_bytes(d)["result.json"] != old["result.json"]
    assert json.loads((d / "result.json").read_text(encoding="utf-8"))["report"]["summary"] == \
        "Отчёт после вопроса в чате."
    assert (d / run_store.REPORT_HISTORY_DIR / "1" / "result.json").read_bytes() == old["result.json"]
    assert kinds(d).count("done") == 2


def test_the_lean_request_reaches_the_agent_without_a_lean_tool_in_the_script(tmp_path):
    reply = "Доказательство не прошло: lean_check недоступен. Ответ и отчёт без изменений."
    d = finished(tmp_path, {"content": reply})
    out = hitl.send_followup(d, followup.LEAN_REQUEST)
    assert out.reply == reply and out.status == "submitted"
    assert followup.LEAN_REQUEST in humans(d)
    assert "lean-formalizer" in followup.LEAN_REQUEST and "lean_check" in followup.LEAN_REQUEST


# ---------------------------------------------------------------------------
# What goes wrong
# ---------------------------------------------------------------------------

def test_an_error_restores_the_report_and_the_next_message_works(tmp_path):
    d = finished(tmp_path)  # no reply left: the follow-up fails with a ScriptError
    before = hitl.load_run(d)
    files = report_bytes(d)
    out = hitl.send_followup(d, "Провальный вопрос")
    assert out.exit_code == hitl.EXIT_NO_REPORT and "mock script" in out.followup_error
    assert out.status == "submitted" and out.reply is None
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and run["result"] == before["result"]
    assert "mock script" in run["followup_error"] and "followup_in_progress" not in run
    assert report_bytes(d) == files
    assert kinds(d)[-2:] == ["followup_abandoned", "segment_end"] and kinds(d).count("done") == 1
    assert not [e for e in events(d) if e["event"] == "segment_end" and e.get("error")]  # no second red line
    assert events(d)[-1]["status"] == "submitted" and "mock script" in events(d)[-2]["error"]
    # the next message starts from the finished state: the failed one is not in its conversation
    script = json.loads((d / hitl.MOCK_SCRIPT_FILE).read_text(encoding="utf-8"))
    script["models"]["main"].append({"content": ANSWER})
    (d / hitl.MOCK_SCRIPT_FILE).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER
    assert "followup_error" not in hitl.load_run(d)
    assert QUESTION in humans(d) and "Провальный вопрос" not in humans(d)


def test_cancel_restores_and_the_next_message_starts_from_the_stable_checkpoint(tmp_path):
    new = H.report_call(H.base_script(), call_id="call-report-2", summary="Отчёт, который отменят.")
    d = finished(tmp_path, new, {"content": ANSWER})
    before = hitl.load_run(d)
    files = report_bytes(d)
    # the follow-up stops at HITL 2: a draft over result.*, a pending interrupt, a tool call without a result
    assert hitl.send_followup(d, "Перепиши").pending.point == hitl.REPORT
    assert report_bytes(d) != files
    assert followup.abandon_followup(d) == "submitted"
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and run["pending"] is None and "followup_in_progress" not in run
    assert run["result"] == before["result"] and run["reviews"] == before["reviews"]
    assert report_bytes(d) == files
    assert kinds(d)[-2:] == ["followup_abandoned", "segment_end"] and events(d)[-2]["error"] is None
    assert followup.abandon_followup(d) is None  # nothing in progress any more
    # the abandoned branch does not poison the next message
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER and out.status == "submitted"
    messages = H.thread_messages(d)
    assert "Перепиши" not in [str(m.content) for m in messages if isinstance(m, HumanMessage)]
    assert not any(tc.get("id") == "call-report-2" for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls)


def test_a_killed_follow_up_leaves_a_running_record_that_abandon_restores(tmp_path):
    d = finished(tmp_path, {"content": ANSWER})
    files = report_bytes(d)
    half_finished(d)
    assert followup.abandon_followup(d) == "submitted"
    assert report_bytes(d) == files
    assert hitl.send_followup(d, QUESTION).reply == ANSWER


def test_what_cannot_be_continued(tmp_path):
    waiting = start(tmp_path, H.base_script(), name="waiting").run_dir
    with pytest.raises(hitl.RunError, match="waiting at a stop"):
        hitl.send_followup(waiting, QUESTION)
    d = finished(tmp_path, {"content": ANSWER}, name="done")
    for empty in ("", "   \n"):
        with pytest.raises(hitl.RunError, match="needs text"):
            hitl.send_followup(d, empty)
    with pytest.raises(hitl.RunError, match="does not apply"):
        hitl.send_followup(d, QUESTION, live=True)
    run = hitl.load_run(d)
    run["status"] = "running"
    hitl.save_run(d, run)
    with pytest.raises(hitl.RunError, match="'running'"):
        hitl.send_followup(d, QUESTION)
    run["status"] = "submitted"
    hitl.save_run(d, run)
    (d / hitl.CHECKPOINT_FILE).unlink()
    with pytest.raises(hitl.RunError, match="no conversation"):
        hitl.send_followup(d, QUESTION)
    assert not (d / run_store.REPORT_HISTORY_DIR).exists()  # refused before anything was touched


def live_factory(script, tmp_path):
    """What ``--live`` gives one process: a live factory whose replies go on from
    where the previous process stopped. Build a new one for each hitl call."""
    scripted = A.scripted_model_factory(script, state_path=tmp_path / "cursors.json")

    def factory(role_key, model_id):
        return scripted(role_key, model_id)

    factory.tfl_live = True
    return factory


def test_a_live_run_is_continued_only_with_live_and_its_factory(tmp_path):
    script = H.base_script()
    script["models"]["main"].append({"content": ANSWER})

    def live():
        return live_factory(script, tmp_path)

    out = hitl.start_run(tmp_path / "lv", TASK, settings=H.example_settings(), model_factory=live())
    out = hitl.resume_run(out.run_dir, APPROVE, model_factory=live(), live=True)
    out = hitl.resume_run(out.run_dir, APPROVE, model_factory=live(), live=True)
    assert out.status == "submitted"
    with pytest.raises(hitl.RunError, match="continuing it makes real API calls"):
        hitl.send_followup(out.run_dir, QUESTION, model_factory=live())
    with pytest.raises(hitl.RunError, match="custom model factory"):
        hitl.send_followup(out.run_dir, QUESTION, live=True)
    assert hitl.send_followup(out.run_dir, QUESTION, model_factory=live(), live=True).reply == ANSWER


# ---------------------------------------------------------------------------
# The CLI, in new processes
# ---------------------------------------------------------------------------

def test_cli_message_in_a_new_process(tmp_path):
    script = H.base_script()
    script["models"]["main"].append({"content": ANSWER})
    path = H.write_script(tmp_path, script)
    env = H.subprocess_env(tmp_path / "runs")
    d = tmp_path / "cli"
    r = H.tfl_deep("--mock-script", str(path), "--save", str(d), "--settings", str(H.SETTINGS_EXAMPLE),
                   "--auto-approve", env=env)
    assert r.returncode == hitl.EXIT_SUBMITTED, r.stderr
    question = tmp_path / "q.txt"
    question.write_text(QUESTION, encoding="utf-8")
    r = H.tfl_deep("--resume", str(d), "--message", f"@{question}", "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_SUBMITTED, r.stderr
    assert ANSWER in r.stdout
    assert QUESTION in humans(d)
    # exhausted script: the follow-up fails, exit 1, the previous report stays
    r = H.tfl_deep("--resume", str(d), "--message", "Ещё вопрос", "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_NO_REPORT and "previous report" in r.stderr
    assert hitl.load_run(d)["status"] == "submitted"


def test_cli_message_excludes_a_decision(tmp_path):
    env = H.subprocess_env(tmp_path / "runs")
    r = H.tfl_deep("--resume", str(tmp_path), "--message", "x", "--decision", "approve", env=env)
    assert r.returncode == 2 and "--message excludes" in r.stderr
    r = H.tfl_deep("--message", "x", "--mock-script", "y.json", env=env)
    assert r.returncode == 2 and "--resume RUN_DIR" in r.stderr


# ---------------------------------------------------------------------------
# The budget stop
# ---------------------------------------------------------------------------

def low_settings(per_task=0.01, reserve=0.001):
    settings = H.example_settings()
    settings["budget"].update({"per_task_usd": per_task, "reserve_for_report_usd": reserve})
    return settings


def no_review_settings(**kw):
    """HITL off: the only stop of the run is the money one."""
    settings = low_settings(**kw)
    settings["hitl"].update({"formalization": False, "report": False})
    return settings


def budget_stop(tmp_path, *, name="run", **kw):
    out = start(tmp_path, H.base_script(), name=name, settings=no_review_settings(), **kw)
    assert out.status == hitl.STATUS_AWAITING and out.pending.point == hitl.BUDGET, out.result
    return out


def test_the_run_stops_for_money_with_a_short_card(tmp_path):
    out = budget_stop(tmp_path)
    args = out.pending.request["action_requests"][0]["args"]
    assert set(args) == {"spent_usd", "limit_usd", "available_usd", "reserve_usd", "day_left_usd"}
    assert args["limit_usd"] == 0.01 and args["day_left_usd"] is None
    assert out.pending.request["review_configs"][0]["allowed_decisions"] == ["approve", "edit"]
    card = hitl.load_run(out.run_dir)["pending"]["card"]
    assert card["point"] == hitl.BUDGET and card["editable"] == ["add_usd"]
    assert card["title"] == "Бюджет задачи заканчивается"
    assert card["summary"][0].startswith("Потрачено $")
    assert "```json" not in card["details_md"]
    assert "add_usd" in hitl.resume_hint(out.run_dir, hitl.BUDGET)


def test_added_money_holds_in_a_new_process_and_is_in_the_report(tmp_path):
    out = budget_stop(tmp_path)
    d = out.run_dir
    per_task = hitl.load_run(d)["settings"]["budget"]["per_task_usd"]
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 5}))
    assert out.status == "submitted", out.result
    run = hitl.load_run(d)
    assert run["budget_limit_usd"] == pytest.approx(5.01)
    assert run["settings"]["budget"]["per_task_usd"] == per_task  # the settings snapshot does not change
    assert [r["point"] for r in run["reviews"]] == [hitl.BUDGET]
    review = run["reviews"][0]
    assert review["decision"] == "extended" and review["add_usd"] == 5 and review["new_limit_usd"] == pytest.approx(5.01)
    assert [e["add_usd"] for e in run["budget_extensions"]] == [5]
    assert out.result["budget"]["limit_usd"] == pytest.approx(5.01)
    assert out.result["budget"]["extensions"][0]["new_limit_usd"] == pytest.approx(5.01)
    assert "Бюджет добавлен" in (d / "result.md").read_text(encoding="utf-8")
    decision = next(e for e in events(d) if e["event"] == "hitl_decision")
    assert decision["add_usd"] == 5 and decision["decision"] == "extended"


def test_the_stop_does_not_count_as_a_model_call(tmp_path):
    settings = no_review_settings()
    reference = hitl.start_run(tmp_path / "ref", TASK, settings={**settings, "budget": H.example_settings()["budget"]},
                               mock_script=H.base_script())
    assert reference.status == "submitted"
    want = hitl.load_run(reference.run_dir)["limits"]
    out = budget_stop(tmp_path)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"add_usd": 5}))
    assert out.status == "submitted"
    assert hitl.load_run(out.run_dir)["limits"]["model_calls"] == want["model_calls"]
    # and the money of the calls is the same: nothing was charged twice over the re-executed node
    assert hitl.load_run(out.run_dir)["budget"]["spent_usd"] == \
        pytest.approx(hitl.load_run(reference.run_dir)["budget"]["spent_usd"], abs=0.001)


def test_finish_with_what_is_left_is_the_old_wrap_up(tmp_path):
    out = budget_stop(tmp_path)
    out = hitl.resume_run(out.run_dir, APPROVE)
    run = hitl.load_run(out.run_dir)
    assert out.status in ("budget_exhausted", "incomplete", "submitted")
    assert out.status != hitl.STATUS_AWAITING and run["budget_declined"] is True
    assert [r["decision"] for r in run["reviews"]] == ["declined"]
    assert "budget_limit_usd" not in run and not run.get("budget_extensions")
    assert not BS.budget_stop_enabled(run)


def test_too_little_money_goes_on_to_wrap_up_without_asking_twice_in_one_step(tmp_path):
    out = budget_stop(tmp_path)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"add_usd": 0.0001}))
    run = hitl.load_run(out.run_dir)
    assert run["budget_limit_usd"] == pytest.approx(0.0101)
    assert not out.awaiting and len(run["reviews"]) == 1


def test_auto_approve_has_no_stop_and_approve_rest_still_stops(tmp_path):
    out = start(tmp_path, H.base_script(), name="auto", settings=low_settings(), auto_approve=True)
    assert out.status == "budget_exhausted"
    assert hitl.load_run(out.run_dir)["budget_stop"] is False
    # review mode auto chosen in the UI is about reviews: money still asks
    out = start(tmp_path, H.base_script(), name="ui-auto", settings=low_settings(), review_mode="auto")
    assert out.awaiting and out.pending.point == hitl.BUDGET
    assert hitl.load_run(out.run_dir)["budget_stop"] is True
    # approve_rest at a review stop: the later stop for money still comes (a budget that fits HITL 1)
    settings = low_settings(per_task=0.7, reserve=0.001)
    out = start(tmp_path, H.base_script(), name="rest", settings=settings)
    assert out.pending.point == hitl.FORMALIZATION
    out = hitl.resume_run(out.run_dir, APPROVE, approve_rest=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    with pytest.raises(hitl.DecisionError, match="not for the budget stop"):
        hitl.resume_run(out.run_dir, APPROVE, approve_rest=True)
    assert hitl.load_run(out.run_dir)["status"] == hitl.STATUS_AWAITING


def test_money_is_never_auto_approved_and_old_runs_keep_working():
    assert not hitl.auto_approves(hitl.REVIEW_AUTO, hitl.BUDGET)
    assert not hitl.auto_approves(hitl.REVIEW_ALL, hitl.BUDGET)
    assert BS.budget_stop_enabled({"review_mode": "all"})
    assert BS.budget_stop_enabled({"review_mode": "auto", "initial_review_mode": "all"})
    assert not BS.budget_stop_enabled({"review_mode": "auto", "initial_review_mode": "auto"})
    assert not BS.budget_stop_enabled({"budget_stop": False})
    assert not BS.budget_stop_enabled({"budget_stop": True, "budget_declined": True})
    assert BS.budget_stop_on({"budget_stop": True, "budget_declined": True})


@pytest.mark.parametrize("payload, needle", [
    ({"add_usd": 0}, "greater than 0"),
    ({"add_usd": -1}, "greater than 0"),
    ({"add_usd": "много"}, "number of dollars"),
    ({"add_usd": True}, "number of dollars"),
    ({"add_usd": float("inf")}, "greater than 0"),
    ({"limit_usd": 100}, "unknown field"),
])
def test_bad_amounts_are_refused_and_the_run_keeps_waiting(tmp_path, payload, needle):
    out = budget_stop(tmp_path)
    with pytest.raises(hitl.DecisionError, match=needle):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", payload))
    assert hitl.load_run(out.run_dir)["status"] == hitl.STATUS_AWAITING


def test_reject_is_not_a_decision_about_money():
    request = BS.budget_request(B.BudgetLow(spent_usd=1, limit_usd=2, available_usd=1, reserve_usd=0.5,
                                            day_left_usd=None))
    with pytest.raises(hitl.DecisionError, match="not allowed at the budget stop"):
        hitl.resume_value(request, hitl.parse_decision("reject", comment="нет"))


def test_a_live_extension_must_fit_in_the_day(tmp_path):
    script = H.base_script()
    out = hitl.start_run(tmp_path / "lv", TASK, settings=no_review_settings(), model_factory=live_factory(script, tmp_path))
    assert out.awaiting and out.pending.point == hitl.BUDGET
    args = out.pending.request["action_requests"][0]["args"]
    assert args["day_left_usd"] > 0
    with pytest.raises(hitl.DecisionError, match="does not fit in the day"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"add_usd": 10_000}),
                        model_factory=live_factory(script, tmp_path), live=True)
    assert hitl.load_run(out.run_dir)["status"] == hitl.STATUS_AWAITING
    # what was asked is not what counts: the remainder is today's when the decision comes
    run = hitl.load_run(out.run_dir)
    log = B.DailySpendLog()
    log.append({"day": log.today(), "task": "other", "usd": 15.0})
    fresh = BS.with_todays_day_left(run, out.pending.request)["action_requests"][0]["args"]
    assert fresh["day_left_usd"] == pytest.approx(run["settings"]["budget"]["per_day_usd"] - log.day_total(), abs=1e-6)
    assert fresh["day_left_usd"] < args["day_left_usd"]
    done = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"add_usd": 1}),
                           model_factory=live_factory(script, tmp_path), live=True)
    assert done.status == "submitted"


def test_no_stop_when_the_day_is_what_binds(tmp_path):
    """More task budget buys nothing when the day's remainder is the limit."""
    est = B.CallEstimate("claude-opus-5-5", 1000, 1000, 0.03)
    log = B.DailySpendLog(tmp_path / "spend.jsonl")
    day_binds = B.BudgetLedger(5.0, reserve_for_report_usd=0.5, spend_log=log, per_day_usd=0.002, task_id="t")
    assert B.BudgetMiddleware(day_binds.root, fit_max_tokens=False)._budget_low_locked(est, day_binds.root) is None
    # the same call with the task's own limit in the way is a stop
    task_binds = B.BudgetLedger(0.02, reserve_for_report_usd=0.001, spend_log=log, per_day_usd=20.0, task_id="t")
    low = B.BudgetMiddleware(task_binds.root, fit_max_tokens=False)._budget_low_locked(est, task_binds.root)
    assert low.limit_usd == 0.02 and low.day_left_usd == pytest.approx(20.0)


def test_the_tty_dialog_asks_about_money(tmp_path):
    out = budget_stop(tmp_path)
    printed = []

    def dialog(*answers):
        replies = iter(answers)
        return hitl.TTYDialog(input_fn=lambda _prompt: next(replies), print_fn=printed.append)

    decision = dialog("a", "-1", "a", "2")(out.pending)
    assert decision.type == "edit" and decision.args == {"add_usd": 2.0} and decision.source == "dialog"
    assert dialog("f")(out.pending).type == "approve"
    assert dialog("l")(out.pending) is None


def test_a_dialog_in_the_same_process_extends_the_running_ledger(tmp_path):
    def dialog(info):
        return hitl.Decision("edit", args={"add_usd": 5}, source="dialog") if info.point == hitl.BUDGET else None

    out = start(tmp_path, H.base_script(), settings=no_review_settings(), dialog=dialog)
    assert out.status == "submitted", out.result
    run = hitl.load_run(out.run_dir)
    assert run["budget_limit_usd"] == pytest.approx(5.01)
    assert out.result["budget"]["limit_usd"] == pytest.approx(5.01)


def test_a_follow_up_may_ask_for_money_again(tmp_path):
    reply = {"content": ANSWER}
    script = H.base_script()
    script["models"]["main"].append(copy.deepcopy(reply))
    out = start(tmp_path, script, settings=no_review_settings())
    out = hitl.resume_run(out.run_dir, APPROVE)  # finish with what is left
    d = out.run_dir
    assert hitl.load_run(d)["budget_declined"] is True
    out = hitl.send_followup(d, QUESTION)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    assert "budget_declined" not in hitl.load_run(d)


# ---------------------------------------------------------------------------
# A follow-up that does not get through its segment
# ---------------------------------------------------------------------------

def half_finished(d):
    """What a process killed in the middle of a follow-up leaves behind."""
    run = hitl.load_run(d)
    run["followup_in_progress"] = {"snapshot": followup.snapshot_report(d), "started_at": run_store.now(),
                       "n_reviews": len(run["reviews"]), "previous_status": run["status"],
                       "previous_result": run["result"], "previous_error": None,
                       "review_switch": {k: run[k] for k in ("review_mode", "auto_approve", "initial_review_mode")}}
    run["status"] = "running"
    hitl.save_run(d, run)
    (d / "result.md").write_text("half-written", encoding="utf-8")


@pytest.mark.parametrize("name", ["final_result", "_write_result", "_factory"])
def test_an_exception_outside_the_graph_restores_the_report(tmp_path, monkeypatch, name):
    d = finished(tmp_path, {"content": "первый"}, {"content": ANSWER})
    files = report_bytes(d)

    def boom(*_a, **_k):
        raise ValueError("boom")

    with monkeypatch.context() as patched:
        patched.setattr(hitl, name, boom)
        with pytest.raises(ValueError, match="boom"):
            hitl.send_followup(d, "Провальный вопрос")
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and "followup_in_progress" not in run and "boom" in run["followup_error"]
    assert report_bytes(d) == files
    last = events(d)[-2]
    assert last["event"] == "followup_abandoned" and "boom" in last["error"] and last["status"] == "submitted"
    assert hitl.send_followup(d, QUESTION).status == "submitted"
    assert "followup_error" not in hitl.load_run(d)


def test_an_interrupt_restores_the_report_and_is_no_error(tmp_path, monkeypatch):
    d = finished(tmp_path, {"content": ANSWER})
    files = report_bytes(d)

    def stop(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(hitl, "final_result", stop)
    with pytest.raises(KeyboardInterrupt):
        hitl.send_followup(d, QUESTION)
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and "followup_in_progress" not in run and "followup_error" not in run
    assert report_bytes(d) == files
    assert events(d)[-2]["event"] == "followup_abandoned" and events(d)[-2]["error"] is None


def test_cli_abandon_after_a_hard_kill(tmp_path):
    script = H.base_script()
    script["models"]["main"].append({"content": ANSWER})
    path = H.write_script(tmp_path, script)
    env = H.subprocess_env(tmp_path / "runs")
    d = tmp_path / "cli"
    r = H.tfl_deep("--mock-script", str(path), "--save", str(d), "--settings", str(H.SETTINGS_EXAMPLE),
                   "--auto-approve", env=env)
    assert r.returncode == hitl.EXIT_SUBMITTED, r.stderr
    files = report_bytes(d)
    half_finished(d)
    # the next message says what to do instead of "the run is 'running'"
    r = H.tfl_deep("--resume", str(d), "--message", QUESTION, "--no-dialog", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "--abandon" in r.stderr
    r = H.tfl_deep("--resume", str(d), "--abandon", "--live", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "no other flag" in r.stderr
    r = H.tfl_deep("--resume", str(d), "--abandon", env=env)
    assert r.returncode == 0, r.stderr
    assert report_bytes(d) == files and hitl.load_run(d)["status"] == "submitted"
    r = H.tfl_deep("--resume", str(d), "--abandon", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "no follow-up in progress" in r.stderr
    r = H.tfl_deep("--abandon", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "--resume RUN_DIR" in r.stderr
    r = H.tfl_deep("--resume", str(d), "--message", QUESTION, "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_SUBMITTED, r.stderr


def test_abandon_refused_while_recording_process_alive(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    (d / "result.json").write_text("{}", encoding="utf-8")
    run = {"run_id": "x", "status": "running", "reviews": [],
           "followup_in_progress": {"snapshot": followup.snapshot_report(d), "previous_status": "submitted",
                                    "pid": os.getpid(), "n_reviews": 0, "review_switch": {}}}
    run_store.save_run(d, run)
    assert followup.pid_alive(os.getpid()) and not followup.pid_alive(None) and not followup.pid_alive(-1)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        run["followup_in_progress"]["pid"] = child.pid
        run_store.save_run(d, run)
        with pytest.raises(run_store.RunError, match="--force"):
            followup.abandon_followup(d, check_alive=True)
    finally:
        child.kill()
        child.wait()
    assert not followup.pid_alive(child.pid)
    assert followup.abandon_followup(d, check_alive=True) == "submitted"


class _SilentProgress:
    def __getattr__(self, _name):
        return lambda *_a, **_k: None


def _plain_run_dir(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "result.json").write_text('{"status": "submitted", "report": {"verdict": "v"}}', encoding="utf-8")
    previous = {"status": "submitted", "verdict": "v", "trust": "library", "confidence_cap": 0.9, "report_digest": "abc"}
    run = {"run_id": "plain", "status": "running", "report_revision": 1, "stable_checkpoint_id": "old", "reviews": [],
           "followup_in_progress": {"snapshot": followup.snapshot_report(d), "previous_status": "submitted",
                        "previous_result": previous, "previous_error": None, "n_reviews": 0,
                        "review_switch": {"review_mode": "all", "auto_approve": False,
                                          "initial_review_mode": "all"}}}
    return d, run


def test_finish_segment_of_a_failed_follow_up_without_a_graph(tmp_path):
    d, run = _plain_run_dir(tmp_path)
    (d / "result.json").write_text("half-written", encoding="utf-8")
    segment = {}
    out = hitl._finish_segment(d, run, segment, state={}, error="boom", budget=None, evidence=None,
                               stable_checkpoint=None, progress=_SilentProgress())
    assert out.exit_code == hitl.EXIT_NO_REPORT and out.followup_error == "boom" and out.status == "submitted"
    assert out.result["report"]["verdict"] == "v"
    assert segment["status"] == "error" and run["stable_checkpoint_id"] == "old" and run["report_revision"] == 1


@pytest.mark.parametrize("digest, revision", [("abc", 1), ("new", 2)])
def test_finish_segment_counts_a_report_only_when_it_says_something_new(tmp_path, monkeypatch, digest, revision):
    d, run = _plain_run_dir(tmp_path)
    report = {"verdict": "v"}
    monkeypatch.setattr(hitl, "final_result", lambda *_a, **_k: {"status": "submitted", "report": report,
                                                                 "trust": "library", "confidence_cap": 0.9})
    monkeypatch.setattr(hitl, "_write_result", lambda *_a, **_k: {"json": d / "result.json"})
    monkeypatch.setattr(followup, "report_digest", lambda _report: digest)
    state = {"messages": [HumanMessage(content="вопрос"), AIMessage(content=ANSWER)]}
    out = hitl._finish_segment(d, run, {}, state=state, error=None, budget=None, evidence=None,
                               stable_checkpoint="new-point", progress=_SilentProgress())
    assert out.reply == ANSWER and out.followup_error is None and out.exit_code == hitl.EXIT_SUBMITTED
    assert run["report_revision"] == revision and run["stable_checkpoint_id"] == "new-point" and "followup_in_progress" not in run


# ---------------------------------------------------------------------------
# The modules TFL Lab asks what a run allows, without the [deep] extra
# ---------------------------------------------------------------------------

def test_the_light_modules_import_nothing_heavy(tmp_path):
    code = (
        "import sys\n"
        "import tfl_deep_agent.budget_stop, tfl_deep_agent.followup, tfl_deep_agent.run_store\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & {'deepagents', 'langchain', 'langchain_core', "
        "'langgraph', 'pydantic'})\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(H.ROOT),
                       env=H.subprocess_env(tmp_path / "runs"), timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# The Lean button
# ---------------------------------------------------------------------------

def _report_on_disk(d, report):
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps({"status": "submitted", "report": report, "trust": "verified",
                                               "verdict_direction": "constructive"}), encoding="utf-8")


SUBMITTED = {"status": "submitted", "result": {"status": "submitted", "trust": "verified"}}


def test_lean_is_offered_on_a_verified_run(tmp_path):
    _report_on_disk(tmp_path, {"language_class": "reg"})
    assert followup.lean_refusal(SUBMITTED, tmp_path) is None
    led = EvidenceLedger.open(tmp_path)
    # other tools' verified evidence is no Lean proof, and neither is a lean_check that did not prove
    led.append(tool="regex_to_dfa", tool_call_id="dfa", result={"ok": True, "data": {}}, trust="verified",
               has_tool_result=True)
    led.append(tool="lean_check", tool_call_id="failed", result={"ok": False, "data": {"status": "failed"}},
               trust="not_verified", has_tool_result=True)
    _report_on_disk(tmp_path, {"language_class": "reg", "evidence_ids": ["dfa", "failed"]})
    assert followup.lean_refusal(SUBMITTED, tmp_path) is None


def test_lean_is_hidden_only_by_a_proof_this_report_cites(tmp_path):
    led = EvidenceLedger.open(tmp_path)
    led.append(tool="lean_check", tool_call_id="proved",
               result={"ok": True, "data": {"status": "proved", "cls": "cfl", "side": "constructive"}},
               trust="verified", has_tool_result=True)
    _report_on_disk(tmp_path, {"language_class": "cfl"})  # a new report that does not cite it
    assert followup.lean_refusal(SUBMITTED, tmp_path) is None
    _report_on_disk(tmp_path, {"language_class": "cfl", "claims": [{"evidence_ids": ["proved"]}]})
    assert "proved lean_check" in followup.lean_refusal(SUBMITTED, tmp_path)


def test_lean_is_hidden_for_ll_and_without_a_report(tmp_path):
    _report_on_disk(tmp_path, {"language_class": "ll"})
    assert "no Lean path" in followup.lean_refusal(SUBMITTED, tmp_path)
    assert "no accepted report" in followup.lean_refusal({"result": {"status": "incomplete"}}, tmp_path)
    assert "no accepted report" in followup.lean_refusal(SUBMITTED, tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# The contract of auto_approve and budget_stop
# ---------------------------------------------------------------------------

def test_budget_stop_defaults_to_not_auto_approve_and_can_be_set(tmp_path):
    out = start(tmp_path, H.base_script(), name="a", settings=no_review_settings(), auto_approve=True, budget_stop=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    out = start(tmp_path, H.base_script(), name="b", settings=no_review_settings(), budget_stop=False)
    assert out.status == "budget_exhausted" and hitl.load_run(out.run_dir)["budget_stop"] is False
    out = start(tmp_path, H.base_script(), name="c", settings=no_review_settings(), review_mode="auto", budget_stop=False)
    assert out.status == "budget_exhausted"


def test_cli_auto_approve_on_a_decision_keeps_the_budget_stop(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(low_settings(per_task=0.7)), encoding="utf-8")
    path = H.write_script(tmp_path, H.base_script())
    env = H.subprocess_env(tmp_path / "runs")
    d = tmp_path / "cli"
    r = H.tfl_deep("--mock-script", str(path), "--save", str(d), "--settings", str(settings), "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_AWAITING, r.stderr
    r = H.tfl_deep("--resume", str(d), "--decision", "approve", "--auto-approve", "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_AWAITING, r.stderr
    run = hitl.load_run(d)
    assert run["pending"]["point"] == hitl.BUDGET and run["budget_stop"] is True and run["review_mode"] == "auto"


def test_cli_auto_approve_does_not_go_with_a_message(tmp_path):
    env = H.subprocess_env(tmp_path / "runs")
    r = H.tfl_deep("--resume", str(tmp_path), "--message", "x", "--auto-approve", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "does not go with --message" in r.stderr


# ---------------------------------------------------------------------------
# Small contract points
# ---------------------------------------------------------------------------

def test_a_long_reply_is_kept_whole(tmp_path):
    emitter = ProgressEmitter(tmp_path, run_id="r")
    emitter.assistant_message("да")
    emitter.assistant_message("x" * (run_store.MAX_MESSAGE_CHARS + 1))
    short, long = [e for e in events(tmp_path) if e["event"] == "assistant_message"]
    assert "truncated" not in short
    assert "truncated" not in long and len(long["text"]) == run_store.MAX_MESSAGE_CHARS + 1


def test_the_message_limit_holds_in_the_core(tmp_path):
    d = finished(tmp_path, {"content": ANSWER})
    with pytest.raises(hitl.RunError, match="too long"):
        hitl.send_followup(d, "x" * (run_store.MAX_MESSAGE_CHARS + 1))
    assert not (d / run_store.REPORT_HISTORY_DIR).exists()


def test_at_at_is_a_literal_at(tmp_path):
    from tfl_deep_agent import cli

    assert cli._text_or_file("@@S -> a", "--message") == "@S -> a"
    text = tmp_path / "q.txt"
    text.write_text("из файла", encoding="utf-8")
    assert cli._text_or_file(f"@{text}", "--message") == "из файла"


def test_the_allowed_decisions_are_checked_before_the_comment():
    with pytest.raises(hitl.DecisionError, match="not allowed at this stop"):
        hitl.parse_decision("reject", allowed=["approve", "edit"])
    with pytest.raises(hitl.DecisionError, match="needs a comment"):
        hitl.parse_decision("reject", allowed=["approve", "reject"])


def test_raise_limit_only_grows_and_says_so():
    seen = []
    ledger = B.BudgetLedger(1.0, reserve_for_report_usd=0.1, task_id="t", listeners=[seen.append])
    assert ledger.raise_limit(3.0) == 3.0 and ledger.root.limit == 3.0
    assert ledger.raise_limit(2.0) == 3.0 and ledger.raise_limit(3.0) == 3.0
    assert [(e["event"], e["previous_usd"], e["limit_usd"]) for e in seen] == [("limit_raised", 1.0, 3.0)]


def test_every_point_has_its_facts():
    for point in (hitl.FORMALIZATION, hitl.REPORT, hitl.EXECUTE, hitl.BUDGET):
        assert hitl.point_facts(point).title
    assert not hitl.point_facts(hitl.BUDGET).auto_approvable
    assert hitl.point_facts(hitl.REPORT).auto_approvable
    assert hitl.point_facts("something else").title is None


# ---------------------------------------------------------------------------
# HITL 2 and a report the tool will refuse (leaked tool-call markup)
# ---------------------------------------------------------------------------

LEAKED = 'Гипотеза</hypothesis><parameter name="construction">обрывок'


def test_hitl_2_has_no_stop_for_a_report_with_leaked_markup():
    script = H.base_script()
    good = script["models"]["main"][-1]["tool_calls"][0]
    config = hitl.report_interrupt_config()
    assert config["when"]({"tool_call": good}) is True
    bad = {**good, "args": {**good["args"], "hypothesis": LEAKED}}
    assert config["when"]({"tool_call": bad}) is False
    # the check looks inside nested fields too
    steps = [{**step, "text": "<invoke name='x'>"} for step in good["args"]["steps"]]
    assert config["when"]({"tool_call": {**good, "args": {**good["args"], "steps": steps}}}) is False


def test_a_report_with_leaked_markup_is_refused_with_no_card_and_resubmitted(tmp_path):
    script = H.base_script()
    last = script["models"]["main"].pop()
    leaked = copy.deepcopy(last)
    leaked["tool_calls"][0]["id"] = "call-report-leaked"
    leaked["tool_calls"][0]["args"]["hypothesis"] = LEAKED
    script["models"]["main"] += [leaked, last]
    out = start(tmp_path, script)
    out = hitl.resume_run(out.run_dir, APPROVE)  # HITL 1
    assert out.awaiting and out.pending.point == hitl.REPORT
    # the card is the good report's: the leaked one went straight back to the model
    args = out.pending.request["action_requests"][0]["args"]
    assert "</hypothesis>" not in json.dumps(args, ensure_ascii=False)
    assert kinds(out.run_dir).count("hitl_awaiting") == 2
    refused = [m for m in H.thread_messages(out.run_dir)
               if isinstance(m, ToolMessage) and m.tool_call_id == "call-report-leaked"]
    assert len(refused) == 1 and "hypothesis" in str(refused[0].content)
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.status == "submitted"
    assert "</hypothesis>" not in (out.run_dir / "result.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A known gap, pinned: a summary before a budget stop is made again after the resume
# ---------------------------------------------------------------------------

SUMMARY_USD = 0.0006


def _summaries_every(monkeypatch, n):
    """The main agent's history is summarized from ``n`` messages on, by a cheap reply."""
    import deepagents.middleware.summarization as summarization

    monkeypatch.setattr(summarization, "compute_summarization_defaults", lambda _model: {
        "trigger": ("messages", n), "keep": ("messages", 2),
        "truncate_args_settings": {"trigger": ("messages", 10000), "keep": ("messages", 10000)}})
    cls = A.scripted_model_class()
    replay = cls._generate

    def generate(self, messages, stop=None, run_manager=None, **kw):
        if B._CALL_KIND.get() == "summary":
            usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="SUMMARY", usage_metadata=usage))])
        return replay(self, messages, stop, run_manager, **kw)

    monkeypatch.setattr(cls, "_generate", generate)


def test_a_summary_before_the_stop_is_paid_for_once_more_and_the_cap_holds(tmp_path, monkeypatch):
    _summaries_every(monkeypatch, 3)
    settings = H.example_settings()
    settings["hitl"].update({"formalization": False, "report": False})
    reference = hitl.start_run(tmp_path / "ref", TASK, settings=settings, mock_script=H.base_script())
    assert reference.status == "submitted"
    settings = no_review_settings(per_task=0.7)
    out = hitl.start_run(tmp_path / "stop", TASK, settings=settings, mock_script=H.base_script())
    assert out.awaiting and out.pending.point == hitl.BUDGET
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"add_usd": 5}))
    assert out.status == "submitted", out.result
    spent = hitl.load_run(out.run_dir)["budget"]["spent_usd"]
    # exactly one repeated summary: the node runs again from its start, the model call is not repeated
    assert spent - hitl.load_run(reference.run_dir)["budget"]["spent_usd"] == pytest.approx(SUMMARY_USD, abs=1e-4)
    assert spent <= hitl.load_run(out.run_dir)["budget_limit_usd"]


# ---------------------------------------------------------------------------
# A review belongs to the report it decided
# ---------------------------------------------------------------------------

REJECT = hitl.parse_decision("reject", comment="Не то.")


def draft_reply(summary, call_id):
    return H.report_call(H.base_script(), call_id=call_id, summary=summary)


def result_json(d):
    return json.loads((d / "result.json").read_text(encoding="utf-8"))


def report_reviews(run):
    return [r for r in run["reviews"] if r["point"] == hitl.REPORT]


def test_a_rejected_draft_and_a_text_reply_leave_the_approved_report_approved(tmp_path):
    d = finished(tmp_path, draft_reply("Черновик один.", "call-draft-1"), {"content": "Оставляю прежний отчёт."},
                 draft_reply("Черновик два.", "call-draft-2"), {"content": "Снова прежний отчёт."})
    approved = result_json(d)
    assert approved["human_review"] == "approved by a human at HITL 2"
    for message in ("Перепиши", "Перепиши ещё раз"):
        assert hitl.send_followup(d, message).pending.point == hitl.REPORT
        out = hitl.resume_run(d, REJECT)
        assert out.status == "submitted" and out.reply
        result = result_json(d)
        assert result["report"]["summary"] == approved["report"]["summary"]
        assert result["human_review"] == "approved by a human at HITL 2"
        assert result["rendered"]["human_review"]["decision"] == "approved"
        assert result["trust"] == approved["trust"] and result["confidence_cap"] == approved["confidence_cap"]
    decisions = [r["decision"] for r in report_reviews(hitl.load_run(d))]
    assert decisions == ["approved", "rejected", "rejected"]


def test_each_report_review_holds_the_digest_of_what_it_decided(tmp_path):
    d = finished(tmp_path, name="approved")
    assert report_reviews(hitl.load_run(d))[-1]["report_digest"] == followup.report_digest(result_json(d)["report"])

    out = start(tmp_path, H.base_script(), name="edited")
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"summary": "Сводка человека."}))
    assert out.status == "submitted"
    result = result_json(out.run_dir)
    assert result["report"]["summary"] == "Сводка человека."
    assert report_reviews(hitl.load_run(out.run_dir))[-1]["report_digest"] == followup.report_digest(result["report"])
    assert result["human_review"].startswith("approved with edits")

    out = start(tmp_path, H.base_script(), name="auto", auto_approve=True)
    result = result_json(out.run_dir)
    assert report_reviews(hitl.load_run(out.run_dir))[-1]["report_digest"] == followup.report_digest(result["report"])
    assert result["human_review"] == hitl.AUTO_APPROVED_LABEL


def test_a_new_report_after_a_rejected_draft_is_judged_by_its_own_review(tmp_path):
    d = finished(tmp_path, draft_reply("Черновик.", "call-draft"), draft_reply("Исправленный.", "call-fixed"),
                 {"content": ANSWER})
    assert hitl.send_followup(d, "Перепиши").pending.point == hitl.REPORT
    assert hitl.resume_run(d, REJECT).pending.point == hitl.REPORT
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"summary": "Исправленный человеком."}))
    assert out.status == "submitted"
    assert result_json(d)["human_review"].startswith("approved with edits")
    assert hitl.load_run(d)["report_revision"] == 2


def test_review_summary_of_runs_with_and_without_digests():
    on = {"hitl": {"report": True}}
    old = [{"point": "report", "decision": "approved"}, {"point": "report", "decision": "rejected"}]
    report = {"verdict": "v"}
    # no digest anywhere: the last review, as before
    assert "rejected" in hitl.review_summary({**on, "reviews": old}, report)[0]
    assert "rejected" in hitl.review_summary({**on, "reviews": old})[0]
    # an old approval with a new draft rejected over it: the draft is not the report
    mixed = [{"point": "report", "decision": "approved"},
             {"point": "report", "decision": "rejected", "report_digest": "another"}]
    assert hitl.review_summary({**on, "reviews": mixed}, report)[0] == "approved by a human at HITL 2"
    # no accepted report: what happened to the last draft
    assert "rejected" in hitl.review_summary({**on, "reviews": mixed})[0]
    # reviews of other reports only: this one was not reviewed
    other = [{"point": "report", "decision": "approved", "report_digest": "another"}]
    assert hitl.review_summary({**on, "reviews": other}, report) == ("not reviewed", {"decision": "not_reviewed"})
    # the same content approved, then rejected (API-025): the approval stays
    digest = followup.report_digest(report)
    twice = [{"point": "report", "decision": "approved", "report_digest": digest},
             {"point": "report", "decision": "rejected", "report_digest": digest}]
    assert hitl.review_summary({**on, "reviews": twice}, report)[0] == "approved by a human at HITL 2"


# ---------------------------------------------------------------------------
# A review-mode switch: applied once the decision fits, and only for what finished
# ---------------------------------------------------------------------------

def _waiting_cli_run(tmp_path, settings=None, name="cli"):
    path = H.write_script(tmp_path, H.base_script())
    env = H.subprocess_env(tmp_path / "runs")
    file = tmp_path / f"{name}-settings.json"
    file.write_text(json.dumps(settings or H.example_settings()), encoding="utf-8")
    d = tmp_path / name
    r = H.tfl_deep("--mock-script", str(path), "--save", str(d), "--settings", str(file), "--no-dialog", env=env)
    assert r.returncode == hitl.EXIT_AWAITING, r.stderr
    return d, env


def test_cli_a_refused_auto_approve_changes_nothing(tmp_path):
    d, env = _waiting_cli_run(tmp_path)
    r = H.tfl_deep("--resume", str(d), "--decision", "edit", "--payload", '{"nope": 1}', "--auto-approve",
                   "--no-dialog", env=env)
    assert r.returncode == run_store.EXIT_USAGE, r.stderr
    run = hitl.load_run(d)
    assert run["review_mode"] == "all" and run["auto_approve"] is False and run["status"] == hitl.STATUS_AWAITING


def test_cli_auto_approve_is_refused_at_the_budget_stop(tmp_path):
    d, env = _waiting_cli_run(tmp_path, no_review_settings())
    assert hitl.load_run(d)["pending"]["point"] == hitl.BUDGET
    r = H.tfl_deep("--resume", str(d), "--decision", "approve", "--auto-approve", "--no-dialog", env=env)
    assert r.returncode == run_store.EXIT_USAGE and "not for the budget stop" in r.stderr
    run = hitl.load_run(d)
    assert run["review_mode"] == "all" and run["status"] == hitl.STATUS_AWAITING and run["reviews"] == []


def test_auto_approve_later_switches_only_after_the_decision_fits(tmp_path):
    out = start(tmp_path, H.base_script())
    with pytest.raises(hitl.DecisionError):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"nope": 1}), auto_approve_later=True)
    assert hitl.load_run(out.run_dir)["review_mode"] == "all"
    out = hitl.resume_run(out.run_dir, APPROVE, auto_approve_later=True)
    assert out.status == "submitted"
    run = hitl.load_run(out.run_dir)
    assert run["review_mode"] == "auto" and run["auto_approve"] is True and run["initial_review_mode"] == "all"
    assert run["budget_stop"] is True and "approve_rest" not in run
    assert [r["decision"] for r in run["reviews"]] == ["approved", "auto_approved"]


@pytest.mark.parametrize("switch", [{"approve_rest": True}, {"auto_approve_later": True}])
def test_a_review_switch_inside_a_failed_follow_up_does_not_outlive_it(tmp_path, switch, monkeypatch):
    d = finished(tmp_path, draft_reply("Черновик.", "call-draft"))
    before = hitl.load_run(d)
    assert hitl.send_followup(d, "Перепиши").pending.point == hitl.REPORT

    def boom(*_a, **_k):
        raise ValueError("boom")

    with monkeypatch.context() as patched:  # an accepted report ends the run: the failure is in writing it
        patched.setattr(hitl, "final_result", boom)
        with pytest.raises(ValueError, match="boom"):
            hitl.resume_run(d, APPROVE, **switch)
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and "boom" in run["followup_error"]
    for key in ("review_mode", "auto_approve", "initial_review_mode"):
        assert run[key] == before[key]
    assert "approve_rest" not in run
    # the next follow-up stops for a human
    script = json.loads((d / hitl.MOCK_SCRIPT_FILE).read_text(encoding="utf-8"))
    script["models"]["main"].append(draft_reply("Черновик после сбоя.", "call-next"))
    (d / hitl.MOCK_SCRIPT_FILE).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    assert hitl.send_followup(d, "Ещё раз").pending.point == hitl.REPORT


def test_a_budget_extension_survives_a_failed_follow_up_and_a_switch_does_not(tmp_path):
    d, run = _plain_run_dir(tmp_path)
    run.update(budget_limit_usd=6.0, budget_extensions=[{"add_usd": 5}], review_mode="auto", auto_approve=True,
               approve_rest={"point": "report"})
    followup.fail_followup(d, run, "boom")
    assert run["budget_limit_usd"] == 6.0 and run["budget_extensions"] == [{"add_usd": 5}]
    assert run["review_mode"] == "all" and run["auto_approve"] is False and "approve_rest" not in run


# ---------------------------------------------------------------------------
# A restore that fails
# ---------------------------------------------------------------------------

@pytest.fixture
def no_waiting(monkeypatch):
    monkeypatch.setattr(run_store.time, "sleep", lambda _s: None)


def copies_fail_after(monkeypatch, allowed, *, times=None):
    """``shutil.copy2`` is denied after ``allowed`` calls: always, or ``times`` times."""
    import shutil

    real = shutil.copy2
    state = {"calls": 0, "denied": 0}

    def copy(*args, **kw):
        state["calls"] += 1
        if state["calls"] > allowed and (times is None or state["denied"] < times):
            state["denied"] += 1
            raise PermissionError(13, "denied")
        return real(*args, **kw)

    monkeypatch.setattr(shutil, "copy2", copy)
    return state


def test_retry_on_a_sharing_violation(no_waiting):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(13, "denied")
        return "done"

    assert run_store.retry_on_sharing_violation(flaky) == "done" and len(attempts) == 3

    def never():
        raise PermissionError(13, "denied")

    with pytest.raises(PermissionError):
        run_store.retry_on_sharing_violation(never)


def test_a_restore_rides_over_a_sharing_violation(tmp_path, monkeypatch, no_waiting):
    d = finished(tmp_path, {"content": ANSWER})
    files = report_bytes(d)
    half_finished(d)
    state = copies_fail_after(monkeypatch, 0, times=3)
    assert followup.abandon_followup(d) == "submitted"
    assert state["denied"] == 3 and report_bytes(d) == files


def test_a_failed_restore_keeps_the_record_and_can_be_repeated(tmp_path, monkeypatch, no_waiting):
    d = finished(tmp_path, {"content": ANSWER})
    files = report_bytes(d)
    half_finished(d)
    with monkeypatch.context() as patched:
        copies_fail_after(patched, 0)
        with pytest.raises(PermissionError):
            followup.abandon_followup(d)
    run = hitl.load_run(d)
    assert run["status"] == "running" and run["followup_in_progress"]["previous_status"] == "submitted"
    assert "followup_abandoned" not in kinds(d)  # the chat is not told the report is back
    assert followup.abandon_followup(d) == "submitted"
    assert report_bytes(d) == files and "followup_in_progress" not in hitl.load_run(d)


def test_a_failed_restore_does_not_hide_why_the_follow_up_failed(tmp_path, monkeypatch, no_waiting):
    d = finished(tmp_path, {"content": ANSWER})

    def boom(*_a, **_k):
        raise ValueError("boom")

    with monkeypatch.context() as patched:
        patched.setattr(hitl, "final_result", boom)
        copies_fail_after(patched, len(run_store.RESULT_FILES))  # the snapshot goes through, the restore does not
        with pytest.raises(ValueError, match="boom"):
            hitl.send_followup(d, QUESTION)
    assert hitl.load_run(d)["followup_in_progress"]["previous_status"] == "submitted"
    assert followup.abandon_followup(d) == "submitted"


# ---------------------------------------------------------------------------
# One failure, one event shape
# ---------------------------------------------------------------------------

def abandoned_tail(d):
    tail = events(d)[-2:]
    assert [e["event"] for e in tail] == ["followup_abandoned", "segment_end"]
    assert tail[1]["status"] == tail[0]["status"] == "submitted" and tail[1]["error"] is None
    return tail[0]


def test_a_failing_agent_and_an_abandon_give_the_same_events(tmp_path):
    d = finished(tmp_path, name="agent")
    out = hitl.send_followup(d, "Провальный вопрос")
    assert "mock script" in out.followup_error
    assert "mock script" in abandoned_tail(d)["error"]

    d = finished(tmp_path, {"content": ANSWER}, name="killed")
    half_finished(d)
    followup.abandon_followup(d, error="the process is gone")
    assert abandoned_tail(d)["error"] == "the process is gone"

    d = finished(tmp_path, {"content": ANSWER}, name="cancelled")
    half_finished(d)
    followup.abandon_followup(d)
    assert abandoned_tail(d)["error"] is None


# ---------------------------------------------------------------------------
# The CLI's way out of a follow-up
# ---------------------------------------------------------------------------

def test_cli_a_message_at_a_stop_inside_a_follow_up_asks_for_the_decision(tmp_path):
    d = finished(tmp_path, draft_reply("Черновик.", "call-draft"))
    assert hitl.send_followup(d, "Перепиши").pending.point == hitl.REPORT
    r = H.tfl_deep("--resume", str(d), "--message", QUESTION, "--no-dialog", env=H.subprocess_env(tmp_path / "runs"))
    assert r.returncode == run_store.EXIT_USAGE and "decide on it first" in r.stderr
    assert "--abandon" not in r.stderr


def test_cli_a_decision_on_a_killed_follow_up_points_at_abandon(tmp_path):
    d = finished(tmp_path, {"content": ANSWER})
    half_finished(d)
    r = H.tfl_deep("--resume", str(d), "--decision", "approve", "--no-dialog", env=H.subprocess_env(tmp_path / "runs"))
    assert r.returncode == run_store.EXIT_USAGE and f"--resume {d} --abandon" in r.stderr


@pytest.mark.parametrize("extra", [
    ["task text"], ["--file", "x.txt"], ["--save", "x"], ["--template", "full_proof"], ["--live"],
    ["--mock-script", "x.json"], ["--review-mode", "all"], ["--auto-approve"], ["--no-dialog"], ["--settings", "x.json"],
    ["--message", "x"], ["--decision", "approve"], ["--payload", "{}"], ["--comment", "x"], ["--approve-rest"],
])
def test_cli_abandon_takes_no_other_flag(tmp_path, capsys, extra):
    from tfl_deep_agent import cli

    with pytest.raises(SystemExit) as stop:
        cli.main(["--resume", str(tmp_path), "--abandon", *extra])
    assert stop.value.code == run_store.EXIT_USAGE and "no other flag" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# A spent day refuses a live follow-up the same way everywhere
# ---------------------------------------------------------------------------

def test_a_spent_day_refuses_a_live_follow_up_before_anything_changes(tmp_path):
    script = H.base_script()
    script["models"]["main"].append({"content": ANSWER})

    def live():
        return live_factory(script, tmp_path)

    out = hitl.start_run(tmp_path / "lv", TASK, settings=H.example_settings(), model_factory=live())
    out = hitl.resume_run(out.run_dir, APPROVE, model_factory=live(), live=True)
    out = hitl.resume_run(out.run_dir, APPROVE, model_factory=live(), live=True)
    d = out.run_dir
    run = hitl.load_run(d)
    run["budget_stop"] = False  # nobody to ask: the refusal stands (with a stop the day question comes first)
    hitl.save_run(d, run)
    assert followup.day_refusal(hitl.load_run(d)) is None
    log = B.DailySpendLog()
    log.append({"day": log.today(), "task": "other", "usd": 1000.0})
    refusal = followup.day_refusal(hitl.load_run(d))
    assert "day's budget is used up" in refusal
    before = hitl.load_run(d)
    with pytest.raises(hitl.RunError, match="day's budget is used up"):
        hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    after = hitl.load_run(d)
    assert after["status"] == before["status"] and "followup_in_progress" not in after
    assert not (d / run_store.REPORT_HISTORY_DIR).exists()


def test_a_spent_day_asks_instead_of_refusing_after_an_accepted_report(tmp_path):
    d, live, per_day = day_short_followup(tmp_path)
    log = B.DailySpendLog()
    log.append({"day": log.today(), "task_id": "more", "usd": 10.0})  # the day is used up
    run = hitl.load_run(d)
    assert run["status"] == "submitted" and followup.day_refusal(run) is None
    out = hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    assert out.awaiting and out.pending.request["action_requests"][0]["args"]["binds"] == "day"
    done = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert done.reply == ANSWER


def test_the_day_says_nothing_about_a_mock_run(tmp_path):
    log = B.DailySpendLog(tmp_path / "spend.jsonl")
    log.append({"day": log.today(), "task": "other", "usd": 1000.0})
    settings = {"budget": {"per_day_usd": 1.0}}
    assert followup.day_refusal({"mode": "mock", "settings": settings}, spend_log=log) is None
    assert "used up" in followup.day_refusal({"mode": "live", "settings": settings}, spend_log=log)
    assert followup.day_refusal({"mode": "live", "settings": {"budget": {}}}, spend_log=log) is None


# ---------------------------------------------------------------------------
# The Lean button and the verdict gate read one predicate
# ---------------------------------------------------------------------------

LEAN_DATA = {"status": "proved", "cls": "cfl", "side": "constructive", "ir_fingerprint": "fp"}
LEAN_RESULT = {"trust": "verified", "verdict_direction": "constructive", "required_ir_fingerprint": "fp"}


def _cited_proof(d, *, data=None, trust="verified", ok=True, result=None):
    led = EvidenceLedger.open(d)
    led.append(tool="lean_check", tool_call_id="proved", result={"ok": ok, "data": {**LEAN_DATA, **(data or {})}},
               trust=trust, has_tool_result=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps({
        "status": "submitted", **LEAN_RESULT, **(result or {}),
        "report": {"language_class": "cfl", "evidence_ids": ["proved"]}}), encoding="utf-8")


def test_a_proof_the_gate_counts_hides_the_lean_button(tmp_path):
    _cited_proof(tmp_path)
    assert "proved lean_check" in followup.lean_refusal(SUBMITTED, tmp_path)


@pytest.mark.parametrize("kw", [
    {"data": {"cls": "reg"}},                               # a statement about another class
    {"data": {"side": None}},                               # no side: the gate has nothing to put it on
    {"data": {"side": "destructive"}},                      # the other side of this verdict
    {"data": {"ir_fingerprint": "another"}},                # another IR: the gate excludes it
    {"data": {"status": "failed"}},
    {"trust": "well_formed"},                               # non-standard axioms
    {"ok": False},
    {"result": {"trust": "not_verified"}},                  # the gate ended lower (HITL 1 rejected, R3, ...)
])
def test_a_proof_the_gate_would_not_count_leaves_the_button(tmp_path, kw):
    _cited_proof(tmp_path, **kw)
    assert followup.lean_refusal(SUBMITTED, tmp_path) is None


@pytest.mark.parametrize("entry, lang, side", [
    ({}, "cfl", "constructive"),
    ({"data": {**LEAN_DATA, "cls": "reg"}}, "cfl", None),
    ({"data": {**LEAN_DATA, "cls": "ll"}}, "ll", None),
    ({"data": {**LEAN_DATA, "side": None}}, "cfl", None),
    ({"data": {**LEAN_DATA, "status": "failed"}}, "cfl", None),
    ({"trust": "well_formed"}, "cfl", None),
    ({"tool": "cyk"}, "cfl", None),
])
def test_the_predicate_is_what_the_gate_classifies_by(entry, lang, side):
    from tfl_deep_agent.evidence.ledger import lean_proof_side

    record = {"id": "e", "tool": "lean_check", "ok": True, "trust": "verified", "data": LEAN_DATA, "args": {}, **entry}
    assert lean_proof_side(record, lang) == side
    if record["tool"] == "lean_check":
        assert A._classify(record, lang, None, [record]) == ([(side, "verified")] if side else [])


def test_the_predicate_carries_the_gates_fingerprint_rule():
    from tfl_deep_agent.evidence.ledger import ir_admissible, lean_proof_side

    record = {"id": "e", "tool": "lean_check", "ok": True, "trust": "verified", "data": LEAN_DATA, "args": {}}
    assert lean_proof_side(record, "cfl", required_fingerprint="fp") == "constructive"
    assert lean_proof_side(record, "cfl", required_fingerprint="another") is None
    assert lean_proof_side({**record, "ok": False}, "cfl") is None
    unbound = {**record, "data": {k: v for k, v in LEAN_DATA.items() if k != "ir_fingerprint"}}
    assert lean_proof_side(unbound, "cfl", required_fingerprint="fp") == "constructive"  # None is admissible
    # the fingerprint from the call's own IR, as the gate reads it
    with_ir = {**unbound, "args": {"ir": {"a": 1}}}
    from tfl_deep_agent.tools.ir import ir_fingerprint

    assert ir_admissible(with_ir, ir_fingerprint({"a": 1})) and not ir_admissible(with_ir, "fp")
    gate = A.verdict_gate({"verdict": "CFL", "language_class": "cfl", "ir_fingerprint": "another"},
                          [{**record, "id": "e"}], ["e"])
    assert gate["excluded_ir_mismatch"] == ["e"]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_followup_and_budget_stop_never_reference_the_hitl_module():
    import inspect
    import re

    for module in (followup, BS):
        assert not re.search(r"\bhitl\b", inspect.getsource(module)), module.__name__


def test_a_requested_budget_stop_with_no_budget_middleware_is_an_error():
    with pytest.raises(ValueError, match="BudgetMiddleware"):
        A._enable_budget_stop([], lambda _low: None)


def test_budget_stop_is_shown_where_the_review_mode_is(tmp_path):
    d = finished(tmp_path, name="asks")
    assert result_json(d)["hitl"]["budget_stop"] is True
    out = start(tmp_path, H.base_script(), name="silent", auto_approve=True)
    assert result_json(out.run_dir)["hitl"]["budget_stop"] is False


def test_an_old_run_after_approve_rest_still_asks_for_money():
    assert BS.budget_stop_enabled({"review_mode": "auto", "auto_approve": True, "initial_review_mode": "all"})
    assert BS.budget_stop_enabled({"review_mode": "auto", "auto_approve": True})
    assert not BS.budget_stop_enabled({"initial_review_mode": "auto"})
    assert not BS.budget_stop_enabled({"budget_stop": False, "initial_review_mode": "all"})


def test_the_review_modes_have_one_home():
    from tfl_deep_agent import cli

    assert hitl.REVIEW_MODES is run_store.REVIEW_MODES and cli.REVIEW_MODES is run_store.REVIEW_MODES
    assert hitl.DECISIONS is run_store.DECISIONS and hitl.first_decision is run_store.first_decision


# ---------------------------------------------------------------------------
# An accepted report is rated by what existed when it was accepted
# ---------------------------------------------------------------------------

LL_IR_B = {"task_type": "ll_check_grammar", "source_text": "S → c. Является ли грамматика LL(1)?",
           "grammar": {"nonterminals": ["S"], "terminals": ["c"], "start": "S",
                       "rules": [{"lhs": "S", "rhs": ["c"]}]},
           "question": "is_ll_k", "k": 1}
REG_IR_B = {"task_type": "classify_and_prove", "source_text": "a*",
            "language_spec": {"kind": "regex", "alphabet": ["a"], "pattern": "a*", "has_backreferences": False}}
NEW_TASK = "А теперь другая задача."


def add_ir_b(script, ir, cls, *after):
    """What a follow-up does next in ``script``: the ir-builder on IR B (a HITL 1 stop), then ``after``."""
    script["models"]["main"] += [
        {"tool_calls": [{"name": "task", "id": "call-ir-builder-b", "args": {
            "subagent_type": "ir-builder", "description": f"Класс {cls}. Построй IR новой задачи."}}]},
        *after]
    script["models"]["ir_builder"] += [
        {"tool_calls": [{"name": "validate_ir", "id": "ib-validate-b", "args": {"cls": cls, "ir": ir}}]},
        {"content": f"Класс {cls}. validate_ir: valid. Неуверенные места: нет."}]
    return script


def rating(d):
    """What must not move while the accepted report stays the same."""
    result, run = result_json(d), hitl.load_run(d)
    return {"status": result["status"], "trust": result["trust"], "cap": result["confidence_cap"],
            "token": result.get("verdict_token"), "task": result["task"], "formalization": result.get("formalization"),
            "revision": run["report_revision"], "digest": run["result"]["report_digest"]}


def finished_ll(tmp_path, *after):
    """A finished LL run (reviewed by a human) whose next message reviews IR B, then ``after``."""
    script = add_ir_b(H.base_script(), LL_IR_B, "ll", *after)
    out = start(tmp_path, script)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.status == "submitted", out.result
    return out.run_dir


def to_ir_b_review(d):
    out = hitl.send_followup(d, NEW_TASK)
    assert out.awaiting and out.pending.point == hitl.FORMALIZATION
    assert out.pending.request["action_requests"][0]["args"]["ir_builder_call_id"] == "call-ir-builder-b"


@pytest.mark.parametrize("decision", [APPROVE, REJECT], ids=["approve", "reject"])
def test_a_new_ir_reviewed_in_a_follow_up_does_not_rate_the_old_report(tmp_path, decision):
    d = finished_ll(tmp_path, {"content": ANSWER}, {"content": ANSWER})
    before = rating(d)
    assert before["trust"] != "not_verified"
    to_ir_b_review(d)
    out = hitl.resume_run(d, decision)
    assert out.status == "submitted" and out.reply == ANSWER
    assert rating(d) == before
    # and on every later message too
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER and rating(d) == before
    assert kinds(d).count("done") == 1


def test_an_edited_transcription_of_a_new_ir_does_not_become_the_old_reports_task(tmp_path):
    d = finished_ll(tmp_path, {"content": ANSWER})
    before = rating(d)
    to_ir_b_review(d)
    edit = hitl.parse_decision("edit", json.dumps({"transcription": "S → c. Новая задача."}, ensure_ascii=False))
    out = hitl.resume_run(d, edit)
    assert out.status == "submitted"
    assert hitl.load_run(d)["reviews"][-1]["transcription"] == "S → c. Новая задача."
    assert hitl.load_run(d)["reviews"][-1]["reviewed_call_id"] == "call-ir-builder-b"
    assert rating(d) == before


def test_the_result_of_a_new_ir_does_not_lift_the_old_report(tmp_path):
    """The case that gave report A ``verified`` on B's evidence: HITL 1 of A was rejected."""
    script = json.loads((H.MOCK / "reg_followup.json").read_text(encoding="utf-8"))
    script["models"]["main"] = script["models"]["main"][:7]  # up to the report
    assert script["models"]["main"][-1]["tool_calls"][0]["name"] == "submit_report"
    add_ir_b(script, REG_IR_B, "reg",
             {"tool_calls": [{"name": "regex_to_dfa", "id": "ev-dfa-b", "args": {"ir": REG_IR_B}}]},
             {"content": ANSWER})
    out = start(tmp_path, script)
    out = hitl.resume_run(out.run_dir, REJECT)
    d = out.run_dir
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted", out.result
    before = rating(d)
    assert before["trust"] == "not_verified"
    to_ir_b_review(d)
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted" and out.reply == ANSWER
    assert [e["id"] for e in result_json(d)["evidence"] if e["id"] == "ev-dfa-b"]  # paid for, still listed
    assert rating(d) == before


def finished_reg(tmp_path, *after):
    """A finished REG run (the mock's regex, reviewed by a human), with ``after`` for its follow-ups."""
    script = json.loads((H.MOCK / "reg_followup.json").read_text(encoding="utf-8"))
    script["models"]["main"] = [*script["models"]["main"][:7], *after]
    out = start(tmp_path, script)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.status == "submitted", out.result
    return out.run_dir


def test_a_later_tool_run_on_the_same_ir_does_not_move_the_old_report(tmp_path):
    """No new HITL 1 here: the gate takes results the report never cited (R4'), so only
    the cut of the ledger holds this one."""
    probe = {"name": "nerode_probe", "id": "ev-later", "args": {"ir": {"$def": "ir"}, "pairs": [{"u": "a", "v": "b"}]}}
    d = finished_reg(tmp_path, {"tool_calls": [probe]}, {"content": ANSWER})
    before = rating(d)
    assert before["trust"] == "verified"
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER
    assert "ev-later" in {r.id for r in EvidenceLedger.load(d).records()}
    assert rating(d) == before


def test_the_gate_itself_counts_a_later_record_that_names_no_ir():
    """Why a record with no IR is cut as well: the fingerprint rule lets it through and
    ``ll_table(grammar=...)`` is a basis once a check matches its grammar."""
    grammar = {"nonterminals": ["S"], "terminals": ["c"], "start": "S", "rules": [{"lhs": "S", "rhs": ["c"]}]}
    report = {"verdict": "LL(1)", "language_class": "ll"}
    binding = {"status": "approved", "ir_fingerprint": "fp-a", "auto": False}
    table = {"id": "t", "tool": "ll_table", "ok": True, "trust": "verified", "args": {"grammar": grammar},
             "data": {"grammar_source": "grammar", "mode": "check", "k": 1}}
    check = {"id": "c", "tool": "grammar_vs_ir", "ok": True, "trust": "bounded_pass",
             "args": {"grammar": grammar}, "data": {"ir_fingerprint": "fp-a"}}
    assert A.verdict_gate(report, [], [], formalization=binding)["trust"] == "not_verified"
    assert A.verdict_gate(report, [table, check], [], formalization=binding)["trust"] == "bounded_pass"


def test_a_new_report_after_a_new_ir_binds_to_it_and_gets_a_revision(tmp_path):
    script = H.base_script()
    new_report = H.report_call(script, call_id="call-report-b", verdict="LL(1)", summary="Отчёт по новой грамматике.",
                               evidence_ids=["ev-table-b"], ir_fingerprint=ir_fingerprint(LL_IR_B))
    add_ir_b(script, LL_IR_B, "ll",
             {"tool_calls": [{"name": "ll_table", "id": "ev-table-b", "args": {"ir": LL_IR_B, "k": 1}}]}, new_report)
    out = start(tmp_path, script)
    out = hitl.resume_run(out.run_dir, APPROVE)
    d = hitl.resume_run(out.run_dir, APPROVE).run_dir
    before = rating(d)
    to_ir_b_review(d)
    out = hitl.resume_run(d, APPROVE)
    assert out.pending.point == hitl.REPORT
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted"
    after, result = rating(d), result_json(d)
    fp = hitl.load_run(d)["reviews"][-2]["ir_fingerprint"]
    assert after["formalization"]["ir_fingerprint"] == fp != before["formalization"]["ir_fingerprint"]
    assert after["revision"] == before["revision"] + 1 and after["digest"] != before["digest"]
    assert result["basis"] == ["ev-table-b"]
    assert hitl.load_run(d)["accepted_report"]["call_id"] == "call-report-b"


def report_after_ir_b(tmp_path, *, decision=APPROVE, **report_fields):
    """A finished LL run, IR B reviewed in a follow-up, then a rewritten report with ``report_fields``."""
    script = H.base_script()
    rewritten = H.report_call(script, call_id="call-report-2", summary="Отчёт переписан.", **report_fields)
    add_ir_b(script, LL_IR_B, "ll",
             {"tool_calls": [{"name": "ll_table", "id": "ev-table-b", "args": {"ir": LL_IR_B, "k": 1}}]}, rewritten)
    out = start(tmp_path, script)
    a_fp = out.pending.request["action_requests"][0]["args"]["ir_fingerprint"]
    out = hitl.resume_run(out.run_dir, APPROVE)
    d = hitl.resume_run(out.run_dir, APPROVE).run_dir
    before = rating(d)
    to_ir_b_review(d)
    out = hitl.resume_run(d, decision)
    assert out.pending.point == hitl.REPORT
    card = out.pending.card
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted", out.result
    return d, a_fp, before, card


def test_a_report_on_the_old_ir_after_a_new_one_is_rated_by_the_old_ir(tmp_path):
    a_fp = ir_fingerprint(H.base_script()["defs"]["ir"])
    d, seen, before, _ = report_after_ir_b(tmp_path, ir_fingerprint=a_fp)
    assert seen == a_fp
    result = result_json(d)
    assert result["required_ir_fingerprint"] == a_fp and result["formalization"]["ir_fingerprint"] == a_fp
    assert result["trust"] == before["trust"] != "not_verified"
    assert "ev-table-b" in result["excluded_ir_mismatch"] and "ev-table-b" not in result["basis"]


def test_a_report_naming_no_approved_ir_has_no_basis_when_several_stand(tmp_path):
    d, _, _, card = report_after_ir_b(tmp_path, ir_fingerprint=None)
    result = result_json(d)
    assert result["trust"] == "not_verified" and result["basis"] == []
    assert result["formalization"]["status"] == "ambiguous"
    assert any("names none of them" in g for g in result["verdict_gate"])
    assert "names none of them" in card
    assert "утверждено несколько IR" in (d / "result.md").read_text(encoding="utf-8")


def test_a_report_naming_an_unknown_ir_has_no_basis_when_several_stand(tmp_path):
    d, _, _, _ = report_after_ir_b(tmp_path, ir_fingerprint="ir-nobody-approved")
    result = result_json(d)
    assert result["trust"] == "not_verified" and result["formalization"]["status"] == "ambiguous"


def test_a_rejected_new_ir_still_blocks_a_new_report_that_names_the_old_one(tmp_path):
    """The last decision rejected: stays ``not_verified``, the safe side of R-IR."""
    a_fp = ir_fingerprint(H.base_script()["defs"]["ir"])
    d, _, _, _ = report_after_ir_b(tmp_path, decision=REJECT, ir_fingerprint=a_fp)
    result = result_json(d)
    assert result["trust"] == "not_verified" and result["formalization"]["status"] == "rejected"


def _ir_rec(call_id, decision, fp):
    return {"id": call_id, "point": hitl.FORMALIZATION, "decision": decision, "ir_fingerprint": fp}


def test_one_approved_ir_lists_nothing_and_a_rejected_one_is_not_listed():
    one = hitl.formalization_binding([_ir_rec("1", "approved", "a"), _ir_rec("2", "unchanged", "a")], on=True)
    assert "approved_irs" not in one
    dropped = hitl.formalization_binding(
        [_ir_rec("1", "approved", "a"), _ir_rec("2", "rejected", "b"), _ir_rec("3", "unchanged", "a")], on=True)
    assert "approved_irs" not in dropped
    two = hitl.formalization_binding(
        [_ir_rec("1", "approved", "a"), _ir_rec("2", "approved", "b"), _ir_rec("3", "unchanged", "a")], on=True)
    assert [ir["ir_fingerprint"] for ir in two["approved_irs"]] == ["b", "a"]


def test_the_named_ir_brings_its_own_human_review():
    """A human approved only B: a report on auto-approved A is not "human-approved"."""
    state = {hitl.STATE_KEY: [_ir_rec("1", "approved", "a"), _ir_rec("2", "approved", "b")]}
    run = {"hitl": {hitl.FORMALIZATION: True},
           "reviews": [{"point": hitl.FORMALIZATION, "source": "auto", "decision": "approved", "ir_fingerprint": "a"},
                       {"point": hitl.FORMALIZATION, "source": "human", "decision": "approved", "ir_fingerprint": "b"}]}
    binding = hitl.run_binding(run, state)
    assert binding["auto"] is False  # the last IR
    on_a = A.binding_for_report(binding, {"ir_fingerprint": "a"})
    assert on_a["auto"] is True and on_a["ir_fingerprint"] == "a" and on_a["review_id"] == "1"
    on_b = A.binding_for_report(binding, {"ir_fingerprint": "b"})
    assert on_b["auto"] is False and on_b["ir_fingerprint"] == "b"
    assert A.binding_for_report(on_a, {"ir_fingerprint": "a"}) == on_a  # the gate narrows it again


def test_review_mode_auto_keeps_the_old_reports_rating_too(tmp_path):
    script = add_ir_b(H.base_script(), LL_IR_B, "ll", {"content": ANSWER})
    out = start(tmp_path, script, review_mode="auto")
    d = out.run_dir
    assert out.status == "submitted", out.result
    before = rating(d)
    out = hitl.send_followup(d, NEW_TASK)
    assert out.status == "submitted" and out.reply == ANSWER
    assert hitl.load_run(d)["reviews"][-1]["reviewed_call_id"] == "call-ir-builder-b"
    assert rating(d) == before


def test_a_first_run_is_rated_over_all_its_records(tmp_path):
    d = finished(tmp_path)
    run, ledger = hitl.load_run(d), EvidenceLedger.load(d)
    assert run["accepted_report"] == {"call_id": "call-report", "evidence_count": len(ledger.records())}


PROBE = {"name": "nerode_probe", "id": "ev-later", "args": {"ir": {"$def": "ir"}, "pairs": [{"u": "a", "v": "b"}]}}


def fail_to_write(*_a, **_k):
    raise OSError("диск заполнен")


def test_an_abandoned_follow_up_leaves_no_baseline_of_its_report(tmp_path, monkeypatch):
    mock = json.loads((H.MOCK / "reg_followup.json").read_text(encoding="utf-8"))
    second = copy.deepcopy(mock["models"]["main"][6])
    second["tool_calls"][0]["id"] = "call-report-2"
    second["tool_calls"][0]["args"]["summary"] = "Отчёт, который не записался."
    d = finished_reg(tmp_path, second, {"tool_calls": [PROBE]}, {"content": ANSWER})
    before, baseline = rating(d), hitl.load_run(d)["accepted_report"]
    assert hitl.send_followup(d, "Перепиши отчёт").pending.point == hitl.REPORT
    with monkeypatch.context() as m:
        m.setattr(hitl, "_write_result", fail_to_write)
        with pytest.raises(OSError):
            hitl.resume_run(d, APPROVE)
    assert "followup_in_progress" not in hitl.load_run(d)
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER
    assert "ev-later" in {r.id for r in EvidenceLedger.load(d).records()}
    assert rating(d) == before
    assert hitl.load_run(d)["accepted_report"] == baseline


def no_report_run(tmp_path, *after):
    script = H.base_script()
    script["models"]["main"] = [{"content": "Отчёта пока нет."}, *after]
    settings = H.example_settings()
    settings["hitl"].update({"formalization": False, "report": False})
    out = start(tmp_path, script, settings=settings)
    assert out.status == "incomplete", out.result
    return out.run_dir


def test_an_abandoned_follow_up_on_a_run_with_no_report_leaves_no_baseline(tmp_path, monkeypatch):
    report = H.report_call(H.base_script(), call_id="call-first", evidence_ids=[])
    for item in [*(report["tool_calls"][0]["args"].get("steps") or []), *(report["tool_calls"][0]["args"].get("claims") or [])]:
        item["evidence_ids"] = []  # the run has no evidence: an id with no record is refused
    d = no_report_run(tmp_path, report)
    with monkeypatch.context() as m:
        m.setattr(hitl, "_write_result", fail_to_write)
        with pytest.raises(OSError):
            hitl.send_followup(d, QUESTION)
    assert "accepted_report" not in hitl.load_run(d)


def test_a_run_with_no_report_takes_no_baseline_when_a_follow_up_starts(tmp_path):
    d = no_report_run(tmp_path, {"content": ANSWER})
    assert hitl.send_followup(d, QUESTION).reply == ANSWER
    assert "accepted_report" not in hitl.load_run(d)


def test_a_run_with_no_baseline_takes_it_when_the_follow_up_starts(tmp_path):
    d = finished_reg(tmp_path, {"tool_calls": [PROBE]}, {"content": ANSWER})
    before = rating(d)
    run = hitl.load_run(d)
    baseline = run.pop("accepted_report")
    hitl.save_run(d, run)
    out = hitl.send_followup(d, QUESTION)
    assert out.reply == ANSWER
    assert "ev-later" in {r.id for r in EvidenceLedger.load(d).records()}
    assert rating(d) == before
    assert hitl.load_run(d)["accepted_report"] == baseline


def test_a_later_human_approval_of_the_same_ir_does_not_lift_an_auto_approved_report(tmp_path):
    script = json.loads((H.MOCK / "reg_followup.json").read_text(encoding="utf-8"))
    script["models"]["main"] = script["models"]["main"][:7]  # up to the report
    ir_a = script["defs"]["ir"]
    add_ir_b(script, REG_IR_B, "reg", {"content": ANSWER})
    out = start(tmp_path, script, review_mode="auto")
    d = out.run_dir
    assert out.status == "submitted", out.result
    before = rating(d)
    assert before["formalization"]["auto"] is True and before["trust"] != "verified"
    # reviews are back with the human for the next message
    run = hitl.load_run(d)
    run.update(review_mode="all", auto_approve=False)
    hitl.save_run(d, run)
    # an IR already approved in this thread has no stop of its own (``unchanged``), so a human
    # approves that fingerprint by editing another IR back to it
    to_ir_b_review(d)
    edit = hitl.parse_decision("edit", json.dumps({"ir": ir_a}, ensure_ascii=False))
    out = hitl.resume_run(d, edit)
    assert out.status == "submitted" and out.reply == ANSWER
    review = hitl.load_run(d)["reviews"][-1]
    assert review["source"] != "auto" and review["decision"] == "edited"
    assert review["reviewed_call_id"] == "call-ir-builder-b"
    assert review["ir_fingerprint"] == before["formalization"]["ir_fingerprint"]
    assert rating(d) == before


# ---------------------------------------------------------------------------
# A HITL 2 draft in a follow-up shows the draft
# ---------------------------------------------------------------------------

DRAFT_TEXT = "Новый текст отчёта из черновика."


def at_a_draft(tmp_path, *after):
    """A finished LL run stopped at HITL 2 on a follow-up's draft with a verdict of its own."""
    draft = H.report_call(H.base_script(), call_id="call-draft", verdict="не LL(1)", summary=DRAFT_TEXT)
    d = finished(tmp_path, draft, *after)
    before = rating(d)
    assert before["trust"] == "verified" and before["cap"] == 0.98
    assert hitl.send_followup(d, "Перепиши отчёт").pending.point == hitl.REPORT
    return d, before


def test_a_draft_in_a_follow_up_shows_its_own_text_and_rating(tmp_path):
    d, before = at_a_draft(tmp_path)
    result = result_json(d)
    assert result["status"] == hitl.STATUS_AWAITING and result["report"]["summary"] == DRAFT_TEXT
    assert result["report"]["verdict"] == "не LL(1)"
    assert DRAFT_TEXT in (d / "result.md").read_text(encoding="utf-8")
    draft = hitl.load_run(d)["draft"]
    assert draft["verdict"] == "не LL(1)"
    assert draft["trust"] == "not_verified" and draft["confidence_cap"] == 0.40
    # the short and the long card carry the draft's verdict and rating; its text is in result.md
    assert "не LL(1)" in (d / hitl.CARD_FILE).read_text(encoding="utf-8")
    assert "Доверие: not_verified · потолок 0.40" in hitl.load_run(d)["pending"]["card"]["summary"]


def test_approving_a_draft_in_a_follow_up_gives_the_new_report_and_a_revision(tmp_path):
    d, before = at_a_draft(tmp_path)
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted"
    after, result = rating(d), result_json(d)
    assert result["report"]["summary"] == DRAFT_TEXT and DRAFT_TEXT in (d / "result.md").read_text(encoding="utf-8")
    assert after["revision"] == before["revision"] + 1 and after["digest"] != before["digest"]
    assert after["trust"] == "not_verified" and after["cap"] == 0.40
    assert hitl.load_run(d)["accepted_report"]["call_id"] == "call-draft"


def test_rejecting_a_draft_in_a_follow_up_keeps_the_old_report(tmp_path):
    d, before = at_a_draft(tmp_path, {"content": ANSWER})
    out = hitl.resume_run(d, REJECT)
    assert out.status == "submitted" and out.reply == ANSWER
    assert rating(d) == before
    assert result_json(d)["report"]["summary"] != DRAFT_TEXT
    assert DRAFT_TEXT not in (d / "result.md").read_text(encoding="utf-8")
    assert hitl.load_run(d)["accepted_report"]["call_id"] == "call-report"


def test_a_first_run_draft_is_unchanged(tmp_path):
    out = start(tmp_path, H.base_script())
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.pending.point == hitl.REPORT
    d = out.run_dir
    result = result_json(d)
    assert result["status"] == hitl.STATUS_AWAITING
    assert hitl.load_run(d)["draft"]["trust"] == "verified"


# ---------------------------------------------------------------------------
# «Завершить с тем, что есть» in a follow-up on a run with an accepted report
# ---------------------------------------------------------------------------

UNANSWERED_NOTE = "сообщение без ответа: бюджет не продлён, отчёт прежний"


def main_calls(d):
    return json.loads((d / hitl.MOCK_CURSORS_FILE).read_text())["cursors"]["main"]


def spent(d):
    return result_json(d)["budget"]["spent_usd"]


#: dollars left after the report: a call that would enter wrap-up, one that would be refused
ENTERS_WRAP_UP, REFUSED = 0.4, 0.001


def low_followup(tmp_path, left):
    d = finished(tmp_path, {"content": ANSWER})
    run = hitl.load_run(d)
    run["settings"]["budget"].update({"per_task_usd": spent(d) + left, "reserve_for_report_usd": left * 0.99})
    hitl.save_run(d, run)
    return d


def assert_unanswered(d, out, before, *, calls, money):
    assert out.status == "submitted" and out.exit_code == hitl.EXIT_SUBMITTED and out.reply is None
    assert out.followup_note == UNANSWERED_NOTE
    assert main_calls(d) == calls, "no further model call"
    assert spent(d) == pytest.approx(money), "nothing charged"
    assert rating(d) == before
    assert "followup_in_progress" not in hitl.load_run(d)
    assert kinds(d).count("done") == 1 and "assistant_message" not in kinds(d)
    assert [e["text"] for e in events(d) if e["event"] == "followup_unanswered"] == [UNANSWERED_NOTE]


LOW = pytest.mark.parametrize("left", [ENTERS_WRAP_UP, REFUSED], ids=["would_enter_wrap_up", "would_be_refused"])


@LOW
def test_declining_at_the_stop_of_a_follow_up_makes_no_model_call(tmp_path, left):
    d = low_followup(tmp_path, left)
    before, money, calls = rating(d), spent(d), main_calls(d)
    assert hitl.send_followup(d, QUESTION).pending.point == hitl.BUDGET
    out = hitl.resume_run(d, APPROVE)  # a new process: budget_declined is already set when the node runs again
    assert_unanswered(d, out, before, calls=calls, money=money)
    assert hitl.load_run(d)["budget_declined"] is True


@LOW
def test_declining_in_the_same_process_makes_no_model_call(tmp_path, left):
    d = low_followup(tmp_path, left)
    before, money, calls = rating(d), spent(d), main_calls(d)
    out = hitl.send_followup(d, QUESTION, dialog=lambda _info: APPROVE)
    assert_unanswered(d, out, before, calls=calls, money=money)


@LOW
def test_a_top_up_too_small_for_the_call_ends_the_same_way(tmp_path, left):
    d = low_followup(tmp_path, left)
    before, money, calls = rating(d), spent(d), main_calls(d)
    assert hitl.send_followup(d, QUESTION).pending.point == hitl.BUDGET
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 0.0001}))
    assert_unanswered(d, out, before, calls=calls, money=money)
    run = hitl.load_run(d)
    assert len([r for r in run["reviews"] if r["point"] == hitl.BUDGET]) == 1  # asked once
    assert run["budget_extensions"][-1]["add_usd"] == 0.0001


@LOW
def test_an_auto_approved_run_keeps_its_accepted_report_when_the_follow_up_has_no_money(tmp_path, left):
    """CA-015, decided 2026-11-10: no budget stop (--auto-approve) does not mean the report may be
    replaced by a truncated one; the call is skipped and the accepted report and label stay."""
    script = H.base_script()
    script["models"]["main"] += [{"content": ANSWER}]
    out = start(tmp_path, script, auto_approve=True)
    assert out.status == "submitted", out.result
    d = out.run_dir
    assert hitl.load_run(d)["budget_stop"] is False
    run = hitl.load_run(d)
    run["settings"]["budget"].update({"per_task_usd": spent(d) + left, "reserve_for_report_usd": left * 0.99})
    hitl.save_run(d, run)
    before, money, calls = rating(d), spent(d), main_calls(d)
    report, label = result_json(d)["report"], result_json(d)["human_review"]
    out = hitl.send_followup(d, QUESTION)
    assert_unanswered(d, out, before, calls=calls, money=money)
    assert result_json(d)["report"] == report and result_json(d)["human_review"] == label


def test_the_next_message_after_an_unanswered_one_asks_for_money_again(tmp_path):
    d = finished(tmp_path, {"content": ANSWER})
    run = hitl.load_run(d)
    run["settings"]["budget"].update({"per_task_usd": spent(d) + REFUSED, "reserve_for_report_usd": REFUSED * 0.99})
    hitl.save_run(d, run)
    hitl.send_followup(d, QUESTION)
    hitl.resume_run(d, APPROVE)
    out = hitl.send_followup(d, QUESTION)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 5}))
    assert out.reply == ANSWER


def test_declining_a_follow_up_with_no_accepted_report_still_gets_its_report_from_wrap_up(tmp_path):
    script = H.base_script()
    late = H.report_call(script, call_id="call-report-late", summary="Отчёт из остатка.", evidence_ids=[])
    args = late["tool_calls"][0]["args"]
    for item in [*(args.get("steps") or []), *(args.get("claims") or [])]:
        item["evidence_ids"] = []  # the run has no evidence yet: an id with no record is refused
    script["models"]["main"] = [{"content": "Отчёта пока нет."}, late]
    out = start(tmp_path, script, settings=no_review_settings(per_task=0.4, reserve=0.1))
    assert out.pending.point == hitl.BUDGET
    d = out.run_dir
    out = hitl.resume_run(d, APPROVE)
    assert out.status != "submitted" and main_calls(d) == 1, out.result
    assert hitl.send_followup(d, QUESTION).pending.point == hitl.BUDGET
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted" and main_calls(d) == 2, out.result
    assert out.followup_note is None and result_json(d)["report"]["summary"] == "Отчёт из остатка."


def test_a_message_with_no_accepted_report_keeps_the_old_wrap_up():
    assert BS.declining_skips_the_call({"budget_stop": True, "followup_in_progress": {"previous_status": "submitted"}})
    assert not BS.declining_skips_the_call({"budget_stop": True,
                                            "followup_in_progress": {"previous_status": "budget_exhausted"}})
    assert not BS.declining_skips_the_call({"budget_stop": True})  # the run itself: wrap-up writes its report
    # user decision 2026-11-10: a run with no budget stop (--auto-approve) is protected too
    assert BS.declining_skips_the_call({"budget_stop": False,
                                        "followup_in_progress": {"previous_status": "submitted"}})
    assert not BS.declining_skips_the_call({"budget_stop": False})


def test_a_resumed_followup_records_its_own_pid(tmp_path, monkeypatch):
    new = H.report_call(H.base_script(), call_id="call-report-2", summary="Отчёт после вопроса в чате.")
    d = finished(tmp_path, new)
    hitl.send_followup(d, "Перепиши сводку")
    run = hitl.load_run(d)
    run["followup_in_progress"]["pid"] = 999999  # the first process is gone
    run_store.save_run(d, run)
    seen = []
    real = hitl.save_run

    def spy(path, data):
        if data.get("followup_in_progress"):
            seen.append(data["followup_in_progress"].get("pid"))
        return real(path, data)

    monkeypatch.setattr(hitl, "save_run", spy)
    hitl.resume_run(d, APPROVE)
    assert os.getpid() in seen and 999999 in seen[:1]  # an earlier save may still carry the old pid; the segment's own save does not
    assert seen[-1] == os.getpid()


def test_abandon_force_skips_the_pid_check(tmp_path, capsys):
    from tfl_deep_agent import cli

    d = tmp_path / "r"
    d.mkdir()
    (d / "result.json").write_text("{}", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        run_store.save_run(d, {"run_id": "x", "status": "running", "reviews": [], "followup_in_progress": {
            "snapshot": followup.snapshot_report(d), "previous_status": "submitted", "pid": child.pid,
            "n_reviews": 0, "review_switch": {}}})
        assert cli.main(["--resume", str(d), "--abandon"]) == 2
        assert "--force" in capsys.readouterr().err
        assert cli.main(["--resume", str(d), "--abandon", "--force"]) == 0
    finally:
        child.kill()
        child.wait()
    assert "followup_in_progress" not in run_store.load_run(d)


def test_resume_exits_2_when_the_follow_up_cannot_start(tmp_path, monkeypatch, capsys):
    from tfl_deep_agent import cli

    d = finished(tmp_path)

    def boom(*_a, **_k):
        raise ImportError("no module named deepagents")

    monkeypatch.setattr(hitl, "send_followup", boom)
    assert cli.main(["--resume", str(d), "--message", "hi"]) == 2
    assert "no module named deepagents" in capsys.readouterr().err


def test_budget_stop_helper_names():
    assert BS.format_usd(2) == "$2.00" and BS.format_usd(0.0123) == "$0.0123"
    assert BS.parse_add_usd({"add_usd": "1.5"}) == 1.5
    with pytest.raises(BS.DecisionError):
        BS.parse_add_usd({"add_usd": 0})
    assert not hasattr(BS, "usd") and not hasattr(BS, "budget_add") and not hasattr(BS, "budget_resume")


def test_snapshot_report_takes_a_run_dir_as_text(tmp_path):
    from tfl_deep_agent import followup as F

    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    assert F.latest_snapshot(str(tmp_path)) == 0
    assert F.snapshot_report(str(tmp_path)) == "report_history/1"
    assert F.latest_snapshot(tmp_path) == 1


@pytest.mark.parametrize("extra", [["--message", "x"], ["--decision", "approve"]])
def test_cli_resume_rejects_force_without_abandon(tmp_path, capsys, extra):
    from tfl_deep_agent import cli

    with pytest.raises(SystemExit) as stop:
        cli.main(["--resume", str(tmp_path), "--force", *extra])
    assert stop.value.code == run_store.EXIT_USAGE and "--force" in capsys.readouterr().err


def test_rejecting_a_byte_identical_report_keeps_the_accepted_label(tmp_path):
    """API-025: the digest filter ignores a rejection of the same bytes when an approval holds that digest."""
    d = finished(tmp_path, H.report_call(H.base_script(), call_id="call-same"), {"content": "Оставляю прежний отчёт."})
    approved = result_json(d)
    assert hitl.send_followup(d, "Повтори отчёт").pending.point == hitl.REPORT
    out = hitl.resume_run(d, REJECT)
    assert out.status == "submitted"
    result = result_json(d)
    assert result["report"] == approved["report"]
    assert result["human_review"] == "approved by a human at HITL 2"
    reviews = report_reviews(hitl.load_run(d))
    assert [r["decision"] for r in reviews] == ["approved", "rejected"]
    assert reviews[0]["report_digest"] == reviews[1]["report_digest"]


def test_review_summary_ignores_a_rejection_of_the_same_digest_only_when_an_approval_holds_it():
    report = {"verdict": "regular", "summary": "s"}
    digest = followup.report_digest(report)
    on = {"hitl": {"report": True}}
    rows = [{"point": "report", "decision": "approved", "report_digest": digest},
            {"point": "report", "decision": "rejected", "report_digest": digest}]
    assert hitl.review_summary({**on, "reviews": rows}, report)[0] == "approved by a human at HITL 2"
    assert "rejected" in hitl.review_summary({**on, "reviews": rows[1:]}, report)[0]
    assert hitl.review_summary({**on, "reviews": rows[::-1]}, report)[0] == "approved by a human at HITL 2"


# ---------------------------------------------------------------------------
# A follow-up message that leads to a new IR review: «Условие» stays, «Уточнение» is added
# ---------------------------------------------------------------------------

def _clarified_run(tmp_path, *, script=None, task=TASK):
    """A finished LL run; its follow-up NEW_TASK reviews IR B, then a new report is submitted and accepted."""
    script = add_ir_b(script or H.base_script(), LL_IR_B, "ll", draft_reply("Отчёт после уточнения.", "call-draft-b"))
    out = hitl.start_run(tmp_path / "cl", task, settings=H.example_settings(), mock_script=script)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, APPROVE)
    assert out.status == "submitted", out.result
    d = out.run_dir
    to_ir_b_review(d)
    out = hitl.resume_run(d, APPROVE)
    assert out.awaiting and out.pending.point == hitl.REPORT, out.result
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted" and result_json(d)["report"]["summary"] == "Отчёт после уточнения."
    return d


def test_a_correction_follow_up_keeps_the_original_task_and_shows_the_clarification(tmp_path):
    d = _clarified_run(tmp_path)
    result = result_json(d)
    assert result["task"] == TASK
    assert result["clarifications"] == [NEW_TASK]
    md = (d / "result.md").read_text(encoding="utf-8")
    assert md.index("## Условие") < md.index(TASK[:20]) < md.index("## Уточнение") < md.index(NEW_TASK)
    html = (d / "result.html").read_text(encoding="utf-8")
    assert "Уточнение" in html and NEW_TASK in html
    review = [r for r in hitl.load_run(d)["reviews"] if r["point"] == hitl.FORMALIZATION][-1]
    assert review["followup_text"] == NEW_TASK and review["task_text"] == TASK


def test_the_hitl1_card_of_a_follow_up_review_shows_the_clarification(tmp_path):
    script = add_ir_b(H.base_script(), LL_IR_B, "ll")
    out = hitl.start_run(tmp_path / "card", TASK, settings=H.example_settings(), mock_script=script)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.resume_run(out.run_dir, APPROVE)
    out = hitl.send_followup(out.run_dir, NEW_TASK)
    args = out.pending.request["action_requests"][0]["args"]
    assert args["task_text"] == TASK and args["followup_text"] == NEW_TASK
    card = (out.run_dir / hitl.CARD_FILE).read_text(encoding="utf-8")
    assert "Clarification" in card and "другая задача" in card


def test_a_file_task_keeps_its_transcription_and_gets_the_clarification(tmp_path):
    from tfl_deep_agent.intake import files as intake_files

    pdf = tmp_path / "task.pdf"
    pdf.write_bytes(H.make_pdf())
    d = _clarified_run(tmp_path, script=H.pdf_script(), task=intake_files.from_path(pdf))
    result = result_json(d)
    assert result["task"] == H.INTAKE_TRANSCRIPTION
    assert result["clarifications"] == [NEW_TASK]


def test_several_clarifications_are_listed_in_order():
    from tfl_deep_agent.reports import render

    md = render.render_report({"task": "TASKTEXT", "clarifications": ["первое", "второе"],
                               "status": "incomplete"}).markdown
    assert md.index("TASKTEXT") < md.index("## Уточнение") < md.index("**1.** первое") < md.index("**2.** второе")
    assert "Уточнение" not in render.render_report({"task": "T", "status": "incomplete"}).markdown


def test_the_task_keeps_its_stars_outside_math_and_katex_is_untouched():
    from tfl_deep_agent.reports import render

    esc = render.escape_stars_outside_math
    assert esc("(ab)* и {a,b}*") == "(ab)\\* и {a,b}\\*"
    assert esc("$a^*$ и $$b^*$$") == "$a^*$ и $$b^*$$"
    assert esc("(ab)* где $L=\\{a^n\\}^*$ и c*") == "(ab)\\* где $L=\\{a^n\\}^*$ и c\\*"
    assert esc("`(ab)*` и c*") == "`(ab)*` и c\\*"
    assert esc("``a*`` и \\(a^*\\) и \\[b^*\\] и c*") == "``a*`` и \\(a^*\\) и \\[b^*\\] и c\\*"
    html = render.render_report({"task": "Язык (ab)* ... {a,b}* и $x^*$", "status": "incomplete"}).html
    main = html.split("<main>")[1].split("</main>")[0]
    assert "(ab)&#42; ... {a,b}&#42;" in main and "<em>" not in main and "$x^*$" in main


# ---------------------------------------------------------------------------
# CA-015, the day case: the day's remainder is what is short in a follow-up
# ---------------------------------------------------------------------------

def day_short_followup(tmp_path, **start_kw):
    """A finished live run (scripted model) whose day has almost nothing left."""
    script = H.base_script()
    script["models"]["main"].append({"content": ANSWER})

    def live():
        return live_factory(script, tmp_path)

    out = hitl.start_run(tmp_path / "lv", TASK, settings=H.example_settings(), model_factory=live(), **start_kw)
    while out.awaiting:
        out = hitl.resume_run(out.run_dir, APPROVE, model_factory=live(), live=True)
    assert out.status == "submitted", out.result
    d = out.run_dir
    run = hitl.load_run(d)
    log = B.DailySpendLog()
    log.append({"day": log.today(), "task_id": "other", "usd": 1.5})  # the validator wants per_task <= per_day
    per_day = log.day_total() + 0.0005
    run["settings"]["budget"].update({"per_day_usd": per_day, "per_task_usd": 1.0, "reserve_for_report_usd": 0.0001})
    hitl.save_run(d, run)
    return d, live, per_day


def live_calls(tmp_path):
    return json.loads((tmp_path / "cursors.json").read_text())["cursors"]["main"]


def test_a_short_day_asks_to_raise_the_day_limit_and_declining_keeps_the_report(tmp_path):
    d, live, per_day = day_short_followup(tmp_path)
    before, calls = rating(d), live_calls(tmp_path)
    out = hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    args = out.pending.request["action_requests"][0]["args"]
    assert args["binds"] == "day" and args["per_day_usd"] == pytest.approx(per_day, abs=1e-5)
    assert "Дневной лимит" in out.pending.request["action_requests"][0]["description"]
    card = hitl.load_run(d)["pending"]["card"]
    assert card["summary"][0].startswith("Сегодня потрачено")
    done = hitl.resume_run(d, APPROVE, model_factory=live(), live=True)
    assert done.status == "submitted" and done.followup_note == UNANSWERED_NOTE and done.reply is None
    assert live_calls(tmp_path) == calls, "no model call"
    assert rating(d) == before
    assert hitl.load_run(d)["budget_declined"] is True


def test_raising_the_day_limit_lets_the_follow_up_run(tmp_path):
    d, live, per_day = day_short_followup(tmp_path)
    assert hitl.send_followup(d, QUESTION, model_factory=live(), live=True).pending.point == hitl.BUDGET
    # the remainder is no cap on a day raise
    done = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert done.reply == ANSWER
    run = hitl.load_run(d)
    assert run["budget_day_limit_usd"] == pytest.approx(per_day + 50)
    assert run["settings"]["budget"]["per_day_usd"] == per_day  # the settings snapshot does not change
    assert BS.per_day_usd(run) == pytest.approx(per_day + 50)
    ext = run["budget_extensions"][-1]
    assert ext["binds"] == "day" and ext["add_usd"] == 50


def test_a_short_day_with_nobody_to_ask_skips_the_call_instead_of_wrapping_up(tmp_path):
    d, live, _ = day_short_followup(tmp_path, auto_approve=True)
    assert hitl.load_run(d)["budget_stop"] is False
    before, calls = rating(d), live_calls(tmp_path)
    out = hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    assert out.status == "submitted" and out.followup_note == UNANSWERED_NOTE
    assert live_calls(tmp_path) == calls and rating(d) == before


def test_the_day_case_is_asked_only_in_a_follow_up_of_an_accepted_report():
    est = B.CallEstimate("claude-opus-5-5", 1000, 1000, 0.03)
    log = B.DailySpendLog()
    led = B.BudgetLedger(5.0, reserve_for_report_usd=0.5, spend_log=log, per_day_usd=0.002, task_id="t")
    mw = B.BudgetMiddleware(led.root, fit_max_tokens=False)
    assert mw._budget_low_locked(est, led.root) is None
    mw.skip_call_when_low = True
    low = mw._budget_low_locked(est, led.root)
    assert low.binds == "day" and low.per_day_usd == 0.002
    assert led.raise_day_limit(7.0) == 7.0 and led.raise_day_limit(1.0) == 7.0


def test_a_day_raise_holds_only_on_the_day_it_was_given():
    run = {"settings": {"budget": {"per_day_usd": 2.0}}}
    BS.apply_budget_review(run, {"decision": BS.EXTENDED, "binds": "day", "new_day_limit_usd": 9.0, "add_usd": 7.0})
    today = run["budget_day_limit_day"]
    assert BS.per_day_usd(run) == 9.0 and BS.day_limit_raise(run, today) == 9.0
    assert BS.day_limit_raise(run, "1999-01-01") == 0.0
    run["budget_day_limit_day"] = "1999-01-01"  # given on an earlier day
    assert BS.per_day_usd(run) == 2.0


def test_the_day_card_is_titled_for_the_day(tmp_path):
    d, live, _ = day_short_followup(tmp_path)
    assert hitl.send_followup(d, QUESTION, model_factory=live(), live=True).pending.point == hitl.BUDGET
    assert hitl.load_run(d)["pending"]["card"]["title"] == "Дневной лимит заканчивается"


def _stored_answer(monkeypatch, edited_args):
    import langgraph.types

    monkeypatch.setattr(langgraph.types, "interrupt", lambda value: {"decisions": [
        {"type": "edit", "edited_action": {"name": "budget", "args": edited_args}}]})


@pytest.mark.parametrize("asked, stored, expected", [
    ("task", {"add_usd": 5, "new_day_limit_usd": 9.0}, ("day", 9.0)),  # day first, task after
    ("day", {"add_usd": 5, "new_limit_usd": 6.0}, ("task", 6.0)),      # task first, day after
])
def test_a_resumed_answer_of_the_other_kind_is_applied_as_what_it_is(monkeypatch, asked, stored, expected):
    """The re-run node's interrupt() returns the stored answer of the EARLIER question."""
    _stored_answer(monkeypatch, stored)
    low = B.BudgetLow(spent_usd=1.0, limit_usd=1.002, available_usd=0.001, reserve_usd=0.0001, day_left_usd=0.0005,
                      binds=asked, per_day_usd=2.0 if asked == "day" else None)
    assert BS.request_budget_extension(low) == expected


def test_the_middleware_applies_the_kind_of_the_answer_not_of_the_question():
    est = B.CallEstimate("claude-opus-5-5", 1000, 1000, 0.03)
    led = B.BudgetLedger(0.05, reserve_for_report_usd=0.04, spend_log=B.DailySpendLog(), per_day_usd=0.002, task_id="t")
    mw = B.BudgetMiddleware(led.root, fit_max_tokens=False)
    mw.skip_call_when_low = True
    mw.request_budget_extension = lambda low: ("task", 9.0)  # the question was about the day
    mw._ask_for_budget(est, led.root)
    assert led.root.limit == 9.0 and led.per_day_usd == 0.002
    mw.request_budget_extension = lambda low: ("day", 7.0)
    mw._ask_for_budget(est, led.root)
    assert led.per_day_usd == 7.0


def test_a_declined_day_card_is_recorded_as_a_day_card_and_reaches_progress(tmp_path):
    from tfl_deep_agent.progress import ProgressEmitter

    low = B.BudgetLow(spent_usd=1.0, limit_usd=2.0, available_usd=0.001, reserve_usd=0.0, day_left_usd=0.0005,
                      binds="day", per_day_usd=2.0)
    action = BS.budget_request(low)["action_requests"][0]
    _, review = BS.budget_decision_response(action, hitl.Decision("approve"))
    assert review["decision"] == BS.DECLINED and review["binds"] == "day"
    pe = ProgressEmitter(tmp_path)
    pe.hitl_decision({**review, "new_day_limit_usd": 9.0})
    event = json.loads((tmp_path / "progress.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["binds"] == "day" and event["new_day_limit_usd"] == 9.0
    task = B.BudgetLow(spent_usd=1.0, limit_usd=2.0, available_usd=0.001, reserve_usd=0.0, day_left_usd=5.0)
    _, review = BS.budget_decision_response(BS.budget_request(task)["action_requests"][0], hitl.Decision("approve"))
    assert "binds" not in review


def test_both_limits_short_the_money_of_two_answers_lets_the_same_message_run(tmp_path):
    """The task limit and the day's remainder are both short: one question per kind, and
    the SAME message gets its answer once both are raised."""
    d, live, per_day = day_short_followup(tmp_path)
    run = hitl.load_run(d)
    run["settings"]["budget"].update({"per_task_usd": spent(d) + REFUSED, "reserve_for_report_usd": REFUSED * 0.99})
    hitl.save_run(d, run)
    calls = live_calls(tmp_path)
    out = hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    first = out.pending.request["action_requests"][0]["args"].get("binds")
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET, "the other kind is asked next"
    assert out.pending.request["action_requests"][0]["args"].get("binds") != first
    assert live_calls(tmp_path) == calls
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert not out.awaiting and out.reply == ANSWER
    assert live_calls(tmp_path) == calls + 1
    run = hitl.load_run(d)
    assert {e.get("binds", "task") for e in run["budget_extensions"][-2:]} == {"day", "task"}


def test_both_limits_short_with_the_task_remainder_smaller_still_asks_the_day_first(tmp_path):
    """own (task) < day remainder, both short for the call: the day is asked first, so the
    task card that follows is capped by the raised day and the SAME message gets its answer."""
    d, live, per_day = day_short_followup(tmp_path)
    run = hitl.load_run(d)
    run["settings"]["budget"].update({
        "per_day_usd": per_day + 0.0015, "per_task_usd": spent(d) + REFUSED, "reserve_for_report_usd": REFUSED * 0.99})
    hitl.save_run(d, run)
    calls = live_calls(tmp_path)
    out = hitl.send_followup(d, QUESTION, model_factory=live(), live=True)
    assert out.awaiting and out.pending.point == hitl.BUDGET
    assert out.pending.request["action_requests"][0]["args"].get("binds") == "day"
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert out.awaiting and out.pending.request["action_requests"][0]["args"].get("binds") in (None, "task")
    out = hitl.resume_run(d, hitl.parse_decision("edit", {"add_usd": 50}), model_factory=live(), live=True)
    assert not out.awaiting and out.reply == ANSWER
    assert live_calls(tmp_path) == calls + 1
