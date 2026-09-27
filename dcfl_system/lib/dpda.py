"""Deterministic pushdown automaton (DPDA) support for the DCFL stack_strategy
contract (docs/VERDICT_POLICY.md R2').

The `dpda` proof_sketch field (prompts/stack_strategy.md, dcfl_system/tz_dcfl_agent_system.md
§5.2) uses its own field names -- `read`/`top` (not cfl_system's `input`/`stack_top`),
`push` topmost-first (same convention as cfl_system/prompts/cfl_pda_builder.md), and
`initial_stack` a topmost-first list (usually a single bottom marker) -- so it needs a
small adapter, not a full re-implementation of everything: ``to_cfl_pda`` still reuses
cfl_system.lib.pda_simulator for the (compatibility-only) structural-validity check, but
the actual DCFL simulation now runs on ``dpda_run``, a dedicated deterministic simulator
(see below for why a general NPDA/BFS simulator is the wrong tool once acceptance can be
keyed on a stack-top, not just a state).

Four functions, per docs/VERDICT_POLICY.md R2':

- ``normalize_epsilon_accept_sinks`` -- the ONE canonical, language-preserving rewrite
  the policy allows *before* determinism is checked (see its own docstring for the
  equivalence argument). Callers (``_verify_stack_strategy_dpda`` in ``oracle_verifier.py``)
  run this FIRST and check/simulate the returned, normalized automaton -- ``check_determinism``
  and ``dpda_run``/``dpda_accepts`` themselves do not call it and do not know about the
  rewrite; they just take whatever ``dpda`` dict they are handed at face value (including
  its optional ``accept_configs`` field, which this rewrite is the one thing that populates).
- ``check_determinism`` -- a purely SYNTACTIC check (no more than one transition per
  (state, stack_top, letter); an epsilon transition never coexists with a letter
  transition for the same (state, stack_top)). This is a *complete* decision
  procedure, so a clean result earns ``trust: verified`` for the determinism
  property itself; a violation is a genuine, deterministic counterexample
  (``refuted``), never a bounded/sampled judgement. Expects an already-normalized
  ``dpda`` (see above) -- it does not itself distinguish an accept-sink epsilon
  transition from any other kind of non-determinism.
- ``dpda_run`` -- a dedicated, hand-written DETERMINISTIC simulator (not the general
  NPDA/BFS explorer in cfl_system.lib.pda_simulator): at every step it takes the letter
  transition for (state, stack_top, next_char) if one exists, otherwise the epsilon
  transition for (state, stack_top) if that is the ONLY one defined there. Acceptance
  (once input is exhausted, after firing any further deterministic epsilon moves) is by
  ``accept_states`` (state alone) OR by ``accept_configs`` (state, stack_top) for
  ``accept_mode in (None, "final_state")``, or by an empty stack for
  ``accept_mode == "empty_stack"`` (unchanged, pre-existing semantics). Returns ``None``
  (never raises) if a pathological epsilon cycle exceeds the step budget -- a genuinely
  deterministic DPDA never does this, so this is purely a defensive bound. Expects an
  already-normalized ``dpda`` for consistency with ``check_determinism``, but does not
  itself require it -- it is a general deterministic simulator that also happens to
  understand ``accept_configs``, so it runs correctly on an un-normalized ``dpda`` too
  (the rewrite is language-preserving; see ``normalize_epsilon_accept_sinks``).
- ``dpda_accepts`` -- thin ``bool``-returning wrapper over ``dpda_run`` (raises
  ``TimeoutError`` instead of returning ``None`` on step-budget exhaustion, for callers
  that want the older exception-based contract). ``to_cfl_pda`` is kept only for the
  compatibility structural-validity check (unknown states/symbols, missing required
  fields) -- it does NOT understand ``accept_configs``, so it must never be used to
  actually simulate a normalized ``dpda``; ``_verify_stack_strategy_dpda`` in
  ``oracle_verifier.py`` uses it only that way, and uses ``dpda_run`` for simulation.
"""
from __future__ import annotations

from typing import Any

from cfl_system.lib.pda_simulator import pda_accepts, validate_pda


class DPDAFormatError(ValueError):
    """Raised when a ``dpda`` dict is too malformed to check or simulate."""


_MAX_STEPS = 10_000

# Agent-written proof_sketches spell an epsilon transition's ``read`` field a
# number of different ways ("", "ε", "eps", "epsilon", besides the canonical
# None) -- normalize every one of them to the canonical `None` that
# `check_determinism`'s coexistence check and `to_cfl_pda`'s conversion (and,
# downstream, cfl_system.lib.pda_simulator) both expect, so an epsilon
# transition recorded as a string is caught and simulated exactly like one
# recorded as `None`, not silently treated as a same-letter transition.
_EPSILON_SPELLINGS = {None, "", "ε", "eps", "epsilon"}


def _normalize_read(read: Any) -> Any:
    """Map any of ``dpda.py``'s recognized epsilon spellings to `None`
    (canonical epsilon); anything else (an actual input letter) passes
    through unchanged."""
    if isinstance(read, str) and read.strip().lower() in {"", "ε", "eps", "epsilon"}:
        return None
    if read is None:
        return None
    return read


# ---------------------------------------------------------------------------
# (a) Canonical normalization (docs/VERDICT_POLICY.md R2', normalization paragraph)
# ---------------------------------------------------------------------------

def normalize_epsilon_accept_sinks(dpda: dict) -> tuple[dict, list[str]]:
    """Canonical, language-preserving rewrite allowed by docs/VERDICT_POLICY.md
    R2' before determinism is checked: for every epsilon transition
    ``(q, Z) -> q_acc`` where ``q_acc`` is an accept state (``accept_states``)
    that has NO outgoing transitions of its own (neither by letter nor by a
    further epsilon), delete that transition and add the pair ``(q, Z)`` to
    the returned dpda's ``accept_configs`` list instead (never ``accept_states``
    -- see below for why the config-level rewrite, unlike an earlier revision
    of this function that blanket-marked ``q`` itself as accepting, needs no
    extra side condition on ``q`` to stay sound). Returns
    ``(normalized_dpda, notes)`` -- ``notes`` is a human-readable list of every
    rewrite performed (empty if none applied), meant to be recorded verbatim in
    ``details["normalization"]`` by the caller (``oracle_verifier._verify_stack_strategy_dpda``).

    Applies ONLY when ``accept_mode`` is ``"final_state"`` or absent (the
    contract's default -- see ``to_cfl_pda``); under ``"empty_stack"``
    acceptance has nothing to do with the current state, so an epsilon into a
    dedicated accepting sink is not this pattern at all and is left alone.
    Any other epsilon transition -- into a state that DOES have outgoing
    transitions, or into a non-accepting state -- is never touched; per R2',
    one coexisting with a letter transition on the same (state, top) remains
    genuine non-determinism (``refuted``), precisely because nothing here
    can vouch for it.

    **Why this needs no "does `q` occur with some other stack top" guard,
    unlike an earlier revision.** The earlier revision of this function
    deleted the transition and unconditionally added `q` itself to
    `accept_states` -- sound ONLY when `q` could be proven to never occur
    with any stack top other than `Z` (an extra, non-local side condition,
    `_pushes_only_onto`, since `final_state` acceptance checks only the
    current state, never the stack). The CURRENT rewrite instead adds the
    exact pair `(q, Z)` to `accept_configs`, and acceptance under `final_state`
    mode is checked against `accept_states` OR `accept_configs` (see
    `dpda_run`) -- i.e. the stack top IS now part of what is checked. `q`
    occurring with some OTHER top `Z' != Z` is therefore harmless: that
    configuration `(q, Z')` is simply not in `accept_configs` (nor, presumably,
    is `q` in `accept_states`), so it is correctly NOT accepting, exactly as
    the original, un-normalized automaton had no applicable transition there
    and rejected. This is why the guard the earlier revision needed
    (`_pushes_only_onto`) is gone entirely: the rewrite is sound for every
    qualifying epsilon transition unconditionally, regardless of what else `q`
    is reachable with.

    **Consequence, precedent (live dcfl-04, docs/VERDICT_POLICY.md R2'):** the
    live dcfl-04 proof_sketch's `dpda` for {aⁿbⁿcᵐ} has states `q0`, `q_b`,
    `q_c`, each with an epsilon `(state, top=Z0) -> q_accept` where `q_accept`
    has no outgoing transitions. Under the CURRENT rewrite all three
    normalize (unlike the earlier revision, which left `q_b` alone because it
    is ALSO reached with `top="A"` via its own pop self-loop) -- each becomes
    an `accept_configs` entry `(q0, "Z0")` / `(q_b, "Z0")` / `(q_c, "Z0")`, and
    the result IS syntactically deterministic (`check_determinism` reports no
    conflicts: the epsilon that used to coexist with `q_b`'s letter transition
    on `(q_b, "Z0")` is gone). But the resulting deterministic DPDA is simply
    WRONG for this language: it has a transition `q0 --b--> q_b` with no
    preceding `a`, so on input `"b"` it reaches `(q_b, top="Z0")` with input
    exhausted -- an accept_configs hit -- and wrongly ACCEPTS `"b"` (0 a's, 1
    b -- not in {aⁿbⁿcᵐ}). This is exactly the kind of defect
    ``check_determinism`` cannot see (the automaton IS deterministic) and only
    simulation against the task's own language oracle catches (`dpda_run`
    disagreeing with the oracle on `"b"` -> `refuted`); see
    ``test_dpda.py::TestNormalizeEpsilonAcceptSinksLiveDcfl04`` for the
    regression test using this precise automaton, and its "fixed" sibling
    (the same automaton minus the stray `q0 --b--> q_b` transition, so `n=0`
    is only reachable via `(q0, "Z0")` itself) for the case where normalization
    AND simulation both succeed.

    **Proof of equivalence (for a qualifying epsilon transition).** Fix an
    epsilon transition ``t = (q, Z) -> q_acc`` where ``q_acc`` has no outgoing
    transitions. In the ORIGINAL automaton, a run that reaches configuration
    ``(q, Z·rest)`` with input exhausted is, from that point on, resolved
    purely by whether ``t`` fires: (i) if it doesn't (there is no more input
    and ``t`` -- being a genuine DPDA transition -- fires deterministically
    whenever nothing else does, so "doesn't fire" only happens if some OTHER
    move preempts it, which cannot happen here since ``t`` is the unique
    epsilon at this configuration by construction of ``check_determinism``\'s
    contract), the run is simply already halted at ``(q, Z·rest)``, and
    whether it is accepting is exactly "is `q` accepting", which after the
    rewrite is answered by ``(q, Z) in accept_configs`` directly -- the SAME
    answer, since the rewrite added precisely this pair; (ii) if it does fire,
    the run moves to ``q_acc`` with the same stack (pushing `Z` back, per the
    transition's own `push`) and halts there with nothing further possible
    (`q_acc` has no outgoing transitions) -- accepting, because `q_acc` is an
    accept state, and this too matches "accept because input ran out at
    `(q, Z)`". No OTHER configuration is affected: the rewrite deletes exactly
    one transition (which becomes unreachable dead code once removed, since
    `q_acc` had no other incoming or outgoing edges tied to this argument) and
    adds exactly one `(q, Z)` pair to `accept_configs`, which only changes the
    acceptance verdict for runs that reach EXACTLY `(q, Z)` with input
    exhausted -- precisely the runs this argument covers. Applying this
    independently to every qualifying epsilon transition preserves the
    language of the whole automaton.
    """
    accept_mode = dpda.get("accept_mode")
    if accept_mode not in (None, "final_state"):
        return dpda, []

    accept_states = list(dpda.get("accept_states") or [])
    if not accept_states:
        return dpda, []

    transitions = dpda.get("transitions") or []
    if not isinstance(transitions, list):
        return dpda, []

    # A state has "outgoing transitions" if it appears as the source of any
    # transition (letter or epsilon) in the ORIGINAL automaton.
    states_with_outgoing = {
        t.get("from") for t in transitions if isinstance(t, dict)
    }
    sink_accepts = {s for s in accept_states if s not in states_with_outgoing}
    if not sink_accepts:
        return dpda, []

    kept: list[dict] = []
    notes: list[str] = []
    existing_configs = {
        (c[0], c[1])
        for c in (dpda.get("accept_configs") or [])
        if isinstance(c, (list, tuple)) and len(c) == 2
    }
    new_accept_configs = set(existing_configs)
    for t in transitions:
        if (
            isinstance(t, dict)
            and _normalize_read(t.get("read")) is None
            and t.get("to") in sink_accepts
        ):
            q, z = t.get("from"), t.get("top")
            notes.append(
                f"removed epsilon transition (q={q!r}, top={z!r}) -> "
                f"{t.get('to')!r} (accepting sink with no outgoing transitions); "
                f"added accepting configuration (q={q!r}, top={z!r}) to "
                f"accept_configs instead"
            )
            new_accept_configs.add((q, z))
        else:
            kept.append(t)

    if not notes:
        return dpda, []

    normalized = dict(dpda)
    normalized["transitions"] = kept
    normalized["accept_configs"] = sorted(
        ([q, z] for q, z in new_accept_configs),
        key=lambda c: (str(c[0]), str(c[1])),
    )
    return normalized, notes


# ---------------------------------------------------------------------------
# (b) Syntactic determinism check
# ---------------------------------------------------------------------------

def _transition_signature(t: dict) -> tuple[Any, tuple[Any, ...]]:
    """Full behavioral identity of a transition, beyond the ``(from, top,
    read)`` key already used to group it: the target state and the exact
    (topmost-first) ``push`` replacement. Two transitions in the same group
    that agree on this signature are the SAME transition written twice
    (e.g. duplicated by a merge or a copy-paste in the proof_sketch), not a
    nondeterministic conflict -- see ``check_determinism``."""
    push = t.get("push") or []
    return (t.get("to"), tuple(push))


def _group_by_pair(transitions: list) -> dict[tuple[Any, Any], dict[Any, list[dict]]]:
    """Group ``transitions`` by ``(from, top)`` and then by normalized
    ``read``, deduplicating exact duplicates (same ``_transition_signature``,
    i.e. same ``to`` + ``push``) within each ``(from, top, read)`` bucket as
    they are inserted.

    This is the ONE place both ``check_determinism`` and ``dpda_run`` build
    their ``(state, top) -> read -> [transitions]`` index, so an exact
    duplicate transition (a proof_sketch's redundant copy-paste) is invisible
    to both in the same way: ``check_determinism`` must not flag it as a
    conflict, and -- the reviewer finding this fixes -- ``dpda_run`` must not
    treat the (state, top, read) pair as having "zero or more than one"
    transition defined (its ``len(...) == 1`` / ``len(...) != 1`` checks)
    just because the same transition happens to be listed twice. Before this
    helper existed, ``check_determinism`` deduplicated by signature but
    ``dpda_run`` built its own ``by_pair`` straight from ``transitions``
    without deduplicating, so a DPDA with an exact duplicate transition could
    be reported deterministic (``check_determinism(dpda) == []``) while
    ``dpda_run`` silently behaved as if NO transition were defined there
    (neither the single-letter-match nor the single-epsilon-match condition
    held), rejecting words the same DPDA without the duplicate would accept.
    """
    by_pair: dict[tuple[Any, Any], dict[Any, list[dict]]] = {}
    for t in transitions:
        if not isinstance(t, dict):
            continue
        key = (t.get("from"), t.get("top"))
        bucket = by_pair.setdefault(key, {}).setdefault(_normalize_read(t.get("read")), [])
        sig = _transition_signature(t)
        if any(_transition_signature(existing) == sig for existing in bucket):
            continue
        bucket.append(t)
    return by_pair


def check_determinism(dpda: dict) -> list[str]:
    """Purely syntactic DPDA determinism check (docs/VERDICT_POLICY.md R2'):
    for every (state, stack_top) pair seen among ``dpda["transitions"]``, at
    most one transition per input letter, and an epsilon transition (``read``
    normalizing to ``None`` -- see ``_normalize_read``) never coexists with a
    letter transition for the same (state, stack_top).

    Before transitions sharing a ``(state, top, read)`` key are counted,
    exact duplicates -- transitions that also agree on ``to`` and ``push``
    (see ``_transition_signature``) -- are collapsed to one: an identical
    transition listed twice is redundant bookkeeping, not a genuine
    nondeterministic choice between two different continuations. Only
    transitions that disagree on where they go or what they push still
    count as a conflict.

    Returns a list of human-readable conflict descriptions (empty list means
    deterministic). This is independent of structural well-formedness
    (unknown states/symbols, missing fields) -- see ``to_cfl_pda``, which
    checks that separately and raises ``DPDAFormatError``. Expects an
    already-normalized ``dpda`` (see ``normalize_epsilon_accept_sinks``) --
    it does not itself distinguish an accept-sink epsilon transition from any
    other kind of non-determinism; a caller that skips normalization first
    will see a normalizable epsilon-into-accept-sink reported as a plain
    conflict, same as any other.
    """
    transitions = dpda.get("transitions") or []
    by_pair = _group_by_pair(transitions)

    conflicts: list[str] = []
    for (state, top), by_read in by_pair.items():
        has_epsilon = None in by_read
        has_letter = any(r is not None for r in by_read)
        if has_epsilon and has_letter:
            letters = sorted(r for r in by_read if r is not None)
            conflicts.append(
                f"(q={state!r}, top={top!r}): epsilon transition coexists with "
                f"letter transition(s) on {letters!r} -- not deterministic"
            )
        for read, unique_ts in by_read.items():
            if len(unique_ts) > 1:
                targets = [tt.get("to") for tt in unique_ts]
                conflicts.append(
                    f"(q={state!r}, top={top!r}, read={read!r}): {len(unique_ts)} transitions "
                    f"defined (to {targets!r}) -- at most one is allowed"
                )
    return conflicts


# ---------------------------------------------------------------------------
# (c) Deterministic simulation
# ---------------------------------------------------------------------------

def _accept_configs_set(dpda: dict) -> set[tuple[Any, Any]]:
    result: set[tuple[Any, Any]] = set()
    for c in dpda.get("accept_configs") or []:
        if isinstance(c, dict):
            result.add((c.get("q"), c.get("top")))
        elif isinstance(c, (list, tuple)) and len(c) == 2:
            result.add((c[0], c[1]))
    return result


def dpda_run(dpda: dict, word: str) -> bool | None:
    """Dedicated DETERMINISTIC simulator for the ``dpda`` proof_sketch contract
    (docs/VERDICT_POLICY.md R2') -- NOT the general NPDA/BFS explorer in
    ``cfl_system.lib.pda_simulator`` (``to_cfl_pda`` + ``pda_accepts`` still
    exists for compatibility, but does not understand ``accept_configs`` and
    must not be used to simulate a normalized ``dpda``).

    At every step: take the letter transition for ``(state, stack_top,
    next_char)`` if input remains and one is defined; otherwise take the
    epsilon transition for ``(state, stack_top)`` if that is the ONLY one
    defined there (zero or more-than-one -> no epsilon move is taken here).
    "Defined" is counted after ``_group_by_pair``'s deduplication by
    ``_transition_signature`` -- an exact duplicate transition (same ``to``
    and ``push`` as another one already listed for the same (state, top,
    read)) is collapsed to a single entry first, the same way
    ``check_determinism`` sees it, so a duplicated transition is simulated
    exactly as if it had been written once, not treated as "more than one"
    (which would wrongly block both the letter and the epsilon move). Once
    input is exhausted, the configuration is accepting if:

    - ``accept_mode == "empty_stack"``: the stack is empty (unchanged,
      pre-existing semantics -- ``accept_states``/``accept_configs`` are
      ignored in this mode);
    - otherwise (``"final_state"`` or absent, the default): the current state
      is in ``accept_states``, OR the pair ``(state, stack_top)`` is in
      ``accept_configs`` (populated by ``normalize_epsilon_accept_sinks`` --
      see its docstring for why config-level acceptance, not a blanket state
      marking, is what makes the rewrite sound without any extra guard).

    If not yet accepting and input is exhausted, a further deterministic
    epsilon move (same "only one defined" rule) is taken and acceptance is
    re-checked, repeating until accepting, stuck (no further move -> reject),
    or a step budget is exceeded -- the last case returns ``None`` (not an
    exception; a genuinely deterministic DPDA never hits this, so it is
    purely a defensive bound against a pathological epsilon cycle a caller
    handed in). Raises ``DPDAFormatError`` only for a structurally
    unusable ``dpda`` (missing ``start``, missing/empty ``initial_stack``, or
    an unrecognized ``accept_mode``) -- anything else (unknown state/symbol
    names, `push`ing an out-of-alphabet symbol) is simply followed literally;
    such names never match any transition key and the run gets stuck and
    rejects, it does not crash.
    """
    if not isinstance(dpda, dict):
        raise DPDAFormatError("dpda must be an object")

    start = dpda.get("start")
    initial_stack = dpda.get("initial_stack")
    if start is None:
        raise DPDAFormatError("dpda must have a 'start' state")
    if not isinstance(initial_stack, list) or not initial_stack:
        raise DPDAFormatError("dpda.initial_stack must be a non-empty list (topmost-first)")
    transitions = dpda.get("transitions") or []
    if not isinstance(transitions, list):
        raise DPDAFormatError("dpda.transitions must be a list")

    accept_mode = dpda.get("accept_mode")
    if accept_mode not in (None, "final_state", "empty_stack"):
        raise DPDAFormatError(f"unknown accept_mode {accept_mode!r}")
    empty_stack_mode = accept_mode == "empty_stack"

    accept_states = set(dpda.get("accept_states") or [])
    accept_configs = _accept_configs_set(dpda)
    if not empty_stack_mode and not accept_states and not accept_configs:
        raise DPDAFormatError(
            "dpda must specify non-empty 'accept_states' or 'accept_configs' "
            "(or accept_mode 'empty_stack')"
        )

    by_pair = _group_by_pair(transitions)

    def accepting(state: Any, stack: list) -> bool:
        if empty_stack_mode:
            return not stack
        top = stack[0] if stack else None
        return state in accept_states or (state, top) in accept_configs

    def eps_step(state: Any, stack: list) -> tuple[Any, list] | None:
        if not stack:
            return None
        top = stack[0]
        eps_ts = by_pair.get((state, top), {}).get(None, [])
        if len(eps_ts) != 1:
            return None
        t = eps_ts[0]
        push = list(t.get("push") or [])
        return t.get("to"), push + stack[1:]

    state = start
    stack = list(initial_stack)
    pos = 0
    n = len(word)
    steps = 0

    while True:
        steps += 1
        if steps > _MAX_STEPS:
            return None

        if pos < n and stack:
            top = stack[0]
            letter_ts = by_pair.get((state, top), {}).get(word[pos], [])
            if len(letter_ts) == 1:
                t = letter_ts[0]
                push = list(t.get("push") or [])
                stack = push + stack[1:]
                state = t.get("to")
                pos += 1
                continue

        if pos == n:
            if accepting(state, stack):
                return True
            step = eps_step(state, stack)
            if step is None:
                return False
            state, stack = step
            continue

        # Input remains but no letter transition matched -- only a
        # deterministic epsilon move can still make progress.
        step = eps_step(state, stack)
        if step is None:
            return False
        state, stack = step


def dpda_accepts(dpda: dict, word: str) -> bool:
    """``bool``-returning wrapper over ``dpda_run`` (docs/VERDICT_POLICY.md
    R2') for callers that want the older exception-based contract.

    Raises ``DPDAFormatError`` for a structurally invalid ``dpda`` (see
    ``dpda_run``), or ``TimeoutError`` if the step budget (10_000) is
    exceeded (e.g. a pathological epsilon cycle) -- callers should treat that
    as inconclusive, not as a definite rejection.
    """
    result = dpda_run(dpda, word)
    if result is None:
        raise TimeoutError(
            f"dpda_run exceeded the {_MAX_STEPS}-step budget while simulating {word!r}"
        )
    return result


# ---------------------------------------------------------------------------
# (d) Conversion (compatibility-only structural-validity check)
# ---------------------------------------------------------------------------

def to_cfl_pda(dpda: dict) -> dict:
    """Convert the ``dpda`` proof_sketch contract to cfl_system.lib.pda_simulator's
    input format:

    - ``read``/``top`` -> ``input``/``stack_top``
    - ``push`` topmost-first -> last-pushed-on-top (reversed), the same
      convention as ``cfl_system.lib.cfl_oracle_test.normalize_agent_pda``
    - ``accept_states`` / ``accept_mode`` -> the simulator's single
      ``accept_mode`` + (for ``final_state``) ``accept_states``
    - a multi-symbol ``initial_stack`` (topmost-first) is unrolled via one
      synthetic epsilon transition from a fresh start state, since the
      simulator only takes a single ``start_stack`` symbol

    **Compatibility only.** This conversion (and the general NPDA/BFS
    simulator it feeds into, via ``pda_accepts``) does NOT understand
    ``accept_configs`` -- it has no notion of a stack-top-conditioned accept
    condition, only whole-state ``accept_states``. It exists purely as a
    structural-validity check (unknown states/symbols, missing required
    fields) that ``_verify_stack_strategy_dpda`` in ``oracle_verifier.py``
    still runs before simulating; the actual simulation against the task's
    language oracle uses ``dpda_run``, never this conversion, precisely
    because a normalized ``dpda``'s ``accept_configs`` entries would be
    silently dropped here.

    Raises ``DPDAFormatError`` for structurally invalid input (missing
    states/start/stack_alphabet, malformed ``initial_stack``, an
    inconsistent accept_mode/accept_states pairing, or anything
    ``cfl_system.lib.pda_simulator.validate_pda`` itself rejects) before any
    simulation is attempted.
    """
    if not isinstance(dpda, dict):
        raise DPDAFormatError("dpda must be an object")

    states = list(dpda.get("states") or [])
    start = dpda.get("start")
    stack_alphabet = list(dpda.get("stack_alphabet") or [])
    initial_stack = dpda.get("initial_stack")
    transitions = dpda.get("transitions") or []

    if not states or start is None or not stack_alphabet:
        raise DPDAFormatError(
            "dpda must have non-empty 'states', a 'start' state, and non-empty "
            "'stack_alphabet'"
        )
    if not isinstance(initial_stack, list) or not initial_stack:
        raise DPDAFormatError("dpda.initial_stack must be a non-empty list (topmost-first)")
    if not isinstance(transitions, list):
        raise DPDAFormatError("dpda.transitions must be a list")

    input_alphabet = sorted(
        {
            _normalize_read(t.get("read"))
            for t in transitions
            if isinstance(t, dict) and _normalize_read(t.get("read")) is not None
        }
    )

    out_transitions = [
        {
            "from": t.get("from"),
            "input": _normalize_read(t.get("read")),
            "stack_top": t.get("top"),
            "to": t.get("to"),
            "push": list(reversed(t.get("push") or [])),
        }
        for t in transitions
        if isinstance(t, dict)
    ]

    accept_states = dpda.get("accept_states")
    accept_mode = dpda.get("accept_mode")
    if accept_mode == "empty_stack":
        mode = "empty_stack"
        final_accept_states: list = []
    elif accept_states:
        mode = "final_state"
        final_accept_states = list(accept_states)
    elif accept_mode == "final_state":
        raise DPDAFormatError("accept_mode 'final_state' requires non-empty 'accept_states'")
    else:
        raise DPDAFormatError(
            "dpda must specify non-empty 'accept_states' or 'accept_mode' "
            "('final_state' or 'empty_stack')"
        )

    real_start = start
    real_start_stack = initial_stack[-1]
    if len(initial_stack) > 1:
        # Desired final stack tuple (bottom..top) is initial_stack reversed;
        # a synthetic epsilon transition from a fresh state pops the bottom
        # marker and pushes exactly that tuple back (see module docstring's
        # derivation: push_list = list(reversed(initial_stack))).
        synthetic = "__dpda_init__"
        suffix = 0
        while synthetic in states:
            suffix += 1
            synthetic = f"__dpda_init__{suffix}"
        states = [*states, synthetic]
        out_transitions.append({
            "from": synthetic,
            "input": None,
            "stack_top": real_start_stack,
            "to": start,
            "push": list(reversed(initial_stack)),
        })
        real_start = synthetic

    pda: dict[str, Any] = {
        "states": states,
        "input_alphabet": input_alphabet,
        "stack_alphabet": stack_alphabet,
        "start_state": real_start,
        "start_stack": real_start_stack,
        "accept_mode": mode,
        "transitions": out_transitions,
    }
    if mode == "final_state":
        pda["accept_states"] = final_accept_states

    errors = validate_pda(pda)
    if errors:
        raise DPDAFormatError("invalid PDA after conversion: " + "; ".join(errors))
    return pda
