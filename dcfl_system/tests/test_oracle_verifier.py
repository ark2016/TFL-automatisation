"""
Tests for the DCFL oracle verifier stub.

Covers verify_agent_results with various agent statuses,
invalid outputs, empty inputs, and mixed scenarios.
"""
from __future__ import annotations

import pytest

from dcfl_system.lib.oracle_verifier import verify_agent_results


# ---------------------------------------------------------------------------
# Success agents -> not_verified
# ---------------------------------------------------------------------------

def test_success_agents_not_verified():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "shallit": {"status": "success", "verdict": "dcfl", "confidence": 0.8},
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "not_verified"


# ---------------------------------------------------------------------------
# Not_applicable agents -> not_applicable
# ---------------------------------------------------------------------------

def test_not_applicable_agents():
    agent_results = {
        "dcfl_pumping": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
        "inh_ambiguity": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Invalid output -> error
# ---------------------------------------------------------------------------

def test_invalid_output_error():
    agent_results = {
        "stack_strategy": "not a dict",
        "shallit": 42,
        "closure_reduction": None,
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "error"
        issues = result[name].get("issues", [])
        assert any("invalid output" in str(i).lower() for i in issues)


# ---------------------------------------------------------------------------
# Empty agent_results -> empty result
# ---------------------------------------------------------------------------

def test_empty_agent_results():
    result = verify_agent_results({}, {})
    assert result == {}


# ---------------------------------------------------------------------------
# Mixed statuses
# ---------------------------------------------------------------------------

def test_mixed_statuses():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "dcfl_pumping": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
        "inh_ambiguity": "broken",
    }
    result = verify_agent_results(agent_results, {})
    assert result["stack_strategy"]["verification_status"] == "not_verified"
    assert result["dcfl_pumping"]["verification_status"] == "not_applicable"
    assert result["inh_ambiguity"]["verification_status"] == "error"


# ---------------------------------------------------------------------------
# Verification result structure
# ---------------------------------------------------------------------------

def test_verification_result_structure():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
    }
    result = verify_agent_results(agent_results, {})
    entry = result["stack_strategy"]
    assert "verification_status" in entry
    assert "checks_run" in entry
    assert "checks_passed" in entry
    assert "checks_total" in entry
    assert isinstance(entry["checks_run"], list)
    assert entry["checks_passed"] == 0
    assert entry["checks_total"] == 0


# ---------------------------------------------------------------------------
# closure_reduction direction vocabulary matches closure_table.py (root
# TODO.md §2: "both" vs "constructive"/"destructive" словари направлений)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction", ["constructive", "destructive", "both"])
def test_closure_reduction_direction_accepts_closure_table_vocabulary(direction):
    """closure_table.py's own proof_direction for the symmetric 'complement'
    operation is 'both' (see get_proof_direction('complement')); the oracle's
    direction_valid check must accept the same value, not just the two the
    reasoning agent ultimately commits to."""
    agent_results = {
        "closure_reduction": {
            "status": "success",
            "verdict": "dcfl",
            "confidence": 0.6,
            "proof_sketch": {
                "operation": "complement",
                "source_language": "a*b*",
                "transformation": "L = complement(a*b*)",
                "direction": direction,
            },
        },
    }
    result = verify_agent_results(agent_results, {})
    entry = result["closure_reduction"]
    assert "direction_valid" in entry["checks_run"]
    assert entry["checks_passed"] == entry["checks_total"], entry.get("issues")


def test_closure_reduction_direction_still_rejects_garbage():
    agent_results = {
        "closure_reduction": {
            "status": "success",
            "verdict": "dcfl",
            "confidence": 0.6,
            "proof_sketch": {
                "operation": "complement",
                "source_language": "a*b*",
                "transformation": "L = complement(a*b*)",
                "direction": "sideways",
            },
        },
    }
    result = verify_agent_results(agent_results, {})
    entry = result["closure_reduction"]
    assert entry["checks_passed"] < entry["checks_total"]
    assert any("direction" in issue for issue in entry.get("issues", []))
