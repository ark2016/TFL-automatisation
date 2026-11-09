---
title: Оценка вердикта кодом (verdict_gate)
tags: [deep-agent, verdict, trust, evidence]
---

# Оценка вердикта кодом (verdict_gate)

Уровень доверия (`trust`) и потолок уверенности (`confidence_cap`) вердикта задаёт код, а не модель. Модель предлагает вердикт и ссылки на свидетельства (`evidence_ids`). Код решает, что из этого допустимо.

> [!important] Источник правил
> Таблицы уровней, потолки и правила R1-R6 определены в [`docs/VERDICT_POLICY.md`](../../../docs/VERDICT_POLICY.md). Эта заметка описывает только то, как они реализованы в `tfl_deep_agent`. Правило меняют сначала в политике, затем в коде. Формулировки лемм лежат в `docs/THEORY.md`.

Связанные заметки: [[tools-and-evidence]] (откуда берутся записи со свидетельствами), [[hitl-and-review-modes]] (HITL 1 и HITL 2), [[run-lifecycle]], [[testing-and-mock]].

## Где что лежит

| Что | Где |
|---|---|
| Уровни доверия, порядок, `clamp_trust`, `strongest_trust`, `confidence_cap` | `tfl_deep_agent/tools/base.py` |
| Таблица потолков `CONFIDENCE_CAPS` | импортируется в `tools/base.py` из `agent_system.lib.claim_verifier` без копирования (ключи: `verified`, `bounded_pass`, `well_formed`, `not_verified`; ключа `refuted` нет, это проверяет `tests/test_tools_base.py`) |
| `verdict_gate`, `_classify`, `collect_result` | `tfl_deep_agent/agent.py` |
| Оценка шагов и утверждений, `final_confidence`, `published_verdict`, `evaluate_report` | `tfl_deep_agent/evidence/policy.py` |
| Привязка к IR, одобренному человеком (`formalization_binding`) | `tfl_deep_agent/hitl.py` |
| Отпечаток IR (`ir_fingerprint`) | `tfl_deep_agent/tools/ir.py` |

## Уровни доверия и потолки

Источник: `tfl_deep_agent/tools/base.py`.

Порядок: `refuted < not_verified < well_formed < bounded_pass < verified`. Потолки по комментарию в коде: verified 0.98, bounded_pass 0.85, well_formed 0.55, not_verified 0.40. Для `refuted` и неизвестного уровня `confidence_cap` возвращает потолок `not_verified`.

- `refuted` не участвует в выборе «сильнейшего» свидетельства: `strongest_trust` его пропускает, а `trust_at_least(refuted, ...)` всегда ложно.
- `clamp_trust(trust, ceiling)` понижает уровень до потолка. `refuted` он по умолчанию не трогает.

> [!note]
> Потолок 0.50 при противоречии (`CONTRADICTION_CAP` в `evidence/policy.py`) не уровень доверия, а отдельное ограничение поверх потолка по уровню.

## Два слоя оценки

Источник: `tfl_deep_agent/evidence/policy.py`.

1. **Шаги и утверждения** (`claim_trust`, `rate_claims`). Уровень равен сильнейшему не опровергнутому `trust` среди процитированных записей, у которых есть результат инструмента (`ok` и `has_tool_result`).
   - Id, которого нет в журнале, делает утверждение `not_verified`, что бы ещё оно ни цитировало.
   - Цитата записи `refuted` помечает утверждение как `contradiction` и ограничивает потолок значением 0.50.
   - Запись без результата инструмента (текстовый ответ субагента, встроенный инструмент, неудачный вызов) попадает в `not_evidence` и не считается свидетельством.
   - `rate_claims` отбрасывает `trust`, `confidence`, `confidence_cap`, которые модель сама положила в утверждение, и подставляет только `trust` и `confidence_cap` из `claim_trust`. Собственного `confidence` утверждение после этого не имеет.
2. **Вердикт** (`evaluate_report`) вызывает `agent.verdict_gate`. Цитируются id из шагов, ключевых `claims`, блока `lean.evidence_ids` и из верхнего `evidence_ids`. Основанием вердикта служат записи всего журнала, включая субагентов.

`final_confidence(cap, model_confidence)` равна `min(cap, уверенность модели)`. Самооценка может только понизить потолок. Если она не число или NaN, остаётся потолок.

`published_verdict` для REG читает вердикт через `agent_system/lib/result_verdict.result_verdict`. Там `null` означает частичный или неразрешённый результат. Остальные классы публикуют токен гейта.

## collect_result: два пути

Источник: `tfl_deep_agent/agent.py`.

- **С журналом свидетельств** (`evidence=` задан, так вызывает `hitl.py`). Оценка идёт через `_rate_from_ledger` и `evaluate_report`. В `result` попадают `steps`, `claims` (только если в отчёте есть непустые `claims`), `trust`, `confidence_cap`, `trust_basis`, `verdict_direction`, `verdict_token`, `published_verdict`, `verdict_gate`, `basis`, `contradiction`, `excluded_ir_mismatch`, `required_ir_fingerprint`, `unknown_ids`, `refuted_evidence`, `trust_source`.
- **Без журнала** (запасной путь, в основном тесты). Свидетельства берутся только из `ToolMessage` главного потока, и вердикт оценивает тот же `verdict_gate`. Результаты субагентов тут не видны.
- **Отчёта нет.** `status` будет `incomplete` или `budget_exhausted`, `trust` равен `not_verified`, `confidence_cap` равен 0.40, а `partial_report` собирает код. Подробности про бюджет см. в [[budget]].
- `pending_report` оценивает черновик отчёта до HITL 2. Результат получает `status: awaiting_human`.
- Параметр `formalization` копируется в `result["formalization"]`.

## verdict_gate

Источник: `tfl_deep_agent/agent.py`. Ниже шаги в том порядке, в каком код их выполняет.

1. **Направление.** `verdict_direction` разбирает текст `verdict` вместе с полем `answer` (`yes`, `no`, `inconclusive`) и даёт `constructive`, `destructive` или `inconclusive`. Разбор консервативный. Отрицание внутри текста («CFL but not DCFL») без `answer` даёт `inconclusive` и пояснение. Расхождение текста и `answer` тоже даёт `inconclusive`. Токены вердикта лежат в `VERDICT_TOKENS` (например, `regular` / `non_regular` / `partial` для REG, `ll` / `not_ll` / `uncertain` для LL).
2. **Привязка к IR** (см. следующий раздел).
3. **Отсев по отпечатку.** Если требуемый отпечаток известен, результаты для другого IR исключаются. Их id попадают в `excluded_ir_mismatch`. Отпечаток записи берёт `_entry_fingerprint`: из `data.ir_fingerprint` либо считается от `args.ir`.
4. **Inconclusive** сразу даёт `not_verified` и потолок 0.40.
5. **Классификация записей** (`_classify`). Для каждой записи определяется, о какой стороне вердикта она говорит и с каким уровнем. Считаются только проверки относительно задачи (IR). Остальное (`cyk`, `run_dfa`, инструменты слов и теории, `regex_to_dfa` по выражению от модели) остаётся вспомогательным и основанием вердикта не служит.
6. **Опровержение.** Процитированный результат, опровергающий сторону вердикта, блокирует вердикт (R2). Если опровергающий результат не процитирован, отбрасываются другие проверки того же артефакта. Артефакт определяется по аргументу из `_ARTIFACT_KEYS` (`dfa`, `regex`, `grammar`, `dpda`, `pda`, `automaton`).
7. **Допустимое основание.** Конструктивный вердикт требует уровень не ниже `bounded_pass` (R2), деструктивный не ниже `well_formed` (R1). Иначе результат `not_verified`, а причина записывается в `verdict_gate`.
8. **Несколько IR.** Если отчёт не назвал отпечаток, а допустимое основание ссылается на разные IR, вердикт `not_verified`.
9. **Уровень основания** равен сильнейшему допустимому. В `basis` попадают id записей этого уровня. Основанием служит лучший допустимый результат, независимо от того, процитировал ли его отчёт (R4'). Цитаты могут только понизить уровень.
10. **Противоречие (R3).** Допустимое свидетельство противоположной стороны означает `contradiction: true`. Тогда возможны три исхода: `verified`-основание против не `verified` стороны сохраняет вердикт с потолком 0.85; `verified`-свидетельство против не `verified`-основания даёт `not_verified`; остальное даёт inconclusive-токен с потолком 0.50.
11. **Процитированные опровергнутые вспомогательные результаты** дают `contradiction` и потолок 0.50.
12. **`finish`.** Id из цитат без результата в журнале понижает уровень на одну ступень (не ниже `not_verified`). Для `not_verified` при вердикте не inconclusive токен заменяется на inconclusive.

Все причины понижения накапливаются в списке `verdict_gate` и показываются в результате и отчёте (R4).

> [!tip] Как отлаживать
> Если вердикт неожиданно `not_verified`, открой `result["verdict_gate"]`. Каждый шаг выше добавляет туда строку. Пустой `basis` вместе с «none found» или «best: ...» означает, что ни одна запись не прошла `_classify` для нужной стороны.

## Что считается основанием по классам

Источник: `_classify` в `tfl_deep_agent/agent.py`. Это описание кода, а не правила. Правила смотри в [`docs/VERDICT_POLICY.md`](../../../docs/VERDICT_POLICY.md).

- **REG.** Конструктивная сторона: `dfa_vs_ir`, а также `regex_to_dfa` в режиме IR. Деструктивная: `nerode_probe` (если проверены пары) и `pump_check_reg` в режиме `claim`. Для `pump_check_reg` уровень зажат до `well_formed` (R6).
- **CFL.** Конструктивная: `grammar_vs_ir` и `input_grammar_cfg` (при `cls=cfl`). Деструктивная: `pump_check_cfl` в режиме `claim`.
- **DCFL.** Конструктивная: `dpda_vs_ir`. Деструктивная: `pump_check_cfl` в режиме `claim` и `nerode_probe` с `cls=dcfl`, у которого уровень зажат до `well_formed` (проверено конечное число пар).
- **LL.** Основанием служит только `ll_table`. Его вес зависит от `data.grammar_source`: грамматика, которую написал агент (`grammar`), даёт максимум `bounded_pass` (при совпадении с `grammar_vs_ir`) или `well_formed`. Грамматика из `ir.grammar` или `ir.language_spec` может дать `verified`. Результат `grammar_filter` не считается языком. Сопоставление с запрошенным `k` (`_ll_verdict_k` берёт его из текста `LL(k)`) и ветки R5 для `min_k` разобраны прямо в коде.
- **Lean (R-Lean).** Результат `lean_check` для REG, CFL и DCFL со статусом `proved`, `trust == verified` и заданной `side` даёт `verified` для этой стороны. Для LL Lean-пути в `_classify` нет. См. [[lean]].

### regex_to_dfa(ir=...) и привязка к определению задачи

`_is_task_definition_dfa` отбирает результат, в котором регулярное выражение или линейную грамматику прочёл сам инструмент из IR (`task_definition`, `source == ir.language_spec`). `_task_definition_dfa_is_bound` делает его `verified` только если одновременно:

- вердикт называет IR (есть отпечаток);
- IR одобрил человек на HITL 1 (не `--auto-approve` и не отключённый HITL 1);
- определение встречается в исходном тексте IR (`definition_in_source_text`).

Иначе запись считается `bounded_pass`, как `dfa_vs_ir`, если сверка с оракулом проверила хотя бы одно слово. Причина понижения записывается в `verdict_gate`.

## Привязка к IR, одобренному человеком

Источники: `tfl_deep_agent/hitl.py` (`formalization_binding`), `tfl_deep_agent/agent.py` (`verdict_gate`).

`formalization_binding(records, on=...)` читает записи HITL по точке формализации (`FORMALIZATION`), игнорируя `superseded`, и берёт последнюю. При `on=False` возвращает `None`, то есть HITL 1 выключен. Статусы:

| `status` | Когда |
|---|---|
| `approved` | последнее решение `approved`, `edited` или `unchanged`, и есть `ir_fingerprint` |
| `no_ir` | одобрено, но отпечатка нет |
| `rejected` | последнее решение иное |
| `not_held` | записей по формализации нет |

Поле `auto` отмечает прогон, где остановки подтверждены автоматически.

В `verdict_gate`:

- Параметр `formalization=None` означает HITL 1 выключен. Действует только правило отпечатка из самого отчёта (`report["ir_fingerprint"]`).
- Статус не `approved` даёт `not_verified` и потолок 0.40 без какого-либо основания. Причина берётся из `_FORMALIZATION_BLOCKS`.
- При `approved` требуемым становится отпечаток одобренного IR, **а не тот, что назвала модель**. Если отчёт назвал другой, добавляется строка в `verdict_gate`, и результаты оцениваются относительно одобренного IR.
- `ir_human` истинно только при `approved` и без `auto`. От него зависит `verified` для `regex_to_dfa(ir=...)`.

> [!warning] auto-approve
> При `--auto-approve` или автоматическом режиме проверок IR считается одобренным, но человеком не просмотренным. Привязка к отпечатку при этом работает, а `verified` для `regex_to_dfa(ir=...)` пропадает. Режимы проверки описаны в [[hitl-and-review-modes]].

`ir_fingerprint(ir)` в `tools/ir.py` равен `"ir-"` плюс первые 12 шестнадцатеричных символов sha256 от канонического JSON (ключи отсортированы). Любая правка IR меняет отпечаток, и результаты для старого IR перестают считаться.

## Что понижает доверие (сводка)

Источники: `agent.py`, `evidence/policy.py`.

- Нет одобренного IR при включённом HITL 1: `not_verified`.
- Результаты для другого IR: исключаются (`excluded_ir_mismatch`).
- Основание из нескольких IR при отчёте без отпечатка: `not_verified`.
- Вердикт неоднозначный или `inconclusive`: `not_verified`, потолок 0.40.
- Нет допустимой проверки нужной стороны (R1, R2): `not_verified`.
- Процитированный результат опровергает сторону вердикта (R2): `not_verified`.
- Верифицированное свидетельство противоположной стороны (R3): `not_verified`.
- Неразрешённое противоречие: inconclusive-токен, потолок 0.50.
- Процитированный опровергнутый вспомогательный результат: потолок 0.50.
- Процитированный id без результата в журнале: уровень на ступень ниже. Для шага или утверждения такой id даёт `not_verified`.
- Самооценка модели: только `min(cap, confidence)`.

## Что учесть при изменениях

- Новый инструмент становится основанием вердикта, только если `_classify` его перечисляет. Достаточно того, что инструмент вернул `trust`, недостаточно.
- Новый токен вердикта добавляют в `VERDICT_TOKENS`. Для REG публикуемый вердикт дополнительно проходит `result_verdict` из `agent_system`.
- Таблицу уровней и потолков не копируют: `tools/base.py` берёт её из `agent_system.lib.claim_verifier`, значит правка идёт там и в политике.
- Тесты гейта лежат в `tfl_deep_agent/tests` (например, `test_evidence.py`, `test_class_ladder.py`). Как их запускать без живых вызовов, см. [[testing-and-mock]].
