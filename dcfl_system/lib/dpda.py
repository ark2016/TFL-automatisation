"""Deterministic pushdown automaton (DPDA) support for the DCFL stack_strategy
contract (docs/VERDICT_POLICY.md R2').

The `dpda` proof_sketch field (prompts/stack_strategy.md, dcfl_system/tz_dcfl_agent_system.md
§5.2) uses its own field names -- `read`/`top` (not cfl_system's `input`/`stack_top`),
`push` topmost-first (same convention as cfl_system/prompts/cfl_pda_builder.md), and
`initial_stack` a topmost-first list (usually a single bottom marker) -- so it needs a
small adapter, not a full re-implementation: dcfl MAY import from cfl_system
(root CLAUDE.md), and cfl_system/lib/pda_simulator.py already supports epsilon
transitions and both acceptance modes (`final_state` / `empty_stack`), so this module
reuses it rather than duplicating a second BFS simulator.

Three functions, per docs/VERDICT_POLICY.md R2':

- ``normalize_epsilon_accept_sinks`` -- the ONE canonical, language-preserving
  rewrite the policy allows *before* determinism is checked (see its own
  docstring for the equivalence argument). Callers (``_verify_stack_strategy_dpda``
  in ``oracle_verifier.py``) run this FIRST and check/simulate the returned,
  normalized automaton -- ``check_determinism`` and ``dpda_accepts`` themselves
  do not call it and do not know about the rewrite; they just take whatever
  ``dpda`` dict they are handed at face value.
- ``check_determinism`` -- a purely SYNTACTIC check (no more than one transition per
  (state, stack_top, letter); an epsilon transition never coexists with a letter
  transition for the same (state, stack_top)). This is a *complete* decision
  procedure, so a clean result earns ``trust: verified`` for the determinism
  property itself; a violation is a genuine, deterministic counterexample
  (``refuted``), never a bounded/sampled judgement. Expects an already-normalized
  ``dpda`` (see above) -- it does not itself distinguish an accept-sink epsilon
  transition from any other kind of non-determinism.
- ``dpda_accepts`` -- simulates the DPDA on a word via `cfl_system.lib.pda_simulator`,
  after converting field names/push order/accept mode via ``to_cfl_pda``. Also
  expects an already-normalized ``dpda``; simulating the un-normalized original
  gives the same language (the rewrite is language-preserving), so this is only
  a matter of consistency with ``check_determinism`` seeing the same automaton.
"""
from __future__ import annotations

from typing import Any

from cfl_system.lib.pda_simulator import pda_accepts, validate_pda


class DPDAFormatError(ValueError):
    """Raised when a ``dpda`` dict is too malformed to check or simulate."""


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

def _pushes_only_onto(dpda: dict, transitions: list, state: Any, top: Any) -> bool:
    """True iff, given ``dpda``'s own push/``initial_stack`` semantics (both
    topmost-first, exactly the ``dpda`` contract -- NOT the converted
    ``to_cfl_pda`` format), ``state`` can only ever become the current state
    with ``top`` on top of the stack:

    - if ``state`` is the DPDA's start state, ``initial_stack``'s topmost
      symbol (``initial_stack[0]``) must be ``top``;
    - AND every transition that targets ``state`` (``t2["to"] == state``,
      including a self-loop) must PUSH a non-empty list whose first
      (topmost) element is ``top``.

    This is a purely local, non-recursive check -- it never needs to reason
    about what was on the stack *before* such a transition fired, because
    pushing a non-empty list always makes its first element the new top
    unconditionally (that is what "push" means), regardless of the rest of
    the stack. A transition that instead POPS (``push == []``) leaves the new
    top dependent on whatever is uncovered underneath, which varies from run
    to run -- such an incoming transition is treated as disqualifying, since
    nothing here can rule out some OTHER top occurring at ``state`` through
    it. See ``normalize_epsilon_accept_sinks`` for why this exact property is
    the one that makes marking ``state`` unconditionally accepting sound.
    """
    if dpda.get("start") == state:
        initial_stack = dpda.get("initial_stack")
        if not isinstance(initial_stack, list) or not initial_stack or initial_stack[0] != top:
            return False
    for t2 in transitions:
        if not isinstance(t2, dict) or t2.get("to") != state:
            continue
        push2 = t2.get("push")
        if not push2 or push2[0] != top:
            return False
    return True


def normalize_epsilon_accept_sinks(dpda: dict) -> tuple[dict, list[str]]:
    """Canonical, language-preserving rewrite allowed by docs/VERDICT_POLICY.md
    R2' before determinism is checked: for every epsilon transition
    ``(q, Z) -> q_acc`` where ``q_acc`` is an accept state (``accept_states``)
    that has NO outgoing transitions of its own (neither by letter nor by a
    further epsilon) AND ``q`` can only ever be the current state with ``Z``
    on top of the stack (``_pushes_only_onto`` -- see below for why this
    extra condition is necessary, beyond what the policy text spells out),
    delete that transition and mark ``q`` itself as accepting instead.
    Returns ``(normalized_dpda, notes)`` -- ``notes`` is a human-readable
    list of every rewrite performed (empty if none applied), meant to be
    recorded verbatim in ``details["normalization"]`` by the caller
    (``oracle_verifier._verify_stack_strategy_dpda``).

    Applies ONLY when ``accept_mode`` is ``"final_state"`` or absent (the
    contract's default -- see ``to_cfl_pda``); under ``"empty_stack"``
    acceptance has nothing to do with the current state, so an epsilon into a
    dedicated accepting sink is not this pattern at all and is left alone.
    Any other epsilon transition -- into a state that DOES have outgoing
    transitions, into a non-accepting state, or whose source state ``q`` can
    ALSO occur with some other stack top -- is never touched; per R2', one
    coexisting with a letter transition on the same (state, top) remains
    genuine non-determinism (``refuted``), precisely because nothing here
    can vouch for it.

    **Why marking ``q`` accepting needs ``q`` to have a single possible top,
    not merely ``q_acc`` being a dead end (a real counterexample).**
    ``final_state`` acceptance checks ONLY the current state, never the
    stack -- that is the entire mechanism the epsilon transition being
    removed relies on: it is the ONE thing in the whole automaton that
    conditions "become accepting" on the stack showing exactly ``Z``. If the
    very same state ``q`` is ALSO reachable with some OTHER top ``Z' != Z``
    (typically because ``q`` doubles as a "still counting" state for a pop
    loop, entered both by the transition that first sets ``top = Z`` and by
    a self-loop / other transition that pops down to ``top = Z'`` first),
    deleting the epsilon and blanket-marking ``q`` as accepting would ALSO
    accept whenever input happens to run out at ``(q, Z')`` -- a
    configuration the ORIGINAL automaton correctly rejected (it simply had
    no applicable transition there, letter or epsilon, and finished in a
    non-accepting state). Concretely: the live dcfl-04 proof_sketch's `dpda`
    for {aⁿbⁿcᵐ} (docs/VERDICT_POLICY.md R2' precedent) has states `q0`,
    `q_b`, `q_c`, each with an epsilon `(state, top=Z0) -> q_accept` where
    `q_accept` has no outgoing transitions. `q0` and `q_c` satisfy
    `_pushes_only_onto(..., top="Z0")` (every transition landing on them
    pushes `"Z0"` as the new top, including `q0` being the start state
    with `initial_stack == ["Z0"]`), so normalizing them is sound. `q_b`
    does NOT: it is entered both by `q0` reading the FIRST `"b"`
    (`top=Z0`, a real state, e.g. n=0) and by its OWN self-loop popping an
    `"A"` (`top=A`, `push=[]`) once per extra `"b"` while counting down a
    block of more than one `a`. Blanket-marking `q_b` accepting would then
    (wrongly) accept `"aab"` (2 a's, 1 b -- input ends at `(q_b, top="A")`,
    mid pop, one `"A"` still unmatched) purely because the automaton halts
    IN `q_b`, even though the original, un-normalized automaton correctly
    rejects it (`dpda_accepts` on the un-normalized dict returns `False` for
    `"aab"`, verified in tests) -- this is exactly the counterexample
    ``_pushes_only_onto`` is designed to catch and rule out (see
    ``test_dpda.py``'s ``TestNormalizeEpsilonAcceptSinks`` for the concrete
    regression test using this precise automaton). `q_b`'s epsilon therefore
    stays untouched by this function and remains genuine non-determinism at
    ``check_determinism`` (an epsilon coexisting with a letter transition on
    `(q_b, "Z0")`) -- a real defect in that particular submitted `dpda` that
    only a genuine redesign (e.g. a dedicated "bottom of this counting
    block" stack symbol so the LAST pop is a distinct, letter-triggered
    transition into its own accepting state -- see
    `dcfl_system/prompts/stack_strategy.md`'s rewritten example) can fix, not
    this mechanical rewrite.

    **Proof of equivalence (for a qualifying ``q``).** Fix an epsilon
    transition ``t = (q, Z) -> q_acc`` where ``q_acc`` has no outgoing
    transitions, and ``_pushes_only_onto(dpda, transitions, q, Z)`` holds --
    i.e. every configuration the automaton is EVER in with current state
    ``q`` has stack-top exactly ``Z`` (by construction: ``q`` becomes current
    only by firing some transition targeting it, and any such transition
    either is the start-state initialization with ``initial_stack[0] == Z``,
    or pushes a non-empty list whose first element is ``Z`` -- pushing a
    non-empty list always makes its first element the new top, regardless of
    what was underneath, so this holds independent of run history). So every
    configuration ``(q, Z·rest)`` reached with input exhausted is, in the
    ORIGINAL automaton, resolved purely by whether ``t`` fires: (i) if it
    doesn't (or there is no more choice because input is exhausted and ``t``
    is an epsilon move that -- being a genuine DPDA transition -- the
    automaton takes deterministically whenever no letter move competes),
    acceptance is decided by whether ``q`` itself is accepting, exactly what
    the rewrite produces directly by adding ``q`` to ``accept_states``; (ii)
    if it does fire, the run halts in ``q_acc`` (no further transition is
    possible, whatever ``t`` pushes, since ``q_acc`` has none), which is
    accepting -- again matching "accept because input ran out in ``q``".
    Since ``q`` is NEVER associated with any stack top other than ``Z``
    (that is exactly what ``_pushes_only_onto`` establishes), there is no
    OTHER configuration -- reached with input exhausted or not -- whose
    acceptance status the rewrite could disturb: every occurrence of ``q``
    behaves identically to how it behaved via ``t``, so deleting ``t`` and
    marking ``q`` accepting changes nothing else. Applying this argument
    independently to every qualifying epsilon transition (each rewrite only
    ever adds a state to ``accept_states`` and removes one transition whose
    target becomes unreachable once removed) preserves the language of the
    whole automaton.
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
    new_accept_states = list(accept_states)
    already_accepting = set(accept_states)
    for t in transitions:
        if (
            isinstance(t, dict)
            and _normalize_read(t.get("read")) is None
            and t.get("to") in sink_accepts
            and _pushes_only_onto(dpda, transitions, t.get("from"), t.get("top"))
        ):
            q = t.get("from")
            notes.append(
                f"removed epsilon transition (q={q!r}, top={t.get('top')!r}) -> "
                f"{t.get('to')!r} (accepting sink with no outgoing transitions, and "
                f"{q!r} only ever occurs with top={t.get('top')!r}); marked {q!r} as "
                f"accepting directly instead"
            )
            if q not in already_accepting:
                new_accept_states.append(q)
                already_accepting.add(q)
        else:
            kept.append(t)

    if not notes:
        return dpda, []

    normalized = dict(dpda)
    normalized["transitions"] = kept
    normalized["accept_states"] = new_accept_states
    return normalized, notes


# ---------------------------------------------------------------------------
# (b) Syntactic determinism check
# ---------------------------------------------------------------------------

def check_determinism(dpda: dict) -> list[str]:
    """Purely syntactic DPDA determinism check (docs/VERDICT_POLICY.md R2'):
    for every (state, stack_top) pair seen among ``dpda["transitions"]``, at
    most one transition per input letter, and an epsilon transition (``read``
    normalizing to ``None`` -- see ``_normalize_read``) never coexists with a
    letter transition for the same (state, stack_top).

    Returns a list of human-readable conflict descriptions (empty list means
    deterministic). This is independent of structural well-formedness
    (unknown states/symbols, missing fields) -- see ``to_cfl_pda``, which
    checks that separately and raises ``DPDAFormatError``.
    """
    transitions = dpda.get("transitions") or []
    by_pair: dict[tuple[Any, Any], dict[Any, list[dict]]] = {}
    for t in transitions:
        if not isinstance(t, dict):
            continue
        key = (t.get("from"), t.get("top"))
        by_pair.setdefault(key, {}).setdefault(_normalize_read(t.get("read")), []).append(t)

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
        for read, ts in by_read.items():
            if len(ts) > 1:
                targets = [tt.get("to") for tt in ts]
                conflicts.append(
                    f"(q={state!r}, top={top!r}, read={read!r}): {len(ts)} transitions "
                    f"defined (to {targets!r}) -- at most one is allowed"
                )
    return conflicts


# ---------------------------------------------------------------------------
# (c) Conversion + simulation
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


def dpda_accepts(dpda: dict, word: str) -> bool:
    """Simulate ``dpda`` on ``word`` via cfl_system.lib.pda_simulator.

    Raises ``DPDAFormatError`` for a structurally invalid ``dpda``, or
    ``TimeoutError`` if the simulator's step budget (10_000) is exceeded
    (e.g. a pathological epsilon cycle) -- callers should treat that as
    inconclusive, not as a definite rejection.
    """
    pda = to_cfl_pda(dpda)
    return pda_accepts(pda, word)
