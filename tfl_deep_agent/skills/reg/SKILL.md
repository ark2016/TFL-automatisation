---
name: reg
description: >-
  Regular languages (REG) in the ИУ-9 TFL course. Use it to decide whether a language is regular and to prove the
  answer, for languages given by a predicate, a grammar, a regex (including back-references) or prose. It also covers
  computational REG tasks: build a DFA or minimal DFA, a complement or a regex, find a pumping length, or say for
  which parameters a language is regular. Load it when the IR class is reg (task types classify, prove_regular,
  prove_non_regular, classify_and_prove, compute_pumping_length, build_dfa, build_complement, build_regex,
  parametric_analysis), or when the question is «регулярен ли язык». Inside: the decision procedure, the pumping lemma,
  Myhill–Nerode and closure statements with THEORY.md and tz section numbers, which tools to call at each step
  (regex_to_dfa, dfa_vs_ir, nerode_probe, pump_check_reg, check_membership, ...), the known traps (reg-03, reg-07, reg-09, reg-12,
  reg-14), and which evidence makes a claim verified and which only bounded.
metadata:
  sources: "docs/THEORY.md §0 §2.0; agent_system/tz_tfl_agent_system.md §1.5-1.9; docs/VERDICT_POLICY.md R1-R3 R6 §4"
  companions: "theory.md traps.md"
---

# REG: is the language regular, and why

Two companion files sit next to this one. Read them with `read_file` when you write the proof:

- `/skills/reg/theory.md` has every statement below **verbatim**, with its file and section.
- `/skills/reg/traps.md` has the trap tasks, the worked routes and the tool pitfalls.

## 1. When this skill applies

- The IR class is `reg`. That is the `agent_system` IR: `language_spec.kind` is one of `predicate`, `grammar`,
  `regex`, `natural`, `arithmetic_index`, and the task types are those in the description.
- The question is regular vs non-regular, or a computation over a regular language.
- It does not apply to CFL, DCFL or LL(k) questions. Load `/skills/cfl/`, `/skills/dcfl/` or `/skills/ll/` for those.
  A language that is not regular can still be CFL, DCFL or LL, and nothing in this skill decides those classes.

## 2. Ground rules

1. **Every machine check is relative to your IR** (`data.ir_fingerprint`, e.g. `ir-69c5c9a92894`). A wrong IR makes
   every check green about another language. Encode the definition from the task text, never your conclusion. Show
   `ir_to_text` and the sample words at HITL 1. The report says "проверено относительно IR №…".
2. **Quote theorems, do not recall them.** Take the statements from `/skills/reg/theory.md` or from
   `get_theorem(id="0")` (THEORY.md §0 is the only REG section there; `list_theorems(cls="reg")` lists it). Lines
   marked **[ошибка]** quote wrong claims. Never use them.
3. **Trust comes from the tool wrapper**, and the evidence ledger records it. Your own assessment never raises
   confidence (VERDICT_POLICY §2). The caps are: `verified` 0.98, `bounded_pass` 0.85, `well_formed` 0.55,
   `not_verified` 0.40.
4. **Failure is not proof (R1).** A DFA you could not build, or `unsupported` from `regex_to_dfa`, is no evidence of
   non-regularity. A pumping attempt that did not close is no evidence of regularity. The pumping lemma is only a
   necessary condition (tz §1.5).
5. **`None` from an oracle means unknown.** It never means "not in L" (VERDICT_POLICY §4).

## 3. Decision procedure

**Step 0: IR and oracle.**
Run `describe_ir_schema(cls="reg")`, build the IR, then run `validate_ir(cls="reg", ir, check_tools=true)`. Read
`data.oracle.available`.

- `predicate`, `grammar` (CYK) and `regex` without back-references have an **exact** oracle.
- `natural`, `arithmetic_index` and back-reference regexes have **none**. `dfa_vs_ir`, `nerode_probe` and
  `check_membership` then return `unsupported`. `pump_check_reg` still runs: it checks the structure of a pumping
  argument (w(p) instantiates and |w(p)| ≥ p) but no membership (`data.semantic_check: false`), and gives
  `well_formed`. That is a destructive basis: `non_regular` at confidence ≤ 0.55 (VERDICT_POLICY R6). For a `natural`
  description in exponent notation, such as `{a^n b^n | n >= 0}`, `pump_check_reg` uses the exponent-notation
  oracle and also checks membership. That oracle is approximate (its "not in L" never refutes) when the condition
  is a comma-separated list (`n != m, n >= 0`, which its parser can misread) or when it accepts no short word;
  write one condition (`n != m`) or re-encode the IR as a predicate.
- No oracle means the 0.55 rests on your written proof alone. So on a language that may be regular (counts,
  residues, parities), re-encode the IR as a predicate first. traps.md reg-03 and reg-09 show faithful encodings.

**Step 1: look at the language** (reference only, never evidence).
`sample_words(cls="reg", ir, max_len=8)` gives short words in and out of L. `ir_to_text(cls="reg", ir)` gives the
paraphrase.

**Step 2: exact shortcuts.** Try these before you form a hypothesis.

- **The task language is a regex without back-references.** It is regular by definition: VERDICT_POLICY §1 lists
  "регулярность заданного regex/DFA" as `verified`. Run `regex_to_dfa(ir=ir)` with the task IR unchanged. The tool
  reads the pattern from the IR itself, compares the DFA with the IR's oracle on all short words, and returns
  `verified` with `data.task_definition: true`, `data.ir_fingerprint` and `data.definition_in_source_text` (whether
  the pattern occurs in the IR's `source_text`, spaces ignored). The verdict gate counts this as the `verified`
  basis of "regular", cap 0.98, only when a human approved that IR at HITL 1 and the source text holds the pattern.
  With HITL 1 off or auto-approved (`--auto-approve`, every eval run), or when the pattern is not in the task text,
  it counts like `dfa_vs_ir`: `bounded_pass`, cap 0.85. The reason: the IR is then your own formalization, and a
  natural-language task written as a regex (`{w | #a(w) = #b(w)}` as `(a|b)*`) would otherwise reach 0.98. So
  write a natural-language task as `natural` or `predicate`, never as a regex of your own. A regex you typed
  yourself, even a copy of the task's pattern, is not the task's definition: `regex_to_dfa(regex=...)` is
  `verified` only for that expression and is no basis. If the
  task's pattern uses syntax the tool does not take, `regex_to_dfa(ir=ir)` is `unsupported`. Then write an
  equivalent regex, run `regex_to_dfa(regex=..., alphabet=Σ)` and `dfa_vs_ir(cls="reg", ir, dfa)`. Its
  `bounded_pass` is the basis, cap 0.85 (R2, R6).
- **The task language is a right-linear or a left-linear grammar.** It is regular (tz §4.9). `regex_to_dfa(ir=ir)`
  compiles the IR's grammar and is `verified` for the task language on the same terms as the regex above (every
  rule's left side and right side must occur in the source text): cap 0.98 with a human-approved IR, else 0.85.
  A grammar that mixes the two directions is `unsupported`, which decides nothing (reg-12). Then guess a regex and
  use `dfa_vs_ir` (`bounded_pass`, cap 0.85).
- **Back-references.** The regex is not regular syntax, but that alone decides nothing: "`(a)\1*` = `aa*`"
  (tz §4.3). See traps.md D.

**Step 3: hypothesis** (a hint, never evidence).
The memory heuristics of tz §4.2 help:

- a comparison of two unbounded counts suggests infinite memory;
- a residue mod k suggests k states;
- a self-embedding rule S → aSb suggests infinite memory, but **only suggests** it: reg-12 and `S → aSb | aS | Sb | ε`
  are regular.

`nerode_probe(ir, estimate_depth=5)` is a heuristic estimate (`well_formed`). Choose a side, but keep the other one in
view until the evidence decides. Run `nerode_probe` with `pairs`/`family`, and `pump_check_reg`, only on a language
you will call non_regular. On a regular L with two or more classes, finitely many pairs always separate, and the
gate counts the result against a 'regular' verdict (R3). A `well_formed` `pump_check_reg` result counts against
'regular' in the same way. `estimate_depth` alone does not count.

**Step 4a: regular.**
1. Write a regex, or a DFA over the **whole** task alphabet.
2. Run `regex_to_dfa(regex=..., alphabet=Σ)`. It returns the minimal complete DFA (`n_states`).
3. Run `dfa_vs_ir(cls="reg", ir, dfa)`. It checks every word up to `max_len` (10 for |Σ| = 2) against the oracle.
   - `bounded_pass` (at least 30 decided words) means the verdict "regular" is allowed, cap 0.85 (R2, R6).
   - `refuted` comes with up to 5 shortest counterexamples. Fix the artifact. "Regular" is **forbidden** with a
     refuted artifact in this round (R2).
   - `well_formed` (an approximate oracle, or too few decided words) and `not_verified` (no decided word) do not
     support "regular" (R2).
4. Write the proof as a DFA invariant. Each state q stands for a prefix property P_q, and the P_q split Σ* into
   disjoint cases. Induction on |w| shows δ̂(q₀, w) = q ⟺ P_q(w). Each P_q must also decide membership: for q ∈ F,
   P_q(w) ⇒ w ∈ L, and for q ∉ F, P_q(w) ⇒ w ∉ L, so L = ⋃_{q∈F} P_q. With the partition this gives
   w ∈ L(A) ⟺ δ̂(q₀, w) ∈ F ⟺ w ∈ L, in both directions. A state whose property neither implies nor excludes
   membership is too coarse: split it. tz §4.12 Template 1 (`myDFA.accepts w ↔ «LANGUAGE_PREDICATE» w`) is the
   Lean shape of this.
   A short `run_dfa(dfa, words)` trace on two or three words makes the report readable (`verified` per word, about
   this DFA only).

**Step 4b: non-regular.** Use the methods in this order of strength of evidence:

1. **Myhill–Nerode family** (§4.2). Find words w₀, w₁, … that are pairwise distinguishable, and a suffix for each
   pair. Check it with `nerode_probe(ir, family={"prefix": "a^n", "suffix": "b^n", "values": [0,1,2,3,4,5],
   "suffix_at": "smaller"|"larger"})`. All pairs separated gives `bounded_pass`, which supports non_regular at cap
   0.85 (R6: "с проверкой слов оракулом → ≤ 0.85"). A claimed suffix that fails is `refuted`, and the pair record
   carries an `alternative_suffix`. The ∀ i ≠ j argument is still yours to write.
2. **Closure reduction** (§4.3). Find a regular R (often a*b*) with L ∩ R = K, where K is non-regular. K is either
   a standard example or proved non-regular by method 1. Prove the equality **in both directions**. Then check the
   claimed members and non-members of K. Take words of R, so that membership in K is membership in L:
   `check_membership(cls="reg", ir, words=[], expected={"aabb": true, "aab": false, ...})` gives `bounded_pass` or
   `refuted` for those labels. When the separating words lie in R, run the K family through `nerode_probe` on the
   task IR itself (traps.md C, "Transfer").
3. **Pumping lemma** (§4.1). Write it with the exact quantifiers, and handle **every** split with |xy| ≤ p,
   |y| ≥ 1. Then check it with `pump_check_reg(ir, word="a^p b^p", exit_i=[0, 2])`: `word` is your w(p) as a
   pattern in p, `exit_i` the exponents your argument uses to leave L. The tool always checks p = 2, 3, 4
   (VERDICT_POLICY §4); `p_values` only adds larger p. For each p it checks |w(p)| ≥ p and, with an oracle,
   w(p) ∈ L. Then it pumps **every** split with |xy| ≤ p, |y| ≥ 1 at i = 0, 2..6. The result is at most
   `well_formed`, so `non_regular` at confidence ≤ 0.55 (VERDICT_POLICY §4 reg/pumping, R6): finitely many p and i
   never prove the claim for all p. It is `refuted` only when |w(p)| < p or when w(p) ∉ L (exact oracle). A split
   that keeps x yⁱ z in L for every i in `exit_i` is an **unclosed split**: the result is `not_verified` (no basis),
   not `refuted`, because the tried i are not all i. Read `data.per_p`: each unclosed split lists `exits_at`, the
   exponents at which it does leave L; fix the argument and check it again. `exit_i` is the same for every p and
   every |y|. An argument whose exit depends on p or |y| (reg-10: w = aᵖbᵖ⁺ᵖ!, i = p!/|y| + 1) cannot be checked
   here and stays unclosed. For such a task re-encode the IR as a predicate and use a Nerode family (method 1). If
   your argument needs p ≥ p₀, pass `p_min=p₀` (the tool then checks p₀, p₀+1, p₀+2) and say so in the proof.
   Closing every split is not a sign that L is non-regular: a regular L whose period or pumping length exceeds the
   largest p tried closes them too (`(a⁵)*` with w = a⁵ᵖ and exit 0 closes all of them at p = 2, 3, 4; THEORY §2.0
   has the same point for {a¹⁰}). So before you rely on `pump_check_reg`, try a DFA (`dfa_vs_ir`) on any language
   that might be regular. Without an oracle only the structure is checked (see step 0). `nerode_probe` needs a REG
   oracle (a `predicate`, `grammar` or `regex` IR); a `natural` IR in exponent notation (reg-01, reg-05, reg-10)
   has an oracle for `pump_check_reg` only, so re-encode it as a predicate first. A Nerode family (method 1) then
   gives the stronger basis, 0.85.

**Step 5: contradictions** (R3, R3′).
Sometimes a DFA has `bounded_pass` and a destructive argument has at least `well_formed`. Then run the destructive
witness words through the DFA (`run_dfa`) and through the oracle (`check_membership`).

- If the DFA and the oracle disagree on a word, the artifact is refuted.
- If the oracle contradicts a membership claim of the proof, the proof is refuted.
- If neither happens and exactly one side is `verified` (a `lean_check` with status `proved` for this IR, or
  `regex_to_dfa(ir=ir)` on the task's own regex or linear grammar, human-approved IR), that side keeps the verdict, ceiling 0.85
  (R3). If the verified side is the opposite of yours, your verdict is blocked; report the verified side's verdict.
- Otherwise (neither side is `verified`) the answer is `partial` (no verdict), confidence ≤ 0.50, with both sides in
  the report. `bounded_pass` against `well_formed` does not decide it (R3).
- The gate does not yet apply R3′ to a refuted DFA. An earlier `dfa_vs_ir` `bounded_pass` for the same DFA still
  counts against a non_regular verdict, even after a `dfa_vs_ir` refutation of that DFA, so the published result
  stays `partial` at 0.50.

**Step 6: report.**
Every claim cites ledger evidence ids, and a fabricated id makes the claim `not_verified`. The verdict is `regular`
or `non_regular`. Use `partial` when no admissible basis exists (R1, R4′). The confidence is the cap of the strongest
supporting evidence, see §5. Choose the template with `list_report_templates`, then call `submit_report` (HITL 2).

**Subagents.** Searching for a Nerode family or a DFA is cheap work. You may delegate it with `spawn_subagent`
(Sonnet or Haiku, tools `sample_words`, `check_membership`, `nerode_probe`, `get_theorem`). Writing the proof and
reviewing it stay on the prover and critic roles.

## 4. Proof methods: the statements (verbatim in theory.md)

### 4.1 Pumping lemma for REG — tz §1.5 (Hopcroft Th. 4.1.1); split condition THEORY.md §0

Source: `agent_system/tz_tfl_agent_system.md` §1.5

> ∀n ∃w∈L (|w|≥n) ∀x,y,z (w=xyz ∧ |xy|≤n ∧ |y|≥1) ∃i≥0: xy^i z ∉ L

Source: `docs/THEORY.md` §0

> Для регулярной накачки используется z=xyz, |xy|≤p, |y|>0

- You choose w after n is fixed, and it must lie in L with |w| ≥ n. The split is the adversary's. i may depend on the
  split. i = 1 is useless.
- |xy| ≤ n puts y inside the first n letters. The CFL window |vwx| ≤ p is a **different** condition. Never mix them
  up (THEORY.md §0).

### 4.2 Myhill–Nerode — tz §1.6 (Hopcroft Th. 3.4.3)

Source: `agent_system/tz_tfl_agent_system.md` §1.6

> L регулярен ⟺ индекс ≡_L конечен (т.е. число классов эквивалентности конечно).

- u ≡_L v ⟺ ∀z ∈ Σ*: (uz ∈ L ⟺ vz ∈ L). A non-regularity proof needs an **infinite** pairwise-distinguishable family.
  Finitely many separated pairs give only a lower bound: n pairwise-distinguishable words show index ≥ n
  (THEORY_REFERENCE #reg), never that the index is infinite (VERDICT_POLICY §4; THEORY.md §0: "бесконечное семейство
  различимых префиксов, а не конечная оценка индекса Нероуда"). The representatives need not lie in L, and the empty
  suffix is allowed.
- "Более того, количество классов ≡_L равно числу состояний минимального ДКА для L" (tz §1.6). This is the minimality
  argument for `build_dfa` (§6).

### 4.3 Closure — tz §1.8

Source: `agent_system/tz_tfl_agent_system.md` §1.8

> 1. **Пересечение с регулярным:** Если L ∩ R нерегулярен для некоторого регулярного R → L нерегулярен.

- The other spec items: an erasing homomorphism h (if h(L) is non-regular, so is L), the inverse homomorphism, and the
  complement.
- A non-regular **subset** proves nothing. The equality has to be proved (THEORY_REFERENCE #reg).
- Regularity transfers through Boolean operations from regular parts. Non-regularity does not (VERDICT_POLICY §4).

### 4.4 Positive methods

- **A DFA or regex** with its invariant proof, plus `dfa_vs_ir` (§3, step 4a).
- **A finite Nerode index.** Name the classes. Since the number of classes equals the number of states of the
  minimal DFA (tz §1.6), present them as a DFA whose states are the classes, and prove and check it like any other
  DFA.
- **Closure.** Write L as a Boolean combination, concatenation, star, reverse or (inverse) homomorphic image of
  languages already shown regular (tz §1.8 table).

## 5. Evidence: what makes a claim verified, bounded, or neither

| Claim | Evidence (tool) | Trust | Cap |
|---|---|---|---|
| the task language is regular: its own regex or linear grammar, compiled from the IR | `regex_to_dfa(ir=ir)` on an IR a human approved at HITL 1, whose `source_text` holds the definition | `verified` | 0.98 for regular |
| the same record with HITL 1 off or auto-approved, or a definition not in the task text | `regex_to_dfa(ir=ir)` (counts like `dfa_vs_ir`) | `bounded_pass` | 0.85 for regular |
| the DFA recognises exactly L(the regex or linear grammar you passed) | `regex_to_dfa(regex=...)` / `regex_to_dfa(grammar=...)` | `verified` | supporting step only, never the verdict basis |
| Lean proof of the IR-generated statement (lean skill; P6 tools) | `lean_check` status `proved` | `verified` | 0.98 |
| your DFA or regex recognises the task language | `dfa_vs_ir` agreement on ≥ 30 decided words, all words up to `max_len` | `bounded_pass` | 0.85 |
| the family's sampled pairs are Nerode-distinguishable | `nerode_probe`, every pair separated | `bounded_pass` | 0.85 for non_regular |
| a pumping argument: w(p) and its exit exponents, checked at p = 2, 3, 4 (with an oracle: w(p) ∈ L and every split; without one: structure only) | `pump_check_reg` | `well_formed` | 0.55 for non_regular |
| a pumping argument with an unclosed split (stays in L at every claimed exit) | `pump_check_reg` | `not_verified` | no basis; not a refutation |
| claimed members / non-members (intersection examples, witness words) | `check_membership` with `expected` | `bounded_pass` / `refuted` | claim check only |
| a closure argument in prose, with no oracle check | none: the gate has no record for it | no basis: `non_regular` is published as `partial` (R1) | 0.40 |
| the output of code you ran in the sandbox | `execute` | at most `well_formed`, never a basis for the verdict | supports steps only |
| labelled samples, membership without `expected`, theorem text, the Nerode estimate | `sample_words`, `check_membership`, `get_theorem`, `nerode_probe(estimate_depth)` | `not_verified` | 0.40 |
| a counterexample to your artifact or claim | `dfa_vs_ir` / `nerode_probe` / `pump_check_reg` / `check_membership` | `refuted` | blocks that claim |
| an unresolved contradiction between the two sides | R3 | — | 0.50 |

- **`verified`** means a complete deterministic check of exactly the stated claim. `run_dfa`, and `regex_to_dfa`
  with `regex` or `grammar`, are `verified` for **the given expression or DFA**, not for the task language (the tool
  says so). `regex_to_dfa(ir=ir)` is the one exception: the tool reads the task's own regex or linear grammar from
  the IR, so its `verified` result is about the task language. The basis for "regular" is `regex_to_dfa(ir=ir)`
  (`verified`, 0.98, with a human-approved IR whose source text holds the definition; else `bounded_pass`, 0.85)
  when the task's definition is a regex or a linear grammar, otherwise `dfa_vs_ir` (`bounded_pass`, 0.85), or
  `lean_check` `proved` (`verified`, 0.98).
- **`bounded_pass`** is always reported with its bounds (`max_len`, `n_words`, `n_unknown`). Write them in the
  report, e.g. «совпадает с оракулом на всех 2047 словах длины ≤ 10».
- "Regular" needs `regex_to_dfa(ir=ir)` on the task's own definition, a check of your artifact against the task
  (`dfa_vs_ir` at `bounded_pass`, ≥ 30 decided words), or a Lean `proved` result (R-Lean, 0.98). Without one the
  gate publishes `partial` (verdict null), confidence ≤ 0.40 (VERDICT_POLICY R2).
- "Non-regular" needs `nerode_probe` pairs (`bounded_pass`, 0.85), a `pump_check_reg` claim (`well_formed`, 0.55)
  or a Lean `proved` result (0.98). A `refuted` or `not_verified` result of either tool is no basis. A `well_formed` `dfa_vs_ir` (fewer than 30 decided words, or an approximate oracle) is not
  enough, so 0.55 is never reached on this side. In the 2026-09-27 Haiku run of the old pipeline, reg-03 and reg-09
  (both `natural` IRs, with no oracle) ended at 0.40 (docs/EVAL_RESULTS.md; the file does not record the cause).

## 6. Recipes for the computational task types

- **`build_dfa`.**
  1. `regex_to_dfa(regex=..., alphabet=Σ)`, with Σ the **whole** task alphabet, gives the minimal complete DFA.
  2. Check that DFA against the IR with `dfa_vs_ir`.
  3. To argue minimality, pick an access word for each state. For each pair u, v of access words, find a suffix z
     with exactly one of uz, vz in L. The empty suffix is allowed. Read z off the DFA: it is a word that takes one
     state into F and the other out of F. Check the labels against the task with
     `check_membership(cls="reg", ir, words=[], expected={uz: true, vz: false, ...})`, which gives `bounded_pass`
     for those labels. A `run_dfa` trace on the same words shows that the DFA agrees. Pairwise separated words lie
     in pairwise different classes of ≡_L, so the index is ≥ `n_states`. Then cite tz §1.6: the number of classes
     equals the number of states of the minimal DFA. tz §1.2 defines δ as total, so a dead state counts as a
     state. Say so if the course answer omits it.
     Do **not** use `nerode_probe` with `pairs` or `family` for this. The verdict gate reads every such result in
     the run as destructive evidence, whether it is cited or not and including subagents' results. A "regular"
     verdict next to it is an unresolved R3 contradiction: `partial`, confidence ≤ 0.50.
- **`build_complement`.**
  1. Build the complete DFA over the **whole** alphabet with `regex_to_dfa(..., alphabet=Σ)`, and flip F (tz §1.8:
     "Инверсия допускающих состояний ДКА"). A partial DFA must be completed first (traps.md D).
  2. Check it with `run_dfa` on the words, then `check_membership` on the original IR with `expected` set to the
     negated labels.
  3. Alternatively, write the complement as an IR (`predicate` with `op: not`) and run `dfa_vs_ir`.
  4. The flipped DFA has the same number of states. u ≡_L v holds iff u ≡ v for Σ*∖L, straight from the definition
     in tz §1.6, so minimality carries over.
- **`build_regex`.** Write the regex, then run `regex_to_dfa` and `dfa_vs_ir` against the IR. To compare it with a
  given regex, put the given regex in a `regex` IR and run `dfa_vs_ir` against that.
- **`compute_pumping_length`.**
  1. The definition is in tz §1.9: the minimal n for which the lemma holds.
  2. An upper bound is p ≤ the number of states of a DFA for L (tz §1.9), so `n_states` of the minimal DFA. That
     is only a bound. To show the lemma holds for a smaller n, argue for **every** w ∈ L with |w| ≥ n that there is
     a split w = xyz with |xy| ≤ n, |y| ≥ 1 and xyⁱz ∈ L for **all** i ≥ 0 (tz §1.5). The ∀ w and ∀ i parts are
     your argument. A `run_dfa` check on finitely many words or values of i is not a proof.
  3. For a lower bound, show that n − 1 fails (the tz §1.5 quantifier order with the constant fixed at n − 1:
     ∃w ∀x,y,z ∃i). Give a word w ∈ L with |w| ≥ n − 1 such that **every** split w = xyz with |xy| ≤ n − 1,
     |y| ≥ 1 has one i ≥ 0 with xyⁱz ∉ L. For a fixed w there are finitely many such splits, and each needs only
     one witness i. So the whole lower bound is a finite check: run `run_dfa` on the minimal DFA for each pumped
     word xyⁱz (`verified` per word).
  4. State which formulation you use (tz §1.5, |w| ≥ n). The tz §1.9 regex bound and its NP-hardness remark are
     **not reviewed** in THEORY.md (theory.md §5). Do not cite them.
- **`parametric_analysis`.** Split into parameter cases, including the degenerate ones (equal parameters, an empty
  or unary alphabet; traps.md C "Counting filters"). Each case is a full REG task with its own IR, its own evidence
  and its own verdict.

## 7. Traps checklist (details in traps.md)

- **reg-14 G_reg.** "L ∩ a*b* = {aⁿbⁿ}" is false, and so is "≡ mod 2, regular". The real intersection is
  {aᵐbᵏ | m ≡ k (2), m ≤ 3k+2}. Check intersection claims with `check_membership(expected)`.
- **reg-12 S → aS | Sb | ε = a*b*.** Self-embedding or mixed recursion is no evidence.
- **reg-03, reg-09.** Counters mod k, and parity of i+j over a*b*, are regular. The eval IRs are `natural`, so
  re-encode them as predicates to get an oracle.
- **reg-07 a^{n²}.** A unary language with no oracle. Write the pumping argument on a^{p²} and check it with
  `pump_check_reg(ir, word="a^(p^2)", exit_i=[2])`: structure only, `well_formed`, so `non_regular` at ≤ 0.55. Only
  a Lean `proved` result goes higher.
- **reg-05.** Nerode suffixes must be instantiated at the right index (`suffix_at: "larger"`).
- **Pumping.** Do not choose the split, do not use |vwx| ≤ p, do not use i = 1, and do not treat a finite p or i
  check as proof. `pump_check_reg` checks finitely many p and i, so its result stays `well_formed`; a regular L
  with a long period ((a⁵)*) closes every split at p = 2, 3, 4 too. An unclosed split is `not_verified`, not
  `refuted`; an exit that depends on p or |y| (reg-10) cannot be stated, so use a Nerode family on a predicate IR.
- **Closure.** A non-regular subset is not enough. Prove both inclusions, and remember Boolean combinations of
  non-regular parts can be regular.
- **Artifacts.** Complement only complete DFAs. Tool regex syntax differs from the IR's Python regex
  (`regex_to_dfa(ir=ir)` checks the two against each other). A back-reference alone decides nothing. Example mock
  artifacts can be wrong.
- **Lean** proof bodies are live model calls. Write them only when the user asked for Lean.
