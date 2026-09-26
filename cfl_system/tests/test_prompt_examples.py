"""Regression tests for the L = {w1w2w1w3} worked example in the CFL prompts.

The reference example was wrong (see docs/THEORY.md §2): the old claim
L ∩ a+b+a+c+ = {a^n b^m a^n c^k} is false (extra a's leak into w3 ∈ {a,c}+, so
the intersection is actually {a^n b^m a^j c^k | j >= n}, which is context-free
and proves nothing). The corrected example uses R = a+b+ac·a+b+ac.

These tests brute-force verify the claims made in the corrected prompts
(cfl_closure_reduction.md, cfl_pumping.md, cfl_reasoning.md,
cfl_retry_planner.md, tz_cfl_agent_system.md) against the language predicate,
and grep the prompt files to make sure the old false example is gone.
"""

import itertools
import re
from pathlib import Path

import pytest

ALLOWED_W1 = set("ab")
ALLOWED_W2 = set("bc")
ALLOWED_W3 = set("ac")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The five places docs/THEORY.md §2 says must be fixed.
FILES_TO_CHECK = [
    PROMPTS_DIR / "cfl_closure_reduction.md",
    PROMPTS_DIR / "cfl_pumping.md",
    PROMPTS_DIR / "cfl_reasoning.md",
    PROMPTS_DIR / "cfl_retry_planner.md",
    REPO_ROOT / "cfl_system" / "tz_cfl_agent_system.md",
]

FALSE_CLAIM_PATTERN = re.compile(r"a\^n b\^m a\^n c\^k")


def find_decomposition(word: str):
    """Brute-force search for a valid w1 w2 w1 w3 decomposition of `word`.

    Returns (w1, w2, w3) if one exists, else None. w1 in {a,b}+, w2 in
    {b,c}+, w3 in {a,c}+, and the word must equal w1 + w2 + w1 + w3.
    """
    n = len(word)
    for len1 in range(1, n + 1):
        w1 = word[:len1]
        if any(c not in ALLOWED_W1 for c in w1):
            continue
        for len2 in range(1, n + 1):
            start2 = len1 + len2
            end2 = start2 + len1
            if end2 >= n:
                continue  # need at least 1 char left for w3
            w2 = word[len1:start2]
            if any(c not in ALLOWED_W2 for c in w2):
                continue
            if word[start2:end2] != w1:
                continue
            w3 = word[end2:]
            if len(w3) < 1 or any(c not in ALLOWED_W3 for c in w3):
                continue
            return (w1, w2, w3)
    return None


def in_L(word: str) -> bool:
    return find_decomposition(word) is not None


R_OLD = re.compile(r"^a+b+a+c+$")
R_NEW = re.compile(r"^a+b+aca+b+ac$")


# ---------------------------------------------------------------------------
# (1) L ∩ a+b+a+c+ = {a^n b^m a^j c^k | j >= n} — the OLD reduction is CFL,
#     not the false a^n b^m a^n c^k.
# ---------------------------------------------------------------------------

def test_old_reduction_is_not_equal_n_a_blocks():
    for n, m, j, k in itertools.product(range(1, 5), repeat=4):
        word = "a" * n + "b" * m + "a" * j + "c" * k
        assert R_OLD.match(word)
        assert in_L(word) == (j >= n), (n, m, j, k)

    # Explicit witness with j != n showing the old "a^n b^m a^n c^k" claim is false.
    witness = "a" + "b" + "aa" + "c"  # n=1, m=1, j=2, k=1
    assert in_L(witness)
    assert find_decomposition(witness) is not None


# ---------------------------------------------------------------------------
# (2) The old pumping word a^p b a^p c is unreliable: pumping the second
#     a-block UP re-decomposes and stays in L (extra a moves into w3).
# ---------------------------------------------------------------------------

def test_old_pumping_word_survives_upward_pump():
    p = 3
    z = "a" * p + "b" + "a" * p + "c"
    assert in_L(z)

    pumped_once = "a" * p + "b" + "a" * (p + 1) + "c"
    pumped_twice = "a" * p + "b" + "a" * (p + 2) + "c"
    assert in_L(pumped_once)
    assert in_L(pumped_twice)


# ---------------------------------------------------------------------------
# (3) L ∩ R = {a^n b^m ac a^n b^m ac | n,m >= 1} for the corrected
#     R = a+b+ac·a+b+ac.
# ---------------------------------------------------------------------------

def test_new_intersection_is_exact_copy_language():
    for n1, m1, n2, m2 in itertools.product(range(1, 5), repeat=4):
        word = "a" * n1 + "b" * m1 + "ac" + "a" * n2 + "b" * m2 + "ac"
        assert R_NEW.match(word)
        expected = n1 == n2 and m1 == m2
        assert in_L(word) == expected, (n1, m1, n2, m2)


# ---------------------------------------------------------------------------
# (4) For z = a^p b^p ac a^p b^p ac (p=3), every CFL-pumping decomposition
#     z = uvwxy with |vwx| <= p, |vx| >= 1, pumped DOWN (i=0), leaves L ∩ R.
# ---------------------------------------------------------------------------

def test_new_word_pumps_down_out_of_intersection():
    p = 3
    z = "a" * p + "b" * p + "ac" + "a" * p + "b" * p + "ac"
    assert R_NEW.match(z)
    assert in_L(z)
    n = len(z)

    checked = 0
    violations = []
    for start in range(0, n + 1):
        for winlen in range(1, p + 1):
            if start + winlen > n:
                continue
            u = z[:start]
            window = z[start:start + winlen]
            y = z[start + winlen:]
            for cut1 in range(0, winlen + 1):
                for cut2 in range(cut1, winlen + 1):
                    v = window[:cut1]
                    x = window[cut2:winlen]
                    if len(v) == 0 and len(x) == 0:
                        continue  # |vx| >= 1
                    w = window[cut1:cut2]
                    checked += 1
                    uwy = u + w + y
                    if in_L(uwy) and R_NEW.match(uwy):
                        violations.append((start, winlen, cut1, cut2, uwy))

    assert checked > 0
    assert violations == []


# ---------------------------------------------------------------------------
# (5) The false example ("L ∩ a+b+a+c+ = {a^n b^m a^n c^k}") must not remain
#     anywhere in the fixed prompt/spec files, and the corrected regex must
#     be present in the closure_reduction prompt.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", FILES_TO_CHECK, ids=lambda p: p.name)
def test_false_example_removed(path: Path):
    text = path.read_text(encoding="utf-8")
    assert not FALSE_CLAIM_PATTERN.search(text), (
        f"{path} still contains the false claim 'a^n b^m a^n c^k'"
    )


def test_closure_reduction_prompt_has_corrected_regex():
    text = (PROMPTS_DIR / "cfl_closure_reduction.md").read_text(encoding="utf-8")
    assert "a+b+aca+b+ac" in text
    assert "a+b+a+c+" not in text or "не годится" in text or "does NOT force" in text
