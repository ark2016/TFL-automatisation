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
  The public route is `verify_agent_results({"stack_strategy": ...}, ir)`. (Since 2026-11-10 its first three steps,
  normalization → determinism → structure, are the public `dcfl_system.lib.dpda.prepare_dpda`, which the
  pipeline's closure_reduction artifact check also uses for a DPDA source; this package's `_prepare_dpda` in
  `tools/dcfl.py` still does the same steps itself, because it reports every conflict and structure error.)
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

## P6 lean — lean_statement / lean_check (2026-11-08)

Files: `tools/lean.py`, `prompts/lean.md`, `tests/test_tools_lean.py` (38 tests, ~20 s with Docker, ~2 s without).
Nothing in the four pipelines was changed; `agent.py` was not touched.

### What is reused (not reimplemented)

- Statement: the same `render_statement_verbose` the pipelines' `formalize.py` call, chosen by direction:
  `agent_system.lib.lean_ir` (regular / non_regular), `cfl_system.lib.lean_ir` (cfl / non_cfl),
  `dcfl_system.lib.lean_ir` (dcfl / non_dcfl). `ll` goes to `agent_system.lib.lean_ir`, which refuses it with
  the R-Lean reason ("LL(k) — не формализуется"); the tool returns `unsupported`.
- File: `type_check.compose_lean_file` (kernel replay + `#print axioms tfl_main` appended); check:
  `type_check.check_lean_file(text, timeout, theorem_name=stmt.name)`; trust mapping:
  `claim_verifier.compute_lean_proof_trust`; `type_check.ALLOWED_AXIOMS`; the comment stripper
  `type_check._strip_lean_comments` (private, imported with a fallback).

### API decisions

- `lean_statement(ir, direction)` -> `statement_id` (`lean-` + 16 hex of sha256 over the direction and the
  statement fields), `statement` (imports + declarations + bare `theorem tfl_main : ...`, the report format),
  `template` (`LeanStatement.render("<PROOF>")`), the raw fields, `proof_body_rules`, `ir_fingerprint`, `cls`,
  `side` (constructive / destructive), `claim`. The statement is kept in a process-local LRU store (128).
- `lean_check(statement_id, proof_body, ir=None, direction=None)`. The model never sends a statement. The id is
  deterministic, so after a resume in a new process the model passes `ir` + `direction`; the statement is
  re-rendered and must give the same id (otherwise `invalid_input`, "cannot be changed"). An `ir` that renders a
  different statement than the stored id is refused too. This is the task's `lean_check(proof_body,
  statement_id)`; TOOL_INVENTORY §8 had `(ir, direction, proof_body)`. Both are covered.
- **Deviation from TOOL_INVENTORY §8:** `lean_statement` is `not_verified` with `data.evidence_role =
  "reference"`, not `well_formed`: a rendered statement checks no claim, and a `well_formed` record could prop a
  claim up to 0.55 (same reasoning as `sample_words`, P1 fix F8).
- Trust of `lean_check`: `verified` only for status `proved` AND axioms ⊆ ALLOWED_AXIOMS (re-checked here
  although `check_lean_file` already enforces it) AND the composed file declares `tfl_main` exactly once.
  `has_sorry` / `error` / `timeout` -> ok=True, `not_verified` (R1). Guard rejection -> failure `invalid_input`,
  `data.status = "rejected"`, `data.violations`. Unavailable checker -> failure `unsupported`,
  `data.status = "unavailable"`. `allow_refuted=False`.
- `data` of `lean_check` carries what the P3 renderer reads from the ledger (`status`, `axioms`, `direction`,
  `statement`, `proof_body`) plus `cls`, `side`, `claim`, `ir_fingerprint`, `statement_id`, trimmed `errors`
  (≤ 10, each ≤ 2000 chars), `attempt` / `attempts_left`, `lean_file_sha256`, `daemon_retries`.
  `evidence_role = "claim_check"`. A test renders a report from such an entry.

### Guard (`guard_proof_body`)

`scan_proof_body` (metaprogramming, `native_decide`, `#`-commands, a line at column 0) plus, on the
comment-stripped body: `sorry`, `sorryAx`, `admit`, `ofReduceBool`, `axiom`, a declaration keyword at the start
of any line (`import|theorem|lemma|def|abbrev|instance|inductive|structure|class|example|opaque|namespace|
section|mutual|universe|variable|attribute|noncomputable|private|protected`, optionally after `@[...]`),
`tfl_main`, a leading `by`, tab characters, an empty body, > 40000 chars. A rejected body never reaches Docker
and does not count as an attempt. CRLF is normalized to LF before composing.

### Limits

- Lean timeout `config.LEAN_TIMEOUT` (180 s, = cfl_system); tool timeout 600 s (the registry maximum), thread mode
  (the `docker run` has its own timeout).
- At most 2 checks run at once (`MAX_PARALLEL_CHECKS`; each container gets 4 GB / 2 CPUs); a waiting call gives up
  with `busy` when it could not finish in time.
- At most 6 checks per statement per process (`MAX_CHECKS_PER_STATEMENT`), then `busy` "attempt limit".
- Docker daemon flake (P0 note: `OCI runtime create failed ... read init-p`): `check_lean_file` reports it as
  `error` with one position-less error. `_is_daemon_failure` recognizes it; the check is retried twice (2 s
  apart) while time allows, and a persistent failure becomes `unavailable` (not an attempt), never a Lean error.

### Acceptance

`docs/examples/reg_grammar_SaSb_lean_proved/proof.lean` split at `theorem tfl_main : ¬ L.IsRegular := by\n  `
and before the replay block: the stored statement + the body re-compose to the golden file **byte for byte**,
and the real `lean_check` in `tfl-lean4` (image present, `sha256:f828d004…`, no build needed) returned
`proved`, axioms `propext, Classical.choice, Quot.sound`, trust `verified` in ~14 s. With `docker` hidden from
PATH the test skips with the reason; a runtime `unavailable` also skips.

### Open (outside my files)

- `agent._classify` / `verdict_gate` do not know `lean_check` yet, so a `verified` Lean result is not a verdict
  basis. Suggested branch: `if tool == "lean_check" and data.get("cls") == lang and data.get("status") ==
  "proved": return [(data["side"], trust)]` (the IR filter already reads `data.ir_fingerprint`).
- `roles.py` still describes the lean role with "(the Lean tools arrive in phase P6)".
- `skills/lean/` is empty; `prompts/lean.md` reads `/skills/lean/SKILL.md` only if it exists and carries a short
  Lean API list copied verbatim from `agent_system/prompts/formalizer.md` (checked against the image there).
- TOOL_INVENTORY §8 should be updated to the trust / argument decisions above.

## P4 orchestration — spawn_subagent / run_workflow / budget_status (2026-11-08)

Files: `orchestration/model_policy.py`, `orchestration/spawn.py`, `orchestration/workflow.py`,
`tests/test_orchestration.py` (54 tests, scripted models only). `agent.py` is untouched; wiring is the
integration step (below).

**What was built**
- `model_policy.resolve_model_choice(settings, pick, reason, what=...)`: tier name or full id; refused if
  unknown, not in `settings.models.allowed` (compared by `pricing_key`, so dated ids match aliases), or
  opus without a `reason` of >= 8 chars while `opus_needs_reason` is on. `TFL_MODEL_OVERRIDE` is applied
  *after* the policy check (a plan refused in a real run is refused under the override too); the result
  says `overridden`. An unpriced override is a `ModelPolicyError`, not an internal error.
  `model_cost_table` gives per-tier worst case at full `max_tokens` and at the 4096-token floor.
- `spawn.OrchestrationContext`: settings, model factory, budget node (default: the active `_CURRENT`
  scope), evidence ledger (default: current), `RunLimits`, depth, listeners, event history. A live
  factory without a budget is refused at `create`.
- `spawn_subagent`: all problems collected into one model-readable refusal (name pattern, depth vs
  `limits.max_spawn_depth`, model policy, tool whitelist, output schema, wrap-up, `per_subagent_max_usd`,
  free remainder), then a floor check (one call at the 4096 floor + the child's reserve), then
  `scope.allocate("spawn:<name>", kind="subagent")`. The child is a `create_agent` with middleware
  call-limit -> `BudgetMiddleware(fit_max_tokens=True, min_output_tokens=4096)` -> concurrency ->
  `EvidenceMiddleware(budget_node=child)`. The node is closed in `finally`; unspent money returns.
  Result data: status (ok / budget_exhausted / call_limit / error), answer, structured (+ errors),
  evidence ids of the child's oracle calls, budget (allocated/spent/returned/calls), model choice, depth.
  Trust is `not_verified` — a subagent's answer is never evidence; its oracle calls are, by id.
- Child tools: whitelist of groups ir, words, reg, cfl, dcfl, ll, theory, lean (`group:<g>` accepted)
  plus `budget_status`. Orchestration tools are not given by default; `spawn_subagent`/`run_workflow`
  may be granted only when the child itself could still spawn (`depth + 2 <= max_spawn_depth`).
  Never given: `submit_report`, file / shell tools, `task`.
- `output_schema`: must be an object schema (checked with jsonschema); passed as
  `ToolStrategy(final_answer)`; the returned value is validated again and errors reported.
- `run_workflow(steps, budget_usd, name, dry_run)`: validation collects everything at once — unique ids,
  id pattern, unknown / self dependencies, cycle (printed as `a -> b -> a`), parallel width (longest-path
  layers) <= `limits.max_parallel`, role, model (explicit or role default; role defaults need no opus
  reason), tools (no spawning inside steps), schema, money (explicit step budgets <= cap, their sum <=
  budget, an equal share for the rest, per-step floor incl. room for dependency answers, min total <=
  budget, budget <= free). Compiled to a `StateGraph` (`dispatch` node + conditional edges returning
  `Send("step", ...)` for every ready step, capped at `max_parallel`; `max_concurrency` too). Each step
  runs in a `branch` allocation under `workflow:<name>`, gets its dependencies' answers / structured
  output / evidence ids appended to its task, and is skipped when a dependency failed unless
  `on_dependency_failure: "run"`. `dry_run` validates and returns the plan without spending.
  Estimated cost = sum of step budgets (upper bound); returned with the actual spend per step.
- `budget_status(detail)`: scope status, free money for a new child, cap, wrap-up, depth / can_spawn,
  max_parallel, `RunLimits.status()`, cost table, opus_needs_reason.
- Tools are `StructuredTool`s (`content_and_artifact`, `metadata.tfl_group="orchestration"`), invalid
  args get a readable "refused: invalid arguments" message, async goes through `asyncio.to_thread`,
  `GraphBubbleUp` (interrupts) is re-raised.

**Decisions / deviations**
- Ad-hoc children use `fit_max_tokens=True` with a 4096 floor: at the full 64k max_tokens one Opus call
  reserves ~$1.28, which makes small child budgets unusable.
- Child evidence is reported by id in the tool result, not merged into the parent's state through a
  `Command`; the records themselves are in the shared `EvidenceLedger` with the child's scope and
  `parent_call` = the spawning tool call id.
- Workflow steps set only `_PARENT_CALL` explicitly; they do not copy the tool call's context (copying
  made parallel child graphs inherit the parent's runnable config / checkpoint namespace).
- Workflow steps using a role get the role's prompt and tools, but no `/skills/` file access (no file
  tools in children); the step footer points them at `get_theorem`.
- Line endings of the new files are CRLF (Windows host); ruff is clean on all four files.

**Integration notes (for the step that edits agent.py)**
- Build `ctx = OrchestrationContext.create(settings, model_factory=factory, budget=ledger)`, pass
  `extra_tools=make_orchestration_tools(ctx)` to `create_tfl_agent`, then `ctx.limits = graph.tfl_limits`
  so children count against the run's call limits (tested in
  `test_tools_work_inside_create_tfl_agent_with_the_run_limits`).
- Children are built with role keys `spawn:<name>` and `workflow:<step_id>`; `ScriptedModels` /
  `load_mock_script` currently reject unknown role keys, so mock scripts need a prefix rule for them.
- The evidence record of a spawning tool call has `cost_usd=None`; the spend is in the ToolResult data
  (`budget.spent_usd`) and in the ledger's child nodes.
- `ctx.listeners` / ledger `_emit({"event": "orchestration", ...})` carry spawn / workflow / step
  start-end events for the TFL Lab live view.

## P4-P6: P5 docker sandbox (2026-11-08)

Files: `sandbox/docker_backend.py`, `sandbox/Dockerfile.sandbox`, `sandbox/build.ps1`, `sandbox/build.sh`,
`tests/test_sandbox.py` (46 tests: 37 without Docker, 9 real-Docker ones that skip with a reason when the CLI, the
daemon or the image is missing, or when the flaky daemon will not start a container). `agent.py` is untouched.

### What it is

- `DockerSandbox(BaseSandbox)` (deepagents 0.7.34): `execute`, `upload_files`, `download_files`, `id`. ls / read /
  write / edit / grep / glob come from `BaseSandbox`, which runs `python3 -c ...` in the container and writes temp
  files to `/tmp`. Checked against the real image: all of them work.
- One container per run: `docker run -d --rm --name tfl-sbx-<run>-<hex8> --label tfl.sandbox=1 --network none
  --read-only --tmpfs /workspace --tmpfs /tmp (+ /large_tool_results, /conversation_history) --memory 1g
  --memory-swap 1g --cpus 1 --pids-limit 256 --user 1000:1000 --security-opt no-new-privileges --cap-drop ALL
  -v ...:ro tfl-sandbox:latest sleep infinity`. The tmpfs mounts are `noexec,nosuid,nodev`, so run scripts as
  `python x.py` / `sh x.sh`, not `./x`. `docker cp` cannot see tmpfs, so upload and download go through
  `docker exec -i ... sh -c` with the path as a positional argument (no quoting of model input into the script).
- `execute`: `docker exec -w /workspace <name> timeout -k 5 <T> sh -c <cmd>`. coreutils `timeout` kills the process
  group inside the container, not only the host-side client. The host waits T + 20 s; if that expires it kills the
  client and runs a python one-liner that SIGKILLs every process except PID 1. A timeout gives exit code 124 and a
  `[timeout]` note. Output: stdout, then stderr lines as `[stderr] ...` (the `LocalShellBackend` convention), at most
  `max_output_bytes` (64 KiB). Each stream is read in a thread and only the first limit+1 bytes are kept, so
  `yes` for 60 s does not fill memory. Over the limit: `truncated=True` and `... [output truncated: N of M bytes
  shown]`. The exit code is not appended, because deepagents' execute tool already adds
  `[Command succeeded/failed with exit code N]`. `timeout <= 0`, a non-int or a value above `max_timeout` (600 s)
  raises `ValueError`, which the execute tool turns into "Invalid parameter".
- Cleanup: context manager, `close()` (idempotent), and `weakref.finalize`, which fires on garbage collection and at
  interpreter exit. The finalizer is registered before `docker run`, so a crash in `start()` still removes the
  container. A real test checks that a child process that never calls `close()` leaves no container behind.
  `remove_stale_containers()` removes all `tfl.sandbox=1` containers after a hard kill (explicit call only: it
  also hits live runs).
- `sandbox_status(image)` returns `(ok, reason)`. `open_sandbox(run_id)` returns `(sandbox | None, reason)`, for
  the integration ("Docker unavailable -> no `execute`, the report says so").
- Flaky daemon (DEV_NOTES P0 "Docker"): about every second `docker run`/`docker exec` fails with `OCI runtime ...
  read init-p: connection reset by peer` / `write init-p: broken pipe`. `start()` retries these up to 6 times with
  backoff. A `docker exec` that failed before the command started (exit 126/127 plus `OCI runtime exec failed`, the
  error text arrives on stdout) is retried up to 5 times. This cannot run the command twice. "executable file not
  found" and the command's own 126/127 are not retried. Without the exec retry, about half of the real calls
  failed during this phase.

### Deviations

- **The whole repo is not mounted.** By default only `agent_system`, `cfl_system`, `dcfl_system`, `ll_system` and
  `docs` are mounted, each at `/opt/tfl/<name>:ro`. The root has `.env` with the API key, `.tfl_lab_runs/`, `.venv/`
  and `tmp_*`. `agent_system/.env` is inside a mounted package, so every secret-looking file in a mount (`.env`,
  `.env.*` except `.example/.sample/.template/.dist`, `*.pem`, `*.key`, `id_rsa|id_ed25519|id_ecdsa`) is covered
  by an empty read-only file bind-mounted on top. A real test checks that a fake key is not readable and the masking
  file is empty. `SandboxConfig(mounts=(".",))` mounts the whole repo at `/opt/tfl`, with secrets still masked.
  `PYTHONPATH=/opt/tfl` is set, so `import cfl_system.lib.cyk` works with or without the coder prompt's
  `sys.path.insert(0, "/opt/tfl")`. The coder prompt's description is still correct.
- The image has no build context: `docker build -t tfl-sandbox:latest - < Dockerfile.sandbox`, so no repository
  file can be copied into it. Runtime deps: the third-party imports of the four packages (an AST scan found
  `anthropic`, `dotenv`, `httpx2`, `langgraph`), pinned to this venv (`anthropic==1.13.0 httpx2==2.13.1
  python-dotenv==1.2.1 langgraph==1.2.20 langchain-core==1.7.6`). A test compares the pins with the installed
  versions: after a venv upgrade, update the Dockerfile and rebuild. User `sandbox` uid/gid 1000, HOME=/workspace.
  The image is 328 MB and was built on 2026-11-08 (first attempt failed on the flaky daemon; `build.sh` on retry).

### For the integration step (agent.py, not done here)

- **deepagents refuses `permissions=` next to an execution backend** unless every rule path starts with a
  CompositeBackend route key (`FilesystemMiddleware._set_permissions` -> `_all_paths_scoped_to_routes`).
  `agent.readonly_permissions()` lists `/skills`, `/skills/**`, `/input`, `/input/**`. The bare roots never
  start with `/skills/` / `/input/`, and `/input/` is no route (input files live in the state). So
  `create_tfl_agent(backend=make_backend(default=DockerSandbox()))` raises `NotImplementedError` today. The P5
  acceptance test (`test_mock_coder_scenario_runs_cyk_in_the_sandbox`) monkeypatches `readonly_permissions` to
  `["/skills/**", "/input/**"]` and adds an `/input/` route (`StateBackend`). agent.py needs the same, done
  properly. A `StateBackend` routed at `/input/` sees `/task.pdf`, not `/input/task.pdf` (CompositeBackend strips
  the prefix), so seeding `run_task(files=)` must follow.
- With the sandbox as the default backend, every non-routed path goes to the container, whose root is read-only.
  Only `/workspace`, `/tmp` and the two deepagents offload roots are writable, so main-agent scratch files must live
  under `/workspace/`. The alternative is a composite whose default stays `StateBackend` with `/workspace/`
  routed to the sandbox. But deepagents only offers `execute` when `CompositeBackend.default` is a sandbox, so that
  needs a wrapper.
- `interrupt_on["execute"]` (HITL, off by default) is wired in agent.py already. It has still not been exercised
  with this backend.

### Test runs

- `pytest -q tfl_deep_agent/tests/test_sandbox.py`: 46 passed (about 25-40 s with Docker). With `docker` hidden from
  PATH: 37 passed, 9 skipped ("Docker sandbox unavailable: the 'docker' executable is not on PATH").
- `pytest -q -n 4 tfl_deep_agent/tests`: 1326 passed, 1 xfailed (79 s, Docker visible).
- Seen once while another agent ran the same suite: two `tfl-sbx-*` containers listed from that concurrent run.
  They were gone 20 s later, so no leak.

## P4-P6: integration (2026-11-08)

Scope: wire P4 (orchestration tools), P5 (Docker sandbox) and P6 (Lean tools, lean-formalizer role) into
`create_tfl_agent` and the roles. Files: `agent.py`, `roles.py`, `prompts/main.md`, new `sandbox/workspace.py`,
new `tests/test_integration_p4_p6.py`.

### Decisions

- **The `sandbox` parameter.** `create_tfl_agent(..., sandbox=None | False | "auto" | True | <SandboxBackendProtocol>)`
  is resolved by `agent.resolve_sandbox` into `graph.tfl_sandbox` (a `SandboxInfo` with available, reason, sandbox,
  owned, kind).
  - The default `None` means no sandbox. Building an agent never touches Docker, so existing tests and library
    callers are unchanged.
  - `"auto"` checks `sandbox_status()` and builds a lazy `DockerSandbox(run_id=name)` that the graph owns.
    `agent.close_sandbox(graph)` removes it; the `DockerSandbox` finalizer also removes it at garbage collection or
    exit.
  - A sandbox the caller passes stays the caller's to close.
  - An explicit `backend=` decides by itself (`backend_can_execute`). Passing both `backend=` and `sandbox=` raises
    `ValueError`.
- **The backend shape.** `make_backend(sandbox=sb)` builds
  `CompositeBackend(default=SandboxedStateBackend(sb), routes={"/skills/": read-only, "/workspace/": WorkspaceBackend(sb)})`.
  - deepagents registers `execute` only when the composite's *default* is a `SandboxBackendProtocol`. It picks the
    state schema (`files`) by `isinstance(default, StateBackend)`.
  - So `SandboxedStateBackend` subclasses both. Files are plain state and `execute` goes to the sandbox, so the
    main agent's scratch files stay in the checkpointed state, as without a sandbox.
  - `WorkspaceBackend` puts the `/workspace` prefix back for the container. CompositeBackend strips the route
    prefix, but the container's writable directory is `/workspace` itself. `WorkspaceBackend` strips the prefix again
    from the paths it returns, so ls, glob, grep, upload and download paths come out as `/workspace/...`.
- **Permissions.** deepagents refuses `FilesystemPermission` rules next to an execution backend unless every rule
  path starts with a route prefix, and `/input/` is not a route. So `agent.permissions_for(backend)` works as follows:
  - A backend without execution gets the old `readonly_permissions()`.
  - A backend with execution gets only `<route>/**` for the read-only routes that are present.
  - `/input/` is then protected by `SandboxedStateBackend` itself, which refuses write, edit, delete and upload
    under `/input/`.
  - The signature of `readonly_permissions` is unchanged. The P5 acceptance test still monkeypatches it. The patch
    is no longer needed but does no harm: that test's backend executes, so `permissions_for` takes the route-scoped
    branch.
- **Only the coder gets execute.** The existing `exclude_tools_middleware(SANDBOX_ONLY_TOOLS)` stays on main and on
  every role without `allow_sandbox`. It hides `execute` from requests and refuses it at dispatch.
  - Without a sandbox, `execute` is not registered at all, and roles with `allow_sandbox` (the `coder`) are dropped
    from the build. deepagents then refuses `task(coder)` as an unknown subagent.
  - The HITL `execute` interrupt is added only when a sandbox is available.
- **System prompt.** `main_system_prompt(..., sandbox=, orchestration=)` adds two lines to "This run":
  - The sandbox line. When available: only the coder runs code under /workspace/, and its output is at most
    well_formed and never the basis of the verdict. When not: "NOT available (<reason>)", with no execute tool, no
    coder role, and the report must say that no code was run.
  - An orchestration-tools line.
- **`prompts/main.md` changes.**
  - A new section "Ad-hoc agents and workflows".
  - The Lean and coder role lines no longer carry phase notes. The coder line says the role exists only with a
    sandbox.
  - "Choosing models" names `spawn_subagent` and `run_workflow`.
- **Orchestration (P4).** `create_tfl_agent(..., orchestration=True)` builds one `OrchestrationContext` inside the
  run's budget and evidence scopes.
  - The context is created with `budget=None` (the enclosing `use_budget` scope), so allocations are children of
    the run's node. It uses the run's evidence ledger and the run's `RunLimits`, so children count against the same
    call limits and depth.
  - The tools go to main only. Spawned and workflow children never get file tools or `execute`, as in P4.
  - Orchestration tools whose names are already in `extra_tools` are skipped, so the P4 test that passes its own
    tools has no duplicates.
  - The context is available as `graph.tfl_orchestration`. `orchestration=False` leaves the tools out.
  - `create_tfl_agent` already refuses a live factory without a budget, so no extra guard is needed.
- **Mock scripts.** `load_mock_script` accepts the P4 child keys `spawn:<name>` and `workflow:<step id>` (P4
  `NAME_PATTERN` after the prefix). Other unknown keys are still errors.
- **Lean (P6).** The `lean` group was already in `MAIN_TOOL_GROUPS` and in the lean-formalizer role.
  - `_classify` now knows `lean_check` (R-Lean). For reg, cfl and dcfl, a result counts as `[(side, "verified")]`
    when `data.cls == lang`, `status == "proved"`, the tool trust is `verified`, and the side is constructive or
    destructive.
  - The IR binding is the usual fingerprint rule of `verdict_gate`. `lean_check` data carries `ir_fingerprint`, so a
    proof for another IR lands in `excluded_ir_mismatch`.
  - `ll` has no Lean path and ignores the result.
  - The role description and `main.md` no longer say "phase P6".

### Tests

`tests/test_integration_p4_p6.py` has 26 tests, all with scripted models.

- **End to end, parametrised over fake and docker.**
  - Main calls `run_workflow` with three parallel steps (prover/sonnet with a `cyk` oracle call, prover/haiku,
    general/haiku) and a critic judge that depends on all three, budget 1.2. Then it calls `task(coder)`.
  - The coder runs `write_file /workspace/brute.py`, then `execute python /workspace/brute.py`. The script is a CYK
    brute force over {a,b}^<=6 that imports `cfl_system.lib.cyk`.
  - Checked: the workflow layers; budget nodes closed and within budget; the branch's cyk record in the run ledger
    under `task/workflow:anbn/cyk-check` with `parent_call`; the `execute` output; the execute record with
    `role=coder` and trust at most well_formed; main has the orchestration, task and lean tools but never `execute`;
    both prompt lines.
  - The `fake` case uses `FakeSandbox`, a `FilesystemBackend` plus `SandboxBackendProtocol` that runs `python <file>`
    locally with the repo on `PYTHONPATH`.
  - The `docker` case uses the real `DockerSandbox` and also checks the `/opt/tfl/cfl_system/lib/cyk.py` path. It
    skips when `sandbox_status()` fails. It passed here with Docker.
- **No sandbox** (`None`, `False`, and `"auto"` with Docker reported missing):
  - no coder model is built;
  - there is no `execute` in main or prover;
  - the prompt states the reason;
  - `task(coder)` is refused.
- **With a sandbox:**
  - prover and main have no `execute`;
  - an `execute` call from main (with strict_tools off) is refused at dispatch and runs nothing;
  - `"auto"` is owned by the graph and closed by `close_sandbox`;
  - the HITL execute interrupt is added only with a sandbox;
  - passing both backend and sandbox raises;
  - an explicit backend decides by itself.
- **Adapters:**
  - `/workspace` path mapping for write, read, ls, glob, grep, edit, upload, download and execute;
  - `/input` is read-only, both in `SandboxedStateBackend` and through the agent;
  - `/skills` writes are denied;
  - scratch files stay in the state;
  - the rule shapes `permissions_for` returns.
- **Orchestration:**
  - `orchestration=False` leaves the tools out;
  - no duplicates with `extra_tools`;
  - a child scripted as `spawn:helper` counts against the shared `RunLimits`;
  - mock-script key validation.
- **Lean:**
  - a proved lean_check is a verified basis (reg destructive, cfl constructive);
  - no basis from failed, non-verified, wrong-class, ll or side-less results;
  - a proof for another IR is excluded;
  - the lean-formalizer has the lean tools and no execute.

### Test runs

- `pytest -q tfl_deep_agent/tests/test_integration_p4_p6.py`: 26 passed. The real Docker e2e case passed.
- `pytest -q -n 4 tfl_deep_agent/tests`: 1352 passed, 1 xfailed (70 s, Docker visible).
- Full suite, `pytest -q -n 6`: 4982 passed, 78 skipped, 3 xfailed, 28 failed.
  - None of the failures are in `tfl_deep_agent`. All 28 are Docker-Lean compile tests in
    agent_system/cfl_system/dcfl_system: the same environment class as the 33 at the P3 gate.
  - One of them, `test_phase3::TestMathlibImportCompiles::test_dfa_import_compiles`, passes when run alone, which
    points to Lean containers under parallel load.

### Open

- `hitl.py` (`start_run`, `resume_run`, `_segment`), the CLI and TFL Lab still call `create_tfl_agent` without
  `sandbox=`. Until the runner passes `sandbox="auto"` and calls `close_sandbox(graph)` at the end, a real run has
  no coder. That change is outside this step's scope.
- `settings.example.json` has no sandbox switch. A `sandbox.enabled` key would need validation in `settings.py`.
- `/workspace` lives in the container. A HITL resume in a new process gets a fresh, empty `/workspace`; the state's
  scratch files survive.
- `execute` results carry no ToolResult, so the ledger stores them as `not_verified`. That stays within "sandbox
  output is at most well_formed", but the report cannot cite them as `well_formed`.
- `collect_result` takes only the state, so `graph.tfl_sandbox.to_dict()` is not yet copied into `result.json`.
  The runner should add it.

## P4-P6 FIX (2026-11-08)

Review findings of P4-P6, checked against the code first. Phase tests: `pytest tfl_deep_agent/tests` 1362 passed,
1 xfailed (the Docker sandbox and Lean tests ran against the real images).

### Fixed

- **lean: stale IR fingerprint (major).** The cache is keyed by `statement_id`. Two IRs that differ only outside
  the language (`source_text`) render the same statement, so the second IR used to get the first IR's
  `ir_fingerprint`, and `verdict_gate` put a verified proof into `excluded_ir_mismatch`. Now `lean_statement`
  reports the fingerprint of the IR passed in that call. `lean_check` reports the fingerprint of its own `ir`, or,
  without one, of the IR this run last rendered the statement from. Only when neither exists does it fall back to
  the cache's most recent IR. `data.ir_fingerprint_source` = `call` / `run` / `cache` says which was used.
  `_resolve` now re-renders every explicitly passed `ir` and compares ids; before, it skipped the check when the
  fingerprints were equal.
- **lean: per-run attempts (minor).** Attempt counters (`MAX_CHECKS_PER_STATEMENT`) and "the IR of this run" now
  live in run scopes keyed by the LangGraph `thread_id` of the tool call (`langgraph.config.get_config()`; hitl
  uses the run id as thread id). Subagents and workflow steps inherit it through the config context var. An
  explicit `lean.lean_run(key)` block takes precedence; with neither, there is one `process` scope. There are at
  most 256 scopes (LRU). The rendered statements stay in a process-wide cache. New helpers: `run_key()`,
  `attempts_used(sid, key)`. `reset_store()` clears both.
- **workflow: opus via role default (two findings, minor).** A step with a role and no model whose configured
  role model is opus now needs `reason` (>= `MIN_REASON_CHARS`) when `opus_needs_reason` is on. The check looks
  at the configured model, not the `TFL_MODEL_OVERRIDE` one, the same as for explicit picks. The given reason is
  logged together with the note `role default: settings.models.roles.<role>`. The step and tool descriptions say
  so. Test: three critic steps without a reason are refused.
- **sandbox: docs mount (major).** `DEFAULT_MOUNTS` mounts the single file `docs/THEORY.md`, no longer `docs/`
  (which holds `EVAL_SET.md`, `EVAL_RESULTS.md` and `docs/examples`). The same concern applies to the pipeline
  packages, so their `tests/` and `examples/` (solved tasks with expected verdicts) are hidden as well, see the
  next item. Checked in a real container: there is no `/opt/tfl/docs/EVAL_SET.md`, `cfl_system/tests` is empty and
  read-only, and `import cfl_system.lib.cyk` still works.
- **sandbox: masking gaps (minor).** `scan_mounts()` replaces the `_SKIP_DIRS` walk.
  `SandboxConfig.hidden_dirs` (default `.git .venv venv .claude .tfl_lab_runs node_modules tmp_* tests
  examples`) are covered by an empty read-only tmpfs (`HIDDEN_TMPFS_OPTS`) wherever they occur in a mount. Every
  other mounted directory is walked, `__pycache__` / `.pytest_cache` / `.ruff_cache` included, for secret files
  and for `hidden_files` (`EVAL_SET.md`, `EVAL_RESULTS.md`), which get the empty mask file. In whole-repo mode
  (`mounts=(".",)`) this hides `.git`, `.venv`, `.claude`, `.tfl_lab_runs`, `tmp_*` and every tests / examples
  directory. `build_run_args(hidden_dirs=...)`, `DockerSandbox.hidden_dirs`. The test that expected
  `__pycache__/.env` to stay unmasked now expects it to be masked.
- **agent: max_spawn_depth = 0 (minor).** Only `budget_status` is bound. The "This run" line then says that
  `spawn_subagent` / `run_workflow` are not available, instead of advertising them.
- **workflow ir_builder vs HITL 1 (minor).** I took the documentation route and kept `ir_builder` in
  `WORKFLOW_ROLES`. The HITL 1 line of the system prompt and the `run_workflow` description now say that an IR
  built in a step or by a spawned subagent is not reviewed: build it with `task(ir-builder)`, or run main's own
  `validate_ir` on it, which HITL 1 does review. `prompts/main.md` (outside this fix's scope) still lists
  `ir_builder` for `run_workflow` without that warning. The generated lines above cover it at run time.
- Sandbox prompt line: "the pipeline packages read-only at /opt/tfl" (it was "the repository").

### Not fixed here (outside this fix's scope: hitl.py, cli.py, settings.py, tools/base.py, middleware/)

- **The runners do not open a sandbox (major, deferred).** This finding is correct: `hitl.start_run` /
  `resume_run` / `_segment` and the CLI never pass `sandbox=`, so no real run has a coder. The fix belongs in
  `hitl.py` / `cli.py` (pass `sandbox="auto"` or a validated `settings` key, call `agent.close_sandbox(graph)` in
  a `finally`, and copy `graph.tfl_sandbox.to_dict()` into `result.json`). Turning a Docker container on in every
  user run also changes behaviour, so the user should decide the default (on / off / a settings switch).
- **execute output as well_formed (minor, deferred).** `EvidenceMiddleware.tool_result_of` admits only tools
  registered in `tools.base.REGISTRY`, and `GROUPS` has no sandbox group. If `execute` were registered under an
  existing group, it would leak into `main_tools()` and the role tool lists. The clean fix is a `sandbox` group
  (ceiling `well_formed`) in `tools/base.py`, kept out of `MAIN_TOOL_GROUPS`, plus a coder-only wrapper. That
  needs changes to tools/base.py and middleware/evidence.py.

## P4-P6 — GATE (2026-11-08)

No code changes in this step. Docker 27.5.1 is up, and the images `tfl-sandbox:latest` and `tfl-lean4:latest` are present.

- `pytest -q -n 4 tfl_deep_agent/tests`: 1361 passed, 1 skipped, 1 xfailed (the known `test_default_ceilings_fit_the_example_budgets`).
  The one skip is a real-Docker test that skipped itself after the flaky daemon would not start a container.
  The four phase files run serially (`test_sandbox`, `test_tools_lean`, `test_integration_p4_p6`,
  `test_orchestration`): 174 passed, 0 skipped. Every real-Docker case ran in that run.
- Full suite, `pytest -q -n 6`: 4992 passed, 70 skipped, 3 xfailed, 36 failed. None of the failures are in tfl_deep_agent.
  All 36 are Docker-Lean compile tests in agent_system / cfl_system / dcfl_system. Their errors are the daemon flake
  (`OCI runtime create failed ... read init-p: connection reset by peer`, `runc ... exit status 139`), the same
  environment class as the 28 at the integration step and the 33 at the P3 gate. Three samples pass when run serially.
- Acceptance, each item met:
  - P4: `test_orchestration::test_three_parallel_branches_and_a_judge_stay_in_budget`, the e2e workflow in
    `test_integration_p4_p6`, and the refusal tests (disallowed model, opus without a reason, depth, cycle printed
    as `a -> b -> a`).
  - P5: `test_sandbox::test_mock_coder_scenario_runs_cyk_in_the_sandbox` and
    `test_real_coder_style_script_imports_cyk_from_opt_tfl` (real container), and the docker case of the e2e
    test.
  - P6: `test_tools_lean::test_golden_reg_proof_verifies_through_the_tools` ran against the real `tfl-lean4`:
    status proved, trust verified.

## P7 UI — backend routes (2026-11-08)

Files:
- `ui_server/deep_routes.py` (new)
- `ui_server/server.py` (a minimal hook)
- `tfl_deep_agent/progress.py` (new: the event emitter)
- `tfl_deep_agent/hitl.py` (emitter calls in `_segment`, plus a retry in `save_run`)
- `ui_server/tests/test_deep_routes.py` (new)
- `ui_server/tests/conftest.py` (now imports the guard fixtures)

Python is `.venv/Scripts/python`.

### Endpoints

All live under `/api/deep/`. Every endpoint below is listed in the `deep_routes` docstring.

| Method | Path | Notes |
|---|---|---|
| GET, POST | `runs` | create a run |
| GET | `runs/<id>?after=N&limit=M` | run detail, polled |
| GET | `runs/<id>/files/<path>` | whitelisted run files |
| POST | `runs/<id>/resume` | answer a HITL stop |
| POST | `runs/<id>/cancel` | stop a run |
| GET | `estimate` | money limits and measured cost |
| GET, PUT | `settings` | deep-agent settings |
| GET | `mock-scripts` | added: the UI needs to list the replay scripts |

`server.py` changes:
- `do_GET` and `_handle_body_request` hand `/api/deep/*` to `deep_routes.dispatch`, which maps its own errors: 400 / 404 / 409, 503 when the `[deep]` extra is missing, otherwise 500.
- `/api/deep/*` keeps the same loopback-Host and same-origin JSON guards as every other route.
- The body limit for `/api/deep/*` is `deep_routes.max_body_bytes()`: `intake.max_file_mb` × 4/3 + 1 MB, capped at 64 MB. Every other route keeps `MAX_BODY_BYTES`.
- `main()` calls `deep_routes.restore_runs()` after the pipeline restore. Errors are logged and never block start-up.
- `deep_routes` is imported lazily, and it imports `ui_server.server` lazily for the stateless helpers: `_read_progress`, `_files_in`, `process_identity`, `_pid_matches`, `_kill_pid`. Under `python -m ui_server.server` that is a second module object, which is harmless because only pure functions are used. A smoke test on port 8799 under `-m` passed; that server was stopped afterwards.

### Decisions

**Run directory.** Runs live in `.tfl_lab_runs/deep/<12 hex>/`. The pipeline registry only scans 12-hex directories directly under `.tfl_lab_runs`, so it never sees these (tested). The 12-hex name is the agent's `run_id` through `--save`.

**Who owns which file.**
- The agent owns `run.json`, checkpoints, evidence, `progress.jsonl` and `result.*`.
- The server owns `lab.json` (status, live, pid + identity, segments with argv / rc / outcome), `run.log` (child stderr; stdout prefixed), `upload/task.<ext>`, `ui_settings.json`, `ui_mock_script.json` and `ui_decisions/NNN-<point>-<decision>.json`.

**Status values.**
- `queued` and `running` mean a process exists.
- After exit, the status comes from the agent's `run.json` together with the exit code:
  - rc 3 + `awaiting_human` + pending → `awaiting_human`;
  - rc 0/1 + a final status → `completed` with `result_status` = submitted / incomplete / budget_exhausted / error;
  - any other rc → `error` with the stderr tail.
- No process while the agent status is still `running` → `interrupted` (a server restart or a crash).
- `cancelled` is a lab override.

**Subprocess.** Every run, start or resume, is one `python -m tfl_deep_agent` process with `--no-dialog`.
- Start: `--file upload/task.<ext> --save <dir> --settings ui_settings.json [--template] [--auto-approve] (--mock-script F | --live)`.
- Resume: `--resume <dir> --decision D [--payload @ui_decisions/…json] [--live]`.
- Text tasks also go through `--file task.txt`, which is one code path and avoids the argv length limit.
- `build_start_command` and `build_resume_command` are module functions so tests can swap them.

**Child environment.**
- `PYTHONIOENCODING` and `PYTHONUTF8` are set.
- `TFL_DEEP_RUNS_DIR` is set to `deep_routes.spend_dir()`, which is the env value or `.tfl_lab_runs`. This is the CLI's default, so UI runs and CLI runs share one daily spend log.
- A mock child gets **no** `ANTHROPIC_API_KEY` even if the server has one. A live child inherits the server's environment as it is; the server never loads `.env`.

**Money.**
- Mock mode is the default and needs a `mock_script`: either an `examples/mock/*.json` name (only a lookup key into that directory) or an inline script object.
- `live` needs `confirm_spend: true` (exactly `true`) and excludes `mock_script`.
- A live run is checked against `DailySpendLog.check_can_start` before anything is spawned (409 when refused). The CLI checks again.
- Resuming a live run needs `confirm_spend` again, because each segment spends.

**Settings for a run.** The current deep settings, merged with `settings_override` by `apply_overrides` and validated, are snapshotted to `ui_settings.json`. A broken settings file is a 409, never a silent fallback to the defaults (it holds the money limits). `PUT /settings` merges a partial object, validates it, and saves it atomically (`save_settings`).

**Uploads.**
- `text`, or `file_b64` + `mime` (+ `name`); a `data:` URL is accepted.
- Pre-validated in the server process by `intake.files.from_text` / `from_base64` with the run's limits. An `IntakeError` is a 400 that carries its code (`bad_base64`, `too_large`, a bad PDF, …).
- The file is stored as `upload/task.<ext>`, with the extension taken from the sniffed type, and the user's file name is kept in `lab.json`.

**Resume.**
- Only from `awaiting_human`, with no live process, not cancelled, and when the agent's `run.json` has a `pending`.
- The decision is pre-checked with `hitl.parse_decision` + `hitl.resume_value(pending.request, …)`, so a misfit is a 400 now and the run keeps waiting.
- `reject` needs a comment. The HITL library accepts an empty one, but the agent needs something to act on (UI choice).
- `payload` + `comment` go to a JSON file passed as `--payload @file`.

**Cancel.**
- A queued run is never started, and its slot wait notices the flag.
- A running run's process tree is killed (`taskkill /T` on Windows).
- An awaiting run becomes `cancelled` and can no longer be resumed.
- Cancel is idempotent on terminal runs.
- An adopted orphan (after a restart) is killed through `server._kill_pid` with its identity.

**Concurrency.** A separate gate, `TFL_LAB_MAX_DEEP_RUNS` (default 2), counted apart from the pipeline gate.

**Detail response.**
- `events`, `next` and `total` follow the same `after=N` contract as `/api/runs/<id>`.
- These views are folded from **all** of `progress.jsonl`, so a late poller sees everything (`fold_events`):
  - `todos` (latest)
  - `tools` (count by trust, failed)
  - `subagents`: a tree by node path (`t1/prover/spawn:x`), with allocated / spent / returned USD, the per-model spend, calls, roles, open/closed status, and `subtree_spent_usd`
  - `budget`: the latest event snapshot, plus today's spend and limit
  - `budget_signals`
  - `orchestration`: spawn_* and workflow_* events
  - `hitl.decisions`
  - `done`
- `hitl.pending` is read from the agent's `run.json`, which is the authority. It carries the point, interrupt_id, allowed decisions, action args, the `hitl.md` card, the source file URL, and at HITL 2 the draft and its URLs.
- `result` comes from `result.json`.

**File route.** Only `result.{json,md,html}`, `hitl.md`, `evidence.jsonl`, `progress.jsonl`, `run.log`, `input/*` and `upload/*`. These go through `server._files_in`, so there is no `..` and no symlink escape. `run.json`, `lab.json`, the settings snapshot and `checkpoints.sqlite` are not served.

**Estimate.**
- Limits come from the settings, optionally with an override passed as a URL-encoded JSON query parameter.
- Today's spend comes from the spend log.
- `model_cost_table(settings)` gives the worst-case cost per call per tier.
- Measured cost: n / mean / min / max of `budget.spent_usd` over finished **live** deep runs (optionally of one template), with the high value capped at `per_task_usd`. It reports "no data" until such a run exists; there is no static price table, as in `/api/estimate`.

**Restore.**
- A `lab.json` that says queued/running with a dead pid is settled from `run.json`, so a HITL stop stays resumable after a restart (tested).
- A live pid is adopted and watched, and settled when it exits.
- Unlike the pipelines, start-up is never refused for deep runs.

**Event emitter** (`tfl_deep_agent/progress.py`). `ProgressEmitter` listens to `BudgetLedger` and `EvidenceLedger` and appends JSON lines to `progress.jsonl`, thread-safe and never raising. Each line carries the cumulative `usage` (calls, tokens, estimated cost) and a `budget` snapshot. Event kinds:
- `segment_start` / `segment_end`, with a 1-based `segment`
- `model_call` (node, role, model, usd)
- `subagent_start` / `subagent_end` (node, kind, allocated / returned USD)
- `subagent_refused`, `budget_wrap_up`, `budget_refused`
- the orchestration events, passed through under their own names
- `todos` (from `write_todos` evidence)
- `tool_call` (id, tool, ok, **trust**, trust_reason, error_kind, role, scope, model, cost, parent_call, elapsed, args preview ≤300 chars, and subagent_type for `task`)
- `hitl_awaiting` (the card ≤20 kB, and the draft at the report point)
- `hitl_decision`
- `done` (status, verdict, language_class, trust, confidence_cap, error)

`hitl._segment` changes:
- Opens the emitter after the evidence ledger.
- Calls `hitl_awaiting` after `_write_awaiting`.
- Calls `hitl_decision` after an in-loop decision.
- Calls `segment_end` / `done` at the two exits.
- Calls `detach()` in `finally`.

### Bug found and fixed

The bug was on Windows, while the UI polls a running run:
- `GET /runs/<id>` read the agent's `run.json`.
- The agent's `save_run` does `tmp.replace(run.json)`. It failed with `PermissionError: [WinError 5]` while the server had the file open, and the run ended as `error`. This was reproduced by the real mock test.

There are two fixes:
1. `deep_routes` never opens `run.json` / `result.json` of a run whose process is alive (`_agent_run_if_idle`; the estimate skips busy runs too). A live run is shown from `progress.jsonl`, which is append-only and safe to read.
2. `hitl.save_run` retries the replace on `PermissionError` (40 × 50 ms) before raising. This covers other readers too: an editor, an antivirus scan.

The agent's `result.*` are written in place with `write_text` (no replace), so readers do not break them.

### Tests

`ui_server/tests/test_deep_routes.py` has 34 tests. No live calls, and no `.env` is loaded.
- **Fake-agent tests.** The start and resume builders are replaced by a script that writes `run.json`, `progress.jsonl` and `result.json`, then exits 0 / 3 / 2 or sleeps. The real builders' argv is recorded and asserted. These cover:
  - 14 parametrised 400s
  - argv and child env (`ANTHROPIC_API_KEY` is dropped for mock)
  - live + `confirm_spend`
  - the day-budget 409
  - PDF upload storage
  - resume validation, payload file and `--live`
  - cancel: running / queued / awaiting
  - a crash returns the stderr tail
  - restore after a dead server
  - the pipeline registry ignores the deep directory
  - the file whitelist
  - `fold_events` tree / costs
  - settings GET / PUT / broken file
  - estimate: no data → measured, plus override
- **HTTP tests** through the real `srv.Handler` on port 0:
  - routes and 404
  - 403 for a cross-origin POST and for a non-JSON PUT
  - `after=N` polling
  - a body over the pipeline 1 MB limit is accepted
  - over the deep limit → 413. This test sends only the Content-Length header, because a large unread body makes Windows reset the connection.
- **One real mock run** through the actual CLI subprocesses: a PDF with `hitl_scripts.pdf_script()` → HITL 1 (card, source URL, tool trust, subagent tree with intake and ir-builder, budget, usage) → resume `edit` of the transcription → HITL 2 (draft and its URLs). A bad edit there is a 400 and the run keeps waiting. Then `approve` → `completed` / `submitted`, `result.task` equals the edited transcription, segment rcs are `[3, 3, 0]`, and the day spend stays 0.

`ui_server/tests/conftest.py` now imports `tfl_deep_agent.tests.guard`. `test_network_guard::test_other_test_dirs_that_use_the_agent_import_the_guard` requires that for any test directory that uses the agent.

### Test results

| Command | Result |
|---|---|
| `pytest -q ui_server/tests tfl_deep_agent/tests/test_hitl.py tfl_deep_agent/tests/test_e2e_mock.py` | 191 passed |
| `pytest -q ui_server/tests/test_deep_routes.py`, run 3× | 34 passed each time |
| `pytest -q -n 4 tfl_deep_agent/tests` | all passed, except the guard test above, which then passed |
| full `pytest -q -n 6` | only failures are Docker-Lean compile tests in agent_system / cfl_system / dcfl_system (`OCI runtime create failed`, `runc ... exit status 139`), the same daemon flake recorded at the P3 and P4-P6 gates |

### Open points

- There is no frontend yet; that is the P7 UI step. The routes return everything the tab needs.
- No `examples/mock` script exercises the PDF intake path. The UI can upload a PDF only with an inline `mock_script`, or in live mode. A small `examples/mock/ll_pdf_intake.json` (built from `pdf_script()`) would make a PDF demo one click.
- The deep runs have their own concurrency gate, so the two gates together can start 2 + 2 processes. A shared gate would need a change to `server.py`'s slot code.

## P7 UI — frontend tab (2026-11-08)

TFL Lab has a second workspace, **Deep Agent**, next to the existing pipelines workspace. A switch in the top bar flips between them (`#mode-pipelines` / `#mode-deep`; the choice is remembered in `localStorage`, and `#deep` in the URL opens it). The pipelines workspace and its tabs are unchanged. The tab talks only to `/api/deep/*` (`ui_server/deep_routes.py`) and follows its contract field for field.

Files: `ui_server/static/index.html` (the `#deep-view` markup and the deep settings modal), `ui_server/static/lab.css` (an appended "Deep Agent" section), `ui_server/static/app.js` (an appended self-contained section plus three one-line changes to shared handlers), `ui_server/tests/test_deep_frontend.py` (new).

### What the tab shows

- **Left, the composer.**
  - A text area that also takes drag-and-drop, a file picker or a pasted image. PDF, PNG, JPG and WebP files are read with `FileReader.readAsDataURL` and sent as `file_b64` + `mime` + `name`; a text file is loaded into the text area. The file is checked against `intake.max_file_mb` before upload, and the server checks again.
  - Run controls: template ("agent picks" leaves it to the agent), main model, budget per task and the two review points. These go out as `settings_override` holding only the values that differ from the saved settings.
  - Mode: one entry per `examples/mock` script, "inline replay script (JSON)", or "live · paid API calls". A live run needs the "I confirm API spend" tick and sends `live: true, confirm_spend: true`. The tick resets after each live start. Cmd/Ctrl+Enter starts only a mock run, never a live one.
  - An auto-approve option.
  - An estimate from `GET /estimate`, debounced. In mock mode it says "$0" and shows the live cap; in live mode it shows the measured average, the cap, the reserve, the main model's worst case per call and today's spend. It turns red when `day.can_start` is false.
  - The list of deep runs, refreshed every 3 s while one is active.
- **Centre.**
  - The run header: status, id, mode, template, verdict with its trust chip and cap, cost, elapsed time, and a cancel button.
  - The plan, from the `todos` in the detail response.
  - The timeline, built from `progress.jsonl` events fetched with the `after` cursor every 0.8 s while a run is queued or running. Each tool call carries its trust chip, indented by scope depth, with model, cost and elapsed time; clicking it shows `args_preview` and the trust reason. Model calls, subagent start and end, spawn and workflow steps, budget signals, HITL stops and decisions, segments and `done` also appear. Filters: all / tools / agents / review. The last 600 rows are drawn.
- **Right, four tabs.**
  - **Review**: the HITL cards.
    - *HITL 1* puts the source (PDF in an iframe, image, or text) next to the editable transcription. It also shows the class select with the `validate_ir` result, the IR as editable JSON, the `ir_to_text` restatement, sample words (`in_language` / `not_in_language`, with "" shown as ε) and the ir-builder answer.
    - *HITL 2* shows the rendered draft in a sandboxed iframe (`draft_urls.html`) next to editable fields: template switcher, verdict, answer, language class, method and summary; hypothesis, construction, conclusion and the steps (as JSON) sit in a drawer. The card head carries the draft's trust and cap, and a banner lists any `unknown_ids`.
    - Any other stop (sandbox `execute`) shows its arguments as JSON, editable when `edit` is allowed.
    - Every card has a comment box and Approve / Save edits / Reject with comment. Changed fields are highlighted, and the edit button lists them.
    - The review history comes from `hitl.decisions`.
  - **Agents**: the subagent and workflow tree from `subagents`, with the per-model cost, calls, roles, allocated and returned budget and subtree cost. It also shows tool calls by trust level and the orchestration and budget-signal log.
  - **Report**: the summary, the template switcher, the report as HTML (sandboxed iframe), Markdown (marked + DOMPurify + KaTeX, the same path as the pipelines' Markdown tab) or JSON, and the evidence table from `result.json`. The table lists id, tool, trust, ok, the citing steps and the reason; it tags `basis`, `refuted` and `unknown` ids and is followed by the per-step trust table.
  - **Log**: `log_tail`, plus the address of the full `run.log`.
- **Bottom, the budget bar.** Task spend against the limit, with the report reserve hatched, and today's spend against `per_day_usd`. Notes for wrap-up, exhaustion, mock (estimate only) and a reached day limit.
- **Settings.** In Deep Agent mode the gear (and "deep-agent settings") opens a modal over `GET/PUT /api/deep/settings`. Its groups are budget, models (allowed list, model per role, `opus_needs_reason`), human review (the three points), limits and intake. Validation errors from the server are shown as they come back.

### Decisions

- **Own tab classes.** The existing `app.js` binds every `.tab` and `.panel` in the document to the pipelines result pane. The deep pane therefore uses `.dtab` / `.dpanel`; a test checks that the deep markup has neither class.
- **Appended section, shared helpers reused.** The deep code is appended at the end of `app.js` and reuses `el`, `num`, `fmtUsd`, `fmtTok`, `fmtDur`, `toMs`, `api`, `sendJson`, `ensureMdLibs` and `preprocessObsidianCallouts`. It does not move or duplicate any marker that `test_static.py` slices by. Three shared handlers changed by one line each:
  - Cmd+Enter starts a deep mock run in deep mode.
  - The gear opens the deep settings in deep mode.
  - Escape also closes the deep settings.
- **Pure helpers are separated.** Everything without DOM access sits between `// ---- deep: pure helpers begin` and `// ---- deep: pure helpers end ----`, so node can run it in tests. That covers body builders, edit diffs, the timeline, tree, budget and estimate views, and the evidence rows.
- **An edit sends only changed fields.** IR and steps are compared as canonical JSON, so key order does not matter. "Save edits" with nothing changed is refused in the browser; the backend would have turned it into an approve. A class change without an IR change is refused with the same rule as the backend ("a class change requires the IR").
- **Polling keeps the reviewer's input.** A card is rebuilt only when the pending `interrupt_id` changes, and the report view only when the result changes. A poll therefore never wipes an edit in progress.
- **Trust is only displayed.** The chip colours follow the five levels of `docs/VERDICT_POLICY.md`. Nothing in the tab computes or raises trust.
- **Iframes.** The report and draft HTML load in `sandbox="allow-scripts"` frames, as in the pipelines HTML tab. The source PDF loads in a plain same-origin iframe, which the browser's PDF viewer needs and the CSP allows.

### Deviations and backend findings

- **Template switcher.** There is no re-render route for a finished run. While HITL 2 is pending, picking a template sets the template field of the HITL 2 edit and moves to the Review tab; "Save edits" then re-renders, which the run check below confirmed. For a finished run the other templates are disabled, and a "use for the next run" button copies the template to the composer.
- **`result.template` in the detail response is the run's start template**, not the one the report was rendered with. After a HITL 2 template edit, `result.json` has `rendered.template = exam_solution` and `report.template = exam_solution`, but top-level `template` is still `short_verdict` (`hitl.py` `result["template"] = run.get("template")`), and `_result_view` passes that value on. The tab reads `rendered.template` from `result.json` (`deepReportTemplate`). The runs-list subtitle still shows the run template. The cleaner fix belongs in `deep_routes._result_view` or `hitl.py`.
- **PDF demo needs an inline script.** No `examples/mock` script reads `/input/task.pdf`. The browser check used the "inline replay script" mode with `tfl_deep_agent.tests.hitl_scripts.pdf_script()`. The open point from the backend step still stands: an `examples/mock/ll_pdf_intake.json` would make it one click.
- **A low per-task budget fails a mock run.** With `per_task_usd = 1.5` the PDF mock run ended `budget_exhausted` after 2 calls: the token estimate of the Opus main model's worst-case call does not fit. This is not a UI bug. The tab shows it as "the run ended without an accepted report: budget_exhausted".

### Tests

`ui_server/tests/test_deep_frontend.py` has 17 tests.
- **Structure (5)**: the deep ids and tabs exist, the existing workspace is still there, no `.tab` / `.panel` in the deep markup, every `/api/deep/*` endpoint is referenced, the sent fields are a subset of `deep_routes.CREATE_KEYS`, the CSS mode toggles and trust classes exist, and the helper markers appear once.
- **Pure helpers under node (11)**: file kind and limits, the override diff, the create body (mock / inline / live with and without confirm / file still reading), HITL 1 and HITL 2 edit diffs, the resume body rules (reject needs a comment, live needs confirm, allowed decisions), timeline rows from `progress.py`-shaped events, the tree and budget views fed with the **output of `deep_routes.fold_events`** (a contract test), the estimate text, evidence rows, and small helpers including `deepReportTemplate`.
- **Serving (1)**: `server.Handler` serves the new `app.js` and `index.html`.

### Verification on a separate server (port 8799, mock only)

- **Server.** A scratch launcher started `ui_server.server` on port 8799. It redirected `RUNS_DIR`, `SETTINGS_PATH`, `deep_routes.DEEP_RUNS_DIR` / `SETTINGS_PATH` (a copy of `settings.example.json`) and `TFL_DEEP_RUNS_DIR` into the session scratchpad, with `ANTHROPIC_API_KEY` removed. The server was stopped afterwards; the user's TFL Lab was not touched.
- **Scripted HTTP client.** It used the exact bodies the tab builds.
  - `GET /settings`, `/mock-scripts` and `/estimate` (with `settings_override`).
  - 400 for a mock run without a script, and for live without `confirm_spend`.
  - A PDF run with an inline `pdf_script()` stopped at HITL 1. The source served as `application/pdf`. Reject without a comment returned 400; an edit of the transcription was accepted.
  - HITL 2 delivered a draft (`verified`, cap 0.98, draft html served). An edit of the template was accepted, and the run ended `completed` / `submitted`.
  - Tree `task` → intake, ir-builder; 10 tool calls (verified 4, well_formed 1, not_verified 5).
- **Headless browser (Playwright).**
  - A PDF dropped on the box via a `DragEvent` showed as a file chip. Inline mode then went to "run mock".
  - At the HITL 1 card, the PDF iframe, IR, restatement and words rendered. Reject without a comment was refused in the browser; the transcription edit was highlighted and sent.
  - At the HITL 2 card, picking `exam_solution` on the Report tab moved to Review with "Save edits (template)". The edit was sent, and the run finished with the report rendered as `exam_solution`.
  - The evidence table showed `ev-ll-table` as basis. The budget bar read `$0.1509 / $5.00`.
  - In the settings modal, a save reported "saved" and updated the composer budget; an invalid value showed the server's 400 text.
  - Switching to Pipelines restored the old workspace, and its tabs still switched panels.
  - The console had no errors besides `favicon.ico` 404 and the intentional 400.
- **Fixes from the browser check**: the left pane overflowed horizontally (two-column field rows now use `minmax(0, 1fr)`); an empty `unknown_ids` printed a bare label; the header template was refreshed only after the next poll.

### Test results

| Command | Result |
|---|---|
| `node --check ui_server/static/app.js` | ok |
| `pytest -q ui_server/tests/test_deep_frontend.py` | 17 passed |
| `pytest -q ui_server/tests tfl_deep_agent/tests/test_network_guard.py` | 176 passed |

### Open points

- `result.template` in `GET /api/deep/runs/<id>` and the runs list should report the rendered template (see above).
- There is no re-render route for a finished run's report with another template. The switcher offers the template only for a new run, or at HITL 2.
- An `examples/mock` script with PDF intake would make the PDF demo one click.

## P7 UI FIX (2026-11-08)

Files: `ui_server/deep_routes.py`, `ui_server/static/app.js`, `ui_server/static/index.html` (one label), `tfl_deep_agent/hitl.py` (`_segment` order), `ui_server/tests/test_deep_routes.py`, `ui_server/tests/test_deep_frontend.py`, `tfl_deep_agent/tests/test_progress_segment.py` (new). All seven review findings were verified in the code and are real; all are fixed.

- **Several actions in one HITL stop (major).** `_hitl_view` returns `pending.actions` (name, args, description, per-action `allowed_decisions`) and `action_count`. The stop's `allowed_decisions` is the intersection over the actions, minus `edit` when there is more than one action, matching `hitl.resume_value`'s rule. `action` / `args` are kept for the first action. `deepGenericCard` renders every action, shows a banner that Approve or Reject applies to all of them, allows editing only for a single action, and puts the `hitl.md` card in a drawer. `deepPendingActions` is a pure helper that falls back to the old single-action shape.
- **A failed resume stays resumable (major).** `_settle_from_disk_locked` keeps `awaiting_human` whenever the agent's `run.json` is still `awaiting_human` with a pending stop, whatever the exit code. `resume_run` writes `running` before the agent runs, so a waiting record means the decision was never applied. For an rc other than 3, `error` holds the exit code and the stderr tail. `api_resume` records `resume_from` (the interrupt id) in `lab.json`, so the message can say "before applying the decision" when the stop is the same. A failed resume launch (rc None) also keeps the stop.
- **Report template (minor).** `_result_view.template` is now `rendered.template`, then `report.template`, then the run template (`rendered_template()`). `run_template` carries the start template. `lab.json` stores `report_template` when a run completes, so the runs list shows it too. A new route, **`POST runs/<id>/render {template}`** (`api_render`), renders a finished run's accepted report (lab status `completed`, result `submitted`) with another template **as a view only**: `views/<t>.{md,html}`, served under `views/`. `result.*`, which the human approved, is never rewritten. It is pure code, with no model call. The renderer gets the evidence ledger, the result's budget summary plus the `run.json` records, and `rendered.human_review`. In the Report tab, the switcher is now live for such runs and shows a banner "a view in X"; the JSON and the evidence table stay those of `result.json`.
- **Checkpointer before emitter (minor).** `_segment` opens the SQLite checkpointer before `open_emitter`. A failure now raises before a `segment_start` is written or listeners are attached; `run.json` is untouched by `_segment`, which is the same state as before P7.
- **Live settings_override (minor).** For live runs, `_live_override_problems` refuses (400) any `budget.per_day_usd` / `per_task_usd` / `per_subagent_max_usd` above the saved value, and any override that turns off `hitl.formalization` / `hitl.report` while the saved settings have it on. Lowering limits and turning review points on are allowed. Changing these for real goes through `PUT /settings`, an explicit, persistent choice. Mock runs are unchanged.
- **auto_approve on live (minor).** `api_create` refuses `auto_approve` together with `live` (400). The composer disables the checkbox in live mode, and `deepCreateBody` refuses auto-approve or a review point turned off in live mode before sending. The label says "mock only".
- **One live deep run at a time (minor).** `_acquire_slot(run_id, live)` also waits for a single live slot (`MAX_CONCURRENT_LIVE_RUNS = 1`, which counts an adopted live orphan too). Mock runs keep the parallel gate (`TFL_LAB_MAX_DEEP_RUNS`). A queued live run is checked against the day budget again by its own CLI when it starts. Still open: a live CLI run started outside TFL Lab is not covered by this gate. Covering it would need cross-process reservations in the spend log.

Tests added:
- `test_hitl_view_shows_every_action_of_one_stop`
- `test_a_resume_refused_by_the_cli_keeps_the_run_resumable` (also checks the stop survives a restore)
- `test_live_override_may_only_lower_money_limits_and_keeps_review`
- `test_live_runs_run_one_at_a_time`
- `test_live_slot_counts_an_adopted_live_orphan`
- `test_result_view_reports_the_rendered_template_and_render_route`
- a render step at the end of the real mock PDF run
- node tests for `deepCreateBody` on live runs and for `deepPendingActions`
- `test_progress_segment.py`: a checkpointer failure leaves no open segment and never opens the emitter

Browser check on a scratch server on port 8799 (mock only, scratchpad dirs, no API key; stopped afterwards):
- a fake two-command `execute` stop showed both commands with only Approve and Reject;
- on a finished mock run, picking `full_proof` loaded `views/full_proof.html` with the view banner;
- the auto-approve box was disabled in live mode.

| Command | Result |
|---|---|
| `pytest -q ui_server/tests tfl_deep_agent/tests/test_hitl.py test_e2e_mock.py test_network_guard.py test_progress_segment.py test_reports.py` | 274 passed |
| `pytest -q -n 4 tfl_deep_agent/tests` | 1362 passed, 1 skipped, 1 xfailed |

## P7 UI — GATE (2026-11-08)

No code changes in this step. Python is `.venv/Scripts/python`; no API key, mock only.

- Full suite, `pytest -q -n 6`: 5053 passed, 70 skipped, 3 xfailed, 35 failed. None of the failures are in
  tfl_deep_agent or ui_server. All 35 are Docker-Lean compile tests in agent_system / cfl_system / dcfl_system,
  failing on the daemon flake (`OCI runtime create/start failed`, `read init-p: connection reset by peer`,
  `runc ... exit status 139`), the same class as at the P3 and P4-P6 gates. A serial rerun of the 35: 23 passed,
  12 failed again with the same daemon errors.
- P7 files run serially (`ui_server/tests`, `test_progress_segment.py`, `test_hitl.py`, `test_network_guard.py`):
  223 passed, 0 skipped (node v22 present, so the frontend node tests ran).
- Acceptance:
  - endpoint tests on a fake agent: met (`ui_server/tests/test_deep_routes.py`, the fake-agent and HTTP tests).
  - a mock PDF run through both HITL points: met
    (`test_deep_routes.py::test_real_mock_pdf_run_through_both_hitl_points`: PDF -> HITL 1 edit -> HITL 2
    approve -> `completed` / `submitted`, segment rcs `[3, 3, 0]`, day spend 0).

## P8 eval+docs — docs (2026-11-08)

Files: `tfl_deep_agent/README.md` (rewritten from the P0 stub), `tfl_deep_agent/CLAUDE.md` (new), root `README.md`
(new section "TFL Deep Agent", plus one line each in Quickstart, Repository layout, `tfl-eval` and External
dependencies), root `CLAUDE.md` (project list, import direction, the live-run rule for the deep agent, settings and
budget location, `tfl-deep` commands, allowed actions), `tests/test_docs.py` (new, 19 tests).

### Decisions

- **The docs follow the code, not the plan, where they differ.** Checked against `cli.py`, `settings.py`,
  `middleware/budget.py`, `hitl.py`, `roles.py`, `config.py`, `sandbox/`, `tools/lean.py`, `ui_server/deep_routes.py`
  and the sibling eval adapter (`tfl_eval/cli.py`, `runners.py`). Examples:
  - the eval flag is `--systems deep`, not the plan's `--system deep`;
  - the sandbox is documented as not opened by the runners (see below);
  - main's worst-case Opus call ($1.28) is documented as an open budget limitation.
- **Commands were run, mock only.** I ran the README quick-start commands in the scratchpad with the API key removed
  and `TFL_DEEP_RUNS_DIR` redirected:
  - `ll_simple_ll1.json --auto-approve`: exit 0, verified, cap 0.98;
  - `reg_regex_regular.json --no-dialog`: exit 3 at HITL 1, then 3 at HITL 2, then 0 with `bounded_pass` / 0.85.
- **Sandbox status is stated as it is.** `hitl.start_run` / `resume_run` still build the agent without `sandbox=`,
  so CLI and TFL Lab runs have no `coder`. The README says so, and
  `test_readme_says_the_runners_open_no_sandbox_while_that_is_true` fails as soon as `hitl.py` starts passing
  `sandbox=`, so the docs change with the behaviour.
- **Root CLAUDE.md pricing line left as written.** It says model prices live in each project's `config.py`, but
  the four pipeline `config.py` files hold no prices: `MODEL_PRICING` is in `agent_system/lib/llm_client.py` (P0
  notes). That is the user's rule text, so I did not rewrite it. I only added the deep-agent line (its `config.py`
  re-exports the prices from `llm_client.py`) and list the mismatch as an open point.

### Tests (`tests/test_docs.py`)

- CLI: every `--flag` in the README's CLI section is a parser option and vice versa. Every `tfl-deep` /
  `python -m tfl_deep_agent` command in the `bash` blocks of both READMEs parses, and its `--mock-script` exists.
- Values against the code:
  - the settings table against `settings.DEFAULTS`, and the root README's budget figures;
  - templates, role keys, subagent names and default model tiers (`agent.TEMPLATES`, `roles.ROLES`,
    `config.MODELS`);
  - exit codes, run-directory file names (`hitl` constants), env vars and the spend-log name;
  - the image names.
- Files: every shipped `examples/mock/*.json` is listed; relative links, anchors and the layout tree resolve.
- Eval section vs `tfl_eval` (`DEEP_SYSTEM`, opt-in, flags, `DEEP_MOCK_DIR`). It skips when the adapter is absent.
- Root README section, and root CLAUDE.md / package CLAUDE.md content and paths.
- **CLAUDE.md files are gitignored** (`.gitignore`: `**/CLAUDE.md`), so the two CLAUDE.md tests skip when the file
  is missing (fresh clone, CI). It also means `tfl_deep_agent/CLAUDE.md` and the root CLAUDE.md edits are **not
  committed** unless the user changes `.gitignore` or force-adds them. That is the user's decision; I did not touch
  `.gitignore`.

### Test runs

- `pytest -q tfl_deep_agent/tests/test_docs.py`: 19 passed.
- `pytest -q -n 4 tfl_deep_agent/tests tests/test_prompt_contracts.py`: 1623 passed, 62 skipped, 1 xfailed. The
  skips are `test_prompt_contracts` pseudo-JSON sketches and the Docker tests; the xfail is the known budget pin.
- `pytest -q -n 4 tests/test_tfl_eval.py tfl_eval/tests` (with the sibling's adapter in the tree): 109 passed,
  8 skipped.

### Open

- The root CLAUDE.md pricing line (see above).
- Whether CLAUDE.md files should be tracked (`.gitignore`).
- The sandbox default for user runs, and the main-agent `max_tokens` vs budget choice, are both still open from
  earlier phases. The docs describe the current behaviour.

## P8 eval+docs — eval adapter (2026-11-08)

Files: `tfl_eval/runners.py` (deep section at the end + `DEEP_SYSTEM` / `ALL_SYSTEMS` / `DEEP_MOCK_DIR`),
`tfl_eval/cli.py` (`--systems deep`, `--deep-settings`, `_run_deep_entry`, `deep_by_class`),
`tfl_eval/tests/test_deep_adapter.py` (new, 39 tests incl. parametrized, ~6 s), `tfl_eval/tests/conftest.py` (new:
imports `tfl_deep_agent.tests.guard`, required by `test_network_guard.py`'s tripwire once a tfl_eval test uses the
agent), `tfl_deep_agent/examples/mock/eval/{reg-04,reg-13,cfl-04,ll-17}.json` (new mock replays).

### Behaviour

- `deep` is **opt-in**: `--systems` still defaults to the four pipelines, whose code path (`_run_entry`, `extract`,
  `validate`, mock / live runners) is unchanged. The one shared change: `out_dir` is computed and created before the
  runs instead of after (deep run dirs live under it); its path is the same.
- `--systems deep` runs **every selected manifest entry, whatever its class** (one agent for all classes). The record
  has `system: "deep"` and the entry's class in `task_system`, so `metrics.by_system` has one `deep` row;
  `report.json` adds `deep_by_class` (the deep records summarized per class). `report.md` shows `deep (reg)` etc.
  `--systems reg,deep` runs both, side by side.
- Input: **only the IR's `source_text`** (`runners.deep_task_text`), no IR. `hitl.start_run(..., auto_approve=True,
  dialog=None)`, run dir `<out>/deep_runs/<id>/`; the verdict is read from that run's `result.json`.
- Verdict mapping (`runners.extract_deep`): `published_verdict` (else `verdict_token`) of a `submitted` result: the
  code-side gate's policy token, already in the eval vocabulary (regular / non_regular / partial, cfl / non_cfl /
  null, dcfl / non_dcfl / null, ll / not_ll / uncertain). The model's free-text verdict is never used. A run without
  an accepted report (`incomplete`, `budget_exhausted`, ...) has verdict `None` = inconclusive. LL k is parsed from
  the report's verdict text (`LL(2)`), only for an `ll` token. Confidence = result `confidence`, else
  `confidence_cap` (the deep result has no point estimate; the cap is the code-set ceiling).
- **Class mismatch**: a report whose `language_class` differs from the entry's class is scored wrong
  (`class_mismatch: true`), even when its token happens to be equal. Its token is from another vocabulary anyway.
- Mock mode: `tfl_deep_agent/examples/mock/eval/<entry id>.json`; an entry without one is `skipped` (as the
  pipelines do without fixtures). Each script's `task` equals the entry's `source_text` and `defs.ir` is the
  manifest's IR (pinned by a test).
- `--live` (not run): no script; `TFL_MODEL_OVERRIDE` gets the eval's Haiku default as for the pipelines; the
  agent needs `ANTHROPIC_API_KEY` in the environment (`tfl-deep` never loads `.env`), else every entry is an error
  record. Usage = `{calls, estimated_cost_usd}` from the result's `budget` block, **live only**: mock budgets are
  token estimates of the scripted replies, not money, so mock records carry `usage: None`.
- `--deep-settings JSON` picks the deep settings file (default: `tfl_deep_agent/settings.json`, else the defaults);
  its per-task / per-day budget applies to every deep entry. Without `--systems deep` it is a usage error (exit 2).

### Mock scripts (`examples/mock/eval/`)

Each: main → ir-builder (`validate_ir`, text answer; HITL 1 auto-approved) → one oracle call → `submit_report`
(template `short_verdict`, with `answer`).

| id | verdict | basis (code gate) | trust / cap |
|---|---|---|---|
| reg-04 `(ab)*a \| b` | regular | `regex_to_dfa` + `dfa_vs_ir` | bounded_pass / 0.85 |
| reg-13 `S -> aSb \| ab` | non_regular | `nerode_probe` family `a^n` / `b^n`, values 1..6 | bounded_pass / 0.85 |
| cfl-04 `S -> aSa \| bSb \| eps` | cfl | `input_grammar_cfg` (R2-CFG) | verified / 0.98 |
| ll-17 `S -> aSb \| eps` (LL(1)?) | ll, k = 1 | `ll_table` on the task's grammar, k = 1 | verified / 0.98 |

No dcfl script: a constructive DCFL basis needs `dpda_vs_ir` with a hand-written DPDA, left for later.
`tfl-eval --systems deep` (mock): 74 tasks, ran 4, skipped 70, accuracy 1.0, ~5 s.

### Open

- **The input is only `source_text`, as the plan says, but many eval `source_text`s do not say which class is asked**
  (`{a^n b^n | n >= 0}` is reg-01, dcfl-01 and ll-01). In a live run the agent must guess the class; a wrong guess
  is a `class_mismatch` (scored wrong). Options: keep it (measures the agent on the raw text), or prefix the
  question per class (a wording decision for the user). Not decided here.
- Deep records count in `overall` / `traps` like any system; with `--systems reg,deep` the overall numbers mix
  both. Per-system rows separate them.

### Test runs

- `pytest -q tfl_eval/tests`: 79 passed, 8 skipped (the pre-existing oracle-coverage skips).
- `pytest -q tfl_deep_agent/tests/test_network_guard.py ui_server/tests tfl_eval/tests`: 263 passed, 8 skipped;
  `tfl_deep_agent/tests/test_docs.py`: 19 passed.
- Full `pytest -q -n auto`: 5112 passed, 73 skipped, 3 xfailed, 31 failed. All 31 are real-Docker Lean checks
  (`test_lean_ir.py`, `test_lean_formalize.py`, `test_lean_examples.py`, `test_phase3.py`) failing with the known
  daemon fault `OCI runtime create failed: runc create failed ... read init-p: connection reset by peer` (P0
  Docker notes); none in tfl_eval, ui_server or tfl_deep_agent.

## P8 eval+docs FIX (2026-11-08)

Files: `tfl_eval/cli.py`, `tfl_eval/tests/test_deep_adapter.py`, `tfl_deep_agent/examples/mock/eval/dcfl-04.json`
(new), `tfl_deep_agent/tests/test_critic_ticket50.py` (new, 3 tests), `tfl_deep_agent/tests/test_docs.py`,
`tfl_deep_agent/prompts/main.md` (4 lines), `README.md`, `tfl_deep_agent/README.md`, root `CLAUDE.md` (revert, see
below), `.github/workflows/tests.yml` (new job). Mock only; no API call, no `.env`.

### Fixed

- **Live deep eval has an eval-level cap.** `tfl-eval --systems deep --live` now prints the worst case
  min(entries x `per_task_usd`, `per_day_usd`) to stderr before anything runs. It refuses (exit 2, no run, no
  report) when that is above `--deep-max-usd` (default $1.00, the per-session budget of root CLAUDE.md). With the
  default $5.00 per task even one entry is refused. The user then narrows `--ids`, lowers `budget.per_task_usd` in
  `--deep-settings`, or passes the agreed budget as `--deep-max-usd`. Mock mode ignores the cap. The per-task and
  per-day checks of `BudgetLedger` still apply inside each run.
- **Reused `--out`.** When a deep run dir is already taken, the entry gets the next free `<id>-2`, `<id>-3`, ...
  (`cli._fresh_run_dir`). So a second eval into the same `--out` (or two evals in the same second) runs instead of
  turning every entry into an error record. The first run keeps `<out>/deep_runs/<id>/`, as documented.
- **`.env` key from the pipelines.** In a mixed live run the pipelines' live runners load `.env` into
  `os.environ`. `main` now snapshots `ANTHROPIC_API_KEY` before the pipeline entries and restores it (or removes
  it) before the deep entries, so the deep agent never runs on a key read from `.env`.
- **Root README.** The coder sentence now says that the CLI, TFL Lab and eval runners do not open a sandbox yet.
  The budget bullet names the open $1.28-vs-reserve gap and links to "Budget semantics".
  `test_readme_says_the_runners_open_no_sandbox_while_that_is_true` now checks both READMEs.
  `test_docs_state_the_main_reserve_gap_while_it_is_open` fails when the READMEs' mention of $1.28 and
  `FIT_MAX_TOKENS_ROLES` (no `main`) disagree.
- **Root CLAUDE.md "Allowed actions" reverted to the user's wording.** The P8 docs step had added
  `tfl_deep_agent/`, `tfl_eval/` and docker for the sandbox to that list. That is the user's permission text, so
  the widening is withdrawn and left as a proposal below. The other P8 CLAUDE.md additions (projects, live-run
  rule, settings, commands) stay: they describe, they do not grant.
- **main.md and HITL 1.** `prompts/main.md` now says:
  - formalization goes through `ir-builder` (`task`) or the main agent's own `validate_ir`;
  - an IR built in a workflow step or by a spawned subagent is not reviewed at HITL 1;
  - oracle results count only for the IR approved at HITL 1.

  `ir_builder` stays in `WORKFLOW_ROLES`. Removing it would change `orchestration/workflow.py` behaviour, and the
  binding already keeps such an IR from raising trust.
- **Ticket-50 critic regression.** `tests/test_critic_ticket50.py` drives `hitl.start_run` with a scripted run:
  - the `critic` gets the golden ticket-50 informal proof, whose `word_instances["4"]` is the wrong a^8 b (ab)^5 a^8;
  - it calls `pump_check_cfl` in claim mode, and the result is `refuted` (role `critic` in the ledger);
  - the "not CFL" report is blocked. Cited: R2, `not_verified`, token `null`. Uncited: R1 finds no basis,
    `not_verified`.

  The claim is read from `docs/examples/.../result.json`, so the test also pins that the golden file still carries
  the wrong instance.
- **DCFL replay.** `examples/mock/eval/dcfl-04.json` covers a trap entry, `{a^n b^n c^m | n, m >= 0}`: ir-builder,
  then `dpda_vs_ir` on `dcfl_system/tests/test_dpda.py::SAFE_ANBNCM_DPDA` (already checked there and in
  `test_tools_dcfl.py`), then `submit_report`. Result: dcfl, bounded_pass / 0.85, basis `ev-dpda-vs-ir`.
  `tfl-eval --systems deep` (mock) now runs 5 entries, at least one per class. This supersedes the "No dcfl script"
  line in the eval-adapter section.
- **CI runs the deep tests.** New job `pytest-deep` in `.github/workflows/tests.yml`:
  - it installs `pip install -e ".[deep,dev]"`;
  - it runs `tfl_deep_agent/tests ui_server/tests tfl_eval/tests tests/test_tfl_eval.py` with
    `-m "not slow" -n auto --dist loadfile`.

  It is a separate job, so the main `pytest` job and its install are unchanged. **It has not run on GitHub yet**:
  Linux-only failures, if any, show up on the first push. The real-Docker tests skip there unless the images exist.

### Completeness gaps from the critic list: status

| gap | status |
|---|---|
| Sandbox never opened by `hitl.start_run` / `resume_run` / CLI / TFL Lab / eval | **Open, user decision**: default on, off, or a validated `settings.sandbox.enabled`. Then pass `sandbox=`, call `close_sandbox` in a `finally`, and write the sandbox info into `result.json`. The docs state the current behaviour, and a test flips with it. |
| Adversarial Opus review of the theory text (PLAN non-negotiable 5): `skills/{reg,cfl,dcfl,ll}/SKILL.md`, `theory.md`, `traps.md`, `prompts/*.md` | **Still pending.** Not run in this fix. The existing tests only pin the verbatim quotes against THEORY.md, not the condensed guidance or the traps. P2-P7 were committed without it. **Before the branch is merged, the user must either have it run or accept it as pending.** |
| Main role `max_tokens` vs `reserve_for_report_usd` (worst case $1.28 > $0.30) | **Open, user decision**: per-role `max_tokens` ceilings, or `main` in `FIT_MAX_TOKENS_ROLES` (at least for wrap-up calls). The strict xfail `test_config.py::test_default_ceilings_fit_the_example_budgets` stays until then. Both READMEs document the gap, and a test pins that. |
| Ticket-50 critic-role regression | **Fixed** (above). |
| main.md lists `ir_builder` for `run_workflow` without the HITL 1 note | **Fixed** in main.md (above). Dropping it from `WORKFLOW_ROLES` is left to the user. |
| Sandbox `execute` output stored `not_verified` (no sandbox tool group with ceiling `well_formed`) | **Deferred.** It needs changes in `tools/base.py` and `middleware/evidence.py`, and it matters only once a runner opens the sandbox (first row). |
| Eval coverage: no dcfl replay | **Fixed** (dcfl-04). |
| Eval coverage: no `examples/mock` PDF-intake script | **Deferred.** Scripted tests already cover the PDF path and build the PDF with pypdf (`tests/hitl_scripts.py::pdf_script`, `test_hitl.py::test_pdf_task_goes_through_intake_and_hitl1`). A shipped example would need a binary PDF in `examples/` plus a README row. |
| Class-ambiguous `source_text` in live eval (a wrong class guess is `class_mismatch`) | **Open, user decision**: keep the raw text, or prefix the question per class. |
| `--systems` vs the plan's `--system` | Kept `--systems`, the existing flag. Argparse prefix matching accepts `--system deep`. |
| xdist flaky `ScriptError` (`last_message_contains`) | **Not reproduced.** Four `-n 8` runs over test_orchestration, test_integration_p4_p6, test_hitl, test_e2e_mock and ui_server/tests passed (299 each). Two `-n auto` runs over the whole phase set passed (1679 each). There is no fix without a failing test id; if it shows up again, capture the id with `-rf` first. |
| `**/CLAUDE.md` gitignored: `tfl_deep_agent/CLAUDE.md` and the root CLAUDE.md edits are not committed | **Open, user decision**: force-add, change `.gitignore`, or keep them local. The commit step must include the untracked P8 files: `tfl_eval/tests/conftest.py`, `tfl_eval/tests/test_deep_adapter.py`, `tfl_deep_agent/examples/mock/eval/`, `tfl_deep_agent/tests/test_docs.py`, `tfl_deep_agent/tests/test_critic_ticket50.py`. |
| CI lacks `[deep]` | **Fixed** (new job, above; not yet run on GitHub). |
| TFL Lab deep runs have their own slot gate (2 + 2 processes) | **Open, user decision**: sharing the gate changes `server.py` behaviour. `TFL_MAX_CONCURRENCY` still bounds API calls within each process. |

### Open (for the user)

- The five user decisions in the table: the sandbox default, main `max_tokens`, class-ambiguous eval text,
  CLAUDE.md tracking, and the shared TFL Lab slot gate.
- The Opus theory review: pending. Accept it as pending, or have it run before merge.
- Proposed, not applied: add `tfl_deep_agent/` and `tfl_eval/` to root CLAUDE.md "Allowed actions", and docker for
  the `tfl-sandbox` image. `tfl_deep_agent/CLAUDE.md` already lists what the package may touch outside itself.
- The root CLAUDE.md pricing line (from "P8 docs").

### Test runs

- `pytest -q tfl_eval/tests tfl_deep_agent/tests/test_docs.py`: 108 passed, 8 skipped.
- `pytest -q tfl_deep_agent/tests/test_critic_ticket50.py`: 3 passed.
- `pytest -q -n auto tfl_deep_agent/tests ui_server/tests tfl_eval/tests tests/test_tfl_eval.py`, run twice: 1679
  passed, 8 skipped, 1 xfailed (the budget pin) each time.
- `tfl-eval --systems deep --ids dcfl-04` (mock): ran 1, accuracy 1.0, bounded_pass / 0.85.

## P8 eval+docs — GATE (2026-11-08)

No code or doc changes in this step: it re-checks the P8 FIX state. Mock only; no API call, no `.env`, no key.

### Acceptance

- **`tfl-eval --system deep` passes in mock mode: met.** `python -m tfl_eval.cli --system deep` (argparse prefix of
  `--systems`) ran 74 tasks: 5 ran (reg-04, reg-13, cfl-04, dcfl-04, ll-17), 69 skipped (no mock replay), 0 errors,
  accuracy 1.0, exit 0. `deep_by_class` has one row per class. A second eval into the same `--out` got the fresh
  `<id>-2` run dirs, as the FIX intended. (The `tfl-eval` / `tfl-deep` console scripts are not installed in this
  `.venv`; `python -m tfl_eval.cli` is the same entry point.)
- **Docs match code: met.** `tests/test_docs.py` passes in the full run (CLI flags, settings defaults, roles, exit
  codes, file names, eval section, the sandbox-status and $1.28-reserve statements that flip with the code). Spot
  check: `hitl.py`, `cli.py` and `ui_server/deep_routes.py` still pass no `sandbox=`, as both READMEs say.
- **Completeness gaps: closed or explained**, as in the FIX table above. Nothing new was closed here. Still open for
  the user: the sandbox default for user runs, main `max_tokens` vs the $0.30 reserve (strict xfail stays), the
  class-ambiguous eval `source_text`, CLAUDE.md tracking (`.gitignore:24 **/CLAUDE.md` still ignores both CLAUDE.md
  files), and the shared TFL Lab slot gate. Deferred: the `sandbox` tool group with ceiling `well_formed` (matters
  only once a runner opens the sandbox) and a shipped PDF-intake mock example. **The adversarial Opus review of the
  theory text (PLAN non-negotiable 5) is still pending and not accepted by the user**; it was not run in this gate.

### Test runs

- Full `pytest -q -n auto` from the repo root: 5064 passed, 90 skipped, 3 xfailed, 75 failed. All 75 failures are
  real-Docker Lean checks in `agent_system/`, `cfl_system/` and `dcfl_system/` (`test_lean_ir.py`,
  `test_lean_formalize.py`, `test_lean_examples.py`, `test_phase3.py`, `test_type_check_security.py`): the Docker
  daemon failed during the run (`runc did not terminate successfully: exit status 139`, then `skipped`/`unavailable`
  lean results). Right after the run `docker` answered `Internal Server Error` on `_ping`, and a serial rerun of a
  sample skipped cleanly (Docker unavailable). Same class as the 31 Docker faults recorded in "P8 eval adapter".
  None of them is in `tfl_deep_agent/`, `ui_server/` or `tfl_eval/`, and no file there was touched by P8.
- No failure in `tfl_deep_agent/tests`, `ui_server/tests`, `tfl_eval/tests` or `tests/test_tfl_eval.py` in that run;
  the xdist `ScriptError` flake did not recur.

## Decisions 2026-11-08 — Track C: class ladder + eval questions

User decision (4): class determination goes from the narrowest class to the widest, and the deep eval input names
the class being scored. Files: `prompts/main.md` (new section «Определение класса»), `tfl_eval/runners.py`,
`tfl_eval/cli.py` (docstring + one call), `tfl_eval/tests/test_deep_adapter.py`, `examples/mock/eval/*.json` (the
`task` field only), `tests/test_class_ladder.py` (new), one line each in `README.md` and `tfl_deep_agent/README.md`.
Mock only; no API call, no `.env`, no key.

### main.md: «Определение класса»

- Placed between "Workflow" and "Roles". It applies when the task asks which class the language belongs to, or
  names no class at all. When the task names one class, the single-question workflow is unchanged.
  The "names no class at all" case is my reading. Without it, a bare language would make the agent guess the class.
- Chain, only as THEORY.md states it: REG ⊊ LL(1) (§3.2), LL(k) ⊊ LL(k+1) as languages (§3.2, with its caveat that
  the proof was not read), ⋃ₖ LL(k) ⊊ DCFL (§3.2), DCFL ⊆ CFL (part II §1.6). THEORY.md never states DCFL ⊊ CFL, so
  the prompt says so and tells the agent not to cite properness as a theorem: "CFL but not DCFL" needs both proofs.
  The examples come from §3.2, §3.3 (C) and part II §3.4. `tests/test_class_ladder.py` checks that each one is in
  THEORY.md, and that neither file contains `DCFL ⊊/⊂ CFL`.
- Procedure:
  1. Try REG, then LL(k) with the smallest k first, then DCFL, then CFL.
  2. Formalize per class, and stop at the first class whose membership a tool result supports (the constructive
     basis listed under "Report").
  3. Prove non-membership for every narrower class. A class that is neither proved nor excluded stays open.
  4. If no class has a positive proof, try a non-CFL proof. Otherwise the verdict is `unknown`.
  5. `language_class` is the positive class. The narrower negative proofs are steps, and their ids are not in the
     verdict's `evidence_ids`.
- Step 6 comes from the code, not from theory. `hitl.formalization_binding` binds the verdict to the **last**
  non-superseded HITL 1 record. So a ladder that formalizes REG, then LL, then goes back to a REG proof would have
  its LL verdict judged against the REG IR, and the LL basis would be excluded. The prompt therefore says: call
  `validate_ir` on the verdict's IR again before `submit_report`. An already approved fingerprint gets an
  `unchanged` record without a stop (`FormalizationReviewMiddleware.before_model`; also
  `test_hitl.py::test_hitl1_already_approved_ir_is_not_asked_again`), and that record becomes the last one. The
  binding side is pinned in `test_class_ladder.py`.
- The "Report" section is unchanged here. Track A changes its REG basis. The ladder refers to it ("the constructive
  basis of the class listed under Report") and does not repeat it, so the two edits do not conflict.
- Not done: a code-level ladder (for example, a gate that checks the narrower negative steps). The gate still rates
  only `language_class`, as the prompt says.

### Eval: one question line per class (system deep only)

- `runners.deep_task_text(ir, task_class)` returns `source_text.strip() + "\n" + deep_class_question(task_class,
  ir)`. `task_class` is now required. An unknown class is a `DeepAdapterError`, which becomes an error record.
  Questions:
  - reg: «Вопрос: является ли язык регулярным?»
  - cfl: «Вопрос: является ли язык контекстно-свободным?»
  - dcfl: «Вопрос: является ли язык детерминированным контекстно-свободным (DCFL)?»
  - ll, `task_type: ll_check_grammar` (Format 3): «Вопрос: является ли данная грамматика LL(k) для некоторого k, и
    если да, то каково наименьшее такое k?»
  - every other ll task (`ll_check_language`, `ll_check_grammar_lang`): the same question about the language,
    «является ли язык LL(k)-языком …» (the term of THEORY.md §3.1).
- Why "the smallest k": that is what the manifest's `expected.k` holds. I checked all 18 ll entries. For example,
  ll-09 asks «LL(2)? LL(3)?» and expects 3, and ll-18 expects 2 with `is_ll_k {1: false, 2: true}`. The question
  does not take k from the IR's `k` field, which for ll-09 / ll-15 is the expected answer.
- The line is always appended, even when `source_text` already asks a question (reg-14, dcfl-08..10, ll-02,
  ll-08..18). A repeated question is harmless, and a single rule is easier to check than a heuristic that detects
  questions.
- The four pipelines are unchanged: they run on the IR and never call `deep_task_text`.
- Mock replays: the replay is keyed by role, not by the task text. Each script's `task` field is updated anyway,
  because `test_every_deep_script_belongs_to_a_manifest_task` pins `task == deep_task_text(ir, class)`. `defs.ir` is
  still the manifest IR (its `source_text` without the question).
- Tests: the question for every manifest entry (parametrized over all 74), the reg-01 / dcfl-01 / ll-01 case,
  the live fake's task texts, and the e2e check that `result.json["task"]` is the text with the question.
- `PLAN.md` P8 still says "source_text only". That is the old contract, now superseded by this decision. PLAN.md is
  outside this track, so it was not edited.

### Test runs

- `tfl-eval --systems deep` (mock, `python -m tfl_eval.cli`): 74 tasks, ran 5, skipped 69, errors 0, accuracy 1.0,
  exit 0.
- `pytest tfl_eval/tests tests/test_tfl_eval.py tfl_deep_agent/tests/{test_class_ladder,test_docs,test_p2_core_fix,
  test_agent_fake_model,test_network_guard,test_skeleton}.py`: 369 passed, 8 skipped, 5 failed. All 5 failures come
  from the concurrent budget track and are outside these files: `test_docs` expects per_task 10.00 in the READMEs
  and still reads `FIT_MAX_TOKENS_ROLES`; `test_p2_core_fix` has two max_tokens / wrap-up tests.

## Decisions 2026-11-08 — Track B: budget-derived max_tokens

User decision (2): `max_tokens` of every budgeted call comes from the remaining budget, and the default
`per_task_usd` is 10.00 ("один прогон ограничен ~10$"). This closes the open "P0 fix" question (option (b)).

### Behaviour (`middleware/budget.py`, `config.py`)

- `config.max_tokens_within(model, input_tokens, budget_usd, ceiling)` is the pure arithmetic:
  `min(ceiling, floor((budget − worst-case input cost) / output price))`, 0 when the prompt alone does not fit, never
  a value whose `worst_case_usd` is above the budget (float residue is corrected downwards).
- `BudgetMiddleware(fit_max_tokens=True)` is now the default for every instance, so it covers the main agent, the
  fixed subagents, deepagents' `general`, `spawn_subagent` children, `run_workflow` branches (`orchestration/spawn.py`
  already passed `fit_max_tokens=True`; no change there) and the budgeted summarizer (ceiling 8192).
  `FIT_MAX_TOKENS_ROLES` is removed; `make_middleware` no longer special-cases `lean`.
- Ceiling = what the request would send anyway: `model_settings["max_tokens"]` > the chat model's `max_tokens`
  (`make_chat_model` sets the role ceiling from `config.MAX_TOKENS` / `MAX_TOKENS_PER_ROLE`, clamped) > the
  middleware's fallback > `config.resolve_max_tokens(role)`. The derived cap is sent through
  `request.model_settings["max_tokens"]` only when it is below the ceiling.
- Room: the scope's `available − reserve` in normal mode. When not even the floor fits there, the cap is derived from
  `available` (reserve included) and `admit` enters wrap-up; that is the report call ("the report call may use the
  reserve"). When the floor does not fit `available` either, the call is refused with the existing `REFUSAL_TEXT`; the
  numbers then show `max_tokens` = the floor.
- Subagent / branch scopes: the middleware charges the active scope (`enter_scope`), and `_available` of a child is its
  own allocation bounded by its ancestors, so a child's cap comes from its allocation, never from the task's remainder.
- The cap and the reservation are taken under one ledger lock (`BudgetNode._admit_locked`), so a parallel sibling
  cannot take the money between the two; listener events (`wrap_up`, `refused`) are emitted after the lock is
  released (`_emit_admission`), so a progress listener never runs under the ledger lock.
- Floor `MIN_OUTPUT_TOKENS = 4096` (or the ceiling when that is lower; a request whose own ceiling is below 4096 is
  never lowered further). Why 4096: adaptive thinking (`thinking: {"type": "adaptive"}`, every 5.x model) has no
  `budget_tokens` and no API minimum besides `max_tokens >= 1` (anthropic SDK `ThinkingConfigAdaptiveParam`); the
  manual-thinking minimum `budget_tokens >= 1024 < max_tokens` (`ThinkingConfigEnabledParam`) is the least reasoning
  the API accepts, and thinking counts toward `max_tokens`, so 4096 leaves ~1k-3k thinking plus a tool call. It
  equals `orchestration/spawn.MIN_OUTPUT_TOKENS` (a test pins that).
- `fit_max_tokens=False` stays as an option (fixed ceiling, refused when it does not fit); only a test pinning the old
  arithmetic uses it.

### Settings

- `per_task_usd` 5.00 → 10.00 (`settings.DEFAULTS`, `settings.example.json`, PLAN.md money line, both READMEs).
  `per_day_usd` 20.00, `per_subagent_max_usd` 2.00 and `reserve_for_report_usd` 0.30 are unchanged: the derived cap
  does not force a change. With a derived cap the reserve is no longer required to hold a full 64000-token call.
- Open point for the user (not changed): the report call gets what the reserve pays for, `(0.30 − prompt × $5/MTok)
  / $20/MTok` on Opus, e.g. ~7500 tokens with a 30k-token prompt; with a prompt of 60k+ tokens the reserve alone
  cannot pay for the 4096 floor and the report call is refused (`budget_exhausted`, partial report). A larger
  `reserve_for_report_usd` (e.g. $0.60-1.00 out of $10) would make long runs safer; that is a money setting, so it is
  left to the user. Also: a lowered cap can end a call at `stop_reason: max_tokens`; the partial answer is charged and
  handled like any other reply.
- `docs/architecture.html` still says $5 per task; PLAN.md wins over it (not edited).

### Tests

- `test_config.py`: the strict xfail `test_default_ceilings_fit_the_example_budgets` is replaced by
  `test_max_tokens_within_is_the_largest_cap_that_fits`, `test_default_report_call_fits_the_reserve_with_a_derived_cap`,
  `test_default_lean_and_ad_hoc_calls_fit_one_subagent_allocation`, `test_default_task_budget_gives_the_full_ceiling_at_the_start`.
- `test_budget.py`: `test_opus_main_in_a_1_50_task_is_not_exhausted_after_two_calls` (5 Opus calls in $1.50, never
  into the reserve, the cap shrinks), `test_call_that_cannot_get_the_floor_is_refused_without_calling_the_model`
  (message + numbers with `max_tokens 4096`), `test_the_reserve_stays_for_the_report_call` (call 1 capped by
  `available − reserve`, call 2 enters wrap-up and is capped by `available`, the report tool runs),
  `test_child_scope_caps_come_from_its_own_allocation`, `test_parallel_branches_get_caps_from_their_own_allocations`
  (3 threads), `test_every_hooked_role_derives_max_tokens`, `test_fixed_ceiling_is_refused_when_deriving_is_off`, and
  `test_derived_max_tokens_reaches_the_wire_with_adaptive_thinking` (the `wire` mock: body `max_tokens` = the derived
  cap, `thinking: adaptive`, effort, fallback unchanged). Wrap-up tests now use a reserve that leaves less than the
  Haiku floor above it.
- Tests outside the scope list that pinned the old arithmetic or the $5 default, changed minimally with the same
  intent: `test_agent_integration.py` (over-budget refusal, subagent refusal x2, wrap-up at the reserve, CLI partial
  report, bad-settings reserve 9 → 11), `test_p2_core_fix.py` (prover now derives; wrap-up text test),
  `test_skeleton.py` (example budget), `test_docs.py` (the gap test became `test_docs_state_the_derived_max_tokens`),
  `ui_server/tests/test_deep_routes.py` (four `per_task_usd == 5.0` defaults → 10.0; fixture data left as is).
  `tfl_eval/tests/test_deep_adapter.py` has a comment `min(74 x 5.00, 20.00)`; the value is still $20, not edited
  (another track owns `tfl_eval`).
- `test_models_request_shape.py` passes unchanged (64000 when no budget middleware is in the stack).

## Decisions 2026-11-08 — Track A: REG gate parity

User decision 1 (relayed): the REG skill states the new behaviour in clear text, not as workaround notes.

- **`pump_check_reg` (tools/reg.py, group reg, `max_trust="well_formed"`, 30 s).** Claim mode only: `ir`, `word`
  (w(p) as a pattern in one variable, which is p: `a^p b^p`, `a^(p^2)`), `exit_i` (the exponents the argument
  claims as exits, from 0, 2..6; i = 1 is rejected), `p_values` (default 2, 3, 4 as in VERDICT_POLICY §4
  reg/pumping; 2..6 distinct values in 1..12, for an argument that needs p >= p0). For each p: |w(p)| >= p, then,
  with an oracle, w(p) in L and every split x y z with |xy| <= p, |y| >= 1 pumped at i = 0, 2..6 (pumped words over
  100 characters count as unknown).
- **Trust.** `refuted` for |w(p)| < p (structural, no oracle needed), for w(p) not in L, and for a split on which
  every claimed exit stays in L (exact oracle). Otherwise `well_formed`, never higher: finite p and i are diagnostics
  (§4, R6). An unknown oracle answer never refutes (§4: None is neither membership nor non-membership). A
  refutation from an inexact oracle is `not_verified`. No oracle (reg-07): structure only, `semantic_check: false`,
  `well_formed`, the same as `agent_system.lib.claim_verifier.verify_pumping_claim` without an oracle.
- **Deviation from the computed task text.** The task said "any split that stays in L for all checked i -> refuted".
  VERDICT_POLICY §4 says survival at several i does not prove survival at all i, and `refuted` needs a concrete
  violated premise. So the tool asks for the claimed exits and refutes only the concrete false claim "this split
  leaves L at one of these i". A split that survives every tried i while no exit was claimed for it is impossible
  here, because the claim covers every split.
- **Oracle.** `oracle_for("reg", ir)`; for kind `natural` with no REG oracle, the exponent-notation oracle of
  `cfl_system.lib.exponent_pattern` (reg-01, reg-05, reg-10 parse; reg-07, reg-02, reg-06, reg-11 do not). This
  fallback lives in `pump_check_reg` only, not in `tools/words.oracle_for`, so no other tool changes. It can never
  raise the trust above `well_formed`.
- **`_eval_exponent`** takes a constant power 0..3 (`n^2`, `n**2`), so `a^(p^2)` instantiates; `n**9**9` is still
  `invalid_input`. Used by `nerode_probe` families too.
- **`regex_to_dfa(ir=...)`.** New third source: the tool reads the regex (back-references -> `unsupported`) or the
  right-/left-linear grammar from the validated IR, builds the DFA and compares it with the IR oracle on all words up
  to a length bound (2000 words; a disagreement -> `unsupported`, a tool/dialect mismatch, never evidence). Data:
  `source: "ir.language_spec"`, `task_definition: true`, `cls: "reg"`, `ir_fingerprint`, `oracle_cross_check`. With
  `regex`/`grammar` the data say `task_definition: false`.
- **Gate (agent.py `_classify`, REG branch).** `regex_to_dfa` counts as constructive `verified` (§1 "регулярность
  заданного regex/DFA", cap 0.98) only for the tool's own IR-mode record and only when the verdict is bound to a
  named IR (`ir_named`: the HITL 1-approved fingerprint, or the report's `ir_fingerprint`); the usual fingerprint
  filter then excludes another IR's record. Without a named IR the gate writes a note and does not count it. This is
  stricter than `lean_check`, which counts without a named IR (open point). `pump_check_reg` with `cls: "reg"` and
  `mode: "claim"` counts as destructive, clamped to `well_formed`.
- **Skills.** skills/reg/SKILL.md and traps.md now describe both tools; the reg-07 route reaches `non_regular` at
  <= 0.55 through `pump_check_reg`. The §4 quotes in theory.md are unchanged. prompts/main.md belongs to another track:
  its REG basis lines still name only `dfa_vs_ir` / `nerode_probe` and say `regex_to_dfa` never raises the verdict;
  the replacement wording is in this track's open issues.
- **Tests.** New `tests/test_tools_pump_reg.py` (35 tests on the eval IRs: reg-07 / reg-13 / reg-01 reach
  `non_regular` at 0.55 through the gate and through `evaluate_report`; wrong claims refuted; reg-04 reaches
  `regular` at `verified` 0.98; a model-written regex and forged records give no basis). `tests/test_skill_reg.py`:
  the "never called" test replaced by tests of the new text and of the reg-07 call.
- **Run.** `pytest -n 4 tfl_deep_agent/tests ui_server/tests/test_deep_routes.py tfl_eval/tests`: 1657 passed,
  8 skipped.

## Decisions 2026-11-08 — Integration of tracks A, B, C

Mock only; no API call, no `.env`, no key; nothing committed.

- **prompts/main.md (REG basis, Track A's wording).** The constructive list now reads "reg: `dfa_vs_ir`, or
  `regex_to_dfa` with `ir` (the task's own regex or right-/left-linear grammar, read from the IR by the tool:
  `verified`, counted only when the verdict names the IR, i.e. the one approved at HITL 1 or the report's
  `ir_fingerprint`)". The destructive sentence names `nerode_probe` or `pump_check_reg` for reg (a `pump_check_reg`
  claim at most `well_formed`, non_regular <= 0.55), `nerode_probe` for dcfl (at most `well_formed`), `pump_check_cfl`
  in claim mode for cfl/dcfl. The artifact sentence now says "`regex_to_dfa` with `regex` or `grammar`". Workflow
  step 4 lists `pump_check_reg` for REG pumped words. These match `agent._classify` (REG branch) and
  skills/reg/SKILL.md §5 (the evidence table) exactly. prompts/critic.md: "for REG, check a pumping argument with `pump_check_reg`
  (|xy| <= p), not `pump_check_cfl`" (the critic has the reg group through `_ORACLE_GROUPS`).
- **Mock scripts.** `examples/mock/reg_regex_regular.json` and `examples/mock/eval/reg-04.json` now follow the skill's
  route for a task given by its own regex: `regex_to_dfa(ir={"$def": "ir"})` instead of `regex_to_dfa(regex=...)`
  (a model-written copy of the pattern, which the gate never counts), and the report carries
  `ir_fingerprint: ir-a8bf22e6dd74` (both scripts use the same IR). The verdict is now `verified`, cap 0.98, basis
  `ev-dfa`; `dfa_vs_ir` stays as the weaker check of the same side. Pinned in
  `test_agent_fake_model.py::test_reg_script_reaches_submit_report_with_real_tool_evidence` (was `bounded_pass` /
  0.85 / basis `ev-dfa-vs-ir`) and `tfl_eval/tests/test_deep_adapter.py` (reg-04 `verified` 0.98, basis `ev-dfa`).
  The descriptions of both scripts say so. reg-13 (`nerode_probe`, 0.85) is unchanged: it is already the
  strongest destructive route. `tests/hitl_scripts.py` is LL-only and needs no change. No script pins a budget
  number, so Track B's derived `max_tokens` needs no script change (the suite is green with it).
- **Load-sensitive test.** `test_reports.py::test_cfl_mock_run_renders_end_to_end`: `grammar_vs_ir` on this IR takes
  0.04 s (1093 words), so its internal 40 s `Deadline` cannot be the cause, and a deadline stop would keep
  `bounded_pass` anyway. What can lower step 3 under full parallel load is the thread runner: its wall clock under
  contention, or `ToolBusy` when other tests in the same xdist worker left more than `MAX_ABANDONED_THREADS` (8)
  timed-out threads running. Neither is what the test checks, so the test runs thread-mode tools directly
  (`monkeypatch` of `base.run_with_timeout`, process mode untouched; each tool's own `Deadline` still applies),
  asserts that `grammar_vs_ir` and `cyk` went through that path, and asserts that no tool call failed, with the
  errors in the message. Every trust assertion is unchanged. Not reproduced locally: the reason is inferred from the
  code.
- **Stale text.** `test_skill_ll.py::test_skill_does_not_recommend_unimplemented_tools` no longer lists
  `pump_check_reg` as unimplemented; it now asserts that no LL file sends a task to that REG tool. PLAN.md P8 says
  "source_text plus one question line for the entry's class" (decision 4). Root `CLAUDE.md`: `per_task_usd` 10.00 and
  the derived `max_tokens`. `tfl_deep_agent/README.md` "Verdict basis" states the REG rules and that
  `reg_regex_regular.json` takes the `regex_to_dfa(ir=...)` route. `docs/TOOL_INVENTORY.md` was already updated by
  Track A.
- **Still open (not changed here):** the adversarial Opus review of the new theory-adjacent text (skills/reg,
  main.md «Определение класса» and its REG basis lines) before commit; the `natural`-IR exponent oracle living only in
  `pump_check_reg`; `regex_to_dfa(ir)` needing a named IR while `lean_check` does not; the reserve size
  (`reserve_for_report_usd` 0.30 cannot pay the 4096 floor on Opus with a prompt of ~56k+ tokens);
  `docs/architecture.html` still says $5.

## Decisions 2026-11-08 — FIX: REG gate binding, pump semantics, budget wrap-up

Fix stage over the ten review findings of tracks A, B and C. Scope: `tools/reg.py`, `agent.py` (REG branch of
`_classify` / `verdict_gate`), `middleware/budget.py`, the REG skill / traps / main prompt / README / inventory
text, and their tests. No pipeline package, `hitl.py` or `evidence/` file was touched.

**F1/F4 (major), `regex_to_dfa(ir=...)` as a `verified` basis was circular.** The model writes the IR. When it encodes a
natural-language task as a regex of its own, the "task's definition" DFA is the model's answer. Decision:
`verified` (cap 0.98) only when both of these hold:
- a human approved that IR at HITL 1 (`formalization.status == "approved"` and not `auto`);
- the definition occurs in the IR's `source_text`. The new data field `definition_in_source_text` holds this. It
  is computed by the tool with whitespace stripped: the regex, or every grammar rule with arrows normalised, must be
  a substring.

Otherwise the record counts like `dfa_vs_ir`: `bounded_pass` (0.85), and only when its oracle cross-check ran on at
least one word. The gate states which condition failed: HITL 1 off, auto-approved, definition not in the source
text, or no IR named. Rejected alternative: matching against the run's raw task text. It needs plumbing through
`hitl.py`, `render.py` and `policy.py`, which are out of scope. A substring check against the raw task is also fooled
by tasks like "L1 = a*b*, L2 = {aⁿbⁿ}". The human approval is the real binding; the source-text check only filters
out an IR whose regex the model invented.

**F7 (major), unclosed pumping split reported as `refuted`.** VERDICT_POLICY §4 says that survival at several i
proves nothing for all i, and that `refuted` needs a concrete violation of a stated premise. A split on which every
claimed exit stays in L may have an exit that depends on p or |y|, as in reg-10 (w = aᵖbᵖ⁺ᵖ!, i = p!/|y|+1).
`pump_check_reg` now returns `refuted` only for |w(p)| < p or w(p) ∉ L with an exact oracle. Unclosed splits are
`not_verified` (no basis), listed in `unclosed_splits`, and the reason says "not a refutation".

**F5, `p_values` could skip the policy p.** p = 2, 3, 4 are now always checked. `p_values` only adds extra p.
`p_min` shifts the base set to p_min..p_min+2 for arguments stated only for large p.

**F9, the exponent oracle misparses comma lists.** `cfl_system.lib.exponent_pattern` reads `n,m ≥ 1` wrongly.
That package is read-only, so this is to report to its owner. `_exponent_oracle_doubt` marks the fallback oracle
approximate (`exact=False`, refutations become `not_verified`) in two cases:
- the condition contains a comma-separated list;
- the parse accepts no word up to length 8.

**F2, F8, F10:** fixed as text and test changes (SKILL.md, traps.md, main.md, README, TOOL_INVENTORY, the reg mock
descriptions, the fake-model, adapter and skill tests). reg-04 and the reg mock now end `bounded_pass` 0.85,
because HITL 1 is off in mock runs.

**F3/F6 (budget), the fixed reserve did not cover a long report prompt, and the first wrap-up call could spend
everything.** `BudgetMiddleware.derive` now returns `(estimate, enter_wrap_up)`:
- **Normal mode.** The cap is the lower of two limits:
  - what is free above the reserve;
  - `_cap_leaving_next`: the largest c such that this call plus one more call at the floor, with a prompt grown
    by c + `NEXT_CALL_MARGIN_TOKENS` (2048), fit in the remainder. It is found by bisection, because
    `worst_case_usd` steps up for long prompts.

  When that cap is below the floor, the call enters wrap-up through the new `_admit_locked(enter_wrap_up=True)`.
- **Wrap-up.** Each call is capped by `_cap_leaving_next` again. Only when not even that leaves the floor does the
  call get everything left.

New tests:
- `test_two_wrap_up_turns_still_reach_the_report`: the first wrap-up turn is not the report and uses its whole cap,
  and the report is still admitted.
- `test_a_long_prompt_still_leaves_the_report_call_payable`: an 80k-token prompt on Opus.

`test_config.py` now records that the $0.30 reserve alone does not pay an 80k-token Opus prompt. The pinned caps in
`test_budget.py` use the helpers `normal_cap` / `leave_next`. Normal calls get slightly lower caps than before
(about 8k instead of 12k on a $0.25 branch). This is the price of keeping the report payable.

**Open points**
- `hitl.report_review_text` builds its binding without `auto`. In an `--auto-approve` run the HITL 2 card text may
  show the REG task-definition record as `verified` while the final result shows `bounded_pass`. The final result
  and the draft use `run_binding` and are correct. The fix belongs in `hitl.py` (another scope).
- The CFL analogue is still open: cfl-04 reaches `verified` 0.98 under `--auto-approve` through the task's own
  grammar. This is the same circularity, out of this scope; it needs the same human-approval and source-text rule.
- The `cfl_system.lib.exponent_pattern` comma-list bug should go to the pipeline owner. Here it is only guarded
  against.
- The new theory-adjacent text needs the Opus adversarial review before commit:
  - the pumping paragraphs in `skills/reg/SKILL.md` and `traps.md`;
  - the reg-10 and (a⁵)* examples (THEORY §2.0).

## Decisions 2026-11-08 — GATE: full suite after tracks A–C and FIX

**Fix (tests only): `TFL_MODEL_OVERRIDE` leaked between tests.**
`test_reports.py::test_cfl_mock_run_renders_end_to_end` failed in 2 of 3 full `-n 4` runs. The report it renders
had no `claude-opus-5-5` row, but the test passed when run alone. The cause was in `tfl_eval/tests/test_deep_adapter.py`.
Its `--live` tests (fake `run_deep`) call `monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)` and then
`cli.main([... "--live"])`. The CLI's `set_live_model_env` then writes `claude-haiku-5-5` into `os.environ`. When the
variable was absent beforehand, pytest's `delitem` records no undo, so the write outlived the test. Every later
deep-agent test in that xdist worker then ran on Haiku. To reproduce: run
`test_live_deep_within_the_cap_runs` and then the report test in one process.

The fix is an autouse fixture `_undo_live_model_override` in `test_deep_adapter.py`. It calls `setenv` and then
`delenv`, the same pattern the older `test_live_runs_every_entry_...` used inline, so teardown removes the variable.
No assertion was changed. `cli.main` itself still sets the variable for the process, because the four pipelines'
live runners read it there. Making the CLI restore it would be a behaviour change and is not done here.

**Test runs.**
- Before the fix (full suite, `-n 4`): 25 failed / 5286 passed, then 43 failed. After the fix: 35 failed / 5278
  passed / 73 skipped.
- Every remaining failure is a Docker-Lean test in agent_system, cfl_system or dcfl_system: `test_lean_ir`,
  `test_lean_examples`, `test_lean_formalize`, `test_phase3`, `test_type_check_security`, and
  `test_constraint_semantics`, which evaluates through Lean. The errors are
  `OCI runtime create/start failed: runc did not terminate successfully: exit status 139` and
  `read init-p: connection reset by peer`. A container that dies early gives an empty result, so the test reports a
  missing `sorry` warning or `len({}) == 0`. The set of failing tests changes from run to run. A serial run of the
  Lean files fails too. Each failure passes when retried alone, at most 8 tries. No pipeline file was changed.
  ll_system and every non-Docker test in the four pipelines are green.
- `tfl_eval/tests` and `tfl_deep_agent/tests/test_reports.py` serially: 211 passed, 8 skipped.
  `tfl_deep_agent` + `tfl_eval` + `ui_server` with `-n 4`: 1806 passed, 8 skipped.
- `python -m tfl_eval.cli --systems deep` (mock): 5 scripted tasks ran (cfl-04, dcfl-04, ll-17, reg-04, reg-13) and
  69 were skipped (no mock script), with 0 errors. All 5 are correct, Brier 0.0137, no confident wrong answers.

## Decisions 2026-11-08 — pump_check_reg refutation fix

**A split in L at every claimed exit is `refuted`** (supersedes the F7 decision above). `exit_i` is the argument's
own claim: on every split of w(p) with |xy| ≤ p, |y| ≥ 1 some i in `exit_i` gives x yⁱ z ∉ L. When an exact oracle
puts x yⁱ z in L for every i in `exit_i` on one split, that stated claim is false at that p: a concrete false
premise (VERDICT_POLICY §1 `refuted`, "детерминированное свидетельство против"; §4 reg/pumping "конкретного нарушения
заявленной предпосылки"). This matches `pump_check_cfl` claim mode (a word the argument declares outside L is in L,
exact oracle only → `refuted`). Every i in `exit_i` is among the tried i, so the old reason ("the tried i are not all
i", §4 survival) did not apply to the stated claim; it was removed from the module and tool docstrings, the result
reason, SKILL.md, traps.md, README and TOOL_INVENTORY. Unknown answers leave the split undecided and an approximate
oracle gives `not_verified`; neither refutes. `data.unclosed_splits` is replaced by `data.exit_failures` (also in
`refutations`). An exit that depends on p or |y| (reg-10, i = p!/|y| + 1) cannot be stated in `exit_i`; fixed exits
for such an argument are now refuted, and the skill says to use a fixed-exit word or accept that the tool cannot
check the argument (Nerode family on a predicate IR). Gate: `_classify` already passes `refuted` through
`clamp_trust`, so a cited refuted `pump_check_reg` blocks `non_regular` by R2 like any cited result of the verdict's
own side; no gate code changed (new test `test_a_cited_exit_refutation_blocks_non_regular_like_r2`).

**`checked_p` described exactly.** SKILL.md, traps.md, the `p_values` / `p_min` field descriptions and
TOOL_INVENTORY now say: p = 2, 3, 4 when `p_min` ≤ 2, or p_min..p_min+2 when `p_min` > 2, plus every `p_values`
entry ≥ `p_min` (so p = 1 with the default `p_min` = 1). The VERDICT_POLICY §4 citation stays only on the default
{2, 3, 4}; shifting up is justified by "a larger p is also a pumping length". A p the argument does not cover can
now refute it, so the docs tell the model to pass `p_min`.

**SKILL.md §5 row.** When the oracle answers "unknown" for w(p) at every p (e.g. beyond the exponent oracle's length
limit), no split is checked and the result is still `well_formed` (VERDICT_POLICY §4: unknown words are skipped).
Two rows replace the old "unclosed split" row: exact oracle → `refuted`, approximate → `not_verified`.

**Tests** (`test_tools_pump_reg.py`): a* with a^p and exit [2] → `refuted`; the same with an approximate oracle →
`not_verified`, with unknown answers at the claimed exit → `well_formed`; reg-04 `(ab)^p a` exit [0], reg-10 fixed
exits and the (aaa)* policy-p case are now `refuted`; the existing refuted cases and reg-07 / reg-13 / reg-01 at
`non_regular` ≤ 0.55 through the gate are unchanged.

**Open point.** The changed pumping paragraphs in SKILL.md and traps.md are theory-adjacent text and need the Opus
adversarial review before commit.

## 2026-11-08: report reserve $1.00, at most a quarter of the task

User decisions: `reserve_for_report_usd` 0.30 → 1.00 (0.30 could not pay a 4096-token Opus report call once the
prompt passed ~56k tokens; 1.00 pays it up to ~180k). Because a $1 live run would then have had no room at all,
the user chose to clamp: the task ledger keeps `settings.report_reserve(per_task, reserve)` =
`min(reserve_for_report_usd, per_task_usd / 4)`, the rule `SubagentBudgetMiddleware` already uses for a child's
reserve. `BudgetSettings.report_reserve_usd` feeds `BudgetLedger.for_task`, both ledgers in `hitl.py`, the dict path
in `agent.py` (and the budget line of the main prompt) and `/api/deep/estimate`. The validator "reserve must be
smaller than per_task_usd" is gone (the clamp makes any non-negative reserve safe). Tests:
`test_budget.py::test_the_task_ledger_keeps_at_most_a_quarter_of_the_task_as_reserve`; `test_config.py` now asserts
that the default reserve pays a floor-sized Opus call with an 80k-token prompt and not with a 190k one.

## 2026-11-08: one .env for every pipeline

The repo had two `.env` files: `agent_system/lib/llm_client._load_env` read `agent_system/.env` (REG, the
formalize entry and every `LLMRunner`), while the cfl / dcfl / ll orchestrators load the root `.env`. With different
keys in them, REG billed a different organization. User decision: only the root `.env` is used, so `_load_env` now
reads `<repo>/.env` (`parents[2]`); environment variables still win. This is the one edit to a pipeline package on
this branch, made on the user's explicit request; it lands in main with the branch PR. `agent_system/.env` is no
longer read and can be deleted by the user. `tfl-deep` and TFL Lab still read no `.env` at all.


## 2026-11-08: review modes, evidence-id check, pending card

User decisions, relayed by the lead agent; this round touched `tools/report.py`, `hitl.py`, `cli.py`, `agent.py`
(two lines), `ui_server/deep_routes.py`, their tests and the docs.

**Evidence ids in `submit_report`.** A Haiku run cited tool names (`regex_to_dfa`) as evidence ids. The tool used to
accept them, and only the gate / renderer marked them unknown. Now `make_submit_report_tool(evidence)` checks every
cited id: `evidence_ids`, `claims[].evidence_ids`, `steps[].evidence_ids` and `lean.evidence_ids`. The check runs
against the run's ledger (`agent._build_agent` passes `evidence_ledger`; without one, the ledger of an enclosing
`use_evidence` block is used). An id that is not in it makes the call a normal tool error: a `ToolException`
through `handle_tool_error`, so the message has status `error` and no artifact. The report gate does not end the
run, nothing is rendered, and `report_interrupt_config`'s `when` skips HITL 2 for such a report.

The error JSON (`error_kind: unknown_evidence_ids`) lists:
- the unknown ids, where each is cited, and a "tool NAME, not a call id" hint;
- the valid ids grouped by tool, with trust, IR fingerprint and the subagent role, the newest 60 (`MAX_LISTED_IDS`).

The model then resubmits. Code never maps a name to an id. Ordinary run limits bound a model that never fixes its
ids: `submit_report` is exempt from the tool-call limit but not from the model-call limit, so such a run ends
`incomplete` / `budget_exhausted` with a partial report. `test_a_model_that_keeps_citing_names_ends_with_a_partial_report`
covers this. The gate's and the renderer's unknown-id handling stays as the second line of defence. The
fabricated-id tests now rate or render a fabricated copy of an accepted report over the honest run's ledger
(`test_hitl`, `test_evidence`, `test_reports`). A human edit at HITL 2 that cites an unknown id is refused as a
`DecisionError` before resuming, by the CLI, the dialog and `/resume` (`_prevalidate` loads `evidence.jsonl`).
Otherwise the edit would be lost to the tool's refusal.

**Review modes.** `run.json` now has `review_mode` (`all` | `formalization` | `auto`) and `initial_review_mode`.
`auto_approve` is kept as `review_mode == "auto"` for old readers, and `hitl.review_mode_of` reads runs written
before the field existed. `_segment` auto-approves a stop when `auto_approves(mode, point)`:
- `auto`: every point;
- `formalization`: only `report`, never HITL 1 or `execute`;
- `all`: none.

An auto stop is the old `--auto-approve` path: `Decision("approve", source="auto")`, recorded as `auto_approved`.
`run_binding` now takes `auto` from the review history, not from the run's mode. An approved IR is human-approved
only if a non-`auto` review approved or edited that fingerprint, so a formalization approved by a human before
`approve_rest` stays human. `formalization_note` says "auto-approved (no human review)". `AUTO_APPROVED_LABEL` is
unchanged, because the renderer and the eval adapter match it.

`resume_run(..., approve_rest=True)` (approve only) does three things:
- marks the review `approve_rest: true`;
- sets `review_mode: auto`;
- stores `approve_rest: {at, point, interrupt_id, ...}` before the segment runs, so a later process keeps it.

CLI changes:
- new `--review-mode`;
- `--auto-approve` stays its alias, and the two conflict when the modes differ;
- `--resume ... --approve-rest`;
- `--review-mode` is refused on `--resume`, since the run keeps its mode.

Routes:
- `review_mode` on create, allowed for live runs too, which still need `confirm_spend`;
- `approve_rest` on resume;
- `review_mode` in the summary and the detail view (the agent's value, else the lab record);
- `review_mode` stored in `lab.json`.

**Pending card.** `pending_point` was the bare point name, and only under `run` in the detail response. A client
reading `data.pending_point` got `null` while the run was `awaiting_human`. In the code paths I tested, `run.pending_point` itself was
never null for a waiting run, but a `run.json` read that fails (a Windows sharing violation, a partial read) would
also have nulled it. Now `hitl._write_awaiting` stores `pending.card` (`hitl.pending_card`: point, title, up to 4
summary lines under 90 characters, `details_md` = the request's sections without the CLI resume hint, `editable`).
`deep_routes.pending_card` serves it in the summary, the runs list and at the top level of the detail response. Its
fallbacks are: built from the request (old runs), then the card cached at settle time, then a minimal card from the
`hitl_awaiting` event plus `hitl.md`. The formalization summary is made by code from `ir_checks`: class, language (a
regex compactly, else the "Язык по IR" line), and the shortest 5 oracle-labelled words in and out of L. The report
summary is the verdict, the draft's trust and cap, and the step / claim counts.

Tests:
- `test_hitl.py`: names refused then ids verified (reg (ab)*a|b, human-approved IR); fabricated ids refused without
  a stop; the loop limit; the human edit with an unknown id; the gate still lowering made-up ids; review-mode
  resolution; `formalization` mode; auto-approved formalization keeps `bounded_pass`; `approve_rest`; the CLI flags;
  the cards.
- `test_deep_routes.py`: `review_mode` on create; `approve_rest`; the card fallbacks; the card shape in the two
  existing flows.

Renderer-text assertions in `test_reports.py` / `test_hitl.py` depend on `reports/render.py`, which another agent
was editing during this round.

## 2026-11-08: report as a solution document

The user: the report must read as a solution, like the pipelines' result.md (why and how), not "доказано, вот
ссылка". `reports/render.py`, the templates, `SKILL.md`, the Report section of `prompts/main.md` and the three mock
reports were rewritten; trust, caps and the gate are unchanged (the renderer still never raises trust).

Layout (every template): `# title`, `## Условие`, `## Ответ`, `## Решение`, `## Заключение`, the Lean section, then
the code-made appendix `## Проверка` (`EVIDENCE_BLOCK_TITLE`, was "Свидетельства, модели и расходы").
- Answer box: `> [!theorem] Ответ: <token in words>` (LL: the model's verdict text; "Ответ (не подтверждён)" at
  `not_verified`; "Ответ не установлен" plus the model's hypothesis when the published token is not decisive), then
  `Класс · Уверенность: N% · <review in a few words>`. Below `verified` a warning callout stands next to the answer
  (`bounded_pass`: "проверен машинно не полностью"; `well_formed`: "НЕ прошло проверку по существу"; lower: the
  pipelines' "Доказательство НЕ прошло независимую проверку"), with the gate notes in short Russian (`_GATE_GIST`).
  `verified` gets a small `[!success]`.
- Решение: «Идея.» (hypothesis; the summary when a conclusion exists) and «Метод.», the THEORY.md citations, the
  construction (the model's `construction` through `safe_block`, which keeps its ``` blocks and closes an unclosed
  one, plus the objects of the cited ok, non-refuted results: the DFA transition table from `data.dfa`, the grammar
  from the call's args / IR, the LL table from `data.parse_table`; a grammar the model already wrote is not printed
  twice), the key claims, the steps `**Шаг k. title**` / claim / `*Обоснование:*`. `exam_solution` and
  `short_verdict` print the steps as a numbered list (`short_verdict` now prints them too).
- Marks: a step or claim whose cited results passed gets "(проверено: tool ✓)" / "(проверено выборочно: …)" /
  "(корректно оформлено: …)"; a refuted citation "(опровергнуто: tool ✗)"; nothing otherwise. No per-step trust
  badges in the text; the unmarked cases (model words only, unknown ids, refuted ids) are listed in «Проверка».
  The marks carry the invisible sentinel `MARK` (U+2063) that `safe_text` strips from model text, so only code
  marks get the `.ev-mark` style; the sentinel is removed from result.md.
- Проверка: one intro line, the basis line (trust, cap, basis ids, token, direction, verdict text), one table
  `инструмент | что проверено | результат | в решении` (bookkeeping calls are left out unless cited), the gate notes
  as bullets, the IR / formalization / review lines, one run line (id, template, status, spend, hash chain), and in
  `<details>` the full call log, the models and roles tables and the budget line. `<details>` is converted only in
  the appendix (`markdown_to_html(details=True)`); in model text it stays escaped.
- HTML: max-width 780px, 15px text, tables scroll inside their box, KaTeX 0.16.11 only when the body has math (the
  `.katex-display` scroll rule ships with the KaTeX block), `*Label:*` at line start is the only italics (model math
  such as `(ab)*a` keeps its stars), display math is not split by `<br>`. Light and dark via CSS variables.

Guidance: steps need a title, a claim and a real justification; `construction` holds the actual object (DFA as a
Markdown table, grammar in a ``` block); Russian text; cite the exact tool-call ids, never tool names (the live
reg-04 run cited `regex_to_dfa` and lost a level). Not done here (owned by another agent): the docstring of
`tools/report.py` `list_report_templates` still names the old block "Свидетельства, модели и расходы".

Tests: `test_reports.py` pins the new order, the answer box, the callouts, the marks and their spoof-proofing, the
construction objects, `safe_block`, `<details>` only in the appendix, KaTeX; `test_hitl.py` no longer needs the
old "↳" step line.

## 2026-11-08: second live smoke run (df4a3b9626cb, Haiku, $0.0106)

- Evidence ids: the report cited `toolu_...` ids only, no unknown ids, so the evidence-id check works.
- Trust was still `bounded_pass`. The ir-builder wrote the task's regex as `((ab)*a|b)`, the task text has
  `(ab)*a | b`, and `tools/reg._definition_in_source_text` needed the pattern as a substring with whitespace
  dropped, so `definition_in_source_text` was false. Fix: `_strip_outer_parens` drops parentheses that enclose the
  whole pattern (one group closing at the last character, not `(?`, escapes skipped) before the substring check.
  They change no word, so the binding rule (the definition is the task's own) is unchanged. Tests in
  `test_tools_reg.py`. The run's own record stays as it was.
- With review mode `formalization` the report is auto-approved, and the label said `--auto-approve`, which the user
  did not pass. `hitl.AUTO_APPROVED_LABEL`, the renderer's label and the gate note now say "review mode or
  `--auto-approve`".

## 2026-11-08: state diagrams in the report (user request)

- `reports/diagrams.py`: code builds Graphviz DOT for the automata the cited tool results carry (the DFA of
  `regex_to_dfa` / `dfa_vs_ir` / `run_dfa` / `nerode_probe`, the DPDA of `dpda_vs_ir` / `dpda_run`) and renders it
  with the local `dot -Tsvg` (Graphviz on PATH; checked once per process, 15 s timeout). Start arrow, double circles
  for accepting states, a dashed trap, parallel edges merged (`a, b`; DPDA `a, Z / AZ`). Up to 30 states / 120
  edges, else only the table.
- The Markdown gets a ```` ```dot ```` block under the transition table; the HTML draws it as inline SVG only when
  the fence text is a DOT source the renderer itself made (`_Ctx.diagrams`), so a `dot` fence in model text stays
  code (Graphviz HTML labels can read local files). The SVG is rejected if it holds script / foreignObject / image /
  on* / javascript:, and black / white become `currentColor` / none, so it reads in light and dark pages.
- Without `dot` the HTML shows the DOT source as code; nothing else changes. Tests: `tests/test_diagrams.py`.

## 2026-11-09: live round on Haiku over CFL, DCFL, LL and a PDF ($0.0494)

Runs 3a223c40e8f4 (cfl-01), 7dbf7558860b (dcfl-02), 93e109bed6fe (ll-03), fa30fb50ab6b (reg-03 from an image-only PDF),
7ef71d330184 (cfl-04). All five verdicts match `docs/EVAL_SET.md`. Trust: well_formed for non_cfl, bounded_pass for the
others, as the policy says for these bases. Nobody called `lean_statement` / `lean_check`: Lean is the agent's own choice.

Fixed after reading the reports:
- A malformed Haiku call put `</hypothesis><parameter name="construction">…` inside `hypothesis`, so `construction` was
  empty and the markup was printed. `submit_report` now refuses tool-call markup in any field, and the model resubmits.
- A file task printed the intake format (`## Tasks`, `## Uncertain readings`) under «Условие».
  `render._task_statement` keeps the text and any uncertain reading.
- `ll_table` with `max_k` keeps its table under `result_for_k`, so the code-made LL table never appeared in a live run.
- The answer box said «Класс: CFL» under "not context-free", and "без проверки человеком" when a human approved the
  formalization and only the report was auto-approved.

Still open: no live reject at HITL 2 (the report was approved), no live `spawn_subagent` / `run_workflow`, no step of the
five reports cites an evidence id (Haiku cites at report level only, so the "(проверено: tool ✓)" marks do not appear).

## 2026-11-09: follow-up messages, the Lean button, the budget stop (user request)

A finished run takes more messages in the same conversation, and a task that runs out of money asks for more instead of
ending. Entry points: `hitl.send_followup`, `--resume RUN_DIR --message TEXT|@FILE`, `POST /api/deep/runs/<id>/message`
`{text | preset: "lean", confirm_spend}`, and the budget stop (`budget_stop.request_budget_extension`, point `budget`, decisions
`edit {"add_usd": X}` = `extended` / `approve` = `declined`). Tests: `tests/test_followup.py`, the follow-up blocks of
`ui_server/tests/test_deep_routes.py` and `test_deep_frontend.py`. Mock script for the UI: `examples/mock/reg_followup.json`.

Decisions and deviations:
- The extended limit lives in `run["budget_limit_usd"]`, not in `run["settings"]["budget"]["per_task_usd"]`: a task limit above
  `per_day_usd` would fail `validate_settings`. `_budget_ledger` applies it. The settings snapshot stays what the run started with.
- `run["limits"]` (model-call and subagent counters) start again for each follow-up. Money stays capped by the ledger.
- A stop in progress is given back to the call limit (`RunLimits.give_back_model_call`): the node re-executes on resume.
- `start_run(budget_stop=None)` means `not auto_approve`. A `--auto-approve` run has no one to ask and keeps the old wrap-up. Review mode
  `auto` chosen in the UI is about reviews only: money still asks. `approve_rest` is refused at the budget stop and does not
  remove a later one.
- "Finish with what is left" sets `budget_declined`, so a new process does not ask again; a new message clears it.
- An extension too small for the next call goes on to wrap-up, with no second question in that step.
- No stop when the day's remainder is what binds in a run's own call: more task budget buys nothing (a follow-up of an accepted report is the exception, CA-015 day case below). Live extensions are checked against the
  remainder at decision time (`with_todays_day_left`), not the one on the card.
- Each message forks from `stable_checkpoint_id`, so a failed or cancelled follow-up leaves no half-finished branch (a tool
  call without a result would break the next model call). Errors restore `report_history/<n>/` and set `followup_error`.
  Budget extensions survive a failed follow-up: that money was granted.
- `report_revision` / `report_digest`: a final card and a new revision only when the report really changed. From the second revision
  the report URLs carry `?v=<revision>`, and the panel key contains the revision, so the panel reloads. A text answer re-renders `result.*`
  with the run's money (the budget section changes), the `report` object and the revision do not.
- The server's `followup_from` record gives a killed or crashed follow-up its finished state back; a crash is shown as
  `followup.error` on a run that stays `completed`, and `run.error` stays for failures of the run itself.
  `followup.in_progress` was added beyond the plan: the frontend needs it to skip the "cannot be continued" warning when a
  follow-up waiting at HITL 2 is cancelled.
- Lean is offered when the report is accepted and the class is not LL, unless this report already cites a `proved`
  `lean_check` (`followup.lean_refusal`). Plain `verified` from other tools does not hide it: the human asked for this button,
  and a REG run approved by a human was exactly the case that lost it (changed in review iteration 2, the coordinator's call).
- `app.js loadScript`: `async = false`. Parallel loading let KaTeX's auto-render run before `katex` and capture `undefined`, which
  left `$...$` unrendered and threw in the Markdown path, in the pipelines' tab as well.
- `ui_server/tests/test_deep_frontend.py` runs the helpers from a temp file: they outgrew the Windows command line (32767 characters).

Review iteration 2 (fixes, with a reproduction first):
- Module split: `followup.py`, `budget_stop.py` and `run_store.py` (run.json, statuses, exit codes, `MAX_MESSAGE_CHARS`). They import
  without langchain, so `ui_server` imports them at module level; `send_followup` and `request_budget_extension` import the heavy
  parts where they are used. `run_store.py` was not in the review's list: both new modules and `hitl` need its constants, and
  `hitl` importing them from a module that imports `hitl` would be a cycle.
- `hitl.POINTS` (title, auto_approvable, shows_args, editable) replaces the switches that read those facts. The pre-existing
  resume logic stays as it was.
- A follow-up record that survives its process means the process did not finish. Exit code 1 is also "no report", so the record
  decides, not the code: `_restore_dead_followup` restores the report outside `_lock` and `_settle_followup_locked` runs first.
- `guarded_segment` catches `BaseException`; `--resume RUN_DIR --abandon` handles a process killed hard.
- During a follow-up the detail's `result` comes from the newest `report_history/<n>/` (immutable, no Windows sharing violation).
  At HITL 2 of a follow-up it is the draft. The client copy in `deepPollOnce` is gone, so a reload shows the same thing. The panel's
  key is the revision, so swapping the URLs to a snapshot and back does not reload the iframe.
- Message files have unique names; the send that loses the race removes its own.
- HITL 2 skips a report with leaked tool-call markup too (the coordinator's addition): the human saw that report and the tool
  refused it only after the approval. `when` now skips invalid fields, unknown ids when it has the ledger, and leaked markup. A
  report refused for unknown ids with no ledger in the config still stops first.

Known gaps: a summarization step may enter wrap-up without asking (it runs inside another node). A summary made in the call that
then hits the budget stop is made and charged again after the resume, exactly once per stop: the node re-executes from its start
(mock: $0.0006 more, `test_a_summary_before_the_stop_is_paid_for_once_more_and_the_cap_holds`; the money cap holds, reviewer's option B).
A killed follow-up's evidence stays in `evidence.jsonl`, and mock cursors are not rewound after a cancel. The preset's message is
shown in full in the chat.

Checked: mock mode only, no API calls. Playwright on an isolated server (port 8791, no key, temp runs dir): follow-up answer
with math, Lean in mock, a new report revision, the budget card with a custom amount. The live look of the composer and the
Lean confirmation was checked by marking the run live in the page's memory, not by a live run; nothing was sent in that state.
No live run covers any of this yet.

Checked in review iteration 2, Playwright on the same kind of isolated server, mock only: the Lean button on a run a human
approved (`verified`); a reload during a follow-up keeps the report (served from `report_history/2`); cancel at HITL 2 of a
follow-up gives "сообщение отменено, отчёт прежний"; a killed follow-up process gives "сообщение не выполнено, отчёт прежний: ..."
with the previous report and `verified` kept (corrected in iteration 3: an exhausted mock script gave a red
"ошибка: mock script: ..." at that point, not this note; iteration 3 made both paths one event shape); the budget card's +$1 and +$5 finish the
run; «Завершить» at a stop before any spend gives `budget_exhausted` and the partial report; a cancelled first segment shows
"Запуск не продолжить" and a disabled composer. «Завершить» after real work ran into the mock script's own `expect` (the wrap-up
note is the last message) and ended with the script's error, so that path in the browser shows only that the wrap-up note reaches
the model; the outcome is covered by tests only.

Review iteration 3 (a reproduction first; 44 tests failed on the code before it):
- A HITL 2 review stores `report_digest`; `review_summary(run, report)` takes the last review of the current report. A positional
  `reviews[:n]` would break on the second message. Trust does not read this: `verdict_gate` reads HITL 1 reviews only. `report_rejections`
  in the hitl block stays a count of history.
- The review-mode switch moved into `resume_run` (`approve_rest`, `auto_approve_later`), applied after `resume_value` succeeds. The CLI
  no longer edits those fields, so a refused command changes nothing. `budget_stop.review_switch_refusal` is the one rule for both.
- `followup_in_progress.review_switch` saves `review_mode`, `auto_approve`, `initial_review_mode`, `approve_rest`; `fail_followup` restores
  them. Budget extensions are not in it: that money was granted.
- `run_store.retry_on_sharing_violation` serves `save_run` and `restore_report`. The record goes only after the restore. `followup.reason`
  gives the `--abandon` command when the record is stuck (`followup.stuck_followup`); no new route.
- One failure, one event shape: `followup_abandoned` (restored status, error), then `segment_end` (restored status, no error).
- `evidence/ledger.lean_proof_side` is the per-entry predicate of the verdict gate, fingerprint rule included (`ir_admissible`,
  `ir_fingerprint` moved there; `tools.ir` imports it). `followup._proved_in` is that predicate plus the result's `trust == "verified"`,
  which is how the gate's multi-entry rules reach it. The gate's behaviour and its tests did not change.
- `followup.day_refusal` serves the route and `hitl.send_followup`. `BudgetLedger.for_task` raises for a new run only, so the follow-up path
  of the `RunError` branch in `_segment` could not run and is gone; the branch stays for a new run.
- `send_followup` moved to `hitl.py`, `first_decision` to `run_store.py`: `followup.py` and `budget_stop.py` do not reference `hitl`.
- `budget_stop_enabled` of a pre-field run reads `initial_review_mode` only, so a pre-field run after `approve_rest` still asks.

Known gaps (not done, the coordinator's call): a pid liveness check for `--abandon`, a lab abandon route, the exit-code rename, the other
API-019 renames, the remaining OO-011 / OO-001 repeats, `deepMessageBody`'s guard.

Checked in review iteration 3, Playwright on an isolated server (port 8791, no key, temp runs dir, a script of its own), mock only:
a rejected draft in a follow-up, then a text reply, keeps "approved by a human at HITL 2"; a new report in a follow-up gives a second
final card and `?v=2` URLs with revision 2 on disk; an exhausted mock script gives one note, "сообщение не выполнено, отчёт прежний: ...",
and no red line; the label «Без проверок». The closed composer's reason was seen on a `run.json` edited by hand to keep the record. A
restore that really fails, a cancel landing as a follow-up finishes, the Lean predicate and the CLI's refusals are covered by tests only.

Final fix round (user's decision; everything else from the reviews goes to the backlog):
- OO-019. An accepted report is rated by what existed when it was accepted. `hitl._as_accepted` cuts the state at the first human
  message after the report: the `tfl_hitl` records and reviews of later tool calls do not count for the rating or the task. It drops
  records of later calls rather than keeping records of earlier ones, so a message summarized away cannot lose an old record, and a
  first run has no human message after its report, so it does not change. `resume_value` stores `reviewed_call_id` in a HITL 1 review.
- The gate does not ignore later evidence: it takes results the report never cited (R4'), and `ir_admissible` lets a record that
  names no IR through. A later `nerode_probe(ir=A)` moved the cap from 0.98 to 0.85 and the revision to 2 on the old code; a later
  `ll_table(grammar=G)` counts too, up to `bounded_pass` next to a matching `grammar_vs_ir`
  (`test_the_gate_itself_counts_a_later_record_that_names_no_ir`). So the rating takes only the first `run.json`
  `accepted_report.evidence_count` records of the ledger, kept with the report's `call_id` when it is accepted. The ledger is
  append-only, so the count is exact, and the evidence of a failed follow-up is cut as well. `collect_result(rating_evidence=...)`
  carries it; the gate and `VERDICT_POLICY.md` did not change. `result["evidence"]` and the report's appendix still list all records.
- CA-015. `BudgetMiddleware.skip_call_when_low`, on for a follow-up of a run with a budget stop whose previous status is `submitted`
  (`budget_stop.declining_skips_the_call`), also after a new process where `budget_declined` already removed the question. After the
  question, a main call that still would enter wrap-up or be refused gets a text reply marked `run_store.UNANSWERED_REPLY` with no
  reservation and no charge. `followup.last_reply` skips that message, `followup_unanswered` carries the chat's note, the CLI prints it
  on stderr (`RunOutcome.followup_note`). The text stays in the conversation, so a later turn reads that the message had no answer.
  `prompts/main.md` stays: no sentence in it became false. Left as they were, as decided: no accepted report. Also
  left: a scope already in wrap-up. The day case was left too, until the user decided on 2026-11-10 (see "CA-015 day case").
  CHANGED by the user on 2026-11-10: the earlier "leave it" for a run with no budget stop (`--auto-approve`) is withdrawn.
  `declining_skips_the_call` no longer asks for `budget_stop_on`: any follow-up of a run with an accepted report skips
  the call when the money does not cover it, so a truncated, auto-accepted report never replaces the accepted one
  (`test_an_auto_approved_run_keeps_its_accepted_report_when_the_follow_up_has_no_money`).
- CA-015 day case (user, 2026-11-10). In a follow-up of an accepted report (`skip_call_when_low`) a main call short because of the
  DAY's remainder no longer goes to wrap-up: `_budget_low_locked` returns `BudgetLow(binds="day", per_day_usd=...)` and the budget stop asks
  about the day limit. `edit` `add_usd` raises `per_day_usd` of the run: `run.json` `budget_day_limit_usd` (`budget_stop.per_day_usd` and
  `_budget_ledger` read it, `BudgetLedger.raise_day_limit` only grows), no cap by the remainder, the settings snapshot stays;
  `budget_extensions` gets `{binds: day, new_day_limit_usd}` (report: "дневной лимит"). `approve`/auto: the call is skipped, accepted report
  kept. Tests: `test_followup.py` `test_a_short_day_*`, `test_raising_the_day_limit_*`, `test_the_day_case_*`. deep.js: no day cap on the +$ buttons for `binds: day`.
  Round 2: `request_budget_extension` returns `(kind, limit)` read from the decision's own key (a re-run node gets the earlier question's
  stored answer back from `interrupt`, so indexing by `low.binds` raised KeyError); the middleware applies the kind it gets. A day raise holds
  only on the day given (`budget_day_limit_day`, `day_limit_raise`). Day card title/button: «Дневной лимит заканчивается» / «Оставить отчёт как есть».
  Round 3 (user decision 2026-11-10, no wrap-up for the day): `BudgetMiddleware._ask_for_budget` asks up to twice in one node, once per kind
  (tracked by the kind of the applied answer, so a re-run node whose first `interrupt()` returns the earlier answer asks the other kind next);
  `followup.day_refusal` is skipped for a run with status `submitted` and the budget stop on (TFL Lab 409 path too), so the day question asks
  instead of a refusal; a declined day card carries `binds: day` and `progress.hitl_decision`/`fold_events` pass `binds`, `new_day_limit_usd`
  (deep.js prints «дневной лимит $X» / «оставить отчёт как есть»). Tests: `test_both_limits_short_*`, `test_a_declined_day_card_*`,
  `test_a_spent_day_asks_*`.
- API-021: `_hitl_view` and `pending_card` apply today's remainder, and the card's summary line is rebuilt from it. API-022: the
  tooltip and the docs say «Без проверок». API-023 / OO-022: a record the lab is not waiting for gives the `--abandon` command,
  whatever the agent's status. CA-018: `_proved_in` reads `gate_entry()`.

Two fixes on top of it:

- A HITL 2 draft in a follow-up: `collect_result` gives `pending_report`, when passed, priority over a report accepted earlier in
  the thread, so `result.*` and `run.json` `draft` show the text and the rating the human is deciding on. Approve gives a new
  revision; reject keeps the old report, its rating and its revision. `hitl.md` and the card show the draft's verdict, rating and
  steps, not its `summary`, as in a first run; the text is in `result.md`, which the card links.
- `final_result` passes `run_binding` a copy of the run without the reviews of later calls: a report is rated by what existed when
  accepted, and a later human approval of the same IR fingerprint does not move it from `bounded_pass` to `verified`. An IR already
  approved in the thread has no stop (`unchanged`), so the only way to that human record is an edit back to that IR; the test does this.

Two gaps in that baseline (`accepted_report`), both reproduced first (R1 went from 0.98 to 0.85 with revision 2):

- `followup_in_progress` keeps `previous_accepted_report`, and `fail_followup` restores it, or drops the key when there was none. A
  follow-up whose report was accepted but whose result failed to write no longer leaves that report's count behind.
- `send_followup` takes the baseline before `begin_followup`, through `_take_report_baseline`: a report in the state with no matching
  `accepted_report` is fixed at the ledger's current count. It reads the state with `run_state`, which calls no model, and that adds
  one agent build per message. A read that fails raises before anything changes in the run; the segment would have failed on it too.

## 2026-11-09: several approved IRs (R-IR) and the browser live check

- A new report in a follow-up was rated against the IR approved last, whatever it was about, so a report on A could get
  `verified` on B's results. Rule R-IR in `docs/VERDICT_POLICY.md`: with two or more IRs standing approved, the report's
  `ir_fingerprint` picks one (`agent.binding_for_report`), with that IR's own human / auto mark; naming none is the status
  `ambiguous`, `not_verified`. With one approved IR nothing changes. The rule covers the class ladder too, so `prompts/main.md`
  step 6 asks for the field instead of a second `validate_ir`. Both live Haiku reports named their IR unprompted.
- Limits: a model that names the wrong approved IR is not caught (the report's formalization line shows it), and a rejected
  last IR still blocks a report on an older approved one (`rejected`, the safe side).
- Live check in TFL Lab, Haiku, $0.058 over two runs: `6fa17e6f5c7e` (`(a|b)*abb`, $0.0354) and `d9b21fc183a2` (`a*b`, $0.0225).
  Passed: a question keeps the report; a draft shows as itself and hides the Lean button; a rejected draft keeps the old report,
  rating and human mark; an approved one gives revision 2 and `result.html?v=2`; the Lean button ends in an honest failure with the
  report and rating unchanged (Haiku wrote `exact?` three times, `lean_check` ran in Docker); the budget card at the start of a run
  with a $0.01 limit, top-up to $0.04 and the run continuing; declining inside a follow-up makes no model call, shows the chat note
  and keeps the report. R-IR itself is covered by mock tests only. A proof that passes `lean_check` was not seen live: that needs Opus.

## Core backlog round (bk/deep-core)

- Lean prompt no longer points at a missing /skills/lean/SKILL.md. Follow-up replies are kept whole (no truncated flag; progress.assistant_message no longer cuts). HITL 1 feedback messages carry additional_kwargs tfl_hitl; the card's task_text is the task stated before the reviewed call and the review stores task_text, so a new report names the new task. _as_accepted cuts at the next task message, not at a HITL note. A non-HITL-2 stop drops a stale rejected draft from result.*. Verdict-gate predicates moved to evidence/rules.py (ledger re-exports them). CA-015 for runs with no budget stop left as decided earlier (needs the user).
- OPEN, needs the user: `_task_text` names the report task after the newest HITL 1 review `task_text`, i.e. the last plain human message before the reviewed call. In a follow-up that is the chat message, so a correction such as "typo: a^n b^2n" becomes the report task instead of the original. Options: (1) keep (right when the follow-up states a whole new task); (2) store the follow-up text in `review['followup_text']` and keep the original as the task; (3) record both in the report. Not changed.

## 2026-11-09: backlog round (ultracode)

Mock-mode work on branch `feat/backlog-ultracode`; no live calls.

- TFL Lab: the continuation timer counts from the latest segment's start; `POST /api/deep/runs/<id>/abandon` and the
  «Отменить продолжение» button (`followup.can_abandon`, 409 while a process is alive); the lab's `review_mode` copy is
  re-read from the agent's `run.json` when a continuation settles; `_watch_orphan` handles a cancel that lands as an adopted
  follow-up finishes; only a button click asks for confirmation (`deepSend*` take `viaButton`); `followup_error` beats the
  exit code only when it is fresh (`agent_error` / `agent_segments` stored at send time).
- CLI: `--abandon` refuses while the pid in `followup_in_progress` is alive (a resumed segment records its own pid);
  `--abandon --force` skips the check; `--force` without `--abandon` is a parser error. `--resume` that cannot start (`OSError`,
  `ImportError`) exits 2 with the reason on stderr. `_check_live_flag` became `_check_live_key`; the mode/`--live` mismatch is
  refused by `hitl.check_run_mode`.
- HITL cards: user text is markdown-escaped (`hitl.md_escape`) so `*` survives; the HITL 2 card and `hitl.md` show the draft
  `summary`. The final report's task statement is not escaped (see decisions).
- Core: no truncation of chat replies (`progress.assistant_message`); `/lean/SKILL.md` no longer referenced; `_task_text` uses
  a review's `task_text` only when it differs from the run's first task message; gate predicates in `evidence/rules.py`.
- Refactors: `budget_add` -> `parse_add_usd`, `budget_resume` -> `budget_decision_response`, `usd` -> `format_usd`;
  `hitl._record_review`, `deep_routes._mark_process_gone`, `_lean_off`, `deepLogNote`, `deepIsAutoSource`.
- Docs: README, `tfl_deep_agent/README.md` and `docs/kb` (`run-lifecycle.md`, `tfl-lab.md`) describe `--force`, the abandon route
  and `report_revision` as implemented.

Open decisions (options and recommendations in TODO.md): exit code for a failed follow-up (keep 1, add alias); TTY dialog at a
budget stop in auto mode (keep exit 3); byte-identical rejected report vs `review_summary` (skip rejected when filtering by
digest); accepted report replaced by a truncated one without a budget stop (drop the `budget_stop_on` condition); escaping `*`
in the rendered report task (only outside `$...$`); follow-up chat message becoming the report task (see the OPEN item above).
Deferred: end-to-end mock test for a rejected draft followed by a new IR; `_is_task_message` for checkpoints without `tfl_hitl`.
Follow-up: the compact exam_04 DPDA (356 transitions) fits the 2000-transition `dpda_vs_ir` limit, unlike the old 5835-transition one.

## 2026-11-10: lean-formalizer prompt knows the lean-theory lemmas (bk2/deep-lean-prompt)

- `prompts/lean.md` lists the Transfer, Closure, RegularFilters and TransferAnBn lemmas of `TflLean/Lemmas.lean`
  (signatures copied from the file), Ogden's lemma (langlib, in the image via `TflLean.Langlib`), a route per direction,
  what is not in the image (LL(k), Parikh, Yu, Shallit 4.7.4, inherent ambiguity, DCFL pumping, DCFL inverse homomorphism,
  grammar to DPDA) and the compiled examples, with four proof bodies quoted verbatim.
- `tests/test_tools_lean.py`: every `TflLean.*` name in `lean.md` must be declared in `Lemmas.lean`, the quoted bodies must
  be substrings of their example files, and the "not in the image" list must stay. `lean_statement` still refuses `ll`.
- Skills: `cfl` step 8 no longer says the only compiled sample is {aⁿbⁿcⁿ} and that closure is not ported; the references to
  a `lean` skill (there is none) now name the `lean-formalizer` subagent.


## deep-decisions (user decisions of 2026-11-10)

- `EXIT_FOLLOWUP_FAILED = 1` (alias of `EXIT_NO_REPORT`, `run_store.py`, used by `cli._report` and `hitl._finish_segment` for a failed
  follow-up); numeric codes and TFL Lab's rc handling unchanged.
- Budget stop in review mode `auto`: exit 3, no terminal dialog in the starting process; a later `--resume` on a terminal asks.
  Docs only (README, `docs/kb/budget.md`).
- API-025: `review_summary` ignores a `rejected` HITL 2 review of the same digest when an approving review of that digest exists.
- CA-015: see the entry above (`declining_skips_the_call` no longer needs `budget_stop_on`).
- The report's task is the original task; a follow-up message that led to a HITL 1 review is stored as `review["followup_text"]`
  (card arg `followup_text`) and rendered as «Уточнение» (`result["clarifications"]`, `clarification_section`). Runs written before this
  key keep the old behaviour for their own reviews (`review["task_text"]` that differs from the first message still wins).
- `render.safe_task_text` escapes `*` outside `$...$` / `$$...$$` in the task.
- Agent identity keys (REG `module`, CFL `agent`, DCFL/LL `agent_name`) are kept; the row is in `docs/pipeline_capabilities.json`
  and the generated README table.
