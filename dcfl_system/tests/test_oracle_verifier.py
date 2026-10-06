"""
Tests for the DCFL oracle verifier stub.

Covers verify_agent_results with various agent statuses,
invalid outputs, empty inputs, and mixed scenarios.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dcfl_system.lib.oracle_verifier import verify_agent_results
from dcfl_system.lib import oracle_verifier as ov

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# ---------------------------------------------------------------------------
# Success agents -> not_verified
# ---------------------------------------------------------------------------

def test_success_agents_not_verified():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "shallit": {"status": "success", "verdict": "dcfl", "confidence": 0.8},
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "not_verified"


# ---------------------------------------------------------------------------
# Not_applicable agents -> not_applicable
# ---------------------------------------------------------------------------

def test_not_applicable_agents():
    agent_results = {
        "dcfl_pumping": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
        "inh_ambiguity": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Invalid output -> error
# ---------------------------------------------------------------------------

def test_invalid_output_error():
    agent_results = {
        "stack_strategy": "not a dict",
        "shallit": 42,
        "closure_reduction": None,
    }
    result = verify_agent_results(agent_results, {})
    for name in agent_results:
        assert result[name]["verification_status"] == "error"
        issues = result[name].get("issues", [])
        assert any("invalid output" in str(i).lower() for i in issues)


# ---------------------------------------------------------------------------
# Empty agent_results -> empty result
# ---------------------------------------------------------------------------

def test_empty_agent_results():
    result = verify_agent_results({}, {})
    assert result == {}


# ---------------------------------------------------------------------------
# Mixed statuses
# ---------------------------------------------------------------------------

def test_mixed_statuses():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
        "dcfl_pumping": {"status": "not_applicable", "verdict": None, "confidence": 0.0},
        "inh_ambiguity": "broken",
    }
    result = verify_agent_results(agent_results, {})
    assert result["stack_strategy"]["verification_status"] == "not_verified"
    assert result["dcfl_pumping"]["verification_status"] == "not_applicable"
    assert result["inh_ambiguity"]["verification_status"] == "error"


# ---------------------------------------------------------------------------
# Verification result structure
# ---------------------------------------------------------------------------

def test_verification_result_structure():
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "confidence": 0.9},
    }
    result = verify_agent_results(agent_results, {})
    entry = result["stack_strategy"]
    assert "verification_status" in entry
    assert "checks_run" in entry
    assert "checks_passed" in entry
    assert "checks_total" in entry
    assert isinstance(entry["checks_run"], list)
    assert entry["checks_passed"] == 0
    assert entry["checks_total"] == 0


# ---------------------------------------------------------------------------
# closure_reduction direction vocabulary matches closure_table.py (root
# TODO.md §2: "both" vs "constructive"/"destructive" словари направлений)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction", ["constructive", "destructive", "both"])
def test_closure_reduction_direction_accepts_closure_table_vocabulary(direction):
    """closure_table.py's own proof_direction for the symmetric 'complement'
    operation is 'both' (see get_proof_direction('complement')); the oracle's
    direction_valid check must accept the same value, not just the two the
    reasoning agent ultimately commits to."""
    agent_results = {
        "closure_reduction": {
            "status": "success",
            "verdict": "dcfl",
            "confidence": 0.6,
            "proof_sketch": {
                "operation": "complement",
                "source_language": "a*b*",
                "transformation": "L = complement(a*b*)",
                "direction": direction,
            },
        },
    }
    result = verify_agent_results(agent_results, {})
    entry = result["closure_reduction"]
    assert "direction_valid" in entry["checks_run"]
    assert entry["checks_passed"] == entry["checks_total"], entry.get("issues")


def test_closure_reduction_direction_still_rejects_garbage():
    agent_results = {
        "closure_reduction": {
            "status": "success",
            "verdict": "dcfl",
            "confidence": 0.6,
            "proof_sketch": {
                "operation": "complement",
                "source_language": "a*b*",
                "transformation": "L = complement(a*b*)",
                "direction": "sideways",
            },
        },
    }
    result = verify_agent_results(agent_results, {})
    entry = result["closure_reduction"]
    assert entry["checks_passed"] < entry["checks_total"]
    assert any("direction" in issue for issue in entry.get("issues", []))


# ---------------------------------------------------------------------------
# dcfl_pumping condition (1)/(2) brute force -- low-level unit tests
# (VERDICT_POLICY.md §4; see test_verdict_policy.py for the end-to-end
# semantic-check tests that exercise these through verify_agent_results).
# ---------------------------------------------------------------------------

def test_pump_outcome_closed_when_i0_fails():
    def pump(i):
        # i=0 gives "X" (not in L), i=2/3 would give "in-L" words -- but
        # since i=0 already fails, the decomposition is closed regardless.
        return ("X" if i == 0 else "ok"), ("Y" if i == 0 else "ok")

    def oracle(word):
        return word == "ok"

    assert ov._pump_outcome(oracle, pump) == "closed"


def test_pump_outcome_inconclusive_when_only_finite_exponents_survive():
    def pump(i):
        return "always-in-L", "always-in-L-too"

    def oracle(word):
        return True

    assert ov._pump_outcome(oracle, pump) == "inconclusive"


def test_pump_outcome_inconclusive_when_oracle_never_decides():
    def pump(i):
        return f"w{i}", f"z{i}"

    def oracle(word):
        return None

    assert ov._pump_outcome(oracle, pump) == "inconclusive"


def test_check_condition1_does_not_infer_all_exponents_from_finite_survival():
    # The checker sees finitely many oracle calls, not a proof of universality.
    universal_oracle = lambda word: True
    status, msg = ov._check_condition1("aaaa", "b", "c", p=2, oracle=universal_oracle)
    assert status == "limit"
    assert msg is None


def test_check_condition2_does_not_infer_all_exponents_from_finite_survival():
    universal_oracle = lambda word: True
    status, msg = ov._check_condition2("aaaa", "b", "c", p=2, oracle=universal_oracle)
    assert status == "limit"
    assert msg is None


def test_check_condition1_no_evidence_when_x_shorter_than_window():
    # x has length 0 -> no candidate window exists at all.
    status, msg = ov._check_condition1("", "y", "z", p=2, oracle=lambda w: False)
    assert status == "no_evidence"
    assert msg is None


def test_bounded_factorizations_includes_trivial_and_respects_cap():
    facs = ov._bounded_factorizations("abcdef", max_mid_len=2)
    assert ("", "", "abcdef") in facs
    assert all(len(mid) <= 2 for _, mid, _ in facs)
    assert any(mid for _, mid, _ in facs)  # at least one non-trivial split


# ---------------------------------------------------------------------------
# dcfl_pumping x/y/z decomposition (blocker fix): the words' own longest
# common prefix must NOT be used as x directly -- it generally leaves y or z
# empty, or the two starting on different letters, either of which violates
# the lemma's own precondition (THEORY.md §1.1: y, z != eps, (1)y = (1)z),
# so a pair checked that way is never actually an instance of the lemma.
# ---------------------------------------------------------------------------

def test_xyz_from_common_prefix_backs_off_by_one_for_theory_worked_example():
    # docs/THEORY.md §1.1: w = a^3 b^3, w' = a^3 b^6 -- the FULL common run
    # is all of w (w is a genuine prefix of w'), which would leave y empty
    # (the bug this fixes); backing off by one character gives exactly the
    # hand-verified decomposition x=a^3 b^2, y=b, z=b^4.
    x, y, z = ov._xyz_from_common_prefix("aaabbb", "aaabbbbbb")
    assert (x, y, z) == ("aaabb", "b", "bbbb")
    assert y[0] == z[0]  # the lemma's own precondition, never skipped


def test_xyz_from_common_prefix_none_when_words_share_no_prefix():
    assert ov._xyz_from_common_prefix("ab", "ba") is None
    assert ov._xyz_from_common_prefix("", "ab") is None


def test_xyz_from_proof_decomposition_prefers_proofs_own_literal_split():
    # The proof's own common_prefix_x/suffix_y/suffix_z, once instantiated,
    # reconstruct word_w/word_w_prime exactly -- use them as-is rather than
    # falling back to the common-prefix heuristic.
    alphabet = {"a", "b"}
    w = "aaabbb"       # a^3 b^3
    w_prime = "aaabbbbbb"  # a^3 b^6
    xyz = ov._xyz_from_proof_decomposition(
        "aⁿbⁿ⁻¹", "b", "bⁿ⁺¹", n=3, alphabet_set=alphabet, w=w, w_prime=w_prime,
    )
    assert xyz == ("aaabb", "b", "bbbb")


def test_xyz_from_proof_decomposition_none_when_inconsistent_with_the_words():
    # suffix_y instantiates to something that doesn't actually reconstruct
    # word_w at this n -- the proof's own claimed split doesn't match the
    # words it's supposedly a decomposition of, so it's not usable.
    alphabet = {"a", "b"}
    xyz = ov._xyz_from_proof_decomposition(
        "aⁿ", "bⁿ", "bⁿ", n=3, alphabet_set=alphabet, w="aaabbb", w_prime="aaabbbbbb",
    )
    assert xyz is None


# ---------------------------------------------------------------------------
# word_instances (docs/VERDICT_POLICY.md §4 dcfl/dcfl_pumping) -- structural
# validation, and the exam_04 (task_grammar_aSSb) regression: this language
# IS actually DCFL, so no dcfl_pumping proof about it -- however
# structurally tidy -- may reach `well_formed` on its own; the mandatory
# semantic check must resolve it one way or the other (refuted, generally,
# since a real counterexample decomposition genuinely exists for a true
# DCFL language and the bounded brute force is expected to find it).
# ---------------------------------------------------------------------------

def test_validate_word_instance_entry_accepts_a_genuine_instance():
    entry = {"w": "aaabbb", "w_prime": "aaabbbbbb", "x_length": 5}
    assert ov._validate_word_instance_entry(entry, 2) == 5
    assert ov._validate_word_instance_entry(entry, 4) == 5  # p < x_length still holds (4<5)


@pytest.mark.parametrize("bad_entry,p", [
    (None, 2),
    ({}, 2),
    ({"w": "", "w_prime": "aaabbbbbb", "x_length": 5}, 2),
    ({"w": "aaabbb", "w_prime": "aaabbbbbb", "x_length": "5"}, 2),  # not an int
    ({"w": "aaabbb", "w_prime": "aaabbbbbb", "x_length": 2}, 2),    # x_length must be > p, not ==
    ({"w": "aaabbb", "w_prime": "aaabbbbbb", "x_length": 6}, 2),    # x_length >= len(w): y would be empty
    ({"w": "aaabbb", "w_prime": "bbaaaa", "x_length": 3}, 2),       # common prefix mismatch
    ({"w": "aaabbbbbb", "w_prime": "aaabbb", "x_length": 6}, 2),    # x_length == len(w_prime): z empty
])
def test_validate_word_instance_entry_rejects_malformed_entries(bad_entry, p):
    assert ov._validate_word_instance_entry(bad_entry, p) is None


def test_validate_word_instance_entry_rejects_mismatched_first_letters():
    # y = w[x_length:] = "a...", z = w_prime[x_length:] = "b..." -- lemma's
    # own precondition (⁽¹⁾y = ⁽¹⁾z) violated.
    entry = {"w": "aaaab", "w_prime": "aaaba", "x_length": 3}
    assert ov._validate_word_instance_entry(entry, 2) is None


def test_verify_dcfl_pumping_exam04_grammar_never_reaches_bounded_pass():
    # A finite experiment may leave a false argument well_formed, but cannot promote it.
    member_pairs = [
        ("ab", "abaabb"),
        ("aabb", "aabbab"),
        ("aaabbb", "aaabbbab"),
        ("ba", "baaabbb"),
    ]
    for w, w_prime in member_pairs:
        xyz = ov._xyz_from_common_prefix(w, w_prime)
        assert xyz is not None, (w, w_prime)
        x, _y, _z = xyz
        x_length = len(x)
        if x_length <= 3:
            continue  # too short to test both p=2 and p=3 -- skip this pair
        proof_sketch = {
            "kind": "dcfl_pumping",
            "pumping_length": "p",
            "word_w": w,
            "word_w_prime": w_prime,
            "common_prefix_x": x,
            "suffix_y": w[x_length:],
            "suffix_z": w_prime[x_length:],
            "first_letters_match": "см. word_instances",
            "condition1_argument": "см. word_instances",
            "condition2_argument": "см. word_instances",
            "word_instances": {
                "2": {"w": w, "w_prime": w_prime, "x_length": x_length},
                "3": {"w": w, "w_prime": w_prime, "x_length": x_length},
            },
        }
        agent_results = {
            "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
        }
        result = verify_agent_results(agent_results, GRAMMAR_ASSB_TASK_IR)
        entry = result["dcfl_pumping"]
        assert entry["trust"] in ("refuted", "not_verified", "well_formed"), (
            w, w_prime, entry["verification_status"]
        )


def test_verify_dcfl_pumping_exam04_small_p_closure_does_not_reach_bounded_pass():
    # Backlog review (BLOCKER): concrete word_instances for exam_04
    # (task_grammar_aSSb, an ACTUALLY-DCFL language) where conditions (1)/(2)
    # both close at p=2 and p=3 (found by brute-force enumeration of the
    # review over members of length <= 10: 101 such instances at p=2, 42 at
    # p=3) -- these two specific instances close both conditions within this
    # module's own decomposition budget (no refuting decomposition happens
    # to be found at this small p), which used to be scored `bounded_pass`
    # 0.85 for a FALSE "non_dcfl" claim -- exactly the dcfl-21 failure mode.
    # A small, fixed p is not exhaustive evidence for Yu's lemma (the real
    # pumping constant for this DPDA is far larger than 2 or 3), so trust
    # must stay at `well_formed`, never `bounded_pass`, regardless of
    # whether the bounded brute force also happens to refute some OTHER
    # word_instances pair.
    proof_sketch = {
        "kind": "dcfl_pumping",
        "pumping_length": "p",
        "word_w": "aababb",
        "word_w_prime": "aabababbab",
        "common_prefix_x": "aaba",
        "suffix_y": "bb",
        "suffix_z": "babbab",
        "first_letters_match": "см. word_instances",
        "condition1_argument": "см. word_instances",
        "condition2_argument": "см. word_instances",
        "word_instances": {
            "2": {"w": "aababb", "w_prime": "aabababbab", "x_length": 4},
            "3": {"w": "aaabbabb", "w_prime": "aaabbabbab", "x_length": 6},
        },
    }
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, GRAMMAR_ASSB_TASK_IR)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "well_formed"
    assert entry["trust"] == "well_formed"


def test_check_condition1_inconclusive_decomposition_downgrades_closed_to_limit():
    # Backlog review (major): a decomposition the oracle can't decide (None
    # on a pumped word) must not be silently dropped -- if ANY tried
    # decomposition is inconclusive, the overall result must not claim
    # "closed" (which would overstate coverage), even when other
    # decompositions genuinely closed. An oracle that never decides anything
    # makes EVERY decomposition inconclusive, so "closed" must never be
    # returned.
    status, _msg = ov._check_condition1("aaaa", "b", "c", p=2, oracle=lambda w: None)
    assert status == "limit"

    status2, _msg2 = ov._check_condition2("aaaa", "b", "c", p=2, oracle=lambda w: None)
    assert status2 == "limit"


# ---------------------------------------------------------------------------
# shallit dead-class / literal-pair extraction -- low-level unit tests
# ---------------------------------------------------------------------------

def test_continuable_true_when_word_itself_is_in_l():
    assert ov._continuable(lambda w: w == "ab", "ab", ["a", "b"]) is True


def test_continuable_exhaustive_false_when_no_extension_helps():
    # Nothing is ever in L -> the whole bounded search is exhausted and
    # decisively returns False (not None).
    assert ov._continuable(lambda w: False, "x", ["a", "b"], max_extra=2) is False


def test_continuable_none_when_oracle_never_decides():
    assert ov._continuable(lambda w: None, "x", ["a", "b"], max_extra=2) is None


def test_extract_literal_word_pairs_finds_distinct_alphabet_runs():
    text = "рассмотрим слова u=ab и v=aab, тогда aabb в языке"
    pairs = ov._extract_literal_word_pairs(text, {"a", "b"}, limit=5)
    tokens = {tok for pair in pairs for tok in pair}
    assert "ab" in tokens and "aab" in tokens
    assert all(u != v for u, v in pairs)


def test_extract_literal_word_pairs_empty_for_purely_symbolic_prose():
    text = "uw ∈ L, vw ∉ L (или наоборот) для произвольных u != v"
    pairs = ov._extract_literal_word_pairs(text, {"a", "b"}, limit=5)
    assert pairs == []


# ---------------------------------------------------------------------------
# shallit dead_class_status (VERDICT_POLICY.md §4 dcfl/shallit) -- the
# mandatory oracle-based dead-class cross-check (independent of
# distinguishing_suffix), plus the structural "infinite + status success"
# self-contradiction check.
# ---------------------------------------------------------------------------

GRAMMAR_ASSB_TASK_IR = json.loads(
    (EXAMPLES_DIR / "task_grammar_aSSb.json").read_text(encoding="utf-8")
)
ANBNCM_TASK_IR_FOR_SHALLIT = json.loads(
    (EXAMPLES_DIR / "task_anbncm.json").read_text(encoding="utf-8")
)


def test_enumerate_words_up_to_length_shortest_first_and_capped():
    words = ov._enumerate_words_up_to_length(["a", "b"], max_len=3, limit=100)
    assert words[0] == ""
    # shortest first, deterministic alphabet order within each length
    assert words[:7] == ["", "a", "b", "aa", "ab", "ba", "bb"]
    assert all(len(w) <= 3 for w in words)
    # capped well below the theoretical count when limit is small
    assert ov._enumerate_words_up_to_length(["a", "b"], max_len=8, limit=10) == \
        ov._enumerate_words_up_to_length(["a", "b"], max_len=8, limit=10)[:10]
    assert len(ov._enumerate_words_up_to_length(["a", "b"], max_len=8, limit=10)) <= 10


def test_enumerate_words_up_to_length_empty_alphabet():
    assert ov._enumerate_words_up_to_length([], max_len=8) == []


def test_check_dead_class_finite_skips_claims_other_than_empty():
    # "infinite" (and missing/invalid) claims are not this function's job --
    # _verify_shallit handles "infinite" structurally, without touching the
    # oracle at all. The legacy "finite" value is normalized onto "empty" by
    # the CALLER (`_normalize_dead_class_status`, docs/VERDICT_POLICY.md §4)
    # before this function ever sees it, so at this low level "finite"
    # itself is just another value this function does nothing with.
    for claimed in ("infinite", "finite", None, "bogus"):
        assert ov._check_dead_class_finite(GRAMMAR_ASSB_TASK_IR, claimed) == (None, None, [])


def test_check_dead_class_finite_search_does_not_certify_deadness():
    # bb is actually dead here, but this bounded membership search cannot certify it.
    outcome, evidence, issues = ov._check_dead_class_finite(GRAMMAR_ASSB_TASK_IR, "empty")
    assert outcome is None and evidence is None
    assert "does not prove" in issues[0]


def test_normalize_dead_class_status_maps_legacy_finite_onto_empty():
    # docs/VERDICT_POLICY.md §4: the schema's live value space is now just
    # {"empty", "infinite"} -- the legacy "finite" value (D finite and
    # nonempty, which cannot actually occur: D is closed under
    # right-extension, so a nonempty D is always infinite) is accepted for
    # backward compatibility and normalized onto "empty" everywhere.
    assert ov._normalize_dead_class_status("finite") == "empty"
    assert ov._normalize_dead_class_status("empty") == "empty"
    assert ov._normalize_dead_class_status("infinite") == "infinite"
    assert ov._normalize_dead_class_status(None) is None
    assert ov._normalize_dead_class_status("bogus") == "bogus"


def test_check_dead_class_legacy_claim_has_same_search_limits():
    # Normalizing a legacy claim does not make bounded search complete.
    outcome, evidence, issues = ov._check_dead_class_finite(
        GRAMMAR_ASSB_TASK_IR, ov._normalize_dead_class_status("finite"),
    )
    assert outcome is None and evidence is None
    assert "does not prove" in issues[0]


WWR_TASK_IR = {
    "task_id": "test_ww_reverse",
    "input_format": "set_builder",
    "language_spec": {
        "kind": "set_builder",
        "word_pattern": "ww^R",
        "variables": [{"name": "w", "domain": "(a|b)*", "quantifier": "forall"}],
        "constraints": [],
    },
    "alphabet": ["a", "b"],
}


def test_check_dead_class_finite_empty_not_falsely_contradicted_for_wwr():
    # Regression for the blocker found in review: {ww^R} (shallit.md's own
    # worked "empty" example, THEORY.md §1.2 -- "любое слово x продолжается
    # до x*x^R") has D = empty (every word continues into L by appending its
    # own reverse), but a FIXED +6-symbol continuation bound falsely
    # "refuted" this: an 8-symbol-or-shorter word can need up to its own
    # length in extra symbols (e.g. 'aaaaaab', 7 symbols, needs exactly the
    # 7-symbol continuation 'baaaaaa' to complete 'aaaaaab'+'baaaaaa' =
    # w * w^R) -- more than a fixed bound of 6. The bound must scale with
    # len(w), not stay fixed, or a correct 'empty' proof gets wrongly
    # refuted.
    outcome, evidence, issues = ov._check_dead_class_finite(WWR_TASK_IR, "empty")
    assert (outcome, evidence, issues) == ("bounded_pass", None, [])


def test_verify_shallit_wwr_empty_claim_is_not_refuted_end_to_end():
    # Same regression, exercised through verify_agent_results end-to-end: a
    # textbook-correct nerode_classes/'empty' proof for {ww^R} must not come
    # back 'refuted' by the dead-class oracle check.
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "empty",
        "distinguishing_suffix": "b a^N b u^R",
        "separation_argument": "u*w — палиндром, v*w — нет при u != v",
        "argument": "любое слово x продолжается до x*x^R в L, значит D пуст; "
                    "по контрапозиции теоремы 4.7.4 [Sh] L не DCFL",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, WWR_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] != "refuted"


def test_verify_shallit_legacy_finite_value_accepted_with_backward_compat_note():
    # docs/VERDICT_POLICY.md §4: the retired "finite" value is still ACCEPTED
    # (not a structural failure) when D genuinely is empty -- normalized to
    # "empty" for the oracle-based dead-class check (same {ww^R} example as
    # the "empty" regression above), with a note in `issues` even though the
    # result is not refuted.
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "finite",
        "distinguishing_suffix": "b a^N b u^R",
        "separation_argument": "u*w — палиндром, v*w — нет при u != v",
        "argument": "любое слово x продолжается до x*x^R в L, значит D пуст; "
                    "по контрапозиции теоремы 4.7.4 [Sh] L не DCFL",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, WWR_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] != "refuted"
    issues = " ".join(entry.get("issues", []))
    assert "finite" in issues and "empty" in issues and "обратная совместимость" in issues


def test_verify_shallit_bounded_dead_search_does_not_refute():
    # The finite search may diagnose a missing continuation, not refute the claim.
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "finite",
        "distinguishing_suffix": "w = b a^N b u^R",
        "separation_argument": "uw и vw различаются по палиндромности",
        "argument": "по контрапозиции теоремы 4.7.4 [Sh]",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, GRAMMAR_ASSB_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "well_formed"
    assert entry["trust"] == "well_formed"
    assert any("unresolved" in issue for issue in entry["issues"])


def test_verify_shallit_exam04_dead_class_status_infinite_with_success_is_refuted():
    # VERDICT_POLICY.md §4: dead_class_status == "infinite" while the agent
    # still reports status == "success" is self-contradictory -- the proof
    # admits the technique is inapplicable but didn't return
    # status == "not_applicable". Refuted structurally, WITHOUT even
    # consulting the oracle (the live dcfl04/exam04 bug this locks in: a
    # proof said "D is infinite, theorem inapplicable" in prose yet still
    # claimed non_dcfl 0.75, and the old gate accepted non_dcfl 0.60).
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "infinite",
        "distinguishing_suffix": "w(u, v) = ...",
        "separation_argument": "uw в L, vw не в L",
        "argument": (
            "Мёртвый класс D бесконечен (bbΣ* ⊆ D), теорема 4.7.4 формально "
            "выполняется, но ничего не доказывает; тем не менее заявляем non_dcfl."
        ),
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, GRAMMAR_ASSB_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "refuted"
    assert entry["trust"] == "refuted"
    assert "inapplicable" in entry["issues"][-1]
    assert "not_applicable" in entry["issues"][-1]


def test_verify_shallit_dead_class_status_missing_is_not_verified():
    # VERDICT_POLICY.md §4: nerode_classes REQUIRES dead_class_status --
    # its absence is not_verified, not a silent well_formed pass.
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "distinguishing_suffix": "w(u, v) = ...",
        "separation_argument": "uw в L, vw не в L",
        "argument": "по контрапозиции теоремы 4.7.4 [Sh]",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, GRAMMAR_ASSB_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "not_verified"
    assert "dead_class_status" in " ".join(entry.get("issues", []))


def test_verify_shallit_anbncm_like_mock_still_not_applicable_as_before():
    # {aⁿbⁿcᵐ}-like disjoint-order language (dcfl_anbncm_shallit.json):
    # status == "not_applicable" with proof_sketch == None is a pure
    # pass-through in verify_agent_results, unaffected by the new
    # dead_class_status machinery (it never even reaches _verify_shallit).
    mock = json.loads(
        (EXAMPLES_DIR / "mock" / "dcfl_anbncm_shallit.json").read_text(encoding="utf-8")
    )
    agent_results = {"shallit": mock}
    result = verify_agent_results(agent_results, ANBNCM_TASK_IR_FOR_SHALLIT)
    assert result["shallit"]["verification_status"] == "not_applicable"


# ---------------------------------------------------------------------------
# stack_strategy / dpda  (docs/VERDICT_POLICY.md R2')
# ---------------------------------------------------------------------------

ANBNCM_TASK_IR = json.loads((EXAMPLES_DIR / "task_anbncm.json").read_text(encoding="utf-8"))

# A correct, deterministic DPDA for L = {a^n b^n c^m | n,m>=1}
# (dcfl_system/examples/mock/dcfl_anbncm_stack_strategy.json). q_push/q_pop
# are kept as SEPARATE states (rather than one state handling both 'a'-push
# and 'b'-pop on stack_top='A') so that once any 'b' is read, a later stray
# 'a' has no transition at all and is correctly rejected -- a single shared
# state here would (wrongly) accept interleavings like "aababbc" by reading
# a post-'b' 'a' as "still pushing".
ANBNCM_DPDA = {
    "states": ["q_push", "q_pop", "q_c"],
    "start": "q_push",
    "accept_states": ["q_c"],
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
        {"from": "q_push", "read": "a", "top": "A", "to": "q_push", "push": ["A", "A"]},
        {"from": "q_push", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
    ],
}

# Wrong: drops the |u| == |v| synchronisation, so it accepts e.g. "aabbbcc"
# (n=2, b-count=3) that is NOT in L -- deterministic, but semantically wrong.
ANBNCM_DPDA_WRONG = {
    "states": ["q0", "q_c"],
    "start": "q0",
    "accept_states": ["q_c"],
    "stack_alphabet": ["Z0"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q0", "push": ["Z0"]},
        {"from": "q0", "read": "b", "top": "Z0", "to": "q0", "push": ["Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
    ],
}

# Not deterministic: two transitions for (q_push, top=Z0, read='a').
ANBNCM_DPDA_NONDETERMINISTIC = {
    "states": ["q_push", "q_pop", "q_alt", "q_c"],
    "start": "q_push",
    "accept_states": ["q_c"],
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q_push", "read": "a", "top": "Z0", "to": "q_push", "push": ["A", "Z0"]},
        {"from": "q_push", "read": "a", "top": "Z0", "to": "q_alt", "push": ["Z0"]},
        {"from": "q_push", "read": "a", "top": "A", "to": "q_push", "push": ["A", "A"]},
        {"from": "q_push", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "b", "top": "A", "to": "q_pop", "push": []},
        {"from": "q_pop", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
    ],
}


def test_stack_strategy_without_dpda_stays_well_formed_at_most():
    """Backward compatibility (docs/VERDICT_POLICY.md R2'): a proof_sketch
    with no `dpda` field behaves exactly as before -- structural fields only,
    capped at `well_formed`."""
    proof_sketch = {
        "phases": [
            {"name": "push", "action": "push", "what": "a", "trigger": "reading a"},
        ],
        "separator": "$",
        "determinism_argument": "...",
    }
    result = ov._verify_stack_strategy(proof_sketch, {})
    assert result["verification_status"] == "well_formed"
    assert "dpda_determinism_and_simulation" not in result["checks_run"]


def test_stack_strategy_dpda_nondeterministic_refuted():
    proof_sketch = {"dpda": ANBNCM_DPDA_NONDETERMINISTIC}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_TASK_IR)
    assert result["verification_status"] == "refuted"
    assert result["trust"] == "refuted"
    issues = " ".join(result.get("issues", []))
    assert "not deterministic" in issues


def test_stack_strategy_dpda_correct_simulation_bounded_pass():
    proof_sketch = {"dpda": ANBNCM_DPDA}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_TASK_IR)
    assert result["verification_status"] == "bounded_pass"
    assert result["trust"] == "bounded_pass"
    details = result["details"]
    assert details["determinism"] == "verified"
    assert details["words_checked"] >= 30


def test_stack_strategy_dpda_wrong_language_refuted():
    proof_sketch = {"dpda": ANBNCM_DPDA_WRONG}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_TASK_IR)
    assert result["verification_status"] == "refuted"
    assert result["trust"] == "refuted"
    issues = " ".join(result.get("issues", []))
    assert "disagrees with the task's language oracle" in issues


def test_stack_strategy_dpda_no_oracle_falls_back_to_well_formed():
    """No set_builder membership oracle available for this task (e.g. a
    grammar-format task, or an exponent-notation word_pattern the oracle
    builder can't parse) -> a deterministic dpda still caps at well_formed,
    never silently promoted to bounded_pass."""
    proof_sketch = {"dpda": ANBNCM_DPDA}
    task_ir = {"input_format": "grammar", "language_spec": {}, "alphabet": ["a", "b", "c"]}
    result = ov._verify_stack_strategy(proof_sketch, task_ir)
    assert result["verification_status"] == "well_formed"
    assert result["details"]["determinism"] == "verified"


# ---------------------------------------------------------------------------
# stack_strategy / dpda normalization (docs/VERDICT_POLICY.md R2',
# normalization paragraph; dcfl_system/lib/dpda.normalize_epsilon_accept_sinks)
# ---------------------------------------------------------------------------

# {a^n b^n c^m | n,m >= 0} -- matches the live dcfl-04 task ("{aⁿbⁿcᵐ | n, m
# >= 0}") the normalization paragraph's precedent is drawn from, unlike
# ANBNCM_TASK_IR above (task_anbncm.json, n,m>=1).
ANBNCM_NM_GE_0_TASK_IR = {
    "task_id": "dcfl_anbncm_nm_ge_0",
    "input_format": "set_builder",
    "task_type": "classify_and_prove_dcfl",
    "language_spec": {
        "kind": "set_builder",
        "word_pattern": "uvw",
        "variables": [
            {"name": "u", "domain": "a*", "quantifier": "forall"},
            {"name": "v", "domain": "b*", "quantifier": "forall"},
            {"name": "w", "domain": "c*", "quantifier": "forall"},
        ],
        "constraints": [
            {"kind": "length_cmp", "args": {"left": "u", "op": "==", "right": "v"}},
        ],
    },
    "alphabet": ["a", "b", "c"],
}

# The EXACT `dpda` from the live Haiku result docs/VERDICT_POLICY.md R2' cites
# (live_c5/dcfl04, `specialist_outputs.stack_strategy.proof_sketch.dpda`):
# `q0`/`q_b`/`q_c` all have an epsilon `(state, top=Z0) -> q_accept` (a dead
# accept sink); `q_b` is ALSO reached by a bare pop that can leave `top="A"`,
# but normalization now applies to all three regardless (config-scoped
# acceptance needs no guard on that -- see test_dpda.py's
# `TestNormalizeEpsilonAcceptSinksLiveDcfl04`). The result IS syntactically
# deterministic, but this submitted automaton is still wrong for the
# language: a stray `q0 --b--> q_b` transition with no preceding `a` makes it
# wrongly accept `"b"` -- a defect only simulation against the language
# oracle catches, not the determinism check.
LIVE_DCFL04_ANBNCM_DPDA = {
    "states": ["q0", "q_a", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A", "Z0"]},
        {"from": "q0", "read": "b", "top": "Z0", "to": "q_b", "push": ["Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}

# A hand-corrected variant of the same language, additionally designed so
# every epsilon-into-`q_accept` source state is reached with a single stack
# top -- see test_dpda.py's SAFE_ANBNCM_DPDA docstring for the construction
# (a dedicated "bottom of the a-block" stack symbol so the state that
# continues after the LAST 'a' is popped is never reached with any other
# stack top). That extra care is no longer required for soundness (config-
# scoped `accept_configs` normalizes LIVE_DCFL04_ANBNCM_DPDA's `q_b` just as
# well without it), but this remains a genuinely correct automaton, unlike
# the live one above.
SAFE_ANBNCM_DPDA = {
    "states": ["q0", "q_a", "q_b_mid", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A1", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A1", "Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A1", "to": "q_a", "push": ["A", "A1"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}


def test_stack_strategy_dpda_live_dcfl04_fully_normalizes_but_refuted_by_simulation(monkeypatch):
    """The exact live dcfl-04 `dpda` (docs/VERDICT_POLICY.md R2' precedent):
    `q0`, `q_b` AND `q_c` all normalize now (config-scoped acceptance needs no
    guard on what else `q_b` occurs with -- see `dpda.normalize_epsilon_accept_sinks`),
    so the result IS syntactically deterministic (no more "not deterministic"
    issue) -- but this submitted automaton is still wrong for the language: a
    stray `q0 --b--> q_b` transition with no preceding `a` makes it wrongly
    accept `"b"`. `sample_words` is stubbed to include `"b"` among the
    candidates (the real word_sampler heuristics do not happen to produce
    this exact one-letter counterexample), so the regression is caught by
    SIMULATION against the language oracle, not by the determinism check --
    the overall verdict is `refuted`, but the issue text names the oracle
    disagreement, never "not deterministic"."""
    monkeypatch.setattr(ov, "sample_words", lambda task_ir, count=60, max_len=10: [{"word": "b"}])
    proof_sketch = {"dpda": LIVE_DCFL04_ANBNCM_DPDA}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_NM_GE_0_TASK_IR)
    assert result["verification_status"] == "refuted"
    assert result["trust"] == "refuted"
    issues = " ".join(result.get("issues", []))
    assert "not deterministic" not in issues
    assert "disagrees with the task's language oracle" in issues
    assert "'b'" in issues
    normalization = result["details"]["normalization"]
    assert len(normalization) == 3
    assert any("q0" in n for n in normalization)
    assert any("q_b" in n for n in normalization)
    assert any("q_c" in n for n in normalization)
    assert {("q0", "Z0"), ("q_b", "Z0"), ("q_c", "Z0")} == {
        (c[0], c[1]) for c in result["details"]["accept_configs"]
    }


def test_stack_strategy_dpda_live_dcfl04_refuted_without_sample_words_stub():
    """Backlog review (major): the EXACT live dcfl-04 `dpda`, run through
    the real `_verify_stack_strategy_dpda` with the real `sample_words` (NO
    monkeypatch) -- the sampler's own heuristics do not happen to produce a
    bare "b" or any other word starting with 'b', so this regressed to
    `bounded_pass` (words_checked=60) before the full-alphabet-enumeration
    fix. The full enumeration (added regardless of what the sampler
    produces) must independently catch a word from `b c*` and refute this
    automaton in the real pipeline, not just under a test-only stub."""
    proof_sketch = {"dpda": LIVE_DCFL04_ANBNCM_DPDA}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_NM_GE_0_TASK_IR)
    assert result["verification_status"] == "refuted"
    assert result["trust"] == "refuted"
    issues = " ".join(result.get("issues", []))
    assert "disagrees with the task's language oracle" in issues
    # A witness from b c* (the automaton wrongly accepts a leading 'b' with
    # no preceding 'a').
    assert re.search(r"word='bc*'", issues) is not None


def test_stack_strategy_dpda_fixed_dcfl04_variant_reaches_bounded_pass():
    """The live dcfl-04 `dpda` minus its one stray transition (`q0 --b--> q_b`)
    normalizes completely (all three epsilons removed, `n=0` reachable only
    directly at `(q0, "Z0")`), stays deterministic, AND now agrees with the
    language oracle on the natural `sample_words` candidates -> `bounded_pass`,
    with all three rewrites recorded."""
    fixed_dpda = {
        **LIVE_DCFL04_ANBNCM_DPDA,
        "transitions": [
            t for t in LIVE_DCFL04_ANBNCM_DPDA["transitions"]
            if not (t.get("from") == "q0" and t.get("read") == "b")
        ],
    }
    proof_sketch = {"dpda": fixed_dpda}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_NM_GE_0_TASK_IR)
    assert result["verification_status"] == "bounded_pass"
    assert result["trust"] == "bounded_pass"
    assert result["details"]["determinism"] == "verified"
    assert len(result["details"]["normalization"]) == 3


def test_stack_strategy_dpda_fully_normalizable_reaches_bounded_pass():
    """A submitted `dpda` where every epsilon-into-accept-sink source state
    normalizes (now unconditionally) reaches `bounded_pass` end to end, with
    all three rewrites recorded."""
    proof_sketch = {"dpda": SAFE_ANBNCM_DPDA}
    result = ov._verify_stack_strategy(proof_sketch, ANBNCM_NM_GE_0_TASK_IR)
    assert result["verification_status"] == "bounded_pass"
    assert result["trust"] == "bounded_pass"
    assert result["details"]["determinism"] == "verified"
    assert len(result["details"]["normalization"]) == 3
