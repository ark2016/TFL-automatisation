---
title: Бюджет и деньги
tags: [deep-agent, budget, settings, middleware]
---

# Бюджет и деньги

Денежная модель TFL Deep Agent на текущем коммите. Связанные заметки: [[architecture]], [[roles-and-subagents]], [[hitl-and-review-modes]], [[run-lifecycle]], [[tfl-lab]], [[testing-and-mock]], [[index]].

Код: `tfl_deep_agent/middleware/budget.py` (вся логика), `tfl_deep_agent/settings.py` (лимиты и проверка), `tfl_deep_agent/settings.example.json` (значения по умолчанию), `tfl_deep_agent/config.py` (`worst_case_usd`, `max_tokens_within`, цены).

> [!warning]
> Менять денежные лимиты (`budget.*`) или выключать точки HITL может только пользователь. Значения по умолчанию записаны в `CLAUDE.md` проекта как решения пользователя от 2026-11-08.

## Настройки (`settings.py`, `settings.example.json`)

Блок `budget` в `settings.json`:

| Поле | По умолчанию | Смысл |
|---|---|---|
| `per_task_usd` | 10.00 | жёсткий потолок одной задачи |
| `per_day_usd` | 20.00 | сумма по всем задачам, начатым в один локальный календарный день |
| `per_subagent_max_usd` | 2.00 | наибольшая выдача одному субагенту или ветке workflow |
| `reserve_for_report_usd` | 1.00 | неприкосновенный остаток на отчёт |

Правила загрузки:

- Файла нет: берётся `DEFAULTS`. `settings.example.json` равен `DEFAULTS` (это закреплено тестом `tests/test_settings.py`).
- Путь: явный аргумент, затем `$TFL_DEEP_SETTINGS`, затем `tfl_deep_agent/settings.json` (в git не попадает).
- Файл может содержать только часть ключей: вложенные объекты сливаются по ключам, списки заменяются целиком.
- Неизвестные ключи, неверные типы и несогласованные значения дают `SettingsError` со списком всех проблем. Сломанный файл никогда не заменяется значениями по умолчанию молча.
- Переопределения CLI и UI (`apply_overrides`, `parse_override_pairs`, форма `budget.per_task_usd=3`) сливаются так же и проверяются заново.
- Проверки `BudgetSettings`: деньги конечны и не отрицательны; `per_task_usd > 0`; `per_task_usd` не больше `per_day_usd`; `per_subagent_max_usd > 0`.

> [!note]
> `per_subagent_max_usd` может быть больше `per_task_usd`: выдача всё равно не превысит остаток родителя. А `reserve_for_report_usd` с `per_task_usd` не сверяется вовсе: реальный резерв считает `report_reserve(per_task, reserve)` как `min(reserve_for_report_usd, per_task_usd / 4)`. Для задачи в $1 с резервом $1.00 реальный резерв равен $0.25. Ledger использует именно `BudgetSettings.report_reserve_usd`.

## Дерево бюджета (`BudgetLedger`, `BudgetNode`)

`BudgetLedger` хранит корневой узел `task` с лимитом `per_task_usd` и резервом. Остальные узлы создаёт `BudgetNode.allocate(name, amount_usd, kind=..., reserve_usd=...)`.

- Выдача сразу вычитается из остатка родителя: это обещание, которое соседи потратить не могут (`_own()` вычитает невозвращённый остаток незакрытых детей).
- Для видов `subagent` и `branch` (`CAPPED_KINDS`) выдача ограничена `per_subagent_max_usd`.
- Выдача не может залезть в резерв родителя: `free = _available() - reserve`.
- Ребёнок не потратит больше своей выдачи и больше остатка любого предка. Если предок перерасходовал из-за неточной оценки, это сужает всех детей под ним.
- `spent` и `inflight` у узла являются суммами по поддереву: расход и резервирования ребёнка прибавляются ко всем предкам.
- `close()` возвращает неистраченную выдачу родителю и закрывает незакрытых детей.
- Одноимённые узлы получают суффикс `#2`, `#3`, ...
- `allocate` бросает `BudgetExceeded` с числами в `details`, если узел закрыт, находится в wrap-up, выдача выше капа или не помещается. Некорректная сумма даёт `ValueError`.
- Все операции идут под одной `RLock` ledger'а. События (`allocate`, `close`, `wrap_up`, `refused`, `call`, `subagent_refused`) уходят подписчикам `listeners` через `ledger._emit` после выхода из собственного блока блокировки узла; ошибка подписчика учёт не ломает. Исключение: `close()` закрывает вложенных детей рекурсивно внутри блока блокировки, поэтому событие `close` ребёнка, закрытого вместе с родителем, подписчики получают, пока `RLock` ещё удерживается.

`BudgetNode.state` принимает значения `normal`, `wrap_up`, `exhausted`. `in_wrap_up()` истинно, если в wrap-up или exhausted сам узел или любой его предок.

### Субагенты: `SubagentBudgetMiddleware`

Оборачивает вызов инструмента `task` (список задаёт `scoped_tools`). Перед вызовом выделяет `min(per_subagent_max_usd, свободное выше резерва родителя)`, запускает инструмент с этим ребёнком как активным scope (`enter_scope`, contextvar `_ACTIVE_SCOPE`) и в `finally` закрывает ребёнка. Неистраченное возвращается родителю.

- Резерв ребёнка по умолчанию: `min(резерв корня, выдача / 4)`, не больше половины выдачи.
- Минимальная выдача `min_allocation_usd` = 0.001. Если выдать нечего (wrap-up, нет свободных денег), инструмент не запускается, а модель получает `ToolMessage` со статусом error и числами. Такой отказ увеличивает `refusals` родителя, но не помечает его `exhausted`.
- `BudgetMiddleware.node` берёт активный scope, если он лежит под узлом этого middleware. Поэтому один раз собранный граф субагента при каждом вызове списывает со свежей выдачи, а параллельные вызовы (каждый в своём контексте) списывают каждый со своей.

## Допуск вызова (worst-case admission)

Источник: `middleware/budget.py`, `config.worst_case_usd`.

1. `BudgetMiddleware.estimate` локально (без API) оценивает токены промпта: `estimate_input_tokens` (3.0 ASCII-символа на токен, 1.5 для не-ASCII, например кириллицы; 6 токенов на сообщение; 600 фиксированных; 7000 на изображение; 6000 на страницу PDF, 20 страниц, если число неизвестно). Оценка умножается на `_scale`: наибольшее наблюдённое отношение «факт/оценка» (только вверх, не больше 4.0).
2. Наихудшая стоимость: `config.worst_case_usd(model, input_tokens, max_tokens)` — каждый токен промпта по большей из ставок input и cache-write плюс `max_tokens` выходных токенов. Если промпт длиннее `LONG_PROMPT_THRESHOLD_TOKENS` и модель есть в `MODEL_PRICING_LONG_PROMPT`, берётся отдельная ценовая карта (тест `test_usage_cost_prices_cache_separately_and_haiku_long_prompt` проверяет её для Haiku). Обе таблицы импортируются из `agent_system/lib/llm_client.py`. Неоцененная модель даёт `UnknownModelError`, а не `None`.
3. `admit` резервирует эту сумму (`inflight`) у узла и всех предков, поэтому параллельные ветки не могут перебронировать деньги. После ответа `settle` заменяет резервирование фактической стоимостью; при ошибке `release` снимает резерв.
4. Лимит допуска: вне wrap-up `avail - reserve`, в wrap-up `avail` целиком. `avail` — это `_available()`, то есть минимум по цепочке предков и по остатку дня.
5. Урезание `max_tokens` и резервирование происходят под одной блокировкой, так что параллельный сосед не перехватит деньги между ними.

Порядок middleware важен: бюджет должен стоять внутри любого middleware, подменяющего `request.model` (он оценивает ту модель, которую видит). Если ответ пришёл без `usage_metadata`, списывается худший случай (`missing_usage="worst_case"`) или ноль (`"zero"`, для сценарных прогонов с фейковой моделью).

## Производный `max_tokens`

Метод `BudgetMiddleware.derive`; арифметика в `config.max_tokens_within`.

`max_tokens = min(потолок, floor((room - худший input) / цена output))`, где потолок даёт `BudgetMiddleware._max_tokens`: первое положительное целое по порядку `request.model_settings["max_tokens"]`, затем `request.model.max_tokens`, затем `max_tokens` самого middleware, иначе `config.resolve_max_tokens(role, model)` (то есть `MAX_TOKENS` роли). Выбор идёт по приоритету, а не по минимуму: значение из `model_settings` (например 8192 у summary) побеждает, даже если оно больше. Результат попадает в запрос через `request.model_settings["max_tokens"]`.

- В нормальном режиме `room` — свободное выше резерва, но дополнительно применяется `_cap_leaving_next`: `max_tokens` не должен мешать оплатить ещё один вызов на минимуме с выросшим промптом (вывод этого вызова плюс `NEXT_CALL_MARGIN_TOKENS` = 2048). Это бисекция, а не формула, потому что цена может скачком расти с длиной промпта.
- Пол — `MIN_OUTPUT_TOKENS` = 4096 (или потолок, если он ниже). Ниже пола вызов не делается.
- Если в нормальном режиме меньше пола, вызов переводит узел в wrap-up. В wrap-up вызову по-прежнему оставляют возможность ещё одного вызова на полу; лишь когда и это не помещается, последний вызов получает всё оставшееся.
- Если ничего не помещается, возвращается оценка на полу, которую `admit` затем отказывает.
- `fit_max_tokens=False` оставляет фиксированный потолок; нужен только тестам, которые закрепляют эту арифметику.

## Wrap-up и отказ

Wrap-up наступает, когда следующий вызов не помещается без резерва даже на полу или не оставляет оплатимым вызов на полу.

В wrap-up:

- вызову разрешено тратить резерв (это вызов отчёта);
- к каждому следующему запросу добавляется `HumanMessage` с текстом `WRAP_UP_INSTRUCTION` (у корня, с упоминанием `submit_report`) или `WRAP_UP_INSTRUCTION_CHILD` (у субагента), с пометкой `additional_kwargs["tfl_budget"] = "wrap_up"`;
- инструменты `task`, `spawn_subagent`, `run_workflow` (`DEFAULT_SPAWNING_TOOLS`) отказываются: `ToolMessage` со статусом error;
- новые выдачи (`allocate`) отказываются.

Отказ: если худший случай не помещается даже на полу, вызов не делается. Middleware возвращает `AIMessage` без вызовов инструментов с текстом `REFUSAL_TEXT` (числа также лежат в `response_metadata["tfl_budget"]`, `stop_reason = "budget_exhausted"`). Цикл агента на этом заканчивается, а узел помечается `exhausted`; рендерер строит частичный отчёт со статусом `budget_exhausted`. `BudgetLedger.exhausted` истинно, если отказ был где угодно в дереве. См. [[reports]].

## Фактический расход

`usage_counts` переводит `usage_metadata` в четыре корзины. langchain-anthropic сообщает `input_tokens` как весь промпт, поэтому обычный input равен `input_tokens - cache_read - cache_creation`. `usage_cost_usd` считает по ценам `config`.

Каждый списанный вызов — `CallRecord` с полем `outcome`:

- `ok` — обычный ход агента;
- `refusal` — ответ был получен, затем исключение (например `ModelRefusalError`): у исключения берётся `raw_message` или `ai_message`, и расход списывается по его usage;
- `cancelled` — отмена (`CancelledError` и подобное, не `Exception`): списывается худший случай, на случай если запрос ушёл и оплачен;
- `summary` — вызов суммаризации.

Любая другая ошибка до ответа (ошибка API): резерв снимается, ничего не списывается.

Суммаризация deepagents по умолчанию вызывает модель мимо пользовательских middleware и не списывалась бы. `budgeted_summarizer` пропускает её через `BudgetMiddleware` (`SUMMARY_MAX_TOKENS` = 8192). При отказе вместо summary возвращается `SUMMARY_REFUSED_TEXT`, модель не вызывается.

## Подключение

Источники: `agent.py` (`create_tfl_agent`, `_budget_scope`), `hitl.py` (`_budget_ledger`), `middleware/budget.py` (`use_budget`, `make_middleware`).

- Живые запуски (`mode == "live"` или живая фабрика моделей): ledger строится через `BudgetLedger.for_task(settings, task_id=run_id, resume=...)`, то есть с проверкой дня и общим журналом. `create_tfl_agent` с живой фабрикой и `budget=None` вне `use_budget` сам вызывает `for_task`; `budget=False` для живой фабрики даёт `ValueError`. Возобновление (`resume=True`, если у запуска больше одного сегмента) стартует с `task_total` из журнала.
- Сценарные (mock) запуски в `hitl._budget_ledger`: `BudgetLedger(...)` напрямую, `spend_log=None`, без `per_day_usd`; уже потраченное берётся из `run["budget"]` (`already_spent_usd`) и записи `records` переносятся в новый ledger. День не проверяется, журнал не пишется.
- `create_tfl_agent(budget=None)` вне `use_budget` с неживой фабрикой заводит ledger в памяти (`per_day_usd=None`, `spend_log=None`) из раздела `budget` настроек. Внутри внешнего `use_budget` берётся его текущий scope. В `create_tfl_agent` и `hitl` передаются `budget=` и `budget_options=`; конструкция `with use_budget(ledger, missing_usage="zero")` описана в docstring `middleware/budget.py`, и `_budget_scope` применяет её сам.
- `make_middleware` и `make_subagent_middleware` возвращают `None`, если нет ни `node=`, ни активного `use_budget`. Это свойство только этих двух функций, а не всего пути запуска.
- `no_budget()` отключает бюджетный middleware при построении агента даже внутри внешнего `use_budget` (`create_tfl_agent(budget=False)`).
- Модель роли берётся из `settings.models.roles` через `config.resolve_model`, так что `TFL_MODEL_OVERRIDE` по-прежнему главнее.

## Журнал дня (`DailySpendLog`)

- Файл `deep_spend_log.jsonl` в каталоге запусков: `$TFL_DEEP_RUNS_DIR` или `<repo>/.tfl_lab_runs` (в git не попадает). Журнал пишут только ledger'ы, построенные через `for_task` (живые запуски); CLI и TFL Lab делят один файл.
- Append-only JSONL: одна строка на списанный вызов, поля `day`, `ts`, `task_id`, `usd` и поля `CallRecord`. Дни — локальные календарные.
- Нечитаемые строки пропускаются и считаются в `bad_lines`; файл не переписывается. Чтение кэшируется по `(размер, mtime)`.
- `check_can_start(per_task_usd, per_day_usd)` бросает `DailyBudgetExceeded`, если `per_task_usd` не помещается в остаток дня. Эта проверка выполняется в `BudgetLedger.for_task` (параметр `check_day`).
- Каждый вызов дополнительно проверяется по остатку дня (`_day_remaining` = `per_day_usd` минус итог дня минус `inflight` корня), потому что две задачи могут идти одновременно.
- `resume=True` (возобновление после HITL в новом процессе): проверка дня пропускается, корень стартует с `task_total(task_id)` — суммы, уже записанной в журнале за эту задачу за любой день. Для `resume` обязателен `task_id`. См. [[hitl-and-review-modes]], [[run-lifecycle]].

> [!warning]
> Журнал дня учитывает только ledger'ы с `spend_log`, то есть построенные через `BudgetLedger.for_task`. Сценарные запуски и ledger'ы в памяти (`spend_log=None`) в него ничего не пишут, и дневной лимит такие запуски не видят.

## Что смотреть при отладке

- Вызов отказан неожиданно: числа в `REFUSAL_TEXT` и `response_metadata["tfl_budget"]` (`available_usd`, `inflight_usd`, `day_remaining_usd`). Частая причина — длинный промпт на Opus, делающий худший случай дорогим, или остаток дня.
- Сильно урезанный `max_tokens`: вызов `derive`; `CallRecord.max_tokens` и `estimated_input_tokens` показывают, с чем вызов был допущен.
- Субагент не запускается: `ToolMessage` с `tfl_budget = "subagent_refused"` и событие `subagent_refused`.
- Статус для UI и `budget_status`: `BudgetNode.status()` (дерево с `limit_usd`, `spent_usd`, `inflight_usd`, `available_usd`, `state`) и `BudgetLedger.summary()` (расход по ролям и моделям).
- Цены и соотношение ставок лежат в `config.py` (`MODEL_PRICING` из `agent_system/lib/llm_client.py`); не копируйте их в другие места.
- Живые запуски стоят реальных денег и разрешены только по явной просьбе пользователя; см. [[testing-and-mock]].
