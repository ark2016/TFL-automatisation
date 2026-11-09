---
title: Инструменты и журнал свидетельств
tags: [deep-agent, tools, evidence, trust]
---

# Инструменты и журнал свидетельств

Заметка про слой инструментов (`tools/`), уровни доверия, которые они могут выдавать, журнал `evidence.jsonl` и проверку того, что отчёт ссылается только на известные id. Правила вердикта и потолки уверенности описаны в `docs/VERDICT_POLICY.md`; как они применяются к итоговому вердикту, см. [[verdict-gate]]. Общую схему агента см. в [[architecture]], роли и субагентов в [[roles-and-subagents]].

> [!note]
> Теорию (леммы о накачке и т. п.) заметка не излагает. Формулировки лежат в `docs/THEORY.md`.

## 1. Слой инструментов: `tools/base.py`

Источники: `tfl_deep_agent/tools/base.py`, `tfl_deep_agent/docs/TOOL_INVENTORY.md`.

Каждый инструмент агента: тонкая pydantic-обёртка над функцией из `agent_system/lib`, `cfl_system/lib`, `dcfl_system/lib` или `ll_system/lib`. Таблица «какая lib-функция за каким инструментом» и форматы входа/выхода лежат в `docs/TOOL_INVENTORY.md`; в код её не дублируют.

Что даёт `base.py`:

- `ToolResult`: единая форма результата (`tool`, `ok`, `data`, `trust`, `trust_reason`, `bounds`, `error`, `error_kind`, `elapsed_s`). Неизменяемая; строится через `ToolResult.success(...)` или `ToolResult.failure(...)`.
- Лимиты на вход (`Limits`, `DEFAULT_LIMITS`, `check_*`, аннотированные типы `Word`, `WordList`, `Alphabet`, `Symbol`).
- Таймауты: `Deadline` для кооперативных циклов, `run_with_timeout` для целого вызова (режимы `thread`, `process`, `none`).
- `ToolRegistry`, глобальный `REGISTRY` и декоратор `tfl_tool` (это `REGISTRY.tool`), который превращает функцию в LangChain `StructuredTool` с `response_format="content_and_artifact"`.

Как пишется инструмент: функция `func(args: <подкласс ToolArgs>) -> ToolResult`, помеченная `@tfl_tool(group=..., max_trust=..., timeout_s=..., timeout_mode=..., allow_refuted=...)`. Декоратор возвращает функцию без изменений (она остаётся picklable для `timeout_mode="process"`); выполняется инструмент только через `REGISTRY.invoke` или `REGISTRY.langchain_tools()`.

### Неочевидные правила

- Доверие считает код обёртки, а не модель. Все схемы аргументов наследуют `ToolArgs` (`extra="forbid"`), и при регистрации проверяется, что ни одно поле схемы, включая вложенные модели, не называется как элемент `RESERVED_ARG_NAMES` (`trust`, `trust_reason`, `trust_level`, `confidence`, `verified`, `evidence_trust`, а также служебные параметры LangChain). Нарушение даёт `ValueError` при регистрации.
- Инварианты `ToolResult` проверяются валидатором: неуспех (`ok=False`) обязан иметь `error` и `trust == "not_verified"`; `bounded_pass` обязан нести `bounds`; `trust_reason` не бывает пустым; `data` приводится к чистому JSON.
- `REGISTRY.invoke` никогда не бросает исключение из-за проблемы инструмента: любая ошибка превращается в `ToolResult` с `ok=False` и `error_kind` из набора `invalid_input`, `input_too_large`, `timeout`, `unsupported`, `busy`, `lib_error`, `internal`.
- Потолок доверия применяется после работы функции (`_apply_ceiling`). Если `trust` выше потолка инструмента, он понижается, а в `trust_reason` дописывается пометка. Если инструмент объявлен с `allow_refuted=False`, его `refuted` превращается в `not_verified`. Если `verified` понижен до `bounded_pass`, а `bounds` не заданы, итог `well_formed`.
- Зависший поток в режиме `thread` убить нельзя. Когда таких брошенных потоков набирается `MAX_ABANDONED_THREADS` (8), новые вызовы в этом режиме получают `ToolBusy` (`error_kind: "busy"`). Для чистых поисков без собственного бюджета времени в `TOOL_INVENTORY.md` рекомендован `timeout_mode="process"`; он требует функций уровня модуля и picklable аргументов.
- В модель уходит `ToolResult.to_content(max_chars)`: если JSON длиннее `Limits.max_content_chars` (20 000), `data` заменяется превью, полный результат остаётся в `ToolMessage.artifact` и в журнале.
- Если LangChain отверг аргументы ещё до вызова, артефакта нет, а `content` содержит тот же `ToolResult` с `error_kind: "invalid_input"`.

### Группы и загрузка

`GROUPS`: `ir`, `words`, `reg`, `cfl`, `dcfl`, `ll`, `theory`, `lean`, `orchestration`, `report`. Модули групп перечислены в `GROUP_MODULES` (`tools/ir.py`, `words.py`, `reg.py`, `cfl.py`, `dcfl.py`, `ll.py`, `theory.py`, `lean.py`, `report.py`); группа `orchestration` в `GROUP_MODULES` модуля нет, у неё есть только потолок `not_verified`. Регистрация происходит при импорте модуля, поэтому перед использованием реестра вызывают `load_groups(...)`; неизвестная группа даёт `KeyError`.

Если инструмент нужно добавить или поменять потолок, правят модуль группы, а `GROUP_MAX_TRUST` и `GROUP_MODULES` в `base.py` только если появляется новая группа. Регистрация падает, если `max_trust` инструмента выше потолка его группы, имя не подходит под `^[a-z][a-z0-9_]{1,63}$`, описание пустое или `timeout_s` вне `(0, max_timeout_s]`. Повторная регистрация имени другой функцией отвергается; той же функцией (reload) заменяет запись.

## 2. Уровни доверия, которые может дать инструмент

Источник: `tools/base.py` (`Trust`, `GROUP_MAX_TRUST`, `clamp_trust`, `strongest_trust`, `confidence_cap`) и декораторы `@tfl_tool` в модулях групп.

Порядок: `refuted < not_verified < well_formed < bounded_pass < verified`. `refuted` в сравнениях не участвует: это найденный контрпример, а не «сила». Потолки уверенности (`CONFIDENCE_CAPS`) берутся из `agent_system.lib.claim_verifier`, не копируются: `verified` 0.98, `bounded_pass` 0.85, `well_formed` 0.55, `not_verified` 0.40 (комментарий в коде ссылается на VERDICT_POLICY §2).

Потолок группы (`GROUP_MAX_TRUST`):

| Группа | Потолок |
|---|---|
| `ir` | `well_formed` |
| `words` | `bounded_pass` |
| `reg`, `cfl`, `dcfl`, `ll`, `lean` | `verified` |
| `theory`, `orchestration`, `report` | `not_verified` |

Собственный `max_trust` инструментов (по декораторам):

| Инструмент | Группа | `max_trust` |
|---|---|---|
| `describe_ir_schema` | ir | `not_verified` |
| `validate_ir`, `ir_to_text` | ir | `well_formed` |
| `sample_words` (`allow_refuted=False`) | words | `not_verified` |
| `check_membership` | words | `bounded_pass` |
| `regex_to_dfa`, `run_dfa` | reg | `verified` |
| `nerode_probe`, `dfa_vs_ir` | reg | `bounded_pass` |
| `pump_check_reg` | reg | `well_formed` |
| `cyk`, `pda_run` | cfl | `verified` |
| `input_grammar_cfg` (`allow_refuted=False`) | cfl | `verified` |
| `grammar_vs_ir` | cfl | `bounded_pass` |
| `to_cnf`, `parikh`, `pump_check_cfl` | cfl | `well_formed` |
| `dpda_run` | dcfl | `verified` |
| `dpda_vs_ir` | dcfl | `bounded_pass` |
| `closure_lookup`, `pattern_match` (оба `allow_refuted=False`) | dcfl | `not_verified` |
| `first_follow`, `ll_table`, `ll_parse`, `grammar_transform` | ll | `verified` |
| `lean_check` (`allow_refuted=False`) | lean | `verified` |
| `lean_statement` (`allow_refuted=False`) | lean | `not_verified` |
| `list_theorems`, `get_theorem` (`allow_refuted=False`) | theory | `not_verified` |
| `list_report_templates` (`allow_refuted=False`) | report | `not_verified` |

Это уровень, выше которого инструмент подняться не может; фактическое значение вызова функция группы вычисляет по результату lib-функции. По `TOOL_INVENTORY.md` §0, ответ оракула `None` («не знаю») никогда не означает «слова нет в L»: такие слова считаются в `bounds.n_unknown` и не входят в решающие.

> [!warning]
> Таблица выше снята с декораторов в момент написания; при смене потолка источником правды остаётся декоратор в модуле группы, а `TOOL_INVENTORY.md` описывает намерение и сигнатуры lib-функций на момент его написания (в нём указан коммит `752a0b5`).

Инструмент `submit_report` (`tools/report.py`) не входит в реестр `REGISTRY`: он собирается через `make_submit_report_tool` как отдельный LangChain-инструмент, см. §4. Этого не видно из списка групп, поэтому при поиске по реестру его там нет.

## 3. Журнал свидетельств

Источники: `tfl_deep_agent/evidence/ledger.py`, `tfl_deep_agent/middleware/evidence.py`.

### Что записывается

`EvidenceMiddleware` оборачивает каждый вызов инструмента (`wrap_tool_call` и `awrap_tool_call`) в главном агенте и во всех ролях и субагентах, все пишут в один `EvidenceLedger` запуска. Одна запись `EvidenceRecord` на вызов: id, `seq`, `tool`, `tool_call_id`, `args`, `ok`, `trust`, `trust_reason`, `bounds`, `error`, `error_kind`, `data`, `has_tool_result`, `status`, `role`, `scope` (путь узла бюджета, см. [[budget]]), `model`, `turn_usd`, `cost_usd`, `parent_call`, `result_digest`, `args_digest`, `truncated`, `elapsed_s`, `ts`, `prev`, `digest`.

Где лежит:

- в памяти ledger-объекта;
- в файле `run_dir/evidence.jsonl`: по JSON-объекту на строку, дозапись под блокировкой с `flush`; файловые инструменты агента работают с виртуальным `StateBackend`, а не с каталогом запуска;
- компактная копия (`state_view`, поля `STATE_FIELDS` без `args` и `data`) в состоянии агента под ключом `tfl_evidence`; редьюсер `merge_evidence` склеивает по id, первая копия побеждает, поэтому возврат списка из субагента через `task` не дублирует записи.

### Откуда берётся доверие записи

Только из кода. Вызов считается свидетельством, если это зарегистрированный в `REGISTRY` инструмент TFL и артефакт `ToolMessage` проходит валидацию как `ToolResult` этого же инструмента (`tool_result_of`); доверие при этом ещё раз зажимается потолком инструмента. Всё остальное (ответ субагента `task`, встроенные `read_file` и `write_todos`, ошибка валидации LangChain без артефакта, вызов, выбросивший исключение, инструмент не из `REGISTRY`) записывается с `not_verified` и `has_tool_result=False`.

> [!note]
> Упавший вызов зарегистрированного инструмента (таймаут, `lib_error`, `busy`, `invalid_input` изнутри `REGISTRY.invoke`) возвращает валидный `ToolResult(ok=False, trust="not_verified")` как артефакт. Он записывается с `has_tool_result=True` и `ok=False`; свидетельством не считается из-за `ok=False` (см. §4, `_is_evidence`), а не из-за флага.

Не попадает в журнал то, что отклонено раньше этого слоя: вызов сверх лимита (`TFLCallLimit`), spawning-инструмент в режиме завершения бюджета, `task` без выделения бюджета. Это отказы, а не результаты (см. [[budget]]).

### Id записи

Id равен `tool_call_id`, если он уникален в журнале. Иначе (пустой или повторный, например у скриптованных моделей в двух субагентах или после возобновления) id формируется как `<tool_call_id или "call">~<seq>`, и middleware дописывает `evidence_id` в содержимое `ToolMessage` (в JSON-объект ключом, к другому тексту префиксной строкой), чтобы модель могла процитировать. В `additional_kwargs["tfl_evidence_id"]` id лежит всегда.

### `parent_call`

`SPAWNING_TOOLS`: `task`, `spawn_subagent`, `run_workflow`. Пока такой вызов выполняется, его `tool_call_id` лежит в контекстной переменной, и вызовы внутри субагента получают его как `parent_call`. По нему различают записи разных вызовов одной роли; HITL 1 берёт IR именно из вызова ir-builder, который проверяется (см. [[hitl-and-review-modes]]).

### Подключение

Агент строят внутри `with use_evidence(ledger):`; вне такого блока и без `ledger=` `make_middleware` возвращает `None` и ничего не пишется. Тот же ledger прикрепляется к графу как `graph.tfl_evidence` (`agent.py`). Подробнее о жизненном цикле запуска см. [[run-lifecycle]].

### Целостность и повторное открытие

Каждая строка несёт `prev` (digest предыдущей) и `digest` (sha256 от канонического JSON записи без `digest`). `EvidenceLedger.verify()` пересчитывает цепочку по файлу и возвращает список проблем (пустой список означает целостность). Цепочка обнаруживает правку, удаление и перестановку строк, но не является границей безопасности: тот, кто может писать в файл, может пересобрать цепочку.

`EvidenceLedger.open(run_dir)` на существующем файле загружает записи, так что id остаются уникальными, а цепочка продолжается (HITL-возобновление в новом процессе). Оборванная последняя строка пропускается и попадает в `verify()`; следующая запись начинается с новой строки. Дубликат id при загрузке пропускается с замечанием.

Большие значения: `data` длиннее `MAX_DATA_CHARS` (200 000 символов JSON) и `args` длиннее `MAX_ARGS_CHARS` (50 000) хранятся как превью (`PREVIEW_CHARS` = 4000) с пометкой в `truncated`; `result_digest` покрывает полное значение.

> [!note]
> `ledger.py` не требует extra `[deep]`: он импортируется на чистой stdlib. `EvidenceMiddleware` без langchain не создаётся (`ImportError`).

## 4. Проверка, что отчёт ссылается на известные id

Источники: `tfl_deep_agent/tools/report.py`, `tfl_deep_agent/evidence/policy.py`.

Проверка двухслойная.

**Первый слой: отказ при подаче отчёта.** `make_submit_report_tool(evidence=...)` (агент передаёт журнал запуска; при `None` берётся журнал из окружающего `use_evidence`). После `validate_report` и проверки на утёкшую разметку вызова инструмента функция `unknown_evidence_ids` обходит все цитирования: `evidence_ids` отчёта, `claims[].evidence_ids`, `steps[].evidence_ids`, `lean.evidence_ids` (перечисляет `cited_evidence_ids`). Если хоть один id отсутствует в журнале, отчёт не принимается: инструмент возвращает ошибку (статус `error`, артефакта нет), так что отчёт не записывается и HITL 2 из-за него не останавливается. В тексте ошибки (`evidence_id_error`, `error_kind: "unknown_evidence_ids"`) перечислены неизвестные id с указанием места цитирования и валидные id, сгруппированные по имени инструмента с доверием и отпечатком IR. Если подставлено имя инструмента вместо id, это отмечается подсказкой. Код никогда сам не подменяет имя на id: модель должна подать отчёт заново. Без журнала id в этом месте не проверяются.

Правка отчёта человеком на HITL 2 проверяется той же парой функций (`hitl.py` вызывает `unknown_evidence_ids` и `evidence_id_error`), см. [[hitl-and-review-modes]].

**Второй слой: понижение доверия.** `evidence/policy.py`, `claim_trust`: id, которого нет в журнале, делает утверждение `not_verified`, что бы ещё оно ни цитировало. Дальше правила:

- берётся сильнейшее нереfuted-доверие среди цитируемых записей, у которых есть результат инструмента (`ok` и `has_tool_result`);
- цитируемая `refuted`-запись помечает утверждение как `contradiction` и ограничивает его `CONTRADICTION_CAP` = 0.50;
- свидетельством считается запись с `ok` и `has_tool_result` (`_is_evidence`); всё остальное (ответ субагента, встроенный инструмент, упавший вызов, в том числе с `ToolResult` и `ok=False`) попадает в `not_evidence`;
- `rate_claims` затирает присланные моделью поля `trust`, `confidence`, `confidence_cap`: утверждения оценивает код.

`evaluate_report` оценивает отчёт целиком: шаги и ключевые утверждения, доверие и потолок вердикта (через `agent.verdict_gate`, см. [[verdict-gate]]), итоговую уверенность `final_confidence = min(cap, model_confidence)` (собственная уверенность модели может только понизить потолок), опубликованный вердикт (`published_verdict`; для REG через `agent_system.lib.result_verdict`). В учёт идут все цитаты: шагов, утверждений, блока Lean и верхнего уровня; неизвестные id собираются в `unknown_ids`. Базой для вердикта служат все записи запуска, включая субагентные, поэтому нужны полные записи журнала, а не компактный `tfl_evidence` (в нём нет `args` и `data`).

> [!warning]
> Для вердиктной части `evaluate_report` нужен полный ledger или его записи. Компактного представления состояния хватает для оценки шагов, но вердикт тогда не получает основания (docstring `evaluate_report`).

Как неизвестные id показываются в отчёте и в окне HITL, см. [[reports]]; в нём (`reports/render.py`) тоже есть блок `unknown_ids`/`refuted_ids`.

## 5. Что смотреть при отладке

- Инструмент вернул не тот уровень доверия: смотреть потолок в декораторе, `GROUP_MAX_TRUST`, `trust_reason` (в нём остаётся пометка «lowered from ... to the ... ceiling»).
- Утверждение в отчёте `not_verified`: сначала `unknown_ids` и `not_evidence` в `claim_trust`, затем `has_tool_result` соответствующей записи в `evidence.jsonl`.
- Цепочка не сходится: `EvidenceLedger.load(run_dir).verify()`.
- Модель подаёт имя инструмента вместо id: это ожидаемый отказ `unknown_evidence_ids`, текст ошибки уже содержит список допустимых id.
- Тесты и скриптованные модели: [[testing-and-mock]]. Lean-инструменты: [[lean]]. Входные данные задачи: [[intake]].
