---
title: Тесты, mock-режим и правило о live-запусках
tags: [deep-agent, testing, mock, eval]
---

# Тесты, mock-режим и правило о live-запусках

Заметка о том, как `tfl_deep_agent` проверяется без обращения к API: сценарии ответов модели (mock scripts), `ScriptedChatModel`, защита от сети в тестах, раскладка тестов и запуск через `tfl-eval --systems deep`. Связанные заметки: [[architecture]], [[budget]], [[hitl-and-review-modes]], [[run-lifecycle]], [[roles-and-subagents]], [[tfl-lab]], [[index]].

> [!warning] Правило о live-запусках
> Live-запуск (`tfl-deep --live`, live в Deep Agent workspace TFL Lab, `tfl-eval --systems deep --live`) допустим только по явной просьбе пользователя, с заранее согласованным бюджетом, только на Haiku (`TFL_MODEL_OVERRIDE=claude-haiku-5-5`) и с оценкой стоимости до запуска. Режим по умолчанию - mock. `tfl-deep` никогда не читает `.env`. Меняет ли он лимиты денег, решает пользователь. Источники: корневой `CLAUDE.md`, `tfl_deep_agent/CLAUDE.md`. Денежные лимиты и их проверку см. в [[budget]].

## Mock scripts

Источники: `tfl_deep_agent/agent.py` (раздел "Scripted replay models"), `tfl_deep_agent/examples/mock/`, `tfl_deep_agent/cli.py`.

Mock script - JSON-файл с заранее записанными ответами модели для каждой роли. Формат проверяет `agent.load_mock_script`:

- `models` (обязательно, непустой объект): ключ роли -> список ответов. Обязателен ключ `main`.
- `defs` (необязательно): именованные фрагменты. Любое место вида `{"$def": "имя"}` внутри `args` вызова инструмента заменяется глубокой копией `defs[имя]` (`_substitute`). Неизвестное имя - `ScriptError`.
- `task`, `template`, `description` (необязательно). CLI берёт `task` из сценария, если текст задачи не передан аргументом; если задачи нет нигде, это ошибка.

Допустимые ключи в `models`: ключи ролей из `roles.ROLES`, `main`, а также `spawn:<name>` для ad-hoc субагента и `workflow:<step id>` для шага workflow (регулярное выражение `_SCRIPT_CHILD_KEY`: после префикса строчные латинские буквы, цифры, `_`, `-`, до 48 символов). Любой другой ключ - `ScriptError` при загрузке. Про роли и дочерних агентов см. [[roles-and-subagents]].

Один ответ (reply) - объект:

- `content` - текст;
- `tool_calls` - список `{name, args, id?}`; без `id` он строится как `call_<role>_<номер ответа>_<номер вызова>`;
- `usage` - необязательно; если не задан, токены оцениваются по длине (символов на 4);
- `expect.last_message_contains` - необязательная проверка: последнее сообщение, пришедшее модели, должно содержать подстроку, иначе `ScriptError`. Так сценарий закрепляет порядок обмена (например, что критик получил слова проверки).

Поставляются сценарии в `examples/mock/`: `reg_regex_regular.json`, `cfl_anbmcm.json`, `ll_simple_ll1.json`, плюс `examples/mock/eval/<id>.json` для задач eval-набора (`cfl-04`, `dcfl-04`, `ll-17`, `reg-04`, `reg-13`). Тест `test_docs.py::test_readme_lists_every_shipped_mock_script` требует, чтобы каждый файл `examples/mock/*.json` был упомянут в таблице `README.md`.

> [!note] Инструменты в mock-режиме настоящие
> Скрипт подменяет только модель. Инструменты (`regex_to_dfa`, `run_dfa`, `validate_ir` и т. д.) работают по-настоящему, поэтому уровни доверия и итоговый вердикт приходят из кода, а не из сценария (подробнее [[tools-and-evidence]], [[verdict-gate]]). Тестовые сценарии HITL (`tests/hitl_scripts.py`) собраны как варианты `ll_simple_ll1.json` именно ради этого.

## ScriptedChatModel и ScriptedModels

Источник: `tfl_deep_agent/agent.py`.

- `ScriptedChatModel` - подкласс `BaseChatModel`, создаётся лениво через `scripted_model_class()` (langchain-core входит в необязательный extra `[deep]`). Каждый вызов модели отдаёт следующий ответ списка. Когда ответы кончились, `ScriptError`: "the agent made more model calls than scripted".
- `bind_tools` возвращает ту же модель, поэтому курсор общий для всех вызовов. Имена инструментов в ответе сверяются с теми, что агент действительно привязал (`strict_tools=True`): вызов инструмента, которого у роли нет, падает громко.
- Свойства для тестов: `calls_made`, `received` (списки сообщений каждого вызова), `bound_tools`.
- `ScriptedModels` (фабрика `scripted_model_factory(script, state_path=None)`) - это `model_factory`: по ключу роли и id модели выдаёт отдельный `ScriptedChatModel` со своим списком ответов; `models[role]` даёт экземпляр для проверок. `unused_replies()` возвращает остаток ответов по ролям (у законченного сценария пусто).
- `create_tfl_agent` без явного `model_factory` отказывается работать (подсказка в сообщении: `scripted_model_factory(...)` для mock). Live-фабрика - `default_model_factory`; она тоже распознаётся (`is_live_factory`), что важно для дневного бюджета, см. [[budget]].

### Курсоры между процессами

`state_path` (в запуске это `mock_cursors.json` в каталоге запуска, константа `hitl.MOCK_CURSORS_FILE`) хранит `{"cursors": {роль: сколько ответов использовано}}` и дописывается после каждого ответа. Поэтому запуск, остановившийся на точке HITL (код выхода 3) и продолженный `--resume` в новом процессе, продолжает сценарий, а не проигрывает его с начала. В `hitl._factory` для запуска с `mode == "mock"` фабрика строится из файла сценария, сохранённого в каталоге запуска. Подробности цикла запуска: [[run-lifecycle]], [[hitl-and-review-modes]].

> [!warning] Типичные поломки при правке сценария
> Лишний или недостающий вызов модели меняет число ответов: нехватка даёт `ScriptError`, избыток виден в `unused_replies()`. После добавления шага агента (например, нового вызова инструмента с ответом модели) сценарии, проходящие этот путь, нужно обновить.

## Защита от сети в тестах

Источники: корневой `conftest.py` (по описанию в `tests/guard.py` и `tests/conftest.py`; самого файла в снимке нет), `tfl_deep_agent/tests/guard.py`, `tfl_deep_agent/tests/conftest.py`, `tfl_deep_agent/tests/test_network_guard.py`.

Слои:

1. Корневой conftest (по описанию в корневом `CLAUDE.md` и в docstring `guard.py`) убирает `ANTHROPIC_API_KEY` и подменяет `anthropic.Anthropic` / `AsyncAnthropic` заглушкой. Сам файл в снимке отсутствует, подробности его работы здесь не описываются.
2. `guard._block_langchain_anthropic_clients` (сессия, autouse): langchain-anthropic создаёт клиент через `anthropic.Client` / `anthropic.AsyncClient` - отдельные алиасы, которые корневая заглушка не покрывает. Фикстура направляет их на ту же заглушку. Сообщение при вызове содержит "disabled under pytest".
3. `guard._no_live_anthropic_key` (функция, autouse): перед каждым тестом убирает ключ и превращает в no-op `llm_client._load_env` и `dotenv.load_dotenv`. Причина: `LLMRunner.__init__` вызывает `_load_env()`, который копирует корневой `.env` в `os.environ`, а оркестраторы пайплайнов зовут `load_dotenv`; одной очистки на сессию не хватает.

`TFL_ALLOW_LIVE=1` выключает все три слоя, и тесты `test_network_guard.py` при этом пропускаются.

> [!warning] Новые каталоги тестов
> Каталог тестов вне пакета, который строит агента или использует `ChatAnthropic`, обязан импортировать фикстуры `guard.py` в свой `conftest.py` (так сделано в `tfl_eval/tests/conftest.py`):
> `from tfl_deep_agent.tests.guard import _block_langchain_anthropic_clients, _no_live_anthropic_key`.
> Иначе падает `test_network_guard.py::test_other_test_dirs_that_use_the_agent_import_the_guard`, который просматривает `testpaths` из `pyproject.toml`.

### Фикстура wire

`tests/conftest.py` даёт фикстуру `wire` для тестов формы запроса (`test_models_request_shape.py`, а также `test_agent_integration.py`, `test_budget.py`, `test_intake.py`). Она подставляет настоящие классы SDK с `httpx2.MockTransport` и неразрешимым базовым URL `http://anthropic.mock.invalid`, записывает запросы (`wire.last`, `wire.requests`) и отвечает потоком SSE (`text_reply_events`). Чат-модель нужно строить внутри теста, после запуска фикстуры. Из машины ничего не уходит даже при промахе подмены транспорта.

## Раскладка тестов

Источники: `tfl_deep_agent/tests/`, `tfl_deep_agent/CLAUDE.md`.

Группы файлов (по именам):

- сборка и поведение агента: `test_agent_fake_model.py`, `test_agent_integration.py`, `test_e2e_mock.py`, `test_integration_p4_p6.py`, `test_orchestration.py`;
- HITL и режимы проверки: `test_hitl.py` (сценарии - `hitl_scripts.py`);
- деньги и запрос: `test_budget.py`, `test_settings.py`, `test_config.py`, `test_models_request_shape.py`;
- инструменты: `test_tools_*.py`, `test_tools_base.py`, `_tool_helpers.py`;
- навыки (skills): `test_skill_reg.py`, `test_skill_cfl.py`, `test_skill_dcfl.py`, `test_skill_ll.py`, `test_class_ladder.py`;
- доказательства, отчёты, диаграммы: `test_evidence.py`, `test_reports.py`, `test_diagrams.py`, `test_critic_ticket50.py`;
- вход: `test_intake.py`; песочница: `test_sandbox.py`;
- исправления ядра и прогресса: `test_p2_core_fix.py` (шлюз вердикта, лимиты запуска, учёт бюджета при сводках, отказах и отмене, фильтр инструментов), `test_progress_segment.py` (порядок открытия чекпойнтера и эмиттера прогресса в `hitl._segment`);
- служебные: `test_skeleton.py` (в том числе проверка, что четыре пайплайна не импортируют `tfl_deep_agent`), `test_network_guard.py`, `test_docs.py`.

Команды (из `tfl_deep_agent/CLAUDE.md`):

```bash
.venv/Scripts/python -m pytest -q tfl_deep_agent/tests        # пакет
.venv/Scripts/python -m pytest -q -n 4 tfl_deep_agent/tests   # быстрее
.venv/Scripts/python -m pytest -q                             # весь репозиторий (testpaths)
```

Особые правила:

- `test_docs.py` держит `README.md` и корневые документы в согласии с CLI, значениями настроек по умолчанию и поставляемыми файлами (флаги парсера, таблица настроек, коды выхода, файлы каталога запуска, список mock-сценариев, ссылки, секция eval). Меняете CLI, `settings.DEFAULTS` или набор файлов - обновляйте документы.
- `settings.example.json` должен совпадать с `settings.DEFAULTS`.
- Docker-тесты должны чисто пропускаться, если нет Docker, демона или образа.
- Полный прогон на этом хосте даёт сбои компиляции Lean в Docker в тестах пайплайнов (нестабильный демон); они не относятся к этому пакету и фиксируются в `DEV_NOTES.md` на каждом шлюзе.
- Тесты агента используют только скриптовые модели (`ScriptedChatModel` через `agent.scripted_model_factory`); в тестах пакета других классов чат-моделей нет. Тестовые заглушки другого рода (`FakeRequest` в `test_budget.py` - заглушка запроса, `FakeSandbox` в `test_integration_p4_p6.py` - заглушка песочницы) моделями не являются. `tfl_deep_agent/CLAUDE.md` дополнительно называет `GenericFakeChatModel` и `FakeMessagesListChatModel`, но в тестах они не используются.

## tfl-eval над deep-системой

Источники: `tfl_eval/cli.py`, `tfl_eval/runners.py`, `tfl_eval/tests/test_deep_adapter.py`, `docs/EVAL_SET.md` (набор задач и ожидаемые вердикты).

`--systems deep` включается явно и в набор по умолчанию не входит (тест `test_default_systems_do_not_include_deep`). Для каждой выбранной записи манифеста deep-агент запускается через `runners.run_deep` -> `hitl.start_run` с `auto_approve=True` и без диалога: каждая остановка HITL одобряется автоматически.

- **Вход агента.** `deep_task_text`: `source_text` из IR плюс отдельной строкой один вопрос по классу записи (`deep_class_question`). Если `source_text` нет - `DeepAdapterError`.
- **Mock-режим.** Сценарий берётся из `tfl_deep_agent/examples/mock/eval/<id записи>.json` (`deep_mock_script`). Записи без сценария получают `status: skipped`. Сейчас сценарии есть для пяти записей (`reg-04`, `reg-13`, `cfl-04`, `dcfl-04`, `ll-17`); тест `test_every_deep_script_belongs_to_a_manifest_task` следит, чтобы каждый такой файл соответствовал задаче манифеста.
- **Результат.** `extract_deep` приводит `result.json` запуска к общей форме `extract` и добавляет `deep_status`, `language_class`, `class_mismatch`. Вердикт - токен политики из `result.json`, а не текст модели (тесты `test_extract_never_uses_the_model_text_verdict`, `test_extract_prefers_published_verdict_over_token`). См. [[verdict-gate]].
- **Каталоги запусков.** `<out>/deep_runs/<id>/` (`<id>-2` и далее при повторах). В отчёте метрики deep-записей дополнительно разбиты по классам (`deep_by_class`).
- **Настройки.** `--deep-settings JSON` (только вместе с `--systems deep`; иначе выход с ошибкой) - файл настроек, чьи лимиты бюджета действуют для каждой задачи. По умолчанию `tfl_deep_agent/settings.json`, если нет - встроенные значения.
- **Деньги.** В mock-режиме числа бюджета являются оценкой токенов, а не расходом; расход записывается только для live (`budget_spent_usd`).

> [!warning] Live над deep
> `--live` с deep-записями сначала считает худший случай `min(число записей * per_task_usd, per_day_usd)` и отказывается стартовать, если он выше `--deep-max-usd` (по умолчанию `DEFAULT_DEEP_MAX_USD = 1.00`). Чтобы пройти проверку, сужают набор через `--ids`, снижают `budget.per_task_usd` в `--deep-settings` или поднимают `--deep-max-usd` до согласованного с пользователем бюджета. Модель для `--live` по умолчанию Haiku (`--model` задаёт явную). Ключ API для deep-записей берётся из окружения как есть: `.env` загружают только live-раннеры пайплайнов, а `tfl-deep` его не читает, поэтому `cli.py` сохраняет состояние ключа "до" пайплайнов. Всё это запускается только по просьбе пользователя.

Для пайплайнов (`--systems cfl,ll,...`) без `--live` используется их `MockRunner`, если манифест указывает существующую фикстуру `mock_task`; иначе запись пропускается.

## Что менять и где

| задача | куда |
|---|---|
| добавить сценарий для CLI | `examples/mock/<имя>.json`, строка в таблице `README.md` (иначе упадёт `test_docs.py`) |
| сценарий для eval-записи | `examples/mock/eval/<id>.json`, `id` должен быть в манифесте `tfl_eval/manifest.json` |
| новый ключ ребёнка в сценарии | `spawn:<name>` / `workflow:<step id>`, имя по `_SCRIPT_CHILD_KEY` |
| новый каталог тестов, строящий агента | импорт фикстур из `tfl_deep_agent.tests.guard` в его `conftest.py` |
| проверка формы запроса к API | фикстура `wire`, не живой вызов |
