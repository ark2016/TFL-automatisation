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

from ll_system.config import MAX_CALLS_PER_AGENT
from ll_system.lib.ll_ir_schema import validate_ll_ir
from ll_system.lib.preprocess import compute_preprocess_hints
from ll_system.lib.ll_table_builder import check_ll_k, find_min_ll_k
from ll_system.lib.claim_verifier import verify_ll_claim

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
from ll_system.lib.agent_output_schema import schema_for as _output_schema_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 2

# Wall-clock budget for the full LL(k) test (check_ll_k / find_min_ll_k) —
# the strong-LL(k) director-set test is cheap and always runs to completion;
# this only bounds the (potentially exponential) local-follow-set test that
# runs when the strong test fails. See docs/THEORY.md §3.1.
_ORACLE_TIME_BUDGET_S = 5.0

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

# docs/VERDICT_POLICY.md §1: trust taxonomy ranking (refuted is excluded from
# "strongest evidence" comparisons — a refuted claim is never chosen as basis
# for its own verdict, it is handled separately as a downgrade signal).
_TRUST_RANK = {
    "refuted": -1,
    "not_verified": 0,
    "inconclusive": 0,   # legacy alias some verifiers may still emit
    "well_formed": 1,
    "bounded_pass": 2,
    "verified": 3,
}


def _trust_rank(status: Any) -> int:
    return _TRUST_RANK.get(status, 0)


def _trust_of_agent(claim_verification: dict, agent_name: str | None) -> str | None:
    if not agent_name:
        return None
    v = claim_verification.get(agent_name)
    if not isinstance(v, dict):
        return None
    return v.get("trust") or v.get("verification_status")


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
    verdict_gate: dict           # {basis, contradiction, downgrades, confidence_cap}

    # -- Accumulated --
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
        from ll_system.config import (
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
        # Haiku is ~15x cheaper than Opus and excellent at structural text
        # conversion — ideal for turning "Opus wrote prose around its JSON"
        # into pure JSON.
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
        request_kwargs = self._build_request_kwargs(
            model=self._json_repair_model,
            max_tokens=min(len(raw_text) // 2 + 2000, 8000),
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
            # Thinking models get adaptive thinking + this agent's effort;
            # temperature is only sent to legacy models (Haiku 4.5). The
            # shared client also retries a retryable error (429/5xx/network/
            # broken stream) with backoff before giving up, and never
            # retries a fatal one (400/401/403/404) — TODO.md §2/§3.
            # output_schema (TODO.md §3 M): agents with a closed contract
            # (`ll_system.lib.agent_output_schema.REQUIRED_KEYS`) get
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
                    # could itself recover from is a malformed *request* for
                    # this one agent (prompt too long, ...), not a dead key
                    # or missing model access -- report as this agent's
                    # agent_error instead of aborting the whole pipeline run.
                    elapsed = _time.monotonic() - t0
                    logger.error("[%s] 400 BadRequest after %.1fs: %s", agent_name, elapsed, exc)
                    return {
                        "agent": agent_name,
                        "status": "agent_error",
                        "verdict": None,
                        "confidence": 0.0,
                        "errors": [f"API error: {exc}"],
                    }
                # 401/403/404: a dead key, no access to the model, or an
                # unknown model ID -- fail fast, re-raised as the original
                # SDK exception type for backward compatibility.
                logger.error("[%s] fatal API error: %s", agent_name, exc)
                raise (exc.original if exc.original is not None else exc)
            except RetryableAPIError as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                return {
                    "agent": agent_name,
                    "status": "agent_error",
                    "verdict": None,
                    "confidence": 0.0,
                    "errors": [f"API error: {exc}"],
                }

            raw_text = result.text
            stop_reason = result.stop_reason
            if result.usage is not None:
                tokens_in = result.usage.input_tokens
                tokens_out = result.usage.output_tokens

            elapsed = _time.monotonic() - t0

            if self.verbose:
                extra = (
                    f" stop={stop_reason}"
                    if stop_reason and stop_reason != "end_turn"
                    else ""
                )
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

            # Try cheap Haiku-based JSON repair before issuing another full retry.
            was_truncated = stop_reason == "max_tokens"
            repaired = self._repair_json_with_haiku(agent_name, raw_text, was_truncated)
            if repaired is not None:
                if was_truncated:
                    # The agent's real reasoning was cut off mid-output — Haiku
                    # only patched the JSON *syntax* of a partial answer, so
                    # its semantic content cannot be trusted as a full result.
                    # Mark it and cap confidence rather than passing it on as
                    # if the agent had actually finished.
                    repaired["_truncated"] = True
                    repaired["_repaired"] = True
                    repaired["status"] = "inconclusive"
                    try:
                        capped = min(float(repaired.get("confidence", 0.0) or 0.0), 0.40)
                    except (TypeError, ValueError):
                        capped = 0.40
                    repaired["confidence"] = capped
                    logger.warning(
                        "[%s] response truncated at max_tokens; Haiku-repaired "
                        "JSON accepted as inconclusive (confidence<=0.40)",
                        agent_name,
                    )
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


# _extract_json / _extract_json_with_error now come from
# agent_system.lib.llm_client (TODO.md §3 — shared across every pipeline so
# the JSON-retry error message stays uniform: position + ±40 chars of
# context, not a bare "length=N" like dcfl's old copy had drifted to).


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
        # TODO §1 (a): read the keys compute_preprocess_hints actually returns
        # (regularity_confidence/regularity_reason), not confidence/reason —
        # those don't exist on `hints` and always read as None/default.
        regularity_reason = hints.get("regularity_reason")
        regularity_confidence = hints.get("regularity_confidence")
        regularity_method = hints.get("regularity_method")
        log_msg(state, f"  regularity shortcut: {regularity_reason}")
        # A heuristic ("trivial_constraint": bounded set-builder variables) must
        # never be reported with the same confidence as a deterministic fact
        # ("finite"/"regex_pattern") — cap at well_formed's ceiling either way,
        # and never raise a less-confident heuristic up to 0.95 (docs/VERDICT_POLICY.md §2).
        trust = "verified" if regularity_method in ("finite", "regex_pattern") else "well_formed"
        trust_cap = 0.98 if trust == "verified" else 0.60
        confidence = min(
            0.95,
            trust_cap,
            _clamp_confidence(regularity_confidence) if regularity_confidence is not None else 0.95,
        )
        return {
            "preprocess_hints": hints,
            "result": {
                "task_type": state["ir"].get("task_type"),
                "source_text": state["ir"].get("source_text"),
                "verdict": "ll",
                "k": 1,
                "confidence": confidence,
                "proof": {
                    "method": "regularity",
                    "details": {"reason": regularity_reason, "method": regularity_method},
                },
                "grammar": None,
                "first_follow_result": None,
                "claim_verification": {},
                "agents_used": [],
                "agents_failed": [],
                "specialist_outputs": {},
                "reasoning_output": {},
                "reasoning_summary": "Язык является регулярным, следовательно LL(1).",
                "errors": [],
                "retries": 0,
                "verdict_gate": {
                    "basis": [{"agent": "regularity_shortcut", "trust": trust}],
                    "contradiction": False,
                    "downgrades": [],
                    "confidence_cap": trust_cap,
                },
            },
        }
    return {"preprocess_hints": hints}


def _run_ll_k_oracle_on(grammar: dict, requested_k: int | None) -> dict:
    """Run the LL(k) oracle on a single grammar. Raises on internal failure —
    caller wraps in try/except (see docs/THEORY.md §3.1 for the shape)."""
    if requested_k is not None and isinstance(requested_k, int) and requested_k >= 1:
        # User asked: "is this grammar LL(requested_k)?"
        # NOTE: ck_result["is_ll_k"] can be None (budget exhausted, no
        # conflict found yet — docs/THEORY.md §3.1: "лимит ⇒ unknown").
        # Do NOT coerce that to bool(None) == False: that would silently
        # turn "we don't know" into a confident "not LL(k)". "found" only
        # ever means "conclusively LL(k)".
        ck_result = check_ll_k(grammar, requested_k, time_budget_s=_ORACLE_TIME_BUDGET_S)
        ff_result = dict(ck_result)
        ff_result["found"] = ck_result.get("is_ll_k") is True
        ff_result["min_k"] = requested_k if ff_result["found"] else None
        ff_result["checked_k"] = requested_k
        return ff_result

    result = find_min_ll_k(grammar, max_k=10, time_budget_s=_ORACLE_TIME_BUDGET_S)
    ff_result = dict(result.get("result_for_k") or {})
    ff_result["found"] = result.get("found", False)
    ff_result["min_k"] = result.get("k")
    # find_min_ll_k-only diagnostics (docs/THEORY.md §3.1): needed by
    # assemble_result_node to tell a certified "not LL(k) for any k"
    # apart from "not LL(k) for k <= max_k_checked" (inconclusive above it)
    # apart from a budget-limited "undetermined" (also inconclusive).
    ff_result["strong_k"] = result.get("strong_k")
    ff_result["max_k_checked"] = result.get("max_k_checked")
    ff_result["max_k_decided"] = result.get("max_k_decided")
    ff_result["undetermined"] = result.get("undetermined")
    ff_result["certificate"] = result.get("certificate")
    return ff_result


def first_follow_oracle_node(state: PipelineState) -> dict:
    """Run LL(k) oracle on a grammar.

    For Format 3: grammar is at state["ir"]["grammar"].
    For Format 2: the given grammar (language_spec, kind == "grammar") is
    tried FIRST — TODO §1(b): it directly answers "is this language LL?",
    not only a grammar an agent happened to propose — falling back to a
    grammar produced by constructive agents (ll_grammar_builder,
    marker_analyzer, grammar_transformer) if the given grammar isn't LL(k).
    For Format 1: only agent-produced grammars are available.

    Tries k=1,2,...,10 via find_min_ll_k. Returns first_follow_result.
    """
    log_msg(state, "first_follow_oracle_node...")
    ir = state["ir"]

    candidates: list[tuple[str, dict]] = []
    if state.get("input_format") == 3:
        g = ir.get("grammar")
        if g:
            candidates.append(("task_grammar", g))
    else:
        if state.get("input_format") == 2:
            spec = ir.get("language_spec")
            if isinstance(spec, dict) and spec.get("kind") == "grammar":
                candidates.append(("given_grammar", spec))
        # Grammar(s) proposed by constructive agents
        agent_results = state.get("agent_results", {})
        for agent_name in ("ll_grammar_builder", "marker_analyzer", "grammar_transformer"):
            out = agent_results.get(agent_name, {})
            if isinstance(out, dict):
                proof_sketch = out.get("proof_sketch") or {}
                g = (
                    proof_sketch.get("grammar")
                    or proof_sketch.get("ll_grammar")
                    or proof_sketch.get("transformed_grammar")
                    or out.get("artifacts", {}).get("ll_grammar")
                    or out.get("grammar")
                )
                if g:
                    candidates.append((agent_name, g))

    if not candidates:
        log_msg(state, "  no grammar available for oracle")
        return {"first_follow_result": {"is_ll_k": None, "reason": "no grammar available"}}

    # Format 3 with a specific k: check exactly that k (not the minimum).
    # ir["k"] == None means "find minimum k" — use find_min_ll_k.
    requested_k = ir.get("k") if state.get("input_format") == 3 else None

    ff_result: dict = {}
    for source_name, grammar in candidates:
        log_msg(state, f"  trying grammar from: {source_name}")
        try:
            candidate_result = _run_ll_k_oracle_on(grammar, requested_k)
        except Exception as exc:
            logger.warning("first_follow_oracle (%s): %s", source_name, exc)
            candidate_result = {"is_ll_k": None, "error": str(exc)}
        candidate_result["grammar_source"] = source_name
        ff_result = candidate_result
        if candidate_result.get("found"):
            # A concrete LL(k) grammar — conclusive for this candidate, stop
            # here (a later candidate could only ever be equally good).
            break

    log_msg(
        state,
        f"  oracle: found={ff_result.get('found')} k={ff_result.get('min_k')} "
        f"source={ff_result.get('grammar_source')}",
    )
    return {"first_follow_result": ff_result}


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
    """Run a single specialist agent. Invoked via Send() fan-out.

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

    input_format = state.get("input_format")

    if ff_found is True and ff_min_k is not None and not ff_error:
        # Positive oracle is conclusive for all formats: a concrete LL grammar exists.
        return {
            "action": "done",
            "verdict": "ll",
            "k": ff_min_k,
            "confidence": 0.95,
            "primary_agent": "first_follow_oracle",
            "primary_method": "first_follow_oracle",
            "summary": f"Grammar is LL({ff_min_k}) (first/follow oracle)",
            "retry_plan": None,
        }
    if ff_found is False and not ff_error:
        if input_format == 3:
            # Format 3 checks a specific grammar — negative oracle IS conclusive.
            return {
                "action": "done",
                "verdict": "not_ll",
                "k": None,
                "confidence": 0.85,
                "primary_agent": "first_follow_oracle",
                "primary_method": "first_follow_oracle",
                "summary": "Grammar is not LL(k) for any k <= 10 (first/follow oracle)",
                "retry_plan": None,
            }
        # Format 1/2: oracle ran on a candidate grammar proposed by a constructive agent.
        # A negative result means that candidate grammar is not LL — it does NOT prove
        # the language itself is not LL.  Continue to agent-based fallback below.

    def _best_agent_result(
        agent_names: set[str],
        expected_verdict: str,
        default_method: str,
    ) -> dict | None:
        """Return the best fallback result from a set of agents.

        Priority: docs/VERDICT_POLICY.md §1 trust rank (verified > bounded_pass
        > well_formed > not_verified), then higher confidence. Skips refuted agents.
        """
        candidates = []
        for name in agent_names:
            v_status = _trust_of_agent(verifications, name)
            if v_status == "refuted":
                continue
            out = agent_results.get(name, {})
            if not isinstance(out, dict):
                continue
            if _normalize_verdict(out.get("verdict")) != expected_verdict:
                continue
            conf = _clamp_confidence(out.get("confidence", 0.5))
            if conf < 0.7:
                continue
            candidates.append((-_trust_rank(v_status), -conf, name, out))  # highest trust first, then higher conf

        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1]))
        _, _, agent_name, out = candidates[0]
        ps = out.get("proof_sketch") or {}
        method = ps.get("method", default_method) if isinstance(ps, dict) else default_method
        return {
            "action": "done",
            "verdict": expected_verdict,
            "k": out.get("k") if expected_verdict == "ll" else None,
            "confidence": _clamp_confidence(out.get("confidence", 0.5)),
            "primary_agent": agent_name,
            "primary_method": method,
            "summary": f"Agent '{agent_name}' "
                       f"{'found LL grammar' if expected_verdict == 'll' else 'proved not LL'}",
            "retry_plan": None,
        }

    # Check constructive agents (ll verdict), then destructive (not_ll).
    # Higher-trust claims have priority within each group, and a
    # higher-trust destructive claim beats a lower-trust constructive one.
    best_constructive = _best_agent_result(
        _CONSTRUCTIVE_AGENTS, "ll", "ll_grammar_construction"
    )
    best_destructive = _best_agent_result(
        _DESTRUCTIVE_AGENTS, "not_ll", "substitution"
    )

    constructive_trust = (
        _trust_of_agent(verifications, best_constructive.get("primary_agent"))
        if best_constructive else None
    )
    destructive_trust = (
        _trust_of_agent(verifications, best_destructive.get("primary_agent"))
        if best_destructive else None
    )

    if best_constructive and best_destructive:
        # Both sides have evidence — prefer the strictly higher-trust one;
        # a tie falls through to constructive (matches prior behavior when
        # both were equally "verified") — R3's contradiction handling lives
        # in assemble_result_node, which has the full claim_verification map.
        if _trust_rank(destructive_trust) > _trust_rank(constructive_trust):
            return best_destructive
        return best_constructive

    if best_constructive:
        return best_constructive
    if best_destructive:
        return best_destructive

    # Count agents with real evidence (>= well_formed) vs refuted claims
    verified_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and _trust_rank(v.get("trust") or v.get("verification_status")) >= _TRUST_RANK["well_formed"]
    )
    refuted_count = sum(
        1 for v in verifications.values()
        if isinstance(v, dict) and (v.get("trust") or v.get("verification_status")) == "refuted"
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
        "primary_agent": None,
        "primary_method": None,
        "summary": "Insufficient evidence to determine LL property",
        "retry_plan": None,
    }


def _agent_retry_hint(claim_verification: dict, agent_name: str) -> dict:
    """docs/VERDICT_POLICY.md R7: per-agent retry hint carrying this round's
    trust plus any concrete counterexamples / invalid symbols the verifier
    already found, so the next round's specialist prompt (via
    retry_context['hints'] -> run_specialist_node's `retry_params`) sees WHY
    it is being retried, not just that it is."""
    v = claim_verification.get(agent_name)
    if not isinstance(v, dict):
        return {"trust": "not_verified"}
    trust = v.get("trust") or v.get("verification_status") or "not_verified"
    hint: dict[str, Any] = {"trust": trust}
    details = v.get("details")
    if not isinstance(details, dict):
        details = {}

    counterexamples: list[str] = []
    le = details.get("language_equivalence")
    if isinstance(le, dict):
        for key in ("mismatches", "missing_from_grammar"):
            counterexamples.extend(w for w in (le.get(key) or []) if isinstance(w, str))
    bwi = details.get("branch_words_instantiation")
    if isinstance(bwi, dict):
        for entry in bwi.get("checked") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("word_1_in_l") is False and isinstance(entry.get("word_1"), str):
                counterexamples.append(entry["word_1"])
            if entry.get("word_2_in_l") is False and isinstance(entry.get("word_2"), str):
                counterexamples.append(entry["word_2"])
    if counterexamples:
        seen: set[str] = set()
        uniq: list[str] = []
        for w in counterexamples:
            if w not in seen:
                seen.add(w)
                uniq.append(w)
        hint["counterexamples"] = uniq

    invalid_terminals = details.get("invalid_terminals")
    if isinstance(invalid_terminals, list) and invalid_terminals:
        hint["invalid_terminals"] = list(invalid_terminals)

    issues = v.get("issues")
    if isinstance(issues, list) and issues:
        hint["issues"] = issues[:5]

    return hint


def apply_verdict_gate(state: PipelineState) -> dict:
    """Deterministic gate (docs/VERDICT_POLICY.md §§1-3, R1-R5) applied to the
    reasoning agent's proposed action/verdict/confidence -- mirrors
    cfl_system.orchestrator.apply_verdict_gate. Runs right after reasoning
    (see verdict_gate_node / build_ll_pipeline_graph), BEFORE decide_retry:
    a done/ll (or done/not_ll) proposal that fails R1/R2's trust floor is
    downgraded to retry when budget remains, and only falls back to
    inconclusive once the retry budget is exhausted. Reviewer round 4: this
    same check used to run only inside assemble_result_node, i.e. AFTER
    decide_retry had already routed straight to formalize_node on the LLM's
    raw (ungated) action -- a refuted constructive artifact never actually
    got retried, it was only ever reported as inconclusive after the fact.

    Every downgrade is logged into verdict_gate.downgrades (R4); a gate-
    triggered retry gets a retry_plan with per-agent trust + counterexamples
    in `hints` (R7) -- and an already-retry proposal from reasoning itself
    gets the same hints attached, if it did not already carry them.

    Returns {"reasoning_output": ..., "verdict_gate": ...} to merge into
    state -- past this point, reasoning_output's action/verdict/confidence
    reflect the gate's decision, never the LLM's raw proposal.
    """
    reasoning = dict(state.get("reasoning_output") or {})
    agent_results = state.get("agent_results", {}) or {}
    claim_verification = state.get("claim_verification", {}) or {}
    ff_result = state.get("first_follow_result") or {}
    retry_round = state.get("retry_round", 0)
    budget_left = retry_round < MAX_RETRIES

    action = reasoning.get("action", "done")
    if action not in ("done", "retry"):
        action = "done"

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): shared by every retry
    # proposal below (the reasoning agent's own retry_plan just here, and
    # every _apply_downgrade call further down) -- an agent already at its
    # call cap must not be handed back to run_specialist_node only to be
    # silently skipped there, counted the same way run_specialist_node
    # counts it (from the full, never-reset `specialist_outputs` history).
    # `downgrades` is declared here (not at its original spot further down)
    # so this early branch can record cap exclusions into the same list the
    # rest of this function's downgrades end up in.
    specialist_outputs = state.get("specialist_outputs", [])
    downgrades: list[str] = []

    def _prior_calls(agent_name: str) -> int:
        return sum(1 for n, _ in specialist_outputs if n == agent_name)

    def _filter_capped(agents: list) -> list:
        """`agents` with any already-capped one dropped (noted in
        `downgrades`)."""
        kept = []
        for a in agents:
            if _prior_calls(a) >= MAX_CALLS_PER_AGENT:
                downgrades.append(
                    f"agent {a} call cap reached ({MAX_CALLS_PER_AGENT} calls), "
                    "excluded from retry plan"
                )
            else:
                kept.append(a)
        return kept

    if action == "retry" and budget_left:
        # R7: attach trust + counterexamples to whatever retry_plan reasoning
        # itself proposed. Nothing to gate on verdict/confidence -- there
        # isn't a final one yet this round.
        retry_plan = dict(reasoning.get("retry_plan") or {})
        agents_to_retry = retry_plan.get("agents_to_retry")
        named_agents = isinstance(agents_to_retry, list) and bool(agents_to_retry)
        if named_agents:
            agents_to_retry = _filter_capped(agents_to_retry)

        if not named_agents or agents_to_retry:
            # Either the planner named no specific agents (retry everything
            # -- unaffected by the cap check) or at least one named agent
            # survived the cap filter.
            if named_agents:
                hints = dict(retry_plan.get("hints") or {})
                for name in agents_to_retry:
                    if isinstance(name, str) and not (isinstance(hints.get(name), dict) and hints[name]):
                        hints[name] = _agent_retry_hint(claim_verification, name)
                retry_plan["hints"] = hints
                retry_plan["agents_to_retry"] = agents_to_retry
                reasoning["retry_plan"] = retry_plan
            reasoning["action"] = "retry"
            return {
                "reasoning_output": reasoning,
                "verdict_gate": {
                    "basis": [], "contradiction": False, "downgrades": downgrades,
                    "confidence_cap": None,
                },
            }
        # Every explicitly named agent was already at its call cap -- fall
        # through to the normal done/verdict gating below (same as the
        # budget-exhausted case) instead of retrying a now-explicitly-empty
        # list, which handle_retry_node would otherwise silently
        # reinterpret as "retry everything"
        # (`agents_to_retry or list(LL_SPECIALIST_NAMES)`).
        action = "done"

    if action == "retry" and not budget_left:
        # Reasoning wants another round but the retry budget is exhausted
        # (retry_round >= MAX_RETRIES). decide_retry (which runs after this
        # gate) will force "done" in that case regardless of `action`, so
        # falling through here to the normal done/verdict gating below is
        # required -- returning early (as the retry branch above does) would
        # let a raw, ungated LLM verdict/confidence leak straight through
        # assemble_result_node once decide_retry routes to formalize_node.
        action = "done"

    raw_verdict = reasoning.get("verdict")
    verdict = _normalize_verdict(raw_verdict) or "uncertain"
    confidence = _clamp_confidence(reasoning.get("confidence", 0.0))
    primary_agent = reasoning.get("primary_agent", "")

    def _best_trust_for(agent_names: set, expected_verdict: str) -> tuple:
        """Strongest non-refuted trust among agents claiming `expected_verdict`,
        plus that agent's name."""
        best_trust, best_agent = None, None
        for name in agent_names:
            out = agent_results.get(name)
            if not isinstance(out, dict) or _normalize_verdict(out.get("verdict")) != expected_verdict:
                continue
            t = _trust_of_agent(claim_verification, name) or "not_verified"
            if t == "refuted":
                continue
            if best_agent is None or _trust_rank(t) > _trust_rank(best_trust):
                best_trust, best_agent = t, name
        return best_trust, best_agent

    def _any_refuted(agent_names: set, expected_verdict: str) -> str | None:
        for name in agent_names:
            out = agent_results.get(name)
            if not isinstance(out, dict) or _normalize_verdict(out.get("verdict")) != expected_verdict:
                continue
            if _trust_of_agent(claim_verification, name) == "refuted":
                return name
        return None

    constructive_trust, constructive_agent = _best_trust_for(_CONSTRUCTIVE_AGENTS, "ll")
    destructive_trust, destructive_agent = _best_trust_for(_DESTRUCTIVE_AGENTS, "not_ll")

    if primary_agent == "first_follow_oracle" and isinstance(ff_result, dict) and ff_result.get("found"):
        # docs/VERDICT_POLICY.md R2: the oracle only ever confirms that ONE
        # specific candidate grammar is LL(k) -- that says nothing by itself
        # about whether the candidate generates the TASK's language.
        grammar_source = ff_result.get("grammar_source")
        if grammar_source == "given_grammar":
            # Format 2: the oracle tested the TASK's OWN grammar -- a full
            # LL(k)-table pass on the grammar actually in question is a
            # complete, deterministic proof: verified.
            if constructive_trust is None or _trust_rank("verified") > _trust_rank(constructive_trust):
                constructive_trust, constructive_agent = "verified", "first_follow_oracle"
        elif grammar_source:
            # Format 1: equivalence with the task's language is exactly what
            # that agent's own claim_verification trust measures.
            source_trust = _trust_of_agent(claim_verification, grammar_source)
            if source_trust == "refuted":
                pass  # constructive verdict via this grammar stays forbidden
            elif source_trust is not None and (
                constructive_trust is None or _trust_rank(source_trust) > _trust_rank(constructive_trust)
            ):
                constructive_trust, constructive_agent = source_trust, grammar_source

    has_constructive = constructive_trust is not None and _trust_rank(constructive_trust) >= _TRUST_RANK["bounded_pass"]
    has_destructive = destructive_trust is not None and _trust_rank(destructive_trust) >= _TRUST_RANK["well_formed"]

    basis: list[dict] = []
    # `downgrades` was already declared above (shared with the early
    # retry-proposal branch's cap-exclusion notes) -- not reinitialized here.
    confidence_cap = 0.40  # docs/VERDICT_POLICY.md §2: only-LLM self-assessment ceiling

    def _apply_downgrade(reason: str, agents: list) -> None:
        nonlocal action, verdict, confidence_cap
        agents = [a for a in agents if a]
        # Cost ceiling (config.MAX_CALLS_PER_AGENT): drop any agent already
        # at its call cap from this retry proposal -- if that empties
        # `agents` entirely, the branch below naturally falls to the "no
        # retry" else (same treatment as retry budget exhausted: pick the
        # strongest admissible basis instead of spending a graph round on a
        # dispatch that would produce no new specialist output).
        agents = _filter_capped(agents)
        if budget_left and agents:
            downgrades.append(f"{reason} -> retry")
            action = "retry"
            hints = {name: _agent_retry_hint(claim_verification, name) for name in agents}
            reasoning["retry_plan"] = {
                "agents_to_retry": agents,
                "hints": hints,
                "reason": downgrades[-1],
            }
        else:
            # docs/VERDICT_POLICY.md R4' -- retry budget exhausted (or
            # nothing left worth retrying this round): pick the STRONGEST
            # ADMISSIBLE basis still standing instead of defaulting straight
            # to `uncertain` -- destructive >= well_formed (not refuted)
            # first, then constructive >= bounded_pass, only then
            # inconclusive. `failure` stays reserved for technical failures,
            # never for "reasoning argued the wrong side" (precedent:
            # cfl-07/cfl-12 eval live-run ending in `failure 0.0` despite a
            # well_formed destructive proof on record).
            if has_destructive:
                verdict = "not_ll"
                confidence_cap = 0.85 if destructive_trust == "bounded_pass" else 0.60
                basis.append({"agent": destructive_agent, "trust": destructive_trust})
                downgrades.append(
                    f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                    f"destructive claim ({destructive_agent}, trust={destructive_trust}) -> not_ll"
                )
            elif has_constructive:
                verdict = "ll"
                confidence_cap = 0.98 if constructive_trust == "verified" else 0.85
                basis.append({"agent": constructive_agent, "trust": constructive_trust})
                downgrades.append(
                    f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                    f"constructive artifact ({constructive_agent}, trust={constructive_trust}) -> ll"
                )
            else:
                downgrades.append(
                    f"{reason} -> retry budget exhausted -> strongest admissible basis: inconclusive"
                )
                verdict = "uncertain"
                confidence_cap = 0.40

    if verdict == "ll":
        if has_constructive:
            confidence_cap = 0.98 if constructive_trust == "verified" else 0.85
            basis = [{"agent": constructive_agent, "trust": constructive_trust}]
        else:
            refuted_agent = _any_refuted(_CONSTRUCTIVE_AGENTS, "ll")
            if refuted_agent:
                # R1/R2: a refuted constructive artifact is not weak evidence
                # for "ll", it is evidence AGAINST this specific attempt --
                # retry that agent if there is budget left.
                _apply_downgrade(
                    f"reasoning proposed done/ll but {refuted_agent}'s grammar was refuted "
                    "(docs/VERDICT_POLICY.md R2)",
                    [refuted_agent],
                )
            elif constructive_agent:
                # A constructive "ll" verdict needs trust >= bounded_pass;
                # well_formed is structure-only (no language-equivalence
                # oracle actually ran) and must not carry a positive "ll"
                # verdict on its own.
                _apply_downgrade(
                    f"reasoning proposed done/ll with {constructive_agent}'s grammar only "
                    "well_formed (no equivalence oracle) (docs/VERDICT_POLICY.md R2)",
                    [constructive_agent],
                )
            else:
                _apply_downgrade(
                    "reasoning proposed done/ll with no constructive artifact at all "
                    "(docs/VERDICT_POLICY.md R2)",
                    list(_CONSTRUCTIVE_AGENTS),
                )
    elif verdict == "not_ll":
        if has_destructive:
            # R2: destructive not_ll by agent >= well_formed; oracle-checked
            # words raise the ceiling from 0.60 to 0.85.
            confidence_cap = 0.85 if destructive_trust == "bounded_pass" else 0.60
            basis = [{"agent": destructive_agent, "trust": destructive_trust}]
        else:
            # R1: a failed/refuted constructive attempt is never, by itself,
            # evidence for the destructive verdict (task_grammar_filter_49
            # precedent) -- retry the destructive agents if budget allows.
            _apply_downgrade(
                "reasoning proposed done/not_ll without a destructive claim >= well_formed "
                "(docs/VERDICT_POLICY.md R1, basis: constructive_failure_only)",
                list(_DESTRUCTIVE_AGENTS),
            )

    # R3: contradiction -- both sides clear their threshold. This is mutually
    # exclusive with the downgrade branches above in practice (those only
    # fire when has_constructive/has_destructive is False for the claimed
    # side), so it never fights with an already-decided retry.
    #
    # docs/VERDICT_POLICY.md R3 (post-R3' revision): "bounded_pass vs
    # well_formed" no longer settles this by rank comparison alone (fix
    # precedent: wwvvR on Haiku 2026-09-27 in cfl_system, where a sample-
    # based bounded_pass grammar outranked a correct well_formed destructive
    # proof and won the dispute wrongly) -- only a `verified` side wins an
    # unresolved contradiction, capped at 0.85 (not the normal 0.98 ceiling);
    # otherwise it stays `uncertain`, capped at 0.50.
    contradiction = has_constructive and has_destructive
    if contradiction:
        if constructive_trust == "verified" and destructive_trust != "verified":
            verdict = "ll"
            confidence_cap = 0.85
        elif destructive_trust == "verified" and constructive_trust != "verified":
            verdict = "not_ll"
            confidence_cap = 0.85
        else:
            verdict = "uncertain"
            confidence_cap = 0.50
        downgrades.append(
            f"contradiction: constructive={constructive_agent}({constructive_trust}) vs "
            f"destructive={destructive_agent}({destructive_trust}) -> {verdict}, "
            f"confidence <= {confidence_cap} (docs/VERDICT_POLICY.md R3)"
        )
        basis = [
            {"agent": constructive_agent, "trust": constructive_trust},
            {"agent": destructive_agent, "trust": destructive_trust},
        ]

    confidence = min(confidence, confidence_cap)

    reasoning["action"] = action
    reasoning["verdict"] = verdict
    reasoning["confidence"] = confidence

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): surface any skipped-retry-
    # due-to-call-cap notes (run_specialist_node) here too, deduplicated (the
    # same agent may have been capped across more than one retry round).
    for note in state.get("call_cap_notes") or []:
        if note not in downgrades:
            downgrades.append(note)

    verdict_gate = {
        "basis": basis,
        "contradiction": contradiction,
        "downgrades": downgrades,
        "confidence_cap": confidence_cap,
    }
    return {"reasoning_output": reasoning, "verdict_gate": verdict_gate}


def verdict_gate_node(state: PipelineState) -> dict:
    """Deterministic gate (docs/VERDICT_POLICY.md) run right after reasoning,
    mirroring cfl_system.orchestrator.verdict_gate_node. See
    apply_verdict_gate for the rules; this node just wires it into the graph,
    logging, before decide_retry reads the (possibly gated) action."""
    log_msg(state, "verdict_gate_node...")
    gate = apply_verdict_gate(state)
    if state.get("verbose"):
        vg = gate["verdict_gate"]
        if vg.get("downgrades"):
            log_msg(state, f"  downgrades: {vg['downgrades']}")
        log_msg(
            state,
            f"  gated action={gate['reasoning_output'].get('action')} "
            f"verdict={gate['reasoning_output'].get('verdict')} "
            f"confidence={gate['reasoning_output'].get('confidence')} "
            f"contradiction={vg.get('contradiction')}",
        )
    return gate


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
        "specialist_outputs": [],   # no-op for the accumulator; collect_specialists_node
        # filters by dispatched agents so stale round-1 entries are ignored
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
    # Compute proof_was_verified based on the primary agent chosen by reasoning.
    # docs/VERDICT_POLICY.md §1/R5 (reviewer round 4): `proof_was_verified` is
    # the formalizer's cue to write "верифицировано" -- reserve that word for
    # a deterministic, COMPLETE check (trust == "verified": a full LL(k)-table
    # test of the exact grammar/claim in question), not `bounded_pass` (a real
    # but bounded/sample check) -- the renderer/formalizer distinguish
    # "проверено полностью" from "проверено выборочно" (§5), this flag must
    # only ever mean the former.
    claim_verification = state.get("claim_verification", {})
    primary_agent = reasoning.get("primary_agent", "")
    primary_trust = _trust_of_agent(claim_verification, primary_agent)
    if primary_agent and primary_trust is not None:
        proof_was_verified = primary_trust == "verified"
    else:
        # No primary agent identified — fall back conservatively to any
        # claim reaching the full "verified" level.
        proof_was_verified = any(
            isinstance(v, dict) and (v.get("trust") or v.get("verification_status")) == "verified"
            for v in claim_verification.values()
        )
    formalizer_input = {
        "ir": state["ir"],
        "reasoning_output": reasoning,
        "specialist_outputs": {
            k: v for k, v in agent_results.items() if k in LL_SPECIALIST_NAMES
        },
        "first_follow_result": state.get("first_follow_result", {}),
        "claim_verification": claim_verification,
        "proof_was_verified": proof_was_verified,
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
        is_ll_k = ff.get("is_ll_k")
        found = ff.get("found")
        certificate = ff.get("certificate")
        # True when the budget ran out somewhere before a witness was found —
        # either this k's own test was cut short (is_ll_k is None), or (on
        # the find_min_ll_k path) some smaller k's test was (undetermined).
        # In either case "not found" does NOT mean "conclusively not LL(k)"
        # (docs/THEORY.md §3.1: "лимит ⇒ unknown") — never conflate the two.
        budget_limited = is_ll_k is None or bool(ff.get("undetermined"))

        # docs/VERDICT_POLICY.md R5 / §2: Format 3 verdicts from a full LL(k)-table
        # test (or a not-LL certificate) are `verified`, capped at 0.98 (never 1.0 —
        # self-assessment never raises it, but the cap itself already isn't absolute
        # certainty). A "not LL(k) for k <= max_k_checked" without a certificate is
        # NOT conclusive for every k (TODO §1: "not_ll с confidence 1.0 после k <= 10"
        # was the bug) — that is `bounded_pass`, capped at 0.85, worded with the
        # "k <= max_k_checked" caveat (see _format3_summary).
        trust = "verified"
        if is_ll_k is True or found is True:
            verdict = "ll"
            k = ff.get("k") or ff.get("min_k")
            confidence = 0.98
        elif certificate is not None:
            # e.g. left recursion (after removing useless symbols) — conclusive
            # for every k (docs/THEORY.md §3.1).
            verdict = "not_ll"
            k = None
            confidence = 0.98
        elif ff.get("checked_k") is not None and is_ll_k is False:
            # A single explicit k was fully (conclusively) tested and failed.
            verdict = "not_ll"
            k = None
            confidence = 0.98
        elif budget_limited:
            verdict = "uncertain"
            k = None
            confidence = 0.0
            trust = "not_verified"
        elif found is False:
            # find_min_ll_k exhausted k <= max_k_checked: every checked k was
            # conclusively decided (not budget-limited) and none was LL(k),
            # but larger k was never tried and there is no certificate —
            # inconclusive beyond max_k_checked, not a claim about every k
            # (docs/VERDICT_POLICY.md R5 — TODO §1).
            verdict = "not_ll"
            k = None
            confidence = 0.85
            trust = "bounded_pass"
        else:
            verdict = "uncertain"
            k = None
            confidence = 0.0
            trust = "not_verified"

        proof = (
            {"method": "first_follow_oracle", "details": ff}
            if (is_ll_k is not None or found is not None or certificate is not None)
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
                "reasoning_output": {},
                "reasoning_summary": reasoning_summary,
                "errors": state.get("errors", []),
                "retries": 0,
                "verdict_gate": {
                    "basis": [{"agent": "first_follow_oracle", "trust": trust}],
                    "contradiction": False,
                    "downgrades": [],
                    "confidence_cap": 0.98 if trust == "verified" else (0.85 if trust == "bounded_pass" else 0.40),
                },
            }
        }

    # Normal path (Format 1 / 2)
    raw_verdict = reasoning.get("verdict")
    verdict = _normalize_verdict(raw_verdict) or "uncertain"
    confidence = _clamp_confidence(reasoning.get("confidence", 0.0))
    claim_verification = state.get("claim_verification", {})

    # Find best proof aligned with the final verdict.
    # Prefer primary_agent named by reasoning; fall back to set iteration.
    primary_agent = reasoning.get("primary_agent", "")
    proof = None
    grammar = None

    def _extract_constructive_proof(agent_name: str) -> tuple[dict | None, dict | None]:
        """Return (proof, grammar) from a constructive agent, or (None, None)."""
        out = agent_results.get(agent_name, {})
        if not isinstance(out, dict):
            return None, None
        ps = out.get("proof_sketch") or {}
        g = (
            ps.get("grammar")
            or ps.get("ll_grammar")
            or ps.get("transformed_grammar")
            or out.get("grammar")
            or out.get("artifacts", {}).get("ll_grammar")
        )
        if g:
            return {"method": ps.get("method", "ll_grammar_construction"), "details": ps}, g
        return None, None

    def _extract_destructive_proof(agent_name: str) -> dict | None:
        """Return proof dict from a destructive agent, or None."""
        out = agent_results.get(agent_name, {})
        if not isinstance(out, dict):
            return None
        if _normalize_verdict(out.get("verdict")) != "not_ll":
            return None
        ps = out.get("proof_sketch")
        if ps:
            return {
                "method": ps.get("method", "substitution") if isinstance(ps, dict) else "substitution",
                "details": ps,
            }
        return None

    if primary_agent == "first_follow_oracle":
        # Oracle is the primary evidence — build proof from first_follow_result
        ff = state.get("first_follow_result", {})
        proof = {
            "method": "first_follow_oracle",
            "details": {
                "is_ll_k": ff.get("is_ll_k"),
                "min_k": ff.get("min_k"),
                "checked_k": ff.get("checked_k"),
                "conflicts": ff.get("conflicts", []),
            },
        }
        # Pull grammar from constructive agents using the same fixed order as the
        # oracle node, so the grammar shown in the result is the one oracle checked.
        for agent_name in ("ll_grammar_builder", "marker_analyzer", "grammar_transformer"):
            _, g = _extract_constructive_proof(agent_name)
            if g is not None:
                grammar = g
                break
    elif verdict != "not_ll":
        # Try primary agent first, then fall back to all constructive agents
        candidates = (
            [primary_agent] + [a for a in _CONSTRUCTIVE_AGENTS if a != primary_agent]
            if primary_agent in _CONSTRUCTIVE_AGENTS
            else list(_CONSTRUCTIVE_AGENTS)
        )
        for agent_name in candidates:
            p, g = _extract_constructive_proof(agent_name)
            if p is not None:
                proof, grammar = p, g
                break

    if verdict == "not_ll" and primary_agent != "first_follow_oracle":
        # Try primary agent first, then all destructive agents
        candidates = (
            [primary_agent] + [a for a in _DESTRUCTIVE_AGENTS if a != primary_agent]
            if primary_agent in _DESTRUCTIVE_AGENTS
            else list(_DESTRUCTIVE_AGENTS)
        )
        for agent_name in candidates:
            p = _extract_destructive_proof(agent_name)
            if p is not None:
                proof = p
                break

    # Formalizer output → reasoning_summary
    formalizer_out = agent_results.get("formalizer") or {}
    reasoning_summary = (
        (formalizer_out.get("markdown_solution") if isinstance(formalizer_out, dict) else None)
        or reasoning.get("summary")
        or reasoning.get("justification")
    )

    # --- Verdict gate (docs/VERDICT_POLICY.md §§1-3, R1-R5) ---
    # R4: reasoning is primary, but the orchestrator checks it deterministically
    # against the trust of the underlying agent claims before trusting its
    # confidence. Reviewer round 4: this gate now runs in verdict_gate_node
    # right after run_reasoning_node (see build_ll_pipeline_graph), BEFORE the
    # retry/done decision, so a done/<verdict> proposal that fails R1/R2's
    # trust floor actually gets retried (when budget remains) instead of only
    # being reported as inconclusive after the fact. When that node has
    # already run, `state["reasoning_output"]` is already gated and
    # `state["verdict_gate"]` already holds its output — reuse both (note
    # `verdict`/`confidence` above were already read from that gated
    # reasoning_output). Only self-gate (call apply_verdict_gate again) when
    # this function is invoked directly, e.g. in unit tests, without that
    # node having run first — this keeps proof/grammar selection above (which
    # reads the pre-gate verdict/primary_agent) identical to before this
    # refactor for such direct calls.
    if state.get("verdict_gate") is not None:
        verdict_gate = state["verdict_gate"]
    else:
        # assemble_result_node is terminal (only reachable via decide_retry
        # returning "done") -- it can never actually act on a gate-proposed
        # retry, so force the gate's budget check to see no budget left: the
        # self-gate fallback always resolves to done/inconclusive, exactly
        # like this same downgrade used to behave unconditionally before
        # this refactor, rather than nonsensically returning action="retry"
        # from a function whose job is to assemble a FINAL result.
        gate_state = dict(state)
        gate_state["retry_round"] = MAX_RETRIES
        gate = apply_verdict_gate(gate_state)
        gated_reasoning = gate["reasoning_output"]
        verdict = _normalize_verdict(gated_reasoning.get("verdict")) or "uncertain"
        confidence = _clamp_confidence(gated_reasoning.get("confidence", 0.0))
        verdict_gate = gate["verdict_gate"]

    downgrades = verdict_gate.get("downgrades") or []
    if downgrades:
        log_msg(state, f"  verdict_gate downgrades: {downgrades}")

    return {
        "result": {
            "task_type": ir.get("task_type"),
            "source_text": ir.get("source_text"),
            "verdict": verdict,
            "k": reasoning.get("k") if verdict == "ll" else None,
            "confidence": confidence,
            "proof": proof,
            "grammar": grammar,
            "first_follow_result": ff_result,
            "claim_verification": claim_verification,
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
            "verdict_gate": verdict_gate,
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
            "reasoning_output": {},
            "reasoning_summary": detail,
            "errors": errors if errors else [detail],
            "retries": state.get("retry_round", 0),
        },
    }


def _format3_summary(ff: dict, verdict: str, k: int | None) -> str:
    """Build a human-readable summary for Format 3 oracle result.

    Distinguishes strong LL(k) from (full) LL(k), and a certified
    "not LL(k) for any k" from an inconclusive "not LL(k) for k <= max_k_checked"
    (docs/THEORY.md §3.1).
    """
    if verdict == "ll" and k is not None:
        if ff.get("is_strong_ll_k"):
            return f"Грамматика является LL({k}); таблица разбора построена для strong LL({k})."
        return (
            f"Грамматика является LL({k}), но не strong LL({k}) "
            "(полный тест Ахо–Ульмана по локальным follow-множествам)."
        )

    if verdict == "not_ll":
        certificate = ff.get("certificate")
        if certificate and certificate.get("type") == "left_recursion":
            return (
                "Данная грамматика (после удаления бесполезных символов) не является LL(k) "
                "ни при каком k: обнаружена левая рекурсия. Это утверждение о ГРАММАТИКЕ, "
                "не о языке — язык может иметь другую, не леворекурсивную LL(k)-грамматику."
            )

        checked_k = ff.get("checked_k")
        if checked_k is not None:
            conflicts = ff.get("ll_conflicts") or ff.get("conflicts") or []
            n = len(conflicts)
            suffix = f": обнаружено {n} конфликт(ов)" if n else ""
            return f"Грамматика не является LL({checked_k}){suffix}."

        max_k_checked = ff.get("max_k_checked")
        if max_k_checked:
            conflicts = ff.get("ll_conflicts") or ff.get("conflicts") or []
            n = len(conflicts)
            suffix = f": обнаружено {n} конфликт(ов)" if n else ""
            return (
                f"Грамматика не является LL(k) при k ≤ {max_k_checked}{suffix} "
                "(без сертификата — для k больше не проверялось)."
            )

        conflicts = ff.get("conflicts") or []
        if conflicts:
            n = len(conflicts)
            return f"Грамматика не является LL(k): обнаружено {n} конфликт(ов) в таблице разбора."
        return "Грамматика не является LL(k) ни для какого k ≤ 10."

    if verdict == "uncertain" and (ff.get("is_ll_k") is None or ff.get("undetermined")):
        return (
            "Бюджет полного LL(k)-теста (время/число таблиц) исчерпан прежде, чем удалось "
            "получить окончательный ответ; конфликт пока не найден, но не проверенные части "
            "поиска могут его содержать (docs/THEORY.md §3.1: «лимит ⇒ unknown») — результат "
            "не является ни подтверждением LL(k), ни опровержением."
        )

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
    graph.add_node("verdict_gate_node", verdict_gate_node)
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

    # Reasoning → verdict gate (docs/VERDICT_POLICY.md R1-R4, §2) → retry or done.
    # The gate runs BEFORE the retry/done decision so a done/<verdict>
    # proposal that fails its trust floor is downgraded to an actual retry
    # (when budget remains), not just reported as inconclusive afterward.
    graph.add_edge("run_reasoning_node", "verdict_gate_node")
    graph.add_conditional_edges(
        "verdict_gate_node",
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
        "call_cap_notes": [],
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
            "reasoning_output": {},
            "reasoning_summary": "Graph produced no result",
            "errors": ["Graph produced no result"],
            "retries": 0,
        }

    # Usage/cost block (TODO.md §3) -- additive, present even without a live
    # agent_runner (an all-zero UsageTracker) so callers can rely on
    # result["usage"] always existing.
    tracker = getattr(agent_runner, "usage_tracker", None)
    result["usage"] = tracker.as_dict() if tracker is not None else UsageTracker().as_dict()

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


def _parse_text_to_ir(text: str, verbose: bool) -> dict:
    """Run the input_parser agent to turn a raw problem statement into an IR.

    Requires a live LLM runner — the agent is an LLM call, there is no
    offline fallback. Raises RuntimeError with a clear message on any
    failure (agent error, unparsable JSON, or an IR that fails validation).
    """
    runner = LiveRunner(verbose=verbose)
    output = runner.run_agent("input_parser", {"source_text": text})
    if output is None or output.get("status") == "agent_error":
        errs = (output or {}).get("errors") or ["input_parser produced no output"]
        raise RuntimeError(f"--text parsing failed: {'; '.join(errs)}")
    ir = output.get("ir")
    if not isinstance(ir, dict):
        raise RuntimeError("--text parsing failed: input_parser returned no 'ir' object")
    parse_errors = output.get("parse_errors") or []
    if parse_errors:
        raise RuntimeError(f"--text parsing failed: {'; '.join(parse_errors)}")
    ir_errors = validate_ll_ir(ir)
    if ir_errors:
        raise RuntimeError(f"--text produced an invalid IR: {'; '.join(ir_errors)}")
    return ir


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the LL(k) analysis pipeline")
    parser.add_argument("input", nargs="?", help="IR JSON file (omit when using --text)")
    parser.add_argument(
        "--text", metavar="TEXT",
        help="Problem statement in natural language; builds the IR via the "
             "input_parser agent instead of reading an IR JSON file. "
             "Requires --live (the parser is itself an LLM call).",
    )
    parser.add_argument("--mock", metavar="DIR", help="Mock responses directory")
    parser.add_argument("--live", action="store_true", help="Use live LLM (requires API key)")
    parser.add_argument("--out", metavar="DIR", help="Output directory")
    parser.add_argument("--save", metavar="DIR",
                        help="Save <stem>_result.{json,md,html} to DIR "
                             "(common CLI contract used by TFL Lab)")
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

    if args.text and not args.input:
        if not args.live:
            print(
                "Error: --text requires --live (it runs the input_parser agent; "
                "there is no offline/--mock path for free-text input)",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            ir = _parse_text_to_ir(args.text, verbose=args.verbose)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        task_name = args.task or "text_input"
    elif args.input:
        with open(args.input, encoding="utf-8") as f:
            ir = json.load(f)
        task_name = args.task or Path(args.input).stem
    else:
        print("Error: provide an IR JSON file, or --text \"<problem statement>\"", file=sys.stderr)
        sys.exit(1)

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
    if args.verbose and agent_runner is not None:
        print(agent_runner.usage_tracker.summary_line(), file=sys.stderr)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out or args.save:
        save_dir = Path(args.save or args.out)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"{task_name}_result.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Result saved to {out_path}", file=sys.stderr)

    if args.save:
        try:
            from ll_system.renderer import render_html, render_markdown
            (save_dir / f"{task_name}_result.md").write_text(render_markdown(result), encoding="utf-8")
            (save_dir / f"{task_name}_result.html").write_text(render_html(result), encoding="utf-8")
        except Exception as exc:
            print(f"Renderer failed: {exc}", file=sys.stderr)

    # Exit code reflects pipeline outcome
    verdict = result.get("verdict")
    if verdict in ("ll", "not_ll"):
        sys.exit(0)
    elif verdict == "uncertain":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
