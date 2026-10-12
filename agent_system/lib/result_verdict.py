"""Read the REG gate's final decision without reviving a rejected proposal."""

from __future__ import annotations


def result_verdict(result: dict) -> str | None:
    evidence = result.get("evidence") or {}
    gate = result.get("verdict_gate") or evidence.get("verdict_gate") or {}
    if result.get("status") in ("partial", "failure") or gate.get("contradiction"):
        return None

    # An explicit null is a decision, not a missing legacy field.
    if "verdict" in result:
        verdict = result["verdict"]
    else:
        reasoning = evidence.get("reasoning") or {}
        inner = reasoning.get("evidence") or reasoning
        verdict = (inner.get("verdict") or reasoning.get("verdict")
                   or (evidence.get("hypothesis") or {}).get("hypothesis"))
    return verdict if verdict in ("regular", "non_regular") else None
