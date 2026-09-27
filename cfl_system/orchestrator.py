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

from cfl_system.config import MAX_CALLS_PER_AGENT
from cfl_system.lib.cfl_ir_schema import validate_cfl_ir
from cfl_system.lib.cfl_hypothesis import analyze_cfl_hypothesis
from cfl_system.lib.language_preprocess import preprocess_language
from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir, grammar_oracle, pda_oracle
from cfl_system.lib.cfl_oracle_test import normalize_agent_pda, oracle_test
from cfl_system.lib.claim_verifier import verify_agent_claims

# Shared Anthropic call machinery (TODO.md §3): kwargs building, streaming +
# retry/backoff, typed errors, concurrency semaphore, usage tracking. See
# agent_system/lib/llm_client.py -- LiveRunner below is a thin wrapper.
from agent_system.lib.llm_client import (
    AnthropicClient,
    FatalAPIError,
    RetryableAPIError,
    UsageTracker,
    _is_adaptive_model as _shared_is_adaptive_model,
    estimate_cost_usd,
    extract_json as _extract_json,
    extract_json_with_error as _extract_json_with_error,
    format_structured_output_flag as _format_structured_output_flag,
    get_concurrency_semaphore,
)
from cfl_system.lib.agent_output_schema import schema_for as _output_schema_for

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

# Tuple, not set: iteration order decides which artifact wins, so keep it
# deterministic (independent of PYTHONHASHSEED).
_CONSTRUCTIVE_AGENTS = ("cfg_builder", "pda_builder")

# Agents whose claim, when trusted, supports a *destructive* (non_cfl) verdict.
_DESTRUCTIVE_AGENTS = (
    "pumping_cfl", "ogden", "closure_reduction", "decomposition",
    "parikh", "interchange", "morphism",
)

# docs/VERDICT_POLICY.md §1: trust taxonomy order (refuted is excluded from
# this ranking on purpose — it is a disqualifier, never "weaker evidence").
_TRUST_RANK = {"not_verified": 0, "well_formed": 1, "bounded_pass": 2, "verified": 3}

# docs/VERDICT_POLICY.md §2: confidence ceiling by strongest supporting basis.
_CONFIDENCE_CAP_BY_TRUST = {
    "verified": 0.98,
    "bounded_pass": 0.85,
    "well_formed": 0.60,
    "not_verified": 0.40,
}
_CONTRADICTION_CONFIDENCE_CAP = 0.50
# docs/VERDICT_POLICY.md R3: when a contradiction survives R3' cross-check,
# the "verified" side still wins, but capped at 0.85 (not the full 0.98 of
# an uncontested `verified` basis) -- the fact that a real contradiction was
# raised at all keeps some doubt alive even when one side is deterministically
# fully proven.
_CONTRADICTION_VERIFIED_CAP = 0.85


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

    # -- Verdict gate (docs/VERDICT_POLICY.md) --
    trust: dict                                          # {agent_name: trust_level}
    verdict_gate: dict                                    # {basis, contradiction, downgrades, confidence_cap}

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
    # Cost ceiling (config.MAX_CALLS_PER_AGENT): notes accumulated whenever a
    # specialist's call cap is reached and a requested retry is skipped for
    # it -- surfaced in the final verdict_gate.downgrades (apply_verdict_gate).
    call_cap_notes: Annotated[list, operator.add]

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
    """Run agents via Anthropic API using prompts from prompts/ directory.

    Thin wrapper (TODO.md §3): prompts/contracts/JSON-parsing/Haiku-repair
    stay pipeline-specific; the actual model call goes through the shared
    ``agent_system.lib.llm_client.AnthropicClient`` — kwargs building,
    stream-and-collect, typed retry/backoff, the process-wide concurrency
    semaphore, and usage tracking all live there now, not here.
    """

    def __init__(self, api_key: str | None = None, verbose: bool = False):
        from cfl_system.config import (
            MODELS, TEMPERATURES, EFFORT, DEFAULT_EFFORT, REFUSAL_FALLBACK,
            MAX_TOKENS, MAX_TOKENS_PER_AGENT,
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
        # TFL_MODEL_OVERRIDE forces every agent onto one model — used for cheap
        # live test runs (e.g. TFL_MODEL_OVERRIDE=claude-haiku-4-5).
        self.model_override = os.environ.get("TFL_MODEL_OVERRIDE", "").strip()
        self.temperatures = TEMPERATURES
        self.efforts = EFFORT
        self.default_effort = DEFAULT_EFFORT
        self.refusal_fallback = REFUSAL_FALLBACK
        self.max_tokens = MAX_TOKENS
        self.max_tokens_per_agent = MAX_TOKENS_PER_AGENT
        self.json_retries = LLM_JSON_RETRIES
        self.prompt_files = PROMPT_FILES
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.verbose = verbose
        self._prompt_cache: dict[str, str] = {}
        # Model used to repair broken JSON returned by specialist agents.
        # Haiku is ~15× cheaper than Opus and excellent at structural text
        # conversion — ideal for turning "Opus wrote prose around its JSON"
        # into pure JSON. One repair call ≈ $0.01 vs ≈ $0.25 for a full
        # Opus retry, and it's faster too (~2s vs ~30s).
        self._json_repair_model = "claude-haiku-4-5"
        # Shared call machinery (TODO.md §3): kwargs building, streaming,
        # retry/backoff, usage tracking. Not the SDK client itself — `self.
        # client` stays the mutable attribute (tests swap it for a mock).
        self.usage_tracker = UsageTracker()
        self._shared = AnthropicClient(
            default_effort=self.default_effort,
            refusal_fallback=self.refusal_fallback,
            usage_tracker=self.usage_tracker,
        )

    # Legacy models — Haiku 4.5 and anything before the 4.6 family — take
    # sampling parameters and have no adaptive thinking / effort. Opus/Sonnet
    # 4.6+ and every 5.x model run adaptive thinking steered by `effort`, and
    # Opus 4.7+, Sonnet 5 and Opus 5.x reject `temperature` with a 400, so
    # thinking models never get sampling parameters.
    _LEGACY_MODEL_RE = _re.compile(r"^claude-3|haiku|-4(-[015])?(-\d{8})?$")
    # Models that get the server-side refusal fallback (see REFUSAL_FALLBACK).
    _FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5")

    @classmethod
    def _is_adaptive_model(cls, model: str) -> bool:
        """Whether `model` runs adaptive thinking (and rejects `temperature`)."""
        return _shared_is_adaptive_model(model)

    def _build_request_kwargs(self, model: str, max_tokens: int,
                              temperature: float, system_prompt: str,
                              user_msg: str, effort: str | None = None) -> dict:
        """Build kwargs for messages.stream / messages.create.

        Thinking models get `thinking: adaptive` + an explicit effort level
        (Opus 5.5 would silently default to "medium"); legacy models get
        `temperature` instead. Delegates to the shared AnthropicClient so
        every pipeline builds kwargs the same way (TODO.md §3).
        """
        return self._shared.build_request_kwargs(
            model, max_tokens, system_prompt, user_msg,
            effort=effort, temperature=temperature,
        )

    @staticmethod
    def _refusal_error(agent_name: str, result: Any) -> dict:
        """agent_error dict for a safety-classifier decline (stop_reason="refusal").

        `result` is a ``llm_client.CallResult`` (or anything exposing the
        same ``refusal_category`` / ``refusal_explanation`` attributes).
        """
        category = getattr(result, "refusal_category", None)
        explanation = getattr(result, "refusal_explanation", None)
        msg = f"Model refused (stop_reason=refusal, category={category})"
        if explanation:
            msg += f": {explanation}"
        logger.error("[%s] %s", agent_name, msg)
        return {
            "agent": agent_name, "status": "agent_error", "verdict": None,
            "confidence": 0.0, "evidence": {}, "errors": [msg],
        }

    def _repair_json_with_haiku(self, agent_name: str, raw_text: str, was_truncated: bool) -> dict | None:
        """Ask Haiku to extract/repair valid JSON from a specialist's raw output.

        Returns the parsed dict on success, None on failure. Never raises.

        Costs pennies compared to retrying the original Opus agent. Handles:
          - trailing prose after the JSON object
          - stray markdown fences inside the object
          - single quotes, trailing commas, comment lines
          - responses truncated at max_tokens (closes open braces/brackets,
            drops the last partial field)

        Not called on empty input.
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
        repair_max_tokens = min(len(raw_text) // 2 + 2000, 8000)
        request_kwargs = self._build_request_kwargs(
            model=self._json_repair_model, max_tokens=repair_max_tokens,
            temperature=0.0, system_prompt=system, user_msg=user_msg,
        )
        try:
            with get_concurrency_semaphore():
                response = self.client.messages.create(**request_kwargs)
        except Exception as exc:
            logger.warning("[%s] JSON repair (Haiku) failed: %s", agent_name, exc)
            return None

        elapsed = _time.monotonic() - t0
        repaired_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                repaired_text += block.text

        usage = getattr(response, "usage", None)
        self.usage_tracker.record(
            getattr(response, "model", self._json_repair_model), usage,
            agent=agent_name, used_structured_output=False,
        )
        if self.verbose:
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
        # cfl_classifier.md states how many specialists are always dispatched
        # regardless of its (advisory) verdict — substitute the count instead
        # of hardcoding it in the prompt text, so adding/removing a
        # specialist can't silently make the prompt lie (docs/TODO.md §6).
        if "{{N_SPECIALISTS}}" in text:
            text = text.replace("{{N_SPECIALISTS}}", str(len(CFL_SPECIALIST_NAMES)))
        self._prompt_cache[agent_name] = text
        return text

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        try:
            system_prompt = self._load_prompt(agent_name)
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Skipping agent '%s': %s", agent_name, exc)
            return None

        model = self.model_override or self.models.get(agent_name, "claude-sonnet-5")
        temperature = self.temperatures.get(agent_name, 0.0)
        effort = self.efforts.get(agent_name, self.default_effort)
        max_tokens = self.max_tokens_per_agent.get(agent_name, self.max_tokens)
        user_content = json.dumps(input_data or {}, ensure_ascii=False, indent=2)

        last_error: str | None = None
        prev_raw_excerpt: str | None = None
        raw_text = ""
        for attempt in range(1 + self.json_retries):
            if attempt > 0 and last_error:
                # Include: (a) the original input, (b) a short excerpt of
                # what the model produced last time, (c) the specific
                # parse error with position/context, (d) concrete fix
                # instructions. This gives Opus enough signal to repair
                # the exact issue instead of blindly regenerating from
                # scratch and repeating the same mistake.
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
            # when max_tokens × projected latency exceeds 10 minutes; streaming
            # lifts that cap and handles long proofs / audits reliably.
            # Thinking models get adaptive thinking + this agent's effort;
            # temperature is only sent to legacy models (Haiku 4.5). The
            # shared client also retries a retryable error (429/5xx/network/
            # broken stream) with backoff before giving up, and never
            # retries a fatal one (400/401/403/404) — TODO.md §2/§3.
            # output_schema (TODO.md §3 M): agents with a closed contract
            # (`cfl_system.lib.agent_output_schema.REQUIRED_KEYS`) get
            # structured outputs instead of "extract JSON from prose";
            # unavailable falls back to a plain call automatically.
            try:
                result = self._shared.call(
                    self.client, model=model, max_tokens=max_tokens,
                    system=system_prompt, user=user_msg,
                    effort=effort, temperature=temperature,
                    output_schema=_output_schema_for(agent_name),
                    agent=agent_name,
                )
            except FatalAPIError as exc:
                if exc.status_code == 400:
                    # A 400 that isn't a schema rejection AnthropicClient.call
                    # could itself recover from (that already retried once
                    # without the schema) is a malformed *request* for this
                    # one agent -- prompt too long, a rejected refusal-fallback
                    # header, ... -- not a dead key or missing model access.
                    # Report it as this agent's agent_error (like a
                    # RetryableAPIError below) instead of aborting the whole
                    # pipeline run.
                    elapsed = _time.monotonic() - t0
                    logger.error("[%s] 400 BadRequest after %.1fs: %s", agent_name, elapsed, exc)
                    return {
                        "agent": agent_name,
                        "status": "agent_error",
                        "verdict": None,
                        "confidence": 0.0,
                        "evidence": {},
                        "errors": [f"API error: {exc}"],
                    }
                # 401/403/404: a dead key, no access to the model, or an
                # unknown model ID -- fail fast instead of treating this as
                # one agent's problem; re-raised as the original SDK
                # exception type for backward compatibility.
                logger.error("[%s] fatal API error: %s", agent_name, exc)
                raise (exc.original if exc.original is not None else exc)
            except RetryableAPIError as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                # Return an agent_error dict (not None) so downstream nodes
                # can record the failure in state["errors"] instead of
                # silently dropping it.
                return {
                    "agent": agent_name,
                    "status": "agent_error",
                    "verdict": None,
                    "confidence": 0.0,
                    "evidence": {},
                    "errors": [f"API error: {exc}"],
                }

            raw_text = result.text
            stop_reason = result.stop_reason
            if result.usage is not None:
                tokens_in = result.usage.input_tokens
                tokens_out = result.usage.output_tokens

            elapsed = _time.monotonic() - t0

            if self.verbose:
                extra = f" stop={stop_reason}" if stop_reason and stop_reason != "end_turn" else ""
                cost = estimate_cost_usd(result.model or model, result.usage)
                cost_str = f" cost≈${cost:.4f}" if cost is not None else ""
                so_str = _format_structured_output_flag(result)
                print(
                    f"[{agent_name}] model={result.model} effort={effort} "
                    f"tokens_in={tokens_in} tokens_out={tokens_out} "
                    f"time={elapsed:.1f}s max={max_tokens}{extra}{cost_str}{so_str}",
                    file=sys.stderr, flush=True,
                )

            # A safety-classifier decline is not a parse problem: retrying or
            # JSON-repairing the (empty/partial) text cannot help.
            if stop_reason == "refusal":
                return self._refusal_error(agent_name, result)

            parsed, parse_error = _extract_json_with_error(raw_text)
            if parsed is not None:
                return parsed

            # Parse failed. Try cheap Haiku-based JSON repair BEFORE issuing
            # another full Opus retry — the raw text usually contains valid
            # content, just with prose around it / trailing commas / stray
            # fences. Haiku fixes that in ~2s for ≈ $0.01. This saves both
            # money and latency vs. re-invoking the original expensive agent.
            was_truncated = stop_reason == "max_tokens"
            repaired = self._repair_json_with_haiku(agent_name, raw_text, was_truncated)
            if repaired is not None:
                repaired["_repaired"] = True
                if was_truncated:
                    # A response that hit max_tokens and had to be patched
                    # back into shape by Haiku is not a full agent output —
                    # the model never finished its reasoning/proof. Flag it
                    # and cap its trustworthiness (docs/TODO.md §2,
                    # docs/VERDICT_POLICY.md §2 "not_verified" ceiling)
                    # instead of letting it pass downstream as a normal
                    # "success"/"verified" result.
                    repaired["_truncated"] = True
                    repaired["status"] = "inconclusive"
                    conf = repaired.get("confidence")
                    try:
                        conf = float(conf)
                    except (TypeError, ValueError):
                        conf = 0.40
                    repaired["confidence"] = min(conf, 0.40)
                return repaired

            # Distinguish truncation (max_tokens) from other parse failures.
            # If Haiku couldn't even repair a truncated response, a full
            # Opus retry with the same limit cannot help either — bail out.
            if was_truncated:
                last_error = (
                    f"Response was truncated at max_tokens={max_tokens} "
                    f"(tokens_out={tokens_out}); Haiku repair also failed. "
                    f"Parse error: {parse_error}"
                )
                logger.error("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)
                break
            # Pass the precise parse error (position + local context) back
            # to Opus on the next retry so it can fix the exact spot.
            last_error = (
                f"{parse_error} "
                f"[response length={len(raw_text)}, Haiku repair also failed]"
            )
            # Keep an excerpt of the bad response so the next retry can
            # see what it wrote and fix it rather than regenerating blind.
            prev_raw_excerpt = raw_text
            logger.warning("[%s] attempt %d: %s", agent_name, attempt + 1, last_error)

        logger.error("[%s] JSON parse failed: %s", agent_name, last_error)
        return {
            "agent": agent_name, "status": "agent_error", "verdict": None,
            "confidence": 0.0, "evidence": {},
            "errors": [last_error or "JSON parse failed"],
            "raw_response": raw_text[:2000],
        }


# _extract_json / _extract_json_with_error now come from
# agent_system.lib.llm_client (TODO.md §3 — shared across every pipeline so
# the JSON-retry error message stays uniform: position + ±40 chars of
# context, not a bare "length=N" like dcfl's old copy had drifted to).


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
        # Do not swallow this silently: a runner exception (network error,
        # bad mock file, etc.) must surface the same way an `agent_error`
        # status does, so the caller records it in state["errors"] instead
        # of the agent looking merely "not dispatched" (TODO.md §2).
        logger.warning("Agent '%s' failed: %s", agent_name, exc)
        return {
            "agent": agent_name,
            "status": "agent_error",
            "verdict": None,
            "confidence": 0.0,
            "evidence": {},
            "errors": [f"runner exception: {exc}"],
        }


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
        hints = retry_ctx.get("hints") or {}
        if isinstance(hints, dict):
            inp["retry_params"] = hints.get(agent_name)
        # Provide prior oracle_test feedback on retry (normalized)
        prior_oracle = state.get("oracle_test_result")
        if prior_oracle:
            inp["prior_oracle_test"] = _normalize_oracle_test(prior_oracle)
    return inp


def _build_reasoning_input(state: PipelineState) -> dict:
    """Build input for the reasoning agent (matches cfl_reasoning.md prompt).

    Marks proof_checker explicitly as "not_run" when it failed, to prevent
    the reasoning agent from hallucinating verification results. Empty dict
    was previously ambiguous and led to the agent inventing "5/5 checks
    passed" claims in its summary.
    """
    evidence = state.get("evidence", {})

    # Make proof_checker status explicit. The reasoning prompt enumerates
    # {verified, issues_found} only; we extend that enum here with
    # {not_run} so the agent cannot silently promote absent data to
    # "verified". Any text the agent writes in its summary must now
    # acknowledge that the checker did not execute.
    pc_out = state.get("proof_checker_output")
    if not pc_out or not isinstance(pc_out, dict) or not pc_out.get("status"):
        pc_errs = [
            e for e in state.get("errors", [])
            if isinstance(e, str) and e.startswith("proof_checker:")
        ]
        proof_checker_field: dict = {
            "status": "not_run",
            "reason": (
                "Proof checker agent did not execute successfully — "
                "do NOT claim verification in your summary or justification. "
                "You may still reach a verdict from specialists + oracle, "
                "but must state that independent verification is absent."
            ),
        }
        if pc_errs:
            proof_checker_field["errors"] = pc_errs
    else:
        proof_checker_field = pc_out

    return {
        "ir": state["ir"],
        "hypothesis": state.get("hypothesis", {}),
        "classifier_hint": state.get("classifier_output", {}),
        "specialist_outputs": {
            k: evidence[k] for k in CFL_SPECIALIST_NAMES if k in evidence
        },
        "failed_agents": _collect_failed_agents(state),
        "oracle_test": _normalize_oracle_test(state.get("oracle_test_result")),
        "claim_verification": _normalize_claim_verification(state.get("claim_verification")),
        "proof_checker": proof_checker_field,
        "retry_count": state.get("retry_round", 0),
        "inversion_count": state.get("inversions_done", 0),
        "max_retries": MAX_RETRIES,
        "max_inversions": MAX_INVERSIONS,
    }


def _collect_failed_agents(state: PipelineState) -> list[dict]:
    """Return list of {agent, error} for agents that are *currently* failed.

    `state["errors"]` is append-only across the whole run (Annotated with
    `operator.add`), so it still holds a round-1 failure message for an
    agent that succeeded on a later retry. Scanning it naively (as this
    function used to) would keep such an agent in `agents_failed` forever
    (docs/TODO.md §2: "агент, успешный при ретрае, не остаётся в
    agents_failed"). Instead: derive the *last* error message per agent
    from the log (latest round wins), then only report agents that have
    no successful result in the current state — i.e. specialists missing
    from `agent_results`, and `proof_checker`/`formalizer` missing from
    their own per-round output slots.
    """
    last_error_by_agent: dict[str, str] = {}
    for err in state.get("errors", []):
        if not isinstance(err, str) or ":" not in err:
            continue
        name, _, msg = err.partition(":")
        name = name.strip()
        if name in CFL_SPECIALIST_NAMES or name in ("proof_checker", "formalizer"):
            # Overwrite on each occurrence so the *latest* round's message
            # (not the first) is what ends up attached to the agent.
            last_error_by_agent[name] = msg.strip()

    agent_results = state.get("agent_results", {})
    proof_checker_ok = bool(state.get("proof_checker_output"))
    formalizer_ok = bool(state.get("evidence", {}).get("formalizer"))

    failed: list[dict] = []
    for name, msg in last_error_by_agent.items():
        if name in CFL_SPECIALIST_NAMES:
            currently_ok = name in agent_results
        elif name == "proof_checker":
            currently_ok = proof_checker_ok
        elif name == "formalizer":
            currently_ok = formalizer_ok
        else:
            currently_ok = False
        if not currently_ok:
            failed.append({"agent": name, "error": msg})
    return failed


def _get_action(reasoning_output: dict) -> str:
    """Extract action from reasoning output — supports both 'action' and 'decision' fields."""
    return reasoning_output.get("action") or reasoning_output.get("decision") or "done"


# Valid action values per reasoning prompt contract
_VALID_ACTIONS = {"done", "retry", "invert"}


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


def _normalize_retry_hints(hints_raw: Any) -> dict:
    """Normalize ``retry_planner.hints`` / ``reasoning.retry_plan.hints`` to
    the ``{agent_name: {...hint fields...}}`` dict every downstream consumer
    (this function's own caller, ``run_retry_planner_node``) expects.

    Accepts two shapes:

    - a **dict** keyed by specialist name (``{"pumping_cfl": {...}, ...}``)
      -- what the prompt's own "## Output Format" documents, and what the
      legacy prose-extraction fallback path (no ``output_config.format``)
      still produces, unchanged;
    - a **list** of ``{"agent": "<name>", ...hint fields...}`` objects --
      what a genuine ``output_config.format`` structured-outputs call now
      produces (``agent_output_schema._RETRY_HINTS_SCHEMA``): the API caps
      how many *union-typed* (nullable/anyOf) parameters a schema may carry
      (separately from its optional-parameter cap -- both confirmed live,
      see ``agent_system/lib/testing/schema_checks.py``'s module
      docstring), and a *map* with one subschema copy per specialist
      multiplies either kind of per-field flexibility by 9 copies; an
      *array* of one shared item schema does not -- the schema is walked
      once regardless of how many elements the model actually returns. The
      trade-off is that a structured-outputs call must now name the agent
      explicitly inside each hint object instead of as its dict key, so
      this immediately restores the dict shape every consumer already
      expects, before any of them see it.

    Anything else (not a dict, not a list, a list entry missing ``"agent"``
    or not a dict at all) is dropped rather than raising -- the LLM may
    still produce malformed JSON via the legacy path, same as before this
    normalization existed.
    """
    if isinstance(hints_raw, dict):
        return hints_raw
    if isinstance(hints_raw, list):
        return {
            item["agent"]: item for item in hints_raw
            if isinstance(item, dict) and isinstance(item.get("agent"), str)
        }
    return {}


def _normalize_morphism_mapping(out: dict | None) -> dict | None:
    """Normalize ``morphism``'s ``evidence.morphism.mapping`` back to the
    ``{symbol: image}`` dict every worked example in ``cfl_morphism.md`` and
    every consumer downstream (the renderer's generic evidence panel)
    expects, immediately after parsing -- same idea as
    ``_normalize_retry_hints`` for ``reasoning``/``retry_planner``'s
    ``hints``.

    A genuine ``output_config.format`` call now produces an **array** of
    ``{"symbol": ..., "image": ...}`` objects instead (see
    ``cfl_system.lib.agent_output_schema._MORPHISM_MAPPING_SCHEMA``'s
    docstring for why: ``additionalProperties: false`` can't leave open a
    key set that varies per task, so the mapping had no schema at all until
    it was remodelled as an array of a fixed shape). The legacy
    prose-extraction fallback path (no schema) still produces the ``{symbol:
    image}`` dict shape unchanged, so this only touches the array shape;
    anything else (missing/not-a-dict `evidence`, `evidence.morphism`,
    or `mapping`, or a list entry missing `symbol`/`image`) is left alone
    rather than raising -- the LLM may still emit malformed JSON via the
    legacy path, same as before this normalization existed."""
    if not isinstance(out, dict):
        return out
    evidence = out.get("evidence")
    if not isinstance(evidence, dict):
        return out
    morphism = evidence.get("morphism")
    if not isinstance(morphism, dict):
        return out
    mapping = morphism.get("mapping")
    if not isinstance(mapping, list):
        return out
    morphism["mapping"] = {
        item["symbol"]: item["image"] for item in mapping
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
        and isinstance(item.get("image"), str)
    }
    return out


def _normalize_claim_verification(raw: dict | None) -> dict:
    """Normalize claim_verification for the reasoning agent prompt.

    docs/VERDICT_POLICY.md §1: claim_verifier returns verification_status in
    {well_formed, bounded_pass, refuted, not_verified} (never "verified" —
    that trust level is reserved for deterministic full proofs elsewhere).
    Earlier this collapsed that taxonomy into a binary {verified,
    issues_found} for the reasoning prompt — that literally told the LLM
    "verified" for a merely well_formed (structure-only) claim, which is
    exactly the honesty gap docs/VERDICT_POLICY.md exists to close (a
    self-reported "verified" must never leak into the rendered proof text
    just because the structure parsed). The prompt contract (cfl_reasoning.md)
    now enumerates the real trust taxonomy, so `status` here IS the trust
    label, unmodified — "verified" is passed through only when the
    verifier itself actually reached that tier (it doesn't yet, for any
    CFL claim_verifier check), never as a stand-in for "structurally OK".
    """
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for agent, cv in raw.items():
        if isinstance(cv, dict):
            normalized = dict(cv)
            vs = normalized.get("verification_status") or normalized.get("trust")
            normalized["status"] = vs if vs in _TRUST_LEVEL_VALUES else "not_verified"
            result[agent] = normalized
        else:
            result[agent] = cv
    return result


_TRUST_LEVEL_VALUES = frozenset({"refuted", "not_verified", "well_formed", "bounded_pass", "verified"})


# ---------------------------------------------------------------------------
# Verdict gate (docs/VERDICT_POLICY.md) — deterministic post-reasoning check
# ---------------------------------------------------------------------------

def _trust_rank(trust: str | None) -> int:
    return _TRUST_RANK.get(trust or "not_verified", 0)


def _oracle_trust(oracle_result: dict | None) -> str:
    """Trust contributed by the shared cfg_builder/pda_builder oracle_test."""
    if not isinstance(oracle_result, dict):
        return "not_verified"
    t = oracle_result.get("trust")
    if isinstance(t, str):
        return t
    status = oracle_result.get("status")
    if status == "pass":
        return "bounded_pass"
    if status == "grammar_incorrect":
        return "refuted"
    return "not_verified"


def _collect_agent_trust(state: PipelineState) -> dict[str, str]:
    """trust per agent (docs/VERDICT_POLICY.md §5): constructive agents get
    the shared oracle_test trust (there is one oracle_test per round, covering
    whichever of cfg_builder/pda_builder produced an artifact); every other
    agent gets its own claim_verification trust.

    A constructive agent only gets the shared oracle_test trust when it
    actually contributed the artifact that trust describes: `status ==
    "success"` AND a usable grammar/PDA is present. Without this guard, a
    `pda_builder` that errored out (or never produced a PDA) would still be
    stamped with cfg_builder's `bounded_pass`/`refuted` verdict just because
    both names are in `agent_results` -- which then lets a refuted
    cfg_builder hide behind pda_builder's borrowed trust in the R3'
    cross-check below (reviewer finding: contradiction stuck at `None`/0.5
    instead of resolving to `non_cfl`).
    """
    trust: dict[str, str] = {}
    agent_results = state.get("agent_results") or {}
    ot_trust = _oracle_trust(state.get("oracle_test_result"))
    for name in _CONSTRUCTIVE_AGENTS:
        output = agent_results.get(name)
        if not isinstance(output, dict) or output.get("status") != "success":
            continue
        if name == "cfg_builder" and not _agent_grammar(output):
            continue
        if name == "pda_builder" and not _agent_pda(output)[0]:
            continue
        trust[name] = ot_trust
    # claim_verification has a generic not_verified fallback entry for ANY
    # dispatched agent without a dedicated verifier (cfg_builder, pda_builder
    # included) — that fallback must never clobber the oracle-derived trust
    # constructive agents already got above.
    cv = state.get("claim_verification") or {}
    for name, v in cv.items():
        if name in _CONSTRUCTIVE_AGENTS:
            continue
        if isinstance(v, dict):
            trust[name] = v.get("trust") or v.get("verification_status") or "not_verified"
    return trust


def _strongest_trust(trust_map: dict[str, str], agents: tuple[str, ...]) -> tuple[str, bool]:
    """Return (best non-refuted trust among `agents`, whether any is refuted)."""
    best = "not_verified"
    any_refuted = False
    for name in agents:
        t = trust_map.get(name)
        if not t:
            continue
        if t == "refuted":
            any_refuted = True
            continue
        if _trust_rank(t) > _trust_rank(best):
            best = t
    return best, any_refuted


def _destructive_agent_argues_non_cfl(state: PipelineState, name: str) -> bool:
    """Whether agent `name`'s own output actually argues non_cfl.

    docs/VERDICT_POLICY.md R1: `_DESTRUCTIVE_AGENTS` includes agents like
    `decomposition` (schema: verdict in {"cfl", null} — it argues FOR cfl by
    exhibiting a CFL-closed decomposition) and `parikh` (schema: verdict in
    {"non_cfl", null}). A well_formed *structural* verification of
    decomposition's claim says nothing about which side that claim
    supports — a well-formed pro-CFL decomposition must never be counted as
    destructive evidence for non_cfl just because it passed structural
    checks (that was the grammar_filter_49 + decomposition regression: a
    well-formed decomposition arguing CFL was silently read as an
    unrelated destructive claim). Only an agent whose own verdict is
    literally "non_cfl", with status=="success", counts here; for `parikh`
    that verdict must additionally be backed by `is_semilinear is False` in
    its own evidence (not just the LLM's textual conclusion).
    """
    agent_results = state.get("agent_results") or {}
    out = agent_results.get(name)
    if not isinstance(out, dict) or out.get("status") != "success":
        return False
    if _normalize_verdict(out.get("verdict")) != "non_cfl":
        return False
    if name == "parikh":
        evidence = out.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("is_semilinear") is not False:
            return False
    return True


def _strongest_destructive_trust(
    state: PipelineState, trust_map: dict[str, str],
) -> tuple[str, bool]:
    """Like `_strongest_trust`, but restricted to agents whose own verdict
    actually argues non_cfl (see `_destructive_agent_argues_non_cfl`)."""
    eligible = tuple(
        name for name in _DESTRUCTIVE_AGENTS
        if _destructive_agent_argues_non_cfl(state, name)
    )
    return _strongest_trust(trust_map, eligible)


def _agent_grammar(output: dict | None) -> dict | None:
    """cfg_builder's grammar, wherever it landed (flat or evidence-wrapped —
    same lookup as `assemble_result_node`/`assemble_early_failure`)."""
    if not isinstance(output, dict):
        return None
    return output.get("grammar") or (output.get("evidence") or {}).get("grammar")


def _agent_pda(output: dict | None) -> tuple[dict | None, str | None]:
    """pda_builder's pda + acceptance_mode, wherever they landed."""
    if not isinstance(output, dict):
        return None, None
    evidence = output.get("evidence") or {}
    pda = output.get("pda") or evidence.get("pda")
    mode = output.get("acceptance_mode") or evidence.get("acceptance_mode")
    return pda, mode


def _constructive_artifact_oracle(agent_results: dict, agent_name: str):
    """Build a membership oracle from the constructive artifact `agent_name`
    actually produced (grammar -> CYK, PDA -> simulator), or None if it isn't
    usable. Never raises."""
    output = agent_results.get(agent_name)
    try:
        if agent_name == "cfg_builder":
            grammar = _agent_grammar(output)
            if not grammar:
                return None
            return grammar_oracle(grammar)
        if agent_name == "pda_builder":
            pda, mode = _agent_pda(output)
            if not pda:
                return None
            return pda_oracle(normalize_agent_pda(pda, acceptance_mode=mode))
    except Exception:
        return None
    return None


def _cross_check_r3prime(
    state: PipelineState,
    trust_map: dict[str, str],
    constructive_trust: str,
    destructive_trust: str,
) -> dict[str, Any]:
    """docs/VERDICT_POLICY.md R3' — deterministic cross-check attempted before
    falling back to an unresolved R3 contradiction.

    Runs the destructive proof's own witness words (already instantiated by
    the step-2 semantic checks in claim_verifier.py — reused here via
    `claim_verification[agent]["details"]["destructive_witnesses"]`, never
    re-derived) through both the task's language oracle and EVERY
    constructive artifact still in play (grammar via CYK / PDA via the
    simulator) — not just the first one found. A shared oracle_test trust
    can cover more than one constructive agent (cfg_builder AND pda_builder
    both bounded_pass), and testing only the first by iteration order would
    let a genuinely-wrong second artifact hide behind the first's refutation
    instead of being refuted itself (reviewer finding):
      (a) a witness the destructive proof claims is NOT in L, but a
          constructive artifact accepts -> THAT artifact is `refuted`;
          a witness claimed IN L that it rejects -> also `refuted`. Each
          constructive agent is checked independently.
      (b) a witness claimed NOT in L that the oracle actually says IS in L
          (or vice versa) -> the destructive proof itself is `refuted`.
    Never raises; any missing evidence (no witnesses, no oracle, no usable
    artifact) just leaves `performed=False` and nothing gets refuted.
    """
    result: dict[str, Any] = {
        "performed": False,
        "constructive_agents": [],
        "destructive_agent": None,
        "constructive_refuted_agents": [],
        "destructive_refuted": False,
        "constructive_reasons": {},
        "destructive_reason": None,
        "counterexamples": [],
    }

    # Every constructive agent that currently clears its threshold (not
    # already refuted) and actually has a usable artifact -- each is tested
    # on its own merits, not just the first match.
    constructive_agents = [
        a for a in _CONSTRUCTIVE_AGENTS
        if trust_map.get(a) not in (None, "refuted")
        and _constructive_artifact_oracle(state.get("agent_results") or {}, a) is not None
    ]
    destructive_agent = next(
        (
            a for a in _DESTRUCTIVE_AGENTS
            if trust_map.get(a) == destructive_trust and _destructive_agent_argues_non_cfl(state, a)
        ),
        None,
    )
    result["constructive_agents"] = constructive_agents
    result["destructive_agent"] = destructive_agent
    if not constructive_agents or not destructive_agent:
        return result

    claim_verification = state.get("claim_verification") or {}
    witnesses = ((claim_verification.get(destructive_agent) or {}).get("details") or {}).get(
        "destructive_witnesses"
    )
    if not isinstance(witnesses, list) or not witnesses:
        return result

    try:
        lang_oracle = cfl_oracle_from_ir(state.get("ir") or {})
    except Exception:
        return result
    if getattr(lang_oracle, "is_approximate", False):
        return result

    artifact_oracles = {
        a: _constructive_artifact_oracle(state.get("agent_results") or {}, a)
        for a in constructive_agents
    }
    artifact_oracles = {a: o for a, o in artifact_oracles.items() if o is not None}
    if not artifact_oracles:
        return result

    result["performed"] = True

    for w in witnesses:
        if not isinstance(w, dict):
            continue
        word = w.get("word")
        expected_in_l = w.get("expected_in_l")
        if not isinstance(word, str) or not word or not isinstance(expected_in_l, bool):
            continue
        try:
            oracle_says = bool(lang_oracle(word))
        except Exception:
            continue

        if not result["destructive_refuted"] and oracle_says != expected_in_l:
            result["destructive_refuted"] = True
            result["destructive_reason"] = (
                f"witness '{word}' (source: {w.get('source')}) claimed "
                f"{'in L' if expected_in_l else 'NOT in L'}, but the oracle says "
                f"{'in L' if oracle_says else 'NOT in L'}"
            )
            result["counterexamples"].append({"word": word, "issue": result["destructive_reason"]})

        if oracle_says == expected_in_l:
            # Only test artifacts against a witness the oracle itself just
            # confirmed -- an oracle-refuted witness says nothing about them.
            for agent, artifact_oracle in artifact_oracles.items():
                if agent in result["constructive_refuted_agents"]:
                    continue
                try:
                    artifact_says = bool(artifact_oracle(word))
                except Exception:
                    continue
                if artifact_says != expected_in_l:
                    result["constructive_refuted_agents"].append(agent)
                    verb = "rejects" if expected_in_l else "accepts"
                    reason = (
                        f"artifact {verb} '{word}' (source: {w.get('source')}), which the "
                        f"oracle says is {'in L' if expected_in_l else 'NOT in L'}"
                    )
                    result["constructive_reasons"][agent] = reason
                    result["counterexamples"].append({"word": word, "issue": reason})

        if result["destructive_refuted"] and len(result["constructive_refuted_agents"]) == len(artifact_oracles):
            break

    return result


def apply_verdict_gate(state: PipelineState) -> dict:
    """Deterministic gate applied to the reasoning agent's proposed verdict.

    Implements docs/VERDICT_POLICY.md rules R1 (failure != refutation), R2
    (constructive verdict needs a >= bounded_pass artifact), R3 (contradiction
    between constructive and destructive evidence), R4 (log every downgrade),
    and the confidence ceiling of §2. Never raises `proof_verified` from an
    LLM's own self-assessment — only from the trust levels computed here.

    Returns the fields to merge into state: an updated `reasoning_output`
    (action/verdict/confidence adjusted in place of the LLM's proposal when a
    rule is violated), plus `trust` and `verdict_gate` for the final result.
    """
    reasoning = dict(state.get("reasoning_output") or {})
    trust_map = _collect_agent_trust(state)

    action = _get_action(reasoning)
    verdict = _normalize_verdict(reasoning.get("verdict"))
    confidence = _clamp_confidence(reasoning.get("confidence"))

    constructive_trust, constructive_refuted = _strongest_trust(trust_map, _CONSTRUCTIVE_AGENTS)
    destructive_trust, destructive_refuted = _strongest_destructive_trust(state, trust_map)

    downgrades: list[str] = []
    basis_note: str | None = None

    retry_round = state.get("retry_round", 0)
    budget_left = retry_round < MAX_RETRIES

    def _downgrade_to_inconclusive_or_retry(reason: str) -> None:
        nonlocal action, verdict, confidence, basis_note
        if budget_left:
            downgrades.append(f"{reason} -> retry")
            action = "retry"
            return
        # docs/VERDICT_POLICY.md R4' — retry budget exhausted (or the
        # reasoning agent's proposal is inadmissible and there is no budget
        # left to fix it): the gate does NOT default straight to
        # inconclusive. It picks the strongest admissible basis still
        # standing — destructive claim >= well_formed (not refuted) first,
        # then a constructive artifact >= bounded_pass, only then
        # inconclusive. `failure` stays reserved for technical failures
        # (API errors, invalid input), never for "reasoning argued the
        # wrong side" (precedent: cfl-07/cfl-12 eval live-run ending in
        # `failure 0.0` despite a well_formed destructive proof being on
        # record). The confidence ceiling by trust (§2, applied further
        # below from the unchanged constructive_trust/destructive_trust)
        # takes care of the cap; this function only picks the verdict.
        action = "done"
        # docs/VERDICT_POLICY.md R4' — `destructive_trust`/`constructive_trust`
        # (from `_strongest_trust`/`_strongest_destructive_trust`) are already
        # computed over only the NON-refuted agents on each side; the
        # `_refuted` flags mean "at least one agent on this side is refuted"
        # (any agent, not necessarily the one behind the best trust), so
        # gating on them here on top of the rank check would wrongly throw
        # away a real admissible basis whenever some OTHER, unrelated agent
        # on the same side happened to be refuted (reviewer finding:
        # ogden=refuted + pumping_cfl=well_formed was being read as "no
        # admissible destructive basis" even though pumping_cfl's own
        # well_formed claim is exactly what R1/R2 asks for).
        if _trust_rank(destructive_trust) >= _trust_rank("well_formed"):
            verdict = "non_cfl"
            downgrades.append(
                f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                f"destructive claim (trust={destructive_trust}) -> non_cfl"
            )
        elif _trust_rank(constructive_trust) >= _trust_rank("bounded_pass"):
            verdict = "cfl"
            downgrades.append(
                f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                f"constructive artifact (trust={constructive_trust}) -> cfl"
            )
        else:
            downgrades.append(
                f"{reason} -> retry budget exhausted -> strongest admissible basis: inconclusive"
            )
            verdict = None
            confidence = min(confidence, 0.40)

    if action == "done" and verdict == "cfl":
        # R2: constructive verdict requires a >= bounded_pass artifact that
        # was not itself refuted by the oracle.
        if constructive_refuted or _trust_rank(constructive_trust) < _trust_rank("bounded_pass"):
            basis_note = "constructive_failure_only" if constructive_refuted else "no_verified_artifact"
            _downgrade_to_inconclusive_or_retry(
                "reasoning proposed done/cfl without a >= bounded_pass constructive artifact"
            )

    elif action == "done" and verdict == "non_cfl":
        # R1/R4: a destructive verdict needs a destructive claim with trust
        # >= well_formed — a constructive agent's failure is NOT evidence.
        # destructive_trust is already the best NON-refuted agent's trust
        # (a different, refuted agent must not veto a genuinely well_formed
        # one — docs/VERDICT_POLICY.md §4/R7 still surfaces the refuted
        # agent separately for the retry planner via `trust`/`basis`).
        if _trust_rank(destructive_trust) < _trust_rank("well_formed"):
            basis_note = "constructive_failure_only" if constructive_refuted else "no_verified_artifact"
            _downgrade_to_inconclusive_or_retry(
                "reasoning proposed done/non_cfl without a >= well_formed destructive claim"
            )

    # R3/R3': contradiction — both sides clear their threshold (destructive_
    # trust/constructive_trust already exclude any individually-refuted
    # agent, per _strongest_trust).
    contradiction = (
        _trust_rank(constructive_trust) >= _trust_rank("bounded_pass")
        and _trust_rank(destructive_trust) >= _trust_rank("well_formed")
    )
    contradiction_details: dict[str, Any] | None = None
    resolved_cap = _CONTRADICTION_CONFIDENCE_CAP
    if contradiction:
        orig_constructive_trust = constructive_trust
        orig_destructive_trust = destructive_trust

        # R3' — try to resolve the contradiction deterministically before
        # falling back to "inconclusive": run the destructive proof's own
        # witness words (reused from claim_verifier's step-2 checks) through
        # the language oracle AND the constructive artifact.
        cross_check = _cross_check_r3prime(state, trust_map, constructive_trust, destructive_trust)

        constructive_any_refuted = bool(cross_check["constructive_refuted_agents"])
        if constructive_any_refuted:
            for agent in cross_check["constructive_refuted_agents"]:
                trust_map[agent] = "refuted"
                downgrades.append(
                    f"R3' cross-check refuted constructive artifact "
                    f"({agent}): {cross_check['constructive_reasons'][agent]}"
                )
            constructive_trust, constructive_refuted = _strongest_trust(trust_map, _CONSTRUCTIVE_AGENTS)
        if cross_check["destructive_refuted"]:
            trust_map[cross_check["destructive_agent"]] = "refuted"
            downgrades.append(
                f"R3' cross-check refuted destructive claim "
                f"({cross_check['destructive_agent']}): {cross_check['destructive_reason']}"
            )
            destructive_trust, destructive_refuted = _strongest_destructive_trust(state, trust_map)

        contradiction_details = {
            "constructive": {"agent": cross_check["constructive_agents"], "trust": orig_constructive_trust},
            "destructive": {"agent": cross_check["destructive_agent"], "trust": orig_destructive_trust},
            "cross_check": {k: v for k, v in cross_check.items()
                             if k not in ("constructive_agents", "destructive_agent")},
        }

        contradiction = (
            _trust_rank(constructive_trust) >= _trust_rank("bounded_pass")
            and _trust_rank(destructive_trust) >= _trust_rank("well_formed")
        )

        if not contradiction:
            # R3' resolved it: the surviving side wins, capped by its OWN
            # trust tier (not the 0.50 unresolved-contradiction cap).
            if constructive_any_refuted and not cross_check["destructive_refuted"]:
                verdict = "non_cfl"
                basis_note = "r3prime_cross_check"
            elif cross_check["destructive_refuted"] and not constructive_any_refuted:
                verdict = "cfl"
                basis_note = "r3prime_cross_check"
            else:
                # Both refuted by cross-check (or cross-check never ran) --
                # no valid basis left either way.
                verdict = None
                basis_note = "r3prime_both_refuted" if cross_check["performed"] else basis_note
                resolved_cap = _CONFIDENCE_CAP_BY_TRUST["not_verified"]
            action = "done"

    if contradiction:
        # docs/VERDICT_POLICY.md R3 (post-R3'): "bounded_pass vs well_formed"
        # no longer settles the dispute by rank -- only a `verified` side
        # wins (capped at 0.85, not 0.98), otherwise it stays inconclusive.
        downgrades.append(
            "contradiction: constructive artifact (trust="
            f"{constructive_trust}) and destructive claim (trust="
            f"{destructive_trust}) both clear their threshold, unresolved by R3' cross-check"
        )
        if constructive_trust == "verified" and destructive_trust != "verified":
            verdict = "cfl"
            resolved_cap = _CONTRADICTION_VERIFIED_CAP
        elif destructive_trust == "verified" and constructive_trust != "verified":
            verdict = "non_cfl"
            resolved_cap = _CONTRADICTION_VERIFIED_CAP
        else:
            verdict = None
            basis_note = basis_note or "contradiction"
            resolved_cap = _CONTRADICTION_CONFIDENCE_CAP
        confidence = min(confidence, resolved_cap)

    # §2: confidence ceiling by the strongest basis actually behind the
    # (possibly just-adjusted) verdict.
    strongest = constructive_trust if _trust_rank(constructive_trust) >= _trust_rank(destructive_trust) else destructive_trust
    cap = _CONFIDENCE_CAP_BY_TRUST.get(strongest, 0.40)
    if contradiction:
        cap = min(cap, resolved_cap)
    confidence = min(confidence, cap)

    basis: list[dict] = [
        {"agent": name, "trust": t} for name, t in sorted(trust_map.items())
    ]

    # proof_verified must come only from these deterministic trust levels,
    # never from proof_checker's own self-assessment (docs/VERDICT_POLICY.md §3).
    # The strongest trust actually behind the (possibly gated) verdict —
    # renderers use this to show the honest word-label (docs/VERDICT_POLICY.md
    # §5: "проверено полностью" / "проверено выборочно" / "корректно
    # оформлено" / "опровергнуто"), and the green "fully verified" banner is
    # reserved for `trust == "verified"` specifically — bounded_pass is a
    # real, deterministic check, but only over a finite sample, not a proof.
    if verdict == "cfl":
        basis_trust = constructive_trust if not constructive_refuted else "refuted"
    elif verdict == "non_cfl":
        basis_trust = destructive_trust
    else:
        basis_trust = "not_verified"
    if contradiction:
        basis_trust = "not_verified"
    proof_verified = basis_trust == "verified"

    reasoning["action"] = action
    reasoning["decision"] = action
    reasoning["verdict"] = verdict
    reasoning["confidence"] = confidence
    if action == "retry" and not reasoning.get("retry_plan"):
        # Synthesize a minimal retry plan so run_retry_planner_node's
        # fallback path (when the LLM planner is unavailable) has somewhere
        # to point — R7: retry the side that failed its threshold.
        if verdict == "cfl" or (verdict is None and constructive_refuted):
            agents_to_retry = [a for a in _CONSTRUCTIVE_AGENTS if trust_map.get(a) != "bounded_pass"]
        else:
            agents_to_retry = [
                a for a in _DESTRUCTIVE_AGENTS
                if _trust_rank(trust_map.get(a)) < _trust_rank("well_formed") or trust_map.get(a) == "refuted"
            ] or list(_DESTRUCTIVE_AGENTS)
        reasoning["retry_plan"] = {
            "agents_to_retry": agents_to_retry,
            "hints": {},
            "reason": downgrades[-1] if downgrades else "verdict_gate",
        }

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): surface any skipped-retry-
    # due-to-call-cap notes (run_specialist_node) here too, deduplicated (the
    # same agent may have been capped across more than one retry round).
    for note in state.get("call_cap_notes") or []:
        if note not in downgrades:
            downgrades.append(note)

    verdict_gate: dict[str, Any] = {
        "basis": basis,
        "basis_trust": basis_trust,
        "contradiction": contradiction,
        "downgrades": downgrades,
        "confidence_cap": cap,
        "proof_verified": proof_verified,
    }
    if basis_note:
        verdict_gate["basis_note"] = basis_note
    if contradiction_details is not None:
        # docs/VERDICT_POLICY.md §5/R3': both sides' trust and the R3'
        # cross-check outcome, so the report always shows both proofs when
        # a contradiction was raised (whether or not it got resolved).
        verdict_gate["contradiction_details"] = contradiction_details

    return {"reasoning_output": reasoning, "trust": trust_map, "verdict_gate": verdict_gate}


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

    trust_map = state.get("trust") or {}
    specialist_outputs_out: dict[str, dict] = {}
    for name in CFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
            out = dict(out)
            if name in trust_map:
                out["trust"] = trust_map[name]
            specialist_outputs_out[name] = out

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": "failure" if errors else "inconclusive",
            "confidence": 0.0,
            "proof": None,
            "proof_verified": False,
            "grammar": grammar,
            "pda": pda,
            "oracle_test": oracle_report,
            "agents_used": sorted(agent_results.keys()),
            "agents_failed": _collect_failed_agents(state),
            "specialist_outputs": specialist_outputs_out,
            "claim_verification": state.get("claim_verification") or {},
            "verdict_gate": state.get("verdict_gate") or {},
            "hints_for_human": [],
            "classifier_hint": state.get("classifier_output") or {},
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

    Cost ceiling: a specialist that has already been called
    ``MAX_CALLS_PER_AGENT`` times for this task (counted from the full,
    never-reset ``specialist_outputs`` history) is not called again -- the
    retry planner may still have named it, but the call is skipped and a
    note is recorded for ``verdict_gate.downgrades`` instead of making
    another LLM call.
    """
    agent_name = state["_specialist_name"]
    prior_calls = sum(1 for n, _ in state.get("specialist_outputs", []) if n == agent_name)
    if prior_calls >= MAX_CALLS_PER_AGENT:
        log_msg(
            state,
            f"  specialist: {agent_name} call cap reached "
            f"({prior_calls}/{MAX_CALLS_PER_AGENT}), skipping retry",
        )
        return {
            "call_cap_notes": [
                f"agent {agent_name} call cap reached ({MAX_CALLS_PER_AGENT} calls)"
            ],
        }
    log_msg(state, f"  specialist: {agent_name}...")
    inp = _build_specialist_input(state, agent_name)
    out = _run_agent(state, agent_name, inp)
    if agent_name == "morphism":
        out = _normalize_morphism_mapping(out)

    # Treat agent_error as a failed run — emit None marker so collect
    # can drop previous stale results for this agent on retry. Also
    # record the error in state["errors"] so the final result surfaces
    # the failure instead of silently pretending the agent was skipped.
    if out is not None and out.get("status") == "agent_error":
        err_msgs = out.get("errors") or [f"{agent_name} returned agent_error"]
        log_msg(state, f"  {agent_name} returned agent_error: {err_msgs}")
        return {
            "specialist_outputs": [(agent_name, None)],
            "errors": [f"{agent_name}: {m}" for m in err_msgs],
        }

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
        from cfl_system.lib.cfl_oracle import UnsupportedOracleKindError
    except ImportError:
        UnsupportedOracleKindError = None
    try:
        oracle_fn = cfl_oracle_from_ir(state["ir"])
        return {"oracle_fn": oracle_fn, "oracle_ok": True}
    except Exception as exc:
        if UnsupportedOracleKindError is not None and isinstance(exc, UnsupportedOracleKindError):
            log_msg(state, f"  oracle not applicable: {exc}")
        else:
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

    # Skip if the oracle is approximate (e.g. natural_language_filter) —
    # running a word-membership comparison against an approximate oracle
    # would produce false passes.
    if getattr(oracle_fn, "is_approximate", False):
        reason = getattr(oracle_fn, "approximation_reason", "approximate oracle")
        log_msg(state, f"  oracle is approximate ({reason}), skipping oracle test")
        return {
            "oracle_test_result": {
                "status": "not_applicable",
                "details": f"oracle is approximate: {reason}",
            }
        }

    constructive_evidence: dict[str, Any] = {}
    for name in _CONSTRUCTIVE_AGENTS:
        output = state.get("agent_results", {}).get(name)
        if output is None:
            continue
        ev = output.get("evidence", {})
        for key in ("grammar", "pda"):
            if key in output:
                constructive_evidence[key] = output[key]
            if isinstance(ev, dict) and key in ev:
                constructive_evidence[key] = ev[key]
        if name == "pda_builder" and isinstance(constructive_evidence.get("pda"), dict):
            # Agent PDAs use the prompt's textbook conventions (topmost-first
            # push, sibling acceptance_mode); convert for the simulator.
            mode = (ev.get("acceptance_mode") if isinstance(ev, dict) else None) \
                or output.get("acceptance_mode")
            constructive_evidence["pda"] = normalize_agent_pda(
                constructive_evidence["pda"], mode,
            )

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
    errors_to_add: list[str] = []
    if output is not None and output.get("status") == "agent_error":
        err_msgs = output.get("errors") or ["proof_checker returned agent_error"]
        errors_to_add = [f"proof_checker: {m}" for m in err_msgs]
        log_msg(state, f"  proof_checker failed: {err_msgs}")
        output = None
    result: dict[str, Any] = {"proof_checker_output": output or {}}
    if errors_to_add:
        result["errors"] = errors_to_add
    return result


def run_reasoning_node(state: PipelineState) -> dict:
    log_msg(state, "run_reasoning_node...")
    # Clear any transient retry_context from a prior round so the
    # planner's should_invert signal doesn't leak into a subsequent
    # reasoning->invert path.
    state["retry_context"] = {}

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
    #   - action not in enum {done, retry, invert}
    #   - action="done" but verdict is missing (contract violation)
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
    return {"reasoning_output": output, "retry_context": {}}


def verdict_gate_node(state: PipelineState) -> dict:
    """Deterministic gate (docs/VERDICT_POLICY.md) run right after reasoning.

    Validates the reasoning agent's proposed action/verdict/confidence
    against R1-R4 and the confidence ceiling of §2, downgrading in place
    when the underlying trust doesn't support what was proposed. Runs before
    `decide_retry`, which reads the (possibly gated) action from
    `reasoning_output`.
    """
    log_msg(state, "verdict_gate_node...")
    gate = apply_verdict_gate(state)
    if state.get("verbose"):
        vg = gate["verdict_gate"]
        if vg.get("downgrades"):
            log_msg(state, f"  downgrades: {vg['downgrades']}")
        log_msg(
            state,
            f"  gated action={_get_action(gate['reasoning_output'])} "
            f"verdict={gate['reasoning_output'].get('verdict')} "
            f"confidence={gate['reasoning_output'].get('confidence')} "
            f"contradiction={vg.get('contradiction')}",
        )
    return gate


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
    If verdict fails to normalize, confidence is reset to 0 to avoid
    publishing a high-confidence inconclusive result.
    """
    normalized_verdict = _normalize_verdict(verdict)
    if verdict and normalized_verdict is None:
        # Invalid verdict — force confidence to 0
        confidence = 0.0
    return {
        "agent": "reasoning",
        "action": action,
        "decision": action,  # mirror for both field names
        "verdict": normalized_verdict,
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

    # docs/VERDICT_POLICY.md §1: claim_verifier reports well_formed/bounded_pass
    # for a passing claim (never "verified" — that trust level is reserved for
    # full deterministic proofs). Recognize both the current vocabulary and
    # the literal string "verified" (kept for callers/tests that inject a
    # claim_verification dict directly rather than through claim_verifier).
    verified_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict)
        and v.get("verification_status") in ("verified", "well_formed", "bounded_pass")
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

    # If there are refuted claims AND retries are still available, retry
    # instead of committing to a verdict based on stale evidence.
    retry_round = state.get("retry_round", 0)
    if refuted_count > 0 and retry_round < MAX_RETRIES:
        refuted_agents = [
            name for name, v in verifications.items()
            if isinstance(v, dict) and v.get("verification_status") == "refuted"
        ]
        return _fallback_reasoning_result(
            action="retry", verdict=None, confidence=0.25,
            summary=f"{refuted_count} claims refuted; retrying {refuted_agents}",
            retry_plan={"agents_to_retry": refuted_agents, "hints": {}},
        )

    hyp = hypothesis.get("hypothesis", "unknown")
    hyp_conf = _clamp_confidence(hypothesis.get("confidence", 0.0))

    # Only trust hypothesis-based done if there are NO refuted claims
    if (
        hyp in ("cfl", "non_cfl")
        and hyp_conf >= 0.7
        and verified_count > 0
        and refuted_count == 0
    ):
        return _fallback_reasoning_result(
            action="done", verdict=hyp,
            confidence=min(hyp_conf + 0.05 * verified_count, 0.95),
            summary=f"Hypothesis '{hyp}' with {verified_count} verified claims",
            primary_evidence="hypothesis",
        )

    # Pick best agent verdict with clamped confidence
    # Skip agents whose claim was refuted
    refuted_agents_set = {
        name for name, v in verifications.items()
        if isinstance(v, dict) and v.get("verification_status") == "refuted"
    }
    best_agent: str | None = None
    best_verdict: str | None = None
    best_conf = 0.0
    for name, output in agent_results.items():
        if name in refuted_agents_set:
            continue
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
    # R7 (docs/VERDICT_POLICY.md): the planner sees per-agent trust so it can
    # prioritize retrying `refuted` agents (with their counterexamples,
    # already carried in oracle_test/specialist_outputs) over merely
    # `not_verified` ones.
    reasoning_with_context["trust"] = state.get("trust", {})

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
        hints_raw = output.get("hints") or output.get("retry_params") or {}
        should_invert = bool(output.get("should_invert_hypothesis", False))
        max_retries_remaining = output.get("max_retries_remaining")
    else:
        if output is not None:
            log_msg(state, "  retry_planner returned agent_error, using reasoning.retry_plan")
        r_plan = reasoning.get("retry_plan") or {}
        if not isinstance(r_plan, dict):
            r_plan = {}
        agents_to_retry = r_plan.get("agents_to_retry")
        hints_raw = r_plan.get("hints") or {}
        should_invert = bool(r_plan.get("should_invert_hypothesis", False))
        max_retries_remaining = r_plan.get("max_retries_remaining")

    # Validate and coerce types defensively (LLM may produce malformed JSON).
    # `hints_raw` may be the legacy per-agent dict OR (a genuine
    # output_config.format call) a list of {"agent": ..., ...} objects --
    # see `_normalize_retry_hints`.
    hints_dict = _normalize_retry_hints(hints_raw)
    if hints_raw and not hints_dict:
        log_msg(
            state,
            f"  hints in unrecognized shape (type={type(hints_raw).__name__}), ignoring",
        )
    # Ensure per-agent hints are also dicts
    hints = {k: v for k, v in hints_dict.items() if isinstance(v, dict)}

    if max_retries_remaining is not None:
        try:
            max_retries_remaining = int(max_retries_remaining)
        except (TypeError, ValueError):
            log_msg(state, f"  max_retries_remaining is not an int, ignoring")
            max_retries_remaining = None

    if agents_to_retry is not None and not isinstance(agents_to_retry, list):
        log_msg(state, f"  agents_to_retry is not a list, ignoring")
        agents_to_retry = None
    if isinstance(agents_to_retry, list):
        # Keep only string entries
        agents_to_retry = [a for a in agents_to_retry if isinstance(a, str)]

    # Validate agents_to_retry: filter unknown names to prevent silent 0-agent dispatch
    if isinstance(agents_to_retry, list):
        valid_agents = [a for a in agents_to_retry if a in CFL_SPECIALIST_NAMES]
        if len(valid_agents) != len(agents_to_retry):
            invalid = [a for a in agents_to_retry if a not in CFL_SPECIALIST_NAMES]
            log_msg(state, f"  WARNING: unknown agents in retry list: {invalid}")
        agents_to_retry = valid_agents

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): an agent already at its call
    # cap must not be handed back to run_specialist_node only to be silently
    # skipped there (a wasted graph round: dispatch, fan-out, fan-in, all for
    # zero new specialist output) -- filter it out of the retry plan itself,
    # counted the same way run_specialist_node counts it (from the full,
    # never-reset `specialist_outputs` history). A plan made ENTIRELY of
    # capped agents becomes an empty plan, which the terminal check below
    # already treats as "give up". Each dropped agent is noted for
    # verdict_gate.downgrades, same as a cap hit inside run_specialist_node.
    call_cap_notes: list[str] = []
    if isinstance(agents_to_retry, list) and agents_to_retry:
        specialist_outputs = state.get("specialist_outputs", [])
        filtered_agents = []
        for a in agents_to_retry:
            prior = sum(1 for n, _ in specialist_outputs if n == a)
            if prior >= MAX_CALLS_PER_AGENT:
                call_cap_notes.append(
                    f"agent {a} call cap reached ({MAX_CALLS_PER_AGENT} calls), "
                    "excluded from retry plan"
                )
            else:
                filtered_agents.append(a)
        if call_cap_notes:
            log_msg(state, f"  retry_planner: capped, excluded from retry: {call_cap_notes}")
        agents_to_retry = filtered_agents

    # Terminal condition: planner returns empty retry list, max_retries_remaining<=0,
    # OR retry_round will exceed MAX_RETRIES, and no USABLE inversion requested → give up.
    # An inversion is usable only if we haven't already exhausted MAX_INVERSIONS.
    empty_list = agents_to_retry is not None and len(agents_to_retry) == 0
    exhausted = max_retries_remaining is not None and max_retries_remaining <= 0
    next_round = state.get("retry_round", 0) + 1
    hard_limit = next_round > MAX_RETRIES
    can_invert = state.get("inversions_done", 0) < MAX_INVERSIONS
    effective_invert = should_invert and can_invert
    terminal = (empty_list or exhausted or hard_limit) and not effective_invert

    log_msg(
        state,
        f"  retry round -> {next_round}, agents={agents_to_retry}, "
        f"should_invert={should_invert}, terminal={terminal}",
    )

    result = {
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
    if call_cap_notes:
        result["call_cap_notes"] = call_cap_notes
    return result


def decide_after_retry_planner(state: PipelineState) -> str:
    """After retry_planner: invert / dispatch / fail."""
    ctx = state.get("retry_context", {})
    if ctx.get("terminal"):
        return "fail"
    should_invert = ctx.get("should_invert")
    can_invert = state.get("inversions_done", 0) < MAX_INVERSIONS
    if should_invert:
        if can_invert:
            return "invert"
        # Planner asked for invert but we're out of inversions — if there's
        # also nothing else to do, fail (terminal should have caught this,
        # but guard defensively).
        agents = state.get("agents_to_retry")
        if isinstance(agents, list) and len(agents) == 0:
            return "fail"
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

    # Pass the deterministic gate's verdict — not the proof_checker LLM's
    # own self-assessment (docs/VERDICT_POLICY.md §3: an LLM's self-report
    # can never promote a claim to "verified"; only verdict_gate.trust ==
    # "verified" can). The formalizer prompt starts with "The informal proof
    # has already been verified by the proof checker" — we must override
    # that assumption unless the deterministic gate itself reached the
    # `verified` trust tier.
    verdict_gate = state.get("verdict_gate") or {}
    proof_was_verified = bool(
        isinstance(verdict_gate, dict) and verdict_gate.get("proof_verified")
    )

    formalizer_input = {
        "ir": state["ir"],
        "reasoning_output": reasoning,
        "specialist_output": agent_results.get(primary, {}),
        "proof_was_verified": proof_was_verified,
        "verification_note": (
            "The proof checker VERIFIED this evidence — you may present it as verified."
            if proof_was_verified
            else "The proof checker did NOT verify this evidence (agent failed or "
                 "did not run). You must NOT claim the proof was independently "
                 "verified. Do not write phrases like 'verified by checker' or "
                 "'N/N checks passed'. Present the proof as the specialist's "
                 "argument, not as a verified theorem."
        ),
    }

    output = _run_agent(state, "formalizer", formalizer_input)
    if output is None:
        # Runner unavailable — silent skip (MockRunner / missing prompt).
        return {}
    if output.get("status") == "agent_error":
        # Record the failure so the final result surfaces it instead of
        # silently returning proof=null with an empty errors list.
        err_msgs = output.get("errors") or ["formalizer returned agent_error"]
        return {
            "errors": [f"formalizer: {m}" for m in err_msgs],
        }

    evidence = dict(state.get("evidence", {}))
    evidence["formalizer"] = output
    # "markdown" is no longer part of the contract (docs/TODO.md §6: the
    # proof was being generated twice — structured proof_document plus a
    # near-duplicate free-form render); kept here only as a fallback for
    # older mocks/live outputs that still include it.
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
    # If the verdict was invalid (normalized to None but raw was truthy),
    # the confidence belongs to a nonsense output — reset to 0.
    if raw_verdict and normalized is None:
        confidence = 0.0
    else:
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

    # Proof source priority:
    #   1. reasoning.proof (rarely set — reasoning prompt returns summary only)
    #   2. formalizer.proof_document / markdown
    #   3. evidence.formatted_proof
    #   4. Fallback: raw evidence from reasoning.primary_evidence specialist
    #      (e.g. pumping_cfl.evidence with cases/conclusion for non_cfl verdicts).
    #      This prevents a successful verdict from being reported with proof=null
    #      when the formalizer agent fails — the underlying specialist data is
    #      still there, just unformatted.
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
        if proof is None:
            primary = reasoning.get("primary_evidence")
            if isinstance(primary, str) and primary in agent_results:
                spec = agent_results[primary]
                if isinstance(spec, dict):
                    spec_ev = spec.get("evidence")
                    if spec_ev:
                        proof = {
                            "source": primary,
                            "note": (
                                "Raw specialist evidence — formalizer agent was "
                                "unavailable or failed. Render with cfl_renderer."
                            ),
                            "evidence": spec_ev,
                            "summary": reasoning.get("summary")
                                or reasoning.get("primary_justification"),
                        }

    # Normalize oracle_test status for the public result contract.
    # Keep the raw status in 'raw_status' for debugging.
    oracle_report = None
    if oracle_result and isinstance(oracle_result, dict):
        normalized = _normalize_oracle_test(oracle_result)
        if normalized.get("status") != "not_applicable":
            oracle_report = normalized

    errors = list(state.get("errors", []))

    # Build explicit list of agents that were dispatched but failed,
    # so agents_used (= succeeded) and agents_failed (= attempted but
    # died) together give a complete picture of coverage.
    failed_agents = _collect_failed_agents(state)

    # Independent-verification flag for the consumer of the result.
    # docs/VERDICT_POLICY.md §3: proof_verified must come ONLY from
    # deterministic signals (verdict_gate's trust-based check), never from
    # proof_checker's own LLM self-assessment — a self-reported "verified"
    # is not evidence. verdict_gate is populated by verdict_gate_node; when
    # this function is called directly (unit tests) without that node
    # having run, it defaults to {} and proof_verified is conservatively False.
    verdict_gate = state.get("verdict_gate") or {}
    trust_map = state.get("trust") or {}
    proof_verified = bool(verdict_gate.get("proof_verified", False))

    # Include raw specialist outputs so the renderer can build per-approach
    # tabs (pumping / Ogden / Parikh / closure / CFG / PDA / ...).
    # Each value is the full agent output dict with verdict+status+evidence,
    # plus its trust level (docs/VERDICT_POLICY.md §5) when known.
    specialist_outputs_out: dict[str, dict] = {}
    for name in CFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
            out = dict(out)
            if name in trust_map:
                out["trust"] = trust_map[name]
            specialist_outputs_out[name] = out

    hints = reasoning.get("hints_for_human") or []
    if not isinstance(hints, list):
        hints = []

    return {
        "result": {
            "task": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": verdict,
            "confidence": confidence,
            "proof": proof,
            "proof_verified": proof_verified,
            "grammar": grammar,
            "pda": pda,
            "oracle_test": oracle_report,
            "agents_used": sorted(agent_results.keys()),
            "agents_failed": failed_agents,
            "specialist_outputs": specialist_outputs_out,
            "claim_verification": state.get("claim_verification") or {},
            "verdict_gate": verdict_gate,
            "hints_for_human": hints,
            "classifier_hint": state.get("classifier_output") or {},
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
    graph.add_node("verdict_gate_node", verdict_gate_node)
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
    graph.add_edge("run_reasoning_node", "verdict_gate_node")

    # Decision: done / retry / invert / fail (reads the gated reasoning_output)
    graph.add_conditional_edges(
        "verdict_gate_node",
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
        "trust": {},
        "verdict_gate": {},
        "reasoning_output": {},
        "proof_checker_output": {},
        "retry_round": 0,
        "inversions_done": 0,
        "retry_context": {},
        "retry_params": {},
        "evidence": {},
        "errors": [],
        "call_cap_notes": [],
        "_specialist_name": "",
        "result": {},
    }

    final_state = graph.invoke(initial_state)
    result = final_state.get("result")
    if not result:
        result = {
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
    # Usage/cost block (TODO.md §3) -- additive, present even without a live
    # agent_runner (an all-zero UsageTracker) so callers can rely on
    # result["usage"] always existing.
    tracker = getattr(agent_runner, "usage_tracker", None)
    result["usage"] = tracker.as_dict() if tracker is not None else UsageTracker().as_dict()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
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
    if args.verbose and live is not None:
        print(live.usage_tracker.summary_line(), file=sys.stderr)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.save:
        save_dir = Path(args.save)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"{task_name}_result.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Result saved to {out_path}", file=sys.stderr)

        # Render Markdown + HTML alongside the JSON, matching agent_system.
        try:
            from cfl_system.lib.cfl_renderer import render_to_file
            md_path = save_dir / f"{task_name}_result.md"
            html_path = save_dir / f"{task_name}_result.html"
            render_to_file(result, str(md_path), fmt="md")
            render_to_file(result, str(html_path), fmt="html")
            print(f"Rendered: {md_path}", file=sys.stderr)
            print(f"Rendered: {html_path}", file=sys.stderr)
        except Exception as exc:
            print(f"Renderer failed: {exc}", file=sys.stderr)

    # Exit code reflects pipeline outcome
    verdict = result.get("verdict")
    if verdict in ("cfl", "non_cfl"):
        sys.exit(0)
    elif verdict == "inconclusive":
        sys.exit(2)
    else:  # failure / None / etc.
        sys.exit(1)


if __name__ == "__main__":
    main()
