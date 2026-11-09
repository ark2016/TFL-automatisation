"""P3 HITL: the formalization stop (HITL 1), the report stop (HITL 2), the
run directory and resuming on a fresh agent over the SQLite checkpoint.

Every model is a ScriptedChatModel replay (``hitl_scripts``); the tools are
the real LL tools. Each ``start_run`` / ``resume_run`` builds a NEW agent on a
NEW SQLite connection and a NEW scripted factory whose cursors come from
``mock_cursors.json``, i.e. what a new process does. The subprocess version
of the cycle is in ``test_e2e_mock.py``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402

from tfl_deep_agent import agent as A  # noqa: E402
from tfl_deep_agent import cli, hitl  # noqa: E402
from tfl_deep_agent.intake import files as intake_files  # noqa: E402
from tfl_deep_agent.middleware import budget as B  # noqa: E402

from . import hitl_scripts as H  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(tmp_path / "runs"))


TASK = H.base_script()["task"]


def start(tmp_path, script, *, name="run", **kw):
    return hitl.start_run(tmp_path / name, kw.pop("task", TASK), settings=H.example_settings(),
                          mock_script=script, **kw)


def humans(run_dir) -> list[str]:
    return [str(m.content) for m in H.thread_messages(run_dir) if isinstance(m, HumanMessage)]


def tool_messages(run_dir, name: str) -> list[ToolMessage]:
    return [m for m in H.thread_messages(run_dir) if isinstance(m, ToolMessage) and m.name == name]


# ---------------------------------------------------------------------------
# HITL 1: formalization
# ---------------------------------------------------------------------------

def test_hitl1_stops_after_ir_builder_with_a_code_made_card(tmp_path):
    out = start(tmp_path, H.base_script())
    assert out.status == hitl.STATUS_AWAITING and out.exit_code == hitl.EXIT_AWAITING
    assert out.pending.point == hitl.FORMALIZATION
    args = out.pending.request["action_requests"][0]["args"]
    # the IR: the ir-builder's accepted validate_ir call (its answer has no JSON block)
    assert args["class"] == "ll" and args["valid"] is True
    assert args["ir"] == H.base_script()["defs"]["ir"]
    assert "ib-validate" in args["ir_source"]
    assert args["ir_fingerprint"].startswith("ir-")
    # restatement and words come from ir_to_text (code), with oracle labels
    assert "S → aAb | bBa" in args["restatement"]
    assert "ab" in args["examples"]["in_language"] and "" in args["examples"]["not_in_language"]
    assert args["transcription"] is None and args["task_text"] == TASK
    run = hitl.load_run(out.run_dir)
    assert run["status"] == "awaiting_human" and run["pending"]["point"] == "formalization"
    card = (out.run_dir / hitl.CARD_FILE).read_text(encoding="utf-8")
    assert "HITL 1" in card and "--resume" in card and args["ir_fingerprint"] in card
    # the ir-builder ran, the main model did not get another turn yet
    used = json.loads((out.run_dir / hitl.MOCK_CURSORS_FILE).read_text())["cursors"]
    assert {k: v for k, v in used.items() if v} == {"main": 2, "ir_builder": 2}
    assert not (out.run_dir / "result.json").exists()


def test_hitl1_reject_then_fix_then_approve(tmp_path):
    out = start(tmp_path, H.hitl1_reject_script())
    first = out.pending.request["action_requests"][0]["args"]["ir_builder_call_id"]
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("reject", comment="Проверь k: в условии LL(1)."))
    # the model saw the comment and asked ir-builder again: a new HITL 1 stop
    assert out.pending.point == hitl.FORMALIZATION
    assert out.pending.request["action_requests"][0]["args"]["ir_builder_call_id"] == "call-ir-builder-2" != first
    msgs = humans(out.run_dir)
    assert msgs[-1].startswith("HITL 1") and "REJECTED" in msgs[-1] and "Проверь k" in msgs[-1]
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.pending.point == hitl.REPORT
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.status == "submitted" and out.exit_code == 0
    recs = out.result["hitl"]["formalization_records"]
    assert [(r["id"], r["decision"]) for r in recs] == [("call-ir-builder", "rejected"),
                                                         ("call-ir-builder-2", "approved")]
    assert humans(out.run_dir)[-1].startswith("HITL 1") and "APPROVED" in humans(out.run_dir)[-1]


def test_hitl1_edit_ir_is_validated_before_resuming(tmp_path):
    out = start(tmp_path, H.base_script())
    with pytest.raises(hitl.DecisionError, match="validate_ir"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"ir": {"task_type": "nonsense"}}))
    # nothing was resumed: the run still waits at the same stop
    run = hitl.load_run(out.run_dir)
    assert run["status"] == "awaiting_human" and run["pending"]["point"] == "formalization"
    assert run["reviews"] == []


def test_hitl1_edit_transcription_and_ir_reach_the_model(tmp_path):
    out = start(tmp_path, H.base_script())
    ir = json.loads(json.dumps(H.base_script()["defs"]["ir"]))
    ir["source_text"] = H.EDITED_TRANSCRIPTION
    out = hitl.resume_run(out.run_dir, hitl.parse_decision(
        "edit", json.dumps({"transcription": H.EDITED_TRANSCRIPTION, "ir": ir}, ensure_ascii=False)))
    assert out.pending.point == hitl.REPORT
    msg = humans(out.run_dir)[-1]
    assert msg.startswith("HITL 1") and "WITH EDITS" in msg
    assert H.EDITED_TRANSCRIPTION in msg and '"source_text"' in msg
    review = hitl.load_run(out.run_dir)["reviews"][0]
    assert review["decision"] == "edited" and review["edited_fields"] == ["transcription", "ir"]
    assert review["transcription"] == H.EDITED_TRANSCRIPTION
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.result["task"] == H.EDITED_TRANSCRIPTION  # the report states the corrected task


def test_hitl1_already_approved_ir_is_not_asked_again(tmp_path):
    s = H.hitl1_reject_script()  # two ir-builder calls with the same IR
    out = start(tmp_path, s)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.pending.point == hitl.REPORT  # the second answer passed without a stop
    recs = hitl.run_state(out.run_dir)[hitl.STATE_KEY]
    assert [r["decision"] for r in recs if r["point"] == "formalization"] == ["approved", "unchanged"]


# ---------------------------------------------------------------------------
# HITL 1 with a PDF task
# ---------------------------------------------------------------------------

def test_pdf_task_goes_through_intake_and_hitl1(tmp_path, capsys):
    pdf = tmp_path / "Задача 3.pdf"
    pdf.write_bytes(H.make_pdf())
    script = H.write_script(tmp_path, H.pdf_script())
    run_dir = tmp_path / "pdf-run"
    rc = cli.main(["--file", str(pdf), "--mock-script", str(script), "--save", str(run_dir),
                   "--settings", str(H.SETTINGS_EXAMPLE), "--no-dialog"])
    assert rc == hitl.EXIT_AWAITING, capsys.readouterr().err
    run = hitl.load_run(run_dir)
    assert run["input"]["kind"] == "pdf" and run["input"]["pages"] == 1
    assert (run_dir / "input" / "task.pdf").read_bytes() == pdf.read_bytes()
    args = run["pending"]["request"]["action_requests"][0]["args"]
    assert args["source"]["kind"] == "pdf" and args["source"]["name"] == "Задача 3.pdf"
    assert args["transcription"] == H.INTAKE_TRANSCRIPTION  # the intake answer
    assert args["valid"] and args["class"] == "ll"
    card = (run_dir / hitl.CARD_FILE).read_text(encoding="utf-8")
    assert "Source: pdf" in card and "Transcription (intake)" in card and "мог быть «e»" in card
    # the intake subagent got the file through deepagents' state files
    first = humans(run_dir)[0]
    assert "/input/task.pdf" in first and "intake" in first
    assert tool_messages(run_dir, "task")[0].status == "success"

    rc = cli.main(["--resume", str(run_dir), "--decision", "edit",
                   "--payload", json.dumps({"transcription": H.EDITED_TRANSCRIPTION}, ensure_ascii=False)])
    assert rc == hitl.EXIT_AWAITING
    assert hitl.load_run(run_dir)["pending"]["point"] == "report"
    rc = cli.main(["--resume", str(run_dir), "--decision", "approve"])
    assert rc == 0, capsys.readouterr().err
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "submitted" and data["task"] == H.EDITED_TRANSCRIPTION
    assert data["input"]["kind"] == "pdf"
    assert data["hitl"]["reviews"][0]["edited_fields"] == ["transcription"]


def test_corrupt_pdf_is_refused_before_a_run(tmp_path, capsys):
    bad = tmp_path / "task.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    rc = cli.main(["--file", str(bad), "--mock-script", str(H.LL_SCRIPT), "--save", str(tmp_path / "r")])
    assert rc == 2 and "task.pdf" in capsys.readouterr().err
    assert not (tmp_path / "r" / hitl.RUN_FILE).exists()


# ---------------------------------------------------------------------------
# HITL 2: report
# ---------------------------------------------------------------------------

def _to_report(tmp_path, script, **kw):
    out = start(tmp_path, script, **kw)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.pending.point == hitl.REPORT
    return out


def test_hitl2_draft_is_rated_by_code_and_rendered(tmp_path):
    out = _to_report(tmp_path, H.base_script())
    text = out.pending.request["action_requests"][0]["description"]
    assert "verified" in text and "ev-ll-table" in text and "HITL 1" in text
    draft = json.loads((out.run_dir / "result.json").read_text(encoding="utf-8"))
    assert draft["status"] == "awaiting_human" and draft["report"]["verdict"] == "LL(1)"
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "ожидает" in md and "Без проверки человеком" in md
    assert hitl.load_run(out.run_dir)["draft"]["trust"] == "verified"


def test_hitl2_reject_then_fix_then_approve_on_fresh_agents(tmp_path):
    out = _to_report(tmp_path, H.reject_cycle_script())
    assert len(out.pending.request["action_requests"][0]["args"]["steps"]) == 2  # the draft
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("reject", {"comment": H.REJECT_COMMENT}))
    # the rejection is the submit_report error result; the model resubmitted
    rejected = tool_messages(out.run_dir, "submit_report")
    assert rejected[-1].status == "error" and H.REJECT_COMMENT in str(rejected[-1].content)
    assert out.pending.point == hitl.REPORT
    assert len(out.pending.request["action_requests"][0]["args"]["steps"]) == 3
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.status == "submitted" and out.exit_code == 0
    r = out.result
    assert r["report"]["summary"].startswith("Множества выбора") and len(r["steps"]) == 3
    assert r["human_review"] == "approved by a human at HITL 2"
    assert r["hitl"]["report_rejections"] == 1
    assert [x["decision"] for x in r["hitl"]["reviews"]] == ["approved", "rejected", "approved"]
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "Проверено человеком" in md and "утверждён" in md
    assert H.base_script()["models"]["main"] and json.loads(
        (out.run_dir / hitl.MOCK_CURSORS_FILE).read_text())["cursors"]["main"] == 6  # whole script used
    assert hitl.load_run(out.run_dir)["status"] == "submitted"


def test_hitl2_edit_fields_are_validated_and_marked(tmp_path):
    out = _to_report(tmp_path, H.base_script())
    with pytest.raises(hitl.DecisionError, match="does not validate"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"language_class": "nope"}))
    assert hitl.load_run(out.run_dir)["status"] == "awaiting_human"
    new_summary = "Грамматика LL(1): таблица без конфликтов (правка человека)."
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"summary": new_summary}))
    assert out.status == "submitted"
    assert out.result["report"]["summary"] == new_summary
    assert out.result["human_review"].startswith("approved with edits") and "summary" in out.result["human_review"]
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "с правками" in md and "summary" in md


def test_invalid_report_is_not_shown_to_the_human(tmp_path):
    s = H.base_script()
    bad = H.report_call(s, call_id="call-bad", steps=[])  # no steps: fails validation
    s["models"]["main"] = [*s["models"]["main"][:4], bad, s["models"]["main"][-1]]
    out = _to_report(tmp_path, s)
    # the invalid call went back to the model as a validation error, no stop for it
    errs = tool_messages(out.run_dir, "submit_report")
    assert errs and errs[0].status == "error"
    assert out.pending.request["action_requests"][0]["args"]["steps"]


# ---------------------------------------------------------------------------
# Evidence ids: submit_report refuses ids that are not in the ledger
# ---------------------------------------------------------------------------

def _refusals(run_dir) -> list[dict]:
    return [json.loads(str(m.content)) for m in tool_messages(run_dir, "submit_report") if m.status == "error"]


def test_fabricated_ids_are_refused_without_a_stop_then_the_honest_report_is_reviewed(tmp_path):
    out = _to_report(tmp_path, H.fabricated_then_honest_script())
    # the fabricated report never reached the human: the stop is for the honest resubmission
    honest = H.base_script()["models"]["main"][-1]["tool_calls"][0]["args"]
    assert out.pending.request["action_requests"][0]["args"]["steps"] == honest["steps"]
    assert out.pending.request["action_requests"][0]["args"]["evidence_ids"] == honest["evidence_ids"]
    (err,) = _refusals(out.run_dir)
    assert err["error_kind"] == "unknown_evidence_ids" and err["ok"] is False
    assert set(err["unknown_ids"]) == {"ev-made-up-step", "ev-made-up-verdict"}
    assert "ev-ll-table" in json.dumps(err["valid_ids"]) and "Resubmit submit_report" in err["error"]
    assert hitl.load_run(out.run_dir)["draft"]["unknown_ids"] == []
    done = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert done.status == "submitted" and done.result["trust"] == "verified" and not done.result["unknown_ids"]


def test_reg_report_citing_tool_names_is_refused_then_verified_with_ids(tmp_path):
    """reg-04 shape: (ab)*a|b, IR approved by a human, the model first cites
    tool names; the refusal lists the call ids, the resubmission is verified."""
    s = H.reg_names_then_ids_script()
    out = start(tmp_path, s, task=s["task"])
    assert out.pending.point == hitl.FORMALIZATION
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.pending.point == hitl.REPORT
    (err,) = _refusals(out.run_dir)
    assert set(err["unknown_ids"]) == {"regex_to_dfa", "dfa_vs_ir"}
    assert any(v.startswith("ev-dfa (verified, ir-") for v in err["valid_ids"]["regex_to_dfa"])
    assert "regex_to_dfa -> ev-dfa (verified, ir-" in err["error"] and "a tool NAME, not a call id" in err["error"]
    # nothing is resolved by code: the report under review is the model's resubmission
    assert out.pending.request["action_requests"][0]["args"]["evidence_ids"] == ["ev-dfa", "ev-dfa-vs-ir"]
    done = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    r = done.result
    assert done.status == "submitted" and r["trust"] == "verified" and r["unknown_ids"] == []
    assert r["formalization"]["status"] == "approved" and r["formalization"]["auto"] is False


def test_a_model_that_keeps_citing_names_ends_with_a_partial_report(tmp_path):
    settings = H.example_settings()
    settings["limits"]["max_model_calls"] = 14
    s = H.always_names_script(30)
    out = hitl.start_run(tmp_path / "loop", s["task"], settings=settings, mock_script=s, review_mode="auto")
    assert out.status == "incomplete" and out.pending is None
    assert out.result["partial_report"]["verdict"]
    assert len(_refusals(out.run_dir)) >= 2
    assert hitl.load_run(out.run_dir)["status"] == "incomplete"


def test_a_human_edit_citing_unknown_ids_is_refused_before_resuming(tmp_path):
    out = _to_report(tmp_path, H.base_script())
    with pytest.raises(hitl.DecisionError, match="ev-made-up"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"evidence_ids": ["ev-ll-table", "ev-made-up"]}))
    run = hitl.load_run(out.run_dir)
    assert run["status"] == hitl.STATUS_AWAITING and run["pending"]["point"] == hitl.REPORT


def test_gate_and_policy_still_flag_unknown_ids(tmp_path):
    """Second line of defence: a report with made-up ids (rated directly,
    since submit_report no longer accepts one) is lowered by the gate."""
    from tfl_deep_agent.evidence import policy as P
    from tfl_deep_agent.evidence.ledger import EvidenceLedger
    from tfl_deep_agent.tools.report import validate_report

    out = start(tmp_path, H.base_script(), auto_approve=True)
    good = out.result
    fab = validate_report(H.fabricated_ids_script()["models"]["main"][-1]["tool_calls"][0]["args"])
    r = P.evaluate_report(fab, EvidenceLedger.load(out.run_dir), formalization=good.get("formalization"))
    assert good["trust"] == "verified" and not good["unknown_ids"]
    assert set(r["unknown_ids"]) == {"ev-made-up-step", "ev-made-up-verdict"}
    # the step citing it is not verified, although its other id is
    assert r["steps"][0]["trust"] == "not_verified" and r["steps"][0]["unknown_ids"] == ["ev-made-up-step"]
    assert r["steps"][1]["trust"] == "verified"
    # the verdict is lowered one level and the cap with it
    assert r["trust"] == "bounded_pass" and r["confidence_cap"] < good["confidence_cap"]
    assert any("ev-made-up-verdict" in g for g in r["verdict_gate"])


# ---------------------------------------------------------------------------
# auto-approve, settings, dialog
# ---------------------------------------------------------------------------

def test_auto_approve_is_marked_in_the_report(tmp_path):
    out = start(tmp_path, H.base_script(), auto_approve=True)
    assert out.status == "submitted"
    assert out.result["human_review"] == hitl.AUTO_APPROVED_LABEL
    assert [r["decision"] for r in out.result["hitl"]["reviews"]] == ["auto_approved", "auto_approved"]
    assert all(r["source"] == "auto" for r in out.result["hitl"]["reviews"])
    for name in ("result.md", "result.html"):
        assert "Без проверки человеком" in (out.run_dir / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Review modes and approve_rest (2026-11-08)
# ---------------------------------------------------------------------------

def test_review_mode_resolution_and_points():
    assert hitl.resolve_review_mode() == "all" and hitl.resolve_review_mode(auto_approve=True) == "auto"
    assert hitl.resolve_review_mode("Formalization") == "formalization"
    with pytest.raises(hitl.RunError, match="unknown review mode"):
        hitl.resolve_review_mode("some")
    with pytest.raises(hitl.RunError, match="conflicts"):
        hitl.resolve_review_mode("all", auto_approve=True)
    assert [hitl.auto_approves("formalization", p) for p in ("formalization", "report", "execute")] == \
        [False, True, False]  # execute is never auto-approved by 'formalization'
    assert all(hitl.auto_approves("auto", p) for p in ("formalization", "report", "execute"))
    assert not any(hitl.auto_approves("all", p) for p in ("formalization", "report", "execute"))
    assert hitl.review_mode_of({"auto_approve": True}) == "auto" and hitl.review_mode_of({}) == "all"


def test_review_mode_formalization_waits_at_hitl1_only(tmp_path):
    s = H.reg_script()
    out = start(tmp_path, s, task=s["task"], review_mode="formalization")
    assert out.pending.point == hitl.FORMALIZATION
    assert hitl.load_run(out.run_dir)["review_mode"] == "formalization"
    done = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    r = done.result
    assert done.status == "submitted"
    assert [(x["point"], x["decision"], x["source"]) for x in r["hitl"]["reviews"]] == [
        ("formalization", "approved", "human"), ("report", "auto_approved", "auto")]
    assert r["human_review"] == hitl.AUTO_APPROVED_LABEL and r["hitl"]["review_mode"] == "formalization"
    # the IR was approved by a human, so regex_to_dfa(ir=) counts as verified
    assert r["formalization"]["auto"] is False and r["trust"] == "verified"


def test_auto_approved_formalization_is_not_a_human_approval(tmp_path):
    s = H.reg_script()
    out = start(tmp_path, s, task=s["task"], review_mode="auto")
    r = out.result
    assert out.status == "submitted" and r["hitl"]["review_mode"] == "auto"
    assert [x["decision"] for x in r["hitl"]["reviews"]] == ["auto_approved", "auto_approved"]
    assert r["formalization"]["status"] == "approved" and r["formalization"]["auto"] is True
    dfa = [e for e in r["evidence"] if e["id"] == "ev-dfa"]
    assert dfa and dfa[0]["trust"] == "verified"  # the tool's own label ...
    assert r["trust"] == "bounded_pass"  # ... but the gate keeps the verdict at bounded_pass
    assert any("auto" in g or "human" in g for g in r["verdict_gate"])


def test_approve_rest_approves_this_stop_and_auto_approves_the_rest(tmp_path):
    s = H.reg_script()
    out = start(tmp_path, s, task=s["task"])
    with pytest.raises(hitl.DecisionError, match="approve_rest"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("reject", comment="no"), approve_rest=True)
    assert hitl.load_run(out.run_dir)["status"] == hitl.STATUS_AWAITING
    done = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"), approve_rest=True)
    r = done.result
    assert done.status == "submitted"
    reviews = r["hitl"]["reviews"]
    assert [(x["point"], x["decision"], x["source"]) for x in reviews] == [
        ("formalization", "approved", "human"), ("report", "auto_approved", "auto")]
    assert reviews[0]["approve_rest"] is True
    run = hitl.load_run(out.run_dir)
    assert run["review_mode"] == "auto" and run["initial_review_mode"] == "all"
    assert run["approve_rest"]["point"] == "formalization"
    assert r["hitl"]["review_mode"] == "auto" and r["hitl"]["initial_review_mode"] == "all"
    # HITL 1 was a human decision: the IR binding stays human
    assert r["formalization"]["auto"] is False and r["trust"] == "verified"


def test_cli_review_mode_and_approve_rest(tmp_path, capsys):
    script = tmp_path / "reg.json"
    script.write_text(json.dumps(H.reg_script(), ensure_ascii=False), encoding="utf-8")
    run_dir = tmp_path / "cli"
    rc = cli.main([H.reg_script()["task"], "--mock-script", str(script), "--save", str(run_dir), "--no-dialog",
                   "--review-mode", "all"])
    assert rc == hitl.EXIT_AWAITING
    with pytest.raises(SystemExit):
        cli.main(["--resume", str(run_dir), "--decision", "reject", "--comment", "x", "--approve-rest"])
    with pytest.raises(SystemExit):
        cli.main(["--resume", str(run_dir), "--decision", "approve", "--review-mode", "auto"])
    with pytest.raises(SystemExit):
        cli.main(["x", "--review-mode", "all", "--auto-approve"])
    rc = cli.main(["--resume", str(run_dir), "--decision", "approve", "--approve-rest", "--no-dialog"])
    assert rc == 0
    run = hitl.load_run(run_dir)
    assert run["status"] == "submitted" and run["review_mode"] == "auto"
    assert [r["source"] for r in run["reviews"]] == ["human", "auto"]


def test_pending_cards_are_short_and_made_by_code(tmp_path):
    s = H.reg_script()
    out = start(tmp_path, s, task=s["task"])
    card = hitl.load_run(out.run_dir)["pending"]["card"]
    assert card["point"] == "formalization" and card["title"]
    assert card["summary"][:2] == ["Класс: REG", "Язык: regex (ab)*a|b над {a, b}"]
    assert card["summary"][2].startswith("в L: a, b, aba") and card["summary"][3].startswith("не в L: ε")
    assert card["editable"] == ["transcription", "ir", "class"]
    assert "Язык по IR" in card["details_md"] and "--resume" not in card["details_md"]
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    card = hitl.load_run(out.run_dir)["pending"]["card"]
    assert card["point"] == "report"
    assert card["summary"] == ["Ответ: regular", "Доверие: verified · потолок 0.98", "Шагов: 3 · утверждений: 0"]
    assert "steps" in card["editable"] and "verdict" in card["editable"]
    for c in (card, hitl.pending_card(out.pending.request)):
        assert len(c["summary"]) <= 4 and all(len(x) < 90 for x in c["summary"])
    # an execute stop: the command, the args JSON in the details
    req = {"action_requests": [{"name": "execute", "args": {"command": "python " + "x" * 200},
                                "description": "run it"}],
           "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}]}
    ex = hitl.pending_card(req)
    assert ex["point"] == "execute" and ex["editable"] == [] and len(ex["summary"][0]) < 90
    assert ex["summary"][0].startswith("Команда: python") and '"command"' in ex["details_md"]


def test_hitl_off_in_settings_runs_through(tmp_path):
    settings = H.example_settings()
    settings["hitl"] = {"formalization": False, "report": False, "execute": False}
    out = hitl.start_run(tmp_path / "off", TASK, settings=settings, mock_script=H.base_script())
    assert out.status == "submitted" and out.result["hitl"]["reviews"] == []
    assert out.result["human_review"].startswith("not reviewed")
    assert out.result["hitl"]["formalization"] == "off"


def test_hitl_needs_a_checkpointer():
    with pytest.raises(ValueError, match="checkpointer"):
        A.create_tfl_agent(H.example_settings(), model_factory=A.scripted_model_factory(H.LL_SCRIPT),
                           hitl=True)


def test_dialog_answers_both_stops_in_one_process(tmp_path):
    answers = iter(["a", "r", "Добавь разбор слов.", "", ".", "a"])
    shown: list[str] = []
    dialog = hitl.TTYDialog(input_fn=lambda _p: next(answers), print_fn=shown.append)
    out = start(tmp_path, H.reject_cycle_script(), dialog=dialog)
    assert out.status == "submitted"
    assert [r["decision"] for r in out.result["hitl"]["reviews"]] == ["approved", "rejected", "approved"]
    assert out.result["hitl"]["reviews"][1]["comment"] == "Добавь разбор слов."
    assert all(r["source"] == "dialog" for r in out.result["hitl"]["reviews"])
    assert sum("HITL 2" in s for s in shown) == 2 and any("HITL 1" in s for s in shown)


def test_dialog_later_leaves_the_run_awaiting(tmp_path):
    dialog = hitl.TTYDialog(input_fn=lambda _p: "l", print_fn=lambda _s: None)
    out = start(tmp_path, H.base_script(), dialog=dialog)
    assert out.awaiting and hitl.load_run(out.run_dir)["status"] == "awaiting_human"


def test_dialog_rejects_a_bad_edit_and_asks_again(tmp_path):
    answers = iter(["e", "", '{"task_type": "bad"}', "a", "a"])
    shown: list[str] = []
    out = start(tmp_path, H.base_script(),
                dialog=hitl.TTYDialog(input_fn=lambda _p: next(answers), print_fn=shown.append))
    assert out.status == "submitted"
    assert any(s.startswith("not accepted") for s in shown)


def test_cli_dialog_only_on_a_tty(monkeypatch):
    args = cli.build_parser().parse_args(["x"])
    assert cli._dialog(args) is None  # pytest's stdin is not a terminal
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert isinstance(cli._dialog(args), hitl.TTYDialog)
    assert cli._dialog(cli.build_parser().parse_args(["x", "--no-dialog"])) is None
    assert cli._dialog(cli.build_parser().parse_args(["x", "--auto-approve"])) is None


# ---------------------------------------------------------------------------
# Decisions and the run directory
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision, payload, comment, match", [
    ("maybe", None, None, "must be one of"),
    ("reject", None, None, "needs a comment"),
    ("reject", '{"comment": "  "}', None, "needs a comment"),
    ("edit", None, None, "needs a payload"),
    ("edit", "{not json", None, "not valid JSON"),
])
def test_parse_decision_errors(decision, payload, comment, match):
    with pytest.raises(hitl.DecisionError, match=match):
        hitl.parse_decision(decision, payload, comment=comment)


def test_parse_decision_forms():
    assert hitl.parse_decision("reject", "plain comment").comment == "plain comment"
    assert hitl.parse_decision("reject", '"json string"').comment == "json string"
    d = hitl.parse_decision("edit", '{"summary": "s", "comment": "why"}')
    assert d.args == {"summary": "s"} and d.comment == "why"


def test_resume_refuses_what_does_not_fit(tmp_path, capsys):
    out = start(tmp_path, H.base_script(), auto_approve=True)
    with pytest.raises(hitl.RunError, match="not 'awaiting_human'"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    waiting = start(tmp_path, H.base_script(), name="w")
    with pytest.raises(hitl.RunError, match="live=True does not apply"):
        hitl.resume_run(waiting.run_dir, hitl.parse_decision("approve"), live=True)
    with pytest.raises(hitl.RunError, match="already holds a run"):
        start(tmp_path, H.base_script(), name="w")
    assert cli.main(["--resume", str(tmp_path / "nowhere"), "--decision", "approve"]) == 2
    assert "not a deep-agent run directory" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--resume", str(waiting.run_dir)])  # no --decision
    with pytest.raises(SystemExit):
        cli.main(["x", "--decision", "approve"])  # --decision without --resume


def test_cli_bad_edit_keeps_the_run_waiting(tmp_path, capsys):
    out = _to_report(tmp_path, H.base_script())
    rc = cli.main(["--resume", str(out.run_dir), "--decision", "edit", "--payload", '{"steps": []}'])
    assert rc == 2 and "does not validate" in capsys.readouterr().err
    assert hitl.load_run(out.run_dir)["status"] == "awaiting_human"
    rc = cli.main(["--resume", str(out.run_dir), "--decision", "approve"])
    assert rc == 0


def test_budget_and_counters_survive_the_process_boundary(tmp_path):
    one = start(tmp_path, H.base_script(), name="one", auto_approve=True).result["budget"]
    out = start(tmp_path, H.base_script(), name="split")
    spent_at_stop = hitl.load_run(out.run_dir)["budget"]["spent_usd"]
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    split = out.result["budget"]
    assert 0 < spent_at_stop < split["spent_usd"]
    assert split["calls"] == one["calls"] and split["spent_usd"] == pytest.approx(one["spent_usd"], rel=1e-3)
    run = hitl.load_run(out.run_dir)
    assert run["limits"]["model_calls"] == split["calls"] and len(run["segments"]) == 3
    assert not (tmp_path / "runs" / B.SPEND_LOG_NAME).exists()  # mock: no money, no day log


def test_task_input_text_round_trip(tmp_path):
    t = intake_files.from_text(TASK)
    out = hitl.start_run(tmp_path / "t", t, settings=H.example_settings(), mock_script=H.base_script(),
                         auto_approve=True)
    assert out.result["task"] == TASK and "input" not in out.result



# ---------------------------------------------------------------------------
# HITL 1 binds the verdict (P3 io+hitl FIX)
# ---------------------------------------------------------------------------

def _edited_ir():
    ir = json.loads(json.dumps(H.base_script()["defs"]["ir"]))
    ir["source_text"] = H.EDITED_TRANSCRIPTION
    return ir


def test_hitl1_edited_ir_is_the_required_ir_of_the_verdict(tmp_path):
    from tfl_deep_agent.tools.ir import ir_fingerprint

    old_fp, new_fp = ir_fingerprint(H.base_script()["defs"]["ir"]), ir_fingerprint(_edited_ir())
    assert old_fp != new_fp
    out = start(tmp_path, H.base_script())
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"ir": _edited_ir()}))
    # the model skipped the re-checks: every oracle result is for the old IR
    assert out.pending.point == hitl.REPORT
    card = out.pending.card
    assert new_fp in card and "excluded" in card and "ev-ll-table" in card
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    r = out.result
    assert r["formalization"]["status"] == "approved" and r["formalization"]["ir_fingerprint"] == new_fp
    assert r["required_ir_fingerprint"] == new_fp
    assert r["trust"] == "not_verified" and r["basis"] == []
    assert "ev-ll-table" in r["excluded_ir_mismatch"]
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "Формализация (HITL 1)" in md and new_fp in md and "Исключены результаты для другого IR" in md


def test_report_fingerprint_cannot_override_the_approved_ir(tmp_path):
    from tfl_deep_agent.tools.ir import ir_fingerprint

    old_fp = ir_fingerprint(H.base_script()["defs"]["ir"])
    s = H.base_script()
    s["models"]["main"][-1] = H.report_call(s, call_id="call-report", ir_fingerprint=old_fp)
    out = start(tmp_path, s)
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("edit", {"ir": _edited_ir()}))
    assert "DIFFERENT IR" in out.pending.card
    r = hitl.resume_run(out.run_dir, hitl.parse_decision("approve")).result
    assert r["trust"] == "not_verified"
    assert any("approved at HITL 1" in g for g in r["verdict_gate"])


def test_a_report_after_a_hitl1_rejection_has_no_basis(tmp_path):
    out = start(tmp_path, H.base_script())  # the model goes on without a new formalization
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("reject", comment="IR неверен"))
    assert out.pending.point == hitl.REPORT
    assert "REJECTED" in out.pending.card
    r = hitl.resume_run(out.run_dir, hitl.parse_decision("approve")).result
    assert r["formalization"]["status"] == "rejected"
    assert r["trust"] == "not_verified" and any("rejected" in g for g in r["verdict_gate"])
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "формализация отклонена" in md


def test_a_run_that_skips_the_formalization_says_so(tmp_path):
    out = start(tmp_path, H.no_ir_builder_script())
    assert out.pending.point == hitl.REPORT  # no HITL 1 was held
    assert "NOT REVIEWED" in out.pending.card
    r = hitl.resume_run(out.run_dir, hitl.parse_decision("approve")).result
    assert r["formalization"]["status"] == "not_held"
    assert r["hitl"]["formalization"].startswith("not held")
    assert r["trust"] == "not_verified" and any("no formalization was reviewed" in g for g in r["verdict_gate"])
    md = (out.run_dir / "result.md").read_text(encoding="utf-8")
    assert "формализация не проверена человеком" in md


def test_main_agent_validate_ir_triggers_hitl1(tmp_path):
    out = start(tmp_path, H.main_validates_script())
    assert out.pending.point == hitl.FORMALIZATION
    args = out.pending.request["action_requests"][0]["args"]
    assert args["trigger"] == "validate_ir" and args["reviewed_call_id"] == "main-validate"
    assert args["ir"] == H.base_script()["defs"]["ir"] and "main agent" in args["ir_source"]
    assert "validated this IR itself" in out.pending.card
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("approve"))
    assert out.pending.point == hitl.REPORT
    r = hitl.resume_run(out.run_dir, hitl.parse_decision("approve")).result
    assert r["formalization"]["status"] == "approved" and r["trust"] == "verified"


def test_hitl1_card_takes_the_ir_of_the_reviewed_ir_builder_call_only(tmp_path):
    out = start(tmp_path, H.second_ir_builder_in_prose_script())
    assert out.pending.request["action_requests"][0]["args"]["ir"] is not None  # the first call validated
    out = hitl.resume_run(out.run_dir, hitl.parse_decision("reject", comment="Проверь k."))
    args = out.pending.request["action_requests"][0]["args"]
    # the second call made no validate_ir: no IR, not the rejected IR of the first call
    assert args["ir_builder_call_id"] == "call-ir-builder-2"
    assert args["ir"] is None and args["ir_source"] is None and "no IR" in args["ir_error"]
    assert args["valid"] is False


def test_a_live_factory_passed_as_model_factory_is_a_live_run(tmp_path):
    from tfl_deep_agent import settings as S

    scripted = A.scripted_model_factory(H.base_script())

    def factory(role_key, model_id):
        return scripted(role_key, model_id)

    factory.tfl_live = True
    out = hitl.start_run(tmp_path / "lv", TASK, settings=H.example_settings(), model_factory=factory)
    run = hitl.load_run(out.run_dir)
    assert run["mode"] == "live" and run["factory"] == "custom"
    led = hitl._budget_ledger(run, S.validate_settings(H.example_settings()), factory)
    assert led.spend_log is not None and led.per_day_usd is not None
    assert (tmp_path / "runs" / B.SPEND_LOG_NAME).exists()  # charged to the per-day log
    with pytest.raises(hitl.RunError, match="live run"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("approve"), model_factory=factory)
    with pytest.raises(hitl.RunError, match="custom model factory"):
        hitl.resume_run(out.run_dir, hitl.parse_decision("approve"), live=True)
    # a live factory never resumes a mock run
    mock = start(tmp_path, H.base_script(), name="mk")
    with pytest.raises(hitl.RunError, match="live model factory"):
        hitl.resume_run(mock.run_dir, hitl.parse_decision("approve"), model_factory=factory)


def test_fabricated_id_renders_as_not_verified_in_full_proof(tmp_path):
    from tfl_deep_agent.reports.render import render_report
    from tfl_deep_agent.tools.report import validate_report

    # the renderer is the second line of defence: render a fabricated copy of
    # an accepted report over the run's ledger (submit_report refuses one)
    out = start(tmp_path, H.base_script(), auto_approve=True)
    fab = validate_report(H.fabricated_ids_script()["models"]["main"][-1]["tool_calls"][0]["args"])
    rendered = render_report({**out.result, "report": fab}, evidence=out.run_dir, template="full_proof")
    steps = rendered.data["rendered"]["steps"]
    assert steps[0]["trust"] == "not_verified" and steps[0]["unknown_ids"] == ["ev-made-up-step"]
    assert steps[1]["trust"] == "verified"
    first = [ln for ln in rendered.markdown.splitlines() if "ev-made-up-step" in ln]
    assert first and "`not_verified`" in first[0] and "`verified`" not in first[0]
