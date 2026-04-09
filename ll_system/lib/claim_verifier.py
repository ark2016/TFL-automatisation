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


def verify_substitution_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a substitution method claim (not-LL proof).

    The substitution method shows language is not LL(k) by finding:
    - w1·v·w2 ∈ L and w1·v·w3 ∈ L (same prefix w1 and lookahead v)
    - After substitution: w1·v·w3' ∉ L (or contradiction)

    Checks (structural, not oracle-based in Phase 1):
    1. method == "substitution"
    2. k field is present (integer or "arbitrary")
    3. witness has required fields: k, lookahead (or w1/w2/w3 pattern)
    4. for_all_k field present
    5. why_not_in_L explanation present

    proof_sketch fields expected:
    {
        "method": "substitution",
        "for_all_k": bool,
        "witness": {
            "k": str | int,
            "w1": str,
            "lookahead": str,
            "suffix_1": str,
            "suffix_2": str,
            "substitution_result": str,
            "why_not_in_L": str
        }
    }
    """
    agent = "substitution_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: method field
    checks_total += 1
    if proof_sketch.get("method") == "substitution":
        checks_passed += 1
    else:
        issues.append(
            f"Expected method='substitution', got {proof_sketch.get('method')!r}"
        )

    # Check 2: k field present
    checks_total += 1
    k = proof_sketch.get("k") or proof_sketch.get("witness", {}).get("k")
    if k is not None:
        checks_passed += 1
    else:
        issues.append("No 'k' field in proof_sketch or witness")

    # Check 3: for_all_k must be True (proof must hold for all k, not just fixed k)
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    # Check 4: witness structure
    witness = proof_sketch.get("witness")
    checks_total += 1
    if not witness or not isinstance(witness, dict):
        issues.append("No 'witness' dict in proof_sketch")
    else:
        checks_passed += 1
        details["witness"] = witness

        # Check 5: required witness fields
        # Prompt uses "lookahead_v" and "why_not_ll"; accept both names.
        def _has_field(w: dict, *names: str) -> bool:
            return any(n in w for n in names)

        required_witness_checks = [
            ("w1",),
            ("lookahead", "lookahead_v"),
            ("suffix_1",),
            ("suffix_2",),
            ("why_not_in_L", "why_not_ll"),
        ]
        missing = [
            names[0] for names in required_witness_checks
            if not _has_field(witness, *names)
        ]
        checks_total += 1
        if missing:
            issues.append(f"Witness is missing fields: {missing}")
        else:
            checks_passed += 1

        # Check 6: why_not_in_L (or why_not_ll) explanation non-empty
        checks_total += 1
        why = witness.get("why_not_in_L") or witness.get("why_not_ll", "")
        if why and isinstance(why, str) and len(why.strip()) > 0:
            checks_passed += 1
        else:
            issues.append("'why_not_in_L' / 'why_not_ll' explanation is missing or empty")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
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
    agent = "marker_detector"
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
    grammar = proof_sketch.get("grammar")
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
    """Verify a prefix_classes method claim (not-LL proof).

    Checks (structural):
    1. method == "prefix_classes"
    2. for_all_k field present
    3. prefix_family present with non-empty description/parametrization
    4. distinguishability_argument present with why_distinguishable

    proof_sketch fields expected:
    {
        "method": "prefix_classes",
        "for_all_k": bool,
        "prefix_family": {"parametrization": str, "description": str, ...},
        "distinguishability_argument": {"why_distinguishable": str, ...},
        "conclusion": str
    }
    """
    agent = "prefix_classes_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: method
    checks_total += 1
    if proof_sketch.get("method") == "prefix_classes":
        checks_passed += 1
    else:
        issues.append(f"Expected method='prefix_classes', got {proof_sketch.get('method')!r}")

    # Check 2: for_all_k must be True (proof must hold for all k, not just fixed k)
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    # Check 3: prefix_family with description
    checks_total += 1
    pf = proof_sketch.get("prefix_family")
    if isinstance(pf, dict) and (pf.get("description") or pf.get("parametrization")):
        checks_passed += 1
        details["prefix_family"] = pf
    else:
        issues.append("Missing or empty 'prefix_family' (need 'description' or 'parametrization')")

    # Check 4: distinguishability_argument with why_distinguishable
    checks_total += 1
    da = proof_sketch.get("distinguishability_argument")
    why = (da or {}).get("why_distinguishable", "") if isinstance(da, dict) else ""
    if isinstance(da, dict) and why and len(why.strip()) > 0:
        checks_passed += 1
        details["distinguishability_argument"] = da
    else:
        issues.append(
            "Missing 'distinguishability_argument' or empty 'why_distinguishable'"
        )

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
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

    # dispatch by method
    method = proof_sketch.get("method", "")
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
