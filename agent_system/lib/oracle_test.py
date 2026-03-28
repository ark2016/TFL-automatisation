"""
Oracle testing module — compare DFA against oracle recognizer.

Per §4.10.4 and §4.10.5.
"""

from __future__ import annotations

from typing import Any, Callable

from .dfa_runner import run_dfa
from .word_generator import generate_test_words


def oracle_test(
    oracle: Callable[[str], bool],
    dfa: dict[str, Any],
    alphabet: list[str],
    strategies: list[str] | None = None,
    max_exhaustive: int = 8,
) -> dict[str, Any]:
    """Test a DFA against an oracle on generated words.

    Returns:
    {
        "status": "pass" | "fail",
        "tested": int,
        "passed": int,
        "counterexample": null | {
            "word": str,
            "oracle_says": bool,
            "automaton_says": bool
        }
    }
    """
    if strategies is None:
        strategies = ["exhaustive_k"]

    words = generate_test_words(alphabet, strategies=strategies, max_exhaustive=max_exhaustive)

    tested = 0
    passed = 0

    for word in words:
        tested += 1
        oracle_result = oracle(word)
        dfa_result = run_dfa(dfa, word)

        if oracle_result == dfa_result:
            passed += 1
        else:
            return {
                "status": "fail",
                "tested": tested,
                "passed": passed,
                "counterexample": {
                    "word": word,
                    "oracle_says": oracle_result,
                    "automaton_says": dfa_result,
                },
            }

    return {
        "status": "pass",
        "tested": tested,
        "passed": passed,
        "counterexample": None,
    }


def differential_test(
    dfa1: dict[str, Any],
    dfa2: dict[str, Any],
    alphabet: list[str],
    max_len: int = 10,
) -> dict[str, Any]:
    """Compare two DFAs on all words up to max_len.

    Returns:
    {
        "status": "identical" | "diverge",
        "tested": int,
        "first_divergence": null | {
            "word": str,
            "dfa1_says": bool,
            "dfa2_says": bool
        },
        "total_divergences": int
    }
    """
    words = generate_test_words(alphabet, strategies=["exhaustive_k"], max_exhaustive=max_len)

    tested = 0
    total_divergences = 0
    first_divergence = None

    for word in words:
        tested += 1
        r1 = run_dfa(dfa1, word)
        r2 = run_dfa(dfa2, word)

        if r1 != r2:
            total_divergences += 1
            if first_divergence is None:
                first_divergence = {
                    "word": word,
                    "dfa1_says": r1,
                    "dfa2_says": r2,
                }

    return {
        "status": "diverge" if total_divergences > 0 else "identical",
        "tested": tested,
        "first_divergence": first_divergence,
        "total_divergences": total_divergences,
    }
