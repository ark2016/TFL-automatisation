import Lean.Replay
import Lean.Elab.Command
import TflLean
import Mathlib.Computability.ContextFreeGrammar

inductive Letter
  | a | b
  deriving DecidableEq, Repr

inductive NT
  | S
  deriving DecidableEq, Repr

def g : ContextFreeGrammar Letter :=
  { NT := NT, initial := NT.S, rules := {⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, []⟩} }

def L : Language Letter := g.language ⊓ ({w : List Letter | (w.length) % 4 = 0} : Language Letter)

theorem tfl_main : L.IsContextFree := by
  -- L is the grammar's language (S → aSb | ε) cut by the filter |w| ≡ 0 (mod 4); the
  -- filter is regular (a cyclic length counter), and CFL ∩ REG is context-free.
  exact TflLean.isContextFree_inf_isRegular ⟨g, rfl⟩ (TflLean.isRegular_length_mod 4 0 (by decide))

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
