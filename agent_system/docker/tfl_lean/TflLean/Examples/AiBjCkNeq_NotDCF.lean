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

def L : Language Letter := {w : List Letter | ∃ i j k : ℕ, w = List.replicate i Letter.a ++ List.replicate j Letter.b ++ List.replicate k Letter.c ∧ (i ≠ j ∨ j ≠ k)}

theorem tfl_main : ¬ is_DCF L := by
  -- CFL but not DCFL (a witness of DCFL ⊊ CFL). If L were DCF, so would be its
  -- complement (DCF_closedUnderComplement), hence context-free (DCFL ⊆ CFL); but on
  -- the slice a⁺ b⁺ c⁺ the complement is exactly {aⁿ bⁿ cⁿ | n ≥ 1}, which is not.
  intro h
  apply TflLean.not_isContextFree_of_slice (L := Lᶜ) Letter.a Letter.b Letter.c _
    (TflLean.isContextFree_of_isDCF (DCF_closedUnderComplement L h))
  intro n m k
  constructor
  · intro hn
    by_contra hne
    exact hn ⟨n + 1, m + 1, k + 1, rfl, by omega⟩
  · rintro ⟨rfl, rfl⟩ ⟨i, j, k', he, hne⟩
    obtain ⟨h1, h2, h3⟩ :=
      TflLean.replicate_append_replicate_append_replicate_inj (by decide) (by decide) (by decide) he
    omega

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
