"""README capability tables must equal the ones generated from docs/pipeline_capabilities.json."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_capability_table", ROOT / "scripts" / "gen_capability_table.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_readme_matches_generated_tables():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert gen.render_readme(readme) == readme, (
        "README capability tables are stale: run `python scripts/gen_capability_table.py --write`"
    )


def test_proof_checker_row_follows_prompts():
    pipes = {p["id"]: p for p in gen.load()}
    assert gen.has_proof_checker(pipes["REG"]) and gen.has_proof_checker(pipes["CFL"])
    assert not gen.has_proof_checker(pipes["DCFL"]) and not gen.has_proof_checker(pipes["LL"])


def test_pipeline_ids_cover_all_four():
    assert [p["id"] for p in gen.load()] == ["REG", "CFL", "DCFL", "LL"]
