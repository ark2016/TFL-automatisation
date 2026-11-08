"""TFL Lab Deep Agent routes (ui_server/deep_routes.py).

Two kinds of tests, none of which calls the Anthropic API:

* fake-agent tests: ``build_start_command`` / ``build_resume_command`` are
  replaced by a tiny script that writes what ``python -m tfl_deep_agent``
  would write (run.json, progress.jsonl, result.json) and exits with the CLI's
  exit code; they cover validation, confirm_spend, cancel, restore, the file
  whitelist, settings, estimate and the body limit;
* a real mock run: a PDF task through the actual CLI subprocesses (scripted
  chat models), stopping at HITL 1 and HITL 2 and resumed over HTTP.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ui_server import deep_routes as dr
from ui_server import server as srv

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_EXAMPLE = ROOT / "tfl_deep_agent" / "settings.example.json"

pytest.importorskip("pydantic")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def deep_state(tmp_path, monkeypatch):
    runs = tmp_path / "lab_runs" / "deep"
    runs.mkdir(parents=True)
    settings = tmp_path / "deep_settings.json"
    shutil.copy(SETTINGS_EXAMPLE, settings)
    monkeypatch.setattr(dr, "DEEP_RUNS_DIR", runs)
    monkeypatch.setattr(dr, "SETTINGS_PATH", settings)
    monkeypatch.setattr(dr, "_runs", {})
    monkeypatch.setattr(dr, "_active", 0)
    monkeypatch.setattr(dr, "_live_active", 0)
    monkeypatch.setattr(dr, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setenv("TFL_DEEP_RUNS_DIR", str(tmp_path / "spend"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    monkeypatch.delenv("TFL_DEEP_SETTINGS", raising=False)
    yield
    # never leave a child behind
    for run in list(dr._runs.values()):
        proc = run.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()


FAKE_AGENT = textwrap.dedent('''
    """Writes what `python -m tfl_deep_agent` writes; argv: run_dir behaviour [decision]."""
    import json, os, sys, time
    from pathlib import Path

    d = Path(sys.argv[1]); mode = sys.argv[2]
    d.mkdir(parents=True, exist_ok=True)

    def ev(event, **kw):
        line = {"event": event, "ts": time.time(), "run_id": d.name, **kw}
        with open(d / "progress.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\\n")

    REQ = {"action_requests": [{"name": "confirm_formalization", "args": {"transcription": "t", "ir": {}},
                                "description": "check"}],
           "review_configs": [{"action_name": "confirm_formalization",
                               "allowed_decisions": ["approve", "edit", "reject"]}]}
    run = {"v": 1, "run_id": d.name, "status": "running", "mode": os.environ.get("FAKE_MODE", "mock"),
           "template": "short_verdict", "hitl": {"formalization": True, "report": True},
           "budget": {"spent_usd": 0.0, "limit_usd": 5.0}}
    print("fake agent", mode, file=sys.stderr, flush=True)
    ev("segment_start", segment=0, usage={"calls": 0}, budget={"spent_usd": 0.0, "limit_usd": 5.0})
    if mode == "sleep":
        (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
        time.sleep(120)
    elif mode == "awaiting":
        run.update(status="awaiting_human", pending={"point": "formalization", "interrupt_id": "i1",
                                                     "request": REQ, "asked_at": "now"})
        (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
        (d / "hitl.md").write_text("# HITL 1\\ncard", encoding="utf-8")
        ev("hitl_awaiting", point="formalization", interrupt_id="i1")
        sys.exit(3)
    elif mode == "awaiting_multi":  # one stop, two parallel tool calls (HumanInTheLoopMiddleware)
        req = {"action_requests": [{"name": "execute", "args": {"command": "ls"}, "description": "run"},
                                   {"name": "execute", "args": {"command": "rm -rf /work"}, "description": "run"}],
               "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]}]}
        run.update(status="awaiting_human", pending={"point": "execute", "interrupt_id": "m1",
                                                     "request": req, "asked_at": "now"})
        (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
        (d / "hitl.md").write_text("# execute\\n2 commands", encoding="utf-8")
        sys.exit(3)
    elif mode == "submitted":
        decision = sys.argv[3] if len(sys.argv) > 3 else None
        run.update(status="submitted", pending=None, budget={"spent_usd": 0.42, "limit_usd": 5.0},
                   result={"status": "submitted", "verdict": "LL(1)", "trust": "verified"})
        (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
        (d / "result.json").write_text(json.dumps({"status": "submitted", "report": {"verdict": "LL(1)"},
                                                   "trust": "verified", "decision": decision,
                                                   "template": "short_verdict",
                                                   "rendered": {"template": "exam_solution"}}),
                                       encoding="utf-8")
        (d / "result.md").write_text("# report", encoding="utf-8")
        ev("done", status="submitted", trust="verified")
        sys.exit(0)
    elif mode == "crash":
        print("Traceback: boom", file=sys.stderr)
        sys.exit(2)
''')


@pytest.fixture
def fake_agent(tmp_path, monkeypatch):
    """Replace the CLI by FAKE_AGENT; returns a dict to steer it and the argv
    the real builders produced."""
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    ctl = {"start": "awaiting", "resume": "submitted", "start_argv": [], "resume_argv": []}
    real_start, real_resume = dr.build_start_command, dr.build_resume_command

    def start(run_dir, task_file, **kw):
        ctl["start_argv"].append(real_start(run_dir, task_file, **kw))
        return [sys.executable, str(script), str(run_dir), ctl["start"]]

    def resume(run_dir, decision, payload_file, **kw):
        ctl["resume_argv"].append(real_resume(run_dir, decision, payload_file, **kw))
        return [sys.executable, str(script), str(run_dir), ctl["resume"], decision]

    monkeypatch.setattr(dr, "build_start_command", start)
    monkeypatch.setattr(dr, "build_resume_command", resume)
    return ctl


def wait_status(run_id, statuses, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = dr.api_detail(run_id)
        if data["run"]["status"] in statuses:
            return data
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} still {dr.api_detail(run_id)['run']['status']!r}; "
                         f"log: {dr.api_detail(run_id)['log_tail']}")


MOCK_TEXT = {"text": "S → aAb | bBa; A → aA | ε; B → bB | ε. LL(1)?", "mock_script": "ll_simple_ll1.json"}


# ---------------------------------------------------------------------------
# POST /api/deep/runs: validation and money
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload, needle", [
    ({"text": "x"}, "mock_script"),
    ({"text": "x", "live": True}, "confirm_spend"),
    ({"text": "x", "live": True, "confirm_spend": "yes"}, "confirm_spend"),
    ({"text": "x", "live": True, "confirm_spend": True, "mock_script": "ll_simple_ll1.json"}, "exclude"),
    ({"mock_script": "ll_simple_ll1.json"}, "exactly one"),
    ({"text": "x", "file_b64": "eA==", "mock_script": "ll_simple_ll1.json"}, "exactly one"),
    ({**MOCK_TEXT, "template": "poem"}, "unknown template"),
    ({**MOCK_TEXT, "mock_script": "../../settings.json"}, "unknown mock script"),
    ({**MOCK_TEXT, "mock_script": {"models": {}}}, "models"),
    ({**MOCK_TEXT, "settings_override": {"budget": {"per_task_usd": -1}}}, "budget"),
    ({**MOCK_TEXT, "settings_override": {"nonsense": 1}}, "nonsense"),
    ({**MOCK_TEXT, "colour": "red"}, "unknown field"),
    ({"file_b64": "!!!not base64", "mime": "application/pdf", "mock_script": "ll_simple_ll1.json"}, "bad_base64"),
    ({"file_b64": base64.b64encode(b"\x00\x01binary").decode(), "mime": "application/pdf",
      "mock_script": "ll_simple_ll1.json"}, ""),
])
def test_create_rejects_bad_requests(payload, needle, fake_agent):
    status, body = dr.dispatch("POST", "/api/deep/runs", "", payload)
    assert status == 400, body
    assert needle.lower() in body["error"].lower()
    assert not dr._runs and not fake_agent["start_argv"]  # nothing spawned, nothing written
    assert not any(dr.DEEP_RUNS_DIR.iterdir())


def test_mock_run_command_and_child_env(fake_agent, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-reach-a-mock-child")
    out = dr.api_create({**MOCK_TEXT, "template": "short_verdict",
                         "settings_override": {"limits": {"max_model_calls": 7}}})
    run_id = out["run_id"]
    assert out["mode"] == "mock" and out["detail_url"] == f"/api/deep/runs/{run_id}"
    data = wait_status(run_id, {"awaiting_human"})
    argv = fake_agent["start_argv"][0]
    run_dir = dr.DEEP_RUNS_DIR / run_id
    assert argv[1:3] == ["-m", "tfl_deep_agent"] and "--live" not in argv
    assert argv[argv.index("--save") + 1] == str(run_dir) and "--no-dialog" in argv
    assert argv[argv.index("--mock-script") + 1].endswith("ll_simple_ll1.json")
    assert argv[argv.index("--template") + 1] == "short_verdict"
    assert Path(argv[argv.index("--file") + 1]).read_text(encoding="utf-8") == MOCK_TEXT["text"]
    snap = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert snap["limits"]["max_model_calls"] == 7 and snap["budget"]["per_task_usd"] == 10.0
    env = dr._child_env(False)
    assert "ANTHROPIC_API_KEY" not in env and env["TFL_DEEP_RUNS_DIR"] == os.environ["TFL_DEEP_RUNS_DIR"]
    assert "ANTHROPIC_API_KEY" in dr._child_env(True)  # live passes the server's own environment through
    # the detail view: status, HITL card from the agent's run.json + hitl.md, events
    assert data["run"]["pending_point"] == "formalization"
    pending = data["hitl"]["pending"]
    assert pending["interrupt_id"] == "i1" and "card" in pending["card"]
    assert pending["allowed_decisions"] == ["approve", "edit", "reject"]
    assert [e["event"] for e in data["events"]] == ["segment_start", "hitl_awaiting"]
    lab = json.loads((run_dir / "lab.json").read_text(encoding="utf-8"))
    assert lab["status"] == "awaiting_human" and lab["segments"][0]["rc"] == 3
    assert "fake agent awaiting" in (run_dir / "run.log").read_text(encoding="utf-8")


def test_live_run_needs_confirm_and_passes_live(fake_agent):
    out = dr.api_create({"text": "x", "live": True, "confirm_spend": True})
    wait_status(out["run_id"], {"awaiting_human"})
    argv = fake_agent["start_argv"][0]
    assert "--live" in argv and "--mock-script" not in argv
    assert dr.api_runs()["runs"][0]["live"] is True


def test_live_run_refused_when_the_day_is_spent(fake_agent):
    spend = Path(os.environ["TFL_DEEP_RUNS_DIR"])
    spend.mkdir(parents=True, exist_ok=True)
    from tfl_deep_agent.middleware import budget as budget_mod

    day = budget_mod.DailySpendLog(spend / budget_mod.SPEND_LOG_NAME).today()
    (spend / budget_mod.SPEND_LOG_NAME).write_text(
        json.dumps({"day": day, "usd": 19.0, "task_id": "old", "ts": time.time()}) + "\n", encoding="utf-8")
    status, body = dr.dispatch("POST", "/api/deep/runs", "", {"text": "x", "live": True, "confirm_spend": True})
    assert status == 409 and "daily budget" in body["error"]
    assert not fake_agent["start_argv"]
    est = dr.api_estimate()
    assert est["day"]["spent_usd"] == 19.0 and est["day"]["can_start"] is False


def test_pdf_upload_is_stored_with_its_type(fake_agent):
    from tfl_deep_agent.tests.hitl_scripts import make_pdf

    pdf = make_pdf()
    out = dr.api_create({"file_b64": "data:application/pdf;base64," + base64.b64encode(pdf).decode(),
                         "name": "Задача 3.pdf", "mock_script": "ll_simple_ll1.json"})
    data = wait_status(out["run_id"], {"awaiting_human"})
    argv = fake_agent["start_argv"][0]
    task = Path(argv[argv.index("--file") + 1])
    assert task.name == "task.pdf" and task.read_bytes() == pdf
    assert data["run"]["input"]["kind"] == "pdf" and data["run"]["input"]["name"] == "Задача 3.pdf"
    status, f = dr.dispatch("GET", f"/api/deep/runs/{out['run_id']}/files/upload/task.pdf")
    assert status == 200 and f.path.read_bytes() == pdf


# ---------------------------------------------------------------------------
# Resume / cancel
# ---------------------------------------------------------------------------

def test_resume_validates_then_runs_a_new_process(fake_agent):
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"awaiting_human"})
    bad = [
        ({"decision": "maybe"}, 400, "decision"),
        ({"decision": "reject"}, 400, "comment"),
        ({"decision": "edit"}, 400, "payload"),
        ({"decision": "approve", "extra": 1}, 400, "unknown field"),
    ]
    for payload, code, needle in bad:
        status, body = dr.dispatch("POST", f"/api/deep/runs/{run_id}/resume", "", payload)
        assert status == code and needle in body["error"], (payload, body)
    assert not fake_agent["resume_argv"]
    out = dr.api_resume(run_id, {"decision": "reject", "comment": "добавь разбор слова aab"})
    assert out == {"run_id": run_id, "status": "queued", "point": "formalization", "decision": "reject"}
    data = wait_status(run_id, {"completed"})
    argv = fake_agent["resume_argv"][0]
    assert argv[argv.index("--resume") + 1] == str(dr.DEEP_RUNS_DIR / run_id)
    assert argv[argv.index("--decision") + 1] == "reject" and "--live" not in argv
    payload_file = Path(argv[argv.index("--payload") + 1].lstrip("@"))
    assert json.loads(payload_file.read_text(encoding="utf-8")) == {"comment": "добавь разбор слова aab"}
    assert data["run"]["result_status"] == "submitted" and data["run"]["segments"] == 2
    assert data["result"]["verdict"] == "LL(1)" and data["result"]["urls"]["md"].endswith("/files/result.md")
    # not waiting any more
    status, body = dr.dispatch("POST", f"/api/deep/runs/{run_id}/resume", "", {"decision": "approve"})
    assert status == 409


def test_live_resume_needs_confirm_spend(fake_agent, monkeypatch):
    run_id = dr.api_create({"text": "x", "live": True, "confirm_spend": True})["run_id"]
    wait_status(run_id, {"awaiting_human"})
    status, body = dr.dispatch("POST", f"/api/deep/runs/{run_id}/resume", "", {"decision": "approve"})
    assert status == 400 and "confirm_spend" in body["error"]
    dr.api_resume(run_id, {"decision": "approve", "confirm_spend": True})
    wait_status(run_id, {"completed"})
    assert "--live" in fake_agent["resume_argv"][0]


def test_cancel_running_kills_the_process(fake_agent):
    fake_agent["start"] = "sleep"
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"running"})
    deadline = time.time() + 10
    while dr._runs[run_id].get("proc") is None and time.time() < deadline:
        time.sleep(0.05)
    proc = dr._runs[run_id]["proc"]
    assert dr.api_cancel(run_id)["status"] == "cancelling"
    data = wait_status(run_id, {"cancelled"})
    assert proc.poll() is not None and data["run"]["error"] == "cancelled by user"
    assert dr.api_cancel(run_id)["status"] == "cancelled"  # idempotent


def test_cancel_waiting_run_blocks_resume(fake_agent):
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"awaiting_human"})
    assert dr.api_cancel(run_id)["status"] == "cancelled"
    status, _ = dr.dispatch("POST", f"/api/deep/runs/{run_id}/resume", "", {"decision": "approve"})
    assert status == 409
    assert dr.dispatch("POST", "/api/deep/runs/0123456789ab/cancel")[0] == 404
    assert dr.dispatch("GET", "/api/deep/runs/..%2f..")[0] == 404


def test_cancel_queued_run_never_starts(fake_agent, monkeypatch):
    monkeypatch.setattr(dr, "MAX_CONCURRENT_RUNS", 1)
    fake_agent["start"] = "sleep"
    first = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(first, {"running"})
    second = dr.api_create(MOCK_TEXT)["run_id"]
    time.sleep(0.3)
    assert dr.api_detail(second)["run"]["status"] == "queued"
    dr.api_cancel(second)
    assert wait_status(second, {"cancelled"})["run"]["error"] == "cancelled while queued"
    dr.api_cancel(first)
    wait_status(first, {"cancelled"})
    assert dr.api_detail(second)["run"]["segments"] == 0  # no process was ever started for it
    assert not (dr.DEEP_RUNS_DIR / second / "run.log").exists()


def test_crash_is_an_error_with_the_stderr_tail(fake_agent):
    fake_agent["start"] = "crash"
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    data = wait_status(run_id, {"error"})
    assert "exit code 2" in data["run"]["error"] and "boom" in data["run"]["error"]


def test_hitl_view_shows_every_action_of_one_stop(fake_agent):
    """One interrupt may hold several tool calls, and a decision applies to all of them:
    the view lists each one, and edit (exactly one action in resume_value) is not offered."""
    fake_agent["start"] = "awaiting_multi"
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    data = wait_status(run_id, {"awaiting_human"})
    p = data["hitl"]["pending"]
    assert p["action_count"] == 2
    assert [a["args"]["command"] for a in p["actions"]] == ["ls", "rm -rf /work"]
    assert all(a["allowed_decisions"] == ["approve", "edit", "reject"] for a in p["actions"])
    assert p["allowed_decisions"] == ["approve", "reject"]
    assert "2 commands" in p["card"]
    status, body = dr.dispatch("POST", f"/api/deep/runs/{run_id}/resume", "",
                               {"decision": "edit", "payload": {"command": "ls -l"}})
    assert status == 400 and "exactly one" in body["error"]
    assert dr.api_detail(run_id)["run"]["status"] == "awaiting_human"
    dr.api_resume(run_id, {"decision": "reject", "comment": "one command at a time"})
    wait_status(run_id, {"completed"})


def test_a_resume_refused_by_the_cli_keeps_the_run_resumable(fake_agent):
    """A resume process that exits (code 2: a CLI refusal) before it touches run.json leaves
    the agent at the same stop: the lab status stays awaiting_human, with the stderr tail."""
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"awaiting_human"})
    fake_agent["resume"] = "crash"
    dr.api_resume(run_id, {"decision": "approve"})
    deadline = time.time() + 30
    while time.time() < deadline:
        data = dr.api_detail(run_id)
        if data["run"]["status"] == "awaiting_human" and data["run"]["error"]:
            break
        time.sleep(0.1)
    run = data["run"]
    assert run["status"] == "awaiting_human", run
    assert "exited with code 2" in run["error"] and "boom" in run["error"] and "same review stop" in run["error"]
    assert data["hitl"]["pending"]["interrupt_id"] == "i1"
    lab = json.loads((dr.DEEP_RUNS_DIR / run_id / "lab.json").read_text(encoding="utf-8"))
    assert lab["status"] == "awaiting_human" and lab["segments"][-1]["rc"] == 2
    # survives a restart, and can be resumed again
    dr._runs.clear()
    dr.restore_runs(adopt=False)
    assert dr.api_detail(run_id)["run"]["status"] == "awaiting_human"
    fake_agent["resume"] = "submitted"
    dr.api_resume(run_id, {"decision": "approve"})
    data = wait_status(run_id, {"completed"})
    assert data["run"]["error"] is None and data["run"]["result_status"] == "submitted"


def test_live_override_may_only_lower_money_limits_and_keeps_review(fake_agent):
    live = {"text": "x", "live": True, "confirm_spend": True}
    for override, needle in (
        ({"budget": {"per_day_usd": 1000, "per_task_usd": 50}}, "per_day_usd"),
        ({"budget": {"per_task_usd": 50}}, "per_task_usd"),
        ({"budget": {"per_subagent_max_usd": 40}}, "per_subagent_max_usd"),
        ({"hitl": {"report": False}}, "hitl.report"),
        ({"hitl": {"formalization": False}}, "hitl.formalization"),
    ):
        status, body = dr.dispatch("POST", "/api/deep/runs", "", {**live, "settings_override": override})
        assert status == 400 and needle in body["error"], (override, body)
    status, body = dr.dispatch("POST", "/api/deep/runs", "", {**live, "auto_approve": True})
    assert status == 400 and "auto_approve" in body["error"]
    assert not fake_agent["start_argv"] and not dr._runs
    # lowering is fine, and a mock run may still auto-approve / switch review points off
    out = dr.api_create({**live, "settings_override": {"budget": {"per_task_usd": 1.0, "per_day_usd": 10}}})
    wait_status(out["run_id"], {"awaiting_human"})
    snap = json.loads((dr.DEEP_RUNS_DIR / out["run_id"] / dr.SETTINGS_SNAPSHOT).read_text(encoding="utf-8"))
    assert snap["budget"]["per_task_usd"] == 1.0 and snap["budget"]["per_day_usd"] == 10
    out = dr.api_create({**MOCK_TEXT, "auto_approve": True, "settings_override": {"hitl": {"report": False}}})
    wait_status(out["run_id"], {"awaiting_human"})


def test_live_runs_run_one_at_a_time(fake_agent, monkeypatch):
    """Each live process reserves its in-flight worst case only in its own ledger, so a
    second live run waits for the first; mock runs keep the parallel gate."""
    monkeypatch.setattr(dr, "MAX_CONCURRENT_RUNS", 3)
    fake_agent["start"] = "sleep"
    live = {"text": "x", "live": True, "confirm_spend": True}
    first = dr.api_create(live)["run_id"]
    wait_status(first, {"running"})
    second = dr.api_create(live)["run_id"]
    mock = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(mock, {"running"})
    time.sleep(0.5)
    assert dr.api_detail(second)["run"]["status"] == "queued"
    dr.api_cancel(first)
    wait_status(first, {"cancelled"})
    wait_status(second, {"running"})
    for rid in (second, mock):
        dr.api_cancel(rid)
        wait_status(rid, {"cancelled"})
    deadline = time.time() + 10
    while dr._live_active and time.time() < deadline:
        time.sleep(0.05)
    assert dr._live_active == 0


def test_live_slot_counts_an_adopted_live_orphan(fake_agent):
    with dr._lock:
        dr._runs["0123456789ab"] = {"live": True, "orphan": True, "status": "running"}
    assert dr._live_orphan_running("fedcba987654") is True
    assert dr._live_orphan_running("0123456789ab") is False
    with dr._lock:
        dr._runs.pop("0123456789ab")


def test_result_view_reports_the_rendered_template_and_render_route(fake_agent):
    fake_agent["start"] = "submitted"
    run_id = dr.api_create({**MOCK_TEXT, "template": "short_verdict"})["run_id"]
    data = wait_status(run_id, {"completed"})
    res = data["result"]
    assert res["template"] == "exam_solution" and res["run_template"] == "short_verdict"
    assert res["can_render"] is True and res["views"] == {}
    summary = dr.api_runs()["runs"][0]
    assert summary["template"] == "exam_solution" and summary["run_template"] == "short_verdict"
    run_dir = dr.DEEP_RUNS_DIR / run_id
    before = (run_dir / "result.json").read_bytes(), (run_dir / "result.md").read_bytes()
    same = dr.api_render(run_id, {"template": "exam_solution"})
    assert same["same_as_result"] is True and same["urls"]["md"].endswith("/files/result.md")
    out = dr.api_render(run_id, {"template": "full_proof"})
    assert out["same_as_result"] is False and out["urls"]["html"].endswith("/files/views/full_proof.html")
    status, f = dr.dispatch("GET", f"/api/deep/runs/{run_id}/files/views/full_proof.md")
    assert status == 200 and "LL(1)" in f.path.read_text(encoding="utf-8")
    assert dr.dispatch("GET", f"/api/deep/runs/{run_id}/files/views/full_proof.html")[0] == 200
    assert ((run_dir / "result.json").read_bytes(), (run_dir / "result.md").read_bytes()) == before
    assert dr.api_detail(run_id)["result"]["views"]["full_proof"]["md"].endswith("views/full_proof.md")
    for payload, code in (({"template": "poem"}, 400), ({"template": "full_proof", "x": 1}, 400), ([], 400)):
        assert dr.dispatch("POST", f"/api/deep/runs/{run_id}/render", "", payload)[0] == code
    # a run that is not finished cannot be re-rendered
    fake_agent["start"] = "awaiting"
    waiting = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(waiting, {"awaiting_human"})
    status, body = dr.dispatch("POST", f"/api/deep/runs/{waiting}/render", "", {"template": "full_proof"})
    assert status == 409 and "submitted" in body["error"]


# ---------------------------------------------------------------------------
# Restore after a server restart
# ---------------------------------------------------------------------------

def test_restore_settles_dead_runs_from_the_agent_record(fake_agent, monkeypatch):
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"awaiting_human"})
    run_dir = dr.DEEP_RUNS_DIR / run_id
    # pretend the server died while a resume was running: lab.json says running, pid is gone
    lab = json.loads((run_dir / "lab.json").read_text(encoding="utf-8"))
    lab.update(status="running", pid=999999, pid_identity={"pid": 999999, "created": 1.0})
    (run_dir / "lab.json").write_text(json.dumps(lab), encoding="utf-8")
    other = dr.DEEP_RUNS_DIR / "abcdefabcdef"
    other.mkdir()
    (other / "lab.json").write_text(json.dumps({**lab, "run_id": "abcdefabcdef"}), encoding="utf-8")
    (other / "run.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    monkeypatch.setattr(dr, "_runs", {})
    assert dr.restore_runs(adopt=False) == []
    assert dr.api_detail(run_id)["run"]["status"] == "awaiting_human"  # still resumable
    assert dr.api_detail("abcdefabcdef")["run"]["status"] == "interrupted"
    dr.api_resume(run_id, {"decision": "approve"})
    assert wait_status(run_id, {"completed"})["run"]["result_status"] == "submitted"


def test_pipeline_registry_ignores_the_deep_dir(fake_agent, monkeypatch):
    monkeypatch.setattr(srv, "RUNS_DIR", dr.DEEP_RUNS_DIR.parent)
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"awaiting_human"})
    assert srv.restore_runs() == [] and run_id not in srv._runs


# ---------------------------------------------------------------------------
# Files, folding, settings, estimate
# ---------------------------------------------------------------------------

def test_file_route_whitelist(fake_agent):
    fake_agent["start"] = "submitted"
    run_id = dr.api_create(MOCK_TEXT)["run_id"]
    wait_status(run_id, {"completed"})
    for name in ("result.md", "result.json", "progress.jsonl", "run.log", "upload/task.txt"):
        status, f = dr.dispatch("GET", f"/api/deep/runs/{run_id}/files/{name}")
        assert status == 200 and isinstance(f, dr.FileResponse), name
    for name in ("run.json", "lab.json", "ui_settings.json", "../lab.json", "upload/../lab.json", "checkpoints.sqlite"):
        assert dr.dispatch("GET", f"/api/deep/runs/{run_id}/files/{name}")[0] == 404, name


def test_fold_events_builds_the_tree_budget_and_todos():
    evs = [
        {"event": "segment_start", "budget": {"spent_usd": 0.0, "limit_usd": 5.0}},
        {"event": "model_call", "node": "t1", "model": "claude-opus-5-5", "usd": 0.10, "role": "main"},
        {"event": "todos", "todos": [{"content": "IR", "status": "in_progress"}]},
        {"event": "subagent_start", "node": "t1/prover", "kind": "subagent", "allocated_usd": 2.0},
        {"event": "model_call", "node": "t1/prover", "model": "claude-opus-5-5", "usd": 0.30, "role": "prover"},
        {"event": "subagent_start", "node": "t1/prover/spawn:x", "kind": "spawn", "allocated_usd": 0.5},
        {"event": "model_call", "node": "t1/prover/spawn:x", "model": "claude-haiku-5-5", "usd": 0.01},
        {"event": "subagent_end", "node": "t1/prover/spawn:x", "returned_usd": 0.49},
        {"event": "tool_call", "tool": "ll_table", "ok": True, "trust": "verified"},
        {"event": "tool_call", "tool": "validate_ir", "ok": False, "trust": "not_verified"},
        {"event": "budget_wrap_up", "node": "t1", "available_usd": 0.2},
        {"event": "workflow_start", "workflow": "w"},
        {"event": "todos", "todos": [{"content": "IR", "status": "completed"}],
         "budget": {"spent_usd": 0.41, "limit_usd": 5.0}, "usage": {"calls": 3}},
        {"event": "hitl_decision", "point": "report", "decision": "approved"},
    ]
    v = dr.fold_events(evs)
    assert v["todos"] == [{"content": "IR", "status": "completed"}]
    assert v["budget"] == {"spent_usd": 0.41, "limit_usd": 5.0} and v["usage"] == {"calls": 3}
    assert v["tools"] == {"total": 2, "by_trust": {"verified": 1, "not_verified": 1}, "failed": 1}
    (root,) = v["subagents"]
    assert root["node"] == "t1" and root["subtree_spent_usd"] == pytest.approx(0.41)
    (prover,) = root["children"]
    assert prover["allocated_usd"] == 2.0 and prover["status"] == "running"
    assert prover["models"] == {"claude-opus-5-5": 0.3}
    (spawn,) = prover["children"]
    assert spawn["kind"] == "spawn" and spawn["status"] == "closed" and spawn["returned_usd"] == 0.49
    assert v["budget_signals"][0]["event"] == "budget_wrap_up"
    assert v["orchestration"][0]["workflow"] == "w" and v["hitl_decisions"][0]["decision"] == "approved"


def test_settings_get_put(tmp_path):
    got = dr.api_get_settings()
    assert got["settings"]["budget"]["per_task_usd"] == 10.0 and got["error"] is None
    assert "short_verdict" in got["templates"] and got["defaults"]["budget"]
    status, body = dr.dispatch("PUT", "/api/deep/settings", "", {"budget": {"per_task_usd": "lots"}})
    assert status == 400
    status, body = dr.dispatch("PUT", "/api/deep/settings", "", {"budget": {"per_task_usd": 1.5},
                                                                 "hitl": {"report": False}})
    assert status == 200 and body["settings"]["budget"]["per_task_usd"] == 1.5
    on_disk = json.loads(Path(dr.SETTINGS_PATH).read_text(encoding="utf-8"))
    assert on_disk["budget"]["per_task_usd"] == 1.5 and on_disk["hitl"]["report"] is False
    assert on_disk["budget"]["per_day_usd"] == 20.0  # the rest kept
    # a broken file is reported, not silently replaced, and blocks a run
    Path(dr.SETTINGS_PATH).write_text("{broken", encoding="utf-8")
    assert dr.api_get_settings()["error"]
    status, body = dr.dispatch("POST", "/api/deep/runs", "", MOCK_TEXT)
    assert status == 409 and "settings" in body["error"]


def test_estimate_no_data_then_measured(fake_agent):
    est = dr.api_estimate()
    assert est["run"]["n"] == 0 and est["run"]["estimated_cost_usd"] is None
    assert est["cap_usd"] == 10.0 and est["day"]["can_start"] is True
    assert est["models"] and all("model" in m or "tier" in m or m for m in est["models"])
    # a finished live run counts, a mock run does not
    for i, (mode, spent) in enumerate((("live", 0.8), ("live", 1.2), ("mock", 0.5))):
        d = dr.DEEP_RUNS_DIR / f"00000000000{i}"
        d.mkdir()
        (d / "run.json").write_text(json.dumps({"status": "submitted", "mode": mode, "template": "full_proof",
                                                "budget": {"spent_usd": spent}}), encoding="utf-8")
    est = dr.api_estimate()
    assert est["run"]["n"] == 2 and est["run"]["estimated_cost_usd"] == 1.0
    assert est["run"]["low_usd"] == 0.8 and est["run"]["high_usd"] == 1.2
    assert dr.api_estimate("short_verdict")["run"]["n"] == 0
    over = dr.api_estimate(None, json.dumps({"budget": {"per_task_usd": 0.5}}))
    assert over["cap_usd"] == 0.5 and over["run"]["high_usd"] == 0.5
    assert dr.dispatch("GET", "/api/deep/estimate", "settings_override=%7Bbad")[0] == 400


def test_mock_scripts_listing():
    names = {m["name"] for m in dr.api_mock_scripts()["mock_scripts"]}
    assert "ll_simple_ll1.json" in names


# ---------------------------------------------------------------------------
# HTTP: the server hook (guards, body limit, files)
# ---------------------------------------------------------------------------

@pytest.fixture
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def http_req(port, method, path, payload=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    body = raw
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if body is not None:
        hdrs.update({"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    hdrs.update(headers or {})
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except ValueError:
        return resp.status, data


def announce_length(port, path, length):
    """POST with a Content-Length header of ``length`` and a one-byte body;
    returns the status (the server answers once it gives up on the body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.putrequest("POST", path, skip_host=True)
    for k, v in {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{port}", "Content-Length": str(length)}.items():
        conn.putheader(k, v)
    conn.endheaders()
    conn.sock.settimeout(3)
    try:
        resp = conn.getresponse()
        status = resp.status
    except (TimeoutError, OSError):
        status = None  # still waiting for the body: not refused up front
    conn.close()
    return status


def test_http_routes_and_guards(port, fake_agent):
    assert http_req(port, "GET", "/api/deep/settings")[0] == 200
    assert http_req(port, "GET", "/api/deep/runs")[1] == {"runs": []}
    assert http_req(port, "GET", "/api/deep/nothing")[0] == 404
    # same-origin JSON only, like every other POST
    status, _ = http_req(port, "POST", "/api/deep/runs", MOCK_TEXT, headers={"Origin": "https://evil.example"})
    assert status == 403 and not fake_agent["start_argv"]
    status, _ = http_req(port, "PUT", "/api/deep/settings", {"budget": {"per_task_usd": 1}},
                         headers={"Content-Type": "text/plain"})
    assert status == 403
    status, body = http_req(port, "POST", "/api/deep/runs", MOCK_TEXT)
    assert status == 200, body
    run_id = body["run_id"]
    wait_status(run_id, {"awaiting_human"})
    status, body = http_req(port, "GET", f"/api/deep/runs/{run_id}?after=1")
    assert status == 200 and [e["event"] for e in body["events"]] == ["hitl_awaiting"]
    assert body["next"] == 2 and body["total"] == 2
    status, card = http_req(port, "GET", f"/api/deep/runs/{run_id}/files/hitl.md")
    assert status == 200 and b"card" in card
    status, body = http_req(port, "POST", f"/api/deep/runs/{run_id}/resume", {"decision": "approve"})
    assert status == 200
    wait_status(run_id, {"completed"})
    assert http_req(port, "POST", f"/api/deep/runs/{run_id}/cancel", {})[1]["status"] == "completed"


def test_http_body_limit_follows_intake_max_file_mb(port, fake_agent):
    dr.api_put_settings({"intake": {"max_file_mb": 1}})
    limit = dr.max_body_bytes()
    assert srv.MAX_BODY_BYTES < limit < 3 * 1024 * 1024
    # bigger than the pipeline limit, under the deep limit: accepted by the server
    # (intake then judges the file: 1.2 MB of base64 is ~0.9 MB of data)
    blob = base64.b64encode(b"%PDF-1.4\n" + b"0" * 900_000).decode()
    status, body = http_req(port, "POST", "/api/deep/runs", {"file_b64": blob, "mime": "application/pdf",
                                                             "mock_script": "ll_simple_ll1.json"})
    assert status == 400 and "pdf" in body["error"].lower()  # a 400 from intake, not a 413
    # over the limit: refused on the Content-Length header, before the body is read
    # (no body is sent, so Windows cannot reset the connection mid-upload)
    assert announce_length(port, "/api/deep/runs", limit + 10) == 413
    # the other routes keep their limit
    assert announce_length(port, "/api/run", srv.MAX_BODY_BYTES + 10) == 413


# ---------------------------------------------------------------------------
# A real mock run: PDF -> HITL 1 (edit) -> HITL 2 (approve) through the CLI
# ---------------------------------------------------------------------------

def test_real_mock_pdf_run_through_both_hitl_points(port):
    pytest.importorskip("deepagents")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    pytest.importorskip("pypdf")
    from tfl_deep_agent.tests import hitl_scripts as H

    pdf = H.make_pdf()
    status, body = http_req(port, "POST", "/api/deep/runs", {
        "file_b64": base64.b64encode(pdf).decode(), "mime": "application/pdf", "name": "Задача 3.pdf",
        "mock_script": H.pdf_script(), "template": "short_verdict"})
    assert status == 200, body
    run_id = body["run_id"]
    run_dir = dr.DEEP_RUNS_DIR / run_id
    data = wait_status(run_id, {"awaiting_human", "error", "completed"}, timeout=240)
    assert data["run"]["status"] == "awaiting_human", data["log_tail"]
    assert data["run"]["mode"] == "mock" and data["run"]["live"] is False

    # HITL 1: the card, the source, the plan, the subagent tree, tool trust
    pending = data["hitl"]["pending"]
    assert pending["point"] == "formalization" and "edit" in pending["allowed_decisions"]
    assert "Transcription (intake)" in pending["card"]
    assert pending["args"]["transcription"] == H.INTAKE_TRANSCRIPTION
    assert pending["source"]["kind"] == "pdf"
    status, src = http_req(port, "GET", pending["source_url"])
    assert status == 200 and src == pdf
    kinds = [e["event"] for e in data["events"]]
    assert kinds[0] == "segment_start" and kinds[-2:] == ["hitl_awaiting", "segment_end"]
    assert data["tools"]["total"] >= 2
    names = {c["name"] for root in data["subagents"] for c in root["children"]}
    assert {"intake", "ir-builder"} <= names, data["subagents"]
    tool_events = [e for e in data["events"] if e["event"] == "tool_call"]
    assert all("trust" in e for e in tool_events)
    assert data["budget"]["spent_usd"] > 0 and data["budget"]["limit_usd"] == 10.0
    assert data["usage"]["calls"] >= 3

    # polling: after=N returns only the newer lines
    n = data["next"]
    assert dr.api_detail(run_id, after=n)["events"] == []

    # edit the transcription at HITL 1 -> stops at HITL 2 with a draft
    status, body = http_req(port, "POST", f"/api/deep/runs/{run_id}/resume",
                            {"decision": "edit", "payload": {"transcription": H.EDITED_TRANSCRIPTION}})
    assert status == 200, body
    data = wait_status(run_id, {"awaiting_human", "error", "completed"}, timeout=240)
    assert data["run"]["status"] == "awaiting_human" and data["run"]["pending_point"] == "report", data["log_tail"]
    assert data["hitl"]["pending"]["draft"] and "md" in data["hitl"]["pending"]["draft_urls"]
    later = dr.api_detail(run_id, after=n)["events"]
    assert later[0]["event"] == "segment_start" and later[0]["segment"] == 2  # 1-based: the first process was segment 1
    assert any(e["event"] == "hitl_decision" and e["decision"] == "edited" for e in later)
    # an edit at HITL 2 that does not fit is a 400 now and the run keeps waiting
    status, body = http_req(port, "POST", f"/api/deep/runs/{run_id}/resume",
                            {"decision": "edit", "payload": {"no_such_field": 1}})
    assert status == 400, body
    assert dr.api_detail(run_id)["run"]["status"] == "awaiting_human"

    # approve the report
    status, body = http_req(port, "POST", f"/api/deep/runs/{run_id}/resume", {"decision": "approve"})
    assert status == 200, body
    data = wait_status(run_id, {"completed", "error", "awaiting_human"}, timeout=240)
    assert data["run"]["status"] == "completed" and data["run"]["result_status"] == "submitted", data["log_tail"]
    assert data["done"]["status"] == "submitted" and data["hitl"]["pending"] is None
    assert [d["decision"] for d in data["hitl"]["decisions"]] == ["edited", "approved"]
    assert data["result"]["human_review"] and data["result"]["urls"]["html"]
    status, md = http_req(port, "GET", data["result"]["urls"]["md"])
    assert status == 200 and md
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["task"] == H.EDITED_TRANSCRIPTION and result["input"]["kind"] == "pdf"
    lab = json.loads((run_dir / "lab.json").read_text(encoding="utf-8"))
    assert [s["rc"] for s in lab["segments"]] == [3, 3, 0]
    # mock spends nothing from the day log
    assert dr.api_estimate()["day"]["spent_usd"] == 0
    # the finished report in another template: a view, result.* untouched
    assert data["result"]["template"] == "short_verdict" and data["result"]["can_render"] is True
    status, body = http_req(port, "POST", f"/api/deep/runs/{run_id}/render", {"template": "exam_solution"})
    assert status == 200 and body["same_as_result"] is False, body
    status, html = http_req(port, "GET", body["urls"]["html"])
    assert status == 200 and html
    after = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert after["rendered"]["template"] == "short_verdict"
