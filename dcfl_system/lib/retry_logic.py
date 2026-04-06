"""
Retry logic for DCFL agent system.

Implements retry_planner logic per §7.2:
- max_retries = 2 (3 total attempts: initial + 2 retry)
- Don't retry agents with status 'not_applicable'
- Don't retry agents with confidence > 0.8 and status 'fail'
- Generate specific hints for retried agents
"""
from __future__ import annotations
from typing import Any

DCFL_SPECIALIST_NAMES = (
    "stack_strategy", "closure_reduction", "dcfl_pumping", "shallit", "inh_ambiguity",
)

MAX_RETRIES = 2

def build_retry_plan(
    agent_results: dict[str, Any],
    oracle_verification: dict[str, Any] | None = None,
    retry_count: int = 0,
) -> dict:
    """Build a RetryPlan based on agent results.

    Returns:
    {
        "needs_retry": bool,
        "agents_to_retry": list[str],
        "hints": dict[str, str],   # agent_name -> hint string
        "max_retries_remaining": int,
    }
    """
    if retry_count >= MAX_RETRIES:
        return {
            "needs_retry": False,
            "agents_to_retry": [],
            "hints": {},
            "max_retries_remaining": 0,
        }

    agents_to_retry = []
    hints = {}

    for name in DCFL_SPECIALIST_NAMES:
        output = agent_results.get(name)
        if not isinstance(output, dict):
            continue

        status = output.get("status", "unknown")
        confidence = float(output.get("confidence", 0.0))

        # Rule: Don't retry not_applicable agents
        if status == "not_applicable":
            continue

        # Rule: Don't retry high-confidence failures (method genuinely doesn't apply)
        if status == "fail" and confidence > 0.8:
            continue

        # Rule: Don't retry successful agents
        if status == "success":
            continue

        # This agent should be retried
        agents_to_retry.append(name)

        # Generate specific hints based on what went wrong
        errors = output.get("errors", [])
        if errors:
            hints[name] = f"Previous attempt had errors: {'; '.join(str(e) for e in errors[:3])}. Try a different approach."
        elif status == "fail":
            hints[name] = "Previous attempt failed. Try different word families or a different decomposition strategy."
        elif status == "uncertain":
            hints[name] = "Previous attempt was uncertain. Try to strengthen the argument with more concrete examples."

    needs_retry = len(agents_to_retry) > 0

    return {
        "needs_retry": needs_retry,
        "agents_to_retry": agents_to_retry,
        "hints": hints,
        "max_retries_remaining": MAX_RETRIES - retry_count - 1 if needs_retry else MAX_RETRIES - retry_count,
    }
