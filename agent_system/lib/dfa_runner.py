"""
DFA runner — simulate a deterministic finite automaton on a word.

Per §4.10.3: O(|w|) per word.
"""

from __future__ import annotations

from typing import Any


def run_dfa(dfa: dict[str, Any], word: str) -> bool:
    """Run a word through a DFA transition table.

    Expected DFA format:
    {
        "states": ["q0", "q1", ...],
        "alphabet": ["a", "b"],
        "transitions": {"q0": {"a": "q1", "b": "q0"}, ...},
        "start": "q0",
        "accept": ["q1", ...]
    }

    Returns True if the word is accepted by the DFA.

    Note: DFA alphabet symbols must be single characters.  The word is
    iterated character-by-character, so multi-character alphabet symbols
    are not supported.  Use ``grammar_to_dfa`` (which rejects multi-char
    terminals) to ensure compatibility.
    """
    state = dfa["start"]
    transitions = dfa["transitions"]
    accept = set(dfa["accept"])

    for symbol in word:
        state_transitions = transitions.get(state)
        if state_transitions is None:
            return False  # no transitions from this state — implicit reject
        next_state = state_transitions.get(symbol)
        if next_state is None:
            return False  # no transition on this symbol — implicit reject
        state = next_state

    return state in accept


def validate_dfa(dfa: dict[str, Any]) -> list[str]:
    """Check that a DFA dict is well-formed. Returns list of errors."""
    errors: list[str] = []

    for field in ("states", "alphabet", "transitions", "start", "accept"):
        if field not in dfa:
            errors.append(f"Missing field '{field}'")
    if errors:
        return errors

    states = set(dfa["states"])
    alphabet = set(dfa["alphabet"])

    if dfa["start"] not in states:
        errors.append(f"Start state '{dfa['start']}' not in states")

    for acc in dfa["accept"]:
        if acc not in states:
            errors.append(f"Accept state '{acc}' not in states")

    transitions = dfa["transitions"]
    for state in states:
        if state not in transitions:
            errors.append(f"No transitions defined for state '{state}'")
            continue
        for sym in alphabet:
            if sym not in transitions[state]:
                errors.append(f"Missing transition ({state}, {sym})")
            elif transitions[state][sym] not in states:
                errors.append(
                    f"Transition ({state}, {sym}) -> '{transitions[state][sym]}' "
                    f"goes to unknown state"
                )

    return errors
