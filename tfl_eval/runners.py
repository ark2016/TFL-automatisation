"""Per-system dispatch: validate an IR, run its pipeline, extract the verdict.

Keeps `tfl_eval.cli` free of the four pipelines' differing contracts
(`agent_system` nests the verdict under `evidence.reasoning`; `cfl_system`,
`dcfl_system` and `ll_system` return it at the top level).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEMS = ("reg", "cfl", "dcfl", "ll")

# Directory each system's MockRunner loads `{task_name}_{agent}[.json|_output.json]`
# files from (see each orchestrator's `MockRunner`).
MOCK_DIRS: dict[str, Path] = {
    "reg": REPO_ROOT / "agent_system" / "examples",
    "cfl": REPO_ROOT / "cfl_system" / "examples" / "mock",
    "dcfl": REPO_ROOT / "dcfl_system" / "examples" / "mock",
    "ll": REPO_ROOT / "ll_system" / "examples" / "mock",
}

# Env var each system's LiveRunner (or llm_client) reads to override the
# configured model — see root CLAUDE.md ("Models and LLM calls").
_MODEL_OVERRIDE_ENV = "TFL_MODEL_OVERRIDE"
_DEFAULT_LIVE_MODEL = "claude-haiku-4-5"


def validate(system: str, ir: dict) -> list[str]:
    """Validate *ir* with the schema validator for *system*. Returns errors."""
    if system == "reg":
        from agent_system.lib.ir_schema import validate_ir
        return validate_ir(ir)
    if system == "cfl":
        from cfl_system.lib.cfl_ir_schema import validate_cfl_ir
        return validate_cfl_ir(ir)
    if system == "dcfl":
        from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir
        return validate_dcfl_ir(ir)
    if system == "ll":
        from ll_system.lib.ll_ir_schema import validate_ll_ir
        return validate_ll_ir(ir)
    raise ValueError(f"Unknown system: {system!r}")


def make_mock_runner(system: str, task_name: str) -> Any:
    """Build the mock runner for *system*, pointed at *task_name*'s files."""
    mock_dir = MOCK_DIRS[system]
    if system == "reg":
        from agent_system.orchestrator import MockRunner
    elif system == "cfl":
        from cfl_system.orchestrator import MockRunner
    elif system == "dcfl":
        from dcfl_system.orchestrator import MockRunner
    elif system == "ll":
        from ll_system.orchestrator import MockRunner
    else:
        raise ValueError(f"Unknown system: {system!r}")
    return MockRunner(str(mock_dir), task_name)


def has_mocks(system: str, task_name: str) -> bool:
    """True if at least one `{task_name}_*` mock file exists for *system*."""
    mock_dir = MOCK_DIRS[system]
    if not mock_dir.exists():
        return False
    return any(mock_dir.glob(f"{task_name}_*"))


def make_live_runner(system: str) -> Any:
    """Build the LLM-backed runner for *system* (real Anthropic API calls)."""
    if system == "reg":
        from agent_system.lib.llm_client import LLMRunner
        return LLMRunner()
    if system == "cfl":
        from cfl_system.orchestrator import LiveRunner
        return LiveRunner()
    if system == "dcfl":
        # dcfl_system.orchestrator.LiveRunner has no default constructor args
        # beyond what LLMRunner-style runners share; import lazily like the rest.
        from dcfl_system.orchestrator import LiveRunner
        return LiveRunner()
    if system == "ll":
        from ll_system.orchestrator import LiveRunner
        return LiveRunner()
    raise ValueError(f"Unknown system: {system!r}")


def run_pipeline(system: str, ir: dict, mock_runner: Any = None, agent_runner: Any = None) -> dict:
    """Run *system*'s pipeline on *ir* and return its raw result dict."""
    if system == "reg":
        from agent_system.orchestrator import Pipeline
        return Pipeline().run_full_pipeline(ir, mock_runner=mock_runner, agent_runner=agent_runner)
    if system == "cfl":
        from cfl_system.orchestrator import run_pipeline as _run
        return _run(ir, mock_runner=mock_runner, agent_runner=agent_runner)
    if system == "dcfl":
        from dcfl_system.orchestrator import run_pipeline as _run
        return _run(ir, mock_runner=mock_runner, agent_runner=agent_runner)
    if system == "ll":
        from ll_system.orchestrator import run_pipeline as _run
        return _run(ir, mock_runner=mock_runner, agent_runner=agent_runner)
    raise ValueError(f"Unknown system: {system!r}")


def _first_dict_verdict(node: Any) -> str | None:
    """Depth-first search for the first string `verdict` key in a nested dict."""
    if isinstance(node, dict):
        v = node.get("verdict")
        if isinstance(v, str):
            return v
        for value in node.values():
            found = _first_dict_verdict(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _first_dict_verdict(item)
            if found is not None:
                return found
    return None


def extract(system: str, result: dict) -> dict:
    """Normalize *result* to `{verdict, confidence, k, verdict_gate, basis_trust}`.

    `agent_system` nests its verdict/confidence under `evidence.reasoning`
    (or uses the explicit gated verdict before falling back to a hypothesis); the other three
    pipelines already expose `verdict`/`confidence` at the top level.
    """
    if system == "reg":
        evidence = result.get("evidence", {}) or {}
        gate = evidence.get("verdict_gate") or result.get("verdict_gate") or {}
        # docs/VERDICT_POLICY.md R3/R1 (reviewer finding): an unresolved
        # contradiction (status=="partial", verdict_gate.contradiction=True)
        # must not surface the reasoning agent's own proposed regular/
        # non_regular verdict as if it were confirmed — see the matching
        # guard in agent_system.orchestrator._result_verdict.
        if result.get("status") == "partial" and gate.get("contradiction"):
            verdict = None
        else:
            reasoning = evidence.get("reasoning", {}) or {}
            r_ev = reasoning.get("evidence", reasoning) if isinstance(reasoning, dict) else {}
            verdict = (
                r_ev.get("verdict")
                or reasoning.get("verdict")
                or result.get("verdict")
                or (evidence.get("hypothesis") or {}).get("hypothesis")
            )
        confidence = result.get("confidence")
        k = None
    else:
        verdict = result.get("verdict")
        if verdict is None:
            verdict = _first_dict_verdict(result)
        confidence = result.get("confidence")
        gate = result.get("verdict_gate") or {}
        k = result.get("k")

    basis = gate.get("basis") or []
    basis_trust = [b.get("trust") for b in basis if isinstance(b, dict)]

    return {
        "verdict": verdict,
        "confidence": confidence,
        "k": k,
        "verdict_gate": gate,
        "basis_trust": basis_trust,
    }


def set_live_model_env(explicit_model: str | None, env: dict[str, str]) -> None:
    """Apply the eval-set's default-Haiku policy for `--live` runs.

    `--model` wins outright; otherwise an already-set `TFL_MODEL_OVERRIDE` is
    left alone (an operator's own override), and only if neither is present
    do we default to Haiku (root CLAUDE.md: "Live test runs use Haiku only").
    """
    if explicit_model:
        env[_MODEL_OVERRIDE_ENV] = explicit_model
    elif _MODEL_OVERRIDE_ENV not in env:
        env[_MODEL_OVERRIDE_ENV] = _DEFAULT_LIVE_MODEL
