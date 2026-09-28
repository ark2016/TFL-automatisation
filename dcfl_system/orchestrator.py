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
import re as _re
import sys
import time as _time
from pathlib import Path
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from dcfl_system.config import (
    FORMALIZATION_ENABLED,
    LEAN_TIMEOUT,
    MAX_CALLS_PER_AGENT,
    MAX_FORMALIZE_ITERATIONS,
)
from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir
from dcfl_system.lib.hypothesis_module import analyze_dcfl_hypothesis
from dcfl_system.lib.pattern_db import match_patterns
from dcfl_system.lib.closure_table import closure_scan
from dcfl_system.lib.word_sampler import sample_words
from dcfl_system.lib.oracle_verifier import (
    verify_agent_results,
    trust_rank,
    trust_at_least,
    confidence_cap_for,
    CONTRADICTION_CONFIDENCE_CAP,
)
from dcfl_system.lib.retry_logic import build_retry_plan
from dcfl_system.lib.lean_ir import render_statement_verbose

# R-Lean (docs/VERDICT_POLICY.md): Lean 4 compose + Docker type check are shared
# with agent_system -- the barriers (scan_proof_body, kernel replay, axiom check,
# sorry-by-position) live there and are not reimplemented here.
from agent_system.lib.type_check import check_lean_file, compose_lean_file, is_docker_available

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
from dcfl_system.lib.agent_output_schema import schema_for as _output_schema_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 2

# docs/VERDICT_POLICY.md R3: when a contradiction is not resolved (R3' has no
# cross-check wired into this orchestrator yet -- dcfl_system/lib/dpda.py DOES
# provide an executable DCFL artifact (check_determinism + dpda_accepts over
# stack_strategy's `dpda` proof_sketch field), but nothing here runs it
# against the destructive proof's witness words the way cfl_system's R3'
# cross-check does, so the plain rule below still applies), a `verified`
# side still wins, but capped at 0.85, not the normal 0.98 ceiling.
_CONTRADICTION_VERIFIED_CAP = 0.85

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
    # Cost ceiling (config.MAX_CALLS_PER_AGENT): notes accumulated whenever a
    # specialist's call cap is reached and a requested retry is skipped for
    # it -- surfaced in the final verdict_gate.downgrades (reasoning_agent_node).
    call_cap_notes: Annotated[list, operator.add]
    # Lean 4 formal proof (docs/VERDICT_POLICY.md R-Lean): `formalize` is the
    # per-call override of config.FORMALIZATION_ENABLED (None = use the
    # default); `formalization` is lean_formalize_node's output, read by
    # renderer_node's `_apply_lean_gate`.
    formalize: bool | None
    formalization: dict | None
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


# _extract_json / _extract_json_with_error now come from
# agent_system.lib.llm_client (TODO.md §3 — shared across every pipeline).
# dcfl's own copy used to give only "JSON parse failed (length=N)" on
# retry, with no position or context, unlike cfl/ll — this brings it in
# line with them (position + ±40 chars of context).


# ---------------------------------------------------------------------------
# LiveRunner
# ---------------------------------------------------------------------------

class LiveRunner:
    """Run agents via Anthropic API using prompts from prompts/ directory.

    Thin wrapper (TODO.md §3): prompts/contracts/JSON-parsing/Haiku-repair
    stay pipeline-specific; the actual model call goes through the shared
    ``agent_system.lib.llm_client.AnthropicClient`` — kwargs building,
    stream-and-collect, typed retry/backoff, the process-wide concurrency
    semaphore, and usage tracking all live there now, not here.
    """

    def __init__(self, api_key: str | None = None, verbose: bool = False):
        from dcfl_system.config import (
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
            raise ValueError("ANTHROPIC_API_KEY not set.")

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

    # Legacy models — Haiku 4.5 and anything before the 4.6 family — take
    # sampling parameters and have no adaptive thinking / effort. Opus/Sonnet
    # 4.6+ and every 5.x model run adaptive thinking steered by `effort`, and
    # Opus 4.7+, Sonnet 5 / 5.5 and Opus 5.x reject `temperature` with a 400, so
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
        request_kwargs = self._build_request_kwargs(
            model=self._json_repair_model,
            max_tokens=min(len(raw_text) // 2 + 2000, 8000),
            temperature=0.0, system_prompt=system, user_msg=user_msg,
        )
        try:
            with get_concurrency_semaphore():
                response = self.client.messages.create(**request_kwargs)
        except Exception:
            return None
        repaired = "".join(b.text for b in response.content if hasattr(b, "text"))
        self.usage_tracker.record(
            getattr(response, "model", self._json_repair_model), getattr(response, "usage", None),
            agent=agent_name, used_structured_output=False,
        )
        return _extract_json(repaired)

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        try:
            system_prompt = self._load_prompt(agent_name)
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Skipping agent '%s': %s", agent_name, exc)
            return None

        model = self.model_override or self.models.get(agent_name, "claude-sonnet-5-5")
        temperature = self.temperatures.get(agent_name, 0.0)
        effort = self.efforts.get(agent_name, self.default_effort)
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
            # Thinking models get adaptive thinking + this agent's effort;
            # temperature is only sent to legacy models (Haiku 4.5). The
            # shared client also retries a retryable error (429/5xx/network/
            # broken stream) with backoff before giving up, and never
            # retries a fatal one (400/401/403/404) — TODO.md §2/§3.
            # output_schema (TODO.md §3 M): agents with a closed contract
            # (`dcfl_system.lib.agent_output_schema.REQUIRED_KEYS`) get
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
                    return {"agent": agent_name, "status": "agent_error", "verdict": None,
                            "confidence": 0.0, "evidence": {}, "errors": [f"API error: {exc}"]}
                # 401/403/404: a dead key, no access to the model, or an
                # unknown model ID -- fail fast, re-raised as the original
                # SDK exception type for backward compatibility.
                logger.error("[%s] fatal API error: %s", agent_name, exc)
                raise (exc.original if exc.original is not None else exc)
            except RetryableAPIError as exc:
                elapsed = _time.monotonic() - t0
                logger.error("[%s] API error after %.1fs: %s", agent_name, elapsed, exc)
                return {"agent": agent_name, "status": "agent_error", "verdict": None,
                        "confidence": 0.0, "evidence": {}, "errors": [f"API error: {exc}"]}

            raw_text = result.text
            stop_reason = result.stop_reason
            tokens_in = result.usage.input_tokens if result.usage else 0
            tokens_out = result.usage.output_tokens if result.usage else 0

            elapsed = _time.monotonic() - t0
            if self.verbose:
                extra = f" stop={stop_reason}" if stop_reason and stop_reason != "end_turn" else ""
                cost = estimate_cost_usd(result.model or model, result.usage)
                cost_str = f" cost≈${cost:.4f}" if cost is not None else ""
                so_str = _format_structured_output_flag(result)
                print(f"[{agent_name}] model={result.model} effort={effort} tokens_in={tokens_in} tokens_out={tokens_out} time={elapsed:.1f}s{extra}{cost_str}{so_str}",
                      file=sys.stderr, flush=True)

            # A safety-classifier decline is not a parse problem: retrying or
            # JSON-repairing the (empty/partial) text cannot help.
            if stop_reason == "refusal":
                return self._refusal_error(agent_name, result)

            parsed, parse_error = _extract_json_with_error(raw_text)
            if parsed is not None:
                return parsed

            # Try Haiku repair
            was_truncated = stop_reason == "max_tokens"
            repaired = self._repair_json_with_haiku(agent_name, raw_text, was_truncated)
            if repaired is not None:
                if was_truncated:
                    # The specialist hit max_tokens and Haiku patched the
                    # JSON shape back together, but it never saw (and could
                    # not reconstruct) the reasoning that was cut off — the
                    # content is not trustworthy enough for a normal verdict.
                    repaired["_truncated"] = True
                    repaired["_repaired"] = True
                    repaired["status"] = "inconclusive"
                    repaired["confidence"] = min(
                        _clamp_confidence(repaired.get("confidence", 0.0)), 0.40,
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
            # Position + ±40 chars of context, fed back to the model on
            # retry (TODO.md §3 — this used to be a bare "length=N").
            last_error = f"{parse_error} [response length={len(raw_text)}, Haiku repair also failed]"
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
# Verdict gate (docs/VERDICT_POLICY.md §3, R1-R4, R7)
#
# The reasoning agent proposes a verdict; the orchestrator is the one thing
# that decides what confidence that verdict is allowed to carry, based on
# the deterministic `trust` the oracle assigned to the evidence behind it
# (never the LLM's own self-reported confidence, which can only lower the
# ceiling, never raise it — VERDICT_POLICY.md §2).
# ---------------------------------------------------------------------------

# stack_strategy only ever argues "dcfl"; closure_reduction can argue either
# way depending on proof_sketch.direction.
_CONSTRUCTIVE_DCFL_AGENTS = ("stack_strategy", "closure_reduction")
_DESTRUCTIVE_NON_DCFL_AGENTS = ("dcfl_pumping", "shallit", "inh_ambiguity", "closure_reduction")


def _agent_trust(oracle_verification: dict, agent_name: str) -> str | None:
    entry = oracle_verification.get(agent_name)
    if not isinstance(entry, dict):
        return None
    return entry.get("trust") or entry.get("verification_status")


def _agent_direction(name: str, output: dict) -> str | None:
    """'constructive' (argues dcfl) / 'destructive' (argues non_dcfl) / None."""
    if name == "stack_strategy":
        return "constructive"
    if name in ("dcfl_pumping", "shallit", "inh_ambiguity"):
        return "destructive"
    if name == "closure_reduction":
        proof = output.get("proof_sketch")
        direction = proof.get("direction") if isinstance(proof, dict) else None
        if direction in ("constructive", "destructive"):
            return direction
    return None


def _best_evidence(
    agent_results: dict, oracle_verification: dict, direction: str,
) -> tuple[str | None, str | None]:
    """Strongest (non-refuted) agent + trust arguing *direction* ('constructive'/'destructive')."""
    wanted_verdict = "dcfl" if direction == "constructive" else "non_dcfl"
    best_name: str | None = None
    best_trust: str | None = None
    for name, output in agent_results.items():
        if not isinstance(output, dict) or output.get("status") != "success":
            continue
        if output.get("verdict") != wanted_verdict:
            continue
        if _agent_direction(name, output) != direction:
            continue
        trust = _agent_trust(oracle_verification, name)
        if trust == "refuted":
            continue  # R2: a refuted artifact is not evidence this round
        if best_trust is None or trust_rank(trust) > trust_rank(best_trust):
            best_name, best_trust = name, trust
    return best_name, best_trust


def _apply_verdict_gate(
    reasoning_output: dict,
    agent_results: dict,
    oracle_verification: dict,
    retry_count: int,
    max_retries: int,
) -> dict:
    """Check the reasoning agent's proposed action/verdict/confidence against
    R1, R2, R3, R4 and cap confidence per the trust of the evidence behind it
    (docs/VERDICT_POLICY.md §2-3). Returns a new reasoning-shaped dict with a
    ``verdict_gate`` block describing what (if anything) was downgraded
    (§5 result format)."""
    out = dict(reasoning_output)
    action = _get_action(out)
    verdict = out.get("verdict")
    confidence = _clamp_confidence(out.get("confidence"))

    downgrades: list[str] = []
    basis: list[dict] = []
    contradiction = False
    cap = 1.0

    if action == "done" and verdict in ("dcfl", "non_dcfl"):
        direction = "constructive" if verdict == "dcfl" else "destructive"
        primary = out.get("primary_evidence")
        primary_output = agent_results.get(primary) if isinstance(primary, str) else None
        primary_trust: str | None = None
        if (
            isinstance(primary_output, dict)
            and primary_output.get("status") == "success"
            and _agent_direction(primary, primary_output) == direction
            and primary_output.get("verdict") == verdict
        ):
            primary_trust = _agent_trust(oracle_verification, primary)

        if primary_trust is None:
            # No successful, direction-matching artifact behind the claim at
            # all (R1: constructive/destructive failure alone proves nothing).
            best_name, best_trust = _best_evidence(agent_results, oracle_verification, direction)
            primary, primary_trust = best_name, best_trust

        # R3 — contradiction: a strong artifact on one side coexists with a
        # deterministic destructive/constructive claim on the other side. This
        # runs BEFORE the R1/R2 threshold check below (using the trust the
        # reasoning agent's own proposal actually has, not a post-R2 nulled
        # verdict) — a below-threshold constructive claim (e.g. stack_strategy
        # at well_formed) next to a real destructive artifact (e.g.
        # dcfl_pumping at bounded_pass) is NOT a contradiction at all: it
        # resolves directly to the destructive side below, not through this
        # branch (see the asymmetric threshold check just below — a
        # well_formed constructive claim already fails R2 on its own, so it
        # must never be treated as strong enough to contest anything).
        opp_direction = "destructive" if direction == "constructive" else "constructive"
        opp_name, opp_trust = _best_evidence(agent_results, oracle_verification, opp_direction)
        if opp_trust is not None:
            if direction == "constructive":
                constructive_name, constructive_trust_val = primary, primary_trust
                destructive_name, destructive_trust_val = opp_name, opp_trust
            else:
                destructive_name, destructive_trust_val = primary, primary_trust
                constructive_name, constructive_trust_val = opp_name, opp_trust

            # docs/VERDICT_POLICY.md R3: a contradiction requires the
            # CONSTRUCTIVE side to be at least bounded_pass AND the
            # DESTRUCTIVE side at least well_formed -- this is NOT symmetric
            # in "own"/"opp" (reviewer finding: the old symmetric check also
            # fired when the constructive side was only well_formed and the
            # destructive side bounded_pass+, throwing away a legitimate
            # non_dcfl backed by a bounded_pass destructive proof just
            # because a well_formed stack_strategy happened to sit on the
            # other side -- a well_formed constructive claim never blocks
            # non_dcfl on its own, it already fails R2).
            if (
                trust_rank(constructive_trust_val) >= trust_rank("bounded_pass")
                and trust_rank(destructive_trust_val) >= trust_rank("well_formed")
            ):
                contradiction = True
                # docs/VERDICT_POLICY.md R3 (post-R3' revision): "bounded_pass
                # vs well_formed" no longer settles this by rank -- only a
                # `verified` side wins (capped at 0.85, not 0.98); otherwise
                # it stays inconclusive (R3': dcfl_system/lib/dpda.py now
                # gives stack_strategy's `dpda` field an executable artifact
                # (determinism check + simulation), but no cross-check against
                # the destructive proof's witnesses is wired into this gate
                # yet, so this falls back to the plain inconclusive rule
                # rather than a real R3' check).
                if constructive_trust_val == "verified" and destructive_trust_val != "verified":
                    winner_verdict = "dcfl"
                    cap = min(cap, _CONTRADICTION_VERIFIED_CAP)
                elif destructive_trust_val == "verified" and constructive_trust_val != "verified":
                    winner_verdict = "non_dcfl"
                    cap = min(cap, _CONTRADICTION_VERIFIED_CAP)
                else:
                    winner_verdict = None
                    cap = min(cap, CONTRADICTION_CONFIDENCE_CAP)
                downgrades.append(
                    f"contradiction: constructive={constructive_name}({constructive_trust_val}) "
                    f"vs destructive={destructive_name}({destructive_trust_val}) -> "
                    f"verdict={winner_verdict!r}, confidence<={cap} (R3)"
                )
                verdict = winner_verdict
                action = "retry" if retry_count < max_retries else "done"
                basis.append({"agent": constructive_name, "trust": constructive_trust_val})
                basis.append({"agent": destructive_name, "trust": destructive_trust_val})

        if not contradiction:
            # docs/VERDICT_POLICY.md R2: a CONSTRUCTIVE verdict ("dcfl") needs
            # an artifact with trust >= bounded_pass -- well_formed is
            # structure-only and must not by itself carry a positive "dcfl"
            # verdict (only inconclusive, capped by well_formed's own 0.55
            # ceiling once R1's "constructive_failure_only" gate has already
            # let it through as a verdict at all). A DESTRUCTIVE verdict
            # ("non_dcfl") still only needs trust >= well_formed per R1.
            required_trust = "bounded_pass" if direction == "constructive" else "well_formed"

            def _r4prime_rescue(reason: str) -> None:
                """docs/VERDICT_POLICY.md R4' — the reasoning agent's proposal
                on `direction` is inadmissible and no more retries are coming
                for this decision. Instead of defaulting straight to
                inconclusive, pick the STRONGEST ADMISSIBLE basis still
                standing (fresh search across ALL agents, not just `primary`
                or reasoning's chosen direction): a destructive claim >=
                well_formed (not refuted) first, then a constructive
                artifact >= bounded_pass, only then inconclusive. `failure`
                stays reserved for technical failures, never for "reasoning
                argued the wrong side" (precedent: cfl-07/cfl-12 eval
                live-run ending in `failure 0.0` despite a well_formed
                destructive proof on record).

                Before picking a single side, R3 is re-checked over this same
                fresh, all-agents search (not over `primary`, which may be
                refuted or simply not the strongest artifact on its side):
                the earlier R3 check above only ever compared `primary` — a
                specific agent the reasoning agent named — against the best
                opposing evidence, so a `primary` that is `refuted` (case A)
                or weaker than another agent on its own side (case B) let a
                real bounded_pass-vs-well_formed contradiction slip through
                as a one-sided rescue (reviewer finding: non_dcfl/dcfl at
                0.60 with contradiction=False when a bounded_pass
                constructive artifact and a well_formed destructive one
                coexisted). Recomputing over `_best_evidence` for both
                directions here catches that regardless of which side
                reasoning happened to name.
                """
                nonlocal action, verdict, cap, contradiction
                action = "done"
                d_name, d_trust = _best_evidence(agent_results, oracle_verification, "destructive")
                c_name, c_trust = _best_evidence(agent_results, oracle_verification, "constructive")

                if (
                    c_trust is not None
                    and d_trust is not None
                    and trust_rank(c_trust) >= trust_rank("bounded_pass")
                    and trust_rank(d_trust) >= trust_rank("well_formed")
                ):
                    # R3, recomputed over the strongest evidence on each side
                    # rather than over `primary` alone (see docstring above).
                    contradiction = True
                    if c_trust == "verified" and d_trust != "verified":
                        verdict = "dcfl"
                        cap = min(cap, _CONTRADICTION_VERIFIED_CAP)
                    elif d_trust == "verified" and c_trust != "verified":
                        verdict = "non_dcfl"
                        cap = min(cap, _CONTRADICTION_VERIFIED_CAP)
                    else:
                        verdict = None
                        cap = min(cap, CONTRADICTION_CONFIDENCE_CAP)
                    basis.append({"agent": c_name, "trust": c_trust})
                    basis.append({"agent": d_name, "trust": d_trust})
                    downgrades.append(
                        f"{reason} -> retry budget exhausted -> R3 contradiction recomputed over "
                        f"best evidence: constructive={c_name}({c_trust}) vs "
                        f"destructive={d_name}({d_trust}) -> verdict={verdict!r}, confidence<={cap} (R3/R4')"
                    )
                    return

                if d_trust is not None and trust_at_least(d_trust, "well_formed"):
                    verdict = "non_dcfl"
                    cap = confidence_cap_for(d_trust)
                    basis.append({"agent": d_name, "trust": d_trust})
                    downgrades.append(
                        f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                        f"destructive claim ({d_name}, trust={d_trust}) -> non_dcfl"
                    )
                    return
                if c_trust is not None and trust_at_least(c_trust, "bounded_pass"):
                    verdict = "dcfl"
                    cap = confidence_cap_for(c_trust)
                    basis.append({"agent": c_name, "trust": c_trust})
                    downgrades.append(
                        f"{reason} -> retry budget exhausted -> strongest admissible basis: "
                        f"constructive artifact ({c_name}, trust={c_trust}) -> dcfl"
                    )
                    return
                verdict, cap = None, 0.40
                downgrades.append(
                    f"{reason} -> retry budget exhausted -> strongest admissible basis: inconclusive"
                )

            if primary_trust == "refuted":
                reason = (
                    f"reasoning proposed done/{verdict} on '{primary}' but its artifact "
                    "is refuted by the oracle (R2)"
                )
                if retry_count < max_retries:
                    downgrades.append(f"{reason} -> retry/inconclusive -> retry")
                    action, verdict, cap = "retry", None, 0.25
                else:
                    _r4prime_rescue(reason)
            elif primary_trust is None or not trust_at_least(primary_trust, required_trust):
                reason = (
                    f"reasoning proposed done/{verdict} with no {required_trust}+ {direction} "
                    "artifact (basis: constructive_failure_only, R1/R2)"
                )
                if retry_count < max_retries:
                    downgrades.append(f"{reason} -> retry/inconclusive -> retry")
                    action, verdict, cap = "retry", None, 0.25
                else:
                    _r4prime_rescue(reason)
            else:
                basis.append({"agent": primary, "trust": primary_trust})
                cap = confidence_cap_for(primary_trust)

    elif action == "done" and verdict not in ("dcfl", "non_dcfl") and verdict is not None:
        # Unnormalized/unknown verdict slipped through — never trust it above not_verified.
        cap = min(cap, confidence_cap_for("not_verified"))

    final_confidence = min(confidence, cap) if action == "done" or action == "retry" else confidence
    out["action"] = action
    out["decision"] = action
    out["verdict"] = verdict
    out["confidence"] = _clamp_confidence(final_confidence)
    out["verdict_gate"] = {
        "basis": basis,
        "contradiction": contradiction,
        "downgrades": downgrades,
        "confidence_cap": cap,
    }
    return out


# ---------------------------------------------------------------------------
# R-Lean gate (docs/VERDICT_POLICY.md R-Lean)
#
# A Lean proof with status `proved` (compiled without errors, no `sorryAx`,
# axioms within {propext, Classical.choice, Quot.sound}, kernel replay -- all
# enforced by agent_system.lib.type_check.check_lean_file) is a machine-checked
# proof of `is_DCF L` / `¬ is_DCF L`: the strongest evidence there is. It sits
# ABOVE R1/R2/R3/R4' (`_apply_verdict_gate`): it needs no artifact from the
# specialists, resolves a contradiction between them, and flips a wrong
# reasoning verdict. Every other status (has_sorry / error / timeout /
# unavailable / not_formalizable) is not evidence either way (R1) and leaves the
# result of `_apply_verdict_gate` exactly as it was.
# ---------------------------------------------------------------------------

def _apply_lean_gate(reasoning_output: dict, formalization: dict | None) -> dict:
    """Apply R-Lean on top of an already-gated reasoning dict; returns a new
    dict (the input is never mutated). No-op unless ``formalization["status"]
    == "proved"`` for a ``dcfl`` / ``non_dcfl`` direction."""
    if not isinstance(formalization, dict) or formalization.get("status") != "proved":
        return reasoning_output
    direction = _normalize_verdict(formalization.get("direction"))
    if direction is None:
        return reasoning_output

    out = dict(reasoning_output)
    gate = dict(out.get("verdict_gate") or {})
    basis = list(gate.get("basis") or [])
    downgrades = list(gate.get("downgrades") or [])
    prior_verdict = _normalize_verdict(out.get("verdict"))
    flipped = prior_verdict is not None and prior_verdict != direction
    verified_cap = confidence_cap_for("verified")

    basis.append({"agent": "lean_formalizer", "trust": "verified", "basis": "lean_proof"})
    if flipped:
        # A machine-checked proof of the OPPOSITE direction outranks the
        # reasoning agent's own claim: the verdict flips at the full verified
        # ceiling (as agent_system.graph.assemble_result_node does), and the
        # disagreement stays visible as `contradiction: true` + a downgrade note.
        downgrades.append(
            f"Lean proof verified for '{direction}', opposite of the proposed "
            f"'{prior_verdict}' -> verdict changed to '{direction}', verified {verified_cap} "
            "(VERDICT_POLICY.md R-Lean: a machine-checked proof takes priority over every "
            "other track; R3)"
        )
        out["primary_evidence"] = "lean_formalizer"
        out["summary"] = (
            f"Lean-verified {direction} (machine-checked proof); the specialists' proposal "
            f"'{prior_verdict}' is overruled. " + str(out.get("summary") or "")
        ).strip()
    elif prior_verdict is None:
        downgrades.append(
            f"Lean proof verified for '{direction}' -> verdict '{direction}', verified "
            f"{verified_cap} (VERDICT_POLICY.md R-Lean; the specialists' gate had left the "
            "verdict inconclusive)"
        )
    elif gate.get("contradiction"):
        downgrades.append(
            f"Lean proof verified for '{direction}': the specialists' R3 contradiction is "
            "resolved by a machine-checked proof (R-Lean)"
        )

    out["action"] = "done"
    out["decision"] = "done"
    out["verdict"] = direction
    out["confidence"] = _clamp_confidence(verified_cap)
    out["verdict_gate"] = {
        **gate,
        "basis": basis,
        # `flipped` keeps the disagreement visible; a confirming/inconclusive
        # proof leaves no unresolved contradiction behind.
        "contradiction": flipped,
        "downgrades": downgrades,
        "confidence_cap": verified_cap,
    }
    return out


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
            # agent_error on a retry round must not erase a valid result
            # from an earlier round (root TODO.md §2) — keep the best
            # (most recent non-error) result we have for this agent.
            continue
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

    # Verdict gate (docs/VERDICT_POLICY.md §3): the reasoning agent's verdict
    # is checked against R1/R2/R3/R4 and confidence is capped per the trust
    # of the evidence behind it — never raised by the LLM's own self-assessment.
    output = _apply_verdict_gate(
        output,
        agent_results=state.get("agent_results") or {},
        oracle_verification=state.get("oracle_verification") or {},
        retry_count=state.get("retry_count", 0),
        max_retries=MAX_RETRIES,
    )

    # Surface any skipped-retry-due-to-call-cap notes (run_specialist_node)
    # in the final verdict_gate.downgrades, deduplicated (the same agent may
    # have been capped across more than one retry round).
    cap_notes = state.get("call_cap_notes") or []
    if cap_notes:
        gate = dict(output.get("verdict_gate") or {})
        existing = list(gate.get("downgrades") or [])
        for note in cap_notes:
            if note not in existing:
                existing.append(note)
        gate["downgrades"] = existing
        output["verdict_gate"] = gate

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


_DCFL_LEAN_PLAN_MAX_CHARS = 12000


def _lean_plan(state: DCFLState, direction: str) -> str:
    """Natural-language plan for the Lean formalizer: the reasoning summary plus
    the ``proof_sketch`` of the strongest specialist that argued *direction*
    (the primary evidence when it fits)."""
    reasoning = state.get("reasoning") or {}
    agent_results = state.get("agent_results") or {}
    goal = "`is_DCF L`" if direction == "dcfl" else "`¬ is_DCF L`"
    parts = [f"Direction to prove: {direction} ({goal})."]
    summary = reasoning.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(f"Reasoning summary: {summary.strip()}")

    primary = reasoning.get("primary_evidence")
    candidates = ([primary] if isinstance(primary, str) else []) + list(DCFL_SPECIALIST_NAMES)
    for name in candidates:
        out = agent_results.get(name)
        if not isinstance(out, dict) or out.get("status") != "success":
            continue
        if out.get("verdict") != direction:
            continue
        sketch = out.get("proof_sketch")
        if sketch:
            parts.append(
                f"Evidence from agent '{name}' -- proof_sketch:\n"
                + json.dumps(sketch, ensure_ascii=False, indent=2)
            )
            break
    return "\n\n".join(parts)[:_DCFL_LEAN_PLAN_MAX_CHARS]


def lean_formalize_node(state: DCFLState) -> dict:
    """R-Lean (docs/VERDICT_POLICY.md): try to machine-check the verdict in Lean 4.

    The theorem *statement* (`is_DCF L` / `¬ is_DCF L`) is generated
    deterministically from the IR by ``lib.lean_ir.render_statement`` -- never by
    the LLM. The ``lean_formalizer`` agent only writes the proof body
    (``compose_lean_file`` splices it in); on a retry (up to
    ``config.MAX_FORMALIZE_ITERATIONS``) it gets the previous attempt's
    ``check_lean_file`` ``errors[]``, never a new statement. Type checking runs
    locally in Docker; without Docker the status is ``unavailable``.

    Disabled by default (``config.FORMALIZATION_ENABLED``, from the
    ``TFL_FORMALIZATION`` env var); ``run_pipeline(..., formalize=...)`` / the
    CLI's ``--formalize`` override it per call. Also a no-op without an agent /
    mock runner (mock mode by default: root CLAUDE.md, live only on request).

    Direction: the reasoning verdict, else the hypothesis prediction. Sets
    ``state["formalization"]`` to ``{status, direction, statement, proof_body,
    attempts, errors, axioms, elapsed}`` (``status`` one of ``proved`` /
    ``has_sorry`` / ``error`` / ``timeout`` / ``unavailable`` /
    ``not_formalizable``); ``renderer_node`` feeds it to ``_apply_lean_gate``.
    An agent failure is recorded as ``status == "error"`` (R1: no evidence
    either way), never as a pipeline error.
    """
    log_msg(state, "lean_formalize_node...")

    override = state.get("formalize")
    enabled = FORMALIZATION_ENABLED if override is None else override
    if not enabled:
        log_msg(state, "  formalization: disabled")
        return {}
    if _get_runner(state) is None:
        log_msg(state, "  formalization: skipped (no runner)")
        return {}

    reasoning = state.get("reasoning") or {}
    direction = _normalize_verdict(reasoning.get("verdict"))
    if direction is None:
        direction = _normalize_verdict((state.get("hypothesis") or {}).get("prediction"))
    if direction is None:
        log_msg(state, "  formalization: no dcfl/non_dcfl direction to prove")
        return {"formalization": {
            "status": "not_formalizable",
            "direction": None,
            "reason": "neither the reasoning verdict nor the hypothesis names a direction",
        }}

    statement, reason = render_statement_verbose(state.get("task_ir") or {}, direction)
    if statement is None:
        log_msg(state, f"  formalization: no Lean statement ({reason})")
        return {"formalization": {
            "status": "not_formalizable",
            "direction": direction,
            "reason": reason or "render_statement returned no statement for this ir/direction",
        }}

    statement_snapshot = {
        "imports": statement.imports,
        "alphabet_decl": statement.alphabet_decl,
        "language_decl": statement.language_decl,
        "theorem_decl": statement.theorem_decl,
        "name": statement.name or "tfl_main",
    }
    # Live run without Docker / the tfl-lean4 image: every attempt would end in
    # `unavailable` after an Opus call -- do not spend it (mock runs skip this
    # check: they cost nothing, and check_lean_file reports `unavailable` itself).
    if state.get("mock_runner") is None and state.get("agent_runner") is not None and not is_docker_available():
        log_msg(state, "  formalization: Docker / tfl-lean4 image unavailable, lean_formalizer not called")
        return {"formalization": {
            "status": "unavailable",
            "direction": direction,
            "statement": statement_snapshot,
            "proof_body": None,
            "attempts": [],
            "errors": [],
            "axioms": [],
            "elapsed": 0.0,
            "reason": "Docker / tfl-lean4 image not available; lean_formalizer was not called",
        }}

    plan = _lean_plan(state, direction)

    attempts: list[dict] = []
    errors: list = []
    proof_body: str | None = None
    axioms: list = []
    status = "error"
    elapsed_total = 0.0

    for attempt_num in range(1, MAX_FORMALIZE_ITERATIONS + 1):
        formalizer_input: dict[str, Any] = {
            "statement": statement_snapshot,
            "plan": plan,
            "available_lemmas": [],
        }
        if errors:
            formalizer_input["errors"] = errors
            formalizer_input["previous_proof_body"] = proof_body

        log_msg(state, f"  lean_formalizer: attempt {attempt_num}/{MAX_FORMALIZE_ITERATIONS}")
        output = _run_agent(state, "lean_formalizer", formalizer_input)
        if output is None:
            log_msg(state, "  lean_formalizer: no output")
            status, errors = "error", ["lean_formalizer produced no output"]
            attempts.append({"attempt": attempt_num, "status": "no_output", "errors": errors})
            break
        if output.get("status") == "agent_error":
            errors = [str(m) for m in (output.get("errors") or ["lean_formalizer returned agent_error"])]
            log_msg(state, f"  lean_formalizer agent_error: {errors}")
            status = "error"
            attempts.append({"attempt": attempt_num, "status": "agent_error", "errors": errors})
            break

        f_ev = output.get("evidence", output)
        new_proof_body = f_ev.get("proof_body") if isinstance(f_ev, dict) else None
        if isinstance(new_proof_body, str) and not new_proof_body.strip():
            # An explicit empty proof_body is the agent's honest "no route with
            # the available lemmas" (see prompts/dcfl_lean_formalizer.md,
            # "Known limitation"): stop now, more attempts on the same dead end
            # only burn budget. Not evidence either way (R1).
            notes = f_ev.get("notes") if isinstance(f_ev, dict) else None
            log_msg(state, "  lean_formalizer: gave up (empty proof_body)")
            status = "error"
            errors = [f"lean_formalizer gave up: {notes}" if notes else "lean_formalizer gave up (empty proof_body)"]
            attempts.append({"attempt": attempt_num, "status": "gave_up", "errors": errors})
            break
        if not isinstance(new_proof_body, str):
            log_msg(state, "  lean_formalizer: no proof_body in output")
            status, errors = "error", ["lean_formalizer output had no proof_body"]
            attempts.append({"attempt": attempt_num, "status": "no_proof_body", "errors": errors})
            if attempt_num < MAX_FORMALIZE_ITERATIONS:
                continue
            break

        proof_body = new_proof_body
        lean_text = compose_lean_file(statement, proof_body)
        tc = check_lean_file(
            lean_text, timeout=LEAN_TIMEOUT, theorem_name=statement_snapshot["name"],
        )
        elapsed_total += tc.get("elapsed", 0.0) or 0.0
        tc_status = tc.get("status", "error")
        tc_errors = tc.get("errors", [])
        log_msg(state, f"  check_lean_file: {tc_status}")
        attempts.append({"attempt": attempt_num, "status": tc_status, "errors": tc_errors})

        status = tc_status
        axioms = tc.get("axioms", [])
        errors = tc_errors

        if tc_status == "proved":
            errors = []
            break
        if tc_status in ("has_sorry", "timeout", "unavailable"):
            # Not evidence either way (R1), and asking the formalizer to
            # re-edit the same body will not fix a `sorry`, a timeout or a
            # missing Docker image.
            break
        # tc_status == "error": retry with the compiler errors fed back (the
        # statement is unchanged -- only formalizer_input["errors"] differs).

    return {"formalization": {
        "status": status,
        "direction": direction,
        "statement": statement_snapshot,
        "proof_body": proof_body,
        "attempts": attempts,
        "errors": errors,
        "axioms": axioms,
        "elapsed": round(elapsed_total, 2),
    }}


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

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): an agent already at its call
    # cap must not be handed back to run_specialist_node only to be silently
    # skipped there (a wasted graph round: dispatch, fan-out, fan-in, all for
    # zero new specialist output) -- filter it out of the retry plan itself,
    # counted the same way run_specialist_node counts it (from the full,
    # never-reset `specialist_outputs` history). A plan made ENTIRELY of
    # capped agents becomes an empty plan (needs_retry below already treats
    # that as terminal). Each dropped agent is noted for
    # verdict_gate.downgrades, same as a cap hit inside run_specialist_node.
    call_cap_notes: list[str] = []
    if agents_to_retry:
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
        plan["agents_to_retry"] = agents_to_retry

    # Recompute needs_retry from the MERGED (and now cap-filtered) list, not
    # build_retry_plan's raw (pre-merge) one — otherwise a reasoning-agent
    # override that adds agents to an empty plan would be silently dropped by
    # setup_dispatch_node.
    plan["needs_retry"] = bool(agents_to_retry)
    next_count = retry_count + 1

    log_msg(
        state,
        f"  retry {next_count}, agents={agents_to_retry}, "
        f"needs_retry={plan.get('needs_retry')}",
    )

    result = {
        "agents_to_retry": agents_to_retry,
        "retry_plan": plan,
        "retry_count": next_count,
    }
    if call_cap_notes:
        result["call_cap_notes"] = call_cap_notes
    return result


def decide_after_retry_planner(state: DCFLState) -> str:
    """Conditional edge after retry_planner_node.

    An EMPTY retry plan (no agent qualifies for retry — e.g. every failing
    agent is `not_applicable`, or a `refuted` agent already retried its
    budget away) is terminal: it must render the current reasoning output
    as-is, not silently fall back to re-dispatching all 5 specialists again
    (docs/VERDICT_POLICY.md §3, R7)."""
    plan = state.get("retry_plan") or {}
    if plan.get("needs_retry"):
        return "retry"
    return "terminal"


def renderer_node(state: DCFLState) -> dict:
    """Assemble final DCFLSolutionOutput dict.

    docs/VERDICT_POLICY.md R1/R2 + §2: `_apply_verdict_gate` only ran against
    the reasoning agent's `action == "done"` branch. When the graph reaches
    this node via the *terminal* path out of `decide_after_retry_planner`
    (an empty retry plan, or budget exhausted) with `action == "retry"` or
    `"fail"` still on the reasoning output, that gate never ran at all — a
    destructive verdict with zero verified evidence would render as-is with
    whatever confidence the LLM proposed. Re-run the gate here, forced to
    `action == "done"` and with the retry budget treated as exhausted (no
    more retries are coming — we are terminal), so R1/R2/R3 and the §2
    confidence ceiling apply to every rendered verdict, not just the ones
    that arrived already marked "done".
    """
    reasoning = state.get("reasoning") or {}
    if _get_action(reasoning) != "done":
        forced = dict(reasoning)
        forced["action"] = "done"
        forced["decision"] = "done"
        gated = _apply_verdict_gate(
            forced,
            agent_results=state.get("agent_results") or {},
            oracle_verification=state.get("oracle_verification") or {},
            retry_count=MAX_RETRIES,
            max_retries=MAX_RETRIES,
        )
        state = {**state, "reasoning": gated}
    # R-Lean (docs/VERDICT_POLICY.md): a `proved` Lean proof outranks everything
    # the gate above decided. No-op for any other status / no formalization.
    formalization = state.get("formalization")
    if formalization:
        state = {**state, "reasoning": _apply_lean_gate(state.get("reasoning") or {}, formalization)}
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

    result = {
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
        # VERDICT_POLICY.md §5 — additive field, never replaces existing ones.
        "verdict_gate": reasoning.get("verdict_gate"),
    }
    # R-Lean: lean_formalize_node's output (statement, proof_body, attempts,
    # status, ...) -- additive; None when the Lean step is off / did not run
    # (same convention as cfl_system).
    result["formalization"] = state.get("formalization") or None

    return {
        "result": result,
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
            "verdict_gate": reasoning.get("verdict_gate"),
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
              -> [done] -> lean_formalize (R-Lean, opt-in) -> renderer -> END
              -> [retry] -> retry_planner -> [non-empty plan] -> setup_dispatch (loop)
                                           -> [empty plan] -> lean_formalize -> renderer -> END
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
    graph.add_node("lean_formalize_node", lean_formalize_node)
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
            "done": "lean_formalize_node",
            "retry": "retry_planner_node",
            "fail": "early_failure_node",
        },
    )

    # Retry planner -> back to dispatch (loop), unless the plan is empty
    # (terminal — R7, don't re-dispatch all 5 specialists on a no-op plan)
    graph.add_conditional_edges(
        "retry_planner_node",
        decide_after_retry_planner,
        {"retry": "setup_dispatch_node", "terminal": "lean_formalize_node"},
    )

    # R-Lean: every path that ends in a rendered verdict passes through the
    # Lean step first (a no-op unless enabled -- see lean_formalize_node).
    graph.add_edge("lean_formalize_node", "renderer_node")

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
    formalize: bool | None = None,
) -> dict:
    """Run the full DCFL pipeline and return the result dict.

    Builds the LangGraph StateGraph, constructs the initial state,
    invokes the graph, and extracts the result.

    ``formalize`` overrides ``config.FORMALIZATION_ENABLED`` (the Lean 4 step,
    docs/VERDICT_POLICY.md R-Lean) for this call: ``None`` = use the default
    (``TFL_FORMALIZATION`` env var), ``True``/``False`` = force on/off.
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
        "call_cap_notes": [],
        "formalize": formalize,
        "formalization": None,
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
            "agents_used": [],
            "retries": 0,
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

    parser = argparse.ArgumentParser(description="Run the DCFL analysis pipeline")
    parser.add_argument("task_file", help="Path to DCFL IR JSON file")
    parser.add_argument("--mock", help="Mock directory for agent outputs")
    parser.add_argument("--live", action="store_true", help="Use live LLM agents")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--formalize", action="store_true",
                        help="Force-enable the Lean 4 formalization step for this run "
                             "(default: TFL_FORMALIZATION env var). The statement is generated "
                             "from the IR; only the proof body comes from the lean_formalizer "
                             "agent (mock mode unless --live).")
    parser.add_argument(
        "--render",
        choices=["json", "md", "html", "all"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument("--output-dir", help="Output directory for rendered files")
    parser.add_argument("--save", metavar="DIR",
                        help="Save <stem>_result.{json,md,html} to DIR "
                             "(common CLI contract used by TFL Lab)")
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
        formalize=True if args.formalize else None,
    )
    if args.verbose and agent is not None:
        print(agent.usage_tracker.summary_line(), file=sys.stderr)

    # Determine output directory
    output_dir = Path(args.save or args.output_dir or "examples/output")
    # --save uses the shared <stem>_result.* naming; --output-dir keeps <stem>.*
    out_stem = f"{task_name}_result" if args.save else task_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Always print JSON to stdout and save all 3 formats when --render is set
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result_json = json.dumps(result, indent=2, ensure_ascii=False)
    print(result_json)

    # Always save JSON
    out_path = output_dir / f"{out_stem}.json"
    out_path.write_text(result_json, encoding="utf-8")
    print(f"Saved: {out_path}", file=sys.stderr)

    # Always save MD
    try:
        from dcfl_system.renderer import render_markdown
        md_text = render_markdown(result)
        md_path = output_dir / f"{out_stem}.md"
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
        html_path = output_dir / f"{out_stem}.html"
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


if __name__ == "__main__":
    main()
