# TFL-automatisation

Multi-agent LangGraph pipelines for Theory of Formal Languages problems (ИУ-9).
Overview and architecture: `README.md`. Open work: `TODO.md` (audit backlog, prioritised).

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
- Request building: `_build_request_kwargs` in each `LiveRunner` / `agent_system/lib/llm_client.py`
  (adaptive thinking + effort; `temperature` only for legacy models).
- Live test runs use Haiku only: `TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live`. Opus runs cost real money — only on request.

## Commands
```bash
# Tests (explicit paths — a bare `pytest` also collects pumping_lemma/tests and hits the API)
.venv/Scripts/python -m pytest agent_system/tests cfl_system/tests dcfl_system/tests ll_system/tests ui_server/tests -q

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
