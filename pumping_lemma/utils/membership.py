"""Oracle wrappers for membership testing."""
from typing import Callable, Optional, Set
from functools import lru_cache


class MembershipOracle:
    """Cached membership oracle for a language."""

    def __init__(self, test_fn: Callable[[str], bool], alphabet: Set[str] = None):
        self._test_fn = test_fn
        self.alphabet = alphabet or set()
        self._cache = {}
        self.query_count = 0

    def __call__(self, word: str) -> bool:
        return self.test(word)

    def test(self, word: str) -> bool:
        if word not in self._cache:
            self.query_count += 1
            self._cache[word] = self._test_fn(word)
        return self._cache[word]

    def batch_test(self, words) -> dict:
        return {w: self.test(w) for w in words}

    def reset_cache(self):
        self._cache.clear()
        self.query_count = 0
