"""Tests for dcfl_system.lib.dpda: syntactic determinism check + conversion/
simulation via cfl_system.lib.pda_simulator (docs/VERDICT_POLICY.md R2')."""
from __future__ import annotations

import pytest

from dcfl_system.lib.dpda import (
    DPDAFormatError,
    check_determinism,
    dpda_accepts,
    normalize_epsilon_accept_sinks,
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


# ---------------------------------------------------------------------------
# normalize_epsilon_accept_sinks (docs/VERDICT_POLICY.md R2', normalization
# paragraph)
# ---------------------------------------------------------------------------

# The EXACT `dpda` from the live Haiku run docs/VERDICT_POLICY.md R2' cites as
# its precedent (live_c5/dcfl04, {aⁿbⁿcᵐ | n,m>=0}, refuted for three epsilon
# transitions `(state, top=Z0) -> q_accept`, all coexisting with a letter
# transition on the same (state, top)). Copied verbatim (state/transition
# order aside) from `specialist_outputs.stack_strategy.proof_sketch.dpda` in
# that saved result.
LIVE_DCFL04_ANBNCM_DPDA = {
    "states": ["q0", "q_a", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A", "Z0"]},
        {"from": "q0", "read": "b", "top": "Z0", "to": "q_b", "push": ["Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}

# A hand-corrected variant of the same {aⁿbⁿcᵐ | n,m>=0} automaton where every
# epsilon-into-`q_accept` source state ALSO satisfies the extra soundness
# condition (`_pushes_only_onto`): `q_b` is entered only via transitions that
# explicitly (re-)push "Z0" -- from `q_a` on the LAST 'a' (top="A1", the
# dedicated "bottom of the a-block" marker) and from `q_b_mid` once it has
# popped down to that same marker -- never via a bare, unconditioned pop.
# `q_b_mid` (not `q_b`) is used for "more 'A's still to pop", so `q_b` itself
# is never associated with any stack top other than "Z0".
SAFE_ANBNCM_DPDA = {
    "states": ["q0", "q_a", "q_b_mid", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A1", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A1", "Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A1", "to": "q_a", "push": ["A", "A1"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}


def _anbncm_oracle(word: str) -> bool:
    """L = {a^n b^n c^m | n,m>=0} membership, by direct counting."""
    i = 0
    while i < len(word) and word[i] == "a":
        i += 1
    n = i
    j = i
    while j < len(word) and word[j] == "b":
        j += 1
    nb = j - i
    k = j
    while k < len(word) and word[k] == "c":
        k += 1
    return k == len(word) and nb == n


class TestNormalizeEpsilonAcceptSinksSafeCase:
    """SAFE_ANBNCM_DPDA: every epsilon-into-accept-sink source state also
    satisfies `_pushes_only_onto` (single possible stack top) -- the fully
    sound case, where normalization resolves ALL THREE conflicts and the
    result is a genuinely deterministic DPDA for the intended language."""

    def test_all_three_epsilons_normalized(self):
        normalized, notes = normalize_epsilon_accept_sinks(SAFE_ANBNCM_DPDA)
        assert len(notes) == 3
        assert {"q0", "q_b", "q_c"} <= set(normalized["accept_states"])

    def test_normalized_result_is_deterministic(self):
        normalized, _ = normalize_epsilon_accept_sinks(SAFE_ANBNCM_DPDA)
        assert check_determinism(normalized) == []

    def test_original_is_not_deterministic_before_normalization(self):
        """Sanity check: the input to normalize_epsilon_accept_sinks really
        does have the three epsilon/letter conflicts it is meant to resolve."""
        conflicts = check_determinism(SAFE_ANBNCM_DPDA)
        assert len(conflicts) == 3

    @pytest.mark.parametrize("word", [
        "", "a", "b", "c", "cc", "ab", "aab", "aabb", "aabbc", "aabbcc",
        "abc", "abcc", "aaabbb", "aaabbbccc", "aabbb", "abb", "aabbbcc",
    ])
    def test_normalized_language_matches_oracle(self, word):
        normalized, _ = normalize_epsilon_accept_sinks(SAFE_ANBNCM_DPDA)
        assert dpda_accepts(normalized, word) == _anbncm_oracle(word)

    def test_no_op_when_already_deterministic(self):
        normalized, _ = normalize_epsilon_accept_sinks(SAFE_ANBNCM_DPDA)
        # Nothing left to normalize the second time around.
        twice, notes = normalize_epsilon_accept_sinks(normalized)
        assert notes == []
        assert twice is normalized


class TestNormalizeEpsilonAcceptSinksUnsafeCase:
    """LIVE_DCFL04_ANBNCM_DPDA: `q_b`'s epsilon into `q_accept` looks
    identical to `q0`'s and `q_c`'s (same "(state, top=Z0) -> dead-end
    accept" shape) but `q_b` is ALSO reached via a bare pop (`q_a` /
    `q_b` itself popping "A") that can land on `top="A"` -- so it must NOT
    be normalized away, or the rewrite silently changes the language."""

    def test_only_q0_and_q_c_are_normalized(self):
        normalized, notes = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert len(notes) == 2
        assert any("q0" in n for n in notes)
        assert any("q_c" in n for n in notes)
        assert not any("q_b" in n for n in notes)
        assert set(normalized["accept_states"]) == {"q_accept", "q0", "q_c"}

    def test_q_b_conflict_remains_after_normalization(self):
        """q0/q_c's epsilons are gone, but q_b's epsilon still coexists with
        its letter transition on (q_b, top=Z0) -- genuine non-determinism,
        not something this rewrite is allowed to paper over."""
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        conflicts = check_determinism(normalized)
        assert len(conflicts) == 1
        assert "q_b" in conflicts[0]

    def test_blanket_marking_q_b_accepting_would_be_unsound(self):
        """Regression / adversarial-review counterexample: if `q_b` were
        blanket-marked accepting (the naive reading of the VERDICT_POLICY.md
        R2' normalization paragraph, ignoring `_pushes_only_onto`), "aab"
        (2 a's, 1 b -- input ends at (q_b, top="A"), one "A" still
        unmatched) would be wrongly ACCEPTED, even though the un-normalized
        original automaton correctly rejects it and the word is genuinely
        not in {aⁿbⁿcᵐ}. This proves `_pushes_only_onto`'s extra guard is
        necessary, not merely cautious."""
        assert _anbncm_oracle("aab") is False
        assert dpda_accepts(LIVE_DCFL04_ANBNCM_DPDA, "aab") is False

        naively_normalized = {
            **LIVE_DCFL04_ANBNCM_DPDA,
            "accept_states": ["q_accept", "q0", "q_b", "q_c"],
            "transitions": [
                t for t in LIVE_DCFL04_ANBNCM_DPDA["transitions"]
                if not (t.get("read") is None and t.get("to") == "q_accept")
            ],
        }
        assert dpda_accepts(naively_normalized, "aab") is True  # the bug

        # The actual (guarded) normalization does not have this bug.
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert dpda_accepts(normalized, "aab") is False

    @pytest.mark.parametrize("word,expected", [
        ("", True), ("ab", True), ("aabb", True), ("aabbcc", True),
        ("aab", False), ("aabbb", False), ("aabbbcc", False),
    ])
    def test_normalized_still_matches_oracle_on_decided_words(self, word, expected):
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        # q_b's genuine conflict makes this dpda formally non-deterministic,
        # but the BFS simulator still explores it correctly as an NPDA, so
        # membership itself is unaffected by leaving q_b's epsilon in place.
        assert dpda_accepts(normalized, word) == expected


class TestNormalizeEpsilonAcceptSinksNoOp:
    def test_empty_stack_mode_untouched(self):
        dpda = {**LIVE_DCFL04_ANBNCM_DPDA, "accept_mode": "empty_stack"}
        normalized, notes = normalize_epsilon_accept_sinks(dpda)
        assert notes == []
        assert normalized is dpda

    def test_no_accept_states_untouched(self):
        dpda = {**LIVE_DCFL04_ANBNCM_DPDA, "accept_states": []}
        normalized, notes = normalize_epsilon_accept_sinks(dpda)
        assert notes == []
        assert normalized is dpda

    def test_target_with_outgoing_transitions_untouched(self):
        """The epsilon target must be a genuine dead end -- an epsilon into
        a state that itself has further transitions is a different kind of
        non-determinism entirely and is left for check_determinism to
        report as-is."""
        dpda = {
            "states": ["p0", "p1"],
            "start": "p0",
            "accept_states": ["p1"],
            "stack_alphabet": ["Z0"],
            "initial_stack": ["Z0"],
            "transitions": [
                {"from": "p0", "read": None, "top": "Z0", "to": "p1", "push": ["Z0"]},
                {"from": "p1", "read": "a", "top": "Z0", "to": "p1", "push": ["Z0"]},
            ],
        }
        normalized, notes = normalize_epsilon_accept_sinks(dpda)
        assert notes == []
        assert normalized is dpda

    def test_target_not_accepting_untouched(self):
        dpda = {
            "states": ["p0", "p1"],
            "start": "p0",
            "accept_states": [],
            "stack_alphabet": ["Z0"],
            "initial_stack": ["Z0"],
            "transitions": [
                {"from": "p0", "read": None, "top": "Z0", "to": "p1", "push": ["Z0"]},
            ],
        }
        normalized, notes = normalize_epsilon_accept_sinks(dpda)
        assert notes == []
        assert normalized is dpda
