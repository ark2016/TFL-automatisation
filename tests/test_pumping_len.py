"""
Тесты для pumping_len.py (TODO.md §4).

Проверяем:
  - конкретные p_min из ТЗ (a*|bbbb, a*, a+, конечный язык, ∅, (ab)*);
  - что ε и ∅ разбираются как языки {ε}/∅, а не как литеральные символы;
  - что конечные и пустые языки не бросают исключение;
  - точность алгоритма на 8 небольших регулярках сверкой с прямым перебором
    по определению (все слова длины ≤ n+p+2, все разбиения, i от 0 до n+1).
"""
from __future__ import annotations

import itertools

import pumping_len as pl


# ============================================================
# Конкретные случаи из ТЗ (TODO.md §4)
# ============================================================

def test_a_star_or_bbbb_p_min_5():
    # bbbb не накачивается ни при каком разбиении с |xy| ≤ 4; все слова
    # длины ≥ 5 в этом языке — это a^i, и они накачиваются.
    result = pl.min_pumping_length("a*|bbbb")
    assert result.p_min == 5
    assert not result.language_empty
    assert result.witness is not None
    assert result.witness.p == 4
    assert result.witness.word == "bbbb"


def test_a_star_p_min_1():
    # ε ∈ a*, но при p=0 её нельзя накачать (|y| ≥ 1 невозможно для |w|=0)
    # ⇒ p=0 невалидно. При p=1 все aⁱ, i ≥ 1, накачиваются ⇒ p_min = 1.
    result = pl.min_pumping_length("a*")
    assert result.p_min == 1
    assert not result.language_empty
    assert result.witness is not None
    assert result.witness.p == 0
    assert result.witness.word == ""  # u=z="" — само пустое слово не накачать


def test_a_plus_p_min_2():
    # a+ = a·a* (пишем как "aa*", т.к. грамматика не знает '+').
    # При p=1 единственное разложение w="a" — x=ε,y=a,z=ε, и xy⁰z=ε ∉ a+.
    # При p=2 разбор по TODO.md §4 показывает рабочее разбиение для w="aa".
    result = pl.min_pumping_length("aa*")
    assert result.p_min == 2
    assert result.witness is not None
    assert result.witness.p == 1


def test_finite_language_p_min_is_max_length_plus_one():
    # {ab, abc}: максимальная длина слова 3 ⇒ p_min = 4 (нет циклов в живой
    # части автомата, все слова короче p автоматически удовлетворяют лемме).
    result = pl.min_pumping_length("ab|abc")
    assert result.p_min == 4
    assert not result.language_empty


def test_empty_language_p_min_0_no_exception():
    # Пустой язык: p=0 валидно тривиально (нет w ∈ L), лемма не бросает
    # исключение (в отличие от старой реализации).
    result = pl.min_pumping_length("∅")  # ∅
    assert result.p_min == 0
    assert result.language_empty
    assert result.witness is None


def test_ab_star_p_min_2():
    result = pl.min_pumping_length("(ab)*")
    assert result.p_min == 2


# ============================================================
# ε / ∅ — не литералы
# ============================================================

def test_epsilon_char_is_language_of_empty_word_not_a_literal():
    dfa = pl.regex_to_min_dfa("ε")  # 'ε'
    assert pl.dfa_accepts(dfa, "")
    # алфавит пуст — 'ε' не стал символом языка
    assert dfa.alphabet == []


def test_empty_parens_is_also_epsilon():
    dfa_eps = pl.regex_to_min_dfa("ε")
    dfa_parens = pl.regex_to_min_dfa("()")
    assert pl.dfa_accepts(dfa_parens, "")
    assert dfa_eps.num_states == dfa_parens.num_states == 1


def test_empty_set_literal_matches_nothing():
    dfa = pl.regex_to_min_dfa("∅")  # ∅
    assert not pl.dfa_accepts(dfa, "")
    assert not pl.dfa_accepts(dfa, "a")


def test_empty_set_absorbs_in_concat_and_union():
    # ∅·a = ∅, a|∅ = a
    assert not pl.dfa_accepts(pl.regex_to_min_dfa("∅a"), "a")
    dfa_union = pl.regex_to_min_dfa("a|∅")
    assert pl.dfa_accepts(dfa_union, "a")
    assert not pl.dfa_accepts(dfa_union, "")


def test_empty_set_star_is_epsilon_language():
    # (∅)* = {ε}
    dfa = pl.regex_to_min_dfa("(∅)*")
    assert pl.dfa_accepts(dfa, "")
    assert not pl.dfa_accepts(dfa, "a")


# ============================================================
# Не бросает исключение на конечных/пустых языках (регрессия TODO.md §4)
# ============================================================

def test_no_exception_on_various_languages():
    for regex in ["∅", "ε", "()", "a", "ab|abc", "a*", "aa*", "(ab)*", "a*|bbbb"]:
        result = pl.min_pumping_length(regex)
        assert isinstance(result.p_min, int) and result.p_min >= 0


# ============================================================
# CLI: печатает p_min, --witness, --json
# ============================================================

def test_cli_prints_p_min(capsys):
    rc = pl.main(["a*|bbbb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "5" in out


def test_cli_witness_flag(capsys):
    rc = pl.main(["a*|bbbb", "--witness"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bbbb" in out


def test_cli_json_flag(capsys):
    import json

    rc = pl.main(["a*|bbbb", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["p_min"] == 5
    assert payload["witness"]["word"] == "bbbb"


# ============================================================
# Брутфорс-проверка по определению (см. TODO.md §4)
# ============================================================

def _brute_force_p_min(dfa: pl.DFA) -> int:
    """Прямой перебор по определению Сипсера: для каждого p проверяет ВСЕ
    слова языка длины от p до n+p+2 и ВСЕ разбиения (без оптимизаций
    pumping_len.py) — независимая проверка корректности алгоритма."""
    n = dfa.num_states
    for p in range(n + 2):
        if _p_is_valid_by_definition(dfa, p):
            return p
    raise AssertionError(f"брутфорс не нашёл валидный p при p <= n+1={n + 1}")


def _p_is_valid_by_definition(dfa: pl.DFA, p: int) -> bool:
    n = dfa.num_states
    bound = n + p + 2
    for length in range(p, bound + 1):
        for tup in itertools.product(dfa.alphabet, repeat=length):
            w = "".join(tup)
            if not pl.dfa_accepts(dfa, w):
                continue
            if not _has_valid_decomposition(dfa, w, p):
                return False
    return True


def _has_valid_decomposition(dfa: pl.DFA, w: str, p: int) -> bool:
    n = dfa.num_states
    limit = min(len(w), p)
    for xy_len in range(1, limit + 1):        # |xy| ∈ [1, p]
        for y_len in range(1, xy_len + 1):    # |y| ∈ [1, |xy|]
            x = w[: xy_len - y_len]
            y = w[xy_len - y_len: xy_len]
            z = w[xy_len:]
            if _pumps_for_all_i(dfa, x, y, z, n):
                return True
    return False


def _pumps_for_all_i(dfa: pl.DFA, x: str, y: str, z: str, n: int) -> bool:
    # xy^i z / принадлежность L периодична по i с периодом ≤ n после
    # применения y (число состояний конечно) — i от 0 до n+1 достаточно,
    # чтобы установить истинность для ВСЕХ i ≥ 0.
    for i in range(n + 2):
        if not pl.dfa_accepts(dfa, x + y * i + z):
            return False
    return True


_BRUTE_FORCE_REGEXES = [
    "ab*",
    "(a|b)*ab",
    "a(bb)*",
    "(ab|ba)*",
    "a*b|b*a",
    "(a|b)(a|b)*c",
    "aab*a",
    "(abc)*|a*",
]


def test_bruteforce_matches_algorithm_on_small_regexes():
    for regex in _BRUTE_FORCE_REGEXES:
        dfa = pl.regex_to_min_dfa(regex)
        algo = pl.compute_min_pumping_length(dfa).p_min
        brute = _brute_force_p_min(dfa)
        assert algo == brute, f"{regex!r}: algo={algo} brute={brute}"


# ============================================================
# Существующий пайплайн regex -> NFA -> DFA -> min DFA
# ============================================================

def test_pipeline_min_dfa_is_correct_language():
    # (a|b)*abb — классический пример из учебника, проверяем на словах.
    dfa = pl.regex_to_min_dfa("(a|b)*abb")
    accepted = ["abb", "aabb", "babb", "ababb"]
    rejected = ["", "a", "ab", "abba", "bab"]
    for w in accepted:
        assert pl.dfa_accepts(dfa, w), w
    for w in rejected:
        assert not pl.dfa_accepts(dfa, w), w


def test_minimization_reduces_state_count():
    # (a|b)*abb классически минимизируется до 4 состояний.
    dfa = pl.regex_to_min_dfa("(a|b)*abb")
    assert dfa.num_states == 4
