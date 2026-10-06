"""Check literal witnesses in the copied-block closure example against its IR."""

import json
from pathlib import Path
import re

from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir


def test_closure_prompt_examples_match_language_and_regular_filter():
    base = Path(__file__).resolve().parents[1]
    ir = json.loads((base / "examples/task_w1w2w1w3.json").read_text(encoding="utf-8"))
    oracle = cfl_oracle_from_ir(ir)
    prompt = (base / "prompts/cfl_closure_reduction.md").read_text(encoding="utf-8")
    section = prompt.split("### Example 1:", 1)[1].split("### Example 2:", 1)[0]
    example = json.loads(re.search(r"```json\s*(.*?)\s*```", section, re.S).group(1))
    evidence = example["evidence"]
    for word in evidence["intersection_examples"]:
        assert re.fullmatch(evidence["regular_language_regex"], word)
        assert oracle(word) is True, word
    for word in evidence["intersection_non_examples"]:
        assert re.fullmatch(evidence["regular_language_regex"], word)
        assert oracle(word) is False, word
