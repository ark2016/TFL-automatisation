"""Tests for ll_system.lib.word_oracle (TODO §1 M: set_builder word-oracle bridge).

Covers the four languages named in the backlog item:
  - {a^n b^n} u {a^n c^n}
  - {w c w^R}
  - {w b c w^R}
  - {a^i b^j | i <= j}

plus the claim_verifier wiring: step 2 (constructive Format 1, substitution
branch_words, prefix_classes distinguishing_suffix) now upgrades trust to
`bounded_pass` on a correct mock grammar/proof and to `refuted` on an
incorrect one, wherever docs/VERDICT_POLICY.md §4 previously fell back to
`well_formed` for lack of a set_builder oracle.
"""
from __future__ import annotations

import json
from pathlib import Path

from ll_system.lib.word_oracle import oracle_from_ll_ir, generate_words
from ll_system.lib.claim_verifier import (
    verify_ll_grammar_claim,
    verify_substitution_claim,
    verify_prefix_classes_claim,
)

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"

# Superscript exponent letters used by the template notation (see
# word_oracle.py's module docstring for the full decoding table).
_SUP_N = "ⁿ"
_SUP_I = "ⁱ"
_SUP_J = "ʲ"


def _ir(language_spec: dict) -> dict:
    return {
        "task_type": "ll_check_language",
        "source_text": "test",
        "language_spec": language_spec,
    }


# ---------------------------------------------------------------------------
# Language fixtures
# ---------------------------------------------------------------------------

IR_ANBN_UNION_ANCN = _ir({
    "kind": "set_builder",
    "alphabet": ["a", "b", "c"],
    "variables": [
        {"name": "n", "domain": {"type": "nat"}},
        {"name": "choice", "domain": {"type": "enum", "values": ["b", "c"]}},
    ],
    "template": ["a" + _SUP_N, "choice" + _SUP_N],
    "constraints": [{"comment": "X in {b, c}, same n"}],
})

IR_WCWREV = _ir({
    "kind": "set_builder",
    "alphabet": ["a", "b", "c"],
    "variables": [{"name": "w", "domain": {"type": "word", "alphabet": ["a", "b"]}}],
    "template": ["w", "c", "rev(w)"],
    "constraints": [],
})

IR_WBCWREV = _ir({
    "kind": "set_builder",
    "alphabet": ["a", "b", "c"],
    "variables": [{"name": "w", "domain": {"type": "word", "alphabet": ["a", "b"]}}],
    "template": ["w", "b", "c", "rev(w)"],
    "constraints": [],
})

IR_AIBJ_ILEQJ = _ir({
    "kind": "set_builder",
    "alphabet": ["a", "b"],
    "variables": [
        {"name": "i", "domain": {"type": "nat"}},
        {"name": "j", "domain": {"type": "nat"}},
    ],
    "template": ["a" + _SUP_I, "b" + _SUP_J],
    "constraints": [{"op": "leq", "left": "i", "right": "j"}],
})

# {w w^R}: dead class is empty -- ANY prefix p is continuable into L via
# w=p, since p + reverse(p) always matches the template (its own reverse is
# always a valid completion). Used as the "dead class really is finite"
# fixture for TestClaimVerifierPrefixClassesStep2 (unlike IR_AIBJ_ILEQJ,
# whose dead class is genuinely infinite -- see the class docstring).
IR_WWREV = _ir({
    "kind": "set_builder",
    "alphabet": ["a", "b"],
    "variables": [{"name": "w", "domain": {"type": "word", "alphabet": ["a", "b"]}}],
    "template": ["w", "rev(w)"],
    "constraints": [],
})


# ---------------------------------------------------------------------------
# Membership: {a^n b^n} u {a^n c^n}
# ---------------------------------------------------------------------------

class TestAnBnUnionAnCn:
    def setup_method(self):
        self.oracle = oracle_from_ll_ir(IR_ANBN_UNION_ANCN)

    def test_oracle_built(self):
        assert self.oracle is not None

    def test_members(self):
        for w in ("", "ab", "aabb", "aaabbb", "ac", "aacc", "aaaccc"):
            assert self.oracle(w), f"{w!r} should be in L"

    def test_non_members(self):
        for w in ("a", "b", "c", "aabbb", "aaabb", "abc", "aabc", "aacb", "bc"):
            assert not self.oracle(w), f"{w!r} should not be in L"

    def test_generate_words_matches_oracle(self):
        words = generate_words(IR_ANBN_UNION_ANCN, 6)
        assert words  # non-empty
        assert all(self.oracle(w) for w in words)
        # Spot-check some expected members are present.
        assert "aabb" in words and "aacc" in words and "" in words

    def test_fallback_placeholder_matches_shipped_example(self):
        """The shipped example's template uses "X^n" although its enum
        variable is named "choice" -- the sole-enum-variable fallback
        (word_oracle.py's _classify_token) must still resolve it, and the
        resulting oracle must behave identically to the equivalent spec
        that names the variable directly in the template."""
        path = _EXAMPLES_DIR / "format1_anbn_union_ancn.json"
        shipped_ir = json.loads(path.read_text(encoding="utf-8"))
        shipped_oracle = oracle_from_ll_ir(shipped_ir)
        assert shipped_oracle is not None
        for w in ("", "ab", "aabb", "ac", "aacc", "aabc", "a", "abc"):
            assert shipped_oracle(w) == self.oracle(w)


# ---------------------------------------------------------------------------
# Membership: {w c w^R}
# ---------------------------------------------------------------------------

class TestWCWRev:
    def setup_method(self):
        self.oracle = oracle_from_ll_ir(IR_WCWREV)

    def test_oracle_built(self):
        assert self.oracle is not None

    def test_members(self):
        for w in ("c", "aca", "bcb", "abcba", "aabcbaa", "abbcbba"):
            assert self.oracle(w), f"{w!r} should be in L"

    def test_non_members(self):
        for w in ("", "a", "ab", "abc", "abcab", "acb", "ccc", "aacaaa"):
            assert not self.oracle(w), f"{w!r} should not be in L"

    def test_generate_words_round_trips(self):
        words = generate_words(IR_WCWREV, 5)
        assert "c" in words
        assert "abcba" in words
        assert all(self.oracle(w) for w in words)
        assert all(w.count("c") == 1 for w in words)


# ---------------------------------------------------------------------------
# Membership: {w b c w^R}
# ---------------------------------------------------------------------------

class TestWBCWRev:
    def setup_method(self):
        self.oracle = oracle_from_ll_ir(IR_WBCWREV)

    def test_oracle_built(self):
        assert self.oracle is not None

    def test_members(self):
        for w in ("bc", "abca", "bbcb", "aabcaa", "babcab"):
            assert self.oracle(w), f"{w!r} should be in L"

    def test_non_members(self):
        for w in ("", "c", "bcb", "abcba", "abcb", "abcab"):
            assert not self.oracle(w), f"{w!r} should not be in L"

    def test_generate_words_round_trips(self):
        words = generate_words(IR_WBCWREV, 5)
        assert "bc" in words
        assert "abca" in words
        assert all(self.oracle(w) for w in words)


# ---------------------------------------------------------------------------
# Membership: {a^i b^j | i <= j}
# ---------------------------------------------------------------------------

class TestAiBjILeqJ:
    def setup_method(self):
        self.oracle = oracle_from_ll_ir(IR_AIBJ_ILEQJ)

    def test_oracle_built(self):
        assert self.oracle is not None

    def test_members(self):
        for w in ("", "b", "bb", "ab", "aabb", "abb", "aabbb"):
            assert self.oracle(w), f"{w!r} should be in L"

    def test_non_members(self):
        for w in ("a", "aa", "aab", "ba", "aba", "aaab"):
            assert not self.oracle(w), f"{w!r} should not be in L"

    def test_generate_words_matches_oracle(self):
        words = generate_words(IR_AIBJ_ILEQJ, 6)
        assert words
        assert all(self.oracle(w) for w in words)
        assert "aabb" in words and "" in words
        assert "aaab" not in words


# ---------------------------------------------------------------------------
# Unsupported specs must return None (or [] for generate_words), never raise
# ---------------------------------------------------------------------------

class TestUnsupported:
    def test_non_set_builder_kind_returns_none(self):
        ir = _ir({"kind": "predicate", "alphabet": ["a"], "variable": "w", "predicate": {}})
        assert oracle_from_ll_ir(ir) is None

    def test_missing_language_spec_returns_none(self):
        assert oracle_from_ll_ir({"task_type": "ll_check_grammar"}) is None

    def test_unknown_template_token_returns_none(self):
        ir = _ir({
            "kind": "set_builder",
            "alphabet": ["a", "b"],
            "variables": [],
            "template": ["z"],  # "z" is neither alphabet nor a variable
            "constraints": [],
        })
        assert oracle_from_ll_ir(ir) is None

    def test_malformed_constraint_returns_none(self):
        ir = _ir({
            "kind": "set_builder",
            "alphabet": ["a"],
            "variables": [{"name": "n", "domain": {"type": "nat"}}],
            "template": ["a" + _SUP_N],
            "constraints": [{"op": "unknown_op", "left": "n", "right": 1}],
        })
        assert oracle_from_ll_ir(ir) is None

    def test_rev_of_unknown_variable_returns_none(self):
        ir = _ir({
            "kind": "set_builder",
            "alphabet": ["a"],
            "variables": [],
            "template": ["rev(w)"],
            "constraints": [],
        })
        assert oracle_from_ll_ir(ir) is None

    def test_generate_words_on_unsupported_spec_is_empty_not_raising(self):
        ir = _ir({"kind": "predicate", "alphabet": ["a"], "variable": "w", "predicate": {}})
        assert generate_words(ir, 5) == []

    def test_oracle_rejects_non_string_input(self):
        oracle = oracle_from_ll_ir(IR_WCWREV)
        assert oracle(123) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# claim_verifier integration (docs/VERDICT_POLICY.md §4): step 2 must now
# use this oracle instead of falling back to well_formed for set_builder IRs.
# ---------------------------------------------------------------------------

class TestClaimVerifierConstructiveStep2:
    """ll_grammar_construction (Format 1, set_builder): step 2 language
    equivalence must upgrade to bounded_pass for a correct grammar and
    refuted for an incorrect one."""

    CORRECT_GRAMMAR = {
        "nonterminals": ["S"],
        "terminals": ["a", "b", "c"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "a"]},
            {"lhs": "S", "rhs": ["b", "S", "b"]},
            {"lhs": "S", "rhs": ["c"]},
        ],
    }

    # "S -> a" for a palindrome-shaped language (TODO §1 M wording): wrong on
    # every count -- generates only {"a"}, which isn't even in L.
    WRONG_GRAMMAR = {
        "nonterminals": ["S"],
        "terminals": ["a"],
        "start": "S",
        "rules": [{"lhs": "S", "rhs": ["a"]}],
    }

    def test_correct_grammar_is_bounded_pass(self):
        result = verify_ll_grammar_claim(
            {"method": "ll_grammar_construction", "k": 1, "grammar": self.CORRECT_GRAMMAR},
            IR_WCWREV,
        )
        assert result["trust"] == "bounded_pass"

    def test_wrong_grammar_is_refuted(self):
        result = verify_ll_grammar_claim(
            {"method": "ll_grammar_construction", "k": 1, "grammar": self.WRONG_GRAMMAR},
            IR_WCWREV,
        )
        assert result["trust"] == "refuted"
        assert any("language" in i for i in result["issues"])


class TestClaimVerifierSubstitutionStep2:
    """substitution (branch_words): concrete word_1/word_2 checked against
    the set_builder oracle must upgrade well_formed to bounded_pass/refuted."""

    BASE_PS = {
        "method": "substitution",
        "for_all_k": True,
        "branch_words": {
            "common_prefix": "aa",
            "word_1": "aabb",
            "word_2": "aacc",
            "lookahead_equal_because": "both instances share the aa prefix",
        },
        "common_form_argument": "shared sentential form a^n X",
        "deciding_nonterminal_argument": "the nonterminal choosing b vs c",
        "pigeonhole_argument": "two derivations collapse for large enough n",
        "proof_explanation": "substitution argument for the union language",
    }

    def test_both_words_in_language_is_bounded_pass(self):
        result = verify_substitution_claim(self.BASE_PS, IR_ANBN_UNION_ANCN)
        assert result["trust"] == "bounded_pass"

    def test_word_not_in_language_is_refuted(self):
        bad_ps = {
            **self.BASE_PS,
            "branch_words": {**self.BASE_PS["branch_words"], "word_2": "aabc"},
        }
        result = verify_substitution_claim(bad_ps, IR_ANBN_UNION_ANCN)
        assert result["trust"] == "refuted"


class TestClaimVerifierPrefixClassesStep2:
    """prefix_classes (dead_class_finite / distinguishing_suffix): a literal
    distinguishing_suffix checked against representative_pairs via the
    set_builder oracle must upgrade well_formed to bounded_pass/refuted --
    but only when every sampled prefix has a continuation witness.
    Missing bounded continuation witnesses leaves the dead-class premise
    unknown, with trust capped at well_formed.

    IR_WWREV ({w w^R}) is used for the "dead class really is finite" cases:
    its dead class is empty, since every prefix p is continuable into L via
    w=p (p + reverse(p) always matches the template). IR_AIBJ_ILEQJ
    ({a^i b^j | i <= j}) is a DCFL whose dead class is genuinely infinite
    (any word with a 'b' before an 'a', e.g. "ba", can never be continued),
    so THEORY §1.2's Theorem 4.7.4 does not apply to it -- it is used below
    to check that the bounded search cannot support the dead-class premise."""

    WWREV_PS = {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 -> not DCFL -> not LL",
        "dead_class_finite": "the dead class is empty: every prefix p continues via p + reverse(p)",
        "distinguishing_suffix": "a",
        "separation_argument": "a^p a is in L (via w=a^p) but a^p a a is not (odd length)",
        "for_all_k": True,
        "conclusion": "the language has infinitely many Nerode classes",
        "proof_explanation": "one class per length of w",
        "representative_pairs": [{"u": "a", "v": "aa"}],
    }

    def test_separating_suffix_is_bounded_pass(self):
        result = verify_prefix_classes_claim(self.WWREV_PS, IR_WWREV)
        assert result["trust"] == "bounded_pass"
        assert result["details"]["distinguishing_suffix_check"]["representative_pairs_checked"]

    def test_non_separating_suffix_is_refuted(self):
        bad_ps = {**self.WWREV_PS, "distinguishing_suffix": "b"}
        result = verify_prefix_classes_claim(bad_ps, IR_WWREV)
        assert result["trust"] == "refuted"
        assert any("distinguishing_suffix" in i for i in result["issues"])

    def test_no_representative_pairs_and_no_random_separation_stays_well_formed(self):
        # {w w^R}: every word the generator produces has even length, so
        # appending any single-character suffix always breaks parity and
        # lands outside L -- no separating pair is found by random sampling
        # either, and with no representative_pairs supplied there is nothing
        # else to check against, so this must stay well_formed, not be
        # upgraded.
        ps = {k: v for k, v in self.WWREV_PS.items() if k != "representative_pairs"}
        result = verify_prefix_classes_claim(ps, IR_WWREV)
        assert result["trust"] == "well_formed"

    AIBJ_PS = {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 -> not DCFL -> not LL",
        "dead_class_finite": "the dead class (words with i < j already violated) is empty",
        "distinguishing_suffix": "bbb",
        "separation_argument": "a^p (b^3) in L iff p >= 3",
        "for_all_k": True,
        "conclusion": "the language has infinitely many Nerode classes",
        "proof_explanation": "one class per value of i",
        "representative_pairs": [{"u": "aa", "v": "aaaaa"}],
    }

    def test_dead_class_check_is_unknown_without_continuation_witnesses(self):
        """The distinguishing_suffix/representative_pairs check alone passes
        here (u="aa" not in L+"bbb"... in_u=True, v="aaaaa"+"bbb" in_v=False:
        a valid separation) -- but the proof's 'dead_class_finite' premise is
        false for this language (see class docstring). The finite search
        cannot establish deadness; without continuation witnesses its
        result is unknown and trust stays well_formed."""
        result = verify_prefix_classes_claim(self.AIBJ_PS, IR_AIBJ_ILEQJ)
        assert result["trust"] == "well_formed"
        assert "dead_class_finite_issues" in result["details"]["distinguishing_suffix_check"]
        assert result["details"]["distinguishing_suffix_check"]["dead_class_finite_check"] == "unknown"
