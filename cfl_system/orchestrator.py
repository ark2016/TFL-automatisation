"""
CFL pipeline orchestrator — LangGraph StateGraph implementation.

Implements the pipeline graph from §3 of the CFL spec using LangGraph
with fan-out/fan-in for parallel specialists, retry cycles, and
hypothesis inversion.

Usage:
    from cfl_system.orchestrator import run_pipeline, MockRunner, LiveRunner
    result = run_pipeline(ir_dict, mock_runner=MockRunner("examples/mock/", "task_w1w2w1w3"))

    # CLI:
    python -m cfl_system.orchestrator examples/task_w1w2w1w3.json --mock examples/mock/
    python -m cfl_system.orchestrator examples/task_w1w2w1w3.json --live
"""

from __future__ import annotations

import json
import logging
import operator
import os
import re as _re
import sys
import time as _time
from pathlib import Path
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from cfl_system.lib.cfl_ir_schema import validate_cfl_ir
from cfl_system.lib.cfl_hypothesis import analyze_cfl_hypothesis
from cfl_system.lib.language_preprocess import preprocess_language
from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
from cfl_system.lib.cfl_oracle_test import oracle_test
from cfl_system.lib.claim_verifier import verify_agent_claims

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
MAX_INVERSIONS = 1

CFL_SPECIALIST_NAMES = (
    "cfg_builder", "pda_builder", "decomposition", "parikh",
    "pumping_cfl", "ogden", "closure_reduction", "interchange", "morphism",
)

_CONSTRUCTIVE_AGENTS = {"cfg_builder", "pda_builder"}


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    """Full state flowing through the LangGraph pipeline."""

    # -- Inputs --
    ir: dict
    mock_runner: Any
    agent_runner: Any
    verbose: bool

    # -- Pipeline data --
    hypothesis: dict
    classifier_output: dict
    preprocess_output: dict

    # -- Specialist dispatch --
    dispatch: dict                                       # {name: bool}
    agents_to_retry: Any                                 # None = all, list = selective
    specialist_outputs: Annotated[list, operator.add]    # [(name, output)]
    agent_results: dict                                  # accumulated across retries

    # -- Oracle --
    oracle_fn: Any
    oracle_ok: bool

    # -- Verification --
    claim_verification: dict
    oracle_test_result: dict

    # -- Reasoning & retry --
    reasoning_output: dict
    proof_checker_output: dict
    retry_round: int
    inversions_done: int
    retry_context: dict
    retry_params: dict

    # -- Accumulated --
    evidence: dict
    errors: Annotated[list, operator.add]

    # -- Fan-out helper --
    _specialist_name: str

    # -- Final --
    result: dict


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

class MockRunner:
    """Load agent outputs from JSON files in a mock directory."""

    def __init__(self, mock_dir: str, task_name: str):
        self.mock_dir = Path(mock_dir)
        self.task_name = task_name

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        path = self.mock_dir / f"{self.task_name}_{agent_name}.json"
        if not path.exists():
            path = self.mock_dir / f"{agent_name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None


class LiveRunner:
    """Run agents via Anthropic API using prompts from prompts/ directory."""

    def __init__(self, api_key: str | None = None, verbose: bool = False):
        from cfl_system.config import (
            MODELS, TEMPERATURES, MAX_TOKENS,
            LLM_JSON_RETRIES, PROMPT_FILES, ANTHROPIC_API_KEY,
        )
        import anthropic

        # Load .env from project root if python-dotenv is available
        if not api_key and not ANTHROPIC_API_KEY:
            try:
                from dotenv import load_dotenv
                load_dotenv(Path(__file__).resolve().parent.parent / ".env")
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            except ImportError:
                pass

        resolved_key = api_key or ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Pass api_key= or set the env var."
            )
        self.client = anthropic.Anthropic(api_key=resolved_key)
        self.models = MODELS
        self.temperatures = TEMPERATURES
        self.max_tokens = MAX_TOKENS
        self.json_retries = LLM_JSON_RETRIES
        self.prompt_files = PROMPT_FILES
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.verbose = verbose
        self._prompt_cache: dict[str, str] = {}

    def _load_prompt(self, agent_name: str) -> str:
        if agent_name in self._prompt_cache:
            return self._prompt_cache[agent_name]
        filename = self.prompt_files.get(agent_name)
        if not filename:
            raise ValueError(f"No prompt file configured for agent '{agent_name}'")
        path = self.prompts_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        text = path.read_text(encoding="utf-8")
        self._prompt_cache[agent_name] = text
        return text

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        try:
            system_prompt = self._load_prompt(agent_name)
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Skipping agent '%s': %s", agent_name, exc)
            return None

        model = self.models.get(agent_name, "claude-sonnet-4-6")
        temperature = self.temperatures.get(agent_name, 0.0)
        user_content = json.dumps(input_data or {}, ensure_ascii=False, indent=2)

        last_error: str | None = None
        for attempt in range(1 + self.json_retries):
            if attempt > 0 and last_error:
                user_msg = (
                    f"{user_content}\n\n"
                    f"[RETRY {attempt}/{self.json_retries}] "
                    f"Your previous response was not valid JSON. Error: {last_error}\n"
                    f"Please respond with ONLY a valid JSON object."
                )
            else:
                user_msg = user_content

            t0 = _time.monotonic()
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )
            except Exception as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                return None

            elapsed = _time.monotonic() - t0
            raw_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw_text += block.text

            tokens_in = response.usage.input_tokens if response.usage else 0
            tokens_out = response.usage.output_tokens if response.usage else 0

            if self.verbose:
                print(
                    f"[{agent_name}] model={model} "
                    f"tokens_in={tokens_in} tokens_out={tokens_out} "
                    f"time={elapsed:.1f}s",
                    file=sys.stderr, flush=True,
                )

            parsed = _extract_json(raw_text)
            if parsed is not None:
                return parsed

            last_error = f"Could not parse JSON from response (length={len(raw_text)})"
            logger.warning("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)

        logger.error("[%s] JSON parse failed after %d attempts", agent_name, 1 + self.json_retries)
        return {
            "agent": agent_name, "status": "agent_error", "verdict": None,
            "confidence": 0.0, "evidence": {},
            "errors": [f"Failed to parse JSON after {1 + self.json_retries} attempts"],
            "raw_response": raw_text[:2000],
        }


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from LLM response text."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    fence_match = _re.search(r"```(?:json)?\s*\n(.*?)\n```", text, _re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            obj = json.loads(text[first_brace:last_brace + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_runner(state: PipelineState) -> MockRunner | LiveRunner | None:
    return state.get("mock_runner") or state.get("agent_runner")


def _run_agent(state: PipelineState, agent_name: str, input_data: dict | None = None) -> dict | None:
    runner = _get_runner(state)
    if runner is None:
        return None
    try:
        return runner.run_agent(agent_name, input_data)
    except NotImplementedError:
        return None
    except Exception as exc:
        logger.warning("Agent '%s' failed: %s", agent_name, exc)
        return None


def log_msg(state: PipelineState, msg: str) -> None:
    if state.get("verbose"):
        print(f"  [{_time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _build_specialist_input(state: PipelineState, agent_name: str) -> dict:
    """Build the input dict for a specialist or system agent."""
    inp: dict[str, Any] = {
        "ir": state["ir"],
        "hypothesis": state.get("hypothesis", {}),
        "classifier_hint": state.get("classifier_output", {}),
        "preprocess": state.get("preprocess_output", {}),
    }
    retry_ctx = state.get("retry_context") or {}
    if retry_ctx:
        hints = retry_ctx.get("hints", {}) or {}
        inp["retry_params"] = hints.get(agent_name)
        # Provide prior oracle_test feedback on retry (normalized)
        prior_oracle = state.get("oracle_test_result")
        if prior_oracle:
            inp["prior_oracle_test"] = _normalize_oracle_test(prior_oracle)
    return inp


def _build_reasoning_input(state: PipelineState) -> dict:
    """Build input for the reasoning agent (matches cfl_reasoning.md prompt)."""
    evidence = state.get("evidence", {})
    return {
        "ir": state["ir"],
        "hypothesis": state.get("hypothesis", {}),
        "classifier_hint": state.get("classifier_output", {}),
        "specialist_outputs": {
            k: evidence[k] for k in CFL_SPECIALIST_NAMES if k in evidence
        },
        "oracle_test": _normalize_oracle_test(state.get("oracle_test_result")),
        "claim_verification": _normalize_claim_verification(state.get("claim_verification")),
        "proof_checker": state.get("proof_checker_output"),
        "retry_count": state.get("retry_round", 0),
        "inversion_count": state.get("inversions_done", 0),
        "max_retries": MAX_RETRIES,
        "max_inversions": MAX_INVERSIONS,
    }


def _get_action(reasoning_output: dict) -> str:
    """Extract action from reasoning output — supports both 'action' and 'decision' fields."""
    return reasoning_output.get("action") or reasoning_output.get("decision") or "done"


# Valid verdict values per reasoning prompt contract
_VALID_VERDICTS = {"cfl", "non_cfl", None}


def _normalize_verdict(raw: Any) -> str | None:
    """Normalize a verdict string to one of {"cfl", "non_cfl", None}.

    Maps common synonyms (context_free, regular, non-cfl, etc.) and rejects
    anything else (returns None), so CLI exit codes and downstream logic
    can rely on a strict enum.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("cfl", "context_free", "is_cfl", "cfg"):
        return "cfl"
    if v in ("non_cfl", "not_cfl", "noncfl", "not_context_free", "not_cf"):
        return "non_cfl"
    return None


# Oracle test status values allowed in LLM-facing contracts per prompts.
_ORACLE_STATUS_MAP = {
    "pass": "pass",
    "fail": "fail",
    "grammar_incorrect": "fail",
    "error": "not_applicable",
    "skipped": "not_applicable",
    "not_applicable": "not_applicable",
}


def _normalize_oracle_test(raw: dict | None) -> dict:
    """Normalize oracle_test result for LLM prompts (pass|fail|not_applicable only)."""
    if not raw:
        return {"status": "not_applicable"}
    if not isinstance(raw, dict):
        return {"status": "not_applicable"}
    normalized = dict(raw)
    orig_status = normalized.get("status", "not_applicable")
    normalized["status"] = _ORACLE_STATUS_MAP.get(orig_status, "not_applicable")
    if orig_status != normalized["status"]:
        normalized["raw_status"] = orig_status
    return normalized


def _normalize_claim_verification(raw: dict | None) -> dict:
    """Normalize claim_verification: add 'status' field from 'verification_status'."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for agent, cv in raw.items():
        if isinstance(cv, dict):
            normalized = dict(cv)
            if "verification_status" in normalized and "status" not in normalized:
                normalized["status"] = normalized["verification_status"]
            result[agent] = normalized
        else:
            result[agent] = cv
    return result


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def validate_ir_node(state: PipelineState) -> dict:
    log_msg(state, "validate_ir_node...")
    errs = validate_cfl_ir(state["ir"])
    if errs:
        return {"errors": errs}
    return {}


def assemble_early_failure(state: PipelineState) -> dict:
    ir = state.get("ir", {})
    errors = list(state.get("errors", []))
    reasoning = state.get("reasoning_output", {}) or {}

    # Also harvest any errors that the reasoning agent itself reported
    reasoning_errors = reasoning.get("errors") or []
    if isinstance(reasoning_errors, list):
        errors.extend(str(e) for e in reasoning_errors)

    if errors:
        detail = "; ".join(errors)
    else:
        # Prefer the contract-compliant 'summary' field, fall back to legacy
        detail = (
            reasoning.get("summary")
            or reasoning.get("reasoning")
            or "Max retries exhausted"
        )

    # Preserve any oracle_test that did run before failure (normalized).
    oracle_report = None
    raw_oracle = state.get("oracle_test_result")
    if raw_oracle and isinstance(raw_oracle, dict):
        normalized = _normalize_oracle_test(raw_oracle)
        if normalized.get("status") != "not_applicable":
            oracle_report = normalized

    agent_results = state.get("agent_results", {}) or {}
    # Extract any grammar/PDA that was built before failure
    grammar = pda = None
    for name in _CONSTRUCTIVE_AGENTS:
        output = agent_results.get(name, {})
        if not isinstance(output, dict):
            continue
        if not grammar:
            grammar = output.get("grammar") or (output.get("evidence", {}) or {}).get("grammar")
        if not pda:
            pda = output.get("pda") or (output.get("evidence", {}) or {}).get("pda")

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "failure" if errors else "inconclusive",
            "confidence": 0.0,
            "proof": None,
            "grammar": grammar,
            "pda": pda,
            "oracle_test": oracle_report,
            "agents_used": sorted(agent_results.keys()),
            "retries": state.get("retry_round", 0),
            "inversions": state.get("inversions_done", 0),
            "errors": errors if errors else [detail],
        },
    }


def analyze_hypothesis_node(state: PipelineState) -> dict:
    log_msg(state, "analyze_hypothesis_node...")
    hyp = analyze_cfl_hypothesis(state["ir"])
    log_msg(state, f"  hypothesis={hyp.get('hypothesis')} conf={hyp.get('confidence')}")
    return {"hypothesis": hyp}


def run_classifier_node(state: PipelineState) -> dict:
    log_msg(state, "run_classifier_node (advisory)...")
    classifier_input = {
        "ir": state["ir"],
        "hypothesis": state.get("hypothesis", {}),
        "preprocess": state.get("preprocess_output", {}),
    }
    output = _run_agent(state, "classifier", classifier_input)
    if output is None or output.get("status") == "agent_error":
        if output is not None:
            log_msg(state, "  classifier returned agent_error, ignoring")
        return {}
    evidence = dict(state.get("evidence", {}))
    evidence["classifier"] = output
    return {"classifier_output": output, "evidence": evidence}


def language_preprocess_node(state: PipelineState) -> dict:
    log_msg(state, "language_preprocess_node...")
    pp = preprocess_language(state["ir"])
    qv = pp.get("quick_verdict")
    if qv:
        log_msg(state, f"  quick_verdict={qv}")
    return {"preprocess_output": pp}


def setup_dispatch_node(state: PipelineState) -> dict:
    """Dispatch ALL 9 agents (first run) or selected agents (retry).

    An empty agents_to_retry list after validation means the planner
    asked for 0 agents — this should already be handled as terminal
    by decide_after_retry_planner. If we reach here with an empty
    list, fall back to all agents (defensive).
    """
    agents_to_retry = state.get("agents_to_retry")

    if isinstance(agents_to_retry, list) and len(agents_to_retry) > 0:
        valid = [a for a in agents_to_retry if a in CFL_SPECIALIST_NAMES]
        if valid:
            dispatch = {name: (name in valid) for name in CFL_SPECIALIST_NAMES}
            log_msg(state, f"  selective dispatch: {valid}")
            return {"dispatch": dispatch}
        log_msg(state, "  agents_to_retry had no valid agents, falling back to all")

    dispatch = {name: True for name in CFL_SPECIALIST_NAMES}
    log_msg(state, "  full dispatch: all 9 agents")
    return {"dispatch": dispatch}


def dispatch_to_specialists(state: PipelineState) -> list[Send]:
    """Conditional edge: fan-out to specialist nodes via Send()."""
    dispatch = state.get("dispatch", {})
    dispatched = [k for k, v in dispatch.items() if v]
    if not dispatched:
        return [Send("collect_specialists_node", state)]
    return [
        Send("run_specialist_node", {**state, "_specialist_name": name})
        for name in dispatched
    ]


def run_specialist_node(state: PipelineState) -> dict:
    """Run a single specialist agent. Invoked via Send() fan-out.

    Emits a tuple (agent_name, output_or_None). `None` signals that the
    retried agent failed (either runner returned None, or output was
    agent_error). This lets `collect_specialists_node` drop stale results.
    """
    agent_name = state["_specialist_name"]
    log_msg(state, f"  specialist: {agent_name}...")
    inp = _build_specialist_input(state, agent_name)
    out = _run_agent(state, agent_name, inp)

    # Treat agent_error as a failed run — emit None marker so collect
    # can drop previous stale results for this agent on retry.
    if out is not None and out.get("status") == "agent_error":
        log_msg(state, f"  {agent_name} returned agent_error")
        return {"specialist_outputs": [(agent_name, None)]}

    return {"specialist_outputs": [(agent_name, out)]}


def collect_specialists_node(state: PipelineState) -> dict:
    """Fan-in: merge specialist_outputs into agent_results and evidence.

    On retry, if the retried agent emitted None (failed or errored), drop
    its previous result to avoid carrying stale evidence forward.
    """
    agent_results = dict(state.get("agent_results", {}))

    # Only the currently dispatched agents are eligible for update.
    # (Send() fan-out puts new outputs at the end of specialist_outputs.)
    dispatched = {k for k, v in state.get("dispatch", {}).items() if v}

    # Process specialist_outputs in order, keeping only the latest entry
    # per agent from the current round (dispatched set).
    latest_this_round: dict[str, Any] = {}
    for name, out in state.get("specialist_outputs", []):
        if name in dispatched:
            latest_this_round[name] = out

    for name, out in latest_this_round.items():
        if out is None:
            # Retried agent failed → drop any stale result
            agent_results.pop(name, None)
        else:
            agent_results[name] = out

    evidence = dict(state.get("evidence", {}))
    # Rebuild evidence entries for specialists (remove ones that were dropped)
    for name in CFL_SPECIALIST_NAMES:
        if name in agent_results:
            evidence[name] = agent_results[name]
        else:
            evidence.pop(name, None)

    log_msg(state, f"  collected specialists (round): {sorted(latest_this_round.keys())}")

    return {"agent_results": agent_results, "evidence": evidence}


def build_oracle_node(state: PipelineState) -> dict:
    if state.get("retry_round", 0) > 0 and state.get("oracle_fn") is not None:
        return {}
    log_msg(state, "build_oracle_node...")
    try:
        oracle_fn = cfl_oracle_from_ir(state["ir"])
        return {"oracle_fn": oracle_fn, "oracle_ok": True}
    except Exception as exc:
        log_msg(state, f"  oracle build failed: {exc}")
        return {"oracle_fn": None, "oracle_ok": False}


def verify_claims_node(state: PipelineState) -> dict:
    log_msg(state, "verify_claims_node...")
    ir = state["ir"]
    verifications: dict[str, dict] = {}
    for name, output in state.get("agent_results", {}).items():
        agent_output = {
            "agent": name,
            "status": output.get("status", "success"),
            "evidence": output.get("evidence", output),
        }
        verifications[name] = verify_agent_claims(agent_output, ir)
    return {"claim_verification": verifications}


def oracle_test_node(state: PipelineState) -> dict:
    log_msg(state, "oracle_test_node...")
    ir = state["ir"]
    oracle_fn = state.get("oracle_fn")
    if not oracle_fn:
        return {"oracle_test_result": {"status": "not_applicable", "details": "no oracle"}}

    constructive_evidence: dict[str, Any] = {}
    for name in _CONSTRUCTIVE_AGENTS:
        output = state.get("agent_results", {}).get(name)
        if output is None:
            continue
        for key in ("grammar", "pda"):
            if key in output:
                constructive_evidence[key] = output[key]
            ev = output.get("evidence", {})
            if isinstance(ev, dict) and key in ev:
                constructive_evidence[key] = ev[key]

    if not constructive_evidence:
        return {"oracle_test_result": {"status": "not_applicable", "details": "no grammar/PDA"}}

    try:
        result = oracle_test(constructive_evidence, ir)
        log_msg(state, f"  oracle_test: {result.get('status')}")
        return {"oracle_test_result": result}
    except Exception as exc:
        return {"oracle_test_result": {"status": "error", "details": str(exc)}}


def run_proof_checker_node(state: PipelineState) -> dict:
    log_msg(state, "run_proof_checker_node...")
    evidence = state.get("evidence", {})
    checker_input = {
        "ir": state["ir"],
        "specialist_outputs": {
            k: evidence[k] for k in CFL_SPECIALIST_NAMES if k in evidence
        },
        "oracle_test": _normalize_oracle_test(state.get("oracle_test_result")),
        "claim_verification": _normalize_claim_verification(state.get("claim_verification")),
    }
    output = _run_agent(state, "proof_checker", checker_input)
    if output is not None and output.get("status") == "agent_error":
        log_msg(state, "  proof_checker returned agent_error, ignoring")
        output = None
    return {"proof_checker_output": output or {}}


def run_reasoning_node(state: PipelineState) -> dict:
    log_msg(state, "run_reasoning_node...")
    reasoning_input = _build_reasoning_input(state)
    output = _run_agent(state, "reasoning", reasoning_input)

    # Normalize verdict against the contract enum {"cfl", "non_cfl", null}
    if isinstance(output, dict) and "verdict" in output:
        raw_verdict = output.get("verdict")
        normalized = _normalize_verdict(raw_verdict)
        if raw_verdict is not None and normalized != raw_verdict:
            log_msg(state, f"  normalized verdict {raw_verdict!r} -> {normalized!r}")
        output["verdict"] = normalized

    # Detect invalid outputs that require fallback:
    #   - None: runner unavailable or call failed
    #   - agent_error: LLM returned non-JSON
    #   - no action/decision field at all
    #   - action="done" but verdict is missing (contract violation)
    needs_fallback = False
    if output is None:
        needs_fallback = True
    elif output.get("status") == "agent_error":
        needs_fallback = True
    elif not output.get("action") and not output.get("decision"):
        needs_fallback = True
    elif _get_action(output) == "done" and not output.get("verdict"):
        needs_fallback = True

    if needs_fallback:
        log_msg(state, "  reasoning unavailable/invalid, using fallback")
        output = _fallback_reasoning(state)

    action = _get_action(output)
    log_msg(state, f"  action={action} verdict={output.get('verdict')}")
    return {"reasoning_output": output}


def _clamp_confidence(value: Any) -> float:
    """Clamp any value to a valid confidence [0.0, 1.0]."""
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _fallback_reasoning_result(
    action: str,
    verdict: str | None,
    confidence: float,
    summary: str,
    primary_evidence: str = "",
    supporting_evidence: list[str] | None = None,
    retry_plan: dict | None = None,
) -> dict:
    """Build a contract-compliant reasoning output for the fallback path.

    Matches the schema in prompts/cfl_reasoning.md (hints_for_human is a list).
    """
    return {
        "agent": "reasoning",
        "action": action,
        "decision": action,  # mirror for both field names
        "verdict": _normalize_verdict(verdict),
        "confidence": _clamp_confidence(confidence),
        "primary_evidence": primary_evidence,
        "supporting_evidence": supporting_evidence or [],
        "contradictions": [],
        "summary": summary,
        "primary_justification": summary,
        "retry_plan": retry_plan,
        "hints_for_human": ["Fallback reasoning (no LLM available)"],
        "errors": [],
    }


def _fallback_reasoning(state: PipelineState) -> dict:
    """Heuristic reasoning when no reasoning agent is available.

    Returns a contract-compliant reasoning output with all required fields.
    Confidence values are clamped to [0, 1].
    """
    hypothesis = state.get("hypothesis", {}) or {}
    preprocess = state.get("preprocess_output", {}) or {}
    oracle_result = state.get("oracle_test_result", {}) or {}
    verifications = state.get("claim_verification", {}) or {}
    agent_results = state.get("agent_results", {}) or {}

    # Quick verdict from preprocessing is authoritative
    quick_verdict = preprocess.get("quick_verdict")
    if quick_verdict in ("cfl", "non_cfl"):
        return _fallback_reasoning_result(
            action="done", verdict=quick_verdict, confidence=0.9,
            summary=preprocess.get("quick_verdict_reason", "Quick verdict from preprocessing"),
            primary_evidence="preprocess",
        )

    verified_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and v.get("verification_status") == "verified"
    )
    refuted_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and v.get("verification_status") == "refuted"
    )

    # Oracle test counterexamples → grammar is wrong, retry the constructive agents
    oracle_status = oracle_result.get("status") if isinstance(oracle_result, dict) else None
    if oracle_status in ("fail", "grammar_incorrect"):
        retry_round = state.get("retry_round", 0)
        if retry_round < MAX_RETRIES:
            return _fallback_reasoning_result(
                action="retry", verdict=None, confidence=0.3,
                summary="Oracle test found counterexamples in proposed grammar/PDA",
                retry_plan={"agents_to_retry": ["cfg_builder", "pda_builder"], "hints": {}},
            )

    if oracle_status == "pass":
        return _fallback_reasoning_result(
            action="done", verdict="cfl", confidence=0.85,
            summary="Grammar/PDA passes oracle test",
            primary_evidence="oracle_test",
        )

    if refuted_count > 0:
        log_msg(state, f"  {refuted_count} claims refuted, weakening hypothesis")

    hyp = hypothesis.get("hypothesis", "unknown")
    hyp_conf = _clamp_confidence(hypothesis.get("confidence", 0.0))

    if hyp in ("cfl", "non_cfl") and hyp_conf >= 0.7 and verified_count > 0:
        return _fallback_reasoning_result(
            action="done", verdict=hyp,
            confidence=min(hyp_conf + 0.05 * verified_count, 0.95),
            summary=f"Hypothesis '{hyp}' with {verified_count} verified claims",
            primary_evidence="hypothesis",
        )

    # Pick best agent verdict with clamped confidence
    best_agent: str | None = None
    best_verdict: str | None = None
    best_conf = 0.0
    for name, output in agent_results.items():
        if not isinstance(output, dict):
            continue
        verdict = output.get("verdict")
        if verdict not in ("cfl", "non_cfl"):
            continue
        conf = _clamp_confidence(output.get("confidence", 0.5))
        if conf > best_conf:
            best_conf = conf
            best_verdict = verdict
            best_agent = name

    if best_verdict is not None and best_conf >= 0.7:
        return _fallback_reasoning_result(
            action="done", verdict=best_verdict, confidence=best_conf,
            summary=f"Agent '{best_agent}' verdict: {best_verdict}",
            primary_evidence=best_agent or "",
        )

    retry_round = state.get("retry_round", 0)
    if retry_round < MAX_RETRIES:
        return _fallback_reasoning_result(
            action="retry", verdict=None, confidence=0.2,
            summary="Insufficient evidence, retrying",
        )

    # Terminal: no retries left, give inconclusive verdict
    final_verdict = hyp if hyp in ("cfl", "non_cfl") else None
    return _fallback_reasoning_result(
        action="done",
        verdict=final_verdict,
        confidence=max(hyp_conf, 0.3) if final_verdict else 0.0,
        summary="Max retries reached, returning best guess from hypothesis",
        primary_evidence="hypothesis" if final_verdict else "",
    )


def decide_retry(state: PipelineState) -> str:
    """Conditional edge after reasoning: done / retry / invert / fail."""
    reasoning = state.get("reasoning_output", {})
    action = _get_action(reasoning)
    retry_round = state.get("retry_round", 0)
    inversions_done = state.get("inversions_done", 0)

    if action == "done":
        return "done"
    if action == "retry" and retry_round < MAX_RETRIES:
        return "retry"
    if action == "invert" and inversions_done < MAX_INVERSIONS:
        return "invert"
    return "fail"


def run_retry_planner_node(state: PipelineState) -> dict:
    log_msg(state, "run_retry_planner_node...")
    reasoning = state.get("reasoning_output", {})
    evidence = state.get("evidence", {})

    # Inject specialist_outputs into reasoning_output per retry_planner contract.
    reasoning_with_context = dict(reasoning)
    reasoning_with_context["specialist_outputs"] = {
        k: evidence[k] for k in CFL_SPECIALIST_NAMES if k in evidence
    }
    reasoning_with_context["oracle_test"] = _normalize_oracle_test(
        state.get("oracle_test_result")
    )
    reasoning_with_context["proof_checker"] = state.get("proof_checker_output", {})

    planner_input = {
        "reasoning_output": reasoning_with_context,
        "retry_count": state.get("retry_round", 0),
        "max_retries": MAX_RETRIES,
    }
    output = _run_agent(state, "retry_planner", planner_input)

    should_invert = False
    max_retries_remaining: int | None = None
    # Fall back to reasoning.retry_plan if planner unavailable OR returned agent_error
    planner_failed = output is None or (
        isinstance(output, dict) and output.get("status") == "agent_error"
    )
    if not planner_failed:
        agents_to_retry = output.get("agents_to_retry")
        hints = output.get("hints") or output.get("retry_params") or {}
        should_invert = bool(output.get("should_invert_hypothesis", False))
        max_retries_remaining = output.get("max_retries_remaining")
    else:
        if output is not None:
            log_msg(state, "  retry_planner returned agent_error, using reasoning.retry_plan")
        r_plan = reasoning.get("retry_plan", {})
        agents_to_retry = r_plan.get("agents_to_retry") if r_plan else None
        hints = r_plan.get("hints", {}) if r_plan else {}

    # Validate agents_to_retry: filter unknown names to prevent silent 0-agent dispatch
    if isinstance(agents_to_retry, list):
        valid_agents = [a for a in agents_to_retry if a in CFL_SPECIALIST_NAMES]
        if len(valid_agents) != len(agents_to_retry):
            invalid = [a for a in agents_to_retry if a not in CFL_SPECIALIST_NAMES]
            log_msg(state, f"  WARNING: unknown agents in retry list: {invalid}")
        agents_to_retry = valid_agents

    # Terminal condition: planner returns empty retry list, max_retries_remaining<=0,
    # OR retry_round will exceed MAX_RETRIES, and no inversion requested → give up.
    empty_list = agents_to_retry is not None and len(agents_to_retry) == 0
    # Safe comparison: treat None as "not exhausted" (trust retry_round limit)
    exhausted = max_retries_remaining is not None and max_retries_remaining <= 0
    next_round = state.get("retry_round", 0) + 1
    hard_limit = next_round > MAX_RETRIES
    terminal = (empty_list or exhausted or hard_limit) and not should_invert

    log_msg(
        state,
        f"  retry round -> {next_round}, agents={agents_to_retry}, "
        f"should_invert={should_invert}, terminal={terminal}",
    )

    return {
        "agents_to_retry": agents_to_retry,
        "retry_params": hints,
        "retry_round": next_round,
        "retry_context": {
            "reasoning": reasoning,
            "hints": hints,
            "should_invert": should_invert,
            "terminal": terminal,
        },
    }


def decide_after_retry_planner(state: PipelineState) -> str:
    """After retry_planner: invert / dispatch / fail."""
    ctx = state.get("retry_context", {})
    if ctx.get("terminal"):
        return "fail"
    if ctx.get("should_invert") and state.get("inversions_done", 0) < MAX_INVERSIONS:
        return "invert"
    return "dispatch"


def invert_hypothesis_node(state: PipelineState) -> dict:
    hypothesis = dict(state.get("hypothesis", {}))
    current = hypothesis.get("hypothesis", "unknown")
    new_hyp = "non_cfl" if current == "cfl" else "cfl"

    log_msg(state, f"  inverting hypothesis: {current} -> {new_hyp}")
    hypothesis["hypothesis"] = new_hyp
    hypothesis["reasoning"] = f"Inverted from '{current}'"

    # If planner asked for selective retry AND inversion, preserve the agents list.
    # Only clear agents_to_retry when entering from reasoning "invert" (no planner context).
    ctx = state.get("retry_context") or {}
    came_from_planner = "should_invert" in ctx
    preserved_agents = state.get("agents_to_retry") if came_from_planner else None

    return {
        "hypothesis": hypothesis,
        "inversions_done": state.get("inversions_done", 0) + 1,
        "agents_to_retry": preserved_agents,
        # Do NOT increment retry_round — inversion is separate from retry
    }


def formalize_node(state: PipelineState) -> dict:
    """Run formalizer agent → structured Markdown proof (no Lean)."""
    runner = _get_runner(state)
    if runner is None:
        return {}

    reasoning = state.get("reasoning_output", {})
    verdict = reasoning.get("verdict")
    if not verdict or verdict == "inconclusive":
        return {}

    agent_results = state.get("agent_results", {})
    primary = reasoning.get("primary_evidence", "")
    formalizer_input = {
        "ir": state["ir"],
        "reasoning_output": reasoning,
        "specialist_output": agent_results.get(primary, {}),
    }

    output = _run_agent(state, "formalizer", formalizer_input)
    if output is None or output.get("status") == "agent_error":
        return {}

    evidence = dict(state.get("evidence", {}))
    evidence["formalizer"] = output
    proof = output.get("proof_document") or output.get("markdown")
    if proof:
        evidence["formatted_proof"] = proof
    return {"evidence": evidence}


def assemble_result_node(state: PipelineState) -> dict:
    ir = state["ir"]
    reasoning = state.get("reasoning_output", {})
    oracle_result = state.get("oracle_test_result", {})
    agent_results = state.get("agent_results", {})
    proof_checker = state.get("proof_checker_output", {})

    raw_verdict = reasoning.get("verdict")
    normalized = _normalize_verdict(raw_verdict)
    verdict = normalized or "inconclusive"
    confidence = _clamp_confidence(reasoning.get("confidence"))

    grammar = pda = None
    for name in _CONSTRUCTIVE_AGENTS:
        output = agent_results.get(name, {})
        if not isinstance(output, dict):
            continue
        if not grammar:
            grammar = output.get("grammar") or (output.get("evidence", {}) or {}).get("grammar")
        if not pda:
            pda = output.get("pda") or (output.get("evidence", {}) or {}).get("pda")

    # Proof source priority: reasoning.proof > formalizer.proof_document > formatted_proof string
    # (proof_checker doesn't return a proof field per its prompt schema)
    proof = None
    evidence = state.get("evidence", {}) or {}
    if reasoning.get("proof"):
        proof = reasoning["proof"]
    else:
        formalizer_out = evidence.get("formalizer") or {}
        if isinstance(formalizer_out, dict):
            if formalizer_out.get("proof_document"):
                proof = formalizer_out["proof_document"]
            elif formalizer_out.get("markdown"):
                proof = formalizer_out["markdown"]
        if proof is None and evidence.get("formatted_proof"):
            proof = evidence["formatted_proof"]

    # Normalize oracle_test status for the public result contract.
    # Keep the raw status in 'raw_status' for debugging.
    oracle_report = None
    if oracle_result and isinstance(oracle_result, dict):
        normalized = _normalize_oracle_test(oracle_result)
        if normalized.get("status") != "not_applicable":
            oracle_report = normalized

    errors = list(state.get("errors", []))

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": verdict,
            "confidence": confidence,
            "proof": proof,
            "grammar": grammar,
            "pda": pda,
            "oracle_test": oracle_report,
            "agents_used": sorted(agent_results.keys()),
            "retries": state.get("retry_round", 0),
            "inversions": state.get("inversions_done", 0),
            "errors": errors,
        },
    }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_cfl_pipeline_graph() -> Any:
    """Build and compile the CFL LangGraph pipeline.

    Returns a compiled StateGraph ready for .invoke(initial_state).
    """
    graph = StateGraph(PipelineState)

    # -- Register nodes --
    graph.add_node("validate_ir_node", validate_ir_node)
    graph.add_node("assemble_early_failure", assemble_early_failure)
    graph.add_node("analyze_hypothesis_node", analyze_hypothesis_node)
    graph.add_node("run_classifier_node", run_classifier_node)
    graph.add_node("language_preprocess_node", language_preprocess_node)
    graph.add_node("setup_dispatch_node", setup_dispatch_node)
    graph.add_node("run_specialist_node", run_specialist_node)
    graph.add_node("collect_specialists_node", collect_specialists_node)
    graph.add_node("build_oracle_node", build_oracle_node)
    graph.add_node("verify_claims_node", verify_claims_node)
    graph.add_node("oracle_test_node", oracle_test_node)
    graph.add_node("run_proof_checker_node", run_proof_checker_node)
    graph.add_node("run_reasoning_node", run_reasoning_node)
    graph.add_node("run_retry_planner_node", run_retry_planner_node)
    graph.add_node("invert_hypothesis_node", invert_hypothesis_node)
    graph.add_node("formalize_node", formalize_node)
    graph.add_node("assemble_result_node", assemble_result_node)

    # -- Edges --

    # START → validate
    graph.add_edge(START, "validate_ir_node")

    # validate → ok/fail
    graph.add_conditional_edges(
        "validate_ir_node",
        lambda state: "fail" if state.get("errors") else "ok",
        {"fail": "assemble_early_failure", "ok": "analyze_hypothesis_node"},
    )
    graph.add_edge("assemble_early_failure", END)

    # Analysis chain: preprocess before classifier (classifier prompt expects preprocess data)
    graph.add_edge("analyze_hypothesis_node", "language_preprocess_node")
    graph.add_edge("language_preprocess_node", "run_classifier_node")
    graph.add_edge("run_classifier_node", "setup_dispatch_node")

    # Fan-out: dispatch → specialists via Send()
    graph.add_conditional_edges(
        "setup_dispatch_node",
        dispatch_to_specialists,
        ["run_specialist_node", "collect_specialists_node"],
    )

    # Fan-in: specialist → collect
    graph.add_edge("run_specialist_node", "collect_specialists_node")

    # Verification chain
    graph.add_edge("collect_specialists_node", "build_oracle_node")
    graph.add_edge("build_oracle_node", "verify_claims_node")
    graph.add_edge("verify_claims_node", "oracle_test_node")
    graph.add_edge("oracle_test_node", "run_proof_checker_node")
    graph.add_edge("run_proof_checker_node", "run_reasoning_node")

    # Decision: done / retry / invert / fail
    graph.add_conditional_edges(
        "run_reasoning_node",
        decide_retry,
        {
            "done": "formalize_node",
            "retry": "run_retry_planner_node",
            "invert": "invert_hypothesis_node",
            "fail": "assemble_early_failure",
        },
    )

    # Retry planner → dispatch / invert / fail
    graph.add_conditional_edges(
        "run_retry_planner_node",
        decide_after_retry_planner,
        {
            "dispatch": "setup_dispatch_node",
            "invert": "invert_hypothesis_node",
            "fail": "assemble_early_failure",
        },
    )

    # Invert → back to dispatch (cycle)
    graph.add_edge("invert_hypothesis_node", "setup_dispatch_node")

    # Final
    graph.add_edge("formalize_node", "assemble_result_node")
    graph.add_edge("assemble_result_node", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_pipeline(
    ir: dict,
    mock_runner: MockRunner | None = None,
    agent_runner: LiveRunner | None = None,
    verbose: bool = False,
) -> dict:
    """Run the full CFL pipeline and return the result dict.

    Builds the LangGraph StateGraph, constructs the initial state,
    invokes the graph, and extracts the result.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO)

    graph = build_cfl_pipeline_graph()

    initial_state: dict[str, Any] = {
        "ir": ir,
        "mock_runner": mock_runner,
        "agent_runner": agent_runner,
        "verbose": verbose,
        "hypothesis": {},
        "classifier_output": {},
        "preprocess_output": {},
        "dispatch": {},
        "agents_to_retry": None,
        "specialist_outputs": [],
        "agent_results": {},
        "oracle_fn": None,
        "oracle_ok": False,
        "claim_verification": {},
        "oracle_test_result": {},
        "reasoning_output": {},
        "proof_checker_output": {},
        "retry_round": 0,
        "inversions_done": 0,
        "retry_context": {},
        "retry_params": {},
        "evidence": {},
        "errors": [],
        "_specialist_name": "",
        "result": {},
    }

    final_state = graph.invoke(initial_state)
    result = final_state.get("result")
    if not result:
        return {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "failure",
            "confidence": 0.0,
            "proof": None,
            "grammar": None,
            "pda": None,
            "oracle_test": None,
            "agents_used": [],
            "retries": 0,
            "inversions": 0,
            "errors": ["Graph produced no result"],
        }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the CFL analysis pipeline")
    parser.add_argument("task_file", help="Path to CFL IR JSON file")
    parser.add_argument("--mock", help="Mock directory for agent outputs")
    parser.add_argument("--live", action="store_true", help="Use Anthropic API")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--save", help="Save result to this directory")
    parser.add_argument("--draw-graph", help="Save pipeline graph PNG to this path")
    args = parser.parse_args()

    if args.draw_graph:
        g = build_cfl_pipeline_graph()
        mmd = g.get_graph().draw_mermaid()
        Path(args.draw_graph).with_suffix(".mmd").write_text(mmd, encoding="utf-8")
        try:
            png = g.get_graph().draw_png()
            Path(args.draw_graph).with_suffix(".png").write_bytes(png)
            print(f"Graph saved: {args.draw_graph}.png", file=sys.stderr)
        except Exception:
            print(f"Graph saved: {args.draw_graph}.mmd (install pygraphviz for PNG)", file=sys.stderr)
        sys.exit(0)

    if args.mock and args.live:
        print("Error: --mock and --live are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    ir_data = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    task_name = Path(args.task_file).stem

    mock = live = None
    if args.mock:
        mock = MockRunner(args.mock, task_name)
    elif args.live:
        live = LiveRunner(verbose=args.verbose)

    result = run_pipeline(ir_data, mock_runner=mock, agent_runner=live, verbose=args.verbose)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.save:
        save_dir = Path(args.save)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"{task_name}_result.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Result saved to {out_path}", file=sys.stderr)

    # Exit code reflects pipeline outcome
    verdict = result.get("verdict")
    if verdict in ("cfl", "non_cfl"):
        sys.exit(0)
    elif verdict == "inconclusive":
        sys.exit(2)
    else:  # failure / None / etc.
        sys.exit(1)
