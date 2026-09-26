"""Claim verifier for ll_system specialist agents.

Dispatches to method-specific verifiers and returns structured results.
"""
from __future__ import annotations

from typing import Any

# Defensive import — may not be available in test environment
try:
    from ll_system.lib.ll_table_builder import check_ll_k
    _HAS_TABLE_BUILDER = True
except ImportError:
    _HAS_TABLE_BUILDER = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    agent: str,
    status: str,
    checks_passed: int,
    checks_total: int,
    issues: list[str],
    details: dict | None = None,
) -> dict:
    """Create standard verification result dict."""
    return {
        "agent": agent,
        "verification_status": status,  # "verified" | "refuted" | "inconclusive" | "error"
        "status": status,               # alias used by reasoning/formalizer prompts
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "issues": issues,
        "details": details or {},
    }


def _validate_grammar_structure(grammar: Any) -> list[str]:
    """Return list of structural issues with the grammar dict (empty = OK)."""
    issues: list[str] = []
    if not isinstance(grammar, dict):
        issues.append("grammar must be a dict")
        return issues
    for field in ("nonterminals", "terminals", "start", "rules"):
        if field not in grammar:
            issues.append(f"grammar missing required field '{field}'")
    if "rules" in grammar and not isinstance(grammar["rules"], list):
        issues.append("grammar['rules'] must be a list")
    return issues


# ---------------------------------------------------------------------------
# Method-specific verifiers
# ---------------------------------------------------------------------------

def verify_ll_grammar_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify an ll_grammar_construction claim.

    Checks:
    1. Grammar is provided in proof_sketch
    2. Grammar has required fields (nonterminals, terminals, start, rules)
    3. Proposed k is a positive integer
    4. If ll_table_builder available: run check_ll_k(grammar, k) and verify is_ll_k=True
    5. If check_ll_k says not LL(k): status = "refuted"

    proof_sketch fields expected:
    {
        "method": "ll_grammar_construction",
        "k": int,
        "grammar": dict,
        "explanation": str (optional)
    }
    """
    agent = "ll_grammar_builder"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: grammar provided
    # Prompt uses "ll_grammar"; accept both field names.
    checks_total += 1
    grammar = proof_sketch.get("grammar") or proof_sketch.get("ll_grammar")
    if grammar is None:
        issues.append("No grammar provided in proof_sketch")
    else:
        checks_passed += 1

        # Check 2: grammar structure
        struct_issues = _validate_grammar_structure(grammar)
        checks_total += 1
        if struct_issues:
            issues.extend(struct_issues)
        else:
            checks_passed += 1

    # Check 3: k is a positive integer
    checks_total += 1
    k = proof_sketch.get("k")
    if not isinstance(k, int) or k < 1:
        issues.append(f"k must be a positive integer, got: {k!r}")
    else:
        checks_passed += 1

    # Check 4: run check_ll_k if available
    if _HAS_TABLE_BUILDER and grammar and not _validate_grammar_structure(grammar) and isinstance(k, int) and k >= 1:
        checks_total += 1
        try:
            result = check_ll_k(grammar, k)
            details["check_ll_k_result"] = result
            if result.get("is_ll_k"):
                checks_passed += 1
            else:
                issues.append(
                    f"check_ll_k reports grammar is NOT LL({k}): "
                    f"{len(result.get('conflicts', []))} conflict(s)"
                )
                details["conflicts"] = result.get("conflicts", [])
        except Exception as exc:
            issues.append(f"check_ll_k raised an error: {exc}")

    if not issues:
        status = "verified"
    elif any("NOT LL" in i for i in issues):
        status = "refuted"
    else:
        status = "inconclusive"

    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


def _try_word_oracle(ir: dict):
    """Best-effort membership oracle for a set_builder language in `ir`.

    Returns a Callable[[str], bool] or None if no oracle can be built (e.g. the
    IR is not in the cfl_system.lib.cfl_oracle language_spec shape, or the
    words involved are parametrized templates rather than concrete strings).
    This is intentionally best-effort: structural checks below never depend on
    it succeeding.
    """
    try:
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
    except ImportError:
        return None
    try:
        return cfl_oracle_from_ir(ir)
    except Exception:
        return None


def _looks_concrete(word: Any) -> bool:
    """True if `word` is a plain terminal string (no template variables like n, k)."""
    if not isinstance(word, str) or not word:
        return False
    return all(ch.isalpha() for ch in word)


def verify_substitution_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a substitution ("branch-point argument") claim (not-LL proof).

    New contract (docs/THEORY.md §3.3 (C)):
    {
        "method": "substitution",
        "branch_words": {
            "common_prefix": str, "word_1": str, "word_2": str,
            "lookahead_equal_because": str
        },
        "common_form_argument": str,
        "deciding_nonterminal_argument": str,
        "pigeonhole_argument": str,
        "for_all_k": bool,
        "proof_explanation": str
    }

    Checks (structural):
    1. method == "substitution"
    2. for_all_k is True
    3. branch_words is a dict with common_prefix/word_1/word_2/lookahead_equal_because
       all non-empty
    4. common_form_argument non-empty
    5. deciding_nonterminal_argument non-empty
    6. pigeonhole_argument non-empty
    7. proof_explanation non-empty

    Fields from the old (pre-revision) contract — "witness", "w1", "lookahead",
    "suffix_1", "suffix_2", "substitution_result", "why_not_in_L", "why_not_ll" —
    are recognized and reported as obsolete (they no longer count towards
    verification either way).
    """
    agent = "substitution_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    obsolete: list[str] = []
    details: dict = {}

    _OBSOLETE_TOP_LEVEL = ("witness", "k", "substitution_result")
    for name in _OBSOLETE_TOP_LEVEL:
        if name in proof_sketch:
            obsolete.append(
                f"obsolete field '{name}' from the pre-revision substitution contract; ignored"
            )
    old_witness = proof_sketch.get("witness")
    if isinstance(old_witness, dict):
        for name in ("w1", "lookahead", "lookahead_v", "suffix_1", "suffix_2",
                     "why_not_in_L", "why_not_ll"):
            if name in old_witness:
                obsolete.append(
                    f"obsolete field 'witness.{name}' from the pre-revision substitution "
                    f"contract; ignored"
                )

    # Check 1: method field
    checks_total += 1
    if proof_sketch.get("method") == "substitution":
        checks_passed += 1
    else:
        issues.append(
            f"Expected method='substitution', got {proof_sketch.get('method')!r}"
        )

    # Check 2: for_all_k must be True (proof must hold for all k, not just fixed k)
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    # Check 3: branch_words structure
    checks_total += 1
    bw = proof_sketch.get("branch_words")
    required_bw_fields = ("common_prefix", "word_1", "word_2", "lookahead_equal_because")
    if isinstance(bw, dict) and all(
        isinstance(bw.get(f), str) and bw.get(f, "").strip() for f in required_bw_fields
    ):
        checks_passed += 1
        details["branch_words"] = bw
    else:
        missing = [f for f in required_bw_fields if not (isinstance(bw, dict) and bw.get(f))] if isinstance(bw, dict) else list(required_bw_fields)
        issues.append(f"Missing or empty 'branch_words' fields: {missing}")

    # Check 4: common_form_argument
    checks_total += 1
    cfa = proof_sketch.get("common_form_argument", "")
    if isinstance(cfa, str) and cfa.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'common_form_argument'")

    # Check 5: deciding_nonterminal_argument
    checks_total += 1
    dna = proof_sketch.get("deciding_nonterminal_argument", "")
    if isinstance(dna, str) and dna.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'deciding_nonterminal_argument'")

    # Check 6: pigeonhole_argument
    checks_total += 1
    pha = proof_sketch.get("pigeonhole_argument", "")
    if isinstance(pha, str) and pha.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'pigeonhole_argument'")

    # Check 7: proof_explanation
    checks_total += 1
    pe = proof_sketch.get("proof_explanation", "")
    if isinstance(pe, str) and pe.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'proof_explanation'")

    # Optional check 8: if a word-oracle for the IR's set_builder language is
    # available and word_1/word_2 are concrete (non-templated) strings, verify
    # they actually belong to L. Best-effort — never blocks verification when
    # unavailable.
    if isinstance(bw, dict):
        w1, w2 = bw.get("word_1"), bw.get("word_2")
        if _looks_concrete(w1) or _looks_concrete(w2):
            oracle = _try_word_oracle(ir)
            if oracle is not None:
                checks_total += 1
                oracle_ok = True
                for w in (w1, w2):
                    if _looks_concrete(w):
                        try:
                            if not oracle(w):
                                oracle_ok = False
                                issues.append(f"word-oracle: {w!r} is claimed in L but oracle rejects it")
                        except Exception as exc:
                            issues.append(f"word-oracle raised an error on {w!r}: {exc}")
                            oracle_ok = False
                if oracle_ok:
                    checks_passed += 1

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues + obsolete,
        details=details,
    )


def verify_grammar_transformation_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a grammar_transformation claim.

    Checks:
    1. Transformed grammar is provided
    2. Transformation steps listed
    3. If ll_table_builder available: check transformed grammar with check_ll_k

    proof_sketch fields expected:
    {
        "method": "grammar_transformation",
        "k": int,
        "original_grammar": dict,
        "transformed_grammar": dict,
        "transformation_steps": list[str],
        "conflicts_remaining": list (empty = success)
    }
    """
    agent = "grammar_transformer"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: transformed_grammar provided
    checks_total += 1
    transformed_grammar = proof_sketch.get("transformed_grammar")
    if transformed_grammar is None:
        issues.append("No 'transformed_grammar' provided in proof_sketch")
    else:
        checks_passed += 1

        # Check 2: grammar structure
        struct_issues = _validate_grammar_structure(transformed_grammar)
        checks_total += 1
        if struct_issues:
            issues.extend(struct_issues)
        else:
            checks_passed += 1

    # Check 3: transformation_steps listed
    # Prompt uses "transformation_log" (list of step objects); accept both names.
    checks_total += 1
    transformation_steps = (
        proof_sketch.get("transformation_steps")
        or proof_sketch.get("transformation_log")
        or []
    )
    if transformation_steps and isinstance(transformation_steps, list) and len(transformation_steps) > 0:
        checks_passed += 1
        details["transformation_steps"] = transformation_steps
    else:
        issues.append("No 'transformation_steps' / 'transformation_log' listed")

    # Check 4: conflicts_remaining is empty (claim of success)
    # Prompt uses "conflicts"; accept both names.
    checks_total += 1
    conflicts_remaining = (
        proof_sketch.get("conflicts_remaining")
        if "conflicts_remaining" in proof_sketch
        else proof_sketch.get("conflicts", [])
    )
    if isinstance(conflicts_remaining, list) and len(conflicts_remaining) == 0:
        checks_passed += 1
    else:
        issues.append(f"'conflicts_remaining' / 'conflicts' is non-empty: {conflicts_remaining}")

    # Check 5: run check_ll_k on transformed grammar if available
    k = proof_sketch.get("k")
    if (
        _HAS_TABLE_BUILDER
        and transformed_grammar
        and not _validate_grammar_structure(transformed_grammar)
        and isinstance(k, int)
        and k >= 1
    ):
        checks_total += 1
        try:
            result = check_ll_k(transformed_grammar, k)
            details["check_ll_k_result"] = result
            if result.get("is_ll_k"):
                checks_passed += 1
            else:
                issues.append(
                    f"Transformed grammar is NOT LL({k}): "
                    f"{len(result.get('conflicts', []))} conflict(s)"
                )
        except Exception as exc:
            issues.append(f"check_ll_k raised an error: {exc}")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


def verify_marker_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a marker_detection claim.

    Checks:
    1. marker symbol/string is identified
    2. grammar is provided
    3. If ll_table_builder available: check grammar is LL(k)

    proof_sketch fields expected:
    {
        "method": "marker_detection",
        "marker": str,
        "k": int,
        "grammar": dict (optional),
        "explanation": str
    }
    """
    agent = "marker_analyzer"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: marker identified
    # Prompt uses "marker_symbol"; accept both names.
    checks_total += 1
    marker = proof_sketch.get("marker") or proof_sketch.get("marker_symbol")
    if marker and isinstance(marker, str) and len(marker.strip()) > 0:
        checks_passed += 1
        details["marker"] = marker
    else:
        issues.append("No 'marker' / 'marker_symbol' identified in proof_sketch")

    # Check 2: explanation provided
    # Prompt uses "marker_description" and "ll_usage"; accept any of the three.
    checks_total += 1
    explanation = (
        proof_sketch.get("explanation")
        or proof_sketch.get("marker_description")
        or proof_sketch.get("ll_usage")
        or ""
    )
    if explanation and isinstance(explanation, str) and len(explanation.strip()) > 0:
        checks_passed += 1
    else:
        issues.append("No 'explanation' / 'marker_description' / 'll_usage' provided in proof_sketch")

    # Check 3: grammar provided (optional but preferred)
    # Prompt puts grammar in artifacts.ll_grammar; proof_sketch may also carry it.
    grammar = (
        proof_sketch.get("grammar")
        or proof_sketch.get("ll_grammar")
        or proof_sketch.get("_artifacts_ll_grammar")  # injected by verify_ll_claim
    )
    # Prompt uses "suggested_k"; accept both names.
    k = proof_sketch.get("k") or proof_sketch.get("suggested_k")
    if grammar is not None:
        checks_total += 1
        struct_issues = _validate_grammar_structure(grammar)
        if struct_issues:
            issues.extend(struct_issues)
        else:
            checks_passed += 1

        # Check 4: run check_ll_k if available
        if (
            _HAS_TABLE_BUILDER
            and not struct_issues
            and isinstance(k, int)
            and k >= 1
        ):
            checks_total += 1
            try:
                result = check_ll_k(grammar, k)
                details["check_ll_k_result"] = result
                if result.get("is_ll_k"):
                    checks_passed += 1
                else:
                    issues.append(
                        f"Grammar with marker is NOT LL({k}): "
                        f"{len(result.get('conflicts', []))} conflict(s)"
                    )
            except Exception as exc:
                issues.append(f"check_ll_k raised an error: {exc}")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


# ---------------------------------------------------------------------------
# Additional method verifiers
# ---------------------------------------------------------------------------

def verify_prefix_classes_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a prefix_classes method claim (not-LL proof via Theorem 4.7.4 [Sh]).

    New contract (docs/THEORY.md §1.2, §3.3 (A)):
    {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 → not DCFL → not LL",
        "dead_class_finite": str,
        "distinguishing_suffix": str,
        "separation_argument": str,
        "for_all_k": bool,
        "conclusion": str,
        "proof_explanation": str
    }

    Checks (structural):
    1. method == "prefix_classes"
    2. for_all_k is True
    3. theorem non-empty
    4. dead_class_finite non-empty (the mandatory dead-class check)
    5. distinguishing_suffix non-empty
    6. separation_argument non-empty
    7. conclusion non-empty
    8. proof_explanation non-empty

    Fields from the old (pre-revision, "LL Nerode theorem") contract —
    "prefix_family", "distinguishability_argument" — are recognized and
    reported as obsolete (they no longer count towards verification either way).
    """
    agent = "prefix_classes_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    obsolete: list[str] = []
    details: dict = {}

    for name in ("prefix_family", "distinguishability_argument"):
        if name in proof_sketch:
            obsolete.append(
                f"obsolete field '{name}' from the pre-revision prefix_classes contract "
                f"(false 'LL Nerode theorem'); ignored"
            )

    # Check 1: method
    checks_total += 1
    if proof_sketch.get("method") == "prefix_classes":
        checks_passed += 1
    else:
        issues.append(f"Expected method='prefix_classes', got {proof_sketch.get('method')!r}")

    # Check 2: for_all_k must be True
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    def _nonempty_str(field: str) -> bool:
        v = proof_sketch.get(field, "")
        return isinstance(v, str) and bool(v.strip())

    # Check 3: theorem
    checks_total += 1
    if _nonempty_str("theorem"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'theorem'")

    # Check 4: dead_class_finite — the mandatory dead-class argument
    checks_total += 1
    if _nonempty_str("dead_class_finite"):
        checks_passed += 1
        details["dead_class_finite"] = proof_sketch.get("dead_class_finite")
    else:
        issues.append(
            "Missing or empty 'dead_class_finite' — Theorem 4.7.4 is vacuous if the dead "
            "class is infinite, so this argument is mandatory"
        )

    # Check 5: distinguishing_suffix
    checks_total += 1
    if _nonempty_str("distinguishing_suffix"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'distinguishing_suffix'")

    # Check 6: separation_argument
    checks_total += 1
    if _nonempty_str("separation_argument"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'separation_argument'")

    # Check 7: conclusion
    checks_total += 1
    if _nonempty_str("conclusion"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'conclusion'")

    # Check 8: proof_explanation
    checks_total += 1
    if _nonempty_str("proof_explanation"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'proof_explanation'")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues + obsolete,
        details=details,
    )


def verify_essential_ambiguity_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify an essential_ambiguity method claim (not-LL proof).

    Checks (structural):
    1. method == "essential_ambiguity"
    2. essentially_ambiguous == True
    3. witness_word non-empty
    4. two_parse_structures is a list with >= 2 entries
    5. why_every_grammar_ambiguous non-empty

    proof_sketch fields expected:
    {
        "method": "essential_ambiguity",
        "essentially_ambiguous": bool,
        "witness_word": str,
        "two_parse_structures": [{"structure_id":1, ...}, {"structure_id":2, ...}],
        "why_every_grammar_ambiguous": str,
        "proof_explanation": str
    }
    """
    agent = "ambiguity_detector"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: method
    checks_total += 1
    if proof_sketch.get("method") == "essential_ambiguity":
        checks_passed += 1
    else:
        issues.append(f"Expected method='essential_ambiguity', got {proof_sketch.get('method')!r}")

    # Check 2: essentially_ambiguous flag
    checks_total += 1
    if proof_sketch.get("essentially_ambiguous") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'essentially_ambiguous' must be True, got {proof_sketch.get('essentially_ambiguous')!r}"
        )

    # Check 3: witness_word
    checks_total += 1
    witness_word = proof_sketch.get("witness_word", "")
    if witness_word and isinstance(witness_word, str) and len(witness_word.strip()) > 0:
        checks_passed += 1
        details["witness_word"] = witness_word
    else:
        issues.append("'witness_word' is missing or empty")

    # Check 4: two_parse_structures with >= 2 entries
    checks_total += 1
    two_ps = proof_sketch.get("two_parse_structures")
    if isinstance(two_ps, list) and len(two_ps) >= 2:
        checks_passed += 1
        details["two_parse_structures"] = two_ps
    else:
        issues.append(
            f"'two_parse_structures' must have >= 2 entries, got: {two_ps!r}"
        )

    # Check 5: why_every_grammar_ambiguous
    checks_total += 1
    why = proof_sketch.get("why_every_grammar_ambiguous", "")
    if why and isinstance(why, str) and len(why.strip()) > 0:
        checks_passed += 1
    else:
        issues.append("'why_every_grammar_ambiguous' is missing or empty")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def verify_ll_claim(agent_result: dict, ir: dict) -> dict:
    """Verify a claim from any ll_system specialist agent.

    Dispatches based on agent_result["proof_sketch"]["method"]:
    - "ll_grammar_construction" → verify_ll_grammar_claim
    - "substitution" → verify_substitution_claim
    - "grammar_transformation" → verify_grammar_transformation_claim
    - "marker_detection" → verify_marker_claim
    - "prefix_classes" → verify_prefix_classes_claim
    - "essential_ambiguity" → verify_essential_ambiguity_claim
    - anything else → inconclusive

    Returns standard result dict (same structure as _make_result).

    agent_result structure:
    {
        "agent_name": str,
        "verdict": "ll" | "not_ll" | "uncertain",
        "confidence": float,
        "proof_sketch": dict | None,
        "artifacts": dict
    }
    """
    if agent_result.get("verdict") == "uncertain":
        return _make_result(
            agent=agent_result.get("agent_name", "unknown"),
            status="inconclusive",
            checks_passed=0,
            checks_total=0,
            issues=["Agent returned uncertain verdict"],
        )

    proof_sketch = agent_result.get("proof_sketch")
    if proof_sketch is None:
        return _make_result(
            agent=agent_result.get("agent_name", "unknown"),
            status="inconclusive",
            checks_passed=0,
            checks_total=0,
            issues=["No proof_sketch in agent result"],
        )

    # For marker_detection: prompt puts grammar in artifacts.ll_grammar.
    # Inject it into proof_sketch so verify_marker_claim can find it.
    method = proof_sketch.get("method", "")
    if method == "marker_detection":
        artifacts = agent_result.get("artifacts") or {}
        art_grammar = artifacts.get("ll_grammar")
        if art_grammar and not proof_sketch.get("grammar") and not proof_sketch.get("ll_grammar"):
            proof_sketch = {**proof_sketch, "_artifacts_ll_grammar": art_grammar}
    dispatch = {
        "ll_grammar_construction": verify_ll_grammar_claim,
        "substitution": verify_substitution_claim,
        "grammar_transformation": verify_grammar_transformation_claim,
        "marker_detection": verify_marker_claim,
        "prefix_classes": verify_prefix_classes_claim,
        "essential_ambiguity": verify_essential_ambiguity_claim,
    }
    verifier = dispatch.get(method)
    if verifier:
        return verifier(proof_sketch, ir)

    return _make_result(
        agent=agent_result.get("agent_name", "unknown"),
        status="inconclusive",
        checks_passed=0,
        checks_total=0,
        issues=[f"No verifier for method '{method}'"],
    )
