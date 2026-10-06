"""
Orchestrator — TFL Agent System pipeline.

Usage (CLI):
    python -m agent_system <ir.json>                          # pure-fn only
    python -m agent_system <ir.json> --mock examples/         # mock agents
    python -m agent_system <ir.json> --live                   # real LLM agents
    python -m agent_system <ir.json> --live --render md       # + markdown output
    python -m agent_system <ir.json> --live --render html     # + html output

Programmatically:
    from agent_system.orchestrator import Pipeline, MockRunner
    result = Pipeline().run_full_pipeline(ir_dict, mock_runner=mock)

    from agent_system.lib.llm_client import LLMRunner
    result = Pipeline().run_full_pipeline(ir_dict, agent_runner=LLMRunner())
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Lib imports (§7 Phase 1 modules)
# ---------------------------------------------------------------------------

from .lib.ir_schema import validate_ir
from .lib.oracle import oracle_from_ir
from .lib.oracle_test import oracle_test
from .lib.dfa_runner import validate_dfa, run_dfa
from .lib.hypothesis_module import analyze_hypothesis
from .lib.type_check import check_lean
from .lib.progress import ProgressWriter


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
        progress: ProgressWriter | None = None,
    ) -> dict[str, Any]:
        """Run the pipeline from a pre-parsed IR dict.

        Args:
            ir:  Validated IR dictionary (see lib.ir_schema).
            dfa: Optional DFA dict to test against the oracle.
            lean_code: Optional Lean 4 code to formalize and type-check.
            progress: Optional :class:`ProgressWriter`; gets a node event for
                the whole step and the final verdict event.

        Returns:
            Structured result per §5.3 contract.
        """
        from .lib.progress import finish_pipeline

        if progress is not None:
            progress.node_start("run_from_ir")
        t0 = time.monotonic()
        result = self._run_from_ir(ir, dfa, lean_code)
        if progress is not None:
            progress.node_done("run_from_ir", {"result": result},
                               elapsed=time.monotonic() - t0)
            finish_pipeline(progress, result)
        return result

    def _run_from_ir(
        self,
        ir: dict,
        dfa: dict | None = None,
        lean_code: str | None = None,
    ) -> dict[str, Any]:
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
        formalize: bool | None = None,
        progress: ProgressWriter | None = None,
    ) -> dict[str, Any]:
        """Run the full end-to-end pipeline per §5.1.

        Delegates to the LangGraph-based pipeline in ``graph.py``.

        Agent resolution order:
        1. *mock_runner* — load from JSON files (for testing).
        2. *agent_runner* — call LLM via API (for production).
        3. Neither — skip LLM agents, pure-fn parts only.

        Both MockRunner and LLMRunner implement ``run_agent(name, data)``.

        Args:
            formalize: Override for Lean 4 formalization. ``None`` (default)
                       uses ``graph.FORMALIZATION_ENABLED`` (itself defaulted
                       from the ``TFL_FORMALIZATION`` env var); pass
                       ``True``/``False`` to force it on/off for this run
                       (the CLI's ``--formalize`` does this).
            progress: Optional :class:`ProgressWriter` (progress.jsonl +
                      partial_result.json in its run dir; see
                      ``lib/progress.py``).

        Returns:
            Structured result per §5.3 output contract.
        """
        from .graph import run_pipeline

        result = run_pipeline(
            ir,
            mock_runner=mock_runner,
            agent_runner=agent_runner,
            formalize=formalize,
            progress=progress,
        )

        # Sync instance attributes for backward compatibility
        # (some tests read pipeline.hypothesis, pipeline.test_result, etc.)
        self.ir = ir
        ev = result.get("evidence", {})
        self.hypothesis = ev.get("hypothesis")
        self.test_result = ev.get("oracle_test")

        return result

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
    parser.add_argument("--save", metavar="DIR", default=None,
                        help="Save <stem>_result.{json,md,html} to DIR "
                             "(common CLI contract used by TFL Lab)")
    parser.add_argument("--verbose", action="store_true",
                        help="Accepted for CLI parity with the other pipelines")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=None,
                        help="Write progress.jsonl + partial_result.json into the --save "
                             "directory while running (default: on iff --save is given; "
                             "--no-progress disables)")
    parser.add_argument("--formalize", action="store_true",
                        help="Force-enable Lean 4 formalization for this run "
                             "(default: TFL_FORMALIZATION env var, currently "
                             "disabled while Lean templates are in progress)")

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
    formalize = True if args.formalize else None
    progress = ProgressWriter.for_cli(
        args.save, args.progress, system="agent_system",
        stem=f"{Path(args.ir_json).stem}_result",
    )
    try:
        if args.mock is not None:
            task_prefix = Path(args.ir_json).stem
            mock_runner = MockRunner(args.mock, task_prefix=task_prefix)
            result = pipeline.run_full_pipeline(
                ir, mock_runner=mock_runner, formalize=formalize, progress=progress)

        elif args.live:
            from .lib.llm_client import LLMRunner
            try:
                llm = LLMRunner(verbose=args.verbose)
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            result = pipeline.run_full_pipeline(
                ir, agent_runner=llm, formalize=formalize, progress=progress)
            if args.verbose:
                print(llm.usage_tracker.summary_line(), file=sys.stderr)

        else:
            dfa = None
            if args.dfa_json:
                try:
                    with open(args.dfa_json, encoding="utf-8") as f:
                        dfa = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    print(f"Error reading DFA file: {exc}", file=sys.stderr)
                    sys.exit(1)
            result = pipeline.run_from_ir(ir, dfa, progress=progress)
    except Exception as exc:
        if progress is not None and progress.status != "error":
            progress.error(f"{type(exc).__name__}: {exc}", exc_type=type(exc).__name__)
        raise

    # Top-level verdict (regular / non_regular), same place as in the
    # cfl / dcfl / ll results, so callers don't dig through evidence.
    result.setdefault("verdict", _result_verdict(result))

    # JSON output
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.save:
        from .lib.renderer import render_to_file

        save_dir = Path(args.save)
        save_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{Path(args.ir_json).stem}_result"
        (save_dir / f"{stem}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        for fmt in ("md", "html"):
            try:
                render_to_file(result, str(save_dir / f"{stem}.{fmt}"), fmt=fmt)
            except Exception as exc:
                print(f"Renderer ({fmt}) failed: {exc}", file=sys.stderr)
        print(f"Result saved to {save_dir / (stem + '.json')}", file=sys.stderr)
        if progress is not None:
            progress.done(result, files=[f"{stem}.{ext}" for ext in ("json", "md", "html")
                                         if (save_dir / f"{stem}.{ext}").exists()])

    # Render
    if args.render:
        from .lib.renderer import render_markdown, render_html, render_to_file

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

        # Priority 1: formalizer-produced code (from graph.py's formalize_node)
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



def _result_verdict(result: dict) -> str | None:
    """Verdict of the reasoning agent (falls back to the hypothesis).

    docs/VERDICT_POLICY.md R3/R1 (reviewer finding): an unresolved
    contradiction leaves `status == "partial"` with `verdict_gate.
    contradiction == True` (graph.py's `assemble_result_node`), but that
    node never clears the reasoning agent's own proposed regular/
    non_regular verdict field -- only its confidence gets capped. Reading
    straight through to `reasoning.verdict` here would leak that
    unconfirmed verdict out as the pipeline's top-level result (and
    `tfl_eval.runners.extract('reg')` duplicates this same lookup, so it
    needs the identical guard).
    """
    evidence = result.get("evidence", {}) or {}
    gate = evidence.get("verdict_gate") or result.get("verdict_gate") or {}
    if result.get("status") == "partial" and gate.get("contradiction"):
        return None
    reasoning = evidence.get("reasoning", {}) or {}
    r_ev = reasoning.get("evidence", reasoning) if isinstance(reasoning, dict) else {}
    return (r_ev.get("verdict")
            or reasoning.get("verdict")
            or result.get("verdict")
            or (evidence.get("hypothesis", {}) or {}).get("hypothesis"))


# Direct script execution (python agent_system/orchestrator.py) is NOT
# supported with relative imports. Use:
#   python -m agent_system <args>
#   python -m agent_system.orchestrator <args>
if __name__ == "__main__":
    main()
