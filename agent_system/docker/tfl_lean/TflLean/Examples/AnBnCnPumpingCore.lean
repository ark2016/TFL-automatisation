import TflLean

inductive Sym
  | a | b | c
  deriving DecidableEq, Fintype, Repr

def L : Language Sym := {w : List Sym | ∃ n : ℕ, w = List.replicate n Sym.a ++ List.replicate n Sym.b ++ List.replicate n Sym.c ∧ n ≥ 0}

theorem tfl_main : ¬ ∃ p : ℕ, ∀ w ∈ L, w.length ≥ p → ∃ u v x y z : List Sym,
    w = u ++ v ++ x ++ y ++ z ∧ (v ++ y).length > 0 ∧ (v ++ x ++ y).length ≤ p ∧
    ∀ i : ℕ, u ++ (List.replicate i v).flatten ++ x ++ (List.replicate i y).flatten ++ z ∈ L := by
  -- Refute the CF pumping property directly (Mathlib only).
  rintro ⟨p, hp⟩
  -- Pump the witness w = a^p b^p c^p.
  obtain ⟨u, v, x, y, z, hsplit, hvy, hvxy, hpump⟩ :=
    hp (List.replicate p Sym.a ++ List.replicate p Sym.b ++ List.replicate p Sym.c)
      ⟨p, rfl, Nat.zero_le _⟩ (by simp only [List.length_append, List.length_replicate]; omega)
  -- Pump down (i = 0): u x z = a^n b^n c^n for some n.
  obtain ⟨n, hn, -⟩ := hpump 0
  simp only [List.replicate_zero, List.flatten_nil, List.append_nil] at hn
  -- Every word over {a, b, c} has length #a + #b + #c.
  have hlen : ∀ l : List Sym, l.length = l.count Sym.a + l.count Sym.b + l.count Sym.c := by
    intro l
    induction l with
    | nil => rfl
    | cons s l ih => cases s <;> simp [List.count_cons, ih] <;> omega
  -- Letter counts before and after pumping down: v y holds equally many a's, b's
  -- and c's, and is non-empty, so it contains at least one a and one c.
  have ca := congrArg (List.count Sym.a) hsplit
  have cb := congrArg (List.count Sym.b) hsplit
  have cc := congrArg (List.count Sym.c) hsplit
  have ca0 := congrArg (List.count Sym.a) hn
  have cb0 := congrArg (List.count Sym.b) hn
  have cc0 := congrArg (List.count Sym.c) hn
  simp [List.count_replicate] at ca cb cc ca0 cb0 cc0
  have lv := hlen v
  have ly := hlen y
  simp only [List.length_append] at hvy hvxy
  -- Prefix counts of w: #a(take m w) = min m p, #c(take m w) = min (m - 2p) p.
  -- An a in v x y forces |u| < p, a c in it forces |u v x y| > 2p, so |v x y| > p.
  have ta : ∀ m, (List.take m (List.replicate p Sym.a ++ List.replicate p Sym.b ++
      List.replicate p Sym.c)).count Sym.a = min m p := by
    intro m; simp [List.take_append_eq_append_take, List.take_replicate, List.count_replicate]
  have tc : ∀ m, (List.take m (List.replicate p Sym.a ++ List.replicate p Sym.b ++
      List.replicate p Sym.c)).count Sym.c = min (m - p - p) p := by
    intro m; simp [List.take_append_eq_append_take, List.take_replicate, List.count_replicate]
  have hu : List.take u.length (List.replicate p Sym.a ++ List.replicate p Sym.b ++
      List.replicate p Sym.c) = u := by
    rw [hsplit]; simp
  have hus : List.take (u ++ v ++ x ++ y).length (List.replicate p Sym.a ++
      List.replicate p Sym.b ++ List.replicate p Sym.c) = u ++ v ++ x ++ y := by
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

#print axioms tfl_main
