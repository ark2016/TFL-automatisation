"""Generic prompt-contract tests for every dcfl_system/prompts/*.md file.

root TODO.md §6: "Contract-тесты промптов: каждый ```json-блок парсится и
содержит обязательные ключи; все ключи, которые формирует builder входа,
упомянуты в промпте" (по образцу `cfl_system/tests/test_pda_contract.py`).

Two independent checks:

1. Every fenced ```json block that IS valid JSON (worked examples — the
   schema-sketch blocks with pseudo-union types like "a" | "b" are not valid
   JSON and are skipped, same convention as
   ``dcfl_system/tests/test_theory_contract.py``) must, when it looks like a
   specialist ``AgentOutput`` (has an ``agent_name`` key), carry the full
   output contract's required keys and a valid ``status``.
2. Every input key the orchestrator actually builds for an agent
   (``_build_specialist_input`` for the 5 specialists, the classifier's own
   input dict, the reasoning agent's own input dict) is literally mentioned
   in that agent's prompt file — so the "Input format" section in the
   prompt can never silently drift from what the orchestrator sends.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dcfl_system.orchestrator import (
    DCFL_SPECIALIST_NAMES,
    _build_specialist_input,
    _VALID_ACTIONS,
)
from dcfl_system.lib.agent_output_schema import REQUIRED_KEYS, _SPECIALISTS, _PROOF_SKETCH_BY_AGENT

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)

# REQUIRED_OUTPUT_KEYS now lives in dcfl_system.lib.agent_output_schema
# (TODO.md §3 M) — the same exhaustive contract also builds the
# output_config.format JSON schema each LiveRunner call sends.
REQUIRED_OUTPUT_KEYS = REQUIRED_KEYS["stack_strategy"]
VALID_STATUSES = {"success", "fail", "not_applicable", "uncertain"}


def test_agent_output_schema_specialists_match_orchestrator():
    """dcfl_system.lib.agent_output_schema hardcodes the specialist name
    list (to avoid a circular import with orchestrator.py) -- keep it in
    sync with the real DCFL_SPECIALIST_NAMES."""
    assert set(_SPECIALISTS) == set(DCFL_SPECIALIST_NAMES)


_STRING_UNION_RE = re.compile(
    r'("(?:[^"\\]|\\.)*")(?:\s*\|\s*(?:"(?:[^"\\]|\\.)*"|null))+'
)
_BRACKET_OR_NULL_RE = re.compile(r'([}\]])\s*\|\s*null\b')


def _loads_pseudo_union_schema(block: str) -> object:
    """Parse a "## Output format" block that uses this repo's pseudo-union
    convention for a type sketch (``"dcfl" | "non_dcfl" | null``, ``{ ... }
    | null``) by collapsing each union to its first alternative, then
    ``json.loads`` the result -- these aren't meant to be valid JSON on
    their own, just close enough to read the key set off of."""
    collapsed = _STRING_UNION_RE.sub(r"\1", block)
    collapsed = _BRACKET_OR_NULL_RE.sub(r"\1", collapsed)
    return json.loads(collapsed)


@pytest.mark.parametrize("agent_name,filename", [
    ("classifier", "classifier.md"),
    ("reasoning", "reasoning_agent.md"),
])
def test_agent_output_schema_matches_prompt_output_format_exactly(agent_name, filename):
    """classifier/reasoning have a closed output_config.format schema too
    (agent_output_schema.REQUIRED_KEYS) -- unlike the specialists' shared
    shape, these aren't covered by the worked-example check above, so
    check them directly against the prompt's own "## Output format" block.
    A closed schema constrains generation, so this must be an exact match,
    not just a superset."""
    path = PROMPTS_DIR / filename
    text = path.read_text(encoding="utf-8")
    m = re.search(r"## Output format.*?```json\n(.*?)\n```", text, re.S)
    assert m, f"{filename}: no '## Output format' ```json block found"
    obj = _loads_pseudo_union_schema(m.group(1))
    assert isinstance(obj, dict)
    assert set(obj.keys()) == set(REQUIRED_KEYS[agent_name])


def _json_blocks(path: Path) -> list[dict]:
    """Every fenced ```json block that parses as a JSON object (schema
    sketches using pseudo-union types like "a" | "b" fail to parse and are
    skipped — they document a type, not a worked example)."""
    text = path.read_text(encoding="utf-8")
    out = []
    for block in JSON_BLOCK_RE.findall(text):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---------------------------------------------------------------------------
# 1. Worked-example AgentOutput blocks carry the required contract keys
# ---------------------------------------------------------------------------

# closure_reduction.md currently has schema sketches only (pseudo-JSON with
# `"a" | "b"` unions), no fully-concrete worked example — unlike the other
# 4 specialists. Adding one is prompt content, not the cosmetic-only cleanup
# in scope here (root TODO.md §6); tracked separately, not required below.
_HAS_WORKED_EXAMPLE = tuple(n for n in DCFL_SPECIALIST_NAMES if n != "closure_reduction")


@pytest.mark.parametrize("agent_name", _HAS_WORKED_EXAMPLE)
def test_specialist_prompt_has_a_parseable_worked_example(agent_name):
    path = PROMPTS_DIR / f"{agent_name}.md"
    outputs = [b for b in _json_blocks(path) if "agent_name" in b]
    assert outputs, f"{path.name}: no worked AgentOutput example parses as JSON"


@pytest.mark.parametrize("agent_name", DCFL_SPECIALIST_NAMES)
def test_specialist_worked_examples_have_required_keys(agent_name):
    path = PROMPTS_DIR / f"{agent_name}.md"
    outputs = [b for b in _json_blocks(path) if "agent_name" in b]
    for out in outputs:
        missing = REQUIRED_OUTPUT_KEYS - set(out.keys())
        assert not missing, f"{path.name}: worked example missing keys {missing}: {out}"
        assert out["agent_name"] == agent_name, (
            f"{path.name}: worked example agent_name={out['agent_name']!r}, "
            f"expected {agent_name!r}"
        )
        assert out["status"] in VALID_STATUSES, (
            f"{path.name}: worked example status={out['status']!r} not in {VALID_STATUSES}"
        )


def test_shallit_schema_requires_dead_class_status_enum():
    """docs/VERDICT_POLICY.md §4 dcfl/shallit: ``dead_class_status`` is a
    closed enum {"empty", "infinite"} in the structured-output schema
    (nullable only because ``prefix_continuation`` proofs leave the whole
    field null) -- no longer a free-text ``dead_class_finite`` field, and the
    old three-way enum's "finite" value is retired from the schema (new live
    generations only ever emit "empty"/"infinite"; oracle_verifier.py still
    reads a legacy "finite" value in already-recorded output as "empty" for
    backward compatibility, but the schema no longer offers it). The
    prompt's own schema-sketch block must document the same field."""
    field_schema = _PROOF_SKETCH_BY_AGENT["shallit"]["properties"]["dead_class_status"]
    string_alt = next(s for s in field_schema["anyOf"] if s.get("type") == "string")
    assert set(string_alt["enum"]) == {"empty", "infinite"}
    assert "dead_class_finite" not in _PROOF_SKETCH_BY_AGENT["shallit"]["properties"]

    text = (PROMPTS_DIR / "shallit.md").read_text(encoding="utf-8")
    assert '"dead_class_status"' in text
    assert "dead_class_finite" not in text


def test_reasoning_agent_examples_use_a_valid_action():
    """reasoning_agent.md's own worked examples (if any parse as plain JSON)
    must use one of the actions the orchestrator actually recognizes
    (``_get_action`` / ``_VALID_ACTIONS``)."""
    path = PROMPTS_DIR / "reasoning_agent.md"
    for block in _json_blocks(path):
        if "action" in block:
            assert block["action"] in _VALID_ACTIONS, (
                f"{path.name}: worked example action={block['action']!r} "
                f"not in {_VALID_ACTIONS}"
            )


# ---------------------------------------------------------------------------
# 2. Every key the orchestrator actually sends is mentioned in the prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_name", DCFL_SPECIALIST_NAMES)
def test_specialist_prompt_mentions_every_orchestrator_input_key(agent_name):
    """`_build_specialist_input` is the single source of truth for what a
    specialist actually receives (root CLAUDE.md: dcfl_system/orchestrator.py
    is where these are built). A minimal state without a retry hint still
    exercises ir/hypothesis/classifier_hint/preprocess; retry_hint is checked
    separately below since it's conditional on a non-empty retry plan."""
    state = {"task_ir": {}, "hypothesis": {}, "classifier_hint": {}, "preprocess": {}}
    built = _build_specialist_input(state, agent_name)
    text = (PROMPTS_DIR / f"{agent_name}.md").read_text(encoding="utf-8")
    for key in built:
        assert f'"{key}"' in text, (
            f"{agent_name}.md: Input format never mentions key {key!r} that "
            f"the orchestrator actually sends"
        )


@pytest.mark.parametrize("agent_name", DCFL_SPECIALIST_NAMES)
def test_specialist_prompt_mentions_retry_hint(agent_name):
    """On a retry round `_build_specialist_input` adds `retry_hint` — every
    specialist prompt's Input format must document it too (it's the only
    channel the retry planner has to tell a specialist what to fix)."""
    state = {
        "task_ir": {}, "hypothesis": {}, "classifier_hint": {}, "preprocess": {},
        "retry_plan": {"hints": {agent_name: "try again"}},
    }
    built = _build_specialist_input(state, agent_name)
    assert "retry_hint" in built
    text = (PROMPTS_DIR / f"{agent_name}.md").read_text(encoding="utf-8")
    assert '"retry_hint"' in text


def test_classifier_prompt_mentions_every_orchestrator_input_key():
    # Mirrors run_classifier_node's classifier_input dict (dcfl_system/orchestrator.py).
    text = (PROMPTS_DIR / "classifier.md").read_text(encoding="utf-8")
    for key in ("ir", "hypothesis", "preprocess"):
        assert f'"{key}"' in text


def test_reasoning_agent_prompt_mentions_every_orchestrator_input_key():
    # Mirrors reasoning_agent_node's reasoning_input dict (dcfl_system/orchestrator.py).
    text = (PROMPTS_DIR / "reasoning_agent.md").read_text(encoding="utf-8")
    for key in (
        "ir", "hypothesis", "classifier_hint", "agent_results",
        "oracle_verification", "retry_count", "max_retries",
    ):
        assert f'"{key}"' in text


# ---------------------------------------------------------------------------
# 3. Cosmetic cleanups (root TODO.md §6): no `Model:` lines left in prompts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(PROMPTS_DIR.glob("*.md")), ids=lambda p: p.name)
def test_prompt_has_no_model_line(path):
    """`**Model:** ...` duplicated config.py and had to be edited on every
    model migration — removed for good (root TODO.md §6)."""
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"^\*\*Model:\*\*", text, re.M), (
        f"{path.name}: still has a '**Model:**' line"
    )
