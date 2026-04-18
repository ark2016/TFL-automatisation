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
        # Haiku is ~15× cheaper than Opus and excellent at structural text
        # conversion — ideal for turning "Opus wrote prose around its JSON"
        # into pure JSON. One repair call ≈ $0.01 vs ≈ $0.25 for a full
        # Opus retry, and it's faster too (~2s vs ~30s).
        self._json_repair_model = "claude-haiku-4-5"

    @staticmethod
    def _model_accepts_temperature(model: str) -> bool:
        """Whether the API accepts the `temperature` parameter for this model.

        Thinking-capable Claude models (Opus 4.7, likely future ones) deprecated
        the `temperature` field — passing it causes a 400 Bad Request. Sonnet 4.x
        and Haiku 4.x still accept it. Keep this a small allow-list of known
        deprecations so we fail closed when new models arrive.
        """
        if not model:
            return True
        # Known models that REJECT temperature. Extend as Anthropic releases more.
        rejects = ("claude-opus-4-7",)
        return not any(model.startswith(p) for p in rejects)

    def _build_request_kwargs(self, model: str, max_tokens: int,
                              temperature: float, system_prompt: str,
                              user_msg: str) -> dict:
        """Build kwargs for messages.stream / messages.create, dropping
        `temperature` for models that no longer accept it."""
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if self._model_accepts_temperature(model):
            kwargs["temperature"] = temperature
        return kwargs

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
            response = self.client.messages.create(**request_kwargs)
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
            # temperature is dropped automatically for models that don't
            # accept it (e.g. Opus 4.7 — adaptive thinking, picks its own).
            request_kwargs = self._build_request_kwargs(
                model=model, max_tokens=max_tokens,
                temperature=temperature, system_prompt=system_prompt,
                user_msg=user_msg,
            )
            try:
                with self.client.messages.stream(**request_kwargs) as stream:
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

            elapsed = _time.monotonic() - t0

            if self.verbose:
                extra = f" stop={stop_reason}" if stop_reason and stop_reason != "end_turn" else ""
                print(
                    f"[{agent_name}] model={model} "
                    f"tokens_in={tokens_in} tokens_out={tokens_out} "
                    f"time={elapsed:.1f}s max={max_tokens}{extra}",
                    file=sys.stderr, flush=True,
                )

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


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from LLM response text (back-compat wrapper)."""
    parsed, _ = _extract_json_with_error(text)
    return parsed


def _extract_json_with_error(text: str) -> tuple[dict | None, str | None]:
    """Extract JSON object and return (parsed, error_detail).

    error_detail is None on success, otherwise a human-readable string
    describing what went wrong at which position, to be fed back to the
    LLM on retry. Tries three strategies in order:
      1) whole text as JSON
      2) content of a ```json fenced block
      3) substring between first '{' and last '}'
    """
    text = text.strip()
    if not text:
        return None, "response was empty"

    last_err: json.JSONDecodeError | None = None
    last_strategy: str = ""

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
        # Build a precise error string with position and local context
        msg = last_err.msg
        line = last_err.lineno
        col = last_err.colno
        pos = last_err.pos

        # Show ~60 chars around the failure point to help the LLM locate it
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
    """Return list of {agent, error} for agents that were dispatched but failed.

    Used to tell the reasoning agent explicitly which specialists did not
    contribute, so it doesn't silently assume coverage it doesn't have.
    """
    failed: list[dict] = []
    seen: set[str] = set()
    for err in state.get("errors", []):
        if not isinstance(err, str) or ":" not in err:
            continue
        name, _, msg = err.partition(":")
        name = name.strip()
        if name in seen:
            continue
        if name in CFL_SPECIALIST_NAMES or name in ("proof_checker", "formalizer"):
            failed.append({"agent": name, "error": msg.strip()})
            seen.add(name)
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


def _normalize_claim_verification(raw: dict | None) -> dict:
    """Normalize claim_verification for the reasoning agent prompt.

    The claim verifier returns verification_status in
    {verified, refuted, inconclusive, error}. The reasoning prompt
    expects status in {verified, issues_found}. Map them:
      verified → verified
      refuted, inconclusive, error → issues_found
    We also preserve the original verification_status for nuance.
    """
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for agent, cv in raw.items():
        if isinstance(cv, dict):
            normalized = dict(cv)
            vs = normalized.get("verification_status")
            if vs == "verified":
                normalized["status"] = "verified"
            elif vs in ("refuted", "inconclusive", "error"):
                normalized["status"] = "issues_found"
            elif "status" not in normalized:
                normalized["status"] = "issues_found"
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

    specialist_outputs_out: dict[str, dict] = {}
    for name in CFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
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
    """
    agent_name = state["_specialist_name"]
    log_msg(state, f"  specialist: {agent_name}...")
    inp = _build_specialist_input(state, agent_name)
    out = _run_agent(state, agent_name, inp)

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

    # Validate and coerce types defensively (LLM may produce malformed JSON)
    if not isinstance(hints_raw, dict):
        log_msg(state, f"  hints is not a dict (type={type(hints_raw).__name__}), ignoring")
        hints = {}
    else:
        # Ensure per-agent hints are also dicts
        hints = {k: v for k, v in hints_raw.items() if isinstance(v, dict)}

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

    # Pass the proof_checker status explicitly so the formalizer knows
    # whether the evidence was independently verified. The formalizer
    # prompt starts with "The informal proof has already been verified
    # by the proof checker" — we must override that assumption when
    # proof_checker did not actually run.
    pc_out = state.get("proof_checker_output") or {}
    proof_was_verified = (
        isinstance(pc_out, dict) and pc_out.get("status") == "verified"
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
    # The proof checker actually verified the evidence iff it produced
    # a dict with status='verified'. Anything else (not_run / issues_found
    # / missing) means the proof is unverified and must be presented as such.
    pc_out = state.get("proof_checker_output") or {}
    proof_verified = (
        isinstance(pc_out, dict) and pc_out.get("status") == "verified"
    )

    # Include raw specialist outputs so the renderer can build per-approach
    # tabs (pumping / Ogden / Parikh / closure / CFG / PDA / ...).
    # Each value is the full agent output dict with verdict+status+evidence.
    specialist_outputs_out: dict[str, dict] = {}
    for name in CFL_SPECIALIST_NAMES:
        out = agent_results.get(name)
        if isinstance(out, dict):
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
