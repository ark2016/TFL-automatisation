/-
  RegLang.lean — Base definitions for TFL Agent System proof templates.
  No Mathlib dependency — everything is self-contained.
  This file is the canonical reference; each template copies the
  definitions it needs so it can be checked standalone.
-/

-- A DFA with parameterized alphabet and state types.
structure DFA (Alpha : Type) (State : Type) where
  step : State → Alpha → State
  start : State
  accept : State → Bool

namespace DFA

/-- Run a DFA on a word (list of symbols), returning the final state. -/
def run (M : DFA Alpha State) (w : List Alpha) : State :=
  w.foldl M.step M.start

/-- A DFA accepts a word when the final state satisfies `accept`. -/
def accepts (M : DFA Alpha State) (w : List Alpha) : Bool :=
  M.accept (M.run w)

end DFA

/-- A language over `Alpha` is a predicate on words. -/
def Language (Alpha : Type) := List Alpha → Prop

/-- A language is regular if some finite-state DFA recognises it. -/
def IsRegular {Alpha : Type} (L : Language Alpha) : Prop :=
  ∃ (n : Nat) (M : DFA Alpha (Fin n)),
    ∀ w, M.accepts w = true ↔ L w

/-- Repeat a list `n` times (helper for pumping). -/
def List.repeat' (l : List α) : Nat → List α
  | 0 => []
  | n + 1 => l ++ List.repeat' l n
