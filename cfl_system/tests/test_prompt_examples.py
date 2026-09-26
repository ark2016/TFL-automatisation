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


# ===========================================================================
# task_grammar_filter_49 worked example (docs/THEORY.md §2.3)
#
# G: S -> aSbb | eps | bbSa | aA ; A -> aA | a, filter |w|_a = |w|_b.
# The reference verdict was "cfl" before this revision — WRONG (see
# cfl_system/tests/test_e2e.py). |a|=|b| is a non-regular two-counter
# equality, so CFL closure under intersection with a regular language does
# not apply directly; L(G) ∩ F is proved non-CFL via closure reduction with
# R = b*a*b*a* and Ogden's lemma.
#
# These tests brute-force verify the §2.3 characterization directly against
# the grammar (BFS derivation), independent of the prompts/mocks.
# ===========================================================================

from functools import lru_cache


@lru_cache(maxsize=None)
def _gen_A(maxlen: int) -> frozenset[str]:
    """A -> aA | a : all strings A derives with length <= maxlen."""
    results = set()
    if maxlen >= 1:
        results.add("a")
    if maxlen >= 2:
        for s in _gen_A(maxlen - 1):
            w = "a" + s
            if len(w) <= maxlen:
                results.add(w)
    return frozenset(results)


@lru_cache(maxsize=None)
def _gen_S(maxlen: int) -> frozenset[str]:
    """S -> aSbb | eps | bbSa | aA : all strings S derives with length <= maxlen."""
    results = {""}
    if maxlen >= 2:
        for s in _gen_A(maxlen - 1):
            w = "a" + s
            if len(w) <= maxlen:
                results.add(w)
    if maxlen >= 3:
        for s in _gen_S(maxlen - 3):
            w1 = "a" + s + "bb"
            if len(w1) <= maxlen:
                results.add(w1)
            w2 = "bb" + s + "a"
            if len(w2) <= maxlen:
                results.add(w2)
    return frozenset(results)


def _filter_49_words(maxlen: int) -> frozenset[str]:
    """L(G) ∩ {w : |w|_a = |w|_b}, all strings up to maxlen."""
    return frozenset(w for w in _gen_S(maxlen) if w.count("a") == w.count("b"))


RE_BSTAR_ASTAR_BSTAR_ASTAR = re.compile(r"^b*a*b*a*$")


def _characterization_formula(max_n: int, maxlen: int) -> frozenset[str]:
    """{l(t1)...l(tn) a^n r(tn)...r(t1) | n >= 2, ti in {A:(a,bb), B:(bb,a)}} U {eps}."""
    out = {""}
    for n in range(2, max_n + 1):
        for choice in itertools.product("AB", repeat=n):
            left = ""
            right = ""
            for c in choice:
                if c == "A":
                    left += "a"
                    right = "bb" + right
                else:
                    left += "bb"
                    right = "a" + right
            w = left + "a" * n + right
            if len(w) <= maxlen:
                out.add(w)
    return frozenset(out)


def _intersection_formula(max_n: int, maxlen: int) -> frozenset[str]:
    """{b^(2m) a^(2n-m) b^(2n-2m) a^m | n >= 2, 0 <= m <= n} U {eps}."""
    out = {""}
    for n in range(2, max_n + 1):
        for m in range(0, n + 1):
            w = "b" * (2 * m) + "a" * (2 * n - m) + "b" * (2 * n - 2 * m) + "a" * m
            if len(w) <= maxlen:
                out.add(w)
    return frozenset(out)


def test_filter_49_language_equals_characterization_up_to_20():
    """L(G) ∩ F == {l(t1)...l(tn) a^n r(tn)...r(t1) | n>=2} U {eps}, |w| <= 20."""
    maxlen = 20
    actual = _filter_49_words(maxlen)
    expected = _characterization_formula(max_n=maxlen, maxlen=maxlen)
    assert actual == expected


def test_filter_49_intersection_with_bastar_up_to_24():
    """(L(G) ∩ F) ∩ b*a*b*a* == {b^2m a^(2n-m) b^(2n-2m) a^m | n>=2, 0<=m<=n} U {eps}, |w| <= 24."""
    maxlen = 24
    words = _filter_49_words(maxlen)
    actual = frozenset(w for w in words if RE_BSTAR_ASTAR_BSTAR_ASTAR.match(w))
    expected = _intersection_formula(max_n=maxlen, maxlen=maxlen)
    assert actual == expected


def _in_intersection_formula(w: str) -> bool:
    """Membership test for (L(G) ∩ F) ∩ b*a*b*a*, by the closed formula (no length cap).

    NOTE: this does NOT greedily split w into four regex groups (b*)(a*)(b*)(a*)
    — that parse is ambiguous whenever an interior block is empty (e.g. the
    middle b-block b^{2n-2m} vanishes when m == n), which silently merges the
    two a-blocks and produces false negatives. Example: "bbbbaaaa" (n=2, m=2)
    IS in L' (b^{2m}=bbbb, a^{2n-m}=aa, b^{2n-2m}=eps, a^m=aa — concatenated,
    the two a-runs merge into one "aaaa"), but the old greedy-regex oracle
    read it as b1=4,a1=4,b2=0,a2=0 and rejected it (a1=4 != 2n-m=2). Instead,
    brute-force over every (n, m) and compare against the explicit string.
    """
    if w == "":
        return True
    if not RE_BSTAR_ASTAR_BSTAR_ASTAR.match(w):
        return False
    max_n = len(w) // 2 + 2  # generous upper bound; b^{2m} alone needs <= len(w)/2
    for n_ in range(2, max_n + 1):
        for m_ in range(0, n_ + 1):
            candidate = "b" * (2 * m_) + "a" * (2 * n_ - m_) + "b" * (2 * n_ - 2 * m_) + "a" * m_
            if len(candidate) > len(w):
                continue
            if candidate == w:
                return True
    return False


def test_in_intersection_formula_handles_empty_middle_block():
    """Regression: a greedy-regex oracle merges the two a-blocks when the
    middle b-block is empty (m == n) and wrongly rejects membership."""
    assert _in_intersection_formula("bbbbaaaa")  # n=2, m=2: b^4 a^2 b^0 a^2
    assert _in_intersection_formula("b" * 6 + "a" * 6)  # n=3, m=3: b^6 a^3 b^0 a^3
    assert not _in_intersection_formula("b" * 4 + "a" * 5)  # not of the closed form


def test_filter_49_ogden_witness_all_partitions_escape():
    """Ogden's lemma for L' = (L(G) ∩ F) ∩ b*a*b*a*, p = 2, z = b^4 a^6 b^4 a^2.

    Marks the first block b^{2p}. For every Ogden-admissible decomposition
    z = uvwxy (vx contains a marked position, vwx contains at most p marked
    positions, |vx| >= 1), at least one of i in {0, 2} pumps the word out of
    L' (membership decided by the closed formula above).
    """
    p = 2
    z = "b" * (2 * p) + "a" * (3 * p) + "b" * (2 * p) + "a" * p
    assert _in_intersection_formula(z)
    n_total = len(z)
    marked = set(range(0, 2 * p))  # positions of the first block b^{2p}

    checked = 0
    for u_len in range(0, n_total + 1):
        for vwx_len in range(0, n_total - u_len + 1):
            vwx_start, vwx_end = u_len, u_len + vwx_len
            if sum(1 for pos in range(vwx_start, vwx_end) if pos in marked) > p:
                continue
            for v_len in range(0, vwx_len + 1):
                for x_len in range(0, vwx_len - v_len + 1):
                    w_len = vwx_len - v_len - x_len
                    v_start, v_end = vwx_start, vwx_start + v_len
                    w_start, w_end = v_end, v_end + w_len
                    x_start, x_end = w_end, w_end + x_len
                    if v_len + x_len == 0:
                        continue  # |vx| >= 1
                    marked_in_vx = sum(
                        1 for pos in list(range(v_start, v_end)) + list(range(x_start, x_end))
                        if pos in marked
                    )
                    if marked_in_vx < 1:
                        continue  # vx must contain a marked position
                    u, v, w, x, y = (
                        z[:v_start], z[v_start:v_end], z[w_start:w_end],
                        z[x_start:x_end], z[x_end:],
                    )
                    checked += 1
                    word0 = u + w + y
                    word2 = u + v * 2 + w + x * 2 + y
                    escapes = (not _in_intersection_formula(word0)) or (not _in_intersection_formula(word2))
                    assert escapes, (u, v, w, x, y)

    assert checked > 0
