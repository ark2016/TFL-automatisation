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
- May import from `cfl_system.lib.cyk` (CYK membership)
- Do NOT modify any files in `agent_system/` or `cfl_system/`

## Code style
- Python 3.12+, type hints everywhere
- Dataclasses or Pydantic for structured data
- Every pure function must have unit tests in `tests/`
- `lib/` stays stdlib-only; runtime deps are declared in the root `pyproject.toml`
- Guard `if proof_sketch is None` before accessing proof fields in renderer
- LangGraph for orchestrator

## DCFL theory reminders
Single source of truth for formulations: `docs/THEORY.md` §1. One-line summaries below; read the
file itself before touching any prompt, dataclass or oracle check that depends on them.

- LR(k) grammars generate exactly DCFL
- Every DCFL has an LR(1) grammar (Knuth); L$ is LR(0)
- DCFL closed under: complement (~), inverse homomorphism (h⁻¹), ∩ REG (via DPDA × DFA, not Table 1),
  right quotient with regular (L/R), MIN(L), MAX(L), haspref(L), L_$ ([GG]; THEORY.md §1.3–1.4)
- DCFL NOT closed under: union, intersection (DCFL∩DCFL), concatenation, Kleene star, reversal, homomorphism
- **§1.1 — двухсловная лемма о накачке (лемма Ю, [Yu]).** Два слова xy, xz ∈ L с общим
  префиксом |x| > p и ⁽¹⁾y = ⁽¹⁾z; условие (1) — пара (x₂, x₄) в любом месте x, |x₂x₃x₄| ≤ p;
  условие (2) — x₂ в последних p символах x, синхронно с y₂/z₂. Одиночная формулировка условия (1)
  ложна (отвергает DCFL {aⁿbⁿcᵐ}).
- **§1.2 — теорема 4.7.4 [Sh].** DCFL ⇒ хотя бы один класс Нероуда бесконечен; контрапозиция:
  все классы конечны ⇒ не DCFL. Ограничение: мёртвый класс D должен быть конечен, иначе
  `not_applicable`.
- **§1.3 — лемма о продолжении.** L — DCFL ⇒ haspref(L), L_$ = {x$y | x∈L, xy∈L} — DCFL;
  L_$ ∩ R ∉ CFL для регулярного R ⇒ L ∉ DCFL.
- Inherently ambiguous → not UnambCF → not DCFL
- Format 2: analyze the LANGUAGE, not the grammar (ambiguous grammar ≠ non-DCFL language)
- exam examples: wvaavRwR, u1au2_u3au4, anb_cnbn are all NON-DCFL (THEORY.md §1.6–1.8); the old
  dcfl reference verdicts were wrong

## Current status
All phases through live LLM integration are implemented (models: see `config.py`).
Open work is tracked in the root `TODO.md`. Live test runs: Haiku only, via
`TFL_MODEL_OVERRIDE=claude-haiku-4-5` (see root `CLAUDE.md`).

## Formal verification
Oracle tests (CYK, word sampling, counterexamples) verify the specialists' proofs. On top of them an
optional Lean 4 step (`lean_formalize_node`, `docs/VERDICT_POLICY.md` R-Lean; spec `tz_dcfl_agent_system.md` §7.3):
`lib/lean_ir.render_statement` generates `is_DCF L` / `¬ is_DCF L` from the IR, the `lean_formalizer` agent
(`prompts/dcfl_lean_formalizer.md`) writes only the proof body, `agent_system.lib.type_check` compiles it in Docker.
`proved` ⇒ verified 0.98 and outranks R1–R4′ (`_apply_lean_gate`). Off by default (`--formalize` /
`TFL_FORMALIZATION=1`); live only on explicit request. Both directions are provable in the image: `dcfl` via an
explicit DPDA (or the `{aⁿbⁿcᵐ}` transfer lemma), `non_dcfl` via DCFL closure (complement / ∩ REG), the inclusion
DCFL ⊆ CFL (`TflLean.isContextFree_of_isDCF`) and refuting the resulting context-free property
(`TflLean.not_isContextFree_of_slice` / CF pumping); reference proof
`agent_system/docker/tfl_lean/TflLean/Examples/AiBjCkNeq_NotDCF.lean`. A `non_dcfl` claim with no such route (e.g. one
that needs Shallit or inherent ambiguity) still ends as `error` (the agent gives up honestly).

## Key commands
```bash
# from the repo root
.venv/Scripts/python -m pytest dcfl_system/tests -q
.venv/Scripts/python -m dcfl_system.lib.dcfl_ir_schema dcfl_system/examples/task_wvaavRwR.json
.venv/Scripts/python -m dcfl_system.orchestrator dcfl_system/examples/task_wvaavRwR.json \
    --mock dcfl_system/examples/mock/ --save out/
```
