"""Tests for CNF conversion (cfl_system.lib.cnf)."""

import pytest
from cfl_system.lib.cnf import to_cnf, is_cnf


# ---- Fixture grammars ----

def _anbn_grammar():
    """S -> aSb | epsilon  (language a^n b^n)."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    }


def _already_cnf():
    """S -> AB | a, A -> a, B -> b."""
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


def _unit_rule_grammar():
    """S -> A, A -> a.  Unit rule S -> A should be eliminated."""
    return {
        "terminals": ["a"],
        "nonterminals": ["S", "A"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["A"]},
            {"lhs": "A", "rhs": ["a"]},
        ],
    }


def _long_rule_grammar():
    """S -> ABCD, A -> a, B -> b, C -> a, D -> b."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S", "A", "B", "C", "D"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["A", "B", "C", "D"]},
            {"lhs": "A", "rhs": ["a"]},
            {"lhs": "B", "rhs": ["b"]},
            {"lhs": "C", "rhs": ["a"]},
            {"lhs": "D", "rhs": ["b"]},
        ],
    }


def _mixed_grammar():
    """S -> aB, B -> b.  Mixed terminal/nonterminal in S -> aB."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S", "B"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "B"]},
            {"lhs": "B", "rhs": ["b"]},
        ],
    }


def _epsilon_only():
    """S -> epsilon.  Language = {epsilon}."""
    return {
        "terminals": [],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": []},
        ],
    }


def _unreachable_grammar():
    """S -> a, X -> b.  X is unreachable."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S", "X"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a"]},
            {"lhs": "X", "rhs": ["b"]},
        ],
    }


# ---- Tests ----

class TestToCnf:
    def test_anbn_is_cnf(self):
        cnf = to_cnf(_anbn_grammar())
        assert is_cnf(cnf), f"Result is not in CNF: {cnf['rules']}"

    def test_already_cnf_preserved(self):
        g = _already_cnf()
        cnf = to_cnf(g)
        assert is_cnf(cnf)
        # Should still have the same language (structural invariant only)
        # At minimum, must have rules for S, A, B producing a, b
        rule_lhss = {r["lhs"] for r in cnf["rules"]}
        assert cnf["start"] in rule_lhss

    def test_unit_rules_eliminated(self):
        cnf = to_cnf(_unit_rule_grammar())
        assert is_cnf(cnf)
        # No unit rules in result
        nonterms = set(cnf["nonterminals"])
        for rule in cnf["rules"]:
            rhs = rule["rhs"]
            if len(rhs) == 1:
                assert rhs[0] not in nonterms, f"Unit rule remains: {rule}"

    def test_long_rules_binarised(self):
        cnf = to_cnf(_long_rule_grammar())
        assert is_cnf(cnf)
        for rule in cnf["rules"]:
            assert len(rule["rhs"]) <= 2

    def test_mixed_terminals_wrapped(self):
        cnf = to_cnf(_mixed_grammar())
        assert is_cnf(cnf)
        # S should produce two nonterminals now (not a terminal + nonterminal)
        terms = set(cnf["terminals"])
        for rule in cnf["rules"]:
            if len(rule["rhs"]) == 2:
                for s in rule["rhs"]:
                    assert s not in terms, f"Terminal in binary rule: {rule}"

    def test_epsilon_only_grammar(self):
        cnf = to_cnf(_epsilon_only())
        assert is_cnf(cnf)
        # Must have exactly S -> epsilon
        eps_rules = [r for r in cnf["rules"] if len(r["rhs"]) == 0]
        assert len(eps_rules) == 1
        assert eps_rules[0]["lhs"] == cnf["start"]

    def test_unreachable_removed(self):
        cnf = to_cnf(_unreachable_grammar())
        assert is_cnf(cnf)
        # X should not appear
        all_syms = set()
        for r in cnf["rules"]:
            all_syms.add(r["lhs"])
            all_syms.update(r["rhs"])
        assert "X" not in all_syms

    def test_invalid_grammar_raises(self):
        with pytest.raises(ValueError):
            to_cnf({"terminals": [], "nonterminals": [], "start": "S", "rules": []})


class TestIsCnf:
    def test_true_for_cnf(self):
        assert is_cnf(_already_cnf()) is True

    def test_false_for_long_rule(self):
        assert is_cnf(_long_rule_grammar()) is False

    def test_false_for_unit_rule(self):
        assert is_cnf(_unit_rule_grammar()) is False

    def test_false_for_mixed(self):
        assert is_cnf(_mixed_grammar()) is False

    def test_true_for_epsilon_start(self):
        assert is_cnf(_epsilon_only()) is True
