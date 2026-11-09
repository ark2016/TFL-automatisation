# LL(k) in Lean: formulation and plan

Status (2026-11-09): **design + a compiled definition**. `ll_system` has no Lean path yet
(`TFL_FORMALIZATION` skips it, see the root `CLAUDE.md`); nothing in the image (`tfl-lean4`:
Lean/Mathlib v4.33.0, langlib @ c5fb834) defines LL(k). This note fixes the statement the
pipeline would prove, checks that it compiles against the image, and lists what is missing for
each kind of `ll_system` verdict. The theory it formalizes is `docs/THEORY.md` §3 (change that
first).

## 1. What exists to build on

- Mathlib `ContextFreeGrammar T` (`NT : Type`, `initial`, `rules : Finset (ContextFreeRule T NT)`),
  `Produces` / `Derives` (`Relation.ReflTransGen`), `language`. Derivations are unordered: there is
  no leftmost derivation and no parse tree.
- langlib `Grammars/LR/Definition.lean`: a *semantic* LR(k) predicate in Knuth's form over langlib's
  own `CF_grammar` (`RewritesRightmost`, `DerivesRightmost`, `lrLookahead k w = w.take k`,
  `CoreIsLRk`, `IsLRk`, `is_LRk`), and `Grammars/LR/Equivalence.lean`: `is_LRk_iff_is_DCF`
  (`0 < k`, `[Fintype T]`), `is_DPDA_of_is_LRk`. Neither module is imported by `TflLean` today.
- langlib `Grammars/ContextFree/MathlibCFG.lean`: the bridge `is_CF_iff_isContextFree` between
  `CF_grammar` and Mathlib's grammars.

The LL(k) definition below mirrors langlib's LR(k) one (same `take k` lookahead, semantic condition
on derivations rather than on a parse table), so that an LL ⇒ LR theorem can later be stated
between the two.

## 2. Definition (compiled)

THEORY.md §3.1: `G` is LL(k) when for any two leftmost derivations
`S ⇒*lm wAα ⇒ wβα ⇒* wx` and `S ⇒*lm wAα ⇒ wγα ⇒* wy` with `FIRST_k(x) = FIRST_k(y)` one has
`β = γ`. In Lean, over Mathlib's grammar (the type the CFL/DCFL statement renderers already emit
for grammar IRs):

```lean
import TflLean

namespace TflLL

variable {T : Type}

/-- One leftmost step: the rewritten nonterminal has only terminals to its left. -/
def ProducesLeftmost (g : ContextFreeGrammar T) (u v : List (Symbol T g.NT)) : Prop :=
  ∃ r ∈ g.rules, ∃ (w : List T) (s : List (Symbol T g.NT)),
    u = w.map Symbol.terminal ++ [Symbol.nonterminal r.input] ++ s ∧
    v = w.map Symbol.terminal ++ r.output ++ s

/-- Leftmost derivation. -/
abbrev DerivesLeftmost (g : ContextFreeGrammar T) :
    List (Symbol T g.NT) → List (Symbol T g.NT) → Prop :=
  Relation.ReflTransGen (ProducesLeftmost g)

/-- `g` is LL(k) (docs/THEORY.md §3.1, [Ni, Def. 8.7]): in a left-sentential form
`w A α` reachable by a leftmost derivation, two rules `A → β`, `A → γ` whose
continuations derive terminal words `x`, `y` with the same first `k` letters
are the same rule. -/
def IsLLk (g : ContextFreeGrammar T) (k : ℕ) : Prop :=
  ∀ (w x y : List T) (A : g.NT) (α β γ : List (Symbol T g.NT)),
    DerivesLeftmost g [Symbol.nonterminal g.initial]
      (w.map Symbol.terminal ++ [Symbol.nonterminal A] ++ α) →
    ⟨A, β⟩ ∈ g.rules → ⟨A, γ⟩ ∈ g.rules →
    g.Derives (β ++ α) (x.map Symbol.terminal) →
    g.Derives (γ ++ α) (y.map Symbol.terminal) →
    x.take k = y.take k → β = γ

/-- A language is LL(k) when some LL(k) grammar generates it. -/
def IsLLkLanguage (k : ℕ) (L : Language T) : Prop :=
  ∃ g : ContextFreeGrammar T, IsLLk g k ∧ g.language = L

/-- LL(k) for some `k`. -/
def IsLLLanguage (L : Language T) : Prop :=
  ∃ k, IsLLkLanguage k L

/-- More lookahead never hurts. -/
theorem IsLLk.mono {g : ContextFreeGrammar T} {k l : ℕ} (hkl : k ≤ l) (h : IsLLk g k) :
    IsLLk g l := by
  intro w x y A α β γ hd hβ hγ hx hy hxy
  refine h w x y A α β γ hd hβ hγ hx hy ?_
  have := congrArg (List.take k) hxy
  simpa [List.take_take, Nat.min_eq_left hkl] using this

end TflLL

/- Example: S → aS | a is not LL(1) (both rules see the lookahead `a` at the start). -/

inductive Letter
  | a | b
  deriving DecidableEq, Repr

inductive NT
  | S
  deriving DecidableEq, Repr

def g : ContextFreeGrammar Letter :=
  { NT := NT, initial := NT.S,
    rules := {⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S]⟩,
              ⟨NT.S, [Symbol.terminal Letter.a]⟩} }

theorem tfl_main : ¬ TflLL.IsLLk g 1 := by
  intro h
  have hβ : (⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S]⟩ :
      ContextFreeRule Letter NT) ∈ g.rules :=
    Finset.mem_insert_self _ _
  have hγ : (⟨NT.S, [Symbol.terminal Letter.a]⟩ : ContextFreeRule Letter NT) ∈ g.rules :=
    Finset.mem_insert_of_mem (Finset.mem_singleton_self _)
  -- the continuation of S → aS derives `aa` (one step S → a); that of S → a is `a`
  have hx : g.Derives ([Symbol.terminal Letter.a, Symbol.nonterminal NT.S] ++ [])
      ([Letter.a, Letter.a].map Symbol.terminal) :=
    Relation.ReflTransGen.single ⟨_, hγ,
      ContextFreeRule.rewrites_of_exists_parts _ [Symbol.terminal Letter.a] []⟩
  have hy : g.Derives ([Symbol.terminal Letter.a] ++ []) ([Letter.a].map Symbol.terminal) :=
    Relation.ReflTransGen.refl
  -- the left-sentential form is the start symbol itself (w = ε, α = ε)
  have hd : TflLL.DerivesLeftmost g [Symbol.nonterminal g.initial]
      (([] : List Letter).map Symbol.terminal ++ [Symbol.nonterminal NT.S] ++ []) :=
    Relation.ReflTransGen.refl
  -- both words start with `a`, yet the rules differ
  have := h [] [Letter.a, Letter.a] [Letter.a] NT.S [] _ _ hd hβ hγ hx hy rfl
  exact List.cons_ne_nil _ _ (List.cons.inj this).2

#print axioms tfl_main
```

`agent_system/tests/test_lean_llk_doc.py` compiles this block in the image (Docker-conditional)
and requires `tfl_main` to depend on the standard axioms only.

Choices and why:

- **The tails use ordinary `Derives`.** A sentential form derives a terminal word iff it does so
  leftmost (reordering independent steps), so `⇒*` and `⇒*lm` give the same `x`, `y`; the plain
  relation is the one Mathlib has lemmas for. Only the prefix `S ⇒*lm wAα` must be leftmost: it is
  what makes `w` the input already read.
- **`x.take k = y.take k` is `FIRST_k(x) = FIRST_k(y)`** for terminal words (prefix of length
  `min k |x|`), the same convention as langlib's `lrLookahead`. Words shorter than `k` are compared
  whole, which is the end-marker convention of THEORY.md §3.1 (`$`-padding is equivalent).
- **Rules are compared by right-hand side.** Both rules have left side `A`, and
  `ContextFreeRule` is a structure, so `β = γ` is equality of the rules.
- **`k` is a parameter, not restricted to `k ≥ 1`** (THEORY.md uses `k ≥ 1`; `IsLLk g 0` is also
  meaningful, "at most one useful rule per reachable context", and costs nothing).
- **Useless symbols are not removed.** The definition quantifies over reachable contexts and
  productive continuations only, so unreachable or unproductive rules never cause a conflict,
  as in the textbook definition.

## 3. Statement shapes for `ll_system`

| verdict | statement | IR |
|---|---|---|
| grammar is LL(k) | `theorem tfl_main : TflLL.IsLLk g k` | grammar (`def g : ContextFreeGrammar Letter`) |
| grammar is not LL(k) for this `k` | `theorem tfl_main : ¬ TflLL.IsLLk g k` | grammar |
| grammar is not LL(k) for any `k` | `theorem tfl_main : ∀ k, ¬ TflLL.IsLLk g k` | grammar |
| language is LL | `theorem tfl_main : TflLL.IsLLLanguage L` | set-builder or grammar |
| language is not LL | `theorem tfl_main : ¬ TflLL.IsLLLanguage L` | set-builder or grammar |

`g` would be rendered exactly as `cfl_system.lib.lean_ir` renders grammar IRs today (inductive
`NT`, rule `Finset` literal). The definitions go into a new `TflLean/LL.lean` re-exported by
`import TflLean` when the first statement shape is wired; until then they live only in this note.

## 4. Proof routes and what each needs

**(a) `¬ IsLLk g k`: available now.** A conflict is a finite witness: one leftmost derivation of
`wAα`, two rules `A → β ≠ γ`, two terminal derivations with equal `take k`. The proof is
mechanical (the example above): rule membership by `Finset.mem_insert_self` /
`Finset.mem_insert_of_mem`, steps by `ContextFreeRule.rewrites_of_exists_parts`, leftmost steps
by unfolding `ProducesLeftmost` with explicit `w`, `s`. `ll_system`'s full test already produces
these witnesses (`ll_table_builder.check_ll_k` → `ll_conflicts`), so a renderer of the conflict
into such a proof body is the shortest path to a first `proved` LL verdict. Caveat: a
`strong`-test conflict (`conflicts`, global FOLLOW_k) is **not** an LL(k) conflict for `k ≥ 2`
(THEORY.md §3.1 example `S → aAaa | bAba`); only `ll_conflicts` from the full test, or the strong
conflicts at `k = 1`, may be rendered.

**(b) `IsLLk g k`: needs one soundness theorem.** The definition quantifies over infinitely many
derivations, so a positive proof needs a finite certificate. Plan:
1. Define semantic `FIRST_k(α) := {x.take k | α ⇒* x}` and the local follow sets `σ(A)` of
   THEORY.md §3.1 as `Set (List T)`.
2. Prove *invariant lemmas*: if a family `F : NT → Finset (List T)` contains `take k` of every
   word derivable from each nonterminal and is closed under the rules (a post-fixed point), it
   over-approximates `FIRST_k`; the same for a family `Σ : NT → Finset (Finset (List T))`
   over-approximating the local follow sets.
3. Soundness: if those over-approximations make the director sets of every pair of rules
   disjoint in every context, then `IsLLk g k`. (SLL(k) is the special case of one global
   follow set per nonterminal, and suffices for every LL(1) grammar.)
4. The certificate (`F`, `Σ`, taken from `ll_system`'s computed sets) is then checked by
   `decide` on `Finset`s of short words: closure, then disjointness. No `native_decide`.

Effort: steps 1–3 are a few hundred lines (induction on derivation length, splitting `β ++ α`
derivations into derivations of the parts, which Mathlib does not provide yet). Kernel `decide`
on tables of a few dozen words is fine; large `k` or alphabets would need a smarter encoding.

**(c) `∀ k, ¬ IsLLk g k`.** THEORY.md §3.1 certificates: left recursion after removing useless
symbols (`A ⇒⁺ Aα`), or ambiguity. Each is a generic theorem (left recursion + productive +
reachable ⇒ for every `k` a conflict, by pumping the left-recursive loop `k` times), then a
finite witness per grammar. Moderate (one generic proof, about the size of (b)).

**(d) Language level.**
- `IsLLLanguage L`: exhibit `g` with `IsLLk g k` (route b) and `g.language = L` (the same
  difficulty as a set-builder CFL proof in `cfl_lean_formalizer.md`).
- `¬ IsLLLanguage L` via **not DCF** (THEORY.md §3.3 (A)): needs `IsLLk g k → ` LR(k) (as
  langlib's `IsLRk` on the translated `CF_grammar`), then langlib's `is_DPDA_of_is_LRk`.
  LL(k) ⇒ LR(k) is classical but not short in Knuth's semantic form (handle uniqueness must be
  derived from leftmost determinism over all right-sentential forms); it also needs `0 < k` and
  `[Fintype T]` (the DCFL renderer already emits a `Fintype` instance). Then every `¬ is_DCF`
  route of the DCFL formalizer (complement, ∩ REG, slice lemma) refutes LL as well.
- `¬ IsLLLanguage L` via the **fork argument** (THEORY.md §3.3 (C), `{aⁿbⁿ} ∪ {aⁿcⁿ}`): needs
  parse trees with subtree substitution ("any mix of the subderivations of `δ = X₁…X_m` is a
  derivation"). Mathlib has no parse trees; this is the largest item (a derivation-tree library
  for `ContextFreeGrammar`, then the argument itself).

## 5. Order of work

1. Move §2 into `TflLean/LL.lean`, re-export it, rebuild the image; add an `ll_system` statement
   renderer for grammar IRs (`IsLLk g k` / `¬ IsLLk g k`) and a reference example per direction
   under `TflLean/Examples/`, registered like the CFL/DCFL ones in
   `agent_system/tests/test_lean_examples.py`.
2. Route (a): render `ll_conflicts` witnesses into proof bodies (deterministic, no LLM needed).
3. Route (b): FIRST_k / σ over-approximation lemmas and the soundness theorem; certificate
   rendering from `check_ll_k`'s sets.
4. Route (c): left-recursion certificate.
5. Route (d): LL(k) ⇒ LR(k) bridge to langlib (unlocks all `¬ is_DCF` proofs for LL), then parse
   trees for the fork argument.

Steps 1–2 are days of work; 3–4 a week or more; 5 is a project of its own.
