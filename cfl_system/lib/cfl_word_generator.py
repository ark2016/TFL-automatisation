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
# Shared oracle helper
# ---------------------------------------------------------------------------

def _try_build_oracle(ir: dict):
    """Try to build the real oracle for an IR; return None on failure."""
    try:
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
        return cfl_oracle_from_ir(ir)
    except Exception:
        return None


def _safe_oracle_check(oracle, word: str) -> bool:
    """Call oracle(word) with exception handling."""
    if oracle is None:
        return False
    try:
        return bool(oracle(word))
    except Exception:
        return False


def _length_equalities(parts: list[str], constraints: list[dict]) -> list[set[str]]:
    """Find groups of parts that must have equal length.

    Scans `length(x) == length(y)` constraints inside top-level ANDs
    and returns connected-component groups.
    """
    groups: list[set[str]] = []

    def find_group(p: str) -> set[str] | None:
        for g in groups:
            if p in g:
                return g
        return None

    def merge(p: str, q: str) -> None:
        gp = find_group(p)
        gq = find_group(q)
        if gp and gq:
            if gp is not gq:
                gp.update(gq)
                groups.remove(gq)
        elif gp:
            gp.add(q)
        elif gq:
            gq.add(p)
        else:
            groups.append({p, q})

    def walk(pred: dict) -> None:
        if not isinstance(pred, dict):
            return
        op = pred.get("op")
        if op == "and":
            for sub in pred.get("operands", []) or []:
                walk(sub)
            return
        if op == "eq":
            left = pred.get("left", {}) or {}
            right = pred.get("right", {}) or {}
            if left.get("kind") == "length" and right.get("kind") == "length":
                lv = left.get("of_var")
                rv = right.get("of_var")
                if lv in parts and rv in parts and lv != rv:
                    merge(lv, rv)

    for c in constraints or []:
        walk(c)
    return groups


def _min_part_lengths(parts: list[str], constraints: list[dict]) -> dict[str, int]:
    """Scan constraints to find minimum required length per part.

    Recognizes patterns like:
      - length(p) > k  →  min(p) = k + 1
      - length(p) >= k → min(p) = k
      - length(p) = k  →  min(p) = k
      - count_symbol(s, p) > k → min(p) = k + 1 (symbol must fit)
      - count_symbol(s, p) >= k → min(p) = k
    Boolean combinations:
      - and: take max of sub-bounds (all must hold)
      - or: take min of sub-bounds (weakest must hold — conservative 0)
      - not: ignore (can't easily invert)
    """
    mins: dict[str, int] = {p: 0 for p in parts}

    def walk(pred: dict, positive: bool) -> None:
        if not isinstance(pred, dict):
            return
        op = pred.get("op")

        if op == "and":
            for sub in pred.get("operands", []) or []:
                walk(sub, positive)
            return
        if op == "or":
            # Can't tighten bounds from disjunction (conservatively skip)
            return
        if op == "not":
            operand = pred.get("operand")
            if operand is None:
                ops = pred.get("operands", []) or []
                operand = ops[0] if ops else None
            if operand is not None:
                walk(operand, not positive)
            return

        if op in ("gt", "ge", "geq", "lt", "le", "leq", "eq"):
            left = pred.get("left", {}) or {}
            right = pred.get("right", {}) or {}
            # Find (var, constant) pair
            var_side = None
            const_side = None
            kind_l = left.get("kind")
            kind_r = right.get("kind")
            if kind_l == "length" and kind_r == "constant":
                var_side = left.get("of_var")
                const_side = right.get("value")
                kind_op = op
            elif kind_r == "length" and kind_l == "constant":
                var_side = right.get("of_var")
                const_side = left.get("value")
                # Flip op direction
                kind_op = {"gt": "lt", "lt": "gt", "ge": "le", "geq": "leq",
                           "le": "ge", "leq": "geq", "eq": "eq"}.get(op, op)
            elif kind_l == "count_symbol" and kind_r == "constant":
                var_side = left.get("in_var")
                const_side = right.get("value")
                kind_op = op
            elif kind_r == "count_symbol" and kind_l == "constant":
                var_side = right.get("in_var")
                const_side = left.get("value")
                kind_op = {"gt": "lt", "lt": "gt", "ge": "le", "geq": "leq",
                           "le": "ge", "leq": "geq", "eq": "eq"}.get(op, op)
            else:
                return

            if var_side is None or not isinstance(const_side, int):
                return
            if var_side not in mins:
                return
            if not positive:
                return  # conservative: skip negated bounds

            if kind_op == "gt":
                mins[var_side] = max(mins[var_side], const_side + 1)
            elif kind_op in ("ge", "geq", "eq"):
                mins[var_side] = max(mins[var_side], const_side)
            # lt / le: upper bounds, not lower — ignored for min

    for c in constraints or []:
        walk(c, True)

    # Propagate mins across equality groups: all parts in a length-equality
    # group must have the same length, so lift to the group's max min.
    groups = _length_equalities(parts, constraints)
    for g in groups:
        group_max = max((mins.get(p, 0) for p in g), default=0)
        for p in g:
            mins[p] = max(mins.get(p, 0), group_max)

    return mins


def _generate_constrained_assignments(
    parts: list[str],
    alphabets: dict[str, list[str]],
    part_mins: dict[str, int],
    length_groups: list[set[str]],
    max_word_len: int,
    count: int,
    rng: random.Random,
) -> list[dict[str, str]]:
    """Produce `count` random assignments that honor per-part mins and
    length-equality groups.

    For each assignment we pick lengths per variable respecting:
      - length >= part_mins[var]
      - all vars in the same length-group share a single length
      - total word length stays within max_word_len
    """
    # Map each var to its group representative (itself if no group)
    group_of: dict[str, set[str]] = {}
    for g in length_groups:
        for p in g:
            group_of[p] = g

    results: list[dict[str, str]] = []
    attempts = 0
    max_attempts = count * 20

    while len(results) < count and attempts < max_attempts:
        attempts += 1
        assignment: dict[str, str] = {}
        group_lengths: dict[int, int] = {}  # id(group) -> chosen length
        success = True

        # Estimate per-part length budget
        slot_budget = max(1, max_word_len // max(len(parts), 1))

        for p in parts:
            if p in assignment:
                continue
            p_min = part_mins.get(p, 0)
            if p in group_of:
                g = group_of[p]
                gid = id(g)
                if gid in group_lengths:
                    length = group_lengths[gid]
                else:
                    # Group hasn't been assigned a length yet
                    group_min = max((part_mins.get(q, 0) for q in g), default=0)
                    upper = max(group_min, min(slot_budget, max_word_len))
                    if group_min > upper:
                        success = False
                        break
                    length = rng.randint(group_min, upper) if upper > group_min else group_min
                    group_lengths[gid] = length
                alpha = alphabets.get(p, ["a"])
                word = "".join(rng.choices(alpha, k=length)) if length > 0 else ""
                # Assign the same length to all group members
                for q in g:
                    if q not in assignment:
                        alpha_q = alphabets.get(q, ["a"])
                        assignment[q] = (
                            "".join(rng.choices(alpha_q, k=length))
                            if length > 0 else ""
                        )
            else:
                upper = max(p_min, min(slot_budget, max_word_len))
                length = rng.randint(p_min, upper) if upper > p_min else p_min
                alpha = alphabets.get(p, ["a"])
                assignment[p] = (
                    "".join(rng.choices(alpha, k=length)) if length > 0 else ""
                )

        if success:
            results.append(assignment)

    return results


# Regex metacharacters and char-class shorthand markers
_REGEX_META = set("()[]{}.*+?|^$\\")


def _infer_regex_alphabet(pattern: str) -> list[str]:
    """Best-effort alphabet inference from a regex pattern.

    Returns a sorted list of characters the pattern can produce.
    For unrestricted wildcards (., negated classes), returns an empty
    list — the caller should treat this as "alphabet unknown" and
    avoid generating words.
    """
    if not pattern:
        return []

    alphabet: set[str] = set()
    i = 0
    n = len(pattern)
    unrestricted = False  # True if any . or [^...] makes alphabet indeterminate
    in_class = False
    class_body: list[str] = []

    while i < n:
        c = pattern[i]

        if in_class:
            if c == "]":
                body = "".join(class_body)
                if body.startswith("^"):
                    # Negated class — alphabet is indeterminate
                    unrestricted = True
                else:
                    alphabet.update(_expand_char_class(body))
                class_body = []
                in_class = False
            else:
                class_body.append(c)
            i += 1
            continue

        if c == "[":
            in_class = True
            i += 1
            continue

        if c == "\\" and i + 1 < n:
            nxt = pattern[i + 1]
            if nxt == "d":
                alphabet.update("0123456789")
            elif nxt == "w":
                alphabet.update("abcdefghijklmnopqrstuvwxyz"
                                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
            elif nxt == "D":  # non-digit — indeterminate
                unrestricted = True
            elif nxt == "W":  # non-word — indeterminate
                unrestricted = True
            elif nxt == "s":
                pass  # whitespace — skip
            elif nxt == "S":
                unrestricted = True
            elif nxt in _REGEX_META:
                alphabet.add(nxt)
            else:
                alphabet.add(nxt)
            i += 2
            continue

        if c == ".":
            # Wildcard — matches any char; alphabet is indeterminate
            unrestricted = True
            i += 1
            continue

        if c in _REGEX_META:
            i += 1
            continue

        # Literal character (alphanumeric or symbol like #, @, $, _)
        alphabet.add(c)
        i += 1

    if unrestricted:
        # Any wildcard or negated class means the full alphabet is
        # indeterminate. A partial literal set would produce an under-
        # approximation and lead to false passes in oracle_test. Return
        # empty to signal "cannot determine".
        return []
    return sorted(alphabet)


def _expand_char_class(body: str) -> set[str]:
    """Expand the body of a [...] char class into a set of characters.

    Handles ranges like a-z, escaped chars, and \\d/\\w shorthand.
    """
    chars: set[str] = set()
    i = 0
    n = len(body)
    # Skip leading ^ (negation) — we conservatively ignore negation
    if n > 0 and body[0] == "^":
        i = 1

    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            nxt = body[i + 1]
            if nxt == "d":
                chars.update("0123456789")
            elif nxt == "w":
                chars.update("abcdefghijklmnopqrstuvwxyz"
                             "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
            elif nxt == "s":
                pass
            else:
                chars.add(nxt)
            i += 2
            continue
        # Range: c-c2
        if i + 2 < n and body[i + 1] == "-" and body[i + 2] != "]":
            start, end = c, body[i + 2]
            try:
                for code in range(ord(start), ord(end) + 1):
                    chars.add(chr(code))
            except (TypeError, ValueError):
                chars.add(c)
                chars.add(end)
            i += 3
            continue
        chars.add(c)
        i += 1

    return chars


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

    if kind == "regex":
        declared = spec.get("alphabet")
        if declared:
            return sorted(declared)
        return _infer_regex_alphabet(spec.get("pattern", ""))

    if kind == "natural":
        # "natural" describes the language in prose — no automated alphabet.
        # Only use an explicitly declared alphabet; otherwise return empty
        # so word generation doesn't fabricate misleading examples.
        return sorted(spec.get("alphabet") or [])

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
    """Enumerate words by trying different part values.

    Candidate words are filtered through the real oracle (which supports
    the full constraint language); falls back to the local _check_constraints
    if the oracle cannot be built. Part-length ranges are adapted to any
    lower bounds extracted from constraints (so |x| >= 6 is honored).
    """
    rng = random.Random(seed)
    parts = spec["parts"]
    alphabets = spec["alphabets"]
    constraints = spec.get("constraints", [])
    pattern = spec["concat_pattern"]

    # Compute minimum part lengths from constraints + length equalities
    part_mins = _min_part_lengths(parts, constraints)
    length_groups = _length_equalities(parts, constraints)

    # Build the real oracle once to validate candidates
    oracle = _try_build_oracle({"language_spec": spec})

    def accept(assignment: dict[str, str], word: str) -> bool:
        if oracle is not None:
            return _safe_oracle_check(oracle, word)
        return _check_constraints(assignment, constraints)

    results: set[str] = set()

    # Strategy 0: constrained random assignments for tricky constraints
    # (large mins or cross-variable equalities). Runs first so hard
    # cases always get some coverage.
    if length_groups or any(m > 3 for m in part_mins.values()):
        constrained = _generate_constrained_assignments(
            parts, alphabets, part_mins, length_groups,
            max_length, max_count * 3, rng,
        )
        for assignment in constrained:
            word = _build_word_from_pattern(assignment, pattern)
            if len(word) <= max_length and accept(assignment, word):
                results.add(word)
                if len(results) >= max_count:
                    break

    # Strategy 1: systematic — try part lengths [min..min+3] honoring constraints
    base_max = min(3, max(1, max_length // max(len(pattern), 1)))
    part_alpha_lists: list[list[str]] = []
    for p in parts:
        alpha = alphabets[p]
        p_min = part_mins.get(p, 0)
        p_max = p_min + base_max
        # Also cap by total word length budget
        p_max = min(p_max, max_length)
        words: list[str] = []
        if p_min == 0:
            words.append("")
        for length in range(max(1, p_min), p_max + 1):
            for combo in itertools.product(alpha, repeat=length):
                words.append("".join(combo))
                if len(words) > 80:
                    break
            if len(words) > 80:
                break
        if not words:
            words = [""]  # safety
        part_alpha_lists.append(words)

    count = 0
    for combo in itertools.product(*part_alpha_lists):
        if count > max_count * 10:
            break
        count += 1
        assignment = dict(zip(parts, combo))
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) > max_length:
            continue
        if accept(assignment, word):
            results.add(word)
        if len(results) >= max_count:
            break

    # Strategy 2: random — fill up if systematic didn't produce enough
    rand_budget = max_count * 10
    for _ in range(rand_budget):
        if len(results) >= max_count:
            break
        assignment = {}
        for p in parts:
            p_min = part_mins.get(p, 0)
            p_max_rand = max(p_min + 5, min(10, max_length // max(len(pattern), 1)))
            length = rng.randint(p_min, p_max_rand)
            assignment[p] = "".join(rng.choices(alphabets[p], k=length)) if length > 0 else ""
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) > max_length:
            continue
        if accept(assignment, word):
            results.add(word)

    return sorted(results)[:max_count]


def _generate_exists_decomposition(
    spec: dict, max_count: int, max_length: int, seed: int
) -> list[str]:
    """Enumerate decompositions, handling rev(X) in concat_pattern.

    Filters candidates through the real oracle so full predicate
    constraints are honored. Part-length ranges are adapted to any
    lower bounds extracted from constraints.
    """
    rng = random.Random(seed)
    parts = spec["parts"]
    alphabets = spec.get("alphabets", {})
    constraints = spec.get("constraints", [])
    pattern = spec["concat_pattern"]

    part_mins = _min_part_lengths(parts, constraints)
    length_groups = _length_equalities(parts, constraints)
    oracle = _try_build_oracle({"language_spec": spec})

    def accept(assignment: dict[str, str], word: str) -> bool:
        if oracle is not None:
            return _safe_oracle_check(oracle, word)
        return _check_constraints(assignment, constraints)

    # Determine alphabet for each part
    part_alphas: dict[str, list[str]] = {}
    for p in parts:
        if p in alphabets:
            part_alphas[p] = alphabets[p]
        else:
            syms: set[str] = set()
            for a in alphabets.values():
                syms.update(a)
            part_alphas[p] = sorted(syms) if syms else ["a", "b"]

    results: set[str] = set()

    # Strategy 0: constrained random for large mins or equality groups
    if length_groups or any(m > 3 for m in part_mins.values()):
        constrained = _generate_constrained_assignments(
            parts, part_alphas, part_mins, length_groups,
            max_length, max_count * 3, rng,
        )
        for assignment in constrained:
            word = _build_word_from_pattern(assignment, pattern)
            if len(word) <= max_length and accept(assignment, word):
                results.add(word)
                if len(results) >= max_count:
                    break

    # Include empty parts case (only if no parts require length > 0)
    if all(part_mins.get(p, 0) == 0 for p in parts):
        assignment = {p: "" for p in parts}
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) <= max_length and accept(assignment, word):
            results.add(word)

    # Systematic: short parts honoring per-part lower bounds
    base_max = min(3, max(1, max_length // max(len(pattern), 1)))
    part_word_lists: list[list[str]] = []
    for p in parts:
        alpha = part_alphas[p]
        p_min = part_mins.get(p, 0)
        p_max = min(p_min + base_max, max_length)
        words: list[str] = []
        if p_min == 0:
            words.append("")
        for length in range(max(1, p_min), p_max + 1):
            for combo in itertools.product(alpha, repeat=length):
                words.append("".join(combo))
                if len(words) > 50:
                    break
            if len(words) > 50:
                break
        if not words:
            words = [""]
        part_word_lists.append(words)

    count = 0
    for combo in itertools.product(*part_word_lists):
        if count > max_count * 10:
            break
        count += 1
        assignment = dict(zip(parts, combo))
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) > max_length:
            continue
        if accept(assignment, word):
            results.add(word)
        if len(results) >= max_count:
            break

    # Random fill honoring minimums
    for _ in range(max_count * 10):
        if len(results) >= max_count:
            break
        assignment = {}
        for p in parts:
            p_min = part_mins.get(p, 0)
            p_max_rand = max(p_min + 4, min(10, max_length // max(len(pattern), 1)))
            length = rng.randint(p_min, p_max_rand)
            assignment[p] = "".join(rng.choices(part_alphas[p], k=length)) if length > 0 else ""
        word = _build_word_from_pattern(assignment, pattern)
        if len(word) > max_length:
            continue
        if accept(assignment, word):
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
    """Generate from grammar, then filter by the real oracle.

    If the filter is natural-language (oracle is marked approximate),
    we cannot produce trustworthy positive words, so return empty.
    """
    grammar = spec.get("grammar", {})
    filt = spec.get("filter", {})

    # Prefer the real oracle; fall back to local _check_filter
    oracle = _try_build_oracle({"language_spec": spec})

    # If the oracle is approximate (NL filter), do NOT publish
    # "positive" words — they would be only grammar matches, not
    # verified against the filter.
    if oracle is not None and getattr(oracle, "is_approximate", False):
        return []

    words = _generate_words_from_grammar(grammar, max_length)

    filtered: list[str] = []
    for w in sorted(words):
        in_lang = (
            _safe_oracle_check(oracle, w) if oracle is not None
            else _check_filter(w, filt)
        )
        if in_lang:
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
    """Exhaustive check of all words up to max_length using the real oracle."""
    alphabet = sorted(spec.get("alphabet", []))
    if not alphabet:
        return []

    oracle = _try_build_oracle({"language_spec": spec})

    def check(word: str) -> bool:
        if oracle is not None:
            return _safe_oracle_check(oracle, word)
        return _check_filter(word, spec.get("predicate", {}))

    return _exhaustive_filter(alphabet, max_count, max_length, check)


def _generate_regex(
    spec: dict, max_count: int, max_length: int, _seed: int
) -> list[str]:
    """Generate words matching a regex IR by exhaustive enumeration + oracle filter."""
    alphabet = sorted(spec.get("alphabet") or [])
    if not alphabet:
        alphabet = _infer_regex_alphabet(spec.get("pattern", ""))
    if not alphabet:
        # No alphabet inferrable — return empty rather than guessing
        return []

    oracle = _try_build_oracle({"language_spec": spec})
    if oracle is None:
        return []

    def check(word: str) -> bool:
        return _safe_oracle_check(oracle, word)

    return _exhaustive_filter(alphabet, max_count, max_length, check)


def _exhaustive_filter(
    alphabet: list[str],
    max_count: int,
    max_length: int,
    check: Any,
    time_budget_sec: float = 5.0,
    max_enumerations: int = 200_000,
) -> list[str]:
    """Enumerate all words up to max_length and keep those satisfying check().

    Protected by both a time budget and an enumeration cap to avoid
    pathological hangs on large alphabets + sparse languages.
    """
    import time as _t
    start = _t.monotonic()
    enumerated = 0

    results: list[str] = []
    if check(""):
        results.append("")
    enumerated += 1

    for length in range(1, max_length + 1):
        if len(results) >= max_count:
            break
        # Early exit: alphabet^length can explode quickly
        try:
            est = len(alphabet) ** length
        except OverflowError:
            est = max_enumerations + 1
        if est > max_enumerations and enumerated > max_enumerations // 2:
            break
        for combo in itertools.product(alphabet, repeat=length):
            enumerated += 1
            if enumerated > max_enumerations:
                return results[:max_count]
            if enumerated % 2000 == 0:
                if _t.monotonic() - start > time_budget_sec:
                    return results[:max_count]
            word = "".join(combo)
            if check(word):
                results.append(word)
                if len(results) >= max_count:
                    break

    return results[:max_count]


# ---------------------------------------------------------------------------
# Negative word generation
# ---------------------------------------------------------------------------

def _simple_membership_check(ir: dict, word: str) -> bool | None:
    """Best-effort membership check.

    Delegates to the real cfl_oracle when possible; falls back to local
    CYK for grammar/grammar_filter if oracle construction fails.
    Returns True/False if determinable, None if unknown.
    """
    # Primary: real oracle (handles all IR kinds including predicate,
    # repeated_subword, exists_decomposition, palindrome, substring, ...)
    try:
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
        oracle = cfl_oracle_from_ir(ir)
        return bool(oracle(word))
    except Exception:
        pass

    # Fallback: local CYK for grammar-based kinds
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "grammar":
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
    if kind == "regex":
        return _generate_regex(spec, max_count, max_length, seed)

    # natural, arithmetic_index: no automated membership check
    return []


def generate_negative_words(
    ir: dict,
    max_count: int = 50,
    max_length: int = 20,
    seed: int = 42,
) -> list[str]:
    """Generate words that should NOT be in the language.

    Strategy: generate random words over the alphabet, verify via oracle
    that they are NOT in the language. Only returns words where
    membership is actually determinable as False. If no oracle is
    available (e.g. natural kind, approximate NL-filter oracle), returns
    empty rather than fabricating "negative" examples.
    """
    rng = random.Random(seed)
    spec = ir.get("language_spec", {}) if isinstance(ir, dict) else {}
    kind = spec.get("kind") if isinstance(spec, dict) else None

    # Kinds without an automated oracle cannot be verified as "not in L".
    if kind in ("natural", "arithmetic_index"):
        return []

    alphabet = _get_alphabet(ir)
    if not alphabet:
        return []

    # Try to build the real oracle; if approximate, we can't reliably
    # label words as negative.
    oracle = _try_build_oracle(ir)
    if oracle is not None and getattr(oracle, "is_approximate", False):
        return []

    positives = set(generate_positive_words(ir, max_count=200, max_length=max_length, seed=seed))

    candidates: list[str] = []
    attempts = 0
    max_attempts = max_count * 20

    while len(candidates) < max_count and attempts < max_attempts:
        attempts += 1
        length = rng.randint(0, max_length)
        word = "".join(rng.choices(alphabet, k=length)) if length > 0 else ""

        if word in positives:
            continue

        # Verify via oracle (or _simple_membership_check as fallback)
        if oracle is not None:
            try:
                in_lang = bool(oracle(word))
            except Exception:
                continue
            if in_lang:
                positives.add(word)
                continue
            candidates.append(word)
        else:
            membership = _simple_membership_check(ir, word)
            if membership is True:
                positives.add(word)
                continue
            if membership is False:
                candidates.append(word)
            # Unknown: skip (was: treat as negative — misleading)

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
