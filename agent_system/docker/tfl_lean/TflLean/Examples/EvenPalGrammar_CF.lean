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
  { NT := NT, initial := NT.S, rules := {⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.terminal Letter.a]⟩, ⟨NT.S, [Symbol.terminal Letter.b, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, []⟩} }

def L : Language Letter := g.language

theorem tfl_main : L.IsContextFree := by
  -- L is defined as g.language, so the statement's own grammar is the witness.
  exact ⟨g, rfl⟩

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
