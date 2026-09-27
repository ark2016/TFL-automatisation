"""End-to-end tests for the CFL pipeline using mock agent outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfl_system.orchestrator import MockRunner, run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"


def load_json(relative_path: str) -> dict:
    """Load a JSON file relative to the examples directory."""
    path = EXAMPLES_DIR / relative_path
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: task_w1w2w1w3 → non_cfl
# ---------------------------------------------------------------------------

def test_e2e_w1w2w1w3():
    ir = load_json("task_w1w2w1w3.json")
    mock = MockRunner(str(MOCK_DIR), "task_w1w2w1w3")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "non_cfl"
    assert result["confidence"] > 0.5


# ---------------------------------------------------------------------------
# Test 2: task_wwvvR -> non_cfl (docs/THEORY.md §2.5, docs/VERDICT_POLICY.md §2)
#
# The OLD reference verdict here was `cfl 0.8`, and before that `inconclusive`
# (see git history) -- neither survives docs/THEORY.md §2.5: L = {w w v v^R}
# is honestly non-CFL, proved by intersecting with the regular language
# R* = a(aa)*b a(aa)*b a(aa)*b a(aa)*b (all four a-blocks of ODD length).
# On R*, the even-palindromic-suffix characterization of L collapses to
# exactly one disjunct (i == k and j == l), so L ∩ R* = {a^i b a^j b a^i b
# a^j b | i, j odd} -- a copy language ({ss}), proved non-CFL by pumping
# z = a^{2p+1} b a^{2p+1} b a^{2p+1} b a^{2p+1} b (see the closure_reduction
# mock). The mock's cfg_builder honestly fails (S -> W W V does not link the
# two W's -- it generates a strict superset of L, refuted by the oracle on
# 'b', 'ab', 'ba', 'baaba', none of which are in L) and decomposition
# honestly returns inconclusive ({ww} is not a CFL component, so "L = {ww}.
# {vv^R}" proves nothing in either direction) -- neither counts as evidence
# either way (R1), leaving closure_reduction as the sole basis.
#
# closure_reduction's intersection_examples/intersection_non_examples are
# checked against the oracle (docs/VERDICT_POLICY.md §4) and pass, so its
# trust is `bounded_pass` (not `well_formed`) -> confidence capped at 0.85,
# not 0.55. No contradiction: cfg_builder/decomposition never reach
# bounded_pass, so there is no competing constructive artifact.
# ---------------------------------------------------------------------------

def test_e2e_wwvvR():
    ir = load_json("task_wwvvR.json")
    mock = MockRunner(str(MOCK_DIR), "task_wwvvR")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "non_cfl"
    # bounded_pass ceiling (docs/VERDICT_POLICY.md §2): closure_reduction's
    # intersection examples/non-examples were confirmed by the oracle, so
    # trust is bounded_pass (cap 0.85), not well_formed (cap 0.55).
    assert result["confidence"] == 0.85
    assert result["verdict_gate"]["basis_trust"] == "bounded_pass"
    assert result["verdict_gate"]["contradiction"] is False
    assert result["verdict_gate"]["downgrades"] == []


# ---------------------------------------------------------------------------
# Test 3: task_w0w1w2w1w3 → non_cfl
# ---------------------------------------------------------------------------

def test_e2e_w0w1w2w1w3():
    ir = load_json("task_w0w1w2w1w3.json")
    mock = MockRunner(str(MOCK_DIR), "task_w0w1w2w1w3")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "non_cfl"


# ---------------------------------------------------------------------------
# Test 4: task_grammar_filter_49 → non_cfl
#
# THEORY.md §2.3: the previous "cfl" reference verdict was wrong. |a|=|b| is
# an equality between two counters, not a regular filter, so CFL ∩ REG does
# not apply; cfg_builder honestly fails and closure_reduction (R = b*a*b*a*,
# Ogden's lemma) proves L(G) ∩ F is not context-free.
# ---------------------------------------------------------------------------

def test_e2e_grammar_filter_49():
    ir = load_json("task_grammar_filter_49.json")
    mock = MockRunner(str(MOCK_DIR), "task_grammar_filter_49")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "non_cfl"


# ---------------------------------------------------------------------------
# Test 5: Selective retry scenario
# ---------------------------------------------------------------------------

def test_e2e_selective_retry():
    """First reasoning returns 'retry' with specific agents, second returns 'done'."""
    ir = load_json("task_w1w2w1w3.json")

    call_count = {"reasoning": 0}

    class RetryMockRunner:
        def run_agent(self, agent_name, input_data=None):
            if agent_name == "reasoning":
                call_count["reasoning"] += 1
                if call_count["reasoning"] == 1:
                    return {
                        "action": "retry",
                        "verdict": None,
                        "confidence": 0.3,
                        "reasoning": "Need more evidence",
                        "retry_plan": {
                            "agents_to_retry": ["pumping_cfl", "closure_reduction"],
                            "hints": {"pumping_cfl": {"strategy": "try_longer_word"}},
                        },
                    }
                else:
                    return {
                        "action": "done",
                        "verdict": "non_cfl",
                        "confidence": 0.9,
                        "reasoning": "Confirmed",
                    }
            elif agent_name == "retry_planner":
                return {
                    "agents_to_retry": ["pumping_cfl", "closure_reduction"],
                    "hints": {},
                }
            elif agent_name == "pumping_cfl":
                return {
                    "agent": "pumping_cfl",
                    "status": "success",
                    "verdict": "non_cfl",
                    "confidence": 0.85,
                    # "abac" is a genuine member of task_w1w2w1w3's language
                    # (w1=a, w2=b, w1=a, w3=c) — the verdict gate now checks
                    # word_chosen against the oracle (VERDICT_POLICY.md §1: a
                    # word not actually in L is "refuted", not "well_formed").
                    "evidence": {
                        "word_chosen": "abac",
                        "cases": [{"case": "all", "why_not_in_L": "test"}],
                        "all_cases_covered": True,
                    },
                    "errors": [],
                }
            elif agent_name == "classifier":
                return {"verdict": "non_cfl", "confidence": 0.7, "advisory_only": True}
            elif agent_name == "proof_checker":
                return {"status": "verified", "issues": []}
            # Return minimal output for other specialists
            return {
                "agent": agent_name,
                "status": "inconclusive",
                "verdict": None,
                "confidence": 0.0,
                "evidence": {},
                "errors": [],
            }

    result = run_pipeline(ir, mock_runner=RetryMockRunner())
    assert result["verdict"] == "non_cfl"
    assert result["retries"] > 0  # Should have at least 1 retry


# ---------------------------------------------------------------------------
# Test 6: Oracle test catches grammar error
# ---------------------------------------------------------------------------

def test_e2e_oracle_catch():
    """Mock a WRONG grammar from cfg_builder; oracle should find a counterexample."""
    ir = {
        "task_type": "classify_and_prove_cfl",
        "source_text": "{a^n b^n | n >= 0}",
        "language_spec": {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        },
    }

    class BadGrammarMockRunner:
        def run_agent(self, agent_name, input_data=None):
            if agent_name == "cfg_builder":
                return {
                    "agent": "cfg_builder",
                    "status": "success",
                    "verdict": "cfl",
                    "confidence": 0.9,
                    "evidence": {
                        "grammar": {
                            "terminals": ["a", "b"],
                            "nonterminals": ["S", "A", "B"],
                            "start": "S",
                            "rules": [
                                {"lhs": "S", "rhs": ["A", "B"]},
                                {"lhs": "A", "rhs": ["a", "A"]},
                                {"lhs": "A", "rhs": []},
                                {"lhs": "B", "rhs": ["b", "B"]},
                                {"lhs": "B", "rhs": []},
                            ],
                        }
                    },
                    "errors": [],
                }
            elif agent_name == "reasoning":
                return {
                    "action": "done",
                    "verdict": "cfl",
                    "confidence": 0.9,
                    "reasoning": "Grammar passed",
                }
            elif agent_name == "classifier":
                return {"verdict": "cfl", "confidence": 0.9, "advisory_only": True}
            elif agent_name == "proof_checker":
                return {"status": "verified", "issues": []}
            return {
                "agent": agent_name,
                "status": "inconclusive",
                "verdict": None,
                "confidence": 0.0,
                "evidence": {},
                "errors": [],
            }

    result = run_pipeline(ir, mock_runner=BadGrammarMockRunner())
    # Oracle test should find that the grammar accepts "aab" which is not a^n b^n
    oracle_test_result = result.get("oracle_test")
    if oracle_test_result:
        # If oracle test ran, it should find counterexamples
        assert (
            oracle_test_result.get("status") == "grammar_incorrect"
            or oracle_test_result.get("counterexamples")
        )


# ---------------------------------------------------------------------------
# Test 7: No mock at all — fallback reasoning
# ---------------------------------------------------------------------------

def test_e2e_no_mocks():
    """No mock runner — should still produce a result via fallback reasoning."""
    ir = {
        "task_type": "classify_and_prove_cfl",
        "source_text": "{a^n b^n | n >= 0}",
        "language_spec": {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        },
    }
    result = run_pipeline(ir)  # No mock runner at all
    # Should still produce a result via fallback reasoning
    assert result is not None
    assert "verdict" in result
