"""Tests for graph edge cases: closure verification, escalate action,
formalization node, and decide_retry routing."""

import time
import unittest
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


# ── formalize_node ────────────────────────────────────────────────────────


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
    @patch("agent_system.graph.run_agent")
    @patch("agent_system.graph.check_lean")
    def test_formalize_happy_path(self, mock_lean, mock_run):
        """When enabled and formalizer returns code, type check runs."""
        mock_run.return_value = {
            "evidence": {"lean_code": "-- valid lean code\nsorry"},
        }
        mock_lean.return_value = {"status": "valid", "time_seconds": 1.0}

        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "proceed_to_formalizer",
                    "verdict": "non_regular",
                    "best_proof": "pumping",
                    "consolidated_proof": "By pumping lemma...",
                },
            },
            evidence={"pumping": {"evidence": {"proof": "..."}}},
            agent_runner="dummy",
        )
        result = formalize_node(state)
        ev = result.get("evidence", {})
        self.assertEqual(ev.get("formalization", {}).get("status"), "valid")
        self.assertTrue(ev.get("formalization", {}).get("lean_verified"))

    @patch("agent_system.graph.FORMALIZATION_ENABLED", True)
    @patch("agent_system.graph.run_agent")
    def test_formalize_no_output(self, mock_run):
        """Formalizer returns None — graceful skip."""
        mock_run.return_value = None
        state = _base_state(
            reasoning_output={
                "evidence": {
                    "action": "proceed_to_formalizer",
                    "verdict": "non_regular",
                    "best_proof": "pumping",
                },
            },
            agent_runner="dummy",
        )
        result = formalize_node(state)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
