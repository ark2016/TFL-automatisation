"""Tests for dcfl_system.lib.dcfl_ir_schema validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dcfl_system.lib.dcfl_ir_schema import (
    DCFLIRValidationError,
    validate_dcfl_ir,
    validate_dcfl_ir_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

EXAMPLE_FILES = [
    "task_wvaavRwR.json",
    "task_u1au2_u3au4.json",
    "task_anb_cnbn.json",
    "task_grammar_aSSb.json",
]


def _load_example(name: str) -> dict:
    path = EXAMPLES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _minimal_set_builder_ir() -> dict:
    """Return a minimal valid set_builder IR."""
    return {
        "source_text": "L = {ab | a,b in Sigma}",
        "input_format": "set_builder",
        "task_type": "classify_dcfl",
        "language_spec": {
            "kind": "set_builder",
            "word_pattern": "ab",
            "variables": [
                {"name": "a", "domain": None, "quantifier": "forall"},
            ],
            "constraints": [],
        },
        "alphabet": ["a", "b"],
    }


def _minimal_grammar_ir() -> dict:
    """Return a minimal valid grammar IR."""
    return {
        "source_text": "S -> a",
        "input_format": "grammar",
        "task_type": "prove_dcfl",
        "language_spec": {
            "kind": "grammar",
            "terminals": ["a"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        },
        "alphabet": ["a"],
    }


# ===================================================================
# 1. Valid IRs — example files
# ===================================================================


class TestExampleFilesValid:
    """Each shipped example must pass validation with zero errors."""

    @pytest.mark.parametrize("filename", EXAMPLE_FILES)
    def test_example_validates(self, filename: str) -> None:
        ir = _load_example(filename)
        errors = validate_dcfl_ir(ir)
        assert errors == [], f"{filename}: {errors}"


# ===================================================================
# 2. Missing / invalid top-level fields
# ===================================================================


class TestTopLevelFields:
    def test_missing_source_text(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["source_text"]
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "source_text" in errors[0]

    def test_empty_source_text(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["source_text"] = ""
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "source_text" in errors[0]

    def test_missing_alphabet(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["alphabet"]
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "alphabet" in errors[0]

    def test_empty_alphabet(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["alphabet"] = []
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "alphabet" in errors[0]

    def test_invalid_task_type(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["task_type"] = "bogus_task"
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "task_type" in errors[0]

    def test_invalid_input_format(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["input_format"] = "xml"
        errors = validate_dcfl_ir(ir)
        assert len(errors) == 1
        assert "input_format" in errors[0]

    @pytest.mark.parametrize(
        "task_type",
        ["classify_dcfl", "prove_dcfl", "prove_non_dcfl", "classify_and_prove_dcfl"],
    )
    def test_all_valid_task_types_accepted(self, task_type: str) -> None:
        ir = _minimal_set_builder_ir()
        ir["task_type"] = task_type
        assert validate_dcfl_ir(ir) == []

    @pytest.mark.parametrize("fmt", ["set_builder", "grammar"])
    def test_all_valid_input_formats_accepted(self, fmt: str) -> None:
        if fmt == "grammar":
            ir = _minimal_grammar_ir()
        else:
            ir = _minimal_set_builder_ir()
        ir["input_format"] = fmt
        assert validate_dcfl_ir(ir) == []

    def test_ir_not_a_dict(self) -> None:
        errors = validate_dcfl_ir("not a dict")
        assert len(errors) == 1
        assert "object" in errors[0].lower() or "dict" in errors[0].lower()


# ===================================================================
# 3. Set-builder language_spec validation
# ===================================================================


class TestSetBuilderSpec:
    def test_missing_word_pattern(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["word_pattern"]
        errors = validate_dcfl_ir(ir)
        assert any("word_pattern" in e for e in errors)

    def test_missing_variables(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["variables"]
        errors = validate_dcfl_ir(ir)
        assert any("variables" in e for e in errors)

    def test_variable_missing_name(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["variables"][0]["name"]
        errors = validate_dcfl_ir(ir)
        assert any("name" in e for e in errors)

    def test_variable_empty_name(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["variables"][0]["name"] = ""
        errors = validate_dcfl_ir(ir)
        assert any("name" in e for e in errors)

    def test_variable_bad_quantifier(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["variables"][0]["quantifier"] = "some"
        errors = validate_dcfl_ir(ir)
        assert any("quantifier" in e for e in errors)

    def test_variable_missing_quantifier(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["variables"][0]["quantifier"]
        errors = validate_dcfl_ir(ir)
        assert any("quantifier" in e for e in errors)

    def test_variable_missing_domain(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["variables"][0]["domain"]
        errors = validate_dcfl_ir(ir)
        assert any("domain" in e for e in errors)

    def test_missing_constraints(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]["constraints"]
        errors = validate_dcfl_ir(ir)
        assert any("constraints" in e for e in errors)

    def test_invalid_constraint_missing_kind(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["constraints"] = [{"args": {"a": 1}}]
        errors = validate_dcfl_ir(ir)
        assert any("kind" in e for e in errors)

    def test_invalid_constraint_unknown_kind(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["constraints"] = [
            {"kind": "unknown_kind", "args": {"a": 1}}
        ]
        errors = validate_dcfl_ir(ir)
        assert any("kind" in e for e in errors)

    def test_invalid_constraint_missing_args(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["constraints"] = [{"kind": "length_cmp"}]
        errors = validate_dcfl_ir(ir)
        assert any("args" in e for e in errors)

    @pytest.mark.parametrize(
        "kind",
        ["length_cmp", "regex_member", "equal", "reverse", "integer_cmp", "disjunction"],
    )
    def test_all_valid_constraint_kinds(self, kind: str) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["constraints"] = [{"kind": kind, "args": {"x": 1}}]
        assert validate_dcfl_ir(ir) == []


# ===================================================================
# 4. Grammar language_spec validation
# ===================================================================


class TestGrammarSpec:
    def test_missing_terminals(self) -> None:
        ir = _minimal_grammar_ir()
        del ir["language_spec"]["terminals"]
        errors = validate_dcfl_ir(ir)
        assert any("terminals" in e for e in errors)

    def test_missing_nonterminals(self) -> None:
        ir = _minimal_grammar_ir()
        del ir["language_spec"]["nonterminals"]
        errors = validate_dcfl_ir(ir)
        assert any("nonterminals" in e for e in errors)

    def test_missing_start(self) -> None:
        ir = _minimal_grammar_ir()
        del ir["language_spec"]["start"]
        errors = validate_dcfl_ir(ir)
        assert any("start" in e for e in errors)

    def test_missing_rules(self) -> None:
        ir = _minimal_grammar_ir()
        del ir["language_spec"]["rules"]
        errors = validate_dcfl_ir(ir)
        assert any("rules" in e for e in errors)

    def test_rule_missing_lhs(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["rules"] = [{"rhs": ["a"]}]
        errors = validate_dcfl_ir(ir)
        assert any("lhs" in e for e in errors)

    def test_rule_missing_rhs(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["rules"] = [{"lhs": "S"}]
        errors = validate_dcfl_ir(ir)
        assert any("rhs" in e for e in errors)

    def test_rule_rhs_not_list(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["rules"] = [{"lhs": "S", "rhs": "a"}]
        errors = validate_dcfl_ir(ir)
        assert any("rhs" in e for e in errors)

    def test_rule_rhs_symbol_not_string(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["rules"] = [{"lhs": "S", "rhs": [123]}]
        errors = validate_dcfl_ir(ir)
        assert any("symbol" in e.lower() or "string" in e.lower() for e in errors)

    def test_empty_start(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["start"] = ""
        errors = validate_dcfl_ir(ir)
        assert any("start" in e for e in errors)

    def test_unknown_lang_spec_kind(self) -> None:
        ir = _minimal_grammar_ir()
        ir["language_spec"]["kind"] = "regex"
        errors = validate_dcfl_ir(ir)
        assert any("kind" in e for e in errors)


# ===================================================================
# 5. JSON parsing — validate_dcfl_ir_json
# ===================================================================


class TestJsonParsing:
    def test_valid_json_roundtrip(self) -> None:
        ir = _minimal_set_builder_ir()
        raw = json.dumps(ir)
        data, errors = validate_dcfl_ir_json(raw)
        assert data == ir
        assert errors == []

    def test_invalid_json(self) -> None:
        data, errors = validate_dcfl_ir_json("{not valid json}")
        assert data is None
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0]

    def test_valid_json_with_validation_errors(self) -> None:
        raw = json.dumps({"not": "a valid ir"})
        data, errors = validate_dcfl_ir_json(raw)
        assert data is not None  # JSON parsed OK
        assert len(errors) > 0  # but validation failed

    @pytest.mark.parametrize("filename", EXAMPLE_FILES)
    def test_example_json_roundtrip(self, filename: str) -> None:
        path = EXAMPLES_DIR / filename
        raw = path.read_text(encoding="utf-8")
        data, errors = validate_dcfl_ir_json(raw)
        assert data is not None
        assert errors == [], f"{filename}: {errors}"


# ===================================================================
# 6. Edge cases
# ===================================================================


class TestEdgeCases:
    def test_empty_constraints_list_is_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["constraints"] = []
        assert validate_dcfl_ir(ir) == []

    def test_null_domain_on_variable_is_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["variables"][0]["domain"] = None
        assert validate_dcfl_ir(ir) == []

    def test_extra_fields_tolerated(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["extra_field"] = "should be ignored"
        ir["language_spec"]["extra_nested"] = 42
        assert validate_dcfl_ir(ir) == []

    def test_no_task_type_still_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["task_type"]
        assert validate_dcfl_ir(ir) == []

    def test_no_input_format_still_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["input_format"]
        assert validate_dcfl_ir(ir) == []

    def test_no_language_spec_still_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        del ir["language_spec"]
        assert validate_dcfl_ir(ir) == []

    def test_task_id_must_be_string(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["task_id"] = 123
        errors = validate_dcfl_ir(ir)
        assert any("task_id" in e for e in errors)

    def test_alphabet_symbol_must_be_string(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["alphabet"] = ["a", 7]
        errors = validate_dcfl_ir(ir)
        assert any("alphabet" in e or "symbol" in e.lower() for e in errors)

    def test_dcfl_ir_validation_error_is_exception(self) -> None:
        assert issubclass(DCFLIRValidationError, Exception)

    def test_variable_domain_string_is_valid(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["variables"][0]["domain"] = "a*b"
        assert validate_dcfl_ir(ir) == []

    def test_variable_domain_non_string_non_null_invalid(self) -> None:
        ir = _minimal_set_builder_ir()
        ir["language_spec"]["variables"][0]["domain"] = 42
        errors = validate_dcfl_ir(ir)
        assert any("domain" in e for e in errors)
