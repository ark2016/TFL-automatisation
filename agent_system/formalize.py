"""``python -m agent_system.formalize <run_dir>`` -- Lean formalization of a
finished REG run (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper: the loop, progress, cost and idempotency live in
``agent_system/lib/formalize_run.py``; this file only supplies the REG
specifics (statement renderer, gate over the assembled result, plan, renderer,
config defaults).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from . import config
from .lib import formalize_run as fr

DIRECTIONS = ("regular", "non_regular")

_PROMPT = Path(__file__).resolve().parent / "prompts" / "formalizer.md"


def _reasoning(result: dict) -> tuple[dict, dict]:
    """``(reasoning, r_ev)`` of an assembled REG result (flat or nested shape)."""
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    reasoning = evidence.get("reasoning") if isinstance(evidence.get("reasoning"), dict) else {}
    inner = reasoning.get("evidence")
    return reasoning, (inner if isinstance(inner, dict) else reasoning)


def pick_direction(result: dict, ir: dict) -> tuple[str | None, str]:
    verdict = result.get("verdict")
    if verdict in DIRECTIONS:
        return verdict, "verdict"
    reasoning, r_ev = _reasoning(result)
    for cand in (r_ev.get("verdict"), reasoning.get("verdict")):
        if cand in DIRECTIONS:
            return cand, "verdict"
    hyp = ((result.get("evidence") or {}).get("hypothesis") or {}).get("hypothesis")
    if hyp in DIRECTIONS:
        return hyp, "hypothesis"
    return None, "none"


def build_plan(result: dict, direction: str) -> Any:
    _, r_ev = _reasoning(result)
    reasoning, _ = _reasoning(result)
    return r_ev.get("consolidated_proof") or reasoning.get("consolidated_proof") or ""


def apply_gate(result: dict, block: dict) -> bool:
    """R-Lean over an assembled REG result: mirrors the ``lean_proved``
    branches of ``graph.assemble_result_node`` (a machine-checked proof takes
    priority over every other track: success, verified 0.98, the verdict flips
    to the proven direction, no contradiction left standing).  One deliberate
    difference: the verdict is set to the proven direction whenever it differs
    from the standing one, also when the standing one was cleared by an
    unresolved R3 contradiction."""
    from .graph import _set_reasoning_verdict
    from .lib.claim_verifier import CONFIDENCE_CAPS, compute_lean_proof_trust

    trust = compute_lean_proof_trust(block)
    direction = trust["direction"]
    if trust["trust"] != "verified" or direction not in DIRECTIONS:
        return False

    evidence = result.setdefault("evidence", {})
    gate = dict(evidence.get("verdict_gate") or result.get("verdict_gate") or {})
    basis = list(gate.get("basis") or [])
    downgrades = list(gate.get("downgrades") or [])
    _, r_ev = _reasoning(result)
    prior = r_ev.get("verdict", (evidence.get("reasoning") or {}).get("verdict"))
    # No reasoning agent ran (or its verdict was cleared): the standing verdict
    # is whatever the result reports (hypothesis fallback).
    standing = prior or (result.get("verdict") if result.get("verdict") in DIRECTIONS else None)
    verified = CONFIDENCE_CAPS["verified"]

    basis.append({"agent": "formalizer", "trust": "verified", "basis": "lean_proof"})
    if standing != direction:
        if prior:
            was = f"reasoning verdict '{prior}'"
        elif standing:
            was = f"the standing verdict '{standing}'"
        else:
            was = "the (inconclusive) standing verdict"
        downgrades.append(
            f"lean proof of '{direction}' overrides {was} "
            f"-> verified {verified} (VERDICT_POLICY.md R-Lean: a "
            "machine-checked proof takes priority over every other track)"
        )
        _set_reasoning_verdict(evidence, direction)
    gate.update({
        "basis": basis,
        "contradiction": False,
        "downgrades": downgrades,
        "confidence_cap": verified,
    })
    evidence["verdict_gate"] = gate
    evidence.pop("needs_human_review", None)
    result["verdict_gate"] = copy.deepcopy(gate)
    result["status"] = "success"
    result["confidence"] = verified
    return True


def _refresh(result: dict) -> None:
    from .orchestrator import _result_verdict
    result["verdict"] = _result_verdict(result)


def _render_statement(ir: dict, direction: str):
    from .lib.lean_ir import render_statement_verbose
    return render_statement_verbose(ir, direction)


def _render_file(result: dict, path: str, fmt: str) -> None:
    from .lib.renderer import render_to_file
    render_to_file(result, path, fmt=fmt)


def build_spec() -> fr.SystemSpec:
    return fr.SystemSpec(
        system="reg",
        agent="formalizer",
        retry_agent="formalizer_retry",
        prompt_path=_PROMPT,
        models=config.MODELS,
        efforts=config.EFFORT,
        temperatures={},
        lean_timeout=config.LEAN_TIMEOUT,
        directions=DIRECTIONS,
        render_statement=_render_statement,
        pick_direction=pick_direction,
        build_plan=build_plan,
        apply_gate=apply_gate,
        render_file=_render_file,
        baseline_paths=(
            "status", "confidence", "verdict", "verdict_gate",
            "evidence.verdict_gate", "evidence.reasoning", "evidence.needs_human_review",
        ),
        block_path=("evidence", "formalization"),
        available_lemmas=(),
        default_retries=config.FORMALIZE_RETRIES,
        default_max_tokens=config.FORMALIZE_MAX_TOKENS,
        refresh=_refresh,
    )


def main(argv: list[str] | None = None) -> int:
    return fr.cli_main(build_spec(), argv, prog="python -m agent_system.formalize")


if __name__ == "__main__":
    sys.exit(main())
