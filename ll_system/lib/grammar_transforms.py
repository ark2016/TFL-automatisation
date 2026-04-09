"""Grammar transformation algorithms for making grammars LL-parseable.

Public API:
    is_left_recursive(grammar)         -- detect direct/indirect left recursion
    eliminate_left_recursion(grammar)  -- remove all left recursion
    left_factor(grammar)               -- eliminate common prefixes
    eliminate_epsilon_rules(grammar)   -- remove A → ε rules
    eliminate_unit_rules(grammar)      -- remove unit rules A → B
    to_ll_normal_form(grammar)         -- apply full pipeline
    is_grammar_equivalent_sample(g1, g2, max_len, alphabet)  -- spot-check equivalence
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from typing import Optional


# ---------------------------------------------------------------------------
# Helper: fresh nonterminal names
# ---------------------------------------------------------------------------

class _NameGen:
    """Generate fresh nonterminal names that don't collide with existing ones."""

    def __init__(self, existing: set[str]) -> None:
        self._existing: set[str] = set(existing)
        self._counters: dict[str, int] = defaultdict(int)

    def fresh(self, prefix: str) -> str:
        """Return a fresh name starting with *prefix* not in existing."""
        while True:
            self._counters[prefix] += 1
            name = f"{prefix}_{self._counters[prefix]}"
            if name not in self._existing:
                self._existing.add(name)
                return name


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_epsilon_rhs(rhs: list[str]) -> bool:
    """Return True if rhs represents epsilon ([] or ["ε"])."""
    return len(rhs) == 0 or rhs == ["ε"]


def _normalize_rhs(rhs: list[str]) -> list[str]:
    """Return [] if epsilon, else return rhs unchanged."""
    if _is_epsilon_rhs(rhs):
        return []
    return list(rhs)


def _normalize_grammar(grammar: dict) -> dict:
    """Return a deep copy with all "ε" in rhs normalized to []."""
    g = copy.deepcopy(grammar)
    g["rules"] = [
        {"lhs": r["lhs"], "rhs": _normalize_rhs(r["rhs"])}
        for r in g["rules"]
    ]
    return g


def _rules_list(grammar: dict) -> list[tuple[str, list[str]]]:
    """Return grammar rules as list of (lhs, rhs) tuples (normalized)."""
    return [(r["lhs"], _normalize_rhs(r["rhs"])) for r in grammar["rules"]]


def _rules_to_dicts(rules: list[tuple[str, list[str]]]) -> list[dict]:
    return [{"lhs": lhs, "rhs": list(rhs)} for lhs, rhs in rules]


def _compute_nullable(rules: list[tuple[str, list[str]]]) -> set[str]:
    """Compute nullable nonterminals via fixed-point iteration."""
    nullable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs not in nullable:
                if len(rhs) == 0 or all(s in nullable for s in rhs):
                    nullable.add(lhs)
                    changed = True
    return nullable


def _compute_reachable(rules: list[tuple[str, list[str]]], start: str) -> set[str]:
    """Compute reachable nonterminals from start symbol."""
    # Collect all nonterminals from rules
    all_lhs = {lhs for lhs, _ in rules}

    reachable: set[str] = {start}
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs in reachable:
                for sym in rhs:
                    if sym in all_lhs and sym not in reachable:
                        reachable.add(sym)
                        changed = True
    return reachable


def _compute_productive(
    rules: list[tuple[str, list[str]]], terminals: set[str]
) -> set[str]:
    """Compute productive nonterminals (those that can derive a terminal string)."""
    productive: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs not in productive:
                if all(s in terminals or s in productive for s in rhs):
                    productive.add(lhs)
                    changed = True
    return productive


def _dedup_rules(rules: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    """Remove duplicate rules preserving order."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    result: list[tuple[str, list[str]]] = []
    for lhs, rhs in rules:
        key = (lhs, tuple(rhs))
        if key not in seen:
            seen.add(key)
            result.append((lhs, list(rhs)))
    return result


# ---------------------------------------------------------------------------
# 1. is_left_recursive
# ---------------------------------------------------------------------------

def is_left_recursive(grammar: dict) -> bool:
    """Return True if grammar has any direct or indirect left recursion.

    Direct: A → A α
    Indirect: A → B α, B → A β (or longer cycles)
    Uses reachability in the "can-start-with" relation.
    """
    g = _normalize_grammar(grammar)
    nonterminals = set(g["nonterminals"])
    rules = _rules_list(g)

    # Build "can-start-with-NT" graph: A → B means A has a rule A → B...
    # (i.e., B is the first symbol, which must be a nonterminal)
    can_start: dict[str, set[str]] = {nt: set() for nt in nonterminals}
    nullable = _compute_nullable(rules)

    for lhs, rhs in rules:
        if lhs not in nonterminals:
            continue
        # Walk through rhs; a nonterminal at position i can be the "start"
        # if all preceding symbols are nullable (or there are none).
        for sym in rhs:
            if sym in nonterminals:
                can_start[lhs].add(sym)
                if sym not in nullable:
                    break  # sym is not nullable, so it blocks further start-symbols
            else:
                break  # terminal blocks further start-symbols

    # Check for cycles: A can_start A (direct or indirect)
    for start_nt in nonterminals:
        visited: set[str] = set()
        queue: deque[str] = deque(can_start[start_nt])
        while queue:
            cur = queue.popleft()
            if cur == start_nt:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            for nxt in can_start.get(cur, set()):
                if nxt not in visited:
                    queue.append(nxt)

    return False


# ---------------------------------------------------------------------------
# 2. eliminate_left_recursion
# ---------------------------------------------------------------------------

def eliminate_left_recursion(grammar: dict) -> dict:
    """Eliminate all left recursion (direct and indirect).

    Returns a new grammar dict. Language is preserved.
    Uses the standard algorithm (Aho, Sethi, Ullman §4.3):

    1. Order nonterminals: A1, A2, ..., An
    2. For i = 1 to n:
       a. For j = 1 to i-1:
          Replace Ai → Aj α with Ai → δ1 α | δ2 α | ...
          where Aj → δ1 | δ2 | ... are current rules for Aj
       b. Eliminate direct left recursion from Ai:
          Ai → Ai α1 | ... | Ai αm | β1 | ... | βk
          becomes:
          Ai → β1 Ai' | ... | βk Ai'
          Ai' → α1 Ai' | ... | αm Ai' | ε

    New nonterminals named: A_prime (or A_prime_2, A_prime_3, ... if conflict).
    """
    g = _normalize_grammar(grammar)
    nonterminals: list[str] = list(g["nonterminals"])
    terminals: set[str] = set(g["terminals"])
    start: str = g["start"]

    # Put start symbol first so it is A1 (important for language preservation)
    if start in nonterminals and nonterminals[0] != start:
        nonterminals.remove(start)
        nonterminals.insert(0, start)

    all_symbols = set(nonterminals) | terminals
    names = _NameGen(all_symbols)

    # rules_for[A] = list of rhs alternatives for A
    rules_for: dict[str, list[list[str]]] = {nt: [] for nt in nonterminals}
    extra_nts: list[str] = []  # newly created prime nonterminals

    for lhs, rhs in _rules_list(g):
        if lhs in rules_for:
            rules_for[lhs].append(rhs)
        # ignore rules for nonterminals not in our ordered list (shouldn't happen)

    def _get_rules(nt: str) -> list[list[str]]:
        return rules_for.get(nt, [])

    def _set_rules(nt: str, alts: list[list[str]]) -> None:
        rules_for[nt] = alts

    for i, ai in enumerate(nonterminals):
        # Step (a): substitute Aj (j < i) into Ai rules
        for j in range(i):
            aj = nonterminals[j]
            new_alts: list[list[str]] = []
            for rhs in _get_rules(ai):
                if rhs and rhs[0] == aj:
                    # Replace Ai → Aj α  with  Ai → δ α  for each Aj → δ
                    tail = rhs[1:]
                    for delta in _get_rules(aj):
                        new_alts.append(list(delta) + list(tail))
                else:
                    new_alts.append(list(rhs))
            _set_rules(ai, new_alts)

        # Step (b): eliminate direct left recursion from Ai
        left_rec: list[list[str]] = []   # Ai → Ai α  →  α
        non_left_rec: list[list[str]] = []  # Ai → β

        for rhs in _get_rules(ai):
            if rhs and rhs[0] == ai:
                left_rec.append(rhs[1:])
            else:
                non_left_rec.append(rhs)

        if not left_rec:
            # No direct left recursion for Ai
            _set_rules(ai, non_left_rec)
            continue

        # Create new nonterminal Ai'
        prime_name = names.fresh(f"{ai}_prime")
        extra_nts.append(prime_name)
        rules_for[prime_name] = []

        # Ai → β becomes  Ai → β Ai'  (if β is not empty, else Ai → Ai')
        new_ai_alts: list[list[str]] = []
        for beta in non_left_rec:
            new_ai_alts.append(list(beta) + [prime_name])
        if not non_left_rec:
            # Grammar has only left-recursive rules for Ai (infinite loop in original)
            # Add Ai → Ai' as fallback (empty β)
            new_ai_alts.append([prime_name])

        # Ai' → α Ai' | ε
        prime_alts: list[list[str]] = []
        for alpha in left_rec:
            prime_alts.append(list(alpha) + [prime_name])
        prime_alts.append([])  # Ai' → ε

        _set_rules(ai, new_ai_alts)
        _set_rules(prime_name, prime_alts)

    # Build result grammar
    all_nts = list(nonterminals) + extra_nts
    result_rules: list[tuple[str, list[str]]] = []
    for nt in all_nts:
        for rhs in _get_rules(nt):
            result_rules.append((nt, list(rhs)))
    result_rules = _dedup_rules(result_rules)

    return {
        "nonterminals": all_nts,
        "terminals": list(terminals),
        "start": start,
        "rules": _rules_to_dicts(result_rules),
    }


# ---------------------------------------------------------------------------
# 3. left_factor
# ---------------------------------------------------------------------------

def left_factor(grammar: dict) -> dict:
    """Apply left factoring to eliminate common prefixes.

    Returns a new grammar dict. Language is preserved.

    For each nonterminal A with rules sharing a common prefix α:
      A → α β1 | α β2 | ... | γ1 | γ2 | ...
    Replace with:
      A → α A' | γ1 | γ2 | ...
      A' → β1 | β2 | ...

    Repeat until no common prefixes remain.
    New nonterminals named: A_lf1, A_lf2, etc.

    Note: The longest common prefix is factored first.
    """
    g = _normalize_grammar(grammar)
    nonterminals: list[str] = list(g["nonterminals"])
    terminals: set[str] = set(g["terminals"])
    start: str = g["start"]

    all_symbols = set(nonterminals) | terminals
    names = _NameGen(all_symbols)

    # rules_for[A] = list of rhs alternatives
    rules_for: dict[str, list[list[str]]] = {nt: [] for nt in nonterminals}
    for lhs, rhs in _rules_list(g):
        if lhs in rules_for:
            rules_for[lhs].append(list(rhs))

    # Track all nonterminals including newly created ones
    all_nts: list[str] = list(nonterminals)

    def _longest_common_prefix(alts: list[list[str]]) -> list[str]:
        """Return the longest prefix shared by at least two alternatives."""
        if len(alts) < 2:
            return []
        # Group alternatives by first symbol
        first_sym_groups: dict[str, list[list[str]]] = defaultdict(list)
        for alt in alts:
            if alt:
                first_sym_groups[alt[0]].append(alt)

        best_prefix: list[str] = []
        for sym, group in first_sym_groups.items():
            if len(group) < 2:
                continue
            # Find longest common prefix of this group
            prefix = list(group[0])
            for other in group[1:]:
                new_prefix: list[str] = []
                for a, b in zip(prefix, other):
                    if a == b:
                        new_prefix.append(a)
                    else:
                        break
                prefix = new_prefix
            if len(prefix) > len(best_prefix):
                best_prefix = prefix

        return best_prefix

    def _factor_once(nt: str) -> bool:
        """Factor one common prefix from nt's rules. Returns True if changed."""
        alts = rules_for[nt]
        prefix = _longest_common_prefix(alts)
        if not prefix:
            return False

        plen = len(prefix)
        # Split into: alternatives that start with prefix vs those that don't
        matched: list[list[str]] = []
        unmatched: list[list[str]] = []
        for alt in alts:
            if alt[:plen] == prefix:
                matched.append(alt[plen:])  # remainder after prefix
            else:
                unmatched.append(alt)

        # Create new nonterminal for the factored part
        new_nt = names.fresh(f"{nt}_lf")
        all_nts.append(new_nt)
        rules_for[new_nt] = matched  # A' → β1 | β2 | ...

        # Replace matched alts with prefix + new_nt
        new_alt = list(prefix) + [new_nt]
        rules_for[nt] = unmatched + [new_alt]
        return True

    # Iteratively factor each nonterminal until stable
    # Use a worklist to avoid infinite loops
    max_iterations = 10_000
    iteration = 0
    worklist = list(all_nts)  # nts that may still need factoring
    while worklist and iteration < max_iterations:
        iteration += 1
        nt = worklist.pop(0)
        if nt not in rules_for:
            continue
        changed = _factor_once(nt)
        if changed:
            # Re-add nt (may have more common prefixes) and the newly created nt
            worklist.append(nt)
            worklist.append(all_nts[-1])

    # Build result
    result_rules: list[tuple[str, list[str]]] = []
    for nt in all_nts:
        for rhs in rules_for.get(nt, []):
            result_rules.append((nt, list(rhs)))
    result_rules = _dedup_rules(result_rules)

    return {
        "nonterminals": list(all_nts),
        "terminals": list(terminals),
        "start": start,
        "rules": _rules_to_dicts(result_rules),
    }


# ---------------------------------------------------------------------------
# 4. eliminate_epsilon_rules
# ---------------------------------------------------------------------------

def eliminate_epsilon_rules(grammar: dict) -> dict:
    """Eliminate epsilon rules (A → ε) except for start symbol if language contains ε.

    For each nullable nonterminal A appearing in a rule B → α A β:
    Add rule B → α β (omitting A).
    Remove all A → ε rules (except possibly S → ε if ε ∈ L).

    Language is preserved.
    """
    g = _normalize_grammar(grammar)
    nonterminals: set[str] = set(g["nonterminals"])
    terminals: set[str] = set(g["terminals"])
    start: str = g["start"]
    rules = _rules_list(g)

    nullable = _compute_nullable(rules)
    epsilon_in_language = start in nullable

    # Generate all subsets of positions to omit for nullable symbols
    new_rules_set: set[tuple[str, tuple[str, ...]]] = set()

    for lhs, rhs in rules:
        if len(rhs) == 0:
            # Skip epsilon rules (will re-add for start if needed)
            continue

        nullable_positions = [i for i, s in enumerate(rhs) if s in nullable]
        n = len(nullable_positions)

        for mask in range(1 << n):
            omit: set[int] = set()
            for bit in range(n):
                if mask & (1 << bit):
                    omit.add(nullable_positions[bit])
            new_rhs = tuple(s for i, s in enumerate(rhs) if i not in omit)
            if len(new_rhs) == 0:
                continue  # would produce epsilon; handled separately
            new_rules_set.add((lhs, new_rhs))

    result_rules = [(lhs, list(rhs)) for lhs, rhs in new_rules_set]

    # Re-add S → ε if epsilon is in the language
    if epsilon_in_language:
        result_rules.append((start, []))

    result_rules = _dedup_rules(result_rules)

    return {
        "nonterminals": list(g["nonterminals"]),
        "terminals": list(terminals),
        "start": start,
        "rules": _rules_to_dicts(result_rules),
    }


# ---------------------------------------------------------------------------
# 5. eliminate_unit_rules
# ---------------------------------------------------------------------------

def eliminate_unit_rules(grammar: dict) -> dict:
    """Eliminate unit rules (A → B where B is a nonterminal).

    Uses transitive closure: if A =>* B via unit rules, and B → α (non-unit),
    add A → α.

    Language is preserved.
    """
    g = _normalize_grammar(grammar)
    nonterminals: set[str] = set(g["nonterminals"])
    terminals: set[str] = set(g["terminals"])
    start: str = g["start"]
    rules = _rules_list(g)

    # Separate unit rules from non-unit rules
    unit_pairs: set[tuple[str, str]] = set()
    non_unit: list[tuple[str, list[str]]] = []

    for lhs, rhs in rules:
        if len(rhs) == 1 and rhs[0] in nonterminals:
            unit_pairs.add((lhs, rhs[0]))
        else:
            non_unit.append((lhs, list(rhs)))

    # Transitive closure of unit pairs (with identity: (A, A) for all A)
    closure: set[tuple[str, str]] = {(nt, nt) for nt in nonterminals}
    closure |= unit_pairs

    changed = True
    while changed:
        changed = False
        new_pairs: set[tuple[str, str]] = set()
        for a, b in closure:
            for b2, c in unit_pairs:
                if b == b2 and (a, c) not in closure:
                    new_pairs.add((a, c))
        if new_pairs:
            closure |= new_pairs
            changed = True

    # For each (A, B) in closure, add all non-unit rules of B as rules for A
    result_set: set[tuple[str, tuple[str, ...]]] = set()
    for a, b in closure:
        for lhs, rhs in non_unit:
            if lhs == b:
                result_set.add((a, tuple(rhs)))

    result_rules = _dedup_rules([(lhs, list(rhs)) for lhs, rhs in result_set])

    return {
        "nonterminals": list(g["nonterminals"]),
        "terminals": list(terminals),
        "start": start,
        "rules": _rules_to_dicts(result_rules),
    }


# ---------------------------------------------------------------------------
# 6. to_ll_normal_form
# ---------------------------------------------------------------------------

def to_ll_normal_form(grammar: dict) -> dict:
    """Apply transformations to make grammar suitable for LL parsing attempt.

    Pipeline:
    1. eliminate_epsilon_rules
    2. eliminate_unit_rules
    3. eliminate_left_recursion
    4. left_factor

    Returns transformed grammar. May or may not be LL(k) — caller should
    check with ll_table_builder.check_ll_k() after transformation.

    Note: These transformations preserve the language but may change the
    parse tree structure. This is acceptable when analyzing whether the
    LANGUAGE (not the grammar) is LL.
    """
    g = eliminate_epsilon_rules(grammar)
    g = eliminate_unit_rules(g)
    g = eliminate_left_recursion(g)
    g = left_factor(g)
    return g


# ---------------------------------------------------------------------------
# 7. is_grammar_equivalent_sample  (BFS word generator)
# ---------------------------------------------------------------------------

def _generate_words(grammar: dict, max_len: int) -> set[str]:
    """BFS generation: collect all words up to max_len from grammar.

    Returns a frozenset of terminal strings. Uses sentential-form BFS.
    Limits iterations to avoid blowup.
    """
    g = _normalize_grammar(grammar)
    terminals: set[str] = set(g["terminals"])
    nonterminals: set[str] = set(g["nonterminals"])
    start: str = g["start"]

    # Build rules_for lookup
    rules_for: dict[str, list[list[str]]] = {nt: [] for nt in nonterminals}
    for r in g["rules"]:
        lhs = r["lhs"]
        rhs = _normalize_rhs(r["rhs"])
        if lhs in rules_for:
            rules_for[lhs].append(rhs)

    # BFS over sentential forms (tuples of symbols)
    words: set[str] = set()
    visited: set[tuple[str, ...]] = set()
    queue: deque[tuple[str, ...]] = deque()
    initial = (start,)
    queue.append(initial)
    visited.add(initial)

    max_queue = 50_000
    steps = 0

    while queue and steps < max_queue:
        steps += 1
        sentential = queue.popleft()

        # Check if it's a terminal word
        if all(s in terminals for s in sentential):
            word = "".join(sentential)
            words.add(word)
            continue

        # Find first nonterminal in sentential form
        idx = next((i for i, s in enumerate(sentential) if s in nonterminals), None)
        if idx is None:
            continue

        nt = sentential[idx]
        prefix = sentential[:idx]
        suffix = sentential[idx + 1:]

        # Don't expand if resulting form exceeds max_len terminals
        for rhs in rules_for.get(nt, []):
            new_form = prefix + tuple(rhs) + suffix
            # Prune: if the sentential form already has more terminals than max_len,
            # or if total length exceeds a reasonable bound, skip.
            terminal_count = sum(1 for s in new_form if s in terminals)
            if terminal_count > max_len:
                continue
            # Upper bound: total symbols (over-approximation)
            if len(new_form) > max_len * 3 + 5:
                continue
            if new_form not in visited:
                visited.add(new_form)
                queue.append(new_form)

    return words


def is_grammar_equivalent_sample(
    grammar1: dict,
    grammar2: dict,
    max_len: int = 8,
    alphabet: Optional[list[str]] = None,
) -> tuple[bool, list[str]]:
    """Spot-check that two grammars generate the same words up to length max_len.

    Returns (all_equal, mismatches).
    mismatches: list of words where grammars disagree (up to 5 examples).

    Uses BFS generation from each grammar to collect all words up to max_len.
    This is approximate — not a proof of equivalence.
    """
    words1 = _generate_words(grammar1, max_len)
    words2 = _generate_words(grammar2, max_len)

    mismatches: list[str] = []

    # Words in g1 but not g2
    for w in sorted(words1 - words2):
        mismatches.append(f"+g1:{w!r}")
        if len(mismatches) >= 5:
            break

    # Words in g2 but not g1
    if len(mismatches) < 5:
        for w in sorted(words2 - words1):
            mismatches.append(f"+g2:{w!r}")
            if len(mismatches) >= 5:
                break

    return (len(mismatches) == 0, mismatches)
