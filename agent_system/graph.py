"""
LangGraph-based orchestrator pipeline for the TFL Agent System.

Replaces the imperative Pipeline.run_full_pipeline() with a declarative
StateGraph that supports fan-out/fan-in for parallel specialists, retry
cycles, and hypothesis inversion.

Usage:
    from agent_system.graph import run_pipeline
    result = run_pipeline(ir_dict, mock_runner=mock, agent_runner=runner)

    # Or build the graph yourself:
    from agent_system.graph import build_full_pipeline_graph
    graph = build_full_pipeline_graph()
    result_state = graph.invoke(initial_state)
"""

from __future__ import annotations

import json
import operator
import os
import queue
import sys
import threading
import time as _time
from pathlib import Path
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .config import MAX_CALLS_PER_AGENT, MAX_FORMALIZE_ITERATIONS, LEAN_TIMEOUT
from .lib.ir_schema import validate_ir
from .lib.oracle import oracle_from_ir
from .lib.oracle_test import oracle_test as run_oracle_test
from .lib.dfa_runner import validate_dfa, run_dfa
from .lib.hypothesis_module import analyze_hypothesis
from .lib.type_check import compose_lean_file, check_lean_file
from .lib.llm_client import UsageTracker


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SPECIALIST_RETRIES = 2
MAX_INVERSIONS = 1
#: Level 1 retry (tz_tfl_agent_system.md §5.2): oracle-test counterexample
#: -> enriched retry to DFA/RE builder, re-tested each time, max 2 retries.
MAX_LEVEL1_RETRIES = 2

def _formalization_enabled_default() -> bool:
    """Default for FORMALIZATION_ENABLED: TFL_FORMALIZATION=1/true/yes/on
    enables it; unset or anything else keeps it disabled (the current
    value) while Lean templates are under active development. A run can
    also enable/disable it per-call via ``run_pipeline(..., formalize=...)``
    / the CLI's ``--formalize`` flag, which takes priority over this
    default (see `formalize_node`)."""
    return os.environ.get("TFL_FORMALIZATION", "").strip().lower() in ("1", "true", "yes", "on")


# Whether Lean 4 formalization + type checking runs by default. Tests patch
# this module attribute directly; `run_pipeline`'s `formalize` argument (and
# the CLI's `--formalize`) override it per-call via state["formalize"].
FORMALIZATION_ENABLED = _formalization_enabled_default()

SPECIALIST_NAMES = (
    "re_builder", "dfa_builder", "pumping",
    "nerode", "closure", "grammar_analyzer",
)


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
    formalize: bool | None                               # None = use FORMALIZATION_ENABLED

    # -- Pipeline data --
    hypothesis: dict
    classifier_output: dict
    classifier_evidence: dict
    grammar_facts: dict
    lang_kind: str
    student_notes: str

    # -- Specialist dispatch --
    dispatch: dict                                       # {name: bool}
    specialist_outputs: Annotated[list, operator.add]    # [(name, output)]
    dfa_builder_output: dict

    # -- Oracle --
    oracle_fn: Any
    oracle_ok: bool

    # -- Verification --
    closure_verification: dict
    claim_verification: dict
    test_result: dict
    dfa: dict | None                                     # DFA behind test_result (R3' cross-check)

    # -- Reasoning & retry --
    reasoning_output: dict
    proof_checker_output: dict
    retry_round: int
    inversions_done: int
    retry_context: dict
    retry_plan: dict                                     # {should_invert, has_retry} — see decide_after_retry_planner

    # -- Lean 4 formal proof (docs/VERDICT_POLICY.md R-Lean) --
    # {status, direction, statement, proof_body, attempts, errors, axioms,
    # elapsed} -- see `formalize_node`. Kept top-level (not nested under
    # `evidence`) because `assemble_result_node`'s gate reads it directly.
    formalization: dict

    # -- Accumulated --
    evidence: dict
    errors: Annotated[list, operator.add]
    # Cost ceiling (config.MAX_CALLS_PER_AGENT): notes accumulated whenever a
    # specialist's call cap is reached and a requested retry is skipped for
    # it -- surfaced in the final verdict_gate.downgrades (assemble_result_node).
    call_cap_notes: Annotated[list, operator.add]

    # -- Fan-out helper --
    _specialist_name: str

    # -- Final --
    result: dict


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def log_msg(state: PipelineState, msg: str) -> None:
    """Log a timestamped message to stderr when verbose mode is on."""
    if state.get("verbose"):
        print(
            f"  [{_time.strftime('%H:%M:%S')}] {msg}",
            file=sys.stderr,
            flush=True,
        )


def run_agent(state: PipelineState, agent_name: str,
              input_data: Any = None) -> dict | None:
    """Try mock runner first, then live runner.  Returns None on skip."""
    mock_runner = state.get("mock_runner")
    agent_runner = state.get("agent_runner")

    if mock_runner is not None:
        out = mock_runner.run_agent(agent_name)
        if out is not None:
            log_msg(state, f"{agent_name}: loaded from mock")
            return out

    if agent_runner is not None:
        log_msg(state, f"{agent_name}: calling LLM...")
        t0 = _time.monotonic()
        out = agent_runner.run_agent(agent_name, input_data)
        elapsed = _time.monotonic() - t0
        if out is not None and out.get("status") == "agent_error":
            log_msg(state, f"{agent_name}: agent_error ({elapsed:.1f}s): {out.get('errors')}")
        elif out is not None:
            log_msg(state, f"{agent_name}: done ({elapsed:.1f}s)")
        else:
            log_msg(state, f"{agent_name}: FAILED ({elapsed:.1f}s)")
        return out

    return None


def _agent_error_messages(agent_name: str, out: dict | None) -> list[str] | None:
    """When *out* is an ``agent_error`` dict (llm_client.py / TODO.md §2),
    return its errors prefixed with the agent name for state["errors"];
    ``None`` otherwise (out is a real result, or the agent was skipped)."""
    if out is None or out.get("status") != "agent_error":
        return None
    err_msgs = out.get("errors") or [f"{agent_name} returned agent_error"]
    return [f"{agent_name}: {m}" for m in err_msgs]


def get_alphabet(ir: dict) -> list[str]:
    """Extract alphabet from IR, falling back to ['a', 'b']."""
    spec = ir.get("language_spec", {})
    if "alphabet" in spec:
        return spec["alphabet"]
    if "terminals" in spec:
        return spec["terminals"]
    return ["a", "b"]


def extract_dfa(agent_output: dict) -> dict | None:
    """Extract a DFA dict from a dfa_builder agent output.

    A structured-output response matches the closed `dfa_builder` schema
    (`agent_system/lib/agent_output_schema.py`), which has no 'evidence' key
    at all -- `dfa` sits at the top level alongside module/status/confidence.
    Fall back to the output itself so that shape is read correctly too.
    """
    evidence = agent_output.get("evidence", agent_output)
    return evidence.get("dfa")


def _bounded_previous_output(prev_output: Any, max_chars: int = 6000) -> Any:
    """Cap a retried agent's own previous artifact (`build_specialist_input`'s
    `retry_context.previous_output`) at `max_chars` of serialized JSON.

    An unbounded previous_output (the full grammar/proof/DFA from the last
    attempt, embedded verbatim so the retried agent has memory of what it
    built -- see `_summarize_agent_output`'s docstring) is the likeliest
    source of a 400 "prompt is too long" on a retry round: it grows every
    round, and can on its own be large enough to blow the context window
    together with the rest of the prompt (agent_system/lib/llm_client.py
    turns a 400 like that into an agent_error rather than aborting the
    pipeline, but avoiding it here is cheaper than one wasted call).
    Returns `prev_output` unchanged when it already fits; otherwise a
    compact summary (`_summarize_agent_output`) plus a truncated verbatim
    excerpt, so the retried agent still sees roughly what it built last
    time instead of nothing.
    """
    try:
        serialized = json.dumps(prev_output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return prev_output
    if len(serialized) <= max_chars:
        return prev_output
    return {
        "truncated": True,
        "reason": (
            f"previous_output was {len(serialized)} chars, over the "
            f"{max_chars}-char retry budget -- truncated to avoid a 400 "
            f"'prompt is too long'"
        ),
        "summary": _summarize_agent_output(prev_output),
        "excerpt": serialized[:max_chars],
    }


def build_specialist_input(state: PipelineState, agent_name: str) -> dict:
    """Build the input dict for a specialist agent."""
    ir = state["ir"]
    inp: dict[str, Any] = {
        "ir": ir,
        "hypothesis": state.get("hypothesis", {}),
        "classifier": state.get("classifier_evidence", {}),
    }
    grammar_facts = state.get("grammar_facts")
    if grammar_facts:
        inp["grammar_facts"] = grammar_facts

    student_notes = state.get("student_notes", "")
    if student_notes:
        inp["student_notes"] = student_notes

    retry_context = state.get("retry_context")
    if retry_context:
        ctx = dict(retry_context)
        fb = ctx.get("feedback", {})
        if agent_name in fb:
            ctx["agent_feedback"] = fb[agent_name]
        # TODO.md §2 / retry planner spec: a retried agent must see its OWN
        # previous artifact/proof, not just the counterexample/feedback —
        # otherwise it "fixes" the issue blind, with no memory of what it
        # built last time.
        prev_output = state.get("evidence", {}).get(agent_name)
        if prev_output is not None:
            ctx["previous_output"] = _bounded_previous_output(prev_output)
        inp["retry_context"] = ctx

    return inp


def _summarize_agent_output(agent_output: Any, max_chars: int = 400) -> dict | None:
    """Compact summary of a specialist's output for the retry planner.

    The planner needs enough to judge whether an agent's prior result was
    right, not the full artifact (that goes to the retried agent itself via
    `build_specialist_input`'s `previous_output` -- see docs/VERDICT_POLICY.md
    R7 / TODO.md §2). Returns ``None`` when there is nothing to summarize.
    """
    if not agent_output or not isinstance(agent_output, dict):
        return None
    ev = agent_output.get("evidence", agent_output)
    verdict = ev.get("verdict") if isinstance(ev, dict) else None
    if isinstance(ev, (dict, list)):
        text = json.dumps(ev, ensure_ascii=False, default=str)
    else:
        text = str(ev)
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    summary: dict[str, Any] = {"status": agent_output.get("status")}
    if verdict is not None:
        summary["verdict"] = verdict
    summary["summary"] = text
    return summary


def _make_result(
    status: str,
    evidence: dict | None = None,
    errors: list[str] | None = None,
    confidence: float = 0.0,
) -> dict[str, Any]:
    """Build a section 5.3 output contract result."""
    return {
        "module": "orchestrator",
        "status": status,
        "evidence": evidence or {},
        "confidence": confidence,
        "errors": errors or [],
    }


def _estimate_index_with_timeout(
    oracle: Any,
    alphabet: list[str],
    max_depth: int,
    timeout: float = 120,
    state: PipelineState | None = None,
) -> dict:
    """Run estimate_index in a fresh daemon thread with a hard timeout.

    A plain daemon thread per call, NOT a shared `ThreadPoolExecutor`
    (TODO.md §2): a non-daemon pool's worker threads are joined by Python at
    interpreter exit, so a computation that is still running when `timeout`
    elapses keeps the whole process alive until it finishes -- for a
    long-running server (TFL Lab) that can mean forever. A bounded pool also
    lets one runaway computation fill the queue and starve later, unrelated
    calls, which then time out after waiting the full `timeout` without ever
    starting. A daemon thread sidesteps both: it is simply abandoned (never
    joined) if it outlives `timeout`, and every call gets its own thread
    instead of competing for a fixed-size pool.

    Returns the result dict on success, or a fallback dict with
    confidence=0 on timeout. Any exception raised by the computation itself
    propagates to the caller once ``timeout`` has not yet elapsed.
    """
    from .lib.congruence import estimate_index

    if state:
        log_msg(state, f"  estimate_index(depth={max_depth}, timeout={timeout}s)...")

    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_queue.put(("ok", estimate_index(oracle, alphabet, max_depth)))
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller's thread
            result_queue.put(("error", exc))

    thread = threading.Thread(target=_worker, name="tfl-estimate-index", daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        if state:
            log_msg(state, f"  estimate_index TIMED OUT after {timeout}s")
        return {
            "estimated_index": "unknown",
            "confidence": 0,
            "growth_pattern": [],
            "reason": f"Timed out after {timeout}s",
        }

    status, payload = result_queue.get()
    if status == "error":
        raise payload
    return payload


def _verify_closure_claim(
    closure_output: dict,
    oracle: Any,
    alphabet: list[str],
    state: PipelineState,
) -> dict | None:
    """Verify closure agent's intersection claim via oracle.

    If the closure agent claims L intersection R is non-regular, we compute
    L intersection R empirically and check whether its Nerode index is
    actually infinite.
    """
    clo_ev = closure_output.get("evidence", closure_output)
    if closure_output.get("status") == "failure":
        return None

    # Extract the regex for the regular language R
    details = clo_ev.get("details") or {}
    reg = details.get("regular_language") or {}
    regex = reg.get("regex")
    if not regex:
        return None

    log_msg(state, f"  verifying closure claim: L ∩ {regex}...")

    try:
        from .lib.dfa_builder import build_dfa_from_regex

        r_dfa = build_dfa_from_regex(regex)

        # Build memoized oracle for L ∩ R to avoid redundant CYK parses
        _oracle_cache: dict[str, bool] = {}

        def intersection_oracle(word: str) -> bool:
            if word not in _oracle_cache:
                _oracle_cache[word] = oracle(word) and run_dfa(r_dfa, word)
            return _oracle_cache[word]

        # Adaptive depth based on alphabet size to prevent timeout:
        # |Σ|=2 → depth 7, |Σ|=3 → depth 4, |Σ|≥4 → depth 3
        _alphabet_depth = {2: 7, 3: 5, 4: 3}
        depth = _alphabet_depth.get(len(alphabet), 3)

        # Estimate Nerode index of L ∩ R (with timeout)
        est = _estimate_index_with_timeout(
            intersection_oracle, alphabet, depth, timeout=120, state=state,
        )
        idx = est.get("estimated_index")
        conf = est.get("confidence", 0)

        if idx != "infinite" and conf >= 0.8:
            log_msg(
                state,
                f"  CLOSURE CLAIM WRONG: L ∩ {regex} has finite index "
                f"{idx} (confidence {conf}) — intersection is regular!",
            )

            # Any word the ORACLE (not a hardcoded a^n b^n literal count —
            # this must generalize to the IR's actual language/alphabet,
            # TODO.md §2) places in L ∩ R is a valid witness that the
            # intersection is inhabited and (per the index estimate above)
            # regular, contradicting the closure agent's non-regular claim.
            from .lib.word_generator import generate_exhaustive
            counterexamples = []
            for w in generate_exhaustive(alphabet, max_len=8):
                if intersection_oracle(w):
                    counterexamples.append(w)
                    if len(counterexamples) >= 3:
                        break

            return {
                "status": "disproved",
                "claim": f"L ∩ {regex} is non-regular",
                "actual": f"L ∩ {regex} has finite Nerode index {idx}",
                "counterexamples": counterexamples,
                "message": (
                    f"Closure agent's claim is WRONG. "
                    f"L ∩ {regex} appears regular (index={idx}). "
                    f"Words in L ∩ {regex} with count_a ≠ count_b: "
                    f"{counterexamples}"
                ),
            }
        elif idx == "infinite" and conf >= 0.8:
            log_msg(
                state,
                f"  closure claim verified: L ∩ {regex} is non-regular "
                f"(index=infinite, confidence {conf})",
            )
            return {
                "status": "verified",
                "claim": f"L ∩ {regex} is non-regular",
                "confidence": conf,
            }
        elif idx == "infinite":
            # Low confidence — not enough evidence to confirm
            log_msg(
                state,
                f"  closure claim plausible but unconfirmed "
                f"(index=infinite, confidence {conf} < 0.8)",
            )
            return {
                "status": "plausible",
                "claim": f"L ∩ {regex} is non-regular",
                "confidence": conf,
            }
        else:
            log_msg(
                state,
                f"  closure claim inconclusive (index={idx}, confidence {conf})",
            )
            return None

    except Exception as exc:
        log_msg(state, f"  closure verification failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def validate_ir_node(state: PipelineState) -> dict:
    """Step 1: validate the IR dict."""
    log_msg(state, "Step 1/9: validating IR...")
    ir_errors = validate_ir(state["ir"])
    if ir_errors:
        return {"errors": ir_errors}
    return {}


def assemble_early_failure(state: PipelineState) -> dict:
    """Terminal node when IR validation fails."""
    return {
        "result": _make_result(
            "failure",
            errors=list(state.get("errors", [])),
            confidence=0.0,
        ),
    }


def analyze_hypothesis_node(state: PipelineState) -> dict:
    """Step 2: heuristic hypothesis analysis."""
    log_msg(state, "Step 2/9: hypothesis analysis...")
    ir = state["ir"]
    hypothesis = analyze_hypothesis(ir)
    log_msg(
        state,
        f"  hypothesis={hypothesis.get('hypothesis')} "
        f"confidence={hypothesis.get('confidence')}",
    )
    evidence = dict(state.get("evidence", {}))
    evidence["hypothesis"] = hypothesis
    return {
        "hypothesis": hypothesis,
        "evidence": evidence,
        "lang_kind": ir.get("language_spec", {}).get("kind", ""),
        "student_notes": ir.get("student_notes", ""),
    }


def run_classifier_node(state: PipelineState) -> dict:
    """Step 3: run the classifier agent."""
    log_msg(state, "Step 3/9: classifier...")
    ir = state["ir"]
    classifier_input: dict[str, Any] = {
        "ir": ir,
        "hypothesis": state.get("hypothesis", {}),
    }
    if ir.get("student_notes"):
        classifier_input["student_notes"] = ir["student_notes"]

    classifier_output = run_agent(state, "classifier", classifier_input)
    err_msgs = _agent_error_messages("classifier", classifier_output)

    evidence = dict(state.get("evidence", {}))
    if err_msgs:
        log_msg(state, f"  classifier agent_error: {err_msgs}")
    elif classifier_output is not None:
        evidence["classifier"] = classifier_output
        # The classifier's closed structured-output schema has no
        # 'evidence' key -- verdict/dispatch/... sit at the top level, so
        # fall back to the output itself when 'evidence' is absent.
        verdict = classifier_output.get("evidence", classifier_output).get("verdict", "?")
        log_msg(state, f"  classifier verdict: {verdict}")

    classifier_evidence = (classifier_output or {}).get("evidence", classifier_output or {})

    result: dict[str, Any] = {
        "classifier_output": classifier_output or {},
        "classifier_evidence": classifier_evidence,
        "evidence": evidence,
    }
    if err_msgs:
        result["errors"] = err_msgs
    return result


def grammar_preprocess_node(state: PipelineState) -> dict:
    """Step 3b: grammar preprocessor (pure-fn, zero cost)."""
    lang_kind = state.get("lang_kind", "")
    if lang_kind != "grammar":
        return {}

    log_msg(state, "Step 3b: grammar preprocessor (pure-fn)...")
    try:
        from .lib.grammar_preprocessor import analyze_grammar

        oracle_fn = state.get("oracle_fn")
        grammar_facts = analyze_grammar(
            state["ir"]["language_spec"],
            oracle=oracle_fn if oracle_fn else None,
            max_word_len=10,
        )
        evidence = dict(state.get("evidence", {}))
        evidence["grammar_facts"] = grammar_facts

        log_msg(state, f"  generated {grammar_facts.get('total_generated', 0)} words")
        log_msg(
            state,
            f"  linear={grammar_facts.get('is_linear')}, "
            f"nested_recursion={grammar_facts.get('has_nested_recursion')}",
        )
        summary = grammar_facts.get("summary", "")
        if summary:
            for line in summary.split("\n"):
                log_msg(state, f"  {line}")

        return {"grammar_facts": grammar_facts, "evidence": evidence}

    except Exception as exc:
        log_msg(state, f"  grammar preprocessor failed: {exc}")
        return {}


def setup_dispatch_node(state: PipelineState) -> dict:
    """Set up the specialist dispatch map.

    On the first pass, uses the classifier's dispatch recommendations
    (if available) to avoid unnecessary specialist calls.  Falls back
    to running all specialists when no classifier output is present.

    On retry passes the dispatch has already been narrowed by the
    retry planner — we keep it as-is.
    """
    # If dispatch was already set by retry_planner, keep it.
    dispatch = state.get("dispatch")
    if dispatch and any(dispatch.values()):
        # Retry path — dispatch already configured by planner
        return {}

    # Check classifier recommendations
    classifier_evidence = state.get("classifier_evidence", {})
    classifier_dispatch = classifier_evidence.get("dispatch")

    if classifier_dispatch and isinstance(classifier_dispatch, dict):
        # Use classifier's dispatch recommendations
        dispatch = dict(classifier_dispatch)
    else:
        # Fallback — dispatch all specialists
        dispatch = {
            "re_builder": True,
            "dfa_builder": True,
            "pumping": True,
            "nerode": True,
            "closure": True,
        }

    # Always add grammar_analyzer for grammar-based tasks
    lang_kind = state.get("lang_kind", "")
    if lang_kind == "grammar":
        dispatch["grammar_analyzer"] = True

    return {"dispatch": dispatch}


def dispatch_to_specialists(state: PipelineState) -> list[Send]:
    """Conditional edge: fan-out to specialist nodes via Send()."""
    dispatch = state.get("dispatch", {})
    dispatched = [k for k, v in dispatch.items() if v]
    if not dispatched:
        # Nothing to dispatch — go straight to collect
        return [Send("collect_specialists_node", state)]
    return [
        Send("run_specialist_node", {**state, "_specialist_name": name})
        for name in dispatched
    ]


def run_specialist_node(state: PipelineState) -> dict:
    """Run a single specialist agent.  Invoked via Send() fan-out.

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
            f"  {agent_name}: call cap reached ({prior_calls}/{MAX_CALLS_PER_AGENT}), skipping retry",
        )
        return {
            "call_cap_notes": [
                f"agent {agent_name} call cap reached ({MAX_CALLS_PER_AGENT} calls)"
            ],
        }
    inp = build_specialist_input(state, agent_name)
    out = run_agent(state, agent_name, inp)
    err_msgs = _agent_error_messages(agent_name, out)
    if err_msgs:
        log_msg(state, f"  {agent_name} agent_error: {err_msgs}")
        return {"specialist_outputs": [], "errors": err_msgs}
    if out is not None:
        return {"specialist_outputs": [(agent_name, out)]}
    return {"specialist_outputs": []}


def collect_specialists_node(state: PipelineState) -> dict:
    """Fan-in: merge specialist_outputs into the evidence dict.

    Later entries with the same agent name override earlier ones, which is
    the correct behaviour for retries.
    """
    evidence = dict(state.get("evidence", {}))
    dfa_builder_output = state.get("dfa_builder_output")

    for name, out in state.get("specialist_outputs", []):
        evidence[name] = out
        if name == "dfa_builder":
            dfa_builder_output = out

    dispatched = [k for k, v in state.get("dispatch", {}).items() if v]
    log_msg(state, f"Step 4/9: specialists collected: {dispatched}")

    return {
        "evidence": evidence,
        "dfa_builder_output": dfa_builder_output,
    }


def build_oracle_node(state: PipelineState) -> dict:
    """Step 5: build the oracle (only on retry_round==0)."""
    retry_round = state.get("retry_round", 0)

    if retry_round > 0:
        # Oracle already built on round 0
        return {"oracle_ok": state.get("oracle_fn") is not None}

    log_msg(state, "Step 5/9: building oracle...")
    ir = state["ir"]
    spec = ir.get("language_spec")
    if spec is None:
        return {
            "oracle_ok": False,
            "errors": ["No language_spec — cannot build oracle"],
        }

    try:
        oracle_fn = oracle_from_ir(ir)
        return {"oracle_fn": oracle_fn, "oracle_ok": True}
    except ValueError as exc:
        return {
            "oracle_ok": False,
            "errors": [f"Oracle build failed: {exc}"],
        }


def verify_closure_node(state: PipelineState) -> dict:
    """Step 5b: verify closure agent's intersection claim.

    Runs once per round, not on every graph step (TODO.md §2): the check
    calls `estimate_index`, which can run up to 120s, so a retry round
    where the planner did NOT re-dispatch `closure` (its claim did not
    change) reuses the previous round's verification instead of paying for
    the same expensive oracle-backed check again.
    """
    oracle_ok = state.get("oracle_ok", False)
    evidence = dict(state.get("evidence", {}))
    dispatch = state.get("dispatch", {})
    is_retry_round = state.get("retry_round", 0) > 0
    already_verified = (
        state.get("closure_verification") or evidence.get("closure_verification")
    )

    if is_retry_round and not dispatch.get("closure") and already_verified is not None:
        return {}

    if oracle_ok and "closure" in evidence:
        closure_check = _verify_closure_claim(
            evidence["closure"],
            state["oracle_fn"],
            get_alphabet(state["ir"]),
            state,
        )
        if closure_check:
            evidence["closure_verification"] = closure_check
            return {
                "closure_verification": closure_check,
                "evidence": evidence,
            }

    return {}


def verify_claims_node(state: PipelineState) -> dict:
    """Step 5c: verify ALL word-membership claims via oracle, plus the
    VERDICT_POLICY.md §4 step-2 semantic checks for pumping/nerode."""
    oracle_ok = state.get("oracle_ok", False)
    evidence = dict(state.get("evidence", {}))
    updates: dict[str, Any] = {}

    if oracle_ok:
        from .lib.claim_verifier import verify_claims

        claims_result = verify_claims(
            evidence, state["oracle_fn"], get_alphabet(state["ir"]),
        )

        if claims_result["disproved"] > 0:
            evidence["claim_verification"] = claims_result
            updates["claim_verification"] = claims_result
            log_msg(
                state,
                f"  CLAIM ERRORS: {claims_result['disproved']} false claims found!",
            )
            for err in claims_result["errors"][:5]:
                log_msg(state, f"    {err}")
        elif claims_result["total_claims"] > 0:
            evidence["claim_verification"] = claims_result
            updates["claim_verification"] = claims_result
            log_msg(
                state,
                f"  claims verified: {claims_result['verified']}"
                f"/{claims_result['total_claims']}",
            )

    # Step 2 (VERDICT_POLICY.md §4): instantiate the pumping/nerode proofs
    # at concrete p/i/j and check them against the oracle, raising trust
    # from `well_formed` to `bounded_pass` (or catching a `refuted` proof).
    from .lib.claim_verifier import verify_nerode_claim, verify_pumping_claim

    oracle_fn = state.get("oracle_fn") if oracle_ok else None
    alphabet = get_alphabet(state["ir"])

    if "pumping" in evidence:
        pumping_check = verify_pumping_claim(evidence["pumping"], oracle_fn, alphabet)
        evidence["pumping_verification"] = pumping_check
        log_msg(state, f"  pumping step-2 check: trust={pumping_check['trust']}")

    if "nerode" in evidence:
        nerode_check = verify_nerode_claim(evidence["nerode"], oracle_fn, alphabet)
        evidence["nerode_verification"] = nerode_check
        log_msg(state, f"  nerode step-2 check: trust={nerode_check['trust']}")

    updates["evidence"] = evidence
    return updates


def oracle_test_node(state: PipelineState) -> dict:
    """Step 6: oracle test with internal Level 1 retry.

    If the DFA fails and we have a counterexample, re-run DFA/RE builders
    with enriched input and re-test.  This stays internal to the node.
    """
    retry_round = state.get("retry_round", 0)
    round_label = f" (retry {retry_round})" if retry_round > 0 else ""
    log_msg(state, f"Step 6/9: oracle test{round_label}...")

    oracle_ok = state.get("oracle_ok", False)
    evidence = dict(state.get("evidence", {}))
    ir = state["ir"]
    dispatch = state.get("dispatch", {})

    dfa_builder_output = state.get("dfa_builder_output")
    test_result = None
    dfa: dict | None = None

    if dfa_builder_output is not None:
        dfa = extract_dfa(dfa_builder_output)

    # Fallback: build DFA from regex
    if dfa is None and "re_builder" in evidence and oracle_ok:
        re_ev = evidence["re_builder"].get(
            "evidence", evidence["re_builder"],
        )
        regex = re_ev.get("regex")
        if regex:
            log_msg(state, f"  building DFA from regex: {regex[:40]}...")
            try:
                from .lib.dfa_builder import build_dfa_from_regex
                dfa = build_dfa_from_regex(regex)
            except Exception as exc:
                log_msg(state, f"  DFA from regex failed: {exc}")

    errors: list[str] = []
    if dfa is not None and oracle_ok:
        dfa_errors = validate_dfa(dfa)
        if dfa_errors:
            errors = [f"DFA validation: {e}" for e in dfa_errors]
        else:
            alphabet = get_alphabet(ir)
            test_result = run_oracle_test(
                state["oracle_fn"], dfa, alphabet,
                strategies=["exhaustive_k"], max_exhaustive=7,
            )
            evidence["oracle_test"] = test_result

    if test_result:
        log_msg(
            state,
            f"  oracle_test: {test_result.get('status')} "
            f"({test_result.get('tested', 0)} words)",
        )

    # --- Level 1 retry: Oracle counterexample → re-run DFA/RE builder ---
    # tz_tfl_agent_system.md §5.2: "Max 2 retries" -- loop bounded by
    # MAX_LEVEL1_RETRIES, re-testing the rebuilt DFA/regex against the
    # oracle after EVERY attempt (not just when dfa_builder itself was
    # re-dispatched: a re_builder-only retry still gets its new regex
    # converted to a DFA and re-tested, TODO.md §2).
    has_runner = (
        state.get("agent_runner") is not None
        or state.get("mock_runner") is not None
    )
    level1_attempts = 0
    while (
        test_result
        and test_result.get("status") == "fail"
        and test_result.get("counterexample")
        and retry_round == 0
        and has_runner
        and level1_attempts < MAX_LEVEL1_RETRIES
    ):
        level1_attempts += 1
        ce = test_result["counterexample"]
        log_msg(
            state,
            f"  oracle counterexample: '{ce.get('word')}' "
            f"(oracle={ce.get('oracle_says')}, dfa={ce.get('automaton_says')})",
        )
        log_msg(
            state,
            f"  → Level 1 retry {level1_attempts}/{MAX_LEVEL1_RETRIES}: "
            "re-running DFA/RE builders with counterexample",
        )

        enriched_input = {
            **build_specialist_input(state, "re_builder"),
            "retry_context": {
                "oracle_counterexample": ce,
                "instruction": (
                    f"Your previous DFA/regex was WRONG. "
                    f"The word '{ce.get('word')}' should be "
                    f"{'accepted' if ce.get('oracle_says') else 'rejected'} "
                    f"but your automaton "
                    f"{'rejected' if ce.get('oracle_says') else 'accepted'} it. "
                    f"Fix your construction."
                ),
            },
        }

        new_regex = None
        if dispatch.get("re_builder"):
            re_out = run_agent(state, "re_builder", enriched_input)
            re_err = _agent_error_messages("re_builder", re_out)
            if re_err:
                log_msg(state, f"  re_builder agent_error: {re_err}")
                errors.extend(re_err)
            elif re_out is not None:
                evidence["re_builder"] = re_out
                re_ev2 = re_out.get("evidence", re_out)
                new_regex = re_ev2.get("regex")

        new_dfa = None
        if dispatch.get("dfa_builder"):
            new_dfa_out = run_agent(state, "dfa_builder", enriched_input)
            dfa_err = _agent_error_messages("dfa_builder", new_dfa_out)
            if dfa_err:
                log_msg(state, f"  dfa_builder agent_error: {dfa_err}")
                errors.extend(dfa_err)
            elif new_dfa_out is not None:
                dfa_builder_output = new_dfa_out
                evidence["dfa_builder"] = new_dfa_out
                new_dfa = extract_dfa(new_dfa_out)

        if new_dfa is None and new_regex and oracle_ok:
            # re_builder-only retry (dfa_builder wasn't dispatched this
            # round) -- build a DFA from the freshly rebuilt regex so it
            # still gets re-tested, instead of silently keeping the old
            # (already-failing) oracle_test result.
            try:
                from .lib.dfa_builder import build_dfa_from_regex
                new_dfa = build_dfa_from_regex(new_regex)
            except Exception as exc:
                log_msg(state, f"  DFA from retried regex failed: {exc}")

        if new_dfa is None or not oracle_ok:
            # Nothing new to re-test with this attempt — stop retrying.
            break

        dfa_errs2 = validate_dfa(new_dfa)
        if dfa_errs2:
            log_msg(state, f"  retried DFA invalid: {dfa_errs2}")
            break

        dfa = new_dfa
        test_result = run_oracle_test(
            state["oracle_fn"], dfa, get_alphabet(ir),
            strategies=["exhaustive_k"], max_exhaustive=7,
        )
        evidence["oracle_test"] = test_result
        log_msg(
            state,
            f"  oracle re-test: {test_result.get('status')} "
            f"({test_result.get('tested', 0)} words)",
        )

    updates: dict[str, Any] = {
        "test_result": test_result,
        "evidence": evidence,
        "dfa_builder_output": dfa_builder_output,
        # R3' cross-check (assemble_result_node): needs the actual DFA dict
        # tested against test_result, not just the pass/fail summary.
        "dfa": dfa,
    }
    if errors:
        updates["errors"] = errors
    return updates


def run_proof_checker_node(state: PipelineState) -> dict:
    """Step 6b: proof checker (Opus, adversarial)."""
    has_runner = (
        state.get("agent_runner") is not None
        or state.get("mock_runner") is not None
    )
    if not has_runner:
        return {}

    retry_round = state.get("retry_round", 0)
    round_label = f" (retry {retry_round})" if retry_round > 0 else ""
    log_msg(state, f"Step 6b/9: proof checker{round_label}...")

    evidence = state.get("evidence", {})
    ir = state["ir"]

    checker_input: dict[str, Any] = {
        "ir": ir,
        "specialist_outputs": {
            k: evidence[k]
            for k in SPECIALIST_NAMES
            if k in evidence
        },
        "oracle_test": evidence.get("oracle_test"),
        "closure_verification": evidence.get("closure_verification"),
    }
    student_notes = state.get("student_notes", "")
    if student_notes:
        checker_input["student_notes"] = student_notes

    checker_output = run_agent(state, "proof_checker", checker_input)
    err_msgs = _agent_error_messages("proof_checker", checker_output)
    if err_msgs:
        log_msg(state, f"  proof_checker agent_error: {err_msgs}")
        return {"errors": err_msgs}

    if checker_output is not None:
        new_evidence = dict(evidence)
        new_evidence["proof_checker"] = checker_output
        return {
            "proof_checker_output": checker_output,
            "evidence": new_evidence,
        }
    return {}


def run_reasoning_node(state: PipelineState) -> dict:
    """Step 7: reasoning agent."""
    retry_round = state.get("retry_round", 0)
    round_label = f" (retry {retry_round})" if retry_round > 0 else ""
    log_msg(state, f"Step 7/9: reasoning agent{round_label}...")

    evidence = state.get("evidence", {})
    ir = state["ir"]

    reasoning_input: dict[str, Any] = {
        "ir": ir,
        "hypothesis": state.get("hypothesis", {}),
        "specialist_outputs": {
            k: evidence[k]
            for k in SPECIALIST_NAMES
            if k in evidence
        },
        "oracle_test": evidence.get("oracle_test"),
        "closure_verification": evidence.get("closure_verification"),
        "proof_checker": evidence.get("proof_checker"),
    }

    if retry_round > 0:
        reasoning_input["retry_round"] = retry_round
        reasoning_input["previous_issues"] = state.get("retry_context")

    reasoning_output = run_agent(state, "reasoning", reasoning_input)
    err_msgs = _agent_error_messages("reasoning", reasoning_output)
    if err_msgs:
        log_msg(state, f"  reasoning agent_error: {err_msgs}")

    new_evidence = dict(evidence)
    if reasoning_output is not None and not err_msgs:
        new_evidence["reasoning"] = reasoning_output

    result: dict[str, Any] = {
        "reasoning_output": reasoning_output,
        "evidence": new_evidence,
    }
    if err_msgs:
        result["errors"] = err_msgs
    return result


def decide_retry(state: PipelineState) -> str:
    """Conditional edge after reasoning: retry, invert, or done."""
    reasoning_output = state.get("reasoning_output")
    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    action = r_ev.get("action", (reasoning_output or {}).get("action", ""))

    retry_round = state.get("retry_round", 0)
    inversions_done = state.get("inversions_done", 0)

    if action == "retry_enriched" and retry_round < MAX_SPECIALIST_RETRIES:
        return "retry"
    if action == "invert_hypothesis" and inversions_done < MAX_INVERSIONS:
        return "invert"
    return "done"


def _specialist_trust(agent: str, evidence: dict) -> str | None:
    """Look up the deterministic trust label for a dispatched specialist,
    per docs/VERDICT_POLICY.md §1/§4 (pumping/nerode step-2 checks; closure
    via its own Nerode-index estimate). ``None`` when nothing was computed
    for this agent (e.g. re_builder/dfa_builder, or closure without an
    oracle) -- the retry planner then falls back to status/verdict alone."""
    if agent == "pumping":
        check = evidence.get("pumping_verification")
        return check.get("trust") if isinstance(check, dict) else None
    if agent == "nerode":
        check = evidence.get("nerode_verification")
        return check.get("trust") if isinstance(check, dict) else None
    if agent == "closure":
        from .lib.claim_verifier import closure_trust_from_verification

        closure_verification = evidence.get("closure_verification")
        return closure_trust_from_verification(
            evidence.get("closure"), closure_verification,
        )
    return None


def _pumping_refuted_hint(pumping_check: dict) -> str:
    """Human-readable hint for the retry planner: which word, at which p,
    the claimed pumping proof actually pumps within L at (VERDICT_POLICY.md
    R7) -- built from `verify_pumping_claim`'s `refuted` counterexample."""
    cx = pumping_check.get("counterexample") or {}
    word, p = cx.get("word"), cx.get("p")
    x, y, z = cx.get("x"), cx.get("y"), cx.get("z")
    if word is not None and p is not None and x is not None:
        return (
            f"слово {word!r} при p={p} накачивается разбиением "
            f"x={x!r}, y={y!r}, z={z!r}"
        )
    return pumping_check.get("reason", "pumping proof refuted by oracle")


def _nerode_refuted_hint(nerode_check: dict) -> str:
    """Human-readable hint for a refuted Myhill-Nerode distinguishability
    proof (VERDICT_POLICY.md R7): which instantiated pair the claimed
    context fails to distinguish."""
    cx = nerode_check.get("counterexample") or {}
    w_i, w_j, ctx = cx.get("w_i"), cx.get("w_j"), cx.get("context")
    if w_i is not None and w_j is not None and ctx is not None:
        return (
            f"контекст {ctx!r} не различает {w_i!r} и {w_j!r} "
            f"(оба слова оракул относит к одному классу)"
        )
    return nerode_check.get("reason", "nerode proof refuted by oracle")


def run_retry_planner_node(state: PipelineState) -> dict:
    """Run the retry planner agent to decide which specialists to re-run.

    docs/VERDICT_POLICY.md R7: the planner input carries each dispatched
    specialist's deterministic `trust` (from the pumping/nerode step-2
    checks and the closure Nerode-index estimate) plus a compact
    `previous_output` summary and the oracle counterexamples behind a
    `refuted` verdict -- from `pumping_verification`/`nerode_verification`/
    `test_result` -- not just status/verdict strings, so the planner can
    tell "failed honestly" apart from "produced a proof the oracle
    disproves".

    TODO.md §2 / spec: the planner may ADD an agent that never ran this
    round (`agents_to_retry` is not restricted to `dispatched`), may
    request hypothesis inversion via `should_invert_hypothesis` (subject to
    `MAX_INVERSIONS`), and an explicitly EMPTY `agents_to_retry` is a
    terminal decision (nothing more to run) -- never silently re-read as
    "no preference, retry everything" (`decide_after_retry_planner`).

    Cost ceiling (config.MAX_CALLS_PER_AGENT): any agent the planner names
    that has already used up its call budget is dropped from
    `agents_to_retry` right here, not left for `run_specialist_node` to
    silently no-op on -- a plan made ENTIRELY of capped agents becomes an
    empty plan, which is the terminal case above, instead of spending a
    whole extra graph round (dispatch + fan-out + fan-in) for zero new
    specialist output. Each dropped agent is noted in `call_cap_notes` for
    `verdict_gate.downgrades`, same as a cap hit inside
    `run_specialist_node` itself.
    """
    reasoning_output = state.get("reasoning_output")
    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    issues = r_ev.get(
        "issues_found",
        (reasoning_output or {}).get("issues_found", []),
    )

    dispatch = state.get("dispatch", {})
    dispatched = [k for k, v in dispatch.items() if v]
    evidence = state.get("evidence", {})
    test_result = state.get("test_result")

    counterexamples: dict[str, Any] = {}

    pumping_check = evidence.get("pumping_verification")
    if isinstance(pumping_check, dict) and pumping_check.get("trust") == "refuted":
        counterexamples["pumping"] = {
            **(pumping_check.get("counterexample") or {}),
            "hint": _pumping_refuted_hint(pumping_check),
        }

    nerode_check = evidence.get("nerode_verification")
    if isinstance(nerode_check, dict) and nerode_check.get("trust") == "refuted":
        counterexamples["nerode"] = {
            **(nerode_check.get("counterexample") or {}),
            "hint": _nerode_refuted_hint(nerode_check),
        }

    if test_result and test_result.get("status") == "fail":
        counterexamples["oracle_test"] = test_result.get("counterexample")

    planner_input = {
        "issues_found": issues,
        "oracle_counterexample": (
            test_result.get("counterexample") if test_result else None
        ),
        "specialist_results": {
            k: {
                "status": evidence[k].get("status", "?"),
                "verdict": evidence[k]
                    .get("evidence", evidence[k])
                    .get("verdict", "?"),
                "trust": _specialist_trust(k, evidence),
                "previous_output": _summarize_agent_output(evidence[k]),
            }
            for k in dispatched
            if k in evidence
        },
        "counterexamples": counterexamples,
        "current_hypothesis": state.get("hypothesis", {}).get("hypothesis"),
    }

    planner_output = run_agent(state, "retry_planner", planner_input)
    err_msgs = _agent_error_messages("retry_planner", planner_output)
    if err_msgs:
        log_msg(state, f"  retry_planner agent_error: {err_msgs}")
    p_ev = (planner_output or {}).get("evidence", planner_output or {})

    # should_invert_hypothesis: honored here (TODO.md §2) with the same
    # MAX_INVERSIONS budget as the reasoning-agent-triggered inversion
    # (decide_retry). When it fires we hand off to invert_hypothesis_node
    # unconditionally -- it resets hypothesis/dispatch/retry_context/evidence
    # itself -- so nothing else in this function needs to run, and
    # retry_round must NOT also be bumped here (invert_hypothesis_node bumps
    # it once itself; bumping it in both places would double-count a single
    # planner decision against the retry budget).
    should_invert = bool(p_ev.get("should_invert_hypothesis", False))
    inversions_done = state.get("inversions_done", 0)
    if should_invert and inversions_done < MAX_INVERSIONS:
        log_msg(state, "  retry_planner: requests hypothesis inversion")
        result: dict[str, Any] = {
            "retry_plan": {"should_invert": True, "has_retry": False},
        }
        if err_msgs:
            result["errors"] = err_msgs
        return result

    # agents_to_retry may name agents that never ran this round at all --
    # the planner can ADD a specialist, not just narrow the existing set.
    # Filter to known specialist names (typo/hallucination safety) and drop
    # grammar_analyzer when this isn't a grammar-kind task.
    valid_agents = set(SPECIALIST_NAMES)
    if state.get("lang_kind", "") != "grammar":
        valid_agents = valid_agents - {"grammar_analyzer"}
    requested_retry = [
        a for a in p_ev.get("agents_to_retry", dispatched) if a in valid_agents
    ]
    feedback_map = p_ev.get("feedback", {})
    skip_agents = set(p_ev.get("skip_agents", []))

    # Cost ceiling (config.MAX_CALLS_PER_AGENT): an agent already at its call
    # cap must not be handed back to run_specialist_node only to be silently
    # skipped there (a wasted graph round: dispatch, fan-out, fan-in, all for
    # zero new specialist output) -- filter it out of the retry plan itself,
    # counted the same way run_specialist_node counts it (from the full,
    # never-reset `specialist_outputs` history), and note the exclusion for
    # verdict_gate.downgrades the same way a cap hit inside
    # run_specialist_node already does.
    capped_notes = []
    filtered_retry = []
    for a in requested_retry:
        prior = sum(1 for n, _ in state.get("specialist_outputs", []) if n == a)
        if prior >= MAX_CALLS_PER_AGENT:
            capped_notes.append(
                f"agent {a} call cap reached ({MAX_CALLS_PER_AGENT} calls), "
                "excluded from retry plan"
            )
        else:
            filtered_retry.append(a)
    requested_retry = filtered_retry

    log_msg(
        state,
        f"  retry_planner: retry {requested_retry}, skip {list(skip_agents)}"
        + (f", capped {capped_notes}" if capped_notes else ""),
    )

    all_names = set(dispatched) | set(requested_retry)
    new_dispatch = {k: (k in requested_retry) for k in all_names}
    retry_round = state.get("retry_round", 0) + 1

    retry_context = {
        "issues": issues,
        "oracle_counterexample": planner_input["oracle_counterexample"],
        "counterexamples": counterexamples,
        "feedback": feedback_map,
        "instruction": (
            "Previous attempt had issues. See 'feedback' for "
            "agent-specific corrections."
        ),
    }

    result = {
        "dispatch": new_dispatch,
        "retry_round": retry_round,
        "retry_context": retry_context,
        # An explicitly empty agents_to_retry is a TERMINAL decision (the
        # planner found nothing worth re-running), not "no preference" --
        # decide_after_retry_planner routes it straight to formalize/assemble
        # instead of cycling back through setup_dispatch_node, which would
        # otherwise fall through to a full re-dispatch of every specialist
        # (setup_dispatch_node treats an all-False dispatch dict as "not yet
        # configured").
        "retry_plan": {"should_invert": False, "has_retry": bool(requested_retry)},
    }
    if capped_notes:
        result["call_cap_notes"] = capped_notes
    if err_msgs:
        result["errors"] = err_msgs
    return result


def decide_after_retry_planner(state: PipelineState) -> str:
    """Conditional edge after run_retry_planner_node.

    Mirrors dcfl_system's `decide_after_retry_planner` (docs/VERDICT_POLICY.md
    §3, R7): an empty retry plan is terminal (render as-is), and a plan that
    asked for hypothesis inversion routes there instead of back through
    setup_dispatch_node.
    """
    plan = state.get("retry_plan") or {}
    if plan.get("should_invert"):
        return "invert"
    if plan.get("has_retry"):
        return "retry"
    return "terminal"


def invert_hypothesis_node(state: PipelineState) -> dict:
    """Invert the hypothesis and set up retry context."""
    reasoning_output = state.get("reasoning_output")
    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    issues = r_ev.get(
        "issues_found",
        (reasoning_output or {}).get("issues_found", []),
    )

    hypothesis = dict(state.get("hypothesis", {}))
    old_hyp = hypothesis.get("hypothesis", "unknown")
    new_hyp = "regular" if old_hyp == "non_regular" else "non_regular"

    log_msg(state, f"  reasoning: inverting hypothesis {old_hyp} → {new_hyp}")

    hypothesis["hypothesis"] = new_hyp
    inversions_done = state.get("inversions_done", 0) + 1
    retry_round = state.get("retry_round", 0) + 1

    evidence = dict(state.get("evidence", {}))
    evidence["hypothesis"] = hypothesis

    # Reset dispatch so setup_dispatch_node builds a fresh full dispatch
    dispatch = {
        "re_builder": True,
        "dfa_builder": True,
        "pumping": True,
        "nerode": True,
        "closure": True,
    }
    lang_kind = state.get("lang_kind", "")
    if lang_kind == "grammar":
        dispatch["grammar_analyzer"] = True

    retry_context = {
        "issues": issues,
        "instruction": (
            f"Hypothesis inverted from {old_hyp} to {new_hyp}. "
            f"Previous agents failed. Try the opposite approach."
        ),
    }

    return {
        "hypothesis": hypothesis,
        "evidence": evidence,
        "inversions_done": inversions_done,
        "retry_round": retry_round,
        "retry_context": retry_context,
        "dispatch": dispatch,
    }


def _lean_stmt_field(statement: Any, name: str, default: Any = None) -> Any:
    """Read *name* off a `lean_ir.render_statement` result, which may be
    the dataclass-like object the real contract returns or a plain dict
    (as used by tests / a fallback statement snapshot)."""
    if isinstance(statement, dict):
        return statement.get(name, default)
    return getattr(statement, name, default)


def formalize_node(state: PipelineState) -> dict:
    """Step 8: Lean 4 formal proof (docs/VERDICT_POLICY.md R-Lean).

    The theorem *statement* is generated deterministically from the IR by
    ``lib.lean_ir.render_statement(ir, direction)`` -- never by the LLM
    (R-Lean: "the formulation ... is generated deterministically from the
    IR, the LLM formalizer writes only the proof body; the formulation
    from the LLM's output is never used"). The formalizer agent only
    fills in the statement's proof (``compose_lean_file``); on a retry (up to
    ``config.MAX_FORMALIZE_ITERATIONS``) it gets back the previous
    attempt's ``check_lean_file`` ``errors[]``, never a new statement.

    Disabled by default (``FORMALIZATION_ENABLED``, itself defaulted from
    the ``TFL_FORMALIZATION`` env var). A per-call override --
    ``run_pipeline(..., formalize=...)`` / the CLI's ``--formalize`` --
    takes priority via ``state["formalize"]``.

    Sets ``state["formalization"]`` to ``{status, direction, statement,
    proof_body, attempts, errors, axioms, elapsed}`` -- read directly by
    `assemble_result_node`'s gate (``status`` is one of ``proved`` /
    ``has_sorry`` / ``error`` / ``timeout`` / ``unavailable`` /
    ``not_formalizable``). Without an agent/mock runner, or when
    reasoning didn't ask for it, this is a no-op (``{}``) -- indistinguishable
    from "skipped" to the gate, which only ever acts on a *present*
    ``formalization["status"] == "proved"``.
    """
    log_msg(state, "Step 8/9: formalization...")

    formalize_override = state.get("formalize")
    enabled = FORMALIZATION_ENABLED if formalize_override is None else formalize_override
    if not enabled:
        log_msg(state, "  formalization: disabled")
        return {}

    reasoning_output = state.get("reasoning_output")
    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    action = r_ev.get("action", (reasoning_output or {}).get("action", ""))
    consolidated = (
        r_ev.get("consolidated_proof")
        or (reasoning_output or {}).get("consolidated_proof", "")
    )

    has_runner = (
        state.get("agent_runner") is not None
        or state.get("mock_runner") is not None
    )
    if action != "proceed_to_formalizer" or not has_runner:
        log_msg(state, "  formalization: skipped (not requested or no runner)")
        return {}

    ir = state["ir"]
    hypothesis = state.get("hypothesis", {})
    direction = (
        r_ev.get("verdict")
        or (reasoning_output or {}).get("verdict")
        or hypothesis.get("hypothesis")
    )

    # lean_ir.py's contract (render_statement) is owned by a parallel
    # agent; import lazily so a run without it yet degrades to
    # `not_formalizable` instead of failing the whole pipeline.
    try:
        from .lib.lean_ir import render_statement
    except ImportError as exc:
        log_msg(state, f"  formalization: lib.lean_ir unavailable ({exc})")
        return {"formalization": {
            "status": "not_formalizable",
            "direction": direction,
            "reason": f"lib.lean_ir not available: {exc}",
        }}

    try:
        statement = render_statement(ir, direction)
    except Exception as exc:  # pragma: no cover - defensive; contract owned elsewhere
        log_msg(state, f"  formalization: render_statement raised ({exc})")
        return {"formalization": {
            "status": "not_formalizable",
            "direction": direction,
            "reason": f"render_statement raised: {exc}",
        }}

    if statement is None:
        log_msg(state, "  formalization: no Lean statement for this ir/direction")
        return {"formalization": {
            "status": "not_formalizable",
            "direction": direction,
            "reason": "render_statement returned no statement for this ir/direction",
        }}

    statement_snapshot = {
        "imports": _lean_stmt_field(statement, "imports"),
        "alphabet_decl": _lean_stmt_field(statement, "alphabet_decl"),
        "language_decl": _lean_stmt_field(statement, "language_decl"),
        "theorem_decl": _lean_stmt_field(statement, "theorem_decl"),
        "name": _lean_stmt_field(statement, "name") or "tfl_main",
    }

    attempts: list[dict] = []
    errors: list = []
    proof_body: str | None = None
    axioms: list = []
    status = "error"
    elapsed_total = 0.0

    for attempt_num in range(1, MAX_FORMALIZE_ITERATIONS + 1):
        formalizer_input: dict[str, Any] = {
            "statement": statement_snapshot,
            "plan": consolidated,
            "available_lemmas": [],
        }
        if errors:
            formalizer_input["errors"] = errors
            formalizer_input["previous_proof_body"] = proof_body

        log_msg(state, f"  formalizer: attempt {attempt_num}/{MAX_FORMALIZE_ITERATIONS}")
        formalizer_output = run_agent(state, "formalizer", formalizer_input)
        err_msgs = _agent_error_messages("formalizer", formalizer_output)
        if err_msgs:
            log_msg(state, f"  formalizer agent_error: {err_msgs}")
            return {"errors": err_msgs}
        if formalizer_output is None:
            log_msg(state, "  formalizer: no output")
            status = "error"
            errors = ["formalizer produced no output"]
            attempts.append({"attempt": attempt_num, "status": "no_output", "errors": errors})
            break

        f_ev = formalizer_output.get("evidence", formalizer_output)
        new_proof_body = f_ev.get("proof_body") if isinstance(f_ev, dict) else None
        if not new_proof_body:
            log_msg(state, "  formalizer: no proof_body in output")
            status = "error"
            errors = ["formalizer output had no proof_body"]
            attempts.append({"attempt": attempt_num, "status": "no_proof_body", "errors": errors})
            if attempt_num < MAX_FORMALIZE_ITERATIONS:
                continue
            break

        proof_body = new_proof_body
        lean_text = compose_lean_file(statement, proof_body)
        tc = check_lean_file(lean_text, timeout=LEAN_TIMEOUT)
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
            # Not evidence either way (R1) and not worth another
            # attempt: `sorry`/a timeout/no Docker image won't be fixed
            # by asking the formalizer to re-edit the same proof body.
            break
        # tc_status == "error": retry with the errors fed back (same
        # statement -- only `formalizer_input["errors"]` changes above).

    formalization = {
        "status": status,
        "direction": direction,
        "statement": statement_snapshot,
        "proof_body": proof_body,
        "attempts": attempts,
        "errors": errors,
        "axioms": axioms,
        "elapsed": round(elapsed_total, 2),
    }
    return {"formalization": formalization}


def _set_reasoning_verdict(evidence: dict, verdict: str | None) -> None:
    """Overwrite the verdict inside `evidence['reasoning']` (both the flat
    shape and the nested `reasoning['evidence']` shape reasoning prompts
    also use) so downstream readers of the top-level result (`orchestrator.
    _result_verdict`, `tfl_eval.runners.extract('reg')`) see the verdict
    gate's own decision, not whatever the reasoning agent originally
    proposed before R1-R3/R3' were applied. Mutates a copy, never the
    dict `state["reasoning_output"]` still holds."""
    reasoning_copy = dict(evidence.get("reasoning") or {})
    inner = reasoning_copy.get("evidence")
    if isinstance(inner, dict):
        inner = dict(inner)
        inner["verdict"] = verdict
        reasoning_copy["evidence"] = inner
    reasoning_copy["verdict"] = verdict
    evidence["reasoning"] = reasoning_copy


def _cross_check_r3prime_reg(
    state: PipelineState,
    dfa: dict | None,
    destructive_witnesses: list[dict],
) -> dict[str, Any]:
    """docs/VERDICT_POLICY.md R3' for REG — deterministic cross-check tried
    before falling back to an unresolved R3 contradiction.

    Mirrors cfl_system.orchestrator._cross_check_r3prime, simplified to
    REG's single constructive-artifact channel (the DFA behind
    `test_result` — there is no separate "which agent produced it" choice
    the way CFL has cfg_builder vs pda_builder): runs the destructive
    proof's own witness words (already instantiated by `verify_pumping_
    claim`/`verify_nerode_claim`'s step-2 checks — reused via
    `evidence["pumping_verification"|"nerode_verification"]["witnesses"]`,
    never re-derived) through both the task's language oracle and the DFA:
      (a) a witness the destructive proof claims is NOT in L, but the DFA
          accepts (or claims IN L, but the DFA rejects) -> the DFA
          (constructive side) is `refuted`.
      (b) a witness whose claimed membership disagrees with what the
          oracle itself says -> the destructive proof is `refuted`.
    Never raises; missing evidence (no witnesses, no oracle, no DFA) just
    leaves `performed=False` and nothing gets refuted.
    """
    result: dict[str, Any] = {
        "performed": False,
        "constructive_refuted": False,
        "destructive_refuted": False,
        "constructive_reason": None,
        "destructive_reason": None,
        "counterexamples": [],
    }
    if not destructive_witnesses or dfa is None:
        return result
    oracle_fn = state.get("oracle_fn")
    if oracle_fn is None:
        return result

    result["performed"] = True

    for w in destructive_witnesses:
        if not isinstance(w, dict):
            continue
        word = w.get("word")
        expected_in_l = w.get("expected_in_l")
        if not isinstance(word, str) or not word or not isinstance(expected_in_l, bool):
            continue
        try:
            oracle_says = bool(oracle_fn(word))
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

        if not result["constructive_refuted"] and oracle_says == expected_in_l:
            # Only test the DFA against a witness the oracle itself just
            # confirmed -- an oracle-refuted witness says nothing about it.
            try:
                dfa_says = bool(run_dfa(dfa, word))
            except Exception:
                continue
            if dfa_says != expected_in_l:
                result["constructive_refuted"] = True
                verb = "rejects" if expected_in_l else "accepts"
                result["constructive_reason"] = (
                    f"DFA {verb} '{word}' (source: {w.get('source')}), which the "
                    f"oracle says is {'in L' if expected_in_l else 'NOT in L'}"
                )
                result["counterexamples"].append({"word": word, "issue": result["constructive_reason"]})

        if result["constructive_refuted"] and result["destructive_refuted"]:
            break

    return result


def assemble_result_node(state: PipelineState) -> dict:
    """Step 9: assemble the final result dict.

    Implements docs/VERDICT_POLICY.md R6 (agent_system): the final
    status/confidence is bounded by the *trust* of the evidence behind it
    (see §1-§2), never derived from a bare test_result.pass/fail or an
    unbounded LLM self-estimate. See R1 (constructive failure is not
    destructive evidence), R3 (contradiction) and R6 (regularity gate).
    """
    log_msg(state, "Step 9/9: assembling result...")

    from .lib.claim_verifier import (
        CONFIDENCE_CAPS,
        compute_destructive_trust,
        compute_lean_proof_trust,
    )

    evidence = dict(state.get("evidence", {}))
    errors = list(state.get("errors", []))
    reasoning_output = state.get("reasoning_output")
    test_result = state.get("test_result")
    hypothesis = state.get("hypothesis", {})
    closure_verification = (
        state.get("closure_verification") or evidence.get("closure_verification")
    )

    # Surface `formalize_node`'s output in the final result too (not just the
    # gate below) -- otherwise a `proved` Lean statement/proof_body that
    # earned `verified` trust would be invisible to anything reading the
    # returned result (report generation, the CLI's JSON/MD output), leaving
    # only the `verdict_gate.basis` entry as indirect evidence it happened.
    formalization = state.get("formalization")
    if formalization is not None:
        evidence["formalization"] = formalization

    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    reasoning_verdict = r_ev.get(
        "verdict", (reasoning_output or {}).get("verdict"),
    )
    reasoning_confidence = r_ev.get(
        "confidence", (reasoning_output or {}).get("confidence"),
    )
    reasoning_action = r_ev.get(
        "action", (reasoning_output or {}).get("action", ""),
    )

    downgrades: list[str] = []
    basis: list[dict[str, str]] = []
    contradiction = False

    def _cap(trust: str) -> float:
        return CONFIDENCE_CAPS.get(trust, CONFIDENCE_CAPS["not_verified"])

    def _bounded(value: Any, cap: float) -> float:
        try:
            return min(float(value), cap)
        except (TypeError, ValueError):
            return cap

    # Escalation → needs human review, never mark as "success"
    if reasoning_action == "escalate":
        status = "partial"
        confidence = float(reasoning_confidence or hypothesis.get("confidence", 0.0))
        evidence["needs_human_review"] = True
    else:
        # -- Lean formal proof (docs/VERDICT_POLICY.md R-Lean): a
        #    deterministic `status == "proved"` result (no compile errors,
        #    no `sorry`, axioms ⊆ {propext, Classical.choice, Quot.sound} --
        #    all already enforced by type_check.check_lean_file) is a
        #    machine-checked proof, so it earns the full `verified` 0.98
        #    ceiling and takes priority over every other track. `direction`
        #    is the verdict formalize_node rendered the statement for; when
        #    it disagrees with `reasoning_verdict` the formal proof still
        #    wins and the verdict flips to the proven direction instead of
        #    being reported as an unresolved contradiction (R-Lean, R3). Any
        #    other status (has_sorry/error/timeout/unavailable/
        #    not_formalizable/missing) is not evidence either way (R1) and
        #    never changes the gate -- `compute_lean_proof_trust` reports
        #    those as `not_verified`.
        lean_trust = compute_lean_proof_trust(state.get("formalization"))
        lean_proved = lean_trust["trust"] == "verified"
        lean_direction = lean_trust["direction"]

        # -- Constructive trust: from the DFA/regex oracle test --
        constructive_trust = None
        if test_result is not None:
            constructive_trust = (
                "bounded_pass" if test_result.get("status") == "pass" else "refuted"
            )
            basis.append({"agent": "oracle_test", "trust": constructive_trust})

        # -- Destructive trust: pumping/nerode/closure, step-2 checked --
        destructive = compute_destructive_trust(evidence, closure_verification)
        for agent_info in destructive["agents"]:
            basis.append({"agent": agent_info["agent"], "trust": agent_info["trust"]})
        destructive_trust = destructive["trust"]
        destructive_ok = destructive_trust not in (None, "refuted", "not_verified")

        if lean_proved and lean_direction and reasoning_verdict and lean_direction != reasoning_verdict:
            # R-Lean + R3: a machine-checked proof of the OPPOSITE direction
            # outranks the reasoning agent's own (unverified) claim -- the
            # verdict flips to the proven direction at the full verified
            # ceiling, rather than being left as a capped contradiction.
            contradiction = True
            status = "success"
            confidence = CONFIDENCE_CAPS["verified"]
            basis.append({"agent": "formalizer", "trust": "verified", "basis": "lean_proof"})
            downgrades.append(
                f"Lean proof verified for '{lean_direction}', opposite of the proposed "
                f"'{reasoning_verdict}' -> verdict changed to '{lean_direction}', verified "
                "0.98 (VERDICT_POLICY.md R-Lean: a machine-checked proof takes priority "
                "over every other track; R3)"
            )
            _set_reasoning_verdict(evidence, lean_direction)

        elif lean_proved:
            # Direction matches `reasoning_verdict` (or the statement carried
            # no explicit direction) -- uncontested, full verified ceiling.
            status = "success"
            confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["verified"])
            basis.append({"agent": "formalizer", "trust": "verified", "basis": "lean_proof"})

        elif constructive_trust == "bounded_pass" and destructive_ok:
            # R3: a passing constructive artifact AND a standing destructive
            # proof cannot both be right. R3' first: try to resolve it
            # deterministically by running the destructive proof's own
            # witness words (reused from claim_verifier's step-2 checks,
            # never re-derived) through the language oracle AND the DFA
            # behind test_result.
            destructive_witnesses: list[dict] = []
            for agent_info in destructive["agents"]:
                det = agent_info.get("details") or {}
                w = det.get("witnesses")
                if isinstance(w, list):
                    destructive_witnesses.extend(w)
            cross_check = _cross_check_r3prime_reg(state, state.get("dfa"), destructive_witnesses)

            if cross_check["destructive_refuted"] and not cross_check["constructive_refuted"]:
                # R3' resolved it: the DFA survives, the destructive proof
                # itself doesn't -> regular, capped at its own bounded_pass
                # ceiling (not the 0.50 unresolved-contradiction cap).
                # `contradiction` stays False -- it was raised and resolved
                # deterministically, not left standing (mirrors
                # cfl_system.orchestrator.apply_verdict_gate's R3' branch).
                status = "success"
                confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["bounded_pass"])
                basis.append({"agent": "r3prime_cross_check", "trust": "bounded_pass"})
                downgrades.append(
                    "R3' cross-check refuted destructive claim: "
                    f"{cross_check['destructive_reason']} -> regular "
                    "(VERDICT_POLICY.md R3')"
                )
                _set_reasoning_verdict(evidence, "regular")
            elif cross_check["constructive_refuted"] and not cross_check["destructive_refuted"]:
                # R3' resolved it the other way: the DFA itself is wrong ->
                # non_regular, capped at the destructive proof's own ceiling.
                # `contradiction` stays False (see comment above).
                status = "success"
                confidence = _bounded(reasoning_confidence, _cap(destructive_trust))
                basis.append({"agent": "r3prime_cross_check", "trust": destructive_trust})
                downgrades.append(
                    "R3' cross-check refuted constructive artifact (DFA): "
                    f"{cross_check['constructive_reason']} -> non_regular "
                    "(VERDICT_POLICY.md R3')"
                )
                _set_reasoning_verdict(evidence, "non_regular")
            else:
                # Unresolved (neither refuted, both refuted, or the
                # cross-check couldn't run at all) -- stays inconclusive.
                # Neither side is `verified` here (that case is handled
                # above), so rank never settles this (docs/VERDICT_POLICY.md
                # R3 fix: "bounded_pass vs well_formed" is never resolved by
                # rank). The reasoning agent's own proposed verdict must not
                # leak through as if it were confirmed (reviewer finding).
                contradiction = True
                status = "partial"
                confidence = _bounded(reasoning_confidence, 0.50)
                downgrades.append(
                    "contradiction: oracle_test passed (constructive bounded_pass) "
                    f"while a destructive proof ({destructive_trust}) also stands, "
                    "unresolved by R3' cross-check -> confidence capped at 0.50 "
                    "(VERDICT_POLICY.md R3)"
                )
                _set_reasoning_verdict(evidence, None)

        elif reasoning_verdict == "non_regular":
            # docs/VERDICT_POLICY.md R1/R2 fix (reviewer finding): the gate
            # must branch on which DIRECTION reasoning actually claimed, not
            # just "is there ANY evidence lying around" — a destructive proof
            # only ever supports non_regular, so it must never be read as
            # backing a "regular" verdict (see the `regular` branch below),
            # and a claimed non_regular with NO destructive evidence at all
            # (regardless of whether the constructive side happened to be
            # refuted, absent, or even passed) is never earned.
            if destructive_ok:
                status = "success"
                confidence = _bounded(reasoning_confidence, _cap(destructive_trust))
                if constructive_trust == "refuted":
                    downgrades.append(
                        "constructive artifact refuted by oracle_test, but a "
                        f"destructive proof ({destructive_trust}) supports non_regular "
                        "-> success instead of failure 0.0 (VERDICT_POLICY.md R6)"
                    )
            elif constructive_trust == "bounded_pass":
                # docs/VERDICT_POLICY.md R4' — reasoning's own non_regular
                # proposal has no destructive proof reaching >= well_formed,
                # but oracle_test actually passed: the gate does not default
                # straight to inconclusive. It picks the strongest admissible
                # basis still standing -- here, the constructive side --
                # rather than reporting `failure`/inconclusive (precedent:
                # cfl-07/cfl-12 eval live-run ending in `failure 0.0` despite
                # deterministic evidence being on record).
                status = "success"
                confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["bounded_pass"])
                downgrades.append(
                    "reasoning proposed non_regular without a >= well_formed destructive "
                    "proof, but oracle_test passed -> retry budget exhausted -> strongest "
                    "admissible basis: regular (bounded_pass) (VERDICT_POLICY.md R4')"
                )
                _set_reasoning_verdict(evidence, "regular")
            else:
                status = "partial"
                confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["not_verified"])
                evidence["basis"] = "constructive_failure_only"
                downgrades.append(
                    "reasoning proposed non_regular but no destructive proof reaches "
                    ">= well_formed trust -> partial, confidence <= 0.40 "
                    "(VERDICT_POLICY.md R1/R2)"
                )

        elif reasoning_verdict == "regular":
            # Symmetric direction check: a "regular" verdict needs the
            # constructive artifact (oracle_test) to have actually passed —
            # a destructive proof standing next to it (or a refuted/absent
            # oracle_test) is never evidence FOR regular on its own.
            if constructive_trust == "bounded_pass":
                status = "success"
                confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["bounded_pass"])
            elif destructive_ok:
                # docs/VERDICT_POLICY.md R4' — reasoning's own regular
                # proposal is unearned (oracle_test refuted/absent), but a
                # destructive proof >= well_formed does stand: the gate
                # picks that strongest admissible basis instead of
                # defaulting to inconclusive (same precedent as above).
                status = "success"
                confidence = _bounded(reasoning_confidence, _cap(destructive_trust))
                downgrades.append(
                    "reasoning proposed regular without oracle_test passing, but a "
                    f"destructive proof ({destructive_trust}) stands -> retry budget "
                    "exhausted -> strongest admissible basis: non_regular "
                    "(VERDICT_POLICY.md R4')"
                )
                _set_reasoning_verdict(evidence, "non_regular")
            else:
                status = "partial"
                confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["not_verified"])
                downgrades.append(
                    "reasoning proposed regular without oracle_test passing "
                    "(constructive trust >= bounded_pass) -> partial, "
                    "confidence <= 0.40 (VERDICT_POLICY.md R2)"
                )

        elif constructive_trust == "bounded_pass":
            # No reasoning verdict to check direction against (e.g. no
            # reasoning agent ran) — fall back to whatever deterministic
            # evidence exists, as before.
            status = "success"
            confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["bounded_pass"])

        elif destructive_ok:
            # R6: a real destructive proof (well_formed/bounded_pass, oracle
            # step-2 checked) supports non_regular even when the
            # constructive track was refuted or never ran at all — never
            # "failure 0.0" for a verdict that the destructive side proves.
            status = "success"
            confidence = _bounded(reasoning_confidence, _cap(destructive_trust))
            if constructive_trust == "refuted":
                downgrades.append(
                    "constructive artifact refuted by oracle_test, but a "
                    f"destructive proof ({destructive_trust}) supports non_regular "
                    "-> success instead of failure 0.0 (VERDICT_POLICY.md R6)"
                )

        elif constructive_trust == "refuted":
            # R1: a refuted constructive artifact alone is never destructive
            # evidence -> inconclusive, not "failure 0.0".
            status = "partial"
            confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["not_verified"])
            evidence["basis"] = "constructive_failure_only"
            downgrades.append(
                "constructive artifact refuted by oracle_test and no verified "
                "destructive proof exists -> inconclusive, not failure 0.0 "
                "(VERDICT_POLICY.md R1)"
            )

        elif reasoning_verdict is not None:
            # Reasoning proposed some other (unrecognized) verdict value,
            # and nothing deterministic backs it -> capped at well_formed
            # (structural-only trust).
            status = "success"
            confidence = _bounded(reasoning_confidence, CONFIDENCE_CAPS["well_formed"])
            downgrades.append(
                "reasoning verdict has no deterministic evidence behind it "
                "-> capped at well_formed 0.55 (VERDICT_POLICY.md §2)"
            )

        elif errors:
            status = "partial"
            confidence = _bounded(
                hypothesis.get("confidence", 0.0), CONFIDENCE_CAPS["not_verified"],
            )
        else:
            status = "partial"
            confidence = _bounded(
                hypothesis.get("confidence", 0.0), CONFIDENCE_CAPS["not_verified"],
            )

    log_msg(state, f"  final: status={status}, confidence={confidence}")

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
        "confidence_cap": confidence,
    }
    evidence["verdict_gate"] = verdict_gate

    result = _make_result(
        status,
        evidence=evidence,
        errors=errors if errors else None,
        confidence=confidence,
    )
    # Also top-level, per VERDICT_POLICY.md §5 (new field, additive only).
    result["verdict_gate"] = verdict_gate

    return {"result": result}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_full_pipeline_graph() -> Any:
    """Build and compile the full LangGraph pipeline.

    Returns:
        A compiled StateGraph ready for ``.invoke(initial_state)``.
    """
    graph = StateGraph(PipelineState)

    # -- Register nodes --
    graph.add_node("validate_ir_node", validate_ir_node)
    graph.add_node("assemble_early_failure", assemble_early_failure)
    graph.add_node("analyze_hypothesis_node", analyze_hypothesis_node)
    graph.add_node("run_classifier_node", run_classifier_node)
    graph.add_node("grammar_preprocess_node", grammar_preprocess_node)
    graph.add_node("setup_dispatch_node", setup_dispatch_node)
    graph.add_node("run_specialist_node", run_specialist_node)
    graph.add_node("collect_specialists_node", collect_specialists_node)
    graph.add_node("build_oracle_node", build_oracle_node)
    graph.add_node("verify_closure_node", verify_closure_node)
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

    # validate → conditional: fail or continue
    graph.add_conditional_edges(
        "validate_ir_node",
        lambda state: "fail" if state.get("errors") else "ok",
        {"fail": "assemble_early_failure", "ok": "analyze_hypothesis_node"},
    )

    # Early failure → END
    graph.add_edge("assemble_early_failure", END)

    # hypothesis → classifier → grammar preprocess → setup dispatch
    graph.add_edge("analyze_hypothesis_node", "run_classifier_node")
    graph.add_edge("run_classifier_node", "grammar_preprocess_node")
    graph.add_edge("grammar_preprocess_node", "setup_dispatch_node")

    # setup_dispatch → fan-out via Send()
    graph.add_conditional_edges(
        "setup_dispatch_node",
        dispatch_to_specialists,
        ["run_specialist_node", "collect_specialists_node"],
    )

    # specialist fan-in → collect
    graph.add_edge("run_specialist_node", "collect_specialists_node")

    # collect → oracle build → verify closure → verify claims → oracle test
    graph.add_edge("collect_specialists_node", "build_oracle_node")
    graph.add_edge("build_oracle_node", "verify_closure_node")
    graph.add_edge("verify_closure_node", "verify_claims_node")
    graph.add_edge("verify_claims_node", "oracle_test_node")

    # oracle test → proof checker → reasoning
    graph.add_edge("oracle_test_node", "run_proof_checker_node")
    graph.add_edge("run_proof_checker_node", "run_reasoning_node")

    # reasoning → conditional: retry / invert / done
    graph.add_conditional_edges(
        "run_reasoning_node",
        decide_retry,
        {
            "retry": "run_retry_planner_node",
            "invert": "invert_hypothesis_node",
            "done": "formalize_node",
        },
    )

    # retry planner → conditional: retry (cycle) / invert / terminal
    graph.add_conditional_edges(
        "run_retry_planner_node",
        decide_after_retry_planner,
        {
            "retry": "setup_dispatch_node",
            "invert": "invert_hypothesis_node",
            "terminal": "formalize_node",
        },
    )

    # invert hypothesis → back to dispatch (cycle)
    graph.add_edge("invert_hypothesis_node", "setup_dispatch_node")

    # formalize → assemble → END
    graph.add_edge("formalize_node", "assemble_result_node")
    graph.add_edge("assemble_result_node", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_pipeline(
    ir: dict,
    mock_runner: Any = None,
    agent_runner: Any = None,
    verbose: bool | None = None,
    formalize: bool | None = None,
) -> dict[str, Any]:
    """Run the full pipeline and return the result dict.

    This is the primary entry point — it builds the graph, constructs the
    initial state, invokes the graph, and extracts the result.

    Args:
        ir: Validated IR dictionary.
        mock_runner: Optional MockRunner for testing.
        agent_runner: Optional LLMRunner for production.
        verbose: Override verbose mode.  Defaults to True when
                 agent_runner is provided.
        formalize: Override for Lean 4 formalization (`formalize_node`).
                   ``None`` (default) uses ``FORMALIZATION_ENABLED``
                   (itself defaulted from the ``TFL_FORMALIZATION`` env
                   var); ``True``/``False`` force it on/off for this run.

    Returns:
        Structured result per section 5.3 output contract.
    """
    if verbose is None:
        verbose = agent_runner is not None

    graph = build_full_pipeline_graph()

    initial_state: dict[str, Any] = {
        "ir": ir,
        "mock_runner": mock_runner,
        "agent_runner": agent_runner,
        "verbose": verbose,
        "formalize": formalize,
        # Initialise accumulator fields
        "hypothesis": {},
        "classifier_output": {},
        "classifier_evidence": {},
        "grammar_facts": {},
        "lang_kind": "",
        "student_notes": "",
        "dispatch": {},
        "specialist_outputs": [],
        "dfa_builder_output": None,
        "oracle_fn": None,
        "oracle_ok": False,
        "closure_verification": {},
        "claim_verification": {},
        "test_result": None,
        "dfa": None,
        "reasoning_output": None,
        "proof_checker_output": None,
        "retry_round": 0,
        "inversions_done": 0,
        "retry_context": {},
        "retry_plan": {},
        "formalization": None,
        "evidence": {},
        "errors": [],
        "call_cap_notes": [],
        "_specialist_name": "",
        "result": {},
    }

    final_state = graph.invoke(initial_state)
    result = final_state.get("result", _make_result("failure", errors=["Graph produced no result"]))
    # Usage/cost block (TODO.md §3) -- additive: never replaces existing
    # result keys. Present even without a live agent_runner (an all-zero
    # UsageTracker) so callers can rely on result["usage"] always existing.
    tracker = getattr(agent_runner, "usage_tracker", None)
    result["usage"] = tracker.as_dict() if tracker is not None else UsageTracker().as_dict()
    return result
