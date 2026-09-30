"""Concrete guards for the 2026-09-30 theory corrections, not universal proofs."""

import itertools
import re

from dcfl_system.lib.oracle_verifier import _verify_shallit


def test_empty_alphabet_cannot_refute_dcfl_by_shallit():
    proof = {
        "technique": "nerode_classes",
        "dead_class_status": "empty",
        "distinguishing_suffix": "There are no distinct words.",
        "separation_argument": "The only Nerode class is finite.",
        "argument": "All classes finite, therefore non-DCFL.",
    }
    result = _verify_shallit(proof, {"alphabet": []})
    assert result["verification_status"] == "refuted"
    assert "nerode_nonempty_alphabet" in result["checks_run"]


def test_course_yu_witnesses_have_a_valid_second_alternative():
    for n, exponent in itertools.product(range(2, 8), range(8)):
        first = (n - 1 + exponent, n - 1 + exponent, n + 1)
        second = (n - 1 + exponent, n + 1, n + 1)
        assert first[0] <= first[1] or first[1] == first[2]
        assert second[0] <= second[1] or second[1] == second[2]
        assert min(first + second) >= 1


def test_exam01_marked_slice_matches_four_equal_exponents():
    def belongs(word: str) -> bool:
        if len(word) % 2:
            return False
        middle = len(word) // 2
        left = word[:middle - 1]
        return (
            word[middle - 1:middle + 1] == "aa"
            and word[middle + 1:] == left[::-1]
            and re.fullmatch(r"(?:a+b)*ab(?:ab|aa)*", left) is not None
        )

    for r, s, t, u in itertools.product(range(1, 5), repeat=4):
        prefix = "ab" * r + "aa" + "ba" * s
        suffix = "baa" + "ba" * t + "baa" + "ba" * u
        assert (belongs(prefix) and belongs(prefix + suffix)) == (r == s == t == u)
