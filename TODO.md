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
- §7: **tfl-eval** — пакет `tfl_eval/` (манифест на 73 задачи из `docs/EVAL_SET.md`, `runners.py`/`metrics.py`/`cli.py`),
  IR-файлы для eval-набора под `*/examples/eval/`, метрики (точность общая/по системе/на ловушках, Brier, доля
  inconclusive, "уверенно неверно"); `--live` реализован, но ни разу не прогонялся (см. открытый пункт).

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
- [ ] 🟠 **M** **Статус языка `task_grammar_aSSb` (exam_04, dcfl) не установлен.** Мок `stack_strategy` без
  `proof_sketch` ⇒ trust `not_verified` ⇒ вердикт по гейту `inconclusive` (не `dcfl 0.55`, `docs/THEORY.md` §5) —
  это верно по политике, но не заменяет теорию: нужно доказать принадлежность/непринадлежность языка DCFL, чтобы
  можно было завести конструктивный мок с настоящим `proof_sketch` и conclusive-эталон.

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
- [ ] ⚪ **S** Учёт токенов и стоимости за прогон (сейчас `usage` печатается только в verbose-режиме) — `UsageTracker.as_dict()`
  ещё не подключён в top-level result JSON пайплайнов и в CLI `--verbose`; сам трекер готов
  (`agent_system/lib/llm_client.py`), нужно только связать его в `run_pipeline`/CLI каждой системы.
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

- [ ] **M** **`tfl-eval`: живой прогон.** Скелет реализован (`tfl_eval/` — манифест на 73 задачи из
  `docs/EVAL_SET.md`, `runners.py`/`metrics.py`/`cli.py`, IR-файлы под `*/examples/eval/`, метрики: точность
  общая/по системе/на ловушках, Brier, доля inconclusive, «уверенно неверно»); `--live` реализован, но никогда
  не запускался (задача прямо запрещала вызовы API) — нужен первый прогон на Haiku
  (`TFL_MODEL_OVERRIDE=claude-haiku-4-5`, только по запросу), затем выборочно на Opus для калибровки потолков (см. §1).
  Также: ll-04 использует моки, не авторизованные под свой конкретный IR (нужна ревизия), а Format-3 LL-задачи
  (`is_ll_k`/`is_sll_k`) пока не сведены в отдельную per-k метрику.
- [ ] **L** Общая библиотека оракулов (CYK/Earley, PDA-симулятор, валидатор грамматик, `is_grammar_equivalent_sample`),
  чтобы dcfl и ll проверяли членство слов так же, как cfl.
- [ ] **M** Один источник правды для документации: таблица возможностей пайплайнов генерируется или проверяется тестом.
