"""Finite pumping experiments cannot establish either universal quantifier."""

from copy import deepcopy

import pytest

from cfl_system.lib.claim_verifier import (
    _check_closure_examples,
    _enumerate_vwx_splits,
    _pump,
    input_grammar_is_cfg,
    verify_pumping_claim,
)
from cfl_system.orchestrator import apply_verdict_gate, assemble_result_node


FINITE_IR = {
    "task_type": "classify_cfl", "source_text": "L = {a^10}",
    "language_spec": {
        "kind": "grammar", "terminals": ["a"], "nonterminals": ["S"],
        "start": "S", "rules": [{"lhs": "S", "rhs": ["a"] * 10}],
    },
}


def evidence(word="a" * 10):
    return {
        "word_chosen": "a^10", "word_instances": {"3": word, "4": word},
        "cases": [{"case": "all", "why_not_in_L": "length changes"}],
        "all_cases_covered": True,
        "marked_positions": {"3": list(range(len(word))), "4": list(range(len(word)))},
    }


@pytest.mark.parametrize("agent", ["pumping_cfl", "ogden"])
def test_finite_language_closes_small_p_but_never_promotes_universal_claim(agent):
    result = verify_pumping_claim(evidence(), FINITE_IR, agent=agent)
    assert result["trust"] == "well_formed"
    assert result["details"]["bounded_check"]["status"] == "closed_for_sampled_p"
    state = {
        "ir": FINITE_IR, "retry_round": 3,
        "reasoning_output": {"action": "done", "verdict": "non_cfl", "confidence": .99},
        "agent_results": {agent: {"status": "success", "verdict": "non_cfl", "evidence": evidence()}},
        "claim_verification": {agent: result},
    }
    gated = apply_verdict_gate(state)
    assert gated["reasoning_output"]["verdict"] == "cfl"
    assert gated["reasoning_output"]["primary_evidence"] == "input_grammar"
    assert gated["verdict_gate"]["proof_verified"] is True
    state.update(gated)
    output = assemble_result_node(state)["result"]
    assert output["grammar"] == FINITE_IR["language_spec"]
    assert output["proof"]["source"] == "input_grammar"
    assert output["verdict"] == "cfl" and output["confidence"] == .98


def test_other_ir_gets_no_cfg_certificate_and_only_structural_confidence():
    ir = {"language_spec": {"kind": "natural", "description": "{a^10}"}}
    result = verify_pumping_claim(evidence(), ir, oracle=lambda w: w == "a" * 10)
    state = {
        "ir": ir, "retry_round": 3,
        "reasoning_output": {"action": "done", "verdict": "non_cfl", "confidence": .99},
        "agent_results": {"pumping_cfl": {"status": "success", "verdict": "non_cfl"}},
        "claim_verification": {"pumping_cfl": result},
    }
    gated = apply_verdict_gate(state)
    assert gated["reasoning_output"]["confidence"] <= .55
    assert gated["verdict_gate"]["proof_verified"] is False


@pytest.mark.parametrize("agent", ["pumping_cfl", "ogden"])
def test_exponent_four_can_break_a_split_that_survives_zero_two_three(agent):
    word = "abcdef"
    forbidden = {_pump(*split, 4) for split in _enumerate_vwx_splits(word, 3)}
    assert word not in forbidden
    ev = evidence(word)
    ev["word_chosen"] = "a^p"
    ev["word_instances"] = {"3": word}
    result = verify_pumping_claim(ev, {}, agent=agent, oracle=lambda w: w not in forbidden)
    assert result["trust"] == "well_formed"
    assert result["details"]["bounded_check"]["unresolved_splits"] > 0


@pytest.mark.parametrize("agent", ["pumping_cfl", "ogden"])
def test_unknown_membership_never_creates_a_refutation_or_negative_witness(agent):
    result = verify_pumping_claim(evidence(), {}, agent=agent, oracle=lambda _w: None)
    assert result["trust"] != "refuted"
    assert result["details"]["destructive_witnesses"] == []
    assert result["details"]["bounded_check"]["unknown_answers"] == 2
    result = verify_pumping_claim(
        evidence(), {}, agent=agent, oracle=lambda w: True if w == "a" * 10 else None,
    )
    assert all(w["expected_in_l"] for w in result["details"]["destructive_witnesses"])
    assert result["details"]["bounded_check"]["status"] == "inconclusive"


def test_too_short_word_cannot_close_pumping_instance():
    result = verify_pumping_claim(evidence("a"), {}, oracle=lambda w: w == "a")
    assert result["details"]["bounded_check"]["p_values"] == []


def test_closure_unknown_answers_do_not_meet_sample_coverage(monkeypatch):
    monkeypatch.setattr("cfl_system.lib.claim_verifier._get_oracle", lambda _ir: lambda _w: None)
    result, witnesses = _check_closure_examples({
        "regular_language_regex": "a*", "intersection_examples": ["a", "aa", "aaa"],
        "intersection_non_examples": ["aaaa", "aaaaa"],
    }, {}, [])
    assert result is None and witnesses == []


@pytest.mark.parametrize("change", [
    {"kind": "grammar_filter"}, {"start": "Z"}, {"terminals": ["S"]},
    {"rules": [{"lhs": ["S"], "rhs": ["a"]}]},
    {"rules": [{"lhs": "S", "rhs": ["Z"]}]},
    {"rules": [{"lhs": "S", "rhs": "a"}]},
])
def test_cfg_certificate_requires_whole_well_formed_input_grammar(change):
    ir = deepcopy(FINITE_IR)
    ir["language_spec"].update(change)
    assert input_grammar_is_cfg(ir) is False


@pytest.mark.parametrize("rules", [[], [{"lhs": "S", "rhs": []}]])
def test_empty_and_epsilon_language_grammars_are_cfl(rules):
    ir = deepcopy(FINITE_IR)
    ir["language_spec"]["rules"] = rules
    result = apply_verdict_gate({"ir": ir})
    assert result["reasoning_output"]["verdict"] == "cfl"
