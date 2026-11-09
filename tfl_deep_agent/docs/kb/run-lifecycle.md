---
title: Жизненный цикл запуска
tags: [deep-agent, run, cli, hitl, progress]
---

# Жизненный цикл запуска

Как устроен каталог запуска, что хранится в `run.json`, как запуск делится на сегменты (процессы), как он продолжается после остановки на HITL и что пишется в `progress.jsonl`. Связанные заметки: [[hitl-and-review-modes]], [[budget]], [[reports]], [[tfl-lab]], [[testing-and-mock]], [[index]].

Источники: `tfl_deep_agent/hitl.py` (логика запуска), `tfl_deep_agent/cli.py` (команда `tfl-deep`), `tfl_deep_agent/progress.py` (события).

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

Имена файлов заданы константами в `hitl.py` (`RUN_FILE`, `CHECKPOINT_FILE`, `MOCK_SCRIPT_FILE`, `MOCK_CURSORS_FILE`, `CARD_FILE`, `INPUT_DIR`) и `progress.PROGRESS_FILE`.

> [!warning]
> `start_run` отказывается работать в каталоге, где уже есть `run.json` (`RunError`: возобновляйте через `--resume` или возьмите новый каталог). Продолжить уже завершённый запуск нельзя: `resume_run` требует статус `awaiting_human` и непустой `pending`.

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

## Коды выхода

Источник: `tfl_deep_agent/cli.py`, `hitl.py` (`EXIT_*`).

| Код | Значение |
|---|---|
| 0 | отчёт принят (`status == submitted`) |
| 1 | запуск закончился без отчёта (`incomplete`, `budget_exhausted`, `error`); частичный результат записан |
| 2 | ошибка использования или настройки (`cli._fail`, `parser.error`) |
| 3 | запуск ждёт решения человека (`awaiting_human`) |

При коде 3 CLI пишет в stderr путь к карточке `hitl.md` и три команды возобновления (`resume_hint`: `approve`, `edit --payload`, `reject --comment`), а в stdout строку `awaiting_human: <point>`. При коде 0 в stdout печатается строка `verdict: ...` с классом, доверием к доказательствам, потолком уверенности и пометкой `review`. При коде 1 в stderr идёт `no report: <status>: <reason>`.

## CLI

Источник: `tfl_deep_agent/cli.py`.

Два режима вызова: новый запуск (`TEXT` или `--file`) и `--resume RUN_DIR --decision ...`.

Правила, которые легко нарушить:

- Mock это режим по умолчанию, но без `--mock-script` и без `--live` запуск отказывает с кодом 2: ничего не уходит в API. Подробнее о сценариях в [[testing-and-mock]].
- `--live` и `--mock-script` взаимно исключаются; `TEXT` и `--file` тоже. Задача может прийти из поля `task` mock-сценария.
- `--live` требует `ANTHROPIC_API_KEY` в окружении; `tfl-deep` никогда не читает `.env`. Живые запуски тратят деньги и допустимы только по явной просьбе с согласованным бюджетом (см. корневой `CLAUDE.md`).
- `--auto-approve` это синоним `--review-mode auto`; вместе с другим `--review-mode` даёт ошибку. С `--resume` он означает «автоодобрять все последующие остановки», а текущее решение остаётся решением человека (в `run.json` записывается `initial_review_mode`).
- С `--resume` допустимы только `--decision`, `--payload`, `--comment`, `--approve-rest`, `--live`, `--no-dialog`, `--auto-approve`. Задача, сценарий, шаблон, настройки и режим проверки берутся из `run.json`.
- `--approve-rest` только с `--decision approve`.
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
- `segment_end` (`status`, `error`); `done` (`status`, `verdict`, `language_class`, `trust`, `confidence_cap`, `error`). `done` сам вызывает `segment_end`, так что у последнего сегмента перед `done` стоит `segment_end` с итоговым статусом.

> [!warning]
> Эмиттер намеренно не бросает исключений в запуск: ошибка записи или неподдающаяся сериализации запись молча отбрасывается, а `open_emitter` при любой ошибке возвращает заглушку `_NullEmitter`. Поэтому `progress.jsonl` не источник истины: отсутствие строки не доказывает, что события не было. Авторитетны бюджет и `evidence.jsonl`.

`read_events(run_dir)` читает файл целиком для тестов и инструментов и пропускает пустые и битые строки.

## Что проверить при отладке

- Запуск «завис» со статусом `running` и нет живого процесса: процесс умер между `save_run` и концом сегмента; `pending` пуст, поэтому `--resume` откажет. Смотрите `progress.jsonl` и `segments` (у последнего нет `ended_at`).
- `--resume` отвечает «nothing to resume»: статус не `awaiting_human`.
- «is a live run ... pass --live» или «is a mock run; --live does not apply»: режим запуска записан в `run.json["mode"]` и не меняется.
- Ошибка агента не даёт код 2: она попадает в `run["error"]`, результат со статусом `error` пишется, код выхода 1.
- Как итоговый статус превращается в отчёт и вердикт, см. [[reports]] и [[verdict-gate]]; для теории `docs/THEORY.md`.
