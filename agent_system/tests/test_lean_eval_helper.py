"""``lean_eval.run_lean``: a container that fails to start is a Docker fault,
not an empty Lean result (no Docker needed: ``_run_lean_once`` is patched)."""

from __future__ import annotations

import pytest

from agent_system.tests import lean_eval

_WARN = '{"severity": "warning", "data": "declaration uses \'sorry\'", "pos": {"line": 1, "column": 0}}\n'


def _fake(results):
    calls = []

    def once(text, timeout):
        calls.append(text)
        return results[len(calls) - 1]

    return once, calls


def test_start_failure_is_retried_then_lean_output_is_parsed(monkeypatch):
    once, calls = _fake([(125, "", "runc did not terminate successfully: exit status 139"), (0, _WARN, "")])
    monkeypatch.setattr(lean_eval, "_run_lean_once", once)
    msgs = lean_eval.run_lean("theorem t : True := sorry")
    assert len(calls) == 2
    assert [m["severity"] for m in msgs] == ["warning"]


def test_repeated_start_failure_raises_instead_of_returning_no_messages(monkeypatch):
    once, calls = _fake([(125, "", "OCI runtime start failed")] * 3)
    monkeypatch.setattr(lean_eval, "_run_lean_once", once)
    with pytest.raises(RuntimeError, match="did not start after 3 attempts.*OCI runtime start failed"):
        lean_eval.run_lean("theorem t : True := sorry")
    assert len(calls) == 3


def test_lean_exit_code_is_not_a_start_failure(monkeypatch):
    # lean exits 1 when the file has errors; that is a Lean result, parsed once.
    err = '{"severity": "error", "data": "unknown identifier", "pos": {"line": 1, "column": 0}}\n'
    once, calls = _fake([(1, err, "")])
    monkeypatch.setattr(lean_eval, "_run_lean_once", once)
    msgs = lean_eval.run_lean("theorem t : True := x")
    assert len(calls) == 1
    assert msgs and msgs[0]["severity"] == "error"
