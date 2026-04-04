"""
CFL pipeline orchestrator — sequential execution of the CFL analysis pipeline.

Implements the pipeline graph from the CFL spec as a sequential loop with
retry and inversion support. All node functions are standalone (ready for
future LangGraph migration) but executed sequentially here.

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
import sys
import time as _time
from pathlib import Path
from typing import Any

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
FORMALIZATION_ENABLED = False  # Phase 3

CFL_SPECIALIST_NAMES = (
    "cfg_builder", "pda_builder", "decomposition", "parikh",
    "pumping_cfl", "ogden", "closure_reduction", "interchange", "morphism",
)

# Agents whose output may contain a grammar or PDA to oracle-test
_CONSTRUCTIVE_AGENTS = {"cfg_builder", "pda_builder"}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

class MockRunner:
    """Load agent outputs from JSON files in a mock directory."""

    def __init__(self, mock_dir: str, task_name: str):
        self.mock_dir = Path(mock_dir)
        self.task_name = task_name

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        # Try task-specific mock first: {task_name}_{agent_name}.json
        path = self.mock_dir / f"{self.task_name}_{agent_name}.json"
        if not path.exists():
            # Try generic: {agent_name}.json
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

        resolved_key = api_key or ANTHROPIC_API_KEY
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
        # Cache loaded prompts
        self._prompt_cache: dict[str, str] = {}

    def _load_prompt(self, agent_name: str) -> str:
        """Load system prompt from prompts/ directory."""
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

    def run_agent(
        self, agent_name: str, input_data: dict | None = None,
    ) -> dict | None:
        """Call the Anthropic API for a given agent.

        Reads the prompt from prompts/{agent}.md, sends the input as JSON
        in the user message, parses the JSON response, retries on parse errors.
        """
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
                # Retry: append parse error to user message
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
                logger.error(
                    "[%s] model=%s API error after %.1fs: %s",
                    agent_name, model, elapsed, exc,
                )
                return None

            elapsed = _time.monotonic() - t0
            # Extract text from response
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
                    file=sys.stderr,
                    flush=True,
                )

            # Parse JSON from response
            parsed = _extract_json(raw_text)
            if parsed is not None:
                return parsed

            last_error = f"Could not parse JSON from response (length={len(raw_text)})"
            logger.warning(
                "[%s] attempt %d: %s", agent_name, attempt + 1, last_error,
            )

        # All retries exhausted
        logger.error("[%s] JSON parse failed after %d attempts", agent_name, 1 + self.json_retries)
        return {
            "agent": agent_name,
            "status": "agent_error",
            "verdict": None,
            "confidence": 0.0,
            "evidence": {},
            "errors": [f"Failed to parse JSON after {1 + self.json_retries} attempts"],
            "raw_response": raw_text[:2000],
        }


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from LLM response text.

    Handles: raw JSON, markdown code fences, leading/trailing text.
    """
    text = text.strip()

    # Try direct parse first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    import re
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try finding first { ... last }
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
# Helper: get the active runner
# ---------------------------------------------------------------------------

def _get_runner(state: dict) -> MockRunner | LiveRunner | None:
    return state.get("mock_runner") or state.get("agent_runner")


def _run_agent(state: dict, agent_name: str, input_data: dict | None = None) -> dict | None:
    """Run an agent through the active runner. Returns output dict or None."""
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


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def validate_ir_node(state: dict) -> dict:
    """Validate the IR and return errors if any."""
    ir = state["ir"]
    errs = validate_cfl_ir(ir)
    if errs:
        return {"errors": errs}
    return {}


def analyze_hypothesis_node(state: dict) -> dict:
    """Analyze the IR to produce a hypothesis (cfl / non_cfl / unknown)."""
    ir = state["ir"]
    hyp = analyze_cfl_hypothesis(ir)
    return {"hypothesis": hyp}


def run_classifier_node(state: dict) -> dict:
    """Run the classifier agent (advisory only). Output goes to evidence."""
    output = _run_agent(state, "classifier")
    result: dict[str, Any] = {}
    if output is not None:
        result["classifier_output"] = output
        evidence = dict(state.get("evidence", {}))
        evidence["classifier"] = output
        result["evidence"] = evidence
    return result


def language_preprocess_node(state: dict) -> dict:
    """Run language preprocessing (filter analysis, Parikh, bounded check)."""
    ir = state["ir"]
    pp = preprocess_language(ir)
    return {"preprocess_output": pp}


def setup_dispatch_node(state: dict) -> dict:
    """Decide which specialist agents to dispatch.

    On first run (agents_to_retry is None): dispatch ALL 9 agents.
    On selective retry: dispatch only the agents in agents_to_retry.
    """
    agents_to_retry = state.get("agents_to_retry")

    if agents_to_retry is not None:
        # Selective retry
        dispatch = {name: (name in agents_to_retry) for name in CFL_SPECIALIST_NAMES}
    else:
        # First run — dispatch all
        dispatch = {name: True for name in CFL_SPECIALIST_NAMES}

    return {"dispatch": dispatch}


def run_specialists(state: dict) -> dict:
    """Run all dispatched specialist agents and collect outputs."""
    dispatch = state.get("dispatch", {})
    specialist_outputs: list[tuple[str, dict]] = []

    for name, should_run in dispatch.items():
        if not should_run:
            continue
        output = _run_agent(state, name)
        if output is not None:
            specialist_outputs.append((name, output))
        elif state.get("verbose"):
            logger.info("Specialist '%s' returned no output", name)

    return {"specialist_outputs": specialist_outputs}


def collect_specialists_node(state: dict) -> dict:
    """Merge new specialist outputs with accumulated results from previous rounds."""
    agent_results = dict(state.get("agent_results", {}))

    # Merge new outputs (overwrite old for retried agents)
    for name, output in state.get("specialist_outputs", []):
        agent_results[name] = output

    # Update evidence
    evidence = dict(state.get("evidence", {}))
    for name, output in agent_results.items():
        evidence[name] = output

    return {
        "agent_results": agent_results,
        "evidence": evidence,
    }


def build_oracle_node(state: dict) -> dict:
    """Build the oracle function from IR. Only on retry_round == 0."""
    if state.get("retry_round", 0) != 0 and state.get("oracle_fn") is not None:
        return {}

    ir = state["ir"]
    try:
        oracle_fn = cfl_oracle_from_ir(ir)
        return {"oracle_fn": oracle_fn, "oracle_ok": True}
    except Exception as exc:
        logger.warning("Could not build oracle: %s", exc)
        return {"oracle_fn": None, "oracle_ok": False}


def verify_claims_node(state: dict) -> dict:
    """Verify claims from each agent result using the claim verifier."""
    ir = state["ir"]
    agent_results = state.get("agent_results", {})
    verifications: dict[str, dict] = {}

    for name, output in agent_results.items():
        # Build an agent_output-shaped dict for the verifier
        agent_output = {
            "agent": name,
            "status": output.get("status", "success"),
            "evidence": output.get("evidence", output),
        }
        verifications[name] = verify_agent_claims(agent_output, ir)

    return {"claim_verification": verifications}


def oracle_test_node(state: dict) -> dict:
    """Test constructive agent outputs (grammar/PDA) against the oracle."""
    ir = state["ir"]
    agent_results = state.get("agent_results", {})
    oracle_fn = state.get("oracle_fn")

    if not oracle_fn:
        return {"oracle_test_result": {"status": "skipped", "details": "no oracle available"}}

    # Collect grammars and PDAs from constructive agents
    constructive_evidence: dict[str, Any] = {}
    for name in _CONSTRUCTIVE_AGENTS:
        output = agent_results.get(name)
        if output is None:
            continue
        # Look for grammar or pda in the output
        if "grammar" in output:
            constructive_evidence["grammar"] = output["grammar"]
        if "pda" in output:
            constructive_evidence["pda"] = output["pda"]
        # Also check nested evidence
        ev = output.get("evidence", {})
        if isinstance(ev, dict):
            if "grammar" in ev:
                constructive_evidence["grammar"] = ev["grammar"]
            if "pda" in ev:
                constructive_evidence["pda"] = ev["pda"]

    if not constructive_evidence:
        return {"oracle_test_result": {"status": "skipped", "details": "no grammar or PDA to test"}}

    try:
        result = oracle_test(constructive_evidence, ir)
        return {"oracle_test_result": result}
    except Exception as exc:
        logger.warning("Oracle test failed: %s", exc)
        return {"oracle_test_result": {"status": "error", "details": str(exc)}}


def run_proof_checker_node(state: dict) -> dict:
    """Run the proof checker agent."""
    output = _run_agent(state, "proof_checker")
    return {"proof_checker_output": output or {}}


def run_reasoning_node(state: dict) -> dict:
    """Run the reasoning agent. Returns decision: done/retry/invert."""
    output = _run_agent(state, "reasoning")
    if output is None:
        # No reasoning agent available — decide based on available evidence
        return {"reasoning_output": _fallback_reasoning(state)}
    return {"reasoning_output": output}


def _fallback_reasoning(state: dict) -> dict:
    """Heuristic reasoning when no reasoning agent is available.

    Uses hypothesis, preprocess quick verdict, oracle test results,
    and claim verification to produce a decision.
    """
    hypothesis = state.get("hypothesis", {})
    preprocess = state.get("preprocess_output", {})
    oracle_result = state.get("oracle_test_result", {})
    verifications = state.get("claim_verification", {})
    agent_results = state.get("agent_results", {})

    # Quick verdict from preprocessing is authoritative
    quick_verdict = preprocess.get("quick_verdict")
    if quick_verdict:
        return {
            "action": "done",
            "verdict": quick_verdict,
            "confidence": 0.9,
            "reasoning": preprocess.get("quick_verdict_reason", "Quick verdict from preprocessing"),
        }

    # Count verified vs refuted claims
    verified_count = sum(
        1 for v in verifications.values() if v.get("verification_status") == "verified"
    )
    refuted_count = sum(
        1 for v in verifications.values() if v.get("verification_status") == "refuted"
    )

    # Oracle test: if grammar/PDA passed, that's strong evidence for CFL
    if oracle_result.get("status") == "pass":
        return {
            "action": "done",
            "verdict": "cfl",
            "confidence": 0.85,
            "reasoning": "Constructive grammar/PDA passes oracle test",
        }

    # If we have strong hypothesis and some verification
    hyp = hypothesis.get("hypothesis", "unknown")
    hyp_conf = hypothesis.get("confidence", 0.0)

    if hyp in ("cfl", "non_cfl") and hyp_conf >= 0.7 and verified_count > 0:
        return {
            "action": "done",
            "verdict": hyp,
            "confidence": min(hyp_conf + 0.05 * verified_count, 0.95),
            "reasoning": f"Hypothesis '{hyp}' (conf={hyp_conf}) with {verified_count} verified claims",
        }

    # If any agent returned a verdict
    for name, output in agent_results.items():
        verdict = output.get("verdict")
        if verdict in ("cfl", "non_cfl"):
            conf = output.get("confidence", 0.5)
            if conf >= 0.7:
                return {
                    "action": "done",
                    "verdict": verdict,
                    "confidence": conf,
                    "reasoning": f"Agent '{name}' verdict: {verdict} (conf={conf})",
                }

    # Not enough evidence — retry if possible
    retry_round = state.get("retry_round", 0)
    if retry_round < MAX_RETRIES:
        return {
            "action": "retry",
            "reasoning": "Insufficient evidence, retrying with all agents",
        }

    # Give up with best guess
    return {
        "action": "done",
        "verdict": hyp if hyp != "unknown" else "inconclusive",
        "confidence": max(hyp_conf, 0.3),
        "reasoning": "Max retries reached, returning best guess from hypothesis",
    }


def decide_retry(state: dict) -> str:
    """Decide next action based on reasoning output.

    Returns: "done", "retry", "invert", or "fail".
    """
    reasoning = state.get("reasoning_output", {})
    action = reasoning.get("action", "done")
    retry_round = state.get("retry_round", 0)
    inversions_done = state.get("inversions_done", 0)

    if action == "done":
        return "done"

    if action == "retry" and retry_round < MAX_RETRIES:
        return "retry"

    if action == "invert" and inversions_done < MAX_INVERSIONS:
        return "invert"

    # Exhausted retries/inversions
    if retry_round >= MAX_RETRIES:
        return "fail"

    return "done"


def run_retry_planner_node(state: dict) -> dict:
    """Run retry planner to decide which agents to re-run."""
    output = _run_agent(state, "retry_planner")

    if output is not None:
        agents_to_retry = output.get("agents_to_retry")
        retry_params = output.get("retry_params", {})
    else:
        # No retry planner available — retry all agents
        agents_to_retry = None
        retry_params = {}

    return {
        "agents_to_retry": agents_to_retry,
        "retry_params": retry_params,
        "retry_context": state.get("reasoning_output", {}),
    }


def invert_hypothesis_node(state: dict) -> dict:
    """Flip hypothesis cfl <-> non_cfl and reset dispatch to all agents."""
    hypothesis = dict(state.get("hypothesis", {}))
    current = hypothesis.get("hypothesis", "unknown")

    if current == "cfl":
        hypothesis["hypothesis"] = "non_cfl"
    elif current == "non_cfl":
        hypothesis["hypothesis"] = "cfl"
    else:
        hypothesis["hypothesis"] = "non_cfl"  # default inversion

    hypothesis["reasoning"] = f"Inverted from '{current}' after inconclusive results"
    inversions_done = state.get("inversions_done", 0) + 1

    return {
        "hypothesis": hypothesis,
        "inversions_done": inversions_done,
        "agents_to_retry": None,  # re-dispatch all agents
    }


def formalize_node(state: dict) -> dict:
    """Run formalizer agent to produce a structured Markdown proof.

    Lean 4 formalization is out of scope — formalizer generates
    exam-ready Markdown with logical steps instead.
    """
    runner = _get_runner(state)
    if runner is None:
        return {}

    reasoning = state.get("reasoning_output", {})
    verdict = reasoning.get("verdict")
    if not verdict or verdict == "inconclusive":
        return {}

    # Build formalizer input
    agent_results = state.get("agent_results", {})
    primary = reasoning.get("primary_evidence", "")
    specialist_output = agent_results.get(primary, {})

    formalizer_input = {
        "ir": state["ir"],
        "reasoning_output": reasoning,
        "specialist_output": specialist_output,
    }

    output = _run_agent(state, "formalizer", formalizer_input)
    if output is None:
        return {}

    evidence = dict(state.get("evidence", {}))
    evidence["formalizer"] = output

    # If the formalizer produced a proof, update the proof in state
    proof = output.get("proof_document") or output.get("markdown")
    if proof:
        evidence["formatted_proof"] = proof

    return {"evidence": evidence}


def assemble_result_node(state: dict) -> dict:
    """Build the final result dict from pipeline state."""
    ir = state["ir"]
    reasoning = state.get("reasoning_output", {})
    oracle_result = state.get("oracle_test_result", {})
    agent_results = state.get("agent_results", {})
    proof_checker = state.get("proof_checker_output", {})

    verdict = reasoning.get("verdict", "inconclusive")
    confidence = reasoning.get("confidence", 0.0)

    # Extract grammar and PDA from constructive agents
    grammar = None
    pda = None
    for name in _CONSTRUCTIVE_AGENTS:
        output = agent_results.get(name, {})
        if not grammar:
            grammar = output.get("grammar") or (output.get("evidence", {}) or {}).get("grammar")
        if not pda:
            pda = output.get("pda") or (output.get("evidence", {}) or {}).get("pda")

    # Extract proof dict
    proof = None
    if proof_checker and proof_checker.get("proof"):
        proof = proof_checker["proof"]
    elif reasoning.get("proof"):
        proof = reasoning["proof"]

    result = {
        "task": ir.get("task_type"),
        "source_text": ir.get("source_text"),
        "verdict": verdict,
        "confidence": confidence,
        "proof": proof,
        "grammar": grammar,
        "pda": pda,
        "oracle_test": oracle_result if oracle_result else None,
        "agents_used": sorted(agent_results.keys()),
        "retries": state.get("retry_round", 0),
    }

    return {"result": result}


def assemble_early_failure(state: dict) -> dict:
    """Build a failure/inconclusive result."""
    ir = state.get("ir", {})
    errors = state.get("errors", [])
    reasoning = state.get("reasoning_output", {})
    retry_round = state.get("retry_round", 0)

    # If there are validation errors, it's a hard failure
    if errors:
        status = "failure"
        detail = "; ".join(errors)
    else:
        status = "inconclusive"
        detail = reasoning.get("reasoning", "Max retries exhausted without conclusive result")

    result = {
        "task": ir.get("task_type"),
        "source_text": ir.get("source_text"),
        "verdict": status,
        "confidence": 0.0,
        "proof": None,
        "grammar": None,
        "pda": None,
        "oracle_test": None,
        "agents_used": sorted(state.get("agent_results", {}).keys()),
        "retries": retry_round,
        "errors": errors if errors else [detail],
    }

    return {"result": result}


# ---------------------------------------------------------------------------
# Sequential pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(
    ir: dict,
    mock_runner: MockRunner | None = None,
    agent_runner: LiveRunner | None = None,
    verbose: bool = False,
) -> dict:
    """Run the full CFL pipeline and return a result dict.

    Executes nodes sequentially with a retry loop. All node functions
    are standalone and can be migrated to LangGraph StateGraph later.

    Parameters
    ----------
    ir : dict
        CFL intermediate representation (validated by validate_cfl_ir).
    mock_runner : MockRunner, optional
        Load agent outputs from JSON files.
    agent_runner : LiveRunner, optional
        Live LLM runner (Phase 4).
    verbose : bool
        Log diagnostic messages.

    Returns
    -------
    dict
        Result with verdict, confidence, proof, grammar, pda, etc.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO)

    # Initialize state
    state: dict[str, Any] = {
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
        "result": {},
    }

    # Step 1: validate IR
    state.update(validate_ir_node(state))
    if state.get("errors"):
        state.update(assemble_early_failure(state))
        return state["result"]

    # Step 2: analyze hypothesis
    state.update(analyze_hypothesis_node(state))

    # Step 3: classifier (advisory)
    state.update(run_classifier_node(state))

    # Step 4: preprocess
    state.update(language_preprocess_node(state))

    # Main loop with retry
    for retry in range(MAX_RETRIES + 1):
        state["retry_round"] = retry

        if verbose:
            logger.info("--- Retry round %d ---", retry)

        # Dispatch
        state.update(setup_dispatch_node(state))

        # Run specialists
        state.update(run_specialists(state))

        # Collect (merge with previous rounds)
        state.update(collect_specialists_node(state))

        # Build oracle (only on first round)
        state.update(build_oracle_node(state))

        # Verify claims
        state.update(verify_claims_node(state))

        # Oracle test
        state.update(oracle_test_node(state))

        # Proof checker
        state.update(run_proof_checker_node(state))

        # Reasoning
        state.update(run_reasoning_node(state))

        # Decide
        decision = decide_retry(state)

        if decision == "done":
            state.update(formalize_node(state))
            state.update(assemble_result_node(state))
            return state["result"]

        if decision == "retry":
            state.update(run_retry_planner_node(state))
            # Reset specialist_outputs for next round (only new ones collected)
            state["specialist_outputs"] = []
            continue

        if decision == "invert":
            state.update(invert_hypothesis_node(state))
            state["specialist_outputs"] = []
            continue

        # decision == "fail"
        break

    # Max retries exceeded
    state.update(assemble_early_failure(state))
    return state["result"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the CFL analysis pipeline",
    )
    parser.add_argument("task_file", help="Path to CFL IR JSON file")
    parser.add_argument("--mock", help="Mock directory for agent outputs")
    parser.add_argument("--live", action="store_true", help="Use Anthropic API (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--save", help="Save live agent outputs to this directory")
    args = parser.parse_args()

    if args.mock and args.live:
        print("Error: --mock and --live are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    ir_data = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    task_name = Path(args.task_file).stem

    mock = None
    live = None
    if args.mock:
        mock = MockRunner(args.mock, task_name)
    elif args.live:
        live = LiveRunner(verbose=args.verbose)

    result = run_pipeline(ir_data, mock_runner=mock, agent_runner=live, verbose=args.verbose)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Optionally save live outputs
    if args.save and live:
        save_dir = Path(args.save)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"{task_name}_result.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Result saved to {out_path}", file=sys.stderr)
