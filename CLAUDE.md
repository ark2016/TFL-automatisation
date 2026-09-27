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
  (Opus 5.5 for reasoning agents, Sonnet 5 for parsing/classification, Haiku 4.5 for JSON repair).
  Change them in all four projects together.
- Theory statements used by prompts: `docs/THEORY.md` — change lemma formulations there first, then in prompts.
- Shared LLM client: `agent_system/lib/llm_client.py` (`AnthropicClient`) — request kwargs (adaptive thinking +
  effort; `temperature` only for legacy models), typed API errors (`FatalAPIError`/`RetryableAPIError`) with
  backoff, the cross-pipeline concurrency semaphore (`TFL_MAX_CONCURRENCY`), and `UsageTracker`. `cfl_system` /
  `dcfl_system` / `ll_system`'s `LiveRunner` are thin wrappers around it — change request-building kwargs there,
  not in each `LiveRunner` separately. Structured outputs (`output_config.format`, per-agent JSON schema) are the
  primary response-parsing path; `agent_output_schema.py` in each project builds the schema, with automatic
  fallback to the legacy brace/fence extraction.
- Live test runs use Haiku only: `TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live`. Opus runs cost real money — only on request.
- `TFL_FORMALIZATION=1` (also `true`/`yes`/`on`) enables `agent_system`'s `formalizer` agent by default for every
  run, without passing `--formalize` each time; unset/other values keep it opt-in per `--formalize`.
- Eval set: `docs/EVAL_SET.md` (73 tasks, expected verdicts) is run by `tfl-eval` (`tfl_eval/`), which reports
  accuracy/calibration metrics — see `README.md` and `TODO.md` §7. Mock mode by default; `--live` only on request.
  Live results so far: `docs/EVAL_RESULTS.md` (24 trap tasks on Haiku, 2026-09-27) — read it before claiming a
  pipeline is or isn't calibrated; a full 73-task run and a post-C2 re-run are still open.
- Model prices live in each project's `config.py` alongside `EFFORT`/`MAX_TOKENS` (see "Models and LLM calls"
  above) — change the pricing table there, in all four projects together, not in docs.

## Commands
```bash
# Tests: the root conftest.py + pyproject.toml testpaths make a bare `pytest` safe now
# (it strips ANTHROPIC_API_KEY and stubs anthropic.Anthropic), but prefer explicit paths for speed:
.venv/Scripts/python -m pytest agent_system/tests cfl_system/tests dcfl_system/tests ll_system/tests ui_server/tests tests -q
# or simply:
.venv/Scripts/python -m pytest -q

# Any pipeline, shared CLI contract (writes <stem>_result.{json,md,html})
.venv/Scripts/python -m cfl_system.orchestrator <ir.json> --save DIR [--live] [--verbose]
.venv/Scripts/python -m agent_system <ir.json> --save DIR [--live]

# TFL Lab
.venv/Scripts/python -m ui_server.server --port 8765
```

## Allowed actions
- Create/edit/delete files in the four projects, `ui_server/`, root docs and CI when the task calls for it
- Run python, pytest, pip install in the project venv; docker for Lean 4 verification
- Do NOT modify `pumping_lemma/` or `reverse_morfism/`
- Do NOT run commands that affect system-level configs
