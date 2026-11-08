# DCFL traps

Part of the `dcfl` skill. Each entry: what goes wrong, where it is recorded, what to do instead.
Blockquotes are verbatim from `docs/THEORY.md` or `docs/VERDICT_POLICY.md` (pinned by a test).
Course material ([Курс]) is not a source of truth: THEORY.md records errors in its solutions.

## Theory errors (formulations marked [ошибка] in THEORY.md)

### T1. Yu's condition (1) with a single factor

THEORY.md §1.1 (verbatim):

> Такая «лемма» ложна: она отвергает DCFL {aⁿbⁿcᵐ}. Контрпример-ловушка:
> x = aⁿbⁿ, y = c, z = cc. Одиночная накачка bʲ и синхронная накачка bʲ ~ cʲ ломают оба слова, но
> настоящее условие (1) выполняется парой (x₂, x₄) = (a, b) на границе блоков:
> a^{n-1}·aⁱ·bⁱ·b^{n-1}·c ∈ L.

Use the two-factor condition (1) of SKILL.md §4.5. Eval dcfl-04 ({aⁿbⁿcᵐ}, `dcfl`) is the trap task.
`pattern_match` still suggests `non_dcfl` for it: do not follow it.

### T2. Course witnesses that satisfy Yu (dcfl-12)

THEORY.md §1.1 (verbatim):

> w = aⁿbⁿcⁿ⁺¹, w′ = aⁿbⁿ⁺¹cⁿ⁺¹, x = aⁿ **не опровергают лемму Ю**.
> При n ≥ 2 условие (2) выполнено: x₂ = a, y₂ = b, z₂ = ε; первая накачанная строка
> всегда имеет i = j, вторая — j = k.

The language is still not DCFL, by MIN (THEORY.md §1.1): "MIN(S) ∩ a⁺b⁺c²c* = {aⁱbʲcʲ | i > j ≥ 2} = K".
When a Yu argument does not close, try a closure reduction before giving up.

### T3. "Infinitely many Nerode classes" / one distinguishable family

THEORY.md §1.2 (verbatim):

> Ложна: для DCFL {aⁿbⁿ} и M = {aⁿ}
> любые два элемента различимы суффиксом bᵏ. Совет «постройте M, в котором любые два элемента
> различимы» — та же ошибка: одного бесконечного семейства недостаточно, нужны **все** классы.

Shallit needs **every** Nerode class finite. Separating every pair u ≠ v (all classes singletons) is the
standard sufficient way (THEORY.md §1.2), not a requirement. For L₂ = {u₃au₄ | |u₃| ≥ |u₄|} = {v a bᑫ | |v| ≥ q}
(eval dcfl-16) it is enough that equivalent words have the same length: for |u| = m < n = |v| the suffix
z = abᵐ⁺¹ gives uz ∉ L₂ and vz ∈ L₂, which already makes every class finite. This holds for L₂, not for the
language of dcfl-09. L = {bᵖ a v a bᑫ | |v| ≥ p + q} has the infinite class {bᵖabᵖ | p ≥ 0}, because
bᵖabᵖ·z ∈ L iff z = z′abᑫ with |z′| ≥ q, which does not depend on p. So Shallit does not apply to L directly.
THEORY.md §1.7 reaches dcfl-09 only through L ∩ aΣ* = a·L₂ and the left quotient by the word a (DCFL is closed
under ∩REG and under left quotient by a word; SKILL.md §4.2, second pattern).

THEORY.md §1.7 (verbatim):

> Значит эквивалентные слова имеют одну длину, и **все классы конечны** ⇒ L₂∉DCFL.

A separating suffix must put exactly one of the two words in L. Check both with `nerode_probe` or
`check_membership`. A suffix that puts every word in L separates nothing:

THEORY.md §1.7 (verbatim):

> Суффикс bᵗa из старого промпта не различает ничего:
> любое слово, оканчивающееся на a, лежит в L₂.

The dead class D is one of the classes and a non-empty D is infinite (T4), so "every class finite"
includes D = ∅; prove that first.

### T4. Shallit with a non-empty dead class

A non-empty D is infinite (it is closed under right extension), so the theorem holds automatically.
Recorded cases: dcfl-08 / exam_01 (THEORY.md §1.6: "Теорема 4.7.4 напрямую не применима: bbΣ* —
бесконечное множество мёртвых слов."), dcfl-21 / exam_04 (THEORY.md §1.10). The policy treats "D is
infinite" together with a success claim as `refuted`:

VERDICT_POLICY.md §4 (verbatim):

> `infinite` при
>   status success ⇒ `refuted` (доказательство само признаёт технику неприменимой).

### T5. A finite search does not find dead words

THEORY.md §1.2 (verbatim):

> Конечный поиск продолжений не устанавливает x ∈ D. Например, в регулярном языке {aⁿ | n ≥ 20} над {a}
> мёртвых слов нет, но для ε недостаточно перебора продолжений длины ≤ 6.

A `check_membership` / `nerode_probe` run that finds no continuation is "unknown", not "dead". D = ∅
needs an argument (for example: every u continues by `u·a`, THEORY.md §1.7).

## Grammar is not language (Format 2)

### T6. An ambiguous or non-LR(k) grammar (dcfl-17, dcfl-21)

THEORY.md §1.10 (verbatim):

> G не LR(k) ни при каком фиксированном k — из этого **ничего не следует о
> языке**: класс DCFL определяется существованием какого-нибудь ДМПА (равносильно — какой-нибудь LR(1)-грамматики [Knuth]),
> а не свойствами данной грамматики.

- dcfl-17: S → aS | Sa | a is ambiguous, but L = a⁺ is regular, hence DCFL (THEORY §3.2). Submit the DFA as
  a DPDA that never changes the stack to `dpda_vs_ir` (SKILL.md step 3). Over one letter it is checked on only
  11 words and stays `well_formed`. `dfa_vs_ir(cls="dcfl")` reaches `bounded_pass` on 41 words, but it is
  supporting evidence only and the gate does not count it. Without a Lean `proved` result the honest outcome
  is `inconclusive` (≤ 0.40). It is never `non_dcfl`.
- dcfl-21 / exam_04 (S → aSSb | ba | Ab, A → aAb | a): DCFL, proved in THEORY.md §1.10 by a profile
  NPDA plus height-determinism; the certificate has 5835 transitions and does not fit `dpda_vs_ir`
  (limit 2000). No destructive method can work here, and THEORY.md says why:

THEORY.md §1.10 (verbatim):

> лемма Ю (§1.1) —
>   контрпримера нет по теореме: L ∈ DCFL, значит заключение леммы выполнено для любых xy, xz ∈ L

Live runs on dcfl-21 produced wrong `non_dcfl` arguments twice (VERDICT_POLICY §1: "dcfl-21 дважды:
shallit, затем dcfl_pumping"). And the Yu check at small p closed every split on this DCFL:

VERDICT_POLICY.md §4 (verbatim):

> лемма Ю
>   гарантирует разбиения лишь при p ≥ p₀, и при малых p условия закрываются и у DCFL-языков (прецедент: exam_04 — DCFL по
>   THEORY.md §1.10, а ложное доказательство non_dcfl при p ∈ {2,3} закрывалось полностью).

## Ambiguity

### T7. A disjoint union of unambiguous branches is not inherently ambiguous (dcfl-10)

THEORY.md §1.8 (verbatim):

> Объединение двух дизъюнктных однозначных КС-языков однозначно ⇒ L **не** существенно
> неоднозначен; пример в `inh_ambiguity.md` («пересечение ветвей aⁿbⁿa») ложен: aⁿbⁿa лежит только во второй ветви.

dcfl-10 is not DCFL by Yu (THEORY.md §1.8), not by ambiguity. Its two branches are unambiguous CFLs (both DCFL), so
their disjointness makes the union unambiguous. Before an ambiguity argument over a union of unambiguous branches, show
with `check_membership` that the claimed overlap words lie in both branches. Disjointness alone proves nothing when a
branch is itself inherently ambiguous.

## Wrong DCFL constructions

### T8. A "separator" that occurs elsewhere (dcfl-08)

THEORY.md §1.6 (verbatim):

> Пример в `stack_strategy.md` объявляет «aa» разделителем фаз — но «aa» встречается внутри w (блоки a⁺b)
> и внутри v (пары aa), поэтому границу фаз ДМПА не видит; рассуждение несостоятельно.

dcfl-08 is not DCFL (continuation lemma, THEORY.md §1.6), so every DPDA for it is wrong on some word, but
`dpda_vs_ir` is a bounded check (VERDICT_POLICY R2′: every word up to length k ≤ 10, plus sampler words) and
need not find that word. L has only three words of length ≤ 10: `abaaba`, `ababaababa`, `abaaaaaaba`. A DPDA
that takes the first `aa` as the centre is refuted on `abaaaaaaba`, but a DPDA that accepts exactly those three
words gets `bounded_pass`. Build the automaton and run `dpda_vs_ir` rather than arguing in words. A
`bounded_pass` here does not make the language DCFL: next to the proof in THEORY.md §1.6 it is a contradiction
to report (VERDICT_POLICY R3).

### T9. DPDA bugs caught only by simulation

- A shared push/pop state accepted `aababbc` (tz §5.2, see `dpda.md` rule 1).
- The live dcfl-04 automaton was deterministic but accepted `b` (`dpda.md` rule 4); the sampler never
  produced that word, the exhaustive short-word enumeration of `dpda_vs_ir` did.
- Empty-stack acceptance is prefix-free only (THEORY.md §1): use `final_state` for a language with a
  word and its proper prefix.

## Closure-list errors

### T10. Union of DCFLs, other non-closures

Non-closure under ∪, ∩, concatenation, star, reversal and homomorphism (THEORY.md §1.4) is about the
class, not about your language: dcfl-11 is a union of two DCFLs and is DCFL, because the first letter
selects the branch. A left quotient by a **regular** language and the inverse of a nondeterministic GSM do
not preserve DCFL (THEORY.md §1.4); only a left quotient by a fixed word and the inverse of a
deterministic sequential GSM do.

### T11. Unsourced claims

THEORY.md §1.9 (verbatim):

> заметки `dcfl_system`: «For every DCFL there exists an SLR(1) grammar» → без источника не утверждать;
>   заменить на «у каждого DCFL есть LR(1)-грамматика; L$ — LR(0)»:

THEORY.md §1.9 (verbatim):

> `pattern_db.py`: pattern `reversal_of_dcfl` («L^R where L is DCFL but L^R is not» → shallit) — это не метод
>   доказательства; пометить как эвристику без метода или удалить.

`pattern_match` is backed by that pattern database: its hints are `not_verified` (SKILL.md ground rule 5).

### T12. A false equality in a reduction

The derived language op(L) or L_$ ∩ R is usually stated as an explicit set; that equality is the step
that breaks. THEORY.md §0 (REG, "L ∩ a*b* = {aⁿbⁿ}"), §2 (CFL) and §5 (a live closure_reduction run)
record such errors:

THEORY.md §2 (verbatim):

> L ∩ a⁺b⁺a⁺c⁺ = {aⁿbᵐaⁿcᵏ}. На самом деле
> L ∩ a⁺b⁺a⁺c⁺ = {aⁿbᵐaʲcᵏ | j ≥ n} — **это КС-язык** (лишние a уходят в w₃ ∈ {a,c}⁺):

THEORY.md §5 (verbatim):

> Найденные дефекты: closure_reduction утверждал
> L ∩ a⁺b⁺ = {aⁿbⁿ} для грамматики, порождающей только {a²ᵐb²ᵐ | m ≥ 2}

Check every set equality with `check_membership` on the original IR, in both directions, before the
non-CFL proof. A pair of related tasks to keep apart: dcfl-15 (L₁ = {u₁au₂ | |u₁| ≤ |u₂|}, DCFL) and
dcfl-16 (L₂ = {u₃au₄ | |u₃| ≥ |u₄|}, not DCFL), THEORY.md §1.7.

## Policy over spec

### T13. Old tz wording

`dcfl_system/tz_dcfl_agent_system.md` §6.1.3–6.1.4 still says "a surviving split refutes the Yu
argument" and "a dead word refutes Shallit". The policy overrides it: a surviving split is an **unclosed
case**, not `refuted`, and a dead word found by a finite search is only `unknown` (VERDICT_POLICY §4).
THEORY.md §1.4 also overrides tz §12.4 on the closure list. When the tz and THEORY.md / VERDICT_POLICY
disagree, follow THEORY.md and VERDICT_POLICY.

## IR and oracle

### T14. IR forms the oracle cannot read

Measured with `validate_ir(cls="dcfl", check_tools=true)` on the eval IRs:

- dcfl-11 as written (`{$ a^n b^n c^m} union {d a^m b^n c^n}, n,m >= 1`) has **no oracle**: the letter `$`
  and the trailing shared condition are not parsed. Rename `$` to an unused letter and give each branch
  its own condition list (`dpda.md` §4). State the renaming in the report.
- Membership answers `null` mean "unknown" (for example words longer than 40 for the exponent oracle);
  they never count as "not in L" and never become negative witnesses.
- A unary alphabet limits `dpda_vs_ir` to 11 decided words (`well_formed`). For a regular language
  `dfa_vs_ir` checks more words, but it is supporting evidence only. The gate bases a `dcfl` verdict only on
  `dpda_vs_ir` ≥ `bounded_pass` or Lean `proved`.
- `input_grammar_cfg` refuses a DCFL task type; run it on a copy of the IR with a CFL task type, and
  remember that it certifies CFL only.
