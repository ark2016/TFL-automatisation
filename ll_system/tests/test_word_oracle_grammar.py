"""Word-oracle bridge for explicit grammars (Format 2 / Format 3).

Mock mode only: pure functions, no LLM. Trust levels follow
docs/VERDICT_POLICY.md section 4: oracle None = unknown (never "not in L"),
finite agreement = bounded_pass at most.
"""

from __future__ import annotations

from ll_system.lib.claim_verifier import (
    verify_ll_grammar_claim,
    verify_prefix_classes_claim,
    verify_substitution_claim,
)
from ll_system.lib.word_oracle import generate_words, oracle_from_ll_ir

ANBN = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": ["ε"]},
    ],
}

PALINDROMES = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "a"]},
        {"lhs": "S", "rhs": ["b", "S", "b"]},
        {"lhs": "S", "rhs": ["a"]},
        {"lhs": "S", "rhs": ["b"]},
        {"lhs": "S", "rhs": []},
    ],
}


def _format2(grammar: dict) -> dict:
    return {
        "task_type": "ll_check_grammar_lang",
        "source_text": "grammar language",
        "language_spec": {"kind": "grammar", **grammar},
    }


def _format3(grammar: dict) -> dict:
    return {
        "task_type": "ll_check_grammar",
        "source_text": "is this grammar LL(k)?",
        "grammar": grammar,
    }


def _all_words(alphabet: str, max_len: int):
    words = [""]
    frontier = [""]
    for _ in range(max_len):
        frontier = [w + c for w in frontier for c in alphabet]
        words.extend(frontier)
    return words


# ---------------------------------------------------------------------------
# oracle / generator
# ---------------------------------------------------------------------------

class TestGrammarOracle:
    def test_anbn_format2_and_format3(self):
        for ir in (_format2(ANBN), _format3(ANBN)):
            oracle = oracle_from_ll_ir(ir)
            assert oracle is not None
            for w in _all_words("ab", 8):
                n = len(w) // 2
                expected = w == "a" * n + "b" * n
                assert oracle(w) is expected, w

    def test_palindromes(self):
        oracle = oracle_from_ll_ir(_format2(PALINDROMES))
        for w in _all_words("ab", 7):
            assert oracle(w) is (w == w[::-1]), w

    def test_symbol_outside_alphabet_is_false(self):
        oracle = oracle_from_ll_ir(_format3(ANBN))
        assert oracle("abc") is False

    def test_generate_words_anbn(self):
        words = generate_words(_format3(ANBN), 6)
        assert words == ["", "ab", "aabb", "aaabbb"]

    def test_generate_words_palindromes_all_in_language(self):
        words = generate_words(_format2(PALINDROMES), 5)
        assert words and all(w == w[::-1] and len(w) <= 5 for w in words)
        assert "aba" in words and "ab" not in words

    def test_multichar_terminal_gives_no_oracle(self):
        g = {
            "nonterminals": ["S"], "terminals": ["ab"], "start": "S",
            "rules": [{"lhs": "S", "rhs": ["ab"]}],
        }
        assert oracle_from_ll_ir(_format2(g)) is None
        assert generate_words(_format2(g), 4) == []

    def test_reserved_terminal_gives_no_oracle(self):
        g = {
            "nonterminals": ["S"], "terminals": ["a", "$"], "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        }
        assert oracle_from_ll_ir(_format3(g)) is None

    def test_broken_grammar_gives_no_oracle(self):
        g = {
            "nonterminals": ["S"], "terminals": ["a"], "start": "T",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        }
        assert oracle_from_ll_ir(_format3(g)) is None

    def test_non_grammar_kinds_still_unsupported(self):
        ir = {
            "task_type": "ll_check_grammar_lang", "source_text": "x",
            "language_spec": {"kind": "natural", "description": "words"},
        }
        assert oracle_from_ll_ir(ir) is None


# ---------------------------------------------------------------------------
# constructive step 2
# ---------------------------------------------------------------------------

def _constructive(candidate: dict, ir: dict, k: int = 1) -> dict:
    return verify_ll_grammar_claim(
        {"method": "ll_grammar_construction", "k": k, "grammar": candidate}, ir,
    )


LL1_ANBN = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

# S -> ab | e : strict subset of {a^n b^n}
SUBSET = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

# strict superset: a*b*
SUPERSET = {
    "nonterminals": ["S", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S"]},
        {"lhs": "S", "rhs": ["B"]},
        {"lhs": "B", "rhs": ["b", "B"]},
        {"lhs": "B", "rhs": []},
    ],
}


class TestConstructiveStep2OnGrammarTasks:
    def test_equivalent_grammar_is_bounded_pass_never_verified_format2(self):
        result = _constructive(LL1_ANBN, _format2(ANBN))
        assert result["trust"] == "bounded_pass"

    def test_equivalent_grammar_is_bounded_pass_format3(self):
        result = _constructive(LL1_ANBN, _format3(ANBN))
        assert result["trust"] == "bounded_pass"
        assert result["details"]["language_equivalence"]["method"] == "grammar_equivalent_sample"

    def test_subset_refuted_both_formats(self):
        for ir in (_format2(ANBN), _format3(ANBN)):
            assert _constructive(SUBSET, ir)["trust"] == "refuted"

    def test_superset_refuted_both_formats(self):
        for ir in (_format2(ANBN), _format3(ANBN)):
            assert _constructive(SUPERSET, ir)["trust"] == "refuted"

    def test_extra_terminal_refuted_format3(self):
        g = {
            "nonterminals": ["S"], "terminals": ["a", "b", "c"], "start": "S",
            "rules": [{"lhs": "S", "rhs": ["c"]}],
        }
        assert _constructive(g, _format3(ANBN))["trust"] == "refuted"


# ---------------------------------------------------------------------------
# prefix_classes step 2 on grammar tasks
# ---------------------------------------------------------------------------

def _prefix_ps(**extra) -> dict:
    ps = {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 -> not DCFL -> not LL",
        "dead_class_finite": "dead class is empty",
        "distinguishing_suffix": "b",
        "separation_argument": "a^i b separates",
        "for_all_k": True,
        "conclusion": "not LL",
        "proof_explanation": "classes of a^i are pairwise distinct",
    }
    ps.update(extra)
    return ps


class TestPrefixClassesStep2OnGrammarTasks:
    def test_separating_pair_gives_bounded_pass_on_explicit_grammar(self):
        # Palindromes: every prefix is live (dead class empty on the sample),
        # "a"+"a" is a palindrome, "b"+"a" is not -> suffix a separates a, b.
        ps = _prefix_ps(
            distinguishing_suffix="a",
            representative_pairs=[{"u": "a", "v": "b"}],
        )
        for ir in (_format2(PALINDROMES), _format3(PALINDROMES)):
            result = verify_prefix_classes_claim(ps, ir)
            assert result["trust"] == "bounded_pass", result
            assert result["trust"] != "verified"

    def test_dead_class_unknown_caps_at_well_formed(self):
        # In a^n b^n the prefix "b" has no continuation inside the search
        # window: the dead-class premise stays unchecked -> well_formed.
        ps = _prefix_ps(representative_pairs=[{"u": "a", "v": "aa"}])
        result = verify_prefix_classes_claim(ps, _format3(ANBN))
        assert result["trust"] == "well_formed"
        check = result["details"]["distinguishing_suffix_check"]
        assert check["representative_pairs_checked"] == [("a", "aa")]

    def test_non_separating_pair_refuted(self):
        # a b in L, a a b not in L is separating; "aa"/"aaa" + "b" both not in L
        ps = _prefix_ps(representative_pairs=[{"u": "aa", "v": "aaa"}])
        result = verify_prefix_classes_claim(ps, _format3(ANBN))
        assert result["trust"] == "refuted"

    def test_sampled_pairs_fallback_on_grammar_task(self):
        ps = _prefix_ps()  # no representative_pairs -> samples from L
        result = verify_prefix_classes_claim(ps, _format2(ANBN))
        # w = a^n b^n: w+"b" never in L, so no separating bucket pair
        assert result["trust"] == "well_formed"

    def test_unknown_oracle_keeps_well_formed(self):
        g = {
            "nonterminals": ["S"], "terminals": ["ab"], "start": "S",
            "rules": [{"lhs": "S", "rhs": ["ab"]}],
        }
        ps = _prefix_ps(representative_pairs=[{"u": "a", "v": "aa"}])
        result = verify_prefix_classes_claim(ps, _format3(g))
        assert result["trust"] == "well_formed"


# ---------------------------------------------------------------------------
# substitution step 2 now has an oracle on grammar tasks
# ---------------------------------------------------------------------------

class TestSubstitutionUsesGrammarOracle:
    def test_oracle_available_for_substitution_step(self):
        from ll_system.lib.claim_verifier import _try_word_oracle
        oracle = _try_word_oracle(_format2(ANBN))
        assert oracle is not None
        assert oracle("aabb") is True
        assert oracle("aab") is False
        assert callable(verify_substitution_claim)
