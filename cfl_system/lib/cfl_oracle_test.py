"""CFL oracle test module — compare proposed grammars/PDAs against the language oracle.

Tests whether L(grammar) = L(predicate) by generating positive, negative,
and boundary words and checking membership in both directions.

Public API:
    oracle_test_grammar(grammar, ir, ...) -> dict
    oracle_test_pda(pda, ir, ...) -> dict
    oracle_test(evidence, ir, ...) -> dict
"""

from __future__ import annotations

from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir, grammar_oracle, pda_oracle
from cfl_system.lib.cfl_word_generator import generate_test_words


def _empty_result() -> dict:
    """Return a result dict with all counters zeroed."""
    return {
        "status": "pass",
        "positive_checked": 0,
        "positive_passed": 0,
        "negative_checked": 0,
        "negative_passed": 0,
        "boundary_checked": 0,
        "boundary_passed": 0,
        "counterexamples": [],
        "details": "",
    }


def _run_test(
    candidate_oracle,
    lang_oracle,
    ir: dict,
    max_words: int,
    max_length: int,
    label: str,
) -> dict:
    """Core testing logic shared by grammar and PDA tests.

    Args:
        candidate_oracle: callable(str) -> bool for the proposed grammar/PDA.
        lang_oracle: callable(str) -> bool for the reference language.
        ir: intermediate representation with language_spec.
        max_words: maximum words to generate per category.
        max_length: maximum word length.
        label: "G" or "PDA" for human-readable messages.

    Returns:
        Result dict with status, counters, counterexamples, and details.
    """
    test_data = generate_test_words(
        ir,
        max_positive=max_words // 3,
        max_negative=max_words // 3,
        max_length=max_length,
    )

    counterexamples: list[dict] = []
    pos_checked = pos_passed = 0
    neg_checked = neg_passed = 0
    bnd_checked = bnd_passed = 0

    # Positive test: words in L should be in L(candidate)
    for word in test_data["positive"]:
        pos_checked += 1
        if candidate_oracle(word):
            pos_passed += 1
        else:
            counterexamples.append({
                "word": word,
                "type": "false_negative",
                "description": f"word '{word}' is in L but not in L({label})",
            })

    # Negative test: words NOT in L should NOT be in L(candidate)
    for word in test_data["negative"]:
        neg_checked += 1
        if not candidate_oracle(word):
            neg_passed += 1
        else:
            counterexamples.append({
                "word": word,
                "type": "false_positive",
                "description": f"word '{word}' is not in L but is in L({label})",
            })

    # Boundary test: edge cases checked against the reference oracle
    for word in test_data["boundary"]:
        bnd_checked += 1
        expected = lang_oracle(word)
        actual = candidate_oracle(word)
        if expected == actual:
            bnd_passed += 1
        else:
            ce_type = "false_negative" if expected else "false_positive"
            counterexamples.append({
                "word": word,
                "type": ce_type,
                "description": (
                    f"boundary word '{word}': expected {expected}, got {actual}"
                ),
            })

    total = pos_checked + neg_checked + bnd_checked
    status = "pass" if not counterexamples else "grammar_incorrect"

    return {
        "status": status,
        "positive_checked": pos_checked,
        "positive_passed": pos_passed,
        "negative_checked": neg_checked,
        "negative_passed": neg_passed,
        "boundary_checked": bnd_checked,
        "boundary_passed": bnd_passed,
        "counterexamples": counterexamples,
        "details": f"Checked {total} words total",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def oracle_test_grammar(
    grammar: dict,
    ir: dict,
    max_words: int = 100,
    max_length: int = 30,
) -> dict:
    """Test a proposed grammar against the language defined by IR.

    Generates words and checks:
    1. Positive: words in L(IR) should be in L(grammar)
    2. Negative: words NOT in L(IR) should NOT be in L(grammar)
    3. Boundary: edge cases

    Returns:
        {
            "status": "pass" | "grammar_incorrect" | "error",
            "positive_checked": int,
            "positive_passed": int,
            "negative_checked": int,
            "negative_passed": int,
            "boundary_checked": int,
            "boundary_passed": int,
            "counterexamples": [
                {
                    "word": str,
                    "type": "false_negative" | "false_positive",
                    "description": str
                }
            ],
            "details": str
        }
    """
    try:
        lang_oracle = cfl_oracle_from_ir(ir)
        gram_oracle = grammar_oracle(grammar)
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = str(e)
        return result

    return _run_test(gram_oracle, lang_oracle, ir, max_words, max_length, "G")


def oracle_test_pda(
    pda: dict,
    ir: dict,
    max_words: int = 100,
    max_length: int = 30,
) -> dict:
    """Test a proposed PDA against the language defined by IR.

    Same return format as oracle_test_grammar.
    """
    try:
        lang_oracle = cfl_oracle_from_ir(ir)
        p_oracle = pda_oracle(pda)
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = str(e)
        return result

    return _run_test(p_oracle, lang_oracle, ir, max_words, max_length, "PDA")


def oracle_test(
    evidence: dict,
    ir: dict,
    max_words: int = 100,
    max_length: int = 30,
) -> dict:
    """Dispatch to grammar or PDA test based on evidence content.

    evidence may contain:
    - "grammar": {...} -> test grammar
    - "pda": {...} -> test PDA
    - both -> test both, merge results

    Returns combined result.
    """
    has_grammar = "grammar" in evidence
    has_pda = "pda" in evidence

    if not has_grammar and not has_pda:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = "evidence contains neither 'grammar' nor 'pda'"
        return result

    # Single evidence type
    if has_grammar and not has_pda:
        return oracle_test_grammar(evidence["grammar"], ir, max_words, max_length)

    if has_pda and not has_grammar:
        return oracle_test_pda(evidence["pda"], ir, max_words, max_length)

    # Both present: test both and merge
    g_result = oracle_test_grammar(evidence["grammar"], ir, max_words, max_length)
    p_result = oracle_test_pda(evidence["pda"], ir, max_words, max_length)

    # Merge: combine counters and counterexamples
    merged = _empty_result()
    merged["positive_checked"] = g_result["positive_checked"] + p_result["positive_checked"]
    merged["positive_passed"] = g_result["positive_passed"] + p_result["positive_passed"]
    merged["negative_checked"] = g_result["negative_checked"] + p_result["negative_checked"]
    merged["negative_passed"] = g_result["negative_passed"] + p_result["negative_passed"]
    merged["boundary_checked"] = g_result["boundary_checked"] + p_result["boundary_checked"]
    merged["boundary_passed"] = g_result["boundary_passed"] + p_result["boundary_passed"]

    all_counterexamples = []
    for ce in g_result["counterexamples"]:
        tagged = {**ce, "source": "grammar"}
        all_counterexamples.append(tagged)
    for ce in p_result["counterexamples"]:
        tagged = {**ce, "source": "pda"}
        all_counterexamples.append(tagged)
    merged["counterexamples"] = all_counterexamples

    # Status: error if either errored, grammar_incorrect if any counterexamples
    if g_result["status"] == "error" or p_result["status"] == "error":
        merged["status"] = "error"
    elif all_counterexamples:
        merged["status"] = "grammar_incorrect"
    else:
        merged["status"] = "pass"

    merged["details"] = (
        f"Grammar: {g_result['details']}; PDA: {p_result['details']}"
    )
    return merged
