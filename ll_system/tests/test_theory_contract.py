"""Contract tests for the ll_system prompts / CLAUDE.md vs docs/THEORY.md.

docs/THEORY.md §3.2-3.3 fixed formulations that used to be wrong:

- `ll_system/CLAUDE.md` claimed "LL ∩ REG = LL" — false; THEORY.md §3.2 gives the
  counterexample L = {aⁿw | w∈{b,c}ⁿ} (LL(1)) ∩ (a*b* ∪ a*c*) (regular) = {aⁿbⁿ}∪{aⁿcⁿ}
  (not LL for any k).
- `ll_prefix_classes_agent.md` invented a false "LL Nerode theorem" ("L is LL(k) iff
  finitely many k-equivalence classes of prefixes") — false (counterexample: {aⁿbⁿ}
  is LL(1) yet its prefixes aⁿ are pairwise 1-distinguishable). The correct theory is
  Theorem 4.7.4 [Sh]: all Myhill-Nerode classes finite ⇒ not DCFL ⇒ not LL.
- `ll_substitution_agent.md` claimed "the parser's configuration after reading u1, u2
  with equal lookahead depends only on the lookahead" — false (a parser's
  configuration includes the stack; the correct argument substitutes
  *subderivations of a nonterminal in the hypothetical LL(k) grammar*, THEORY.md
  §3.3 (C), the "branch-point" argument).

This file checks (a) the prompt/CLAUDE.md text itself no longer contains the false
formulations and does contain the correct ones, and (b) that the worked-example JSON
blocks in both prompts parse and carry the required new-contract keys.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PREFIX_CLASSES_MD = PROMPTS_DIR / "ll_prefix_classes_agent.md"
SUBSTITUTION_MD = PROMPTS_DIR / "ll_substitution_agent.md"
CLAUDE_MD = Path(__file__).resolve().parent.parent / "CLAUDE.md"
TZ_MD = Path(__file__).resolve().parent.parent / "tz_ll_agent_system.md"

JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)


def _json_blocks(path: Path) -> list[dict]:
    """Parse every fenced ```json block that IS valid JSON (schema-illustration
    blocks with pseudo-union types like "not_ll | uncertain" are skipped — they
    are not meant to parse)."""
    text = path.read_text(encoding="utf-8")
    parsed = []
    for raw in JSON_BLOCK_RE.findall(text):
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return parsed


# ---------------------------------------------------------------------------
# (a) CLAUDE.md — no "LL ∩ REG = LL"
# ---------------------------------------------------------------------------

def test_claude_md_does_not_claim_ll_cap_reg_equals_ll():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "LL ∩ REG = LL" not in text


def test_claude_md_mentions_not_closed_under_union_and_reg_intersection():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "closed under" in text.lower()
    assert "∩ REG" in text
    assert "∪" in text


def test_claude_md_references_theory_doc():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "docs/THEORY.md" in text


# ---------------------------------------------------------------------------
# (b) prefix_classes prompt — no false "LL Nerode theorem", no draft chatter
# ---------------------------------------------------------------------------

def test_prefix_classes_prompt_has_no_false_ll_nerode_theorem():
    text = PREFIX_CLASSES_MD.read_text(encoding="utf-8")
    assert "LL Nerode Theorem" not in text
    assert "finitely many k-equivalence classes" not in text.lower()


def test_prefix_classes_prompt_has_no_draft_chatter():
    text = PREFIX_CLASSES_MD.read_text(encoding="utf-8")
    for marker in ("hmm", "Let me use", "wait, is this", "Let me restart"):
        assert marker.lower() not in text.lower(), f"draft marker {marker!r} still present"


def test_prefix_classes_prompt_has_theorem_4_7_4():
    text = PREFIX_CLASSES_MD.read_text(encoding="utf-8")
    assert "4.7.4" in text
    assert "DCFL" in text


def test_prefix_classes_prompt_requires_dead_class_check():
    text = PREFIX_CLASSES_MD.read_text(encoding="utf-8")
    assert "dead_class_finite" in text


def test_prefix_classes_prompt_json_blocks_have_new_contract_keys():
    blocks = _json_blocks(PREFIX_CLASSES_MD)
    proof_blocks = [
        b["proof_sketch"] for b in blocks
        if isinstance(b, dict) and isinstance(b.get("proof_sketch"), dict)
    ]
    assert proof_blocks, "expected at least one parseable proof_sketch JSON block"
    for ps in proof_blocks:
        assert ps.get("method") == "prefix_classes"
        for key in ("theorem", "dead_class_finite", "distinguishing_suffix",
                    "separation_argument", "for_all_k", "conclusion", "proof_explanation"):
            assert key in ps, f"missing {key!r} in {ps}"


# ---------------------------------------------------------------------------
# (c) substitution prompt — no false "parser configuration depends only on
#     lookahead", no draft chatter
# ---------------------------------------------------------------------------

def test_substitution_prompt_has_no_false_lookahead_only_claim():
    text = SUBSTITUTION_MD.read_text(encoding="utf-8")
    assert "depend only on the lookahead" not in text.lower()


def test_substitution_prompt_has_no_draft_chatter():
    text = SUBSTITUTION_MD.read_text(encoding="utf-8")
    for marker in ("hmm", "let me restart", "let me be precise", "wait, let me",
                   "this is getting complicated"):
        assert marker.lower() not in text.lower(), f"draft marker {marker!r} still present"


def test_substitution_prompt_has_branch_point_argument():
    text = SUBSTITUTION_MD.read_text(encoding="utf-8")
    assert "branch" in text.lower()
    assert "nonterminal" in text.lower()
    assert "pigeonhole" in text.lower() or "Дирихле" in text


def test_substitution_prompt_json_blocks_have_new_contract_keys():
    blocks = _json_blocks(SUBSTITUTION_MD)
    proof_blocks = [
        b["proof_sketch"] for b in blocks
        if isinstance(b, dict) and isinstance(b.get("proof_sketch"), dict)
    ]
    assert proof_blocks, "expected at least one parseable proof_sketch JSON block"
    for ps in proof_blocks:
        assert ps.get("method") == "substitution"
        for key in ("branch_words", "common_form_argument", "deciding_nonterminal_argument",
                    "pigeonhole_argument", "for_all_k", "proof_explanation"):
            assert key in ps, f"missing {key!r} in {ps}"


# ---------------------------------------------------------------------------
# (d) tz_ll_agent_system.md — §4.7/§4.9 and oracle table reference the new theory
# ---------------------------------------------------------------------------

def test_tz_ll_prefix_classes_section_references_theorem_4_7_4():
    text = TZ_MD.read_text(encoding="utf-8")
    section = text.split("### 4.9. prefix_classes_agent")[1].split("### 4.")[0] \
        if "### 4.9. prefix_classes_agent" in text else ""
    assert section, "expected a §4.9 prefix_classes_agent section"
    assert "4.7.4" in section
    assert "мёртв" in section.lower()


def test_tz_ll_substitution_section_references_branch_argument():
    text = TZ_MD.read_text(encoding="utf-8")
    section = text.split("### 4.7. substitution_agent")[1].split("### 4.")[0] \
        if "### 4.7. substitution_agent" in text else ""
    assert section, "expected a §4.7 substitution_agent section"
    assert "развилк" in section.lower() or "X_{t*}" in section or "X_{t\\*}" in section


def test_tz_ll_does_not_claim_ll_cap_reg_equals_ll():
    text = TZ_MD.read_text(encoding="utf-8")
    assert "LL ∩ REG = LL" not in text
