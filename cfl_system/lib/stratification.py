"""
Bounded language test and stratification analysis.

A bounded language has the form L ⊆ w₁* · w₂* · … · wₙ*.
For bounded CFLs the Ginsburg–Spanier theorem provides an algorithmic
characterisation via stratified semilinear exponent sets.
"""

from __future__ import annotations

from typing import Any

from cfl_system.lib.parikh import (
    _generate_words_from_grammar,
    check_semilinearity,
    parikh_image_from_grammar,
)


# ---------------------------------------------------------------------------
# Bounded language detection
# ---------------------------------------------------------------------------

def _detect_bounding_words_repeated_subword(spec: dict) -> dict:
    """Check if a repeated_subword language is bounded.

    If every part's alphabet is a single character, the language is
    bounded by that character's Kleene star for each slot in the
    concat_pattern.
    """
    alphabets = spec.get("alphabets", {})
    concat_pattern = spec.get("concat_pattern", [])

    single_char_parts = all(len(v) == 1 for v in alphabets.values())
    if single_char_parts:
        bounding = [alphabets[p][0] for p in concat_pattern]
        return {
            "is_bounded": True,
            "bounding_words": bounding,
            "explanation": (
                f"Each part uses a single-character alphabet, so "
                f"L ⊆ {'·'.join(w + '*' for w in bounding)}."
            ),
        }

    # Multi-char alphabets — the language might still be bounded but we
    # cannot easily determine the bounding words.
    for part, alpha in alphabets.items():
        if len(alpha) > 1:
            return {
                "is_bounded": None,
                "bounding_words": None,
                "explanation": (
                    f"Part '{part}' has multi-character alphabet {alpha}; "
                    f"cannot determine bounding from structure alone."
                ),
            }

    return {
        "is_bounded": None,
        "bounding_words": None,
        "explanation": "Could not determine if language is bounded.",
    }


def _detect_bounding_words_grammar(spec: dict) -> dict:
    """Heuristic check for whether a grammar generates a bounded language.

    Simple case: if the grammar only uses single terminal characters and
    all rules produce terminals in a fixed left-to-right order, the
    language may be bounded.  This is a rough heuristic.
    """
    terminals = spec.get("terminals", [])

    # Very simple case: single terminal → L ⊆ a*
    if len(terminals) == 1:
        return {
            "is_bounded": True,
            "bounding_words": [terminals[0]],
            "explanation": (
                f"Single terminal '{terminals[0]}': L ⊆ {terminals[0]}*."
            ),
        }

    # For two terminals with rules of the form S → aSb | ε
    # this produces {aⁿbⁿ} ⊆ a*b* — bounded.
    # We check whether every rule's terminals appear in non-decreasing
    # alphabetical order.
    rules = spec.get("rules", [])
    sorted_terminals = sorted(terminals)
    terminal_set = set(terminals)

    all_ordered = True
    for rule in rules:
        rhs_terminals = [s for s in rule.get("rhs", []) if s in terminal_set]
        indices = []
        for t in rhs_terminals:
            indices.append(sorted_terminals.index(t))
        if indices != sorted(indices):
            all_ordered = False
            break

    if all_ordered and len(terminals) >= 2:
        return {
            "is_bounded": True,
            "bounding_words": sorted_terminals,
            "explanation": (
                f"All rules emit terminals in non-decreasing order "
                f"({', '.join(sorted_terminals)}): L ⊆ "
                f"{'·'.join(t + '*' for t in sorted_terminals)}."
            ),
        }

    return {
        "is_bounded": None,
        "bounding_words": None,
        "explanation": (
            "Grammar structure does not obviously yield a bounded language."
        ),
    }


def is_bounded_language(ir: dict) -> dict:
    """Check whether the language described by *ir* is bounded.

    Returns a dict with ``is_bounded``, ``bounding_words``,
    ``explanation``.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "repeated_subword":
        return _detect_bounding_words_repeated_subword(spec)

    if kind in ("grammar", "grammar_filter"):
        g = spec if kind == "grammar" else spec.get("grammar", {})
        return _detect_bounding_words_grammar(g)

    return {
        "is_bounded": None,
        "bounding_words": None,
        "explanation": f"Cannot determine boundedness for kind '{kind}'.",
    }


# ---------------------------------------------------------------------------
# Word decomposition
# ---------------------------------------------------------------------------

def _decompose_word(word: str, bounding_words: list[str]) -> tuple[int, ...] | None:
    """Decompose *word* as w₁^k₁ · w₂^k₂ · … · wₙ^kₙ.

    Uses a greedy left-to-right approach: consume as many copies of the
    current bounding word as possible, then move to the next one.

    Returns the exponent tuple (k₁, …, kₙ) or ``None`` if
    decomposition fails.
    """
    exponents: list[int] = []
    pos = 0
    for bw in bounding_words:
        count = 0
        bw_len = len(bw)
        if bw_len == 0:
            # ε* = {ε}, contributes exponent 0
            exponents.append(0)
            continue
        while pos + bw_len <= len(word) and word[pos: pos + bw_len] == bw:
            count += 1
            pos += bw_len
        exponents.append(count)

    if pos == len(word):
        return tuple(exponents)
    return None


# ---------------------------------------------------------------------------
# Stratification check
# ---------------------------------------------------------------------------

def check_stratification(
    grammar: dict,
    bounding_words: list[str],
    max_length: int = 20,
) -> dict:
    """Check if a bounded CFL satisfies the stratification criterion.

    Generates words from *grammar*, decomposes each into the given
    bounding structure, and checks whether the exponent vectors form a
    semilinear set (as required by the Ginsburg–Spanier theorem for
    bounded CFLs).

    Returns a dict with ``is_stratified``, ``is_cfl``,
    ``exponent_vectors``, ``explanation``.
    """
    if not bounding_words:
        return {
            "is_stratified": None,
            "is_cfl": None,
            "exponent_vectors": [],
            "explanation": "No bounding words provided.",
        }

    words = _generate_words_from_grammar(grammar, max_length)

    if not words:
        return {
            "is_stratified": True,
            "is_cfl": True,
            "exponent_vectors": [],
            "explanation": (
                "Grammar generates no words up to the length bound — "
                "empty language is trivially CFL."
            ),
        }

    exponent_vectors: set[tuple[int, ...]] = set()
    decomposition_failures: list[str] = []

    for w in sorted(words):
        ev = _decompose_word(w, bounding_words)
        if ev is not None:
            exponent_vectors.add(ev)
        else:
            decomposition_failures.append(w)

    if decomposition_failures:
        return {
            "is_stratified": None,
            "is_cfl": None,
            "exponent_vectors": sorted(exponent_vectors),
            "explanation": (
                f"{len(decomposition_failures)} word(s) could not be "
                f"decomposed into the bounding structure "
                f"({'·'.join(w + '*' for w in bounding_words)}). "
                f"Examples: {decomposition_failures[:3]}"
            ),
        }

    n_dims = len(bounding_words)
    semi = check_semilinearity(exponent_vectors, n_dims)

    is_stratified = semi["is_semilinear"]
    if is_stratified is True:
        is_cfl = True
        explanation = (
            f"Exponent vectors form a semilinear set ({len(exponent_vectors)} "
            f"vectors over {n_dims} dimensions). {semi['explanation']} "
            f"Consistent with CFL (Ginsburg–Spanier)."
        )
    elif is_stratified is False:
        is_cfl = False
        explanation = (
            f"Exponent vectors are NOT semilinear. {semi['explanation']} "
            f"By contrapositive of Ginsburg–Spanier, the language is not CFL."
        )
    else:
        is_cfl = None
        explanation = (
            f"Semilinearity of exponent vectors is inconclusive. "
            f"{semi['explanation']}"
        )

    return {
        "is_stratified": is_stratified,
        "is_cfl": is_cfl,
        "exponent_vectors": sorted(exponent_vectors),
        "explanation": explanation,
    }
