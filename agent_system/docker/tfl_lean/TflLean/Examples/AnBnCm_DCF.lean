import Lean.Replay
import Lean.Elab.Command
import TflLean
import Langlib.Classes.DeterministicContextFree.Definition

inductive Letter
  | a | b | c
  deriving DecidableEq, Repr

instance : Fintype Letter where
  elems := {Letter.a, Letter.b, Letter.c}
  complete := by intro x; cases x <;> decide

def L : Language Letter := {w : List Letter | ∃ m n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ++ List.replicate m Letter.c ∧ n ≥ 0 ∧ m ≥ 0}

theorem tfl_main : is_DCF L := by
  -- L is langlib's DCFL {aⁿ bⁿ cᵐ} (DCFLIntersection.DCF_lang_eq_any over Fin 3)
  -- moved to Letter: TflLean.isDCF_anbncm after reordering the existentials.
  have e : L = {w | ∃ n m : ℕ, w = List.replicate n Letter.a ++
      List.replicate n Letter.b ++ List.replicate m Letter.c} := by
    ext w
    constructor
    · rintro ⟨m, n, rfl, -, -⟩
      exact ⟨n, m, rfl⟩
    · rintro ⟨n, m, rfl⟩
      exact ⟨m, n, rfl, Nat.zero_le _, Nat.zero_le _⟩
  rw [e]
  exact TflLean.isDCF_anbncm (by decide) (by decide) (by decide)

open Lean in
run_cmd do
  let env ← getEnv
  let mut newConsts : Std.HashMap Name ConstantInfo := {}
  for (n, ci) in env.constants.toList do
    if !env.const2ModIdx.contains n then
      newConsts := newConsts.insert n ci
  try
    let importEnv ← importModules env.imports {} (trustLevel := 0)
    let _ ← Lean.Environment.replay newConsts importEnv
    logInfo "TFL_REPLAY_OK"
  catch e =>
    logError s!"TFL_REPLAY_FAIL: {(← e.toMessageData.toString)}"

#print axioms tfl_main
