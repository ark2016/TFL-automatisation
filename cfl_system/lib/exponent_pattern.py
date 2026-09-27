"""
Exponent-notation language descriptions -> membership oracle.

Handles the free-text "натуральное" notation frequently used for
``language_spec.kind == "natural"`` (and any other kind carrying a plain
``description``/``word_pattern`` string) that ``cfl_oracle.py`` otherwise
cannot mechanise, e.g.::

    {a^n b^n c^m | n, m >= 0}
    a^i b^j c^k d^l | i = 0 or j = k = l
    a^n b^{2n}
    (ab)^n c^n
    a^n b^m | n != m
    a^n a^m

Grammar (informal):

    pattern   := '{' body '}' | body
    body      := blocks ['|' condition]
    blocks    := (block | literal)*
    block     := (LETTER | '(' WORD ')') '^' exponent
    exponent  := '{' arith '}' | bare-arith-run
    condition := boolean formula over chains of linear (in)equalities,
                 using the English keywords "and" / "or" / "not",
                 parentheses, and comma-separated left/right sides for
                 declarations like "n, m >= 0" or chains like "j = k = l"
                 / "i <= j <= k".

``parse_exponent_pattern(text)`` never raises on unparseable input; it
returns ``None`` instead -- also for a pattern this module cannot
mechanically evaluate (an exponent with two-or-more still-unbound
variables no other block ever pins down, a condition referencing a
variable no block binds, or an identifier that isn't a plain one-letter
exponent variable, e.g. "mod"/"prime"/"in" folded in by implicit
multiplication, or "R" which denotes reversal elsewhere in the codebase)
-- rather than a callable that silently rejects every word. The returned
:class:`ExponentPattern` is itself callable as a ``word -> bool | None``
membership oracle: ``None`` (never a guess) for a word longer than
``max_word_len`` (default 40) or when the backtracking search exceeds its
``step_budget``, per docs/VERDICT_POLICY.md §4 "оракул для экспоненциальной
нотации". It also carries ``is_approximate = True`` (mirroring the
``grammar_filter`` natural-language-filter oracle's convention), so callers
requiring a fully trusted oracle for a semantic-check upgrade skip it.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ExponentPattern", "parse_exponent_pattern"]

_MAX_WORD_LEN = 40
_STEP_BUDGET = 200_000


class _BudgetExceeded(Exception):
    """Internal: the backtracking search exceeded its step budget."""


class _ParseError(Exception):
    """Internal: a parse failure inside the recursive-descent parsers."""


# ---------------------------------------------------------------------------
# Tokenizer (shared by arithmetic-expression and boolean-condition parsing)
# ---------------------------------------------------------------------------

_TOKEN_SPEC: list[tuple[str, str]] = [
    ("LE", r"<=|≤"),
    ("GE", r">=|≥"),
    ("NE", r"!=|≠"),
    ("EQEQ", r"=="),
    ("EQ", r"="),
    ("LT", r"<"),
    ("GT", r">"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("COMMA", r","),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("NUM", r"\d+"),
    ("ID", r"[A-Za-zЀ-ӿ_][A-Za-zЀ-ӿ0-9_]*"),
    ("WS", r"\s+"),
]
_MASTER_RE = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in _TOKEN_SPEC))
_KEYWORDS = {"and": "AND", "or": "OR", "not": "NOT"}

# Exponent-notation variables in this module's supported grammar are always
# one letter (n, m, i, j, k, l, ...). A longer identifier reaching the token
# stream is prose that implicit multiplication would otherwise silently fold
# in as a variable (e.g. "n mod 2" -> "n * mod * 2"), never a real one --
# docs/VERDICT_POLICY.md §4 "оракул для экспоненциальной нотации" requires
# ``None`` rather than a reject-everything oracle for such input. "R" is
# reserved outright: elsewhere in the codebase (dcfl_system.lib.word_sampler's
# "^R" modifier) it denotes *reversal* of a word variable ("w w^R" = ww^R),
# never an exponent count, so treating it as one silently mis-parses that
# notation instead of rejecting it.
_RESERVED_SINGLE_LETTER_IDS = {"R"}


def _tokens_use_only_plain_variables(tokens: list[tuple[str, str]]) -> bool:
    for kind, val in tokens:
        if kind == "ID" and (len(val) != 1 or val in _RESERVED_SINGLE_LETTER_IDS):
            return False
    return True


def _tokenize(s: str) -> list[tuple[str, str]] | None:
    tokens: list[tuple[str, str]] = []
    pos = 0
    n = len(s)
    while pos < n:
        m = _MASTER_RE.match(s, pos)
        if not m:
            return None
        kind = m.lastgroup
        val = m.group()
        pos = m.end()
        if kind == "WS":
            continue
        if kind == "ID":
            low = val.lower()
            if low in _KEYWORDS:
                tokens.append((_KEYWORDS[low], val))
                continue
        tokens.append((kind, val))
    return tokens


class _TokenStream:
    __slots__ = ("tokens", "pos")

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str] | None:
        tok = self.peek()
        self.pos += 1
        return tok


# ---------------------------------------------------------------------------
# Arithmetic expressions: ('num', v) | ('var', name) | ('neg', e) | ('bin', op, l, r)
# ---------------------------------------------------------------------------

def _parse_arith(p: _TokenStream) -> tuple:
    return _parse_add(p)


def _parse_add(p: _TokenStream) -> tuple:
    left = _parse_mul(p)
    while p.peek() is not None and p.peek()[0] in ("PLUS", "MINUS"):
        op = p.next()[0]
        right = _parse_mul(p)
        left = ("bin", "+" if op == "PLUS" else "-", left, right)
    return left


def _parse_mul(p: _TokenStream) -> tuple:
    left = _parse_unary(p)
    while True:
        tok = p.peek()
        if tok is None:
            break
        if tok[0] == "STAR":
            p.next()
            right = _parse_unary(p)
            left = ("bin", "*", left, right)
        elif tok[0] in ("NUM", "ID", "LPAREN"):
            # implicit multiplication: "2n", "2(n+1)", etc.
            right = _parse_unary(p)
            left = ("bin", "*", left, right)
        else:
            break
    return left


def _parse_unary(p: _TokenStream) -> tuple:
    tok = p.peek()
    if tok is not None and tok[0] == "MINUS":
        p.next()
        return ("neg", _parse_unary(p))
    return _parse_atom(p)


def _parse_atom(p: _TokenStream) -> tuple:
    tok = p.next()
    if tok is None:
        raise _ParseError("unexpected end of expression")
    kind, val = tok
    if kind == "NUM":
        return ("num", int(val))
    if kind == "ID":
        return ("var", val)
    if kind == "LPAREN":
        inner = _parse_add(p)
        close = p.next()
        if close is None or close[0] != "RPAREN":
            raise _ParseError("missing closing parenthesis")
        return inner
    raise _ParseError(f"unexpected token {tok!r} in arithmetic expression")


def _parse_arith_from_str(raw: str) -> tuple | None:
    tokens = _tokenize(raw)
    if not tokens:
        return None
    if not _tokens_use_only_plain_variables(tokens):
        return None
    p = _TokenStream(tokens)
    try:
        expr = _parse_arith(p)
    except _ParseError:
        return None
    if p.peek() is not None:
        return None
    return expr


def _eval_arith(expr: tuple, env: dict[str, int]) -> int | None:
    kind = expr[0]
    if kind == "num":
        return expr[1]
    if kind == "var":
        return env.get(expr[1])
    if kind == "neg":
        v = _eval_arith(expr[1], env)
        return None if v is None else -v
    if kind == "bin":
        _, op, l, r = expr
        lv = _eval_arith(l, env)
        rv = _eval_arith(r, env)
        if lv is None or rv is None:
            return None
        if op == "+":
            return lv + rv
        if op == "-":
            return lv - rv
        if op == "*":
            return lv * rv
    return None


def _vars_of(expr: tuple) -> set[str]:
    kind = expr[0]
    if kind == "num":
        return set()
    if kind == "var":
        return {expr[1]}
    if kind == "neg":
        return _vars_of(expr[1])
    if kind == "bin":
        return _vars_of(expr[2]) | _vars_of(expr[3])
    return set()


def _solve_affine(expr: tuple, var: str, target: int, env: dict[str, int]) -> int | None:
    """Solve ``expr(var) == target`` for *var*, holding every OTHER variable
    in *expr* at its value in *env*. Works for any expression that is affine
    in *var* (numeric literals, plain variables, and +/-/* combinations
    thereof, e.g. "n", "2*n", "n-1", "2n+1") by evaluating at var=0 and
    var=1 and inverting the resulting linear map; returns ``None`` if the
    map isn't affine-solvable (division doesn't come out integral) or if
    some OTHER variable in *expr* is not yet bound in *env*."""
    env0 = dict(env)
    env0[var] = 0
    env1 = dict(env)
    env1[var] = 1
    f0 = _eval_arith(expr, env0)
    f1 = _eval_arith(expr, env1)
    if f0 is None or f1 is None:
        return None
    slope = f1 - f0
    if slope == 0:
        return 0 if f0 == target else None
    diff = target - f0
    if diff % slope != 0:
        return None
    return diff // slope


# ---------------------------------------------------------------------------
# Boolean condition: ('chain', groups, ops) | ('not', n) | ('and', [n]) | ('or', [n])
# ---------------------------------------------------------------------------

_REL_KINDS = {
    "LE": "<=", "GE": ">=", "NE": "!=", "EQEQ": "==", "EQ": "==",
    "LT": "<", "GT": ">",
}
_CMP_FUNCS: dict[str, Any] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _parse_or(p: _TokenStream) -> tuple:
    nodes = [_parse_and(p)]
    while p.peek() is not None and p.peek()[0] == "OR":
        p.next()
        nodes.append(_parse_and(p))
    return nodes[0] if len(nodes) == 1 else ("or", nodes)


def _parse_and(p: _TokenStream) -> tuple:
    nodes = [_parse_factor(p)]
    while p.peek() is not None and p.peek()[0] == "AND":
        p.next()
        nodes.append(_parse_factor(p))
    return nodes[0] if len(nodes) == 1 else ("and", nodes)


def _parse_factor(p: _TokenStream) -> tuple:
    if p.peek() is not None and p.peek()[0] == "NOT":
        p.next()
        return ("not", _parse_factor(p))
    if p.peek() is not None and p.peek()[0] == "LPAREN":
        save = p.pos
        try:
            p.next()
            inner = _parse_or(p)
            close = p.next()
            if close is None or close[0] != "RPAREN":
                raise _ParseError("missing closing parenthesis")
            return inner
        except _ParseError:
            # Not a boolean-grouping paren after all (e.g. "(i+j) <= k") --
            # backtrack and let it be consumed as an arithmetic atom below.
            p.pos = save
    return _parse_chain(p)


def _parse_chain(p: _TokenStream) -> tuple:
    groups = [_parse_atom_group(p)]
    ops: list[str] = []
    while p.peek() is not None and p.peek()[0] in _REL_KINDS:
        ops.append(_REL_KINDS[p.next()[0]])
        groups.append(_parse_atom_group(p))
    if not ops:
        raise _ParseError("comparison expected")
    return ("chain", groups, ops)


def _parse_atom_group(p: _TokenStream) -> list[tuple]:
    exprs = [_parse_arith(p)]
    while p.peek() is not None and p.peek()[0] == "COMMA":
        p.next()
        exprs.append(_parse_arith(p))
    return exprs


def _parse_condition_clause(s: str) -> tuple | None:
    tokens = _tokenize(s)
    if not tokens:
        return None
    if not _tokens_use_only_plain_variables(tokens):
        return None
    p = _TokenStream(tokens)
    try:
        node = _parse_or(p)
    except _ParseError:
        return None
    if p.peek() is not None:
        return None
    return node


def _parse_condition_str(s: str) -> tuple | None:
    """Parse a full condition string, treating a top-level ``;`` as another
    conjunction separator alongside "and" -- for descriptions that tack on
    a domain declaration after the main formula, e.g. ``"i <= j or j = k;
    i,j,k >= 1"``. Every ``;``-clause must parse for the whole condition to
    parse (never silently drops a clause)."""
    clauses = [c.strip() for c in s.split(";")]
    if any(not c for c in clauses):
        return None
    if len(clauses) == 1:
        return _parse_condition_clause(clauses[0])
    parsed = [_parse_condition_clause(c) for c in clauses]
    if any(p is None for p in parsed):
        return None
    return ("and", parsed)


def _eval_bool(node: tuple, env: dict[str, int]) -> bool | None:
    kind = node[0]
    if kind == "chain":
        _, groups, ops = node
        for i, op in enumerate(ops):
            left_vals = [_eval_arith(e, env) for e in groups[i]]
            right_vals = [_eval_arith(e, env) for e in groups[i + 1]]
            if any(v is None for v in left_vals) or any(v is None for v in right_vals):
                return None
            cmp_fn = _CMP_FUNCS[op]
            for a in left_vals:
                for b in right_vals:
                    if not cmp_fn(a, b):
                        return False
        return True
    if kind == "not":
        r = _eval_bool(node[1], env)
        return None if r is None else (not r)
    if kind == "and":
        for n in node[1]:
            r = _eval_bool(n, env)
            if r is None:
                return None
            if not r:
                return False
        return True
    if kind == "or":
        saw_none = False
        for n in node[1]:
            r = _eval_bool(n, env)
            if r is None:
                saw_none = True
            elif r:
                return True
        return None if saw_none else False
    return None


def _condition_vars(node: tuple) -> set[str]:
    """All variables referenced anywhere in a parsed boolean-condition AST
    (mirrors ``_vars_of`` for arithmetic expressions)."""
    kind = node[0]
    if kind == "chain":
        vs: set[str] = set()
        for group in node[1]:
            for e in group:
                vs |= _vars_of(e)
        return vs
    if kind == "not":
        return _condition_vars(node[1])
    if kind in ("and", "or"):
        vs = set()
        for n in node[1]:
            vs |= _condition_vars(n)
        return vs
    return set()


def _pattern_is_resolvable(segments: list[dict], condition: tuple | None) -> bool:
    """Static, parse-time-only check (docs/VERDICT_POLICY.md §4): a pattern
    this module cannot mechanically decide must come back as ``None``
    (unparseable), never as a callable that silently rejects every word.

    1. Walk the exponent blocks, propagating which variables become bound
       (a block with exactly one still-unbound variable pins it down via
       ``_solve_affine`` at match time). A block that STILL has two or more
       unbound variables after every other block has had its turn cannot be
       resolved by this matcher (e.g. ``a^{n+m} b^n c^m`` -- resolvable,
       since n/m are each pinned by the b/c blocks -- vs. a block whose
       extra variable no other block ever binds).
    2. Every variable the condition references must be one some block
       actually binds; otherwise ``_eval_bool`` would silently return
       ``None`` (treated as reject) for every word, e.g. ``"a^n | n = 2k"``
       or ``"a^n | k >= 0"`` where ``k`` is never bound by any block.
    """
    block_var_sets = [_vars_of(seg["expr"]) for seg in segments if "unit" in seg]

    bound: set[str] = set()
    pending = list(block_var_sets)
    progress = True
    while progress and pending:
        progress = False
        still_pending = []
        for vs in pending:
            unbound = vs - bound
            if len(unbound) <= 1:
                bound |= unbound
                progress = True
            else:
                still_pending.append(vs)
        pending = still_pending
    if pending:
        return False

    if condition is not None:
        all_vars = set().union(*block_var_sets) if block_var_sets else set()
        if not _condition_vars(condition).issubset(all_vars):
            return False

    return True


# ---------------------------------------------------------------------------
# Pattern (block) parsing
# ---------------------------------------------------------------------------

_BARE_EXP_CHARS = frozenset(string.ascii_letters + string.digits + "+-*_")


def _scan_bare_exponent(s: str, i: int) -> tuple[str, int] | None:
    n = len(s)
    j = i
    while j < n and s[j] in _BARE_EXP_CHARS:
        j += 1
    if j == i:
        return None
    return s[i:j], j


def _find_matching_paren(s: str, open_pos: int) -> int | None:
    """Index of the ``)`` matching the ``(`` at *open_pos* (balanced), or
    ``None`` if unbalanced."""
    depth = 0
    for k in range(open_pos, len(s)):
        if s[k] == "(":
            depth += 1
        elif s[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    return None


def _scan_exponent(s: str, i: int) -> tuple[tuple, int] | None:
    n = len(s)
    if i < n and s[i] == "{":
        j = s.find("}", i + 1)
        if j == -1:
            return None
        raw = s[i + 1:j]
        i2 = j + 1
    elif i < n and s[i] == "(":
        j = _find_matching_paren(s, i)
        if j is None:
            return None
        raw = s[i + 1:j]
        i2 = j + 1
    else:
        res = _scan_bare_exponent(s, i)
        if res is None:
            return None
        raw, i2 = res
    expr = _parse_arith_from_str(raw)
    if expr is None:
        return None
    return expr, i2


def _parse_pattern_blocks(s: str) -> list[dict] | None:
    segments: list[dict] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            j = s.find(")", i + 1)
            if j == -1:
                return None
            unit = s[i + 1:j]
            if not unit or not unit.isalpha():
                return None
            i2 = j + 1
            if i2 >= n or s[i2] != "^":
                return None
            res = _scan_exponent(s, i2 + 1)
            if res is None:
                return None
            expr, i3 = res
            segments.append({"unit": unit, "expr": expr})
            i = i3
            continue
        if ch.isalpha():
            if i + 1 < n and s[i + 1] == "^":
                res = _scan_exponent(s, i + 2)
                if res is None:
                    return None
                expr, i3 = res
                segments.append({"unit": ch, "expr": expr})
                i = i3
                continue
            j = i
            buf: list[str] = []
            while j < n and s[j].isalpha() and not (j + 1 < n and s[j + 1] == "^"):
                buf.append(s[j])
                j += 1
            if not buf:
                return None
            segments.append({"literal": "".join(buf)})
            i = j
            continue
        # Unknown top-level character (stray digit, bracket, punctuation) --
        # this isn't pure exponent-block notation; bail out.
        return None
    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ExponentPattern:
    """A parsed exponent-notation language description; callable as a
    ``word -> bool | None`` membership oracle. ``None`` means "unknown"
    (word longer than ``max_word_len``, or the backtracking search exceeded
    ``step_budget``) -- never a guess, per docs/VERDICT_POLICY.md §4.

    ``is_approximate`` is always set (mirrors the ``grammar_filter``
    natural-language-filter oracle's convention in ``cfl_oracle.py``):
    even a successfully parsed pattern is fundamentally bounded (fixed
    ``max_word_len``/``step_budget``), so callers that need a fully
    trusted oracle for a semantic upgrade (``claim_verifier._get_oracle``,
    ``cfl_oracle_test``'s R2'/R3' cross-checks) skip it rather than trust
    it fully.
    """

    segments: list[dict] = field(default_factory=list)
    condition: tuple | None = None
    max_word_len: int = _MAX_WORD_LEN
    step_budget: int = _STEP_BUDGET
    is_approximate: bool = True
    approximation_reason: str = (
        "exponent-notation oracle is bounded (|w| <= max_word_len, fixed "
        "backtracking step budget) — docs/VERDICT_POLICY.md §4"
    )

    def accepts(self, word: str) -> bool | None:
        if not isinstance(word, str):
            return None
        if len(word) > self.max_word_len:
            return None
        budget = [self.step_budget]
        try:
            return self._match(word, 0, 0, {}, budget, [])
        except _BudgetExceeded:
            return None

    def __call__(self, word: str) -> bool | None:
        return self.accepts(word)

    def _match(
        self,
        word: str,
        idx: int,
        pos: int,
        env: dict[str, int],
        budget: list[int],
        pending: list[tuple[tuple, int]],
    ) -> bool:
        budget[0] -= 1
        if budget[0] <= 0:
            raise _BudgetExceeded()

        if idx == len(self.segments):
            if pos != len(word):
                return False
            # Blocks deferred while >= 2 of their variables were still
            # unbound (see _pattern_is_resolvable) get checked now, once
            # every variable has (by construction) been pinned down by
            # some other, single-variable block.
            for expr, count in pending:
                val = _eval_arith(expr, env)
                if val is None or val != count:
                    return False
            if self.condition is None:
                return True
            return bool(_eval_bool(self.condition, env))

        seg = self.segments[idx]
        if "literal" in seg:
            text = seg["literal"]
            if word.startswith(text, pos):
                return self._match(word, idx + 1, pos + len(text), env, budget, pending)
            return False

        unit = seg["unit"]
        expr = seg["expr"]
        ulen = len(unit)
        if ulen == 0:
            return self._match(word, idx + 1, pos, env, budget, pending)

        unbound = sorted(v for v in _vars_of(expr) if v not in env)

        if not unbound:
            count = _eval_arith(expr, env)
            if count is None or count < 0:
                return False
            consumed = unit * count
            if word.startswith(consumed, pos):
                return self._match(word, idx + 1, pos + len(consumed), env, budget, pending)
            return False

        max_count = (len(word) - pos) // ulen

        if len(unbound) == 1:
            var = unbound[0]
            for count in range(0, max_count + 1):
                consumed = unit * count
                if not word.startswith(consumed, pos):
                    continue
                val = _solve_affine(expr, var, count, env)
                if val is None or val < 0:
                    continue
                new_env = dict(env)
                new_env[var] = val
                if self._match(word, idx + 1, pos + len(consumed), new_env, budget, pending):
                    return True
            return False

        # Two or more still-unbound variables here: _pattern_is_resolvable
        # guarantees every one of them gets bound by some OTHER (single-
        # variable) block, so defer this block's constraint -- enumerate
        # how many repetitions it consumes now, without committing to
        # values for its variables yet, and verify the deferred equation
        # once the terminal state is reached above.
        for count in range(0, max_count + 1):
            consumed = unit * count
            if not word.startswith(consumed, pos):
                continue
            new_pending = pending + [(expr, count)]
            if self._match(word, idx + 1, pos + len(consumed), env, budget, new_pending):
                return True
        return False


@dataclass
class _UnionPattern:
    """Callable ``word -> bool | None`` combining several
    :class:`ExponentPattern` alternatives via logical OR (for descriptions
    like ``"{a^n b^n | n>=0} union {a^n b^(2n) | n>=0}"``). ``None`` (never
    a guess) unless some branch is definitely ``True`` while every other
    branch is either ``False`` or unknown."""

    parts: list[ExponentPattern] = field(default_factory=list)
    is_approximate: bool = True
    approximation_reason: str = (
        "exponent-notation union oracle is bounded (see each branch's "
        "max_word_len/step_budget) — docs/VERDICT_POLICY.md §4"
    )

    def __call__(self, word: str) -> bool | None:
        results = [part(word) for part in self.parts]
        if any(r is True for r in results):
            return True
        if any(r is None for r in results):
            return None
        return False


_UNION_SPLIT_RE = re.compile(r"\bunion\b|∪", re.IGNORECASE)


def _parse_single_pattern(s: str) -> ExponentPattern | None:
    """Parse one (non-union) exponent-notation group -- the body of
    ``parse_exponent_pattern`` before union-splitting was factored out."""
    core = s.strip()
    if core.startswith("{") and core.endswith("}"):
        core = core[1:-1]

    pattern_str, sep, condition_str = core.partition("|")
    pattern_str = pattern_str.strip()
    condition_str = condition_str.strip()

    segments = _parse_pattern_blocks(pattern_str)
    if not segments:
        return None
    if not any("unit" in seg for seg in segments):
        # Pure literal text, no exponent blocks at all -- not our notation.
        return None

    condition_ast: tuple | None = None
    if sep:
        if not condition_str:
            return None
        condition_ast = _parse_condition_str(condition_str)
        if condition_ast is None:
            return None

    if not _pattern_is_resolvable(segments, condition_ast):
        return None

    return ExponentPattern(segments=segments, condition=condition_ast)


def parse_exponent_pattern(text: Any) -> ExponentPattern | _UnionPattern | None:
    """Parse an exponent-notation language description into a callable
    ``word -> bool`` oracle, or ``None`` if *text* isn't (purely) that
    notation. Never raises.

    Supports a top-level ``A union B`` (or ``A ∪ B``) of two or more
    such groups (e.g. ``"{a^n b^n | n>=0} union {a^n b^(2n) | n>=0}"``),
    combining them via logical OR -- ``None`` unless EVERY branch parses.
    """
    try:
        if not isinstance(text, str):
            return None
        s = text.strip()
        if not s:
            return None

        branches = [b.strip() for b in _UNION_SPLIT_RE.split(s) if b.strip()]
        if len(branches) > 1:
            parsed = [_parse_single_pattern(b) for b in branches]
            if any(p is None for p in parsed):
                return None
            return _UnionPattern(parts=parsed)

        return _parse_single_pattern(s)
    except Exception:
        return None
