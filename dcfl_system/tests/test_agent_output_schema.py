"""``dcfl_system.lib.agent_output_schema`` contract checks (TODO.md §3 M).

Mirrors ``agent_system/tests/test_agent_output_schema.py``: the top-level
key set (``REQUIRED_KEYS``) is checked against each prompt's own worked
example by ``dcfl_system/tests/test_prompt_contracts.py`` already, so this
file only checks the *new* piece -- that every ``_FIELD_SCHEMAS`` entry (a)
covers exactly those same keys, (b) is actually valid per
``output_config.format``'s rules (no empty ``{}``, ``additionalProperties:
False`` at every level), and (c) every agent in ``REQUIRED_KEYS`` (except
the no-fixed-contract ``input_parser``) actually has one -- unlike
``cfl_system`` / ``ll_system``, no dcfl agent's contract has a field keyed
by something chosen at generation time, so nothing here is exempted.
"""

from __future__ import annotations

import pytest

from dcfl_system.lib.agent_output_schema import (
    REQUIRED_KEYS, _FIELD_SCHEMAS, _NO_FIXED_CONTRACT, schema_for,
)
from agent_system.lib.testing.schema_checks import assert_field_schemas_are_valid


def test_every_agent_has_a_schema():
    assert set(_FIELD_SCHEMAS) == set(REQUIRED_KEYS)
    for agent_name in REQUIRED_KEYS:
        assert schema_for(agent_name) is not None


def test_schema_for_no_fixed_contract_agent_is_none():
    for agent_name in _NO_FIXED_CONTRACT:
        assert schema_for(agent_name) is None


@pytest.mark.parametrize("agent_name", sorted(_FIELD_SCHEMAS))
def test_field_schema_keys_match_required_keys_exactly(agent_name):
    assert set(_FIELD_SCHEMAS[agent_name]) == set(REQUIRED_KEYS[agent_name])


def test_field_schemas_have_no_empty_or_open_subschema():
    assert_field_schemas_are_valid(_FIELD_SCHEMAS)


def test_schema_for_builds_a_closed_top_level_schema():
    schema = schema_for("classifier")
    assert schema is not None
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(REQUIRED_KEYS["classifier"])
    assert set(schema["properties"]) == set(REQUIRED_KEYS["classifier"])
