"""Tests for dfa_to_regex (state elimination algorithm)."""

import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from lib.dfa_builder import dfa_to_regex, build_dfa_from_regex
from lib.dfa_runner import run_dfa


def _all_words(alphabet, max_length):
    """Generate all words over alphabet up to max_length."""
    words = []
    for length in range(max_length + 1):
        for combo in product(alphabet, repeat=length):
            words.append("".join(combo))
    return words


class TestDfaToRegex(unittest.TestCase):
    """Round-trip and edge-case tests for dfa_to_regex."""

    def _assert_equivalent(self, dfa1, dfa2, alphabet, max_length):
        """Assert two DFAs accept the same language on all words up to max_length."""
        for w in _all_words(alphabet, max_length):
            r1 = run_dfa(dfa1, w)
            r2 = run_dfa(dfa2, w)
            self.assertEqual(
                r1, r2,
                f"Mismatch on word {w!r}: dfa1={r1}, dfa2={r2}"
            )

    def test_roundtrip_a_star_b(self):
        """Round-trip: regex 'a*b' -> DFA -> regex -> DFA."""
        dfa1 = build_dfa_from_regex("a*b")
        regex2 = dfa_to_regex(dfa1)
        dfa2 = build_dfa_from_regex(regex2)
        self._assert_equivalent(dfa1, dfa2, ["a", "b"], 6)

    def test_roundtrip_ab_star_abb(self):
        """Round-trip: regex '(a|b)*abb' -> DFA -> regex -> DFA."""
        dfa1 = build_dfa_from_regex("(a|b)*abb")
        regex2 = dfa_to_regex(dfa1)
        dfa2 = build_dfa_from_regex(regex2)
        self._assert_equivalent(dfa1, dfa2, ["a", "b"], 8)

    def test_single_state_accept_all(self):
        """Single-state DFA that accepts all words over {a, b}."""
        dfa = {
            "states": ["q0"],
            "alphabet": ["a", "b"],
            "transitions": {"q0": {"a": "q0", "b": "q0"}},
            "start": "q0",
            "accept": ["q0"],
        }
        regex = dfa_to_regex(dfa)
        dfa2 = build_dfa_from_regex(regex)
        self._assert_equivalent(dfa, dfa2, ["a", "b"], 6)

    def test_ends_with_b(self):
        """Two-state DFA: accepts strings ending with 'b'."""
        dfa = {
            "states": ["q0", "q1"],
            "alphabet": ["a", "b"],
            "transitions": {
                "q0": {"a": "q0", "b": "q1"},
                "q1": {"a": "q0", "b": "q1"},
            },
            "start": "q0",
            "accept": ["q1"],
        }
        regex = dfa_to_regex(dfa)
        dfa2 = build_dfa_from_regex(regex)
        self._assert_equivalent(dfa, dfa2, ["a", "b"], 6)

    def test_no_accept_states(self):
        """DFA with no accept states should produce empty-language indicator."""
        dfa = {
            "states": ["q0"],
            "alphabet": ["a", "b"],
            "transitions": {"q0": {"a": "q0", "b": "q0"}},
            "start": "q0",
            "accept": [],
        }
        regex = dfa_to_regex(dfa)
        self.assertEqual(regex, "∅")


if __name__ == "__main__":
    unittest.main()
