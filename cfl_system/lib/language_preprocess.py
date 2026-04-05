"""
Language preprocessing module.

Enriches CFL IR with precomputed features before specialist agents run.
Handles Format 2 (grammar + filter) analysis, bounded language detection,
and Parikh semilinearity pre-checks.
"""

from __future__ import annotations

from typing import Any

from cfl_system.lib.parikh import analyze_parikh
from cfl_system.lib.stratification import is_bounded_language, check_stratification


# ---------------------------------------------------------------------------
# Filter analysis (grammar_filter kind)
# ---------------------------------------------------------------------------

def _classify_single_filter(filt: dict) -> tuple[bool | None, str]:
    """Classify a single (non-boolean) filter predicate.

    Returns (is_regular, filter_type).
    """
    # Natural language filter -- cannot determine
    # Canonical: {"kind": "natural_language_filter", "description": "..."}
    # Legacy:    {"natural_language_filter": "..."}
    if filt.get("kind") == "natural_language_filter" or "natural_language_filter" in filt:
        return None, "natural_language"

    op = filt.get("op")
    left = filt.get("left", {})
    right = filt.get("right", {})

    # --- Modular predicate ---
    if "modulus" in filt:
        return True, "modular"

    # --- Boolean combinations ---
    if op in ("and", "or"):
        # handled by caller; should not reach here, but be safe
        return None, "complex"
    if op == "not":
        return None, "complex"

    # --- Comparison operators (support both short and explicit forms) ---
    if op in ("eq", "ne", "neq", "lt", "le", "leq", "gt", "ge", "geq"):
        left_kind = left.get("kind")
        right_kind = right.get("kind")

        # Comparison with constant: length/count vs constant
        if right_kind == "constant" and left_kind in ("length", "count_symbol"):
            return True, "comparison_with_constant"
        if left_kind == "constant" and right_kind in ("length", "count_symbol"):
            return True, "comparison_with_constant"

        # Two count_symbol comparisons (e.g. |w|_a = |w|_b) -- NOT regular
        if left_kind == "count_symbol" and right_kind == "count_symbol":
            return False, "comparison_of_counts"

        # Two non-constant expressions -- generally not regular
        if left_kind in ("length", "count_symbol") and right_kind in ("length", "count_symbol"):
            return False, "comparison_of_expressions"

        # Fallback
        return None, "complex"

    return None, "complex"


def _analyze_filter_recursive(filt: dict) -> tuple[bool | None, str]:
    """Recursively analyze a filter, handling boolean combinations.

    Returns (is_regular, filter_type).
    """
    op = filt.get("op")

    # Boolean AND / OR
    if op in ("and", "or"):
        operands = filt.get("operands", [])
        if not operands:
            return None, "complex"

        sub_results = [_analyze_filter_recursive(sub) for sub in operands]
        sub_regulars = [r for r, _ in sub_results]
        sub_types = [t for _, t in sub_results]

        # If any sub-filter is indeterminate, whole thing is indeterminate
        if any(r is None for r in sub_regulars):
            return None, "complex"
        # If all regular -> regular
        if all(r is True for r in sub_regulars):
            return True, f"boolean_{op}_of_regular"
        # If any non-regular -> non-regular
        return False, f"boolean_{op}_with_non_regular"

    # Boolean NOT — schema uses `operands: [predicate]`; accept legacy `operand` too
    if op == "not":
        operand = None
        if "operand" in filt:
            operand = filt.get("operand")
        else:
            ops_list = filt.get("operands") or []
            if isinstance(ops_list, list) and ops_list:
                operand = ops_list[0]
        if not isinstance(operand, dict):
            return None, "complex"
        r, t = _analyze_filter_recursive(operand)
        # Regular languages are closed under complement
        return r, f"not_{t}"

    # Leaf filter
    return _classify_single_filter(filt)


def _analyze_filter(filter_spec: dict) -> dict:
    """Analyze whether a filter predicate is regular.

    Returns filter_analysis dict.
    """
    is_regular, filter_type = _analyze_filter_recursive(filter_spec)

    if is_regular is True:
        strategy = "pda_x_dfa"
        explanation = (
            f"Filter is regular ({filter_type}). "
            f"CFL intersect REG = CFL, so PDA x DFA product construction applies."
        )
    elif is_regular is False:
        strategy = "manual"
        explanation = (
            f"Filter is not regular ({filter_type}). "
            f"Cannot use PDA x DFA product; manual analysis required."
        )
    else:
        strategy = None
        explanation = (
            f"Cannot determine regularity of filter ({filter_type}). "
            f"Manual inspection needed."
        )

    return {
        "filter_is_regular": is_regular,
        "intersection_strategy": strategy,
        "filter_type": filter_type,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Bounded language test
# ---------------------------------------------------------------------------

def _check_bounded(ir: dict) -> dict | None:
    """Check if language is bounded. Delegates to stratification module.

    Returns bounded_analysis dict or None if not applicable.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind not in ("grammar", "grammar_filter", "repeated_subword"):
        return None

    bounded = is_bounded_language(ir)

    result: dict[str, Any] = {
        "is_bounded": bounded.get("is_bounded"),
        "bounding_words": bounded.get("bounding_words"),
        "explanation": bounded.get("explanation", ""),
    }

    # If bounded and we have a grammar, also run stratification
    if bounded.get("is_bounded") is True and bounded.get("bounding_words"):
        grammar = None
        if kind == "grammar":
            grammar = spec
        elif kind == "grammar_filter":
            grammar = spec.get("grammar")

        if grammar is not None:
            strat = check_stratification(
                grammar, bounded["bounding_words"], max_length=20
            )
            result["stratification"] = {
                "is_stratified": strat.get("is_stratified"),
                "is_cfl": strat.get("is_cfl"),
                "explanation": strat.get("explanation", ""),
            }

    return result


# ---------------------------------------------------------------------------
# Parikh pre-check
# ---------------------------------------------------------------------------

def _parikh_precheck(ir: dict) -> dict | None:
    """Quick Parikh semilinearity check.

    Returns parikh_precheck dict or None if not applicable.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind not in ("grammar", "grammar_filter", "repeated_subword"):
        return None

    parikh = analyze_parikh(ir)

    conclusion = parikh.get("conclusion")

    return {
        "is_semilinear": parikh.get("is_semilinear"),
        "conclusion": conclusion,
        "explanation": parikh.get("explanation", ""),
    }


# ---------------------------------------------------------------------------
# Quick verdict
# ---------------------------------------------------------------------------

def _determine_quick_verdict(
    filter_analysis: dict | None,
    bounded_analysis: dict | None,
    parikh_precheck: dict | None,
) -> tuple[str | None, str | None]:
    """Determine if a quick verdict is possible.

    Returns (verdict, reason) where verdict is "cfl", "non_cfl", or None.
    """
    # Parikh non-semilinear -> definitely not CFL
    if parikh_precheck and parikh_precheck.get("is_semilinear") is False:
        return "non_cfl", "Parikh image is not semilinear, so the language cannot be context-free."

    # If the filter is uncomputable (natural language / complex), we cannot
    # safely give a quick CFL verdict even if the grammar part looks CFL.
    # Bail out early so bounded/Parikh analysis on the grammar alone
    # doesn't produce a misleading verdict.
    filter_uncomputable = (
        filter_analysis is not None
        and filter_analysis.get("filter_is_regular") is None
    )
    if filter_uncomputable:
        return None, None

    # Grammar + regular filter -> CFL (CFL intersect REG = CFL)
    if filter_analysis and filter_analysis.get("filter_is_regular") is True:
        return "cfl", "Grammar generates a CFL; filter is regular; CFL intersect REG = CFL."

    # Bounded + stratification gives answer
    if bounded_analysis and bounded_analysis.get("is_bounded") is True:
        strat = bounded_analysis.get("stratification")
        if strat:
            if strat.get("is_cfl") is True:
                return "cfl", (
                    "Language is bounded and exponent vectors are semilinear "
                    "(Ginsburg-Spanier theorem)."
                )
            if strat.get("is_cfl") is False:
                return "non_cfl", (
                    "Language is bounded but exponent vectors are not semilinear "
                    "(contrapositive of Ginsburg-Spanier)."
                )

    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preprocess_language(ir: dict) -> dict:
    """Preprocess language IR and return enriched analysis.

    Returns a dict with filter_analysis, bounded_analysis,
    parikh_precheck, quick_verdict, and quick_verdict_reason.
    Always returns a complete dict even on malformed input.
    """
    spec = ir.get("language_spec") if isinstance(ir, dict) else None
    if not isinstance(spec, dict):
        return {
            "filter_analysis": None,
            "bounded_analysis": None,
            "parikh_precheck": None,
            "quick_verdict": None,
            "quick_verdict_reason": None,
        }
    kind = spec.get("kind")

    # Filter analysis (only for grammar_filter)
    filter_analysis = None
    filter_uncomputable = False
    if kind == "grammar_filter":
        filt = spec.get("filter", {})
        filter_analysis = _analyze_filter(filt)
        # Natural-language filter or anything we can't classify as regular
        # means we should NOT compute bounded/Parikh over the raw grammar
        # and present it as if it described the whole language.
        if filter_analysis and filter_analysis.get("filter_is_regular") is None:
            filter_uncomputable = True

    if filter_uncomputable:
        # Filter makes the language's shape unknowable — skip bounded/Parikh
        # to avoid giving the classifier misleading "definitive" data.
        bounded_analysis = None
        parikh = None
    else:
        # Bounded language test
        bounded_analysis = _check_bounded(ir)
        # Parikh pre-check
        parikh = _parikh_precheck(ir)

    # Quick verdict
    verdict, reason = _determine_quick_verdict(
        filter_analysis, bounded_analysis, parikh
    )

    return {
        "filter_analysis": filter_analysis,
        "bounded_analysis": bounded_analysis,
        "parikh_precheck": parikh,
        "quick_verdict": verdict,
        "quick_verdict_reason": reason,
    }
