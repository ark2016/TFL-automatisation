# Project: CFL Agent System

## What this is
Agentic system for analyzing context-free language properties (КС-свойство).
Extension of the existing REG agent system in `agent_system/`.
Full spec: `cfl_system/tz_cfl_agent_system.md`

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
- Python 3.11+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- No dependencies beyond stdlib + jsonschema for Phase 1
- Guard `if proof is None` before accessing proof fields in renderer (recurring bug)

## Current phase
Phase 2: Agents + Orchestrator (complete)
- Phase 1: 15 pure-fn modules in lib/, 275+ tests
- Phase 2: 15 prompts, orchestrator with MockRunner, mock outputs for 4 tasks, E2E tests
- Phase 3 next: Lean 4 formalization
- Phase 4 next: Live LLM integration

## Key commands
```bash
cd cfl_system/
python -m pytest tests/ -v
python -m lib.cfl_ir_schema examples/task_w1w2w1w3.json
python orchestrator.py examples/task_w1w2w1w3.json --mock examples/mock/
```

## Allowed actions
- Create/edit/delete any files inside `cfl_system/`
- Run python, pytest, pip install in project venv
- Import from `agent_system/` (read only)
- Do NOT modify files outside `cfl_system/`

## Critical reminders
- CYK timeout: 10s for words > 100 chars
- PDA sim: max_stack_depth=1000, max_steps=10000
- Parikh: semilinearity is NECESSARY but NOT SUFFICIENT for CFL
- {ww} is NOT CFL — verify decomposition claims
- LaTeX: use `b·a^i` not `ba^i`, no `\,`


## Out of scope
- Lean 4 formalization — cancelled until proof templates are manually written and compiled
- formalize_node outputs Markdown only, no Lean code generation