"""
Claim verifier — validates claims made by specialist agents.

Dispatches to agent-specific verifiers (pumping, closure, decomposition, Parikh)
and returns a structured verification result with pass/fail counts and issues.

Trust taxonomy (docs/VERDICT_POLICY.md §1). This module never returns "verified"
(full deterministic proof) — its structural-only pass is "well_formed", and a
successful Step-2 semantic check (docs/VERDICT_POLICY.md §4) upgrades it to
"bounded_pass". Order: refuted < not_verified < well_formed < bounded_pass.
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


def _check_pumping_word_instances(
    evidence: dict, ir: dict, issues: list[str], oracle=None,
) -> tuple[str | None, list[dict]]:
    """Step-2 semantic check for the plain (Bar-Hillel) pumping lemma
    (docs/VERDICT_POLICY.md §4). NOT valid for Ogden's lemma — see
    `_check_ogden_word_instances` — because it bounds |vwx| by p directly,
    whereas Ogden's p bounds the number of MARKED positions in vwx.

    Expects evidence["word_instances"] = {"3": "<instantiated word>", "4": "..."}
    (concrete words, p already substituted). For each p in {3, 4} present:
      1. z must be in L (oracle).
      2. Every split z=uvwxy with |vwx|<=p, |vx|>=1 must have some i in
         {0, 2, 3} with the pumped word NOT in L.
    All closed -> "bounded_pass". Any violation -> "refuted". No oracle or no
    usable entries -> None (leave trust at well_formed).

    A split is refuted only if the pumped word is in L for ALL of i=0, i=2
    AND i=3 (reviewer finding, docs/VERDICT_POLICY.md §4): a correct proof is
    free to rely on i=3 rather than i=2 to disqualify a split, so finding
    i=0/i=2 both in L is not by itself proof the split fails — checking only
    those two i values would refute an otherwise-valid proof.

    `oracle`, when given, overrides the oracle built from `ir` (used by
    closure_reduction's nested check, which must test membership in L ∩ R,
    not L alone).

    Returns (trust_or_None, witnesses): `witnesses` are the concrete words
    this check actually instantiated and their expected oracle membership
    (docs/VERDICT_POLICY.md R3' — reused by the verdict gate's cross-check
    instead of re-deriving them from `word_instances`/splits again).
    """
    raw = evidence.get("word_instances")
    witnesses, add_witness = _witness_collector()
    if not isinstance(raw, dict) or not raw:
        return None, witnesses
    if oracle is None:
        oracle = _get_oracle(ir)
    if oracle is None:
        return None, witnesses

    checked_any = False
    for key, word in raw.items():
        if not isinstance(word, str) or not word:
            continue
        p = _extract_p(key)
        if p not in (3, 4):
            continue
        try:
            in_l = bool(oracle(word))
        except Exception:
            continue
        if not in_l:
            issues.append(
                f"word_instances[p={p}] = '{word}' is NOT in L (oracle) — "
                f"pumping witness invalid"
            )
            return "refuted", witnesses
        checked_any = True
        add_witness(word, True, f"word_instances[p={p}]")
        for (u, v, w, x, y) in _enumerate_vwx_splits(word, p):
            try:
                in0 = bool(oracle(_pump(u, v, w, x, y, 0)))
                in2 = bool(oracle(_pump(u, v, w, x, y, 2)))
            except Exception:
                continue
            if in0 and in2:
                try:
                    in3 = bool(oracle(_pump(u, v, w, x, y, 3)))
                except Exception:
                    in3 = False  # can't confirm i=3 stays in L -> don't refute on it
                if in3:
                    issues.append(
                        f"word_instances[p={p}] = '{word}': split v='{v}' x='{x}' is not "
                        f"disqualified by any i in {{0,2,3}} — uv^iwx^iy is in L for "
                        f"i=0,2,3"
                    )
                    return "refuted", witnesses
                add_witness(_pump(u, v, w, x, y, 3), False, f"pumping p={p} split v={v!r} x={x!r} i=3")
                continue
            if not in0:
                add_witness(_pump(u, v, w, x, y, 0), False, f"pumping p={p} split v={v!r} x={x!r} i=0")
            if not in2:
                add_witness(_pump(u, v, w, x, y, 2), False, f"pumping p={p} split v={v!r} x={x!r} i=2")
    return ("bounded_pass" if checked_any else None), witnesses


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
) -> tuple[str | None, list[dict]]:
    """Step-2 semantic check for Ogden's lemma (docs/VERDICT_POLICY.md §4 fix).

    Expects evidence["word_instances"] (as for plain pumping) AND
    evidence["marked_positions"] = {"3": [i0, i1, ...], "4": [...]} — the
    0-indexed positions in the instantiated word that the proof marks. Per
    Ogden's lemma, a decomposition with the required guarantees exists only
    when AT LEAST p symbols are marked; fewer than that and the split search
    below (`_enumerate_ogden_splits`) is too narrow — it can miss the split
    the real proof needs and wrongly certify an incorrect non-CFL proof as
    `bounded_pass` (reviewer finding, docs/VERDICT_POLICY.md §4). So a given
    p is skipped entirely (not checked, not refuted) whenever fewer than p
    positions are marked for it. Without `marked_positions` for a given p,
    that p's semantic check is likewise skipped entirely (never falls back
    to the plain length-bounded enumeration, which is unsound for Ogden's
    lemma) — trust stays at `well_formed` for that instance.

    Returns (trust_or_None, witnesses) — see `_check_pumping_word_instances`
    for the witness format (docs/VERDICT_POLICY.md R3'), including the same
    "refuted only if i=0, i=2 AND i=3 are all in L" rule.
    """
    raw = evidence.get("word_instances")
    witnesses, add_witness = _witness_collector()
    if not isinstance(raw, dict) or not raw:
        return None, witnesses
    marked_raw = evidence.get("marked_positions")
    if not isinstance(marked_raw, dict) or not marked_raw:
        return None, witnesses
    if oracle is None:
        oracle = _get_oracle(ir)
    if oracle is None:
        return None, witnesses

    checked_any = False
    for key, word in raw.items():
        if not isinstance(word, str) or not word:
            continue
        p = _extract_p(key)
        if p not in (3, 4):
            continue
        marks = marked_raw.get(key)
        if not isinstance(marks, list) or not marks:
            continue
        marked_positions = {m for m in marks if isinstance(m, int) and 0 <= m < len(word)}
        if not marked_positions:
            continue
        if len(marked_positions) < p:
            issues.append(
                f"word_instances[p={p}] = '{word}': only {len(marked_positions)} position(s) "
                f"marked, need >= {p} per Ogden's lemma — semantic check skipped for this p"
            )
            continue
        try:
            in_l = bool(oracle(word))
        except Exception:
            continue
        if not in_l:
            issues.append(
                f"word_instances[p={p}] = '{word}' is NOT in L (oracle) — "
                f"Ogden witness invalid"
            )
            return "refuted", witnesses
        checked_any = True
        add_witness(word, True, f"word_instances[p={p}]")
        for (u, v, w, x, y) in _enumerate_ogden_splits(word, marked_positions, p):
            try:
                in0 = bool(oracle(_pump(u, v, w, x, y, 0)))
                in2 = bool(oracle(_pump(u, v, w, x, y, 2)))
            except Exception:
                continue
            if in0 and in2:
                try:
                    in3 = bool(oracle(_pump(u, v, w, x, y, 3)))
                except Exception:
                    in3 = False  # can't confirm i=3 stays in L -> don't refute on it
                if in3:
                    issues.append(
                        f"word_instances[p={p}] = '{word}': marked split v='{v}' x='{x}' is "
                        f"not disqualified by any i in {{0,2,3}} — uv^iwx^iy is in L for "
                        f"i=0,2,3"
                    )
                    return "refuted", witnesses
                add_witness(_pump(u, v, w, x, y, 3), False, f"ogden p={p} split v={v!r} x={x!r} i=3")
                continue
            if not in0:
                add_witness(_pump(u, v, w, x, y, 0), False, f"ogden p={p} split v={v!r} x={x!r} i=0")
            if not in2:
                add_witness(_pump(u, v, w, x, y, 2), False, f"ogden p={p} split v={v!r} x={x!r} i=2")
    return ("bounded_pass" if checked_any else None), witnesses


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

    for w in examples:
        if not isinstance(w, str):
            continue
        if not pattern.fullmatch(w):
            issues.append(f"intersection_examples: '{w}' does not match R = /{regex}/")
            return "refuted", witnesses
        try:
            in_l = bool(oracle(w))
        except Exception:
            continue
        if not in_l:
            issues.append(
                f"intersection_examples: '{w}' matches R but is NOT in L (oracle) — "
                f"claimed word ∉ L ∩ R"
            )
            return "refuted", witnesses
        add_witness(w, True, "intersection_examples")

    for w in non_examples:
        if not isinstance(w, str):
            continue
        if not pattern.fullmatch(w):
            issues.append(f"intersection_non_examples: '{w}' does not match R = /{regex}/")
            return "refuted", witnesses
        try:
            in_l = bool(oracle(w))
        except Exception:
            continue
        if in_l:
            issues.append(
                f"intersection_non_examples: '{w}' matches R but IS in L (oracle) — "
                f"claimed L ∩ R description is wrong (precedent: live run 2026-09-27, "
                f"see docs/THEORY.md §2)"
            )
            return "refuted", witnesses
        add_witness(w, False, "intersection_non_examples")

    return "bounded_pass", witnesses


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

    Semantic step 2 (bounded_pass): if evidence["word_instances"] gives concrete
    words for p in {3,4}, brute-force every uvwxy split and check the pumping
    lemma actually holds (docs/VERDICT_POLICY.md §4). For `agent="ogden"`,
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
            if word_oracle(word_chosen):
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

    structurally_refuted = any("NOT in L" in i for i in issues)
    status = "well_formed" if not issues else ("refuted" if structurally_refuted else "not_verified")

    trust = status
    witnesses: list[dict] = []
    if status != "refuted":
        if agent == "ogden":
            semantic, witnesses = _check_ogden_word_instances(evidence, ir, issues, oracle=oracle)
        else:
            semantic, witnesses = _check_pumping_word_instances(evidence, ir, issues, oracle=oracle)
        if semantic == "refuted":
            status = "refuted"
            trust = "refuted"
        elif semantic == "bounded_pass" and status == "well_formed":
            trust = "bounded_pass"

    return _make_result(
        agent=agent,
        status=status,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        # docs/VERDICT_POLICY.md R3': the concrete witness words this check
        # instantiated, reused by the verdict gate's cross-check instead of
        # re-deriving them.
        details={"word_verified": checks_passed > 0 and not issues, "destructive_witnesses": witnesses},
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
    # an L-oracle is available; fall back to the plain L oracle only when
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

                def nested_oracle(w: str, _pat=pattern_for_nested, _l=base_oracle) -> bool:
                    return bool(_pat.fullmatch(w)) and bool(_l(w))
            except re.error:
                pattern_for_nested = None
                nested_oracle = None
        pumping_result = verify_pumping_claim(
            intersection_proof, ir,
            agent=("ogden" if intersection_proof.get("method") == "ogden" else "pumping_cfl"),
            oracle=nested_oracle,
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
