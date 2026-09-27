# ТЗ: Агентская система для анализа детерминированности КС-языков

## 0. Контекст

Данная система — третий компонент в линейке агентских систем для задач ТФЯ:

- **REG-система** (`agent_system/`) — анализ регулярности. Реализована, live-тестирована.
- **CFL-система** (`cfl_system/`) — анализ КС-свойства. Архитектура зафиксирована.
- **DCFL-система** (`dcfl_system/`) — анализ детерминированности КС-языка. **Данное ТЗ.**

Вопрос задачи: «является ли данный КС-язык детерминированным (DCFL)?»

Ключевая теорема: **LR(k)-грамматики порождают ровно класс DCFL.**
У каждого DCFL есть LR(1)-грамматика (Кнут); L$ — LR(0) (THEORY.md §1.9; без источника
формулировку про SLR(1) не утверждать).

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

Рассуждает на уровне:
- Сколько фаз push/pop у стека?
- Есть ли чёткий разделитель между фазами?
- Регулярные ограничения на компоненты → конечная память (состояния DPDA)
- Где точка переключения с push на pop? Можно ли её определить детерминированно?

**R2' (docs/VERDICT_POLICY.md) — с этой ревизии агент ОБЯЗАН построить явный ДМПА.**
Словесное рассуждение о фазах (`phases`/`separator`/`finite_control`/`determinism_argument`)
остаётся человекочитаемой частью доказательства, но само по себе НЕ является сертификатом:
для `status: "success"` поле `dpda` (см. ниже) обязательно. Если построить полный ДМПА не
удаётся — агент возвращает `status: "uncertain"` (язык, вероятно, DCFL, но конструкция не
доведена до конца) или `status: "not_applicable"` (метод структурно неприменим — см. §5.2
правило про разделители), а не словесную «стратегию» без автомата.

**Формат proof_sketch:**
```python
@dataclass
class StackStrategyProof:
    kind: Literal["stack_strategy"]
    phases: list[StackPhase]        # Фазы работы стека (человекочитаемо)
    separator: str | None           # Разделитель между фазами
    finite_control: str             # Описание конечной памяти
    determinism_argument: str       # Почему выбор перехода однозначен
    regex_in_states: list[str]      # Какие regex-ограничения → состояния
    dpda: DPDA                      # ОБЯЗАТЕЛЬНО при status="success" (R2')
```

```python
@dataclass
class StackPhase:
    name: str                       # "push_w", "compare_v^R", ...
    action: str                     # "push" | "pop" | "compare" | "skip"
    what: str                       # Что кладём/снимаем/сравниваем
    trigger: str                    # Что переключает на следующую фазу
```

```python
@dataclass
class DPDATransition:
    from_: str                      # исходное состояние ("from" в JSON)
    read: str | None                # читаемый символ, null = эпсилон-переход
    top: str                        # требуемая (и снимаемая) верхушка стека
    to: str                         # целевое состояние
    push: list[str]                 # что класть, ВЕРХНИЙ СИМВОЛ ПЕРВЫМ (как в cfl_pda_builder.md)

@dataclass
class DPDA:
    states: list[str]
    start: str
    accept_states: list[str] | None      # для accept_mode="final_state"
    accept_mode: Literal["final_state", "empty_stack"] | None
    stack_alphabet: list[str]
    initial_stack: list[str]             # верхний символ первым (обычно один маркер дна)
    transitions: list[DPDATransition]
```

Детерминизм проверяется механически (`dcfl_system/lib/dpda.check_determinism`, §6.1.1): для
каждой пары (состояние, верхушка стека) — не более одного перехода на каждую букву, и
эпсилон-переход никогда не сосуществует с переходом по букве для той же пары. **Каждая
логически различная фаза счёта должна иметь СВОЁ состояние** — одно состояние, обслуживающее
одновременно «докладываем в первый блок» (по букве X) и «снимаем на второй блок» (по букве Y)
с ОДНОЙ И ТОЙ ЖЕ верхушкой стека, синтаксически детерминировано (переходы различаются буквой),
но допускает семантическую ошибку: случайная буква первого рода после начала второй фазы
незаметно принимается за продолжение первой (пример проверен в
`dcfl_system/tests/test_oracle_verifier.py`/`test_dpda.py`: наивный ДМПА для {aⁿbⁿcᵐ} с общим
состоянием `q_count` для push-a/pop-b ошибочно принимал `"aababbc"`).

**Пример для {$aⁿbⁿcᵐ | n,m ≥ 1} ∪ {d aᵐbⁿcⁿ | m,n ≥ 1}** (THEORY.md §1.9; НЕ `{wvaav^Rw^R}` —
там кандидат-разделитель `aa` встречается и внутри `w ∈ (aa*b)*a`, и внутри `v ∈ b(ab|aa)*`, так
что это не настоящий разделитель, а сам язык не является DCFL, см. THEORY.md §1.6):
```json
{
  "kind": "stack_strategy",
  "phases": [
    {"name": "select_mode", "action": "skip", "what": "первый символ входа ($ или d)", "trigger": "единственный раз, когда автомат ветвится"},
    {"name": "push_a ($-ветвь)", "action": "push", "what": "символы a", "trigger": "режим $ активен"},
    {"name": "pop_b_then_skip_c ($-ветвь)", "action": "pop", "what": "b снимает a со стека, затем c читается без обращения к стеку", "trigger": "смена a→b, затем b→c однозначно видна по входу"},
    {"name": "push_a (d-ветвь)", "action": "push", "what": "символы a (счётчик m)", "trigger": "режим d активен"},
    {"name": "compare_bc (d-ветвь)", "action": "compare", "what": "b и c сравниваются друг с другом (bⁿ=cⁿ)", "trigger": "смена a→b кладёт маркер границы, смена b→c запускает сравнение"}
  ],
  "separator": "$ | d (первый символ слова — не входит в основной алфавит {a,b,c}, встречается только один раз)",
  "finite_control": "Один бит режима ($-ветвь или d-ветвь), выбранный по первому символу и неизменный до конца слова",
  "determinism_argument": "Первый символ ($ или d) не встречается больше нигде в слове — настоящий разделитель, однозначно выбирающий один из двух непересекающихся детерминированных сценариев; дальше в каждой ветви — обычный левый-направо стек с двумя счётными фазами.",
  "regex_in_states": [],
  "dpda": {
    "states": ["q0", "qA0", "qA_push", "qA_pop", "qA_c", "qB0", "qB_skip", "qB_push", "qB_pop", "qB_done"],
    "start": "q0",
    "accept_states": ["qA_c", "qB_done"],
    "stack_alphabet": ["Z0", "A", "B", "B1"],
    "initial_stack": ["Z0"],
    "transitions": [
      {"from": "q0", "read": "$", "top": "Z0", "to": "qA0", "push": ["Z0"]},
      {"from": "q0", "read": "d", "top": "Z0", "to": "qB0", "push": ["Z0"]},
      {"from": "qA0", "read": "a", "top": "Z0", "to": "qA_push", "push": ["A", "Z0"]},
      {"from": "qA_push", "read": "a", "top": "A", "to": "qA_push", "push": ["A", "A"]},
      {"from": "qA_push", "read": "b", "top": "A", "to": "qA_pop", "push": []},
      {"from": "qA_pop", "read": "b", "top": "A", "to": "qA_pop", "push": []},
      {"from": "qA_pop", "read": "c", "top": "Z0", "to": "qA_c", "push": ["Z0"]},
      {"from": "qA_c", "read": "c", "top": "Z0", "to": "qA_c", "push": ["Z0"]},
      {"from": "qB0", "read": "a", "top": "Z0", "to": "qB_skip", "push": ["Z0"]},
      {"from": "qB_skip", "read": "a", "top": "Z0", "to": "qB_skip", "push": ["Z0"]},
      {"from": "qB_skip", "read": "b", "top": "Z0", "to": "qB_push", "push": ["B1", "Z0"]},
      {"from": "qB_push", "read": "b", "top": "B1", "to": "qB_push", "push": ["B", "B1"]},
      {"from": "qB_push", "read": "b", "top": "B", "to": "qB_push", "push": ["B", "B"]},
      {"from": "qB_push", "read": "c", "top": "B1", "to": "qB_done", "push": []},
      {"from": "qB_push", "read": "c", "top": "B", "to": "qB_pop", "push": []},
      {"from": "qB_pop", "read": "c", "top": "B", "to": "qB_pop", "push": []},
      {"from": "qB_pop", "read": "c", "top": "B1", "to": "qB_done", "push": []}
    ]
  }
}
```

Обратите внимание: `qA_push`/`qA_pop` и `qB_push`/`qB_pop` — РАЗНЫЕ состояния (не одно общее
«счётное» состояние на push и pop) именно из-за детерминизма-по-смыслу, описанного выше в §5.2.
Обратите внимание также на `B1` — отдельный символ стека для ПЕРВОГО (самого нижнего) `b`
своего блока: именно поэтому снятие последнего `b` блока (`top: "B1"`) — переход по РЕАЛЬНОЙ
букве `c` прямо в принимающее состояние `qB_done`, а не `ε`-переход из `qB_pop` в отдельный
`qB_accept` (как в более ранней, некорректной ревизии этого примера — см. правило про ε-переходы
в `stack_strategy.md` и `normalize_epsilon_accept_sinks` в §6.1.1 ниже: тот же `qB_pop`
одновременно достижим и с `top: "B"` (ещё не всё снято), и с `top: "B1"` (снято всё) — если бы
`qB_pop` был просто помечен принимающим вместо ε-перехода, слово остановилось бы на середине
блока `b` тоже принималось бы, что неверно; поэтому нужен ИМЕННО отдельный стековый символ и
переход по букве, а не одна лишь пометка состояния).

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

**Цель:** Доказать, что язык НЕ DCFL, используя двухсловную лемму о накачке для DCFL
(лемма Ю, [Yu]; см. `docs/THEORY.md` §1.1). В курсе ИУ-9 эту лемму иногда называют леммой
Шаллита; в этой системе `shallit` — отдельный агент (классы Нероуда / лемма о продолжении,
§5.5), не путать.

**Формулировка леммы.** Пусть L — DCFL. Существует p такая, что для любых xy ∈ L, xz ∈ L
с |x| > p и одинаковыми первыми буквами y и z выполнено хотя бы одно из условий:

- **(1)** x = x₁x₂x₃x₄x₅, |x₂x₄| ≥ 1, |x₂x₃x₄| ≤ p (пара (x₂, x₄) стоит **в любом месте** x,
  ограничена только длина окна), и ∀i ≥ 0: x₁x₂ⁱx₃x₄ⁱx₅y ∈ L И x₁x₂ⁱx₃x₄ⁱx₅z ∈ L.
- **(2)** x = x₁x₂x₃, y = y₁y₂y₃, z = z₁z₂z₃, |x₂| ≥ 1, |x₂x₃| ≤ p (x₂ — в последних p
  символах x), и ∀i ≥ 0: x₁x₂ⁱx₃y₁y₂ⁱy₃ ∈ L И x₁x₂ⁱx₃z₁z₂ⁱz₃ ∈ L.

**Отрицание (доказательство L ∉ DCFL):** для всякого p указать xy, xz ∈ L, |x| > p,
⁽¹⁾y = ⁽¹⁾z, и показать, что для КАЖДОГО допустимого разбиения по (1) и по (2) существует i,
при котором хотя бы одно из двух накачанных слов выходит из L.

**Важно:** условие (1) — это пара (x₂, x₄) в произвольном месте x, а не одиночный фактор в
хвосте x; при ошибочной (одиночной) формулировке лемма ложно отвергает DCFL {aⁿbⁿcᵐ}
(контрпример-ловушка: x = aⁿbⁿ, y = c, z = cc — пара (a, b) на границе блоков накачивается,
одиночный фактор — нет). Агент обязан проверять условие (1) именно как пару.

**Формат proof_sketch:**
```python
@dataclass
class DCFLPumpingProof:
    kind: Literal["dcfl_pumping"]
    pumping_length: str          # "p" (символическое)
    word_w: str                  # Первое слово w = xy
    word_w_prime: str            # Второе слово w' = xz
    common_prefix_x: str         # Общий префикс x, |x| > p
    suffix_y: str                # y (суффикс w)
    suffix_z: str                # z (суффикс w')
    first_letters_match: str     # Почему первые буквы y и z совпадают
    condition1_argument: str     # Почему никакая пара (x2, x4) в любом окне x с |x2x3x4|<=p не накачивается для обоих слов
    condition2_argument: str     # Почему никакое x2 в последних p символах x с синхронной накачкой y2/z2 не проходит
    word_instances: dict | None  # ОБЯЗАТЕЛЬНО (docs/VERDICT_POLICY.md §4): {"2": {"w":..,"w_prime":..,"x_length":..}, "3": {...}} — конкретные слова w=xy, w'=xz при n=p+2 для p in {2,3}; без него trust не выше not_verified
```

### 5.5. shallit (деструктивный)

**Модель:** Opus

**Цель:** Доказать, что язык НЕ DCFL одной из двух техник [Sh, §4.7] (`docs/THEORY.md` §1.2–1.3):
теоремой 4.7.4 о классах Нероуда или леммой о продолжении.

**Техника 1 — nerode_classes (теорема 4.7.4 [Sh]).** Если L — DCFL, хотя бы один класс
эквивалентности Майхилла–Нероуда языка L бесконечен. Контрапозиция: если все классы конечны, то
L ∉ DCFL — обычно доказывается предъявлением разделяющего суффикса w(u,v) для любых u ≠ v.
Техника применима только если ВСЕ классы Нероуда, включая мёртвый класс D (слова, не продолжаемые
ни в одно слово L), конечны (THEORY.md §1.2). «Бесконечно много классов» — не аргумент: у {aⁿbⁿ}
бесконечно много классов, а язык DCFL. Если D бесконечен (например, bbΣ* ⊆ D), верните status
not_applicable. Агент обязан явно заявить это в обязательном поле `dead_class_status ∈ {"empty",
"infinite"}`; D замкнут относительно продолжений справа (если x ∈ D, то xΣ* ⊆ D), поэтому непустой
D бесконечен, и «D конечен» означает D = ∅ — третьего значения нет. `"infinite"` означает, что
техника неприменима — тогда `status` должен быть `not_applicable`, а не `success` (заявить
`dead_class_status: "infinite"` и всё же вернуть `success`/`non_dcfl` — самопротиворечие,
оракул-верификатор отвергает это как `refuted`). Старое значение `"finite"` (до этой ревизии
контракта) читается оракул-верификатором как `"empty"` для обратной совместимости.

**Техника 2 — prefix_continuation (лемма о продолжении).** Пусть L — DCFL, $ ∉ Σ. Тогда
haspref(L) = {xy | x ∈ L, xy ∈ L, y ≠ ε} и L_$ = {x$y | x ∈ L, xy ∈ L} — DCFL. Если для
некоторого регулярного R язык L_$ ∩ R (или haspref(L) ∩ R) не является КС, то по контрапозиции
(DCFL замкнуты относительно ∩ REG и DCFL ⊆ CFL) L ∉ DCFL.

**Формат proof_sketch:**
```python
@dataclass
class ShallitProof:
    kind: Literal["shallit"]
    technique: Literal["nerode_classes", "prefix_continuation"]
    dead_class_status: Literal["empty", "infinite"] | None  # nerode_classes: ОБЯЗАТЕЛЬНО; null для prefix_continuation (legacy "finite" читается как "empty")
    distinguishing_suffix: str | None   # nerode_classes: w(u,v) для произвольных u != v
    separation_argument: str | None     # nerode_classes: почему uw in L, vw not in L
    derived_language: str | None        # prefix_continuation: L_$ ∩ R или haspref(L) ∩ R = {...}
    regular_filter: str | None          # prefix_continuation: R
    non_cfl_argument: str | None        # prefix_continuation: доказательство не-КС производного языка
    argument: str                       # полное рассуждение на русском
```

**Пример nerode_classes для {ww^R | w ∈ {a,b}*}:**
```json
{
  "kind": "shallit",
  "technique": "nerode_classes",
  "dead_class_status": "empty",
  "distinguishing_suffix": "Для u != v: N = 2|uv|, w = b a^N b u^R",
  "separation_argument": "u·w — палиндром (∈ L); v·w — не палиндром при v != u (∉ L)",
  "derived_language": null,
  "regular_filter": null,
  "non_cfl_argument": null,
  "argument": "Все пары u != v различимы ⇒ все классы Нероуда одноэлементны ⇒ конечны; мёртвый класс пуст ⇒ по контрапозиции 4.7.4 L не DCFL"
}
```

**Пример prefix_continuation для {aⁿbⁿ} ∪ {aⁿb²ⁿ}:**
```json
{
  "kind": "shallit",
  "technique": "prefix_continuation",
  "dead_class_status": null,
  "distinguishing_suffix": null,
  "separation_argument": null,
  "derived_language": "L_$ ∩ a*b*$b⁺ = {aⁿbⁿ$bⁿ | n >= 1}",
  "regular_filter": "R = a*b*$b⁺",
  "non_cfl_argument": "{aⁿbⁿ$bⁿ} не КС по лемме о накачке для КС (три равных счётчика)",
  "argument": "L_$ была бы DCFL (лемма о продолжении), тогда L_$ ∩ R была бы DCFL ⊆ CFL — противоречие с не-КС {aⁿbⁿ$bⁿ}, значит L не DCFL"
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

**Пример для {aⁱbʲcᵏ | i = j ∨ j = k}** (THEORY.md §1.5, [Sh, Thm 4.4.1] — классический
существенно неоднозначный язык):
```json
{
  "kind": "inh_ambiguity",
  "disjunction_identified": "i=j ∨ j=k при общем индексе j",
  "overlap_words": "Слова вида aⁿbⁿcⁿ (n ≥ 1): удовлетворяют и i=j, и j=k одновременно.",
  "ambiguity_argument": "Язык представим как L₁ ∪ L₂, где L₁ = {aⁱbʲcᵏ | i=j}, L₂ = {aⁱbʲcᵏ | j=k}. На словах aⁿbⁿcⁿ ∈ L₁ ∩ L₂ любая грамматика для L обязана порождать эти слова и по правилам, «отвечающим» за L₁ (структура a=b), и по правилам, «отвечающим» за L₂ (структура b=c) — это даёт два структурно различных дерева вывода для одного и того же слова в любой грамматике языка [Sh, Thm 4.4.1].",
  "dcfl_implication": "Существенно неоднозначный → не UnambCF → не DCFL (т.к. DCFL ⊂ UnambCF)"
}
```

**Важно (THEORY.md §1.8, exam_03).** Для {aⁿb*(cⁿ|bⁿ)ac* | n > 0} этот метод **не работает**:
при n ≥ 1 ветви aⁿbᵐcⁿac˔ и aⁿb^{m+n}ac˔ дизъюнктны (в первой перед финальным `a` обязательно
стоит cⁿ, во второй — нет ни одной буквы c), значит язык **не** является существенно
неоднозначным — объединение двух дизъюнктных однозначных КС-языков однозначно. Правильный
вердикт для этой задачи — `not_applicable` (не подходящий метод); не-DCFL доказывается отдельно
двухсловной леммой Ю (`dcfl_pumping`, THEORY.md §1.8).

---

## 6. Verification Layer

### 6.1. oracle_verification_node

**Модель:** Pure function (без LLM)

**Вход:** Результаты всех 5 агентов + `DCFLTaskIR` + `PreprocessResult`

**Задача:** Проверить claim'ы агентов алгоритмически, насколько возможно.

**6.1.1. Проверка stack_strategy (docs/VERDICT_POLICY.md R2', реализовано в
`dcfl_system/lib/oracle_verifier._verify_stack_strategy` + `dcfl_system/lib/dpda.py`):**
- Если агент указал regex-ограничения → проверить, что ДКА для них корректен
  (построить ДКА, проверить на sample_words).
- Если указан разделитель → проверить, что разделитель однозначно определяет
  точку переключения (на sample_words).
- Проверить, что sample_words из preprocess_node действительно принадлежат языку.
- **Без поля `dpda`** в `proof_sketch` — как раньше, только структурные проверки выше,
  trust не выше `well_formed` (словесная стратегия — не сертификат, R2').
- **С полем `dpda`** (§5.2):
  0. **(0) Нормализация** (docs/VERDICT_POLICY.md R2', абзац про каноническую нормализацию;
     `dpda.normalize_epsilon_accept_sinks`) — выполняется ПЕРВОЙ, до всего остального, над
     ДМПА как он есть в `proof_sketch`. Для каждого ε-перехода `(q, Z) → q_acc`, где `q_acc`
     принимающий и без исходящих переходов, переход удаляется, а пара `(q, Z)` добавляется в
     множество принимающих КОНФИГУРАЦИЙ `accept_configs` (не в `accept_states` — `q` сам по
     себе принимающим не становится). Условие на `q` («достижимо только с `top = Z`»)
     здесь не нужно: приёмка проверяется по точной паре (состояние, верхушка), поэтому `q`,
     встречающийся и с другой верхушкой `Z' != Z`, от этого не страдает — конфигурация
     `(q, Z')` просто не входит в `accept_configs` и остаётся неприемлющей, как и в исходном
     автомате. Контрпример на реальных данных (`live_c5/dcfl04`, ДМПА для {aⁿbⁿcᵐ}): состояние
     `q_b` (наравне с `q0` и `q_c`) стоит перед таким ε-переходом (`(q_b, Z0) → q_accept`) и
     ТАКЖЕ достижимо с `top = A` через собственный цикл снятия (`(q_b, A, read=b) → q_b`) —
     нормализация всё равно применяется ко всем трём (никакого исключения для `q_b`), результат
     синтаксически детерминирован (`check_determinism` чисто), но сам ДМПА содержит лишний
     переход `q0 --b--> q_b` без предшествующего `a`, из-за которого слово `"b"` ошибочно
     принимается (вход исчерпан в `(q_b, Z0)` — попадание в `accept_configs`) — этот дефект
     ловит только шаг (b) (симуляция против оракула), а не (a): тест
     `test_dpda.py::TestNormalizeEpsilonAcceptSinksLiveDcfl04` фиксирует и нормализацию всех
     трёх переходов, и это расхождение с языком на слове `"b"`, отдельно от `test_dpda.py`'s
     `TestFixedDcfl04VariantReachesBoundedPass` — тот же автомат без лишнего перехода
     нормализуется и проходит симуляцию (`bounded_pass`). Заметки о каждой применённой замене
     пишутся в `details["normalization"]`, сам `accept_configs` — в `details["accept_configs"]`,
     независимо от итогового результата шагов (a)/(b) ниже, которые всегда работают уже над
     НОРМАЛИЗОВАННЫМ автоматом.
  1. **(a) Синтаксическая проверка детерминизма** (`dpda.check_determinism`) — для каждой
     пары (состояние, верхушка стека) не более одного перехода на каждую букву, и
     эпсилон-переход не сосуществует с переходом по букве для той же пары. Проверка
     ПОЛНАЯ (не выборочная) → нарушение даёт `refuted` с указанием конфликтующих переходов.
  2. **(b) Симуляция.** ДМПА конвертируется в формат `cfl_system.lib.pda_simulator`
     (`dpda.to_cfl_pda` — переименование полей `read`/`top` → `input`/`stack_top`, разворот
     `push` в порядок «последний-положенный-наверху», как `cfl_oracle_test.normalize_agent_pda`)
     — тот симулятор уже поддерживает и эпсилон-переходы, и оба режима принятия, второй
     симулятор не пишется. Прогоняются слова из `word_sampler.sample_words` (положительные и
     отрицательные, длина ≤ 10) через настоящий оракул языка ЗАДАЧИ
     (`build_set_builder_membership_oracle` на `task_ir`, не на утверждениях агента).
     Все совпало на ≥ 30 решённых оракулом словах → `bounded_pass` (`details.determinism =
     "verified"`); расхождение хотя бы на одном слове → `refuted` с контрпримером; нет
     оракула для языка задачи или недостаточно слов → `well_formed` (ДМПА синтаксически
     корректен и детерминирован, но не проверен против языка).

**6.1.2. Проверка closure_reduction:**
- Если дополнение → проверить, что complement(sample_words) ∩ L = ∅
  (слова из дополнения не принадлежат L и наоборот).
- Если пересечение с регулярным → проверить, что sample_words ∈ L ∩ R.

**6.1.3. Проверка dcfl_pumping:**
- Проверить наличие полей контракта DCFLPumpingProof, в частности `condition1_argument` и
  `condition2_argument` (замена устаревшего единого `no_pumping_argument`), и — ОТДЕЛЬНО,
  ОБЯЗАТЕЛЬНО — `word_instances`: без него trust не выше `not_verified` (не `well_formed`!),
  структурно неинстанцированное доказательство недоказательно (прецедент: live dcfl-21 —
  well_formed non_dcfl 0.60 для языка, который на самом деле DCFL).
- Из `word_instances["2"]`/`["3"]` взять конкретные w, w', x_length (n = p+2 для p ∈ {2,3});
  проверить структурно: общий префикс x = w[:x_length] действительно совпадает у w и w',
  x_length > p, суффиксы y = w[x_length:], z = w'[x_length:] непусты и начинаются с одной буквы.
- С оракулом языка задачи: проверить, что w, w' действительно принадлежат L.
- Для каждого допустимого разбиения (по условию (1) — пара (x₂,x₄) в любом окне ≤ p; по
  условию (2) — x₂ в последних p символах x) проверить, что накачка при i ∈ {0,2} (или, если оба
  выжили, i=3) ломает принадлежность хотя бы одного из двух слов; если нашлось разбиение, где ОБА
  слова остаются в L для всех i ∈ {0,2,3} ⇒ `refuted` (условие леммы выполнено — доказательство
  неверно); если для каждого из p ∈ {2,3} все допустимые разбиения по (1) и (2) закрыты ⇒
  `well_formed` (необходимое, но не достаточное условие — реальная константа Ю обычно много
  больше 2–3, так что замыкание на этом маленьком фиксированном p доказывает лишь то, что оракул
  не нашёл контрпримера среди проверенных разбиений); `bounded_pass` для `dcfl_pumping` не
  выдаётся никогда — решающим является только настоящий контрпример (`refuted`).

**6.1.4. Проверка shallit:**
- Проверить наличие полей по указанной `technique`: для `nerode_classes` обязательны
  `dead_class_status` (∈ {"empty", "infinite"} — старое значение `"finite"` принимается для
  обратной совместимости и читается как `"empty"`, с пометкой в `issues`), `distinguishing_suffix`,
  `separation_argument` (отсутствие или невалидное значение `dead_class_status` ⇒ `not_verified`);
  для `prefix_continuation` обязательны `derived_language`, `regular_filter`, `non_cfl_argument`.
- `dead_class_status == "infinite"` при `status == "success"` ⇒ `refuted` (доказательство само
  признаёт технику неприменимой — должно было вернуть `not_applicable`), без обращения к оракулу.
- Для `nerode_classes`, когда есть оракул принадлежности (`build_membership_oracle_from_ir`,
  ВСЕГДА, независимо от вида `distinguishing_suffix`): перебрать слова длины ≤ 8 над алфавитом
  (не более 5000 слов) и для каждого проверить непродолжаемость в L (`_continuable`, поиск до
  `max(6, |слово|)` символов — граница растёт вместе со словом, а не фиксирована на +6, иначе
  восьмисимвольное слово, которому нужно 7-8 символов продолжения, ложно считается мёртвым; с
  бюджетом узлов, ответ "неизвестно" — слово пропускается). D замкнут вправо (если x мёртв, то и
  xy мёртв для любого y), поэтому непустой D всегда бесконечен, а «конечен, но не пуст» не
  бывает: найдено ХОТЯ БЫ ОДНО непродолжаемое слово ⇒ `refuted` для `"empty"` (и для
  нормализованного из `"finite"`, не только когда непродолжаемые слова находятся на каждой
  длине). Иначе — для
  конкретных u, v и w = w(u,v) (только если суффикс буквальный) проверить uw ∈ L и vw ∉ L (через
  CYK или подстановку).
- Для `prefix_continuation`: проверить, что производный язык (L_$ ∩ R или haspref(L) ∩ R)
  вычислен согласно указанному R на sample_words, и что заявленный не-КС аргумент не содержит
  явных противоречий (полная проверка не-КС неразрешима — проверяем необходимые условия).

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
