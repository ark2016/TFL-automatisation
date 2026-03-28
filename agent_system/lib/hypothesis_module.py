"""
Hypothesis Module (§4.2) — predicate analysis to classify language atoms.

Analyses the language_spec from a JSON IR dict and classifies each
constituent "atom" as requiring finite or infinite memory, then derives
an overall regularity hypothesis with confidence and suggested agents.

Usage:
    from lib.hypothesis_module import analyze_hypothesis
    result = analyze_hypothesis(ir_dict)
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COMPARISON_OPS = frozenset({"lt", "leq", "gt", "geq", "eq", "neq"})
_BOOLEAN_OPS = frozenset({"and", "or", "not"})
_UNBOUNDED_KINDS = frozenset({"count_symbol", "count_subword", "length"})
_FINITE_PATTERN_OPS = frozenset({
    "is_substring", "is_not_substring",
    "starts_with", "not_starts_with",
})
_PALINDROME_OPS = frozenset({"is_palindrome", "is_not_palindrome"})
_SUPER_LINEAR_MARKERS = ("^2", "^3", "**2", "**3", "log", "2^", "!", "n*n")

_SUGGESTED_AGENTS = {
    "regular": ["dfa_builder", "re_builder"],
    "non_regular": ["pumping", "nerode", "closure"],
    "unknown": ["pumping", "nerode", "dfa_builder"],
}


# ---------------------------------------------------------------------------
# Atom collectors
# ---------------------------------------------------------------------------

def _collect_atoms(pred: dict, atoms: list[dict]) -> None:
    """Recursively walk a predicate tree and collect leaf atoms."""
    if not isinstance(pred, dict):
        return

    op = pred.get("op")

    # --- Boolean combinators: recurse into operands ---
    if op in _BOOLEAN_OPS:
        for operand in pred.get("operands", []):
            _collect_atoms(operand, atoms)
        return

    # --- Comparison atom ---
    if op in _COMPARISON_OPS:
        _classify_comparison(pred, op, atoms)
        return

    # --- Modular atom (always finite) ---
    if "modulus" in pred and "remainder" in pred:
        modulus = pred["modulus"]
        remainder = pred["remainder"]
        atoms.append({
            "description": f"expr mod {modulus} == {remainder}",
            "memory_type": "finite",
            "states_needed": modulus,
            "reason": "modular arithmetic",
        })
        return

    # --- ExistsDecomposition ---
    if "parts" in pred and "concat_pattern" in pred:
        _classify_decomposition(pred, atoms)
        return

    # --- Fixed-pattern search ops (finite) ---
    if op in _FINITE_PATTERN_OPS:
        atoms.append({
            "description": f"{op} check",
            "memory_type": "finite",
            "reason": "fixed-pattern search is regular",
        })
        return

    # --- Palindrome ops (infinite) ---
    if op in _PALINDROME_OPS:
        atoms.append({
            "description": f"{op} check on variable",
            "memory_type": "infinite",
            "reason": "palindrome testing requires unbounded memory",
        })
        return


def _classify_comparison(pred: dict, op: str, atoms: list[dict]) -> None:
    """Classify a comparison predicate atom."""
    left = pred.get("left", {})
    right = pred.get("right", {})
    left_kind = left.get("kind", "")
    right_kind = right.get("kind", "")

    left_unbounded = left_kind in _UNBOUNDED_KINDS
    right_unbounded = right_kind in _UNBOUNDED_KINDS

    if left_unbounded and right_unbounded:
        # Same quantity on both sides with eq → tautology (finite)
        if left == right and op == "eq":
            atoms.append({
                "description": f"{left_kind} {op} itself",
                "memory_type": "finite",
                "reason": "tautological comparison",
            })
        else:
            # Build a human-readable description
            left_desc = _quantity_description(left)
            right_desc = _quantity_description(right)
            atoms.append({
                "description": f"{left_desc} {op} {right_desc}",
                "memory_type": "infinite",
                "reason": "comparison of two unbounded quantities",
            })

    elif left_unbounded and right_kind == "constant":
        atoms.append({
            "description": f"{_quantity_description(left)} {op} constant({right.get('value')})",
            "memory_type": "finite",
            "reason": "bounded comparison with constant",
        })

    elif right_unbounded and left_kind == "constant":
        atoms.append({
            "description": f"constant({left.get('value')}) {op} {_quantity_description(right)}",
            "memory_type": "finite",
            "reason": "bounded comparison with constant",
        })

    else:
        atoms.append({
            "description": f"{left_kind} {op} {right_kind}",
            "memory_type": "unknown",
            "reason": "unable to classify",
        })


def _quantity_description(node: dict) -> str:
    """Build a short text description of a quantity node."""
    kind = node.get("kind", "?")
    symbol = node.get("symbol")
    if symbol:
        return f"{kind}({symbol})"
    return kind


def _is_palindromic_prefix_or_suffix(pred: dict) -> bool:
    """Check if the decomposition is a palindromic prefix/suffix pattern.

    Patterns like w = v·rev(v)·u  or  w = u·v·rev(v)  with ∃v (|v|≥1)
    mean "w starts/ends with a 2-char palindrome (aa or bb)" — finite memory.

    The key: v·rev(v) appears at the start or end of concat_pattern,
    and there is at least one OTHER part (u) absorbing the rest of w.
    If v·rev(v) IS the entire word (no u), then it's "w is a palindrome"
    which requires infinite memory.
    """
    concat_pattern = pred.get("concat_pattern", [])
    if len(concat_pattern) < 2:
        return False

    # Find all rev(X) entries and their adjacent X
    for i, elem in enumerate(concat_pattern):
        if not (isinstance(elem, str) and elem.startswith("rev(") and elem.endswith(")")):
            continue
        inner = elem[4:-1]
        # Check if the non-reversed version is adjacent
        if i > 0 and concat_pattern[i - 1] == inner:
            # Found X, rev(X) pair. Is there anything else in the pattern?
            if len(concat_pattern) > 2:
                return True  # prefix/suffix palindrome — finite
        if i < len(concat_pattern) - 1 and concat_pattern[i + 1] == inner:
            # Found rev(X), X pair
            if len(concat_pattern) > 2:
                return True

    return False


def _classify_decomposition(pred: dict, atoms: list[dict]) -> None:
    """Classify an ExistsDecomposition atom."""
    concat_pattern = pred.get("concat_pattern", [])
    constraints = pred.get("constraints", [])

    has_rev = any(
        isinstance(e, str) and "rev(" in e
        for e in concat_pattern
    )

    if has_rev and _is_palindromic_prefix_or_suffix(pred):
        atoms.append({
            "description": "palindromic prefix/suffix (vv^R as part of word)",
            "memory_type": "finite",
            "reason": (
                "palindromic prefix/suffix with ∃v,|v|≥1 reduces to "
                "checking first/last 2 symbols (e.g. aa or bb) — finite memory"
            ),
        })
    elif has_rev:
        atoms.append({
            "description": "existential decomposition (all parts bounded)",
            "memory_type": "finite",
            "reason": (
                "all decomposition parts are constrained to "
                "constant length"
            ),
        })
    else:
        atoms.append({
            "description": "existential decomposition",
            "memory_type": "unknown",
            "reason": "decomposition without reversal — needs deeper analysis",
        })

    # Recurse into sub-constraints
    for c in constraints:
        _collect_atoms(c, atoms)


def _all_parts_bounded(pred: dict) -> bool:
    """Check whether every part in a decomposition is bounded to constant length."""
    parts = pred.get("parts", [])
    constraints = pred.get("constraints", [])

    if not parts or not constraints:
        return False

    bounded_parts: set[str] = set()

    for c in constraints:
        # A constraint like length(part) <= constant bounds that part
        op = c.get("op", "")
        if op in ("eq", "leq", "lt"):
            left = c.get("left", {})
            right = c.get("right", {})
            if (left.get("kind") == "length"
                    and left.get("of") in parts
                    and right.get("kind") == "constant"):
                bounded_parts.add(left["of"])
            elif (right.get("kind") == "length"
                    and right.get("of") in parts
                    and left.get("kind") == "constant"
                    and op in ("eq", "geq", "gt")):
                bounded_parts.add(right["of"])

    return set(parts) == bounded_parts and len(parts) > 0


# ---------------------------------------------------------------------------
# Grammar analysis
# ---------------------------------------------------------------------------

def _analyze_grammar_atoms(spec: dict, atoms: list[dict]) -> None:
    """Inspect grammar rules for recursion patterns."""
    rules = spec.get("rules", [])
    if not rules:
        atoms.append({
            "description": "grammar structure (no rules)",
            "memory_type": "unknown",
            "reason": "empty grammar — cannot classify",
        })
        return

    nonterminals = set(spec.get("nonterminals", []))

    has_nested = False
    all_right_linear = True
    all_left_linear = True

    for rule in rules:
        rhs = rule.get("rhs", [])
        nt_positions = [i for i, s in enumerate(rhs) if s in nonterminals]

        if len(nt_positions) == 0:
            # Terminal-only production — compatible with both
            continue

        if len(nt_positions) == 1:
            pos = nt_positions[0]
            # Right-linear: NT must be at last position
            if pos != len(rhs) - 1:
                all_right_linear = False
            # Left-linear: NT must be at first position (position 0)
            if pos != 0:
                all_left_linear = False
            # Nested: terminals on both sides of the NT
            if 0 < pos < len(rhs) - 1:
                has_nested = True
        else:
            # Multiple nonterminals in one RHS → neither right- nor left-linear
            all_right_linear = False
            all_left_linear = False

    if has_nested:
        atoms.append({
            "description": "grammar with nested recursion (e.g. S->aSb)",
            "memory_type": "infinite",
            "reason": "nested recursion generates non-regular patterns",
        })
    elif all_right_linear:
        atoms.append({
            "description": "right-linear grammar",
            "memory_type": "finite",
            "reason": "right-linear grammars generate regular languages",
        })
    elif all_left_linear:
        atoms.append({
            "description": "left-linear grammar",
            "memory_type": "finite",
            "reason": "left-linear grammars generate regular languages",
        })
    else:
        atoms.append({
            "description": "grammar structure",
            "memory_type": "unknown",
            "reason": "grammar structure unclear — needs deeper analysis",
        })


# ---------------------------------------------------------------------------
# Regex analysis
# ---------------------------------------------------------------------------

def _analyze_regex_atoms(spec: dict, atoms: list[dict]) -> None:
    """Classify a regex-based language spec."""
    if spec.get("has_backreferences"):
        atoms.append({
            "description": "regex with backreferences",
            "memory_type": "infinite",
            "reason": "backreferences can encode non-regular patterns",
        })
    else:
        atoms.append({
            "description": "standard regex (no backreferences)",
            "memory_type": "finite",
            "reason": (
                "regular expressions without backreferences "
                "define regular languages"
            ),
        })


# ---------------------------------------------------------------------------
# Arithmetic index analysis
# ---------------------------------------------------------------------------

def _analyze_arithmetic_index_atoms(spec: dict, atoms: list[dict]) -> None:
    """Classify an arithmetic-index language spec."""
    fn = spec.get("index_function", "")

    if any(marker in fn for marker in _SUPER_LINEAR_MARKERS):
        atoms.append({
            "description": f"arithmetic index: {fn}",
            "memory_type": "infinite",
            "reason": "super-linear / non-periodic index function",
        })
    else:
        atoms.append({
            "description": f"arithmetic index: {fn}",
            "memory_type": "unknown",
            "reason": "index function needs deeper analysis",
        })


# ---------------------------------------------------------------------------
# Hypothesis derivation
# ---------------------------------------------------------------------------

def _derive_hypothesis(atoms: list[dict]) -> dict[str, Any]:
    """From classified atoms, derive overall hypothesis, confidence, agents."""
    if not atoms:
        return {
            "hypothesis": "unknown",
            "confidence": 0.0,
            "suggested_agents": _SUGGESTED_AGENTS["unknown"],
        }

    memory_types = [a["memory_type"] for a in atoms]

    if any(m == "infinite" for m in memory_types):
        hypothesis = "non_regular"
        confidence = 0.85
    elif all(m == "finite" for m in memory_types):
        hypothesis = "regular"
        confidence = 0.9
    else:
        # Mix of finite and unknown (no infinite)
        hypothesis = "unknown"
        confidence = 0.3

    return {
        "hypothesis": hypothesis,
        "confidence": confidence,
        "suggested_agents": list(_SUGGESTED_AGENTS[hypothesis]),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_hypothesis(ir: dict) -> dict[str, Any]:
    """Analyze an IR dict and produce a regularity hypothesis.

    Args:
        ir: JSON IR dict containing at least a ``language_spec`` key.

    Returns:
        A dict with keys ``atoms``, ``hypothesis``, ``confidence``, and
        ``suggested_agents``.  See module docstring for schema details.
    """
    spec = ir.get("language_spec")
    if spec is None:
        return {
            "atoms": [],
            "hypothesis": "unknown",
            "confidence": 0.0,
            "suggested_agents": [],
        }

    kind = spec.get("kind")
    atoms: list[dict] = []

    if kind == "predicate":
        predicate = spec.get("predicate")
        if predicate:
            _collect_atoms(predicate, atoms)

    elif kind == "grammar":
        _analyze_grammar_atoms(spec, atoms)

    elif kind == "regex":
        _analyze_regex_atoms(spec, atoms)

    elif kind == "arithmetic_index":
        _analyze_arithmetic_index_atoms(spec, atoms)

    derived = _derive_hypothesis(atoms)

    return {
        "atoms": atoms,
        "hypothesis": derived["hypothesis"],
        "confidence": derived["confidence"],
        "suggested_agents": derived["suggested_agents"],
    }
