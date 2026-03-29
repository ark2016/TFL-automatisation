"""
Grammar Preprocessor — pure-fn analysis of grammar-based languages.

Runs BEFORE LLM specialist agents and provides concrete facts:
generated words, intersection patterns, linearity, recursion structure.
Zero token cost, ~1-2 seconds.
"""

from __future__ import annotations

from typing import Any, Callable

from .grammar_utils import (
    is_right_linear,
    is_left_linear,
    has_nested_recursion,
    generate_words,
    cyk_parse,
    grammar_to_dfa,
)
from .dfa_builder import build_dfa_from_regex
from .dfa_runner import run_dfa
from .congruence import estimate_index
from .word_generator import generate_exhaustive


def analyze_grammar(
    spec: dict,
    oracle: Callable[[str], bool] | None = None,
    max_word_len: int = 10,
) -> dict[str, Any]:
    """Analyze a grammar and return concrete facts for LLM agents.

    Args:
        spec: GrammarLanguage IR spec with terminals, nonterminals, start, rules.
        oracle: Optional oracle function (word -> bool). If None, built from spec.
        max_word_len: Max word length for enumeration.

    Returns:
        Dict of grammar facts.
    """
    facts: dict[str, Any] = {}

    # --- Linearity ---
    facts["is_right_linear"] = is_right_linear(spec)
    facts["is_left_linear"] = is_left_linear(spec)
    facts["is_linear"] = facts["is_right_linear"] or facts["is_left_linear"]
    facts["has_nested_recursion"] = has_nested_recursion(spec)

    # --- Generate words ---
    words = generate_words(spec, max_len=max_word_len)
    sorted_words = sorted(words, key=lambda w: (len(w), w))
    facts["generated_words"] = sorted_words[:100]  # cap for prompt size
    facts["total_generated"] = len(words)

    # --- Derivation trees (show HOW grammar produces words) ---
    facts["derivations"] = _generate_derivations(spec, max_word_len)

    # --- Grammar to DFA (if linear) ---
    dfa = grammar_to_dfa(spec)
    if dfa is not None:
        facts["convertible_to_dfa"] = True
        facts["dfa"] = dfa
        facts["verdict_from_structure"] = "regular"
    else:
        facts["convertible_to_dfa"] = False
        facts["dfa"] = None

    # --- Build oracle if not provided ---
    if oracle is None:
        # Use generated words as oracle
        word_set = words

        def oracle(w: str) -> bool:
            if len(w) > max_word_len:
                return False
            return w in word_set

    # --- Intersection analysis with common regular languages ---
    alphabet = spec.get("terminals", ["a", "b"])

    # Only do intersection analysis for binary alphabet
    if set(alphabet) <= {"a", "b"}:
        facts["intersections"] = _analyze_intersections(
            oracle, alphabet, max_word_len
        )

    # --- Summary for agents ---
    facts["summary"] = _build_summary(facts)

    return facts


def _generate_derivations(
    spec: dict,
    max_len: int,
    max_derivations: int = 20,
) -> list[dict]:
    """Generate sample derivation trees showing how the grammar produces words.

    Returns list of derivations, each with the step-by-step sentential forms
    and the final word.
    """
    rules = [(r["lhs"], r["rhs"]) for r in spec["rules"]]
    start = spec["start"]
    terminals = set(spec["terminals"])

    from collections import deque

    # BFS: each entry is (sentential_form_as_tuple, list_of_steps)
    queue: deque[tuple[tuple[str, ...], list[str]]] = deque()
    init = (start,)
    queue.append((init, [start]))

    # Group derivations by word: word -> list of step-sequences
    word_derivations: dict[str, list[list[str]]] = {}
    max_per_word = 3  # keep up to 3 different derivations per word
    total_found = 0
    forms_explored = 0

    while queue and total_found < max_derivations * 2 and forms_explored < 50_000:
        form, steps = queue.popleft()
        forms_explored += 1

        # Check if all terminals
        if all(s in terminals for s in form):
            word = "".join(form) if form else "ε"
            if len(word) <= max_len:
                if word not in word_derivations:
                    word_derivations[word] = []
                if len(word_derivations[word]) < max_per_word:
                    word_derivations[word].append(steps)
                    total_found += 1
            continue

        # Total length guard
        term_count = sum(1 for s in form if s in terminals)
        if term_count > max_len:
            continue
        if len(form) > max_len * 2 + 5:
            continue

        # Expand first nonterminal
        for i, sym in enumerate(form):
            if sym not in terminals:
                for lhs, rhs in rules:
                    if lhs == sym:
                        new_form = form[:i] + tuple(rhs) + form[i + 1:]
                        rhs_str = " ".join(rhs) if rhs else "ε"
                        new_form_str = " ".join(s for s in new_form) if new_form else "ε"
                        step = f"→ {new_form_str}  [{lhs}→{rhs_str}]"
                        new_steps = steps + [step]

                        if len(new_steps) <= max_len + 5:
                            queue.append((new_form, new_steps))
                break  # leftmost derivation only

    # Build results: group by word, show multiple derivation paths
    results: list[dict] = []
    for word in sorted(word_derivations, key=lambda w: (len(w), w)):
        paths = word_derivations[word]
        entry: dict[str, Any] = {
            "word": word,
            "length": len(word) if word != "ε" else 0,
            "num_derivations": len(paths),
            "derivation": " ".join(paths[0]),  # primary path as string
        }
        if len(paths) > 1:
            entry["alternative_derivations"] = [
                " ".join(p) for p in paths[1:]
            ]
        results.append(entry)
        if len(results) >= max_derivations:
            break

    return results


def _analyze_intersections(
    oracle: Callable[[str], bool],
    alphabet: list[str],
    max_len: int,
) -> dict[str, Any]:
    """Analyze L ∩ R for several common regular languages R."""
    results: dict[str, Any] = {}

    patterns = {
        "a*b*": "a*b*",
        "a+b+": "a+b+",
        "(ab)*": "(ab)*",
        "b*a*b*": "b*a*b*",
    }

    for name, regex in patterns.items():
        try:
            r_dfa = build_dfa_from_regex(regex)
        except Exception:
            continue

        def make_inter_oracle(dfa_local):
            def inter(w):
                return oracle(w) and run_dfa(dfa_local, w)
            return inter

        inter_oracle = make_inter_oracle(r_dfa)

        # Collect words in L ∩ R
        inter_words = []
        for w in generate_exhaustive(alphabet, max_len=min(max_len, 8)):
            try:
                if inter_oracle(w):
                    inter_words.append(w)
            except (ValueError, Exception):
                continue

        if not inter_words:
            results[name] = {"words": [], "is_regular": True, "note": "empty intersection"}
            continue

        # Estimate regularity of L ∩ R
        try:
            est = estimate_index(inter_oracle, alphabet, max_depth=6)
            idx = est.get("estimated_index")
            conf = est.get("confidence", 0)
        except Exception:
            idx = "unknown"
            conf = 0

        # Detect pattern for a*b* intersection
        pattern_desc = None
        if name in ("a*b*", "a+b+"):
            pattern_desc = _detect_ab_pattern(inter_words)

        entry: dict[str, Any] = {
            "words": inter_words[:30],
            "total": len(inter_words),
            "estimated_index": idx,
            "confidence": conf,
        }
        if idx == "infinite" or (isinstance(idx, int) and conf < 0.5):
            entry["is_regular"] = False
        elif isinstance(idx, int) and conf >= 0.8:
            entry["is_regular"] = True
        else:
            entry["is_regular"] = None  # inconclusive

        if pattern_desc:
            entry["pattern"] = pattern_desc

        results[name] = entry

    return results


def _detect_ab_pattern(words: list[str]) -> str | None:
    """Try to detect pattern in words of form a^m b^k."""
    pairs: list[tuple[int, int]] = []
    for w in words:
        if not all(c in "ab" for c in w):
            continue
        # Check if word is a^m b^k form
        switched = False
        valid = True
        for i, c in enumerate(w):
            if c == "b":
                if not switched:
                    switched = True
            elif c == "a" and switched:
                valid = False
                break
        if not valid:
            continue
        m = w.count("a")
        k = w.count("b")
        pairs.append((m, k))

    if not pairs:
        return None

    # Check: m == k for all pairs?
    if all(m == k for m, k in pairs):
        return "m == k (i.e. {a^n b^n})"

    # Check: m ≡ k (mod 2)?
    if all(m % 2 == k % 2 for m, k in pairs):
        return "m ≡ k (mod 2)"

    # Check: m ≡ k (mod 3)?
    if all(m % 3 == k % 3 for m, k in pairs):
        return "m ≡ k (mod 3)"

    # Check: all pairs
    if len(pairs) <= 20:
        return f"specific pairs: {pairs}"

    return None


def _build_summary(facts: dict) -> str:
    """Build a human-readable summary of grammar facts."""
    lines = []

    if facts["is_linear"]:
        kind = "right-linear" if facts["is_right_linear"] else "left-linear"
        lines.append(f"Grammar is {kind} → language is REGULAR.")
    elif facts["has_nested_recursion"]:
        lines.append("Grammar has nested recursion (e.g. S→aSb). "
                      "Language MAY be non-regular, but nested recursion "
                      "alone doesn't prove it (other rules may compensate).")

    n = facts["total_generated"]
    lines.append(f"Generated {n} words up to length 10.")

    derivations = facts.get("derivations", [])
    if derivations:
        lines.append(f"\nSample derivations ({len(derivations)} words):")
        for d in derivations[:10]:
            w = d["word"]
            n_paths = d["num_derivations"]
            path = d["derivation"]
            tag = f" [{n_paths} paths]" if n_paths > 1 else ""
            lines.append(f"  {w}{tag}: {path}")

    inters = facts.get("intersections", {})
    for name, data in inters.items():
        if not data.get("words"):
            continue
        pattern = data.get("pattern", "")
        is_reg = data.get("is_regular")
        idx = data.get("estimated_index")
        words_sample = data["words"][:5]

        if is_reg is True:
            lines.append(
                f"L ∩ {name} is REGULAR (index={idx}). "
                f"Pattern: {pattern}. "
                f"Sample words: {words_sample}. "
                f"DO NOT use L ∩ {name} for closure proof — it won't work!"
            )
        elif is_reg is False:
            lines.append(
                f"L ∩ {name} appears NON-REGULAR (index={idx}). "
                f"This CAN be used for closure proof. "
                f"Sample words: {words_sample}."
            )
        else:
            lines.append(
                f"L ∩ {name}: regularity unclear (index={idx}). "
                f"Sample: {words_sample}."
            )

    return "\n".join(lines)
