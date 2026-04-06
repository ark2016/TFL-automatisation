# Анализ детерминированности КС-языка

**Задача:** S → aSSb | ba | Ab, A → aAb | a

> [!theorem] Вердикт: ДКСЯ (детерминированный) ✅
> Уверенность: 55%

## Доказательство

**Метод:** stack_strategy

Max retries reached, returning best guess

**Итог рассуждения:** Max retries reached, returning best guess

**Основной источник:** stack_strategy

### Результаты агентов

| Агент | Статус | Вердикт | Уверенность |
|-------|--------|---------|-------------|
| stack_strategy | uncertain | dcfl | 0.55 |
| closure_reduction | not_applicable | None | 0.00 |
| dcfl_pumping | fail | None | 0.20 |
| shallit | not_applicable | None | 0.00 |
| inh_ambiguity | not_applicable | None | 0.00 |

### Oracle верификация

| Агент | Статус | Проверки |
|-------|--------|----------|
| stack_strategy | not_verified | — |
| closure_reduction | not_applicable | — |
| dcfl_pumping | not_applicable | — |
| shallit | not_applicable | — |
| inh_ambiguity | not_applicable | — |
