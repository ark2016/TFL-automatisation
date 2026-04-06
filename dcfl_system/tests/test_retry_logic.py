"""
Tests for DCFL retry logic per S7.2.

Covers retry planning: which agents get retried, hint generation,
max_retries enforcement, and decrementing counters.
"""
from __future__ import annotations

import pytest

from dcfl_system.lib.retry_logic import build_retry_plan, MAX_RETRIES


# ---------------------------------------------------------------------------
# All agents success -> no retry
# ---------------------------------------------------------------------------

def test_all_success_no_retry():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "closure_reduction": {"status": "success", "verdict": "dcfl", "confidence": 0.85},
        "dcfl_pumping": {"status": "success", "verdict": "dcfl", "confidence": 0.8},
        "shallit": {"status": "success", "verdict": "dcfl", "confidence": 0.75},
        "inh_ambiguity": {"status": "success", "verdict": "non_dcfl", "confidence": 0.7},
    }
    plan = build_retry_plan(agent_results)
    assert plan["needs_retry"] is False
    assert plan["agents_to_retry"] == []


# ---------------------------------------------------------------------------
# Some agents fail with low confidence -> retry those
# ---------------------------------------------------------------------------

def test_low_confidence_fail_retried():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
        "dcfl_pumping": {"status": "fail", "verdict": None, "confidence": 0.5},
        "shallit": {"status": "success", "verdict": "dcfl", "confidence": 0.8},
        "inh_ambiguity": {"status": "success", "verdict": "non_dcfl", "confidence": 0.7},
    }
    plan = build_retry_plan(agent_results)
    assert plan["needs_retry"] is True
    assert "closure_reduction" in plan["agents_to_retry"]
    assert "dcfl_pumping" in plan["agents_to_retry"]
    # Successful agents should NOT be retried
    assert "stack_strategy" not in plan["agents_to_retry"]
    assert "shallit" not in plan["agents_to_retry"]


# ---------------------------------------------------------------------------
# Not_applicable agents -> not retried
# ---------------------------------------------------------------------------

def test_not_applicable_not_retried():
    agent_results = {
        "stack_strategy": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results)
    assert "stack_strategy" not in plan["agents_to_retry"]
    assert "closure_reduction" in plan["agents_to_retry"]


# ---------------------------------------------------------------------------
# High-confidence fail -> not retried
# ---------------------------------------------------------------------------

def test_high_confidence_fail_not_retried():
    agent_results = {
        "stack_strategy": {"status": "fail", "verdict": None, "confidence": 0.85},
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results)
    assert "stack_strategy" not in plan["agents_to_retry"]
    assert "closure_reduction" in plan["agents_to_retry"]


# ---------------------------------------------------------------------------
# Success agents -> not retried
# ---------------------------------------------------------------------------

def test_success_not_retried():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
    }
    plan = build_retry_plan(agent_results)
    assert "stack_strategy" not in plan["agents_to_retry"]
    assert plan["needs_retry"] is False


# ---------------------------------------------------------------------------
# retry_count >= MAX_RETRIES -> no retry
# ---------------------------------------------------------------------------

def test_max_retries_reached_no_retry():
    agent_results = {
        "stack_strategy": {"status": "fail", "verdict": None, "confidence": 0.3},
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.2},
    }
    plan = build_retry_plan(agent_results, retry_count=MAX_RETRIES)
    assert plan["needs_retry"] is False
    assert plan["agents_to_retry"] == []
    assert plan["max_retries_remaining"] == 0


def test_max_retries_exceeded_no_retry():
    agent_results = {
        "stack_strategy": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results, retry_count=MAX_RETRIES + 1)
    assert plan["needs_retry"] is False
    assert plan["agents_to_retry"] == []


# ---------------------------------------------------------------------------
# Hints are generated for retried agents
# ---------------------------------------------------------------------------

def test_hints_generated_for_fail():
    agent_results = {
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results)
    assert "closure_reduction" in plan["hints"]
    assert isinstance(plan["hints"]["closure_reduction"], str)
    assert len(plan["hints"]["closure_reduction"]) > 0


def test_hints_generated_for_errors():
    agent_results = {
        "shallit": {
            "status": "fail",
            "verdict": None,
            "confidence": 0.2,
            "errors": ["timeout", "parse error"],
        },
    }
    plan = build_retry_plan(agent_results)
    assert "shallit" in plan["hints"]
    hint = plan["hints"]["shallit"]
    assert "timeout" in hint or "error" in hint.lower()


def test_hints_generated_for_uncertain():
    agent_results = {
        "dcfl_pumping": {
            "status": "uncertain",
            "verdict": None,
            "confidence": 0.4,
        },
    }
    plan = build_retry_plan(agent_results)
    assert "dcfl_pumping" in plan["hints"]
    assert "uncertain" in plan["hints"]["dcfl_pumping"].lower()


# ---------------------------------------------------------------------------
# max_retries_remaining decrements correctly
# ---------------------------------------------------------------------------

def test_max_retries_remaining_first_retry():
    agent_results = {
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results, retry_count=0)
    assert plan["needs_retry"] is True
    # After first retry attempt: MAX_RETRIES - 0 - 1 = 1
    assert plan["max_retries_remaining"] == MAX_RETRIES - 1


def test_max_retries_remaining_second_retry():
    agent_results = {
        "closure_reduction": {"status": "fail", "verdict": None, "confidence": 0.3},
    }
    plan = build_retry_plan(agent_results, retry_count=1)
    assert plan["needs_retry"] is True
    # After second retry attempt: MAX_RETRIES - 1 - 1 = 0
    assert plan["max_retries_remaining"] == MAX_RETRIES - 2


def test_max_retries_remaining_no_retry_needed():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
    }
    plan = build_retry_plan(agent_results, retry_count=0)
    assert plan["needs_retry"] is False
    # No retry needed: MAX_RETRIES - 0 = 2
    assert plan["max_retries_remaining"] == MAX_RETRIES


# ---------------------------------------------------------------------------
# Empty agent_results
# ---------------------------------------------------------------------------

def test_empty_agent_results_no_retry():
    plan = build_retry_plan({})
    assert plan["needs_retry"] is False
    assert plan["agents_to_retry"] == []
