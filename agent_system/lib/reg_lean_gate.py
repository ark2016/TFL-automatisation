"""R-Lean gate for REG -- the single implementation (docs/VERDICT_POLICY.md R-Lean).

Used by both ``graph.assemble_result_node`` (formalization inside a run) and
``agent_system.formalize.apply_gate`` (the separate formalize entry over an
already assembled result), so the same situation gives the same result in
both.

A machine-checked proof (``compute_lean_proof_trust`` -> ``verified``) takes
priority over every other track: status ``success``, confidence
``CONFIDENCE_CAPS["verified"]`` (0.98), no contradiction left standing. When
the reasoning agent's verdict is set and disagrees with the proven direction,
the reasoning verdict flips to the proven direction (recorded in
``downgrades``). Without a reasoning verdict the verdict is left alone.
"""

from __future__ import annotations

from typing import Any

from .claim_verifier import CONFIDENCE_CAPS, compute_lean_proof_trust


def set_reasoning_verdict(evidence: dict, verdict: str | None) -> None:
    """Overwrite the verdict inside ``evidence['reasoning']`` (both the flat
    shape and the nested ``reasoning['evidence']`` shape reasoning prompts
    also use) so downstream readers of the top-level result
    (``orchestrator._result_verdict``, ``tfl_eval.runners.extract('reg')``)
    see the verdict gate's own decision. Mutates a copy, never the dict
    ``state["reasoning_output"]`` still holds."""
    reasoning_copy = dict(evidence.get("reasoning") or {})
    inner = reasoning_copy.get("evidence")
    if isinstance(inner, dict):
        inner = dict(inner)
        inner["verdict"] = verdict
        reasoning_copy["evidence"] = inner
    reasoning_copy["verdict"] = verdict
    evidence["reasoning"] = reasoning_copy


def lean_gate_decision(formalization: Any, reasoning_verdict: str | None) -> dict | None:
    """The R-Lean decision, or ``None`` when there is no machine-checked proof
    (any other formalization status is not evidence either way, R1).

    Returns ``{"status", "confidence", "basis", "direction", "override",
    "downgrade"}``; ``override`` is true when ``reasoning_verdict`` is set and
    differs from the proven direction (``downgrade`` is then the message)."""
    trust = compute_lean_proof_trust(formalization)
    if trust["trust"] != "verified":
        return None
    direction = trust["direction"]
    verified = CONFIDENCE_CAPS["verified"]
    override = bool(direction and reasoning_verdict and direction != reasoning_verdict)
    downgrade = None
    if override:
        downgrade = (
            f"lean proof of '{direction}' overrides reasoning verdict "
            f"'{reasoning_verdict}' -> verified 0.98 (VERDICT_POLICY.md R-Lean: a "
            "machine-checked proof takes priority over every other track)"
        )
    return {
        "status": "success",
        "confidence": verified,
        "basis": {"agent": "formalizer", "trust": "verified", "basis": "lean_proof"},
        "direction": direction,
        "override": override,
        "downgrade": downgrade,
    }
