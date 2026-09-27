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
