import TflLean

inductive Sym
  | a | b
  deriving DecidableEq, Fintype, Repr

def L : Language Sym := {w : List Sym | ∃ n : ℕ, w = List.replicate n Sym.a ++ List.replicate n Sym.b ∧ n ≥ 0}

theorem tfl_main : ¬ L.IsRegular := by
  -- Myhill–Nerode by pigeonhole: of the card σ + 1 prefixes a^0 … a^(card σ),
  -- two (a^i, a^j with i ≠ j) end in the same DFA state; a^i b^i is accepted,
  -- hence so is a^j b^i, which is not in L (count the a's and the b's).
  rintro ⟨σ, _, M, hM⟩
  obtain ⟨i, j, hij, heq⟩ := Fintype.exists_ne_map_eq_of_card_lt
    (fun k : Fin (Fintype.card σ + 1) => M.eval (List.replicate k Sym.a)) (by simp)
  have hacc : List.replicate (i : ℕ) Sym.a ++ List.replicate i Sym.b ∈ M.accepts := by
    rw [hM]; exact ⟨i, rfl, Nat.zero_le _⟩
  have hacc' : List.replicate (j : ℕ) Sym.a ++ List.replicate i Sym.b ∈ M.accepts := by
    rw [DFA.mem_accepts] at hacc ⊢
    simp only [DFA.eval, DFA.evalFrom_of_append] at hacc heq ⊢
    rw [← heq]; exact hacc
  rw [hM] at hacc'
  obtain ⟨n, hn, -⟩ := hacc'
  have h1 := congrArg (List.count Sym.a) hn
  have h2 := congrArg (List.count Sym.b) hn
  simp [List.count_replicate] at h1 h2
  exact hij (Fin.ext (by omega))

#print axioms tfl_main
