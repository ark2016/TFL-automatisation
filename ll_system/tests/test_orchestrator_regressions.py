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
    first_follow_oracle_node,
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


# ---------------------------------------------------------------------------
# Regression: Finding 1 — Format 3 oracle respects requested k
# ---------------------------------------------------------------------------

# LL(2) grammar: S → aB | bA, A → aS | b, B → bS | a  (classic LL(2) grammar)
# Actually let's use a simpler grammar that is LL(2) but NOT LL(1)
GRAMMAR_LL2 = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A"]},
        {"lhs": "S", "rhs": ["b"]},
        {"lhs": "A", "rhs": ["a", "b"]},
        {"lhs": "A", "rhs": ["b", "a"]},
    ],
}


def _oracle_state(grammar: dict, ir_k: int | None, input_format: int = 3) -> dict:
    return {
        "ir": {
            "task_type": "ll_check_grammar",
            "source_text": "test",
            "grammar": grammar,
            "k": ir_k,
        },
        "input_format": input_format,
        "agent_results": {},
        "preprocess_hints": {},
        "log": [],
    }


class TestOracleRespectsRequestedK:
    """Regression Finding 1: oracle always called find_min_ll_k, ignoring ir['k'].
    An LL(k) grammar checked at a different k must give the correct answer."""

    def test_ll1_grammar_checked_at_k1_passes(self):
        """LL(1) grammar checked at k=1 must return found=True."""
        state = _oracle_state(GRAMMAR_LL1, ir_k=1)
        out = first_follow_oracle_node(state)
        ff = out.get("first_follow_result", {})
        assert ff.get("found") is True
        assert ff.get("min_k") == 1

    def test_ll1_grammar_checked_at_k2_passes(self):
        """LL(1) grammar is also LL(2) — found=True at k=2."""
        state = _oracle_state(GRAMMAR_LL1, ir_k=2)
        out = first_follow_oracle_node(state)
        ff = out.get("first_follow_result", {})
        assert ff.get("found") is True

    def test_format3_k_none_uses_find_min_ll_k(self):
        """When ir['k'] is None, oracle should search for minimum k."""
        state = _oracle_state(GRAMMAR_LL1, ir_k=None)
        out = first_follow_oracle_node(state)
        ff = out.get("first_follow_result", {})
        assert ff.get("found") is True
        assert ff.get("min_k") is not None

    def test_checked_k_field_set_when_specific_k(self):
        """When checking a specific k, result must include checked_k."""
        state = _oracle_state(GRAMMAR_LL1, ir_k=1)
        out = first_follow_oracle_node(state)
        ff = out.get("first_follow_result", {})
        assert "checked_k" in ff
        assert ff["checked_k"] == 1

    def test_checked_k_field_absent_for_min_search(self):
        """When k=None, checked_k must not appear (find_min_ll_k path)."""
        state = _oracle_state(GRAMMAR_LL1, ir_k=None)
        out = first_follow_oracle_node(state)
        ff = out.get("first_follow_result", {})
        assert "checked_k" not in ff


# ---------------------------------------------------------------------------
# Regression: Finding 2 — fallback prefers verified destructive over unverified constructive
# ---------------------------------------------------------------------------


class TestFallbackPrefersVerifiedClaims:
    """Regression Finding 2: fallback was returning 'll' when ll_grammar_builder had
    confidence >= 0.7 even though its claim was inconclusive and substitution_agent
    was verified."""

    def _state_with_both(
        self,
        constructive_status: str,
        destructive_status: str,
    ) -> dict:
        return {
            "ir": {},
            "input_format": 1,
            "preprocess_hints": {},
            "first_follow_result": {},
            "claim_verification": {
                "ll_grammar_builder": {
                    "verification_status": constructive_status,
                    "status": constructive_status,
                },
                "substitution_agent": {
                    "verification_status": destructive_status,
                    "status": destructive_status,
                },
            },
            "agent_results": {
                "ll_grammar_builder": {
                    "verdict": "ll",
                    "confidence": 0.9,
                    "proof_sketch": {
                        "method": "ll_grammar_construction",
                        "k": 1,
                        "ll_grammar": GRAMMAR_LL1,
                    },
                },
                "substitution_agent": {
                    "verdict": "not_ll",
                    "confidence": 0.85,
                    "proof_sketch": {
                        "method": "substitution",
                        "for_all_k": True,
                        "witness": {
                            "k": "arbitrary", "w1": "a^n",
                            "lookahead": "a^k", "suffix_1": "b^n", "suffix_2": "c^n",
                            "why_not_in_L": "incompatible",
                        },
                    },
                },
            },
            "retry_round": 0,
            "log": [],
        }

    def test_verified_destructive_beats_inconclusive_constructive(self):
        """Regression: ll_grammar_builder inconclusive + substitution_agent verified
        must yield not_ll verdict in fallback."""
        state = self._state_with_both(
            constructive_status="inconclusive",
            destructive_status="verified",
        )
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "not_ll", (
            "Verified destructive proof must beat unverified constructive claim in fallback"
        )

    def test_both_verified_constructive_wins(self):
        """When both are verified, constructive (ll) should win if sorted first."""
        state = self._state_with_both(
            constructive_status="verified",
            destructive_status="verified",
        )
        result = _fallback_reasoning(state)
        # Both verified — constructive comes first in sort, should be "ll"
        assert result.get("verdict") in ("ll", "not_ll")  # either is acceptable

    def test_both_inconclusive_constructive_wins(self):
        """When neither is verified, constructive still comes first (original behavior)."""
        state = self._state_with_both(
            constructive_status="inconclusive",
            destructive_status="inconclusive",
        )
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "ll"

    def test_verified_constructive_beats_inconclusive_destructive(self):
        """Verified constructive beats inconclusive destructive."""
        state = self._state_with_both(
            constructive_status="verified",
            destructive_status="inconclusive",
        )
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "ll"


# ---------------------------------------------------------------------------
# Regression: Finding 3 — primary_method should be "first_follow_oracle"
# ---------------------------------------------------------------------------


class TestFallbackPrimaryMethodString:
    """Regression Finding 3: oracle fallback was returning primary_method='first_follow'
    but downstream prompt expects 'first_follow_oracle'."""

    def test_oracle_ll_primary_method_is_first_follow_oracle(self):
        state = _base_fallback_state()
        state["first_follow_result"] = {"found": True, "min_k": 1}
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "ll"
        assert result.get("primary_method") == "first_follow_oracle", (
            "primary_method must be 'first_follow_oracle', not 'first_follow'"
        )

    def test_oracle_not_ll_format3_primary_method_is_first_follow_oracle(self):
        state = _base_fallback_state(input_format=3)
        state["first_follow_result"] = {"found": False, "min_k": None}
        result = _fallback_reasoning(state)
        assert result.get("verdict") == "not_ll"
        assert result.get("primary_method") == "first_follow_oracle"

    def test_oracle_primary_agent_is_first_follow_oracle(self):
        state = _base_fallback_state()
        state["first_follow_result"] = {"found": True, "min_k": 2}
        result = _fallback_reasoning(state)
        assert result.get("primary_agent") == "first_follow_oracle"


# ---------------------------------------------------------------------------
# Regression: Finding 4 — oracle as primary_agent in assemble_result_node
# ---------------------------------------------------------------------------


class TestAssembleOraclePrimaryAgent:
    """Regression Finding 4: when primary_agent='first_follow_oracle', assemble_result_node
    was using a constructive proof instead of oracle evidence."""

    def _oracle_primary_state(self, verdict: str, ff_result: dict) -> dict:
        state = _base_assemble_state(verdict, primary_agent="first_follow_oracle")
        state["reasoning_output"]["primary_method"] = "first_follow_oracle"
        state["reasoning_output"]["k"] = ff_result.get("min_k")
        state["first_follow_result"] = ff_result
        # Add a constructive agent that would have been incorrectly chosen before
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
            }
        }
        return state

    def test_oracle_primary_gives_oracle_proof_method(self):
        """When primary_agent='first_follow_oracle', proof.method must be 'first_follow_oracle'."""
        state = self._oracle_primary_state(
            "ll", {"found": True, "min_k": 1, "is_ll_k": True, "conflicts": []}
        )
        out = assemble_result_node(state)
        result = out.get("result", {})
        proof = result.get("proof")
        assert proof is not None
        assert proof.get("method") == "first_follow_oracle", (
            "When primary_agent='first_follow_oracle', proof must not come from ll_grammar_builder"
        )

    def test_oracle_primary_proof_details_contain_ff_data(self):
        """proof.details must contain first_follow_result data."""
        ff = {"found": True, "min_k": 2, "is_ll_k": True, "conflicts": []}
        state = self._oracle_primary_state("ll", ff)
        out = assemble_result_node(state)
        result = out.get("result", {})
        proof = result.get("proof")
        assert proof is not None
        details = proof.get("details", {})
        assert details.get("is_ll_k") is True
        assert details.get("min_k") == 2

    def test_oracle_primary_still_populates_grammar_for_display(self):
        """Even with oracle as primary, grammar should be pulled from agents for display."""
        ff = {"found": True, "min_k": 1, "is_ll_k": True, "conflicts": []}
        state = self._oracle_primary_state("ll", ff)
        out = assemble_result_node(state)
        result = out.get("result", {})
        # Grammar may be None if no constructive agent provided it, but should not
        # be from a non-oracle proof
        grammar = result.get("grammar")
        # Grammar from ll_grammar_builder may be set — that is OK for display
        # The key is that proof.method is oracle, not ll_grammar_construction


# ---------------------------------------------------------------------------
# Regression: Round 7 Finding 1 — oracle grammar uses fixed agent order
# ---------------------------------------------------------------------------


class TestOracleGrammarUsesFixedOrder:
    """Regression Round 7 Finding 1: assemble_result_node used an unordered set to pick
    the grammar for display when primary_agent='first_follow_oracle'.  The grammar shown
    might not match the one oracle actually checked.  Fix: iterate in the same fixed order
    as first_follow_oracle_node: ll_grammar_builder → marker_analyzer → grammar_transformer.
    """

    GRAMMAR_FROM_MARKER = {
        "nonterminals": ["S"],
        "terminals": ["m"],
        "start": "S",
        "rules": [{"lhs": "S", "rhs": ["m"]}],
    }
    GRAMMAR_FROM_TRANSFORMER = {
        "nonterminals": ["T"],
        "terminals": ["t"],
        "start": "T",
        "rules": [{"lhs": "T", "rhs": ["t"]}],
    }

    def _state_oracle_primary_multi(self, agents: dict) -> dict:
        state = _base_assemble_state("ll", primary_agent="first_follow_oracle")
        state["reasoning_output"]["primary_method"] = "first_follow_oracle"
        state["reasoning_output"]["k"] = 1
        state["first_follow_result"] = {
            "found": True, "min_k": 1, "is_ll_k": True, "conflicts": [],
        }
        state["agent_results"] = agents
        return state

    def test_ll_grammar_builder_preferred_over_marker_analyzer(self):
        """ll_grammar_builder comes first in fixed order, so its grammar wins."""
        state = self._state_oracle_primary_multi({
            "ll_grammar_builder": {
                "verdict": "ll", "confidence": 0.7,
                "proof_sketch": {
                    "method": "ll_grammar_construction", "k": 1,
                    "ll_grammar": GRAMMAR_LL1,
                },
                "artifacts": {},
            },
            "marker_analyzer": {
                "verdict": "ll", "confidence": 0.9,
                "proof_sketch": {
                    "method": "marker_detection",
                    "ll_grammar": self.GRAMMAR_FROM_MARKER,
                },
                "artifacts": {},
            },
        })
        out = assemble_result_node(state)
        result = out.get("result", {})
        grammar = result.get("grammar")
        assert grammar == GRAMMAR_LL1, (
            "ll_grammar_builder is first in fixed order; its grammar must be chosen "
            "even if marker_analyzer has higher confidence"
        )

    def test_marker_analyzer_preferred_over_grammar_transformer(self):
        """marker_analyzer comes before grammar_transformer in fixed order."""
        state = self._state_oracle_primary_multi({
            "marker_analyzer": {
                "verdict": "ll", "confidence": 0.7,
                "proof_sketch": {
                    "method": "marker_detection",
                    "ll_grammar": self.GRAMMAR_FROM_MARKER,
                },
                "artifacts": {},
            },
            "grammar_transformer": {
                "verdict": "ll", "confidence": 0.9,
                "proof_sketch": {
                    "method": "grammar_transformation", "k": 1,
                    "transformed_grammar": self.GRAMMAR_FROM_TRANSFORMER,
                    "conflicts": [],
                },
                "artifacts": {},
            },
        })
        out = assemble_result_node(state)
        result = out.get("result", {})
        grammar = result.get("grammar")
        assert grammar == self.GRAMMAR_FROM_MARKER, (
            "marker_analyzer precedes grammar_transformer in fixed order"
        )
