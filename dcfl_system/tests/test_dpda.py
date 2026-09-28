"""Tests for dcfl_system.lib.dpda: syntactic determinism check + conversion/
simulation via cfl_system.lib.pda_simulator (docs/VERDICT_POLICY.md R2')."""
from __future__ import annotations

import pytest

from dcfl_system.lib.dpda import (
    DPDAFormatError,
    check_determinism,
    dpda_accepts,
    dpda_run,
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

    def test_exact_duplicate_transition_is_not_a_conflict(self):
        """Two transitions for the same (state, top, read) that also agree
        on `to` and `push` are the SAME transition listed twice -- e.g. from
        a merge or a copy-paste in the proof_sketch -- not a genuine choice
        between two different continuations, so this must NOT be reported
        as non-determinism."""
        dpda = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                # exact duplicate of the existing q_push/a/Z0 transition
                {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
            ],
        }
        assert check_determinism(dpda) == []

    def test_duplicate_epsilon_spelling_of_identical_transition_is_not_a_conflict(self):
        """The same transition duplicated with a different (but equivalent)
        epsilon spelling is still the same transition, not a conflict. Uses
        a fresh (state, top) pair with no other transitions, so this
        exercises only the duplicate-epsilon case, not the (unrelated)
        epsilon-coexists-with-letter rule."""
        dpda = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_new", "read": None, "top": "Z0", "to": "q_pop", "push": ["Z0"]},
                {"from": "q_new", "read": "eps", "top": "Z0", "to": "q_pop", "push": ["Z0"]},
            ],
        }
        assert check_determinism(dpda) == []

    def test_exact_duplicate_transition_does_not_break_dpda_run(self):
        """Reviewer finding: check_determinism collapses an exact duplicate
        transition to one and reports no conflict, but dpda_run used to build
        its own, non-deduplicating `by_pair` index, so `len(letter_ts) == 1`
        (and eps_step's `len(eps_ts) != 1`) saw 2 entries for the duplicated
        (state, top, read) and behaved as if NO transition were defined there
        -- silently rejecting words the original (non-duplicated) DPDA
        accepts. dpda_run on a DPDA with an exact duplicate must match
        dpda_run on the same DPDA without it, for every word, and must still
        agree with check_determinism (== []) that the automaton is
        deterministic."""
        duplicated = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                # exact duplicate of the existing q_push/a/Z0 transition
                {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
            ],
        }
        assert check_determinism(duplicated) == []
        for word in ("abc", "aabbcc", "aaabbbccc", "", "bbcc", "aabb", "aabbb"):
            assert dpda_run(duplicated, word) == dpda_run(ANBNCM_DPDA, word)

    def test_duplicate_plus_genuinely_different_transition_still_conflicts(self):
        """Deduplication must not hide a genuine conflict: alongside an
        exact duplicate, a third transition for the same (state, top, read)
        that goes somewhere else is still non-determinism."""
        dpda = {
            **ANBNCM_DPDA,
            "transitions": [
                *ANBNCM_DPDA["transitions"],
                {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
                {"from": "q_push", "read": "a", "top": "Z0", "to": "q_c", "push": []},
            ],
        }
        conflicts = check_determinism(dpda)
        assert conflicts
        assert any("q_push" in c and "read='a'" in c and "2 transitions" in c for c in conflicts)


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

# A hand-corrected, genuinely correct variant of the same {aⁿbⁿcᵐ | n,m>=0}
# automaton where every epsilon-into-`q_accept` source state ALSO happens to
# be entered only with a single stack top: `q_b` is entered only via
# transitions that explicitly (re-)push "Z0" -- from `q_a` on the LAST 'a'
# (top="A1", the dedicated "bottom of the a-block" marker) and from
# `q_b_mid` once it has popped down to that same marker -- never via a bare,
# unconditioned pop. `q_b_mid` (not `q_b`) is used for "more 'A's still to
# pop", so `q_b` itself is never associated with any stack top other than
# "Z0" (this single-stack-top property is no longer REQUIRED for
# normalization to be sound -- see TestNormalizeEpsilonAcceptSinksLiveDcfl04
# below -- but this automaton also happens to have it, and is correct).
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
    """SAFE_ANBNCM_DPDA: a hand-designed automaton where every epsilon-into-
    accept-sink source state happens to be reached with a single stack top
    (by construction, via a dedicated "bottom of the a-block" symbol) -- the
    normalization resolves ALL THREE conflicts and the result is a genuinely
    deterministic, and genuinely correct, DPDA for the intended language.
    (This automaton no longer needs that extra care to normalize soundly --
    see TestNormalizeEpsilonAcceptSinksLiveDcfl04 below, where the SAME
    normalization also applies to a state reached with more than one stack
    top, and stays sound precisely because acceptance is now scoped to the
    exact (state, top) pair via `accept_configs`, not a blanket state mark --
    but it remains a good example of a stack_strategy `dpda` that is both
    normalizable AND correct.)"""

    def test_all_three_epsilons_normalized(self):
        normalized, notes = normalize_epsilon_accept_sinks(SAFE_ANBNCM_DPDA)
        assert len(notes) == 3
        assert {("q0", "Z0"), ("q_b", "Z0"), ("q_c", "Z0")} <= {
            (c[0], c[1]) for c in normalized["accept_configs"]
        }
        # accept_states is left untouched -- normalization no longer mutates it.
        assert normalized["accept_states"] == SAFE_ANBNCM_DPDA["accept_states"]

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


# The live automaton minus its one stray transition (`q0 --b--> q_b` with no
# preceding `a`) -- the fix docs/VERDICT_POLICY.md R2' points at: `n=0` is
# then reachable only directly at `(q0, "Z0")` itself (via `accept_configs`),
# never through `q_b`, so `dpda_run` agrees with the oracle on every word,
# including "b" (see TestNormalizeEpsilonAcceptSinksLiveDcfl04 below for the
# UNFIXED automaton's "b" bug this removes).
FIXED_DCFL04_ANBNCM_DPDA = {
    **LIVE_DCFL04_ANBNCM_DPDA,
    "transitions": [
        t for t in LIVE_DCFL04_ANBNCM_DPDA["transitions"]
        if not (t.get("from") == "q0" and t.get("read") == "b")
    ],
}


class TestNormalizeEpsilonAcceptSinksLiveDcfl04:
    """LIVE_DCFL04_ANBNCM_DPDA (docs/VERDICT_POLICY.md R2' precedent, the
    exact live dcfl-04 `dpda`): `q0`, `q_b` and `q_c` each have an epsilon
    `(state, top=Z0) -> q_accept` into the same dead-end accepting sink.
    Under the CURRENT (config-scoped) normalization all three qualify and are
    rewritten -- including `q_b`, even though it is ALSO reached via a bare
    pop that can leave `top="A"` -- because acceptance is now keyed on the
    exact pair `(q_b, "Z0")`, not on `q_b` as a whole (see
    `normalize_epsilon_accept_sinks`'s docstring for the soundness argument;
    an earlier revision of this function needed an extra guard,
    `_pushes_only_onto`, and left `q_b` un-normalized for exactly this
    reason -- that guard no longer exists because it is no longer needed).
    The result IS syntactically deterministic, but this SUBMITTED automaton
    is still wrong for the language: it has a stray `q0 --b--> q_b` with no
    preceding `a`, so `"b"` reaches an `accept_configs` hit and is wrongly
    accepted. This is exactly the kind of defect only simulation against the
    language oracle catches, not the (clean) determinism check."""

    def test_all_three_epsilons_normalize(self):
        normalized, notes = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert len(notes) == 3
        assert any("q0" in n for n in notes)
        assert any("q_b" in n for n in notes)
        assert any("q_c" in n for n in notes)
        assert {("q0", "Z0"), ("q_b", "Z0"), ("q_c", "Z0")} == {
            (c[0], c[1]) for c in normalized["accept_configs"]
        }
        # accept_states is left untouched -- normalization no longer mutates it.
        assert normalized["accept_states"] == ["q_accept"]

    def test_normalized_result_is_deterministic(self):
        """Unlike an earlier revision (which left q_b's epsilon in place and
        reported one conflict there), the current normalization removes it
        too -- check_determinism reports a clean automaton."""
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert check_determinism(normalized) == []

    def test_deterministic_but_wrongly_accepts_b(self):
        """`"b"` (0 a's, 1 b) is genuinely not in {aⁿbⁿcᵐ} -- this submitted
        automaton is wrong for the language independently of normalization
        (it already has the stray `q0 --b--> q_b` transition with no `a`
        ever pushed, reaching `q_accept` via `q_b`'s own epsilon once input
        runs out at `(q_b, top="Z0")`). What normalization changes is only
        WHICH check catches it: under an EARLIER revision (guarded by
        `_pushes_only_onto`), `q_b`'s epsilon stayed un-normalized and kept
        coexisting with a letter transition on `(q_b, "Z0")`, so
        `check_determinism` refuted it directly, before simulation ever ran.
        Under the CURRENT (config-scoped) normalization, `q_b`'s epsilon
        normalizes away too, the result is genuinely, fully deterministic --
        so this defect can now ONLY be caught by simulating against the
        language oracle (docs/VERDICT_POLICY.md R2', live dcfl-04
        precedent), never by the determinism check."""
        assert _anbncm_oracle("b") is False
        assert dpda_accepts(LIVE_DCFL04_ANBNCM_DPDA, "b") is True  # wrong already, pre-normalization

        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert check_determinism(normalized) == []  # genuinely deterministic now
        assert dpda_accepts(normalized, "b") is True  # ...and still genuinely wrong

    def test_config_scoped_acceptance_still_rejects_aab(self):
        """Regression / adversarial-review counterexample an EARLIER revision
        needed a guard (`_pushes_only_onto`) to avoid: if `q_b` were instead
        blanket-marked accepting (ignoring which stack top it occurs with),
        "aab" (2 a's, 1 b -- input ends at (q_b, top="A"), one "A" still
        unmatched) would be wrongly ACCEPTED. The CURRENT mechanism needs no
        such guard because it never blanket-marks `q_b`: it only adds the
        exact pair `(q_b, "Z0")` to `accept_configs`, so `(q_b, "A")` --
        "aab"'s actual halting configuration -- is correctly not accepting."""
        assert _anbncm_oracle("aab") is False

        naively_normalized = {
            **LIVE_DCFL04_ANBNCM_DPDA,
            "accept_states": ["q_accept", "q0", "q_b", "q_c"],
            "transitions": [
                t for t in LIVE_DCFL04_ANBNCM_DPDA["transitions"]
                if not (t.get("read") is None and t.get("to") == "q_accept")
            ],
        }
        assert dpda_accepts(naively_normalized, "aab") is True  # the bug a blanket mark would have

        # The actual (config-scoped) normalization does not have this bug.
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert dpda_accepts(normalized, "aab") is False

    @pytest.mark.parametrize("word,expected", [
        ("", True), ("ab", True), ("aabb", True), ("aabbcc", True),
        ("aab", False), ("aabbb", False), ("aabbbcc", False),
    ])
    def test_normalized_still_matches_oracle_on_decided_words(self, word, expected):
        normalized, _ = normalize_epsilon_accept_sinks(LIVE_DCFL04_ANBNCM_DPDA)
        assert dpda_accepts(normalized, word) == expected


class TestFixedDcfl04VariantReachesBoundedPass:
    """FIXED_DCFL04_ANBNCM_DPDA: the live automaton minus the one stray
    transition responsible for the "b" bug above. Normalization still
    removes all three epsilons (n=0 is now reachable only directly at
    `(q0, "Z0")`), the result is deterministic, AND it agrees with the
    oracle on every word -- including the ones that exposed the bug."""

    def test_all_three_epsilons_normalize_and_result_is_deterministic(self):
        normalized, notes = normalize_epsilon_accept_sinks(FIXED_DCFL04_ANBNCM_DPDA)
        assert len(notes) == 3
        assert check_determinism(normalized) == []

    @pytest.mark.parametrize("word,expected", [
        ("", True), ("b", False), ("bb", False), ("bc", False),
        ("c", True), ("cc", True), ("ab", True), ("aabb", True),
        ("aabbcc", True), ("aab", False), ("aabbb", False),
    ])
    def test_normalized_matches_oracle(self, word, expected):
        normalized, _ = normalize_epsilon_accept_sinks(FIXED_DCFL04_ANBNCM_DPDA)
        assert dpda_accepts(normalized, word) == expected
        assert _anbncm_oracle(word) == expected  # sanity: the oracle itself agrees


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
