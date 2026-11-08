"""Deep Agent tab of TFL Lab: the page structure (runs | chat | report), the API
surface it calls and its pure helpers (run under node against the shapes
ui_server/deep_routes.py returns). The tab's code is ui_server/static/deep.js;
its pure helpers sit between the "deep: pure helpers" markers there."""
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
DEEP = (STATIC / "deep.js").read_text(encoding="utf-8")
CSS = (STATIC / "lab.css").read_text(encoding="utf-8")

BEGIN = "// ---- deep: pure helpers begin"
END = "// ---- deep: pure helpers end ----"

# fields the tab sends that the backend contract adds (review modes, "approve the rest");
# the tab falls back to the older fields when a server refuses them
NEW_CREATE_KEYS = {"review_mode"}
NEW_RESUME_KEYS = {"approve_rest"}

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _deep_markup():
    return INDEX[INDEX.index('id="deep-view"'):INDEX.index('id="deep-settings-back"')]


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------
def test_deep_tab_is_runs_chat_report():
    deep = _deep_markup()
    # left: new task, runs, settings
    for needle in ('id="deep-new"', 'id="deep-run-list"', 'id="deep-run-count"', 'id="deep-settings-open"',
                   # center: header with the slim budget line, the stream, the composer
                   'id="deep-head"', 'id="dh-title"', 'id="dh-status"', 'id="deep-budget-line"', 'id="deep-cancel"',
                   'id="deep-log-open"', 'id="deep-stream"', 'id="deep-feed"', 'id="deep-tail"',
                   'id="deep-composer"', 'id="deep-drop"', 'id="deep-text"', 'id="deep-file-input"', 'id="deep-run"',
                   'id="deep-review-mode"', 'id="deep-budget"', 'id="deep-main-model"', 'id="deep-template"',
                   'id="deep-mode"', 'id="deep-mock-inline"', 'id="deep-live-group"', 'id="deep-confirm"',
                   'id="deep-estimate"', 'id="deep-msg"',
                   # right: the report document, closable / widenable, the log behind a small link
                   'id="deep-report-pane"', 'id="deep-report"', 'id="deep-log"', 'id="deep-panel-close"',
                   'id="deep-panel-wide"', 'id="deep-panel-log"'):
        assert needle in deep, needle
    assert "Новая задача" in deep and "Запуски" in deep
    assert 'placeholder="Опишите задачу или перетащите PDF/изображение"' in deep
    assert 'id="deep-settings-back"' in INDEX
    # the old overloaded layout is gone: no tab strip, no timeline filters, no budget footer
    for gone in ('data-dtab=', 'id="deep-timeline"', 'id="deep-budget-bar"', 'id="deep-plan"', 'id="deep-hitl1"',
                 'id="deep-auto-approve"'):
        assert gone not in INDEX, gone
    # the existing workspace and its tabs stay
    for needle in ('id="workspace"', 'data-tab="progress"', 'id="run-list"', 'id="settings-back"'):
        assert needle in INDEX, needle


def test_deep_tab_avoids_the_pipelines_tab_classes():
    # app.js binds every `.tab` / `.panel` to the pipelines result pane; the deep pane must not use them
    tokens = {t for cls in re.findall(r'class="([^"]*)"', _deep_markup()) for t in cls.split()}
    assert "tab" not in tokens and "panel" not in tokens


def test_deep_js_is_loaded_after_app_js():
    a = INDEX.index('<script src="/static/app.js"></script>')
    d = INDEX.index('<script src="/static/deep.js"></script>')
    assert a < d
    # the deep code moved out of app.js; app.js only calls into it from the shared shortcuts
    assert BEGIN not in APP and "function deepStartRun" not in APP
    assert "function deepStartRun" in DEEP and "function deepRunShortcut" in DEEP
    for fn in ("isDeepMode", "deepOpenSettings", "deepCloseSettings", "deepRunShortcut"):
        assert fn + "(" in APP and f"function {fn}(" in DEEP, fn


def test_deep_frontend_calls_every_deep_endpoint():
    for path in ("'/deep/settings'", "'/deep/mock-scripts'", "'/deep/runs'", "/deep/estimate",
                 "/resume`", "/cancel`", "/render`", "/files/"):
        assert path in DEEP, path
    assert "'PUT'" in DEEP
    # fields of POST /api/deep/runs the tab sends are accepted by the backend (or are the new contract)
    sent = {"text", "file_b64", "mime", "name", "template", "settings_override", "auto_approve",
            "mock_script", "live", "confirm_spend", "review_mode"}
    assert sent <= set(dr.CREATE_KEYS) | NEW_CREATE_KEYS
    assert {"decision", "payload", "comment", "confirm_spend", "approve_rest"} <= set(dr.RESUME_KEYS) | NEW_RESUME_KEYS


def test_report_iframe_is_sandboxed_without_same_origin():
    assert "setAttribute('sandbox', 'allow-scripts')" in DEEP
    assert "allow-same-origin" not in DEEP


def test_deep_css_layout():
    assert "body.mode-deep .workspace" in CSS
    assert "body:not(.mode-deep) .deep-view" in CSS
    assert ".deep-view.has-panel" in CSS and ".deep-view:not(.has-panel) .dk-report" in CSS
    assert ".deep-view.has-panel.panel-wide .dk-chat" in CSS
    assert "@media (max-width: 1100px)" in CSS
    for t in ("verified", "refuted", "bounded_pass", "well_formed", "not_verified"):
        assert f".trust-{t}" in CSS
    # the removed layout's styles went with it
    for gone in (".dtab", ".dpanel", ".tl-row", ".bbar"):
        assert gone not in CSS, gone


def test_helpers_region_is_marked_once():
    assert DEEP.count(BEGIN) == 1 and DEEP.count(END) == 1
    assert DEEP.index(BEGIN) < DEEP.index(END)


def test_favicon_link_and_file():
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in INDEX
    svg = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg") and "#5fbfa8" in svg


# ---------------------------------------------------------------------------
# pure helpers under node
# ---------------------------------------------------------------------------
def _js(calls):
    """Run [(fn, args...)] through the deep helpers; return the list of results."""
    numbers = APP[APP.index("function num(v)"):APP.index("const projectLabel")]
    helpers = DEEP[DEEP.index(BEGIN):DEEP.index(END)]
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
    assert "лимит 2 MB" in r[4]
    assert r[5] is None
    assert "не поддерживается" in r[6]
    assert r[7] == "JVBERi0="


SETTINGS = {"budget": {"per_task_usd": 5, "per_day_usd": 20}, "models": {"roles": {"main": "claude-opus-5-5"}},
            "hitl": {"formalization": True, "report": True, "execute": False}}


@needs_node
def test_override_holds_only_changed_values():
    same = {"perTaskUsd": "5", "mainModel": "claude-opus-5-5", "hitlFormalization": True, "hitlReport": True}
    changed = {"perTaskUsd": "1.5", "mainModel": "claude-haiku-5-5", "hitlFormalization": False,
               "hitlReport": True}
    r = _js([["deepOverride", same, SETTINGS], ["deepOverride", changed, SETTINGS],
             ["deepOverride", {"perTaskUsd": "5", "mainModel": None}, SETTINGS]])
    assert r[0] is None
    assert r[1] == {"budget": {"per_task_usd": 1.5}, "models": {"roles": {"main": "claude-haiku-5-5"}},
                    "hitl": {"formalization": False}}
    assert r[2] is None       # "agent picks" sends no model override


@needs_node
def test_review_mode_of_saved_settings():
    r = _js([["deepReviewModeOf", SETTINGS],
             ["deepReviewModeOf", {"hitl": {"formalization": True, "report": False}}],
             ["deepReviewModeOf", {"hitl": {"formalization": False, "report": False}}],
             ["deepReviewModeOf", None]])
    assert r == ["all", "formalization", "auto", "all"]


@needs_node
def test_create_body_follows_the_create_contract():
    r = _js([
        ["deepCreateBody", {"text": " L = {a^n b^n} ", "mode": "mock", "mockName": "cfl_anbmcm.json",
                            "reviewMode": "all"}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": None}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": False}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "mockName": "a.json",
                            "template": "short_verdict", "reviewMode": "formalization"}],
        ["deepCreateBody", {"file": {"b64": "JVBE", "mime": "application/pdf", "name": "t.pdf"},
                            "mode": "inline", "mockInline": '{"models": {"main": ["hi"]}}', "reviewMode": "auto"}],
        ["deepCreateBody", {"text": "x", "mode": "inline", "mockInline": '{"models": {}}'}],
        ["deepCreateBody", {"file": {"b64": None, "mime": "image/png"}, "mode": "mock", "mockName": "a"}],
        ["deepCreateBody", {"text": "  ", "mode": "mock", "mockName": "a"}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": "a", "reviewMode": "sometimes"}],
    ])
    assert r[0] == {"body": {"text": "L = {a^n b^n}", "review_mode": "all", "mock_script": "cfl_anbmcm.json"}}
    assert "сценарий mock" in r[1]["error"]
    assert "подтверждаю расходы" in r[2]["error"]
    assert r[3] == {"body": {"text": "x", "template": "short_verdict", "review_mode": "formalization",
                             "live": True, "confirm_spend": True}}
    assert r[4] == {"body": {"file_b64": "JVBE", "mime": "application/pdf", "name": "t.pdf", "review_mode": "auto",
                             "mock_script": {"models": {"main": ["hi"]}}}}
    assert "непустой" in r[5]["error"]
    assert "читается" in r[6]["error"]
    assert "опишите задачу" in r[7]["error"]
    assert "неизвестный режим" in r[8]["error"]
    for res in r:
        if "body" in res:
            assert set(res["body"]) <= set(dr.CREATE_KEYS) | NEW_CREATE_KEYS


@needs_node
def test_create_body_legacy_server_folds_the_review_mode():
    """A server without review_mode: the mode is sent as the older hitl override / auto_approve."""
    r = _js([
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": "a.json", "reviewMode": "auto", "legacy": True,
                            "autoApprove": True, "override": {"hitl": {"formalization": False, "report": False}}}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": "a.json", "reviewMode": "formalization",
                            "legacy": True, "override": {"hitl": {"report": False}}}],
    ])
    assert r[0]["body"] == {"text": "x", "settings_override": {"hitl": {"formalization": False, "report": False}},
                            "auto_approve": True, "mock_script": "a.json"}
    assert r[1]["body"] == {"text": "x", "settings_override": {"hitl": {"report": False}}, "mock_script": "a.json"}
    for res in r:
        assert set(res["body"]) <= set(dr.CREATE_KEYS)


@needs_node
def test_create_body_keeps_review_and_limits_on_live_runs():
    """A live run picks "no stops" only as review_mode (auto_approve stays mock-only), keeps the hitl
    points of the settings and may only lower the money limit; the composer says so before sending."""
    r = _js([
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "reviewMode": "auto"}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "autoApprove": True}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True,
                            "override": {"hitl": {"report": False}}}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "savedPerTask": 10,
                            "override": {"budget": {"per_task_usd": 12}}}],
        ["deepCreateBody", {"text": "x", "mode": "live", "confirm": True, "savedPerTask": 10,
                            "override": {"budget": {"per_task_usd": 1}}}],
        ["deepCreateBody", {"text": "x", "mode": "mock", "mockName": "a.json", "savedPerTask": 10,
                            "override": {"budget": {"per_task_usd": 50}}}],
    ])
    assert r[0]["body"]["review_mode"] == "auto" and r[0]["body"]["confirm_spend"] is True
    assert "только для mock" in r[1]["error"]
    assert "точки проверки" in r[2]["error"] and "report" in r[2]["error"]
    assert "только уменьшить" in r[3]["error"]
    assert r[4]["body"]["settings_override"] == {"budget": {"per_task_usd": 1}} and r[4]["body"]["live"] is True
    assert r[5]["body"]["settings_override"] == {"budget": {"per_task_usd": 50}}


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


FORMALIZATION_ARGS = {
    "task_text": "L = (ab)*a | b", "transcription": None, "class": "reg",
    "ir": {"task_type": "regularity", "language_spec": {"kind": "regex", "alphabet": ["a", "b"],
                                                        "pattern": "(ab)*a|b"}},
    "valid": True, "errors": [],
    "examples": {"in_language": ["a", "b", "aba"], "not_in_language": ["", "ab"], "max_len": 6},
}


@needs_node
def test_pending_view_reads_the_pending_point_object():
    detail = {"run": {"status": "awaiting_human", "pending_point": "report"},
              "pending_point": {"point": "report", "title": "Черновик отчёта готов",
                                "summary": ["L нерегулярен", "", "trust: verified", "a", "b", "c"],
                                "details_md": "## детали", "editable": ["verdict", "template"],
                                "interrupt_id": "i9"},
              "hitl": {"pending": {"point": "report", "interrupt_id": "i9", "card": "old card"}}}
    v = _one("deepPendingView", detail)
    assert v["point"] == "report" and v["title"] == "Черновик отчёта готов"
    assert v["summary"] == ["L нерегулярен", "trust: verified", "a", "b"]       # blanks dropped, at most 4
    assert v["detailsMd"] == "## детали" and v["editable"] == ["verdict", "template"]
    assert v["legacy"] is False and v["interruptId"] == "i9"
    assert v["allowed"] == ["approve", "reject", "edit"]
    # the same object inside run works too; a finished run has no stop
    v2 = _one("deepPendingView", {"run": {"status": "awaiting_human",
                                          "pending_point": {"point": "execute", "summary": ["ls"],
                                                            "allowed_decisions": ["approve", "reject"]}}})
    assert v2["point"] == "execute" and v2["allowed"] == ["approve", "reject"] and v2["title"]
    assert _one("deepPendingView", {"run": {"status": "completed"}, "pending_point": {"point": "report"}}) is None
    assert _one("deepPendingView", None) is None


@needs_node
def test_pending_view_falls_back_to_the_old_hitl_pending():
    detail = {"run": {"status": "awaiting_human", "pending_point": "formalization"},
              "hitl": {"pending": {"point": "formalization", "interrupt_id": "i1", "card": "# hitl card",
                                   "allowed_decisions": ["approve", "edit", "reject"],
                                   "actions": [{"name": "submit_formalization", "args": FORMALIZATION_ARGS}]}}}
    v = _one("deepPendingView", detail)
    assert v["legacy"] is True and v["point"] == "formalization" and v["title"] == "Формализация"
    assert v["summary"][0] == "REG · (ab)*a|b над {a, b}"
    assert v["summary"][1] == "в L: a, b, aba · нет: ε, ab"
    assert v["detailsMd"] == "# hitl card" and v["editable"] is None
    bad = {**FORMALIZATION_ARGS, "valid": False, "errors": ["alphabet is empty"]}
    v2 = _one("deepPendingView", {"run": {"status": "awaiting_human"},
                                  "hitl": {"pending": {"point": "formalization", "args": bad}}})
    assert any("validate_ir" in s and "alphabet is empty" in s for s in v2["summary"])
    # a minimal card (summary [] when the server could not build one) still gets lines from the request
    v3 = _one("deepPendingView", {**detail, "pending_point": {"point": "formalization", "title": "Формализация",
                                                              "summary": [], "details_md": "", "editable": []}})
    assert v3["summary"][0] == "REG · (ab)*a|b над {a, b}" and v3["legacy"] is False
    assert v3["allowed"] == ["approve", "edit", "reject"]        # from the request's allowed_decisions
    rep = _one("deepPendingView", {"run": {"status": "awaiting_human"},
                                   "hitl": {"pending": {"point": "report",
                                                        "draft": {"verdict": "REG", "trust": "bounded_pass",
                                                                  "confidence_cap": 0.7}}}})
    assert rep["summary"] == ["REG", "bounded pass · cap 0.70"]


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
    assert "требует IR" in r[2]["error"]          # deep_routes / hitl: a class change requires the IR
    assert r[3]["changed"] == ["ir", "class"]
    assert "не JSON" in r[4]["error"]
    assert "пуста" in r[5]["error"]
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
    assert "verdict не может быть пустым" in r[2]["error"]
    assert "непустым" in r[3]["error"]
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
        ["deepResumeBody", "approve", {"approveRest": True}],
        ["deepResumeBody", "reject", {"comment": "no", "approveRest": True}],
        ["deepResumeBody", "approve", {"approveRest": True, "live": True, "confirm": True}],
    ])
    assert r[0] == {"body": {"decision": "approve"}}
    assert "нужен комментарий" in r[1]["error"]
    assert r[2] == {"body": {"decision": "reject", "comment": "wrong IR"}}
    assert "ничего не изменено" in r[3]["error"]
    assert r[4] == {"body": {"decision": "edit", "comment": "c", "payload": {"class": "ll"}}}
    assert "подтверждаю расходы" in r[5]["error"]
    assert r[6] == {"body": {"decision": "approve", "confirm_spend": True}}
    assert "нельзя" in r[7]["error"]
    assert r[8] == {"body": {"decision": "approve", "approve_rest": True}}
    assert "только вместе с «принять»" in r[9]["error"]
    assert r[10] == {"body": {"decision": "approve", "approve_rest": True, "confirm_spend": True}}
    for res in r:
        if "body" in res:
            assert set(res["body"]) <= set(dr.RESUME_KEYS) | NEW_RESUME_KEYS


def _sample_events():
    """The order the mock reg run writes (progress.py): the `task` tool call lands before subagent_end."""
    ts = "2026-11-08T04:56:21.167+03:00"
    b = {"spent_usd": 0.1, "limit_usd": 5.0, "reserve_usd": 0.3, "wrap_up": False, "exhausted": False}
    evs = [
        {"event": "segment_start", "segment": 1, "mode": "mock", "template": "full_proof", "resumed_with": None},
        {"event": "model_call", "node": "task", "role": "main", "model": "claude-opus-5-5", "usd": 0.05,
         "outcome": "ok"},
        {"event": "todos", "todos": [{"content": "parse", "status": "completed"},
                                     {"content": "prove", "status": "in_progress"}], "role": "main"},
        {"event": "tool_call", "id": "T1", "tool": "write_todos", "ok": True, "scope": "task"},
        {"event": "model_call", "node": "task", "role": "main", "model": "claude-opus-5-5", "usd": 0.01},
        {"event": "subagent_start", "node": "task/ir-builder", "kind": "subagent", "allocated_usd": 1.0},
        {"event": "model_call", "node": "task/ir-builder", "role": "intake", "model": "claude-sonnet-5-5",
         "usd": 0.02},
        {"event": "tool_call", "id": "E1", "tool": "check_words", "ok": True, "trust": "bounded_pass",
         "trust_reason": "words up to length 8", "scope": "task/ir-builder", "args_preview": "{\"max_len\": 8}"},
        {"event": "tool_call", "id": "T2", "tool": "task", "subagent_type": "ir-builder", "ok": True,
         "scope": "task"},
        {"event": "subagent_end", "node": "task/ir-builder", "returned_usd": 0.98},
        {"event": "model_call", "node": "task", "role": "main", "model": "claude-opus-5-5", "usd": 0.03},
        {"event": "tool_call", "id": "E2", "tool": "validate_ir", "ok": False, "status": "error",
         "error_kind": "ValueError", "error": "bad", "trust": "not_verified", "scope": "task"},
        {"event": "tool_call", "id": "E3", "tool": "cyk", "ok": True, "trust": "verified",
         "trust_reason": "derivation found", "scope": "task"},
        {"event": "spawn_start", "name": "prover", "model": {"effective_model": "claude-haiku-5-5"},
         "budget_usd": 0.5},
        {"event": "subagent_start", "node": "task/prover", "kind": "spawn", "allocated_usd": 0.5},
        {"event": "model_call", "node": "task/prover", "role": "prover", "model": "claude-haiku-5-5", "usd": 0.004},
        {"event": "subagent_end", "node": "task/prover", "returned_usd": 0.49},
        {"event": "spawn_end", "name": "prover", "status": "ok", "spent_usd": 0.004},
        {"event": "budget_wrap_up", "node": "task", "available_usd": 0.3},
        {"event": "hitl_awaiting", "point": "report", "interrupt_id": "i1"},
        {"event": "hitl_decision", "point": "report", "decision": "edited", "source": "human",
         "edited_fields": ["template"], "comment": "shorter"},
        {"event": "hitl_decision", "point": "formalization", "decision": "auto_approved", "source": "auto_approve"},
        {"event": "segment_end", "status": "error", "error": "RuntimeError: boom"},
        {"event": "done", "status": "submitted", "verdict": "CFL", "language_class": "cfl", "trust": "verified",
         "confidence_cap": 0.85},
    ]
    return [{**e, "ts": ts, "budget": b} for e in evs]


@needs_node
def test_chat_items_group_the_activity():
    items = _one("deepChatItems", _sample_events())
    kinds = [i["type"] for i in items]
    assert kinds == ["activity", "plan", "activity", "agent", "activity", "agent", "note", "decision", "decision",
                     "note", "done"]
    # one collapsed row per run of consecutive main calls; write_todos and the `task` call are not repeated
    first, second, third = (i for i in items if i["type"] == "activity")
    assert first["modelCalls"] == 1 and first["tools"] == []
    assert second["modelCalls"] == 1 and second["tools"] == []
    assert [t["name"] for t in third["tools"]] == ["validate_ir", "cyk"] and third["errors"] == 1
    assert third["tools"][0]["error"] == "ValueError: bad" and third["tools"][1]["trust"] == "verified"
    assert third["cost"] == pytest.approx(0.03)
    sub, spawn = (i for i in items if i["type"] == "agent")
    assert sub["name"] == "ir-builder" and sub["status"] == "closed" and sub["returned"] == pytest.approx(0.98)
    assert [t["name"] for t in sub["tools"]] == ["check_words"] and sub["tools"][0]["reason"] == "words up to length 8"
    assert sub["models"] == ["sonnet"]
    # spawn_start + its budget scope are one row
    assert spawn["name"] == "prover" and spawn["kind"] == "spawn" and spawn["model"] == "haiku"
    assert spawn["status"] == "closed" and spawn["spent"] == pytest.approx(0.004) and spawn["modelCalls"] == 1
    notes = [i for i in items if i["type"] == "note"]
    assert notes[0]["cls"] == "warn" and notes[1]["cls"] == "err" and "boom" in notes[1]["text"]
    d1, d2 = (i for i in items if i["type"] == "decision")
    assert d1["text"] == "✎ отчёт исправлен · вы (template) · «shorter»" and d1["kind"] == "edited"
    assert d2["text"] == "✓ формализация принята · автоматически" and d2["kind"] == "auto"
    done = items[-1]
    assert done["status"] == "submitted" and done["trust"] == "verified" and done["cap"] == pytest.approx(0.85)
    # keys are stable across polls (the expanded rows survive re-renders)
    again = _one("deepChatItems", _sample_events()[:12])
    assert [i["key"] for i in again] == [i["key"] for i in items][:len(again)]


@needs_node
def test_activity_line_and_decisions():
    items = _one("deepChatItems", _sample_events())
    lines = _js([["deepActivityLine", i] for i in items if i["type"] in ("activity", "agent")])
    assert lines[0]["text"] == "1 вызов модели · opus · $0.0500"
    assert lines[2]["text"] == "1 инструмент · sonnet · $0.0200" and lines[2]["trusts"] == ["bounded_pass"]
    assert lines[3]["text"] == "2 инструмента · opus · $0.0300" and lines[3]["errors"] == 1
    assert lines[3]["trusts"] == ["verified"]
    assert lines[4]["text"] == "1 вызов модели · haiku · $0.0040"     # a spawned agent shows what it spent
    r = _js([["deepDecisionText", {"point": "formalization", "decision": "approved", "source": "human"}],
             ["deepDecisionText", {"point": "report", "decision": "rejected", "source": "human", "comment": "нет"}],
             ["deepDecisionText", {"point": "execute", "decision": "approved", "source": "approve_rest"}],
             ["deepPlural", 1, ["инструмент", "инструмента", "инструментов"]],
             ["deepPlural", 4, ["инструмент", "инструмента", "инструментов"]],
             ["deepPlural", 11, ["инструмент", "инструмента", "инструментов"]],
             ["deepPlural", 22, ["инструмент", "инструмента", "инструментов"]]])
    assert r[0] == {"text": "✓ формализация принята · вы", "kind": "approved"}
    assert r[1] == {"text": "✗ отчёт отклонён · вы · «нет»", "kind": "rejected"}
    assert r[2]["text"] == "✓ команда разрешена · вы, всё до конца"
    assert r[3:] == ["инструмент", "инструмента", "инструментов", "инструмента"]


@needs_node
def test_budget_line_from_fold_events():
    """Contract: the header line takes the budget shape deep_routes.fold_events produces."""
    folded = dr.fold_events(_sample_events())
    budget = {**folded["budget"], "day": {"spent_usd": 18, "limit_usd": 20, "can_start": False}}
    v = _one("deepBudgetLine", budget, "mock")
    assert v["text"] == "$0.1000 / $5 · сегодня $18 / $20"
    assert v["warn"] is True and "mock" in v["title"] and "дневной лимит" in v["title"]
    v2 = _one("deepBudgetLine", {"spent_usd": 0.0107, "limit_usd": 1, "day": {"spent_usd": 0.01, "limit_usd": 20}},
              "live")
    assert v2["text"] == "$0.0107 / $1 · сегодня $0.01 / $20" and v2["warn"] is False
    assert _one("deepBudgetLine", None, None)["text"] == ""


@needs_node
def test_estimate_line():
    est = {"cap_usd": 5, "reserve_for_report_usd": 0.3, "day": {"spent_usd": 1, "limit_usd": 20, "can_start": True},
           "run": {"n": 2, "estimated_cost_usd": 0.4}}
    r = _js([["deepEstimateLine", est, {"mode": "mock"}],
             ["deepEstimateLine", est, {"mode": "live"}],
             ["deepEstimateLine", {**est, "day": {"spent_usd": 17, "limit_usd": 20, "can_start": False}},
              {"mode": "live"}],
             ["deepEstimateLine", None, {"mode": "live"}]])
    assert r[0] == {"text": "", "cls": ""}
    assert r[1]["text"] == "live: платные вызовы API, не больше $5 за задачу · обычно ~$0.4000 · сегодня $1 / $20"
    assert r[1]["cls"] == "strong"
    assert r[2]["cls"] == "blocked" and "откажет" in r[2]["text"]
    assert r[3]["cls"] == "strong"


@needs_node
def test_run_titles_and_list_lines():
    long = "Докажите, что язык L = {a^n b^n c^m | n, m >= 0} контекстно-свободен, и постройте грамматику"
    r = _js([["deepTitleFromText", long],
             ["deepTitleFromText", "  короткая   задача "],
             ["deepRunTitle", {"run_id": "abc", "input": {"kind": "pdf", "name": "exam.pdf"}}, None],
             ["deepRunTitle", {"run_id": "abc", "title": "своё имя"}, "текст"],
             ["deepRunTitle", {"run_id": "abc", "input": {"kind": "text"}}, None],
             ["deepRunSub", {"status": "awaiting_human", "pending_point": "formalization", "mode": "mock"}],
             ["deepRunSub", {"status": "awaiting_human", "pending_point": {"point": "report"}, "mode": "live"}],
             ["deepRunSub", {"status": "completed", "mode": "mock"}]])
    assert r[0].endswith("…") and len(r[0]) <= 47 and r[0].startswith("Докажите, что язык")
    assert r[1] == "короткая задача"
    assert r[2] == "PDF · exam.pdf"
    assert r[3] == "своё имя"
    assert r[4] == "abc"
    assert r[5] == "ждёт: формализация"
    assert r[6] == "ждёт: отчёт · live"
    assert r[7] == "готово"


@needs_node
def test_small_helpers():
    r = _js([["deepWord", ""], ["deepWord", "ab"], ["deepTrustClass", "refuted"], ["deepTrustClass", "weird"],
             ["deepShortModel", "claude-sonnet-5-5"], ["deepSame", {"a": 1, "b": [1, {"c": 2}]},
                                                       {"b": [1, {"c": 2}], "a": 1}],
             ["deepModelName", {"effective_model": "claude-haiku-5-5", "requested": "opus"}],
             ["deepArgsEdits", {"cmd": "ls", "n": 1}, '{"cmd": "ls -l", "n": 1}'],
             # a report-stop template edit shows in result.json `rendered.template`, not in the run's template
             ["deepReportTemplate", {"template": "short_verdict"}, {"rendered": {"template": "exam_solution"}}],
             ["deepReportTemplate", {"template": "short_verdict"}, None],
             ["deepUsdShort", 20], ["deepUsdShort", 1.5], ["deepUsdShort", 0.01], ["deepUsdShort", 0.004],
             ["deepUsdShort", 0]])
    assert r[:5] == ["ε", "ab", "trust trust-refuted", "trust trust-not_verified", "sonnet"]
    assert r[5] is True
    assert r[6] == "claude-haiku-5-5"
    assert r[7] == {"payload": {"cmd": "ls -l"}, "changed": ["cmd"]}
    assert r[8:10] == ["exam_solution", "short_verdict"]
    assert r[10:] == ["$20", "$1.50", "$0.01", "$0.0040", "$0"]


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
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/deep.js") as r:
            body = r.read().decode("utf-8")
        assert BEGIN in body and "function deepStartRun" in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert 'id="deep-view"' in r.read().decode("utf-8")
        for path in ("/static/favicon.svg", "/favicon.ico"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                assert r.status == 200 and r.headers["Content-Type"] == "image/svg+xml", path
                assert b"<svg" in r.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
