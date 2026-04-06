"""
Pattern database for the DCFL agent system.

Pure-function module that matches DCFL task IRs against known structural
patterns to suggest a verdict (dcfl / non_dcfl) and a recommended
specialist agent.  No LLM calls — all logic is deterministic.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Pattern database
# ---------------------------------------------------------------------------

PATTERN_DB: list[dict[str, str]] = [
    # 1
    {
        "pattern": "single_palindrome_with_separator",
        "description": "w c w^R with separator c",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{wcw^R | w ∈ {a,b}*}",
    },
    # 2
    {
        "pattern": "single_palindrome_no_separator",
        "description": "w w^R without separator",
        "verdict": "non_dcfl",
        "method": "shallit",
        "example": "{ww^R | w ∈ {a,b}*}",
    },
    # 3
    {
        "pattern": "sequential_palindromes",
        "description": "sequential w/w^R then v/v^R",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{ww^Rvv^R | w,v ∈ {a,b}*}",
    },
    # 4
    {
        "pattern": "nested_palindromes_with_separator",
        "description": "wv...v^Rw^R with separator",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{wvaav^Rw^R}",
    },
    # 5
    {
        "pattern": "nested_palindromes_no_separator",
        "description": "wv...v^Rw^R without separator",
        "verdict": "non_dcfl",
        "method": "dcfl_pumping",
        "example": "{wvv^Rw^R | no sep}",
    },
    # 6
    {
        "pattern": "single_count_match",
        "description": "a^n b^n",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{a^n b^n | n≥0}",
    },
    # 7
    {
        "pattern": "single_length_cmp",
        "description": "one length comparison |u1| ≤ |u2|",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{u₁au₂ | |u₁| ≤ |u₂|}",
    },
    # 8
    {
        "pattern": "dual_length_cmp_compatible",
        "description": "two compatible length comparisons",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{u₁au₂u₃au₄ | |u₁|≤|u₂| & |u₃|≥|u₄|}",
    },
    # 9
    {
        "pattern": "dual_length_cmp_conflicting",
        "description": "two conflicting length comparisons",
        "verdict": "non_dcfl",
        "method": "dcfl_pumping",
        "example": "{u₁au₂u₃bu₄ | |u₁|=|u₃| & |u₂|=|u₄|}",
    },
    # 10
    {
        "pattern": "shared_var_disjunction",
        "description": "a^n...f(n)...g(n) with disjunction f|g",
        "verdict": "non_dcfl",
        "method": "inh_ambiguity",
        "example": "{a^i b^j c^k | i=j ∨ j=k}",
    },
    # 11
    {
        "pattern": "regex_constrained_palindrome",
        "description": "palindrome with regex-constrained variable",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "{wvaav^Rw^R | w∈(aa*b)*a, v∈b(ab|aa)*}",
    },
    # 12
    {
        "pattern": "count_with_regex_body",
        "description": "a^n X a^n with X regular",
        "verdict": "dcfl",
        "method": "closure_reduction",
        "example": "{a^n b* a^n | n≥1}",
    },
    # 13
    {
        "pattern": "disjunction_count_parts",
        "description": "disjunction in counted parts",
        "verdict": "non_dcfl",
        "method": "inh_ambiguity",
        "example": "{a^n b*(c^n|b^n)ac*}",
    },
    # 14
    {
        "pattern": "triple_count",
        "description": "a^n b^n c^n",
        "verdict": "non_dcfl",
        "method": "dcfl_pumping",
        "example": "{a^n b^n c^n}",
    },
    # 15
    {
        "pattern": "complement_of_known",
        "description": "complement of a known language",
        "verdict": "dcfl",
        "method": "closure_reduction",
        "example": "~{a^n b^n c^n}",
    },
    # 16
    {
        "pattern": "reg_intersection",
        "description": "DCFL intersected with regular",
        "verdict": "dcfl",
        "method": "closure_reduction",
        "example": "{a^n b^n} ∩ a*b*",
    },
    # 17
    {
        "pattern": "grammar_lr_like",
        "description": "grammar that looks LR(k)-parseable",
        "verdict": "dcfl",
        "method": "stack_strategy",
        "example": "S→aSb | ab",
    },
    # 18
    {
        "pattern": "grammar_ambiguous_union",
        "description": "grammar with ambiguous union structure",
        "verdict": "non_dcfl",
        "method": "inh_ambiguity",
        "example": "S→AB|CD, A→aAb|ε, ...",
    },
    # 19
    {
        "pattern": "inverse_homomorphism",
        "description": "obtained via inverse homomorphism from DCFL",
        "verdict": "dcfl",
        "method": "closure_reduction",
        "example": "h^{-1}(L) where L DCFL",
    },
    # 20
    {
        "pattern": "reversal_of_dcfl",
        "description": "reversal of a DCFL",
        "verdict": "non_dcfl",
        "method": "shallit",
        "example": "L^R where L is DCFL but L^R is not",
    },
]

# Index for O(1) lookup by pattern code
_PATTERN_INDEX: dict[str, dict[str, str]] = {p["pattern"]: p for p in PATTERN_DB}


# ---------------------------------------------------------------------------
# Internal helpers — word-pattern analysis
# ---------------------------------------------------------------------------

_RE_REVERSE = re.compile(r"\^R|_R|\\text\{R\}|\\mathrm\{R\}")
_RE_SEPARATOR = re.compile(r"(?<=[a-z])\s*[c#$@]\s*(?=[a-z])", re.IGNORECASE)
_RE_COUNT_PAIR = re.compile(r"([a-z])\s*\^\s*([a-z])\b.*?\1\s*\^\s*\2", re.IGNORECASE)
_RE_TRIPLE_COUNT = re.compile(
    r"([a-z])\s*\^\s*([a-z])\s+"
    r"([a-z])\s*\^\s*\2\s+"
    r"([a-z])\s*\^\s*\2",
    re.IGNORECASE,
)
_RE_POWER = re.compile(r"([a-z])\s*\^\s*([a-z])", re.IGNORECASE)


def _has_reverse(text: str) -> bool:
    """Return True if *text* contains a reversal marker (^R, _R, etc.)."""
    return bool(_RE_REVERSE.search(text))


def _has_separator_between_reversal(text: str) -> bool:
    """Heuristic: separator symbol between a variable and its reversal."""
    return bool(_RE_SEPARATOR.search(text))


def _count_reversals(text: str) -> int:
    return len(_RE_REVERSE.findall(text))


def _count_powers(text: str) -> int:
    """Count distinct power notations like a^n."""
    return len(_RE_POWER.findall(text))


# ---------------------------------------------------------------------------
# Internal helpers — constraint analysis
# ---------------------------------------------------------------------------

def _constraint_kinds(constraints: list[dict]) -> set[str]:
    return {c.get("kind", "") for c in constraints}


def _count_length_cmps(constraints: list[dict]) -> int:
    return sum(1 for c in constraints if c.get("kind") == "length_cmp")


def _has_disjunction(constraints: list[dict]) -> bool:
    return any(c.get("kind") == "disjunction" for c in constraints)


def _has_regex_member(constraints: list[dict]) -> bool:
    return any(c.get("kind") == "regex_member" for c in constraints)


def _length_cmps_are_compatible(constraints: list[dict]) -> bool:
    """Two length constraints are *compatible* when they can be satisfied by a
    single left-to-right stack pass.  Heuristic: the comparisons reference
    disjoint variable pairs, or both use the same direction (<=, >=)."""
    lcmps = [c for c in constraints if c.get("kind") == "length_cmp"]
    if len(lcmps) < 2:
        return True
    # collect the variable names referenced
    var_sets: list[set[str]] = []
    ops: list[str] = []
    for lc in lcmps:
        args = lc.get("args", {})
        vs: set[str] = set()
        for key in ("left", "right", "lhs", "rhs", "var1", "var2"):
            val = args.get(key)
            if isinstance(val, str):
                vs.add(val)
        var_sets.append(vs)
        ops.append(args.get("op", args.get("operator", "=")))
    # disjoint variable sets => compatible
    if len(var_sets) >= 2 and not var_sets[0] & var_sets[1]:
        return True
    # same direction => compatible
    if all(op in ("<=", "<", "≤") for op in ops) or all(
        op in (">=", ">", "≥") for op in ops
    ):
        return True
    return False


def _constraints_reference_shared_vars(constraints: list[dict]) -> bool:
    """Heuristic: disjunction + multiple constraints sharing a variable."""
    all_vars: list[str] = []
    for c in constraints:
        args = c.get("args", {})
        for key in ("left", "right", "lhs", "rhs", "var1", "var2", "var"):
            val = args.get(key)
            if isinstance(val, str):
                all_vars.append(val)
    return len(all_vars) != len(set(all_vars))


# ---------------------------------------------------------------------------
# Internal helpers — variable analysis
# ---------------------------------------------------------------------------

def _any_regex_domain(variables: list[dict]) -> bool:
    """True if any variable has a non-trivial regex domain."""
    for v in variables:
        domain = v.get("domain")
        if domain and not re.fullmatch(r"[{}\w,*\s]+", domain):
            return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers — grammar analysis
# ---------------------------------------------------------------------------

def _is_lr_like(rules: list[dict]) -> bool:
    """Heuristic: grammar appears LR-parseable if no non-terminal appears
    both left-recursive and right-recursive, and there are no epsilon-
    ambiguous union starts."""
    lhs_set: set[str] = set()
    left_recursive: set[str] = set()
    right_recursive: set[str] = set()
    for rule in rules:
        lhs = rule.get("lhs", "")
        rhs = rule.get("rhs", [])
        lhs_set.add(lhs)
        if rhs and rhs[0] == lhs:
            left_recursive.add(lhs)
        if rhs and rhs[-1] == lhs:
            right_recursive.add(lhs)
    both = left_recursive & right_recursive
    return len(both) == 0


def _has_ambiguous_union(rules: list[dict]) -> bool:
    """Heuristic: a non-terminal has 2+ productions whose RHS start with
    different non-terminals that can both derive strings starting with
    the same terminal — a classic ambiguity indicator."""
    from collections import defaultdict

    prods: dict[str, list[list[str]]] = defaultdict(list)
    for rule in rules:
        prods[rule.get("lhs", "")].append(rule.get("rhs", []))

    nonterminals = set(prods.keys())

    for lhs, rhs_list in prods.items():
        if len(rhs_list) < 2:
            continue
        starting_nts: set[str] = set()
        for rhs in rhs_list:
            if rhs and rhs[0] in nonterminals and rhs[0] != lhs:
                starting_nts.add(rhs[0])
        if len(starting_nts) >= 2:
            return True
    return False


def _grammar_has_nesting(rules: list[dict]) -> bool:
    """Heuristic: grammar contains rules of the form A -> a A b (wrapping)."""
    for rule in rules:
        lhs = rule.get("lhs", "")
        rhs = rule.get("rhs", [])
        if len(rhs) >= 3 and rhs[0] != lhs and rhs[-1] != lhs:
            if lhs in rhs[1:-1]:
                return True
    return False


# ---------------------------------------------------------------------------
# Matchers — one per pattern or pattern family
# ---------------------------------------------------------------------------

def _match_set_builder(ir: dict) -> list[dict[str, Any]]:
    """Score patterns against a set_builder IR."""
    spec = ir.get("language_spec", {})
    wp: str = spec.get("word_pattern", "")
    variables: list[dict] = spec.get("variables", [])
    constraints: list[dict] = spec.get("constraints", [])
    source: str = ir.get("source_text", "")
    combined = f"{wp} {source}"

    hits: list[dict[str, Any]] = []

    has_rev = _has_reverse(combined)
    has_sep = _has_separator_between_reversal(combined)
    n_rev = _count_reversals(combined)
    n_powers = _count_powers(combined)
    n_lcmps = _count_length_cmps(constraints)
    has_disj = _has_disjunction(constraints)
    has_regex = _has_regex_member(constraints) or _any_regex_domain(variables)
    kinds = _constraint_kinds(constraints)

    # --- palindrome family ---
    if has_rev:
        if n_rev == 1 and has_sep:
            hits.append(_hit("single_palindrome_with_separator", 0.9,
                             "single reversal with separator detected"))
        if n_rev == 1 and not has_sep:
            hits.append(_hit("single_palindrome_no_separator", 0.9,
                             "single reversal without separator"))
        if n_rev >= 2:
            # sequential vs nested: look at word_pattern ordering
            # nested: wv...v^Rw^R  sequential: ww^Rvv^R
            if _looks_nested(wp):
                if has_sep:
                    hits.append(_hit("nested_palindromes_with_separator", 0.85,
                                     "nested reversal structure with separator"))
                else:
                    hits.append(_hit("nested_palindromes_no_separator", 0.85,
                                     "nested reversal structure without separator"))
            else:
                hits.append(_hit("sequential_palindromes", 0.8,
                                 "sequential reversal pairs detected"))

        # regex-constrained palindrome
        if has_rev and has_regex:
            hits.append(_hit("regex_constrained_palindrome", 0.75,
                             "palindrome with regex-constrained variables"))

    # --- count / power family ---
    if n_powers >= 3 and not has_rev:
        # check for triple count (a^n b^n c^n)
        if _RE_TRIPLE_COUNT.search(combined):
            hits.append(_hit("triple_count", 0.9,
                             "three powers sharing a single index"))
        elif n_powers >= 3:
            hits.append(_hit("triple_count", 0.6,
                             "three or more power notations detected"))

    if n_powers == 2 and not has_rev:
        hits.append(_hit("single_count_match", 0.85,
                         "two matching powers detected (a^n b^n pattern)"))

    # count with regex body
    if n_powers >= 2 and has_regex and not has_rev:
        hits.append(_hit("count_with_regex_body", 0.75,
                         "counted pair with regex body"))

    # --- length comparison family ---
    if n_lcmps == 1:
        hits.append(_hit("single_length_cmp", 0.85,
                         "single length comparison in constraints"))
    elif n_lcmps >= 2:
        if _length_cmps_are_compatible(constraints):
            hits.append(_hit("dual_length_cmp_compatible", 0.8,
                             "two compatible length comparisons"))
        else:
            hits.append(_hit("dual_length_cmp_conflicting", 0.8,
                             "two conflicting length comparisons"))

    # --- disjunction family ---
    if has_disj:
        if _constraints_reference_shared_vars(constraints):
            hits.append(_hit("shared_var_disjunction", 0.85,
                             "disjunction over shared variable indices"))
        if n_powers >= 2:
            hits.append(_hit("disjunction_count_parts", 0.75,
                             "disjunction among counted parts"))

    # --- closure / complement / intersection ---
    if re.search(r"~|complement|overline|\\overline", source, re.IGNORECASE):
        hits.append(_hit("complement_of_known", 0.8,
                         "complement operator detected in source"))

    if re.search(r"∩|\\cap|intersect", source, re.IGNORECASE):
        hits.append(_hit("reg_intersection", 0.8,
                         "intersection operator detected in source"))

    if re.search(r"h\s*\^?\s*\{?\s*-1\s*\}?|inverse\s+homomorphism",
                 source, re.IGNORECASE):
        hits.append(_hit("inverse_homomorphism", 0.8,
                         "inverse homomorphism detected in source"))

    if re.search(r"L\s*\^R|reversal\s+of", source, re.IGNORECASE):
        hits.append(_hit("reversal_of_dcfl", 0.75,
                         "reversal of a DCFL detected in source"))

    return hits


def _match_grammar(ir: dict) -> list[dict[str, Any]]:
    """Score patterns against a grammar IR."""
    spec = ir.get("language_spec", {})
    rules: list[dict] = spec.get("rules", [])
    source: str = ir.get("source_text", "")

    hits: list[dict[str, Any]] = []

    if not rules:
        return hits

    if _is_lr_like(rules):
        score = 0.85 if _grammar_has_nesting(rules) else 0.7
        hits.append(_hit("grammar_lr_like", score,
                         "grammar appears LR(k)-parseable"))

    if _has_ambiguous_union(rules):
        hits.append(_hit("grammar_ambiguous_union", 0.85,
                         "grammar has ambiguous union structure"))

    # Also check source text for closure operators
    if re.search(r"~|complement|overline", source, re.IGNORECASE):
        hits.append(_hit("complement_of_known", 0.7,
                         "complement operator detected in source text"))

    if re.search(r"∩|\\cap|intersect", source, re.IGNORECASE):
        hits.append(_hit("reg_intersection", 0.7,
                         "intersection operator in source text"))

    return hits


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _hit(pattern: str, score: float, reason: str) -> dict[str, Any]:
    """Build a PatternMatch dict from a pattern code, score, and reason."""
    entry = _PATTERN_INDEX[pattern]
    return {
        "pattern": pattern,
        "score": round(score, 4),
        "verdict": entry["verdict"],
        "method": entry["method"],
        "reason": reason,
    }


def _looks_nested(word_pattern: str) -> bool:
    """Heuristic: decide if a multi-reversal word_pattern is nested
    (wv…v^Rw^R) rather than sequential (ww^Rvv^R).

    Nested pattern has the *first* variable's reversal appearing *last*.
    """
    # Tokenise simple variable names and reversal markers
    tokens = re.findall(r"[a-zA-Z]+(?:\^R)?", word_pattern)
    vars_order: list[str] = []
    for t in tokens:
        base = t.replace("^R", "")
        if base not in vars_order:
            vars_order.append(base)
    # In nested: the first base var's ^R appears after the second base var's ^R
    # e.g. w v v^R w^R  =>  w appears first, w^R appears last
    rev_positions: dict[str, int] = {}
    for i, t in enumerate(tokens):
        if t.endswith("^R"):
            rev_positions[t.replace("^R", "")] = i
    if len(vars_order) >= 2 and len(rev_positions) >= 2:
        first_var, second_var = vars_order[0], vars_order[1]
        if (first_var in rev_positions and second_var in rev_positions
                and rev_positions[first_var] > rev_positions[second_var]):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_patterns(ir: dict) -> list[dict[str, Any]]:
    """Match a DCFL IR against the pattern database.

    Parameters
    ----------
    ir : dict
        A validated DCFL IR (as produced by ``dcfl_ir_schema.validate_dcfl_ir``).

    Returns
    -------
    list[dict]
        List of PatternMatch dicts::

            {
                "pattern": str,      # pattern code from PATTERN_DB
                "score": float,      # 0.0-1.0 match confidence
                "verdict": str,      # "dcfl" | "non_dcfl"
                "method": str,       # recommended specialist agent
                "reason": str,       # why this pattern matched
            }

        Sorted by *score* descending.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind", "")

    if kind == "set_builder":
        hits = _match_set_builder(ir)
    elif kind == "grammar":
        hits = _match_grammar(ir)
    else:
        # Fallback: try both and merge
        hits = _match_set_builder(ir) + _match_grammar(ir)

    # Deduplicate by pattern (keep highest score)
    best: dict[str, dict[str, Any]] = {}
    for h in hits:
        key = h["pattern"]
        if key not in best or h["score"] > best[key]["score"]:
            best[key] = h

    return sorted(best.values(), key=lambda x: x["score"], reverse=True)


def get_pattern(pattern_code: str) -> dict[str, str] | None:
    """Look up a single pattern entry by its code. Returns None if not found."""
    return _PATTERN_INDEX.get(pattern_code)


def list_patterns() -> list[dict[str, str]]:
    """Return a copy of the full pattern database."""
    return list(PATTERN_DB)
