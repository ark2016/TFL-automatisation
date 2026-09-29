"""``python -m dcfl_system.formalize <run_dir>`` -- Lean formalization of a
finished DCFL run (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper: the loop, progress, cost and idempotency live in
``agent_system/lib/formalize_run.py``; this file only supplies the DCFL
specifics (statement renderer, ``_apply_lean_gate`` mapped onto the assembled
result, plan, renderer, config defaults).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent_system.lib import formalize_run as fr

from . import config

DIRECTIONS = ("dcfl", "non_dcfl")


def pick_direction(result: dict, ir: dict) -> tuple[str | None, str]:
    from .orchestrator import _normalize_verdict

    verdict = _normalize_verdict(result.get("verdict"))
    if verdict in DIRECTIONS:
        return verdict, "verdict"
    hyp = result.get("hypothesis")
    pred = _normalize_verdict(hyp.get("prediction")) if isinstance(hyp, dict) else None
    if pred in DIRECTIONS:
        return pred, "hypothesis"
    return None, "none"


def _reasoning_view(result: dict) -> dict:
    """The reasoning-shaped dict ``_apply_lean_gate`` works on, rebuilt from
    the assembled result."""
    return {
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "verdict_gate": result.get("verdict_gate") or {},
        "summary": result.get("reasoning_summary"),
        "primary_evidence": result.get("primary_evidence"),
    }


def build_plan(result: dict, direction: str) -> Any:
    from .orchestrator import _lean_plan

    reasoning = _reasoning_view(result)
    agents = result.get("agent_results") or result.get("specialist_outputs") or {}
    return _lean_plan({"reasoning": reasoning, "agent_results": agents}, direction)


def apply_gate(result: dict, block: dict) -> bool:
    from .orchestrator import _apply_lean_gate

    out = _apply_lean_gate(_reasoning_view(result), block)
    if block.get("status") != "proved" or out.get("verdict") is None:
        return False
    result["verdict"] = out["verdict"]
    result["confidence"] = out["confidence"]
    result["verdict_gate"] = out["verdict_gate"]
    result["primary_evidence"] = out.get("primary_evidence")
    result["reasoning_summary"] = out.get("summary")
    return True


def _render_statement(ir: dict, direction: str):
    from .lib.lean_ir import render_statement_verbose
    return render_statement_verbose(ir, direction)


def _render_file(result: dict, path: str, fmt: str) -> None:
    from .renderer import render_to_file
    render_to_file(result, path, fmt)


def build_spec() -> fr.SystemSpec:
    return fr.SystemSpec(
        system="dcfl",
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
        baseline_paths=("verdict", "confidence", "verdict_gate", "primary_evidence",
                        "reasoning_summary"),
        block_path=("formalization",),
        available_lemmas=(),
        default_retries=config.FORMALIZE_RETRIES,
        default_max_tokens=config.FORMALIZE_MAX_TOKENS,
    )


def main(argv: list[str] | None = None) -> int:
    return fr.cli_main(build_spec(), argv, prog="python -m dcfl_system.formalize")


if __name__ == "__main__":
    sys.exit(main())
