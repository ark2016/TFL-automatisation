"""
LL IR Schema definition and validation.

Defines the JSON Schema for the LL(k) Intermediate Representation (IR)
and provides validation functions for ll_system task types.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# LL-specific constants
# ---------------------------------------------------------------------------

_LL_TASK_TYPES = {
    "ll_check_language",      # Format 1: set-builder language → is LL?
    "ll_check_grammar_lang",  # Format 2: grammar given → is language LL?
    "ll_check_grammar",       # Format 3: is this grammar LL(k)?
}

_LL_LANG_KINDS = {
    # inherited from base system
    "predicate",
    "grammar",
    "regex",
    "natural",
    "arithmetic_index",
    # new LL-specific kinds
    "set_builder",   # Format 1: {w1 a w2 | constraints}
}


class LLIRValidationError(Exception):
    """Raised when LL IR validation fails."""
    pass


def _ll_check(condition: bool, msg: str) -> None:
    """Raise LLIRValidationError if condition is False."""
    if not condition:
        raise LLIRValidationError(msg)


# ---------------------------------------------------------------------------
# Grammar validation helper
# ---------------------------------------------------------------------------

def _validate_ll_grammar(spec: Any, path: str) -> None:
    """Validate a grammar spec: requires nonterminals, terminals, start, rules.

    Rules format: flat list of {"lhs": str, "rhs": list[str]}.
    Empty rhs [] means epsilon. "ε" in rhs is also epsilon.
    """
    _ll_check(isinstance(spec, dict), f"{path}: grammar must be an object")
    for field in ("nonterminals", "terminals", "start", "rules"):
        _ll_check(field in spec, f"{path}: grammar requires '{field}'")
    _ll_check(
        isinstance(spec["nonterminals"], list),
        f"{path}: nonterminals must be a list",
    )
    _ll_check(
        isinstance(spec["terminals"], list),
        f"{path}: terminals must be a list",
    )
    _ll_check(
        isinstance(spec["start"], str) and len(spec["start"]) > 0,
        f"{path}: start must be a non-empty string",
    )
    _ll_check(isinstance(spec["rules"], list), f"{path}: rules must be a list")
    for i, rule in enumerate(spec["rules"]):
        _ll_check(
            isinstance(rule, dict),
            f"{path}.rules[{i}]: rule must be an object",
        )
        _ll_check(
            "lhs" in rule and "rhs" in rule,
            f"{path}.rules[{i}]: rule requires 'lhs' and 'rhs'",
        )
        _ll_check(
            isinstance(rule["lhs"], str) and len(rule["lhs"]) > 0,
            f"{path}.rules[{i}]: 'lhs' must be a non-empty string",
        )
        _ll_check(
            isinstance(rule["rhs"], list),
            f"{path}.rules[{i}]: 'rhs' must be a list",
        )


# ---------------------------------------------------------------------------
# LL language_spec validators
# ---------------------------------------------------------------------------

def _validate_set_builder(spec: dict, path: str) -> None:
    """Validate a set_builder language_spec.

    Expected structure:
    {
      "kind": "set_builder",
      "alphabet": ["a", "b"],
      "variables": [{"name": "w1", "domain": {...}}, ...],
      "template": ["w1", "a", "w2"],
      "constraints": [...]   # optional
    }
    """
    _ll_check("alphabet" in spec, f"{path}: set_builder requires 'alphabet'")
    _ll_check(
        isinstance(spec["alphabet"], list),
        f"{path}: alphabet must be a list",
    )
    _ll_check("variables" in spec, f"{path}: set_builder requires 'variables'")
    variables = spec["variables"]
    _ll_check(
        isinstance(variables, list),
        f"{path}: variables must be a list",
    )
    for i, var in enumerate(variables):
        _ll_check(
            isinstance(var, dict),
            f"{path}.variables[{i}]: variable must be an object",
        )
        _ll_check(
            "name" in var,
            f"{path}.variables[{i}]: variable requires 'name'",
        )
        _ll_check(
            "domain" in var,
            f"{path}.variables[{i}]: variable requires 'domain'",
        )
        _ll_check(
            isinstance(var["name"], str) and len(var["name"]) > 0,
            f"{path}.variables[{i}]: 'name' must be a non-empty string",
        )
        _ll_check(
            isinstance(var["domain"], dict),
            f"{path}.variables[{i}]: 'domain' must be an object",
        )
    _ll_check("template" in spec, f"{path}: set_builder requires 'template'")
    _ll_check(
        isinstance(spec["template"], list),
        f"{path}: template must be a list of strings",
    )
    for i, item in enumerate(spec["template"]):
        _ll_check(
            isinstance(item, str),
            f"{path}.template[{i}]: template item must be a string",
        )
    # constraints is optional; if present, must be a list
    if "constraints" in spec:
        _ll_check(
            isinstance(spec["constraints"], list),
            f"{path}: constraints must be a list",
        )


def _validate_base_grammar_kind(spec: dict, path: str) -> None:
    """Validate a grammar-kind language_spec (base kind, without requiring kind field)."""
    for field in ("terminals", "nonterminals", "start", "rules"):
        _ll_check(field in spec, f"{path}: grammar requires '{field}'")
    _ll_check(isinstance(spec["rules"], list), f"{path}: rules must be a list")
    for i, rule in enumerate(spec["rules"]):
        _ll_check(
            isinstance(rule, dict),
            f"{path}.rules[{i}]: rule must be an object",
        )
        _ll_check(
            "lhs" in rule and "rhs" in rule,
            f"{path}.rules[{i}]: rule requires 'lhs' and 'rhs'",
        )


# ---------------------------------------------------------------------------
# Top-level LL language_spec validation
# ---------------------------------------------------------------------------

def _validate_ll_language_spec(spec: Any, path: str) -> None:
    """Validate an LL language_spec (includes LL-specific kinds)."""
    _ll_check(isinstance(spec, dict), f"{path}: language_spec must be an object")
    kind = spec.get("kind")
    _ll_check(kind in _LL_LANG_KINDS, f"{path}: unknown kind '{kind}'")

    if kind == "set_builder":
        _validate_set_builder(spec, path)
    elif kind == "grammar":
        _validate_base_grammar_kind(spec, path)
    elif kind == "predicate":
        _ll_check(
            "alphabet" in spec,
            f"{path}: predicate language requires 'alphabet'",
        )
        _ll_check(
            isinstance(spec["alphabet"], list) and len(spec["alphabet"]) > 0,
            f"{path}: alphabet must be non-empty list",
        )
        _ll_check(
            "predicate" in spec,
            f"{path}: predicate language requires 'predicate'",
        )
    elif kind == "regex":
        _ll_check("pattern" in spec, f"{path}: regex requires 'pattern'")
        _ll_check(
            "has_backreferences" in spec,
            f"{path}: regex requires 'has_backreferences'",
        )
    elif kind == "natural":
        _ll_check(
            "description" in spec,
            f"{path}: natural requires 'description'",
        )
    elif kind == "arithmetic_index":
        _ll_check(
            "symbol" in spec and "index_function" in spec,
            f"{path}: arithmetic_index requires 'symbol' and 'index_function'",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_ll_ir(ir: Any) -> list[str]:
    """Validate LL IR dict. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    try:
        _ll_check(isinstance(ir, dict), "IR must be a JSON object")
        _ll_check("task_type" in ir, "Missing required field 'task_type'")
        _ll_check(
            ir.get("task_type") in _LL_TASK_TYPES,
            f"Invalid task_type: '{ir.get('task_type')}'",
        )
        _ll_check(
            "source_text" in ir
            and isinstance(ir["source_text"], str)
            and len(ir["source_text"]) > 0,
            "Missing or empty 'source_text'",
        )

        task_type = ir.get("task_type")

        if task_type == "ll_check_grammar":
            # Format 3: grammar at top level
            _ll_check(
                "grammar" in ir,
                "ll_check_grammar requires top-level 'grammar' field",
            )
            _validate_ll_grammar(ir["grammar"], "grammar")
        else:
            # Format 1 and 2: language_spec required
            _ll_check(
                "language_spec" in ir,
                f"'{task_type}' requires 'language_spec'",
            )
            _validate_ll_language_spec(ir["language_spec"], "language_spec")

    except LLIRValidationError as e:
        errors.append(str(e))
    return errors


def validate_ll_ir_json(json_str: str) -> tuple[dict | None, list[str]]:
    """Parse JSON string and validate as LL IR. Returns (parsed_dict, errors)."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"]
    return data, validate_ll_ir(data)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m ll_system.lib.ll_ir_schema <file.json>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        import json as _json
        data = _json.load(f)
    errors = validate_ll_ir(data)
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("Valid LL IR")
