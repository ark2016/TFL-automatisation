"""Contract tests for the dcfl_pumping / shallit prompts vs docs/THEORY.md.

docs/THEORY.md §1.1 and §1.2 fixed two formulations that used to be wrong:

- dcfl_pumping.md: condition (1) of the two-word DCFL pumping lemma (лемма Ю)
  is a PAIR (x2, x4) anywhere in x with |x2 x3 x4| <= p — NOT a single factor
  x2 in the tail of x with |x2 x3| <= p, |x2| > 0 (that formulation is false:
  it falsely rejects the DCFL {a^n b^n c^m}, see THEORY.md §1.1).
- shallit.md: the theorem is Theorem 4.7.4 [Sh] (Myhill-Nerode class count),
  with the "dead class" caveat — NOT the old "homogeneous infinite subset"
  claim (false: for {a^n b^n} and M = {a^n} any two elements are already
  distinguishable, see THEORY.md §1.2).

This file checks (a)/(b) the prompt text itself, (c) that the worked-example
JSON blocks in both prompts parse and carry the required contract keys, and
(d) that dcfl_system.lib.oracle_verifier accepts the new fields and rejects
the obsolete ones with an issue, not a silent pass.

Status strings below use the trust taxonomy from docs/VERDICT_POLICY.md §1:
a structural-only pass is reported as ``well_formed`` (used to be ``verified``)
and a structural issue with no counterexample as ``not_verified`` (used to be
``issues_found``) — no task_ir is passed in these tests, so the step-2
semantic oracle checks (§4) never fire and trust never exceeds well_formed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dcfl_system.lib.oracle_verifier import verify_agent_results

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DCFL_PUMPING_MD = PROMPTS_DIR / "dcfl_pumping.md"
SHALLIT_MD = PROMPTS_DIR / "shallit.md"
STACK_STRATEGY_MD = PROMPTS_DIR / "stack_strategy.md"
INH_AMBIGUITY_MD = PROMPTS_DIR / "inh_ambiguity.md"
MOCK_DIR = Path(__file__).resolve().parent.parent / "examples" / "mock"

JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)


def _json_blocks(path: Path) -> list[dict]:
    """Parse every fenced ```json block that IS valid JSON (schema-illustration
    blocks with pseudo-union types like "a" | "b" are skipped — they are not
    meant to parse)."""
    text = path.read_text(encoding="utf-8")
    parsed = []
    for raw in JSON_BLOCK_RE.findall(text):
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return parsed


# ---------------------------------------------------------------------------
# (a) dcfl_pumping.md — condition (1) is the PAIR formulation, not the old
#     single-factor one.
# ---------------------------------------------------------------------------

def test_dcfl_pumping_prompt_has_pair_formulation():
    text = DCFL_PUMPING_MD.read_text(encoding="utf-8")
    assert "x₁x₂x₃x₄x₅" in text
    assert "x₂x₃x₄" in text and "≤ p" in text
    assert re.search(r"\|x₂x₃x₄\|\s*≤\s*p", text)


def test_dcfl_pumping_prompt_does_not_contain_old_single_factor_condition():
    text = DCFL_PUMPING_MD.read_text(encoding="utf-8")
    # The old (false) condition (1) read "|x2 x3| <= p, |x2| > 0" (a single
    # factor x2 in the tail of x). It must not appear anywhere as a live
    # formulation of condition (1).
    assert not re.search(r"\|x2\s*x3\|\s*<=\s*p,\s*\|x2\|\s*>\s*0", text)
    assert "|x2 x3| <= p" not in text


# ---------------------------------------------------------------------------
# (b) shallit.md — Theorem 4.7.4, dead class, no "homogeneous".
# ---------------------------------------------------------------------------

def test_shallit_prompt_has_theorem_4_7_4_and_dead_class():
    text = SHALLIT_MD.read_text(encoding="utf-8")
    assert "Theorem 4.7.4" in text
    assert ("dead class" in text.lower()) or ("мёртв" in text)


def test_shallit_prompt_has_no_homogeneous_wording():
    text = SHALLIT_MD.read_text(encoding="utf-8")
    assert "homogeneous" not in text.lower()


# ---------------------------------------------------------------------------
# (c) JSON contract blocks parse and carry the required keys.
# ---------------------------------------------------------------------------

def test_dcfl_pumping_json_blocks_parse_and_have_required_keys():
    blocks = _json_blocks(DCFL_PUMPING_MD)
    proof_blocks = [
        b["proof_sketch"] for b in blocks
        if isinstance(b, dict) and isinstance(b.get("proof_sketch"), dict)
    ]
    assert proof_blocks, "no worked-example proof_sketch JSON block found in dcfl_pumping.md"
    required = {
        "kind", "pumping_length", "word_w", "word_w_prime", "common_prefix_x",
        "suffix_y", "suffix_z", "first_letters_match",
        "condition1_argument", "condition2_argument",
    }
    for proof in proof_blocks:
        missing = required - set(proof.keys())
        assert not missing, f"proof_sketch missing keys: {missing}"
        assert "no_pumping_argument" not in proof


def test_shallit_json_blocks_parse_and_have_required_keys():
    blocks = _json_blocks(SHALLIT_MD)
    proof_blocks = [
        b["proof_sketch"] for b in blocks
        if isinstance(b, dict) and isinstance(b.get("proof_sketch"), dict)
    ]
    assert proof_blocks, "no worked-example proof_sketch JSON block found in shallit.md"

    seen_techniques = set()
    for proof in proof_blocks:
        assert "technique" in proof
        technique = proof["technique"]
        assert technique in ("nerode_classes", "prefix_continuation")
        seen_techniques.add(technique)
        # obsolete fields must not appear
        for old_field in ("infinite_set_description", "separating_context", "two_elements"):
            assert old_field not in proof

        if technique == "nerode_classes":
            assert proof.get("dead_class_status") in ("empty", "finite", "infinite"), (
                "nerode_classes example must have a valid 'dead_class_status' "
                f"(empty/finite/infinite): {proof.get('dead_class_status')!r}"
            )
            for field in ("distinguishing_suffix", "separation_argument", "argument"):
                assert proof.get(field), f"nerode_classes example missing non-empty '{field}'"
        elif technique == "prefix_continuation":
            for field in ("derived_language", "regular_filter", "non_cfl_argument", "argument"):
                assert proof.get(field), f"prefix_continuation example missing non-empty '{field}'"

    # Both worked examples in shallit.md should be present.
    assert seen_techniques == {"nerode_classes", "prefix_continuation"}


# ---------------------------------------------------------------------------
# (d) oracle_verifier accepts the new fields and rejects the obsolete ones.
# ---------------------------------------------------------------------------

def test_verifier_accepts_dcfl_pumping_new_contract():
    proof_sketch = {
        "kind": "dcfl_pumping",
        "pumping_length": "p",
        "word_w": "aⁿbⁿ",
        "word_w_prime": "aⁿb²ⁿ",
        "common_prefix_x": "aⁿbⁿ⁻¹",
        "suffix_y": "b",
        "suffix_z": "bⁿ⁺¹",
        "first_letters_match": "обе 'b'",
        "condition1_argument": "для любой пары (x2, x4) в окне <= p ...",
        "condition2_argument": "для любого x2 в последних p символах ...",
    }
    agent_results = {
        "dcfl_pumping": {
            "status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch,
        }
    }
    result = verify_agent_results(agent_results, {})
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "well_formed"
    assert "issues" not in entry


def test_verifier_flags_obsolete_no_pumping_argument():
    proof_sketch = {
        "kind": "dcfl_pumping",
        "word_w": "aⁿbⁿ",
        "word_w_prime": "aⁿb²ⁿ",
        "common_prefix_x": "aⁿbⁿ⁻¹",
        "suffix_y": "b",
        "suffix_z": "bⁿ⁺¹",
        "first_letters_match": "обе 'b'",
        "no_pumping_argument": "старое поле, больше не поддерживается",
    }
    agent_results = {
        "dcfl_pumping": {
            "status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch,
        }
    }
    result = verify_agent_results(agent_results, {})
    entry = result["dcfl_pumping"]
    assert entry["verification_status"] == "not_verified"
    issues = " ".join(entry.get("issues", []))
    assert "no_pumping_argument" in issues
    assert "condition1_argument" in issues and "condition2_argument" in issues


def test_verifier_accepts_shallit_nerode_classes_contract():
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_status": "empty",
        "distinguishing_suffix": "w = b a^N b u^R",
        "separation_argument": "uw палиндром, vw — нет",
        "argument": "по контрапозиции теоремы 4.7.4",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "well_formed"


def test_verifier_accepts_shallit_prefix_continuation_contract():
    proof_sketch = {
        "kind": "shallit",
        "technique": "prefix_continuation",
        "derived_language": "L_$ ∩ a*b*$b+ = {a^n b^n $ b^n}",
        "regular_filter": "R = a*b*$b+",
        "non_cfl_argument": "лемма о накачке для КС",
        "argument": "лемма о продолжении + замкнутость DCFL относительно ∩REG",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "well_formed"


def test_verifier_rejects_shallit_missing_technique_specific_fields():
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "argument": "рассуждение есть, но остальных полей нет",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "not_verified"
    issues = " ".join(entry.get("issues", []))
    assert "dead_class_status" in issues
    assert "distinguishing_suffix" in issues
    assert "separation_argument" in issues


@pytest.mark.parametrize("old_field", ["infinite_set_description", "separating_context", "two_elements"])
def test_verifier_flags_obsolete_homogeneous_subsets_fields(old_field):
    proof_sketch = {
        "kind": "shallit",
        old_field: "старая формулировка",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "not_verified"
    issues = " ".join(entry.get("issues", []))
    assert "obsolete Shallit formulation (homogeneous subsets)" in issues


# ---------------------------------------------------------------------------
# (e) THEORY.md Part II §1.6-1.8 round-2 checks:
#     - stack_strategy.md no longer offers 'wvaav...' as a solved (success)
#       DCFL example (THEORY.md §1.6: 'aa' is not a true separator there).
#     - inh_ambiguity.md documents the disjoint-branches caveat (§1.8).
#     - exam_01/02/03 reasoning mocks all agree the language is non_dcfl.
# ---------------------------------------------------------------------------

def test_stack_strategy_prompt_has_no_wvaav_success_example():
    text = STACK_STRATEGY_MD.read_text(encoding="utf-8")
    # 'wvaav' may still be *mentioned* (as a not_applicable / cautionary
    # example, per THEORY.md §1.6), but it must never appear as the language
    # of a "success"/"verdict": "dcfl" worked example.
    for block in _json_blocks(STACK_STRATEGY_MD):
        if not isinstance(block, dict):
            continue
        blob = json.dumps(block, ensure_ascii=False)
        if "wvaav" in blob.lower():
            assert block.get("status") != "success" or block.get("verdict") != "dcfl", (
                "stack_strategy.md still has a success/dcfl worked example "
                "using wvaav...w^R (THEORY.md §1.6: 'aa' is not a true "
                "separator for this language)"
            )
    # Also guard the prose directly: no "success" example section headed by
    # wvaav should exist.
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "wvaav" in line.lower() and "## full example" in line.lower():
            pytest.fail(f"stack_strategy.md:{lineno}: wvaav used as a solved-example header: {line!r}")


def test_inh_ambiguity_prompt_documents_disjoint_branches():
    text = INH_AMBIGUITY_MD.read_text(encoding="utf-8")
    assert ("disjoint" in text.lower()) or ("дизъюнкт" in text.lower())


@pytest.mark.parametrize("stem", ["dcfl_exam_01_reasoning", "dcfl_exam_02_reasoning", "dcfl_exam_03_reasoning"])
def test_exam_reasoning_mocks_agree_on_non_dcfl(stem):
    data = json.loads((MOCK_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    assert data.get("verdict") == "non_dcfl", (
        f"{stem}.json: expected verdict=non_dcfl per docs/THEORY.md §1.6-1.8, got {data.get('verdict')!r}"
    )


def test_verifier_rejects_shallit_invalid_technique():
    proof_sketch = {
        "kind": "shallit",
        "technique": "bogus_technique",
        "argument": "что-то",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "not_verified"


# ---------------------------------------------------------------------------
# (f) THEORY.md §1.8 round-2 fix: the exam_03 dcfl_pumping proof used to
#     claim "a = b = c needed for the c^n branch" and "any pair in the a/b
#     region kills xy at i=2" — both false (reviewer counterexample: n=5,
#     x2=b in the b-block, i=2 gives xy=a^5 b^6 c^5 a in L). The fixed
#     argument's words are checked here by brute-force membership in
#     L = {a^n b^m c^n a c^l} u {a^n b^{m+n} a c^l}, n>=1, m,l>=0.
# ---------------------------------------------------------------------------

def _in_exam03_language(word: str) -> bool:
    """Membership oracle for L = {a^n b^m c^n a c^l} u {a^n b^{m+n} a c^l}."""
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
    # branch 1: a^n b^m c^n a c^l
    k = j
    c1 = 0
    while k < len(word) and word[k] == "c":
        k += 1
        c1 += 1
    if c1 == n and k < len(word) and word[k] == "a" and all(ch == "c" for ch in word[k + 1:]):
        return True
    # branch 2: a^n b^{m+n} a c^l (no c between the b-block and the 'a')
    if m_total >= n and j < len(word) and word[j] == "a" and all(ch == "c" for ch in word[j + 1:]):
        return True
    return False


def test_exam03_bruteforce_membership_oracle_sanity():
    assert _in_exam03_language("abba")          # n=1, m+n=2 >= 1: branch 2
    assert _in_exam03_language("aabbcca")       # n=2, m=0, c^2, a: branch 1
    assert _in_exam03_language("aabbccacc")     # branch 1 with l=2
    assert not _in_exam03_language("")
    assert not _in_exam03_language("bb")
    assert not _in_exam03_language("aabbccca")  # n=2, c-count=3 != n, no branch matches
    # reviewer's counterexample to the OLD (false) proof: a^5 b^6 c^5 a is in L
    # (branch 1, n=5, m=6, l=0) even though "b" was pumped inside the b-block.
    assert _in_exam03_language("a" * 5 + "b" * 6 + "c" * 5 + "a")


def test_exam03_pumping_words_are_correct_per_theory_1_8():
    """Both base words and every counterexample used by the fixed §1.8 proof
    in dcfl_system/prompts/dcfl_pumping.md are verified here for a concrete
    n, s (docs/THEORY.md §1.8)."""
    n = 4
    xy = "a" * n + "b" * n + "c" * n + "a"  # branch c^n, m=n, l=0
    xz = "a" * n + "b" * n + "a"  # branch b^n, m=0
    assert _in_exam03_language(xy)
    assert _in_exam03_language(xz)

    s = 1
    # condition (1): pair in the a-block, i=2 -> a-count n+s != c-count n,
    # and the word still contains c^n so it cannot match branch 2 either.
    cond1_a_block = "a" * (n + s) + "b" * n + "c" * n + "a"
    assert not _in_exam03_language(cond1_a_block)

    # condition (1): pair in the b-block, i=0 -> xz loses s letters b.
    cond1_b_block = "a" * n + "b" * (n - 1 - s) + "a"
    assert not _in_exam03_language(cond1_b_block)

    # condition (2): x2 = b^s in the tail of x, i=0, for every factor
    # z2 of z = "ba" (z2 in {eps, b, a, ba}).
    prefix = "a" * n + "b" * (n - 1 - s)
    z2_to_remainder = {"": "ba", "b": "a", "a": "b", "ba": ""}
    for z2, z1z3 in z2_to_remainder.items():
        pumped = prefix + z1z3
        assert not _in_exam03_language(pumped), (z2, pumped)
