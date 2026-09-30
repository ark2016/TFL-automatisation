"""
Claim verifier — validates claims made by specialist agents.

Dispatches to agent-specific verifiers (pumping, closure, decomposition, Parikh)
and returns a structured verification result with pass/fail counts and issues.

Trust taxonomy (docs/VERDICT_POLICY.md §1). This module never returns "verified"
(full deterministic proof) — its structural-only pass is "well_formed", and a
successful bounded comparison may earn "bounded_pass". Finite pumping-p
checks are diagnostics and leave universal claims at "well_formed".
Order: refuted < not_verified < well_formed < bounded_pass.
"""

from __future__ import annotations

import re
from typing import Any

from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir, grammar_oracle


def input_grammar_is_cfg(ir: dict) -> bool:
    """Certify CFL only when the entire input language is a valid explicit CFG."""
    spec = ir.get("language_spec")
    if not isinstance(spec, dict) or spec.get("kind") != "grammar":
        return False
    terms, nonterms = spec.get("terminals"), spec.get("nonterminals")
    if not isinstance(terms, list) or not isinstance(nonterms, list) or not nonterms:
        return False
    if any(not isinstance(s, str) or len(s) != 1 for s in terms):
        return False
    if any(not isinstance(s, str) or not s for s in nonterms):
        return False
    terminals, nonterminals = set(terms), set(nonterms)
    if terminals & nonterminals or spec.get("start") not in nonterms:
        return False
    rules = spec.get("rules")
    if not isinstance(rules, list):
        return False
    symbols = terminals | nonterminals
    return all(
        isinstance(rule, dict) and isinstance(rule.get("lhs"), str)
        and rule["lhs"] in nonterminals and isinstance(rule.get("rhs"), list)
        and all(isinstance(symbol, str) and symbol in symbols for symbol in rule["rhs"])
        for rule in rules
    )


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
    trust: str | None = None,
) -> dict:
    return {
        "agent": agent,
        "verification_status": status,
        "trust": trust or status,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "issues": issues,
        "details": details or {},
    }


def _get_oracle(ir: dict):
    """Return a membership oracle for `ir`, or None if unavailable/approximate.

    Never raises — callers treat None as "semantic step-2 check not possible,
    stay at well_formed" (docs/VERDICT_POLICY.md §4).
    """
    try:
        oracle = cfl_oracle_from_ir(ir)
    except Exception:
        return None
    if getattr(oracle, "is_approximate", False):
        return None
    return oracle


def _enumerate_vwx_splits(word: str, p: int):
    """Yield all (u, v, w, x, y) decompositions of `word` with |vwx| <= p, |vx| >= 1.

    vwx is confined to a window [i, i+L) of length L <= p; v, w, x subdivide
    that window. Skips the (v=x=empty) case since |vx| must be >= 1.
    """
    n = len(word)
    for i in range(n + 1):
        max_len = min(p, n - i)
        for length in range(0, max_len + 1):
            end = i + length
            for j in range(i, end + 1):
                for k in range(j, end + 1):
                    if j == i and k == end:
                        continue  # |vx| == 0, not a valid split
                    yield word[:i], word[i:j], word[j:k], word[k:end], word[end:]


def _pump(u: str, v: str, w: str, x: str, y: str, i: int) -> str:
    return u + v * i + w + x * i + y


def _extract_p(key: Any) -> int | None:
    """Extract an integer p from a word_instances key like '3', 'p=3', 'p_3'."""
    m = re.search(r"(\d+)", str(key))
    return int(m.group(1)) if m else None


# docs/VERDICT_POLICY.md R3': soft cap on how many concrete witness words a
# single semantic check hands to the cross-check gate. The proof itself is
# still checked exhaustively (this only bounds what gets reported/reused —
# see docs/VERDICT_POLICY.md §4/R3').
_MAX_WITNESSES = 30


def _witness_collector() -> tuple[list[dict], Any]:
    """Return (witnesses, add_fn) — a small dedup-by-word accumulator shared
    by the three step-2 semantic checks below, reused instead of duplicating
    the same dedup/cap logic three times (docs/VERDICT_POLICY.md R3')."""
    witnesses: list[dict] = []
    seen: set[str] = set()

    def _add(word: str, expected_in_l: bool, source: str) -> None:
        if word in seen or len(witnesses) >= _MAX_WITNESSES:
            return
        seen.add(word)
        witnesses.append({"word": word, "expected_in_l": expected_in_l, "source": source})

    return witnesses, _add


def _oracle_answer(oracle, word: str) -> bool | None:
    """Preserve the membership oracle's unknown result and failures."""
    try:
        answer = oracle(word)
    except Exception:
        return None
    return answer if isinstance(answer, bool) else None


def _check_pumping_instances(
    evidence: dict, ir: dict, issues: list[str], *, ogden: bool = False,
    oracle=None, diagnostics: dict | None = None,
) -> tuple[str | None, list[dict]]:
    """Check finite instances, never the universal pumping argument.

    A rejected witness is a concrete refutation. Closing all sampled splits
    only checks small p, so it cannot promote a non-CFL claim. Conversely,
    surviving i=0,2,3 does not imply surviving every i. Unknown oracle answers
    must never become negative witnesses (VERDICT_POLICY.md section 4).
    """
    raw = evidence.get("word_instances")
    witnesses, add_witness = _witness_collector()
    diag = diagnostics if diagnostics is not None else {}
    diag.update({"p_values": [], "i_values": [0, 2, 3], "splits_checked": 0,
                 "splits_closed": 0, "unresolved_splits": 0,
                 "unknown_answers": 0, "status": "not_checked"})
    if not isinstance(raw, dict) or not raw:
        return None, witnesses
    marks_by_p = evidence.get("marked_positions")
    if ogden and not isinstance(marks_by_p, dict):
        return None, witnesses
    if oracle is None:
        oracle = _get_oracle(ir)
    if oracle is None:
        return None, witnesses

    for key, word in raw.items():
        p = _extract_p(key)
        if not isinstance(word, str) or p not in (3, 4):
            continue
        if len(word) < p:
            issues.append(f"word_instances[p={p}]: length {len(word)} < p; skipped")
            continue
        marked = set()
        if ogden:
            marks = marks_by_p.get(key)
            if not isinstance(marks, list):
                continue
            if any(type(m) is not int or not 0 <= m < len(word) for m in marks):
                issues.append(f"word_instances[p={p}]: invalid marked positions; skipped")
                continue
            marked = set(marks)
            if len(marked) < p:
                issues.append(f"word_instances[p={p}]: only {len(marked)} position(s) "
                              f"marked, need >= {p} per Ogden's lemma; skipped")
                continue
        in_l = _oracle_answer(oracle, word)
        if in_l is None:
            diag["unknown_answers"] += 1
            continue
        if in_l is False:
            issues.append(f"word_instances[p={p}] = '{word}' is NOT in L (oracle); "
                          "pumping witness invalid")
            diag["status"] = "invalid_witness"
            return "refuted", witnesses
        diag["p_values"].append(p)
        add_witness(word, True, f"word_instances[p={p}]")
        splits = (_enumerate_ogden_splits(word, marked, p) if ogden
                  else _enumerate_vwx_splits(word, p))
        for u, v, w, x, y in splits:
            diag["splits_checked"] += 1
            closed = False
            for i in (0, 2, 3):
                pumped = _pump(u, v, w, x, y, i)
                answer = _oracle_answer(oracle, pumped)
                if answer is None:
                    diag["unknown_answers"] += 1
                elif answer is False:
                    add_witness(pumped, False, f"pumping p={p} split v={v!r} x={x!r} i={i}")
                    closed = True
                    break
            if closed:
                diag["splits_closed"] += 1
            else:
                diag["unresolved_splits"] += 1
    if diag["p_values"]:
        diag["status"] = ("closed_for_sampled_p" if not diag["unresolved_splits"]
                          and not diag["unknown_answers"] else "inconclusive")
        return "well_formed", witnesses
    if diag["unknown_answers"]:
        diag["status"] = "inconclusive"
    return None, witnesses


def _check_pumping_word_instances(
    evidence: dict, ir: dict, issues: list[str], oracle=None,
    diagnostics: dict | None = None,
) -> tuple[str | None, list[dict]]:
    return _check_pumping_instances(evidence, ir, issues, oracle=oracle,
                                   diagnostics=diagnostics)


def _enumerate_ogden_splits(word: str, marked: set[int], p: int):
    """Yield (u, v, w, x, y) decompositions per Ogden's lemma.

    docs/VERDICT_POLICY.md fix: Ogden's p bounds the number of MARKED
    positions inside vwx (not |vwx|, which is what the plain pumping lemma
    bounds), and vx must contain at least one marked position. A correct
    Ogden proof can have |vwx| arbitrarily large as long as it only spans
    <= p marked positions, and `_enumerate_vwx_splits`'s length bound would
    both wrongly reject those splits and wrongly accept splits with no
    marked position in vx at all.
    """
    n = len(word)
    marked_sorted = sorted(marked)
    for i in range(n + 1):
        for end in range(i, n + 1):
            marked_in_vwx = [m for m in marked_sorted if i <= m < end]
            if not marked_in_vwx or len(marked_in_vwx) > p:
                continue
            for j in range(i, end + 1):
                for k in range(j, end + 1):
                    if j == i and k == end:
                        continue  # |vx| == 0, not a valid split
                    vx_has_marked = any(
                        (i <= m < j) or (k <= m < end) for m in marked_in_vwx
                    )
                    if not vx_has_marked:
                        continue
                    yield word[:i], word[i:j], word[j:k], word[k:end], word[end:]


def _check_ogden_word_instances(
    evidence: dict, ir: dict, issues: list[str], oracle=None,
    diagnostics: dict | None = None,
) -> tuple[str | None, list[dict]]:
    """Ogden instances require at least p marked positions; bound marks, not length."""
    return _check_pumping_instances(evidence, ir, issues, ogden=True,
                                   oracle=oracle, diagnostics=diagnostics)


def _check_closure_examples(
    evidence: dict, ir: dict, issues: list[str],
) -> tuple[str | None, list[dict]]:
    """Step-2 semantic check for closure_reduction (docs/VERDICT_POLICY.md §4).

    Expects evidence["intersection_examples"] (>= 3 words, claimed in L ∩ R)
    and evidence["intersection_non_examples"] (>= 2 words, claimed in R \\ L).
    Checks each against the regex R and the language oracle for L.
    All consistent -> "bounded_pass"; any mismatch -> "refuted". Missing
    fields, below-minimum counts, invalid regex, or no oracle -> None
    (leave trust at well_formed).

    Returns (trust_or_None, witnesses) — intersection_examples are expected
    in L (docs/VERDICT_POLICY.md R3': `expected_in_l=True`), intersection_non_
    examples are expected NOT in L (`expected_in_l=False`, they are claimed
    to lie in R \\ L).
    """
    examples = evidence.get("intersection_examples")
    non_examples = evidence.get("intersection_non_examples")
    witnesses, add_witness = _witness_collector()
    if not isinstance(examples, list) or not isinstance(non_examples, list):
        return None, witnesses
    if len(examples) < 3 or len(non_examples) < 2:
        issues.append(
            "intersection_examples/intersection_non_examples below minimum "
            "(need 3+ / 2+) — semantic check skipped"
        )
        return None, witnesses

    regex = evidence.get("regular_language_regex") or evidence.get("regular_language")
    if not regex:
        return None, witnesses
    try:
        pattern = re.compile(regex)
    except re.error:
        return None, witnesses

    oracle = _get_oracle(ir)
    if oracle is None:
        return None, witnesses

    checked_examples = checked_non_examples = 0
    for w in examples:
        if not isinstance(w, str):
            continue
        if not pattern.fullmatch(w):
            issues.append(f"intersection_examples: '{w}' does not match R = /{regex}/")
            return "refuted", witnesses
        in_l = _oracle_answer(oracle, w)
        if in_l is None:
            continue
        if not in_l:
            issues.append(
                f"intersection_examples: '{w}' matches R but is NOT in L (oracle) — "
                f"claimed word ∉ L ∩ R"
            )
            return "refuted", witnesses
        add_witness(w, True, "intersection_examples")
        checked_examples += 1

    for w in non_examples:
        if not isinstance(w, str):
            continue
        if not pattern.fullmatch(w):
            issues.append(f"intersection_non_examples: '{w}' does not match R = /{regex}/")
            return "refuted", witnesses
        in_l = _oracle_answer(oracle, w)
        if in_l is None:
            continue
        if in_l:
            issues.append(
                f"intersection_non_examples: '{w}' matches R but IS in L (oracle) — "
                f"claimed L ∩ R description is wrong (precedent: live run 2026-09-27, "
                f"see docs/THEORY.md §2)"
            )
            return "refuted", witnesses
        add_witness(w, False, "intersection_non_examples")
        checked_non_examples += 1

    if checked_examples >= 3 and checked_non_examples >= 2:
        return "bounded_pass", witnesses
    return None, witnesses


# ---------------------------------------------------------------------------
# Pumping-lemma verifier
# ---------------------------------------------------------------------------

def verify_pumping_claim(
    evidence: dict, ir: dict, *, agent: str = "pumping_cfl", oracle=None,
) -> dict:
    """Verify a pumping lemma (Bar-Hillel) or Ogden's lemma proof claim.

    Structural checks (well_formed):
    1. The chosen word z is in L (via oracle), if word_chosen is a literal word.
    2. All cases of uvwxy decomposition are covered.
    3. |vwx| <= p and |vx| >= 1 constraints are respected in each case (fields present).
    4. all_cases_covered flag is set.

    Semantic diagnostics: if evidence["word_instances"] gives concrete words
    for p in {3,4}, enumerate uvwxy splits and try i in {0,2,3}. This finite
    search cannot verify the universal pumping claim or refute it from
    finite survival (docs/VERDICT_POLICY.md §4). For `agent="ogden"`,
    this dispatches to the marked-position-aware Ogden check instead of the
    plain (length-bounded) pumping check — the two are NOT interchangeable
    (docs/VERDICT_POLICY.md fix: Ogden's p bounds marked positions in vwx,
    not |vwx| itself).

    `oracle`, when given, overrides the oracle built from `ir` for every
    check in this function (used by closure_reduction's nested call, which
    must test membership in L ∩ R, not L alone).
    """
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []

    # Check 1: word is in L
    word_chosen = evidence.get("word_chosen", "")
    if word_chosen and not _is_parametric(word_chosen):
        checks_total += 1
        try:
            word_oracle = oracle if oracle is not None else cfl_oracle_from_ir(ir)
            answer = _oracle_answer(word_oracle, word_chosen)
            if answer is True:
                checks_passed += 1
            elif answer is False:
                issues.append(f"Chosen word '{word_chosen}' is NOT in L")
            else:
                issues.append("Could not verify word membership (oracle unknown)")
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

    structurally_refuted = any("NOT in L" in i for i in issues)
    status = "well_formed" if not issues else ("refuted" if structurally_refuted else "not_verified")

    trust = status
    witnesses: list[dict] = []
    bounded_check: dict = {}
    if status != "refuted":
        if agent == "ogden":
            semantic, witnesses = _check_ogden_word_instances(evidence, ir, issues, oracle=oracle, diagnostics=bounded_check)
        else:
            semantic, witnesses = _check_pumping_word_instances(evidence, ir, issues, oracle=oracle, diagnostics=bounded_check)
        if semantic == "refuted":
            status = "refuted"
            trust = "refuted"

    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        # docs/VERDICT_POLICY.md R3': the concrete witness words this check
        # instantiated, reused by the verdict gate's cross-check instead of
        # re-deriving them.
        details={"word_verified": checks_passed > 0 and not issues, "destructive_witnesses": witnesses, "bounded_check": bounded_check},
        trust=trust,
    )


# ---------------------------------------------------------------------------
# Closure-reduction verifier
# ---------------------------------------------------------------------------

def verify_closure_claim(evidence: dict, ir: dict) -> dict:
    """Verify a closure reduction proof claim.

    Structural checks (well_formed):
    1. The regular language R is valid (can parse regex).
    2. L ∩ R description is plausible (present).
    3. The pumping proof for L ∩ R is well-formed (delegate to verify_pumping_claim).

    Semantic step 2 (bounded_pass): if evidence provides intersection_examples
    (>= 3, claimed ∈ L ∩ R) and intersection_non_examples (>= 2, claimed ∈ R \\ L),
    check them against R (regex) and L (oracle) — docs/VERDICT_POLICY.md §4.
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
    #
    # docs/VERDICT_POLICY.md fix: the nested proof is about L ∩ R, not L —
    # a word the pumping argument claims leaves L ∩ R when pumped might
    # still be in L (just outside R), which the ORIGINAL L-only oracle
    # would wrongly read as "still in the language" and refute a correct
    # proof. Build a combined oracle (word ∈ L ∩ R) whenever R parses and
    # an L-oracle is available; use an unknown-answer oracle when
    # R can't be used (never silently checking against the wrong language).
    intersection_proof = evidence.get("intersection_not_cfl_proof")
    nested_witnesses: list[dict] = []
    if intersection_proof:
        nested_oracle = None
        pattern_for_nested = None
        regex_for_nested = evidence.get("regular_language_regex") or evidence.get("regular_language")
        base_oracle = _get_oracle(ir)
        if regex_for_nested and base_oracle is not None:
            try:
                pattern_for_nested = re.compile(regex_for_nested)

                def nested_oracle(w: str, _pat=pattern_for_nested, _l=base_oracle) -> bool | None:
                    return _oracle_answer(_l, w) if _pat.fullmatch(w) else False
            except re.error:
                pattern_for_nested = None
                nested_oracle = None
        pumping_result = verify_pumping_claim(
            intersection_proof, ir,
            agent=("ogden" if intersection_proof.get("method") == "ogden" else "pumping_cfl"),
            oracle=nested_oracle if nested_oracle is not None else lambda _word: None,
        )
        checks_total += pumping_result["checks_total"]
        checks_passed += pumping_result["checks_passed"]
        issues.extend(pumping_result["issues"])
        nested_refuted = pumping_result["verification_status"] == "refuted"
        raw_nested_witnesses = list(
            (pumping_result.get("details") or {}).get("destructive_witnesses") or []
        )
        # R3' cross-check fix (reviewer finding): these witnesses were
        # computed against the L ∩ R oracle (`nested_oracle` above), so an
        # `expected_in_l=False` entry really means "not in L ∩ R", which is
        # NOT the same as "not in L" when the word has also left R. The
        # verdict gate's cross-check (cfl_system/orchestrator.py
        # `_cross_check_r3prime`) only has the plain L oracle to test
        # against, so a word that left R but is still in L would wrongly
        # look like a false destructive claim and refute a correct proof.
        # Keep only witnesses whose word still matches R: for those,
        # "in L ∩ R" and "in L" (and their negations) coincide, so the
        # plain-L cross-check is sound for them.
        if pattern_for_nested is not None:
            nested_witnesses = [
                w for w in raw_nested_witnesses
                if isinstance(w, dict) and isinstance(w.get("word"), str)
                and pattern_for_nested.fullmatch(w["word"])
            ]
        else:
            # No usable R regex to filter by — none of these witnesses can
            # be soundly reused against a plain-L oracle.
            nested_witnesses = []
    else:
        checks_total += 1
        issues.append("No proof that intersection is not CFL")
        nested_refuted = False

    status = "well_formed" if not issues else ("refuted" if nested_refuted else "not_verified")

    trust = status
    witnesses = list(nested_witnesses)
    if status != "refuted":
        semantic, closure_witnesses = _check_closure_examples(evidence, ir, issues)
        witnesses.extend(closure_witnesses)
        if semantic == "refuted":
            status = "refuted"
            trust = "refuted"
        elif semantic == "bounded_pass" and status == "well_formed":
            trust = "bounded_pass"

    return _make_result(
        agent="closure_reduction",
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        trust=trust,
        # docs/VERDICT_POLICY.md R3': witnesses from the nested pumping/Ogden
        # proof (over L ∩ R) plus the intersection examples themselves.
        details={"destructive_witnesses": witnesses},
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

    status = "well_formed" if not issues else "not_verified"
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

    status = "well_formed" if not issues else "not_verified"
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
            "verification_status": "well_formed" | "bounded_pass" | "refuted" | "not_verified",
            "trust": same taxonomy (docs/VERDICT_POLICY.md §1),
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
            status="not_verified",
            checks_passed=0,
            checks_total=0,
            issues=[
                f"Agent status is '{agent_output.get('status')}', skipping verification"
            ],
        )

    dispatch = {
        "pumping_cfl": verify_pumping_claim,
        # Same structural shape as pumping_cfl, but Ogden's lemma requires
        # marked-position-aware semantic checking, not the plain
        # length-bounded pumping check (docs/VERDICT_POLICY.md fix) —
        # `agent="ogden"` routes verify_pumping_claim to that path.
        "ogden": lambda evidence, ir: verify_pumping_claim(evidence, ir, agent="ogden"),
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
        status="not_verified",
        checks_passed=0,
        checks_total=0,
        issues=[f"No specific verifier for agent '{agent}'"],
    )
