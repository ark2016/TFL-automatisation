"""End to end, mock mode, across processes: ``python -m tfl_deep_agent``
started, stopped at HITL 1 and HITL 2, and resumed by NEW processes with
``--resume RUN_DIR --decision ...`` (a reject -> fix -> approve cycle at the
report). Nothing reaches the API: the replies come from a mock script, the
subprocesses get no ``ANTHROPIC_API_KEY``, and mock runs never build a
``ChatAnthropic``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from tfl_deep_agent import hitl  # noqa: E402

from . import hitl_scripts as H  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    return H.subprocess_env(tmp_path / "runs")


def test_reject_fix_approve_across_four_processes(tmp_path, env):
    script = H.write_script(tmp_path, H.reject_cycle_script())
    run_dir = tmp_path / "run"

    # process 1: start; stops at HITL 1 (no TTY, so no dialog)
    p = H.tfl_deep("--mock-script", str(script), "--save", str(run_dir), "--settings", str(H.SETTINGS_EXAMPLE),
                   env=env)
    assert p.returncode == hitl.EXIT_AWAITING, p.stderr
    assert "awaiting_human: formalization" in p.stdout and "--resume" in p.stderr
    run = hitl.load_run(run_dir)
    assert run["status"] == "awaiting_human" and run["pending"]["point"] == "formalization"
    assert (run_dir / hitl.CHECKPOINT_FILE).is_file() and (run_dir / "evidence.jsonl").is_file()

    # process 2: approve the formalization; stops at HITL 2 with the draft
    p = H.tfl_deep("--resume", str(run_dir), "--decision", "approve", env=env)
    assert p.returncode == hitl.EXIT_AWAITING, p.stderr
    assert "awaiting_human: report" in p.stdout
    draft = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert draft["status"] == "awaiting_human" and len(draft["report"]["steps"]) == 2

    # process 3: return the report with a comment; the agent resubmits and stops again
    p = H.tfl_deep("--resume", str(run_dir), "--decision", "reject", "--comment", H.REJECT_COMMENT, env=env)
    assert p.returncode == hitl.EXIT_AWAITING, p.stderr
    run = hitl.load_run(run_dir)
    assert run["pending"]["point"] == "report"
    assert len(run["pending"]["request"]["action_requests"][0]["args"]["steps"]) == 3
    assert run["reviews"][-1]["decision"] == "rejected" and run["reviews"][-1]["comment"] == H.REJECT_COMMENT

    # process 4: approve; the report is accepted
    p = H.tfl_deep("--resume", str(run_dir), "--decision", "approve", env=env)
    assert p.returncode == 0, p.stderr
    assert "verdict: LL(1)" in p.stdout and "approved by a human" in p.stdout
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "submitted" and data["trust"] == "verified"
    assert data["human_review"] == "approved by a human at HITL 2"
    assert data["hitl"]["report_rejections"] == 1
    assert len(data["steps"]) == 3 and data["mode"] == "mock"
    assert "no money spent" in data["budget"]["note"]
    # the comment reached the model as the submit_report error, in the checkpointed thread
    rejected = [m for m in H.thread_messages(run_dir)
                if getattr(m, "name", None) == "submit_report" and m.status == "error"]
    assert rejected and H.REJECT_COMMENT in str(rejected[0].content)
    run = hitl.load_run(run_dir)
    assert run["status"] == "submitted" and len(run["segments"]) == 4 and run["pending"] is None
    md = (run_dir / "result.md").read_text(encoding="utf-8")
    assert "Проверено человеком" in md and "ev-ll-table" in md

    # the finished run cannot be resumed again
    p = H.tfl_deep("--resume", str(run_dir), "--decision", "approve", env=env)
    assert p.returncode == 2 and "nothing to resume" in p.stderr


def test_auto_approve_single_process_marks_the_report(tmp_path, env):
    run_dir = tmp_path / "auto"
    p = H.tfl_deep("--mock-script", str(H.LL_SCRIPT), "--save", str(run_dir), "--settings",
                   str(H.SETTINGS_EXAMPLE), "--auto-approve", env=env)
    assert p.returncode == 0, p.stderr
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert data["human_review"].startswith("auto-approved")
    assert "Без проверки человеком" in (run_dir / "result.md").read_text(encoding="utf-8")


def test_default_run_dir_is_under_the_runs_dir(tmp_path, env):
    p = H.tfl_deep("--mock-script", str(H.LL_SCRIPT), "--settings", str(H.SETTINGS_EXAMPLE), env=env)
    assert p.returncode == hitl.EXIT_AWAITING, p.stderr
    runs = [d for d in (tmp_path / "runs").iterdir() if (d / hitl.RUN_FILE).is_file()]
    assert len(runs) == 1 and runs[0].name.startswith("deep-")
    assert hitl.load_run(runs[0])["status"] == "awaiting_human"
