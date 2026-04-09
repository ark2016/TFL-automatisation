# Project: DCFL Agent System

## What this is
Agentic system for analyzing determinism of context-free languages (DCFL property).
Third in the TFL agent system family: REG → CFL → DCFL.
Full spec: dcfl_system/tz_dcfl_agent_system.md

## Architecture rules
- Pure functions in `lib/` — no LLM calls, fully testable
- Prompts in `prompts/` — externalized, not hardcoded
- JSON Schema for all inter-module contracts
- Oracle testing (CYK, word sampling) before any LLM reasoning
- Classifier is ADVISORY ONLY — all 5 specialist agents always dispatched
- Selective retry: reasoning agent specifies which agents to re-run with hints

## 5 Specialist agents
1. `stack_strategy` — prove DCFL by reasoning about stack phases/separators
2. `closure_reduction` — prove DCFL or non-DCFL via Table 1 closure properties (~, h⁻¹, ∩REG)
3. `dcfl_pumping` — prove non-DCFL via DCFL pumping lemma (paired words)
4. `shallit` — prove non-DCFL via Shallit's lemma (infinite equivalence classes)
5. `inh_ambiguity` — prove non-DCFL via inherent ambiguity (all grammars ambiguous)

## Dependencies on other systems
- May import from `agent_system.lib.ir_schema` (base IR schema)
- May import from `agent_system.lib.oracle` (base oracle framework)
- May import from `agent_system.lib.word_generator` (base word generator)
- May import from `cfl_system.lib.cyk_oracle` (CYK membership oracle)
- Do NOT modify any files in `agent_system/` or `cfl_system/`

## Code style
- Python 3.11+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- No dependencies beyond stdlib + jsonschema for Phase 1
- Guard `if proof_sketch is None` before accessing proof fields in renderer
- LangGraph for orchestrator

## DCFL theory reminders
- LR(k) grammars generate exactly DCFL
- For every DCFL there exists an SLR(1) grammar
- DCFL closed under: complement (~), inverse homomorphism (h⁻¹), ∩ REG (via DPDA × DFA, not Table 1)
- DCFL NOT closed under: union, intersection (DCFL∩DCFL), concatenation, Kleene star, reversal, homomorphism
- DCFL pumping lemma ≠ CFL pumping lemma (two words, two conditions: prefix-only + synchronized suffix)
- Shallit's lemma: ∀ infinite M ∃ infinite homogeneous M' ⊆ M (negation: ∃ M with no homogeneous subset)
- Inherently ambiguous → not UnambCF → not DCFL
- Format 2: analyze the LANGUAGE, not the grammar (ambiguous grammar ≠ non-DCFL language)

## Current phase
Phase 1: Core Infrastructure (pure fn, no LLM)
See §10 Phase 1 in the spec for the full list.

## Formal verification
NOT implemented. No Lean/Coq proof checking — not in scope.
Proofs verified only via oracle tests (CYK, word sampling, counterexamples)
and LLM-based reasoning.

## Key commands
```bash
cd dcfl_system/
python -m pytest tests/ -v
python -m lib.dcfl_ir_schema examples/task_wvaavRwR.json
python orchestrator.py examples/task_wvaavRwR.json --mock examples/mock/
```
