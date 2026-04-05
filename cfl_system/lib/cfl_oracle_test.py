"""CFL oracle test module — compare proposed grammars/PDAs against the language oracle.

Tests whether L(grammar) = L(predicate) by generating positive, negative,
and boundary words and checking membership in both directions.

Public API:
    oracle_test_grammar(grammar, ir, ...) -> dict
    oracle_test_pda(pda, ir, ...) -> dict
    oracle_test(evidence, ir, ...) -> dict
"""

from __future__ import annotations

from cfl_system.lib.cfl_oracle import (
    cfl_oracle_from_ir,
    grammar_oracle,
    pda_oracle,
    UnsupportedOracleKindError,
)
from cfl_system.lib.cfl_word_generator import generate_test_words


# Minimum number of words required for a "pass" verdict. Below this,
# the test is reported as not_applicable (coverage too low to conclude).
_MIN_COVERAGE = 3


def _safe_call(oracle, word: str) -> bool | None:
    """Call oracle(word) catching exceptions; returns None on failure."""
    try:
        return bool(oracle(word))
    except Exception:
        return None


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

    All oracle calls are exception-safe: if either oracle raises, the
    word is skipped (not counted as pass or fail). Words where lang_oracle
    itself raises cannot contribute to the test.

    Below _MIN_COVERAGE effectively-checked words, the status is
    "not_applicable" (insufficient coverage to claim a pass).
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
    skipped = 0

    # Positive test: words in L should be in L(candidate)
    for word in test_data["positive"]:
        cand = _safe_call(candidate_oracle, word)
        if cand is None:
            skipped += 1
            continue
        pos_checked += 1
        if cand:
            pos_passed += 1
        else:
            counterexamples.append({
                "word": word,
                "type": "false_negative",
                "description": f"word '{word}' is in L but not in L({label})",
            })

    # Negative test: words NOT in L should NOT be in L(candidate)
    for word in test_data["negative"]:
        cand = _safe_call(candidate_oracle, word)
        if cand is None:
            skipped += 1
            continue
        neg_checked += 1
        if not cand:
            neg_passed += 1
        else:
            counterexamples.append({
                "word": word,
                "type": "false_positive",
                "description": f"word '{word}' is not in L but is in L({label})",
            })

    # Boundary test: edge cases checked against the reference oracle
    for word in test_data["boundary"]:
        expected = _safe_call(lang_oracle, word)
        actual = _safe_call(candidate_oracle, word)
        if expected is None or actual is None:
            skipped += 1
            continue
        bnd_checked += 1
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

    if counterexamples:
        status = "grammar_incorrect"
    elif total < _MIN_COVERAGE:
        # Not enough test words to conclude — avoid ложный pass.
        status = "not_applicable"
    else:
        status = "pass"

    return {
        "status": status,
        "positive_checked": pos_checked,
        "positive_passed": pos_passed,
        "negative_checked": neg_checked,
        "negative_passed": neg_passed,
        "boundary_checked": bnd_checked,
        "boundary_passed": bnd_passed,
        "counterexamples": counterexamples,
        "details": (
            f"Checked {total} words total"
            + (f" ({skipped} skipped due to oracle errors)" if skipped else "")
            + (f"; below min coverage ({_MIN_COVERAGE})" if total < _MIN_COVERAGE else "")
        ),
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
    except UnsupportedOracleKindError as e:
        result = _empty_result()
        result["status"] = "not_applicable"
        result["details"] = f"no automated oracle: {e}"
        return result
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = str(e)
        return result

    try:
        gram_oracle = grammar_oracle(grammar)
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = f"grammar construction failed: {e}"
        return result

    # If the language oracle is approximate (e.g. natural_language_filter),
    # running membership comparisons would produce misleading passes.
    if getattr(lang_oracle, "is_approximate", False):
        reason = getattr(lang_oracle, "approximation_reason", "approximate oracle")
        result = _empty_result()
        result["status"] = "not_applicable"
        result["details"] = f"language oracle is approximate: {reason}"
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
    except UnsupportedOracleKindError as e:
        result = _empty_result()
        result["status"] = "not_applicable"
        result["details"] = f"no automated oracle: {e}"
        return result
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = str(e)
        return result

    # Validate PDA structure upfront (pda_oracle is lazy).
    try:
        from cfl_system.lib.pda_simulator import validate_pda
        pda_errors = validate_pda(pda)
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = f"PDA validation failed: {e}"
        return result
    if pda_errors:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = f"PDA invalid: {'; '.join(pda_errors)}"
        return result

    try:
        p_oracle = pda_oracle(pda)
    except Exception as e:
        result = _empty_result()
        result["status"] = "error"
        result["details"] = f"PDA construction failed: {e}"
        return result

    if getattr(lang_oracle, "is_approximate", False):
        reason = getattr(lang_oracle, "approximation_reason", "approximate oracle")
        result = _empty_result()
        result["status"] = "not_applicable"
        result["details"] = f"language oracle is approximate: {reason}"
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

    # Status resolution (priority order):
    #   1. error → error
    #   2. any counterexamples → grammar_incorrect
    #   3. both not_applicable → not_applicable
    #   4. at least one pass, none failed → pass
    g_status = g_result["status"]
    p_status = p_result["status"]

    if g_status == "error" or p_status == "error":
        merged["status"] = "error"
    elif all_counterexamples:
        merged["status"] = "grammar_incorrect"
    elif g_status == "not_applicable" and p_status == "not_applicable":
        merged["status"] = "not_applicable"
    elif "pass" in (g_status, p_status):
        # At least one real test ran and passed; the other may be not_applicable.
        merged["status"] = "pass"
    else:
        merged["status"] = "not_applicable"

    merged["details"] = (
        f"Grammar: {g_result['details']}; PDA: {p_result['details']}"
    )
    return merged
