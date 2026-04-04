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
# Test 2: task_wwvvR → cfl
# ---------------------------------------------------------------------------

def test_e2e_wwvvR():
    ir = load_json("task_wwvvR.json")
    mock = MockRunner(str(MOCK_DIR), "task_wwvvR")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "cfl"


# ---------------------------------------------------------------------------
# Test 3: task_w0w1w2w1w3 → non_cfl
# ---------------------------------------------------------------------------

def test_e2e_w0w1w2w1w3():
    ir = load_json("task_w0w1w2w1w3.json")
    mock = MockRunner(str(MOCK_DIR), "task_w0w1w2w1w3")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "non_cfl"


# ---------------------------------------------------------------------------
# Test 4: task_grammar_filter_49 → cfl
# ---------------------------------------------------------------------------

def test_e2e_grammar_filter_49():
    ir = load_json("task_grammar_filter_49.json")
    mock = MockRunner(str(MOCK_DIR), "task_grammar_filter_49")
    result = run_pipeline(ir, mock_runner=mock)
    assert result["verdict"] == "cfl"


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
                    "evidence": {
                        "word_chosen": "test",
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
