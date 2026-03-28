/-
  Template: Prove non-regularity via the Pumping Lemma.

  Self-contained — no imports, no Mathlib.
  Strategy: assume L is regular (exists a recognising DFA),
  then derive a contradiction.
  FILL markers use `sorry`.
-/

-- ===== Base definitions (from RegLang.lean) =====

structure DFA (Alpha : Type) (State : Type) where
  step : State → Alpha → State
  start : State
  accept : State → Bool

namespace DFA

def run (M : DFA Alpha State) (w : List Alpha) : State :=
  w.foldl M.step M.start

def accepts (M : DFA Alpha State) (w : List Alpha) : Bool :=
  M.accept (M.run w)

end DFA

def Language (Alpha : Type) := List Alpha → Prop

def IsRegular {Alpha : Type} (L : Language Alpha) : Prop :=
  ∃ (n : Nat) (M : DFA Alpha (Fin n)),
    ∀ w, M.accepts w = true ↔ L w

def List.repeat' (l : List α) : Nat → List α
  | 0 => []
  | n + 1 => l ++ List.repeat' l n

-- ===== Template begins =====

-- FILL: Define alphabet
abbrev Alpha : Type := Fin 2

-- FILL: Language predicate (the language claimed to be non-regular)
def languagePred : Language Alpha := sorry  -- «PREDICATE»

/-- The language is not regular. -/
theorem not_regular : ¬ IsRegular (Alpha := Alpha) languagePred := by
  intro ⟨n, M, hM⟩
  -- FILL: Choose a word w in L with |w| >= n
  -- FILL: For any split w = x ++ y ++ z with |y| >= 1 and |x ++ y| <= n,
  --       find a pump index i such that x ++ y^i ++ z is not in L
  sorry  -- «PROOF»
