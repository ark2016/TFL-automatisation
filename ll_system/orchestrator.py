"""
LL(k) pipeline orchestrator — LangGraph StateGraph implementation.

Implements the pipeline graph for LL(k) checking using LangGraph
with fan-out/fan-in for parallel specialists, retry cycles, and
Format 3 fast path (grammar directly to oracle, skip agents).

Usage:
    from ll_system.orchestrator import run_pipeline, MockRunner, LiveRunner
    result = run_pipeline(ir_dict, mock_runner=MockRunner("examples/mock/", "task_grammar_bb_aA"))

    # CLI:
    python -m ll_system.orchestrator examples/task_grammar_bb_aA.json --mock examples/mock/
    python -m ll_system.orchestrator examples/task_grammar_bb_aA.json --live
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

from ll_system.lib.ll_ir_schema import validate_ll_ir
from ll_system.lib.preprocess import compute_preprocess_hints
from ll_system.lib.ll_table_builder import check_ll_k, find_min_ll_k
from ll_system.lib.claim_verifier import verify_ll_claim

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 2

LL_SPECIALIST_NAMES = (
    "ll_grammar_builder",
    "marker_analyzer",
    "grammar_transformer",
    "substitution_agent",
    "ambiguity_detector",
    "prefix_classes_agent",
)

_CONSTRUCTIVE_AGENTS = {"ll_grammar_builder", "marker_analyzer", "grammar_transformer"}
_DESTRUCTIVE_AGENTS = {"substitution_agent", "ambiguity_detector", "prefix_classes_agent"}


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    """Full state flowing through the LangGraph LL pipeline."""

    # -- Inputs --
    ir: dict
    mock_runner: Any
    agent_runner: Any
    verbose: bool

    # -- Pipeline data --
    input_format: int            # 1, 2, or 3
    preprocess_hints: dict
    classifier_output: dict

    # -- Specialist dispatch --
    dispatch: dict                                       # {name: bool}
    agents_to_retry: Any                                 # None = all, list = selective
    specialist_outputs: Annotated[list, operator.add]    # [(name, output)]
    agent_results: dict                                  # accumulated across retries

    # -- Oracle --
    first_follow_result: dict    # from check_ll_k / find_min_ll_k

    # -- Verification --
    claim_verification: dict

    # -- Reasoning & retry --
    reasoning_output: dict
    retry_round: int
    retry_context: dict

    # -- Accumulated --
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
        from ll_system.config import (
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
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Pass api_key= or set the env var."
            )
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
        # Model used to repair broken JSON returned by specialist agents.
        # Haiku is ~15x cheaper than Opus and excellent at structural text
        # conversion — ideal for turning "Opus wrote prose around its JSON"
        # into pure JSON.
        self._json_repair_model = "claude-haiku-4-5-20251001"

    def _repair_json_with_haiku(
        self, agent_name: str, raw_text: str, was_truncated: bool
    ) -> dict | None:
        """Ask Haiku to extract/repair valid JSON from a specialist's raw output.

        Returns the parsed dict on success, None on failure. Never raises.
        """
        if not raw_text or not raw_text.strip():
            return None

        truncation_note = (
            "\n\nNOTE: The original response was truncated at max_tokens. "
            "The JSON is incomplete. Close any open braces/brackets sensibly, "
            "dropping the last partial field if needed. Preserve all fully-"
            "written fields verbatim."
            if was_truncated else ""
        )

        system = (
            "You are a JSON repair tool. You will receive text that was "
            "supposed to be a single JSON object but failed to parse. "
            "Your ONLY job is to extract or fix that JSON object and return "
            "it as valid JSON. Do NOT add explanations, comments, or markdown "
            "fences. Do NOT change semantic content — only fix syntax "
            "(quote style, trailing commas, stray text around the object, "
            "missing closing braces). Return ONLY the repaired JSON object, "
            "nothing else."
        )
        user_msg = (
            f"Agent: {agent_name}\n"
            f"The following text failed JSON parsing. Repair it and return "
            f"the clean JSON object:{truncation_note}\n\n"
            f"```\n{raw_text}\n```"
        )

        t0 = _time.monotonic()
        try:
            response = self.client.messages.create(
                model=self._json_repair_model,
                max_tokens=min(len(raw_text) // 2 + 2000, 8000),
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as exc:
            logger.warning("[%s] JSON repair (Haiku) failed: %s", agent_name, exc)
            return None

        elapsed = _time.monotonic() - t0
        repaired_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                repaired_text += block.text

        if self.verbose:
            usage = response.usage
            t_in = usage.input_tokens if usage else 0
            t_out = usage.output_tokens if usage else 0
            print(
                f"[{agent_name}] json-repair via haiku: "
                f"tokens_in={t_in} tokens_out={t_out} time={elapsed:.1f}s",
                file=sys.stderr, flush=True,
            )

        parsed = _extract_json(repaired_text)
        if parsed is not None:
            logger.info("[%s] JSON repaired via Haiku", agent_name)
        return parsed

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
        max_tokens = self.max_tokens_per_agent.get(agent_name, self.max_tokens)
        user_content = json.dumps(input_data or {}, ensure_ascii=False, indent=2)

        last_error: str | None = None
        prev_raw_excerpt: str | None = None
        raw_text = ""
        for attempt in range(1 + self.json_retries):
            if attempt > 0 and last_error:
                excerpt = ""
                if prev_raw_excerpt:
                    excerpt = (
                        f"\nYour previous response (first 500 chars):\n"
                        f"---\n{prev_raw_excerpt[:500]}\n---\n"
                        f"(then {max(0, len(prev_raw_excerpt) - 500)} more chars "
                        f"that were also invalid)"
                    )
                user_msg = (
                    f"{user_content}\n\n"
                    f"[RETRY {attempt}/{self.json_retries}] "
                    f"Your previous response could not be parsed as JSON.\n"
                    f"Parse error: {last_error}"
                    f"{excerpt}\n\n"
                    f"CRITICAL instructions for this retry:\n"
                    f"1. Output ONLY a single JSON object — nothing before, "
                    f"nothing after, no markdown fences, no commentary.\n"
                    f"2. Fix the specific error shown above at the indicated "
                    f"position.\n"
                    f"3. Preserve all the semantic content you intended last "
                    f"time — only fix the syntax.\n"
                    f"4. Double-check: matched braces/brackets, commas "
                    f"between fields, double quotes (not single), no "
                    f"trailing commas, no comments."
                )
            else:
                user_msg = user_content

            t0 = _time.monotonic()
            raw_text = ""
            tokens_in = tokens_out = 0
            stop_reason = None
            # Always stream. Non-streaming requests are rejected by the SDK
            # when max_tokens x projected latency exceeds 10 minutes; streaming
            # lifts that cap and handles long proofs reliably.
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
                if final_msg.usage is not None:
                    tokens_in = final_msg.usage.input_tokens
                    tokens_out = final_msg.usage.output_tokens
                stop_reason = getattr(final_msg, "stop_reason", None)
            except Exception as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                return {
                    "agent": agent_name,
                    "status": "agent_error",
                    "verdict": None,
                    "confidence": 0.0,
                    "errors": [f"API error: {exc}"],
                }

            elapsed = _time.monotonic() - t0

            if self.verbose:
                extra = (
                    f" stop={stop_reason}"
                    if stop_reason and stop_reason != "end_turn"
                    else ""
                )
                print(
                    f"[{agent_name}] model={model} "
                    f"tokens_in={tokens_in} tokens_out={tokens_out} "
                    f"time={elapsed:.1f}s max={max_tokens}{extra}",
                    file=sys.stderr, flush=True,
                )

            parsed, parse_error = _extract_json_with_error(raw_text)
            if parsed is not None:
                return parsed

            # Try cheap Haiku-based JSON repair before issuing another full retry.
            was_truncated = stop_reason == "max_tokens"
            repaired = self._repair_json_with_haiku(agent_name, raw_text, was_truncated)
            if repaired is not None:
                return repaired

            if was_truncated:
                last_error = (
                    f"Response was truncated at max_tokens={max_tokens} "
                    f"(tokens_out={tokens_out}); Haiku repair also failed. "
                    f"Parse error: {parse_error}"
                )
                logger.error("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)
                break

            last_error = (
                f"{parse_error} "
                f"[response length={len(raw_text)}, Haiku repair also failed]"
            )
            prev_raw_excerpt = raw_text
            logger.warning("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)

        logger.error("[%s] JSON parse failed: %s", agent_name, last_error)
        return {
            "agent": agent_name, "status": "agent_error", "verdict": None,
            "confidence": 0.0,
            "errors": [last_error or "JSON parse failed"],
            "raw_response": raw_text[:2000],
        }


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Extract JSON object from LLM response text (back-compat wrapper)."""
    parsed, _ = _extract_json_with_error(text)
    return parsed


def _extract_json_with_error(text: str) -> tuple[dict | None, str | None]:
    """Extract JSON object and return (parsed, error_detail).

    error_detail is None on success, otherwise a human-readable string
    describing what went wrong at which position. Tries three strategies:
      1) whole text as JSON
      2) content of a ```json fenced block
      3) substring between first '{' and last '}'
    """
    text = text.strip()
    if not text:
        return None, "response was empty"

    last_err: json.JSONDecodeError | None = None
    last_strategy: str = ""
    fence_match = None
    first_brace = -1
    last_brace = -1

    # Strategy 1: whole text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
        return None, f"parsed as JSON but top-level is {type(obj).__name__}, not object"
    except json.JSONDecodeError as e:
        last_err, last_strategy = e, "whole text"

    # Strategy 2: fenced code block
    fence_match = _re.search(r"```(?:json)?\s*\n(.*?)\n```", text, _re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj, None
            return None, f"fenced block parsed but top-level is {type(obj).__name__}"
        except json.JSONDecodeError as e:
            last_err, last_strategy = e, "fenced block"

    # Strategy 3: first-brace to last-brace
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        substring = text[first_brace:last_brace + 1]
        try:
            obj = json.loads(substring)
            if isinstance(obj, dict):
                return obj, None
            return None, f"brace-substring parsed but top-level is {type(obj).__name__}"
        except json.JSONDecodeError as e:
            last_err, last_strategy = e, "brace substring"

    if last_err is not None:
        msg = last_err.msg
        line = last_err.lineno
        col = last_err.colno
        pos = last_err.pos

        src = text
        if last_strategy == "fenced block" and fence_match:
            src = fence_match.group(1)
        elif last_strategy == "brace substring" and first_brace != -1:
            src = text[first_brace:last_brace + 1]

        start = max(0, pos - 30)
        end = min(len(src), pos + 30)
        context = src[start:end].replace("\n", "\\n")
        pointer_offset = pos - start
        pointer = " " * pointer_offset + "^"

        return None, (
            f"{msg} at line {line} column {col} (char {pos}) "
            f"— tried strategy: {last_strategy}. "
            f"Context around failure:\n  {context}\n  {pointer}"
        )

    return None, "no JSON object found in response (no '{' / '}' delimiters)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_runner(state: PipelineState) -> MockRunner | LiveRunner | None:
    return state.get("mock_runner") or state.get("agent_runner")


def _run_agent(
    state: PipelineState, agent_name: str, input_data: dict | None = None
) -> dict | None:
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


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

def _normalize_verdict(raw: Any) -> str | None:
    """Normalize a verdict string to one of {"ll", "not_ll", "uncertain", None}.

    Maps common synonyms and rejects anything else (returns None).
    """
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("ll", "is_ll", "ll_k", "is_ll_k"):
        return "ll"
    if v in ("not_ll", "non_ll", "not_ll_k", "non_ll_k", "not_context_free"):
        return "not_ll"
    if v == "uncertain":
        return "uncertain"
    return None


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


def _collect_failed_agents(state: PipelineState) -> list[dict]:
    """Return list of {agent, error} for agents that were dispatched but failed."""
    failed: list[dict] = []
    seen: set[str] = set()
    for err in state.get("errors", []):
        if not isinstance(err, str) or ":" not in err:
            continue
        name, _, msg = err.partition(":")
        name = name.strip()
        if name in seen:
            continue
        if name in LL_SPECIALIST_NAMES or name in ("formalizer", "reasoning_agent"):
            failed.append({"agent": name, "error": msg.strip()})
            seen.add(name)
    return failed


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def validate_ir_node(state: PipelineState) -> dict:
    log_msg(state, "validate_ir_node...")
    errs = validate_ll_ir(state["ir"])
    if errs:
        return {"errors": errs}
    # Detect format from task_type
    task_type = state["ir"].get("task_type", "")
    if task_type == "ll_check_grammar":
        fmt = 3
    elif task_type == "ll_check_grammar_lang":
        fmt = 2
    else:
        fmt = 1
    log_msg(state, f"  input_format={fmt} task_type={task_type!r}")
    return {"input_format": fmt}


def preprocess_node(state: PipelineState) -> dict:
    log_msg(state, "preprocess_node...")
    hints = compute_preprocess_hints(state["ir"])
    log_msg(state, f"  is_regular={hints.get('is_regular')} conf={hints.get('confidence')}")

    # Regularity short-circuit: if regular → LL(1) immediately (no agents needed)
    if hints.get("is_regular"):
        regularity_reason = hints.get("reason") or hints.get("regularity_reason")
        log_msg(state, f"  regularity shortcut: {regularity_reason}")
        return {
            "preprocess_hints": hints,
            "result": {
                "task_type": state["ir"].get("task_type"),
                "source_text": state["ir"].get("source_text"),
                "verdict": "ll",
                "k": 1,
                "confidence": 0.95,
                "proof": {
                    "method": "regularity",
                    "details": {"reason": regularity_reason},
                },
                "grammar": None,
                "first_follow_result": None,
                "claim_verification": {},
                "agents_used": [],
                "agents_failed": [],
                "specialist_outputs": {},
                "reasoning_summary": "Язык является регулярным, следовательно LL(1).",
                "errors": [],
                "retries": 0,
            },
        }
    return {"preprocess_hints": hints}


def first_follow_oracle_node(state: PipelineState) -> dict:
    """Run LL(k) oracle on a grammar.

    For Format 3: grammar is at state["ir"]["grammar"].
    For Format 1/2: look for grammar produced by constructive agents
    (ll_grammar_builder, marker_analyzer, grammar_transformer).

    Tries k=1,2,...,10 via find_min_ll_k. Returns first_follow_result.
    """
    log_msg(state, "first_follow_oracle_node...")
    ir = state["ir"]
    grammar = None

    if state.get("input_format") == 3:
        grammar = ir.get("grammar")
        log_msg(state, "  using grammar from IR (Format 3)")
    else:
        # Try to find a grammar proposed by constructive agents
        agent_results = state.get("agent_results", {})
        for agent_name in ("ll_grammar_builder", "marker_analyzer", "grammar_transformer"):
            out = agent_results.get(agent_name, {})
            if isinstance(out, dict):
                proof_sketch = out.get("proof_sketch") or {}
                g = (
                    proof_sketch.get("grammar")
                    or out.get("artifacts", {}).get("ll_grammar")
                    or out.get("grammar")
                )
                if g:
                    grammar = g
                    log_msg(state, f"  using grammar from agent: {agent_name}")
                    break

    if not grammar:
        log_msg(state, "  no grammar available for oracle")
        return {"first_follow_result": {"is_ll_k": None, "reason": "no grammar available"}}

    try:
        result = find_min_ll_k(grammar, max_k=10)
        ff_result = dict(result.get("result_for_k") or {})
        ff_result["found"] = result.get("found", False)
        ff_result["min_k"] = result.get("k")
        log_msg(
            state,
            f"  oracle: found={ff_result.get('found')} k={ff_result.get('min_k')}",
        )
        return {"first_follow_result": ff_result}
    except Exception as exc:
        logger.warning("first_follow_oracle: %s", exc)
        return {"first_follow_result": {"is_ll_k": None, "error": str(exc)}}


def run_classifier_node(state: PipelineState) -> dict:
    log_msg(state, "run_classifier_node (advisory)...")
    classifier_input = {
        "ir": state["ir"],
        "preprocess_hints": state.get("preprocess_hints", {}),
    }
    output = _run_agent(state, "classifier", classifier_input)
    if output is None or output.get("status") == "agent_error":
        if output is not None:
            log_msg(state, "  classifier returned agent_error, ignoring")
        return {}
    return {"classifier_output": output}


def setup_dispatch_node(state: PipelineState) -> dict:
    """Dispatch all 6 agents (first run) or selected agents (retry)."""
    agents_to_retry = state.get("agents_to_retry")

    if isinstance(agents_to_retry, list) and len(agents_to_retry) > 0:
        valid = [a for a in agents_to_retry if a in LL_SPECIALIST_NAMES]
        if valid:
            dispatch = {name: (name in valid) for name in LL_SPECIALIST_NAMES}
            log_msg(state, f"  selective dispatch: {valid}")
            return {"dispatch": dispatch}
        log_msg(state, "  agents_to_retry had no valid agents, falling back to all")

    dispatch = {name: True for name in LL_SPECIALIST_NAMES}
    log_msg(state, "  full dispatch: all 6 agents")
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
    """Run a single specialist agent. Invoked via Send() fan-out."""
    agent_name = state["_specialist_name"]
    log_msg(state, f"  specialist: {agent_name}...")

    inp: dict[str, Any] = {
        "ir": state["ir"],
        "preprocess_hints": state.get("preprocess_hints", {}),
        "classifier_hint": state.get("classifier_output", {}),
    }
    retry_ctx = state.get("retry_context") or {}
    if retry_ctx:
        hints = retry_ctx.get("hints") or {}
        if isinstance(hints, dict):
            agent_hint = hints.get(agent_name)
            if agent_hint is not None:
                inp["retry_params"] = agent_hint

    out = _run_agent(state, agent_name, inp)

    if out is not None and out.get("status") == "agent_error":
        err_msgs = out.get("errors") or [f"{agent_name} returned agent_error"]
        log_msg(state, f"  {agent_name} returned agent_error: {err_msgs}")
        return {
            "specialist_outputs": [(agent_name, None)],
            "errors": [f"{agent_name}: {m}" for m in err_msgs],
        }

    return {"specialist_outputs": [(agent_name, out)]}


def collect_specialists_node(state: PipelineState) -> dict:
    """Fan-in: merge specialist_outputs into agent_results.

    On retry, if the retried agent emitted None (failed), drop its
    previous result to avoid carrying stale evidence forward.
    """
    agent_results = dict(state.get("agent_results", {}))

    dispatched = {k for k, v in state.get("dispatch", {}).items() if v}

    latest_this_round: dict[str, Any] = {}
    for name, out in state.get("specialist_outputs", []):
        if name in dispatched:
            latest_this_round[name] = out

    for name, out in latest_this_round.items():
        if out is None:
            # Retried agent failed → drop stale result
            agent_results.pop(name, None)
        else:
            agent_results[name] = out

    log_msg(state, f"  collected specialists (round): {sorted(latest_this_round.keys())}")
    return {"agent_results": agent_results}


def verify_claims_node(state: PipelineState) -> dict:
    """For each specialist result, call verify_ll_claim."""
    log_msg(state, "verify_claims_node...")
    ir = state["ir"]
    verifications: dict[str, dict] = {}
    for name, output in state.get("agent_results", {}).items():
        if not isinstance(output, dict):
            continue
        agent_result_formatted = {
            "agent_name": name,
            "verdict": output.get("verdict"),
            "proof_sketch": output.get("proof_sketch"),
            "artifacts": output.get("artifacts", {}),
        }
        try:
            verifications[name] = verify_ll_claim(agent_result_formatted, ir)
        except Exception as exc:
            logger.warning("verify_ll_claim failed for %s: %s", name, exc)
            verifications[name] = {"verification_status": "error", "error": str(exc)}
    return {"claim_verification": verifications}


def run_reasoning_node(state: PipelineState) -> dict:
    """Run the reasoning agent to synthesize a verdict."""
    log_msg(state, "run_reasoning_node...")

    # Clear any stale retry_context from a prior round
    state["retry_context"] = {}

    agent_results = state.get("agent_results", {})
    reasoning_input = {
        "ir": state["ir"],
        "preprocess_hints": state.get("preprocess_hints", {}),
        "classifier_hint": state.get("classifier_output", {}),
        "specialist_outputs": {
            k: v for k, v in agent_results.items() if k in LL_SPECIALIST_NAMES
        },
        "first_follow_result": state.get("first_follow_result", {}),
        "claim_verification": state.get("claim_verification", {}),
        "retry_count": state.get("retry_round", 0),
        "max_retries": MAX_RETRIES,
    }
    output = _run_agent(state, "reasoning_agent", reasoning_input)

    # Normalize verdict
    if isinstance(output, dict) and "verdict" in output:
        raw_verdict = output.get("verdict")
        normalized = _normalize_verdict(raw_verdict)
        if raw_verdict is not None and normalized != raw_verdict:
            log_msg(state, f"  normalized verdict {raw_verdict!r} -> {normalized!r}")
        output["verdict"] = normalized

    # Detect invalid outputs that require fallback
    needs_fallback = False
    if output is None:
        needs_fallback = True
    elif output.get("status") == "agent_error":
        needs_fallback = True
    elif not output.get("action"):
        needs_fallback = True
    else:
        action = output.get("action", "done")
        if action not in ("done", "retry"):
            log_msg(state, f"  reasoning returned unknown action={action!r}, using fallback")
            needs_fallback = True
        elif action == "done" and not output.get("verdict"):
            needs_fallback = True

    if needs_fallback:
        log_msg(state, "  reasoning unavailable/invalid, using fallback")
        output = _fallback_reasoning(state)

    action = output.get("action", "done")
    log_msg(state, f"  action={action} verdict={output.get('verdict')}")
    return {"reasoning_output": output, "retry_context": {}}


def _fallback_reasoning(state: PipelineState) -> dict:
    """Heuristic reasoning when no reasoning agent is available."""
    preprocess_hints = state.get("preprocess_hints", {}) or {}
    ff_result = state.get("first_follow_result", {}) or {}
    verifications = state.get("claim_verification", {}) or {}
    agent_results = state.get("agent_results", {}) or {}
    retry_round = state.get("retry_round", 0)

    # Oracle result is authoritative when available
    ff_found = ff_result.get("found")
    ff_min_k = ff_result.get("min_k")
    ff_error = ff_result.get("error")

    if ff_found is True and ff_min_k is not None and not ff_error:
        return {
            "action": "done",
            "verdict": "ll",
            "k": ff_min_k,
            "confidence": 0.95,
            "summary": f"Grammar is LL({ff_min_k}) (first/follow oracle)",
            "retry_plan": None,
        }
    if ff_found is False and not ff_error:
        return {
            "action": "done",
            "verdict": "not_ll",
            "k": None,
            "confidence": 0.85,
            "summary": "Grammar is not LL(k) for any k <= 10 (first/follow oracle)",
            "retry_plan": None,
        }

    # Check if any constructive agent returned a valid LL grammar
    for agent_name in _CONSTRUCTIVE_AGENTS:
        out = agent_results.get(agent_name, {})
        if isinstance(out, dict) and _normalize_verdict(out.get("verdict")) == "ll":
            conf = _clamp_confidence(out.get("confidence", 0.5))
            if conf >= 0.7:
                return {
                    "action": "done",
                    "verdict": "ll",
                    "k": out.get("k"),
                    "confidence": conf,
                    "summary": f"Agent '{agent_name}' found LL grammar",
                    "retry_plan": None,
                }

    # Check destructive agents
    for agent_name in _DESTRUCTIVE_AGENTS:
        out = agent_results.get(agent_name, {})
        if isinstance(out, dict) and _normalize_verdict(out.get("verdict")) == "not_ll":
            conf = _clamp_confidence(out.get("confidence", 0.5))
            if conf >= 0.7:
                return {
                    "action": "done",
                    "verdict": "not_ll",
                    "k": None,
                    "confidence": conf,
                    "summary": f"Agent '{agent_name}' proved not LL",
                    "retry_plan": None,
                }

    # Count verified vs refuted claims
    verified_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and v.get("verification_status") == "verified"
    )
    refuted_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and v.get("verification_status") == "refuted"
    )

    if refuted_count > 0 and retry_round < MAX_RETRIES:
        refuted_agents = [
            name for name, v in verifications.items()
            if isinstance(v, dict) and v.get("verification_status") == "refuted"
        ]
        return {
            "action": "retry",
            "verdict": None,
            "k": None,
            "confidence": 0.2,
            "summary": f"{refuted_count} claims refuted; retrying {refuted_agents}",
            "retry_plan": {"agents_to_retry": refuted_agents, "hints": {}},
        }

    if retry_round < MAX_RETRIES and not agent_results:
        return {
            "action": "retry",
            "verdict": None,
            "k": None,
            "confidence": 0.2,
            "summary": "No agent results, retrying",
            "retry_plan": None,
        }

    # Terminal: return uncertain
    return {
        "action": "done",
        "verdict": "uncertain",
        "k": None,
        "confidence": 0.0,
        "summary": "Insufficient evidence to determine LL property",
        "retry_plan": None,
    }


def decide_retry(state: PipelineState) -> str:
    """Conditional edge after reasoning: done or retry."""
    reasoning = state.get("reasoning_output", {})
    action = reasoning.get("action", "done")
    retry_round = state.get("retry_round", 0)
    if action == "retry" and retry_round < MAX_RETRIES:
        return "retry"
    return "done"


def handle_retry_node(state: PipelineState) -> dict:
    """Prepare state for the next retry round."""
    log_msg(state, "handle_retry_node...")
    reasoning = state.get("reasoning_output", {})
    retry_plan = reasoning.get("retry_plan") or {}
    agents_to_retry = retry_plan.get("agents_to_retry") or list(LL_SPECIALIST_NAMES)
    new_round = state.get("retry_round", 0) + 1
    log_msg(state, f"  retry round -> {new_round}, agents={agents_to_retry}")
    return {
        "agents_to_retry": agents_to_retry,
        "retry_round": new_round,
        "retry_context": retry_plan,
        "specialist_outputs": [],   # reset for new round (Annotated[list, add] accumulator)
    }


def formalize_node(state: PipelineState) -> dict:
    """Run formalizer agent → structured Markdown proof."""
    log_msg(state, "formalize_node...")
    runner = _get_runner(state)
    if runner is None:
        return {}

    reasoning = state.get("reasoning_output", {})
    verdict = reasoning.get("verdict")
    if not verdict or verdict == "uncertain":
        return {}

    agent_results = state.get("agent_results", {})
    formalizer_input = {
        "ir": state["ir"],
        "reasoning_output": reasoning,
        "specialist_outputs": {
            k: v for k, v in agent_results.items() if k in LL_SPECIALIST_NAMES
        },
        "first_follow_result": state.get("first_follow_result", {}),
        "claim_verification": state.get("claim_verification", {}),
    }

    output = _run_agent(state, "formalizer", formalizer_input)
    if output is None:
        return {}
    if output.get("status") == "agent_error":
        err_msgs = output.get("errors") or ["formalizer returned agent_error"]
        return {"errors": [f"formalizer: {m}" for m in err_msgs]}

    # Store formalizer output in agent_results for retrieval in assemble_result_node
    updated_results = dict(state.get("agent_results", {}))
    updated_results["formalizer"] = output
    return {"agent_results": updated_results}


def assemble_result_node(state: PipelineState) -> dict:
    """Assemble the final result dict from pipeline state."""
    log_msg(state, "assemble_result_node...")
    ir = state["ir"]
    reasoning = state.get("reasoning_output", {})
    ff_result = state.get("first_follow_result")
    agent_results = state.get("agent_results", {})

    # For Format 3 (fast path), reasoning_output may be empty —
    # derive verdict directly from first_follow_result.
    if state.get("input_format") == 3 and not reasoning.get("verdict"):
        ff = ff_result or {}
        is_ll = ff.get("is_ll_k") or ff.get("found", False)
        is_ll_explicit = ff.get("is_ll_k") if ff.get("is_ll_k") is not None else ff.get("found")
        if is_ll_explicit is True:
            verdict = "ll"
            k = ff.get("k") or ff.get("min_k")
            confidence = 1.0
        elif is_ll_explicit is False:
            verdict = "not_ll"
            k = None
            confidence = 1.0
        else:
            verdict = "uncertain"
            k = None
            confidence = 0.0

        proof = (
            {"method": "first_follow_oracle", "details": ff}
            if is_ll_explicit is not None
            else None
        )
        grammar = ir.get("grammar")
        reasoning_summary = _format3_summary(ff, verdict, k)

        return {
            "result": {
                "task_type": ir.get("task_type"),
                "source_text": ir.get("source_text"),
                "verdict": verdict,
                "k": k,
                "confidence": confidence,
                "proof": proof,
                "grammar": grammar,
                "first_follow_result": ff_result,
                "claim_verification": {},
                "agents_used": ["first_follow_oracle"],
                "agents_failed": [],
                "specialist_outputs": {},
                "reasoning_summary": reasoning_summary,
                "errors": state.get("errors", []),
                "retries": 0,
            }
        }

    # Normal path (Format 1 / 2)
    raw_verdict = reasoning.get("verdict")
    verdict = _normalize_verdict(raw_verdict) or "uncertain"
    confidence = _clamp_confidence(reasoning.get("confidence", 0.0))

    # Find best proof from constructive agents
    proof = None
    grammar = None
    for agent_name in _CONSTRUCTIVE_AGENTS:
        out = agent_results.get(agent_name, {})
        if isinstance(out, dict):
            ps = out.get("proof_sketch") or {}
            if ps.get("grammar"):
                grammar = ps["grammar"]
                proof = {
                    "method": ps.get("method", "ll_grammar_construction"),
                    "details": ps,
                }
                break
            # Also look for grammar at top level
            if not grammar and out.get("grammar"):
                grammar = out["grammar"]

    # For not_ll, get destructive proof
    if verdict == "not_ll" and proof is None:
        for agent_name in _DESTRUCTIVE_AGENTS:
            out = agent_results.get(agent_name, {})
            if isinstance(out, dict) and _normalize_verdict(out.get("verdict")) == "not_ll":
                ps = out.get("proof_sketch")
                if ps:
                    proof = {
                        "method": ps.get("method", "substitution") if isinstance(ps, dict) else "substitution",
                        "details": ps,
                    }
                    break

    # Formalizer output → reasoning_summary
    formalizer_out = agent_results.get("formalizer") or {}
    reasoning_summary = (
        (formalizer_out.get("markdown_solution") if isinstance(formalizer_out, dict) else None)
        or reasoning.get("summary")
        or reasoning.get("justification")
    )

    return {
        "result": {
            "task_type": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": verdict,
            "k": reasoning.get("k"),
            "confidence": confidence,
            "proof": proof,
            "grammar": grammar,
            "first_follow_result": ff_result,
            "claim_verification": state.get("claim_verification", {}),
            "agents_used": sorted(agent_results.keys()),
            "agents_failed": _collect_failed_agents(state),
            "specialist_outputs": {
                k: v for k, v in agent_results.items()
                if k in LL_SPECIALIST_NAMES
            },
            "reasoning_output": reasoning,
            "reasoning_summary": reasoning_summary,
            "errors": state.get("errors", []),
            "retries": state.get("retry_round", 0),
        }
    }


def assemble_early_failure(state: PipelineState) -> dict:
    """Assemble result for validation errors or unrecoverable failures."""
    log_msg(state, "assemble_early_failure...")
    ir = state.get("ir", {})
    errors = list(state.get("errors", []))

    if errors:
        detail = "; ".join(str(e) for e in errors)
    else:
        detail = "Pipeline failed without error details"

    agent_results = state.get("agent_results", {}) or {}
    specialist_outputs_out: dict[str, dict] = {}
    for name in LL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
            specialist_outputs_out[name] = out

    return {
        "result": {
            "task_type": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "uncertain",
            "k": None,
            "confidence": 0.0,
            "proof": None,
            "grammar": None,
            "first_follow_result": None,
            "claim_verification": {},
            "agents_used": sorted(agent_results.keys()),
            "agents_failed": _collect_failed_agents(state),
            "specialist_outputs": specialist_outputs_out,
            "reasoning_summary": detail,
            "errors": errors if errors else [detail],
            "retries": state.get("retry_round", 0),
        },
    }


def _format3_summary(ff: dict, verdict: str, k: int | None) -> str:
    """Build a human-readable summary for Format 3 oracle result."""
    if verdict == "ll" and k is not None:
        return f"Грамматика является LL({k}) (первое/следующее множество без конфликтов)."
    if verdict == "not_ll":
        conflicts = ff.get("conflicts") or []
        if conflicts:
            n = len(conflicts)
            return f"Грамматика не является LL(k): обнаружено {n} конфликт(ов) в таблице разбора."
        return "Грамматика не является LL(k) ни для какого k ≤ 10."
    return "Не удалось определить LL-свойство грамматики."


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_ll_pipeline_graph() -> Any:
    """Build and compile the LL(k) LangGraph pipeline.

    Returns a compiled StateGraph ready for .invoke(initial_state).

    Flow:
    - Format 3 (ll_check_grammar): validate → oracle → assemble (fast path, no agents)
    - Format 1/2: validate → preprocess [→ early exit if regular] →
        classifier → dispatch → specialists → oracle → verify → reasoning
        → [retry loop] → formalize → assemble
    """
    graph = StateGraph(PipelineState)

    # Register nodes
    graph.add_node("validate_ir_node", validate_ir_node)
    graph.add_node("preprocess_node", preprocess_node)
    graph.add_node("first_follow_oracle_node", first_follow_oracle_node)
    graph.add_node("run_classifier_node", run_classifier_node)
    graph.add_node("setup_dispatch_node", setup_dispatch_node)
    graph.add_node("run_specialist_node", run_specialist_node)
    graph.add_node("collect_specialists_node", collect_specialists_node)
    graph.add_node("verify_claims_node", verify_claims_node)
    graph.add_node("run_reasoning_node", run_reasoning_node)
    graph.add_node("handle_retry_node", handle_retry_node)
    graph.add_node("formalize_node", formalize_node)
    graph.add_node("assemble_result_node", assemble_result_node)
    graph.add_node("assemble_early_failure", assemble_early_failure)

    # START → validate
    graph.add_edge(START, "validate_ir_node")

    # validate → Format 3 fast path / normal / early fail
    graph.add_conditional_edges(
        "validate_ir_node",
        lambda s: (
            "early_fail" if s.get("errors")
            else "fast_path" if s.get("input_format") == 3
            else "normal"
        ),
        {
            "early_fail": "assemble_early_failure",
            "fast_path": "first_follow_oracle_node",
            "normal": "preprocess_node",
        },
    )

    # preprocess → regularity shortcut or continue
    graph.add_conditional_edges(
        "preprocess_node",
        lambda s: "regular_shortcut" if s.get("result") else "continue",
        {
            "regular_shortcut": END,
            "continue": "run_classifier_node",
        },
    )

    # Normal flow: classifier → dispatch → fan-out
    graph.add_edge("run_classifier_node", "setup_dispatch_node")
    graph.add_conditional_edges(
        "setup_dispatch_node",
        dispatch_to_specialists,
        ["run_specialist_node", "collect_specialists_node"],
    )
    graph.add_edge("run_specialist_node", "collect_specialists_node")

    # After specialists: oracle → verify → reasoning
    graph.add_edge("collect_specialists_node", "first_follow_oracle_node")

    # After oracle: Format 3 goes directly to assemble; normal goes to verify
    graph.add_conditional_edges(
        "first_follow_oracle_node",
        lambda s: "from_format3" if s.get("input_format") == 3 else "normal",
        {
            "from_format3": "assemble_result_node",
            "normal": "verify_claims_node",
        },
    )

    graph.add_edge("verify_claims_node", "run_reasoning_node")

    # Reasoning → retry or done
    graph.add_conditional_edges(
        "run_reasoning_node",
        decide_retry,
        {
            "retry": "handle_retry_node",
            "done": "formalize_node",
        },
    )

    graph.add_edge("handle_retry_node", "setup_dispatch_node")
    graph.add_edge("formalize_node", "assemble_result_node")
    graph.add_edge("assemble_result_node", END)
    graph.add_edge("assemble_early_failure", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Cached graph instance
# ---------------------------------------------------------------------------

_ll_pipeline_graph = None


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_pipeline(
    ir: dict,
    mock_runner: MockRunner | None = None,
    agent_runner: LiveRunner | None = None,
    verbose: bool = False,
    output_dir: str | None = None,
    task_name: str | None = None,
) -> dict:
    """Run the LL pipeline on an IR dict. Returns the result dict."""
    global _ll_pipeline_graph

    if verbose:
        logging.basicConfig(level=logging.INFO)

    if _ll_pipeline_graph is None:
        _ll_pipeline_graph = build_ll_pipeline_graph()

    initial_state: dict[str, Any] = {
        "ir": ir,
        "mock_runner": mock_runner,
        "agent_runner": agent_runner,
        "verbose": verbose,
        "input_format": 0,       # determined in validate_ir_node
        "preprocess_hints": {},
        "classifier_output": {},
        "dispatch": {},
        "agents_to_retry": None,
        "specialist_outputs": [],
        "agent_results": {},
        "first_follow_result": {},
        "claim_verification": {},
        "reasoning_output": {},
        "retry_round": 0,
        "retry_context": {},
        "errors": [],
        "_specialist_name": "",
        "result": {},
    }

    # Each retry adds ~10 node invocations (dispatch + 6 specialists + collect +
    # oracle + verify + reasoning).  With MAX_RETRIES=2, the worst case is
    # ~3 full rounds = ~30 node hops, plus fan-out overhead.  Set a generous
    # recursion limit so a legitimate retry cycle never hits the LangGraph cap.
    _recursion_limit = 100 + 20 * MAX_RETRIES
    final_state = _ll_pipeline_graph.invoke(
        initial_state, config={"recursion_limit": _recursion_limit}
    )
    result = final_state.get("result") or {}

    if not result:
        result = {
            "task_type": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "uncertain",
            "k": None,
            "confidence": 0.0,
            "proof": None,
            "grammar": None,
            "first_follow_result": None,
            "claim_verification": {},
            "agents_used": [],
            "agents_failed": [],
            "specialist_outputs": {},
            "reasoning_summary": "Graph produced no result",
            "errors": ["Graph produced no result"],
            "retries": 0,
        }

    # Optionally render outputs
    if output_dir and result:
        try:
            from ll_system.renderer import render_result
            render_result(result, output_dir=output_dir, task_name=task_name)
        except Exception as exc:
            logger.warning("Renderer failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the LL(k) analysis pipeline")
    parser.add_argument("input", help="IR JSON file")
    parser.add_argument("--mock", metavar="DIR", help="Mock responses directory")
    parser.add_argument("--live", action="store_true", help="Use live LLM (requires API key)")
    parser.add_argument("--out", metavar="DIR", help="Output directory")
    parser.add_argument("--task", metavar="NAME", help="Task name for mock file lookup")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--draw-graph", metavar="PATH", help="Save pipeline graph to this path (.mmd/.png)"
    )
    args = parser.parse_args()

    if args.draw_graph:
        g = build_ll_pipeline_graph()
        mmd = g.get_graph().draw_mermaid()
        Path(args.draw_graph).with_suffix(".mmd").write_text(mmd, encoding="utf-8")
        try:
            png = g.get_graph().draw_png()
            Path(args.draw_graph).with_suffix(".png").write_bytes(png)
            print(f"Graph saved: {args.draw_graph}.png", file=sys.stderr)
        except Exception:
            print(
                f"Graph saved: {args.draw_graph}.mmd (install pygraphviz for PNG)",
                file=sys.stderr,
            )
        sys.exit(0)

    if args.mock and args.live:
        print("Error: --mock and --live are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        ir = json.load(f)

    task_name = args.task or Path(args.input).stem
    mock_runner = MockRunner(args.mock, task_name) if args.mock else None
    agent_runner = LiveRunner(verbose=args.verbose) if args.live else None

    result = run_pipeline(
        ir,
        mock_runner=mock_runner,
        agent_runner=agent_runner,
        verbose=args.verbose,
        output_dir=args.out,
        task_name=task_name,
    )

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out:
        save_dir = Path(args.out)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"{task_name}_result.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Result saved to {out_path}", file=sys.stderr)

    # Exit code reflects pipeline outcome
    verdict = result.get("verdict")
    if verdict in ("ll", "not_ll"):
        sys.exit(0)
    elif verdict == "uncertain":
        sys.exit(2)
    else:
        sys.exit(1)
