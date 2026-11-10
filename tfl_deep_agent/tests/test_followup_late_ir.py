"""End to end (scripted models): a draft rejected at HITL 2 in one follow-up, then a new
IR stopped at HITL 1 in the next. The stopped run's result.* must not show the rejected
draft as pending, and its hitl block must not pair the late IR's review with the old
formalization."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from tfl_deep_agent import hitl  # noqa: E402

from . import hitl_scripts as H  # noqa: E402
from .test_followup import (ANSWER, APPROVE, LL_IR_B, NEW_TASK, REJECT, _env,  # noqa: E402,F401
                            add_ir_b, draft_reply, result_json, start)

DRAFT = "Черновик, который отклонят."


def test_a_late_ir_stop_after_a_rejected_draft_keeps_the_old_report_and_formalization(tmp_path):
    script = H.base_script()
    script["models"]["main"] += [draft_reply(DRAFT, "call-draft"), {"content": ANSWER}]
    add_ir_b(script, LL_IR_B, "ll")
    out = start(tmp_path, script)
    d = out.run_dir
    out = hitl.resume_run(d, APPROVE)
    out = hitl.resume_run(d, APPROVE)
    assert out.status == "submitted", out.result
    approved = result_json(d)
    old_records = (approved.get("hitl") or {}).get("formalization_records") or []
    old_fps = [r.get("ir_fingerprint") for r in old_records]
    assert old_fps

    # follow-up 1: a draft, rejected at HITL 2
    assert hitl.send_followup(d, "Перепиши").pending.point == hitl.REPORT
    out = hitl.resume_run(d, REJECT)
    assert out.status == "submitted" and out.reply == ANSWER

    # follow-up 2: a new IR, stopped at HITL 1
    out = hitl.send_followup(d, NEW_TASK)
    assert out.awaiting and out.pending.point == hitl.FORMALIZATION
    assert out.pending.request["action_requests"][0]["args"]["ir_builder_call_id"] == "call-ir-builder-b"

    run = hitl.load_run(d)
    result = result_json(d)
    # the rejected draft is not on the page, nor pending
    assert DRAFT not in json.dumps(result, ensure_ascii=False)
    assert result["report"]["summary"] == approved["report"]["summary"]
    assert result["human_review"] == "approved by a human at HITL 2"
    assert result["rendered"]["human_review"]["decision"] == "approved"
    assert result["trust"] == approved["trust"] and result["confidence_cap"] == approved["confidence_cap"]
    # the late IR (not yet decided) is not paired with the old formalization
    records = (result.get("hitl") or {}).get("formalization_records") or []
    assert [r.get("ir_fingerprint") for r in records] == old_fps
    assert all(r.get("reviewed_call_id") != "call-ir-builder-b" for r in records)
    assert result.get("formalization") == approved.get("formalization")
    assert [r["decision"] for r in run["reviews"] if r["point"] == hitl.REPORT] == ["approved", "rejected"]
