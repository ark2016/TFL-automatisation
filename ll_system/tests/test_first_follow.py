"""Tests for ll_system.lib.first_follow — FIRST_k, FOLLOW_k, NULLABLE."""
from __future__ import annotations

import pytest

from ll_system.lib.first_follow import (
    compute_all,
    compute_first_k,
    compute_first_k_seq,
    compute_follow_k,
    compute_nullable,
    director_set,
    k_concat,
)
from ll_system.lib.ll_table_builder import build_parse_table, check_ll_k, find_min_ll_k

# ---------------------------------------------------------------------------
# Grammar fixtures
# ---------------------------------------------------------------------------

# Simple LL(1): S → aAb | bBa,  A → aA | ε,  B → bB | ε
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

# Grammar with epsilon-nullable nonterminals: S → AB, A → a | ε, B → b | ε
GRAMMAR_NULLABLE = {
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

# Classic LL(1) arithmetic: E → T E',  E' → + T E' | ε,
#                           T → F T',  T' → * F T' | ε,  F → ( E ) | i
GRAMMAR_ARITH = {
    "nonterminals": ["E", "Ep", "T", "Tp", "F"],
    "terminals": ["(", ")", "+", "*", "i"],
    "start": "E",
    "rules": [
        {"lhs": "E", "rhs": ["T", "Ep"]},
        {"lhs": "Ep", "rhs": ["+", "T", "Ep"]},
        {"lhs": "Ep", "rhs": []},
        {"lhs": "T", "rhs": ["F", "Tp"]},
        {"lhs": "Tp", "rhs": ["*", "F", "Tp"]},
        {"lhs": "Tp", "rhs": []},
        {"lhs": "F", "rhs": ["(", "E", ")"]},
        {"lhs": "F", "rhs": ["i"]},
    ],
}

# Not LL(1) but LL(2): S → aB | aC,  B → b,  C → c
# At k=1: both S-rules have director set {a} → conflict.
# At k=2: S→aB has director set {ab}, S→aC has director set {ac} → no conflict.
GRAMMAR_LL2 = {
    "nonterminals": ["S", "B", "C"],
    "terminals": ["a", "b", "c"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "B"]},
        {"lhs": "S", "rhs": ["a", "C"]},
        {"lhs": "B", "rhs": ["b"]},
        {"lhs": "C", "rhs": ["c"]},
    ],
}

# Grammar using explicit "ε" symbol instead of empty list
GRAMMAR_EPS_SYMBOL = {
    "nonterminals": ["S", "A"],
    "terminals": ["a"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["A"]},
        {"lhs": "A", "rhs": ["a"]},
        {"lhs": "A", "rhs": ["ε"]},
    ],
}


# ===========================================================================
# 1. compute_nullable
# ===========================================================================

class TestComputeNullable:
    def test_grammar_ll1_only_A_and_B_nullable(self):
        nullable = compute_nullable(GRAMMAR_LL1)
        assert "A" in nullable
        assert "B" in nullable
        assert "S" not in nullable

    def test_grammar_nullable_A_and_B_nullable(self):
        nullable = compute_nullable(GRAMMAR_NULLABLE)
        assert "A" in nullable
        assert "B" in nullable

    def test_grammar_nullable_S_is_nullable(self):
        # S → AB, A →* ε, B →* ε  ⟹  S →* ε
        nullable = compute_nullable(GRAMMAR_NULLABLE)
        assert "S" in nullable

    def test_no_nullable_without_epsilon(self):
        grammar = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        }
        nullable = compute_nullable(grammar)
        assert "S" not in nullable

    def test_explicit_epsilon_symbol(self):
        nullable = compute_nullable(GRAMMAR_EPS_SYMBOL)
        assert "A" in nullable

    def test_chain_nullable(self):
        # A → B,  B → C,  C → ε  ⟹  A, B, C all nullable
        grammar = {
            "nonterminals": ["A", "B", "C"],
            "terminals": [],
            "start": "A",
            "rules": [
                {"lhs": "A", "rhs": ["B"]},
                {"lhs": "B", "rhs": ["C"]},
                {"lhs": "C", "rhs": []},
            ],
        }
        nullable = compute_nullable(grammar)
        assert nullable == {"A", "B", "C"}


# ===========================================================================
# 2. k_concat
# ===========================================================================

class TestKConcat:
    def test_simple_concat_k1(self):
        result = k_concat({"a", ""}, {"b"}, 1)
        assert result == {"a", "b"}

    def test_simple_concat_k2(self):
        result = k_concat({"ab", "a"}, {"c", "d"}, 2)
        # "ab" already length 2 → stays "ab"
        # "a" + "c" → "ac", "a" + "d" → "ad"
        assert result == {"ab", "ac", "ad"}

    def test_empty_x(self):
        assert k_concat(set(), {"a"}, 1) == set()

    def test_empty_y(self):
        assert k_concat({"a"}, set(), 1) == set()

    def test_both_empty(self):
        assert k_concat(set(), set(), 1) == set()

    def test_k0(self):
        result = k_concat({"abc"}, {"def"}, 0)
        assert result == {""}

    def test_epsilon_in_x(self):
        # "" + "ab" → "ab"[:2] = "ab"
        result = k_concat({""}, {"ab"}, 2)
        assert result == {"ab"}

    def test_truncation(self):
        result = k_concat({"abc"}, {"def"}, 2)
        assert result == {"ab"}

    def test_epsilon_propagation_k1(self):
        # {"", "a"} ⊕_1 {"b"} = {"b", "a"}
        result = k_concat({"", "a"}, {"b"}, 1)
        assert result == {"b", "a"}


# ===========================================================================
# 3. compute_first_k — k=1
# ===========================================================================

class TestFirstK1:
    def setup_method(self):
        self.first1 = compute_first_k(GRAMMAR_LL1, 1)

    def test_first1_S(self):
        assert self.first1["S"] == {"a", "b"}

    def test_first1_A_contains_epsilon(self):
        assert "" in self.first1["A"]

    def test_first1_A_contains_a(self):
        assert "a" in self.first1["A"]

    def test_first1_B_contains_epsilon(self):
        assert "" in self.first1["B"]

    def test_first1_B_contains_b(self):
        assert "b" in self.first1["B"]

    def test_first1_arith_E(self):
        first1 = compute_first_k(GRAMMAR_ARITH, 1)
        # E → T Ep → F Tp Ep; F → (E) | i
        assert first1["E"] == {"(", "i"}

    def test_first1_arith_Ep(self):
        first1 = compute_first_k(GRAMMAR_ARITH, 1)
        # Ep → + T Ep | ε
        assert first1["Ep"] == {"+", ""}

    def test_first1_arith_T(self):
        first1 = compute_first_k(GRAMMAR_ARITH, 1)
        assert first1["T"] == {"(", "i"}

    def test_first1_nullable_grammar_S(self):
        first1 = compute_first_k(GRAMMAR_NULLABLE, 1)
        # S → AB; A can be ε so first(S) includes first(B)={"b",""}; A → a so {"a"}
        assert "a" in first1["S"]
        assert "b" in first1["S"]
        assert "" in first1["S"]


# ===========================================================================
# 4. compute_first_k — k=2
# ===========================================================================

class TestFirstK2:
    def test_first2_ll2_grammar(self):
        first2 = compute_first_k(GRAMMAR_LL2, 2)
        # S → aB | aC; B→b, C→c  ⟹  FIRST_2(S) = {"ab", "ac"}
        assert first2["S"] == {"ab", "ac"}

    def test_first2_arith_E(self):
        first2 = compute_first_k(GRAMMAR_ARITH, 2)
        # FIRST_2(F) = {"(E"[:2]→"(E"? no — "(E" means the terminal ( then E
        # Actually FIRST_2(F) = {"((" , "(i", "i)"[:2]→ just "i" padded by follow?
        # F → (E): FIRST_2 starts with "(", then FIRST_1(E) = {i,(} → {"(i","(("}
        # F → i: FIRST_2 = {"i"}
        # So FIRST_2(F) ⊇ {"(i", "((", "i"}
        assert "(i" in first2["F"] or "i" in first2["F"]

    def test_first2_explicit_eps(self):
        first2 = compute_first_k(GRAMMAR_EPS_SYMBOL, 2)
        assert "a" in first2["A"]
        assert "" in first2["A"]


# ===========================================================================
# 5. compute_first_k_seq
# ===========================================================================

class TestFirstKSeq:
    def setup_method(self):
        self.nullable = compute_nullable(GRAMMAR_LL1)
        self.first1 = compute_first_k(GRAMMAR_LL1, 1)

    def test_seq_empty(self):
        result = compute_first_k_seq([], GRAMMAR_LL1, 1, self.first1, self.nullable)
        assert result == {""}

    def test_seq_single_terminal(self):
        result = compute_first_k_seq(["a"], GRAMMAR_LL1, 1, self.first1, self.nullable)
        assert result == {"a"}

    def test_seq_single_nonterminal_nullable(self):
        result = compute_first_k_seq(["A"], GRAMMAR_LL1, 1, self.first1, self.nullable)
        # FIRST_1(A) = {"a", ""}
        assert result == {"a", ""}

    def test_seq_terminal_then_nonterminal(self):
        # ["a", "A"]: first terminal 'a' already fills k=1 slot
        result = compute_first_k_seq(["a", "A"], GRAMMAR_LL1, 1, self.first1, self.nullable)
        assert result == {"a"}

    def test_seq_nullable_then_terminal(self):
        # ["A", "b"]: A can derive ε, so 'b' is reachable
        result = compute_first_k_seq(["A", "b"], GRAMMAR_LL1, 1, self.first1, self.nullable)
        # FIRST_1(A) = {"a", ""}, then ⊕_1 {"b"} = {"a", "b"}
        assert result == {"a", "b"}

    def test_seq_k2_two_terminals(self):
        nullable = compute_nullable(GRAMMAR_LL2)
        first2 = compute_first_k(GRAMMAR_LL2, 2)
        # ["a", "B"] in GRAMMAR_LL2: FIRST_2("a" B) = "a" ⊕_2 FIRST_1(B)={"b"} = {"ab"}
        result = compute_first_k_seq(["a", "B"], GRAMMAR_LL2, 2, first2, nullable)
        assert result == {"ab"}


# ===========================================================================
# 6. compute_follow_k — k=1
# ===========================================================================

class TestFollowK1:
    def setup_method(self):
        self.nullable = compute_nullable(GRAMMAR_LL1)
        self.first1 = compute_first_k(GRAMMAR_LL1, 1)
        self.follow1 = compute_follow_k(GRAMMAR_LL1, 1, self.first1, self.nullable)

    def test_follow1_S_contains_eos(self):
        # FOLLOW_1(S) = {"$"}
        assert "$" in self.follow1["S"]

    def test_follow1_A_contains_b(self):
        # S → a A b: A is followed by 'b'
        assert "b" in self.follow1["A"]

    def test_follow1_A_does_not_contain_a(self):
        # Nothing puts 'a' in FOLLOW(A)
        assert "a" not in self.follow1["A"]

    def test_follow1_B_contains_a(self):
        # S → b B a: B is followed by 'a'
        assert "a" in self.follow1["B"]

    def test_follow1_arith(self):
        nullable = compute_nullable(GRAMMAR_ARITH)
        first1 = compute_first_k(GRAMMAR_ARITH, 1)
        follow1 = compute_follow_k(GRAMMAR_ARITH, 1, first1, nullable)
        # FOLLOW(Ep) should include FOLLOW(E) = {"$", ")"}
        assert "$" in follow1["Ep"]
        assert ")" in follow1["Ep"]
        # FOLLOW(E) includes ")" (from F → (E)) and "$"
        assert ")" in follow1["E"]
        assert "$" in follow1["E"]


# ===========================================================================
# 7. compute_follow_k — k=2
# ===========================================================================

class TestFollowK2:
    def test_follow2_S_contains_double_eos(self):
        nullable = compute_nullable(GRAMMAR_LL1)
        first2 = compute_first_k(GRAMMAR_LL1, 2)
        follow2 = compute_follow_k(GRAMMAR_LL1, 2, first2, nullable)
        assert "$$" in follow2["S"]

    def test_follow2_A_for_ll1(self):
        nullable = compute_nullable(GRAMMAR_LL1)
        first2 = compute_first_k(GRAMMAR_LL1, 2)
        follow2 = compute_follow_k(GRAMMAR_LL1, 2, first2, nullable)
        # A is followed by 'b', then FOLLOW_2(S) = {"$$"}
        # b + "$" → "b$"
        assert any("b" in s for s in follow2["A"])


# ===========================================================================
# 8. director_set
# ===========================================================================

class TestDirectorSet:
    def setup_method(self):
        self.nullable, self.first1, self.follow1 = compute_all(GRAMMAR_LL1, 1)

    def test_director_S_to_aAb(self):
        ds = director_set(
            GRAMMAR_LL1, "S", ["a", "A", "b"], 1,
            self.first1, self.follow1, self.nullable
        )
        assert "a" in ds

    def test_director_S_to_bBa(self):
        ds = director_set(
            GRAMMAR_LL1, "S", ["b", "B", "a"], 1,
            self.first1, self.follow1, self.nullable
        )
        assert "b" in ds

    def test_director_A_epsilon(self):
        ds = director_set(
            GRAMMAR_LL1, "A", [], 1,
            self.first1, self.follow1, self.nullable
        )
        # FIRST_1(ε) = {""} ⊕_1 FOLLOW_1(A) = {"b"} → {"b"}
        assert "b" in ds

    def test_director_disjoint_for_ll1(self):
        ds_aAb = director_set(
            GRAMMAR_LL1, "S", ["a", "A", "b"], 1,
            self.first1, self.follow1, self.nullable
        )
        ds_bBa = director_set(
            GRAMMAR_LL1, "S", ["b", "B", "a"], 1,
            self.first1, self.follow1, self.nullable
        )
        assert ds_aAb.isdisjoint(ds_bBa)


# ===========================================================================
# 9. check_ll_k
# ===========================================================================

class TestCheckLLK:
    def test_grammar_ll1_is_ll1(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        assert result["is_ll_k"] is True
        assert result["conflicts"] == []
        assert result["k"] == 1

    def test_grammar_ll1_parse_table_not_none(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        assert result["parse_table"] is not None

    def test_grammar_ll2_is_not_ll1(self):
        result = check_ll_k(GRAMMAR_LL2, 1)
        assert result["is_ll_k"] is False
        assert len(result["conflicts"]) > 0

    def test_grammar_ll2_is_ll2(self):
        result = check_ll_k(GRAMMAR_LL2, 2)
        assert result["is_ll_k"] is True

    def test_grammar_arith_is_ll1(self):
        result = check_ll_k(GRAMMAR_ARITH, 1)
        assert result["is_ll_k"] is True

    def test_first_sets_sorted(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        for nt, lst in result["first_sets"].items():
            assert lst == sorted(lst), f"FIRST_1({nt}) not sorted"

    def test_follow_sets_sorted(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        for nt, lst in result["follow_sets"].items():
            assert lst == sorted(lst), f"FOLLOW_1({nt}) not sorted"


# ===========================================================================
# 10. find_min_ll_k
# ===========================================================================

class TestFindMinLLK:
    def test_ll1_grammar_min_k_is_1(self):
        result = find_min_ll_k(GRAMMAR_LL1)
        assert result["found"] is True
        assert result["k"] == 1

    def test_ll2_grammar_min_k_is_2(self):
        result = find_min_ll_k(GRAMMAR_LL2)
        assert result["found"] is True
        assert result["k"] == 2

    def test_arith_min_k_is_1(self):
        result = find_min_ll_k(GRAMMAR_ARITH)
        assert result["found"] is True
        assert result["k"] == 1

    def test_not_found_returns_found_false(self):
        # Left-recursive grammar: S → S + S | a
        # Left recursion makes a grammar non-LL(k) for any finite k because
        # the parser would loop forever trying to expand S.
        # With max_k=4 none of the k values should be conflict-free.
        grammar_lr = {
            "nonterminals": ["S"],
            "terminals": ["a", "+"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["S", "+", "S"]},
                {"lhs": "S", "rhs": ["a"]},
            ],
        }
        result = find_min_ll_k(grammar_lr, max_k=4)
        assert result["found"] is False
        assert result["k"] is None
        assert result["max_k_checked"] == 4


# ===========================================================================
# 11. build_parse_table
# ===========================================================================

class TestBuildParseTable:
    def test_ll1_table_no_conflicts(self):
        nullable, first1, follow1 = compute_all(GRAMMAR_LL1, 1)
        table, conflicts = build_parse_table(GRAMMAR_LL1, 1, nullable, first1, follow1)
        assert conflicts == []

    def test_ll1_table_has_S_entries(self):
        nullable, first1, follow1 = compute_all(GRAMMAR_LL1, 1)
        table, _ = build_parse_table(GRAMMAR_LL1, 1, nullable, first1, follow1)
        # table["S"]["a"] should map to rhs ["a", "A", "b"]
        assert table["S"]["a"] == ["a", "A", "b"]
        assert table["S"]["b"] == ["b", "B", "a"]

    def test_ll2_conflict_at_k1(self):
        nullable, first1, follow1 = compute_all(GRAMMAR_LL2, 1)
        _, conflicts = build_parse_table(GRAMMAR_LL2, 1, nullable, first1, follow1)
        assert len(conflicts) > 0

    def test_ll2_no_conflict_at_k2(self):
        nullable, first2, follow2 = compute_all(GRAMMAR_LL2, 2)
        _, conflicts = build_parse_table(GRAMMAR_LL2, 2, nullable, first2, follow2)
        assert conflicts == []
