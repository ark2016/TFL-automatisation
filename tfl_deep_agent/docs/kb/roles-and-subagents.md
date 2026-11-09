---
title: Роли и субагенты
tags: [deep-agent, roles, subagents, orchestration, model-policy]
---

# Роли и субагенты

Связанные заметки: [[architecture]], [[budget]], [[tools-and-evidence]], [[verdict-gate]], [[hitl-and-review-modes]], [[deepagents-usage]], [[testing-and-mock]].

Заметка описывает три механизма делегирования у главного агента: фиксированные роли (инструмент `task` из deepagents), ad-hoc субагентов (`spawn_subagent`) и план шагов (`run_workflow`), а также политику выбора модели для каждого из них.

## 1. Фиксированные роли

Источник: `tfl_deep_agent/roles.py`, `tfl_deep_agent/prompts/*.md`, сборка в `tfl_deep_agent/agent.py`.

Роль описывается неизменяемым dataclass `Role` (`key`, `name`, `description`, `prompt`, `tool_groups`, `allow_sandbox`, `skills`, `default_model`, `inline_prompt`). Словарь `ROLES` задаёт семь ролей; `ROLE_BY_NAME` отображает имя субагента в ключ роли.

| key | имя субагента (`task(subagent_type=...)`) | группы инструментов | промпт | примечание |
|---|---|---|---|---|
| `intake` | `intake` | нет (только встроенное чтение файлов из `/input`) | `prompts/intake.md` | без skills |
| `ir_builder` | `ir-builder` | `ir`, `words` | `prompts/ir_builder.md` | |
| `prover` | `prover` | `theory`, `ir`, `reg`, `cfl`, `dcfl`, `ll`, `words` | `prompts/prover.md` | |
| `critic` | `critic` | те же, что у `prover` | `prompts/critic.md` | чистый контекст |
| `lean` | `lean-formalizer` | `lean`, `theory` | `prompts/lean.md` | |
| `coder` | `coder` | нет, но разрешён встроенный `execute` | `prompts/coder.md` | без skills; только при наличии песочницы |
| `general` | `general-purpose` | `theory` | встроенная строка `_GENERAL_PROMPT` | без skills, модель по умолчанию Haiku |

Неочевидные правила:

- Ключ роли (`ir_builder`, `lean`) и имя субагента (`ir-builder`, `lean-formalizer`) различаются. Ключ используется в `settings.json` (`models.roles`) и в `config.MODELS`, имя — при вызове `task`. В `run_workflow` поле `role` принимает и то и другое (`_role_key` в `orchestration/workflow.py` сначала ищет значение в `ROLE_BY_NAME` (имя `ir-builder` даёт ключ роли), и только потом заменяет `-` на `_`; порядок важен: для `general-purpose` обратный порядок дал бы несуществующий ключ `general_purpose`).
- `general` существует только затем, чтобы заменить встроенный `general-purpose` из deepagents: тот по умолчанию получил бы модель главного агента и все его инструменты. `agent.py` всегда добавляет `general` в список ролей, даже если вызывающий его не указал. У него нет оракулов и `submit_report`, и промпт запрещает ему решать вопросы теории.
- Инструмент `execute` (`SANDBOX_ONLY_TOOLS`) оставляется только роли с `allow_sandbox=True`, то есть `coder`. Остальным агентам, включая главного, он вырезается middleware `exclude_tools_middleware`. Если песочница недоступна (`sandbox.available` ложно), `agent.py` выбрасывает из списка роли с `allow_sandbox`, и `coder` не строится.
- `build_subagents(...)` превращает роли в `SubAgent`-словари deepagents: `name`, `description`, `system_prompt`, `model`, `tools`, `middleware` и, если у роли `skills=True` и есть источники, `skills`. Модель и middleware передаются колбэками `model_for_role` и `middleware_for_role`.
- В `agent.py` модель на роль создаётся один раз (кэш `models`), и ею же пользуется суммаризация. Middleware роли: лимиты вызовов, затем бюджетный и прочие из `default_middleware`, при необходимости фильтр `execute`. Подробнее о бюджетных хуках см. [[budget]], о фиксации доказательств см. [[tools-and-evidence]].
- `role_tools(role)` подгружает нужные группы из реестра P1 (`tools/base.py`) лениво и отдаёт LangChain-инструменты.
- `description` роли — то, что главный агент читает, выбирая роль. Правите описание — меняете поведение выбора.

> [!note] Промпты
> Все промпты ролей лежат в `tfl_deep_agent/prompts/` (`main.md`, `intake.md`, `ir_builder.md`, `prover.md`, `critic.md`, `lean.md`, `coder.md`). `load_prompt` читает файл в UTF-8 и обрезает хвостовые пробелы. Формулировки теории роли `prover`, `critic` и главный агент берут через `get_theorem` / `list_theorems` из `docs/THEORY.md`. При этом сами промпты `main.md` и `critic.md` тоже кратко пересказывают часть теории (цепочку классов, факты о замкнутости) со ссылками на разделы `docs/THEORY.md`. Если правите теорию, меняйте сначала `docs/THEORY.md`, затем проверьте эти места в промптах.

## 2. Выбор модели для фиксированных ролей

Источник: `tfl_deep_agent/config.py`, `tfl_deep_agent/roles.py` (`role_model_request`), `tfl_deep_agent/agent.py` (`resolve_role_model`), `tfl_deep_agent/settings.example.json`.

Приоритет, по которому `resolve_role_model` выбирает модель роли:

1. `TFL_MODEL_OVERRIDE` (переменная окружения; перебивает всё);
2. явный аргумент `requested`;
3. `settings.models.roles[<ключ роли>]`;
4. `config.MODELS[<ключ роли>]`;
5. `Role.default_model` (только `general`: Haiku);
6. `config.DEFAULT_MODEL` (Opus).

После выбора модель проверяется по `settings.models.allowed` (сравнение по ключу прайс-карты, поэтому датированный id совпадает с алиасом). Если модель не в списке, поднимается `ModelNotAllowedError`. Исключение: роль, у которой только кодовый дефолт (`general`), получает самую дешёвую разрешённую модель вместо ошибки.

Значения по умолчанию из `config.MODELS`: `main`, `prover`, `critic`, `lean` — Opus; `intake`, `ir_builder`, `coder` — Sonnet. Уровень рассуждения `config.EFFORT`: `high` для `main`, `prover`, `critic`, `lean`; `medium` для `intake`, `ir_builder`, `coder`; для ролей вне таблицы (ad-hoc) `DEFAULT_EFFORT = "high"`. `MAX_TOKENS = 64000`, для `lean` в `MAX_TOKENS_PER_ROLE` задано 128000. Это потолки: реальный `max_tokens` вызова ограничивается остатком бюджета (см. [[budget]]).

> [!warning] Синхронность настроек
> `config.py` дублирует id моделей, `EFFORT` и `MAX_TOKENS` из четырёх пайплайнов и по правилам репозитория должен меняться вместе с ними. Цены в нём не копируются: `MODEL_PRICING` реэкспортируется из `agent_system/lib/llm_client.py`.

`resolve_model` и `pricing_key` работают «закрыто»: id без прайс-карты даёт `UnknownModelError`, а не уходит в API. Алиасы `opus` / `sonnet` / `haiku` раскрываются через `normalize_model`.

## 3. `spawn_subagent`

Источник: `tfl_deep_agent/orchestration/spawn.py`.

Главный агент (или ребёнок, которому разрешено) создаёт одного ad-hoc агента через `langchain.agents.create_agent`. Аргументы (`SpawnArgs`, `extra="forbid"`): `name`, `instructions`, `model`, `tools`, `budget_usd`, `output_schema`, `reason`.

Что проверяет `prepare_spawn` (все проблемы собираются в один отказ `OrchestrationError`, чтобы модель исправила их за один заход):

- `name` соответствует `^[a-z0-9][a-z0-9_-]{0,47}$`;
- глубина: главный агент на глубине 0, ребёнок на глубине вызывающего плюс 1, не более `limits.max_spawn_depth` (по умолчанию 2);
- модель проходит `model_policy.resolve_model_choice` (см. раздел 5);
- инструменты из белого списка (`resolve_tool_names`), не более `MAX_TOOLS = 40`; допускается запись `group:<имя>`;
- `output_schema` не длиннее `MAX_SCHEMA_CHARS = 20_000` символов, `instructions` — `MAX_INSTRUCTIONS_CHARS = 40_000`;
- бюджет: скоуп вызывающего не в состоянии wrap-up; `budget_usd` не больше `per_subagent_max_usd` и не больше свободного остатка над резервом скоупа; суммы хватает хотя бы на один вызов выбранной модели с `max_tokens` на полу `MIN_OUTPUT_TOKENS = 4096` плюс резерв ребёнка.

Правила ребёнка:

- `CHILD_TOOL_GROUPS = ir, words, reg, cfl, dcfl, ll, theory, lean`. Нет группы `report` (то есть `submit_report`) и нет `execute`. Если `tools` пуст, у ребёнка нет инструментов.
- Оркестрационные инструменты (`spawn_subagent`, `run_workflow`) даются, только если названы в `tools` и если глубина ребёнка позволяет ему самому породить потомка; `budget_status` можно называть всегда.
- Системный промпт один на всех: `ADHOC_SYSTEM_PROMPT` в `spawn.py` (не из `prompts/`). Инструкции вызывающего приходят первым пользовательским сообщением. Ребёнок не видит контекста вызывающего.
- Ребёнок получает собственный узел бюджета (`BudgetNode.allocate(kind="subagent")`, имя `spawn:<name>`), резерв `min(резерв корня, сумма/4, сумма/2)` (`child_reserve`); неизрасходованное возвращается вызывающему при закрытии узла (`run_child`).
- Middleware ребёнка (`child_middleware`): лимиты вызовов, `BudgetMiddleware` с `fit_max_tokens=True`, `ConcurrencyMiddleware`, `EvidenceMiddleware`. Вызовы оракулов внутри ребёнка попадают в общий реестр доказательств с общими id.
- `role_key` ребёнка в фабрике моделей и в бюджете: `spawn:<name>`. Фабрика получает пару `(role_key, id модели)`.
- `run_child` не бросает исключение при сбое ребёнка: возвращает статус `error`; `GraphRecursionError` превращается в сообщение о пределе шагов (`2 * max_model_calls + 10`). Прерывания и отмены (`GraphBubbleUp`) пробрасываются.
- Ответ ребёнка обрезается до `MAX_ANSWER_CHARS = 12_000` и **не является доказательством**: результат несёт пометку `NOT_EVIDENCE`, а в списке `evidence` перечислены id вызовов оракулов ребёнка. Метаданные инструмента: `tfl_max_trust = not_verified`. Подробнее о доверии см. [[verdict-gate]].

> [!note] Отказ — это результат инструмента
> Любой отказ возвращается как `ToolResult` с `ok: false` и текстом со списком проблем и подсказкой (`_spawn_hint` содержит остаток бюджета и глубину). Исключение наружу не вылетает. Если нужно понять, почему агент не создал субагента, смотрите этот текст и события `spawn_refused` в `OrchestrationContext.history`.

Вместе с `spawn_subagent` и `run_workflow` регистрируется `budget_status` (`make_orchestration_tools`): бесплатный инструмент, показывает остаток, лимиты, глубину, `can_spawn` и худшую стоимость одного вызова для каждой разрешённой модели (`model_policy.model_cost_table`).

### OrchestrationContext

`OrchestrationContext.create(...)` связывает настройки прогона, `model_factory`, бюджет, реестр доказательств и `RunLimits`. Живая фабрика без бюджета отвергается (`ValueError`). `ctx.limits` можно привязать после сборки графа: дети строятся лениво, при вызове инструмента. `ctx.emit(...)` пишет событие в `history`, оповещает слушателей и бюджетный реестр (оттуда события идут в UI).

## 4. `run_workflow`

Источник: `tfl_deep_agent/orchestration/workflow.py`.

Принимает JSON-план: `steps` (от 1 до `MAX_STEPS = 16`), `budget_usd`, `name`, `dry_run`. Поля шага (`StepSpec`): `id`, `task`, `role`, `model`, `tools`, `depends_on`, `budget_usd`, `reason`, `output_schema`, `on_dependency_failure` (`skip` по умолчанию или `run`).

Порядок работы:

1. `validate_workflow` проверяет весь план и перечисляет **все** проблемы сразу: уникальность `id`, известные `depends_on`, отсутствие самозависимости и циклов (цикл печатается как `a -> b -> a`), модели по политике, инструменты, глубину, ширину слоя, деньги.
2. При `dry_run=true` возвращается только проверка и оценка (`plan.summary()`), ничего не запускается.
3. `execute_plan` выделяет узел бюджета `workflow:<name>` (`kind="workflow"`), компилирует `StateGraph`: узел `dispatch` через условное ребро отправляет `Send("step", ...)` на каждый готовый шаг, узел `step` запускает шаг через `spawn.run_child` в своём узле бюджета (`kind="branch"`). Параллелизм ограничен `max_concurrency = limits.max_parallel`.

Неочевидные правила:

- Роли шага: только `WORKFLOW_ROLES = ir_builder, prover, critic, general, lean`. `intake` (нужен файл) и `coder` (нужна песочница) в шагах недоступны, для них используйте `task`.
- Шаг с `role`, но без `model` берёт модель роли из настроек (`_role_default_choice`, с учётом `TFL_MODEL_OVERRIDE` и `allowed`). Если это Opus и включён `opus_needs_reason`, шагу всё равно нужен `reason`: нельзя запустить несколько Opus-веток, просто опустив `model`.
- Внутри шагов нельзя вызывать `spawn_subagent` и `run_workflow`; файловых инструментов и `/skills/` у шага нет (текст `STEP_FOOTER`), теорию он берёт через `get_theorem` / `list_theorems`.
- Слой шага определяется длиннейшим путём зависимостей; в слое не больше `limits.max_parallel` шагов, иначе план отвергается.
- Бюджет ветки: явный `budget_usd` шага либо равная доля остатка, ограниченная `per_subagent_max_usd`. Каждая ветка должна оплатить минимум один вызов своей модели (с учётом места под ответы зависимостей) плюс резерв. Сумма бюджетов веток (она же оценка верхней границы расхода) обязана уложиться в `budget_usd` плана, а он — в свободный остаток вызывающего.
- Ответ каждой зависимости передаётся зависимому шагу, обрезанный до `DEP_ANSWER_CHARS = 6_000` символов.
- Шаг, чья зависимость не завершилась со статусом `ok`, пропускается, если не указано `"on_dependency_failure": "run"`.
- Итоговый статус плана: `ok` (все шаги ok), `partial` или `failed` (ни одного ok). В результате для каждого шага статус, ответ, модель, стоимость и id доказательств. Как и у `spawn_subagent`, ответ не является доказательством.

> [!warning] IR из ad-hoc агента не проходит ревью
> По `prompts/main.md`, формализация, сделанная в шаге workflow или spawned-субагентом, не доходит до человеческого ревью формализации (HITL 1), а результаты оракулов засчитываются в вердикт только для IR, одобренного на HITL 1. Формализовать для ревью нужно через роль `ir-builder` (`task`) или собственный `validate_ir` главного агента. См. [[hitl-and-review-modes]] и [[verdict-gate]].

## 5. Политика выбора модели для ad-hoc агентов

Источник: `tfl_deep_agent/orchestration/model_policy.py`.

`resolve_model_choice(settings, requested, reason, what=...)` проверяет выбор модели и возвращает `ModelChoice` (`requested`, `tier`, `model`, `effective_model`, `overridden`, `reason`). Порядок проверок:

1. Модель задана строкой и не пуста (иначе ошибка со списком допустимых уровней).
2. Это уровень (`haiku` / `sonnet` / `opus`) либо полный id с прайс-картой; неизвестное отвергается.
3. Модель входит в `settings.models.allowed` (по ключу прайс-карты). Если списка нет, разрешены все три.
4. Для Opus при включённом `settings.models.opus_needs_reason` (по умолчанию включён) нужен `reason` не короче `MIN_REASON_CHARS = 8` символов. Он попадает в результат инструмента и, через аргументы вызова, в реестр доказательств.
5. Затем применяется `TFL_MODEL_OVERRIDE`: реально собирается переопределённая модель, а `overridden: true` показывается в результате. Проверки 1–4 при этом всё равно выполняются по исходному выбору, поэтому план, отвергнутый в боевом прогоне, отвергается и в прогоне с override.

Подсказки по уровням (`TIER_GUIDE`; они подставляются в описание инструмента `spawn_subagent` и в таблицу `model_cost_table`). В `prompts/main.md` есть собственный текст о выборе уровня, по смыслу близкий, но другими словами: при правке меняйте оба места. По `TIER_GUIDE`: Haiku — механика (перебор случаев, проверка форматов, массовые вызовы оракулов, черновые гипотезы); Sonnet — транскрипция, IR, код для песочницы, стандартные доказательства по известной схеме; Opus — нестандартная теория, критика, Lean.

Все ошибки — `ModelPolicyError`, текст рассчитан на модель, сделавшую выбор: что не так, что разрешено, как исправить.

`model_cost_table` даёт по каждому разрешённому уровню худшую стоимость одного вызова при `max_tokens` по умолчанию и на полу 4096 токенов (для `budget_status` и описаний инструментов).

> [!note] Что менять
> Разрешённые модели, привязку ролей к моделям и `opus_needs_reason` правят в `settings.json` (шаблон `settings.example.json`, ключи `models.allowed`, `models.roles`, `models.opus_needs_reason`). Лимиты глубины и параллелизма — `limits.max_spawn_depth` (2) и `limits.max_parallel` (4) там же. Денежные лимиты и отключение точек HITL — решение пользователя, см. [[budget]].

## 6. Где что отлаживать

- Агент не создаёт субагента или не принимает план: читайте текст отказа в результате `spawn_subagent` / `run_workflow`; он перечисляет каждую проблему. Для проверки большого плана заранее есть `dry_run`.
- Не та модель у роли: проверьте цепочку приоритетов из раздела 2, в первую очередь переменную `TFL_MODEL_OVERRIDE` и `models.allowed`.
- Роль `coder` пропала: нет песочницы, `agent.py` её не строит.
- Расход ребёнка: итоговый блок `budget` в результате (`allocated_usd`, `spent_usd`, `returned_usd`, `calls`, `refusals`).
- Тесты и скриптованные модели: см. [[testing-and-mock]].
