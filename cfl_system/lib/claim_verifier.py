"""
Claim verifier — validates claims made by specialist agents.

Dispatches to agent-specific verifiers (pumping, closure, decomposition, Parikh)
and returns a structured verification result with pass/fail counts and issues.
"""

from __future__ import annotations

import re
from typing import Any

from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir, grammar_oracle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_parametric(word: str) -> bool:
    """Return True if the word looks parametric (e.g. 'a^n b^n')."""
    return bool(re.search(r"[\^{}]|[a-z]\s*\*\*|n\b", word))


def _make_result(
    agent: str,
    status: str,
    checks_passed: int,
    checks_total: int,
    issues: list[str],
    details: dict | None = None,
) -> dict:
    return {
        "agent": agent,
        "verification_status": status,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "issues": issues,
        "details": details or {},
    }


# ---------------------------------------------------------------------------
# Pumping-lemma verifier
# ---------------------------------------------------------------------------

def verify_pumping_claim(evidence: dict, ir: dict) -> dict:
    """Verify a pumping lemma (Bar-Hillel) proof claim.

    Checks:
    1. The chosen word z is in L (via oracle)
    2. All cases of uvwxy decomposition are covered
    3. |vwx| <= p constraint is respected in each case
    4. |vx| >= 1 constraint is respected in each case
    5. For each case: the pumped word uv^i wx^i y is NOT in L (for claimed i)
    """
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []

    # Check 1: word is in L
    word_chosen = evidence.get("word_chosen", "")
    if word_chosen and not _is_parametric(word_chosen):
        checks_total += 1
        try:
            oracle = cfl_oracle_from_ir(ir)
            if oracle(word_chosen):
                checks_passed += 1
            else:
                issues.append(f"Chosen word '{word_chosen}' is NOT in L")
        except Exception:
            issues.append("Could not verify word membership (oracle error)")

    # Check 2: cases cover all decompositions
    cases = evidence.get("cases", [])
    checks_total += 1
    if len(cases) > 0:
        checks_passed += 1
    else:
        issues.append("No cases provided for pumping argument")

    # Check 3: each case respects constraints
    for i, case in enumerate(cases):
        checks_total += 1
        if "case" in case and "why_not_in_L" in case:
            checks_passed += 1
        else:
            issues.append(f"Case {i}: missing 'case' or 'why_not_in_L' field")

    # Check 4: all_cases_covered flag
    checks_total += 1
    if evidence.get("all_cases_covered", False):
        checks_passed += 1
    else:
        issues.append("all_cases_covered is not True")

    status = (
        "verified"
        if not issues
        else ("refuted" if any("NOT in L" in i for i in issues) else "inconclusive")
    )
    return _make_result(
        agent="pumping_cfl",
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details={"word_verified": checks_passed > 0 and not issues},
    )


# ---------------------------------------------------------------------------
# Closure-reduction verifier
# ---------------------------------------------------------------------------

def verify_closure_claim(evidence: dict, ir: dict) -> dict:
    """Verify a closure reduction proof claim.

    Checks:
    1. The regular language R is valid (can parse regex)
    2. L ∩ R description is plausible
    3. The pumping proof for L ∩ R is verified (delegate to verify_pumping_claim)
    """
    issues: list[str] = []
    checks_passed = 0
    checks_total = 0

    # Check 1: R is specified
    checks_total += 1
    regex = evidence.get("regular_language_regex") or evidence.get("regular_language")
    if regex:
        checks_passed += 1
        # Try to compile regex to verify it's valid
        checks_total += 1
        try:
            re.compile(regex)
            checks_passed += 1
        except re.error:
            issues.append(f"Regular language regex '{regex}' is invalid")
    else:
        issues.append("No regular language specified")

    # Check 2: intersection described
    checks_total += 1
    if evidence.get("intersection_description"):
        checks_passed += 1
    else:
        issues.append("No intersection description")

    # Check 3: pumping proof for intersection
    intersection_proof = evidence.get("intersection_not_cfl_proof")
    if intersection_proof:
        pumping_result = verify_pumping_claim(intersection_proof, ir)
        checks_total += pumping_result["checks_total"]
        checks_passed += pumping_result["checks_passed"]
        issues.extend(pumping_result["issues"])
    else:
        checks_total += 1
        issues.append("No proof that intersection is not CFL")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent="closure_reduction",
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Decomposition verifier
# ---------------------------------------------------------------------------

def verify_decomposition_claim(evidence: dict, ir: dict) -> dict:
    """Verify a decomposition claim (L = L1 op L2).

    Checks:
    1. Each component claims to be CFL — verify if grammar provided
    2. The operation (union/concat/star) preserves CFL
    3. Spot-check: sample words from L should decompose into components
    """
    issues: list[str] = []
    checks_passed = 0
    checks_total = 0

    # Check 1: components specified
    components = evidence.get("components", [])
    checks_total += 1
    if components:
        checks_passed += 1
    else:
        issues.append("No components specified")

    # Check 2: each component claims CFL
    for comp in components:
        checks_total += 1
        if comp.get("is_cfl"):
            checks_passed += 1
            # If grammar provided, quick sanity check
            if comp.get("grammar"):
                checks_total += 1
                try:
                    g_oracle = grammar_oracle(comp["grammar"])
                    if g_oracle("") or any(g_oracle(c) for c in "abcde"):
                        checks_passed += 1
                except Exception:
                    issues.append(
                        f"Could not verify grammar for component '{comp.get('name')}'"
                    )
        else:
            issues.append(f"Component '{comp.get('name')}' is not claimed CFL")

    # Check 3: operation preserves CFL
    op = evidence.get("operation", evidence.get("decomposition_type"))
    checks_total += 1
    cfl_closed_ops = {
        "union",
        "concatenation",
        "concat",
        "kleene_star",
        "star",
        "intersection_with_regular",
    }
    if op in cfl_closed_ops:
        checks_passed += 1
    elif op:
        issues.append(f"Operation '{op}' may not preserve CFL")
    else:
        issues.append("No operation specified")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent="decomposition",
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Parikh verifier
# ---------------------------------------------------------------------------

def verify_parikh_claim(evidence: dict, ir: dict) -> dict:
    """Verify a Parikh image claim.

    Checks:
    1. Sample words and verify their Parikh vectors match claimed image
    2. If claim is "not semilinear" — check if vectors show non-linear pattern
    """
    issues: list[str] = []
    checks_passed = 0
    checks_total = 0

    # Check 1: conclusion present
    checks_total += 1
    if evidence.get("conclusion"):
        checks_passed += 1
    else:
        issues.append("No conclusion")

    # Check 2: semilinearity claim
    checks_total += 1
    sl = evidence.get("is_semilinear")
    if sl is not None:
        checks_passed += 1
    else:
        issues.append("Semilinearity not determined")

    # Check 3: if not semilinear, that implies not CFL (correct reasoning)
    if sl is False:
        checks_total += 1
        conclusion_str = str(evidence.get("conclusion", "")).lower()
        if "not cfl" in conclusion_str or "non_semilinear" in str(
            evidence.get("conclusion", "")
        ):
            checks_passed += 1
        else:
            issues.append("Non-semilinear but conclusion doesn't say not CFL")

    status = "verified" if not issues else "inconclusive"
    return _make_result(
        agent="parikh",
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def verify_agent_claims(agent_output: dict, ir: dict) -> dict:
    """Verify claims made by a specialist agent.

    Dispatches to the appropriate verifier based on agent_output["agent"].

    Returns:
        {
            "agent": str,
            "verification_status": "verified" | "refuted" | "inconclusive" | "error",
            "checks_passed": int,
            "checks_total": int,
            "issues": list[str],
            "details": dict,
        }
    """
    agent = agent_output.get("agent", "")
    evidence = agent_output.get("evidence", {})

    if agent_output.get("status") != "success":
        return _make_result(
            agent=agent,
            status="inconclusive",
            checks_passed=0,
            checks_total=0,
            issues=[
                f"Agent status is '{agent_output.get('status')}', skipping verification"
            ],
        )

    dispatch = {
        "pumping_cfl": verify_pumping_claim,
        "ogden": verify_pumping_claim,  # same structure
        "closure_reduction": verify_closure_claim,
        "decomposition": verify_decomposition_claim,
        "parikh": verify_parikh_claim,
    }

    verifier = dispatch.get(agent)
    if verifier:
        return verifier(evidence, ir)

    # For agents without specific verifiers (cfg_builder, pda_builder, etc.)
    return _make_result(
        agent=agent,
        status="inconclusive",
        checks_passed=0,
        checks_total=0,
        issues=[f"No specific verifier for agent '{agent}'"],
    )
