"""Tests for the full Aho–Ullman LL(k) test (docs/THEORY.md §3.1).

Covers ``check_ll_k`` (strong LL(k) vs full LL(k), budget behaviour) and
``find_min_ll_k`` (left-recursion certificate, "not LL(k) up to max_k_checked"
without a certificate, strong_k tracking, timing regression).
"""
from __future__ import annotations

import time

import pytest

from ll_system.lib.ll_table_builder import check_ll_k, find_min_ll_k

# ---------------------------------------------------------------------------
# Grammar fixtures
# ---------------------------------------------------------------------------

# [AU]'s standard example: LL(2) but not strong LL(2) — FOLLOW_2(A) = {aa, ba}
# (global), so A -> b and A -> eps conflict on "ba"; with the LOCAL follow sets
# {aa} and {ba} kept apart, there is no conflict. Strong LL(k) only catches up
# at k=3 (docs/THEORY.md §3.1).
GRAMMAR_AU_LL2_NOT_STRONG = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A", "a", "a"]},
        {"lhs": "S", "rhs": ["b", "A", "b", "a"]},
        {"lhs": "A", "rhs": ["b"]},
        {"lhs": "A", "rhs": []},
    ],
}

# Plain LL(1) grammar — LL(1) and strong LL(1) coincide (docs/THEORY.md §3.1:
# "При k = 1 классы грамматик совпадают").
GRAMMAR_LL1 = {
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
}

# Direct left recursion: S -> S a | b.
GRAMMAR_LEFT_RECURSIVE = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["S", "a"]},
        {"lhs": "S", "rhs": ["b"]},
    ],
}

# Left-recursive B is UNREACHABLE from the start symbol S -> a: the grammar
# itself is plain LL(1) (docs/THEORY.md §3.1: left-recursion certificate
# must be checked "после удаления бесполезных символов"; also
# ll_system/CLAUDE.md: "left recursion != not LL"). is_left_recursive(grammar)
# alone would (wrongly) say True here; find_min_ll_k must remove useless
# symbols first and correctly find LL(1).
GRAMMAR_UNREACHABLE_LEFT_RECURSION = {
    "nonterminals": ["S", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a"]},
        {"lhs": "B", "rhs": ["B", "a"]},
        {"lhs": "B", "rhs": ["b"]},
    ],
}

# Regression for the sigma(A)-as-flat-set bug (docs/THEORY.md §3.1): S -> AB,
# A -> eps | b, B -> aa | ba. sigma(A) must be a SET OF SETS: the single
# context L = {aa, ba} (from FIRST_2(B)), not two separate singleton contexts
# {aa} and {ba}. Tested per-singleton, A -> eps and A -> b never conflict
# (eps~aa=aa vs b~aa=ba; eps~ba=ba vs b~ba=bb) -- but tested against the
# whole set L at once, A -> eps gives {aa, ba} and A -> b gives {ba, bb},
# which DO intersect on "ba". So this grammar is NOT LL(2) (only SLL/LL(3)),
# and a test that flattens sigma into individual strings would wrongly
# accept it as LL(2).
GRAMMAR_SIGMA_SET_OF_SETS = {
    "nonterminals": ["S", "A", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["A", "B"]},
        {"lhs": "A", "rhs": []},
        {"lhs": "A", "rhs": ["b"]},
        {"lhs": "B", "rhs": ["a", "a"]},
        {"lhs": "B", "rhs": ["b", "a"]},
    ],
}

# Not LL(k) for ANY k, without left recursion: S -> aB | aC, B -> aB | eps,
# C -> aC | eps. B and C both generate exactly a* (the same language), so
# FIRST_k(aB) == FIRST_k(aC) for every k — a genuine, unbounded FIRST/FIRST
# conflict that no amount of extra lookahead resolves, and that persists
# under the local-follow-set test too (it doesn't depend on the follow
# context L at all). No left recursion anywhere, so find_min_ll_k has no
# certificate to offer — the result must be reported as inconclusive beyond
# max_k, not as a universal "not LL(k) for any k".
GRAMMAR_NO_LL_ANY_K = {
    "nonterminals": ["S", "B", "C"],
    "terminals": ["a"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "B"]},
        {"lhs": "S", "rhs": ["a", "C"]},
        {"lhs": "B", "rhs": ["a", "B"]},
        {"lhs": "B", "rhs": []},
        {"lhs": "C", "rhs": ["a", "C"]},
        {"lhs": "C", "rhs": []},
    ],
}


# ===========================================================================
# 1. check_ll_k — strong vs full LL(k) ([AU] example)
# ===========================================================================

class TestCheckLLKStrongVsFull:
    def test_k2_not_strong_but_ll(self):
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 2)
        assert result["is_strong_ll_k"] is False
        assert result["is_ll_k"] is True
        assert result["test_complete"] is True

    def test_k2_full_test_has_no_conflicts(self):
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 2)
        assert result["ll_conflicts"] == []

    def test_k2_parse_table_is_none(self):
        # Only strong LL(k) yields a single global parse table.
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 2)
        assert result["parse_table"] is None

    def test_k3_is_strong(self):
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 3)
        assert result["is_strong_ll_k"] is True
        assert result["is_ll_k"] is True
        assert result["parse_table"] is not None

    def test_k1_is_neither_strong_nor_ll(self):
        # At k=1 both A -> b and A -> eps are directed by lookahead "b"
        # regardless of local follow context — a genuine conflict.
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 1)
        assert result["is_strong_ll_k"] is False
        assert result["is_ll_k"] is False
        assert result["test_complete"] is True

    def test_k1_ll_conflicts_shape(self):
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 1)
        assert len(result["ll_conflicts"]) >= 1
        conflict = result["ll_conflicts"][0]
        assert set(conflict) == {"nonterminal", "local_follow", "lookahead", "competing_rules"}
        assert conflict["nonterminal"] == "A"
        assert conflict["local_follow"] == sorted(conflict["local_follow"])
        assert len(conflict["competing_rules"]) == 2


# ===========================================================================
# 1b. sigma(A) must be a set of SETS, not a flattened set of strings
# ===========================================================================

class TestSigmaIsSetOfSets:
    """S -> AB, A -> eps|b, B -> aa|ba: not LL(2) (only LL/SLL(3))."""

    def test_k2_is_not_ll(self):
        result = check_ll_k(GRAMMAR_SIGMA_SET_OF_SETS, 2)
        assert result["is_strong_ll_k"] is False
        assert result["is_ll_k"] is False
        assert result["test_complete"] is True

    def test_k2_conflict_local_follow_is_the_combined_set(self):
        result = check_ll_k(GRAMMAR_SIGMA_SET_OF_SETS, 2)
        assert len(result["ll_conflicts"]) >= 1
        conflict = result["ll_conflicts"][0]
        assert conflict["nonterminal"] == "A"
        # The conflict is only visible when {"aa", "ba"} is tested as one
        # context -- not as two singleton contexts {"aa"} and {"ba"}.
        assert ["aa", "ba"] in conflict["local_follow"]

    def test_k3_is_ll(self):
        result = check_ll_k(GRAMMAR_SIGMA_SET_OF_SETS, 3)
        assert result["is_ll_k"] is True

    def test_find_min_ll_k_is_3(self):
        result = find_min_ll_k(GRAMMAR_SIGMA_SET_OF_SETS, max_k=5)
        assert result["found"] is True
        assert result["k"] == 3


# ===========================================================================
# 2. LL(1) = strong LL(1)
# ===========================================================================

class TestLL1EqualsStrongLL1:
    """docs/THEORY.md §3.1: 'При k = 1 классы грамматик совпадают.'"""

    def test_ll1_grammar_matches(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        assert result["is_ll_k"] == result["is_strong_ll_k"] == True

    def test_au_grammar_at_k1_matches(self):
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 1)
        assert result["is_ll_k"] == result["is_strong_ll_k"] == False

    def test_no_ll_any_k_grammar_at_k1_matches(self):
        result = check_ll_k(GRAMMAR_NO_LL_ANY_K, 1)
        assert result["is_ll_k"] == result["is_strong_ll_k"] == False


# ===========================================================================
# 3. find_min_ll_k — [AU] example: k=2, strong_k=3
# ===========================================================================

class TestFindMinLLKStrongK:
    def test_finds_k2_and_strong_k3(self):
        result = find_min_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, max_k=10)
        assert result["found"] is True
        assert result["k"] == 2
        assert result["strong_k"] == 3
        assert result["certificate"] is None
        assert result["not_ll_any_k"] is False

    def test_simple_ll1_grammar_strong_k_equals_k(self):
        result = find_min_ll_k(GRAMMAR_LL1, max_k=5)
        assert result["found"] is True
        assert result["k"] == 1
        assert result["strong_k"] == 1


# ===========================================================================
# 4. Left recursion -> certificate, no k actually checked
# ===========================================================================

class TestLeftRecursionCertificate:
    def test_certificate_present(self):
        result = find_min_ll_k(GRAMMAR_LEFT_RECURSIVE, max_k=10)
        assert result["found"] is False
        assert result["k"] is None
        assert result["not_ll_any_k"] is True
        assert result["certificate"] is not None
        assert result["certificate"]["type"] == "left_recursion"

    def test_no_k_actually_checked(self):
        result = find_min_ll_k(GRAMMAR_LEFT_RECURSIVE, max_k=10)
        assert result["max_k_checked"] == 0
        assert result["result_for_k"] == {}

    def test_timing_regression_under_one_second(self):
        """docs/THEORY.md checklist: 'find_min_ll_k на левой рекурсии < 1 с.'

        The left-recursion pre-check must short-circuit before the (possibly
        exponential) k-loop, so this must be near-instant even with a large
        max_k.
        """
        start = time.perf_counter()
        result = find_min_ll_k(GRAMMAR_LEFT_RECURSIVE, max_k=10)
        elapsed = time.perf_counter() - start
        assert result["found"] is False
        assert elapsed < 1.0, f"find_min_ll_k took {elapsed:.3f}s on left recursion, expected < 1s"


class TestUnreachableLeftRecursionIsNotACertificate:
    """An unreachable nonterminal's left recursion must not taint the verdict."""

    def test_finds_ll1_despite_unreachable_left_recursive_nonterminal(self):
        result = find_min_ll_k(GRAMMAR_UNREACHABLE_LEFT_RECURSION, max_k=5)
        assert result["found"] is True
        assert result["k"] == 1
        assert result["not_ll_any_k"] is False
        assert result["certificate"] is None


# ===========================================================================
# 5. No certificate available -> inconclusive beyond max_k_checked
# ===========================================================================

class TestNoCertificateInconclusive:
    """A grammar that is genuinely not LL(k) for any k (FIRST_k(aB) ==
    FIRST_k(aC) for every k), but has no left recursion for find_min_ll_k to
    certify: the result must say 'not found up to max_k_checked', not claim
    a universal certificate.
    """

    def test_not_found_and_no_certificate(self):
        result = find_min_ll_k(GRAMMAR_NO_LL_ANY_K, max_k=5)
        assert result["found"] is False
        assert result["k"] is None
        assert result["certificate"] is None
        assert result["not_ll_any_k"] is False

    def test_max_k_checked_equals_requested_max_k(self):
        result = find_min_ll_k(GRAMMAR_NO_LL_ANY_K, max_k=5)
        assert result["max_k_checked"] == 5

    def test_every_checked_k_has_conflicts(self):
        for k in range(1, 5):
            result = check_ll_k(GRAMMAR_NO_LL_ANY_K, k)
            assert result["is_ll_k"] is False, f"expected a conflict at k={k}"


# ===========================================================================
# 6. Budget: max_tables / time_budget_s -> is_ll_k=None, test_complete=False
# ===========================================================================

class TestBudgetTruncation:
    def test_max_tables_zero_gives_unknown(self):
        # is_strong_ll_k is False for this grammar at k=2 (see TestCheckLLKStrongVsFull),
        # so the full test would normally run — but with no table budget at all it
        # cannot even start, so the result must be "unknown", not a false "True".
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 2, max_tables=0)
        assert result["is_ll_k"] is None
        assert result["test_complete"] is False

    def test_budget_does_not_affect_strong_pass(self):
        # is_strong_ll_k short-circuits before the budget is ever consulted.
        result = check_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, 3, max_tables=0)
        assert result["is_strong_ll_k"] is True
        assert result["is_ll_k"] is True
        assert result["test_complete"] is True
