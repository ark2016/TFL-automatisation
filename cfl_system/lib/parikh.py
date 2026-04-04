"""
Parikh image computation and semilinearity analysis.

The Parikh image maps a word to its letter-count vector.
By Parikh's theorem, every CFL has a semilinear Parikh image.
A non-semilinear Parikh image implies the language is NOT context-free.
"""

from __future__ import annotations

from collections import deque
from itertools import product
from math import gcd
from functools import reduce
from typing import Any


# ---------------------------------------------------------------------------
# Parikh vector
# ---------------------------------------------------------------------------

def parikh_vector(word: str, alphabet: list[str]) -> tuple[int, ...]:
    """Compute Parikh vector of *word* over the given ordered *alphabet*.

    Each component counts the number of occurrences of the corresponding
    alphabet symbol in *word*.

    >>> parikh_vector("aabb", ["a", "b"])
    (2, 2)
    """
    return tuple(word.count(sym) for sym in alphabet)


# ---------------------------------------------------------------------------
# Word generation helpers (BFS derivation from a grammar)
# ---------------------------------------------------------------------------

def _generate_words_from_grammar(grammar: dict, max_length: int) -> set[str]:
    """BFS-derive terminal words from *grammar* up to *max_length*.

    *grammar* must have keys: start, rules, terminals, nonterminals.
    Returns a set of terminal strings.
    """
    start = grammar["start"]
    rules = grammar["rules"]
    terminals = set(grammar["terminals"])
    nonterminals = set(grammar["nonterminals"])

    # Group rules by LHS
    rules_by_lhs: dict[str, list[list[str]]] = {}
    for rule in rules:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        rules_by_lhs.setdefault(lhs, []).append(rhs)

    # BFS over sentential forms
    visited: set[tuple[str, ...]] = set()
    queue: deque[list[str]] = deque()
    queue.append([start])
    visited.add((start,))

    words: set[str] = set()

    while queue:
        form = queue.popleft()

        # Check if terminal string
        if all(sym in terminals or sym == "" for sym in form):
            w = "".join(form)
            if len(w) <= max_length:
                words.add(w)
            continue

        # Total length lower bound (count terminals already present)
        terminal_count = sum(1 for sym in form if sym in terminals)
        if terminal_count > max_length:
            continue

        # Find leftmost nonterminal and expand it
        for i, sym in enumerate(form):
            if sym in nonterminals:
                for rhs in rules_by_lhs.get(sym, []):
                    new_form = form[:i] + rhs + form[i + 1:]
                    # Prune if too long (count only terminals; nonterminals
                    # might still produce epsilon)
                    t_count = sum(1 for s in new_form if s in terminals)
                    if t_count > max_length:
                        continue
                    if len(new_form) > max_length * 3:
                        # Hard cap to avoid runaway forms
                        continue
                    key = tuple(new_form)
                    if key not in visited:
                        visited.add(key)
                        queue.append(new_form)
                break  # only expand leftmost nonterminal

    return words


# ---------------------------------------------------------------------------
# Parikh image from grammar
# ---------------------------------------------------------------------------

def parikh_image_from_grammar(
    grammar: dict, max_length: int = 15
) -> set[tuple[int, ...]]:
    """Generate words from *grammar* up to *max_length* and return
    the set of their Parikh vectors.

    The alphabet is taken from ``grammar["terminals"]`` (sorted for
    deterministic ordering).
    """
    alphabet = sorted(grammar["terminals"])
    words = _generate_words_from_grammar(grammar, max_length)
    return {parikh_vector(w, alphabet) for w in words}


# ---------------------------------------------------------------------------
# Semilinearity analysis
# ---------------------------------------------------------------------------

def _analyze_1d(values: list[int]) -> dict:
    """Analyze a sorted list of non-negative integers for semilinearity.

    A 1-D set is semilinear iff it is eventually periodic — i.e. a finite
    union of arithmetic progressions.
    """
    if not values:
        return {
            "is_semilinear": True,
            "linear_sets": [],
            "explanation": "Empty set is trivially semilinear.",
        }

    vals = sorted(set(values))

    if len(vals) == 1:
        return {
            "is_semilinear": True,
            "linear_sets": [{"base": (vals[0],), "periods": []}],
            "explanation": f"Singleton set {{{vals[0]}}} is semilinear.",
        }

    # Compute consecutive differences
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]

    # Check if all differences are equal (single arithmetic progression)
    if len(set(diffs)) == 1:
        period = diffs[0]
        return {
            "is_semilinear": True,
            "linear_sets": [{"base": (vals[0],), "periods": [(period,)]}],
            "explanation": (
                f"Single arithmetic progression: base={vals[0]}, step={period}."
            ),
        }

    # Check if differences eventually stabilise (become periodic)
    # Look for a repeating pattern in the last half of diffs
    if len(diffs) >= 4:
        # Check if the last N diffs are all the same
        tail = diffs[len(diffs) // 2:]
        if len(set(tail)) == 1:
            return {
                "is_semilinear": True,
                "linear_sets": None,
                "explanation": (
                    "Differences eventually stabilise — likely a finite union "
                    "of arithmetic progressions."
                ),
            }

    # Check for quadratic growth (non-semilinear indicator)
    # If diffs are strictly increasing, suspect n^2 growth
    if len(diffs) >= 3 and all(diffs[i] < diffs[i + 1] for i in range(len(diffs) - 1)):
        # Verify: second differences constant → quadratic
        second_diffs = [diffs[i + 1] - diffs[i] for i in range(len(diffs) - 1)]
        if len(set(second_diffs)) == 1 and second_diffs[0] > 0:
            return {
                "is_semilinear": False,
                "linear_sets": None,
                "explanation": (
                    "Quadratic growth detected (constant second differences). "
                    "Not an eventually periodic set → not semilinear."
                ),
            }

    # Try decomposing into a small number of arithmetic progressions
    # by grouping values by residue classes
    for modulus in range(1, min(len(vals), 8)):
        residue_groups: dict[int, list[int]] = {}
        for v in vals:
            residue_groups.setdefault(v % modulus if modulus else 0, []).append(v)

        all_ap = True
        for _res, group in residue_groups.items():
            if len(group) < 2:
                continue
            g_diffs = [group[i + 1] - group[i] for i in range(len(group) - 1)]
            if len(set(g_diffs)) != 1:
                all_ap = False
                break

        if all_ap:
            linear_sets = []
            for _res, group in residue_groups.items():
                base = (group[0],)
                if len(group) >= 2:
                    periods = [(group[1] - group[0],)]
                else:
                    periods = []
                linear_sets.append({"base": base, "periods": periods})
            return {
                "is_semilinear": True,
                "linear_sets": linear_sets,
                "explanation": (
                    f"Decomposed into {len(linear_sets)} arithmetic "
                    f"progression(s) (modulus {modulus})."
                ),
            }

    return {
        "is_semilinear": None,
        "linear_sets": None,
        "explanation": "Could not determine semilinearity from sampled data.",
    }


def _vectors_to_projections(
    vectors: set[tuple[int, ...]], dim: int
) -> list[list[int]]:
    """Project vectors onto each coordinate axis."""
    projections: list[list[int]] = [[] for _ in range(dim)]
    for v in vectors:
        for i in range(dim):
            projections[i].append(v[i])
    return projections


def check_semilinearity(
    vectors: set[tuple[int, ...]], alphabet_size: int
) -> dict:
    """Heuristic semilinearity check for a finite sample of Parikh vectors.

    Returns a dict with keys ``is_semilinear``, ``linear_sets``,
    ``explanation``.

    * ``True`` — the sample is consistent with a semilinear set and a
      decomposition was found.
    * ``False`` — the sample exhibits a pattern (e.g. quadratic growth)
      that is incompatible with semilinearity.
    * ``None`` — inconclusive.
    """
    if not vectors:
        return {
            "is_semilinear": True,
            "linear_sets": [],
            "explanation": "Empty set is trivially semilinear.",
        }

    dim = alphabet_size
    if dim == 0:
        return {
            "is_semilinear": True,
            "linear_sets": [],
            "explanation": "Alphabet is empty; only the empty word is possible.",
        }

    # --- 1-D shortcut ---
    if dim == 1:
        vals = sorted(v[0] for v in vectors)
        return _analyze_1d(vals)

    # --- Multi-dimensional ---

    # 1. Check each projection independently
    projections = _vectors_to_projections(vectors, dim)
    proj_results = [_analyze_1d(sorted(set(p))) for p in projections]

    any_non_semilinear = any(r["is_semilinear"] is False for r in proj_results)
    if any_non_semilinear:
        axis = next(
            i for i, r in enumerate(proj_results) if r["is_semilinear"] is False
        )
        return {
            "is_semilinear": False,
            "linear_sets": None,
            "explanation": (
                f"Projection onto axis {axis} is not semilinear: "
                f"{proj_results[axis]['explanation']}"
            ),
        }

    # 2. Check if all vectors lie on a single linear set
    #    v = base + k * period for some base and period vectors.
    sorted_vecs = sorted(vectors)
    if len(sorted_vecs) >= 2:
        base = sorted_vecs[0]
        # Compute diffs from base
        diffs = [
            tuple(v[i] - base[i] for i in range(dim))
            for v in sorted_vecs[1:]
        ]
        # Check if all diffs are integer multiples of the first diff
        d0 = diffs[0]
        if all(x == 0 for x in d0):
            # All vectors might be the same
            if all(all(x == 0 for x in d) for d in diffs):
                return {
                    "is_semilinear": True,
                    "linear_sets": [{"base": base, "periods": []}],
                    "explanation": "All vectors are identical — singleton set.",
                }
        else:
            # Find a non-zero component of d0 to use as reference
            ref_idx = next((i for i in range(dim) if d0[i] != 0), None)
            if ref_idx is not None:
                all_collinear = True
                for d in diffs:
                    if d0[ref_idx] == 0:
                        all_collinear = False
                        break
                    # Check d = k * d0 for some integer k
                    k_num = d[ref_idx]
                    k_den = d0[ref_idx]
                    if k_num % k_den != 0:
                        all_collinear = False
                        break
                    k = k_num // k_den
                    if k < 0:
                        all_collinear = False
                        break
                    if any(d[j] != k * d0[j] for j in range(dim)):
                        all_collinear = False
                        break

                if all_collinear:
                    return {
                        "is_semilinear": True,
                        "linear_sets": [
                            {"base": base, "periods": [d0]}
                        ],
                        "explanation": (
                            f"Single linear set: base={base}, period={d0}."
                        ),
                    }

    # 3. All projections semilinear, but can't find simple joint structure
    all_proj_semilinear = all(r["is_semilinear"] is True for r in proj_results)
    if all_proj_semilinear and len(vectors) <= 30:
        return {
            "is_semilinear": True,
            "linear_sets": None,
            "explanation": (
                "All projections are semilinear and sample is small — "
                "consistent with semilinearity."
            ),
        }

    return {
        "is_semilinear": None,
        "linear_sets": None,
        "explanation": (
            "Multi-dimensional analysis inconclusive. "
            "Projections are individually semilinear but joint structure "
            "could not be determined from sampled data."
        ),
    }


# ---------------------------------------------------------------------------
# High-level analysis
# ---------------------------------------------------------------------------

def _sample_words_repeated_subword(ir_spec: dict, max_total: int = 15) -> set[str]:
    """Generate sample words from a repeated_subword language_spec.

    Enumerate small assignments for each part and concatenate.
    """
    parts = ir_spec["parts"]
    concat_pattern = ir_spec["concat_pattern"]
    alphabets = ir_spec["alphabets"]

    # Generate short words for each part
    part_words: dict[str, list[str]] = {}
    for part in parts:
        syms = alphabets[part]
        words: list[str] = []
        # Generate words of lengths 0..max_per_part from the part's alphabet
        max_per_part = min(4, max_total // max(len(concat_pattern), 1))
        for length in range(0, max_per_part + 1):
            for combo in product(syms, repeat=length):
                words.append("".join(combo))
        part_words[part] = words

    # Enumerate combinations (limit to avoid explosion)
    result: set[str] = set()
    # Build assignments: one word per part
    part_list = list(parts)
    word_lists = [part_words[p] for p in part_list]

    count = 0
    max_combos = 2000
    for combo in product(*word_lists):
        assignment = dict(zip(part_list, combo))
        w = "".join(assignment[p] for p in concat_pattern)
        if len(w) <= max_total:
            # Check constraints (basic: length > 0)
            ok = True
            for c in ir_spec.get("constraints", []):
                if c.get("op") == "gt":
                    left = c.get("left", {})
                    right = c.get("right", {})
                    if (left.get("kind") == "length"
                            and right.get("kind") == "constant"):
                        var = left["of_var"]
                        val = right["value"]
                        if len(assignment.get(var, "")) <= val:
                            ok = False
                            break
                elif c.get("op") == "eq":
                    left = c.get("left", {})
                    right = c.get("right", {})
                    if (left.get("kind") == "length"
                            and right.get("kind") == "length"):
                        v1 = left["of_var"]
                        v2 = right["of_var"]
                        if len(assignment.get(v1, "")) != len(assignment.get(v2, "")):
                            ok = False
                            break
            if ok:
                result.add(w)
        count += 1
        if count >= max_combos:
            break

    return result


def _get_alphabet_from_ir(ir: dict) -> list[str]:
    """Extract a sorted alphabet from an IR dict."""
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")

    if kind == "grammar" or kind == "grammar_filter":
        g = spec if kind == "grammar" else spec.get("grammar", {})
        return sorted(g.get("terminals", []))

    if kind == "repeated_subword":
        syms: set[str] = set()
        for alpha in spec.get("alphabets", {}).values():
            syms.update(alpha)
        return sorted(syms)

    return []


def analyze_parikh(ir: dict) -> dict:
    """High-level Parikh image analysis for a CFL IR dict.

    Returns a dict with human-readable description, semilinearity
    verdict, sampled vectors, explanation, and optional conclusion.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")
    alphabet = _get_alphabet_from_ir(ir)

    vectors: set[tuple[int, ...]] = set()

    if kind == "grammar":
        vectors = parikh_image_from_grammar(spec, max_length=15)
    elif kind == "grammar_filter":
        grammar = spec.get("grammar", {})
        all_vectors = parikh_image_from_grammar(grammar, max_length=15)
        # Apply filter: for now, support count-based equality filters
        filt = spec.get("filter", {})
        if filt.get("op") == "eq":
            left = filt.get("left", {})
            right = filt.get("right", {})
            if (left.get("kind") == "count_symbol"
                    and right.get("kind") == "count_symbol"):
                sym_a = left["symbol"]
                sym_b = right["symbol"]
                idx_a = alphabet.index(sym_a) if sym_a in alphabet else None
                idx_b = alphabet.index(sym_b) if sym_b in alphabet else None
                if idx_a is not None and idx_b is not None:
                    vectors = {v for v in all_vectors if v[idx_a] == v[idx_b]}
                else:
                    vectors = all_vectors
            else:
                vectors = all_vectors
        else:
            vectors = all_vectors
    elif kind == "repeated_subword":
        words = _sample_words_repeated_subword(spec, max_total=15)
        vectors = {parikh_vector(w, alphabet) for w in words}
    else:
        return {
            "commutative_image": f"Unsupported language kind: {kind}",
            "is_semilinear": None,
            "vectors_sampled": [],
            "explanation": f"Cannot compute Parikh image for kind '{kind}'.",
            "conclusion": None,
        }

    semi = check_semilinearity(vectors, len(alphabet))

    conclusion = None
    if semi["is_semilinear"] is False:
        conclusion = "non_semilinear → not CFL"

    # Build commutative image description
    if len(vectors) <= 10:
        ci_desc = f"Sampled vectors: {sorted(vectors)}"
    else:
        sample = sorted(vectors)[:5]
        ci_desc = f"Sampled {len(vectors)} vectors, first 5: {sample}"

    return {
        "commutative_image": ci_desc,
        "is_semilinear": semi["is_semilinear"],
        "vectors_sampled": sorted(vectors),
        "explanation": semi["explanation"],
        "conclusion": conclusion,
    }
