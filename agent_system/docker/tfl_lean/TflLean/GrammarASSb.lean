import Mathlib.Computability.DFA
import Mathlib.Computability.ContextFreeGrammar
import Langlib.Classes.DeterministicContextFree.Definition
import Langlib.Classes.DeterministicContextFree.Closure.Bijection
import Langlib.Classes.DeterministicContextFree.Closure.Complement
import Langlib.Classes.DeterministicContextFree.Closure.IntersectionRegular
import TflLean.Lemmas

/-!
# TflLean.GrammarASSb — the language of `S → aSSb | ba | Ab`, `A → aAb | a` is DCF

Task `dcfl_exam_04` (`dcfl_system/examples/task_grammar_aSSb.json`), proof of
docs/THEORY.md §1.10 ("Компактный ДМПА через лес Дика").

* `LangA a b`, `LangS a b` — the languages of `A` and `S` as inductive predicates
  over any alphabet with two letters `a`, `b`.
* `Sem`, `den_of_mem_language` — soundness of a context-free grammar by
  closure of a family of predicates under its rules (generic).
* `RT.mk`, `RT.run`, `RT.accepts_eq` — an ε-free DPDA given by a step
  function accepts exactly the words on which the functional run ends in a
  final state (generic).
* `isDCF_LangS` — `{w | LangS a b w}` is deterministic context-free when `a ≠ b`.

Proof of `isDCF_LangS`. `L' = L \ {ba}` (`InL1`) consists of Dyck words
(`a` opens, `b` closes). A forest `Fo` is a list of trees; `flat` is its
word. `al F : St` is a ten-flag abstraction of a forest (empty, the leaf
`ab`, a path `aⁿbⁿ`, in `L'`, in `L'L'`, …); the flags of `F ++ [tree C]`
are a function `upd` of the flags of `F` and of `C`. `sound` — a set flag
implies the property of `flat F`; `complete` — every word of `L'` is `flat`
of a forest whose `grp` flag is set. The DPDA keeps one stack cell (the
parent's flags) per open tree: push on `a`, `upd` on `b`; `run_flat` and
`decode` relate its runs to forests. The word `ba` is added by
`(Lᶜ ⊓ Rᶜ)ᶜ` with a regular `R`. Finally the alphabet `Bool` is moved onto
`a`, `b` by `DCF_of_map_injective_DCF`.
-/

namespace TflLean.ASSb

/-! ## Real-time DPDA as a step function -/

namespace RT
variable {Q T S : Type} [Fintype Q] [Fintype T] [Fintype S]

/-- Real-time (ε-free) DPDA from a partial step function. -/
def mk (q0 : Q) (z0 : S) (F : Set Q) (δ : Q → T → S → Option (Q × List S)) : DPDA Q T S where
  initial_state := q0
  start_symbol := z0
  final_states := F
  transition := δ
  epsilon_transition := fun _ _ => none
  no_mixed := by intro q Z h; exact absurd rfl h

/-- Functional run of a real-time DPDA. -/
def run (δ : Q → T → S → Option (Q × List S)) : List T → Q → List S → Option (Q × List S)
  | [], q, γ => some (q, γ)
  | a :: w, q, γ =>
    match γ with
    | [] => none
    | Z :: γ' => (δ q a Z).bind fun pβ => run δ w pβ.1 (pβ.2 ++ γ')

variable {q0 : Q} {z0 : S} {F : Set Q} {δ : Q → T → S → Option (Q × List S)}

theorem mem_tf {q : Q} {a : T} {Z : S} {x : Q × List S} :
    x ∈ (mk q0 z0 F δ).toPDA.transition_fun q a Z ↔ δ q a Z = some x := by
  show x ∈ (match δ q a Z with | some p => ({p} : Set _) | none => ∅) ↔ _
  cases δ q a Z <;> simp [eq_comm]

theorem not_mem_tf' {q : Q} {Z : S} {x : Q × List S} :
    x ∉ (mk q0 z0 F δ).toPDA.transition_fun' q Z := by
  show x ∉ (∅ : Set _)
  simp

theorem reaches_iff (w : List T) : ∀ (q : Q) (γ : List S) (q' : Q) (γ' : List S),
    PDA.Reaches (⟨q, w, γ⟩ : PDA.conf (mk q0 z0 F δ).toPDA) ⟨q', [], γ'⟩ ↔
      run δ w q γ = some (q', γ') := by
  induction w with
  | nil =>
    intro q γ q' γ'
    constructor
    · intro h
      rcases Relation.ReflTransGen.cases_head h with h | ⟨c, hc, -⟩
      · injection h with h1 h2 h3
        simp [run, h1, h3]
      · exfalso
        cases γ with
        | nil => exact hc
        | cons Z α =>
          obtain ⟨p, β, hm, -⟩ := hc
          exact not_mem_tf' hm
    · intro h
      simp only [run, Option.some.injEq, Prod.mk.injEq] at h
      obtain ⟨rfl, rfl⟩ := h
      exact Relation.ReflTransGen.refl
  | cons a w ih =>
    intro q γ q' γ'
    cases γ with
    | nil =>
      constructor
      · intro h
        rcases Relation.ReflTransGen.cases_head h with h | ⟨c, hc, -⟩
        · injection h with h1 h2 h3
          cases h2
        · exact absurd hc (fun h => h)
      · intro h
        simp [run] at h
    | cons Z α =>
      constructor
      · intro h
        rcases Relation.ReflTransGen.cases_head h with h | ⟨c, hc, hr⟩
        · injection h with h1 h2 h3
          cases h2
        · rcases hc with ⟨p, β, hm, rfl⟩ | ⟨p, β, hm, -⟩
          · rw [mem_tf] at hm
            simp only [run, hm, Option.bind_some]
            exact (ih _ _ _ _).1 hr
          · exact absurd hm not_mem_tf'
      · intro h
        simp only [run] at h
        cases hδ : δ q a Z with
        | none => simp [hδ] at h
        | some x =>
          obtain ⟨p, β⟩ := x
          simp only [hδ, Option.bind_some] at h
          exact Relation.ReflTransGen.head (Or.inl ⟨p, β, mem_tf.2 hδ, rfl⟩) ((ih _ _ _ _).2 h)

theorem accepts_eq : (mk q0 z0 F δ).acceptsByFinalState =
    {w | ∃ q γ, run δ w q0 [z0] = some (q, γ) ∧ q ∈ F} := by
  ext w
  constructor
  · rintro ⟨q, hq, γ, h⟩
    exact ⟨q, γ, (reaches_iff w _ _ _ _).1 h, hq⟩
  · rintro ⟨q, γ, h, hq⟩
    exact ⟨q, hq, γ, (reaches_iff w _ _ _ _).2 h⟩

end RT

/-! ## The grammar's languages as inductive predicates -/

/-- `A → aAb | a`. -/
inductive LangA {T : Type} (a b : T) : List T → Prop
  | base : LangA a b [a]
  | step {x} : LangA a b x → LangA a b (a :: (x ++ [b]))

/-- `S → aSSb | ba | Ab`. -/
inductive LangS {T : Type} (a b : T) : List T → Prop
  | ba : LangS a b [b, a]
  | ofA {x} : LangA a b x → LangS a b (x ++ [b])
  | node {x y} : LangS a b x → LangS a b y → LangS a b (a :: (x ++ y ++ [b]))

/-- The letter map `Bool → T`, `false ↦ a`, `true ↦ b`. -/
def bl {T : Type} (a b : T) : Bool → T
  | false => a
  | true => b

theorem bl_injective {T : Type} {a b : T} (hab : a ≠ b) : Function.Injective (bl a b) := by
  intro i j h
  cases i <;> cases j
  · rfl
  · exact absurd h hab
  · exact absurd h.symm hab
  · rfl

theorem LangA_map {T : Type} {a b : T} {x : List Bool} (h : LangA false true x) :
    LangA a b (x.map (bl a b)) := by
  induction h with
  | base => exact LangA.base
  | step _ ih =>
    simp only [List.map_cons, List.map_append, List.map_nil]
    exact LangA.step ih

theorem LangS_map {T : Type} {a b : T} {x : List Bool} (h : LangS false true x) :
    LangS a b (x.map (bl a b)) := by
  induction h with
  | ba => exact LangS.ba
  | ofA hx =>
    rw [List.map_append]
    exact LangS.ofA (LangA_map hx)
  | node _ _ ihx ihy =>
    simp only [List.map_cons, List.map_append, List.map_nil]
    exact LangS.node ihx ihy

theorem LangA_unmap {T : Type} {a b : T} {w : List T} (h : LangA a b w) :
    ∃ x, LangA false true x ∧ x.map (bl a b) = w := by
  induction h with
  | base => exact ⟨[false], LangA.base, rfl⟩
  | step _ ih =>
    obtain ⟨x, hx, rfl⟩ := ih
    exact ⟨false :: (x ++ [true]), LangA.step hx, by simp [bl]⟩

theorem LangS_unmap {T : Type} {a b : T} {w : List T} (h : LangS a b w) :
    ∃ x, LangS false true x ∧ x.map (bl a b) = w := by
  induction h with
  | ba => exact ⟨[true, false], LangS.ba, rfl⟩
  | ofA hx =>
    obtain ⟨x, hx', rfl⟩ := LangA_unmap hx
    exact ⟨x ++ [true], LangS.ofA hx', by simp [bl]⟩
  | node _ _ ihx ihy =>
    obtain ⟨x, hx, rfl⟩ := ihx
    obtain ⟨y, hy, rfl⟩ := ihy
    exact ⟨false :: (x ++ y ++ [true]), LangS.node hx hy, by simp [bl]⟩

/-! ## Soundness of a grammar by closure under its rules -/

/-- `Sem den u w`: the word `w` is obtained from the sentential form `u` by
replacing every nonterminal `n` with a word satisfying `den n`. -/
inductive Sem {T N : Type} (den : N → List T → Prop) : List (Symbol T N) → List T → Prop
  | nil : Sem den [] []
  | term (t : T) {u w} : Sem den u w → Sem den (Symbol.terminal t :: u) (t :: w)
  | nt {n x u w} : den n x → Sem den u w → Sem den (Symbol.nonterminal n :: u) (x ++ w)

theorem Sem.append {T N : Type} {den : N → List T → Prop} {u v : List (Symbol T N)}
    {w1 w2 : List T} (h1 : Sem den u w1) (h2 : Sem den v w2) :
    Sem den (u ++ v) (w1 ++ w2) := by
  induction h1 with
  | nil => simpa using h2
  | term t _ ih => exact Sem.term t ih
  | nt hx _ ih => rw [List.append_assoc]; exact Sem.nt hx ih

theorem Sem.split {T N : Type} {den : N → List T → Prop} :
    ∀ (u : List (Symbol T N)) {v : List (Symbol T N)} {w : List T}, Sem den (u ++ v) w →
      ∃ w1 w2, w = w1 ++ w2 ∧ Sem den u w1 ∧ Sem den v w2
  | [], v, w, h => ⟨[], w, rfl, Sem.nil, h⟩
  | s :: u, v, w, h => by
    cases h with
    | term t h' =>
      obtain ⟨w1, w2, rfl, h1, h2⟩ := Sem.split u h'
      exact ⟨t :: w1, w2, rfl, Sem.term t h1, h2⟩
    | nt hx h' =>
      obtain ⟨w1, w2, rfl, h1, h2⟩ := Sem.split u h'
      exact ⟨_ ++ w1, w2, by simp, Sem.nt hx h1, h2⟩

theorem Sem.map_terminal {T N : Type} {den : N → List T → Prop} (w : List T) :
    Sem den (w.map Symbol.terminal) w := by
  induction w with
  | nil => exact Sem.nil
  | cons t w ih => exact Sem.term t ih

/-- If the predicates `den n` are closed under the rules of `g`, every word
of `g.language` satisfies `den g.initial`. -/
theorem den_of_mem_language {T : Type} (g : ContextFreeGrammar T)
    (den : g.NT → List T → Prop)
    (hrule : ∀ r ∈ g.rules, ∀ w, Sem den r.output w → den r.input w)
    {w : List T} (h : w ∈ g.language) : den g.initial w := by
  have step : ∀ {u v : List (Symbol T g.NT)} {w : List T},
      g.Produces u v → Sem den v w → Sem den u w := by
    intro u v w hp hv
    obtain ⟨r, hr, hrw⟩ := hp
    obtain ⟨p, q, rfl, rfl⟩ := hrw.exists_parts
    obtain ⟨w12, w3, rfl, h12, h3⟩ := Sem.split _ hv
    obtain ⟨w1, w2, rfl, h1, h2⟩ := Sem.split _ h12
    have hn : Sem den [Symbol.nonterminal r.input] w2 := by
      simpa using Sem.nt (hrule r hr w2 h2) Sem.nil
    exact (h1.append hn).append h3
  have all : ∀ {u v : List (Symbol T g.NT)} {w : List T},
      g.Derives u v → Sem den v w → Sem den u w := by
    intro u v w hd hv
    induction hd using Relation.ReflTransGen.head_induction_on with
    | refl => exact hv
    | head hp _ ih => exact step hp ih
  have h' : Sem den [Symbol.nonterminal g.initial] w := all h (Sem.map_terminal w)
  clear h
  cases h' with
  | nt hx h2 => cases h2; simpa using hx

/-! ## L' = L \ {ba} as an inductive set -/

inductive InP : List Bool → Prop
  | base : InP [false, true]
  | step {x} : InP x → InP (false :: (x ++ [true]))

inductive InL1 : List Bool → Prop
  | path {x} : InP x → InL1 x
  | nn {x y} : InL1 x → InL1 y → InL1 (false :: (x ++ y ++ [true]))
  | ln {y} : InL1 y → InL1 (false :: true :: false :: (y ++ [true]))
  | nl {x} : InL1 x → InL1 (false :: (x ++ [true, false, true]))
  | lll : InL1 [false, true, false, true, false, true]

theorem InP_of_InA {x} (h : LangA false true x) : InP (x ++ [true]) := by
  induction h with
  | base => exact InP.base
  | step _ ih => exact InP.step ih

theorem InA_of_InP {x} (h : InP x) : ∃ y, LangA false true y ∧ x = y ++ [true] := by
  induction h with
  | base => exact ⟨[false], LangA.base, rfl⟩
  | step _ ih =>
    obtain ⟨y, hy, rfl⟩ := ih
    exact ⟨false :: (y ++ [true]), LangA.step hy, rfl⟩

theorem InL_iff (w : List Bool) : LangS false true w ↔ (w = [true, false] ∨ InL1 w) := by
  constructor
  · intro h
    induction h with
    | ba => exact Or.inl rfl
    | ofA hx => exact Or.inr (InL1.path (InP_of_InA hx))
    | node _ _ ihx ihy =>
      right
      rcases ihx with rfl | ihx <;> rcases ihy with rfl | ihy
      · exact InL1.lll
      · exact InL1.ln ihy
      · simpa using InL1.nl ihx
      · exact InL1.nn ihx ihy
  · rintro (rfl | h)
    · exact LangS.ba
    · induction h with
      | path hp =>
        obtain ⟨y, hy, rfl⟩ := InA_of_InP hp
        exact LangS.ofA hy
      | nn _ _ ihx ihy => exact LangS.node ihx ihy
      | ln _ ih => exact LangS.node LangS.ba ih
      | nl _ ih => simpa using LangS.node ih LangS.ba
      | lll => exact LangS.node LangS.ba LangS.ba

/-! ## Dyck forests and their finite abstraction -/

inductive Fo
  | nil : Fo
  | snoc : Fo → Fo → Fo

def flat : Fo → List Bool
  | .nil => []
  | .snoc F C => flat F ++ false :: (flat C ++ [true])

structure St where
  e : Bool
  lf : Bool
  pth : Bool
  grp : Bool
  gg : Bool
  ins1 : Bool
  ll : Bool
  glf : Bool
  gins : Bool
  gll : Bool
  deriving DecidableEq

noncomputable instance : Fintype St :=
  Fintype.ofInjective
    (fun s : St => (s.e, s.lf, s.pth, s.grp, s.gg, s.ins1, s.ll, s.glf, s.gins, s.gll))
    (by intro x y h; cases x; cases y; simp_all)

def St.init : St := ⟨true, false, false, false, false, false, false, false, false, false⟩

def St.wd (c : St) : Bool := c.e || c.pth || c.gg

def upd (p c : St) : St :=
  { e := false
    lf := p.e && c.e
    pth := p.e && (c.e || c.pth)
    grp := (p.e && c.wd) || (p.lf && c.grp) || (p.ins1 && c.e) || (p.ll && c.e)
    gg := (p.grp && c.wd) || (p.glf && c.grp) || (p.gins && c.e) || (p.gll && c.e)
    ins1 := p.e && c.grp
    ll := p.lf && c.e
    glf := p.grp && c.e
    gins := p.grp && c.grp
    gll := p.glf && c.e }

def ext (q : St) : Fo → St
  | .nil => q
  | .snoc F C => upd (ext q F) (ext St.init C)

def al (F : Fo) : St := ext St.init F

theorem al_nil : al Fo.nil = St.init := rfl
theorem al_snoc (F C : Fo) : al (Fo.snoc F C) = upd (al F) (al C) := rfl

theorem sound (F : Fo) :
    ((al F).e = true → flat F = []) ∧
    ((al F).lf = true → flat F = [false, true]) ∧
    ((al F).pth = true → InP (flat F)) ∧
    ((al F).grp = true → InL1 (flat F)) ∧
    ((al F).gg = true → ∃ x y, InL1 x ∧ InL1 y ∧ flat F = x ++ y) ∧
    ((al F).ins1 = true → ∃ x, InL1 x ∧ flat F = false :: (x ++ [true])) ∧
    ((al F).ll = true → flat F = [false, true, false, true]) ∧
    ((al F).glf = true → ∃ x, InL1 x ∧ flat F = x ++ [false, true]) ∧
    ((al F).gins = true → ∃ x y, InL1 x ∧ InL1 y ∧ flat F = x ++ false :: (y ++ [true])) ∧
    ((al F).gll = true → ∃ x, InL1 x ∧ flat F = x ++ [false, true, false, true]) := by
  induction F with
  | nil =>
    refine ⟨fun _ => rfl, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
    all_goals (intro h; simp [al_nil, St.init] at h)
  | snoc F C ihF ihC =>
    obtain ⟨fe, flf, -, fgrp, -, fins, fll, fglf, fgins, fgll⟩ := ihF
    obtain ⟨ce, -, cpth, cgrp, cgg, -, -, -, -, -⟩ := ihC
    have hwd : (al C).wd = true → InL1 (false :: (flat C ++ [true])) := by
      intro h
      simp only [St.wd, Bool.or_eq_true] at h
      rcases h with (h | h) | h
      · rw [ce h]; exact InL1.path InP.base
      · exact InL1.path (InP.step (cpth h))
      · obtain ⟨x, y, hx, hy, e⟩ := cgg h
        rw [e]; exact InL1.nn hx hy
    refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
    · intro h; simp [al_snoc, upd] at h
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      simp [flat, fe h.1, ce h.2]
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true, Bool.or_eq_true] at h
      obtain ⟨h1, h2⟩ := h
      simp only [flat, fe h1, List.nil_append]
      rcases h2 with h2 | h2
      · rw [ce h2]; exact InP.base
      · exact InP.step (cpth h2)
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true, Bool.or_eq_true] at h
      rcases h with ((⟨h1, h2⟩ | ⟨h1, h2⟩) | ⟨h1, h2⟩) | ⟨h1, h2⟩
      · simp only [flat, fe h1, List.nil_append]; exact hwd h2
      · simp only [flat, flf h1]; exact InL1.ln (cgrp h2)
      · obtain ⟨x, hx, e⟩ := fins h1
        simp only [flat, e, ce h2]
        simpa using InL1.nl hx
      · simp only [flat, fll h1, ce h2]; exact InL1.lll
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true, Bool.or_eq_true] at h
      rcases h with ((⟨h1, h2⟩ | ⟨h1, h2⟩) | ⟨h1, h2⟩) | ⟨h1, h2⟩
      · exact ⟨flat F, false :: (flat C ++ [true]), fgrp h1, hwd h2, rfl⟩
      · obtain ⟨x, hx, e⟩ := fglf h1
        exact ⟨x, _, hx, InL1.ln (cgrp h2), by simp [flat, e]⟩
      · obtain ⟨x, y, hx, hy, e⟩ := fgins h1
        exact ⟨x, _, hx, InL1.nl hy, by simp [flat, e, ce h2]⟩
      · obtain ⟨x, hx, e⟩ := fgll h1
        exact ⟨x, _, hx, InL1.lll, by simp [flat, e, ce h2]⟩
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      exact ⟨flat C, cgrp h.2, by simp [flat, fe h.1]⟩
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      simp [flat, flf h.1, ce h.2]
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      exact ⟨flat F, fgrp h.1, by simp [flat, ce h.2]⟩
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      exact ⟨flat F, flat C, fgrp h.1, cgrp h.2, rfl⟩
    · intro h
      simp only [al_snoc, upd, Bool.and_eq_true] at h
      obtain ⟨x, hx, e⟩ := fglf h.1
      exact ⟨x, hx, by simp [flat, e, ce h.2]⟩

theorem pathC {x} (h : InP x) :
    ∃ C, x = false :: (flat C ++ [true]) ∧ ((al C).e = true ∨ (al C).pth = true) := by
  induction h with
  | base => exact ⟨Fo.nil, rfl, Or.inl rfl⟩
  | step _ ih =>
    obtain ⟨C, rfl, hC⟩ := ih
    refine ⟨Fo.snoc Fo.nil C, by simp [flat], Or.inr ?_⟩
    rcases hC with h | h <;> simp [al_snoc, upd, al_nil, St.init, h]

theorem complete {y} (h : InL1 y) : ∀ F1 : Fo, ∃ F', flat F' = flat F1 ++ y ∧
    ((al F1).e = true → (al F').grp = true) ∧ ((al F1).grp = true → (al F').gg = true) := by
  induction h with
  | path hp =>
    intro F1
    obtain ⟨C, rfl, hC⟩ := pathC hp
    refine ⟨Fo.snoc F1 C, rfl, ?_, ?_⟩ <;> intro h <;> rcases hC with hC | hC <;>
      simp [al_snoc, upd, St.wd, h, hC]
  | nn _ _ ihx ihy =>
    intro F1
    obtain ⟨G1, e1, g1, -⟩ := ihx Fo.nil
    obtain ⟨G2, e2, -, g2⟩ := ihy G1
    have hg := g2 (g1 rfl)
    refine ⟨Fo.snoc F1 G2, by simp [flat, e2, e1], ?_, ?_⟩ <;> intro h <;>
      simp [al_snoc, upd, St.wd, h, hg]
  | ln _ ih =>
    intro F1
    obtain ⟨G, e, g, -⟩ := ih Fo.nil
    have hg := g rfl
    refine ⟨Fo.snoc (Fo.snoc F1 Fo.nil) G, by simp [flat, e], ?_, ?_⟩ <;> intro h <;>
      simp [al_snoc, upd, al_nil, St.init, St.wd, h, hg]
  | nl _ ih =>
    intro F1
    obtain ⟨G, e, g, -⟩ := ih Fo.nil
    have hg := g rfl
    refine ⟨Fo.snoc (Fo.snoc F1 G) Fo.nil, by simp [flat, e], ?_, ?_⟩ <;> intro h <;>
      simp [al_snoc, upd, al_nil, St.init, St.wd, h, hg]
  | lll =>
    intro F1
    refine ⟨Fo.snoc (Fo.snoc (Fo.snoc F1 Fo.nil) Fo.nil) Fo.nil, by simp [flat], ?_, ?_⟩ <;>
      intro h <;> simp [al_snoc, upd, al_nil, St.init, St.wd, h]

/-! ## The DPDA: one stack cell per open tree -/

abbrev Cell := Bool × St

def δ (q : Cell) (t : Bool) (Z : Cell) : Option (Cell × List Cell) :=
  match t with
  | false => some ((false, St.init), [q, Z])
  | true => if q.1 then none else some ((Z.1, upd Z.2 q.2), [])

theorem run_a (w : List Bool) (q Z : Cell) (γ : List Cell) :
    RT.run δ (false :: w) q (Z :: γ) = RT.run δ w (false, St.init) (q :: Z :: γ) := rfl

theorem run_b (w : List Bool) (c p : St) (r : Bool) (γ : List Cell) :
    RT.run δ (true :: w) (false, c) ((r, p) :: γ) = RT.run δ w (r, upd p c) γ := rfl

theorem run_flat (F : Fo) : ∀ (rest : List Bool) (r : Bool) (q : St) (Z : Cell) (γ : List Cell),
    RT.run δ (flat F ++ rest) (r, q) (Z :: γ) = RT.run δ rest (r, ext q F) (Z :: γ) := by
  induction F with
  | nil => intro rest r q Z γ; rfl
  | snoc F C ihF ihC =>
    intro rest r q Z γ
    have e : flat (Fo.snoc F C) ++ rest
        = flat F ++ (false :: (flat C ++ (true :: rest))) := by simp [flat]
    rw [e, ihF, run_a, ihC, run_b]
    rfl

def cells : List Fo → List Cell
  | [] => [(true, St.init)]
  | F :: l => (l.isEmpty, al F) :: cells l

def pre : List Fo → List Bool
  | [] => []
  | G :: l => pre l ++ flat G ++ [false]

theorem decode (w : List Bool) : ∀ (F : Fo) (l : List Fo) (q : Cell) (γ : List Cell),
    RT.run δ w (l.isEmpty, al F) (cells l) = some (q, γ) →
    ∃ F' l', q = (l'.isEmpty, al F') ∧ γ = cells l' ∧ pre l' ++ flat F' = pre l ++ flat F ++ w := by
  induction w with
  | nil =>
    intro F l q γ h
    simp only [RT.run, Option.some.injEq, Prod.mk.injEq] at h
    exact ⟨F, l, h.1.symm, h.2.symm, by simp⟩
  | cons t w ih =>
    intro F l q γ h
    cases t with
    | false =>
      have h' : RT.run δ w ((F :: l).isEmpty, al Fo.nil) (cells (F :: l)) = some (q, γ) := by
        cases l <;> exact h
      obtain ⟨F', l', h1, h2, h3⟩ := ih _ _ _ _ h'
      exact ⟨F', l', h1, h2, by rw [h3]; simp [pre, flat]⟩
    | true =>
      cases l with
      | nil => simp [RT.run, δ, cells] at h
      | cons G l =>
        have h' : RT.run δ w (l.isEmpty, al (Fo.snoc G F)) (cells l) = some (q, γ) := h
        obtain ⟨F', l', h1, h2, h3⟩ := ih _ _ _ _ h'
        exact ⟨F', l', h1, h2, by rw [h3]; simp [pre, flat]⟩

def L1 : Language Bool := {w | InL1 w}

theorem isDCF_L1 : is_DCF L1 := by
  refine ⟨Cell, Cell, inferInstance, inferInstance,
    RT.mk (true, St.init) (true, St.init) {q | q.1 = true ∧ q.2.grp = true} δ, ?_⟩
  rw [RT.accepts_eq]
  ext w
  constructor
  · rintro ⟨q, γ, h, hq⟩
    have hq' : q.1 = true ∧ q.2.grp = true := hq
    obtain ⟨F', l', rfl, -, h3⟩ := decode w Fo.nil [] q γ h
    cases l' with
    | cons G l => simp at hq'
    | nil =>
      have hw : w = flat F' := by simpa [pre, flat] using h3.symm
      rw [hw]
      show InL1 (flat F')
      exact (sound F').2.2.2.1 hq'.2
  · intro h
    have h : InL1 w := h
    obtain ⟨F', e, g, -⟩ := complete h Fo.nil
    have e' : w = flat F' := by simpa [flat] using e.symm
    refine ⟨(true, al F'), [(true, St.init)], ?_, rfl, g rfl⟩
    have h2 := run_flat F' [] true St.init (true, St.init) []
    rw [e']
    simpa [RT.run, al] using h2

/-! ## The word `ba` is a regular language -/

inductive D
  | s0 | s1 | s2 | dead
  deriving DecidableEq

instance : Fintype D where
  elems := {D.s0, D.s1, D.s2, D.dead}
  complete := by intro x; cases x <;> decide

def dstep : D → Bool → D
  | .s0, true => .s1
  | .s1, false => .s2
  | _, _ => .dead

def MD : DFA Bool D := ⟨dstep, D.s0, {D.s2}⟩

theorem foldl_dead (w : List Bool) : List.foldl dstep D.dead w = D.dead := by
  induction w with
  | nil => rfl
  | cons x w ih => cases x <;> simpa [dstep] using ih

theorem regBA : ({[true, false]} : Language Bool).IsRegular := by
  refine TflLean.isRegular_of_dfa MD (fun w => ?_)
  show w = [true, false] ↔ MD.evalFrom D.s0 w = D.s2
  rcases w with _ | ⟨x, _ | ⟨y, _ | ⟨z, w⟩⟩⟩
  · simp [DFA.evalFrom, MD]
  · cases x <;> simp [DFA.evalFrom, MD, dstep]
  · cases x <;> cases y <;> simp [DFA.evalFrom, MD, dstep]
  · cases x <;> cases y <;> cases z <;> simp [DFA.evalFrom, MD, dstep, foldl_dead]

/-- The language of `S → aSSb | ba | Ab`, `A → aAb | a` over `Bool`
(`false = a`, `true = b`). -/
def L0 : Language Bool := {w | LangS false true w}

theorem isDCF_L0 : is_DCF L0 := by
  have hL : L0 = L1 + {[true, false]} := by
    ext w
    rw [Language.mem_add]
    show LangS false true w ↔ (InL1 w ∨ w = [true, false])
    rw [InL_iff]
    exact or_comm
  rw [hL]
  have e : L1 + {[true, false]}
      = (L1ᶜ ⊓ ({[true, false]} : Language Bool)ᶜ)ᶜ := by
    ext w
    simp only [Language.add_def]
    change (InL1 w ∨ w = [true, false]) ↔ ¬ (¬ InL1 w ∧ ¬ w = [true, false])
    tauto
  rw [e]
  exact DCF_closedUnderComplement _
    (DCF_inter_regular _ _ (DCF_closedUnderComplement _ isDCF_L1) regBA.compl)

/-- The language of `S → aSSb | ba | Ab`, `A → aAb | a` is deterministic
context-free (docs/THEORY.md §1.10). -/
theorem isDCF_LangS {T : Type} [Fintype T] {a b : T} (hab : a ≠ b) :
    is_DCF ({w | LangS a b w} : Language T) := by
  have e : ({w | LangS a b w} : Language T) = Language.map (bl a b) L0 := by
    ext w
    constructor
    · intro hw
      obtain ⟨x, hx, rfl⟩ := LangS_unmap hw
      exact (TflLean.mem_map_iff _ _ _).2 ⟨x, hx, rfl⟩
    · intro hw
      obtain ⟨v, hv, rfl⟩ := (TflLean.mem_map_iff _ _ _).1 hw
      exact LangS_map hv
  rw [e]
  exact DCF_of_map_injective_DCF (bl_injective hab) _ isDCF_L0

end TflLean.ASSb
