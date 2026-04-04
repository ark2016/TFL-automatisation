"""Chomsky Normal Form conversion for context-free grammars.

Public API:
    to_cnf(grammar)  -- convert a CFG dict to CNF
    is_cnf(grammar)  -- check whether a grammar dict is already in CNF
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(grammar: dict) -> None:
    for key in ("terminals", "nonterminals", "start", "rules"):
        if key not in grammar:
            raise ValueError(f"Grammar missing required key: {key!r}")
    if grammar["start"] not in grammar["nonterminals"]:
        raise ValueError("Start symbol not in nonterminals")
    terms = set(grammar["terminals"])
    nonterms = set(grammar["nonterminals"])
    if terms & nonterms:
        raise ValueError("Terminals and nonterminals overlap")
    for rule in grammar["rules"]:
        if rule["lhs"] not in nonterms:
            raise ValueError(f"LHS {rule['lhs']!r} not in nonterminals")
        for sym in rule["rhs"]:
            if sym not in terms and sym not in nonterms:
                raise ValueError(f"Symbol {sym!r} not in terminals or nonterminals")


# ---------------------------------------------------------------------------
# Helper: fresh nonterminal names
# ---------------------------------------------------------------------------

class _NameGen:
    """Generate fresh nonterminal names that don't collide with existing ones."""

    def __init__(self, existing: set[str]) -> None:
        self._existing = set(existing)
        self._counters: dict[str, int] = defaultdict(int)

    def fresh(self, prefix: str) -> str:
        while True:
            self._counters[prefix] += 1
            name = f"{prefix}_{self._counters[prefix]}"
            if name not in self._existing:
                self._existing.add(name)
                return name


# ---------------------------------------------------------------------------
# Internal representation: rules as list[tuple[str, list[str]]]
# ---------------------------------------------------------------------------

def _rules_from_grammar(grammar: dict) -> list[tuple[str, list[str]]]:
    return [(r["lhs"], list(r["rhs"])) for r in grammar["rules"]]


def _rules_to_dicts(rules: list[tuple[str, list[str]]]) -> list[dict]:
    return [{"lhs": lhs, "rhs": rhs} for lhs, rhs in rules]


# ---------------------------------------------------------------------------
# Step 1: START — new start symbol if needed
# ---------------------------------------------------------------------------

def _step_start(
    rules: list[tuple[str, list[str]]],
    start: str,
    nonterminals: set[str],
    names: _NameGen,
) -> tuple[list[tuple[str, list[str]]], str, set[str]]:
    # Check if start appears on any RHS
    appears = any(start in rhs for _, rhs in rules)
    if not appears:
        return rules, start, nonterminals

    new_start = names.fresh("_S0")
    nonterminals = nonterminals | {new_start}
    rules = [(new_start, [start])] + rules
    return rules, new_start, nonterminals


# ---------------------------------------------------------------------------
# Step 2: TERM — replace terminals in mixed rules of length >= 2
# ---------------------------------------------------------------------------

def _step_term(
    rules: list[tuple[str, list[str]]],
    terminals: set[str],
    nonterminals: set[str],
    names: _NameGen,
) -> tuple[list[tuple[str, list[str]]], set[str]]:
    term_map: dict[str, str] = {}  # terminal -> wrapper nonterminal
    extra_rules: list[tuple[str, list[str]]] = []

    def get_wrapper(t: str) -> str:
        if t not in term_map:
            nt = names.fresh(f"_T_{t}")
            term_map[t] = nt
            nonterminals.add(nt)
            extra_rules.append((nt, [t]))
        return term_map[t]

    new_rules: list[tuple[str, list[str]]] = []
    for lhs, rhs in rules:
        if len(rhs) < 2:
            new_rules.append((lhs, rhs))
            continue
        new_rhs = []
        for sym in rhs:
            if sym in terminals:
                new_rhs.append(get_wrapper(sym))
            else:
                new_rhs.append(sym)
        new_rules.append((lhs, new_rhs))

    return new_rules + extra_rules, nonterminals


# ---------------------------------------------------------------------------
# Step 3: BIN — binarise rules with RHS > 2
# ---------------------------------------------------------------------------

def _step_bin(
    rules: list[tuple[str, list[str]]],
    nonterminals: set[str],
    names: _NameGen,
) -> tuple[list[tuple[str, list[str]]], set[str]]:
    new_rules: list[tuple[str, list[str]]] = []
    for lhs, rhs in rules:
        if len(rhs) <= 2:
            new_rules.append((lhs, rhs))
            continue
        # A -> B1 B2 ... Bk  =>  A -> B1 C1, C1 -> B2 C2, ..., C_{k-2} -> B_{k-1} Bk
        syms = rhs
        cur = lhs
        for i in range(len(syms) - 2):
            nxt = names.fresh(f"_BIN_{lhs}")
            nonterminals.add(nxt)
            new_rules.append((cur, [syms[i], nxt]))
            cur = nxt
        new_rules.append((cur, [syms[-2], syms[-1]]))

    return new_rules, nonterminals


# ---------------------------------------------------------------------------
# Step 4: DEL — epsilon elimination
# ---------------------------------------------------------------------------

def _find_nullable(
    rules: list[tuple[str, list[str]]],
) -> set[str]:
    nullable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs in nullable:
                continue
            if len(rhs) == 0 or all(s in nullable for s in rhs):
                nullable.add(lhs)
                changed = True
    return nullable


def _step_del(
    rules: list[tuple[str, list[str]]],
    start: str,
) -> list[tuple[str, list[str]]]:
    nullable = _find_nullable(rules)
    if not nullable:
        return rules

    new_rules_set: set[tuple[str, tuple[str, ...]]] = set()

    for lhs, rhs in rules:
        if len(rhs) == 0:
            continue  # remove epsilon rules (re-add for start later)

        # Find positions of nullable symbols
        nullable_positions = [i for i, s in enumerate(rhs) if s in nullable]

        # Generate all subsets of nullable positions to omit
        for mask in range(1 << len(nullable_positions)):
            omit = set()
            for bit, pos in enumerate(nullable_positions):
                if mask & (1 << bit):
                    omit.add(pos)
            new_rhs = tuple(s for i, s in enumerate(rhs) if i not in omit)
            if len(new_rhs) == 0:
                continue  # don't add epsilon (handled below)
            new_rules_set.add((lhs, new_rhs))

    # If start was nullable, add S -> epsilon
    result = [(lhs, list(rhs)) for lhs, rhs in new_rules_set]
    if start in nullable:
        result.append((start, []))

    return result


# ---------------------------------------------------------------------------
# Step 5: UNIT — remove unit (chain) rules A -> B
# ---------------------------------------------------------------------------

def _step_unit(
    rules: list[tuple[str, list[str]]],
    nonterminals: set[str],
) -> list[tuple[str, list[str]]]:
    # Separate unit rules from non-unit rules
    unit_rules: set[tuple[str, str]] = set()
    non_unit: list[tuple[str, list[str]]] = []

    for lhs, rhs in rules:
        if len(rhs) == 1 and rhs[0] in nonterminals:
            unit_rules.add((lhs, rhs[0]))
        else:
            non_unit.append((lhs, rhs))

    # Compute transitive closure of unit pairs
    # Start with identity: (A, A) for all nonterminals
    pairs: set[tuple[str, str]] = {(nt, nt) for nt in nonterminals}
    changed = True
    while changed:
        changed = False
        new_pairs: set[tuple[str, str]] = set()
        for a, b in pairs:
            for b2, c in unit_rules:
                if b == b2 and (a, c) not in pairs:
                    new_pairs.add((a, c))
        if new_pairs:
            pairs |= new_pairs
            changed = True

    # For each pair (A, B), add all non-unit rules of B as rules for A
    result_set: set[tuple[str, tuple[str, ...]]] = set()
    for a, b in pairs:
        for lhs, rhs in non_unit:
            if lhs == b:
                result_set.add((a, tuple(rhs)))

    return [(lhs, list(rhs)) for lhs, rhs in result_set]


# ---------------------------------------------------------------------------
# Cleanup: remove unreachable and unproductive nonterminals
# ---------------------------------------------------------------------------

def _productive(
    rules: list[tuple[str, list[str]]],
    terminals: set[str],
) -> set[str]:
    productive: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs in productive:
                continue
            if all(s in terminals or s in productive for s in rhs):
                productive.add(lhs)
                changed = True
    return productive


def _reachable(
    rules: list[tuple[str, list[str]]],
    start: str,
) -> set[str]:
    reachable: set[str] = {start}
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs in reachable:
                for s in rhs:
                    if s not in reachable:
                        reachable.add(s)
                        changed = True
    return reachable


def _cleanup(
    rules: list[tuple[str, list[str]]],
    start: str,
    terminals: set[str],
    nonterminals: set[str],
) -> tuple[list[tuple[str, list[str]]], set[str]]:
    # Remove unproductive
    prod = _productive(rules, terminals)
    # Start must be kept even if unproductive (for empty-language grammars)
    prod.add(start)
    rules = [
        (lhs, rhs)
        for lhs, rhs in rules
        if lhs in prod and all(s in terminals or s in prod for s in rhs)
    ]

    # Remove unreachable
    reach = _reachable(rules, start)
    rules = [
        (lhs, rhs)
        for lhs, rhs in rules
        if lhs in reach
    ]

    surviving_nt = {lhs for lhs, _ in rules}
    for _, rhs in rules:
        for s in rhs:
            if s in nonterminals:
                surviving_nt.add(s)
    surviving_nt &= nonterminals  # only keep actual nonterminals

    return rules, surviving_nt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_cnf(grammar: dict) -> dict:
    """Convert grammar to Chomsky Normal Form.

    Returns a new grammar dict in CNF.
    Raises ValueError if grammar is invalid.
    """
    _validate(grammar)
    grammar = copy.deepcopy(grammar)

    terminals = set(grammar["terminals"])
    nonterminals = set(grammar["nonterminals"])
    start = grammar["start"]
    rules = _rules_from_grammar(grammar)

    all_symbols = terminals | nonterminals
    names = _NameGen(all_symbols)

    # 1. START
    rules, start, nonterminals = _step_start(rules, start, nonterminals, names)

    # 2. TERM
    rules, nonterminals = _step_term(rules, terminals, nonterminals, names)

    # 3. BIN
    rules, nonterminals = _step_bin(rules, nonterminals, names)

    # 4. DEL (epsilon elimination)
    rules = _step_del(rules, start)

    # 5. UNIT (chain rule elimination)
    rules = _step_unit(rules, nonterminals)

    # Cleanup
    rules, nonterminals = _cleanup(rules, start, terminals, nonterminals)

    # Collect terminals actually used
    used_terminals = set()
    for _, rhs in rules:
        for s in rhs:
            if s in terminals:
                used_terminals.add(s)

    # Ensure start is in nonterminals
    nonterminals.add(start)

    return {
        "terminals": sorted(used_terminals),
        "nonterminals": sorted(nonterminals),
        "start": start,
        "rules": _rules_to_dicts(rules),
    }


def is_cnf(grammar: dict) -> bool:
    """Check if grammar is already in CNF."""
    try:
        _validate(grammar)
    except ValueError:
        return False

    terminals = set(grammar["terminals"])
    nonterminals = set(grammar["nonterminals"])
    start = grammar["start"]

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        length = len(rhs)

        if length == 0:
            # S -> epsilon allowed only for start, and start must not appear on any RHS
            if lhs != start:
                return False
            # Check start doesn't appear on RHS of any rule
            for r2 in grammar["rules"]:
                if start in r2["rhs"]:
                    return False
        elif length == 1:
            # Must be A -> a (terminal)
            if rhs[0] not in terminals:
                return False
        elif length == 2:
            # Must be A -> BC (two nonterminals)
            if rhs[0] not in nonterminals or rhs[1] not in nonterminals:
                return False
        else:
            return False

    return True
