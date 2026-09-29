"""``python -m cfl_system.formalize <run_dir>`` -- Lean formalization of a
finished CFL run (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper: the loop, progress, cost and idempotency live in
``agent_system/lib/formalize_run.py``; this file only supplies the CFL
specifics (statement renderer, ``apply_lean_gate`` over the assembled result,
plan, renderer, config defaults).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agent_system.lib import formalize_run as fr

from . import config

DIRECTIONS = ("cfl", "non_cfl")

_TRUST_RANK = {"verified": 3, "bounded_pass": 2, "well_formed": 1}
_PLAN_EVIDENCE_MAX_CHARS = 30000


def pick_direction(result: dict, ir: dict) -> tuple[str | None, str]:
    from .orchestrator import _normalize_verdict

    verdict = _normalize_verdict(result.get("verdict"))
    if verdict in DIRECTIONS:
        return verdict, "verdict"
    hint = result.get("classifier_hint")
    hinted = _normalize_verdict(hint.get("verdict")) if isinstance(hint, dict) else None
    if hinted in DIRECTIONS:
        return hinted, "hypothesis"
    return None, "none"


def build_plan(result: dict, direction: str) -> Any:
    """The pipeline's own ``_lean_plan`` fed from the stored result: the
    strongest specialist that argued *direction* is the method."""
    from .orchestrator import _lean_plan, _normalize_verdict

    outs = result.get("specialist_outputs") or {}
    primary, best = None, -1
    for name, out in outs.items():
        if not isinstance(out, dict) or out.get("status") != "success":
            continue
        if _normalize_verdict(out.get("verdict")) != direction:
            continue
        rank = _TRUST_RANK.get(out.get("trust"), 0)
        if rank > best:
            primary, best = name, rank
    proof = result.get("proof")
    summary = proof.get("summary") if isinstance(proof, dict) else (proof if isinstance(proof, str) else None)
    plan = _lean_plan(
        {"reasoning_output": {"primary_evidence": primary, "summary": summary},
         "agent_results": outs},
        direction,
    )
    ev = plan.get("specialist_evidence")
    if ev is not None:
        text = json.dumps(ev, ensure_ascii=False)
        if len(text) > _PLAN_EVIDENCE_MAX_CHARS:
            plan["specialist_evidence"] = text[:_PLAN_EVIDENCE_MAX_CHARS] + " ...[truncated]"
    return plan


def apply_gate(result: dict, block: dict) -> bool:
    from .orchestrator import _normalize_verdict, apply_lean_gate

    lean = apply_lean_gate(
        block, _normalize_verdict(result.get("verdict")), result.get("confidence"),
        result.get("verdict_gate") or {},
    )
    if not lean["proved"]:
        return False
    result["verdict"] = lean["verdict"]
    result["confidence"] = lean["confidence"]
    result["verdict_gate"] = lean["verdict_gate"]
    result["proof_verified"] = bool(lean["verdict_gate"].get("proof_verified", False))
    if lean["flipped"]:
        # the specialists' informal proof argued the OTHER side: the Lean proof
        # becomes the proof of record (same as assemble_result_node)
        old = result.get("proof")
        result["proof"] = {
            "source": "lean_formalizer",
            "note": (
                "Machine-checked Lean 4 proof (docs/VERDICT_POLICY.md R-Lean); "
                "it replaced the specialists' argument for the opposite verdict."
            ),
            "evidence": {
                "statement": block.get("statement"),
                "proof_body": block.get("proof_body"),
            },
            "summary": old.get("summary") if isinstance(old, dict) else None,
        }
    return True


def _render_statement(ir: dict, direction: str):
    from .lib.lean_ir import render_statement_verbose
    return render_statement_verbose(ir, direction)


def _render_file(result: dict, path: str, fmt: str) -> None:
    from .lib.cfl_renderer import render_to_file
    render_to_file(result, path, fmt=fmt)


def build_spec() -> fr.SystemSpec:
    from .lib.agent_output_schema import schema_for
    from .orchestrator import _LEAN_AVAILABLE_LEMMAS

    return fr.SystemSpec(
        system="cfl",
        agent="lean_formalizer",
        retry_agent="lean_formalizer_retry",
        prompt_path=Path(__file__).resolve().parent / "prompts" / config.PROMPT_FILES["lean_formalizer"],
        models=config.MODELS,
        efforts=config.EFFORT,
        temperatures=config.TEMPERATURES,
        lean_timeout=config.LEAN_TIMEOUT,
        directions=DIRECTIONS,
        render_statement=_render_statement,
        pick_direction=pick_direction,
        build_plan=build_plan,
        apply_gate=apply_gate,
        render_file=_render_file,
        baseline_paths=("verdict", "confidence", "verdict_gate", "proof", "proof_verified"),
        block_path=("formalization",),
        available_lemmas=tuple(_LEAN_AVAILABLE_LEMMAS),
        output_schema=schema_for("lean_formalizer") or fr.proof_body_schema(),
        default_retries=config.FORMALIZE_RETRIES,
        default_max_tokens=config.FORMALIZE_MAX_TOKENS,
    )


def main(argv: list[str] | None = None) -> int:
    return fr.cli_main(build_spec(), argv, prog="python -m cfl_system.formalize")


if __name__ == "__main__":
    sys.exit(main())
