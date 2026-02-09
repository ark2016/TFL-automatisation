"""Word generation strategies for testing languages."""
from typing import List, Set, Optional, Iterator
from itertools import product
import re


def generate_all_words(alphabet: Set[str], max_length: int, max_words: int = 50000) -> Iterator[str]:
    """Generate all words over alphabet up to max_length, ordered by length.

    Stops after max_words to prevent combinatorial explosion.
    """
    alpha = sorted(alphabet)
    count = 0
    for length in range(max_length + 1):
        for combo in product(alpha, repeat=length):
            if count >= max_words:
                return
            yield ''.join(combo)
            count += 1


def generate_words_of_length(alphabet: Set[str], length: int) -> Iterator[str]:
    """Generate all words of exactly given length."""
    alpha = sorted(alphabet)
    for combo in product(alpha, repeat=length):
        yield ''.join(combo)


def generate_pumping_splits(word: str, pumping_constant: int):
    """Generate all valid splits xyz where |xy| <= p and |y| > 0."""
    n = len(word)
    p = min(pumping_constant, n)
    for xy_end in range(1, p + 1):
        for x_end in range(0, xy_end):
            x = word[:x_end]
            y = word[x_end:xy_end]
            z = word[xy_end:]
            yield x, y, z


def pump_word(x: str, y: str, z: str, i: int) -> str:
    """Construct xy^iz."""
    return x + y * i + z


def generate_parametric_words(alphabet: Set[str], pattern: str, params: range) -> List[str]:
    """Generate words from parametric pattern.
    Patterns: 'a^n b^n', 'a^n', 'a^n b^n c^n', '(ab)^n', etc.
    """
    alpha = sorted(alphabet)
    words = []
    # Parse pattern like "a^n b^n"
    parts = pattern.strip().split()
    for n in params:
        word = ""
        for part in parts:
            m = re.match(r'(\(?.+?\)?\^?)n', part) or re.match(r'(.+)\^n', part)
            if m:
                base = m.group(1).strip('()')
                word += base * n
            elif '^' in part:
                sym, exp = part.split('^')
                sym = sym.strip('()')
                word += sym * int(exp)
            else:
                word += part
        words.append(word)
    return words


def generate_critical_words(alphabet: Set[str], structural_pattern: Optional[str],
                           min_length: int, max_length: int) -> List[str]:
    """Generate words likely to be useful for pumping lemma analysis."""
    alpha = sorted(alphabet)
    words = []
    if not alpha:
        return words

    a = alpha[0]
    b = alpha[1] if len(alpha) > 1 else a

    for n in range(min_length, max_length + 1):
        # Single symbol repetitions
        words.append(a * n)
        if a != b:
            words.append(b * n)
            # Balanced words
            words.append(a * n + b * n)
            # Alternating
            words.append((a + b) * (n // 2))

    if structural_pattern == "balanced":
        for n in range(min_length // 2, max_length // 2 + 1):
            words.append(a * n + b * n)
    elif structural_pattern == "palindrome":
        for n in range(min_length, max_length + 1):
            half = a * (n // 2)
            words.append(half + half[::-1])
            if n % 2:
                words.append(half + a + half[::-1])
    elif structural_pattern == "power":
        i = 1
        while i ** 2 <= max_length:
            words.append(a * (i ** 2))
            i += 1

    return words
