"""Tests for dcfl_system.lib.dpda: syntactic determinism check + conversion/
simulation via cfl_system.lib.pda_simulator (docs/VERDICT_POLICY.md R2')."""
from __future__ import annotations

import pytest

from dcfl_system.lib.dpda import (
    DPDAFormatError,
    check_determinism,
    dpda_accepts,
    to_cfl_pda,
)


# ---------------------------------------------------------------------------
# A real DPDA for L = {a^n b^n c^m | n>=1, m>=1} (dcfl_system/examples/task_anbncm.json)
# ---------------------------------------------------------------------------

ANBNCM_DPDA = {
    "states": ["q_push", "q_pop", "q_c"],
    "start": "q_push",
    "accept_states": ["q_c"],
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
        {"from": "q_push", "read": "a", "top": "A", "to": "q_push", "push": ["A", "A"]},
        # First 'b' commits to the pop phase -- q_pop has no 'a' transition,
        # so an 'a' after any 'b' has started is correctly rejected (an
        # earlier single-state design conflated push/pop on the same stack
        # top 'A' and wrongly accepted interleavings like "aababbc").
        {"from": "q_push", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
    ],
}


class TestCheckDeterminismValid:
    def test_anbncm_dpda_is_deterministic(self):
        assert check_determinism(ANBNCM_DPDA) == []


class TestCheckDeterminismInvalid:
    def test_two_transitions_same_letter_conflict(self):
        bad = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_push", "read": "a", "top": "Z0", "to": "q_c", "push": []},
            ],
        }
        conflicts = check_determinism(bad)
        assert conflicts
        assert any("q_push" in c and "read='a'" in c for c in conflicts)

    def test_epsilon_and_letter_coexist_conflict(self):
        bad = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_pop", "read": None, "top": "A", "to": "q_c", "push": []},
            ],
        }
        conflicts = check_determinism(bad)
        assert conflicts
        assert any("epsilon transition coexists" in c for c in conflicts)

    @pytest.mark.parametrize("spelling", ["", "ε", "eps", "epsilon", "EPS", "Epsilon"])
    def test_epsilon_written_as_string_still_coexistence_conflicts(self, spelling):
        """An epsilon transition recorded as a string (any of dpda.py's
        recognized spellings, case-insensitive) must be normalized to the
        canonical `None` before the coexistence check, so it is caught
        exactly like a `None`-read epsilon transition (reviewer finding:
        a string-spelled epsilon used to be treated as an ordinary,
        distinct 'letter', silently hiding a real non-determinism)."""
        bad = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_pop", "read": spelling, "top": "A", "to": "q_c", "push": []},
            ],
        }
        conflicts = check_determinism(bad)
        assert conflicts
        assert any("epsilon transition coexists" in c for c in conflicts)

    @pytest.mark.parametrize("spelling", ["", "ε", "eps", "epsilon"])
    def test_two_string_spelled_epsilon_transitions_on_same_pair_conflict(self, spelling):
        """Two epsilon transitions on the same (state, top), one spelled
        `None` and one spelled as a string, must be recognized as the SAME
        read (both normalize to `None`) and therefore reported as a
        multiple-transition conflict, not silently accepted as two
        different reads."""
        dpda = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_c", "read": None, "top": "Z0", "to": "q_pop", "push": ["Z0"]},
                {"from": "q_c", "read": spelling, "top": "Z0", "to": "q_push", "push": ["Z0"]},
            ],
        }
        conflicts = check_determinism(dpda)
        assert conflicts
        assert any("q_c" in c and "2 transitions" in c for c in conflicts)


# ---------------------------------------------------------------------------
# Conversion + simulation
# ---------------------------------------------------------------------------

class TestToCflPda:
    def test_converts_push_order_and_accept_mode(self):
        pda = to_cfl_pda(ANBNCM_DPDA)
        assert pda["accept_mode"] == "final_state"
        assert pda["accept_states"] == ["q_c"]
        assert pda["start_state"] == "q_push"
        assert pda["start_stack"] == "Z0"
        # topmost-first ["A", "Z0"] -> simulator's last-pushed-on-top ["Z0", "A"]
        first = next(t for t in pda["transitions"] if t["from"] == "q_push" and t["input"] == "a"
                     and t["stack_top"] == "Z0")
        assert first["push"] == ["Z0", "A"]

    def test_missing_required_fields_raises(self):
        with pytest.raises(DPDAFormatError):
            to_cfl_pda({"states": [], "start": "q0", "stack_alphabet": ["Z"],
                        "initial_stack": ["Z"], "transitions": []})

    def test_missing_accept_info_raises(self):
        bad = {**ANBNCM_DPDA, "accept_states": None}
        bad.pop("accept_states", None)
        with pytest.raises(DPDAFormatError):
            to_cfl_pda(bad)

    @pytest.mark.parametrize("spelling", ["", "ε", "eps", "epsilon"])
    def test_string_spelled_epsilon_normalizes_to_none(self, spelling):
        """`read` written as a string epsilon spelling must convert to the
        same `input: None` the simulator expects for an epsilon move (not a
        literal one-character input alphabet symbol)."""
        dpda = {
            "states": ["p0", "p_accept"],
            "start": "p0",
            "accept_states": ["p_accept"],
            "stack_alphabet": ["Z0"],
            "initial_stack": ["Z0"],
            "transitions": [
                {"from": "p0", "read": spelling, "top": "Z0", "to": "p_accept", "push": ["Z0"]},
            ],
        }
        pda = to_cfl_pda(dpda)
        assert pda["transitions"][0]["input"] is None
        assert spelling not in pda["input_alphabet"]

    def test_multi_symbol_initial_stack(self):
        # A trivial DPDA whose initial stack already has two symbols
        # (topmost-first: "A" on top of "Z0"), immediately accepting on
        # empty input once "A" is popped.
        dpda = {
            "states": ["p0", "p_accept"],
            "start": "p0",
            "accept_states": ["p_accept"],
            "stack_alphabet": ["Z0", "A"],
            "initial_stack": ["A", "Z0"],
            "transitions": [
                {"from": "p0", "read": None, "top": "A", "to": "p_accept", "push": []},
            ],
        }
        assert dpda_accepts(dpda, "") is True

    def test_string_spelled_epsilon_is_simulated_as_epsilon(self):
        """A DPDA whose only path to acceptance goes through a `read: "eps"`
        transition must be simulated exactly like `read: None` -- a string
        epsilon spelling that is not normalized would look like it wants an
        input symbol literally named "eps" and reject every real word."""
        dpda = {
            "states": ["p0", "p_accept"],
            "start": "p0",
            "accept_states": ["p_accept"],
            "stack_alphabet": ["Z0", "A"],
            "initial_stack": ["A", "Z0"],
            "transitions": [
                {"from": "p0", "read": "eps", "top": "A", "to": "p_accept", "push": []},
            ],
        }
        assert dpda_accepts(dpda, "") is True


class TestDpdaAcceptsSimulation:
    @pytest.mark.parametrize("word,expected", [
        ("abc", True),
        ("aabbcc", True),
        ("aaabbbccc", True),
        ("aabbc", True),
        ("", False),
        ("bbcc", False),      # n=0
        ("aabb", False),      # m=0
        ("aabbb", False),     # n=2, b-count=3
        ("aabbbcc", False),   # n=2, b-count=3
        ("aabbcccd", False),  # trailing symbol outside alphabet
    ])
    def test_membership(self, word, expected):
        assert dpda_accepts(ANBNCM_DPDA, word) is expected

    def test_empty_stack_mode(self):
        # A DPDA for {a^n b^n c | n>=1} (single trailing 'c'), accepting by
        # empty stack instead of final state: the bottom marker Z0 is popped
        # by the one 'c' that must be the last input symbol -- a genuine
        # empty-stack acceptance can only fire once nothing else remains to
        # read, since the simulator has no transitions once the stack is
        # empty (docs note in this test module).
        dpda = {
            "states": ["q0", "q_count"],
            "start": "q0",
            "accept_mode": "empty_stack",
            "stack_alphabet": ["Z0", "A"],
            "initial_stack": ["Z0"],
            "transitions": [
                {"from": "q0", "read": "a", "top": "Z0", "to": "q_count", "push": ["A", "Z0"]},
                {"from": "q_count", "read": "a", "top": "A", "to": "q_count", "push": ["A", "A"]},
                {"from": "q_count", "read": "b", "top": "A", "to": "q_count", "push": []},
                {"from": "q_count", "read": "c", "top": "Z0", "to": "q_count", "push": []},
            ],
        }
        assert dpda_accepts(dpda, "aabbc") is True
        assert dpda_accepts(dpda, "aabbcc") is False  # nothing left to consume 2nd 'c'
        assert dpda_accepts(dpda, "aabb") is False
