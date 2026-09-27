"""
Tests for docs/VERDICT_POLICY.md as implemented in dcfl_system.

Covers the trust taxonomy and confidence caps (§1-2), the step-2 semantic
oracle checks for dcfl_pumping / shallit-nerode_classes (§4), the verdict
gate rules R1-R4 and R7 (§3, orchestrator._apply_verdict_gate /
retry_logic.build_retry_plan), the result format addition (§5), and the
trust-label rendering rules (§5, renderer.py).

All mocks here are constructed inline (temporary), per the task's own
preference for "temporary mocks in the test" over files under
dcfl_system/examples/mock/.
"""
from __future__ import annotations

import pytest

from dcfl_system.lib import oracle_verifier as ov
from dcfl_system.lib.oracle_verifier import (
    verify_agent_results,
    trust_rank,
    trust_at_least,
    confidence_cap_for,
    CONTRADICTION_CONFIDENCE_CAP,
)
from dcfl_system.lib.retry_logic import build_retry_plan, MAX_RETRIES
from dcfl_system.orchestrator import (
    MAX_CALLS_PER_AGENT,
    _apply_verdict_gate,
    decide_after_retry_planner,
    run_specialist_node,
)
from dcfl_system.renderer import render_markdown, render_html, _trust_label


# ---------------------------------------------------------------------------
# Fixtures: a tiny set_builder language with a real membership oracle,
# L = {a^n b^n} via word_pattern "uv" (u in a*, v in b*, |u| == |v|).
# Trivial DCFL, but that's irrelevant here — only the step-2 PLUMBING
# (word instantiation + oracle membership) is under test, not the truth
# of any theorem about this toy language.
# ---------------------------------------------------------------------------

ANBN_TASK_IR = {
    "input_format": "set_builder",
    "alphabet": ["a", "b"],
    "language_spec": {
        "word_pattern": "uv",
        "variables": [
            {"name": "u", "domain": "a*"},
            {"name": "v", "domain": "b*"},
        ],
        "constraints": [
            {"kind": "length_cmp", "args": {"left": "u", "op": "==", "right": "v"}},
        ],
    },
}


# ---------------------------------------------------------------------------
# §1-2: trust taxonomy ordering and confidence caps
# ---------------------------------------------------------------------------

def test_trust_order_matches_policy_table():
    # refuted < not_verified < well_formed < bounded_pass < verified
    assert trust_rank("refuted") < trust_rank("not_verified")
    assert trust_rank("not_verified") < trust_rank("well_formed")
    assert trust_rank("well_formed") < trust_rank("bounded_pass")
    assert trust_rank("bounded_pass") < trust_rank("verified")


def test_trust_at_least_excludes_refuted_even_if_ranked_low():
    assert trust_at_least("refuted", "not_verified") is False
    assert trust_at_least("well_formed", "well_formed") is True
    assert trust_at_least("not_verified", "well_formed") is False


def test_confidence_caps_match_policy_table():
    assert confidence_cap_for("verified") == 0.98
    assert confidence_cap_for("bounded_pass") == 0.85
    assert confidence_cap_for("well_formed") == 0.60
    assert confidence_cap_for("not_verified") == 0.40
    assert confidence_cap_for(None) == 0.40  # unknown/self-assessment only
    assert CONTRADICTION_CONFIDENCE_CAP == 0.50


# ---------------------------------------------------------------------------
# §4 step 2: dcfl_pumping semantic oracle check
# ---------------------------------------------------------------------------

def _pumping_proof(word_w: str, word_w_prime: str) -> dict:
    return {
        "kind": "dcfl_pumping",
        "pumping_length": "p",
        "word_w": word_w,
        "word_w_prime": word_w_prime,
        "common_prefix_x": "aⁿ",
        "suffix_y": "bⁿ",
        "suffix_z": "z",
        "first_letters_match": "both 'b'",
        "condition1_argument": "argument 1",
        "condition2_argument": "argument 2",
    }


def test_dcfl_pumping_semantic_check_refuted_when_membership_holds_but_both_pumps_survive():
    # word_w = a^n b^n, word_w_prime = a^{n+1} b^{n+1}: both instantiate to
    # words that ARE in L = {a^n b^n} at n = p+1 for p in {2, 3} — membership
    # alone would look fine — but a^n b^n really IS DCFL, so a genuine
    # counterexample decomposition exists (e.g. pumping one 'a' together
    # with one 'b' from the tail of x synchronously into y and z keeps both
    # words balanced at i = 0, 2 AND 3): this concrete "non_dcfl via Yu's
    # lemma" proof is simply WRONG for this pair, and the brute-force
    # condition (1)/(2) search (VERDICT_POLICY.md §4) must catch it, not
    # just rubber-stamp membership.
    proof = _pumping_proof("aⁿbⁿ", "aⁿ⁺¹bⁿ⁺¹")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "refuted"
    assert entry["trust"] == "refuted"
    assert entry["issues"], "refuted result must carry the concrete counterexample"


def _anbn_union_anb2n_oracle(word: str) -> bool:
    """L = {a^n b^n} u {a^n b^2n | n >= 1} (docs/THEORY.md §1.1 worked example)."""
    if not word or any(ch not in ("a", "b") for ch in word):
        return False
    a_count = len(word) - len(word.lstrip("a"))
    rest = word[a_count:]
    if a_count == 0 or any(ch != "b" for ch in rest):
        return False
    b_count = len(rest)
    return b_count == a_count or b_count == 2 * a_count


def test_dcfl_pumping_semantic_check_bounded_pass_theory_worked_example(monkeypatch):
    # docs/THEORY.md §1.1 worked example: L = {a^n b^n} u {a^n b^2n}, n>=1.
    # x = a^n b^{n-1}, y = b, z = b^{n+1} (xy = a^n b^n, xz = a^n b^2n): a
    # genuine, hand-verified non-DCFL proof — both condition (1) and (2)
    # must close for every decomposition the brute force tries.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _anbn_union_anb2n_oracle,
    )
    proof = _pumping_proof("aⁿbⁿ", "aⁿb²ⁿ")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b"], "language_spec": {}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"
    assert "issues" not in entry


def test_dcfl_pumping_semantic_check_refuted_when_a_word_is_not_a_member():
    # word_w_prime = a^{n+1} b^n is NOT in L (unequal a/b counts) at either p.
    proof = _pumping_proof("aⁿbⁿ", "aⁿ⁺¹bⁿ")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "refuted"
    assert entry["trust"] == "refuted"
    assert entry["issues"], "refuted result must carry the concrete counterexample"
    assert "word_w_prime" in entry["issues"][0]


def test_dcfl_pumping_semantic_check_skipped_for_free_text_proof():
    # Realistic free-text proof prose (as in dcfl_system/examples/mock) is
    # NOT pure exponent notation — the step-2 check must not fire, and trust
    # stays at the structural-only well_formed tier (VERDICT_POLICY.md §4
    # fallback: "без оракула trust остаётся well_formed").
    proof = _pumping_proof(
        "w = xy = W1 = (ab)^n aa (ba)^n ∈ L",
        "w' = xz = W2 = (ab)^n aab (ab)^n aa (ba)^n baa (ba)^n ∈ L",
    )
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "well_formed"
    assert entry["trust"] == "well_formed"


def test_dcfl_pumping_semantic_check_skipped_without_set_builder_oracle():
    # Same clean exponent-notation words, but a grammar-format task_ir has no
    # set_builder oracle available -> stays well_formed, not upgraded.
    proof = _pumping_proof("aⁿbⁿ", "aⁿ⁺¹bⁿ⁺¹")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, {"input_format": "grammar", "alphabet": ["a", "b"]})
    assert result["dcfl_pumping"]["verification_status"] == "well_formed"


# ---------------------------------------------------------------------------
# §4 step 2: shallit / nerode_classes semantic oracle check
# ---------------------------------------------------------------------------

def _nerode_proof(distinguishing_suffix: str) -> dict:
    return {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "empty",
        "distinguishing_suffix": distinguishing_suffix,
        "separation_argument": "uw in L, vw not in L",
        "argument": "by contrapositive of Theorem 4.7.4",
    }


def _bypass_dead_class_check(monkeypatch):
    """These tests are about the REPRESENTATIVE-PAIR separation logic only
    (root docstring: "only the step-2 PLUMBING ... is under test, not the
    truth of any theorem about this toy language") -- ANBN_TASK_IR's REAL
    dead class is actually infinite (e.g. 'ba' never continues into
    a^n b^n), which the mandatory oracle-based dead_class_status check
    (VERDICT_POLICY.md §4) would otherwise (correctly) refute regardless of
    the pair-separation outcome under test here. That check has its own
    dedicated tests below; bypass it here so it doesn't shadow the
    pair-separation behavior these tests target."""
    monkeypatch.setattr(ov, "_check_dead_class_finite", lambda *a, **k: (None, None, []))


def test_shallit_nerode_semantic_check_bounded_pass(monkeypatch):
    # 'ab' (+'b' -> 'abb', not in L) and 'aab' (+'b' -> 'aabb', in L):
    # a real, oracle-checked separation on two concrete words.
    _bypass_dead_class_check(monkeypatch)
    monkeypatch.setattr(
        ov, "sample_words",
        lambda ir, count=10, max_len=12: [
            {"word": "ab", "in_language": True},
            {"word": "aab", "in_language": False},
        ],
    )
    proof = _nerode_proof("b")
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"


def test_shallit_nerode_semantic_check_not_refuted_by_unrelated_random_words(monkeypatch):
    # docs/VERDICT_POLICY.md §4 fix: the Shallit/Nerode claim is about
    # REPRESENTATIVES of the classes named in the proof's own general
    # argument, not arbitrary random strings. 'ab' and 'aabb' are both
    # already balanced, so +'b' breaks both the same way on these two
    # UNRELATED samples -- that is not evidence against the proof (it does
    # not supply representative_pairs of its own here), so trust must stay
    # at well_formed rather than being falsely "refuted" (was the bug this
    # test used to lock in; see VERDICT_POLICY.md §4 and the reviewer note
    # on oracle_verifier.py in the verdict-gate branch).
    _bypass_dead_class_check(monkeypatch)
    monkeypatch.setattr(
        ov, "sample_words",
        lambda ir, count=10, max_len=12: [
            {"word": "ab", "in_language": True},
            {"word": "aabb", "in_language": True},
        ],
    )
    proof = _nerode_proof("b")
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "well_formed"
    assert entry["trust"] == "well_formed"


def test_shallit_nerode_semantic_check_refuted_by_own_representative_pairs(monkeypatch):
    # When the proof DOES supply concrete representative_pairs (its general
    # argument instantiated at a chosen n), a suffix that fails to separate
    # THAT pair is a genuine refutation (docs/VERDICT_POLICY.md §4).
    _bypass_dead_class_check(monkeypatch)
    proof = _nerode_proof("b")
    proof["representative_pairs"] = [{"u": "ab", "v": "aabb"}]
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "refuted"
    assert entry["issues"]
    assert "representative pair" in entry["issues"][0]


def test_shallit_nerode_semantic_check_skipped_for_parametric_suffix(monkeypatch):
    # Realistic prose (as in dcfl_system/examples/mock) — contains symbols
    # outside the alphabet (letters, spaces, '^') -- must not be
    # mechanically instantiated; trust stays well_formed.
    _bypass_dead_class_check(monkeypatch)
    proof = _nerode_proof("w = b a^N b u^R")
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    assert result["shallit"]["verification_status"] == "well_formed"


def test_shallit_nerode_semantic_check_refuted_when_dead_class_finite_is_false(monkeypatch):
    # The proof CLAIMS dead_class_status "empty", but for a^n b^n the dead
    # class is actually infinite ('b', 'ba' and every word outside a*b* can
    # never be continued into L) -- the mandatory, exhaustive dead-class
    # search (VERDICT_POLICY.md §4) must catch this and refute the whole
    # nerode_classes proof, even though the separating pair it also supplies
    # is perfectly real. Unlike the plumbing tests above, this one does NOT
    # bypass the dead-class check -- it is the dedicated test for it.
    proof = _nerode_proof("b")  # dead_class_status: "empty" (FALSE for a^n b^n)
    proof["representative_pairs"] = [{"u": "ab", "v": "aab"}]
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, ANBN_TASK_IR)
    entry = result["shallit"]
    assert entry["verification_status"] == "refuted"
    assert entry["trust"] == "refuted"
    assert "dead_class_status" in entry["issues"][0]
    # the shortest dead word the exhaustive enumeration hits first (a bare
    # 'b' can never gain a matching 'a' before it) -- not necessarily 'ba'.
    assert "'b'" in entry["issues"][0]


# ---------------------------------------------------------------------------
# §4 step 2: shallit / prefix_continuation semantic oracle check (x$y)
# ---------------------------------------------------------------------------

def _prefix_continuation_proof(derived_language: str) -> dict:
    return {
        "kind": "shallit",
        "technique": "prefix_continuation",
        "derived_language": derived_language,
        "regular_filter": "a*b*$b+",
        "non_cfl_argument": "pumping on the derived language",
        "argument": "L_$ ∩ a*b*$b+ is not context-free ⇒ L not DCFL",
    }


def test_shallit_prefix_continuation_bounded_pass(monkeypatch):
    # docs/THEORY.md §1.1 example: L = {a^n b^n} u {a^n b^2n}.
    # L_$ ∩ a*b*$b+ = {a^n b^n $ b^n} -- x = a^n b^n (in L, branch 1),
    # xy = a^n b^2n (in L, branch 2): the necessary precondition for this
    # to be a real L_$ instance holds for every sampled n.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _anbn_union_anb2n_oracle,
    )
    proof = _prefix_continuation_proof("aⁿbⁿ$bⁿ")
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b"], "language_spec": {}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["shallit"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"


def test_shallit_prefix_continuation_refuted_when_continuation_word_is_wrong(monkeypatch):
    # Same language, but the proof's own worked instance is wrong: xy =
    # a^n b^n a^n is not of the shape a^m b^m or a^m b^2m for any m, so it
    # is never in L -- a factual error in the proof's derived_language,
    # caught mechanically rather than trusted at face value.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _anbn_union_anb2n_oracle,
    )
    proof = _prefix_continuation_proof("aⁿbⁿ$aⁿ")
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b"], "language_spec": {}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["shallit"]
    assert entry["verification_status"] == "refuted"
    assert entry["issues"]


# ---------------------------------------------------------------------------
# §4 step 2 end-to-end: exam_01 / exam_02 / exam_03-style languages.
#
# These reconstruct the mathematical content behind
# dcfl_system/examples/mock/dcfl_exam_0{1,2,3}_*.json (whose prose uses
# grouped exponent notation like "(ab)^n" that instantiate_exponent_pattern
# does not parse) as directly oracle-checkable fixtures, so the genuine
# proofs get bounded_pass and deliberately-wrong variants get refuted.
# ---------------------------------------------------------------------------

def test_dcfl_pumping_exam01_style_reversal_language_bounded_pass():
    # exam_01: L = {x aa x^R | x in (a+b)*ab(ab|aa)*} (THEORY.md §1.6-ish
    # palindrome-with-marker language). x = (ab)^n, W1 = xy = x aa x^R.
    # Real DSL oracle: word_pattern "HaaH^R" with H's domain restricted to
    # the same regular set as the proof's X.
    task_ir = {
        "input_format": "set_builder",
        "alphabet": ["a", "b"],
        "language_spec": {
            "word_pattern": "HaaH^R",
            "variables": [{"name": "H", "domain": "(a+b)*ab(ab|aa)*"}],
            "constraints": [],
        },
    }
    n = 4
    h = "ab" * n
    w1 = h + "aa" + h[::-1]
    x_part = ("ab" * n) + "aa" + ("ba" * (n - 1)) + "b"
    z_part = "a" + "b" + "aa" + ("ba" * n) + "baa" + ("ba" * n)
    w2 = x_part + z_part
    proof = _pumping_proof(w1, w2)
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"


def _in_exam03_language(word: str) -> bool:
    """L = {a^n b^m c^n a c^l} u {a^n b^{m+n} a c^l}, n>=1, m,l>=0
    (docs/THEORY.md §1.8 / dcfl_system/tests/test_theory_contract.py)."""
    i = 0
    n = 0
    while i < len(word) and word[i] == "a":
        i += 1
        n += 1
    if n == 0:
        return False
    a_end = i
    j = i
    while j < len(word) and word[j] == "b":
        j += 1
    m_total = j - a_end
    k = j
    c1 = 0
    while k < len(word) and word[k] == "c":
        k += 1
        c1 += 1
    if c1 == n and k < len(word) and word[k] == "a" and all(ch == "c" for ch in word[k + 1:]):
        return True
    if m_total >= n and j < len(word) and word[j] == "a" and all(ch == "c" for ch in word[j + 1:]):
        return True
    return False


def test_dcfl_pumping_exam03_style_two_branch_language_bounded_pass(monkeypatch):
    # exam_03: word_w = a^n b^n c^n a (branch c^n), word_w_prime = a^n b^n a
    # (branch b^n, m=0) -- the fixed §1.8 proof from dcfl_pumping.md.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _in_exam03_language,
    )
    proof = _pumping_proof("aⁿbⁿcⁿa", "aⁿbⁿa")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b", "c"], "language_spec": {}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"


def test_dcfl_pumping_exam03_style_two_branch_language_refuted_for_wrong_pair(monkeypatch):
    # Both words are still genuinely in L (branch b^n with m=0 vs m=1), but
    # this particular pair is a BAD choice for the pumping argument: a
    # decomposition survives pumping at i=0,2,3 (the extra 'b' in
    # word_w_prime lines up with a pumpable window in the shared a/b
    # prefix), so this concrete proof is wrong even though the underlying
    # theorem (L not DCFL) is true.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _in_exam03_language,
    )
    proof = _pumping_proof("aⁿbⁿa", "aⁿbⁿ⁺¹a")
    agent_results = {"dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b", "c"], "language_spec": {}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "refuted"
    assert entry["issues"]


def _in_exam02_style_language(word: str) -> bool:
    """L2 = {u3 a u4 | |u3| >= |u4|}, read via the word's LAST 'a' (so u4 is
    the trailing run of b's, u3 = v as in the exam_02 mock's "v a b^r"
    shape) -- dcfl_system/examples/mock/dcfl_exam_02_shallit.json."""
    if "a" not in word:
        return False
    last_a = word.rfind("a")
    tail = word[last_a + 1:]
    if any(ch != "b" for ch in tail):
        return False
    return last_a >= len(tail)


def test_shallit_nerode_exam02_style_language_bounded_pass(monkeypatch):
    # exam_02: dead class D is empty (appending one more 'a' to ANY word
    # always lands in L2, since the new 'a' is trivially the last one with
    # an empty b-tail); u='a', v='aa' are separated by suffix 'b'
    # (u+'b'='ab' not in L2, v+'b'='aab' in L2) — a real class separation.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _in_exam02_style_language,
    )
    monkeypatch.setattr(
        ov, "sample_words",
        lambda ir, count=10, max_len=30: [
            {"word": w, "in_language": _in_exam02_style_language(w)}
            for w in ("", "a", "b", "aa", "ab", "ba", "bb")
            if len(w) <= max_len
        ],
    )
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b"], "language_spec": {}}
    # dead_class_status: "empty" (via _nerode_proof) is TRUE here -- any
    # word extends into L2 by appending one more 'a' (v a b^r -> v a b^r a)
    # -- so the mandatory oracle-based dead-class check finds no
    # contradiction and this exercises the (unbypassed) representative-pair
    # logic for real.
    proof = _nerode_proof("b")
    proof["representative_pairs"] = [{"u": "a", "v": "aa"}]
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["shallit"]
    assert entry["verification_status"] == "bounded_pass"
    assert entry["trust"] == "bounded_pass"


def test_shallit_nerode_exam02_style_language_refuted_for_wrong_separating_pair(monkeypatch):
    # u='a', v='ab' with suffix 'b': u+'b'='ab' not in L2, v+'b'='abb' is
    # ALSO not in L2 -- 'b' does not separate this pair, so the proof's own
    # representative_pairs claim is refuted.
    monkeypatch.setattr(
        ov, "build_membership_oracle_from_ir",
        lambda ir, max_word_len=60: _in_exam02_style_language,
    )
    task_ir = {"input_format": "set_builder", "alphabet": ["a", "b"], "language_spec": {}}
    proof = _nerode_proof("b")
    proof["representative_pairs"] = [{"u": "a", "v": "ab"}]
    agent_results = {"shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof}}
    result = verify_agent_results(agent_results, task_ir)
    entry = result["shallit"]
    assert entry["verification_status"] == "refuted"
    assert entry["issues"]


# ---------------------------------------------------------------------------
# §3 verdict gate: R1 -- constructive/destructive failure alone is not
# evidence for the opposite (or even the same-direction) verdict.
# ---------------------------------------------------------------------------

def test_r1_no_successful_artifact_forces_inconclusive():
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.92,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        # stack_strategy failed to construct -- a constructive FAILURE, not
        # destructive evidence (this is the task_grammar_filter_49 pattern).
        "stack_strategy": {"status": "fail", "verdict": None, "confidence": 0.3},
        "dcfl_pumping": {"status": "fail", "verdict": None, "confidence": 0.2},
        "shallit": {"status": "not_applicable", "verdict": None},
        "inh_ambiguity": {"status": "not_applicable", "verdict": None},
        "closure_reduction": {"status": "not_applicable", "verdict": None},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "not_verified"},
        "dcfl_pumping": {"trust": "not_verified"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    assert gated["verdict"] is None  # -> "inconclusive" once assembled
    assert gated["confidence"] <= 0.40
    assert gated["verdict_gate"]["downgrades"], "must record why it was downgraded"
    assert any("constructive_failure_only" in d for d in gated["verdict_gate"]["downgrades"])


# ---------------------------------------------------------------------------
# §3 verdict gate: R2 -- well_formed-only evidence is admissible but capped
# at 0.60, not stuck at some arbitrary lower number and not silently
# allowed to keep the LLM's 0.9.
# ---------------------------------------------------------------------------

def test_r2_well_formed_only_evidence_caps_confidence_at_060():
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {"dcfl_pumping": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] == pytest.approx(0.60)
    assert gated["verdict_gate"]["basis"] == [{"agent": "dcfl_pumping", "trust": "well_formed"}]
    assert gated["verdict_gate"]["contradiction"] is False


def test_r2_constructive_dcfl_verdict_requires_bounded_pass_not_well_formed():
    # docs/VERDICT_POLICY.md R2 fix (reviewer finding): unlike the destructive
    # direction (well_formed is enough per R1), a CONSTRUCTIVE "dcfl" verdict
    # needs trust >= bounded_pass. stack_strategy at well_formed alone (no
    # competing destructive evidence, so R3 does not rescue it either) must
    # downgrade to inconclusive <= 0.40, not stand as "dcfl <= 0.60".
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
    }
    oracle_verification = {"stack_strategy": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.40
    assert gated["verdict_gate"]["downgrades"]


def test_bounded_pass_evidence_caps_confidence_at_085_not_060():
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.99,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {"dcfl_pumping": {"trust": "bounded_pass"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# §3 verdict gate: R2/R7 -- a refuted primary artifact forbids the verdict
# this round; retried if budget remains, else terminal inconclusive.
# ---------------------------------------------------------------------------

def test_refuted_primary_artifact_retries_when_budget_remains():
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {"dcfl_pumping": {"trust": "refuted", "issues": ["word_w not in L"]}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    assert gated["action"] == "retry"
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.25


def test_refuted_primary_artifact_terminal_inconclusive_when_budget_exhausted():
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {"dcfl_pumping": {"trust": "refuted"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["action"] == "done"
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.40


# ---------------------------------------------------------------------------
# Reviewer finding (backlog round C2, not yet fixed): the "no {required_trust}+
# artifact" branch (primary ran successfully but its trust is below what R2
# requires -- NOT refuted) used to call _r4prime_rescue unconditionally,
# logging "retry budget exhausted" even when retry_count < max_retries and
# skipping a retry the refuted-artifact branch right above it would have
# taken. It must retry while budget remains, exactly like the refuted branch,
# and only rescue once the budget is actually gone.
# ---------------------------------------------------------------------------

def test_no_required_trust_artifact_retries_when_budget_remains():
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {"kind": "stack_strategy"}},
    }
    # well_formed is below required_trust=bounded_pass for a constructive
    # "dcfl" verdict (R2) -- but it is NOT refuted, so this must retry, not
    # go straight to R4' rescue.
    oracle_verification = {"stack_strategy": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    assert gated["action"] == "retry"
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.25
    assert not any("retry budget exhausted" in d for d in gated["verdict_gate"]["downgrades"])


def test_no_required_trust_artifact_rescues_when_budget_exhausted():
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {"kind": "stack_strategy"}},
    }
    oracle_verification = {"stack_strategy": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["action"] == "done"
    # well_formed doesn't meet the bounded_pass R2 needs for "dcfl", and
    # there's no destructive evidence either -> R4' finds nothing admissible.
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.40
    assert any("retry budget exhausted" in d for d in gated["verdict_gate"]["downgrades"])


# ---------------------------------------------------------------------------
# §3 R3 (post-R3' revision, reviewer finding fix) -- a contradiction now
# requires the CONSTRUCTIVE side to be >= bounded_pass AND the destructive
# side >= well_formed; it is NOT symmetric. A merely well_formed
# stack_strategy does not block a genuine non_dcfl backed by a bounded_pass+
# destructive proof -- a well_formed constructive claim already fails R2 on
# its own and was never going to carry a "dcfl" verdict either way, so
# flagging a contradiction there only threw away real evidence sitting on
# the destructive side. A GENUINE contradiction (constructive >= bounded_pass
# AND destructive >= well_formed) still falls back to inconclusive unless one
# side is `verified` (docs/VERDICT_POLICY.md R3 fix, precedent: wwvvR on
# Haiku 2026-09-27 in cfl_system) -- DCFL has no executable artifact to run
# R3' cross-check against yet (dcfl_system/CLAUDE.md "Formal verification NOT
# implemented"), so an unresolved contradiction with neither side `verified`
# always falls back to inconclusive, regardless of which side has higher rank.
# ---------------------------------------------------------------------------

def test_well_formed_constructive_does_not_block_non_dcfl():
    """A well_formed stack_strategy sitting next to a bounded_pass
    dcfl_pumping is NOT a contradiction — the constructive side never clears
    the bounded_pass threshold R3 now requires, so a correctly-proposed
    non_dcfl resolves cleanly instead of being nulled out to 'inconclusive'."""
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "well_formed"},
        "dcfl_pumping": {"trust": "bounded_pass"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict_gate"]["contradiction"] is False
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] <= 0.85


def test_well_formed_constructive_alone_still_fails_r2_for_dcfl():
    """A well_formed stack_strategy is still not enough, on its own, to carry
    a "dcfl" verdict (R2) — this is R1/R2, not R3: no contradiction is even
    considered because "dcfl" itself is unearned (stack_strategy never
    clears bounded_pass). But retries are exhausted and a bounded_pass
    dcfl_pumping IS sitting on the destructive side — docs/VERDICT_POLICY.md
    R4' now picks that strongest admissible basis instead of defaulting to
    inconclusive: non_dcfl, capped at bounded_pass's own 0.85 ceiling."""
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "well_formed"},
        "dcfl_pumping": {"trust": "bounded_pass"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict_gate"]["contradiction"] is False
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] <= 0.85
    assert any(
        "strongest admissible basis" in d and "non_dcfl" in d
        for d in gated["verdict_gate"]["downgrades"]
    )


def test_r4prime_no_admissible_basis_either_side_stays_inconclusive_not_failure():
    """docs/VERDICT_POLICY.md R4' — retries exhausted, reasoning's proposal
    unearned, and NEITHER side has any admissible evidence at all -> plain
    inconclusive (verdict None, confidence <= 0.40), never `failure`
    (precedent: cfl-07/cfl-12 eval live-run ending in `failure 0.0`)."""
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
    }
    oracle_verification = {"stack_strategy": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.40
    assert any("strongest admissible basis: inconclusive" in d for d in gated["verdict_gate"]["downgrades"])


def test_r3_genuine_contradiction_stays_inconclusive_when_neither_side_verified():
    """A GENUINE contradiction: the constructive side now actually clears
    bounded_pass (not just well_formed) while the destructive side is only
    well_formed. Neither side is `verified`, so it stays inconclusive."""
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "bounded_pass"},
        "dcfl_pumping": {"trust": "well_formed"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict_gate"]["contradiction"] is True
    assert gated["confidence"] <= 0.50
    assert gated["verdict"] is None


def test_r3_contradiction_verified_side_wins_capped_at_085():
    # A hypothetical `verified` destructive claim (DCFL has no exhaustive
    # checker yet, but the gate logic must still honor `verified` correctly
    # whenever one eventually exists) still wins a genuine unresolved
    # contradiction (constructive >= bounded_pass here too), but capped at
    # 0.85, not the normal 0.98 `verified` ceiling.
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.99,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "bounded_pass"},
        "dcfl_pumping": {"trust": "verified"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["verdict_gate"]["contradiction"] is True
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] == 0.85


# ---------------------------------------------------------------------------
# §3 R4': retry budget exhausted, reasoning's own proposal has no artifact at
# all (not just insufficient trust) -> the well_formed destructive evidence
# still on record rescues the round instead of `failure`/inconclusive.
# Precedent: live-run eval 2026-09-27, cfl-07/cfl-12 ended `failure 0.0`
# despite a well_formed destructive proof on record.
# ---------------------------------------------------------------------------

def test_r4prime_destructive_well_formed_rescues_unsupported_constructive_proposal():
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
    }
    oracle_verification = {"dcfl_pumping": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["action"] == "done"
    assert gated["verdict"] == "non_dcfl"
    assert gated["confidence"] <= 0.60
    assert any("strongest admissible basis" in d for d in gated["verdict_gate"]["downgrades"])


# ---------------------------------------------------------------------------
# §3 R4' x R3 (reviewer finding fix) — _r4prime_rescue must not violate R3.
# The contradiction check earlier in _apply_verdict_gate only ever compares
# reasoning's own `primary_evidence` agent against the best OPPOSING
# evidence; when `primary` itself is refuted (scenario A) or simply weaker
# than another agent on its own side (scenario B), that check's "own side"
# trust is not the strongest evidence actually on the table, so a real
# bounded_pass-vs-well_formed contradiction can slip past it undetected and
# reach _r4prime_rescue, which used to just grab the destructive (or
# constructive) side without re-checking R3 at all. Both scenarios below
# are taken verbatim from the reviewer's repro.
# ---------------------------------------------------------------------------

def test_r4prime_rescue_scenario_a_does_not_bypass_r3():
    """Scenario A: reasoning proposes done/non_dcfl on dcfl_pumping (refuted
    by the oracle); shallit (well_formed) is the only OTHER destructive
    evidence, but stack_strategy sits on the constructive side at
    bounded_pass (now reachable via the DPDA certificate). Retries are
    exhausted. The old code let dcfl_pumping's refutation fall through to
    _r4prime_rescue, which then picked shallit as the "strongest admissible
    destructive basis" and returned non_dcfl at 0.60 with
    contradiction=False -- but stack_strategy (bounded_pass, constructive)
    vs shallit (well_formed, destructive) IS a genuine, unresolved R3
    contradiction (neither side `verified`) and must stay inconclusive at
    confidence <= 0.50."""
    reasoning = {"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                 "primary_evidence": "dcfl_pumping"}
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "dcfl_pumping"}},
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "shallit"}},
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
    }
    oracle_verification = {
        "dcfl_pumping": {"trust": "refuted"},
        "shallit": {"trust": "well_formed"},
        "stack_strategy": {"trust": "bounded_pass"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["action"] == "done"
    assert gated["verdict_gate"]["contradiction"] is True
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.50


def test_r4prime_rescue_scenario_b_does_not_bypass_r3():
    """Scenario B: reasoning proposes done/dcfl on stack_strategy
    (well_formed only -- fails R2 on its own), closure_reduction argues the
    constructive side too and clears bounded_pass, while shallit sits on the
    destructive side at well_formed. Retries are exhausted. The old code let
    _r4prime_rescue search destructive-first and return non_dcfl at 0.60 via
    shallit, with contradiction=False -- but closure_reduction (bounded_pass,
    constructive) vs shallit (well_formed, destructive) is the same genuine
    R3 contradiction as scenario A and must stay inconclusive at
    confidence <= 0.50, not resolve to either side."""
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.9,
                 "primary_evidence": "stack_strategy"}
    agent_results = {
        "stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}},
        "closure_reduction": {
            "status": "success", "verdict": "dcfl",
            "proof_sketch": {"kind": "closure_reduction", "direction": "constructive"},
        },
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": {"kind": "shallit"}},
    }
    oracle_verification = {
        "stack_strategy": {"trust": "well_formed"},
        "closure_reduction": {"trust": "bounded_pass"},
        "shallit": {"trust": "well_formed"},
    }
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=MAX_RETRIES, max_retries=MAX_RETRIES)
    assert gated["action"] == "done"
    assert gated["verdict_gate"]["contradiction"] is True
    assert gated["verdict"] is None
    assert gated["confidence"] <= 0.50


# ---------------------------------------------------------------------------
# §3 R7: retry_logic reads `trust`, not a nonexistent
# verification_status == "refuted"; a refuted agent is retried unconditionally
# with the oracle's counterexample passed as a hint.
# ---------------------------------------------------------------------------

def test_retry_logic_retries_refuted_agent_even_with_high_confidence_success():
    agent_results = {
        "dcfl_pumping": {"status": "success", "verdict": "non_dcfl", "confidence": 0.95},
    }
    oracle_verification = {
        "dcfl_pumping": {
            "trust": "refuted",
            "issues": ["word_w_prime='aaaabbb' not in L"],
        },
    }
    plan = build_retry_plan(agent_results, oracle_verification, retry_count=0)
    assert plan["needs_retry"] is True
    assert "dcfl_pumping" in plan["agents_to_retry"]
    assert "aaaabbb" in plan["hints"]["dcfl_pumping"]
    assert "REFUTED" in plan["hints"]["dcfl_pumping"]


def test_retry_logic_does_not_retry_not_applicable_even_if_marked_refuted():
    # not_applicable is checked first -- an agent that didn't run has
    # nothing for the oracle to refute in practice, but the ordering itself
    # (not_applicable short-circuits before the refuted check) is what's
    # under test here for robustness.
    agent_results = {"shallit": {"status": "not_applicable", "verdict": None}}
    oracle_verification = {"shallit": {"trust": "refuted"}}
    plan = build_retry_plan(agent_results, oracle_verification, retry_count=0)
    assert "shallit" not in plan["agents_to_retry"]


# ---------------------------------------------------------------------------
# §3 R7 / orchestrator: an empty retry plan is terminal, not a silent
# full re-dispatch of all 5 specialists.
# ---------------------------------------------------------------------------

def test_empty_retry_plan_is_terminal():
    state = {"retry_plan": {"needs_retry": False, "agents_to_retry": []}}
    assert decide_after_retry_planner(state) == "terminal"


def test_nonempty_retry_plan_loops_back_to_dispatch():
    state = {"retry_plan": {"needs_retry": True, "agents_to_retry": ["dcfl_pumping"]}}
    assert decide_after_retry_planner(state) == "retry"


def test_terminal_path_via_empty_plan_still_applies_verdict_gate():
    # docs/VERDICT_POLICY.md R1/R2 hole (reviewer finding, dcfl_system/
    # orchestrator.py): the LLM proposed action="retry", verdict="non_dcfl",
    # confidence=0.92, but every specialist is not_applicable/failed with no
    # destructive artifact at all -- build_retry_plan legitimately returns an
    # EMPTY plan (nothing left worth retrying), so decide_after_retry_planner
    # sends this straight to renderer_node without ever going through the
    # action=="done" branch of _apply_verdict_gate. renderer_node must
    # re-gate on this terminal path -- a destructive verdict with zero
    # evidence must not survive at confidence 0.92.
    from dcfl_system.orchestrator import renderer_node

    state = {
        "task_ir": {"task_type": "classify_and_prove_dcfl", "source_text": "test"},
        "reasoning": {
            "action": "retry", "decision": "retry", "verdict": "non_dcfl",
            "confidence": 0.92, "primary_evidence": "dcfl_pumping",
            "summary": "LLM thinks non_dcfl but plan came back empty",
        },
        "agent_results": {
            "stack_strategy": {"status": "fail", "verdict": None, "confidence": 0.3},
            "dcfl_pumping": {"status": "not_applicable", "verdict": None},
            "shallit": {"status": "not_applicable", "verdict": None},
            "inh_ambiguity": {"status": "not_applicable", "verdict": None},
            "closure_reduction": {"status": "not_applicable", "verdict": None},
        },
        "oracle_verification": {},
        "retry_count": 1,
        "errors": [],
    }
    out = renderer_node(state)
    result = out["result"]
    assert result["verdict"] != "non_dcfl", (
        "destructive verdict rendered with zero evidence behind it (R1 hole)"
    )
    assert result["confidence"] <= 0.40
    assert result["verdict_gate"]["downgrades"]


# ---------------------------------------------------------------------------
# §5 result format: verdict_gate is additive and always present in the
# gated reasoning output.
# ---------------------------------------------------------------------------

def test_verdict_gate_block_has_the_documented_shape():
    reasoning = {"action": "done", "verdict": "dcfl", "confidence": 0.5,
                 "primary_evidence": "stack_strategy"}
    agent_results = {"stack_strategy": {"status": "success", "verdict": "dcfl", "proof_sketch": {}}}
    oracle_verification = {"stack_strategy": {"trust": "well_formed"}}
    gated = _apply_verdict_gate(reasoning, agent_results, oracle_verification,
                                 retry_count=0, max_retries=MAX_RETRIES)
    gate = gated["verdict_gate"]
    assert set(gate.keys()) == {"basis", "contradiction", "downgrades", "confidence_cap"}
    assert isinstance(gate["basis"], list)
    assert isinstance(gate["downgrades"], list)
    assert isinstance(gate["contradiction"], bool)


# ---------------------------------------------------------------------------
# §5 renderer: trust labels in words, green "verified" banner reserved
# for full verification, and new-contract proof_sketch fields still render.
# ---------------------------------------------------------------------------

def test_trust_labels_are_russian_words_not_raw_status_strings():
    assert _trust_label("well_formed") == "корректно оформлено"
    assert _trust_label("bounded_pass") == "проверено выборочно"
    assert _trust_label("verified") == "проверено полностью"
    assert _trust_label("refuted") == "опровергнуто"


def _minimal_result(oracle_entry: dict, verdict: str = "non_dcfl", proof_method: str = "dcfl_pumping") -> dict:
    agent_name = proof_method
    proof_sketch = (
        {
            "kind": "dcfl_pumping",
            "condition1_argument": "cond1",
            "condition2_argument": "cond2",
        }
        if proof_method == "dcfl_pumping" else
        {
            "kind": "shallit",
            "technique": "nerode_classes",
            "dead_class_status": "empty",
            "derived_language": "L2 = {...}",
        }
    )
    return {
        "task": "classify_and_prove_dcfl",
        "source_text": "test",
        "verdict": verdict,
        "confidence": 0.6,
        "proof_method": proof_method,
        "proof_sketch": proof_sketch,
        "proof_text": None,
        "oracle_verification": {agent_name: oracle_entry},
        "agents_used": [agent_name],
        "specialist_outputs": {
            agent_name: {"status": "success", "verdict": verdict, "proof_sketch": proof_sketch},
        },
        "agent_results": {},
        "hints_for_human": [],
        "errors": [],
        "retries": 0,
    }


def _uses_banner_class(html: str, css_class: str) -> bool:
    """Whether *html* has an element actually carrying *css_class*, not just
    the CSS rule definition (which is always present in <style>)."""
    return f'class="{css_class}"' in html


def test_html_green_banner_only_for_verified_trust():
    html_verified = render_html(_minimal_result({"trust": "verified", "checks_passed": 1, "checks_total": 1}))
    assert _uses_banner_class(html_verified, "s-banner-ok")

    html_bounded = render_html(_minimal_result({"trust": "bounded_pass", "checks_passed": 1, "checks_total": 1}))
    assert not _uses_banner_class(html_bounded, "s-banner-ok")
    assert _uses_banner_class(html_bounded, "s-banner-info")

    html_well_formed = render_html(_minimal_result({"trust": "well_formed", "checks_passed": 1, "checks_total": 1}))
    assert not _uses_banner_class(html_well_formed, "s-banner-ok")
    assert not _uses_banner_class(html_well_formed, "s-banner-info")
    assert _uses_banner_class(html_well_formed, "s-banner-warn")


def test_html_shows_refuted_warning_banner():
    html = render_html(_minimal_result({"trust": "refuted", "issues": ["word_w not in L"]}, verdict="inconclusive"))
    assert "опровергнуто" in html or "контрпример" in html


def test_markdown_and_html_render_new_contract_fields_not_ignored():
    pumping = _minimal_result({"trust": "bounded_pass"}, proof_method="dcfl_pumping")
    nerode = _minimal_result({"trust": "bounded_pass"}, proof_method="shallit")
    for field_value, result in (
        ("cond1", pumping), ("cond2", pumping),
        ("empty", nerode), ("L2 = {...}", nerode),
    ):
        md = render_markdown(result)
        html = render_html(result)
        assert field_value in md, f"{field_value!r} missing from markdown render"
        assert field_value in html, f"{field_value!r} missing from html render"


def test_oracle_table_shows_trust_word_not_raw_status():
    result = _minimal_result({"trust": "bounded_pass", "checks_passed": 2, "checks_total": 2})
    md = render_markdown(result)
    html = render_html(result)
    assert "проверено выборочно" in md
    assert "проверено выборочно" in html


# ---------------------------------------------------------------------------
# Cost ceiling (TODO.md backlog round C2): config.MAX_CALLS_PER_AGENT — the
# orchestrator must never call the same specialist more than this many times
# for one task. Precedent: live cfl-12 eval run, cfg_builder alone was
# called 6 times across retries (130 706 output tokens, $0.80).
# ---------------------------------------------------------------------------

class _CountingRunner:
    """A mock runner that records every agent name it was actually asked to
    run — used to assert the call cap is enforced at the call site itself,
    not just in whatever the runner happens to return."""

    def __init__(self):
        self.calls: list[str] = []

    def run_agent(self, agent_name, input_data=None):
        self.calls.append(agent_name)
        return {
            "agent": agent_name, "status": "success", "verdict": "non_dcfl",
            "confidence": 0.5, "evidence": {},
        }


def _specialist_state(agent_name: str, specialist_outputs: list, runner: _CountingRunner) -> dict:
    return {
        "_specialist_name": agent_name,
        "specialist_outputs": specialist_outputs,
        "mock_runner": runner,
        "agent_runner": None,
        "verbose": False,
        "task_ir": {},
        "hypothesis": {},
        "classifier_hint": {},
        "preprocess": {},
        "retry_context": {},
    }


class TestCallCapAtSpecialistCallSite:
    def test_specialist_skipped_once_cap_reached(self):
        runner = _CountingRunner()
        history = [("dcfl_pumping", {"status": "success"})] * MAX_CALLS_PER_AGENT
        state = _specialist_state("dcfl_pumping", history, runner)
        result = run_specialist_node(state)
        assert runner.calls == []  # no LLM call made
        assert "specialist_outputs" not in result
        assert any(
            "dcfl_pumping" in note and "call cap reached" in note
            for note in result.get("call_cap_notes", [])
        )

    def test_specialist_still_called_below_cap(self):
        runner = _CountingRunner()
        history = [("dcfl_pumping", {"status": "success"})] * (MAX_CALLS_PER_AGENT - 1)
        state = _specialist_state("dcfl_pumping", history, runner)
        result = run_specialist_node(state)
        assert runner.calls == ["dcfl_pumping"]
        assert "call_cap_notes" not in result

    def test_mock_retry_scenario_never_exceeds_cap(self):
        """Simulate the retry planner asking for the SAME agent every round
        (the worst case, matching the live cfl-12 precedent) across more
        rounds than the cap allows -- the runner must never see more than
        MAX_CALLS_PER_AGENT actual calls for it."""
        runner = _CountingRunner()
        specialist_outputs: list = []
        for _ in range(MAX_CALLS_PER_AGENT + 4):  # far more "retry rounds" than the cap
            state = _specialist_state("dcfl_pumping", specialist_outputs, runner)
            result = run_specialist_node(state)
            specialist_outputs = specialist_outputs + list(result.get("specialist_outputs", []))
        assert runner.calls.count("dcfl_pumping") == MAX_CALLS_PER_AGENT

    def test_call_cap_note_surfaces_in_verdict_gate_downgrades(self):
        """reasoning_agent_node threads run_specialist_node's call_cap_notes
        into the final verdict_gate.downgrades (dedup across rounds)."""
        from dcfl_system.orchestrator import reasoning_agent_node

        class _NoReasoningRunner:
            def run_agent(self, agent_name, input_data=None):
                return None  # forces the fallback-reasoning path

        state = {
            "task_ir": {}, "hypothesis": {}, "classifier_hint": {},
            "agent_results": {}, "oracle_verification": {},
            "retry_count": MAX_RETRIES, "verbose": False,
            "mock_runner": _NoReasoningRunner(), "agent_runner": None,
            "call_cap_notes": [
                "agent dcfl_pumping call cap reached (3 calls)",
                "agent dcfl_pumping call cap reached (3 calls)",  # duplicate, from a later round
            ],
        }
        result = reasoning_agent_node(state)
        downgrades = result["reasoning"]["verdict_gate"]["downgrades"]
        matches = [d for d in downgrades if "dcfl_pumping call cap reached" in d]
        assert len(matches) == 1
