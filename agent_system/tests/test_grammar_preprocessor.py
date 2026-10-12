"""Tests for agent_system/lib/grammar_preprocessor.py — the pure-fn,
zero-token-cost grammar analysis that runs before the LLM specialists."""

from __future__ import annotations

import unittest

from agent_system.lib.grammar_preprocessor import analyze_grammar, _detect_ab_pattern


# ---------------------------------------------------------------------------
# Fixture grammars
# ---------------------------------------------------------------------------

def _anbn_spec() -> dict:
    """S -> a S b | a b  (non-regular, {a^n b^n | n >= 1})."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": ["a", "b"]},
        ],
    }


def _right_linear_spec() -> dict:
    """S -> a S | b A | eps ; A -> b A | eps  (regular: a* (b b*)?)."""
    return {
        "terminals": ["a", "b"],
        "nonterminals": ["S", "A"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S"]},
            {"lhs": "S", "rhs": ["b", "A"]},
            {"lhs": "S", "rhs": []},
            {"lhs": "A", "rhs": ["b", "A"]},
            {"lhs": "A", "rhs": []},
        ],
    }


class TestLinearityAndRecursion(unittest.TestCase):

    def test_right_linear_grammar_is_flagged_regular_and_convertible(self):
        facts = analyze_grammar(_right_linear_spec(), max_word_len=6)
        self.assertTrue(facts["is_right_linear"])
        self.assertFalse(facts["is_left_linear"])
        self.assertTrue(facts["is_linear"])
        self.assertTrue(facts["convertible_to_dfa"])
        self.assertIsNotNone(facts["dfa"])
        self.assertEqual(facts["verdict_from_structure"], "regular")
        self.assertIn("REGULAR", facts["summary"])

    def test_nested_recursion_grammar_is_not_linear_and_not_convertible(self):
        facts = analyze_grammar(_anbn_spec(), max_word_len=8)
        self.assertFalse(facts["is_right_linear"])
        self.assertFalse(facts["is_left_linear"])
        self.assertFalse(facts["is_linear"])
        self.assertTrue(facts["has_nested_recursion"])
        self.assertFalse(facts["convertible_to_dfa"])
        self.assertIsNone(facts["dfa"])
        # Nested recursion is a hint, not proof -- summary must hedge, not
        # assert non-regularity outright (a^n b^n happens to be non-regular,
        # but the preprocessor can't know that from structure alone).
        self.assertIn("MAY be non-regular", facts["summary"])


class TestWordGeneration(unittest.TestCase):

    def test_generates_expected_anbn_words_up_to_length(self):
        facts = analyze_grammar(_anbn_spec(), max_word_len=8)
        self.assertEqual(
            facts["generated_words"], ["ab", "aabb", "aaabbb", "aaaabbbb"],
        )
        self.assertEqual(facts["total_generated"], 4)

    def test_derivations_are_grouped_and_sorted_by_length(self):
        facts = analyze_grammar(_anbn_spec(), max_word_len=8)
        derivations = facts["derivations"]
        self.assertTrue(derivations)
        words = [d["word"] for d in derivations]
        self.assertEqual(words, sorted(words, key=len))
        first = derivations[0]
        self.assertEqual(first["word"], "ab")
        self.assertIn("derivation", first)
        self.assertGreaterEqual(first["num_derivations"], 1)


class TestIntersectionAnalysis(unittest.TestCase):
    """a^n b^n intersected with common regular languages -- exercises the
    same "is L ∩ R regular?" estimate the closure agent's claim gets
    checked against (docs/VERDICT_POLICY.md §4)."""

    def test_anbn_intersect_astar_bstar_is_flagged_non_regular_with_pattern(self):
        facts = analyze_grammar(_anbn_spec(), max_word_len=8)
        inter = facts["intersections"]["a*b*"]
        self.assertFalse(inter["is_regular"])
        self.assertEqual(inter["pattern"], "m == k (i.e. {a^n b^n})")
        self.assertIn("a*b*", facts["summary"])
        # The summary must warn AGAINST using a regular intersection for a
        # closure proof, and FOR a non-regular one -- opposite guidance
        # swapped would silently sabotage the closure agent.
        self.assertIn("CAN be used for closure proof", facts["summary"])

    def test_anbn_intersect_ab_star_is_regular(self):
        """L ∩ (ab)* = {ab} — finite, hence regular; must not be flagged as
        a usable non-regular witness for closure."""
        facts = analyze_grammar(_anbn_spec(), max_word_len=8)
        inter = facts["intersections"]["(ab)*"]
        self.assertTrue(inter["is_regular"])
        self.assertIn("DO NOT use L ∩ (ab)*", facts["summary"])

    def test_no_intersection_analysis_outside_binary_alphabet(self):
        spec = {
            "terminals": ["a", "b", "c"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        }
        facts = analyze_grammar(spec, max_word_len=4)
        self.assertNotIn("intersections", facts)


class TestDetectAbPattern(unittest.TestCase):

    def test_equal_counts_pattern(self):
        self.assertEqual(
            _detect_ab_pattern(["ab", "aabb", "aaabbb"]),
            "m == k (i.e. {a^n b^n})",
        )

    def test_mod2_pattern_when_counts_are_not_equal(self):
        # (m, k) = (2,4), (4,2), (1,3) -- m != k, but m % 2 == k % 2 always.
        result = _detect_ab_pattern(["aabbbb", "aaaabb", "abbb"])
        self.assertEqual(result, "m ≡ k (mod 2)")

    def test_mod3_pattern(self):
        # (m, k) = (3,6), (6,3) -- m != k, m % 2 != k % 2, but m % 3 == k % 3.
        result = _detect_ab_pattern(["aaabbbbbb", "aaaaaabbb"])
        self.assertEqual(result, "m ≡ k (mod 3)")

    def test_no_words_is_none(self):
        self.assertIsNone(_detect_ab_pattern([]))

    def test_words_not_in_a_star_b_star_shape_are_skipped(self):
        """'ba' has a 'b' before an 'a' -- not of the form a^m b^k, so it
        contributes no pair; with nothing left, the result is None."""
        self.assertIsNone(_detect_ab_pattern(["ba"]))


if __name__ == "__main__":
    unittest.main()
