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
    """R-Lean over an assembled REG result. The decision itself
    (``lib/reg_lean_gate.lean_gate_decision``) is the very one
    ``graph.assemble_result_node`` uses, so a proof gives the same status,
    confidence, gate and verdict here as inside a run: success, verified 0.98,
    no contradiction standing; the reasoning verdict flips to the proven
    direction only when it is set and disagrees (recorded in ``downgrades``).
    A result whose reasoning agent escalated is left alone, as in the graph.

    The result is already assembled, so two things the graph never sees are
    restored: a reasoning verdict cleared by an unresolved R3 contradiction
    (which the graph would not have applied next to a proof) gets the proven
    direction back, and that stale contradiction downgrade is dropped."""
    from .lib.reg_lean_gate import lean_gate_decision, set_reasoning_verdict

    evidence = result.setdefault("evidence", {})
    reasoning, r_ev = _reasoning(result)
    action = r_ev.get("action", reasoning.get("action", ""))
    if action == "escalate":
        return False
    prior = r_ev.get("verdict", reasoning.get("verdict"))
    decision = lean_gate_decision(block, prior)
    if decision is None or decision["direction"] not in DIRECTIONS:
        return False
    direction = decision["direction"]

    gate = dict(evidence.get("verdict_gate") or result.get("verdict_gate") or {})
    basis = list(gate.get("basis") or [])
    downgrades = list(gate.get("downgrades") or [])
    cleared = bool(gate.get("contradiction")) and not prior
    if cleared:
        downgrades = [d for d in downgrades if not str(d).startswith("contradiction:")]

    # A forced re-run over a block gated inside a pipeline run meets its own
    # earlier entries: never list them twice.
    basis = [b for b in basis if b != decision["basis"]] + [decision["basis"]]
    if decision["override"] and decision["downgrade"] not in downgrades:
        downgrades.append(decision["downgrade"])
        set_reasoning_verdict(evidence, direction)
    elif cleared:
        set_reasoning_verdict(evidence, direction)
    gate.update({
        "basis": basis,
        "contradiction": False,
        "downgrades": downgrades,
        "confidence_cap": decision["confidence"],
    })
    evidence["verdict_gate"] = gate
    evidence.pop("needs_human_review", None)
    result["verdict_gate"] = copy.deepcopy(gate)
    result["status"] = decision["status"]
    result["confidence"] = decision["confidence"]
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
