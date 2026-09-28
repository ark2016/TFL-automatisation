# Project: CFL Agent System

## What this is
Agentic system for analyzing context-free language properties (КС-свойство).
Extension of the existing REG agent system in `agent_system/`.
Full spec: `cfl_system/tz_cfl_agent_system.md`

Theory (lemma formulations, worked examples): `docs/THEORY.md` §2 is the single source of truth for CFL closure/pumping examples used in the prompts below.

## Architecture rules
- Pure functions in `lib/` — no LLM calls, fully testable
- Prompts in `prompts/` — externalized, not hardcoded
- JSON Schema for all inter-module contracts
- Oracle testing (CYK / PDA) before any LLM reasoning
- Classifier is ADVISORY ONLY — all 9 specialist agents always dispatched
- Selective retry: reasoning agent specifies which agents to re-run

## Dependencies on REG system
- May import from `agent_system.lib.ir_schema` (base IR schema)
- May import from `agent_system.lib.oracle` (base oracle framework)
- May import from `agent_system.lib.word_generator` (base word generator)
- Do NOT modify any files in `agent_system/`

## External tool requirements

- **Graphviz `dot` binary** — required for rendering PDA state diagrams as
  inline SVG in the HTML reports (`cfl_renderer.py`). The renderer invokes
  `dot -Tsvg` via subprocess. If `dot` is not on PATH, the renderer falls
  back to a mermaid diagram (requires internet for CDN). Install:
  - Windows: download from <https://graphviz.org/download/> (add `bin/` to PATH)
  - macOS: `brew install graphviz`
  - Linux: `apt install graphviz` / `dnf install graphviz`
  - Verify: `dot -V` should print the version.

## Code style
- Python 3.12+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- `lib/` stays stdlib-only; runtime deps are declared in the root `pyproject.toml`
- Guard `if proof is None` before accessing proof fields in renderer (recurring bug)

## Current status
All phases through live LLM integration are implemented (models: see `config.py`).
Open work is tracked in the root `TODO.md`. Live test runs: Haiku only, via
`TFL_MODEL_OVERRIDE=claude-haiku-4-5` (see root `CLAUDE.md`).

## Key commands
```bash
# from the repo root
.venv/Scripts/python -m pytest cfl_system/tests -q
.venv/Scripts/python -m cfl_system.orchestrator cfl_system/examples/task_w1w2w1w3.json \
    --mock cfl_system/examples/mock/ --save out/
TFL_MODEL_OVERRIDE=claude-haiku-4-5 .venv/Scripts/python -m cfl_system.orchestrator \
    cfl_system/examples/task_w1w2w1w3.json --live --verbose --save out/
```

## Allowed actions
- Create/edit/delete any files inside `cfl_system/`
- Run python, pytest, pip install in project venv
- Import from `agent_system/` (read only)
- Do NOT modify files outside `cfl_system/`

## Critical reminders
- CYK timeout: 10s for words > 100 chars
- PDA sim: max_stack_depth=1000, max_steps=10000
- Parikh: semilinearity is NECESSARY but NOT SUFFICIENT for CFL (also for bounded languages:
  Ginsburg–Spanier needs *stratified* semilinear sets — `check_stratification` only gives hints)
- PDA contract: prompts use topmost-first `push` + sibling `acceptance_mode`; the simulator wants
  last-pushed-on-top + `accept_mode` — convert agent PDAs with `normalize_agent_pda()`
- {ww} is NOT CFL — verify decomposition claims
- LaTeX: use `b·a^i` not `ba^i`, no `\,`


## Lean 4 formalization (R-Lean, opt-in)
- `formalize_node` (agent `formalizer`) outputs Markdown only, no Lean — untouched.
- `lean_formalize_node` (agent `lean_formalizer`, `prompts/cfl_lean_formalizer.md`) is a separate step after the
  verdict: `--formalize` / `TFL_FORMALIZATION=1`. Statement from `lib/lean_ir.py`, agent writes only the proof body,
  `agent_system.lib.type_check` checks it in Docker; `proved` => verified 0.98 (`apply_lean_gate`). Spec: `tz_cfl_agent_system.md` §5.5.
- Live runs of this step cost Opus calls: only on explicit request (root `CLAUDE.md`)