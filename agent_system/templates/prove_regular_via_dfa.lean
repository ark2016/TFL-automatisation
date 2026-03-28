/-
  Template: Prove regularity by constructing a DFA.

  Self-contained — no imports, no Mathlib.
  FILL markers indicate placeholders for the Formalizer Agent.
  All FILL sections use `sorry` so the file compiles as-is.
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

-- FILL: Define alphabet (e.g., Fin 2 for {a, b})
abbrev Alpha : Type := Fin 2

-- FILL: Number of DFA states
abbrev numStates : Nat := 3  -- «NUM_STATES»

-- FILL: State type
abbrev Q : Type := Fin numStates

-- FILL: Transition function
def delta : Q → Alpha → Q := sorry  -- «STATE_TRANSITIONS»

-- FILL: Start state
def q0 : Q := ⟨0, by decide⟩  -- «START»

-- FILL: Accept predicate
def isAccept : Q → Bool := sorry  -- «ACCEPT_STATES»

/-- The constructed DFA. -/
def myDFA : DFA Alpha Q where
  step := delta
  start := q0
  accept := isAccept

-- FILL: Language predicate (the language we claim the DFA recognises)
def languagePred : Language Alpha := sorry  -- «LANGUAGE_PREDICATE»

-- FILL: Correctness — the DFA recognises exactly `languagePred`
theorem dfa_correct (w : List Alpha) :
    myDFA.accepts w = true ↔ languagePred w := by
  sorry  -- «PROOF»

/-- Corollary: the language is regular. -/
theorem language_is_regular : IsRegular (Alpha := Alpha) languagePred :=
  ⟨numStates, myDFA, dfa_correct⟩
