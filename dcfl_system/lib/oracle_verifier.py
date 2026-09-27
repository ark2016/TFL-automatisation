"""
Oracle verifier for DCFL agent system.

Verifies claims made by specialist agents (stack_strategy, closure_reduction,
dcfl_pumping, shallit, inh_ambiguity) using pure-function checks — no LLM calls.

Implements §6.1 of the DCFL spec, and the trust taxonomy / step-2 semantic
checks of docs/VERDICT_POLICY.md §1 and §4:

- structural-only pass (fields present, JSON parses) -> ``well_formed``
  (this used to be reported as ``verified`` — see VERDICT_POLICY.md §1).
- a structural issue with no concrete counterexample -> ``not_verified``
  (this used to be reported as ``issues_found``).
- a deterministic oracle check that actually falsifies the claim (a word the
  proof claims is in L turns out not to be, or vice versa) -> ``refuted``.
- a deterministic oracle check that CONFIRMS the claim on concrete
  instances (but does not exhaustively cover all p / all pairs) ->
  ``bounded_pass``.
- an exhaustive deterministic check -> ``verified`` (not currently produced
  by this module — DCFL has no exhaustive checker yet, see dcfl_system/CLAUDE.md
  "Formal verification NOT implemented").
"""
from __future__ import annotations

import re
from typing import Any, Callable

from dcfl_system.lib.word_sampler import (
    check_constraints,
    sample_words,
    build_set_builder_membership_oracle,
    instantiate_exponent_pattern,
)


# ---------------------------------------------------------------------------
# Trust taxonomy (VERDICT_POLICY.md §1-2)
# ---------------------------------------------------------------------------

# Order: refuted < not_verified < well_formed < bounded_pass < verified
# (refuted is excluded from "strongest evidence" comparisons, not compared).
TRUST_LEVELS: tuple[str, ...] = (
    "refuted", "not_verified", "well_formed", "bounded_pass", "verified",
)
_TRUST_LEVEL_SET = frozenset(TRUST_LEVELS)
_TRUST_RANK: dict[str, int] = {name: i for i, name in enumerate(TRUST_LEVELS)}

# VERDICT_POLICY.md §2 — confidence ceilings by the strongest trust behind a claim.
CONFIDENCE_CAPS: dict[str, float] = {
    "verified": 0.98,
    "bounded_pass": 0.85,
    "well_formed": 0.60,
    "not_verified": 0.40,
    "not_applicable": 0.40,
    "error": 0.40,
}
# Cap that applies whenever R3's contradiction condition holds, regardless of
# which side "wins" the trust comparison.
CONTRADICTION_CONFIDENCE_CAP = 0.50


def trust_rank(trust: str | None) -> int:
    """Rank a trust label for comparison; unknown/None ranks as not_verified."""
    return _TRUST_RANK.get(trust or "", _TRUST_RANK["not_verified"])


def trust_at_least(trust: str | None, threshold: str) -> bool:
    """True if *trust* is >= *threshold* in the taxonomy order, excluding refuted."""
    if trust == "refuted":
        return False
    return trust_rank(trust) >= _TRUST_RANK[threshold]


def confidence_cap_for(trust: str | None) -> float:
    """The confidence ceiling (VERDICT_POLICY.md §2) for a given trust label."""
    return CONFIDENCE_CAPS.get(trust or "", CONFIDENCE_CAPS["not_verified"])


def _trust_for_status(status: str) -> str:
    """Map a verification_status to its trust-taxonomy label."""
    return status if status in _TRUST_LEVEL_SET else "not_verified"


# ---------------------------------------------------------------------------
# Per-agent verification helpers
# ---------------------------------------------------------------------------

def _make_result(
    status: str,
    checks_run: list[str],
    checks_passed: int,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    """Build a single-agent verification result dict."""
    result: dict[str, Any] = {
        "verification_status": status,
        "trust": _trust_for_status(status),
        "checks_run": checks_run,
        "checks_passed": checks_passed,
        "checks_total": len(checks_run),
    }
    if issues:
        result["issues"] = issues
    return result


def _check(
    name: str,
    condition: bool,
    fail_msg: str,
    checks_run: list[str],
    passed: list[bool],
    issues: list[str],
) -> None:
    """Run a single named check, recording the outcome."""
    checks_run.append(name)
    if condition:
        passed.append(True)
    else:
        passed.append(False)
        issues.append(fail_msg)


# ---------------------------------------------------------------------------
# 1. stack_strategy  (§6.1.1)
# ---------------------------------------------------------------------------

def _verify_stack_strategy(proof_sketch: dict, task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    # regex_in_states — if present, every pattern must compile
    regex_in_states = proof_sketch.get("regex_in_states")
    if regex_in_states is not None:
        if isinstance(regex_in_states, list):
            for idx, pat in enumerate(regex_in_states):
                cname = f"regex_compile[{idx}]"
                try:
                    re.compile(pat)
                    _check(cname, True, "", checks_run, passed, issues)
                except re.error as exc:
                    _check(cname, False, f"regex '{pat}' failed to compile: {exc}",
                           checks_run, passed, issues)
        elif isinstance(regex_in_states, dict):
            for key, pat in regex_in_states.items():
                cname = f"regex_compile[{key}]"
                try:
                    re.compile(str(pat))
                    _check(cname, True, "", checks_run, passed, issues)
                except re.error as exc:
                    _check(cname, False, f"regex '{pat}' for state '{key}' failed to compile: {exc}",
                           checks_run, passed, issues)

    # separator — non-empty string
    separator = proof_sketch.get("separator")
    if separator is not None:
        _check(
            "separator_non_empty",
            isinstance(separator, str) and len(separator) > 0,
            "separator must be a non-empty string",
            checks_run, passed, issues,
        )

    # phases — each must have name, action, what, trigger
    phases = proof_sketch.get("phases")
    if phases is not None and isinstance(phases, list):
        required_fields = {"name", "action", "what", "trigger"}
        for idx, phase in enumerate(phases):
            if not isinstance(phase, dict):
                _check(f"phase[{idx}]_is_dict", False,
                       f"phase[{idx}] is not a dict",
                       checks_run, passed, issues)
                continue
            missing = required_fields - set(phase.keys())
            _check(
                f"phase[{idx}]_fields",
                len(missing) == 0,
                f"phase[{idx}] missing required fields: {missing}",
                checks_run, passed, issues,
            )

    # sample words membership check (set_builder only)
    input_format = task_ir.get("input_format", "")
    if input_format == "set_builder":
        spec = task_ir.get("language_spec", {})
        constraints = spec.get("constraints", [])
        if constraints:
            try:
                samples = sample_words(task_ir, count=10, max_len=30)
                positive = [s for s in samples if s.get("in_language") is True]
                if positive:
                    checked = 0
                    ok = 0
                    for sw in positive[:5]:
                        variables = sw.get("variables")
                        if variables is not None:
                            checked += 1
                            if check_constraints(variables, constraints):
                                ok += 1
                    if checked > 0:
                        _check(
                            "sample_words_membership",
                            ok == checked,
                            f"Only {ok}/{checked} sampled positive words satisfied constraints",
                            checks_run, passed, issues,
                        )
            except Exception as exc:
                _check("sample_words_membership", False,
                       f"Error sampling words: {exc}",
                       checks_run, passed, issues)

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"
    return _make_result(status, checks_run, n_passed, issues if issues else None)


# ---------------------------------------------------------------------------
# 2. closure_reduction  (§6.1.2)
# ---------------------------------------------------------------------------

def _verify_closure_reduction(proof_sketch: dict, _task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    operation = proof_sketch.get("operation", "")

    if operation == "complement":
        src = proof_sketch.get("source_language", "")
        _check(
            "complement_source_language",
            isinstance(src, str) and len(src) > 0,
            "complement operation requires non-empty source_language",
            checks_run, passed, issues,
        )
        trans = proof_sketch.get("transformation", "")
        _check(
            "complement_transformation",
            isinstance(trans, str) and len(trans) > 0,
            "complement operation requires non-empty transformation",
            checks_run, passed, issues,
        )

    elif operation == "reg_intersection":
        src = proof_sketch.get("source_language", "")
        _check(
            "reg_intersection_source_language",
            isinstance(src, str) and len(src) > 0,
            "reg_intersection requires non-empty source_language",
            checks_run, passed, issues,
        )
        trans = proof_sketch.get("transformation", "")
        _check(
            "reg_intersection_transformation",
            isinstance(trans, str) and len(trans) > 0,
            "reg_intersection requires non-empty transformation description",
            checks_run, passed, issues,
        )

    # direction check (applies regardless of operation).
    # "both" is included alongside "constructive"/"destructive" to match
    # closure_table.py's proof_direction vocabulary (e.g. "complement" is
    # symmetric — closed under it proves DCFL in either direction); the
    # verdict gate (orchestrator._agent_direction) still only treats an
    # explicit "constructive"/"destructive" as directed evidence, so a
    # "both" proof_sketch is well-formed here but counts toward neither
    # verdict direction there.
    direction = proof_sketch.get("direction", "")
    if direction:
        _check(
            "direction_valid",
            direction in ("constructive", "destructive", "both"),
            f"direction must be one of 'constructive', 'destructive', 'both', got '{direction}'",
            checks_run, passed, issues,
        )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"
    return _make_result(status, checks_run, n_passed, issues if issues else None)


# ---------------------------------------------------------------------------
# 3. dcfl_pumping  (§6.1.3)
# ---------------------------------------------------------------------------

def _verify_dcfl_pumping(proof_sketch: dict, task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    # word_w
    word_w = proof_sketch.get("word_w")
    _check(
        "word_w_specified",
        word_w is not None and isinstance(word_w, str) and len(word_w) > 0,
        "word_w must be a non-empty string",
        checks_run, passed, issues,
    )

    # word_w_prime
    word_w_prime = proof_sketch.get("word_w_prime")
    _check(
        "word_w_prime_specified",
        word_w_prime is not None and isinstance(word_w_prime, str) and len(word_w_prime) > 0,
        "word_w_prime must be a non-empty string",
        checks_run, passed, issues,
    )

    # common_prefix_x — described as having length > p
    common_prefix_x = proof_sketch.get("common_prefix_x")
    _check(
        "common_prefix_x_specified",
        common_prefix_x is not None and (
            (isinstance(common_prefix_x, str) and len(common_prefix_x) > 0)
            or (isinstance(common_prefix_x, dict) and len(common_prefix_x) > 0)
        ),
        "common_prefix_x must be specified and described as having length > p",
        checks_run, passed, issues,
    )

    # suffix_y
    suffix_y = proof_sketch.get("suffix_y")
    _check(
        "suffix_y_specified",
        suffix_y is not None and (
            (isinstance(suffix_y, str) and len(suffix_y) > 0)
            or (isinstance(suffix_y, dict) and len(suffix_y) > 0)
        ),
        "suffix_y must be specified",
        checks_run, passed, issues,
    )

    # suffix_z
    suffix_z = proof_sketch.get("suffix_z")
    _check(
        "suffix_z_specified",
        suffix_z is not None and (
            (isinstance(suffix_z, str) and len(suffix_z) > 0)
            or (isinstance(suffix_z, dict) and len(suffix_z) > 0)
        ),
        "suffix_z must be specified",
        checks_run, passed, issues,
    )

    # first_letters_match reasoning
    first_letters_match = proof_sketch.get("first_letters_match")
    _check(
        "first_letters_match_reasoning",
        first_letters_match is not None and (
            (isinstance(first_letters_match, str) and len(first_letters_match) > 0)
            or (isinstance(first_letters_match, dict) and len(first_letters_match) > 0)
        ),
        "first_letters_match reasoning must be provided",
        checks_run, passed, issues,
    )

    # condition1_argument / condition2_argument — the current contract (THEORY.md §1.1):
    # condition (1) is a PAIR (x2, x4) anywhere in x, condition (2) is a suffix window x2.
    # The obsolete single-factor contract used "no_pumping_argument" instead — flag it
    # explicitly so agents/prompts still emitting it get a clear, actionable issue.
    condition1_argument = proof_sketch.get("condition1_argument")
    condition2_argument = proof_sketch.get("condition2_argument")
    if condition1_argument is None and condition2_argument is None and "no_pumping_argument" in proof_sketch:
        _check(
            "condition1_condition2_arguments_present",
            False,
            "obsolete field 'no_pumping_argument' found — the contract now requires separate "
            "'condition1_argument' (пара (x2, x4) в любом месте x, |x2x3x4| <= p) и "
            "'condition2_argument' (x2 в последних p символах x, синхронно с y2/z2); "
            "см. THEORY.md §1.1 и dcfl_pumping.md",
            checks_run, passed, issues,
        )
    else:
        _check(
            "condition1_argument_non_empty",
            isinstance(condition1_argument, str) and len(condition1_argument) > 0,
            "condition1_argument must be a non-empty string: почему никакая пара (x2, x4) в "
            "любом месте x с |x2x3x4| <= p не накачивается синхронно для обоих слов xy и xz",
            checks_run, passed, issues,
        )
        _check(
            "condition2_argument_non_empty",
            isinstance(condition2_argument, str) and len(condition2_argument) > 0,
            "condition2_argument must be a non-empty string: почему никакое x2 (|x2|>=1) в "
            "последних p символах x не сохраняет оба слова в L при синхронной накачке",
            checks_run, passed, issues,
        )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"

    # Step 2 (VERDICT_POLICY.md §4, dcfl_pumping): if the structure is
    # well-formed, try to instantiate word_w/word_w_prime at concrete n and
    # check membership with a real oracle. Only runs when both an oracle and
    # a clean (pure exponent-notation) instantiation are available; otherwise
    # trust stays at well_formed, per the fallback in VERDICT_POLICY.md §4.
    if status == "well_formed":
        semantic_status, semantic_check, semantic_issue = _semantic_check_dcfl_pumping(
            word_w, word_w_prime, task_ir,
        )
        if semantic_status is not None:
            status = semantic_status
            checks_run.append(semantic_check)
            if semantic_status == "refuted":
                issues.append(semantic_issue)
            else:
                n_passed += 1

    return _make_result(status, checks_run, n_passed, issues if issues else None)


def _yu_condition2_check(
    w: str, w_prime: str, p: int, oracle: Callable[[str], bool | None],
) -> tuple[bool | None, str | None]:
    """Bounded check of THEORY.md §1.1 condition (2) of Yu's DCFL pumping
    lemma for one concrete instantiated pair (w, w') at bound p: some
    non-empty x2 within the last p symbols of the common prefix x, pumped
    synchronously into both words, must break membership of at least one of
    them (docs/VERDICT_POLICY.md §4).

    Returns (ok, counterexample):
      - (None, None)  — the common prefix is too short (<= p) to exercise
        condition (2) at all; this pair provides no evidence either way.
      - (True, None)  — every tested x2 broke at least one word (evidence
        FOR the claim on this pair).
      - (False, msg)  — some x2 kept both w and w' in L (a genuine
        counterexample to condition (2) on this pair).
    """
    common_len = 0
    for a, b in zip(w, w_prime):
        if a != b:
            break
        common_len += 1
    if common_len <= p:
        return None, None

    window_start = max(0, common_len - p)
    window = w[window_start:common_len]
    for i in range(len(window)):
        for j in range(i + 1, len(window) + 1):
            x2 = window[i:j]
            pos = window_start + i
            pumped_w = w[:pos] + x2 + w[pos:]
            pumped_wp = w_prime[:pos] + x2 + w_prime[pos:]
            in_w = oracle(pumped_w)
            in_wp = oracle(pumped_wp)
            if in_w is None or in_wp is None:
                continue
            if in_w and in_wp:
                return False, (
                    f"p={p}: x2={x2!r} at pos {pos} — pumping keeps BOTH words in L "
                    f"(pumped word_w={pumped_w!r}, pumped word_w_prime={pumped_wp!r})"
                )
    return True, None


def _semantic_check_dcfl_pumping(
    word_w: Any, word_w_prime: Any, task_ir: dict,
) -> tuple[str | None, str, str]:
    """Step 2 for dcfl_pumping (VERDICT_POLICY.md §4).

    Instantiate ``word_w``/``word_w_prime`` at n = p + 1 for p in {2, 3},
    check w, w' in L with a real membership oracle, AND exercise condition
    (2) of Yu's lemma (THEORY.md §1.1: some x2 in the last p symbols of the
    common prefix must break the pumping) by brute-force over all such x2 —
    membership alone is necessary but not sufficient evidence for the claim.
    Returns (status_or_None, check_name, issue). ``status`` is None when
    neither the pattern nor a membership oracle could be used, and stays
    None (leaving trust at ``well_formed``) when membership holds but
    condition (2) could not be exercised on either p (common prefix too
    short) — that is membership-only evidence, which is not enough to earn
    ``bounded_pass``.
    """
    check_name = "semantic_word_membership[p=2,3]"
    if not isinstance(word_w, str) or not isinstance(word_w_prime, str):
        return None, check_name, ""

    input_format = task_ir.get("input_format", "")
    if input_format != "set_builder":
        return None, check_name, ""

    spec = task_ir.get("language_spec", {})
    alphabet = task_ir.get("alphabet", [])
    oracle = build_set_builder_membership_oracle(spec, alphabet)
    if oracle is None:
        return None, check_name, ""

    alphabet_set = set(alphabet)
    instances: list[tuple[int, str, str]] = []
    for p in (2, 3):
        n = p + 1
        w = instantiate_exponent_pattern(word_w, n, alphabet_set)
        w_prime = instantiate_exponent_pattern(word_w_prime, n, alphabet_set)
        if w is None or w_prime is None:
            return None, check_name, ""
        instances.append((p, w, w_prime))

    bad: list[str] = []
    for p, w, w_prime in instances:
        in_w = oracle(w)
        in_w_prime = oracle(w_prime)
        if in_w is None or in_w_prime is None:
            return None, check_name, ""
        if not (in_w and in_w_prime):
            bad.append(
                f"p={p}: word_w={w!r} in L={in_w}, word_w_prime={w_prime!r} in L={in_w_prime}"
            )

    if bad:
        return "refuted", check_name, (
            "oracle check failed for instantiated word(s): " + "; ".join(bad)
        )

    # Membership alone confirmed; now exercise condition (2) (THEORY.md
    # §1.1) — the check that actually distinguishes a real pumping argument
    # from two arbitrary words that both happen to be in L.
    check_name = "semantic_word_membership_and_condition2[p=2,3]"
    condition2_evidence = False
    for p, w, w_prime in instances:
        ok, msg = _yu_condition2_check(w, w_prime, p, oracle)
        if ok is False:
            return "refuted", check_name, (
                "condition (2) counterexample: " + msg
            )
        if ok is True:
            condition2_evidence = True

    if not condition2_evidence:
        # Only membership was actually checkable — per VERDICT_POLICY.md §4,
        # that is not enough to earn bounded_pass on its own.
        return None, check_name, ""

    return "bounded_pass", check_name, ""


# ---------------------------------------------------------------------------
# 4. shallit  (§6.1.4)
# ---------------------------------------------------------------------------

def _verify_shallit(proof_sketch: dict, task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    # Obsolete "homogeneous subsets" contract (pre-revision shallit.md): flag it explicitly
    # instead of silently accepting fields the current theory no longer supports.
    obsolete_fields = {"infinite_set_description", "separating_context", "two_elements"}
    present_obsolete = obsolete_fields & set(proof_sketch.keys())
    if present_obsolete:
        _check(
            "no_obsolete_homogeneous_subsets_fields",
            False,
            "obsolete Shallit formulation (homogeneous subsets) — "
            f"found obsolete field(s) {sorted(present_obsolete)}; the contract now requires "
            "'technique' ('nerode_classes' | 'prefix_continuation') with the fields specific to "
            "that technique, see THEORY.md §1.2–1.3 и shallit.md",
            checks_run, passed, issues,
        )
        n_passed = sum(1 for p in passed if p)
        status = "well_formed" if n_passed == len(checks_run) else "not_verified"
        return _make_result(status, checks_run, n_passed, issues if issues else None)

    technique = proof_sketch.get("technique")
    _check(
        "technique_valid",
        technique in ("nerode_classes", "prefix_continuation"),
        "technique must be one of 'nerode_classes', 'prefix_continuation'",
        checks_run, passed, issues,
    )

    def _non_empty_str(value: Any) -> bool:
        return isinstance(value, str) and len(value) > 0

    if technique == "nerode_classes":
        for field, ru_hint in (
            ("dead_class_finite", "почему мёртвый класс D конечен/пуст"),
            ("distinguishing_suffix", "разделяющий суффикс w(u, v) для произвольных u != v"),
            ("separation_argument", "почему uw ∈ L, vw ∉ L (или наоборот)"),
            ("argument", "полное рассуждение"),
        ):
            _check(
                f"{field}_non_empty",
                _non_empty_str(proof_sketch.get(field)),
                f"{field} must be a non-empty string ({ru_hint})",
                checks_run, passed, issues,
            )
    elif technique == "prefix_continuation":
        for field, ru_hint in (
            ("derived_language", "L_$ ∩ R или haspref(L) ∩ R"),
            ("regular_filter", "регулярный язык R"),
            ("non_cfl_argument", "доказательство, что производный язык не КС"),
            ("argument", "полное рассуждение"),
        ):
            _check(
                f"{field}_non_empty",
                _non_empty_str(proof_sketch.get(field)),
                f"{field} must be a non-empty string ({ru_hint})",
                checks_run, passed, issues,
            )
    else:
        _check(
            "argument_non_empty",
            _non_empty_str(proof_sketch.get("argument")),
            "argument must be a non-empty string",
            checks_run, passed, issues,
        )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"

    # Step 2 (VERDICT_POLICY.md §4, shallit/nerode_classes): if distinguishing_suffix
    # is a concrete literal word (not a parametric prose description) and a
    # membership oracle exists for this task, instantiate 2-3 pairs of distinct
    # short words from word_sampler and check uw in L, vw not in L.
    if status == "well_formed" and technique == "nerode_classes":
        semantic_status, semantic_check, semantic_issue = _semantic_check_shallit_nerode(
            proof_sketch, task_ir,
        )
        if semantic_status is not None:
            status = semantic_status
            checks_run.append(semantic_check)
            if semantic_issue:
                issues.append(semantic_issue)
            if semantic_status != "refuted":
                n_passed += 1

    return _make_result(status, checks_run, n_passed, issues if issues else None)


def _continuable(oracle, word: str, alphabet: list[str], max_extra: int = 4) -> bool | None:
    """Whether some extension of `word` (up to `max_extra` more symbols) is in L.

    Breadth-first over extension length so the shortest witness is found
    first. Returns True/False, or None if the oracle never answered
    definitively within the search bound (search inconclusive — the caller
    must not treat that as evidence of an infinite dead class).
    """
    if not alphabet:
        return None
    frontier = [word]
    saw_definite_answer = False
    for _ in range(max_extra + 1):
        next_frontier: list[str] = []
        for w in frontier:
            verdict_w = oracle(w)
            if verdict_w is not None:
                saw_definite_answer = True
                if verdict_w:
                    return True
            for ch in alphabet:
                next_frontier.append(w + ch)
        # Cap the branching to keep this a "bounded, approximate check"
        # (VERDICT_POLICY.md §4), not a real BFS over an exponential tree.
        frontier = next_frontier[:64]
    return False if saw_definite_answer else None


def _check_dead_class_finite(task_ir: dict) -> tuple[bool | None, list[str]]:
    """Step 2, dead-class part (VERDICT_POLICY.md §4): sample short words
    (<=6) and check each is continuable into L within a bounded search.

    This is necessarily approximate (failing to find a continuation in a
    bounded search is not proof that none exists), so a failure here is
    reported as an *issue* but never turns a `well_formed` result into
    `refuted` on its own — only a real, unbounded contradiction (from the
    proof's own instances) may do that.
    """
    input_format = task_ir.get("input_format", "")
    if input_format != "set_builder":
        return None, []
    spec = task_ir.get("language_spec", {})
    alphabet = task_ir.get("alphabet", [])
    oracle = build_set_builder_membership_oracle(spec, alphabet)
    if oracle is None:
        return None, []
    try:
        samples = sample_words(task_ir, count=10, max_len=6)
    except Exception:
        return None, []
    short_words = [
        s.get("word") for s in samples
        if isinstance(s.get("word"), str) and len(s.get("word")) <= 6
    ]
    if not short_words:
        return None, []
    issues: list[str] = []
    checked = 0
    for w in short_words[:6]:
        cont = _continuable(oracle, w, alphabet)
        if cont is None:
            continue
        checked += 1
        if not cont:
            issues.append(
                f"dead_class_finite check: no continuation of {w!r} into L found "
                f"within a bounded search (not conclusive on its own, but worth a retry hint)"
            )
    if checked == 0:
        return None, []
    return (len(issues) == 0), issues


def _semantic_check_shallit_nerode(
    proof_sketch: dict, task_ir: dict,
) -> tuple[str | None, str, str]:
    """Step 2 for shallit/nerode_classes (VERDICT_POLICY.md §4).

    The Shallit/Nerode claim is about REPRESENTATIVES of the (infinitely
    many) Nerode classes named in the proof's own general argument, not
    about arbitrary random strings — so this only refutes the claim against
    concrete instances the proof itself supplies (``representative_pairs``:
    a list of ``{"u": ..., "v": ...}`` literal words over the task alphabet,
    understood as the proof's general argument instantiated at some chosen
    n). Random samples from ``word_sampler`` may only be used to look for a
    supporting separating pair (upgrading to ``bounded_pass``); they can
    never refute the proof, since a suffix failing to separate arbitrary
    unrelated strings says nothing about whether it separates the actual
    class representatives the proof is about.

    Only runs when ``distinguishing_suffix`` is a concrete literal word over
    the task alphabet (no free parameters) and a set_builder membership
    oracle is available; otherwise trust stays at ``well_formed`` per the
    fallback in VERDICT_POLICY.md §4.
    """
    distinguishing_suffix = proof_sketch.get("distinguishing_suffix")
    check_name = "semantic_nerode_separation[pairs]"
    if not isinstance(distinguishing_suffix, str) or not distinguishing_suffix:
        return None, check_name, ""

    input_format = task_ir.get("input_format", "")
    if input_format != "set_builder":
        return None, check_name, ""

    alphabet = task_ir.get("alphabet", [])
    alphabet_set = set(alphabet)
    if any(ch not in alphabet_set for ch in distinguishing_suffix):
        # Contains variables/prose (e.g. "b a^N b u^R") — not a literal we
        # can instantiate mechanically.
        return None, check_name, ""

    spec = task_ir.get("language_spec", {})
    oracle = build_set_builder_membership_oracle(spec, alphabet)
    if oracle is None:
        return None, check_name, ""

    status: str | None = None
    issue = ""

    # Representative pairs supplied by the proof itself (its general
    # argument, instantiated at a concrete n) — the only thing that can
    # refute the claim.
    raw_pairs = proof_sketch.get("representative_pairs")
    checked_pairs: list[tuple[str, str]] = []
    if isinstance(raw_pairs, list):
        for item in raw_pairs[:5]:
            if not isinstance(item, dict):
                continue
            u, v = item.get("u"), item.get("v")
            if not (isinstance(u, str) and isinstance(v, str) and u != v):
                continue
            if any(ch not in alphabet_set for ch in u + v):
                continue
            checked_pairs.append((u, v))

    bad: list[str] = []
    n_definite = 0
    for u, v in checked_pairs:
        in_u = oracle(u + distinguishing_suffix)
        in_v = oracle(v + distinguishing_suffix)
        if in_u is None or in_v is None:
            continue
        n_definite += 1
        if not ((in_u and not in_v) or (in_v and not in_u)):
            bad.append(f"u={u!r}, v={v!r}: uw in L={in_u}, vw in L={in_v} (same class)")

    if bad:
        return "refuted", check_name, (
            "distinguishing_suffix does not separate the proof's own representative "
            "pair(s): " + "; ".join(bad)
        )
    if n_definite > 0:
        status = "bounded_pass"

    if status is None:
        # No usable representative pairs — fall back to random samples,
        # but ONLY to look for supporting evidence (never to refute).
        try:
            samples = sample_words(task_ir, count=10, max_len=12)
        except Exception:
            samples = []
        candidate_words = sorted(
            {s.get("word") for s in samples if isinstance(s.get("word"), str)},
            key=len,
        )
        in_bucket: list[str] = []
        out_bucket: list[str] = []
        for w in candidate_words:
            verdict_w = oracle(w + distinguishing_suffix)
            if verdict_w is None:
                continue
            (in_bucket if verdict_w else out_bucket).append(w)
        if in_bucket and out_bucket:
            n_pairs = min(3, len(in_bucket) * len(out_bucket))
            pairs = [(in_bucket[i % len(in_bucket)], out_bucket[i % len(out_bucket)])
                     for i in range(n_pairs)]
            seen_pairs: set[tuple[str, str]] = set()
            unique_pairs: list[tuple[str, str]] = []
            for pr in pairs:
                if pr not in seen_pairs:
                    seen_pairs.add(pr)
                    unique_pairs.append(pr)
            checked = "; ".join(f"u={u!r},v={v!r}" for u, v in unique_pairs)
            check_name = check_name + f" ({checked})"
            status = "bounded_pass"
        # If the suffix fails to separate arbitrary random samples, that is
        # NOT evidence against the proof (VERDICT_POLICY.md §4 fix) — leave
        # status None so trust stays at well_formed.

    if status == "bounded_pass":
        dead_ok, dead_issues = _check_dead_class_finite(task_ir)
        if dead_ok is False:
            issue = "; ".join(dead_issues)
            check_name = check_name + " + dead_class_finite"
            # Approximate, bounded check — never downgrades past well_formed
            # on its own (docs/VERDICT_POLICY.md §4: not conclusive).
            status = "bounded_pass"

    return status, check_name, issue


# ---------------------------------------------------------------------------
# 5. inh_ambiguity  (§6.1.5)
# ---------------------------------------------------------------------------

def _verify_inh_ambiguity(proof_sketch: dict, _task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    # disjunction_identified
    di = proof_sketch.get("disjunction_identified", "")
    _check(
        "disjunction_identified_non_empty",
        isinstance(di, str) and len(di) > 0,
        "disjunction_identified must be a non-empty string",
        checks_run, passed, issues,
    )

    # overlap_words
    ow = proof_sketch.get("overlap_words", "")
    _check(
        "overlap_words_non_empty",
        (isinstance(ow, str) and len(ow) > 0) or (isinstance(ow, list) and len(ow) > 0),
        "overlap_words must be non-empty",
        checks_run, passed, issues,
    )

    # ambiguity_argument
    aa = proof_sketch.get("ambiguity_argument", "")
    _check(
        "ambiguity_argument_non_empty",
        isinstance(aa, str) and len(aa) > 0,
        "ambiguity_argument must be a non-empty string",
        checks_run, passed, issues,
    )

    # dcfl_implication — must mention "not DCFL" or "UnambCF"
    di_impl = proof_sketch.get("dcfl_implication", "")
    _check(
        "dcfl_implication_mentions_not_dcfl_or_unambcf",
        isinstance(di_impl, str) and (
            "not dcfl" in di_impl.lower()
            or "not a dcfl" in di_impl.lower()
            or "unambcf" in di_impl.lower()
            or "not deterministic" in di_impl.lower()
        ),
        "dcfl_implication must mention 'not DCFL' or 'UnambCF'",
        checks_run, passed, issues,
    )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"
    return _make_result(status, checks_run, n_passed, issues if issues else None)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_VERIFIERS: dict[str, Any] = {
    "stack_strategy": _verify_stack_strategy,
    "closure_reduction": _verify_closure_reduction,
    "dcfl_pumping": _verify_dcfl_pumping,
    "shallit": _verify_shallit,
    "inh_ambiguity": _verify_inh_ambiguity,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_agent_results(agent_results: dict[str, Any], task_ir: dict) -> dict:
    """Verify claims from all specialist agents.

    Pure function — no LLM calls.  Dispatches to per-agent verifiers based
    on agent_name, checking structural and semantic properties of proof_sketch.

    Parameters
    ----------
    agent_results : dict[str, Any]
        Mapping of agent_name -> agent output dict.  Each output is expected
        to have ``status`` and optionally ``proof_sketch``.
    task_ir : dict
        The validated DCFL IR for the task.

    Returns
    -------
    dict
        Mapping of agent_name -> verification result with keys:
        ``verification_status``, ``checks_run``, ``checks_passed``,
        ``checks_total``, and optionally ``issues``.
    """
    result: dict[str, Any] = {}

    for agent_name, output in agent_results.items():
        # Guard: output must be a dict
        if not isinstance(output, dict):
            result[agent_name] = _make_result(
                "error", [], 0, ["invalid output format — expected dict"]
            )
            continue

        status = output.get("status", "unknown")

        # If agent marked itself not_applicable, pass through
        if status == "not_applicable":
            result[agent_name] = _make_result("not_applicable", [], 0)
            continue

        # If agent failed and has no proof_sketch, nothing to verify
        proof_sketch = output.get("proof_sketch")
        if status == "fail" and proof_sketch is None:
            result[agent_name] = _make_result("not_applicable", [], 0)
            continue

        # If proof_sketch is None, we cannot verify
        if proof_sketch is None:
            result[agent_name] = _make_result("not_verified", [], 0)
            continue

        # Dispatch to agent-specific verifier
        verifier = _VERIFIERS.get(agent_name)
        if verifier is None:
            # Unknown agent — cannot verify
            result[agent_name] = _make_result(
                "not_verified", [], 0,
                [f"no verifier implemented for agent '{agent_name}'"],
            )
            continue

        try:
            result[agent_name] = verifier(proof_sketch, task_ir)
        except Exception as exc:
            result[agent_name] = _make_result(
                "error", [], 0, [f"exception during verification: {exc}"]
            )

    return result
