"""
Test word generator for oracle testing.

Per §4.10.2: generates words using multiple strategies.
"""

from __future__ import annotations

import random
from itertools import product as cart_product


def generate_exhaustive(alphabet: list[str], max_len: int = 8) -> list[str]:
    """All words over alphabet up to length max_len."""
    words: list[str] = [""]  # epsilon
    for length in range(1, max_len + 1):
        for combo in cart_product(alphabet, repeat=length):
            words.append("".join(combo))
    return words


def generate_boundary(alphabet: list[str], pumping_constant: int) -> list[str]:
    """Words of lengths around the pumping constant p: p-1, p, p+1, 2p."""
    targets = {
        max(0, pumping_constant - 1),
        pumping_constant,
        pumping_constant + 1,
        2 * pumping_constant,
    }
    words: list[str] = []
    for length in sorted(targets):
        if length == 0:
            words.append("")
            continue
        for combo in cart_product(alphabet, repeat=length):
            words.append("".join(combo))
            if len(words) > 5000:
                return words
    return words


def generate_random_long(
    alphabet: list[str],
    count: int = 50,
    min_len: int = 20,
    max_len: int = 50,
    seed: int | None = None,
) -> list[str]:
    """Random long words for smoke testing."""
    rng = random.Random(seed)
    words: list[str] = []
    for _ in range(count):
        length = rng.randint(min_len, max_len)
        word = "".join(rng.choice(alphabet) for _ in range(length))
        words.append(word)
    return words


def generate_nerode_targets(base_words: list[str], contexts: list[str]) -> list[str]:
    """Concatenate base words with distinguishing contexts."""
    words: list[str] = []
    for w in base_words:
        words.append(w)
        for z in contexts:
            words.append(w + z)
    return words


def generate_test_words(
    alphabet: list[str],
    strategies: list[str] | None = None,
    pumping_constant: int = 5,
    max_exhaustive: int = 8,
    seed: int | None = 42,
) -> list[str]:
    """Generate test words using specified strategies.

    Strategies:
    - 'exhaustive_k': all words up to length k (default k=max_exhaustive)
    - 'boundary': words around pumping constant
    - 'random_long': random words of length 20-50
    """
    if strategies is None:
        strategies = ["exhaustive_k"]

    all_words: list[str] = []
    seen: set[str] = set()

    def add_unique(words: list[str]) -> None:
        for w in words:
            if w not in seen:
                seen.add(w)
                all_words.append(w)

    for strategy in strategies:
        if strategy == "exhaustive_k":
            add_unique(generate_exhaustive(alphabet, max_exhaustive))
        elif strategy == "boundary":
            add_unique(generate_boundary(alphabet, pumping_constant))
        elif strategy == "random_long":
            add_unique(generate_random_long(alphabet, seed=seed))
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    return all_words
