# ТЗ: Агентская система для анализа детерминированности КС-языков

## 0. Контекст

Данная система — третий компонент в линейке агентских систем для задач ТФЯ:

- **REG-система** (`agent_system/`) — анализ регулярности. Реализована, live-тестирована.
- **CFL-система** (`cfl_system/`) — анализ КС-свойства. Архитектура зафиксирована.
- **DCFL-система** (`dcfl_system/`) — анализ детерминированности КС-языка. **Данное ТЗ.**

Вопрос задачи: «является ли данный КС-язык детерминированным (DCFL)?»

Ключевая теорема: **LR(k)-грамматики порождают ровно класс DCFL.**
Для всякого DCFL существует SLR(1)-грамматика (следствие).

**Папка:** `dcfl_system/` (рядом с `agent_system/` и `cfl_system/`, не внутри).

**Зависимости от REG/CFL-систем:**
- Импорт `agent_system.lib.ir_schema` (базовая IR schema) — допустим
- Импорт `agent_system.lib.oracle` (oracle framework) — допустим
- Импорт `agent_system.lib.word_generator` (генератор слов) — допустим
- Импорт `cfl_system.lib.cyk_oracle` (CYK membership) — допустим
- **НЕ МОДИФИЦИРОВАТЬ** файлы в `agent_system/` и `cfl_system/`

**Фреймворк:** LangGraph (как REG и CFL системы).

**Формальная верификация (Lean/Coq): НЕ реализуется.**
В текущей версии и в ближайших планах отсутствует проверка доказательств
через системы формальных доказательств (Lean 4, Coq и т.п.).
Доказательства верифицируются только через oracle-тесты (CYK, word sampling,
подстановка контрпримеров) и LLM-based reasoning. Формализация в Lean —
потенциальное расширение на будущее, но не входит в scope данного ТЗ.

---

## 1. Типы задач

### 1.1. Формат 1 — язык задан множеством (set-builder)

```
{wvaav^R w^R | w ∈ (aa*b)*a & v ∈ b(ab|aa)*}
{w₁w₂ | w₁ = u₁au₂ & w₂ = u₃au₄ & |u₁| ≤ |u₂| & |u₃| ≥ |u₄|}
{aⁿb*(cⁿ|bⁿ)ac* | n > 0}
```

Задача: определить, является ли L детерминированным КС-языком (DCFL), и доказать.

### 1.2. Формат 2 — язык задан грамматикой

```
S → aSSb | ba | Ab, A → aAb | a
S → aSa | aA, A → S | aB, B → bb | AbS
S → SaS | bS | T, T → bbT | ab
```

Задача: определить, является ли L(G) детерминированным, и доказать.
**Примечание:** грамматика может быть неоднозначной, но язык — детерминированным
(если существует другая, однозначная грамматика для того же языка).

### 1.3. Типы задач по частоте (из ~70 билетов ИУ9)

| Тип | Частота | Типичный вердикт |
|-----|---------|------------------|
| Условия на длины частей (`\|w₁\| ≤ \|w₂\|`) | очень часто | обычно DCFL (один счётчик в стеке) |
| w/w^R пары (палиндромные конструкции) | часто | одна пара → DCFL; вложенные → зависит от разделителей |
| aⁿbᵏaᵐbʳ (арифметические связи) | часто | одно условие → DCFL; дизъюнкция → часто не DCFL |
| Грамматика → детерминированность | часто | анализ языка, не грамматики |
| Регулярные компоненты + счётчик | средне | DCFL (рег. ограничения → конечная память) |
| LL-свойство | часто | подтип DCFL |

---

## 2. IR Schema

### 2.1. Расширение базовой IR

DCFL-система расширяет `agent_system.lib.ir_schema.LanguageIR`.

```python
@dataclass
class DCFLTaskIR:
    """Внутреннее представление задачи на детерминированность."""
    task_id: str
    source_text: str                    # Исходный текст задачи
    input_format: Literal["set_builder", "grammar"]
    language_spec: LanguageSpec         # Описание языка (§2.2)
    alphabet: set[str]                  # Алфавит
    hypothesis: HypothesisResult | None # Результат hypothesis module
    classifier_hint: str | None         # "dcfl" | "non_dcfl" | "uncertain"
    preprocess: PreprocessResult | None # Результат preprocess
```

### 2.2. LanguageSpec

```python
@dataclass
class SetBuilderSpec:
    """Формат 1: язык задан множеством."""
    word_pattern: str           # Шаблон слова: "wvaav^Rw^R"
    variables: list[Variable]   # Переменные w, v, u₁...
    constraints: list[Constraint]  # Ограничения на переменные

@dataclass
class Variable:
    name: str                   # "w", "v", "u₁"
    domain: str | None          # Регулярное ограничение: "(aa*b)*a" или None
    quantifier: str             # "forall" (default) | "exists"

@dataclass
class Constraint:
    kind: str                   # "length_cmp" | "regex_member" | "equal" | "reverse"
    args: dict                  # Зависит от kind

@dataclass
class GrammarSpec:
    """Формат 2: язык задан грамматикой."""
    terminals: list[str]
    nonterminals: list[str]
    start: str
    rules: list[ProductionRule]

@dataclass
class ProductionRule:
    lhs: str                    # Нетерминал
    rhs: list[str]              # Правая часть (список символов)
```

### 2.3. Пример IR (Формат 1)

```json
{
  "task_id": "dcfl_exam_01",
  "source_text": "Проверить язык на детерминизм: {wvaav^Rw^R | w ∈ (aa*b)*a & v ∈ b(ab|aa)*}",
  "input_format": "set_builder",
  "language_spec": {
    "kind": "set_builder",
    "word_pattern": "wvaav^Rw^R",
    "variables": [
      {"name": "w", "domain": "(aa*b)*a", "quantifier": "forall"},
      {"name": "v", "domain": "b(ab|aa)*", "quantifier": "forall"}
    ],
    "constraints": []
  },
  "alphabet": ["a", "b"]
}
```

### 2.4. Пример IR (Формат 1, условия на длины)

```json
{
  "task_id": "dcfl_exam_02",
  "source_text": "Исследовать на детерминированность: {w₁w₂ | w₁ = u₁au₂ & w₂ = u₃au₄ & |u₁| ≤ |u₂| & |u₃| ≥ |u₄|}",
  "input_format": "set_builder",
  "language_spec": {
    "kind": "set_builder",
    "word_pattern": "u₁au₂u₃au₄",
    "variables": [
      {"name": "u₁", "domain": null, "quantifier": "forall"},
      {"name": "u₂", "domain": null, "quantifier": "forall"},
      {"name": "u₃", "domain": null, "quantifier": "forall"},
      {"name": "u₄", "domain": null, "quantifier": "forall"}
    ],
    "constraints": [
      {"kind": "length_cmp", "args": {"left": "u₁", "op": "<=", "right": "u₂"}},
      {"kind": "length_cmp", "args": {"left": "u₃", "op": ">=", "right": "u₄"}}
    ]
  },
  "alphabet": ["a", "b"]
}
```

### 2.5. Пример IR (Формат 1, дизъюнкция)

```json
{
  "task_id": "dcfl_exam_03",
  "source_text": "Является ли данный язык детерминированным? {aⁿb*(cⁿ|bⁿ)ac* | n > 0}",
  "input_format": "set_builder",
  "language_spec": {
    "kind": "set_builder",
    "word_pattern": "aⁿ b* (cⁿ|bⁿ) a c*",
    "variables": [
      {"name": "n", "domain": null, "quantifier": "forall"}
    ],
    "constraints": [
      {"kind": "integer_cmp", "args": {"left": "n", "op": ">", "right": 0}},
      {"kind": "disjunction", "args": {
        "branches": ["cⁿ", "bⁿ"],
        "shared_var": "n"
      }}
    ]
  },
  "alphabet": ["a", "b", "c"]
}
```

### 2.6. Пример IR (Формат 2)

```json
{
  "task_id": "dcfl_exam_04",
  "source_text": "S → aSSb | ba | Ab, A → aAb | a",
  "input_format": "grammar",
  "language_spec": {
    "kind": "grammar",
    "terminals": ["a", "b"],
    "nonterminals": ["S", "A"],
    "start": "S",
    "rules": [
      {"lhs": "S", "rhs": ["a", "S", "S", "b"]},
      {"lhs": "S", "rhs": ["b", "a"]},
      {"lhs": "S", "rhs": ["A", "b"]},
      {"lhs": "A", "rhs": ["a", "A", "b"]},
      {"lhs": "A", "rhs": ["a"]}
    ]
  },
  "alphabet": ["a", "b"]
}
```

---

## 3. Pipeline (LangGraph)

### 3.1. Граф

```
__start__
    │
    ▼
input_parser_node          ──── fail ───→ early_failure → __end__
    │ ok
    ▼
hypothesis_module_node
    │
    ▼
run_classifier_node        ◄── advisory only (dashed border)
    │
    ▼
preprocess_node            ──── pattern_db + closure_scan + sampling
    │
    ▼
dispatch_all_agents        ──── всегда все 5 агентов
    │
    ├───────────────────┐
    ▼                   ▼
┌─ Prove DCFL ──┐  ┌─ Prove non-DCFL ──┐
│ stack_strategy │  │ dcfl_pumping      │
│ closure_reduc. │  │ shallit           │
└────────────────┘  │ inh_ambiguity     │
                    └───────────────────┘
    │                   │
    └────────┬──────────┘
             ▼
    oracle_verification_node
             │
             ▼
    reasoning_agent_node ────── retry? ──→ retry_planner_node ──→ dispatch
             │ done
             ▼
    renderer_node
             │
             ▼
         __end__
```

### 3.2. Три архитектурных принципа

**Принцип 1: Classifier is advisory only.**
`run_classifier_node` НЕ управляет dispatch. Все 5 агентов запускаются всегда.
Classifier hint передаётся в `reasoning_agent_node` как один из сигналов.
Урок из REG-системы: classifier ошибался на ~20% задач; gating вызывал
пропуск правильного доказательства.

**Принцип 2: All agents always dispatch.**
`dispatch_all_agents` запускает все 5 specialist agents параллельно.
Не зависит от classifier, hypothesis, или preprocess.
Агенты, для которых задача нерелевантна, быстро возвращают `status: "not_applicable"`.

**Принцип 3: Selective retry.**
`retry_planner_node` анализирует результаты и перезапускает ТОЛЬКО
конкретные агенты с модифицированными параметрами. Не перезапускает всех.
Пример: reasoning agent решает, что `dcfl_pumping` был близок к успеху,
но выбрал неудачную пару слов → retry_planner перезапускает только
`dcfl_pumping` с подсказкой «попробовать семейство a^n b^n c^n вместо...».

### 3.3. LangGraph: реализация графа

**State schema:**
```python
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END

class DCFLState(TypedDict):
    # Input
    source_text: str
    task_ir: DCFLTaskIR | None

    # Preprocessing
    hypothesis: HypothesisResult | None
    classifier_hint: ClassifierResult | None
    preprocess: PreprocessResult | None

    # Agent results
    agent_results: dict[str, AgentOutput]   # agent_name → output

    # Verification
    oracle_verification: dict | None

    # Reasoning
    reasoning: ReasoningResult | None
    retry_count: int
    retry_plan: RetryPlan | None

    # Output
    solution: DCFLSolutionOutput | None
    error: str | None
```

**Граф:**
```python
graph = StateGraph(DCFLState)

# Nodes
graph.add_node("input_parser", input_parser_node)
graph.add_node("hypothesis_module", hypothesis_module_node)
graph.add_node("classifier", run_classifier_node)
graph.add_node("preprocess", preprocess_node)
graph.add_node("dispatch_agents", dispatch_all_agents_node)
graph.add_node("oracle_verify", oracle_verification_node)
graph.add_node("reasoning", reasoning_agent_node)
graph.add_node("retry_planner", retry_planner_node)
graph.add_node("renderer", renderer_node)
graph.add_node("early_failure", early_failure_node)

# Edges: linear pipeline
graph.set_entry_point("input_parser")
graph.add_conditional_edges("input_parser", route_after_parse,
    {"ok": "hypothesis_module", "fail": "early_failure"})
graph.add_edge("hypothesis_module", "classifier")
graph.add_edge("classifier", "preprocess")
graph.add_edge("preprocess", "dispatch_agents")
graph.add_edge("dispatch_agents", "oracle_verify")
graph.add_edge("oracle_verify", "reasoning")

# Conditional: retry or finish
graph.add_conditional_edges("reasoning", route_after_reasoning,
    {"done": "renderer", "retry": "retry_planner"})
graph.add_edge("retry_planner", "dispatch_agents")  # loop back

# Terminals
graph.add_edge("renderer", END)
graph.add_edge("early_failure", END)
```

**dispatch_all_agents_node** запускает 5 агентов параллельно:
```python
async def dispatch_all_agents_node(state: DCFLState) -> DCFLState:
    agents = [
        stack_strategy_agent,
        closure_reduction_agent,
        dcfl_pumping_agent,
        shallit_agent,
        inh_ambiguity_agent,
    ]
    agent_input = AgentInput(
        task=state["task_ir"],
        hypothesis=state["hypothesis"],
        preprocess=state["preprocess"],
        retry_hint=state["retry_plan"].hints.get(a.name) if state["retry_plan"] else None,
    )
    # Параллельный запуск через asyncio.gather или LangGraph parallel
    results = await asyncio.gather(*[a.run(agent_input) for a in agents])
    return {"agent_results": {r.agent_name: r for r in results}}
```

**route_after_reasoning** управляет retry:
```python
def route_after_reasoning(state: DCFLState) -> str:
    r = state["reasoning"]
    if r.needs_retry and state["retry_count"] < 2:
        return "retry"
    return "done"
```

---

## 4. Модули

### 4.1. input_parser_node

**Модель:** Sonnet (лёгкая задача)

**Вход:** Сырой текст задачи (из фото OCR или текстовый ввод).

**Выход:** `DCFLTaskIR` (§2.1).

**Логика:**
1. Определить формат: set-builder vs грамматика
2. Извлечь переменные, ограничения, алфавит
3. Для грамматик: парсинг продукций в структурированный формат
4. Валидация IR: все переменные использованы, алфавит непустой, формат корректен
5. При ошибке парсинга: `status: "parse_error"` → early_failure

**Критические правила парсинга:**
- `w^R` — реверс переменной w, НЕ степень
- `v ∈ b(ab|aa)*` — регулярное ограничение на переменную v
- `|u₁| ≤ |u₂|` — ограничение на длины
- `cⁿ|bⁿ` — дизъюнкция с общей переменной n
- `n > 0` — ограничение на параметр

### 4.2. hypothesis_module_node

**Модель:** Pure function (без LLM)

**Вход:** `DCFLTaskIR`

**Выход:** `HypothesisResult`

```python
@dataclass
class HypothesisResult:
    pattern_type: str           # Тип паттерна (§4.2.1)
    memory_analysis: str        # "single_stack" | "nested_stack" | "two_stacks" | "unknown"
    separator_analysis: str     # "clear_separator" | "no_separator" | "ambiguous"
    disjunction_present: bool   # Есть ли дизъюнкция с общей переменной
    regex_constrained_vars: list[str]  # Переменные с регулярными ограничениями
    prediction: str             # "likely_dcfl" | "likely_non_dcfl" | "uncertain"
    confidence: float           # 0.0–1.0
    reasoning: str              # Человеко-читаемое обоснование
```

**4.2.1. Классификация паттернов:**

| Паттерн | Код | Предсказание |
|---------|-----|--------------|
| Одна пара w/w^R с разделителем | `single_palindrome_sep` | likely_dcfl |
| Одна пара w/w^R без разделителя | `single_palindrome_nosep` | likely_non_dcfl |
| Две последовательные пары w/w^R, v/v^R | `sequential_palindromes` | likely_dcfl |
| Две вложенные пары wvv^Rw^R | `nested_palindromes` | зависит от разделителя |
| Одно условие на длины | `single_length_cmp` | likely_dcfl |
| Два условия на длины | `dual_length_cmp` | зависит от совместимости |
| Дизъюнкция с общей переменной (n=j ∨ j=k) | `shared_var_disjunction` | likely_non_dcfl |
| Грамматика (Format 2) | `grammar_analysis` | uncertain |

**4.2.2. Анализ памяти:**

- `single_stack`: Одна фаза push + одна фаза pop. Пример: `{aⁿbⁿ}`.
- `nested_stack`: Вложенные push/pop с разделителем. Пример: `{wvv^Rw^R}`.
- `two_stacks`: Требуются два независимых стека → скорее не DCFL.
- `unknown`: Не удалось определить.

### 4.3. run_classifier_node

**Модель:** Sonnet

**Вход:** `DCFLTaskIR` + `HypothesisResult`

**Выход:**
```python
@dataclass
class ClassifierResult:
    verdict: str    # "dcfl" | "non_dcfl" | "uncertain"
    confidence: float
    reasoning: str
    suggested_methods: list[str]  # ["stack_strategy", "dcfl_pumping", ...]
```

**ADVISORY ONLY.** Результат не влияет на dispatch. Передаётся в reasoning agent.

### 4.4. preprocess_node

**Модель:** Pure function (без LLM)

**Вход:** `DCFLTaskIR` + `HypothesisResult`

**Выход:** `PreprocessResult`

```python
@dataclass
class PreprocessResult:
    pattern_db_matches: list[PatternMatch]  # Совпадения с базой паттернов
    closure_scan: ClosureScanResult         # Результат анализа по Table 1
    sample_words: list[SampleWord]          # Сгенерированные слова для тестов
    complement_structure: str | None        # Описание структуры дополнения (если простое)
```

**4.4.1. pattern_db — база паттернов**

Содержит ~20 паттернов из документа «Типы задач на детерминированность» (§1.3):

```python
PATTERN_DB = [
    {
        "pattern": "single_palindrome_with_separator",
        "description": "w c w^R с разделителем c",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{wcw^R | w ∈ {a,b}*}"
    },
    {
        "pattern": "shared_var_disjunction",
        "description": "aⁿ...f(n)...g(n) с дизъюнкцией f(n)|g(n)",
        "verdict": "non_dcfl",
        "method": "inh_ambiguity",
        "example": "{aⁱbʲcᵏ | i=j ∨ j=k}"
    },
    # ... ещё ~18 паттернов
]
```

**4.4.2. closure_scan — анализ по Table 1**

Проверяет, получен ли язык через операции, по которым DCFL (не) замкнут:

| Операция | DCFL замкнут? | Что это значит |
|----------|---------------|----------------|
| ~ (дополнение) | **ДА** | complement(L) DCFL ⟺ L DCFL |
| h⁻¹ (обратный гомоморфизм) | **ДА** | L = h⁻¹(L') и L' DCFL ⟹ L DCFL |
| ∩ REG | **ДА** | L = L' ∩ R и L' DCFL ⟹ L DCFL |
| ∪ (объединение) | **НЕТ** | L = L₁ ∪ L₂ — ничего не следует |
| · (конкатенация) | **НЕТ** | L = L₁·L₂ — ничего не следует |
| * (звезда Клини) | **НЕТ** | L = L₁* — ничего не следует |
| R (реверс) | **НЕТ** | L = L₁^R — ничего не следует |
| h (гомоморфизм) | **НЕТ** | L = h(L₁) — ничего не следует |

**4.4.3. sample_words — генерация слов**

Для задач с грамматикой (Format 2): CYK-based генерация слов длины 1..20.
Для задач с множеством (Format 1): подстановка конкретных значений переменных.

Цель: дать агентам конкретные примеры слов из языка для рассуждений.

---

## 5. Specialist Agents

### 5.1. Общий контракт

Каждый агент получает на вход:
```python
@dataclass
class AgentInput:
    task: DCFLTaskIR
    hypothesis: HypothesisResult
    preprocess: PreprocessResult
    retry_hint: str | None      # Подсказка от retry_planner (при повторном запуске)
```

Каждый агент выдаёт:
```python
@dataclass
class AgentOutput:
    agent_name: str
    status: str                 # "success" | "fail" | "not_applicable" | "uncertain"
    verdict: str | None         # "dcfl" | "non_dcfl" | None
    proof_sketch: ProofSketch | None
    evidence: list[str]         # Человеко-читаемые шаги рассуждения
    confidence: float           # 0.0–1.0
    errors: list[str]           # Ошибки, если были
```

### 5.2. stack_strategy (конструктивный)

**Модель:** Opus

**Цель:** Доказать, что язык DCFL, рассуждая о стратегии стека.

**НЕ строит DPDA формально.** Рассуждает на уровне:
- Сколько фаз push/pop у стека?
- Есть ли чёткий разделитель между фазами?
- Регулярные ограничения на компоненты → конечная память (состояния DPDA)
- Где точка переключения с push на pop? Можно ли её определить детерминированно?

**Формат proof_sketch:**
```python
@dataclass
class StackStrategyProof:
    kind: Literal["stack_strategy"]
    phases: list[StackPhase]        # Фазы работы стека
    separator: str | None           # Разделитель между фазами
    finite_control: str             # Описание конечной памяти
    determinism_argument: str       # Почему выбор перехода однозначен
    regex_in_states: list[str]      # Какие regex-ограничения → состояния
```

```python
@dataclass
class StackPhase:
    name: str                       # "push_w", "compare_v^R", ...
    action: str                     # "push" | "pop" | "compare" | "skip"
    what: str                       # Что кладём/снимаем/сравниваем
    trigger: str                    # Что переключает на следующую фазу
```

**Пример для {wvaav^Rw^R | w ∈ (aa*b)*a, v ∈ b(ab|aa)*}:**
```json
{
  "kind": "stack_strategy",
  "phases": [
    {"name": "push_w", "action": "push", "what": "символы w", "trigger": "конец w (определяется по regex (aa*b)*a)"},
    {"name": "push_v", "action": "push", "what": "символы v поверх w", "trigger": "встретили 'aa' — разделитель"},
    {"name": "pop_v^R", "action": "compare", "what": "сравниваем с v^R из стека", "trigger": "v закончился (стек дошёл до маркера w)"},
    {"name": "pop_w^R", "action": "compare", "what": "сравниваем с w^R из стека", "trigger": "стек пуст"}
  ],
  "separator": "aa (разделяет v и v^R, чётко маркирует переход)",
  "finite_control": "Состояния DPDA отслеживают позицию в регулярных выражениях (aa*b)*a и b(ab|aa)* — конечное число состояний",
  "determinism_argument": "Переключение push_w→push_v детерминировано: w ∈ (aa*b)*a заканчивается на 'a', за которым идёт символ из v ∈ b(ab|aa)*, т.е. 'b'. Это однозначно определяет точку переключения.",
  "regex_in_states": ["(aa*b)*a → 3 состояния ДКА", "b(ab|aa)* → 4 состояния ДКА"]
}
```

### 5.3. closure_reduction (конструктивный + деструктивный)

**Модель:** Opus

**Цель:** Доказать DCFL или non-DCFL через замкнутые операции из Table 1.

**Три инструмента в одном агенте:**

**Инструмент 1: Дополнение (~)**
- Конструктивно: если complement(L) проще и доказуемо DCFL → L тоже DCFL.
- Деструктивно: если complement(L) не КС → L не DCFL.

**Инструмент 2: Обратный гомоморфизм (h⁻¹)**
- Конструктивно: L = h⁻¹(L') для известного DCFL L' → L тоже DCFL.

**Инструмент 3: Пересечение с регулярным (∩ REG)**
- Конструктивно: L = L' ∩ R для DCFL L' и регулярного R → L DCFL.
- Деструктивно: L ∩ R не КС для некоторого регулярного R → L не DCFL.
  (Потому что DCFL ∩ REG ⊆ DCFL ⊆ CFL.)

**Формат proof_sketch:**
```python
@dataclass
class ClosureReductionProof:
    kind: Literal["closure_reduction"]
    operation: str              # "complement" | "inv_homomorphism" | "reg_intersection"
    direction: str              # "constructive" | "destructive"
    source_language: str        # Описание L' (известного DCFL или не-CFL)
    transformation: str         # Описание операции
    result_argument: str        # Почему результат DCFL/не-CFL
```

### 5.4. dcfl_pumping (деструктивный)

**Модель:** Opus

**Цель:** Доказать, что язык НЕ DCFL, используя лемму о накачке для DCFL.

**Формулировка леммы (отрицание):**

Для любой длины накачки p найти два слова w = xy, w' = xz, где:
- |x| > p
- первые буквы y и z совпадают
- НЕ существует разбиения x = x₁x₂x₃, y = y₁y₂y₃, z = z₁z₂z₃ с
  |x₂x₃| ≤ p, |x₂| > 0, такого что
  ∀i: x₁x₂ⁱx₃y₁y₂ⁱy₃ ∈ L И x₁x₂ⁱx₃z₁z₂ⁱz₃ ∈ L

**Формат proof_sketch:**
```python
@dataclass
class DCFLPumpingProof:
    kind: Literal["dcfl_pumping"]
    pumping_length: str         # "p" (символическое)
    word_w: str                 # Первое слово w = xy
    word_w_prime: str           # Второе слово w' = xz
    common_prefix_x: str        # Общий префикс x, |x| > p
    suffix_y: str               # y (суффикс w)
    suffix_z: str               # z (суффикс w')
    first_letters_match: str    # Почему первые буквы y и z совпадают
    no_pumping_argument: str    # Почему синхронная накачка невозможна
```

### 5.5. shallit (деструктивный)

**Модель:** Opus

**Цель:** Доказать, что язык НЕ DCFL, используя лемму Шаллита.

**Формулировка:**
Для DCFL L существует бесконечное множество M ⊆ Σ*, такое что для всякого w:
либо Mw ⊆ L, либо Mw ∩ L = ∅.

**Отрицание:** Для каждого бесконечного M найдётся w, разделяющее M
(т.е. существуют u, v ∈ M: uw ∈ L, но vw ∉ L).

**Формат proof_sketch:**
```python
@dataclass
class ShallitProof:
    kind: Literal["shallit"]
    infinite_set_description: str   # "Для любого бесконечного M..."
    separating_context: str         # Как для каждого M найти w
    two_elements: str               # u, v ∈ M: uw ∈ L, vw ∉ L
    argument: str                   # Полное рассуждение
```

**Пример для палиндромов {ww^R | w ∈ {a,b}*}:**
```json
{
  "kind": "shallit",
  "infinite_set_description": "Возьмём любое бесконечное M ⊆ {a,b}*",
  "separating_context": "Для любых u ≠ v ∈ M, возьмём w = ba^{|uv|}bu^R",
  "two_elements": "u·w = u·ba^{|uv|}bu^R — палиндром (∈ L), v·w = v·ba^{|uv|}bu^R — не палиндром (∉ L)",
  "argument": "Никакие два различных слова не попадают в один класс эквивалентности → бесконечно много классов → не DCFL"
}
```

### 5.6. inh_ambiguity (деструктивный)

**Модель:** Opus

**Цель:** Доказать, что язык НЕ DCFL, через существенную неоднозначность.

**Теоретическое обоснование:**
- Каждый DCFL имеет однозначную грамматику (потому что DCFL ⊆ UnambCF).
- Если язык существенно неоднозначен (все его грамматики неоднозначны) → он не DCFL.
- Классический пример: {aⁱbʲcᵏ | i = j ∨ j = k}.

**Стратегия агента:**
1. Обнаружить паттерн дизъюнкции с общей переменной.
2. Показать, что для слов, удовлетворяющих обоим ветвям дизъюнкции,
   любая грамматика вынуждена порождать два разных дерева вывода.
3. Формализовать через теорему Огдена или прямое рассуждение.

**Формат proof_sketch:**
```python
@dataclass
class InherentAmbiguityProof:
    kind: Literal["inh_ambiguity"]
    disjunction_identified: str     # Какая дизъюнкция
    overlap_words: str              # Слова, удовлетворяющие обеим ветвям
    ambiguity_argument: str         # Почему любая грамматика неоднозначна
    dcfl_implication: str           # "DCFL ⊂ UnambCF, поэтому..."
```

**Пример для {aⁿb*(cⁿ|bⁿ)ac* | n > 0}:**
```json
{
  "kind": "inh_ambiguity",
  "disjunction_identified": "cⁿ|bⁿ при общем n с префиксом aⁿb*",
  "overlap_words": "Слова вида aⁿbⁿac* (при пустом b* перед bⁿ): совпадают с веткой bⁿ. При bⁿ = bⁿ после b* получаем пересечение.",
  "ambiguity_argument": "Язык представим как L₁ ∪ L₂, где L₁ = {aⁿb*cⁿac*}, L₂ = {aⁿb*bⁿac*}. Пересечение L₁ ∩ L₂ на словах с b*bⁿ неоднозначно: разбиение b*bⁿ допускает разные интерпретации границы b*/bⁿ. По теореме о существенной неоднозначности объединения...",
  "dcfl_implication": "Существенно неоднозначный → не UnambCF → не DCFL (т.к. DCFL ⊂ UnambCF)"
}
```

---

## 6. Verification Layer

### 6.1. oracle_verification_node

**Модель:** Pure function (без LLM)

**Вход:** Результаты всех 5 агентов + `DCFLTaskIR` + `PreprocessResult`

**Задача:** Проверить claim'ы агентов алгоритмически, насколько возможно.

**6.1.1. Проверка stack_strategy:**
- Если агент указал regex-ограничения → проверить, что ДКА для них корректен
  (построить ДКА, проверить на sample_words).
- Если указан разделитель → проверить, что разделитель однозначно определяет
  точку переключения (на sample_words).
- Проверить, что sample_words из preprocess_node действительно принадлежат языку.

**6.1.2. Проверка closure_reduction:**
- Если дополнение → проверить, что complement(sample_words) ∩ L = ∅
  (слова из дополнения не принадлежат L и наоборот).
- Если пересечение с регулярным → проверить, что sample_words ∈ L ∩ R.

**6.1.3. Проверка dcfl_pumping:**
- Подставить конкретные значения p = 1, 2, ..., 10.
- Для каждого p проверить, что указанные слова w, w' действительно принадлежат L.
- Для каждого допустимого разбиения проверить, что накачка ломает принадлежность.

**6.1.4. Проверка shallit:**
- Для конкретных u, v ∈ M и w проверить uw ∈ L и vw ∉ L (через CYK или подстановку).

**6.1.5. Проверка inh_ambiguity:**
- Проверить, что указанные overlap_words действительно принадлежат L.
- Проверить, что обе ветви дизъюнкции действительно покрывают эти слова.
- (Полная формальная проверка существенной неоднозначности неразрешима;
  проверяем только необходимые условия.)

### 6.2. CYK Oracle

Переиспользуется из `cfl_system.lib.cyk_oracle`:
- Вход: грамматика (CNF) + слово
- Выход: принадлежит / не принадлежит
- Timeout: 10 секунд для слов > 100 символов

Для задач Format 1 (без грамматики): oracle работает через подстановку
значений переменных и проверку ограничений.

### 6.3. Word Sampler

Переиспользуется из `agent_system.lib.word_generator`:
- Для Format 2: генерация слов из грамматики (BFS по деривациям, но с CYK-подтверждением)
- Для Format 1: перебор значений переменных, подстановка, проверка ограничений

---

## 7. Reasoning и Retry

### 7.1. reasoning_agent_node

**Модель:** Opus

**Вход:** Результаты всех 5 агентов + oracle verification + classifier hint + hypothesis

**Задача:**
1. Агрегировать результаты: какие агенты вернули `success`, какие `fail`
2. Проверить непротиворечивость: если и конструктивный, и деструктивный agent
   вернули `success` — конфликт, нужно разобраться
3. Выбрать наиболее убедительное доказательство
4. Если ни один агент не дал `success` → сформировать retry_plan
5. Синтезировать финальный вердикт + proof sketch

**Выход:**
```python
@dataclass
class ReasoningResult:
    verdict: str                # "dcfl" | "non_dcfl" | "uncertain"
    confidence: float           # 0.0–1.0
    chosen_proof: AgentOutput   # Лучшее доказательство
    supporting_agents: list[str] # Агенты, подтвердившие вердикт
    conflicting_agents: list[str] # Агенты с противоположным вердиктом
    needs_retry: bool
    retry_plan: RetryPlan | None
```

### 7.2. retry_planner_node

**Модель:** Pure function (логика) + Sonnet (генерация hint)

**Вход:** `ReasoningResult` с `needs_retry = True`

**Выход:** `RetryPlan`

```python
@dataclass
class RetryPlan:
    agents_to_retry: list[str]          # Какие агенты перезапустить
    hints: dict[str, str]               # agent_name → подсказка
    max_retries_remaining: int          # Сколько попыток осталось (default 2)
```

**Пример:**
```json
{
  "agents_to_retry": ["dcfl_pumping"],
  "hints": {
    "dcfl_pumping": "Попробуй семейство слов a^n b^n с контекстом c^n вместо предыдущего выбора. Общий префикс должен покрывать часть a^n."
  },
  "max_retries_remaining": 1
}
```

**Правила retry:**
- max_retries = 2 (итого 3 попытки: initial + 2 retry)
- Не перезапускать агентов со статусом `not_applicable`
- Не перезапускать агентов с `confidence > 0.8` и `status = "fail"`
  (скорее всего метод действительно не подходит)
- При retry передавать конкретную подсказку, а не просто "попробуй ещё раз"

---

## 8. Выходной формат

### 8.1. Финальный JSON

```python
@dataclass
class DCFLSolutionOutput:
    task_id: str
    verdict: str                    # "dcfl" | "non_dcfl"
    proof_method: str               # "stack_strategy" | "closure_reduction" | ...
    proof_sketch: ProofSketch       # Структурированное доказательство
    proof_text: str                 # Текст доказательства (LaTeX)
    confidence: float
    agent_results: dict[str, AgentOutput]  # Результаты всех агентов
    oracle_verification: dict       # Результаты oracle-проверок
    metadata: dict                  # Время работы, модели, retry count
```

### 8.2. Renderer

**Выходные файлы:**
- `{task_id}_solution.json` — полный JSON с результатами
- `{task_id}_solution.md` — Markdown с доказательством
- `{task_id}_solution.html` — HTML с табами (задание / решение / agent logs)

**КРИТИЧЕСКИ ВАЖНО:** Guard `if proof_sketch is None` перед обращением к полям proof.
Урок из REG-системы: renderer падал с NoneType access при отказе агента.

### 8.3. LaTeX-конвенции (из REG-системы)

- Использовать `b·aⁱ` (с разделяющей точкой), не `baⁱ`
- Не использовать `\,` (thin space) — вызывает проблемы рендеринга
- Степени: `a^{n}`, не `a^n` для многосимвольных показателей
- Множества: `\{`, `\}` с escape

---

## 9. Файловая структура

```
dcfl_system/
├── CLAUDE.md                       # Контекст для Claude Code
├── tz_dcfl_agent_system.md         # Данное ТЗ
│
├── lib/                            # Pure functions (без LLM)
│   ├── __init__.py
│   ├── dcfl_ir_schema.py           # IR schema (§2)
│   ├── hypothesis_module.py        # Hypothesis module (§4.2)
│   ├── pattern_db.py               # База паттернов (§4.4.1)
│   ├── closure_table.py            # Table 1 — closure properties (§4.4.2)
│   ├── word_sampler.py             # Генератор слов для DCFL-задач
│   ├── oracle_verifier.py          # Oracle verification (§6.1)
│   └── retry_logic.py              # Логика retry_planner (§7.2)
│
├── prompts/                        # Промпты агентов
│   ├── input_parser.md
│   ├── classifier.md
│   ├── stack_strategy.md           # §5.2
│   ├── closure_reduction.md        # §5.3
│   ├── dcfl_pumping.md             # §5.4
│   ├── shallit.md                  # §5.5
│   ├── inh_ambiguity.md            # §5.6
│   └── reasoning_agent.md
│
├── orchestrator.py                 # LangGraph orchestrator
├── renderer.py                     # Генерация .json/.md/.html
│
├── examples/                       # Примеры задач
│   ├── task_wvaavRwR.json          # Задача 1 (§2.3)
│   ├── task_u1au2_u3au4.json       # Задача 2 (§2.4)
│   ├── task_anb_cnbn.json          # Задача 3 (§2.5)
│   ├── task_grammar_aSSb.json      # Задача 4 (§2.6)
│   └── mock/                       # Mock-ответы агентов для тестов
│       ├── stack_strategy_mock.json
│       ├── closure_reduction_mock.json
│       ├── dcfl_pumping_mock.json
│       ├── shallit_mock.json
│       └── inh_ambiguity_mock.json
│
├── tests/
│   ├── test_ir_schema.py
│   ├── test_hypothesis_module.py
│   ├── test_pattern_db.py
│   ├── test_closure_table.py
│   ├── test_word_sampler.py
│   ├── test_oracle_verifier.py
│   ├── test_retry_logic.py
│   └── test_orchestrator.py
```

---

## 10. Фазы реализации

### Phase 1: Core Infrastructure (pure fn, без LLM)

1. `lib/dcfl_ir_schema.py` — IR schema с валидацией
2. `lib/hypothesis_module.py` — классификация паттернов
3. `lib/pattern_db.py` — база паттернов из §1.3
4. `lib/closure_table.py` — Table 1 + logic
5. `lib/word_sampler.py` — генерация слов
6. `examples/` — 4 JSON-файла с задачами
7. Тесты на всё

```bash
cd dcfl_system/
python -m pytest tests/ -v
python -m lib.dcfl_ir_schema examples/task_wvaavRwR.json
```

### Phase 2: Orchestrator + Mock Agents

1. `orchestrator.py` — LangGraph граф с заглушками агентов
2. `examples/mock/` — mock-ответы для каждого агента
3. `renderer.py` — генерация .json + .md + .html
4. Тесты end-to-end с mock-данными

```bash
python orchestrator.py examples/task_wvaavRwR.json --mock examples/mock/ --render html
```

### Phase 3: LLM Agents

1. `prompts/stack_strategy.md` — промпт для stack_strategy
2. `prompts/closure_reduction.md` — промпт для closure_reduction
3. `prompts/dcfl_pumping.md` — промпт для dcfl_pumping
4. `prompts/shallit.md` — промпт для shallit
5. `prompts/inh_ambiguity.md` — промпт для inh_ambiguity
6. `prompts/reasoning_agent.md` — промпт для reasoning agent
7. `lib/oracle_verifier.py` — verification layer
8. Интеграция с Claude API / subagent delegation
9. Live-тестирование на 4 задачах из examples/

```bash
python orchestrator.py examples/task_anb_cnbn.json --live --render html
```

### Phase 4: Testing + Polish

1. Тестирование на реальных экзаменационных задачах (билеты)
2. Финальная отладка renderer (guard `proof is None`)
3. Расширение pattern_db новыми паттернами из билетов
4. Калибровка confidence thresholds на реальных данных

---

## 11. Зависимости

### 11.1. Зависимости от других систем

| Зависимость | Откуда | Зачем |
|-------------|--------|-------|
| `ir_schema` | `agent_system.lib` | Базовые типы IR |
| `oracle` | `agent_system.lib` | Oracle framework |
| `word_generator` | `agent_system.lib` | Генератор слов |
| `cyk_oracle` | `cfl_system.lib` | CYK membership |

### 11.2. Python-зависимости

- Python 3.11+
- `langgraph` — orchestrator
- `jsonschema` — валидация IR
- `anthropic` — Claude API (Phase 3)
- Нет зависимостей beyond stdlib + jsonschema для Phase 1

---

## 12. Критические замечания (из опыта REG и CFL систем)

**1. Classifier НИКОГДА не гейтит dispatch.**
В REG-системе classifier ошибался на задаче {w | w = vv^R u ∨ w = uvv^R},
неверно определив `vv^R` как finite-memory. Если бы dispatch зависел от classifier,
правильное доказательство не было бы найдено.

**2. Лемма накачки для DCFL — это НЕ лемма накачки для CFL.**
Формулировка другая: нужны ДВА слова с общим длинным префиксом и синхронная накачка.
Агент `dcfl_pumping` не должен путать с CFL pumping.

**3. Существенная неоднозначность ≠ неоднозначность грамматики.**
Грамматика может быть неоднозначной, но язык — однозначным (есть другая,
однозначная грамматика). Существенная неоднозначность — свойство ЯЗЫКА, не грамматики.

**4. DCFL замкнут только по ~ и h⁻¹.**
Не по ∪, ∩, ·, *, R, h. Table 1 — единственный источник истины.
Дополнительно: DCFL ∩ REG = DCFL (из конструкции DPDA × DFA),
но это не отражено в таблице (там ∩ означает пересечение с тем же классом).

**5. Guard `proof is None` в renderer.**
REG-система падала, когда агент возвращал `proof_sketch: null`.
Все обращения к полям proof_sketch должны быть guarded.

**6. BFS-оракул ненадёжен для глубоких деривации.**
В REG-системе BFS с ограничением глубины давал false negatives.
Использовать CYK (точный) вместо BFS (приближённый).

**7. Формат 2: анализировать ЯЗЫК, не грамматику.**
Если грамматика неоднозначна — это не значит, что язык не DCFL.
Нужно рассуждать о свойствах порождаемого языка, а не о структуре грамматики.
