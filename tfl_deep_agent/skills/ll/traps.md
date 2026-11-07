# LL skill — traps

Sources: `docs/EVAL_SET.md` (LL rows, expected verdicts), `docs/EVAL_RESULTS.md` (live Haiku run of the
trap tasks, 2026-09-27), `docs/THEORY.md` §3 (the `[ошибка]` paragraphs), `ll_system/tz_ll_agent_system.md`
§11, `ll_system/CLAUDE.md` ("Critical reminders"). Eval ids below are the `tfl_eval/manifest.json` ids.
The Format 3 rows' tool outcomes are pinned by `tfl_deep_agent/tests/test_skill_ll.py`.

## 1. Eval LL tasks and what each one tests

| id | Task | Expected | Trap / what to do |
|---|---|---|---|
| ll-01 | {aⁿbⁿ} | ll, k = 1 | S → aSb \| ε. Non-regular but LL(1) (THEORY §3.2). |
| ll-02 | {aⁿbⁿ} ∪ {aⁿcⁿ} | not_ll | DCFL, not LL: branch-point argument §3.3 (C), the first worked template. Both halves are LL(1); the union is not (no closure under ∪, §3.2). The eval IR's constraint is a comment, so its oracle is inexact: re-encode it as in SKILL.md §5.1. |
| ll-03 | {wcwᴿ} | ll, k = 1 | S → aSa \| bSb \| c. |
| ll-04 | {w b c wᴿ \| w ∈ {a,b}*} | ll, k = 1 | S → aSa \| bT, T → c \| aSab \| bTb (THEORY §3.4). |
| ll-05 | {w b* c wᴿ} | not_ll | **[trap: looks like ll-04]** DCFL but not LL: §3.4 corollary (limited flexibility). Old ll_system prompts and mocks declared it LL(1); that is an `[ошибка]` (§3.4). |
| ll-06 | {aⁱbʲ \| i ≤ j} | ll, k = 1 | S → TB, T → aTb \| ε, B → bB \| ε. |
| ll-07 | {aⁱbʲ \| i ≥ j} | not_ll | **[trap: mirror of ll-06]** §3.4 theorem: words aⁿbᵏ and aⁿbⁿ, finite unary tail, W_G. |
| ll-08 | Format 3: S → aAaa \| bAba, A → b \| ε | LL(2), not SLL(2); SLL(3) | **[trap: strong vs LL]** `ll_table(k=2)` gives `is_ll_k` true and `is_strong_ll_k` false. With `max_k`, least k = 2 and `strong_k` = 3. Never call it "not LL(2)" from the strong director sets. |
| ll-09 | Format 3: S → AB, A → ε \| b, B → aa \| ba | not LL(2), LL(3) | **[trap: plain σ]** σ(A) = {{aa, ba}}: A's single local follow set already contains both lookaheads, so A → b and A → ε conflict on `ba` even in the full test. `ll_table(k=2)` gives `refuted`, and `ll_table(k=3)` gives `verified`. |
| ll-10 | Format 3: E → E + T \| T, T → id | not LL(k) for any k | Left-recursion certificate: `ll_table(max_k=…)` gives `certificate.type = left_recursion`, `verified`. That is about **this grammar**. |
| ll-11 | Format 2: the language of the ll-10 grammar | ll, k = 1 | **[trap: grammar ≠ language]** E → T E′, E′ → + T E′ \| ε. Use `grammar_transform(ops=["eliminate_left_recursion"])`, then `ll_table` on the result. The language claim is `bounded_pass` (sample equivalence). |
| ll-12 | {aⁿ0bⁿ} ∪ {aⁿ1b²ⁿ} | not_ll | §3.3 (C), the second worked example (markers 0/1 in one X_{t*}). |
| ll-13 | (ab)*c | ll, k = 1 | Regular ⇒ LL(1): the DFA grammar Q → aδ(Q,a), with Q → ε for the accepting states (§3.2). |
| ll-14 | {aⁱbʲcᵏ \| i = j ∨ j = k} | not_ll | Inherently ambiguous ⇒ not DCFL ⇒ not LL (§1.5, §3.3 (B)). Theorem 4.7.4 does **not** apply: L ⊆ a*b*c*, so the dead class is infinite (tz_ll §4.9). |
| ll-15 | Format 3: S → a¹¹b \| a¹¹c | LL(12), not LL(11) | **[trap: k > 10]** `ll_table(max_k=10)` gives only `bounded_pass` "not LL(k) for k ≤ 10". `ll_table(max_k=12)` gives least k = 12, `verified`. Always search with `max_k=12`. |
| ll-16 | {wwᴿ} | not_ll | Not DCFL (§1.2: suffix b·aᴺ·b·uᴿ, D = ∅) ⇒ not LL. |
| ll-17 | Format 3: S → aSb \| ε | LL(1) | Table without conflicts. |
| ll-18 | Format 3: S → aS \| a | not LL(1), LL(2) | **[trap: right recursion]** Both rules have FIRST₁ = {a}. At k = 2, S → aS gives `aa` and S → a gives `a$`: no conflict, least k = 2. |

## 2. Wrong or superseded formulations in THEORY.md — never use them

These are the paragraphs THEORY.md marks **[ошибка]** or «Исправлено», plus the items §3.5 lists as
corrections. They are described here, not quoted as facts. Each is followed by what THEORY.md puts in
its place.

- §3.1: the old ll_system code decided **SLL(k)** (director sets with the global FOLLOW_k) and
  reported "not LL(k)" at confidence 1.0. Fixed: `ll_table` runs the full test, and `is_strong_ll_k` is
  reported separately.
- §3.2: "LL ∩ REG = LL" is false. Counterexample: L = {aⁿw | w ∈ {b,c}ⁿ} is LL(1), and
  L ∩ (a*b* ∪ a*c*) = {aⁿbⁿ} ∪ {aⁿcⁿ} is not LL.
- §3.3: "L is LL(k) ⇔ finitely many k-classes of prefixes" is false: the prefixes aⁿ of the LL(1)
  language {aⁿbⁿ} are pairwise 1-distinguishable. The correct use of prefix classes is Theorem 4.7.4
  (all Nerode classes finite ⇒ not DCFL ⇒ not LL), with the dead class D = ∅.
- §3.3: "after reading u₁ and u₂ with the same lookahead the parser is in one configuration" is false:
  otherwise every LL language would be regular, because the configuration includes the stack. Use
  argument (C), which substitutes derivations of a nonterminal of the hypothetical LL(k) grammar.
- §3.3 (C), marked «Исправлено»: the old bound |x|−k < |w| ≤ |x|+k for an arbitrary x is false
  (S → a¹⁰⁰A, A → b | c, k = 1). Use the common prefix of length ℓ ≥ k and ℓ−k < |w| ≤ ℓ.
- §3.4: {w b* c wᴿ} was declared LL(1) in old prompts and mocks. It is DCFL but not LL.
- §3.4: the old "bounded number of continuations" form of the flexibility lemma did not fix the
  prefix u or justify finiteness of L(δ). Use the §3.4 version.
- §3.5: «{aⁿbⁿcⁿ}^complement is DCFL but not known to be LL» was replaced by the checked examples of
  the catalog. "L ∩ R inherently ambiguous ⇒ L inherently ambiguous" holds under exactly one
  condition: L is context-free, because the unambiguous CF languages are closed under ∩ REG.
- §1.2 (used by route A): "for every infinite M ⊆ Σ* there is an infinite homogeneous M′ ⊆ M" is
  false (DCFL {aⁿbⁿ}, M = {aⁿ}). One infinite family of pairwise distinguishable words is not enough:
  **all** classes must be finite.

## 3. Method traps

- **Substitution ≠ pumping** (tz_ll §11.1). The branch-point argument is a separate technique.
- **Left recursion ≠ not LL** for a language (tz_ll §11.2, ll_system/CLAUDE.md).
- **A failed construction is not a proof of "not LL"** (VERDICT_POLICY R1). "I could not build an
  LL(1) grammar" is never evidence.
- **The least k is part of the answer** (tz_ll §11.3). For a grammar, `ll_table(max_k=12)` decides it
  (minimality is established only after a definite negative for every smaller k, THEORY §3.1). For a
  language, the least k of your grammar is only an upper bound (SKILL.md §8).
- **Dead class** (§1.2): Theorem 4.7.4 says nothing when D = {x | no z with xz ∈ L} is infinite, for
  example L ⊆ a*b*. A finite search for continuations does not establish x ∈ D.
- **Grammar ambiguity** says nothing about the language (§1.5). Inherent ambiguity is a property of
  every grammar of L.
- **Hierarchy**: REG ⊊ LL(1), LL(k) ⊊ LL(k+1), ⋃ LL(k) ⊊ DCFL (§3.2; the proof of the strict
  hierarchy was not read in the THEORY revision). "DCFL" does not imply "LL". "Not DCFL" implies
  "not LL".
- **Oracles**: an answer of `None` is unknown, never "not in L" (VERDICT_POLICY §4 ll / language
  oracle). A `set_builder` constraint written as `{"comment": ...}` is ignored by the oracle, which
  then is inexact, so `grammar_vs_ir` stays at `well_formed`.
- **Character-level grammars**: terminals are single characters, and `$` and `ε` are reserved. ε is
  `rhs: []` (or `["ε"]`). For `id` in E → E + T | T, T → id, use a one-letter terminal such as `i`,
  as the eval IR ll-10/ll-11 does.

## 4. From the live runs (`docs/EVAL_RESULTS.md`, Haiku, 2026-09-27, before round C2)

- ll 7/7 correct. The four Format 3 traps (ll-08/09/15/18) were answered by the oracle alone: 0.98,
  about 0 s, no LLM call.
- The destructive language traps were the expensive ones: ll-05 took 378.6 s and ll-07 81.7 s, both
  `not_ll` 0.60. The `well_formed` cap has since been lowered to **0.55** (VERDICT_POLICY §2), so a
  written but not machine-checked "not LL" proof now ends at ≤ 0.55. Spend effort on checking the
  witness words (SKILL.md §6), not on longer prose.
- ll-11 (grammar ≠ language) was correct at 0.85, the `bounded_pass` cap: equivalence by sample, not
  `verified`.
