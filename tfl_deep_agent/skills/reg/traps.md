# REG traps and worked notes

Companion to `/skills/reg/SKILL.md`. Each entry gives the trap, where it comes from, and the tool check that catches
it. Blocks after a `Source:` line are verbatim (a test checks this). Eval ids refer to `docs/EVAL_SET.md`. The five
manifest traps are reg-03, reg-07, reg-09, reg-12 and reg-14.

## A. Eval trap tasks

### reg-14: the grammar G_reg and the false intersection (THEORY.md §0)

Source: `docs/EVAL_SET.md`

> | reg-14 | грамматика S → SaSb \| ε \| A, A → bb \| aa \| bSb | non_regular | THEORY.md §0: L ∩ a*b* = {aᵐbᵏ \| m ≡ k (2), m ≤ 3k+2} **[trap: ложное «= {aⁿbⁿ}»]** |

- The verdict non_regular is right. Two old pipeline prompts stated wrong intersections in their examples:
  "= {aⁿbⁿ}", and "= {aᵐbᵏ | m ≡ k (mod 2)}, regular". Both are **[ошибка]** lines in THEORY.md §0, see `/skills/reg/theory.md` §6.
- Catch it: before you use any claimed intersection, label its small words with
  `check_membership(cls="reg", ir, words=[], expected={...})`. The THEORY.md §0 counterexamples `bb`, `aa`, `abbb`,
  `aaaaab` (= a⁵b) are in L, and `aaaa` (= a⁴) and `aaaaaaab` (= a⁷b) are not. An expected label that the exact CYK
  oracle contradicts gives `refuted`.
- Proof route: the family a^{3n+2} with suffix bⁿ (THEORY.md §0).
  `nerode_probe(ir, family={"prefix": "a^(3n+2)", "suffix": "b^n", "values": [0,1,2,3,4]})` gives `bounded_pass` on
  this IR (`agent_system/examples/task2_grammar_sasb.json`). The ∀-argument (m ≤ 3k+2 by induction over the
  derivation) is in THEORY.md §0. Golden report: `docs/examples/reg_grammar_SaSb_lean_proved/`.

### reg-12: S → aS | Sb | ε is regular

Source: `docs/EVAL_SET.md`

> | reg-12 | грамматика S → aS \| Sb \| ε | regular | L = a*b* **[trap: не право-линейная, но регулярный язык]** |

- The grammar mixes right and left recursion, so neither linear-grammar criterion applies. `regex_to_dfa(grammar=...)`
  returns `unsupported`, which is **not** evidence of non-regularity (the tool says so).
- Self-embedding is not evidence of non-regularity either. `agent_system/prompts/classifier.md` uses the example
  `S -> aSb | aS | Sb | epsilon`, which generates `a*b*`.
- Route: guess `a*b*`, then `regex_to_dfa(regex="a*b*", alphabet=["a","b"])`, then `dfa_vs_ir(ir, dfa)`. This gives
  `bounded_pass` (all 2047 words up to length 10). The proof needs both inclusions: every derivation keeps all a's
  left of S and all b's right of it, and aⁱbʲ is derived by i steps S → aS, then j steps S → Sb, then S → ε.

### reg-03: #a ≡ #b (mod 2) is regular

Source: `docs/EVAL_SET.md`

> | reg-03 | {w ∈ {a,b}* \| #a(w) ≡ #b(w) (mod 2)} | regular | ДКА на 2 состояния **[trap: счётчики, но по модулю]** |

- Two counters, but only their residues matter. The REG spec's memory heuristic says
  "Modular(count_symbol, k, r) → finite, states = k" (tz §4.2). That is a hint, not a proof.
- The eval IR is `kind: natural`, so it has **no oracle**. Re-encode the definition faithfully as a predicate, without
  building your answer into it:
  `{"op":"or","operands":[{"op":"and","operands":[A0,B0]},{"op":"and","operands":[A1,B1]}]}`, where
  `Ar = {"expr":{"kind":"count_symbol","symbol":"a","in_var":"w"},"modulus":2,"remainder":r}`, and `Br` is the same
  with `b`.
- Over {a,b}, #a + #b = |w| and −1 ≡ 1 (mod 2), so the language is "|w| even". Check it with
  `regex_to_dfa(regex="((a|b)(a|b))*")` and then `dfa_vs_ir`, which gives `bounded_pass`.
- `docs/EVAL_RESULTS.md` (Haiku, 2026-09-27): the old pipeline answered regular at confidence 0.40; the file gives
  no cause. Without a `dfa_vs_ir` `bounded_pass` (or a Lean `proved` result), "regular" is published as `partial`,
  confidence ≤ 0.40 (VERDICT_POLICY R2).

### reg-09: i + j even over a*b* is regular

Source: `docs/EVAL_SET.md`

> | reg-09 | {aⁱbʲ \| i + j чётно} | regular | a*b* ∩ (ΣΣ)* **[trap: выглядит как счёт]** |

- An intersection of two regular languages. The eval IR is `natural`. A faithful predicate is an `and` of an
  `exists_decomposition` (parts x, y; `count_symbol b in x = 0`; `count_symbol a in y = 0`) and
  `{"expr":{"kind":"length","of_var":"w"},"modulus":2,"remainder":0}`.
- `(aa)*(bb)*|a(aa)*b(bb)*` passes `dfa_vs_ir` on that IR. EVAL_RESULTS: the old pipeline also answered 0.40 here.

### reg-07: {a^{n²}} is not regular

Source: `docs/EVAL_SET.md`

> | reg-07 | {a^{n²} \| n ≥ 0} | non_regular | унарный язык непериодичен **[trap: унарный]** |

- No oracle exists: the eval IR is `natural`. The predicate IR has no squares, `arithmetic_index` has no oracle, and the
  CFL exponent-notation oracle only takes linear conditions. So `nerode_probe`, `check_membership` and
  `pump_check_cfl` return `unsupported`. So no ledger record supports `non_regular`: for REG the gate counts only
  oracle-backed `nerode_probe` pairs and `lean_check` `proved`. Without a Lean proof the report is published as
  `partial`, confidence ≤ 0.40 (R1). Sandbox enumeration is never a basis for the verdict.
- Pumping route (tz §1.5 quantifiers): for p, take z = a^{p²}. Every split has y = a^k with 1 ≤ k ≤ p, so
  xy²z = a^{p²+k} and p² < p²+k ≤ p²+p < (p+1)². So p²+k is not a square, and xy²z ∉ L.

## B. Other eval tasks with a known pitfall

- **reg-05 {aⁿbᵐ | n ≥ m ≥ 0}: the suffix depends on the larger index.** For prefixes aⁱ, aʲ with i < j, the suffix
  bʲ separates them: aʲbʲ ∈ L, aⁱbʲ ∉ L. The suffix bⁱ does not: both aⁱbⁱ and aʲbⁱ are in L. With
  `nerode_probe(family={"prefix":"a^n","suffix":"b^n","values":[...],"suffix_at":"larger"})` you get `bounded_pass`.
  The default `suffix_at: "smaller"` is `refuted` (e.g. ε, a with z = ε). Read a `refuted` pair's
  `alternative_suffix` before you change the proof.
- **reg-10 {aⁱbʲ | i ≠ j}.** EVAL_SET: "дополнение ∩ a*b* = {aⁿbⁿ}". Both closure steps (complement, ∩ a*b*) must be
  named. A direct Nerode family also works: aⁱ, aʲ (i ≠ j) are separated by bⁱ.
- **reg-02 {#a = #b}.** The tz §1.8 example: L ∩ a*b* = {aⁿbⁿ}. Prove both inclusions.
- **reg-13 S → aSb | ab** is {aⁿbⁿ | n ≥ 1}. The family aⁿ / bⁿ gives `bounded_pass` with values ≥ 1 (e.g.
  `"values": [1,2,3,4,5]`) or with `suffix_at: "larger"`. If the values include n = 0 and you keep the default
  `suffix_at: "smaller"`, it is `refuted`: the pair ε, a gets the suffix b⁰ = ε, and ε ∉ L, a ∉ L. A DFA for `a+b+`
  is `refuted` by `dfa_vs_ir` with the counterexample `aab`.

## C. Proof-method traps

### Pumping lemma (REG)

Source: `docs/THEORY.md` §0

> Условие КС-леммы |vwx|≤p не ограничивает положение окна и здесь его подставлять нельзя.

- Quantifiers (tz §1.5): ∀n ∃w ∀(x,y,z) ∃i. You choose w after n is fixed. The adversary chooses the split, so
  **every** split with |xy| ≤ n, |y| ≥ 1 must be handled. i = 1 never helps (xy¹z = w). These rules are also in
  `agent_system/prompts/pumping_agent.md`.
- The word must be in L and have length ≥ n for every n, so give it as a family in n.
- There is no `pump_check_reg` tool (TOOL_INVENTORY §3: deferred). One concrete split can be run as a diagnostic
  through `pump_check_cfl` split mode with `cls="reg"`. Map x → `u`, y → `v`, z → `w` and leave `x`, `y` empty.
  **Do not pass `p`**: the tool would then check the CFL window |vwx| ≤ p, which here is |yz|, the wrong condition.
  Check |xy| ≤ p yourself. The result is at most `not_verified` (`evidence_role: "diagnostic"`). It is `refuted` only
  when z ∉ L or when a claimed exit is in L.
- A pumping argument produces no gate record in the deep agent (VERDICT_POLICY §4 reg/pumping allows at most
  `well_formed`, and no REG pumping tool exists). On its own it leaves the verdict `partial` (≤ 0.40). If an oracle
  exists, prefer a Nerode family for the evidence and keep pumping as the written argument.

### Myhill–Nerode

- A finite set of separated pairs proves nothing about infinite index (VERDICT_POLICY §4). The report needs the
  ∀ i ≠ j argument in prose. `nerode_probe` checks finitely many instances (`bounded_pass`).
- The representatives need not be in L: "The words need not be in L themselves" (`agent_system/prompts/nerode_agent.md`).
  The empty suffix is allowed (VERDICT_POLICY §4).
- "No suffix found up to length L" is `not_verified`. It is not an equivalence proof.
- `estimate_depth`, `likely_infinite` and the growth pattern are heuristics (`well_formed` at most). Never cite them as
  "the index is infinite".

### Closure reductions

- Prove the equality L ∩ R = K in both directions. A non-regular subset proves nothing (THEORY_REFERENCE #reg,
  quoted in `/skills/reg/theory.md` §3).
- Check the claimed members and non-members of L ∩ R with `check_membership(expected=...)`. Precedent, THEORY.md §5
  (part II): a live run claimed L ∩ a⁺b⁺ = {aⁿbⁿ} for a grammar that generates only {a²ᵐb²ᵐ | m ≥ 2}.
- Transfer to L itself: suppose a Nerode family separates K = L ∩ R, and every separating word lies in R
  (uz ∈ K, vz ∈ R \ K). Then the same words separate L, because vz ∈ R and vz ∉ L ∩ R give vz ∉ L. So you can run
  `nerode_probe` on the task IR directly, and the evidence is about L, not about K.

### Counting filters and Boolean combinations

Source: `docs/THEORY_REFERENCE.md` #filters

> При смешанных знаках насыщение неверно: последующие буквы могут уменьшить значение. Контрпример: F={w | #a(w)−#b(w)≥0} не регулярно над {a,b}. Префиксы aⁿ и aᵐ при n<m различает суффикс bᵐ.

Source: `docs/THEORY_REFERENCE.md` #filters

> Но #a=#a задаёт Σ*, а #a<#a — ∅. Дегенерации алфавита и область переменной должны проверяться.

- A non-regular part does not make the union or intersection non-regular (VERDICT_POLICY §4). The parametric tasks
  ("for which ξ₁, ξ₂", tz §2 category F) hinge on exactly such degenerate cases. Treat each parameter case as its
  own task with its own IR and evidence.

## D. Artifact and tool traps

- **Partial DFA.** `run_dfa` accepts a DFA with missing transitions (implicit dead state, flagged `partial_dfa`).
  Complementing it by flipping the accept states is wrong, because the words that fall into the implicit dead state
  are lost. Complement only a complete DFA over the **whole** task alphabet: `regex_to_dfa(..., alphabet=Σ)` always
  returns the minimal complete one. (DEV_NOTES P1 reg: the lib's `minimize_dfa` is wrong on partial DFAs, and
  `regex_to_dfa` completes before minimizing.)
- **Regex dialects.** `regex_to_dfa` takes single-character literals, `|`, `*`, `+`, `?`, `()`, `\`. It rejects `.`,
  `[`, `]`, `{`, `}`, `^`, `$`, spaces, `ε` and back-references. Write ε as `()`. The IR's regex oracle is a Python
  full match of `pattern`. If you rewrite the task's regex to fit the tool, the DFA describes **your** rewrite. Run
  `dfa_vs_ir` against the original IR.
- **Back-references.** "`(a)\1*` = `aa*`" (tz §4.3): a back-reference alone decides nothing. A regex IR with
  `has_backreferences: true` has no oracle, and `regex_to_dfa` refuses it. Re-encode the language as a predicate if
  you can, or reason by hand and stay at `well_formed`.
- **The oracle checks the IR, not the task.** A wrong IR makes every check green about another language. Show the
  `ir_to_text` paraphrase and the sample words at HITL 1. Encode the definition, never your conclusion (see reg-03).
  Write "проверено относительно IR №<ir_fingerprint>" in the report.
- **Wrong mock artifacts exist.** `agent_system/examples/task1_palindrome_prefix_suffix_{dfa_builder,re_builder}_output.json`
  ("starts or ends with aa / bb") are refuted by `abba` (`dfa_vs_ir`, DEV_NOTES P1 reg). Never copy an example
  artifact without running it against the IR.
- **Lean is a live call.** EVAL_RESULTS, incident 2026-09-29: a REG formalizer run hit the 64k output-token limit and
  cost about $2.3 with no result. Lean proof bodies are written only on explicit request (PLAN, CLAUDE.md).
