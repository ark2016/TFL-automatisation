"""Shared ``output_config.format`` schema sanity checks (TODO.md §3 M).

Used by every system's ``test_agent_output_schema.py`` (agent_system, cfl,
dcfl, ll) to check a ``_FIELD_SCHEMAS`` entry never repeats an actual,
live-confirmed ``output_config.format`` rejection:

- an empty ``{}`` subschema ("accepts any JSON value"): ``"Empty schema ({})
  that accepts any JSON value is not supported. Please specify a concrete
  type."`` (verified 2026-06);
- an object missing ``additionalProperties: False`` at some nesting level:
  the API requires it "for 'object' type" everywhere, not just at the
  schema's root (verified 2026-06);
- too many **optional** properties (a property not in its own object's
  ``required`` list) anywhere in the schema tree: ``"Schemas contains too
  many optional parameters (N), which would make grammar compilation
  inefficient. Reduce the number of optional parameters in your tool
  schemas (limit: 24)."`` (verified live 2026-09-27, see
  ``cfl_system/tests/test_agent_output_schema.py`` and this round's
  ``llm_client._SCHEMA_REJECTION_MARKERS`` update — a live cfl eval run
  hit this on ``reasoning``/``retry_planner``'s 63-optional-property
  ``hints`` map, with no matching rejection marker, so every reasoning
  call fell through as a hard ``agent_error`` instead of falling back to
  the legacy prose-extraction path). Counted **recursively**: at *every*
  object schema in the tree, however deeply nested, ``len(properties) -
  len(required)`` (that object's OWN optional count) adds to the running
  total — regardless of whether the *parent* property holding that object
  is itself required. Confirmed live with a schema of 20 optional
  top-level properties plus one *required* nested object whose own 10
  properties are all optional: rejected as ``"too many optional
  parameters (29)"`` — 19 (20 top-level properties minus the 1 that is the
  required nested object itself) + 10 (the nested object's own optionals).
- too many **union-typed** properties (a property whose own subschema is
  ``anyOf`` — every ``nullable(...)`` field is one of these — or a bare
  ``type: [...]`` array) anywhere in the tree: ``"Schemas contains too
  many parameters with union types (N parameters with type arrays or
  anyOf). This causes exponential compilation cost. Reduce the number of
  nullable or union-typed parameters (limit: 16 parameters with
  unions)."`` (verified live 2026-09-27 — a *separate* limit from the
  optional-properties one above: this round's first fix for
  ``reasoning``/``retry_planner`` converted every optional property in
  their 63-slot ``hints`` map to required+nullable, which fixed the
  24-optional-properties rejection only to hit this 16-union-typed-
  properties one instead, at 49). Counted recursively the same way: every
  object schema's own properties are checked (a property whose value has
  ``anyOf`` counts 1), then each property's subschema (and each ``anyOf``
  branch) is walked for further nested union-typed properties.
"""

from __future__ import annotations

from typing import Any

# The API's own limits (verified live 2026-09-27, see module docstring) --
# two separate caps, not one: a schema can independently blow past either.
MAX_OPTIONAL_PROPERTIES = 24
MAX_UNION_TYPED_PROPERTIES = 16


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


def count_optional_properties(node: Any) -> int:
    """Count optional properties (not in their own object's ``required``)
    **recursively** over the whole schema tree rooted at `node` — matches
    the API's own (live-confirmed, see module docstring) counting rule:
    every object schema at every depth contributes ``len(properties) -
    len(required)``, whether or not the property holding that object is
    itself required."""
    if not isinstance(node, dict):
        return 0
    total = 0
    if "anyOf" in node:
        for branch in node["anyOf"]:
            total += count_optional_properties(branch)
        return total
    node_type = node.get("type")
    if node_type == "object":
        props = node.get("properties", {})
        required = set(node.get("required", []))
        total += sum(1 for k in props if k not in required)
        for sub in props.values():
            total += count_optional_properties(sub)
    elif node_type == "array":
        total += count_optional_properties(node.get("items", {}))
    return total


def assert_optional_properties_within_limit(
    schema: dict[str, Any], path: str, *, limit: int = MAX_OPTIONAL_PROPERTIES,
) -> None:
    """Raise ``AssertionError`` if `schema` (a full ``output_config.format``
    schema, e.g. one ``schema_for(agent_name)`` builds) has more than
    `limit` optional properties anywhere in its tree (recursively — see
    :func:`count_optional_properties`) — the actual TODO.md bug: a schema
    like this is rejected outright by the API with no chance to fall back
    (``llm_client._looks_like_schema_rejection`` only fires *after* the API
    has already said no)."""
    n = count_optional_properties(schema)
    assert n <= limit, (
        f"{path}: {n} optional properties (counted recursively) exceeds "
        f"the API's limit of {limit} -- the API rejects this schema "
        f"outright with 'Schemas contains too many optional parameters "
        f"({n if n else '...'}), ... (limit: {limit})'. Convert some of "
        f"them to required+nullable (schema_object(...) without an "
        f"explicit `required=[]`, wrapped in `nullable(...)`) instead of "
        f"leaving them out of `required`."
    )


def assert_schemas_within_optional_limit(
    schemas: dict[str, dict[str, Any] | None], *, limit: int = MAX_OPTIONAL_PROPERTIES,
) -> None:
    """Run :func:`assert_optional_properties_within_limit` over every
    non-``None`` schema in an ``{agent_name: schema_for(agent_name)}``-shaped
    mapping."""
    for agent_name, schema in schemas.items():
        if schema is None:
            continue
        assert_optional_properties_within_limit(schema, agent_name, limit=limit)


def count_union_typed_properties(node: Any) -> int:
    """Count properties whose own subschema is a union type (``anyOf`` —
    every ``nullable(...)`` field is one of these — or a bare ``type:
    [...]`` array) **recursively** over the whole schema tree rooted at
    `node` — the API's second, separate limit from
    :func:`count_optional_properties` (see module docstring)."""
    if not isinstance(node, dict):
        return 0
    total = 0
    if "anyOf" in node:
        # `node` itself is the union-typed value of some property one
        # level up -- that occurrence is counted there. Recurse into each
        # branch for further, deeper-nested union-typed properties.
        for branch in node["anyOf"]:
            total += count_union_typed_properties(branch)
        return total
    node_type = node.get("type")
    if node_type == "object":
        for sub in node.get("properties", {}).values():
            if isinstance(sub, dict) and (
                "anyOf" in sub or isinstance(sub.get("type"), list)
            ):
                total += 1
            total += count_union_typed_properties(sub)
    elif node_type == "array":
        total += count_union_typed_properties(node.get("items", {}))
    return total


def assert_union_typed_properties_within_limit(
    schema: dict[str, Any], path: str, *, limit: int = MAX_UNION_TYPED_PROPERTIES,
) -> None:
    """Raise ``AssertionError`` if `schema` has more than `limit`
    union-typed (``anyOf`` / ``nullable(...)``) properties anywhere in its
    tree (recursively — see :func:`count_union_typed_properties`) — the
    API's second, separate optional/union limit (see module docstring)."""
    n = count_union_typed_properties(schema)
    assert n <= limit, (
        f"{path}: {n} union-typed (nullable/anyOf) properties (counted "
        f"recursively) exceeds the API's limit of {limit} -- the API "
        f"rejects this schema outright with 'Schemas contains too many "
        f"parameters with union types ({n if n else '...'} parameters "
        f"with type arrays or anyOf) ... (limit: {limit} parameters with "
        f"unions)'. Some `nullable(...)` fields need to become plain "
        f"required (non-nullable) fields, or the shape needs to change "
        f"(e.g. a map keyed by a fixed small set turned into an array of "
        f"one shared item schema, as `cfl_system.lib.agent_output_schema`'s "
        f"`_RETRY_HINTS_SCHEMA` does) instead of adding more nullable "
        f"copies of the same subschema."
    )


def assert_schemas_within_union_typed_limit(
    schemas: dict[str, dict[str, Any] | None], *, limit: int = MAX_UNION_TYPED_PROPERTIES,
) -> None:
    """Run :func:`assert_union_typed_properties_within_limit` over every
    non-``None`` schema in an ``{agent_name: schema_for(agent_name)}``-shaped
    mapping."""
    for agent_name, schema in schemas.items():
        if schema is None:
            continue
        assert_union_typed_properties_within_limit(schema, agent_name, limit=limit)
