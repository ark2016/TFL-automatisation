import Lean.Replay
import Lean.Elab.Command
import TflLean
import Mathlib.Computability.ContextFreeGrammar

inductive Letter
  | a | b
  deriving DecidableEq, Repr

inductive NT
  | S | A
  deriving DecidableEq, Repr

def g : ContextFreeGrammar Letter :=
  { NT := NT, initial := NT.S, rules := {⟨NT.S, [Symbol.nonterminal NT.S, Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩, ⟨NT.S, []⟩, ⟨NT.S, [Symbol.nonterminal NT.A]⟩, ⟨NT.A, [Symbol.terminal Letter.b, Symbol.terminal Letter.b]⟩, ⟨NT.A, [Symbol.terminal Letter.a, Symbol.terminal Letter.a]⟩, ⟨NT.A, [Symbol.terminal Letter.b, Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩} }

def L : Language Letter := g.language

theorem tfl_main : ¬ L.IsRegular := by
  letI : DecidableEq g.NT := inferInstanceAs (DecidableEq NT)
  have hnt : ∀ x : NT, x = NT.S ∨ x = NT.A := by
    intro x
    cases x <;> simp
  have hout : ∀ r ∈ g.rules, r.output.count (Symbol.terminal Letter.a : Symbol Letter g.NT) + 2 * (r.output.count (Symbol.nonterminal NT.S : Symbol Letter g.NT) + r.output.count (Symbol.nonterminal NT.A : Symbol Letter g.NT)) ≤ 3 * r.output.count (Symbol.terminal Letter.b : Symbol Letter g.NT) + 2 := by
    first | decide | (intro r hr; rcases Finset.mem_insert.1 hr with rfl | hr; decide; rcases Finset.mem_insert.1 hr with rfl | hr; decide; rcases Finset.mem_insert.1 hr with rfl | hr; decide; rcases Finset.mem_insert.1 hr with rfl | hr; decide; rcases Finset.mem_insert.1 hr with rfl | hr; decide; rcases Finset.mem_singleton.1 hr with rfl; decide)
  have inv : ∀ u v : List (Symbol Letter g.NT), g.Derives u v → u.count (Symbol.terminal Letter.a : Symbol Letter g.NT) + 2 * (u.count (Symbol.nonterminal NT.S : Symbol Letter g.NT) + u.count (Symbol.nonterminal NT.A : Symbol Letter g.NT)) ≤ 3 * u.count (Symbol.terminal Letter.b : Symbol Letter g.NT) + 2 → v.count (Symbol.terminal Letter.a : Symbol Letter g.NT) + 2 * (v.count (Symbol.nonterminal NT.S : Symbol Letter g.NT) + v.count (Symbol.nonterminal NT.A : Symbol Letter g.NT)) ≤ 3 * v.count (Symbol.terminal Letter.b : Symbol Letter g.NT) + 2 := by
    intro u v h hu
    induction h with
    | refl => exact hu
    | tail _ hp ih =>
      obtain ⟨⟨ri, ro⟩, hr, hrw⟩ := hp
      have ho := hout _ hr
      rw [ContextFreeRule.rewrites_iff] at hrw
      obtain ⟨p, q, rfl, rfl⟩ := hrw
      rcases hnt ri with rfl | rfl <;> simp [List.count_append, List.count_cons, List.count_nil] at ih ho ⊢ <;> omega
  have step : ∀ (p q : List (Symbol Letter g.NT)) (n : g.NT) (o : List (Symbol Letter g.NT)), (⟨n, o⟩ : ContextFreeRule Letter g.NT) ∈ g.rules → g.Derives (p ++ [Symbol.nonterminal n] ++ q) (p ++ o ++ q) := by
    intro p q n o hr
    refine Relation.ReflTransGen.single ?_
    unfold ContextFreeGrammar.Produces
    refine ⟨⟨n, o⟩, hr, ?_⟩
    rw [ContextFreeRule.rewrites_iff]
    exact ⟨p, q, rfl, rfl⟩
  have ctx : ∀ (p q u v : List (Symbol Letter g.NT)), g.Derives u v → g.Derives (p ++ u ++ q) (p ++ v ++ q) := by
    intro p q u v h
    induction h with
    | refl => exact Relation.ReflTransGen.refl
    | tail _ hbc ih =>
      obtain ⟨r, hr, hrw⟩ := hbc
      rw [ContextFreeRule.rewrites_iff] at hrw
      obtain ⟨p', q', rfl, rfl⟩ := hrw
      refine Relation.ReflTransGen.tail ih ?_
      unfold ContextFreeGrammar.Produces
      refine ⟨r, hr, ?_⟩
      rw [ContextFreeRule.rewrites_iff]
      exact ⟨p ++ p', q' ++ q, by simp, by simp⟩
  have hbase : g.Derives [Symbol.nonterminal NT.S] [Symbol.terminal Letter.a, Symbol.terminal Letter.a] := by
    have h1 := step [] [] NT.S [Symbol.nonterminal NT.A] (by first | decide | exact Finset.mem_insert_of_mem (Finset.mem_insert_of_mem (Finset.mem_insert_self _ _)))
    have h2 := step [] [] NT.A [Symbol.terminal Letter.a, Symbol.terminal Letter.a] (by first | decide | exact Finset.mem_insert_of_mem (Finset.mem_insert_of_mem (Finset.mem_insert_of_mem (Finset.mem_insert_of_mem (Finset.mem_insert_self _ _)))))
    simp only [List.nil_append, List.append_nil] at h1 h2
    exact Relation.ReflTransGen.trans h1 h2
  have hsnoc : ∀ (n : ℕ) (x : Letter), List.replicate (n + 1) x = List.replicate n x ++ [x] := by
    intro n x
    induction n with
    | zero => rfl
    | succ n ih =>
      show x :: List.replicate (n + 1) x = x :: (List.replicate n x ++ [x])
      rw [ih]
  have hmem : ∀ i : ℕ, g.Derives [Symbol.nonterminal NT.S] (List.map Symbol.terminal (List.replicate (3 * i + 2) Letter.a ++ List.replicate i Letter.b)) := by
    intro i
    induction i with
    | zero => first | exact hbase | simpa using hbase
    | succ i ih =>
      have h1 := step [] [] NT.S [Symbol.nonterminal NT.S, Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.terminal Letter.b] (by first | decide | exact Finset.mem_insert_self _ _)
      have h2 := ctx [] [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, Symbol.terminal Letter.b] _ _ hbase
      have h3 := ctx [Symbol.terminal Letter.a, Symbol.terminal Letter.a, Symbol.terminal Letter.a] [Symbol.terminal Letter.b] _ _ ih
      simp only [List.nil_append, List.append_nil, List.cons_append, List.singleton_append] at h1 h2
      have ea : List.replicate (3 * (i + 1) + 2) Letter.a = [Letter.a, Letter.a, Letter.a] ++ List.replicate (3 * i + 2) Letter.a := by
        rw [show 3 * (i + 1) + 2 = (3 * i + 2) + 3 by omega]
        all_goals first | rfl | simp [List.replicate_succ]
      have e : (List.map Symbol.terminal (List.replicate (3 * (i + 1) + 2) Letter.a ++ List.replicate (i + 1) Letter.b) : List (Symbol Letter g.NT)) = [Symbol.terminal Letter.a, Symbol.terminal Letter.a, Symbol.terminal Letter.a] ++ List.map Symbol.terminal (List.replicate (3 * i + 2) Letter.a ++ List.replicate i Letter.b) ++ [Symbol.terminal Letter.b] := by
        rw [ea, hsnoc i Letter.b]
        all_goals simp
      show g.Derives [Symbol.nonterminal NT.S] (List.map Symbol.terminal (List.replicate (3 * (i + 1) + 2) Letter.a ++ List.replicate (i + 1) Letter.b))
      rw [e]
      simp only [List.singleton_append, List.cons_append, List.nil_append] at h3 ⊢
      exact Relation.ReflTransGen.trans h1 (Relation.ReflTransGen.trans h2 h3)
  rintro ⟨σ, _, M, hM⟩
  have key : ∀ m n : ℕ, m < n → M.eval (List.replicate (3 * m + 2) Letter.a) = M.eval (List.replicate (3 * n + 2) Letter.a) → False := by
    intro m n hmn he
    have hacc : List.replicate (3 * m + 2) Letter.a ++ List.replicate m Letter.b ∈ M.accepts := by
      rw [hM]
      exact hmem m
    have hacc' : List.replicate (3 * n + 2) Letter.a ++ List.replicate m Letter.b ∈ M.accepts := by
      rw [DFA.mem_accepts] at hacc ⊢
      simp only [DFA.eval, DFA.evalFrom_of_append] at hacc he ⊢
      rw [← he]
      exact hacc
    rw [hM] at hacc'
    have hd : g.Derives [Symbol.nonterminal g.initial] (List.map Symbol.terminal (List.replicate (3 * n + 2) Letter.a ++ List.replicate m Letter.b)) := hacc'
    have hc := inv _ _ hd (by first | decide | simp [g, List.count_cons, List.count_nil])
    simp [List.count_append, List.count_replicate, List.map_append, List.map_replicate] at hc
    omega
  obtain ⟨i, j, hij, heq⟩ := Fintype.exists_ne_map_eq_of_card_lt
    (fun k : Fin (Fintype.card σ + 1) => M.eval (List.replicate (3 * (k : ℕ) + 2) Letter.a)) (by simp)
  have hij' : (i : ℕ) ≠ (j : ℕ) := fun h => hij (Fin.ext h)
  rcases Nat.lt_or_gt_of_ne hij' with h | h
  · exact key i j h heq
  · exact key j i h heq.symm

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
