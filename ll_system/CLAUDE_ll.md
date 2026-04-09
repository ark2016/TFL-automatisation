# Project: LL Property Agent System

## What this is
Agentic system for checking LL(k) property of context-free languages and grammars.
Extension of REG (`agent_system/`) and CFL (`cfl_system/`) agent systems.
Full spec: `ll_system/tz_ll_agent_system.md`

## Architecture rules
- Pure functions in `lib/` — no LLM calls, fully testable
- Prompts in `prompts/` — externalized, not hardcoded
- JSON Schema for all inter-module contracts
- FIRST/FOLLOW oracle verifies all grammar candidates algorithmically
- Classifier is ADVISORY ONLY — all 6 specialist agents always dispatched
- Selective retry: reasoning agent specifies which agents to re-run
- Format 3 fast path: grammar LL(k) check goes directly to oracle, no agents

## Dependencies on other systems
- Import from `agent_system.lib.ir_schema` (base IR schema)
- Import from `agent_system.lib.oracle` (base oracle framework)
- Import from `agent_system.lib.word_generator` (word generation)
- Import from `cfl_system.lib.cyk_oracle` (CYK membership check)
- Import from `cfl_system.lib.cfg_utils` (grammar utilities)
- Do NOT modify any files in `agent_system/` or `cfl_system/`

## Code style
- Python 3.11+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- No dependencies beyond stdlib + jsonschema for Phase 1
- Guard `if proof is None` before accessing proof fields in renderer
- BFS word generation with length limits (not recursive DFS)

## Key commands
```bash
cd ll_system/
python -m pytest tests/ -v
python -m pytest tests/test_first_follow.py -v
python -m lib.ll_ir_schema examples/format3_simple_ll1.json
python orchestrator.py examples/format1_anbn_union_ancn.json --mock examples/mock/
```

## Allowed actions
- Create/edit/delete any files inside `ll_system/`
- Run python, pytest, pip install in project venv
- Import from `agent_system/` and `cfl_system/` (read only)
- Do NOT modify files outside `ll_system/`

## Critical reminders
- Substitution method ≠ Pumping Lemma — different technique, don't confuse
- Language vs grammar: left recursion ≠ not LL (language may still be LL)
- LL(k) ⊂ LL(k+1) strictly; minimum k is part of the answer
- FIRST/FOLLOW fixed-point computation must converge (max iterations guard)
- k-prefix concatenation: X ⊕_k Y = {(xy)[:k] | x ∈ X, y ∈ Y}
- LL closure: LL ∩ REG = LL; LL ∪ LL ≠ necessarily LL
- Hierarchy: REG ⊂ LL(1) ⊂ LL(k) ⊂ LR(k) = DCFL ⊂ CFL
- LaTeX: use `b·a^i` not `ba^i`, no `\,`

## Current phase
Phase 1: Core Infrastructure (pure fn, no LLM)
See §9 Phase 1 in the spec for the full list.
