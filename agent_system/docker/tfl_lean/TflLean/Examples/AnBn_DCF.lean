import Lean.Replay
import Lean.Elab.Command
import TflLean
import Langlib.Classes.DeterministicContextFree.Definition

inductive Letter
  | a | b
  deriving DecidableEq, Repr

instance : Fintype Letter where
  elems := {Letter.a, Letter.b}
  complete := by intro x; cases x <;> decide

def L : Language Letter := {w : List Letter | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0}

theorem tfl_main : is_DCF L := by
  -- L is {aⁿbⁿ}: langlib's DPDA for it (anbn_is_DCF over Bool) moved to Letter.
  have e : L = {w | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b} := by
    ext w
    constructor
    · rintro ⟨n, rfl, -⟩
      exact ⟨n, rfl⟩
    · rintro ⟨n, rfl⟩
      exact ⟨n, rfl, Nat.zero_le _⟩
  rw [e]
  exact TflLean.isDCF_anbn (by decide)

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
