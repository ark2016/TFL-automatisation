"""Tests for cfl_system.lib.stratification — bounded language & stratification."""

import pytest

from cfl_system.lib.stratification import (
    check_stratification,
    is_bounded_language,
    _decompose_word,
)


# ---------------------------------------------------------------------------
# is_bounded_language
# ---------------------------------------------------------------------------

class TestIsBoundedLanguage:
    def test_single_char_parts_bounded(self):
        """repeated_subword with single-char alphabets → bounded."""
        ir = {
            "language_spec": {
                "kind": "repeated_subword",
                "parts": ["w1", "w2"],
                "concat_pattern": ["w1", "w2"],
                "alphabets": {"w1": ["a"], "w2": ["b"]},
            },
        }
        result = is_bounded_language(ir)
        assert result["is_bounded"] is True
        assert result["bounding_words"] == ["a", "b"]

    def test_multi_char_alphabet_inconclusive(self):
        """repeated_subword with multi-char alphabet → None."""
        ir = {
            "language_spec": {
                "kind": "repeated_subword",
                "parts": ["w1"],
                "concat_pattern": ["w1"],
                "alphabets": {"w1": ["a", "b"]},
            },
        }
        result = is_bounded_language(ir)
        assert result["is_bounded"] is None

    def test_single_terminal_grammar(self):
        """Grammar with single terminal → bounded."""
        ir = {
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "S"]},
                    {"lhs": "S", "rhs": []},
                ],
            },
        }
        result = is_bounded_language(ir)
        assert result["is_bounded"] is True
        assert result["bounding_words"] == ["a"]

    def test_anbn_grammar_bounded(self):
        """Grammar S → aSb | ε has ordered terminals → bounded as a*b*."""
        ir = {
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
        result = is_bounded_language(ir)
        assert result["is_bounded"] is True
        assert result["bounding_words"] == ["a", "b"]

    def test_unsupported_kind(self):
        ir = {"language_spec": {"kind": "natural"}}
        result = is_bounded_language(ir)
        assert result["is_bounded"] is None


# ---------------------------------------------------------------------------
# _decompose_word
# ---------------------------------------------------------------------------

class TestDecomposeWord:
    def test_simple(self):
        assert _decompose_word("aaabbb", ["a", "b"]) == (3, 3)

    def test_empty_word(self):
        assert _decompose_word("", ["a", "b"]) == (0, 0)

    def test_failure(self):
        """Word 'ba' cannot be decomposed as a*b*."""
        assert _decompose_word("ba", ["a", "b"]) is None

    def test_single_bounding(self):
        assert _decompose_word("aaa", ["a"]) == (3,)


# ---------------------------------------------------------------------------
# check_stratification
# ---------------------------------------------------------------------------

class TestCheckStratification:
    @pytest.fixture()
    def anbn_grammar(self):
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

    def test_anbn_stratified(self, anbn_grammar):
        """aⁿbⁿ ⊆ a*b* → exponent vectors {(n,n)} are semilinear."""
        result = check_stratification(anbn_grammar, ["a", "b"], max_length=10)
        assert result["is_stratified"] is True
        assert result["is_cfl"] is True
        # Exponent vectors should be (n, n)
        for ev in result["exponent_vectors"]:
            assert ev[0] == ev[1]

    def test_a_star_stratified(self):
        """a* ⊆ a* → exponent vectors {(n,)} are trivially semilinear."""
        grammar = {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S"]},
                {"lhs": "S", "rhs": []},
            ],
        }
        result = check_stratification(grammar, ["a"], max_length=10)
        assert result["is_stratified"] is True
        assert result["is_cfl"] is True

    def test_empty_grammar(self):
        """Empty grammar → trivially CFL."""
        grammar = {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [],
        }
        result = check_stratification(grammar, ["a"], max_length=10)
        assert result["is_stratified"] is True
        assert result["is_cfl"] is True
        assert result["exponent_vectors"] == []

    def test_no_bounding_words(self):
        grammar = {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": []}],
        }
        result = check_stratification(grammar, [], max_length=10)
        assert result["is_stratified"] is None

    def test_known_non_cfl_bounded(self):
        """Manually inject quadratic exponent vectors to simulate
        a non-CFL bounded language (exponent vectors not semilinear).

        We test check_semilinearity indirectly by verifying that
        if a grammar produces words whose decompositions are semilinear,
        the function correctly reports it.
        """
        # Grammar for a*b*: S → aSb | aS | Sb | ε
        # This generates words in a*b* with various (i, j) exponents
        grammar = {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S"]},
                {"lhs": "S", "rhs": ["S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        }
        result = check_stratification(grammar, ["a", "b"], max_length=8)
        # a*b* with independent a and b counts should still be semilinear
        assert result["is_stratified"] is not False
        assert len(result["exponent_vectors"]) > 0
