"""FIRST_k, FOLLOW_k, NULLABLE computation for LL(k) grammar analysis."""
from __future__ import annotations

_MAX_ITER = 1000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_epsilon_rhs(rhs: list[str]) -> bool:
    """Return True if *rhs* represents an epsilon production."""
    return len(rhs) == 0 or rhs == ["ε"]


# ---------------------------------------------------------------------------
# k-prefix concatenation
# ---------------------------------------------------------------------------

def k_concat(X: set[str], Y: set[str], k: int) -> set[str]:
    """k-prefix concatenation: X ⊕_k Y = {(x + y)[:k] for x in X for y in Y}.

    For each x in X and y in Y, the result string is (x + y) truncated to k
    characters.  Strings already of length k are left unchanged (y is ignored).
    """
    if k == 0:
        return {""}
    result: set[str] = set()
    for x in X:
        for y in Y:
            result.add((x + y)[:k])
    return result


# ---------------------------------------------------------------------------
# NULLABLE
# ---------------------------------------------------------------------------

def compute_nullable(grammar: dict) -> set[str]:
    """Return the set of nonterminals A such that A =>* ε.

    Uses fixed-point iteration (Kleene ascending chain).
    """
    nullable: set[str] = set()

    for _ in range(_MAX_ITER):
        changed = False
        for rule in grammar["rules"]:
            lhs: str = rule["lhs"]
            rhs: list[str] = rule["rhs"]
            if lhs in nullable:
                continue
            if _is_epsilon_rhs(rhs) or all(sym in nullable for sym in rhs):
                nullable.add(lhs)
                changed = True
        if not changed:
            break

    return nullable


# ---------------------------------------------------------------------------
# FIRST_k helpers
# ---------------------------------------------------------------------------

def compute_first_k_seq(
    seq: list[str],
    grammar: dict,
    k: int,
    first_k: dict[str, set[str]],
    nullable: set[str],
) -> set[str]:
    """Compute FIRST_k of a sequence of grammar symbols.

    *seq* may contain terminals and nonterminals (or be empty).
    Returns a set of strings of length ≤ k; ``""`` in the result means *seq*
    can derive ε.
    """
    if k == 0:
        return {""}
    if not seq:
        return {""}

    terminals = set(grammar.get("terminals", []))

    # Start with {""}; iterate left to right, concatenating FIRST_k of each symbol.
    result: set[str] = {""}
    for sym in seq:
        if sym == "ε":
            # treat explicit ε symbol as the empty string — no-op
            continue
        if sym in terminals:
            sym_first: set[str] = {sym}
        else:
            sym_first = first_k.get(sym, set())

        # Only extend strings in *result* that are still shorter than k.
        # k_concat handles the truncation correctly.
        result = k_concat(result, sym_first, k)

        # If no prefix in *result* can still grow (all are length k), stop early.
        if all(len(s) >= k for s in result):
            break

    return result


# ---------------------------------------------------------------------------
# FIRST_k
# ---------------------------------------------------------------------------

def compute_first_k(grammar: dict, k: int) -> dict[str, set[str]]:
    """Compute FIRST_k(A) for every nonterminal A.

    Returns a dict mapping each nonterminal to its FIRST_k set (strings of
    length ≤ k; ``""`` means A can derive ε).

    Uses fixed-point iteration (at most *_MAX_ITER* passes).
    """
    nonterminals: list[str] = grammar["nonterminals"]
    nullable = compute_nullable(grammar)

    # Initialise: every nonterminal starts with an empty FIRST_k set.
    first_k: dict[str, set[str]] = {nt: set() for nt in nonterminals}

    for _ in range(_MAX_ITER):
        changed = False
        for rule in grammar["rules"]:
            lhs: str = rule["lhs"]
            rhs: list[str] = rule["rhs"]

            if _is_epsilon_rhs(rhs):
                new_strings = {""}
            else:
                new_strings = compute_first_k_seq(rhs, grammar, k, first_k, nullable)

            before = len(first_k[lhs])
            first_k[lhs].update(new_strings)
            if len(first_k[lhs]) != before:
                changed = True

        if not changed:
            break

    return first_k


# ---------------------------------------------------------------------------
# FOLLOW_k
# ---------------------------------------------------------------------------

def compute_follow_k(
    grammar: dict,
    k: int,
    first_k: dict[str, set[str]],
    nullable: set[str],
) -> dict[str, set[str]]:
    """Compute FOLLOW_k(A) for every nonterminal A.

    Returns a dict mapping each nonterminal to its FOLLOW_k set (strings of
    length ≤ k using ``"$"`` as the end-of-input sentinel).

    FOLLOW_k(start) initially contains ``"$" * k`` (a string of k dollar signs
    for k ≥ 1; ``""`` for k = 0).

    For each rule B → α A β:
        FOLLOW_k(A) ⊇ k_concat(FIRST_k(β), FOLLOW_k(B), k)

    Iterates to fixed point (at most *_MAX_ITER* passes).
    """
    nonterminals: list[str] = grammar["nonterminals"]
    start: str = grammar["start"]

    follow_k: dict[str, set[str]] = {nt: set() for nt in nonterminals}

    # Seed the start symbol with k dollar signs.
    eos = "$" * k if k > 0 else ""
    follow_k[start].add(eos)

    for _ in range(_MAX_ITER):
        changed = False
        for rule in grammar["rules"]:
            lhs_b: str = rule["lhs"]
            rhs: list[str] = rule["rhs"]

            if _is_epsilon_rhs(rhs):
                continue

            for i, sym in enumerate(rhs):
                if sym == "ε":
                    continue
                if sym not in nonterminals:
                    # sym is a terminal — terminals have no FOLLOW set
                    continue

                # β is everything after position i in the rhs
                beta = rhs[i + 1:]

                # FIRST_k(β) ⊕_k FOLLOW_k(B)
                first_beta = compute_first_k_seq(beta, grammar, k, first_k, nullable)
                contribution = k_concat(first_beta, follow_k[lhs_b], k)

                before = len(follow_k[sym])
                follow_k[sym].update(contribution)
                if len(follow_k[sym]) != before:
                    changed = True

        if not changed:
            break

    return follow_k


# ---------------------------------------------------------------------------
# Director set
# ---------------------------------------------------------------------------

def director_set(
    grammar: dict,
    nonterminal: str,
    rhs: list[str],
    k: int,
    first_k: dict[str, set[str]],
    follow_k: dict[str, set[str]],
    nullable: set[str],
) -> set[str]:
    """Compute the director (predict) set for rule *nonterminal* → *rhs*.

    director(A → α) = FIRST_k(α) ⊕_k FOLLOW_k(A)
                    = k_concat(FIRST_k_seq(α), FOLLOW_k(A), k)
    """
    first_alpha = compute_first_k_seq(rhs, grammar, k, first_k, nullable)
    return k_concat(first_alpha, follow_k[nonterminal], k)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def compute_all(
    grammar: dict, k: int
) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    """Compute nullable, first_k, and follow_k in one call.

    Returns ``(nullable, first_k, follow_k)``.
    """
    nullable = compute_nullable(grammar)
    first_k = compute_first_k(grammar, k)
    follow_k = compute_follow_k(grammar, k, first_k, nullable)
    return nullable, first_k, follow_k
