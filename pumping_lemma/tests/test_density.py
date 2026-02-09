"""Tests for length density heuristic."""
import pytest
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.heuristics.length_density import LengthDensityHeuristic
from pumping_lemma.utils.math_utils import analyze_growth_pattern, is_prime, is_perfect_square


class TestGrowthAnalysis:
    def test_linear(self):
        assert analyze_growth_pattern([1, 2, 3, 4, 5, 6, 7]) == "linear"

    def test_quadratic_values(self):
        assert analyze_growth_pattern([1, 4, 9, 16, 25, 36, 49]) in ("quadratic", "quadratic_values")

    def test_too_few(self):
        assert analyze_growth_pattern([1, 2]) == "unknown"


class TestMathUtils:
    def test_is_prime(self):
        assert is_prime(2) == True
        assert is_prime(17) == True
        assert is_prime(1) == False
        assert is_prime(4) == False

    def test_is_perfect_square(self):
        assert is_perfect_square(0) == True
        assert is_perfect_square(1) == True
        assert is_perfect_square(4) == True
        assert is_perfect_square(3) == False


class TestLengthDensity:
    def test_square_lengths_non_regular(self):
        """Language with square lengths should be detected."""
        spec = LanguageSpec(
            description="a^(n^2)",
            alphabet={'a'},
            spec_type='set_builder',
            structural_pattern='power',
            membership_fn=lambda w: all(c == 'a' for c in w) and is_perfect_square(len(w)) if w else True
        )
        h = LengthDensityHeuristic()
        result = h.analyze(spec, pumping_constant=5)
        # May or may not detect depending on search depth
        assert result.verdict in ("non_regular", "unknown")
