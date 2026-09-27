"""
Word sampler for the DCFL agent system.

Generates concrete sample words from a DCFL task IR for use in:
- Oracle verification of agent claims
- Providing examples to specialist agents
- Testing membership in the language

Pure-function module: all public functions are stateless and deterministic
(seeded RNG).
"""

from __future__ import annotations

import itertools
import re
from collections import deque
from random import Random
from typing import Any

# ---------------------------------------------------------------------------
# Internal RNG (deterministic seed for reproducibility)
# ---------------------------------------------------------------------------

_DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# SampleWord factory
# ---------------------------------------------------------------------------

def _sample_word(
    word: str,
    *,
    in_language: bool | None = None,
    variables: dict[str, str] | None = None,
    source: str = "random",
) -> dict[str, Any]:
    """Create a SampleWord dict."""
    return {
        "word": word,
        "length": len(word),
        "in_language": in_language,
        "variables": variables,
        "source": source,
    }


# ===================================================================
# Constraint checking
# ===================================================================

_CMP_OPS: dict[str, Any] = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "!=": lambda a, b: a != b,
}


def check_constraints(variables: dict[str, str], constraints: list[dict]) -> bool:
    """Check if *variables* satisfy every constraint in *constraints*.

    Supported constraint kinds:
    - length_cmp:    |left_var| op |right_var|
    - integer_cmp:   integer_var op value
    - regex_member:  variable value matches regex
    - equal:         left_var == right_var (string equality)
    - reverse:       left_var == reverse(right_var)
    - disjunction:   at least one branch must hold
    """
    for c in constraints:
        kind = c.get("kind", "")
        args = c.get("args", {})

        if kind == "length_cmp":
            left = variables.get(args.get("left", ""), "")
            right = variables.get(args.get("right", ""), "")
            op = args.get("op", "==")
            cmp_fn = _CMP_OPS.get(op)
            if cmp_fn is None:
                return False
            if not cmp_fn(len(left), len(right)):
                return False

        elif kind == "integer_cmp":
            var_name = args.get("var", "")
            op = args.get("op", "==")
            value = args.get("value", 0)
            cmp_fn = _CMP_OPS.get(op)
            if cmp_fn is None:
                return False
            try:
                var_val = int(variables.get(var_name, "0"))
            except (ValueError, TypeError):
                return False
            if not cmp_fn(var_val, value):
                return False

        elif kind == "regex_member":
            var_name = args.get("var", "")
            pattern = args.get("pattern", ".*")
            val = variables.get(var_name, "")
            if not re.fullmatch(pattern, val):
                return False

        elif kind == "equal":
            left = variables.get(args.get("left", ""), "")
            right = variables.get(args.get("right", ""), "")
            if left != right:
                return False

        elif kind == "reverse":
            left = variables.get(args.get("left", ""), "")
            right = variables.get(args.get("right", ""), "")
            if left != right[::-1]:
                return False

        elif kind == "disjunction":
            branches = args.get("branches", [])
            if not branches:
                return False
            if not any(check_constraints(variables, [b]) for b in branches):
                return False

        else:
            # Unknown constraint kind — fail safe
            return False

    return True


# ===================================================================
# Pattern parsing  (set_builder word_pattern)
# ===================================================================

def _parse_word_pattern(pattern: str, var_names: list[str]) -> list[dict]:
    """Parse a word_pattern string into a list of segments.

    Each segment is one of:
      {"type": "var",     "name": "w", "modifier": None}
      {"type": "var",     "name": "w", "modifier": "R"}     # reversed
      {"type": "literal", "text": "aa"}

    The parser is greedy: it scans left-to-right, trying to match the
    longest known variable name (optionally followed by ``^R``).
    Anything that doesn't match a variable is accumulated as a literal.
    """
    # Sort var names longest-first so we match greedily.
    sorted_vars = sorted(var_names, key=len, reverse=True)
    segments: list[dict] = []
    i = 0
    literal_buf: list[str] = []

    while i < len(pattern):
        matched = False
        for vn in sorted_vars:
            if pattern[i:].startswith(vn):
                # Check for ^R modifier
                after = i + len(vn)
                modifier = None
                if pattern[after: after + 2] == "^R":
                    modifier = "R"
                    after += 2

                # Flush literal buffer
                if literal_buf:
                    segments.append({"type": "literal", "text": "".join(literal_buf)})
                    literal_buf.clear()

                segments.append({"type": "var", "name": vn, "modifier": modifier})
                i = after
                matched = True
                break

        if not matched:
            literal_buf.append(pattern[i])
            i += 1

    if literal_buf:
        segments.append({"type": "literal", "text": "".join(literal_buf)})

    return segments


class _BudgetExceeded(Exception):
    """Raised internally when the membership-oracle backtracking search
    exceeds its step budget; the caller treats this as "inconclusive"
    rather than guessing True/False."""


def _match_segments(
    word: str,
    segments: list[dict],
    idx: int,
    pos: int,
    assignments: dict[str, str],
    constraints: list[dict],
    domain_map: dict[str, str | None],
    budget: list[int],
) -> bool:
    """Backtracking matcher: does *word* decompose per *segments* (var/literal),
    with all variable occurrences consistent, all domain regexes satisfied,
    and *constraints* holding for the resulting assignment?"""
    budget[0] -= 1
    if budget[0] <= 0:
        raise _BudgetExceeded()

    if idx == len(segments):
        if pos != len(word):
            return False
        return check_constraints(assignments, constraints)

    seg = segments[idx]
    if seg["type"] == "literal":
        text = seg["text"]
        if word.startswith(text, pos):
            return _match_segments(
                word, segments, idx + 1, pos + len(text),
                assignments, constraints, domain_map, budget,
            )
        return False

    # seg["type"] == "var"
    name = seg["name"]
    modifier = seg.get("modifier")
    if name in assignments:
        val = assignments[name]
        consumed = val[::-1] if modifier == "R" else val
        if word.startswith(consumed, pos):
            return _match_segments(
                word, segments, idx + 1, pos + len(consumed),
                assignments, constraints, domain_map, budget,
            )
        return False

    domain = domain_map.get(name)
    max_len = len(word) - pos
    for length in range(0, max_len + 1):
        consumed = word[pos:pos + length]
        val = consumed[::-1] if modifier == "R" else consumed
        if domain is not None and not re.fullmatch(domain, val):
            continue
        assignments[name] = val
        if _match_segments(
            word, segments, idx + 1, pos + length,
            assignments, constraints, domain_map, budget,
        ):
            return True
        del assignments[name]
    return False


def build_set_builder_membership_oracle(
    spec: dict,
    alphabet: list[str],
    max_word_len: int = 60,
    step_budget: int = 200_000,
) -> Any:
    """Build a membership oracle ``word -> bool | None`` for a set_builder
    ``language_spec``, when the spec's ``word_pattern`` is a pure
    concatenation of named variables and alphabet-only literals (the
    "word_sampler + шаблоны" oracle referenced in docs/VERDICT_POLICY.md §4).

    Returns ``None`` when the pattern isn't of that shape (e.g. it uses
    exponent/star/alternation notation like ``"aⁿb*(cⁿ|bⁿ)ac*"``, which this
    matcher does not attempt to decide) — callers must then leave trust at
    ``well_formed`` rather than guess.

    The returned oracle itself returns ``None`` (rather than True/False) when
    the backtracking search exceeds its step budget — an "inconclusive", not
    a guess.
    """
    variables: list[dict] = spec.get("variables", [])
    var_names = [v["name"] for v in variables]
    word_pattern: str = spec.get("word_pattern", "")
    segments = _parse_word_pattern(word_pattern, var_names)

    if not any(s["type"] == "var" for s in segments):
        return None

    alphabet_set = set(alphabet)
    for seg in segments:
        if seg["type"] == "literal" and any(ch not in alphabet_set for ch in seg["text"]):
            # Pattern has non-alphabet meta characters (*, (, ), |, digits, …)
            # — not a plain concatenation-of-variables pattern.
            return None

    domain_map: dict[str, str | None] = {v["name"]: v.get("domain") for v in variables}
    constraints: list[dict] = spec.get("constraints", [])

    def oracle(word: str) -> bool | None:
        if not isinstance(word, str) or len(word) > max_word_len:
            return None
        budget = [step_budget]
        try:
            return _match_segments(word, segments, 0, 0, {}, constraints, domain_map, budget)
        except _BudgetExceeded:
            return None

    return oracle


# ===================================================================
# Parametric exponent-notation word instantiation (VERDICT_POLICY.md §4)
# ===================================================================

_SUPERSCRIPT_MAP: dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "ⁿ": "n",  # ⁿ
    "⁺": "+",  # ⁺
    "⁻": "-",  # ⁻
}
_SUPERSCRIPT_CHARS = frozenset(_SUPERSCRIPT_MAP)
_EXPONENT_EXPR_RE = re.compile(r"\A[0-9n+\-*]+\Z")


def _parse_exponent_word(pattern: str, alphabet: set[str]) -> list[tuple[str, str]] | None:
    """Parse a pure exponent-notation word like ``"aⁿbⁿ⁻¹"`` into
    ``[(letter, exponent_expr), ...]`` where ``exponent_expr`` is an ascii
    arithmetic expression in ``n`` (e.g. ``"n"``, ``"n-1"``, ``"2*n"``).

    Returns ``None`` for anything that isn't purely (alphabet letter +
    optional superscript exponent)* — prose, spaces, ``=``, parentheses,
    etc. all bail out, so free-text proof fields are never misinterpreted.
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch not in alphabet:
            return None
        i += 1
        exp_chars: list[str] = []
        while i < n and pattern[i] in _SUPERSCRIPT_CHARS:
            exp_chars.append(_SUPERSCRIPT_MAP[pattern[i]])
            i += 1
        if exp_chars:
            expr = re.sub(r"(\d)(n)", r"\1*\2", "".join(exp_chars))
            tokens.append((ch, expr))
        else:
            tokens.append((ch, "1"))
    return tokens


def _eval_exponent_expr(expr: str, n: int) -> int | None:
    if not _EXPONENT_EXPR_RE.match(expr):
        return None
    try:
        value = eval(expr, {"__builtins__": {}}, {"n": n})  # noqa: S307 — validated charset
    except Exception:
        return None
    if not isinstance(value, int) or value < 0:
        return None
    return value


def instantiate_exponent_pattern(pattern: str, n: int, alphabet: set[str]) -> str | None:
    """Instantiate a pure exponent-notation word pattern (e.g. ``"aⁿbⁿ⁻¹"``)
    at a concrete integer ``n``, returning the concrete word, or ``None`` if
    *pattern* isn't purely exponent notation over *alphabet* (prose, spacing,
    formulas with ``=``, parenthesised groups, etc. all bail out safely).
    """
    if not isinstance(pattern, str) or not pattern:
        return None
    tokens = _parse_exponent_word(pattern, alphabet)
    if tokens is None:
        return None
    out: list[str] = []
    for letter, expr in tokens:
        exp = _eval_exponent_expr(expr, n)
        if exp is None:
            return None
        out.append(letter * exp)
    return "".join(out)


def _build_word_from_segments(
    segments: list[dict],
    assignments: dict[str, str],
) -> str:
    """Assemble a word from parsed segments + variable assignments."""
    parts: list[str] = []
    for seg in segments:
        if seg["type"] == "literal":
            parts.append(seg["text"])
        elif seg["type"] == "var":
            val = assignments.get(seg["name"], "")
            if seg.get("modifier") == "R":
                val = val[::-1]
            parts.append(val)
    return "".join(parts)


# ===================================================================
# Domain word generation (simple regex subset)
# ===================================================================

def _generate_from_simple_regex(
    pattern: str,
    alphabet: list[str],
    rng: Random,
    count: int = 8,
    max_len: int = 20,
) -> list[str]:
    """Generate a few words matching a simple regex *pattern*.

    Handles a useful subset:
    - Literal characters
    - Character classes ``[ab]``
    - ``*`` (Kleene star), ``+`` (one-or-more)
    - Parenthesised groups ``(ab)*``
    - Concatenation

    For patterns we can't handle, fall back to random strings.
    """
    results: set[str] = set()

    # Quick attempt: just try random generation via ``re`` acceptance.
    for _ in range(count * 40):
        length = rng.randint(0, max_len)
        word = "".join(rng.choice(alphabet) for _ in range(length))
        if re.fullmatch(pattern, word):
            results.add(word)
        if len(results) >= count:
            break

    return list(results)[:count]


def _random_words(
    alphabet: list[str],
    rng: Random,
    count: int = 8,
    max_len: int = 20,
) -> list[str]:
    """Generate random words over *alphabet*."""
    words: list[str] = []
    for _ in range(count):
        length = rng.randint(0, max_len)
        words.append("".join(rng.choice(alphabet) for _ in range(length)))
    return words


# ===================================================================
# sample_from_set_builder
# ===================================================================

def sample_from_set_builder(
    spec: dict,
    alphabet: list[str],
    count: int = 20,
    max_len: int = 30,
) -> list[dict]:
    """Generate words by substituting variable values in set_builder format.

    Parameters
    ----------
    spec : dict
        A ``language_spec`` dict with ``word_pattern``, ``variables``,
        ``constraints``.
    alphabet : list[str]
        The language alphabet.
    count : int
        Target number of sample words.
    max_len : int
        Maximum word length to emit.
    """
    rng = Random(_DEFAULT_SEED)

    word_pattern: str = spec.get("word_pattern", "")
    variables: list[dict] = spec.get("variables", [])
    constraints: list[dict] = spec.get("constraints", [])

    var_names = [v["name"] for v in variables]
    segments = _parse_word_pattern(word_pattern, var_names)

    # Pre-generate candidate values for each variable.
    var_candidates: dict[str, list[str]] = {}
    per_var_count = max(12, count * 2)
    for v in variables:
        domain = v.get("domain")
        if domain is not None:
            candidates = _generate_from_simple_regex(
                domain, alphabet, rng, count=per_var_count, max_len=max_len
            )
        else:
            candidates = _random_words(alphabet, rng, count=per_var_count, max_len=max_len)
        # Always include the empty string as a candidate.
        if "" not in candidates:
            candidates.append("")
        var_candidates[v["name"]] = candidates

    # Enumerate combinations (bounded).
    results: list[dict] = []
    seen: set[str] = set()

    # Cartesian product of candidate lists, capped to avoid explosion.
    candidate_lists = [var_candidates[vn] for vn in var_names]
    max_combos = count * 50
    combo_iter = itertools.product(*candidate_lists)

    for idx, combo in enumerate(combo_iter):
        if idx >= max_combos:
            break
        assignment = dict(zip(var_names, combo))

        if not check_constraints(assignment, constraints):
            continue

        word = _build_word_from_segments(segments, assignment)
        if len(word) > max_len:
            continue
        if word in seen:
            continue
        seen.add(word)
        results.append(
            _sample_word(
                word,
                in_language=True,
                variables=assignment,
                source="substitution",
            )
        )
        if len(results) >= count:
            break

    return results


# ===================================================================
# sample_from_grammar
# ===================================================================

def sample_from_grammar(
    spec: dict,
    count: int = 20,
    max_len: int = 30,
) -> list[dict]:
    """Generate words from a grammar via BFS derivation.

    Parameters
    ----------
    spec : dict
        A ``language_spec`` dict with ``terminals``, ``nonterminals``,
        ``start``, ``rules``.
    count : int
        Target number of words.
    max_len : int
        Maximum word length.
    """
    terminals: set[str] = set(spec.get("terminals", []))
    nonterminals: set[str] = set(spec.get("nonterminals", []))
    start: str = spec.get("start", "S")
    rules: list[dict] = spec.get("rules", [])

    # Build rule map: nonterminal -> list of rhs alternatives
    rule_map: dict[str, list[list[str]]] = {}
    for r in rules:
        lhs = r["lhs"]
        rhs = r["rhs"]
        rule_map.setdefault(lhs, []).append(rhs)

    results: list[dict] = []
    seen: set[str] = set()

    # BFS over sentential forms
    queue: deque[list[str]] = deque()
    queue.append([start])
    visited: set[tuple[str, ...]] = {(start,)}

    max_iterations = count * 500

    iteration = 0
    while queue and len(results) < count and iteration < max_iterations:
        iteration += 1
        form = queue.popleft()

        # Check total length of terminal symbols so far.
        terminal_len = sum(len(s) for s in form if s in terminals or s not in nonterminals)
        if terminal_len > max_len:
            continue

        # Find the leftmost non-terminal.
        nt_index = -1
        for i, sym in enumerate(form):
            if sym in nonterminals:
                nt_index = i
                break

        if nt_index == -1:
            # All terminals — we have a word.
            word = "".join(form)
            if len(word) <= max_len and word not in seen:
                seen.add(word)
                results.append(
                    _sample_word(word, in_language=True, source="grammar_derivation")
                )
            continue

        # Expand the leftmost non-terminal with each rule.
        nt = form[nt_index]
        for rhs in rule_map.get(nt, []):
            new_form = form[:nt_index] + rhs + form[nt_index + 1:]
            key = tuple(new_form)
            if key not in visited:
                visited.add(key)
                queue.append(new_form)

    return results


# ===================================================================
# generate_negative_examples
# ===================================================================

def generate_negative_examples(
    ir: dict,
    positive_words: list[dict],
    count: int = 10,
) -> list[dict]:
    """Generate words that likely do NOT belong to the language.

    Strategies applied to each positive word:
    1. Permute symbols (swap two adjacent characters)
    2. Truncate (remove last character)
    3. Add extra symbols from alphabet
    """
    rng = Random(_DEFAULT_SEED)
    alphabet: list[str] = ir.get("alphabet", ["a", "b"])
    positive_set: set[str] = {pw["word"] for pw in positive_words}

    negatives: list[dict] = []
    seen: set[str] = set()

    def _add(word: str, src: str) -> None:
        if word not in seen and word not in positive_set:
            seen.add(word)
            negatives.append(
                _sample_word(word, in_language=False, source=src)
            )

    for pw in positive_words:
        w = pw["word"]
        if len(negatives) >= count:
            break

        # Strategy 1: swap adjacent characters
        if len(w) >= 2:
            idx = rng.randint(0, len(w) - 2)
            permuted = list(w)
            permuted[idx], permuted[idx + 1] = permuted[idx + 1], permuted[idx]
            candidate = "".join(permuted)
            if candidate != w:
                _add(candidate, "boundary")

        # Strategy 2: truncate
        if len(w) >= 1:
            _add(w[:-1], "boundary")

        # Strategy 3: prepend / append extra symbol
        extra = rng.choice(alphabet)
        _add(w + extra, "boundary")
        _add(extra + w, "boundary")

    # If we still need more, generate random words.
    attempts = 0
    while len(negatives) < count and attempts < count * 20:
        attempts += 1
        length = rng.randint(0, 30)
        word = "".join(rng.choice(alphabet) for _ in range(length))
        _add(word, "random")

    return negatives[:count]


# ===================================================================
# Top-level public API
# ===================================================================

def sample_words(
    ir: dict,
    count: int = 20,
    max_len: int = 30,
) -> list[dict]:
    """Generate sample words for a DCFL task.

    Dispatches to the appropriate sampler based on ``input_format``
    and also generates negative examples.

    Parameters
    ----------
    ir : dict
        A validated DCFL IR dict.
    count : int
        Target number of positive sample words.
    max_len : int
        Maximum word length.

    Returns
    -------
    list[dict]
        List of SampleWord dicts, each containing:
        - ``word``: the actual word (str)
        - ``length``: len(word)
        - ``in_language``: True / False / None
        - ``variables``: variable assignments if set_builder, else None
        - ``source``: "substitution" | "grammar_derivation" | "boundary" | "random"
    """
    input_format: str = ir.get("input_format", "")
    spec: dict = ir.get("language_spec", {})
    alphabet: list[str] = ir.get("alphabet", ["a", "b"])

    positive: list[dict]
    if input_format == "set_builder":
        positive = sample_from_set_builder(spec, alphabet, count=count, max_len=max_len)
    elif input_format == "grammar":
        positive = sample_from_grammar(spec, count=count, max_len=max_len)
    else:
        # Unknown format — generate random words with unknown membership.
        rng = Random(_DEFAULT_SEED)
        positive = [
            _sample_word(
                "".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_len))),
                in_language=None,
                source="random",
            )
            for _ in range(count)
        ]

    # Generate negative examples (roughly half the positive count).
    neg_count = max(count // 2, 5)
    negatives = generate_negative_examples(ir, positive, count=neg_count)

    return positive + negatives
