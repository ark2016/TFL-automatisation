# ТЗ: Агентская система для проверки LL-свойства языков

**Версия:** 0.1 (draft)
**Дата:** 2026-04-09
**Scope:** Проверка LL(k)-свойства КС-языков и КС-грамматик.
**Runtime:** Claude Code (Max) + Lean 4 (Docker) + Python pure functions
**Источники:** Aho, Sethi, Ullman (Dragon Book); Hopcroft, Ullman, Motwani;
Edelmann, Hamza, Kunčak (LL(1) с деривативами); Olkhovsky, Okhotin (LL(k)
линейные конъюнктивные грамматики); Opedal et al. (left-corner
transformations); база знаний ИУ-9.

---

## 0. Контекст

Данная система — подмодуль проекта TFL-automatisation. Она анализирует,
является ли заданный КС-язык (или КС-грамматика) **LL(k)** для некоторого k,
или не является LL ни для какого k.

Система переиспользует инфраструктуру REG-системы (`agent_system/`) и
CFL-системы (`cfl_system/`): IR schema, oracle framework, word generator,
Lean Docker, orchestrator pattern.

**Папка:** `ll_system/` (рядом с `agent_system/` и `cfl_system/`).
**Зависимости:**
- `agent_system.lib.ir_schema` — базовые типы IR
- `agent_system.lib.oracle` — oracle framework
- `agent_system.lib.word_generator` — генерация слов
- `cfl_system.lib.cyk_oracle` — CYK oracle для проверки принадлежности
- `cfl_system.lib.cfg_utils` — утилиты для работы с грамматиками

---

## 1. Типы задач

### 1.1. Формат 1 — язык задан множеством

Язык описан set-builder нотацией. Вопрос: является ли **язык** LL(k)
для некоторого k.

Примеры из билетов:

```
{w₁ a w₂ | |w₁|_a = |w₂|_b}                         — билет 29
{w₁ b w₂ aᵐ | |w₁|_a < |w₂|_a}                      — билет 36
{w₁ bⁿ w₂ | |w₁| = |w₂|}                             — билет 38
{aⁿ bᵏ aᵐ | n > k}                                   — билет 70к
{aⁿ b*(cⁿ | bⁿ) c*}                                  — билет 8
{aⁿ(bⁿ | (ba)ⁿ | (a*bb)ⁿ)}                          — билет 48
{w a w^R v | w ∈ (abaa)* | a*b, v ∈ a*}              — билет 52
{w b* c w^R | w ∈ {a,b}*}                             — билет 6
{(ww^R)* | w ∈ (aab)*}                                — билет 66к
```

### 1.2. Формат 2 — грамматика → LL-свойство языка

Дана КС-грамматика. Вопрос: является ли **язык** данной грамматики LL(k).
Конкретная грамматика может не быть LL(k) (например, из-за левой рекурсии),
но язык — быть.

Примеры:

```
S → SabS | Sc | ε                                     — билет 1
S → SaSb | ε | A,  A → bc | cSc                       — билет 21
S → aSB | ε,  B → Bb | Sb | a                         — билет 13
```

### 1.3. Формат 3 — является ли данная грамматика LL(k)

Вопрос про **конкретную грамматику** (не язык). Чисто алгоритмическая задача:
построить FIRST_k / FOLLOW_k, проверить конфликты. LLM не нужен.

Примеры:

```
S → aAb | bBa
A → aA | ε
B → bB | ε
→ Проверить: LL(1)? LL(2)?
```

**Маршрутизация:** Format 3 идёт по fast path напрямую в `first_follow_oracle`,
минуя classifier и specialist agents.

---

## 2. IR Schema

### 2.1. Расширение базовой IR schema

Поле `task_type` расширяется значениями:

```
"ll_check_language"    — Формат 1: язык → LL?
"ll_check_grammar_lang" — Формат 2: грамматика → LL-свойство языка
"ll_check_grammar"     — Формат 3: конкретная грамматика → LL(k)?
```

### 2.2. Структура IR для Format 1

```json
{
  "task_type": "ll_check_language",
  "alphabet": ["a", "b", "c"],
  "language": {
    "type": "set_builder",
    "variables": [
      {"name": "w₁", "domain": {"type": "star", "base": ["a", "b"]}},
      {"name": "w₂", "domain": {"type": "star", "base": ["a", "b"]}}
    ],
    "template": ["w₁", "a", "w₂"],
    "constraints": [
      {
        "type": "count_eq",
        "lhs": {"count_of": "a", "in": "w₁"},
        "rhs": {"count_of": "b", "in": "w₂"}
      }
    ]
  },
  "question": "is_ll"
}
```

### 2.3. Структура IR для Format 2

```json
{
  "task_type": "ll_check_grammar_lang",
  "alphabet": ["a", "b", "c"],
  "grammar": {
    "nonterminals": ["S", "A"],
    "terminals": ["a", "b", "c"],
    "start": "S",
    "rules": [
      {"lhs": "S", "rhs": [["S", "a", "S", "b"], ["ε"]]},
      {"lhs": "S", "rhs": [["A"]]},
      {"lhs": "A", "rhs": [["b", "c"], ["c", "S", "c"]]}
    ]
  },
  "question": "is_ll_language"
}
```

### 2.4. Структура IR для Format 3

```json
{
  "task_type": "ll_check_grammar",
  "grammar": {
    "nonterminals": ["S", "A", "B"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
      {"lhs": "S", "rhs": [["a", "A", "b"], ["b", "B", "a"]]},
      {"lhs": "A", "rhs": [["a", "A"], ["ε"]]},
      {"lhs": "B", "rhs": [["b", "B"], ["ε"]]}
    ]
  },
  "question": "is_ll_k",
  "k": null
}
```

Поле `k`: если задано число — проверить LL(k) для конкретного k. Если `null` —
найти минимальное k, для которого грамматика LL(k), или доказать, что такого
k не существует.

### 2.5. Структура ответа агентов (AgentResult)

```json
{
  "agent_name": "substitution_agent",
  "verdict": "not_ll",
  "confidence": 0.85,
  "proof_sketch": {
    "method": "substitution",
    "for_all_k": true,
    "witness": {
      "k": "k (arbitrary)",
      "w1": "a^{n+k}",
      "lookahead": "b^k",
      "suffix_1": "b^{n}",
      "suffix_2": "c^{n}",
      "substitution_result": "a^{n+k} b^{k} c^{n}",
      "why_not_in_L": "не принадлежит ни {aⁿbⁿ}, ни {aⁿcⁿ}"
    }
  },
  "artifacts": {
    "ll_grammar": null,
    "first_follow_table": null,
    "counterexample_words": ["a^{n+k} b^{k} c^{n}"]
  }
}
```

### 2.6. Структура ответа oracle (OracleResult)

```json
{
  "oracle_type": "first_follow",
  "grammar_verified": true,
  "k": 1,
  "is_ll_k": true,
  "first_sets": {"S": {"a", "b"}, "A": {"a", "ε"}},
  "follow_sets": {"S": {"$"}, "A": {"b"}},
  "conflicts": [],
  "parse_table": {
    "S": {"a": ["a", "A", "b"], "b": ["b", "B", "a"]},
    "A": {"a": ["a", "A"], "b": ["ε"]}
  }
}
```

При наличии конфликтов:

```json
{
  "is_ll_k": false,
  "conflicts": [
    {
      "nonterminal": "S",
      "lookahead": "a",
      "competing_rules": [["S", "a", "b"], ["a", "A"]],
      "type": "first_first"
    }
  ]
}
```

---

## 3. Pipeline (граф LangGraph)

### 3.1. Текстовая схема

```
__start__
    │
    ▼
input_parser_node ──── Format 3 ──────────────┐
    │                                          │
    ▼                                          │
language_preprocess_node ── regular ── __end__ │
    │                                          │
    ▼                                          │
run_classifier_node (advisory)                 │
    │                                          │
    ├──────── all 6 agents always ──────┐      │
    │                                   │      │
    ▼                                   ▼      │
┌─────────────────┐  ┌──────────────────┐      │
│  CONSTRUCTIVE   │  │   DESTRUCTIVE    │      │
│                 │  │                  │      │
│ ll_grammar_     │  │ substitution_    │      │
│   builder       │  │   agent          │      │
│                 │  │                  │      │
│ marker_         │  │ ambiguity_       │      │
│   analyzer      │  │   detector       │      │
│                 │  │                  │      │
│ grammar_        │  │ prefix_classes_  │      │
│   transformer   │  │   agent          │      │
└────────┬────────┘  └────────┬─────────┘      │
         │                    │                │
         └────────┬───────────┘                │
                  ▼                            │
         first_follow_oracle ◄─────────────────┘
                  │
                  ▼
         reasoning_agent ──── retry ──→ agents
                  │
                  ▼
         formalizer_node
                  │
                  ▼
         lean_verifier_node (optional)
                  │
                  ▼
         renderer_node
                  │
                  ▼
            __end__
```

### 3.2. Три архитектурных принципа

1. **Classifier advisory only.** Classifier не гейтит dispatch. Все 6
   специалистов запускаются всегда. Classifier hint уходит только в
   `reasoning_agent` как один из сигналов для взвешивания результатов.

2. **All agents always run.** Ни один агент не пропускается на основании
   classifier output. Урок из REG-системы: hypothesis module ошибался
   на палиндромных конструкциях, и пропуск агентов приводил к false negatives.

3. **Selective retry.** `reasoning_agent` формирует retry plan с конкретным
   списком агентов для повторного запуска и модифицированными параметрами
   (например, `substitution_agent` с другим выбором k). Полный рестарт
   всех агентов не производится.

---

## 4. Спецификация модулей

### 4.1. input_parser_node

**Модель:** Sonnet
**Вход:** Текст задачи (NL + формулы)
**Выход:** IR (§2.2, §2.3, или §2.4)
**Логика:**
1. Определить формат (1, 2, или 3) по ключевым словам:
   - Есть грамматика + вопрос "является ли грамматика LL" → Format 3
   - Есть грамматика + вопрос "является ли язык LL" → Format 2
   - Язык задан множеством → Format 1
2. Распарсить в IR.
3. Если Format 3 → маршрутизировать в `first_follow_oracle` напрямую
   (fast path).
4. Если parse failed → early_fail с сообщением об ошибке.

**Промпт:** `prompts/input_parser.md`

### 4.2. language_preprocess_node

**Тип:** Алгоритмический (pure fn) + Sonnet для нетривиальных случаев
**Вход:** IR
**Выход:** Обогащённый IR + preprocessing hints

**Пре-фильтры:**

1. **Regularity check.** Если язык распознан как регулярный (например,
   конечный, задан регулярным выражением, или условие тривиализируется) →
   ответ "LL(1)" немедленно, pipeline short-circuits к `__end__`.

   Примеры тривиализации:
   - `{w ∈ {a,b}* | |w| mod 3 = 0}` — регулярный
   - `{w₁ a w₂ | |w₁| = 0}` — фактически `{a w₂ | w₂ ∈ Σ*}` = `a·Σ*` —
     регулярный

2. **Disjunction pattern detection.** Обнаруживает структурные паттерны,
   указывающие на ¬LL:
   - Дизъюнкция в суффиксе с общим счётчиком: `aⁿ(bⁿ|cⁿ)`, `{aⁿbⁿ}∪{aⁿcⁿ}`
   - Объединение с общим префиксом и разными суффиксами
   - Разнородные суффиксы после общего стекового контекста

   Обнаруженный паттерн передаётся как hint в state для `substitution_agent`.

3. **Grammar → language extraction** (для Format 2). Для простых грамматик
   с левой рекурсией: попытка определить язык, который грамматика порождает.
   Если язык определён — переключить pipeline на анализ языка (как Format 1).

**Выходные hints (добавляются в state):**

```json
{
  "preprocess_hints": {
    "is_regular": false,
    "disjunction_pattern": {
      "detected": true,
      "pattern_type": "suffix_disjunction",
      "shared_prefix": "aⁿ",
      "branches": ["bⁿ", "cⁿ"],
      "shared_counter": "n"
    },
    "extracted_language": null,
    "structural_features": [
      "palindrome_construction",
      "explicit_midpoint_marker",
      "length_condition"
    ]
  }
}
```

### 4.3. run_classifier_node

**Модель:** Sonnet
**Вход:** IR + preprocessing hints
**Выход:** Advisory classification (не влияет на dispatch)

```json
{
  "prediction": "not_ll",
  "confidence": 0.75,
  "reasoning": "Дизъюнкция cⁿ|bⁿ после общего aⁿ — при чтении
    слева направо парсер не может определить, какой суффикс выбрать,
    не прочитав его целиком.",
  "suggested_methods": ["substitution", "disjunction_pattern"],
  "suggested_k": null
}
```

### 4.4. ll_grammar_builder (конструктивный)

**Модель:** Opus
**Вход:** IR + preprocessing hints
**Выход:** AgentResult с candidate LL(k)-грамматикой

**Стратегия:**
1. Проанализировать структуру языка: маркеры, разделители, стековые фазы.
2. Попытаться синтезировать LL(k)-грамматику — набор правил, в которых
   при чтении слева направо с предпросмотром k символов однозначно
   определяется, какое правило применить.
3. Предложить конкретное k.
4. Если грамматика синтезирована — передать в `first_follow_oracle`
   для алгоритмической верификации.

**Типичные паттерны, где builder успешен:**
- Явный маркер середины (`w b* c w^R` → LL(1))
- Стековые фазы с чётко определённой границей
- Вложенные скобочные структуры с уникальными разделителями
- Язык из одной строковой операции без дизъюнкции

**JSON выход:**

```json
{
  "agent_name": "ll_grammar_builder",
  "verdict": "ll",
  "confidence": 0.9,
  "proof_sketch": {
    "method": "ll_grammar_construction",
    "k": 1,
    "grammar": {
      "nonterminals": ["S", "M"],
      "terminals": ["a", "b", "c"],
      "start": "S",
      "rules": [
        {"lhs": "S", "rhs": [["a", "S", "a"], ["b", "S", "b"], ["M"]]},
        {"lhs": "M", "rhs": [["c"], ["c", "M", "c"]]}
      ]
    },
    "explanation": "Маркер c однозначно определяет середину. При чтении
      слева направо: если видим a или b — S → xSx; если видим c — S → M."
  }
}
```

### 4.5. marker_analyzer (конструктивный)

**Модель:** Sonnet
**Вход:** IR + preprocessing hints
**Выход:** AgentResult

**Стратегия:**
1. Искать в структуре языка **явный маркер** — символ или подстроку,
   которая однозначно отделяет одну фазу разбора от другой.
2. Типичные маркеры:
   - Уникальный символ, не используемый ни в одной из компонент
     (`c` в `{w b* c w^R}`)
   - Фиксированная подстрока-разделитель
   - Переход между алфавитами (`a*` → `b*` → `c*`)
3. Если маркер найден — объяснить, как LL-парсер использует его для
   однозначного выбора правила. Предложить грамматику.

**Когда marker_analyzer не работает:**
- Палиндромы без маркера (`{ww^R | w ∈ {a,b}*}` — не LL)
- Дизъюнкция с одинаковым алфавитом (`aⁿ(bⁿ|cⁿ)` если b,c не маркеры)
- Условия на длины без явного разделителя

### 4.6. grammar_transformer (конструктивный)

**Модель:** Sonnet
**Вход:** IR (только Format 2) + preprocessing hints
**Выход:** AgentResult с трансформированной грамматикой

**Стратегия:**
1. Получить грамматику из Format 2.
2. Применить последовательность трансформаций:
   a. **Устранение левой рекурсии** — метод из Aho, Ullman:
      `A → Aα | β` ⟹ `A → βA'`, `A' → αA' | ε`
   b. **Левая факторизация** — общий префикс:
      `A → αβ₁ | αβ₂` ⟹ `A → αA'`, `A' → β₁ | β₂`
   c. **Устранение ε-правил** (если мешают LL-свойству)
   d. **Подстановка** коротких правил
3. Проверить трансформированную грамматику: передать в
   `first_follow_oracle`.
4. Если конфликты остались — сообщить, какие именно.

**Важно:** Трансформации сохраняют язык грамматики, но не обязательно
структуру деревьев разбора. Это корректно, потому что вопрос про язык.

**Применяется только для Format 2.** Для Format 1 грамматику синтезирует
`ll_grammar_builder`.

### 4.7. substitution_agent (деструктивный)

**Модель:** Opus
**Вход:** IR + preprocessing hints (включая disjunction_pattern hint)
**Выход:** AgentResult

**Стратегия (метод подмены):**

Для каждого k (начиная с k=1, до разумного предела или для произвольного k):

1. Найти два слова `w₁·v·w₂` и `w₁·v·w₃` в L, где `|v| = k` (lookahead).
2. Показать, что при подмене суффиксов слово `w₁·v·w₃'` ∉ L (или наоборот).
3. Объяснить, почему это означает, что при одинаковом lookahead v парсер
   не может определить, какое правило применить.

**Формальная запись:**

Пусть `w₁ v w₂ ∈ L` и `w₁ v w₃ ∈ L`, где `|v| = k`.
Если после чтения `w₁` стек содержит ≥ k+s символов, то последние s
символов стека «раскрываются» внутри w₂ и w₃ (не затрагивая v), и
суффиксы должны быть взаимозаменяемы.
Если нашли w₂, w₃, которые нельзя подменить — язык не LL(k).

**Пример (из базы знаний):**

`{aⁿbⁿ} ∪ {aⁿcⁿ}`. Для любого k:
- `w₁ = a^{n+k}`, lookahead = первые k символов суффикса
- После чтения `aⁿ` в стеке ≥ k+2 символов
- Подмена `b^{n+k}` на `c^{n+k}` → `a^{n+k} b^{n+k-m} c^t ∉ L`
- Противоречие → не LL(k) ни для какого k

**Использование hint:** Если `preprocess_hints.disjunction_pattern.detected`,
агент сразу фокусируется на подмене суффиксов из `branches`.

### 4.8. ambiguity_detector (деструктивный)

**Модель:** Opus
**Вход:** IR + preprocessing hints
**Выход:** AgentResult

**Стратегия:**
1. Проверить, является ли язык **существенно неоднозначным** — т.е. не
   существует однозначной КС-грамматики, порождающей L.
2. Если язык существенно неоднозначен → он не LL (LL-грамматика всегда
   однозначна).

**Критерий:** Язык L существенно неоднозначен, если для **каждой**
грамматики G с L(G) = L найдётся слово с двумя разными деревьями разбора.

**Классический пример:**
`{aⁱ bʲ cᵏ | i = j ∨ j = k}` — существенно неоднозначен (теорема Огдена).
Слово `aⁿ bⁿ cⁿ` допускает два разбора: по условию i=j и по условию j=k.

**Ограничения:** Существенная неоднозначность — сложное свойство, не всегда
легко доказуемое. Агент может вернуть `verdict: "uncertain"`.

### 4.9. prefix_classes_agent (деструктивный)

**Модель:** Opus
**Вход:** IR + preprocessing hints
**Выход:** AgentResult

**Стратегия:**
1. Для данного k построить экспоненциально много (≥ 2^{k+s}) различных
   префиксов, которые «ведут себя по-разному» при одном lookahead.
2. Два префикса u₁, u₂ «ведут себя по-разному», если существуют суффиксы
   v₁, v₂ такие, что `u₁·v₁ ∈ L`, `u₂·v₂ ∈ L`, но `u₁·v₂ ∉ L` или
   `u₂·v₁ ∉ L`, при этом FIRST_k(v₁) = FIRST_k(v₂).
3. Если для любого k можно построить достаточно много таких классов —
   язык не LL(k).

**Связь с Nerode:** Этот метод по духу аналогичен теореме Майхилла-Нероде
для регулярных языков. Для LL-языков число классов эквивалентности
по «поведению при одном lookahead» конечно для каждого k.

**Когда эффективен:**
- Языки с зависимостями между удалёнными частями слова
- Палиндромные конструкции без маркера

---

## 5. Verification layer

### 5.1. first_follow_oracle (pure fn)

**Тип:** Алгоритмический, без LLM.
**Вход:** КС-грамматика + параметр k
**Выход:** OracleResult (§2.6)

**Алгоритм:**
1. Вычислить NULLABLE(A) для каждого нетерминала A.
2. Вычислить FIRST_k(α) для каждой правой части α:
   - FIRST_k(ε) = {ε}
   - FIRST_k(a·β) = {a} ⊕_k FIRST_k(β) (k-prefix concatenation)
   - FIRST_k(A·β) = FIRST_k(A) ⊕_k FIRST_k(β)
   - FIRST_k(A) = ⋃ FIRST_k(αᵢ) для всех правил A → αᵢ
   Итерация до неподвижной точки.
3. Вычислить FOLLOW_k(A) для каждого нетерминала A:
   - FOLLOW_k(S) = {$}
   - Если B → αAβ, то FOLLOW_k(A) ⊇ FIRST_k(β) ⊕_k FOLLOW_k(B)
   Итерация до неподвижной точки.
4. Проверить LL(k)-условие: для всех пар правил A → α | β:
   FIRST_k(α · FOLLOW_k(A)) ∩ FIRST_k(β · FOLLOW_k(A)) = ∅
5. Если условие выполнено → построить таблицу разбора.
   Если нет → вернуть список конфликтов.

**Операция ⊕_k (k-prefix concatenation):**
`X ⊕_k Y = {(xy)[:k] | x ∈ X, y ∈ Y}`

**Timeout:** 5s для грамматик с > 50 правилами.
**Max k:** 10 (для автоматического поиска минимального k).

### 5.2. ll_parse_oracle (pure fn)

**Тип:** Алгоритмический.
**Вход:** LL(k)-грамматика (проверенная `first_follow_oracle`) +
         набор тестовых слов
**Выход:** Результат разбора для каждого слова

**Логика:**
1. Получить таблицу разбора из `first_follow_oracle`.
2. Для каждого тестового слова:
   a. Запустить LL(k)-парсер (стек + lookahead).
   b. Вернуть: принято/отклонено + дерево разбора (если принято).
3. Сверить результат с CYK oracle (из CFL-системы) — оба должны
   дать одинаковый ответ для каждого слова.

**Назначение:** Верифицировать, что синтезированная LL(k)-грамматика
порождает тот же язык, что и исходный.

### 5.3. claim_verifier (pure fn)

**Тип:** Алгоритмический.
**Вход:** AgentResult от любого агента
**Выход:** Verified / Refuted / Inconclusive

**Проверки по типу доказательства:**

| Метод | Проверка |
|-------|---------|
| `ll_grammar_construction` | `first_follow_oracle` на предложенной грамматике |
| `substitution` | Проверить `w₁·v·w₂ ∈ L` и `w₁·v·w₃ ∈ L` через CYK oracle; проверить `w₁·v·w₃' ∉ L` |
| `essential_ambiguity` | Проверить, что предложенное слово действительно допускает два разбора |
| `prefix_classes` | Проверить, что предложенные префиксы действительно ведут себя по-разному |
| `marker_detection` | `first_follow_oracle` на предложенной грамматике |
| `grammar_transformation` | `first_follow_oracle` на трансформированной грамматике + проверить эквивалентность языков (через тестовые слова) |

### 5.4. word_generator

Переиспользуется из `agent_system.lib.word_generator`. Генерирует тестовые
слова для oracle:
- Слова из L (positive examples)
- Слова вне L (negative examples)
- Граничные случаи (пустое слово, очень длинные слова, слова на границе
  условий)

---

## 6. Reasoning + Retry

### 6.1. reasoning_agent

**Модель:** Opus
**Вход:** Все AgentResult (6 штук) + OracleResult + classifier hint
**Выход:** Финальный вердикт + proof + retry plan (если нужен)

**Логика:**
1. Собрать все результаты. Приоритет:
   - Oracle-verified results > unverified agent claims
   - Constructive proof (LL-грамматика + oracle OK) > destructive proof
   - Конструктивные и деструктивные не могут быть оба верифицированы —
     если это произошло, что-то сломалось.
2. Если есть конфликт между агентами — запросить retry.
3. Если ни один агент не дал уверенного результата — запросить retry
   с модифицированными параметрами.

### 6.2. retry_plan

```json
{
  "retry_requested": true,
  "max_retries": 2,
  "agents_to_retry": [
    {
      "agent": "substitution_agent",
      "modified_params": {
        "k_range": [3, 7],
        "word_length_min": 20,
        "hint": "Попробовать подмену с w₁ = a^{n+k} b^k"
      }
    }
  ],
  "reason": "substitution_agent вернул uncertain для k=1,2; возможно,
    нужен больший k для демонстрации конфликта."
}
```

**Max retries:** 2 (жёстко). После 2 retry — reasoning agent обязан
вынести вердикт на основании имеющихся данных, даже если confidence < 0.7.

---

## 7. Выходной формат

### 7.1. Четыре формата вывода

| Формат | Файл | Назначение |
|--------|------|-----------|
| JSON | `result.json` | Machine-readable, для пайплайна |
| Markdown | `result.md` | Human-readable, текст решения |
| HTML | `result.html` | Экзаменационный стиль с табами |
| Lean 4 | `result.lean` | Формальное доказательство |

### 7.2. Структура JSON

```json
{
  "task_type": "ll_check_language",
  "input": "...",
  "verdict": "not_ll",
  "proof": {
    "method": "substitution",
    "details": { ... }
  },
  "confidence": 0.95,
  "agents_used": ["substitution_agent", "ambiguity_detector"],
  "oracle_checks": [
    {"type": "cyk", "words_checked": 50, "all_consistent": true}
  ]
}
```

### 7.3. Guard: proof is None

**Критически важно:** В renderer_node всегда проверять:

```python
if result.proof is None:
    # Не пытаться обращаться к result.proof.method
    # Рендерить "не удалось доказать" с объяснением
    render_inconclusive(result)
else:
    render_proof(result)
```

Этот баг уже был в REG-системе (renderer crashes when `proof=None`).

---

## 8. Файловая структура

```
ll_system/
├── CLAUDE.md                        # Контекст для Claude Code
├── tz_ll_agent_system.md            # Данный документ
├── lib/
│   ├── __init__.py
│   ├── ll_ir_schema.py              # IR schema (§2)
│   ├── first_follow.py              # FIRST_k / FOLLOW_k алгоритмы (§5.1)
│   ├── ll_table_builder.py          # LL(k) parse table construction
│   ├── ll_parser.py                 # LL(k) parser (§5.2)
│   ├── grammar_transforms.py        # Left recursion elim, left factoring
│   ├── claim_verifier.py            # Claim verification (§5.3)
│   ├── preprocess.py                # Regularity check, disjunction detect
│   └── utils.py                     # Вспомогательные функции
├── prompts/
│   ├── input_parser.md
│   ├── classifier.md
│   ├── ll_grammar_builder.md
│   ├── marker_analyzer.md
│   ├── grammar_transformer.md
│   ├── substitution_agent.md
│   ├── ambiguity_detector.md
│   ├── prefix_classes_agent.md
│   ├── reasoning_agent.md
│   └── formalizer.md
├── agents/
│   ├── __init__.py
│   ├── input_parser.py
│   ├── classifier.py
│   ├── ll_grammar_builder.py
│   ├── marker_analyzer.py
│   ├── grammar_transformer.py
│   ├── substitution_agent.py
│   ├── ambiguity_detector.py
│   ├── prefix_classes_agent.py
│   ├── reasoning_agent.py
│   └── formalizer.py
├── orchestrator.py                  # LangGraph pipeline
├── renderer.py                      # JSON/MD/HTML/Lean output
├── tests/
│   ├── test_first_follow.py
│   ├── test_ll_parser.py
│   ├── test_grammar_transforms.py
│   ├── test_preprocess.py
│   ├── test_claim_verifier.py
│   ├── test_ir_schema.py
│   └── test_integration.py
├── examples/
│   ├── format1_anbn_union_ancn.json
│   ├── format1_wb_star_c_wR.json
│   ├── format2_SabS_Sc_eps.json
│   ├── format3_simple_ll1.json
│   └── mock/                        # Mock LLM responses for testing
│       ├── ll_grammar_builder_mock.json
│       ├── substitution_agent_mock.json
│       └── ...
└── lean_templates/
    ├── ll_grammar_proof.lean
    └── not_ll_proof.lean
```

---

## 9. Фазы реализации

### Phase 1: Core Infrastructure (pure fn, no LLM)

**Цель:** Реализовать все алгоритмические компоненты.

**Задачи:**
1. `lib/ll_ir_schema.py` — IR schema с валидацией (jsonschema)
2. `lib/first_follow.py` — FIRST_k, FOLLOW_k, NULLABLE
3. `lib/ll_table_builder.py` — LL(k) table construction + conflict detection
4. `lib/ll_parser.py` — LL(k) parser (стек + lookahead)
5. `lib/grammar_transforms.py` — left recursion elimination, left factoring
6. `lib/preprocess.py` — regularity check, disjunction pattern detection
7. `lib/claim_verifier.py` — claim verification
8. Тесты для всего вышеперечисленного

**Команды:**
```bash
cd ll_system/
python -m pytest tests/test_first_follow.py -v
python -m pytest tests/test_ll_parser.py -v
python -m pytest tests/test_grammar_transforms.py -v
python -m pytest tests/ -v  # все тесты
```

**Критерий готовности:** Все тесты проходят. `first_follow_oracle` корректно
определяет LL(1)/LL(2)/не-LL для 10+ тестовых грамматик.

### Phase 2: Prompts + Mock Agents

**Цель:** Написать промпты для всех 6 агентов + reasoning + formalizer.
Тестировать с mock LLM responses.

**Задачи:**
1. Промпты для каждого агента (`prompts/`)
2. Mock responses для каждого агента (`examples/mock/`)
3. `orchestrator.py` — LangGraph pipeline (работает с mock)
4. `renderer.py` — вывод в 4 формата
5. Интеграционный тест с mock: задача → pipeline → результат

**Команды:**
```bash
python orchestrator.py examples/format1_anbn_union_ancn.json --mock examples/mock/
python orchestrator.py examples/format3_simple_ll1.json --mock examples/mock/
```

### Phase 3: LLM Integration

**Цель:** Подключить реальные LLM (Sonnet для лёгких агентов,
Opus для тяжёлых).

**Задачи:**
1. Подключить Anthropic API для каждого агента
2. Реализовать retry logic
3. Тестирование на реальных задачах из билетов

**Модели:**
- Sonnet: input_parser, classifier, marker_analyzer, grammar_transformer
- Opus: ll_grammar_builder, substitution_agent, ambiguity_detector,
  prefix_classes_agent, reasoning_agent

### Phase 4: Lean 4 Integration + Live Testing

**Цель:** Lean verification + тестирование на полном наборе билетов.

**Задачи:**
1. Lean 4 templates для LL-доказательств
2. Docker environment для Lean
3. Прогон на задачах из ~70 билетов (задачи на LL-свойство)
4. Сбор метрик: accuracy, false positives/negatives

---

## 10. Зависимости

### 10.1. Python

```
python >= 3.11
jsonschema
langgraph
anthropic
```

### 10.2. Из REG-системы

```python
from agent_system.lib.ir_schema import BaseIR, Alphabet, Constraint
from agent_system.lib.oracle import OracleBase
from agent_system.lib.word_generator import WordGenerator
```

### 10.3. Из CFL-системы

```python
from cfl_system.lib.cyk_oracle import CYKOracle
from cfl_system.lib.cfg_utils import CFG, parse_grammar, normalize_grammar
```

### 10.4. Lean 4

Docker image `leanprover/lean4:v4.18.0` (переиспользуется из REG/CFL).

---

## 11. Критические замечания

1. **Подмена ≠ накачка.** Метод подмены (substitution method) — это НЕ
   лемма о накачке. Это отдельная техника, специфичная для LL-свойства.
   Не путать в промптах.

2. **Язык vs грамматика.** Формулировка вопроса критически важна:
   - "Является ли грамматика LL(k)" — проверяется алгоритмически
   - "Является ли язык LL(k)" — может потребовать синтез другой грамматики
   Левая рекурсия в грамматике НЕ означает, что язык не LL.

3. **LL(k) иерархия.** LL(k) ⊂ LL(k+1) (строго). Если язык LL(k),
   он автоматически LL(n) для всех n > k. Минимальное k — часть ответа.

4. **Замыкание LL-языков.** LL-языки замкнуты относительно пересечения
   с регулярными языками. НЕ замкнуты относительно объединения,
   конкатенации, дополнения, реверса.

5. **Guard proof=None.** Renderer ОБЯЗАН проверять `proof is None` перед
   обращением к полям proof. Баг из REG-системы.

6. **BFS word generation.** Генерация слов из грамматики должна
   использовать BFS с ограничением по длине, а не рекурсивный DFS.
   DFS зависает на рекурсивных грамматиках. Баг из REG-системы.

7. **Ключевая интуиция для промптов.** LL-парсер читает строго слева
   направо и должен в каждой точке, глядя на k символов вперёд,
   однозначно выбрать правило. Если для выбора нужна информация
   «из будущего» (палиндромы без маркера, aⁿbⁿ ∪ aⁿcⁿ) — язык не LL.

8. **Иерархия для ориентира.**
   Регулярные ⊂ LL(1) ⊂ LL(k) ⊂ LR(0)+ε ⊂ LR(k) = DCFL ⊂ КС.
   Связь с DCFL-системой: если DCFL-система доказала, что язык не DCFL →
   он тем более не LL.

---

## 12. Связь с другими подсистемами

### 12.1. Связь с DCFL-системой

Если DCFL-система (`dcfl_system/`) уже вынесла вердикт по задаче:
- `is_dcfl: false` → язык не LL (автоматически, без запуска LL-агентов)
- `is_dcfl: true` → язык может быть LL или не-LL (нужен LL-анализ)

### 12.2. Связь с CFL-системой

Если CFL-система вынесла:
- `is_cfl: false` → язык не LL (автоматически)
- `is_cfl: true` → нужен дальнейший анализ

### 12.3. Каскадная маршрутизация

Для задач вида "определить положение языка в иерархии Хомского" возможна
каскадная маршрутизация:

```
CFL → DCFL → LL → конкретное k
```

Каждая подсистема может short-circuit если ответ отрицательный на более
широком уровне.
