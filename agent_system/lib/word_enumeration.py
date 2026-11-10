"""Exhaustive enumeration of short words over an alphabet.

Shared by the bounded checks that run an executable artifact on *all* words
up to a length bound (docs/VERDICT_POLICY.md R2' for a DPDA, R3' for an LL(k)
parse table): the bound is the largest length k <= ``max_len_cap`` with
sum_{i<=k} |Sigma|^i <= ``budget``.
"""

from __future__ import annotations

from itertools import product

FULL_ENUMERATION_WORD_BUDGET = 3000
FULL_ENUMERATION_MAX_LEN = 10


def words_up_to(alphabet: list[str], max_len: int) -> list[str]:
    """Every word over ``alphabet`` of length 0..``max_len``, shortest first."""
    words: list[str] = [""]
    for length in range(1, max_len + 1):
        words.extend("".join(combo) for combo in product(alphabet, repeat=length))
    return words


def full_enumeration_max_len(
    alphabet_size: int, budget: int = FULL_ENUMERATION_WORD_BUDGET,
    max_len_cap: int = FULL_ENUMERATION_MAX_LEN,
) -> int:
    """Largest word length k (at most ``max_len_cap``, at least 1) such that
    enumerating every word over an alphabet of this size up to length k stays
    within ``budget`` words in total. For |Sigma| = 3 this is k = 6
    (3 + 9 + ... + 729 + 1 = 1093 <= 3000 < 1093 + 2187); a unary or empty
    alphabet gets ``max_len_cap``."""
    if alphabet_size <= 1:
        return max_len_cap
    k = 0
    total = 1  # the empty word
    while k < max_len_cap:
        nxt = alphabet_size ** (k + 1)
        if total + nxt > budget:
            break
        total += nxt
        k += 1
    return max(k, 1)
