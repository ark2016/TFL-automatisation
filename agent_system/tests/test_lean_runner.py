"""Lean runner seam and Docker start failures (no Docker, no API: the
subprocess and the formalizer are scripted).

A ``docker run`` that fails to start (exit 125/126/127 or a daemon message)
is retried; if it still fails the result is the terminal ``unavailable`` --
never ``error``, which would buy another paid formalizer attempt.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from agent_system import formalize as reg_formalize
from agent_system.lib import formalize_run as fr
from agent_system.lib import type_check as tcmod
from agent_system.lib.testing.fake_anthropic import FakeAnthropic
from agent_system.tests.test_formalize_run import OPUS, body_turn, load_result, make_run_dir

PROOF = "theorem tfl_main : True := by trivial\n\n#print axioms tfl_main\n"
START_ERR = "docker: Error response from daemon: OCI runtime create failed: runc did not terminate"


def cp(rc, stdout="", stderr=""):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


class Scripted:
    """Stands in for ``subprocess.run``; replays *results* in order (the last
    one repeats)."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        r = self.results[min(len(self.calls), len(self.results)) - 1]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(tcmod.RUNNER_ENV, raising=False)
    monkeypatch.delenv(tcmod.PROJECT_DIR_ENV, raising=False)
    monkeypatch.setattr(tcmod, "START_RETRY_SLEEP_S", 0.0)
    monkeypatch.setattr(tcmod, "is_docker_available", lambda: True)


def install(monkeypatch, *results):
    fake = Scripted(*results)
    monkeypatch.setattr(tcmod.subprocess, "run", fake)
    return fake


def test_start_failure_is_retried_then_lean_output_is_used(monkeypatch):
    err = '{"severity":"error","data":"boom","pos":{"line":1,"column":0}}\n'
    fake = install(monkeypatch, cp(125, stderr=START_ERR), cp(1, err))
    res = tcmod.check_lean_file(PROOF)
    assert len(fake.calls) == 2
    assert res["status"] == "error" and res["errors"][0]["data"] == "boom"


def test_three_start_failures_are_unavailable_with_docker_stderr(monkeypatch):
    fake = install(monkeypatch, cp(125, stderr=START_ERR))
    res = tcmod.check_lean_file(PROOF)
    assert len(fake.calls) == tcmod.START_ATTEMPTS == 3
    assert res["status"] == "unavailable"
    assert "OCI runtime create failed" in res["message"]


def test_daemon_message_with_other_exit_code_is_a_start_failure(monkeypatch):
    fake = install(monkeypatch, cp(1, "", START_ERR))
    assert tcmod.check_lean_file(PROOF)["status"] == "unavailable"
    assert len(fake.calls) == 3


def test_lean_error_exit_is_not_retried(monkeypatch):
    fake = install(monkeypatch, cp(1, "", "some stderr"))
    res = tcmod.check_lean_file(PROOF)
    assert len(fake.calls) == 1 and res["status"] == "error"


def test_legacy_check_lean_start_failure_is_skipped_not_invalid(monkeypatch):
    fake = install(monkeypatch, cp(125, stderr=START_ERR))
    res = tcmod.check_lean("def x := 1")
    assert len(fake.calls) == 3
    assert res["status"] == "skipped" and "OCI runtime" in res["message"]


def test_legacy_check_lean_retries_then_reports_valid(monkeypatch):
    install(monkeypatch, cp(126, stderr="cannot exec"), cp(0, ""))
    assert tcmod.check_lean("def x := 1")["status"] == "valid"


def test_unavailable_stops_the_formalizer_loop(tmp_path, monkeypatch):
    """Graph-level (run_formalization, real check_lean_file): Docker fails to
    start on every attempt -> exactly one formalizer call, status unavailable."""
    monkeypatch.setattr(fr, "is_docker_available", lambda: True)
    fake = install(monkeypatch, cp(125, stderr=START_ERR))
    run_dir = make_run_dir(tmp_path)
    client = FakeAnthropic([body_turn("a"), body_turn("b", model=OPUS), body_turn("c", model=OPUS)])
    summary = fr.run_formalization(run_dir, reg_formalize.build_spec(), client=client)
    assert summary["status"] == "unavailable"
    assert len(client.stream_calls) == 1
    assert len(fake.calls) == 3
    assert load_result(run_dir)["evidence"]["formalization"]["status"] == "unavailable"


# --- local runner -----------------------------------------------------------

def test_local_runner_runs_lake_in_the_project_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(tcmod.RUNNER_ENV, "local")
    monkeypatch.setenv(tcmod.PROJECT_DIR_ENV, str(tmp_path))
    fake = install(monkeypatch, cp(1, "", "boom"))
    res = tcmod.check_lean_file(PROOF, timeout=77)
    cmd, kw = fake.calls[0]
    assert cmd[:4] == ["lake", "env", "lean", "--json"]
    assert kw["cwd"] == str(tmp_path) and kw["timeout"] == 77
    assert len(fake.calls) == 1 and res["status"] == "error"


def test_local_runner_timeout_status(monkeypatch):
    monkeypatch.setenv(tcmod.RUNNER_ENV, "local")
    install(monkeypatch, subprocess.TimeoutExpired("lake", 5))
    assert tcmod.check_lean_file(PROOF, timeout=5)["status"] == "timeout"


def test_runner_defaults_to_docker_and_project_dir_default():
    assert tcmod.lean_runner() == "docker"
    assert tcmod.local_project_dir() == tcmod.DOCKER_DIR / "tfl_lean"


def test_local_availability_needs_lake_and_built_project(monkeypatch, tmp_path):
    monkeypatch.setenv(tcmod.RUNNER_ENV, "local")
    monkeypatch.setenv(tcmod.PROJECT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(tcmod.shutil, "which", lambda n: "/bin/lake")
    assert tcmod._local_lean_available() is False
    (tmp_path / "lakefile.toml").write_text("", encoding="utf-8")
    assert tcmod._local_lean_available() is False
    (tmp_path / ".lake").mkdir()
    assert tcmod._local_lean_available() is True
    monkeypatch.setattr(tcmod.shutil, "which", lambda n: None)
    assert tcmod._local_lean_available() is False


# --- shared detector --------------------------------------------------------

def test_deep_agent_uses_the_shared_detector():
    from tfl_deep_agent.tools import lean

    assert lean._is_daemon_failure is tcmod.is_daemon_failure
    assert lean._DAEMON_ERROR_RE is tcmod.DAEMON_ERROR_RE
    res = {"status": "error", "errors": [{"severity": "error", "data": START_ERR}]}
    assert tcmod.is_daemon_failure(res)
    assert not tcmod.is_daemon_failure({**res, "status": "proved"})
    assert not tcmod.is_daemon_failure(
        {"status": "error", "errors": [{"data": START_ERR, "pos": {"line": 1}}]})


# --- CI workflow ------------------------------------------------------------

def test_lean_workflow_uses_the_local_runner_and_does_not_skip():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/lean.yml").read_text(encoding="utf-8")
    assert "TFL_LEAN_RUNNER: local" in text and "workflow_dispatch" in text and "schedule" in text
    assert "lake exe cache get" in text and "lake build" in text and "actions/cache" in text
    for f in ("test_phase3.py", "test_lean_examples.py", "test_lean_llk_doc.py", "test_lean_ir.py"):
        assert f in text, f
    assert "docker/build-push-action" not in text
    assert "is_docker_available()" in text and "skipped" in text
