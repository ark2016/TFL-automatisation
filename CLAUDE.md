# TFL-automatisation

Multi-agent LangGraph pipelines for Theory of Formal Languages problems (ИУ-9).
Overview and architecture: `README.md`. Open work: `TODO.md` (audit backlog, prioritised).
Verdict/confidence rules: `docs/VERDICT_POLICY.md` — change the policy there first.

## Projects
- `agent_system/` — REG (regularity). Spec: `agent_system/tz_tfl_agent_system.md`
- `cfl_system/` — CFL. Spec + notes: `cfl_system/tz_cfl_agent_system.md`, `cfl_system/CLAUDE.md`
- `dcfl_system/` — DCFL. `dcfl_system/tz_dcfl_agent_system.md`, `dcfl_system/CLAUDE.md`
- `ll_system/` — LL(k). `ll_system/tz_ll_agent_system.md`, `ll_system/CLAUDE.md`
- `ui_server/` — TFL Lab local web UI (stdlib server + single-page frontend)

Import direction: cfl may import from agent_system; dcfl / ll may import from agent_system and cfl_system. Never the reverse.

## Existing code (reference, do not modify)
- `pumping_lemma/` — previous pumping lemma implementation
- `reverse_morfism/` — inverse homomorphism solver
Their tests are not part of the suite: `pumping_lemma/tests` calls the real Anthropic API.

## Models and LLM calls
- Models, per-agent `EFFORT`, `MAX_TOKENS`, `REFUSAL_FALLBACK` live in each project's `config.py`
  (Opus 5.5 for reasoning agents, Sonnet 5.5 for parsing/classification, Haiku 4.5 for JSON repair).
  Change them in all four projects together.
- Theory statements used by prompts: `docs/THEORY.md` — change lemma formulations there first, then in prompts.
- Shared LLM client: `agent_system/lib/llm_client.py` (`AnthropicClient`) — request kwargs (adaptive thinking +
  effort; `temperature` only for legacy models), typed API errors (`FatalAPIError`/`RetryableAPIError`) with
  backoff, the cross-pipeline concurrency semaphore (`TFL_MAX_CONCURRENCY`), and `UsageTracker`. `cfl_system` /
  `dcfl_system` / `ll_system`'s `LiveRunner` are thin wrappers around it — change request-building kwargs there,
  not in each `LiveRunner` separately. Structured outputs (`output_config.format`, per-agent JSON schema) are the
  primary response-parsing path; `agent_output_schema.py` in each project builds the schema, with automatic
  fallback to the legacy brace/fence extraction.
- Live API runs (any model) happen **only on the user's explicit request, with a budget agreed beforehand** (order of $1 per
  session); never as an automatic step of a workflow, round or review. All verification defaults to mock mode. When a live run
  is requested, use Haiku only (`TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live`), state the estimated cost first, and run the
  smallest task set that answers the question. Opus runs cost real money — only on request.
- `TFL_FORMALIZATION=1` (also `true`/`yes`/`on`) enables the Lean formalization step (`agent_system` builds a fixed theorem statement from the IR and asks its formalizer for only the proof body; `cfl_system`/`dcfl_system` run `lean_formalize_node`; `ll_system` has no Lean path) by default for every
  run, without passing `--formalize` each time; unset/other values keep it opt-in per `--formalize`. This only
  turns on the pipeline step; it does not by itself spend API budget. Within that step, Lean **type-checking**
  (`agent_system/lib/type_check.py`, `docker run ... lake env lean`) runs locally in Docker as ordinary pipeline
  work, no live-call rule applies to it. Writing the Lean **proof body** is an LLM call and follows the live-run
  rule above like every other agent call: mock mode by default, live only on explicit request with an agreed
  budget. Formalization is also a separate entry over a finished run (`python -m <system>.formalize <run_dir>`, "Formalize" button in TFL Lab; needs `confirm_spend`); in-run it stays opt-in. See README.md "R-Lean architecture" and `docs/VERDICT_POLICY.md`'s R-Lean rule.
- Eval set: `docs/EVAL_SET.md` (74 tasks, expected verdicts) is run by `tfl-eval` (`tfl_eval/`), which reports
  accuracy/calibration metrics — see `README.md` and `TODO.md` §7. Mock mode by default; `--live` only on request.
  The current manifest contains 25 trap tasks. `docs/EVAL_RESULTS.md` records a historical Haiku run over 24 traps
  (2026-09-27); read it before claiming a
  pipeline is or isn't calibrated; a full 74-task run and a post-C2 re-run are still open.
- Model prices live in each project's `config.py` alongside `EFFORT`/`MAX_TOKENS` (see "Models and LLM calls"
  above) — change the pricing table there, in all four projects together, not in docs.

## Commands
```bash
# Tests: pyproject.toml testpaths includes tfl_eval/tests and excludes the legacy
# pumping_lemma/tests that call the real Anthropic API. The root conftest also
# strips ANTHROPIC_API_KEY and stubs anthropic.Anthropic.
.venv/Scripts/python -m pytest -q

# Any pipeline, shared CLI contract (writes <stem>_result.{json,md,html})
.venv/Scripts/python -m cfl_system.orchestrator <ir.json> --save DIR [--live] [--verbose]
.venv/Scripts/python -m agent_system <ir.json> --save DIR [--live]

# TFL Lab (no run timeout, manual cancel; runs persist in run.json; progress.jsonl / partial_result.json per run dir)
.venv/Scripts/python -m ui_server.server --port 8765
```

## Allowed actions
- Create/edit/delete files in the four projects, `ui_server/`, root docs and CI when the task calls for it
- Run python, pytest, pip install in the project venv; docker for Lean 4 verification
- Do NOT modify `pumping_lemma/` or `reverse_morfism/`
- Do NOT run commands that affect system-level configs
