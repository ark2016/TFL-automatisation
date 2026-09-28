"""``cfl_system.lib.agent_output_schema`` contract checks (TODO.md §3 M).

Mirrors ``agent_system/tests/test_agent_output_schema.py``: the top-level
key set (``REQUIRED_KEYS``) is checked against each prompt's own worked
example by ``cfl_system/tests/test_prompt_contracts.py`` already, so this
file only checks the *new* piece -- that every ``_FIELD_SCHEMAS`` entry (a)
covers exactly those same keys, (b) is actually valid per
``output_config.format``'s rules (no empty ``{}``, ``additionalProperties:
False`` at every level, no more than 24 optional properties anywhere in the
tree), and that the one specialist with a genuinely dynamically-keyed
nested field is deliberately exempted, not forgotten.
"""

from __future__ import annotations

import pytest

from cfl_system.lib.agent_output_schema import (
    REQUIRED_KEYS, _FIELD_SCHEMAS, _NO_FIXED_CONTRACT, schema_for,
)
from agent_system.lib.testing.schema_checks import (
    assert_field_schemas_are_valid, assert_schemas_within_optional_limit,
    assert_schemas_within_union_typed_limit,
)

# No agent is exempt any more: `pumping_cfl` / `ogden`'s `word_instances` /
# `marked_positions` turned out to be keyed by the fixed literal strings
# "3"/"4" (not a per-task choice), and `morphism`'s `evidence.morphism.
# mapping` (genuinely keyed by the language's own per-task alphabet
# symbols) was remodelled as an array of {symbol, image} objects instead of
# an object keyed by those symbols -- see the module docstring in
# cfl_system/lib/agent_output_schema.py.
_DYNAMIC_KEY_EXEMPT: frozenset[str] = frozenset()


def test_dynamic_key_exempt_agents_are_in_required_keys_but_not_field_schemas():
    for agent_name in _DYNAMIC_KEY_EXEMPT:
        assert agent_name in REQUIRED_KEYS
        assert agent_name not in _FIELD_SCHEMAS
        assert schema_for(agent_name) is None


def test_every_non_exempt_agent_has_a_schema():
    for agent_name in REQUIRED_KEYS:
        if agent_name in _DYNAMIC_KEY_EXEMPT:
            continue
        assert schema_for(agent_name) is not None, (
            f"{agent_name!r} has a REQUIRED_KEYS entry but no schema -- "
            "add one to _FIELD_SCHEMAS or list it as dynamic-key exempt"
        )


def test_schema_for_no_fixed_contract_agent_is_none():
    for agent_name in _NO_FIXED_CONTRACT:
        assert schema_for(agent_name) is None


@pytest.mark.parametrize("agent_name", sorted(_FIELD_SCHEMAS))
def test_field_schema_keys_match_required_keys_exactly(agent_name):
    assert set(_FIELD_SCHEMAS[agent_name]) == set(REQUIRED_KEYS[agent_name])


def test_field_schemas_have_no_empty_or_open_subschema():
    assert_field_schemas_are_valid(_FIELD_SCHEMAS)


def test_field_schemas_are_within_the_api_optional_properties_limit():
    """The actual TODO.md bug this round fixed: `reasoning` / `retry_planner`
    each had 63 optional properties (recursively) in their shared 9-agent
    retry-hint map -- rejected outright by the API with no schema-rejection
    marker matched, so structured outputs never fell back and every
    `reasoning` call in a live eval run failed as a hard `agent_error`."""
    assert_schemas_within_optional_limit(
        {name: schema_for(name) for name in _FIELD_SCHEMAS}
    )


def test_field_schemas_are_within_the_api_union_typed_properties_limit():
    """A SEPARATE limit from the one above: converting those 63 optional
    properties straight to required+nullable (this round's first attempt)
    traded the "too many optional parameters" 400 for a "too many
    parameters with union types" one instead (49 vs. a limit of 16) --
    fixed by turning the map into an array of one shared item schema
    (`_RETRY_HINTS_SCHEMA`) instead, which has neither problem."""
    assert_schemas_within_union_typed_limit(
        {name: schema_for(name) for name in _FIELD_SCHEMAS}
    )


def test_schema_for_builds_a_closed_top_level_schema():
    schema = schema_for("classifier")
    assert schema is not None
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(REQUIRED_KEYS["classifier"])
    assert set(schema["properties"]) == set(REQUIRED_KEYS["classifier"])
