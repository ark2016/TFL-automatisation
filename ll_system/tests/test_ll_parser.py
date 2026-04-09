"""Tests for ll_system.lib.ll_parser and ll_system.lib.ll_table_builder."""
from __future__ import annotations

import pytest

from ll_system.lib.ll_parser import LLParser, parse_word
from ll_system.lib.ll_table_builder import check_ll_k

# ---------------------------------------------------------------------------
# Grammar fixtures
# ---------------------------------------------------------------------------

# LL(1): S → aAb | bBa,  A → aA | ε,  B → bB | ε
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


# ===========================================================================
# LLParser — construction
# ===========================================================================

class TestLLParserConstruction:
    def test_construct_ll1(self):
        parser = LLParser(GRAMMAR_LL1, 1)
        assert parser is not None

    def test_construct_arith(self):
        parser = LLParser(GRAMMAR_ARITH, 1)
        assert parser is not None

    def test_construct_ll2_at_k2(self):
        parser = LLParser(GRAMMAR_LL2, 2)
        assert parser is not None

    def test_raises_for_not_ll1(self):
        with pytest.raises(ValueError):
            LLParser(GRAMMAR_LL2, 1)

    def test_from_table(self):
        result = check_ll_k(GRAMMAR_LL1, 1)
        parser = LLParser.from_table(GRAMMAR_LL1, result["parse_table"], 1)
        assert parser is not None


# ===========================================================================
# LLParser — accepted words for GRAMMAR_LL1
# Language: { a^n b^n | n≥1 } ∪ { b^n a^n | n≥1 }
# S → aAb:  A=ε → "ab"; A→aA=a → "aab"; etc.
# S → bBa:  B=ε → "ba"; B→bB=b → "bba"; etc.
# ===========================================================================

class TestLLParserAcceptedWords:
    def setup_method(self):
        self.parser = LLParser(GRAMMAR_LL1, 1)

    def test_accept_ab(self):
        assert self.parser.parse("ab")["accepted"] is True

    def test_accept_ba(self):
        assert self.parser.parse("ba")["accepted"] is True

    def test_accept_aab(self):
        assert self.parser.parse("aab")["accepted"] is True

    def test_accept_aaab(self):
        assert self.parser.parse("aaab")["accepted"] is True

    def test_accept_bba(self):
        assert self.parser.parse("bba")["accepted"] is True

    def test_accept_bbba(self):
        assert self.parser.parse("bbba")["accepted"] is True

    def test_accept_aabb(self):
        # aabb — is this accepted? S → aAb.  A must derive "ab" but A only derives a^n.
        # So "aabb" is NOT in the language.
        assert self.parser.parse("aabb")["accepted"] is False

    def test_reject_empty(self):
        assert self.parser.parse("")["accepted"] is False

    def test_reject_a_only(self):
        assert self.parser.parse("a")["accepted"] is False

    def test_reject_b_only(self):
        assert self.parser.parse("b")["accepted"] is False

    def test_reject_aa(self):
        assert self.parser.parse("aa")["accepted"] is False

    def test_reject_bb(self):
        assert self.parser.parse("bb")["accepted"] is False

    def test_reject_aba(self):
        assert self.parser.parse("aba")["accepted"] is False


# ===========================================================================
# LLParser — parse result structure
# ===========================================================================

class TestParseResultStructure:
    def setup_method(self):
        self.parser = LLParser(GRAMMAR_LL1, 1)

    def test_result_has_accepted(self):
        r = self.parser.parse("ab")
        assert "accepted" in r

    def test_result_has_word(self):
        r = self.parser.parse("ab")
        assert r["word"] == "ab"

    def test_result_has_steps(self):
        r = self.parser.parse("ab")
        assert "steps" in r
        assert isinstance(r["steps"], list)

    def test_result_has_error_none_on_accept(self):
        r = self.parser.parse("ab")
        assert r["error"] is None

    def test_result_has_error_on_reject(self):
        r = self.parser.parse("aa")
        assert r["error"] is not None
        assert isinstance(r["error"], str)


# ===========================================================================
# LLParser — arithmetic grammar
# ===========================================================================

class TestArithParser:
    def setup_method(self):
        self.parser = LLParser(GRAMMAR_ARITH, 1)

    def test_accept_single_i(self):
        assert self.parser.parse("i")["accepted"] is True

    def test_accept_i_plus_i(self):
        assert self.parser.parse("i+i")["accepted"] is True

    def test_accept_i_plus_i_times_i(self):
        assert self.parser.parse("i+i*i")["accepted"] is True

    def test_accept_i_times_i(self):
        assert self.parser.parse("i*i")["accepted"] is True

    def test_accept_paren_i(self):
        assert self.parser.parse("(i)")["accepted"] is True

    def test_reject_i_plus(self):
        assert self.parser.parse("i+")["accepted"] is False

    def test_reject_plus_i(self):
        assert self.parser.parse("+i")["accepted"] is False

    def test_reject_empty(self):
        assert self.parser.parse("")["accepted"] is False


# ===========================================================================
# parse_word convenience function
# ===========================================================================

class TestParseWord:
    def test_parse_word_accepted(self):
        r = parse_word(GRAMMAR_LL1, 1, "ab")
        assert r["accepted"] is True

    def test_parse_word_rejected(self):
        r = parse_word(GRAMMAR_LL1, 1, "aa")
        assert r["accepted"] is False

    def test_parse_word_not_ll_k(self):
        # GRAMMAR_LL2 is not LL(1)
        r = parse_word(GRAMMAR_LL2, 1, "ab")
        assert r["accepted"] is False
        assert "not LL" in r["error"]

    def test_parse_word_ll2_accepted(self):
        # S → aB | aC, B → b — "ab" is accepted at k=2
        r = parse_word(GRAMMAR_LL2, 2, "ab")
        assert r["accepted"] is True

    def test_parse_word_ll2_rejected(self):
        # "aa" is not in the language of GRAMMAR_LL2
        r = parse_word(GRAMMAR_LL2, 2, "aa")
        assert r["accepted"] is False
