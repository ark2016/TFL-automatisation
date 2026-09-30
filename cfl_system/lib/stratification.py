"""
Bounded language test and stratification analysis.

A bounded language has the form L ⊆ w₁* · w₂* · … · wₙ*.
For bounded CFLs the Ginsburg–Spanier theorem provides an algorithmic
characterisation via stratified semilinear exponent sets.
"""

from __future__ import annotations

from functools import lru_cache

from cfl_system.lib.parikh import (
    _generate_words_from_grammar,
    check_semilinearity,
    _is_valid_cfg,
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
    parts = [
        p[4:-1] if p.startswith("rev(") and p.endswith(")") else p
        for p in concat_pattern
    ]
    if any(p not in alphabets for p in parts):
        return {
            "is_bounded": None,
            "bounding_words": None,
            "explanation": "Concat pattern has a part without a declared alphabet.",
        }

    single_char_parts = all(len(v) == 1 for v in alphabets.values())
    if single_char_parts:
        bounding = [alphabets[p][0] for p in parts]
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
    """Certify L(G) is inside the sorted terminal-star template exactly.

    For each nonterminal, compute its transition relation in the template
    DFA by a least fixed point. A missing inclusion certificate says nothing
    about existence of some other bounding expression.
    """
    if not _is_valid_cfg(spec):
        return {
            "is_bounded": None,
            "bounding_words": None,
            "explanation": "Invalid or unsupported CFG; boundedness is unknown.",
        }
    terminals = sorted(set(spec["terminals"]))
    # State 0 is initial; states 1..k remember the last terminal rank.
    # Every state except sink accepts, including the initial epsilon state.
    sink = len(terminals) + 1
    states = range(sink + 1)
    identity = {(q, q) for q in states}
    relations: dict[str, set[tuple[int, int]]] = {
        nt: set() for nt in spec["nonterminals"]
    }
    for rank, terminal in enumerate(terminals, 1):
        relations[terminal] = {
            (q, rank if q <= rank else sink) for q in states
        }

    changed = True
    while changed:
        changed = False
        for rule in spec["rules"]:
            composed = identity
            for symbol in rule["rhs"]:
                next_states: dict[int, set[int]] = {}
                for source, dest in relations[symbol]:
                    next_states.setdefault(source, set()).add(dest)
                composed = {
                    (source, dest)
                    for source, middle in composed
                    for dest in next_states.get(middle, ())
                }
                if not composed:
                    break
            relation = relations[rule["lhs"]]
            additions = composed - relation
            if additions:
                relation.update(additions)
                changed = True

    if (0, sink) in relations[spec["start"]]:
        return {
            "is_bounded": None,
            "bounding_words": None,
            "sorted_template_inclusion": False,
            "explanation": (
                "A generated word violates the sorted terminal-star template. "
                "This does not determine whether some other bounding expression exists."
            ),
        }
    return {
        "is_bounded": True,
        "bounding_words": terminals,
        "sorted_template_inclusion": True,
        "evidence_scope": "cfg_dfa_fixed_point",
        "explanation": (
            "Exact CFG transition fixed point proves L is contained in "
            + (" ".join(t + "*" for t in terminals) or "{epsilon}") + "."
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
    """Find one tuple for word = w1^k1 ... wm^km by memoized backtracking.

    Ambiguous bounds can have several valid tuples; this function returns
    one, not the full exponent preimage required by Ginsburg-Spanier.
    Empty bounding words are represented with exponent zero.
    """
    @lru_cache(maxsize=None)
    def solve(slot: int, pos: int) -> tuple[int, ...] | None:
        if slot == len(bounding_words):
            return () if pos == len(word) else None
        bw = bounding_words[slot]
        if not bw:
            tail = solve(slot + 1, pos)
            return (0,) + tail if tail is not None else None
        ends = [pos]
        while word.startswith(bw, ends[-1]):
            ends.append(ends[-1] + len(bw))
        for count in range(len(ends) - 1, -1, -1):
            tail = solve(slot + 1, ends[count])
            if tail is not None:
                return (count,) + tail
        return None

    return solve(0, 0)


# ---------------------------------------------------------------------------
# Stratification check
# ---------------------------------------------------------------------------

def check_stratification(
    grammar: dict,
    bounding_words: list[str],
    max_length: int = 20,
) -> dict:
    """Return finite exponent diagnostics, never a stratification/CFL verdict.

    Each sampled word contributes one valid decomposition. Ambiguous
    bounding words may admit additional tuples, so this is not the complete
    exponent preimage in the Ginsburg-Spanier theorem. Neither an empty
    sample nor its observed growth pattern proves anything about that set.
    """
    result = {
        "is_stratified": None,
        "is_cfl": None,
        "looks_semilinear": None,
        "exponent_vectors": [],
        "evidence_scope": "finite_sample",
        "exponent_scope": "one_decomposition_per_sampled_word",
        "max_length": max_length,
    }
    if not bounding_words:
        return {**result, "explanation": "No bounding words provided."}
    if not _is_valid_cfg(grammar):
        return {**result, "explanation": "Invalid or unsupported CFG; no exponent analysis."}

    words = _generate_words_from_grammar(grammar, max_length)
    if not words:
        return {
            **result,
            "sample_is_semilinear": True,
            "explanation": (
                "No words were sampled within the derivation/length bounds. "
                "This does not establish an empty language or stratification."
            ),
        }

    exponent_vectors: set[tuple[int, ...]] = set()
    decomposition_failures: list[str] = []
    for word in sorted(words):
        exponents = _decompose_word(word, bounding_words)
        if exponents is None:
            decomposition_failures.append(word)
        else:
            exponent_vectors.add(exponents)

    semi = check_semilinearity(exponent_vectors, len(bounding_words))
    result.update({
        "exponent_vectors": sorted(exponent_vectors),
        "looks_semilinear": semi["looks_semilinear"],
        "sample_is_semilinear": semi["sample_is_semilinear"],
    })
    if decomposition_failures:
        return {
            **result,
            "explanation": (
                f"{len(decomposition_failures)} sampled word(s) cannot be "
                f"decomposed into the proposed bounds; examples: {decomposition_failures[:3]}. "
                "The bounding expression therefore does not contain the grammar language. "
                "No CFL or stratification conclusion follows."
            ),
        }
    return {
        **result,
        "explanation": (
            f"Sampled {len(exponent_vectors)} exponent vectors. {semi['explanation']} "
            "Only one decomposition per sampled word is included. "
            "Ginsburg-Spanier requires a stratified semilinear representation "
            "of the full exponent preimage; these diagnostics do not decide it."
        ),
    }
