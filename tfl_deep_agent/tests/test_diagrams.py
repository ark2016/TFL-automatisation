"""State diagrams in the report (``reports/diagrams.py``, ``render._ledger_objects``)."""

from __future__ import annotations

import pytest

from tfl_deep_agent.reports import diagrams as D
from tfl_deep_agent.reports import render as R

DFA = {"states": ["q0", "q1", "q2", "q3", "q4"], "alphabet": ["a", "b"], "start": "q0", "accept": ["q1", "q2"],
       "transitions": {"q0": {"a": "q1", "b": "q2"}, "q1": {"a": "q3", "b": "q4"}, "q2": {"a": "q3", "b": "q3"},
                       "q3": {"a": "q3", "b": "q3"}, "q4": {"a": "q1", "b": "q3"}}}

needs_dot = pytest.mark.skipif(D.dot_binary() is None, reason="Graphviz dot is not installed")


def test_dfa_dot_marks_start_accepting_trap_and_merges_parallel_edges() -> None:
    dot = D.dfa_to_dot(DFA)
    assert dot is not None
    assert '__start -> "q0";' in dot
    assert '"q1" [shape=doublecircle];' in dot and '"q2" [shape=doublecircle];' in dot
    assert '"q3" [style=dashed];' in dot                       # the trap
    assert '"q2" -> "q3" [label="a, b"];' in dot               # parallel edges merged
    assert '"q3" -> "q3" [label="a, b"];' in dot


def test_dfa_dot_escapes_names_and_refuses_big_automata() -> None:
    evil = {"states": ['p"];x[label=<IMG SRC="/etc/passwd"/>'], "alphabet": ["a"], "start": "p",
            "accept": [], "transitions": {'p"];x[label=<IMG SRC="/etc/passwd"/>': {"a": "p"}}}
    dot = D.dfa_to_dot(evil)
    assert dot is not None and '\\"];x[label=<IMG SRC=\\"/etc/passwd\\"/>' in dot
    big = {"states": [f"s{i}" for i in range(D.MAX_DIAGRAM_STATES + 1)], "alphabet": ["a"], "start": "s0",
           "transitions": {f"s{i}": {"a": "s0"} for i in range(D.MAX_DIAGRAM_STATES + 1)}}
    assert D.dfa_to_dot(big) is None
    assert D.dfa_to_dot({"states": ["q"], "transitions": {}}) is None


def test_dpda_dot_labels_read_top_push() -> None:
    dpda = {"states": ["p", "f"], "start": "p", "accept_states": ["f"], "stack_alphabet": ["Z", "A"],
            "initial_stack": ["Z"],
            "transitions": [{"from": "p", "read": "a", "top": "Z", "to": "p", "push": ["A", "Z"]},
                            {"from": "p", "read": "a", "top": "A", "to": "p", "push": ["A", "A"]},
                            {"from": "p", "read": None, "top": "Z", "to": "f", "push": []}]}
    dot = D.dpda_to_dot(dpda)
    assert dot is not None
    assert '"p" -> "p" [label="a, Z / AZ\\na, A / AA"];' in dot
    assert '"p" -> "f" [label="ε, Z / ε"];' in dot
    assert '"f" [shape=doublecircle];' in dot


@needs_dot
def test_svg_is_themed_and_inert() -> None:
    svg = D.dot_to_svg(D.dfa_to_dot(DFA) or "")
    assert svg is not None and svg.startswith("<svg")
    assert 'stroke="black"' not in svg and "currentColor" in svg
    assert "<script" not in svg.lower() and "<?xml" not in svg


def test_svg_without_dot_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(D, "_dot_binary", None)
    assert D.dot_to_svg("digraph { a -> b }") is None


def test_only_code_made_dot_fences_become_pictures() -> None:
    dot = "digraph X {\n  a -> b;\n}"
    md = f"```dot\n{dot}\n```"
    assert '<figure class="diagram"><svg>pic</svg></figure>' in R.markdown_to_html(md, diagrams={dot: "<svg>pic</svg>"})
    assert "<figure" not in R.markdown_to_html(md)                               # a model's dot fence stays code
    assert "<figure" not in R.markdown_to_html(md, diagrams={dot: ""})           # dot unavailable: code block
    assert "<pre><code" in R.markdown_to_html(md, diagrams={"digraph Y {}": "<svg/>"})


def test_report_draws_the_cited_dfa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(D, "dot_to_svg", lambda dot: '<svg data-test="dfa"></svg>')
    ev = [{"id": "toolu_dfa", "tool": "regex_to_dfa", "ok": True, "trust": "verified", "has_tool_result": True,
           "args": {"regex": "(ab)*a|b"}, "data": {"dfa": DFA, "cls": "reg"}}]
    result = {"task": "L = (ab)*a | b", "report": {
        "verdict": "REG", "answer": "yes", "summary": "L регулярен.", "method": "ДКА по выражению",
        "construction": "Минимальный ДКА.", "evidence_ids": ["toolu_dfa"],
        "steps": [{"title": "ДКА", "text": "Построен ДКА.", "evidence_ids": ["toolu_dfa"]}]}}
    rep = R.render_report(result, evidence=ev, template="full_proof", status="submitted")
    assert "**Диаграмма состояний ДКА:**" in rep.markdown and "```dot\ndigraph DFA {" in rep.markdown
    assert '<figure class="diagram"><svg data-test="dfa"></svg></figure>' in rep.html
