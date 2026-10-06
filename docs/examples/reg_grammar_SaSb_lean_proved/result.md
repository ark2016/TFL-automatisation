# Результат анализа языка

## Гипотеза
**Вердикт:** non_regular  
**Уверенность:** 0.85  
**Предложенные агенты:** pumping, nerode, closure

### Атомы анализа
| Описание | Тип памяти | Причина |
|----------|-----------|---------|
| grammar with nested recursion (e.g. S->aSb) | infinite | nested recursion generates non-regular patterns |

## Классификация
**Вердикт:** non_regular  
**Обоснование:** Step-back: (1) Memory: the grammar has self-embedding rules (S -> SaSb, A -> bSb) that appear to require matching nested structure, i.e. unbounded stack-like memory. (2) Structure: S -> SaSb has S on both sides of the terminal a, and A -> bSb wraps S between b's, so this is nested recursion rather than a right-linear pattern. (3) Similar language: it resembles balanced-bracket languages like {a^n b^n}, with a as an opening and b as a closing symbol (S -> SaSb is like a Dyck-style rule). Hard rule nested_recursion applies because of the SaSb self-embedding, so the verdict is non_regular. Caveat: the base cases bb, aa and ε, together with the bSb wrapper, mean the language could differ from a pure Dyck language, so pumping and Nerode proofs should be checked carefully.

## Доказательство

### Лемма о накачке
**Выбор слова:** w = a^(3n+2) b^n

**Принадлежность:** Индукция по k ≥ 1 показывает, что a^(3k+2) b^k ∈ L. База k=1: a^5 b = aa·a·aa·b выводится так: S → SaSb, левое S → A → aa, правое S → A → aa. Шаг: пусть a^(3(k-1)+2) b^(k-1) ∈ L. Тогда a^(3k+2) b^k = aa · a · a^(3(k-1)+2) b^(k-1) · b. Применяем S → SaSb, левое S → A → aa, правое S выводит a^(3(k-1)+2) b^(k-1) по предположению индукции. Длины сходятся: 2+1+(3k-1)+1 = 3k+2 букв a (с учётом b) — всё согласовано. Значит w = a^(3n+2) b^n ∈ L при любом n ≥ 1.

**Анализ разбиения:** Сначала докажем две вспомогательные леммы. Лемма 1: L ∩ a* = {ε, aa}. Действительно, всякое непустое слово, выведенное через S → SaSb, оканчивается на b. Через S → A получаются только bb, aa и слова вида bSb, начинающиеся с b. Поэтому единственное непустое слово из L без букв b — это aa. Лемма 2: если a^m b^k ∈ L и k ≥ 1, то m ≤ 3k+2. Доказываем индукцией по k, разбирая первый шаг вывода. (1) S → ε или S → A → aa: тогда k = 0, что противоречит условию k ≥ 1. (2) S → A → bb или S → A → bSb: слово начинается с b, значит m = 0 ≤ 3k+2. (3) S → SaSb: тогда u = s1·a·s2·b, где s1, s2 ∈ L. Буква a, стоящая после s1, лежит в блоке a^m, поэтому s1 = a^p, p < m. По лемме 1 p ∈ {0, 2}. Отсюда s2 = a^(m-1-p) b^(k-1). Если k = 1, то s2 ∈ a*, и по лемме 1 m-1-p ≤ 2, откуда m ≤ 3+p ≤ 5 = 3·1+2. Если k ≥ 2, то по предположению индукции m-1-p ≤ 3(k-1)+2, откуда m ≤ 3k+p ≤ 3k+2. Лемма доказана. Теперь сама накачка: пусть w = xyz, |xy| ≤ n, |y| ≥ 1. Так как первые 3n+2 ≥ n символов w — буквы a, префикс xy целиком лежит в блоке из a. Значит y = a^j, где 1 ≤ j ≤ n.

  Накачанное слово: xy^2 z = a^(3n+2+j) b^n
  Противоречие: Здесь число b равно k = n ≥ 1, а число a равно m = 3n+2+j > 3n+2 = 3k+2. По лемме 2 любое слово a^m b^k ∈ L с k ≥ 1 удовлетворяет m ≤ 3k+2. Следовательно, a^(3n+2+j) b^n ∉ L.

**Заключение:** Для любой длины накачки n выбрано слово w = a^(3n+2) b^n ∈ L, |w| ≥ n. При любом разбиении w = xyz с |xy| ≤ n и |y| ≥ 1 слово xy^2 z не принадлежит L. По лемме о накачке язык грамматики S → SaSb | ε | A, A → bb | aa | bSb не является регулярным. Замечание: L ∩ a*b* совпадает с {a^m b^k : k ≥ 1, m+k чётно, m ≤ 3k+2} ∪ {ε, aa}, а не с условием m ≡ k (mod 2), предложенным препроцессором. Так, a^7 b ∉ L, хотя 7 ≡ 1 (mod 2).

### Теорема Майхилла-Нероуда
**Семейство слов:** a^{3i+2} для i \u2208 i ≥ 0

**Различающие контексты:**
| Пара | Контекст | В языке | Не в языке |
|------|---------|---------|------------|
| ['a^{3i+2}', 'a^{3j+2}'] | b^i | a^{3i+2} b^i ∈ L: по индукции S ⇒ S a S b ⇒* aa·a·(a^{3(i-1)+2} b^{i-1})·b = a^{3i+2} b^i; база i=0: S ⇒ A ⇒ aa | a^{3j+2} b^i ∉ L, так как |w|_a = 3j+2 > 3i+2 = 3|w|_b + 2, что нарушает инвариант |w|_a ≤ 3|w|_b + 2, выполненный для всех слов из L |

**Аргумент:** 1) Инвариант. Индукцией по выводу докажем, что для любого терминального слова w, выводимого из S или из A, выполнено |w|_a ≤ 3|w|_b + 2. Правило S → ε: 0 ≤ 2. A → aa: 2 ≤ 2. A → bb: 0 ≤ 8. A → bSb: если для u выполнено |u|_a ≤ 3|u|_b + 2, то для bub имеем |u|_a ≤ 3(|u|_b + 2) + 2. S → A: наследуется. S → S a S b: если u, v удовлетворяют инварианту, то |uavb|_a = |u|_a + |v|_a + 1 ≤ 3|u|_b + 2 + 3|v|_b + 2 + 1 = 3(|u|_b + |v|_b + 1) + 2 = 3|uavb|_b + 2. Значит, каждое слово из L удовлетворяет |w|_a ≤ 3|w|_b + 2. 2) Принадлежность. Индукцией по i покажем, что a^{3i+2} b^i ∈ L. База: S ⇒ A ⇒ aa. Шаг: S ⇒ S a S b, где первое S ⇒ A ⇒ aa, а второе S ⇒* a^{3(i-1)+2} b^{i-1} (по предположению индукции); получаем aa·a·a^{3i-1} b^{i-1}·b = a^{3i+2} b^i. 3) Различимость. Пусть i < j. Контекст z = b^i: слово a^{3i+2} b^i ∈ L по п. 2, а слово a^{3j+2} b^i содержит 3j+2 > 3i+2 = 3·i + 2 букв a при i буквах b, поэтому по п. 1 не принадлежит L. Следовательно, a^{3i+2} и a^{3j+2} не эквивалентны по Нероуду. Бесконечное множество {a^{3i+2} | i ≥ 0} попарно различимо, значит, отношение ≡_L имеет бесконечный индекс.

**Заключение:** По теореме Майхилла-Нероуда отношение эквивалентности Нероуда для L имеет бесконечный индекс, поэтому язык L не является регулярным (не распознаётся никаким конечным автоматом).

### Замыкание
**Метод:** inverse_homomorphism
**Заключение:** Предположим, что L регулярен. Класс регулярных языков замкнут относительно обратного гомоморфизма и пересечения, а язык x*y* регулярен. Для гомоморфизма g(x) = a⁶, g(y) = b² язык g⁻¹(L) ∩ x*y* тогда тоже был бы регулярным. Выше показано: L ∩ a*b* = {a^m b^k | m ≡ k (mod 2), m ≤ 3k + 2}, откуда g⁻¹(L) ∩ x*y* = {x^i y^j | i ≤ j}. Этот язык нерегулярен по лемме о накачке (слово x^p y^p). Противоречие. Следовательно, язык грамматики S → SaSb | ε | A, A → bb | aa | bSb не является регулярным. Замечание: уже L ∩ a*b* нерегулярен из-за линейного ограничения m ≤ 3k + 2 (число a ограничено числом b). Одна только чётность длины такого эффекта не дала бы.

## Консолидированное доказательство
Язык L грамматики S → SaSb | ε | A, A → bb | aa | bSb не является регулярным.

1) Инвариант. Индукцией по выводу докажем, что для любого слова w, выводимого из S или A, выполнено |w|_a ≤ 3|w|_b + 2.
- S → ε: 0 ≤ 2.
- A → aa: 2 ≤ 2.
- A → bb: 0 ≤ 8.
- A → bSb: если |u|_a ≤ 3|u|_b + 2, то |bub|_a = |u|_a ≤ 3(|u|_b + 2) + 2.
- S → A: инвариант наследуется.
- S → SaSb: |uavb|_a = |u|_a + |v|_a + 1 ≤ 3|u|_b + 2 + 3|v|_b + 2 + 1 = 3(|u|_b + |v|_b + 1) + 2 = 3|uavb|_b + 2.

2) Принадлежность. Индукцией по i покажем, что a^{3i+2} b^i ∈ L для всех i ≥ 0.
- База: S ⇒ A ⇒ aa.
- Шаг: S ⇒ SaSb. Левое S ⇒ A ⇒ aa, правое S ⇒* a^{3(i-1)+2} b^{i-1} по предположению индукции. Получаем aa·a·a^{3i-1} b^{i-1}·b = a^{3i+2} b^i.

3) Различимость (теорема Майхилла-Нероуда). Рассмотрим семейство слов a^{3i+2}, i ≥ 0. Пусть i < j, возьмём контекст z = b^i.
- a^{3i+2} b^i ∈ L по п. 2.
- a^{3j+2} b^i ∉ L: в нём 3j+2 > 3i+2 букв a при i буквах b, что нарушает инвариант п. 1.
Значит, слова a^{3i+2} попарно неэквивалентны по Нероуду. Отношение ≡_L имеет бесконечный индекс, и по теореме Майхилла-Нероуда L не распознаётся никаким конечным автоматом.

4) Независимое подтверждение леммой о накачке. Пусть n — длина накачки, w = a^{3n+2} b^n ∈ L, |w| ≥ n. При любом разбиении w = xyz с |xy| ≤ n, |y| ≥ 1 имеем y = a^j, 1 ≤ j ≤ n. Тогда xy²z = a^{3n+2+j} b^n. Здесь число a больше 3n+2, что нарушает инвариант, поэтому xy²z ∉ L. Противоречие с леммой о накачке.

5) Подтверждение через замкнутость. L ∩ a*b* = {a^m b^k | m ≡ k (mod 2), m ≤ 3k+2}. Для гомоморфизма g(x) = a⁶, g(y) = b² имеем g(x^i y^j) = a^{6i} b^{2j}. Условие 6i ≤ 3·2j + 2 равносильно i ≤ j, чётность выполнена всегда. Поэтому g⁻¹(L) ∩ x*y* = {x^i y^j | i ≤ j}. Этот язык нерегулярен (накачка слова x^p y^p). Регулярные языки замкнуты относительно обратного гомоморфизма и пересечения с регулярным языком, поэтому L не может быть регулярным.

## Подсказки и наблюдения
- Установлено: L ∩ a*b* = {aᵐbᵏ | m ≡ k (mod 2), 0 ≤ m ≤ 3k+2}. Это НЕ {aⁿbⁿ}: например, bb, aa, aaaaab ∈ L.
- Инвариант 1: |w|_a ≡ |w|_b (mod 2) для всех w ∈ L; все слова L имеют чётную длину.
- Инвариант 2: |w|_a ≤ 3|w|_b + 2. Именно он даёт нерегулярность: число a ограничено линейной функцией от числа b.
- Ключевое семейство: a^{3i+2} b^i ∈ L — максимальное количество a при i буквах b. Оно получается правилом S → SaSb с левым S ⇒ aa.
- Контрпример к наивной гипотезе «m ≡ k (mod 2)»: a⁷b ∉ L, хотя 7 и 1 одной чётности.
- Предупреждение: гомоморфизм g(x) = a³, g(y) = b не подходит — прообраз зависит ещё и от чётности. Используйте g(x) = a⁶, g(y) = b² (прообраз {x^i y^j | i ≤ j}) или обходитесь без гомоморфизма.
- Рекомендация для экзамена: самое короткое строгое доказательство — через теорему Майхилла-Нероуда. Нужны три шага: инвариант |w|_a ≤ 3|w|_b + 2, принадлежность a^{3i+2}b^i ∈ L и различение a^{3i+2}, a^{3j+2} контекстом b^i.

## Верификация

### Формализация (Lean 4)
Статус: proved

## Итог
**Статус:** success  
**Уверенность:** 0.98
**Доверие:** проверено полностью (verified)

**Usage:** 7 calls, 97662 tokens, estimated cost ≈ $0.8922

## Lean 4 formalization (R-Lean)

- status: **proved**
- direction: `non_regular`
- models: first `claude-opus-5-5`, corrections `claude-sonnet-5-5`
- attempts: 1, cost: $2.1071
- axioms: propext, Classical.choice, Quot.sound

Statement (generated from the IR):

```lean
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
theorem tfl_main : ¬ L.IsRegular
```

Proof body:

```lean
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
```

| # | model | result | tokens in/out | cost |
|---|---|---|---|---|
| 1 | claude-opus-5-5 | proved | 10853/103185 | $2.1071 |
