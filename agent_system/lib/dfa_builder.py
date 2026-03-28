"""
DFA builder — regex to minimized DFA via Thompson + subset construction + Hopcroft.

Per §4.5 of the TFL Agent System spec.

Pipeline: regex string -> AST -> ε-NFA (Thompson) -> DFA (subset construction) -> minimized DFA (Hopcroft).
"""

from __future__ import annotations

from collections import deque
from typing import Any


# ---------------------------------------------------------------------------
# 1. AST node types
# ---------------------------------------------------------------------------

class _ASTNode:
    """Base class for regex AST nodes."""
    pass


class Literal(_ASTNode):
    __slots__ = ("char",)

    def __init__(self, char: str) -> None:
        self.char = char

    def __repr__(self) -> str:
        return f"Literal({self.char!r})"


class Epsilon(_ASTNode):
    def __repr__(self) -> str:
        return "Epsilon()"


class Concat(_ASTNode):
    __slots__ = ("left", "right")

    def __init__(self, left: _ASTNode, right: _ASTNode) -> None:
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"Concat({self.left!r}, {self.right!r})"


class Union(_ASTNode):
    __slots__ = ("left", "right")

    def __init__(self, left: _ASTNode, right: _ASTNode) -> None:
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"Union({self.left!r}, {self.right!r})"


class Star(_ASTNode):
    __slots__ = ("child",)

    def __init__(self, child: _ASTNode) -> None:
        self.child = child

    def __repr__(self) -> str:
        return f"Star({self.child!r})"


class Plus(_ASTNode):
    __slots__ = ("child",)

    def __init__(self, child: _ASTNode) -> None:
        self.child = child

    def __repr__(self) -> str:
        return f"Plus({self.child!r})"


class Optional(_ASTNode):
    __slots__ = ("child",)

    def __init__(self, child: _ASTNode) -> None:
        self.child = child

    def __repr__(self) -> str:
        return f"Optional({self.child!r})"


# ---------------------------------------------------------------------------
# 2. Regex parser  (precedence: * > concat > |)
# ---------------------------------------------------------------------------

class _RegexParser:
    """Recursive-descent parser for regular expressions.

    Grammar (from highest to lowest precedence):
        expr     -> concat ('|' concat)*
        concat   -> unary+
        unary    -> atom ('*' | '+' | '?')*
        atom     -> '(' expr ')' | literal_char
    """

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self._pos = 0

    def _peek(self) -> str | None:
        if self._pos < len(self._pattern):
            return self._pattern[self._pos]
        return None

    def _advance(self) -> str:
        ch = self._pattern[self._pos]
        self._pos += 1
        return ch

    def parse(self) -> _ASTNode:
        if len(self._pattern) == 0:
            return Epsilon()
        node = self._expr()
        if self._pos < len(self._pattern):
            raise ValueError(
                f"Unexpected character {self._pattern[self._pos]!r} "
                f"at position {self._pos}"
            )
        return node

    def _expr(self) -> _ASTNode:
        node = self._concat()
        while self._peek() == "|":
            self._advance()  # consume '|'
            right = self._concat()
            node = Union(node, right)
        return node

    def _concat(self) -> _ASTNode:
        parts: list[_ASTNode] = []
        while self._peek() is not None and self._peek() not in ("|", ")"):
            parts.append(self._unary())
        if len(parts) == 0:
            return Epsilon()
        node = parts[0]
        for p in parts[1:]:
            node = Concat(node, p)
        return node

    def _unary(self) -> _ASTNode:
        node = self._atom()
        while self._peek() in ("*", "+", "?"):
            op = self._advance()
            if op == "*":
                node = Star(node)
            elif op == "+":
                node = Plus(node)
            else:
                node = Optional(node)
        return node

    def _atom(self) -> _ASTNode:
        ch = self._peek()
        if ch == "(":
            self._advance()  # consume '('
            node = self._expr()
            if self._peek() != ")":
                raise ValueError(
                    f"Expected ')' at position {self._pos}"
                )
            self._advance()  # consume ')'
            return node
        if ch == "\\":
            self._advance()  # consume backslash
            if self._peek() is None:
                raise ValueError("Trailing backslash in regex")
            return Literal(self._advance())
        if ch is None or ch in ("|", ")", "*", "+", "?"):
            raise ValueError(
                f"Unexpected {ch!r} at position {self._pos}"
            )
        return Literal(self._advance())


def parse_regex(pattern: str) -> _ASTNode:
    """Parse a regex string into an AST."""
    return _RegexParser(pattern).parse()


# ---------------------------------------------------------------------------
# 3. Thompson's construction  (AST -> ε-NFA)
# ---------------------------------------------------------------------------

class _NFAFragment:
    """A fragment of an ε-NFA with a single start and single accept state."""
    __slots__ = ("start", "accept")

    def __init__(self, start: int, accept: int) -> None:
        self.start = start
        self.accept = accept


class _ThompsonBuilder:
    """Builds an ε-NFA from a regex AST using Thompson's construction."""

    def __init__(self) -> None:
        self._next_state = 0
        # transitions[state] = list of (symbol_or_None, target)
        # None represents ε-transition
        self.transitions: dict[int, list[tuple[str | None, int]]] = {}
        self.alphabet: set[str] = set()

    def _new_state(self) -> int:
        s = self._next_state
        self._next_state += 1
        self.transitions[s] = []
        return s

    def _add_transition(self, src: int, symbol: str | None, dst: int) -> None:
        self.transitions[src].append((symbol, dst))
        if symbol is not None:
            self.alphabet.add(symbol)

    def build(self, node: _ASTNode) -> _NFAFragment:
        if isinstance(node, Literal):
            s = self._new_state()
            a = self._new_state()
            self._add_transition(s, node.char, a)
            return _NFAFragment(s, a)

        if isinstance(node, Epsilon):
            s = self._new_state()
            a = self._new_state()
            self._add_transition(s, None, a)
            return _NFAFragment(s, a)

        if isinstance(node, Concat):
            left = self.build(node.left)
            right = self.build(node.right)
            # merge left.accept with right.start via ε
            self._add_transition(left.accept, None, right.start)
            return _NFAFragment(left.start, right.accept)

        if isinstance(node, Union):
            s = self._new_state()
            a = self._new_state()
            left = self.build(node.left)
            right = self.build(node.right)
            self._add_transition(s, None, left.start)
            self._add_transition(s, None, right.start)
            self._add_transition(left.accept, None, a)
            self._add_transition(right.accept, None, a)
            return _NFAFragment(s, a)

        if isinstance(node, Star):
            s = self._new_state()
            a = self._new_state()
            child = self.build(node.child)
            self._add_transition(s, None, child.start)
            self._add_transition(s, None, a)
            self._add_transition(child.accept, None, child.start)
            self._add_transition(child.accept, None, a)
            return _NFAFragment(s, a)

        if isinstance(node, Plus):
            # child · child*  — desugar
            child_once = self.build(node.child)
            child_star = self.build(Star(node.child))
            self._add_transition(child_once.accept, None, child_star.start)
            return _NFAFragment(child_once.start, child_star.accept)

        if isinstance(node, Optional):
            # ε | child  — desugar
            s = self._new_state()
            a = self._new_state()
            child = self.build(node.child)
            self._add_transition(s, None, child.start)
            self._add_transition(s, None, a)
            self._add_transition(child.accept, None, a)
            return _NFAFragment(s, a)

        raise TypeError(f"Unknown AST node type: {type(node)}")  # pragma: no cover


def _thompson(ast: _ASTNode) -> tuple[dict[int, list[tuple[str | None, int]]], int, int, set[str]]:
    """Run Thompson's construction. Returns (transitions, start, accept, alphabet)."""
    builder = _ThompsonBuilder()
    frag = builder.build(ast)
    return builder.transitions, frag.start, frag.accept, builder.alphabet


# ---------------------------------------------------------------------------
# 4. Subset construction  (ε-NFA -> DFA)
# ---------------------------------------------------------------------------

def _epsilon_closure(
    states: frozenset[int],
    transitions: dict[int, list[tuple[str | None, int]]],
) -> frozenset[int]:
    """Compute the ε-closure of a set of NFA states."""
    stack = list(states)
    closure = set(states)
    while stack:
        s = stack.pop()
        for symbol, target in transitions.get(s, []):
            if symbol is None and target not in closure:
                closure.add(target)
                stack.append(target)
    return frozenset(closure)


def _move(
    states: frozenset[int],
    symbol: str,
    transitions: dict[int, list[tuple[str | None, int]]],
) -> frozenset[int]:
    """Compute the set of states reachable from *states* on *symbol* (no ε)."""
    result: set[int] = set()
    for s in states:
        for sym, target in transitions.get(s, []):
            if sym == symbol:
                result.add(target)
    return frozenset(result)


def _subset_construction(
    nfa_transitions: dict[int, list[tuple[str | None, int]]],
    nfa_start: int,
    nfa_accept: int,
    alphabet: set[str],
) -> dict[str, Any]:
    """Convert an ε-NFA (from Thompson) to a DFA via subset construction."""
    sorted_alphabet = sorted(alphabet)

    start_closure = _epsilon_closure(frozenset({nfa_start}), nfa_transitions)

    # Map frozenset -> state name
    state_map: dict[frozenset[int], str] = {}
    counter = 0

    def _get_name(fs: frozenset[int]) -> str:
        nonlocal counter
        if fs not in state_map:
            state_map[fs] = f"q{counter}"
            counter += 1
        return state_map[fs]

    start_name = _get_name(start_closure)

    dfa_transitions: dict[str, dict[str, str]] = {}
    queue: deque[frozenset[int]] = deque([start_closure])
    visited: set[frozenset[int]] = {start_closure}

    while queue:
        current = queue.popleft()
        current_name = _get_name(current)
        dfa_transitions[current_name] = {}
        for sym in sorted_alphabet:
            moved = _move(current, sym, nfa_transitions)
            target = _epsilon_closure(moved, nfa_transitions)
            if not target:
                # dead state — will be added later if needed
                target_name = _get_name(frozenset())
                if frozenset() not in visited:
                    visited.add(frozenset())
                    queue.append(frozenset())
            else:
                target_name = _get_name(target)
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
            dfa_transitions[current_name][sym] = target_name

    # Determine accept states
    accept_states: list[str] = []
    for fs, name in state_map.items():
        if nfa_accept in fs:
            accept_states.append(name)

    all_states = sorted(state_map.values(), key=lambda x: int(x[1:]))

    # Ensure dead state has transitions too
    for s in all_states:
        if s not in dfa_transitions:
            dfa_transitions[s] = {sym: s for sym in sorted_alphabet}

    return {
        "states": all_states,
        "alphabet": sorted_alphabet,
        "transitions": dfa_transitions,
        "start": start_name,
        "accept": sorted(accept_states, key=lambda x: int(x[1:])),
    }


# ---------------------------------------------------------------------------
# 5. Hopcroft minimization
# ---------------------------------------------------------------------------

def minimize_dfa(dfa: dict[str, Any]) -> dict[str, Any]:
    """Minimize a DFA using Hopcroft's algorithm.

    Takes and returns a DFA dict in the standard format:
    {
        "states": [...], "alphabet": [...],
        "transitions": {...}, "start": "q0", "accept": [...]
    }
    """
    states = set(dfa["states"])
    alphabet = list(dfa["alphabet"])
    transitions = dfa["transitions"]
    start = dfa["start"]
    accept = set(dfa["accept"])

    # --- Step 0: Remove unreachable states ---
    reachable: set[str] = set()
    queue: deque[str] = deque([start])
    reachable.add(start)
    while queue:
        s = queue.popleft()
        for sym in alphabet:
            t = transitions.get(s, {}).get(sym)
            if t is not None and t not in reachable:
                reachable.add(t)
                queue.append(t)

    states = states & reachable
    accept = accept & reachable

    if not states:
        # Degenerate: no states at all
        return {
            "states": ["q0"],
            "alphabet": alphabet,
            "transitions": {"q0": {sym: "q0" for sym in alphabet}},
            "start": "q0",
            "accept": [],
        }

    # --- Step 1: Initial partition {accept, non-accept} ---
    non_accept = states - accept
    partition: list[set[str]] = []
    if accept:
        partition.append(set(accept))
    if non_accept:
        partition.append(set(non_accept))
    if not partition:
        partition.append(set(states))

    # Build a map: state -> partition index (for quick lookup)
    def _state_to_block(st: str) -> int:
        for i, block in enumerate(partition):
            if st in block:
                return i
        return -1  # pragma: no cover

    # --- Step 2: Refine ---
    changed = True
    while changed:
        changed = False
        new_partition: list[set[str]] = []
        for block in partition:
            if len(block) <= 1:
                new_partition.append(block)
                continue
            # Try to split this block
            # Pick a representative
            rep = next(iter(block))
            same: set[str] = {rep}
            diff: set[str] = set()
            for s in block:
                if s == rep:
                    continue
                equivalent = True
                for sym in alphabet:
                    rep_target = transitions.get(rep, {}).get(sym)
                    s_target = transitions.get(s, {}).get(sym)
                    if _state_to_block(rep_target) != _state_to_block(s_target) if (rep_target and s_target) else (rep_target != s_target):
                        equivalent = False
                        break
                if equivalent:
                    same.add(s)
                else:
                    diff.add(s)
            new_partition.append(same)
            if diff:
                new_partition.append(diff)
                changed = True
        partition = new_partition

    # If a split produced something with diff but those diffs also need splitting,
    # keep iterating. The outer while-changed handles this.

    # --- Step 3: Further refine diffs against each other ---
    # The above loop may leave blocks that still contain distinguishable states
    # (the "diff" bucket groups all non-equivalent-to-rep states together).
    # We need to keep refining until stable.
    # Re-run the full refinement loop until truly stable.
    stable = False
    while not stable:
        stable = True
        refined: list[set[str]] = []
        for block in partition:
            if len(block) <= 1:
                refined.append(block)
                continue
            # Group states by their transition signature
            sig_map: dict[tuple[int, ...], set[str]] = {}
            for s in block:
                sig_parts: list[int] = []
                for sym in alphabet:
                    t = transitions.get(s, {}).get(sym)
                    # Find which block t belongs to
                    found = -1
                    for i, b in enumerate(partition):
                        if t in b:
                            found = i
                            break
                    sig_parts.append(found)
                sig = tuple(sig_parts)
                if sig not in sig_map:
                    sig_map[sig] = set()
                sig_map[sig].add(s)
            groups = list(sig_map.values())
            refined.extend(groups)
            if len(groups) > 1:
                stable = False
        partition = refined

    # --- Step 4: Build minimized DFA ---
    # Assign new names to blocks
    block_names: dict[int, str] = {}
    # Find the block containing the start state first so it gets q0
    start_block_idx = -1
    for i, block in enumerate(partition):
        if start in block:
            start_block_idx = i
            break

    counter = 0
    block_names[start_block_idx] = f"q{counter}"
    counter += 1
    for i in range(len(partition)):
        if i != start_block_idx:
            block_names[i] = f"q{counter}"
            counter += 1

    # State -> block index
    state_block: dict[str, int] = {}
    for i, block in enumerate(partition):
        for s in block:
            state_block[s] = i

    new_transitions: dict[str, dict[str, str]] = {}
    new_accept: list[str] = []
    new_states: list[str] = []

    for i, block in enumerate(partition):
        name = block_names[i]
        new_states.append(name)
        rep = next(iter(block))
        new_transitions[name] = {}
        for sym in alphabet:
            t = transitions.get(rep, {}).get(sym)
            if t is not None:
                new_transitions[name][sym] = block_names[state_block[t]]
            else:
                new_transitions[name][sym] = name  # self-loop on missing
        # Check if this block contains an accept state
        if block & accept:
            new_accept.append(name)

    new_states.sort(key=lambda x: int(x[1:]))
    new_accept.sort(key=lambda x: int(x[1:]))

    return {
        "states": new_states,
        "alphabet": alphabet,
        "transitions": new_transitions,
        "start": block_names[start_block_idx],
        "accept": new_accept,
    }


# ---------------------------------------------------------------------------
# 6. nfa_to_dfa  (public helper)
# ---------------------------------------------------------------------------

def nfa_to_dfa(nfa: dict[str, Any]) -> dict[str, Any]:
    """Convert an NFA dict to a DFA dict via subset construction.

    NFA format:
    {
        "states": ["s0", "s1", ...],
        "alphabet": ["a", "b"],
        "transitions": {"s0": {"a": ["s1", "s2"], "b": ["s0"]}, ...},
        "epsilon_transitions": {"s0": ["s1"], ...},
        "start": "s0",
        "accept": ["s2"]
    }
    """
    alphabet = list(nfa["alphabet"])
    nfa_states = nfa["states"]
    nfa_trans = nfa["transitions"]
    eps_trans = nfa.get("epsilon_transitions", {})
    nfa_start = nfa["start"]
    nfa_accept_set = set(nfa["accept"])

    # Build internal transition table keyed by int for efficiency
    name_to_id: dict[str, int] = {name: i for i, name in enumerate(nfa_states)}
    id_to_name: dict[int, str] = {i: name for name, i in name_to_id.items()}

    # transitions_int[state_id] = list of (symbol_or_None, target_id)
    transitions_int: dict[int, list[tuple[str | None, int]]] = {
        i: [] for i in range(len(nfa_states))
    }
    for src, sym_map in nfa_trans.items():
        src_id = name_to_id[src]
        for sym, targets in sym_map.items():
            if isinstance(targets, list):
                for t in targets:
                    transitions_int[src_id].append((sym, name_to_id[t]))
            else:
                # Single target string (tolerate)
                transitions_int[src_id].append((sym, name_to_id[targets]))

    for src, targets in eps_trans.items():
        src_id = name_to_id[src]
        if isinstance(targets, list):
            for t in targets:
                transitions_int[src_id].append((None, name_to_id[t]))
        else:
            transitions_int[src_id].append((None, name_to_id[targets]))

    # Find accept ids
    accept_ids = {name_to_id[a] for a in nfa_accept_set}

    # Use the internal subset construction
    sorted_alph = sorted(alphabet)
    start_id = name_to_id[nfa_start]
    start_closure = _epsilon_closure(frozenset({start_id}), transitions_int)

    state_map: dict[frozenset[int], str] = {}
    counter = 0

    def _get_name(fs: frozenset[int]) -> str:
        nonlocal counter
        if fs not in state_map:
            state_map[fs] = f"q{counter}"
            counter += 1
        return state_map[fs]

    start_name = _get_name(start_closure)
    dfa_transitions: dict[str, dict[str, str]] = {}
    bfs_queue: deque[frozenset[int]] = deque([start_closure])
    visited: set[frozenset[int]] = {start_closure}

    while bfs_queue:
        current = bfs_queue.popleft()
        current_name = _get_name(current)
        dfa_transitions[current_name] = {}
        for sym in sorted_alph:
            moved = _move(current, sym, transitions_int)
            target = _epsilon_closure(moved, transitions_int)
            if not target:
                target_name = _get_name(frozenset())
                if frozenset() not in visited:
                    visited.add(frozenset())
                    bfs_queue.append(frozenset())
            else:
                target_name = _get_name(target)
                if target not in visited:
                    visited.add(target)
                    bfs_queue.append(target)
            dfa_transitions[current_name][sym] = target_name

    accept_states: list[str] = []
    for fs, name in state_map.items():
        if fs & accept_ids:
            accept_states.append(name)

    all_states = sorted(state_map.values(), key=lambda x: int(x[1:]))

    for s in all_states:
        if s not in dfa_transitions:
            dfa_transitions[s] = {sym: s for sym in sorted_alph}

    return {
        "states": all_states,
        "alphabet": sorted_alph,
        "transitions": dfa_transitions,
        "start": start_name,
        "accept": sorted(accept_states, key=lambda x: int(x[1:])),
    }


# ---------------------------------------------------------------------------
# 7. Main entry point
# ---------------------------------------------------------------------------

def build_dfa_from_regex(regex: str) -> dict[str, Any]:
    """Build a minimized DFA from a regex string.

    Supported syntax:
        a, b, ...   literal characters
        |           alternation
        *           Kleene star
        +           one or more
        ?           zero or one
        ()          grouping
        \\          escape next character

    Returns a DFA dict:
    {
        "states": ["q0", "q1", ...],
        "alphabet": ["a", "b"],
        "transitions": {"q0": {"a": "q1", ...}, ...},
        "start": "q0",
        "accept": ["q1", ...]
    }
    """
    ast = parse_regex(regex)
    nfa_trans, nfa_start, nfa_accept, alphabet = _thompson(ast)

    if not alphabet:
        # Regex matches only ε (empty string) or nothing.
        # Return a minimal DFA with an empty alphabet.
        # Check if ε is accepted by seeing if accept is in ε-closure of start.
        closure = _epsilon_closure(frozenset({nfa_start}), nfa_trans)
        accepts_epsilon = nfa_accept in closure
        if accepts_epsilon:
            return {
                "states": ["q0"],
                "alphabet": [],
                "transitions": {"q0": {}},
                "start": "q0",
                "accept": ["q0"],
            }
        else:
            return {
                "states": ["q0"],
                "alphabet": [],
                "transitions": {"q0": {}},
                "start": "q0",
                "accept": [],
            }

    raw_dfa = _subset_construction(nfa_trans, nfa_start, nfa_accept, alphabet)
    return minimize_dfa(raw_dfa)


# ---------------------------------------------------------------------------
# 8. DFA -> Regex  (state elimination)
# ---------------------------------------------------------------------------

def _regex_union(a: str | None, b: str | None) -> str | None:
    """Combine with |. Returns None for ∅|∅."""
    if a is None:
        return b
    if b is None:
        return a
    if a == b:
        return a
    return f"({a}|{b})"


def _regex_concat(a: str | None, b: str | None) -> str | None:
    """Concatenate. Returns None if either is ∅."""
    if a is None or b is None:
        return None
    if a == "":
        return b  # ε concat
    if b == "":
        return a
    # Add parens around union expressions
    a_part = f"({a})" if "|" in a and not a.startswith("(") else a
    b_part = f"({b})" if "|" in b and not b.startswith("(") else b
    return f"{a_part}{b_part}"


def _regex_star(a: str | None) -> str:
    """Kleene star. star(None) = star(∅) = ε (empty string "")."""
    if a is None or a == "":
        return ""
    if len(a) == 1:
        return f"{a}*"
    return f"({a})*"


def dfa_to_regex(dfa: dict) -> str:
    """Convert a DFA to a regular expression using state elimination.

    Args:
        dfa: DFA dict with keys: states, alphabet, transitions, start, accept.

    Returns:
        Regex string equivalent to the DFA's language.
    """
    states = list(dfa["states"])
    alphabet = list(dfa["alphabet"])
    transitions = dfa["transitions"]
    start = dfa["start"]
    accept = list(dfa["accept"])

    # Edge case: no accept states → empty language
    if not accept:
        return "∅"

    # --- Step 1: Build GNFA ---
    # Add new start state S and new accept state A
    gnfa_start = "__S__"
    gnfa_accept = "__A__"
    all_states = [gnfa_start] + states + [gnfa_accept]

    # edges[p][q] = regex_string or None
    edges: dict[str, dict[str, str | None]] = {
        s: {t: None for t in all_states} for s in all_states
    }

    # ε-transition from new start to original start
    edges[gnfa_start][start] = ""

    # ε-transitions from original accept states to new accept
    for acc in accept:
        edges[acc][gnfa_accept] = _regex_union(edges[acc][gnfa_accept], "")

    # Add DFA transitions as regex labels
    for src in states:
        for sym in alphabet:
            dst = transitions.get(src, {}).get(sym)
            if dst is not None:
                edges[src][dst] = _regex_union(edges[src][dst], sym)

    # --- Step 2: Eliminate states one by one ---
    # Eliminate all original DFA states (not gnfa_start or gnfa_accept)
    for q_rip in states:
        # Get all remaining states except q_rip
        remaining = [s for s in all_states if s != q_rip]

        for q_i in remaining:
            for q_j in remaining:
                r1 = edges[q_i][q_rip]
                r2 = edges[q_rip][q_rip]
                r3 = edges[q_rip][q_j]
                r4 = edges[q_i][q_j]

                if r1 is not None and r3 is not None:
                    new_path = _regex_concat(r1, _regex_concat(_regex_star(r2), r3))
                    edges[q_i][q_j] = _regex_union(r4, new_path)
                # else edges[q_i][q_j] stays as r4

        # Remove q_rip from the graph
        all_states = remaining
        del edges[q_rip]
        for s in all_states:
            if q_rip in edges[s]:
                del edges[s][q_rip]

    # --- Step 3: Final regex ---
    result = edges[gnfa_start][gnfa_accept]
    if result is None:
        return "∅"
    return result
