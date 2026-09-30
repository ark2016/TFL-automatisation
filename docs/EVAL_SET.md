# Eval-набор: эталонные вердикты (спецификация для `tfl-eval`)

Дата: 2026-09-27. Каждый вердикт ниже проверен теоретически (ссылки на `docs/THEORY.md` или классические результаты);
где вердикт получен перебором — указано. Реализация (`tfl-eval`, IR-файлы в `*/examples/eval/`, метрики) — TODO §7.
Обозначения ловушек: **[trap:…]** — задача, на которой ожидается типичная ошибка агентов; такие задачи важнее для
калибровки, чем «учебные». Метрики прогона: точность вердикта, калибровка confidence (Brier), доля `inconclusive`,
стоимость/токены/время; по умолчанию прогон на Haiku (`TFL_MODEL_OVERRIDE=claude-haiku-4-5`).

## REG (`agent_system`): `regular` / `non_regular`

| id | Язык | Эталон | Обоснование / ловушка |
|---|---|---|---|
| reg-01 | {aⁿbⁿ \| n ≥ 0} | non_regular | накачка / Нероуд |
| reg-02 | {w ∈ {a,b}* \| #a(w) = #b(w)} | non_regular | ∩ a*b* = {aⁿbⁿ} |
| reg-03 | {w ∈ {a,b}* \| #a(w) ≡ #b(w) (mod 2)} | regular | ДКА на 2 состояния **[trap: счётчики, но по модулю]** |
| reg-04 | (ab)*a \| b | regular | regex |
| reg-05 | {aⁿbᵐ \| n ≥ m ≥ 0} | non_regular | Нероуд: aⁱ различимы суффиксом bⁱ |
| reg-06 | {w ∈ {a,b}* \| w = wᴿ} | non_regular | накачка на aⁿbaⁿ |
| reg-07 | {a^{n²} \| n ≥ 0} | non_regular | унарный язык непериодичен **[trap: унарный]** |
| reg-08 | {w ∈ {a,b}* \| w содержит aba} | regular | ДКА подстроки |
| reg-09 | {aⁱbʲ \| i + j чётно} | regular | a*b* ∩ (ΣΣ)* **[trap: выглядит как счёт]** |
| reg-10 | {aⁱbʲ \| i ≠ j} | non_regular | дополнение ∩ a*b* = {aⁿbⁿ} |
| reg-11 | {ww \| w ∈ {a,b}*} | non_regular | Нероуд (и не КС) |
| reg-12 | грамматика S → aS \| Sb \| ε | regular | L = a*b* **[trap: не право-линейная, но регулярный язык]** |
| reg-13 | грамматика S → aSb \| ab | non_regular | {aⁿbⁿ, n ≥ 1} |
| reg-14 | грамматика S → SaSb \| ε \| A, A → bb \| aa \| bSb | non_regular | THEORY.md §0: L ∩ a*b* = {aᵐbᵏ \| m ≡ k (2), m ≤ 3k+2} **[trap: ложное «= {aⁿbⁿ}»]** |
| reg-15 | {aⁿ \| n ≢ 0 (mod 3)} | regular | ДКА на 3 состояния |

## CFL (`cfl_system`): `cfl` / `non_cfl`

| id | Язык | Эталон | Обоснование / ловушка |
|---|---|---|---|
| cfl-01 | {aⁿbⁿcⁿ} | non_cfl | накачка Бар-Хиллеля |
| cfl-02 | {aⁱbʲcᵏ \| i = j ∨ j = k} | cfl | объединение двух КС; существенно неоднозначен (Sh Thm 4.4.1) |
| cfl-03 | {ww \| w ∈ {a,b}*} | non_cfl | накачка на aᵖbᵖaᵖbᵖ |
| cfl-04 | {wwᴿ \| w ∈ {a,b}*} | cfl | S → aSa \| bSb \| ε |
| cfl-05 | {aⁱbʲcᵏ \| i ≤ j ≤ k} | non_cfl | накачка z = aᵖbᵖcᵖ (все случаи, i ∈ {0,2}) |
| cfl-06 | {w₁w₂w₁w₃ \| w₁∈{a,b}⁺, w₂∈{b,c}⁺, w₃∈{a,c}⁺} | non_cfl | THEORY.md §2: R = a⁺b⁺ac·a⁺b⁺ac **[trap: ложное пересечение с a⁺b⁺a⁺c⁺]** |
| cfl-07 | S → aSbb \| ε \| bbSa \| aA, A → aA \| a; фильтр #a = #b | non_cfl | THEORY.md §2.3 (Огден) **[trap: «фильтр-счётчик регулярен»]** |
| cfl-08 | {wwvvᴿ \| w,v ∈ {a,b}*} | non_cfl | THEORY.md §2.5 **[trap: «L = Σ*»]** |
| cfl-09 | {aⁱbʲ \| i ≠ j} | cfl | S → aSb \| A \| B, A → aA \| a, B → bB \| b |
| cfl-10 | {a^{2ⁿ} \| n ≥ 0} | non_cfl | унарный КС = регулярный; Париха |
| cfl-11 | {a^{6n}b^{6n}c^{6n}} | non_cfl | как cfl-01 **[trap: множитель 6]** |
| cfl-12 | {aⁱbʲcᵏdˡ \| i = 0 ∨ j = k = l} | non_cfl | лемма Огдена (стандартная накачка не работает) **[trap: Огден]** [IR: `language_spec.kind: "natural"`, без word-оракула до раунда C4 — `cfl_system/lib/exponent_pattern.py` теперь парсит эту формулировку напрямую; live-подтверждение см. `docs/EVAL_RESULTS.md`] |
| cfl-13 | язык Дика над {(, )} | cfl | S → (S)S \| ε |
| cfl-14 | {aⁿbᵐ \| n ≥ m} | cfl | S → aS \| T, T → aTb \| ε |
| cfl-15 | {aⁱbʲcᵏ \| i + j = k} | cfl | S → aSc \| T, T → bTc \| ε |
| cfl-16 | {w ∈ {a,b}* \| #a = 2·#b} | cfl | взвешенный счётчик на стеке |
| cfl-17 | S → aSb \| ε с фильтром \|w\| ≡ 0 (mod 4) | cfl | CFL ∩ REG **[trap: фильтр здесь регулярен]** |
| cfl-18 | {aⁿbⁿ} ∪ {aⁿb²ⁿ} | cfl | объединение КС (но не DCFL — см. dcfl-05) |
| cfl-19 | {aⁿbᵐaⁿ \| n,m ≥ 0} | cfl | S → aSa \| B, B → bB \| ε **[trap: похоже на копию]** |
| cfl-20 | {aⁿbⁿaⁿ} | non_cfl | как aⁿbⁿcⁿ |

## DCFL (`dcfl_system`): `dcfl` / `non_dcfl`

| id | Язык | Эталон | Обоснование / ловушка |
|---|---|---|---|
| dcfl-01 | {aⁿbⁿ} | dcfl | стек |
| dcfl-02 | {wcwᴿ \| w ∈ {a,b}*} | dcfl | уникальный маркер |
| dcfl-03 | {wwᴿ \| w ∈ {a,b}*} | non_dcfl | THEORY.md §1.2 (Thm 4.7.4) |
| dcfl-04 | {aⁿbⁿcᵐ \| n,m ≥ 0} | dcfl | **[trap: старая формулировка леммы Ю отвергала его]** [IR: `input_format: "set_builder"`, `variables: []`, `word_pattern` — экспоненциальная строка (`"a^n b^n c^m"`), не конкатенация именованных `variables` → `build_set_builder_membership_oracle` возвращала `None` до раунда C4; см. `docs/EVAL_RESULTS.md`] |
| dcfl-05 | {aⁿbⁿ} ∪ {aⁿb²ⁿ} | non_dcfl | THEORY.md §1.1 (лемма Ю) / §1.3 (L_$) |
| dcfl-06 | {aⁱbʲcᵏ \| i = j ∨ j = k} | non_dcfl | существенная неоднозначность |
| dcfl-07 | {aⁱbʲ \| i ≠ j} | dcfl | ДМПА: несовпадение счётчиков |
| dcfl-08 | {wvaavᴿwᴿ \| w ∈ (aa*b)*a, v ∈ b(ab\|aa)*} (exam_01) | non_dcfl | THEORY.md §1.6 **[trap: «aa — разделитель»; эталон исправлен 2026-09-27]** |
| dcfl-09 | {w₁w₂ \| w₁ = u₁au₂, \|u₁\| ≤ \|u₂\|; w₂ = u₃au₄, \|u₃\| ≥ \|u₄\|} (exam_02) | non_dcfl | THEORY.md §1.7 **[эталон исправлен]** |
| dcfl-10 | {aⁿb*(cⁿ\|bⁿ)ac* \| n > 0} (exam_03) | non_dcfl | THEORY.md §1.8 (лемма Ю; НЕ неоднозначность) **[trap: ветви дизъюнктны]** |
| dcfl-11 | {$aⁿbⁿcᵐ} ∪ {d aᵐbⁿcⁿ}, n,m ≥ 1 | dcfl | первый символ выбирает режим (курс, 2025_22) |
| dcfl-12 | {aⁱbʲcᵏ \| i ≤ j ∨ j = k; i,j,k ≥ 1} | non_dcfl | THEORY.md §1.1: MIN(S) ∩ a⁺b⁺c²c* не КС; прежние свидетели Ю из курса допускают накачку |
| dcfl-13 | язык Дика | dcfl | стек |
| dcfl-14 | {aⁿbᵐ \| n ≥ m} | dcfl | стек с остатком |
| dcfl-15 | {u₁au₂ \| \|u₁\| ≤ \|u₂\|} | dcfl | THEORY.md §1.7 (L₁) **[trap: пара с dcfl-16]** [IR правлен в раунде C4: добавлены `variables` + конкатенационный `word_pattern`, чтобы заработал существующий segment-matcher — `dcfl_system/examples/eval/dcfl-15.json`, не подтверждено вживую] |
| dcfl-16 | {u₃au₄ \| \|u₃\| ≥ \|u₄\|} | non_dcfl | THEORY.md §1.7 (L₂: все классы Нероуда конечны) |
| dcfl-17 | грамматика S → aS \| Sa \| a (Format 2) | dcfl | L = a⁺ регулярен **[trap: грамматика неоднозначна, язык — DCFL]** [IR: `input_format: "grammar"` — `build_grammar_membership_oracle` (CYK через `cfl_system.lib.cfl_oracle`) закрыт в раунде C2; живой прогон до C4 всё ещё дал `inconclusive` (см. `docs/EVAL_RESULTS.md`), перепроверка не проводилась] |
| dcfl-18 | {aⁿbⁿ} ∪ {aⁿbᵐcⁿ}, n,m ≥ 1 | non_dcfl | haspref(L) = {aⁿbᵐcⁿ \| m ≥ n} ∉ CFL (THEORY.md §1.3) |
| dcfl-19 | {w ∈ {a,b}* \| w ≠ xx} | non_dcfl | дополнение ∩ … = {xx} ∉ CFL (Sh Ex. 4.7.2) |
| dcfl-20 | {aⁱbʲcᵏ \| i ≠ j ∨ j ≠ k} | non_dcfl | дополнение ∩ a*b*c* = {aⁿbⁿcⁿ} ∉ CFL |
| dcfl-21 | грамматика S → aSSb \| ba \| Ab, A → aAb \| a (exam_04) | dcfl | THEORY.md §1.10 (профильный НМПА + height-determinism, [NS, Thm 4]) **[trap: грамматика неоднозначна и не LR(k) — соблазн заключить не DCFL]** |

## LL (`ll_system`): `ll` (с минимальным k) / `not_ll`; Format 3 — свойство грамматики

| id | Язык / грамматика | Эталон | Обоснование / ловушка |
|---|---|---|---|
| ll-01 | {aⁿbⁿ} | ll, k = 1 | S → aSb \| ε |
| ll-02 | {aⁿbⁿ} ∪ {aⁿcⁿ} | not_ll | THEORY.md §3.3 (C) — DCFL, не LL |
| ll-03 | {wcwᴿ} | ll, k = 1 | S → aSa \| bSb \| c |
| ll-04 | {w b c wᴿ \| w ∈ {a,b}*} | ll, k = 1 | S → aSa \| bT, T → c \| aSab \| bTb (THEORY.md §3.4) |
| ll-05 | {w b* c wᴿ} | not_ll | THEORY.md §3.4 (ограниченная гибкость) **[trap: похоже на ll-04]** |
| ll-06 | {aⁱbʲ \| i ≤ j} | ll, k = 1 | S → TB, T → aTb \| ε, B → bB \| ε |
| ll-07 | {aⁱbʲ \| i ≥ j} | not_ll | THEORY.md §3.4 **[trap: зеркало ll-06]** |
| ll-08 | Format 3: S → aAaa \| bAba, A → b \| ε | LL(2), не SLL(2); SLL(3) | Aho–Ullman **[trap: strong vs LL]** |
| ll-09 | Format 3: S → AB, A → ε \| b, B → aa \| ba | не LL(2), LL(3) | σ(A) = {{aa, ba}} **[trap: плоское σ]** |
| ll-10 | Format 3: E → E + T \| T, T → id | не LL(k) ∀k (левая рекурсия) | сертификат; язык — см. ll-11 |
| ll-11 | Format 2: язык грамматики ll-10 | ll, k = 1 | E → T E′, E′ → + T E′ \| ε **[trap: грамматика ≠ язык]** |
| ll-12 | {aⁿ0bⁿ} ∪ {aⁿ1b²ⁿ} | not_ll | THEORY.md §3.3 (второй пример) |
| ll-13 | (ab)*c | ll, k = 1 | регулярный ⇒ LL(1) |
| ll-14 | {aⁱbʲcᵏ \| i = j ∨ j = k} | not_ll | существенно неоднозначен ⇒ не LL |
| ll-15 | Format 3: S → a¹¹b \| a¹¹c | LL(12), не LL(11) | **[trap: k > 10 — перебор k ≤ 10 не conclusive]** |
| ll-16 | {wwᴿ} | not_ll | не DCFL ⇒ не LL (THEORY.md §1.2) |
| ll-17 | Format 3: S → aSb \| ε | LL(1) | таблица без конфликтов |
| ll-18 | Format 3: S → aS \| a | не LL(1), LL(2) | FIRST₁ обоих правил = {a}; при k = 2: S → aS даёт «aa», S → a даёт «a$» — конфликта нет; минимальный k = 2 **[trap: правая рекурсия без конфликта при k = 2]** |

## Замечания к реализации
- IR-файлы для каждой строки — в `*/examples/eval/<id>.json`; там, где язык уже есть в `examples/`, использовать его (exam_01–03,
  grammar_filter_49, wwvvR, w1w2w1w3, anbn_ancn, wbcwR).
- Для LL Format 3 эталон — свойство грамматики (`is_ll_k(k)` для указанных k и минимальный k), для Format 1/2 — свойство языка.
- Ловушки (trap) считать отдельной метрикой: точность на ловушках — главный индикатор, что промпты не «зазубрили» примеры.
- Вердикт `inconclusive` при верном эталоне засчитывается как «не ошибка, но и не успех» (отдельная доля); ложный уверенный
  вердикт (confidence ≥ 0.6) — как грубая ошибка.
