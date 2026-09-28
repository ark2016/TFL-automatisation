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
  `grammar_analyzer.md`/`proof_checker.md`/`reasoning_agent.md`/`nerode_agent.md` исправлен (§0).

Легенда: 🔴 high · 🟠 medium · ⚪ low; объём: S ≤ 30 мин · M ≤ день · L > дня.

---

## 1. Честность вердиктов (главная тема)

- [ ] 🟠 **S** **LL: `not_ll` с confidence 1.0 после проверки только k ≤ 10** (`orchestrator.py:1158-1161, 1361-1366`).
  S→a¹¹b | a¹¹c — это LL(12). → сохранять `max_k_checked`, писать «k ≤ 10»; conclusive — только с сертификатом
  (левая рекурсия / неоднозначность, `docs/THEORY.md` §3.1) — это тот самый открытый подпункт после перехода
  на полный тест Ахо–Ульмана: полный тест сам по себе не убирает необходимость сертификата при переборе k ≤ K.
- [ ] 🔴 **S→M** **«verified» ставится за наличие полей во всех трёх верификаторах.**
  - `dcfl_system/lib/oracle_verifier.py:115-143` — тавтологичная «семантическая» проверка; `:305-311` `concrete_words_present = True`;
    `:430-441` — проверка по ключевым словам; `renderer.py:727-750` рисует зелёный баннер.
  - `cfl_system/lib/claim_verifier.py:47-109, 175-239` — pumping/Ogden/decomposition только по ключам;
    `tests/test_claim_verifier.py:49` ассертит `verified` для «доказательства» про CFL aⁿbⁿ.
  - `ll_system/lib/claim_verifier.py:59-139` — constructive `verified` без сверки языка (`S→a` для палиндромов → verified);
    `:142-243, 455-517, 528-598` — destructive-проверки только на поля; `:41` — символы не валидируются
    (`S→ba|bc` при `terminals=['a']` → LL(1), verified).
  → **шаг 1 (S):** статусы `well_formed` / `verified` / `bounded_pass` / `not_verified`, структурный проход ≠ verified.
  → **шаг 2 (M):** реальные проверки: в ll — `is_grammar_equivalent_sample` (`grammar_transforms.py:649`) и `validate_grammar_symbols`;
    в cfl — инстанцировать p=3..6 и перебирать uvwxy оракулом; в dcfl — разбирать w/w′ через `_parse_word_pattern`.
- [ ] 🟠 **S–M** **Детерминированная сверка итогового вердикта с оракулом.**
  - `agent_system/graph.py:1181-1187`: `test_result` проверяется раньше вердикта reasoning — верный `non_regular` получает `failure 0.0`,
    ограниченный pass даёт `success 1.0`.
  - `cfl_system/orchestrator.py:1072-1117`: ничто не мешает `done/cfl` при `grammar_incorrect`; `proof_verified` (`:1606`) — самооценка LLM.
  - `dcfl_system/orchestrator.py:669-688`: fallback реагирует только на `refuted`, которого верификатор не выдаёт;
    `retry_logic.py:19-84` не читает `oracle_verification`.
  → главным считать вердикт reasoning, оракул — свидетельством; противоречие → retry или снижение confidence с пометкой `contradiction`.
  - **Воспроизведено вживую (2026-09-26, Haiku через `TFL_MODEL_OVERRIDE`):** `cfl_system/examples/task_grammar_filter_49.json`.
    Грамматика и PDA от агентов неверны (43 настоящих контрпримера), retry → invert → `done non_cfl` с confidence **0.92**.
    Ревизия раунда 2 (`docs/THEORY.md` §2.3) показала: вердикт `non_cfl` был **верным** — язык действительно не КС
    (доказано леммой Огдена; старый эталон `cfl` в `cfl_system/tests/test_e2e.py:69` был ошибочен и уже заменён на
    `non_cfl` в этом же раунде правок).
    Ошибочным был **путь**, а не итог: «не удалось построить верный артефакт» ≠ «язык не КС» — после inversion всё ещё
    нужен проверенный деструктивный аргумент, иначе `inconclusive` / низкий confidence, а не совпадение с правильным
    вердиктом по случайной причине.
- [ ] 🟠 **S** **Live-прогон на Haiku для `ll_prefix_classes_agent.md` / `ll_substitution_agent.md`.**
  Промпты переписаны под теорему 4.7.4 (`docs/THEORY.md` §3.3, аргумент «развилки» (C)); нужен прогон
  `TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live` на {aⁿbⁿ}∪{aⁿcⁿ} и т.п., чтобы проверить, что агенты
  реально следуют новым полям контракта (`common_prefix`, `branch_words`, `deciding_nonterminal_argument`,
  `pigeonhole_argument`, `dead_class_finite`), а не повторяют старые ложные схемы по инерции few-shot.
- [ ] 🟠 **S** **Live-прогон на Haiku для `dcfl_system/prompts/shallit.md`.**
  Формулировка переписана на теорему 4.7.4 + лемму о продолжении (`docs/THEORY.md` §1.2–1.3); нужен тот же
  live-прогон на Haiku, чтобы проверить, что агент `shallit` действительно выбирает `technique`
  (`nerode_classes` | `prefix_continuation`) и заполняет `dead_class_finite`, а не игнорирует новый контракт.
- [ ] 🟠 **S** **Верификаторы по-прежнему структурные** (из пункта «`verified` ставится за наличие полей…» ниже):
  шаг 1 (статусы `well_formed`/`verified`/`bounded_pass`/`not_verified`) не подменяет шаг 2 (реальная сверка
  языка/грамматики) — он всё ещё не сделан ни в одной из трёх систем.
- [ ] ⚪ **S** `ll_system/orchestrator.py:631`: shortcut по регулярности ставит 0.95 даже эвристике с 0.75 и читает несуществующие
  ключи `confidence/reason` (правильно `regularity_confidence/regularity_reason`).
- [ ] 🟠 **S** `ll_system/orchestrator.py:674-694`: в Format 2 заданная грамматика не уходит в оракул, проверяется только первая
  грамматика агента; `preprocess.py:34` (проверка конечности) для Format 2 не работает.
- [ ] ⚪ **S** `agent_system/graph.py:1094`: `lean_verified=True` ставится файлам с `sorry` → `status=="valid" and sorry_count==0`.
- [ ] 🟠 **S** После смены эталонов ревизии раунда 2 (`dcfl_exam_01/02/03` → `non_dcfl`, `task_grammar_filter_49` → `non_cfl`,
  `wbcwR` → язык `{w b c wᴿ}` с вердиктом `ll`, см. `docs/THEORY.md` Часть II): прогнать
  `TFL_MODEL_OVERRIDE=claude-haiku-4-5 ... --live` на всех
  этих exam-задачах, чтобы проверить, что агенты и оракулы реально сходятся к новым вердиктам, а не повторяют старые
  ложные схемы по инерции few-shot.
- [ ] 🟠 **S** `dcfl_system/renderer.py` и рендереры `cfl_system`/`ll_system`: проверить отображение новых полей
  контрактов (например `dead_class_finite`, `technique`, `common_prefix`, `branch_words` и т.п. из промптов раунда 2) —
  сейчас неясно, показывает ли HTML/MD-отчёт эти поля вообще или тихо их игнорирует.
- [ ] 🟠 **S** `ll_system`: мост word-оракула для `set_builder` (открытый пункт из отчёта раунда 1) — до сих пор не
  реализован; без него часть LL-конструкций формата `set_builder` не проверяется по словам, а верификация остаётся
  структурной.

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
