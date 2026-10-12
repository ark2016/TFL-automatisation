"""The reported grammar must be the artifact checked by the LL oracle."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ll_system.orchestrator import assemble_result_node, run_pipeline

LL_GRAMMAR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}
REJECTED_GRAMMAR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["S", "a"]},
        {"lhs": "S", "rhs": ["b"]},
    ],
}


class _Runner:
    def __init__(self, outputs: dict):
        self.outputs = outputs

    def run_agent(self, agent_name: str, input_data: dict | None = None) -> dict | None:
        return deepcopy(self.outputs.get(agent_name))


def _builder_output(grammar: dict) -> dict:
    return {
        "status": "success",
        "verdict": "ll",
        "confidence": 0.99,
        "proof_sketch": {
            "method": "ll_grammar_construction",
            "k": 1,
            "grammar": grammar,
        },
    }


def _ir(grammar: dict) -> dict:
    return {
        "task_type": "ll_check_grammar_lang",
        "source_text": "Does the grammar's language have the LL property?",
        "language_spec": {"kind": "grammar", **grammar},
    }


@pytest.mark.parametrize("with_rejected_candidate", [False, True])
def test_given_grammar_is_preserved_in_pipeline_and_exports(tmp_path, with_rejected_candidate):
    ir = _ir(LL_GRAMMAR)
    outputs = {"ll_grammar_builder": _builder_output(REJECTED_GRAMMAR)} if with_rejected_candidate else {}
    result = run_pipeline(
        ir, mock_runner=_Runner(outputs), output_dir=str(tmp_path), task_name="given",
    )

    assert result["verdict"] == "ll"
    assert result["first_follow_result"]["grammar_source"] == "given_grammar"
    if with_rejected_candidate:
        assert result["claim_verification"]["ll_grammar_builder"]["trust"] == "refuted"
    assert result["grammar"] == ir["language_spec"]
    exported = json.loads((tmp_path / "given.json").read_text(encoding="utf-8"))
    assert exported["grammar"] == ir["language_spec"]
    assert "S → a S b | ε" in (tmp_path / "given.md").read_text(encoding="utf-8")
    assert "S → a S b | ε" in (tmp_path / "given.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("source", ["marker_analyzer", "grammar_transformer"])
def test_pipeline_reports_later_candidate_selected_by_oracle(source):
    given = deepcopy(LL_GRAMMAR)
    given["rules"].insert(0, {"lhs": "S", "rhs": ["S"]})
    if source == "marker_analyzer":
        selected = {
            "status": "success",
            "verdict": "ll",
            "proof_sketch": {
                "method": "marker_detection", "k": 1,
                "marker": "b", "explanation": "b closes each nested pair",
            },
            "artifacts": {"ll_grammar": LL_GRAMMAR},
            "grammar": REJECTED_GRAMMAR,
        }
    else:
        selected = {
            "status": "success",
            "verdict": "ll",
            "proof_sketch": {
                "method": "grammar_transformation", "k": 1,
                "transformed_grammar": LL_GRAMMAR,
                "transformation_steps": ["Remove the redundant S -> S rule"],
                "conflicts_remaining": [],
            },
        }
    result = run_pipeline(
        _ir(given),
        mock_runner=_Runner({
            "ll_grammar_builder": _builder_output(REJECTED_GRAMMAR),
            source: selected,
        }),
    )

    assert result["verdict"] == "ll"
    assert result["first_follow_result"]["grammar_source"] == source
    assert result["claim_verification"]["ll_grammar_builder"]["trust"] == "refuted"
    assert result["claim_verification"][source]["trust"] == "bounded_pass"
    assert result["grammar"] == LL_GRAMMAR


@pytest.mark.parametrize("source", [None, "missing_agent", "marker_analyzer"])
def test_missing_oracle_source_artifact_does_not_substitute_another_grammar(source):
    result = assemble_result_node({
        "ir": _ir(LL_GRAMMAR),
        "input_format": 2,
        "reasoning_output": {
            "verdict": "ll", "confidence": 0.9,
            "primary_agent": "first_follow_oracle",
        },
        "first_follow_result": {"found": True, "min_k": 1, "grammar_source": source},
        "agent_results": {"ll_grammar_builder": _builder_output(REJECTED_GRAMMAR)},
    })["result"]

    assert result["grammar"] is None
