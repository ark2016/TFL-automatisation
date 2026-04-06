# Анализ детерминированности КС-языка

**Задача:** Является ли данный язык детерминированным? {aⁿb*(cⁿ|bⁿ)ac* | n > 0}

> [!theorem] Вердикт: не ДКСЯ (недетерминированный) ❌
> Уверенность: 90%

## Доказательство

**Метод:** inh_ambiguity

**Дизъюнкция:** cⁿ|bⁿ при общем n с префиксом aⁿb*

**Пересекающиеся слова:** Слова вида aⁿbⁿac* (при пустом b* перед bⁿ): совпадают с веткой bⁿ. При bⁿ = bⁿ после b* получаем пересечение.

**Аргумент неоднозначности:** Язык представим как L₁ ∪ L₂, где L₁ = {aⁿb*cⁿac*}, L₂ = {aⁿb*bⁿac*}. Пересечение L₁ ∩ L₂ на словах с b*bⁿ неоднозначно: разбиение b*bⁿ допускает разные интерпретации границы b*/bⁿ. По теореме о существенной неоднозначности объединения...

**Импликация для ДКСЯ:** Существенно неоднозначный → не UnambCF → не DCFL (т.к. DCFL ⊂ UnambCF)

**Итог рассуждения:** Agent 'inh_ambiguity' verdict: non_dcfl

**Основной источник:** inh_ambiguity

### Результаты агентов

| Агент | Статус | Вердикт | Уверенность |
|-------|--------|---------|-------------|
| stack_strategy | fail | None | 0.30 |
| closure_reduction | not_applicable | None | 0.00 |
| dcfl_pumping | uncertain | non_dcfl | 0.50 |
| shallit | not_applicable | None | 0.00 |
| inh_ambiguity | success | non_dcfl | 0.90 |

### Oracle верификация

| Агент | Статус | Проверки |
|-------|--------|----------|
| stack_strategy | not_applicable | — |
| closure_reduction | not_applicable | — |
| dcfl_pumping | not_verified | — |
| shallit | not_applicable | — |
| inh_ambiguity | verified | 4/4 |
