"""Constructive DCFL certificate for task_grammar_aSSb (dcfl_exam_04).

L = L(G), G: S -> aSSb | ba | Ab, A -> aAb | a (dcfl_system/examples/task_grammar_aSSb.json).

This module mechanizes the proof of docs/THEORY.md §1.10 ("task_grammar_aSSb (dcfl_exam_04)
-- DCFL"). Read that section first; the summary below only orients the code:

  (1) `in_language` -- membership straight from the grammar (brute-force parse, cached).
      This is the independent oracle everything else is checked against.
  (2) `build_npda` -- the *profile* NPDA of §1.10 step (1): a real-time NPDA (no
      epsilon-transitions) whose stack height after any prefix u (other than u in
      {"b", "ba"}) equals the profile h(u) = |u|_a - |u|_b. A stack cell is either a
      "node" (a tree vertex opened by an `a`, tagged with its phase -- the number of
      completed children, 0/1/2) or a "leaf-a" (an `a` of an aⁿbⁿ leaf). The ONLY
      nondeterminism is at the first letter of a new subtree: push a "node" or a
      "leaf-a" (the grammar's own S -> aSSb / A -> a ambiguity, turned into a stack
      choice). The `ba` leaf at an empty stack is a separate pair of finite-control
      transitions (state 'BAroot' -> 'D'), not part of the stack discipline.
  (3) `determinize` -- height-deterministic summary/profile construction, [NS, Thm 4]
      (D. Nowotka, J. Srba, "Height-Deterministic Pushdown Automata", MFCS 2007, LNCS
      4708, pp. 125-134): the same scheme used to determinize visibly pushdown automata
      [Alur-Madhusudan, STOC 2004]. A DPDA stack symbol at level i is the SET of triples
      (p, X, q) reachable at that level, where p is the pair (NPDA state, NPDA stack top)
      recorded immediately before the last unmatched push into this level, X is the
      symbol that push put on the NPDA stack, and q is the current NPDA state. Push
      introduces a fresh such set (and pushes the pre-push DPDA state itself as the new
      stack symbol below it); pop matches the popped set's triples against the level
      below BY THE PAIR (state, top-of-stack symbol) -- keying only on state (dropping
      the stack symbol) would make this a superset construction: aaababbb (not in L)
      would be wrongly accepted (see docs/THEORY.md §1.10, step 3).
  (4) `quotient` -- bisimulation minimization of the raw summary DPDA down to the
      certificate's actual state count (states merged when they agree on every
      (top-of-stack-class, letter) -> (kind, target-class) signature, refined to a
      fixed point).
  (5) `export_dpda` -- serialize to the `dpda` proof_sketch contract of
      dcfl_system/prompts/stack_strategy.md, checked by dcfl_system/lib/dpda.py
      (`check_determinism`, `dpda_accepts`) and dcfl_system/lib/oracle_verifier.py.

CLI:
    python -m dcfl_system.tools.grammar_aSSb_dpda --out PATH [--check N]

rebuilds the certificate end-to-end and, with `--check N`, cross-checks the exported
DPDA (via a small fast simulator following the same contract semantics as
dcfl_system.lib.dpda.dpda_accepts) against `in_language` on every word of length <= N.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# (0) Grammar oracle: membership straight from G, cached.
# ---------------------------------------------------------------------------

# G: S -> aSSb | ba | Ab, A -> aAb | a.  L(A) = {a^(k+1) b^k : k >= 0}, so
# Ab = {a^n b^n : n >= 1}; combined with S -> ba and closure "x, y in L(S) =>
# a x y b in L(S)", L(S) is the smallest set containing "ba" and every a^n b^n
# (n >= 1), closed under that operation. Words of L(S) are exactly the "bark"
# (crown) readings of binary trees: an internal node is a*(child1)*(child2)*b,
# a leaf is "ba" or a^n b^n.


@cache
def in_language(w: str) -> bool:
    """Decide w in L(G) by direct (cached) recursive descent over G -- the
    brute-force oracle everything else in this module is checked against.
    """
    n = len(w)
    if n % 2 or n < 2:
        return False
    if w in ("ab", "ba"):
        return True
    if w == "a" * (n // 2) + "b" * (n // 2):
        return True
    if w[0] != "a" or w[-1] != "b":
        return False
    inner = w[1:-1]
    return any(
        in_language(inner[:k]) and in_language(inner[k:])
        for k in range(2, len(inner) - 1, 2)
    )


# ---------------------------------------------------------------------------
# (1) The profile NPDA (THEORY.md §1.10, step 1).
# ---------------------------------------------------------------------------

# NPDA stack bottom sentinel (distinct from any pushed cell, which is always a
# 3-tuple ('n' | 'L', phase-of-cell-below, is-bottom-cell)).
BOTTOM: tuple = ("BOT",)

# Finite control: ('N', k) -- top cell pending, k = phase already recorded for
# it (0, 1 or 2 completed children); ('BA', k, k2, bottom) -- mid-way through
# the "leaf ba" branch (k = phase of the node cell that was popped to start
# it, k2/bottom = annotation to restore on the following 'a'); 'BAroot' /
# 'D' -- the two states of the separate "ba at an empty stack" branch (state
# 'D' is the unique accepting state: root closed, stack empty, no further
# moves); dead = no listed transition (implicit rejection).

Action = tuple  # ("push", cell) | ("pop",) | ("int",)


def _npda_step(state: Any, top: Any, ch: str) -> list[tuple[Any, Action]]:
    """One NPDA move: all (new_state, action) pairs for reading `ch` with
    `top` on top of the stack in `state`. THEORY.md §1.10, step 1.
    """
    out: list[tuple[Any, Action]] = []
    if state == "D" or state == "DEAD":
        return out

    if state[0] == "BA":
        _, k, k2, bottom = state
        if ch == "a":
            # Restore the node cell (phase k, now +1 completed child) that
            # the 'b' of this "leaf ba" popped off; k2/bottom carry over.
            out.append((("N", k + 1), ("push", ("n", k2, bottom))))
        return out

    if top == BOTTOM:
        if state == ("N", 0):
            if ch == "a":
                # Only nondeterminism: first letter of the (only) subtree at
                # the root is the start of a "leaf-a" or a new "node".
                out.append((("N", 0), ("push", ("L", 0, True))))
                out.append((("N", 0), ("push", ("n", 0, True))))
            else:
                # Whole word is "ba" -- separate finite-control branch.
                out.append(("BAroot", ("int",)))
        elif state == "BAroot":
            if ch == "a":
                out.append(("D", ("int",)))
        return out

    base, k2, bottom = top
    if state[0] == "N":
        k = state[1]
        if ch == "a":
            if base == "L":
                if k == 0:
                    out.append((("N", 0), ("push", ("L", 0, False))))
            else:  # "node" cell with phase k
                if k <= 1:
                    out.append((("N", 0), ("push", ("L", k, False))))
                    out.append((("N", 0), ("push", ("n", k, False))))
        else:  # ch == "b"
            if base == "L" or k == 2:
                # Leaf-a finished, or node's 2nd child finished: pop, bump
                # the phase recorded for the cell below (or accept if it was
                # the bottom cell).
                out.append(("D" if bottom else ("N", k2 + 1), ("pop",)))
            else:
                # First child of a node finished as a "leaf ba": pop the
                # node, remember its phase, restore it on the next 'a'.
                out.append((("BA", k, k2, bottom), ("pop",)))
    elif state[0] == "BA":
        _, k, k2, bottom = state
        if ch == "a":
            out.append((("N", k + 1), ("push", ("n", k2, bottom))))
    return out


@dataclass(frozen=True)
class NPDA:
    """The profile NPDA: initial configuration + transition function."""

    initial_state: Any
    bottom: Any
    step: Callable[[Any, Any, str], list[tuple[Any, Action]]]


def build_npda() -> NPDA:
    """Build the profile NPDA for L(G) (THEORY.md §1.10, step 1)."""
    return NPDA(initial_state=("N", 0), bottom=BOTTOM, step=_npda_step)


def npda_run(npda: NPDA, w: str) -> tuple[bool, set[int] | None]:
    """Run `npda` on `w` breadth-first over ALL nondeterministic branches.

    Returns (accepted, heights), where `heights` is the set of stack heights
    across every live configuration after reading all of `w` (or None if the
    automaton died -- no live configuration -- before the end of `w`). Used
    both by `--check` and by the height-determinism test: THEORY.md §1.10
    step (2) claims every live configuration after any prefix has the SAME
    stack height, i.e. `len(heights) <= 1` always.
    """
    confs = {(npda.initial_state, (npda.bottom,))}
    for ch in w:
        nxt: set[tuple[Any, tuple]] = set()
        for state, stack in confs:
            for state2, action in npda.step(state, stack[-1], ch):
                if action[0] == "push":
                    nxt.add((state2, stack + (action[1],)))
                elif action[0] == "pop":
                    nxt.add((state2, stack[:-1]))
                else:
                    nxt.add((state2, stack))
        confs = nxt
        if not confs:
            return False, None
    heights = {len(stack) for _, stack in confs}
    return any(state == "D" for state, _ in confs), heights


# ---------------------------------------------------------------------------
# (2)-(3) Determinization: height-deterministic summary construction.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawDPDA:
    """Pre-quotient summary DPDA (THEORY.md §1.10, step 3).

    `names` maps each reachable DPDA state (a frozenset of (predecessor,
    pushed-symbol, npda-state) triples) to a name; `transitions` is a flat
    list of (from_name, letter, top_name, kind, to_name) with
    kind in {"push", "pop", "int"} ("int" = same height, state changes only --
    the two finite-control moves of the "ba at an empty stack" branch);
    `accepting`/`start` are names too. `top_name` "BOT" denotes height 0
    (nothing below).
    """

    names: dict[frozenset, str]
    transitions: list[tuple[str, str, str, str, str]]
    accepting: set[str]
    start: str


_BOTNAME = "BOT"


def _push_or_pop(npda: NPDA, T: frozenset, ch: str):
    """Classify the summary-DPDA move of state `T` on letter `ch`: returns
    ("push" | "int" | "pop", <new triple set>) or None if `T` dies on `ch`.
    Height-determinism (THEORY.md §1.10, step 2) is what makes exactly one of
    push/int/pop apply to every triple in `T` at once -- never a mix.
    """
    pushes: set = set()
    pops: set = set()
    ints: set = set()
    for (p, X, q) in T:
        for q2, action in npda.step(q, X, ch):
            if action[0] == "push":
                pushes.add(((q, X), action[1], q2))
            elif action[0] == "pop":
                pops.add((p, X, q, q2))
            else:
                ints.add((p, X, q2))
    if pushes:
        return "push", frozenset(pushes)
    if ints:
        return "int", frozenset(ints)
    if pops:
        return "pop", frozenset(pops)
    return None


def _pop_result(pops: frozenset, Tb: frozenset) -> frozenset | None:
    """Resolve a "pop" move against the level below (`Tb`): match each popped
    triple's recorded (state, top) pair `p` against the (ending-state,
    symbol) of a triple in `Tb` -- keying on the PAIR, not just the state
    (THEORY.md §1.10, step 3; keying on state alone yields a superset).
    """
    new = set()
    for (p, X, q, q2) in pops:
        for (p1, Y, pp) in Tb:
            if (pp, Y) == p:
                new.add((p1, Y, q2))
    return frozenset(new) if new else None


def determinize(npda: NPDA | None = None) -> RawDPDA:
    """Build the raw (pre-quotient) summary DPDA by BFS reachability over
    (below-symbol, current-state) pairs, starting from the single initial
    triple set. THEORY.md §1.10, step 3; see also `quotient` for minimization.
    """
    npda = npda or build_npda()
    T0 = frozenset({(None, npda.bottom, npda.initial_state)})
    pairs: set[tuple[Any, frozenset]] = {(_BOTNAME, T0)}
    changed = True
    while changed:
        changed = False
        for (Y, T) in list(pairs):
            for ch in "ab":
                r = _push_or_pop(npda, T, ch)
                if r is None:
                    continue
                kind, val = r
                if kind == "push":
                    candidate = (T, val)
                    if candidate not in pairs:
                        pairs.add(candidate)
                        changed = True
                elif kind == "int":
                    candidate = (Y, val)
                    if candidate not in pairs:
                        pairs.add(candidate)
                        changed = True
                else:  # "pop": Y is itself a reachable T, matched against every level below it
                    if Y == _BOTNAME:
                        continue
                    for (Y2, Yb) in list(pairs):
                        if Yb != Y:
                            continue
                        nt = _pop_result(val, Y)
                        if nt is None:
                            continue
                        candidate = (Y2, nt)
                        if candidate not in pairs:
                            pairs.add(candidate)
                            changed = True

    states = {T for _, T in pairs} | {Y for Y, _ in pairs if Y != _BOTNAME}
    names = {
        T: f"T{i}"
        for i, T in enumerate(
            sorted(states, key=lambda T: (len(T), sorted(map(str, T))))
        )
    }
    accepting = {names[T] for T in states if any(q == "D" for _, _, q in T)}

    transitions: list[tuple[str, str, str, str, str]] = []
    for (Y, T) in sorted(pairs, key=lambda p: (names.get(p[0], _BOTNAME), names[p[1]])):
        for ch in "ab":
            r = _push_or_pop(npda, T, ch)
            if r is None:
                continue
            kind, val = r
            top_name = names.get(Y, _BOTNAME)
            if kind == "push":
                transitions.append((names[T], ch, top_name, "push", names[val]))
            elif kind == "int":
                transitions.append((names[T], ch, top_name, "int", names[val]))
            else:
                if Y == _BOTNAME:
                    continue
                nt = _pop_result(val, Y)
                if nt is not None:
                    transitions.append((names[T], ch, top_name, "pop", names[nt]))

    return RawDPDA(names=names, transitions=transitions, accepting=accepting, start=names[T0])


# ---------------------------------------------------------------------------
# (4) Bisimulation quotient (minimization).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotientDPDA:
    """Minimized summary DPDA: states/stack symbols are equivalence-class ids
    (int), -1 reserved for the bottom-of-stack class ("BOT" / "Z0"). `trans`
    maps (from_class, letter, top_class) -> (kind, to_class).
    """

    q0: int
    acc: frozenset[int]
    trans: dict[tuple[int, str, int], tuple[str, int]]


def quotient(raw: RawDPDA, max_iterations: int = 100) -> QuotientDPDA:
    """Bisimulation-minimize `raw`: states are merged while they agree, for
    every letter, on the (class-of-top, kind, class-of-target) behaviour for
    every stack-top class -- refined to a fixed point (partition refinement).
    """
    state_names = set(raw.names.values())
    all_symbols = state_names | {_BOTNAME}
    tm: dict[tuple[str, str, str], tuple[str, str]] = {}
    for s, ch, top, kind, tgt in raw.transitions:
        tm[(s, ch, top)] = (kind, tgt)

    cls: dict[str, int] = {s: (1 if s in raw.accepting else 0) for s in state_names}
    cls[_BOTNAME] = -1

    def sig(s: str):
        out = []
        for ch in "ab":
            m: dict[Any, set] = {}
            for top in all_symbols:
                r = tm.get((s, ch, top))
                key = cls[top]
                val = None if r is None else (r[0], cls[r[1]])
                m.setdefault(key, set()).add(val)
            out.append(tuple(sorted((k, tuple(sorted(map(str, v)))) for k, v in m.items())))
        return (cls[s], tuple(out))

    ordered_state_names = sorted(state_names)  # fixed iteration order: reproducible class ids
    for _ in range(max_iterations):
        sigs = {s: sig(s) for s in state_names}
        newid: dict[Any, int] = {}
        newcls: dict[str, int] = {}
        for s in ordered_state_names:
            newcls[s] = newid.setdefault(sigs[s], len(newid))
        newcls[_BOTNAME] = -1
        n_old = len(set(cls.values()))
        n_new = len(set(newcls.values()))
        cls = newcls
        if n_new == n_old:
            break
    else:
        raise RuntimeError("bisimulation refinement did not converge")

    qtrans: dict[tuple[int, str, int], tuple[str, int]] = {}
    for (s, ch, top), (kind, tgt) in tm.items():
        key = (cls[s], ch, cls[top])
        val = (kind, cls[tgt])
        if key in qtrans and qtrans[key] != val:
            raise RuntimeError(f"inconsistent quotient at {key}: {qtrans[key]} vs {val}")
        qtrans[key] = val

    qacc = frozenset(cls[s] for s in raw.accepting)
    q0 = cls[raw.start]
    return QuotientDPDA(q0=q0, acc=qacc, trans=qtrans)


# ---------------------------------------------------------------------------
# (5) Export to the stack_strategy `dpda` contract.
# ---------------------------------------------------------------------------


def _cert_name(cls_id: int) -> str:
    return "Z0" if cls_id == -1 else f"T{cls_id}"


def export_dpda(quot: QuotientDPDA) -> dict[str, Any]:
    """Serialize `quot` to the `dpda` proof_sketch contract
    (dcfl_system/prompts/stack_strategy.md; checked by dcfl_system/lib/dpda.py).
    """
    items = [(k[0], k[1], k[2], v[0], v[1]) for k, v in quot.trans.items()]
    states = sorted({t[0] for t in items} | {t[4] for t in items} | {quot.q0} | quot.acc)
    stack = sorted({t[2] for t in items} | {t[0] for t in items if t[3] == "push"} | {-1})
    out: dict[str, Any] = {
        "states": [_cert_name(s) for s in states],
        "start": _cert_name(quot.q0),
        "accept_states": [_cert_name(s) for s in sorted(quot.acc)],
        "accept_mode": "final_state",
        "stack_alphabet": [_cert_name(z) for z in stack],
        "initial_stack": ["Z0"],
        "transitions": [],
    }
    for s, ch, top, kind, tgt in items:
        if kind == "push":
            push = [_cert_name(s), _cert_name(top)]
        elif kind == "pop":
            push = []
        else:  # "int": same height, state only changes -- pop+push the same symbol back
            push = [_cert_name(top)]
        out["transitions"].append(
            {"from": _cert_name(s), "read": ch, "top": _cert_name(top), "to": _cert_name(tgt), "push": push}
        )
    return out


def build_fast_index(cert: dict[str, Any]) -> dict[tuple[str, str, str], dict]:
    """Precompute the (state, top, read) -> transition index used by
    `dpda_fast_accepts`, so a `--check`/test loop over many words builds it once.
    """
    return {(t["from"], t["top"], t["read"]): t for t in cert["transitions"]}


def dpda_fast_accepts(
    cert: dict[str, Any], w: str, index: dict[tuple[str, str, str], dict] | None = None
) -> bool:
    """Simulate the exported certificate directly (same "from/read/top/to/push,
    topmost-first" contract semantics as dcfl_system.lib.dpda.dpda_accepts, just
    without the generic cfl_system.lib.pda_simulator machinery) -- an
    independent, much faster check for `--check`/tests than round-tripping
    through the shared simulator on every word.
    """
    idx = index if index is not None else build_fast_index(cert)
    acc = set(cert["accept_states"])
    state = cert["start"]
    stack = list(reversed(cert["initial_stack"]))  # bottom .. top
    for ch in w:
        if not stack:
            return False
        t = idx.get((state, stack[-1], ch))
        if t is None:
            return False
        stack.pop()
        stack.extend(reversed(t["push"]))
        state = t["to"]
    return state in acc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_certificate() -> dict[str, Any]:
    """Run the full pipeline (build_npda -> determinize -> quotient ->
    export_dpda) and return the certificate dict."""
    return export_dpda(quotient(determinize(build_npda())))


def _check(cert: dict[str, Any], max_len: int) -> int:
    """Compare `cert` against `in_language` on every word of length <= max_len.
    Returns the number of mismatches (0 = certificate agrees with the oracle)."""
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
        prog="python -m dcfl_system.tools.grammar_aSSb_dpda",
        description=(
            "Rebuild the DCFL certificate DPDA for task_grammar_aSSb "
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
    out_path.write_text(json.dumps(cert, separators=(",", ":")), encoding="utf-8")

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
