"""
CFL IR Schema definition and validation.

Extends the base IR schema from agent_system.lib.ir_schema with
context-free language specific task types and language_spec kinds.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_system.lib.ir_schema import (
    _validate_expr,
    _validate_predicate,
    _validate_language_spec as _validate_base_language_spec,
    IRValidationError,
)

# ---------------------------------------------------------------------------
# CFL-specific constants
# ---------------------------------------------------------------------------

_CFL_TASK_TYPES = {
    "classify_cfl",
    "prove_cfl",
    "prove_non_cfl",
    "classify_and_prove_cfl",
    "grammar_filter_cfl",
}

_CFL_LANG_KINDS = {
    # inherited from base
    "predicate",
    "grammar",
    "regex",
    "natural",
    "arithmetic_index",
    # new CFL kinds
    "grammar_filter",
    "repeated_subword",
    "exists_decomposition",
}

# Pattern for rev(X) in concat_pattern
_REV_PATTERN = re.compile(r"^rev\((\w+)\)$")


class CFLIRValidationError(Exception):
    """Raised when CFL IR validation fails."""
    pass


def _cfl_check(condition: bool, msg: str) -> None:
    if not condition:
        raise CFLIRValidationError(msg)


# ---------------------------------------------------------------------------
# Grammar validation (reused for grammar_filter)
# ---------------------------------------------------------------------------

def _validate_grammar(spec: Any, path: str) -> None:
    """Validate a grammar spec object (same structure as base 'grammar' kind)."""
    _cfl_check(isinstance(spec, dict), f"{path}: grammar must be an object")
    _cfl_check(spec.get("kind") == "grammar", f"{path}: grammar must have kind='grammar'")
    for field in ("terminals", "nonterminals", "start", "rules"):
        _cfl_check(field in spec, f"{path}: grammar requires '{field}'")
    _cfl_check(isinstance(spec["rules"], list), f"{path}: rules must be a list")
    for i, rule in enumerate(spec["rules"]):
        _cfl_check(isinstance(rule, dict), f"{path}.rules[{i}]: rule must be an object")
        _cfl_check("lhs" in rule and "rhs" in rule,
                    f"{path}.rules[{i}]: rule requires 'lhs' and 'rhs'")


# ---------------------------------------------------------------------------
# CFL language_spec validators
# ---------------------------------------------------------------------------

def _validate_grammar_filter(spec: dict, path: str) -> None:
    """Validate a grammar_filter language_spec."""
    _cfl_check("grammar" in spec, f"{path}: grammar_filter requires 'grammar'")
    _validate_grammar(spec["grammar"], f"{path}.grammar")

    _cfl_check("filter" in spec, f"{path}: grammar_filter requires 'filter'")
    filt = spec["filter"]
    _cfl_check(isinstance(filt, dict), f"{path}.filter: filter must be an object")

    # Filter can be a Predicate (reuse base validation) or a natural_language_filter
    if "natural_language_filter" in filt:
        _cfl_check(isinstance(filt["natural_language_filter"], str),
                    f"{path}.filter: natural_language_filter must be a string")
    else:
        # Treat as a Predicate — use base validation (raises IRValidationError)
        try:
            _validate_predicate(filt, f"{path}.filter")
        except IRValidationError as e:
            raise CFLIRValidationError(str(e))


def _validate_repeated_subword(spec: dict, path: str) -> None:
    """Validate a repeated_subword language_spec."""
    _cfl_check("parts" in spec, f"{path}: repeated_subword requires 'parts'")
    _cfl_check("concat_pattern" in spec, f"{path}: repeated_subword requires 'concat_pattern'")
    _cfl_check("alphabets" in spec, f"{path}: repeated_subword requires 'alphabets'")

    parts = spec["parts"]
    concat_pattern = spec["concat_pattern"]
    alphabets = spec["alphabets"]

    _cfl_check(isinstance(parts, list) and len(parts) > 0,
               f"{path}: parts must be a non-empty list")
    _cfl_check(isinstance(concat_pattern, list) and len(concat_pattern) > 0,
               f"{path}: concat_pattern must be a non-empty list")
    _cfl_check(isinstance(alphabets, dict),
               f"{path}: alphabets must be an object")

    parts_set = set(parts)

    # Every item in concat_pattern must be a defined part
    for i, item in enumerate(concat_pattern):
        _cfl_check(item in parts_set,
                    f"{path}.concat_pattern[{i}]: '{item}' is not in parts {parts}")

    # alphabets keys must match parts
    _cfl_check(set(alphabets.keys()) == parts_set,
               f"{path}: alphabets keys {set(alphabets.keys())} must match parts {parts_set}")

    # Each alphabet must be a non-empty list
    for key, val in alphabets.items():
        _cfl_check(isinstance(val, list) and len(val) > 0,
                    f"{path}.alphabets.{key}: must be a non-empty list")

    # Validate constraints if present
    for i, c in enumerate(spec.get("constraints", [])):
        try:
            _validate_predicate(c, f"{path}.constraints[{i}]")
        except IRValidationError as e:
            raise CFLIRValidationError(str(e))


def _validate_exists_decomposition(spec: dict, path: str) -> None:
    """Validate an exists_decomposition language_spec."""
    _cfl_check("parts" in spec, f"{path}: exists_decomposition requires 'parts'")
    _cfl_check("concat_pattern" in spec, f"{path}: exists_decomposition requires 'concat_pattern'")

    parts = spec["parts"]
    concat_pattern = spec["concat_pattern"]

    _cfl_check(isinstance(parts, list) and len(parts) > 0,
               f"{path}: parts must be a non-empty list")
    _cfl_check(isinstance(concat_pattern, list) and len(concat_pattern) > 0,
               f"{path}: concat_pattern must be a non-empty list")

    parts_set = set(parts)

    # Each item in concat_pattern is either a part name or rev(X) where X is a part
    for i, item in enumerate(concat_pattern):
        m = _REV_PATTERN.match(item)
        if m:
            inner = m.group(1)
            _cfl_check(inner in parts_set,
                        f"{path}.concat_pattern[{i}]: rev({inner}) references unknown part '{inner}'")
        else:
            _cfl_check(item in parts_set,
                        f"{path}.concat_pattern[{i}]: '{item}' is not in parts and not rev(X)")

    # alphabets optional but if present, keys must be subset of parts
    if "alphabets" in spec:
        alphabets = spec["alphabets"]
        _cfl_check(isinstance(alphabets, dict), f"{path}: alphabets must be an object")
        for key in alphabets:
            _cfl_check(key in parts_set,
                        f"{path}.alphabets: key '{key}' is not in parts {parts}")

    # Validate constraints if present
    for i, c in enumerate(spec.get("constraints", [])):
        try:
            _validate_predicate(c, f"{path}.constraints[{i}]")
        except IRValidationError as e:
            raise CFLIRValidationError(str(e))


# ---------------------------------------------------------------------------
# Top-level CFL language_spec validation
# ---------------------------------------------------------------------------

def _validate_cfl_language_spec(spec: Any, path: str) -> None:
    """Validate a CFL language_spec (extends base kinds with CFL-specific ones)."""
    _cfl_check(isinstance(spec, dict), f"{path}: language_spec must be an object")
    kind = spec.get("kind")
    _cfl_check(kind in _CFL_LANG_KINDS, f"{path}: unknown kind '{kind}'")

    if kind == "grammar_filter":
        _validate_grammar_filter(spec, path)
    elif kind == "repeated_subword":
        _validate_repeated_subword(spec, path)
    elif kind == "exists_decomposition":
        _validate_exists_decomposition(spec, path)
    else:
        # Delegate to base validation for inherited kinds
        try:
            _validate_base_language_spec(spec, path)
        except IRValidationError as e:
            raise CFLIRValidationError(str(e))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_cfl_ir(ir: Any) -> list[str]:
    """Validate a CFL IR dict. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    try:
        _cfl_check(isinstance(ir, dict), "IR must be a JSON object")
        _cfl_check("task_type" in ir, "Missing required field 'task_type'")
        _cfl_check(ir.get("task_type") in _CFL_TASK_TYPES,
                    f"Invalid task_type: '{ir.get('task_type')}'")
        _cfl_check(
            "source_text" in ir
            and isinstance(ir["source_text"], str)
            and len(ir["source_text"]) > 0,
            "Missing or empty 'source_text'",
        )
        if "language_spec" in ir:
            _validate_cfl_language_spec(ir["language_spec"], "language_spec")
    except CFLIRValidationError as e:
        errors.append(str(e))
    return errors


def validate_cfl_ir_json(json_str: str) -> tuple[dict | None, list[str]]:
    """Parse JSON string and validate as CFL IR. Returns (parsed_dict, errors)."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"]
    return data, validate_cfl_ir(data)
