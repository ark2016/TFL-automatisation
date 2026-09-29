# Анализ КС-свойства

**Задача:** Проверить язык на контекстную свободу: L = {w₁ b w₂ w₃ | |w₁| = |w₂| ∧ |w₁| = |w₃| ∧ w₂ ∈ (ab)*}, w₁, w₃ ∈ {a,b}*. (Билет №50, ИУ-9)

> [!theorem] Вердикт: не КС (not context-free) ❌
> Уверенность: 98%

> [!success] Доказательство прошло независимую проверку верификатором

> **Доверие к вердикту:** проверено полностью

> [!proof] Доказательство: L не является КС-языком (ogden)

### Шаги доказательства

**Шаг 1. Предположение**

Допустим, $L$ — КС-язык. Тогда по лемме Огдена существует константа $p > 0$ со следующим свойством. Для любого слова $z \in L$, в котором отмечено не менее $p$ позиций, найдётся разбиение $z = u \cdot v \cdot w \cdot x \cdot y$, где $v \cdot x$ содержит хотя бы одну отмеченную позицию, $v \cdot w \cdot x$ содержит не более $p$ отмеченных позиций и $u \cdot v^i \cdot w \cdot x^i \cdot y \in L$ для всех $i \geq 0$.

*Обоснование:* Лемма Огдена для КС-языков; рассуждение ведётся от противного.

**Шаг 2. Вспомогательное наблюдение о структуре слов из L**

Пусть $t \in L$ и $n = |w_1|$. Тогда $|t| = 3n + 1$, поэтому $n = (|t| - 1)/3$ однозначно определяется длиной слова, а значит, однозначно определено и разбиение $t = w_1 \cdot b \cdot w_2 \cdot w_3$. Так как $w_2 \in (ab)^*$ и $|w_2| = n$, число $n = 2k$ чётно. Отсюда $|t| \equiv 1 \pmod 6$.

В 0-индексации получаем критерий: $t \in L \iff |t| = 3n+1$, $n = 2k$, $t[n] = b$ и $t[n+1..2n] = (ab)^k$. В частности, при $n \geq 2$ последний символ блока $w_2$ равен $b$, то есть $t[2n] = b$.

*Обоснование:* Из $|w_1| = |w_2| = |w_3| = n$ и $|b| = 1$ следует $|t| = 3n+1$. Слово из $(ab)^*$ имеет чётную длину и при непустоте оканчивается на $b$.

**Шаг 3. Выбор слова**

Выберем $z = a^{2p} \cdot b \cdot (ab)^p \cdot a^{2p}$, где $|z| = 6p + 1 \geq p$.

Позиции в 0-индексации:
- $0..2p-1$ — блок $a^{2p}$ (это $w_1$);
- $2p$ — средний символ $b$;
- $2p+1..4p$ — блок $(ab)^p$ (это $w_2$);
- $4p+1..6p$ — блок $a^{2p}$ (это $w_3$).

Примеры: при $p = 3$ получаем aaaaaababababaaaaaa, при $p = 4$ — aaaaaaaabababababaaaaaaaa.

**Обоснование принадлежности:** положим $w_1 = a^{2p}$, $w_2 = (ab)^p$, $w_3 = a^{2p}$. Тогда $|w_1| = |w_2| = |w_3| = 2p$ и $w_2 \in (ab)^*$, следовательно $z \in L$.

*Обоснование:* Слово явно представлено в требуемом виде $w_1 \cdot b \cdot w_2 \cdot w_3$, и все условия языка выполнены.

**Шаг 4. Разметка позиций**

Отметим все $6p+1$ позиций слова $z$ (позиции $0, 1, \ldots, 6p$). При такой разметке условие «$v \cdot w \cdot x$ содержит не более $p$ отмеченных позиций» означает $|v \cdot w \cdot x| \leq p$. Условие «$v \cdot x$ содержит отмеченную позицию» означает $|v \cdot x| \geq 1$.

*Обоснование:* Разметка всех позиций — частный случай леммы Огдена, совпадающий с классической леммой Бар-Хиллеля. Ограничение $|v \cdot w \cdot x| \leq p$ нужно для анализа случаев.

**Шаг 5. Разбиение**

Рассмотрим произвольное разбиение $z = u \cdot v \cdot w \cdot x \cdot y$, где $|v \cdot w \cdot x| \leq p$ и $d = |v \cdot x| \geq 1$. Во всех случаях возьмём $i = 0$ и рассмотрим $z_0 = u \cdot w \cdot y$ длины $|z_0| = 6p + 1 - d$.

*Обоснование:* Лемма Огдена при полной разметке гарантирует существование такого разбиения. Чтобы получить противоречие, достаточно показать, что для любого такого разбиения $z_0 \notin L$.

**Шаг 6. Разбор случаев**

| Случай | Расположение $v \cdot w \cdot x$ | Накачанное слово ($i = 0$) | Почему $\notin L$ |
|---|---|---|---|
| 1: $6 \nmid d$ | любое, $|v \cdot w \cdot x| \leq p$ | $z_0 = u \cdot w \cdot y$, $|z_0| = 6p+1-d$ | По шагу 2 все слова $L$ имеют длину $\equiv 1 \pmod 6$. Так как $d \not\equiv 0 \pmod 6$, имеем $6p+1-d \not\equiv 1 \pmod 6$. При $p \leq 5$ возможен только этот случай, так как $1 \leq d \leq p < 6$. |
| 2: $d = 6e$, $e \geq 1$; все удалённые позиции (из $v$ и $x$) строго правее $2p-2e$ | $v \cdot w \cdot x$ начинается правее позиции $2p-2e$ | $z_0$ длины $6(p-e)+1$, $n' = 2(p-e)$ | Для $z_0 \in L$ нужно $z_0[n'] = z_0[2p-2e] = b$. Позиции левее удалённых не сдвигаются, поэтому $z_0[2p-2e] = z[2p-2e]$. Так как $0 \leq 2p-2e < 2p$, это позиция блока $a^{2p}$, и там стоит $a \neq b$. |
| 3: $d = 6e$, $e \geq 1$; $v$ или $x$ содержит позицию $\leq 2p-2e$ | так как $|v \cdot w \cdot x| \leq p$, весь $v \cdot w \cdot x$ лежит в позициях $\leq 3p-2e-1$ | $z_0$ длины $6(p-e)+1$, $n' = 2(p-e)$; блок $w_2'$ занимает позиции $2p-2e+1..4p-4e$ | Так как $6e \leq p$, имеем $k' = p-e \geq 1$, и для $z_0 \in L$ нужно $z_0[2n'] = z_0[4p-4e] = b$. Все удалённые позиции меньше $3p-2e < 4p+2e$, поэтому $z_0[4p-4e] = z[4p-4e+6e] = z[4p+2e]$. Из $1 \leq 2e \leq p/3$ следует $4p+1 \leq 4p+2e \leq 6p$, то есть это позиция блока $w_3 = a^{2p}$, и там стоит $a \neq b$. |

**Полнота разбора:** если $6 \nmid d$, работает случай 1. Если $6 \mid d$, то либо все удалённые позиции правее $2p-2e$ (случай 2), либо хотя бы одна из них $\leq 2p-2e$ (случай 3). Эти варианты исчерпывают все возможности.

*Обоснование:* В каждом случае используется критерий принадлежности из шага 2: длина $\equiv 1 \pmod 6$, символ $b$ на позиции $n'$ и символ $b$ на позиции $2n'$. Позиции левее удалённых символов не меняются, а позиции правее всех удалённых сдвигаются ровно на $d = 6e$.

**Шаг 7. Заключение**

Во всех случаях разбиения $z = u \cdot v \cdot w \cdot x \cdot y$, удовлетворяющего условиям леммы Огдена, слово $u \cdot v^0 \cdot w \cdot x^0 \cdot y \notin L$. Это противоречит лемме Огдена. Следовательно, $L$ **не является КС-языком**. $\blacksquare$

*Замечание.* Рассуждающий агент также указал альтернативный путь: пересечение с регулярным языком $R = a^+ \cdot bb \cdot (ab)^+ \cdot b \cdot a^+$ и применение леммы о накачке к $L \cap R$.

*Обоснование:* Предположение о контекстной свободе $L$ привело к противоречию с необходимым условием леммы Огдена.

### Заключение

Язык $L = \{ w_1 \cdot b \cdot w_2 \cdot w_3 \mid |w_1| = |w_2| = |w_3|,\ w_2 \in (ab)^* \}$ не является контекстно-свободным. В доказательстве использована лемма Огдена с разметкой всех позиций (что равносильно лемме Бар-Хиллеля) на слове $a^{2p} \cdot b \cdot (ab)^p \cdot a^{2p}$ при $i = 0$. Доказательство изложено по аргументации специалиста; независимая автоматическая проверка не проводилась.

**Источники:** Лемма Огдена для КС-языков, Лемма Бар-Хиллеля (pumping lemma для КС-языков), Замкнутость КС-языков относительно пересечения с регулярными языками (альтернативный путь)

**Агенты (успешно):** cfg_builder, closure_reduction, decomposition, interchange, morphism, ogden, parikh, pda_builder, pumping_cfl

**Usage:** 13 calls, 241429 tokens, estimated cost ≈ $2.3906

## Lean 4 formalization (R-Lean)

- status: **proved**
- direction: `non_cfl`
- models: first `claude-opus-5-5`, corrections `claude-sonnet-5-5`
- attempts: 1, cost: $1.5929
- axioms: propext, Classical.choice, Quot.sound

Statement (generated from the IR):

```lean
import TflLean
import Mathlib.Computability.ContextFreeGrammar
import Langlib.Classes.ContextFree.Pumping.Pumping
import Langlib.Classes.ContextFree.Basics.Ogden
inductive Letter
  | a | b
  deriving DecidableEq, Repr
def L : Language Letter := {x : List Letter | ∃ p_w1 p_m p_w2 p_w3 : List Letter, x = p_w1 ++ p_m ++ p_w2 ++ p_w3 ∧ (∀ ch ∈ p_m, ch = Letter.b) ∧ (p_m.length = 1) ∧ (p_w1.length = p_w2.length) ∧ (p_w1.length = p_w3.length) ∧ ((p_w2.length) % 2 = 0) ∧ (¬ ([Letter.a, Letter.a] <:+: p_w2)) ∧ (¬ ([Letter.b, Letter.b] <:+: p_w2)) ∧ ((p_w2.length = 0) ∨ ([Letter.a] <+: p_w2))}
theorem tfl_main : ¬ L.IsContextFree
```

Proof body:

```lean
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
```

| # | model | result | tokens in/out | cost |
|---|---|---|---|---|
| 1 | claude-opus-5-5 | proved | 15912/76464 | $1.5929 |
