# TODO — находки аудита проекта (2026-09-26)

Источник — автоматический аудит (8 областей, каждая находка перепроверена отдельным агентом).
Номера строк соответствуют состоянию на коммит `cb2c314`; после раунда правок (2026-09-27, ветка `backlog`)
многие сдвинулись или устарели — там, где это очевидно, номера убраны, файл называется без строки.

**Уже исправлено** (не повторять):

- миграция на Opus 5.5 / Sonnet 5: effort, refusal, streaming, чтение блоков по типу;
- TFL Lab: path traversal, CSRF/Host, запуск всех 4 пайплайнов (`--save`);
- CFL: формат PDA, маскировка контрпримеров, `quick_verdict` при нерегулярном фильтре, «semilinear ⇒ CFL»;
- `_CONSTRUCTIVE_AGENTS` → tuple; `ui_server/tests` в CI; `anthropic>=0.77.0`;
- CodeQL: раздача файлов через индекс, `permissions: contents: read` в CI;
- документация: README (CLI, различия пайплайнов, теория DCFL/LL, тесты), корневой и подсистемные `CLAUDE.md`,
  `CONTRIBUTING.md`, шаблоны issue/PR, `SECURITY.md`;
- формулировка леммы Шаллита (`dcfl_system/prompts/shallit.md`) — заменена на теорему 4.7.4 (классы
  Майхилла–Нероуда) + ограничение про мёртвый класс, см. `docs/THEORY.md` §1.2;
- ложное эталонное доказательство в CFL-промптах (пересечение с `a⁺b⁺a⁺c⁺`) — заменено на корректный
  пример `R = a⁺b⁺ac·a⁺b⁺ac`, см. `docs/THEORY.md` §2;
- «LL: оракул проверяет strong LL(k), а не LL(k)» — теперь сначала дешёвый SLL(k)-тест, затем при
  необходимости полный тест Ахо–Ульмана (`docs/THEORY.md` §3.1); открытый подпункт про conclusive
  `not_ll` без сертификата — ниже;
- «`find_min_ll_k` на левой рекурсии работает около часа» — закрыт: сертификат левой рекурсии + бюджет
  по времени/числу конфликтов;
- §6: `ll_system/CLAUDE.md` «LL ∩ REG = LL» — утверждение было ложным (контрпример
  {aⁿw | w ∈ {b,c}ⁿ} ∩ (a*b* ∪ a*c*) = {aⁿbⁿ}∪{aⁿcⁿ}, не LL), см. `docs/THEORY.md` §3.2;
- ревизия раунда 2 (`docs/THEORY.md` Часть II): эталоны `dcfl_exam_01/02/03` (dcfl) заменены на `non_dcfl`
  (лемма Ю / Шаллита, §1.6–1.8), эталон `task_grammar_filter_49` (cfl) — на `non_cfl` (лемма Огдена, §2.3);
  пример `wbcwR` переведён на язык `{w b c wᴿ}` (LL(1): `S→aSa|bT, T→c|aSab|bTb`) с вердиктом `ll`;
  `{w b* c wᴿ}` остался ловушкой DCFL-не-LL (лемма об ограниченной гибкости, §3.4); interchange lemma
  переформулирована с условием плотности, лемма Соколовского удалена (§2.4); hard rule классификатора
  «равенство счётчиков — регулярно» исправлена (§2.3); REG-пример пересечения с `a*b*` в
  `grammar_analyzer.md`/`proof_checker.md`/`reasoning_agent.md`/`nerode_agent.md` исправлен (§0);
- live-прогоны на Haiku (`TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live`, 2026-09-27) на всех 5 exam-задачах
  раунда 2 (`dcfl_exam_01/02/03`, `task_grammar_filter_49`, `wbcwR`/`{w b c wᴿ}`) — все сошлись с новыми эталонами
  (`docs/THEORY.md` Часть II, §1.10);
- эталон `task_wwvvR` (`cfl_system/tests/test_e2e.py`) — заменён на `non_cfl` (язык {w w v vᴿ} доказанно не КС,
  проверено перебором на словах длины ≤ 10, `docs/THEORY.md` §2.5);
- шаг 1 таксономии доверия (`well_formed`/`verified`/`bounded_pass`/`not_verified`/`refuted`) и гейт R1–R7 —
  реализованы по `docs/VERDICT_POLICY.md` во всех четырёх системах (см. `result["verdict_gate"]`);
- LL: `not_ll` с оговоркой `k ≤ K` (`max_k_checked`) вместо conclusive без сертификата (`docs/VERDICT_POLICY.md` R5);
- `ll_system/orchestrator.py:631`: regularity-shortcut исправлен — читает `regularity_confidence`/`regularity_reason`,
  не завышает confidence эвристики;
- `ll_system/orchestrator.py:674-694`: Format 2 — заданная грамматика уходит в оракул, не только первая грамматика агента;
- `agent_system/graph.py:1094`: `lean_verified=True` только при `status == "valid" and sorry_count == 0`;
- рендереры (`dcfl_system/renderer.py`, `cfl_system`/`ll_system`) показывают поля контрактов раунда 2
  (`dead_class_finite`, `technique`, `common_prefix`, `branch_words` и т.п.) и метки доверия словами вместо
  зелёного баннера «verified» (`docs/VERDICT_POLICY.md` §5);
- §2 (устойчивость ретраев): API-ошибка/refusal больше не пропадает как `None` — `agent_system/lib/llm_client.py`
  (`_AgentAPIError`/`_AgentRefusal`) + `graph.py` возвращают/пробрасывают `agent_error` дальше по цепочке узлов;
  `dcfl_system/lib/retry_logic.py` (`_clamp_confidence`) — `confidence: null` не роняет пайплайн; пустой retry-план
  в `dcfl_system/orchestrator.py` (`decide_after_retry_planner`) терминален и не передиспатчит всех специалистов;
  ретрай на исчерпанном бюджете идёт через `renderer_node` (форсированный гейт), не через `early_failure_node`;
  `issues_found`/`error` учитываются через таксономию доверия (`trust`), не отдельной веткой; `cfl_system/orchestrator.py`
  ведёт карту отказов по раундам вместо append-only `state['errors']`; Haiku-repair помечает результат
  `_truncated`/`_repaired` (cfl/dcfl/ll `orchestrator.py`), не выдаёт обрезанный вывод как полноценный;
- §5: голый `pytest` из корня безопасен — корневой `conftest.py` (autouse-фикстура убирает `ANTHROPIC_API_KEY` и
  подменяет `anthropic.Anthropic`) + `pyproject.toml` `[tool.pytest.ini_options] testpaths`;
- §4: `pumping_len.py` — точный поиск `p_min` (не эвристика «первое повторение состояния»), `ε`/`∅` как
  языковые литералы, конечные языки без исключений, CLI (`--witness`, `--json`) и собственные тесты (`tests/`);
  `ui_server/static/index.html` — `marked`-вывод санитизирован (DOMPurify), CDN на cdnjs с SRI, CSP через `<meta>`,
  iframe `sandbox`; `ui_server/server.py` — `MAX_CONCURRENT_RUNS`, `RUN_TIMEOUT_SECONDS`, `POST .../cancel`;
  корень репозитория очищен (`tmp*/`, `.codex_tmp/`, лишние `.env`, `gen_pass.py`) и `tmp*/` в `.gitignore`;
- §6: дублирующее поле `markdown` убрано из контракта `cfl_formalizer`; строки `**Model:** ...` убраны из промптов;
  `advisory_only` больше не пишется агентами (остаётся только в старых example-фикстурах);
- §5/упаковка: `pyproject.toml` — console scripts `tfl-reg`/`tfl-cfl`/`tfl-dcfl`/`tfl-ll`/`tfl-lab`,
  `package-data` для `prompts/*.md`/`examples/`/`templates/`/`static/`, тесты исключены из wheel;
  `.github/workflows/tests.yml` обновлён (см. дифф);
- §2 (раунд B): `agent_system/graph.py` — стейтфул-ретраи (`previous_output` полностью в retry_context агента,
  краткое summary в `specialist_results` для planner'а), Level-1 retry — ограниченный цикл (`MAX_LEVEL1_RETRIES=2`,
  §5.2) с повторным оракульным тестом после **каждой** попытки, включая ретрай только `re_builder`;
- §3: **общий LLM-клиент** — `agent_system/lib/llm_client.py` (`AnthropicClient.build_request_kwargs()`/`.call()`,
  типизированные `FatalAPIError`/`RetryableAPIError`, backoff с джиттером, семафор конкурентности через
  `TFL_MAX_CONCURRENCY`, `UsageTracker`/`as_dict()`); `cfl/dcfl/ll_system/orchestrator.py`'s `LiveRunner` — тонкие
  обёртки вокруг него;
- §3: **structured outputs** (`output_config.format`, GA, без beta-заголовка) — основной путь парсинга ответов
  агентов во всех четырёх системах (`agent_system/lib/agent_output_schema.py` + копии в cfl/dcfl/ll_system),
  с автоматическим фолбэком на старую эвристику извлечения JSON при отказе API от схемы (`_looks_like_schema_rejection`);
  `input_parser`/`formalizer`/`ll_input_parser` намеренно оставлены на старом пути (не фиксированная форма вывода);
- §5: общий scripted-двойник `agent_system/lib/testing/fake_anthropic.py` (thinking-блок, `max_tokens`, `refusal`,
  обрыв стрима, фатальные/повторяемые ошибки) + тесты путей `run_agent`/`LiveRunner`/`LLMRunner`
  (`*/tests/test_run_agent_paths.py`) во всех четырёх системах; `agent_system/tests`: добавлены тесты retry/invert/
  Level-1 (`test_retry_flow.py`), `_extract_json` (`test_extract_json.py`), `grammar_preprocessor`
  (`test_grammar_preprocessor.py`), `claim_verifier` расширен;
- §1: **мост word-оракула для `ll_system` `set_builder`** — `ll_system/lib/word_oracle.py`
  (`oracle_from_ll_ir`/`generate_words`: домены `nat`/`enum`/`word`, `rev(...)`, связанные переменные),
  подключён в `claim_verifier.py` шаг 2 (`prefix_classes` для `set_builder`-IR теперь проверяется семантически,
  как dcfl `nerode_classes`); Format 2/3 (грамматика) по-прежнему без семантической проверки — пункт закрыт частично,
  см. открытый список ниже;
- §1: семантическая проверка (шаг 2, §4) для `dcfl_system/lib/oracle_verifier.py` — `dcfl_pumping` (лемма Ю)
  теперь брутфорсит оба условия (1)/(2) на нескольких `p`, включая `i=3` как tie-breaker, с бюджетом на число
  разложений (см. открытый пункт про калибровку бюджета ниже);
- §7: **tfl-eval** — пакет `tfl_eval/` (манифест на 74 задачи из `docs/EVAL_SET.md`, `runners.py`/`metrics.py`/`cli.py`),
  IR-файлы для eval-набора под `*/examples/eval/`, метрики (точность общая/по системе/на ловушках, Brier, доля
  inconclusive, "уверенно неверно"); `--live` реализован, но ни разу не прогонялся (см. открытый пункт).
- live-прогон 24 ловушек на Haiku (2026-09-27, до раунда C2) — 19/19 решённых верно, 0 ложно-уверенных ошибок;
  результаты и диагнозы см. `docs/EVAL_RESULTS.md`; закрыл на deferred-инфраструктуре (полный прогон 73 задач
  и перепрогон ловушек после C2 — остаются открытыми, см. §7);
- раунд C2: **сертификат DCFL** (R2′, `docs/VERDICT_POLICY.md`) — `stack_strategy` обязан выдавать исполняемый
  `dpda` (`dcfl_system/lib/dpda.py`: синтаксическая проверка детерминированности), иначе `not_applicable`/`uncertain`,
  не голая прозаическая «стратегия» (`dcfl_system/prompts/stack_strategy.md`, `dcfl_system/tz_dcfl_agent_system.md` §5.2/§6.1.1);
- раунд C2: **R4′** («ретрай-бюджет исчерпан → гейт берёт сильнейшее допустимое основание среди уже собранных
  доказательств») реализован в вердикт-гейтах всех четырёх пайплайнов (`cfl/dcfl/ll_system/orchestrator.py`,
  `agent_system/graph.py`), заменяя прежний безусловный откат в `inconclusive`/`failure`;
- раунд C2: **баг structured outputs** — `build_agent_output_schema` строил пустые `{}`-подсхемы, API их отклонял,
  а клиент запоминал модель как «отклонившую схему» на весь процесс, из-за чего первый же вызов на модель отравлял
  все последующие (объясняет `structured_output_calls = 0` в прогоне выше); исправлено — реальные посвойственные
  подсхемы (`schema_string`/`schema_number`/`schema_boolean`/`schema_null`/`schema_array`/`schema_object`/`nullable`
  в `agent_system/lib/llm_client.py`) и переписанные `*/lib/agent_output_schema.py` во всех четырёх системах;
- раунд C2: **Ogden semantic check контракт** — `cfl_system/prompts/cfl_ogden.md` теперь требует `word_instances`
  и `marked_positions` (индексированные по `p`), без которых `verify_agent_claims` не мог поднять доверие выше
  `well_formed` (проверялось на реальных предикате/`grammar_filter` оракулах, `cfl_system/tests/test_claim_verifier.py`);
- цены/usage за прогон — `UsageTracker.as_dict()` подключён к top-level result JSON и CLI `--verbose`
  во всех четырёх системах (`agent_system/tests/test_usage_block.py` и аналоги в cfl/dcfl/ll);
- статус языка `task_grammar_aSSb` (exam_04) установлен: **DCFL**, `docs/THEORY.md` §1.10 (профильный НМПА,
  height-determinism, [NS, Thm 4]); машинно построенный сертификат-ДМПА в
  `dcfl_system/examples/certificates/grammar_aSSb_dpda.json` (345 состояний, 5835 переходов), инлайнен в мок
  `dcfl_system/examples/mock/dcfl_exam_04_stack_strategy.json`; `dcfl_system/lib/word_sampler.py` получил
  `build_grammar_membership_oracle` (CYK по грамматике задачи через `cfl_system.lib.cfl_oracle`), так что
  `oracle_verifier` теперь может дать `bounded_pass` и для `input_format: "grammar"`-задач (закрывает заодно
  половину пункта про `dcfl-17`, см. открытый список ниже) — E2E `dcfl_system/tests/test_orchestrator.py`
  эталон `dcfl 0.85`; открыто: компактный ДМПА вручную (не требуется для вердикта, см. §7);
- раунд C4: **оракулы для экспоненциальной нотации (`cfl-12`, `dcfl-04`) и для
  переписанного IR (`dcfl-15/16`)** — новый парсер `cfl_system/lib/exponent_pattern.py`
  (строки вида `aⁿbⁿcᵐ`) вписан в `cfl_oracle_from_ir` (`language_spec.kind: "natural"`)
  и в единую точку `dcfl_system/lib/word_sampler.py::build_membership_oracle_from_ir`
  (на неё переведён весь `oracle_verifier.py`); `dcfl-15/16` вместо парсера получили
  правку eval-IR (`variables` + конкатенационный `word_pattern`). Не проверено вживую —
  см. `docs/EVAL_RESULTS.md` «Перепроверка после C2» / «Что исправляет C4» и открытый
  пункт в §7;
- раунд C4: **вердикт-гейт R4′ retry-budget баг (dcfl)** — ветка «primary отработал, но
  ниже `{required_trust}`, НЕ refuted» в `_apply_verdict_gate`
  (`dcfl_system/orchestrator.py`) вызывала `_r4prime_rescue` безусловно вместо повтора,
  пока `retry_count < max_retries`; исправлено, чтобы вести себя как ветка refuted;
- раунд C5: **live-перепроверка после C4** (`dcfl-04/15/17/21`, `cfl-07/12`, Haiku,
  коммит `8eeda88`) проведена — см. `docs/EVAL_RESULTS.md` «Live-перепроверка после C4».
  `cfl-12` подтверждён верным (`non_cfl 0.60`); `cfl-07` — честный `inconclusive`, не
  баг. Закрыла неопределённость по этим 6 задачам, но **открыла новые находки** — см.
  ниже (R2′ ε-нормализация и dcfl-21 false-confident);
- раунд C5: **R2′ каноническая ε-нормализация ДМПА** —
  `dcfl_system/lib/dpda.py::normalize_epsilon_accept_sinks` реализована с необходимым
  условием безопасности сверх буквального текста политики (не только «`q_acc` без
  исходящих переходов», но и «`q`, на который ссылается `(q, Z) -> q_acc`, статически
  никогда не встречается с другим стек-топом, кроме `Z`») — буквальная формулировка без
  этого условия ложно принимала бы слова вроде `"aab"` (контрпример на живом ДМПА
  `dcfl-04`, `dcfl_system/tests/test_dpda.py::TestNormalizeEpsilonAcceptSinksUnsafeCase`).
  Живой ДМПА `dcfl-04` после нормализации по-прежнему `refuted`, не `bounded_pass` — это
  не баг нормализации, а реальный дефект конкретного сгенерированного автомата (нужен
  редизайн состояний, не переименование), см. `docs/EVAL_RESULTS.md`;
- раунд C5: **`docs/VERDICT_POLICY.md` §4 dcfl/shallit — `dead_class_status` enum**
  (`empty`/`finite`/`infinite`) вместо свободнотекстового `dead_class_finite`, реализовано
  в `dcfl_system/prompts/shallit.md`, `dcfl_system/lib/agent_output_schema.py`,
  `dcfl_system/lib/oracle_verifier.py` (см. дифф); мок `dcfl_exam_02_shallit.json`
  обновлён. Известный gap: у reduction-based доказательств Шаллита оракул проверяет
  мёртвый класс по IR задачи целиком, а не по производному языку L2 доказательства —
  задокументирован как follow-up (см. открытый список ниже);

Легенда: 🔴 high · 🟠 medium · ⚪ low; объём: S ≤ 30 мин · M ≤ день · L > дня.

---

## 1. Честность вердиктов (главная тема)

Перенесено в «Уже исправлено» (см. список выше по файлу): live-прогоны Haiku на всех 5 задачах раунда 2
(эталоны 2026-09-27), шаг 1 таксономии доверия (`well_formed`/`verified`/`bounded_pass`/`not_verified`),
гейт R1–R7 (`docs/VERDICT_POLICY.md`), `not_ll` с оговоркой `k ≤ K` без сертификата, regularity-shortcut
(`ll_system/orchestrator.py:631`), Format 2 в оракул, `lean_verified` при `sorry_count == 0`, рендереры
(`dead_class_finite`, `technique`, `common_prefix`, `branch_words` и т.п. теперь отображаются).

Остаётся открытым:

- [ ] 🟠 **S** **`ll_system`: мост word-оракула для `set_builder` — только Format 1.** Реализован в
  `ll_system/lib/word_oracle.py` (`oracle_from_ll_ir`/`generate_words`) и подключён в `claim_verifier.py` шаг 2
  для `set_builder`-IR. Format 2/3 (грамматика задана явно) по-прежнему без семантической проверки шага 2 (там
  `prefix_classes`/`constructive` не проверяются оракулом) — остаётся открытым.
- [ ] 🟠 **M** **Полная семантическая проверка доказательств.** Шаг 2 из `docs/VERDICT_POLICY.md` §4 покрывает
  cfl (pumping/ogden/closure_reduction), dcfl (pumping/Shallit), ll (constructive/substitution/prefix_classes),
  reg (pumping/Nerode) там, где оракул есть; это остаётся частичной, а не полной верификацией содержания
  доказательства — общая переносимая инфраструктура (не только «есть оракул — есть проверка») ещё не сделана.
- [ ] ⚪ **M** **Калибровка потолков confidence после прогонов на Opus.** Потолки в `docs/VERDICT_POLICY.md` §2
  (0.98/0.85/0.60/0.40/0.50) подобраны по опыту с Haiku (см. живые прогоны 2026-09-27); нужна повторная калибровка
  на Opus 5.5 (только по явному запросу — прогоны на Opus стоят реальных денег, см. `CLAUDE.md`).

**Находки live-прогона 2026-09-27 (Haiku, все 5 exam-задач раунда 2 сошлись с новыми эталонами):**

- `closure_reduction` на Haiku утверждал `L ∩ a⁺b⁺ = {aⁿbⁿ}` вместо верного `{a²ᵐb²ᵐ | m ≥ 2}` — грамматика/PDA были
  синтаксически правдоподобны, но описание пересечения неверно. Закрыто добавлением поля `intersection_examples`
  (конкретные слова пересечения) и шагом 2 (`docs/VERDICT_POLICY.md` §4): слова из `intersection_description`
  проверяются оракулом на принадлежность `L ∩ R`, расхождение → `refuted`, а не `well_formed`.
- `pumping_cfl` на ретрае иногда ломал JSON, добавляя мета-реплику до/после объекта (например «Хорошо, пробую другой
  подход:» перед `{`) — парсер падал на валидном по существу ответе. Промпт ретрая ужесточён: явный запрет текста
  вне JSON-объекта, только сам объект в ответе.

## 2. Устойчивость ретраев и ошибок

- [ ] 🟠 **S** Лимит Haiku-repair 8000 токенов не позволяет починить большие обрезанные ответы (`_truncated`-флаг
  уже проставляется, см. «Уже исправлено»; сам лимит не увеличен).
- [ ] 🟠 **M** `agent_system/graph.py`: planner не может добавить агента, которого сам не ретраил; пустой
  `agents_to_retry` → полный re-dispatch; `should_invert_hypothesis` никто не читает.
- [ ] 🟠 **M** `agent_system/graph.py`: опровержение closure-claim подогнано под aⁿbⁿ (считает литералы `a`/`b`);
  по таймауту 120 с остаётся брошенный поток; проверка повторяется каждый раунд.
- [ ] ⚪ **S** `dcfl_system/orchestrator.py`: `agent_error` при ретрае затирает валидный результат прошлого раунда.
- [ ] ⚪ **S** `dcfl_system/lib/closure_table.py` / `oracle_verifier.py`: словари направлений не согласованы
  (`both` против `constructive/destructive`).

## 3. LLM-интеграция (архитектура)

- [ ] ⚪ **S** **Prompt caching** (`cache_control`) для больших повторных промптов (`cfl_reasoning`, `cfl_pumping`, `ll_reasoning_agent`, 15–20 KB);
  `student_notes` — отдельным блоком. Проверять по `usage.cache_read_input_tokens`.
- [ ] ⚪ **S** `agent_system/lib/llm_client.py`: `student_notes` дублируются в system prompt и в user JSON.
- [ ] ⚪ **S** `agent_system`: при refusal JSON-ретрай делает лишний второй вызов (`run_agent` не отличает refusal от невалидного JSON).
- [ ] ⚪ **—** После миграции перемерить стоимость и время на 1–2 задачах (на Haiku через `TFL_MODEL_OVERRIDE`, затем выборочно на Opus)
  и подстроить `EFFORT` по агентам.

## 4. TFL Lab и корень репозитория

- [ ] ⚪ **S** `agent_system/orchestrator.py`: мёртвые копии `_run_formalizer`, `_verify_closure_claim` (сверить с
  текущим файлом — номера строк устарели).

## 5. Тесты, CI, упаковка

- [ ] 🟠 **M** `dcfl_system/examples/mock/*_reasoning.json`: в моках нет `action`, поэтому все E2E идут через `_fallback_reasoning`;
  LLM-путь reasoning, collect/fan-in и `_verify_*` не тестируются.
- [ ] ⚪ **S** Contract-тесты промптов: каждый ```json-блок парсится и содержит обязательные ключи; все ключи, которые формирует
  builder входа, упомянуты в промпте (по образцу `cfl_system/tests/test_pda_contract.py`).
- [ ] ⚪ **S** `agent_system/tests/test_phase3.py`: шаблоны Lean никогда не компилируются в CI (Lean-задача в CI неблокирующая, а не
  выполняющая реальную компиляцию — см. `.github/workflows/tests.yml`).

## 6. Документация и промпты

- [ ] ⚪ **S** 14 промптов: блоки «Reasoning (Chain-of-Thought)» перед JSON и черновые реплики «Wait — ...»
  (`cfl_pumping.md`, `cfl_decomposition.md` и др., сверить с текущим текстом). С adaptive thinking рассуждения в
  выводе не нужны — почистить.
- [ ] ⚪ **S** `agent_system/prompts/reasoning_agent.md`, `pumping_agent.md`: инструкция требует русский текст, а примеры на английском.
- [ ] ⚪ **S** Ключи входа (`grammar_facts`, `retry_context`, `ir`/`classifier_hint`) не совпадают с разделом Input Format в промптах
  (`agent_system/graph.py`, `dcfl_system/orchestrator.py`).
- [ ] ⚪ **M** Контракты вывода — pseudo-JSON в промптах всё ещё различаются по системам (`module`/`agent`/`agent_name`,
  разные наборы status); structured outputs (`output_config.format`, см. «Уже исправлено») закрывают парсинг
  через закрытую JSON-схему для каждого агента, но сами промпты/примеры пока не унифицированы по именованию полей.
- [ ] ⚪ **S** `agent_system/orchestrator.py`: `input_parser` настроен, но нигде не вызывается (шаг parse из спецификации
  отсутствует) → реализовать `--text` или пометить как deferred. (`ll_system` уже реализовал `--text`
  через `input_parser` — см. «Уже исправлено»; остаётся только `agent_system`.)
- [ ] ⚪ **S** `agent_system/lib/claim_verifier.py`: алфавит `[ab]` захардкожен, нет левой границы слова, `in\s+L` матчит «in length».
- [ ] ⚪ **S** `ll_system/lib/ll_ir_schema.py:88`: `$`/`ε` не зарезервированы и конфликтуют с маркером конца ввода.

## 7. Стратегия

- [x] **M** **`tfl-eval`: первый живой прогон.** 24 ловушки (не все 73, набор на тот момент) прогнаны на Haiku
  2026-09-27 — 19/19 решённых верно, 0 ложно-уверенных ошибок; результаты, диагнозы и что уже исправлено —
  `docs/EVAL_RESULTS.md`.
- [x] **S** **Частичная перепроверка 5 ловушек после C2 (до C4).** `dcfl-04/15/17`, `cfl-07/12` — все
  5 дали `inconclusive 0.40` (не регрессия: ожидаемые gap'ы на тот момент, см. диагнозы в
  `docs/EVAL_RESULTS.md` «Перепроверка после C2»); подтвердило, что `structured_output_calls > 0`
  (баг «пустая `{}`-подсхема травит модель» закрыт) и выявило ранее незадокументированный баг cfl-07
  (лимит optional-параметров инструмента reasoning/retry_planner, не пойман `_looks_like_schema_rejection`).
- [ ] 🟠 **M** **Живой прогон полного eval-набора (74 задачи).** 6 ловушек (`dcfl-04/15/17/21`,
  `cfl-07/12`) перепрогнаны после C4 (раунд C5, см. `docs/EVAL_RESULTS.md` «Live-перепроверка после C4»).
  Остаются: полный набор из 74 задач `docs/EVAL_SET.md` (~49 нетравматичных задач ни разу не прогонялись
  вживую), плюс повторный прогон `dcfl-04/15` после редизайна `stack_strategy`-ДМПА (R2′-нормализация
  сама по себе не поможет этим двум конкретным автоматам, см. ниже) и `dcfl-21` после проверки
  `dead_class_status`.
- [x] **S** **cfl-07 живая перепроверка после C4.** Подтверждено (раунд C5): узел больше не 400-падает
  по лимиту optional-параметров, `structured_output_calls > 0`; итог — честный `inconclusive 0.40`
  (оракул не может опровергнуть/подтвердить деструктивные доказательства Haiku ни за одну попытку) — не
  баг, ожидаемый предел текущей модели. См. `docs/EVAL_RESULTS.md`.
- [x] **M** **Оракулы для `dcfl-04`/`dcfl-15`/`cfl-12` (экспоненциальный `word_pattern`/`natural`).**
  Закрыто раундом C4, подтверждено раундом C5 живьём: `cfl-12` → `non_cfl 0.60` ✓. `dcfl-04`/`dcfl-15`
  оракул членства строится, но обе задачи **всё равно** остаются `inconclusive` — блокер сместился на
  саму R2′-нормализацию ДМПА (см. следующий пункт), не на отсутствие оракула. (`dcfl-17`, `input_format:
  "grammar"`, закрыт ранее в C2: `build_grammar_membership_oracle`.)
- [ ] 🟠 **M** **R2′-нормализация ε-переходов не спасает конкретные ДМПА `dcfl-04`/`dcfl-15`.**
  `normalize_epsilon_accept_sinks` (раунд C5, `dcfl_system/lib/dpda.py`) реализована с необходимым
  условием безопасности, но живые сгенерированные Haiku-автоматы для этих двух задач нарушают именно это
  условие (состояние перед ε-переходом в `q_accept` делит стек-топ с буквенными переходами) — нужен
  редизайн промпта/примера `stack_strategy.md`, чтобы модель строила ε-переход из состояния, действительно
  изолированного по стек-топу, а не просто переименовывала состояния. См. `docs/EVAL_RESULTS.md`.
- [ ] 🔴 **S** **dcfl-21 (`task_grammar_aSSb`/exam_04) — ложно-уверенный `non_dcfl`, найдено раундом C5.**
  Живой прогон дал `non_dcfl 0.60` через `shallit`/`nerode_classes`, хотя доказательство само признаёт
  бесконечность мёртвого класса (`dead_class_finite`: «D бесконечен, теорема неприменима») — по
  `docs/VERDICT_POLICY.md` §4 это должно было форсировать `dead_class_status: not_applicable`, а не
  `well_formed`-вердикт. Нужно подтвердить, что узел `shallit` в этом промпте реально эмитит новую
  enum-схему (не старый свободнотекстовый `dead_class_finite`), и/или усилить `_verify_shallit`
  (`dcfl_system/lib/oracle_verifier.py`) так, чтобы признание бесконечности мёртвого класса в тексте
  доказательства форсировало `not_applicable` независимо от формы поля; затем перепрогнать `dcfl-21`.
  См. `docs/EVAL_RESULTS.md`.
- [ ] ⚪ **M** **Калибровка потолков confidence на Opus.** См. §1 — только по явному запросу (реальные деньги).
- [ ] ⚪ **S** **Lean 4 в CI не выполняет реальную компиляцию** — см. §5 (3 skipped-теста нужен Docker `tfl-lean4`,
  CI-джоба неблокирующая).
- [ ] ⚪ **S** **Компактный ДМПА для exam_04 (`task_grammar_aSSb`) вручную.** Машинный сертификат
  (345 состояний, 5835 переходов, `dcfl_system/examples/certificates/grammar_aSSb_dpda.json`) достаточен
  для вердикта и уже используется моком; ручное компактное построение не требуется для честности вердикта,
  остаётся как отдельное упражнение/документация (не блокирует ничего из открытого выше).
- [ ] 🟠 **S** **`cfg_builder` обрезка по `max_tokens` (cfl-12).** Живой прогон (раунд C5,
  `docs/EVAL_RESULTS.md`) показал два обрезанных по `max_tokens=64000` ответа `cfg_builder` до финальной
  сходимости на верном `non_cfl 0.60`; итог верный, но стоимость/время растут из-за повторов после
  обрезки — поднять лимит для этого агента в `dcfl_system/config.py`/`cfl_system/config.py` либо разбить
  ответ на меньшие структурные шаги.
- [ ] 🟠 **M** **Мост word-оракула для Format 2/3 (`ll_system`)** — см. §1: Format 1 (`set_builder`) закрыт
  (`ll_system/lib/word_oracle.py`), явно заданная грамматика (Format 2/3) по-прежнему без семантической проверки шага 2.
- [ ] 🟠 **M** **R3′ (исполняемый конструктивный артефакт) для dcfl/ll помимо уже сделанного ДМПА** — R2′ дал
  dcfl исполняемый сертификат (`dpda.py`); `ll_system` и остальные конструктивные заявления dcfl (кроме
  `stack_strategy`) пока не имеют аналогичного исполняемого артефакта — оценить, где R3′ применимо дальше.
  Также: ll-04 использует моки, не авторизованные под свой конкретный IR (нужна ревизия), а Format-3 LL-задачи
  (`is_ll_k`/`is_sll_k`) пока не сведены в отдельную per-k метрику.
- [ ] **L** Общая библиотека оракулов (CYK/Earley, PDA-симулятор, валидатор грамматик, `is_grammar_equivalent_sample`),
  чтобы dcfl и ll проверяли членство слов так же, как cfl.
- [ ] **M** Один источник правды для документации: таблица возможностей пайплайнов генерируется или проверяется тестом.
