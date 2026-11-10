import Lean.Replay
import Lean.Elab.Command
import TflLean
import Langlib.Classes.DeterministicContextFree.Definition
import Mathlib.Computability.ContextFreeGrammar

inductive Letter
  | a | b
  deriving DecidableEq, Repr

instance : Fintype Letter where
  elems := {Letter.a, Letter.b}
  complete := by intro x; cases x <;> decide

inductive NT
  | S | A
  deriving DecidableEq, Repr

def g : ContextFreeGrammar Letter :=
  { NT := NT, initial := NT.S, rules := {⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, [Symbol.terminal Letter.b, Symbol.terminal Letter.a]⟩, ⟨NT.S, [Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a, Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a]⟩} }

def L : Language Letter := g.language

theorem tfl_main : is_DCF L := by
  -- L = g.language is the set LangS a b of TflLean.GrammarASSb (S → aSSb | ba | Ab,
  -- A → aAb | a as inductive predicates); TflLean.ASSb.isDCF_LangS is its DPDA.
  have key : L = ({w | TflLean.ASSb.LangS Letter.a Letter.b w} : Language Letter) := by
    ext w
    constructor
    · -- ⊆: the predicates (S ↦ LangS, A ↦ LangA) are closed under the five rules.
      intro h
      refine TflLean.ASSb.den_of_mem_language g
        (fun (n : NT) (v : List Letter) => match n with
          | NT.S => TflLean.ASSb.LangS Letter.a Letter.b v
          | NT.A => TflLean.ASSb.LangA Letter.a Letter.b v) ?_ h
      show ∀ r : ContextFreeRule Letter NT,
        r ∈ ({⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, [Symbol.terminal Letter.b, Symbol.terminal Letter.a]⟩, ⟨NT.S, [Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a, Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a]⟩} : Finset (ContextFreeRule Letter NT)) →
        ∀ v : List Letter, TflLean.ASSb.Sem _ r.output v → _
      intro r hr' v hs
      simp only [Finset.mem_insert, Finset.mem_singleton] at hr'
      rcases hr' with rfl | rfl | rfl | rfl | rfl
      · cases hs with
        | term _ hs => cases hs with
          | nt hx hs => cases hs with
            | nt hy hs => cases hs with
              | term _ hs =>
                cases hs
                show TflLean.ASSb.LangS Letter.a Letter.b _
                simpa using TflLean.ASSb.LangS.node hx hy
      · cases hs with
        | term _ hs => cases hs with
          | term _ hs =>
            cases hs
            exact TflLean.ASSb.LangS.ba
      · cases hs with
        | nt hx hs => cases hs with
          | term _ hs =>
            cases hs
            exact TflLean.ASSb.LangS.ofA hx
      · cases hs with
        | term _ hs => cases hs with
          | nt hx hs => cases hs with
            | term _ hs =>
              cases hs
              exact TflLean.ASSb.LangA.step hx
      · cases hs with
        | term _ hs =>
          cases hs
          exact TflLean.ASSb.LangA.base
    · -- ⊇: every LangA / LangS word is derived by the matching rules.
      intro h
      have prod : ∀ r : ContextFreeRule Letter NT,
          r ∈ ({⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, [Symbol.terminal Letter.b, Symbol.terminal Letter.a]⟩, ⟨NT.S, [Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a, Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a]⟩} : Finset (ContextFreeRule Letter NT)) →
          g.Derives [Symbol.nonterminal r.input] r.output := fun r hr =>
        ContextFreeGrammar.Produces.single ⟨r, hr, ContextFreeRule.Rewrites.input_output⟩
      have derA : ∀ x, TflLean.ASSb.LangA Letter.a Letter.b x →
          g.Derives [Symbol.nonterminal NT.A] (x.map Symbol.terminal) := by
        intro x hx
        induction hx with
        | base => exact prod ⟨NT.A, [Symbol.terminal Letter.a]⟩ (by simp)
        | step _ ih =>
          refine (prod ⟨NT.A, [Symbol.terminal Letter.a, Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩ (by simp)).trans ?_
          simp only [List.map_cons, List.map_append, List.map_nil]
          exact (ih.append_right [Symbol.terminal Letter.b]).append_left [Symbol.terminal Letter.a]
      have derS : ∀ x, TflLean.ASSb.LangS Letter.a Letter.b x →
          g.Derives [Symbol.nonterminal NT.S] (x.map Symbol.terminal) := by
        intro x hx
        induction hx with
        | ba => exact prod ⟨NT.S, [Symbol.terminal Letter.b, Symbol.terminal Letter.a]⟩ (by simp)
        | ofA hx =>
          refine (prod ⟨NT.S, [Symbol.nonterminal NT.A, Symbol.terminal Letter.b]⟩ (by simp)).trans ?_
          simp only [List.map_cons, List.map_append, List.map_nil]
          exact (derA _ hx).append_right [Symbol.terminal Letter.b]
        | @node x y _ _ ihx ihy =>
          refine (prod ⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩ (by simp)).trans ?_
          have h1 := (ihx.append_right [Symbol.nonterminal NT.S, Symbol.terminal Letter.b]).append_left [Symbol.terminal Letter.a]
          have h2 := (ihy.append_right [Symbol.terminal Letter.b]).append_left (Symbol.terminal Letter.a :: x.map Symbol.terminal)
          simp only [List.map_cons, List.map_append, List.map_nil, List.append_assoc]
          exact h1.trans h2
      exact derS w h
  rw [key]
  exact TflLean.ASSb.isDCF_LangS (by decide)

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
