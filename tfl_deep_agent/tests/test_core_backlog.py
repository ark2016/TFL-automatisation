"""Backlog fixes in the core: the task a report names, the Lean prompt path."""

from __future__ import annotations

import pytest

pytest.importorskip("deepagents")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from tfl_deep_agent import hitl  # noqa: E402
from tfl_deep_agent.hitl import FORMALIZATION  # noqa: E402


def test_card_task_is_the_original_task_and_the_follow_up_message_is_kept_apart():
    a = HumanMessage(content="Task A")
    note = HumanMessage(content="HITL 1 ...", additional_kwargs={"tfl_hitl": FORMALIZATION})
    b = HumanMessage(content="Task B")
    target = AIMessage(content="ir B")
    msgs = [a, AIMessage(content="ir A"), note, b, target]
    assert hitl._first_human_text(msgs) == "Task A"
    assert hitl._followup_text(msgs, 4) == "Task B"
    assert hitl._followup_text(msgs, 1) is None  # the run's own first message is not a follow-up
    assert hitl._followup_text(msgs[:3], 3) is None  # harness notes are not tasks


def test_report_names_the_task_of_the_latest_reviewed_ir():
    run = {"task_text": "Task A", "reviews": [
        {"point": FORMALIZATION, "reviewed_call_id": "c1", "task_text": "Task A"},
        {"point": FORMALIZATION, "reviewed_call_id": "c2", "task_text": "Task B"}]}
    assert hitl._task_text(run, {"messages": []}) == "Task B"
    assert hitl._task_text(run, {"messages": []}, later_calls={"c2"}) == "Task A"
    edited = {"task_text": "Task A", "reviews": [
        {"point": FORMALIZATION, "reviewed_call_id": "c1", "transcription": "Fixed A", "task_text": "Task A"}]}
    assert hitl._task_text(edited, {"messages": []}) == "Fixed A"


def test_plain_approval_after_an_edited_transcription_keeps_the_correction():
    run = {"task_text": "orig", "reviews": [
        {"point": FORMALIZATION, "reviewed_call_id": "c1", "transcription": "Fixed"},
        {"point": FORMALIZATION, "reviewed_call_id": "c2", "task_text": "orig"}]}
    state = {"messages": [HumanMessage(content="orig")]}
    assert hitl._task_text(run, state) == "Fixed"
    # a new task stated later still wins
    run["reviews"][1]["task_text"] = "new task"
    assert hitl._task_text(run, state) == "new task"


def test_pdf_task_plain_approval_reports_the_transcription_not_the_harness_instruction(tmp_path, capsys):
    from tfl_deep_agent import cli
    from tfl_deep_agent.tests import hitl_scripts as H

    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(H.make_pdf())
    script = H.write_script(tmp_path, H.pdf_script())
    d = tmp_path / "run"
    assert cli.main(["--file", str(pdf), "--mock-script", str(script), "--save", str(d),
                     "--settings", str(H.SETTINGS_EXAMPLE), "--no-dialog"]) == hitl.EXIT_AWAITING
    assert cli.main(["--resume", str(d), "--decision", "approve"]) == hitl.EXIT_AWAITING
    assert cli.main(["--resume", str(d), "--decision", "approve"]) == 0, capsys.readouterr().err
    import json
    assert json.loads((d / "result.json").read_text(encoding="utf-8"))["task"] == H.INTAKE_TRANSCRIPTION


def test_lean_prompt_does_not_send_the_model_to_a_missing_skill_file():
    from pathlib import Path

    text = (Path(hitl.__file__).parent / "prompts" / "lean.md").read_text(encoding="utf-8")
    assert "exists, read it" not in text and "/skills/lean/SKILL.md" not in text
    main = (Path(hitl.__file__).parent / "prompts" / "main.md").read_text(encoding="utf-8")
    assert "`lean`," not in main and "no Lean skill file" in main


def test_gate_predicates_live_in_rules_and_ledger_reexports_them():
    import ast
    from pathlib import Path

    from tfl_deep_agent.evidence import ledger, rules

    for name in ("canonical_json", "entry_fingerprint", "ir_admissible", "ir_fingerprint", "lean_proof_side"):
        assert getattr(ledger, name) is getattr(rules, name)
    tree = ast.parse(Path(rules.__file__).read_text(encoding="utf-8"))
    imported = {n.module if isinstance(n, ast.ImportFrom) else a.name
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert not any(m and (m.startswith(".") or "tfl_deep_agent" in m) for m in imported)


def test_as_accepted_does_not_cut_at_a_hitl_note_in_the_reports_own_segment():
    from langchain_core.messages import ToolMessage

    report = ToolMessage(content="ok", name="submit_report", tool_call_id="r1", artifact={"ok": True, "report": {}})
    note = HumanMessage(content="HITL 1 ...", additional_kwargs={"tfl_hitl": FORMALIZATION})
    state = {"messages": [HumanMessage(content="A"), report, note]}
    assert hitl._as_accepted(state) == (state, frozenset())
    follow = HumanMessage(content="B?")
    later = ToolMessage(content="x", name="validate_ir", tool_call_id="v2")
    cut, ids = hitl._as_accepted({"messages": [*state["messages"], follow, later]})
    assert ids == {"v2"} and len(cut["messages"]) == 3


def test_a_stop_after_a_rejected_draft_rewrites_the_pending_result(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hitl, "final_result", lambda *a, **k: {"status": "submitted"})
    monkeypatch.setattr(hitl, "_write_result", lambda d, run, result, budget, evidence: calls.append(result))

    class G:
        def get_state(self, config):
            class S:
                values = {"messages": []}
            return S()

    run = {"draft": {"verdict": "regular"}}
    hitl._drop_stale_draft(tmp_path, run, G(), {}, None, None)
    assert "draft" not in run and calls == [{"status": "submitted"}]
