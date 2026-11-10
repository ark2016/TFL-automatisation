---
name: ll
description: >-
  LL(k) tasks. Format 3: is this grammar LL(k), and what is its least k. Format 2: is the language of
  this grammar LL. Format 1: is this set-builder language LL(k) for some k. The skill gives the decision
  procedure, the exact LL statements quoted from docs/THEORY.md §3 (and the DCFL §1 facts that
  "not DCFL ⇒ not LL" relies on), and the tools to call at each step (ll_table, first_follow, ll_parse,
  grammar_transform, grammar_vs_ir, validate_ir, check_membership, get_theorem). It lists the known
  traps: strong vs full LL(k), grammar vs language, k above 10, w b* c wᴿ, no closure under ∪ or ∩ REG.
  It also says which evidence makes an LL claim verified, bounded_pass or only well_formed.
allowed-tools: >-
  ll_table first_follow ll_parse grammar_transform grammar_vs_ir validate_ir describe_ir_schema
  ir_to_text sample_words check_membership cyk regex_to_dfa list_theorems get_theorem
metadata:
  classes: ll
  theory_sections: "3.1 3.2 3.3 3.4 3.5 1.2 1.5"
---

# LL(k) skill

Files next to this one (read them with `read_file`):

- `/skills/ll/theory.md`: the LL statements **verbatim** from `docs/THEORY.md` §3.1–§3.5 and the
  DCFL facts §1.2, §1.5. It also lists what THEORY.md marks as not read or not verified.
- `/skills/ll/traps.md`: the trap catalog (eval tasks ll-01…ll-18, the `[ошибка]` formulations to
  avoid, findings from the live runs).

Never state an LL lemma from memory. Quote it from `theory.md` or call `get_theorem(id="3.3")`
(ids: `3.1`, `3.2`, `3.3`, `3.4`, `3.5`; the DCFL sections `1.1`…`1.5`). `list_theorems(cls="ll")`
does **not** list the DCFL sections; ask for `cls="dcfl"` as well when you use route (A).

## 1. When this skill applies

- IR `task_type` is `ll_check_grammar` (Format 3), `ll_check_grammar_lang` (Format 2) or
  `ll_check_language` (Format 1). `describe_ir_schema(cls="ll")` shows the shapes.
- The text asks «является ли грамматика LL(k)», «найти минимальное k», «построить LL(1)-таблицу /
  FIRST / FOLLOW», «является ли язык LL», «устранить левую рекурсию и проверить LL(1)».
- A cascade question about where a language sits in the hierarchy, handled as CFL → DCFL → LL. A
  proved "not CFL" or "not DCFL" already settles "not LL" (route (A) below). A proved "DCFL" does not:
  `⋃_k LL(k) ⊊ DCFL` (theory.md §B).
- Not for proving DCFL / non-DCFL or CFL / non-CFL as such. Use the `dcfl` / `cfl` skills, then
  come back here.

## 2. First decision: is the question about a grammar or about a language?

<!-- quote: 3.1 -->
```text
Здесь k ≥ 1, грамматика конечна, ε не является буквой алфавита. LL(k)-**язык** — язык,
имеющий хотя бы одну LL(k)-грамматику; отрицательный тест данной грамматики не решает этот вопрос.
```

<!-- quote: tz_ll -->
```text
2. **Язык vs грамматика.** Формулировка вопроса критически важна:
   - "Является ли грамматика LL(k)" — проверяется алгоритмически
   - "Является ли язык LL(k)" — может потребовать синтез другой грамматики
   Левая рекурсия в грамматике НЕ означает, что язык не LL.
```

| Format | Question | What answers it | Best trust |
|---|---|---|---|
| 3 `ll_check_grammar` | is **this grammar** LL(k) / least k | `ll_table`: a complete test for each fixed k (THEORY §3.1; a budget overrun gives «не установлено»). With `max_k=12` it settles the least k only if that k is ≤ 12 or there is a left-recursion certificate; otherwise only «не LL(k) при k ≤ 12» | `verified` (fixed k, least k found, certificate); `bounded_pass` (nothing found for k ≤ 12, VERDICT_POLICY R5) |
| 2 `ll_check_grammar_lang` | is **L(G)** LL | `ll_table` on G itself, or a transformed / new grammar plus an equivalence check, or a destructive proof | `verified` (G itself is LL(k)), otherwise `bounded_pass` / `well_formed` |
| 1 `ll_check_language` | is the **set-builder language** LL | a new grammar plus `grammar_vs_ir`, or a destructive proof | `bounded_pass` (constructive), `well_formed` / `bounded_pass` (destructive) |

## 3. Format 3: a property of the given grammar (algorithmic, no subagent needed)

The spec routes Format 3 straight to the FIRST/FOLLOW oracle ("LLM не нужен", tz_ll §1.3). In the
2026-09-27 live run the four Format 3 traps were solved by the oracle alone, at confidence 0.98
(`docs/EVAL_RESULTS.md`). Do not spend model budget on them.

1. `validate_ir(cls="ll", ir=IR)`. A grammar inside the IR that fails validation is `invalid_input`,
   because the IR is your own input. Fix the IR and get it approved at HITL 1. Do not guess.
2. For each k the task names: `ll_table(ir=IR, k=K)`. When the task asks for the least k, or
   `ir.k` is `null`: `ll_table(ir=IR, max_k=12)`. Always search up to **12**, the tool's maximum,
   not 10 (trap ll-15: S → a¹¹b | a¹¹c is LL(12) and not LL(11)).
3. When the task asks about **strong** LL(k), report `data.is_strong_ll_k` separately. A grammar can be
   LL(k) without being SLL(k) for k ≥ 2 (trap ll-08), and for k = 1 the two classes coincide (theory.md §A).
4. For the sets shown in the report: `first_follow(ir=IR, k=K)`. Its director sets are the
   **strong** ones, FIRST_k(α) ⊕_k FOLLOW_k(A) with the global FOLLOW_k. A conflict among them at
   k ≥ 2 does **not** refute LL(k). Only `ll_table` decides (traps ll-08, ll-09).
5. Optional illustration: `ll_parse(ir=IR, k=K, word="...")` gives the leftmost derivation of a
   word. On a grammar that is LL(k) but not SLL(k), the tool reports `unsupported`. A larger k need
   not help: an LL(k) grammar is not always SLL(k') for some larger k' (THEORY §3.1 gives only one
   example, S → aAaa | bAba, A → b | ε, which becomes strong at k = 3). Check
   `ll_table(ir=IR, max_k=12)`: if `data.strong_k` is set, run `ll_parse` with that k. Otherwise
   use `cyk` for membership.

How to word each `ll_table` outcome (these are the tool's own claims, `data.scope = "grammar"`):

| Outcome | Wording | Trust |
|---|---|---|
| `k` given, `is_ll_k` true | «грамматика G является LL(k)» | `verified` |
| `k` given, `is_ll_k` false | «грамматика G не LL(k)»: a conflict of the full test for this k | `refuted` (on the claim "G is LL(k)"): a complete negative answer for this k |
| `k` given, `is_ll_k` null | budget ran out: «не установлено» | `not_verified` |
| `max_k`, `found` | «G — LL(k), не LL(j) при j < k; минимальное k = …» | `verified` |
| `max_k`, `certificate.type = left_recursion` | «G не LL(k) ни при каком k» (left recursion after useless symbols are removed) | `verified` |
| `max_k`, not found, no certificate | «G не LL(k) при k ≤ K», never «не LL» | `bounded_pass`, confidence ≤ 0.85 (VERDICT_POLICY R5) |
| `max_k`, `undetermined` | «не установлено» | `not_verified` |

These are statements about **G**. Never turn them into a statement about L(G) (trap ll-10 / ll-11).

## 4. Format 2: is the language of the given grammar LL?

1. `validate_ir(cls="ll", ir=IR)`, then `ll_table(ir=IR, max_k=12)` on the **given** grammar.
   - `found` with k: L(G) is LL(k) by the definition quoted in §2. The tool's `verified` is a complete
     check of the grammar in question. ll_system's gate does the same (`grammar_source ==
     "given_grammar"` → `verified`). The least k of **this** grammar is only an upper bound for the
     language (see §8).
   - a left-recursion certificate or "not LL(k) for k ≤ K": this says **nothing** about the language.
     Go on to step 2.
2. Transform: `grammar_transform(ir=IR, ops=["eliminate_left_recursion", "left_factor"])`. If the tool
   says `unsupported` (hidden left recursion behind a nullable prefix), use
   `ops=["to_ll_normal_form"]`. Then run `ll_table(grammar=G2, max_k=12)` on the result `G2`. The
   transformed grammar is `well_formed`. The tool's own equivalence check (exact CYK on both grammars
   over every word up to `max_len`) gives `bounded_pass`, or `refuted` on a mismatch. A transform
   does not guarantee LL(k) (theory.md §E).
3. Independent check against the task's language: `grammar_vs_ir(grammar=G2, ir=IR, cls="ll")`.
   For Format 2 the oracle is exact CYK on the task's own grammar.
4. "L(G) is LL(k)" via G2 is then `bounded_pass` (confidence ≤ 0.85): the table is `verified` for G2,
   and L(G2) = L(G) is checked on a sample (VERDICT_POLICY R2: for Format 1/2 the result is
   `bounded_pass` by sample equivalence, never `verified`).
5. If no transform gives an LL grammar, reason about the language itself. Write a grammar by hand as
   in §5, or prove "not LL" as in §6. A failed construction is **not** evidence for "not LL"
   (VERDICT_POLICY R1).

## 5. Format 1: a set-builder language

### 5.1 Give the language an exact oracle

Without a membership oracle no constructive LL verdict is possible. R2 needs a grammar checked
against L at `bounded_pass` or better, and the result then stays `uncertain`. A language given in words or
as `language_spec.kind = "natural"` has **no** LL oracle. Every Format 1 eval IR except ll-02 has
that kind: `grammar_vs_ir` answers `not_verified`, "no LL oracle for kind 'natural'". ll-02's
`set_builder` IR has a comment-only constraint, so its oracle is inexact. Have the `ir-builder` role
write a `set_builder` spec in the shape the ll word oracle understands
(`ll_system/lib/word_oracle.py`):

- variables: `nat` (an exponent, optional `min`), `enum` (one symbol from `values`), `word` over an
  `alphabet`. A `word` variable used twice (bare and as `rev(w)`) binds to the same string.
- template tokens: literal runs (`"c"`), variable names, `"rev(w)"`, `"<base><superscript>"`
  (`"aⁿ"`, `"bʲ"`, `"Xⁿ"` with an enum base).
- constraints: `{"op": "eq|neq|lt|leq|gt|geq", "left": ..., "right": ...}` and `and` / `or` / `not`
  trees. A `{"comment": ...}` constraint is **ignored** by the oracle. The oracle is then
  inexact, and `grammar_vs_ir` stays at `well_formed`. Encode every condition formally.

Examples (each one passes `validate_ir`, and its oracle is exact; pinned by a test):

```json ll-07
{"task_type": "ll_check_language", "source_text": "{a^i b^j | i >= j >= 0}",
 "language_spec": {"kind": "set_builder", "alphabet": ["a", "b"],
  "variables": [{"name": "i", "domain": {"type": "nat"}}, {"name": "j", "domain": {"type": "nat"}}],
  "template": ["aⁱ", "bʲ"], "constraints": [{"op": "geq", "left": "i", "right": "j"}]}}
```

```json ll-05
{"task_type": "ll_check_language", "source_text": "{w b* c w^R | w in {a,b}*}",
 "language_spec": {"kind": "set_builder", "alphabet": ["a", "b", "c"],
  "variables": [{"name": "w", "domain": {"type": "word", "alphabet": ["a", "b"]}},
                {"name": "m", "domain": {"type": "nat"}}],
  "template": ["w", "bᵐ", "c", "rev(w)"], "constraints": []}}
```

```json ll-02
{"task_type": "ll_check_language", "source_text": "{a^n b^n} U {a^n c^n}",
 "language_spec": {"kind": "set_builder", "alphabet": ["a", "b", "c"],
  "variables": [{"name": "n", "domain": {"type": "nat"}},
                {"name": "X", "domain": {"type": "enum", "values": ["b", "c"]}}],
  "template": ["aⁿ", "Xⁿ"], "constraints": []}}
```

Then run `validate_ir(cls="ll", ir=IR)` (it also reports whether an oracle builds) and
`ir_to_text(cls="ll", ir=IR)` for HITL 1. Every later check is **relative to this IR**. The report
must name the IR it checked against.

### 5.2 Hypothesis

<!-- quote: tz_ll -->
```text
7. **Ключевая интуиция для промптов.** LL-парсер читает строго слева
   направо и должен в каждой точке, глядя на k символов вперёд,
   однозначно выбрать правило. Если для выбора нужна информация
   «из будущего» (палиндромы без маркера, aⁿbⁿ ∪ aⁿcⁿ) — язык не LL.
```

This is an intuition for picking a side, not a proof. Before guessing, check the catalog in
theory.md §C and §D. Look-alike pairs with opposite answers sit there side by side: {w b c wᴿ}
LL(1) vs {w b* c wᴿ} not LL; {aⁱbʲ | i ≤ j} LL(1) vs {aⁱbʲ | i ≥ j} not LL.

### 5.3 Constructive side: "L is LL(k)"

1. Write a candidate grammar G. Typical successful patterns (tz_ll §4.4): an explicit marker,
   stack phases with a clear boundary, nested brackets with distinct delimiters. For a regular
   language, take the DFA grammar Q → aδ(Q,a), with Q → ε for the accepting states Q
   (theory.md §B), which is LL(1). Get the DFA
   from `regex_to_dfa(regex=...)` and drop the dead state. Check the regex syntax first: no
   back-references (VERDICT_POLICY R2).
2. `ll_table(grammar=G, max_k=12)`: G must be LL(k) (`verified`). Otherwise revise G.
   `first_follow(grammar=G, k=K)` shows where the conflict is.
3. `grammar_vs_ir(grammar=G, ir=IR, cls="ll")`. You need `bounded_pass`: every word up to the
   reported `max_len`, at least 30 decided words, no counterexample. `refuted` with a counterexample
   forbids the constructive verdict this round. Fix G and rerun.
4. Spot checks, if useful: `ll_parse(grammar=G, k=K, word=...)` on words from
   `sample_words(cls="ll", ir=IR)`. `sample_words` is reference material only and is never evidence.

Both checks are needed: the table proves "G is LL(k)", and the sample proves L(G) = L only up to
`max_len`. The language claim is therefore `bounded_pass`, confidence ≤ 0.85. LL is not formalized
in Lean (VERDICT_POLICY R-Lean: «LL(k) — не формализуется»), so nothing raises it to `verified`.

## 6. Destructive side: "L is not LL(k) for any k"

Pick the route from the shape of L (theory.md §C, catalog; tz_ll §4.7–§4.9):

| Shape of L | Route | Statement (theory.md) |
|---|---|---|
| probably not DCFL (palindromes without a marker, {wwᴿ}, unions like {aⁿbⁿ} ∪ {aⁿb²ⁿ}) | (A) not DCFL ⇒ not LL | §3.3 (A); the techniques of §1.1–§1.4 (load the `dcfl` skill) |
| not DCFL **and** every prefix extends to a word of L (dead class D = ∅) | (A) through Theorem 4.7.4 | §1.2 with the dead-class check |
| inherently ambiguous ({aⁱbʲcᵏ \| i = j ∨ j = k}) | (B) | §3.3 (B), §1.5 |
| DCFL, a choice between branches after a long common prefix ({aⁿbⁿ} ∪ {aⁿcⁿ}, {aⁿ0bⁿ} ∪ {aⁿ1b²ⁿ}) | (C) branch-point argument | §3.3 (C) and its two worked templates |
| DCFL, a bounded unary tail after a fixed prefix ({aⁱbʲ \| i ≥ j}, {w b* c wᴿ}) | (C) + the limited-flexibility lemma | §3.4 |

The three routes, verbatim:

<!-- quote: 3.3 -->
```text
**(A) Не DCFL ⇒ не LL.** Все техники раздела 1 (лемма Ю, теорема 4.7.4, дополнение, L_$).
Так доказывается не-LL для PAL, {wwᴿ}, {aⁱbʲcᵏ | i=j ∨ j=k} и т.п.

**(B) Существенная неоднозначность ⇒ не LL** (LL-грамматики однозначны).
```

<!-- quote: 3.3 -->
```text
Это и есть корректное содержание «метода подстановки»: подставляются **выводы нетерминала** в
гипотетической LL(k)-грамматике, а не «конфигурации парсера».
```

How to run it:

1. Have the `prover` role write the proof. It quotes the statement it uses from `theory.md` and takes an
   **arbitrary** k **and an arbitrary** LL(k) grammar G for L (the argument must hold for every k and every
   such G). It gives the branch words explicitly as words in n, with n chosen from k and G (n > k in §3.3 (C),
   n > k + W_G in §3.4). The common form wδ (ℓ−k < |w| ≤ ℓ), the nonterminal X_{t*} and the pigeonhole pair
   (n, n′) depend on the unknown G. The proof establishes that they exist (common-form, mixing and pigeonhole
   arguments) and never fixes them as concrete values.
2. Check the witnesses by machine. Instantiate the branch words at n = k + 2 for k ∈ {1, 2}
   (VERDICT_POLICY §4 ll/substitution) and call
   `check_membership(cls="ll", ir=IR, words=[...], expected={"aaabbb": true, ...})`. Do the same for the
   words the proof declares ∉ L, such as the substituted word aⁿb^{n′}, and for route (A) for the
   Nerode pairs (u·w ∈ L, v·w ∉ L). An undecided expected word gives `not_verified`, never
   "not in L". An oracle answer that contradicts the proof gives `refuted`: the proof is wrong, so
   rewrite it.
3. Have the `critic` role check the proof against the quoted statement: the quantifiers, the ℓ ≥ k
   condition, the independence of the subtrees, and the dead-class check for §1.2.
4. Route (B): no tool here checks inherent ambiguity, and the ambiguity of one **grammar** says
   nothing about the language (§1.5). A hand proof is a supporting step. The gate gives the language
   verdict no destructive basis, so the published verdict is `uncertain`.
5. Route (A): when the not-DCFL step has a Lean statement (the `lean-formalizer` subagent, direction `non_dcfl`),
   the `lean_check` result is `verified` for the step 'L is not DCFL', but the gate does not count it
   for an `ll` report. The published verdict stays `uncertain`. The step from not-DCFL to not-LL is
   §3.3 (A) itself.

## 7. Evidence → trust

Caps (VERDICT_POLICY §2): `verified` 0.98, `bounded_pass` 0.85, `well_formed` 0.55, `not_verified`
0.40 (only `uncertain`), an unresolved contradiction 0.50. The evidence block and trust labels in the
report are generated from the tool ledger. Your own confidence can only lower them.

| Claim | Evidence | Trust |
|---|---|---|
| G is LL(k) for the given k | `ll_table(k=K)` | `verified` if the test passes; `refuted` if the full test (local follow sets) finds a conflict |
| G is not LL(k) for the given k | `ll_table(k=K)`, a conflict of the full test | settled completely for this k. The ledger records the result as `refuted` because it refutes the claim "G is LL(k)", not this claim (§3, VERDICT_POLICY §1). It supports the negative answer for this k and for every smaller k (an LL(k′) grammar with k′ ≤ k is also LL(k), §3.1), and says nothing about larger k or about L(G) |
| least k of G | `ll_table(max_k=…)`, `found` | `verified` |
| G is not LL(k) for any k | `ll_table(max_k=…)` left-recursion certificate | `verified` |
| G is not LL(k) for k ≤ K | `ll_table(max_k=K)` without a certificate | `bounded_pass` |
| FIRST_k / FOLLOW_k sets of G | `first_follow` | `verified` |
| w ∈ L(G) / w ∉ L(G) by the LL(k) parser | `ll_parse` | `verified` for that word |
| L(G2) = L(G) after transforms | `grammar_transform` equivalence check | `bounded_pass` (mismatch: `refuted`) |
| L(G) = L for a constructed G | `grammar_vs_ir(cls="ll")` with an exact oracle | `bounded_pass`; inexact oracle `well_formed`; no oracle `not_verified` |
| language of the given Format 2 grammar is LL(k) | `ll_table` on that grammar, `found` | `verified` |
| language is LL(k) via a new or transformed grammar | the table + the equivalence check | `bounded_pass` |
| language is not LL | a written proof (A)/(B)/(C) | supporting only; the gate publishes `uncertain` (no destructive LL basis for a language question) |
| witness words, sample words | `check_membership` without `expected`, `sample_words` | `not_verified` (reference only) |

Gate rules to keep in mind (VERDICT_POLICY §3): R1, a failed construction is not a destructive
proof. R2, a constructive `ll` verdict needs a grammar at `bounded_pass` or better. R3, a
`bounded_pass` grammar against a `well_formed` "not LL" proof is a contradiction that stays
unresolved at ≤ 0.50 unless one side is `verified` or the cross-check (R3′) refutes one. Run the
proof's witness words through the grammar with `ll_parse` / `cyk`, and the grammar's passing words
through the proof's claims. R5, "not LL" for a grammar needs the full test or a certificate, and for
a language it needs a destructive proof, never left recursion.

## 8. Wording the answer

- Say which object a claim is about: «грамматика G …» vs «язык L …».
- Least k of a language. If the language is LL(k) via a grammar whose least k is k₀, then k₀ is
  an **upper bound** for the language. "The language is not LL(k₀ − 1)" is a separate claim about the
  language, and it needs its own proof (§2: a negative test of one grammar does not decide it). For
  k₀ = 1 nothing more is needed (k ≥ 1). Otherwise write «LL(k₀); минимальность k для языка не доказана»
  unless you have that proof.
- For a grammar: «не LL(k) при k ≤ K» when `ll_table` found no k and no certificate. Never write «не LL».
- An `uncertain` answer with the evidence that exists is better than a confident wrong one. In the eval
  a false verdict at confidence ≥ 0.6 counts as a gross error (`docs/EVAL_SET.md`).

## 9. Traps, short list (details and eval ids in `/skills/ll/traps.md`)

1. Grammar ≠ language: left recursion or an LL(k) conflict of one grammar says nothing about the
   language (ll-10 vs ll-11).
2. Strong vs full LL(k): for k ≥ 2 a strong-test conflict does not refute LL(k) (ll-08). Plain σ: a
   local follow set may still conflict (ll-09). Only `ll_table` decides.
3. k above 10: search with `max_k=12`. "Not found for k ≤ K" is not "not LL" (ll-15).
4. Right recursion such as S → aS | a is not LL(1) but is LL(2) (ll-18).
5. Look-alikes: {w b c wᴿ} LL(1) vs {w b* c wᴿ} not LL (ll-04 / ll-05), and {aⁱbʲ | i ≤ j} LL(1) vs
   {aⁱbʲ | i ≥ j} not LL (ll-06 / ll-07).
6. No closure under ∪ or under ∩ REG. Do not prove "LL" by splitting L into LL pieces, and do not
   prove "not LL" by intersecting with a regular set as if LL were closed under it (theory.md §B).
   Closure under complement and concatenation has no source, so do not claim it.
7. "Finitely many k-prefix classes ⇔ LL(k)" is **false** ([ошибка] in THEORY §3.3). Prefix classes
   only work through Theorem 4.7.4 and need the dead class D = ∅.
8. "After u₁, u₂ with the same lookahead the parser is in the same configuration" is **false**: the
   configuration includes the stack. Substitute **derivations of a nonterminal** (§3.3).
9. A bounded word generator only gives the words it found. A missing word is not "∉ L", and a
   matching sample is `bounded_pass`, not equivalence (§3.5).
10. Eliminating left recursion and factoring preserve the language under the stated preconditions,
    but they do not guarantee LL(k). Hidden recursion behind a nullable prefix needs ε/unit
    elimination first (§3.5).
