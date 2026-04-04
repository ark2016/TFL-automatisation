"""Tests for cfl_system.lib.cfl_ir_schema — CFL IR validation."""

from __future__ import annotations

import json
import os
import copy
import pytest

from cfl_system.lib.cfl_ir_schema import (
    validate_cfl_ir,
    validate_cfl_ir_json,
    CFLIRValidationError,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_example(name: str) -> dict:
    path = os.path.join(EXAMPLES_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _minimal_repeated_subword() -> dict:
    return {
        "task_type": "classify_cfl",
        "source_text": "test repeated subword",
        "language_spec": {
            "kind": "repeated_subword",
            "parts": ["x"],
            "concat_pattern": ["x", "x"],
            "alphabets": {"x": ["a"]},
            "constraints": [],
        },
    }


def _minimal_exists_decomposition() -> dict:
    return {
        "task_type": "prove_cfl",
        "source_text": "test exists decomposition",
        "language_spec": {
            "kind": "exists_decomposition",
            "parts": ["u"],
            "concat_pattern": ["u", "rev(u)"],
            "alphabets": {"u": ["a", "b"]},
            "constraints": [],
        },
    }


def _minimal_grammar_filter() -> dict:
    return {
        "task_type": "grammar_filter_cfl",
        "source_text": "test grammar filter",
        "language_spec": {
            "kind": "grammar_filter",
            "grammar": {
                "kind": "grammar",
                "terminals": ["a"],
                "nonterminals": ["S"],
                "start": "S",
                "rules": [{"lhs": "S", "rhs": ["a"]}],
            },
            "filter": {
                "op": "gt",
                "left": {"kind": "length", "of_var": "w"},
                "right": {"kind": "constant", "value": 0},
            },
        },
    }


# ---------------------------------------------------------------------------
# Task type tests
# ---------------------------------------------------------------------------

class TestTaskTypes:
    @pytest.mark.parametrize("tt", [
        "classify_cfl", "prove_cfl", "prove_non_cfl",
        "classify_and_prove_cfl", "grammar_filter_cfl",
    ])
    def test_valid_cfl_task_types(self, tt):
        ir = {"task_type": tt, "source_text": "some language"}
        assert validate_cfl_ir(ir) == []

    def test_invalid_task_type(self):
        ir = {"task_type": "classify", "source_text": "reg task"}
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "Invalid task_type" in errors[0]

    def test_unknown_task_type(self):
        ir = {"task_type": "bogus", "source_text": "nope"}
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "bogus" in errors[0]

    def test_missing_task_type(self):
        ir = {"source_text": "no task"}
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1

    def test_missing_source_text(self):
        ir = {"task_type": "classify_cfl"}
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "source_text" in errors[0]


# ---------------------------------------------------------------------------
# repeated_subword tests
# ---------------------------------------------------------------------------

class TestRepeatedSubword:
    def test_valid_minimal(self):
        assert validate_cfl_ir(_minimal_repeated_subword()) == []

    def test_missing_parts(self):
        ir = _minimal_repeated_subword()
        del ir["language_spec"]["parts"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "parts" in errors[0]

    def test_missing_concat_pattern(self):
        ir = _minimal_repeated_subword()
        del ir["language_spec"]["concat_pattern"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "concat_pattern" in errors[0]

    def test_missing_alphabets(self):
        ir = _minimal_repeated_subword()
        del ir["language_spec"]["alphabets"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "alphabets" in errors[0]

    def test_concat_pattern_references_unknown_part(self):
        ir = _minimal_repeated_subword()
        ir["language_spec"]["concat_pattern"] = ["x", "y"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "'y'" in errors[0]

    def test_alphabets_keys_mismatch(self):
        ir = _minimal_repeated_subword()
        ir["language_spec"]["alphabets"] = {"x": ["a"], "z": ["b"]}
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "alphabets keys" in errors[0]

    def test_valid_with_constraints(self):
        ir = _minimal_repeated_subword()
        ir["language_spec"]["constraints"] = [
            {"op": "gt", "left": {"kind": "length", "of_var": "x"},
             "right": {"kind": "constant", "value": 0}}
        ]
        assert validate_cfl_ir(ir) == []


# ---------------------------------------------------------------------------
# exists_decomposition tests
# ---------------------------------------------------------------------------

class TestExistsDecomposition:
    def test_valid_minimal(self):
        assert validate_cfl_ir(_minimal_exists_decomposition()) == []

    def test_missing_parts(self):
        ir = _minimal_exists_decomposition()
        del ir["language_spec"]["parts"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1

    def test_rev_unknown_part(self):
        ir = _minimal_exists_decomposition()
        ir["language_spec"]["concat_pattern"] = ["u", "rev(z)"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "rev(z)" in errors[0]

    def test_plain_unknown_part(self):
        ir = _minimal_exists_decomposition()
        ir["language_spec"]["concat_pattern"] = ["u", "q"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "'q'" in errors[0]


# ---------------------------------------------------------------------------
# grammar_filter tests
# ---------------------------------------------------------------------------

class TestGrammarFilter:
    def test_valid_minimal(self):
        assert validate_cfl_ir(_minimal_grammar_filter()) == []

    def test_missing_grammar(self):
        ir = _minimal_grammar_filter()
        del ir["language_spec"]["grammar"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "grammar" in errors[0]

    def test_missing_filter(self):
        ir = _minimal_grammar_filter()
        del ir["language_spec"]["filter"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "filter" in errors[0]

    def test_invalid_grammar_missing_rules(self):
        ir = _minimal_grammar_filter()
        del ir["language_spec"]["grammar"]["rules"]
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "rules" in errors[0]

    def test_natural_language_filter(self):
        ir = _minimal_grammar_filter()
        ir["language_spec"]["filter"] = {
            "natural_language_filter": "words with equal a's and b's"
        }
        assert validate_cfl_ir(ir) == []


# ---------------------------------------------------------------------------
# Unknown language_spec kind
# ---------------------------------------------------------------------------

class TestUnknownKind:
    def test_unknown_lang_kind(self):
        ir = {
            "task_type": "classify_cfl",
            "source_text": "test",
            "language_spec": {"kind": "alien"},
        }
        errors = validate_cfl_ir(ir)
        assert len(errors) == 1
        assert "unknown kind" in errors[0]


# ---------------------------------------------------------------------------
# Base kinds still work via delegation
# ---------------------------------------------------------------------------

class TestBaseKindDelegation:
    def test_predicate_kind_valid(self):
        ir = {
            "task_type": "classify_cfl",
            "source_text": "test",
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b"],
                "variable": "w",
                "predicate": {
                    "op": "gt",
                    "left": {"kind": "length", "of_var": "w"},
                    "right": {"kind": "constant", "value": 0},
                },
            },
        }
        assert validate_cfl_ir(ir) == []

    def test_natural_kind_valid(self):
        ir = {
            "task_type": "classify_cfl",
            "source_text": "test",
            "language_spec": {
                "kind": "natural",
                "description": "all palindromes over {a,b}",
            },
        }
        assert validate_cfl_ir(ir) == []


# ---------------------------------------------------------------------------
# JSON string validation
# ---------------------------------------------------------------------------

class TestJsonValidation:
    def test_invalid_json(self):
        data, errors = validate_cfl_ir_json("{bad json")
        assert data is None
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0]

    def test_valid_json(self):
        ir = {"task_type": "classify_cfl", "source_text": "test"}
        data, errors = validate_cfl_ir_json(json.dumps(ir))
        assert data == ir
        assert errors == []


# ---------------------------------------------------------------------------
# Example file validation
# ---------------------------------------------------------------------------

class TestExampleFiles:
    @pytest.mark.parametrize("filename", [
        "task_w1w2w1w3.json",
        "task_wwvvR.json",
        "task_w0w1w2w1w3.json",
        "task_grammar_filter_49.json",
    ])
    def test_example_valid(self, filename):
        ir = _load_example(filename)
        errors = validate_cfl_ir(ir)
        assert errors == [], f"{filename}: {errors}"

    @pytest.mark.parametrize("filename", [
        "task_w1w2w1w3.json",
        "task_wwvvR.json",
        "task_w0w1w2w1w3.json",
        "task_grammar_filter_49.json",
    ])
    def test_example_json_roundtrip(self, filename):
        path = os.path.join(EXAMPLES_DIR, filename)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        data, errors = validate_cfl_ir_json(raw)
        assert data is not None
        assert errors == [], f"{filename}: {errors}"
