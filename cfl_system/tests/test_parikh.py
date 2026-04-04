"""Tests for cfl_system.lib.parikh — Parikh image and semilinearity."""

import pytest

from cfl_system.lib.parikh import (
    analyze_parikh,
    check_semilinearity,
    parikh_image_from_grammar,
    parikh_vector,
)


# ---------------------------------------------------------------------------
# parikh_vector
# ---------------------------------------------------------------------------

class TestParikhVector:
    def test_basic(self):
        assert parikh_vector("aabb", ["a", "b"]) == (2, 2)

    def test_empty_word(self):
        assert parikh_vector("", ["a", "b"]) == (0, 0)

    def test_single_symbol_alphabet(self):
        assert parikh_vector("aaaa", ["a"]) == (4,)

    def test_three_symbols(self):
        assert parikh_vector("abcabc", ["a", "b", "c"]) == (2, 2, 2)

    def test_missing_symbol(self):
        """Word contains no occurrences of some alphabet symbols."""
        assert parikh_vector("aaa", ["a", "b"]) == (3, 0)


# ---------------------------------------------------------------------------
# parikh_image_from_grammar
# ---------------------------------------------------------------------------

class TestParikhImageFromGrammar:
    @pytest.fixture()
    def anbn_grammar(self):
        """Grammar for {aⁿbⁿ | n ≥ 0}: S → aSb | ε"""
        return {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        }

    @pytest.fixture()
    def a_star_grammar(self):
        """Grammar for a*: S → aS | ε"""
        return {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S"]},
                {"lhs": "S", "rhs": []},
            ],
        }

    def test_anbn_vectors(self, anbn_grammar):
        vectors = parikh_image_from_grammar(anbn_grammar, max_length=10)
        # Should contain (0,0), (1,1), (2,2), ..., (5,5)
        for n in range(6):
            assert (n, n) in vectors
        # Should NOT contain off-diagonal vectors
        for v in vectors:
            assert v[0] == v[1], f"Unexpected vector {v} in aⁿbⁿ"

    def test_a_star_vectors(self, a_star_grammar):
        vectors = parikh_image_from_grammar(a_star_grammar, max_length=8)
        for n in range(9):
            assert (n,) in vectors

    def test_empty_grammar(self):
        """Grammar with no rules produces no words (not even ε)."""
        grammar = {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [],
        }
        vectors = parikh_image_from_grammar(grammar, max_length=10)
        assert vectors == set()


# ---------------------------------------------------------------------------
# check_semilinearity
# ---------------------------------------------------------------------------

class TestCheckSemilinearity:
    def test_single_linear_set(self):
        """Vectors {(n, n) | n=0..10} form a single linear set."""
        vectors = {(n, n) for n in range(11)}
        result = check_semilinearity(vectors, alphabet_size=2)
        assert result["is_semilinear"] is True

    def test_arithmetic_progression_1d(self):
        """1D set {0, 2, 4, 6, 8} is semilinear."""
        vectors = {(n,) for n in range(0, 10, 2)}
        result = check_semilinearity(vectors, alphabet_size=1)
        assert result["is_semilinear"] is True

    def test_empty_set(self):
        result = check_semilinearity(set(), alphabet_size=2)
        assert result["is_semilinear"] is True

    def test_singleton(self):
        result = check_semilinearity({(3, 5)}, alphabet_size=2)
        assert result["is_semilinear"] is True

    def test_quadratic_growth_1d(self):
        """1D set {0, 1, 4, 9, 16, 25, ...} (squares) is NOT semilinear."""
        vectors = {(n * n,) for n in range(8)}
        result = check_semilinearity(vectors, alphabet_size=1)
        assert result["is_semilinear"] is False

    def test_non_semilinear_via_projection(self):
        """If one projection is non-semilinear, the whole set is."""
        # (n², n) — first projection is quadratic
        vectors = {(n * n, n) for n in range(8)}
        result = check_semilinearity(vectors, alphabet_size=2)
        assert result["is_semilinear"] is False


# ---------------------------------------------------------------------------
# analyze_parikh (integration)
# ---------------------------------------------------------------------------

class TestAnalyzeParikh:
    def test_grammar_ir(self):
        ir = {
            "task_type": "classify_cfl",
            "source_text": "aⁿbⁿ",
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a", "b"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "S", "b"]},
                    {"lhs": "S", "rhs": []},
                ],
            },
        }
        result = analyze_parikh(ir)
        assert result["is_semilinear"] is True
        assert result["conclusion"] is None
        assert len(result["vectors_sampled"]) > 0

    def test_repeated_subword_ir(self):
        ir = {
            "task_type": "classify_and_prove_cfl",
            "source_text": "aⁿbⁿ as repeated_subword",
            "language_spec": {
                "kind": "repeated_subword",
                "parts": ["w1", "w2"],
                "concat_pattern": ["w1", "w2"],
                "alphabets": {"w1": ["a"], "w2": ["b"]},
                "constraints": [],
            },
        }
        result = analyze_parikh(ir)
        assert result["is_semilinear"] is not False
        assert len(result["vectors_sampled"]) > 0

    def test_unsupported_kind(self):
        ir = {
            "language_spec": {"kind": "natural"},
        }
        result = analyze_parikh(ir)
        assert result["is_semilinear"] is None
        assert "Unsupported" in result["commutative_image"]

    def test_grammar_filter_ir(self):
        ir = {
            "task_type": "grammar_filter_cfl",
            "source_text": "test",
            "language_spec": {
                "kind": "grammar_filter",
                "grammar": {
                    "kind": "grammar",
                    "terminals": ["a", "b"],
                    "nonterminals": ["S"],
                    "start": "S",
                    "rules": [
                        {"lhs": "S", "rhs": ["a", "S", "b"]},
                        {"lhs": "S", "rhs": []},
                    ],
                },
                "filter": {
                    "op": "eq",
                    "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
                    "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
                },
            },
        }
        result = analyze_parikh(ir)
        # aⁿbⁿ already has equal a/b counts, so filter keeps everything
        assert result["is_semilinear"] is True
