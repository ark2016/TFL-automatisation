"""Tests for preprocess.py"""
from __future__ import annotations

import pytest
from ll_system.lib.preprocess import (
    check_regularity_hints,
    detect_disjunction_pattern,
    extract_structural_features,
    compute_preprocess_hints,
)


# ---------------------------------------------------------------------------
# Fixtures / shared IRs
# ---------------------------------------------------------------------------

# Format 3: grammar with recursion
IR_FORMAT3_LL1 = {
    "task_type": "ll_check_grammar",
    "source_text": "Is grammar G LL(k)?",
    "grammar": {
        "nonterminals": ["S", "A"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "A", "b"]},
            {"lhs": "A", "rhs": ["a", "A"]},   # recursive
            {"lhs": "A", "rhs": []},
        ],
    },
    "question": "is_ll_k",
    "k": None,
}

# Format 3: finite grammar (no recursion)
IR_FORMAT3_FINITE = {
    "task_type": "ll_check_grammar",
    "source_text": "Finite grammar",
    "grammar": {
        "nonterminals": ["S", "A"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "A", "b"]},
            {"lhs": "A", "rhs": ["a"]},
            {"lhs": "A", "rhs": ["b"]},
        ],
    },
    "question": "is_ll_k",
    "k": None,
}

# Format 1: regex
IR_FORMAT3_REGEX = {
    "task_type": "ll_check_language",
    "source_text": "L = {a^n | n >= 0}",
    "language_spec": {
        "kind": "regex",
        "pattern": "a*",
        "has_backreferences": False,
        "alphabet": ["a"],
    },
    "question": "is_ll",
}

# Format 1: set_builder for aⁿbⁿ ∪ aⁿcⁿ
IR_FORMAT1_ANBN_ANCN = {
    "task_type": "ll_check_language",
    "source_text": "L = {aⁿbⁿ} ∪ {aⁿcⁿ}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b", "c"],
        "variables": [{"name": "n", "domain": {"type": "nat"}}],
        "template": ["aⁿ", "Xⁿ"],
        "constraints": [],
    },
    "question": "is_ll",
}

# Set-builder with counting constraints
IR_SET_BUILDER_COUNTING = {
    "task_type": "ll_check_language",
    "source_text": "L = {a^n b^n | n >= 1}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [{"name": "n", "domain": {"type": "nat"}}],
        "template": ["a^n", "b^n"],
        "constraints": [{"type": "equal", "left": "n_a", "right": "n_b"}],
    },
    "question": "is_ll",
}

# Set-builder with no variables (trivially finite)
IR_SET_BUILDER_NO_VARS = {
    "task_type": "ll_check_language",
    "source_text": "L = {ab, ba}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [],
        "template": ["a", "b"],
        "constraints": [],
    },
    "question": "is_ll",
}

# Set-builder with bounded variables
IR_SET_BUILDER_BOUNDED = {
    "task_type": "ll_check_language",
    "source_text": "L = {a^n | 0 <= n <= 5}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a"],
        "variables": [{"name": "n", "domain": {"type": "bounded", "min": 0, "max": 5}}],
        "template": ["a^n"],
        "constraints": [],
    },
    "question": "is_ll",
}

# Grammar with left recursion
IR_LEFT_RECURSIVE = {
    "task_type": "ll_check_grammar",
    "source_text": "Left-recursive grammar",
    "grammar": {
        "nonterminals": ["S", "A"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["S", "a"]},  # direct left recursion
            {"lhs": "S", "rhs": ["b"]},
            {"lhs": "A", "rhs": ["a"]},
        ],
    },
    "question": "is_ll_k",
    "k": None,
}

# Grammar with prefix ambiguity (FIRST/FIRST conflict)
IR_PREFIX_AMBIGUOUS = {
    "task_type": "ll_check_grammar",
    "source_text": "Grammar with FIRST/FIRST conflict",
    "grammar": {
        "nonterminals": ["S"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": ["a", "b"]},   # both start with 'a'
        ],
    },
    "question": "is_ll_k",
    "k": None,
}

# Palindrome IR
IR_PALINDROME = {
    "task_type": "ll_check_language",
    "source_text": "L is the set of palindromes over {a, b}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [],
        "template": [],
        "constraints": [],
    },
    "question": "is_ll",
}

# IR with union in source text
IR_UNION_SOURCE = {
    "task_type": "ll_check_language",
    "source_text": "L = L1 ∪ L2",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [{"name": "n", "domain": {"type": "nat"}}],
        "template": ["a^n"],
        "constraints": [],
    },
    "question": "is_ll",
}

# IR with reversal
IR_REVERSAL = {
    "task_type": "ll_check_language",
    "source_text": "L = {w w^R | w in {a,b}*}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [{"name": "w", "domain": {"type": "star"}}],
        "template": ["w", "w^R"],
        "constraints": [],
    },
    "question": "is_ll",
}


# ---------------------------------------------------------------------------
# Tests: check_regularity_hints
# ---------------------------------------------------------------------------

class TestCheckRegularityHints:
    def test_regex_is_regular(self):
        result = check_regularity_hints(IR_FORMAT3_REGEX)
        assert result["is_regular"] is True

    def test_regex_confidence_is_1(self):
        result = check_regularity_hints(IR_FORMAT3_REGEX)
        assert result["confidence"] == 1.0

    def test_regex_method_is_regex_pattern(self):
        result = check_regularity_hints(IR_FORMAT3_REGEX)
        assert result["method"] == "regex_pattern"

    def test_recursive_grammar_not_regular(self):
        result = check_regularity_hints(IR_FORMAT3_LL1)
        assert result["is_regular"] is False

    def test_finite_grammar_is_regular(self):
        result = check_regularity_hints(IR_FORMAT3_FINITE)
        assert result["is_regular"] is True

    def test_finite_grammar_method_is_finite(self):
        result = check_regularity_hints(IR_FORMAT3_FINITE)
        assert result["method"] == "finite"

    def test_set_builder_no_vars_is_regular(self):
        result = check_regularity_hints(IR_SET_BUILDER_NO_VARS)
        assert result["is_regular"] is True

    def test_set_builder_bounded_vars_is_regular(self):
        result = check_regularity_hints(IR_SET_BUILDER_BOUNDED)
        assert result["is_regular"] is True

    def test_set_builder_nat_var_not_regular(self):
        # nat domain variable → potentially infinite → not regular
        result = check_regularity_hints(IR_SET_BUILDER_COUNTING)
        assert result["is_regular"] is False

    def test_result_has_required_keys(self):
        result = check_regularity_hints(IR_FORMAT3_LL1)
        assert "is_regular" in result
        assert "confidence" in result
        assert "reason" in result
        assert "method" in result

    def test_left_recursive_grammar_not_regular(self):
        result = check_regularity_hints(IR_LEFT_RECURSIVE)
        assert result["is_regular"] is False

    def test_confidence_is_float_in_range(self):
        for ir in [IR_FORMAT3_REGEX, IR_FORMAT3_LL1, IR_FORMAT3_FINITE]:
            result = check_regularity_hints(ir)
            assert isinstance(result["confidence"], float)
            assert 0.0 <= result["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Tests: detect_disjunction_pattern
# ---------------------------------------------------------------------------

class TestDetectDisjunctionPattern:
    def test_result_has_required_keys(self):
        result = detect_disjunction_pattern(IR_FORMAT3_LL1)
        for key in ("detected", "pattern_type", "shared_prefix", "branches",
                    "shared_counter", "description"):
            assert key in result

    def test_union_source_text_detected(self):
        result = detect_disjunction_pattern(IR_UNION_SOURCE)
        assert result["detected"] is True

    def test_union_source_pattern_type(self):
        result = detect_disjunction_pattern(IR_UNION_SOURCE)
        assert result["pattern_type"] == "suffix_disjunction"

    def test_palindrome_source_text_detected(self):
        result = detect_disjunction_pattern(IR_PALINDROME)
        assert result["detected"] is True
        assert result["pattern_type"] == "palindrome"

    def test_left_recursive_detected_as_prefix_ambiguity(self):
        result = detect_disjunction_pattern(IR_LEFT_RECURSIVE)
        assert result["detected"] is True
        assert result["pattern_type"] == "prefix_ambiguity"

    def test_prefix_ambiguous_grammar_detected(self):
        result = detect_disjunction_pattern(IR_PREFIX_AMBIGUOUS)
        assert result["detected"] is True

    def test_simple_ll1_grammar_not_detected(self):
        # A clean LL(1) grammar: S → a A b | b B a
        ir = {
            "task_type": "ll_check_grammar",
            "source_text": "clean LL(1) grammar",
            "grammar": {
                "nonterminals": ["S", "A", "B"],
                "terminals": ["a", "b"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["a", "A", "b"]},
                    {"lhs": "S", "rhs": ["b", "B", "a"]},
                    {"lhs": "A", "rhs": ["a", "A"]},
                    {"lhs": "A", "rhs": []},
                    {"lhs": "B", "rhs": ["b", "B"]},
                    {"lhs": "B", "rhs": []},
                ],
            },
            "question": "is_ll_k",
            "k": None,
        }
        result = detect_disjunction_pattern(ir)
        # No left recursion, no shared first terminal among rules of same NT
        assert result["detected"] is False

    def test_not_detected_returns_none_fields(self):
        ir = {
            "task_type": "ll_check_grammar",
            "source_text": "clean grammar",
            "grammar": {
                "nonterminals": ["S"],
                "terminals": ["a"],
                "start": "S",
                "rules": [{"lhs": "S", "rhs": ["a"]}],
            },
            "question": "is_ll_k",
        }
        result = detect_disjunction_pattern(ir)
        if not result["detected"]:
            assert result["pattern_type"] is None
            assert result["shared_prefix"] is None


# ---------------------------------------------------------------------------
# Tests: extract_structural_features
# ---------------------------------------------------------------------------

class TestExtractStructuralFeatures:
    def test_returns_list(self):
        features = extract_structural_features(IR_FORMAT3_LL1)
        assert isinstance(features, list)

    def test_left_recursive_grammar_has_feature(self):
        features = extract_structural_features(IR_LEFT_RECURSIVE)
        assert "left_recursive_grammar" in features

    def test_palindrome_source_has_feature(self):
        features = extract_structural_features(IR_PALINDROME)
        assert "palindrome_construction" in features

    def test_union_structure_detected(self):
        features = extract_structural_features(IR_UNION_SOURCE)
        assert "union_structure" in features

    def test_counting_constraint_detected(self):
        features = extract_structural_features(IR_SET_BUILDER_COUNTING)
        assert "counting_constraint" in features

    def test_reversal_detected(self):
        features = extract_structural_features(IR_REVERSAL)
        assert "reversal_component" in features

    def test_no_false_features_for_simple_grammar(self):
        # A simple grammar with no special features
        ir = {
            "task_type": "ll_check_grammar",
            "source_text": "simple grammar",
            "grammar": {
                "nonterminals": ["S"],
                "terminals": ["a"],
                "start": "S",
                "rules": [{"lhs": "S", "rhs": ["a"]}],
            },
            "question": "is_ll_k",
        }
        features = extract_structural_features(ir)
        assert "left_recursive_grammar" not in features
        assert "palindrome_construction" not in features

    def test_features_are_strings(self):
        for ir in [IR_FORMAT3_LL1, IR_LEFT_RECURSIVE, IR_PALINDROME, IR_UNION_SOURCE]:
            features = extract_structural_features(ir)
            assert all(isinstance(f, str) for f in features)

    def test_ambiguous_grammar_feature_detected(self):
        features = extract_structural_features(IR_PREFIX_AMBIGUOUS)
        assert "ambiguous_grammar" in features


# ---------------------------------------------------------------------------
# Tests: compute_preprocess_hints
# ---------------------------------------------------------------------------

class TestComputePreprocessHints:
    REQUIRED_KEYS = {
        "is_regular",
        "regularity_confidence",
        "regularity_reason",
        "disjunction_pattern",
        "extracted_language",
        "structural_features",
    }
    DISJUNCTION_KEYS = {
        "detected",
        "pattern_type",
        "shared_prefix",
        "branches",
        "shared_counter",
        "description",
    }

    def test_returns_all_required_keys(self):
        hints = compute_preprocess_hints(IR_FORMAT3_LL1)
        assert self.REQUIRED_KEYS.issubset(hints.keys())

    def test_disjunction_pattern_has_required_keys(self):
        hints = compute_preprocess_hints(IR_FORMAT3_LL1)
        assert self.DISJUNCTION_KEYS.issubset(hints["disjunction_pattern"].keys())

    def test_regex_is_regular(self):
        hints = compute_preprocess_hints(IR_FORMAT3_REGEX)
        assert hints["is_regular"] is True

    def test_recursive_grammar_not_regular(self):
        hints = compute_preprocess_hints(IR_FORMAT3_LL1)
        assert hints["is_regular"] is False

    def test_structural_features_is_list(self):
        for ir in [IR_FORMAT3_LL1, IR_FORMAT3_REGEX, IR_FORMAT1_ANBN_ANCN]:
            hints = compute_preprocess_hints(ir)
            assert isinstance(hints["structural_features"], list)

    def test_extracted_language_is_none_in_phase1(self):
        hints = compute_preprocess_hints(IR_FORMAT3_LL1)
        assert hints["extracted_language"] is None

    def test_regularity_confidence_in_range(self):
        hints = compute_preprocess_hints(IR_FORMAT3_REGEX)
        conf = hints["regularity_confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0

    def test_left_recursive_grammar_features_present(self):
        hints = compute_preprocess_hints(IR_LEFT_RECURSIVE)
        assert "left_recursive_grammar" in hints["structural_features"]
