"""Comprehensive tests for ll_system/lib/grammar_transforms.py.

Covers:
  - is_left_recursive
  - eliminate_left_recursion
  - left_factor
  - eliminate_epsilon_rules
  - eliminate_unit_rules
  - to_ll_normal_form
  - is_grammar_equivalent_sample
  - edge cases
"""

from __future__ import annotations

import pytest

from ll_system.lib.grammar_transforms import (
    eliminate_epsilon_rules,
    eliminate_left_recursion,
    eliminate_unit_rules,
    is_grammar_equivalent_sample,
    is_left_recursive,
    left_factor,
    to_ll_normal_form,
    _generate_words,
    _compute_nullable,
    _is_epsilon_rhs,
    _normalize_rhs,
)


# ---------------------------------------------------------------------------
# Grammar fixtures
# ---------------------------------------------------------------------------

# Direct left recursion: S → Sa | b
# Language: b, ba, baa, baaa, ... = b·a*
GRAMMAR_DIRECT_LR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["S", "a"]},
        {"lhs": "S", "rhs": ["b"]},
    ],
}

# Indirect left recursion: S → Aa | b, A → Sc | d
GRAMMAR_INDIRECT_LR = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b", "c", "d"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["A", "a"]},
        {"lhs": "S", "rhs": ["b"]},
        {"lhs": "A", "rhs": ["S", "c"]},
        {"lhs": "A", "rhs": ["d"]},
    ],
}

# Needs left factoring: S → aAb | aAc
GRAMMAR_NEEDS_FACTORING = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b", "c"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A", "b"]},
        {"lhs": "S", "rhs": ["a", "A", "c"]},
        {"lhs": "A", "rhs": ["a"]},
        {"lhs": "A", "rhs": []},
    ],
}

# Has epsilon rules: S → AB, A → a | ε, B → b | ε
GRAMMAR_WITH_EPSILON = {
    "nonterminals": ["S", "A", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["A", "B"]},
        {"lhs": "A", "rhs": ["a"]},
        {"lhs": "A", "rhs": []},
        {"lhs": "B", "rhs": ["b"]},
        {"lhs": "B", "rhs": []},
    ],
}

# Has unit rules: S → A | b, A → B | a, B → c
GRAMMAR_WITH_UNITS = {
    "nonterminals": ["S", "A", "B"],
    "terminals": ["a", "b", "c"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["A"]},
        {"lhs": "S", "rhs": ["b"]},
        {"lhs": "A", "rhs": ["B"]},
        {"lhs": "A", "rhs": ["a"]},
        {"lhs": "B", "rhs": ["c"]},
    ],
}

# Simple non-left-recursive grammar: S → aSb | ε
GRAMMAR_NON_LR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

# Grammar with single terminal rule: S → a
GRAMMAR_SINGLE_TERMINAL = {
    "nonterminals": ["S"],
    "terminals": ["a"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a"]},
    ],
}

# Grammar with no rules
GRAMMAR_NO_RULES = {
    "nonterminals": ["S"],
    "terminals": ["a"],
    "start": "S",
    "rules": [],
}

# Grammar with "ε" symbol in rhs (should normalize to [])
GRAMMAR_EPSILON_SYMBOL = {
    "nonterminals": ["S"],
    "terminals": ["a"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a"]},
        {"lhs": "S", "rhs": ["ε"]},
    ],
}

# Grammar with longer common prefix: S → abC | abD
GRAMMAR_LONGER_PREFIX = {
    "nonterminals": ["S", "C", "D"],
    "terminals": ["a", "b", "c", "d"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "b", "C"]},
        {"lhs": "S", "rhs": ["a", "b", "D"]},
        {"lhs": "C", "rhs": ["c"]},
        {"lhs": "D", "rhs": ["d"]},
    ],
}

# Grammar for LL normal form: has left recursion + common prefix
# E → E + T | T, T → id
GRAMMAR_EXPR_LR = {
    "nonterminals": ["E", "T"],
    "terminals": ["+", "id"],
    "start": "E",
    "rules": [
        {"lhs": "E", "rhs": ["E", "+", "T"]},
        {"lhs": "E", "rhs": ["T"]},
        {"lhs": "T", "rhs": ["id"]},
    ],
}


# ---------------------------------------------------------------------------
# Helper: check no unit rules in grammar
# ---------------------------------------------------------------------------

def _has_unit_rules(grammar: dict) -> bool:
    nonterminals = set(grammar["nonterminals"])
    for rule in grammar["rules"]:
        rhs = rule["rhs"]
        if len(rhs) == 1 and rhs[0] in nonterminals:
            return True
    return False


def _has_epsilon_rules(grammar: dict) -> bool:
    """Check if grammar has epsilon rules (excluding S → ε if ε ∈ L)."""
    start = grammar["start"]
    for rule in grammar["rules"]:
        rhs = rule["rhs"]
        if (rhs == [] or rhs == ["ε"]) and rule["lhs"] != start:
            return True
    return False


def _has_common_prefix(grammar: dict) -> bool:
    """Return True if any nonterminal has rules with a common first token."""
    from collections import defaultdict
    rules_by_nt: dict[str, list[list[str]]] = defaultdict(list)
    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        if rhs and rhs != ["ε"]:
            rules_by_nt[lhs].append(rhs)
    for nt, alts in rules_by_nt.items():
        first_syms = [a[0] for a in alts if a]
        if len(first_syms) != len(set(first_syms)):
            return True
    return False


def _generates_word(grammar: dict, word: str) -> bool:
    """Check if grammar generates the given word via BFS."""
    words = _generate_words(grammar, len(word) + 1)
    return word in words


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_is_epsilon_rhs_empty_list(self):
        assert _is_epsilon_rhs([]) is True

    def test_is_epsilon_rhs_epsilon_symbol(self):
        assert _is_epsilon_rhs(["ε"]) is True

    def test_is_epsilon_rhs_nonempty(self):
        assert _is_epsilon_rhs(["a"]) is False
        assert _is_epsilon_rhs(["a", "b"]) is False

    def test_normalize_rhs_empty(self):
        assert _normalize_rhs([]) == []

    def test_normalize_rhs_epsilon_symbol(self):
        assert _normalize_rhs(["ε"]) == []

    def test_normalize_rhs_nonempty(self):
        assert _normalize_rhs(["a", "b"]) == ["a", "b"]

    def test_compute_nullable_simple(self):
        rules = [("S", []), ("A", ["a"])]
        nullable = _compute_nullable(rules)
        assert "S" in nullable
        assert "A" not in nullable

    def test_compute_nullable_chain(self):
        rules = [("A", []), ("S", ["A"])]
        nullable = _compute_nullable(rules)
        assert "S" in nullable
        assert "A" in nullable


# ---------------------------------------------------------------------------
# 1. is_left_recursive
# ---------------------------------------------------------------------------

class TestIsLeftRecursive:
    def test_direct_lr_is_true(self):
        assert is_left_recursive(GRAMMAR_DIRECT_LR) is True

    def test_indirect_lr_is_true(self):
        assert is_left_recursive(GRAMMAR_INDIRECT_LR) is True

    def test_needs_factoring_is_not_lr(self):
        assert is_left_recursive(GRAMMAR_NEEDS_FACTORING) is False

    def test_simple_non_lr(self):
        assert is_left_recursive(GRAMMAR_NON_LR) is False

    def test_single_terminal_not_lr(self):
        assert is_left_recursive(GRAMMAR_SINGLE_TERMINAL) is False

    def test_no_rules_not_lr(self):
        assert is_left_recursive(GRAMMAR_NO_RULES) is False

    def test_expr_lr_is_true(self):
        assert is_left_recursive(GRAMMAR_EXPR_LR) is True

    def test_with_epsilon_not_lr(self):
        assert is_left_recursive(GRAMMAR_WITH_EPSILON) is False

    def test_with_units_not_lr(self):
        assert is_left_recursive(GRAMMAR_WITH_UNITS) is False

    def test_epsilon_symbol_not_lr(self):
        assert is_left_recursive(GRAMMAR_EPSILON_SYMBOL) is False


# ---------------------------------------------------------------------------
# 2. eliminate_left_recursion
# ---------------------------------------------------------------------------

class TestEliminateLeftRecursion:
    def test_direct_lr_result_is_not_lr(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert is_left_recursive(result) is False

    def test_direct_lr_generates_b(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert _generates_word(result, "b")

    def test_direct_lr_generates_ba(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert _generates_word(result, "ba")

    def test_direct_lr_generates_baa(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert _generates_word(result, "baa")

    def test_direct_lr_does_not_generate_a(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert not _generates_word(result, "a")

    def test_direct_lr_language_preserved(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, result, max_len=6)
        assert ok, f"Language differs: {mismatches}"

    def test_indirect_lr_result_is_not_lr(self):
        result = eliminate_left_recursion(GRAMMAR_INDIRECT_LR)
        assert is_left_recursive(result) is False

    def test_indirect_lr_language_preserved(self):
        result = eliminate_left_recursion(GRAMMAR_INDIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_INDIRECT_LR, result, max_len=5)
        assert ok, f"Language differs: {mismatches}"

    def test_non_lr_grammar_not_lr_after(self):
        result = eliminate_left_recursion(GRAMMAR_NON_LR)
        assert is_left_recursive(result) is False

    def test_non_lr_language_preserved(self):
        result = eliminate_left_recursion(GRAMMAR_NON_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_NON_LR, result, max_len=6)
        assert ok, f"Language differs: {mismatches}"

    def test_expr_lr_result_not_lr(self):
        result = eliminate_left_recursion(GRAMMAR_EXPR_LR)
        assert is_left_recursive(result) is False

    def test_result_has_valid_structure(self):
        result = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        assert "nonterminals" in result
        assert "terminals" in result
        assert "start" in result
        assert "rules" in result
        assert result["start"] == "S"


# ---------------------------------------------------------------------------
# 3. left_factor
# ---------------------------------------------------------------------------

class TestLeftFactor:
    def test_common_prefix_factored(self):
        result = left_factor(GRAMMAR_NEEDS_FACTORING)
        assert not _has_common_prefix(result)

    def test_factored_language_preserved(self):
        result = left_factor(GRAMMAR_NEEDS_FACTORING)
        ok, mismatches = is_grammar_equivalent_sample(
            GRAMMAR_NEEDS_FACTORING, result, max_len=5
        )
        assert ok, f"Language differs: {mismatches}"

    def test_factored_introduces_new_nonterminal(self):
        result = left_factor(GRAMMAR_NEEDS_FACTORING)
        # S_lf or similar should appear
        new_nts = [
            nt for nt in result["nonterminals"]
            if nt not in GRAMMAR_NEEDS_FACTORING["nonterminals"]
        ]
        assert len(new_nts) >= 1

    def test_grammar_without_common_prefix_unchanged_language(self):
        result = left_factor(GRAMMAR_NON_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_NON_LR, result, max_len=6)
        assert ok, f"Language differs: {mismatches}"

    def test_longer_prefix_factored(self):
        result = left_factor(GRAMMAR_LONGER_PREFIX)
        assert not _has_common_prefix(result)

    def test_longer_prefix_language_preserved(self):
        result = left_factor(GRAMMAR_LONGER_PREFIX)
        ok, mismatches = is_grammar_equivalent_sample(
            GRAMMAR_LONGER_PREFIX, result, max_len=5
        )
        assert ok, f"Language differs: {mismatches}"

    def test_single_terminal_unchanged(self):
        result = left_factor(GRAMMAR_SINGLE_TERMINAL)
        assert not _has_common_prefix(result)

    def test_result_has_valid_structure(self):
        result = left_factor(GRAMMAR_NEEDS_FACTORING)
        assert "nonterminals" in result
        assert "terminals" in result
        assert "start" in result
        assert "rules" in result


# ---------------------------------------------------------------------------
# 4. eliminate_epsilon_rules
# ---------------------------------------------------------------------------

class TestEliminateEpsilonRules:
    def test_no_epsilon_rules_except_start(self):
        result = eliminate_epsilon_rules(GRAMMAR_WITH_EPSILON)
        assert not _has_epsilon_rules(result)

    def test_language_preserved_epsilon_word(self):
        # S → AB; A → a | ε; B → b | ε  →  L = {ε, a, b, ab}
        result = eliminate_epsilon_rules(GRAMMAR_WITH_EPSILON)
        words = _generate_words(result, 3)
        assert "a" in words
        assert "b" in words
        assert "ab" in words
        # ε should NOT be in result because start S is nullable
        # (S → ε should be present for start)
        # Actually GRAMMAR_WITH_EPSILON: S → AB, A → a|ε, B → b|ε
        # S can derive ε: S → AB → ε·ε = ε
        # So S → ε should remain
        assert "" in words  # epsilon is in language

    def test_nullable_propagated(self):
        # A → a | ε means A is nullable; B → b | ε means B is nullable
        # S → AB should expand to S → AB | A | B | ε (ε only for start)
        result = eliminate_epsilon_rules(GRAMMAR_WITH_EPSILON)
        all_rhs = [tuple(r["rhs"]) for r in result["rules"] if r["lhs"] == "S"]
        # Both A-only and B-only variants must appear (A and B are nullable)
        assert ("A",) in all_rhs, f"S → A missing; S rules: {all_rhs}"
        assert ("B",) in all_rhs, f"S → B missing; S rules: {all_rhs}"
        assert ("A", "B") in all_rhs, f"S → AB missing; S rules: {all_rhs}"

    def test_epsilon_symbol_normalized(self):
        result = eliminate_epsilon_rules(GRAMMAR_EPSILON_SYMBOL)
        # No rules should have ["ε"] literally
        for rule in result["rules"]:
            assert rule["rhs"] != ["ε"]

    def test_start_symbol_preserved(self):
        result = eliminate_epsilon_rules(GRAMMAR_WITH_EPSILON)
        assert result["start"] == "S"

    def test_no_rules_grammar_doesnt_crash(self):
        result = eliminate_epsilon_rules(GRAMMAR_NO_RULES)
        assert "rules" in result

    def test_language_no_epsilon(self):
        # GRAMMAR_DIRECT_LR has no epsilon rules; should be unchanged language
        result = eliminate_epsilon_rules(GRAMMAR_DIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, result, max_len=5)
        assert ok, f"Language differs: {mismatches}"


# ---------------------------------------------------------------------------
# 5. eliminate_unit_rules
# ---------------------------------------------------------------------------

class TestEliminateUnitRules:
    def test_no_unit_rules_in_result(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        assert not _has_unit_rules(result)

    def test_s_generates_b(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        assert _generates_word(result, "b")

    def test_s_generates_a(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        assert _generates_word(result, "a")

    def test_s_generates_c(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        assert _generates_word(result, "c")

    def test_language_preserved(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_WITH_UNITS, result, max_len=5)
        assert ok, f"Language differs: {mismatches}"

    def test_already_no_unit_rules_unchanged_language(self):
        result = eliminate_unit_rules(GRAMMAR_DIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, result, max_len=5)
        assert ok, f"Language differs: {mismatches}"

    def test_result_has_valid_structure(self):
        result = eliminate_unit_rules(GRAMMAR_WITH_UNITS)
        assert "nonterminals" in result
        assert "terminals" in result
        assert "start" in result
        assert "rules" in result


# ---------------------------------------------------------------------------
# 6. to_ll_normal_form
# ---------------------------------------------------------------------------

class TestToLLNormalForm:
    def test_direct_lr_becomes_non_lr(self):
        result = to_ll_normal_form(GRAMMAR_DIRECT_LR)
        assert is_left_recursive(result) is False

    def test_direct_lr_language_preserved(self):
        result = to_ll_normal_form(GRAMMAR_DIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, result, max_len=6)
        assert ok, f"Language differs: {mismatches}"

    def test_indirect_lr_becomes_non_lr(self):
        result = to_ll_normal_form(GRAMMAR_INDIRECT_LR)
        assert is_left_recursive(result) is False

    def test_expr_lr_becomes_non_lr(self):
        result = to_ll_normal_form(GRAMMAR_EXPR_LR)
        assert is_left_recursive(result) is False

    def test_expr_lr_language_preserved(self):
        result = to_ll_normal_form(GRAMMAR_EXPR_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_EXPR_LR, result, max_len=6)
        assert ok, f"Language differs: {mismatches}"

    def test_no_epsilon_rules_except_start(self):
        result = to_ll_normal_form(GRAMMAR_WITH_EPSILON)
        assert not _has_epsilon_rules(result)

    def test_no_unit_rules_in_result(self):
        result = to_ll_normal_form(GRAMMAR_WITH_UNITS)
        assert not _has_unit_rules(result)

    def test_result_has_valid_structure(self):
        result = to_ll_normal_form(GRAMMAR_DIRECT_LR)
        assert "nonterminals" in result
        assert "terminals" in result
        assert "start" in result
        assert "rules" in result
        assert result["start"] == "S"

    def test_pipeline_does_not_crash_on_epsilon_grammar(self):
        result = to_ll_normal_form(GRAMMAR_WITH_EPSILON)
        assert isinstance(result, dict)

    def test_pipeline_does_not_crash_on_units(self):
        result = to_ll_normal_form(GRAMMAR_WITH_UNITS)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 7. is_grammar_equivalent_sample
# ---------------------------------------------------------------------------

class TestIsGrammarEquivalentSample:
    def test_same_grammar_equal(self):
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, GRAMMAR_DIRECT_LR)
        assert ok is True
        assert mismatches == []

    def test_direct_lr_vs_transformed_equal(self):
        transformed = eliminate_left_recursion(GRAMMAR_DIRECT_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, transformed, max_len=6)
        assert ok is True, f"Mismatches: {mismatches}"

    def test_different_grammars_disagree(self):
        # S → a vs S → b  should disagree
        g1 = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        }
        g2 = {
            "nonterminals": ["S"],
            "terminals": ["b"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["b"]}],
        }
        ok, mismatches = is_grammar_equivalent_sample(g1, g2)
        assert ok is False
        assert len(mismatches) > 0

    def test_non_lr_vs_eliminated(self):
        transformed = eliminate_left_recursion(GRAMMAR_NON_LR)
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_NON_LR, transformed, max_len=6)
        assert ok is True, f"Mismatches: {mismatches}"

    def test_return_type(self):
        ok, mismatches = is_grammar_equivalent_sample(GRAMMAR_DIRECT_LR, GRAMMAR_DIRECT_LR)
        assert isinstance(ok, bool)
        assert isinstance(mismatches, list)


# ---------------------------------------------------------------------------
# 8. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_rules_grammar_is_not_lr(self):
        assert is_left_recursive(GRAMMAR_NO_RULES) is False

    def test_no_rules_elim_left_recursion_no_crash(self):
        result = eliminate_left_recursion(GRAMMAR_NO_RULES)
        assert isinstance(result, dict)

    def test_no_rules_left_factor_no_crash(self):
        result = left_factor(GRAMMAR_NO_RULES)
        assert isinstance(result, dict)

    def test_no_rules_elim_epsilon_no_crash(self):
        result = eliminate_epsilon_rules(GRAMMAR_NO_RULES)
        assert isinstance(result, dict)

    def test_no_rules_elim_units_no_crash(self):
        result = eliminate_unit_rules(GRAMMAR_NO_RULES)
        assert isinstance(result, dict)

    def test_single_terminal_not_lr(self):
        assert is_left_recursive(GRAMMAR_SINGLE_TERMINAL) is False

    def test_single_terminal_elim_lr_no_change(self):
        result = eliminate_left_recursion(GRAMMAR_SINGLE_TERMINAL)
        assert _generates_word(result, "a")
        assert not _generates_word(result, "b")

    def test_epsilon_symbol_rhs_normalized_in_elim_eps(self):
        result = eliminate_epsilon_rules(GRAMMAR_EPSILON_SYMBOL)
        # "a" is still generated
        assert _generates_word(result, "a")

    def test_epsilon_symbol_rhs_normalized_in_lr_check(self):
        # Grammar with "ε" should not throw
        ok = is_left_recursive(GRAMMAR_EPSILON_SYMBOL)
        assert isinstance(ok, bool)

    def test_ll_normal_form_single_terminal(self):
        result = to_ll_normal_form(GRAMMAR_SINGLE_TERMINAL)
        assert _generates_word(result, "a")
        assert is_left_recursive(result) is False

    def test_needs_factoring_is_not_lr(self):
        assert is_left_recursive(GRAMMAR_NEEDS_FACTORING) is False

    def test_longer_prefix_is_not_lr(self):
        assert is_left_recursive(GRAMMAR_LONGER_PREFIX) is False

    def test_with_epsilon_language_equivalence_after_pipeline(self):
        result = to_ll_normal_form(GRAMMAR_WITH_EPSILON)
        ok, mismatches = is_grammar_equivalent_sample(
            GRAMMAR_WITH_EPSILON, result, max_len=4
        )
        assert ok, f"Language differs: {mismatches}"
