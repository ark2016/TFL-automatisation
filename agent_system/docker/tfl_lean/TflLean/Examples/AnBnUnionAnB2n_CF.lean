import Lean.Replay
import Lean.Elab.Command
import TflLean
import Mathlib.Computability.ContextFreeGrammar

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | (∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0) ∨ (∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate (2 * n) Letter.b ∧ n ≥ 0)}

theorem tfl_main : L.IsContextFree := by
  -- L = {aⁿbⁿ} ∪ {aⁿb²ⁿ}. Both parts are images of langlib's {aⁿbⁿ} (grammar S → aSb | ε)
  -- under the homomorphisms a ↦ a, b ↦ b and a ↦ a, b ↦ bb; CFL is closed under union.
  let A : Language Letter :=
    {w | ∃ n : ℕ, w = List.replicate (1 * n) Letter.a ++ List.replicate (1 * n) Letter.b}
  let B : Language Letter :=
    {w | ∃ n : ℕ, w = List.replicate (1 * n) Letter.a ++ List.replicate (2 * n) Letter.b}
  have e : L = A + B := by
    ext w
    rw [Language.mem_add]
    constructor
    · rintro (⟨n, rfl, -⟩ | ⟨n, rfl, -⟩)
      · exact Or.inl ⟨n, by simp⟩
      · exact Or.inr ⟨n, by simp⟩
    · rintro (⟨n, rfl⟩ | ⟨n, rfl⟩)
      · exact Or.inl ⟨n, by simp, Nat.zero_le _⟩
      · exact Or.inr ⟨n, by simp, Nat.zero_le _⟩
  rw [e]
  exact TflLean.isContextFree_add (TflLean.isContextFree_anbn_mul _ _ 1 1)
    (TflLean.isContextFree_anbn_mul _ _ 1 2)

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
