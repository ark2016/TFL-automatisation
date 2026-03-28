"""
Grammar analysis utilities for TFL Agent System.

Implements CYK parser, linearity checks, word generation,
and grammar-to-DFA conversion per §4.9 of the spec.
"""

from __future__ import annotations

from collections import deque
from itertools import count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nonterminals(grammar: dict) -> set[str]:
    return set(grammar["nonterminals"])


def _terminals(grammar: dict) -> set[str]:
    return set(grammar["terminals"])


def _is_nt(symbol: str, nonterminals: set[str]) -> bool:
    return symbol in nonterminals


# ---------------------------------------------------------------------------
# 1. Right-linearity check
# ---------------------------------------------------------------------------

def is_right_linear(grammar: dict) -> bool:
    """Check if *every* rule has the form A -> w B  or  A -> w.

    ``w`` is a (possibly empty) string of terminals and ``B`` is a single
    nonterminal.  The left-hand side must be a single nonterminal.
    """
    nts = _nonterminals(grammar)
    ts = _terminals(grammar)

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]  # list of symbols

        if lhs not in nts:
            return False

        # Empty rhs (epsilon) is allowed: A -> ε  (w is empty, no B).
        if not rhs:
            continue

        # Find how many nonterminals are in the rhs and where.
        nt_positions = [i for i, s in enumerate(rhs) if s in nts]

        if len(nt_positions) > 1:
            return False
        if len(nt_positions) == 1:
            # The single nonterminal must be the *last* symbol.
            if nt_positions[0] != len(rhs) - 1:
                return False
            # Everything before it must be terminals.
            if not all(s in ts for s in rhs[:-1]):
                return False
        else:
            # All symbols must be terminals.
            if not all(s in ts for s in rhs):
                return False

    return True


# ---------------------------------------------------------------------------
# 2. Left-linearity check
# ---------------------------------------------------------------------------

def is_left_linear(grammar: dict) -> bool:
    """Check if *every* rule has the form A -> B w  or  A -> w."""
    nts = _nonterminals(grammar)
    ts = _terminals(grammar)

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]

        if lhs not in nts:
            return False

        if not rhs:
            continue

        nt_positions = [i for i, s in enumerate(rhs) if s in nts]

        if len(nt_positions) > 1:
            return False
        if len(nt_positions) == 1:
            if nt_positions[0] != 0:
                return False
            if not all(s in ts for s in rhs[1:]):
                return False
        else:
            if not all(s in ts for s in rhs):
                return False

    return True


# ---------------------------------------------------------------------------
# 3. Nested-recursion detection
# ---------------------------------------------------------------------------

def has_nested_recursion(grammar: dict) -> bool:
    """Detect nested recursion: a nonterminal A such that A =>* x A y
    where x contains at least one terminal and y contains at least one
    terminal (i.e. A is flanked by terminals on both sides in some
    derivation path).

    We build a graph of (nonterminal, has_terminal_left, has_terminal_right)
    reachability and check if any nonterminal can reach itself with both
    flags set.
    """
    nts = _nonterminals(grammar)
    ts = _terminals(grammar)

    # For each rule A -> α, determine for every nonterminal B in α whether
    # there are terminals to its left and/or right within that rule.
    # Then propagate through a fixpoint.

    # State: for each (A, B) pair, track (can_have_terminal_left, can_have_terminal_right)
    # meaning A =>* ... t ... B ... t ... for some derivation.

    # edges[A] = list of (B, has_t_left, has_t_right) from single rule step
    edges: dict[str, list[tuple[str, bool, bool]]] = {nt: [] for nt in nts}

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        if lhs not in nts:
            continue

        for i, sym in enumerate(rhs):
            if sym not in nts:
                continue
            left_part = rhs[:i]
            right_part = rhs[i + 1:]
            has_t_left = any(s in ts for s in left_part)
            has_t_right = any(s in ts for s in right_part)

            # Also track nonterminals to the left/right — they might derive
            # terminals in further steps.  We handle this via propagation.
            edges[lhs].append((sym, has_t_left, has_t_right))

    # reach[(A, B)] = (left_flag, right_flag)
    # meaning A =>* α B β where left_flag means α contains a terminal,
    # right_flag means β contains a terminal (through some derivation).
    reach: dict[tuple[str, str], tuple[bool, bool]] = {}

    # Initialise with direct edges.
    for a in nts:
        reach[(a, a)] = (False, False)  # A =>* A (zero steps)
        for (b, tl, tr) in edges[a]:
            key = (a, b)
            if key in reach:
                old_l, old_r = reach[key]
                reach[key] = (old_l or tl, old_r or tr)
            else:
                reach[key] = (tl, tr)

    # Also: if A -> ... X ... B ... Y ..., then nonterminals X, Y might
    # derive terminals.  We need to know which nonterminals can derive
    # a string containing at least one terminal.
    can_derive_terminal: dict[str, bool] = {}

    def _compute_can_derive_terminal() -> None:
        changed = True
        for nt in nts:
            can_derive_terminal[nt] = False
        while changed:
            changed = False
            for rule in grammar["rules"]:
                lhs = rule["lhs"]
                if lhs not in nts or can_derive_terminal[lhs]:
                    continue
                rhs = rule["rhs"]
                # If rhs contains a terminal, or a nonterminal that can
                # derive a terminal, then lhs can derive a terminal.
                if any(s in ts for s in rhs):
                    can_derive_terminal[lhs] = True
                    changed = True
                elif any(s in nts and can_derive_terminal[s] for s in rhs):
                    can_derive_terminal[lhs] = True
                    changed = True

    _compute_can_derive_terminal()

    # Rebuild edges with refined left/right info using can_derive_terminal.
    for a in nts:
        for rule in grammar["rules"]:
            if rule["lhs"] != a:
                continue
            rhs = rule["rhs"]
            for i, sym in enumerate(rhs):
                if sym not in nts:
                    continue
                left_part = rhs[:i]
                right_part = rhs[i + 1:]

                has_t_left = (
                    any(s in ts for s in left_part)
                    or any(s in nts and can_derive_terminal[s] for s in left_part)
                )
                has_t_right = (
                    any(s in ts for s in right_part)
                    or any(s in nts and can_derive_terminal[s] for s in right_part)
                )

                key = (a, sym)
                if key in reach:
                    old_l, old_r = reach[key]
                    reach[key] = (old_l or has_t_left, old_r or has_t_right)
                else:
                    reach[key] = (has_t_left, has_t_right)

    # Floyd-Warshall–style fixpoint: compose paths.
    changed = True
    while changed:
        changed = False
        for a in nts:
            for b in nts:
                if (a, b) not in reach:
                    continue
                l_ab, r_ab = reach[(a, b)]
                for c in nts:
                    if (b, c) not in reach:
                        continue
                    l_bc, r_bc = reach[(b, c)]
                    # A =>* ... B =>* ... C
                    # left flag for (A,C): left of A->B path, or (right of A->B
                    # has something) no — we combine: the left context of A->C
                    # is anything to the left of C in the whole derivation.
                    # That includes left-of-B in A's derivation PLUS left-of-C
                    # in B's derivation.
                    new_l = l_ab or l_bc
                    new_r = r_ab or r_bc
                    key = (a, c)
                    if key in reach:
                        old_l, old_r = reach[key]
                        if (new_l and not old_l) or (new_r and not old_r):
                            reach[key] = (old_l or new_l, old_r or new_r)
                            changed = True
                    else:
                        reach[key] = (new_l, new_r)
                        changed = True

    # Check: any nonterminal A with reach[(A,A)] having both flags set.
    for a in nts:
        if (a, a) in reach:
            l, r = reach[(a, a)]
            if l and r:
                return True

    return False


# ---------------------------------------------------------------------------
# 4. Word generation (BFS)
# ---------------------------------------------------------------------------

def generate_words(grammar: dict, max_len: int = 12) -> set[str]:
    """Generate all words derivable from the grammar up to *max_len*
    using BFS over sentential forms with leftmost derivation.
    """
    nts = _nonterminals(grammar)
    start = grammar["start"]

    # Group rules by LHS for fast lookup.
    rules_by_lhs: dict[str, list[list[str]]] = {}
    for rule in grammar["rules"]:
        rules_by_lhs.setdefault(rule["lhs"], []).append(rule["rhs"])

    result: set[str] = set()
    initial: tuple[str, ...] = (start,)
    queue: deque[tuple[str, ...]] = deque([initial])
    visited: set[tuple[str, ...]] = {initial}

    while queue:
        form = queue.popleft()

        # Find leftmost nonterminal.
        nt_idx = -1
        for i, sym in enumerate(form):
            if sym in nts:
                nt_idx = i
                break

        if nt_idx == -1:
            # All terminals.
            word = "".join(form)
            if len(word) <= max_len:
                result.add(word)
            continue

        nt_sym = form[nt_idx]
        for rhs in rules_by_lhs.get(nt_sym, []):
            new_form = form[:nt_idx] + tuple(rhs) + form[nt_idx + 1:]

            # Count terminals already present to prune early.
            terminal_count = sum(1 for s in new_form if s not in nts)
            if terminal_count > max_len:
                continue

            if new_form not in visited:
                visited.add(new_form)
                queue.append(new_form)

    return result


# ---------------------------------------------------------------------------
# 5. Conversion to Chomsky Normal Form
# ---------------------------------------------------------------------------

def to_cnf(grammar: dict) -> dict:
    """Convert grammar to Chomsky Normal Form.

    Steps:
    1. Eliminate ε-productions (preserve if start derives ε).
    2. Eliminate unit productions (A -> B).
    3. Convert to A -> BC | A -> a form.

    Returns a new grammar dict.
    """
    nts = set(grammar["nonterminals"])
    ts = set(grammar["terminals"])
    start = grammar["start"]
    rules: list[tuple[str, list[str]]] = [
        (r["lhs"], list(r["rhs"])) for r in grammar["rules"]
    ]

    # ---- Step 0: Collect helper ------------------------------------------
    def _rules_as_set(
        rs: list[tuple[str, list[str]]],
    ) -> set[tuple[str, tuple[str, ...]]]:
        return {(lhs, tuple(rhs)) for lhs, rhs in rs}

    # ---- Step 1: Eliminate ε-productions ---------------------------------
    # Find nullable nonterminals.
    nullable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if lhs in nullable:
                continue
            # rhs is ε  or all symbols in rhs are nullable.
            if not rhs or all(s in nullable for s in rhs):
                nullable.add(lhs)
                changed = True

    start_derives_eps = start in nullable

    # For every rule, generate all combinations with nullable symbols
    # either present or absent, then remove pure ε-productions.
    new_rules_set: set[tuple[str, tuple[str, ...]]] = set()
    for lhs, rhs in rules:
        # Positions of nullable symbols.
        nullable_pos = [i for i, s in enumerate(rhs) if s in nullable]
        # Generate all subsets of nullable positions to "remove".
        for mask in range(1 << len(nullable_pos)):
            removed = set()
            for bit_idx, pos in enumerate(nullable_pos):
                if mask & (1 << bit_idx):
                    removed.add(pos)
            new_rhs = tuple(s for i, s in enumerate(rhs) if i not in removed)
            if new_rhs:  # Don't add ε-productions.
                new_rules_set.add((lhs, new_rhs))

    # If start derives ε, we add a new start symbol.
    if start_derives_eps:
        new_start = start + "'"
        while new_start in nts:
            new_start += "'"
        nts.add(new_start)
        new_rules_set.add((new_start, (start,)))
        new_rules_set.add((new_start, ()))  # ε-production only for new start
        start = new_start

    rules = [(lhs, list(rhs)) for lhs, rhs in new_rules_set]

    # ---- Step 2: Eliminate unit productions (A -> B) ---------------------
    # Build unit-pair closure.
    unit_pairs: dict[str, set[str]] = {nt: {nt} for nt in nts}
    changed = True
    while changed:
        changed = False
        for lhs, rhs in rules:
            if len(rhs) == 1 and rhs[0] in nts:
                b = rhs[0]
                for c in list(unit_pairs.get(b, set())):
                    if c not in unit_pairs.setdefault(lhs, set()):
                        unit_pairs[lhs].add(c)
                        changed = True

    non_unit_rules: list[tuple[str, list[str]]] = [
        (lhs, rhs)
        for lhs, rhs in rules
        if not (len(rhs) == 1 and rhs[0] in nts)
    ]

    expanded: set[tuple[str, tuple[str, ...]]] = set()
    for a in nts:
        for b in unit_pairs.get(a, {a}):
            for lhs, rhs in non_unit_rules:
                if lhs == b:
                    expanded.add((a, tuple(rhs)))

    rules = [(lhs, list(rhs)) for lhs, rhs in expanded]

    # ---- Step 3: Convert to proper CNF -----------------------------------
    # 3a. For rules with |rhs| >= 2, replace every terminal 'a' with a
    #     fresh nonterminal T_a -> a.
    terminal_nt: dict[str, str] = {}
    extra_rules: list[tuple[str, list[str]]] = []
    _counter = count()

    def _get_terminal_nt(t: str) -> str:
        if t not in terminal_nt:
            name = f"_T_{t}_{next(_counter)}"
            while name in nts:
                name = f"_T_{t}_{next(_counter)}"
            terminal_nt[t] = name
            nts.add(name)
            extra_rules.append((name, [t]))
        return terminal_nt[t]

    cnf_rules: list[tuple[str, list[str]]] = []
    for lhs, rhs in rules:
        if not rhs:
            # Only allowed for new start (ε).
            cnf_rules.append((lhs, rhs))
            continue
        if len(rhs) == 1:
            # Must be a terminal (unit NTs removed above).
            cnf_rules.append((lhs, rhs))
            continue
        # Replace terminals in rhs with wrapper nonterminals.
        new_rhs = [
            _get_terminal_nt(s) if s in ts else s for s in rhs
        ]
        # 3b. Break long rules into binary chains.
        while len(new_rhs) > 2:
            right_part = new_rhs[-2:]
            new_nt = f"_B_{next(_counter)}"
            while new_nt in nts:
                new_nt = f"_B_{next(_counter)}"
            nts.add(new_nt)
            extra_rules.append((new_nt, right_part))
            new_rhs = new_rhs[:-2] + [new_nt]
        cnf_rules.append((lhs, new_rhs))

    cnf_rules.extend(extra_rules)

    # Build output grammar.
    all_nts = sorted(nts)
    return {
        "terminals": sorted(ts),
        "nonterminals": all_nts,
        "start": start,
        "rules": [{"lhs": lhs, "rhs": rhs} for lhs, rhs in cnf_rules],
    }


# ---------------------------------------------------------------------------
# 6. CYK membership test
# ---------------------------------------------------------------------------

def cyk_parse(grammar: dict, word: str) -> bool:
    """CYK membership test.  Converts to CNF first if needed."""
    cnf = to_cnf(grammar)
    nts = set(cnf["nonterminals"])
    start = cnf["start"]
    rules = cnf["rules"]

    # Special case: empty word.
    if not word:
        for rule in rules:
            if rule["lhs"] == start and rule["rhs"] == []:
                return True
        return False

    n = len(word)

    # Build lookup structures.
    # unit_map: terminal -> set of nonterminals
    unit_map: dict[str, set[str]] = {}
    # pair_map: (B, C) -> set of nonterminals A  where A -> B C
    pair_map: dict[tuple[str, str], set[str]] = {}

    for rule in rules:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        if len(rhs) == 1 and rhs[0] not in nts:
            unit_map.setdefault(rhs[0], set()).add(lhs)
        elif len(rhs) == 2:
            pair_map.setdefault((rhs[0], rhs[1]), set()).add(lhs)

    # T[i][j] = set of nonterminals deriving word[i:j+1].
    T: list[list[set[str]]] = [[set() for _ in range(n)] for _ in range(n)]

    # Fill diagonal.
    for i in range(n):
        T[i][i] = unit_map.get(word[i], set()).copy()

    # Fill table bottom-up by span length.
    for span in range(2, n + 1):
        for i in range(n - span + 1):
            j = i + span - 1
            for k in range(i, j):
                for b in T[i][k]:
                    for c in T[k + 1][j]:
                        for a in pair_map.get((b, c), set()):
                            T[i][j].add(a)

    return start in T[0][n - 1]


# ---------------------------------------------------------------------------
# 7. Grammar → DFA (for linear grammars only)
# ---------------------------------------------------------------------------

def grammar_to_dfa(grammar: dict) -> dict | None:
    """Convert a right-linear or left-linear grammar to a DFA.

    Returns ``None`` if the grammar is neither right-linear nor left-linear.
    """
    if is_right_linear(grammar):
        return _right_linear_to_dfa(grammar)
    if is_left_linear(grammar):
        return _left_linear_to_dfa(grammar)
    return None


def _right_linear_to_dfa(grammar: dict) -> dict:
    """Convert a right-linear grammar to a DFA via NFA then subset construction."""
    nts = _nonterminals(grammar)
    ts = _terminals(grammar)
    start = grammar["start"]
    accept_state = "_accept"

    # Build NFA transitions:  state --(symbol)--> set of states
    nfa_trans: dict[str, dict[str, set[str]]] = {nt: {} for nt in nts}
    nfa_trans[accept_state] = {}

    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        if not rhs:
            # A -> ε : lhs is accepting
            # We add an ε-move or simply mark; handle via accept set below.
            nfa_trans.setdefault(lhs, {}).setdefault("", set()).add(accept_state)
            continue

        nt_positions = [i for i, s in enumerate(rhs) if s in nts]
        if nt_positions:
            # A -> w B : consume w character by character via chain states.
            target_nt = rhs[-1]
            terminals_seq = rhs[:-1]
        else:
            # A -> w : consume w then go to accept.
            target_nt = accept_state
            terminals_seq = rhs

        # Build chain of states for multi-character terminal strings.
        current = lhs
        for idx, t in enumerate(terminals_seq):
            if idx < len(terminals_seq) - 1:
                # Intermediate state.
                inter = f"_q_{lhs}_{id(rule)}_{idx}"
                nfa_trans.setdefault(current, {}).setdefault(t, set()).add(inter)
                nfa_trans.setdefault(inter, {})
                current = inter
            else:
                nfa_trans.setdefault(current, {}).setdefault(t, set()).add(
                    target_nt
                )

        if not terminals_seq:
            # A -> B (unit rule among nonterminals — treat as ε-move)
            nfa_trans.setdefault(lhs, {}).setdefault("", set()).add(target_nt)

    # ε-closure helper
    def eps_closure(states: frozenset[str]) -> frozenset[str]:
        stack = list(states)
        closure = set(states)
        while stack:
            s = stack.pop()
            for t in nfa_trans.get(s, {}).get("", set()):
                if t not in closure:
                    closure.add(t)
                    stack.append(t)
        return frozenset(closure)

    # Subset construction (NFA → DFA).
    alphabet = sorted(ts)
    start_set = eps_closure(frozenset([start]))

    dfa_states: dict[frozenset[str], str] = {}
    state_counter = count()

    def state_name(fs: frozenset[str]) -> str:
        if fs not in dfa_states:
            dfa_states[fs] = f"q{next(state_counter)}"
        return dfa_states[fs]

    dfa_start = state_name(start_set)
    dfa_transitions: dict[str, dict[str, str]] = {}
    dfa_accept: list[str] = []
    queue: deque[frozenset[str]] = deque([start_set])
    visited_dfa: set[frozenset[str]] = {start_set}

    dead_state = "_dead"

    while queue:
        current_set = queue.popleft()
        cname = state_name(current_set)
        dfa_transitions[cname] = {}

        for sym in alphabet:
            next_states: set[str] = set()
            for s in current_set:
                for t in nfa_trans.get(s, {}).get(sym, set()):
                    next_states.add(t)
            next_closed = eps_closure(frozenset(next_states))

            if not next_closed:
                dfa_transitions[cname][sym] = dead_state
            else:
                nname = state_name(next_closed)
                dfa_transitions[cname][sym] = nname
                if next_closed not in visited_dfa:
                    visited_dfa.add(next_closed)
                    queue.append(next_closed)

    # Mark accept states.
    for fs, name in dfa_states.items():
        if accept_state in fs:
            dfa_accept.append(name)

    # Add dead state transitions.
    all_state_names = sorted(dfa_states.values()) + [dead_state]
    dfa_transitions[dead_state] = {sym: dead_state for sym in alphabet}

    return {
        "states": all_state_names,
        "alphabet": alphabet,
        "transitions": dfa_transitions,
        "start": dfa_start,
        "accept": sorted(dfa_accept),
    }


def _left_linear_to_dfa(grammar: dict) -> dict:
    """Convert a left-linear grammar to a DFA by reversing it to
    right-linear, building the DFA, and then reversing the DFA
    (reverse → determinise).

    Simpler approach: convert left-linear to right-linear by reversing
    each rule, build DFA for the reversed language, then reverse the DFA.
    """
    nts = set(grammar["nonterminals"])
    ts = set(grammar["terminals"])

    # Reverse each rule: A -> B w  becomes  A -> w^R B
    # (swap to right-linear for the reversed language).
    reversed_rules: list[dict] = []
    for rule in grammar["rules"]:
        lhs = rule["lhs"]
        rhs = list(rule["rhs"])
        if not rhs:
            reversed_rules.append({"lhs": lhs, "rhs": []})
            continue

        if rhs[0] in nts:
            # A -> B w  =>  A -> w^R B  (right-linear for L^R)
            nt_part = rhs[0]
            terminal_part = rhs[1:]
            reversed_rules.append(
                {"lhs": lhs, "rhs": list(reversed(terminal_part)) + [nt_part]}
            )
        else:
            # A -> w  =>  A -> w^R
            reversed_rules.append({"lhs": lhs, "rhs": list(reversed(rhs))})

    reversed_grammar = {
        "terminals": grammar["terminals"],
        "nonterminals": grammar["nonterminals"],
        "start": grammar["start"],
        "rules": reversed_rules,
    }

    # Build DFA for L^R.
    dfa_rev = _right_linear_to_dfa(reversed_grammar)

    # Reverse the DFA to get NFA for L, then determinise.
    return _reverse_and_determinise_dfa(dfa_rev)


def _reverse_and_determinise_dfa(dfa: dict) -> dict:
    """Reverse a DFA (swap start/accept, reverse transitions) and
    determinise the resulting NFA via subset construction.
    """
    alphabet = dfa["alphabet"]
    old_start = dfa["start"]
    old_accept = set(dfa["accept"])
    old_trans = dfa["transitions"]

    # Build reversed NFA transitions.
    nfa_trans: dict[str, dict[str, set[str]]] = {
        s: {sym: set() for sym in alphabet} for s in dfa["states"]
    }
    for src, sym_map in old_trans.items():
        for sym, dst in sym_map.items():
            nfa_trans.setdefault(dst, {}).setdefault(sym, set()).add(src)

    # New start = set of old accept states.
    new_start_set = frozenset(old_accept)
    # New accept = any set containing old start.

    state_map: dict[frozenset[str], str] = {}
    counter = count()

    def name(fs: frozenset[str]) -> str:
        if fs not in state_map:
            state_map[fs] = f"q{next(counter)}"
        return state_map[fs]

    dead = "_dead"
    transitions: dict[str, dict[str, str]] = {}
    accept: list[str] = []

    queue: deque[frozenset[str]] = deque([new_start_set])
    visited: set[frozenset[str]] = {new_start_set}

    while queue:
        cur = queue.popleft()
        cname = name(cur)
        transitions[cname] = {}
        for sym in alphabet:
            nxt: set[str] = set()
            for s in cur:
                nxt |= nfa_trans.get(s, {}).get(sym, set())
            nxt_fs = frozenset(nxt)
            if not nxt_fs:
                transitions[cname][sym] = dead
            else:
                transitions[cname][sym] = name(nxt_fs)
                if nxt_fs not in visited:
                    visited.add(nxt_fs)
                    queue.append(nxt_fs)

    # Accept states: those containing the old start.
    for fs, n in state_map.items():
        if old_start in fs:
            accept.append(n)

    all_states = sorted(state_map.values()) + [dead]
    transitions[dead] = {sym: dead for sym in alphabet}

    return {
        "states": all_states,
        "alphabet": alphabet,
        "transitions": transitions,
        "start": name(new_start_set),
        "accept": sorted(accept),
    }
