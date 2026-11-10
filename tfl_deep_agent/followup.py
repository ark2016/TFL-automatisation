"""Follow-up messages: a finished run continues in the same conversation.

Terms: a *follow-up* is the process that answers one message; the *message* is the
text the human sent (``--message``, ``POST .../message``, the ``user_message`` event);
the *reply* is the agent's closing text.

Each follow-up starts from ``run.json`` ``stable_checkpoint_id``, the state of the
last finished segment, so a killed or failed one (dangling tool calls, a pending
interrupt on its branch) never poisons the next message. The report is copied to
``report_history/<n>/`` first. While a follow-up is in progress, ``run.json``
``followup_in_progress`` holds what restores the run, so a record that survives its
process means that process did not finish. A follow-up that did not finish leaves the
previous report, status, reviews and review mode in place, ``followup_error`` says why
(no ``followup_error``: it was cancelled), and the run takes the next message. The record
goes only after the report is back: a restore that fails keeps it, and can be repeated.

``run.json`` ``report_revision`` tracks the report object, not the files: a text
reply re-renders ``result.*`` (the budget section changes) and keeps the revision;
only a report that says something new gets a new one.

This module decides what a run allows and keeps the report's history. It imports
without the ``[deep]`` extra, so TFL Lab can ask it; the module that runs the agent
builds on it, never the other way round.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .budget_stop import budget_stop_on, per_day_usd
from .evidence.ledger import EvidenceLedger, cited_evidence_ids, lean_proof_side
from .run_store import (
    CHECKPOINT_FILE,
    FINAL_STATUSES,
    MAX_MESSAGE_CHARS,
    REPORT_HISTORY_DIR,
    RESULT_FILES,
    STATUS_AWAITING,
    UNANSWERED_REPLY,
    RunError,
    load_run,
    message_text,
    now,
    read_result,
    retry_on_sharing_violation,
    save_run,
)

if TYPE_CHECKING:
    from .middleware.budget import DailySpendLog

LEAN_REQUEST = (
    "Формально проверь ответ в Lean: поручи доказательство роли lean-formalizer. Если доказательство пройдёт "
    "(lean_check со статусом proved), отправь полный отчёт заново через submit_report и сошлись на id этой "
    "записи lean_check. Если не пройдёт, прямо скажи об этом и объясни, на чём оно остановилось; ответ и "
    "отчёт оставь без изменений."
)

#: the fixed messages a client may send by name (TFL Lab's button «Формально верифицировать (Lean)»)
PRESET_MESSAGES = {"lean": LEAN_REQUEST}

#: the chat's note on a message that ended with no model call
UNANSWERED_NOTE = "сообщение без ответа: бюджет не продлён, отчёт прежний"

#: why a run with a follow-up record that nothing is finishing takes no message
STUCK_REFUSAL = ("a follow-up is recorded as in progress; if its process is gone, abandon that follow-up first "
                 "(the previous report comes back)")

#: the language classes with no Lean path
_NO_LEAN_CLASSES = ("ll",)

#: what a stop inside the follow-up may switch; a follow-up that does not finish takes it back
_REVIEW_SWITCH_KEYS = ("review_mode", "auto_approve", "initial_review_mode", "approve_rest")


# ---------------------------------------------------------------------------
# What a run allows
# ---------------------------------------------------------------------------

def clean_message(message: Any) -> str:
    """The text of a follow-up message; :class:`RunError` when empty or too long."""
    text = str(message or "").strip()
    if not text:
        raise RunError("a follow-up message needs text")
    if len(text) > MAX_MESSAGE_CHARS:
        raise RunError(f"the message is too long: {len(text)} characters, at most {MAX_MESSAGE_CHARS}")
    return text


def abandon_command(run_dir: str | Path) -> str:
    """The command that drops the follow-up in progress of ``run_dir``."""
    return f"python -m tfl_deep_agent --resume {run_dir} --abandon"


def stuck_followup(run: Mapping[str, Any]) -> bool:
    """A follow-up is recorded as in progress and is not waiting at a stop: its process
    is working, or it is gone. Only ``--abandon`` or TFL Lab gets the run out of that."""
    return bool(run.get("followup_in_progress")) and run.get("status") != STATUS_AWAITING


def followup_refusal(run: Mapping[str, Any], run_dir: str | Path) -> str | None:
    """Why this run cannot take a follow-up now, or ``None`` when it can."""
    status = run.get("status")
    if status == STATUS_AWAITING:
        return "the run is waiting at a stop; decide on it first"
    if status not in FINAL_STATUSES:
        if stuck_followup(run):
            return STUCK_REFUSAL
        return f"the run is {status!r}; a follow-up message goes to a finished run ({', '.join(FINAL_STATUSES)})"
    if not (Path(run_dir) / CHECKPOINT_FILE).is_file():
        return f"no {CHECKPOINT_FILE}, so there is no conversation to continue"
    return None


def day_refusal(run: Mapping[str, Any], spend_log: DailySpendLog | None = None) -> str | None:
    """Why a live run's follow-up has nothing to spend today, or ``None``. A mock run and
    a run with no ``budget.per_day_usd`` have no such limit. ``spend_log``: default, the
    log of ``$TFL_DEEP_RUNS_DIR``."""
    per_day = per_day_usd(run)
    if run.get("mode") != "live" or per_day is None:
        return None
    if run.get("status") == "submitted" and budget_stop_on(run):
        return None  # an accepted report and somebody to ask: the day budget-stop question comes first
    if spend_log is None:
        from .middleware.budget import DailySpendLog

        spend_log = DailySpendLog()
    if spend_log.remaining(per_day) > 1e-9:
        return None
    return (f"the day's budget is used up ({spend_log.day_total():.2f} of {per_day:.2f} USD, budget.per_day_usd); "
            "a follow-up has nothing to spend today")


def _proved_in(result: Mapping[str, Any], report: Mapping[str, Any], run_dir: Path) -> bool:
    """Whether ``report`` cites a Lean proof the verdict gate counts for its verdict.
    ``result``: its result, which says how the gate ended on the report: a proof it
    counts gives ``verified``, whatever kept it lower (HITL 1 rejected, a contradiction,
    several IRs) leaves the button."""
    if result.get("trust") != "verified":
        return False
    cited = {i for i, _ in cited_evidence_ids(report)}
    try:
        records = EvidenceLedger.load(run_dir).records()
    except (OSError, ValueError):  # no ledger, or it cannot be read: nothing is known to be proved
        return False
    lang = str(report.get("language_class") or "")
    required = result.get("required_ir_fingerprint")
    direction = result.get("verdict_direction")
    return any(r.id in cited and r.has_tool_result
               and lean_proof_side(r.gate_entry(), lang, required_fingerprint=required) == direction
               for r in records)


def lean_refusal(run: Mapping[str, Any], run_dir: str | Path) -> str | None:
    """Why formal verification is not offered for this run, or ``None`` when it is.

    A run's trust does not hide it: a human asks for this button, and an answer can
    be ``verified`` by other tools with no Lean proof at all."""
    d = Path(run_dir)
    result = read_result(d) or {}
    report = result.get("report")
    if (run.get("result") or {}).get("status") != "submitted" or not isinstance(report, Mapping):
        return "no accepted report yet"
    if report.get("language_class") in _NO_LEAN_CLASSES:
        return "LL(k) has no Lean path"
    if _proved_in(result, report, d):
        return "this report already cites a proved lean_check"
    return None


# ---------------------------------------------------------------------------
# Starting and abandoning
# ---------------------------------------------------------------------------

def begin_followup(d: Path, run: dict[str, Any]) -> None:
    """Record the follow-up in progress in ``run`` (the report goes to its history; the
    caller saves ``run``). What the message may change starts over; the conversation
    continues from the stable checkpoint."""
    run["followup_in_progress"] = {
        "snapshot": snapshot_report(d), "started_at": now(), "pid": os.getpid(), "previous_status": run.get("status"),
        "previous_result": run.get("result"), "previous_error": run.get("error"),
        "previous_accepted_report": run.get("accepted_report"),
        "n_reviews": len(run.get("reviews") or []),
        "review_switch": {k: run[k] for k in _REVIEW_SWITCH_KEYS if k in run}}
    run.pop("followup_error", None)
    run.pop("budget_declined", None)  # a new message may ask about money again
    run["limits"] = {"model_calls": 0, "tool_calls": 0}
    run["pending"] = None
    run["status"] = "running"


def fail_followup(d: Path, run: dict[str, Any], error: str | None) -> None:
    """The follow-up in progress did not finish: back to the report and the evidence it is
    rated by, the status, the reviews and the review mode it started from (the caller saves ``run``). Money stays
    spent, and an extension the human granted stays granted. A restore that fails raises
    with ``run`` untouched, so its record stays and it can be repeated."""
    record = run["followup_in_progress"]
    restore_report(d, record["snapshot"])
    del run["followup_in_progress"]
    run["status"] = record["previous_status"]
    run["result"] = record.get("previous_result")
    run["error"] = record.get("previous_error")
    run["pending"] = None
    run.pop("draft", None)
    run["reviews"] = list(run.get("reviews") or [])[:int(record.get("n_reviews") or 0)]
    if record.get("previous_accepted_report"):
        run["accepted_report"] = record["previous_accepted_report"]
    else:
        run.pop("accepted_report", None)
    saved = record["review_switch"]
    for key in _REVIEW_SWITCH_KEYS:
        if key in saved:
            run[key] = saved[key]
        else:
            run.pop(key, None)
    if error:
        run["followup_error"] = error


def emit_abandoned(emitter: Any, status: str, error: str | None) -> None:
    """The events of a follow-up that did not finish, however it ended: the chat's note
    (the report stayed), then the end of the segment with the restored status."""
    emitter.emit("followup_abandoned", status=status, error=error)
    emitter.segment_end(status)


def pid_alive(pid: Any) -> bool:
    """Whether a process with this pid exists (``False`` for a missing or invalid pid)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def abandon_followup(run_dir: str | Path, *, error: str | None = None,
                     run: dict[str, Any] | None = None, check_alive: bool = False) -> str | None:
    """Drop the follow-up in progress; no process of the run may be alive. The report,
    the status, the reviews and the review mode are as before the message, nothing is
    pending, and the next message starts from the stable checkpoint. ``error``: why it did
    not finish (``None``: it was cancelled). ``run``: the caller's newer copy of
    ``run.json``, so the money a dying segment has counted is not lost. Returns the
    restored status, ``None`` when the run has no follow-up in progress. ``check_alive`` (the CLI's ``--abandon``): refuse with ``RunError`` while the pid recorded with the follow-up is alive. A restore that
    fails raises ``OSError`` and leaves the record, and the chat is told nothing."""
    d = Path(run_dir)
    current = load_run(d) if run is None else run
    if not current.get("followup_in_progress"):
        return None
    if check_alive:
        pid = current["followup_in_progress"].get("pid")
        if pid != os.getpid() and pid_alive(pid):
            raise RunError(f"{d}: a process with the pid {pid} recorded for the follow-up exists; if it is the "
                           "follow-up, wait for it; if the follow-up was killed and the pid now belongs to another "
                           "process, repeat with --force")
    fail_followup(d, current, error)
    save_run(d, current)
    try:
        from .progress import ProgressEmitter

        emit_abandoned(ProgressEmitter(d, run_id=current.get("run_id"),
                                       segment=len(current.get("segments") or []) or None),
                       str(current["status"]), error)
    except Exception:  # noqa: BLE001 - a chat note, never a failure of the cancel
        pass
    return str(current["status"])


# ---------------------------------------------------------------------------
# The report's history
# ---------------------------------------------------------------------------

def latest_snapshot(run_dir: str | Path) -> int:
    """The newest ``report_history/<n>``, 0 when there is none."""
    hist = Path(run_dir) / REPORT_HISTORY_DIR
    if not hist.is_dir():
        return 0
    return max((int(p.name) for p in hist.iterdir() if p.is_dir() and p.name.isdigit()), default=0)


def snapshot_report(run_dir: str | Path) -> str:
    """Copy ``result.*`` to a new ``report_history/<n>/``; its path (as text) relative to ``run_dir``."""
    d = Path(run_dir)
    dst = d / REPORT_HISTORY_DIR / str(latest_snapshot(d) + 1)
    dst.mkdir(parents=True)
    for name in RESULT_FILES:
        if (d / name).is_file():
            shutil.copy2(d / name, dst / name)
    return dst.relative_to(d).as_posix()


def restore_report(d: Path, snapshot: str) -> None:
    """``result.*`` back from ``snapshot``; a reader holding one open only delays it."""
    src = d / snapshot
    for name in RESULT_FILES:
        source, target = src / name, d / name
        if source.is_file():
            retry_on_sharing_violation(lambda s=source, t=target: shutil.copy2(s, t))
        else:
            retry_on_sharing_violation(lambda t=target: t.unlink(missing_ok=True))


def report_digest(report: Any) -> str | None:
    if not report:
        return None
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def report_changed(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    """Whether a follow-up changed what the report says (a run written before
    ``report_digest`` existed is compared by its verdict)."""
    prev = previous or {}
    keys = ("status", "verdict", "trust", "confidence_cap")
    if any(prev.get(k) != current.get(k) for k in keys):
        return True
    return bool(prev.get("report_digest")) and prev["report_digest"] != current.get("report_digest")


def last_reply(state: Mapping[str, Any]) -> str | None:
    """The agent's closing text of a follow-up: the last AI message after the last
    human one, when it calls no tools."""
    from langchain_core.messages import AIMessage, HumanMessage

    for msg in reversed(list(state.get("messages") or [])):
        if isinstance(msg, HumanMessage):
            return None
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None) or _is_unanswered(msg):
                return None
            return message_text(msg).strip() or None
    return None


def _is_unanswered(msg: Any) -> bool:
    return (getattr(msg, "additional_kwargs", None) or {}).get("tfl_budget") == UNANSWERED_REPLY


def unanswered_note(state: Mapping[str, Any]) -> str | None:
    """The chat's note when the follow-up ended with no model call
    (``budget_stop.declining_skips_the_call``), else ``None``."""
    from langchain_core.messages import AIMessage

    last = next((m for m in reversed(list(state.get("messages") or [])) if isinstance(m, AIMessage)), None)
    return UNANSWERED_NOTE if last is not None and _is_unanswered(last) else None


__all__ = [
    "LEAN_REQUEST", "PRESET_MESSAGES", "STUCK_REFUSAL", "UNANSWERED_NOTE", "abandon_command", "abandon_followup", "begin_followup", "clean_message",
    "day_refusal", "emit_abandoned", "fail_followup", "followup_refusal", "last_reply", "latest_snapshot",
    "lean_refusal", "report_changed", "report_digest", "restore_report", "snapshot_report", "stuck_followup",
    "unanswered_note",
]
