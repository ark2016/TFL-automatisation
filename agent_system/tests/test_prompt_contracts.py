"""Contract tests for agent_system/prompts/*.md (TODO.md section 6).

Mirrors cfl_system/tests/test_prompt_contracts.py:

1. Every fenced ```json block parses as JSON or is an explicit pseudo-schema
   sketch (placeholders `{ ... }`, `<text>`, bare `a | b` unions).
2. Every parseable block that is an example of the agent's own output
   (`"module": <agent>`) carries all keys of the agent's output contract
   (`agent_system.lib.agent_output_schema.REQUIRED_KEYS`).
3. Every input key listed in `_INPUT_KEYS` (a hand-maintained copy of what
   `graph.py` builds for each agent; update it together with graph.py) is
   mentioned, quoted or backticked, in that agent's prompt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_system.lib.agent_output_schema import REQUIRED_KEYS, _NO_FIXED_CONTRACT

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_FILES = sorted(p.name for p in PROMPTS_DIR.glob("*.md"))
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')

def _iter_json_blocks(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text(encoding="utf-8").split("\n")
    heading, start, blocks = "(no heading)", None, []
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading = line.strip()
        if line.strip() == "```json":
            start = i
        elif line.strip() == "```" and start is not None:
            blocks.append((heading, "\n".join(lines[start + 1:i])))
            start = None
    return blocks


def _is_pseudo_schema(block: str) -> bool:
    skeleton = _STRING_RE.sub("", block)
    return bool(re.search(r"\.\.\.|[<>|]", skeleton))


@pytest.mark.parametrize("filename", PROMPT_FILES)
def test_json_blocks_parse_or_are_marked_pseudo_schema(filename):
    bad = []
    for heading, raw in _iter_json_blocks(PROMPTS_DIR / filename):
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            if not _is_pseudo_schema(raw):
                bad.append(f"{heading!r}: {exc}")
    assert not bad, f"{filename}: invalid JSON block(s):\n" + "\n".join(bad)


def _own_examples(agent: str, path: Path) -> list[tuple[str, dict]]:
    out = []
    for heading, raw in _iter_json_blocks(path):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (
            parsed.get("module") == agent
            # proof_checker / retry_planner outputs carry no `module` field
            or (agent in ("proof_checker", "retry_planner") and REQUIRED_KEYS[agent] <= parsed.keys())
        ):
            out.append((heading, parsed))
    return out


@pytest.mark.parametrize("agent", sorted(set(REQUIRED_KEYS) - _NO_FIXED_CONTRACT))
def test_agent_examples_have_required_keys(agent):
    path = PROMPTS_DIR / f"{agent}.md"
    assert path.exists(), f"prompt file missing: {path}"
    required = REQUIRED_KEYS[agent]
    own = _own_examples(agent, path)
    # Contract: no block that claims to be this agent's output may miss keys.
    missing = [f"{h!r}: missing {sorted(required - o.keys())}" for h, o in own if required - o.keys()]
    assert not missing, f"{path.name}: example(s) missing contract keys:\n" + "\n".join(missing)
    # The output-format sketch (possibly pseudo-JSON) must name every key.
    text = path.read_text(encoding="utf-8")
    for key in required:
        assert f'"{key}"' in text, f"{path.name}: contract key {key!r} never appears in the prompt"


# ---- Input Format vs graph.py ---------------------------------------------

_INPUT_KEYS = {
    "classifier": {"ir", "hypothesis"},
    "pumping_agent": {"ir", "hypothesis", "classifier", "grammar_facts", "student_notes", "retry_context"},
    "nerode_agent": {"ir", "hypothesis", "classifier", "grammar_facts", "student_notes", "retry_context"},
    "closure_agent": {"ir", "hypothesis", "classifier", "grammar_facts", "student_notes", "retry_context"},
    "dfa_builder": {"ir", "hypothesis", "classifier", "grammar_facts", "student_notes", "retry_context"},
    "re_builder": {"ir", "hypothesis", "classifier", "grammar_facts", "student_notes", "retry_context"},
    "reasoning_agent": {
        "ir", "hypothesis", "specialist_outputs", "oracle_test", "closure_verification",
        "proof_checker", "retry_round", "previous_issues",
    },
    "proof_checker": {"ir", "student_notes"},
}


@pytest.mark.parametrize("agent,keys", sorted(_INPUT_KEYS.items()))
def test_input_keys_built_by_graph_are_mentioned_in_prompt(agent, keys):
    text = (PROMPTS_DIR / f"{agent}.md").read_text(encoding="utf-8")
    missing = sorted(k for k in keys if f'"{k}"' not in text and f"`{k}`" not in text)
    assert not missing, f"{agent}.md does not mention input key(s) {missing}"
