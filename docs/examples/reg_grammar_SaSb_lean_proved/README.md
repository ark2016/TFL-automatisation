# REG: S → SaSb | ε | A, A → bb | aa | bSb — non-regular, proved in Lean

Live run from TFL Lab on 2026-09-29 (run `99ec5c2964e7`), production models
(Opus 5.5 for specialists and reasoning, Sonnet 5.5 for classification).

| | |
|---|---|
| Verdict | `non_regular`, confidence 0.98 (`verified`, basis: Lean proof) |
| Pipeline | 7 calls, ≈ $0.89 |
| Formalization | separate "Formalize" step, `proved` on attempt 1 (Opus 5.5), ≈ $2.11 |
| Axioms | `propext`, `Classical.choice`, `Quot.sound` |

Files:
- `input.json` — the task IR
- `result.{json,md,html}` — pipeline result with the `formalization` block
- `progress.jsonl` — per-node progress events
- `proof.lean` — statement generated from the IR plus the proof body written by the model

Re-check the proof locally (Docker image `tfl-lean4`, see the root README):

```bash
agent_system/docker/run_check.sh docs/examples/reg_grammar_SaSb_lean_proved/proof.lean
```
