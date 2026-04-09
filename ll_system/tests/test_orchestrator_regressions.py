"""
Regression tests for Findings 1, 3, 4, 5 in the orchestrator live-pipeline.
"""
from __future__ import annotations

import pytest

orchestrator_mod = pytest.importorskip(
    "ll_system.orchestrator", reason="orchestrator not available"
)

from ll_system.orchestrator import (  # noqa: E402
    _fallback_reasoning,
    assemble_result_node,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

GRAMMAR_LL1 = {
    "nonterminals": ["S"],
    "terminals": ["a"],
    "start": "S",
    "rules": [{"lhs": "S", "rhs": ["a"]}],
}

GRAMMAR_LL1_B = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A", "b"]},
        {"lhs": "A", "rhs": []},
    ],
}

IR_FORMAT1 = {
    "task_type": "ll_check_grammar_lang",
    "source_text": "test",
    "language_spec": {"kind": "natural", "description": "test"},
}


def _base_fallback_state(input_format: int = 1) -> dict:
    return {
        "ir": IR_FORMAT1,
        "input_format": input_format,
        "preprocess_hints": {},
        "first_follow_result": {},
        "claim_verification": {},
        "agent_results": {},
        "retry_round": 0,
        "log": [],
    }


def _base_assemble_state(verdict: str, primary_agent: str = "") -> dict:
    return {
        "ir": IR_FORMAT1,
        "input_format": 1,
        "agent_results": {},
        "reasoning_output": {
            "action": "done",
            "verdict": verdict,
            "k": None,
            "confidence": 0.9,
            "summary": "test",
            "primary_agent": primary_agent,
            "primary_method": "substitution",
        },
        "first_follow_result": {},
        "claim_verification": {},
        "preprocess_hints": {},
        "classifier_output": {},
        "errors": [],
        "log": [],
        "retry_round": 0,
    }


# ---------------------------------------------------------------------------
# Regression: Finding 1 — Format 1/2 oracle not_ll is not conclusive
# ---------------------------------------------------------------------------


class TestOracleNotLLForFormat12:
    """Regression Finding 1: _fallback_reasoning was returning not_ll for Format 1/2
    when ff_found=False, even though oracle only tested a candidate grammar."""

    def test_format3_oracle_not_ll_is_conclusive(self):
        """For Format 3 (grammar check), ff_found=False IS conclusive."""
        state = _base_fallback_state(input_format=3)
        state["first_follow_result"] = {"found": False, "min_k": None}
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "not_ll"

    def test_format1_oracle_not_ll_is_not_conclusive(self):
        """Regression: Format 1/2 with ff_found=False must NOT immediately return not_ll."""
        state = _base_fallback_state(input_format=1)
        state["first_follow_result"] = {"found": False, "min_k": None}
        result = _fallback_reasoning(state)
        # Must NOT be not_ll from oracle alone — should continue to agent-based fallback
        assert result.get("verdict") != "not_ll", (
            "Format 1/2 oracle negative result must NOT produce fallback not_ll verdict — "
            "it only means the proposed grammar is not LL, not the language"
        )

    def test_format2_oracle_not_ll_is_not_conclusive(self):
        """Same constraint for Format 2."""
        state = _base_fallback_state(input_format=2)
        state["first_follow_result"] = {"found": False, "min_k": None}
        result = _fallback_reasoning(state)
        assert result.get("verdict") != "not_ll"

    def test_format1_oracle_positive_is_still_conclusive(self):
        """Positive oracle (found=True) IS conclusive for all formats."""
        state = _base_fallback_state(input_format=1)
        state["first_follow_result"] = {"found": True, "min_k": 1}
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "ll"
        assert result.get("k") == 1


# ---------------------------------------------------------------------------
# Regression: Finding 3 — assemble_result_node honors primary_agent
# ---------------------------------------------------------------------------


class TestAssembleResultHonorsPrimaryAgent:
    """Regression Finding 3: proof was selected by first element of set, not primary_agent."""

    def test_ll_proof_uses_primary_agent(self):
        """When primary_agent=marker_analyzer and both constructive agents ran,
        proof must come from marker_analyzer."""
        state = _base_assemble_state("ll", primary_agent="marker_analyzer")
        state["agent_results"] = {
            "ll_grammar_builder": {
                "verdict": "ll",
                "confidence": 0.9,
                "proof_sketch": {
                    "method": "ll_grammar_construction",
                    "k": 1,
                    "ll_grammar": GRAMMAR_LL1,
                },
                "artifacts": {},
            },
            "marker_analyzer": {
                "verdict": "ll",
                "confidence": 0.88,
                "proof_sketch": {
                    "method": "marker_detection",
                    "marker_symbol": "c",
                    "ll_grammar": GRAMMAR_LL1_B,
                },
                "artifacts": {},
            },
        }
        out = assemble_result_node(state)
        result = out.get("result", {})
        proof = result.get("proof")
        assert proof is not None
        assert proof.get("method") == "marker_detection", (
            "proof.method must come from primary_agent=marker_analyzer, not ll_grammar_builder"
        )

    def test_not_ll_proof_uses_primary_destructive_agent(self):
        """When primary_agent=prefix_classes_agent, proof must come from it."""
        state = _base_assemble_state("not_ll", primary_agent="prefix_classes_agent")
        state["reasoning_output"]["primary_method"] = "prefix_classes"
        state["agent_results"] = {
            "substitution_agent": {
                "verdict": "not_ll",
                "confidence": 0.9,
                "proof_sketch": {"method": "substitution", "for_all_k": True, "witness": {}},
                "artifacts": {},
            },
            "prefix_classes_agent": {
                "verdict": "not_ll",
                "confidence": 0.88,
                "proof_sketch": {
                    "method": "prefix_classes",
                    "for_all_k": True,
                    "prefix_family": {"description": "u_n = a^n", "parametrization": "a^n"},
                    "distinguishability_argument": {"why_distinguishable": "different completions"},
                },
                "artifacts": {},
            },
        }
        out = assemble_result_node(state)
        result = out.get("result", {})
        proof = result.get("proof")
        assert proof is not None
        assert proof.get("method") == "prefix_classes", (
            "proof.method must come from primary_agent=prefix_classes_agent"
        )


# ---------------------------------------------------------------------------
# Regression: Finding 4 — _fallback_reasoning fills primary_agent/primary_method
# ---------------------------------------------------------------------------


class TestFallbackHasPrimaryFields:
    """Regression Finding 4: _fallback_reasoning outputs were missing primary_agent
    and primary_method, which formalizer needs."""

    def test_oracle_ll_path_has_primary_fields(self):
        state = _base_fallback_state()
        state["first_follow_result"] = {"found": True, "min_k": 1}
        result = _fallback_reasoning(state)
        assert "primary_agent" in result, "oracle ll path must include primary_agent"
        assert "primary_method" in result, "oracle ll path must include primary_method"

    def test_oracle_not_ll_format3_has_primary_fields(self):
        state = _base_fallback_state(input_format=3)
        state["first_follow_result"] = {"found": False, "min_k": None}
        result = _fallback_reasoning(state)
        assert "primary_agent" in result
        assert "primary_method" in result

    def test_constructive_agent_path_has_primary_fields(self):
        state = _base_fallback_state()
        state["agent_results"] = {
            "ll_grammar_builder": {
                "verdict": "ll",
                "confidence": 0.9,
                "proof_sketch": {"method": "ll_grammar_construction", "k": 1, "ll_grammar": GRAMMAR_LL1},
            }
        }
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "ll"
        assert "primary_agent" in result
        assert result["primary_agent"] == "ll_grammar_builder"
        assert "primary_method" in result

    def test_destructive_agent_path_has_primary_fields(self):
        state = _base_fallback_state()
        state["agent_results"] = {
            "substitution_agent": {
                "verdict": "not_ll",
                "confidence": 0.9,
                "proof_sketch": {"method": "substitution"},
            }
        }
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "not_ll"
        assert "primary_agent" in result
        assert result["primary_agent"] == "substitution_agent"
        assert "primary_method" in result
        assert result["primary_method"] == "substitution"

    def test_uncertain_terminal_has_primary_fields(self):
        state = _base_fallback_state()
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "uncertain" or result.get("action") in ("done", "retry")
        if result.get("verdict") == "uncertain":
            assert "primary_agent" in result
            assert "primary_method" in result


# ---------------------------------------------------------------------------
# Regression: Finding 5 — transformed_grammar recognized by oracle and assembly
# ---------------------------------------------------------------------------


class TestTransformedGrammarRecognized:
    """Regression Finding 5: grammar_transformer's proof_sketch.transformed_grammar
    was not found by oracle node or result assembly."""

    def test_assemble_result_uses_transformed_grammar(self):
        """When grammar_transformer provides only transformed_grammar (not grammar/ll_grammar),
        result.grammar must still be populated."""
        state = _base_assemble_state("ll", primary_agent="grammar_transformer")
        state["agent_results"] = {
            "grammar_transformer": {
                "verdict": "ll",
                "confidence": 0.85,
                "proof_sketch": {
                    "method": "grammar_transformation",
                    "k": 1,
                    "transformed_grammar": GRAMMAR_LL1,   # prompt field name
                    "transformation_log": [
                        {"step": "eliminate_left_recursion", "explanation": "applied"}
                    ],
                    "conflicts": [],
                },
                "artifacts": {},  # no ll_grammar in artifacts
            }
        }
        out = assemble_result_node(state)
        result = out.get("result", {})
        grammar = result.get("grammar")
        assert grammar is not None, (
            "result.grammar must be populated from proof_sketch.transformed_grammar"
        )
        assert grammar == GRAMMAR_LL1

    def test_assemble_result_proof_has_correct_method(self):
        """proof.method must be grammar_transformation when transformer is primary."""
        state = _base_assemble_state("ll", primary_agent="grammar_transformer")
        state["agent_results"] = {
            "grammar_transformer": {
                "verdict": "ll",
                "confidence": 0.85,
                "proof_sketch": {
                    "method": "grammar_transformation",
                    "k": 1,
                    "transformed_grammar": GRAMMAR_LL1,
                    "conflicts": [],
                },
                "artifacts": {},
            }
        }
        out = assemble_result_node(state)
        result = out.get("result", {})
        proof = result.get("proof")
        assert proof is not None
        assert proof.get("method") == "grammar_transformation"
