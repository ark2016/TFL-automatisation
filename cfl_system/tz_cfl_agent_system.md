# ТЗ: Агентская система для анализа КС-свойства языков

## 0. Контекст

Данная система расширяет существующую агентскую систему для анализа регулярности
(REG-система, `agent_system/`). REG-система уже реализована (4 фазы, 83 теста)
и содержит IR schema, oracle, DFA runner, hypothesis module, orchestrator с LLM.

Новая CFL-система анализирует, является ли заданный язык **контекстно-свободным**.
Она переиспользует инфраструктуру REG-системы (IR schema, oracle framework,
orchestrator pattern, Lean Docker), но добавляет новые specialist agents,
новые типы oracle, и расширенную verification layer.

**Папка:** `cfl_system/` (рядом с `agent_system/`, не внутри).
**Зависимости от REG-системы:** импорт `agent_system.lib.ir_schema`,
`agent_system.lib.oracle`, `agent_system.lib.word_generator` допустим.

---

## 1. Типы задач

### 1.1. Формат 1 — язык задан множеством (основной)

Язык описан через set-builder нотацию:

```
{w₁w₂w₁w₃ | w₂ ∈ {b,c}*, w₁ ∈ {a,b}*, w₃ ∈ {a,c}*, |wᵢ| > 0}
{wwvvᴿ | v, w ∈ {a,b}*}
{w₀w₁w₂w₁w₃ | wᵢ ∈ {a,b,c}*, |w₁| > 1}
```

Задача: определить, является ли L контекстно-свободным, и доказать.

### 1.2. Формат 2 — грамматика + фильтр

Даётся КС-грамматика G и дополнительное условие на слова:

```
Билет 49: L(G) ∩ {w : |w|_a = |w|_b}, где G: S → aSbb | ε | bbSa | aA, A → aA | a
Билет 54: L(G) ∩ {w : |w|_b > |w|_a}, где G: S → bSSb | ba | aSSa
```

Задача: определить, является ли L(G) ∩ F контекстно-свободным.
Если F задаёт регулярное условие, пересечение КС ∩ REG = КС.
Если F нерегулярно — нужен отдельный анализ.

### 1.3. task_type

```
classify_cfl            — определить, КС ли язык
prove_cfl               — доказать, что язык КС (построить грамматику / PDA)
prove_non_cfl           — доказать, что язык не КС
classify_and_prove_cfl  — определить + доказать
grammar_filter_cfl      — Формат 2: грамматика + фильтр
```

---

## 2. IR — расширение JSON Schema

Расширяем существующую IR schema из `agent_system/lib/ir_schema.py`.

### 2.1. Новые task_type

Добавить в enum `task_type`:
```
"classify_cfl", "prove_cfl", "prove_non_cfl",
"classify_and_prove_cfl", "grammar_filter_cfl"
```

### 2.2. GrammarFilterLanguage (новый тип LanguageSpec)

```json
{
  "type": "object",
  "required": ["kind", "grammar", "filter"],
  "properties": {
    "kind": { "const": "grammar_filter" },
    "grammar": { "$ref": "#/$defs/GrammarLanguage" },
    "filter": {
      "oneOf": [
        { "$ref": "#/$defs/Predicate" },
        {
          "type": "object",
          "required": ["kind", "description"],
          "properties": {
            "kind": { "const": "natural_language_filter" },
            "description": { "type": "string" }
          }
        }
      ]
    }
  }
}
```

**Пример (билет 49):**
```json
{
  "task_type": "grammar_filter_cfl",
  "source_text": "Язык слов грамматики S→aSbb|ε|bbSa|aA, A→aA|a, у которых |a|=|b|",
  "language_spec": {
    "kind": "grammar_filter",
    "grammar": {
      "kind": "grammar",
      "terminals": ["a", "b"],
      "nonterminals": ["S", "A"],
      "start": "S",
      "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b", "b"]},
        {"lhs": "S", "rhs": []},
        {"lhs": "S", "rhs": ["b", "b", "S", "a"]},
        {"lhs": "S", "rhs": ["a", "A"]},
        {"lhs": "A", "rhs": ["a", "A"]},
        {"lhs": "A", "rhs": ["a"]}
      ]
    },
    "filter": {
      "op": "eq",
      "left": {"kind": "count_symbol", "symbol": "a", "in_var": "w"},
      "right": {"kind": "count_symbol", "symbol": "b", "in_var": "w"}
    }
  }
}
```

### 2.3. Новые типы предикатов

**RepeatedSubword** — повторяющееся подслово (w₁ ... w₁ ...):
```json
{
  "type": "object",
  "required": ["kind", "parts", "concat_pattern", "alphabets"],
  "properties": {
    "kind": { "const": "repeated_subword" },
    "parts": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Named parts: ['w1', 'w2', 'w3']"
    },
    "concat_pattern": {
      "type": "array",
      "items": { "type": "string" },
      "description": "How word is formed: ['w1', 'w2', 'w1', 'w3']"
    },
    "alphabets": {
      "type": "object",
      "additionalProperties": {
        "type": "array",
        "items": { "type": "string" }
      },
      "description": "Alphabet per part: {'w1': ['a','b'], 'w2': ['b','c']}"
    },
    "constraints": {
      "type": "array",
      "items": { "$ref": "#/$defs/Predicate" }
    }
  }
}
```

**Пример:** {w₁w₂w₁w₃ | w₂ ∈ {b,c}*, w₁ ∈ {a,b}*, w₃ ∈ {a,c}*, |wᵢ| > 0}
```json
{
  "kind": "repeated_subword",
  "parts": ["w1", "w2", "w3"],
  "concat_pattern": ["w1", "w2", "w1", "w3"],
  "alphabets": {
    "w1": ["a", "b"],
    "w2": ["b", "c"],
    "w3": ["a", "c"]
  },
  "constraints": [
    {"op": "gt", "left": {"kind": "length", "of_var": "w1"}, "right": {"kind": "constant", "value": 0}},
    {"op": "gt", "left": {"kind": "length", "of_var": "w2"}, "right": {"kind": "constant", "value": 0}},
    {"op": "gt", "left": {"kind": "length", "of_var": "w3"}, "right": {"kind": "constant", "value": 0}}
  ]
}
```

**SquarePalindrome** — конкатенация квадрата и палиндрома:
```json
{
  "kind": "exists_decomposition",
  "parts": ["w", "v"],
  "concat_pattern": ["w", "w", "v", "rev(v)"],
  "constraints": []
}
```

---

## 3. Архитектура — граф pipeline

```
__start__
    │
    ▼
validate_ir_node ──fail──▶ assemble_early_failure
    │ ok
    ▼
analyze_hypothesis_node
    │
    ▼
run_classifier_node  ← ADVISORY: пунктирная обводка, no dispatch gate.
    │                   Выход — hint для reasoning, не gate для dispatch.
    ▼
language_preprocess_node  ← Format 2: анализ фильтра, bounded lang test.
    │
    ▼
setup_dispatch_node  ◀──────────────────────────────────────┐
    │                                                        │ selected
    ▼                                                        │ agents
┌─────────────────────────────────────────────┐              │ only
│  9 specialist agents (parallel dispatch)    │              │
│                                             │              │
│  CONSTRUCTIVE (teal):     DESTRUCTIVE:      │              │
│  ┌──────────────┐  ┌──────────────────┐     │              │
│  │ cfg_builder  │  │ pumping_cfl      │     │              │
│  │ pda_builder  │  │ ogden            │     │              │
│  │ decomposition│  │ closure_reduction│     │              │
│  │ parikh       │  │ interchange      │     │              │
│  └──────────────┘  │ morphism         │     │              │
│                    └──────────────────┘     │              │
└─────────────────────────────────────────────┘              │
    │                                                        │
    ▼                                                        │
collect_specialists_node                                     │
    │                                                        │
    ▼                                                        │
build_oracle_node  ← CYK oracle из предложенной CFG         │
    │                                                        │
    ▼                                                        │
verify_claims_node  ← Проверка pumping args, Parikh, etc.   │
    │                                                        │
    ▼                                                        │
oracle_test_node  ← CFG vs word generator spot-check        │
    │                                                        │
    ▼                                                        │
run_proof_checker_node                                       │
    │                                                        │
    ▼                                                        │
run_reasoning_node                                           │
    │           │            │                               │
    ▼ done      ▼ invert     ▼ retry                         │
formalize    invert_hyp    retry_planner_node ────────────────┘
    │           │              │ fail
    ▼           ▼              ▼
assemble_result_node      assemble_early_failure
    │                          │
    ▼                          ▼
                   __end__
```

### 3.1. Ключевые принципы

1. **Classifier advisory only.** `run_classifier_node` выдаёт hint (CFL / non-CFL /
   uncertain + confidence), но `setup_dispatch_node` ВСЕГДА запускает все 9 агентов.
   Classifier hint используется только `run_reasoning_node` для взвешивания.

2. **Dispatch all agents always.** Корректность важнее скорости. Если classifier
   сказал «CFL» но pumping_cfl нашёл доказательство ¬CFL — reasoning agent
   принимает pumping proof (после верификации).

3. **Selective retry.** `run_reasoning_node` при retry указывает КОНКРЕТНЫЙ список
   агентов для перезапуска + модифицированные параметры. `retry_planner_node`
   транслирует это в конфиг для `setup_dispatch_node`.

---

## 4. Спецификация модулей

### 4.1. Input Parser (LLM + pure fn)

**Вход:** Текст задачи (русский или формальная нотация).
**Выход:** JSON IR по расширенной schema из §2.
**Модель:** Sonnet 4.6 (temperature=0, structured output=JSON).
**Промпт:** `prompts/cfl_input_parser.md`

Критические паттерны:
- `w₁...w₁...` (повтор подслова) → RepeatedSubword
- `wwvvᴿ` → ExistsDecomposition с concat_pattern ["w","w","v","rev(v)"]
- `S → aSbb | ...` + условие → GrammarFilterLanguage
- `|wᵢ| > 0` → constraints на длину частей
- Формат 2 detection: наличие грамматики + «у которых» / «содержащих»

### 4.2. Hypothesis Module (pure fn)

**Файл:** `lib/cfl_hypothesis.py`
**Вход:** JSON IR (LanguageSpec).
**Выход:**
```json
{
  "features": {
    "has_repeated_subword": true,
    "has_reverse": false,
    "has_counting_constraint": false,
    "is_bounded_language": false,
    "is_grammar_filter": false,
    "filter_is_regular": null,
    "crossed_dependencies": true,
    "nesting_depth": null
  },
  "hypothesis": "non_cfl",
  "confidence": 0.75,
  "reasoning": "Repeated w₁ at non-adjacent positions with different surrounding alphabets suggests crossed dependency"
}
```

**Эвристики:**
- Повтор подслова w₁...w₁ с ≥ 1 другим подсловом между → suspect non-CFL
- `vvᴿ` (палиндром) — CFL if standalone, needs analysis in composition
- Crossed dependencies (w₁...w₂...w₁...w₂) → strong signal non-CFL
- Grammar + regular filter → CFL (intersection theorem)
- Grammar + non-regular filter → needs analysis
- Bounded language (w₁^k₁...wₙ^kₙ) → stratification test (deterministic)
- All parts from same alphabet, no repeat → likely CFL

### 4.3. Classifier Agent (LLM, advisory)

**Файл:** `prompts/cfl_classifier.md`
**Вход:** JSON IR + hypothesis output.
**Выход:**
```json
{
  "verdict": "non_cfl",
  "confidence": 0.7,
  "reasoning": "Repeated subword w1 at positions 1 and 3 with intervening w2 creates a copying dependency that CFGs cannot track",
  "advisory_only": true
}
```
**Модель:** Sonnet 4.6 (temperature=0).
**Роль:** Чисто рекомендательная. Dispatch не зависит от verdict.

### 4.4. Language Preprocess Node (pure fn)

**Файл:** `lib/language_preprocess.py`
**Вход:** JSON IR.
**Выход:** Обогащённый IR + precomputed features.

Логика:
1. **Format 2:** Если `kind == "grammar_filter"`:
   - Анализировать фильтр: регулярен ли?
   - Если фильтр = Comparison(count_a, count_b) → регулярное условие (через PDA × DFA)
   - Если фильтр = Modular → регулярное условие
   - Если фильтр нерегулярен → пометить `filter_analysis_needed: true`
   - Результат: `{"filter_is_regular": true/false, "intersection_strategy": "pda_x_dfa" | "manual"}`

2. **Bounded language test:** Если язык имеет вид w₁^k₁...wₙ^kₙ:
   - Применить критерий стратифицированных периодов (алгоритмически)
   - Если удалось определить → добавить `bounded_lang_result` в IR
   - Это НЕ LLM-агент, а чистая функция

3. **Parikh pre-check:** Быстрая проверка: если коммутативный образ не полулинеен →
   сразу `non_cfl` (без агентов)

### 4.5. Specialist Agents

Все агенты получают на вход JSON:
```json
{
  "ir": { ... },
  "hypothesis": { ... },
  "classifier_hint": { ... },
  "preprocess": { ... },
  "retry_params": null | { "strategy": "...", "hint": "..." }
}
```

Все агенты возвращают:
```json
{
  "agent": "agent_name",
  "status": "success" | "failure" | "inconclusive",
  "verdict": "cfl" | "non_cfl" | null,
  "evidence": { ... },
  "confidence": 0.0..1.0,
  "errors": []
}
```

#### 4.5.1. cfg_builder (constructive, LLM)

**Промпт:** `prompts/cfl_cfg_builder.md`
**Задача:** Построить КС-грамматику G такую, что L(G) = L.
**Стратегии:**
- Прямое построение (для простых языков типа {aⁿbⁿ})
- Модификация известных грамматик
- Конструкция через замыкания (если decomposition нашёл L = L₁ op L₂)
- Для Format 2 с регулярным фильтром: модифицировать грамматику

**Выход (evidence):**
```json
{
  "grammar": {
    "terminals": ["a", "b"],
    "nonterminals": ["S"],
    "start": "S",
    "rules": [
      {"lhs": "S", "rhs": ["a", "S", "b"]},
      {"lhs": "S", "rhs": []}
    ]
  },
  "explanation": "..."
}
```
**Модель:** Opus 4.7 (temperature=0.2).

#### 4.5.2. pda_builder (constructive, LLM)

**Промпт:** `prompts/cfl_pda_builder.md`
**Задача:** Построить МП-автомат, распознающий L.
**Когда эффективен:** Языки со стековой семантикой, вложенные скобки,
зеркальные конструкции.

**Выход (evidence):**
```json
{
  "pda": {
    "states": ["q0", "q1", "q_accept"],
    "input_alphabet": ["a", "b"],
    "stack_alphabet": ["Z", "A"],
    "start_state": "q0",
    "start_stack": "Z",
    "accept_states": ["q_accept"],
    "transitions": [
      {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["A", "Z"]},
      {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
      {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
      {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
      {"from": "q1", "input": null, "stack_top": "Z", "to": "q_accept", "push": []}
    ]
  },
  "explanation": "..."
}
```
**Модель:** Opus 4.7 (temperature=0.2).

#### 4.5.3. decomposition (constructive, LLM)

**Промпт:** `prompts/cfl_decomposition.md`
**Задача:** Разложить L на КС-операции: L = L₁ ∪ L₂, L = L₁ · L₂, L = L₁*.
**Когда эффективен:** Языки, описанные как объединение / конкатенация.
Пример: {wwvvᴿ} = L_square · L_palindrome.

**Выход (evidence):**
```json
{
  "decomposition_type": "concatenation",
  "components": [
    {"name": "L_square", "description": "{ww | w ∈ {a,b}*}", "is_cfl": true, "grammar": {...}},
    {"name": "L_palindrome", "description": "{vv^R | v ∈ {a,b}*}", "is_cfl": true, "grammar": {...}}
  ],
  "operation": "concat",
  "conclusion": "L = L_square · L_palindrome, both CFL, concatenation of CFL is CFL → L is CFL",
  "explanation": "..."
}
```
**Модель:** Opus 4.7 (temperature=0.3).

**Важно:** НЕ все декомпозиции корректны. {ww} сам по себе НЕ КС. Агент
должен обосновать, что каждая компонента КС, и что операция сохраняет КС.
Верификация через oracle_test.

#### 4.5.4. parikh (constructive/destructive, pure fn + LLM)

**Промпт:** `prompts/cfl_parikh.md`
**Задача:** Вычислить коммутативный образ языка, проверить полулинейность.

**Pure fn часть:** `lib/parikh.py`
- Для грамматики: извлечь правила, вычислить Parikh image
- Для предикатного языка: проанализировать constraints на counts
- Проверка полулинейности: множество векторов ∈ ℕᵏ — finite union of linear sets?

**LLM часть:** Если pure fn недостаточно (язык задан сложным предикатом),
LLM анализирует коммутативный образ.

**Выход (evidence):**
```json
{
  "commutative_image": "(ab)*  или  {(n, n+n!) | n ≥ 0}",
  "is_semilinear": true | false,
  "explanation": "...",
  "conclusion": "non_semilinear → not CFL"
}
```

#### 4.5.5. pumping_cfl (destructive, LLM)

**Промпт:** `prompts/cfl_pumping.md`
**Задача:** Доказать, что L не КС, используя лемму Bar-Hillel.

**Стратегия:**
1. Выбрать «хорошее» слово z ∈ L, |z| ≥ p
2. Показать, что для ВСЕХ разбиений z = uvwxy с |vwx| ≤ p, |vx| ≥ 1
   существует i ≥ 0 такое, что uvⁱwxⁱy ∉ L
3. Если прямая накачка не работает — предложить closure_reduction (intersect с REG)

**Выход (evidence):**
```json
{
  "word_chosen": "a^p b^p a^p b^p",
  "word_parametric": "a^{p} b^{p} a^{p} b^{p}",
  "cases": [
    {
      "case": "v and x are in first a-block",
      "pumped_word": "a^{p+|vx|} b^p a^p b^p",
      "why_not_in_L": "First a-block ≠ third a-block in length"
    },
    {
      "case": "v in first a-block, x in first b-block",
      "pumped_word": "...",
      "why_not_in_L": "..."
    }
  ],
  "all_cases_covered": true,
  "conclusion": "L is not context-free"
}
```
**Модель:** Opus 4.7 (temperature=0.1).

#### 4.5.6. ogden (destructive, LLM)

**Промпт:** `prompts/cfl_ogden.md`
**Задача:** Доказать ¬CFL с помощью леммы Огдена (расширение pumping).

**Когда эффективен:** Когда стандартная накачка не работает, потому что
можно «накачать» не тот блок. Разметка позиций позволяет сосредоточиться
на нужной части слова.

Пример: {aⁱbʲcᵏdˡ : i=0 или j=k=l} — стандартная накачка не работает,
но Огден с разметкой позиций b-блока доказывает ¬CFL.

**Выход (evidence):**
```json
{
  "word_chosen": "a b^p c^p d^p",
  "marked_positions": "all positions in b-block",
  "num_marked": "p",
  "cases": [ ... ],
  "conclusion": "L is not context-free (by Ogden's lemma)"
}
```
**Модель:** Opus 4.7 (temperature=0.1).

#### 4.5.7. closure_reduction (destructive, LLM)

**Промпт:** `prompts/cfl_closure_reduction.md`
**Задача:** Доказать ¬CFL через пересечение с регулярным языком.

**Стратегия:**
1. Подобрать регулярный язык R такой, что L ∩ R «проще» для анализа
2. Показать, что L ∩ R не КС (через pumping)
3. Поскольку CFL ∩ REG = CFL, если L ∩ R не КС → L не КС

**Пример:** L = {w₁w₂w₁w₃ | ...}, R = a* · {b,c}* · a* · {a,c}*
Тогда L ∩ R = {aⁿ · w₂ · aⁿ · w₃} — содержит aⁿ(b|c)ᵐaⁿ, далее pumping.

**Выход (evidence):**
```json
{
  "regular_language": "a*(b|c)*a*(a|c)*",
  "regular_language_regex": "a*(b|c)*a*(a|c)*",
  "intersection_description": "{a^n w₂ a^n w₃ | w₂ ∈ {b,c}+, w₃ ∈ {a,c}+}",
  "intersection_not_cfl_proof": {
    "method": "pumping",
    "word_chosen": "a^p b a^p c",
    "cases": [ ... ]
  },
  "conclusion": "L ∩ R is not CFL, R is regular, therefore L is not CFL"
}
```
**Модель:** Opus 4.7 (temperature=0.2).

#### 4.5.8. interchange (destructive, LLM)

**Промпт:** `prompts/cfl_interchange.md`
**Задача:** Доказать ¬CFL с помощью Interchange lemma или леммы Соколовского.

**Interchange lemma:** Для КС-языка L существует константа c такая, что
для любых n, из любого множества из n² слов длины n в L можно выбрать
n слов, для которых interchange (обмен подсловами) сохраняет принадлежность L.

**Когда эффективен:** Когда pumping и Ogden не работают.
Пример: {xyyz | |y| > 0} над алфавитом из 3+ букв.

**Выход (evidence):**
```json
{
  "method": "interchange_lemma" | "sokolowski",
  "chosen_words": "...",
  "interchange_result": "...",
  "contradiction": "...",
  "conclusion": "L is not context-free"
}
```
**Модель:** Opus 4.7 (temperature=0.2).

#### 4.5.9. morphism (destructive, LLM)

**Промпт:** `prompts/cfl_morphism.md`
**Задача:** Доказать ¬CFL через гомоморфизм.

**Стратегия:**
- Найти гомоморфизм h: Σ* → Γ* такой, что h(L) не КС
- Поскольку CFL замкнуты относительно гомоморфизмов, если h(L) не КС → L не КС
- Или найти обратный гомоморфизм h⁻¹ такой, что h⁻¹(L) не КС

**Выход (evidence):**
```json
{
  "morphism_type": "direct" | "inverse",
  "morphism": {"a": "a", "b": "b", "c": ""},
  "image_language": "{a^n b^n | n ≥ 0} — this is CFL, try other approach",
  "image_not_cfl_proof": { ... } | null,
  "conclusion": "..."
}
```
**Модель:** Opus 4.7 (temperature=0.2).

---

## 5. Verification Layer

### 5.1. build_oracle_node (pure fn)

**Файл:** `lib/cfl_oracle.py`

Если какой-либо constructive agent предложил грамматику G:
1. Привести G к нормальной форме Хомского (CNF) — `lib/cnf.py`
2. Реализовать CYK oracle: `cyk_check(grammar, word) → bool`
3. Oracle = CYK парсер для G

Если предложен PDA:
1. Симулятор PDA: `pda_run(pda, word) → bool`
2. Oracle = PDA симулятор

**Важно:** CYK работает за O(n³ · |G|). Для длинных слов (n > 100)
нужен timeout.

### 5.2. verify_claims_node (pure fn + LLM)

**Файл:** `lib/claim_verifier.py`

Для каждого agent output:

**pumping_cfl / ogden:**
- Проверить, что выбранное слово z ∈ L (через oracle или структуру предиката)
- Проверить, что |z| ≥ p (или что длина параметрическая)
- Проверить, что ВСЕ случаи разбиения uvwxy покрыты
- Проверить, что |vwx| ≤ p в каждом случае
- Проверить, что |vx| ≥ 1 в каждом случае
- Для каждого случая: проверить, что uvⁱwxⁱy ∉ L для указанного i
  (через oracle, подстановку конкретных значений)

**closure_reduction:**
- Проверить, что R регулярен (построить DFA для regex R)
- Проверить, что L ∩ R описан корректно
- Делегировать проверку pumping proof для L ∩ R

**decomposition:**
- Проверить, что L = L₁ op L₂ корректно (выборочно: слова из L должны
  раскладываться в компоненты)
- Проверить, что каждая компонента КС
- Проверить, что операция сохраняет КС-свойство

**parikh:**
- Проверить вычисление коммутативного образа на конкретных словах

**cfg_builder / pda_builder:**
- Делегировать в oracle_test_node

### 5.3. oracle_test_node (pure fn)

**Файл:** `lib/cfl_oracle_test.py`

Если есть предложенная грамматика G и oracle для L (из предиката IR):

1. **Positive test:** Сгенерировать слова, удовлетворяющие предикату L.
   Проверить через CYK, что G их порождает.
   Генерация: `lib/cfl_word_generator.py`

2. **Negative test:** Сгенерировать слова, НЕ удовлетворяющие предикату.
   Проверить через CYK, что G их НЕ порождает.

3. **Boundary test:** Слова минимальной длины, edge cases.

Если найден контрпример (слово в L но не в L(G), или наоборот):
- Записать в `oracle_test_result.counterexample`
- `status: "grammar_incorrect"`

**Word generator для CFL:**
- Exhaustive: все слова длины ≤ k (k = 6..10)
- Structured: генерация по структуре предиката
  (например, для RepeatedSubword — перебор w₁, w₂, w₃)
- Random: случайные слова длины до 20

### 5.4. run_proof_checker_node (LLM)

**Промпт:** `prompts/cfl_proof_checker.md`
**Вход:** Все verified claims + oracle test results.
**Задача:** Финальная проверка целостности доказательства.
Не генерирует новые доказательства — только проверяет существующие.

---

## 6. Reasoning и Retry

### 6.1. run_reasoning_node (LLM)

**Промпт:** `prompts/cfl_reasoning.md`
**Вход:** Все agent outputs + verification results + classifier hint.
**Выход:**
```json
{
  "decision": "done" | "invert" | "retry",
  "verdict": "cfl" | "non_cfl" | null,
  "confidence": 0.95,
  "primary_evidence": "pumping_cfl",
  "supporting_evidence": ["closure_reduction", "parikh"],
  "contradictions": [],
  "retry_plan": null | { ... }
}
```

**Логика:**
- Если есть verified constructive proof (grammar + oracle test pass) → CFL, done
- Если есть verified destructive proof (pumping/Ogden + claim verify pass) → non-CFL, done
- Если constructive и destructive оба success → CONFLICT. Проверить oracle_test.
  Если oracle нашёл контрпример в grammar → grammar неверна → доверять destructive.
  Иначе → retry с модифицированными параметрами.
- Если все inconclusive → retry

### 6.2. retry_planner_node (pure fn + LLM)

**Файл:** `lib/retry_planner.py`
**Промпт:** `prompts/cfl_retry_planner.md`

**Вход от reasoning:**
```json
{
  "retry_plan": {
    "agents_to_retry": ["pumping_cfl", "closure_reduction"],
    "reason": "Pumping failed — word choice was wrong. Try intersecting with a*b*c* first.",
    "hints": {
      "pumping_cfl": {"strategy": "try_longer_word", "avoid": "a^p b^p"},
      "closure_reduction": {"regular_language_hint": "a*b*c*"}
    },
    "max_retries_remaining": 2
  }
}
```

**Selective dispatch:** `setup_dispatch_node` получает список
`agents_to_retry` и запускает ТОЛЬКО их, с `retry_params` из hints.
Агенты, не указанные в списке, НЕ перезапускаются — их предыдущие
результаты сохраняются в `collect_specialists_node`.

**Max retries:** 3 итерации. После 3 failed retries → `assemble_early_failure`
с verdict "inconclusive".

---

## 7. Выходной формат

### 7.1. Результат (assemble_result_node)

```json
{
  "task": "classify_and_prove_cfl",
  "source_text": "...",
  "verdict": "non_cfl",
  "confidence": 0.95,
  "proof": {
    "method": "closure_reduction + pumping",
    "summary": "Intersected L with R = a*(b|c)*a*(a|c)* (regular). Showed L ∩ R contains {a^n b a^n c | n ≥ 1} which is not CFL by Bar-Hillel pumping.",
    "details": { ... }
  },
  "grammar": null,
  "pda": null,
  "oracle_test": {
    "status": "not_applicable",
    "positive_checked": 0,
    "negative_checked": 0,
    "counterexamples": []
  },
  "agents_used": ["pumping_cfl", "closure_reduction", "parikh"],
  "retries": 0
}
```

### 7.2. Rendered output

Рендеринг в Obsidian-compatible Markdown:
- Callouts: `> [!theorem]`, `> [!proof]`, `> [!note]`
- LaTeX: `$...$` и `$$...$$`
- Таблицы для case analysis в pumping proofs
- Грамматика в формате `S → aSb | ε`

**Файл:** `lib/cfl_renderer.py`
**Важно:** Глобальный guard `if proof is None` перед рендерингом proof —
повторяющийся баг из REG-системы.

---

## 8. Файловая структура

```
cfl_system/
├── CLAUDE.md                           # Контекст для Claude Code
├── tz_cfl_agent_system.md              # Это ТЗ
├── lib/
│   ├── __init__.py
│   ├── cfl_ir_schema.py                # Расширение IR schema (§2)
│   ├── cfl_hypothesis.py               # Hypothesis module (§4.2)
│   ├── language_preprocess.py           # Format 2 filter + bounded test (§4.4)
│   ├── cnf.py                          # Chomsky Normal Form conversion
│   ├── cyk.py                          # CYK parser
│   ├── pda_simulator.py                # PDA simulator
│   ├── cfl_oracle.py                   # CYK/PDA oracle (§5.1)
│   ├── cfl_word_generator.py           # Word generator for CFL (§5.3)
│   ├── cfl_oracle_test.py              # Oracle test: CFG vs predicate (§5.3)
│   ├── claim_verifier.py               # Pumping/closure claim verification (§5.2)
│   ├── parikh.py                       # Parikh image computation (§4.5.4)
│   ├── retry_planner.py                # Selective retry logic (§6.2)
│   ├── cfl_renderer.py                 # Obsidian markdown output (§7.2)
│   └── stratification.py              # Bounded language stratification test
├── prompts/
│   ├── cfl_input_parser.md
│   ├── cfl_classifier.md
│   ├── cfl_cfg_builder.md
│   ├── cfl_pda_builder.md
│   ├── cfl_decomposition.md
│   ├── cfl_parikh.md
│   ├── cfl_pumping.md
│   ├── cfl_ogden.md
│   ├── cfl_closure_reduction.md
│   ├── cfl_interchange.md
│   ├── cfl_morphism.md
│   ├── cfl_reasoning.md
│   ├── cfl_retry_planner.md
│   ├── cfl_proof_checker.md
│   └── cfl_formalizer.md
├── orchestrator.py                     # Main pipeline (§3 graph)
├── examples/
│   ├── task_w1w2w1w3.json              # {w₁w₂w₁w₃ | ...}
│   ├── task_wwvvR.json                 # {wwvvᴿ | ...}
│   ├── task_w0w1w2w1w3.json            # {w₀w₁w₂w₁w₃ | ...}
│   ├── task_grammar_filter_49.json     # Билет 49
│   └── mock/                           # Mock agent outputs for testing
│       ├── task_w1w2w1w3_pumping_cfl.json
│       ├── task_w1w2w1w3_closure_reduction.json
│       └── ...
├── tests/
│   ├── test_ir_schema.py
│   ├── test_hypothesis.py
│   ├── test_cnf.py
│   ├── test_cyk.py
│   ├── test_pda_simulator.py
│   ├── test_oracle.py
│   ├── test_oracle_test.py
│   ├── test_claim_verifier.py
│   ├── test_parikh.py
│   ├── test_word_generator.py
│   ├── test_preprocess.py
│   ├── test_renderer.py
│   ├── test_stratification.py
│   └── test_e2e.py
└── docker/                             # Lean 4 (Phase 3)
    └── ...
```

---

## 9. Фазы реализации

### Phase 1 — Core Infrastructure (pure fn, без LLM)

**Приоритет:** Максимальный. Всё тестируемо без API.

1. `cfl_ir_schema.py` — расширение schema: GrammarFilterLanguage,
   RepeatedSubword, новые task_types. Валидация.
2. `cnf.py` — конверсия CFG → Chomsky Normal Form.
   Алгоритм: удаление ε-правил → удаление цепных → удаление длинных → замена терминалов.
3. `cyk.py` — CYK parser. Вход: CNF grammar + word. Выход: bool.
   Сложность: O(n³ · |G|). Timeout для n > 100.
4. `pda_simulator.py` — BFS/DFS симуляция PDA. Timeout для зацикливания.
5. `cfl_oracle.py` — обёртка: grammar → CYK oracle, PDA → PDA oracle.
6. `cfl_word_generator.py` — генерация слов по структуре предиката.
   Exhaustive (≤ длина k), structured (по RepeatedSubword), random.
7. `cfl_oracle_test.py` — сравнение grammar oracle vs predicate oracle.
8. `cfl_hypothesis.py` — эвристики из §4.2.
9. `language_preprocess.py` — Format 2 filter analysis, bounded test.
10. `parikh.py` — вычисление коммутативного образа, проверка полулинейности.
11. `stratification.py` — критерий стратифицированных периодов.
12. `claim_verifier.py` — проверка pumping/closure claims.
13. `cfl_renderer.py` — Obsidian markdown output. Guard `if proof is None`.
14. `examples/` — 4 примера задач в JSON IR формате.
15. Тесты: ≥ 50 тестов покрывающих все модули.

**Команда для CC:**
```
Прочитай cfl_system/tz_cfl_agent_system.md §9 Phase 1.
Реализуй все 15 пунктов последовательно. После каждого модуля
запускай pytest. Импорт из agent_system/ допустим.
```

### Phase 2 — Agents + Orchestrator (LLM prompts + mock mode)

1. Все промпты в `prompts/` (15 файлов). Каждый промпт:
   - Role description (1 sentence)
   - Input format (JSON schema reference)
   - Output format (JSON schema with required fields)
   - Examples (1-2 input/output pairs из examples/)
   - Constraints (что НЕ делать)
   - Retry params handling

2. `orchestrator.py` — полный pipeline по графу из §3:
   - Mock mode: загрузка agent outputs из `examples/mock/`
   - Live mode: вызов через Anthropic SDK
   - Selective retry: `setup_dispatch_node` принимает `agents_to_retry` list
   - State management: сохранение previous agent results при retry

3. Mock agent outputs в `examples/mock/` для всех 4 примеров задач.

4. E2E тесты: прогон всех 4 задач в mock mode.

**Команда для CC:**
```
Прочитай cfl_system/tz_cfl_agent_system.md §9 Phase 2.
Напиши промпты для всех агентов, orchestrator с mock mode,
mock outputs для примеров, e2e тесты.
```

### Phase 3 — Lean 4 Formalization

Переиспользовать Docker из REG-системы (`agent_system/docker/`).
Добавить proof templates для:
- Pumping Lemma CFL
- Closure properties (CFL ∩ REG = CFL)
- Parikh's theorem application

**Отложить до завершения Phase 2.**

### Phase 4 — Live LLM Integration

Подключить Anthropic SDK (`--live` mode).
Протестировать на реальных задачах из билетов.

---

## 10. Зависимости

```
Python 3.11+
jsonschema  — валидация IR
anthropic   — SDK (Phase 4)
pytest      — тесты
```

Никаких внешних зависимостей для Phase 1 кроме jsonschema.

---

## 11. Критические замечания

1. **`proof is None` guard** — в `cfl_renderer.py` ОБЯЗАТЕЛЬНО проверять
   `if proof is None` перед любым обращением к полям proof.
   Это повторяющийся баг из REG-системы.

2. **CYK timeout** — для слов длиной > 100 символов CYK может работать
   секунды. Установить timeout 10s. Для oracle_test использовать
   слова длиной ≤ 30.

3. **PDA simulation termination** — BFS с ограничением глубины стека
   (max_stack_depth = 1000) и max_steps = 10000.

4. **Decomposition verification** — агент decomposition может предложить
   некорректное разложение (например, {ww} КС — это НЕПРАВИЛЬНО).
   `verify_claims_node` ОБЯЗАН проверять каждую компоненту через oracle_test.

5. **Classifier advisory** — НИКОГДА не фильтровать агентов по verdict
   classifier. Dispatch all always.

6. **Parikh limitation** — полулинейность коммутативного образа — НЕОБХОДИМОЕ,
   но НЕ достаточное условие для КС. {aⁿbⁿcⁿ} не КС, но его коммутативный
   образ (abc)* регулярен. Parikh может только отвергнуть (если не полулинеен),
   но не подтвердить.

7. **LaTeX conventions** — из REG-системы: использовать `b·a^i` (не `ba^i`),
   избегать `\,` (thin space).
