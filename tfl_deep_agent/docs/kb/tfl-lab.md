---
title: TFL Lab - рабочая область Deep Agent
tags: [deep-agent, tfl-lab, ui, api]
---

# TFL Lab: вкладка Deep Agent

TFL Lab (`python -m ui_server.server --port 8765`) - локальный веб-интерфейс. Вторая рабочая область, **Deep Agent**,
открывается переключателем в верхней панели или адресом `http://127.0.0.1:8765/#deep`. Агент в ней не живёт в процессе
сервера: каждый запуск и каждое продолжение - отдельный дочерний процесс `python -m tfl_deep_agent` (см. [[run-lifecycle]]).

Источники: `ui_server/deep_routes.py` (бэкенд, docstring модуля - полный список маршрутов), `ui_server/static/deep.js`
(фронтенд), раздел "TFL Lab tab" в `tfl_deep_agent/README.md`. Слои агента, которые UI только показывает:
[[hitl-and-review-modes]], [[budget]], [[reports]], [[intake]], [[testing-and-mock]].

> [!note] Где что менять
> Маршруты, процессы, слоты, форма `pending_point` - `ui_server/deep_routes.py`. Чат, карточка проверки, панель отчёта,
> окно настроек - `ui_server/static/deep.js`. Разметка и стили - `ui_server/static/index.html`, `ui_server/static/lab.css`.
> Модуль `tfl_deep_agent` в `deep_routes.py` импортируется лениво, поэтому остальной TFL Lab работает без extra `[deep]`;
> при `ImportError` маршрут отвечает 503.

## Бэкенд: маршруты

Всё под префиксом `/api/deep/`. Вход - функция `dispatch(method, path, query, payload)`, возвращает `(HTTP-статус, JSON | FileResponse)`.

| Маршрут | Назначение |
|---|---|
| `GET /runs`, `POST /runs` | список (новые первыми) / создание запуска |
| `GET /runs/<id>?after=N&limit=M` | деталь запуска плюс события `progress.jsonl` с индекса N |
| `GET /runs/<id>/files/<path>` | отдача файлов запуска (белый список, ниже) |
| `POST /runs/<id>/resume` | решение по HITL-остановке |
| `POST /runs/<id>/cancel` | отмена |
| `POST /runs/<id>/render` | показать принятый отчёт в другом шаблоне |
| `GET /estimate` | лимиты, расход за день, измеренная цена прошлых live-запусков |
| `GET /settings`, `PUT /settings` | настройки агента |
| `GET /mock-scripts` | сценарии из `tfl_deep_agent/examples/mock` |

Коды ответов задаются исключениями: `BadRequest` (и любой `ValueError`) - 400, `NotFound` - 404, `Conflict` - 409,
`ImportError` - 503, остальное - 500.

### Создание запуска (`api_create`)

- Поля тела ограничены `CREATE_KEYS`; неизвестное поле - 400. Ровно одно из `text` / `file_b64` (+ `mime`, `name`).
- **Деньги.** Режим по умолчанию mock: нужен `mock_script` (имя из `/mock-scripts` или объект с непустым `models`).
  `live: true` требует `confirm_spend: true` и исключает `mock_script`. Сервер не читает `.env`: ключ должен быть в его окружении.
  Mock-потомок получает окружение без `ANTHROPIC_API_KEY` (`_child_env`).
- `auto_approve` - только mock, это псевдоним `review_mode: "auto"`; вместе с другим `review_mode` - 400.
  Живой запуск выбирает `review_mode` явно.
- `settings_override` накладывается на сохранённые настройки через `apply_overrides`. Для **live** действует ограничение
  `_live_override_problems`: лимиты `per_day_usd`, `per_task_usd`, `per_subagent_max_usd` можно только понижать, а точки
  `hitl.formalization` и `hitl.report`, включённые в сохранённых настройках, нельзя выключить для одного платного запуска.
  Повысить лимит или отключить проверку можно только через `PUT /settings` - это осознанное постоянное решение.
- Для live до старта вызывается `_check_day_budget` (`DailySpendLog.check_can_start`); отказ - 409.
- Битый файл настроек - 409 (`load_deep_settings`), а не молчаливая подстановка умолчаний: в файле денежные лимиты.
- Размер тела: `max_body_bytes()` = `intake.max_file_mb * 4/3` плюс 1 MB запаса, не больше 64 MB (base64 раздувает файл).

### Каталог запуска

`.tfl_lab_runs/deep/<12 hex>/` (`DEEP_RUNS_DIR`). Реестр пайплайнов в `server.py` смотрит только 12-hex каталоги прямо
под `.tfl_lab_runs`, поэтому эти запуски не видит. Владельцы файлов разделены:

- агент: `run.json`, `checkpoints.sqlite`, `evidence.jsonl`, `progress.jsonl`, `result.*`, `hitl.md`;
- `deep_routes.py`: `lab.json` (статус процесса, pid, сегменты), `run.log` (stderr потомка), `upload/` (файл задачи),
  `ui_settings.json` (снимок настроек запуска), `ui_mock_script.json`, `ui_decisions/`, `views/`.

Через `GET .../files/<path>` отдаются только `result.{json,md,html}`, `hitl.md`, `evidence.jsonl`, `progress.jsonl`,
`run.log` и каталоги `input/`, `upload/`, `views/` (`SERVED_FILES`, `SERVED_DIRS`). Новый файл для UI надо добавить туда.

Снимок настроек (`ui_settings.json`) передаётся потомку как `--settings`, поэтому правка настроек во время запуска
на него не влияет.

## Процессы, слоты, статусы

Каждый старт и каждое продолжение - поток `_worker`, который запускает `subprocess.Popen` (`_spawn_and_wait`) с
`stdin=DEVNULL`, читает stderr (в `run.log`, буфер хранит 20 строк, но в сообщение об ошибке идут последние 10) и stdout (с префиксом `[stdout]`).
Команды собирают `build_start_command` (`--file ... --save ... --settings ... --no-dialog`, плюс `--template`, `--live` или
`--mock-script`, `--auto-approve` или `--review-mode`) и `build_resume_command` (`--resume ... --decision ... --payload @file`,
`--approve-rest`, `--live`).

**Слоты** (`_acquire_slot` / `_release_slot`, условие `_slots`):

- одновременно не больше `MAX_CONCURRENT_RUNS` процессов (переменная `TFL_LAB_MAX_DEEP_RUNS`, по умолчанию 2);
- live - не больше `MAX_CONCURRENT_LIVE_RUNS = 1`. Причина в коде: у каждого live-процесса свой `BudgetLedger`, он вычитает
  из общего журнала расхода только собственные резервы, и два параллельных процесса могли бы вместе превысить `per_day_usd`;
- сирота-live (процесс пережил рестарт сервера и ещё работает) тоже держит live-слот (`_live_orphan_running`).

Пока запуск ждёт слот, статус `queued`; отмена в очереди даёт `cancelled`.

**Статусы лаборатории** (отличаются от статусов агента): `queued`, `running` (активные), `awaiting_human`, терминальные
`completed`, `error`, `cancelled`, `interrupted`. Статус агента (`submitted`, `incomplete`, `budget_exhausted`, `error`)
попадает в `result_status`. Перевод после выхода процесса делает `_settle_from_disk_locked` по `run.json` агента и коду выхода
(`0` сдан, `1` нет отчёта, `2` ошибка использования, `3` остановка на HITL):

- `run.json` в `awaiting_human` с `pending` - статус остаётся `awaiting_human` при любом коде выхода. Если продолжение упало
  до применения решения (например, отказ CLI из-за `--live` без ключа), запуск остаётся продолжаемым, а причина кладётся в `error`;
- статус агента `submitted`, `incomplete` или `budget_exhausted` при коде `None`, `0` или `1` - `completed`, статус агента в `result_status`;
- статус агента `error` при коде `None` или `1` - `completed`, если есть `result.json`, иначе `error` (текст берётся из `error` агента), `result_status = error`;
- иначе код `None` - `interrupted`;
- прочее - `error` с кодом выхода и хвостом stderr (последние 10 строк).

> [!warning] Windows и `run.json`
> Пока процесс запуска жив, `run.json` читать нельзя: на Windows читатель с открытым файлом ломает атомарную замену на
> стороне агента (нарушение совместного доступа). Поэтому `_agent_run_if_idle` читает `run.json` только у неактивных запусков,
> а живой запуск показывается по `progress.jsonl` (append-only). То же правило в `_past_live_costs`.

**Отмена** (`api_cancel`): активный запуск - `cancel_requested` и `_kill` (на Windows `taskkill /T /F`, убивает дерево).
Запуск в `awaiting_human` отменяется сразу без процесса и **потом продолжить его нельзя**. На терминальном статусе идемпотентна.

**Продолжение** (`api_resume`):

1. Тело ограничено `RESUME_KEYS`; `decision` одно из `approve`, `edit`, `reject`; `approve_rest` только вместе с `approve`.
2. Запуск должен быть в `awaiting_human` и в `run.json` должен быть `pending`, иначе 409. Для live снова нужен `confirm_spend`.
3. `reject` требует комментарий. `_prevalidate` проверяет решение в этом же процессе (`hitl.parse_decision`, `hitl.resume_value`,
   в том числе id доказательств правки отчёта по `evidence.jsonl`): несоответствие даёт 400, а запуск продолжает ждать.
4. Полезная нагрузка пишется в `ui_decisions/NNN-<point>-<decision>.json` и передаётся как `--payload @файл`.
5. `approve_rest` переводит запуск в `review_mode = auto`; агент записывает то же в свой `run.json` (источник истины).

**Рестарт сервера** (`restore_runs`): запуски перечитываются из `lab.json`. Запись `queued`/`running` без живого процесса
оседает через тот же `_settle_from_disk_locked` с кодом `None` (правила выше: остановка HITL остаётся `awaiting_human`,
финальный статус агента даёт `completed` или `error`, `interrupted` - только если ничего из этого не подошло); живой процесс усыновляется как
`orphan` и наблюдается потоком `_watch_orphan` (опрос раз в 2 с, отмена убивает процесс по pid и признаку identity).
Ожидающий человека запуск процесса не требует, поэтому рестарт ему не вредит.

## Деталь запуска: что отдаёт `GET /runs/<id>`

`api_detail` читает `progress.jsonl` с индекса `after` (контракт опроса тот же, что у `/api/runs/<id>` пайплайнов; читает
`_read_progress` из `server.py`) и отдельно сворачивает **весь** файл функцией `fold_events`. Поэтому клиент, подключившийся
поздно, всё равно получает план (`todos`), счётчики инструментов по доверию (`tools`), дерево субагентов с моделями и ценой
(`subagents`), бюджет, сигналы бюджета и историю решений. Ключи ответа: `run`, `pending_point`, `review_mode`, `events`,
`next`, `total`, `usage`, `todos`, `tools`, `subagents`, `orchestration`, `budget` (с блоком `day`), `budget_signals`,
`hitl`, `result` (`null`, пока запуск активен), `done`, `log_tail` (последние 50 строк).

**`pending_point`** - карточка остановки, строится кодом без вызова модели (`pending_card`). Источники по порядку:
`pending.card` из `run.json`; карточка из `hitl.pending_card` по ожидающему запросу; кэш `pending_card`, записанный при
оседании; минимальная карточка из последнего события `hitl_awaiting` и `hitl.md`. Для запуска не в `awaiting_human` - `null`.
Поля: `point` (`formalization` | `report` | `execute`), `title`, `summary` (до 4 строк), `details_md`, `editable`.

В `hitl.pending` лежат все вызовы инструментов остановки: один interrupt может нести несколько вызовов, решение
применяется ко всем, поэтому `allowed_decisions` - пересечение разрешённых по каждому, а при нескольких вызовах `edit`
убирается (`resume_value` требует ровно одно действие). Детали политики - в [[hitl-and-review-modes]].

`review_mode` берётся из `run.json` агента, иначе из записи лаборатории (`_review_mode`).

## Просмотр отчёта в другом шаблоне (`api_render`)

`POST /runs/<id>/render {template}` только для завершённого запуска с принятым отчётом (`status == submitted`,
`_can_render`). Шаблон должен входить в `render.SELECTABLE_TEMPLATES`. Результат пишется в `views/<template>.{md,html}`,
`result.*` не переписывается: это то, что человек принял на HITL 2. Вызова модели нет, расхода нет. Если шаблон совпадает
с текущим, ответ `same_as_result: true` со ссылками на `result.*`. Про сам рендер - [[reports]].

## Фронтенд (`deep.js`)

Файл подключается после `app.js` и использует его помощники (`$`, `el`, `api`, `sendJson`, `num`, `fmtUsd`, `toMs`, `fmtDur`,
`fmtWhen`, `ensureMdLibs`, `preprocessObsidianCallouts`). Он делится на две части:

- **Чистые функции** между маркерами `// ---- deep: pure helpers begin` и `... end` - без DOM. Их исполняет под node тест
  `ui_server/tests/test_deep_frontend.py`, поэтому логику (сборка тел запросов, правки, свёртка событий) держите в этом блоке,
  а DOM-код - после него.
- **Состояние и DOM** - объект `deep` (запуски, текущий `runId`, `events`, `cursor`, `pollGen`, ключи перерисовки) и обработчики.

### Опрос

`deepOpenRun` обнуляет состояние и запускает `deepStartPolling`: цикл раз в 800 мс запрашивает
`/deep/runs/<id>?after=<cursor>`, дописывает события и сдвигает `cursor` на `next`. Счётчик `pollGen` отбрасывает ответы
устаревших циклов. Опрос останавливается, когда статус не `queued`/`running`; ожидание решения опрос **не** держит,
поэтому после `resume` и после отмены опрос запускается заново. Список запусков обновляется раз в 3 с, пока есть активные.
Заголовок вкладки браузера получает префикс ⏸, пока какой-то запуск ждёт решения.

### Композер

Текст, либо файл (PDF, PNG, JPG, WebP; перетаскивание, выбор или вставка картинки), передаётся как base64 в JSON.
Текстовый файл (`.txt`, `.md`) просто подставляется в поле. Размер проверяется по `intake.max_file_mb` из настроек, но
сервер проверяет ещё раз (`intake`, см. [[intake]]). `Ctrl+Enter` запускает только mock; live - кнопкой.

`deepCreateBody` собирает тело `POST /runs` и повторяет серверные запреты до отправки: для live нужен флажок
"подтверждаю расходы", нельзя поднять бюджет выше сохранённого, нельзя выключить точки проверки. `deepOverride` посылает
в `settings_override` только то, что отличается от сохранённых настроек (бюджет задачи, основная модель, точки `hitl`).
Режимы проверки в селекторе: `all` "Подтверждать всё", `formalization` "Только формализацию", `auto` "Без остановок".

Совместимость: если сервер отвечает 400 с упоминанием `review_mode`, форма один раз переходит на старый вариант
(`deep.legacyCreate`: режим превращается в флаги `hitl` и `auto_approve`). Это код фронтенда для старых серверов,
для текущего бэкенда он не нужен.

Строка оценки под композером (`deepEstimateLine`) берётся из `GET /estimate` и показывается только для live; при
исчерпанном дневном лимите (`day.can_start === false`) сообщает, что сервер откажет.

### Чат

`deepChatItems(events)` превращает события `progress.jsonl` в элементы ленты:

- подряд идущие `model_call`/`tool_call` главного агента - одна свёрнутая строка `activity` ("4 инструмента · haiku · $0.0040");
- область субагента, `spawn_*` или `workflow_*` - элемент `agent` с вложенной активностью и шагами workflow;
- первое событие `todos` - элемент `plan` (в нём показывается последний план);
- `hitl_decision` - строка решения ("✓ формализация принята · вы"), `budget_wrap_up`/`budget_refused`/`subagent_refused`/
  ошибка сегмента - заметка, `done` - итоговая карточка.

Вызов `write_todos` и вызов `task`, для которого уже есть строка агента, в ленту отдельно не попадают.
Перерисовка ленты пропускается, пока не изменился ключ (`feedKey`: число событий, раскрытые строки, наличие результата).

### Карточка проверки

`deepPendingView` читает `pending_point` (при его отсутствии у старых серверов - запасной разбор `hitl.pending`),
`deepReviewCard` рисует заголовок, до 4 строк, "подробнее" (рендер `details_md`) и кнопки. Набор кнопок определяется
`allowed` из ответа сервера. "Принять всё до конца" отправляет `approve_rest` и недоступна, если сервер не прислал
`pending_point`. Для live карточка требует отдельный флажок подтверждения расходов. Правки (`deepEditor`) собираются
чистыми функциями `deepHitl1Edits` (транскрипция, IR, класс), `deepReportEdits` (поля `DEEP_REPORT_FIELDS`, шаги как JSON)
и `deepArgsEdits` (прочие остановки) и отправляют только изменённые поля; смена класса без нового IR отклоняется на клиенте.
`deepResumeBody` повторяет серверные проверки (комментарий при `reject`, непустой `payload` при `edit`).

### Панель отчёта

`deepPanelSource` решает, что показывать справа: на остановке `report` - черновик (`draft_urls` из `hitl.pending`),
после завершения - `result.urls`. HTML-отчёт открывается в `iframe` с `sandbox="allow-scripts"` (скрипты отчёта работают,
доступа к origin нет); если HTML нет, Markdown рендерится через `marked` + `DOMPurify` + KaTeX
(`deepRenderMarkdownInto`, весь текст агента проходит санитайзер). Селектор шаблона в панели появляется, только если
`result.can_render`; он вызывает `/render` и держит выбранный вид в `deep.viewTpl`, подписывая "вид в шаблоне X; отчёт принят как Y".
Вторая вкладка панели - журнал (`log_tail`, ссылки на `run.log` и `progress.jsonl`). Панель можно закрыть и расширить.

### Окно настроек

Кнопка ⚙ в списке запусков открывает модальное окно. `GET /settings` отдаёт `settings`, `defaults`, `path`, `templates`,
`schema`, `error`; при битом файле показываются умолчания и текст ошибки. Форма (`deepBuildSettingsForm`) покрывает бюджет
(`per_task_usd`, `per_day_usd`, `per_subagent_max_usd`, `reserve_for_report_usd`), разрешённые модели и модели ролей,
`models.opus_needs_reason`, точки `hitl` (`formalization`, `report`, `execute`), лимиты (`limits.*`) и приём файлов (`intake.*`).
Сохранение - `PUT /settings` со всем деревом из формы; сервер сливает его поверх текущих настроек (или умолчаний при битом файле),
проверяет и пишет атомарно (`save_settings`); невалидное - 400 и файл не меняется.

> [!warning] Деньги и проверки - решение владельца
> Изменение денежных лимитов и выключение HITL-точки через это окно - решение пользователя, а не рабочая подстановка
> агента (см. `CLAUDE.md` проекта и [[budget]]). Запуск-за-запуском их можно только ужесточать (правила выше).

## Что проверить при отладке

- Запуск "завис" в `queued`: посмотрите слоты (`TFL_LAB_MAX_DEEP_RUNS`, единственный live-слот, живой сирота после рестарта).
- Запуск в `awaiting_human` без карточки: порядок источников `pending_card`; `hitl.md` читается как последний запас.
- Статус `error` после продолжения: сравните `error` с кодом выхода и хвостом `run.log`; остановка не теряется, если
  `run.json` всё ещё в `awaiting_human`.
- Файл не открывается из UI: проверьте белый список `SERVED_FILES`/`SERVED_DIRS`.
- Тесты бэкенда и фронтенда: `ui_server/tests/test_deep_routes.py`, `ui_server/tests/test_deep_frontend.py`
  (запускать только mock, см. [[testing-and-mock]]).
