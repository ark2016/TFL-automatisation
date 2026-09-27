"""Contract test for agent_system/lib/agent_output_schema.py (TODO.md §3 M).

`REQUIRED_KEYS` there is meant to be the *exhaustive* top-level key set of
each agent's own prompt contract -- a closed output_config.format schema
constrains generation, so an entry that is missing a key the prompt
actually documents would silently make the model drop it, not just fail a
test. This re-derives each agent's key set from its own
`agent_system/prompts/<name>.md` "## Output Format" worked example and
checks it matches `REQUIRED_KEYS` exactly (not just a superset/subset), so
a prompt edit that adds/removes a top-level key is caught immediately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_system.lib.agent_output_schema import (
    REQUIRED_KEYS, _FIELD_SCHEMAS, _NO_FIXED_CONTRACT, build_agent_output_schema, schema_for,
)
from agent_system.lib.testing.schema_checks import assert_field_schemas_are_valid

# Agents whose top-level key set is fixed (REQUIRED_KEYS has an entry) but
# whose contract has a nested field keyed by something chosen at
# generation time (a DFA's own state names, an alphabet-keyed
# homomorphism mapping, ...) -- see agent_output_schema.py's module
# docstring. `output_config.format`'s `additionalProperties: false`
# requirement (every nesting level, no `patternProperties`) can't express
# that, so these get no schema at all, same as `_NO_FIXED_CONTRACT`.
_DYNAMIC_KEY_EXEMPT = frozenset({"closure_agent", "dfa_builder"})

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_BLOCK_RE = re.compile(r"## Output Format.*?```json\n(.*?)\n```", re.S)


def _output_format_keys(agent_name: str) -> set[str]:
    path = PROMPTS_DIR / f"{agent_name}.md"
    text = path.read_text(encoding="utf-8")
    m = _BLOCK_RE.search(text)
    assert m, f"{path.name}: no '## Output Format' ```json block found"
    obj = json.loads(m.group(1))
    assert isinstance(obj, dict)
    return set(obj.keys())


@pytest.mark.parametrize("agent_name", sorted(REQUIRED_KEYS))
def test_required_keys_match_prompt_output_format_exactly(agent_name):
    actual = _output_format_keys(agent_name)
    expected = set(REQUIRED_KEYS[agent_name])
    assert actual == expected, (
        f"{agent_name}: REQUIRED_KEYS {expected} != prompt's own Output "
        f"Format keys {actual} -- update agent_output_schema.py"
    )


def test_every_json_agent_is_covered():
    """Every prompt file except the no-fixed-contract ones must have a
    REQUIRED_KEYS entry -- otherwise a new agent silently gets no
    structured-output schema (not wrong, just an easy thing to forget)."""
    all_agents = {p.stem for p in PROMPTS_DIR.glob("*.md")}
    covered = set(REQUIRED_KEYS) | set(_NO_FIXED_CONTRACT)
    assert all_agents == covered, all_agents ^ covered


def test_schema_for_unknown_or_exempt_agent_is_none():
    assert schema_for("input_parser") is None
    assert schema_for("formalizer") is None
    assert schema_for("no_such_agent") is None


def test_schema_for_dynamic_key_exempt_agent_is_none():
    """`closure_agent` / `dfa_builder` stay in REQUIRED_KEYS (their
    top-level key set is fixed and prompt-verified) but have no
    `_FIELD_SCHEMAS` entry -- a nested field is keyed by something chosen
    at generation time (DFA state names, an alphabet-keyed homomorphism
    mapping), which `additionalProperties: false` can't express."""
    for agent_name in _DYNAMIC_KEY_EXEMPT:
        assert agent_name in REQUIRED_KEYS
        assert agent_name not in _FIELD_SCHEMAS
        assert schema_for(agent_name) is None


def test_schema_for_known_agent_is_closed_and_required():
    schema = schema_for("classifier")
    assert schema == build_agent_output_schema(_FIELD_SCHEMAS["classifier"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(REQUIRED_KEYS["classifier"])
    assert set(schema["properties"]) == set(REQUIRED_KEYS["classifier"])


@pytest.mark.parametrize("agent_name", sorted(_FIELD_SCHEMAS))
def test_field_schema_keys_match_required_keys_exactly(agent_name):
    """Every `_FIELD_SCHEMAS` entry must cover exactly the same top-level
    keys as `REQUIRED_KEYS` -- a real per-key subschema for a key the
    prompt doesn't have (or a missing one for a key it does) would build
    an invalid or silently-wrong `output_config.format` schema."""
    assert set(_FIELD_SCHEMAS[agent_name]) == set(REQUIRED_KEYS[agent_name])


def test_every_field_schema_property_is_a_concrete_type():
    """No empty ``{}`` subschema anywhere, and every object closes with
    ``additionalProperties: False`` at every nesting level (TODO.md §3 M's
    actual bug -- see ``agent_system.lib.testing.schema_checks`` for the
    exact API error messages this guards against)."""
    assert_field_schemas_are_valid(_FIELD_SCHEMAS)


def test_schema_for_resolves_run_agent_names_after_alias_resolution():
    """`LLMRunner.run_agent` calls `schema_for` with the agent names
    `graph.py` dispatches under ('pumping', 'nerode', 'closure', 'reasoning',
    ...), not the prompt file names REQUIRED_KEYS is keyed by -- it must
    resolve them first (`self._resolve_prompt_name(agent_name)`), or every
    one of these agents silently gets no structured-output schema. The
    `_DYNAMIC_KEY_EXEMPT` agents are the deliberate exception (a nested
    field's real contract can't be expressed as a closed schema at all --
    see agent_output_schema.py's module docstring), not an oversight."""
    from agent_system import graph
    from agent_system.lib.llm_client import LLMRunner

    runner = LLMRunner(api_key="sk-ant-test-not-used")
    names = set(graph.SPECIALIST_NAMES) | {
        "reasoning", "classifier", "proof_checker", "retry_planner",
    }
    for name in sorted(names):
        resolved = runner._resolve_prompt_name(name)
        if resolved in _DYNAMIC_KEY_EXEMPT:
            assert schema_for(resolved) is None
            continue
        assert schema_for(resolved) is not None, (
            f"{name!r} (resolved to {resolved!r}) has no structured-output "
            f"schema -- update _FIELD_SCHEMAS or _PROMPT_ALIASES"
        )
