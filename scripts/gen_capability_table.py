"""Generate the README pipeline capability tables from docs/pipeline_capabilities.json.

    python scripts/gen_capability_table.py          # print both tables
    python scripts/gen_capability_table.py --write  # rewrite the marked README blocks
    python scripts/gen_capability_table.py --check  # exit 1 if README differs

The "proof_checker agent" row is derived from the presence of a
``*proof_checker.md`` prompt in each pipeline's prompts directory.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "pipeline_capabilities.json"
README = ROOT / "README.md"


def load() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))["pipelines"]


def has_proof_checker(p: dict) -> bool:
    return any((ROOT / p["prompts_dir"]).glob("*proof_checker.md"))


def glance_table(pipes: list[dict]) -> str:
    rows = ["| Pipeline | Language class | Agents | Flagship theorems |", "|---|---|---|---|"]
    for p in pipes:
        rows.append(f"| {p['label']} | {p['class']} | {p['agents']} | {p['theorems']} |")
    return "\n".join(rows)


def differences_table(pipes: list[dict]) -> str:
    def yn(v: bool) -> str:
        return "✓" if v else "—"

    lines = ["| Stage | " + " | ".join(p["id"] for p in pipes) + " |",
             "|---|" + ":-:|" * len(pipes)]

    def row(name: str, vals: list[str]) -> None:
        lines.append(f"| {name} | " + " | ".join(vals) + " |")

    row("Classifier (advisory)", [yn(p["classifier"]) for p in pipes])
    row("Deterministic oracle", [p["oracle"] for p in pipes])
    row("Claim verification", [yn(p["claim_verification"]) for p in pipes])
    row("`proof_checker` agent", [yn(has_proof_checker(p)) for p in pipes])
    row("Retry", [p["retry"] for p in pipes])
    row("Hypothesis inversion", [yn(p["hypothesis_inversion"]) for p in pipes])
    row("Proof output / formalization", [p["proof_output"] for p in pipes])
    row("Agent identity key in the structured-output schema", [p["agent_key"] for p in pipes])
    return "\n".join(lines)


def tables() -> dict[str, str]:
    pipes = load()
    return {"capabilities-glance": glance_table(pipes),
            "capabilities-differences": differences_table(pipes)}


def _pattern(name: str) -> re.Pattern:
    return re.compile(rf"(<!-- BEGIN {name} -->\n)(.*?)(\n<!-- END {name} -->)", re.S)


def render_readme(text: str) -> str:
    text = text.replace("\r\n", "\n")
    for name, table in tables().items():
        pat = _pattern(name)
        if not pat.search(text):
            raise SystemExit(f"README is missing the {name} markers")
        text = pat.sub(lambda m: m.group(1) + table + m.group(3), text)
    return text


def main(argv: list[str]) -> int:
    if "--write" in argv or "--check" in argv:
        old = README.read_text(encoding="utf-8").replace("\r\n", "\n")
        new = render_readme(old)
        if "--check" in argv:
            if old != new:
                print("README capability tables are stale; run scripts/gen_capability_table.py --write")
                return 1
            return 0
        README.write_text(new, encoding="utf-8", newline="\n")
        return 0
    for name, t in tables().items():
        print(f"<!-- {name} -->\n{t}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
