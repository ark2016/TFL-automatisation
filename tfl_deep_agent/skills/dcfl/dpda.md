# DPDA certificate: contract, checks, design rules

Part of the `dcfl` skill. Read it before you build the automaton for a `dcfl` verdict
(VERDICT_POLICY R2'). The same JSON goes to `dpda_run` and `dpda_vs_ir`.

## 1. The JSON contract (`dcfl_system/tz_dcfl_agent_system.md` §5.2)

```json
{
  "states": ["q0", "q1"],
  "start": "q0",
  "accept_states": ["q1"],
  "accept_mode": "final_state",
  "stack_alphabet": ["Z0", "A"],
  "initial_stack": ["Z0"],
  "transitions": [
    {"from": "q0", "read": "a", "top": "Z0", "to": "q1", "push": ["A", "Z0"]}
  ]
}
```

- `read`: one input letter, or `null` for an ε-move (`""`, `"ε"`, `"eps"`, `"epsilon"` also mean ε).
  A multi-character `read` is rejected by the tool schema: words are read letter by letter.
- `top`: the stack top the move requires. It is **always popped**; `push` replaces it.
- `push`: **topmost symbol first**. `[]` pops, `["A", "Z0"]` on top `Z0` puts `A` above `Z0`, and
  `["Z0"]` on top `Z0` leaves the stack unchanged.
- `initial_stack`: topmost first, usually a single bottom marker `["Z0"]`.
- `accept_mode`: omit it or use `"final_state"`. THEORY §1 defines DCFL by final-state acceptance;
  empty-stack acceptance without an end marker only gives prefix-free languages, so it fails for any
  language that contains a word and one of its proper prefixes (for example `ab` and `abaabb` in dcfl-21).
- `accept_configs`: leave it out. The normalization below fills it.
- A missing transition means the run is stuck and the word is rejected; you do not need a dead state.
- Limits of the tools: 300 states, 300 stack symbols, 2000 transitions, `push` ≤ 16 symbols, initial
  stack ≤ 8, 64 KiB of JSON per call. A larger machine-built certificate (the exam_04 / dcfl-21
  automaton of THEORY §1.10 has 5835 transitions) does not fit: cite it as a reference in the report
  and do not claim a tool check for it.

## 2. What the tools check (R2')

`dpda_vs_ir` runs, in order: the canonical normalization, the complete determinism check, the structure
check, then the deterministic simulator against the task oracle on every word up to the length k with
Σ_{i≤k}|Σ|ⁱ ≤ 3000 (k ≤ 10) plus sampled words up to length 10. `dpda_run` does the first three and runs
only the words you give it, without comparing with the task language.

The determinism rule and the normalization, as the policy states them:

VERDICT_POLICY.md §3 R2′ (verbatim):

> Детерминизм — синтаксическое свойство и проверяется
>   полностью (не более одного перехода на (q, a, Z), и не сосуществуют переходы по ε и по букве при одинаковых (q, Z)) ⇒
>   для самого свойства «детерминирован» trust `verified`;

VERDICT_POLICY.md §3 R2′ (verbatim):

> ε-переход из конфигурации (q, Z) в принимающее состояние-сток q_acc без исходящих переходов удаляется, а пара (q, Z)
>   добавляется в множество **принимающих конфигураций**: слово принимается при исчерпании входа, если состояние принимающее или
>   пара (состояние, вершина стека) — принимающая конфигурация.

Outcomes: a determinism conflict or a structural error → `refuted` (the words are not run); a decided word
on which the DPDA and the exact oracle disagree → `refuted` with counterexamples; agreement on ≥ 30 decided
words → `bounded_pass`; fewer decided words or an approximate oracle → `well_formed`.

Coverage limit: on a **unary** alphabet the full enumeration stops at length 10, so only 11 words are
decided and the result stays `well_formed` (measured on dcfl-17). For a regular language, write the DFA as
a DPDA that never changes the stack (SKILL.md step 3). It meets the same unary limit. `dfa_vs_ir(cls="dcfl")`
enumerates up to length 40 on one letter and can reach `bounded_pass`, but it is supporting evidence only. For
`dcfl` the gate counts only `dpda_vs_ir` ≥ `bounded_pass` or Lean `proved`, so a unary language without a Lean
proof ends `inconclusive` (≤ 0.40).

## 3. Design rules (each one comes from a recorded bug)

1. **One state per counting phase.** Do not let one state both push for the first block and pop for the
   second block on the same stack top:

   tz_dcfl_agent_system.md §5.2 (verbatim):

   > **Каждая
   > логически различная фаза счёта должна иметь СВОЁ состояние** — одно состояние, обслуживающее
   > одновременно «докладываем в первый блок» (по букве X) и «снимаем на второй блок» (по букве Y)
   > с ОДНОЙ И ТОЙ ЖЕ верхушкой стека, синтаксически детерминировано (переходы различаются буквой),
   > но допускает семантическую ошибку: случайная буква первого рода после начала второй фазы
   > незаметно принимается за продолжение первой

2. **Mark the bottom of a counted block with its own stack symbol** (`B1` in the example below), so that
   popping the last symbol of the block is a move on a real letter into the accepting state, instead of an
   ε-move from a state that is also reached with other stack tops.
3. **No ε-move on a (state, top) pair that also has a letter move.** That is nondeterminism and is
   `refuted`. The only ε-move to an accept state that the checker repairs is one into a sink without
   outgoing transitions (the normalization above).
4. **Every transition must be justified by the language.** A transition that fires before any counting has
   started is the classic error: the live dcfl-04 automaton was deterministic after normalization and
   still accepted `b`:

   VERDICT_POLICY.md §3 R2′ (verbatim):

   > после нормализации он детерминирован, но неверен — лишний переход q0 —b→ q_b — и
   >   опровергается симуляцией словом «b» из полного перебора коротких слов; сэмплер такого слова не порождал

5. **Smoke-test before the full check.** `dpda_run` on ε, every single letter, the shortest members of
   each branch, words with one letter too many or too few, and "wrong first letter" words. Then
   `dpda_vs_ir`. Read every counterexample and repair the automaton; do not argue with the oracle.
6. **A real separator only.** A phase switch must be triggered by a symbol or factor that cannot occur
   at other positions (THEORY §1.9; dcfl-08: `aa` is not a separator).

## 4. Worked example from the spec

Language {$aⁿbⁿcᵐ | n,m ≥ 1} ∪ {d aᵐbⁿcⁿ | m,n ≥ 1} (eval dcfl-11; THEORY §1.9 proposes it as the
`stack_strategy` example). The first letter selects the mode, and the two modes are ordinary
left-to-right stack phases. The automaton below is copied unchanged from
`dcfl_system/tz_dcfl_agent_system.md` §5.2:

```json
{
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
```

Note the separate `qA_push` / `qA_pop` and `qB_push` / `qB_pop` states (rule 1) and the `B1` bottom
symbol (rule 2).

Checking it with the tools needs an oracle, and the eval IR `dcfl_system/examples/eval/dcfl-11.json`
has none: its `word_pattern` uses `$` and a trailing shared condition. With `$` renamed to an unused
letter `e` (in the alphabet, in the pattern and in the automaton) and the pattern written per branch,
`{e a^n b^n c^m | n,m >= 1} union {d a^m b^n c^n | n,m >= 1}`, `dpda_vs_ir` gives `bounded_pass`. The
report must then say that the check was made on the renamed alphabet.
