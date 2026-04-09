"""LL(k) parse table construction and conflict detection."""
from __future__ import annotations

from ll_system.lib.first_follow import (
    compute_all,
    director_set,
    k_concat,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_epsilon_rhs(rhs: list[str]) -> bool:
    return len(rhs) == 0 or rhs == ["ε"]


# ---------------------------------------------------------------------------
# Core table builder
# ---------------------------------------------------------------------------

def build_parse_table(
    grammar: dict,
    k: int,
    nullable: set[str],
    first_k: dict[str, set[str]],
    follow_k: dict[str, set[str]],
) -> tuple[dict, list[dict]]:
    """Build the LL(k) parse table.

    Returns ``(table, conflicts)`` where:
    - *table* maps ``{nonterminal: {lookahead: rhs}}``
    - *conflicts* is a (possibly empty) list of conflict dicts

    A conflict occurs when two rules for the same nonterminal share a lookahead
    string in their director sets.
    """
    nonterminals: set[str] = set(grammar["nonterminals"])
    table: dict[str, dict[str, list[str]]] = {nt: {} for nt in nonterminals}
    conflicts: list[dict] = []

    for rule in grammar["rules"]:
        lhs: str = rule["lhs"]
        rhs: list[str] = rule["rhs"]

        ds = director_set(grammar, lhs, rhs, k, first_k, follow_k, nullable)

        for lookahead in ds:
            if lookahead in table[lhs]:
                existing_rhs = table[lhs][lookahead]
                # Only record a conflict when the two rules are genuinely different.
                if existing_rhs != rhs:
                    # Determine conflict type
                    # first/first if neither is triggered solely by follow
                    conflict_type = _classify_conflict(
                        grammar, lhs, existing_rhs, rhs, k, first_k, follow_k, nullable
                    )
                    conflicts.append(
                        {
                            "nonterminal": lhs,
                            "lookahead": lookahead,
                            "competing_rules": [existing_rhs, rhs],
                            "type": conflict_type,
                        }
                    )
            else:
                table[lhs][lookahead] = rhs

    return table, conflicts


def _classify_conflict(
    grammar: dict,
    nonterminal: str,
    rhs1: list[str],
    rhs2: list[str],
    k: int,
    first_k: dict[str, set[str]],
    follow_k: dict[str, set[str]],
    nullable: set[str],
) -> str:
    """Heuristically classify a conflict as 'first_first' or 'first_follow'."""
    from ll_system.lib.first_follow import compute_first_k_seq

    first1 = compute_first_k_seq(rhs1, grammar, k, first_k, nullable)
    first2 = compute_first_k_seq(rhs2, grammar, k, first_k, nullable)

    # If either rule's FIRST_k contains "" (nullable), the conflict involves FOLLOW
    if "" in first1 or "" in first2:
        return "first_follow"
    return "first_first"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_ll_k(grammar: dict, k: int) -> dict:
    """Check whether *grammar* is LL(k) for the given *k*.

    Returns a result dict with keys:
    - ``"is_ll_k"`` (bool)
    - ``"k"`` (int)
    - ``"first_sets"`` (dict[str, list[str]]) — sorted for JSON stability
    - ``"follow_sets"`` (dict[str, list[str]])
    - ``"conflicts"`` (list[dict]) — empty when ``is_ll_k`` is True
    - ``"parse_table"`` (dict | None) — built only when there are no conflicts
    """
    nullable, first_k, follow_k = compute_all(grammar, k)

    table, conflicts = build_parse_table(grammar, k, nullable, first_k, follow_k)

    is_ll = len(conflicts) == 0

    return {
        "is_ll_k": is_ll,
        "k": k,
        "first_sets": {nt: sorted(s) for nt, s in first_k.items()},
        "follow_sets": {nt: sorted(s) for nt, s in follow_k.items()},
        "conflicts": conflicts,
        "parse_table": table if is_ll else None,
    }


def find_min_ll_k(grammar: dict, max_k: int = 10) -> dict:
    """Find the minimum *k* for which *grammar* is LL(k).

    Checks k = 1, 2, …, *max_k* in order and returns as soon as LL(k) holds.

    Returns:
    - ``"found"`` (bool)
    - ``"k"`` (int | None) — the minimum k if found
    - ``"max_k_checked"`` (int)
    - ``"result_for_k"`` (dict) — ``check_ll_k`` result for the winning k, or
      the result for *max_k* when not found
    """
    last_result: dict = {}
    for k in range(1, max_k + 1):
        result = check_ll_k(grammar, k)
        last_result = result
        if result["is_ll_k"]:
            return {
                "found": True,
                "k": k,
                "max_k_checked": max_k,
                "result_for_k": result,
            }

    return {
        "found": False,
        "k": None,
        "max_k_checked": max_k,
        "result_for_k": last_result,
    }
