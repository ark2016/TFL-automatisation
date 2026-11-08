"""Deep Agent tab of TFL Lab: the page structure, the API surface it calls and
its pure helpers (run under node against the shapes ui_server/deep_routes.py
returns). The helpers live in app.js between the "deep: pure helpers" markers."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ui_server import deep_routes as dr

STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "lab.css").read_text(encoding="utf-8")

BEGIN = "// ---- deep: pure helpers begin"
END = "// ---- deep: pure helpers end ----"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------
def test_deep_tab_structure_present():
    for needle in ('id="mode-switch"', 'data-mode="pipelines"', 'data-mode="deep"', 'id="deep-view"',
                   'id="deep-drop"', 'id="deep-text"', 'id="deep-file-input"', 'id="deep-template"',
                   'id="deep-main-model"', 'id="deep-budget"', 'id="deep-mode"', 'id="deep-confirm"',
                   'id="deep-estimate"', 'id="deep-run"', 'id="deep-plan"', 'id="deep-timeline"',
                   'data-dtab="review"', 'data-dtab="agents"', 'data-dtab="report"', 'data-dtab="log"',
                   'id="deep-review"', 'id="deep-agents"', 'id="deep-report"', 'id="deep-budget-bar"',
                   'id="deep-bb-task-fill"', 'id="deep-bb-day-fill"', 'id="deep-settings-back"'):
        assert needle in INDEX, needle
    # the existing workspace and its tabs stay
    for needle in ('id="workspace"', 'data-tab="progress"', 'id="run-list"', 'id="settings-back"'):
        assert needle in INDEX, needle


def test_deep_tab_uses_its_own_tab_classes():
    # app.js binds every `.tab` / `.panel` to the pipelines result pane; the deep pane must not use them
    deep = INDEX[INDEX.index('id="deep-view"'):INDEX.index('id="deep-settings-back"')]
    tokens = {t for cls in re.findall(r'class="([^"]*)"', deep) for t in cls.split()}
    assert "tab" not in tokens and "panel" not in tokens
    assert 'class="dtab' in deep and 'class="dpanel' in deep


def test_deep_frontend_calls_every_deep_endpoint():
    for path in ("'/deep/settings'", "'/deep/mock-scripts'", "'/deep/runs'", "/deep/estimate",
                 "/resume`", "/cancel`", "/render`", "/files/"):
        assert path in APP, path
    assert "'PUT'" in APP
    # fields of POST /api/deep/runs the tab sends are all accepted by the backend
    sent = {"text", "file_b64", "mime", "name", "template", "settings_override", "auto_approve",
            "mock_script", "live", "confirm_spend"}
    assert sent <= set(dr.CREATE_KEYS)


def test_deep_css_hides_the_other_workspace():
    assert "body.mode-deep .workspace" in CSS
    assert "body:not(.mode-deep) .deep-view" in CSS
    for t in ("verified", "refuted", "bounded_pass", "well_formed", "not_verified"):
        assert f".trust-{t}" in CSS


def test_helpers_region_is_marked_once():
    assert APP.count(BEGIN) == 1 and APP.count(END) == 1
    assert APP.index(BEGIN) < APP.index(END)


# ---------------------------------------------------------------------------
# pure helpers under node
# ---------------------------------------------------------------------------
def _js(calls):
    """Run [(fn, args...)] through the deep helpers; return the list of results."""
    numbers = APP[APP.index("function num(v)"):APP.index("const projectLabel")]
    helpers = APP[APP.index(BEGIN):APP.index(END)]
    script = numbers + helpers + r"""
const calls = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = calls.map(([fn, ...args]) => {
  const f = eval(fn);
  const r = f(...args);
  return r === undefined ? null : r;
});
process.stdout.write(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", script], input=json.dumps(calls), capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _one(fn, *args):
    return _js([[fn, *args]])[0]


@needs_node
def test_file_kind_and_limits():
    r = _js([
        ["deepFileKind", "Task.PDF", ""],
        ["deepFileKind", "scan.jpeg", "image/jpeg"],
        ["deepFileKind", "x.bin", "application/octet-stream"],
        ["deepFileKind", "notes.txt", "text/plain"],
        ["deepFileProblem", "a.pdf", "application/pdf", 3 * 1024 * 1024, 2],
        ["deepFileProblem", "a.pdf", "application/pdf", 1024, 2],
        ["deepFileProblem", "a.exe", "", 10, 2],
        ["deepB64FromDataUrl", "data:application/pdf;base64,JVBERi0="],
    ])
    assert r[0] == {"kind": "file", "mime": "application/pdf", "label": "PDF"}
    assert r[1]["mime"] == "image/jpeg"
    assert r[2] is None
    assert r[3]["kind"] == "text"
    assert "limit is 2 MB" in r[4]
    assert r[5] is None
    assert "unsupported" in r[6]
    assert r[7] == "JVBERi0="


SETTINGS = {"budget": {"per_task_usd": 5, "per_day_usd": 20}, "models": {"roles": {"main": "claude-opus-5-5"}},
            "hitl": {"formalization": True, "report": True, "execute": False}}


@needs_node
def test_override_holds_only_changed_values():
    same = {"perTaskUsd": "5", "mainModel": "claude-opus-5-5", "hitlFormalization": True, "hitlReport": True}
    changed = {"perTaskUsd": "1.5", "mainModel": "claude-haiku-5-5", "hitlFormalization": False,
               "hitlReport": True}
    r = _js([["deepOverride", same, SETTINGS], ["deepOverride", changed, SETTINGS]])
    assert r[0] is None
    assert r[1] == {"budget": {"per_task_usd": 1.5}, "models": {"roles": {"main": "claude-haiku-5-5"}},
                    "hitl": {"formalization": False}}


@needs_node
def test_create_body_follows_the_create_contract():
    r = _js([
        ["deepCreateBody", {"text": " L = {a^n b^n} ", "mode": "mock", "mockName": "cfl_anbmcm.json"}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": None}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": False}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "mockName": "a.json",
                            "template": "short_verdict"}],
        ["deepCreateBody", {"file": {"b64": "JVBE", "mime": "application/pdf", "name": "t.pdf"},
                            "mode": "inline", "mockInline": '{"models": {"main": ["hi"]}}', "autoApprove": True}],
        ["deepCreateBody", {"text": "x", "mode": "inline", "mockInline": '{"models": {}}'}],
        ["deepCreateBody", {"file": {"b64": None, "mime": "image/png"}, "mode": "mock", "mockName": "a"}],
        ["deepCreateBody", {"text": "  ", "mode": "mock", "mockName": "a"}],
    ])
    assert r[0] == {"body": {"text": "L = {a^n b^n}", "mock_script": "cfl_anbmcm.json"}}
    assert "replay script" in r[1]["error"]
    assert "confirm" in r[2]["error"]
    assert r[3] == {"body": {"text": "x", "template": "short_verdict", "live": True, "confirm_spend": True}}
    assert r[4] == {"body": {"file_b64": "JVBE", "mime": "application/pdf", "name": "t.pdf", "auto_approve": True,
                             "mock_script": {"models": {"main": ["hi"]}}}}
    assert "non-empty" in r[5]["error"]
    assert "still being read" in r[6]["error"]
    assert "task text" in r[7]["error"]
    for res in r:
        if "body" in res:
            assert set(res["body"]) <= set(dr.CREATE_KEYS)


@needs_node
def test_create_body_keeps_review_on_live_runs():
    """The server refuses auto_approve and switching a review point off for a live run;
    the composer says so before sending."""
    r = _js([
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "autoApprove": True}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True,
                            "override": {"hitl": {"report": False}}}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True,
                            "override": {"hitl": {"formalization": True}, "budget": {"per_task_usd": 1}}}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": "a.json", "autoApprove": True,
                            "override": {"hitl": {"report": False}}}],
    ])
    assert "mock runs" in r[0]["error"]
    assert "review points" in r[1]["error"] and "report" in r[1]["error"]
    assert r[2]["body"]["settings_override"] == {"hitl": {"formalization": True}, "budget": {"per_task_usd": 1}}
    assert r[3]["body"]["auto_approve"] is True


@needs_node
def test_pending_actions_lists_every_tool_call_of_a_stop():
    two = {"actions": [{"name": "execute", "args": {"command": "ls"}},
                       {"name": "execute", "args": {"command": "rm x"}}], "action": "execute",
           "args": {"command": "ls"}}
    old = {"action": "execute", "args": {"command": "ls"}, "allowed_decisions": ["approve"]}
    r = _js([["deepPendingActions", two], ["deepPendingActions", old], ["deepPendingActions", {}],
             ["deepPendingActions", None]])
    assert [a["args"]["command"] for a in r[0]] == ["ls", "rm x"]
    assert r[1] == [{"name": "execute", "args": {"command": "ls"}, "allowed_decisions": ["approve"]}]
    assert r[2] == [] and r[3] == []


HITL1_ARGS = {"transcription": "L = {a^n b^n | n >= 0}", "class": "cfl",
              "ir": {"class": "cfl", "alphabet": ["a", "b"], "language": "a^n b^n"}}


@needs_node
def test_hitl1_edits_send_only_changed_fields():
    same_ir = json.dumps({"language": "a^n b^n", "alphabet": ["a", "b"], "class": "cfl"}, indent=2)
    new_ir = json.dumps({"class": "dcfl", "alphabet": ["a", "b"], "language": "a^n b^n"})
    r = _js([
        ["deepHitl1Edits", HITL1_ARGS, {"transcription": "L = {a^n b^n | n >= 0}", "irText": same_ir, "cls": "cfl"}],
        ["deepHitl1Edits", HITL1_ARGS, {"transcription": "L = {a^n b^n | n >= 1}", "irText": same_ir, "cls": "cfl"}],
        ["deepHitl1Edits", HITL1_ARGS, {"transcription": HITL1_ARGS["transcription"], "irText": same_ir,
                                        "cls": "dcfl"}],
        ["deepHitl1Edits", HITL1_ARGS, {"irText": new_ir, "cls": "dcfl"}],
        ["deepHitl1Edits", HITL1_ARGS, {"irText": "{bad"}],
        ["deepHitl1Edits", HITL1_ARGS, {"transcription": "   "}],
    ])
    assert r[0] == {"payload": {}, "changed": []}
    assert r[1]["payload"] == {"transcription": "L = {a^n b^n | n >= 1}"}
    assert "needs an IR" in r[2]["error"]          # deep_routes / hitl: a class change requires the IR
    assert r[3]["changed"] == ["ir", "class"]
    assert "not valid JSON" in r[4]["error"]
    assert "empty" in r[5]["error"]
    assert set(r[3]["payload"]) <= {"transcription", "ir", "class"}


REPORT_ARGS = {"verdict": "L is CFL", "answer": "yes", "language_class": "cfl", "method": "grammar",
               "summary": "s", "template": "full_proof",
               "steps": [{"title": "t", "claim": "c", "justification": "j", "evidence_ids": ["E1"]}]}


@needs_node
def test_report_edits_send_only_changed_fields():
    steps = json.dumps(REPORT_ARGS["steps"], indent=2)
    base = {"verdict": "L is CFL", "answer": "yes", "language_class": "cfl", "method": "grammar", "summary": "s",
            "template": "full_proof", "stepsText": steps}
    r = _js([
        ["deepReportEdits", REPORT_ARGS, base],
        ["deepReportEdits", REPORT_ARGS, {**base, "template": "short_verdict", "answer": ""}],
        ["deepReportEdits", REPORT_ARGS, {**base, "verdict": "  "}],
        ["deepReportEdits", REPORT_ARGS, {**base, "stepsText": "[]"}],
        ["deepReportEdits", REPORT_ARGS, {**base, "stepsText": json.dumps([{"title": "t2", "claim": "c"}])}],
        ["deepReportEdits", {"verdict": "v", "method": "m", "steps": []}, {"template": "full_proof"}],
    ])
    assert r[0] == {"payload": {}, "changed": []}
    assert r[1]["payload"] == {"answer": None, "template": "short_verdict"}
    assert "verdict must not be empty" in r[2]["error"]
    assert "non-empty" in r[3]["error"]
    assert r[4]["changed"] == ["steps"]
    assert r[5]["changed"] == []     # no template in the args means full_proof


@needs_node
def test_resume_body_rules():
    r = _js([
        ["deepResumeBody", "approve", {}],
        ["deepResumeBody", "reject", {"comment": " "}],
        ["deepResumeBody", "reject", {"comment": "wrong IR"}],
        ["deepResumeBody", "edit", {"payload": {}}],
        ["deepResumeBody", "edit", {"payload": {"class": "ll"}, "comment": "c"}],
        ["deepResumeBody", "approve", {"live": True, "confirm": False}],
        ["deepResumeBody", "approve", {"live": True, "confirm": True}],
        ["deepResumeBody", "edit", {"payload": {"x": 1}, "allowed": ["approve", "reject"]}],
    ])
    assert r[0] == {"body": {"decision": "approve"}}
    assert "needs a comment" in r[1]["error"]
    assert r[2] == {"body": {"decision": "reject", "comment": "wrong IR"}}
    assert "nothing is edited" in r[3]["error"]
    assert r[4] == {"body": {"decision": "edit", "comment": "c", "payload": {"class": "ll"}}}
    assert "confirm" in r[5]["error"]
    assert r[6] == {"body": {"decision": "approve", "confirm_spend": True}}
    assert "not allowed" in r[7]["error"]


def _sample_events():
    ts = "2026-11-08T04:56:21.167+03:00"
    b = {"spent_usd": 0.1, "limit_usd": 5.0, "reserve_usd": 0.3, "wrap_up": False, "exhausted": False}
    u = {"calls": 1, "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.1}
    evs = [
        {"event": "segment_start", "segment": 1, "mode": "mock", "template": "full_proof", "resumed_with": None},
        {"event": "todos", "todos": [{"content": "parse", "status": "completed"},
                                     {"content": "prove", "status": "in_progress"}], "role": "main"},
        {"event": "model_call", "node": "task", "role": "main", "model": "claude-opus-5-5", "usd": 0.05,
         "input_tokens": 1000, "output_tokens": 200, "outcome": "ok"},
        {"event": "subagent_start", "node": "task/intake", "kind": "subagent", "allocated_usd": 1.0},
        {"event": "model_call", "node": "task/intake", "role": "intake", "model": "claude-sonnet-5-5", "usd": 0.02},
        {"event": "tool_call", "id": "E1", "tool": "check_words", "ok": True, "trust": "bounded_pass",
         "trust_reason": "words up to length 8", "scope": "task/intake", "role": "intake",
         "args_preview": "{\"max_len\": 8}", "cost_usd": 0, "elapsed_s": 0.4},
        {"event": "tool_call", "id": "E2", "tool": "validate_ir", "ok": False, "status": "error",
         "error_kind": "ValueError", "error": "bad", "trust": "not_verified", "scope": "task"},
        {"event": "subagent_end", "node": "task/intake", "returned_usd": 0.98},
        {"event": "spawn_start", "name": "prover", "model": "claude-haiku-5-5", "budget_usd": 0.5},
        {"event": "budget_wrap_up", "node": "task", "available_usd": 0.3},
        {"event": "hitl_awaiting", "point": "report", "interrupt_id": "i1",
         "draft": {"verdict": "CFL", "trust": "verified", "confidence_cap": 0.85, "unknown_ids": []}},
        {"event": "hitl_decision", "point": "report", "decision": "edit", "source": "human",
         "edited_fields": ["template"], "comment": "shorter"},
        {"event": "done", "status": "submitted", "verdict": "CFL", "language_class": "cfl", "trust": "verified",
         "confidence_cap": 0.85},
    ]
    return [{**e, "ts": ts, "budget": b, "usage": u} for e in evs]


@needs_node
def test_timeline_rows_from_progress_events():
    rows = _one("deepTimeline", _sample_events())
    by = {r["event"] + ":" + str(i): r for i, r in enumerate(rows)}
    assert len(rows) == len(_sample_events())
    tool_ok = rows[5]
    assert tool_ok["group"] == "tools" and tool_ok["trust"] == "bounded_pass" and tool_ok["depth"] == 1
    assert tool_ok["args"] == "{\"max_len\": 8}" and tool_ok["trustReason"] == "words up to length 8"
    tool_err = rows[6]
    assert tool_err["ok"] is False and tool_err["cls"] == "err" and "ValueError: bad" in tool_err["detail"]
    assert rows[2]["model"] == "claude-opus-5-5" and rows[2]["cost"] == 0.05
    assert rows[3]["title"].endswith("intake started")
    assert rows[8]["group"] == "agents" and rows[8]["model"] == "claude-haiku-5-5"
    assert rows[9]["cls"] == "warn"
    assert rows[10]["group"] == "hitl" and rows[10]["trust"] == "verified"
    assert "confidence cap 0.85" in rows[10]["detail"] and "unknown ids" not in rows[10]["detail"]
    assert "edited: template" in rows[11]["detail"]
    assert rows[12]["cls"] == "done" and rows[12]["trust"] == "verified"
    assert all(r["time"] for r in by.values())
    tools_only = _one("deepFilterRows", rows, "tools")
    assert [r["event"] for r in tools_only] == ["tool_call", "tool_call"]


@needs_node
def test_tree_and_budget_from_fold_events():
    """Contract: the JS views take the shapes deep_routes.fold_events produces."""
    folded = dr.fold_events(_sample_events())
    tree = _one("deepFlattenTree", folded["subagents"])
    assert [(n["node"], n["depth"]) for n in tree] == [("task", 0), ("task/intake", 1)]
    assert tree[0]["subtree"] == pytest.approx(0.07)
    assert tree[1]["models"] == {"claude-sonnet-5-5": 0.02} and tree[1]["status"] == "closed"
    assert tree[1]["branch"] == "└ "
    budget = {**folded["budget"], "day": {"spent_usd": 18, "limit_usd": 20, "can_start": False}}
    v = _one("deepBudgetView", budget, "mock")
    assert v["task"]["pct"] == pytest.approx(2.0) and v["task"]["reservePct"] == pytest.approx(6.0)
    assert v["day"]["state"] == "over" and v["warn"] is True
    assert "day limit reached" in v["note"] and "mock" in v["note"]
    v2 = _one("deepBudgetView", {"spent_usd": 4.9, "limit_usd": 5, "wrap_up": True}, "live")
    assert v2["task"]["state"] == "warn" and "wrap-up" in v2["note"]
    assert _one("deepBudgetView", None, None)["task"]["text"] == "—"


@needs_node
def test_estimate_text():
    est = {"cap_usd": 5, "reserve_for_report_usd": 0.3, "per_subagent_max_usd": 2,
           "day": {"spent_usd": 1, "limit_usd": 20, "can_start": True},
           "run": {"n": 0, "estimated_cost_usd": None}, "roles": {"main": "claude-opus-5-5"},
           "models": [{"tier": "opus", "model": "claude-opus-5-5", "call_worst_case_usd": 0.6}]}
    r = _js([["deepEstimateView", est, {"mode": "mock"}],
             ["deepEstimateView", est, {"mode": "live", "mainModel": "claude-opus-5-5"}],
             ["deepEstimateView", {**est, "day": {"spent_usd": 17, "limit_usd": 20, "can_start": False}},
              {"mode": "live"}]])
    assert "$0" in r[0]["text"] and r[0]["cls"] == ""
    assert "no data yet" in r[1]["text"] and "main opus" in r[1]["text"] and r[1]["cls"] == "strong"
    assert r[2]["cls"] == "blocked" and "refuses" in r[2]["text"]


@needs_node
def test_evidence_rows_flag_basis_refuted_and_unknown():
    result = {"basis": ["E1"], "refuted_evidence": ["E2"], "unknown_ids": ["E9"],
              "evidence": [{"id": "E1", "tool": "cyk", "ok": True, "trust": "verified"},
                           {"id": "E2", "tool": "check_words", "ok": False, "trust": "refuted",
                            "trust_reason": "counterexample"}],
              "steps": [{"evidence_ids": ["E1", "E9"]}, {"evidence_ids": ["E1"]}]}
    rows = _one("deepEvidenceRows", result)
    assert [(r["id"], r["basis"], r["refuted"], r["missing"]) for r in rows] == [
        ("E1", True, False, False), ("E2", False, True, False), ("E9", False, False, True)]
    assert rows[0]["steps"] == [1, 2] and rows[2]["steps"] == [1]
    assert rows[1]["reason"] == "counterexample"
    assert _one("deepEvidenceRows", None) == []


@needs_node
def test_small_helpers():
    r = _js([["deepWord", ""], ["deepWord", "ab"], ["deepTrustClass", "refuted"], ["deepTrustClass", "weird"],
             ["deepShortModel", "claude-sonnet-5-5"], ["deepSame", {"a": 1, "b": [1, {"c": 2}]},
                                                       {"b": [1, {"c": 2}], "a": 1}],
             ["deepModelName", {"effective_model": "claude-haiku-5-5", "requested": "opus"}],
             ["deepArgsEdits", {"cmd": "ls", "n": 1}, '{"cmd": "ls -l", "n": 1}'],
             # a HITL 2 template edit shows in result.json `rendered.template`, not in the run's template
             ["deepReportTemplate", {"template": "short_verdict"}, {"rendered": {"template": "exam_solution"}}],
             ["deepReportTemplate", {"template": "short_verdict"}, None]])
    assert r[8:] == ["exam_solution", "short_verdict"]
    assert r[:5] == ["ε", "ab", "trust trust-refuted", "trust trust-not_verified", "sonnet"]
    assert r[5] is True
    assert r[6] == "claude-haiku-5-5"
    assert r[7] == {"payload": {"cmd": "ls -l"}, "changed": ["cmd"]}


# ---------------------------------------------------------------------------
# served by the server
# ---------------------------------------------------------------------------
def test_static_files_served_with_deep_section():
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from ui_server import server as srv

    handler = getattr(srv, "Handler", None) or getattr(srv, "LabHandler", None)
    if handler is None:
        pytest.skip("server handler class not found")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/app.js") as r:
            body = r.read().decode("utf-8")
        assert BEGIN in body and "function deepStartRun" in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert 'id="deep-view"' in r.read().decode("utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()
