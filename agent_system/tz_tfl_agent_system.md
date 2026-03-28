# ТЗ: Агентская система для задач по теории формальных языков (REG)

**Версия:** 0.1 (draft)  
**Дата:** 2026-03-27  
**Scope:** Регулярные языки (REG). Расширение до CFL — отдельная итерация.  
**Runtime:** Claude Code (Max plan) + Lean 4 (Docker) + Python pure functions  
**Источники формул:** Hopcroft, Ullman, Motwani; Боянчик (алгебраический подход); база знаний ИУ-9

---

## 1. Определения и формулы

### 1.1. Основные объекты

**Алфавит** Σ — конечное непустое множество символов. Типичные случаи: Σ = {a, b}, Σ = {(, )}, Σ = {a}.

**Слово** w ∈ Σ* — конечная последовательность символов. ε — пустое слово. |w| — длина слова. |w|_a — число вхождений символа a в слово w. |w|_ξ — число вхождений подслова ξ в слово w (с перекрытиями или без — уточняется в задаче). w^R — реверс слова w.

**Язык** L ⊆ Σ* — множество слов над алфавитом Σ.

### 1.2. Детерминированный конечный автомат (ДКА)

**Определение (Hopcroft, Def. 2.2.1):**
ДКА — это пятёрка A = ⟨Q, Σ, δ, q₀, F⟩, где:
- Q — конечное множество состояний
- Σ — входной алфавит
- δ: Q × Σ → Q — функция переходов (тотальная)
- q₀ ∈ Q — начальное состояние
- F ⊆ Q — множество допускающих состояний

**Расширенная функция переходов** δ̂: Q × Σ* → Q:
- δ̂(q, ε) = q
- δ̂(q, wa) = δ(δ̂(q, w), a)

**Язык ДКА:** L(A) = {w ∈ Σ* | δ̂(q₀, w) ∈ F}

### 1.3. Недетерминированный конечный автомат (НКА)

**Определение (Hopcroft, Def. 2.3.1):**
НКА — пятёрка A = ⟨Q, Σ, δ, q₀, F⟩, где δ: Q × (Σ ∪ {ε}) → 2^Q.

**Теорема (Hopcroft, Th. 2.3.5):** Для любого НКА существует эквивалентный ДКА (конструкция подмножеств).

### 1.4. Регулярные выражения

**Определение (Hopcroft, Def. 3.1.1):**
Регулярные выражения над Σ определяются индуктивно:
- ∅, ε, a (для каждого a ∈ Σ) — базовые РВ
- Если R₁, R₂ — РВ, то R₁ | R₂ (объединение), R₁ · R₂ (конкатенация), R₁* (итерация Клини) — РВ

**Теорема Клини (Hopcroft, Th. 3.2.1):** Класс языков, описываемых РВ, совпадает с классом языков, распознаваемых конечными автоматами.

### 1.5. Лемма о накачке для регулярных языков

**Теорема (Hopcroft, Th. 4.1.1, Pumping Lemma for REG):**

Пусть L — регулярный язык. Тогда существует натуральное число n (длина накачки), такое что для любого слова w ∈ L, |w| ≥ n, существует разбиение w = xyz, удовлетворяющее условиям:

1. |xy| ≤ n
2. |y| ≥ 1 (т.е. y ≠ ε)
3. Для всех i ≥ 0: xy^i z ∈ L

**Контрапозитив (для доказательства нерегулярности):**

Если для любого n ∈ ℕ существует слово w ∈ L, |w| ≥ n, такое что для любого разбиения w = xyz с |xy| ≤ n и |y| ≥ 1 существует i ≥ 0, для которого xy^i z ∉ L, то L нерегулярен.

**Порядок кванторов в доказательстве нерегулярности:**
∀n ∃w∈L (|w|≥n) ∀x,y,z (w=xyz ∧ |xy|≤n ∧ |y|≥1) ∃i≥0: xy^i z ∉ L

### 1.6. Теорема Майхилла-Нероуда

**Определение (Hopcroft, Def. 3.4.1; Боянчик, Th. 1.6):**

Правая конгруэнция Нероуда: для языка L ⊆ Σ*, определим отношение эквивалентности ≡_L на Σ*:

u ≡_L v  ⟺  ∀z ∈ Σ*: (uz ∈ L ⟺ vz ∈ L)

Слово z называется **различающим контекстом** (distinguishing extension) для пары (u, v).

**Теорема Майхилла-Нероуда (Hopcroft, Th. 3.4.3):**

L регулярен ⟺ индекс ≡_L конечен (т.е. число классов эквивалентности конечно).

Более того, количество классов ≡_L равно числу состояний минимального ДКА для L.

### 1.7. Синтаксическая конгруэнция и синтаксический моноид

**Определение (Боянчик, Def. 1.7):**

Синтаксическая конгруэнция ~_L на Σ*:

w ~_L w'  ⟺  ∀u, v ∈ Σ*: (uwv ∈ L ⟺ uw'v ∈ L)

Это двусторонняя версия конгруэнции Нероуда. Фактор-множество Σ*/~_L с операцией конкатенации образует **синтаксический моноид** M(L).

**Теорема (Боянчик, Th. 1.6):**

L регулярен ⟺ M(L) конечен.

**Связь с ДКА (Боянчик, Ex. 13):** Синтаксический гомоморфизм h: Σ* → M(L) задаётся формулой h(w) = (q ↦ δ̂(q, w)), то есть каждому слову сопоставляется преобразование состояний минимального ДКА.

### 1.8. Замкнутость класса REG

Класс регулярных языков замкнут относительно **всех** стандартных операций:

| Операция | Обозначение | Замкнутость REG | Конструкция | Применимость в Closure Agent |
|----------|-------------|-----------------|-------------|------------------------------|
| Объединение | L₁ ∪ L₂ | + | НКА: новый старт с ε-переходами | Синтез |
| Пересечение | L₁ ∩ L₂ | + | Прямое произведение ДКА | Опровержение (ключевое) |
| Дополнение | Σ* \ L | + | Инверсия допускающих состояний ДКА | Синтез + опровержение |
| Конкатенация | L₁ · L₂ | + | НКА: конец L₁ → начало L₂ | Синтез |
| Итерация Клини | L* | + | НКА: ε-переход из конца в начало | Синтез |
| Shuffle | L₁ ш L₂ | + | Произведение с чередованием | Редко |
| Реверс | L^R | + | Реверс всех переходов ДКА | Синтез + опровержение |
| SHIFT | SHIFT(L) | + | [116] | Редко |
| Гомоморфизм | h(L) | + | Замена переходов | Опровержение (затирающий h) |
| ε-free гомоморфизм | h_ε-free(L) | + | Замена переходов | Опровержение |
| Кодовый гомоморфизм | h_code(L) | + | Замена переходов | Редко |
| Обратный гомоморфизм | h⁻¹(L) | + | Композиция δ с h | Опровержение |

**Примечание для будущего расширения (CFL):**
CF НЕ замкнут относительно ∩ и ~ (complement). Однако CF ∩ REG = CF (пересечение КС-языка с регулярным = КС). Это позволяет использовать аргумент: «L ∩ R не КС для регулярного R → L не КС».

**Применение для доказательства нерегулярности (инструменты Closure Agent):**

1. **Пересечение с регулярным:** Если L ∩ R нерегулярен для некоторого регулярного R → L нерегулярен. Пример: L = {w | |w|_a = |w|_b}, R = a*b*, L ∩ R = {a^n b^n} — нерегулярен.
2. **Затирающий гомоморфизм:** h: Σ* → Γ* с h(c) = ε. Если h(L) нерегулярен → L нерегулярен. Пример: L ⊆ {a,b,c}*, h(c) = ε, h(L) = {a^n b^n}.
3. **Обратный гомоморфизм:** Если h⁻¹(L) нерегулярен → L нерегулярен.
4. **Дополнение:** Если L нерегулярен ⟺ Σ* \ L нерегулярен (иногда проще работать с дополнением).

### 1.9. Длина накачки

Для регулярного языка L, заданного ДКА с n состояниями, длина накачки p ≤ n.

Для языка, заданного регулярным выражением R, длина накачки p ≤ |R|_ops + 1, где |R|_ops — число символов-операндов в R (без учёта операторов | · *).

Точная длина накачки — минимальное n, для которого выполняется лемма о накачке. Нахождение точной длины накачки — NP-трудная задача в общем случае.

---

## 2. Категории задач (по билетам ИУ-9)

На основе анализа семестровых и экзаменационных задач выделены следующие категории:

### Категория A: Предикатные языки
**Вход:** Язык задан предикатом над словами.
**Примеры:**
- `{w ∈ {a,b}* | ∃v,u(|v|>0 & w = vv^R u ∨ w = uvv^R)}`
- `{z₁wawbz₂ | z_i, w ∈ {a,b}⁺ & |z₁| < |z₂|}`
- `{w₁w₂ | |w₁| > 0 & ∃v(w₁ = vv^R)}`
- `{w₁w₂w₁ | |w₁| > 0 & w₂ не начинается с w₁}`

**Задача:** Классифицировать (REG / non-REG) и доказать.

### Категория B: Языки грамматик (CFG → REG?)
**Вход:** Контекстно-свободная грамматика.
**Примеры:**
- S → SaSb | ε | A,  A → bb | aa | bSb
- S → BB | aA,  A → SS | cB,  B → ε | SAb
- S → abSbb | ε | bbSba | aA,  A → bA | S
- S → aSSb | ba | Ab,  A → aAb | aSa

**Задача:** Определить, порождает ли грамматика регулярный язык. Если да — построить РВ или ДКА.

### Категория C: Regex с backreference
**Вход:** Расширенное регулярное выражение с \k (backreference).
**Примеры:**
- `((a*)b?(\2)⁺)*`
- `(a*(a*)b\2)*`

**Задача:** Определить, является ли язык регулярным (backreference может выводить за пределы REG).

### Категория D: Вычислительные задачи над REG
**Вход:** Регулярное выражение или ДКА.
**Примеры:**
- Найти длину накачки языка `((a|b)*bb(a|b)(a|b))|(b(abaa)*|abb*)*`
- Построить дополнение к `((aa|bb)*aba)*` над {a,b}
- Построить минимальный ДКА

**Задача:** Вычислить конкретный объект (число, автомат, РВ).

### Категория E: Метаязыковые задачи
**Вход:** Описание языка на естественном языке (например, «язык регулярных выражений без вложенных скобок»).
**Примеры:**
- Построить минимальный ДКА для языка регулярных выражений без итераций, над выражениями, которые могут принимать ε, без вложенных скобок. Алфавит регулярок — {a}.
- Подмножество неправильных скобочных последовательностей, исправляемых одной перестановкой, с условием на реверс/замену.

**Задача:** Формализовать описание и решить.

### Категория F: Параметрические задачи
**Вход:** Язык, зависящий от параметра.
**Примеры:**
- Для каких слов ξ₁, ξ₂ язык {|w|_ξ₁ = |w|_ξ₂ | w ∈ {a,b}*} регулярен?
- {a^(n/log n)} — регулярен ли?

**Задача:** Определить, при каких значениях параметров язык регулярен.

---

## 3. IR (Intermediate Representation) — JSON Schema

### 3.1. Верхний уровень

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["task_type", "source_text"],
  "properties": {
    "task_type": {
      "enum": [
        "classify",
        "prove_regular",
        "prove_non_regular",
        "classify_and_prove",
        "compute_pumping_length",
        "build_dfa",
        "build_complement",
        "build_regex",
        "parametric_analysis"
      ]
    },
    "source_text": { "type": "string" },
    "language_spec": { "$ref": "#/$defs/LanguageSpec" }
  }
}
```

### 3.2. Спецификация языка (LanguageSpec)

```json
{
  "$defs": {
    "LanguageSpec": {
      "oneOf": [
        { "$ref": "#/$defs/PredicateLanguage" },
        { "$ref": "#/$defs/GrammarLanguage" },
        { "$ref": "#/$defs/RegexLanguage" },
        { "$ref": "#/$defs/NaturalLanguage" },
        { "$ref": "#/$defs/ArithmeticIndexLanguage" }
      ]
    }
  }
}
```

### 3.3. Предикатный язык (PredicateLanguage)

```json
{
  "type": "object",
  "required": ["kind", "alphabet", "variable", "predicate"],
  "properties": {
    "kind": { "const": "predicate" },
    "alphabet": {
      "type": "array",
      "items": { "type": "string" },
      "examples": [["a", "b"], ["(", ")"]]
    },
    "variable": { "type": "string", "default": "w" },
    "predicate": { "$ref": "#/$defs/Predicate" }
  }
}
```

### 3.4. Типы предикатов (Predicate)

```json
{
  "$defs": {
    "Predicate": {
      "oneOf": [
        { "$ref": "#/$defs/BooleanCombination" },
        { "$ref": "#/$defs/Comparison" },
        { "$ref": "#/$defs/Modular" },
        { "$ref": "#/$defs/ExistsDecomposition" },
        { "$ref": "#/$defs/SubstringCheck" },
        { "$ref": "#/$defs/PrefixSuffixCheck" },
        { "$ref": "#/$defs/PalindromeCheck" },
        { "$ref": "#/$defs/NegationStartsWith" }
      ]
    },

    "BooleanCombination": {
      "type": "object",
      "required": ["op", "operands"],
      "properties": {
        "op": { "enum": ["and", "or", "not"] },
        "operands": {
          "type": "array",
          "items": { "$ref": "#/$defs/Predicate" }
        }
      }
    },

    "Comparison": {
      "type": "object",
      "required": ["op", "left", "right"],
      "properties": {
        "op": { "enum": ["eq", "neq", "lt", "leq", "gt", "geq"] },
        "left": { "$ref": "#/$defs/Expr" },
        "right": { "$ref": "#/$defs/Expr" }
      }
    },

    "Modular": {
      "type": "object",
      "required": ["expr", "modulus", "remainder"],
      "properties": {
        "expr": { "$ref": "#/$defs/Expr" },
        "modulus": { "type": "integer", "minimum": 2 },
        "remainder": { "type": "integer", "minimum": 0 }
      }
    },

    "ExistsDecomposition": {
      "type": "object",
      "required": ["parts", "concat_pattern", "constraints"],
      "properties": {
        "parts": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Bound variable names: ['v', 'u']"
        },
        "concat_pattern": {
          "type": "array",
          "items": { "type": "string" },
          "description": "How parts form w: ['v', 'rev(v)', 'u']"
        },
        "constraints": {
          "type": "array",
          "items": { "$ref": "#/$defs/Predicate" }
        }
      }
    },

    "SubstringCheck": {
      "type": "object",
      "required": ["substring_expr", "in_var"],
      "properties": {
        "op": { "enum": ["is_substring", "is_not_substring"] },
        "substring_expr": { "type": "string" },
        "in_var": { "type": "string" }
      }
    },

    "Expr": {
      "oneOf": [
        {
          "type": "object",
          "properties": {
            "kind": { "const": "count_symbol" },
            "symbol": { "type": "string" },
            "in_var": { "type": "string" }
          }
        },
        {
          "type": "object",
          "properties": {
            "kind": { "const": "count_subword" },
            "subword": { "type": "string" },
            "in_var": { "type": "string" }
          }
        },
        {
          "type": "object",
          "properties": {
            "kind": { "const": "length" },
            "of_var": { "type": "string" }
          }
        },
        {
          "type": "object",
          "properties": {
            "kind": { "const": "constant" },
            "value": { "type": "integer" }
          }
        }
      ]
    }
  }
}
```

### 3.5. Язык грамматики (GrammarLanguage)

```json
{
  "type": "object",
  "required": ["kind", "terminals", "nonterminals", "start", "rules"],
  "properties": {
    "kind": { "const": "grammar" },
    "terminals": { "type": "array", "items": { "type": "string" } },
    "nonterminals": { "type": "array", "items": { "type": "string" } },
    "start": { "type": "string" },
    "rules": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "lhs": { "type": "string" },
          "rhs": { "type": "array", "items": { "type": "string" } }
        }
      }
    }
  }
}
```

**Пример** (задача: S → SaSb | ε | A, A → bb | aa | bSb):
```json
{
  "kind": "grammar",
  "terminals": ["a", "b"],
  "nonterminals": ["S", "A"],
  "start": "S",
  "rules": [
    {"lhs": "S", "rhs": ["S", "a", "S", "b"]},
    {"lhs": "S", "rhs": []},
    {"lhs": "S", "rhs": ["A"]},
    {"lhs": "A", "rhs": ["b", "b"]},
    {"lhs": "A", "rhs": ["a", "a"]},
    {"lhs": "A", "rhs": ["b", "S", "b"]}
  ]
}
```

### 3.6. Regex язык (RegexLanguage)

```json
{
  "type": "object",
  "required": ["kind", "pattern", "has_backreferences"],
  "properties": {
    "kind": { "const": "regex" },
    "alphabet": { "type": "array", "items": { "type": "string" } },
    "pattern": { "type": "string" },
    "has_backreferences": { "type": "boolean" }
  }
}
```

### 3.7. Арифметический индексный язык

```json
{
  "type": "object",
  "required": ["kind", "symbol", "index_function"],
  "properties": {
    "kind": { "const": "arithmetic_index" },
    "symbol": { "type": "string", "description": "e.g. 'a'" },
    "index_function": {
      "type": "string",
      "description": "Math expression for the index: 'n/log(n)', 'n^2', '2^n'"
    }
  }
}
```

---

## 4. Спецификация модулей

### 4.1. Input Parser (LLM + pure fn)

**Вход:** Текст задачи на русском или формальная нотация.
**Выход:** JSON IR по schema из §3.
**Модель:** Sonnet 4.6 (temperature=0, structured output=JSON).
**Валидация:** Pure fn `validate_ir(json)` — проверка по JSON Schema.
**Retry:** Если невалидный JSON — повтор с ошибкой валидации в промпте (max 2 retry).

**Критические паттерны для парсера** (из анализа билетов):
- Кванторы: «∃v,u(...)» → ExistsDecomposition
- Палиндром: «vv^R» → concat_pattern: ["v", "rev(v)"]
- Модулярная арифметика: «|w| mod 3 = 0» → Modular
- Отрицание: «w₂ не начинается с w₁» → NegationStartsWith
- Грамматика: «S → ...» → GrammarLanguage
- Backreference: «\2» → RegexLanguage с has_backreferences=true
- Параметрический: «для каких ξ» → task_type: parametric_analysis

### 4.2. Hypothesis Module (pure fn)

**Вход:** JSON IR (LanguageSpec).
**Выход:**
```json
{
  "atoms": [
    {
      "description": "count_a == count_b",
      "memory_type": "infinite",
      "reason": "comparison of two unbounded quantities"
    },
    {
      "description": "length mod 3 == 0",
      "memory_type": "finite",
      "states_needed": 3,
      "reason": "modular arithmetic"
    }
  ],
  "hypothesis": "non_regular",
  "confidence": 0.85,
  "suggested_agents": ["pumping", "nerode", "closure"]
}
```

**Эвристики:**
- Comparison(count_symbol_a, count_symbol_b) → infinite (если символы разные)
- Modular(length, k, r) → finite, states = k
- Modular(count_symbol, k, r) → finite, states = k
- ExistsDecomposition с rev() → depends (нужен анализ: если сводится к фиксированной подстроке — finite)
- Grammar с правилами вида S → aSb → likely infinite (вложенная рекурсия)
- Grammar только с правыми рекурсиями (A → aB | b) → finite (правая линейная)

### 4.3. Classifier Agent (LLM)

**Вход:** JSON IR + hypothesis module output.

**Явные триггеры (hard rules, до LLM):**
- `has_backreferences=true` → `hypothesis: likely_non_regular`, `confidence: 0.7`, dispatch pumping + nerode + RE builder (на случай если backreference тривиален, как `(a)\1*` = `aa*`)
- Grammar с вложенной рекурсией (S→aSb) → `hypothesis: likely_non_regular`, `confidence: 0.8`
- Все атомы hypothesis module = finite → `hypothesis: regular`, `confidence: 0.9`

**Выход:**
```json
{
  "verdict": "regular",
  "confidence": 0.9,
  "reasoning": "All predicate atoms have finite memory. Total states ≤ 3×2 = 6.",
  "dispatch": {
    "re_builder": true,
    "dfa_builder": true,
    "pumping": false,
    "nerode": false,
    "closure": false
  }
}
```
**Модель:** Sonnet 4.6 (temperature=0).

### 4.4. RE Builder Agent (LLM)

**Вход:** JSON IR + classifier verdict + hypothesis analysis.
**Выход:**
```json
{
  "status": "success",
  "regex": "(aa|bb)(a|b)*|(a|b)*(aa|bb)",
  "explanation": "Слово начинается с палиндрома vv^R (|v|≥1) ⟺ начинается с aa или bb. Аналогично для суффикса.",
  "confidence": 0.95
}
```
**Модель:** Opus 4.6 (temperature=0.1). Требует глубокого рассуждения.

### 4.5. DFA Builder Agent (LLM + pure fn)

**Вход:** JSON IR + (опционально) regex от RE Builder.
**Выход:**
```json
{
  "status": "success",
  "dfa": {
    "states": ["q0", "q1", "q2", "q_dead"],
    "alphabet": ["a", "b"],
    "transitions": {
      "q0": {"a": "q1", "b": "q1"},
      "q1": {"a": "q2", "b": "q2"},
      "q2": {"a": "q2", "b": "q2"},
      "q_dead": {"a": "q_dead", "b": "q_dead"}
    },
    "start": "q0",
    "accept": ["q2"]
  },
  "explanation": "Состояния: q0=начало, q1=прочитан один символ, q2=прочитаны ≥2 символов (принимаем все). Достаточно видеть первые два символа."
}
```

**Pure fn:** `build_dfa_from_regex(regex)` — конструкция Томпсона + детерминизация + минимизация. Если regex доступен, LLM не нужен.

### 4.6. Pumping Agent (LLM)

**Вход:** JSON IR + hypothesis (verdict: non_regular).
**Выход:**
```json
{
  "status": "success",
  "proof": {
    "word_choice": {
      "word": "a^n b^n",
      "word_parameterized": true,
      "parameter": "n",
      "membership_argument": "Clearly a^n b^n ∈ L since count_a = count_b = n."
    },
    "length_argument": "|a^n b^n| = 2n ≥ n for n ≥ 1.",
    "cut_analysis": {
      "method": "exhaustive",
      "argument": "Since |xy| ≤ n, y consists entirely of a's. Let y = a^k, k ≥ 1.",
      "pumped_word": "a^(n+k) b^n for i=2",
      "contradiction": "count_a = n+k ≠ n = count_b, so xy²z ∉ L."
    },
    "pump_value": 2,
    "conclusion": "L is not regular by the Pumping Lemma."
  }
}
```
**Модель:** Opus 4.6 (temperature=0.1).

### 4.7. Nerode Agent (LLM + pure fn)

**Вход:** JSON IR + hypothesis.
**Выход:**
```json
{
  "status": "success",
  "proof": {
    "word_sequence": ["ε", "a", "aa", "aaa"],
    "distinguishing_contexts": [
      {"pair": ["ε", "a"], "context": "b", "why": "b ∈ L but ab ∉ L (depends on language)"},
      {"pair": ["a", "aa"], "context": "b", "why": "ab ∈ L but aab ∉ L (depends)"}
    ],
    "argument": "The words a^0, a^1, a^2, ... are pairwise distinguishable by contexts b^i. Hence ≡_L has infinite index.",
    "conclusion": "By Myhill-Nerode theorem, L is not regular."
  }
}
```

**Pure fn:** `compute_nerode_classes(oracle, alphabet, max_depth)` — строит классы Нероуда для коротких слов, проверяя различимость через oracle.

### 4.8. Closure Agent (LLM)

**Вход:** JSON IR + hypothesis.
**Выход:**
```json
{
  "status": "success",
  "method": "intersection_with_regular",
  "regular_language": {
    "regex": "a*b*",
    "justification": "L(a*b*) is regular."
  },
  "intersection_result": {
    "language": "{a^n b^n | n ≥ 0}",
    "is_regular": false,
    "proof_method": "known_non_regular"
  },
  "conclusion": "L ∩ a*b* = {a^n b^n} is not regular. Since a*b* is regular and REG is closed under intersection, L must be non-regular."
}
```

Также поддерживает:
- Гомоморфизм: `h: Σ* → Γ*`, в т.ч. затирающий (h(a) = ε)
  - **Алгоритм удаления символов:** для языка над Σ = {a, b, c}, применить h(c) = ε и проверить, сводится ли h(L) к известному нерегулярному языку (например, a^n b^n). Если h(L) нерегулярен, то L нерегулярен (REG замкнут относительно гомоморфизмов).
  - Пример: L = {a^n c^* b^n | n ≥ 0}, h(c) = ε → h(L) = {a^n b^n} — нерегулярен → L нерегулярен.
  - Ограничение: работает только когда язык содержит «лишний» символ, удаление которого обнажает нерегулярную структуру. Для палиндромов и многих грамматик — не применим.
- Обратный гомоморфизм
- Дополнение

**Модель:** Opus 4.6.

### 4.9. Grammar Analyzer (LLM + pure fn)

Отдельный модуль для Категории B (языки грамматик).

**Вход:** GrammarLanguage IR.
**Метод:**
1. Pure fn: проверить, является ли грамматика правой/левой линейной → автоматически REG
2. Pure fn: построить множество порождаемых слов до длины k (k=10-12)
3. LLM: проанализировать структуру рекурсии. Если вложенная (S→aSb) → likely non-REG. Если только хвостовая (S→aS|b) → REG.
4. LLM: попытаться свести к РВ, если REG.

**Выход:** Аналогичен classifier + RE builder / pumping.

### 4.10. Oracle Testing Module (pure fn)

**Вход:** JSON IR + ДКА (от DFA builder) или pumping proof.
**Компоненты:**

#### 4.10.1. Oracle Generator
```python
def oracle_from_ir(ir: dict) -> Callable[[str], bool]:
    """
    Compile IR predicate into a naive recognizer function.
    For existential quantifiers: enumerate all decompositions.
    For grammars: CYK parser or exhaustive derivation.
    """
```

Ограничения: oracle корректен, но медленный (экспоненциальный для ExistsDecomposition). Используется только для слов длины ≤ MAX_TEST_LENGTH (default: 12).

#### 4.10.2. Word Generator
```python
def generate_test_words(alphabet: list, strategies: list) -> list[str]:
    """
    Strategies:
    - 'exhaustive_k': all words up to length k (default k=8)
    - 'boundary': words of length p-1, p, p+1, 2p around pumping constant
    - 'nerode_targets': words from Nerode agent's distinguishing set
    - 'random_long': random words of length 20-50 for smoke test
    """
```

#### 4.10.3. DFA Runner
```python
def run_dfa(dfa: dict, word: str) -> bool:
    """Run word through DFA transition table. O(|w|)."""
```

#### 4.10.4. Oracle Test
```python
def oracle_test(ir: dict, dfa: dict, n_words: int = 1000) -> dict:
    """
    Returns:
    {
        "status": "pass" | "fail",
        "tested": 1000,
        "passed": 1000,
        "counterexample": null | {
            "word": "aabba",
            "oracle_says": true,
            "automaton_says": false
        }
    }
    """
```

#### 4.10.5. Differential Testing (Counter-example Mining)
```python
def differential_test(dfa1: dict, dfa2: dict, alphabet: list, max_len: int = 10) -> dict:
    """
    Compare two automata/regex on ALL words up to max_len.
    Used when RE Builder and DFA Builder produce different results,
    or when two specialist agents disagree.
    First divergence = counterexample for retry.
    
    Returns:
    {
        "status": "identical" | "diverge",
        "tested": 2046,
        "first_divergence": null | {
            "word": "abba",
            "dfa1_says": true,
            "dfa2_says": false
        },
        "total_divergences": 0 | 17
    }
    """
```
Стратегия: если два агента выдали результат (оба claim success), differential test — первый шаг перед reasoning agent. Расхождение = конкретный фидбек для retry без участия LLM.

### 4.11. Reasoning Agent (LLM)

**Вход:** Outputs от всех запущенных specialist agents + oracle test results.
**Выход:**
```json
{
  "verdict": "non_regular",
  "confidence": 0.98,
  "best_proof": "pumping",
  "consolidated_proof": "...(unified proof text)...",
  "oracle_validation": "pass (1024/1024 words tested)",
  "issues_found": [],
  "action": "proceed_to_formalizer"
}
```

**Возможные actions:**
- `proceed_to_formalizer` — всё ок, идём к формализации
- `retry_enriched` — несогласованность, повторить специалистов с контекстом
- `invert_hypothesis` — классификатор ошибся, инвертировать REG/non-REG
- `escalate` — max retries exceeded, передать человеку

**Модель:** Opus 4.6 (temperature=0).
**Max retries:** 3 на уровне reasoning → specialist, 2 на уровне reasoning → formalizer.

### 4.12. Formalizer Agent (LLM — template-based)

**Вход:** Consolidated proof от Reasoning Agent + proof template.
**Выход:** Lean 4 proof term (заполненный template).

**Proof templates (Lean 4):**

#### Template 1: DFA correctness (REG proof)
```lean
-- Template: prove_regular_via_dfa.lean
import Mathlib.Computability.DFA

-- FILL: alphabet type
abbrev Alpha := Fin 2  -- {0=a, 1=b}

-- FILL: state type
abbrev State := Fin «NUM_STATES»

-- FILL: transition function
def delta : State → Alpha → State
  | «STATE_TRANSITIONS»

-- FILL: start state
def q0 : State := «START»

-- FILL: accept states
def isAccept : State → Bool
  | «ACCEPT_STATES»

def myDFA : DFA Alpha State := {
  step := delta,
  start := q0,
  accept := fun q => isAccept q
}

-- FILL: correctness proof (invariant)
theorem dfa_correct (w : List Alpha) :
    myDFA.accepts w ↔ «LANGUAGE_PREDICATE» w := by
  sorry -- FILL: tactic proof
```

#### Template 2: Pumping Lemma (non-REG proof)
```lean
-- Template: prove_non_regular_via_pumping.lean
import Mathlib.Computability.RegularExpressions

theorem not_regular :
    ¬ ∃ (n : ℕ) (A : DFA Alpha (Fin n)), ∀ w, A.accepts w ↔ «PREDICATE» w := by
  intro ⟨n, A, hA⟩
  -- By pumping lemma for A (n states)
  -- FILL: word choice
  let s := «WORD_CHOICE»
  -- FILL: membership proof
  have hs : «PREDICATE» s := «MEMBERSHIP_PROOF»
  -- FILL: length bound
  have hlen : s.length ≥ n := «LENGTH_PROOF»
  -- FILL: case analysis on xyz decomposition
  sorry -- FILL
```

#### Template 3: Myhill-Nerode (non-REG proof)
```lean
-- Template: prove_non_regular_via_nerode.lean

-- FILL: infinite sequence of pairwise distinguishable words
def words : ℕ → List Alpha
  | n => «WORD_FUNCTION»

-- FILL: distinguishing contexts
def contexts : ℕ → ℕ → List Alpha
  | i, j => «CONTEXT_FUNCTION»

-- FILL: proof that words are pairwise distinguishable
theorem pairwise_distinct (i j : ℕ) (hij : i ≠ j) :
    ∃ z, «PREDICATE» (words i ++ z) ≠ «PREDICATE» (words j ++ z) := by
  exact ⟨contexts i j, «DISTINGUISHING_PROOF»⟩

-- Therefore the Nerode equivalence has infinite index
theorem not_regular : ¬ IsRegular «LANGUAGE» := by
  sorry -- FILL: from pairwise_distinct
```

### 4.13. Type Checker (pure fn)

**Вход:** Lean 4 файл (заполненный template).
**Метод:** `lean <file.lean>` в Docker-контейнере.
**Выход:**
```json
{
  "status": "valid" | "invalid" | "timeout",
  "errors": ["line 42: type mismatch..."] | null,
  "time_seconds": 3.2
}
```

**Docker image:** `debian:stable-slim` + elan + lean 4.x + mathlib cache.
**Timeout:** 120 секунд.
**Memory limit:** 2GB.

---

## 5. Протокол взаимодействия модулей

### 5.1. Основной flow (happy path)

```
User Input
  → [Input Parser] → IR (JSON)
  → [Hypothesis Module] → atoms + hypothesis
  → [Classifier] → verdict + dispatch
  → [Specialist Agents] (parallel subset based on dispatch)
      ├── RE Builder → regex
      ├── DFA Builder → DFA
      ├── Pumping Agent → pumping proof
      ├── Nerode Agent → nerode proof
      └── Closure Agent → closure argument
  → [Oracle Test] (DFA vs oracle, if DFA available)
  → [Reasoning Agent] → consolidated proof
  → [Formalizer] → Lean 4 proof term
  → [Type Checker] → valid/invalid
  → Output: verified proof + human-readable explanation
```

### 5.2. Retry protocol

**Level 1: Oracle test failure**
```
Oracle test fails (counterexample found)
  → Reasoning Agent receives counterexample
  → Enriched retry to DFA Builder / RE Builder
     with counterexample word attached
  → Max 2 retries
```

**Level 2: Specialist disagreement**
```
Reasoning Agent detects inconsistency
  (e.g., RE Builder says REG, Nerode says non-REG)
  → Run pure fn: test RE Builder's DFA against Nerode's distinguishing words
  → Send failing evidence to the incorrect agent
  → Max 2 retries
```

**Level 3: Hypothesis inversion**
```
Both specialists fail in same direction
  (e.g., both cannot build DFA)
  → Reasoning Agent inverts hypothesis (REG → non-REG)
  → Re-dispatch to opposite set of agents
  → Include evidence from failed attempt
  → Max 1 inversion
```

**Level 4: Formalizer failure**
```
Type checker returns invalid
  → Error message sent to Reasoning Agent
  → Reasoning Agent decides:
     a) Retry formalizer with error context
     b) Revise proof and re-formalize
  → Max 2 retries on formalizer
```

**Level 5: Escalation**
```
Total retries exceeded
  → Output partial result with confidence score
  → Flag for human review
```

### 5.3. Structured output contract

Каждый модуль возвращает JSON со следующими обязательными полями:
```json
{
  "module": "string (module name)",
  "status": "success | failure | timeout | partial",
  "evidence": { ... },
  "confidence": 0.0-1.0,
  "errors": [] | null
}
```

---

## 6. Deployment

### 6.1. Claude Code orchestration

```
claude-code-session/
├── prompts/                    # Externalized prompts (BP 5)
│   ├── input_parser.md
│   ├── classifier.md
│   ├── re_builder.md
│   ├── dfa_builder.md
│   ├── pumping_agent.md
│   ├── nerode_agent.md
│   ├── closure_agent.md
│   ├── grammar_analyzer.md
│   ├── reasoning_agent.md
│   └── formalizer.md
├── lib/                        # Pure functions (Python)
│   ├── ir_schema.py            # JSON Schema validation
│   ├── hypothesis_module.py    # Predicate analysis
│   ├── oracle.py               # Oracle from IR
│   ├── word_generator.py       # Test word generation
│   ├── dfa_runner.py           # DFA simulation
│   ├── dfa_builder.py          # Thompson + determinize + minimize
│   ├── congruence.py           # Nerode/syntactic congruence (guarded)
│   ├── oracle_test.py          # Oracle vs DFA testing
│   ├── grammar_utils.py        # CYK parser, linearity check
│   └── type_check.py           # Lean 4 invocation wrapper
├── templates/                  # Lean 4 proof templates
│   ├── prove_regular_via_dfa.lean
│   ├── prove_non_regular_via_pumping.lean
│   ├── prove_non_regular_via_nerode.lean
│   └── lib/                    # Shared Lean definitions
│       └── RegLang.lean        # Language predicates, DFA defs
├── docker/
│   └── Dockerfile.lean4        # Lean 4 environment
├── orchestrator.py             # Main pipeline script
└── README.md
```

### 6.2. Lean 4 Docker

```dockerfile
FROM debian:stable-slim
RUN apt-get update && apt-get install -y curl git && apt-get clean
RUN useradd -m lean
USER lean
WORKDIR /home/lean
ENV PATH="/home/lean/.elan/bin:$PATH"
RUN curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --default-toolchain none
RUN elan default leanprover/lean4:v4.18.0
RUN lake --version && lean --version
```

### 6.3. Resource estimates (per task)

| Module | Type | LLM tokens | Compute time |
|--------|------|------------|-------------|
| Input Parser | LLM (Sonnet) | ~1-2K | 1-2s |
| Hypothesis | Pure fn | 0 | <0.1s |
| Classifier | LLM (Sonnet) | ~1-2K | 1-2s |
| RE Builder | LLM (Opus) | ~2-4K | 3-5s |
| DFA Builder | LLM + pure fn | ~1-3K | 2-5s |
| Pumping Agent | LLM (Opus) | ~3-5K | 5-8s |
| Nerode Agent | LLM + pure fn | ~2-4K | 3-6s |
| Closure Agent | LLM (Opus) | ~2-4K | 3-5s |
| Oracle Test | Pure fn | 0 | 1-10s |
| Reasoning Agent | LLM (Opus) | ~3-6K | 5-10s |
| Formalizer | LLM (Opus) | ~3-8K | 5-15s |
| Type Checker | Pure fn (Docker) | 0 | 5-60s |
| **Total (happy path)** | | **~20-40K** | **~30-120s** |
| **Total (with retries)** | | **~40-80K** | **~60-300s** |

### 6.4. Claude Code Max plan capacity

- Max 5x ($100/мес): ~88K tokens / 5hr window → 2-4 tasks per window
- Max 20x ($200/мес): ~220K tokens / 5hr window → 5-10 tasks per window
- Pure fn compute: бесплатно, не расходует токены
- Lean type checking: бесплатно (Docker, local compute)

---

## 7. Приоритет реализации

### Phase 1: Core (MVP)
1. `lib/ir_schema.py` — JSON Schema + валидация
2. `lib/oracle.py` — oracle from IR для категорий A, B
3. `lib/word_generator.py` — генерация тестовых слов
4. `lib/dfa_runner.py` — прогон ДКА
5. `lib/oracle_test.py` — oracle vs DFA
6. `prompts/input_parser.md` — промпт для парсера
7. `orchestrator.py` — минимальный pipeline (parse → classify → build → test)

### Phase 2: Specialist agents
8. `prompts/` — все промпты для специалистов
9. `lib/hypothesis_module.py` — анализ предикатов
10. `lib/dfa_builder.py` — конструкция Томпсона
11. `lib/congruence.py` — guarded Nerode computation
12. `lib/grammar_utils.py` — CYK, linearity check

### Phase 3: Formalization
13. `templates/` — Lean 4 proof templates
14. `docker/Dockerfile.lean4` — контейнер
15. `lib/type_check.py` — обёртка над lean
16. `prompts/formalizer.md` — промпт для формализатора

### Phase 4: Hardening
17. Retry logic в orchestrator
18. Structured logging / tracing
19. Evaluation на полном наборе билетов ИУ-9
20. Расширение IR schema для CFL