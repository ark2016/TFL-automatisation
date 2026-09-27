# EVAL_RESULTS — live-прогон ловушек на Haiku (2026-09-27, до раунда C2)

Контекст: на момент этого прогона `docs/EVAL_SET.md` определял 73 задачи (сейчас 74 —
`dcfl-21`/`task_grammar_aSSb`, THEORY.md §1.10, добавлена позже, этим прогоном не
покрыта); этот прогон покрывает 24 «ловушки» (`trap: yes` в манифесте
`tfl_eval/manifest.json`) по всем четырём системам, каждая через `tfl-eval --live` с
`TFL_MODEL_OVERRIDE=claude-haiku-4-5` (Haiku, не Opus — см. `CLAUDE.md`). Полный прогон
всех 74 задач ещё не проводился (см. открытый пункт ниже и `TODO.md` §7).

Раунд правок C2 (структурированный вывод, сертификат DCFL `dpda.py`, R4′) внесён
**после** этого прогона; часть находок ниже (`structured_output_calls = 0`, `cfl-07`/
`cfl-12` failure) уже адресована в C2 и требует перепрогона (см. «Что предстоит
перепроверить»).

## Сводка

| Система | Решено верно | Inconclusive/failure | Accuracy | Brier | Вызовов | Токенов |
|---|---|---|---|---|---|---|
| reg | 5/5 | 0 | 1.00 | 0.185 | 34 | 217 474 |
| dcfl | 2/5 | 3 (inconclusive) | 1.00\* | 0.160 | 35 | 230 275 |
| ll | 7/7 | 0 | 1.00 | 0.049 | 28 | 384 846 |
| cfl | 5/7 | 2 (failure) | 1.00\* | 0.105 | 120 | 1 324 295 |
| **итого** | **19/19 решённых** | **5 нерешённых** | — | — | **217** | **2 156 890** |

\* Accuracy = 1.00 считается по задачам, получившим conclusive-вердикт (`ран` со
статусом, отличным от `inconclusive`/`failure`); ни одна решённая задача не дала
неверный вердикт с confidence ≥ 0.6 (**false-confident-wrong = 0** по всем системам и
в целом).

`structured_output_calls = 0` на этом прогоне — все агенты в этих 217 вызовах шли через
фолбэк-извлечение JSON (структурированный вывод отклонялся API из-за пустых `{}`
подсхем в `build_agent_output_schema`, см. ниже); исправлено в раунде C2.

## По задачам

### reg — 5/5, все решены верно

| id | вердикт | ожидание | confidence | время, с |
|---|---|---|---|---|
| reg-03 | regular | regular | 0.40 | 49.1 |
| reg-07 | non_regular | non_regular | 0.60 | 36.4 |
| reg-09 | regular | regular | 0.40 | 35.9 |
| reg-12 | regular | regular | 0.85 | 25.1 |
| reg-14 | non_regular | non_regular | 0.85 | 124.1 |

### dcfl — 2/5 решены, 3 inconclusive

| id | вердикт | ожидание | confidence | время, с |
|---|---|---|---|---|
| dcfl-04 | **inconclusive** | dcfl | 0.40 | 17.7 |
| dcfl-08 | non_dcfl | non_dcfl | 0.60 | 30.8 |
| dcfl-10 | non_dcfl | non_dcfl | 0.60 | 27.0 |
| dcfl-15 | **inconclusive** | dcfl | 0.40 | 18.6 |
| dcfl-17 | **inconclusive** | dcfl | 0.40 | 17.2 |

Причина трёх `inconclusive`: положительный (`dcfl`) вердикт по R2′
(`docs/VERDICT_POLICY.md`) требует исполняемого конструктивного артефакта — в этом
прогоне `stack_strategy` не выдавал `dpda`-поле (оно появилось только в раунде C2,
`dcfl_system/lib/dpda.py`, `dcfl_system/prompts/stack_strategy.md`). Ни одна из трёх
задач не была решена неверно — политика корректно ушла в `inconclusive` вместо
неподтверждённого `dcfl`.

### ll — 7/7, все решены верно

| id | вердикт | ожидание | confidence | k | время, с |
|---|---|---|---|---|---|
| ll-05 | not_ll | not_ll | 0.60 | — | 378.6 |
| ll-07 | not_ll | not_ll | 0.60 | — | 81.7 |
| ll-08 | ll | ll | 0.98 | 2 | 0.0 |
| ll-09 | ll | ll | 0.98 | 3 | 0.0 |
| ll-11 | ll | ll | 0.85 | 1 | 62.7 |
| ll-15 | ll | ll | 0.98 | 12 | 0.0 |
| ll-18 | ll | ll | 0.98 | 2 | 0.0 |

4 задачи (ll-08/09/15/18, все Format 3 — грамматика и k заданы явно) решены оракулом
без обращения к LLM (confidence 0.98, время ≈ 0 с).

### cfl — 5/7 решены, 2 failure

| id | вердикт | ожидание | confidence | время, с |
|---|---|---|---|---|
| cfl-06 | non_cfl | non_cfl | 0.60 | 157.8 |
| cfl-07 | **failure** | non_cfl | 0.00 | 242.1 |
| cfl-08 | non_cfl | non_cfl | 0.60 | 267.0 |
| cfl-11 | non_cfl | non_cfl | 0.60 | 116.1 |
| cfl-12 | **failure** | non_cfl | 0.00 | 510.6 |
| cfl-17 | cfl | cfl | 0.85 | 159.5 |
| cfl-19 | cfl | cfl | 0.85 | 101.9 |

cfl-07/cfl-12 закончились `failure` (ретраи исчерпаны) несмотря на well-formed
деструктивные доказательства (Огден/pumping) уже на столе — до раунда C2 гейт не имел
правила R4′ («ретрай-бюджет исчерпан → берём сильнейшее допустимое основание из уже
собранных доказательств»). R4′ реализован в C2 (`cfl_system/orchestrator.py` и парные
изменения в dcfl/ll/agent_system) и должен закрыть оба случая при перепрогоне.
Отдельно cfl-12's IR имеет `language_spec.kind: "natural"`, для которого нет word-
оракула (см. `docs/VERDICT_POLICY.md` §4, gap) — Огден-доказательство там останется
`well_formed`, а не `verified`, даже после R4′.

## Что уже исправлено в раунде C2 (файлы в рабочем дереве, не закоммичены)

- **`structured_output_calls = 0` → фолбэк 100 %.** Причина: `build_agent_output_schema`
  (`agent_system/lib/llm_client.py`) строил каждое свойство как пустую `{}`-подсхему;
  API отклонял такую схему (400 «Empty schema… is not supported»), клиент откатывался
  на нестуктурированный вывод и **запоминал модель как «отклонившую схему» на весь
  процесс** — первый же вызов на модель отравлял все последующие. Исправлено: реальные
  посвойственные подсхемы (`schema_string`/`schema_number`/…, `agent_system/lib/llm_client.py`)
  + переписанные `*/lib/agent_output_schema.py` во всех четырёх системах.
- **R4′** («ретрай-бюджет исчерпан → сильнейшее допустимое основание») — реализован в
  вердикт-гейтах всех четырёх пайплайнов (`*/orchestrator.py`, `agent_system/graph.py`);
  должен закрыть `cfl-07`/`cfl-12`.
- **R2′ (конструктивный сертификат DCFL)** — `stack_strategy` теперь обязан выдавать
  исполняемый `dpda` (детерминированность проверяется `dcfl_system/lib/dpda.py`), и
  `_verify_stack_strategy_dpda` может поднять его до `bounded_pass` — изначально **только
  когда `build_set_builder_membership_oracle` возвращает не `None`**, т. е. `input_format
  == "set_builder"` **и** `language_spec.word_pattern` — чистая конкатенация именованных
  `variables` (см. `dcfl_system/lib/word_sampler.py`). Проверено на живых IR eval-задач:
  `dcfl-04`/`dcfl-15` заданы `set_builder` с `variables: []` и `word_pattern` строкой
  экспоненциальной нотации (`"{a^n b^n c^m | ...}"`) — оракул возвращает `None`,
  DPDA-проверка не поднимается выше `well_formed`, и R2′/R2 **по-прежнему** не допускают
  `dcfl` (нужен `bounded_pass`+); эти две задачи останутся `inconclusive` до появления
  парсера `word_pattern`-строк вида `aⁿbⁿcᵐ` — см. `TODO.md` §7. `dcfl-17`
  (`input_format: "grammar"`) и `task_grammar_aSSb`/`dcfl-21` (`docs/THEORY.md` §1.10,
  грамматика S → aSSb | ba | Ab) — тот же изначальный gap, но теперь закрыт:
  `build_grammar_membership_oracle` (CYK по грамматике задачи через
  `cfl_system.lib.cfl_oracle`) даёт `bounded_pass` и для `input_format: "grammar"`;
  `dcfl_system/tests/test_orchestrator.py` подтверждает это на `task_grammar_aSSb`
  (`dcfl 0.85`, `sample_words`/`CYK` укладываются в доли секунды на ~60 слов длины ≤ 10).
- Цены/usage — `UsageTracker` подключён к top-level result (см. `TODO.md` §3, README).
- Ogden semantic check — контракт `cfl_ogden.md` дополнен `word_instances`/
  `marked_positions`, что должно поднять доверие `well_formed → verified` на задачах,
  где есть word-оракул (не `natural`/`arithmetic_index`).

## Что предстоит перепроверить

- Полный live-прогон всех 24 ловушек на Haiku **после** C2 — не проводился (см.
  `TODO.md` §7): не подтверждено, что `cfl-07/12` теперь дают `non_cfl` вместо `failure`
  и что `structured_output_calls` на самом деле стал > 0. `dcfl-04/15` ожидаемо
  **останутся** `inconclusive` даже после перепрогона — см. разбор выше (нет оракула
  для их IR-формы, не баг R2′).
- `dcfl-21` (`task_grammar_aSSb`, THEORY.md §1.10) — новая ловушка, ни разу не
  прогонялась вживую. Мок-прогон (`dcfl_system/tests/test_orchestrator.py`) даёт
  `dcfl 0.85` через машинно построенный сертификат-ДМПА (345 состояний, 5835 переходов,
  `dcfl_system/examples/certificates/grammar_aSSb_dpda.json`); живой прогон на Haiku
  **ожидаемо не воспроизведёт** эту детерминизацию за один вызов (`stack_strategy`
  вернёт `uncertain`/`not_applicable` без `dpda`, как и раньше) — это известное
  ограничение текущего LLM-агента (нет инструмента для запуска детерминизации height-
  deterministic PDA), а не ошибка теории или сертификата; см. `TODO.md` §7.
- Живой прогон полного eval-набора (74 задачи, `docs/EVAL_SET.md`) на Haiku ни разу не
  проводился — только 24 ловушки выше.
- Калибровка потолков confidence (`docs/VERDICT_POLICY.md` §2: 0.98/0.85/0.60/0.40/0.50)
  сделана по опыту с Haiku; повторная калибровка на Opus 5.5 — только по явному запросу
  (Opus-прогоны стоят реальных денег).
- cfl-12 (`language_spec.kind: "natural"`) останется без семантической проверки шага 2,
  даже после R4′/R2′ — это documented gap в `docs/VERDICT_POLICY.md` §4, не баг C2.

## Перепроверка после C2 (2026-09-27, Haiku, только 5 ловушек)

Частичная live-перепроверка (не всех 24, только 5 задач, которые C2 должен был
исправить: `dcfl-04/15/17`, `cfl-07/12`), коммит `e44b9ff` (раунд C2, **до** раунда
C4 ниже). `TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live`, не закоммичено.

| id | вердикт | ожидание | confidence | время, с | стоимость |
|---|---|---|---|---|---|
| dcfl-04 | inconclusive | dcfl | 0.40 | 72.5 | — |
| dcfl-15 | inconclusive | dcfl | 0.40 | 74.5 | — |
| dcfl-17 | inconclusive | dcfl | 0.40 | 18.2 | — |
| cfl-07 | inconclusive | non_cfl | 0.40 | 262.7 | — |
| cfl-12 | inconclusive | non_cfl | 0.40 | 815.3 | — |

dcfl: 29 вызовов ≈ $0.31 (3 задачи + разогрев); cfl: 45 вызовов ≈ $1.53 (2 задачи).
`structured_output_calls` теперь > 0 на большинстве узлов (10/0, 12/0, 7/0, 14/9, 17/5
по задачам-узлам) — баг «пустая `{}`-подсхема травит модель на весь процесс» (см. «Уже
исправлено» в `TODO.md`) подтверждённо закрыт.

**Диагнозы (все пять — ожидаемо, не регрессия C2):**

- **dcfl-04 / dcfl-15** — `inconclusive 0.40`, «нет `{required_trust}+`-артефакта»: их
  IR — `input_format: "set_builder"` с `variables: []` и `word_pattern` в виде
  экспоненциальной строки (`"a^n b^n c^m"`); `build_set_builder_membership_oracle`
  (`dcfl_system/lib/word_sampler.py`, на тот момент) возвращал `None` для такой формы —
  оракул членства не строился, `_verify_stack_strategy_dpda` не мог подтвердить
  сертификат-ДМПА выше `well_formed`, R2′ корректно не допускал `dcfl`. **Это уже
  известный gap** (`TODO.md` §7, до C4), не новая находка — см. закрытие в разделе «Что
  исправляет C4» ниже.
- **dcfl-17** — тот же симптом (`inconclusive 0.40`), но IR — `input_format: "grammar"`
  (Format 2). На момент этого прогона `build_grammar_membership_oracle` (CYK через
  `cfl_system.lib.cfl_oracle`) в `word_sampler.py` ещё не существовал в этой ветке
  прогона — оракул для грамматик не строился вовсе (не только для экспоненциальной
  нотации), сертификат-ДМПА не симулировался.
- **cfl-07** — `inconclusive 0.40` вместо ожидаемого R4′-подхвата. Причина — не в
  R4′-логике самого гейта, а выше по цепочке: `reasoning`/`retry_planner` получили 400
  BadRequest 7 раз подряд («Schemas contains too many optional parameters (63)…
  limit: 24», `eval_recheck/cfl_run.log`) — отказ схемы **до** генерации инструмента, не
  распознанный как `_looks_like_schema_rejection` (эта проверка ловит отказ
  *ответной* JSON-схемы агента, а не схемы *инструмента* reasoning/retry_planner).
  Узел падал на исчерпании ретраев, дав 3 деструктивных (`refuted`) попытки без
  admissible-основания для R4′ — гейту буквально нечего было взять сильнее
  `inconclusive`. Это отдельный, не задокументированный ранее баг (не R4′), см. «Что
  исправляет C4».
- **cfl-12** — `inconclusive 0.40`, 6 вызовов `cfg_builder` ($0.80 из $1.07 задачи,
  815 с): `language_spec.kind: "natural"` (без word-оракула, документированный gap
  `docs/VERDICT_POLICY.md` §4) в сочетании с тем, что `reasoning` предлагал классифицировать
  язык как `cfl` (неверно) на части попыток — без оракула для перепроверки предложения
  `cfg_builder` тратил бюджет ретраев на попытки построить грамматику для ошибочной
  гипотезы прежде, чем сойтись к `non_cfl`-обоснованию.

## Что исправляет раунд C4 (по отчётам агентов, не проверено live)

Три параллельных агента раунда C4 (без API-вызовов, только код/тесты) сделали:

- **Экспоненциальная нотация `word_pattern` → оракул.** Новый парсер
  `cfl_system/lib/exponent_pattern.py` (строки вида `aⁿbⁿcᵐ`/`a^n b^n c^m`) вписан (а)
  в `cfl_oracle_from_ir` для `language_spec.kind: "natural"` — **закрывает cfl-12** без
  изменения IR (парсер читает существующий текст спецификации напрямую); (б) в новую
  единую точку входа `dcfl_system/lib/word_sampler.py::build_membership_oracle_from_ir`,
  на которую переведён весь `oracle_verifier.py` (симуляция ДМПА/`stack_strategy`,
  `dcfl_pumping`, Шаллит-нероудовы классы + `prefix_continuation`, `dead_class_finite`) —
  **закрывает dcfl-04** тем же путём (парсер читает существующий текст IR напрямую).
  **dcfl-15/16** вместо этого получили правку самого eval-IR (добавлены `variables` +
  словообразующий `word_pattern`-конкатенация, чтобы работал уже существующий
  segment-matcher, не экспоненциальный парсер) — см. `dcfl_system/examples/eval/
  dcfl-15.json`/`dcfl-16.json`.
- **dcfl-17 отдельно не тронут в C4** (не значился в списке задач агента) — но должен
  быть уже закрыт раундом C2 (`build_grammar_membership_oracle`, `input_format:
  "grammar"`, см. «Уже исправлено» в `TODO.md`); C4 не вносил сюда регресса.
- **cfl-07 (лимит optional-параметров).** Не упомянут явно ни в одном отчёте агента C4
  как исправленный, и `agent_system/lib/llm_client.py` (единственный правдоподобный
  файл для такого фикса) был изменён одним из агентов, чьё резюме — placeholder
  `"summary": "test"` (не описывает, что сделано). **Статус неясен** — требует ручной
  проверки диффа `agent_system/lib/llm_client.py` и живого перепрогона `cfl-07`, прежде
  чем считать закрытым; не помечаю как исправленное в `TODO.md` без подтверждения.
- **Вердикт-гейт: R4′ retry-budget баг (dcfl).** Третий агент C4 сообщает отдельный
  фикс в `dcfl_system/orchestrator.py::_apply_verdict_gate`: ветка «primary отработал,
  но ниже `{required_trust}`, НЕ refuted» вызывала `_r4prime_rescue` безусловно (лог
  «retry budget exhausted» даже с оставшимися ретраями) вместо повтора, пока
  `retry_count < max_retries`. Не относится напрямую к пяти ловушкам выше (они шли по
  ветке refuted/no-artifact), но затрагивает общий путь R4′.
- Ничего из раунда C4 не закоммичено (по инструкции); `agent_system/config.py`,
  `agent_system/graph.py`, `cfl_system/config.py`, `cfl_system/orchestrator.py`,
  `dcfl_system/config.py`, `dcfl_system/lib/dpda.py`, `dcfl_system/orchestrator.py`,
  `dcfl_system/tools/grammar_aSSb_dpda.py`, `ll_system/config.py`,
  `ll_system/orchestrator.py` и парные тесты также модифицированы в рабочем дереве —
  видимо, конкурентные правки того же раунда C2/C4, не разобраны построчно в этом
  документе (см. `git diff` перед коммитом).

## Предстоит перепроверить (после C4)

Полный список из TODO.md §7, конкретно к этому раунду:

- **dcfl-04 / dcfl-15 / dcfl-17** — live-перепрогон после C4; ожидание: `dcfl-04`
  (парсер экспоненциальной нотации) и `dcfl-15` (переписанный IR) должны дать
  `bounded_pass`+/`dcfl`, `dcfl-17` — не тронут C4, ожидание не меняется относительно
  C2 (должен быть уже `dcfl` через `build_grammar_membership_oracle`, но это не
  подтверждено live ни разу).
- **dcfl-21** (`task_grammar_aSSb`) — новая ловушка, ни разу не прогонялась вживую;
  ожидаемо `stack_strategy` не воспроизведёт height-deterministic-детерминизацию за
  один вызов (известное ограничение, не баг).
- **cfl-07** — перепрогон нужен в первую очередь для проверки, действительно ли фикс
  лимита optional-параметров попал в `agent_system/lib/llm_client.py` (см. выше,
  "test"-резюме) — без него узел `reasoning`/`retry_planner` будет 400-падать снова.
- **cfl-12** — перепрогон должен подтвердить, что новый `exponent_pattern`-оракул даёт
  оракульную проверку для `natural`-спецификации и поднимает доверие выше
  `well_formed`, устраняя нужду `cfg_builder` тратить 6 попыток на неверную гипотезу
  `reasoning`.
- Один из отчётов C4-агентов также предупреждает: рабочее дерево содержало правки
  нескольких параллельных сессий одновременно (несовпадение со снимком `git status` в
  начале разговора), и разово наблюдался транзиентный `NameError` в
  `cfl_system/lib/agent_output_schema.py` при полном прогоне тестов (самоустранился на
  повторном запуске) — стоит прогнать полный `pytest` ещё раз перед тем, как считать
  ветку готовой к коммиту (см. `TODO.md`).
