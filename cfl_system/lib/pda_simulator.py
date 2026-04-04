"""Pushdown automaton simulator using BFS over configurations.

A configuration is (state, input_position, stack_as_tuple).
The simulator explores all reachable configurations via BFS,
tracking visited ones to avoid infinite loops on epsilon-transitions.
"""

from __future__ import annotations

from collections import deque
from typing import Any

MAX_STEPS = 10_000
MAX_STACK_DEPTH = 1_000

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pda(pda: dict) -> list[str]:
    """Validate PDA definition. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    required_keys = {
        "states", "input_alphabet", "stack_alphabet",
        "start_state", "start_stack", "accept_mode", "transitions",
    }
    missing = required_keys - set(pda.keys())
    if missing:
        errors.append(f"Missing required keys: {sorted(missing)}")
        return errors  # can't validate further

    states = set(pda["states"])
    input_alpha = set(pda["input_alphabet"])
    stack_alpha = set(pda["stack_alphabet"])

    # accept_mode
    accept_mode = pda["accept_mode"]
    if accept_mode not in ("final_state", "empty_stack"):
        errors.append(
            f"accept_mode must be 'final_state' or 'empty_stack', got '{accept_mode}'"
        )

    # start_state
    if pda["start_state"] not in states:
        errors.append(
            f"start_state '{pda['start_state']}' not in states"
        )

    # start_stack
    if pda["start_stack"] not in stack_alpha:
        errors.append(
            f"start_stack '{pda['start_stack']}' not in stack_alphabet"
        )

    # accept_states
    if accept_mode == "final_state":
        accept_states = set(pda.get("accept_states", []))
        if not accept_states:
            errors.append("final_state mode requires non-empty accept_states")
        elif not accept_states.issubset(states):
            bad = accept_states - states
            errors.append(f"accept_states not in states: {sorted(bad)}")

    # transitions
    for i, t in enumerate(pda["transitions"]):
        prefix = f"transition[{i}]"
        if t.get("from") not in states:
            errors.append(f"{prefix}: 'from' state '{t.get('from')}' not in states")
        if t.get("to") not in states:
            errors.append(f"{prefix}: 'to' state '{t.get('to')}' not in states")

        inp = t.get("input")
        if inp is not None and inp not in input_alpha:
            errors.append(
                f"{prefix}: input symbol '{inp}' not in input_alphabet"
            )

        st = t.get("stack_top")
        if st not in stack_alpha:
            errors.append(
                f"{prefix}: stack_top '{st}' not in stack_alphabet"
            )

        for sym in t.get("push", []):
            if sym not in stack_alpha:
                errors.append(
                    f"{prefix}: push symbol '{sym}' not in stack_alphabet"
                )

    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_transition_map(
    transitions: list[dict],
) -> dict[tuple[str, Any, str], list[tuple[str, list[str]]]]:
    """Index transitions by (from_state, input_symbol, stack_top).

    Returns mapping to list of (to_state, push_list).
    input_symbol is either a string or None (epsilon).
    """
    tmap: dict[tuple[str, Any, str], list[tuple[str, list[str]]]] = {}
    for t in transitions:
        key = (t["from"], t.get("input"), t["stack_top"])
        tmap.setdefault(key, []).append((t["to"], t.get("push", [])))
    return tmap


def _accepts_config(
    pda: dict,
    state: str,
    pos: int,
    stack: tuple[str, ...],
    word_len: int,
) -> bool:
    """Check whether a configuration is accepting."""
    if pos != word_len:
        return False
    mode = pda["accept_mode"]
    if mode == "final_state":
        return state in pda.get("accept_states", [])
    else:  # empty_stack
        return len(stack) == 0


# ---------------------------------------------------------------------------
# BFS core
# ---------------------------------------------------------------------------

def _bfs(pda: dict, word: str, detailed: bool = False) -> dict:
    """Run BFS over PDA configurations.

    Returns a result dict used by both public functions.
    """
    tmap = _build_transition_map(pda["transitions"])
    start_stack = (pda["start_stack"],)
    start_state = pda["start_state"]
    word_len = len(word)

    # BFS queue entries: (state, input_position, stack_tuple)
    start_cfg = (start_state, 0, start_stack)
    queue: deque[tuple[str, int, tuple[str, ...]]] = deque()
    queue.append(start_cfg)
    visited: set[tuple[str, int, tuple[str, ...]]] = {start_cfg}

    steps = 0
    max_stack_depth = len(start_stack)
    accepting_config: dict | None = None

    while queue:
        if steps >= MAX_STEPS:
            raise TimeoutError(
                f"PDA simulation exceeded {MAX_STEPS} steps"
            )
        steps += 1

        state, pos, stack = queue.popleft()

        # Check acceptance
        if _accepts_config(pda, state, pos, stack, word_len):
            accepting_config = {
                "state": state,
                "remaining_input": word[pos:],
                "stack": list(stack),
            }
            if not detailed:
                return {"accepted": True}
            return {
                "accepted": True,
                "steps_used": steps,
                "max_stack_depth": max_stack_depth,
                "accepting_config": accepting_config,
                "configs_explored": len(visited),
            }

        # No stack top means no transitions possible (stack empty but not accepted)
        if not stack:
            continue
        stack_top = stack[-1]
        rest_stack = stack[:-1]

        # Gather successor configurations for both real-input and epsilon moves
        keys_to_try: list[tuple[Any, int]] = []
        # epsilon transitions (don't consume input)
        keys_to_try.append((None, pos))
        # real input transitions
        if pos < word_len:
            keys_to_try.append((word[pos], pos + 1))

        for inp_sym, new_pos in keys_to_try:
            key = (state, inp_sym, stack_top)
            for to_state, push_list in tmap.get(key, []):
                new_stack = rest_stack + tuple(push_list)
                if len(new_stack) > MAX_STACK_DEPTH:
                    continue  # skip oversized stacks
                cfg = (to_state, new_pos, new_stack)
                if cfg not in visited:
                    visited.add(cfg)
                    queue.append(cfg)
                    if len(new_stack) > max_stack_depth:
                        max_stack_depth = len(new_stack)

    # Exhausted all configurations without accepting
    if detailed:
        return {
            "accepted": False,
            "steps_used": steps,
            "max_stack_depth": max_stack_depth,
            "accepting_config": None,
            "configs_explored": len(visited),
        }
    return {"accepted": False}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pda_accepts(pda: dict, word: str) -> bool:
    """Check if PDA accepts the word using BFS.

    Args:
        pda: PDA definition dict.
        word: input string.

    Returns:
        True if PDA accepts the word.

    Raises:
        TimeoutError: if max_steps (10000) exceeded.
        ValueError: if PDA definition is invalid.
    """
    errors = validate_pda(pda)
    if errors:
        raise ValueError(f"Invalid PDA: {'; '.join(errors)}")
    return _bfs(pda, word)["accepted"]


def pda_accepts_detailed(pda: dict, word: str) -> dict:
    """Like pda_accepts but returns detailed execution info.

    Returns:
        {
            "accepted": bool,
            "steps_used": int,
            "max_stack_depth": int,
            "accepting_config": {"state": str, "remaining_input": str, "stack": list} | None,
            "configs_explored": int,
        }

    Raises:
        TimeoutError: if max_steps (10000) exceeded.
        ValueError: if PDA definition is invalid.
    """
    errors = validate_pda(pda)
    if errors:
        raise ValueError(f"Invalid PDA: {'; '.join(errors)}")
    return _bfs(pda, word, detailed=True)
