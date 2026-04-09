# Проверка LL-свойства

**Задача:** L = {aⁿbⁿ | n≥0} ∪ {aⁿcⁿ | n≥0}. Is this language LL(k) for some k?

*Тип задачи: Формат 1: проверка языка на LL*

## Вердикт: Не LL ни для какого k

**Уверенность:** 95%

## Доказательство

**Метод:** substitution

> **Свидетель:** `{'k': 'k (произвольное)', 'w1': 'a^{n+k} (n произвольное, n ≥ 1)', 'lookahead': 'b^k', 'suffix_1': 'b^n ∈ {aⁿbⁿ} после a^{n+k}b^k = a^{n+k}b^{n+k}', 'suffix_2': 'c^n', 'substitution_result': 'a^{n+k} b^k c^n', 'why_not_in_L': 'Слово a^{n+k}b^k c^n не принадлежит ни {aⁿbⁿ}, ни {aⁿcⁿ}: в {aⁿbⁿ} нужно n=n+k и равное число b, в {aⁿcⁿ} нет символов b.'}`

## Проверка утверждений

- **ll_grammar_builder**: inconclusive (0/0 проверок) — Agent returned uncertain verdict
- **marker_analyzer**: inconclusive (0/0 проверок) — Agent returned uncertain verdict
- **grammar_transformer**: inconclusive (0/0 проверок) — Agent returned uncertain verdict
- **substitution_agent**: verified (6/6 проверок)
- **ambiguity_detector**: inconclusive (0/0 проверок) — Agent returned uncertain verdict
- **prefix_classes_agent**: inconclusive (0/0 проверок) — No verifier for method 'prefix_classes'

## Решение

**Задача:** Определить, является ли язык $L = \{a^n b^n \mid n \geq 0\} \cup \{a^n c^n \mid n \geq 0\}$ LL(k)-языком.

**Вердикт:** Язык **не является LL(k) ни для какого $k$**.

### Доказательство (метод подмены)

Предположим, что $L$ является LL(k) для некоторого $k$. Зафиксируем произвольное $n > k$.

Рассмотрим два слова:
- $w_1 = a^{n+k}b^{n+k} \in L$ (из $\{a^n b^n\}$)
- $w_2 = a^{n+k}c^{n+k} \in L$ (из $\{a^n c^n\}$)

После чтения $a^{n+k}$ LL(k)-парсер смотрит на следующие $k$ символов:
- В $w_1$: lookahead = $b^k$
- В $w_2$: lookahead = $c^k$

Lookaheads различны, поэтому парсер может различить продолжения. Однако рассмотрим:
- $u = a^{n+k}$, lookahead $v = b^k$, суффикс $s_1 = b^n$ → $uv s_1 = a^{n+k}b^{n+k} \in L$
- Подмена: суффикс $s_2 = c^n$ → $uv s_2 = a^{n+k}b^k c^n$

Но $a^{n+k}b^k c^n \notin L$: это слово не входит в $\{a^m b^m\}$ (числа $b$ и $a$ различны при $k > 0$) и не входит в $\{a^m c^m\}$ (присутствует $b$). Противоречие с предположением LL(k). $\square$

## Рекомендации агента-рассуждателя

*агент: **substitution_agent**, метод: **substitution***

Язык $L = \{a^n b^n \mid n \geq 0\} \cup \{a^n c^n \mid n \geq 0\}$ не является LL(k) ни для какого $k$.

**Обоснование:** Метод подмены (доказан агентом substitution_agent): для произвольного $k$ рассмотрим слова $a^{n+k}b^{n+k}$ и $a^{n+k}c^{n+k}$, оба принадлежащих $L$. После чтения $a^{n+k}$ lookahead равен $b^k$ для первого и $c^k$ для второго. Поскольку lookaheads различны, LL(k)-парсер может их различить. Однако метод подмены показывает, что после чтения $a^{n+k}$ с lookahead $b^k$ парсер должен продолжить $b^n$, но не может принять $c^n$. Это противоречит детерминированности для слов $a^{n+k}b^{n+k}$ и требованию отклонить $a^{n+k}b^k c^n \notin L$. Prefix classes agent подтверждает: для каждого $k$ существуют бесконечно много k-различимых префиксов.

## Анализ агентов

**Агенты (успешно):** ambiguity_detector, formalizer, grammar_transformer, ll_grammar_builder, marker_analyzer, prefix_classes_agent, substitution_agent

- **ll_grammar_builder**: вердикт `uncertain`, уверенность 5%
- **marker_analyzer**: вердикт `uncertain`, уверенность 5%
- **grammar_transformer**: вердикт `uncertain`, уверенность 10%
- **substitution_agent**: вердикт `not_ll`, уверенность 95%
  - Метод: `substitution`
  - k: k (произвольное)
  - w1: a^{n+k} (n произвольное, n ≥ 1)
  - lookahead: b^k
  - suffix_1: b^n ∈ {aⁿbⁿ} после a^{n+k}b^k = a^{n+k}b^{n+k}
- **ambiguity_detector**: вердикт `uncertain`, уверенность 30%
- **prefix_classes_agent**: вердикт `not_ll`, уверенность 75%
  - Метод: `prefix_classes`
  - Для каждого k существуют бесконечно много попарно k-различимых префиксов: a, aa, aaa, ... Префикс aⁿ отличается от aᵐ (n≠m), так как после aⁿ необходимо читать bⁿ или cⁿ, а после aᵐ — bᵐ или cᵐ.
