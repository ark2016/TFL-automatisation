"""
IR Schema definition and validation for TFL Agent System.

Defines the JSON Schema for the Intermediate Representation (IR)
and provides validation functions per §3 of the spec.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# JSON Schema (§3)
# ---------------------------------------------------------------------------

EXPR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["kind", "symbol", "in_var"],
            "properties": {
                "kind": {"const": "count_symbol"},
                "symbol": {"type": "string", "minLength": 1},
                "in_var": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["kind", "subword", "in_var"],
            "properties": {
                "kind": {"const": "count_subword"},
                "subword": {"type": "string", "minLength": 1},
                "in_var": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["kind", "of_var"],
            "properties": {
                "kind": {"const": "length"},
                "of_var": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["kind", "value"],
            "properties": {
                "kind": {"const": "constant"},
                "value": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    ]
}

PREDICATE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["op", "operands"],
            "properties": {
                "op": {"enum": ["and", "or", "not"]},
                "operands": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Predicate"},
                },
            },
        },
        {
            "type": "object",
            "required": ["op", "left", "right"],
            "properties": {
                "op": {"enum": ["eq", "neq", "lt", "leq", "gt", "geq"]},
                "left": {"$ref": "#/$defs/Expr"},
                "right": {"$ref": "#/$defs/Expr"},
            },
        },
        {
            "type": "object",
            "required": ["expr", "modulus", "remainder"],
            "properties": {
                "expr": {"$ref": "#/$defs/Expr"},
                "modulus": {"type": "integer", "minimum": 2},
                "remainder": {"type": "integer", "minimum": 0},
            },
        },
        {
            "type": "object",
            "required": ["parts", "concat_pattern", "constraints"],
            "properties": {
                "parts": {"type": "array", "items": {"type": "string"}},
                "concat_pattern": {"type": "array", "items": {"type": "string"}},
                "constraints": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Predicate"},
                },
            },
        },
        {
            "type": "object",
            "required": ["op", "substring_expr", "in_var"],
            "properties": {
                "op": {"enum": ["is_substring", "is_not_substring"]},
                "substring_expr": {"type": "string"},
                "in_var": {"type": "string"},
            },
        },
        {
            "type": "object",
            "required": ["op", "prefix_expr", "of_var"],
            "properties": {
                "op": {"enum": ["starts_with", "not_starts_with"]},
                "prefix_expr": {"type": "string"},
                "of_var": {"type": "string"},
            },
        },
        {
            "type": "object",
            "required": ["op", "var"],
            "properties": {
                "op": {"enum": ["is_palindrome", "is_not_palindrome"]},
                "var": {"type": "string"},
            },
        },
    ]
}

LANGUAGE_SPEC_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["kind", "alphabet", "variable", "predicate"],
            "properties": {
                "kind": {"const": "predicate"},
                "alphabet": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "variable": {"type": "string"},
                "predicate": {"$ref": "#/$defs/Predicate"},
            },
        },
        {
            "type": "object",
            "required": ["kind", "terminals", "nonterminals", "start", "rules"],
            "properties": {
                "kind": {"const": "grammar"},
                "terminals": {"type": "array", "items": {"type": "string"}},
                "nonterminals": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "string"},
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["lhs", "rhs"],
                        "properties": {
                            "lhs": {"type": "string"},
                            "rhs": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        {
            "type": "object",
            "required": ["kind", "pattern", "has_backreferences"],
            "properties": {
                "kind": {"const": "regex"},
                "alphabet": {"type": "array", "items": {"type": "string"}},
                "pattern": {"type": "string"},
                "has_backreferences": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "required": ["kind", "description"],
            "properties": {
                "kind": {"const": "natural"},
                "description": {"type": "string"},
            },
        },
        {
            "type": "object",
            "required": ["kind", "symbol", "index_function"],
            "properties": {
                "kind": {"const": "arithmetic_index"},
                "symbol": {"type": "string"},
                "index_function": {"type": "string"},
            },
        },
    ]
}

IR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["task_type", "source_text"],
    "properties": {
        "task_type": {
            "enum": [
                "classify",
                "prove_regular",
                "prove_non_regular",
                "classify_and_prove",
                "compute_pumping_length",
                "build_dfa",
                "build_complement",
                "build_regex",
                "parametric_analysis",
            ]
        },
        "source_text": {"type": "string", "minLength": 1},
        "language_spec": {"$ref": "#/$defs/LanguageSpec"},
        "student_notes": {
            "type": "string",
            "description": "Optional student comments, ideas, or partial solutions to guide the agents",
        },
    },
    "$defs": {
        "LanguageSpec": LANGUAGE_SPEC_SCHEMA,
        "Predicate": PREDICATE_SCHEMA,
        "Expr": EXPR_SCHEMA,
    },
}

# ---------------------------------------------------------------------------
# Validation (pure, no external deps beyond stdlib)
# ---------------------------------------------------------------------------

_VALID_TASK_TYPES = {
    "classify",
    "prove_regular",
    "prove_non_regular",
    "classify_and_prove",
    "compute_pumping_length",
    "build_dfa",
    "build_complement",
    "build_regex",
    "parametric_analysis",
}

_VALID_LANG_KINDS = {"predicate", "grammar", "regex", "natural", "arithmetic_index"}


class IRValidationError(Exception):
    pass


def _check(condition: bool, msg: str) -> None:
    if not condition:
        raise IRValidationError(msg)


def _validate_expr(expr: Any, path: str) -> None:
    _check(isinstance(expr, dict), f"{path}: Expr must be an object")
    kind = expr.get("kind")
    _check(kind in ("count_symbol", "count_subword", "length", "constant"),
           f"{path}: unknown Expr kind '{kind}'")
    if kind == "count_symbol":
        _check("symbol" in expr and "in_var" in expr,
               f"{path}: count_symbol requires 'symbol' and 'in_var'")
    elif kind == "count_subword":
        _check("subword" in expr and "in_var" in expr,
               f"{path}: count_subword requires 'subword' and 'in_var'")
    elif kind == "length":
        _check("of_var" in expr, f"{path}: length requires 'of_var'")
    elif kind == "constant":
        _check("value" in expr and isinstance(expr["value"], int),
               f"{path}: constant requires integer 'value'")


def _validate_predicate(pred: Any, path: str) -> None:
    _check(isinstance(pred, dict), f"{path}: Predicate must be an object")

    if "op" in pred and pred["op"] in ("and", "or", "not"):
        operands = pred.get("operands", [])
        _check(isinstance(operands, list), f"{path}: operands must be a list")
        if pred["op"] == "not":
            _check(len(operands) == 1, f"{path}: 'not' requires exactly 1 operand")
        else:
            _check(len(operands) >= 1, f"{path}: '{pred['op']}' requires ≥1 operands")
        for i, op in enumerate(operands):
            _validate_predicate(op, f"{path}.operands[{i}]")

    elif "op" in pred and pred["op"] in ("eq", "neq", "lt", "leq", "gt", "geq"):
        _check("left" in pred and "right" in pred,
               f"{path}: comparison requires 'left' and 'right'")
        _validate_expr(pred["left"], f"{path}.left")
        _validate_expr(pred["right"], f"{path}.right")

    elif "modulus" in pred:
        _check("expr" in pred and "remainder" in pred,
               f"{path}: modular requires 'expr', 'modulus', 'remainder'")
        _validate_expr(pred["expr"], f"{path}.expr")
        _check(isinstance(pred["modulus"], int) and pred["modulus"] >= 2,
               f"{path}: modulus must be int ≥ 2")
        _check(isinstance(pred["remainder"], int) and pred["remainder"] >= 0,
               f"{path}: remainder must be int ≥ 0")

    elif "parts" in pred:
        _check("concat_pattern" in pred and "constraints" in pred,
               f"{path}: ExistsDecomposition requires 'parts', 'concat_pattern', 'constraints'")
        _check(isinstance(pred["parts"], list), f"{path}: parts must be a list")
        _check(isinstance(pred["concat_pattern"], list), f"{path}: concat_pattern must be a list")
        for i, c in enumerate(pred.get("constraints", [])):
            _validate_predicate(c, f"{path}.constraints[{i}]")

    elif "op" in pred and pred["op"] in ("is_substring", "is_not_substring"):
        _check("substring_expr" in pred and "in_var" in pred,
               f"{path}: substring check requires 'substring_expr' and 'in_var'")

    elif "op" in pred and pred["op"] in ("starts_with", "not_starts_with"):
        _check("prefix_expr" in pred and "of_var" in pred,
               f"{path}: prefix check requires 'prefix_expr' and 'of_var'")

    elif "op" in pred and pred["op"] in ("is_palindrome", "is_not_palindrome"):
        _check("var" in pred, f"{path}: palindrome check requires 'var'")

    else:
        raise IRValidationError(f"{path}: unknown predicate structure: {list(pred.keys())}")


def _validate_language_spec(spec: Any, path: str) -> None:
    _check(isinstance(spec, dict), f"{path}: language_spec must be an object")
    kind = spec.get("kind")
    _check(kind in _VALID_LANG_KINDS, f"{path}: unknown kind '{kind}'")

    if kind == "predicate":
        _check("alphabet" in spec, f"{path}: predicate language requires 'alphabet'")
        _check(isinstance(spec["alphabet"], list) and len(spec["alphabet"]) > 0,
               f"{path}: alphabet must be non-empty list")
        _check("predicate" in spec, f"{path}: predicate language requires 'predicate'")
        _validate_predicate(spec["predicate"], f"{path}.predicate")

    elif kind == "grammar":
        for field in ("terminals", "nonterminals", "start", "rules"):
            _check(field in spec, f"{path}: grammar requires '{field}'")
        _check(isinstance(spec["rules"], list), f"{path}: rules must be a list")
        for i, rule in enumerate(spec["rules"]):
            _check("lhs" in rule and "rhs" in rule,
                   f"{path}.rules[{i}]: rule requires 'lhs' and 'rhs'")

    elif kind == "regex":
        _check("pattern" in spec, f"{path}: regex requires 'pattern'")
        _check("has_backreferences" in spec, f"{path}: regex requires 'has_backreferences'")

    elif kind == "natural":
        _check("description" in spec, f"{path}: natural requires 'description'")

    elif kind == "arithmetic_index":
        _check("symbol" in spec and "index_function" in spec,
               f"{path}: arithmetic_index requires 'symbol' and 'index_function'")


def validate_ir(ir: Any) -> list[str]:
    """Validate an IR dict. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    try:
        _check(isinstance(ir, dict), "IR must be a JSON object")
        _check("task_type" in ir, "Missing required field 'task_type'")
        _check(ir.get("task_type") in _VALID_TASK_TYPES,
               f"Invalid task_type: '{ir.get('task_type')}'")
        _check("source_text" in ir and isinstance(ir["source_text"], str)
               and len(ir["source_text"]) > 0,
               "Missing or empty 'source_text'")
        if "language_spec" in ir:
            _validate_language_spec(ir["language_spec"], "language_spec")
    except IRValidationError as e:
        errors.append(str(e))
    return errors


def validate_ir_json(json_str: str) -> tuple[dict | None, list[str]]:
    """Parse JSON string and validate as IR. Returns (parsed_dict, errors)."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"]
    return data, validate_ir(data)
