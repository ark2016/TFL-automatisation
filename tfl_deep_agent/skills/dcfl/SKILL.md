---
name: dcfl
description: >-
  Deterministic context-free languages (DCFL, ДМПА, детерминированные КС-языки): decide whether a
  language given by a set-builder description or by a grammar is DCFL, and prove it. Use it for the IR
  task types classify_dcfl, prove_dcfl, prove_non_dcfl and classify_and_prove_dcfl, for questions such
  as "является ли язык детерминированным", and inside LL tasks that argue "not DCFL, hence not LL"
  (THEORY §3.3 (A)). It covers the executable DPDA certificate (R2', dpda_vs_ir), closure reductions to
  a non-CFL language, the continuation lemma (L_$, haspref), Shallit's Myhill-Nerode theorem 4.7.4,
  Yu's two-word pumping lemma and inherent ambiguity, with their exact statements from docs/THEORY.md,
  the DCFL trap tasks of docs/EVAL_SET.md, and the trust each check can earn under VERDICT_POLICY.
allowed-tools:
  - validate_ir
  - describe_ir_schema
  - ir_to_text
  - sample_words
  - check_membership
  - dpda_run
  - dpda_vs_ir
  - closure_lookup
  - pattern_match
  - nerode_probe
  - dfa_vs_ir
  - regex_to_dfa
  - cyk
  - to_cnf
  - input_grammar_cfg
  - pump_check_cfl
  - list_theorems
  - get_theorem
metadata:
  classes: dcfl
  sources: >-
    docs/THEORY.md §1, §1.6-1.10, §2.0, §3.2-3.3; docs/VERDICT_POLICY.md §2-4 (R1-R4', R2');
    dcfl_system/tz_dcfl_agent_system.md §5-6; docs/EVAL_SET.md (DCFL); docs/EVAL_RESULTS.md
  supporting-files: traps.md dpda.md
---

# DCFL: is the language deterministic context-free?

Supporting files in this folder (read them with `read_file` when the step needs them):

- `/skills/dcfl/dpda.md` — the DPDA JSON contract, the R2' normalization, design rules that avoid the
  known automaton bugs, and a worked DPDA from the spec.
- `/skills/dcfl/traps.md` — every known DCFL trap (theory errors, eval trap tasks, live-run failures), with
  verbatim quotes.

Theory text in this file is quoted **verbatim** from `docs/THEORY.md` (the blockquotes; each one is
pinned by a test). For the full section, with proofs and worked examples, call `get_theorem` with the
section id (`1.1`, `1.2`, `1.3`, `1.4`, `1.5`, `1.6` … `1.10`, `2.0`, `3.3`). A `get_theorem` result is
reference material: it is never evidence and never raises trust. Never write a lemma statement from
memory, and never use a formulation that THEORY.md marks **[ошибка]**.

## 1. When this skill applies

- The question is about the class DCFL: "детерминированный КС-язык", "ДМПА", "LR(k)", "DCFL / non-DCFL".
  The IR class is `dcfl` (`validate_ir(cls="dcfl")`); its task types are `classify_dcfl`, `prove_dcfl`,
  `prove_non_dcfl`, `classify_and_prove_dcfl`, and the `input_format` / `language_spec.kind` is
  `set_builder` (Format 1) or `grammar` (Format 2).
- An LL(k) task needs "not LL for any k" for a **language**: route (A) of THEORY §3.3 proves "not DCFL"
  with this skill (then use the `ll` skill for the rest). Route (C) (DCFL but not LL) is in the `ll`
  skill, not here.
- A CFL task does **not** need this skill: being CFL is a weaker property. Conversely, "the language is
  CFL" (for example by `input_grammar_cfg`) says nothing about DCFL (THEORY §2.0: it "не устанавливает DCFL
  или LL(k)").

The class itself, as THEORY.md fixes it (acceptance mode matters for the DPDA you build):

THEORY.md §1 (verbatim):

> Здесь DCFL означает языки ДМПА с **принятием по заключительному состоянию после чтения всего
> слова**, с разрешёнными ε-переходами. Принятие по пустому стеку без концевого маркера задаёт
> более узкий класс: такой детерминированный язык префиксно-свободен.

Inclusions you may cite: DCFL ⊆ CFL (THEORY §1.3: DCFL "лежат в CFL"); regular languages are DCFL:

THEORY.md §3.2 (verbatim):

> ⋃_k LL(k) ⊊ DCFL = LR(1)

together with "REG ⊊ LL(1) (языки)" from the same section.

## 2. Ground rules

1. **Analyze the language, not the grammar** (Format 2). An ambiguous grammar, a grammar that is not
   LR(k), or a failed attempt to build a DPDA is not evidence that the language is not DCFL
   (THEORY §1.5, §1.10; VERDICT_POLICY R1: "провал ≠ опровержение").
2. **A `dcfl` verdict needs an executable DPDA** that passes `dpda_vs_ir` (VERDICT_POLICY R2'). A verbal
   stack strategy, a closure argument or an LR(1)-grammar sketch alone keeps the claim at `well_formed`
   or below.
3. **A `non_dcfl` verdict needs a destructive argument** (sections 4.2–4.6 below) whose concrete witnesses
   the oracle does not refute. Without one the verdict is `inconclusive` (verdict null, confidence ≤ 0.40).
4. **Every oracle check is relative to the IR you built.** If the IR is wrong, the checks are worthless:
   show the IR to the human (`ir_to_text`) before relying on it. An oracle answer `null` is "unknown",
   never "not in L".
5. **Hints are not evidence.** `pattern_match`, `closure_lookup`, `sample_words` and `get_theorem` are
   `not_verified` by design. `pattern_match` is the old pipeline's advisory classifier and is wrong on
   known traps (it suggests `non_dcfl` for the DCFL {aⁿbⁿcᵐ}, eval dcfl-04).
6. **Your own confidence never raises the trust.** The evidence block and the trust labels of the report are
   computed by code from the tool results; cite tool results, do not restate them as stronger.

## 3. Decision procedure

**Step 0 — IR and oracle.** Build the DCFL IR (`describe_ir_schema(cls="dcfl")` for the shape and
examples). Run `validate_ir(cls="dcfl", ir, check_tools=true)`: it reports whether a membership oracle
and a Lean statement can be built. Without an oracle, `dpda_vs_ir`, `check_membership`, `nerode_probe`
and `pump_check_cfl` are unsupported and nothing can rise above `well_formed`. Oracle-friendly IR forms
(measured on the eval IRs; see `traps.md` §"IR and oracle"):

- exponent notation in `word_pattern`, one condition list per union branch:
  `{a^n b^n | n >= 0} union {a^n b^(2n) | n >= 0}`;
- a concatenation of declared `variables` with regular `domain`s plus `length_cmp` constraints
  (`u₁au₂` with |u₁| ≤ |u₂|);
- `kind: grammar` (exact CYK oracle).

The exponent oracle answers unknown for words longer than 40. A trailing shared condition
(`{…} union {…}, n,m >= 1`) and the letter `$` in the alphabet are **not** parsed; restate the branches
and rename `$` to an unused letter, and say so in the report.

**Step 1 — Look at the language.** `ir_to_text` (paraphrase plus labelled examples, needed for HITL 1),
`sample_words` (reference only), and `check_membership` on words you pick yourself: the shortest words,
words at the boundary of each condition, words that sit in two branches of a union.

**Step 2 — Form a hypothesis by asking where a DPDA would have to guess.** A DPDA reads left to right and
cannot undo a choice. Typical sources of an unavoidable guess: a union whose branches share a long prefix
but need different stack use (dcfl-05 {aⁿbⁿ} ∪ {aⁿb²ⁿ}); a mirror point without a marker (dcfl-03
{wwᴿ}); a condition on a position that is only known at the end (dcfl-16, "the last a"). A guess that is
resolved by a visible letter is not a real guess (dcfl-02 {wcwᴿ}, dcfl-11 where the first letter selects
the mode). Use `pattern_match` and `closure_lookup(ir=...)` only as a list of things to try. Check the
catalog: THEORY §3.3 ("Известные факты о конкретных языках") and §3.4, and the DCFL table of
`docs/EVAL_SET.md`.

**Step 3 — Constructive attempt (`dcfl`).** Build an explicit DPDA (`/skills/dcfl/dpda.md`), smoke-test it
with `dpda_run` on hand-picked words, then run `dpda_vs_ir(dpda, ir)`. Fix every counterexample and rerun.
`bounded_pass` is the target. The verdict gate counts only two things as the basis of a `dcfl` verdict:
`dpda_vs_ir` at ≥ `bounded_pass`, or a `lean_check` result `proved` (step 7). `regex_to_dfa` and
`dfa_vs_ir(cls="dcfl")` are supporting evidence only and are never the basis. If the language is regular
(§3.2 above: REG ⊊ LL(1) ⊊ DCFL), turn the DFA into a DPDA that never changes the stack and run it through
`dpda_vs_ir`. Use one stack symbol `Z0` and `initial_stack: ["Z0"]`, write every δ(q, a) = p as
`{"from": q, "read": a, "top": "Z0", "to": p, "push": ["Z0"]}`, and keep the same accept states. On a unary
alphabet `dpda_vs_ir` decides only 11 words and stays `well_formed` (dcfl-17). Without a Lean proof the gate
then caps the result at `inconclusive` (≤ 0.40). Report that, cite the DFA check as support, and name the
unary coverage limit as the reason. Treating `dfa_vs_ir(cls="dcfl")` as a DPDA certificate would be a
VERDICT_POLICY change for the user to decide.

**Step 4 — Destructive attempt (`non_dcfl`).** Try the methods in this order. The first two end in a
non-CFL statement the oracle can test. Of these, only a closure reduction by complement and/or ∩ REG also
has a Lean route (section 6): the `tfl-lean4` image has no DCFL closure lemma for inverse homomorphism,
MIN / MAX, left quotient, haspref or L_$.

1. closure reduction to a non-CFL language (4.2): complement, ∩ REG, inverse homomorphism, MIN / MAX,
   left quotient by a fixed word;
2. the continuation lemma (4.3): L_$ ∩ R or haspref(L) ∩ R not CFL;
3. Shallit's theorem 4.7.4 (4.4), only when the dead class is empty;
4. Yu's two-word lemma (4.5), with both conditions refuted for every admissible split;
5. inherent ambiguity (4.6), only for a language known to be inherently ambiguous.

For every non-CFL sub-claim, prove it with the CFL pumping lemma or Ogden's lemma in the quantifier order
of THEORY §2.0 (section 4.7) and check it with `pump_check_cfl` on an IR of the derived language.

**Step 5 — Both sides at once.** If a DPDA passes `dpda_vs_ir` and a destructive argument also stands, there
is a contradiction (VERDICT_POLICY R3). Cross-check (R3'): run the destructive witnesses (w, w′ of Yu, the
pairs u, v and suffixes of Shallit, the examples of the derived language) through `dpda_run` and through
`check_membership`. A disagreement refutes one side. If both survive and one side is `verified` (a Lean
`proved` result for this IR, step 7; the determinism check of `dpda_run` does not count), that side keeps
the verdict with ceiling 0.85 (VERDICT_POLICY R3). If neither side is `verified`, report `inconclusive`
with confidence ≤ 0.50 and both arguments: `bounded_pass` against `well_formed` does not settle the
dispute.

**Step 6 — Neither side.** Report `inconclusive` (confidence ≤ 0.40) and say what is missing. Do not turn
"I could not build a DPDA" into `non_dcfl`, or "no counterexample found" into `dcfl`.

**Step 7 — Optional Lean.** If the `lean_statement` / `lean_check` tools are available and the IR has a
Lean statement (`validate_ir(..., check_tools=true)` → `lean_statement`), a `proved` result is the only
way to `verified` (0.98). See the `lean` skill and section 6.

## 4. Proof methods (statements verbatim from docs/THEORY.md)

### 4.1 Constructive: an executable DPDA (VERDICT_POLICY R2')

The certificate is the automaton itself, in the JSON contract of `dpda.md`. Determinism is checked
completely and syntactically; the language is checked by simulation against the task oracle on all short
words plus sampled words. Write the human-readable stack strategy (phases, what is pushed, what switches
the phase) next to it, but it is the DPDA that gets checked. For machine-built certificates (for example a
determinized height-deterministic automaton) THEORY says:

THEORY.md §1.10 (verbatim):

> Для сертификата по политике R2′ достаточно машинно-построенного ДМПА: детерминизм проверяется
> синтаксически, язык — симуляцией против оракула (`bounded_pass`, потолок 0.85).

Constructive closure facts (complement of a DCFL, DCFL ∩ REG, inverse homomorphism; section 4.2) explain
**why** a DPDA exists, but the verdict still rests on the DPDA you submit to `dpda_vs_ir`.

### 4.2 Closure reductions

THEORY.md §1.4 is marked **[источники и границы указаны отдельно]**, not [проверено]: the closure list
below is sourced per item in that section, and THEORY.md says the original [GG] has not been read in
full ("Оригинал [GG] пока не прочитан целиком; его атрибуция не означает проверки исходного
доказательства"). Cite it as §1.4 and prefer complement and inverse homomorphism (confirmed by the [Sh, IV 30]
table) and ∩ REG, MIN and left quotient by a fixed word (short arguments in §1.4: the DPDA × DFA
product, the proper-accepted-prefix flag, the start from the configuration reached on the word).
Right regular quotient and MAX rest on the longer configuration-test argument of §1.4.
`closure_lookup(operation=...)` returns
the same statements with the section text.

THEORY.md §1.4 (verbatim):

> Замкнуты: дополнение относительно фиксированного Σ*, ∩ REG, обратный гомоморфизм
> (включая стирающий); правое частное L/R = {x | ∃y ∈ R: xy ∈ L} при регулярном R;
> MIN(L) = {x ∈ L | ни один собственный префикс x не лежит в L};
> MAX(L) = {x ∈ L | ни одно собственное продолжение x не лежит в L};
> обратное отображение **детерминированного последовательного** GSM, в том числе со стирающим
> выходом; haspref и L_$ (п. 1.3).

THEORY.md §1.4 (verbatim):

> Левое частное на **фиксированное слово** также сохраняет DCFL (старт из достигнутой фиксированной
> конфигурации). Левое частное на произвольный регулярный язык в общем случае не сохраняет DCFL:

THEORY.md §1.4 (verbatim):

> Не замкнуты: ∪, ∩ (DCFL ∩ DCFL), конкатенация, звезда, обращение, гомоморфизм
> (в т.ч. ε-свободный).

The destructive use of the ∩ REG closure, as the spec states it (DCFL ⊆ CFL, THEORY §1.3):

tz_dcfl_agent_system.md §5.3 (verbatim):

> - Деструктивно: L ∩ R не КС для некоторого регулярного R → L не DCFL.
>   (Потому что DCFL ∩ REG ⊆ DCFL ⊆ CFL.)

Same pattern: if op(L) is not CFL for a closure operation op from the list above (complement, MIN, an
inverse homomorphism image, …), then L is not DCFL. Eval examples: dcfl-19 and dcfl-20 (complement ∩ REG),
dcfl-12 (MIN, THEORY §1.1).

A second pattern: op(L) is **CFL but not DCFL**, shown by a DCFL method (Shallit, Yu, the continuation
lemma), and op preserves DCFL; then L is not DCFL. Here "op(L) is not CFL" is false, so the first pattern
does not apply. Eval example dcfl-09, where L ∩ aΣ* = a·L₂ and L₂ = {v a bᑫ | |v| ≥ q} is context-free:

THEORY.md §1.7 (verbatim):

> Значит эквивалентные слова имеют одну длину, и **все классы конечны** ⇒ L₂∉DCFL.

THEORY.md §1.7 (verbatim):

> DCFL замкнуты относительно ∩REG и левого частного
> на слово (a⁻¹L — тот же ДМПА, стартующий из конфигурации после чтения a) ⇒ **L ∉ DCFL**.

Non-closure under ∪ is an existential statement: it never shows that a **given** union is not DCFL
(dcfl-11, a union of two DCFLs, is DCFL).

The weakest link of a reduction is the claimed equality "op(L) = {…}". THEORY §2 and §5 record false
equalities of exactly this kind. Check it: `check_membership` on the original IR for words that the equality
puts in and out of the derived language, and say in the report which words you checked.

### 4.3 The continuation lemma (L_$, haspref)

THEORY.md §1.3 (verbatim):

> **Лемма.** Пусть L — DCFL, $ ∉ Σ. Тогда
> - (a) haspref(L) = {xy | x ∈ L, xy ∈ L, y ≠ ε} — DCFL;
> - (b) L_$ = {x$y | x ∈ L, xy ∈ L} — DCFL.

THEORY.md §1.3 (verbatim):

> **Применение.** L_$ ∩ R ∉ CFL (для регулярного R) ⇒ L ∉ DCFL (DCFL замкнуты относительно ∩REG и
> лежат в CFL).

Worked examples in §1.3 ({aⁿbⁿ} ∪ {aⁿb²ⁿ}, {aⁿbⁿ} ∪ {aⁿbᵐcⁿ}) and §1.6 (exam_01, with an exact computation
of K = L_$ ∩ R). Membership in L_$ is checked through L: x$y ∈ L_$ iff x ∈ L and xy ∈ L, so check both
with `check_membership` on the original IR.

### 4.4 Shallit's theorem 4.7.4 (Myhill–Nerode classes)

THEORY.md §1.2 (verbatim):

> **Теорема 4.7.4 [Sh].** Для конечного непустого алфавита Σ, если L ⊆ Σ* — DCFL, то хотя бы один класс эквивалентности
> Майхилла–Нероуда языка L (x ~_L y ⇔ ∀z: xz ∈ L ⇔ yz ∈ L) **бесконечен**.

THEORY.md §1.2 (verbatim):

> **Контрапозиция (рабочая форма).** Если **все** классы Нероуда языка L конечны, то L ∉ DCFL.
> Стандартный способ: показать, что любые два различных слова u ≠ v различимы (∃w: ровно одно
> из uw, vw лежит в L) — тогда все классы одноэлементны.

The precondition that decides whether the method applies at all:

THEORY.md §1.2 (verbatim):

> **Ограничение метода — «мёртвый» класс.** D = {x | ∄z: xz ∈ L} — тоже класс Нероуда. Если D
> бесконечен (например, L ⊆ a*b*: все слова вне Pref(a*b*) мертвы), теорема выполняется автоматически
> и **ничего не даёт**. Поэтому перед применением нужно проверить, что D конечен, а это значит D = ∅:

So: first prove D = ∅ (every word has a continuation into L) by an argument, then show that **every**
Nerode class is finite. Separating arbitrary u ≠ v by a suffix w(u, v) (all classes are singletons, the
standard way above) is sufficient but not necessary: it is enough that each class is finite, for example
that equivalent words have the same length. THEORY §1.7 does this for L₂ = {u₃au₄ | |u₃| ≥ |u₄|} (eval
dcfl-16): z = abᵐ⁺¹ separates any words of lengths m < n. This holds for L₂, not for dcfl-09's L: L has the
infinite class {bᵖabᵖ | p ≥ 0}, so dcfl-09 is handled by the reduction L ∩ aΣ* = a·L₂ plus left quotient
by a (§4.2, second pattern), not by Shallit on L itself. One infinite family of pairwise distinguishable
words is not enough (traps.md T3).
If D ≠ ∅ the method is `not_applicable`; stop and use another
method (live run dcfl-21: the prover itself wrote "D is infinite" and still claimed non-DCFL, see
`traps.md`). Tools: `nerode_probe(cls="dcfl", pairs=[...])` or `family=...` for concrete pairs and the
claimed suffix; `check_membership` with `expected: true` on a concrete continuation for each prefix you
tested. A failed continuation search is not a proof that a word is dead (THEORY §1.2, the {aⁿ | n ≥ 20}
example).

### 4.5 Yu's two-word pumping lemma

THEORY.md §1.1 (verbatim):

> **Лемма.** Пусть L — DCFL. Существует константа p такая, что для любых двух слов
> xy ∈ L и xz ∈ L с |x| > p и одинаковыми первыми буквами у y и z (⁽¹⁾y = ⁽¹⁾z, оба непусты)
> выполнено хотя бы одно из условий:
>
> - **(1) — накачка внутри префикса.** x = x₁x₂x₃x₄x₅, |x₂x₄| ≥ 1, |x₂x₃x₄| ≤ p, и для всех i ≥ 0
>   x₁x₂ⁱx₃x₄ⁱx₅·y ∈ L **и** x₁x₂ⁱx₃x₄ⁱx₅·z ∈ L.
>   Пара (x₂, x₄) может стоять **в любом месте** x; ограничена только длина окна x₂x₃x₄.
> - **(2) — синхронная накачка с суффиксами.** x = x₁x₂x₃, y = y₁y₂y₃, z = z₁z₂z₃,
>   |x₂| ≥ 1, |x₂x₃| ≤ p (x₂ лежит в последних p символах x), и для всех i ≥ 0
>   x₁x₂ⁱx₃·y₁y₂ⁱy₃ ∈ L **и** x₁x₂ⁱx₃·z₁z₂ⁱz₃ ∈ L.
>
> Отрицание (доказательство L ∉ DCFL): для всякого p указать xy, xz ∈ L, |x| > p, ⁽¹⁾y = ⁽¹⁾z,
> и показать, что **и (1), и (2) нарушены** — для каждого допустимого разбиения найти i, при котором
> хотя бы одно из двух накачанных слов выходит из L.

Condition (1) is a **pair** (x₂, x₄) anywhere in x. The single-factor version is false (it rejects the
DCFL {aⁿbⁿcᵐ}; `traps.md`). Worked examples: §1.1 ({aⁿbⁿ} ∪ {aⁿb²ⁿ}), §1.8 (exam_03). The pipeline contract
also asks for concrete instances: w = xy, w′ = xz at n = p + 2 for p ∈ {2, 3} (VERDICT_POLICY §4
dcfl/dcfl_pumping). Check w, w′ ∈ L and every word your argument declares outside L with
`check_membership(..., expected=...)`. Closing all splits at p = 2, 3 is only a necessary condition: the
DCFL exam_04 closes them too.

### 4.6 Inherent ambiguity

THEORY.md §1.5 (verbatim):

> Существенно неоднозначный ⇒ не UnambCF ⇒ не DCFL (у DCFL есть LR(1)-грамматика, LR(k)-грамматики
> однозначны). Пример [Sh, Thm 4.4.1]: {aⁱbʲcᵏ | i = j ∨ j = k}.
> Неоднозначность **грамматики** не говорит ничего о языке (Format 2).

Use it only for a language that is known to be inherently ambiguous (eval dcfl-06 is the textbook case);
no tool checks inherent ambiguity. If the branches of the union are unambiguous CFLs, check with
`check_membership` before you use it that they really overlap: a disjoint union of two unambiguous CFLs is
unambiguous. In dcfl-10 both branches are unambiguous (DCFL) and disjoint, so the language is not inherently
ambiguous (THEORY §1.8).

### 4.7 The non-CFL sub-proof

Methods 4.2 and 4.3 end in "this derived language is not CFL". Prove that with the CFL pumping lemma or
Ogden's lemma, in this quantifier order:

THEORY.md §2.0 (verbatim):

> У КС-языка существует p ≥ 1 такое, что **для каждого** z ∈ L, |z| ≥ p, **существует** допустимое
> разбиение z=uvwxy, |vwx| ≤ p, |vx| ≥ 1, с uvⁱwxⁱy ∈ L **для всех** i ≥ 0 [R-Pump, с. 7].
> Не-КС доказательство должно отрицать именно этот порядок: ∀p ∃z ∀разбиение ∃i с выходом из L.

Write an IR for the derived language (`cls="cfl"`) and check your pumping evidence with `pump_check_cfl`
(claim mode); that check is at most `well_formed`. The `cfl` skill has the details.

## 5. Tools by step

| Step | Tool | What it gives you | Trust it can earn |
|---|---|---|---|
| 0 | `describe_ir_schema(cls="dcfl")` | IR shape, task types, examples | reference |
| 0 | `validate_ir(cls="dcfl", check_tools=true)` | errors, IR fingerprint, oracle / Lean availability | `well_formed` (structure only) |
| 1 | `ir_to_text(cls="dcfl")` | deterministic paraphrase + labelled words (HITL 1) | `well_formed` |
| 1 | `sample_words(cls="dcfl")` | words in / not in L | reference only (`not_verified`) |
| 1, 4 | `check_membership(cls="dcfl", words, expected)` | membership of your witnesses | `bounded_pass` for those words / `refuted` |
| 2 | `pattern_match`, `closure_lookup` | advisory patterns, closure facts with §1.4 text | `not_verified` |
| 3 | `dpda_run(dpda, words)` | normalization notes, determinism, runs on THIS automaton | `verified` (determinism) / `refuted` |
| 3 | `dpda_vs_ir(dpda, ir)` | R2' check against the task language | `bounded_pass` / `refuted` / `well_formed` |
| 3 | `regex_to_dfa`, `dfa_vs_ir(cls="dcfl")` | regular languages: DFA vs the task oracle (supporting evidence only; the gate does not count it for a `dcfl` verdict) | `bounded_pass` / `refuted` (not a verdict basis) |
| 4 | `nerode_probe(cls="dcfl")` | separating suffixes for concrete pairs / a family | `bounded_pass` for those pairs / `refuted` |
| 4 | `pump_check_cfl(cls="cfl", ir=<derived IR>)` | pumping / Ogden evidence for a non-CFL sub-claim | at most `well_formed` / `refuted` |
| 4 | `cyk`, `to_cnf` | membership in a grammar you built (not the task language) | `verified` for that grammar |
| 0 (Format 2) | `input_grammar_cfg` (on a copy of the IR with a CFL task type) | the language is CFL by definition | `verified`, for CFL only |
| any | `list_theorems(cls="dcfl")`, `get_theorem(id)` | verbatim THEORY sections | reference |
| 7 | `lean_statement`, `lean_check` (when registered) | Lean proof of `is_DCF L` / `¬ is_DCF L` | `proved` → `verified` |

## 6. What makes a claim verified, bounded or only well-formed

Caps (VERDICT_POLICY §2): `verified` 0.98, `bounded_pass` 0.85, `well_formed` 0.55, `not_verified` 0.40
(verdict only `inconclusive`), unresolved contradiction 0.50.

| Claim | Evidence | Trust (ceiling) |
|---|---|---|
| "this DPDA is deterministic" | `dpda_run`, no conflict after normalization (a `dpda_vs_ir` record alone gives at most `bounded_pass`, and `refuted` if the DPDA disagrees with the oracle, so cite `dpda_run`) | `verified` (property of the automaton only) |
| `dcfl` | `dpda_vs_ir` agrees on ≥ 30 decided words | `bounded_pass` (0.85) |
| `dcfl` | DPDA agrees on fewer than 30 decided words (e.g. a unary alphabet), or no oracle | `well_formed` |
| `dcfl` | stack strategy in words, closure argument, LR(1) sketch, no DPDA | ≤ `well_formed`; the verdict is not admissible (R2') |
| `dcfl` | Lean `proved` for `is_DCF L` | `verified` (0.98) |
| `non_dcfl` by closure / L_$ + non-CFL pumping | written proof + `pump_check_cfl` (claim mode) on an IR of the derived language + equality examples by `check_membership` | `well_formed` (0.55) only when the verdict gate admits the derived-IR result, which today means HITL 1 off and no IR fingerprint in the report. With HITL 1 on (the default) the verdict is bound to the task IR approved there. The derived-IR result is then a result "for another IR" and is excluded, so the verdict is `inconclusive` (≤ 0.40) unless Lean proves `¬ is_DCF L` (last row). Do not send the derived IR to HITL 1 as the task's formalization: a main-thread `validate_ir` on it does that, and the verdict is about L, not op(L). |
| `non_dcfl` by Shallit | `nerode_probe(cls="dcfl")` on the task IR separates the checked pairs, and a continuation into L is confirmed for each checked prefix | `well_formed` (0.55), never `bounded_pass`: the gate counts `nerode_probe(cls="dcfl")` as at most `well_formed` (finitely many pairs, not "every class finite"). |
| `non_dcfl` by Yu | written proof + instances w, w′ at p ∈ {2, 3}, their membership checked by `check_membership` | No tool checks a Yu argument, and `check_membership` is not destructive evidence for the gate, so the verdict is `inconclusive` (`not_verified`, ≤ 0.40) unless Lean proves `¬ is_DCF L`. The instances are still required, and an instance the oracle refutes refutes the argument. |
| `non_dcfl` by inherent ambiguity | written proof, overlap words checked by `check_membership` | No tool basis: `inconclusive` (≤ 0.40) unless Lean proves `¬ is_DCF L` (only necessary conditions are checkable). |
| `non_dcfl` | Lean `proved` for `¬ is_DCF L`: complement and/or ∩ REG closure (`DCF_closedUnderComplement`, `DCF_inter_regular`), then DCFL ⊆ CFL (`TflLean.isContextFree_of_isDCF`) and a proof that the derived language is not CF (reference: `agent_system/docker/tfl_lean/TflLean/Examples/AiBjCkNeq_NotDCF.lean`). ¬ CF of L itself works only when L is not context-free, which is not the case for any `non_dcfl` task in docs/EVAL_SET.md | `verified` (0.98) |
| anything | a counterexample from an exact oracle, a determinism conflict, a witness that is not in L, Shallit with a non-empty dead class claimed as success | `refuted` |

Finite checks never prove the general statement: all splits closing at p = 2, 3, or ten separated Nerode
pairs, are necessary conditions, not proofs (THEORY §2.0: "Конечная проверка … — диагностика").

## 7. Traps (summary; details and quotes in `/skills/dcfl/traps.md`)

- Yu's condition (1) with a single factor: false, rejects {aⁿbⁿcᵐ} (dcfl-04).
- Course witnesses that do not refute Yu (dcfl-12): condition (2) holds; use MIN (THEORY §1.1).
- "Infinitely many Nerode classes" or one pairwise-distinguishable family: not an argument ({aⁿbⁿ} is DCFL).
- Shallit with a non-empty dead class (dcfl-21, dcfl-08): the theorem gives nothing.
- An ambiguous / non-LR(k) grammar (dcfl-17, dcfl-21): nothing follows about the language.
- Disjoint union of unambiguous CFL branches (dcfl-10): not inherently ambiguous.
- "aa" as a phase separator (dcfl-08): it occurs inside w and v.
- The pair dcfl-15 (DCFL) / dcfl-16 (not DCFL), and the suffix bᵗa that distinguishes nothing (THEORY §1.7).
- DPDA bugs: one state for push and pop phases ("aababbc"), a stray transition accepting "b" (live dcfl-04),
  an ε-move to an accept state shared by several stack tops, empty-stack acceptance for a language that is
  not prefix-free.
- Closure-list errors: union / intersection / reversal / homomorphism / left quotient by a regular
  language do **not** preserve DCFL; "SLR(1) for every DCFL" has no source (THEORY §1.9).
- IR forms without an oracle (`$`, a trailing shared condition) and the unary-alphabet coverage limit of
  `dpda_vs_ir`.
