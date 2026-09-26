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
            for field in ("dead_class_finite", "distinguishing_suffix", "separation_argument", "argument"):
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
    assert entry["verification_status"] == "verified"
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
    assert entry["verification_status"] == "issues_found"
    issues = " ".join(entry.get("issues", []))
    assert "no_pumping_argument" in issues
    assert "condition1_argument" in issues and "condition2_argument" in issues


def test_verifier_accepts_shallit_nerode_classes_contract():
    proof_sketch = {
        "kind": "shallit",
        "technique": "nerode_classes",
        "dead_class_finite": "D пуст: любое слово продолжается до палиндрома",
        "distinguishing_suffix": "w = b a^N b u^R",
        "separation_argument": "uw палиндром, vw — нет",
        "argument": "по контрапозиции теоремы 4.7.4",
    }
    agent_results = {
        "shallit": {"status": "success", "verdict": "non_dcfl", "proof_sketch": proof_sketch}
    }
    result = verify_agent_results(agent_results, {})
    entry = result["shallit"]
    assert entry["verification_status"] == "verified"


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
    assert entry["verification_status"] == "verified"


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
    assert entry["verification_status"] == "issues_found"
    issues = " ".join(entry.get("issues", []))
    assert "dead_class_finite" in issues
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
    assert entry["verification_status"] == "issues_found"
    issues = " ".join(entry.get("issues", []))
    assert "obsolete Shallit formulation (homogeneous subsets)" in issues


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
    assert entry["verification_status"] == "issues_found"
