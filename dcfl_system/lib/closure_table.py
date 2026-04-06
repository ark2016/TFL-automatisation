"""
DCFL closure properties table and scanner.

Encodes which operations preserve the DCFL class and scans
a DCFL IR to suggest applicable proof strategies.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Table 1 — DCFL closure properties
# ---------------------------------------------------------------------------

CLOSURE_TABLE: list[dict[str, Any]] = [
    {
        "operation": "complement",
        "symbol": "~",
        "closed": True,
        "implication_if_closed": "complement(L) is DCFL ⟺ L is DCFL",
        "proof_direction": "both",
    },
    {
        "operation": "inverse_homomorphism",
        "symbol": "h⁻¹",
        "closed": True,
        "implication_if_closed": "if L' is DCFL and L = h⁻¹(L'), then L is DCFL",
        "proof_direction": "constructive",
    },
    {
        "operation": "reg_intersection",
        "symbol": "∩ REG",
        "closed": True,
        "implication_if_closed": "if L' is DCFL and R is regular, then L' ∩ R is DCFL",
        "proof_direction": "constructive",
    },
    {
        "operation": "union",
        "symbol": "∪",
        "closed": False,
        "implication_if_closed": None,
        "proof_direction": None,
    },
    {
        "operation": "concatenation",
        "symbol": "·",
        "closed": False,
        "implication_if_closed": None,
        "proof_direction": None,
    },
    {
        "operation": "kleene_star",
        "symbol": "*",
        "closed": False,
        "implication_if_closed": None,
        "proof_direction": None,
    },
    {
        "operation": "reversal",
        "symbol": "R",
        "closed": False,
        "implication_if_closed": None,
        "proof_direction": None,
    },
    {
        "operation": "homomorphism",
        "symbol": "h",
        "closed": False,
        "implication_if_closed": None,
        "proof_direction": None,
    },
]

# Fast lookup by operation name
_BY_OP: dict[str, dict[str, Any]] = {entry["operation"]: entry for entry in CLOSURE_TABLE}


# ---------------------------------------------------------------------------
# Simple queries
# ---------------------------------------------------------------------------

def is_closed(operation: str) -> bool:
    """Check if DCFL is closed under the given operation.

    Raises ``KeyError`` if *operation* is not in the table.
    """
    entry = _BY_OP.get(operation)
    if entry is None:
        raise KeyError(
            f"Unknown operation '{operation}'. "
            f"Known operations: {sorted(_BY_OP)}"
        )
    return entry["closed"]


def get_implication(operation: str) -> str | None:
    """Get the implication string for a closed operation.

    Returns ``None`` when the operation is not closed (no implication)
    or when the operation is not in the table.
    """
    entry = _BY_OP.get(operation)
    if entry is None:
        return None
    return entry["implication_if_closed"]


def get_proof_direction(operation: str) -> str | None:
    """Get proof direction: ``'constructive'``, ``'destructive'``, or ``'both'``.

    Returns ``None`` when the operation is not closed or not in the table.
    """
    entry = _BY_OP.get(operation)
    if entry is None:
        return None
    return entry["proof_direction"]


# ---------------------------------------------------------------------------
# Helpers for closure_scan
# ---------------------------------------------------------------------------

def _has_disjunction_constraint(spec: dict[str, Any]) -> bool:
    """Return True if *spec* contains a disjunction constraint."""
    for constraint in spec.get("constraints", []):
        if constraint.get("kind") == "disjunction":
            return True
    return False


def _has_regex_constraint(spec: dict[str, Any]) -> bool:
    """Return True if *spec* contains a regex_member constraint."""
    for constraint in spec.get("constraints", []):
        if constraint.get("kind") == "regex_member":
            return True
    return False


def _has_inverse_homomorphism_pattern(spec: dict[str, Any]) -> bool:
    """Heuristic: detect constraints that look like inverse homomorphism.

    A language defined via ``w = h⁻¹(w')`` typically appears as a
    constraint with kind ``equal`` whose args reference a mapping.
    """
    for constraint in spec.get("constraints", []):
        args = constraint.get("args", {})
        if constraint.get("kind") == "equal" and "mapping" in args:
            return True
        # Also match explicit "inverse_homomorphism" tags
        if "inverse_homomorphism" in str(args):
            return True
    return False


def _describe_complement(spec: dict[str, Any]) -> str | None:
    """If the language has a simple complement description, return it."""
    word_pattern = spec.get("word_pattern")
    constraints = spec.get("constraints", [])

    if not word_pattern or not constraints:
        return None

    # Simple case: single negated constraint
    if len(constraints) == 1:
        c = constraints[0]
        if c.get("kind") == "length_cmp":
            args = c.get("args", {})
            op = args.get("op", "")
            negated_op = {"<": ">=", ">": "<=", "<=": ">", ">=": "<",
                          "==": "!=", "!=": "=="}.get(op)
            if negated_op:
                return (
                    f"complement is {word_pattern} with "
                    f"{args.get('lhs', '?')} {negated_op} {args.get('rhs', '?')}"
                )

    return None


def _strategy(
    operation: str,
    direction: str,
    description: str,
    confidence: float,
) -> dict[str, Any]:
    """Build a strategy dict."""
    return {
        "operation": operation,
        "direction": direction,
        "description": description,
        "confidence": round(confidence, 2),
    }


# ---------------------------------------------------------------------------
# Public scanner
# ---------------------------------------------------------------------------

def closure_scan(ir: dict[str, Any]) -> dict[str, Any]:
    """Scan a DCFL IR for applicable closure-based proof strategies.

    Analyzes ``language_spec`` to detect whether the language can be
    expressed via operations that DCFL is closed under (or not closed
    under).

    Parameters
    ----------
    ir : dict
        A validated DCFL IR dictionary.

    Returns
    -------
    dict with keys:
        - ``applicable_strategies`` — list of strategy dicts
        - ``complement_structure`` — description of complement if simple
        - ``warnings`` — list of warning strings
    """
    strategies: list[dict[str, Any]] = []
    warnings: list[str] = []
    complement_structure: str | None = None

    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    # --- 1. Disjunction → warn about union not being closed ----------------
    if kind == "set_builder" and _has_disjunction_constraint(spec):
        warnings.append(
            "disjunction detected in constraints — DCFL is NOT closed under "
            "union, so no conclusion follows from closure properties alone; "
            "consider inherent ambiguity or direct DPDA construction"
        )

    # --- 2. Regex-constrained variables → reg_intersection -----------------
    if kind == "set_builder" and _has_regex_constraint(spec):
        strategies.append(
            _strategy(
                operation="reg_intersection",
                direction="constructive",
                description=(
                    "regex_member constraint found — if the base language is "
                    "DCFL, intersecting with the regular constraint preserves "
                    "DCFL membership"
                ),
                confidence=0.7,
            )
        )

    # --- 3. Grammar → check complement structure ---------------------------
    if kind == "grammar":
        strategies.append(
            _strategy(
                operation="complement",
                direction="both",
                description=(
                    "grammar provided — consider whether the complement is a "
                    "simpler or known DCFL; DCFL is closed under complement"
                ),
                confidence=0.5,
            )
        )

    # --- 4. Complement always a viable strategy ----------------------------
    if kind == "set_builder":
        complement_structure = _describe_complement(spec)
        desc = (
            "DCFL is closed under complement — proving complement(L) is DCFL "
            "proves L is DCFL, and vice versa"
        )
        if complement_structure:
            desc += f"; complement structure: {complement_structure}"
        strategies.append(
            _strategy(
                operation="complement",
                direction="both",
                description=desc,
                confidence=0.6,
            )
        )

    # --- 5. Inverse homomorphism patterns ----------------------------------
    if kind == "set_builder" and _has_inverse_homomorphism_pattern(spec):
        strategies.append(
            _strategy(
                operation="inverse_homomorphism",
                direction="constructive",
                description=(
                    "inverse homomorphism pattern detected — if a known DCFL "
                    "L' exists such that L = h⁻¹(L'), then L is DCFL"
                ),
                confidence=0.65,
            )
        )

    return {
        "applicable_strategies": strategies,
        "complement_structure": complement_structure,
        "warnings": warnings,
    }
