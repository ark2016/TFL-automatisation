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

## P1 tools — tool base + inventory (2026-11-07)

Files: `tools/base.py`, `docs/TOOL_INVENTORY.md`, `tests/test_tools_base.py`, `tests/_tool_helpers.py`.
The group modules (`tools/ir.py`, `words.py`, `reg.py`, `cfl.py`, `dcfl.py`, `ll.py`, `theory.py`) are NOT written
here. They build on `base.py` and on the inventory.

### `tools/base.py` contract (what the group modules rely on)

- `ToolResult` (pydantic, frozen, `extra="forbid"`): `tool, ok, data, trust, trust_reason, bounds, error, error_kind,
  elapsed_s`. Build it with `ToolResult.success(data, trust=..., reason=..., bounds=...)` or
  `ToolResult.failure(msg, kind=...)`. Invariants are enforced by validation:
  - a failure is always `not_verified`;
  - `bounded_pass` requires `bounds`;
  - `trust_reason` is never empty;
  - `data` is converted to plain JSON (sets become sorted lists, tuples become lists, tuple keys become `"a,b"`).
- Trust helpers: `TRUST_LEVELS` (policy order), `trust_rank`, `trust_at_least`, `clamp_trust`, `strongest_trust`,
  `confidence_cap`. `CONFIDENCE_CAPS` **is** `agent_system.lib.claim_verifier.CONFIDENCE_CAPS` (reused, not copied;
  pinned by a test). P3's `evidence/policy.py` can import these instead of defining its own.
- Registration:
  `@tfl_tool(group=..., max_trust=..., timeout_s=30, timeout_mode="thread"|"process"|"none", allow_refuted=True)`
  on `func(args: <ToolArgs subclass>) -> ToolResult`. The name defaults to the function name, the description to
  its docstring (which is required), and the schema to the parameter annotation. A class defined inside a function
  cannot be resolved under `from __future__ import annotations`, so in that case pass `args_schema=`. The decorator
  returns the function unchanged.
- Registration is refused when:
  - the schema does not subclass `ToolArgs` (`extra="forbid"`);
  - a field anywhere in the schema (nested models included) is named `trust`, `trust_reason`, `confidence`,
    `verified`, ... (`RESERVED_ARG_NAMES`);
  - `max_trust` is above the group ceiling `GROUP_MAX_TRUST`. The ceilings are: `ir` well_formed; `words`
    bounded_pass; reg / cfl / dcfl / ll / lean verified; theory / orchestration / report not_verified.
- Execution: `REGISTRY.invoke(name, args)` is the only path. In order, it:
  1. checks the JSON byte limit;
  2. validates against the schema;
  3. runs the function under its timeout;
  4. turns every exception into a failure (`ToolError` subclasses carry `kind`; any other exception becomes
     `lib_error`; a non-`ToolResult` return or an invalid result becomes `internal`);
  5. clamps the trust to the ceiling. A result lowered to `bounded_pass` without bounds goes down to
     `well_formed`. `refuted` passes through unless the tool has `allow_refuted=False`.
- LangChain: `REGISTRY.langchain_tools(groups=..., names=...)` returns `StructuredTool`s with
  `response_format="content_and_artifact"`.
  - The model reads `content`: the ToolResult JSON, with `data` replaced by a preview above 20000 chars.
  - `ToolMessage.artifact` is the full `ToolResult.model_dump(mode="json")`. **P3 EvidenceMiddleware should read
    the artifact.**
  - When LangChain's own validation rejects a call (for example an extra `trust` key), the tool never runs. The
    message then has `status="error"` and no artifact, and its content is a failure ToolResult JSON with
    `error_kind="invalid_input"`.
  - A `ToolNode` inside a `StateGraph` is covered by a test. A bare `ToolNode.invoke` outside a graph fails under
    langgraph 1.2 (`Missing required config key 'N/A'`). That is langgraph behaviour, not ours.
- `load_groups(groups=None)` imports `GROUP_MODULES` (the tools/ modules only; orchestration tools live in
  `orchestration/` and register themselves, if they register at all).

### Limits and timeouts (decisions)

- `DEFAULT_LIMITS`:
  - 64 KiB of JSON per call;
  - word ≤ 100 (matches cyk's own > 100 guard), ≤ 500 words, alphabet ≤ 16;
  - grammar ≤ 300 rules, RHS ≤ 24, ≤ 120 nonterminals;
  - exhaustive enumeration ≤ 20000 words;
  - LL k ≤ 6;
  - default timeout 30 s, max 600 s;
  - model content ≤ 20000 chars.

  Group code can pass tighter values. `max_len_within(|Σ|, 3000, 10)` reproduces dcfl's
  `_full_enumeration_max_len` (pinned against it for |Σ| = 1..5).
- Timeout modes. Python cannot kill a thread.
  - `thread` (default): a timed-out call keeps running in the background. Such calls are counted. Once
    `MAX_ABANDONED_THREADS = 8` are still running, new thread-mode calls get `error_kind="busy"`, so runaway CPU
    work does not pile up.
  - `process`: a `spawn` child that is killed on timeout. The function and its args must be picklable, and
    start-up costs about 1 s on Windows. It works under xdist (`-n 4` run).
  - The inventory says which lib calls need `process`: searches without their own budget.
  - `Deadline` is for cooperative loops.

### Deviations / findings from the inventory

- **CFL pumping trust.** The architecture table gives CFL a max of "bounded_pass". VERDICT_POLICY §4
  (cfl/pumping, ogden) caps finite pumping checks at **well_formed** (+ `refuted`). The policy wins (PLAN
  non-negotiable 6). The cfl group ceiling is `verified` because `cyk` on a given grammar and `input_grammar_cfg`
  (R2-CFG) are complete checks.
- **Tools added to the table:**
  - `grammar_vs_ir` (`oracle_test_grammar` / `oracle_test_pda`, R2 bounded_pass / refuted);
  - `input_grammar_cfg` (R2-CFG verified);
  - `dpda_vs_ir` (the full R2' procedure);
  - `pump_check_reg`.

  Optional: `stratification`, `dcfl_claim_check`, `ll_claim_check`.
- **The R2' DPDA procedure is private.** It lives only in `dcfl_system.lib.oracle_verifier._verify_stack_strategy_dpda`.
  The public route is `verify_agent_results({"stack_strategy": ...}, ir)`.
- **No lib backing.** `describe_ir_schema` has no schema constants for CFL / DCFL / LL (only REG has `IR_SCHEMA`),
  and the kind sets are private. `ir_to_text`, `list_theorems` and `get_theorem` have no lib function at all.
  They are written in the package.
- **Oracle signatures differ** (`bool` vs `bool | None`; raise vs return `None` when there is no oracle). The
  inventory suggests one shared dispatch helper in `tools/words.py`.

### Tests

`tests/test_tools_base.py`: 44 tests, about 7 s serial, including 2 process-mode tests. No LLM, no network, no
Docker. `pytest -q -n 4 tfl_deep_agent/tests`: 153 passed, 1 xfailed.

## P1 tools — theory group (2026-11-07)

Files: `tools/theory.py`, `tests/test_tools_theory.py` (101 tests, about 4 s, no LLM / network / Docker).

- `list_theorems` / `get_theorem` parse `docs/THEORY.md` in the package (no lib backing, inventory §7). The parser
  only splits at Markdown headings of levels 1–3 outside code fences and copies lines; it never rewrites text.
  `get_theorem` returns the section **verbatim** (heading line included, trailing blank lines and `---` rules
  dropped), pinned byte-for-byte against the file for every marked section.
- **Ids.** The file's own numbers (`1.1`, `2.0`, `1.10`, `0`). Part I and Part II share one numbering; a number that
  occurs in both parts (today only `4`, the two checklists) gets part-qualified ids `I.4` / `II.4`, and a bare `4`
  is refused as ambiguous. Every numbered section also answers to `<part>.<number>` (`II.1.10`); `§` and a
  trailing dot are accepted. Level-1 part heads are `I` (title + sources legend) and `II`. An unnumbered heading
  would get `<part>.h<line>` (none exist today).
- **Status** = the last `**[...]**` marker in the heading, verbatim with brackets (`[проверено]`,
  `[проверено перебором]`, `[доказано ...]`, `[источники и границы указаны отдельно]`), `null` without one. No
  normalised "status kind": the tool descriptions explain the markers by quoting the file's preamble rule
  ("проверено перебором" = only the stated finite range). `error_notes` counts `**[ошибка]**` paragraphs (they quote
  a wrong formulation in order to correct it; the description tells the model never to use them).
- **Classes** come from the numbering convention stated in the Part II preamble: top-level 0 → reg, 1 → dcfl,
  2 → cfl, 3 → ll; part heads, §4 checklists and §5 live-run log → `meta` (hidden unless `include_meta`). LL proofs
  also use the DCFL sections (§3.3 (A)); the agent has to ask for `dcfl` too, the tool does not guess cross-links.
- `list_theorems(task_type=...)` maps an IR task type to its class through the validators' own sets
  (`agent_system.lib.ir_schema._VALID_TASK_TYPES`, `cfl_ir_schema._CFL_TASK_TYPES`, `dcfl_ir_schema._DCFL_TASK_TYPES`,
  `ll_ir_schema._LL_TASK_TYPES` — private names, imported read-only; the four sets are disjoint). A `cls` that
  contradicts `task_type` is `invalid_input`. `query` is a case-insensitive substring over heading + own text and
  returns up to 3 matching lines verbatim (cut at 240 chars with " …").
- Paging: `max_chars` 500..15000 (default 8000) and `offset`/`next_offset`, cut on a line end when it falls in the
  second half of the page. 15000 chars keep the model content under `max_content_chars` (checked on the largest
  case, `get_theorem("1", include_subsections=True)`, 12.2k chars).
- Trust: always `not_verified` (`allow_refuted=False`); the reason says a theorem text is reference material and
  its marker does not raise trust. Missing / non-UTF-8 / heading-less file → failure `unsupported`. The file is
  cached by (path, mtime, size). `THEORY_PATH` is module-level so tests can point it at a fixture; in an installed
  wheel without `docs/` the tools fail as `unsupported` rather than guess.
- Public helpers for P2 (skills / prompts that quote theory): `load_theory()`, `parse_theory(text)`,
  `TheoryDoc.resolve(ref)`, `own_text` / `full_text`, `theory_class_for_task_type`.

## P1 tools — dcfl group (2026-11-07)

Files: `tools/dcfl.py`, `tests/test_tools_dcfl.py` (84 tests, about 4 s, no LLM / network / Docker).
Tools: `dpda_run`, `closure_lookup`, `pattern_match`. `dpda_vs_ir` and `dcfl_claim_check` (inventory §5) are NOT
written; `dpda_vs_ir` needs the oracle dispatch helper that `tools/words.py` should own (inventory §10).

- **`dpda_run`** (ceiling `verified`). Runs, in the pipeline's R2' order: `normalize_epsilon_accept_sinks` →
  `check_determinism` → `to_cfl_pda` (structure, compatibility only) → `dpda.dpda_run` per word on the
  *normalized* automaton. Trust: a determinism conflict or a structural error → `refuted`, and the words are not run
  (listed in `not_run`), the same verdict `_verify_stack_strategy_dpda` gives; otherwise `verified` for
  "deterministic" and for each accept/reject answer on this automaton. The reason text says explicitly that it is not
  a check against the task language (the live dcfl-04 automaton is `verified` here and still accepts "b"; that is
  pinned). A `None` run (10000-step budget) is listed in `unknown`, never as a reject.
  - Schema: typed `DPDASpec` / `DPDATransition` with the contract's JSON key `from` (pydantic alias plus
    `serialize_by_alias=True`, so LangChain's nested-model hand-off keeps the key; a LangChain-path test pins it).
    `read` must be one character or an epsilon spelling (`null`, `""`, `ε`, `eps`, `epsilon`), because words are read
    character by character and a multi-character `read` would silently never fire.
  - Decision: undeclared states / stack symbols are **not** rejected by the schema. They go to the lib's structural
    check and come back as `refuted`, as in R2'. Schema-level rejection (`invalid_input`) is only for shape problems
    (missing fields, unknown keys, bad `accept_mode`, empty `initial_stack`, multi-character `read`).
  - A DPDA with `accept_configs` but no `accept_states` is refuted by `to_cfl_pda`, exactly as in the pipeline.
  - Limits: 300 states / stack symbols, 2000 transitions, push ≤ 16, initial stack ≤ 8, 200 words. The 64 KiB call
    limit applies; the exam_04 certificate (5835 transitions, ~785 KB) is `input_too_large` (pinned).
  - `data.dpda_id`: first 16 hex chars of SHA-256 over the sorted-key JSON of the submitted automaton.
- **`closure_lookup`** (ceiling `not_verified`, `allow_refuted=False`). The operation is an enum of 19 names: the 8
  `CLOSURE_TABLE` operations plus 11 that only `docs/THEORY.md` §1.4 lists (`intersection` = DCFL ∩ DCFL,
  `difference`, `right_quotient_regular`, `min`, `max`, `inverse_deterministic_gsm`,
  `inverse_nondeterministic_transducer`, `haspref`, `marked_continuation` = L_$, `left_quotient_word`,
  `left_quotient_regular`). Every entry carries a **verbatim quote** from §1.4. A test checks each quote against the
  file (ignoring whitespace and `**`) and checks that `closed` agrees with `closure_table.is_closed`; the tool refuses
  to answer if the two sources ever disagree. No implication text was written for the THEORY-only operations.
  `include_theory_text` returns §1.4 verbatim with its line range. `ir` runs `closure_scan`, and its `confidence`
  field is renamed `heuristic_weight`, so the model never sees a confidence-looking number from a heuristic.
- **`pattern_match`** (ceiling `not_verified`, `allow_refuted=False`). `verdict` / `method` are renamed
  `suggested_verdict` / `suggested_method`, and every result carries an `advisory` note (dcfl_system/CLAUDE.md:
  the classifier is ADVISORY ONLY). Pinned trap: on eval dcfl-04 ({aⁿbⁿcᵐ}, expected `dcfl`) the top hint is
  `triple_count` → `non_dcfl`, while a correct deterministic automaton exists (tested against the task oracle).
- **Lib finding (not fixed, the lib is read-only):** `pattern_db.match_patterns` raises `TypeError` on an IR whose
  variable `domain` is an object (the ll Format 1 shape, `ll_system/examples/format1_anbn_union_ancn.json`).
  `pattern_match` and `closure_lookup` turn `TypeError` / `AttributeError` / `ValueError` from the lib into
  `invalid_input` with a hint about the DCFL IR format. IRs of other classes that do not crash are accepted, and
  their `validate_dcfl_ir` errors are returned in `ir_errors`.
- Both enums (`PatternCode`, `ClosureOperation`) are checked against the lib tables at import time, so a lib change
  fails loudly instead of leaving a stale schema.

## P1 tools — cfl group (2026-11-07)

Files: `tools/cfl.py`, `tests/test_tools_cfl.py` (80 tests, about 8 s serial; 2 of them spawn a process).
Tools: `to_cnf`, `cyk`, `pda_run`, `parikh`, `pump_check_cfl`. `grammar_vs_ir`, `input_grammar_cfg` and the
optional `stratification` from the inventory's §4 are **not** in this scope and were not written.

### Trust decisions

- **`to_cnf`**: ceiling `well_formed` (a construction, not evidence about the task language). Tests compare the CNF
  language with the input's up to length 7 by an independent fixed-point enumerator, on 6 example grammars from
  agent / cfl / dcfl / ll.
- **`cyk`**: ceiling `verified`, for "w ∈ L(G)" / "w ∉ L(G)" about the given grammar only. Optional `expected
  {word: bool}`: a contradicted claim → `refuted`. An input already in CNF is used as is, otherwise it goes through
  `to_cnf` first. Terminals must be single characters (`unsupported` otherwise), because the lib reads words
  character by character. A word with symbols outside the terminals gets `False` (exact) and a note. There is a 25 s
  cooperative deadline: on expiry the decided words stay `verified`, the rest go to `unchecked`, and bounds have
  `truncated`. `table_word` (≤ 12 chars) returns the non-empty CYK cells over the CNF grammar.
- **`pda_run` (deviation from the inventory, sound):** the inventory says a rejection is `bounded_pass`. The BFS in
  `pda_simulator` explores every reachable configuration with a visited set. It drops a configuration only when its
  stack would exceed `MAX_STACK_DEPTH` (1000), and it raises `TimeoutError` past `MAX_STEPS`. A move builds a stack of
  at most (deepest explored stack − 1) + (longest push). So when `max_stack_depth − 1 + max_push ≤ 1000`, nothing
  was dropped and the rejection is exact → `verified`. Otherwise the result is `bounded_pass` with `max_steps` /
  `max_stack_depth` / `truncated` (pinned by an ε-push loop test). A step-budget overrun is **unknown** (`None`),
  never "rejected" (pinned by a branching ε-loop test). An inexact rejection cannot refute a claimed acceptance.
  - `push_order` defaults to `top_first` (textbook and the cfl pda_builder contract), which goes through
    `cfl_oracle_test.normalize_agent_pda`. `top_last` is the simulator's native order. A test checks that the two
    agree and that misreading the order changes the answers.
  - The live pda_builder PDA for task11 ({aⁱbʲ | j < i < 2j}, `cfl_system/examples/live_outputs`) matches the
    definition on all aⁱbʲ with i, j < 8 (pinned).
- **`parikh`**: ceiling `well_formed`, always (§4 cfl/parikh). It has three modes, and exactly one may be given:
  - words + an ordered alphabet → exact vectors;
  - grammar + `max_length ≤ 15` → an image sample;
  - ir → `analyze_parikh`.

  `conclusion` is always `None`. The grammar and IR modes have no budget of their own, so they run in a killable
  `spawn` process (`run_with_timeout(mode="process")`, 35 s) inside the tool's 45 s thread timeout. That costs about
  1 s per call on Windows. The words mode stays in-process. An IR whose `analyze_parikh` reports
  `evidence_scope: not_verified` (an unsupported kind or an invalid CFG) → `unsupported`.
- **`pump_check_cfl`**: ceiling `well_formed` (+ `refuted`), as the inventory and §4 cfl/pumping require.
  - Mode `claim`: the prover's evidence JSON (or a whole agent output with `evidence`) goes to
    `claim_verifier.verify_pumping_claim`, and the lib's `trust` is mapped
    (refuted / well_formed / else not_verified). `bounded_check` becomes bounds (`p_values`, `i_values`, `n_splits`,
    `splits_closed`, `unresolved_splits`, `n_unknown`). `method: auto` picks Ogden from `agent == "ogden"` or a
    `marked_positions` object.
  - Mode `split`: one u·v·w·x·y. Admissibility is enforced as `invalid_input`: |vx| ≥ 1, plus with `p`: |z| ≥ p and
    |vwx| ≤ p; for Ogden: ≥ p marks, a mark in vx, ≤ p marks in vwx. `refuted` when z ∉ L, or when a
    `claimed_exit_i` word is in L. A split that survives every tried i is `well_formed` with a "not a refutation"
    reason. Unknown oracle answers → `not_verified`.
  - Regression from PLAN: the ticket-50 golden run's pumping evidence (wrong p = 4 instance a⁸b(ab)⁵a⁸) → `refuted`.
    With the corrected instance a⁸b(ab)⁴a⁸ the result stays `well_formed` (never higher), even though every split
    closes.

### Membership oracle for pump_check_cfl (`cfl.language_oracle`)

- Order: `cfl_oracle_from_ir` → dcfl `build_membership_oracle_from_ir` → ll `oracle_from_ll_ir`. Every answer that is
  not a bool, and every exception, is `None` (unknown). `data.oracle.source` names the builder that was used.
- **Decision on approximate oracles.** The exponent-notation oracle (`ExponentPattern`, flagged `is_approximate` by
  the lib) **is used**. It is exact for the parsed pattern and answers `None` outside its bounds, so VERDICT_POLICY §4
  lets semantic checks use it and skip the `None` answers. The lib's own `claim_verifier._get_oracle` skips it for the
  word_instances step, but it does use it for the `word_chosen ∈ L` check. The deep agent passes one oracle
  explicitly for both checks, so the two are consistent. The grammar-filter oracle for a **natural-language filter**
  accepts a superset of L. It is refused (not the IR's language), and the dispatch falls through to dcfl / ll.
- If no oracle exists, claim mode still runs the structural checks with an always-`None` oracle, so nothing semantic
  is decided. Split mode → `unsupported`.
- This is a local dispatch. TOOL_INVENTORY §10 suggests one shared `words.oracle_for(cls, ir)`. `tools/words.py` was
  still empty when this group was written. Once it exists, `language_oracle` can delegate to it, but keep the
  natural-language-filter refusal and the `None` mapping.
- `ir_sha256` / `grammar_sha256` / `pda_sha256`: first 16 hex chars of SHA-256 over the sorted-key JSON
  (`cfl.ir_digest`). The dcfl group's `dpda_id` uses the same rule. If P3's ledger settles on one IR id, switch to it.

### Grammar argument

- `Grammar` is a typed nested model with `terminals`, `nonterminals`, `start` and `rules [{lhs, rhs}]`. Unknown keys
  such as `kind` are ignored, so an IR's `language_spec` can be passed as is. The schema caps: ≤ 300 rules,
  rhs ≤ 24, ≤ 120 nonterminals, ≤ 16 terminals.
- A rhs token `ε` / `eps` / `epsilon` / `λ` is read as ε unless it is a declared symbol (noted in `data.notes`). The
  cfl lib itself would reject it as an undeclared symbol.

## P1 tools — ir + words

Files: `tools/ir.py` (group `ir`: `describe_ir_schema`, `validate_ir`, `ir_to_text`), `tools/words.py` (group
`words`: `sample_words`, `check_membership`, plus the shared `oracle_for`), `tests/test_tools_ir_words.py`.

- **Trust.** `describe_ir_schema` is `not_verified` (reference only). `validate_ir` and `ir_to_text` are `well_formed`
  for a valid IR (structure only), and an invalid IR is `ok=True, not_verified` with the validator errors.
  `sample_words` and `check_membership` are `bounded_pass` only when an **exact** oracle decided the words, and
  the bounds are the longest decided word, the number of decided and unknown words, and `truncated`. An expected
  label contradicted by an exact oracle is `refuted`. Approximate oracles and generator-only labels stay at
  `well_formed` and never refute.
- **IR fingerprint.** `ir-` + the first 12 hex chars of SHA-256 over the sorted-key JSON. It is in `data` and in every
  `trust_reason`, because the results are relative to that IR. The cfl group's `ir_sha256` uses 16 hex chars with
  the same rule. P3 should settle on one id.
- **`oracle_for(cls, ir)`** is the shared class → oracle dispatch (TOOL_INVENTORY §10). `ClassOracle.query` returns
  `(True | False | None, note)`. An exception or a non-bool answer becomes `None`, never `False` (VERDICT_POLICY §4).
  It needs a declared alphabet of single-character symbols and raises `unsupported` otherwise; it does not guess an
  alphabet from a regex.
- **Pipeline finding (agent_system, not fixed there):** `agent_system.lib.oracle`'s predicate
  `exists_decomposition` binds `concat_pattern` elements that are not in `parts` as free parts. For
  `examples/task_z1wawbz2.json` (`z1 w a w b z2`) it therefore accepts `aaaaaaa`. `words.py` evaluates such
  predicates with its own literal-aware split search, which delegates every leaf to the lib's `_eval_predicate`.
  Without literals the lib oracle is used unchanged. The test `test_check_membership_literal_aware_reg_predicate`
  pins both behaviours.
- **CFL `predicate`** goes through the same REG path, not `cfl_oracle.predicate_oracle`, which maps errors to
  `False`.
- **Approximate oracles.** A CFL natural-language `grammar_filter` checks only the grammar, so it is `exact=False`.
  An LL `set_builder` with comment-only constraints that the LL oracle ignores (`format1_anbn_union_ancn.json`) is
  also `exact=False`. The CFL exponent-notation oracle counts as exact but bounded: `None` for |w| > 40 or when its
  step budget runs out. That matches the cfl group's decision.
- **Cost guard instead of process mode.** Split searches cost about (n+1)^parts and one call cannot be interrupted.
  A word whose estimate exceeds `MAX_SPLIT_WORK` (5e6) gets `None` ("work limit"). The tools run in thread mode
  with a `Deadline`, and when the time budget runs out the remaining words are unknown (`truncated`).
- **Sampling.** Candidates come from three sources: every word up to the length where at most `max_enumerated`
  exist, the class generator (cfl `generate_test_words`, dcfl `sample_words`, ll `generate_words`), and seeded
  random longer words. All of them are re-labelled by the oracle. Without an oracle only the generator's positive
  words are reported (`labels_from: generator`, `well_formed`). Its negatives are mutation heuristics ("likely not
  in L"), so they are dropped.
- **`ir_to_text`** is a deterministic Russian paraphrase with no LLM, plus up to 10 oracle-labelled example words of
  length ≤ 10. Decomposition literals are quoted (`'a'`) and `rev(x)` is written `xᴿ`.

## P1 tools — ll group (2026-11-07)

Files: `tools/ll.py`, `tests/test_tools_ll.py` (65 tests, about 5 s serial; no LLM, no network, no Docker).
Tools: `first_follow`, `ll_table`, `ll_parse`, `grammar_transform` (group `ll`, ceiling `verified`).

- **Input.** Every tool takes `grammar` (a typed `Grammar` model: single-character terminals, `$`/`ε` reserved,
  `rhs: []` or `["ε"]` for ε) **or** an `ir` that carries one: top-level `grammar` (LL Format 3), `language_spec`
  of kind `grammar` (LL Format 2, REG/DCFL grammar tasks), or the `grammar` inside a CFL `grammar_filter` spec.
  An IR without a grammar (`set_builder`, `natural`) is `unsupported`. Bad symbols (undeclared, reserved,
  overlapping, multi-character terminal) are `invalid_input`, not `refuted`: the tool cannot compute, so the call is
  not evidence.
- **Grammar id.** `g-` + 12 hex chars of SHA-256 over the canonical normalized grammar (sorted terminals,
  nonterminals and rules), in `data.grammar_id` and in every `claim`. It names the grammar, not the IR (the ir and
  cfl groups use `ir-…` over the IR). The same grammar given as `grammar` or inside an `ir` gets the same id.
- **k up to 12, not `DEFAULT_LIMITS.max_k` = 6 (deviation).** Eval task ll-15 (`S → a^11 b | a^11 c`) asks about
  LL(11) / LL(12). The cost guard is instead: when the lookahead universe `enumeration_size(|Σ|+1, k)` exceeds
  `LOOKAHEAD_INLINE_LIMIT` (5000), the lib call (compute_all / check_ll_k / find_min_ll_k) runs in a killable
  `spawn` child (`run_with_timeout(mode="process")`, 52 s) inside the 60 s thread-mode tool. ll-15 at k = 11 / 12
  takes about 0.3 s that way. Small inputs stay in-thread (ms).
- **Trust.** `first_follow`: verified. `ll_table` with `k`: LL(k) → verified; `is_ll_k` False → refuted
  ("conflict in the LL(k) table for the claimed k", VERDICT_POLICY §1); `None` (budget) → not_verified with
  `bounds.truncated`. With `max_k`: least k found → verified (the lib stops at the first undecided k, so every
  smaller k has a definite negative, THEORY §3.1); left-recursion certificate → verified; no k ≤ K without a
  certificate → bounded_pass (`bounds.max_k`, R5); undetermined → not_verified. Every ll_table result carries
  `scope: "grammar"`, a `claim` string and a note that it says nothing about the language.
  `ll_parse`: verified (accepted or rejected; a word with a symbol outside the terminals is rejected without
  parsing, because `$` in a word would fake the end marker in the lib parser); step limit → not_verified.
  `grammar_transform`: no mismatch → bounded_pass, mismatch → refuted, check skipped / impossible → well_formed.
- **ll_parse limits of the lib.** `LLParser(grammar, k)` crashes on a grammar that is LL(k) but not strong LL(k)
  (`parse_table` is `None`). The tool runs `check_ll_k` itself and uses `LLParser.from_table`; not LL(k) →
  `invalid_input` (conflicts in `data`), LL(k) but not SLL(k) (ll-08 at k = 2) → `unsupported` with a hint (larger k,
  or cfl `cyk`). The productions the parser applied are returned as a leftmost derivation plus sentential forms.
  Pinned against CYK on every word up to length 5–8 for four example grammars.
- **grammar_transform's equivalence check is the package's own**, not `is_grammar_equivalent_sample`: that one
  compares BFS-generated samples (queue / form-size caps can skip short words) and ignores its `alphabet` argument.
  `compare_grammars` runs exact CYK (cfl `to_cnf` + `cyk_parse`) on both grammars over **every** word up to
  `max_len` (default: as many as fit into 2000 words, at most 8; at most 10, and `check_enumeration`), with a 35 s
  `Deadline`; on a timeout `bounds.max_len` is the longest length covered completely. The tool runs in
  `timeout_mode="process"` (the transforms have no budget). ε-elimination is refused up front above 20000 rule
  variants (2^m per rule with m nullable positions), and a transformed grammar above 3000 rules / 600 nonterminals /
  rhs 100 is `input_too_large`. `eliminate_left_recursion`'s `ValueError` (hidden nullable-prefix recursion) is
  `unsupported` with the hint to use `to_ll_normal_form`. The lib builds ε/unit results from sets, so the tool
  restores the input terminal order and sorts rules by (nonterminal order, rhs) for deterministic output.
- **Findings in ll_system (not fixed there):** `parse_word` (`LLParser.parse`) on an LL(k)-but-not-SLL(k) grammar
  raises `AttributeError: 'NoneType' object has no attribute 'get'` (ll-08, k = 2) instead of reporting it; `parse_word` treats `$` inside the word as end of
  input (`S → a`, k = 1 accepts `a$`); `is_grammar_equivalent_sample(..., alphabet=)` ignores `alphabet`.

## P1 tools — reg group (2026-11-07)

Files: `tools/reg.py`, `tests/test_tools_reg.py` (32 tests, about 11 s serial; no LLM, no network, no Docker).
Tools: `regex_to_dfa`, `run_dfa`, `nerode_probe`, `dfa_vs_ir`. `pump_check_reg` (inventory "added") is NOT written
here; it was outside this step's scope.

### Shared code used

- The IR oracle comes from `tools/words.py:oracle_for(cls, ir)` (inventory §10), which also validates the IR
  (`ir.require_valid`) and reads the alphabet (`ir.alphabet_for`). The IR must declare its alphabet; no alphabet
  override argument and no guessing. The fingerprint is `ir.ir_fingerprint` under `data.ir_fingerprint`, the same key
  as the words tools. `data.oracle` is `ClassOracle.describe()`.
- An approximate oracle (`ClassOracle.exact` false: CFL natural-language `grammar_filter`, LL `set_builder` with
  comment-only constraints, e.g. `ll_system/examples/format1_anbn_union_ancn.json`) gives neither `bounded_pass`
  nor `refuted` in `nerode_probe` / `dfa_vs_ir`: every outcome is `well_formed` (pinned by a test).

### Trust decisions

- `regex_to_dfa` (`verified`): "the DFA recognises exactly L(regex) / L(grammar)". The reason text says that this is
  about the given expression, not the task language. The result is always the **minimal complete** DFA over the
  alphabet used (`alphabet=` extends it with a dead state). `n_states` is that state count.
- `run_dfa` (`verified`): exact run on the given DFA. Missing transitions are allowed (implicit dead state, like
  `dfa_runner.run_dfa`) and flagged `partial_dfa`. Structural errors are `invalid_input`: start or accept not in
  states, a transition to an unknown state, a symbol outside the alphabet, a multi-character symbol.
- `dfa_vs_ir` (ceiling `bounded_pass`): exhaustive enumeration of every word up to `max_len` (default: about 4000
  words, so 10 for |Σ|=2 and 7 for |Σ|=3; 40 for |Σ|=1; at most 20000 words).
  - A decided disagreement is `refuted` (up to 5 shortest counterexamples).
  - Agreement on ≥ 30 decided words is `bounded_pass`. `MIN_DECISIVE_WORDS = 30`, the same floor as dcfl R2'.
  - Fewer decided words is `well_formed`; none is `not_verified`.
  - `None` answers are skipped and counted in `n_unknown`.
  - On the inner deadline, or after 5 counterexamples, `bounds.max_len` is the last fully covered length.
  - Deviation from the inventory: `oracle_test.oracle_test` is **not** called. It compares `oracle(w) != dfa(w)`
    directly, so a `None` answer would count as a counterexample. The loop is in the wrapper; the word list comes from
    `word_generator.generate_exhaustive`.
- `nerode_probe` (ceiling `bounded_pass`, VERDICT_POLICY §4 reg/nerode). Modes:
  - explicit `pairs` (`{u, v, suffix?}`);
  - a `family` pattern: `prefix` such as `a^n`, optional `suffix` such as `b^n`, `values` (2–12 values in 0..50),
    `suffix_at: smaller|larger`; all pairs i < j are checked;
  - an optional heuristic `estimate_depth` (≤ 6).

  Trust of the whole request:
  - every pair separated (found or claimed suffix) → `bounded_pass`. Bounds: `n_pairs`, `max_suffix_len`,
    `max_len` = longest word asked, `n_words` / `n_unknown` = distinct decided / unknown words.
  - any claimed suffix that does not separate (incl. u = v) → `refuted`. The pair record then carries an
    `alternative_suffix` found by search, so the agent can fix the proof.
  - any pair left open ("no suffix up to L", or unknown answers) → `not_verified`, with per-pair status in `data`.
    A partial result is not `well_formed`: re-call with the separated pairs for bounded evidence on them.
  - the estimate alone → `well_formed`. It never raises the trust of a request with pairs.

  The empty suffix is allowed (§4). The suffix search is None-aware and in the wrapper, because
  `congruence.find_distinguishing_context` compares `oracle(a) != oracle(b)` and would read `None` vs `False` as
  separating. `compute_nerode_classes` is used only for the estimate, through a strict oracle view: an unknown answer
  or the deadline aborts the estimate (`status: oracle_unknown` / `timeout`).

### Lib findings (libs are read-only)

- `dfa_builder.minimize_dfa` gives a **wrong** DFA on a partial DFA. Example: x -a-> y, y -a-> x, no b
  transitions, comes back with b self-loops, which accept new words. `regex_to_dfa` always completes the DFA
  (`_complete`, dead state) before minimizing. Anyone else who calls `minimize_dfa` must do the same.
- `dfa_builder.parse_regex` reads `.`, `[`, `]`, `{`, `}`, `^`, `$`, spaces and `ε` as **literal symbols**. It does
  not reject them. `regex_to_dfa` rejects them with a hint, rejects back-references (`\1`) and requires `()` for ε.
- `claim_verifier.instantiate_word_pattern` evaluates exponents with `eval` over the charset `[0-9+\-*\s]`. That
  charset allows `**`, so `a^(9**9**9)` would hang the call, and a large count builds a huge string before any
  length check. The family patterns of `nerode_probe` therefore use a local parser (`_parse_pattern` /
  `_eval_exponent`):
  - `ast` with `+`, `-`, `*` only;
  - one variable;
  - bounded intermediate values;
  - the instance length checked block by block.

  It also accepts what the lib regex does not: groups `(ab)^n`, `{}` exponents, `a^nb^n`, and digit symbols.
- The mock outputs `agent_system/examples/task1_palindrome_prefix_suffix_{dfa_builder,re_builder}_output.json`
  ("starts or ends with aa / bb") are **wrong** for their task. `abba` has the even palindrome prefix `abba`, so it
  is in L, but neither the DFA nor the regex accepts it. `dfa_vs_ir` refutes both with `abba`, pinned in a test. This
  is a statement about those mock files only; nothing about the task's regularity is claimed here.

### Limits

- `MAX_DFA_STATES = 500`, for input and output. A 500-state binary DFA is about 20 KB of JSON, so it fits back into
  the 64 KiB call limit. `(a|b)*a(a|b)^9` (1024 minimal states) gives `input_too_large`.
- Regex ≤ 200 chars. `regex_to_dfa` runs with `timeout_mode="process"` and 30 s, because subset construction has no
  budget of its own; the process start-up costs about 0.5–1 s.
- `nerode_probe`: ≤ 80 pairs; ≤ 4096 suffixes per pair (default about 1024, so 9 for |Σ|=2); 30 s.
- `dfa_vs_ir`: 60 s, thread mode with an inner `Deadline` (registry timeout − 3 s), so a slow oracle returns a
  truncated result instead of a timeout failure.

### Tests and real tasks used

- REG: eval reg-04 (regex, pass) and reg-12 (the S → aS | Sb | ε trap). reg-12 itself is `unsupported` for
  `regex_to_dfa`; a right-linear a*b* grammar passes against it. Also reg-13 (family aⁿ / bⁿ: bounded_pass; a+b+
  refuted), reg-01 (natural: no reg oracle), task1 (the refuted mocks; pair ε/aa separated by the empty suffix; aa/bb
  no suffix → not_verified) and task3 (back-reference rejected).
- CFL: task_anbncn (exponent oracle; an unknown above length 40 → not_verified) and task11 (no oracle).
- DCFL: task_anbncm (family aⁿ / bⁿc: bounded_pass; claimed suffix `c`: refuted with alternative `bc`) and
  task_grammar_aSSb (refuted).
- LL: Format 3 (aa*b | bb*a: bounded_pass), Format 2 (pair) and Format 1 (approximate → well_formed).
- The `None` policy is pinned with a scripted `ClassOracle` monkeypatched in place of `oracle_for`.
- Serial and `-n 4` runs are green.

## P1 tools — fix round (review findings, 2026-11-07)

All eight review findings were reproduced before fixing (scratch scripts, not committed); none rejected.
This section supersedes "Membership oracle for pump_check_cfl (`cfl.language_oracle`)" in the cfl notes above.

### Findings and fixes

- **F1 (major, cfl) — pump_check_cfl had its own oracle dispatch.** It went through
  `cfl_oracle.cfl_oracle_from_ir`, whose predicate path binds `concat_pattern` literals as free parts, and it
  mapped oracle errors to `False`. On `agent_system/examples/task_z1wawbz2.json` as `classify_cfl` it reported
  "'aaaaaaaa' IS in L" and refuted a correct exit claim. Fix: `cfl.language_oracle` now goes through the shared
  `words.oracle_for` (the dispatch `check_membership` uses). New optional `cls` argument; auto mode tries
  cfl → dcfl → ll → reg, skipping a class whose IR schema does not validate. The natural-language-filter
  oracle is still refused (`unsupported`). An answer is `oracle.query(w)[0]`: `None` stays unknown, errors
  never become `False`, and the cost guard / `max_word_len` of `ClassOracle` apply (this is the missing
  `MAX_SPLIT_WORK`-style guard). An inexact oracle never refutes: a `refuted` from claim mode is lowered to
  `not_verified` with the reason. Regression test pins agreement with `check_membership(cls="cfl")`.
- **F2 (major, dcfl/cfl) — tools named in descriptions and the inventory did not exist.** Implemented
  `dpda_vs_ir` (dcfl), `grammar_vs_ir` and `input_grammar_cfg` (cfl). Shared helpers in `words.py`:
  `compare_with_oracle` (every word up to `max_len_within(|Σ|, 3000, 10)`, then generator words; a word counts
  only when both the oracle and the artifact decide it; stops at 5 counterexamples or the deadline) and
  `comparison_result` (exact oracle + counterexample → `refuted`; no decided word → `not_verified`; approximate
  oracle or < 30 decided words → `well_formed`; else `bounded_pass` with `max_len`, `n_words`, `n_unknown`,
  `exhaustive_up_to`, `truncated`). `dpda_vs_ir` reuses the `dpda_run` preparation (`_prepare_dpda`:
  normalization, determinism, structure) so the R2' refutations are the same in both tools.
  Deviation from the inventory: `grammar_vs_ir` does **not** call `cfl_oracle_test.oracle_test_grammar` (it
  builds its oracle with the same buggy `cfl_oracle_from_ir` dispatch as F1); the PDA variant is deferred.
  `dpda_vs_ir` does not call the private `_verify_stack_strategy_dpda`; same R2' rule via the shared helpers.
  `pump_check_reg` stays **not implemented (deferred)** and is marked so in TOOL_INVENTORY §3.
- **F3 (minor, words) — sample_words overstated exhaustive coverage.** With `max_enumerated < |Σ| + 1`, length 1
  was reported exhaustive and the "no word of length ≤ N is in L" note was emitted. `exhaustive_up_to` is now the
  last length enumerated in full (`None` when none), the note needs a fully enumerated N and no unknown word,
  and random words start after the exhaustive length.
- **F4 (minor, cfl) — split mode with every pumped word undecided gave `well_formed` "survived".** Now
  `not_verified`, "no pumped word could be decided by the oracle (z in L = …)".
- **F5 (minor, ll/cfl) — invalid submitted grammar was `invalid_input`, but `dpda_run` gave `refuted`.**
  VERDICT_POLICY §1 lists "невалидные символы грамматики" under `refuted`, so the policy rule wins: a
  **submitted** grammar / PDA with undeclared or overlapping symbols or an undeclared start → `ok`, `refuted`,
  `data = {valid: false, validation_errors, <artifact hash>, evidence_role: "invalid_artifact"}` in `to_cnf`,
  `cyk`, `parikh` (grammar mode), `grammar_vs_ir`, `pda_run` and all four ll tools. Kept as `invalid_input`:
  schema / shape errors (missing PDA keys, multi-character terminals), size limits, and a grammar embedded in
  the IR (ll `ir.grammar`) — the IR is the agent's own input, not an artifact under test.
- **F6 (major, cfl) — one admissible split gave `well_formed`.** A single split is a diagnostic, never a
  destructive argument (VERDICT_POLICY §4 cfl/pumping): split mode is capped at `not_verified`,
  `data.evidence_role = "diagnostic"`; `refuted` only for z ∉ L or a confirmed-exit claim contradicted by an
  exact oracle. Claim mode keeps its `well_formed` ceiling.
- **F7 (major, words) — check_membership gave `bounded_pass` with an undecided expected word.** Trust is now
  computed over the expected words only; any undecided expected word → `not_verified`
  (`data.undecided_expected`). Same pattern fixed in `cyk` (expected word cut by the time limit →
  `not_verified`, `data.unchecked_expected`) and `pda_run` (expected word unknown, or expected-accept with an
  inexact rejection → `not_verified`, `data.open_expected`).
- **F8 (minor, words) — oracle-only labelling reported `bounded_pass`.** `sample_words` is now
  `max_trust="not_verified"`, `allow_refuted=False`, `evidence_role = "reference"`; `check_membership` without
  `expected` is `not_verified` with the same role. Bounds are still reported for the reader.

### Evidence roles (`data.evidence_role`)

`reference` (oracle-only), `claim_check` (expected labels), `diagnostic` (one pumping split), `artifact_check`
(artifact vs IR), `invalid_artifact` (submitted artifact with invalid symbols), `input_grammar` (R2-CFG).
P3's ledger can use the role to keep reference / diagnostic results out of the verdict evidence.

### Tests

Updated the tests that pinned the old trust (sample_words / check_membership ceilings, cfl invalid grammar and
PDA cases, pda_run stack cap, oracle source strings, split-mode trust, ll invalid grammar). Added regressions for
each finding plus `grammar_vs_ir` (pass / counterexample / exponent IR / invalid grammar / no oracle),
`input_grammar_cfg`, `dpda_vs_ir` (safe dcfl-04 → bounded_pass, live dcfl-04 → refuted on `b`, mock vs
task_anbncm, nondeterministic, bad IR) and the cyk time-limit cut (monkeypatched `Deadline`).
`pytest -q -n 4 tfl_deep_agent/tests`: 611 passed, 1 xfailed (serial run also green). Full suite with `-n 4`:
4236 passed, 41 failed. All 41 are Lean / Docker tests in agent_system, cfl_system and dcfl_system. The Docker
daemon is failing on this host (`runc did not terminate successfully: exit status 139`, `read init-p:
connection reset by peer`; a serial re-run of `test_phase3::test_dfa_template_compiles` fails the same
way, while `dcfl_system/tests/test_constraint_semantics.py` passes serially). None of them are in tfl_deep_agent.

## P1 tools — GATE (2026-11-07)

- `pytest -q tfl_deep_agent/tests` (serial): 611 passed, 1 xfailed.
- Full suite, `docker` hidden from PATH (Docker tests skip cleanly): **4186 passed, 161 skipped, 3 xfailed, 0 failed** (`-n auto`, 88 s).
- Full suite with Docker visible: 34 failed, 4238 passed, 75 skipped, 3 xfailed. All 34 are Lean-in-Docker tests in
  agent_system / cfl_system / dcfl_system that hit the flaky daemon (`OCI runtime create failed ... read init-p:
  connection reset by peer`). A serial re-run of three of them: 2 passed, 1 failed with the same daemon error. None of them are in
  tfl_deep_agent, and none touch P1 files.
- Registry check (`load_groups()`): 26 tools in ir/words/reg/cfl/dcfl/ll/theory. Every tool name is used in a
  `tests/test_tools_*` file, and every ceiling is at or below its group's `GROUP_MAX_TRUST`. Caps come from
  `agent_system.lib.claim_verifier.CONFIDENCE_CAPS` (0.98 / 0.85 / 0.55 / 0.40). The lean and report groups register no tools yet (P6 / P3).

## P2 core — skill:ll (2026-11-07)

Files: `skills/ll/SKILL.md`, `skills/ll/theory.md`, `skills/ll/traps.md`, `tests/test_skill_ll.py` (56 tests, ~6 s;
no LLM / network / Docker).

- **Format** (deepagents 0.7.34 `middleware/skills.py`): YAML frontmatter with `name: ll` (must equal the folder
  name), `description` (≤ 1024 chars, else deepagents truncates it), `allowed-tools` (advisory; space-separated) and
  `metadata` (string values only). `metadata.include_tools` is **not** used: it would gate tool disclosure on reading
  the skill, which is an architecture decision for `agent.py`, not for a skill file. Supporting files are referenced
  by their virtual path `/skills/ll/<file>.md`. The test assumes `/skills/` maps to `tfl_deep_agent/skills/`. It
  loads them through `FilesystemBackend(root_dir=tfl_deep_agent, virtual_mode=True)` + `SkillsMiddleware.before_agent`.
- **Theory quotes are verbatim and machine-checked.** Every quote sits in a `<!-- quote: ID -->` + fenced `text`
  block. The test requires the block to be a substring of `TheoryDoc.own_text(resolve(ID))` (or of
  `ll_system/tz_ll_agent_system.md` for `tz_ll`), and it must not contain `[ошибка]`. `theory.md` was generated once
  from exact THEORY.md line ranges (a scratch script, not committed), then committed as a static file. If THEORY.md
  is edited, the test names the drifting quote. Status marks the skill reports (§3.1/3.2/3.3/1.2/1.5
  `[проверено]`, §3.4 `[доказано]`, §3.5 none, §1.4 `[источники и границы указаны отдельно]`) and the "not read"
  caveats ([AU], Kurki-Suonio/RS strict hierarchy, [RS] via [Ni]) are pinned against the parser.
- **Tool calls in the text are checked.** Each backticked `name(arg=...)` must be a registered tool with those
  argument names (`rev(...)` is the set_builder template token, not a call). The unimplemented `ll_claim_check` and
  `pump_check_reg` are not mentioned. `max_k=12` is pinned to `ll.LL_MAX_K`.
- **Promised tool outcomes are pinned:** ll-08 (LL(2), not SLL(2), least k 2, strong_k 3), ll-09 (k=2 refuted,
  k=3 verified), ll-10 (left-recursion certificate, scope grammar), ll-11 (given grammar certificate → transform
  bounded_pass → LL(1) verified → grammar_vs_ir bounded_pass), ll-15 (max_k=10 bounded_pass, max_k=12 → 12),
  ll-17, ll-18; the ll-06 constructive path (THEORY §3.4 grammar vs a set_builder IR → bounded_pass, vs the ll-07
  mirror IR → refuted); branch-word witness checks via `check_membership(expected=...)`; the regex → DFA grammar
  route for ll-13 (LL(1), ll_parse agrees); natural-kind IRs have no LL oracle; the eval ll-02 IR's oracle is inexact
  (comment-only constraint). The trap table in `traps.md` must cover every `ll-*` id in `tfl_eval/manifest.json` with
  the manifest's verdict / k, and must flag every manifest trap.
- **Decisions / deviations.**
  - Trust for "language is LL(k)" via the **given** Format 2 grammar is `verified`. VERDICT_POLICY R2 only says
    "Format 1/2 → bounded_pass by sample equivalence". The skill follows `ll_system/orchestrator.py`
    (`grammar_source == "given_grammar"` → verified) and says so. P3's ledger should make the same call, or the
    skill text must change.
  - Destructive "not LL" proofs: the skill states VERDICT_POLICY §4 ll/substitution (up to `bounded_pass` with
    instantiated, oracle-checked branch words). It says the ledger applies this, not the model. No deep-agent tool
    implements the full substitution contract check yet (`ll_claim_check` is optional/unimplemented). Until P3
    decides, a written proof plus `check_membership` evidence is what exists.
  - Least k of a **language**: the skill tells the agent that the least k of its grammar is only an upper bound.
    "Not LL(k₀−1)" for the language needs its own proof (from the §3.1 definition). This is a consequence of the
    quoted definition, not a new lemma.
  - Route (B), inherent ambiguity: no tool checks it, so a hand proof is `well_formed` and `uncertain` is acceptable.
    R5 lists an ambiguity certificate as `verified` for a *grammar*, but no tool produces one.
- **Needs before commit:** the adversarial Opus review of the theory text (PLAN non-negotiable 5). The quotes are
  verbatim, but the surrounding guidance (route table in SKILL.md §6, trap explanations in traps.md) is my
  condensation and has not been reviewed.

## P2 core — skill:dcfl (2026-11-07)

Files: `skills/dcfl/SKILL.md` (frontmatter + procedure + methods + tool map + evidence table + trap summary),
`skills/dcfl/traps.md` (T1–T14), `skills/dcfl/dpda.md` (DPDA contract, R2' checks, design rules, tz §5.2 example),
`tests/test_skill_dcfl.py` (36 tests).

- **Frontmatter.** `name: dcfl` (= dir), description 1024-char limit checked with deepagents' own
  `_parse_skill_metadata`; `allowed-tools` as a YAML list (only displayed by SkillsMiddleware, not enforced);
  `metadata` holds `classes` / `sources` / `supporting-files` strings, never `include_tools` (that key is special).
  Supporting files are addressed as `/skills/dcfl/<file>.md` (read-only `/skills` mount, architecture doc).
- **Quotes.** Every statement block is introduced by `<Source> §N (verbatim):` and a blockquote; the test checks each
  one is a whitespace-normalised substring of `docs/THEORY.md`, `docs/VERDICT_POLICY.md` or the tz spec. Inline
  quotes in prose are pinned by a separate list. Section citations §1.1–§1.10, §2.0, §3.2, §3.3 are resolved with
  `tools.theory.load_theory()`; status markers are compared with the parsed headings ([проверено] for 1.1/1.2/1.3/1.5,
  §1.4 is "[источники и границы указаны отдельно]" and the skill says so, [GG] not read in full; §1.6–1.9 have no marker).
- **Method order** (destructive): closure reduction → continuation lemma → Shallit (only with D = ∅) → Yu → inherent
  ambiguity; closure / L_$ first because they end in a non-CFL claim that `pump_check_cfl` and the Lean route can test.
  This is my ordering, not a THEORY.md rule.
- **Policy over spec.** Where tz §6.1.3/6.1.4 and §12.4 disagree with VERDICT_POLICY §4 / THEORY §1.4, the skill
  follows the policy/THEORY (surviving Yu split = unclosed, finite dead-word search = unknown). Stated in traps.md T13.
- **Evidence table** follows VERDICT_POLICY §2–4: DPDA + `dpda_vs_ir` ≥ 30 decided words → `bounded_pass`; Yu without
  instances → `not_verified`, all splits closed at p ∈ {2,3} → `well_formed`; Shallit continuation stage ≤ `bounded_pass`
  and `well_formed` while the dead-class premise is open; closure/L_$ claims rated `well_formed` because the non-CFL
  part is at most `well_formed` (`pump_check_cfl` ceiling) — conservative reading.
- **Findings (tools measured, libs untouched):**
  - eval `dcfl-11.json` as written has **no oracle** (`$` letter and the trailing shared ", n,m >= 1" are not parsed);
    renaming `$`→`e` and per-branch conditions gives an oracle, and the tz §5.2 DPDA (renamed) gets `bounded_pass`
    (exhaustive to length 4, ~800 words). Both pinned by tests. The tz example is the DCFL eval answer for dcfl-11 —
    putting it in the skill is an eval-contamination trade-off (same automaton is already in the tz spec and THEORY §1.9
    recommends it as the stack_strategy example); flag for the eval owner.
  - unary alphabet: `dpda_vs_ir` decides only 11 words (lengths 0..10) → `well_formed`; `dfa_vs_ir(cls="dcfl")`
    reaches `bounded_pass` on dcfl-17 (41 words) — the skill routes regular languages to the DFA path.
  - `input_grammar_cfg` rejects DCFL task types; the skill says to call it on a CFL-typed copy and that it certifies
    CFL only (THEORY §2.0).
  - `pattern_match` suggests non_dcfl on DCFL dcfl-04 → hints marked advisory.
  - the exam_04 certificate (5835 transitions) exceeds the 2000-transition tool limit; the skill says not to claim a
    tool check for it.
- **Not covered / open:** no Yu instance checker tool (`dcfl_claim_check` not implemented) — Yu evidence is
  `check_membership` on hand-picked instances; Lean tools (`lean_statement`, `lean_check`) are named as future tools
  (allowlisted in the test). **Needs before commit:** the adversarial Opus review of the non-quoted guidance
  (procedure, evidence table, trap explanations), per the light-model-proof-drift rule.

## P2 core — skill:cfl (2026-11-07)

- **Files:** `skills/cfl/SKILL.md` (procedure, proof methods, tool map, evidence/caps, theory status table),
  `skills/cfl/traps.md` (EVAL_SET CFL trap rows + failure patterns from runs), `tests/test_skill_cfl.py` (20 tests).
- **Decisions:**
  - Frontmatter: `name`, folded `description` (≤1024, mentions the CFL task types, key tools, traps.md, "Not for DCFL
    or LL(k)"), `metadata` with string values only (`sources`, `supporting_files`). No `allowed-tools` and no
    `metadata.include_tools` (it would hide the tools until the skill is read).
  - Quote convention: a line `Verbatim, <THEORY.md|tz_cfl|VERDICT_POLICY.md> §<id>:` followed by `> ` lines. The test
    checks each quote word for word (whitespace-normalised) against the named THEORY section's `full_text` (via
    `load_theory().resolve`) or the whole tz/VERDICT_POLICY file, refuses any unmarked blockquote, and refuses quotes
    taken from the wrong sentence of an `**[ошибка]**` paragraph. Guidance is English; theory statements stay Russian
    quotes. Mutation check done by hand: changing `|vx| ≥ 1` to `≥ 0` in a quote and `claim=` to `claims=` in a call both
    fail the tests.
  - Every backticked `tool(...)` must be a registered tool with real argument names, a later-phase tool listed in
    TOOL_INVENTORY (`lean_statement`, `lean_check`, `budget_status`, `spawn_subagent`, `run_workflow`,
    `list_report_templates`, `submit_report`), or `read_file`/`execute`. The tool-map table may not claim a trust above
    the tools' ceilings; the caps table equals `CONFIDENCE_CAPS`; trap rows' id/language/verdict equal EVAL_SET and every
    `[trap` row of the CFL table is present.
  - Recipes are pinned on the real tools: the SKILL.md pumping-claim JSON on `task_anbncn.json` → `well_formed`,
    p ∈ {3,4}, 0 unresolved splits; cfl-06 closure check (wrong {aⁿbᵐaⁿcᵏ} refuted by `abaac`, the corrected L ∩ R
    `bounded_pass` on 81 words of R); cfl-17 grammar S → aaSbb | ε `bounded_pass`, S → aSb | ε refuted,
    `input_grammar_cfg` `not_verified` (grammar_filter) and `verified` on cfl-04; {ww} as `exists_decomposition` gets the
    exact split-search oracle; ticket-50 instances a⁸b(ab)⁵a⁸ ∉ L, a⁸b(ab)⁴a⁸ ∈ L.
- **Findings / deviations:**
  - No CFL section of THEORY.md carries a plain `[проверено]` marker (§2 and §2.5 are `[проверено перебором]`, §2.0/§2.3/
    §2.4 unmarked; §1.5 `[проверено]` is in the DCFL part). The skill's status table says so and a test pins it.
  - cfl-10 ({a^{2ⁿ}}) and cfl-03 ({ww} as text) have no oracle as written; EVAL_SET's cfl-10 justification ("унарный КС
    = регулярный; Париха") has no THEORY section, so the skill does not quote it as a theorem.
  - **cfl-11 gap:** the exponent-notation oracle stops at `max_word_len` 40; a¹⁸b¹⁸c¹⁸ / a²⁴b²⁴c²⁴ are undecided and
    `pump_check_cfl` still returns `well_formed` with `p_values: []`, `n_unknown: 2`, 0 splits — nothing was checked.
    The skill tells the agent to read `bounds.p_values`; a tool-side downgrade (e.g. `not_verified` when no instance is
    decided) would be safer — proposed for the tool owner, not done here (out of scope).
  - No PDA-vs-IR tool for CFL: a PDA is checked only by `pda_run` with oracle labels as `expected`. Closure
    `bounded_pass` covers the intersection description on sampled words only, not the pumping of L ∩ R.
- **Not covered / open:** Lean, report and orchestration tools are named as future tools only. `test_skeleton.py::
  test_cli_stub_returns_not_implemented` fails in the full run because `cli.py` was changed concurrently (mock mode now
  needs `--mock-script`); not in this scope. **Needs before commit:** the adversarial Opus review of the non-quoted
  guidance (procedure, evidence table, trap explanations), per PLAN non-negotiable 5 / the light-model-proof-drift rule.

## P2 core — skill:reg (2026-11-07)

Files: `skills/reg/SKILL.md` (procedure, tools, evidence table, trap checklist; ~17 KB), `skills/reg/theory.md`
(statements verbatim), `skills/reg/traps.md` (trap tasks, worked routes, tool pitfalls),
`tests/test_skill_reg.py` (67 tests, about 9 s; no LLM, network or Docker).

- **Format.** Frontmatter `name: reg`, a `description` of 969 characters (limit 1024) that names the task types,
  the methods, the core tools and the five trap ids, plus string-valued `metadata` (`sources`, `companions`). No
  `allowed-tools`: it is experimental in deepagents and would add a line to every system prompt. The test loads
  the skill through `SkillsMiddleware(backend=FilesystemBackend(root_dir=skills/), sources=["/"]).before_agent`
  and asserts there are no warnings. Companion files are referenced by their virtual paths `/skills/reg/*.md`,
  which assumes the skills directory is mounted at `/skills/` (architecture: CompositeBackend, read-only).
- **Quoting convention (tested).** A line `Source: \`<repo path>\` §<n>` (or `#<anchor>` for THEORY_REFERENCE)
  followed by a `>` block. The test checks that the block is verbatim, after whitespace normalisation, in **that
  section** (THEORY.md through `tools.theory.load_theory().resolve`, other files by heading number or anchor). Short
  inline quotes are pinned in `INLINE_QUOTES`. Every `THEORY.md §n`, `tz §n`, `VERDICT_POLICY §n` and `R<n>` cited
  must exist. Every `` `tool(`` called must be registered, listed in TOOL_INVENTORY.md (lean / report /
  orchestration, later phases) or be a deepagents built-in.
- **Theory sources.**
  - THEORY.md has only one REG section, §0 (part II, [доказано индукцией и Нероудом], about G_reg). So the general
    statements (pumping lemma, Myhill–Nerode, closure, pumping length) are quoted from
    `agent_system/tz_tfl_agent_system.md` §1.5–1.9 and labelled as tz statements that THEORY.md does not review.
  - THEORY_REFERENCE.md (the registry THEORY.md points to) is quoted twice: the "non-regular subset proves nothing"
    rule (#reg) and the counting-filter limits (#filters).
  - The tz §1.9 regex bound and its NP-hardness remark appear in no reviewed file. The skill marks them "not
    reviewed, do not cite".
- **Trust mapping.** Taken from VERDICT_POLICY and the P1 tool ceilings:
  - Nerode with `nerode_probe` bounded_pass gives non_regular ≤ 0.85 (R6 "с проверкой слов оракулом").
  - Pumping is ≤ 0.55 (§4 reg/pumping).
  - "Regular" needs `dfa_vs_ir` bounded_pass (R2), or `regex_to_dfa` verified when the regex or linear grammar is
    the task's own definition.
  - Lean is `verified` only through the P6 tools.
- **pump_check_reg is still not implemented.** For a single-split REG diagnostic the skill uses `pump_check_cfl`
  with `cls="reg"`, (x, y, z) → (u, v, w) and empty CFL x, y, and **without `p`**. With `p`, the tool checks the
  CFL window |vwx| = |yz| ≤ p and rejects valid REG splits. A test pins that behaviour. A real `pump_check_reg`
  (|xy| ≤ p) would let the skill drop this workaround.
- **Eval trap IRs.** reg-03, reg-07 and reg-09 are `kind: natural` in `agent_system/examples/eval/`, so they have
  no oracle. The skill shows faithful predicate encodings for reg-03 and reg-09, and the test checks that they
  validate with an exact oracle and that the proposed DFAs reach bounded_pass. reg-07 (a^{n²}) has no oracle in any
  IR kind, so it stays ≤ 0.55 without Lean. The worked pumping argument is in traps.md.
- **Theory text written here** (applications, not new lemmas). Each needs the Opus adversarial review before commit
  (PLAN non-negotiable 5):
  - the reg-07 pumping inequality p² < p²+k ≤ p²+p < (p+1)²;
  - reg-03 "#a ≡ #b (mod 2) ⇔ |w| even";
  - the reg-12 inclusions;
  - the reg-05 / reg-10 suffix choices;
  - the closure-to-Nerode transfer (words of R separating K also separate L);
  - complement minimality (≡_L = ≡ for Σ*∖L);
  - the pumping-length recipe (upper bound only from DFA size; the lower bound needs ∀ splits).
- **Probe artefact, not a bug.** `regex_to_dfa` accepts control characters as literal symbols (`"(a)\x01*"`
  produced a DFA over {\x01, a}). Back-references written correctly (`\1`) are rejected as documented.
- **Full deep-agent suite** (`-n 4`): 875 passed, 1 xfailed, 1 failed. The failure is
  `test_skeleton.py::test_cli_stub_returns_not_implemented`: `cli.py` was being rewritten concurrently by another
  P2 step, so it is outside this scope.

## P2 core — settings + budget + concurrency (2026-11-07)

Files: `settings.py`, `middleware/budget.py`, `middleware/concurrency.py`, `tests/test_settings.py`,
`tests/test_budget.py`. `settings.example.json` is unchanged (it already equals `settings.DEFAULTS`; pinned).

### settings.py

- pydantic v2 models, `extra="forbid"`, `strict=True` (a JSON string `"5"` for a number is an error), validated on
  assignment. Sections: budget / models / limits / hitl / intake, exactly the PLAN schema.
- Path: explicit arg > `$TFL_DEEP_SETTINGS` > `tfl_deep_agent/settings.json` (gitignored). No file → `DEFAULTS`.
  A partial file is deep-merged over the defaults (objects key by key, lists replaced). **A broken file raises
  `SettingsError` listing every problem; it never falls back to defaults silently** (it holds money limits).
- Model ids accept aliases (`haiku` / `sonnet` / `opus`) and are stored as ids; every id needs a price card
  (`config.pricing_key`). `models.roles` keys must be known roles (`config.MODELS`), and every role model must be in
  `models.allowed` (narrowing `allowed` therefore needs the roles changed too: an explicit error, not a silent swap).
- Consistency: `per_task_usd > 0`, `per_task_usd <= per_day_usd`, `reserve_for_report_usd < per_task_usd`,
  `per_subagent_max_usd > 0`. `per_subagent_max_usd > per_task_usd` is allowed on purpose (a UI override that only
  lowers the task budget must validate); the ledger caps an allocation by what is free anyway.
- Overrides: `apply_overrides(settings, mapping)` (UI `settings_override`) and
  `parse_override_pairs(["budget.per_task_usd=3", "models.roles.main=sonnet"])` (CLI; values parsed as JSON, else
  string). `save_settings` validates and writes the full document atomically (temp file + `os.replace`).
  `settings_json_schema()` for the UI form.
- `Settings.model_for_role(role)`: `TFL_MODEL_OVERRIDE` > `models.roles` > `config.MODELS`; the override wins over
  `models.allowed` (it is the shared cheap-test switch). P4's `model_policy` decides whether ad-hoc picks may bypass.

### middleware/budget.py

- `BudgetLedger` (one per task) with a tree of `BudgetNode`s. `spent` / `inflight` are subtree totals. An allocation
  (`node.allocate(name, usd, kind="subagent"|"branch"|"workflow"|..., reserve_usd=0)`) is a promise taken out of the
  parent at once; `subagent` / `branch` are capped by `per_subagent_max_usd`, a `workflow` container only by what is
  free. Allocations never touch the parent's reserve. `available()` = own remainder (limit − used − open children's
  unspent promises), bounded by every ancestor's remainder (an ancestor that overran shrinks its children) and by the
  day's remainder. `close()` returns the unspent part.
- Every call: the worst case `config.worst_case_usd(model, est_input, max_tokens)` is **reserved (in flight)** before
  the handler runs, so parallel branches cannot over-commit; it is replaced by the actual cost afterwards, and
  released on an exception (an API error charges nothing).
- Wrap-up: a node enters it when the next call does not fit in `available − reserve`; that call (and later ones) may
  use the reserve. In wrap-up a `HumanMessage` "BUDGET WRAP-UP ... submit_report ..." is appended **to the request
  only** (not stored in state; appended at the end so the cached prefix survives), the spawning tools `task`,
  `spawn_subagent`, `run_workflow` get an error `ToolMessage`, and `allocate` raises. A child scope gets a "return
  your final answer to the caller" variant. An ancestor's wrap-up applies to its descendants.
- Refusal: when even `available` does not fit, the handler is not called and an `AIMessage` without tool calls comes
  back: text with the numbers, `response_metadata["stop_reason"] == "budget_exhausted"`,
  `response_metadata["tfl_budget"]` = the numbers. The node's state becomes `exhausted` and `ledger.exhausted` is
  true. **P3 renderer: use `ledger.exhausted` (or that stop_reason) for the status `budget_exhausted`.**
- Actual cost: `usage_metadata` → plain input = `input_tokens − cache_read − cache_creation` (P0 note), priced with
  `llm_client.estimate_cost_usd` (Haiku 5.5 long-prompt card included). The response's `model_name` is used for
  pricing when it has a price card (the server-side refusal fallback may answer on another model). No usage metadata
  → charged at the worst case (`missing_usage="worst_case"`, default) or 0 (`"zero"`, for scripted runs).
- Input tokens are estimated **locally** (count_tokens would be an API call): ASCII 3 chars/token, non-ASCII 1.5,
  +6 per message, +600 per request, tool schemas as JSON, image 7000, PDF 6000 per page (pages read with pypdf; 20 if
  unreadable); base64 payloads are never counted as text. The middleware scales later estimates up by the largest
  actual/estimate ratio it has seen (cap 4x, never down). So the first call of a scope can be underestimated if the
  heuristic is wrong; the actual cost is charged in any case.
- Per day: `DailySpendLog` = `<runs_dir>/deep_spend_log.jsonl`, `runs_dir` = `$TFL_DEEP_RUNS_DIR` or
  `<repo>/.tfl_lab_runs` (TFL Lab's gitignored runs dir, so the CLI and the UI share one day). One line per charged
  call (`task_id`, `day` = local date, `usd`, tokens, model, role, node). `BudgetLedger.for_task(settings,
  task_id=..., spend_log=...)` refuses to start (`DailyBudgetExceeded`) when `per_task_usd` does not fit in the day's
  remainder; each call is also checked against `per_day − day_total − this task's in-flight` (another process may
  spend meanwhile). `for_task(..., resume=True)` (HITL resume in a new process) skips the start check and starts the
  root at `log.task_total(task_id)`. Bad log lines are skipped and counted, never rewritten.
- `ledger.summary()`: totals, `by_role`, `by_model`, the tree (UI budget bar; P4's `budget_status` can return
  `node.status()`). `listeners=[fn]` receive `allocate` / `call` / `wrap_up` / `refused` / `close` events
  (exceptions in a listener are swallowed).
- **Hook for `agent.default_middleware`**: `budget.make_middleware(settings, role=...)` returns `None` unless the
  agent is built inside `with use_budget(ledger_or_node, **middleware_kwargs):` (or `node=` is passed). So tests and
  builds without a ledger never write to the real runs-dir spend log. Scripted fake-model runs should use
  `use_budget(ledger, missing_usage="zero")` or give their fake replies `usage_metadata`; otherwise every call is
  charged its worst case (Opus 64000 → $1.28+) and a $5 task is refused after about 3 calls. The fallback model for
  chat models without `.model` is the role's model from settings via `config.resolve_model` (the override applies).
  `concurrency.make_middleware(...)` always returns a `ConcurrencyMiddleware`.
- Order: put `BudgetMiddleware` inside any middleware that swaps `request.model` (it prices the model it sees).
  A `ModelCallLimitMiddleware` for `limits.max_model_calls` is not part of this module.

### max_tokens vs budget (the open P0 decision) — NOT decided here

- The default follows the spec literally: the worst case uses the full `max_tokens`, and the call is refused when it
  does not fit. With the current ceilings (Opus 64000 = $1.28) a $5.00 task enters wrap-up once less than $1.58 is
  left.
- Option (b) from "P0 fix" is implemented but **off**: `BudgetMiddleware(..., fit_max_tokens=True,
  min_output_tokens=4096)` lowers `max_tokens` for one call (through `model_settings`, which reaches `ChatAnthropic`
  as a call kwarg) to what fits, and refuses below the floor. Turning it on (or per-role ceilings, option (a)) is the
  user's call; `test_config.py::test_default_ceilings_fit_the_example_budgets` stays a strict xfail.

### concurrency.py

- Reuses `agent_system.lib.llm_client.get_concurrency_semaphore()` (looked up at call time, so `TFL_MAX_CONCURRENCY`
  set before the first call takes effect; the same object as the pipelines'). Sync: blocking acquire. Async: polls
  `acquire(blocking=False)` with a 5 → 100 ms back-off, so it never blocks the event loop and a cancelled waiter holds
  no slot. One slot per model call (SDK retries included). Tool calls (and so subagent runs inside `task`) are not
  wrapped, so a parent waiting on a subagent never holds a slot: no nested-acquire deadlock.

### Tests

- `tests/test_settings.py` (43) and `tests/test_budget.py` (43): ledger arithmetic, sibling promises, ancestor
  overrun, in-flight parallel reservations, the wrap-up / refusal state machine, the daily log (injected clock, bad
  lines, start check, spend by another process, resume), the token estimate (images, PDF pages via pypdf, tools),
  cache-aware pricing, and the middleware inside real `langchain.agents.create_agent` graphs with a local scripted
  `BaseChatModel` (sync + async, refusal without calling the model, wrap-up injection and the blocked spawn tool,
  missing usage, fit_max_tokens, release on error, estimate scaling, three threaded branches under one workflow
  allocation), the semaphore bounds, and the `make_middleware` hooks.
- `pytest -q -n 4 tfl_deep_agent/tests`: 875 passed, 1 xfailed, 1 failed (`test_skeleton.py::
  test_cli_stub_returns_not_implemented`, caused by the concurrent `cli.py` change; not in this scope).

## P2 core — agent, roles, prompts, cli (2026-11-08)

Files: `agent.py`, `roles.py`, `prompts/{main,ir_builder,prover,critic,coder,intake,lean}.md` (intake / lean are
stubs for P3 / P6), `cli.py`, `examples/mock/{reg_regex_regular,cfl_anbmcm,ll_simple_ll1}.json`,
`tests/test_agent_fake_model.py` (30 tests incl. parametrized, ~13 s, no API / network / Docker). `tests/test_skeleton.py`: the P0
"cli stub returns not implemented" test now asserts the mock-mode refusal (`--mock-script` in the error).

### Decisions (deepagents 0.7.34)

- **write_todos is not built in** in this deepagents version: `create_tfl_agent` adds langchain's
  `TodoListMiddleware()` to the main agent itself.
- **general-purpose subagent replaced.** deepagents adds a "general-purpose" subagent (main model, all main tools,
  none of our middleware) unless a spec with that name exists. `roles.ROLES["general"]` is that spec: Haiku by
  default, theory tools only, inline prompt, with the same budget / concurrency middleware as every other role, so it
  is no hole in the budget.
- **Declarative subagents do not inherit the main middleware**: every role spec gets its own list
  (`default_middleware(settings, role)` → `make_middleware(settings, role=...)` of `middleware/budget.py`,
  `concurrency.py`, `evidence.py`; `None` = nothing). `create_tfl_agent(middleware=..., subagent_middleware=...)`
  replaces the hook (a list shared by all roles, or `role -> list`). Budget only applies inside
  `budget.use_budget(ledger)` (contextvar read at build time), as `budget.py` documents.
- **`execute` is removed** from every agent except roles with `allow_sandbox=True` (coder) by a
  `wrap_model_call` filter; P4 adds the sandbox backend and HITL for it.
- **Skills are read-only**: `CompositeBackend(default=StateBackend(), routes={"/skills/":
  ReadOnlyFilesystemBackend(tfl_deep_agent/skills, virtual_mode=True)})` plus a `FilesystemPermission(write, deny)`
  on `/skills/**`. Both layers: the backend refuses write / edit / delete / upload even if a permission rule is
  dropped. `skills=["/skills/"]` for main, prover, critic, ir-builder, lean; not for intake / coder / general.
- **Run termination without `return_direct`.** langchain ends the loop on `return_direct` even when the tool call
  failed validation, so an invalid report would end the run. Instead `ReportGateMiddleware.before_model` jumps to
  `end` only after a **successful** `submit_report` ToolMessage (artifact `ok: true`). An invalid report returns a
  JSON error and the model gets another turn (tested).
- **submit_report (minimal, P3 replaces it):** `verdict`, `language_class` (reg/cfl/dcfl/ll), `method`, `steps`
  (1..60 of `{claim, justification, evidence_ids}`), `evidence_ids`, `template` (4 templates), `summary`,
  `ir_fingerprint`; `extra="forbid"` via `ToolArgs`, so trust / confidence fields cannot be passed.
- **Evidence ids in P2 are tool-call ids of the main thread.** `collect_result` reads every ToolMessage whose artifact
  is a P1 `ToolResult`, keys it by `tool_call_id`, and rates the report: a step's `trust` = strongest non-refuted
  trust of its referenced results (`not_verified` for none / unknown ids, which are listed); the verdict `trust` and
  `confidence_cap` come from the verdict-level `evidence_ids` (the step ids if there are none). So a report that
  cites an exact CYK run for "the grammar generates L" does not lift the verdict above the bounded `grammar_vs_ir`
  it lists for the verdict (the CFL mock pins `bounded_pass` / 0.85). The model never sets trust.
  Limits: tool calls made **inside subagents** are not in the main state, so a report cannot cite them (the
  subagent's text answer is a `task` result without trust). P3's evidence ledger (middleware/evidence.py) should
  record subagent tool results under run-wide ids.
- **Budget status.** A `BudgetMiddleware` refusal (`stop_reason == "budget_exhausted"`) without a report gives
  `status: budget_exhausted` in `collect_result`; the CLI also checks `ledger.exhausted`.

### Mock scripts (`--mock-script`, tests)

`{"description", "task", "template", "defs": {name: value}, "models": {role_key: [reply, ...]}}`; a reply is
`{"content"?, "tool_calls"?: [{"name", "args", "id"?}], "usage"?, "expect"?: {"last_message_contains": str}}` and
`{"$def": name}` anywhere in args is replaced by a deep copy of `defs[name]`. `ScriptedChatModel` (one per role,
`bind_tools` returns itself) replays them in order; it fails loudly (`ScriptError`) when a reply calls a tool that is
not bound for that role, when the script runs out, or when an `expect` does not match. Usage is estimated from the
message sizes, so `BudgetMiddleware` charges a mock run like a live one (mock ledgers have no spend log). The tools are
real: the trust levels in the results come from the libraries.

- REG: `(ab)*a|b` (eval reg-04 IR): main → ir-builder (describe_ir_schema, validate_ir, ir_to_text, sample_words) →
  regex_to_dfa (verified) + list_theorems → dfa_vs_ir (bounded_pass) → critic (run_dfa, check_membership) →
  submit_report.
- CFL: `{a^n b^m c^m | n, m >= 0}`, grammar S → aS | B, B → bBc | ε: ir-builder → prover (get_theorem 2.0, cyk,
  grammar_vs_ir) → main grammar_vs_ir (bounded_pass) + cyk (verified) → submit_report.
- LL: `ll_system/examples/format3_simple_ll1.json`: ir-builder → first_follow, ll_table (verified), get_theorem 3.1 →
  ll_parse aab / ba → submit_report (template short_verdict).

### CLI

`tfl-deep (TEXT | --file f.txt|f.md) [--save DIR] [--template T] [--mock-script FILE | --live] [--auto-approve]
[--settings JSON]`. Without `--live` a script is required (clear error, exit 2); the task comes from TEXT / `--file`,
else from the script's `task`. `--live` needs `ANTHROPIC_API_KEY` in the environment (never loads `.env`), uses
`BudgetLedger.for_task` (the shared per-day log, refuses a task that does not fit the day) and prints a warning.
Mock runs use their own ledger without the log. PDF / image → "intake arrives in P3" (exit 2). The run uses an
`InMemorySaver`, so a failure half-way still yields the partial state; `--save` writes `result.json` / `result.md`
(minimal; P3's renderer replaces them). Exit 0 submitted, 1 no report (`incomplete` / `error` / `budget_exhausted`),
2 usage. `--auto-approve` only sets `human_review` until HITL lands in P3.

### Findings / open

- Lib finding (cfl_system, not fixed: read-only): the `exponent_pattern` oracle mis-parses
  `{a^n b^m c^m | n >= 0, m >= 0}` (it judges `bc` not in L); `{a^n b^m c^m | n, m >= 0}` works. The CFL mock uses
  the second form. An IR builder writing the first form would get false counterexamples from `grammar_vs_ir`.
- `regex_to_dfa` runs in a spawned process; from a `python -` (stdin) script it reports a timeout because the child
  cannot re-import `<stdin>`. Files / pytest / the CLI are fine.
- The main prompt refers to a `report-templates` skill that P3 writes; `lean.md` / `intake.md` are stubs.
- Prompts contain no lemma statements; theory goes through the skills and `get_theorem`. Per the light-model-drift
  rule the prompts still need the adversarial Opus review before commit.
- Tests: `pytest -q -n 4 tfl_deep_agent/tests`: 906 passed, 1 xfailed (test_agent_fake_model.py alone: 30 tests).

## P2 core — integration: one ledger, concurrency, caching, skills (2026-11-08)

Files: `agent.py`, `middleware/budget.py`, `cli.py`, `tests/test_agent_integration.py` (new, 22 tests, ~13 s),
`tests/test_agent_fake_model.py` (`test_disallowed_model_is_refused` adapted to the settings validation).

### Settings

- `agent.settings_dict` now validates: a mapping goes through `settings.validate_settings` (merged over the
  defaults), a `Settings` object is used as is, `None` is `load_settings()`. A broken budget / model section raises
  `SettingsError` before any ledger exists (fail closed). A role model outside `models.allowed` is therefore a
  `SettingsError`; `ModelNotAllowedError` stays for what settings cannot see (the `general` role's own Haiku default
  with a narrowed `allowed`).

### One ledger for the run (`create_tfl_agent(budget=...)`)

- `budget=` a `BudgetLedger` (its root) or a `BudgetNode`; `None` = the enclosing `use_budget` scope, else a fresh
  in-memory ledger from `settings.budget` (no per-day log: building an agent never writes files); `False` = no
  budget middleware (also inside an outer `use_budget`, via the new `budget.no_budget()`). `budget_options` go to
  every `BudgetMiddleware` (`missing_usage`, `fit_max_tokens`, ...). The scope is attached as `graph.tfl_budget`.
  The build runs inside `use_budget(node)`, so `default_middleware` gives the main agent and every role a
  `BudgetMiddleware` over the same node (plus `ConcurrencyMiddleware`, as before). Explicit `middleware=` /
  `subagent_middleware=` still replace the hook, budget included.
- **Per-subagent allocation (architecture "Иерархия").** New `budget.SubagentBudgetMiddleware`, inserted in the
  main stack right after (inside) its `BudgetMiddleware`: around every `task` tool call it allocates
  `min(per_subagent_max_usd, parent's available - parent's reserve)` as a child node named after `subagent_type`
  (`task/ir-builder`, `task/critic`, `#2` for repeats), with child reserve `min(root reserve, allocation / 4)`, runs
  the tool with the child as the **active scope** (contextvar `budget._ACTIVE_SCOPE`, `enter_scope()`), and closes
  it afterwards (the unspent part returns). `BudgetMiddleware.node` is now a property: the active scope when it lies
  under the middleware's own node, else the node it was built with — so a subagent graph built once charges a fresh
  allocation per invocation, and parallel `task` calls (each tool call runs in its own copied context, sync thread
  pool and async gather alike; tested) charge their own children. The wrap-up variant "return your final answer to
  the caller" and the per-child refusal now apply to subagents. No allocation possible (nothing free, under
  `min_allocation_usd`, wrap-up) → an error `ToolMessage` "Refused: no budget for a new task call ..." with the
  numbers; it counts in `node.refusals` but does not make the scope exhausted. In wrap-up the outer
  `BudgetMiddleware` refuses `task` first (its own message) and nothing is allocated.
- **For P4**: `spawn_subagent` / `run_workflow` allocate their own nodes; build their dynamic agents with
  `make_middleware(..., node=child)` or run them inside `budget.enter_scope(child)`. Add the tool names to
  `SubagentBudgetMiddleware(scoped_tools=...)` only if they do not allocate themselves.

### Result / partial report

- `collect_result(state, ledger=...)`: a refusal anywhere in the ledger makes a run without a report
  `budget_exhausted` (also when the last message is the main agent's own text after a refused subagent);
  `budget_refusals` lists the scopes with refusals. Every run without an accepted report gets `partial_report`
  (code-built: verdict "not established", trust `not_verified`, cap 0.40, the evidence that is `ok` and not refuted
  with its own trust, refuted / failed tool calls, the subagents' answers, rejected report count, todos, last
  message). A subagent refusal that the main agent answers with `submit_report` stays `submitted` (the refusal is
  in `budget_refusals`). P3's renderer should take `partial_report` as the input of the `budget_exhausted`
  template. The CLI passes `budget=ledger` / `ledger=ledger` and writes a "Частичный отчёт" section to `result.md`.

### Prompt caching — nothing to add

- deepagents 0.7.34 already appends `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")` (lazy
  stand-in) to the main stack and to **every** declarative subagent stack, after the user middleware. A no-op for
  scripted models. Pinned on the real `ChatAnthropic` request path (the `wire` fixture, no network): every request
  of main and subagent carries top-level `cache_control: {"type": "ephemeral", "ttl": "5m"}` plus the tag on the
  last system block and the last tool. The budget's wrap-up note is appended at the end of the request (outside the
  cached prefix). The same test pins cache-aware charging: the API's `input_tokens` excludes the cache tokens,
  langchain-anthropic folds them in, the budget takes them out again (2000 plain + 1500 read + 300 write).

### Skills

- Pinned: the main agent, ir-builder, prover / critic get the four skills (`/skills/{reg,cfl,dcfl,ll}/SKILL.md`)
  listed in their system prompt; general-purpose does not; all four SKILL.md files read through `read_file`.

### Findings / open

- **Budget hole: deepagents' summarization middleware** (`create_summarization_middleware`, in every stack, outer
  to our middleware) calls the model directly when the context passes ~85% of the model window; that call does
  not go through `BudgetMiddleware` and is not charged. Rare at these budgets, but real money in a long live run.
  Options: a LangChain callback on the chat models that charges calls not seen by the middleware, or replacing the
  summarization middleware by name with one that routes through the budget. Not decided here.
- `BudgetMiddleware` sits before deepagents' `SkillsMiddleware` in the stack, so its input estimate does not see
  the skills listing in the system prompt (~2-3k tokens); the estimate scale-up after the first call covers it,
  and the actual usage is charged in any case.
- `graph.tfl_budget` is the scope the default hook charges; with explicit `middleware=` lists it may be unused.

## P2 core FIX (2026-11-08)

Fixes for the 19 reviewer findings on the P2 core. All tests run in mock mode with scripted / fake chat
models; no live call was made. New tests: `tests/test_p2_core_fix.py`; updated: `test_agent_fake_model.py`,
`test_agent_integration.py`.

### Verdict gate (findings 1, 9, 19): the basis is derived by code, citations only lower it

- `agent.verdict_gate` follows docs/VERDICT_POLICY.md. The verdict's direction comes from the new
  `submit_report.answer` field (`yes` / `no` / `inconclusive`), else from the verdict text ("X" / "not X";
  a negation inside an unparsed text and no `answer` → inconclusive). It is mapped to the class token
  (reg `regular`/`non_regular`/`partial`, cfl/dcfl `…`/`non_…`/`null`, ll `ll`/`not_ll`/`uncertain`).
- **Basis**: the strongest admissible result of the run on the verdict's side that checks it **against
  the task (IR)** (R4′), not whatever the model cited. Constructive results: `dfa_vs_ir`, `grammar_vs_ir`,
  `dpda_vs_ir`, `input_grammar_cfg` (R2-CFG); they need ≥ `bounded_pass` (R2). Destructive results:
  `nerode_probe` (reg; dcfl clamped to `well_formed`, since a probe cannot close the dead-class premise),
  `pump_check_cfl` in claim mode; they need ≥ `well_formed` (R1). LL: `ll_table`, classified by its
  `grammar_source` and `k`. A grammar the model wrote itself counts only together with a `grammar_vs_ir` check.
  Exact checks about the model's own artifact (`regex_to_dfa`, `cyk`, `run_dfa`, `ll_parse`) support
  steps but never raise the verdict. This is the deliberate change behind the REG mock going from
  `verified` to `bounded_pass` 0.85 (R6), and the LL mock now passes the IR to `ll_table` (`verified`).
- Citations can only lower: a cited refuted same-side result blocks the verdict; an uncited refuted result
  drops the same artifact from the basis; opposite-side evidence is a contradiction (R3: 0.85 when one
  side is `verified`, else inconclusive ≤ 0.50); an unknown cited id lowers trust one level (decision: one
  level, not to `not_verified`, so a typo does not wipe a real basis). `ir_fingerprint` in the report
  excludes results for another IR; without it a basis spanning several IRs is not admissible.
  Every downgrade is listed in `verdict_gate` (R4) and printed by the CLI next to `verdict_token`.
- Prompt `main.md` now states the tokens, the `answer` field and that code chooses the basis.

### Models (finding 2), defaults (finding 14)

- `general` (the general-purpose subagent) has no config default. When the requested model is not
  allowed and the role uses only a default (not set in settings, not requested), it falls back to the
  cheapest allowed model (by `worst_case_usd`). An explicitly set model that is not allowed still
  raises. `settings.KNOWN_ROLES` includes `general`, so it can be configured. DEFAULTS unchanged.

### Summarization (findings 3, 10)

- deepagents' summarization runs outside the user middleware. Each stack that has a `BudgetMiddleware`
  now gets a summarization middleware built with `summarizer=budget.budgeted_summarizer(...)`, which
  chains the summary call through the budget and concurrency middleware (same name, so it replaces
  deepagents' default in place). The summary call is capped at `SUMMARY_MAX_TOKENS = 8192`; a refused call
  returns a fixed "[No summary: …]" text instead of failing the run. Summary calls are recorded with
  `outcome="summary"` and do not count against `RunLimits`. A non-BaseChatModel keeps the default (TypeError).

### Wrap-up, report gate, execute (findings 4, 5, 12)

- A text answer after the ledger reached wrap-up → `budget_exhausted` (not `incomplete`).
- `ReportGateMiddleware` looks at all trailing ToolMessages, so a report accepted next to another tool call
  in the same turn ends the run.
- The tool filter now also refuses an excluded tool at dispatch (`wrap_tool_call`), not only hides it.

### Lean max_tokens (finding 6)

- `fit_max_tokens` defaults on only for the `lean` role (Opus at 128k would reserve $2.57 per call).
  Open for the user: whether to lower `MAX_TOKENS` generally; not changed here.

### Limits (findings 7, 13), concurrency (finding 8)

- `RunLimits` enforces `max_model_calls`, `max_tool_calls`, `max_parallel`, `max_spawn_depth` for the whole
  graph, subagents included (one counter per graph). The main agent keeps a reserve (2 calls when the limit
  is ≥ 10, 1 when ≥ 3) for its report; a wrap-up note is appended near the end. `submit_report` and
  `write_todos` are never refused; spawning tools are refused when `max_spawn_depth = 0`. A run stopped by
  the limit gets `status` with reason "the run's model-call limit …" and `result["limits"]`.
- `max_parallel` is a run-wide `BoundedSemaphore` taken before the process-wide one.
- The parallel-task test now uses a barrier on the `allocate` event to prove the two allocations overlap
  (1.00 left vs the 2.00 cap).

### Refusal / cancellation charging (finding 11)

- A served refusal (exception carrying the AIMessage) is charged as `outcome="refusal"`. A cancellation
  (BaseException that is not an Exception) is charged at the worst case with `usage_missing` — this
  over-counts if the call was cancelled while still waiting for the semaphore (accepted: safe side). Other
  API errors release the reservation.

### Live factory safety

- `create_tfl_agent` requires an explicit `model_factory`. With the live factory, `budget=False` is
  rejected and `budget=None` gets a per-task ledger from settings, so a live run cannot be unbudgeted.

### DCFL skill (findings 15-17), theory sources (finding 18)

- `skills/dcfl/traps.md` T3 and `SKILL.md` §4.4: Shallit needs **every** Nerode class finite;
  separating all pairs is sufficient, not necessary (THEORY §1.2, §1.7 quoted verbatim).
- `SKILL.md` §4.2: dcfl-09 moved from the "op(L) not CFL" pattern to a separate "op(L) CFL but not DCFL,
  op preserves DCFL" pattern (∩REG and left quotient by a word, THEORY §1.7 quoted).
- `main.md`, `prover.md`, `critic.md`: when THEORY.md has no section, statements may come from the verbatim
  quotes in `/skills/<class>/theory.md` (which copy the tz specs and THEORY_REFERENCE.md); nothing else.

## P2 core — GATE (2026-11-08)

- `pytest -q -n 4 tfl_deep_agent/tests`: 988 passed, 1 xfailed (38 s, no API / network / Docker).
- Full suite with `docker` hidden from PATH (the Docker tests skip cleanly): **4563 passed, 161 skipped, 3 xfailed, 0 failed**
  (`-n auto`, 116 s).
- Full suite with Docker visible: 36 failed, 4618 passed, 70 skipped, 3 xfailed. All 36 are Lean-in-Docker tests in
  agent_system / cfl_system / dcfl_system that hit the same flaky daemon as at the P1 gate (`OCI runtime create failed ...
  read init-p: connection reset by peer`). The failing set changes from run to run, a lone re-run passes, and a serial
  re-run of the group still failed 36 times with the same daemon error. None of them are in tfl_deep_agent.
- Acceptance, fake models: the CLI on `examples/mock/{reg_regex_regular,cfl_anbmcm,ll_simple_ll1}.json` exits 0 with a
  submitted report each time (REG `regular`, bounded_pass / 0.85; CFL, bounded_pass / 0.85; LL(1), verified / 0.98).
  The budget path is covered by `test_agent_integration.py::test_main_call_over_budget_is_refused_and_a_partial_report_is_built`,
  `::test_subagent_refusal_without_a_report_is_budget_exhausted` and `::test_cli_budget_exhausted_writes_the_partial_report`,
  plus `test_agent_fake_model.py::test_budget_refusal_ends_the_run_without_report` / `::test_cli_budget_exhausted_status`.
  In those tests the refused call gives `stop_reason: budget_exhausted`, status `budget_exhausted`, and a `partial_report`
  with verdict "not established", `not_verified`, and the evidence established so far. All pass.
- Still open before commit (from the P2 notes): the adversarial Opus review of the prompt / skill theory text
  (light-model-drift rule), and the summarization-middleware budget hole.

## P3 io+hitl — evidence (2026-11-08)

Files: `evidence/ledger.py`, `evidence/policy.py`, `middleware/evidence.py`, `tests/test_evidence.py` (48 tests, ~14 s,
scripted models, no API / network / Docker).

### Ledger (`evidence/ledger.py`)

- `EvidenceLedger(path=None)` / `EvidenceLedger.open(run_dir)` → `run_dir/evidence.jsonl`, one canonical-JSON line per
  record, appended + flushed under a lock. Re-opening an existing file (HITL resume in a new process) loads it and
  continues ids and the chain; a torn last line is skipped. `EvidenceLedger.load(path)` reads one back.
- Record fields: `id, seq, tool, tool_call_id, args, ok, trust, trust_reason, bounds, error, error_kind, data,
  has_tool_result, status, role, scope (budget node path), model, turn_usd, cost_usd, result_digest, args_digest,
  truncated, elapsed_s, ts, v, prev, digest`. `data` > 200k / `args` > 50k JSON chars are stored as a preview;
  `result_digest` (sha256) always covers the full result.
- Hash chain (`prev` / `digest`), `verify()` lists edited / removed / reordered lines. A tamper check for the human
  and renderer, not a security boundary.
- **Ids**: the tool-call id when unique in the ledger, else `<tool_call_id or "call">~<seq>` (scripted models reuse
  ids across subagents; a resumed run may too). The P2 reports that cite main-thread tool-call ids keep working.
- `use_evidence(ledger)` (contextvar, read at build time, like `budget.use_budget`); listeners like `BudgetLedger`
  (event `evidence`, compact view) for the P7 UI.

### Middleware (`middleware/evidence.py`)

- `make_middleware(settings, role=...)` is the `agent.default_middleware` hook: returns an `EvidenceMiddleware` over the
  `use_evidence` ledger, **`None` outside such a block** (nothing recorded; same rule as the budget). So the
  integration (CLI / run / UI, not in my scope) must build the agent inside
  `with use_evidence(EvidenceLedger.open(run_dir)):` — every role stack then records into the same ledger.
- Trust is set by code only: evidence = a **registered TFL tool** (`tools.base.REGISTRY`) whose ToolMessage artifact
  validates as a `ToolResult` of that same tool; its trust is clamped to the tool's ceiling again. Everything else
  (`task` answers, built-ins, validation errors, `status="error"`, raised calls, a non-TFL tool returning a
  trust-shaped artifact) is recorded `not_verified`, `has_tool_result: false`.
- State: the handler result becomes a `Command(update={"messages": [msg], "tfl_evidence": [compact record]})`
  (a Command from the tool, e.g. deepagents' `task`, keeps its update and gains the key). Reducer `merge_evidence`
  merges **by id** — deepagents passes non-excluded state keys into the subagent and back through `task`'s Command,
  so without it subagent records would be duplicated. The compact view has no `args` / `data`.
- The ToolMessage always gets `additional_kwargs["tfl_evidence_id"]`; the content is changed only when the evidence
  id differs from the tool-call id (JSON object: `"evidence_id"` key first; text: a `[evidence_id: …]` line), so
  main-thread content and the budget estimates of existing runs are unchanged.
- `model`: the requesting AIMessage's `response_metadata` model, else the ledger record of that turn, else the role's
  configured model. `turn_usd`: the cost of that model call (last `BudgetLedger` record of this role in the caller's
  scope; shared by parallel calls of the turn). `cost_usd`: 0.0 for local tools; for a spawning tool run inside a
  fresh `SubagentBudgetMiddleware` allocation, the spend of that allocation (`task` → the subagent's cost); `None`
  for a spawning tool without one. **For P4**: `spawn_subagent` / `run_workflow` allocate inside the tool, so their
  `cost_usd` is `None` here; either run them inside an allocation opened outside this middleware or report the
  spend in the ToolResult data.
- Interrupts (`GraphBubbleUp`) and cancellations are not recorded (the call re-runs on resume); other exceptions are
  recorded (`error_kind: "raised"`) and re-raised.
- Not recorded, by position: calls refused by `TFLCallLimit`, by the budget wrap-up, or by `SubagentBudgetMiddleware`
  (they are outside this middleware and are refusals, not results).

### Policy (`evidence/policy.py`)

- `claim_trust(ids, records)`: strongest non-refuted trust among cited records with a tool result (`ok`). **Any id
  not in the ledger makes the claim `not_verified`** (architecture.html), whatever else it cites. A cited `refuted`
  record → `contradiction`, cap 0.50 (the R3 ceiling, as `verdict_gate` does for refuted cited support). Records
  without a tool result are listed in `not_evidence`. Works on the ledger, the jsonl records or the compact state view.
- `rate_claims` drops any model-supplied `trust` / `confidence` on a claim.
- `evaluate_report(report, ledger)`: steps via `claim_trust`; the verdict via the existing `agent.verdict_gate` (R1-R6)
  fed with **all** ledger records that carry a tool result, subagents included (fixes the P2 limit "a report cannot
  cite subagent results"; R4′ basis can now be a subagent's check). Verdict-level unknown ids still lower one level
  (the P2 gate's rule, stated in `main.md`); only per-claim is strict. Needs full records (args / data); the compact
  state view rates steps but gives the verdict no basis.
- `final_confidence(cap, own) = min(cap, own)`; a non-number / NaN / bool model value is ignored, so it can never
  raise the cap. `evaluate_report` takes `model_confidence=` or the report's `confidence` field if one appears.
- `published_verdict`: REG goes through `agent_system/lib/result_verdict.result_verdict` (null for partial / an
  unresolved contradiction; a contradiction R3 resolved for a verified side keeps its verdict); other classes publish
  the gate token. That is the only place `result_verdict` fits (it is the REG result reader, not a cap function);
  caps come from `tools.base.CONFIDENCE_CAPS` = `agent_system.lib.claim_verifier.CONFIDENCE_CAPS`.

### Not done here (outside my files)

- `agent.collect_result` still builds evidence from main-thread ToolMessages; the renderer / CLI should switch to
  `policy.evaluate_report(report, ledger)` and wrap the build in `use_evidence`. In the mocks the ledger-based rating
  is never below P2's (pinned by `test_reg_and_ll_runs_publish_through_the_policy`).
- Tests: `pytest -q -n 4 tfl_deep_agent/tests`: 1089 passed, 1 xfailed.

## P3 io+hitl — intake (2026-11-08)

Files: `intake/files.py`, `prompts/intake.md`, `tests/test_intake.py`. `cli.py`, `agent.py` and the `/input` route are
not touched; wiring them to the API below is the HITL / CLI / UI owners' step.

### API (`tfl_deep_agent.intake.files`)

- `from_text(text)`, `from_path(path)` (CLI `--file`), `from_bytes(data, name=, declared_mime=)`,
  `from_base64(payload, name=, declared_mime=)` (TFL Lab upload; plain base64 or a `data:<mime>;base64,` URL) each
  return a frozen `TaskInput` or raise `IntakeError(ValueError)` with a human-readable message and a stable `.code`
  (`empty`, `too_large`, `too_many_pages`, `unsupported_type`, `type_mismatch`, `corrupt`, `encrypted`, `not_utf8`,
  `bad_base64`, `not_found`, `image_too_large`) for the UI.
- `limits=` takes `IntakeLimits`, a `Settings`, its `.intake`, a mapping, or `None` (= `settings.DEFAULTS["intake"]`).
- `TaskInput`: `kind` (`text` / `pdf` / `image`), `mime_type`, `name`, `data`, `text`, `pages`, `width`/`height`,
  `warnings`; `content_blocks(instruction=None)` -> langchain standard blocks (`{"type": "file", "base64", "mime_type":
  "application/pdf"}`, `{"type": "image", "base64", "mime_type"}`, text block for text), the format `models.py` pins;
  `input_path` (`/input/task.<ext>`), `to_file_data()` (deepagents `FileData`, base64 for binaries),
  `metadata()` (JSON-safe, no content, with sha256), `describe()`, `needs_transcription`.

### Decisions

- **Type by magic bytes**, not extension / browser MIME. A known extension that disagrees with the content (a `.jpg`
  that is a PNG) is a `warning`, and the content's type is used. A `.pdf` / image extension (or declared MIME) on
  content without a recognised signature is `type_mismatch`. GIF / BMP / TIFF / HEIC / AVIF / ZIP-DOCX / DOC / RTF /
  executables are named in the error. Unsigned content is text only if it has no NUL in the first 8 KB and is valid
  UTF-8 (BOM stripped). Text is normalised: CRLF -> LF, **NFC** (NFKC would fold `aⁿ` to `an`), stripped.
- `max_file_mb` uses 1 MB = 1024² bytes and applies to every input, text included. `from_path` checks `stat()` before
  reading; `from_base64` checks the encoded length before decoding.
- **Anthropic API caps added on top of the settings** (settings can only lower them), so the API's 400 becomes a readable
  intake error: images <= 5 MB and <= 8000 px per side, PDFs <= 100 pages, no encrypted PDFs (also owner-password-only
  ones; the message suggests re-printing to a new PDF). Image dimensions come from the PNG / JPEG (SOF scan) / WebP
  (VP8 / VP8L / VP8X) headers by hand, no Pillow dependency; an unreadable header is `corrupt`.
- PDF page count: `pypdf.PdfReader(strict=False)`; any pypdf / parse error -> `corrupt`. pypdf logs "EOF marker not
  found" for a truncated file; harmless.
- `input_path` is a fixed ASCII `/input/task.<ext>` with the extension of the sniffed type, because deepagents'
  `read_file` picks the block MIME type from `mimetypes.guess_type(suffix)`. **Finding:** Python 3.12 on this Windows host
  has no `.webp` mapping, so `read_file` would send a WebP as `application/octet-stream`. `files.py` registers
  `image/webp` with `mimetypes.add_type` at import when it is missing (process-global, benign). Whoever wires `/input`
  must import `intake.files` before the agent reads a WebP (pinned by the deepagents read_file test).
- `prompts/intake.md` (no theory statements, only transcription rules): read `/input/task.*` with `read_file`; keep
  the original wording; Unicode formulas (`aⁿbⁿ`, `|w|ₐ`, `→`, `ε`, `wᴿ`; `^(...)` / `_` when no Unicode glyph);
  grammars / automata copied symbol by symbol; every task of a multi-task file as a numbered list with its page; every
  uncertain reading with both readings and the one used; no silent typo fixes. Fixed answer format for HITL 1 parsing:
  `## Tasks` (numbered list) and `## Uncertain readings` (`- none` when clear).

### Tests

- `tests/test_intake.py`, 53 tests, fixtures generated in the test (pypdf `PdfWriter` for 1..101-page and encrypted PDFs,
  hand-built PNG / JPEG / WebP headers, Pillow JPEG / progressive JPEG / WebP lossy + lossless via `importorskip`).
  Covers limits, sniffing, mismatches, corrupt / encrypted / oversized files, base64 / data URLs, `FileData`, a real
  deepagents `read_file` round-trip through a scripted model (PDF and WebP), and the blocks on the wire through
  `make_chat_model(role="intake")` (`document` / `image` with a `base64` source).
- `pytest -q tfl_deep_agent/tests`: 1041 passed, 1 xfailed. Full `pytest -q -n auto`: the only failures (37) are the
  Docker Lean tests in `agent_system` / `cfl_system` / `dcfl_system` (`docker: ... OCI runtime start failed: runc ...
  exit status 139`), a host Docker problem unrelated to this change.

## P3 io+hitl — reports (2026-11-08)

Files: `skills/report-templates/` (SKILL.md, `full_proof.md`, `exam_solution.md`, `short_verdict.md`,
`lean_appendix.md`, internal `partial.md`), `reports/render.py`, `tools/report.py`, `tests/test_reports.py`
(48 tests, ~5 s, scripted models, no API / network / Docker).

### Templates

- `string.Template` files; every placeholder is in `render.PLACEHOLDERS` (pinned by a test). The structure follows the
  goldens `docs/examples/reg_grammar_SaSb_lean_proved/result.md` and `cfl_ticket50_w1bw2w3_lean_proved/result.md`:
  `> [!theorem] Вердикт:` callout, `**Шаг N. title**` + `**Обоснование:**`, `## Lean 4 formalization (R-Lean)` with
  "Statement (generated from the IR):" / "Proof body:".
- `lean_appendix` = the `full_proof` body + the Lean section. The Lean section is appended to any template when the
  report has a `lean` block or the ledger has a `lean_check` entry; status / axioms / statement / proof body are read
  from those ledger entries (P6 writes them), never from the model's note. No entry: "не подтверждён журналом".
- `partial` (not selectable) renders a run without an accepted report (`budget_exhausted`, `incomplete`, `error`):
  verdict "Не установлен", trust `not_verified`, what the ledger established, refuted / failed calls, subagent answers
  (escaped), todos, last message, number of rejected report attempts.

### Renderer (`reports/render.py`)

- `render_report(result, *, evidence=, budget=, task_text=, human_review=, template=, status=, title=)` ->
  `RenderedReport(markdown, html, data)`; `write_report(run_dir, result, **kw)` writes `result.json` / `result.md` /
  `result.html` and reads `run_dir/evidence.jsonl` when no evidence is passed. `result.json` = the result plus
  `"rendered"` (per-claim / per-step trust, where-used, integrity, budget); `data` / `args` of records are not copied.
- The "Свидетельства, модели и расходы" block is appended by code after template substitution, so no template can
  drop it: evidence table (who called, trust, where used, note, model / cost when present), hash-chain integrity
  (`EvidenceLedger.verify()`), model-only / unknown / refuted citations, cost by model and by role, spent / limit,
  budget refusals, run status, review mark.
- Per-claim trust is computed by code from the ledger: strongest non-refuted cited record with a tool result; any id
  not in the ledger -> `not_verified` and the id is listed; no ids -> "держится только на словах модели"; a refuted
  citation supports nothing. Verdict trust is `result["trust"]` (the P2 `verdict_gate`) and is **never raised**: the cap
  is `min(result cap, policy cap of that trust)`. A result without trust runs `agent.verdict_gate` itself; failing that,
  `not_verified` / 0.40. Same rule as `evidence/policy.claim_trust` (strict per claim), implemented locally so the
  renderer also works on a plain list / jsonl / compact state view; the two can be merged later.
- Inputs are duck-typed: evidence = path / dir / list / mapping / `EvidenceLedger` (records via `to_dict` /
  `model_dump` / `asdict`), deduplicated by id; budget = `BudgetLedger`, a node (`.ledger`) or a `summary()` dict.
- Anti-spoof: model text lines starting with `#`, `>` or three backticks are escaped (`\#`, ...), so a report cannot
  open a section, a callout (`[!success]`) or a fence; a fence inside a Lean proof body gets a zero-width space; all
  HTML is escaped. HTML: the evidence block is in `<section class="generated" data-generated-by="code">`; the
  `[!success]` callout only for `verified`.
- Markdown -> HTML is a small local converter (headings, paragraphs, lists, tables, fences, quotes, callouts, bold,
  code). `agent_system/lib/renderer.py` renders its own result schema, so only the trust labels and the KaTeX snippet
  (from the cfl renderer) are reused. KaTeX is loaded only when the body has math and renders only inside `<main>`,
  so the `$` of the cost table is never math.
- Human review: `normalize_review` maps strings / dicts (also langchain-style `approve` / `edit` / `reject`) to
  `approved`, `edited` (edited fields listed), `auto_approved`, `rejected`, `awaiting`, `not_reviewed`; everything but
  approved / edited is marked "Без проверки человеком". `status="awaiting_human"` marks a HITL 2 draft.

### Tools (`tools/report.py`)

- `SubmitReportArgs(ToolArgs)` (extra="forbid", no reserved names): verdict, answer, language_class, method,
  hypothesis, lemmas [{theorem_id, use}], claims [{text, evidence_ids}], steps (1..60) [{title, claim, justification,
  theorem_ids, evidence_ids}], construction, conclusion, evidence_ids, template, summary, ir_fingerprint,
  lean {evidence_ids, proof_path, note}. A superset of the P2 schema: every P2 mock `submit_report` validates (test).
- **Deviation**: the task's "class" field is `language_class` (`class` is a Python keyword, and `agent.verdict_gate`
  already reads `language_class`).
- Theorem ids are checked against docs/THEORY.md (`load_theory().resolve`); the check is skipped if the file is
  unavailable. Evidence ids are **not** rejected: an unknown id is accepted and costs trust in the renderer and the
  gate (a fabricated id must lower trust, not just cause a retry).
- `submit_report` is not a registry tool (the report gate `_is_submitted_report` needs `artifact.report`, and
  `agent.main_tools()` filters out any registry `submit_report`). `make_submit_report_tool()` is a drop-in for
  `agent.make_submit_report_tool()` with the same artifact contract (pinned by a test against `_is_submitted_report`).
- `list_report_templates(name=None)` is a registry tool (group `report`, `not_verified`): names, titles, when to use,
  fields; with `name`, also the template text and its placeholders.

### Not done here (outside my files)

- `agent.py` (integrator): switch `main_tools()` to `tools.report.make_submit_report_tool` (P3 schema), fix its
  docstring, and have the CLI / run call `write_report(run_dir, result, evidence=<EvidenceLedger>, budget=<BudgetLedger>,
  task_text=..., human_review=...)` instead of its own writer. Until then P2 runs still use the P2 schema (which the
  renderer reads fine).
- The renderer's per-claim rule duplicates `evidence/policy.claim_trust`; when `collect_result` switches to
  `policy.evaluate_report`, the renderer can call it instead.
- Dev caveat: editing these files with a bash heredoc that runs Python mangles backslashes; use the Edit tool.

## P3 io+hitl — HITL + integration (2026-11-08)

Files: `hitl.py` (new), `agent.py`, `cli.py` (rewritten), `tests/test_hitl.py`, `tests/test_e2e_mock.py`,
`tests/hitl_scripts.py` (new: shared mock-script variants for the two test files); minimal edits to
`tests/test_agent_fake_model.py` / `tests/test_agent_integration.py` (see below).

What is wired into `create_tfl_agent` now:
- `evidence=EvidenceLedger` → the build runs inside `use_evidence(ledger)`, so `default_middleware` gives
  `EvidenceMiddleware` to the main agent and every role; `graph.tfl_evidence` is the ledger.
- `submit_report` / its schema are `tools.report.make_submit_report_tool` / `report_args_models` (the P2 copy is
  gone); `/input/` is a read-only route next to `/skills/`; `run_task(files=)` seeds the state's files.
- `hitl=` (`None`/`False` off — the library default, so P2 callers are unchanged; `True` = `settings.hitl`; a
  mapping of points). Any point on without a checkpointer is a `ValueError`. `graph.tfl_hitl` = the points.
  `main_system_prompt(hitl=)` adds one line per point under "## This run".
- `collect_result(evidence=ledger)` rates the report with `evidence.policy.evaluate_report` (subagent tool results
  count; any cited id not in the ledger makes its step `not_verified`, a verdict-level unknown id lowers the verdict
  one level); `result["evidence"]` is the ledger's tool results. Without `evidence=` the P2 rule stays.
  `pending_report=` rates a not-yet-accepted report (the HITL 2 draft, `status: awaiting_human`).
- `ScriptedModels(state_path=)` persists the per-role reply cursors (`mock_cursors.json`) so a mock run resumed in
  a new process continues the script; `ScriptedChatModel._on_advance` is the hook.

Decisions:
- HITL 1 is a custom `FormalizationReviewMiddleware` (`before_model` of the main agent + `langgraph.types.interrupt`),
  not `interrupt_on`: the stop must come *after* the ir-builder answer, and `interrupt_on` only stops *before* a tool
  runs. On resume only the hook re-runs (no model / subagent call is repeated). It fires once per ir-builder answer
  (`tfl_hitl` state key, reducer merges by `(point, id)`); an answer whose IR fingerprint was already approved
  passes (`decision: unchanged`). The request is built by code in the `HITLRequest` shape (action
  `formalization`): task text, source metadata, the intake answer (transcription), the IR (the answer's JSON block,
  else the ir-builder's last accepted `validate_ir` in the evidence ledger — the mock ir-builder answers in prose),
  and `validate_ir` + `ir_to_text` run by code on that IR (restatement + oracle-labelled words; `ir_to_text`
  already samples words, so `sample_words` is not called). These calls are not evidence. The decision reaches the
  model as a `HumanMessage` starting "HITL 1" (after the tool messages; langchain-anthropic merges consecutive user
  turns). Edit: `transcription` / `ir` / `class`, the IR validated before resuming (`DecisionError` keeps the run
  waiting).
- HITL 2 is deepagents `interrupt_on["submit_report"]` with a `when` predicate (only a report that passes
  `validate_report` is shown; an invalid one returns its validation error to the model) and a callable description
  (code-rated summary via `policy.evaluate_report`, unknown ids flagged). Reject → the tool's error result "User
  rejected the tool call for `submit_report` with reason: HITL 2: the human returned the report ... «comment»"; the
  agent continues and resubmits. Edit → merged over the original args, validated, `edited_fields` recorded and
  rendered ("утверждён с правками; изменённые поля: ...").
- `execute` (off by default) is `interrupt_on["execute"]`, inherited by the declarative subagents (only the coder
  has it). Not exercised by a test (needs a sandbox backend).
- Runner (`hitl.start_run` / `resume_run`): one SQLite checkpointer per run dir (`thread_id` = run id); at an
  unanswered stop `run.json` gets `status: awaiting_human` + the pending request, `hitl.md` the card, and at HITL 2
  `result.*` the draft (rendered with `status="awaiting_human"`), then the process returns (CLI exit 3). Resume
  rebuilds everything fresh: settings snapshot from run.json, budget (mock: `already_spent_usd` + restored
  `CallRecord`s, no day log; live: `BudgetLedger.for_task(resume=True)`), `RunLimits` counters, the evidence ledger
  (`EvidenceLedger.open` continues `evidence.jsonl`), the mock cursors. A live run needs `--live` again to resume;
  a mock run refuses `--live`. The mock script is copied into the run dir (`mock_script.json`), the task file into
  `input/`.
- `--auto-approve` answers every stop with approve in the same process (review `source: auto`, decision
  `auto_approved`; the report says "Без проверки человеком ... `--auto-approve`"). A `dialog` (CLI: `TTYDialog`,
  only when stdin and stdout are TTYs and neither `--no-dialog` nor `--auto-approve`) asks in-process; "later"
  leaves the run awaiting. A dialog decision that does not fit (bad edit) is reported and asked again.
- `result["human_review"]` stays a string ("approved by a human at HITL 2", "approved with edits ...",
  `AUTO_APPROVED_LABEL`, "not reviewed ..."); `result["hitl"]` has the points, every review, the formalization
  records and `report_rejections`. When the edited transcription exists it becomes `result["task"]`.
- CLI: `--resume RUN_DIR --decision approve|edit|reject [--payload JSON|@file] [--comment TEXT]`, `--no-dialog`;
  `--save` is the run dir (default `default_runs_dir()/deep-<timestamp>-<hex>`); PDF / image files go through
  `intake.files.from_path` and are passed to the agent as state files at `/input/task.<ext>` with a first message
  that tells main to delegate to `intake`. Exit codes 0 / 1 / 2 / 3 (3 = awaiting human).
- `hitl.run_state(run_dir)` reads the latest state through a fresh agent with empty replays (the checkpoint blob in
  langgraph 1.2 does not carry `messages` in `channel_values`, so `SqliteSaver.get_tuple` alone is not enough).

Existing tests changed (HITL is on by default in `settings.example.json`, and pytest has no TTY, so a run without
`--auto-approve` now stops with exit 3): `--auto-approve` added to five CLI tests (the "not reviewed" assertion of
`test_cli_task_text_overrides_script_task` became "auto-approved"); the `task.pdf` needle "P3" became
`type_mismatch` (a PDF holding `b"x"` is an intake error now); the partial-report needle "Частичный отчёт" became
"отчёт частичный" (the P3 renderer's wording).

Known limitations / open:
- HITL 1 fires only when the agent calls `ir-builder`; a run that never does is recorded as
  `hitl.formalization = "not held (no ir-builder answer)"`, and the report still goes through HITL 2.
- With several tasks in one file the human picks at HITL 1 via an edited transcription; no separate chooser.
- `execute` HITL is wired but untested; HITL inside a subagent (interrupt in a `task` tool call) resumes by
  re-running that subagent's pending tool call — not checked against deepagents 0.7.34 yet.
- The TFL Lab UI tab and the eval adapter should call `hitl.start_run` / `resume_run` (eval: `auto_approve=True`).

## P3 io+hitl FIX (2026-11-08)

Review findings (9) checked against the code; all real, all fixed. Files: `hitl.py`, `agent.py`,
`evidence/ledger.py`, `evidence/policy.py`, `middleware/evidence.py`, `reports/render.py`, tests
(`test_hitl.py`, `test_evidence.py`, `hitl_scripts.py`).

### HITL 1 binds the verdict by code (findings 1, 6, 2, 9)
- `hitl.formalization_binding(tfl_hitl records, on=, auto=)` -> `None` (HITL 1 off) or
  `{"status": approved | rejected | no_ir | not_held, "decision", "ir_fingerprint", "review_id", "auto"}` from
  the last non-superseded formalization record (`approved` / `edited` / `unchanged` = approved).
  `hitl.run_binding(run, state)` is what `final_result` and the HITL 2 draft pass to
  `collect_result(formalization=)` -> `evaluate_report(formalization=)` -> `verdict_gate(formalization=)`.
- `verdict_gate`: with an approved binding, the approved fingerprint is the required IR whatever
  `report.ir_fingerprint` says (results for another IR go to `excluded_ir_mismatch`; a differing report
  fingerprint is a gate line). `rejected` / `not_held` / `no_ir` -> `not_verified` with a gate line, no basis.
  `binding=None` keeps the P2 rule (the report's own fingerprint). New result fields: `formalization`,
  `required_ir_fingerprint`.
- HITL 2 card (`report_review_text(..., formalization_on=)`, `formalization_note`) always states the HITL 1
  outcome when it is on, including "NOT REVIEWED" and "DIFFERENT IR" / excluded ids. The renderer's IR line
  (`_ir_line`) and the generated evidence block print `formalization_label` (not reviewed / rejected /
  auto-approved / approved, IR fp, excluded ids).
- Bypass (findings 2, 9): `FormalizationReviewMiddleware` also stops after an accepted `validate_ir` call of
  the **main** agent (request `trigger: "validate_ir"`, IR = the call's argument; `reviewed_call_id`;
  `ir_builder_call_id` is `None` then). An already approved fingerprint passes as `unchanged`. A run that
  never reaches HITL 1 (oracles on an IR nobody validated) is `not_held` -> `not_verified`. Rejecting
  `submit_report` after a HITL 1 rejection was not added: the report goes through and is `not_verified` with
  the reason, which keeps the flow simple and the human informed. The main-prompt HITL line says so.

### HITL 1 IR lookup per call (finding 4)
- `EvidenceRecord.parent_call` (new field, default `None`): `EvidenceMiddleware` sets a context variable
  (`_PARENT_CALL`) to the spawning call's `tool_call_id` while a `task` call runs; the subagent's middleware
  records it. Tested for sequential calls (cfl / ll mock runs); parallel `task` calls should keep their own
  value (context variables follow langchain's `copy_context` executor threads and awaited coroutines), but no
  test runs two ir-builders in parallel yet. `find_ir(call_id=)` / `_ledger_validations(parent_call=)` take only the
  reviewed call's validations; a ledger without `parent_call` (older run dirs) uses the call's seq window
  (after the previous ir-builder `task` record, before its own). None there -> "no IR" (never an older IR).

### Smaller fixes
- Finding 3: `evaluate_report` cites `claims[].evidence_ids` and `lean.evidence_ids` too (unknown ids lower the
  verdict, reach `unknown_ids`, the card); returns `claims` rated with `rate_claims`; `collect_result` writes
  `result["claims"]`. The renderer's `_verdict_trust` fallback cites the same set.
- Finding 5: `EvidenceLedger._load` remembers a missing trailing newline; the next `_write` starts a new line,
  so a record is never glued to a torn fragment (verify reports only the fragment line).
- Finding 7: `start_run(model_factory=<tfl_live factory>)` is `mode: "live"` (`run["factory"] = "custom"`):
  per-day ledger (`BudgetLedger.for_task`), shared spend log, `--live` needed on resume, and the factory too.
  `_budget_ledger` also checks `is_live_factory(factory)`; a live factory cannot resume a mock / custom run.
- Finding 8: `render._judge` makes a claim with an id not in the ledger `not_verified` (the `claim_trust`
  rule); `rendered.steps[*].trust` now equals `result.steps[*].trust`.

Tests: 18 new (test_hitl: edited IR binds the verdict, report fp cannot override, report after a HITL 1
rejection, a run that skips HITL 1, main validate_ir triggers HITL 1, per-call IR lookup, live factory,
full_proof render of a fabricated id; test_evidence: torn tail append, claims / lean ids, gate binding
cases, parent_call). `tfl_deep_agent/tests`: 1188 passed, 1 xfailed.

## P3 io+hitl — GATE (2026-11-08)

- `pytest -q tfl_deep_agent/tests`: 1188 passed, 1 xfailed (about 130 s serial).
- Full suite (`pytest -q -n auto`): 4816 passed, 75 skipped, 3 xfailed, 33 failed. All 33 failures are real-Docker
  Lean tests in `agent_system` / `cfl_system` / `dcfl_system` (`test_lean_ir.py`, `test_lean_examples.py`,
  `test_lean_formalize.py`, `test_phase3.py`, `test_constraint_semantics.py`). They fail inside the Docker daemon
  itself (`OCI runtime start failed: runc did not terminate successfully: exit status 139`) and keep failing when rerun
  one at a time, so they come from the environment and not from this phase. P3 touched none of those packages.
- Acceptance: reject -> fix -> approve: `test_e2e_mock.py::test_reject_fix_approve_across_four_processes`,
  `test_hitl.py::test_hitl1_reject_then_fix_then_approve`, `test_hitl.py::test_hitl2_reject_then_fix_then_approve_on_fresh_agents`.
  Fabricated evidence id lowers trust: `test_evidence.py::test_a_fabricated_evidence_id_lowers_trust`,
  `test_hitl.py::test_fabricated_evidence_id_lowers_trust`, `test_reports.py::test_fabricated_evidence_id_lowers_trust_end_to_end`.
  All pass, none skipped.
