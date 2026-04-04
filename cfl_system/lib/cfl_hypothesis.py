"""CFL hypothesis module — heuristic analysis of language specifications.

Analyzes a CFL IR to predict whether a language is context-free or not,
and suggests which specialist agents should investigate.  Purely heuristic,
no LLM calls.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Agent suggestions per hypothesis
# ---------------------------------------------------------------------------

AGENT_MAP: dict[str, list[str]] = {
    "cfl": ["cfg_builder", "pda_builder", "decomposition", "parikh"],
    "non_cfl": ["pumping_cfl", "ogden", "closure_reduction", "interchange", "morphism"],
    "unknown": ["cfg_builder", "pumping_cfl", "closure_reduction", "parikh", "ogden"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _are_interleaved(pos1: list[int], pos2: list[int]) -> bool:
    """Return True if positions of two repeated parts are interleaved.

    Example: pos1=[0,2], pos2=[1,3] → True  (0 < 1 < 2 < 3)
    """
    # We need at least one element from each list to sit between elements of
    # the other.  Concretely: ∃ a,b in pos1, ∃ c in pos2 with a < c < b,
    # OR ∃ a,b in pos2, ∃ c in pos1 with a < c < b.
    s1, s2 = sorted(pos1), sorted(pos2)
    for c in s2:
        # Is c between any two consecutive occurrences in s1?
        for i in range(len(s1) - 1):
            if s1[i] < c < s1[i + 1]:
                return True
    for c in s1:
        for i in range(len(s2) - 1):
            if s2[i] < c < s2[i + 1]:
                return True
    return False


def _is_counting_pred(constraint: Any) -> bool:
    """Heuristic: does *constraint* express a counting relationship?

    Recognised shapes (dict):
      {"type": "length_relation", ...}
      {"type": "count_relation", ...}
      {"lhs": ..., "op": ..., "rhs": ...}  where both sides reference symbols
    Strings containing '|' (cardinality notation) also count.
    """
    if isinstance(constraint, str):
        return "|" in constraint
    if isinstance(constraint, dict):
        ctype = constraint.get("type", "")
        if ctype in ("length_relation", "count_relation"):
            return True
        # Generic {lhs, op, rhs} where both sides are symbol counts
        if "lhs" in constraint and "rhs" in constraint:
            lhs, rhs = constraint["lhs"], constraint["rhs"]
            if isinstance(lhs, str) and isinstance(rhs, str):
                # Both sides reference symbol counts → counting
                return True
    return False


def _is_filter_regular(filter_spec: dict) -> bool | None:
    """Decide whether a grammar_filter's filter predicate is regular.

    Returns True  → filter is regular  (CFL ∩ REG = CFL)
    Returns False → filter is NOT regular
    Returns None  → cannot determine
    """
    if not filter_spec:
        return None

    ftype = filter_spec.get("type", "")

    # Modular constraints are regular
    if ftype == "modular":
        return True

    # Comparison with a constant on one side is regular (e.g. |w| > 5)
    if ftype in ("length_bound", "length_comparison"):
        return True

    # Comparison of two symbol counts (e.g. |a| = |b|) is NOT regular
    if ftype in ("count_relation", "symbol_count_comparison"):
        return False

    # Explicit flag
    if "is_regular" in filter_spec:
        return bool(filter_spec["is_regular"])

    # Natural-language filter — we can't tell
    if ftype == "natural_language_filter":
        return None

    # Fallback: try to infer from structure
    if "lhs" in filter_spec and "rhs" in filter_spec:
        lhs, rhs = filter_spec["lhs"], filter_spec["rhs"]
        # If one side is a constant number → regular
        if isinstance(lhs, (int, float)) or isinstance(rhs, (int, float)):
            return True
        # Both sides are symbol-count references → not regular
        if isinstance(lhs, str) and isinstance(rhs, str):
            return False

    return None


def _has_counting(filter_spec: dict) -> bool:
    """Return True if *filter_spec* involves a counting constraint."""
    if not filter_spec:
        return False
    ftype = filter_spec.get("type", "")
    if ftype in ("count_relation", "symbol_count_comparison"):
        return True
    if "lhs" in filter_spec and "rhs" in filter_spec:
        lhs, rhs = filter_spec["lhs"], filter_spec["rhs"]
        if isinstance(lhs, str) and isinstance(rhs, str):
            return True
    return False


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _extract_features(ir: dict) -> dict:
    """Extract heuristic features from an IR dict."""
    spec = ir.get("language_spec", {})
    kind = spec.get("kind", "")

    features: dict[str, Any] = {
        "has_repeated_subword": False,
        "has_reverse": False,
        "has_counting_constraint": False,
        "is_bounded_language": False,
        "is_grammar_filter": kind == "grammar_filter",
        "filter_is_regular": None,
        "crossed_dependencies": False,
        "nesting_depth": None,
    }

    if kind == "repeated_subword":
        pattern = spec.get("concat_pattern", [])
        parts = spec.get("parts", {})

        # Build position map
        part_positions: dict[str, list[int]] = {}
        for i, p in enumerate(pattern):
            part_positions.setdefault(p, []).append(i)

        repeated = [p for p, pos in part_positions.items() if len(pos) > 1]
        features["has_repeated_subword"] = len(repeated) > 0

        # Crossed dependencies: two different repeated parts with interleaved positions
        for idx, p1 in enumerate(repeated):
            for p2 in repeated[idx + 1 :]:
                pos1, pos2 = part_positions[p1], part_positions[p2]
                if _are_interleaved(pos1, pos2):
                    features["crossed_dependencies"] = True

        # Bounded language: every part has a single-char alphabet
        alphabets = spec.get("alphabets", {})
        if alphabets:
            features["is_bounded_language"] = all(
                len(a) == 1 for a in alphabets.values()
            )

    elif kind == "exists_decomposition":
        pattern = spec.get("concat_pattern", [])
        features["has_reverse"] = any(
            isinstance(p, str) and p.startswith("rev(") for p in pattern
        )

        # Check for ww-like pattern (same base part repeated without reverse)
        part_positions: dict[str, list[tuple[int, bool]]] = {}
        for i, p in enumerate(pattern):
            if isinstance(p, str) and p.startswith("rev(") and p.endswith(")"):
                base = p[4:-1]
                is_rev = True
            else:
                base = p
                is_rev = False
            part_positions.setdefault(base, []).append((i, is_rev))

        for base, positions in part_positions.items():
            non_rev = [(i, r) for i, r in positions if not r]
            if len(non_rev) > 1:
                features["has_repeated_subword"] = True

    elif kind == "grammar_filter":
        filter_spec = spec.get("filter", {})
        features["filter_is_regular"] = _is_filter_regular(filter_spec)
        features["has_counting_constraint"] = _has_counting(filter_spec)

    # Global: check constraints for counting
    constraints = spec.get("constraints", [])
    if constraints:
        features["has_counting_constraint"] = any(
            _is_counting_pred(c) for c in constraints
        )

    return features


# ---------------------------------------------------------------------------
# Hypothesis derivation
# ---------------------------------------------------------------------------


def _derive_hypothesis(
    features: dict, kind: str
) -> tuple[str, float, str]:
    """Return (hypothesis, confidence, reasoning)."""

    if features["crossed_dependencies"]:
        return (
            "non_cfl",
            0.85,
            "Crossed dependencies detected — two different repeated parts "
            "are interleaved, creating a pattern that context-free grammars "
            "cannot generate.",
        )

    if features["has_repeated_subword"] and not features["has_reverse"]:
        if features["is_bounded_language"]:
            return (
                "unknown",
                0.4,
                "Repeated subword with single-character alphabets (bounded "
                "language). This may still be CFL; algorithmic analysis needed.",
            )
        return (
            "non_cfl",
            0.75,
            "Repeated subword with multi-character alphabet and no reverse — "
            "creates a copying dependency that CFGs typically cannot handle.",
        )

    if features["is_grammar_filter"] and features["filter_is_regular"] is True:
        return (
            "cfl",
            0.9,
            "Grammar with a regular filter. By closure of CFLs under "
            "intersection with regular languages, the result is CFL.",
        )

    if features["is_grammar_filter"] and features["filter_is_regular"] is False:
        return (
            "unknown",
            0.4,
            "Grammar with a non-regular filter. The intersection of a CFL "
            "with a non-regular language requires further analysis.",
        )

    if features["has_reverse"] and not features["has_repeated_subword"]:
        return (
            "cfl",
            0.7,
            "Pattern contains reverse (palindrome-like structure) without "
            "repeated subwords — CFL-friendly via non-deterministic PDA.",
        )

    if kind == "grammar":
        return (
            "cfl",
            0.95,
            "An explicit context-free grammar is provided, so the language "
            "is CFL by definition.",
        )

    return (
        "unknown",
        0.3,
        "Insufficient structural signals to determine CFL status. "
        "Multiple specialist agents should investigate.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_cfl_hypothesis(ir: dict) -> dict:
    """Analyze language spec to predict CFL / non-CFL and suggest agents.

    Parameters
    ----------
    ir : dict
        A CFL intermediate representation with at least a ``language_spec``
        key containing ``kind`` and kind-specific fields.

    Returns
    -------
    dict
        ``features``          – extracted boolean/numeric signals
        ``hypothesis``        – ``"cfl"`` | ``"non_cfl"`` | ``"unknown"``
        ``confidence``        – float in [0, 1]
        ``reasoning``         – human-readable explanation
        ``suggested_agents``  – list of agent names to dispatch
    """
    features = _extract_features(ir)
    kind = ir.get("language_spec", {}).get("kind", "")
    hypothesis, confidence, reasoning = _derive_hypothesis(features, kind)

    return {
        "features": features,
        "hypothesis": hypothesis,
        "confidence": confidence,
        "reasoning": reasoning,
        "suggested_agents": list(AGENT_MAP.get(hypothesis, AGENT_MAP["unknown"])),
    }
