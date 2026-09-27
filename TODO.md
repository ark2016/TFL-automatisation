# TODO — находки аудита проекта (2026-09-26)

Источник — автоматический аудит (8 областей, каждая находка перепроверена отдельным агентом).
Номера строк соответствуют состоянию на коммит `cb2c314` и со временем могут сдвинуться.

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
  зелёного баннера «verified» (`docs/VERDICT_POLICY.md` §5).

Легенда: 🔴 high · 🟠 medium · ⚪ low; объём: S ≤ 30 мин · M ≤ день · L > дня.

---

## 1. Честность вердиктов (главная тема)

Перенесено в «Уже исправлено» (см. список выше по файлу): live-прогоны Haiku на всех 5 задачах раунда 2
(эталоны 2026-09-27), шаг 1 таксономии доверия (`well_formed`/`verified`/`bounded_pass`/`not_verified`),
гейт R1–R7 (`docs/VERDICT_POLICY.md`), `not_ll` с оговоркой `k ≤ K` без сертификата, regularity-shortcut
(`ll_system/orchestrator.py:631`), Format 2 в оракул, `lean_verified` при `sorry_count == 0`, рендереры
(`dead_class_finite`, `technique`, `common_prefix`, `branch_words` и т.п. теперь отображаются).

Остаётся открытым:

- [ ] 🟠 **M** **`ll_system`: мост word-оракула для `set_builder`.** Шаг 2 (детерминированная семантическая проверка,
  `docs/VERDICT_POLICY.md` §4) поднимает trust с `well_formed` до `bounded_pass`/`refuted` только там, где оракул
  уже есть (cfl: CYK/grammar_filter; dcfl: `word_sampler`+шаблоны; reg: regex/DFA). Для LL-задач в формате
  `set_builder` общего word-оракула по-прежнему нет (открытый пункт из отчёта раунда 1) — без него trust остаётся
  `well_formed`, потолок confidence 0.60.
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

- [ ] 🟠 **S** `agent_system/lib/llm_client.py` + `graph.py:121-144`: ошибка API → `None`, в `state['errors']` не попадает;
  при мёртвом ключе итог `partial 0.85, errors=[]`; JSON-ретрай повторяет auth/refusal.
  → возвращать dict `agent_error`; на `AuthenticationError/PermissionDeniedError/NotFoundError` падать сразу.
- [ ] 🟠 **S** `dcfl_system/lib/retry_logic.py:51`: `float(None)` при `confidence: null` роняет пайплайн → `_clamp_confidence` (`orchestrator.py:403`).
- [ ] 🟠 **S** `dcfl_system/orchestrator.py:509-511`: пустой retry-план перезапускает всех 5 Opus-специалистов (в пробе 15 лишних вызовов)
  → сделать пустой план терминальным.
- [ ] 🟠 **S** `dcfl_system/orchestrator.py:736-746`: `retry` на лимите уходит в `early_failure`, вердикт 0.95 теряется → направлять в `_fallback_reasoning`.
- [ ] 🟠 **S** `dcfl_system/orchestrator.py:669`: считать `issues_found`/`error` провалом наравне с `refuted`.
- [ ] 🟠 **S** `cfl_system/orchestrator.py:98, 650-668`: ошибки раундов не очищаются, агент, успешный при ретрае, остаётся в `agents_failed`
  → вести карту отказов по раундам.
- [ ] 🟠 **S** Haiku-repair после `max_tokens` возвращает обрезанный вывод как полноценный
  (`cfl_system/orchestrator.py` ~430, `dcfl` ~346, `ll` ~406) → флаг `_truncated/_repaired`, статус `inconclusive`, ограничить confidence.
  Лимит repair 8000 токенов не позволяет починить большие ответы.
- [ ] 🟠 **M** `agent_system/graph.py:163-187, 726-739`: ретраи без состояния — агенту говорят «твой DFA/regex неверен», но не показывают его;
  planner (`:885-900`) видит только status/verdict → передавать `previous_output`.
- [ ] 🟠 **M** `agent_system/graph.py:741-769`: Level-1 retry перезапускает `re_builder`, но новый regex не перетестируется; 1 попытка вместо 2 (§5.2).
- [ ] 🟠 **M** `agent_system/graph.py:916, 490-494`: planner не может добавить агента; пустой `agents_to_retry` → полный re-dispatch;
  `should_invert_hypothesis` никто не читает.
- [ ] 🟠 **M** `agent_system/graph.py:256-322`: опровержение closure-claim подогнано под aⁿbⁿ (считает литералы `a`/`b`);
  по таймауту 120 с остаётся брошенный поток (`:206-253`); проверка повторяется каждый раунд.
- [ ] 🟠 **M** Нет разделения ошибок API на фатальные (400/401/403/404) и повторяемые (429/529/overloaded, обрыв стрима);
  ошибки посреди стрима не ретраятся; fan-out без ограничения конкурентности (`dcfl_system/orchestrator.py:324` и копии).
- [ ] ⚪ **S** `dcfl_system/orchestrator.py:492-494`: `agent_error` при ретрае затирает валидный результат прошлого раунда.
- [ ] ⚪ **S** `cfl_system/orchestrator.py:533`: исключения раннера возвращаются как `None` без записи — упавший агент выглядит пропущенным.
- [ ] ⚪ **S** `dcfl_system/lib/closure_table.py:36` / `oracle_verifier.py:199-203`: словари направлений не согласованы
  (`both` против `constructive/destructive`).

## 3. LLM-интеграция (архитектура)

- [ ] 🟠 **L** **Общий LLM-клиент** вместо 4 копий (`agent_system/lib/llm_client.py`, `cfl/dcfl/ll_system/orchestrator.py: LiveRunner`).
  Копии уже разошлись: dcfl при ретрае отдаёт только `JSON parse failed (length=N)`, без позиции и контекста.
  Одно место для kwargs (thinking/effort), stream-and-collect, `stop_reason`, типизированных ошибок и backoff,
  семафора конкурентности и `UsageTracker` (токены / cache-hit / стоимость → блок `usage` в result JSON и в TFL Lab).
- [ ] 🟠 **M** **Structured outputs** (`output_config.format`) вместо «от первой `{` до последней `}`»: эвристика ломается на
  нотации множеств `{aⁿbⁿ | n≥0}` в прозе; в `agent_system/lib/llm_client.py` литерал `{}` из прозы становится всем выводом.
  Общий конверт `AgentOutput` (status, verdict, confidence, errors); `_extract_json`, Haiku-repair и алиасы — только fallback.
- [ ] ⚪ **S** **Prompt caching** (`cache_control`) для больших повторных промптов (`cfl_reasoning`, `cfl_pumping`, `ll_reasoning_agent`, 15–20 KB);
  `student_notes` — отдельным блоком. Проверять по `usage.cache_read_input_tokens`.
- [ ] ⚪ **S** Учёт токенов и стоимости за прогон (сейчас `usage` печатается только в verbose-режиме).
- [ ] ⚪ **S** `agent_system/lib/llm_client.py`: `student_notes` дублируются в system prompt и в user JSON.
- [ ] ⚪ **S** `agent_system`: при refusal JSON-ретрай делает лишний второй вызов (`run_agent` не отличает refusal от невалидного JSON).
- [ ] ⚪ **—** После миграции перемерить стоимость и время на 1–2 задачах (на Haiku через `TFL_MODEL_OVERRIDE`, затем выборочно на Opus)
  и подстроить `EFFORT` по агентам.

## 4. TFL Lab и корень репозитория

- [ ] 🟠 **M** `pumping_len.py:335-404, 91`: неверное определение p_min (для `a*|bbbb` выдаёт 2 вместо 5), `ε` разбирается как литерал,
  конечные языки бросают исключение, CLI нет, тестов нет. README рекламирует файл как готовый инструмент.
- [ ] ⚪ **S** `ui_server/static/index.html:1311-1320, 924, 1216-1219`: вывод `marked` без санитизации в `innerHTML`,
  iframe без `sandbox`, CDN без SRI, нет CSP.
- [ ] ⚪ **S** `ui_server/server.py`: нет лимита параллельных прогонов, таймаута `proc.wait()`, отмены и вытеснения старых запусков.
- [ ] ⚪ **S** Мусор в корне: `tmp9llpogor/`, `.codex_tmp/` (≈800 файлов), `pumping lemma/` (с пробелом), `gen_pass.py`, пустой `examples/`,
  два `.env` → удалить, добавить `tmp*/` в `.gitignore`.
- [ ] ⚪ **S** `agent_system/orchestrator.py:284-432, 514-630`: мёртвые копии `_run_formalizer`, `_verify_closure_claim`.
- [ ] ⚪ **S** `agent_system/graph.py:46`: `FORMALIZATION_ENABLED` захардкожен.

## 5. Тесты, CI, упаковка

- [ ] 🟠 **S** **Голый `pytest` из корня делает реальные платные вызовы API.** `pumping_lemma/orchestrator/graph.py:26`:
  `llm_client=None` → `LLMClient()` + `load_dotenv()`; `test_unknown_language` падает (`nl_parser.py:137`).
  → `[tool.pytest.ini_options] testpaths = [...]` в `pyproject.toml` + корневой `conftest.py` с autouse-фикстурой,
  которая убирает `ANTHROPIC_API_KEY` и подменяет `anthropic.Anthropic`.
- [ ] 🟠 **M** Ветки `run_agent` (retry, Haiku-repair, `max_tokens`, исключения API) не покрыты ни в одной системе →
  общий `FakeAnthropic` (thinking-блок перед текстом, `max_tokens`, `refusal`, обрыв стрима) и `ScriptedRunner`
  (список ответов на агента, запись входов) для тестов retry/invert/Level-1.
- [ ] 🟠 **M** `dcfl_system/examples/mock/*_reasoning.json`: в моках нет `action`, поэтому все E2E идут через `_fallback_reasoning`;
  LLM-путь reasoning, collect/fan-in и `_verify_*` не тестируются.
- [ ] 🟠 **M** `agent_system/tests`: нет тестов retry/invert/Level-1, `claim_verifier`, `grammar_preprocessor`, `_extract_json`;
  `MockRunner` игнорирует вход.
- [ ] 🟠 **S** `ll_system/tests/test_first_follow.py`: нет случаев SLL≠LL, большого k на левой рекурсии, кривых грамматик.
- [ ] ⚪ **S** Пустые тесты: `cfl_system/tests/test_orchestrator.py:133-145, 635-647` (`except (ValueError, Exception): pass`, оставляют ключ
  из `.env` в `os.environ`) → `pytest.raises` + monkeypatch; `ll_system/tests/test_orchestrator.py:434-456` — убрать `try/except`.
- [ ] ⚪ **S** Contract-тесты промптов: каждый ```json-блок парсится и содержит обязательные ключи; все ключи, которые формирует
  builder входа, упомянуты в промпте (по образцу `cfl_system/tests/test_pda_contract.py`).
- [ ] ⚪ **S** `pyproject.toml`: `prompts/*.md`, `examples`, `static`, шаблоны Lean не попадают в wheel, а `*/tests` попадают;
  сломан скрипт `inverse-homomorphism`; extra `ui=streamlit` вводит в заблуждение, extra `langgraph` дублирует основные зависимости;
  нет entry points для четырёх систем; описание устарело.
- [ ] ⚪ **S** `.github/workflows/tests.yml`: добавить `timeout-minutes`, `concurrency`;
  lint (`ruff`), coverage с порогом, сборку wheel со smoke-установкой; неблокирующую Lean-задачу; тесты `pumping_len.py`.
- [ ] ⚪ **S** `agent_system/tests/test_phase3.py:49`: шаблоны Lean никогда не компилируются в CI.

## 6. Документация и промпты

- [ ] 🟠 **S** `cfl_system/prompts/cfl_formalizer.md:40, 194`: доказательство генерируется дважды, поле `markdown` почти не используется
  (`orchestrator.py:1514, 1563-1566`) → убрать из контракта (лишние Opus-токены).
- [ ] ⚪ **S** 14 промптов: блоки «Reasoning (Chain-of-Thought)» перед JSON и черновые реплики «Wait — ...»
  (`cfl_pumping.md:195-198`, `cfl_decomposition.md:122,178`). С adaptive thinking рассуждения в выводе не нужны — почистить.
- [ ] ⚪ **S** `agent_system/prompts/reasoning_agent.md:91`, `pumping_agent.md:55-100`: инструкция требует русский текст, а примеры на английском.
- [ ] ⚪ **S** Ключи входа (`grammar_facts`, `retry_context`, `ir`/`classifier_hint` — `agent_system/graph.py:163`,
  `dcfl_system/orchestrator.py:388-400`) не совпадают с разделом Input Format в промптах.
- [ ] ⚪ **M** Контракты вывода — pseudo-JSON, различаются по системам (`module`/`agent`/`agent_name`, разные наборы status);
  парсеры латают расхождения алиасами (решается structured outputs, см. раздел 3).
- [ ] ⚪ **S** Строки `**Model:** ...` в промптах дублируют `config.py` и требуют правки при каждой миграции → удалить;
  `advisory_only` никто не читает; количество агентов захардкожено (`cfl_classifier.md:84`).
- [ ] ⚪ **S** `agent_system/orchestrator.py:681` / `ll_system/config.py:103`: `input_parser` настроен, но нигде не вызывается
  (шаг parse из спецификации отсутствует) → реализовать `--text` или пометить как deferred.
- [ ] ⚪ **S** `agent_system/lib/claim_verifier.py:129,157`: алфавит `[ab]` захардкожен, нет левой границы слова, `in\s+L` матчит «in length».
- [ ] ⚪ **S** `ll_system/lib/ll_ir_schema.py:88`: `$`/`ε` не зарезервированы и конфликтуют с маркером конца ввода.

## 7. Стратегия

- [ ] **L** **Eval-набор** из 30–50 задач с эталонными вердиктами, включая ловушки: {a^6n b^6n c^6n}, LL(2)-не-SLL(2),
  левая рекурсия, неоднозначная грамматика DCFL-языка. Отдельная команда `tfl-eval --systems cfl,ll --live`
  (по умолчанию на Haiku), метрики: точность вердикта, калибровка confidence, стоимость, токены, время → `.tfl_lab_runs/evals/`.
  Без этого нельзя измерить эффект миграции на Opus 5.5 и правок промптов.
- [ ] **L** Общая библиотека оракулов (CYK/Earley, PDA-симулятор, валидатор грамматик, `is_grammar_equivalent_sample`),
  чтобы dcfl и ll проверяли членство слов так же, как cfl.
- [ ] **M** Единая таксономия доверия во всех системах (`verified` / `bounded_pass` / `well_formed` / `llm_checked` / `refuted`)
  и `proof_verified` только из детерминированных сигналов.
- [ ] **M** Единый CLI и формат результата (`--save` уже есть) + console scripts `tfl-lab`, `tfl-reg`, `tfl-cfl`, `tfl-dcfl`, `tfl-ll`.
- [ ] **M** Один источник правды для документации: таблица возможностей пайплайнов генерируется или проверяется тестом.
