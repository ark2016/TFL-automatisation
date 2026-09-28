"""Contract tests for docs/THEORY.md Part II, §0 (REG round-2 revision).

Grammar under discussion: S -> SaSb | eps | A,  A -> bb | aa | bSb.

Round-1 prompts wrongly claimed L intersect a*b* = {a^n b^n}. The correct
fact (verified here by brute-force enumeration, and documented in
docs/THEORY.md §0) is:

    L ∩ a*b* = {a^m b^k | m ≡ k (mod 2), m <= 3k + 2}

and this sublanguage is itself non-regular (Myhill-Nerode: the words
a^(3k+2), k = 0, 1, 2, ..., are pairwise distinguishable by the context b^k).

These tests guard against the false formula creeping back into the prompts,
and independently re-derive the correct intersection by brute force.
"""

import functools
import pathlib
import re
import unittest

PROMPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "prompts"

TARGET_FILES = [
    "grammar_analyzer.md",
    "proof_checker.md",
    "reasoning_agent.md",
    "nerode_agent.md",
]

# Lines that legitimately quote the false formula as an example of a WRONG
# claim being examined/refuted (proof_checker.md's whole job is to flag such
# claims) are exempt, provided they are clearly marked as a "claim" under
# scrutiny rather than asserted as an established fact.
BAD_PATTERN = re.compile(r"a\*b\*[^\n]*=\s*\{a(\^n\b|ⁿ)", re.IGNORECASE)
CLAIM_MARKER = re.compile(r"claim", re.IGNORECASE)


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class TestNoFalseIntersectionFormula(unittest.TestCase):
    """(a) None of the four prompts assert L ∩ a*b* = {a^n b^n} as fact."""

    def test_no_bare_false_formula(self):
        for name in TARGET_FILES:
            content = _read(name)
            for lineno, line in enumerate(content.splitlines(), start=1):
                if not BAD_PATTERN.search(line):
                    continue
                self.assertTrue(
                    CLAIM_MARKER.search(line),
                    msg=(
                        f"{name}:{lineno}: line asserts the false formula "
                        f"'L ∩ a*b* = {{a^n b^n}}' without marking it as an "
                        f"examined/refuted claim: {line!r}"
                    ),
                )

    def test_grammar_analyzer_has_no_false_formula_at_all(self):
        # grammar_analyzer.md's own worked example must state the true fact,
        # not merely quote-and-refute a false one.
        content = _read("grammar_analyzer.md")
        self.assertFalse(BAD_PATTERN.search(content))
        self.assertIn("3k+2", content)

    def test_reasoning_agent_has_no_false_formula_at_all(self):
        content = _read("reasoning_agent.md")
        self.assertFalse(BAD_PATTERN.search(content))
        self.assertIn("3k+2", content)

    def test_nerode_agent_example_is_correctly_labelled(self):
        content = _read("nerode_agent.md")
        self.assertNotIn("balanced parentheses", content)


class TestProofCheckerExampleConsistency(unittest.TestCase):
    """(b) proof_checker.md must not claim the (wrong) actual intersection
    {a^m b^k | m ≡ k (mod 2)} is regular -- it is not."""

    def test_no_regular_claim_next_to_mod_two_formula(self):
        content = _read("proof_checker.md")
        for line in content.splitlines():
            if "m ≡ k" in line:
                self.assertNotIn(
                    "which is regular",
                    line,
                    msg=(
                        "proof_checker.md still claims the m ≡ k (mod 2) "
                        "intersection is regular; it is non-regular "
                        "(see docs/THEORY.md §0)"
                    ),
                )

    def test_still_flags_generic_non_regular_when_actually_regular_case(self):
        # The general checklist item must survive the fix (docs/THEORY.md
        # §4 says to keep it -- it's an unrelated, still-valid error class).
        content = _read("proof_checker.md")
        self.assertIn(
            "Incorrect claim that intersection is non-regular when it's "
            "actually regular",
            content,
        )


# ---------------------------------------------------------------------------
# (c) Brute force: enumerate L(G) up to length 12 and verify
#     L ∩ a*b* = {a^m b^k | m ≡ k (mod 2), m <= 3k + 2}.
# ---------------------------------------------------------------------------

MAX_LEN = 12

# Minimal number of terminal symbols a nonterminal can still contribute.
_MIN_LEN = {"S": 0, "A": 2}

PRODUCTIONS = {
    "S": [("S", "a", "S", "b"), (), ("A",)],
    "A": [("b", "b"), ("a", "a"), ("b", "S", "b")],
}


def _min_total(form):
    total = 0
    for sym in form:
        if sym in _MIN_LEN:
            total += _MIN_LEN[sym]
        else:
            total += 1
    return total


@functools.lru_cache(maxsize=None)
@functools.lru_cache(maxsize=None)
def enumerate_language(max_len: int) -> frozenset[str]:
    """BFS/DFS over sentential forms of G, pruned by achievable length.

    Pure/deterministic in ``max_len`` -- cached so the several ``setUp``
    methods below (each calling this once per test) don't repeat the same
    BFS several times per TestCase."""
    words: set[str] = set()
    seen: set[tuple] = set()
    stack = [("S",)]
    while stack:
        form = stack.pop()
        if form in seen:
            continue
        seen.add(form)
        if _min_total(form) > max_len:
            continue
        # find leftmost nonterminal
        idx = next((i for i, s in enumerate(form) if s in PRODUCTIONS), None)
        if idx is None:
            word = "".join(form)
            if len(word) <= max_len:
                words.add(word)
            continue
        for rhs in PRODUCTIONS[form[idx]]:
            new_form = form[:idx] + rhs + form[idx + 1:]
            if _min_total(new_form) <= max_len and new_form not in seen:
                stack.append(new_form)
    return frozenset(words)


class TestBruteForceIntersection(unittest.TestCase):
    def setUp(self):
        self.language = enumerate_language(MAX_LEN)
        # sanity: grammar actually produces something beyond epsilon
        self.assertIn("", self.language)
        self.assertIn("bb", self.language)
        self.assertIn("aa", self.language)

    def _expected_ab_words(self, max_len):
        expected = set()
        for m in range(0, max_len + 1):
            for k in range(0, max_len + 1 - m):
                if m + k > max_len:
                    continue
                if (m - k) % 2 == 0 and m <= 3 * k + 2:
                    expected.add("a" * m + "b" * k)
        return expected

    def test_intersection_matches_formula(self):
        ab_star = re.compile(r"^a*b*$")
        actual = {w for w in self.language if ab_star.match(w)}
        expected = self._expected_ab_words(MAX_LEN)
        self.assertEqual(actual, expected)

    def test_false_formula_would_be_wrong(self):
        # Guard against regressing to the old, false claim: {a^n b^n} is a
        # strict (wrong) subset of the true intersection -- bb and aa are
        # counterexamples that are in L but not of the form a^n b^n.
        self.assertIn("bb", self.language)
        self.assertIn("aa", self.language)
        naive_claim = {"a" * n + "b" * n for n in range(0, MAX_LEN // 2 + 1)}
        ab_star = re.compile(r"^a*b*$")
        actual = {w for w in self.language if ab_star.match(w)}
        self.assertNotEqual(actual, naive_claim)


# ---------------------------------------------------------------------------
# (d) task2_grammar_sasb_pumping_output.json (docs/THEORY.md §0 round 2):
#     the old mock's step "any word of the form a^m bb b^n requires m = n"
#     was false (bbbb = a^0 bb b^2, m=0 != n=2, is in L(G)). The fixed mock
#     uses w = a^(3p+2) b^p and pumps UP (i=2); verify both claims against
#     the same BFS grammar enumeration used above.
# ---------------------------------------------------------------------------

import json
import pathlib

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "examples"


class TestTask2PumpingMockWordsAreCorrect(unittest.TestCase):
    # Longest word any assertion below checks is a^(3p+2+k) b^p for p=3,
    # k<=p=3 -> length 14 (test_pumped_up_word_leaves_language_for_small_p);
    # 15 keeps a 1-symbol margin. enumerate_language's sentential-form BFS
    # grows steeply with max_len (~19s at 20 vs ~0.2s at 15, since the
    # grammar's two recursive productions roughly quadruple the frontier
    # every +2), so the old max_len=20 paid for ~450k words when <=15
    # already covers every word these tests need.
    def setUp(self):
        self.language = enumerate_language(15)
        with open(
            EXAMPLES_DIR / "task2_grammar_sasb_pumping_output.json", encoding="utf-8"
        ) as f:
            self.mock = json.load(f)

    def test_old_false_claim_is_gone(self):
        blob = json.dumps(self.mock, ensure_ascii=False)
        self.assertNotIn("requires m = n", blob)

    def test_old_false_claim_counterexample_is_in_language(self):
        # bbbb = a^0 bb b^2 (m=0, n=2): the old mock's claim "m = n required"
        # is refuted by this single word already being in L(G).
        self.assertIn("bbbb", self.language)

    def test_base_word_is_in_language_for_small_p(self):
        for p in range(1, 4):
            w = "a" * (3 * p + 2) + "b" * p
            self.assertIn(w, self.language, f"a^(3p+2) b^p not in L(G) for p={p}")

    def test_pumped_up_word_leaves_language_for_small_p(self):
        for p in range(1, 4):
            for k in range(1, p + 1):
                w = "a" * (3 * p + 2 + k) + "b" * p
                self.assertNotIn(
                    w, self.language,
                    f"pumped-up word a^(3p+2+k) b^p (p={p}, k={k}) unexpectedly in L(G)",
                )


if __name__ == "__main__":
    unittest.main()
