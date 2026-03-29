"""
Orchestrator — TFL Agent System pipeline.

Usage:
    python orchestrator.py <ir.json>                          # pure-fn only
    python orchestrator.py <ir.json> --mock examples/         # mock agents
    python orchestrator.py <ir.json> --live                   # real LLM agents
    python orchestrator.py <ir.json> --live --render md       # + markdown output
    python orchestrator.py <ir.json> --live --render html     # + html output

Programmatically:
    from orchestrator import Pipeline, MockRunner
    result = Pipeline().run_full_pipeline(ir_dict, mock_runner=mock)

    from lib.llm_client import LLMRunner
    result = Pipeline().run_full_pipeline(ir_dict, agent_runner=LLMRunner())
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Lib imports (§7 Phase 1 modules)
# ---------------------------------------------------------------------------

from lib.ir_schema import validate_ir
from lib.oracle import oracle_from_ir
from lib.oracle_test import oracle_test
from lib.dfa_runner import validate_dfa, run_dfa
from lib.hypothesis_module import analyze_hypothesis
from lib.type_check import check_lean


# ---------------------------------------------------------------------------
# MockRunner — load pre-computed agent outputs from JSON files
# ---------------------------------------------------------------------------

class MockRunner:
    """Load mock agent outputs from examples/ directory."""

    def __init__(self, task_dir: str | Path, task_prefix: str = ""):
        """Initialise a MockRunner.

        Args:
            task_dir: directory containing mock output files.
            task_prefix: filename prefix for the task, e.g.
                ``"task1_palindrome_prefix_suffix"``.  Mock files are
                expected to be named
                ``{task_prefix}_{agent_name}_output.json``.
        """
        self.task_dir = Path(task_dir)
        self.task_prefix = task_prefix

    def run_agent(self, agent_name: str) -> dict | None:
        """Load mock output for *agent_name*.  Returns ``None`` if the
        corresponding file does not exist."""
        filename = (
            f"{self.task_prefix}_{agent_name}_output.json"
            if self.task_prefix
            else f"{agent_name}_output.json"
        )
        path = self.task_dir / filename
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """Minimal MVP orchestrator pipeline.

    Steps:
        1. Validate IR
        2. Analyze hypothesis (heuristic classification)
        3. Build oracle from IR
        4. If DFA provided — validate and test against oracle
        5. Return structured result (§5.3 contract)
    """

    def __init__(self) -> None:
        self.ir: dict | None = None
        self.hypothesis: dict | None = None
        self.oracle_fn = None
        self.test_result: dict | None = None

    def formalize_and_check(
        self,
        lean_code: str,
        retry_fn: Callable[[list[str]], str | None] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """Type-check Lean 4 code with retry protocol (§5.2 Level 4).

        Args:
            lean_code: Lean 4 source code to check.
            retry_fn: Optional callback that receives error list and returns
                      corrected Lean code, or None to stop retrying.
            max_retries: Maximum retry attempts (default 2 per spec).

        Returns:
            Type check result dict.
        """
        result = check_lean(lean_code)
        attempts = 0

        while (
            result.get("status") == "invalid"
            and retry_fn is not None
            and attempts < max_retries
        ):
            errors = result.get("errors", [])
            corrected = retry_fn(errors)
            if corrected is None:
                break
            lean_code = corrected
            result = check_lean(lean_code)
            attempts += 1

        return result

    def get_template(self, name: str) -> str | None:
        """Read a Lean 4 proof template from templates/ directory.

        Args:
            name: Template name without extension (e.g., "prove_regular_via_dfa")

        Returns:
            Template content string, or None if not found.
        """
        template_dir = Path(__file__).parent / "templates"
        template_file = template_dir / f"{name}.lean"
        if template_file.exists():
            return template_file.read_text(encoding="utf-8")
        return None

    def run_from_ir(
        self,
        ir: dict,
        dfa: dict | None = None,
        lean_code: str | None = None,
    ) -> dict[str, Any]:
        """Run the pipeline from a pre-parsed IR dict.

        Args:
            ir:  Validated IR dictionary (see lib.ir_schema).
            dfa: Optional DFA dict to test against the oracle.
            lean_code: Optional Lean 4 code to formalize and type-check.

        Returns:
            Structured result per §5.3 contract.
        """
        errors: list[str] = []

        # --- Step 1: Validate IR ---
        ir_errors = validate_ir(ir)
        if ir_errors:
            return _result("failure", errors=ir_errors, confidence=0.0)

        self.ir = ir

        # --- Step 2: Hypothesis analysis ---
        self.hypothesis = analyze_hypothesis(ir)

        # --- Step 3: Build oracle ---
        spec = ir.get("language_spec")
        if spec is None:
            return _result(
                "partial",
                evidence={"hypothesis": self.hypothesis},
                errors=["No language_spec — cannot build oracle"],
                confidence=self.hypothesis.get("confidence", 0.0),
            )

        try:
            self.oracle_fn = oracle_from_ir(ir)
        except ValueError as exc:
            return _result(
                "partial",
                evidence={"hypothesis": self.hypothesis},
                errors=[f"Oracle build failed: {exc}"],
                confidence=self.hypothesis.get("confidence", 0.0),
            )

        # --- Step 4: Test DFA if provided ---
        if dfa is not None:
            dfa_errors = validate_dfa(dfa)
            if dfa_errors:
                return _result(
                    "failure",
                    evidence={"hypothesis": self.hypothesis},
                    errors=[f"DFA validation: {e}" for e in dfa_errors],
                    confidence=0.0,
                )

            alphabet = _get_alphabet(ir)
            self.test_result = oracle_test(
                self.oracle_fn,
                dfa,
                alphabet,
                strategies=["exhaustive_k"],
                max_exhaustive=7,
            )

            status = "success" if self.test_result["status"] == "pass" else "failure"
            confidence = 1.0 if status == "success" else 0.0

            evidence: dict[str, Any] = {
                "hypothesis": self.hypothesis,
                "oracle_test": self.test_result,
            }

            # --- Step 5: Formalize if lean_code provided ---
            if lean_code is not None:
                evidence["formalization"] = self.formalize_and_check(lean_code)

            return _result(
                status,
                evidence=evidence,
                confidence=confidence,
            )

        # No DFA — build evidence
        evidence_no_dfa: dict[str, Any] = {"hypothesis": self.hypothesis}

        # --- Formalize if lean_code provided (no-DFA path) ---
        if lean_code is not None:
            evidence_no_dfa["formalization"] = self.formalize_and_check(lean_code)

        return _result(
            "partial",
            evidence=evidence_no_dfa,
            confidence=self.hypothesis.get("confidence", 0.0),
        )

    # ------------------------------------------------------------------
    # Full end-to-end pipeline (§5.1)
    # ------------------------------------------------------------------

    def run_full_pipeline(
        self,
        ir: dict,
        mock_runner: MockRunner | None = None,
        agent_runner: Any | None = None,
    ) -> dict[str, Any]:
        """Run the full end-to-end pipeline per §5.1.

        Agent resolution order:
        1. *mock_runner* — load from JSON files (for testing).
        2. *agent_runner* — call LLM via API (for production).
        3. Neither — skip LLM agents, pure-fn parts only.

        Both MockRunner and LLMRunner implement ``run_agent(name, data)``.

        Returns:
            Structured result per §5.3 output contract.
        """
        import time as _time

        errors: list[str] = []
        evidence: dict[str, Any] = {}
        verbose = agent_runner is not None  # log progress in live mode

        def _log(msg: str) -> None:
            if verbose:
                print(f"  [{_time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

        def _run(agent_name: str, input_data: Any = None) -> dict | None:
            """Try mock first, then live runner."""
            if mock_runner is not None:
                out = mock_runner.run_agent(agent_name)
                if out is not None:
                    _log(f"{agent_name}: loaded from mock")
                    return out
            if agent_runner is not None:
                _log(f"{agent_name}: calling LLM...")
                t0 = _time.monotonic()
                out = agent_runner.run_agent(agent_name, input_data)
                elapsed = _time.monotonic() - t0
                if out is not None:
                    _log(f"{agent_name}: done ({elapsed:.1f}s)")
                else:
                    _log(f"{agent_name}: FAILED ({elapsed:.1f}s)")
                return out
            return None

        # --- Step 1: Validate IR ---
        _log("Step 1/9: validating IR...")
        ir_errors = validate_ir(ir)
        if ir_errors:
            return _result("failure", errors=ir_errors, confidence=0.0)

        self.ir = ir

        # --- Step 2: Hypothesis analysis ---
        _log("Step 2/9: hypothesis analysis...")
        self.hypothesis = analyze_hypothesis(ir)
        evidence["hypothesis"] = self.hypothesis
        _log(f"  hypothesis={self.hypothesis.get('hypothesis')} "
             f"confidence={self.hypothesis.get('confidence')}")

        # --- Step 3: Classifier ---
        _log("Step 3/9: classifier...")
        classifier_input = {
            "ir": ir,
            "hypothesis": self.hypothesis,
        }
        if ir.get("student_notes"):
            classifier_input["student_notes"] = ir["student_notes"]
        classifier_output = _run("classifier", classifier_input)
        if classifier_output is not None:
            evidence["classifier"] = classifier_output
            verdict = (classifier_output.get("evidence", {})
                       .get("verdict", "?"))
            _log(f"  classifier verdict: {verdict}")
        classifier_evidence = (classifier_output or {}).get("evidence", {})

        # Always run ALL specialists — correctness over speed
        dispatch = {
            "re_builder": True,
            "dfa_builder": True,
            "pumping": True,
            "nerode": True,
            "closure": True,
        }
        # Add grammar_analyzer for grammar-based tasks
        lang_kind = ir.get("language_spec", {}).get("kind")
        if lang_kind == "grammar":
            dispatch["grammar_analyzer"] = True

        # --- Step 3b: Grammar preprocessor (pure-fn, zero cost) ---
        grammar_facts: dict | None = None
        if lang_kind == "grammar":
            _log("Step 3b: grammar preprocessor (pure-fn)...")
            try:
                from lib.grammar_preprocessor import analyze_grammar
                grammar_facts = analyze_grammar(
                    ir["language_spec"],
                    oracle=self.oracle_fn if self.oracle_fn else None,
                    max_word_len=10,
                )
                evidence["grammar_facts"] = grammar_facts
                _log(f"  generated {grammar_facts.get('total_generated', 0)} words")
                _log(f"  linear={grammar_facts.get('is_linear')}, "
                     f"nested_recursion={grammar_facts.get('has_nested_recursion')}")
                summary = grammar_facts.get("summary", "")
                if summary:
                    for line in summary.split("\n"):
                        _log(f"  {line}")
            except Exception as exc:
                _log(f"  grammar preprocessor failed: {exc}")

        # --- Step 4: Run specialist agents ---
        # ============================================================
        # Steps 4–7: Specialist → Oracle → Reasoning  (with retry loop)
        # Retry protocol §5.2: max 2 enriched retries + 1 inversion
        # ============================================================
        MAX_SPECIALIST_RETRIES = 2
        MAX_INVERSIONS = 1
        retry_round = 0
        inversions_done = 0
        retry_context: dict | None = None  # enriched context from reasoning
        reasoning_output: dict | None = None
        dfa_builder_output: dict | None = None

        while retry_round <= MAX_SPECIALIST_RETRIES:
            round_label = (f" (retry {retry_round})" if retry_round > 0
                           else "")

            # --- Step 4: Run specialists ---
            dispatched = [k for k, v in dispatch.items() if v]
            _log(f"Step 4/9: specialists{round_label}: {dispatched}")

            # Extract student notes once (empty string if absent)
            _student_notes = ir.get("student_notes", "")

            def _build_specialist_input(agent_name: str) -> dict:
                inp = {
                    "ir": ir,
                    "hypothesis": self.hypothesis,
                    "classifier": classifier_evidence,
                }
                if grammar_facts:
                    inp["grammar_facts"] = grammar_facts
                if _student_notes:
                    inp["student_notes"] = _student_notes
                if retry_context:
                    ctx = dict(retry_context)
                    fb = ctx.get("feedback", {})
                    if agent_name in fb:
                        ctx["agent_feedback"] = fb[agent_name]
                    inp["retry_context"] = ctx
                return inp

            # Run all dispatched specialists IN PARALLEL
            if agent_runner is not None and len(dispatched) > 1:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                _log(f"  launching {len(dispatched)} agents in parallel...")

                def _run_one(name: str) -> tuple[str, dict | None]:
                    return name, _run(name, _build_specialist_input(name))

                with ThreadPoolExecutor(max_workers=len(dispatched)) as pool:
                    futures = {pool.submit(_run_one, name): name
                               for name in dispatched}
                    for future in as_completed(futures):
                        name, out = future.result()
                        if out is not None:
                            evidence[name] = out
                            if name == "dfa_builder":
                                dfa_builder_output = out
            else:
                # Sequential fallback (mock mode or single agent)
                for agent_name in dispatched:
                    agent_out = _run(agent_name, _build_specialist_input(agent_name))
                    if agent_out is not None:
                        evidence[agent_name] = agent_out
                        if agent_name == "dfa_builder":
                            dfa_builder_output = agent_out

            # --- Step 5: Build oracle ---
            if retry_round == 0:
                _log("Step 5/9: building oracle...")
                spec = ir.get("language_spec")
                oracle_ok = False
                if spec is not None:
                    try:
                        self.oracle_fn = oracle_from_ir(ir)
                        oracle_ok = True
                    except ValueError as exc:
                        errors.append(f"Oracle build failed: {exc}")
                else:
                    errors.append("No language_spec — cannot build oracle")
            else:
                oracle_ok = self.oracle_fn is not None

            # --- Step 5b: Verify closure agent's intersection claim ---
            if oracle_ok and "closure" in evidence:
                closure_check = _verify_closure_claim(
                    evidence["closure"], self.oracle_fn, _get_alphabet(ir), _log)
                if closure_check:
                    evidence["closure_verification"] = closure_check

            # --- Step 5c: Verify ALL word-membership claims via oracle ---
            if oracle_ok:
                from lib.claim_verifier import verify_claims
                claims_result = verify_claims(
                    evidence, self.oracle_fn, _get_alphabet(ir))
                if claims_result["disproved"] > 0:
                    evidence["claim_verification"] = claims_result
                    _log(f"  CLAIM ERRORS: {claims_result['disproved']} false claims found!")
                    for err in claims_result["errors"][:5]:
                        _log(f"    {err}")
                elif claims_result["total_claims"] > 0:
                    evidence["claim_verification"] = claims_result
                    _log(f"  claims verified: {claims_result['verified']}/{claims_result['total_claims']}")

            # --- Step 6: Oracle test ---
            _log(f"Step 6/9: oracle test{round_label}...")
            self.test_result = None
            dfa: dict | None = None
            if dfa_builder_output is not None:
                dfa = _extract_dfa(dfa_builder_output)

            # Fallback: build DFA from regex if no DFA but RE available
            if dfa is None and "re_builder" in evidence and oracle_ok:
                re_ev = evidence["re_builder"].get("evidence",
                            evidence["re_builder"])
                regex = re_ev.get("regex")
                if regex:
                    _log(f"  building DFA from regex: {regex[:40]}...")
                    try:
                        from lib.dfa_builder import build_dfa_from_regex
                        dfa = build_dfa_from_regex(regex)
                    except Exception as exc:
                        _log(f"  DFA from regex failed: {exc}")

            if dfa is not None and oracle_ok:
                dfa_errors = validate_dfa(dfa)
                if dfa_errors:
                    errors.extend(f"DFA validation: {e}" for e in dfa_errors)
                else:
                    alphabet = _get_alphabet(ir)
                    self.test_result = oracle_test(
                        self.oracle_fn,
                        dfa,
                        alphabet,
                        strategies=["exhaustive_k"],
                        max_exhaustive=7,
                    )
                    evidence["oracle_test"] = self.test_result

            if self.test_result:
                _log(f"  oracle_test: {self.test_result.get('status')} "
                     f"({self.test_result.get('tested', 0)} words)")

            # --- Level 1 retry: Oracle counterexample → re-run DFA/RE builder ---
            if (self.test_result
                    and self.test_result.get("status") == "fail"
                    and self.test_result.get("counterexample")
                    and retry_round == 0
                    and (agent_runner is not None or mock_runner is not None)):
                ce = self.test_result["counterexample"]
                _log(f"  oracle counterexample: '{ce.get('word')}' "
                     f"(oracle={ce.get('oracle_says')}, dfa={ce.get('automaton_says')})")
                _log(f"  → Level 1 retry: re-running DFA/RE builders with counterexample")

                enriched_input = {
                    **_build_specialist_input("re_builder"),
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
                    re_out = _run("re_builder", enriched_input)
                    if re_out is not None:
                        evidence["re_builder"] = re_out
                if dispatch.get("dfa_builder"):
                    dfa_builder_output = _run("dfa_builder", enriched_input)
                    if dfa_builder_output is not None:
                        evidence["dfa_builder"] = dfa_builder_output
                        # Re-run oracle test
                        dfa2 = _extract_dfa(dfa_builder_output)
                        if dfa2 is not None and oracle_ok:
                            dfa_errs2 = validate_dfa(dfa2)
                            if not dfa_errs2:
                                self.test_result = oracle_test(
                                    self.oracle_fn, dfa2, _get_alphabet(ir),
                                    strategies=["exhaustive_k"],
                                    max_exhaustive=7,
                                )
                                evidence["oracle_test"] = self.test_result
                                _log(f"  oracle re-test: {self.test_result.get('status')} "
                                     f"({self.test_result.get('tested', 0)} words)")

            # --- Step 6b: Proof checker (Opus, adversarial) ---
            if agent_runner is not None or mock_runner is not None:
                _log(f"Step 6b/9: proof checker{round_label}...")
                checker_input = {
                    "ir": ir,
                    "specialist_outputs": {
                        k: evidence[k]
                        for k in ("re_builder", "dfa_builder", "pumping",
                                   "nerode", "closure", "grammar_analyzer")
                        if k in evidence
                    },
                    "oracle_test": evidence.get("oracle_test"),
                    "closure_verification": evidence.get("closure_verification"),
                }
                if _student_notes:
                    checker_input["student_notes"] = _student_notes
                checker_output = _run("proof_checker", checker_input)
                if checker_output is not None:
                    evidence["proof_checker"] = checker_output

            # --- Step 7: Reasoning agent ---
            _log(f"Step 7/9: reasoning agent{round_label}...")
            reasoning_input = {
                "ir": ir,
                "hypothesis": self.hypothesis,
                "specialist_outputs": {
                    k: evidence[k]
                    for k in ("re_builder", "dfa_builder", "pumping",
                               "nerode", "closure", "grammar_analyzer")
                    if k in evidence
                },
                "oracle_test": evidence.get("oracle_test"),
                "closure_verification": evidence.get("closure_verification"),
                "proof_checker": evidence.get("proof_checker"),
            }
            if retry_round > 0:
                reasoning_input["retry_round"] = retry_round
                reasoning_input["previous_issues"] = retry_context

            reasoning_output = _run("reasoning", reasoning_input)
            if reasoning_output is not None:
                evidence["reasoning"] = reasoning_output

            # --- Check if reasoning says retry ---
            r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
            action = r_ev.get("action", (reasoning_output or {}).get("action", ""))
            issues = r_ev.get("issues_found", (reasoning_output or {}).get("issues_found", []))

            if action == "retry_enriched" and retry_round < MAX_SPECIALIST_RETRIES:
                # Ask Retry Planner (Sonnet) which agents to re-run
                planner_input = {
                    "issues_found": issues,
                    "oracle_counterexample": (
                        self.test_result.get("counterexample")
                        if self.test_result else None
                    ),
                    "specialist_results": {
                        k: {
                            "status": evidence[k].get("status", "?"),
                            "verdict": evidence[k].get("evidence", evidence[k])
                                       .get("verdict", "?"),
                        }
                        for k in dispatched if k in evidence
                    },
                    "current_hypothesis": self.hypothesis.get("hypothesis"),
                }
                planner_output = _run("retry_planner", planner_input)
                p_ev = (planner_output or {}).get("evidence", planner_output or {})

                agents_to_retry = p_ev.get("agents_to_retry", dispatched)
                feedback_map = p_ev.get("feedback", {})
                skip_agents = set(p_ev.get("skip_agents", []))

                _log(f"  retry_planner: retry {agents_to_retry}, skip {list(skip_agents)}")

                # Update dispatch to only retry selected agents
                dispatch = {k: (k in agents_to_retry) for k in dispatched}

                retry_context = {
                    "issues": issues,
                    "oracle_counterexample": planner_input["oracle_counterexample"],
                    "feedback": feedback_map,
                    "instruction": (
                        "Previous attempt had issues. See 'feedback' for "
                        "agent-specific corrections."
                    ),
                }
                retry_round += 1
                continue

            if action == "invert_hypothesis" and inversions_done < MAX_INVERSIONS:
                old_hyp = self.hypothesis.get("hypothesis", "unknown")
                new_hyp = "regular" if old_hyp == "non_regular" else "non_regular"
                _log(f"  reasoning: inverting hypothesis {old_hyp} → {new_hyp}")
                inversions_done += 1
                self.hypothesis = {**self.hypothesis, "hypothesis": new_hyp}
                evidence["hypothesis"] = self.hypothesis
                # dispatch stays the same — always all agents
                retry_context = {
                    "issues": issues,
                    "instruction": (
                        f"Hypothesis inverted from {old_hyp} to {new_hyp}. "
                        f"Previous agents failed. Try the opposite approach."
                    ),
                }
                retry_round += 1
                continue

            # No retry needed — break
            break

        # --- Step 8: Formalize ---
        _log("Step 8/9: formalization...")
        r_evidence = (reasoning_output or {}).get("evidence", reasoning_output or {})
        action = (r_evidence.get("action")
                  or (reasoning_output or {}).get("action", ""))
        best_proof = (r_evidence.get("best_proof")
                      or (reasoning_output or {}).get("best_proof", ""))
        consolidated = (r_evidence.get("consolidated_proof")
                        or (reasoning_output or {}).get("consolidated_proof", ""))

        # Formalization disabled for now (Lean errors under investigation)
        # To re-enable: remove the `if False` guard below
        lean_code: str | None = None
        if False:  # DISABLED — re-enable when Lean templates are stable
            lean_code = r_evidence.get("lean_code")
            if (not lean_code
                    and action == "proceed_to_formalizer"
                    and (agent_runner is not None or mock_runner is not None)):
                lean_code = self._run_formalizer(
                    ir, evidence, best_proof, consolidated,
                    _run, _log, errors,
                )
            if lean_code is not None:
                evidence["lean_code"] = lean_code
        _log("  formalization: disabled")

        # --- Step 9: Assemble final result ---
        _log("Step 9/9: assembling result...")

        # Use reasoning agent verdict/confidence if available (overrides hypothesis)
        r_ev = (reasoning_output or {}).get("evidence", reasoning_output or {})
        reasoning_verdict = r_ev.get("verdict", (reasoning_output or {}).get("verdict"))
        reasoning_confidence = r_ev.get("confidence", (reasoning_output or {}).get("confidence"))

        if self.test_result is not None:
            if self.test_result.get("status") == "pass":
                status = "success"
                confidence = 1.0
            else:
                status = "failure"
                confidence = 0.0
        elif reasoning_verdict is not None:
            # Reasoning agent gave a verdict → success
            status = "success"
            confidence = float(reasoning_confidence or 0.9)
        elif errors:
            status = "partial"
            confidence = self.hypothesis.get("confidence", 0.0)
        else:
            status = "partial"
            confidence = self.hypothesis.get("confidence", 0.0)

        _log(f"  final: status={status}, confidence={confidence}")

        return _result(
            status,
            evidence=evidence,
            errors=errors if errors else None,
            confidence=confidence,
        )

    def _run_formalizer(
        self,
        ir: dict,
        evidence: dict,
        best_proof: str,
        consolidated: str,
        _run: Callable,
        _log: Callable,
        errors: list[str],
    ) -> str | None:
        """Run formalizer agent with template + type check + retry."""
        # Pick template
        reasoning = evidence.get("reasoning", {})
        r_ev = reasoning.get("evidence", reasoning)
        verdict = (r_ev.get("verdict")
                   or reasoning.get("verdict")
                   or self.hypothesis.get("hypothesis", "unknown"))

        if verdict == "regular":
            template_name = "prove_regular_via_dfa"
        elif best_proof == "nerode":
            template_name = "prove_non_regular_via_nerode"
        else:
            template_name = "prove_non_regular_via_pumping"

        template = self.get_template(template_name)
        if not template:
            _log(f"  template '{template_name}' not found, skipping")
            return None

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

        _log(f"  formalizer: template={template_name}")
        formalizer_output = _run("formalizer", formalizer_input)

        if formalizer_output is None:
            _log("  formalizer: no output")
            return None

        # Formalizer returns plain Lean code (not JSON-wrapped)
        f_ev = formalizer_output.get("evidence", formalizer_output)
        lean_code = None

        # The output might be the raw Lean code as a string in evidence
        if isinstance(f_ev, str):
            lean_code = f_ev
        elif isinstance(f_ev, dict):
            lean_code = (f_ev.get("lean_code")
                         or f_ev.get("code")
                         or f_ev.get("output"))

        # Fallback: the formalizer prompt says "output only Lean 4 code"
        # so the raw response text might be in the module's output
        if not lean_code:
            _log("  formalizer: could not extract Lean code from output")
            evidence["formalization"] = {
                "status": "skipped",
                "message": "Formalizer did not produce Lean code",
            }
            return None

        _log(f"  formalizer: got {len(lean_code)} chars of Lean code")

        # Type check with retry
        max_retries = 2
        for attempt in range(1 + max_retries):
            _log(f"  type_check: attempt {attempt + 1}/{1 + max_retries}...")
            tc_result = check_lean(lean_code)
            tc_status = tc_result.get("status", "skipped")
            _log(f"  type_check: {tc_status} ({tc_result.get('time_seconds', 0)}s)")

            if tc_status == "valid":
                evidence["formalization"] = {
                    "status": "valid",
                    "lean_verified": True,
                    "sorry_count": _count_sorry(lean_code),
                    "time_seconds": tc_result.get("time_seconds", 0),
                    "warnings": tc_result.get("warnings"),
                }
                return lean_code

            if tc_status == "skipped":
                evidence["formalization"] = {
                    "status": "skipped",
                    "message": tc_result.get("message", "Docker not available"),
                }
                return lean_code  # save anyway

            if tc_status in ("invalid", "timeout") and attempt < max_retries:
                # Retry: send errors back to formalizer
                tc_errors = tc_result.get("errors", [])
                _log(f"  type_check errors: {tc_errors[:3]}")
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
                retry_output = _run("formalizer", retry_input)
                if retry_output:
                    r_ev2 = retry_output.get("evidence", retry_output)
                    new_code = None
                    if isinstance(r_ev2, str):
                        new_code = r_ev2
                    elif isinstance(r_ev2, dict):
                        new_code = (r_ev2.get("lean_code")
                                    or r_ev2.get("code")
                                    or r_ev2.get("output"))
                    if new_code:
                        lean_code = new_code
                        continue

            # Final failure
            evidence["formalization"] = {
                "status": tc_status,
                "lean_verified": False,
                "sorry_count": _count_sorry(lean_code),
                "errors": tc_result.get("errors"),
                "time_seconds": tc_result.get("time_seconds", 0),
            }
            return lean_code

        return lean_code

    def get_prompt(self, name: str = "input_parser") -> str | None:
        """Read a prompt file from the prompts/ directory.

        Returns the prompt text, or None if the file is not found.
        """
        prompt_dir = Path(__file__).parent / "prompts"
        prompt_file = prompt_dir / f"{name}.md"
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_lean_stub(result: dict, pipeline: Pipeline) -> str | None:
    """Generate a Lean 4 proof stub from the appropriate template.

    Picks template based on verdict:
    - regular → prove_regular_via_dfa
    - non_regular + pumping → prove_non_regular_via_pumping
    - non_regular + nerode → prove_non_regular_via_nerode
    Adds a comment header with task info and consolidated proof.
    """
    evidence = result.get("evidence", {})
    reasoning = evidence.get("reasoning", {})
    r_ev = reasoning.get("evidence", reasoning) if reasoning else {}
    verdict = (r_ev.get("verdict")
               or reasoning.get("verdict")
               or evidence.get("hypothesis", {}).get("hypothesis"))
    best_proof = r_ev.get("best_proof", reasoning.get("best_proof", ""))

    # Choose template
    if verdict == "regular":
        template_name = "prove_regular_via_dfa"
    elif best_proof == "nerode":
        template_name = "prove_non_regular_via_nerode"
    elif verdict == "non_regular":
        template_name = "prove_non_regular_via_pumping"
    else:
        return None

    template = pipeline.get_template(template_name)
    if not template:
        return None

    # Build comment header
    source = ""
    for key in ("classifier", "pumping", "nerode", "closure"):
        agent = evidence.get(key, {})
        agent_ev = agent.get("evidence", agent)
        if isinstance(agent_ev, dict):
            ir_inner = agent_ev.get("ir", {})
            if isinstance(ir_inner, dict) and ir_inner.get("source_text"):
                source = ir_inner["source_text"]
                break

    consolidated = (r_ev.get("consolidated_proof")
                    or reasoning.get("consolidated_proof", ""))

    header_lines = [
        f"-- Task: {source}" if source else "-- Task: (see IR JSON)",
        f"-- Verdict: {verdict}",
        f"-- Template: {template_name}",
        f"-- Best proof method: {best_proof}" if best_proof else "",
        "--",
        "-- Generated by TFL Agent System",
        "-- Fill in the sorry placeholders to complete the proof.",
        "",
    ]
    if consolidated:
        header_lines.insert(4, "--")
        for line in consolidated.split("\n")[:20]:
            header_lines.insert(5, f"-- {line}")

    header = "\n".join(line for line in header_lines if line is not None)
    return header + "\n" + template


def _verify_closure_claim(
    closure_output: dict,
    oracle: Any,
    alphabet: list[str],
    _log: Any,
) -> dict | None:
    """Verify closure agent's intersection claim via oracle.

    If the closure agent claims L ∩ R is non-regular, we compute L ∩ R
    empirically and check whether its Nerode index is actually infinite.
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

    _log(f"  verifying closure claim: L ∩ {regex}...")

    try:
        from lib.dfa_builder import build_dfa_from_regex
        from lib.dfa_runner import run_dfa
        from lib.congruence import estimate_index

        r_dfa = build_dfa_from_regex(regex)

        # Build oracle for L ∩ R
        def intersection_oracle(word: str) -> bool:
            return oracle(word) and run_dfa(r_dfa, word)

        # Estimate Nerode index of L ∩ R
        est = estimate_index(intersection_oracle, alphabet, max_depth=7)
        idx = est.get("estimated_index")
        conf = est.get("confidence", 0)

        if idx != "infinite" and conf >= 0.8:
            _log(f"  CLOSURE CLAIM WRONG: L ∩ {regex} has finite index "
                 f"{idx} (confidence {conf}) — intersection is regular!")

            # Find concrete counterexample to "L ∩ R = {aⁿbⁿ}"
            # by listing words in L ∩ R that aren't of form aⁿbⁿ
            from lib.word_generator import generate_exhaustive
            counterexamples = []
            for w in generate_exhaustive(alphabet, max_len=8):
                if intersection_oracle(w):
                    # Check if this word disproves {aⁿbⁿ} claim
                    a_count = sum(1 for c in w if c == 'a')
                    b_count = sum(1 for c in w if c == 'b')
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
        elif idx == "infinite":
            _log(f"  closure claim verified: L ∩ {regex} is non-regular "
                 f"(index=infinite, confidence {conf})")
            return {"status": "verified", "claim": f"L ∩ {regex} is non-regular"}
        else:
            _log(f"  closure claim inconclusive (index={idx}, confidence {conf})")
            return None

    except Exception as exc:
        _log(f"  closure verification failed: {exc}")
        return None


def _count_sorry(lean_code: str) -> int:
    """Count the number of 'sorry' occurrences in Lean code."""
    return lean_code.count("sorry")


def _extract_dfa(agent_output: dict) -> dict | None:
    """Extract a DFA dict from a dfa_builder agent output."""
    evidence = agent_output.get("evidence", {})
    return evidence.get("dfa")


def _result(
    status: str,
    evidence: dict | None = None,
    errors: list[str] | None = None,
    confidence: float = 0.0,
) -> dict[str, Any]:
    """Build a §5.3 output contract result."""
    return {
        "module": "orchestrator",
        "status": status,
        "evidence": evidence or {},
        "confidence": confidence,
        "errors": errors or [],
    }


def _get_alphabet(ir: dict) -> list[str]:
    """Extract alphabet from IR, falling back to ['a', 'b']."""
    spec = ir.get("language_spec", {})
    if "alphabet" in spec:
        return spec["alphabet"]
    if "terminals" in spec:
        return spec["terminals"]
    return ["a", "b"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TFL Agent System Orchestrator",
    )
    parser.add_argument("ir_json", help="Path to IR JSON file")
    parser.add_argument("dfa_json", nargs="?", default=None,
                        help="Path to DFA JSON (only without --mock/--live)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", metavar="DIR", default=None,
                      help="Directory with mock agent output JSON files")
    mode.add_argument("--live", action="store_true",
                      help="Use real LLM agents via Anthropic API")

    parser.add_argument("--render", metavar="FMT", nargs="?",
                        const="md", default=None, choices=["md", "html"],
                        help="Render output as markdown (default) or html")
    parser.add_argument("--render-out", metavar="FILE", default=None,
                        help="Write rendered output to file instead of stdout")
    parser.add_argument("--notes", metavar="TEXT", default=None,
                        help="Student notes/ideas to inject into agent prompts")

    args = parser.parse_args()

    # Load IR
    try:
        with open(args.ir_json, encoding="utf-8") as f:
            ir = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading IR file: {exc}", file=sys.stderr)
        sys.exit(1)

    # Inject student notes from CLI if provided
    if args.notes:
        ir["student_notes"] = args.notes

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pipeline = Pipeline()

    if args.mock is not None:
        task_prefix = Path(args.ir_json).stem
        mock_runner = MockRunner(args.mock, task_prefix=task_prefix)
        result = pipeline.run_full_pipeline(ir, mock_runner=mock_runner)

    elif args.live:
        from lib.llm_client import LLMRunner
        try:
            llm = LLMRunner()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        result = pipeline.run_full_pipeline(ir, agent_runner=llm)

    else:
        dfa = None
        if args.dfa_json:
            try:
                with open(args.dfa_json, encoding="utf-8") as f:
                    dfa = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"Error reading DFA file: {exc}", file=sys.stderr)
                sys.exit(1)
        result = pipeline.run_from_ir(ir, dfa)

    # JSON output
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Render
    if args.render:
        from lib.renderer import render_markdown, render_html, render_to_file

        # Auto-generate output filenames from IR name
        ir_stem = Path(args.ir_json).stem
        out_dir = Path(args.ir_json).parent / "output"
        out_dir.mkdir(exist_ok=True)

        # Always save both md and html
        render_to_file(result, str(out_dir / f"{ir_stem}.md"), fmt="md")
        render_to_file(result, str(out_dir / f"{ir_stem}.html"), fmt="html")

        # Also save raw JSON
        json_out = out_dir / f"{ir_stem}.json"
        json_out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Save Lean 4 proof code if available
        lean_saved = False
        lean_path = out_dir / f"{ir_stem}.lean"
        ev = result.get("evidence", {})

        # Priority 1: formalizer-produced code (from _run_formalizer)
        if ev.get("lean_code"):
            lean_path.write_text(ev["lean_code"], encoding="utf-8")
            lean_saved = True
        # Priority 2: lean_code inside formalization evidence
        elif ev.get("formalization", {}).get("lean_code"):
            lean_path.write_text(ev["formalization"]["lean_code"], encoding="utf-8")
            lean_saved = True
        # Priority 3: generate stub from template
        if not lean_saved:
            lean_stub = _generate_lean_stub(result, pipeline)
            if lean_stub:
                lean_path.write_text(lean_stub, encoding="utf-8")
                lean_saved = True

        fm = ev.get("formalization", {})
        fm_label = ""
        if fm.get("status") == "valid":
            sorry_n = fm.get("sorry_count", 0)
            fm_label = " (verified ✓)" if sorry_n == 0 else f" ({sorry_n} sorry)"
        elif fm.get("status") == "invalid":
            fm_label = " (type errors)"
        elif fm.get("status") == "skipped":
            fm_label = " (stub, Docker unavailable)"
        elif not fm:
            fm_label = " (stub)"

        print(f"\nСохранено:", file=sys.stderr)
        print(f"  {out_dir / f'{ir_stem}.json'}", file=sys.stderr)
        print(f"  {out_dir / f'{ir_stem}.md'}", file=sys.stderr)
        print(f"  {out_dir / f'{ir_stem}.html'}", file=sys.stderr)
        if lean_saved:
            print(f"  {lean_path}{fm_label}", file=sys.stderr)

        # Also print requested format to stdout
        if args.render == "html":
            rendered = render_html(result)
        else:
            rendered = render_markdown(result)

        if args.render_out:
            render_to_file(result, args.render_out, fmt=args.render)
        else:
            print("\n" + "=" * 60)
            print(rendered)

    sys.exit(0 if result["status"] != "failure" else 1)


if __name__ == "__main__":
    main()
