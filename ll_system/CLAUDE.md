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
- Import from `cfl_system.lib.cyk` (CYK membership check)
- Do NOT modify any files in `agent_system/` or `cfl_system/`

## Code style
- Python 3.12+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- `lib/` stays stdlib-only; runtime deps are declared in the root `pyproject.toml`
- Guard `if proof is None` before accessing proof fields in renderer
- BFS word generation with length limits (not recursive DFS)

## Key commands
```bash
# from the repo root
.venv/Scripts/python -m pytest ll_system/tests -q
.venv/Scripts/python -m pytest ll_system/tests/test_first_follow.py -v
.venv/Scripts/python -m ll_system.lib.ll_ir_schema ll_system/examples/format3_simple_ll1.json
.venv/Scripts/python -m ll_system.orchestrator ll_system/examples/format1_anbn_union_ancn.json \
    --mock ll_system/examples/mock/ --save out/
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
- `ll_table_builder`/`orchestrator` now run the full LL(k)-table test (§3.1, `docs/THEORY.md`),
  not just strong LL(k); `is_strong_ll_k` is reported separately for diagnostics
- k-prefix concatenation: X ⊕_k Y = {(xy)[:k] | x ∈ X, y ∈ Y}
- **LL closure (see `docs/THEORY.md` §3.2 for proofs):**
  - LL-languages are **not** closed under ∪: {aⁿbⁿ} and {aⁿcⁿ} are both LL(1), but their union is
    not LL(k) for any k.
  - LL-languages are **not** closed under ∩ REG either. Counterexample:
    L = {aⁿw | w ∈ {b,c}ⁿ, n ≥ 0} is LL(1) (`S → aSX | ε`, `X → b | c`), R = a\*b\* ∪ a\*c\* is
    regular, but L ∩ R = {aⁿbⁿ} ∪ {aⁿcⁿ} is not LL.
  - LL(k)-**languages** coincide with strong-LL(k)-languages [RS, Thm 2]: every LL(k) grammar has
    a structurally equivalent strong-LL(k) grammar for the same language. The strong/non-strong
    distinction only matters at the level of *grammars* (a grammar can be LL(k) without being
    SLL(k), e.g. `S → aAaa | bAba, A → b | ε`), never at the level of languages.
- Hierarchy (strict): REG ⊂ LL(1) ⊊ LL(k) ⊊ LL(k+1) ⊊ ⋃_k LL(k) ⊊ DCFL = LR(1) ⊂ CFL
- LaTeX: use `b·a^i` not `ba^i`, no `\,`

## Current status
All phases through live LLM integration are implemented (models: see `config.py`).
Open work is tracked in the root `TODO.md`. Live test runs: Haiku only, via
`TFL_MODEL_OVERRIDE=claude-haiku-4-5` (see root `CLAUDE.md`).

## Theory reference
`docs/THEORY.md` is the single source of truth for the lemmas/theorems used by the destructive
agents (`substitution_agent` — branch-point argument §3.3 (C); `prefix_classes_agent` — Theorem
4.7.4 [Sh] via not-DCFL §3.3 (A)) and for LL-language closure properties (§3.2). Prompts and
`lib/claim_verifier.py` must not drift from its formulations.
