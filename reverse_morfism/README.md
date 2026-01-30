# Обратный гомоморфизм для регулярных языков

Построение прообраза регулярного языка относительно гомоморфизма.

Дан язык L и гомоморфизм h. Строится минимальный ДКА для h^(-1)(L) = {w | h(w) in L}.

## Установка

```bash
pip install graphviz
```

Для генерации PNG также нужен системный Graphviz: https://graphviz.org/download/

## Запуск

```bash
cd reverse_morfism
python main.py
```

## Пример

**Задача:** Построить прообраз языка ((aba)*bb|aa)* относительно гомоморфизма h(a) = baa, h(b) = aaa, h(c) = a, h(d) = ab.

### Ввод

```
L = ((aba)*bb|aa)*

h: a=baa b=aaa c=a d=ab
h2: [нажать Enter — пустая строка завершает ввод]
```

Все символы одного гомоморфизма вводятся **на одной строке** через пробел.

### Вывод

```
------------------------------------------------------------
SOLUTION
------------------------------------------------------------

Language L: ((aba)*bb|aa)*
Homomorphism h: h(a)=baa, h(b)=aaa, h(c)=a, h(d)=ab

Result: minimal DFA with 6 states
Alphabet: {a, b, c, d}
Start state: q0
Accept states: {q0}

Regular expression for h^(-1)(L): (((eps+(b+c)((b+c)(b+c))*(b+c))+(b+c)((b+c)(b+c))*d(a(b+c)((b+c)(b+c))*d)*(a+a(b+c)((b+c)(b+c))*(b+c)))+((d+(b+c)((b+c)(b+c))*(b+c)d)+(b+c)((b+c)(b+c))*d(a(b+c)((b+c)(b+c))*d)*(ad+a(b+c)((b+c)(b+c))*(b+c)d))((d(a(b+c)((b+c)(b+c))*d)*(ad+a(b+c)((b+c)(b+c))*(b+c)d)+cd))*d(a(b+c)((b+c)(b+c))*d)*(a+a(b+c)((b+c)(b+c))*(b+c)))

Transition table:
State   a       b       c       d
----------------------------------------
>*q0    q3      q1      q1      q5
  q1    q3      q0      q0      q2
  q2    q0      q3      q3      q3
  q3    q3      q3      q3      q3
  q4    q3      q3      q3      q5
  q5    q3      q3      q4      q2
```

Обозначения в таблице: `>` — начальное состояние, `*` — принимающее.

### Проверка слов

После таблицы программа предлагает проверить слова:

```
Words: bb cb dd eps

  w = 'bb'
  h(w) = 'aaaaaa'
  w in h^(-1)(L): [+] ACCEPTED

  w = 'cb'
  h(w) = 'aaaa'
  w in h^(-1)(L): [+] ACCEPTED

  w = 'dd'
  h(w) = 'abab'
  w in h^(-1)(L): [-] REJECTED

  w = 'eps'
  h(w) = 'eps'
  w in h^(-1)(L): [+] ACCEPTED
```

Для проверки пустого слова вводите `eps`.

### Сохранение автомата

```
Save automaton? (filename without extension, or 'n'):
> result

DOT saved: result.dot
PNG saved: result.png
```

## Формат ввода

### Регулярное выражение

| Синтаксис | Значение          | Пример       |
|-----------|-------------------|--------------|
| `a`       | символ            | `a`, `0`, `1`|
| `(R)`     | группировка       | `(ab)`       |
| `R*`      | замыкание Клини   | `a*`         |
| `R+`      | одно или более    | `a+`         |
| `R?`      | ноль или одно     | `a?`         |
| `RS`      | конкатенация      | `ab`         |
| `R\|S`    | объединение       | `a\|b`       |

### Гомоморфизм

Формат: `символ=образ`, через пробел. Все символы на одной строке.

```
h: a=baa b=aaa c=a d=ab
```

Для пустого образа (eps): `z=eps` или `z=`.

### Композиция гомоморфизмов

Для вычисления (h1 o h2)^(-1)(L) = h2^(-1)(h1^(-1)(L)):

```
h: a=01 b=10          <-- h1: ближайший к L
h2: x=ab y=ba         <-- h2: внешний
h3:                    <-- Enter для завершения
```

## Использование из кода

```python
from inverse_homomorphism import (
    InverseHomomorphismSolver,
    dfa_to_table,
    dfa_to_regex,
    dfa_to_dot
)

solver = InverseHomomorphismSolver()
solver.set_language("((aba)*bb|aa)*")
solver.add_homomorphism({
    'a': 'baa', 'b': 'aaa', 'c': 'a', 'd': 'ab'
})

dfa = solver.solve()

print(dfa_to_table(dfa))       # таблица переходов
print(dfa_to_regex(dfa))       # регулярное выражение
print(dfa.accepts("bb"))       # True
print(dfa.accepts("dd"))       # False

# Сохранить в DOT/PNG
with open("result.dot", "w") as f:
    f.write(dfa_to_dot(dfa))
```

## Тесты

```bash
cd reverse_morfism
python tests.py
```

## Алгоритм

Подробное описание алгоритма с формулами: [algo.md](algo.md)

Краткая суть:

1. Строим NFA из регулярного выражения (Thompson construction)
2. NFA -> DFA (Subset construction)
3. Делаем DFA тотальным (добавляем sink state)
4. Строим новый DFA: delta'(q, a) = delta_hat(q, h(a))
5. Минимизируем результат
