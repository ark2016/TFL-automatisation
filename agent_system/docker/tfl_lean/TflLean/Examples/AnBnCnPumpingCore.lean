import Lean.Replay
import Lean.Elab.Command
import TflLean

inductive Letter
  | a | b | c
  deriving DecidableEq, Repr

def L : Language Letter := {w : List Letter | ∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ++ List.replicate n Letter.c ∧ n ≥ 0}

theorem tfl_main : ¬ ∃ p : ℕ, ∀ w ∈ L, w.length ≥ p → ∃ u v x y z : List Letter,
    w = u ++ v ++ x ++ y ++ z ∧ (v ++ y).length > 0 ∧ (v ++ x ++ y).length ≤ p ∧
    ∀ i : ℕ, u ++ (List.replicate i v).flatten ++ x ++ (List.replicate i y).flatten ++ z ∈ L := by
  -- Refute the CF pumping property directly (Mathlib only).
  rintro ⟨p, hp⟩
  -- Pump the witness w = a^p b^p c^p.
  obtain ⟨u, v, x, y, z, hsplit, hvy, hvxy, hpump⟩ :=
    hp (List.replicate p Letter.a ++ List.replicate p Letter.b ++ List.replicate p Letter.c)
      ⟨p, rfl, Nat.zero_le _⟩ (by simp only [List.length_append, List.length_replicate]; omega)
  -- Pump down (i = 0): u x z = a^n b^n c^n for some n.
  obtain ⟨n, hn, -⟩ := hpump 0
  simp only [List.replicate_zero, List.flatten_nil, List.append_nil] at hn
  -- Every word over {a, b, c} has length #a + #b + #c.
  have hlen : ∀ l : List Letter, l.length = l.count Letter.a + l.count Letter.b + l.count Letter.c := by
    intro l
    induction l with
    | nil => rfl
    | cons s l ih => cases s <;> simp [List.count_cons, ih] <;> omega
  -- Letter counts before and after pumping down: v y holds equally many a's, b's
  -- and c's, and is non-empty, so it contains at least one a and one c.
  have ca := congrArg (List.count Letter.a) hsplit
  have cb := congrArg (List.count Letter.b) hsplit
  have cc := congrArg (List.count Letter.c) hsplit
  have ca0 := congrArg (List.count Letter.a) hn
  have cb0 := congrArg (List.count Letter.b) hn
  have cc0 := congrArg (List.count Letter.c) hn
  simp [List.count_replicate] at ca cb cc ca0 cb0 cc0
  have lv := hlen v
  have ly := hlen y
  simp only [List.length_append] at hvy hvxy
  -- Prefix counts of w: #a(take m w) = min m p, #c(take m w) = min (m - 2p) p.
  -- An a in v x y forces |u| < p, a c in it forces |u v x y| > 2p, so |v x y| > p.
  have ta : ∀ m, (List.take m (List.replicate p Letter.a ++ List.replicate p Letter.b ++
      List.replicate p Letter.c)).count Letter.a = min m p := by
    intro m; simp [List.take_append, List.take_replicate, List.count_replicate]
  have tc : ∀ m, (List.take m (List.replicate p Letter.a ++ List.replicate p Letter.b ++
      List.replicate p Letter.c)).count Letter.c = min (m - p - p) p := by
    intro m; simp [List.take_append, List.take_replicate, List.count_replicate]
  have hu : List.take u.length (List.replicate p Letter.a ++ List.replicate p Letter.b ++
      List.replicate p Letter.c) = u := by
    rw [hsplit]; simp
  have hus : List.take (u ++ v ++ x ++ y).length (List.replicate p Letter.a ++
      List.replicate p Letter.b ++ List.replicate p Letter.c) = u ++ v ++ x ++ y := by
    rw [hsplit, List.take_left]
  have h1 := ta u.length
  have h2 := ta (u ++ v ++ x ++ y).length
  have h3 := tc u.length
  have h4 := tc (u ++ v ++ x ++ y).length
  rw [hu] at h1 h3
  rw [hus] at h2 h4
  simp only [List.count_append, List.length_append] at h2 h4
  clear hp hpump hsplit hn hu hus ta tc hlen
  omega

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
