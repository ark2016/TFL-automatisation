"""Tests for CYK parser (cfl_system.lib.cyk)."""

import pytest
from cfl_system.lib.cnf import to_cnf
from cfl_system.lib.cyk import cyk_parse, cyk_parse_table


# ---- Fixture grammars (already in CNF or to be converted) ----

def _anbn_grammar():
    """S -> aSb | epsilon  (a^n b^n)."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    }


def _simple_cnf():
    """S -> AB | a, A -> a, B -> b.  L = {ab, a}."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S", "A", "B"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["A", "B"]},
            {"lhs": "S", "rhs": ["a"]},
            {"lhs": "A", "rhs": ["a"]},
            {"lhs": "B", "rhs": ["b"]},
        ],
    }


def _single_char_cnf():
    """S -> a."""
    return {
        "terminals": ["a"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a"]},
        ],
    }


def _nullable_start():
    """S -> epsilon. Language = {epsilon}."""
    return {
        "terminals": [],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": []},
        ],
    }


def _non_nullable_start():
    """S -> a. Language = {a}, no epsilon."""
    return {
        "terminals": ["a"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a"]},
        ],
    }


def _ambiguous_grammar():
    """S -> SS | a.  Ambiguous: 'aa' can be parsed as S(S(a), S(a))."""
    return {
        "terminals": ["a"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["S", "S"]},
            {"lhs": "S", "rhs": ["a"]},
        ],
    }


# ---- Tests ----

class TestCykParse:
    def test_anbn_accepts_aabb(self):
        cnf = to_cnf(_anbn_grammar())
        assert cyk_parse(cnf, "aabb") is True

    def test_anbn_rejects_aab(self):
        cnf = to_cnf(_anbn_grammar())
        assert cyk_parse(cnf, "aab") is False

    def test_empty_word_nullable_start(self):
        cnf = to_cnf(_nullable_start())
        assert cyk_parse(cnf, "") is True

    def test_empty_word_non_nullable_start(self):
        g = _non_nullable_start()
        assert cyk_parse(g, "") is False

    def test_single_character(self):
        g = _single_char_cnf()
        assert cyk_parse(g, "a") is True
        assert cyk_parse(g, "b") is False

    def test_longer_words_anbn(self):
        cnf = to_cnf(_anbn_grammar())
        assert cyk_parse(cnf, "aaabbb") is True
        assert cyk_parse(cnf, "aaaabbbb") is True
        assert cyk_parse(cnf, "aaabb") is False

    def test_ambiguous_grammar(self):
        g = _ambiguous_grammar()
        # Already in CNF
        assert cyk_parse(g, "a") is True
        assert cyk_parse(g, "aa") is True
        assert cyk_parse(g, "aaa") is True
        assert cyk_parse(g, "") is False

    def test_word_not_in_language(self):
        g = _simple_cnf()
        assert cyk_parse(g, "ba") is False
        assert cyk_parse(g, "bb") is False
        assert cyk_parse(g, "aab") is False

    def test_integration_to_cnf_then_cyk(self):
        """End-to-end: raw grammar -> to_cnf -> cyk_parse."""
        raw = {
            "terminals": ["a", "b"],
            "nonterminals": ["S", "A"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["A", "S", "A"]},
                {"lhs": "S", "rhs": ["a", "b"]},
                {"lhs": "A", "rhs": ["S"]},
                {"lhs": "A", "rhs": ["a"]},
            ],
        }
        cnf = to_cnf(raw)
        assert cyk_parse(cnf, "ab") is True
        assert cyk_parse(cnf, "aabab") is True
        assert cyk_parse(cnf, "b") is False


class TestCykParseTable:
    def test_table_dimensions(self):
        g = _simple_cnf()
        table = cyk_parse_table(g, "ab")
        assert len(table) == 2
        assert len(table[0]) == 2
        assert len(table[1]) == 2

    def test_table_base_case(self):
        g = _simple_cnf()
        table = cyk_parse_table(g, "ab")
        # table[0][0] should contain nonterminals deriving 'a'
        assert "A" in table[0][0]
        assert "S" in table[0][0]  # S -> a
        # table[1][0] should contain nonterminals deriving 'b'
        assert "B" in table[1][0]

    def test_empty_word_returns_empty(self):
        g = _simple_cnf()
        table = cyk_parse_table(g, "")
        assert table == []

    def test_table_full_parse(self):
        g = _simple_cnf()
        table = cyk_parse_table(g, "ab")
        # table[0][1] = nonterminals deriving "ab" — should contain S
        assert "S" in table[0][1]
