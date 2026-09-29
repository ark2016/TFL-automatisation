import Mathlib.Computability.DFA
import Mathlib.Computability.RegularExpressions
import Mathlib.Computability.ContextFreeGrammar
import Mathlib.Algebra.BigOperators.Group.Finset.Piecewise
import Langlib.Classes.ContextFree.Pumping.Pumping
-- Transfer section (below): langlib's bridges and worked class-membership
-- results, moved from langlib's `Fin 3` alphabet onto any alphabet.
import Langlib.Grammars.ContextFree.MathlibCFG
import Langlib.Classes.ContextFree.Closure.InverseHomomorphism
import Langlib.Classes.ContextFree.Closure.IntersectionRegular
import Langlib.Classes.ContextFree.Examples.AnBnCnPos
import Langlib.Classes.DeterministicContextFree.Examples.AbcPositive
import Langlib.Classes.DeterministicContextFree.Examples.AnBnCm
import Langlib.Classes.DeterministicContextFree.Closure.Bijection
import Langlib.Classes.DeterministicContextFree.Closure.Complement
import Langlib.Classes.DeterministicContextFree.Closure.IntersectionRegular
import Langlib.Classes.DeterministicContextFree.Inclusion.ContextFree

/-!
# TflLean.Lemmas — reusable lemmas for R-Lean proof bodies

Helpers that the worked examples in `TflLean/Examples/` needed, stated
generically (any alphabet `α`, not the per-task `Letter`). Built into the
`tfl-lean4` image (Lean/Mathlib v4.33.0 + langlib @ c5fb834) and re-exported
by `import TflLean`, so a proof body checked by the harness can use every name
below as `TflLean.<name>`.

Everything lives in the `TflLean` namespace.

## Contents

* Counting: `count_replicate_self`, `count_replicate_of_ne`,
  `count_flatten_replicate`, `length_eq_sum_count`,
  `replicate_append_replicate_inj`.
* Regular: `evalFrom_cons`, `isRegular_of_dfa`,
  `not_isRegular_of_distinguishable` (easy half of Myhill–Nerode),
  `IsRegular.pumping` (textbook pumping lemma for `Language.IsRegular`).
* Context-free: `CFPumping` (the conclusion of langlib's
  `Language.IsContextFree.pumping`, with `v ^+^ i` unfolded),
  `IsContextFree.cfPumping` (Mathlib's `L.IsContextFree → CFPumping L`, via
  langlib), `not_isContextFree_of_not_cfPumping`, `flatten_replicate_zero`,
  `flatten_replicate_two`.
* Transfer from langlib (CFL/DCFL statements; see `Examples/AnBnAnNotCF.lean`,
  `Examples/AnBnCm_DCF.lean`, `Examples/AnBnCmPos_DCF.lean`,
  `Examples/AiBjCkNeq_NotDCF.lean`):
  `fin3Map` / `fin3Map_injective` (langlib's `Fin 3` alphabet `0,1,2` ↦ any
  three letters), `mem_map_iff`, `replicate_append_inj`,
  `replicate_append_replicate_append_replicate_inj`,
  `isContextFree_of_isDCF` (DCFL ⊆ CFL, langlib's `is_CF_of_is_DCF`),
  `not_isDCF_of_not_isContextFree`, `not_isContextFree_of_slice` (if `L`
  restricted to the slice `x⁺ y⁺ z⁺` is exactly `{xⁿ yⁿ zⁿ | n ≥ 1}`, then `L`
  is not context-free -- inverse homomorphism + intersection with a regular
  language + langlib's `notCF_lang_eq_eq_pos`), `isDCF_anbncm` /
  `isDCF_anbncm_pos` (langlib's DPDA for `{aⁿ bⁿ cᵐ}` and its `n, m ≥ 1`
  slice, over any three distinct letters). langlib names that are useful
  directly: `is_CF_iff_isContextFree`, `is_CF_of_is_DCF`,
  `DCF_closedUnderComplement L h : is_DCF Lᶜ`,
  `DCF_inter_regular L R hL hR : is_DCF (L ⊓ R)`.

With these, the proof body of `Examples/AnBnNotRegular.lean` shrinks to
(Myhill–Nerode)

```
apply TflLean.not_isRegular_of_distinguishable (fun i => List.replicate i Letter.a)
intro i j hij
refine ⟨List.replicate i Letter.b, ⟨i, rfl, Nat.zero_le _⟩, ?_⟩
rintro ⟨n, hn, -⟩
have := TflLean.replicate_append_replicate_inj (by decide) hn
omega
```

or, via the textbook pumping lemma (pump `aᵖbᵖ` down: `y` lies inside the
`a`-block, so `x z` has fewer `a`'s than `b`'s)

```
intro h
obtain ⟨p, hp⟩ := TflLean.IsRegular.pumping h
obtain ⟨x, y, z, hw, hxy, hy, hpump⟩ :=
  hp (List.replicate p Letter.a ++ List.replicate p Letter.b) ⟨p, rfl, Nat.zero_le _⟩ (by simp)
obtain ⟨n, hn, -⟩ := hpump 0
have hy' : 0 < y.length := List.length_pos_of_ne_nil hy
simp only [List.length_append] at hxy
have hta : List.take (x ++ y).length (List.replicate p Letter.a ++ List.replicate p Letter.b) = x ++ y := by
  rw [hw, List.append_assoc x y z, ← List.append_assoc, List.take_left]
have hya : y.count Letter.a = y.length := by
  have h1 : (x ++ y).count Letter.a = (x ++ y).length := by
    rw [← hta]; simp [List.take_append, List.take_replicate, List.count_replicate]; omega
  simp only [List.count_append, List.length_append] at h1
  have := List.count_le_length (a := Letter.a) (l := x)
  have := List.count_le_length (a := Letter.a) (l := y); omega
have ca := congrArg (List.count Letter.a) hw
have cb := congrArg (List.count Letter.b) hw
have ca0 := congrArg (List.count Letter.a) hn
have cb0 := congrArg (List.count Letter.b) hn
simp [List.count_replicate] at ca cb ca0 cb0
have hlen : ∀ l : List Letter, l.length = l.count Letter.a + l.count Letter.b := by
  intro l
  induction l with
  | nil => rfl
  | cons s l ih => cases s <;> simp [ih] <;> omega
have := hlen y
omega
```

and the proof body of `Examples/AnBnCnNotCF.lean` to
`apply TflLean.not_isContextFree_of_not_cfPumping` followed by the (langlib-free)
body of `Examples/AnBnCnPumpingCore.lean`.

(`agent_system/tests/test_lean_examples.py` compiles both blocks against the
`Examples/AnBnNotRegular.lean` statement, and the CF variant against the
`Examples/AnBnCnNotCF.lean` one, so they stay in sync with the lemmas.)
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
(for `Letter = {a, b, c}`: `|w| = #a + #b + #c`). -/
theorem length_eq_sum_count [Fintype α] (l : List α) : l.length = ∑ s, l.count s := by
  induction l with
  | nil => simp
  | cons x l ih =>
    simp only [List.length_cons, List.count_cons, Finset.sum_add_distrib, ih]
    simp [Finset.sum_ite_eq]

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
`(List.replicate i v).flatten`). Refuting it is the combinatorial half of a
`¬ L.IsContextFree` proof (see `not_isContextFree_of_not_cfPumping` and
`Examples/AnBnCnPumpingCore.lean`). -/
def CFPumping (L : Language α) : Prop :=
  ∃ p : ℕ, ∀ w ∈ L, w.length ≥ p → ∃ u v x y z : List α,
    w = u ++ v ++ x ++ y ++ z ∧ (v ++ y).length > 0 ∧ (v ++ x ++ y).length ≤ p ∧
    ∀ i : ℕ, u ++ (List.replicate i v).flatten ++ x ++ (List.replicate i y).flatten ++ z ∈ L

/-- Mathlib's `Language.IsContextFree` satisfies the pumping property
(langlib's `Language.IsContextFree.pumping`; `v ^+^ i` is by definition
`(List.replicate i v).flatten`). -/
theorem IsContextFree.cfPumping {T : Type} {L : Language T} (h : L.IsContextFree) :
    CFPumping L :=
  h.pumping

/-- To refute `L.IsContextFree`, refute the pumping property. -/
theorem not_isContextFree_of_not_cfPumping {T : Type} {L : Language T} (h : ¬ CFPumping L) :
    ¬ L.IsContextFree :=
  fun hL => h (IsContextFree.cfPumping hL)

/-- Pumping down: `v⁰ = ε`. -/
theorem flatten_replicate_zero (v : List α) : (List.replicate 0 v).flatten = [] := rfl

/-- Pumping up once: `v² = v v`. -/
theorem flatten_replicate_two (v : List α) : (List.replicate 2 v).flatten = v ++ v := by
  simp [List.replicate_succ]

end ContextFree

section Transfer

/-! Moving langlib's worked results (stated over `Fin 3` with `a_ = 0`,
`b_ = 1`, `c_ = 2`) onto a task alphabet. The task alphabet `Letter` of a
CFL statement has no `Fintype` instance, so nothing here asks for one on `α`
except where langlib's `is_DCF` itself does. -/

variable {β : Type}

/-- The letter map `Fin 3 → β`, `0 ↦ x`, `1 ↦ y`, `2 ↦ z`. -/
def fin3Map (x y z : β) : Fin 3 → β := ![x, y, z]

theorem fin3Map_injective {x y z : β} (hxy : x ≠ y) (hxz : x ≠ z) (hyz : y ≠ z) :
    Function.Injective (fin3Map x y z) := by
  intro i j h
  fin_cases i <;> fin_cases j <;> simp_all [fin3Map, eq_comm]

/-- Membership in Mathlib's `Language.map` (the image under a letter map). -/
theorem mem_map_iff {γ : Type} (f : γ → β) (L : Language γ) (w : List β) :
    w ∈ Language.map f L ↔ ∃ v ∈ L, v.map f = w :=
  Iff.rfl

/-- `aⁱ u = aʲ v` where neither `u` nor `v` starts with `a` forces `i = j`, `u = v`. -/
theorem replicate_append_inj {a : β} {i j : ℕ} {u v : List β}
    (hu : u.head? ≠ some a) (hv : v.head? ≠ some a)
    (h : List.replicate i a ++ u = List.replicate j a ++ v) : i = j ∧ u = v := by
  induction i generalizing j with
  | zero =>
    cases j with
    | zero => simpa using h
    | succ j => simp [List.replicate_succ] at h; subst h; simp at hu
  | succ i ih =>
    cases j with
    | zero => simp [List.replicate_succ] at h; subst h; simp at hv
    | succ j =>
      simp [List.replicate_succ] at h
      obtain ⟨h1, h2⟩ := ih h
      exact ⟨by omega, h2⟩

/-- `aⁱ bʲ cᵏ = aⁱ' bʲ' cᵏ'` (pairwise distinct letters) forces equal exponents. -/
theorem replicate_append_replicate_append_replicate_inj [DecidableEq β] {a b c : β}
    (hab : a ≠ b) (hac : a ≠ c) (hbc : b ≠ c) {i j k i' j' k' : ℕ}
    (h : List.replicate i a ++ List.replicate j b ++ List.replicate k c =
      List.replicate i' a ++ List.replicate j' b ++ List.replicate k' c) :
    i = i' ∧ j = j' ∧ k = k' := by
  have ha := congrArg (List.count a) h
  have hb := congrArg (List.count b) h
  have hc := congrArg (List.count c) h
  simp [List.count_replicate, hab, hac, hbc, Ne.symm hab, Ne.symm hac, Ne.symm hbc] at ha hb hc
  exact ⟨ha, hb, hc⟩

/-- DCFL ⊆ CFL (langlib's `is_CF_of_is_DCF`), landing in Mathlib's predicate. -/
theorem isContextFree_of_isDCF {T : Type} [Fintype T] {L : Language T} (h : is_DCF L) :
    L.IsContextFree :=
  is_CF_iff_isContextFree.mp (is_CF_of_is_DCF h)

/-- A language that is not context-free is not deterministic context-free. -/
theorem not_isDCF_of_not_isContextFree {T : Type} [Fintype T] {L : Language T}
    (h : ¬ L.IsContextFree) : ¬ is_DCF L :=
  fun hd => h (isContextFree_of_isDCF hd)

/-- If on the slice `x⁺ y⁺ z⁺` the language `L` is exactly `{xⁿ yⁿ zⁿ | n ≥ 1}`,
then `L` is not context-free. (`x`, `y`, `z` need not be distinct: `x = z = a`,
`y = b` covers `{aⁿ bⁿ aⁿ}`.) Proof: the preimage of `L` under `0 ↦ x, 1 ↦ y,
2 ↦ z` (inverse homomorphism) intersected with the regular `abcPositive` is
langlib's `lang_eq_eq_pos`, which `notCF_lang_eq_eq_pos` refutes. -/
theorem not_isContextFree_of_slice {L : Language β} (x y z : β)
    (h : ∀ n m k : ℕ, List.replicate (n + 1) x ++ List.replicate (m + 1) y ++
      List.replicate (k + 1) z ∈ L ↔ n = m ∧ m = k) :
    ¬ L.IsContextFree := by
  intro hL
  apply notCF_lang_eq_eq_pos
  have h1 := CF_closed_under_inverse_homomorphism L (fun i => [fin3Map x y z i])
    (is_CF_iff_isContextFree.mpr hL)
  have h2 := CF_of_CF_inter_regular h1 abcPositive_regular
  have hmap : ∀ w : List (Fin 3),
      extendHom (fun i => [fin3Map x y z i]) w = w.map (fin3Map x y z) := by
    intro w; induction w with
    | nil => rfl
    | cons c w ih => simp [extendHom] at ih ⊢; exact ih
  convert h2 using 1
  ext w
  constructor
  · rintro ⟨n, rfl⟩
    refine ⟨?_, n, n, n, rfl⟩
    show extendHom _ _ ∈ L
    rw [hmap]
    simpa [fin3Map, a_, b_, c_] using (h n n n).2 ⟨rfl, rfl⟩
  · rintro ⟨hw, n, m, k, rfl⟩
    change extendHom _ _ ∈ L at hw
    rw [hmap] at hw
    obtain ⟨rfl, rfl⟩ :=
      (h n m k).1 (by simpa [fin3Map, a_, b_, c_, List.append_assoc] using hw)
    exact ⟨n, rfl⟩

/-- `{aⁿ bⁿ cᵐ | n, m ≥ 0}` is deterministic context-free (langlib's DPDA,
`DCFLIntersection.DCF_lang_eq_any`, moved along `fin3Map a b c`). -/
theorem isDCF_anbncm [Fintype β] {a b c : β} (hab : a ≠ b) (hac : a ≠ c) (hbc : b ≠ c) :
    is_DCF ({w | ∃ n m : ℕ, w = List.replicate n a ++ List.replicate n b ++
      List.replicate m c} : Language β) := by
  have e : ({w | ∃ n m : ℕ, w = List.replicate n a ++ List.replicate n b ++
      List.replicate m c} : Language β) = Language.map (fin3Map a b c) lang_eq_any := by
    ext w
    constructor
    · rintro ⟨n, m, rfl⟩
      exact (mem_map_iff _ _ _).2 ⟨_, ⟨n, m, rfl⟩, by simp [fin3Map, a_, b_, c_]⟩
    · intro hw
      obtain ⟨v, ⟨n, m, rfl⟩, rfl⟩ := (mem_map_iff _ _ _).1 hw
      exact ⟨n, m, by simp [fin3Map, a_, b_, c_]⟩
  rw [e]
  exact DCF_of_map_injective_DCF (fin3Map_injective hab hac hbc) _
    DCFLIntersection.DCF_lang_eq_any

/-- `{aⁿ⁺¹ bⁿ⁺¹ cᵐ⁺¹ | n, m ≥ 0}` is deterministic context-free (langlib's
`DCF_lang_eq_any_pos`, moved along `fin3Map a b c`). -/
theorem isDCF_anbncm_pos [Fintype β] {a b c : β} (hab : a ≠ b) (hac : a ≠ c) (hbc : b ≠ c) :
    is_DCF ({w | ∃ n m : ℕ, w = List.replicate (n + 1) a ++ List.replicate (n + 1) b ++
      List.replicate (m + 1) c} : Language β) := by
  have e : ({w | ∃ n m : ℕ, w = List.replicate (n + 1) a ++ List.replicate (n + 1) b ++
      List.replicate (m + 1) c} : Language β) =
      Language.map (fin3Map a b c) lang_eq_any_pos := by
    ext w
    constructor
    · rintro ⟨n, m, rfl⟩
      exact (mem_map_iff _ _ _).2
        ⟨_, ⟨⟨n + 1, m + 1, rfl⟩, n, n, m, rfl⟩, by simp [fin3Map, a_, b_, c_]⟩
    · intro hw
      obtain ⟨v, ⟨⟨n, m, rfl⟩, n', m', k', hv⟩, rfl⟩ := (mem_map_iff _ _ _).1 hw
      have ha := congrArg (List.count a_) hv
      have hb := congrArg (List.count b_) hv
      have hc := congrArg (List.count c_) hv
      simp [a_, b_, c_, List.count_replicate] at ha hb hc
      obtain ⟨n, rfl⟩ : ∃ p, n = p + 1 := ⟨n - 1, by omega⟩
      obtain ⟨m, rfl⟩ : ∃ p, m = p + 1 := ⟨m - 1, by omega⟩
      exact ⟨n, m, by simp [fin3Map, a_, b_, c_]⟩
  rw [e]
  exact DCF_of_map_injective_DCF (fin3Map_injective hab hac hbc) _ DCF_lang_eq_any_pos

end Transfer

end TflLean
