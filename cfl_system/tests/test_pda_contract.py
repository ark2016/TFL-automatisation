"""pda_builder prompt contract vs. pda_simulator, and oracle_test merging.

Regression for: the prompt's own aⁿbⁿ PDA was rejected on ab / aabb because
the simulator wanted `accept_mode` inside the PDA and treated the last push
symbol as the stack top, while the prompt uses topmost-first push and a
sibling `acceptance_mode`; and a broken PDA used to turn real grammar
counterexamples into status=error (dropped from the report).
"""

import json
import re
from pathlib import Path

import pytest

from cfl_system.lib import cfl_oracle_test
from cfl_system.lib.cfl_oracle_test import normalize_agent_pda, oracle_test
from cfl_system.lib.pda_simulator import pda_accepts, validate_pda

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "cfl_pda_builder.md"


def _prompt_examples() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)\n```", PROMPT.read_text(encoding="utf-8"), re.S)
    out = []
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue  # schema sketches with placeholders
        if data.get("agent") == "pda_builder" and "pda" in (data.get("evidence") or {}):
            out.append(data["evidence"])
    return out


def test_prompt_has_solved_examples():
    assert len(_prompt_examples()) >= 2


@pytest.mark.parametrize("evidence", _prompt_examples(), ids=lambda e: e["pda"]["start_state"])
def test_prompt_example_pdas_accept_their_sample_runs(evidence):
    pda = normalize_agent_pda(evidence["pda"], evidence.get("acceptance_mode"))
    assert validate_pda(pda) == []
    for run in evidence.get("sample_runs", []):
        assert pda_accepts(pda, run["word"]), run["word"]


def test_anbn_example_is_exact():
    anbn = _prompt_examples()[0]
    pda = normalize_agent_pda(anbn["pda"], anbn["acceptance_mode"])
    for w in ["ab", "aabb", "aaabbb"]:
        assert pda_accepts(pda, w), w
    for w in ["", "a", "b", "aab", "abb", "ba", "abab"]:
        assert not pda_accepts(pda, w), w


def test_normalize_does_not_mutate_and_infers_mode():
    pda = {"accept_states": ["f"], "transitions": [{"push": ["A", "Z"]}]}
    out = normalize_agent_pda(pda)
    assert out["accept_mode"] == "final_state"
    assert out["transitions"][0]["push"] == ["Z", "A"]
    assert pda["transitions"][0]["push"] == ["A", "Z"]
    assert "accept_mode" not in pda


def _result(status, counterexamples=()):
    r = cfl_oracle_test._empty_result()
    r["status"] = status
    r["counterexamples"] = list(counterexamples)
    return r


@pytest.mark.parametrize("g_status, p_status, g_ces, expected", [
    ("grammar_incorrect", "error", [{"word": "ab"}], "grammar_incorrect"),
    ("pass", "error", [], "pass"),
    ("error", "pass", [], "pass"),
    ("error", "not_applicable", [], "error"),
    ("not_applicable", "not_applicable", [], "not_applicable"),
])
def test_merge_counterexamples_win_over_errors(monkeypatch, g_status, p_status, g_ces, expected):
    monkeypatch.setattr(cfl_oracle_test, "oracle_test_grammar",
                        lambda *a, **k: _result(g_status, g_ces))
    monkeypatch.setattr(cfl_oracle_test, "oracle_test_pda",
                        lambda *a, **k: _result(p_status))
    merged = oracle_test({"grammar": {}, "pda": {}}, ir={})
    assert merged["status"] == expected
    assert len(merged["counterexamples"]) == len(g_ces)
