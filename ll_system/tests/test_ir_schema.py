"""Tests for ll_ir_schema and utils."""

from __future__ import annotations

import copy
import json
import os

import pytest

from ll_system.lib.ll_ir_schema import (
    LLIRValidationError,
    validate_ll_ir,
    validate_ll_ir_json,
)
from ll_system.lib.utils import (
    grammar_nonterminals,
    grammar_terminals,
    is_epsilon_rhs,
    normalize_grammar,
    rules_for,
    validate_grammar_symbols,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

GRAMMAR_SIMPLE = {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "A", "b"]},
        {"lhs": "A", "rhs": ["a", "A"]},
        {"lhs": "A", "rhs": []},
    ],
}

FORMAT3_IR = {
    "task_type": "ll_check_grammar",
    "source_text": "Check if grammar is LL(1)",
    "grammar": GRAMMAR_SIMPLE,
    "question": "is_ll_k",
    "k": None,
}

FORMAT1_IR = {
    "task_type": "ll_check_language",
    "source_text": "L = {aⁿbⁿ | n≥0}. Is this language LL?",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [
            {"name": "n", "domain": {"type": "nat"}},
        ],
        "template": ["aⁿ", "bⁿ"],
        "constraints": [],
    },
    "question": "is_ll",
}

FORMAT2_IR = {
    "task_type": "ll_check_grammar_lang",
    "source_text": "S → SabS | Sc | ε. Is the language LL(k)?",
    "language_spec": {
        "kind": "grammar",
        "nonterminals": ["S"],
        "terminals": ["a", "b", "c"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["S", "a", "b", "S"]},
            {"lhs": "S", "rhs": ["S", "c"]},
            {"lhs": "S", "rhs": []},
        ],
    },
    "question": "is_ll_language",
}


def _deep(d: dict) -> dict:
    """Deep copy a dict to avoid mutation between tests."""
    return copy.deepcopy(d)


# ---------------------------------------------------------------------------
# Task type validation
# ---------------------------------------------------------------------------


class TestTaskTypes:
    @pytest.mark.parametrize(
        "task_type",
        ["ll_check_language", "ll_check_grammar_lang", "ll_check_grammar"],
    )
    def test_valid_ll_task_types(self, task_type: str) -> None:
        if task_type == "ll_check_grammar":
            ir = {
                "task_type": task_type,
                "source_text": "test",
                "grammar": GRAMMAR_SIMPLE,
            }
        else:
            ir = {
                "task_type": task_type,
                "source_text": "test",
                "language_spec": {
                    "kind": "natural",
                    "description": "some language",
                },
            }
        assert validate_ll_ir(ir) == []

    def test_invalid_task_type(self) -> None:
        ir = {"task_type": "classify_cfl", "source_text": "test"}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "Invalid task_type" in errors[0]

    def test_unknown_task_type(self) -> None:
        ir = {"task_type": "bogus_type", "source_text": "test"}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "bogus_type" in errors[0]

    def test_missing_task_type(self) -> None:
        ir = {"source_text": "no task type"}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "task_type" in errors[0]

    def test_missing_source_text(self) -> None:
        ir = {"task_type": "ll_check_grammar", "grammar": GRAMMAR_SIMPLE}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "source_text" in errors[0]

    def test_empty_source_text(self) -> None:
        ir = {
            "task_type": "ll_check_grammar",
            "source_text": "",
            "grammar": GRAMMAR_SIMPLE,
        }
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "source_text" in errors[0]

    def test_ir_must_be_dict(self) -> None:
        errors = validate_ll_ir(["not", "a", "dict"])
        assert len(errors) == 1
        assert "JSON object" in errors[0]


# ---------------------------------------------------------------------------
# Format 3: ll_check_grammar
# ---------------------------------------------------------------------------


class TestFormat3LLCheckGrammar:
    def test_valid_format3(self) -> None:
        assert validate_ll_ir(_deep(FORMAT3_IR)) == []

    def test_missing_grammar_field(self) -> None:
        ir = _deep(FORMAT3_IR)
        del ir["grammar"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "grammar" in errors[0]

    def test_grammar_missing_nonterminals(self) -> None:
        ir = _deep(FORMAT3_IR)
        del ir["grammar"]["nonterminals"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "nonterminals" in errors[0]

    def test_grammar_missing_terminals(self) -> None:
        ir = _deep(FORMAT3_IR)
        del ir["grammar"]["terminals"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "terminals" in errors[0]

    def test_grammar_missing_start(self) -> None:
        ir = _deep(FORMAT3_IR)
        del ir["grammar"]["start"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "start" in errors[0]

    def test_grammar_missing_rules(self) -> None:
        ir = _deep(FORMAT3_IR)
        del ir["grammar"]["rules"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "rules" in errors[0]

    def test_grammar_rule_missing_lhs(self) -> None:
        ir = _deep(FORMAT3_IR)
        ir["grammar"]["rules"][0] = {"rhs": ["a"]}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "lhs" in errors[0]

    def test_grammar_rule_missing_rhs(self) -> None:
        ir = _deep(FORMAT3_IR)
        ir["grammar"]["rules"][0] = {"lhs": "S"}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "rhs" in errors[0]

    def test_grammar_epsilon_rhs_valid(self) -> None:
        ir = _deep(FORMAT3_IR)
        # Epsilon as [] is valid
        ir["grammar"]["rules"].append({"lhs": "S", "rhs": []})
        assert validate_ll_ir(ir) == []

    def test_grammar_not_object(self) -> None:
        ir = _deep(FORMAT3_IR)
        ir["grammar"] = "not a dict"
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "grammar must be an object" in errors[0]


# ---------------------------------------------------------------------------
# Format 1: ll_check_language
# ---------------------------------------------------------------------------


class TestFormat1LLCheckLanguage:
    def test_valid_format1(self) -> None:
        assert validate_ll_ir(_deep(FORMAT1_IR)) == []

    def test_missing_language_spec(self) -> None:
        ir = {
            "task_type": "ll_check_language",
            "source_text": "test",
        }
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "language_spec" in errors[0]

    def test_set_builder_missing_alphabet(self) -> None:
        ir = _deep(FORMAT1_IR)
        del ir["language_spec"]["alphabet"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "alphabet" in errors[0]

    def test_set_builder_missing_variables(self) -> None:
        ir = _deep(FORMAT1_IR)
        del ir["language_spec"]["variables"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "variables" in errors[0]

    def test_set_builder_variable_missing_name(self) -> None:
        ir = _deep(FORMAT1_IR)
        ir["language_spec"]["variables"] = [{"domain": {"type": "nat"}}]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "name" in errors[0]

    def test_set_builder_variable_missing_domain(self) -> None:
        ir = _deep(FORMAT1_IR)
        ir["language_spec"]["variables"] = [{"name": "n"}]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "domain" in errors[0]

    def test_set_builder_missing_template(self) -> None:
        ir = _deep(FORMAT1_IR)
        del ir["language_spec"]["template"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "template" in errors[0]

    def test_set_builder_constraints_optional(self) -> None:
        ir = _deep(FORMAT1_IR)
        del ir["language_spec"]["constraints"]
        assert validate_ll_ir(ir) == []

    def test_unknown_language_spec_kind(self) -> None:
        ir = _deep(FORMAT1_IR)
        ir["language_spec"] = {"kind": "alien_kind"}
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "unknown kind" in errors[0]


# ---------------------------------------------------------------------------
# Format 2: ll_check_grammar_lang
# ---------------------------------------------------------------------------


class TestFormat2LLCheckGrammarLang:
    def test_valid_format2(self) -> None:
        assert validate_ll_ir(_deep(FORMAT2_IR)) == []

    def test_grammar_kind_missing_rules(self) -> None:
        ir = _deep(FORMAT2_IR)
        del ir["language_spec"]["rules"]
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "rules" in errors[0]

    def test_natural_kind_valid(self) -> None:
        ir = {
            "task_type": "ll_check_grammar_lang",
            "source_text": "natural language description",
            "language_spec": {
                "kind": "natural",
                "description": "all palindromes over {a, b}",
            },
        }
        assert validate_ll_ir(ir) == []

    def test_natural_kind_missing_description(self) -> None:
        ir = {
            "task_type": "ll_check_grammar_lang",
            "source_text": "test",
            "language_spec": {"kind": "natural"},
        }
        errors = validate_ll_ir(ir)
        assert len(errors) == 1
        assert "description" in errors[0]


# ---------------------------------------------------------------------------
# JSON string validation
# ---------------------------------------------------------------------------


class TestJsonValidation:
    def test_invalid_json_string(self) -> None:
        data, errors = validate_ll_ir_json("{bad json}")
        assert data is None
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0]

    def test_valid_json_string(self) -> None:
        ir = {"task_type": "ll_check_grammar", "source_text": "test", "grammar": GRAMMAR_SIMPLE}
        data, errors = validate_ll_ir_json(json.dumps(ir))
        assert data is not None
        assert errors == []

    def test_empty_json_string(self) -> None:
        data, errors = validate_ll_ir_json("")
        assert data is None
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0]


# ---------------------------------------------------------------------------
# Example file validation
# ---------------------------------------------------------------------------


class TestExampleFiles:
    @pytest.mark.parametrize(
        "filename",
        [
            "format1_anbn_union_ancn.json",
            "format2_SabS_Sc_eps.json",
            "format3_simple_ll1.json",
        ],
    )
    def test_example_valid(self, filename: str) -> None:
        path = os.path.join(EXAMPLES_DIR, filename)
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        errors = validate_ll_ir(ir)
        assert errors == [], f"{filename}: {errors}"

    @pytest.mark.parametrize(
        "filename",
        [
            "format1_anbn_union_ancn.json",
            "format2_SabS_Sc_eps.json",
            "format3_simple_ll1.json",
        ],
    )
    def test_example_json_roundtrip(self, filename: str) -> None:
        path = os.path.join(EXAMPLES_DIR, filename)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        data, errors = validate_ll_ir_json(raw)
        assert data is not None
        assert errors == [], f"{filename}: {errors}"


# ---------------------------------------------------------------------------
# Utils: normalize_grammar
# ---------------------------------------------------------------------------


class TestNormalizeGrammar:
    def test_epsilon_symbol_normalized(self) -> None:
        grammar = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a"]},
                {"lhs": "S", "rhs": ["ε"]},
            ],
        }
        result = normalize_grammar(grammar)
        assert result["rules"][1]["rhs"] == []

    def test_empty_rhs_unchanged(self) -> None:
        grammar = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": []}],
        }
        result = normalize_grammar(grammar)
        assert result["rules"][0]["rhs"] == []

    def test_normal_rules_unchanged(self) -> None:
        result = normalize_grammar(GRAMMAR_SIMPLE)
        assert result["rules"][0]["rhs"] == ["a", "A", "b"]

    def test_does_not_mutate_input(self) -> None:
        grammar = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["ε"]}],
        }
        original_rhs = grammar["rules"][0]["rhs"]
        normalize_grammar(grammar)
        assert grammar["rules"][0]["rhs"] == ["ε"]  # unchanged


# ---------------------------------------------------------------------------
# Utils: rules_for
# ---------------------------------------------------------------------------


class TestRulesFor:
    def test_returns_alternatives_for_S(self) -> None:
        result = rules_for(GRAMMAR_SIMPLE, "S")
        assert result == [["a", "A", "b"]]

    def test_returns_alternatives_for_A(self) -> None:
        result = rules_for(GRAMMAR_SIMPLE, "A")
        assert [["a", "A"], []] == result

    def test_unknown_nonterminal_returns_empty(self) -> None:
        result = rules_for(GRAMMAR_SIMPLE, "X")
        assert result == []

    def test_normalizes_epsilon_on_the_fly(self) -> None:
        grammar = {
            "nonterminals": ["S"],
            "terminals": ["a"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["ε"]}],
        }
        result = rules_for(grammar, "S")
        assert result == [[]]


# ---------------------------------------------------------------------------
# Utils: is_epsilon_rhs
# ---------------------------------------------------------------------------


class TestIsEpsilonRhs:
    def test_empty_list_is_epsilon(self) -> None:
        assert is_epsilon_rhs([]) is True

    def test_epsilon_symbol_is_epsilon(self) -> None:
        assert is_epsilon_rhs(["ε"]) is True

    def test_nonempty_rhs_is_not_epsilon(self) -> None:
        assert is_epsilon_rhs(["a"]) is False

    def test_multi_symbol_rhs_is_not_epsilon(self) -> None:
        assert is_epsilon_rhs(["a", "b"]) is False


# ---------------------------------------------------------------------------
# Utils: grammar_nonterminals / grammar_terminals
# ---------------------------------------------------------------------------


class TestGrammarSymbolSets:
    def test_nonterminals(self) -> None:
        assert grammar_nonterminals(GRAMMAR_SIMPLE) == {"S", "A"}

    def test_terminals(self) -> None:
        assert grammar_terminals(GRAMMAR_SIMPLE) == {"a", "b"}


# ---------------------------------------------------------------------------
# Utils: validate_grammar_symbols
# ---------------------------------------------------------------------------


class TestValidateGrammarSymbols:
    def test_valid_grammar(self) -> None:
        assert validate_grammar_symbols(GRAMMAR_SIMPLE) == []

    def test_start_not_in_nonterminals(self) -> None:
        grammar = {**GRAMMAR_SIMPLE, "start": "Z"}
        errors = validate_grammar_symbols(grammar)
        assert len(errors) == 1
        assert "'Z'" in errors[0]

    def test_symbol_overlap(self) -> None:
        grammar = {
            "nonterminals": ["S", "a"],
            "terminals": ["a", "b"],
            "start": "S",
            "rules": [],
        }
        errors = validate_grammar_symbols(grammar)
        assert len(errors) == 1
        assert "both" in errors[0]
