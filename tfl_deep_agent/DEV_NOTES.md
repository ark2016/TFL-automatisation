# tfl_deep_agent — dev notes

Running notes per phase: environment decisions, deviations from `PLAN.md` / `docs/architecture.html`,
things later phases must know. Append under your own phase heading.

Python: .venv/Scripts/python

(Python 3.12.9, the repository's main `.venv`. No separate `.venv-deep` was needed; see P0.)

## How to run the suites

```bash
# everything (the four pipelines, ui_server, tfl_eval, tfl_deep_agent) -- pyproject testpaths
.venv/Scripts/python -m pytest -q
# only the deep agent (about 5 s, no Docker, no API)
.venv/Scripts/python -m pytest -q tfl_deep_agent/tests
# faster full run
.venv/Scripts/python -m pytest -q -n auto
```

Fresh environment: `pip install -e ".[deep,dev]"`. Without the `[deep]` extra, the tfl_deep_agent tests that need
langchain-anthropic / deepagents skip themselves (`pytest.importorskip`), so CI's `pip install -e .[dev]` stays green.
CI (`.github/workflows/tests.yml`) does not install `[deep]` yet. Adding it there is a later decision (P8), outside
the files P0 may touch.

## P0 — skeleton and dependencies (2026-11-07)

### Dependencies

- `pip freeze` before any change: `docs/pip_freeze_before.txt`.
- `pip install --dry-run "deepagents==0.7.34" langchain-anthropic langgraph-checkpoint-sqlite pypdf` wanted these
  upgrades: langgraph 1.0.7 → 1.2.20, langchain-core 1.6.9 → 1.7.6, langchain-anthropic 1.7.5 → 1.7.10,
  langgraph-checkpoint 4.0.0 → 4.3.1, langgraph-prebuilt 1.0.7 → 1.1.1, langgraph-sdk 0.3.3 → 0.4.14,
  langsmith 0.6.6 → 0.14.17, langchain-protocol 0.0.19 → 0.0.21, certifi. New: deepagents 0.7.34, langchain 1.4.9,
  langgraph-checkpoint-sqlite 3.1.3, aiosqlite, sqlite-vec, langchain-google-genai + google-genai + google-auth
  (a hard dependency of deepagents 0.7.34), wcmatch, bracex, filetype, websockets, pyasn1(-modules).
  **anthropic (1.13.0), httpx (0.28.1) and httpx2 (2.13.1) were not touched.** pypdf 6.19.0 was already installed.
- Installed into `.venv`. Full suite with the upgrades, `docker` hidden from PATH, so the Docker tests skip the way
  they do on a machine without Docker. The original P0 claim of "3662 passed, 0 failed" was **wrong**: the
  same runs had 1 failure (`test_api_key_is_stripped`, see "P0 fix" below). After the P0 fix:
  **3684 passed, 161 skipped, 3 xfailed, 0 failed** (`-n auto`, 83 s; the 3rd xfail is the budget pin below), and
  a serial `agent_system/tests tfl_deep_agent/tests` run is green as well. With Docker visible, the only failures are Lean-in-Docker checks hit by the
  daemon fault described below. The same class of failures happened before the upgrade.
- Packaging was checked with `pip wheel --no-deps --no-build-isolation` into a scratch directory. The wheel has
  `tfl-deep = tfl_deep_agent.cli:main`, `Provides-Extra: deep` with the five requirements, `prompts/*.md`,
  `settings.example.json` and the sandbox files, and no `tests/`. The empty `skills/*/.gitkeep` placeholders are
  dotfiles that setuptools globs skip, which is harmless. The real `SKILL.md` files (P2) will be packaged.
- The `.venv` has a stale editable install of the project that points at an old path
  (`C:\programming\TFL\TFL-automatisation`), so no `tfl-*` console scripts exist in `.venv/Scripts`. Everything
  works through `python -m ...` from the repo root. P0 did not re-register it. To fix it, run
  `pip install -e ".[deep,dev]"` once.
- Decision: **keep the upgrades in `.venv`; no `.venv-deep`.** `.venv-deep/` is in `.gitignore` anyway in case a
  later upgrade forces the split.

### Docker (state on 2026-11-07)

- Docker Desktop is running: server 27.5.1.
- Image `tfl-lean4:latest` exists (id `sha256:f828d0048005…`, created 2026-09-28, about 16 GB unpacked).
- The daemon is flaky right now: about every second `docker run` fails at once with
  `OCI runtime create failed: runc create failed: unable to start container process: waiting for init preliminary
  setup: read init-p: connection reset by peer`. `type_check.check_lean` reports that as status `invalid` with the
  daemon message as its only error, so Lean tests in the pipelines fail at random (43 failed before the upgrade
  and 35 after it, under `-n auto`; a different subset every run, and each one passes when re-run alone). This is an
  environment problem, not a code problem. It happened with the old package versions too. Restarting Docker Desktop
  (or WSL) would probably fix it. P0 did not restart it, because the user may be using it. P5/P6 Docker tests
  should treat a daemon-level `docker run` failure as "unavailable" and skip, not fail.

### `models.make_chat_model` — choice: plain `ChatAnthropic`, shape taken from `AnthropicClient`

- langchain-anthropic 1.7.10 can express every field we send, so no `BaseChatModel` wrapper was needed:
  - `thinking={"type": "adaptive"}` is passed explicitly. Without it, langchain-anthropic would add its own default
    `{"type": "adaptive", "display": "summarized"}` whenever an effort is set.
  - `effort=` → `output_config.effort`.
  - Refusal fallback (Opus 5.x): `model_kwargs={"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
    "extra_body": {"fallbacks": "default"}}`. These go straight into `messages.create(**payload)`.
  - Legacy models (Haiku 4.5): `temperature` travels in `extra_body`, exactly as `AnthropicClient` does it (the 1.x
    SDK signatures no longer take `temperature`). `ChatAnthropic.temperature` stays `None`, so it is never sent at
    the top level.
  - `max_tokens` is clamped with `llm_client.clamp_max_tokens`.
  - `streaming=True`, like `AnthropicClient.call`, which always streams. With a 64000 ceiling, the SDK refuses
    non-streaming calls.
- The kwargs are not re-implemented. `models.request_kwargs` calls `AnthropicClient.build_request_kwargs` and maps the
  result onto `ChatAnthropic` parameters, so a change to the pipelines' request shape reaches the agent automatically.
  Any new field in `build_request_kwargs` is forwarded through `model_kwargs`, not dropped.
- PDF / image content blocks: langchain's standard blocks
  (`{"type": "file", "base64", "mime_type": "application/pdf"}`, `{"type": "image", "base64", "mime_type"}`) become
  Anthropic `document` / `image` blocks with a `base64` source. Native Anthropic blocks pass through unchanged. Both
  forms are pinned for PNG / JPEG / WebP and PDF.
- Tests (`tests/test_models_request_shape.py`) send real SDK requests into an `httpx2.MockTransport`, because the
  anthropic 1.x SDK uses **httpx2**, not httpx. A plain `httpx.MockTransport` is rejected with `TypeError`. The base
  URL is `http://anthropic.mock.invalid` (env `ANTHROPIC_API_URL`, read by `ChatAnthropic` when it is built), and the
  key is the fake `"test"`.

### Network guard — the root conftest did NOT cover langchain-anthropic by itself

- `ChatAnthropic._client` / `_async_client` call `anthropic.Client(...)` / `anthropic.AsyncClient(...)`. These are
  separate module attributes from the `anthropic.Anthropic` / `AsyncAnthropic` that the root `conftest.py` stubs.
  With only the root stub in place, a `ChatAnthropic` built under pytest would get the real client.
- Fix (inside the package, since the root conftest is outside P0's file scope):
  `tfl_deep_agent/tests/conftest.py` has a session autouse fixture that depends on the root
  `_block_live_anthropic_calls` and points `anthropic.Client` / `AsyncClient` at the same stub. It honours
  `TFL_ALLOW_LIVE=1` like the root fixture.
- `tests/test_network_guard.py` proves it. All four SDK names are the stub, and the API key is absent. `invoke` and
  `ainvoke` raise "Real Anthropic API calls are disabled under pytest", with and without an explicit key. A
  `deepagents.create_deep_agent(model=make_chat_model(...))` graph also hits the stub on its first call.
- **Superseded by "P0 fix" below** (shared `tests/guard.py` + tripwire test). Original note: the guard covers tests under `tfl_deep_agent/tests` only. `ui_server` (P7) and
  `tfl_eval` (P8) tests that build a real `ChatAnthropic` would not be covered. The robust fix is two lines in the
  root `conftest.py` (also stub `anthropic.Client` / `anthropic.AsyncClient`). That file is outside the PLAN's
  allowed list, so it needs the user's approval. Until then, P7/P8 tests must use fake chat models, or copy this
  fixture.

### Notes for later phases

- **P2, budget:** `usage_metadata["input_tokens"]` from langchain-anthropic is the *total prompt*, that is
  input + cache read + cache write (pinned in `test_streamed_reply_parsing_thinking_and_usage`). Price it as
  `input_tokens - cache_read - cache_creation` at the input rate, plus `input_token_details.cache_read` /
  `cache_creation` at their own rates. Otherwise cached tokens get billed at the full input price.
- **P2, worst-case check:** `MAX_TOKENS = 64000` (the pipelines' convention) means one Opus call reserves
  64000 × $20/MTok = $1.28 before it is made. With `per_task_usd` 5.00, only about 3 worst-case Opus calls fit.
  P2 may want a lower per-role ceiling for `main`, so it does not refuse calls too early. That is a behaviour
  decision for the user.
- **P2, betas:** the refusal-fallback beta goes in `extra_headers["anthropic-beta"]`. If anything later sets
  `ChatAnthropic(betas=[...])` (which routes to `client.beta.messages`), the two `anthropic-beta` values must be
  merged into one comma-separated header. Otherwise one of them silently overrides the other.
- **P2, retries / concurrency:** `ChatAnthropic` retries through the SDK (`max_retries=2`). It does not use
  `AnthropicClient`'s classified backoff or the `TFL_MAX_CONCURRENCY` semaphore. `middleware/concurrency.py` should
  wrap model calls in `agent_system.lib.llm_client.get_concurrency_semaphore()`.
- **P2, `config.py`:** prices are re-exported from `agent_system/lib/llm_client.py` (`MODEL_PRICING`, the long-prompt
  Haiku 5.5 card, `estimate_cost_usd`). They are not copied, because the four pipeline `config.py` files hold no
  prices. `test_config.py` pins that they are the same objects. Role keys match `settings.example.json`
  `models.roles`. `"lean"` is the lean-formalizer role.
- Importing `agent_system.lib.llm_client` does not load `.env`. Only `LLMRunner.__init__` calls `_load_env()`, and
  `models.py` never builds an `LLMRunner`.
- `cli.py` is a stub with the final argument contract. It exits with code 2 and prints "not implemented yet".

## P0 fix — review findings (2026-11-07)

- **Key leak in a full run (fixed).** The root conftest strips `ANTHROPIC_API_KEY` once per session.
  `LLMRunner.__init__` → `llm_client._load_env()` later copies `agent_system/.env` back into `os.environ`, so every
  serial full run failed `test_api_key_is_stripped`, and later `make_chat_model()` calls without `api_key=` were
  built with the real key. Nothing was sent, because the `anthropic.Client` stub still blocked the call. Fix:
  `tests/guard.py` has a function-scoped autouse fixture `_no_live_anthropic_key`. It removes the key and makes
  `llm_client._load_env` and `dotenv.load_dotenv` no-ops for every test (skipped under `TFL_ALLOW_LIVE=1`). The
  reviewer's repro (`agent_system/tests/test_llm_request_params.py` + `test_network_guard.py`) is green now.
- **Guard for other test dirs (closed without touching the root conftest).** The session fixture that stubs
  `anthropic.Client` / `AsyncClient` moved from `tests/conftest.py` to `tests/guard.py`, and `tests/conftest.py`
  imports both fixtures from there. In P7 / P8, `ui_server/tests/conftest.py` and `tfl_eval/tests/conftest.py`
  must import them (the snippet is in the `guard.py` docstring).
  `test_network_guard.py::test_other_test_dirs_that_use_the_agent_import_the_guard` scans every pyproject testpath
  and fails as soon as a test file there mentions `tfl_deep_agent` / `langchain_anthropic` / `ChatAnthropic` while
  the dir's conftest does not import the guard. The one-place alternative still needs the user's approval: two
  lines in the root `conftest.py`. I did not use a `-p` plugin in pyproject `addopts`. pytest imports it before the
  rootdir is on `sys.path`, so `python -m pytest` started from a subdirectory would crash with the stale editable
  install.
- **`make_chat_model(**chat_kwargs)` could replace resolved fields (fixed).** A caller kwarg for model / model_name,
  max_tokens_to_sample, reasoning_effort / effort, thinking, output_config, model_kwargs, betas, streaming,
  disable_streaming, temperature, top_k or top_p raises `TypeError`. Aliases are read from
  `ChatAnthropic.model_fields`. `max_tokens`, `effort` and `temperature` are `make_chat_model`'s own parameters,
  so they still go through `config.resolve_*` and the clamp. Pinned under `TFL_MODEL_OVERRIDE`.
- **Unpriced model ids fail closed (fixed).** `config.resolve_model` raises `config.UnknownModelError` (a
  `ValueError`) when the id has no `MODEL_PRICING` card. The id is first normalized with llm_client's dated-suffix
  rule, and the check covers both the override and `requested`. A dated id keeps its spelling on the wire. New
  `config.pricing_key(model)` and `config.worst_case_usd(model, input_tokens, max_tokens)` also raise on an unknown
  price and never return `None`. The worst case prices prompt tokens at max(input, cache_write), because deepagents
  marks the prompt with `cache_control`, and uses Haiku 5.5's long-prompt card above the threshold.
  P2's BudgetMiddleware should use `worst_case_usd`.
- **Agent-path request shape (pinned).** `test_deep_agent_graph_keeps_the_request_shape` builds
  `create_deep_agent(model=make_chat_model(role="main"), system_prompt="S")` on the wire mock. It asserts that
  thinking, effort, fallbacks and the beta header survive, that deepagents' tools are bound, and that prompt caching
  has the form deepagents gives it: `system` as text blocks with `cache_control` on the last block, plus a top-level
  `cache_control: {type: ephemeral, ttl: 5m}`.
- **OPEN, needs the user's decision before P2: max_tokens ceilings vs budgets (NOT changed).** With the current
  defaults, the worst case of one call is larger than the matching budget:
  `main`/report 64000 × $20/MTok = $1.28 > `reserve_for_report_usd` $0.30;
  `lean` 128000 × $20 = $2.56 > `per_subagent_max_usd` $2.00;
  `spawn_subagent(model="sonnet", budget_usd=0.40)` needs 64000 × $10 = $0.64.
  The architecture's `run_workflow` example (2 Sonnet + 1 Haiku + an Opus judge, $2.00) needs about $2.59.
  Options for the user:
  (a) per-role or per-call ceilings for budgeted calls (for example main/report ≤ 15000 on Opus = $0.30);
  (b) derive `max_tokens` per call from the remaining budget: `floor((remaining − input worst case) / output rate)`,
  refusing the call below a floor;
  (c) raise the reserve and `per_subagent_max_usd` in settings.
  This changes agent behaviour, so I left it to the user.
  `test_config.py::test_default_ceilings_fit_the_example_budgets` is a strict xfail that pins the conflict. Remove
  the xfail once a choice is implemented.
