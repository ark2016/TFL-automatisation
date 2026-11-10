---
title: Жизненный цикл запуска
tags: [deep-agent, run, cli, hitl, progress]
---

# Жизненный цикл запуска

Как устроен каталог запуска, что хранится в `run.json`, как запуск делится на сегменты (процессы), как он продолжается после остановки на HITL и что пишется в `progress.jsonl`. Связанные заметки: [[hitl-and-review-modes]], [[budget]], [[reports]], [[tfl-lab]], [[testing-and-mock]], [[index]].

Источники: `tfl_deep_agent/hitl.py` (логика запуска), `tfl_deep_agent/cli.py` (команда `tfl-deep`), `tfl_deep_agent/progress.py` (события), `tfl_deep_agent/run_store.py` (`run.json`, статусы, коды выхода), `tfl_deep_agent/followup.py` (продолжение завершённого запуска).

## Каталог запуска

Источник: `tfl_deep_agent/hitl.py` (докстринг модуля, `start_run`, `_write_awaiting`), `tfl_deep_agent/cli.py`.

Каталог задаётся `--save RUN_DIR`. Без `--save` это `<runs dir>/<run id>`; `runs dir` возвращает `default_runs_dir()` из `middleware/budget.py`: `$TFL_DEEP_RUNS_DIR` или `<repo>/.tfl_lab_runs`. Идентификатор запуска даёт `cli.new_run_id()`: `deep-ГГГГММДД-ЧЧММСС-<6 hex>`. При `--save` `run_id` по умолчанию равен имени каталога (`d.name`).

| Файл | Что внутри |
|---|---|
| `run.json` | состояние запуска (см. ниже) |
| `checkpoints.sqlite` | чекпоинты LangGraph, `thread_id` = id запуска |
| `evidence.jsonl` | журнал доказательств, при возобновлении дописывается (см. [[tools-and-evidence]]) |
| `progress.jsonl` | события для живого вида TFL Lab |
| `mock_script.json`, `mock_cursors.json` | только mock: копия сценария и использованные ответы по ролям |
| `input/task.<ext>` | файл задачи как его дал человек (только для pdf / изображений) |
| `hitl.md` | карточка текущей остановки |
| `result.json` / `result.md` / `result.html` | отчёт; пока ждёт HITL 2, это черновик со статусом `awaiting_human` |
| `report_history/<n>/` | копии `result.*`, снятые перед каждым сообщением-продолжением (см. «Продолжение завершённого запуска») |

Имена файлов заданы константами в `hitl.py` (`MOCK_SCRIPT_FILE`, `MOCK_CURSORS_FILE`, `CARD_FILE`, `INPUT_DIR`), `run_store.py` (`RUN_FILE`, `CHECKPOINT_FILE`, `REPORT_HISTORY_DIR`, `RESULT_FILES`) и `progress.PROGRESS_FILE`. Файлы `lab.json`, `run.log`, `upload/`, `views/` и прочее принадлежат TFL Lab, не агенту ([[tfl-lab]]).

> [!warning]
> `start_run` отказывается работать в каталоге, где уже есть `run.json` (`RunError`: возобновляйте через `--resume` или возьмите новый каталог). `resume_run` отвечает на остановку: он требует статус `awaiting_human` и непустой `pending`. Завершённый запуск (`submitted`, `incomplete`, `budget_exhausted`, `error`) принимает сообщение через `send_followup` (`--resume RUN_DIR --message`), см. «Продолжение завершённого запуска».

## Поля run.json

Источник: `hitl.start_run`, `_segment`, `_write_awaiting`, `resume_run`.

Начальное содержимое собирает `start_run` (`"v": RUN_SCHEMA`, сейчас 1):

- `run_id`, `thread_id` (равен `run_id`), `created_at`, `updated_at` (проставляет `save_run` при каждой записи);
- `status`: `running`, `awaiting_human` или итоговый (`submitted`, `incomplete`, `budget_exhausted`, `error`);
- `mode`: `mock`, `live` или `custom` (своя фабрика моделей); `factory`: `"custom"` или `None`; `mock_script`: имя копии сценария;
- `template`: шаблон отчёта; если не задан, берётся из mock-сценария, иначе `full_proof`;
- `task_text` (для текстовой задачи), `message` (первое сообщение агента), `input` (метаданные файла и `stored_as`);
- `settings`: полный словарь настроек на момент старта; `hitl`: включённые точки;
- `review_mode`, `initial_review_mode`, `auto_approve` (см. [[hitl-and-review-modes]]);
- `reviews`: история решений человека и автоодобрений; каждое решение получает `interrupt_id`;
- `pending`: ожидающий запрос или `None`; в нём `interrupt_id`, `point`, `request`, `asked_at`, `card` (короткая карточка, TFL Lab показывает её как `pending_point`);
- `budget`: `spent_usd`, `limit_usd`, `records` (записи вызовов), см. [[budget]];
- `limits`: счётчики `model_calls` и `tool_calls`;
- `segments`: список сегментов; `result`, `error`.

Добавляются позже: `draft` (при остановке на HITL 2: `verdict`, `trust`, `confidence_cap`, `unknown_ids`; удаляется при завершении), `approve_rest` (при `--approve-rest`).

Поля остановки по бюджету и продолжения (`run_store.py`, `budget_stop.py`, `followup.py`, `hitl.py`):

- `budget_stop`: спрашивает ли запуск денег; задаётся при старте и главнее любого режима проверок ([[budget]]); `budget_limit_usd`, `budget_extensions`, `budget_declined` хранят ответ на остановке;
- `stable_checkpoint_id`: чекпоинт последнего законченного сегмента, с которого начинается следующее сообщение;
- `report_revision`: номер версии отчёта как объекта (не файлов), в follow-up растёт только при смене статуса, вердикта, доверия, потолка уверенности или `report_digest`, любой другой сегмент, пишущий отчёт, растит её всегда;
- `accepted_report` (`call_id`, `evidence_count`): какой отчёт принят и сколько записей журнала существовало в этот момент (по ним он оценивается, см. [[verdict-gate]]);
- `followup_in_progress`: запись, по которой запуск восстанавливается, пока сообщение не закончилось; `followup_error`: почему последнее сообщение не закончилось (поля нет, если его отменили);
- `limits` при каждом сообщении обнуляется; деньги ограничены ledger'ом.

`save_run` пишет через временный файл и `replace`. На Windows замена файла, открытого другим процессом (например, TFL Lab опрашивает запуск), может падать с `PermissionError`; функция повторяет попытку до 40 раз с паузой 0,05 с.

## Сегменты и возобновление в новом процессе

Источник: `hitl._segment`, `resume_run`, `start_run`.

Сегмент это один процесс между стартом (или возобновлением) и остановкой/концом. `_segment` добавляет в `run["segments"]` запись `{started_at, resumed_with}`; по завершении сегмент получает `ended_at` и `status`. `resumed_with` это решение, которое возобновило сегмент (у первого сегмента `None`).

Что делает `_segment`:

1. Строит `BudgetLedger` (`_budget_ledger`) и открывает `EvidenceLedger` в каталоге запуска.
2. Открывает `SqliteSaver` на `checkpoints.sqlite` (`_open_checkpointer`), затем эмиттер прогресса. Порядок важен: если чекпоинтер не открылся, сегмент падает до записи `segment_start`.
3. Создаёт агента на этом чекпоинте и восстанавливает счётчики `limits` из `run.json`.
4. В цикле вызывает `graph.invoke`. Если после вызова есть прерывание, то либо автоодобряет его (`auto_approves`), либо пишет `awaiting`-состояние и спрашивает диалог. Если решения нет (`dialog=None` или он вернул `None`), цикл прерывается, запуск остаётся в ожидании.
5. Любое исключение агента не ломает запись результата: оно превращается в `error`, а итоговый результат всё равно пишется (частичный).
6. В `finally` сохраняет `limits`, `budget` и `ended_at`.

Возобновление (`resume_run`, CLI: `--resume RUN_DIR --decision ...`) проверяет:

- статус `awaiting_human` и наличие `pending`, иначе `RunError`;
- режим: live-запуск требует `--live` повторно (деньги), mock-запуск `--live` отвергает; `custom`-запуск требует снова передать `model_factory`; live-фабрика не может возобновить не-live запуск;
- решение сверяется с ожидающим запросом (`resume_value`); несоответствие даёт `DecisionError`, и запуск остаётся как был;
- `approve_rest` допустим только с `approve`.

Затем решение добавляется в `reviews`, `pending` обнуляется, статус `running`, и запускается новый `_segment` с `Command(resume={interrupt_id: value})`.

Что переживает границу процесса: `run.json`, чекпоинты, `evidence.jsonl`; в mock-режиме фабрика моделей читает сценарий из копии `mock_script.json` и курсор из `mock_cursors.json`. Бюджет восстанавливает `_budget_ledger` (`hitl.py`); записи `CallRecord` в любом режиме берутся из `run["budget"]["records"]`, а источник уже потраченной суммы зависит от режима:

- mock и `custom`: `BudgetLedger` создаётся без дневного лога, `already_spent_usd` берётся из `run["budget"]["spent_usd"]`;
- live (`mode == "live"` или live-фабрика): `BudgetLedger.for_task(settings, task_id=run_id, resume=...)`, `resume=True`, если сегментов больше одного (текущий уже добавлен в список). Тогда потраченное читается не из `run.json`, а из дневного лога: `log.task_total(task_id)`. При `resume=True` проверка дневного лимита (`check_can_start`) пропускается.

Поэтому `BudgetExceeded` от дневного лимита, превращаемое в `RunError("budget: ...")`, возможно только на первом live-сегменте. Подробности лимитов в [[budget]]; источники: `hitl._budget_ledger`, `middleware/budget.py` (`BudgetLedger.for_task`).

> [!note]
> `hitl.run_state(run_dir)` читает последнее состояние основного потока из `checkpoints.sqlite` свежим агентом на пустых ответах моделей: ни одного вызова модели, бюджет без дневного лога. Нужно для UI и тестов.

## Продолжение завершённого запуска

Источники: `tfl_deep_agent/followup.py`, `hitl.send_followup`, `hitl.guarded_segment`, `hitl._finish_segment`.

Завершённый запуск принимает сообщения в том же разговоре: `tfl-deep --resume RUN_DIR --message TEXT|@FILE`, в TFL Lab `POST /api/deep/runs/<id>/message` и поле ввода ([[tfl-lab]]). Нового чата не открывается: тот же `thread_id`, те же чекпоинты.

- **Когда можно** (`followup_refusal`): статус из `FINAL_STATUSES` (`submitted`, `incomplete`, `budget_exhausted`, `error`) и есть `checkpoints.sqlite`. Запуск на остановке (`awaiting_human`) сначала ждёт решения; запись `followup_in_progress` без ожидающей остановки закрывает продолжение (`STUCK_REFUSAL`), выход через `--abandon`. Живой запуск отвергается (`RunError`) без `--live` и при исчерпанном дне ([[budget]]); пустой текст и длиннее 20000 символов тоже.
- **С чего начинается.** Каждое сообщение ветвится от `stable_checkpoint_id`, поэтому упавшее или убитое сообщение не оставляет в разговоре вызова инструмента без результата. Перед сообщением `result.*` копируется в `report_history/<n>/`, а `run.json` получает `followup_in_progress` (снимок, прежние статус, результат, `accepted_report`, число проверок и значения переключателей режима проверки). `_take_report_baseline` фиксирует, по скольким записям журнала оценён уже принятый отчёт, до первой записи сообщения.
- **Чем заканчивается.** Агент отвечает текстом (отчёт, статус и оценка остаются; `assistant_message`) или присылает новый полный отчёт через `submit_report` (HITL 2 по режиму запуска; новая `report_revision`, если отчёт изменился). Сообщение без вызова модели, потому что бюджет не продлён, описано в [[budget]].
- **Если не закончилось.** Исключение, прерывание клавиатурой, выход интерпретатора и ошибка агента (`guarded_segment`) возвращают прежний отчёт, статус, проверки и режим проверки (`fail_followup`), `followup_error` говорит почему, код выхода 1 (псевдоним `EXIT_FOLLOWUP_FAILED = 1`); запуск принимает следующее сообщение. Деньги остаются потраченными, добавка на остановке по бюджету остаётся выданной. Убитый жёстко процесс запись не снимает: её снимает `--abandon` или TFL Lab. Если откат не удался, запись остаётся и откат можно повторить.
- **Кнопка Lean** - это заготовка сообщения: [[lean]].
- **Оценка принятого отчёта** при сообщениях: [[verdict-gate]].

## Коды выхода

Источник: `tfl_deep_agent/cli.py`, `hitl.py` (`EXIT_*`).

| Код | Значение |
|---|---|
| 0 | отчёт принят (`status == submitted`) |
| 1 | запуск закончился без отчёта (`incomplete`, `budget_exhausted`, `error`); частичный результат записан. Также сообщение-продолжение не закончилось: прежний отчёт и статус остались, причина в `followup_error` |
| 2 | ошибка использования или настройки (`cli._fail`, `parser.error`) |
| 3 | запуск ждёт решения человека (`awaiting_human`) |

При коде 3 CLI пишет в stderr путь к карточке `hitl.md` и три команды возобновления (`resume_hint`: `approve`, `edit --payload`, `reject --comment`), а в stdout строку `awaiting_human: <point>`. При коде 0 в stdout печатается строка `verdict: ...` с классом, доверием к доказательствам, потолком уверенности и пометкой `review`. При коде 1 в stderr идёт `no report: <status>: <reason>`.

## CLI

Источник: `tfl_deep_agent/cli.py`.

Три режима вызова: новый запуск (`TEXT` или `--file`), `--resume RUN_DIR --decision ...` (ответ на остановку) и `--resume RUN_DIR --message TEXT|@FILE` (сообщение к завершённому запуску); отдельно `--resume RUN_DIR --abandon`.

Правила, которые легко нарушить:

- Mock это режим по умолчанию, но без `--mock-script` и без `--live` запуск отказывает с кодом 2: ничего не уходит в API. Подробнее о сценариях в [[testing-and-mock]].
- `--live` и `--mock-script` взаимно исключаются; `TEXT` и `--file` тоже. Задача может прийти из поля `task` mock-сценария.
- `--live` требует `ANTHROPIC_API_KEY` в окружении; `tfl-deep` никогда не читает `.env`. Живые запуски тратят деньги и допустимы только по явной просьбе с согласованным бюджетом (см. корневой `CLAUDE.md`).
- `--auto-approve` это режим `auto` плюс отсутствие остановки по бюджету (`budget_stop` ложно); не просто синоним `--review-mode auto`, который деньги по-прежнему спрашивает. Вместе с другим `--review-mode` даёт ошибку. С `--resume --decision` он означает «автоодобрять все последующие проверки», а текущее решение остаётся решением человека (в `run.json` записывается `initial_review_mode`); `budget_stop` запуска при этом не меняется, и на остановке по бюджету флаг отвергается. С `--message` не сочетается.
- С `--resume` допустимы только `--decision`, `--payload`, `--comment`, `--approve-rest`, `--message`, `--abandon`, `--force` (только с `--abandon`), `--live`, `--no-dialog`, `--auto-approve`. Задача, сценарий, шаблон, настройки и режим проверки берутся из `run.json`. `--message` исключает `--decision`, `--payload`, `--comment`, `--approve-rest`, `--auto-approve`; `--abandon` не берёт других флагов.
- `--approve-rest` только с `--decision approve`, и не на остановке по бюджету.
- `--message` принимает текст, `@FILE` (читается файл) или `@@текст` (буквальный `@текст`); длина до `MAX_MESSAGE_CHARS` = 20000. Живой запуск требует `--live` и `ANTHROPIC_API_KEY`, как и при `--decision`.
- `--payload` принимает JSON-строку или `@FILE` (читается файл как текст); для `reject` это комментарий, для `edit` объект с изменяемыми полями.
- Интерактивный диалог (`TTYDialog`) включается, только если и stdin, и stdout терминалы и не задано `--no-dialog`, `--auto-approve` или `--review-mode auto`. Иначе остановка даёт код 3.
- Шаблоны: `full_proof`, `exam_solution`, `short_verdict`, `lean_appendix`. Допустимые типы файлов задачи: `.txt`, `.md`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp` (см. [[intake]]).
- Файл настроек `--settings` должен существовать; без него действует `tfl_deep_agent/settings.json` с запасом на значения по умолчанию.

## progress.jsonl

Источник: `tfl_deep_agent/progress.py`.

Файл дописывается агентским процессом (`ProgressEmitter`, подключённый в `_segment` к бюджетному и evidence-журналам) и читается сервером TFL Lab по индексу строки (`?after=N`). Возобновление пишет в тот же файл, поэтому он содержит историю всех сегментов. Один JSON-объект на строку.

Поля каждой строки: `event`, `ts`, `run_id`, `segment` (номер сегмента с 1, вычисляется как длина `run["segments"]`). Если подключён бюджет, добавляются `usage` (`calls`, `input_tokens`, `output_tokens`, `estimated_cost_usd`) и `budget` (`spent_usd`, `limit_usd`, `reserve_usd`, `wrap_up`, `exhausted`).

События:

- `segment_start` (`mode`, `template`, `resumed_with`, `hitl`); если сегмент возобновлён, сразу за ним идёт `hitl_decision`;
- `todos` из вызова `write_todos`;
- `tool_call`: по одной записи evidence-журнала (id, tool, ok, trust, scope, elapsed_s, `args_preview` до 300 символов, для `task` ещё `subagent_type`); `trust` проставляет код, не модель;
- `model_call`, `subagent_start`, `subagent_end`, `budget_wrap_up`, `budget_refused`, `subagent_refused`: из событий бюджета (`allocate` превращается в `subagent_start` с `allocated_usd`);
- события оркестрации (`spawn_*`, `workflow_*` и т. п.) пробрасываются под своими именами;
- `hitl_awaiting` (`point`, `interrupt_id`, `card` до 20 000 символов, `draft` только для `report`), `hitl_decision`;
- `user_message` (`text`), `assistant_message` (`text`): сообщение человека к завершённому запуску и текстовый ответ агента; `followup_unanswered` (`text`): сообщение закончилось без вызова модели, потому что бюджет не продлён; `followup_abandoned` (`status`, `error`): сообщение не закончилось, прежний отчёт вернулся (`error: null` - его отменили), сразу за ним идёт `segment_end` с восстановленным статусом;
- решения на остановке по бюджету идут в `hitl_decision` (`extended` / `declined`, `add_usd`, `new_limit_usd`); `hitl_awaiting` получает `point: budget`;
- `segment_end` (`status`, `error`); `done` (`status`, `verdict`, `language_class`, `trust`, `confidence_cap`, `error`). `done` сам вызывает `segment_end`, так что у последнего сегмента перед `done` стоит `segment_end` с итоговым статусом. После сообщения `done` пишется только если отчёт изменился или статус не `submitted`.

> [!warning]
> Эмиттер намеренно не бросает исключений в запуск: ошибка записи или неподдающаяся сериализации запись молча отбрасывается, а `open_emitter` при любой ошибке возвращает заглушку `_NullEmitter`. Поэтому `progress.jsonl` не источник истины: отсутствие строки не доказывает, что события не было. Авторитетны бюджет и `evidence.jsonl`.

`read_events(run_dir)` читает файл целиком для тестов и инструментов и пропускает пустые и битые строки.

## Что проверить при отладке

- Запуск «завис» со статусом `running` и нет живого процесса: процесс умер между `save_run` и концом сегмента; `pending` пуст, поэтому `--resume --decision` откажет. Если в `run.json` есть `followup_in_progress`, процесс умер на сообщении: `--resume RUN_DIR --abandon` возвращает прежний отчёт (пока жив процесс, записавший `pid` в `followup_in_progress`, команда откажет). Смотрите `progress.jsonl` и `segments` (у последнего нет `ended_at`).
- `--resume` отвечает «nothing to resume»: статус не `awaiting_human`.
- «is a live run ... pass --live» или «is a mock run; --live does not apply»: режим запуска записан в `run.json["mode"]` и не меняется.
- Ошибка агента не даёт код 2: она попадает в `run["error"]`, результат со статусом `error` пишется, код выхода 1.
- Как итоговый статус превращается в отчёт и вердикт, см. [[reports]] и [[verdict-gate]]; для теории `docs/THEORY.md`.
