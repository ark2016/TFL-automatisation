"""CFL word generator for oracle testing.

Generates positive (in-language), negative (not-in-language), and
boundary (edge-case) words for various CFL IR language_spec kinds:
  - repeated_subword
  - exists_decomposition
  - grammar
  - grammar_filter
  - predicate
"""

from __future__ import annotations

import itertools
import random
import re
from typing import Any

from cfl_system.lib.parikh import _generate_words_from_grammar

_REV_PATTERN = re.compile(r"^rev\((\w+)\)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_alphabet(ir: dict) -> list[str]:
    """Extract sorted alphabet from IR."""
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "grammar":
        return sorted(spec.get("terminals", []))

    if kind == "grammar_filter":
        g = spec.get("grammar", {})
        return sorted(g.get("terminals", []))

    if kind == "repeated_subword":
        syms: set[str] = set()
        for alpha in spec.get("alphabets", {}).values():
            syms.update(alpha)
        return sorted(syms)

    if kind == "exists_decomposition":
        if "alphabets" in spec:
            syms2: set[str] = set()
            for alpha in spec["alphabets"].values():
                syms2.update(alpha)
            return sorted(syms2)
        # Fallback: try alphabet field
        return sorted(spec.get("alphabet", []))

    if kind == "predicate":
        return sorted(spec.get("alphabet", []))

    return []


def _check_constraints(assignment: dict[str, str], constraints: list[dict]) -> bool:
    """Evaluate constraint predicates against a part assignment.

    Supports:
      - gt: left > right
      - ge: left >= right
      - lt: left < right
      - le: left <= right
      - eq: left == right
      - ne: left != right

    Expression kinds:
      - length: len(assignment[of_var])
      - constant: literal value
      - count_symbol: count of symbol in assignment[in_var]
    """
    _ops = {
        "gt":  (lambda a, b: a > b),
        "ge":  (lambda a, b: a >= b),
        "geq": (lambda a, b: a >= b),
        "lt":  (lambda a, b: a < b),
        "le":  (lambda a, b: a <= b),
        "leq": (lambda a, b: a <= b),
        "eq":  (lambda a, b: a == b),
        "ne":  (lambda a, b: a != b),
        "neq": (lambda a, b: a != b),
    }
    for c in constraints:
        op = c.get("op")
        if op not in _ops:
            # Unknown operator — conservatively reject (was: silently pass)
            return False
        left_val = _eval_expr(c.get("left", {}), assignment)
        right_val = _eval_expr(c.get("right", {}), assignment)
        if left_val is None or right_val is None:
            # Unknown expression kind — conservatively reject
            return False
        if not _ops[op](left_val, right_val):
            return False
    return True


def _eval_expr(expr: dict, assignment: dict[str, str]) -> int | None:
    """Evaluate an expression in a constraint."""
    kind = expr.get("kind")
    if kind == "length":
        var = expr.get("of_var", "")
        return len(assignment.get(var, ""))
    if kind == "constant":
        return expr.get("value")
    if kind == "count_symbol":
        sym = expr.get("symbol", "")
        var = expr.get("in_var", "")
        return assignment.get(var, "").count(sym)
    return None


def _build_word_from_pattern(
    assignment: dict[str, str], pattern: list[str]
) -> str:
    """Build a word from assignment and concat_pattern, handling rev(X)."""
    parts = []
    for seg in pattern:
        m = _REV_PATTERN.match(seg)
        if m:
            inner = m.group(1)
            parts.append(assignment.get(inner, "")[::-1])
        else:
            parts.append(assignment.get(seg, ""))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Positive generators per language_spec kind
# ---------------------------------------------------------------------------

def _generate_repeated_subword(
    spec: dict, max_count: int, max_length: int, seed: int
) -> list[str]:
    """Enumerate words by trying different part values."""
    rng = random.Random(seed)
    parts = spec["parts"]
    alphabets = spec["alphabets"]
    constraints = spec.get("constraints", [])
    pattern = spec["concat_pattern"]

    results: set[str] = set()

    # Strategy 1: systematic — try all part lengths 1..3
    max_part_len = min(3, max(1, max_length // max(len(pattern), 1)))
    part_alpha_lists: list[list[str]] = []
    for p in parts:
        alpha = alphabets[p]
        words: list[str] = []
        for length in range(1, max_part_len + 1):
            for combo in itertools.product(alpha, repeat=length):
                words.append("".join(combo))
                if len(words) > 50:
                    break
            if len(words) > 50:
                break
        part_alpha_lists.append(words)

    count = 0
    for combo in itertools.product(*part_alpha_lists):
        if count > max_count * 5:
            break
        count += 1
        assignment = dict(zip(parts, combo))
        if not _check_constraints(assignment, constraints):
            continue
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length:
            results.add(word)
        if len(results) >= max_count:
            break

    # Strategy 2: random — fill up if systematic didn't produce enough
    for _ in range(max_count * 3):
        if len(results) >= max_count:
            break
        assignment = {}
        for p in parts:
            length = rng.randint(1, min(5, max(1, max_length // max(len(pattern), 1))))
            assignment[p] = "".join(rng.choices(alphabets[p], k=length))
        if not _check_constraints(assignment, constraints):
            continue
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length:
            results.add(word)

    return sorted(results)[:max_count]


def _generate_exists_decomposition(
    spec: dict, max_count: int, max_length: int, seed: int
) -> list[str]:
    """Enumerate decompositions, handling rev(X) in concat_pattern."""
    rng = random.Random(seed)
    parts = spec["parts"]
    alphabets = spec.get("alphabets", {})
    constraints = spec.get("constraints", [])
    pattern = spec["concat_pattern"]

    # Determine alphabet for each part
    part_alphas: dict[str, list[str]] = {}
    for p in parts:
        if p in alphabets:
            part_alphas[p] = alphabets[p]
        else:
            # Default: union of all known alphabets
            syms: set[str] = set()
            for a in alphabets.values():
                syms.update(a)
            part_alphas[p] = sorted(syms) if syms else ["a", "b"]

    results: set[str] = set()

    # Include empty parts case
    assignment = {p: "" for p in parts}
    if _check_constraints(assignment, constraints):
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length:
            results.add(word)

    # Systematic: short parts
    max_part_len = min(3, max(1, max_length // max(len(pattern), 1)))
    part_word_lists: list[list[str]] = []
    for p in parts:
        alpha = part_alphas[p]
        words: list[str] = [""]
        for length in range(1, max_part_len + 1):
            for combo in itertools.product(alpha, repeat=length):
                words.append("".join(combo))
                if len(words) > 30:
                    break
            if len(words) > 30:
                break
        part_word_lists.append(words)

    count = 0
    for combo in itertools.product(*part_word_lists):
        if count > max_count * 5:
            break
        count += 1
        assignment = dict(zip(parts, combo))
        if not _check_constraints(assignment, constraints):
            continue
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length:
            results.add(word)
        if len(results) >= max_count:
            break

    # Random fill
    for _ in range(max_count * 3):
        if len(results) >= max_count:
            break
        assignment = {}
        for p in parts:
            length = rng.randint(0, min(4, max(1, max_length // max(len(pattern), 1))))
            assignment[p] = "".join(rng.choices(part_alphas[p], k=length))
        if not _check_constraints(assignment, constraints):
            continue
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length:
            results.add(word)

    return sorted(results)[:max_count]


def _generate_grammar(
    spec: dict, max_count: int, max_length: int, _seed: int
) -> list[str]:
    """BFS derivation from start symbol."""
    words = _generate_words_from_grammar(spec, max_length)
    return sorted(words)[:max_count]


def _generate_grammar_filter(
    spec: dict, max_count: int, max_length: int, seed: int
) -> list[str]:
    """Generate from grammar, then filter by predicate."""
    grammar = spec.get("grammar", {})
    filt = spec.get("filter", {})

    words = _generate_words_from_grammar(grammar, max_length)

    # Apply filter
    filtered: list[str] = []
    for w in sorted(words):
        if _check_filter(w, filt):
            filtered.append(w)
            if len(filtered) >= max_count:
                break

    return filtered


def _check_filter(word: str, filt: dict) -> bool:
    """Check if a word passes the grammar_filter predicate.

    Natural-language filters are indeterminate: accept everything the
    grammar generates (caller filters by grammar separately).
    Unknown operators are conservatively rejected.
    """
    if not isinstance(filt, dict):
        return True

    # Natural language filter: cannot evaluate, accept all words
    if filt.get("kind") == "natural_language_filter" or "natural_language_filter" in filt:
        return True

    # Boolean combinations
    op = filt.get("op")
    if op in ("and", "or"):
        operands = filt.get("operands", []) or []
        results = [_check_filter(word, o) for o in operands]
        return all(results) if op == "and" else any(results)
    if op == "not":
        operand = filt.get("operand") or (filt.get("operands") or [{}])[0]
        return not _check_filter(word, operand)

    # Modular predicate
    if "modulus" in filt and "remainder" in filt and "expr" in filt:
        val = _eval_expr(filt.get("expr", {}), {"w": word})
        if val is None:
            return False
        try:
            return val % int(filt["modulus"]) == int(filt["remainder"])
        except (TypeError, ValueError):
            return False

    # No op field at all — conservatively reject (was: silently pass)
    if not op:
        return False

    # Build a pseudo-assignment with "w" as the whole word
    assignment = {"w": word}

    left_val = _eval_expr(filt.get("left", {}), assignment)
    right_val = _eval_expr(filt.get("right", {}), assignment)

    if left_val is None or right_val is None:
        return False  # can't evaluate — conservatively reject

    _ops = {
        "eq":  (lambda a, b: a == b),
        "ne":  (lambda a, b: a != b),
        "neq": (lambda a, b: a != b),
        "gt":  (lambda a, b: a > b),
        "ge":  (lambda a, b: a >= b),
        "geq": (lambda a, b: a >= b),
        "lt":  (lambda a, b: a < b),
        "le":  (lambda a, b: a <= b),
        "leq": (lambda a, b: a <= b),
    }
    if op in _ops:
        return _ops[op](left_val, right_val)
    return False  # unknown op


def _generate_predicate(
    spec: dict, max_count: int, max_length: int, _seed: int
) -> list[str]:
    """Exhaustive check of all words up to max_length."""
    alphabet = sorted(spec.get("alphabet", []))
    predicate = spec.get("predicate", {})
    if not alphabet:
        return []

    results: list[str] = []
    # Check empty word
    if _check_filter("", predicate):
        results.append("")

    for length in range(1, max_length + 1):
        if len(results) >= max_count:
            break
        for combo in itertools.product(alphabet, repeat=length):
            word = "".join(combo)
            if _check_filter(word, predicate):
                results.append(word)
                if len(results) >= max_count:
                    break

    return results[:max_count]


# ---------------------------------------------------------------------------
# Negative word generation
# ---------------------------------------------------------------------------

def _simple_membership_check(ir: dict, word: str) -> bool | None:
    """Best-effort membership check without importing external oracle.

    Returns True/False if determinable, None if unknown.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "grammar":
        # Use CYK via CNF
        try:
            from cfl_system.lib.cnf import to_cnf
            from cfl_system.lib.cyk import cyk_parse
            cnf = to_cnf(spec)
            return cyk_parse(cnf, word)
        except Exception:
            return None

    if kind == "grammar_filter":
        grammar = spec.get("grammar", {})
        filt = spec.get("filter", {})
        try:
            from cfl_system.lib.cnf import to_cnf
            from cfl_system.lib.cyk import cyk_parse
            cnf = to_cnf(grammar)
            if not cyk_parse(cnf, word):
                return False
            return _check_filter(word, filt)
        except Exception:
            return None

    if kind == "repeated_subword":
        # Generate positives and check membership
        # This is a heuristic — not exhaustive
        return None

    if kind == "exists_decomposition":
        return None

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_positive_words(
    ir: dict,
    max_count: int = 50,
    max_length: int = 20,
    seed: int = 42,
) -> list[str]:
    """Generate words that SHOULD be in the language.

    Strategy depends on language_spec kind:
    - repeated_subword: enumerate part assignments
    - exists_decomposition: enumerate decompositions
    - grammar: BFS derivation from start symbol
    - grammar_filter: generate from grammar, filter by predicate
    - predicate: exhaustive check of all words up to max_length
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "repeated_subword":
        return _generate_repeated_subword(spec, max_count, max_length, seed)
    if kind == "exists_decomposition":
        return _generate_exists_decomposition(spec, max_count, max_length, seed)
    if kind == "grammar":
        return _generate_grammar(spec, max_count, max_length, seed)
    if kind == "grammar_filter":
        return _generate_grammar_filter(spec, max_count, max_length, seed)
    if kind == "predicate":
        return _generate_predicate(spec, max_count, max_length, seed)

    return []


def generate_negative_words(
    ir: dict,
    max_count: int = 50,
    max_length: int = 20,
    seed: int = 42,
) -> list[str]:
    """Generate words that should NOT be in the language.

    Strategy: generate random words over the alphabet, filter out those
    in the language (using a simple oracle check).
    """
    rng = random.Random(seed)
    alphabet = _get_alphabet(ir)
    if not alphabet:
        return []

    # First get positive words to exclude
    positives = set(generate_positive_words(ir, max_count=200, max_length=max_length, seed=seed))

    candidates: list[str] = []
    attempts = 0
    max_attempts = max_count * 10

    while len(candidates) < max_count and attempts < max_attempts:
        attempts += 1
        length = rng.randint(0, max_length)
        word = "".join(rng.choices(alphabet, k=length))

        # Quick exclusion: skip known positives
        if word in positives:
            continue

        # Try oracle check
        membership = _simple_membership_check(ir, word)
        if membership is True:
            positives.add(word)
            continue
        if membership is False:
            candidates.append(word)
            continue

        # Unknown — use heuristic: if not in positives set, treat as negative
        candidates.append(word)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for w in candidates:
        if w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) >= max_count:
                break

    return result


def generate_boundary_words(ir: dict, max_count: int = 20) -> list[str]:
    """Generate edge-case words: empty string, single chars, minimal words."""
    alphabet = _get_alphabet(ir)
    results: list[str] = []

    # Empty string
    results.append("")

    # Each single character from alphabet
    for ch in alphabet:
        results.append(ch)

    # Two-character combinations
    for a in alphabet:
        for b in alphabet:
            results.append(a + b)
            if len(results) >= max_count:
                break
        if len(results) >= max_count:
            break

    # Minimal positive word (shortest from positives)
    positives = generate_positive_words(ir, max_count=50, max_length=10, seed=0)
    if positives:
        shortest = min(positives, key=len)
        if shortest not in results:
            results.append(shortest)

    return results[:max_count]


def generate_test_words(
    ir: dict,
    max_positive: int = 30,
    max_negative: int = 30,
    max_length: int = 20,
    seed: int = 42,
) -> dict:
    """Generate comprehensive test set.

    Returns:
        {
            "positive": list[str],
            "negative": list[str],
            "boundary": list[str]
        }
    """
    return {
        "positive": generate_positive_words(ir, max_count=max_positive, max_length=max_length, seed=seed),
        "negative": generate_negative_words(ir, max_count=max_negative, max_length=max_length, seed=seed),
        "boundary": generate_boundary_words(ir),
    }
