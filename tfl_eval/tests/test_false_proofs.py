"""False-proof corpus (tfl_eval/false_proofs): pins how far deliberately
false agent proofs get through each pipeline's own verifiers and verdict
gate today (docs/SEMANTIC_CHECK.md).

These numbers describe current verifier behaviour, not desired behaviour.
A verifier change that catches a false proof should update ``EXPECTED`` and
``EXPECTED_SUMMARY`` (and docs/SEMANTIC_CHECK.md) in the same commit; a
change that lets one through further shows up here as a failure.
"""

from __future__ import annotations

import json

import pytest

from tfl_eval.false_proofs import (
    CASES,
    PIPELINES,
    check_counterexample,
    replay,
    run_cases,
    summarize,
)
from tfl_eval.false_proofs.runner import main

# case id -> (agent trust, gate verdict, gate confidence)
EXPECTED: dict[str, tuple[str, str | None, float]] = {
    "reg-nerode-mod3": ("bounded_pass", "non_regular", 0.85),
    "reg-pumping-fixed-split": ("well_formed", "non_regular", 0.55),
    "reg-regex-finite-anbn": ("bounded_pass", "regular", 0.85),
    "reg-nerode-mod2": ("refuted", None, 0.40),
    "cfl-closure-wrong-intersection": ("bounded_pass", "non_cfl", 0.85),
    "cfl-w1w2w1w3-wrong-intersection": ("bounded_pass", "non_cfl", 0.85),
    "cfl-pumping-fixed-split": ("well_formed", "non_cfl", 0.55),
    "cfl-ogden-invalid-marking": ("well_formed", "non_cfl", 0.55),
    "dcfl-shallit-nerode-even-a": ("bounded_pass", "non_dcfl", 0.85),
    "dcfl-shallit-prefix-continuation": ("bounded_pass", "non_dcfl", 0.85),
    "dcfl-shallit-dead-class-infinite": ("refuted", None, 0.40),
    "dcfl-shallit-wrong-dead-class": ("well_formed", "non_dcfl", 0.55),
    "dcfl-pumping-small-p-closure": ("well_formed", "non_dcfl", 0.55),
    "dcfl-dpda-finite-patch": ("bounded_pass", "dcfl", 0.85),
    "dcfl-dpda-stray-transition": ("refuted", None, 0.40),
    "dcfl-dpda-exam04-missing-rule": ("refuted", None, 0.40),
    "dcfl-closure-union-inverted": ("well_formed", "non_dcfl", 0.55),
    "ll-substitution-concrete-words": ("bounded_pass", "not_ll", 0.85),
    "ll-substitution-template": ("bounded_pass", "not_ll", 0.85),
    "ll-builder-first-follow-conflict": ("refuted", "uncertain", 0.40),
    # R3′ (2026-11-10): an LL(k) claim without its parse table is capped at
    # well_formed (it was bounded_pass on the step-2 sample before).
    "ll-builder-finite-approximation": ("well_formed", "uncertain", 0.40),
    "ll-format3-false-ll1": ("refuted", "uncertain", 0.40),
}

_KEYS = ("cases", "refuted", "well_formed", "bounded_pass", "verified",
         "above_well_formed", "wrong_verdict", "confident_wrong")
EXPECTED_SUMMARY: dict[str, tuple[int, ...]] = {
    #        cases ref  wf  bp  ver  >wf  wrong  conf_wrong
    "reg":  (4,    1,   1,  2,  0,   2,   3,     2),
    "cfl":  (4,    0,   2,  2,  0,   2,   3,     1),
    "dcfl": (9,    3,   3,  3,  0,   3,   6,     3),
    "ll":   (5,    2,   1,  2,  0,   2,   2,     2),
    "all":  (22,   6,   7,  9,  0,   9,   14,    8),
}


@pytest.fixture(scope="module")
def results():
    return {r.case_id: r for r in run_cases()}


def test_corpus_shape():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    assert 15 <= len(CASES) <= 25
    assert {c.pipeline for c in CASES} == set(PIPELINES)
    assert set(ids) == set(EXPECTED)
    for case in CASES:
        assert case.why_false and case.counterexample.get("kind"), case.id
        assert case.claimed_verdict in {
            "reg": ("regular", "non_regular"), "cfl": ("cfl", "non_cfl"),
            "dcfl": ("dcfl", "non_dcfl"), "ll": ("ll", "not_ll"),
        }[case.pipeline], case.id
    # Most cases argue a false verdict; the rest are false proofs of a true one.
    assert sum(1 for c in CASES if not c.verdict_correct) >= 15


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_counterexample_holds(case):
    """Each recorded counterexample is confirmed by the task's own oracle,
    independently of the verifier under test -- the corpus only contains
    proofs that really are false."""
    ok, why = check_counterexample(case)
    assert ok, why


@pytest.mark.parametrize("case_id", sorted(EXPECTED))
def test_case_outcome_pinned(results, case_id):
    trust, verdict, confidence = EXPECTED[case_id]
    r = results[case_id]
    assert (r.agent_trust, r.gate_verdict) == (trust, verdict)
    assert r.gate_confidence == pytest.approx(confidence)


def test_summary_pinned(results):
    summary = summarize(list(results.values()))
    for pipeline, expected in EXPECTED_SUMMARY.items():
        assert tuple(summary[pipeline][k] for k in _KEYS) == expected, pipeline


def test_trust_above_well_formed_is_what_makes_a_false_proof_confident(results):
    """A false verdict reaches confidence >= 0.6 exactly when the false proof
    reached bounded_pass: well_formed is capped at 0.55 by the gate."""
    for r in results.values():
        if r.wrong_verdict:
            assert r.confident_wrong == r.above_well_formed, r.case_id


def test_replay_does_not_mutate_corpus():
    case = next(c for c in CASES if c.id == "dcfl-dpda-finite-patch")
    before = json.dumps([case.ir, case.agents, case.reasoning], sort_keys=True)
    replay(case)
    assert json.dumps([case.ir, case.agents, case.reasoning], sort_keys=True) == before


def test_cli_json(capsys):
    assert main(["--pipeline", "ll", "--json", "--no-counterexamples"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [c["case_id"] for c in payload["cases"]] == [c.id for c in CASES if c.pipeline == "ll"]
    assert payload["summary"]["ll"]["above_well_formed"] == EXPECTED_SUMMARY["ll"][5]
