import Lean.Replay
import Lean.Elab.Command
import TflLean

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0}

theorem tfl_main : ¬ L.IsRegular := by
  -- Myhill–Nerode by pigeonhole: of the card σ + 1 prefixes a^0 … a^(card σ),
  -- two (a^i, a^j with i ≠ j) end in the same DFA state; a^i b^i is accepted,
  -- hence so is a^j b^i, which is not in L (count the a's and the b's).
  rintro ⟨σ, _, M, hM⟩
  obtain ⟨i, j, hij, heq⟩ := Fintype.exists_ne_map_eq_of_card_lt
    (fun k : Fin (Fintype.card σ + 1) => M.eval (List.replicate k Letter.a)) (by simp)
  have hacc : List.replicate (i : ℕ) Letter.a ++ List.replicate i Letter.b ∈ M.accepts := by
    rw [hM]; exact ⟨i, rfl, Nat.zero_le _⟩
  have hacc' : List.replicate (j : ℕ) Letter.a ++ List.replicate i Letter.b ∈ M.accepts := by
    rw [DFA.mem_accepts] at hacc ⊢
    simp only [DFA.eval, DFA.evalFrom_of_append] at hacc heq ⊢
    rw [← heq]; exact hacc
  rw [hM] at hacc'
  obtain ⟨n, hn, -⟩ := hacc'
  have h1 := congrArg (List.count Letter.a) hn
  have h2 := congrArg (List.count Letter.b) hn
  simp [List.count_replicate] at h1 h2
  exact hij (Fin.ext (by omega))

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
