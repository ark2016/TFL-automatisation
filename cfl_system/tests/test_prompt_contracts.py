"""Contract tests for cfl_system/prompts/*.md (docs/TODO.md §5, §6).

Two things are checked for every fenced ```json block in every prompt:

1. It either parses as valid JSON, or it is a recognizable *pseudo-schema*
   sketch (Input Format sections, "Evidence schema" sub-sections, etc. use
   placeholders like `{ ... }`, `<free text>` or a bare (unquoted)
   `type1 | type2` union — none of that is legal JSON syntax, and none of
   it can appear in valid JSON outside of a string literal). A block that
   fails to parse AND contains none of those markers is a real bug, not an
   intentional sketch, and fails the test.
2. Every block that *does* parse and represents an example of an agent's
   own output (its "agent" field matches, or — for the classifier, which
   has no "agent" field — it has the classifier's own key shape) contains
   all keys the orchestrator/contract requires for that agent. This is the
   same idea as `test_pda_contract.py`'s prompt-vs-code check, generalized
   to every agent instead of just pda_builder.

Prompt files with no fixed single-object contract (cfl_input_parser.md, whose
output shape depends on the `kind` of language IR being produced) are only
checked for rule (1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cfl_system.config import PROMPT_FILES
from cfl_system.lib.agent_output_schema import REQUIRED_KEYS, _NO_FIXED_CONTRACT

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# REQUIRED_KEYS / _NO_FIXED_CONTRACT now live in cfl_system.lib.agent_output_schema
# (TODO.md §3 M) — the same exhaustive per-agent contract also builds the
# output_config.format JSON schema each LiveRunner call sends, so this test
# and the live request stay in sync by construction instead of by hand.


def _iter_json_blocks(path: Path) -> list[tuple[str, str]]:
    """Return [(nearest preceding heading, raw block text), ...]."""
    lines = path.read_text(encoding="utf-8").split("\n")
    heading = "(no heading)"
    blocks: list[tuple[str, str]] = []
    start = None
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading = line.strip()
        if line.strip() == "```json":
            start = i
        elif line.strip() == "```" and start is not None:
            blocks.append((heading, "\n".join(lines[start + 1:i])))
            start = None
    return blocks


_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _is_pseudo_schema(block: str) -> bool:
    """Whether `block` is an intentional non-JSON schema sketch.

    Valid JSON syntax outside of string literals only ever uses
    `{ } [ ] : , ` plus whitespace and literal numbers/true/false/null.
    Strip every quoted string out, and if anything from `< > | ` or a bare
    `...` remains in the skeleton, this block is using one of the
    conventions this codebase's prompts use for a placeholder/type-union
    sketch (e.g. `{ ... }`, `<free text>`, `null | { ... }`,
    `"a" | "b"`), not real JSON — that is the "explicit marker" docs/TODO.md
    §5 asks for, and it is exempt from parsing.
    """
    skeleton = _STRING_RE.sub("", block)
    return bool(re.search(r"\.\.\.|[<>|]", skeleton))


def _agent_examples(agent_name: str, blocks: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    out = []
    for heading, raw in blocks:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("agent") == agent_name:
            out.append((heading, parsed))
        elif agent_name == "classifier" and {"verdict", "confidence", "reasoning"} <= parsed.keys():
            out.append((heading, parsed))
    return out


@pytest.mark.parametrize("agent_name,filename", sorted(PROMPT_FILES.items()))
def test_json_blocks_parse_or_are_marked_pseudo_schema(agent_name, filename):
    path = PROMPTS_DIR / filename
    assert path.exists(), f"prompt file missing: {path}"
    blocks = _iter_json_blocks(path)
    assert blocks, f"{filename}: no ```json blocks found at all"
    bad = []
    for heading, raw in blocks:
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            if not _is_pseudo_schema(raw):
                bad.append(f"{heading!r}: {exc}")
    assert not bad, f"{filename}: invalid JSON block(s) not marked as pseudo-schema:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "agent_name,filename",
    sorted((a, f) for a, f in PROMPT_FILES.items() if a not in _NO_FIXED_CONTRACT),
)
def test_agent_examples_have_required_keys(agent_name, filename):
    path = PROMPTS_DIR / filename
    required = REQUIRED_KEYS.get(agent_name)
    assert required, f"no REQUIRED_KEYS entry for agent {agent_name!r} — add one"
    blocks = _iter_json_blocks(path)
    examples = _agent_examples(agent_name, blocks)
    assert examples, (
        f"{filename}: no parseable example of agent {agent_name!r}'s own output "
        f"found (looked for a block with \"agent\": {agent_name!r})"
    )
    missing = []
    for heading, example in examples:
        gap = required - example.keys()
        if gap:
            missing.append(f"{heading!r}: missing {sorted(gap)}")
    assert not missing, f"{filename}: example(s) missing contract keys:\n" + "\n".join(missing)
