"""
DCFL pipeline orchestrator -- LangGraph StateGraph implementation.

Implements the pipeline graph from S3.3 of the DCFL spec using LangGraph
with fan-out/fan-in for parallel specialists, retry cycles, and
fallback heuristic reasoning.

Usage:
    from dcfl_system.orchestrator import run_pipeline, MockRunner
    result = run_pipeline(ir_dict, mock_runner=MockRunner("examples/mock/", "task_dcfl_anbn"))

    # CLI:
    python -m dcfl_system.orchestrator examples/task_dcfl_anbn.json --mock examples/mock/
"""

from __future__ import annotations

import json
import logging
import operator
import os
import sys
import time as _time
from pathlib import Path
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir
from dcfl_system.lib.hypothesis_module import analyze_dcfl_hypothesis
from dcfl_system.lib.pattern_db import match_patterns
from dcfl_system.lib.closure_table import closure_scan
from dcfl_system.lib.word_sampler import sample_words
from dcfl_system.lib.oracle_verifier import verify_agent_results
from dcfl_system.lib.retry_logic import build_retry_plan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 2

DCFL_SPECIALIST_NAMES = (
    "stack_strategy", "closure_reduction", "dcfl_pumping", "shallit", "inh_ambiguity",
)


# ---------------------------------------------------------------------------
# DCFLState
# ---------------------------------------------------------------------------

class DCFLState(TypedDict):
    """Full state flowing through the LangGraph DCFL pipeline."""

    # -- Inputs --
    source_text: str
    task_ir: dict | None
    hypothesis: dict | None
    classifier_hint: dict | None
    preprocess: dict | None
    agent_results: dict                                    # agent_name -> output
    oracle_verification: dict | None
    reasoning: dict | None
    retry_count: int
    retry_plan: dict | None
    solution: dict | None
    error: str | None

    # -- Internal --
    mock_runner: Any
    agent_runner: Any
    verbose: bool
    dispatch: dict                                         # {name: bool}
    agents_to_retry: Any                                   # None = all, list = selective
    specialist_outputs: Annotated[list, operator.add]      # [(name, output)]
    evidence: dict
    errors: Annotated[list, operator.add]
    _specialist_name: str
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
        # Try task-specific file first, then generic
        path = self.mock_dir / f"{self.task_name}_{agent_name}.json"
        if not path.exists():
            path = self.mock_dir / f"{agent_name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Extract JSON object from LLM response text."""
    import re as _re
    text = text.strip()
    if not text:
        return None
    # Strategy 1: whole text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Strategy 2: fenced code block
    fence_match = _re.search(r"```(?:json)?\s*\n(.*?)\n```", text, _re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Strategy 3: first { to last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            obj = json.loads(text[first:last + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# LiveRunner
# ---------------------------------------------------------------------------

class LiveRunner:
    """Run agents via Anthropic API using prompts from prompts/ directory."""

    def __init__(self, api_key: str | None = None, verbose: bool = False):
        from dcfl_system.config import (
            MODELS, TEMPERATURES, MAX_TOKENS, MAX_TOKENS_PER_AGENT,
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
            raise ValueError("ANTHROPIC_API_KEY not set.")

        self.client = anthropic.Anthropic(api_key=resolved_key)
        self.models = MODELS
        self.temperatures = TEMPERATURES
        self.max_tokens = MAX_TOKENS
        self.max_tokens_per_agent = MAX_TOKENS_PER_AGENT
        self.json_retries = LLM_JSON_RETRIES
        self.prompt_files = PROMPT_FILES
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.verbose = verbose
        self._prompt_cache: dict[str, str] = {}
        self._json_repair_model = "claude-haiku-4-5"

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

    def _repair_json_with_haiku(self, agent_name: str, raw_text: str, was_truncated: bool) -> dict | None:
        """Ask Haiku to repair broken JSON from a specialist's output."""
        if not raw_text or not raw_text.strip():
            return None
        truncation_note = (
            "\nNOTE: Response was truncated. Close open braces/brackets, drop last partial field."
            if was_truncated else ""
        )
        system = (
            "You are a JSON repair tool. Extract or fix the JSON object. "
            "Return ONLY valid JSON, nothing else."
        )
        user_msg = f"Agent: {agent_name}\nRepair this:{truncation_note}\n```\n{raw_text}\n```"
        try:
            response = self.client.messages.create(
                model=self._json_repair_model,
                max_tokens=min(len(raw_text) // 2 + 2000, 8000),
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception:
            return None
        repaired = "".join(b.text for b in response.content if hasattr(b, "text"))
        return _extract_json(repaired)

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        try:
            system_prompt = self._load_prompt(agent_name)
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Skipping agent '%s': %s", agent_name, exc)
            return None

        model = self.models.get(agent_name, "claude-sonnet-4-6")
        temperature = self.temperatures.get(agent_name, 0.0)
        max_tokens = self.max_tokens_per_agent.get(agent_name, self.max_tokens)
        user_content = json.dumps(input_data or {}, ensure_ascii=False, indent=2)

        last_error = None
        raw_text = ""
        for attempt in range(1 + self.json_retries):
            if attempt > 0 and last_error:
                user_msg = (
                    f"{user_content}\n\n"
                    f"[RETRY {attempt}/{self.json_retries}] "
                    f"Previous response failed JSON parse: {last_error}\n"
                    f"Output ONLY a single JSON object."
                )
            else:
                user_msg = user_content

            t0 = _time.monotonic()
            raw_text = ""
            stop_reason = None
            try:
                with self.client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                ) as stream:
                    for chunk in stream.text_stream:
                        raw_text += chunk
                    final_msg = stream.get_final_message()
                stop_reason = getattr(final_msg, "stop_reason", None)
                tokens_in = final_msg.usage.input_tokens if final_msg.usage else 0
                tokens_out = final_msg.usage.output_tokens if final_msg.usage else 0
            except Exception as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                return {"agent": agent_name, "status": "agent_error", "verdict": None,
                        "confidence": 0.0, "evidence": {}, "errors": [f"API error: {exc}"]}

            elapsed = _time.monotonic() - t0
            if self.verbose:
                extra = f" stop={stop_reason}" if stop_reason and stop_reason != "end_turn" else ""
                print(f"[{agent_name}] model={model} tokens_in={tokens_in} tokens_out={tokens_out} time={elapsed:.1f}s{extra}",
                      file=sys.stderr, flush=True)

            parsed = _extract_json(raw_text)
            if parsed is not None:
                return parsed

            # Try Haiku repair
            was_truncated = stop_reason == "max_tokens"
            repaired = self._repair_json_with_haiku(agent_name, raw_text, was_truncated)
            if repaired is not None:
                return repaired

            if was_truncated:
                break
            last_error = f"JSON parse failed (length={len(raw_text)})"
            logger.warning("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)

        return {"agent": agent_name, "status": "agent_error", "verdict": None,
                "confidence": 0.0, "evidence": {}, "errors": [last_error or "JSON parse failed"],
                "raw_response": raw_text[:2000]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_runner(state: DCFLState) -> MockRunner | LiveRunner | None:
    return state.get("mock_runner") or state.get("agent_runner")


def _run_agent(state: DCFLState, agent_name: str, input_data: dict | None = None) -> dict | None:
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


def log_msg(state: DCFLState, msg: str) -> None:
    if state.get("verbose"):
        print(f"  [{_time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _build_specialist_input(state: DCFLState, agent_name: str) -> dict:
    """Build the input dict for a specialist agent."""
    inp: dict[str, Any] = {
        "ir": state.get("task_ir") or {},
        "hypothesis": state.get("hypothesis") or {},
        "classifier_hint": state.get("classifier_hint") or {},
        "preprocess": state.get("preprocess") or {},
    }
    # On retry, include hints from retry plan
    retry_plan = state.get("retry_plan") or {}
    hints = retry_plan.get("hints") or {}
    if isinstance(hints, dict) and agent_name in hints:
        inp["retry_hint"] = hints[agent_name]
    return inp


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


def _normalize_verdict(raw: Any) -> str | None:
    """Normalize a verdict string to one of {\"dcfl\", \"non_dcfl\", None}."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("dcfl", "is_dcfl", "deterministic_context_free"):
        return "dcfl"
    if v in ("non_dcfl", "not_dcfl", "nondcfl", "not_deterministic_context_free"):
        return "non_dcfl"
    return None


def _get_action(reasoning_output: dict) -> str:
    """Extract action from reasoning output."""
    return reasoning_output.get("action") or reasoning_output.get("decision") or "done"


_VALID_ACTIONS = {"done", "retry", "fail"}


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def input_parser_node(state: DCFLState) -> dict:
    """Validate IR using dcfl_ir_schema.validate_dcfl_ir."""
    log_msg(state, "input_parser_node...")
    ir = state.get("task_ir")
    if ir is None:
        return {"errors": ["task_ir is None"]}
    errs = validate_dcfl_ir(ir)
    if errs:
        return {"errors": errs, "error": "; ".join(errs)}
    return {}


def hypothesis_module_node(state: DCFLState) -> dict:
    """Analyze hypothesis using dcfl_system.lib.hypothesis_module."""
    log_msg(state, "hypothesis_module_node...")
    ir = state.get("task_ir") or {}
    hyp = analyze_dcfl_hypothesis(ir)
    log_msg(state, f"  hypothesis={hyp.get('prediction')} conf={hyp.get('confidence')}")
    return {"hypothesis": hyp}


def run_classifier_node(state: DCFLState) -> dict:
    """Run classifier agent (advisory only)."""
    log_msg(state, "run_classifier_node (advisory)...")
    classifier_input = {
        "ir": state.get("task_ir") or {},
        "hypothesis": state.get("hypothesis") or {},
        "preprocess": state.get("preprocess") or {},
    }
    output = _run_agent(state, "classifier", classifier_input)
    if output is None or output.get("status") == "agent_error":
        if output is not None:
            log_msg(state, "  classifier returned agent_error, ignoring")
        return {}
    return {"classifier_hint": output}


def preprocess_node(state: DCFLState) -> dict:
    """Run preprocessing: pattern_db, closure_table, word_sampler."""
    log_msg(state, "preprocess_node...")
    ir = state.get("task_ir") or {}

    patterns = match_patterns(ir)
    closure = closure_scan(ir)
    words = sample_words(ir)

    preprocess_result = {
        "patterns": patterns,
        "closure": closure,
        "sample_words": words,
    }
    log_msg(state, f"  patterns={len(patterns)} words={len(words)}")
    return {"preprocess": preprocess_result}


def setup_dispatch_node(state: DCFLState) -> dict:
    """Dispatch all 5 agents (first run) or selected agents (retry)."""
    agents_to_retry = state.get("agents_to_retry")

    if isinstance(agents_to_retry, list) and len(agents_to_retry) > 0:
        valid = [a for a in agents_to_retry if a in DCFL_SPECIALIST_NAMES]
        if valid:
            dispatch = {name: (name in valid) for name in DCFL_SPECIALIST_NAMES}
            log_msg(state, f"  selective dispatch: {valid}")
            return {"dispatch": dispatch}
        log_msg(state, "  agents_to_retry had no valid agents, falling back to all")

    dispatch = {name: True for name in DCFL_SPECIALIST_NAMES}
    log_msg(state, "  full dispatch: all 5 agents")
    return {"dispatch": dispatch}


def dispatch_all_agents_node(state: DCFLState) -> list[Send]:
    """Conditional edge: fan-out to specialist nodes via Send()."""
    dispatch = state.get("dispatch", {})
    dispatched = [k for k, v in dispatch.items() if v]
    if not dispatched:
        return [Send("collect_specialists_node", state)]
    return [
        Send("run_specialist_node", {**state, "_specialist_name": name})
        for name in dispatched
    ]


def run_specialist_node(state: DCFLState) -> dict:
    """Run a single specialist agent. Invoked via Send() fan-out."""
    agent_name = state["_specialist_name"]
    log_msg(state, f"  specialist: {agent_name}...")
    inp = _build_specialist_input(state, agent_name)
    out = _run_agent(state, agent_name, inp)

    if out is not None and out.get("status") == "agent_error":
        err_msgs = out.get("errors") or [f"{agent_name} returned agent_error"]
        log_msg(state, f"  {agent_name} returned agent_error: {err_msgs}")
        return {
            "specialist_outputs": [(agent_name, None)],
            "errors": [f"{agent_name}: {m}" for m in err_msgs],
        }

    return {"specialist_outputs": [(agent_name, out)]}


def collect_specialists_node(state: DCFLState) -> dict:
    """Fan-in: merge specialist_outputs into agent_results."""
    agent_results = dict(state.get("agent_results", {}))

    dispatched = {k for k, v in state.get("dispatch", {}).items() if v}

    # Keep only the latest entry per agent from the current round
    latest_this_round: dict[str, Any] = {}
    for name, out in state.get("specialist_outputs", []):
        if name in dispatched:
            latest_this_round[name] = out

    for name, out in latest_this_round.items():
        if out is None:
            agent_results.pop(name, None)
        else:
            agent_results[name] = out

    evidence = dict(state.get("evidence", {}))
    for name in DCFL_SPECIALIST_NAMES:
        if name in agent_results:
            evidence[name] = agent_results[name]
        else:
            evidence.pop(name, None)

    log_msg(state, f"  collected specialists (round): {sorted(latest_this_round.keys())}")

    return {"agent_results": agent_results, "evidence": evidence}


def oracle_verification_node(state: DCFLState) -> dict:
    """Verify agent results using oracle_verifier."""
    log_msg(state, "oracle_verification_node...")
    ir = state.get("task_ir") or {}
    agent_results = state.get("agent_results") or {}

    verification = verify_agent_results(agent_results, ir)
    log_msg(state, f"  verified {len(verification)} agents")
    return {"oracle_verification": verification}


def reasoning_agent_node(state: DCFLState) -> dict:
    """Run reasoning agent, with fallback to heuristic reasoning."""
    log_msg(state, "reasoning_agent_node...")

    reasoning_input = {
        "ir": state.get("task_ir") or {},
        "hypothesis": state.get("hypothesis") or {},
        "classifier_hint": state.get("classifier_hint") or {},
        "agent_results": state.get("agent_results") or {},
        "oracle_verification": state.get("oracle_verification") or {},
        "retry_count": state.get("retry_count", 0),
        "max_retries": MAX_RETRIES,
    }
    output = _run_agent(state, "reasoning", reasoning_input)

    # Normalize verdict
    if isinstance(output, dict) and "verdict" in output:
        raw_verdict = output.get("verdict")
        normalized = _normalize_verdict(raw_verdict)
        if raw_verdict is not None and normalized != raw_verdict:
            log_msg(state, f"  normalized verdict {raw_verdict!r} -> {normalized!r}")
        output["verdict"] = normalized

    # Detect invalid outputs requiring fallback
    needs_fallback = False
    if output is None:
        needs_fallback = True
    elif output.get("status") == "agent_error":
        needs_fallback = True
    elif not output.get("action") and not output.get("decision"):
        needs_fallback = True
    else:
        act = _get_action(output)
        if act not in _VALID_ACTIONS:
            log_msg(state, f"  reasoning returned unknown action={act!r}, using fallback")
            needs_fallback = True
        elif act == "done" and not output.get("verdict"):
            needs_fallback = True

    if needs_fallback:
        log_msg(state, "  reasoning unavailable/invalid, using fallback")
        output = _fallback_reasoning(state)

    action = _get_action(output)
    log_msg(state, f"  action={action} verdict={output.get('verdict')}")
    return {"reasoning": output}


def _fallback_reasoning_result(
    action: str,
    verdict: str | None,
    confidence: float,
    summary: str,
    primary_evidence: str = "",
    retry_plan: dict | None = None,
) -> dict:
    """Build a contract-compliant reasoning output for the fallback path."""
    normalized_verdict = _normalize_verdict(verdict)
    if verdict and normalized_verdict is None:
        confidence = 0.0
    return {
        "agent": "reasoning",
        "action": action,
        "decision": action,
        "verdict": normalized_verdict,
        "confidence": _clamp_confidence(confidence),
        "primary_evidence": primary_evidence,
        "summary": summary,
        "retry_plan": retry_plan,
        "hints_for_human": ["Fallback reasoning (no LLM available)"],
        "errors": [],
    }


def _fallback_reasoning(state: DCFLState) -> dict:
    """Heuristic reasoning when no reasoning agent is available.

    Picks the highest-confidence agent result as primary evidence.
    """
    hypothesis = state.get("hypothesis") or {}
    agent_results = state.get("agent_results") or {}
    oracle_verification = state.get("oracle_verification") or {}
    retry_count = state.get("retry_count", 0)

    # Check for refuted claims
    refuted_agents = [
        name for name, v in oracle_verification.items()
        if isinstance(v, dict) and v.get("verification_status") == "refuted"
    ]

    if refuted_agents and retry_count < MAX_RETRIES:
        return _fallback_reasoning_result(
            action="retry", verdict=None, confidence=0.25,
            summary=f"Claims refuted for {refuted_agents}; retrying",
            retry_plan={"agents_to_retry": refuted_agents, "hints": {}},
        )

    # Pick highest-confidence agent result
    best_agent: str | None = None
    best_verdict: str | None = None
    best_conf = 0.0
    refuted_set = set(refuted_agents)
    for name, output in agent_results.items():
        if name in refuted_set:
            continue
        if not isinstance(output, dict):
            continue
        verdict = output.get("verdict")
        if verdict not in ("dcfl", "non_dcfl"):
            continue
        conf = _clamp_confidence(output.get("confidence", 0.5))
        if conf > best_conf:
            best_conf = conf
            best_verdict = verdict
            best_agent = name

    if best_verdict is not None and best_conf >= 0.6:
        return _fallback_reasoning_result(
            action="done", verdict=best_verdict, confidence=best_conf,
            summary=f"Agent '{best_agent}' verdict: {best_verdict}",
            primary_evidence=best_agent or "",
        )

    # Check hypothesis prediction
    hyp_prediction = hypothesis.get("prediction", "uncertain")
    hyp_conf = _clamp_confidence(hypothesis.get("confidence", 0.0))
    if hyp_prediction in ("likely_dcfl", "likely_non_dcfl") and hyp_conf >= 0.7:
        mapped_verdict = "dcfl" if "dcfl" in hyp_prediction and "non" not in hyp_prediction else "non_dcfl"
        return _fallback_reasoning_result(
            action="done", verdict=mapped_verdict, confidence=hyp_conf,
            summary=f"Hypothesis prediction: {hyp_prediction}",
            primary_evidence="hypothesis",
        )

    if retry_count < MAX_RETRIES:
        return _fallback_reasoning_result(
            action="retry", verdict=None, confidence=0.2,
            summary="Insufficient evidence, retrying",
        )

    # Terminal: no retries left
    final_verdict = best_verdict
    return _fallback_reasoning_result(
        action="done",
        verdict=final_verdict,
        confidence=max(best_conf, 0.3) if final_verdict else 0.0,
        summary="Max retries reached, returning best guess",
        primary_evidence=best_agent or "",
    )


def decide_after_reasoning(state: DCFLState) -> str:
    """Conditional edge after reasoning: done / retry / fail."""
    reasoning = state.get("reasoning") or {}
    action = _get_action(reasoning)
    retry_count = state.get("retry_count", 0)

    if action == "done":
        return "done"
    if action == "retry" and retry_count < MAX_RETRIES:
        return "retry"
    return "fail"


def retry_planner_node(state: DCFLState) -> dict:
    """Build retry plan using dcfl_system.lib.retry_logic."""
    log_msg(state, "retry_planner_node...")
    reasoning = state.get("reasoning") or {}
    agent_results = state.get("agent_results") or {}
    oracle_verification = state.get("oracle_verification") or {}
    retry_count = state.get("retry_count", 0)

    plan = build_retry_plan(agent_results, oracle_verification, retry_count)

    # Also check reasoning for its own retry_plan override
    reasoning_plan = reasoning.get("retry_plan") or {}
    if isinstance(reasoning_plan, dict) and reasoning_plan.get("agents_to_retry"):
        # Merge: prefer reasoning agent's selection if available
        r_agents = reasoning_plan.get("agents_to_retry", [])
        if isinstance(r_agents, list) and r_agents:
            valid = [a for a in r_agents if a in DCFL_SPECIALIST_NAMES]
            if valid:
                plan["agents_to_retry"] = valid
                # Merge hints
                r_hints = reasoning_plan.get("hints") or {}
                if isinstance(r_hints, dict):
                    merged_hints = dict(plan.get("hints", {}))
                    merged_hints.update(r_hints)
                    plan["hints"] = merged_hints

    agents_to_retry = plan.get("agents_to_retry", [])
    next_count = retry_count + 1

    log_msg(
        state,
        f"  retry {next_count}, agents={agents_to_retry}, "
        f"needs_retry={plan.get('needs_retry')}",
    )

    return {
        "agents_to_retry": agents_to_retry,
        "retry_plan": plan,
        "retry_count": next_count,
    }


def renderer_node(state: DCFLState) -> dict:
    """Assemble final DCFLSolutionOutput dict."""
    return assemble_result_node(state)


def assemble_result_node(state: DCFLState) -> dict:
    """Assemble the final result dict."""
    ir = state.get("task_ir") or {}
    reasoning = state.get("reasoning") or {}
    agent_results = state.get("agent_results") or {}
    oracle_verification = state.get("oracle_verification") or {}

    raw_verdict = reasoning.get("verdict")
    normalized = _normalize_verdict(raw_verdict)
    verdict = normalized or "inconclusive"

    if raw_verdict and normalized is None:
        confidence = 0.0
    else:
        confidence = _clamp_confidence(reasoning.get("confidence"))

    errors = list(state.get("errors", []))

    # Build specialist outputs for the result
    specialist_outputs_out: dict[str, dict] = {}
    for name in DCFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
            specialist_outputs_out[name] = out

    hints = reasoning.get("hints_for_human") or []
    if not isinstance(hints, list):
        hints = []

    # Extract proof info from the winning specialist for renderer compatibility
    primary_ev = reasoning.get("primary_evidence") or ""
    proof_method = primary_ev if primary_ev and primary_ev != "hypothesis" else None
    proof_sketch = None
    proof_text = None
    if primary_ev and primary_ev in specialist_outputs_out:
        winner = specialist_outputs_out[primary_ev]
        proof_sketch = winner.get("proof_sketch")
        proof_text = winner.get("proof_text")
    # Only use reasoning summary as proof_text when there's no structured proof_sketch
    if not proof_text and not proof_sketch:
        proof_text = reasoning.get("summary")

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": verdict,
            "confidence": confidence,
            "hypothesis": state.get("hypothesis"),
            "classifier_hint": state.get("classifier_hint"),
            "preprocess": state.get("preprocess"),
            "reasoning_summary": reasoning.get("summary"),
            "primary_evidence": primary_ev,
            "proof_method": proof_method,
            "proof_sketch": proof_sketch,
            "proof_text": proof_text,
            "oracle_verification": oracle_verification,
            "agents_used": sorted(agent_results.keys()),
            "specialist_outputs": specialist_outputs_out,
            "agent_results": specialist_outputs_out,
            "hints_for_human": hints,
            "retries": state.get("retry_count", 0),
            "errors": errors,
        },
        "solution": {
            "verdict": verdict,
            "confidence": confidence,
            "summary": reasoning.get("summary"),
        },
    }


def early_failure_node(state: DCFLState) -> dict:
    """Return error result on early failure."""
    ir = state.get("task_ir") or {}
    errors = list(state.get("errors", []))
    reasoning = state.get("reasoning") or {}

    reasoning_errors = reasoning.get("errors") or []
    if isinstance(reasoning_errors, list):
        errors.extend(str(e) for e in reasoning_errors)

    if errors:
        detail = "; ".join(errors)
    else:
        detail = reasoning.get("summary") or "Pipeline failed"

    agent_results = state.get("agent_results") or {}
    specialist_outputs_out: dict[str, dict] = {}
    for name in DCFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
            specialist_outputs_out[name] = out

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "failure" if errors else "inconclusive",
            "confidence": 0.0,
            "hypothesis": state.get("hypothesis"),
            "classifier_hint": state.get("classifier_hint"),
            "preprocess": state.get("preprocess"),
            "reasoning_summary": None,
            "primary_evidence": None,
            "proof_method": None,
            "proof_sketch": None,
            "proof_text": None,
            "oracle_verification": state.get("oracle_verification"),
            "agents_used": sorted(agent_results.keys()),
            "specialist_outputs": specialist_outputs_out,
            "agent_results": specialist_outputs_out,
            "hints_for_human": [],
            "retries": state.get("retry_count", 0),
            "errors": errors if errors else [detail],
        },
        "solution": None,
        "error": detail,
    }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_dcfl_pipeline_graph() -> Any:
    """Build and compile the DCFL LangGraph pipeline.

    Graph structure:
        START -> input_parser -> [ok] -> hypothesis_module -> classifier
              -> preprocess -> setup_dispatch -> (fan-out 5 specialists)
              -> collect_specialists -> oracle_verify -> reasoning
              -> [done] -> renderer -> END
              -> [retry] -> retry_planner -> setup_dispatch (loop)
              -> [fail] -> early_failure -> END
        input_parser -> [fail] -> early_failure -> END
    """
    graph = StateGraph(DCFLState)

    # -- Register nodes --
    graph.add_node("input_parser_node", input_parser_node)
    graph.add_node("early_failure_node", early_failure_node)
    graph.add_node("hypothesis_module_node", hypothesis_module_node)
    graph.add_node("run_classifier_node", run_classifier_node)
    graph.add_node("preprocess_node", preprocess_node)
    graph.add_node("setup_dispatch_node", setup_dispatch_node)
    graph.add_node("run_specialist_node", run_specialist_node)
    graph.add_node("collect_specialists_node", collect_specialists_node)
    graph.add_node("oracle_verification_node", oracle_verification_node)
    graph.add_node("reasoning_agent_node", reasoning_agent_node)
    graph.add_node("retry_planner_node", retry_planner_node)
    graph.add_node("renderer_node", renderer_node)

    # -- Edges --

    # START -> validate
    graph.add_edge(START, "input_parser_node")

    # input_parser -> ok/fail
    graph.add_conditional_edges(
        "input_parser_node",
        lambda state: "fail" if state.get("errors") else "ok",
        {"fail": "early_failure_node", "ok": "hypothesis_module_node"},
    )
    graph.add_edge("early_failure_node", END)

    # Analysis chain
    graph.add_edge("hypothesis_module_node", "run_classifier_node")
    graph.add_edge("run_classifier_node", "preprocess_node")
    graph.add_edge("preprocess_node", "setup_dispatch_node")

    # Fan-out: dispatch -> specialists via Send()
    graph.add_conditional_edges(
        "setup_dispatch_node",
        dispatch_all_agents_node,
        ["run_specialist_node", "collect_specialists_node"],
    )

    # Fan-in: specialist -> collect
    graph.add_edge("run_specialist_node", "collect_specialists_node")

    # Verification chain
    graph.add_edge("collect_specialists_node", "oracle_verification_node")
    graph.add_edge("oracle_verification_node", "reasoning_agent_node")

    # Decision: done / retry / fail
    graph.add_conditional_edges(
        "reasoning_agent_node",
        decide_after_reasoning,
        {
            "done": "renderer_node",
            "retry": "retry_planner_node",
            "fail": "early_failure_node",
        },
    )

    # Retry planner -> back to dispatch (loop)
    graph.add_edge("retry_planner_node", "setup_dispatch_node")

    # Final
    graph.add_edge("renderer_node", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_pipeline(
    ir: dict,
    mock_runner: MockRunner | None = None,
    agent_runner: Any | None = None,
    verbose: bool = False,
) -> dict:
    """Run the full DCFL pipeline and return the result dict.

    Builds the LangGraph StateGraph, constructs the initial state,
    invokes the graph, and extracts the result.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO)

    graph = build_dcfl_pipeline_graph()

    initial_state: dict[str, Any] = {
        "source_text": ir.get("source_text", ""),
        "task_ir": ir,
        "hypothesis": None,
        "classifier_hint": None,
        "preprocess": None,
        "agent_results": {},
        "oracle_verification": None,
        "reasoning": None,
        "retry_count": 0,
        "retry_plan": None,
        "solution": None,
        "error": None,
        # Internal
        "mock_runner": mock_runner,
        "agent_runner": agent_runner,
        "verbose": verbose,
        "dispatch": {},
        "agents_to_retry": None,
        "specialist_outputs": [],
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
            "agents_used": [],
            "retries": 0,
            "errors": ["Graph produced no result"],
        }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the DCFL analysis pipeline")
    parser.add_argument("task_file", help="Path to DCFL IR JSON file")
    parser.add_argument("--mock", help="Mock directory for agent outputs")
    parser.add_argument("--live", action="store_true", help="Use live LLM agents")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--render",
        choices=["json", "md", "html", "all"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument("--output-dir", help="Output directory for rendered files")
    args = parser.parse_args()

    if args.mock and args.live:
        print("Error: --mock and --live are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    ir_data = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    task_name = Path(args.task_file).stem

    # Also try to extract task_id from IR for MockRunner
    task_id = ir_data.get("task_id", task_name)

    mock = agent = None
    if args.mock:
        mock = MockRunner(args.mock, task_id)
    if args.live:
        agent = LiveRunner(verbose=args.verbose)

    result = run_pipeline(
        ir_data,
        mock_runner=mock,
        agent_runner=agent,
        verbose=args.verbose,
    )

    # Determine output directory
    output_dir = Path(args.output_dir) if args.output_dir else Path("examples/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Always print JSON to stdout and save all 3 formats when --render is set
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result_json = json.dumps(result, indent=2, ensure_ascii=False)
    print(result_json)

    # Always save JSON
    out_path = output_dir / f"{task_name}.json"
    out_path.write_text(result_json, encoding="utf-8")
    print(f"Saved: {out_path}", file=sys.stderr)

    # Always save MD
    try:
        from dcfl_system.renderer import render_markdown
        md_text = render_markdown(result)
        md_path = output_dir / f"{task_name}.md"
        md_path.write_text(md_text, encoding="utf-8")
        print(f"Saved: {md_path}", file=sys.stderr)
    except ImportError:
        print("Warning: dcfl_system.renderer not available for MD output", file=sys.stderr)
    except Exception as exc:
        print(f"MD render failed: {exc}", file=sys.stderr)

    # Always save HTML
    try:
        from dcfl_system.renderer import render_html
        html_text = render_html(result)
        html_path = output_dir / f"{task_name}.html"
        html_path.write_text(html_text, encoding="utf-8")
        print(f"Saved: {html_path}", file=sys.stderr)
    except ImportError:
        print("Warning: dcfl_system.renderer not available for HTML output", file=sys.stderr)
    except Exception as exc:
        print(f"HTML render failed: {exc}", file=sys.stderr)

    # Exit code reflects pipeline outcome
    verdict = result.get("verdict")
    if verdict in ("dcfl", "non_dcfl"):
        sys.exit(0)
    elif verdict == "inconclusive":
        sys.exit(2)
    else:  # failure / None / etc.
        sys.exit(1)
