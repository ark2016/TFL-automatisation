import Lean.Replay
import Lean.Elab.Command
import TflLean

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | Even (w.count Letter.a)}

theorem tfl_main : L.IsRegular := by
  -- Explicit two-state DFA: the state is the parity of the a's read so far
  -- (`true` = odd); accept iff the parity is even.
  let M : DFA Letter Bool :=
    { step := fun s x => if x = Letter.a then !s else s
      start := false
      accept := {false} }
  -- Invariant, by induction on the word (generalizing the start state).
  have key : ∀ (w : List Letter) (s : Bool),
      M.evalFrom s w = (s ^^ !decide (Even (w.count Letter.a))) := by
    intro w
    induction w with
    | nil => intro s; simp [DFA.evalFrom]
    | cons x w ih =>
      intro s
      show M.evalFrom (M.step s x) w = _
      rw [ih]
      cases x <;> cases s <;> simp [M, List.count_cons, Nat.even_add_one, ← Nat.not_even_iff_odd]
  refine ⟨Bool, inferInstance, M, ?_⟩
  ext w
  rw [DFA.mem_accepts, DFA.eval, key]
  simp [M]
  rfl

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
