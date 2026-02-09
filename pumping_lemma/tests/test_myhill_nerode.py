"""Tests for Myhill-Nerode heuristic."""
import pytest
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.heuristics.myhill_nerode import MyhillNerodeHeuristic


class TestMyhillNerode:
    def test_anbn_non_regular(self):
        """a^n b^n should have infinitely many equivalence classes."""
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
            membership_fn=check_anbn
        )
        h = MyhillNerodeHeuristic()
        result = h.analyze(spec, pumping_constant=15)
        assert result.verdict == "non_regular"

    def test_no_membership_unknown(self):
        spec = LanguageSpec(description="test", alphabet={'a'})
        h = MyhillNerodeHeuristic()
        result = h.analyze(spec)
        assert result.verdict == "unknown"
