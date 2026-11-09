"""Regression for the critic role (PLAN: "the ticket-50 wrong p=4 instance
becomes a regression test for the critic").

The golden ticket-50 run (`docs/examples/cfl_ticket50_w1bw2w3_lean_proved/`)
has a correct parametric pumping word a^{2p} b (ab)^p a^{2p}, but its informal
proof prints the p=4 instance as a^8 b (ab)^5 a^8, which is not in L
(skills/cfl/traps.md, trap 1). Here a scripted run gives that proof to the
`critic` role. The critic checks it with `pump_check_cfl` (claim mode), the
oracle refutes it, and the code-side verdict gate then refuses to publish the
"not CFL" verdict that rests on it.

Scripted chat models only (`hitl.start_run` with a mock script, every HITL
stop auto-approved); the tools and the verdict gate are the real ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from tfl_deep_agent import hitl  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "docs" / "examples" / "cfl_ticket50_w1bw2w3_lean_proved"
WRONG_P4 = "aaaaaaaababababababaaaaaaaa"  # a^8 b (ab)^5 a^8: |w2| = 10 != |w1| = 8


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_agent(obj: Any, agent: str) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if obj.get("agent") == agent and isinstance(obj.get("evidence"), dict):
            return obj
        children = list(obj.values())
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_agent(child, agent)
        if found:
            return found
    return None


IR = _load(GOLDEN / "input.json")
CLAIM = _find_agent(_load(GOLDEN / "result.json"), "pumping_cfl")


def _informal_proof(claim: dict[str, Any]) -> str:
    """The proof text the critic is asked to check: the golden claim's
    parametric word and its printed word instances (the p=4 one is wrong)."""
    ev = claim["evidence"]
    instances = ", ".join(f"p={p}: {w}" for p, w in sorted((ev.get("word_instances") or {}).items()))
    return (f"Доказательство (лемма о накачке для КС-языков): слово z = {claim.get('word_chosen')}; "
            f"примеры слова для конкретных p: {instances}.")


def _script(*, cite_critic: bool) -> dict[str, Any]:
    task = IR["source_text"]
    report_ids = ["cr-pump"] if cite_critic else []
    return {
        "description": "ticket-50 critic regression: the critic refutes the informal pumping proof whose "
                       "p=4 instance a^8 b (ab)^5 a^8 is not in L.",
        "task": task,
        "template": "short_verdict",
        "defs": {"ir": IR, "claim": CLAIM},
        "models": {
            "main": [
                {"tool_calls": [{"name": "task", "id": "call-ir-builder", "args": {
                    "subagent_type": "ir-builder",
                    "description": f"Класс cfl. Построй IR для задачи: «{task}». Верни IR, fingerprint и пересказ."}}]},
                {"tool_calls": [{"name": "task", "id": "call-critic", "args": {
                    "subagent_type": "critic",
                    "description": ("Проверь доказательство того, что L не КС, против IR задачи. "
                                    + _informal_proof(CLAIM)
                                    + " IR: " + json.dumps(IR, ensure_ascii=False)
                                    + " Проверь утверждение о накачке оракулом pump_check_cfl (mode claim).")}}]},
                {"tool_calls": [{"name": "submit_report", "id": "call-report", "args": {
                    "verdict": "not CFL", "answer": "no", "language_class": "cfl",
                    "method": "лемма о накачке для КС-языков",
                    "summary": "По лемме о накачке язык не контекстно-свободен.",
                    "template": "short_verdict",
                    "steps": [{"claim": "Слово z = a^{2p} b (ab)^p a^{2p} нельзя накачать.",
                               "justification": "Разбор случаев разбиения z = uvwxy.",
                               "evidence_ids": report_ids}],
                    "evidence_ids": report_ids}}]},
            ],
            "ir_builder": [
                {"tool_calls": [{"name": "validate_ir", "id": "ib-validate",
                                 "args": {"cls": "cfl", "ir": {"$def": "ir"}}}]},
                {"content": "Класс cfl, task_type classify_and_prove_cfl, language_spec kind "
                            "exists_decomposition: w1 b w2 w3, |w1| = |w2| = |w3|, w2 из (ab)*. "
                            "validate_ir: valid. Неуверенные места: нет."},
            ],
            "critic": [
                {"tool_calls": [{"name": "pump_check_cfl", "id": "cr-pump",
                                 "args": {"ir": {"$def": "ir"}, "claim": {"$def": "claim"}}}]},
                {"content": "Опровергнуто (cr-pump): пример для p=4 a^8 b (ab)^5 a^8 не принадлежит L "
                            "(|w2| = 10, а |w1| = 8). Доказательство в таком виде не принимается."},
            ],
        },
    }


def _run(tmp_path: Path, *, cite_critic: bool) -> dict[str, Any]:
    run_dir = tmp_path / ("cited" if cite_critic else "uncited")
    outcome = hitl.start_run(run_dir, IR["source_text"], mock_script=_script(cite_critic=cite_critic),
                             auto_approve=True, dialog=None)
    assert not outcome.awaiting
    return _load(run_dir / "result.json")


def _critic_record(result: dict[str, Any]) -> dict[str, Any]:
    (rec,) = [e for e in result["evidence"] if e["id"] == "cr-pump"]
    return rec


def test_golden_claim_still_carries_the_wrong_p4_instance():
    assert CLAIM is not None
    assert CLAIM["evidence"]["word_instances"]["4"] == WRONG_P4


def test_critic_refutes_the_ticket50_proof_and_the_cited_verdict_is_blocked(tmp_path):
    result = _run(tmp_path, cite_critic=True)
    assert result["status"] == "submitted"
    rec = _critic_record(result)
    assert rec["tool"] == "pump_check_cfl" and rec["role"] == "critic"
    assert rec["trust"] == "refuted" and WRONG_P4 in (rec["trust_reason"] or "")
    assert "cr-pump" in result["refuted_evidence"]
    # VERDICT_POLICY R2: a cited result refuting the verdict's side blocks it
    assert result["trust"] == "not_verified"
    assert result["confidence_cap"] <= 0.40
    assert result["basis"] == []
    assert any("refutes" in line and "cr-pump" in line for line in result["verdict_gate"]), result["verdict_gate"]
    assert result["verdict_token"] is None and result.get("published_verdict") is None  # cfl: inconclusive = null


def test_an_uncited_refutation_still_leaves_the_verdict_without_a_basis(tmp_path):
    result = _run(tmp_path, cite_critic=False)
    assert result["status"] == "submitted"
    assert _critic_record(result)["trust"] == "refuted"
    # the refuted claim is the only destructive evidence: R1 finds no basis
    assert result["trust"] == "not_verified" and result["basis"] == []
    assert any(line.startswith("R1") for line in result["verdict_gate"]), result["verdict_gate"]
    assert result["verdict_token"] is None
