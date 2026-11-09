---
title: Общая архитектура TFL Deep Agent
tags: [deep-agent, architecture, middleware]
---

# Общая архитектура

Один ReAct-агент на `deepagents` (поверх `langchain.agents.create_agent`) решает задачи всех четырёх классов: REG, CFL, DCFL, LL(k). Он стоит рядом с четырьмя пайплайнами и не заменяет их. Связанные заметки: [[deepagents-usage]], [[roles-and-subagents]], [[tools-and-evidence]], [[verdict-gate]], [[hitl-and-review-modes]], [[budget]], [[run-lifecycle]], [[reports]], [[intake]], [[lean]], [[tfl-lab]], [[testing-and-mock]], [[index]].

> [!note] Что главнее
> Если `docs/architecture.html` и `PLAN.md` расходятся, действует `PLAN.md` (так сказано в его начале). Теорию (леммы, формулировки) здесь не пересказываем: см. `docs/THEORY.md` и `docs/VERDICT_POLICY.md`.

## Поток одного запуска

Источники: `tfl_deep_agent/hitl.py` (`start_run`, `_segment`, `final_result`), `tfl_deep_agent/agent.py`, `tfl_deep_agent/cli.py`.

1. **Вход.** CLI (`python -m tfl_deep_agent`) или TFL Lab вызывает `hitl.start_run(run_dir, task, ...)`. `task` — текст или `intake.files.TaskInput` (текст, PDF, картинка). Должен быть задан ровно один из режимов модели: `mock_script`, `model_factory` или `live=True`; иначе `RunError`. Каталог запуска не должен уже содержать `run.json`.
2. **Подготовка.** `start_run` сохраняет копию скрипта (mock) и входной файл в `input/`, собирает `run.json` (настройки запуска, точки HITL, режим ревью, счётчики) и вызывает `_segment`.
3. **Сегмент.** `_segment` открывает SQLite-checkpointer (`checkpoints.sqlite`, `thread_id` равен id запуска), бюджет (`BudgetLedger`), журнал свидетельств (`EvidenceLedger.open`) и эмиттер `progress.jsonl`. Затем строит граф через `agent.create_tfl_agent(...)` и вызывает `graph.invoke` в цикле.
4. **Цикл ReAct.** Главный агент планирует через `write_todos`, вызывает инструменты, делегирует ролям через `task`, при необходимости использует `spawn_subagent`, `run_workflow`, `budget_status`. Заканчивается вызовом `submit_report`.
5. **Остановки HITL.** Прерывание LangGraph возвращает управление в `_segment`. Если режим ревью автоматически одобряет эту точку (`auto_approves`), сразу ставится решение `approve`. Иначе пишется карточка (`hitl.md`); если диалога нет, сегмент завершается статусом `awaiting_human` (код выхода 3). Продолжение: `resume_run` или `--resume` в новом процессе, состояние берётся из checkpointer. Подробнее: [[hitl-and-review-modes]], [[run-lifecycle]].
6. **Итог.** После конца графа `final_result` вызывает `agent.collect_result(state, ..., evidence=ledger)`: отчёт, доверие по шагам и вердикт считает код по журналу, а не модель ([[verdict-gate]]). `_write_result` пишет `result.{json,md,html}` ([[reports]]). Если отчёта нет, `collect_result` строит `partial_report` со статусом (`incomplete`, `error` или `budget_exhausted`).

> [!warning] Сегмент, а не процесс
> Запуск состоит из сегментов: каждый `--resume` строит граф заново на том же checkpoint. Счётчики вызовов (`limits.model_calls`, `limits.tool_calls`) и потраченные деньги переносятся через `run.json`: после сборки графа `RunLimits` инициализируются из него. Состояние, которое должно пережить остановку, нужно хранить в `run.json` или в checkpoint, иначе оно обнулится.

## Сборка агента: `create_tfl_agent`

Файл: `tfl_deep_agent/agent.py`.

- `model_factory` обязателен (`None` даёт `TypeError`), чтобы библиотечный вызов не создал реальные API-модели случайно. Для mock-запусков это `scripted_model_factory`, для live — `default_model_factory` (`is_live_factory` его распознаёт).
- Живая фабрика требует бюджет: `budget=False` для неё запрещён, а при `budget=None` вне `use_budget` берётся `BudgetLedger.for_task` (проверка дневного лимита, общий журнал трат). Подробнее: [[budget]].
- Любая включённая точка HITL требует `checkpointer`, иначе `ValueError`.
- Модель для каждой роли выбирает `resolve_role_model`: `settings.models.roles`, список `models.allowed`, а `TFL_MODEL_OVERRIDE` перекрывает всё. На роль создаётся один экземпляр модели: агент и его суммаризация используют один и тот же.
- Роли берутся из `roles.ROLES`: `intake`, `ir_builder`, `prover`, `critic`, `lean`, `coder`; роль `general` добавляется всегда и заменяет встроенный `general-purpose` из deepagents. Без sandbox роли с `allow_sandbox` (то есть `coder`) выбрасываются. См. [[roles-and-subagents]].
- На графе остаются атрибуты `tfl_budget`, `tfl_evidence`, `tfl_hitl`, `tfl_sandbox`, `tfl_limits`, `tfl_orchestration`; ими пользуются `hitl.py`, UI и тесты.
- Файловая система (`make_backend`): `CompositeBackend` с маршрутом `/skills/` (read-only, из `tfl_deep_agent/skills`). Всё остальное уходит в default-бэкенд: `StateBackend` (файлы в состоянии графа) или, при sandbox, `SandboxedStateBackend`. При sandbox добавляется маршрут `/workspace/` в контейнер, и регистрируется `execute`.
- `/input/` (файл задания) **не** маршрут `CompositeBackend`: он лежит в default-бэкенде. Запись в него запрещает правило из `readonly_permissions` / `permissions_for`, а при sandbox ещё и сам `SandboxedStateBackend`. Лишние маршруты может добавить только вызывающий через `routes=`.

## Стек middleware

Источник: `_build_agent`, `default_middleware`, `call_limit_middleware`, `_with_subagent_budget`, `_with_budgeted_summarization` в `tfl_deep_agent/agent.py`.

Список, который `_build_agent` передаёт в `create_deep_agent(middleware=...)` для главного агента, в порядке сборки:

| # | Middleware | Откуда |
|---|---|---|
| 1 | `TodoListMiddleware` | langchain; deepagents 0.7.34 сам его больше не добавляет |
| 2 | `CallLimitMiddleware(role="main")` | `call_limit_middleware`, лимиты `settings.limits` (`RunLimits`) |
| 3 | `BudgetMiddleware` | `middleware/budget.py` |
| 4 | `SubagentBudgetMiddleware` | вставляется `_with_subagent_budget` сразу после `BudgetMiddleware` |
| 5 | `ConcurrencyMiddleware` | `middleware/concurrency.py` |
| 6 | `EvidenceMiddleware` | `middleware/evidence.py` |
| 7 | `TFLToolFilter` | `exclude_tools_middleware(SANDBOX_ONLY_TOOLS)`: убирает `execute` из запросов главного агента |
| 8 | `FormalizationReviewMiddleware` | только при включённом HITL 1 (`hitl.py`) |
| 9 | `ReportGateMiddleware` | завершает запуск после принятого `submit_report` |
| 10 | `SummarizationMiddleware` | добавляется в конец функцией `_with_budgeted_summarization` |

Пункты 3, 5, 6 приходят из `default_middleware(settings, "main")`: он перебирает модули `budget`, `concurrency`, `evidence` именно в таком порядке и берёт `make_middleware(...)` каждого. Модуль без `make_middleware` или вернувший `None` ничего не добавляет: `budget.make_middleware` возвращает `None`, если нет ни `node`, ни области `use_budget`; `evidence.make_middleware` — если нет ledger. Явный `middleware=` в `create_tfl_agent` заменяет этот хук целиком, в том числе бюджет.

Остальное (файловая система, `task`, skills, prompt caching) добавляет сам `deepagents`. По докстрингу `agent.py`, его Anthropic-кэширование стоит после наших middleware: системный промпт и список инструментов остаются кэшируемым префиксом, а заметка бюджета дописывается в конец запроса и кэш не ломает.

Стек каждой фиксированной роли (`role_mw`): `CallLimitMiddleware(role=...)`, затем результат `default_middleware(cfg, role_key)` (бюджет, concurrency, evidence), затем `TFLToolFilter` для ролей без `allow_sandbox`, затем суммаризация. `SubagentBudgetMiddleware` есть только у главного агента.

> [!warning] Порядок имеет смысл
> - `CallLimitMiddleware` стоит снаружи `BudgetMiddleware` намеренно: отказ по лимиту вызовов не резервирует деньги (докстринг `call_limit_middleware`).
> - `SubagentBudgetMiddleware` стоит внутри `BudgetMiddleware`: отказ `task` в режиме wrap-up приходит первым (докстринг `_with_subagent_budget`).
> - Суммаризация добавляется нашей версией под тем же именем `SummarizationMiddleware`, поэтому `create_deep_agent` подставляет её вместо своей. Встроенная вызывала бы модель мимо всех пользовательских middleware, без списания денег. Если в стеке нет `BudgetMiddleware` или модель не `BaseChatModel`, остаётся стандартная суммаризация deepagents.
> - `ReportGateMiddleware` в `before_model` смотрит на ToolMessage последнего хода: если среди них принятый `submit_report` (`artifact.ok is True` и есть `report`), следующий вызов модели пропускается (`jump_to: end`). Отчёт, отклонённый валидацией или человеком, `ok` не имеет, и цикл продолжается.

При отладке легко перепутать три разных причины остановки: конец по принятому отчёту (`ReportGate`), конец по лимиту вызовов (`CallLimit`, `stop_reason == "call_limit"`) и отказ по деньгам (`BudgetMiddleware`, `stop_reason == "budget_exhausted"`, см. [[budget]]).

## Оркестрация

Источник: `_orchestration_tools` в `agent.py`, каталог `tfl_deep_agent/orchestration/`.

При `orchestration=True` главный агент получает `spawn_subagent`, `run_workflow`, `budget_status` над одним `OrchestrationContext`. Контекст делит с запуском `RunLimits` (потомки считаются в те же лимиты и глубину) и журнал свидетельств; бюджет берётся из окружающей области `use_budget`, поэтому выделения становятся детьми узла бюджета запуска. Если `limits.max_spawn_depth < 1`, привязывается только `budget_status`. Одноимённые инструменты из `extra_tools` вытесняют встроенные. Подробнее: [[roles-and-subagents]].

## Отношение к четырём пайплайнам

Источники: `PLAN.md` (раздел Non-negotiables, пункты 2 и 3), корневой `CLAUDE.md`, импорты в `tfl_deep_agent/tools/*.py`.

> [!warning] Направление импортов
> `tfl_deep_agent` импортирует `agent_system`, `cfl_system`, `dcfl_system`, `ll_system` и никогда их не меняет. Эти четыре пакета `tfl_deep_agent` не импортируют. `ui_server` и `tfl_eval` импортировать `tfl_deep_agent` могут. `pumping_lemma/` и `reverse_morfism/` не трогаем.

- Пайплайны агенту **не** выдаются как инструменты. Инструменты (`tools/reg.py`, `cfl.py`, `dcfl.py`, `ll.py`, `words.py`, `ir.py`, `theory.py`, `lean.py`, `report.py`) — типизированные обёртки над модулями `lib/` пайплайнов, каждый результат несёт `trust`. Примеры реальных импортов: `cfl_system.lib.cyk`, `dcfl_system.lib.pattern_db`, `ll_system.lib.word_oracle`, `agent_system.lib.type_check`.
- Общее берётся у `agent_system`, а не копируется: клиент и семафор (`llm_client`, `get_concurrency_semaphore`), цены (`MODEL_PRICING`), потолки уверенности (`CONFIDENCE_CAPS` из `claim_verifier`), рендер (`renderer`), Lean-проверка (`type_check`).
- Если нужного хелпера у пайплайна нет, он пишется внутри `tfl_deep_agent/`, а не добавляется в пайплайн (`PLAN.md`).
- Модели и цены зеркалируют `config.py` пайплайнов (`tfl_deep_agent/config.py`); менять их нужно во всех пакетах вместе (корневой `CLAUDE.md`).

## Карта каталога

| Путь | Назначение |
|---|---|
| `agent.py` | сборка графа, middleware-хуки, `collect_result`, `verdict_gate`, mock-скрипты (`ScriptedModels`) |
| `hitl.py` | `start_run` / `resume_run`, сегменты, карточки, решения, `run.json` |
| `cli.py` | аргументы `tfl-deep`, коды выхода |
| `roles.py`, `prompts/*.md` | фиксированные роли и их промпты |
| `tools/` | слой инструментов над `lib/` |
| `middleware/` | `budget.py`, `concurrency.py`, `evidence.py` |
| `evidence/` | `ledger.py`, `policy.py` (оценка отчёта по журналу) |
| `orchestration/` | `spawn.py`, `workflow.py`, `model_policy.py` |
| `intake/files.py` | приём текста, PDF, картинки |
| `reports/` | `render.py`, `diagrams.py` |
| `sandbox/` | `docker_backend.py`, `Dockerfile.sandbox`, `workspace.py` |
| `skills/` | методички по классам и шаблонам отчётов (агент читает их через `/skills/`) |
| `settings.py`, `settings.example.json` | настройки и валидация |
| `progress.py` | поток `progress.jsonl` для TFL Lab |

## Что менять и где

- Новый middleware главного агента: либо модуль с `make_middleware` в `middleware/` и его имя в кортеже `("budget", "concurrency", "evidence")` внутри `default_middleware`, либо явный `middleware=`. Явный список обходит бюджет.
- Новая роль: запись в `roles.ROLES`, промпт в `prompts/`, при необходимости группа инструментов; middleware роли собирает `role_mw` автоматически.
- Новая точка HITL: `HITL_POINTS` и `hitl_points` в `agent.py`, ветка в `_build_agent`, обработка в `hitl._segment` и карточка (см. [[hitl-and-review-modes]]).
- Изменение денежных лимитов или выключение точки HITL — решение пользователя (корневой `CLAUDE.md`).
