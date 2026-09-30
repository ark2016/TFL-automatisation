"""
Parikh image computation and semilinearity analysis.

The Parikh image maps a word to its letter-count vector.
By Parikh's theorem, every CFL has a semilinear Parikh image.
A non-semilinear Parikh image implies the language is NOT context-free.
"""

from __future__ import annotations

from collections import deque
from itertools import product


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

def _is_valid_cfg(grammar: dict) -> bool:
    """Validate the explicit character-CFG contract before using a theorem."""
    if not isinstance(grammar, dict):
        return False
    terms, nonterms = grammar.get("terminals"), grammar.get("nonterminals")
    if not isinstance(terms, list) or not isinstance(nonterms, list) or not nonterms:
        return False
    if any(not isinstance(s, str) or len(s) != 1 for s in terms):
        return False
    if any(not isinstance(s, str) or not s for s in nonterms):
        return False
    terminals, nonterminals = set(terms), set(nonterms)
    if terminals & nonterminals or grammar.get("start") not in nonterms:
        return False
    rules = grammar.get("rules")
    if not isinstance(rules, list):
        return False
    symbols = terminals | nonterminals
    return all(
        isinstance(rule, dict) and isinstance(rule.get("lhs"), str)
        and rule["lhs"] in nonterminals and isinstance(rule.get("rhs"), list)
        and all(isinstance(s, str) and s in symbols for s in rule["rhs"])
        for rule in rules
    )


def _generate_words_from_grammar(grammar: dict, max_length: int) -> set[str]:
    """BFS-derive terminal words from *grammar* up to *max_length*.

    *grammar* must have keys: start, rules, terminals, nonterminals.
    Returns sampled terminal strings. The sentential-form cap may omit
    words even within the length bound, so this is not a complete image.
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
    """Describe finite observed gaps; never decide eventual periodicity."""
    vals = sorted(set(values))
    if len(vals) <= 1:
        return {"looks_semilinear": True, "explanation": "Empty or singleton sample."}
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    if len(set(diffs)) == 1:
        return {
            "looks_semilinear": True,
            "explanation": f"Observed arithmetic progression with step {diffs[0]}.",
        }
    if len(diffs) >= 4 and len(set(diffs[len(diffs) // 2:])) == 1:
        return {
            "looks_semilinear": True,
            "explanation": "Observed differences stabilise within the sample.",
        }
    if len(diffs) >= 3:
        second_diffs = [b - a for a, b in zip(diffs, diffs[1:])]
        if len(set(second_diffs)) == 1 and second_diffs[0] > 0:
            return {
                "looks_semilinear": False,
                "explanation": "Observed quadratic growth is a heuristic warning only.",
            }
    for modulus in range(1, min(len(vals), 8)):
        groups: dict[int, list[int]] = {}
        for value in vals:
            groups.setdefault(value % modulus, []).append(value)
        if all(len({b - a for a, b in zip(g, g[1:])}) <= 1 for g in groups.values()):
            return {
                "looks_semilinear": True,
                "explanation": f"Sample fits arithmetic progressions modulo {modulus}.",
            }
    return {"looks_semilinear": None, "explanation": "No simple observed gap pattern."}


def check_semilinearity(
    vectors: set[tuple[int, ...]], alphabet_size: int
) -> dict:
    """Report a finite sample separately from its unknown full Parikh image.

    Every finite subset of N^d is semilinear: ``linear_sets`` is its exact
    union of singletons, with no inferred infinite periods. The unknown
    underlying image always has ``is_semilinear=None``. ``looks_semilinear``
    records gap/projection diagnostics only and cannot justify a CFL verdict.
    """
    if not isinstance(alphabet_size, int) or alphabet_size < 0:
        raise ValueError("alphabet_size must be a non-negative integer")
    if any(
        len(v) != alphabet_size
        or any(not isinstance(x, int) or x < 0 for x in v)
        for v in vectors
    ):
        raise ValueError("vectors must lie in N^alphabet_size")

    linear_sets = [{"base": v, "periods": []} for v in sorted(vectors)]
    if not vectors or alphabet_size == 0 or len(vectors) == 1:
        hint = {"looks_semilinear": True, "explanation": "Empty or singleton sample."}
    elif alphabet_size == 1:
        hint = _analyze_1d([v[0] for v in vectors])
    else:
        projections = [_analyze_1d([v[i] for v in vectors]) for i in range(alphabet_size)]
        warning_axis = next(
            (i for i, p in enumerate(projections) if p["looks_semilinear"] is False),
            None,
        )
        if warning_axis is not None:
            hint = {
                "looks_semilinear": False,
                "explanation": f"Projection {warning_axis}: {projections[warning_axis]['explanation']}",
            }
        elif all(p["looks_semilinear"] is True for p in projections) and len(vectors) <= 30:
            hint = {
                "looks_semilinear": True,
                "explanation": "Small sample has simple individual projection patterns.",
            }
        else:
            hint = {
                "looks_semilinear": None,
                "explanation": "No simple joint pattern was established from the sample.",
            }
    return {
        "is_semilinear": None,
        "sample_is_semilinear": True,
        "looks_semilinear": hint["looks_semilinear"],
        "linear_sets": linear_sets,
        "evidence_scope": "finite_sample",
        "explanation": (
            f"{hint['explanation']} The finite sample is an exact union of "
            "singleton linear sets. It cannot determine semilinearity of "
            "the underlying full image."
        ),
    }


# ---------------------------------------------------------------------------
# High-level analysis
# ---------------------------------------------------------------------------

def _sample_words_repeated_subword(ir_spec: dict, max_total: int = 15) -> set[str]:
    """Generate sample words from a repeated_subword language_spec.

    Enumerate candidate assignments and concatenate. Only a few constraint
    shapes are applied below; returned words may violate other constraints.
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
        w = "".join(
            assignment[p[4:-1]][::-1] if p.startswith("rev(") and p.endswith(")")
            else assignment[p]
            for p in concat_pattern
        )
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

    Certifies the full image only for a valid, unfiltered explicit CFG,
    by Parikh's theorem. All finite-pattern diagnostics remain heuristic.
    Filtered/repeated-subword samplers return candidate vectors from a
    superset because they apply only some predicates; provenance exposes
    this limitation. No negative CFL conclusion is inferred from a sample.
    """
    spec = ir.get("language_spec", {})
    kind = spec.get("kind")
    if kind in ("grammar", "grammar_filter"):
        grammar = spec if kind == "grammar" else spec.get("grammar", {})
        if not _is_valid_cfg(grammar):
            return {
                "commutative_image": "Invalid or unsupported CFG representation.",
                "is_semilinear": None,
                "looks_semilinear": None,
                "evidence_scope": "not_verified",
                "vectors_sampled": [],
                "explanation": "Cannot apply Parikh's theorem to an invalid CFG.",
                "conclusion": None,
            }
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
            "looks_semilinear": None,
            "evidence_scope": "not_verified",
            "vectors_sampled": [],
            "explanation": f"Cannot compute Parikh image for kind '{kind}'.",
            "conclusion": None,
        }

    semi = check_semilinearity(vectors, len(alphabet))

    theorem_applies = kind == "grammar"
    provenance = {
        "kind": "language_members" if theorem_applies else "candidate_superset",
        "max_length": 15,
        "complete_within_bound": False,
        "membership_verified": theorem_applies,
        "limitations": (
            "BFS sentential-form cap may omit words."
            if theorem_applies else
            "Only selected filter/constraint shapes are applied; candidate "
            "vectors may not belong to the target language image."
        ),
    }

    # Build commutative image description
    if len(vectors) <= 10:
        ci_desc = f"Sampled {'member' if theorem_applies else 'candidate'} vectors: {sorted(vectors)}"
    else:
        sample = sorted(vectors)[:5]
        ci_desc = f"Sampled {len(vectors)} {'member' if theorem_applies else 'candidate'} vectors, first 5: {sample}"

    return {
        "commutative_image": ci_desc,
        "is_semilinear": True if theorem_applies else None,
        "looks_semilinear": semi["looks_semilinear"],
        "sample_is_semilinear": semi["sample_is_semilinear"],
        "linear_sets": semi["linear_sets"],
        "evidence_scope": "grammar_theorem" if theorem_applies else "finite_sample",
        "sample_provenance": provenance,
        "vectors_sampled": sorted(vectors),
        "explanation": (
            "Parikh's theorem certifies semilinearity of the full image of "
            "this valid CFG, independently of the sampled pattern. "
            if theorem_applies else provenance["limitations"] + " "
        ) + semi["explanation"],
        "conclusion": None,
    }
