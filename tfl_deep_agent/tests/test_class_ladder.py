"""The class ladder of prompts/main.md («Определение класса», user decision 2026-11-08).

For a "which class" task the main prompt goes REG -> LL(k) -> DCFL -> CFL and stops at the
first class with a positive, tool-checked proof. These tests pin:

* the order and the stop rule;
* that every inclusion the section states is stated in docs/THEORY.md, and that it claims no
  inclusion THEORY.md does not state (DCFL ⊆ CFL is not stated as proper there);
* the binding rule step 6 relies on: the IR approved *last* at HITL 1 is the one the verdict is
  judged against, and re-validating an approved IR makes it the last one again.
"""

from __future__ import annotations

import re
from pathlib import Path

from tfl_deep_agent import hitl

PKG = Path(__file__).resolve().parent.parent
ROOT = PKG.parent
MAIN = (PKG / "prompts" / "main.md").read_text(encoding="utf-8")
THEORY = (ROOT / "docs" / "THEORY.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}\n")
    nxt = text.find("\n## ", start + 3)
    return text[start:] if nxt < 0 else text[start:nxt]


LADDER = _section(MAIN, "Определение класса")
FLAT = " ".join(LADDER.split())


def test_the_section_sits_between_the_workflow_and_the_roles():
    order = [MAIN.index(h) for h in ("## Workflow", "## Определение класса", "## Roles (`task` tool)")]
    assert order == sorted(order)


def test_named_class_keeps_the_single_question_flow():
    assert "When the task names one class" in FLAT and "answer exactly that question" in FLAT
    assert "names no class at all" in FLAT


def test_the_ladder_goes_from_the_narrowest_class_and_stops_at_the_first_positive_proof():
    assert "Try REG, then LL(k) with the smallest k first, then DCFL, then CFL." in FLAT
    assert "Stop at the first class whose membership a tool result supports." in FLAT
    assert "Your own reasoning is not a positive proof." in FLAT
    assert "Prove non-membership for every narrower class." in FLAT
    # an unresolved narrower class is not reported as excluded
    assert "do not claim non-membership" in FLAT


def test_the_chain_is_the_one_theory_states():
    assert "REG ⊊ LL(1) ⊆ ⋃ₖ LL(k) ⊊ DCFL ⊆ CFL." in LADDER
    # THEORY.md §3.2 (part I) and §1.6 (part II)
    theory = " ".join(THEORY.split())
    assert "REG ⊊ LL(1) (языки)" in theory
    assert "LL(k) ⊊ LL(k+1) как языки" in theory
    assert "⋃_k LL(k) ⊊ DCFL" in theory
    assert "DCFL ⊆ CFL" in theory
    assert "Не DCFL ⇒ не LL" in theory  # §3.3 (A), used for "non-membership goes down"


def test_no_inclusion_theory_does_not_state():
    # THEORY.md never states DCFL ⊊ CFL; the section must not claim it either
    assert not re.search(r"DCFL\s*[⊊⊂]\s*CFL", THEORY)
    assert not re.search(r"DCFL\s*[⊊⊂]\s*CFL", LADDER)
    assert "does not state this inclusion as proper" in FLAT


def test_the_examples_are_theory_examples():
    theory = " ".join(THEORY.split())
    # §3.3 (C) / part II §3.4: DCFL but not LL
    assert "{aⁿbⁿ} ∪ {aⁿcⁿ}, {aⁿ0bⁿ} ∪ {aⁿ1b²ⁿ}: DCFL, не LL (C)" in theory
    assert "{w b* c wᴿ | w ∈ {a,b}*} — DCFL, но не LL(k)" in theory
    assert "{aⁱbʲ | i ≥ j}, {bᴹcbʲ | j ≤ M}, {w b* c wᴿ} — DCFL, не LL" in theory
    # §3.2: the DFA grammar and the witness of REG ⊊ LL(1)
    assert "грамматика ДКА Q → aδ(Q,a), Q → ε для допускающих состояний — LL(1)" in theory
    assert "{aⁿbⁿ} — нерегулярный LL(1)-язык" in theory
    for text in ("{aⁿbⁿ} ∪ {aⁿcⁿ}", "{aⁿ0bⁿ} ∪ {aⁿ1b²ⁿ}", "{w b* c wᴿ}", "{aⁱbʲ | i ≥ j}",
                 "Q → aδ(Q,a), Q → ε", "{aⁿbⁿ} is a non-regular LL(1) language"):
        assert text in FLAT, text


def test_the_lean_remark_matches_the_lean_tool():
    from tfl_deep_agent.tools import lean

    assert "REG, DCFL and CFL have a `lean_statement` (LL(k) has none)" in FLAT
    doc = " ".join((lean.__doc__ or "").split())
    assert "LL(k) is" in doc and "dcfl_system.lib.lean_ir" in doc and "cfl_system.lib.lean_ir" in doc


def test_the_report_names_the_positive_class_only():
    assert "`language_class` is the class of the positive proof" in FLAT
    assert "do not list those ids in the verdict's `evidence_ids`" in FLAT
    assert "Code rates only the verdict of `language_class`" in FLAT


# ---------------------------------------------------------------------------
# step 6: the last approved IR binds the verdict
# ---------------------------------------------------------------------------

def _rec(call_id, decision, fp):
    return {"id": call_id, "point": hitl.FORMALIZATION, "decision": decision, "ir_fingerprint": fp}


def test_the_ir_approved_last_binds_the_verdict():
    reg_then_ll = [_rec("v1", "approved", "fp-reg"), _rec("v2", "approved", "fp-ll")]
    binding = hitl.formalization_binding(reg_then_ll, on=True)
    assert binding["status"] == "approved" and binding["ir_fingerprint"] == "fp-ll"


def test_revalidating_an_approved_ir_makes_it_the_last_one_again():
    records = [_rec("v1", "approved", "fp-ll"), _rec("v2", "approved", "fp-reg"),
               _rec("v3", "unchanged", "fp-ll")]  # what HITL 1 records for an already approved IR
    binding = hitl.formalization_binding(records, on=True)
    assert binding["status"] == "approved" and binding["ir_fingerprint"] == "fp-ll"
    assert "set the report's `ir_fingerprint` to the approved IR the verdict is about" in FLAT
    assert "An IR that was already approved passes HITL 1 without a new stop." in FLAT
