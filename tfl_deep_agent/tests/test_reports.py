"""P3 reports: templates, the renderer and the report tools.

No test here reaches a model API. The renderer tests use hand-made results and
ledgers; the end-to-end tests replay the P2 mock scripts with scripted chat
models (skipped without the ``[deep]`` extra).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tfl_deep_agent.middleware import budget as B
from tfl_deep_agent.reports import render as R
from tfl_deep_agent.tools import base

PKG = Path(R.__file__).resolve().parent.parent
REPO = PKG.parent
MOCK = PKG / "examples" / "mock"
GOLDEN_REG = REPO / "docs" / "examples" / "reg_grammar_SaSb_lean_proved" / "result.md"
GOLDEN_CFL = REPO / "docs" / "examples" / "cfl_ticket50_w1bw2w3_lean_proved" / "result.md"


# ---------------------------------------------------------------------------
# Fixtures: a hand-made run
# ---------------------------------------------------------------------------

def entry(eid: str, tool: str, trust: str, *, ok: bool = True, reason: str = "r", **kw: Any) -> dict[str, Any]:
    return {"id": eid, "tool": tool, "ok": ok, "trust": trust, "trust_reason": reason,
            "error": None if ok else "boom", **kw}


def make_report(**over: Any) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "verdict": "regular", "answer": "yes", "language_class": "reg",
        "method": "ДКА по выражению и сверка с IR",
        "hypothesis": "Язык задан регулярным выражением, значит регулярен.",
        "lemmas": [{"theorem_id": "1.1", "use": "не нужна, пример ссылки"}],
        "claims": [
            {"text": "ДКА принимает ровно L", "evidence_ids": ["ev-dfa-vs-ir"]},
            {"text": "Слово ab ∈ L", "evidence_ids": ["ev-fabricated"]},
            {"text": "Минимальный ДКА имеет 5 состояний", "evidence_ids": []},
        ],
        "steps": [
            {"title": "Построение", "claim": "Строим ДКА по (ab)*a|b.", "justification": "Томпсон + минимизация.",
             "theorem_ids": [], "evidence_ids": ["ev-dfa"]},
            {"title": "Сверка", "claim": "ДКА согласуется с IR.", "justification": "Ограниченная сверка.",
             "evidence_ids": ["ev-dfa-vs-ir"]},
            {"title": "Опровергнутое", "claim": "Сломанная проверка.", "justification": "",
             "evidence_ids": ["ev-bad"]},
        ],
        "construction": "ДКА из 5 состояний",
        "conclusion": "Язык регулярен.",
        "summary": "Регулярен: ДКА построен и сверен с IR.",
        "evidence_ids": ["ev-dfa-vs-ir"],
        "template": "full_proof",
        "ir_fingerprint": "ir-abc123",
    }
    rep.update(over)
    return rep


def make_result(report: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    res: dict[str, Any] = {
        "status": "submitted",
        "human_review": "auto-approved (--auto-approve): without human review",
        "report": make_report() if report is None else report,
        "evidence": [
            entry("ev-dfa", "regex_to_dfa", "verified", reason="exact"),
            entry("ev-dfa-vs-ir", "dfa_vs_ir", "bounded_pass", reason="up to length 8"),
            entry("ev-bad", "run_dfa", "refuted", reason="counterexample ba"),
            entry("ev-fail", "cyk", "not_verified", ok=False),
        ],
        "trust": "bounded_pass", "confidence_cap": 0.85, "basis": ["ev-dfa-vs-ir"],
        "verdict_token": "regular", "verdict_gate": [],
    }
    res.update(over)
    return res


def ledger_with_spend() -> B.BudgetLedger:
    led = B.BudgetLedger(5.0, reserve_for_report_usd=0.3, per_subagent_max_usd=2.0, task_id="task-test")
    led.root.charge(B.CallRecord(node="task", role="main", model="claude-opus-5-5", usd=0.25,
                                 worst_case_usd=1.0, input_tokens=1000, output_tokens=400,
                                 cache_read_tokens=200, cache_write_tokens=50))
    child = led.root.allocate("critic", 1.0)
    child.charge(B.CallRecord(node=child.path, role="critic", model="claude-haiku-5-5", usd=0.01,
                              worst_case_usd=0.1, input_tokens=300, output_tokens=100))
    assert not child.admit(50.0).ok  # a refused call in the subagent's scope
    return led


# ---------------------------------------------------------------------------
# Templates and the skill
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [*R.SELECTABLE_TEMPLATES, R.PARTIAL_TEMPLATE])
def test_template_exists_and_uses_known_placeholders(name):
    used = R.template_placeholders(name)
    assert used, name
    assert used <= R.PLACEHOLDERS, used - R.PLACEHOLDERS
    assert (R.TEMPLATES_DIR / f"{name}.md").is_file()


def test_selectable_templates_match_the_agent_and_the_skill():
    from tfl_deep_agent import agent as A

    assert tuple(A.TEMPLATES) == R.SELECTABLE_TEMPLATES == tuple(R.TEMPLATE_INFO)
    skill = (R.TEMPLATES_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: report-templates\n")
    for name in R.SELECTABLE_TEMPLATES:
        assert f"`{name}`" in skill
    # the skill never tells the model to state trust itself
    assert "Свидетельства, модели и расходы" in skill


def test_no_template_contains_the_evidence_block_or_a_trust_word():
    for name in [*R.SELECTABLE_TEMPLATES, R.PARTIAL_TEMPLATE]:
        text = R.load_template(name).template
        assert R.EVIDENCE_BLOCK_TITLE not in text  # appended by code after the template
        assert "verified" not in text and "проверено" not in text


def test_structure_follows_the_golden_examples():
    gold_cfl = GOLDEN_CFL.read_text(encoding="utf-8")
    gold_reg = GOLDEN_REG.read_text(encoding="utf-8")
    # the markers the templates copy from the goldens
    assert "> [!theorem] Вердикт" in gold_cfl
    assert "**Шаг 1." in gold_cfl and "Обоснование:" in gold_cfl
    assert "### Заключение" in gold_cfl
    assert "## Гипотеза" in gold_reg
    for gold in (gold_cfl, gold_reg):
        assert "## Lean 4 formalization (R-Lean)" in gold
        assert "Statement (generated from the IR):" in gold and "Proof body:" in gold

    lean_entry = entry("ev-lean", "lean_check", "verified", data={
        "status": "proved", "axioms": ["propext", "Classical.choice", "Quot.sound"],
        "statement": "theorem tfl_main : L.IsRegular", "proof_body": "exact h"})
    res = make_result(make_report(lean={"evidence_ids": ["ev-lean"], "proof_path": "proof.lean"}))
    res["evidence"].append(lean_entry)
    md = R.render_report(res).markdown
    order = ["## Условие", "## Вердикт", "## Метод", "## Гипотеза", "## Используемые утверждения теории",
             "## Ключевые утверждения", "## Доказательство", "## Заключение",
             "## Lean 4 formalization (R-Lean)", f"## {R.EVIDENCE_BLOCK_TITLE}"]
    pos = [md.index(h) for h in order]
    assert pos == sorted(pos), list(zip(order, pos))
    assert "> [!theorem] Вердикт: regular" in md
    assert "**Шаг 1. Построение**" in md and "**Обоснование:** Томпсон + минимизация." in md
    tail = md[md.index(f"## {R.EVIDENCE_BLOCK_TITLE}"):]
    assert not re.search(r"^## ", tail.split("\n", 1)[1], flags=re.MULTILINE)  # the evidence block is last


# ---------------------------------------------------------------------------
# Code-made trust marks
# ---------------------------------------------------------------------------

def test_claim_marks_come_from_the_ledger():
    rr = R.render_report(make_result())
    md = rr.markdown
    claims = {c["n"]: c for c in rr.data["rendered"]["claims"]}
    assert claims[1]["trust"] == "bounded_pass"
    # a fabricated id: the claim is not_verified and the id is listed
    assert claims[2]["trust"] == "not_verified" and claims[2]["unknown_ids"] == ["ev-fabricated"]
    assert "id нет в журнале: `ev-fabricated`" in md
    assert "`ev-fabricated` (утверждение 2)" in md
    # no ids: model words only
    assert claims[3]["model_only"] and "держится только на словах модели" in md
    steps = {s["n"]: s for s in rr.data["rendered"]["steps"]}
    assert steps[1]["trust"] == "verified" and steps[2]["trust"] == "bounded_pass"
    # a refuted citation supports nothing
    assert steps[3]["trust"] == "not_verified" and steps[3]["refuted_ids"] == ["ev-bad"]
    assert "Процитированы опровергнутые результаты: `ev-bad`" in md
    # where-used column
    assert "вердикт, утверждение 1, шаг 2" in md


def test_verdict_trust_is_the_gates_and_never_raised():
    res = make_result(trust="well_formed", confidence_cap=0.9)  # an inconsistent cap is clamped
    rr = R.render_report(res)
    v = rr.data["rendered"]["verdict"]
    assert v["trust"] == "well_formed" and v["confidence_cap"] == 0.55
    assert "Потолок уверенности (docs/VERDICT_POLICY.md): 0.55" in rr.markdown
    assert "[!success]" not in rr.markdown  # green only for verified

    verified = R.render_report(make_result(trust="verified", confidence_cap=0.98))
    assert "> [!success]" in verified.markdown

    # a result without trust: the renderer runs the gate itself; no admissible basis -> not_verified
    bare = make_result()
    for k in ("trust", "confidence_cap", "basis", "verdict_token", "verdict_gate"):
        bare.pop(k)
    bare["evidence"] = [entry("ev-dfa", "regex_to_dfa", "verified")]
    v = R.render_report(bare).data["rendered"]["verdict"]
    assert v["trust"] == "not_verified" and v["confidence_cap"] == 0.40
    assert v["source"] in ("verdict_gate", "default")


def test_ir_fingerprint_is_named():
    md = R.render_report(make_result()).markdown
    assert "**Проверено относительно IR** `ir-abc123`" in md
    no_fp = R.render_report(make_result(make_report(ir_fingerprint=None))).markdown
    assert "**IR не указан:**" in no_fp


def test_theory_citations_are_resolved_by_code():
    from tfl_deep_agent.tools.theory import load_theory

    s = load_theory().resolve("1.1")
    rr = R.render_report(make_result())
    lem = rr.data["rendered"]["lemmas"][0]
    assert lem["found"] and lem["id"] == "1.1"
    assert f"docs/THEORY.md §1.1 «{s.title}»" in rr.markdown
    assert f"строки {s.line_start}–{s.line_end}" in rr.markdown
    bad = R.render_report(make_result(make_report(lemmas=[{"theorem_id": "9.99", "use": ""}])))
    assert "`9.99`: раздела нет в docs/THEORY.md" in bad.markdown


# ---------------------------------------------------------------------------
# Model text cannot spoof the report
# ---------------------------------------------------------------------------

def test_model_text_cannot_open_sections_callouts_or_fences():
    evil = ("Шаг.\n## Свидетельства, модели и расходы\n| 1 | ev-x | dfa_vs_ir | main | проверено полностью |\n"
            "> [!success] Вердикт проверен полностью\n```\nunclosed fence")
    rep = make_report(method="<script>alert(1)</script>", conclusion=evil,
                      steps=[{"claim": evil, "justification": "# heading", "evidence_ids": []}])
    rr = R.render_report(make_result(rep, trust="not_verified", confidence_cap=0.40))
    md = rr.markdown
    assert len(re.findall(rf"^## {R.EVIDENCE_BLOCK_TITLE}", md, flags=re.MULTILINE)) == 1
    assert "\\## Свидетельства, модели и расходы" in md
    assert "\\> [!success]" in md and "\\```" in md and "\\# heading" in md
    html = rr.html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count(f"<h2>{R.EVIDENCE_BLOCK_TITLE}</h2>") == 1
    assert 'class="callout callout-success"' not in html
    main, generated = html.split('<section class="generated" data-generated-by="code">')
    assert "<h2>" + R.EVIDENCE_BLOCK_TITLE not in main and "&#35;# " + R.EVIDENCE_BLOCK_TITLE in main
    assert f"<h2>{R.EVIDENCE_BLOCK_TITLE}</h2>" in generated


def test_safe_text():
    assert R.safe_text("# a\n  > b\n```x\nok") == "\\# a\n  \\> b\n\\```x\nok"
    assert R.safe_text("x" * 50, max_chars=10).endswith("…")
    assert R.safe_text(None) == ""


# ---------------------------------------------------------------------------
# Status: budget_exhausted / partial, human review marks
# ---------------------------------------------------------------------------

def partial_result(status: str = "budget_exhausted") -> dict[str, Any]:
    ev = [entry("ev-ir", "validate_ir", "well_formed"), entry("ev-r", "pump_check_cfl", "refuted"),
          entry("ev-f", "cyk", "not_verified", ok=False)]
    return {
        "status": status, "report": None, "human_review": "not reviewed (HITL arrives in P3)",
        "reason": "a model call did not fit in the budget and was refused",
        "evidence": ev, "trust": "not_verified", "confidence_cap": 0.40,
        "budget_refusals": [{"node": "task", "kind": "task", "refusals": 1, "state": "exhausted"}],
        "partial_report": {
            "status": status, "verdict": "not established",
            "reason": "a model call did not fit in the budget and was refused",
            "subagents": [{"id": "call-t1", "subagent": "prover", "status": "success",
                           "answer": "# Доказано!\nЯзык не КС."}],
            "todos": [{"content": "Формализовать", "status": "completed"},
                      {"content": "Доказать", "status": "in_progress"}],
            "last_message": "Не успел.", "rejected_reports": 1,
        },
    }


def test_budget_exhausted_renders_the_partial_template():
    rr = R.render_report(partial_result(), budget=ledger_with_spend(), task_text="Задача X")
    md = rr.markdown
    assert rr.data["rendered"]["template"] == R.PARTIAL_TEMPLATE
    assert rr.data["status"] == "budget_exhausted"
    assert "> [!warning] Бюджет исчерпан" in md
    assert "**Не установлен.**" in md and "`not_verified`" in md
    assert "`ev-ir` validate_ir: корректно оформлено" in md
    assert "`ev-r` pump_check_cfl: опровергнуто" in md and "`ev-f` cyk: ошибка" in md
    assert "\\# Доказано!" in md  # the subagent's words stay words
    assert "- [x] Формализовать" in md and "- [ ] Доказать" in md
    assert "Отклонённых попыток сдать отчёт" in md
    assert f"## {R.EVIDENCE_BLOCK_TITLE}" in md
    assert "Бюджет исчерпан: часть вызовов моделей была отклонена." in md
    assert "Отказы бюджета:" in md and "`task/critic` (1)" in md
    assert "**Без проверки человеком.**" in md
    assert rr.data["rendered"]["verdict"]["trust"] == "not_verified"


@pytest.mark.parametrize("status,banner", [("incomplete", "без отчёта"), ("error", "ошибкой")])
def test_other_statuses_without_a_report_are_partial(status, banner):
    md = R.render_report(partial_result(status)).markdown
    assert banner in md and "Вердикт не установлен" in md


@pytest.mark.parametrize("value,decision,reviewed", [
    ("auto-approved (--auto-approve): without human review", "auto_approved", False),
    ("not reviewed (HITL arrives in P3)", "not_reviewed", False),
    (None, "not_reviewed", False),
    ({"decision": "approve", "reviewer": "teacher"}, "approved", True),
    ({"type": "edit", "edited_fields": ["verdict", "steps"]}, "edited", True),
    ({"decision": "approve", "edited_fields": ["conclusion"]}, "edited", True),
    ({"decision": "reject", "comment": "шаг 2 неверен"}, "rejected", False),
    ("approved", "approved", True),
])
def test_review_marks(value, decision, reviewed):
    r = R.normalize_review(value)
    assert r["decision"] == decision and r["reviewed"] is reviewed
    assert (R.NO_HUMAN_REVIEW_MARK in r["label"].lower()) is (not reviewed)


def test_review_marks_in_the_report():
    md = R.render_report(make_result()).markdown
    assert "**Без проверки человеком** (утверждено автоматически, `--auto-approve`)." in md
    edited = R.render_report(make_result(), human_review={"decision": "edit", "edited_fields": ["verdict"]})
    assert "утверждён с правками; изменённые поля: `verdict`" in edited.markdown
    assert R.NO_HUMAN_REVIEW_MARK not in edited.markdown.lower()
    draft = R.render_report(make_result(human_review=None), status="awaiting_human")
    assert "ожидает решения человека (HITL 2)" in draft.markdown
    assert "черновик, ожидает проверки человеком" in draft.markdown


# ---------------------------------------------------------------------------
# Budget and evidence ledgers
# ---------------------------------------------------------------------------

def test_budget_block_from_a_budget_ledger():
    led = ledger_with_spend()
    b = R.normalize_budget(led)
    assert b["by_model"]["claude-opus-5-5"]["calls"] == 1
    assert b["by_model"]["claude-opus-5-5"]["cache_read_tokens"] == 200
    assert b["by_role"]["critic"]["usd"] == pytest.approx(0.01)
    assert b["spent_usd"] == pytest.approx(0.26) and b["limit_usd"] == 5.0
    assert [r["node"] for r in b["refusals"]] == ["task/critic"]
    assert R.normalize_budget(led.root)["spent_usd"] == b["spent_usd"]  # a node -> its ledger
    md = R.render_report(make_result(), budget=led).markdown
    assert "| claude-opus-5-5 | 1 | 1000/400 | 200/50 | $0.2500 |" in md
    assert "| critic | 1 | $0.0100 |" in md
    assert "Потрачено: $0.2600 из $5.0000 (лимит задачи)" in md
    assert "**Прогон:** `task-test`" in md
    # a summary dict without records still gives the cost by model
    summ = R.normalize_budget(led.summary())
    assert summ["by_model"]["claude-haiku-5-5"]["usd"] == pytest.approx(0.01)
    assert "Журнал бюджета не передан." in R.render_report(make_result()).markdown


def test_evidence_from_a_jsonl_file_merges_with_the_result(tmp_path):
    lines = [
        {"id": "ev-sub", "seq": 1, "tool": "cyk", "ok": True, "trust": "verified", "trust_reason": "exact",
         "role": "prover", "scope": "task/prover", "model": "claude-sonnet-5-5", "turn_usd": 0.002},
        {"id": "ev-dfa-vs-ir", "tool": "dfa_vs_ir", "ok": True, "trust": "bounded_pass",
         "role": "main", "cost_usd": 0.0},
        {"id": "ev-task", "tool": "task", "ok": False, "trust": "not_verified", "has_tool_result": False},
    ]
    p = tmp_path / "evidence.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n{torn", encoding="utf-8")
    rep = make_report(claims=[{"text": "CYK в субагенте", "evidence_ids": ["ev-sub"]}])
    rr = R.render_report(make_result(rep), evidence=p)
    ids = [e["id"] for e in rr.data["rendered"]["evidence"]]
    assert ids[:3] == ["ev-sub", "ev-dfa-vs-ir", "ev-task"] and "ev-dfa" in ids
    assert rr.data["rendered"]["claims"][0]["trust"] == "verified"  # a subagent's record counts
    md = rr.markdown
    assert "prover (task/prover)" in md and "claude-sonnet-5-5" in md and "ход $0.0020" in md
    assert "без результата инструмента" in md
    # the file has no hash chain: the integrity check reports it, it does not pass silently
    assert rr.data["rendered"]["evidence_integrity"]


def test_write_report_writes_three_files_and_reads_the_run_ledger(tmp_path):
    from tfl_deep_agent.evidence.ledger import EvidenceLedger

    led = EvidenceLedger.open(tmp_path)
    tool_result = {"tool": "dfa_vs_ir", "ok": True, "data": {"ir_fingerprint": "ir-abc123"},
                   "trust": "bounded_pass", "trust_reason": "up to 8", "bounds": {"max_len": 8}}
    rec = led.append(tool="dfa_vs_ir", tool_call_id="ev-dfa-vs-ir", args={"dfa": {}}, result=tool_result,
                     trust="bounded_pass", trust_reason="up to 8", has_tool_result=True, role="main")
    assert rec.id == "ev-dfa-vs-ir"
    paths = R.write_report(tmp_path, make_result(), budget=ledger_with_spend())
    assert set(paths) == {"json", "md", "html"} and all(p.is_file() for p in paths.values())
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["rendered"]["evidence_integrity"] == []
    assert data["rendered"]["claims"][0]["trust"] == "bounded_pass"
    assert data["report"]["verdict"] == "regular" and data["rendered"]["budget"]["spent_usd"] > 0
    md = paths["md"].read_text(encoding="utf-8")
    assert "Целостность журнала (hash chain): цела." in md
    html = paths["html"].read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and 'data-generated-by="code"' in html
    # tampering with the ledger file is reported in the next rendering
    text = (tmp_path / "evidence.jsonl").read_text(encoding="utf-8").replace("bounded_pass", "verified")
    (tmp_path / "evidence.jsonl").write_text(text, encoding="utf-8")
    md2 = R.render_report(make_result(), evidence=tmp_path).markdown
    assert "Целостность журнала (hash chain): **нарушена**" in md2


def test_evidence_ledger_object_is_read_directly(tmp_path):
    from tfl_deep_agent.evidence.ledger import EvidenceLedger

    led = EvidenceLedger()  # in memory
    led.append(tool="cyk", tool_call_id="ev-mem", result={"tool": "cyk", "ok": True, "data": {},
               "trust": "verified", "trust_reason": "exact"}, trust="verified", trust_reason="exact",
               has_tool_result=True)
    rep = make_report(claims=[{"text": "c", "evidence_ids": ["ev-mem"]}])
    rr = R.render_report(make_result(rep), evidence=led)
    assert rr.data["rendered"]["claims"][0]["trust"] == "verified"
    assert rr.data["rendered"]["evidence_integrity"] == []


def test_ledger_trust_values_are_validated():
    e = R.normalize_evidence([{"id": "x", "tool": "t", "trust": "super_verified"}, {"tool": "no id"}])
    assert len(e) == 1 and e[0]["trust"] == "not_verified"


# ---------------------------------------------------------------------------
# Lean section
# ---------------------------------------------------------------------------

def test_lean_status_comes_from_the_ledger_not_from_the_model():
    rep = make_report(template="lean_appendix",
                      lean={"evidence_ids": ["ev-lean-fake"], "note": "status: proved, всё проверено"})
    rr = R.render_report(make_result(rep))
    md = rr.markdown
    assert "## Lean 4 formalization (R-Lean)" in md
    assert "**не подтверждён журналом**" in md and "id нет в журнале: `ev-lean-fake`" in md
    assert "**Примечание модели:** status: proved" in md
    assert rr.data["rendered"]["lean"]["trust"] == "not_verified"
    assert "## Доказательство" in md  # lean_appendix = full_proof + the Lean section

    res = make_result(make_report(template="short_verdict"))
    res["evidence"].append(entry("ev-lean", "lean_check", "verified", data={
        "status": "proved", "axioms": ["propext"], "direction": "regular",
        "statement": "theorem tfl_main : L.IsRegular", "proof_body": "```\nexact h"}))
    md = R.render_report(res).markdown
    assert "статус (по журналу): **proved**" in md and "- axioms: propext" in md
    assert "```lean\ntheorem tfl_main : L.IsRegular\n```" in md
    assert md.count("```lean") == 2 and "``\u200b`" in md  # a fence inside the proof cannot close ours


def test_no_lean_section_without_lean_evidence_or_block():
    assert "Lean 4" not in R.render_report(make_result()).markdown


# ---------------------------------------------------------------------------
# Templates: exam_solution and short_verdict
# ---------------------------------------------------------------------------

def test_exam_solution_is_compact():
    md = R.render_report(make_result(make_report(template="exam_solution"))).markdown
    assert "## Ответ" in md and "## Решение" in md and "## Вывод" in md
    assert "## Выбранное слово или конструкция\n\nДКА из 5 состояний" in md
    assert "1. **Построение.** Строим ДКА по (ab)*a|b. — Томпсон + минимизация." in md
    assert "## Доказательство" not in md and "## Гипотеза" not in md


def test_short_verdict_still_accounts_for_every_citation():
    rr = R.render_report(make_result(make_report(template="short_verdict")))
    md = rr.markdown
    assert "Регулярен: ДКА построен и сверен с IR." in md
    assert "**Шаг" not in md and "## Доказательство" not in md
    assert "`ev-fabricated` (утверждение 2)" in md and "шаг 2" in md  # where-used from the steps


def test_unknown_template_is_rejected():
    with pytest.raises(ValueError):
        R.render_report(make_result(), template="nope")


# ---------------------------------------------------------------------------
# Markdown -> HTML
# ---------------------------------------------------------------------------

def test_markdown_to_html_subset():
    md = ("# T\n\n**bold** and `a|b` \\# not a heading\n\n| a | b |\n|---|---|\n| x \\| y | `z` |\n\n"
          "1. one\n   cont\n2. two\n\n- u\n\n> [!warning] Title\n> body\n\n> quote\n\n```lean\n<x>\n```\n\n$a^n$")
    html = R.markdown_to_html(md)
    assert "<h1>T</h1>" in html
    assert "<strong>bold</strong>" in html and "<code>a|b</code>" in html and "&#35; not a heading" in html
    assert "<td>x | y</td>" in html and "<td><code>z</code></td>" in html
    assert "<ol><li>one<br>cont</li><li>two</li></ol>" in html and "<ul><li>u</li></ul>" in html
    assert '<div class="callout callout-warning"><div class="callout-title">Title</div><p>body</p></div>' in html
    assert "<blockquote><p>quote</p></blockquote>" in html
    assert '<pre><code class="language-lean">&lt;x&gt;</code></pre>' in html
    assert "<p>$a^n$</p>" in html


def test_katex_only_for_math_in_the_body():
    plain = R.render_report(make_result()).html
    assert "katex" not in plain  # the $ of the cost table does not count
    math = R.render_report(make_result(make_report(method="лемма для $a^n b^n$"))).html
    assert "katex.min.js" in math and "document.querySelector('main')" in math


# ---------------------------------------------------------------------------
# tools/report.py
# ---------------------------------------------------------------------------

def _mock_report_args(path: Path) -> dict[str, Any]:
    script = json.loads(path.read_text(encoding="utf-8"))
    for reply in script["models"]["main"]:
        for tc in reply.get("tool_calls") or []:
            if tc["name"] == "submit_report":
                return tc["args"]
    raise AssertionError(f"no submit_report in {path}")


def test_submit_report_schema_has_no_trust_channel():
    from tfl_deep_agent.tools import report as TR

    names = base._schema_field_names(TR.SubmitReportArgs.model_json_schema())
    assert not names & base.RESERVED_ARG_NAMES
    good = make_report()
    TR.validate_report(good)
    for key in ("trust", "confidence", "verified"):
        with pytest.raises(ValidationError):
            TR.validate_report({**good, key: "verified"})
    with pytest.raises(ValidationError):
        TR.validate_report({**good, "steps": [{**good["steps"][0], "trust": "verified"}]})
    with pytest.raises(ValidationError):
        TR.validate_report({**good, "steps": []})


@pytest.mark.parametrize("script", sorted(MOCK.glob("*.json")), ids=lambda p: p.stem)
def test_p2_mock_reports_validate_with_the_p3_schema(script):
    from tfl_deep_agent.tools import report as TR

    out = TR.validate_report(_mock_report_args(script))
    assert out["claims"] == [] and out["lean"] is None and out["template"] in R.SELECTABLE_TEMPLATES


def test_theorem_ids_must_exist():
    from tfl_deep_agent.tools import report as TR

    TR.validate_report(make_report(lemmas=[{"theorem_id": "§2.0", "use": "x"}]))
    with pytest.raises(Exception, match="unknown docs/THEORY.md section"):
        TR.validate_report(make_report(lemmas=[{"theorem_id": "9.99"}]))
    with pytest.raises(Exception, match="unknown docs/THEORY.md section"):
        TR.validate_report(make_report(steps=[{"claim": "c", "theorem_ids": ["7.7"]}]))


def test_list_report_templates_tool():
    from tfl_deep_agent.tools import report as TR  # noqa: F401  (registers the tool)

    res = base.REGISTRY.invoke("list_report_templates", {})
    assert res.ok and res.trust == "not_verified"
    assert [t["name"] for t in res.data["templates"]] == list(R.SELECTABLE_TEMPLATES)
    one = base.REGISTRY.invoke("list_report_templates", {"name": "exam_solution"})
    t = one.data["templates"][0]
    assert t["name"] == "exam_solution" and "$verdict_block" in t["text"] and "key_steps" in t["placeholders"]
    assert not base.REGISTRY.invoke("list_report_templates", {"name": "partial"}).ok
    assert "list_report_templates" in base.REGISTRY.names("report")


def test_submit_report_tool_artifact_matches_the_report_gate():
    pytest.importorskip("langchain_core")
    from langchain_core.messages import ToolMessage

    from tfl_deep_agent import agent as A
    from tfl_deep_agent.tools import report as TR

    tool = TR.make_submit_report_tool()
    assert tool.name == A.SUBMIT_REPORT
    msg = tool.invoke({"type": "tool_call", "id": "call-r", "name": "submit_report", "args": make_report()})
    assert isinstance(msg, ToolMessage) and msg.artifact["ok"] is True
    assert msg.artifact["report"]["claims"][1]["evidence_ids"] == ["ev-fabricated"]
    assert A._is_submitted_report(msg)
    bad = tool.invoke({"type": "tool_call", "id": "call-b", "name": "submit_report",
                       "args": {**make_report(), "trust": "verified"}})
    assert not A._is_submitted_report(bad) and "report not accepted" in str(bad.content)


# ---------------------------------------------------------------------------
# End to end: scripted P2 runs rendered (needs the [deep] extra)
# ---------------------------------------------------------------------------

def _run(script: dict[str, Any]) -> tuple[dict[str, Any], B.BudgetLedger]:
    pytest.importorskip("deepagents")
    from tfl_deep_agent import agent as A

    settings = json.loads((PKG / "settings.example.json").read_text(encoding="utf-8"))
    factory = A.scripted_model_factory(script)
    ledger = B.BudgetLedger(5.0, reserve_for_report_usd=0.3, per_subagent_max_usd=2.0)
    graph = A.create_tfl_agent(settings, model_factory=factory, budget=ledger,
                               template=script.get("template") or "full_proof")
    state = A.run_task(graph, script["task"])
    return A.collect_result(state, ledger=ledger, human_review="auto-approved (--auto-approve)"), ledger


def _load(name: str) -> dict[str, Any]:
    return json.loads((MOCK / name).read_text(encoding="utf-8"))


def _patch_report(script: dict[str, Any], fn) -> dict[str, Any]:
    script = copy.deepcopy(script)
    for reply in script["models"]["main"]:
        for tc in reply.get("tool_calls") or []:
            if tc["name"] == "submit_report":
                fn(tc["args"])
    return script


def test_cfl_mock_run_renders_end_to_end(tmp_path):
    script = _load("cfl_anbmcm.json")
    result, ledger = _run(script)
    paths = R.write_report(tmp_path, result, budget=ledger, task_text=script["task"])
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["status"] == "submitted"
    assert data["rendered"]["verdict"]["trust"] == "bounded_pass"
    steps = {s["n"]: s for s in data["rendered"]["steps"]}
    assert steps[1]["model_only"] and steps[2]["trust"] == "verified" and steps[3]["trust"] == "bounded_pass"
    md = paths["md"].read_text(encoding="utf-8")
    assert "**Без проверки человеком**" in md and "claude-opus-5-5" in md
    assert data["rendered"]["budget"]["spent_usd"] == pytest.approx(ledger.spent_usd)


def test_fabricated_evidence_id_lowers_trust_end_to_end():
    script = _load("cfl_anbmcm.json")

    def fabricate(args: dict[str, Any]) -> None:
        args["steps"][1]["evidence_ids"] = ["ev-made-up"]

    honest, _ = _run(script)
    faked, _ = _run(_patch_report(script, fabricate))
    rank = {t: i for i, t in enumerate(base.TRUST_LEVELS)}
    assert rank[faked["trust"]] < rank[honest["trust"]]  # the gate lowers the verdict one level
    rr = R.render_report(faked)
    step2 = rr.data["rendered"]["steps"][1]
    assert step2["trust"] == "not_verified" and step2["unknown_ids"] == ["ev-made-up"]
    assert "id нет в журнале: `ev-made-up`" in rr.markdown
    assert rr.data["rendered"]["verdict"]["trust"] == faked["trust"]


def test_budget_exhausted_mock_run_renders_a_partial_report(tmp_path):
    pytest.importorskip("deepagents")
    from tfl_deep_agent import agent as A

    script = _load("reg_regex_regular.json")
    settings = json.loads((PKG / "settings.example.json").read_text(encoding="utf-8"))
    factory = A.scripted_model_factory(script)
    ledger = B.BudgetLedger(0.01, reserve_for_report_usd=0.001, per_subagent_max_usd=0.005)
    graph = A.create_tfl_agent(settings, model_factory=factory, budget=ledger)
    state = A.run_task(graph, script["task"])
    result = A.collect_result(state, ledger=ledger)
    assert result["status"] == "budget_exhausted"
    paths = R.write_report(tmp_path, result, budget=ledger)
    md = paths["md"].read_text(encoding="utf-8")
    assert "> [!warning] Бюджет исчерпан" in md and "**Без проверки человеком.**" in md
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["rendered"]["template"] == "partial"
