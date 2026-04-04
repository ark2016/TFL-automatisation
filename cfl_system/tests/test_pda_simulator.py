"""Tests for the PDA simulator module."""

import pytest

from cfl_system.lib.pda_simulator import (
    pda_accepts,
    pda_accepts_detailed,
    validate_pda,
)

# ---------------------------------------------------------------------------
# PDA definitions used across tests
# ---------------------------------------------------------------------------

# Accepts a^n b^n (n >= 0) via final state
ANBN_PDA = {
    "states": ["q0", "q1", "q_accept"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z", "A"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "final_state",
    "accept_states": ["q_accept"],
    "transitions": [
        # Push A for each 'a'
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["Z", "A"]},
        {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
        # Pop A for each 'b'
        {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        # Accept on seeing bottom-of-stack marker
        {"from": "q1", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
        # Accept empty word directly
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
    ],
}

# Accepts even-length palindromes {ww^R} over {a, b} via final state.
# Nondeterministic: guesses the midpoint.
PALINDROME_PDA = {
    "states": ["q0", "q1", "q_accept"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z", "A", "B"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "final_state",
    "accept_states": ["q_accept"],
    "transitions": [
        # Phase 1: push input symbols
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["Z", "A"]},
        {"from": "q0", "input": "b", "stack_top": "Z", "to": "q0", "push": ["Z", "B"]},
        {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "input": "a", "stack_top": "B", "to": "q0", "push": ["B", "A"]},
        {"from": "q0", "input": "b", "stack_top": "A", "to": "q0", "push": ["A", "B"]},
        {"from": "q0", "input": "b", "stack_top": "B", "to": "q0", "push": ["B", "B"]},
        # Nondeterministic switch to phase 2 (epsilon)
        {"from": "q0", "input": None, "stack_top": "A", "to": "q1", "push": ["A"]},
        {"from": "q0", "input": None, "stack_top": "B", "to": "q1", "push": ["B"]},
        # Phase 2: match input against stack
        {"from": "q1", "input": "a", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": "b", "stack_top": "B", "to": "q1", "push": []},
        # Accept when stack shows bottom marker
        {"from": "q1", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
        # Accept empty word
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
    ],
}

# Accepts a^n b^n via empty stack mode
ANBN_EMPTY_STACK_PDA = {
    "states": ["q0", "q1"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z", "A"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "empty_stack",
    "transitions": [
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["Z", "A"]},
        {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        # Pop bottom marker after matching
        {"from": "q1", "input": None, "stack_top": "Z", "to": "q1", "push": []},
        # Accept empty word: pop Z immediately
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q0", "push": []},
    ],
}

# Balanced parentheses via final state
PARENS_PDA = {
    "states": ["q0", "q_accept"],
    "input_alphabet": ["(", ")"],
    "stack_alphabet": ["Z", "L"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_mode": "final_state",
    "accept_states": ["q_accept"],
    "transitions": [
        # Push for '('
        {"from": "q0", "input": "(", "stack_top": "Z", "to": "q0", "push": ["Z", "L"]},
        {"from": "q0", "input": "(", "stack_top": "L", "to": "q0", "push": ["L", "L"]},
        # Pop for ')'
        {"from": "q0", "input": ")", "stack_top": "L", "to": "q0", "push": []},
        # Accept when input consumed and stack shows bottom
        {"from": "q0", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
    ],
}


# ---------------------------------------------------------------------------
# Tests: a^n b^n (final state mode)
# ---------------------------------------------------------------------------

class TestAnBn:
    def test_aabb_accepted(self):
        assert pda_accepts(ANBN_PDA, "aabb") is True

    def test_ab_accepted(self):
        assert pda_accepts(ANBN_PDA, "ab") is True

    def test_empty_accepted(self):
        assert pda_accepts(ANBN_PDA, "") is True

    def test_aab_rejected(self):
        assert pda_accepts(ANBN_PDA, "aab") is False

    def test_ba_rejected(self):
        assert pda_accepts(ANBN_PDA, "ba") is False

    def test_aaabbb_accepted(self):
        assert pda_accepts(ANBN_PDA, "aaabbb") is True


# ---------------------------------------------------------------------------
# Tests: palindromes (nondeterministic)
# ---------------------------------------------------------------------------

class TestPalindrome:
    def test_abba_accepted(self):
        assert pda_accepts(PALINDROME_PDA, "abba") is True

    def test_aa_accepted(self):
        assert pda_accepts(PALINDROME_PDA, "aa") is True

    def test_abab_rejected(self):
        assert pda_accepts(PALINDROME_PDA, "abab") is False

    def test_empty_accepted(self):
        assert pda_accepts(PALINDROME_PDA, "") is True

    def test_aabbaa_accepted(self):
        assert pda_accepts(PALINDROME_PDA, "aabbaa") is True


# ---------------------------------------------------------------------------
# Tests: empty stack acceptance mode
# ---------------------------------------------------------------------------

class TestEmptyStack:
    def test_aabb_accepted(self):
        assert pda_accepts(ANBN_EMPTY_STACK_PDA, "aabb") is True

    def test_aab_rejected(self):
        assert pda_accepts(ANBN_EMPTY_STACK_PDA, "aab") is False

    def test_empty_accepted(self):
        assert pda_accepts(ANBN_EMPTY_STACK_PDA, "") is True


# ---------------------------------------------------------------------------
# Tests: balanced parentheses
# ---------------------------------------------------------------------------

class TestParentheses:
    def test_matched(self):
        assert pda_accepts(PARENS_PDA, "(())") is True

    def test_simple(self):
        assert pda_accepts(PARENS_PDA, "()") is True

    def test_unmatched(self):
        assert pda_accepts(PARENS_PDA, "(()") is False

    def test_empty(self):
        assert pda_accepts(PARENS_PDA, "") is True

    def test_nested(self):
        assert pda_accepts(PARENS_PDA, "((()))") is True

    def test_extra_close(self):
        assert pda_accepts(PARENS_PDA, "())") is False


# ---------------------------------------------------------------------------
# Tests: epsilon transitions (covered implicitly, but explicit here)
# ---------------------------------------------------------------------------

class TestEpsilonTransitions:
    def test_epsilon_only_accept(self):
        """PDA that accepts empty string via epsilon transition."""
        pda = {
            "states": ["q0", "q1"],
            "input_alphabet": [],
            "stack_alphabet": ["Z"],
            "start_state": "q0",
            "start_stack": "Z",
            "accept_mode": "final_state",
            "accept_states": ["q1"],
            "transitions": [
                {"from": "q0", "input": None, "stack_top": "Z", "to": "q1", "push": []},
            ],
        }
        assert pda_accepts(pda, "") is True

    def test_epsilon_does_not_consume_input(self):
        """Epsilon transition should not consume any input character."""
        pda = {
            "states": ["q0", "q1"],
            "input_alphabet": ["a"],
            "stack_alphabet": ["Z"],
            "start_state": "q0",
            "start_stack": "Z",
            "accept_mode": "final_state",
            "accept_states": ["q1"],
            "transitions": [
                # Epsilon moves to q1 but input 'a' is never consumed
                {"from": "q0", "input": None, "stack_top": "Z", "to": "q1", "push": []},
            ],
        }
        # Word "a" should NOT be accepted because there's no transition consuming 'a'
        assert pda_accepts(pda, "a") is False


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_pda(self):
        assert validate_pda(ANBN_PDA) == []

    def test_missing_keys(self):
        errors = validate_pda({"states": []})
        assert len(errors) > 0
        assert "Missing required keys" in errors[0]

    def test_bad_start_state(self):
        pda = {**ANBN_PDA, "start_state": "qX"}
        errors = validate_pda(pda)
        assert any("start_state" in e for e in errors)

    def test_bad_start_stack(self):
        pda = {**ANBN_PDA, "start_stack": "X"}
        errors = validate_pda(pda)
        assert any("start_stack" in e for e in errors)

    def test_bad_accept_mode(self):
        pda = {**ANBN_PDA, "accept_mode": "magic"}
        errors = validate_pda(pda)
        assert any("accept_mode" in e for e in errors)

    def test_bad_accept_states(self):
        pda = {**ANBN_PDA, "accept_states": ["qX"]}
        errors = validate_pda(pda)
        assert any("accept_states" in e for e in errors)

    def test_bad_transition_state(self):
        pda = {
            **ANBN_PDA,
            "transitions": [
                {"from": "qX", "input": "a", "stack_top": "Z", "to": "q0", "push": []},
            ],
        }
        errors = validate_pda(pda)
        assert any("'from' state" in e for e in errors)

    def test_invalid_pda_raises_valueerror(self):
        pda = {**ANBN_PDA, "start_state": "qX"}
        with pytest.raises(ValueError, match="Invalid PDA"):
            pda_accepts(pda, "ab")


# ---------------------------------------------------------------------------
# Tests: detailed execution info
# ---------------------------------------------------------------------------

class TestDetailed:
    def test_accepted_has_config(self):
        result = pda_accepts_detailed(ANBN_PDA, "aabb")
        assert result["accepted"] is True
        assert result["steps_used"] > 0
        assert result["max_stack_depth"] >= 1
        assert result["accepting_config"] is not None
        assert result["accepting_config"]["remaining_input"] == ""
        assert result["configs_explored"] > 0

    def test_rejected_has_no_config(self):
        result = pda_accepts_detailed(ANBN_PDA, "aab")
        assert result["accepted"] is False
        assert result["accepting_config"] is None
        assert result["steps_used"] > 0

    def test_stack_depth_grows(self):
        result = pda_accepts_detailed(ANBN_PDA, "aaaaaabbbbbb")
        assert result["accepted"] is True
        # Stack should have grown to at least 6 (for 6 a's)
        assert result["max_stack_depth"] >= 6


# ---------------------------------------------------------------------------
# Tests: timeout on pathological PDA
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_raised(self):
        """PDA with explosive nondeterminism that exceeds step limit.

        Each epsilon transition pops the top and pushes two *different*
        symbols (A or B), creating a binary tree of distinct stack
        configurations.  With branching factor 2 at every step, the
        BFS frontier doubles each round and quickly exceeds 10 000 steps
        well before stacks reach depth 1 000.
        """
        pda = {
            "states": ["q0", "q_accept"],
            "input_alphabet": ["a"],
            "stack_alphabet": ["Z", "A", "B"],
            "start_state": "q0",
            "start_stack": "Z",
            "accept_mode": "final_state",
            "accept_states": ["q_accept"],
            "transitions": [
                # For every stack-top symbol, two epsilon choices that each
                # grow the stack by one, producing distinct configurations.
                {"from": "q0", "input": None, "stack_top": "Z", "to": "q0", "push": ["A", "Z"]},
                {"from": "q0", "input": None, "stack_top": "Z", "to": "q0", "push": ["B", "Z"]},
                {"from": "q0", "input": None, "stack_top": "A", "to": "q0", "push": ["A", "A"]},
                {"from": "q0", "input": None, "stack_top": "A", "to": "q0", "push": ["B", "A"]},
                {"from": "q0", "input": None, "stack_top": "B", "to": "q0", "push": ["A", "B"]},
                {"from": "q0", "input": None, "stack_top": "B", "to": "q0", "push": ["B", "B"]},
            ],
        }
        with pytest.raises(TimeoutError):
            pda_accepts(pda, "a")
