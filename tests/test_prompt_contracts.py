"""Cross-project prompt contracts (all four pipelines in one place).

1. Every ```json fenced block in `<project>/prompts/*.md` parses as JSON.
   Prompts legitimately contain pseudo-JSON (placeholders `<...>`, ellipses
   `...`, bare `a | b` unions outside string literals): those blocks are
   skipped with an explicit reason. A block that fails to parse and has none
   of these markers is a genuine bug; known ones are listed in
   KNOWN_BROKEN as strict xfails (prompts are owned elsewhere).
2. Every top-level key an agent's output schema / REQUIRED_KEYS declares is
   mentioned (as a quoted `"key"`) in that agent's own prompt, so the
   contract the live request enforces cannot silently drift from what the
   prompt tells the model to emit (cf. cfl_system/tests/test_pda_contract.py).
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ["agent_system", "cfl_system", "dcfl_system", "ll_system"]

# (project, prompt file, block index within the file) -> reason. Filled in from
# the first run of this test; empty means no genuinely broken example exists.
KNOWN_BROKEN: dict[tuple[str, str, int], str] = {}

_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _json_blocks(path: Path) -> list[tuple[int, int, str, str]]:
    """[(index, 1-based start line, nearest heading, block text), ...]."""
    lines = path.read_text(encoding="utf-8").split("\n")
    heading, start, out = "(no heading)", None, []
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading = line.strip()
        if line.strip() == "```json":
            start = i
        elif line.strip() == "```" and start is not None:
            out.append((len(out), start + 1, heading, "\n".join(lines[start + 1:i])))
            start = None
    return out


def _pseudo_reason(block: str) -> str | None:
    """Why the block is an intentional non-JSON sketch, or None."""
    if re.search(r'"\.\.\.[^"]*"\s*[:}\]]', block):
        return "string placeholder (\"...description...\")"
    skeleton = _STRING_RE.sub("", block)
    if "..." in skeleton:
        return "ellipsis placeholder"
    if re.search(r"[<>]", skeleton):
        return "<placeholder>"
    if "|" in skeleton:
        return "bare type union (a | b)"
    return None


def _cases() -> list:
    cases = []
    for project in PROJECTS:
        for path in sorted((ROOT / project / "prompts").glob("*.md")):
            for idx, line, heading, block in _json_blocks(path):
                marks = []
                if (project, path.name, idx) in KNOWN_BROKEN:
                    marks.append(pytest.mark.xfail(
                        strict=True, reason=KNOWN_BROKEN[(project, path.name, idx)]))
                cases.append(pytest.param(
                    project, path.name, line, heading, block, marks=marks,
                    id=f"{project}/{path.name}:{line}"))
    return cases


def test_prompts_exist_for_every_project():
    for project in PROJECTS:
        assert list((ROOT / project / "prompts").glob("*.md")), project


@pytest.mark.parametrize("project,fname,line,heading,block", _cases())
def test_json_block_parses(project, fname, line, heading, block):
    reason = _pseudo_reason(block)
    try:
        json.loads(block)
        return
    except json.JSONDecodeError as exc:
        err = exc
    if reason:
        pytest.skip(f"pseudo-JSON sketch ({reason}): {project}/prompts/{fname}:{line} under {heading}")
    pytest.fail(f"{project}/prompts/{fname}:{line} ({heading}): invalid JSON: {err}")


# --- schema keys vs prompt --------------------------------------------------

def _prompt_file(project: str, agent: str) -> Path | None:
    prompts = ROOT / project / "prompts"
    config = importlib.import_module(f"{project}.config")
    files = getattr(config, "PROMPT_FILES", None)
    if files is not None:
        name = files.get(agent)
        return prompts / name if name else None
    return prompts / f"{agent}.md"   # agent_system: <agent>.md


def _schema_cases() -> list:
    cases = []
    for project in PROJECTS:
        schema_mod = importlib.import_module(f"{project}.lib.agent_output_schema")
        for agent, keys in sorted(schema_mod.REQUIRED_KEYS.items()):
            if agent in getattr(schema_mod, "_NO_FIXED_CONTRACT", frozenset()):
                continue
            cases.append(pytest.param(project, agent, sorted(keys), id=f"{project}/{agent}"))
    return cases


@pytest.mark.parametrize("project,agent,keys", _schema_cases())
def test_schema_keys_are_mentioned_in_agent_prompt(project, agent, keys):
    path = _prompt_file(project, agent)
    assert path is not None and path.is_file(), f"no prompt file for {project}/{agent}"
    text = path.read_text(encoding="utf-8")
    missing = [k for k in keys if f'"{k}"' not in text]
    assert not missing, f"{path.name} never mentions output key(s) {missing} of {project}/{agent}"
