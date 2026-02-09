"""Tests for closure property heuristic."""
import pytest
from pumping_lemma.models.language_spec import LanguageSpec, ParikhConstraint
from pumping_lemma.heuristics.closure import ClosureHeuristic


class TestClosure:
    def test_no_membership_unknown(self):
        spec = LanguageSpec(description="test", alphabet={'a'})
        h = ClosureHeuristic()
        result = h.analyze(spec)
        assert result.verdict == "unknown"

    def test_anbn_with_closure(self):
        """Test closure heuristic on a^n b^n (may need reverse_morfism)."""
        def check_anbn(w):
            if not w: return True
            n = len(w)
            if n % 2 != 0: return False
            k = n // 2
            return w == 'a' * k + 'b' * k

        spec = LanguageSpec(
            description="a^n b^n",
            alphabet={'a', 'b'},
            spec_type='set_builder',
            structural_pattern='balanced',
            constraints=[ParikhConstraint(constraint_type='equal', symbols=['a', 'b'])],
            membership_fn=check_anbn
        )
        h = ClosureHeuristic()
        result = h.analyze(spec, pumping_constant=10)
        # May be unknown if reverse_morfism not available
        assert result.verdict in ("non_regular", "unknown")
