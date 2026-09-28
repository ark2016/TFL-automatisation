"""Tests for graph edge cases: closure verification, escalate action,
formalization node, and decide_retry routing."""

import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_system.graph import (
    _estimate_index_with_timeout,
    _verify_closure_claim,
    assemble_result_node,
    decide_retry,
    formalize_node,
    setup_dispatch_node,
    FORMALIZATION_ENABLED,
)


def _base_state(**overrides):
    """Build a minimal PipelineState dict for unit-testing nodes."""
    state = {
        "ir": {"task_type": "classify_and_prove", "source_text": "test",
               "language_spec": {"kind": "predicate", "alphabet": ["a", "b"],
                                 "variable": "w",
                                 "predicate": {"op": "eq",
                                               "left": {"kind": "length", "of_var": "w"},
                                               "right": {"kind": "constant", "value": 0}}}},
        "mock_runner": None,
        "agent_runner": None,
        "verbose": False,
        "hypothesis": {"hypothesis": "non_regular", "confidence": 0.7},
        "classifier_output": {},
        "classifier_evidence": {},
        "grammar_facts": {},
        "lang_kind": "predicate",
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
    state.update(overrides)
    return state


class TestClosureVerification(unittest.TestCase):

    def test_timeout_returns_unknown_without_blocking(self):
        def slow_estimate(*_args, **_kwargs):
            time.sleep(0.2)
            return {"estimated_index": "infinite", "confidence": 1.0}

        t0 = time.perf_counter()
        with patch("agent_system.lib.congruence.estimate_index", slow_estimate):
            result = _estimate_index_with_timeout(
                lambda _w: True,
                ["a", "b"],
                max_depth=4,
                timeout=0.01,
            )
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 0.1)
        self.assertEqual(result["estimated_index"], "unknown")
        self.assertEqual(result["confidence"], 0)

    def test_worker_error_is_not_reported_as_timeout(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("estimate exploded")

        with patch("agent_system.lib.congruence.estimate_index", boom):
            with self.assertRaisesRegex(RuntimeError, "estimate exploded"):
                _estimate_index_with_timeout(
                    lambda _w: True,
                    ["a", "b"],
                    max_depth=4,
                    timeout=0.05,
                )

    def test_low_confidence_infinite_is_plausible_and_memoized(self):
        closure_output = {
            "status": "success",
            "evidence": {
                "details": {
                    "regular_language": {"regex": "a*"},
                },
            },
        }
        state = _base_state()
        oracle_calls: list[str] = []

        def oracle(word: str) -> bool:
            oracle_calls.append(word)
            return True

        def fake_estimate(intersection_oracle, alphabet, max_depth, timeout, state):
            self.assertEqual(alphabet, ["a", "b"])
            self.assertEqual(max_depth, 7)
            self.assertEqual(timeout, 120)
            self.assertTrue(intersection_oracle("ab"))
            self.assertTrue(intersection_oracle("ab"))
            self.assertTrue(intersection_oracle("a"))
            return {"estimated_index": "infinite", "confidence": 0.75}

        with patch("agent_system.graph._estimate_index_with_timeout", fake_estimate):
            with patch("agent_system.graph.run_dfa", return_value=True):
                with patch(
                    "agent_system.lib.dfa_builder.build_dfa_from_regex",
                    return_value={},
                ):
                    result = _verify_closure_claim(
                        closure_output,
                        oracle,
                        ["a", "b"],
                        state,
                    )

        self.assertEqual(result["status"], "plausible")
        self.assertEqual(result["confidence"], 0.75)
        self.assertEqual(oracle_calls, ["ab", "a"])

    def test_process_exits_promptly_after_timeout(self):
        """Regression test for the `ThreadPoolExecutor`-backed version of
        `_estimate_index_with_timeout`: its non-daemon pool threads are
        joined by Python at interpreter exit, so a computation that outlives
        `timeout` kept the WHOLE PROCESS alive until it finished (TODO.md
        §2) -- a daemon thread must not. Run in a subprocess since that is
        the only way to observe "does the process exit", with a background
        computation (30s) far longer than the outer `subprocess.run` timeout
        (10s) -- this fails with a `TimeoutExpired` under the old pool-based
        implementation."""
        script = textwrap.dedent("""
            import time
            from unittest.mock import patch
            from agent_system.graph import _estimate_index_with_timeout

            def never_returns_in_time(*_a, **_kw):
                time.sleep(30)
                return {"estimated_index": "infinite", "confidence": 1.0}

            with patch("agent_system.lib.congruence.estimate_index", never_returns_in_time):
                result = _estimate_index_with_timeout(
                    lambda _w: True, ["a", "b"], max_depth=4, timeout=0.05,
                )
            assert result["estimated_index"] == "unknown", result
            print("OK")
        """)
        repo_root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)


# ── escalate action ────────────────────────────────────────────────────────


class TestEscalateAction(unittest.TestCase):
    """action='escalate' must produce status='partial', never 'success'."""

    def test_escalate_with_verdict_is_partial(self):
        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "escalate",
                    "verdict": "non_regular",
                    "confidence": 0.6,
                    "issues_found": ["Contradictory specialist outputs"],
                },
            },
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["evidence"].get("needs_human_review"))

    def test_escalate_without_verdict_is_partial(self):
        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "escalate",
                    "issues_found": ["Cannot determine regularity"],
                },
            },
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["evidence"].get("needs_human_review"))

    def test_escalate_confidence_from_reasoning(self):
        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "escalate",
                    "confidence": 0.3,
                },
            },
        )
        result = assemble_result_node(state)["result"]
        self.assertAlmostEqual(result["confidence"], 0.3)

    def test_escalate_confidence_falls_back_to_hypothesis(self):
        state = _base_state(
            hypothesis={"hypothesis": "non_regular", "confidence": 0.85},
            reasoning_output={
                "evidence": {"action": "escalate"},
            },
        )
        result = assemble_result_node(state)["result"]
        self.assertAlmostEqual(result["confidence"], 0.85)


# ── decide_retry routing ──────────────────────────────────────────────────


class TestDecideRetry(unittest.TestCase):

    def test_escalate_goes_to_done(self):
        state = _base_state(
            reasoning_output={"evidence": {"action": "escalate"}},
        )
        self.assertEqual(decide_retry(state), "done")

    def test_retry_enriched_within_limit(self):
        state = _base_state(
            retry_round=0,
            reasoning_output={"evidence": {"action": "retry_enriched"}},
        )
        self.assertEqual(decide_retry(state), "retry")

    def test_retry_enriched_exceeds_limit(self):
        state = _base_state(
            retry_round=2,
            reasoning_output={"evidence": {"action": "retry_enriched"}},
        )
        self.assertEqual(decide_retry(state), "done")

    def test_invert_within_limit(self):
        state = _base_state(
            inversions_done=0,
            reasoning_output={"evidence": {"action": "invert_hypothesis"}},
        )
        self.assertEqual(decide_retry(state), "invert")

    def test_invert_exceeds_limit(self):
        state = _base_state(
            inversions_done=1,
            reasoning_output={"evidence": {"action": "invert_hypothesis"}},
        )
        self.assertEqual(decide_retry(state), "done")

    def test_proceed_to_formalizer_goes_to_done(self):
        state = _base_state(
            reasoning_output={"evidence": {"action": "proceed_to_formalizer"}},
        )
        self.assertEqual(decide_retry(state), "done")

    def test_no_reasoning_output_goes_to_done(self):
        state = _base_state(reasoning_output=None)
        self.assertEqual(decide_retry(state), "done")


# ── classifier dispatch ───────────────────────────────────────────────────


class TestClassifierDispatch(unittest.TestCase):

    def test_uses_classifier_dispatch_when_present(self):
        state = _base_state(
            classifier_evidence={
                "dispatch": {
                    "re_builder": True,
                    "dfa_builder": True,
                    "pumping": False,
                    "nerode": False,
                    "closure": False,
                },
            },
        )
        result = setup_dispatch_node(state)
        d = result["dispatch"]
        self.assertTrue(d["re_builder"])
        self.assertTrue(d["dfa_builder"])
        self.assertFalse(d["pumping"])
        self.assertFalse(d["nerode"])
        self.assertFalse(d["closure"])

    def test_fallback_when_no_classifier_dispatch(self):
        state = _base_state(classifier_evidence={})
        result = setup_dispatch_node(state)
        d = result["dispatch"]
        self.assertTrue(all(v is True for v in d.values()))

    def test_grammar_analyzer_added_for_grammar_kind(self):
        state = _base_state(
            lang_kind="grammar",
            classifier_evidence={
                "dispatch": {"pumping": True, "nerode": True,
                             "re_builder": False, "dfa_builder": False,
                             "closure": False},
            },
        )
        result = setup_dispatch_node(state)
        self.assertTrue(result["dispatch"]["grammar_analyzer"])

    def test_retry_dispatch_preserved(self):
        """On retry, dispatch set by retry_planner should not be overwritten."""
        state = _base_state(
            dispatch={"pumping": True, "nerode": False, "re_builder": False,
                       "dfa_builder": False, "closure": False},
            classifier_evidence={
                "dispatch": {"re_builder": True, "dfa_builder": True,
                             "pumping": False, "nerode": False, "closure": False},
            },
        )
        result = setup_dispatch_node(state)
        # Should return empty — existing dispatch is kept
        self.assertEqual(result, {})


# ── formalize_node (docs/VERDICT_POLICY.md R-Lean) ─────────────────────────
#
# lib.lean_ir (render_statement) is a parallel agent's contract and may not
# exist on disk yet -- these tests never create or import the real module;
# they inject a fake one into sys.modules so
# `from .lib.lean_ir import render_statement` inside formalize_node resolves
# to the fake, exactly like it would resolve to the real module once it
# lands. See agent_system/tests/test_phase3.py for the compose/check_lean_file
# and gate-level R-Lean tests.

import sys
import types
import unittest.mock as _mock


_FAKE_STATEMENT = {
    # Shaped like the real `lib.lean_ir.LeanStatement` contract: a *bare*
    # theorem_decl with no `:=` (compose_lean_file's dict-fallback path
    # appends `:= by\n  <proof_body>` itself) rather than the older
    # `<PROOF>`-placeholder shape (still supported, see test_phase3.py).
    "imports": ["import Mathlib.Computability.DFA"],
    "alphabet_decl": "abbrev Alpha := Fin 2",
    "language_decl": "def inLang (w : List Alpha) : Prop := True",
    "theorem_decl": "theorem tfl_main : True",
    "name": "tfl_main",
}


def _install_fake_lean_ir(render_statement_fn):
    """Install a fake `agent_system.lib.lean_ir` module for the duration of
    a test and return the mock.patch.dict context manager for it."""
    fake_module = types.ModuleType("agent_system.lib.lean_ir")
    fake_module.render_statement = render_statement_fn
    return _mock.patch.dict(sys.modules, {"agent_system.lib.lean_ir": fake_module})


class TestFormalizeNode(unittest.TestCase):

    def test_disabled_by_default(self):
        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer"},
            },
            agent_runner="dummy",
        )
        result = formalize_node(state)
        self.assertEqual(result, {})

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    def test_skipped_when_no_runner(self):
        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer"},
            },
        )
        result = formalize_node(state)
        self.assertEqual(result, {})

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    def test_skipped_when_action_not_formalizer(self):
        state = _base_state(
            reasoning_output={
                "evidence": {"action": "done", "verdict": "regular"},
            },
            agent_runner="dummy",
        )
        result = formalize_node(state)
        self.assertEqual(result, {})

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    def test_not_formalizable_when_lean_ir_missing(self):
        """No `lib.lean_ir` module at all (parallel agent hasn't landed it
        yet) -> graceful `not_formalizable`, not a crash."""
        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer", "verdict": "regular"},
            },
            agent_runner="dummy",
        )
        with _mock.patch.dict(sys.modules, {"agent_system.lib.lean_ir": None}):
            result = formalize_node(state)
        self.assertEqual(result["formalization"]["status"], "not_formalizable")
        self.assertEqual(result["formalization"]["direction"], "regular")

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    def test_not_formalizable_when_render_statement_returns_none(self):
        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer", "verdict": "regular"},
            },
            agent_runner="dummy",
        )
        with _install_fake_lean_ir(lambda ir, direction: None):
            result = formalize_node(state)
        self.assertEqual(result["formalization"]["status"], "not_formalizable")

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    @patch("agent_system.graph.check_lean_file")
    def test_formalize_happy_path_proved(self, mock_check):
        """First formalizer attempt compiles clean -> status proved,
        axioms echoed from check_lean_file, single attempt logged."""
        mock_check.return_value = {
            "status": "proved", "errors": [], "warnings": [],
            "axioms": ["propext"], "elapsed": 1.5,
        }

        class _Runner:
            def run_agent(self, name, input_data):
                self.last_input = input_data
                return {"evidence": {"proof_body": "by trivial", "lemmas_used": []}}

        runner = _Runner()
        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "proceed_to_formalizer",
                    "verdict": "regular",
                    "consolidated_proof": "Trivially true.",
                },
            },
            agent_runner=runner,
        )
        with _install_fake_lean_ir(lambda ir, direction: _FAKE_STATEMENT):
            result = formalize_node(state)

        formalization = result["formalization"]
        self.assertEqual(formalization["status"], "proved")
        self.assertEqual(formalization["direction"], "regular")
        self.assertEqual(formalization["axioms"], ["propext"])
        self.assertEqual(formalization["proof_body"], "by trivial")
        self.assertEqual(len(formalization["attempts"]), 1)
        # No `errors` key was passed on the first (only) attempt.
        self.assertNotIn("errors", runner.last_input)
        mock_check.assert_called_once()

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    @patch("agent_system.graph.check_lean_file")
    def test_formalize_retries_with_errors_then_proves(self, mock_check):
        """First attempt errors, second (fed the errors[]) proves -- the
        MAX_FORMALIZE_ITERATIONS retry cycle (config.py)."""
        first_errors = [{"severity": "error", "data": "unknown identifier 'foo'"}]
        mock_check.side_effect = [
            {"status": "error", "errors": first_errors,
             "warnings": [], "axioms": [], "elapsed": 0.5},
            {"status": "proved", "errors": [], "warnings": [], "axioms": [], "elapsed": 0.7},
        ]

        calls = []

        class _Runner:
            def run_agent(self, name, input_data):
                calls.append(input_data)
                if "errors" in input_data:
                    return {"evidence": {"proof_body": "by simp"}}
                return {"evidence": {"proof_body": "by exact foo"}}

        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer", "verdict": "non_regular"},
            },
            agent_runner=_Runner(),
        )
        with _install_fake_lean_ir(lambda ir, direction: _FAKE_STATEMENT):
            result = formalize_node(state)

        formalization = result["formalization"]
        self.assertEqual(formalization["status"], "proved")
        self.assertEqual(formalization["proof_body"], "by simp")
        self.assertEqual(len(formalization["attempts"]), 2)
        self.assertEqual(mock_check.call_count, 2)
        # The formulation never changes across attempts -- only the proof
        # body does. The second call carries the first call's errors[] back.
        self.assertNotIn("errors", calls[0])
        self.assertIn("errors", calls[1])
        self.assertEqual(calls[1]["errors"], first_errors)

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    @patch("agent_system.graph.check_lean_file")
    def test_has_sorry_stops_without_exhausting_retries(self, mock_check):
        """`has_sorry` is a dead end (R1: not evidence either way) --
        formalize_node stops immediately instead of burning the retry
        budget on a proof body that will just say `sorry` again."""
        mock_check.return_value = {
            "status": "has_sorry", "errors": [], "warnings": [{"data": "declaration uses 'sorry'"}],
            "axioms": [], "elapsed": 0.3,
        }

        class _Runner:
            def run_agent(self, name, input_data):
                return {"evidence": {"proof_body": "by sorry"}}

        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer", "verdict": "regular"},
            },
            agent_runner=_Runner(),
        )
        with _install_fake_lean_ir(lambda ir, direction: _FAKE_STATEMENT):
            result = formalize_node(state)

        self.assertEqual(result["formalization"]["status"], "has_sorry")
        self.assertEqual(mock_check.call_count, 1)

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    def test_formalize_no_output(self):
        """Formalizer returns None -- graceful `error`, no crash."""
        class _Runner:
            def run_agent(self, name, input_data):
                return None

        state = _base_state(
            reasoning_output={
                "evidence": {"action": "proceed_to_formalizer", "verdict": "non_regular"},
            },
            agent_runner=_Runner(),
        )
        with _install_fake_lean_ir(lambda ir, direction: _FAKE_STATEMENT):
            result = formalize_node(state)
        self.assertEqual(result["formalization"]["status"], "error")
        self.assertEqual(result["formalization"]["proof_body"], None)


if __name__ == "__main__":
    unittest.main()
