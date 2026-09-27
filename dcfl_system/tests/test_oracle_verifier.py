"""
Tests for the DCFL oracle verifier stub.

Covers verify_agent_results with various agent statuses,
invalid outputs, empty inputs, and mixed scenarios.
"""
from __future__ import annotations

import pytest

from dcfl_system.lib.oracle_verifier import verify_agent_results
from dcfl_system.lib import oracle_verifier as ov


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


def test_pump_outcome_refuted_when_all_of_0_2_3_survive():
    def pump(i):
        return "always-in-L", "always-in-L-too"

    def oracle(word):
        return True

    assert ov._pump_outcome(oracle, pump) == "refuted"


def test_pump_outcome_inconclusive_when_oracle_never_decides():
    def pump(i):
        return f"w{i}", f"z{i}"

    def oracle(word):
        return None

    assert ov._pump_outcome(oracle, pump) == "inconclusive"


def test_check_condition1_refutes_a_trivially_universal_language():
    # oracle accepts every word -> any decomposition survives pumping at
    # i=0,2,3 -> condition (1) must report "refuted" immediately.
    universal_oracle = lambda word: True
    status, msg = ov._check_condition1("aaaa", "b", "c", p=2, oracle=universal_oracle)
    assert status == "refuted"
    assert msg is not None and "condition (1)" in msg


def test_check_condition2_refutes_a_trivially_universal_language():
    universal_oracle = lambda word: True
    status, msg = ov._check_condition2("aaaa", "b", "c", p=2, oracle=universal_oracle)
    assert status == "refuted"
    assert msg is not None and "condition (2)" in msg


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
