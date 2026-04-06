"""Hypothesis module for DCFL agent system.

Pure-function heuristic analyzer that predicts whether a language
described by a DCFL IR dict is DCFL or not, without invoking an LLM.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REVERSED_VAR_RE = re.compile(r"([a-zA-Z_]\w*)\^R")
_VARIABLE_TOKEN_RE = re.compile(r"[a-zA-Z_]\w*(?:\^R)?")
_FIXED_SYMBOL_RE = re.compile(r"[a-zA-Z0-9]+")


def _extract_reversed_vars(word_pattern: str) -> list[str]:
    """Return names of variables that appear reversed (X^R) in *word_pattern*."""
    return _REVERSED_VAR_RE.findall(word_pattern)


def _extract_all_var_tokens(word_pattern: str) -> list[str]:
    """Return every variable token (with optional ^R suffix) in order."""
    return _VARIABLE_TOKEN_RE.findall(word_pattern)


def _count_palindrome_pairs(word_pattern: str, variables: list[dict[str, Any]]) -> int:
    """Count how many variables appear both plain and reversed."""
    var_names = {v["name"] for v in variables}
    reversed_names = set(_extract_reversed_vars(word_pattern))
    return len(reversed_names & var_names)


def _count_length_cmp_constraints(constraints: list[dict[str, Any]]) -> int:
    """Count constraints of kind ``length_cmp``."""
    return sum(1 for c in constraints if c.get("kind") == "length_cmp")


def _has_disjunction_with_shared_var(
    constraints: list[dict[str, Any]],
    variables: list[dict[str, Any]],
) -> bool:
    """Return *True* if there is a disjunction constraint that references
    a variable also used elsewhere (shared variable)."""
    var_names = {v["name"] for v in variables}
    for c in constraints:
        if c.get("kind") != "disjunction":
            continue
        # args should contain variable references; check overlap with known vars
        args = c.get("args", [])
        mentioned = {a for a in args if a in var_names}
        if len(mentioned) >= 1:
            return True
    return False


def _detect_separator(word_pattern: str, variables: list[dict[str, Any]]) -> str:
    """Determine whether *word_pattern* contains a fixed separator between
    palindromic / counted parts.

    Returns ``"clear_separator"`` | ``"no_separator"`` | ``"ambiguous"``.
    """
    var_names = {v["name"] for v in variables}
    tokens = _extract_all_var_tokens(word_pattern)

    # Walk the pattern and look for fixed literal segments between variable tokens
    remaining = word_pattern
    for tok in tokens:
        idx = remaining.find(tok)
        if idx == -1:
            continue
        remaining = remaining[idx + len(tok):]

    # Rebuild: split pattern by variable tokens; anything left is a literal separator
    parts = re.split(r"[a-zA-Z_]\w*(?:\^R)?", word_pattern)
    # Filter to non-empty literal segments that are not just whitespace
    separators = [p.strip() for p in parts if p.strip()]

    if not separators:
        return "no_separator"

    # If every separator is a single fixed symbol, it's clear
    if all(_FIXED_SYMBOL_RE.fullmatch(s) for s in separators):
        return "clear_separator"

    return "ambiguous"


def _regex_constrained_variables(variables: list[dict[str, Any]]) -> list[str]:
    """Return names of variables whose ``domain`` is non-null (regex-constrained)."""
    return [v["name"] for v in variables if v.get("domain") is not None]


def _detect_nesting(word_pattern: str, variables: list[dict[str, Any]]) -> bool:
    """Return *True* when the pattern has nested palindromes (e.g. ``w v v^R w^R``)."""
    var_names = {v["name"] for v in variables}
    tokens = _extract_all_var_tokens(word_pattern)

    # Build an abstract stack of open/close events
    # open  = plain variable that also has a ^R counterpart
    # close = reversed variable
    reversed_vars = set(_extract_reversed_vars(word_pattern))
    paired_vars = reversed_vars & var_names

    stack: list[str] = []
    for tok in tokens:
        base = tok.replace("^R", "")
        if base not in paired_vars:
            continue
        if tok.endswith("^R"):
            # close
            if stack and stack[-1] != base:
                # closing a different variable than the top → nesting
                return True
            if stack:
                stack.pop()
        else:
            stack.append(base)
    return False


# ---------------------------------------------------------------------------
# Memory analysis
# ---------------------------------------------------------------------------

def _analyze_memory(
    word_pattern: str,
    variables: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> str:
    """Classify the memory requirement of the language.

    Returns ``"single_stack"`` | ``"nested_stack"`` | ``"two_stacks"`` | ``"unknown"``.
    """
    palindrome_pairs = _count_palindrome_pairs(word_pattern, variables)
    length_cmps = _count_length_cmp_constraints(constraints)
    nested = _detect_nesting(word_pattern, variables)

    # Two independent palindrome pairs that are NOT nested → may need two stacks
    if palindrome_pairs >= 2 and not nested:
        return "single_stack"  # sequential palindromes can share one stack

    if nested:
        sep = _detect_separator(word_pattern, variables)
        if sep == "clear_separator":
            return "nested_stack"
        return "two_stacks"

    if palindrome_pairs == 1 or length_cmps >= 1:
        return "single_stack"

    if palindrome_pairs == 0 and length_cmps == 0:
        return "unknown"

    return "unknown"


# ---------------------------------------------------------------------------
# Pattern classification
# ---------------------------------------------------------------------------

def _classify_pattern_set_builder(
    word_pattern: str,
    variables: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> str:
    """Return a pattern code for a set-builder language spec."""
    palindrome_pairs = _count_palindrome_pairs(word_pattern, variables)
    length_cmps = _count_length_cmp_constraints(constraints)
    has_disj = _has_disjunction_with_shared_var(constraints, variables)

    if has_disj:
        return "shared_var_disjunction"

    nested = _detect_nesting(word_pattern, variables)

    if palindrome_pairs == 1 and not nested:
        sep = _detect_separator(word_pattern, variables)
        if sep == "clear_separator":
            return "single_palindrome_sep"
        return "single_palindrome_nosep"

    if palindrome_pairs >= 2:
        if nested:
            return "nested_palindromes"
        return "sequential_palindromes"

    if length_cmps == 1:
        return "single_length_cmp"

    if length_cmps >= 2:
        return "dual_length_cmp"

    return "grammar_analysis"  # fallback


# ---------------------------------------------------------------------------
# Prediction logic
# ---------------------------------------------------------------------------

_PATTERN_PREDICTION: dict[str, tuple[str, float]] = {
    "single_palindrome_sep":   ("likely_dcfl",      0.85),
    "single_palindrome_nosep": ("likely_non_dcfl",   0.80),
    "sequential_palindromes":  ("likely_dcfl",       0.70),
    "shared_var_disjunction":  ("likely_non_dcfl",   0.85),
    "single_length_cmp":       ("likely_dcfl",       0.80),
    "grammar_analysis":        ("uncertain",         0.40),
}


def _predict(
    pattern_type: str,
    memory_analysis: str,
    separator_analysis: str,
) -> tuple[str, float]:
    """Return ``(prediction, confidence)``."""
    if pattern_type in _PATTERN_PREDICTION:
        return _PATTERN_PREDICTION[pattern_type]

    if pattern_type == "nested_palindromes":
        if separator_analysis == "clear_separator":
            return ("likely_dcfl", 0.65)
        return ("likely_non_dcfl", 0.70)

    if pattern_type == "dual_length_cmp":
        # Two length comparisons: compatible if they share the same stack direction
        if memory_analysis == "single_stack":
            return ("likely_dcfl", 0.60)
        return ("uncertain", 0.45)

    return ("uncertain", 0.30)


def _build_reasoning(
    pattern_type: str,
    memory_analysis: str,
    separator_analysis: str,
    disjunction_present: bool,
    regex_vars: list[str],
    prediction: str,
) -> str:
    """Produce a short human-readable explanation."""
    parts: list[str] = []

    parts.append(f"Pattern classified as '{pattern_type}'.")

    if memory_analysis != "unknown":
        parts.append(f"Memory model: {memory_analysis}.")

    if separator_analysis == "clear_separator":
        parts.append("A clear fixed separator was detected between palindromic/counted segments.")
    elif separator_analysis == "no_separator":
        parts.append("No fixed separator found between segments.")

    if disjunction_present:
        parts.append("A disjunction with a shared variable is present, "
                      "which typically requires non-determinism.")

    if regex_vars:
        parts.append(f"Variables with regex-constrained domains: {', '.join(regex_vars)}.")

    verdict_map = {
        "likely_dcfl": "The language is likely deterministic context-free.",
        "likely_non_dcfl": "The language is likely NOT deterministic context-free.",
        "uncertain": "Cannot determine DCFL membership with confidence from heuristics alone.",
    }
    parts.append(verdict_map.get(prediction, ""))

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_dcfl_hypothesis(ir: dict[str, Any]) -> dict[str, Any]:
    """Analyze a DCFL IR and return a HypothesisResult dict.

    Parameters
    ----------
    ir:
        A DCFL IR dict conforming to the schema in ``dcfl_ir_schema.py``.
        Must contain ``input_format``, ``language_spec``, and ``alphabet``.

    Returns
    -------
    dict
        A ``HypothesisResult`` with keys: ``pattern_type``,
        ``memory_analysis``, ``separator_analysis``, ``disjunction_present``,
        ``regex_constrained_vars``, ``prediction``, ``confidence``,
        ``reasoning``.
    """
    input_format: str = ir.get("input_format", "")
    language_spec: dict[str, Any] = ir.get("language_spec", {})
    kind: str = language_spec.get("kind", input_format)

    # ----- Grammar path (format 2) -----
    if kind == "grammar":
        return {
            "pattern_type": "grammar_analysis",
            "memory_analysis": "unknown",
            "separator_analysis": "ambiguous",
            "disjunction_present": False,
            "regex_constrained_vars": [],
            "prediction": "uncertain",
            "confidence": 0.40,
            "reasoning": (
                "Pattern classified as 'grammar_analysis'. "
                "Grammar-based input requires deeper analysis; "
                "cannot determine DCFL membership from heuristics alone."
            ),
        }

    # ----- Set-builder path (format 1) -----
    word_pattern: str = language_spec.get("word_pattern", "")
    variables: list[dict[str, Any]] = language_spec.get("variables", [])
    constraints: list[dict[str, Any]] = language_spec.get("constraints", [])

    pattern_type = _classify_pattern_set_builder(word_pattern, variables, constraints)
    memory_analysis = _analyze_memory(word_pattern, variables, constraints)
    separator_analysis = _detect_separator(word_pattern, variables)
    disjunction_present = _has_disjunction_with_shared_var(constraints, variables)
    regex_vars = _regex_constrained_variables(variables)

    prediction, confidence = _predict(pattern_type, memory_analysis, separator_analysis)

    reasoning = _build_reasoning(
        pattern_type,
        memory_analysis,
        separator_analysis,
        disjunction_present,
        regex_vars,
        prediction,
    )

    return {
        "pattern_type": pattern_type,
        "memory_analysis": memory_analysis,
        "separator_analysis": separator_analysis,
        "disjunction_present": disjunction_present,
        "regex_constrained_vars": regex_vars,
        "prediction": prediction,
        "confidence": confidence,
        "reasoning": reasoning,
    }
