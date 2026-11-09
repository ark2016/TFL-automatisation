---
title: HITL и режимы подтверждения
tags: [deep-agent, hitl, review-modes]
---

# HITL и режимы подтверждения

Точки остановки агента на проверку человеком, формат решений, режимы `all` / `formalization` / `auto`, `approve_rest` и пометки об автоматическом утверждении. Связанные заметки: [[verdict-gate]], [[run-lifecycle]], [[reports]], [[tfl-lab]], [[budget]], [[testing-and-mock]], [[intake]].

Источники: `tfl_deep_agent/hitl.py` (вся логика), `tfl_deep_agent/README.md` (разделы «Human in the loop» и «Review modes»). Пометки в отчёте: `tfl_deep_agent/reports/render.py`; учёт автоматического утверждения в шлюзе вердикта: `tfl_deep_agent/agent.py`.

## Точки остановки

Константы `FORMALIZATION`, `REPORT`, `EXECUTE` в `hitl.py`. Какие точки включены, задаёт `settings.hitl` (по умолчанию `formalization` и `report` включены, `execute` выключена; см. README, раздел Settings). Выключенная точка не останавливает запуск вовсе, в том числе в режиме `all`.

| Точка | Где срабатывает | Чем реализована |
|---|---|---|
| HITL 1, `formalization` | перед следующим вызовом модели главным агентом после ответа `ir-builder` или после принятого `validate_ir` самого главного агента | `FormalizationReviewMiddleware.before_model` (LangGraph `interrupt`) |
| HITL 2, `report` | перед выполнением `submit_report` | `report_interrupt_config()` для `interrupt_on` deepagents (`HumanInTheLoopMiddleware`) |
| `execute` | перед командой песочницы роли `coder` | `execute_interrupt_config()` |

Оба запроса имеют форму `HITLRequest` (`action_requests`, `review_configs`), ответ имеет форму `HITLResponse` (`{"decisions": [...]}`). Поэтому CLI, TFL Lab и адаптер оценки обрабатывают их одним кодом. `request_point()` определяет точку по имени действия (`formalization`, `submit_report`, `execute`, иначе `other`).

### HITL 1: формализация

Источник: `hitl.py`, функции `formalization_request`, `formalization_card`, класс `FormalizationReviewMiddleware`.

- Запрос строится кодом, а не моделью. `ir_checks()` запускает `validate_ir` и `ir_to_text` по IR, поэтому пересказ и слова «в L / не в L» нельзя считать перефразировкой модели. Эти вызовы не записываются в журнал доказательств.
- IR берётся из JSON-блока в ответе `ir-builder`, иначе из последнего принятого `validate_ir` внутри этого же вызова `ir-builder` (`find_ir`, привязка по `parent_call` журнала). Вызовы других `ir-builder` не подмешиваются. Если IR не найден, карточка так и говорит.
- Если у IR уже есть одобренный отпечаток (`approved` или `edited` в `tfl_hitl`), остановки нет: пишется запись `unchanged`.
- Если подряд пришло несколько ответов, проверяется последний, остальные получают `superseded`.
- Состояние хранится в ключе `tfl_hitl` (редьюсер `merge_reviews`: запись с тем же `(point, id)` заменяет прежнюю).
- Решение доходит до модели сообщением, начинающимся с `HITL 1 (formalization): ...`.

> [!warning]
> HITL 1 ловит только IR, построенный `ir-builder` или проверенный самим главным агентом через `validate_ir`. IR, собранный внутри шага workflow или порождённым субагентом, README прямо называет непроверяемым на HITL 1.

### HITL 2: отчёт

Источник: `hitl.py`, `report_interrupt_config`, `report_review_text`.

- Предикат `when` пропускает остановку только для отчёта, поля которого проходят `validate_report` и (если передан журнал) все id доказательств которого есть в журнале. Иначе остановки нет, а ошибку получает модель от самого `submit_report`.
- Текст карточки `report_review_text` считается кодом: доверие вердикту, потолок уверенности, основание, снижения шлюза, доверие по шагам, строка про решение HITL 1 (`formalization_note`). Слова модели доверия не задают. Правила вердиктов: `docs/VERDICT_POLICY.md`, см. [[verdict-gate]].
- Пока запуск ждёт HITL 2, рендерится черновик `result.*` со статусом `awaiting_human`; его оценка лежит в `run.json` в поле `draft`.

### execute

Конфиг `execute_interrupt_config()` разрешает все три решения. Подробностей про песочницу в `hitl.py` нет; см. [[roles-and-subagents]].

## Решения: approve / edit / reject

Источник: `hitl.py`, `Decision`, `parse_decision`, `resume_value`.

- `DECISIONS = ("approve", "edit", "reject")`. `Decision.source` по умолчанию `human`; в комментарии к полю перечислены `human`, `auto`, `dialog`, но код значение не проверяет (`__post_init__` проверяет только `type`, `parse_decision` пропускает любую строку).
- `reject` требует комментарий (`--comment` или `--payload '{"comment": ...}'`), иначе `DecisionError`: агент продолжит работу с этим комментарием.
- `edit` требует `--payload` с JSON-объектом изменяемых полей. Если после сравнения с оригиналом ни одно поле не изменилось, решение превращается в `approve`.
- `resume_value()` проверяет решение до возобновления. При несоответствии бросается `DecisionError`, а состояние запуска остаётся прежним.

Что редактируется:

| Точка | Поля `edit` | Проверка |
|---|---|---|
| HITL 1 | `transcription`, `ir`, `class` (`FORMALIZATION_EDIT_FIELDS`) | IR проходит `validate_ir`; смена `class` требует и IR; пустая транскрипция отвергается |
| HITL 2 | поля `SubmitReportArgs` (README: `verdict`, `answer`, `method`, `steps`, `template`, `summary` и др.) | отчёт валидируется заново; id доказательств вне журнала отвергаются до возобновления |

Изменённые поля HITL 2 помечаются в отчёте как отредактированные.

Что получает модель:
- approve на HITL 1: сообщение «APPROVED» с отпечатком IR.
- edit на HITL 1: «APPROVED WITH EDITS» с новым текстом и IR и указанием, что результаты оракула для старого IR не засчитываются, проверки надо повторить.
- reject на HITL 1: комментарий и запрет начинать доказательство, пока не одобрена новая формализация.
- reject на HITL 2: ошибка `submit_report` с комментарием (`reject_message`); агент правит отчёт и вызывает `submit_report` снова.

## Привязка вердикта к решению HITL 1

Источник: `hitl.py`, `formalization_binding`, `run_binding`; README.

`formalization_binding` возвращает `None` при выключенном HITL 1, иначе статус:

- `approved`: последнее решение `approved`, `edited` или `unchanged`, и есть отпечаток IR; засчитываются только результаты оракула для этого IR;
- `rejected`: последнее решение отклонило формализацию, и после него ничего не одобрено;
- `no_ir`: одобрено, но IR не было;
- `not_held`: HITL 1 включён, но ни одна формализация до него не дошла.

По README, отклонённая или ни разу не рассмотренная формализация оставляет вердикт `not_verified`. Подробности шлюза: [[verdict-gate]].

## Режимы подтверждения

Источник: `hitl.py` (`REVIEW_MODES`, `resolve_review_mode`, `review_mode_of`, `auto_approves`), README, раздел «Review modes».

Режим один на запуск, хранится в `run.json` в поле `review_mode`. Задаётся при старте: `--review-mode` в CLI, поле `review_mode` в TFL Lab ([[tfl-lab]]).

| Режим | HITL 1 | HITL 2 | `execute` |
|---|---|---|---|
| `all` (по умолчанию) | ждёт человека | ждёт человека | ждёт человека |
| `formalization` | ждёт человека | утверждается автоматически | ждёт человека |
| `auto` | автоматически | автоматически | автоматически |

Тонкости:
- `auto_approve=True` / `--auto-approve` является псевдонимом `auto`. Сочетание с другим явным режимом даёт `RunError`; неизвестный режим тоже.
- Старый `run.json` без поля `review_mode` читается как `auto` при `auto_approve`, иначе как `all` (`review_mode_of`).
- Решение принимает `auto_approves(mode, point)`; цикл `_segment` вызывает её на каждом прерывании. Автоматическое решение это `Decision("approve", source="auto")`, оно проходит через тот же `resume_value`, что и человеческое.
- Включены ли точки, решает `settings.hitl` (в `start_run` параметр `hitl`: `True` значит настройки, словарь точек или `False`).

> [!note]
> Режим только отвечает на остановку без человека. Он не меняет бюджет и не отключает проверок кодом. Изменение лимитов и отключение точки HITL, по CLAUDE.md проекта, решение пользователя.

## approve_rest

Источник: `hitl.py`, `resume_run`.

`resume_run(..., approve_rest=True)` (CLI `--approve-rest`, поле `approve_rest` в `POST /api/deep/runs/<id>/resume`) допустим только вместе с `approve`; иначе `DecisionError`. Эффект:

1. Текущая остановка утверждается как решение человека (в записи `review["approve_rest"] = True`).
2. В `run.json` `review_mode` становится `auto`, `auto_approve` становится `True`, `initial_review_mode` сохраняет прежний режим, `approve_rest` получает время, точку, `interrupt_id`, источник и ревьюера.
3. Все последующие остановки утверждаются автоматически с `source: auto`. Режим хранится в `run.json`, поэтому его помнит и следующий процесс.

Утверждение формализации через `approve_rest` по-прежнему считается человеческим (README, `run_binding`).

## Что помечается как автоматически утверждённое

- В истории (`run.json`, поле `reviews`): запись с `source: auto` и решением `auto_approved` (`resume_value`).
- В отчёте: метка «без проверки человеком» (`NO_HUMAN_REVIEW_MARK` в `reports/render.py`). Текст для `human_review`: `AUTO_APPROVED_LABEL` из `hitl.py` (`review_summary`). Если записей HITL 2 нет, метка `not reviewed`.
- `formalization_note` пишет «auto-approved (no human review)», если `binding["auto"]`. В карточке HITL 2 эта строка не появляется: `report_review_text` вызывает `formalization_binding` без `auto=` (по умолчанию `False`) и не использует `run_binding`, так что там `auto` всегда `False`. Пометку `auto` проставляет `run_binding` (его вызывают `_write_awaiting` и `final_result`).
- В шлюзе вердикта (`agent.py`): автоматически утверждённая формализация не считается одобрением человеком, так что `regex_to_dfa(ir=...)` остаётся `bounded_pass` (README); в причине снижения указано, что IR утверждён автоматически.

`run_binding` сначала передаёт в `formalization_binding` `auto = (review_mode_of(run) == auto)`. Только для статуса `approved` он переопределяет `auto` по истории: IR считается одобренным человеком, если на HITL 1 есть запись с `source != "auto"`, решением `approved` или `edited` и тем же отпечатком. Для `rejected`, `no_ir` и `not_held` `auto` остаётся взятым из режима. Следствия для `approved`:
- режим `formalization`: одобренный человеком IR остаётся человеческим, хотя HITL 2 автоматический;
- режим `auto`, затем `approve_rest`: формализация, утверждённая автоматически, остаётся автоматической.

> [!warning]
> Когда в автоматическом запуске (в том числе в оценке `tfl-eval`) вердикт получил низкое доверие, смотрите `reviews` в `run.json` и блок `hitl` результата: там `review_mode`, `initial_review_mode`, `approve_rest`, `formalization`.

## Запуск, пауза и возобновление

Источник: `hitl.py` (`start_run`, `resume_run`, `_segment`, `_write_awaiting`, `TTYDialog`), README.

- Состояние агента лежит в SQLite-чекпоинтере `checkpoints.sqlite` каталога запуска. Остановка без ответа даёт `run.json` со `status: awaiting_human`, полем `pending` и карточкой `hitl.md`; процесс завершается с кодом 3 (`EXIT_AWAITING`). Остальные коды: 0 отчёт принят, 1 завершён без отчёта. Подробно: [[run-lifecycle]].
- Новый процесс продолжает запуск командой `tfl-deep --resume DIR --decision ...`. Прерывание возобновляется через `Command(resume={interrupt_id: value})`; на HITL 1 повторно выполняется только `before_model`, субагент заново не вызывается.
- `dialog` (в CLI это `TTYDialog`: approve / edit / reject / later) спрашивает в том же процессе. Ответ `None` (later) оставляет запуск в ожидании. Решение, не подходящее запросу, в диалоге не принимается, вопрос повторяется.
- Живой запуск при `resume` требует `live=True`, mock-запуск `live` отвергает; кастомная фабрика моделей передаётся заново. См. [[budget]] и [[testing-and-mock]].
- Короткая карточка для TFL Lab: `pending_card()`, лежит в `run.json` как `pending.card` (не более 4 строк короче 90 символов, `details_md`, `editable`); строится кодом без вызова модели.

## Что менять и где

- Новый режим: `REVIEW_MODES` и `auto_approves` в `hitl.py`, затем README (таблица), `cli.py` (флаг), `ui_server` и тесты.
- Новое редактируемое поле HITL 1: `FORMALIZATION_EDIT_FIELDS`, `_edit_formalization`, `FormalizationReviewMiddleware._apply`.
- Текст, который видит модель: `_apply`, `reject_message`.
- Текст карточки: `formalization_card`, `report_review_text`.
- Что считается одобрением человеком: `run_binding`; затем `agent.py` и `reports/render.py`, чтобы метки не разошлись.
- Правила вердикта и уверенности меняются сначала в `docs/VERDICT_POLICY.md`. Теория: `docs/THEORY.md`.
