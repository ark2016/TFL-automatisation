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

Two independent checks, per docs/VERDICT_POLICY.md R2':

- ``check_determinism`` -- a purely SYNTACTIC check (no more than one transition per
  (state, stack_top, letter); an epsilon transition never coexists with a letter
  transition for the same (state, stack_top)). This is a *complete* decision
  procedure, so a clean result earns ``trust: verified`` for the determinism
  property itself; a violation is a genuine, deterministic counterexample
  (``refuted``), never a bounded/sampled judgement.
- ``dpda_accepts`` -- simulates the DPDA on a word via `cfl_system.lib.pda_simulator`,
  after converting field names/push order/accept mode via ``to_cfl_pda``.
"""
from __future__ import annotations

from typing import Any

from cfl_system.lib.pda_simulator import pda_accepts, validate_pda


class DPDAFormatError(ValueError):
    """Raised when a ``dpda`` dict is too malformed to check or simulate."""


# ---------------------------------------------------------------------------
# (a) Syntactic determinism check
# ---------------------------------------------------------------------------

def check_determinism(dpda: dict) -> list[str]:
    """Purely syntactic DPDA determinism check (docs/VERDICT_POLICY.md R2'):
    for every (state, stack_top) pair seen among ``dpda["transitions"]``, at
    most one transition per input letter, and an epsilon transition (``read``
    is ``None``) never coexists with a letter transition for the same
    (state, stack_top).

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
        by_pair.setdefault(key, {}).setdefault(t.get("read"), []).append(t)

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
# (b) Conversion + simulation
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
        {t.get("read") for t in transitions if isinstance(t, dict) and t.get("read") is not None}
    )

    out_transitions = [
        {
            "from": t.get("from"),
            "input": t.get("read"),
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
