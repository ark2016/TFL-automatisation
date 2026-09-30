"""Universal claims need more than a finite search with no counterexample."""

from dcfl_system.lib import oracle_verifier as ov


def test_shallit_cannot_call_a_prefix_dead_when_extension_is_beyond_search(monkeypatch):
    # L = {a^n | n >= 20}; every unary word extends into L, so D is empty.
    oracle = lambda word: len(word) >= 20
    monkeypatch.setattr(ov, "build_membership_oracle_from_ir", lambda _ir: oracle)
    result, witness, issues = ov._check_dead_class_finite({"alphabet": ["a"]}, "empty")
    assert oracle("a" * 20)
    assert result is None and witness is None
    assert any("does not prove" in issue for issue in issues)


def test_yu_split_surviving_zero_two_three_can_fail_at_four():
    oracle = lambda word: len(word) != 4
    pump = lambda i: ("a" * i, "a" * i)
    assert all(oracle(pump(i)[0]) for i in (0, 1, 2, 3))
    assert not oracle(pump(4)[0])
    assert ov._pump_outcome(oracle, pump) == "inconclusive"


def test_separating_pair_does_not_discharge_unknown_dead_class(monkeypatch):
    oracle = lambda word: len(word) >= 20
    monkeypatch.setattr(ov, "build_membership_oracle_from_ir", lambda _ir: oracle)
    proof = {
        "dead_class_status": "empty",
        "distinguishing_suffix": "a",
        "representative_pairs": [{"u": "a" * 19, "v": "a" * 18}],
    }
    assert oracle(proof["representative_pairs"][0]["u"] + "a")
    assert not oracle(proof["representative_pairs"][0]["v"] + "a")
    status, _check, issue = ov._semantic_check_shallit_nerode(proof, {"alphabet": ["a"]})
    assert status is None
    assert "does not prove" in issue
