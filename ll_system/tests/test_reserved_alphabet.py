"""Reserved symbols ('$' end-of-input marker, 'ε' empty word) in IR alphabets."""

from __future__ import annotations

import copy

import pytest

from ll_system.lib.ll_ir_schema import validate_ll_ir

_FORMAT1 = {
    "task_type": "ll_check_language",
    "source_text": "L = {a^n b^n}",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b"],
        "variables": [{"name": "n", "domain": {"type": "nat"}}],
        "template": ["aⁿ", "bⁿ"],
        "constraints": [],
    },
}

_FORMAT2 = {
    "task_type": "ll_check_grammar_lang",
    "source_text": "S -> aSb | eps",
    "language_spec": {
        "kind": "grammar",
        "nonterminals": ["S"],
        "terminals": ["a", "b"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    },
}


def _fresh(ir: dict) -> dict:
    return copy.deepcopy(ir)


def test_ordinary_alphabets_valid() -> None:
    assert validate_ll_ir(_fresh(_FORMAT1)) == []
    assert validate_ll_ir(_fresh(_FORMAT2)) == []


@pytest.mark.parametrize("sym", ["$", "ε"])
def test_set_builder_alphabet_rejects_reserved(sym: str) -> None:
    ir = _fresh(_FORMAT1)
    ir["language_spec"]["alphabet"] = ["a", sym]
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "reserved" in errors[0]
    assert sym in errors[0]
    assert "alphabet" in errors[0]


@pytest.mark.parametrize("sym", ["$", "ε"])
def test_predicate_alphabet_rejects_reserved(sym: str) -> None:
    ir = {
        "task_type": "ll_check_grammar_lang",
        "source_text": "test",
        "language_spec": {
            "kind": "predicate",
            "alphabet": ["a", sym],
            "variable": "w",
            "predicate": {},
        },
    }
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "reserved" in errors[0]


def test_variable_domain_alphabet_rejected() -> None:
    ir = _fresh(_FORMAT1)
    ir["language_spec"]["variables"] = [
        {"name": "w", "domain": {"type": "word", "alphabet": ["a", "$"]}},
    ]
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "domain.alphabet" in errors[0]


def test_variable_enum_values_rejected() -> None:
    ir = _fresh(_FORMAT1)
    ir["language_spec"]["variables"] = [
        {"name": "x", "domain": {"type": "enum", "values": ["a", "ε"]}},
    ]
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "domain.values" in errors[0]


def test_grammar_kind_alphabet_field_rejected() -> None:
    ir = _fresh(_FORMAT2)
    ir["language_spec"]["alphabet"] = ["a", "$"]
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "reserved" in errors[0]


def test_format3_grammar_terminals_rejected() -> None:
    ir = {
        "task_type": "ll_check_grammar",
        "source_text": "test",
        "grammar": {
            "nonterminals": ["S"],
            "terminals": ["a", "ε"],
            "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a"]}],
        },
    }
    errors = validate_ll_ir(ir)
    assert len(errors) == 1
    assert "reserved" in errors[0]
