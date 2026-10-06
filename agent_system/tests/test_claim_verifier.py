"""Tests for lib/claim_verifier.py: alphabet taken from the IR (not
hardcoded [ab]), word-bounded markers, and "in L" not matching "in length"
(TODO.md §3)."""

from agent_system.lib.claim_verifier import _extract_claims, verify_claims


def test_extract_claims_uses_given_alphabet_not_hardcoded_ab():
    """A word using a terminal outside {a,b} is only recognised when that
    terminal is actually part of the passed-in alphabet."""
    text = "the word abcabc in L holds"

    assert _extract_claims(text, ["a", "b", "c"]) == [
        {"word": "abcabc", "claimed_in_L": True, "context": "abcabc in L"},
    ]
    # Without 'c' in the alphabet, the word can't even match the regex
    # (previously it was hardcoded to [ab] regardless of what was passed).
    assert _extract_claims(text, ["a", "b"]) == []


def test_in_l_does_not_match_in_length():
    """'in L' must not fire inside unrelated English words like 'length'."""
    text = "the word aabb is discussed in length below"
    assert _extract_claims(text, ["a", "b"]) == []


def test_in_l_does_not_match_inside_within():
    """The marker must be word-bounded on the left too, not just matched as
    a bare substring of a longer word like 'within'."""
    text = "this closure property holds within L for all n"
    assert _extract_claims(text, ["a", "b"]) == []


def test_in_l_matches_concrete_claim():
    text = "the word aabb in L is accepted, but aaab not in L"
    claims = _extract_claims(text, ["a", "b"])
    by_word = {c["word"]: c["claimed_in_L"] for c in claims}
    assert by_word == {"aabb": True, "aaab": False}


def test_verify_claims_alphabet_from_ir_flows_through():
    """verify_claims (the entry point graph.py calls) must pass the IR's
    alphabet down to _extract_claims -- not silently default to a/b."""
    evidence = {
        "pumping": {"evidence": {"proof": "consider the word abcabc in L"}},
    }
    oracle = lambda w: True  # noqa: E731 -- every word accepted for this check

    result_abc = verify_claims(evidence, oracle, alphabet=["a", "b", "c"])
    assert result_abc["total_claims"] == 1

    result_ab = verify_claims(evidence, oracle, alphabet=["a", "b"])
    assert result_ab["total_claims"] == 0


# ---------------------------------------------------------------------------
# verify_claims: trust taxonomy over the disproved/verified breakdown
# (docs/VERDICT_POLICY.md §1: a structural pass over free-text claims is
# well_formed at best, never `verified`; an oracle-contradicted claim is
# `refuted`; no claims at all is `not_verified`).
# ---------------------------------------------------------------------------

def test_verify_claims_disproved_claim_is_refuted_with_error_message():
    evidence = {
        "pumping": {"evidence": {"proof": "the word aabb in L holds"}},
    }
    oracle = lambda w: False  # noqa: E731 -- oracle disagrees with the claim

    result = verify_claims(evidence, oracle, alphabet=["a", "b"])

    assert result["trust"] == "refuted"
    assert result["total_claims"] == 1
    assert result["disproved"] == 1
    assert result["verified"] == 0
    assert any("aabb" in e for e in result["errors"])
    assert result["disproved_claims"][0]["word"] == "aabb"


def test_verify_claims_all_correct_is_well_formed_not_verified_label():
    """Even when every claim checks out, this is still just a structural
    pass over free text -- it must never claim the full `verified` trust
    reserved for deterministic checks (VERDICT_POLICY.md §1)."""
    evidence = {
        "pumping": {"evidence": {"proof": "the word aabb in L holds"}},
    }
    oracle = lambda w: True  # noqa: E731 -- oracle agrees with the claim

    result = verify_claims(evidence, oracle, alphabet=["a", "b"])

    assert result["trust"] == "well_formed"
    assert result["disproved"] == 0
    assert result["verified"] == 1


def test_unknown_membership_cannot_refute_a_free_text_claim():
    evidence = {"pumping": {"evidence": {"proof": "the word aabb in L holds"}}}
    result = verify_claims(evidence, lambda word: None, alphabet=["a", "b"])
    assert result["trust"] == "well_formed"
    assert result["disproved"] == result["verified"] == 0


def test_verify_claims_no_claims_found_is_not_verified():
    evidence = {"pumping": {"evidence": {"proof": "a purely qualitative argument"}}}
    oracle = lambda w: True  # noqa: E731

    result = verify_claims(evidence, oracle, alphabet=["a", "b"])

    assert result["trust"] == "not_verified"
    assert result["total_claims"] == 0


def test_verify_claims_scans_every_known_specialist_agent():
    """Claims are pulled from every specialist output present in evidence
    (pumping, nerode, closure, re_builder, dfa_builder, grammar_analyzer,
    reasoning), tagged with which agent made them -- not just the first one
    found."""
    evidence = {
        "pumping": {"evidence": {"proof": "aabb in L"}},
        "nerode": {"evidence": {"proof": "aaab not in L"}},
    }
    oracle = lambda w: w == "aabb"  # noqa: E731 -- aabb in L, aaab not in L

    result = verify_claims(evidence, oracle, alphabet=["a", "b"])

    assert result["total_claims"] == 2
    assert result["verified"] == 2
    assert result["disproved"] == 0
    agents = {c["agent"] for c in result["verified_claims"]}
    assert agents == {"pumping", "nerode"}
