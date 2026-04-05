"""
CFL Oracle module — build word-membership oracles from CFL IR specs.

Supports all CFL language_spec kinds:
  - predicate: delegates to agent_system oracle or local implementation
  - grammar: CYK-based (to_cnf + cyk_parse)
  - grammar_filter: grammar oracle AND filter predicate
  - repeated_subword: exhaustive split enumeration
  - exists_decomposition: exhaustive decomposition with rev() support
  - pda: PDA simulation
"""

from __future__ import annotations

import re
from typing import Callable

from cfl_system.lib.cnf import to_cnf
from cfl_system.lib.cyk import cyk_parse
from cfl_system.lib.pda_simulator import pda_accepts

# Try importing the base oracle for predicate delegation
try:
    from agent_system.lib.oracle import oracle_from_ir as _base_oracle_from_ir
    from agent_system.lib.oracle import _eval_predicate as _base_eval_predicate
    from agent_system.lib.oracle import _eval_expr as _base_eval_expr
except ImportError:
    _base_oracle_from_ir = None
    _base_eval_predicate = None
    _base_eval_expr = None


# ---------------------------------------------------------------------------
# Local expression / predicate evaluators (fallback if base not available)
# ---------------------------------------------------------------------------

def _eval_expr(expr: dict, env: dict[str, str]) -> int:
    """Evaluate an Expr node given variable bindings.

    Missing variables are treated as empty strings (defensive) to
    avoid crashing the oracle on malformed predicates.
    """
    kind = expr.get("kind")
    if kind == "constant":
        return int(expr.get("value", 0))
    if kind == "length":
        return len(env.get(expr.get("of_var", ""), ""))
    if kind == "count_symbol":
        return env.get(expr.get("in_var", ""), "").count(expr.get("symbol", ""))
    if kind == "count_subword":
        word = env.get(expr.get("in_var", ""), "")
        sub = expr.get("subword", "")
        if not sub:
            return 0
        count, start = 0, 0
        while True:
            idx = word.find(sub, start)
            if idx == -1:
                break
            count += 1
            start = idx + 1
        return count
    # Try base evaluator as fallback (may handle extended kinds)
    if _base_eval_expr is not None:
        try:
            return _base_eval_expr(expr, env)
        except (KeyError, ValueError, TypeError):
            return 0
    return 0


def _eval_predicate(pred: dict, env: dict[str, str], alphabet: list[str]) -> bool:
    """Evaluate a Predicate node."""
    if _base_eval_predicate is not None:
        return _base_eval_predicate(pred, env, alphabet)

    op = pred.get("op")

    # Boolean combination
    if op in ("and", "or", "not"):
        results = [_eval_predicate(o, env, alphabet) for o in pred["operands"]]
        if op == "and":
            return all(results)
        if op == "or":
            return any(results)
        return not results[0]

    # Comparison (support both short and explicit forms)
    if op in ("eq", "neq", "ne", "lt", "leq", "le", "gt", "geq", "ge"):
        left = _eval_expr(pred.get("left", {}), env)
        right = _eval_expr(pred.get("right", {}), env)
        ops = {
            "eq":  lambda a, b: a == b,
            "neq": lambda a, b: a != b,
            "ne":  lambda a, b: a != b,
            "lt":  lambda a, b: a < b,
            "leq": lambda a, b: a <= b,
            "le":  lambda a, b: a <= b,
            "gt":  lambda a, b: a > b,
            "geq": lambda a, b: a >= b,
            "ge":  lambda a, b: a >= b,
        }
        return ops[op](left, right)

    # Modular
    if "modulus" in pred and "remainder" in pred and "expr" in pred:
        val = _eval_expr(pred["expr"], env)
        return val % pred["modulus"] == pred["remainder"]

    # ExistsDecomposition (nested)
    if "parts" in pred and "concat_pattern" in pred:
        return _eval_decomposition(pred, env, alphabet)

    # Palindrome check
    if op in ("is_palindrome", "is_not_palindrome"):
        word = env[pred["var"]]
        is_pal = word == word[::-1]
        return is_pal if op == "is_palindrome" else not is_pal

    raise ValueError(f"Unknown predicate structure: {pred}")


def _eval_decomposition(pred: dict, env: dict[str, str], alphabet: list[str]) -> bool:
    """Evaluate an exists-decomposition predicate by trying all splits."""
    parts = pred["parts"]
    concat_pattern = pred["concat_pattern"]
    constraints = pred.get("constraints", [])
    main_var = next((v for v in env), "w")
    word = env.get(main_var, "")

    def _resolve(elem: str, bindings: dict[str, str]) -> str | None:
        if elem.startswith("rev(") and elem.endswith(")"):
            inner = elem[4:-1]
            if inner in bindings:
                return bindings[inner][::-1]
            return None
        return bindings.get(elem)

    def try_split(pos: int, pat_idx: int, bindings: dict[str, str]) -> bool:
        if pat_idx == len(concat_pattern):
            if pos != len(word):
                return False
            merged = {**env, **bindings}
            return all(_eval_predicate(c, merged, alphabet) for c in constraints)

        elem = concat_pattern[pat_idx]
        resolved = _resolve(elem, bindings)
        if resolved is not None:
            seg_len = len(resolved)
            if pos + seg_len > len(word):
                return False
            if word[pos:pos + seg_len] != resolved:
                return False
            return try_split(pos + seg_len, pat_idx + 1, bindings)

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
# Grammar oracle (CYK-based)
# ---------------------------------------------------------------------------

def grammar_oracle(grammar: dict) -> Callable[[str], bool]:
    """Build oracle from grammar using CYK.

    Converts grammar to CNF once, then uses CYK for each membership query.
    """
    cnf = to_cnf(grammar)

    def oracle(word: str) -> bool:
        return cyk_parse(cnf, word)

    return oracle


# ---------------------------------------------------------------------------
# PDA oracle
# ---------------------------------------------------------------------------

def pda_oracle(pda: dict) -> Callable[[str], bool]:
    """Build oracle from PDA definition."""

    def oracle(word: str) -> bool:
        return pda_accepts(pda, word)

    return oracle


# ---------------------------------------------------------------------------
# Predicate oracle
# ---------------------------------------------------------------------------

def predicate_oracle(spec: dict) -> Callable[[str], bool]:
    """Build oracle from predicate language spec.

    Delegates to agent_system.lib.oracle.oracle_from_ir if available,
    otherwise implements basic predicate evaluation. Returns False
    on malformed predicates instead of crashing.
    """
    if _base_oracle_from_ir is not None:
        ir = {"language_spec": spec}
        try:
            base_oracle = _base_oracle_from_ir(ir)
            def safe_base_oracle(word: str) -> bool:
                try:
                    return base_oracle(word)
                except (KeyError, ValueError, TypeError):
                    return False
            return safe_base_oracle
        except (ValueError, KeyError):
            pass  # fall through to local implementation

    alphabet = spec.get("alphabet", ["a", "b"])
    variable = spec.get("variable", "w")
    predicate = spec.get("predicate", {})

    def oracle(word: str) -> bool:
        env = {variable: word}
        try:
            return _eval_predicate(predicate, env, alphabet)
        except (KeyError, ValueError, TypeError):
            return False

    return oracle


# ---------------------------------------------------------------------------
# Grammar-filter oracle
# ---------------------------------------------------------------------------

def _eval_filter_predicate(pred: dict, word: str) -> bool:
    """Evaluate a filter predicate against a word.

    Supports comparisons involving count_symbol, length, etc.
    """
    env = {"w": word}
    alphabet = list(set(word)) if word else ["a", "b"]
    return _eval_predicate(pred, env, alphabet)


def _grammar_filter_oracle(spec: dict) -> Callable[[str], bool]:
    """Build oracle for grammar_filter kind."""
    g_oracle = grammar_oracle(spec["grammar"])
    filter_spec = spec.get("filter") or {}

    # Natural language filters cannot be evaluated automatically.
    # Canonical: {"kind": "natural_language_filter", "description": "..."}
    # Legacy:    {"natural_language_filter": "..."}
    if (
        filter_spec.get("kind") == "natural_language_filter"
        or "natural_language_filter" in filter_spec
    ):
        # Accept anything the grammar generates; the filter is for humans.
        return g_oracle

    def check(word: str) -> bool:
        if not g_oracle(word):
            return False
        try:
            return _eval_filter_predicate(filter_spec, word)
        except (KeyError, ValueError, TypeError):
            return False

    return check


# ---------------------------------------------------------------------------
# Repeated-subword oracle
# ---------------------------------------------------------------------------

_REV_PATTERN = re.compile(r"^rev\((\w+)\)$")


def _repeated_subword_oracle(spec: dict) -> Callable[[str], bool]:
    """Build oracle for repeated_subword kind.

    For spec like:
        parts: [w1, w2, w3]
        concat_pattern: [w1, w2, w1, w3]
        alphabets: {w1: [a,b], w2: [b,c], w3: [a,c]}
        constraints: [...]

    Checks that the word decomposes as concat_pattern with:
    - same-named parts having the same value
    - each part using only its allowed alphabet
    - all constraints satisfied
    """
    parts = spec["parts"]
    concat_pattern = spec["concat_pattern"]
    alphabets = spec.get("alphabets", {})
    constraints = spec.get("constraints", [])

    def oracle(word: str) -> bool:
        return _try_repeated_split(
            word, 0, 0, {}, parts, concat_pattern, alphabets, constraints
        )

    return oracle


def _try_repeated_split(
    word: str,
    pos: int,
    pat_idx: int,
    bindings: dict[str, str],
    parts: list[str],
    concat_pattern: list[str],
    alphabets: dict[str, list[str]],
    constraints: list[dict],
) -> bool:
    """Recursively try all ways to split word into concat_pattern segments."""
    if pat_idx == len(concat_pattern):
        if pos != len(word):
            return False
        # Check constraints
        env = {"w": word, **bindings}
        alphabet = list(set(word)) if word else ["a", "b"]
        return all(_eval_predicate(c, env, alphabet) for c in constraints)

    part_name = concat_pattern[pat_idx]

    if part_name in bindings:
        # This part is already bound — the segment must match exactly
        expected = bindings[part_name]
        seg_len = len(expected)
        if pos + seg_len > len(word):
            return False
        if word[pos:pos + seg_len] != expected:
            return False
        return _try_repeated_split(
            word, pos + seg_len, pat_idx + 1, bindings,
            parts, concat_pattern, alphabets, constraints,
        )

    # Unbound part — try all possible segment lengths
    allowed = set(alphabets.get(part_name, []))

    for end in range(pos, len(word) + 1):
        seg = word[pos:end]
        # Check alphabet constraint
        if allowed and not all(ch in allowed for ch in seg):
            continue
        new_bindings = {**bindings, part_name: seg}
        if _try_repeated_split(
            word, end, pat_idx + 1, new_bindings,
            parts, concat_pattern, alphabets, constraints,
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Exists-decomposition oracle
# ---------------------------------------------------------------------------

def _exists_decomposition_oracle(spec: dict) -> Callable[[str], bool]:
    """Build oracle for exists_decomposition kind.

    Similar to repeated_subword but concat_pattern can include rev(X) entries.
    """
    parts = spec["parts"]
    concat_pattern = spec["concat_pattern"]
    alphabets = spec.get("alphabets", {})
    constraints = spec.get("constraints", [])

    def oracle(word: str) -> bool:
        return _try_decomposition_split(
            word, 0, 0, {}, parts, concat_pattern, alphabets, constraints
        )

    return oracle


def _resolve_pattern_element(
    elem: str, bindings: dict[str, str]
) -> tuple[str | None, str, bool]:
    """Resolve a concat_pattern element.

    Returns (resolved_value_or_None, part_name, is_reversed).
    """
    m = _REV_PATTERN.match(elem)
    if m:
        part_name = m.group(1)
        if part_name in bindings:
            return bindings[part_name][::-1], part_name, True
        return None, part_name, True
    if elem in bindings:
        return bindings[elem], elem, False
    return None, elem, False


def _try_decomposition_split(
    word: str,
    pos: int,
    pat_idx: int,
    bindings: dict[str, str],
    parts: list[str],
    concat_pattern: list[str],
    alphabets: dict[str, list[str]],
    constraints: list[dict],
) -> bool:
    """Recursively try all ways to split word for exists_decomposition."""
    if pat_idx == len(concat_pattern):
        if pos != len(word):
            return False
        env = {"w": word, **bindings}
        alphabet = list(set(word)) if word else ["a", "b"]
        return all(_eval_predicate(c, env, alphabet) for c in constraints)

    elem = concat_pattern[pat_idx]
    resolved, part_name, is_rev = _resolve_pattern_element(elem, bindings)

    if resolved is not None:
        seg_len = len(resolved)
        if pos + seg_len > len(word):
            return False
        if word[pos:pos + seg_len] != resolved:
            return False
        return _try_decomposition_split(
            word, pos + seg_len, pat_idx + 1, bindings,
            parts, concat_pattern, alphabets, constraints,
        )

    # Unbound part — try all possible segment lengths
    allowed = set(alphabets.get(part_name, []))

    for end in range(pos, len(word) + 1):
        seg = word[pos:end]
        # For rev(X), the actual part value is the reverse of the segment
        actual_val = seg[::-1] if is_rev else seg
        if allowed and not all(ch in allowed for ch in actual_val):
            continue
        new_bindings = {**bindings, part_name: actual_val}
        if _try_decomposition_split(
            word, end, pat_idx + 1, new_bindings,
            parts, concat_pattern, alphabets, constraints,
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API: dispatch by kind
# ---------------------------------------------------------------------------

def cfl_oracle_from_ir(ir: dict) -> Callable[[str], bool]:
    """Build an oracle (word -> bool) from CFL IR.

    Supports all language_spec kinds:
    - predicate: delegates to agent_system's oracle_from_ir
    - grammar: uses CYK (to_cnf + cyk_parse)
    - grammar_filter: grammar oracle AND filter predicate
    - repeated_subword: exhaustive check of part assignments
    - exists_decomposition: exhaustive decomposition check
    """
    spec = ir.get("language_spec")
    if spec is None:
        raise ValueError("IR has no language_spec")

    kind = spec["kind"]

    if kind == "grammar":
        return grammar_oracle(spec)

    if kind == "predicate":
        return predicate_oracle(spec)

    if kind == "grammar_filter":
        return _grammar_filter_oracle(spec)

    if kind == "repeated_subword":
        return _repeated_subword_oracle(spec)

    if kind == "exists_decomposition":
        return _exists_decomposition_oracle(spec)

    # Try delegating to base oracle for other kinds (regex, etc.)
    if _base_oracle_from_ir is not None:
        return _base_oracle_from_ir(ir)

    raise ValueError(f"Unsupported language_spec kind for CFL oracle: {kind}")
