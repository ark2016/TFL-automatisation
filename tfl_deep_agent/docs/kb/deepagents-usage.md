---
title: Использование deepagents, LangChain и LangGraph
tags: [deep-agent, deepagents, langchain, langgraph, backend, middleware]
---

# Использование deepagents, LangChain и LangGraph

Заметка о том, как `tfl_deep_agent` собирает граф агента поверх `deepagents.create_deep_agent`, что берёт у библиотек готовым, что добавляет и что отключает. Общая картина: [[architecture]]. Роли: [[roles-and-subagents]]. Деньги: [[budget]]. Остановки на человеке: [[hitl-and-review-modes]]. Тесты и скрипты ответов модели: [[testing-and-mock]].

> [!note] Версии
> `DEV_NOTES.md` фиксирует `deepagents==0.7.34`: поведение, описанное ниже, проверено именно на ней. Зависимости лежат в extra `[deep]` (`pip install -e ".[deep,dev]"`). Поэтому `deepagents` и `langchain` импортируются лениво, внутри функций (`agent.py`), а тесты без этих пакетов пропускаются через `pytest.importorskip`.

## Точка входа

Источники: `tfl_deep_agent/agent.py`, `tfl_deep_agent/roles.py`.

`agent.create_tfl_agent(settings, *, model_factory, checkpointer, middleware, subagent_middleware, interrupt_on, backend, template, roles, extra_tools, store, name, debug, budget, budget_options, evidence, hitl, task_source, sandbox, orchestration)` готовит окружение (бюджет, реестр доказательств, песочница, точки HITL) и вызывает внутреннюю `_build_agent`. Та, в свою очередь, вызывает `deepagents.create_deep_agent(model, tools, system_prompt, middleware, subagents, skills, permissions, backend, interrupt_on, checkpointer, store, name, debug)`.

Неочевидные правила:

- `model_factory` обязателен, без него `TypeError`: библиотечный вызов не должен случайно создать настоящие модели. Для mock-запусков передают `scripted_model_factory(...)`, для live `default_model_factory` (настоящий `ChatAnthropic` через `models.make_chat_model`, помечен `tfl_live = True`).
- Для live-фабрики `budget=False` запрещён, а при `budget=None` вне блока `use_budget` создаётся `BudgetLedger.for_task` (проверка дневного лимита и общий журнал расходов). См. [[budget]].
- Любая включённая точка HITL без `checkpointer` даёт `ValueError`.
- После сборки на граф вешаются атрибуты: `tfl_budget`, `tfl_evidence`, `tfl_hitl`, `tfl_sandbox` ставит `create_tfl_agent`, а `tfl_limits` и `tfl_orchestration` ставит `_build_agent`. Их читают вызывающие, не пересобирая граф.
- Допустимые шаблоны отчёта: `full_proof`, `exam_solution`, `short_verdict`, `lean_appendix` (`TEMPLATES`), иначе `ValueError`.

## Модели

Источники: `agent.py` (`resolve_role_model`), `roles.py` (`role_model_request`).

Порядок выбора модели роли: `TFL_MODEL_OVERRIDE`, затем явный `requested`, затем `settings.models.roles[role]`, затем `config.MODELS[role]`, затем собственный `default_model` роли (он есть только у `general`, это Haiku). Неоценённый id даёт `config.UnknownModelError`, модель вне `settings.models.allowed` даёт `ModelNotAllowedError`.

> [!note] Исключение для `general`
> Если у роли нет ничего, кроме кодового значения по умолчанию, и оно не входит в `allowed`, берётся самая дешёвая разрешённая модель (`cheapest_model`, считается по `config.worst_case_usd` для 1000 входных и 1000 выходных токенов). Явно заданная, но не разрешённая модель по-прежнему поднимает ошибку.

Экземпляр модели на роль создаётся один раз (`model_for`) и используется и агентом, и его суммаризацией.

## Бэкенд и маршруты файловой системы

Источники: `agent.py` (`make_backend`, `permissions_for`, `readonly_permissions`), `DEV_NOTES.md` (разделы про песочницу и права).

`make_backend` строит `CompositeBackend`:

| Маршрут | Бэкенд | Назначение |
|---|---|---|
| `/skills/` | `ReadOnlyFilesystemBackend(root_dir=tfl_deep_agent/skills, virtual_mode=True)` | навыки по классам, только чтение |
| `/workspace/` | `WorkspaceBackend(sandbox)` | только если есть песочница |
| остальное, без песочницы | `StateBackend()` | временные файлы в состоянии графа |
| остальное, с песочницей | `SandboxedStateBackend(sandbox)` | файлы в состоянии, `execute` в контейнере |

`ReadOnlyFilesystemBackend` наследует `FilesystemBackend` и отказывает на `write`, `edit`, `delete`, `upload_files` (сообщение `permission denied: this path is read-only`). Класс определён лениво, через модульный `__getattr__`.

Защита записи двухслойная: сам бэкенд отказывает, а `FilesystemPermission(operations=["write"], mode="deny")` даёт модели понятное сообщение раньше. Файл задачи (PDF или картинка) лежит в состоянии под `/input/` (`INPUT_ROUTE`) и тоже защищён от изменения. См. [[intake]].

> [!warning] Права и песочница
> `deepagents` отказывается принимать `permissions=` рядом с бэкендом, умеющим исполнять команды, если пути правил не начинаются с ключа маршрута, а `/input/` маршрутом не является. Поэтому `permissions_for(backend)` работает так: без исполнения возвращает `readonly_permissions()` (`/skills`, `/skills/**`, `/input`, `/input/**`), с исполнением только `<route>/**` для имеющихся только-читаемых маршрутов, а `/input/` тогда защищает сам `SandboxedStateBackend`. Кроме того, `deepagents` регистрирует `execute`, только если бэкенд по умолчанию является `SandboxBackendProtocol`. Отсюда класс `SandboxedStateBackend`, унаследованный и от состояния, и от песочницы.

## Песочница и `execute`

Источники: `agent.py` (`resolve_sandbox`, `SandboxInfo`, `exclude_tools_middleware`), `roles.py` (`SANDBOX_ONLY_TOOLS`).

- `sandbox=None` или `False`: песочницы нет, Docker не трогается. `"auto"` или `True`: `DockerSandbox`, если `docker_backend.sandbox_status()` подтверждает Docker, демон и образ (контейнер стартует лениво). Можно передать готовый объект `SandboxBackendProtocol`. Одновременно `backend=` и `sandbox=` передавать нельзя (`ValueError`).
- Без песочницы `execute` не регистрируется, роль `coder` не строится (роли с `allow_sandbox=True` отсекаются), а системный промпт называет причину.
- Встроенный `execute` остаётся только у роли `coder`. У остальных его убирает `TFLToolFilter` (`exclude_tools_middleware`): скрывает инструмент из запроса модели и отказывает при вызове, потому что узел инструментов графа знает все встроенные инструменты и выдуманный вызов дошёл бы до исполнения.
- Контейнер, открытый через `"auto"`, закрывает `close_sandbox(graph)`. Переданный вызывающим закрывает вызывающий.

## Инструменты: todo, task и свои

Источники: `agent.py` (`_build_agent`, `main_tools`), `DEV_NOTES.md` (раздел Decisions).

- `write_todos` в `deepagents 0.7.34` больше не встроен, поэтому `_build_agent` сам ставит первым в стек главного агента `langchain.agents.middleware.TodoListMiddleware()`.
- `task` (вызов подагента по имени) встроен в `deepagents`. Роли ему передаются декларативными словарями `SubAgent`. Подробнее: [[roles-and-subagents]].
- Инструменты главного агента: все группы `MAIN_TOOL_GROUPS` из реестра `tools.base.REGISTRY`, плюс `submit_report` (над реестром доказательств), плюс инструменты оркестрации (`spawn_subagent`, `run_workflow`, `budget_status`) через `OrchestrationContext`. Если `limits.max_spawn_depth < 1`, привязан только `budget_status`. Одноимённые инструменты из `extra_tools` вытесняют оркестрационные. См. [[tools-and-evidence]].
- Файловые инструменты приходят из `FilesystemMiddleware` самого `deepagents`.

## Подагенты

Источники: `roles.py` (`ROLES`, `build_subagents`), `agent.py`.

`build_subagents` превращает `Role` в словарь `{name, description, system_prompt, model, tools, middleware[, skills]}`.

> [!warning] Декларативные подагенты не наследуют middleware главного агента
> Каждой роли `_build_agent` собирает свой список: `call_limit_middleware`, затем `default_middleware(cfg, role)`, затем `TFLToolFilter` (если нет `allow_sandbox`), затем суммаризация с учётом бюджета. Чего нет в этом списке, у роли не работает. Меняя стек, проверяйте обе ветки: `role_mw` и `main_mw`.

- `general-purpose` `deepagents` добавляет сам (модель главного агента, все его инструменты, ваше middleware не применяется), пока не передана спецификация с таким именем. `ROLES["general"]` её заменяет: Haiku, только группа `theory`, встроенный промпт, тот же бюджетный middleware. `_build_agent` всегда добавляет `general` в список ролей.
- Навыки (`skills=["/skills/"]`) получают роли с `skills=True`: `ir-builder`, `prover`, `critic`, `lean-formalizer`. Не получают `intake`, `coder`, `general-purpose`. Главный агент навыки получает тоже.
- Ключ роли в настройках и имя подагента для `task` различаются: `ir_builder` и `ir-builder`, `lean` и `lean-formalizer`, `general` и `general-purpose` (словарь `ROLE_BY_NAME`).

## Навыки (skills)

Источники: `agent.py` (`SKILLS_DIR`, `SKILLS_ROUTE`), `DEV_NOTES.md`.

Навыки лежат в `tfl_deep_agent/skills/{reg,cfl,dcfl,ll}/SKILL.md`. Формат определяет `SkillsMiddleware` из `deepagents`: YAML-шапка с `name` (равен имени папки) и `description` (до 1024 символов, длиннее `deepagents` обрезает). Поле `allowed-tools` есть в `dcfl`, `ll` и `report-templates`; в `reg` и `cfl` его нет. Теория в навыки переносится из `docs/THEORY.md`, здесь формулировок нет. См. также [[verdict-gate]].

## Суммаризация

Источники: `agent.py` (`_with_budgeted_summarization`), `DEV_NOTES.md` (раздел Summarization).

`deepagents` кладёт в каждый стек `SummarizationMiddleware`, но он вызывает модель в обход пользовательского middleware, то есть без учёта бюджета. Проект подменяет его своим: `create_summarization_middleware(model, backend, summarizer=budget.budgeted_summarizer(...))` с тем же именем `SummarizationMiddleware`, и `create_deep_agent` ставит его на место стандартного.

- Подмена делается только если в стеке есть `BudgetMiddleware`, иначе остаётся стандартная.
- Вызов суммаризации ограничен `SUMMARY_MAX_TOKENS = 8192`. При отказе бюджета возвращается фиксированный текст «[No summary: ...]», запуск не падает. Такие вызовы пишутся с `outcome="summary"` и в `RunLimits` не считаются.
- Если модель не `BaseChatModel`, `TypeError` перехватывается и остаётся стандартная суммаризация.

## Middleware проекта

Источник: `agent.py`. Подробности по деньгам: [[budget]].

Порядок в стеке главного агента: `TodoListMiddleware`, `call_limit_middleware(main)`, результат `default_middleware(...)` (бюджет, конкурентность, реестр доказательств; сразу после `BudgetMiddleware` `_with_subagent_budget` вставляет `SubagentBudgetMiddleware`), `TFLToolFilter`, `FormalizationReviewMiddleware` (если включён HITL 1), `ReportGateMiddleware`, затем суммаризация с бюджетом. Кэширование промпта (`AnthropicPromptCachingMiddleware`) `deepagents` добавляет сам после ваших слоёв, поэтому записка о лимите, дописываемая в конец запроса, кэшируемый префикс не ломает.

- **`default_middleware`** обходит модули `middleware.budget`, `.concurrency`, `.evidence` и вызывает у каждого `make_middleware(settings, role=...)`. Модуль без такой функции пропускается. Явные `middleware=` и `subagent_middleware=` заменяют этот хук целиком, вместе с `BudgetMiddleware`. `SubagentBudgetMiddleware` добавляет отдельно `_with_subagent_budget` (только в стек главного агента, и после выбора между `middleware=` и `default_middleware`), поэтому он остаётся и при явном `middleware=`, если в списке есть бюджет.
- **`RunLimits`** (`settings.limits`): общие для запуска счётчики `max_model_calls` (по умолчанию в коде 80), `max_tool_calls` (300), `max_parallel` (4), `max_spawn_depth` (2). Последние `main_reserve` модельных вызовов (2 при `max_model_calls >= 10`) оставлены главному агенту под отчёт. `submit_report` и `write_todos` лимитом инструментов не отклоняются. Перед исчерпанием в запрос дописывается сообщение с просьбой завершить работу, после исчерпания вызов не делается, а возвращается `AIMessage` без вызовов инструментов с `stop_reason == "call_limit"`.
- **`ReportGateMiddleware`** завершает запуск (`jump_to: end` в `before_model`), когда среди ответов на последний ход есть успешный `submit_report` (артефакт `ok: true`). Использован вместо `return_direct`: `langchain` завершал бы цикл и на отчёте, отклонённом валидацией.

## Чекпойнтер и HITL

Источники: `agent.py` (`hitl_points`, `_build_agent`), `hitl.py` (`_open_checkpointer`, `CHECKPOINT_FILE`).

- Чекпойнтер создаёт не `agent.py`, а `hitl.py`: `SqliteSaver` над `checkpoints.sqlite` в каталоге запуска, `thread_id` равен id запуска. Возобновление в новом процессе пересобирает граф на том же файле.
- Три точки (`HITL_POINTS`): `formalization` (HITL 1, после ответа ir-builder, через `FormalizationReviewMiddleware`), `report` (HITL 2, через `interrupt_on["submit_report"]` с предикатом валидности), `execute` (только при доступной песочнице). Неизвестное имя точки даёт `ValueError`. Подробно: [[hitl-and-review-modes]].
- Переданный аргумент `interrupt_on` добавляется поверх собственных правил и может их перекрыть (`all_interrupts.update`).

## Запуск и результат

Источник: `agent.py` (`run_task`, `collect_result`).

`run_task(agent, task_text, thread_id=..., files=...)` вызывает `agent.invoke({"messages": [HumanMessage], "files": ...})`. При HITL состояние может остановиться на прерывании (ключ `__interrupt__`), дальше им управляет `hitl.py`. `collect_result` собирает итог из состояния, а вердикт и уровень доверия вычисляет код (`verdict_gate`), не модель. См. [[verdict-gate]], [[reports]], [[run-lifecycle]].

## Что проект добавляет и отключает

| Вопрос | Что сделано |
|---|---|
| `write_todos` | добавлен вручную (`TodoListMiddleware`) |
| подагент `general-purpose` | заменён своим: дешёвый, без оракулов |
| `execute` | только у `coder` и только с песочницей |
| `/skills/`, `/input/` | только чтение (бэкенд плюс правило) |
| суммаризация | подменена версией с учётом бюджета |
| завершение запуска | `ReportGateMiddleware`, не `return_direct` |
| кэширование промпта | как есть, из `deepagents` |
| настоящие модели | только через `default_model_factory` и только с бюджетом |

Правила из `PLAN.md`, важные при работе с библиотеками: четыре пакета пайплайнов только импортируются и не правятся, при разработке нет вызовов API, тесты идут на поддельных или скриптовых моделях (см. [[testing-and-mock]]). Теория: `docs/THEORY.md`.
