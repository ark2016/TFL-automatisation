/-
  Template: Prove non-regularity via the Myhill-Nerode theorem.

  Self-contained — no imports, no Mathlib.
  Strategy: exhibit infinitely many pairwise distinguishable words,
  which forces any recognising DFA to have infinitely many states,
  contradicting finiteness.
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

-- ===== Template begins =====

-- FILL: Define alphabet
abbrev Alpha : Type := Fin 2

-- FILL: Language predicate (the language claimed to be non-regular)
def languagePred : Language Alpha := sorry  -- «PREDICATE»

-- FILL: An infinite family of words indexed by Nat.
-- For distinct i, j these words must be Myhill-Nerode distinguishable.
def words : Nat → List Alpha := sorry  -- «WORD_FUNCTION»

-- FILL: For each pair (i, j) with i <> j, a distinguishing suffix z
-- such that exactly one of (words i ++ z) and (words j ++ z) is in L.
def distinguisher : Nat → Nat → List Alpha := sorry  -- «CONTEXT_FUNCTION»

-- FILL: Proof that distinct indices yield distinguishable words
theorem pairwise_distinct (i j : Nat) (hij : i ≠ j) :
    ∃ z : List Alpha,
      languagePred (words i ++ z) ↔ ¬ languagePred (words j ++ z) := by
  sorry  -- «DISTINGUISHING_PROOF»

/-- The language is not regular. -/
theorem not_regular : ¬ IsRegular (Alpha := Alpha) languagePred := by
  intro ⟨n, M, hM⟩
  -- With n states, by pigeonhole two of words 0, ..., words n
  -- must reach the same DFA state, contradicting pairwise_distinct.
  sorry  -- «PROOF»
