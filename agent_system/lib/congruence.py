"""
Guarded Myhill-Nerode congruence computation.

Per §4.7: compute Nerode equivalence classes within bounded depth
to avoid infinite computation.  Used by the Nerode Agent to estimate
whether a language has finite or infinite index.
"""

from __future__ import annotations

from itertools import product as cart_product
from typing import Callable


# ---------------------------------------------------------------------------
# Helper: word generation
# ---------------------------------------------------------------------------

def _words_up_to(alphabet: list[str], max_len: int) -> list[str]:
    """Generate all words over *alphabet* with length 0..max_len."""
    words: list[str] = [""]
    for length in range(1, max_len + 1):
        for combo in cart_product(alphabet, repeat=length):
            words.append("".join(combo))
    return words


def _words_of_length(alphabet: list[str], length: int) -> list[str]:
    """Generate all words over *alphabet* of exactly *length*."""
    if length == 0:
        return [""]
    return ["".join(combo) for combo in cart_product(alphabet, repeat=length)]


# ---------------------------------------------------------------------------
# Core: find a distinguishing context
# ---------------------------------------------------------------------------

def find_distinguishing_context(
    oracle: Callable[[str], bool],
    word1: str,
    word2: str,
    alphabet: list[str],
    max_len: int = 8,
) -> str | None:
    """Find a context *z* such that ``oracle(word1+z) != oracle(word2+z)``.

    Tries all contexts of length 0, 1, ..., *max_len* (shortest first).
    Returns the distinguishing context, or ``None`` if none is found.
    """
    for ctx_len in range(0, max_len + 1):
        for combo in cart_product(alphabet, repeat=ctx_len) if ctx_len > 0 else [()]:
            z = "".join(combo)
            if oracle(word1 + z) != oracle(word2 + z):
                return z
    return None


# ---------------------------------------------------------------------------
# Core: compute Nerode equivalence classes
# ---------------------------------------------------------------------------

def compute_nerode_classes(
    oracle: Callable[[str], bool],
    alphabet: list[str],
    max_depth: int = 8,
) -> dict:
    """Build Nerode equivalence classes for words up to length *max_depth*.

    Uses incremental refinement: for each new word, test it against the
    representatives of existing classes.  If no existing class matches,
    a new class is created and the distinguishing context is recorded.

    Returns a dict with keys:
        classes, num_classes, growth_pattern, likely_infinite,
        distinguishing_pairs.
    """
    # Context length used when testing equivalence of two words.
    # Shorter than max_depth to keep oracle calls manageable.
    ctx_max = min(max_depth, 6)

    # Each class: (representative, member_list, accepting_flag)
    classes: list[tuple[str, list[str], bool]] = []
    distinguishing_pairs: list[dict] = []

    # Track how many classes exist after processing each depth level.
    growth_pattern: list[int] = []

    # Process words depth-by-depth so we can record the growth pattern.
    for depth in range(0, max_depth + 1):
        words_at_depth = _words_of_length(alphabet, depth)

        for w in words_at_depth:
            placed = False
            for cls_rep, cls_members, _ in classes:
                ctx = find_distinguishing_context(
                    oracle, w, cls_rep, alphabet, ctx_max,
                )
                if ctx is None:
                    # No distinguishing context found — treat as equivalent.
                    cls_members.append(w)
                    placed = True
                    break
                # Record the first distinguishing pair we encounter for
                # this word (only when a *new* class is about to be created
                # do we store the pair — see below).  Here we just continue
                # searching remaining classes.

            if not placed:
                # New equivalence class.
                acc = oracle(w)
                classes.append((w, [w], acc))

                # Record distinguishing pair with *some* existing class rep
                # (pick the first one that is distinguishable).
                if len(classes) > 1:
                    for prev_rep, _, _ in classes[:-1]:
                        ctx = find_distinguishing_context(
                            oracle, w, prev_rep, alphabet, ctx_max,
                        )
                        if ctx is not None:
                            r1 = oracle(w + ctx)
                            r2 = oracle(prev_rep + ctx)
                            distinguishing_pairs.append({
                                "word1": prev_rep,
                                "word2": w,
                                "context": ctx,
                                "reason": (
                                    f"oracle({prev_rep + ctx!r})={r1} but "
                                    f"oracle({w + ctx!r})={r2}"
                                ),
                            })
                            break

        growth_pattern.append(len(classes))

    # Decide whether infinite index is likely.
    likely_infinite = _is_likely_infinite(growth_pattern)

    # Build the output class list.
    out_classes = [
        {
            "representative": rep if rep != "" else "\u03b5",
            "members": members,
            "accepting": acc,
        }
        for rep, members, acc in classes
    ]

    return {
        "classes": out_classes,
        "num_classes": len(classes),
        "growth_pattern": growth_pattern,
        "likely_infinite": likely_infinite,
        "distinguishing_pairs": distinguishing_pairs,
    }


# ---------------------------------------------------------------------------
# Heuristic: growth-pattern analysis
# ---------------------------------------------------------------------------

def _is_likely_infinite(growth_pattern: list[int]) -> bool:
    """Return ``True`` when the class count never stabilises.

    Stabilisation = same count for 3+ consecutive depth levels.
    """
    if len(growth_pattern) < 4:
        return False

    # Check whether there is a run of 3+ identical values anywhere.
    for i in range(len(growth_pattern) - 2):
        if (
            growth_pattern[i]
            == growth_pattern[i + 1]
            == growth_pattern[i + 2]
        ):
            return False

    # If no stabilisation seen and the count keeps rising, likely infinite.
    return True


# ---------------------------------------------------------------------------
# Public: estimate Nerode index
# ---------------------------------------------------------------------------

def estimate_index(
    oracle: Callable[[str], bool],
    alphabet: list[str],
    max_depth: int = 10,
) -> dict:
    """Estimate whether the Nerode index is finite or infinite.

    Returns a dict with keys:
        estimated_index  — an int or the string ``"infinite"``
        confidence       — float in [0, 1]
        growth_pattern   — class counts per depth level
        reason           — human-readable justification
    """
    result = compute_nerode_classes(oracle, alphabet, max_depth)
    gp = result["growth_pattern"]

    # Look for stabilisation: 3+ consecutive identical values.
    stable_idx: int | None = None
    for i in range(len(gp) - 2):
        if gp[i] == gp[i + 1] == gp[i + 2]:
            stable_idx = i
            break

    if stable_idx is not None:
        index = gp[stable_idx]
        # Confidence increases with the length of the stable run.
        run_len = 0
        for j in range(stable_idx, len(gp)):
            if gp[j] == index:
                run_len += 1
            else:
                break
        confidence = min(1.0, 0.6 + 0.1 * run_len)
        reason = (
            f"Class count stabilized at {index} for depths "
            f"{stable_idx}-{stable_idx + run_len - 1}"
        )
        return {
            "estimated_index": index,
            "confidence": round(confidence, 2),
            "growth_pattern": gp,
            "reason": reason,
        }

    # No stabilisation — check whether growth is monotone.
    monotone_increasing = all(
        gp[i + 1] >= gp[i] for i in range(len(gp) - 1)
    )
    still_growing = len(gp) >= 2 and gp[-1] > gp[-2]

    if monotone_increasing and still_growing:
        confidence = min(1.0, 0.5 + 0.05 * len(gp))
        reason = (
            f"Class count grew from {gp[0]} to {gp[-1]} "
            f"over {len(gp)} depth levels without stabilizing"
        )
        return {
            "estimated_index": "infinite",
            "confidence": round(confidence, 2),
            "growth_pattern": gp,
            "reason": reason,
        }

    # Ambiguous case: not monotone, not stabilised.
    confidence = 0.3
    reason = (
        "Growth pattern is non-monotone and did not stabilize; "
        "index estimation is uncertain"
    )
    return {
        "estimated_index": gp[-1],
        "confidence": round(confidence, 2),
        "growth_pattern": gp,
        "reason": reason,
    }
