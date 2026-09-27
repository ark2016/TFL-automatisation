"""Contract tests for ll_system prompts.

Two things drift silently when prompts are edited by hand:
  1. A fenced ```json example stops being valid JSON (typo, stray comma,
     an unescaped placeholder).
  2. The keys an "Input Format" example advertises fall out of sync with
     the keys the orchestrator actually puts into `input_data` for that
     agent (see `ll_system/orchestrator.py`: `run_classifier_node`,
     `run_specialist_node`, `run_reasoning_node`, `_run_formalizer_node`,
     `_parse_text_to_ir`).

Modeled on `cfl_system/tests/test_pda_contract.py`, but generic across all
ll_system prompts rather than tied to one agent's domain object.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# A schema sketch uses a bare, unquoted `...` as a placeholder for "some
# nested value goes here" (e.g. `"language": { ... }` or
# `{"type": "set_builder", ...}`) — that is not valid JSON and is not meant
# to be. Any other parse failure is a real defect. Detected by stripping out
# quoted string contents first, so an ellipsis that is part of an actual
# string value (e.g. "L = {w ... wᴿ}") does not count.
_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')


def _has_bare_ellipsis(block: str) -> bool:
    return "..." in _STRING_LITERAL.sub('""', block)


def _loads_lenient(block: str) -> object:
    """Parse a ```json example that may contain bare `...` schema-sketch
    placeholders (e.g. `"language": { ... }`), by turning each one into
    `null` first. Ellipses inside quoted strings are left untouched.
    """
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    tmp = _STRING_LITERAL.sub(_protect, block)
    tmp = re.sub(r"\{\s*\.\.\.\s*\}", "{}", tmp)   # "language": { ... }
    tmp = re.sub(r"\[\s*\.\.\.\s*\]", "[]", tmp)   # "operands": [ ... ]
    tmp = tmp.replace("...", "null")               # bare "key": ...
    tmp = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], tmp)
    return json.loads(tmp)

# Required keys for each agent's Output Format example. Every agent must
# produce at least a status/verdict-shaped envelope the orchestrator can
# read (see docs/VERDICT_POLICY.md and `_run_agent` callers in orchestrator.py).
_REQUIRED_OUTPUT_KEYS: dict[str, set[str]] = {
    "ll_classifier": {"prediction", "confidence"},
    "ll_grammar_builder": {"agent_name", "verdict", "confidence"},
    "ll_marker_analyzer": {"agent_name", "verdict", "confidence"},
    "ll_grammar_transformer": {"agent_name", "verdict", "confidence"},
    "ll_substitution_agent": {"agent_name", "verdict", "confidence"},
    "ll_ambiguity_detector": {"agent_name", "verdict", "confidence"},
    "ll_prefix_classes_agent": {"agent_name", "verdict", "confidence"},
    "ll_reasoning_agent": {"verdict", "confidence", "summary", "justification", "primary_method"},
    "ll_formalizer": {"agent_name", "status", "confidence"},
    "ll_input_parser": {"ir", "format", "parse_errors"},
}

# The keys the orchestrator actually assembles into `input_data` for each
# agent (see the corresponding `*_input = {...}` / `_run_agent(..., {...})`
# call sites in orchestrator.py). "Input Format" examples in the prompt must
# document exactly this top-level key set (`retry_params` is optional: it is
# only added when a retry is in progress, so prompts may omit or include it).
_ORCHESTRATOR_INPUT_KEYS: dict[str, set[str]] = {
    "ll_classifier": {"ir", "preprocess_hints"},
    "ll_grammar_builder": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_marker_analyzer": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_grammar_transformer": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_substitution_agent": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_ambiguity_detector": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_prefix_classes_agent": {"ir", "preprocess_hints", "classifier_hint", "retry_params"},
    "ll_reasoning_agent": {
        "ir", "preprocess_hints", "classifier_hint", "specialist_outputs",
        "first_follow_result", "claim_verification", "retry_count", "max_retries",
    },
    "ll_formalizer": {
        "ir", "reasoning_output", "specialist_outputs", "first_follow_result",
        "claim_verification", "proof_was_verified",
    },
    "ll_input_parser": {"source_text", "retry_params"},
}
# Keys that are optional in an example (present on retry only).
_OPTIONAL_INPUT_KEYS = {"retry_params"}


def _prompt_files() -> list[Path]:
    return sorted(PROMPTS_DIR.glob("ll_*.md"))


def _json_blocks(path: Path) -> list[str]:
    return re.findall(r"```json\s*\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)


def _agent_key(path: Path) -> str:
    return path.stem


def _section_blocks(path: Path, heading: str) -> list[str]:
    """Return the fenced ```json blocks that fall under an exact '## heading'
    line (not a longer heading that merely starts with the same words, e.g.
    '## Input Format Detection — Three Formats' vs '## Input Format').
    """
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"\n(?=## )", text)
    out: list[str] = []
    for section in sections:
        first_line = section.lstrip().splitlines()[0] if section.strip() else ""
        if first_line.rstrip() == heading:
            out.extend(re.findall(r"```json\s*\n(.*?)\n```", section, re.S))
    return out


@pytest.mark.parametrize("path", _prompt_files(), ids=lambda p: p.stem)
def test_json_blocks_parse_or_are_schema_sketches(path: Path) -> None:
    for block in _json_blocks(path):
        try:
            json.loads(block)
        except json.JSONDecodeError:
            assert _has_bare_ellipsis(block), (
                f"{path.name}: a ```json block failed to parse and is not a "
                f"'{{ ... }}' schema sketch — likely a real typo:\n{block[:300]}"
            )


@pytest.mark.parametrize("path", _prompt_files(), ids=lambda p: p.stem)
def test_output_format_examples_have_required_keys(path: Path) -> None:
    required = _REQUIRED_OUTPUT_KEYS[_agent_key(path)]
    blocks = _section_blocks(path, "## Output Format")
    complete = []
    for block in blocks:
        try:
            data = _loads_lenient(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            complete.append(data)
    assert complete, f"{path.name}: no parseable JSON example under '## Output Format'"
    for data in complete:
        missing = required - data.keys()
        assert not missing, f"{path.name}: Output Format example missing keys {missing}: {data}"


@pytest.mark.parametrize("path", _prompt_files(), ids=lambda p: p.stem)
def test_input_format_matches_orchestrator_keys(path: Path) -> None:
    agent = _agent_key(path)
    expected = _ORCHESTRATOR_INPUT_KEYS[agent]
    blocks = _section_blocks(path, "## Input Format")
    assert blocks, f"{path.name}: no '## Input Format' section with a JSON example"
    data = _loads_lenient(blocks[0])
    assert isinstance(data, dict)
    actual = set(data.keys())

    required = expected - _OPTIONAL_INPUT_KEYS
    missing = required - actual
    assert not missing, (
        f"{path.name}: Input Format example is missing keys the orchestrator "
        f"actually sends: {missing}"
    )
    unexpected = actual - expected
    assert not unexpected, (
        f"{path.name}: Input Format example documents keys the orchestrator "
        f"never sends: {unexpected} (update the prompt or _ORCHESTRATOR_INPUT_KEYS)"
    )


def test_every_prompt_file_is_covered() -> None:
    covered = set(_REQUIRED_OUTPUT_KEYS) & set(_ORCHESTRATOR_INPUT_KEYS)
    present = {p.stem for p in _prompt_files()}
    assert present == covered, present ^ covered
