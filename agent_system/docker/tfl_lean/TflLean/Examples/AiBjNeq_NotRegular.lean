import Lean.Replay
import Lean.Elab.Command
import TflLean

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | ∃ i j : ℕ, w = List.replicate i Letter.a ++ List.replicate j Letter.b ∧ i ≠ j}

theorem tfl_main : ¬ L.IsRegular := by
  -- Intersection method: if L were regular, so would be Lᶜ ∩ a*b* (REG is closed under
  -- complement and intersection), and that language is {aⁿbⁿ}, which is not regular.
  intro h
  apply TflLean.not_isRegular_anbn (a := Letter.a) (b := Letter.b) (by decide)
  have e : ({w | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b} :
      Language Letter) = Lᶜ ⊓ {w | ∃ i j : ℕ, w = List.replicate i Letter.a ++
      List.replicate j Letter.b} := by
    ext w
    constructor
    · rintro ⟨n, rfl⟩
      refine ⟨?_, n, n, rfl⟩
      rintro ⟨i, j, he, hne⟩
      obtain ⟨rfl, rfl⟩ := TflLean.replicate_append_replicate_inj (by decide) he
      exact hne rfl
    · rintro ⟨hc, i, j, rfl⟩
      have hij : i = j := by
        by_contra hne
        exact hc ⟨i, j, rfl, hne⟩
      exact ⟨i, by rw [hij]⟩
  rw [e]
  exact h.compl.inf (TflLean.isRegular_replicate_append_replicate (by decide))

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
