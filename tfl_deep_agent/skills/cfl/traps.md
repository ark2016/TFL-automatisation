# CFL traps

Supporting file of the `cfl` skill. Read `SKILL.md` first. Sources: docs/EVAL_SET.md (the CFL table,
expected verdicts), docs/THEORY.md §2–§2.5, docs/EVAL_RESULTS.md (live Haiku runs), docs/VERDICT_POLICY.md,
the golden example `docs/examples/cfl_ticket50_w1bw2w3_lean_proved/`. A test checks the ids and expected
verdicts below against EVAL_SET. The arguments themselves live in THEORY.md: fetch them with `get_theorem`
and do not rebuild them from this list.

## 1. Eval tasks where the obvious answer is wrong

| id | language (as in EVAL_SET) | expected | why it is a trap | what to do |
|---|---|---|---|---|
| cfl-06 | {w₁w₂w₁w₃ \| w₁∈{a,b}⁺, w₂∈{b,c}⁺, w₃∈{a,c}⁺} | non_cfl | The intersection with a⁺b⁺a⁺c⁺ is {aⁿbᵐaʲcᵏ \| j ≥ n}, which is CFL: the extra a's go into w₃. Pumping L directly is unreliable because a pumped word can be split again. | `get_theorem("2")`: R = a⁺b⁺ac·a⁺b⁺ac, then pumping of L ∩ R with i = 0. Check the description of L ∩ R with `check_membership` on words of R (SKILL.md §4.3, step 3). |
| cfl-07 | S → aSbb \| ε \| bbSa \| aA, A → aA \| a; фильтр #a = #b | non_cfl | "A counter filter is regular, so CFL ∩ REG" is false: equality of two counters is not a regular filter. In the 2026-09-26 run, non_cfl 0.92 came after an inversion with no destructive proof (R1). | `get_theorem("2.3")`: intersect with b*a*b*a*, then Ogden with z = b²ᵖa³ᵖb²ᵖaᵖ, marking the first b-block. |
| cfl-08 | {wwvvᴿ \| w,v ∈ {a,b}*} | non_cfl | "L = Σ* because w, v may be empty" is false (every word of L has even length). The grammar S → W W V with independent W generates w₁w₂vvᴿ, which is not L. A decomposition into {ww}·{vvᴿ} fails because {ww} is not CFL. | `get_theorem("2.5")`: R* = a(aa)*b·a(aa)*b·a(aa)*b·a(aa)*b, then pumping. If a grammar "passes" the sample, cross-check it (SKILL.md Step 6). |
| cfl-11 | {a^{6n}b^{6n}c^{6n}} | non_cfl | The same as cfl-01, but the multiplier changes the instances: z must be in L. With z = a^{6p}b^{6p}c^{6p}, the p = 3 instance a¹⁸b¹⁸c¹⁸ has 54 letters, more than the exponent-notation oracle's `max_word_len` 40, so it is undecided and nothing is pumped. | Compute the instances. After `pump_check_cfl`, read `bounds.p_values` and `bounds.n_unknown`: an empty `p_values` means no instance was checked. Prefer a parametric word whose p = 3, 4 instances stay within the oracle bound, if your argument allows it; otherwise say in the report that the instances were not checked. |
| cfl-12 | {aⁱbʲcᵏdˡ \| i = 0 ∨ j = k = l} | non_cfl | Plain pumping fails: the language satisfies the pumping condition with p = 1 (THEORY.md §2.0). The live run after C2 spent most of its budget ($0.80 of $1.07) on grammars for a wrong `cfl` hypothesis. | Ogden: z = abᵖcᵖdᵖ, mark all b, i = 2 (`get_theorem("2.0")`). Pass `marked_positions` per p to `pump_check_cfl`. A plain-pumping claim on z = abᵖcᵖdᵖ also comes back `well_formed`, but with `bounds.unresolved_splits` = 9 (status `inconclusive`). Those splits pump only the leading a and stay in L for every i. The Ogden claim closes all 2232 splits (`closed_for_sampled_p`). |
| cfl-17 | S → aSb \| ε с фильтром \|w\| ≡ 0 (mod 4) | cfl | Here the filter is regular (a length condition modulo 4). `input_grammar_cfg` does not apply, because the IR is a `grammar_filter`. | Build a grammar for L(G) ∩ F, for example S → aaSbb \| ε, and check it with `grammar_vs_ir` (expect `bounded_pass`). |
| cfl-19 | {aⁿbᵐaⁿ \| n,m ≥ 0} | cfl | It looks like a copy language, but it is nested: S → aSa \| B, B → bB \| ε. | Constructive branch first; `input_grammar_cfg` if the IR is that grammar. |

Other CFL rows of EVAL_SET that are easy to get wrong:

| id | language (as in EVAL_SET) | expected | note |
|---|---|---|---|
| cfl-02 | {aⁱbʲcᵏ \| i = j ∨ j = k} | cfl | Inherently ambiguous (THEORY.md §1.5), but still a union of two CFLs. Ambiguity is a DCFL argument, never a non-CFL one. |
| cfl-03 | {ww \| w ∈ {a,b}*} | non_cfl | As `natural` text it has no oracle. Write it as `exists_decomposition` with `concat_pattern: ["w", "w"]` so that the split-search oracle exists. |
| cfl-05 | {aⁱbʲcᵏ \| i ≤ j ≤ k} | non_cfl | EVAL_SET: pumping with z = aᵖbᵖcᵖ, all cases, i ∈ {0, 2}. Different cases need different i. |
| cfl-10 | {a^{2ⁿ} \| n ≥ 0} | non_cfl | EVAL_SET's justification is "унарный КС = регулярный; Париха". THEORY.md has no section on this, so do not quote it as a checked theorem. Its `natural` IR has no oracle, so every check is structural only (cap 0.55 at best). |
| cfl-18 | {aⁿbⁿ} ∪ {aⁿb²ⁿ} | cfl | A union of two CFLs. It is not DCFL (eval dcfl-05); do not mix the two questions. |
| cfl-20 | {aⁿbⁿaⁿ} | non_cfl | Like aⁿbⁿcⁿ with the third block written as a's again. |

## 2. Failure patterns from runs and reviews

1. **A wrong instance of a correct parametric word.** The ticket-50 run (golden example
   `cfl_ticket50_w1bw2w3_lean_proved`) chose z = a²ᵖ·b·(ab)ᵖ·a²ᵖ, which is correct, but printed `a⁸b(ab)⁵a⁸`
   (length 27) for p = 4. That word is not in L; the correct instance is a⁸b(ab)⁴a⁸ (length 25).
   `pump_check_cfl` returns `refuted` for the wrong instance (pinned by a test). Generate instances by
   substituting p into the parametric word, then check z ∈ L with `check_membership` before you submit.
2. **Finite closure taken as proof.** Every split closing for p = 3, 4 and i = 0, 2, 3 is a diagnostic
   (THEORY.md §2.0: L = {a¹⁰} closes too). The trust stays `well_formed`.
3. **Survival taken as refutation.** A split that stays in L for i ∈ {0, 2, 3} does not refute an
   argument: the exit may be at a larger i, and the needed i may depend on p and the split.
4. **Nested pumping checked against the wrong language.** In a closure argument, check the pumping
   against an IR of L ∩ R. A pumped word that left R can still be in L.
5. **Wrong intersection description.** Before pumping L ∩ R, check the claimed equality on words of R
   and of L′ (SKILL.md §4.3, step 3). Precedents: cfl-06 above; a closure_reduction that claimed
   L ∩ a⁺b⁺ = {aⁿbⁿ} for a grammar that generates only {a²ᵐb²ᵐ | m ≥ 2} (THEORY.md Part II §5).
6. **A failed construction taken as a non-CFL proof** (VERDICT_POLICY R1). If no grammar passes,
   that is not evidence for `non_cfl`.
7. **A sampled grammar beating a correct proof** (VERDICT_POLICY R3, the wwvvR precedent). `bounded_pass`
   against `well_formed` does not settle a conflict: cross-check it (SKILL.md Step 6).
8. **Interchange lemma without its bound.** "Among n words of one length, two are interchangeable" drops
   the bound k ≥ |R| / (c(n+1)²) and proves nothing (THEORY.md §2.4). Do not use Sokolowski's lemma (removed in §2.4).
9. **Parikh as proof.** A semilinear image is necessary, not sufficient. A finite sample is always
   semilinear. `parikh` is `well_formed` only.
10. **Missing or approximate oracle.** A `natural` description that is not exponent notation, a copy
    language written as text, or a `natural_language_filter` gives no exact oracle. Then nothing semantic
    is checked. Re-express the IR first (SKILL.md Step 1).
11. **Tool-level pitfalls.** Grammar terminals must be single characters (`cyk` returns `unsupported`
    otherwise). A PDA's `push` order is `top_first` by default; misreading it changes the answers.
    Undeclared or overlapping grammar symbols make the artifact `refuted` (`invalid_artifact`).
12. **Budget burn on one side.** Two live Haiku runs of eval cfl-12 show the cost (EVAL_RESULTS). After C2,
    the constructive builder (`cfg_builder`) used 6 calls ($0.80 of the task's $1.07) on grammars for a wrong
    `cfl` hypothesis, and the run ended `inconclusive 0.40`. After C4, the run reached the correct
    `non_cfl 0.60`, but `cfg_builder` was cut twice at `max_tokens=64000` first. Time-box each branch and
    check `budget_status`.
13. **A heuristic taken as a verdict.** The tz_cfl §4.2 hints and any classifier output are advisory.
    The verdict needs evidence from SKILL.md §6.
