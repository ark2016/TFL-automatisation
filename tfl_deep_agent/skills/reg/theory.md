# REG theory: statements quoted verbatim

Companion to `/skills/reg/SKILL.md`. Every block below is copied word for word from the file and section in its
`Source:` line (a test checks this). Quote these, or call `get_theorem(id=...)` for the THEORY.md sections, rather
than restating a lemma from memory. Status marks are the files' own:

- `docs/THEORY.md` covers REG only in **§0 (part II)**, marked **[доказано индукцией и Нероудом]**. It proves the facts
  about one grammar, G_reg, and states the REG pumping split condition. It has no general REG section.
- The general REG statements (pumping lemma, Myhill–Nerode, closure, pumping length) come from the REG spec
  `agent_system/tz_tfl_agent_system.md` §1, which cites Hopcroft–Motwani–Ullman and Боянчик.
  THEORY.md does not review them. Its registry `docs/THEORY_REFERENCE.md#reg` restates them, gives a local proof of
  the pumping lemma and the closure constructions, and checks the Nerode statement against Mathlib.
- Parts of tz §1.9 are not reviewed anywhere in the repo, see section 5.

## 1. Pumping lemma for regular languages

Source: `agent_system/tz_tfl_agent_system.md` §1.5

> **Теорема (Hopcroft, Th. 4.1.1, Pumping Lemma for REG):**
> Пусть L — регулярный язык. Тогда существует натуральное число n (длина накачки), такое что для любого слова w ∈ L, |w| ≥ n, существует разбиение w = xyz, удовлетворяющее условиям:
> 1. |xy| ≤ n
> 2. |y| ≥ 1 (т.е. y ≠ ε)
> 3. Для всех i ≥ 0: xy^i z ∈ L

Source: `agent_system/tz_tfl_agent_system.md` §1.5

> Если для любого n ∈ ℕ существует слово w ∈ L, |w| ≥ n, такое что для любого разбиения w = xyz с |xy| ≤ n и |y| ≥ 1 существует i ≥ 0, для которого xy^i z ∉ L, то L нерегулярен.

Source: `agent_system/tz_tfl_agent_system.md` §1.5

> **Порядок кванторов в доказательстве нерегулярности:**
> ∀n ∃w∈L (|w|≥n) ∀x,y,z (w=xyz ∧ |xy|≤n ∧ |y|≥1) ∃i≥0: xy^i z ∉ L

The REG split is bounded by |xy|, which fixes where y lies. THEORY.md states this, and warns against borrowing the
CFL window condition:

Source: `docs/THEORY.md` §0

> Для регулярной накачки используется z=xyz, |xy|≤p, |y|>0: в слове a^{3p+2}bᵖ фактор y целиком состоит из a; i=2 нарушает m≤3k+2. Условие КС-леммы |vwx|≤p не ограничивает положение окна и здесь его подставлять нельзя.

A finite check of the lemma is only a diagnostic. This is the REG rule of the verdict policy:

Source: `docs/VERDICT_POLICY.md` §4

> **reg / pumping.** Инстанцировать семейство при p ∈ {2,3,4}; для каждого слова проверить все xyz с |xy| ≤ p, |y| > 0. Результаты при конечных p и i — только диагностика (`well_formed`): закрытие этих p не доказывает ∀p, а выживание при нескольких i не доказывает ∀i. `refuted` требует конкретного нарушения заявленной предпосылки, например z ∉ L. Ответ оракула `None` не является ни принадлежностью, ни непринадлежностью.

THEORY.md §2.0 makes the same point for the CFL lemma, with a counterexample that applies to any finite check:

Source: `docs/THEORY.md` §2.0

> Конечная проверка p=3,4 и i=0,2,3 — диагностика. Даже полное закрытие разбиений при этих p не доказывает общую теорему: у L={a¹⁰} оно выполняется, но константа p=11 удовлетворяет лемме вакуумно. И наоборот, выживание разбиения при трёх i не доказывает выживания при всех i. Нужное i может зависеть от p и разбиения. `None` оракула означает неизвестность, а не непринадлежность.

The lemma is a necessary condition only: tz §1.5 states "L regular ⇒ …" and its contrapositive, nothing more. A
language for which no pumping contradiction was found is **not** shown to be regular (VERDICT_POLICY R1).

## 2. Myhill–Nerode theorem

Source: `agent_system/tz_tfl_agent_system.md` §1.6

> u ≡_L v  ⟺  ∀z ∈ Σ*: (uz ∈ L ⟺ vz ∈ L)
> Слово z называется **различающим контекстом** (distinguishing extension) для пары (u, v).

Source: `agent_system/tz_tfl_agent_system.md` §1.6

> **Теорема Майхилла-Нероуда (Hopcroft, Th. 3.4.3):**
> L регулярен ⟺ индекс ≡_L конечен (т.е. число классов эквивалентности конечно).
> Более того, количество классов ≡_L равно числу состояний минимального ДКА для L.

What a finite check of Nerode pairs is worth:

Source: `docs/VERDICT_POLICY.md` §4

> **reg / nerode.** Проверка конкретных пар и заявленного различающего суффикса может дать `bounded_pass`/`refuted` для этих свидетельств; конечное число пар не доказывает бесконечность индекса. Пустой суффикс допустим. Неизвестная принадлежность оставляет проверку незавершённой.

THEORY.md §0 shows the form a non-regularity argument has to take (an infinite family, not a bound):

Source: `docs/THEORY.md` §0

> В частности, a^{3n+2}bⁿ∈L для всех n, а a^{3m+2}bⁿ∉L при m>n. Это бесконечное семейство различимых префиксов, а не конечная оценка индекса Нероуда.

The two-sided version (syntactic monoid, tz §1.7: "L регулярен ⟺ M(L) конечен") is rarely needed for exam tasks.
Prefer the right congruence.

## 3. Closure properties

REG is closed under all the standard operations in the table of tz §1.8 (union, intersection, complement,
concatenation, star, shuffle, reverse, SHIFT, homomorphism, inverse homomorphism). The spec lists how to use them
against regularity:

Source: `agent_system/tz_tfl_agent_system.md` §1.8

> 1. **Пересечение с регулярным:** Если L ∩ R нерегулярен для некоторого регулярного R → L нерегулярен. Пример: L = {w | |w|_a = |w|_b}, R = a*b*, L ∩ R = {a^n b^n} — нерегулярен.
> 2. **Затирающий гомоморфизм:** h: Σ* → Γ* с h(c) = ε. Если h(L) нерегулярен → L нерегулярен. Пример: L ⊆ {a,b,c}*, h(c) = ε, h(L) = {a^n b^n}.
> 3. **Обратный гомоморфизм:** Если h⁻¹(L) нерегулярен → L нерегулярен.

The equality L ∩ R = K has to be proved in both directions. A non-regular subset proves nothing:

Source: `docs/THEORY_REFERENCE.md` #reg

> Это локальные конструкции. Для отрицательного вывода нужно доказать равенство результата преобразования конкретному нерегулярному языку. Наличие нерегулярного подмножества ничего не доказывает: Σ* содержит все такие подмножества над Σ.

Boolean combinations: regularity transfers from the parts, non-regularity does not:

Source: `docs/VERDICT_POLICY.md` §4

> Регулярность Boolean-комбинаций переносится от регулярных компонентов; нерегулярность так не переносится. Смешанные знаки в пороговой линейной сумме не допускают общего сокращения к ДКА.

Complement and DFA constructions (tz §1.8 table, "Конструкция" column): complement is "Инверсия допускающих состояний
ДКА". This requires a total δ: tz §1.2 defines the DFA transition function as "δ: Q × Σ → Q — функция переходов
(тотальная)".

## 4. Regular grammars and regex with back-references

Source: `agent_system/tz_tfl_agent_system.md` §4.9

> 1. Pure fn: проверить, является ли грамматика правой/левой линейной → автоматически REG

Back-references are not regular syntax, and their presence alone decides nothing:

Source: `agent_system/tz_tfl_agent_system.md` §4.3

> - `has_backreferences=true` → `hypothesis: likely_non_regular`, `confidence: 0.7`, dispatch pumping + nerode + RE builder (на случай если backreference тривиален, как `(a)\1*` = `aa*`)

The confidence 0.7 in that spec line is the old pipeline's prior, not an allowed confidence for a verdict here. The
caps of VERDICT_POLICY §2 apply.

## 5. Pumping length (task type `compute_pumping_length`)

Source: `agent_system/tz_tfl_agent_system.md` §1.9

> Для регулярного языка L, заданного ДКА с n состояниями, длина накачки p ≤ n.

Source: `agent_system/tz_tfl_agent_system.md` §1.9

> Точная длина накачки — минимальное n, для которого выполняется лемма о накачке.

**Not reviewed.** tz §1.9 also bounds p for a regex by its operand count and says that the exact pumping length is
NP-hard to find. Neither claim appears in THEORY.md or THEORY_REFERENCE.md, and no test checks them. Do not cite
them as established facts. If you need such a bound, prove it in the report.

## 6. The worked grammar G_reg (eval reg-14, golden example `docs/examples/reg_grammar_SaSb_lean_proved`)

Source: `docs/THEORY.md` §0

> Грамматика G_reg: S → SaSb | ε | A, A → bb | aa | bSb.

Source: `docs/THEORY.md` §0

> - **[ошибка]** `grammar_analyzer.md` (пример вывода): «L ∩ a*b* = {aⁿbⁿ}». Неверно: bb, aa, abbb, a⁵b ∈ L ∩ a*b*.
> - **[ошибка]** `proof_checker.md` (пример вывода): «на самом деле пересечение = {aᵐbᵏ | m ≡ k (mod 2)}, регулярно». Тоже неверно: a⁴ ∉ L, a⁷b ∉ L.

Both **[ошибка]** lines quote wrong claims in order to refute them. Never reuse them as facts.

Source: `docs/THEORY.md` §0

> - **Факт.** L ∩ a*b* = {aᵐbᵏ | m ≡ k (mod 2), m ≤ 3k + 2} (перебор до длины 16). Инвариант: каждое правило сохраняет |w|_a ≡ |w|_b (mod 2). Этот язык **нерегулярен**: слова a^{3k+2} попарно различимы суффиксом bᵏ (a^{3k+2}bᵏ ∈ L, a^{3k′+2}bᵏ ∉ L при k′ > k).

Source: `docs/THEORY.md` §0

> **Общее доказательство, дополняющее перебор.** Для любого слова, выводимого из S, индукция по дереву вывода даёт m≡k (mod 2) и m≤3k+2, где m=#a, k=#b.

For the rest of the proof (both inclusions, then the pumping word a^{3p+2}bᵖ) call `get_theorem(id="0")`. It
returns the section verbatim.
