"""``lean_eval.run_lean`` reuses the shared runner (``type_check.run_lean_process``):
a container that fails to start is a Docker fault, not an empty Lean result
(no Docker needed: ``subprocess.run`` is scripted)."""

from __future__ import annotations

import pytest

from agent_system.lib import type_check
from agent_system.tests import lean_eval
from agent_system.tests.test_lean_runner import Scripted, cp

_WARN = '{"severity": "warning", "data": "declaration uses \'sorry\'", "pos": {"line": 1, "column": 0}}\n'


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.delenv(type_check.RUNNER_ENV, raising=False)
    monkeypatch.setattr(type_check, "START_RETRY_SLEEP_S", 0.0)


def _install(monkeypatch, *results):
    fake = Scripted(*results)
    monkeypatch.setattr(type_check.subprocess, "run", fake)
    return fake


def test_start_failure_is_retried_then_lean_output_is_parsed(monkeypatch):
    fake = _install(monkeypatch, cp(125, "", "runc did not terminate successfully: exit status 139"), cp(0, _WARN))
    msgs = lean_eval.run_lean("theorem t : True := sorry")
    assert len(fake.calls) == 2
    assert [m["severity"] for m in msgs] == ["warning"]


def test_repeated_start_failure_raises_instead_of_returning_no_messages(monkeypatch):
    fake = _install(monkeypatch, cp(125, "", "OCI runtime start failed"))
    with pytest.raises(RuntimeError, match="did not start after 3 attempts.*OCI runtime start failed"):
        lean_eval.run_lean("theorem t : True := sorry")
    assert len(fake.calls) == 3


def test_lean_exit_code_is_not_a_start_failure(monkeypatch):
    # lean exits 1 when the file has errors; that is a Lean result, parsed once.
    err = '{"severity": "error", "data": "unknown identifier", "pos": {"line": 1, "column": 0}}\n'
    fake = _install(monkeypatch, cp(1, err))
    msgs = lean_eval.run_lean("theorem t : True := x")
    assert len(fake.calls) == 1
    assert msgs and msgs[0]["severity"] == "error"


def test_local_runner_is_followed(monkeypatch, tmp_path):
    monkeypatch.setenv(type_check.RUNNER_ENV, "local")
    monkeypatch.setenv(type_check.PROJECT_DIR_ENV, str(tmp_path))
    fake = _install(monkeypatch, cp(0, _WARN))
    assert lean_eval.run_lean("x")[0]["severity"] == "warning"
    assert fake.calls[0][0][0] == "lake" and fake.calls[0][1]["cwd"] == str(tmp_path)
