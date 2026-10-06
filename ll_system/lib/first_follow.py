"""FIRST_k, FOLLOW_k, NULLABLE computation for LL(k) grammar analysis."""
from __future__ import annotations

import time
from collections import defaultdict, deque


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_epsilon_rhs(rhs: list[str]) -> bool:
    """Return True if *rhs* represents an epsilon production."""
    return len(rhs) == 0 or rhs == ["ε"]


def _rule_dependencies(grammar: dict) -> dict[str, set[int]]:
    """Index rules to revisit when a right-hand-side nonterminal changes."""
    dependencies: dict[str, set[int]] = defaultdict(set)
    nonterminals = set(grammar["nonterminals"])
    for index, rule in enumerate(grammar["rules"]):
        for symbol in rule["rhs"]:
            if symbol in nonterminals:
                dependencies[symbol].add(index)
    return dependencies


# ---------------------------------------------------------------------------
# k-prefix concatenation
# ---------------------------------------------------------------------------

def k_concat(X: set[str], Y: set[str], k: int) -> set[str]:
    """k-prefix concatenation: X ⊕_k Y = {(x + y)[:k] for x in X for y in Y}.

    For each x in X and y in Y, the result string is (x + y) truncated to k
    characters.  Strings already of length k are left unchanged (y is ignored).
    """
    if k == 0:
        return {""} if X and Y else set()
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

    Uses a dependency worklist until the finite ascending chain stabilizes.
    """
    nullable: set[str] = set()

    dependencies = _rule_dependencies(grammar)
    pending = deque(range(len(grammar["rules"])))
    queued = set(pending)
    while pending:
        index = pending.popleft()
        queued.remove(index)
        rule = grammar["rules"][index]
        lhs, rhs = rule["lhs"], rule["rhs"]
        if lhs not in nullable and (
            _is_epsilon_rhs(rhs) or all(sym in nullable for sym in rhs)
        ):
            nullable.add(lhs)
            for dependent in dependencies.get(lhs, set()):
                if dependent not in queued:
                    pending.append(dependent)
                    queued.add(dependent)

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

        # Even a length-k prefix needs a productive suffix: FIRST_k consists
        # of prefixes of complete terminal derivations, not partial ones.
        if not result:
            break

    return result


# ---------------------------------------------------------------------------
# FIRST_k
# ---------------------------------------------------------------------------

def compute_first_k(grammar: dict, k: int) -> dict[str, set[str]]:
    """Compute FIRST_k(A) for every nonterminal A.

    Returns a dict mapping each nonterminal to its FIRST_k set (strings of
    length ≤ k; ``""`` means A can derive ε).

    Uses a dependency worklist with no arbitrary iteration cutoff. Each set
    grows monotonically within the finite universe of strings of length ≤ k.
    """
    nonterminals: list[str] = grammar["nonterminals"]
    nullable = compute_nullable(grammar)

    # Initialise: every nonterminal starts with an empty FIRST_k set.
    first_k: dict[str, set[str]] = {nt: set() for nt in nonterminals}

    dependencies = _rule_dependencies(grammar)
    pending = deque(range(len(grammar["rules"])))
    queued = set(pending)
    while pending:
        index = pending.popleft()
        queued.remove(index)
        rule = grammar["rules"][index]
        lhs, rhs = rule["lhs"], rule["rhs"]
        new_strings = compute_first_k_seq(rhs, grammar, k, first_k, nullable)
        if not new_strings.issubset(first_k[lhs]):
            first_k[lhs].update(new_strings)
            for dependent in dependencies.get(lhs, set()):
                if dependent not in queued:
                    pending.append(dependent)
                    queued.add(dependent)

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

    Propagates changes through a worklist until the finite fixed point.
    """
    nonterminals: list[str] = grammar["nonterminals"]
    start: str = grammar["start"]

    follow_k: dict[str, set[str]] = {nt: set() for nt in nonterminals}

    # Seed the start symbol with k dollar signs.
    eos = "$" * k if k > 0 else ""
    follow_k[start].add(eos)

    edges = _follow_dependencies(grammar, k, first_k, nullable)
    pending = deque([start])
    queued = {start}
    while pending:
        lhs = pending.popleft()
        queued.remove(lhs)
        for symbol, first_tail in edges.get(lhs, []):
            contribution = k_concat(first_tail, follow_k[lhs], k)
            if not contribution.issubset(follow_k[symbol]):
                follow_k[symbol].update(contribution)
                if symbol not in queued:
                    pending.append(symbol)
                    queued.add(symbol)

    return follow_k


def _follow_dependencies(
    grammar: dict, k: int, first_k: dict[str, set[str]], nullable: set[str]
) -> dict[str, list[tuple[str, set[str]]]]:
    """Transfer continuations at occurrences with a productive left prefix.

    S ⇒*_lm w A α requires the preceding symbols to derive terminal w.
    An occurrence behind a nonproductive symbol can never be reached there.
    """
    edges: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
    nonterminals = set(grammar["nonterminals"])
    for rule in grammar["rules"]:
        rhs = rule["rhs"]
        for index, symbol in enumerate(rhs):
            if symbol in nonterminals:
                first_tail = compute_first_k_seq(
                    rhs[index + 1:], grammar, k, first_k, nullable
                )
                edges[rule["lhs"]].append((symbol, first_tail))
                if not first_k[symbol]:
                    break
    return edges


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
    instead and is strictly more restrictive for k ≥ 2.

    Fixed-point construction:
        σ(S) ∋ {frozenset({""})}  (start symbol followed by end-of-input only)
        for each rule A → X1 … Xn, each L ∈ σ(A), each nonterminal Xi
        whose preceding symbols X1 … X_{i-1} derive a terminal string:
            σ(Xi) ⊇ {frozenset(k_concat(FIRST_k(X_{i+1}…Xn), L, k))}
    iterated to a fixed point. Each individual σ(A) is finite (⊆ 2^(Σ^{≤k})),
    so this always terminates in the absence of a *deadline*, though the
    number of distinct contexts can be exponential in the worst case — hence
    the *deadline* budget (docs/THEORY.md §3.1: "число таблиц в худшем случае
    экспоненциально — нужен бюджет").

    Returns ``(sigma, complete)``. *complete* is False when *deadline* (a
    ``time.monotonic()`` timestamp) is reached,
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

    edges = _follow_dependencies(grammar, k, first_k, nullable)
    pending = deque((start, context) for context in sigma.get(start, set()))
    while pending:
        if deadline is not None and time.monotonic() > deadline:
            return sigma, False
        lhs, local_follow = pending.popleft()
        for symbol, first_tail in edges.get(lhs, []):
            new_context = frozenset(k_concat(first_tail, set(local_follow), k))
            if new_context not in sigma[symbol]:
                sigma[symbol].add(new_context)
                pending.append((symbol, new_context))
            if deadline is not None and time.monotonic() > deadline:
                return sigma, False
    return sigma, True
