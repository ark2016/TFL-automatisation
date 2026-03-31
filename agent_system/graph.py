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

import operator
import sys
import time as _time
from pathlib import Path
from typing import Any, Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .lib.ir_schema import validate_ir
from .lib.oracle import oracle_from_ir
from .lib.oracle_test import oracle_test as run_oracle_test
from .lib.dfa_runner import validate_dfa, run_dfa
from .lib.hypothesis_module import analyze_hypothesis
from .lib.type_check import check_lean


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SPECIALIST_RETRIES = 2
MAX_INVERSIONS = 1

# Set to True to enable Lean 4 formalization + type checking.
# Disabled while Lean templates are under active development.
FORMALIZATION_ENABLED = False

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

    # -- Reasoning & retry --
    reasoning_output: dict
    proof_checker_output: dict
    retry_round: int
    inversions_done: int
    retry_context: dict

    # -- Accumulated --
    evidence: dict
    errors: Annotated[list, operator.add]

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
        if out is not None:
            log_msg(state, f"{agent_name}: done ({elapsed:.1f}s)")
        else:
            log_msg(state, f"{agent_name}: FAILED ({elapsed:.1f}s)")
        return out

    return None


def get_alphabet(ir: dict) -> list[str]:
    """Extract alphabet from IR, falling back to ['a', 'b']."""
    spec = ir.get("language_spec", {})
    if "alphabet" in spec:
        return spec["alphabet"]
    if "terminals" in spec:
        return spec["terminals"]
    return ["a", "b"]


def extract_dfa(agent_output: dict) -> dict | None:
    """Extract a DFA dict from a dfa_builder agent output."""
    evidence = agent_output.get("evidence", {})
    return evidence.get("dfa")


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
        inp["retry_context"] = ctx

    return inp


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
    """Run estimate_index in a daemon thread with a hard timeout.

    Uses a daemon thread so the process is not blocked if the
    computation exceeds *timeout* seconds — the thread is abandoned
    and will be cleaned up when the process exits.

    Returns the result dict on success, or a fallback dict with
    confidence=0 on timeout.
    """
    import threading
    from .lib.congruence import estimate_index

    if state:
        log_msg(state, f"  estimate_index(depth={max_depth}, timeout={timeout}s)...")

    result_box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result_box["result"] = estimate_index(oracle, alphabet, max_depth)
        except Exception as exc:
            result_box["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        if state:
            log_msg(state, f"  estimate_index TIMED OUT after {timeout}s")
        return {
            "estimated_index": "unknown",
            "confidence": 0,
            "growth_pattern": [],
            "reason": f"Timed out after {timeout}s",
        }

    if "error" in result_box:
        raise result_box["error"]

    return result_box["result"]


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

            from .lib.word_generator import generate_exhaustive
            counterexamples = []
            for w in generate_exhaustive(alphabet, max_len=8):
                if intersection_oracle(w):
                    a_count = sum(1 for c in w if c == "a")
                    b_count = sum(1 for c in w if c == "b")
                    if a_count != b_count and len(w) > 0:
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

    evidence = dict(state.get("evidence", {}))
    if classifier_output is not None:
        evidence["classifier"] = classifier_output
        verdict = classifier_output.get("evidence", {}).get("verdict", "?")
        log_msg(state, f"  classifier verdict: {verdict}")

    classifier_evidence = (classifier_output or {}).get("evidence", {})

    return {
        "classifier_output": classifier_output or {},
        "classifier_evidence": classifier_evidence,
        "evidence": evidence,
    }


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
    """Run a single specialist agent.  Invoked via Send() fan-out."""
    agent_name = state["_specialist_name"]
    inp = build_specialist_input(state, agent_name)
    out = run_agent(state, agent_name, inp)
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
    """Step 5b: verify closure agent's intersection claim."""
    oracle_ok = state.get("oracle_ok", False)
    evidence = dict(state.get("evidence", {}))

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
    """Step 5c: verify ALL word-membership claims via oracle."""
    oracle_ok = state.get("oracle_ok", False)
    if not oracle_ok:
        return {}

    from .lib.claim_verifier import verify_claims

    evidence = dict(state.get("evidence", {}))
    claims_result = verify_claims(
        evidence, state["oracle_fn"], get_alphabet(state["ir"]),
    )

    if claims_result["disproved"] > 0:
        evidence["claim_verification"] = claims_result
        log_msg(
            state,
            f"  CLAIM ERRORS: {claims_result['disproved']} false claims found!",
        )
        for err in claims_result["errors"][:5]:
            log_msg(state, f"    {err}")
        return {"claim_verification": claims_result, "evidence": evidence}
    elif claims_result["total_claims"] > 0:
        evidence["claim_verification"] = claims_result
        log_msg(
            state,
            f"  claims verified: {claims_result['verified']}"
            f"/{claims_result['total_claims']}",
        )
        return {"claim_verification": claims_result, "evidence": evidence}

    return {}


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
    has_runner = (
        state.get("agent_runner") is not None
        or state.get("mock_runner") is not None
    )
    if (
        test_result
        and test_result.get("status") == "fail"
        and test_result.get("counterexample")
        and retry_round == 0
        and has_runner
    ):
        ce = test_result["counterexample"]
        log_msg(
            state,
            f"  oracle counterexample: '{ce.get('word')}' "
            f"(oracle={ce.get('oracle_says')}, dfa={ce.get('automaton_says')})",
        )
        log_msg(state, "  → Level 1 retry: re-running DFA/RE builders with counterexample")

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

        if dispatch.get("re_builder"):
            re_out = run_agent(state, "re_builder", enriched_input)
            if re_out is not None:
                evidence["re_builder"] = re_out

        if dispatch.get("dfa_builder"):
            new_dfa_out = run_agent(state, "dfa_builder", enriched_input)
            if new_dfa_out is not None:
                dfa_builder_output = new_dfa_out
                evidence["dfa_builder"] = new_dfa_out
                dfa2 = extract_dfa(new_dfa_out)
                if dfa2 is not None and oracle_ok:
                    dfa_errs2 = validate_dfa(dfa2)
                    if not dfa_errs2:
                        test_result = run_oracle_test(
                            state["oracle_fn"], dfa2, get_alphabet(ir),
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

    new_evidence = dict(evidence)
    if reasoning_output is not None:
        new_evidence["reasoning"] = reasoning_output

    return {
        "reasoning_output": reasoning_output,
        "evidence": new_evidence,
    }


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


def run_retry_planner_node(state: PipelineState) -> dict:
    """Run the retry planner agent to decide which specialists to re-run."""
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
            }
            for k in dispatched
            if k in evidence
        },
        "current_hypothesis": state.get("hypothesis", {}).get("hypothesis"),
    }

    planner_output = run_agent(state, "retry_planner", planner_input)
    p_ev = (planner_output or {}).get("evidence", planner_output or {})

    agents_to_retry = p_ev.get("agents_to_retry", dispatched)
    feedback_map = p_ev.get("feedback", {})
    skip_agents = set(p_ev.get("skip_agents", []))

    log_msg(
        state,
        f"  retry_planner: retry {agents_to_retry}, skip {list(skip_agents)}",
    )

    new_dispatch = {k: (k in agents_to_retry) for k in dispatched}
    retry_round = state.get("retry_round", 0) + 1

    retry_context = {
        "issues": issues,
        "oracle_counterexample": planner_input["oracle_counterexample"],
        "feedback": feedback_map,
        "instruction": (
            "Previous attempt had issues. See 'feedback' for "
            "agent-specific corrections."
        ),
    }

    return {
        "dispatch": new_dispatch,
        "retry_round": retry_round,
        "retry_context": retry_context,
    }


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


def formalize_node(state: PipelineState) -> dict:
    """Step 8: formalization.

    Calls the formalizer agent when reasoning says ``proceed_to_formalizer``
    and an agent/mock runner is available.  Type-checks the resulting
    Lean 4 code via Docker.

    Disabled by default (``FORMALIZATION_ENABLED = False``).  Set the flag
    to ``True`` once Lean templates are stable.
    """
    log_msg(state, "Step 8/9: formalization...")

    if not FORMALIZATION_ENABLED:
        log_msg(state, "  formalization: disabled")
        return {}

    reasoning_output = state.get("reasoning_output")
    r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
    action = r_ev.get("action", (reasoning_output or {}).get("action", ""))
    best_proof = r_ev.get("best_proof", (reasoning_output or {}).get("best_proof", ""))
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

    evidence = dict(state.get("evidence", {}))
    ir = state["ir"]

    # Pick Lean template
    hypothesis = state.get("hypothesis", {})
    verdict = (
        r_ev.get("verdict")
        or (reasoning_output or {}).get("verdict")
        or hypothesis.get("hypothesis", "unknown")
    )
    if verdict == "regular":
        template_name = "prove_regular_via_dfa"
    elif best_proof == "nerode":
        template_name = "prove_non_regular_via_nerode"
    else:
        template_name = "prove_non_regular_via_pumping"

    template_dir = Path(__file__).parent / "templates"
    template_file = template_dir / f"{template_name}.lean"
    if not template_file.exists():
        log_msg(state, f"  template '{template_name}' not found, skipping")
        return {}
    template = template_file.read_text(encoding="utf-8")

    # Build formalizer input
    specialist_key = best_proof if best_proof in evidence else None
    if not specialist_key:
        for k in ("pumping", "nerode", "closure", "dfa_builder", "re_builder"):
            if k in evidence:
                specialist_key = k
                break
    specialist_out = evidence.get(specialist_key, {}) if specialist_key else {}
    spec_ev = specialist_out.get("evidence", specialist_out)

    formalizer_input = {
        "consolidated_proof": consolidated,
        "best_proof": best_proof,
        "template_type": template_name,
        "template_code": template,
        "specialist_output": spec_ev,
        "ir": ir,
        "dfa": evidence.get("dfa_builder", {}).get("evidence", {}).get("dfa"),
    }

    log_msg(state, f"  formalizer: template={template_name}")
    formalizer_output = run_agent(state, "formalizer", formalizer_input)
    if formalizer_output is None:
        log_msg(state, "  formalizer: no output")
        return {}

    # Extract Lean code
    f_ev = formalizer_output.get("evidence", formalizer_output)
    lean_code = None
    if isinstance(f_ev, str):
        lean_code = f_ev
    elif isinstance(f_ev, dict):
        lean_code = f_ev.get("lean_code") or f_ev.get("code") or f_ev.get("output")
    if not lean_code:
        log_msg(state, "  formalizer: could not extract Lean code")
        evidence["formalization"] = {"status": "skipped", "message": "No Lean code produced"}
        return {"evidence": evidence}

    log_msg(state, f"  formalizer: got {len(lean_code)} chars of Lean code")
    evidence["lean_code"] = lean_code

    # Type-check with retry
    max_retries = 2
    for attempt in range(1 + max_retries):
        log_msg(state, f"  type_check: attempt {attempt + 1}/{1 + max_retries}...")
        tc_result = check_lean(lean_code)
        tc_status = tc_result.get("status", "skipped")
        log_msg(state, f"  type_check: {tc_status}")

        if tc_status == "valid":
            sorry_count = lean_code.count("sorry")
            evidence["formalization"] = {
                "status": "valid",
                "lean_verified": True,
                "sorry_count": sorry_count,
                "time_seconds": tc_result.get("time_seconds", 0),
                "warnings": tc_result.get("warnings"),
            }
            return {"evidence": evidence}

        if tc_status == "skipped":
            evidence["formalization"] = {
                "status": "skipped",
                "message": tc_result.get("message", "Docker not available"),
            }
            return {"evidence": evidence}

        if tc_status in ("invalid", "timeout") and attempt < max_retries:
            tc_errors = tc_result.get("errors", [])
            log_msg(state, f"  type_check errors: {tc_errors[:3]}")
            retry_input = {
                "consolidated_proof": consolidated,
                "best_proof": best_proof,
                "template_type": template_name,
                "template_code": template,
                "previous_attempt": lean_code,
                "lean_errors": tc_errors,
                "instruction": (
                    "Your previous Lean 4 code had errors. "
                    "Fix the errors and return the corrected COMPLETE Lean 4 file. "
                    "Output ONLY Lean 4 code, no markdown."
                ),
            }
            retry_output = run_agent(state, "formalizer", retry_input)
            if retry_output:
                r_ev2 = retry_output.get("evidence", retry_output)
                new_code = None
                if isinstance(r_ev2, str):
                    new_code = r_ev2
                elif isinstance(r_ev2, dict):
                    new_code = r_ev2.get("lean_code") or r_ev2.get("code") or r_ev2.get("output")
                if new_code:
                    lean_code = new_code
                    evidence["lean_code"] = lean_code
                    continue

        # Final failure
        sorry_count = lean_code.count("sorry")
        evidence["formalization"] = {
            "status": tc_status,
            "lean_verified": False,
            "sorry_count": sorry_count,
            "errors": tc_result.get("errors"),
            "time_seconds": tc_result.get("time_seconds", 0),
        }
        return {"evidence": evidence}

    return {"evidence": evidence}


def assemble_result_node(state: PipelineState) -> dict:
    """Step 9: assemble the final result dict."""
    log_msg(state, "Step 9/9: assembling result...")

    evidence = dict(state.get("evidence", {}))
    errors = list(state.get("errors", []))
    reasoning_output = state.get("reasoning_output")
    test_result = state.get("test_result")
    hypothesis = state.get("hypothesis", {})

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

    # Escalation → needs human review, never mark as "success"
    if reasoning_action == "escalate":
        status = "partial"
        confidence = float(reasoning_confidence or hypothesis.get("confidence", 0.0))
        evidence["needs_human_review"] = True
    elif test_result is not None:
        if test_result.get("status") == "pass":
            status = "success"
            confidence = 1.0
        else:
            status = "failure"
            confidence = 0.0
    elif reasoning_verdict is not None:
        status = "success"
        confidence = float(reasoning_confidence or 0.9)
    elif errors:
        status = "partial"
        confidence = hypothesis.get("confidence", 0.0)
    else:
        status = "partial"
        confidence = hypothesis.get("confidence", 0.0)

    log_msg(state, f"  final: status={status}, confidence={confidence}")

    return {
        "result": _make_result(
            status,
            evidence=evidence,
            errors=errors if errors else None,
            confidence=confidence,
        ),
    }


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

    # retry planner → back to dispatch (cycle)
    graph.add_edge("run_retry_planner_node", "setup_dispatch_node")

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
        "reasoning_output": None,
        "proof_checker_output": None,
        "retry_round": 0,
        "inversions_done": 0,
        "retry_context": {},
        "evidence": {},
        "errors": [],
        "_specialist_name": "",
        "result": {},
    }

    final_state = graph.invoke(initial_state)
    return final_state.get("result", _make_result("failure", errors=["Graph produced no result"]))
