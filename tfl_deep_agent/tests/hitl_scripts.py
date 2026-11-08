"""Mock scripts and helpers for the HITL tests (test_hitl.py, test_e2e_mock.py).

Every script is a variant of ``examples/mock/ll_simple_ll1.json`` (the LL(1)
grammar S -> aAb | bBa; A -> aA | eps; B -> bB | eps), so the tools are the
real LL tools and the trust levels come from the library, not the script.
"""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tfl_deep_agent import agent as A
from tfl_deep_agent import hitl

PKG = Path(A.__file__).resolve().parent
ROOT = PKG.parent
MOCK = PKG / "examples" / "mock"
SETTINGS_EXAMPLE = PKG / "settings.example.json"
LL_SCRIPT = MOCK / "ll_simple_ll1.json"

REJECT_COMMENT = "Добавь в отчёт шаг с разбором слов aab и ba; черновик без него."
EDITED_TRANSCRIPTION = "S → aAb | bBa; A → aA | ε; B → bB | ε. Является ли эта грамматика LL(1)?"
INTAKE_TRANSCRIPTION = ("## Tasks\n1. S → aAb | bBa; A → aA | ε; B → bB | ε. Является ли грамматика LL(1)?\n\n"
                        "## Uncertain readings\n- в правиле для B символ «ε» мог быть «e»")


def base_script() -> dict[str, Any]:
    return copy.deepcopy(json.loads(LL_SCRIPT.read_text(encoding="utf-8")))


def _report(script: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(script["models"]["main"][-1])


def report_call(script: dict[str, Any], *, call_id: str, **changes: Any) -> dict[str, Any]:
    reply = _report(script)
    call = reply["tool_calls"][0]
    call["id"] = call_id
    call["args"].update(changes)
    return reply


def reject_cycle_script() -> dict[str, Any]:
    """HITL 2 reject -> fix -> approve: a draft report without the parse step,
    then (after the rejection) the full report."""
    s = base_script()
    full = _report(s)
    draft = report_call(s, call_id="call-report-draft",
                        summary="Черновик: таблица LL(1) без конфликтов.",
                        steps=full["tool_calls"][0]["args"]["steps"][:2])
    final = report_call(s, call_id="call-report-final")
    s["models"]["main"] = [*s["models"]["main"][:4], draft, final]
    return s


def hitl1_reject_script() -> dict[str, Any]:
    """HITL 1 reject -> second ir-builder call -> approve -> report."""
    s = base_script()
    main = s["models"]["main"]
    again = copy.deepcopy(main[1])
    again["tool_calls"][0]["id"] = "call-ir-builder-2"
    again["tool_calls"][0]["args"]["description"] += " Учти замечание человека."
    ib = s["models"]["ir_builder"]
    ib2 = copy.deepcopy(ib)
    ib2[0]["tool_calls"][0]["id"] = "ib-validate-2"
    ib2[1]["content"] = "Исправлено по замечанию: класс ll, k = 1. validate_ir: valid."
    s["models"]["main"] = [main[0], main[1], again, *main[2:]]
    s["models"]["ir_builder"] = [*ib, *ib2]
    return s


def no_ir_builder_script() -> dict[str, Any]:
    """The main agent never calls the ir-builder (nor validate_ir): it runs
    the oracles on its own IR and submits."""
    s = base_script()
    main = s["models"]["main"]
    s["models"]["main"] = [main[0], *main[2:]]
    s["models"]["ir_builder"] = []
    return s


def main_validates_script() -> dict[str, Any]:
    """The main agent builds the IR itself and checks it with validate_ir
    instead of asking the ir-builder."""
    s = base_script()
    main = s["models"]["main"]
    own = {"tool_calls": [{"name": "validate_ir", "id": "main-validate",
                           "args": {"cls": "ll", "ir": {"$def": "ir"}}}]}
    s["models"]["main"] = [main[0], own, *main[2:]]
    s["models"]["ir_builder"] = []
    return s


def second_ir_builder_in_prose_script() -> dict[str, Any]:
    """Like :func:`hitl1_reject_script`, but the second ir-builder call
    answers in prose without any validate_ir call."""
    s = hitl1_reject_script()
    s["models"]["ir_builder"] = [*s["models"]["ir_builder"][:2],
                                 {"content": "Исправлено по замечанию (без проверки validate_ir)."}]
    return s


def fabricated_ids_script() -> dict[str, Any]:
    """A report citing ids that no tool call produced."""
    s = base_script()
    steps = copy.deepcopy(_report(s)["tool_calls"][0]["args"]["steps"])
    steps[0]["evidence_ids"] = ["ev-first-follow", "ev-made-up-step"]
    s["models"]["main"][-1] = report_call(s, call_id="call-report", steps=steps,
                                          evidence_ids=["ev-ll-table", "ev-made-up-verdict"])
    return s


def fabricated_then_honest_script() -> dict[str, Any]:
    """:func:`fabricated_ids_script`'s report (refused by submit_report: its
    made-up ids are not in the ledger), then the honest one."""
    s = fabricated_ids_script()
    s["models"]["main"].append(report_call(base_script(), call_id="call-report-honest"))
    return s


def tool_names_by_id(script: dict[str, Any]) -> dict[str, str]:
    """Every scripted tool-call id -> its tool name (all roles)."""
    out: dict[str, str] = {}
    for replies in script["models"].values():
        for reply in replies:
            for tc in reply.get("tool_calls") or []:
                if tc.get("id"):
                    out[tc["id"]] = tc["name"]
    return out


def names_instead_of_ids(script: dict[str, Any], *, call_id: str = "call-report-names") -> dict[str, Any]:
    """The script's final report with every evidence id replaced by the name
    of the tool that produced it (the mistake submit_report refuses)."""
    names = tool_names_by_id(script)
    reply = _report(script)
    call = reply["tool_calls"][0]
    call["id"] = call_id
    args = call["args"]

    def swap(ids: list[str]) -> list[str]:
        return [names.get(i, i) for i in ids]

    args["evidence_ids"] = swap(args.get("evidence_ids") or [])
    for key in ("steps", "claims"):
        for item in args.get(key) or []:
            item["evidence_ids"] = swap(item.get("evidence_ids") or [])
    if isinstance(args.get("lean"), dict):
        args["lean"]["evidence_ids"] = swap(args["lean"].get("evidence_ids") or [])
    return reply


def reg_script() -> dict[str, Any]:
    return copy.deepcopy(json.loads((MOCK / "reg_regex_regular.json").read_text(encoding="utf-8")))


def reg_names_then_ids_script() -> dict[str, Any]:
    """reg (ab)*a|b: a report citing tool names (refused), then the honest one."""
    s = reg_script()
    s["models"]["main"] = [*s["models"]["main"][:-1], names_instead_of_ids(s), s["models"]["main"][-1]]
    return s


def always_names_script(times: int) -> dict[str, Any]:
    """reg: the model keeps citing tool names ``times`` times and never fixes them."""
    s = reg_script()
    bad = [names_instead_of_ids(s, call_id=f"call-names-{i}") for i in range(times)]
    s["models"]["main"] = [*s["models"]["main"][:-1], *bad]
    return s


def pdf_script() -> dict[str, Any]:
    """A PDF task: main delegates the transcription to intake (which reads
    /input/task.pdf with deepagents' read_file), then formalizes it."""
    s = base_script()
    main = s["models"]["main"]
    to_intake = {"tool_calls": [{"name": "task", "id": "call-intake", "args": {
        "subagent_type": "intake", "description": "Транскрибируй задачу из /input/task.pdf."}}]}
    s["models"]["main"] = [to_intake, *main[1:]]
    s["models"]["intake"] = [
        {"tool_calls": [{"name": "read_file", "id": "in-read", "args": {"file_path": "/input/task.pdf"}}]},
        {"content": INTAKE_TRANSCRIPTION},
    ]
    s["task"] = ""
    return s


def write_script(tmp_path: Path, script: dict[str, Any], name: str = "script.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return p


def example_settings() -> dict[str, Any]:
    return json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))


def make_pdf(pages: int = 1) -> bytes:
    import pypdf

    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def thread_messages(run_dir: str | Path) -> list[Any]:
    """The main thread's messages at the run's latest SQLite checkpoint, read
    by a fresh agent instance (what a new process resumes from)."""
    return list(hitl.run_state(run_dir).get("messages") or [])


def subprocess_env(runs_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "TFL_DEEP_RUNS_DIR": str(runs_dir)})
    env.pop("TFL_MODEL_OVERRIDE", None)
    env.pop("TFL_ALLOW_LIVE", None)
    return env


def tfl_deep(*args: str, env: dict[str, str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    """``python -m tfl_deep_agent ARGS`` in a new process (no TTY: stdin is /dev/null)."""
    return subprocess.run([sys.executable, "-m", "tfl_deep_agent", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, cwd=ROOT, env=env,
                          stdin=subprocess.DEVNULL, check=False)
