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

def L : Language Letter := {w : List Letter | ∃ n_u n_v n_w : ℕ, w = List.replicate n_u Letter.a ++ List.replicate n_v Letter.b ++ List.replicate n_w Letter.c ∧ n_u ≥ 1 ∧ n_v ≥ 1 ∧ n_w ≥ 1 ∧ n_u = n_v}

theorem tfl_main : is_DCF L := by
  -- L is langlib's DCFL {aⁿ⁺¹ bⁿ⁺¹ cᵐ⁺¹} (DCF_lang_eq_any_pos over Fin 3) moved to
  -- Letter: rewrite L into the shape of TflLean.isDCF_anbncm_pos, then apply it.
  have e : L = {w | ∃ n m : ℕ, w = List.replicate (n + 1) Letter.a ++
      List.replicate (n + 1) Letter.b ++ List.replicate (m + 1) Letter.c} := by
    ext w
    constructor
    · rintro ⟨i, j, k, rfl, hi, -, hk, rfl⟩
      refine ⟨i - 1, k - 1, ?_⟩
      rw [Nat.sub_add_cancel hi, Nat.sub_add_cancel hk]
    · rintro ⟨n, m, rfl⟩
      exact ⟨n + 1, n + 1, m + 1, rfl, by omega, by omega, by omega, rfl⟩
  rw [e]
  exact TflLean.isDCF_anbncm_pos (by decide) (by decide) (by decide)

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
