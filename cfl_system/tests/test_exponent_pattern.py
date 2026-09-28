"""Tests for cfl_system.lib.exponent_pattern (docs/VERDICT_POLICY.md §4).

Covers: parsing exponent-notation language descriptions into a callable
membership oracle, boolean conditions (and/or/not, chains like "j=k=l" and
"i<=j<=k"), domain declarations ("n, m >= 0"), the "union" combinator, and
the never-raise contract on unparseable text.
"""

from __future__ import annotations

import itertools

import pytest

from cfl_system.lib.exponent_pattern import ExponentPattern, parse_exponent_pattern


def _words_up_to(alphabet: list[str], max_len: int):
    for length in range(0, max_len + 1):
        for combo in itertools.product(alphabet, repeat=length):
            yield "".join(combo)


class TestBasicBlocks:
    def test_anbncm(self):
        p = parse_exponent_pattern("a^n b^n c^m")
        assert isinstance(p, ExponentPattern)
        assert p("") is True
        assert p("abc") is True
        assert p("aabbccc") is True
        assert p("aabb") is True
        assert p("c") is True
        assert p("ab") is True  # n=1, m=0
        assert p("aab") is False  # n mismatch
        assert p("ba") is False
        assert p("abcabc") is False

    def test_wrapped_in_braces_with_domain_declaration(self):
        p = parse_exponent_pattern("{a^n b^n c^m | n, m >= 0}")
        assert isinstance(p, ExponentPattern)
        assert p("") is True
        assert p("aabbccc") is True
        assert p("aab") is False

    def test_number_exponent(self):
        p = parse_exponent_pattern("a^3 b^2")
        assert isinstance(p, ExponentPattern)
        assert p("aaabb") is True
        assert p("aabb") is False
        assert p("aaabbb") is False


class TestOrCondition:
    def test_i_eq_0_or_j_eq_k_eq_l(self):
        p = parse_exponent_pattern("{a^i b^j c^k d^l | i = 0 or j = k = l}")
        assert isinstance(p, ExponentPattern)
        # i = 0 branch: any j, k, l
        assert p("bcd") is True
        assert p("bbccdd") is True
        assert p("") is True
        # j = k = l branch (i != 0 allowed)
        assert p("abcd") is True       # i=1,j=k=l=1
        assert p("aabbccdd") is True   # i=2,j=k=l=2
        # neither branch holds
        assert p("aabccddd") is False  # i=2,j=1,k=2,l=3
        assert p("aabbccd") is False   # i=2,j=2,k=2,l=1


class TestChainedComparisons:
    def test_le_chain(self):
        p = parse_exponent_pattern("a^i b^j c^k | i <= j <= k")
        assert isinstance(p, ExponentPattern)
        assert p("abc") is True       # 1<=1<=1
        assert p("abbccc") is True    # 1<=2<=3
        assert p("aabbc") is False    # 2<=2<=1 false


class TestImplicitMultiplication:
    def test_braced_expr_2n(self):
        p = parse_exponent_pattern("a^n b^{2n}")
        assert isinstance(p, ExponentPattern)
        assert p("") is True
        assert p("abb") is True
        assert p("aabbbb") is True
        assert p("ab") is False
        assert p("aab") is False
        assert p("abbb") is False

    def test_parenthesised_multi_letter_exponent(self):
        p = parse_exponent_pattern("a^(6n) b^(6n) c^(6n)")
        assert isinstance(p, ExponentPattern)
        assert p("") is True
        assert p("a" * 6 + "b" * 6 + "c" * 6) is True
        assert p("a" * 6 + "b" * 6 + "c" * 5) is False


class TestParenthesizedRepeatedWord:
    def test_ab_repeated_word(self):
        p = parse_exponent_pattern("(ab)^n c^n")
        assert isinstance(p, ExponentPattern)
        assert p("") is True
        assert p("abc") is True
        assert p("ababcc") is True
        assert p("ab") is False
        assert p("c") is False
        assert p("abcc") is False
        assert p("ababc") is False


class TestInequalityCondition:
    def test_n_neq_m(self):
        p = parse_exponent_pattern("a^n b^m | n != m")
        assert isinstance(p, ExponentPattern)
        assert p("") is False
        assert p("ab") is False
        assert p("aabb") is False
        assert p("b") is True
        assert p("aab") is True
        assert p("abb") is True

    def test_unicode_ne_symbol(self):
        p = parse_exponent_pattern("a^n b^m | n ≠ m")
        assert isinstance(p, ExponentPattern)
        assert p("ab") is False
        assert p("aab") is True


class TestNestedSameLetterBlocks:
    def test_a_to_n_a_to_m_general_backtracking(self):
        p = parse_exponent_pattern("a^n a^m")
        assert isinstance(p, ExponentPattern)
        # Any total number of a's decomposes as SOME (n, m) split.
        assert p("") is True
        assert p("a") is True
        assert p("aaaaa") is True
        assert p("b") is False
        assert p("aaba") is False

    def test_nested_blocks_with_condition_pick_valid_split(self):
        # Only n=0,m=5 (or n=5,m=0, etc.) satisfy n+m==5; condition should
        # still be satisfiable via backtracking over the split point.
        p = parse_exponent_pattern("a^n a^m | n + m = 5")
        assert isinstance(p, ExponentPattern)
        assert p("aaaaa") is True
        assert p("aaaa") is False
        assert p("aaaaaa") is False


class TestUnionCombinator:
    def test_two_branches(self):
        p = parse_exponent_pattern(
            "{a^n b^n | n >= 0} union {a^n b^(2n) | n >= 0}"
        )
        assert p is not None
        assert p("") is True
        assert p("ab") is True
        assert p("abb") is True
        assert p("aabbbb") is True
        assert p("abbb") is False


class TestNeverRaises:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            None,
            42,
            "not exponent notation at all",
            "{ww | w in {a,b}*}",
            "a^(2^n)",  # nested exponent, not supported
            "a^n b^m ===",
            "a^n b^m | )(",
            "a^",
            "(unclosed",
        ],
    )
    def test_returns_none_not_raise(self, text):
        result = parse_exponent_pattern(text)
        assert result is None

    def test_word_length_capped_at_40(self):
        p = parse_exponent_pattern("a^n")
        assert isinstance(p, ExponentPattern)
        assert p("a" * 40) is True
        # Over the cap is "unknown", never a guessed reject (root CLAUDE.md
        # item 3 / docs/VERDICT_POLICY.md §4): a bounded consumer (e.g. the
        # dcfl word_sampler bridge, whose own cap is looser) must be able to
        # tell "definitely not in L" apart from "too long to check".
        assert p("a" * 41) is None

    def test_oracle_never_raises_on_arbitrary_word(self):
        p = parse_exponent_pattern("a^n b^n c^m | n, m >= 0")
        assert isinstance(p, ExponentPattern)
        for w in ("", "xyz", "a" * 5 + "!" * 3, "abcabcabc"):
            # Must not raise regardless of the word's content.
            assert p(w) in (True, False)


class TestCallableContract:
    def test_is_callable_word_to_bool(self):
        p = parse_exponent_pattern("a^n b^n")
        assert callable(p)
        assert isinstance(p("aabb"), bool)


# ---------------------------------------------------------------------------
# Bug-fix regression tests: a pattern this module cannot mechanically decide
# must come back as None (unparseable), never as a callable that silently
# rejects (or, for accepts()/__call__, silently mis-answers) every word --
# docs/VERDICT_POLICY.md §4.
# ---------------------------------------------------------------------------


class TestMultiVariableExponentResolvedByOtherBlocks:
    def test_a_to_n_plus_m_resolved_via_b_and_c_blocks(self):
        # a^{n+m} b^n c^m: the a-block alone has two unbound variables, but
        # n and m are each pinned down by the (single-variable) b/c blocks,
        # so the pattern IS resolvable and must not be rejected wholesale.
        p = parse_exponent_pattern("a^{n+m} b^n c^m")
        assert isinstance(p, ExponentPattern)
        assert p("") is True                # n=0, m=0
        assert p("aaabbc") is True          # n=2, m=1 -> a^(n+m) = a^3
        assert p("aabbc") is False          # a-count doesn't match n+m
        assert p("aaabb") is False          # no c's at all -> no valid split

    def test_i_plus_j_form_general(self):
        p = parse_exponent_pattern("{a^{i+j} b^i c^j | i, j >= 0}")
        assert isinstance(p, ExponentPattern)
        assert p("aabc") is True            # i=1,j=1 -> a^2 b^1 c^1
        assert p("aaaabbcc") is True         # i=2,j=2 -> a^4 b^2 c^2
        assert p("aaabbcc") is False        # a-count 3 != i+j = 2+2


class TestConditionVariableMustBeBound:
    def test_condition_var_not_in_any_block_returns_none(self):
        assert parse_exponent_pattern("a^n | n = 2k") is None
        assert parse_exponent_pattern("a^n | k >= 0") is None


class TestUnknownWordNotTreatedAsVariable:
    def test_mod_word_returns_none_not_reject_all(self):
        # Before the fix, "n mod 2 = 0" silently parsed as the implicit-
        # multiplication expression "n * mod * 2 = 0", giving a callable
        # that rejected every word (including words that should match).
        assert parse_exponent_pattern("a^n b^m | n mod 2 = 0") is None

    def test_eval_reg15_shape_returns_none(self):
        # docs/EVAL_SET.md reg-15: {a^n | n mod 3 != 0}
        assert parse_exponent_pattern("{a^n | n mod 3 != 0}") is None

    def test_prime_and_in_words_return_none(self):
        assert parse_exponent_pattern("a^n | n prime = 0") is None
        assert parse_exponent_pattern("a^n b^m | n in m = 0") is None


class TestReversalNotationNotMistakenForExponent:
    def test_w_w_reversed_returns_none(self):
        # "w w^R" / "{w w^R}" denote w followed by the REVERSAL of w -- a
        # notation this module doesn't support (word variables, not fixed
        # alphabet letters) -- not "literal 'w' followed by w repeated R
        # times", which is what naively treating R as an exponent count
        # variable would (wrongly) accept/reject.
        assert parse_exponent_pattern("w w^R") is None
        assert parse_exponent_pattern("{w w^R}") is None


class TestApproximateFlag:
    """docs/VERDICT_POLICY.md §4: even a successfully parsed pattern is
    fundamentally bounded, so it must self-identify as approximate the same
    way cfl_oracle.py's natural_language_filter oracle does, letting
    claim_verifier._get_oracle / cfl_oracle_test skip it for a full
    semantic-check upgrade rather than trust it unconditionally."""

    def test_single_pattern_is_approximate(self):
        p = parse_exponent_pattern("a^n b^n")
        assert isinstance(p, ExponentPattern)
        assert p.is_approximate is True
        assert isinstance(p.approximation_reason, str) and p.approximation_reason

    def test_union_pattern_is_approximate(self):
        p = parse_exponent_pattern("{a^n b^n | n>=0} union {a^n b^(2n) | n>=0}")
        assert p is not None
        assert p.is_approximate is True


class TestBudgetExceededReturnsNone:
    def test_budget_exhaustion_is_unknown_not_false(self):
        p = parse_exponent_pattern("a^n a^m a^k a^l | n + m + k + l = 3")
        assert isinstance(p, ExponentPattern)
        # step_budget=1: the very first recursive _match call already
        # exceeds it, regardless of the word -- deterministic regardless of
        # search order.
        tiny = ExponentPattern(
            segments=p.segments, condition=p.condition, step_budget=1,
        )
        assert tiny("aaa") is None
