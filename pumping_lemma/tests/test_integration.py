"""Integration tests for the full pipeline."""
import pytest
from pumping_lemma.orchestrator.graph import RegularityChecker


class TestIntegration:
    def setup_method(self):
        self.checker = RegularityChecker(llm_client=None)

    def test_anbn(self):
        """Classic a^n b^n should be non-regular."""
        verdict = self.checker.check(
            "{a^n b^n | n >= 0}",
            mode="full",
            pumping_constant=15
        )
        # Should be detected as non-regular by at least one heuristic
        assert verdict.verdict == "non_regular"
        assert verdict.confidence > 0
        assert len(verdict.proof_trace) > 0

    def test_unknown_language(self):
        """Unrecognized language returns something (not crash)."""
        verdict = self.checker.check("какой-то странный язык", mode="quick")
        assert verdict.verdict in ("regular", "non_regular", "unknown")
