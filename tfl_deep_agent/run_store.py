"""The run directory's record: ``run.json``, its statuses, file names and exit codes.

The one place for what the agent, the CLI and TFL Lab must agree on. Pure stdlib,
so ``ui_server`` imports it at module level and the rest of TFL Lab keeps working
without the ``[deep]`` extra.

Three keys of ``run.json`` say who answers what. ``review_mode`` (:data:`REVIEW_MODES`)
is what decides which reviews wait for a human. ``auto_approve`` is its legacy mirror:
true exactly when ``review_mode == "auto"``, and it decides nothing. ``budget_stop`` is
the authority on whether the run asks for money, fixed when the run starts; no review
mode changes it.

While a follow-up is in progress, ``followup_in_progress`` holds what restores the run
(module ``followup``); ``followup_error`` says why the last one did not finish.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

RUN_FILE = "run.json"
CHECKPOINT_FILE = "checkpoints.sqlite"
REPORT_HISTORY_DIR = "report_history"
RESULT_FILES = ("result.json", "result.md", "result.html")

STATUS_AWAITING = "awaiting_human"
#: the statuses a run ends a segment with; a follow-up message is allowed in these
FINAL_STATUSES = ("submitted", "incomplete", "budget_exhausted", "error")

#: exit codes of a run (the CLI's): 0 report accepted, 1 ended without a report or a
#: follow-up failed (the previous report stays), 2 usage or setup error, 3 waiting for a human
EXIT_SUBMITTED, EXIT_NO_REPORT, EXIT_USAGE, EXIT_AWAITING = 0, 1, 2, 3
EXIT_FOLLOWUP_FAILED = EXIT_NO_REPORT  # alias, same number: a follow-up failed, the previous report stays

#: the longest follow-up message; its progress event keeps the same amount
MAX_MESSAGE_CHARS = 20_000

#: ``additional_kwargs["tfl_budget"]`` of the reply of a follow-up that made no model call
UNANSWERED_REPLY = "unanswered"

#: what a human may decide at a stop
DECISIONS = ("approve", "edit", "reject")

#: per-run review modes (user decision 2026-11-08): ``all`` every enabled point waits for a
#: human; ``formalization`` HITL 1 (and ``execute``) wait, HITL 2 is auto-approved; ``auto``
#: every review is auto-approved. An auto-approved review is recorded with source ``auto`` and
#: decision ``auto_approved``. The budget stop is no review: see ``budget_stop``.
REVIEW_ALL, REVIEW_FORMALIZATION, REVIEW_AUTO = "all", "formalization", "auto"
REVIEW_MODES = (REVIEW_ALL, REVIEW_FORMALIZATION, REVIEW_AUTO)

_T = TypeVar("_T")


class DecisionError(ValueError):
    """A human decision that does not fit the pending request (wrong type, an
    edit that does not validate, a rejection without a comment)."""


class RunError(ValueError):
    """A run directory that cannot be started or resumed as asked."""


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def get_field(obj: Any, key: str, default: Any = None) -> Any:
    """``key`` of a mapping or an attribute of an object (interrupts, messages)."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def message_text(msg: Any) -> str:
    text = getattr(msg, "text", None)
    if isinstance(text, str):
        return text
    content = get_field(msg, "content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def first_decision(response: Any) -> dict[str, Any]:
    """The first decision of a resume value (``{"decisions": [...]}``, or one decision)."""
    if isinstance(response, Mapping) and isinstance(response.get("decisions"), list) and response["decisions"]:
        d = response["decisions"][0]
        if isinstance(d, Mapping):
            return dict(d)
    if isinstance(response, Mapping) and response.get("type") in DECISIONS:
        return dict(response)
    raise ValueError(f"HITL: a resume value must be {{'decisions': [{{'type': ...}}]}}, got {response!r}")


def retry_on_sharing_violation(action: Callable[[], _T], *, attempts: int = 40, delay: float = 0.05) -> _T:
    """``action()``, retried briefly on ``PermissionError``. Windows: replacing or
    removing a file another process has open for reading (TFL Lab polling the run, an
    editor, an antivirus scan) fails with a transient sharing violation (WinError 5 / 32).
    The last attempt's error is raised."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise ValueError(f"attempts must be >= 1, got {attempts}")


def load_run(run_dir: str | Path) -> dict[str, Any]:
    p = Path(run_dir) / RUN_FILE
    if not p.is_file():
        raise RunError(f"{run_dir}: not a deep-agent run directory (no {RUN_FILE})")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunError(f"{p}: unreadable: {exc}") from None


def save_run(run_dir: str | Path, run: Mapping[str, Any]) -> Path:
    p = Path(run_dir) / RUN_FILE
    data = {**dict(run), "updated_at": now()}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    retry_on_sharing_violation(lambda: tmp.replace(p))
    return p


def read_result(run_dir: str | Path) -> dict[str, Any] | None:
    """``result.json`` of the run; ``None`` when it is missing or unreadable."""
    try:
        data = json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "CHECKPOINT_FILE", "DECISIONS", "EXIT_AWAITING", "EXIT_FOLLOWUP_FAILED", "EXIT_NO_REPORT", "EXIT_SUBMITTED", "EXIT_USAGE", "FINAL_STATUSES",
    "MAX_MESSAGE_CHARS", "REPORT_HISTORY_DIR", "RESULT_FILES", "REVIEW_ALL", "REVIEW_AUTO", "REVIEW_FORMALIZATION",
    "REVIEW_MODES", "RUN_FILE", "STATUS_AWAITING", "UNANSWERED_REPLY", "DecisionError", "RunError", "first_decision", "get_field",
    "load_run", "message_text", "now", "read_result", "retry_on_sharing_violation", "save_run",
]
