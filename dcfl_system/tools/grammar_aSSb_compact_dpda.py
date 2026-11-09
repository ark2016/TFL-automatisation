"""Compact DCFL certificate for task_grammar_aSSb (dcfl_exam_04).

L = L(G), G: S -> aSSb | ba | Ab, A -> aAb | a (dcfl_system/examples/task_grammar_aSSb.json).

`grammar_aSSb_dpda.py` builds the 345-state certificate by the generic [NS, Thm 4]
determinization of the profile NPDA. This module builds a much smaller DPDA from a
structural description of L (docs/THEORY.md §1.10, "Компактный ДМПА"):

  (1) Every word of L other than "ba" is a Dyck word (a = open, b = close). Read it as
      its Dyck forest: a tree is a matched pair a ... b, its children are the trees
      directly inside. ``dyck_forest`` computes that forest.
  (2) Let L' = L \\ {ba}. L' = {aⁿbⁿ : n >= 1} ∪ a·L·L·b, and the four shapes of a·X·Y·b
      (X, Y each either "ba" or a word of L') give the recursive rule:
        * a forest F is an *L-group* iff F = [t] with word(t) ∈ L', or F = [leaf, t] /
          [t, leaf] where the word strictly inside t is in L', or F = [leaf, leaf, leaf];
        * word(t) ∈ L' for a tree t = a·u·b iff u = a^k b^k (k >= 0) -- the children of t
          form an empty or a path-shaped forest -- or the children of t split into two
          consecutive L-groups.
      A word w belongs to L iff w = "ba" or w is a non-empty Dyck word whose forest is an
      L-group (``forest_member``).
  (3) So each closed tree only needs a finite *type* -- the four flags (leaf, path,
      inside ∈ L', word ∈ L') -- and the type of a tree depends only on the sequence of
      its children's types. Five flag combinations occur (``TYPE_FLAGS``); every other
      combination makes the word unrecoverable. The child-type sequences of an open
      tree are tracked by a minimal DFA (``children_dfa``, 14 states), the top-level
      forest by a second minimal DFA whose output is "is an L-group" (``root_dfa``,
      6 states).
  (4) The DPDA (``build_certificate``) keeps one stack cell per open tree, exactly like
      the Dyck-language DPDA: on `a` it pushes the current DFA state and starts a fresh
      child, on `b` it closes the current tree, pops its parent's DFA state and feeds
      the closed tree's type into it. There are no epsilon transitions. The word "ba"
      is a separate two-state branch at the bottom of the stack.

Correctness of (2) is the hand argument recorded in docs/THEORY.md §1.10; everything
else is mechanical, and tests check the result against the grammar oracle
(`grammar_aSSb_dpda.in_language`) on every word up to a fixed length.

CLI:
    python -m dcfl_system.tools.grammar_aSSb_compact_dpda --out PATH [--check N]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from dcfl_system.tools.grammar_aSSb_dpda import build_fast_index, dpda_fast_accepts, in_language

# ---------------------------------------------------------------------------
# (1)-(2) Tree types and the forest characterization.
# ---------------------------------------------------------------------------

# Flags of a closed tree t = a·u·b: (leaf, path, inside_in_L1, word_in_L1), where
# L1 = L' = L \ {ba}:
#   leaf          -- t = ab (no children);
#   path          -- t = aⁿbⁿ (a chain of n >= 1 nested trees);
#   inside_in_L1  -- u ∈ L';
#   word_in_L1    -- a·u·b ∈ L'.
# Exactly these five combinations occur for trees whose word can still be part of a
# word of L (checked by `reachable_type_flags` in the tests).
TYPE_FLAGS: dict[str, tuple[bool, bool, bool, bool]] = {
    "LEAF": (True, True, False, True),    # ab
    "PATH": (False, True, True, True),    # aⁿbⁿ, n >= 2
    "SI": (False, False, True, True),     # not a path; inside ∈ L' and word ∈ L'
    "S": (False, False, False, True),     # word ∈ L' only
    "I": (False, False, True, False),     # inside ∈ L' only
}
TYPE_ORDER: tuple[str, ...] = ("LEAF", "PATH", "SI", "S", "I")
_FLAGS_TO_TYPE = {flags: name for name, flags in TYPE_FLAGS.items()}

# A forest with more children than this is never an L-group, and a tree with more
# children than this is never of any type (an L-group has <= 3 trees, two of them <= 6).
MAX_CHILDREN = 6


def _leaf(t: str) -> bool:
    return TYPE_FLAGS[t][0]


def _path(t: str) -> bool:
    return TYPE_FLAGS[t][1]


def _inside(t: str) -> bool:
    return TYPE_FLAGS[t][2]


def _word(t: str) -> bool:
    return TYPE_FLAGS[t][3]


def is_group(seq: Sequence[str]) -> bool:
    """Is a forest whose trees have types `seq` an L-group, i.e. the forest of a
    word of L'? (docs/THEORY.md §1.10, compact DPDA, rule (G))."""
    if len(seq) == 1:
        return _word(seq[0])
    if len(seq) == 2:
        return (_leaf(seq[0]) and _inside(seq[1])) or (_inside(seq[0]) and _leaf(seq[1]))
    if len(seq) == 3:
        return all(_leaf(t) for t in seq)
    return False


def vertex_flags(seq: Sequence[str]) -> tuple[bool, bool, bool, bool]:
    """Flags of a tree whose children have types `seq` (rule (T))."""
    leaf = len(seq) == 0
    path = leaf or (len(seq) == 1 and _path(seq[0]))
    inside = is_group(seq)
    word = path or any(is_group(seq[:k]) and is_group(seq[k:]) for k in range(1, len(seq)))
    return leaf, path, inside, word


def vertex_type(seq: Sequence[str]) -> str | None:
    """Type name of a tree whose children have types `seq`, or None if the tree can
    never be part of a word of L (all four flags false)."""
    flags = vertex_flags(seq)
    if not any(flags):
        return None
    try:
        return _FLAGS_TO_TYPE[flags]
    except KeyError as exc:  # pragma: no cover - excluded by reachable_type_flags
        raise AssertionError(f"unexpected tree flags {flags} for children {seq}") from exc


def reachable_type_flags() -> set[tuple[bool, bool, bool, bool]]:
    """Closure of the leaf flags under `vertex_flags` over child sequences of length
    <= MAX_CHILDREN + 1, dropping the all-false combination. Must equal the five
    TYPE_FLAGS values (checked in the tests)."""
    found = {vertex_flags(())}
    changed = True
    while changed:
        changed = False
        names = sorted(_FLAGS_TO_TYPE[f] for f in found if f in _FLAGS_TO_TYPE)
        if len(names) != len(found):
            unknown = [f for f in found if f not in _FLAGS_TO_TYPE]
            raise AssertionError(f"flag combination outside TYPE_FLAGS: {unknown}")
        for n in range(MAX_CHILDREN + 2):
            for seq in itertools.product(names, repeat=n):
                flags = vertex_flags(seq)
                if any(flags) and flags not in found:
                    found.add(flags)
                    changed = True
    return found


Tree = list  # a tree is the list of its children


def dyck_forest(w: str) -> list[Tree] | None:
    """The Dyck forest of `w` (a opens a tree, b closes it), or None if `w` is not a
    Dyck word (some prefix has more b's than a's, or the a's are not all closed)."""
    stack: list[list[Tree]] = [[]]
    for ch in w:
        if ch == "a":
            stack.append([])
        elif ch == "b":
            if len(stack) == 1:
                return None
            tree = stack.pop()
            stack[-1].append(tree)
        else:
            return None
    if len(stack) != 1:
        return None
    return stack[0]


def tree_type(tree: Tree) -> str | None:
    child_types = [tree_type(c) for c in tree]
    if any(t is None for t in child_types):
        return None
    return vertex_type(child_types)


def forest_member(w: str) -> bool:
    """Membership in L via the forest characterization (docs/THEORY.md §1.10)."""
    if w == "ba":
        return True
    forest = dyck_forest(w)
    if not forest:
        return False
    types = [tree_type(t) for t in forest]
    if any(t is None for t in types):
        return False
    return is_group(types)


# ---------------------------------------------------------------------------
# (3) Minimal DFAs over child-type sequences.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeDFA:
    """A DFA over TYPE_ORDER with an output per state (None = no output).

    `states` maps a state name to its output; `delta` maps (state, type) to a
    state; only states from which some output is still reachable are kept, and a
    missing `delta` entry means the sequence can no longer produce an output.
    """

    start: str
    states: dict[str, Hashable]
    delta: dict[tuple[str, str], str]

    def has_continuation(self, state: str) -> bool:
        return any((state, t) in self.delta for t in TYPE_ORDER)


def _minimal_dfa(prefix: str, output: Callable[[tuple[str, ...]], Hashable]) -> TypeDFA:
    """Minimal DFA (Moore partition refinement) for the output function `output`
    over child-type sequences. Sequences longer than MAX_CHILDREN + 1 never have an
    output, so the trie of all sequences up to that length is finite and complete.
    A state is named after its shortest representative sequence, e.g. `N(LEAF.SI)`.
    """
    max_len = MAX_CHILDREN + 1

    @cache
    def alive(seq: tuple[str, ...]) -> bool:
        if output(seq) is not None:
            return True
        if len(seq) >= max_len:
            return False
        return any(alive(seq + (t,)) for t in TYPE_ORDER)

    seqs = [
        seq
        for n in range(max_len + 1)
        for seq in itertools.product(TYPE_ORDER, repeat=n)
        if alive(seq)
    ]

    def step(seq: tuple[str, ...], t: str) -> tuple[str, ...] | None:
        nxt = seq + (t,)
        return nxt if len(nxt) <= max_len and alive(nxt) else None

    ids: dict[Hashable, int] = {}
    cls = {seq: ids.setdefault(output(seq), len(ids)) for seq in seqs}
    while True:
        sig_ids: dict[tuple, int] = {}
        new = {}
        for seq in seqs:
            sig = (cls[seq],) + tuple(
                cls[n] if (n := step(seq, t)) is not None else -1 for t in TYPE_ORDER
            )
            new[seq] = sig_ids.setdefault(sig, len(sig_ids))
        stable = len(set(new.values())) == len(set(cls.values()))
        cls = new
        if stable:
            break

    rep: dict[int, tuple[str, ...]] = {}
    for seq in seqs:  # seqs is ordered by length, then TYPE_ORDER: first = shortest
        rep.setdefault(cls[seq], seq)
    name = {c: f"{prefix}({'.'.join(seq)})" for c, seq in rep.items()}

    states = {name[c]: output(seq) for c, seq in rep.items()}
    delta: dict[tuple[str, str], str] = {}
    for c, seq in rep.items():
        for t in TYPE_ORDER:
            nxt = step(seq, t)
            if nxt is not None:
                delta[(name[c], t)] = name[cls[nxt]]
    return TypeDFA(start=name[cls[()]], states=states, delta=delta)


@cache
def children_dfa() -> TypeDFA:
    """DFA over the child types of an open tree; output = the tree's type if it were
    closed now (None = it could not be part of a word of L)."""
    return _minimal_dfa("N", vertex_type)


@cache
def root_dfa() -> TypeDFA:
    """DFA over the types of the top-level trees; output True = the forest read so far
    is an L-group (the word read so far is in L')."""
    return _minimal_dfa("R", lambda seq: True if is_group(seq) else None)


# ---------------------------------------------------------------------------
# (4) The DPDA.
# ---------------------------------------------------------------------------

BOTTOM = "Z0"
BA_STATE = "BA"  # after the leading b of the root word "ba"


def build_certificate() -> dict[str, Any]:
    """Assemble the compact DPDA in the stack_strategy `dpda` contract
    (dcfl_system/prompts/stack_strategy.md; checked by dcfl_system/lib/dpda.py).

    Control state = DFA state of the innermost open tree (an `N(...)` state), or of
    the top-level forest when no tree is open (an `R(...)` state, only ever with Z0
    on top). Stack cells = DFA states of the enclosing open trees.
    """
    inner = children_dfa()
    root = root_dfa()

    accept_states = sorted(s for s, out in root.states.items() if out)
    # The root word "ba" ends in a state that accepts and has no moves; a root
    # state with exactly that behaviour (e.g. after one tree of type S) exists,
    # reuse it instead of adding another state.
    ba_done = next(
        (s for s in accept_states if not root.has_continuation(s)),
        None,
    )
    extra_states = [BA_STATE]
    if ba_done is None:
        ba_done = "BA_DONE"
        extra_states.append(ba_done)
        accept_states.append(ba_done)

    root_pushable = sorted(s for s in root.states if root.has_continuation(s))
    inner_pushable = sorted(s for s in inner.states if inner.has_continuation(s))
    inner_tops = root_pushable + inner_pushable

    transitions: list[dict[str, Any]] = []

    def add(src: str, read: str, top: str, dst: str, push: list[str]) -> None:
        transitions.append({"from": src, "read": read, "top": top, "to": dst, "push": push})

    # `a` opens a child tree: save the current DFA state, start a fresh one.
    for q in root_pushable:
        add(q, "a", BOTTOM, inner.start, [q, BOTTOM])
    for q in inner_pushable:
        for top in inner_tops:
            add(q, "a", top, inner.start, [q, top])

    # `b` closes the current tree: feed its type to the parent's DFA state.
    for q in sorted(inner.states):
        tree = inner.states[q]
        if tree is None:
            continue
        for parent in root_pushable:
            dst = root.delta.get((parent, tree))
            if dst is not None:
                add(q, "b", parent, dst, [])
        for parent in inner_pushable:
            dst = inner.delta.get((parent, tree))
            if dst is not None:
                add(q, "b", parent, dst, [])

    # The root word "ba" (the only word of L that is not a Dyck word).
    add(root.start, "b", BOTTOM, BA_STATE, [BOTTOM])
    add(BA_STATE, "a", BOTTOM, ba_done, [BOTTOM])

    states = sorted(root.states) + sorted(inner.states) + extra_states
    return {
        "states": states,
        "start": root.start,
        "accept_states": accept_states,
        "accept_mode": "final_state",
        "stack_alphabet": [BOTTOM, *inner_tops],
        "initial_stack": [BOTTOM],
        "transitions": transitions,
    }


def dump_certificate(cert: dict[str, Any]) -> str:
    """JSON text of `cert` with one transition per line (readable diffs)."""
    lines = ["{"]
    keys = list(cert)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        value = cert[key]
        if key == "transitions":
            lines.append(f' "{key}": [')
            for j, t in enumerate(value):
                tcomma = "," if j < len(value) - 1 else ""
                lines.append("  " + json.dumps(t, ensure_ascii=False) + tcomma)
            lines.append(" ]" + comma)
        else:
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _check(cert: dict[str, Any], max_len: int) -> int:
    """Number of words of length <= max_len on which `cert` disagrees with the
    grammar oracle `in_language` (0 = agrees)."""
    index = build_fast_index(cert)
    mismatches = 0
    for n in range(1, max_len + 1):
        for tup in itertools.product("ab", repeat=n):
            w = "".join(tup)
            if dpda_fast_accepts(cert, w, index) != in_language(w):
                mismatches += 1
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dcfl_system.tools.grammar_aSSb_compact_dpda",
        description=(
            "Build the compact DCFL certificate DPDA for task_grammar_aSSb "
            "(docs/THEORY.md §1.10) and optionally cross-check it against the "
            "grammar oracle."
        ),
    )
    parser.add_argument("--out", required=True, help="path to write the certificate JSON to")
    parser.add_argument(
        "--check",
        type=int,
        default=None,
        metavar="N",
        help="cross-check the certificate against in_language() on all words of length <= N",
    )
    args = parser.parse_args(argv)

    cert = build_certificate()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_certificate(cert), encoding="utf-8", newline="\n")
    print(
        f"states={len(cert['states'])} stack={len(cert['stack_alphabet'])} "
        f"transitions={len(cert['transitions'])} bytes={out_path.stat().st_size}"
    )
    if args.check is not None:
        mismatches = _check(cert, args.check)
        print(f"check up to length {args.check}: mismatches={mismatches}")
        if mismatches:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
