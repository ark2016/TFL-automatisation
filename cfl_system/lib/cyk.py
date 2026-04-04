"""CYK (Cocke-Younger-Kasami) parser for CNF grammars.

Public API:
    cyk_parse(grammar, word)        -- membership test
    cyk_parse_table(grammar, word)  -- full parse table for debugging
"""

from __future__ import annotations

import time
from typing import Any


def _build_lookup(grammar: dict) -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]], bool]:
    """Build lookup tables from CNF grammar.

    Returns:
        unit_map:  terminal -> set of nonterminals (A -> a rules)
        pair_map:  (B, C) -> set of nonterminals (A -> BC rules)
        start_nullable: whether start -> epsilon exists
    """
    terminals = set(grammar["terminals"])
    nonterminals = set(grammar["nonterminals"])
    start = grammar["start"]

    unit_map: dict[str, set[str]] = {}   # a -> {A | A -> a}
    pair_map: dict[tuple[str, str], set[str]] = {}  # (B,C) -> {A | A -> BC}
    start_nullable = False

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]

        if len(rhs) == 0:
            if lhs == start:
                start_nullable = True
        elif len(rhs) == 1:
            unit_map.setdefault(rhs[0], set()).add(lhs)
        elif len(rhs) == 2:
            key = (rhs[0], rhs[1])
            pair_map.setdefault(key, set()).add(lhs)

    return unit_map, pair_map, start_nullable


def cyk_parse_table(grammar: dict, word: str) -> list[list[set[str]]]:
    """Return the full CYK parse table.

    table[i][l] = set of nonterminals that generate word[i:i+l+1].
    i is the start index, l is (length - 1).
    """
    unit_map, pair_map, start_nullable = _build_lookup(grammar)
    n = len(word)

    if n == 0:
        return []

    # table[i][l] = set of nonterminals deriving word[i .. i+l]
    table: list[list[set[str]]] = [[set() for _ in range(n)] for _ in range(n)]

    use_timeout = n > 100
    t0 = time.monotonic() if use_timeout else 0.0

    # Length 1 substrings
    for i in range(n):
        ch = word[i]
        if ch in unit_map:
            table[i][0] = set(unit_map[ch])

    # Length 2 .. n
    for length in range(2, n + 1):  # substring length
        l = length - 1  # 0-based length index
        for i in range(n - l):  # start position
            cell: set[str] = set()
            for k in range(1, length):  # split: first part has length k
                left = table[i][k - 1]
                right = table[i + k][l - k]
                if not left or not right:
                    continue
                for b in left:
                    for c in right:
                        key = (b, c)
                        if key in pair_map:
                            cell.update(pair_map[key])
            table[i][l] = cell

        # Periodic timeout check
        if use_timeout and length % 10 == 0:
            if time.monotonic() - t0 > 10.0:
                raise TimeoutError(
                    f"CYK parsing exceeded 10s timeout (word length={n}, "
                    f"processed up to substring length={length})"
                )

    return table


def cyk_parse(grammar: dict, word: str) -> bool:
    """Check if word is in L(grammar) using CYK algorithm.

    Grammar MUST be in CNF (call to_cnf first if needed).
    For empty word, checks if start symbol is nullable.
    Raises TimeoutError if word length > 100 and processing exceeds 10s.
    Returns True if word is in L(grammar).
    """
    start = grammar["start"]

    if len(word) == 0:
        # Check for S -> epsilon
        for rule in grammar["rules"]:
            if rule["lhs"] == start and len(rule["rhs"]) == 0:
                return True
        return False

    table = cyk_parse_table(grammar, word)
    n = len(word)
    return start in table[0][n - 1]
