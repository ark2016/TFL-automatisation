import Lean.Replay
import Lean.Elab.Command
import TflLean
import Mathlib.Computability.ContextFreeGrammar
import Langlib.Classes.ContextFree.Pumping.Pumping
import Langlib.Classes.ContextFree.Basics.Ogden

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ++ List.replicate n Letter.a ∧ n ≥ 0}

theorem tfl_main : ¬ L.IsContextFree := by
  -- On the slice a⁺ b⁺ a⁺ the language is exactly {aⁿ bⁿ aⁿ | n ≥ 1}; TflLean's
  -- transfer lemma (inverse homomorphism 0 ↦ a, 1 ↦ b, 2 ↦ a, intersection with
  -- the regular a⁺b⁺c⁺, langlib's notCF_lang_eq_eq_pos) does the rest.
  apply TflLean.not_isContextFree_of_slice Letter.a Letter.b Letter.a
  intro n m k
  constructor
  · rintro ⟨p, hp, -⟩
    -- a^(n+1) b^(m+1) a^(k+1) = a^p b^p a^p: peel the leading a-block, then compare.
    simp only [List.append_assoc] at hp
    obtain ⟨h1, hp⟩ := TflLean.replicate_append_inj (by simp [List.replicate_succ])
      (by cases p <;> simp [List.replicate_succ]) hp
    obtain ⟨h2, h3⟩ := TflLean.replicate_append_replicate_inj (by decide) hp
    omega
  · rintro ⟨rfl, rfl⟩
    exact ⟨n + 1, rfl, Nat.zero_le _⟩

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
