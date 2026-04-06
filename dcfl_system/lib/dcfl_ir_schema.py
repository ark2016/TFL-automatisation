"""
DCFL IR Schema definition and validation.

Extends the base IR schema from agent_system.lib.ir_schema with
deterministic context-free language specific task types and language_spec kinds.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_system.lib.ir_schema import IRValidationError

# ---------------------------------------------------------------------------
# DCFL-specific constants
# ---------------------------------------------------------------------------

_DCFL_TASK_TYPES = {
    "classify_dcfl",
    "prove_dcfl",
    "prove_non_dcfl",
    "classify_and_prove_dcfl",
}

_DCFL_INPUT_FORMATS = {"set_builder", "grammar"}

_DCFL_LANG_KINDS = {"set_builder", "grammar"}

_VALID_QUANTIFIERS = {"forall", "exists"}

_VALID_CONSTRAINT_KINDS = {
    "length_cmp",
    "regex_member",
    "equal",
    "reverse",
    "integer_cmp",
    "disjunction",
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class DCFLIRValidationError(Exception):
    """Raised when DCFL IR validation fails."""
    pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _dcfl_check(condition: bool, msg: str) -> None:
    if not condition:
        raise DCFLIRValidationError(msg)


# ---------------------------------------------------------------------------
# Variable validation
# ---------------------------------------------------------------------------

def _validate_variable(var: Any, path: str) -> None:
    """Validate a single Variable object."""
    _dcfl_check(isinstance(var, dict), f"{path}: variable must be an object")
    _dcfl_check(
        "name" in var and isinstance(var["name"], str) and len(var["name"]) > 0,
        f"{path}: variable requires non-empty 'name' string",
    )
    _dcfl_check(
        "domain" in var,
        f"{path}: variable requires 'domain' (str or null)",
    )
    if var["domain"] is not None:
        _dcfl_check(
            isinstance(var["domain"], str),
            f"{path}: domain must be a string or null",
        )
    _dcfl_check(
        "quantifier" in var and var["quantifier"] in _VALID_QUANTIFIERS,
        f"{path}: variable requires 'quantifier' in {_VALID_QUANTIFIERS}",
    )


# ---------------------------------------------------------------------------
# Constraint validation
# ---------------------------------------------------------------------------

def _validate_constraint(constraint: Any, path: str) -> None:
    """Validate a single Constraint object."""
    _dcfl_check(isinstance(constraint, dict), f"{path}: constraint must be an object")
    _dcfl_check(
        "kind" in constraint and constraint["kind"] in _VALID_CONSTRAINT_KINDS,
        f"{path}: constraint requires 'kind' in {_VALID_CONSTRAINT_KINDS}",
    )
    _dcfl_check(
        "args" in constraint and isinstance(constraint["args"], dict),
        f"{path}: constraint requires 'args' dict",
    )


# ---------------------------------------------------------------------------
# language_spec validators
# ---------------------------------------------------------------------------

def _validate_set_builder_spec(spec: dict, path: str) -> None:
    """Validate a set_builder language_spec."""
    _dcfl_check(
        "word_pattern" in spec and isinstance(spec["word_pattern"], str)
        and len(spec["word_pattern"]) > 0,
        f"{path}: set_builder requires non-empty 'word_pattern' string",
    )

    # variables
    _dcfl_check("variables" in spec, f"{path}: set_builder requires 'variables'")
    variables = spec["variables"]
    _dcfl_check(
        isinstance(variables, list),
        f"{path}: variables must be a list",
    )
    for i, var in enumerate(variables):
        _validate_variable(var, f"{path}.variables[{i}]")

    # constraints
    _dcfl_check("constraints" in spec, f"{path}: set_builder requires 'constraints'")
    constraints = spec["constraints"]
    _dcfl_check(
        isinstance(constraints, list),
        f"{path}: constraints must be a list",
    )
    for i, c in enumerate(constraints):
        _validate_constraint(c, f"{path}.constraints[{i}]")


def _validate_grammar_spec(spec: dict, path: str) -> None:
    """Validate a grammar language_spec."""
    for field in ("terminals", "nonterminals", "start", "rules"):
        _dcfl_check(field in spec, f"{path}: grammar requires '{field}'")

    _dcfl_check(
        isinstance(spec["terminals"], list),
        f"{path}: terminals must be a list",
    )
    _dcfl_check(
        isinstance(spec["nonterminals"], list),
        f"{path}: nonterminals must be a list",
    )
    _dcfl_check(
        isinstance(spec["start"], str) and len(spec["start"]) > 0,
        f"{path}: start must be a non-empty string",
    )
    _dcfl_check(
        isinstance(spec["rules"], list),
        f"{path}: rules must be a list",
    )
    for i, rule in enumerate(spec["rules"]):
        _dcfl_check(
            isinstance(rule, dict),
            f"{path}.rules[{i}]: rule must be an object",
        )
        _dcfl_check(
            "lhs" in rule and "rhs" in rule,
            f"{path}.rules[{i}]: rule requires 'lhs' and 'rhs'",
        )
        _dcfl_check(
            isinstance(rule["lhs"], str),
            f"{path}.rules[{i}]: lhs must be a string",
        )
        _dcfl_check(
            isinstance(rule["rhs"], list),
            f"{path}.rules[{i}]: rhs must be a list",
        )
        for j, sym in enumerate(rule["rhs"]):
            _dcfl_check(
                isinstance(sym, str),
                f"{path}.rules[{i}].rhs[{j}]: each symbol must be a string",
            )


# ---------------------------------------------------------------------------
# Top-level language_spec validation
# ---------------------------------------------------------------------------

def _validate_dcfl_language_spec(spec: Any, path: str) -> None:
    """Validate a DCFL language_spec."""
    _dcfl_check(isinstance(spec, dict), f"{path}: language_spec must be an object")
    kind = spec.get("kind")
    _dcfl_check(
        kind in _DCFL_LANG_KINDS,
        f"{path}: unknown kind '{kind}', expected one of {_DCFL_LANG_KINDS}",
    )

    if kind == "set_builder":
        _validate_set_builder_spec(spec, path)
    elif kind == "grammar":
        _validate_grammar_spec(spec, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_dcfl_ir(ir: Any) -> list[str]:
    """Validate a DCFL IR dict. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    try:
        _dcfl_check(isinstance(ir, dict), "IR must be a JSON object")

        # task_type (optional but validated if present)
        if "task_type" in ir:
            _dcfl_check(
                ir["task_type"] in _DCFL_TASK_TYPES,
                f"Invalid task_type: '{ir.get('task_type')}', "
                f"expected one of {_DCFL_TASK_TYPES}",
            )

        # task_id
        if "task_id" in ir:
            _dcfl_check(
                isinstance(ir["task_id"], str),
                "task_id must be a string",
            )

        # source_text (required, non-empty)
        _dcfl_check(
            "source_text" in ir
            and isinstance(ir["source_text"], str)
            and len(ir["source_text"]) > 0,
            "Missing or empty 'source_text'",
        )

        # input_format
        if "input_format" in ir:
            _dcfl_check(
                ir["input_format"] in _DCFL_INPUT_FORMATS,
                f"Invalid input_format: '{ir.get('input_format')}', "
                f"expected one of {_DCFL_INPUT_FORMATS}",
            )

        # language_spec
        if "language_spec" in ir:
            _validate_dcfl_language_spec(ir["language_spec"], "language_spec")

        # alphabet (required, non-empty)
        _dcfl_check(
            "alphabet" in ir,
            "Missing required field 'alphabet'",
        )
        _dcfl_check(
            isinstance(ir["alphabet"], list) and len(ir["alphabet"]) > 0,
            "alphabet must be a non-empty list",
        )
        for i, sym in enumerate(ir["alphabet"]):
            _dcfl_check(
                isinstance(sym, str),
                f"alphabet[{i}]: each symbol must be a string",
            )

    except DCFLIRValidationError as e:
        errors.append(str(e))
    return errors


def validate_dcfl_ir_json(json_str: str) -> tuple[dict | None, list[str]]:
    """Parse JSON string and validate as DCFL IR.

    Returns (parsed_dict, errors). If JSON is invalid, parsed_dict is None.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"]
    return data, validate_dcfl_ir(data)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python -m dcfl_system.lib.dcfl_ir_schema <file.json>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    data, errors = validate_dcfl_ir_json(raw)
    if errors:
        print(f"Validation FAILED ({len(errors)} error(s)):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("Validation OK")
        sys.exit(0)
