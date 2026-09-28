import Mathlib.Computability.DFA
import Mathlib.Computability.RegularExpressions
import Mathlib.Computability.ContextFreeGrammar
import Mathlib.Algebra.BigOperators.Group.Finset.Basic

/-!
# TflLean.Lemmas — reusable lemmas for R-Lean proof bodies

Helpers that the worked examples in `TflLean/Examples/` needed, stated
generically (any alphabet `α`, not the per-task `Sym`). Mathlib only — no
langlib — so this file compiles against the current `tfl-lean4` image
(Lean/Mathlib v4.18.0) and is meant to keep compiling on the v4.33.0 +
langlib pin in `../lakefile.toml`.

Everything lives in the `TflLean` namespace.

NOTE: `TflLean.lean` does not import this module yet, so a proof body checked
by the harness (whose statement only says `import TflLean`) cannot reference
these names until the image is rebuilt with `import TflLean.Lemmas` added
there. Until then the examples inline the same facts as `have`s (see
`Examples/*.lean`).

## Contents

* Counting: `count_replicate_self`, `count_replicate_of_ne`,
  `count_flatten_replicate`, `length_eq_sum_count`,
  `replicate_append_replicate_inj`.
* Regular: `evalFrom_cons`, `isRegular_of_dfa`,
  `not_isRegular_of_distinguishable` (easy half of Myhill–Nerode),
  `IsRegular.pumping` (textbook pumping lemma for `Language.IsRegular`).
* Context-free: `CFPumping` (the conclusion of langlib's
  `Language.IsContextFree.pumping`, Mathlib-only), `flatten_replicate_zero`,
  `flatten_replicate_two`.

With `TflLean.Lemmas` available, the proof body of
`Examples/AnBnNotRegular.lean` shrinks to

```
apply TflLean.not_isRegular_of_distinguishable (fun i => List.replicate i Sym.a)
intro i j hij
refine ⟨List.replicate i Sym.b, ⟨i, rfl, Nat.zero_le _⟩, ?_⟩
rintro ⟨n, hn, -⟩
have := TflLean.replicate_append_replicate_inj (by decide) hn
omega
```
-/

namespace TflLean

variable {α : Type*}

section Counting

variable [DecidableEq α]

/-- `#a(aⁿ) = n`. -/
theorem count_replicate_self (a : α) (n : ℕ) : (List.replicate n a).count a = n := by
  simp

/-- `#a(bⁿ) = 0` for `a ≠ b`. -/
theorem count_replicate_of_ne {a b : α} (h : a ≠ b) (n : ℕ) :
    (List.replicate n b).count a = 0 := by
  simp [List.count_replicate, Ne.symm h]

/-- Letter count of a pumped block `vⁱ` (written `(List.replicate i v).flatten`,
which is what langlib's `v ^+^ i` unfolds to). -/
theorem count_flatten_replicate (a : α) (v : List α) (i : ℕ) :
    ((List.replicate i v).flatten).count a = i * v.count a := by
  induction i with
  | zero => simp
  | succ i ih => simp [List.replicate_succ, List.count_append, ih, Nat.succ_mul, Nat.add_comm]

/-- Over a finite alphabet the length of a word is the sum of its letter counts
(for `Sym = {a, b, c}`: `|w| = #a + #b + #c`). -/
theorem length_eq_sum_count [Fintype α] (l : List α) : l.length = ∑ s, l.count s := by
  induction l with
  | nil => simp
  | cons x l ih =>
    simp only [List.length_cons, List.count_cons, Finset.sum_add_distrib, ih]
    simp

/-- `aⁱ bʲ = aᵏ bˡ` (with `a ≠ b`) forces `i = k` and `j = l`. -/
theorem replicate_append_replicate_inj {a b : α} (hab : a ≠ b) {i j k l : ℕ}
    (h : List.replicate i a ++ List.replicate j b = List.replicate k a ++ List.replicate l b) :
    i = k ∧ j = l := by
  have ha := congrArg (List.count a) h
  have hb := congrArg (List.count b) h
  simp [List.count_replicate, hab, Ne.symm hab] at ha hb
  exact ⟨ha, hb⟩

end Counting

section Regular

variable {σ : Type*}

/-- One step of `DFA.evalFrom` (holds by `rfl`). -/
theorem evalFrom_cons (M : DFA α σ) (s : σ) (a : α) (w : List α) :
    M.evalFrom s (a :: w) = M.evalFrom (M.step s a) w := rfl

/-- Regularity from an explicit finite DFA and a membership characterisation. -/
theorem isRegular_of_dfa {σ : Type} [Fintype σ] (M : DFA α σ) {L : Language α}
    (h : ∀ w, w ∈ L ↔ M.eval w ∈ M.accept) : L.IsRegular :=
  ⟨σ, inferInstance, M, by ext w; rw [DFA.mem_accepts, h]⟩

/-- Easy direction of Myhill–Nerode: an infinite family of pairwise
distinguishable prefixes rules out every finite DFA. -/
theorem not_isRegular_of_distinguishable {L : Language α} (f : ℕ → List α)
    (h : ∀ i j, i ≠ j → ∃ z, f i ++ z ∈ L ∧ f j ++ z ∉ L) : ¬ L.IsRegular := by
  rintro ⟨σ, _, M, rfl⟩
  obtain ⟨i, j, hij, heq⟩ := Fintype.exists_ne_map_eq_of_card_lt
    (fun k : Fin (Fintype.card σ + 1) => M.eval (f k)) (by simp)
  obtain ⟨z, hi, hj⟩ := h i j (fun e => hij (Fin.ext e))
  apply hj
  rw [DFA.mem_accepts] at hi ⊢
  simp only [DFA.eval, DFA.evalFrom_of_append] at hi heq ⊢
  rwa [← heq]

/-- Pumping lemma for regular languages, stated for `Language.IsRegular`
(`yⁱ` written as `(List.replicate i y).flatten`). -/
theorem IsRegular.pumping {L : Language α} (hL : L.IsRegular) :
    ∃ p : ℕ, ∀ w ∈ L, p ≤ w.length → ∃ x y z : List α,
      w = x ++ y ++ z ∧ (x ++ y).length ≤ p ∧ y ≠ [] ∧
      ∀ i : ℕ, x ++ (List.replicate i y).flatten ++ z ∈ L := by
  obtain ⟨σ, _, M, rfl⟩ := hL
  refine ⟨Fintype.card σ, fun w hw hlen => ?_⟩
  obtain ⟨x, y, z, hw, hxy, hy, hsub⟩ := M.pumping_lemma hw hlen
  refine ⟨x, y, z, hw, by simpa using hxy, hy, fun i => hsub ?_⟩
  refine Language.mem_mul.2 ⟨x ++ (List.replicate i y).flatten, ?_, z, rfl, rfl⟩
  refine Language.mem_mul.2 ⟨x, rfl, (List.replicate i y).flatten, ?_, rfl⟩
  rw [Language.mem_kstar]
  exact ⟨List.replicate i y, rfl, fun b hb => (List.eq_of_mem_replicate hb : b = y) ▸ rfl⟩

end Regular

section ContextFree

/-- The conclusion of the context-free pumping lemma, in the exact shape of
langlib's `Language.IsContextFree.pumping` (`v ^+^ i` unfolds to
`(List.replicate i v).flatten`). Refuting it is the Mathlib-only half of a
`¬ L.IsContextFree` proof; see `Examples/AnBnCnPumpingCore.lean`. -/
def CFPumping (L : Language α) : Prop :=
  ∃ p : ℕ, ∀ w ∈ L, w.length ≥ p → ∃ u v x y z : List α,
    w = u ++ v ++ x ++ y ++ z ∧ (v ++ y).length > 0 ∧ (v ++ x ++ y).length ≤ p ∧
    ∀ i : ℕ, u ++ (List.replicate i v).flatten ++ x ++ (List.replicate i y).flatten ++ z ∈ L

/-- Pumping down: `v⁰ = ε`. -/
theorem flatten_replicate_zero (v : List α) : (List.replicate 0 v).flatten = [] := rfl

/-- Pumping up once: `v² = v v`. -/
theorem flatten_replicate_two (v : List α) : (List.replicate 2 v).flatten = v ++ v := by
  simp [List.replicate_succ]

end ContextFree

end TflLean
