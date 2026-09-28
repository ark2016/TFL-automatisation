"""Shared utilities for ll_system."""

from __future__ import annotations


def normalize_grammar(grammar: dict) -> dict:
    """Normalize grammar rules: replace ["ε"] rhs with [] (empty list).

    Returns a new grammar dict (does not mutate input).
    """
    new_rules = []
    for rule in grammar.get("rules", []):
        rhs = rule.get("rhs", [])
        if rhs == ["ε"]:
            rhs = []
        new_rules.append({**rule, "rhs": list(rhs)})
    return {**grammar, "rules": new_rules}


def grammar_nonterminals(grammar: dict) -> set[str]:
    """Return set of nonterminals."""
    return set(grammar.get("nonterminals", []))


def grammar_terminals(grammar: dict) -> set[str]:
    """Return set of terminals."""
    return set(grammar.get("terminals", []))


def rules_for(grammar: dict, nonterminal: str) -> list[list[str]]:
    """Return list of RHS alternatives for the given nonterminal.

    Each RHS is a list of symbols ([] means epsilon).
    """
    result: list[list[str]] = []
    for rule in grammar.get("rules", []):
        if rule.get("lhs") == nonterminal:
            rhs = rule.get("rhs", [])
            # Normalize ["ε"] to [] on the fly
            if rhs == ["ε"]:
                rhs = []
            result.append(list(rhs))
    return result


def is_epsilon_rhs(rhs: list[str]) -> bool:
    """Return True if rhs represents epsilon ([] or ["ε"])."""
    return rhs == [] or rhs == ["ε"]


# Symbols reserved by the LL(k) machinery itself: '$' marks end-of-input in
# FIRST/FOLLOW_k and the parse table, 'ε' marks the empty production. Neither
# may be declared as an actual terminal or nonterminal — that would silently
# collide with the oracle's own bookkeeping.
RESERVED_SYMBOLS = frozenset({"$", "ε"})


def validate_grammar_symbols(grammar: dict) -> list[str]:
    """Check grammar consistency: start in nonterminals, no symbol overlap,
    no use of the reserved symbols ('$', 'ε') as declared terminals/nonterminals.

    Returns list of error strings (empty = OK).
    """
    errors: list[str] = []
    nonterminals = set(grammar.get("nonterminals", []))
    terminals = set(grammar.get("terminals", []))
    start = grammar.get("start")

    if start and start not in nonterminals:
        errors.append(
            f"start symbol '{start}' is not in nonterminals {sorted(nonterminals)}"
        )

    overlap = nonterminals & terminals
    if overlap:
        errors.append(
            f"symbols appear in both nonterminals and terminals: {sorted(overlap)}"
        )

    reserved_used = (nonterminals | terminals) & RESERVED_SYMBOLS
    if reserved_used:
        errors.append(
            f"symbols {sorted(reserved_used)} are reserved (end-of-input marker "
            "'$' and epsilon 'ε') and cannot be declared as terminals or "
            "nonterminals"
        )

    return errors
