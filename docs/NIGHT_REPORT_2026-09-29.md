# Night report 2026-09-29

Branch `lean-formalization` (HEAD `0a7324e` at start; the night's work is uncommitted in the working tree).
No Anthropic API calls were made (mock mode, the root `conftest.py` blocks the client). Lean type-checking ran
locally in Docker (`tfl-lean4`: Lean v4.33.0 + Mathlib v4.33.0 + langlib).

## What was done, by round

**Round 1 (parallel agents)**
- Model migration Sonnet 5 -> Sonnet 5.5 (`claude-sonnet-5-5`) in all four `config.py`; `MODEL_PRICING` entry
  ($2 / $10, cache read $0.20, cache write 5m $2.50; the 1h write is not modelled); `claude-sonnet-5` kept as legacy
  in the price table. Tests, `.env.example`, README/CLAUDE.md/CONTRIBUTING/TODO/tz updated.
- CFL: `lean_formalize_node` (after the Markdown `formalize_node`, before `assemble_result_node`), flag
  `--formalize` / `TFL_FORMALIZATION`, prompt `cfl_lean_formalizer.md`, mocks for `task_anbncn`, tests
  `test_lean_formalize.py`.
- DCFL: same node between reasoning/retry_planner and the renderer; prompt `dcfl_lean_formalizer.md`; mocks for
  `dcfl_anbncm` and exam_01..04; tests `test_lean_formalize.py`.
- IR -> Lean translator: grammars in CFL statements, exponent notation with a decidable companion, reversals and
  palindromes, predicate filters (`predicate_to_lean`); golden tests unchanged and green.

**Round 2**
- Five reference proofs, all `proved` (allowed axioms only, replay OK): `AnBnAnNotCF` (CFL, non-CF via the new
  closure lemma `not_isContextFree_of_slice`), `EvenPalGrammar_CF` (CFL), `AnBnCm_DCF`, `AnBnCmPos_DCF`,
  `AiBjCkNeq_NotDCF` (DCFL). New lemmas in `TflLean/Lemmas.lean`, including the bridge `DCFL ⊆ CFL`
  (`isContextFree_of_isDCF`, `not_isDCF_of_not_isContextFree`), which makes the `non_dcfl` direction provable.
- Docker-conditional tests for the examples in all three projects.

**Round 3 (this one)**: README, TODO, CLAUDE.md, spec wording (DCFL §7.3: `non_dcfl` is now provable), this report.

## What did not work / is left, and why
- Grammar-defined DCFL (exam_04): no grammar -> DPDA bridge in the image; the mock is an honest refusal.
- CFL positive direction: only `EvenPalGrammar_CF` compiled; an Opus formalizer will likely burn retries on
  other grammars. A worked `a^n b^n` via `is_CF_iff_isContextFree` is open.
- cfl-10 `{a^(2^n)}` renders to `None` (nested exponent), so langlib's `notCF_unaryPow2` is unused.
- Methods with no Lean lemmas (closure with REG, Parikh, substitution, Yu, Shallit 4.7.4) are not formalized.
- Renderers for CFL/DCFL do not show `result["formalization"]`; DCFL `lean_formalizer` has no structured-output
  schema; `lean_formalizer` config exists only in CFL/DCFL `config.py`.
- `_LEAN_AVAILABLE_LEMMAS` in `cfl_system/orchestrator.py` does not list the new `TflLean.*` lemmas.
- Stale text outside my file list: `dcfl_system/CLAUDE.md` ("Formal verification": says `non_dcfl` is unprovable) and
  the comment on the Lemmas layer in `agent_system/docker/Dockerfile.lean4`.
- Branches: nothing deleted. `lean-mathlib` is contained in `lean-formalization` but is not in `main` (PR #10 open);
  no other local branch exists besides `main`. Untracked `f1.lean` in the repo root predates the work.

## Numbers
- Tests: see the final line of this section, filled from the last full run
  (`pytest -q -n 4 --dist loadfile`): 2826 passed, 8 skipped, 2 xfailed in ~8 min. `pytest tests -q`: 48 passed.
- Proofs: 5 new reference proofs, 9 example files in `TflLean/Examples/` in total, all `proved`.
- Image: ~16 GB; Lean-project layers ~20 min to build (`cache get` ~7 min, `TflLean.Langlib` ~67 s, export ~8 min);
  a file with `import TflLean` type-checks in seconds.
- Live cost: $0 (no live runs).

## Needs a decision from the user
1. **PR #10** (`lean-mathlib`) is open; its commit is an ancestor of `lean-formalization`. Close it in favour of
   #11, or merge it first; only then delete the `lean-mathlib` branch (local and remote).
2. **PR #11** (`lean-formalization`): review and merge; this working tree is not committed yet.
3. **Live comparison of Sonnet 5.5 vs Sonnet 5** (2-3 tasks, Haiku is not relevant here) and a first live run of
   the prover on CFL/DCFL: only on your explicit request with an agreed budget (order of $1). Also unverified:
   whether server-side refusal fallback works for Sonnet 5.5 (currently only Opus/Fable).
4. Whether to make CI run Lean (registry/`.lake` cache) or keep only the Docker-free `lean_ir` unit tests.
