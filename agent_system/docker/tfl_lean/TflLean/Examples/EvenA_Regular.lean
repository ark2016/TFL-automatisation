import TflLean

inductive Sym
  | a | b
  deriving DecidableEq, Fintype, Repr

def L : Language Sym := {w : List Sym | Even (w.count Sym.a)}

theorem tfl_main : L.IsRegular := by
  -- Explicit two-state DFA: the state is the parity of the a's read so far
  -- (`true` = odd); accept iff the parity is even.
  let M : DFA Sym Bool :=
    { step := fun s x => if x = Sym.a then !s else s
      start := false
      accept := {false} }
  -- Invariant, by induction on the word (generalizing the start state).
  have key : ∀ (w : List Sym) (s : Bool),
      M.evalFrom s w = (s ^^ !decide (Even (w.count Sym.a))) := by
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

#print axioms tfl_main
