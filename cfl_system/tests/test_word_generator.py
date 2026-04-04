"""Tests for cfl_word_generator module."""

from __future__ import annotations

import pytest

from cfl_system.lib.cfl_word_generator import (
    generate_positive_words,
    generate_negative_words,
    generate_boundary_words,
    generate_test_words,
    _get_alphabet,
    _check_constraints,
)


# ---------------------------------------------------------------------------
# Fixtures: IR dicts for various language_spec kinds
# ---------------------------------------------------------------------------

REPEATED_SUBWORD_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{w1w2w1w3 | w2 in {b,c}*, w1 in {a,b}*, w3 in {a,c}*, |wi|>0}",
    "language_spec": {
        "kind": "repeated_subword",
        "parts": ["w1", "w2", "w3"],
        "concat_pattern": ["w1", "w2", "w1", "w3"],
        "alphabets": {"w1": ["a", "b"], "w2": ["b", "c"], "w3": ["a", "c"]},
        "constraints": [
            {"op": "gt", "left": {"kind": "length", "of_var": "w1"}, "right": {"kind": "constant", "value": 0}},
            {"op": "gt", "left": {"kind": "length", "of_var": "w2"}, "right": {"kind": "constant", "value": 0}},
            {"op": "gt", "left": {"kind": "length", "of_var": "w3"}, "right": {"kind": "constant", "value": 0}},
        ],
    },
}

GRAMMAR_ANBN_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{a^n b^n | n >= 0}",
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

EXISTS_DECOMP_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{wwvvR | v, w in {a,b}*}",
    "language_spec": {
        "kind": "exists_decomposition",
        "parts": ["w", "v"],
        "concat_pattern": ["w", "w", "v", "rev(v)"],
        "alphabets": {"w": ["a", "b"], "v": ["a", "b"]},
        "constraints": [],
    },
}

GRAMMAR_FILTER_IR = {
    "task_type": "grammar_filter_cfl",
    "source_text": "Grammar S->aSbb|eps|bbSa|aA, A->aA|a with |a|=|b|",
    "language_spec": {
        "kind": "grammar_filter",
        "grammar": {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S", "A"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b", "b"]},
                {"lhs": "S", "rhs": []},
                {"lhs": "S", "rhs": ["b", "b", "S", "a"]},
                {"lhs": "S", "rhs": ["a", "A"]},
                {"lhs": "A", "rhs": ["a", "A"]},
                {"lhs": "A", "rhs": ["a"]},
            ],
        },
        "filter": {
            "op": "eq",
            "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
            "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"},
        },
    },
}

GRAMMAR_EPSILON_ONLY_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "Grammar generating only epsilon",
    "language_spec": {
        "kind": "grammar",
        "terminals": ["a"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": []},
        ],
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGeneratePositiveRepeatedSubword:
    def test_returns_nonempty(self):
        words = generate_positive_words(REPEATED_SUBWORD_IR, max_count=20)
        assert len(words) > 0

    def test_words_match_pattern(self):
        """Every word should contain the w1 part repeated (it appears twice in the pattern)."""
        words = generate_positive_words(REPEATED_SUBWORD_IR, max_count=20, max_length=12)
        # Each word is w1 + w2 + w1 + w3 where w1 in {a,b}+, w2 in {b,c}+, w3 in {a,c}+
        # All words should use only chars from {a, b, c}
        for w in words:
            assert all(ch in "abc" for ch in w), f"Unexpected char in '{w}'"
            assert len(w) >= 3, f"Word '{w}' too short for |wi|>0 constraint"

    def test_respects_max_length(self):
        words = generate_positive_words(REPEATED_SUBWORD_IR, max_count=50, max_length=8)
        for w in words:
            assert len(w) <= 8


class TestGeneratePositiveGrammar:
    def test_anbn_words(self):
        words = generate_positive_words(GRAMMAR_ANBN_IR, max_count=20, max_length=10)
        assert "" in words, "Empty word should be generated (S -> eps)"
        assert "ab" in words
        assert "aabb" in words

    def test_anbn_structure(self):
        """All generated words should have form a^n b^n."""
        words = generate_positive_words(GRAMMAR_ANBN_IR, max_count=50, max_length=20)
        for w in words:
            n = len(w) // 2
            assert w == "a" * n + "b" * n, f"'{w}' is not a^n b^n"


class TestGeneratePositiveExistsDecomposition:
    def test_returns_valid_words(self):
        words = generate_positive_words(EXISTS_DECOMP_IR, max_count=20, max_length=12)
        assert len(words) > 0
        # Each word is ww + v + rev(v), so all chars from {a, b}
        for w in words:
            assert all(ch in "ab" for ch in w), f"Unexpected char in '{w}'"

    def test_includes_empty(self):
        """Empty assignment (w='', v='') should produce empty word."""
        words = generate_positive_words(EXISTS_DECOMP_IR, max_count=50, max_length=20)
        assert "" in words


class TestGenerateNegativeWords:
    def test_returns_words_not_in_language(self):
        """Negative words for a^n b^n should not be of the form a^n b^n."""
        negatives = generate_negative_words(GRAMMAR_ANBN_IR, max_count=20, max_length=10)
        assert len(negatives) > 0
        for w in negatives:
            n = len(w) // 2
            # Should NOT be a^n b^n
            assert w != "a" * n + "b" * n or len(w) % 2 != 0, (
                f"Negative word '{w}' is actually in the language"
            )

    def test_uses_correct_alphabet(self):
        negatives = generate_negative_words(GRAMMAR_ANBN_IR, max_count=20, max_length=10)
        for w in negatives:
            assert all(ch in "ab" for ch in w)


class TestGenerateBoundaryWords:
    def test_includes_empty_string(self):
        boundary = generate_boundary_words(GRAMMAR_ANBN_IR)
        assert "" in boundary

    def test_includes_single_chars(self):
        boundary = generate_boundary_words(GRAMMAR_ANBN_IR)
        assert "a" in boundary
        assert "b" in boundary

    def test_respects_max_count(self):
        boundary = generate_boundary_words(GRAMMAR_ANBN_IR, max_count=5)
        assert len(boundary) <= 5


class TestGenerateTestWords:
    def test_returns_dict_with_all_keys(self):
        result = generate_test_words(GRAMMAR_ANBN_IR, max_positive=10, max_negative=10)
        assert "positive" in result
        assert "negative" in result
        assert "boundary" in result

    def test_positive_and_negative_disjoint(self):
        result = generate_test_words(GRAMMAR_ANBN_IR, max_positive=20, max_negative=20)
        pos_set = set(result["positive"])
        neg_set = set(result["negative"])
        overlap = pos_set & neg_set
        # Negative generator tries to exclude positives, so overlap should be empty or minimal
        assert len(overlap) == 0, f"Overlap between positive and negative: {overlap}"


class TestGrammarEpsilonOnly:
    def test_only_empty_word(self):
        words = generate_positive_words(GRAMMAR_EPSILON_ONLY_IR, max_count=20, max_length=10)
        assert words == [""], f"Expected only empty word, got {words}"


class TestDeterminism:
    def test_same_seed_same_result(self):
        w1 = generate_positive_words(REPEATED_SUBWORD_IR, max_count=20, seed=123)
        w2 = generate_positive_words(REPEATED_SUBWORD_IR, max_count=20, seed=123)
        assert w1 == w2

    def test_different_seed_may_differ(self):
        """Different seeds should produce potentially different random supplements."""
        # For repeated_subword, the systematic part is the same but random part may differ
        w1 = generate_positive_words(REPEATED_SUBWORD_IR, max_count=50, max_length=20, seed=1)
        w2 = generate_positive_words(REPEATED_SUBWORD_IR, max_count=50, max_length=20, seed=999)
        # Both should be non-empty; they may or may not be identical
        assert len(w1) > 0
        assert len(w2) > 0


class TestGrammarFilter:
    def test_filter_applied(self):
        """All words from grammar_filter should satisfy |a|=|b|."""
        words = generate_positive_words(GRAMMAR_FILTER_IR, max_count=30, max_length=12)
        for w in words:
            assert w.count("a") == w.count("b"), (
                f"Word '{w}' does not satisfy |a|=|b|"
            )


class TestHelpers:
    def test_get_alphabet_grammar(self):
        alpha = _get_alphabet(GRAMMAR_ANBN_IR)
        assert alpha == ["a", "b"]

    def test_get_alphabet_repeated_subword(self):
        alpha = _get_alphabet(REPEATED_SUBWORD_IR)
        assert alpha == ["a", "b", "c"]

    def test_check_constraints_gt(self):
        constraints = [
            {"op": "gt", "left": {"kind": "length", "of_var": "w1"}, "right": {"kind": "constant", "value": 0}},
        ]
        assert _check_constraints({"w1": "ab"}, constraints) is True
        assert _check_constraints({"w1": ""}, constraints) is False

    def test_check_constraints_eq(self):
        constraints = [
            {"op": "eq", "left": {"kind": "length", "of_var": "x"}, "right": {"kind": "length", "of_var": "y"}},
        ]
        assert _check_constraints({"x": "ab", "y": "cd"}, constraints) is True
        assert _check_constraints({"x": "a", "y": "cd"}, constraints) is False
