# Contributing

Thanks for considering a contribution. This project is primarily a personal research tool for a formal-languages course, but fixes, new test cases, and additional pipelines are welcome.

## Quick setup

```bash
git clone https://github.com/Ark2016/TFL-automatisation.git
cd TFL-automatisation
python -m venv .venv
.venv/Scripts/activate            # Windows; `source .venv/bin/activate` elsewhere
pip install -e .[dev]              # pytest, pytest-cov, ruff
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env   # only needed for --live runs
```

Run the full test suite before pushing:

```bash
python -m pytest agent_system/tests cfl_system/tests dcfl_system/tests ll_system/tests ui_server/tests -q
```

All suites must stay green (currently: **1383 passed, 3 skipped** — the skips need Docker with the Lean 4 image). A bare `pytest` from the repo root now collects the same suites (`[tool.pytest.ini_options] testpaths` in `pyproject.toml`) and the root `conftest.py` blocks any real Anthropic API call for the whole session as a safety net — but the legacy `pumping_lemma/tests`, which call the real API, are intentionally left out of `testpaths`, so run it against them explicitly (and expect a real bill) only if you mean to.

## Scope of a good PR

- **Bug fixes** — reproduce with a new test, then fix.
- **New test cases** — real problems from the ИУ-9 problem set, whether the pipeline solves them or not. Failing cases are valuable signal.
- **New specialist agent** — a new attack for an existing pipeline. Include a prompt file, a mock fixture under `examples/`, and at least one unit test.
- **New pipeline** — a fifth language class (e.g. LBA / context-sensitive). Follow the same layout as the four existing projects: `orchestrator.py` + `config.py` + `prompts/` + `lib/` + `examples/` + `tests/`.
- **Renderer improvements** — better HTML, more callout types, richer LaTeX support.
- **UI** — frontend tweaks in `ui_server/static/index.html` or backend routes in `ui_server/server.py`.

## What to avoid

- Don't add hard dependencies that aren't already in `pyproject.toml` unless necessary. Streamlit/React/etc. are out of scope — the UI is intentionally vanilla JS + stdlib Python.
- Don't commit live run artifacts. `examples/live_outputs/` and `examples/output/` are git-ignored; if you need to share a specific run, attach the JSON in the PR description instead.
- Don't bump LLM model IDs without updating every project together. All four pipelines use the same three-tier model stack — Opus 5.5 (reasoning), Sonnet 5.5 (parsing / classification), Haiku 4.5 (JSON repair) — pinned in each project's `config.py` together with the per-agent `EFFORT` levels. Don't reintroduce `temperature` for thinking models (400 on Opus 4.7+ / Sonnet 5 / 5.5).
- Don't include a real `ANTHROPIC_API_KEY` anywhere — `.env` is git-ignored, `.env.example` uses a placeholder.

## Code style

- Python: standard library formatting (`black`-compatible is fine but not required).
- Prompt files: concise Russian-language instructions, one `system` prompt per agent, matching the I/O contract documented at the top of each prompt.
- Comments: explain **why**, not **what**. The naming convention `_foo_bar_node` for LangGraph nodes and `foo_bar_agent` for prompt files should be kept consistent.

## Testing an LLM change

Work up from free to paid:

```bash
# 1. Offline: no runner, fallback reasoning — exercises orchestrator / oracles / renderer
python -m cfl_system.orchestrator cfl_system/examples/task11_ai_bj_between.json --verbose --save out/

# 2. Replay recorded agent outputs
python -m cfl_system.orchestrator cfl_system/examples/task_w1w2w1w3.json --mock cfl_system/examples/mock/ --save out/

# 3. Live but cheap: every agent on Haiku (a full CFL run is well under $1)
TFL_MODEL_OVERRIDE=claude-haiku-4-5 python -m cfl_system.orchestrator     cfl_system/examples/task11_ai_bj_between.json --live --verbose --save out/
```

Only run the production models (Opus 5.5 / Sonnet 5.5) when you need to judge answer quality. If a regression is isolable to a pure-function module (`lib/`), add a unit test and keep the LLM out of the critical path.

## Opening an issue

Use the bug report template at `.github/ISSUE_TEMPLATE/bug_report.md`. Attach the task IR JSON, the full stderr log, and the `_result.json` if one was produced. Live-run issues should say which models were used (default stack or `TFL_MODEL_OVERRIDE`) and the `effort` levels if you changed them.

## License

By contributing, you agree your changes are released under the [MIT license](LICENSE).
