"""Tests for cfl_system.lib.cfl_hypothesis."""

from __future__ import annotations

import pytest

from cfl_system.lib.cfl_hypothesis import (
    AGENT_MAP,
    _are_interleaved,
    _extract_features,
    _is_filter_regular,
    analyze_cfl_hypothesis,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_ir(kind: str, **extra) -> dict:
    spec = {"kind": kind, **extra}
    return {"language_spec": spec}


# ── 1. Crossed dependencies → non_cfl, high confidence ──────────────────


class TestCrossedDependencies:
    def test_interleaved_repeated_subword(self):
        """Pattern w1 w2 w1 w2 has crossed dependencies → non_cfl."""
        ir = _make_ir(
            "repeated_subword",
            concat_pattern=["w1", "w2", "w1", "w2"],
            parts={"w1": {}, "w2": {}},
            alphabets={"w1": ["a", "b"], "w2": ["c", "d"]},
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "non_cfl"
        assert result["confidence"] == pytest.approx(0.85)
        assert result["features"]["crossed_dependencies"] is True

    def test_interleaved_helper(self):
        assert _are_interleaved([0, 2], [1, 3]) is True
        assert _are_interleaved([0, 1], [2, 3]) is False


# ── 2. Repeated subword without crossed deps → non_cfl, moderate ────────


class TestRepeatedSubword:
    def test_repeated_no_cross(self):
        """w1 w2 w1 w3 — w1 repeated but not interleaved with another repeat."""
        ir = _make_ir(
            "repeated_subword",
            concat_pattern=["w1", "w2", "w1", "w3"],
            parts={"w1": {}, "w2": {}, "w3": {}},
            alphabets={"w1": ["a", "b"], "w2": ["c"], "w3": ["d"]},
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "non_cfl"
        assert result["confidence"] == pytest.approx(0.75)
        assert result["features"]["has_repeated_subword"] is True
        assert result["features"]["crossed_dependencies"] is False

    def test_bounded_repeated_is_unknown(self):
        """Single-char alphabets → bounded language → unknown."""
        ir = _make_ir(
            "repeated_subword",
            concat_pattern=["w1", "w2", "w1"],
            parts={"w1": {}, "w2": {}},
            alphabets={"w1": ["a"], "w2": ["b"]},
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "unknown"
        assert result["features"]["is_bounded_language"] is True


# ── 3. exists_decomposition with rev → cfl ──────────────────────────────


class TestExistsDecomposition:
    def test_palindrome_pattern(self):
        """v rev(v) → CFL-friendly palindrome."""
        ir = _make_ir(
            "exists_decomposition",
            concat_pattern=["v", "rev(v)"],
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "cfl"
        assert result["confidence"] == pytest.approx(0.7)
        assert result["features"]["has_reverse"] is True
        assert result["features"]["has_repeated_subword"] is False

    def test_ww_no_reverse(self):
        """w w (no reverse) → suspect non-CFL."""
        ir = _make_ir(
            "exists_decomposition",
            concat_pattern=["w", "w"],
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "non_cfl"
        assert result["confidence"] == pytest.approx(0.75)
        assert result["features"]["has_repeated_subword"] is True
        assert result["features"]["has_reverse"] is False


# ── 4. grammar_filter ────────────────────────────────────────────────────


class TestGrammarFilter:
    def test_regular_filter_is_cfl(self):
        """Grammar + regular filter → CFL."""
        ir = _make_ir(
            "grammar_filter",
            grammar={"rules": []},
            filter={"type": "modular", "mod": 2, "remainder": 0},
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "cfl"
        assert result["confidence"] == pytest.approx(0.9)
        assert result["features"]["filter_is_regular"] is True

    def test_non_regular_filter_is_unknown(self):
        """Grammar + non-regular filter (|a| = |b|) → unknown."""
        ir = _make_ir(
            "grammar_filter",
            grammar={"rules": []},
            filter={"type": "symbol_count_comparison", "lhs": "a", "rhs": "b"},
        )
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "unknown"
        assert result["confidence"] == pytest.approx(0.4)
        assert result["features"]["filter_is_regular"] is False
        assert result["features"]["has_counting_constraint"] is True


# ── 5. grammar kind → cfl ───────────────────────────────────────────────


class TestGrammarKind:
    def test_plain_grammar(self):
        ir = _make_ir("grammar", rules=[])
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "cfl"
        assert result["confidence"] == pytest.approx(0.95)


# ── 6. natural kind → unknown ───────────────────────────────────────────


class TestNaturalKind:
    def test_natural_language(self):
        ir = _make_ir("natural", description="all strings with equal a's and b's")
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == "unknown"
        assert result["confidence"] == pytest.approx(0.3)


# ── 7. Feature extraction correctness ───────────────────────────────────


class TestFeatureExtraction:
    def test_repeated_subword_features(self):
        ir = _make_ir(
            "repeated_subword",
            concat_pattern=["w1", "w2", "w1"],
            parts={"w1": {}, "w2": {}},
            alphabets={"w1": ["a", "b"], "w2": ["c"]},
        )
        features = _extract_features(ir)
        assert features["has_repeated_subword"] is True
        assert features["crossed_dependencies"] is False
        assert features["is_bounded_language"] is False
        assert features["is_grammar_filter"] is False

    def test_grammar_filter_features(self):
        ir = _make_ir(
            "grammar_filter",
            grammar={"rules": []},
            filter={"type": "length_bound", "bound": 10},
        )
        features = _extract_features(ir)
        assert features["is_grammar_filter"] is True
        assert features["filter_is_regular"] is True

    def test_counting_constraint_from_constraints(self):
        ir = _make_ir(
            "repeated_subword",
            concat_pattern=["w1", "w2"],
            parts={"w1": {}, "w2": {}},
            alphabets={"w1": ["a"], "w2": ["b"]},
            constraints=[{"type": "length_relation", "op": "="}],
        )
        features = _extract_features(ir)
        assert features["has_counting_constraint"] is True


# ── 8. Suggested agents match hypothesis ─────────────────────────────────


class TestSuggestedAgents:
    @pytest.mark.parametrize(
        "kind, extra, expected_hyp",
        [
            ("grammar", {"rules": []}, "cfl"),
            (
                "repeated_subword",
                {
                    "concat_pattern": ["w1", "w2", "w1", "w2"],
                    "parts": {"w1": {}, "w2": {}},
                    "alphabets": {"w1": ["a", "b"], "w2": ["c", "d"]},
                },
                "non_cfl",
            ),
            ("natural", {"description": "something"}, "unknown"),
        ],
    )
    def test_agents_from_map(self, kind, extra, expected_hyp):
        ir = _make_ir(kind, **extra)
        result = analyze_cfl_hypothesis(ir)
        assert result["hypothesis"] == expected_hyp
        assert result["suggested_agents"] == AGENT_MAP[expected_hyp]


# ── 9. _is_filter_regular edge cases ────────────────────────────────────


class TestIsFilterRegular:
    def test_natural_language_filter_is_none(self):
        assert _is_filter_regular({"type": "natural_language_filter"}) is None

    def test_empty_filter_is_none(self):
        assert _is_filter_regular({}) is None

    def test_explicit_is_regular_flag(self):
        assert _is_filter_regular({"is_regular": True}) is True
        assert _is_filter_regular({"is_regular": False}) is False

    def test_constant_comparison_is_regular(self):
        assert _is_filter_regular({"lhs": "count_a", "rhs": 5}) is True

    def test_symbol_comparison_not_regular(self):
        assert _is_filter_regular({"lhs": "count_a", "rhs": "count_b"}) is False


# ── 10. exists_decomposition with rev + repeated subword ─────────────────


class TestMixedPatterns:
    def test_rev_with_repeated_base(self):
        """w rev(w) w — has reverse AND repeated subword."""
        ir = _make_ir(
            "exists_decomposition",
            concat_pattern=["w", "rev(w)", "w"],
        )
        result = analyze_cfl_hypothesis(ir)
        # w appears twice (non-rev) → repeated subword, but also has reverse
        # Repeated subword + no-reverse check: has_reverse is True, so the
        # "non_cfl" branch for repeated subword won't fire.
        # Neither will the pure-reverse branch (has_repeated_subword is True).
        # Falls through to unknown.
        assert result["features"]["has_reverse"] is True
        assert result["features"]["has_repeated_subword"] is True
        assert result["hypothesis"] == "unknown"
