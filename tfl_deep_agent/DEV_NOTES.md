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
