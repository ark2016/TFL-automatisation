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
-- Closure / TransferAnBn sections (below): substitution, homomorphism, letter maps,
-- REG ⊆ DCFL / CFL, and langlib's {aⁿbⁿ} / {a^(2^(k+1))} examples.
import Langlib.Classes.ContextFree.Closure.Substitution
import Langlib.Classes.ContextFree.Closure.Homomorphism
import Langlib.Classes.ContextFree.Closure.Bijection
import Langlib.Classes.ContextFree.Examples.AnBn
import Langlib.Classes.ContextFree.Examples.UnaryA2PowSucc
import Langlib.Classes.DeterministicContextFree.Examples.AnBn
import Langlib.Classes.Regular.Examples.AnBn
import Langlib.Classes.Regular.Inclusion.DeterministicContextFree
import Langlib.Classes.Regular.Inclusion.ContextFree

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
* Closure (langlib, in Mathlib's predicates): `isContextFree_inf_isRegular`,
  `isDCF_inf_isRegular`, the intersection methods `not_isContextFree_of_inf_isRegular`,
  `not_isRegular_of_inf_isRegular`, `not_isDCF_of_inf_isRegular`; REG ⊆ DCFL / CFL
  (`isDCF_of_isRegular`, `isContextFree_of_isRegular`, both `[Fintype T]`); union
  `isContextFree_add`; substitution `isContextFree_subst`; homomorphisms
  `mem_homomorphicImage_iff`, `isContextFree_homomorphicImage`,
  `not_isContextFree_of_homomorphicImage`, `isContextFree_map`,
  `isContextFree_inverseHomomorphicImage`.
* Regular filters (explicit DFAs): `isRegular_length_mod`, `isRegular_count_mod`,
  `isRegular_replicate_append_replicate` (`a* b*`).
* `{aⁿ bⁿ}` and `{a^(2^k)}` over any alphabet (see `Examples/AnBnMod4Filter_CF.lean`,
  `Examples/AnBnUnionAnB2n_CF.lean`, `Examples/AnBn_DCF.lean`): `boolMap` /
  `boolMap_injective` (langlib's `Bool` alphabet, `false ↦ a`, `true ↦ b`), `map_anbn`,
  `isContextFree_anbn`, `isContextFree_anbn_mul` (`{a^(j·n) b^(k·n)}`), `isDCF_anbn`,
  `not_isRegular_anbn`, `not_isContextFree_pow2`.

Not here (no formalization in Mathlib v4.33.0 or langlib @ c5fb834): Parikh's theorem,
Yu's pumping lemma for DCFL, Shallit's Theorem 4.7.4, and LL(k) (for which
`docs/LEAN_LLK.md` designs a statement).

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

section Closure

/-! Closure properties from langlib, stated with Mathlib's `Language.IsContextFree` /
`Language.IsRegular` and langlib's `is_DCF`. The `not_*_of_*` forms are the closure
proof methods: intersect with a regular language or take a homomorphic image, and land
on a language already known to be outside the class. -/

/-- CFL ∩ REG is context-free (langlib's `CF_of_CF_inter_regular`). -/
theorem isContextFree_inf_isRegular {T : Type} {L R : Language T}
    (hL : L.IsContextFree) (hR : R.IsRegular) : (L ⊓ R).IsContextFree :=
  is_CF_iff_isContextFree.mp (CF_of_CF_inter_regular (is_CF_iff_isContextFree.mpr hL) hR)

/-- Intersection method for CFL: if `L ∩ R` is not context-free for a regular `R`, then
`L` is not context-free. -/
theorem not_isContextFree_of_inf_isRegular {T : Type} {L R : Language T}
    (hR : R.IsRegular) (h : ¬ (L ⊓ R).IsContextFree) : ¬ L.IsContextFree :=
  fun hL => h (isContextFree_inf_isRegular hL hR)

/-- Intersection method for REG (Mathlib's `Language.IsRegular.inf`): if `L ∩ R` is not
regular for a regular `R`, then `L` is not regular. -/
theorem not_isRegular_of_inf_isRegular {L R : Language α}
    (hR : R.IsRegular) (h : ¬ (L ⊓ R).IsRegular) : ¬ L.IsRegular :=
  fun hL => h (hL.inf hR)

/-- DCFL ∩ REG is deterministic context-free (langlib's `DCF_inter_regular`). -/
theorem isDCF_inf_isRegular {T : Type} [Fintype T] {L R : Language T}
    (hL : is_DCF L) (hR : R.IsRegular) : is_DCF (L ⊓ R) :=
  DCF_inter_regular L R hL hR

/-- Intersection method for DCFL: if `L ∩ R` is not DCF for a regular `R`, then `L` is
not DCF. -/
theorem not_isDCF_of_inf_isRegular {T : Type} [Fintype T] {L R : Language T}
    (hR : R.IsRegular) (h : ¬ is_DCF (L ⊓ R)) : ¬ is_DCF L :=
  fun hL => h (isDCF_inf_isRegular hL hR)

/-- REG ⊆ DCFL (langlib's `is_DCF_of_is_RG` after `is_RG_of_isRegular`): a regular
language -- e.g. the language of a grammar shown equal to a DFA's -- is DCF. -/
theorem isDCF_of_isRegular {T : Type} [Fintype T] {L : Language T} (h : L.IsRegular) :
    is_DCF L :=
  is_DCF_of_is_RG (is_RG_of_isRegular h)

/-- REG ⊆ CFL (langlib's `is_CF_of_is_RG`). -/
theorem isContextFree_of_isRegular {T : Type} [Fintype T] {L : Language T}
    (h : L.IsRegular) : L.IsContextFree :=
  is_CF_iff_isContextFree.mp (is_CF_of_is_RG (is_RG_of_isRegular h))

/-- CFL ∪ CFL is context-free (`L₁ + L₂` is the union; langlib's
`Language.IsContextFree.add`). -/
theorem isContextFree_add {T : Type} {L₁ L₂ : Language T}
    (h₁ : L₁.IsContextFree) (h₂ : L₂.IsContextFree) : (L₁ + L₂).IsContextFree :=
  Language.IsContextFree.add h₁ h₂

/-- Substituting a context-free language for every letter of a context-free language
gives a context-free language (langlib's `Language.IsContextFree.subst`). -/
theorem isContextFree_subst {α β : Type} {L : Language α} {f : α → Language β}
    (hL : L.IsContextFree) (hf : ∀ a, (f a).IsContextFree) : (L.subst f).IsContextFree :=
  Language.IsContextFree.subst L f hL hf

/-- Membership in the image of `L` under the string homomorphism `h`
(langlib's `Language.homomorphicImage`, the substitution `a ↦ {h a}`). -/
theorem mem_homomorphicImage_iff {α β : Type} (L : Language α) (h : α → List β)
    (u : List β) : u ∈ L.homomorphicImage h ↔ ∃ w ∈ L, w.flatMap h = u := by
  have key : ∀ (w : List α) (u : List β),
      u ∈ (w.map fun x => ({h x} : Language β)).prod ↔ u = w.flatMap h := by
    intro w
    induction w with
    | nil => intro u; exact Iff.rfl
    | cons x xs ih =>
      intro u
      rw [List.map_cons, List.prod_cons, Language.mem_mul]
      constructor
      · rintro ⟨u₁, hu₁, u₂, hu₂, rfl⟩
        rw [Set.mem_singleton_iff.mp hu₁, (ih u₂).1 hu₂]
        simp
      · rintro rfl
        exact ⟨h x, rfl, xs.flatMap h, (ih _).2 rfl, by simp⟩
  constructor
  · rintro ⟨w, hw, hu⟩
    exact ⟨w, hw, ((key w u).1 hu).symm⟩
  · rintro ⟨w, hw, rfl⟩
    exact ⟨w, hw, (key w _).2 rfl⟩

/-- The homomorphic image of a context-free language is context-free (langlib's
`CF_closed_under_homomorphism`). -/
theorem isContextFree_homomorphicImage {α β : Type} {L : Language α} (h : α → List β)
    (hL : L.IsContextFree) : (L.homomorphicImage h).IsContextFree :=
  is_CF_iff_isContextFree.mp (CF_closed_under_homomorphism L h (is_CF_iff_isContextFree.mpr hL))

/-- Homomorphism method for CFL: if the image of `L` under some string homomorphism is
not context-free, then `L` is not context-free. -/
theorem not_isContextFree_of_homomorphicImage {α β : Type} {L : Language α}
    (h : α → List β) (hn : ¬ (L.homomorphicImage h).IsContextFree) : ¬ L.IsContextFree :=
  fun hL => hn (isContextFree_homomorphicImage h hL)

/-- The image of a context-free language under a letter map is context-free (langlib's
`CF_of_map_CF`; no injectivity needed). -/
theorem isContextFree_map {α β : Type} (f : α → β) {L : Language α}
    (hL : L.IsContextFree) : (Language.map f L).IsContextFree :=
  is_CF_iff_isContextFree.mp (CF_of_map_CF f L (is_CF_iff_isContextFree.mpr hL))

/-- The preimage of a context-free language under a string homomorphism is context-free
(langlib's `CF_closed_under_inverse_homomorphism`). -/
theorem isContextFree_inverseHomomorphicImage {α β : Type} [Fintype α] {L : Language β}
    (h : α → List β) (hL : L.IsContextFree) : (L.inverseHomomorphicImage h).IsContextFree :=
  is_CF_iff_isContextFree.mp
    (CF_closed_under_inverse_homomorphism L h (is_CF_iff_isContextFree.mpr hL))

end Closure

section RegularFilters

/-! Regular "filters" for the intersection method: counters modulo `m` as explicit DFAs. -/

/-- `{w | |w| mod m = r}` is regular (a cyclic counter on `Fin m`). -/
theorem isRegular_length_mod (m r : ℕ) (hm : 0 < m) :
    Language.IsRegular ({w | w.length % m = r} : Language α) := by
  let M : DFA α (Fin m) :=
    { step := fun s _ => ⟨(s.val + 1) % m, Nat.mod_lt _ hm⟩
      start := ⟨0, hm⟩
      accept := {s | s.val = r} }
  have hrun : ∀ (s : Fin m) (w : List α), (M.evalFrom s w).val = (s.val + w.length) % m := by
    intro s w
    induction w generalizing s with
    | nil => simp [DFA.evalFrom, Nat.mod_eq_of_lt s.isLt]
    | cons x w ih =>
      rw [evalFrom_cons, ih]
      simp only [M, List.length_cons, Nat.mod_add_mod]
      congr 1
      omega
  refine isRegular_of_dfa M (fun w => ?_)
  show w.length % m = r ↔ (M.evalFrom M.start w).val = r
  rw [hrun]
  simp [M]

/-- `{w | #a(w) mod m = r}` is regular (a cyclic counter of the letter `a`). -/
theorem isRegular_count_mod [DecidableEq α] (a : α) (m r : ℕ) (hm : 0 < m) :
    Language.IsRegular ({w | w.count a % m = r} : Language α) := by
  let M : DFA α (Fin m) :=
    { step := fun s x => if x = a then ⟨(s.val + 1) % m, Nat.mod_lt _ hm⟩ else s
      start := ⟨0, hm⟩
      accept := {s | s.val = r} }
  have hrun : ∀ (s : Fin m) (w : List α), (M.evalFrom s w).val = (s.val + w.count a) % m := by
    intro s w
    induction w generalizing s with
    | nil => simp [DFA.evalFrom, Nat.mod_eq_of_lt s.isLt]
    | cons x w ih =>
      rw [evalFrom_cons, ih]
      by_cases hx : x = a
      · subst hx
        have hs : (M.step s x).val = (s.val + 1) % m := by simp [M]
        rw [hs, List.count_cons_self, Nat.mod_add_mod]
        congr 1
        omega
      · have hs : M.step s x = s := by simp [M, hx]
        have hc : (x :: w).count a = w.count a := by simp [hx]
        rw [hs, hc]
  refine isRegular_of_dfa M (fun w => ?_)
  show w.count a % m = r ↔ (M.evalFrom M.start w).val = r
  rw [hrun]
  simp [M]

/-- `a* b*` (`{aⁱ bʲ | i, j ≥ 0}`, `a ≠ b`) is regular: a three-state DFA (`0` = still in
the `a`-block, `1` = in the `b`-block, `2` = dead). -/
theorem isRegular_replicate_append_replicate [DecidableEq α] {a b : α} (hab : a ≠ b) :
    Language.IsRegular ({w | ∃ i j : ℕ, w = List.replicate i a ++ List.replicate j b} :
      Language α) := by
  let M : DFA α (Fin 3) :=
    { step := fun s x =>
        if s = 0 then (if x = a then 0 else if x = b then 1 else 2)
        else if s = 1 then (if x = b then 1 else 2) else 2
      start := 0
      accept := {s | s ≠ 2} }
  have run2 : ∀ w, M.evalFrom 2 w = 2 := by
    intro w
    induction w with
    | nil => rfl
    | cons x w ih => rw [evalFrom_cons]; simpa [M] using ih
  have run1 : ∀ w, M.evalFrom 1 w ≠ 2 ↔ ∃ j, w = List.replicate j b := by
    intro w
    induction w with
    | nil => exact ⟨fun _ => ⟨0, rfl⟩, fun _ => (by decide : (1 : Fin 3) ≠ 2)⟩
    | cons x w ih =>
      rw [evalFrom_cons]
      by_cases hx : x = b
      · subst hx
        have hs : M.step 1 x = 1 := by simp [M]
        rw [hs, ih]
        constructor
        · rintro ⟨j, rfl⟩; exact ⟨j + 1, rfl⟩
        · rintro ⟨j, hj⟩
          rcases j with _ | j
          · simp at hj
          · exact ⟨j, (List.cons_inj_right _).1 hj⟩
      · have hs : M.step 1 x = 2 := by simp [M, hx]
        rw [hs, run2]
        simp only [ne_eq, not_true_eq_false, false_iff, not_exists]
        intro j hj
        rcases j with _ | j
        · simp at hj
        · exact hx (List.cons_eq_cons.1 hj).1
  have run0 : ∀ w, M.evalFrom 0 w ≠ 2 ↔ ∃ i j, w = List.replicate i a ++ List.replicate j b := by
    intro w
    induction w with
    | nil => exact ⟨fun _ => ⟨0, 0, rfl⟩, fun _ => (by decide : (0 : Fin 3) ≠ 2)⟩
    | cons x w ih =>
      rw [evalFrom_cons]
      by_cases hxa : x = a
      · subst hxa
        have hs : M.step 0 x = 0 := by simp [M]
        rw [hs, ih]
        constructor
        · rintro ⟨i, j, rfl⟩; exact ⟨i + 1, j, rfl⟩
        · rintro ⟨i, j, hij⟩
          rcases i with _ | i
          · rcases j with _ | j
            · simp at hij
            · exact absurd (List.cons_eq_cons.1 hij).1 hab
          · exact ⟨i, j, (List.cons_inj_right _).1 hij⟩
      · by_cases hxb : x = b
        · subst hxb
          have hs : M.step 0 x = 1 := by simp [M, hxa]
          rw [hs, run1]
          constructor
          · rintro ⟨j, rfl⟩; exact ⟨0, j + 1, rfl⟩
          · rintro ⟨i, j, hij⟩
            rcases i with _ | i
            · rcases j with _ | j
              · simp at hij
              · exact ⟨j, (List.cons_inj_right _).1 hij⟩
            · exact absurd (List.cons_eq_cons.1 hij).1 hxa
        · have hs : M.step 0 x = 2 := by simp [M, hxa, hxb]
          rw [hs, run2]
          simp only [ne_eq, not_true_eq_false, false_iff, not_exists]
          intro i j hij
          rcases i with _ | i
          · rcases j with _ | j
            · simp at hij
            · exact hxb (List.cons_eq_cons.1 hij).1
          · exact hxa (List.cons_eq_cons.1 hij).1
  refine isRegular_of_dfa M (fun w => ?_)
  show (∃ i j : ℕ, w = List.replicate i a ++ List.replicate j b) ↔ M.evalFrom M.start w ≠ 2
  exact (run0 w).symm

end RegularFilters

section TransferAnBn

/-! langlib's `{aⁿ bⁿ}` (`anbn` over `Bool`, `false = a`, `true = b`) and
`{a^(2^(k+1))}` (`unaryPow2`) moved onto a task alphabet. -/

variable {β : Type}

/-- The letter map `Bool → β`, `false ↦ a`, `true ↦ b`. -/
def boolMap (a b : β) : Bool → β := fun x => if x then b else a

theorem boolMap_injective {a b : β} (hab : a ≠ b) : Function.Injective (boolMap a b) := by
  intro x y h
  cases x <;> cases y <;> simp_all [boolMap, eq_comm]

/-- langlib's `anbn` moved along `boolMap a b` is `{aⁿ bⁿ | n ≥ 0}`. -/
theorem map_anbn (a b : β) :
    Language.map (boolMap a b) anbn =
      ({w | ∃ n : ℕ, w = List.replicate n a ++ List.replicate n b} : Language β) := by
  ext w
  constructor
  · intro hw
    obtain ⟨v, ⟨n, rfl⟩, rfl⟩ := (mem_map_iff _ _ _).1 hw
    exact ⟨n, by simp [boolMap]⟩
  · rintro ⟨n, rfl⟩
    exact (mem_map_iff _ _ _).2 ⟨_, ⟨n, rfl⟩, by simp [boolMap]⟩

/-- `{aⁿ bⁿ | n ≥ 0}` is context-free over any alphabet (langlib's `anbn_is_CF`, whose
witness is the grammar `S → aSb | ε`). -/
theorem isContextFree_anbn (a b : β) :
    Language.IsContextFree
      ({w | ∃ n : ℕ, w = List.replicate n a ++ List.replicate n b} : Language β) := by
  rw [← map_anbn]
  exact isContextFree_map _ (is_CF_iff_isContextFree.mp anbn_is_CF)

/-- `{a^(j·n) b^(k·n) | n ≥ 0}` is context-free (the image of `{aⁿ bⁿ}` under the
homomorphism `a ↦ aʲ`, `b ↦ bᵏ`); e.g. `j = 1`, `k = 2` gives `{aⁿ b²ⁿ}`. -/
theorem isContextFree_anbn_mul (a b : β) (j k : ℕ) :
    Language.IsContextFree
      ({w | ∃ n : ℕ, w = List.replicate (j * n) a ++ List.replicate (k * n) b} :
        Language β) := by
  have e : ({w | ∃ n : ℕ, w = List.replicate (j * n) a ++ List.replicate (k * n) b} :
      Language β) = anbn.homomorphicImage
        (fun x => if x then List.replicate k b else List.replicate j a) := by
    ext u
    constructor
    · rintro ⟨n, rfl⟩
      refine (mem_homomorphicImage_iff _ _ _).2 ⟨_, ⟨n, rfl⟩, ?_⟩
      simp [List.flatMap_append, List.flatMap_replicate, Nat.mul_comm]
    · intro hu
      obtain ⟨w, ⟨n, rfl⟩, rfl⟩ := (mem_homomorphicImage_iff _ _ _).1 hu
      exact ⟨n, by simp [List.flatMap_append, List.flatMap_replicate, Nat.mul_comm]⟩
  rw [e]
  exact isContextFree_homomorphicImage _ (is_CF_iff_isContextFree.mp anbn_is_CF)

/-- `{aⁿ bⁿ | n ≥ 0}` is deterministic context-free (langlib's DPDA `anbn_is_DCF`, moved
along `boolMap a b`). -/
theorem isDCF_anbn [Fintype β] {a b : β} (hab : a ≠ b) :
    is_DCF ({w | ∃ n : ℕ, w = List.replicate n a ++ List.replicate n b} : Language β) := by
  rw [← map_anbn]
  exact DCF_of_map_injective_DCF (boolMap_injective hab) _ anbn_is_DCF

/-- `{aⁿ bⁿ | n ≥ 0}` is not regular (langlib's `map_anbn_not_isRegular`); the usual
target of `not_isRegular_of_inf_isRegular`. -/
theorem not_isRegular_anbn {a b : β} (hab : a ≠ b) :
    ¬ Language.IsRegular
      ({w | ∃ n : ℕ, w = List.replicate n a ++ List.replicate n b} : Language β) := by
  rw [← map_anbn]
  exact map_anbn_not_isRegular (boolMap_injective hab)

/-- `{a^(2^k) | k ≥ 0}` is not context-free (langlib's `notCF_unaryPow2`, i.e. the
`k ≥ 1` part over `Bool`, reached by the letter map `_ ↦ false` and the regular filter
`|w| ≥ 2`). -/
theorem not_isContextFree_pow2 (a : β) :
    ¬ Language.IsContextFree ({w | ∃ k : ℕ, w = List.replicate (2 ^ k) a} : Language β) := by
  intro h
  -- the filter `|w| ≥ 2` as a three-state DFA (state 2 = "at least two letters read")
  let M : DFA Bool (Fin 3) :=
    { step := fun s _ => if s.val = 0 then 1 else 2
      start := 0
      accept := {s | s.val = 2} }
  have hrun : ∀ (s : Fin 3) (w : List Bool),
      (M.evalFrom s w).val = min (s.val + w.length) 2 := by
    intro s w
    induction w generalizing s with
    | nil => simp [DFA.evalFrom]; omega
    | cons x w ih =>
      rw [evalFrom_cons, ih]
      simp only [M, List.length_cons]
      split <;> simp_all <;> omega
  have hR : Language.IsRegular ({w | 2 ≤ w.length} : Language Bool) :=
    isRegular_of_dfa M (fun w => by
      show 2 ≤ w.length ↔ (M.evalFrom M.start w).val = 2
      rw [hrun]; simp [M])
  have h2 := isContextFree_inf_isRegular (isContextFree_map (fun _ : β => false) h) hR
  apply notCF_unaryPow2
  rw [is_CF_iff_isContextFree]
  convert h2 using 1
  ext w
  constructor
  · rintro ⟨k, rfl⟩
    refine ⟨(mem_map_iff _ _ _).2 ⟨List.replicate (2 ^ (k + 1)) a, ⟨k + 1, rfl⟩, by simp⟩, ?_⟩
    show 2 ≤ (List.replicate (2 ^ (k + 1)) false).length
    simp [Nat.pow_succ]
    exact Nat.one_le_two_pow
  · rintro ⟨hw, hlen⟩
    obtain ⟨v, ⟨k, rfl⟩, rfl⟩ := (mem_map_iff _ _ _).1 hw
    change 2 ≤ (List.map _ (List.replicate (2 ^ k) a)).length at hlen
    obtain ⟨j, rfl⟩ : ∃ j, k = j + 1 := by
      rcases k with _ | j
      · simp at hlen
      · exact ⟨j, rfl⟩
    exact ⟨j, by simp⟩

end TransferAnBn

end TflLean
