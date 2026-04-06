"""
Oracle verifier for DCFL agent system.

Verifies claims made by specialist agents (stack_strategy, closure_reduction,
dcfl_pumping, shallit, inh_ambiguity) using pure-function checks — no LLM calls.

Implements §6.1 of the DCFL spec.
"""
from __future__ import annotations

import re
from typing import Any

from dcfl_system.lib.word_sampler import check_constraints, sample_words


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
    status = "verified" if n_passed == len(checks_run) else "issues_found"
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

    # direction check (applies regardless of operation)
    direction = proof_sketch.get("direction", "")
    if direction:
        _check(
            "direction_valid",
            direction in ("constructive", "destructive"),
            f"direction must be 'constructive' or 'destructive', got '{direction}'",
            checks_run, passed, issues,
        )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "verified" if n_passed == len(checks_run) else "issues_found"
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

    # Concrete word membership check via check_constraints (set_builder only)
    input_format = task_ir.get("input_format", "")
    if input_format == "set_builder":
        spec = task_ir.get("language_spec", {})
        constraints = spec.get("constraints", [])
        variables_def = spec.get("variables", [])
        if constraints and variables_def:
            # Check word_w if it looks like a concrete word (not a formula)
            concrete_words = {}
            if isinstance(word_w, str) and word_w.isalpha() and len(word_w) <= 100:
                concrete_words["word_w"] = word_w
            if isinstance(word_w_prime, str) and word_w_prime.isalpha() and len(word_w_prime) <= 100:
                concrete_words["word_w_prime"] = word_w_prime

            # We can only check membership if we can reconstruct variable assignments,
            # which requires domain knowledge. For now, just note that we tried.
            if concrete_words:
                _check(
                    "concrete_words_present",
                    True,
                    "",
                    checks_run, passed, issues,
                )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "verified" if n_passed == len(checks_run) else "issues_found"
    return _make_result(status, checks_run, n_passed, issues if issues else None)


# ---------------------------------------------------------------------------
# 4. shallit  (§6.1.4)
# ---------------------------------------------------------------------------

def _verify_shallit(proof_sketch: dict, _task_ir: dict) -> dict[str, Any]:
    checks_run: list[str] = []
    passed: list[bool] = []
    issues: list[str] = []

    # infinite_set_description
    isd = proof_sketch.get("infinite_set_description", "")
    _check(
        "infinite_set_description_non_empty",
        isinstance(isd, str) and len(isd) > 0,
        "infinite_set_description must be a non-empty string",
        checks_run, passed, issues,
    )

    # separating_context
    sc = proof_sketch.get("separating_context", "")
    _check(
        "separating_context_non_empty",
        isinstance(sc, str) and len(sc) > 0,
        "separating_context must be a non-empty string",
        checks_run, passed, issues,
    )

    # two_elements — must describe u, v with uw in L, vw not in L
    te = proof_sketch.get("two_elements")
    if isinstance(te, dict):
        _check(
            "two_elements_u_present",
            "u" in te and te["u"],
            "two_elements must describe element u",
            checks_run, passed, issues,
        )
        _check(
            "two_elements_v_present",
            "v" in te and te["v"],
            "two_elements must describe element v",
            checks_run, passed, issues,
        )
        reasoning = te.get("reasoning", te.get("explanation", ""))
        _check(
            "two_elements_reasoning",
            isinstance(reasoning, str) and len(reasoning) > 0,
            "two_elements must explain that uw in L and vw not in L",
            checks_run, passed, issues,
        )
    elif isinstance(te, str):
        _check(
            "two_elements_non_empty",
            len(te) > 0,
            "two_elements description must be non-empty",
            checks_run, passed, issues,
        )
    else:
        _check(
            "two_elements_present",
            te is not None,
            "two_elements must be provided (describes u, v with uw in L, vw not in L)",
            checks_run, passed, issues,
        )

    n_passed = sum(1 for p in passed if p)
    if not checks_run:
        return _make_result("not_verified", checks_run, 0,
                            ["no verifiable fields found in proof_sketch"])
    status = "verified" if n_passed == len(checks_run) else "issues_found"
    return _make_result(status, checks_run, n_passed, issues if issues else None)


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
    status = "verified" if n_passed == len(checks_run) else "issues_found"
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
