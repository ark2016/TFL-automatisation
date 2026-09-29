import Lean.Replay
import Lean.Elab.Command
import TflLean
import Mathlib.Computability.ContextFreeGrammar
import Langlib.Classes.ContextFree.Pumping.Pumping
import Langlib.Classes.ContextFree.Basics.Ogden

inductive Letter
  | a | b
  deriving DecidableEq, Repr

def L : Language Letter := {x : List Letter | ∃ p_w1 p_m p_w2 p_w3 : List Letter, x = p_w1 ++ p_m ++ p_w2 ++ p_w3 ∧ (∀ ch ∈ p_m, ch = Letter.b) ∧ (p_m.length = 1) ∧ (p_w1.length = p_w2.length) ∧ (p_w1.length = p_w3.length) ∧ ((p_w2.length) % 2 = 0) ∧ (¬ ([Letter.a, Letter.a] <:+: p_w2)) ∧ (¬ ([Letter.b, Letter.b] <:+: p_w2)) ∧ ((p_w2.length = 0) ∨ ([Letter.a] <+: p_w2))}

theorem tfl_main : ¬ L.IsContextFree := by
  intro h
  obtain ⟨p, hp⟩ := h.pumping
  -- (ab)^q never contains [c,c]; neither does b(ab)^q.
  have alt : ∀ q : ℕ, ∀ s t : List Letter, ∀ c : Letter,
      s ++ [c, c] ++ t ≠ (List.replicate q [Letter.a, Letter.b]).flatten ∧
      s ++ [c, c] ++ t ≠ Letter.b :: (List.replicate q [Letter.a, Letter.b]).flatten := by
    intro q
    induction q with
    | zero =>
      intro s t c
      constructor
      · intro hst
        have h' := congrArg List.length hst
        simp only [List.length_append, List.length_cons, List.length_nil, List.replicate_zero, List.flatten_nil] at h'
        omega
      · intro hst
        have h' := congrArg List.length hst
        simp only [List.length_append, List.length_cons, List.length_nil, List.replicate_zero, List.flatten_nil] at h'
        omega
    | succ q ih =>
      intro s t c
      have e : (List.replicate (q + 1) [Letter.a, Letter.b]).flatten = Letter.a :: Letter.b :: (List.replicate q [Letter.a, Letter.b]).flatten := by
        rw [List.replicate_succ, List.flatten_cons, List.cons_append, List.cons_append, List.nil_append]
      rw [e]
      constructor
      · intro hst
        cases s with
        | nil =>
          simp only [List.nil_append, List.cons_append] at hst
          obtain ⟨h1, h2⟩ := List.cons.inj hst
          obtain ⟨h3, -⟩ := List.cons.inj h2
          subst h1
          cases h3
        | cons x s' =>
          simp only [List.cons_append] at hst
          exact (ih s' t c).2 (List.cons.inj hst).2
      · intro hst
        cases s with
        | nil =>
          simp only [List.nil_append, List.cons_append] at hst
          obtain ⟨h1, h2⟩ := List.cons.inj hst
          obtain ⟨h3, -⟩ := List.cons.inj h2
          subst h1
          cases h3
        | cons x s' =>
          cases s' with
          | nil =>
            simp only [List.nil_append, List.cons_append] at hst
            obtain ⟨-, h2⟩ := List.cons.inj hst
            obtain ⟨h3, h4⟩ := List.cons.inj h2
            obtain ⟨h5, -⟩ := List.cons.inj h4
            subst h3
            cases h5
          | cons y s'' =>
            simp only [List.cons_append] at hst
            exact (ih s'' t c).2 (List.cons.inj (List.cons.inj hst).2).2
  have hrep : ∀ i j : ℕ, List.replicate (i + j) Letter.a = List.replicate i Letter.a ++ List.replicate j Letter.a := by
    intro i j
    induction i with
    | zero => simp
    | succ i ih => rw [Nat.add_right_comm, List.replicate_succ, List.replicate_succ, ih, List.cons_append]
  have hlenM : ∀ q : ℕ, (List.replicate q [Letter.a, Letter.b]).flatten.length = 2 * q := by
    intro q
    induction q with
    | zero => simp
    | succ q ih =>
      rw [List.replicate_succ, List.flatten_cons, List.length_append, ih]
      simp only [List.length_cons, List.length_nil]
      omega
  have hstart : ∀ q : ℕ, (List.replicate q [Letter.a, Letter.b]).flatten.length = 0 ∨ [Letter.a] <+: (List.replicate q [Letter.a, Letter.b]).flatten := by
    intro q
    cases q with
    | zero => left; simp
    | succ q =>
      right
      exact ⟨Letter.b :: (List.replicate q [Letter.a, Letter.b]).flatten, by simp [List.replicate_succ]⟩
  obtain ⟨M, hM⟩ : ∃ M : List Letter, M = (List.replicate p [Letter.a, Letter.b]).flatten := ⟨_, rfl⟩
  have hMlen : M.length = 2 * p := by rw [hM]; exact hlenM p
  have hMaa : ¬ [Letter.a, Letter.a] <:+: M := by
    rintro ⟨s, t, hst⟩
    rw [hM] at hst
    exact (alt p s t Letter.a).1 hst
  have hMbb : ¬ [Letter.b, Letter.b] <:+: M := by
    rintro ⟨s, t, hst⟩
    rw [hM] at hst
    exact (alt p s t Letter.b).1 hst
  have hMst : M.length = 0 ∨ [Letter.a] <+: M := by rw [hM]; exact hstart p
  -- Witness W = a^{2p} b (ab)^p a^{2p}.
  obtain ⟨W, hW⟩ : ∃ W : List Letter, W = List.replicate (2 * p) Letter.a ++ [Letter.b] ++ M ++ List.replicate (2 * p) Letter.a := ⟨_, rfl⟩
  have hWl : W.length = 6 * p + 1 := by
    rw [hW]
    simp only [List.length_append, List.length_replicate, List.length_cons, List.length_nil, hMlen]
    omega
  have hWL : W ∈ L := by
    rw [hW]
    exact ⟨List.replicate (2 * p) Letter.a, [Letter.b], M, List.replicate (2 * p) Letter.a, rfl, by simp, rfl, by rw [List.length_replicate, hMlen], rfl, by rw [hMlen]; omega, hMaa, hMbb, hMst⟩
  have hWlen : W.length ≥ p := by omega
  obtain ⟨u, v, x, y, z, hsplit, hvy, hvxy, hpump⟩ := hp W hWL hWlen
  obtain ⟨w1, m, w2, w3, hw, hm, hml, h12, h13, hev, hnaa, -, -⟩ := hpump 0
  simp only [nTimes, List.replicate_zero, List.flatten_nil, List.append_nil] at hw
  have L1 := congrArg List.length hsplit
  simp only [List.length_append, hWl] at L1 hvy hvxy
  have L2 := congrArg List.length hw
  simp only [List.length_append] at L2
  rcases Nat.lt_or_ge u.length (w1.length + 1) with hlt | hge
  · -- u is short: the last |w1|+2 letters of u x z are a's, so w2 ends with aa.
    obtain ⟨w2a, w2b, hw2, hw2b⟩ : ∃ w2a w2b : List Letter, w2 = w2a ++ w2b ∧ w2b.length = 2 :=
      ⟨List.take (w1.length - 2) w2, List.drop (w1.length - 2) w2, by rw [List.take_append_drop], by rw [List.length_drop]; omega⟩
    obtain ⟨z1, z2, hz, hz2⟩ : ∃ z1 z2 : List Letter, z = z1 ++ z2 ∧ z2.length = w1.length + 2 :=
      ⟨List.take (z.length - (w1.length + 2)) z, List.drop (z.length - (w1.length + 2)) z, by rw [List.take_append_drop], by rw [List.length_drop]; omega⟩
    have hN2 : w1.length + 2 ≤ 2 * p := by omega
    have hA : List.replicate (2 * p) Letter.a = List.replicate (2 * p - (w1.length + 2)) Letter.a ++ List.replicate (w1.length + 2) Letter.a := by
      rw [← hrep, Nat.sub_add_cancel hN2]
    rw [hz, hw2] at hw
    have E1 : (u ++ x ++ z1) ++ z2 = (w1 ++ m ++ w2a) ++ (w2b ++ w3) := by
      simp only [List.append_assoc] at hw ⊢
      exact hw
    have r1 := (List.append_inj' E1 (by simp only [List.length_append]; omega)).2
    have E2 : (u ++ v ++ x ++ y ++ z1) ++ z2 = (List.replicate (2 * p) Letter.a ++ [Letter.b] ++ M ++ List.replicate (2 * p - (w1.length + 2)) Letter.a) ++ List.replicate (w1.length + 2) Letter.a := by
      have hWW : u ++ v ++ x ++ y ++ z = List.replicate (2 * p) Letter.a ++ [Letter.b] ++ M ++ List.replicate (2 * p) Letter.a := by
        rw [← hsplit, hW]
      rw [hz] at hWW
      simp only [List.append_assoc] at hWW ⊢
      rw [hWW, ← hA]
    have r2 := (List.append_inj' E2 (by rw [hz2, List.length_replicate])).2
    have h22 : List.replicate (w1.length + 2) Letter.a = List.replicate 2 Letter.a ++ List.replicate w1.length Letter.a := by
      rw [show w1.length + 2 = 2 + w1.length by omega, hrep 2 w1.length]
    have r3 : w2b ++ w3 = List.replicate 2 Letter.a ++ List.replicate w1.length Letter.a := r1.symm.trans (r2.trans h22)
    have r4 := (List.append_inj r3 (by rw [hw2b, List.length_replicate])).1
    have r5 : w2b = [Letter.a, Letter.a] := r4.trans rfl
    exact hnaa ⟨w2a, [], by rw [List.append_nil, hw2, r5]⟩
  · -- u is long: the first |w1|+1 letters of u x z are a's, so the middle letter is a.
    have t1 : List.take (w1.length + 1) W = List.take (w1.length + 1) u := by
      rw [hsplit]
      simp only [List.append_assoc]
      first
        | exact List.take_append_of_le_length hge
        | simp [List.take_append, Nat.sub_eq_zero_of_le hge]
    have t2 : List.take (w1.length + 1) (u ++ x ++ z) = List.take (w1.length + 1) u := by
      simp only [List.append_assoc]
      first
        | exact List.take_append_of_le_length hge
        | simp [List.take_append, Nat.sub_eq_zero_of_le hge]
    have t3 : List.take (w1.length + 1) (w1 ++ m ++ w2 ++ w3) = w1 ++ m := by
      rw [show w1 ++ m ++ w2 ++ w3 = (w1 ++ m) ++ (w2 ++ w3) by simp only [List.append_assoc], show w1.length + 1 = (w1 ++ m).length by simp [hml], List.take_left]
    have hle2 : w1.length + 1 ≤ (List.replicate (2 * p) Letter.a).length := by
      rw [List.length_replicate]; omega
    have hle3 : w1.length + 1 ≤ 2 * p := by omega
    have t4 : List.take (w1.length + 1) W = List.replicate (w1.length + 1) Letter.a := by
      rw [hW]
      simp only [List.append_assoc]
      first
        | rw [List.take_append_of_le_length hle2, List.take_replicate, Nat.min_eq_left hle3]
        | simp [List.take_append, Nat.sub_eq_zero_of_le hle2, List.take_replicate, Nat.min_eq_left hle3]
    have key : w1 ++ m = List.replicate (w1.length + 1) Letter.a :=
      t3.symm.trans ((congrArg (List.take (w1.length + 1)) hw.symm).trans (t2.trans (t1.symm.trans t4)))
    obtain ⟨c, hc⟩ : ∃ c, c ∈ m := by
      cases m with
      | nil => simp at hml
      | cons c r => exact ⟨c, by simp⟩
    have hcb := hm c hc
    have hmem : c ∈ List.replicate (w1.length + 1) Letter.a := by
      rw [← key]; simp [hc]
    have hca := List.eq_of_mem_replicate hmem
    rw [hca] at hcb
    cases hcb

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
