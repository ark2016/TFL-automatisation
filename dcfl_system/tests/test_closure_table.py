"""Tests for dcfl_system.lib.closure_table module."""

from __future__ import annotations

import json
import pathlib

import pytest

from dcfl_system.lib.closure_table import (
    CLOSURE_TABLE,
    closure_scan,
    get_implication,
    get_proof_direction,
    is_closed,
)

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "examples"

# ---- required keys for every table entry ----
REQUIRED_KEYS = {"operation", "symbol", "closed", "implication_if_closed", "proof_direction"}


# ====================================================================
# 1. Table integrity
# ====================================================================

class TestTableIntegrity:
    def test_table_has_8_entries(self):
        assert len(CLOSURE_TABLE) == 8

    @pytest.mark.parametrize("entry", CLOSURE_TABLE, ids=lambda e: e["operation"])
    def test_entry_has_required_keys(self, entry):
        assert REQUIRED_KEYS <= set(entry.keys())


# ====================================================================
# 2. is_closed — known operations
# ====================================================================

class TestIsClosed:
    @pytest.mark.parametrize(
        "operation, expected",
        [
            ("complement", True),
            ("inverse_homomorphism", True),
            ("reg_intersection", True),
            ("union", False),
            ("concatenation", False),
            ("kleene_star", False),
            ("reversal", False),
            ("homomorphism", False),
        ],
    )
    def test_is_closed(self, operation, expected):
        assert is_closed(operation) is expected


# ====================================================================
# 3. is_closed — unknown operation raises KeyError
# ====================================================================

class TestIsClosedUnknown:
    def test_unknown_operation_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown operation 'bogus'"):
            is_closed("bogus")


# ====================================================================
# 4. get_implication
# ====================================================================

class TestGetImplication:
    @pytest.mark.parametrize(
        "operation",
        ["complement", "inverse_homomorphism", "reg_intersection"],
    )
    def test_closed_ops_return_string(self, operation):
        result = get_implication(operation)
        assert isinstance(result, str) and len(result) > 0

    @pytest.mark.parametrize(
        "operation",
        ["union", "concatenation", "kleene_star", "reversal", "homomorphism"],
    )
    def test_non_closed_ops_return_none(self, operation):
        assert get_implication(operation) is None

    def test_unknown_op_returns_none(self):
        assert get_implication("unknown_op") is None


# ====================================================================
# 5. get_proof_direction
# ====================================================================

class TestGetProofDirection:
    def test_complement_both(self):
        assert get_proof_direction("complement") == "both"

    def test_inverse_homomorphism_constructive(self):
        assert get_proof_direction("inverse_homomorphism") == "constructive"

    def test_reg_intersection_constructive(self):
        assert get_proof_direction("reg_intersection") == "constructive"

    @pytest.mark.parametrize(
        "operation",
        ["union", "concatenation", "kleene_star", "reversal", "homomorphism"],
    )
    def test_non_closed_returns_none(self, operation):
        assert get_proof_direction(operation) is None

    def test_unknown_op_returns_none(self):
        assert get_proof_direction("unknown_op") is None


# ====================================================================
# 6. closure_scan — disjunction triggers warning about union
# ====================================================================

class TestClosureScanDisjunction:
    def test_disjunction_produces_union_warning(self):
        ir = {
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "aⁿ b* (cⁿ|bⁿ) a c*",
                "constraints": [
                    {
                        "kind": "disjunction",
                        "args": {"branches": ["cⁿ", "bⁿ"], "shared_var": "n"},
                    }
                ],
            }
        }
        result = closure_scan(ir)
        assert len(result["warnings"]) >= 1
        assert "union" in result["warnings"][0].lower()


# ====================================================================
# 7. closure_scan — regex-constrained variables → reg_intersection
# ====================================================================

class TestClosureScanRegex:
    def test_regex_constraint_yields_reg_intersection(self):
        ir = {
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "w",
                "constraints": [
                    {"kind": "regex_member", "args": {"var": "w", "regex": "a*b*"}},
                ],
            }
        }
        result = closure_scan(ir)
        ops = [s["operation"] for s in result["applicable_strategies"]]
        assert "reg_intersection" in ops


# ====================================================================
# 8. closure_scan — grammar IR → complement strategy
# ====================================================================

class TestClosureScanGrammar:
    def test_grammar_yields_complement_strategy(self):
        ir = {
            "language_spec": {
                "kind": "grammar",
                "terminals": ["a", "b"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": []}],
            }
        }
        result = closure_scan(ir)
        ops = [s["operation"] for s in result["applicable_strategies"]]
        assert "complement" in ops


# ====================================================================
# 9. closure_scan — result structure
# ====================================================================

class TestClosureScanResultStructure:
    def test_result_has_required_keys(self):
        result = closure_scan({"language_spec": {"kind": "set_builder", "constraints": []}})
        assert "applicable_strategies" in result
        assert "complement_structure" in result
        assert "warnings" in result

    def test_strategies_is_list(self):
        result = closure_scan({"language_spec": {"kind": "set_builder", "constraints": []}})
        assert isinstance(result["applicable_strategies"], list)

    def test_warnings_is_list(self):
        result = closure_scan({"language_spec": {"kind": "set_builder", "constraints": []}})
        assert isinstance(result["warnings"], list)

    def test_empty_ir_returns_empty(self):
        result = closure_scan({})
        assert result["applicable_strategies"] == []
        assert result["complement_structure"] is None
        assert result["warnings"] == []


# ====================================================================
# 10. closure_scan on example task JSON files
# ====================================================================

class TestClosureScanExamples:
    _example_files = sorted(EXAMPLES_DIR.glob("task_*.json"))

    @pytest.fixture(params=_example_files, ids=lambda p: p.stem)
    def example_ir(self, request):
        with open(request.param, encoding="utf-8") as f:
            return json.load(f)

    def test_example_returns_valid_structure(self, example_ir):
        result = closure_scan(example_ir)
        assert isinstance(result, dict)
        assert "applicable_strategies" in result
        assert "complement_structure" in result
        assert "warnings" in result
        assert isinstance(result["applicable_strategies"], list)
        assert isinstance(result["warnings"], list)
        for strategy in result["applicable_strategies"]:
            assert "operation" in strategy
            assert "direction" in strategy
            assert "description" in strategy
            assert "confidence" in strategy
            assert isinstance(strategy["confidence"], float)

    def test_all_four_examples_loaded(self):
        assert len(self._example_files) == 4, (
            f"Expected 4 example JSON files, found {len(self._example_files)}: "
            f"{[p.name for p in self._example_files]}"
        )
