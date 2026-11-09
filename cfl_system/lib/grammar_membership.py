"""Shared exact grammar-membership oracle (CNF + CYK).

Single place used by cfl_system, dcfl_system and ll_system so that all three
decide word membership of an explicit grammar identically:

    normalize_epsilon_grammar(g)  -- map the "ε" rhs spelling to []
    grammar_membership(g)         -- word -> bool, exact (to_cnf + cyk_parse)

Conversion / parsing failures propagate; callers decide how to degrade
(dcfl and ll turn them into "no oracle" / ``None``).
"""

from __future__ import annotations

import copy
from typing import Callable

from cfl_system.lib.cnf import to_cnf
from cfl_system.lib.cyk import cyk_parse


def is_epsilon_rhs(rhs: list[str]) -> bool:
    """True if rhs represents epsilon ([] or ["ε"])."""
    return len(rhs) == 0 or rhs == ["ε"]


def normalize_epsilon_grammar(grammar: dict) -> dict:
    """Return a deep copy with every ["ε"] rhs replaced by []."""
    g = copy.deepcopy(grammar)
    g["rules"] = [
        {"lhs": r["lhs"], "rhs": [] if is_epsilon_rhs(r["rhs"]) else list(r["rhs"])}
        for r in g["rules"]
    ]
    return g


def grammar_membership(grammar: dict) -> Callable[[str], bool]:
    """Exact membership check for *grammar* (ε spelling normalized first)."""
    cnf = to_cnf(normalize_epsilon_grammar(grammar))
    return lambda word: cyk_parse(cnf, word)
