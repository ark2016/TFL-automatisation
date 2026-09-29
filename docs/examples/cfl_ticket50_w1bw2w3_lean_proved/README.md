# CFL: L = {w₁ b w₂ w₃ | |w₁| = |w₂| = |w₃|, w₂ ∈ (ab)*} (ticket 50) — non-CFL, proved in Lean

Live run from TFL Lab on 2026-09-29 (run `5038df712c77`), production models
(Opus 5.5 for specialists and reasoning, Sonnet 5.5 for classification).

| | |
|---|---|
| Verdict | `non_cfl`, confidence 0.98 (`verified`, basis: Lean proof); before formalization 0.85 |
| Pipeline | 13 calls, ≈ $2.39 |
| Formalization | separate "Formalize" step, `proved` on attempt 1 (Opus 5.5), ≈ $1.59 |
| Run total | ≈ $3.98 |
| Axioms | `propext`, `Classical.choice`, `Quot.sound` |

The Lean proof uses the CFL pumping lemma from langlib on the word `a^{2p} b (ab)^p a^{2p}` with `i = 0`:
the length argument (`3n+1`) and the position of the middle `b` / the last letter of `w₂` are
checked by case analysis on where `u` ends.

Files:
- `input.json` — the task IR (same as `cfl_system/examples/task_ticket50_w1bw2w3.json`)
- `result.{json,md,html}` — pipeline result with the `formalization` block
- `progress.jsonl` — per-node progress events
- `proof.lean` — statement generated from the IR plus the proof body written by the model

Note: the informal proof in `result.md` cites the hint from the classifier for `p = 4`
(`a⁸b(ab)⁵a⁸`), which is a known wrong instance of the built-in `pumping_cfl` tool; the parametric
argument itself is correct and is what the Lean proof formalizes.

Re-check the proof locally (Docker image `tfl-lean4`, see the root README):

```bash
agent_system/docker/run_check.sh docs/examples/cfl_ticket50_w1bw2w3_lean_proved/proof.lean
```
