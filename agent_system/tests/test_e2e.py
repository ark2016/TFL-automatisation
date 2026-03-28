"""End-to-end tests — full mock pipeline on all 3 example tasks."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import Pipeline, MockRunner

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_ir(name: str) -> dict:
    with open(EXAMPLES_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def _run_mock(task_name: str) -> dict:
    ir = _load_ir(task_name)
    mock = MockRunner(EXAMPLES_DIR, task_prefix=task_name)
    pipeline = Pipeline()
    return pipeline.run_full_pipeline(ir, mock_runner=mock)


# ---------------------------------------------------------------------------


class TestTask1PalindromeRegular(unittest.TestCase):
    """Task 1: palindrome prefix/suffix — REGULAR language."""

    def setUp(self):
        self.result = _run_mock("task1_palindrome_prefix_suffix")

    def test_pipeline_runs(self):
        self.assertIn("module", self.result)
        self.assertEqual(self.result["module"], "orchestrator")

    def test_hypothesis_is_regular(self):
        hyp = self.result["evidence"]["hypothesis"]
        self.assertEqual(hyp["hypothesis"], "regular")

    def test_classifier_loaded(self):
        self.assertIn("classifier", self.result["evidence"])
        clf = self.result["evidence"]["classifier"]
        self.assertEqual(clf["evidence"]["verdict"], "regular")

    def test_re_builder_loaded(self):
        self.assertIn("re_builder", self.result["evidence"])
        regex = self.result["evidence"]["re_builder"]["evidence"]["regex"]
        self.assertIn("aa|bb", regex)

    def test_dfa_builder_loaded(self):
        self.assertIn("dfa_builder", self.result["evidence"])
        dfa = self.result["evidence"]["dfa_builder"]["evidence"]["dfa"]
        self.assertIn("states", dfa)
        self.assertIn("transitions", dfa)

    def test_oracle_test_ran(self):
        """Oracle test must run because DFA is available and oracle can be built."""
        self.assertIn("oracle_test", self.result["evidence"])

    def test_oracle_test_finds_counterexample(self):
        """The mock DFA for '(aa|bb)Σ*|Σ*(aa|bb)' is wrong for this language.
        Oracle test should find 'abba' (v='ab', v^R='ba') as counterexample."""
        ot = self.result["evidence"]["oracle_test"]
        self.assertEqual(ot["status"], "fail")
        self.assertIsNotNone(ot["counterexample"])
        # The counterexample word should be accepted by oracle but not by DFA
        self.assertTrue(ot["counterexample"]["oracle_says"])
        self.assertFalse(ot["counterexample"]["automaton_says"])

    def test_status_failure_due_to_counterexample(self):
        """Pipeline should report failure since oracle test found discrepancy."""
        self.assertEqual(self.result["status"], "failure")


class TestTask2GrammarNonRegular(unittest.TestCase):
    """Task 2: grammar S→SaSb|ε|A — NON-REGULAR language."""

    def setUp(self):
        self.result = _run_mock("task2_grammar_sasb")

    def test_hypothesis_non_regular(self):
        hyp = self.result["evidence"]["hypothesis"]
        self.assertEqual(hyp["hypothesis"], "non_regular")

    def test_classifier_non_regular(self):
        clf = self.result["evidence"]["classifier"]
        self.assertEqual(clf["evidence"]["verdict"], "non_regular")

    def test_pumping_proof_loaded(self):
        self.assertIn("pumping", self.result["evidence"])
        pumping = self.result["evidence"]["pumping"]
        self.assertEqual(pumping["status"], "success")
        self.assertEqual(pumping["evidence"]["verdict"], "non_regular")

    def test_no_dfa_no_oracle_test(self):
        """Non-regular track: no DFA built, no oracle test."""
        self.assertNotIn("dfa_builder", self.result["evidence"])
        self.assertNotIn("oracle_test", self.result["evidence"])

    def test_status_partial(self):
        """No oracle test → status is partial (proof not verified by oracle)."""
        self.assertEqual(self.result["status"], "partial")


class TestTask3RegexBackref(unittest.TestCase):
    """Task 3: regex with backreference — NON-REGULAR."""

    def setUp(self):
        self.result = _run_mock("task3_regex_backref")

    def test_hypothesis_non_regular(self):
        hyp = self.result["evidence"]["hypothesis"]
        self.assertEqual(hyp["hypothesis"], "non_regular")

    def test_pumping_and_closure_loaded(self):
        self.assertIn("pumping", self.result["evidence"])
        self.assertIn("closure", self.result["evidence"])

    def test_closure_proof_present(self):
        closure = self.result["evidence"]["closure"]
        self.assertEqual(closure["status"], "success")
        evidence_str = json.dumps(closure["evidence"])
        self.assertIn("intersection", evidence_str.lower())

    def test_oracle_not_built(self):
        """Regex with backreferences can't build oracle — error should be recorded."""
        self.assertIn("Oracle build failed", str(self.result["errors"]))

    def test_status_partial(self):
        self.assertEqual(self.result["status"], "partial")


class TestPipelineWithoutMock(unittest.TestCase):
    """Verify that run_from_ir still works (backward compatibility)."""

    def test_run_from_ir_still_works(self):
        ir = _load_ir("task2_grammar_sasb")
        pipeline = Pipeline()
        result = pipeline.run_from_ir(ir)
        self.assertEqual(result["module"], "orchestrator")
        self.assertIn(result["status"], ("success", "partial", "failure"))
        self.assertIn("hypothesis", result["evidence"])


if __name__ == "__main__":
    unittest.main()
