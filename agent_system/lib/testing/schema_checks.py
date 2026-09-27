"""Shared ``output_config.format`` schema sanity checks (TODO.md §3 M).

Used by every system's ``test_agent_output_schema.py`` (agent_system, cfl,
dcfl, ll) to check a ``_FIELD_SCHEMAS`` entry never repeats the actual
TODO.md §3 M bug: an empty ``{}`` subschema, or an object missing
``additionalProperties: False`` at some nesting level. Both are rejected
live by ``output_config.format.schema`` (verified 2026-06 against
``client.messages.create``) —

- ``{}`` ("accepts any JSON value"): ``"Empty schema ({}) that accepts any
  JSON value is not supported. Please specify a concrete type."``
- an object without ``additionalProperties: false`` at *every* level
  (including nested): the API requires it "for 'object' type" everywhere,
  not just at the schema's root.
"""

from __future__ import annotations

from typing import Any


def assert_no_empty_or_open_subschema(node: Any, path: str) -> None:
    """Walk one subschema (a property, an array's ``items``, an ``anyOf``
    branch, ...) and raise ``AssertionError`` at the first empty ``{}`` or
    object missing ``additionalProperties: False``, at any depth."""
    assert node != {}, f"{path}: empty/unconstrained subschema"
    if "anyOf" in node:
        for i, branch in enumerate(node["anyOf"]):
            assert_no_empty_or_open_subschema(branch, f"{path}.anyOf[{i}]")
        return
    assert "type" in node, f"{path}: no 'type' and no 'anyOf'"
    if node["type"] == "object":
        assert node.get("additionalProperties") is False, (
            f"{path}: object without additionalProperties=False"
        )
        for key, sub in node.get("properties", {}).items():
            assert_no_empty_or_open_subschema(sub, f"{path}.{key}")
    elif node["type"] == "array":
        assert_no_empty_or_open_subschema(node["items"], f"{path}[]")


def assert_field_schemas_are_valid(field_schemas: dict[str, dict[str, Any]]) -> None:
    """Run :func:`assert_no_empty_or_open_subschema` over every property of
    every agent in a ``_FIELD_SCHEMAS``-shaped mapping."""
    for agent_name, properties in field_schemas.items():
        for key, sub in properties.items():
            assert_no_empty_or_open_subschema(sub, f"{agent_name}.{key}")
