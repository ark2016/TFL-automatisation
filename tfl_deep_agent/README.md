# tfl_deep_agent — TFL Deep Agent

One autonomous [deepagents](https://pypi.org/project/deepagents/) (LangChain) agent for Theory of Formal
Languages tasks of all four classes — REG, CFL, DCFL and LL(k). It sits next to the four pipelines
(`agent_system/`, `cfl_system/`, `dcfl_system/`, `ll_system/`) and does not replace them.

- **Input.** A task as text, a PDF or an image (PNG / JPG / WebP).
- **Work.** The agent plans by itself (`write_todos`), calls typed oracle tools over the pipelines' `lib/`
  packages, delegates to fixed roles, spawns ad-hoc subagents and parallel workflows, and stays inside a money
  budget set in the settings.
- **Output.** A report from one of four templates (`result.{json,md,html}`). A human approves it at two
  review points (HITL). The trust labels and the confidence ceiling are computed by code from the evidence
  ledger, never by the model.

Documents:
- [`PLAN.md`](PLAN.md) is the binding contract.
- [`docs/architecture.html`](docs/architecture.html) is the full architecture, in Russian.
- [`DEV_NOTES.md`](DEV_NOTES.md) holds the phase notes, decisions and deviations.
- [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md) is the tool inventory.
- [`CLAUDE.md`](CLAUDE.md) has the rules for agents that edit this package.

## How it differs from the pipelines

| | Four pipelines | Deep agent |
|---|---|---|
| Control flow | a fixed LangGraph per class, written by hand | one ReAct agent that plans its own steps |
| Classes | one package per class | one agent for REG / CFL / DCFL / LL(k) |
| Input | a hand-written IR JSON (`<ir.json>`) | text, PDF or image; the agent builds the IR itself (`ir-builder`) |
| Models | fixed per agent in `config.py` | a model per role from `settings.json`; ad-hoc subagents pick from `models.allowed` |
| Cost control | `TFL_MODEL_OVERRIDE`, the effort per agent | a hierarchical money budget, checked before every model call |
| Human review | none in the run (a run ends with a result) | HITL 1 on the formalization and HITL 2 on the report, resumable in a new process |
| Mock mode | `--mock DIR` with recorded agent outputs | `--mock-script FILE`: scripted model replies, with real tools |
| Old pipelines as tools | — | no; the agent calls the `lib/` oracles directly |

The trust rules are the same: `docs/VERDICT_POLICY.md` (refuted / verified / bounded_pass / well_formed /
not_verified, with the confidence caps 0.98 / 0.85 / 0.55 / 0.40 for verified / bounded_pass / well_formed /
not_verified).

## Install

The agent needs the optional `[deep]` extra: `deepagents==0.7.34`, `jsonschema`, `langchain`, `langchain-anthropic`,
`langgraph-checkpoint-sqlite` and `pypdf`.

```bash
.venv/Scripts/python -m pip install -e ".[deep,dev]"
```

- It goes into the repository's main `.venv`. No separate `.venv-deep` is needed; `DEV_NOTES.md` "P0" lists the
  upgrades it pulls in.
- The install also registers the `tfl-deep` console script. Without it, use `python -m tfl_deep_agent` from the
  repository root; it takes the same arguments.
- Without the extra, the package still imports, and the tests that need langchain-anthropic or deepagents skip
  themselves.

Docker is optional. It is needed for two things only, see [Sandbox and Lean](#sandbox-and-lean).

## Quick start (mock mode, free)

Mock mode is the default. The agent runs its real loop with its real tools, but every model reply is replayed
from a script, so no request reaches the Anthropic API. Four scripts ship in [`examples/mock/`](examples/mock/):

| script | task | template |
|---|---|---|
| `reg_regex_regular.json` | `(ab)*a\|b` is regular | `full_proof` |
| `reg_followup.json` | the same task, with replies for follow-up messages (a text answer, then a new report) | `full_proof` |
| `cfl_anbmcm.json` | `{a^n b^m c^m}` is context-free | `full_proof` |
| `ll_simple_ll1.json` | `S -> aAb \| bBa; A -> aA \| ε; B -> bB \| ε` is LL(1) | `short_verdict` |

Scripts for the eval entries `cfl-04`, `dcfl-04`, `ll-17`, `reg-04` and `reg-13` are in `examples/mock/eval/<id>.json`;
`tfl-eval --systems deep` uses them (see [Eval](#eval)), and TFL Lab's script list does not offer them.

```bash
# Both review points answered "approve" automatically (marked in the report as not reviewed by a human)
.venv/Scripts/python -m tfl_deep_agent --mock-script tfl_deep_agent/examples/mock/ll_simple_ll1.json \
    --save out/ll1 --auto-approve
# -> verdict: LL(1) (ll); evidence trust: verified; confidence cap 0.98; review: auto-approved ...

# With human review: the run stops at HITL 1 (exit code 3), each --resume continues it in a new process
.venv/Scripts/python -m tfl_deep_agent --mock-script tfl_deep_agent/examples/mock/reg_regex_regular.json \
    --save out/reg --no-dialog
.venv/Scripts/python -m tfl_deep_agent --resume out/reg --decision approve   # HITL 1 -> stops at HITL 2
.venv/Scripts/python -m tfl_deep_agent --resume out/reg --decision approve   # HITL 2 -> result.{json,md,html}
```

## CLI

```
tfl-deep (TEXT | --file task.{txt,md,pdf,png,jpg,jpeg,webp}) [--save RUN_DIR]
         [--template full_proof|exam_solution|short_verdict|lean_appendix]
         [--mock-script FILE | --live] [--review-mode all|formalization|auto] [--auto-approve]
         [--no-dialog] [--settings JSON]
tfl-deep --resume RUN_DIR --decision approve|edit|reject
         [--payload JSON|@FILE] [--comment TEXT] [--approve-rest] [--live] [--auto-approve] [--no-dialog]
```

| flag | meaning |
|---|---|
| `TEXT` | the task statement as text |
| `--file F` | a task file. Text (`.txt`, `.md`) is read as the statement. A PDF or an image is passed to the agent as `/input/task.<ext>`; the `intake` role transcribes it, and the human checks that transcription at HITL 1. The type is decided by magic bytes. Limits: `settings.intake` plus the API caps. |
| `--mock-script F` | the scripted replay for a mock run. Without `--live` it is required. With no `TEXT` / `--file`, the task comes from the script's `task` field. |
| `--live` | real `ChatAnthropic` models; **spends money**. It needs `ANTHROPIC_API_KEY` in the environment; `tfl-deep` never loads `.env`. It excludes `--mock-script`. |
| `--save DIR` | the run directory. Default: `<runs dir>/deep-<timestamp>-<hex>`, where the runs dir is `$TFL_DEEP_RUNS_DIR` or `.tfl_lab_runs`. |
| `--template T` | the report template. Default: the mock script's template, else `full_proof`. The agent may pick another one in `submit_report`. |
| `--review-mode M` | which HITL stops wait for a human, see [Review modes](#review-modes): `all` (default), `formalization` (HITL 1 waits, the report is auto-approved) or `auto` (every review is auto-approved). |
| `--auto-approve` | review mode `auto` plus no budget stop: approve every review in the same process and do not ask for money (for eval and tests). The report says "без проверки человеком". With `--resume --decision`, the decision given is yours, every later review is auto-approved, and the run keeps its budget stop. It does not go with `--message`. |
| `--no-dialog` | never ask on the terminal; stop with `awaiting_human` (exit 3) instead. A run whose stdin or stdout is not a TTY behaves the same way. |
| `--settings F` | a settings file instead of the default one, see [Settings](#settings). The file must exist. |
| `--resume DIR` | continue a run that waits for a human decision, or send a finished run a `--message`. It takes only `--decision`, `--payload`, `--comment`, `--approve-rest`, `--message`, `--abandon`, `--live`, `--no-dialog` and `--auto-approve`; the task, script, template, settings and review mode are the run's own. |
| `--decision D` | `approve`, `edit` or `reject` |
| `--payload P` | for `edit`, a JSON object with the fields to change; for `reject`, the comment (a JSON string or `{"comment": ...}`). `@FILE` reads the payload from a file, `@@text` is the literal `@text`. |
| `--comment C` | the comment for the agent; required with `reject` |
| `--approve-rest` | only with `--decision approve`: approve this stop as your decision, then auto-approve every later review of the run (its review mode becomes `auto`, stored in `run.json`). The budget stop stays, and this flag is refused at it. |
| `--message TEXT` | with `--resume` on a finished run: a follow-up in the same conversation, see [Follow-up messages](#follow-up-messages). `@FILE` reads the text from a file, `@@text` is the literal `@text`. Excludes `--decision`, `--payload`, `--comment`, `--approve-rest` and `--auto-approve`. |
| `--abandon` | with `--resume`: drop a follow-up, whether its process was killed or it waits at a stop (a draft waiting for a decision is thrown away); the previous report, status and reviews come back. The process must be gone; this is not checked. It takes no other flag. |

Exit codes: `0` the report was accepted, `1` the run ended without a report (a partial result is written: `incomplete`,
`error` or `budget_exhausted`) or a follow-up failed (the previous report stays),
`2` usage or setup error, `3` waiting for a human decision.

**Live runs.** By the repository rules (root `CLAUDE.md`), a live run happens only on the user's explicit request,
with a budget agreed beforehand. Use Haiku only (`TFL_MODEL_OVERRIDE=claude-haiku-5-5 ... --live`), state the estimated
cost first, and run the smallest task that answers the question. A live run is also checked against the day's spend
log before it starts. Resuming a live run needs `--live` again, because every segment spends money. A mock run
refuses `--live` on resume.

### The run directory

| file | contents |
|---|---|
| `run.json` | status, task, settings snapshot, mode, HITL history, pending request, budget spent with the call records, call counters; the keys below |
| `checkpoints.sqlite` | the LangGraph checkpoints (`thread_id` = run id); a resume in a new process continues from them |
| `report_history/<n>/` | the report before follow-up message `n`; restored when that message fails or is cancelled, and shown by TFL Lab while it is in progress |
| `evidence.jsonl` | the evidence ledger: every tool result with its trust, scope and role, continued on resume |
| `progress.jsonl` | the live event stream for TFL Lab (model calls, tool calls with trust, subagents, budget, HITL) |
| `mock_script.json`, `mock_cursors.json` | mock mode only: a copy of the script and the replies used per role |
| `input/task.*` | the task file as the human gave it |
| `hitl.md` | the pending HITL card |
| `result.{json,md,html}` | the rendered report; a draft while HITL 2 waits |
| `run.log`, `upload/`, `views/` | TFL Lab only, not written by the agent: the child process's stderr, the uploaded task file, and the accepted report rendered with another template (`views/<template>.{md,html}`; `result.*` is never rewritten) |

Keys of `run.json` that the sections below rely on:

| key | meaning |
|---|---|
| `review_mode` | the effective review mode; `initial_review_mode` is the one the run started with |
| `auto_approve` | the legacy mirror of `review_mode == "auto"`; read `review_mode` |
| `approve_rest` | the human's `--approve-rest` decision, when one was made |
| `budget_stop` | whether the run asks for money when it runs out; authoritative, it does not follow `auto_approve` |
| `budget_limit_usd`, `budget_extensions` | the task limit raised at budget stops, and the record of each extension |
| `budget_declined` | the human chose to finish with what is left; a new message clears it |
| `accepted_report` | the accepted report's `call_id` and `evidence_count`: how much of the ledger rates it |
| `report_revision`, `stable_checkpoint_id` | the accepted report's revision; the state the next message starts from |
| `followup_in_progress` | what restores the run while a follow-up runs; a record that outlives its process means it did not finish |
| `followup_error` | why the last follow-up did not finish |

### Mock scripts

```json
{"description": "...", "task": "...", "template": "full_proof",
 "defs": {"name": {}},
 "models": {"main": [{"content": "...", "tool_calls": [{"name": "validate_ir", "args": {"ir": {"$def": "name"}}}]}],
            "ir_builder": ["..."], "spawn:helper": ["..."], "workflow:step-id": ["..."]}}
```

- `models` maps a role key (the fixed roles, `spawn:<name>` for an ad-hoc subagent, `workflow:<step id>` for a
  workflow step) to a list of replies, replayed in order.
- A reply has `content`, `tool_calls` (`name`, `args`, optional `id`), an optional `usage` and an optional
  `expect: {"last_message_contains": ...}`.
- `{"$def": name}` anywhere in the args is replaced by a copy of `defs[name]`.
- The replay fails loudly in three cases: a reply calls a tool the role does not have, the script runs out, or an
  `expect` does not match.
- The tools are real, so the trust levels in the report come from the libraries. Usage is estimated from the
  message sizes, so the budget charges a mock run like a live one. A mock run never writes the day's spend log.

## Human in the loop

Two review points are on by default (`settings.hitl`). Both use the `HITLRequest` / `HITLResponse` shape, so the
CLI, TFL Lab and the eval adapter handle them the same way.

- **HITL 1, formalization.** The agent stops after the `ir-builder` answers, or after the main agent's own accepted
  `validate_ir`, so building the IR without `ir-builder` does not skip the review.
  - The card shows the transcription (the intake answer, or the task text), the IR, and its restatement and
    sample words. Code makes the restatement and words with `validate_ir` and `ir_to_text`; they are not the
    model's paraphrase.
  - `edit` takes `transcription`, `ir` and `class`. The edited IR is validated before the run continues.
  - The decision binds the verdict by code. Only oracle results for the approved IR count, whatever fingerprint
    the report names. A rejected or never-reviewed formalization leaves the verdict `not_verified`.
- **HITL 2, report.** `submit_report` is interrupted before it runs, and only a report whose fields validate and
  whose evidence ids are all in the ledger is shown. The card has the draft `result.*` and the code-rated trust.
  - A report citing an id that is not in `evidence.jsonl` (a tool name such as `regex_to_dfa` instead of its call
    id, or a made-up label) is not shown and not accepted: `submit_report` returns an error the model sees, with
    the unknown ids and the valid ids grouped by tool (`regex_to_dfa -> toolu_01Xb... (verified, ir-86ca...)`),
    and the model resubmits. Code never maps a name to an id. The run limits still apply, so a model that never
    fixes its ids ends `incomplete` / `budget_exhausted` with a partial report. The verdict gate and the renderer
    still flag an unknown id (second line of defence), and a human edit at HITL 2 citing one is refused before
    the run resumes.
  - `edit` changes report fields (for example `verdict`, `answer`, `method`, `steps`, `template`, `summary`). The
    fields are validated again and marked in the report as edited.
  - `reject` returns the comment to the agent, which then fixes the report and submits it again.
- **`execute`** (off by default) would confirm each sandbox command of the `coder` role.

How the decision is given:
- On a terminal, the CLI asks in the same process (a TTY dialog).
- Otherwise the process writes `run.json` with `status: awaiting_human` and the card `hitl.md`, prints the three
  resume commands and exits with code 3. `tfl-deep --resume DIR --decision ...` then continues the run in a new
  process. The budget, the call counters, the evidence ledger and the mock cursors are restored from the run
  directory.
- `--review-mode auto` (or `--auto-approve`) answers every review with approve, see below. In review mode `auto` a budget stop on a terminal still exits with
  code 3 and waits for `--resume`, because money is never auto-approved.

### Review modes

A run has one review mode (`run.json` `review_mode`), chosen at start (`--review-mode`, TFL Lab `review_mode`):

| mode | HITL 1 (formalization) | HITL 2 (report) | `execute` |
|---|---|---|---|
| `all` (default) | waits for a human | waits for a human | waits for a human |
| `formalization` | waits for a human | auto-approved | waits for a human |
| `auto` (`--auto-approve`) | auto-approved | auto-approved | auto-approved |

Only enabled points stop at all (`settings.hitl`). An auto-approved review is recorded with `source: auto` and decision
`auto_approved`, and an auto-approved report says "без проверки человеком". An auto-approved formalization is not a
human approval: the verdict gate treats it like HITL 1 off, so `regex_to_dfa(ir=...)` stays `bounded_pass`.
`--resume ... --decision approve --approve-rest` approves the pending stop as the human's decision and switches the
run to `auto`; a formalization approved that way still counts as human-approved.

```bash
tfl-deep --resume out/reg --decision edit --payload '{"transcription": "...", "ir": {...}}'   # HITL 1
tfl-deep --resume out/reg --decision edit --payload @edit.json                               # HITL 2
tfl-deep --resume out/reg --decision reject --comment "step 3 uses an uninstantiated p"
```

### The budget stop

When the main agent's next call would enter wrap-up or be refused, the run stops and asks for money instead of
ending. `--decision edit --payload '{"add_usd": 5}'` raises this run's limit (stored in `run.json`, the settings do not
change; live, it must fit in the day's remainder) and is recorded as `extended`. `--decision approve` finishes with what
is left, as wrap-up always did, and is recorded as `declined`. The report's run section lists the extensions. If the
day's remainder is what binds, there is no stop, because more task budget would buy nothing. A subagent or a
summarization never stops the run. The stop does not depend on `settings.hitl`: with both review points off it still
asks; only `auto_approve` or `budget_stop=False` turns it off.

Inside a follow-up on a run with an accepted report, declining makes no model call, and neither does a top-up too small
for the call: nothing is charged, the report, status and revision stay, and the chat gets a note (`followup_unanswered`:
"сообщение без ответа: бюджет не продлён, отчёт прежний"). Evidence already paid for stays in `evidence.jsonl`. A
follow-up with no accepted report keeps wrap-up, which is how it gets its first report; so does a run with no budget
stop.

`auto_approve` means review mode `auto` plus no budget stop, so only a run started with `--auto-approve` goes straight
to the report. Review mode `auto` on its own, and `--approve-rest`, concern reviews and still stop for money.
`hitl.start_run(..., budget_stop=...)` sets it apart from those; the default `None` means `not auto_approve`.

### Follow-up messages

A finished run (`submitted`, `incomplete`, `budget_exhausted` or `error`) continues on its own conversation:

```bash
tfl-deep --resume out/reg --message "Почему состояние q2 принимающее?"
```

The agent answers in text: `result.*` is re-rendered from the last accepted report, so with an accepted report the
report and the status `submitted` stay, and without one the status is what the run's state gives. If it changes the answer, a proof or the evidence, it calls
`submit_report` again, and HITL 2 stops as usual. The previous report is kept in `report_history/<n>/`. Each message
starts from the last finished state (`stable_checkpoint_id` in `run.json`) with fresh call limits; money stays capped.
A follow-up that ends in an error, or is cancelled, restores the previous report and status, and the next message
works as if it never happened. An error exits with code 1, `RunOutcome.followup_error` and `followup_error` in `run.json`
hold it, and `RunOutcome.result` is the previous result. While a follow-up is in progress, `followup_in_progress` in `run.json` holds
what restores the run, including its review mode: `--approve-rest` given at a stop inside a follow-up is undone when that
follow-up fails or is cancelled, so the next one stops for a human. A budget extension stays, because it was granted. A record that outlives its process means that process did not finish: `--resume DIR --abandon`
restores the run, and TFL Lab does it by itself and shows why in `followup.error` (`run.error` stays for failures of
the run itself). A restore that fails keeps the record: TFL Lab closes the composer, and `followup.reason` gives the
`--abandon` command.

A HITL 2 review record stores `report_digest`, the digest of the report it decided (for an edit, of the edited report).
`human_review` in the result comes from the last review whose digest matches the current report, so a rejected draft
followed by a text reply leaves "approved by a human" on the report that was approved. Older runs, with no digest, read
the last review.

An accepted report is rated by what existed when it was accepted. A new IR reviewed in a follow-up, or a tool run after
the report, moves neither its trust, cap and verdict token nor its task. A report submitted after them binds to them as
usual. The later results are still listed in the report's evidence appendix and in `evidence.jsonl`.

`report_revision` in `run.json` tracks the report object, not the files: a text answer re-renders `result.*` with the
run's money and keeps the revision. The chat events: `user_message`, `assistant_message` (`truncated: true` when the
text was cut to the limit), `followup_abandoned` (`error` says why; `null`: it was cancelled). `segment_end` is the end
signal of a segment; `done` is a final report state to display. A failed follow-up emits `followup_abandoned` with the
restored status and the error, then `segment_end` with the restored status and no error, whichever way it failed.

## Settings

- **File.** `tfl_deep_agent/settings.json` is gitignored. `$TFL_DEEP_SETTINGS` points at another path, and
  `--settings FILE` overrides both for one run.
- **Defaults.** [`settings.example.json`](settings.example.json) equals the code defaults (`settings.DEFAULTS`),
  and a test pins that. A missing file means the defaults.
- **Partial files.** A file may hold only some keys. They are merged over the defaults, nested objects key by key,
  lists replaced as a whole.
- **Validation.** Unknown keys, wrong types and inconsistent values are refused with every problem listed. A broken
  file never falls back to the defaults silently, because it holds the money limits.
- **TFL Lab** edits the same file (`GET` / `PUT /api/deep/settings`).

| section | keys (defaults) |
|---|---|
| `budget` | `per_task_usd` 10.00, `per_day_usd` 20.00, `per_subagent_max_usd` 2.00, `reserve_for_report_usd` 1.00 |
| `models` | `allowed` (Haiku / Sonnet / Opus 5.5; aliases `haiku` / `sonnet` / `opus` are accepted), `roles` (model per fixed role), `opus_needs_reason` true |
| `limits` | `max_model_calls` 80, `max_tool_calls` 300, `max_parallel` 4, `max_spawn_depth` 2 |
| `hitl` | `formalization` true, `report` true, `execute` false |
| `intake` | `max_pdf_pages` 20, `max_file_mb` 10 |

Every model id needs a price card in `agent_system/lib/llm_client.py` (`MODEL_PRICING`), because the budget can
neither reserve for nor charge an unpriced model. `models.roles` may name only models in `models.allowed`.

### Budget semantics

Code: [`middleware/budget.py`](middleware/budget.py).

- **Hierarchy.** The task has a root node with `per_task_usd`. Each `task` call, ad-hoc subagent and workflow branch
  gets a child allocation:
  - the allocation is taken from the parent's remainder at once;
  - it is capped by `per_subagent_max_usd`;
  - it can never use the parent's `reserve_for_report_usd`.

  A child cannot spend more than its allocation, nor more than any ancestor still has. Money a child did not spend
  goes back to the parent.
- **`max_tokens` comes from the remaining budget.** Every budgeted model call (the main agent, the fixed subagents,
  `spawn_subagent` children, workflow branches and the summarization call) gets
  `max_tokens = min(ceiling, floor((room − worst-case input cost) / output price))`.
  - The ceiling is the role's value in `config.py` (`MAX_TOKENS` 64000, `lean` 128000), clamped to the model's
    output cap, or a lower value the request sets itself (the summary call: 8192).
  - The room is what the call's own scope has free above its reserve. A subagent or a branch counts only its own
    allocation.
  - The 4096-token floor (or the ceiling, when that is lower) is the least a call gets. A call that cannot get
    the floor is refused. Adaptive thinking has no thinking budget to satisfy, but thinking tokens count toward
    `max_tokens`, and 4096 leaves room for some reasoning plus a tool call or a short answer.

  So a call with the full ceiling is made while the scope can pay for it, and later calls get smaller caps instead
  of being refused. A call with a small cap can stop at `max_tokens`. Its partial answer is charged like any other.
- **Check before each call.** The worst case of a call is reserved while the call runs, so parallel branches cannot
  over-commit. The worst case is every prompt token at the dearer of the input and cache-write rates, plus
  `max_tokens` output tokens (the derived cap), at the prices in `llm_client.py`. When the call returns, the actual
  cost from the usage metadata replaces the reservation, with cache tokens priced at their own rates.
- **Refusal.** A call that does not fit even at the floor is not made. The agent loop ends, and the renderer writes a
  partial report with status `budget_exhausted`.
- **Wrap-up.** When the next call cannot get the floor above the reserve:
  - the agent is told to submit its report;
  - the spawning tools (`task`, `spawn_subagent`, `run_workflow`) are refused;
  - the calls may use the reserve. Their `max_tokens` is derived from the remainder with the reserve, so the
    report call gets what the reserve pays for. With the default $1.00 on Opus that is
    (1.00 − prompt tokens × $5/MTok) / $20/MTok, for example about 42500 tokens with a 30000-token prompt and
    30000 tokens with an 80000-token prompt; the 4096-token floor stays payable up to a prompt of about 180000 tokens.
- **Per day.** `<runs dir>/deep_spend_log.jsonl` holds one line per charged live call. A live task cannot start
  when its `per_task_usd` does not fit in what is left of the local calendar day, and every call is checked against
  that remainder as well. The CLI and TFL Lab share the log, and TFL Lab also allows only one live deep run at a time.
- **Small task budgets.** The task keeps `min(reserve_for_report_usd, per_task_usd / 4)` as its reserve, the rule
  fixed subagents follow too: a $1.00 live run keeps $0.25, the $10.00 default keeps the full $1.00. A reserve above
  the task budget is therefore not an error.
- **Defaults.** With $10.00 per task the main agent's first calls get the full 64000 tokens on Opus. A $2.00
  allocation of a fixed subagent (a `task` call) keeps a $0.50 reserve for the subagent's answer (a quarter of the allocation, below the $1.00 root reserve). The
  $1.50 above it pays for at most 75000 Opus output tokens per call, minus the prompt. For the `lean-formalizer` that is below
  its 128000 ceiling. The cap shrinks as the subagent spends.

### Models

`config.py` mirrors the four pipelines' `config.py`: Opus 5.5 `claude-opus-5-5`, Sonnet 5.5 `claude-sonnet-5-5`,
Haiku 5.5 `claude-haiku-5-5`, the same `EFFORT`, `MAX_TOKENS` and `REFUSAL_FALLBACK` conventions. The prices are
re-exported from `agent_system/lib/llm_client.py`, not copied. `models.py` builds `ChatAnthropic` with the request
shape of `AnthropicClient.build_request_kwargs` (adaptive thinking plus effort, no temperature on 5.x models).

| role (settings key) | subagent name | default model | what it does |
|---|---|---|---|
| `main` | — | Opus | plans, forms the hypothesis, picks methods and models, writes the report |
| `intake` | `intake` | Sonnet | transcribes a PDF or image task into text |
| `ir_builder` | `ir-builder` | Sonnet | task text → an IR that passes `validate_ir` |
| `prover` | `prover` | Opus | the proof, every concrete step checked by an oracle |
| `critic` | `critic` | Opus | a fresh-context search for the error in a proof |
| `lean` | `lean-formalizer` | Opus | the Lean 4 proof body for a statement fixed by code |
| `coder` | `coder` | Sonnet | Python in the Docker sandbox (only when a sandbox is open) |
| `general` | `general-purpose` | Haiku | small generic chores, theory tools only |

- `TFL_MODEL_OVERRIDE` forces every role and every ad-hoc subagent to one model. The model policy is checked
  first, against the configured model.
- An Opus pick for `spawn_subagent` or `run_workflow`, including a workflow step whose role default is Opus, needs
  a `reason` of at least 8 characters while `opus_needs_reason` is on. The reason is logged.
- Model calls share the process-wide `TFL_MAX_CONCURRENCY` semaphore (default 4) of `agent_system/lib/llm_client.py`.
  A TFL Lab process that runs a pipeline and a deep-agent task at once therefore has one limit. Inside a run,
  `limits.max_parallel` is a second bound.

## Tools, evidence and the report

- **Tools.** The groups are `ir`, `words`, `reg`, `cfl`, `dcfl`, `ll`, `theory`, `lean`, `orchestration` and
  `report`. Each tool is a pydantic-typed wrapper over a pipeline `lib/` function, and every result carries a
  `trust` computed by code. Group ceilings: IR tools are at most `well_formed`, word sampling at most `bounded_pass`.
  Theory, orchestration and report tools produce no evidence.
- **Skills.** `skills/{reg,cfl,dcfl,ll,report-templates}` are served read-only under `/skills/`.
  The theory statements in them are quoted or condensed from `docs/THEORY.md` and the `tz_*.md` specs.
  `skills/lean/` is empty: `prompts/lean.md` carries the Lean API notes.
- **Evidence.** Every tool result is recorded in `evidence.jsonl`, including the results of subagents and workflow
  branches, under run-wide ids. A report step cites evidence ids:
  - a step's trust is the strongest non-refuted trust among its cited results;
  - an id that is not in the ledger makes its step `not_verified`;
  - an unknown id at the verdict level lowers the verdict one level.
- **Verdict basis.** The basis is derived by code from oracle results for the IR approved at HITL 1. The model's
  self-assessment never raises the confidence. For REG, `regex_to_dfa(ir=...)` on the task's own regex or right- /
  left-linear grammar is a `verified` basis for `regular` (cap 0.98, VERDICT_POLICY §1) only when a human approved
  that IR at HITL 1 and its `source_text` holds the definition; with HITL 1 off or auto-approved (every eval run) it
  counts like `dfa_vs_ir` (`bounded_pass`, 0.85), since the IR may be the model's own formalization.
  `regex_to_dfa` on a regex the model wrote is never a basis. A `pump_check_reg` claim is a destructive basis of at
  most `well_formed` (`non_regular` ≤ 0.55, R6); a split that stays in L at every claimed exit (exact oracle)
  makes it `refuted`, and a cited refuted claim blocks `non_regular`.
  `reg_regex_regular.json` takes the first route (HITL 1 off there, so 0.85).
- **State diagrams.** For a DFA a cited tool call returned (`regex_to_dfa`, `dfa_vs_ir`, `run_dfa`, `nerode_probe`)
  and for a DPDA passed to a cited `dpda_vs_ir` / `dpda_run`, the renderer draws a state diagram in the "Конструкция"
  part. `reports/diagrams.py` builds the DOT source in code (`dfa_to_dot`, `dpda_to_dot`), and `dot_to_svg` renders it
  with the Graphviz `dot` binary (`dot -Tsvg`, found on `PATH`). Only DOT made by code is drawn; a `dot` block written by
  the model stays plain code. Automata above 30 states or 120 edges get no diagram. Without `dot`, or if it fails or
  times out, the HTML keeps the DOT source as a code block next to the transition table, and `result.md` always has
  the `dot` block. Graphviz is optional; the diagram tests skip without it.
- **Templates** (`skills/report-templates/`): `full_proof`, `exam_solution`, `short_verdict` and `lean_appendix`.
  Their structure follows the golden reports in `docs/examples/reg_grammar_SaSb_lean_proved/` and
  `docs/examples/cfl_ticket50_w1bw2w3_lean_proved/`: Условие, Ответ, Решение (idea, construction, steps with
  justifications), Заключение. The answer box, the trust marks and the appendix "Проверка" (evidence table, gate
  notes, run, models and costs) are generated by code.

### Ad-hoc subagents and workflows

- **`spawn_subagent`** creates a subagent from a name, a prompt, a tool whitelist (groups `ir`, `words`, `reg`,
  `cfl`, `dcfl`, `ll`, `theory`, `lean`), a model from `models.allowed`, a budget and an optional output JSON
  schema.
  - A subagent's answer is never evidence; its oracle calls are, by id.
  - Depth is limited by `limits.max_spawn_depth`.
- **`run_workflow`** compiles a list of steps with dependencies into a LangGraph `StateGraph` with `Send` fan-out.
  - Steps run in parallel up to `limits.max_parallel`, each in its own budget branch.
  - Duplicate ids, unknown dependencies, cycles (printed as `a -> b -> a`), a disallowed model, an Opus pick without
    a reason and a budget that does not fit are all refused in one message.
  - `dry_run` validates the plan without spending.
- **`budget_status`** reports the money left, the free amount for a new child, the wrap-up state and a per-model
  cost table.

An IR built inside a workflow step or by a spawned subagent is not reviewed at HITL 1. Build it with
`task(ir-builder)`, or run the main agent's own `validate_ir` on it.

## Sandbox and Lean

Both need Docker. Their tests skip cleanly when Docker, the daemon or the image is missing.

**Sandbox (the `coder` role).**
- `sandbox/docker_backend.py` (`DockerSandbox`) runs one container per run from the image `tfl-sandbox:latest`.
  Build the image with `sandbox/build.ps1` or `sh tfl_deep_agent/sandbox/build.sh`, about 330 MB. The Dockerfile
  goes in on stdin with no build context, so no repository file ends up in the image.
- Container limits: `--network none`, a read-only root, `/workspace` and `/tmp` as `noexec` tmpfs, 1 GB memory,
  1 CPU, 256 pids, uid 1000, `--cap-drop ALL`, `no-new-privileges`.
- Mounts: only the four pipeline packages and `docs/THEORY.md`, read-only at `/opt/tfl`, with `PYTHONPATH=/opt/tfl`.
  Their `tests/` and `examples/` directories, `EVAL_SET.md`, `EVAL_RESULTS.md` and every secret-looking file
  (`.env`, keys) are masked.
- The coder's output is at most `well_formed` and never the basis of the verdict. Only the `coder` role gets
  `execute`; the main agent and the other roles never do.
- **Status.** `create_tfl_agent(sandbox="auto" | True | <backend>)` wires it in, but the runners (`hitl.start_run` /
  `resume_run`, so the CLI and TFL Lab too) do not open a sandbox yet. A CLI or UI run therefore has no `coder`, no
  `execute`, and its prompt tells the agent so. Turning the sandbox on for user runs is a decision still open
  (`DEV_NOTES.md` "P4-P6 FIX").

**Lean (`lean_statement`, `lean_check`, the `lean-formalizer` role).**
- The Lean file is always composed by code (`docs/VERDICT_POLICY.md` R-Lean):
  - `lean_statement` renders the theorem statement from the IR with the pipelines' renderers
    (`agent_system` / `cfl_system` / `dcfl_system` `lib/lean_ir.py`);
  - the model writes only the proof body;
  - `lean_check` runs the `scan_proof_body` guard and type-checks the file through
    `agent_system/lib/type_check.py` in the `tfl-lean4` Docker image. Build that image as the root
    `README.md` "The `tfl-lean4` Docker image" describes.
- A check with status `proved` and only the allowed axioms is `verified`, and for the task's approved IR it is a
  verified basis for its side of the verdict.
- LL(k) has no Lean path.
- Type-checking runs locally in Docker and costs no API money. Writing the proof body is a model call, like every
  other.

## TFL Lab tab

TFL Lab (`python -m ui_server.server --port 8765`) has a second workspace, **Deep Agent**. Use the switch in the top
bar, or open `http://127.0.0.1:8765/#deep`. Backend: `ui_server/deep_routes.py` (`/api/deep/*`).

The tab is a chat (code: `ui_server/static/deep.js`): the runs on the left, one run's conversation in the middle,
the report on the right.

- **Runs.** "+ Новая задача", the deep runs (first words of the task, status dot, cost), ⚙ for the settings modal
  (`GET` / `PUT /api/deep/settings`).
- **Composer** (bottom of the chat). Text, or a PDF / image by drag-and-drop, the attach button or paste; Ctrl+Enter
  sends a mock run. Chips under it: review mode (`review_mode`: Подтверждать всё / Только формализацию / Без
  проверок), budget $, main model, report template, and mock script / inline script / live. Live shows a one-line
  cost note and needs the "подтверждаю расходы API" tick; it may only lower the money limits.
- **Conversation.** The task as a user message; consecutive model / tool calls folded into one row
  ("▸ 4 инструмента · haiku · $0.004", expands to tool, trust chip, one-line reason); the plan (`todos`) as a
  checklist; subagents / spawned agents / workflows as collapsed rows; decisions as one line
  ("✓ формализация принята · вы"); errors and budget signals visibly. A run at a stop gets an inline card from
  `pending_point` (title, ≤ 4 summary lines, Принять / Изменить / Отклонить, "Принять всё до конца" =
  `approve_rest`, "подробнее" = `details_md`); Изменить opens the IR / transcription or the report fields inline.
  The header carries a slim budget line (task and day).
- **After the report.** The composer sends the next message into the same conversation (`POST .../message`). The
  button «Формально верифицировать (Lean)» under the last final card sends the preset `lean`; live, a second click
  confirms the spend.
- **Budget card.** At the budget stop: spent / limit, +$1, +$5, an amount of your own, «Завершить с тем, что есть». A
  live run's buttons block on today's remainder of the day log, and the click is the decision about money.
- **Report.** The finished report (or the draft at the report stop) in a sandboxed iframe; another template can be
  viewed for an accepted report; closable, widenable; the raw log is behind "журнал".

API additions (2026-11-08; the full list is in the `ui_server/deep_routes.py` docstring):
- `POST /api/deep/runs` takes `"review_mode": "all" | "formalization" | "auto"` (live runs too; live still needs
  `confirm_spend`). `"auto_approve": true` (mock only) means review mode `auto` plus no budget stop; `"review_mode":
  "auto"` on its own concerns reviews, so the run still asks for money.
- `POST /api/deep/runs/<id>/resume` takes `"approve_rest": true`, only with `"decision": "approve"`.
- `POST /api/deep/runs/<id>/message` takes `{"text": ... | "preset": "lean", "confirm_spend": true}`: a follow-up on a
  finished run (409 while it cannot take one, 400 for a bad body). `GET /api/deep/runs/<id>` carries `followup`:
  `{can_message, reason, in_progress, error, lean}`, and `hitl.budget_stop`.
- Endpoints the list above does not cover (all under `/api/deep/`): `POST /runs/<id>/cancel` kills the run's process,
  or cancels a run waiting at a stop; a follow-up that is waiting or running goes back to its previous report instead
  and the run can take another message. `POST /runs/<id>/render {"template"}` shows the accepted report of a finished
  run in another template as `views/<template>.{md,html}` (no model call, `result.*` untouched). `GET /estimate`
  returns the money limits, today's spend and the measured cost of past live deep runs. `GET /mock-scripts` lists
  `examples/mock/*.json`. `GET /runs/<id>/files/<path>` serves a fixed set only: `result.{json,md,html}`, `hitl.md`,
  `evidence.jsonl`, `progress.jsonl`, `run.log` and the directories `input/`, `upload/`, `views/`, `report_history/`.
- `GET /api/deep/runs/<id>` and the runs list carry `review_mode` (the effective one: `auto` after `approve_rest`)
  and `pending_point`. `pending_point` is `null` unless the run is `awaiting_human`; then it is the stop's card,
  made by code (no model call), also at the top level of the detail response:

```json
{"point": "formalization",
 "title": "HITL 1 · формализация: условие и IR",
 "summary": ["Класс: REG", "Язык: regex (ab)*a|b над {a, b}", "в L: a, b, aba, ababa, abababa",
             "не в L: ε, aabbab, aababab, babbbba, aabaabbb"],
 "details_md": "## HITL 1: transcription and IR ...",
 "editable": ["transcription", "ir", "class"]}
```

  `summary` has at most 4 lines, each shorter than 90 characters; for HITL 2 it is
  `["Ответ: <verdict>", "Доверие: <trust> · потолок <cap>", "Шагов: N · утверждений: M"]`. `details_md` is the full
  `hitl.md` section of the stop; `editable` lists the fields an `edit` decision accepts (`[]`: no edit).

UI runs live in `.tfl_lab_runs/deep/<id>/`. Each start or resume is one `python -m tfl_deep_agent ... --no-dialog`
subprocess, so a HITL stop survives a server restart. A mock child never receives `ANTHROPIC_API_KEY`.

## Eval

`tfl-eval --systems deep` runs the eval set (`docs/EVAL_SET.md`, `tfl_eval/manifest.json`) through the agent.

```bash
tfl-eval --systems deep                      # mock mode: entries with a replay script run, the rest are skipped
tfl-eval --systems deep --ids reg-04,ll-17   # a subset
tfl-eval --systems deep --deep-settings my_settings.json   # another settings file (its budget applies per task)
```

- `deep` is opt-in; it is not in the default system set.
- The agent runs on every selected entry, whatever its class. It gets only the entry's `source_text`, never the IR,
  followed by one question line for the entry's class: «Вопрос: является ли язык регулярным?» (reg),
  «... контекстно-свободным?» (cfl), «... детерминированным контекстно-свободным (DCFL)?» (dcfl), and for ll the
  smallest k for which the given grammar (Format 3) or the language is LL(k). The same language is asked under
  several classes ({a^n b^n} is reg-01, dcfl-01 and ll-01), so the question names the one being scored.
- Every HITL stop is auto-approved.
- The verdict comes from the run's `result.json`. A report for another class than the entry's is scored wrong.
- Mock mode replays `examples/mock/eval/<id>.json`; an entry without a script is reported `skipped`.
- Run directories go to `<out>/deep_runs/<id>/`. When that directory is already used (a second eval with the same
  `--out`), the next free `<id>-2`, `<id>-3`, ... is taken.
- `--live` only on the user's explicit request, with an agreed budget, on Haiku. Before the first call the eval
  prints the deep entries' worst case, min(entries x `per_task_usd`, `per_day_usd`), and refuses to start (exit 2)
  when it is above `--deep-max-usd` (default $1.00). With the default $10.00 per task even one entry is above it:
  narrow the set with `--ids`, lower `budget.per_task_usd` in `--deep-settings`, or pass the agreed budget as
  `--deep-max-usd`.
- In a mixed live run (`--systems reg,deep --live`) the pipelines' live runners load `.env`. The eval restores
  `ANTHROPIC_API_KEY` to its value from before them, so the deep entries never use a key that came from `.env`.

The metrics are the pipelines' (root `README.md`, "`tfl-eval`"). The adapter's notes are in `DEV_NOTES.md` under P8.

## Tests

```bash
.venv/Scripts/python -m pytest -q tfl_deep_agent/tests          # the package only; no API, no network
.venv/Scripts/python -m pytest -q -n 4 tfl_deep_agent/tests     # in parallel
```

- Every test uses scripted or fake chat models.
- `tests/guard.py` strips `ANTHROPIC_API_KEY`, makes `.env` loading a no-op and stubs `anthropic.Client` /
  `AsyncClient`, which langchain-anthropic uses. Any other test directory that builds the agent must import it
  (`test_network_guard.py` checks that).
- The real-Docker sandbox and Lean tests skip with a reason when Docker is unavailable, and the diagram tests when
  Graphviz `dot` is not on `PATH`.
- `tests/test_docs.py` keeps this README in step with the parser, the settings defaults, the run directory table,
  the shipped mock scripts and the package layout; update the README when you change one of them.
- Follow-up messages, the budget stop and the Lean button: `tests/test_followup.py`. TFL Lab's side:
  `ui_server/tests/test_deep_routes.py` and `ui_server/tests/test_deep_frontend.py` (its pure-helper tests run under
  node and skip without it). They use mock scripts only:

  ```bash
  .venv/Scripts/python -m pytest -q ui_server/tests/test_deep_routes.py ui_server/tests/test_deep_frontend.py
  ```

## Package layout

```
tfl_deep_agent/
├── cli.py, __main__.py   # tfl-deep / python -m tfl_deep_agent
├── agent.py              # create_tfl_agent, scripted mock models, collect_result, verdict gate
├── hitl.py               # HITL 1/2 middleware, start_run / resume_run / send_followup, TTY dialog
├── followup.py           # follow-up messages: what a run allows, report history, the Lean button rule
├── budget_stop.py        # the budget stop: asking for more money
├── run_store.py          # run.json, statuses, exit codes (stdlib only)
├── roles.py, prompts/    # fixed roles and their system prompts
├── skills/               # read-only skills per class + report templates
├── settings.py           # settings schema, loading, overrides (settings.example.json = defaults)
├── config.py, models.py  # model ids, effort, max_tokens; ChatAnthropic factory
├── tools/                # typed tools over the pipelines' lib/ (each result carries trust)
├── evidence/             # evidence ledger (evidence.jsonl) and the trust policy
├── middleware/           # budget, concurrency, evidence middleware
├── orchestration/        # spawn_subagent, run_workflow, model policy
├── intake/files.py       # text / PDF / image -> content blocks
├── reports/              # render.py: result.{json,md,html}; diagrams.py: DFA / DPDA state diagrams (DOT, SVG)
├── progress.py           # progress.jsonl for TFL Lab
├── sandbox/              # DockerSandbox, Dockerfile.sandbox, build scripts, /workspace adapter
├── examples/mock/        # mock replay scripts
└── tests/
```
