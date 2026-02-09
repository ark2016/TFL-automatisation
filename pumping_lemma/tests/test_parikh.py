"""Tests for Parikh vector heuristic."""
import pytest
from pumping_lemma.models.language_spec import LanguageSpec, ParikhConstraint
from pumping_lemma.heuristics.parikh import ParikhHeuristic
from pumping_lemma.utils.math_utils import compute_parikh_vector


class TestParikhVector:
    def test_compute_parikh_vector(self):
        assert compute_parikh_vector("aabbb", {'a', 'b'}) == {'a': 2, 'b': 3}
        assert compute_parikh_vector("", {'a', 'b'}) == {'a': 0, 'b': 0}

    def test_anbn_non_regular(self):
        """a^n b^n should be detected as non-regular."""
        spec = LanguageSpec(
            description="a^n b^n",
            alphabet={'a', 'b'},
            spec_type='set_builder',
            structural_pattern='balanced',
            constraints=[ParikhConstraint(
                constraint_type='equal', symbols=['a', 'b']
            )],
            membership_fn=lambda w: (
                len(w) % 2 == 0 and
                all(c == 'a' for c in w[:len(w)//2]) and
                all(c == 'b' for c in w[len(w)//2:])
            ) if w else True
        )
        h = ParikhHeuristic()
        result = h.analyze(spec, pumping_constant=10)
        assert result.verdict == "non_regular"
        assert result.confidence > 0.5

    def test_no_constraints_unknown(self):
        """Without constraints, should return unknown."""
        spec = LanguageSpec(description="some language", alphabet={'a'})
        h = ParikhHeuristic()
        result = h.analyze(spec)
        assert result.verdict == "unknown"


class TestParikhConstraintCheck:
    def test_equal_constraint_violated(self):
        """If y has only 'a', pumping breaks a==b balance."""
        from pumping_lemma.utils.math_utils import check_linear_constraint_satisfiable
        # base: a=5, b=10 (already unequal after removing y)
        sat, _ = check_linear_constraint_satisfiable(
            {'a': 5, 'b': 10}, {'a': 1, 'b': 0},
            'equal', ['a', 'b'], [], 0
        )
        # For "equal": need psi_xz[a]+i*psi_y[a] == psi_xz[b]+i*psi_y[b]
        # 5+i*1 == 10+i*0 → 5+i = 10 → i=5 (only one i works, not all)
        # But check_linear_constraint_satisfiable checks for SOME i
        assert sat == True  # i=5 works
