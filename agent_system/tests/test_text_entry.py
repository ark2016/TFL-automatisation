"""`--text` entry: input_parser builds the IR (scripted runner, no API)."""
import sys

import pytest

from agent_system.orchestrator import main, parse_text_to_ir

_IR = {
    "task_type": "classify",
    "source_text": "L = {a^n b^n}",
    "language_spec": {
        "kind": "predicate", "alphabet": ["a", "b"], "variable": "w",
        "predicate": {"op": "eq",
                      "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                      "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"}},
    },
}


class _Runner:
    def __init__(self, out):
        self.out, self.calls = out, []

    def run_agent(self, name, input_data=None):
        self.calls.append((name, input_data))
        return self.out


def test_wrapped_ir_is_unwrapped_and_validated():
    r = _Runner({"module": "input_parser", "status": "success", "evidence": _IR})
    ir = parse_text_to_ir("L = {a^n b^n}", r)
    assert ir["task_type"] == "classify"
    assert r.calls == [("input_parser", {"source_text": "L = {a^n b^n}"})]


def test_missing_source_text_is_filled_from_input():
    ir = {k: v for k, v in _IR.items() if k != "source_text"}
    out = parse_text_to_ir("my text", _Runner({"evidence": ir}))
    assert out["source_text"] == "my text"


def test_agent_error_raises():
    with pytest.raises(RuntimeError, match="parsing failed"):
        parse_text_to_ir("x", _Runner({"status": "agent_error", "errors": ["boom"]}))


def test_invalid_ir_raises():
    bad = dict(_IR, task_type="nonsense")
    with pytest.raises(RuntimeError, match="invalid IR"):
        parse_text_to_ir("x", _Runner({"evidence": bad}))


def test_cli_text_requires_live(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_system", "--text", "L = ..."])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 1
    assert "--text requires --live" in capsys.readouterr().err


def test_cli_needs_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_system"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 1
