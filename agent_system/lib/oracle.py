"""
Oracle module — compile IR predicates into naive recognizer functions.

Per §4.10.1: for existential quantifiers enumerate all decompositions,
for grammars use exhaustive derivation. Correct but slow —
use only for words up to MAX_TEST_LENGTH.
"""

from __future__ import annotations

from itertools import product as cart_product
from typing import Callable

MAX_TEST_LENGTH = 12


# ---------------------------------------------------------------------------
# Expression evaluators
# ---------------------------------------------------------------------------

def _eval_expr(expr: dict, env: dict[str, str]) -> int:
    """Evaluate an Expr node given variable bindings env: {var_name -> word}."""
    kind = expr["kind"]
    if kind == "constant":
        return expr["value"]
    if kind == "length":
        return len(env[expr["of_var"]])
    if kind == "count_symbol":
        return env[expr["in_var"]].count(expr["symbol"])
    if kind == "count_subword":
        word = env[expr["in_var"]]
        sub = expr["subword"]
        if not sub:
            return 0
        count = 0
        start = 0
        while True:
            idx = word.find(sub, start)
            if idx == -1:
                break
            count += 1
            start = idx + 1  # overlapping counts
        return count
    raise ValueError(f"Unknown expr kind: {kind}")


# ---------------------------------------------------------------------------
# Predicate evaluators
# ---------------------------------------------------------------------------

def _eval_predicate(pred: dict, env: dict[str, str], alphabet: list[str]) -> bool:
    """Evaluate a Predicate node. `alphabet` needed for decomposition enumeration."""
    op = pred.get("op")

    # Boolean combination
    if op in ("and", "or", "not"):
        results = [_eval_predicate(o, env, alphabet) for o in pred["operands"]]
        if op == "and":
            return all(results)
        if op == "or":
            return any(results)
        return not results[0]

    # Comparison
    if op in ("eq", "neq", "lt", "leq", "gt", "geq"):
        left = _eval_expr(pred["left"], env)
        right = _eval_expr(pred["right"], env)
        ops = {"eq": lambda a, b: a == b, "neq": lambda a, b: a != b,
               "lt": lambda a, b: a < b, "leq": lambda a, b: a <= b,
               "gt": lambda a, b: a > b, "geq": lambda a, b: a >= b}
        return ops[op](left, right)

    # Modular
    if "modulus" in pred and "remainder" in pred and "expr" in pred:
        val = _eval_expr(pred["expr"], env)
        return val % pred["modulus"] == pred["remainder"]

    # ExistsDecomposition
    if "parts" in pred and "concat_pattern" in pred:
        return _eval_exists_decomposition(pred, env, alphabet)

    # Substring check
    if op in ("is_substring", "is_not_substring"):
        sub_val = env.get(pred["substring_expr"], pred["substring_expr"])
        word = env[pred["in_var"]]
        found = sub_val in word
        return found if op == "is_substring" else not found

    # Prefix check
    if op in ("starts_with", "not_starts_with"):
        prefix_val = env.get(pred["prefix_expr"], pred["prefix_expr"])
        word = env[pred["of_var"]]
        starts = word.startswith(prefix_val)
        return starts if op == "starts_with" else not starts

    # Palindrome check
    if op in ("is_palindrome", "is_not_palindrome"):
        word = env[pred["var"]]
        is_pal = word == word[::-1]
        return is_pal if op == "is_palindrome" else not is_pal

    raise ValueError(f"Unknown predicate structure: {pred}")


def _resolve_concat_element(elem: str, part_bindings: dict[str, str]) -> str | None:
    """Resolve one element of concat_pattern.

    Returns the string value, or None if 'rev(x)' references an unbound part.
    """
    if elem.startswith("rev(") and elem.endswith(")"):
        inner = elem[4:-1]
        if inner in part_bindings:
            return part_bindings[inner][::-1]
        return None
    return part_bindings.get(elem)


def _eval_exists_decomposition(pred: dict, env: dict[str, str], alphabet: list[str]) -> bool:
    """Evaluate ExistsDecomposition by enumerating all split positions.

    w = concat_pattern assembled from parts.
    We try all ways to split env["w"] (the main variable) into the parts.
    """
    parts = pred["parts"]
    concat_pattern = pred["concat_pattern"]
    constraints = pred.get("constraints", [])
    main_var = next((v for v in env), "w")
    word = env.get(main_var, "")

    # We need to enumerate all ways to assign substrings to `parts` such that
    # concatenation of resolved concat_pattern equals `word`.
    # Strategy: recursive search over split points.

    def try_split(pos: int, pat_idx: int, bindings: dict[str, str]) -> bool:
        if pat_idx == len(concat_pattern):
            if pos != len(word):
                return False
            # All pattern elements consumed, check constraints
            merged_env = {**env, **bindings}
            return all(_eval_predicate(c, merged_env, alphabet) for c in constraints)

        elem = concat_pattern[pat_idx]

        # If elem is a known part already bound, or rev(known), its length is fixed
        resolved = _resolve_concat_element(elem, bindings)
        if resolved is not None:
            seg_len = len(resolved)
            if pos + seg_len > len(word):
                return False
            if word[pos:pos + seg_len] != resolved:
                return False
            return try_split(pos + seg_len, pat_idx + 1, bindings)

        # elem is an unbound part name — try all lengths
        is_rev = elem.startswith("rev(") and elem.endswith(")")
        part_name = elem[4:-1] if is_rev else elem

        for end in range(pos, len(word) + 1):
            seg = word[pos:end]
            actual_val = seg[::-1] if is_rev else seg
            new_bindings = {**bindings, part_name: actual_val}
            if try_split(end, pat_idx + 1, new_bindings):
                return True
        return False

    return try_split(0, 0, {})


# ---------------------------------------------------------------------------
# Grammar oracle (exhaustive derivation up to bounded length)
# ---------------------------------------------------------------------------

def _grammar_oracle(spec: dict, max_len: int = MAX_TEST_LENGTH) -> Callable[[str], bool]:
    """Build oracle for a grammar by generating all words up to max_len."""
    rules: list[tuple[str, list[str]]] = [(r["lhs"], r["rhs"]) for r in spec["rules"]]
    start = spec["start"]
    terminals = set(spec["terminals"])

    generated: set[str] = set()

    def derive(sentential_forms: set[tuple[str, ...]], depth: int) -> None:
        if depth > max_len * 2 + 10:
            return
        next_forms: set[tuple[str, ...]] = set()
        for form in sentential_forms:
            # Check if terminal
            word = "".join(form)
            if all(s in terminals for s in form) and len(word) <= max_len:
                generated.add(word)
                continue
            if len(word) > max_len:
                continue
            # Try expanding first non-terminal
            for i, sym in enumerate(form):
                if sym not in terminals:
                    for lhs, rhs in rules:
                        if lhs == sym:
                            new_form = form[:i] + tuple(rhs) + form[i + 1:]
                            total_len = sum(1 for s in new_form if s in terminals)
                            nt_count = sum(1 for s in new_form if s not in terminals)
                            if total_len <= max_len and nt_count + total_len <= max_len * 3:
                                next_forms.add(new_form)
                    break  # only expand first NT (leftmost derivation)

        if next_forms:
            derive(next_forms, depth + 1)

    derive({(start,)}, 0)

    def oracle(word: str) -> bool:
        if len(word) > max_len:
            raise ValueError(f"Word length {len(word)} exceeds grammar oracle limit {max_len}")
        return word in generated

    return oracle


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def oracle_from_ir(ir: dict) -> Callable[[str], bool]:
    """Compile IR into a recognizer function word -> bool.

    Handles:
    - predicate languages (category A)
    - grammar languages (category B)
    - regex languages (basic, no backreferences)

    Raises ValueError for unsupported language specs.
    """
    spec = ir.get("language_spec")
    if spec is None:
        raise ValueError("IR has no language_spec")

    kind = spec["kind"]

    if kind == "predicate":
        alphabet = spec["alphabet"]
        variable = spec.get("variable", "w")
        predicate = spec["predicate"]

        def oracle(word: str) -> bool:
            env = {variable: word}
            return _eval_predicate(predicate, env, alphabet)

        return oracle

    if kind == "grammar":
        return _grammar_oracle(spec)

    if kind == "regex" and not spec.get("has_backreferences", False):
        import re
        pattern = spec["pattern"]
        compiled = re.compile(f"^(?:{pattern})$")

        def oracle(word: str) -> bool:
            return compiled.match(word) is not None

        return oracle

    raise ValueError(f"Unsupported language_spec kind for oracle: {kind}")
