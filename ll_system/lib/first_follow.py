"""FIRST_k, FOLLOW_k, NULLABLE computation for LL(k) grammar analysis."""
from __future__ import annotations

import time

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


# ---------------------------------------------------------------------------
# Local follow sets σ(A) — full Aho–Ullman LL(k) test [AU, §5.1]
# ---------------------------------------------------------------------------

def compute_local_follow_sets(
    grammar: dict,
    k: int,
    first_k: dict[str, set[str]],
    nullable: set[str],
    *,
    deadline: float | None = None,
) -> tuple[dict[str, set[frozenset[str]]], bool]:
    """Compute the local follow sets σ(A) for every nonterminal A.

    Unlike the single *global* FOLLOW_k(A) (``compute_follow_k``), σ(A) tracks
    every distinct k-length *set* of continuations that can actually follow A
    in some left-most derivation from the start symbol:

        σ(A) = {FIRST_k(α) | S ⇒*_lm w A α}

    Crucially, σ(A) is a set of **sets** of strings (each element L ∈ σ(A) is
    a whole FIRST_k(α) for one specific α, kept together as one context), not
    a flat set of strings — the full LL(k) test (docs/THEORY.md §3.1,
    [AU, §5.1]) checks ``k_concat(FIRST_k(β), L) ∩ k_concat(FIRST_k(γ), L)``
    for the *same* L as a whole. Flattening σ(A) into individual strings and
    testing each singleton context separately is unsound — it can miss a
    conflict that only shows up when the elements of L are combined (see the
    regression for ``S → AB, A → ε|b, B → aa|ba`` in ``test_ll_k_full.py``:
    not LL(2) only when {aa, ba} is tested together as one context).

    The strong-LL(k) director-set test uses a single global FOLLOW_k(A)
    instead and is strictly weaker for k ≥ 2.

    Fixed-point construction:
        σ(S) ∋ {frozenset({""})}  (start symbol followed by end-of-input only)
        for each rule A → X1 … Xn, each L ∈ σ(A), each nonterminal Xi:
            σ(Xi) ⊇ {frozenset(k_concat(FIRST_k(X_{i+1}…Xn), L, k))}
    iterated to a fixed point. Each individual σ(A) is finite (⊆ 2^(Σ^{≤k})),
    so this always terminates in the absence of a *deadline*, though the
    number of distinct contexts can be exponential in the worst case — hence
    the *deadline* budget (docs/THEORY.md §3.1: "число таблиц в худшем случае
    экспоненциально — нужен бюджет").

    Returns ``(sigma, complete)``. *complete* is False when *deadline* (a
    ``time.monotonic()`` timestamp) is reached, or the iteration cap is hit,
    before the fixed point is reached; in that case *sigma* is a sound but
    possibly incomplete (under-approximated) set of local follow contexts —
    safe to use for reporting conflicts actually found, but not to certify
    the absence of conflicts.
    """
    nonterminals: set[str] = set(grammar["nonterminals"])
    start: str = grammar["start"]

    sigma: dict[str, set[frozenset[str]]] = {nt: set() for nt in nonterminals}
    if start in sigma:
        sigma[start].add(frozenset({""}))

    rules: list[tuple[str, list[str]]] = [
        (r["lhs"], [] if _is_epsilon_rhs(r["rhs"]) else list(r["rhs"]))
        for r in grammar["rules"]
    ]

    for _ in range(_MAX_ITER):
        changed = False
        for lhs, rhs in rules:
            if lhs not in sigma:
                continue
            local_contexts = list(sigma[lhs])
            if not local_contexts:
                continue
            for i, sym in enumerate(rhs):
                if sym == "ε" or sym not in nonterminals:
                    continue
                tail = rhs[i + 1:]
                first_tail = compute_first_k_seq(tail, grammar, k, first_k, nullable)
                for local_follow in local_contexts:
                    new_context = frozenset(k_concat(first_tail, set(local_follow), k))
                    if new_context not in sigma[sym]:
                        sigma[sym].add(new_context)
                        changed = True
            if deadline is not None and time.monotonic() > deadline:
                return sigma, False
        if not changed:
            return sigma, True

    return sigma, False
