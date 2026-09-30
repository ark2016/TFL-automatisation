"""LL(k) parse table construction and conflict detection."""
from __future__ import annotations

import time

from ll_system.lib.first_follow import (
    compute_all,
    compute_first_k_seq,
    compute_local_follow_sets,
    director_set,
    k_concat,
)
from ll_system.lib.grammar_transforms import is_left_recursive, remove_useless_symbols


# ---------------------------------------------------------------------------
# Budget defaults (full LL(k) test — see _full_ll_k_test)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_TABLES = 20000
_MAX_LL_CONFLICTS_STORED = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_epsilon_rhs(rhs: list[str]) -> bool:
    return len(rhs) == 0 or rhs == ["ε"]


def _is_strong_ll_k(grammar: dict, k: int) -> bool:
    """Cheap-only strong LL(k) check (no full LL(k) test)."""
    nullable, first_k, follow_k = compute_all(grammar, k)
    _, conflicts = build_parse_table(grammar, k, nullable, first_k, follow_k)
    return len(conflicts) == 0


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
        rhs: list[str] = [] if _is_epsilon_rhs(rule["rhs"]) else rule["rhs"]

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
# Full LL(k) test [AU, §5.1] via local follow sets σ(A) (docs/THEORY.md §3.1)
# ---------------------------------------------------------------------------

def _full_ll_k_test(
    grammar: dict,
    k: int,
    nullable: set[str],
    first_k: dict[str, set[str]],
    *,
    deadline: float | None,
    max_tables: int,
) -> tuple[bool | None, list[dict], bool]:
    """Run the full Aho–Ullman LL(k) test using local follow sets σ(A).

    G is LL(k) iff for every nonterminal A, every L ∈ σ(A) (L itself a whole
    *set* of strings — see ``compute_local_follow_sets``), and every pair of
    distinct rules A → β, A → γ:
        k_concat(FIRST_k(β), L) ∩ k_concat(FIRST_k(γ), L) = ∅
    checked against the same L as a whole — never by splitting L into its
    individual strings and testing each singleton separately (that would be
    a strictly weaker, unsound test for k ≥ 2: docs/THEORY.md §3.1).

    Returns ``(is_ll_k, ll_conflicts, test_complete)``:
    - ``is_ll_k`` is ``None`` when the *max_tables* / *deadline* budget was
      exhausted before every (A, L) context could be checked *and* no
      conflict had been found yet (undetermined — a later, unchecked context
      might still conflict).
    - once a single conflict is found the answer is conclusively ``False``
      regardless of how much of the budget remains (one counter-example is
      enough — no need to enumerate the rest).
    - ``ll_conflicts`` entries: ``{"nonterminal", "local_follow" (sorted list
      of the σ(A) contexts — each itself a sorted list of strings — that
      produce this lookahead), "lookahead", "competing_rules"}``, capped at
      ``_MAX_LL_CONFLICTS_STORED``.
    """
    sigma, sigma_complete = compute_local_follow_sets(
        grammar, k, first_k, nullable, deadline=deadline
    )

    rules_by_lhs: dict[str, list[list[str]]] = {}
    for rule in grammar["rules"]:
        rhs = [] if _is_epsilon_rhs(rule["rhs"]) else list(rule["rhs"])
        alternatives = rules_by_lhs.setdefault(rule["lhs"], [])
        if rhs not in alternatives:
            alternatives.append(rhs)

    # (nonterminal, rhs1, rhs2, lookahead) -> set of σ(A) contexts (each a
    # frozenset of strings) that produce this lookahead conflict
    conflict_map: dict[
        tuple[str, tuple[str, ...], tuple[str, ...], str], set[frozenset[str]]
    ] = {}
    is_ll = True
    tables_built = 0
    truncated = not sigma_complete

    if sigma_complete:
        for lhs, alts in rules_by_lhs.items():
            if len(alts) < 2:
                continue
            for local_follow in sorted(sigma.get(lhs, set()), key=lambda ctx: sorted(ctx)):
                tables_built += 1
                if tables_built > max_tables or (
                    deadline is not None and time.monotonic() > deadline
                ):
                    truncated = True
                    break
                director_sets = [
                    k_concat(
                        compute_first_k_seq(rhs, grammar, k, first_k, nullable),
                        set(local_follow),
                        k,
                    )
                    for rhs in alts
                ]
                for i in range(len(alts)):
                    for j in range(i + 1, len(alts)):
                        common = director_sets[i] & director_sets[j]
                        if not common:
                            continue
                        is_ll = False
                        rules_key = (tuple(alts[i]), tuple(alts[j]))
                        for lookahead in common:
                            key = (lhs, rules_key[0], rules_key[1], lookahead)
                            conflict_map.setdefault(key, set()).add(local_follow)
            if truncated:
                break

    conflicts: list[dict] = []
    for (lhs, rhs1, rhs2, lookahead), contexts in sorted(conflict_map.items()):
        conflicts.append(
            {
                "nonterminal": lhs,
                "local_follow": sorted(sorted(ctx) for ctx in contexts),
                "lookahead": lookahead,
                "competing_rules": [list(rhs1), list(rhs2)],
            }
        )
        if len(conflicts) >= _MAX_LL_CONFLICTS_STORED:
            break

    if is_ll and truncated:
        # Nothing found yet, but the search was cut short by the budget —
        # cannot certify the absence of a conflict in the unchecked contexts.
        return None, conflicts, False
    return is_ll, conflicts, True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_ll_k(
    grammar: dict,
    k: int,
    *,
    time_budget_s: float | None = None,
    max_tables: int = _DEFAULT_MAX_TABLES,
) -> dict:
    """Check whether *grammar* is LL(k) for the given *k* (docs/THEORY.md §3.1).

    Runs two distinct tests:
    - **strong LL(k)** (``is_strong_ll_k``): the classic director-set test
      with a single *global* FOLLOW_k(A) per nonterminal — cheap and always
      complete. SLL(k) ⇒ LL(k) [RS, Thm 2], so a pass here is conclusive for
      LL(k) too and the (expensive) full test below is skipped.
    - **LL(k)** (``is_ll_k``): the full Aho–Ullman test [AU, §5.1] via local
      follow sets σ(A) — only run when the strong test fails, since it can
      require exponentially many tables in the worst case and needs a
      *time_budget_s* / *max_tables* budget.

    The time budget covers local-follow enumeration and the full-table test
    only. Exact NULLABLE/FIRST/FOLLOW computation and the strong test run
    before that deadline starts; this is not a wall-clock cap on this call.

    Returns a result dict with keys:
    - ``"is_ll_k"`` (bool | None) — ``None`` when the budget was exhausted
      before a conclusive answer (see ``"test_complete"``).
    - ``"is_strong_ll_k"`` (bool)
    - ``"k"`` (int)
    - ``"first_sets"`` / ``"follow_sets"`` (dict[str, list[str]]) — global
      FIRST_k / FOLLOW_k, sorted for JSON stability.
    - ``"conflicts"`` (list[dict]) — strong-test conflicts (``competing_rules``
      + ``type``, as built by ``build_parse_table``); empty when
      ``is_strong_ll_k`` is True.
    - ``"ll_conflicts"`` (list[dict]) — full-test conflicts (see
      ``_full_ll_k_test``); only populated when the strong test failed.
    - ``"parse_table"`` (dict | None) — built only when ``is_strong_ll_k`` is
      True. The full test's per-context (σ(A)-indexed) tables aren't a
      single global parse table, so no table is returned for a grammar that
      is LL(k) but not strong LL(k).
    - ``"test_complete"`` (bool) — False only when ``is_ll_k`` is None
      (budget exhausted before a conclusive answer).
    """
    nullable, first_k, follow_k = compute_all(grammar, k)

    table, strong_conflicts = build_parse_table(grammar, k, nullable, first_k, follow_k)
    is_strong = len(strong_conflicts) == 0

    result = {
        "is_ll_k": True if is_strong else None,
        "is_strong_ll_k": is_strong,
        "k": k,
        "first_sets": {nt: sorted(s) for nt, s in first_k.items()},
        "follow_sets": {nt: sorted(s) for nt, s in follow_k.items()},
        "conflicts": strong_conflicts,
        "ll_conflicts": [],
        "parse_table": table if is_strong else None,
        "test_complete": True,
    }

    if is_strong:
        # SLL(k) ⇒ LL(k) [RS, Thm 2] — the full (expensive) test isn't needed.
        return result

    deadline = time.monotonic() + time_budget_s if time_budget_s is not None else None
    is_ll, ll_conflicts, test_complete = _full_ll_k_test(
        grammar, k, nullable, first_k, deadline=deadline, max_tables=max_tables
    )
    result["is_ll_k"] = is_ll
    result["ll_conflicts"] = ll_conflicts
    result["test_complete"] = test_complete
    return result


def find_min_ll_k(
    grammar: dict,
    max_k: int = 10,
    *,
    time_budget_s: float | None = None,
) -> dict:
    """Find the minimum *k* for which *grammar* is (full) LL(k).

    Checks k = 1, 2, …, *max_k* in order via :func:`check_ll_k` and returns
    as soon as the full LL(k) test passes.

    Before searching, checks *grammar* for left recursion — after first
    removing useless symbols (non-productive / unreachable nonterminals,
    docs/THEORY.md §3.1: "после удаления бесполезных символов"), since an
    unreachable or dead nonterminal's own left recursion says nothing about
    whether *this* grammar can generate its language via a leftmost
    derivation. A left-recursive grammar (A ⇒⁺ A α) has no LL(k) grammar for
    *this same derivation* for any k [AU] — that is a statement about this
    specific grammar, not about the language (a left-recursive grammar's
    language may still have a different, non-left-recursive LL(k) grammar;
    see ll_system/CLAUDE.md: "left recursion ≠ not LL"). This short-circuits
    to an immediate certificate instead of running *max_k* doomed (and
    potentially expensive) full tests.

    Absent a certificate (e.g. an essentially-ambiguous grammar with no left
    recursion), exhausting k ≤ *max_k* without success is reported as
    "not LL(k) for k ≤ max_k_checked" — not a claim about every k. And when
    a checked k hits its own budget (``test_complete`` False, i.e.
    ``is_ll_k`` came back ``None``), the search stops with
    ``"undetermined"`` — a larger witnessed k could not establish minimality;
    the true answer might lie in the
    unchecked (or budget-cut) part of the search space, so callers must not
    treat "not found" as a conclusive negative in that case (docs/THEORY.md
    §3.1: "лимит ⇒ unknown").

    Returns:
    - ``"found"`` (bool)
    - ``"k"`` (int | None) — the minimum k with full LL(k), if found
    - ``"strong_k"`` (int | None) — the minimum k with strong LL(k) among the
      k values actually checked, if any (independent of ``"k"`` / ``"found"``)
    - ``"max_k_checked"`` (int) — the highest k actually run through
      ``check_ll_k`` (0 when the left-recursion certificate short-circuits
      the search)
    - ``"max_k_decided"`` (int) — the highest k for which ``check_ll_k``
      returned a *conclusive* answer (``test_complete`` True); can be less
      than ``max_k_checked`` when the budget ran out partway through
    - ``"undetermined"`` (bool) — True when the search stopped at an
      inconclusive k or exhausted its time budget before finding a witness
    - ``"result_for_k"`` (dict) — ``check_ll_k`` result for the winning k, or
      for the last k checked when not found (``{}`` when short-circuited)
    - ``"certificate"`` (dict | None) — ``{"type": "left_recursion", ...}``
      when the grammar is proven not LL(k) for *any* k; ``None`` otherwise
      (including when the k ≤ max_k search simply found no witness)
    - ``"not_ll_any_k"`` (bool) — True only when ``"certificate"`` is set
    """
    if is_left_recursive(remove_useless_symbols(grammar)):
        return {
            "found": False,
            "k": None,
            "strong_k": None,
            "max_k_checked": 0,
            "max_k_decided": 0,
            "undetermined": False,
            "result_for_k": {},
            "certificate": {
                "type": "left_recursion",
                "detail": (
                    "This grammar (after removing non-productive/unreachable "
                    "symbols) has direct or indirect left recursion (A =>+ A alpha); "
                    "THIS GRAMMAR is not LL(k) for any k (docs/THEORY.md §3.1, [AU]). "
                    "This is a statement about the grammar, not the language: the "
                    "language may still have a different, non-left-recursive LL(k) "
                    "grammar."
                ),
            },
            "not_ll_any_k": True,
        }

    deadline = time.monotonic() + time_budget_s if time_budget_s is not None else None

    last_result: dict = {}
    strong_k: int | None = None
    found_k: int | None = None
    found_result: dict | None = None
    max_k_checked = 0
    max_k_decided = 0
    undetermined = False

    # Keep scanning k while either the minimal LL(k) or the minimal strong
    # LL(k) is still unknown — once found_k is set, only the cheap strong
    # check is needed for the remaining k values (the expensive full test
    # has already answered the question the caller asked).
    for k in range(1, max_k + 1):
        if deadline is not None and time.monotonic() > deadline:
            undetermined = found_k is None
            break
        max_k_checked = k

        if found_k is None:
            remaining = (deadline - time.monotonic()) if deadline is not None else None
            result = check_ll_k(grammar, k, time_budget_s=remaining)
            last_result = result
            if result.get("is_ll_k") is None or not result.get("test_complete", True):
                undetermined = True
                break
            max_k_decided = k
            if strong_k is None and result.get("is_strong_ll_k"):
                strong_k = k
            if result.get("is_ll_k") is True:
                found_k = k
                found_result = result
        elif strong_k is None and _is_strong_ll_k(grammar, k):
            strong_k = k

        if found_k is not None and strong_k is not None:
            break

    if found_k is not None:
        return {
            "found": True,
            "k": found_k,
            "strong_k": strong_k,
            "max_k_checked": max_k_checked,
            "max_k_decided": max_k_decided,
            "undetermined": False,
            "result_for_k": found_result,
            "certificate": None,
            "not_ll_any_k": False,
        }

    return {
        "found": False,
        "k": None,
        "strong_k": strong_k,
        "max_k_checked": max_k_checked,
        "max_k_decided": max_k_decided,
        "undetermined": undetermined,
        "result_for_k": last_result,
        "certificate": None,
        "not_ll_any_k": False,
    }
