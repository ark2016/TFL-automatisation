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
    build_membership_oracle_from_ir,
    instantiate_exponent_pattern,
)
from dcfl_system.lib.dpda import (
    DPDAFormatError,
    check_determinism,
    dpda_run,
    normalize_epsilon_accept_sinks,
    to_cfl_pda,
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
# well_formed capped at 0.55 (not 0.60): without a machine check, confidence
# must not reach 0.6, the threshold at which tfl-eval treats a verdict as
# "confident" (docs/VERDICT_POLICY.md §2, 2026-09-27).
CONFIDENCE_CAPS: dict[str, float] = {
    "verified": 0.98,
    "bounded_pass": 0.85,
    "well_formed": 0.55,
    "not_verified": 0.40,
    "not_applicable": 0.40,
    "error": 0.40,
}
# Cap that applies whenever R3's contradiction condition holds, regardless of
# which side "wins" the trust comparison.
CONTRADICTION_CONFIDENCE_CAP = 0.50

# stack_strategy / dpda (docs/VERDICT_POLICY.md R2'): the minimum number of
# decisively-oracled words (up to this length) a simulation check must cover
# before it can earn `bounded_pass` -- below this, coverage is treated as
# incomplete and trust stays at `well_formed`.
_DPDA_MIN_WORDS = 30
_DPDA_MAX_WORD_LEN = 10

# Backlog review (major): `sample_words`'s own heuristics do not reliably
# produce short "wrong prefix" words (e.g. a bare "b" with no preceding "a"),
# so a submitted dpda that is wrong on exactly those words (live dcfl-04:
# `q0 --b--> q_b` accepts "b", "bc", "bcc", ...) could reach `bounded_pass`
# in the real pipeline even though it is refuted by simulation. A full
# enumeration of every word over the task alphabet up to this budget is
# added to (never replaces) the sampler's candidates so short counterexamples
# like "b" are always covered, not just under a test's `sample_words` stub.
_DPDA_FULL_ENUM_WORD_BUDGET = 3000


def _full_enumeration_max_len(
    alphabet_size: int, budget: int = _DPDA_FULL_ENUM_WORD_BUDGET,
    max_len_cap: int = _DPDA_MAX_WORD_LEN,
) -> int:
    """Largest word length k (capped at ``max_len_cap``, matching the
    simulation's own ``_DPDA_MAX_WORD_LEN``) such that enumerating every word
    over an alphabet of this size up to length k stays within ``budget``
    total words -- for |Sigma| = 3 this gives k = 6 (3**1 + ... + 3**6 =
    1092 <= 3000 < 1092 + 3**7), the exact bound the review asked for."""
    if alphabet_size <= 1:
        return max_len_cap
    k = 0
    total = 1  # the empty word (length 0)
    while k < max_len_cap:
        nxt = alphabet_size ** (k + 1)
        if total + nxt > budget:
            break
        total += nxt
        k += 1
    return max(k, 1)


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
    has_dpda = isinstance(proof_sketch.get("dpda"), dict)

    if not checks_run and not has_dpda:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])

    status = "well_formed" if (not checks_run or n_passed == len(checks_run)) else "not_verified"

    if not has_dpda:
        # docs/VERDICT_POLICY.md R2': no executable DPDA artifact -> trust
        # caps at well_formed regardless of how many word-strategy fields
        # were present and internally consistent (a prose "стратегия
        # словами" is not a certificate).
        return _make_result(status, checks_run, n_passed, issues if issues else None)

    # R2' -- a `dpda` artifact IS present: (a) syntactic determinism check
    # (complete -> refuted on any violation), then (b) bounded simulation
    # against the task's own language oracle (bounded_pass / refuted /
    # well_formed if no oracle or insufficient coverage).
    dpda_status, dpda_check, dpda_issue, dpda_details = _verify_stack_strategy_dpda(
        proof_sketch["dpda"], task_ir,
    )
    checks_run.append(dpda_check)
    if dpda_issue:
        issues.append(dpda_issue)

    if dpda_status == "refuted":
        refuted_result = _make_result("refuted", checks_run, n_passed, issues)
        if dpda_details:
            refuted_result["details"] = dpda_details
        return refuted_result

    if dpda_status is not None:
        status = dpda_status
        n_passed += 1

    result = _make_result(status, checks_run, n_passed, issues if issues else None)
    if dpda_details:
        result["details"] = dpda_details
    return result


def _verify_stack_strategy_dpda(
    dpda: Any, task_ir: dict,
) -> tuple[str | None, str, str, dict[str, Any] | None]:
    """R2' (docs/VERDICT_POLICY.md): verify a stack_strategy proof's `dpda`
    artifact.

    (0) Normalization (docs/VERDICT_POLICY.md R2', normalization paragraph):
        ``dpda.normalize_epsilon_accept_sinks`` runs FIRST, on the dpda AS
        SUBMITTED -- it is the one canonical, language-preserving rewrite the
        policy allows before determinism is checked: an epsilon transition
        `(q, Z) -> q_acc` into a dead-end accepting sink is deleted, and the
        pair `(q, Z)` is added to the normalized dpda's `accept_configs`
        instead (acceptance is then keyed on the exact stack-top configuration,
        not blanket-marking `q` itself -- see the function's own docstring for
        the equivalence argument, and why this needs no extra guard on what
        else `q` occurs with, unlike an earlier revision). Every subsequent
        step -- (a) and (b) below -- runs against the NORMALIZED automaton,
        never the original; the rewrite's notes (empty if nothing qualified)
        are recorded in the returned ``details["normalization"]``, and the
        resulting ``accept_configs`` (if any) in ``details["accept_configs"]``,
        regardless of the final trust, so the report always shows exactly what
        was rewritten before determinism was judged.
    (a) Syntactic determinism (``dpda.check_determinism``) -- a COMPLETE
        check, so a violation is a genuine, deterministic counterexample:
        ``refuted``, with the conflicting transitions named. Because
        normalization now applies unconditionally to every qualifying
        epsilon-into-accept-sink, a submitted dpda that is deterministic
        AFTER normalization is not automatically CORRECT for the task's
        language -- see (b): a stray letter transition can still make the
        normalized, deterministic automaton accept words it shouldn't
        (docs/VERDICT_POLICY.md R2' precedent: live dcfl-04, `q0 --b--> q_b`
        with no preceding `a` wrongly reaches an `accept_configs` hit on
        input `"b"`), and only simulation against the language oracle catches
        that, not this syntactic check.
    (b) Simulation: run the (normalized) DPDA via ``dcfl_system.lib.dpda.dpda_run``
        (a dedicated deterministic simulator that understands `accept_configs`
        -- NOT ``to_cfl_pda``/``pda_accepts``, which silently ignore it) on
        words sampled by ``word_sampler`` (positive and negative, length <=
        ``_DPDA_MAX_WORD_LEN``) against the task's OWN language oracle
        (``word_sampler.build_membership_oracle_from_ir`` -- the single
        oracle-building entry point, dispatching on ``set_builder``
        (variables or exponent-notation pattern) / ``grammar``; never the
        proof's own claims). All of at least ``_DPDA_MIN_WORDS`` decisively-
        oracled words agree -> ``bounded_pass`` (details:
        ``determinism: "verified"``); any disagreement -> ``refuted`` with
        the counterexample(s). No membership oracle available for this task
        (unsupported ``input_format``, or the spec doesn't parse into one),
        or fewer than ``_DPDA_MIN_WORDS`` words could be decided -> trust
        stays ``well_formed`` (the DPDA is syntactically valid and
        deterministic, just not checked against the language).
    """
    check_name = "dpda_determinism_and_simulation"
    if not isinstance(dpda, dict):
        return "refuted", check_name, "dpda must be an object", None

    dpda, normalization_notes = normalize_epsilon_accept_sinks(dpda)
    base_details: dict[str, Any] = {}
    if normalization_notes:
        base_details["normalization"] = normalization_notes
    if dpda.get("accept_configs"):
        base_details["accept_configs"] = dpda["accept_configs"]

    conflicts = check_determinism(dpda)
    if conflicts:
        return "refuted", check_name, (
            "dpda is not deterministic: " + "; ".join(conflicts)
        ), {**base_details, "determinism": "violated", "conflicts": conflicts}

    # to_cfl_pda is used here ONLY as a compatibility structural-validity
    # check (unknown states/symbols, missing required fields) -- it does not
    # understand accept_configs, so it must never be used to simulate; (b)
    # below uses dpda_run instead.
    try:
        to_cfl_pda(dpda)
    except DPDAFormatError as exc:
        return "refuted", check_name, f"dpda is structurally invalid: {exc}", {
            **base_details, "determinism": "verified", "structure": "invalid",
        }

    oracle = build_membership_oracle_from_ir(task_ir)
    if oracle is None:
        return "well_formed", check_name, "", {**base_details, "determinism": "verified"}

    try:
        samples = sample_words(task_ir, count=60, max_len=_DPDA_MAX_WORD_LEN)
    except Exception as exc:
        return "well_formed", check_name, (
            f"could not sample words for simulation: {exc}"
        ), {**base_details, "determinism": "verified"}

    candidate_set = {
        s["word"] for s in samples
        if isinstance(s.get("word"), str) and len(s["word"]) <= _DPDA_MAX_WORD_LEN
    }
    # Backlog review (major): always add a full enumeration of the task
    # alphabet (not just the sampler's heuristic picks) so a short but
    # decisive counterexample the sampler never happens to generate -- e.g.
    # a bare "b" with no preceding "a" -- is still caught by simulation.
    alphabet = task_ir.get("alphabet")
    if isinstance(alphabet, list) and alphabet:
        max_len = _full_enumeration_max_len(len(alphabet))
        candidate_set.update(_enumerate_words_up_to_length(
            alphabet, max_len=max_len, limit=_DPDA_FULL_ENUM_WORD_BUDGET,
        ))
    candidates = sorted(candidate_set)

    checked = 0
    mismatches: list[str] = []
    for word in candidates:
        expected = oracle(word)
        if expected is None:
            continue
        actual = dpda_run(dpda, word)
        if actual is None:
            # Step budget exceeded (pathological epsilon cycle) -- inconclusive
            # for this word, not a definite rejection; skip it like a TimeoutError.
            continue
        checked += 1
        if actual != expected:
            mismatches.append(
                f"word={word!r}: oracle says in_L={expected}, dpda says accepted={actual}"
            )
        if len(mismatches) >= 5:
            break

    if mismatches:
        return "refuted", check_name, (
            "dpda simulation disagrees with the task's language oracle: "
            + "; ".join(mismatches)
        ), {
            **base_details, "determinism": "verified",
            "counterexamples": mismatches, "words_checked": checked,
        }

    if checked < _DPDA_MIN_WORDS:
        return "well_formed", check_name, (
            f"only {checked} words could be decisively checked (< {_DPDA_MIN_WORDS}) "
            "-- coverage incomplete, trust stays well_formed"
        ), {**base_details, "determinism": "verified", "words_checked": checked}

    return "bounded_pass", check_name, "", {
        **base_details, "determinism": "verified", "words_checked": checked,
    }


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

    # word_instances (docs/VERDICT_POLICY.md §4 dcfl/dcfl_pumping): concrete
    # word_w = x + y, word_w_prime = x + z instantiated at n = p + 2 for
    # p in {2, 3}, REQUIRED -- "без word_instances trust не выше
    # not_verified" (not well_formed!): a structurally-correct proof whose
    # words are only ever described in prose (like "aⁿbⁿ⁻¹") but never
    # actually instantiated is not a proof (the live dcfl-21 bug this locks
    # in: well_formed non_dcfl 0.60 for an actual DCFL language). This is a
    # REQUIRED check like the others above -- its failure caps `status`
    # below at "not_verified", same mechanism as every other missing field.
    word_instances = proof_sketch.get("word_instances")
    parsed_instances: dict[int, tuple[str, str, int]] = {}
    _check(
        "word_instances_present",
        isinstance(word_instances, dict) and "2" in word_instances and "3" in word_instances,
        "word_instances must be a closed object {'2': {...}, '3': {...}} giving the "
        "concrete word_w/word_w_prime instantiated at n = p + 2 for p in {2, 3} "
        "(docs/VERDICT_POLICY.md §4: without it, trust cannot exceed not_verified)",
        checks_run, passed, issues,
    )
    if isinstance(word_instances, dict):
        for p in (2, 3):
            entry = word_instances.get(str(p))
            x_length = _validate_word_instance_entry(entry, p)
            _check(
                f"word_instances_p{p}_valid",
                x_length is not None,
                f"word_instances['{p}'] must be {{'w': ..., 'w_prime': ..., "
                f"'x_length': ...}} with a common prefix x of length > {p} shared by "
                "w and w_prime, and suffixes y = w[x_length:], z = w_prime[x_length:] "
                "that are both non-empty and start with the same letter (THEORY.md §1.1)",
                checks_run, passed, issues,
            )
            if x_length is not None:
                parsed_instances[p] = (entry["w"], entry["w_prime"], x_length)

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "well_formed" if n_passed == len(checks_run) else "not_verified"

    # Step 2 (VERDICT_POLICY.md §4, dcfl_pumping): if the structure is
    # well-formed (which now requires a valid word_instances for BOTH p=2
    # and p=3), check w, w' in L with a real oracle and brute-force
    # conditions (1)/(2) on the proof's own literal x/y/z decomposition.
    # Only runs when an oracle is available; otherwise trust stays at
    # well_formed, per the fallback in VERDICT_POLICY.md §4.
    if status == "well_formed":
        semantic_status, semantic_check, semantic_issue = _semantic_check_dcfl_pumping(
            parsed_instances, task_ir,
        )
        if semantic_status is not None:
            status = semantic_status
            checks_run.append(semantic_check)
            if semantic_issue:
                issues.append(semantic_issue)
            if semantic_status != "refuted":
                n_passed += 1

    return _make_result(status, checks_run, n_passed, issues if issues else None)


def _validate_word_instance_entry(entry: Any, p: int) -> int | None:
    """Structural validation of one ``word_instances['<p>']`` entry (no
    oracle needed): returns the entry's own ``x_length`` when it is a
    genuinely usable instance of Yu's two-word pumping lemma (THEORY.md
    §1.1) at this ``p`` -- ``w``/``w_prime`` are non-empty strings sharing a
    literal common prefix of length ``x_length`` (> ``p``, and strictly
    shorter than both words, so the suffixes ``y``/``z`` are non-empty), with
    ``y[0] == z[0]`` (the lemma's own precondition) -- or ``None`` if not.
    """
    if not isinstance(entry, dict):
        return None
    w = entry.get("w")
    w_prime = entry.get("w_prime")
    x_length = entry.get("x_length")
    if not isinstance(w, str) or not w or not isinstance(w_prime, str) or not w_prime:
        return None
    if not isinstance(x_length, int) or isinstance(x_length, bool):
        return None
    if not (p < x_length < len(w) and x_length < len(w_prime)):
        return None
    if w[:x_length] != w_prime[:x_length]:
        return None
    y, z = w[x_length:], w_prime[x_length:]
    if not y or not z or y[0] != z[0]:
        return None
    return x_length


def _pump_outcome(
    oracle: Callable[[str], bool | None],
    pump: Callable[[int], tuple[str, str]],
) -> str:
    """Given ``pump(i) -> (pumped_w, pumped_w_prime)`` for one concrete
    decomposition, decide whether the decomposition is:

    - ``"closed"``      — some i in {0, 2} (or, if both of those keep both
      words in L, i = 3 as a tie-breaker — VERDICT_POLICY.md §4) breaks
      membership of at least one pumped word: this decomposition does NOT
      refute the claim that the lemma's condition fails everywhere.
    - ``"refuted"``      — i = 0, 2 AND 3 all keep BOTH pumped words in L: a
      genuine counterexample (the condition actually holds here).
    - ``"inconclusive"`` — the oracle could not decide enough of the
      required i's to tell either way.
    """
    saw_inconclusive = False
    for i in (0, 2):
        w_i, wp_i = pump(i)
        in_w = oracle(w_i)
        in_wp = oracle(wp_i)
        if in_w is False or in_wp is False:
            return "closed"
        if in_w is None or in_wp is None:
            saw_inconclusive = True
    if saw_inconclusive:
        return "inconclusive"
    w3, wp3 = pump(3)
    in_w3 = oracle(w3)
    in_wp3 = oracle(wp3)
    if in_w3 is False or in_wp3 is False:
        return "closed"
    if in_w3 is None or in_wp3 is None:
        return "inconclusive"
    return "refuted"


_COND1_MAX_DECOMPOSITIONS = 600
_COND2_MAX_DECOMPOSITIONS = 3000
_COND2_MAX_SUFFIX_FACTOR_LEN = 4


def _check_condition1(
    x: str, y: str, z: str, p: int, oracle: Callable[[str], bool | None],
    max_decompositions: int = _COND1_MAX_DECOMPOSITIONS,
) -> tuple[str, str | None]:
    """THEORY.md §1.1 condition (1): brute-force every pair (x2, x4) — in any
    position within x, with x = x1 x2 x3 x4 x5, |x2 x4| >= 1, |x2 x3 x4| <= p
    — and check that pumping i in {0, 2, (3)} breaks membership of at least
    one of xy, xz for EVERY such pair (docs/VERDICT_POLICY.md §4).

    Returns (status, message): status is one of "closed" (every pair tried
    was decisively resolved -- either found to survive pumping and closed,
    or refuted -- with NO pair left inconclusive), "refuted" (some pair
    survives i=0,2,3 — a genuine counterexample, message describes it),
    "no_evidence" (x too short to yield any candidate pair), or "limit" (the
    brute force exceeded its decomposition budget before finishing, OR at
    least one tried pair's oracle calls never decided -- either way,
    coverage is incomplete and "closed" would overstate what was actually
    checked; backlog review, major finding).
    """
    n = len(x)
    tested = 0
    any_closed = False
    any_inconclusive = False
    for start in range(n):
        for length in range(1, p + 1):
            end = start + length
            if end > n:
                break
            window = x[start:end]
            wl = len(window)
            for a in range(wl + 1):
                for b in range(a, wl + 1):
                    if a == 0 and b == wl:
                        continue  # x2 == x4 == "" excluded: |x2 x4| >= 1
                    tested += 1
                    if tested > max_decompositions:
                        return "limit", None
                    x2, x3, x4 = window[:a], window[a:b], window[b:]

                    def pump(i: int, x2=x2, x3=x3, x4=x4, start=start, end=end):
                        core = x2 * i + x3 + x4 * i
                        prefix = x[:start] + core + x[end:]
                        return prefix + y, prefix + z

                    outcome = _pump_outcome(oracle, pump)
                    if outcome == "refuted":
                        return "refuted", (
                            f"condition (1) p={p}: window x[{start}:{end}]={window!r}, "
                            f"split x2={x2!r}/x3={x3!r}/x4={x4!r} keeps both pumped "
                            f"words in L at i=0,2,3"
                        )
                    if outcome == "closed":
                        any_closed = True
                    elif outcome == "inconclusive":
                        any_inconclusive = True
    if any_inconclusive:
        return "limit", None
    return ("closed" if any_closed else "no_evidence"), None


def _bounded_factorizations(
    s: str, max_mid_len: int,
) -> list[tuple[str, str, str]]:
    """All (s1, s2, s3) with s = s1 + s2 + s3 and 0 <= len(s2) <= max_mid_len.

    The trivial "no pumping in this component" factorization (s2 = "") is
    included exactly once (position doesn't matter when s2 is empty).
    Bounding |s2| keeps condition (2)'s y/z-factorization search tractable
    (docs/VERDICT_POLICY.md §4: "лимит по числу разбиений") — this is a
    deliberate approximation, not the literal unbounded "all factorizations".
    """
    out: list[tuple[str, str, str]] = [("", "", s)]
    n = len(s)
    for start in range(n + 1):
        max_len = min(max_mid_len, n - start)
        for length in range(1, max_len + 1):
            end = start + length
            out.append((s[:start], s[start:end], s[end:]))
    return out


def _check_condition2(
    x: str, y: str, z: str, p: int, oracle: Callable[[str], bool | None],
    max_decompositions: int = _COND2_MAX_DECOMPOSITIONS,
    max_suffix_factor_len: int = _COND2_MAX_SUFFIX_FACTOR_LEN,
) -> tuple[str, str | None]:
    """THEORY.md §1.1 condition (2): brute-force every x2 within the last p
    symbols of x (x = x1 x2 x3, |x2| >= 1, |x2 x3| <= p), synchronised with
    every (bounded) factorization of y = y1 y2 y3 and z = z1 z2 z3, and check
    that pumping i in {0, 2, (3)} breaks membership of at least one of
    x1 x2^i x3 y1 y2^i y3, x1 x2^i x3 z1 z2^i z3 for EVERY such triple
    (docs/VERDICT_POLICY.md §4). Same return convention as
    ``_check_condition1``.
    """
    n = len(x)
    tested = 0
    any_closed = False
    any_inconclusive = False
    max_tail = min(p, n)
    y_factorizations = _bounded_factorizations(y, max_suffix_factor_len)
    z_factorizations = _bounded_factorizations(z, max_suffix_factor_len)
    for tail_len in range(1, max_tail + 1):
        pos = n - tail_len
        tail = x[pos:]
        for k in range(1, tail_len + 1):
            x2, x3 = tail[:k], tail[k:]
            for y1, y2, y3 in y_factorizations:
                for z1, z2, z3 in z_factorizations:
                    tested += 1
                    if tested > max_decompositions:
                        return "limit", None

                    def pump(i: int, x2=x2, x3=x3, pos=pos,
                             y1=y1, y2=y2, y3=y3, z1=z1, z2=z2, z3=z3):
                        prefix = x[:pos] + x2 * i + x3
                        return (
                            prefix + y1 + y2 * i + y3,
                            prefix + z1 + z2 * i + z3,
                        )

                    outcome = _pump_outcome(oracle, pump)
                    if outcome == "refuted":
                        return "refuted", (
                            f"condition (2) p={p}: x2={x2!r} in tail x[{pos}:{n}], "
                            f"y=({y1!r}+{y2!r}+{y3!r}), z=({z1!r}+{z2!r}+{z3!r}) "
                            f"keeps both pumped words in L at i=0,2,3"
                        )
                    if outcome == "closed":
                        any_closed = True
                    elif outcome == "inconclusive":
                        any_inconclusive = True
    if any_inconclusive:
        return "limit", None
    return ("closed" if any_closed else "no_evidence"), None


def _xyz_from_proof_decomposition(
    common_prefix_x: Any, suffix_y: Any, suffix_z: Any,
    n: int, alphabet_set: set[str], w: str, w_prime: str,
) -> tuple[str, str, str] | None:
    """Instantiate the proof's OWN claimed ``common_prefix_x``/``suffix_y``/
    ``suffix_z`` at the same ``n`` used for word_w/word_w_prime, when they
    are given in the same pure exponent notation (not a free-form prose/dict
    description, which ``instantiate_exponent_pattern`` can't parse).

    Returns (x, y, z) only if this reconstructs the already-checked
    ``w``/``w_prime`` exactly (``w == x + y``, ``w_prime == x + z``) — i.e.
    the proof's decomposition is actually consistent with the words it
    claims are in L at this instantiation. ``None`` otherwise (caller falls
    back to ``_xyz_from_common_prefix``).
    """
    if not (
        isinstance(common_prefix_x, str)
        and isinstance(suffix_y, str)
        and isinstance(suffix_z, str)
    ):
        return None
    x = instantiate_exponent_pattern(common_prefix_x, n, alphabet_set)
    y = instantiate_exponent_pattern(suffix_y, n, alphabet_set)
    z = instantiate_exponent_pattern(suffix_z, n, alphabet_set)
    if x is None or y is None or z is None:
        return None
    if w != x + y or w_prime != x + z:
        return None
    return x, y, z


def _xyz_from_common_prefix(w: str, w_prime: str) -> tuple[str, str, str] | None:
    """Fall back to a prefix of ``w``/``w_prime``'s own longest common run,
    backed off by exactly one character from the FULL common run so that
    the lemma's own precondition (THEORY.md §1.1: y, z non-empty and
    ``⁽¹⁾y = ⁽¹⁾z``) holds automatically: dropping the last shared character
    from x turns it into the shared first character of both y and z, and
    since it was still shared (index common_len - 1 is inside the run),
    ``y[0] == z[0]`` is guaranteed. Using the FULL common run instead (the
    previous, buggy behaviour) always left y or z empty or starting on the
    first point of disagreement, so the lemma's precondition never held and
    every checked pair was one the lemma doesn't even apply to.

    Returns None if the common run is too short to back off from (length 0)
    or backing off still leaves y or z empty (w == w_prime, or one is a
    prefix of the other with nothing past the back-off point).
    """
    common_len = 0
    for a, b in zip(w, w_prime):
        if a != b:
            break
        common_len += 1
    if common_len < 1:
        return None
    x_len = common_len - 1
    x, y, z = w[:x_len], w[x_len:], w_prime[x_len:]
    if not y or not z or y[0] != z[0]:
        return None
    return x, y, z


def _semantic_check_dcfl_pumping(
    instances: dict[int, tuple[str, str, int]], task_ir: dict,
) -> tuple[str | None, str, str]:
    """Step 2 for dcfl_pumping (VERDICT_POLICY.md §4).

    Check w, w' in L with a real membership oracle, AND brute-force BOTH
    condition (1) (pair (x2, x4) anywhere in x, |x2 x3 x4| <= p) and
    condition (2) (x2 in the last p symbols of x, synchronised with every
    bounded factorization of y and z) of Yu's two-word pumping lemma
    (THEORY.md §1.1) — membership alone is necessary but not sufficient
    evidence for the claim that the lemma's disjunction fails everywhere.

    ``instances`` maps p (2 and/or 3) to the proof's own ``(w, w_prime,
    x_length)`` — the CONCRETE literal words from ``proof_sketch``'s
    mandatory ``word_instances`` field (docs/VERDICT_POLICY.md §4: "без
    word_instances trust не выше not_verified"), already structurally
    validated by :func:`_validate_word_instance_entry` (common prefix
    ``x = w[:x_length]`` genuinely shared by both words, ``y = w[x_length:]``
    / ``z = w_prime[x_length:]`` non-empty with ``y[0] == z[0]``) by the time
    this function is called — no further parsing/instantiation of prose is
    needed or attempted here.

    Returns (status_or_None, check_name, issue):
    - ``"refuted"`` — either a word isn't actually in L, or some
      decomposition under condition (1) or (2) survives pumping at
      i = 0, 2 AND 3 (a genuine counterexample to the "no pumping works"
      claim).
    - ``"well_formed"`` (with a note) — EVERY other outcome, including when
      conditions (1)/(2) fully closed for a tested p (backlog review,
      BLOCKER fix): closure at a small, fixed p in {2, 3} is NOT elevated to
      ``bounded_pass`` any more. Yu's real pumping constant for a given DCFL
      is generally far larger than 2 or 3, so a decomposition search bounded
      to |x2 x3 x4| <= p at p = 2 or 3 only ever samples a vanishing sliver
      of the space the lemma actually quantifies over -- closing every
      decomposition it happened to try proves nothing about the ones it
      didn't, and is reproducibly reachable for words of an ACTUALLY-DCFL
      language (docs/VERDICT_POLICY.md §4 / live dcfl-21 precedent: enumerating
      members of `task_grammar_aSSb`, a genuine DCFL language, up to length 10
      found 101 word_instances pairs at p=2 and 42 at p=3 whose conditions (1)
      and (2) both close under this exact search, i.e. a FALSE `non_dcfl`
      claim reaching `bounded_pass` -- see
      ``test_verify_dcfl_pumping_exam04_small_p_closure_does_not_reach_bounded_pass``).
      Only an actual counterexample (``"refuted"``) is decisive; closure
      alone, a decomposition-budget limit, or "no candidate decomposition"
      all leave trust at ``well_formed`` (0.55) -- a necessary-condition
      check, not sufficient evidence.
    - ``None`` — no oracle could be used, or ``instances`` was empty; trust
      stays at ``well_formed`` with no note.
    """
    check_name = "semantic_word_membership[p=2,3]"
    if not instances:
        return None, check_name, ""

    oracle = build_membership_oracle_from_ir(task_ir)
    if oracle is None:
        return None, check_name, ""

    bad: list[str] = []
    for p, (w, w_prime, _x_length) in sorted(instances.items()):
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

    # Membership alone confirmed; now exercise conditions (1) and (2)
    # (THEORY.md §1.1) — the checks that actually distinguish a real
    # pumping argument from two arbitrary words that both happen to be in L.
    check_name = "semantic_word_membership_and_conditions[p=2,3]"
    any_p_fully_closed = False
    limit_hit = False
    inconclusive_p: list[int] = []
    for p, (w, w_prime, x_length) in sorted(instances.items()):
        x, y, z = w[:x_length], w[x_length:], w_prime[x_length:]

        status1, msg1 = _check_condition1(x, y, z, p, oracle)
        if status1 == "refuted":
            return "refuted", check_name, msg1

        status2, msg2 = _check_condition2(x, y, z, p, oracle)
        if status2 == "refuted":
            return "refuted", check_name, msg2

        if status1 == "closed" and status2 == "closed":
            any_p_fully_closed = True
        elif status1 == "limit" or status2 == "limit":
            limit_hit = True
        else:
            # "no_evidence" on either condition (no candidate decomposition
            # to test at all) -- not the same as "closed", and per
            # VERDICT_POLICY.md §4 not enough to count this p as evidence.
            inconclusive_p.append(p)

    if any_p_fully_closed and not limit_hit and not inconclusive_p:
        # Backlog review (BLOCKER): closure of conditions (1)/(2) at a small,
        # fixed p in {2, 3} is NOT sufficient evidence for Yu's pumping lemma
        # -- the real constant is generally far larger, so this bounded
        # search only ever samples a sliver of what the lemma quantifies
        # over. Confirmed reproducible on an ACTUALLY-DCFL language
        # (task_grammar_aSSb / exam_04): dozens of word_instances close both
        # conditions at p=2/p=3 for a FALSE non_dcfl claim. Trust stays at
        # well_formed (0.55) -- a necessary-condition check, never
        # bounded_pass -- and only a genuine counterexample (refuted, above)
        # is decisive (docs/VERDICT_POLICY.md §4).
        return "well_formed", check_name, (
            "conditions (1)/(2) closed for the tested p (all decompositions "
            "tried broke membership at some i), but closure at a small fixed "
            "p is not exhaustive evidence for Yu's lemma -- trust stays "
            "well_formed, not bounded_pass (docs/VERDICT_POLICY.md §4)"
        )
    if limit_hit or inconclusive_p:
        return "well_formed", check_name, (
            "condition (1)/(2) check did not fully close for every tested p "
            "(decomposition-budget limit hit, and/or no candidate decomposition "
            "at some p) — coverage incomplete, trust stays well_formed "
            "(docs/VERDICT_POLICY.md §4)"
        )
    # Only membership was actually checkable — per VERDICT_POLICY.md §4,
    # that is not enough to earn bounded_pass on its own.
    return None, check_name, ""


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
        # dead_class_status is a closed enum, not a free-text field (VERDICT_POLICY.md
        # §4 dcfl/shallit): absent/invalid ⇒ this check fails ⇒ not_verified below;
        # "infinite" is a valid VALUE but makes the technique self-admittedly
        # inapplicable, handled by the explicit early-refute right after this loop.
        #
        # The contract's live value space is now just {"empty", "infinite"} (D is
        # closed under right-extension, so "D finite and nonempty" cannot occur --
        # "D конечен" only ever meant D = ∅). The legacy value "finite" (pre
        # docs/VERDICT_POLICY.md §4 trim) is still ACCEPTED here for backward
        # compatibility with already-recorded output (older mocks/live runs), read
        # as "empty" for every downstream check, with a note in `issues` (not a
        # failing check -- the value itself was never wrong, just retired).
        raw_dead_status = proof_sketch.get("dead_class_status")
        dead_class_status = _normalize_dead_class_status(raw_dead_status)
        if raw_dead_status == "finite":
            issues.append(
                "dead_class_status: устаревшее значение 'finite' прочитано как "
                "'empty' (обратная совместимость, docs/VERDICT_POLICY.md §4) -- D "
                "замкнут относительно продолжений справа, поэтому непустой D "
                "бесконечен, и 'D конечен' всегда означало D = ∅"
            )
        _check(
            "dead_class_status_valid",
            dead_class_status in ("empty", "infinite"),
            "dead_class_status must be one of 'empty', 'infinite' (legacy 'finite' "
            "is accepted and read as 'empty') -- the мёртвый класс D (THEORY.md §1.2); "
            "nerode_classes is inapplicable when D is infinite",
            checks_run, passed, issues,
        )
        for field, ru_hint in (
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
        # VERDICT_POLICY.md §4: "infinite при status success ⇒ refuted (доказательство
        # само признаёт технику неприменимой)" -- a proof that claims dead_class_status
        # == "infinite" but was still returned as a "success" (this verifier is never
        # reached for status == "not_applicable", see verify_agent_results) is
        # self-contradictory: it should have returned not_applicable instead.
        if dead_class_status == "infinite":
            return _make_result(
                "refuted", checks_run, sum(1 for p in passed if p),
                issues + [
                    "dead_class_status = 'infinite': proof admits the technique is "
                    "inapplicable (dead class D infinite ⇒ theorem 4.7.4 [Sh] gives no "
                    "information about DCFL membership, THEORY.md §1.2) -- should have "
                    "returned status 'not_applicable', not a non_dcfl verdict"
                ],
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
    elif status == "well_formed" and technique == "prefix_continuation":
        semantic_status, semantic_check, semantic_issue = (
            _semantic_check_shallit_prefix_continuation(proof_sketch, task_ir)
        )
        if semantic_status is not None:
            status = semantic_status
            checks_run.append(semantic_check)
            if semantic_issue:
                issues.append(semantic_issue)
            if semantic_status != "refuted":
                n_passed += 1

    return _make_result(status, checks_run, n_passed, issues if issues else None)


class _SearchBudgetExceeded(Exception):
    """Internal: the bounded continuability search exceeded its node budget."""


def _continuable(
    oracle, word: str, alphabet: list[str],
    max_extra: int = 6, node_budget: int = 20_000,
) -> bool | None:
    """Whether some extension of `word` (up to `max_extra` more symbols) is
    in L, decided by an EXHAUSTIVE depth-first search over that bound (every
    node up to depth ``max_extra`` is visited unless the search is cut short
    by ``node_budget`` or an inconclusive oracle answer).

    Returns True (a continuation into L was found), False (the exhaustive
    search visited every extension up to the bound and found none — a
    decisive negative WITHIN this bound, per docs/VERDICT_POLICY.md §4), or
    None (the search could not be completed — either the oracle couldn't
    decide some word, or the node budget ran out — genuinely inconclusive,
    the caller must not treat this as evidence either way).
    """
    if not alphabet:
        return None
    budget = [node_budget]

    def dfs(w: str, depth: int) -> bool:
        budget[0] -= 1
        if budget[0] <= 0:
            raise _SearchBudgetExceeded()
        verdict = oracle(w)
        if verdict is None:
            raise _SearchBudgetExceeded()
        if verdict:
            return True
        if depth >= max_extra:
            return False
        return any(dfs(w + ch, depth + 1) for ch in alphabet)

    try:
        return dfs(word, 0)
    except _SearchBudgetExceeded:
        return None


_DEAD_CLASS_MAX_WORD_LEN = 8
_DEAD_CLASS_WORD_LIMIT = 5000
# _continuable's node budget for the dead-class search: with max_extra now
# scaling up to 2 * _DEAD_CLASS_MAX_WORD_LEN + 2 = 18, an exhaustive DFS
# proving a genuine "no continuation" (which must visit the whole bounded
# tree, unlike finding a witness, which can return early) needs real
# headroom -- well beyond _continuable's own 20_000 default -- so a
# longer/larger-alphabet word doesn't spuriously exceed budget (and get
# skipped as None, safe but less useful) on an otherwise-decidable word.
_DEAD_CLASS_NODE_BUDGET = 2_000_000


def _enumerate_words_up_to_length(
    alphabet: list[str], max_len: int = _DEAD_CLASS_MAX_WORD_LEN,
    limit: int = _DEAD_CLASS_WORD_LIMIT,
) -> list[str]:
    """Every word over ``alphabet`` of length 0..``max_len``, shortest
    first (breadth-first, deterministic order per ``alphabet``'s own
    order), capped at ``limit`` words total (VERDICT_POLICY.md §4
    dcfl/shallit: "перебрать все слова длины <= 8 ... ограничить 5000
    слов"). Empty ``alphabet`` -> ``[]``.
    """
    if not alphabet:
        return []
    words: list[str] = [""]
    frontier = [""]
    for _ in range(max_len):
        next_frontier: list[str] = []
        for w in frontier:
            for ch in alphabet:
                next_frontier.append(w + ch)
                if len(words) + len(next_frontier) >= limit:
                    words.extend(next_frontier)
                    return words[:limit]
        frontier = next_frontier
        words.extend(frontier)
    return words[:limit]


def _normalize_dead_class_status(raw: Any) -> Any:
    """Legacy 'finite' (pre docs/VERDICT_POLICY.md §4 trim, when the enum was
    {"empty", "finite", "infinite"}) is read as 'empty' for every downstream
    check: D is closed under right-extension (x ∈ D ⇒ xΣ* ⊆ D), so a
    nonempty D is always infinite -- "D конечен" could only ever have meant
    D = ∅ in practice, so 'finite' and 'empty' were never actually distinct
    claims. 'empty', 'infinite', and anything else (missing/invalid) pass
    through unchanged -- the caller's own validity check handles those.
    """
    return "empty" if raw == "finite" else raw


def _check_dead_class_finite(
    task_ir: dict, claimed_status: str | None,
) -> tuple[str | None, list[str] | None, list[str]]:
    """Step 2, dead-class part (VERDICT_POLICY.md §4 dcfl/shallit): cross-
    check the proof's own ``dead_class_status`` claim (``"empty"``, after
    :func:`_normalize_dead_class_status` maps the retired legacy value
    ``"finite"`` onto it) against the oracle. Runs ALWAYS whenever a membership
    oracle exists for this task, independent of the shape of
    ``distinguishing_suffix`` (the caller, ``_semantic_check_shallit_nerode``,
    no longer gates this on a literal suffix).

    Enumerates EVERY word of length <= 8 over the task alphabet (capped at
    5000 words, shortest first) and, for each, checks continuability into L
    via ``_continuable`` — an EXHAUSTIVE bounded search of up to
    ``max(6, 2 * len(w) + 2)`` more symbols (never fewer than 6, but growing
    with the word's own length), not a truncated heuristic, so a ``False``
    result is a genuine (if bounded) counterexample. The bound must scale
    with ``len(w)``: a fixed ``+6`` regardless of ``w``'s length is unsound
    once ``w`` itself is close to the 8-symbol enumeration cap. Two
    concrete counterexamples drove the ``2 * len(w) + 2`` choice (a plain
    ``len(w)`` is NOT always enough): (1) the generic "double the word to
    close it" witness that proves many languages' dead class empty (e.g.
    {ww^R}'s own `x -> x·x^R`, shallit.md's own worked example) needs
    exactly ``len(w)`` extra symbols — an 8-symbol word needs up to 8 more,
    not 6; a fixed ``+6`` bound falsely called such words dead ('aaaaaab'
    wrongly 'refuted' an 'empty' claim, needing the 7-symbol continuation
    'baaaaaa'); (2) `task_u1au2_u3au4`'s own language needs as much as
    ``len(w) + 2`` for an all-one-letter word (e.g. a run of b's has to
    wait for two fresh 'a's plus a padding block at least as long as the
    run itself) — strictly more than ``len(w)``, which is why the bound
    uses ``2 * len(w) + 2`` and not just ``len(w)``. On `task_grammar_aSSb`
    the old fixed bound also wrongly listed 'aaaaaaa'/'aaaaaaaa' (7/8 a's)
    as dead even though a⁷b⁷, a⁸b⁸ ∈ L (continuations of length 7/8).
    Words where the oracle can't decide some extension, or where the
    deeper search exceeds its node budget, are skipped entirely
    (``_continuable`` returns ``None``; R1: absence of evidence is not
    evidence) — never miscounted as dead.

    - ``claimed_status == "empty"``: ANY single dead word found ⇒
      contradicted. D is closed under right-extension: if x has no
      continuation into L, then neither does xy for any y (a continuation z
      of xy would make yz a continuation of x). So a nonempty D is always
      infinite (xΣ* ⊆ D for any x ∈ D) -- one confirmed dead word is enough,
      there is no theoretical basis for demanding dead words at
      multiple/every length first.
    - any other ``claimed_status`` (``"infinite"``, missing, invalid,
      including the legacy ``"finite"`` -- callers normalize that to
      ``"empty"`` via :func:`_normalize_dead_class_status` before calling
      this function): this function does nothing (those cases are handled
      structurally by ``_verify_shallit`` itself, or aren't a claim this
      check can test).

    Returns ``(outcome, evidence_words, issues)``:
    - ``(None, None, [])`` — no oracle / no alphabet / no contradiction
      found (genuinely inconclusive or consistent with the claim; the
      caller must NOT treat this as confirming the claim either).
    - ``("empty_contradicted", [word], [msg])`` (``claimed_status == "empty"``)
    """
    if claimed_status != "empty":
        return None, None, []
    alphabet = task_ir.get("alphabet", [])
    if not alphabet:
        return None, None, []
    oracle = build_membership_oracle_from_ir(task_ir)
    if oracle is None:
        return None, None, []

    for w in _enumerate_words_up_to_length(alphabet):
        max_extra = max(6, 2 * len(w) + 2)
        cont = _continuable(oracle, w, alphabet, max_extra=max_extra,
                             node_budget=_DEAD_CLASS_NODE_BUDGET)
        if cont is None or cont:
            continue
        # w is dead (provably no continuation into L within the bound) --
        # D is not empty.
        return "empty_contradicted", [w], [
            f"dead_class_status claims 'empty' but {w!r} has NO continuation "
            "into L within an exhaustive bounded search (up to "
            f"{max_extra} more symbols) -- the dead class D is not empty"
        ]
    return None, None, []


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

    The dead-class part is the one exception to "only literal suffixes":
    it runs ALWAYS whenever a membership oracle exists for this task,
    independent of the shape of ``distinguishing_suffix`` -- the proof's
    own ``dead_class_status`` claim (``"empty"``, or the legacy ``"finite"``
    normalized onto it) is checked against the oracle regardless of how the
    rest of the proof is phrased (VERDICT_POLICY.md §4 fix).
    """
    check_name = "semantic_nerode_separation[pairs]"
    alphabet = task_ir.get("alphabet", [])
    alphabet_set = set(alphabet)

    claimed_status = _normalize_dead_class_status(proof_sketch.get("dead_class_status"))
    dead_outcome, _dead_evidence, dead_issues = _check_dead_class_finite(
        task_ir, claimed_status,
    )
    if dead_outcome is not None:
        return "refuted", check_name + " + dead_class_status", "; ".join(dead_issues)

    distinguishing_suffix = proof_sketch.get("distinguishing_suffix")
    if not isinstance(distinguishing_suffix, str) or not distinguishing_suffix:
        return None, check_name, ""

    if any(ch not in alphabet_set for ch in distinguishing_suffix):
        # Contains variables/prose (e.g. "b a^N b u^R") — not a literal we
        # can instantiate mechanically.
        return None, check_name, ""

    oracle = build_membership_oracle_from_ir(task_ir)
    if oracle is None:
        return None, check_name, ""

    status: str | None = None
    issue = ""

    # Representative pairs supplied by the proof itself (its general
    # argument, instantiated at a concrete n) — the only thing that can
    # refute the claim. Structured `representative_pairs` field takes
    # priority; failing that, best-effort literal-word extraction from the
    # proof's own prose (distinguishing_suffix / separation_argument /
    # argument) supplies 3-5 concrete u != v pairs when the proof happens to
    # name concrete examples (VERDICT_POLICY.md §4: "брать u, v из
    # distinguishing_suffix/separation_argument, если там есть примеры").
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

    if not checked_pairs:
        prose = " ".join(
            str(proof_sketch.get(f, ""))
            for f in ("distinguishing_suffix", "separation_argument", "argument")
        )
        checked_pairs = _extract_literal_word_pairs(prose, alphabet_set, limit=5)

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
        # No usable representative pairs — fall back to short words that
        # are themselves continuable into L (real class representatives,
        # not arbitrary junk strings), and look for a supporting separating
        # pair ONLY (never to refute — a suffix failing to separate
        # unrelated random words says nothing about the proof's own claim).
        try:
            samples = sample_words(task_ir, count=10, max_len=12)
        except Exception:
            samples = []
        candidate_words = sorted(
            {s.get("word") for s in samples if isinstance(s.get("word"), str)},
            key=len,
        )
        candidate_words = [
            w for w in candidate_words
            if _continuable(oracle, w, alphabet) is not False
        ]
        in_bucket: list[str] = []
        out_bucket: list[str] = []
        for w in candidate_words:
            verdict_w = oracle(w + distinguishing_suffix)
            if verdict_w is None:
                continue
            (in_bucket if verdict_w else out_bucket).append(w)
        if in_bucket and out_bucket:
            n_pairs = min(5, len(in_bucket) * len(out_bucket))
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

    return status, check_name, issue


_LITERAL_WORD_RE_CACHE: dict[frozenset, re.Pattern] = {}


def _extract_literal_word_pairs(
    text: str, alphabet_set: set[str], limit: int = 5,
) -> list[tuple[str, str]]:
    """Best-effort extraction of concrete u != v literal words (runs of >= 2
    alphabet characters) from free-form proof prose, per VERDICT_POLICY.md
    §4 ("брать u, v из distinguishing_suffix/separation_argument, если там
    есть примеры"). Purely textual and safe: prose with no literal
    alphabet-only runs (the common case for genuinely general arguments)
    yields no pairs, leaving the caller's other fallbacks in charge.
    """
    if not text or not alphabet_set:
        return []
    key = frozenset(alphabet_set)
    pattern = _LITERAL_WORD_RE_CACHE.get(key)
    if pattern is None:
        chars = "".join(sorted(re.escape(ch) for ch in alphabet_set))
        pattern = re.compile(f"[{chars}]{{2,}}")
        _LITERAL_WORD_RE_CACHE[key] = pattern
    seen: list[str] = []
    for tok in pattern.findall(text):
        if tok not in seen:
            seen.append(tok)
    pairs: list[tuple[str, str]] = []
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            if seen[i] != seen[j]:
                pairs.append((seen[i], seen[j]))
            if len(pairs) >= limit:
                return pairs
    return pairs


def _semantic_check_shallit_prefix_continuation(
    proof_sketch: dict, task_ir: dict,
) -> tuple[str | None, str, str]:
    """Step 2 for shallit/prefix_continuation (VERDICT_POLICY.md §4):
    instantiate 5-10 concrete ``x$y`` words from ``derived_language`` (when
    it is pure exponent notation either side of a literal ``$``) and check
    x ∈ L, xy ∈ L with the task's own membership oracle — the necessary
    precondition for x$y to genuinely belong to L_$ = {x$y | x∈L, xy∈L}.

    This does not verify the deeper claim (that the derived language ∩ R is
    not context-free) — that stays an LLM/reasoning claim — only that the
    proof's own worked instances are not simply mis-described.
    """
    check_name = "semantic_prefix_continuation[x$y]"
    derived = proof_sketch.get("derived_language")
    if not isinstance(derived, str) or "$" not in derived:
        return None, check_name, ""

    alphabet = task_ir.get("alphabet", [])
    alphabet_set = set(alphabet)
    oracle = build_membership_oracle_from_ir(task_ir)
    if oracle is None:
        return None, check_name, ""

    left, _, right = derived.partition("$")
    left, right = left.strip(), right.strip()

    bad: list[str] = []
    checked = 0
    for n in range(1, 9):
        x = instantiate_exponent_pattern(left, n, alphabet_set)
        y = instantiate_exponent_pattern(right, n, alphabet_set)
        if x is None or y is None:
            # Not pure exponent notation on both sides (prose, "∩ R"
            # filters, etc.) — bail out entirely rather than guess.
            return None, check_name, ""
        in_x = oracle(x)
        in_xy = oracle(x + y)
        if in_x is None or in_xy is None:
            continue
        checked += 1
        if not (in_x and in_xy):
            bad.append(f"n={n}: x={x!r} in L={in_x}, xy={(x + y)!r} in L={in_xy}")
        if checked >= 8:
            break

    if bad:
        return "refuted", check_name, (
            "derived_language instance(s) fail x ∈ L, xy ∈ L: " + "; ".join(bad)
        )
    if checked == 0:
        return None, check_name, ""
    return "bounded_pass", check_name, ""


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
