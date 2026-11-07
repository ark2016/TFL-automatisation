"""``tfl-deep`` command line.

    tfl-deep (TEXT | --file task.txt) [--save DIR] [--template T]
             [--mock-script FILE | --live] [--auto-approve] [--settings JSON]

Mock mode is the default: the agent runs its real loop and its real tools, but
every model reply is replayed from ``--mock-script`` (a JSON replay, see
``tfl_deep_agent/examples/mock/``); no request reaches the Anthropic API. A run
without ``--live`` and without a script is refused with a clear message.

``--live`` builds real ``ChatAnthropic`` models and spends money. It needs
``ANTHROPIC_API_KEY`` in the environment (this command never loads ``.env``).
Per the repository rules it is used only on the user's explicit request, with
a budget agreed beforehand.

P2 scope: text tasks (``TEXT`` or a ``.txt`` / ``.md`` file). PDF / image
intake, HITL dialogs and the template renderer arrive in P3; until then
``--save`` writes a minimal ``result.json`` / ``result.md`` and
``--auto-approve`` is only recorded in the result.

Exit codes: 0 report submitted, 1 the run ended without a report (partial
result written), 2 usage / setup error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

TEMPLATES = ["full_proof", "exam_solution", "short_verdict", "lean_appendix"]
TEXT_SUFFIXES = {".txt", ".md"}
FILE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tfl-deep",
        description="TFL Deep Agent: solve a Theory of Formal Languages task (REG / CFL / DCFL / LL(k)).",
    )
    p.add_argument("text", nargs="?", help="task statement as text")
    p.add_argument("--file", help="task file: .txt / .md (P2); .pdf / .png / .jpg / .webp arrive in P3")
    p.add_argument("--save", metavar="DIR", help="directory for result.{json,md}")
    p.add_argument("--template", default=None, choices=TEMPLATES,
                   help="report template (default: the mock script's, else full_proof)")
    p.add_argument("--live", action="store_true",
                   help="call the real Anthropic API (spends money; default is mock mode)")
    p.add_argument("--mock-script", metavar="FILE",
                   help="scripted model replay for a mock run (the default mode when --live is absent); "
                        "see tfl_deep_agent/examples/mock/")
    p.add_argument("--auto-approve", action="store_true",
                   help="approve both HITL points without asking (recorded in the result)")
    p.add_argument("--settings", metavar="JSON", help="settings file (default: tfl_deep_agent/settings.json)")
    return p


def _fail(msg: str) -> int:
    print(f"tfl-deep: error: {msg}", file=sys.stderr)
    return 2


def _read_task_file(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in FILE_SUFFIXES:
        raise ValueError(f"{p.name}: PDF / image intake arrives in phase P3; give the task as text for now")
    if suffix not in TEXT_SUFFIXES:
        raise ValueError(f"{p.name}: unsupported task file type {suffix or '(none)'}; "
                         f"use .txt / .md ({', '.join(sorted(FILE_SUFFIXES))} in P3)")
    if not p.is_file():
        raise ValueError(f"task file not found: {path}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"task file is empty: {path}")
    return text


def _load_settings(path: str | None) -> Any:
    """``settings.load_settings`` (defaults merged under the file; a missing
    default file means the defaults). An explicit ``--settings`` must exist."""
    from . import settings as settings_mod

    if path is not None and not Path(path).is_file():
        raise ValueError(f"settings file not found: {path}")
    try:
        return settings_mod.load_settings(path)
    except settings_mod.SettingsError as exc:
        problems = "; ".join(getattr(exc, "problems", None) or [])
        raise ValueError(f"{exc}{': ' + problems if problems else ''}") from None


def _render_markdown(result: dict[str, Any], task_text: str) -> str:
    report = result.get("report") or {}
    lines = ["# TFL Deep Agent — result", "", "## Условие", "", task_text, ""]
    if result["status"] != "submitted":
        lines += ["## Статус", "", f"**{result['status']}** — {result.get('reason', '')}", ""]
        if result.get("last_message"):
            lines += ["Последнее сообщение агента:", "", result["last_message"], ""]
        partial = result.get("partial_report") or {}
        if partial:
            lines += ["## Частичный отчёт (сгенерирован кодом)", "",
                      f"Вердикт: **{partial.get('verdict')}**, доверие `{partial.get('trust')}`, "
                      f"потолок confidence {partial.get('confidence_cap')}.", ""]
            if partial.get("established"):
                lines.append("Установлено инструментами:")
                lines += [f"- `{e['id']}` {e['tool']}: `{e['trust']}`" for e in partial["established"]]
                lines.append("")
            if partial.get("refuted"):
                lines.append("Опровергнуто инструментами:")
                lines += [f"- `{e['id']}` {e['tool']}" for e in partial["refuted"]]
                lines.append("")
            for sub in partial.get("subagents") or []:
                answer = " ".join(str(sub.get("answer") or "").split())[:300]
                lines.append(f"- субагент {sub.get('subagent')}: {answer}")
            if partial.get("subagents"):
                lines.append("")
    else:
        lines += [
            "## Вердикт", "",
            f"**{report.get('verdict')}** (класс `{report.get('language_class')}`), "
            f"доверие по свидетельствам вердикта: `{result.get('trust')}`, потолок confidence "
            f"{result.get('confidence_cap')}", "",
            f"Метод: {report.get('method')}", "",
        ]
        basis = ", ".join(result.get("basis") or []) or "нет допустимого основания"
        lines += [f"Токен вердикта по VERDICT_POLICY: `{result.get('verdict_token')}`; основание (выбрано кодом): "
                  f"{basis}", ""]
        if result.get("verdict_gate"):
            lines.append("Понижения (VERDICT_POLICY R1–R4):")
            lines += [f"- {g}" for g in result["verdict_gate"]]
            lines.append("")
        if report.get("summary"):
            lines += [report["summary"], ""]
        lines += ["## Доказательство", ""]
        for i, step in enumerate(result.get("steps") or [], 1):
            ids = ", ".join(step.get("evidence_ids") or []) or "—"
            lines.append(f"{i}. {step.get('claim')} — свидетельства: {ids} "
                         f"(сильнейшее: `{step.get('trust')}`)")
            if step.get("justification"):
                lines.append(f"   {step['justification']}")
            if step.get("unknown_ids"):
                lines.append(f"   несуществующие id: {', '.join(step['unknown_ids'])}")
        lines.append("")
    lines += ["## Свидетельства (сгенерировано кодом)", "", "| id | инструмент | trust | основание |",
              "|---|---|---|---|"]
    for ev in result.get("evidence") or []:
        reason = str(ev.get("trust_reason") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{ev['id']}` | {ev.get('tool')} | {ev.get('trust')} | {reason[:200]} |")
    lines += ["", f"Проверка человеком: {result.get('human_review')}", ""]
    return "\n".join(lines)


def save_result(result: dict[str, Any], task_text: str, save_dir: str | Path) -> list[Path]:
    """Write ``result.json`` and ``result.md`` (P2 minimal; P3's renderer adds
    the templates and ``result.html``)."""
    d = Path(save_dir)
    d.mkdir(parents=True, exist_ok=True)
    payload = {"task": task_text, **result}
    json_path = d / "result.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                         encoding="utf-8")
    md_path = d / "result.md"
    md_path.write_text(_render_markdown(result, task_text), encoding="utf-8")
    return [json_path, md_path]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.text and args.file:
        parser.error("give either TEXT or --file, not both")
    if args.live and args.mock_script:
        parser.error("--live and --mock-script exclude each other")
    if not args.text and not args.file and not args.mock_script:
        parser.error("give the task as TEXT or --file")

    if not args.live and not args.mock_script:
        return _fail(
            "mock mode (the default) needs --mock-script FILE: a scripted replay of the model's replies "
            "(examples: tfl_deep_agent/examples/mock/*.json). Nothing is sent to the API without --live; "
            "pass --live only with an agreed budget.")

    try:
        task_text = _read_task_file(args.file) if args.file else (args.text or "")
        settings = _load_settings(args.settings)
    except ValueError as exc:
        return _fail(str(exc))

    from . import agent as agent_mod

    script = None
    if args.mock_script:
        try:
            factory = agent_mod.scripted_model_factory(args.mock_script)
        except agent_mod.ScriptError as exc:
            return _fail(str(exc))
        script = factory.script
        if not task_text:
            task_text = str(script.get("task") or "").strip()
        if not task_text:
            return _fail("no task: give TEXT / --file, or put a 'task' into the mock script")
        mode = "mock"
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return _fail("--live needs ANTHROPIC_API_KEY in the environment (tfl-deep never loads .env)")
        factory = agent_mod.default_model_factory
        mode = "live"
        print("tfl-deep: LIVE mode: real Anthropic API calls, this spends money.", file=sys.stderr)

    template = args.template or (script or {}).get("template") or "full_proof"
    human_review = ("auto-approved (--auto-approve): without human review" if args.auto_approve
                    else "not reviewed (HITL arrives in P3)")
    from langgraph.checkpoint.memory import InMemorySaver

    from .middleware import budget as budget_mod

    # Budget: a live run charges the shared per-day log in the runs directory
    # and is refused when the task does not fit in the day. A mock run gets its
    # own ledger without the log (no money is spent): the scripted replies'
    # token estimates still go through BudgetMiddleware, so the path is the
    # same, and the numbers are reported as an estimate.
    try:
        if mode == "live":
            ledger = budget_mod.BudgetLedger.for_task(settings)
        else:
            b = settings.budget
            ledger = budget_mod.BudgetLedger(
                b.per_task_usd, reserve_for_report_usd=b.reserve_for_report_usd,
                per_subagent_max_usd=b.per_subagent_max_usd, spend_log=None, task_id="mock-run")
    except budget_mod.BudgetExceeded as exc:
        return _fail(f"budget: {exc}")

    # In-memory checkpoints keep the state of a run that fails half-way, so a
    # partial result can still be written. P3 replaces this with SQLite in the
    # run dir (resume in a new process).
    checkpointer = InMemorySaver()
    thread_id = "tfl-deep-cli"
    try:
        graph = agent_mod.create_tfl_agent(settings, model_factory=factory, template=template,
                                           checkpointer=checkpointer, budget=ledger)
    except (ValueError, TypeError) as exc:
        return _fail(f"cannot build the agent: {exc}")

    error: str | None = None
    state: dict[str, Any] = {"messages": []}
    try:
        state = agent_mod.run_task(graph, task_text, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 - any agent failure still yields a partial result
        prefix = "mock script" if isinstance(exc, agent_mod.ScriptError) else type(exc).__name__
        error = f"{prefix}: {exc}"
        try:
            state = dict(graph.get_state({"configurable": {"thread_id": thread_id}}).values)
        except Exception:  # noqa: BLE001
            state = {"messages": []}

    result = agent_mod.collect_result(state, human_review=human_review, ledger=ledger)
    result["mode"] = mode
    result["template"] = template
    result["budget_refusal"] = result.pop("budget", None)
    result["budget"] = {**ledger.summary(),
                        "note": ("live: charged to the per-day log" if mode == "live"
                                 else "mock: token estimates of the scripted replies; no money spent")}
    if not result["budget_refusal"]:
        result.pop("budget_refusal")
    if error:
        result["status"] = "error"
        result["reason"] = error
        if result.get("partial_report"):
            result["partial_report"]["status"] = "error"
            result["partial_report"]["reason"] = error
    if args.save:
        paths = save_result(result, task_text, args.save)
        print("tfl-deep: wrote " + ", ".join(str(p) for p in paths), file=sys.stderr)

    if result["status"] == "submitted":
        report = result["report"]
        print(f"verdict: {report['verdict']} ({report['language_class']}); evidence trust: "
              f"{result['trust']}; confidence cap {result['confidence_cap']}")
        return 0
    print(f"no report: {result['status']}: {result.get('reason', '')}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
