"""Per-system dispatch: validate an IR, run its pipeline, extract the verdict.

Keeps `tfl_eval.cli` free of the four pipelines' differing contracts
(`agent_system` nests the verdict under `evidence.reasoning`; `cfl_system`,
`dcfl_system` and `ll_system` return it at the top level).

System "deep" (`tfl_deep_agent`, opt-in) has its own entry points at the end
of the module: `deep_task_text`, `deep_mock_script`, `run_deep`, `extract_deep`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEMS = ("reg", "cfl", "dcfl", "ll")

# `tfl_deep_agent` (one agent for all four classes): opt-in via `--systems deep`,
# never part of the default `--systems` (= SYSTEMS), so a plain `tfl-eval` run
# behaves exactly as before.
DEEP_SYSTEM = "deep"
ALL_SYSTEMS = SYSTEMS + (DEEP_SYSTEM,)

# Mock replays for the deep agent, one per manifest entry: `<entry id>.json`
# (`tfl_deep_agent.agent.load_mock_script` format, see tfl_deep_agent/DEV_NOTES.md).
DEEP_MOCK_DIR = REPO_ROOT / "tfl_deep_agent" / "examples" / "mock" / "eval"

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
_DEFAULT_LIVE_MODEL = "claude-haiku-5-5"


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
        from agent_system.lib.result_verdict import result_verdict

        evidence = result.get("evidence", {}) or {}
        gate = evidence.get("verdict_gate") or result.get("verdict_gate") or {}
        verdict = result_verdict(result)
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


# ---------------------------------------------------------------------------
# system "deep": tfl_deep_agent
# ---------------------------------------------------------------------------
#
# The deep agent gets only the task's `source_text` plus one question line for
# the entry's class (no IR: it formalizes the task itself), runs with every HITL stop auto-approved, and writes
# `result.{json,md,html}` into its run directory. The verdict is the policy
# token the agent's code-side verdict gate published (`published_verdict`,
# else `verdict_token`): the same vocabulary as the four pipelines
# (regular / non_regular / partial, cfl / non_cfl / null, dcfl / non_dcfl /
# null, ll / not_ll / uncertain), never the model's free-text verdict.

class DeepAdapterError(RuntimeError):
    """The deep agent could not be run for an eval entry (bad input, a run
    that stopped for a human although auto-approve was on, ...)."""


def deep_mock_script(entry_id: str) -> Path | None:
    """The deep agent's mock replay for manifest entry *entry_id*, if any."""
    path = DEEP_MOCK_DIR / f"{entry_id}.json"
    return path if path.is_file() else None


# One explicit question per manifest class, appended to the deep agent's input
# (user decision 2026-11-08): many `source_text`s name no class, and the same
# language is asked under several classes ({a^n b^n} is reg-01, dcfl-01 and
# ll-01). The LL question asks for the smallest k, which is what the manifest's
# `expected.k` holds; a grammar task (`ll_check_grammar`, Format 3) asks about
# the given grammar, every other LL task about the language.
DEEP_CLASS_QUESTIONS: dict[str, str] = {
    "reg": "Вопрос: является ли язык регулярным?",
    "cfl": "Вопрос: является ли язык контекстно-свободным?",
    "dcfl": "Вопрос: является ли язык детерминированным контекстно-свободным (DCFL)?",
}
DEEP_LL_GRAMMAR_QUESTION = ("Вопрос: является ли данная грамматика LL(k) для некоторого k, "
                            "и если да, то каково наименьшее такое k?")
DEEP_LL_LANGUAGE_QUESTION = ("Вопрос: является ли язык LL(k)-языком для некоторого k, "
                             "и если да, то каково наименьшее такое k?")


def deep_class_question(task_class: str, ir: dict) -> str:
    """The one-line question for a manifest entry of class *task_class*."""
    if task_class == "ll":
        if ir.get("task_type") == "ll_check_grammar":
            return DEEP_LL_GRAMMAR_QUESTION
        return DEEP_LL_LANGUAGE_QUESTION
    question = DEEP_CLASS_QUESTIONS.get(task_class)
    if question is None:
        raise DeepAdapterError(f"no deep question for task class {task_class!r}")
    return question


def deep_task_text(ir: dict, task_class: str) -> str:
    """The only input the deep agent gets: the IR's `source_text`, then on
    its own line the question of the entry's class (`deep_class_question`)."""
    text = ir.get("source_text")
    if not isinstance(text, str) or not text.strip():
        raise DeepAdapterError("the task IR has no source_text (the deep agent's only input)")
    return f"{text.strip()}\n{deep_class_question(task_class, ir)}"


def load_deep_settings(path: str | None = None) -> Any:
    """`tfl_deep_agent.settings.load_settings` (*path*, else the package's
    settings.json, else the defaults); an explicit *path* must exist."""
    if path is not None and not Path(path).is_file():
        raise DeepAdapterError(f"deep settings file not found: {path}")
    from tfl_deep_agent import settings as settings_mod

    try:
        return settings_mod.load_settings(path)
    except settings_mod.SettingsError as exc:
        problems = "; ".join(getattr(exc, "problems", None) or [])
        raise DeepAdapterError(f"{exc}{': ' + problems if problems else ''}") from None


def run_deep(task_text: str, run_dir: Path, *, mock_script: Path | None = None,
             live: bool = False, settings: Any = None) -> dict:
    """Run the deep agent on *task_text* in *run_dir* with `--auto-approve`
    semantics and return its `result.json` (the run's outcome as a fallback).

    Exactly one of *mock_script* (mock mode, no API) and *live* (real
    Anthropic API calls, spends money) must be given."""
    if (mock_script is not None) == bool(live):
        raise DeepAdapterError("run_deep needs exactly one of mock_script or live=True")
    from tfl_deep_agent import hitl

    try:
        outcome = hitl.start_run(
            run_dir, task_text, settings=settings,
            mock_script=str(mock_script) if mock_script is not None else None,
            live=bool(live), auto_approve=True, dialog=None,
        )
    except (hitl.RunError, hitl.DecisionError) as exc:
        raise DeepAdapterError(str(exc)) from None
    if outcome.awaiting:
        point = getattr(outcome.pending, "point", "?")
        raise DeepAdapterError(f"the run stopped for a human decision at {point} despite auto-approve")
    result_path = Path(run_dir) / "result.json"
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    return dict(outcome.result or {})


def _deep_ll_k(verdict_text: Any) -> int | None:
    """k from the report's verdict text ('LL(1)', 'grammar is LL(2)'), if any."""
    m = re.search(r"ll\s*\(\s*(\d+)\s*\)", str(verdict_text or "").lower())
    return int(m.group(1)) if m else None


def extract_deep(result: dict, task_class: str | None = None) -> dict:
    """Normalize a deep-agent `result.json` to the `extract` shape plus the
    deep-specific fields `deep_status`, `language_class`, `class_mismatch`,
    `trust`.

    * verdict: the published policy token when the report was accepted
      (`status: submitted`), else None (a run without a report is
      inconclusive);
    * confidence: the code-set ceiling (`confidence`, else `confidence_cap`);
      the model's self-assessment never raises it;
    * k: for an `ll` verdict, the k named in the report's verdict text;
    * class_mismatch: the report answered another class than the manifest
      task's (its token is then from another vocabulary and scores wrong).
    """
    status = result.get("status")
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    submitted = status == "submitted" and bool(report)
    lang = report.get("language_class") if submitted else None

    verdict = None
    k = None
    if submitted:
        # `published_verdict` is what the result publishes (REG: None for an
        # unresolved contradiction); older results only carry the gate token
        verdict = result["published_verdict"] if "published_verdict" in result else result.get("verdict_token")
        if lang == "ll" and verdict == "ll":
            k = _deep_ll_k(report.get("verdict"))

    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = result.get("confidence_cap")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = None

    trust = result.get("trust") if submitted else None
    basis = list(result.get("basis") or []) if submitted else []
    gate = {
        "trust": trust,
        "confidence_cap": result.get("confidence_cap"),
        "basis": basis,
        "downgrades": list(result.get("verdict_gate") or []),
        "verdict_direction": result.get("verdict_direction"),
        "contradiction": result.get("contradiction"),
    }
    return {
        "verdict": verdict,
        "confidence": confidence,
        "k": k,
        "verdict_gate": gate,
        "basis_trust": [trust] if basis and trust else [],
        "deep_status": status,
        "language_class": lang,
        "class_mismatch": bool(submitted and task_class is not None and lang != task_class),
        "trust": trust,
        "human_review": result.get("human_review"),
        "budget_spent_usd": (result.get("budget") or {}).get("spent_usd"),
    }
