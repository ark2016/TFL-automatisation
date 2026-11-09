"""TFL Lab: the "Deep Agent" routes (``tfl_deep_agent``, P7 backend).

    GET  /api/deep/runs                       -> {"runs": [summary, ...]} (newest first)
    POST /api/deep/runs                       -> {"run_id", "status", "detail_url"}
         {text | file_b64 + mime [+ name], template?, settings_override?,
          mock_script? (an examples/mock name or a script object; mock mode, the default),
          live?, confirm_spend? (required with live),
          review_mode? ("all" (default) | "formalization" | "auto"; live runs too),
          auto_approve? (mock only; review mode "auto" plus no budget stop: eval and tests.
                         review_mode "auto" alone concerns reviews, so the run still asks for money)}
    GET  /api/deep/runs/<id>[?after=N&limit=M]
                                              -> {"run", "pending_point", "review_mode", "events",
                                                  "next", "total", "usage", "todos", "tools",
                                                  "subagents", "budget", "hitl", "result", "log_tail",
                                                  "followup"}
         events are the progress.jsonl lines from index N (0-based) on (the same
         polling contract as /api/runs/<id>); the other keys are folded from the
         whole file / the run directory, so a client that starts polling late
         still sees the plan, the subagent tree, the budget bar and the HITL card.
    GET  /api/deep/runs/<id>/files/<path>     -> result.{json,md,html}, hitl.md, evidence.jsonl,
                                                 progress.jsonl, run.log, input/*, upload/*, views/*,
                                                 report_history/<n>/result.*
    POST /api/deep/runs/<id>/resume           -> {decision: approve | edit | reject, payload?,
                                                  comment?, confirm_spend? (live runs),
                                                  approve_rest? (only with approve)}
                                                 at the budget stop: edit {"add_usd": X} adds money
                                                 (recorded as extended), approve finishes with what
                                                 is left (recorded as declined)
    POST /api/deep/runs/<id>/abandon          -> drops a follow-up recorded as in progress with no process alive
                                                  (the CLI's --abandon); 409 otherwise
    POST /api/deep/runs/<id>/message          -> {text | preset: "lean", confirm_spend? (live runs)}:
                                                 a follow-up to a finished run, in the same conversation
    POST /api/deep/runs/<id>/cancel           -> {"run_id", "status"}
    POST /api/deep/runs/<id>/render           -> {template}: the finished, accepted report rendered
                                                 with another template into views/<t>.{md,html}
                                                 (a view; result.* is never rewritten)
    GET  /api/deep/estimate[?template=&settings_override=<json>]
                                              -> money limits from the settings, today's spend,
                                                 measured cost of past live deep runs
    GET  /api/deep/settings, PUT /api/deep/settings (a partial object merged over the current)
    GET  /api/deep/mock-scripts               -> the replay scripts of tfl_deep_agent/examples/mock

Review modes (per run; ``run.review_mode`` / ``review_mode`` is the effective
one): ``all`` -- every enabled HITL point waits for a human; ``formalization``
-- HITL 1 (and ``execute``) wait, HITL 2 is auto-approved; ``auto`` -- every
review is auto-approved. An auto-approved review is recorded like
``--auto-approve`` (source ``auto``, decision ``auto_approved``) and an
auto-approved formalization is not a human approval for the verdict gate.
``approve_rest: true`` on a resume approves this stop as the human's decision
and switches the run to ``auto`` (stored in the agent's run.json, so it holds
across processes); ``review_mode`` then reads ``auto``.

``pending_point`` (in every run summary, and at the top level of the detail
response) is ``null`` unless the run is ``awaiting_human``; then it is the
short card of the stop, built by code (no model call)::

    {"point": "formalization" | "report" | "execute" | "budget",
     "title": str,
     "summary": [str, ...],      # at most 4 lines, each shorter than 90 chars
     "details_md": str,          # the full hitl.md section of this stop
     "editable": [str, ...]}     # the fields an "edit" decision accepts ([] = no edit)

Formalization summary: ``["Класс: REG", "Язык: regex (ab)*a|b над {a, b}",
"в L: a, b, aba, ababa", "не в L: ε, bb, ..."]``; report summary:
``["Ответ: <verdict>", "Доверие: <trust> · потолок <cap>", "Шагов: N ·
утверждений: M"]``. It comes from ``run.json`` ``pending.card``, else is built
from the pending request, else from the card cached when the stop settled, so
a waiting run never shows ``null`` (before 2026-11-08 it was the point name
only, and only under ``run``: a client reading the top level saw ``null``).

Runs execute in a subprocess, ``python -m tfl_deep_agent ... --save <run_dir>
--no-dialog``, like the pipelines. A HITL stop ends that process with exit code
3 and the agent's ``run.json`` status ``awaiting_human``; ``.../resume`` starts
a new process ``--resume <run_dir> --decision ...``. So a waiting run needs no
live process and survives a server restart.

Follow-ups: ``.../message`` starts ``--resume <run_dir> --message @<file>`` on a
finished run. ``followup`` in the detail says whether it can be used and what the
Lean preset would run::

    {"can_message": bool, "reason": str | None, "in_progress": bool,
     "error": str | None,   # why the last follow-up did not finish (cancelling it is no error)
     "lean": {"available": bool, "reason": str | None, "model": str | None,
              "subagent_limit_usd": float | None}}

What a run allows is decided by ``tfl_deep_agent.followup``; this module shapes the
JSON and adds the lab's own cases (active, cancelled, interrupted).

While a follow-up runs, ``lab.json`` ``followup_from`` holds the lab status it
started from. A follow-up that is cancelled, whose process dies or which the CLI
refuses does not end the run: the lab record goes back to that status, the agent
restores the previous report (``followup.abandon_followup``), and the run can still
be continued. A restore that fails keeps the agent's record: ``followup.reason`` then
gives the ``--abandon`` command that is the way out. ``run.error`` stays for failures
of the run itself. A cancelled or interrupted run that is not a follow-up cannot be
continued.

``result`` during a follow-up: the view of the newest ``report_history/<n>/`` (immutable,
so reading it never gets in the way of the agent's writes), or of the draft while HITL 2
waits. ``result.report_revision`` tracks the report object, not the files: a text reply
re-renders ``result.*`` and keeps it (a follow-up bumps it only when status, verdict, trust, confidence cap
or ``report_digest`` changes). From revision 2 on the URLs carry ``?v=<n>``.

Run directory: ``<DEEP_RUNS_DIR>/<12 hex>/`` (``.tfl_lab_runs/deep``; the
pipeline registry in ``server.py`` only lists 12-hex directories directly under
``.tfl_lab_runs``, so it never sees these). The agent owns ``run.json``,
``checkpoints.sqlite``, ``evidence.jsonl``, ``progress.jsonl``
(``tfl_deep_agent/progress.py``) and ``result.*``; this module owns
``lab.json`` (process status, pid, segments), ``run.log`` (the child's stderr),
``upload/`` (the task file), ``ui_settings.json`` (the settings snapshot of
the run) and ``ui_mock_script.json`` / ``ui_decisions/``.

Money: mock mode is the default and needs a script; ``live`` needs
``confirm_spend: true`` (and the server never loads ``.env``: the key must be
in its environment). A live resume needs ``confirm_spend`` again. Mock children
get no ``ANTHROPIC_API_KEY`` at all.

``tfl_deep_agent``'s settings, intake and hitl are imported lazily, so the rest of
TFL Lab works without the ``[deep]`` extra; ``run_store``, ``followup`` and
``budget_stop`` import no third-party package and are imported at the top
(``tfl_deep_agent/tests`` checks that they stay so).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from tfl_deep_agent import budget_stop, followup
from tfl_deep_agent.run_store import (
    EXIT_AWAITING,
    EXIT_NO_REPORT,
    EXIT_SUBMITTED,
    FINAL_STATUSES,
    REPORT_HISTORY_DIR,
    REVIEW_MODES,
    RUN_FILE,
    RunError,
)
from tfl_deep_agent.run_store import STATUS_AWAITING as WAITING_STATUS

logger = logging.getLogger("tfl_lab.deep")

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "/api/deep/"
DEEP_RUNS_DIR = ROOT / ".tfl_lab_runs" / "deep"
MOCK_DIR = ROOT / "tfl_deep_agent" / "examples" / "mock"
#: deep-agent settings file; None = ``tfl_deep_agent.settings.settings_path()``
#: (``$TFL_DEEP_SETTINGS`` or ``tfl_deep_agent/settings.json``)
SETTINGS_PATH: Path | None = None

LAB_FILE = "lab.json"
PROGRESS_FILE = "progress.jsonl"
LOG_FILE = "run.log"
UPLOAD_DIR = "upload"
SETTINGS_SNAPSHOT = "ui_settings.json"
MOCK_SNAPSHOT = "ui_mock_script.json"
DECISIONS_DIR = "ui_decisions"
#: re-rendered views of a finished report in another template (POST runs/<id>/render)
VIEWS_DIR = "views"
SERVED_FILES = {"result.json", "result.md", "result.html", "hitl.md", "evidence.jsonl", PROGRESS_FILE, LOG_FILE}
SERVED_DIRS = ("input/", f"{UPLOAD_DIR}/", f"{VIEWS_DIR}/", f"{REPORT_HISTORY_DIR}/")

RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
MOCK_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\.json")

MB = 1024 * 1024
BODY_SLACK_BYTES = 1 * MB
MAX_BODY_CAP_BYTES = 64 * MB
LOG_LINES_KEPT = 2000

MAX_CONCURRENT_RUNS = int(os.environ.get("TFL_LAB_MAX_DEEP_RUNS", "2"))
#: live deep runs at once (one: their per-day reservations are per process, see _acquire_slot)
MAX_CONCURRENT_LIVE_RUNS = 1
_SLOT_POLL_SECONDS = 0.2
_WAIT_POLL_SECONDS = 0.5
_ORPHAN_POLL_SECONDS = 2.0

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "error", "cancelled", "interrupted"}

CREATE_KEYS = {"text", "file_b64", "mime", "name", "template", "settings_override", "mock_script", "live",
               "confirm_spend", "auto_approve", "review_mode"}
RESUME_KEYS = {"decision", "payload", "comment", "confirm_spend", "reviewer", "approve_rest"}
MESSAGE_KEYS = {"text", "preset", "confirm_spend"}
_EXT_FOR_MIME = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                 "text/plain": ".txt"}


# ---------------------------------------------------------------------------
# Errors and responses
# ---------------------------------------------------------------------------

class NotFound(LookupError):
    """404."""


class Conflict(ValueError):
    """409: valid, but not applicable to the run's current state."""


class BadRequest(ValueError):
    """400."""


@dataclass
class FileResponse:
    path: Path


# ---------------------------------------------------------------------------
# Helpers (the stateless ones of server.py, imported lazily: server.py imports
# this module from its handler, so no import cycle at load time)
# ---------------------------------------------------------------------------

def _lab() -> Any:
    from ui_server import server

    return server


def _atomic_write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _now() -> float:
    return time.time()


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def spend_dir() -> Path:
    """Where the per-day spend log lives: ``$TFL_DEEP_RUNS_DIR`` (what the CLI
    reads) or the parent of :data:`DEEP_RUNS_DIR` (``.tfl_lab_runs``, the CLI's
    default), so the UI and the CLI share one day's log."""
    env = os.environ.get("TFL_DEEP_RUNS_DIR", "").strip()
    return Path(env) if env else DEEP_RUNS_DIR.parent


def _run_dirs() -> dict[str, Path]:
    if not DEEP_RUNS_DIR.is_dir():
        return {}
    return {d.name: d for d in DEEP_RUNS_DIR.iterdir() if d.is_dir() and RUN_ID_RE.fullmatch(d.name)}


# ---------------------------------------------------------------------------
# Settings (tfl_deep_agent.settings)
# ---------------------------------------------------------------------------

def _settings_mod() -> Any:
    from tfl_deep_agent import settings as settings_mod

    return settings_mod


def _settings_problems(exc: Exception) -> str:
    problems = getattr(exc, "problems", None) or []
    head = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return head + (": " + "; ".join(problems) if problems and len(problems) > 1 or
                   (problems and problems[0] != head) else "")


def load_deep_settings() -> Any:
    """The current ``Settings``; a broken settings file is a :class:`Conflict`
    (it holds money limits: never silently replaced by the defaults)."""
    mod = _settings_mod()
    try:
        return mod.load_settings(SETTINGS_PATH)
    except mod.SettingsError as exc:
        raise Conflict(f"the deep-agent settings file is invalid: {_settings_problems(exc)}") from None


def api_get_settings() -> dict[str, Any]:
    mod = _settings_mod()
    from tfl_deep_agent.cli import TEMPLATES

    out: dict[str, Any] = {"defaults": mod.DEFAULTS, "path": str(mod.settings_path(SETTINGS_PATH)),
                           "templates": list(TEMPLATES), "error": None}
    try:
        out["settings"] = mod.load_settings(SETTINGS_PATH).to_dict()
    except mod.SettingsError as exc:
        out["settings"] = mod.default_settings().to_dict()
        out["error"] = _settings_problems(exc)
    try:
        out["schema"] = mod.settings_json_schema()
    except Exception:  # noqa: BLE001 - the form can live without it
        out["schema"] = None
    return out


def api_put_settings(payload: Any) -> dict[str, Any]:
    """Merge a partial settings object over the current settings (over the
    defaults when the file is broken), validate, write atomically."""
    mod = _settings_mod()
    if not isinstance(payload, dict) or not payload:
        raise BadRequest("settings must be a non-empty JSON object (a partial settings tree)")
    try:
        base = mod.load_settings(SETTINGS_PATH)
    except mod.SettingsError:
        base = mod.default_settings()
    try:
        new = mod.apply_overrides(base, payload)
    except mod.SettingsError as exc:
        raise BadRequest(_settings_problems(exc)) from None
    mod.save_settings(new, mod.settings_path(SETTINGS_PATH))
    return api_get_settings()


def max_body_bytes() -> int:
    """Request body limit of the deep routes: a base64 upload of
    ``intake.max_file_mb`` plus slack (capped at 64 MB)."""
    try:
        mb = float(load_deep_settings().intake.max_file_mb)
    except Exception:  # noqa: BLE001 - a broken settings file still gets a sane limit
        mb = 10.0
    return int(min(MAX_BODY_CAP_BYTES, mb * MB * 4 / 3 + BODY_SLACK_BYTES))


# ---------------------------------------------------------------------------
# Mock scripts
# ---------------------------------------------------------------------------

def _mock_index() -> dict[str, Path]:
    if not MOCK_DIR.is_dir():
        return {}
    base = MOCK_DIR.resolve()
    return {f.name: f for f in MOCK_DIR.glob("*.json")
            if f.is_file() and f.resolve().is_relative_to(base) and MOCK_NAME_RE.fullmatch(f.name)}


def api_mock_scripts() -> dict[str, Any]:
    out = []
    for name, path in sorted(_mock_index().items()):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        roles = sorted((data.get("models") or {}).keys()) if isinstance(data.get("models"), dict) else []
        out.append({"name": name, "description": data.get("description"),
                    "task": str(data.get("task") or "")[:400], "template": data.get("template"),
                    "roles": roles, "uses_intake": "intake" in roles})
    return {"mock_scripts": out}


# ---------------------------------------------------------------------------
# Registry (in memory, mirrored to <run_dir>/lab.json)
# ---------------------------------------------------------------------------

_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_slots = threading.Condition()
_active = 0
_live_active = 0

_RECORD_KEYS = ("run_id", "status", "live", "mode", "template", "input", "created", "started", "finished",
                "pid", "pid_identity", "server_pid", "server_identity", "error", "result_status", "segments",
                "auto_approve", "review_mode", "resume_from", "report_template", "followup_from",
                "followup_error")


def _new_entry(run_id: str, run_dir: Path, *, live: bool, template: str | None, input_info: dict[str, Any],
               auto_approve: bool, review_mode: str | None = None) -> dict[str, Any]:
    lab = _lab()
    return {
        "run_id": run_id, "run_dir": str(run_dir), "status": "queued", "live": live,
        "mode": "live" if live else "mock", "template": template, "input": input_info,
        "created": _now(), "started": None, "finished": None, "pid": None, "pid_identity": None,
        "server_pid": os.getpid(), "server_identity": lab.process_identity(os.getpid()),
        "error": None, "result_status": None, "segments": [], "auto_approve": auto_approve,
        "review_mode": review_mode or ("auto" if auto_approve else "all"),
        "lines": [], "proc": None, "cancel_requested": False, "orphan": False,
    }


def _persist_locked(run: dict[str, Any]) -> None:
    try:
        _atomic_write_json(Path(run["run_dir"]) / LAB_FILE, {k: run.get(k) for k in _RECORD_KEYS})
    except OSError:
        logger.exception("could not write %s for %s", LAB_FILE, run.get("run_id"))


def _get_run(run_id: str) -> dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id or ""):
        raise NotFound(f"deep run not found: {run_id}")
    run = _runs.get(run_id)
    if run is None:
        raise NotFound(f"deep run not found: {run_id}")
    return run


def _append_log_locked(run: dict[str, Any], line: str) -> None:
    run["lines"].append(line)
    del run["lines"][:-LOG_LINES_KEPT]
    try:
        with open(Path(run["run_dir"]) / LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _agent_run(run_dir: Path) -> dict[str, Any] | None:
    data = _read_json(run_dir / RUN_FILE)
    return data if isinstance(data, dict) else None


def _agent_run_if_idle(run: dict[str, Any]) -> dict[str, Any] | None:
    """The agent's run.json, but only while no process of the run is alive:
    on Windows a reader holding the file open makes the agent's atomic
    replace fail (a sharing violation), so a live run is shown from
    progress.jsonl (append-only) instead."""
    if run.get("status") in ACTIVE_STATUSES:
        return None
    return _agent_run(Path(run["run_dir"]))


def _abandon_agent_followup(run: dict[str, Any], *, error: str | None = None) -> bool:
    """Restore the previous report of the follow-up in progress, if any (a no-op
    otherwise). No process of the run may be alive. It copies files, so it runs
    outside ``_lock``, while the status keeps other readers off ``run.json``. Returns
    whether the run is out of the follow-up; ``False``: the record stays, and the lab
    record still settles, so ``followup.reason`` says what to do."""
    try:
        followup.abandon_followup(run["run_dir"], error=error)
    except Exception:  # noqa: BLE001 - a restore that fails must not take the settling with it
        logger.exception("could not restore the report of the follow-up of %s", run.get("run_id"))
        return False
    return True


def _not_restored_note(run: dict[str, Any]) -> str:
    return ("the previous report could not be restored, the follow-up stays recorded: "
            + followup.abandon_command(run["run_dir"]))


def _restore_dead_followup(run: dict[str, Any], *, rc: int | None, tail: str = "",
                           reason: str | None = None) -> str | None:
    """A follow-up record that survives its process means the process did not finish,
    whatever its exit code: restore the previous report and return the note for the
    human, else ``None``. A follow-up that waits at a stop has no process and is not
    dead. Call it before ``_settle_from_disk_locked``, outside ``_lock``."""
    agent = _agent_run(Path(run["run_dir"])) or {}
    if not run.get("followup_from") or not agent.get("followup_in_progress"):
        return None
    if agent.get("status") == WAITING_STATUS and agent.get("pending"):
        return None
    if rc is None:
        note = reason or "the follow-up's process is gone"
    else:
        note = f"the follow-up's process exited with code {rc}" + (f"\n{tail}" if tail else "")
    return note if _abandon_agent_followup(run, error=note) else f"{note}\n{_not_restored_note(run)}"


def _drop_stale_views(run: dict[str, Any], revision_before: Any) -> None:
    """``views/`` were rendered from the report a follow-up has replaced."""
    if (_agent_run(Path(run["run_dir"])) or {}).get("report_revision") != revision_before:
        shutil.rmtree(Path(run["run_dir"]) / VIEWS_DIR, ignore_errors=True)


def _followup_back_locked(run: dict[str, Any], note: str | None = None) -> None:
    """A follow-up that did not run or was dropped: the lab record is back to the
    status it started from (the report is not touched here), so the run can still be
    continued. ``note``: why it did not finish, for ``followup.error``; ``run.error``
    is the run's own. Caller holds ``_lock``; it persists afterwards."""
    prev = run.get("followup_from") or {}
    run["followup_from"] = None
    run["status"] = prev.get("status") or "completed"
    run["result_status"] = prev.get("result_status")
    run["error"] = prev.get("error")
    run["followup_error"] = note
    run["finished"] = _now()
    run["pid"] = None
    run["pid_identity"] = None
    run["orphan"] = False
    run["cancel_requested"] = False


def _settle_cancelled_locked(run: dict[str, Any], error: str, *, note: str | None = None) -> None:
    """The human cancelled, with no process of the run alive: a follow-up (its report
    already restored, :func:`_settle_cancelled`) stays continuable, anything else is
    ``cancelled``. ``note``: what went wrong with the restore. Caller holds ``_lock``."""
    if run.get("followup_from"):
        _followup_back_locked(run, note)
    else:
        run["status"] = "cancelled"
        run["error"] = error
        run["finished"] = _now()
        run["pid"] = None
        run["pid_identity"] = None
        run["orphan"] = False


def _settle_cancelled(run: dict[str, Any], error: str, *, rc: int | None = None) -> None:
    """Cancelled with no process alive: the report first, without ``_lock``."""
    note = None
    if run.get("followup_from") and not _abandon_agent_followup(run):
        note = _not_restored_note(run)
    with _lock:
        _settle_cancelled_locked(run, error, note=note)
        if rc is not None and run["segments"]:
            run["segments"][-1].update(finished=run["finished"], rc=rc, outcome="cancelled")
        _persist_locked(run)


def _fresh_agent_error(run: dict[str, Any], agent: dict[str, Any]) -> bool:
    """The agent's ``followup_error`` belongs to this follow-up: it is new since the message
    was sent, or the agent started a segment for it. ``run.json`` keeps the error of an earlier
    follow-up until ``begin_followup`` clears it, which a CLI that refused the message never reaches."""
    error = agent.get("followup_error")
    if not error:
        return False
    before = run.get("followup_from") or {}
    return (error != before.get("agent_error")
            or len(agent.get("segments") or []) > int(before.get("agent_segments") or 0))


def _settle_followup_locked(run: dict[str, Any], agent: dict[str, Any], *, rc: int | None, tail: str,
                            dead_note: str | None) -> bool:
    """Handle a follow-up before the run's own statuses. Returns whether it settled the
    lab record; ``False`` when the follow-up waits at a stop or ran to its end, which
    the general path handles. ``dead_note``: from :func:`_restore_dead_followup`."""
    if not run.get("followup_from"):
        return False
    if dead_note:
        _followup_back_locked(run, dead_note)
    elif agent.get("status") == WAITING_STATUS and agent.get("pending"):
        return False
    elif _fresh_agent_error(run, agent):  # the agent failed and has restored the report itself; its reason beats rc
        _followup_back_locked(run, str(agent["followup_error"]))
    elif rc not in (None, EXIT_SUBMITTED, EXIT_NO_REPORT):  # the CLI refused it: nothing has changed
        _followup_back_locked(run, f"the follow-up did not run (exit code {rc}); the previous report stays"
                              + (f"\n{tail}" if tail else ""))
    else:
        return False
    return True


def _sync_review_mode_locked(run: dict[str, Any], agent: dict[str, Any]) -> None:
    """The agent's ``run.json`` is the authority on the review mode: a continuation that
    failed before it applied ``approve_rest`` would leave the lab's copy at ``auto``.
    Caller holds ``_lock``."""
    mode = agent.get("review_mode")
    if mode in REVIEW_MODES:
        run["review_mode"] = mode
        run["auto_approve"] = mode == "auto"


def _settle_from_disk_locked(run: dict[str, Any], *, rc: int | None = None, tail: str = "",
                             reason: str | None = None, dead_followup_note: str | None = None) -> None:
    """The lab status after the run's process ended (or vanished), read from
    the agent's ``run.json``: awaiting_human / completed (+ result_status) /
    error / interrupted. A follow-up is handled first (:func:`_settle_followup_locked`)."""
    agent = _agent_run(Path(run["run_dir"])) or {}
    _sync_review_mode_locked(run, agent)
    run["finished"] = _now()
    run["pid"] = None
    run["pid_identity"] = None
    run["orphan"] = False
    if not _settle_followup_locked(run, agent, rc=rc, tail=tail, dead_note=dead_followup_note):
        _settle_status_locked(run, agent, rc=rc, tail=tail, reason=reason)
    if run.get("followup_from") and run["status"] != WAITING_STATUS:  # the follow-up ran to its end
        record = run["followup_from"]
        run["followup_from"] = None
        _drop_stale_views(run, record.get("report_revision"))
    if run["status"] == "completed":
        run["report_template"] = rendered_template(_read_json(Path(run["run_dir"]) / "result.json"))
    if run["segments"]:
        seg = run["segments"][-1]
        seg.setdefault("finished", run["finished"])
        if rc is not None:
            seg["rc"] = rc
        seg["outcome"] = run["status"]
    _persist_locked(run)


def _settle_status_locked(run: dict[str, Any], agent: dict[str, Any], *, rc: int | None, tail: str,
                          reason: str | None) -> None:
    """The lab status from the agent's status and the exit code."""
    st = agent.get("status")
    if st == WAITING_STATUS and agent.get("pending"):
        # still waiting, whatever the exit code: a resume that failed before it applied the
        # decision (a CLI refusal such as "--live needs ANTHROPIC_API_KEY", exit 2) leaves
        # run.json at the same stop (resume_run sets "running" before the agent runs), so
        # the run stays resumable; the failure is shown in `error`
        run["status"] = WAITING_STATUS
        run["pending_card"] = pending_card(run, agent)
        if rc in (None, EXIT_AWAITING):
            run["error"] = None
        else:
            prev = run.get("resume_from")
            same = prev and prev == (agent.get("pending") or {}).get("interrupt_id")
            run["error"] = ((f"the resume process exited with code {rc} before applying the decision; "
                             "the run still waits at the same review stop and can be resumed again") if same
                            else (f"the process exited with code {rc}; the agent's record still waits at a "
                                  "review stop, so the run can be resumed")) + (f"\n{tail}" if tail else "")
    elif st in FINAL_STATUSES and st != "error" and rc in (None, EXIT_SUBMITTED, EXIT_NO_REPORT):
        run["status"] = "completed"
        run["result_status"] = st
        run["error"] = None
    elif st == "error" and rc in (None, EXIT_NO_REPORT):
        run["status"] = "completed" if (Path(run["run_dir"]) / "result.json").is_file() else "error"
        run["result_status"] = "error"
        run["error"] = agent.get("error") or "the agent reported an error"
    elif rc is None:
        run["status"] = "interrupted"
        run["error"] = reason or "the run's process is gone"
    else:
        run["status"] = "error"
        run["error"] = (f"exit code {rc}" + (f"\n{tail}" if tail else ""))


def _settle_process_end(run: dict[str, Any], *, rc: int | None = None, tail: str = "",
                        reason: str | None = None) -> None:
    """The run's process ended or vanished: a follow-up it left behind is restored first,
    without ``_lock``, then the lab record settles from the disk."""
    note = _restore_dead_followup(run, rc=rc, tail=tail, reason=reason)
    with _lock:
        _settle_from_disk_locked(run, rc=rc, tail=tail, reason=reason, dead_followup_note=note)


# ---------------------------------------------------------------------------
# Process handling
# ---------------------------------------------------------------------------

def _live_orphan_running(run_id: str) -> bool:
    """A live run adopted after a server restart whose process is still running (it holds
    the live slot although no worker of this server counted it). Caller holds ``_lock``."""
    return any(rid != run_id and r.get("live") and r.get("orphan") and r.get("status") in ACTIVE_STATUSES
               for rid, r in _runs.items())


def _acquire_slot(run_id: str, live: bool = False) -> bool:
    """Wait for a run slot (``MAX_CONCURRENT_RUNS``) and, for a live process, for the single
    live slot (``MAX_CONCURRENT_LIVE_RUNS``): every live process has its own BudgetLedger,
    whose day-remainder check subtracts only its own in-flight reservation from the shared
    spend log, so two parallel live runs could together pass ``per_day_usd``."""
    global _active, _live_active
    with _slots:
        while True:
            with _lock:
                run = _runs.get(run_id)
                if run is not None and run.get("cancel_requested"):
                    return False
                live_busy = live and (_live_active >= MAX_CONCURRENT_LIVE_RUNS or _live_orphan_running(run_id))
            if _active < MAX_CONCURRENT_RUNS and not live_busy:
                break
            _slots.wait(timeout=_SLOT_POLL_SECONDS)
        _active += 1
        if live:
            _live_active += 1
        return True


def _release_slot(live: bool = False) -> None:
    global _active, _live_active
    with _slots:
        _active = max(0, _active - 1)
        if live:
            _live_active = max(0, _live_active - 1)
        _slots.notify_all()


def _kill(proc: subprocess.Popen) -> None:
    """Kill the child and its process tree (Windows: taskkill /T)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _child_env(live: bool) -> dict[str, str]:
    env = dict(os.environ)
    if not live:
        env.pop("ANTHROPIC_API_KEY", None)  # a mock run never needs it
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "TFL_DEEP_RUNS_DIR": str(spend_dir())})
    return env


def _spawn_and_wait(run_id: str, cmd: list[str], env: dict[str, str]) -> tuple[int | None, str | None, list[str]]:
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as exc:  # noqa: BLE001
        return None, f"failed to launch: {exc}", []
    lab = _lab()
    with _lock:
        run = _runs.get(run_id)
        cancel_now = bool(run and run.get("cancel_requested"))
        if run is not None:
            run["proc"] = proc
            run["pid"] = proc.pid
            run["pid_identity"] = lab.process_identity(proc.pid)
            _persist_locked(run)
    if cancel_now:
        _kill(proc)
    tail: list[str] = []

    def pump(stream: Any, prefix: str) -> None:
        for line in stream:
            line = line.rstrip("\n")
            with _lock:
                r = _runs.get(run_id)
                if r is not None:
                    _append_log_locked(r, prefix + line)
            if not prefix:
                tail.append(line)
                del tail[:-20]

    threads = [threading.Thread(target=pump, args=(proc.stderr, ""), daemon=True),
               threading.Thread(target=pump, args=(proc.stdout, "[stdout] "), daemon=True)]
    for t in threads:
        t.start()
    outcome = None
    while True:
        try:
            rc = proc.wait(timeout=_WAIT_POLL_SECONDS)
            with _lock:
                r = _runs.get(run_id)
                if r is not None and r.get("cancel_requested"):
                    outcome = "cancelled"
            break
        except subprocess.TimeoutExpired:
            with _lock:
                r = _runs.get(run_id)
                cancel = bool(r and r.get("cancel_requested"))
            if cancel:
                _kill(proc)
                rc = proc.wait()
                outcome = "cancelled"
                break
    for t in threads:
        t.join(timeout=2.0)
    with _lock:
        r = _runs.get(run_id)
        if r is not None:
            r["proc"] = None
    return rc, outcome, tail


def _followup_finished_as_cancel_landed(run: dict[str, Any], rc: int | None) -> bool:
    """A cancel reached a follow-up that had already finished: the process ran and left
    no record in ``run.json``, so there is nothing to abandon."""
    return rc is not None and _followup_record_gone(run)


def _followup_record_gone(run: dict[str, Any]) -> bool:
    """The run is in a follow-up whose process left no record in ``run.json``."""
    return (bool(run.get("followup_from"))
            and not (_agent_run(Path(run["run_dir"])) or {}).get("followup_in_progress"))


def _worker(run_id: str, cmd: list[str], live: bool, action: str) -> None:
    if not _acquire_slot(run_id, live):
        run = _runs.get(run_id)
        if run is not None:
            _settle_cancelled(run, "cancelled while queued")
        return
    try:
        with _lock:
            run = _runs.get(run_id)
            if run is None:
                return
            cancelled = bool(run.get("cancel_requested"))
        if cancelled:
            _settle_cancelled(run, "cancelled while queued")
            return
        with _lock:
            run["status"] = "running"
            run["started"] = run.get("started") or _now()
            run["finished"] = None
            run["segments"].append({"action": action, "started": _now(), "argv": cmd[3:]})
            _append_log_locked(run, f"--- {action}: {' '.join(cmd[3:])} ---")
            _persist_locked(run)
        rc, outcome, tail = _spawn_and_wait(run_id, cmd, _child_env(live))
        run = _runs.get(run_id)
        if run is None:
            return
        if outcome == "cancelled" and _followup_finished_as_cancel_landed(run, rc):
            with _lock:
                run["cancel_requested"] = False
            # what this process wrote is the truth; an exit code of the kill says nothing about it
            _settle_process_end(run, rc=rc if rc in (EXIT_SUBMITTED, EXIT_NO_REPORT) else None,
                                tail="\n".join(tail[-10:]))
        elif outcome == "cancelled":
            _settle_cancelled(run, "cancelled by user", rc=rc)
        elif rc is None:
            with _lock:
                agent = _agent_run(Path(run["run_dir"])) or {}
                _sync_review_mode_locked(run, agent)
                waiting = agent.get("status") == WAITING_STATUS and bool(agent.get("pending"))
                if run.get("followup_from") and not waiting:
                    _followup_back_locked(run, outcome)  # the follow-up never started
                else:
                    run["status"] = WAITING_STATUS if waiting else "error"  # a failed resume launch keeps the stop
                    run["error"] = outcome
                    run["finished"] = _now()
                _append_log_locked(run, f"ERROR: {outcome}")
                _persist_locked(run)
        else:
            _settle_process_end(run, rc=rc, tail="\n".join(tail[-10:]))
    finally:
        _release_slot(live)


def _start_worker(run_id: str, cmd: list[str], live: bool, action: str) -> None:
    threading.Thread(target=_worker, args=(run_id, cmd, live, action), daemon=True).start()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def build_start_command(run_dir: Path, task_file: Path, *, settings_file: Path, template: str | None,
                        mock_script: Path | None, live: bool, auto_approve: bool,
                        review_mode: str | None = None) -> list[str]:
    """argv of ``python -m tfl_deep_agent --file ... --save RUN_DIR --no-dialog ...``
    (``review_mode`` -> ``--review-mode``; ``auto_approve`` -> ``--auto-approve``, which
    means review mode ``auto`` plus no budget stop)."""
    cmd = [sys.executable, "-m", "tfl_deep_agent", "--file", str(task_file), "--save", str(run_dir),
           "--settings", str(settings_file), "--no-dialog"]
    if template:
        cmd += ["--template", template]
    if live:
        cmd.append("--live")
    elif mock_script is not None:
        cmd += ["--mock-script", str(mock_script)]
    if auto_approve:
        cmd.append("--auto-approve")
    elif review_mode and review_mode != "all":
        cmd += ["--review-mode", review_mode]
    return cmd


def build_resume_command(run_dir: Path, decision: str, payload_file: Path | None, *, live: bool,
                         approve_rest: bool = False) -> list[str]:
    """argv of ``python -m tfl_deep_agent --resume RUN_DIR --decision D [--payload @file]
    [--approve-rest] --no-dialog``."""
    cmd = [sys.executable, "-m", "tfl_deep_agent", "--resume", str(run_dir), "--decision", decision,
           "--no-dialog"]
    if payload_file is not None:
        cmd += ["--payload", "@" + str(payload_file)]
    if approve_rest:
        cmd.append("--approve-rest")
    if live:
        cmd.append("--live")
    return cmd


def build_followup_command(run_dir: Path, message_file: Path, *, live: bool) -> list[str]:
    """argv of ``python -m tfl_deep_agent --resume RUN_DIR --message @file --no-dialog``
    (the text goes through a file, never through argv)."""
    cmd = [sys.executable, "-m", "tfl_deep_agent", "--resume", str(run_dir), "--message", "@" + str(message_file),
           "--no-dialog"]
    if live:
        cmd.append("--live")
    return cmd


# ---------------------------------------------------------------------------
# POST /api/deep/runs
# ---------------------------------------------------------------------------

def _check_day_budget(settings: Any) -> None:
    from tfl_deep_agent.middleware import budget as budget_mod

    log = _spend_log()
    try:
        log.check_can_start(settings.budget.per_task_usd, settings.budget.per_day_usd)
    except budget_mod.DailyBudgetExceeded as exc:
        raise Conflict(str(exc)) from None


#: money limits a live run's settings_override may only lower (the saved settings are the cap)
LIVE_MONEY_KEYS = ("per_day_usd", "per_task_usd", "per_subagent_max_usd")
#: review points a live run's settings_override may not turn off
LIVE_HITL_KEYS = ("formalization", "report")


def _live_override_problems(saved: Any, merged: Any) -> list[str]:
    """Why ``merged`` (saved settings + a run's override) is not allowed for a live run:
    a money limit above the saved one (per_day_usd is a global cap, not a per-request
    value), or a review point the saved settings have on and the override turns off.
    Changing those needs ``PUT /api/deep/settings``, an explicit, persistent choice."""
    problems = []
    for key in LIVE_MONEY_KEYS:
        was, now = float(getattr(saved.budget, key)), float(getattr(merged.budget, key))
        if now > was + 1e-9:
            problems.append(f"budget.{key} {now:g} is above the saved {was:g} (an override may only lower "
                            "money limits; raise them in the settings)")
    for key in LIVE_HITL_KEYS:
        if bool(getattr(saved.hitl, key)) and not bool(getattr(merged.hitl, key)):
            problems.append(f"hitl.{key} cannot be turned off for one paid run (the saved settings have it on)")
    return problems


def _task_input(payload: dict[str, Any], settings: Any) -> Any:
    from tfl_deep_agent.intake import files as intake_files

    text, file_b64 = payload.get("text"), payload.get("file_b64")
    if (text is None) == (file_b64 is None):
        raise BadRequest("give exactly one of text or file_b64 (+ mime)")
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise BadRequest("name must be a string")
    try:
        if text is not None:
            if not isinstance(text, str):
                raise BadRequest("text must be a string")
            return intake_files.from_text(text, limits=settings)
        if not isinstance(file_b64, str):
            raise BadRequest("file_b64 must be a base64 string")
        mime = payload.get("mime")
        if mime is not None and not isinstance(mime, str):
            raise BadRequest("mime must be a string")
        return intake_files.from_base64(file_b64, name=Path(name).name if name else None, declared_mime=mime,
                                        limits=settings)
    except intake_files.IntakeError as exc:
        raise BadRequest(f"{exc} ({exc.code})") from None


def _mock_source(spec: Any) -> tuple[Path | None, dict[str, Any] | None]:
    """``(path of an examples/mock script, None)`` or ``(None, inline script)``."""
    if isinstance(spec, str):
        path = _mock_index().get(spec.strip())
        if path is None:
            raise BadRequest(f"unknown mock script {spec!r}; GET /api/deep/mock-scripts lists them")
        return path, None
    if isinstance(spec, dict):
        if not isinstance(spec.get("models"), dict) or not spec["models"]:
            raise BadRequest("an inline mock_script needs a non-empty 'models' object {role: [replies]}")
        return None, spec
    raise BadRequest("mock_script must be the name of an examples/mock script or a script object")


def api_create(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadRequest("the request body must be a JSON object")
    unknown = sorted(set(payload) - CREATE_KEYS)
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")
    live = payload.get("live", False)
    if not isinstance(live, bool):
        raise BadRequest("live must be a boolean")
    if live and payload.get("confirm_spend") is not True:
        raise BadRequest("a live run makes paid API calls: send {\"confirm_spend\": true}")
    if live and payload.get("mock_script") is not None:
        raise BadRequest("live and mock_script exclude each other")
    if not live and payload.get("mock_script") is None:
        raise BadRequest("mock mode (the default) needs mock_script: the name of a replay script "
                         "(GET /api/deep/mock-scripts) or a script object; nothing is sent to the API "
                         "without live + confirm_spend")
    from tfl_deep_agent.cli import TEMPLATES

    template = payload.get("template")
    if template is not None and template not in TEMPLATES:
        raise BadRequest(f"unknown template {template!r}; one of {', '.join(TEMPLATES)}")
    auto_approve = payload.get("auto_approve", False)
    if not isinstance(auto_approve, bool):
        raise BadRequest("auto_approve must be a boolean")
    if live and auto_approve:
        raise BadRequest("auto_approve is for mock runs (eval and tests): a paid live run keeps its human "
                         "review points; a live run chooses review_mode explicitly")
    review_mode = payload.get("review_mode")
    if review_mode is not None and review_mode not in REVIEW_MODES:
        raise BadRequest(f"review_mode must be one of {', '.join(REVIEW_MODES)}")
    if auto_approve and review_mode not in (None, "auto"):
        raise BadRequest("auto_approve means review mode 'auto' plus no budget stop; it conflicts with "
                         f"review_mode {review_mode!r}")
    if auto_approve:
        review_mode = "auto"

    mod = _settings_mod()
    saved = load_deep_settings()
    settings = saved
    override = payload.get("settings_override")
    if override is not None:
        if not isinstance(override, dict):
            raise BadRequest("settings_override must be a JSON object (a partial settings tree)")
        try:
            settings = mod.apply_overrides(settings, override)
        except mod.SettingsError as exc:
            raise BadRequest(_settings_problems(exc)) from None
        if live:
            problems = _live_override_problems(saved, settings)
            if problems:
                raise BadRequest("settings_override of a live run: " + "; ".join(problems))
    mock_path, mock_inline = (None, None) if live else _mock_source(payload.get("mock_script"))
    task = _task_input(payload, settings)
    if live:
        _check_day_budget(settings)

    run_id = uuid.uuid4().hex[:12]
    run_dir = DEEP_RUNS_DIR / run_id
    (run_dir / UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    ext = ".txt" if task.kind == "text" else _EXT_FOR_MIME.get(task.mime_type, Path(task.name).suffix or ".bin")
    task_file = run_dir / UPLOAD_DIR / f"task{ext}"
    if task.kind == "text":
        task_file.write_text(task.text or "", encoding="utf-8")
    else:
        task_file.write_bytes(task.data)
    settings_file = run_dir / SETTINGS_SNAPSHOT
    _atomic_write_json(settings_file, settings.to_dict())
    if mock_inline is not None:
        mock_path = run_dir / MOCK_SNAPSHOT
        _atomic_write_json(mock_path, mock_inline)
    input_info = {"kind": task.kind, "mime": task.mime_type, "name": Path(str(payload.get("name") or task.name)).name,
                  "size_bytes": len(task.data) if task.data is not None else None, "pages": task.pages,
                  "file": f"{UPLOAD_DIR}/{task_file.name}", "warnings": list(task.warnings)}
    cmd = build_start_command(run_dir, task_file, settings_file=settings_file, template=template,
                              mock_script=mock_path, live=live, auto_approve=auto_approve,
                              review_mode=review_mode)
    with _lock:
        run = _new_entry(run_id, run_dir, live=live, template=template, input_info=input_info,
                         auto_approve=review_mode == "auto", review_mode=review_mode or "all")
        _runs[run_id] = run
        _persist_locked(run)
    _start_worker(run_id, cmd, live, "start")
    return {"run_id": run_id, "status": "queued", "mode": "live" if live else "mock",
            "review_mode": review_mode or "all",
            "detail_url": f"{PREFIX}runs/{run_id}", "warnings": list(task.warnings)}


# ---------------------------------------------------------------------------
# POST /api/deep/runs/<id>/resume and /cancel
# ---------------------------------------------------------------------------

def _decision_file_payload(decision: str, payload: Any, comment: Any) -> Any:
    """What goes to ``--payload @file``: edit -> the fields (+ comment);
    reject / approve -> ``{"comment": ...}`` (or nothing)."""
    if comment is not None and not isinstance(comment, str):
        raise BadRequest("comment must be a string")
    comment = comment.strip() if isinstance(comment, str) and comment.strip() else None
    if decision == "edit":
        if not isinstance(payload, dict) or not payload:
            raise BadRequest("edit needs payload: a JSON object of the fields to change")
        out = dict(payload)
        if comment and "comment" not in out:
            out["comment"] = comment
        return out
    if payload is not None and not isinstance(payload, (dict, str)):
        raise BadRequest("payload must be an object (or, for reject, the comment text)")
    if isinstance(payload, dict):
        out = dict(payload)
        if comment:
            out["comment"] = comment
        return out or None
    if isinstance(payload, str) and payload.strip():
        return {"comment": comment or payload.strip()}
    return {"comment": comment} if comment else None


def _prevalidate(pending: dict[str, Any], decision: str, file_payload: Any, run_dir: Path | None = None) -> None:
    """Check the decision against the pending request in-process (the same
    checks the resumed process makes, including a report edit's evidence ids
    against the run's ``evidence.jsonl``), so a misfit is a 400 now and the run
    keeps waiting. Skipped when tfl_deep_agent.hitl cannot be imported."""
    try:
        from tfl_deep_agent import hitl
    except Exception:  # noqa: BLE001 - the child process checks it as well
        return
    evidence = None
    if run_dir is not None and (run_dir / "evidence.jsonl").is_file():
        try:
            from tfl_deep_agent.evidence.ledger import EvidenceLedger

            evidence = EvidenceLedger.load(run_dir)
        except Exception:  # noqa: BLE001 - the child process checks the ids as well
            evidence = None
    request = pending.get("request") or {}
    try:
        dec = hitl.parse_decision(decision, file_payload, allowed=hitl.allowed_decisions(request))
        hitl.resume_value(request, dec, evidence=evidence)
    except hitl.DecisionError as exc:
        raise BadRequest(str(exc)) from None


def api_resume(run_id: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadRequest("the request body must be a JSON object")
    unknown = sorted(set(payload) - RESUME_KEYS)
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in ("approve", "edit", "reject"):
        raise BadRequest("decision must be one of approve, edit, reject")
    approve_rest = payload.get("approve_rest", False)
    if not isinstance(approve_rest, bool):
        raise BadRequest("approve_rest must be a boolean")
    if approve_rest and decision != "approve":
        raise BadRequest("approve_rest goes only with decision 'approve'")
    with _lock:
        run = _get_run(run_id)
        status = run["status"]
        run_dir = Path(run["run_dir"])
        live = bool(run.get("live"))
    if status != WAITING_STATUS:
        raise Conflict(f"the run is {status!r}, not {WAITING_STATUS!r}: nothing to resume")
    agent = _agent_run(run_dir) or {}
    pending = agent.get("pending")
    if agent.get("status") != WAITING_STATUS or not isinstance(pending, dict):
        raise Conflict(f"the agent's run.json is {agent.get('status')!r} without a pending stop: nothing to resume")
    live = live or agent.get("mode") == "live"
    if live and payload.get("confirm_spend") is not True:
        raise BadRequest("resuming a live run makes paid API calls: send {\"confirm_spend\": true}")
    refusal = budget_stop.review_switch_refusal(pending.get("point")) if approve_rest else None
    if refusal:
        raise BadRequest(refusal)
    file_payload = _decision_file_payload(decision, payload.get("payload"), payload.get("comment"))
    _prevalidate(_with_todays_money(pending, agent), decision, file_payload, run_dir)  # first: what the stop allows
    if decision == "reject" and not (isinstance(file_payload, dict) and str(file_payload.get("comment") or "").strip()):
        raise BadRequest("reject needs a comment for the agent (comment, or payload {\"comment\": ...})")

    payload_file = None
    if file_payload is not None:
        ddir = run_dir / DECISIONS_DIR
        ddir.mkdir(parents=True, exist_ok=True)
        n = len(list(ddir.glob("*.json"))) + 1
        payload_file = ddir / f"{n:03d}-{pending.get('point') or 'stop'}-{decision}.json"
        _atomic_write_json(payload_file, file_payload)
    cmd = build_resume_command(run_dir, decision, payload_file, live=live, approve_rest=approve_rest)
    with _lock:
        run = _get_run(run_id)
        if run["status"] != WAITING_STATUS:  # raced with another resume / cancel
            raise Conflict(f"the run is {run['status']!r}, not {WAITING_STATUS!r}: nothing to resume")
        run["status"] = "queued"
        run["cancel_requested"] = False
        run["error"] = None
        run["resume_from"] = pending.get("interrupt_id")
        if approve_rest:
            run["review_mode"] = "auto"  # the child stores it in run.json as well
            run["auto_approve"] = True
        run["server_pid"] = os.getpid()
        run["server_identity"] = _lab().process_identity(os.getpid())
        _persist_locked(run)
    _start_worker(run_id, cmd, live, f"resume {pending.get('point')}: {decision}"
                  + (" (approve the rest)" if approve_rest else ""))
    out = {"run_id": run_id, "status": "queued", "point": pending.get("point"), "decision": decision}
    if approve_rest:
        out["approve_rest"] = True
    return out


def _with_todays_money(pending: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    """The budget stop of a live run is checked against today's remainder of the day log."""
    if pending.get("point") != budget_stop.BUDGET:
        return pending
    return {**pending, "request": budget_stop.with_todays_day_left(agent, pending.get("request") or {},
                                                                   spend_log=_spend_log())}


def _message_text(payload: dict[str, Any]) -> str:
    """The text of a follow-up: ``text``, or the fixed text of ``preset``."""
    text, preset = payload.get("text"), payload.get("preset")
    if preset is not None:
        if text is not None:
            raise BadRequest("send either text or preset, not both")
        if not isinstance(preset, str) or preset not in followup.PRESET_MESSAGES:
            raise BadRequest(f"unknown preset {preset!r}; one of {', '.join(followup.PRESET_MESSAGES)}")
        return followup.PRESET_MESSAGES[preset]
    try:
        return followup.clean_message(text if isinstance(text, str) else None)
    except RunError as exc:
        raise BadRequest(str(exc)) from None


def api_message(run_id: str, payload: Any) -> dict[str, Any]:
    """``POST runs/<id>/message``: a follow-up to a finished run, in a new process
    on the same conversation. 409 while the run is going or waiting at a stop."""
    if not isinstance(payload, dict):
        raise BadRequest("the request body must be a JSON object")
    unknown = sorted(set(payload) - MESSAGE_KEYS)
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")
    text = _message_text(payload)
    with _lock:
        run = dict(_get_run(run_id))
    run_dir = Path(run["run_dir"])
    agent = _agent_run_if_idle(run)
    view = _followup_view(run, agent)
    if not view["can_message"]:
        raise Conflict(view["reason"] or "this run cannot take a follow-up message")
    if payload.get("preset") == "lean" and not view["lean"]["available"]:
        raise Conflict(view["lean"]["reason"] or "formal verification is not available for this run")
    agent = agent or {}
    live = bool(run.get("live")) or agent.get("mode") == "live"
    if live:
        if payload.get("confirm_spend") is not True:
            raise BadRequest("a follow-up on a live run makes paid API calls: send {\"confirm_spend\": true}")
        refusal = followup.day_refusal(agent, _spend_log())
        if refusal:
            raise Conflict(refusal)
    ddir = run_dir / DECISIONS_DIR
    ddir.mkdir(parents=True, exist_ok=True)
    message_file = ddir / f"message-{uuid.uuid4().hex[:8]}.txt"  # two sends never share a file
    message_file.write_text(text, encoding="utf-8")
    cmd = build_followup_command(run_dir, message_file, live=live)
    snapshots_before = followup.latest_snapshot(run_dir)
    with _lock:
        current = _get_run(run_id)
        conflict = current["status"] != run["status"]  # raced with another message / resume
        if not conflict:
            current["followup_from"] = {"status": current["status"], "result_status": current.get("result_status"),
                                        "error": current.get("error"), "report_revision": agent.get("report_revision"),
                                        "agent_error": agent.get("followup_error"),
                                        "agent_segments": len(agent.get("segments") or []),
                                        "snapshots_before": snapshots_before}
            current["followup_error"] = None
            current["status"] = "queued"
            current["cancel_requested"] = False
            current["error"] = None
            current["server_pid"] = os.getpid()
            current["server_identity"] = _lab().process_identity(os.getpid())
            _persist_locked(current)
        else:
            status_now = current["status"]
    if conflict:
        message_file.unlink(missing_ok=True)
        raise Conflict(f"the run is {status_now!r} now: send the message when it finishes")
    _start_worker(run_id, cmd, live, "follow-up: Lean" if payload.get("preset") == "lean" else "follow-up message")
    return {"run_id": run_id, "status": "queued"}


def api_abandon(run_id: str) -> dict[str, Any]:
    """``POST runs/<id>/abandon``: drop a follow-up that is recorded as in progress while
    no process of the run is alive (the CLI's ``--abandon``): the previous report comes
    back and the run can take a message again. 409 while a process is alive or when
    there is nothing to abandon."""
    with _lock:
        run = _get_run(run_id)
        if run["status"] in ACTIVE_STATUSES or run["status"] == WAITING_STATUS:
            raise Conflict(f"the run is {run['status']!r}: a follow-up can be abandoned only when nothing is running")
        proc, pid, ident = run.get("proc"), run.get("pid"), run.get("pid_identity")
    if proc is not None and proc.poll() is None:
        raise Conflict("a process of the run is still alive")
    if pid and _lab()._pid_matches(pid, ident):
        raise Conflict("a process of the run is still alive")
    if not (_agent_run(Path(run["run_dir"])) or {}).get("followup_in_progress"):
        raise Conflict("this run has no follow-up in progress")
    if not _abandon_agent_followup(run):
        raise Conflict(_not_restored_note(run))
    with _lock:
        run["followup_from"] = None
        run["followup_error"] = None
        run["pid"] = None
        run["pid_identity"] = None
        run["orphan"] = False
        _persist_locked(run)
    return {"run_id": run_id, "status": run["status"]}


def api_cancel(run_id: str) -> dict[str, Any]:
    """Cancel a queued / running run (kills its process) or a run waiting for
    a human (it then cannot be resumed). A follow-up goes back to its previous
    report instead and can be continued. Idempotent on terminal runs."""
    with _lock:
        run = _get_run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": run["status"]}
        waiting = run["status"] == WAITING_STATUS
        waiting_followup = waiting and bool(run.get("followup_from"))
        if waiting and not waiting_followup:
            _settle_cancelled_locked(run, "cancelled by user while waiting for a human decision")
            _persist_locked(run)
            return {"run_id": run_id, "status": run["status"]}
        if waiting_followup:
            run["status"] = "running"  # keeps other readers off run.json, and any decision out, during the restore
        run["cancel_requested"] = True
        proc = run.get("proc")
        orphan_pid = run.get("pid") if run.get("orphan") else None
        orphan_identity = run.get("pid_identity")
    if waiting_followup:
        _settle_cancelled(run, "cancelled by user while waiting for a human decision")
        return {"run_id": run_id, "status": run["status"]}
    with _slots:
        _slots.notify_all()
    if proc is not None:
        _kill(proc)
    elif orphan_pid:
        _lab()._kill_pid(orphan_pid, orphan_identity)
    return {"run_id": run_id, "status": "cancelling"}


# ---------------------------------------------------------------------------
# GET /api/deep/runs, /api/deep/runs/<id>
# ---------------------------------------------------------------------------

def _read_events(run_dir: Path, after: int = 0, limit: int = 10**9) -> dict[str, Any]:
    """progress.jsonl lines from index ``after`` (complete lines only; the
    server's ``_read_progress`` contract)."""
    return _lab()._read_progress(run_dir, after, limit)


def _node_parent(path: str, known: dict[str, Any]) -> str | None:
    cut = path
    while "/" in cut:
        cut = cut.rsplit("/", 1)[0]
        if cut in known:
            return cut
    return None


def fold_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The live view folded from all events: the plan, tool-call counts by
    trust, the subagent / workflow tree with models and cost, the budget, the
    orchestration log and the HITL history."""
    todos: list[Any] = []
    nodes: dict[str, dict[str, Any]] = {}
    tools = {"total": 0, "by_trust": {}, "failed": 0}
    orchestration: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    awaiting: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    signals: list[dict[str, Any]] = []
    done: dict[str, Any] | None = None

    def node(path: str, kind: str | None = None) -> dict[str, Any]:
        n = nodes.get(path)
        if n is None:
            n = nodes[path] = {"node": path, "name": path.rsplit("/", 1)[-1], "kind": kind or (
                "task" if "/" not in path else "subagent"), "allocated_usd": None, "spent_usd": 0.0, "calls": 0,
                "models": {}, "roles": [], "status": "running" if "/" in path else "root", "returned_usd": None}
        return n

    for ev in events:
        kind = ev.get("event")
        if isinstance(ev.get("budget"), dict):
            budget = ev["budget"]
        if isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        if kind == "todos" and isinstance(ev.get("todos"), list):
            todos = ev["todos"]
        elif kind == "tool_call":
            tools["total"] += 1
            t = str(ev.get("trust") or "not_verified")
            tools["by_trust"][t] = tools["by_trust"].get(t, 0) + 1
            if ev.get("ok") is False or ev.get("status") in ("error", "exception"):
                tools["failed"] += 1
        elif kind == "model_call" and isinstance(ev.get("node"), str):
            n = node(ev["node"])
            usd = float(ev.get("usd") or 0.0)
            n["spent_usd"] = round(n["spent_usd"] + usd, 6)
            n["calls"] += 1
            model = str(ev.get("model") or "?")
            n["models"][model] = round(n["models"].get(model, 0.0) + usd, 6)
            role = ev.get("role")
            if role and role not in n["roles"]:
                n["roles"].append(role)
        elif kind == "subagent_start" and isinstance(ev.get("node"), str):
            n = node(ev["node"], ev.get("kind"))
            n["kind"] = ev.get("kind") or n["kind"]
            n["allocated_usd"] = ev.get("allocated_usd")
            n["status"] = "running"
        elif kind == "subagent_end" and isinstance(ev.get("node"), str):
            n = node(ev["node"])
            n["status"] = "closed"
            n["returned_usd"] = ev.get("returned_usd")
        elif kind in ("budget_wrap_up", "budget_refused", "subagent_refused"):
            signals.append({k: ev.get(k) for k in ("event", "ts", "node", "available_usd", "worst_case_usd",
                                                    "tool", "reason") if ev.get(k) is not None})
        elif isinstance(kind, str) and (kind.startswith("spawn_") or kind.startswith("workflow_")):
            orchestration.append({k: v for k, v in ev.items() if k not in ("usage", "budget")})
        elif kind == "hitl_awaiting":
            awaiting = {k: ev.get(k) for k in ("point", "interrupt_id", "ts", "draft", "segment")}
        elif kind == "hitl_decision":
            decisions.append({k: ev.get(k) for k in ("point", "decision", "source", "comment", "edited_fields",
                                                     "interrupt_id", "ts", "segment", "add_usd", "new_limit_usd")})
        elif kind == "done":
            done = {k: ev.get(k) for k in ("status", "verdict", "language_class", "trust", "confidence_cap",
                                           "error", "ts")}

    # tree: every node under its longest known prefix; the subtree cost
    tree_nodes = {p: {**n, "children": []} for p, n in nodes.items()}
    roots: list[dict[str, Any]] = []
    for path, n in tree_nodes.items():
        parent = _node_parent(path, tree_nodes)
        (tree_nodes[parent]["children"] if parent else roots).append(n)

    def total(n: dict[str, Any]) -> float:
        n["subtree_spent_usd"] = round(n["spent_usd"] + sum(total(c) for c in n["children"]), 6)
        return n["subtree_spent_usd"]

    for r in roots:
        total(r)
    return {"todos": todos, "tools": tools, "subagents": roots, "budget": budget, "usage": usage,
            "orchestration": orchestration[-200:], "budget_signals": signals[-50:],
            "hitl_decisions": decisions, "last_awaiting": awaiting, "done": done}


def _file_url(run_id: str, rel: str) -> str:
    return f"{PREFIX}runs/{run_id}/files/{rel}"


def _point_title(point: str) -> str:
    try:
        from tfl_deep_agent import hitl

        return hitl.point_facts(point).title or str(point)
    except Exception:  # noqa: BLE001 - a fallback card: the point's name will do
        return str(point)


def pending_card(run: dict[str, Any], agent: dict[str, Any] | None,
                 awaiting: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """``pending_point`` of a run waiting for a human: the short card
    ``{"point", "title", "summary", "details_md", "editable"}``, else ``None``.

    Source, in order: the card the agent stored in ``run.json``
    (``pending.card``, written with the stop); one built now from the pending
    request (``hitl.pending_card``, a run written before cards existed); the
    card cached when the lab settled the stop (``run.json`` unreadable for a
    moment); a minimal card from the last ``hitl_awaiting`` event and
    ``hitl.md``. So a waiting run never shows ``pending_point: null``."""
    if run.get("status") != WAITING_STATUS:
        return None
    agent = agent or {}
    pending = agent.get("pending") if isinstance(agent.get("pending"), dict) else None
    if pending is not None:
        pending = _with_todays_money(pending, agent)
        point = pending.get("point")
        card = pending.get("card") if isinstance(pending.get("card"), dict) else None
        if card is None:
            try:
                from tfl_deep_agent import hitl

                card = hitl.pending_card(pending.get("request") or {}, point=point,
                                         draft=agent.get("draft") if point == "report" else None)
            except Exception:  # noqa: BLE001 - fall back to the minimal card below
                card = None
        actions = (pending.get("request") or {}).get("action_requests") or []
        if card is not None and point == budget_stop.BUDGET and actions:  # the stored line has the asked remainder
            card = {**card, "summary": budget_stop.budget_summary(actions[0].get("args") or {})}
        if card is not None:
            return {"point": point or card.get("point"), "title": card.get("title") or "",
                    "summary": [str(s) for s in card.get("summary") or []][:4],
                    "details_md": str(card.get("details_md") or ""),
                    "editable": list(card.get("editable") or [])}
    cached = run.get("pending_card")
    if isinstance(cached, dict):
        return cached
    point = (pending or {}).get("point") or (awaiting or {}).get("point")
    if not point:
        return None
    try:
        details = (Path(run["run_dir"]) / "hitl.md").read_text(encoding="utf-8")
    except OSError:
        details = ""
    return {"point": point, "title": _point_title(point), "summary": [],
            "details_md": details, "editable": []}


def _review_mode(run: dict[str, Any], agent: dict[str, Any] | None) -> str:
    """The run's effective review mode: the agent's ``run.json`` (the
    authority; ``auto`` after approve_rest), else the lab record."""
    mode = (agent or {}).get("review_mode") or run.get("review_mode")
    if mode in REVIEW_MODES:
        return str(mode)
    return "auto" if (agent or {}).get("auto_approve") or run.get("auto_approve") else "all"


def _hitl_view(run: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any]:
    """The pending HITL card (from the agent's run.json; the authority), the
    points and the review history."""
    run_dir = Path(run["run_dir"])
    agent = agent or {}
    out: dict[str, Any] = {"points": agent.get("hitl"), "auto_approve": agent.get("auto_approve"),
                           "review_mode": _review_mode(run, agent), "approve_rest": agent.get("approve_rest"),
                           "budget_stop": budget_stop.budget_stop_on(agent) if agent else None,
                           "reviews": agent.get("reviews") or [], "pending": None}
    pending = agent.get("pending")
    if run["status"] == WAITING_STATUS and isinstance(pending, dict):
        request = _with_todays_money(pending, agent).get("request") or {}
        configs = {c.get("action_name"): c for c in request.get("review_configs") or [] if isinstance(c, dict)}
        # one stop may carry several tool calls (HumanInTheLoopMiddleware puts every call of
        # one model turn that needs review into one interrupt), and hitl.resume_value applies
        # the decision to all of them: show them all, and allow only what fits every one
        actions = []
        for act in request.get("action_requests") or []:
            if not isinstance(act, dict):
                continue
            own = list((configs.get(act.get("name")) or {}).get("allowed_decisions") or ["approve", "reject"])
            actions.append({"name": act.get("name"), "args": act.get("args"),
                            "description": act.get("description"), "allowed_decisions": own})
        allowed = list(actions[0]["allowed_decisions"]) if actions else ["approve", "reject"]
        for act in actions[1:]:
            allowed = [d for d in allowed if d in act["allowed_decisions"]]
        if len(actions) > 1:
            allowed = [d for d in allowed if d != "edit"]  # resume_value: edit needs exactly one action
        first = actions[0] if actions else {}
        try:
            card = (run_dir / "hitl.md").read_text(encoding="utf-8")
        except OSError:
            card = None
        view = {"point": pending.get("point"), "interrupt_id": pending.get("interrupt_id"),
                "asked_at": pending.get("asked_at"), "allowed_decisions": allowed,
                "actions": actions, "action_count": len(actions),
                "action": first.get("name"), "args": first.get("args"), "description": first.get("description"),
                "card": card, "card_url": _file_url(run["run_id"], "hitl.md"),
                "summary_card": pending_card(run, agent)}
        src = agent.get("input") or {}
        if src.get("stored_as"):
            view["source_url"] = _file_url(run["run_id"], str(src["stored_as"]))
            view["source"] = src
        if pending.get("point") == "report":
            view["draft"] = agent.get("draft")
            view["draft_urls"] = {s: _file_url(run["run_id"], f"result.{s}") for s in ("md", "html", "json")
                                  if (run_dir / f"result.{s}").is_file()}
        out["pending"] = view
    return out


def _lean_view(agent: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Whether the button «Формально верифицировать (Lean)» applies
    (``followup.lean_refusal``), and what it would run."""
    def off(reason: str) -> dict[str, Any]:
        return {"available": False, "reason": reason, "model": None, "subagent_limit_usd": None}

    refusal = followup.lean_refusal(agent, run_dir)
    if refusal:
        return off(refusal)
    settings = agent.get("settings") if isinstance(agent.get("settings"), dict) else {}
    try:
        from tfl_deep_agent.agent import resolve_role_model

        model = resolve_role_model(settings, "lean")
    except Exception as exc:  # noqa: BLE001 - not offered when the model cannot be resolved
        return off(f"the lean role has no usable model: {exc}")
    limit = (settings.get("budget") or {}).get("per_subagent_max_usd")
    return {"available": True, "reason": None, "model": model, "subagent_limit_usd": limit}


def _followup_view(run: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any]:
    """``followup`` of the detail: can a message be sent now, and the Lean button. This
    adds the lab's own cases to ``followup.followup_refusal``. ``agent``: ``None``
    while a process of the run is alive."""
    in_progress = bool(run.get("followup_from"))
    error = run.get("followup_error") or (agent or {}).get("followup_error")

    def closed(reason: str) -> dict[str, Any]:
        return {"can_message": False, "reason": reason, "in_progress": in_progress, "error": error,
                "lean": {"available": False, "reason": reason, "model": None, "subagent_limit_usd": None}}

    status = run.get("status")
    if status in ACTIVE_STATUSES:
        return closed("the agent is working; wait for it to finish")
    if status == WAITING_STATUS:
        return closed("the agent is waiting for your decision")
    agent = agent or {}
    run_dir = Path(run["run_dir"])
    if agent.get("followup_in_progress"):  # the lab is not waiting for it: nothing here will finish it
        out = closed(f"{followup.STUCK_REFUSAL}: {followup.abandon_command(run_dir)}")
        out["can_abandon"] = True  # POST runs/<id>/abandon does the same
        return out
    if status in ("cancelled", "interrupted"):
        return closed(f"this run was {status}: start a new task")
    refusal = followup.followup_refusal(agent, run_dir)
    if refusal:
        return closed(refusal)
    return {"can_message": True, "reason": None, "in_progress": False, "error": error,
            "lean": _lean_view(agent, run_dir)}


def _result_view(run: dict[str, Any], revision: Any = None, *, source: str = "") -> dict[str, Any] | None:
    """The view of ``result.*``, or of the same files in ``source`` (a directory of the
    run). ``revision``: ``run.json`` ``report_revision``, which tracks the report object,
    not the files. From the second one on the URLs carry it, so a browser and the report
    panel take the new report."""
    run_dir = Path(run["run_dir"])
    prefix = f"{source}/" if source else ""
    data = _read_json(run_dir / prefix / "result.json")
    if not isinstance(data, dict):
        return None
    report = data.get("report") if isinstance(data.get("report"), dict) else {}
    query = f"?v={revision}" if isinstance(revision, int) and revision > 1 else ""
    out = {"status": data.get("status"), "draft": data.get("status") == WAITING_STATUS, "report_revision": revision,
           "verdict": report.get("verdict"), "language_class": report.get("language_class"),
           "trust": data.get("trust"), "confidence_cap": data.get("confidence_cap"),
           "human_review": data.get("human_review"), "template": rendered_template(data),
           "run_template": data.get("template"), "reason": data.get("reason"),
           "urls": {s: _file_url(run["run_id"], f"{prefix}result.{s}") + query for s in ("json", "md", "html")
                    if (run_dir / prefix / f"result.{s}").is_file()}}
    out["can_render"] = _can_render(run, data)
    out["views"] = {t: {s: _file_url(run["run_id"], f"{VIEWS_DIR}/{t}.{s}") for s in ("md", "html")}
                    for t in _rendered_views(run_dir)}
    return out


def _followup_result_view(run: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any] | None:
    """``result`` during a follow-up: the draft while HITL 2 waits (no process is writing
    then), else the newest snapshot this follow-up made. Before it exists (or while its
    files are being copied) the agent has not touched ``result.*`` yet, so those are
    still the previous report."""
    record = run["followup_from"]
    pending = (agent or {}).get("pending")
    if run["status"] == WAITING_STATUS and isinstance(pending, dict) and pending.get("point") == "report":
        return _result_view(run, (agent or {}).get("report_revision"))
    revision = record.get("report_revision")
    newest = followup.latest_snapshot(run["run_dir"])
    view = None
    if newest > int(record.get("snapshots_before") or 0):
        view = _result_view(run, revision, source=f"{REPORT_HISTORY_DIR}/{newest}")
    return view or _result_view(run, revision)


def _detail_result(run: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any] | None:
    if run.get("followup_from"):
        return _followup_result_view(run, agent)
    if run["status"] in ACTIVE_STATUSES:
        return None
    return _result_view(run, (agent or {}).get("report_revision"))


def rendered_template(data: Any) -> str | None:
    """The template ``result.*`` was rendered with: ``rendered.template`` (a HITL 2 edit may
    change it), else ``report.template``, else the run's start template."""
    if not isinstance(data, dict):
        return None
    rendered = data.get("rendered") if isinstance(data.get("rendered"), dict) else {}
    report = data.get("report") if isinstance(data.get("report"), dict) else {}
    return rendered.get("template") or report.get("template") or data.get("template")


def _rendered_views(run_dir: Path) -> list[str]:
    d = run_dir / VIEWS_DIR
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.html") if (d / f"{p.stem}.md").is_file())


def _can_render(run: dict[str, Any], data: Any) -> bool:
    """A finished run with an accepted report can be viewed in another template."""
    return (run.get("status") == "completed" and isinstance(data, dict) and data.get("status") == "submitted"
            and isinstance(data.get("report"), dict))


def api_render(run_id: str, payload: Any) -> dict[str, Any]:
    """``POST runs/<id>/render {template}``: render the finished run's accepted report with
    another template, **for viewing**. The output goes to ``views/<template>.{md,html}``;
    ``result.*`` (what the human approved at HITL 2) is never rewritten. Pure code: no model
    call, no spend."""
    if not isinstance(payload, dict):
        raise BadRequest("the request body must be a JSON object")
    unknown = sorted(set(payload) - {"template"})
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")
    from tfl_deep_agent.reports import render as render_mod

    template = payload.get("template")
    if template not in render_mod.SELECTABLE_TEMPLATES:
        raise BadRequest(f"unknown template {template!r}; one of {', '.join(render_mod.SELECTABLE_TEMPLATES)}")
    with _lock:
        run = dict(_get_run(run_id))
    run_dir = Path(run["run_dir"])
    data = _read_json(run_dir / "result.json")
    if not _can_render(run, data):
        raise Conflict("only a finished run with an accepted report (status submitted) can be re-rendered")
    urls = {s: _file_url(run_id, f"{VIEWS_DIR}/{template}.{s}") for s in ("md", "html")}
    if template == rendered_template(data):
        return {"run_id": run_id, "template": template, "same_as_result": True,
                "urls": {s: _file_url(run_id, f"result.{s}") for s in ("md", "html")}}
    result = {k: v for k, v in data.items() if k != "rendered"}
    rendered = data.get("rendered") if isinstance(data.get("rendered"), dict) else {}
    budget = data.get("budget") if isinstance(data.get("budget"), dict) else None
    agent = _agent_run(run_dir) or {}
    records = (agent.get("budget") or {}).get("records") if isinstance(agent.get("budget"), dict) else None
    if budget is not None and records:
        budget = {**budget, "records": records}
    evidence = run_dir / "evidence.jsonl"
    try:
        rep = render_mod.render_report(result, evidence=evidence if evidence.is_file() else None, budget=budget,
                                       task_text=rendered.get("task") or result.get("task"),
                                       human_review=rendered.get("human_review") or result.get("human_review"),
                                       template=template, status=data.get("status"))
    except (ValueError, TypeError) as exc:
        raise Conflict(f"cannot render the report with {template!r}: {exc}") from None
    vdir = run_dir / VIEWS_DIR
    vdir.mkdir(exist_ok=True)
    (vdir / f"{template}.md").write_text(rep.markdown, encoding="utf-8")
    (vdir / f"{template}.html").write_text(rep.html, encoding="utf-8")
    return {"run_id": run_id, "template": template, "same_as_result": False, "urls": urls,
            "note": "a view only: result.* keeps the template the report was approved with"}


def _summary_locked(run: dict[str, Any], agent: dict[str, Any] | None = None,
                    awaiting: dict[str, Any] | None = None) -> dict[str, Any]:
    if agent is None:
        agent = _agent_run_if_idle(run) or {}
    budget = agent.get("budget") or {}
    result = agent.get("result") if isinstance(agent.get("result"), dict) else {}
    started = run.get("started") or run.get("created")
    segments = run.get("segments") or []
    if run["status"] in ACTIVE_STATUSES and segments:
        # a continuation: the clock of "the agent is working" counts from its own start
        # (none while it is still queued)
        started = (segments[-1] or {}).get("started") if run["status"] == "running" else None
    end = run.get("finished") if run.get("finished") else (_now() if run["status"] in ACTIVE_STATUSES else None)
    return {
        "run_id": run["run_id"], "status": run["status"], "live": bool(run.get("live")),
        "mode": run.get("mode"),
        "template": run.get("report_template") or run.get("template") or agent.get("template"),
        "run_template": run.get("template") or agent.get("template"),
        "input": run.get("input"), "created": run.get("created"), "started": run.get("started"),
        "finished": run.get("finished"), "elapsed": (end - started) if (end and started) else None,
        "error": run.get("error"), "agent_status": agent.get("status"),
        "result_status": run.get("result_status"),
        "pending_point": pending_card(run, agent, awaiting),
        "review_mode": _review_mode(run, agent),
        "verdict": result.get("verdict"), "trust": result.get("trust"),
        "confidence_cap": result.get("confidence_cap"),
        "spent_usd": budget.get("spent_usd"), "limit_usd": budget.get("limit_usd"),
        "segments": len(run.get("segments") or []), "detail_url": f"{PREFIX}runs/{run['run_id']}",
    }


def api_runs() -> dict[str, Any]:
    with _lock:
        runs = [dict(r) for r in _runs.values()]
    out = [_summary_locked(r) for r in runs]
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return {"runs": out}


def _int_param(value: Any, name: str, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        n = int(str(value))
    except ValueError:
        raise BadRequest(f"{name} must be an integer") from None
    return max(lo, min(hi, n))


def _spend_log() -> Any:
    """The day's spend log of this server's runs."""
    from tfl_deep_agent.middleware import budget as budget_mod

    return budget_mod.DailySpendLog(spend_dir() / budget_mod.SPEND_LOG_NAME)


def _day_view(settings: Any = None) -> dict[str, Any] | None:
    try:
        settings = settings or load_deep_settings()
        log = _spend_log()
        spent = log.day_total()
        limit = float(settings.budget.per_day_usd)
        return {"day": log.today(), "spent_usd": round(spent, 6), "limit_usd": limit,
                "remaining_usd": round(limit - spent, 6),
                "can_start": float(settings.budget.per_task_usd) <= limit - spent + 1e-9}
    except Exception:  # noqa: BLE001 - a view
        return None


def api_detail(run_id: str, after: Any = None, limit: Any = None) -> dict[str, Any]:
    after_n = _int_param(after, "after", 0, 0, 10**9)
    limit_n = _int_param(limit, "limit", 1000, 1, 5000)
    with _lock:
        run = dict(_get_run(run_id))
        run["lines"] = list(run["lines"][-50:])
    run_dir = Path(run["run_dir"])
    agent = _agent_run_if_idle(run)
    progress = _read_events(run_dir, after_n, limit_n)
    view = fold_events(_read_events(run_dir)["events"])
    budget = dict(view["budget"] or {})
    if not budget and agent and isinstance(agent.get("budget"), dict):
        budget = {"spent_usd": agent["budget"].get("spent_usd"), "limit_usd": agent["budget"].get("limit_usd")}
    budget["day"] = _day_view()
    summary = _summary_locked(run, agent or {}, view.get("last_awaiting"))
    return {
        "run": summary,
        "pending_point": summary["pending_point"],
        "review_mode": summary["review_mode"],
        "events": progress["events"], "next": progress["next"], "total": progress["total"],
        "usage": view["usage"], "todos": view["todos"], "tools": view["tools"], "subagents": view["subagents"],
        "orchestration": view["orchestration"], "budget": budget, "budget_signals": view["budget_signals"],
        "hitl": {**_hitl_view(run, agent), "decisions": view["hitl_decisions"]},
        "result": _detail_result(run, agent),
        "done": view["done"],
        "log_tail": run["lines"],
        "followup": _followup_view(run, agent),
    }


def api_file(run_id: str, rel: str) -> FileResponse:
    with _lock:
        run_dir = Path(_get_run(run_id)["run_dir"])
    rel = rel.strip("/")
    if rel not in SERVED_FILES and not any(rel.startswith(d) for d in SERVED_DIRS):
        raise NotFound("not found")
    path = _lab()._files_in(run_dir, recursive=True).get(rel)
    if path is None:
        raise NotFound("not found")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# GET /api/deep/estimate
# ---------------------------------------------------------------------------

ESTIMATE_NOTE = "measured from the recorded live deep-agent runs (mock runs spend nothing)"
ESTIMATE_NO_DATA_NOTE = "no data: no finished live deep-agent run yet; the hard cap is per_task_usd"


def _past_live_costs(template: str | None = None) -> list[float]:
    costs = []
    with _lock:
        busy = {rid for rid, r in _runs.items() if r.get("status") in ACTIVE_STATUSES}
    for rid, d in _run_dirs().items():
        if rid in busy:  # never open the run.json of a live process (see _agent_run_if_idle)
            continue
        agent = _agent_run(d)
        if not agent or agent.get("mode") != "live" or agent.get("status") not in FINAL_STATUSES:
            continue
        if template and agent.get("template") != template:
            continue
        spent = (agent.get("budget") or {}).get("spent_usd")
        if isinstance(spent, (int, float)) and not isinstance(spent, bool):
            costs.append(float(spent))
    return costs


def api_estimate(template: Any = None, override: Any = None) -> dict[str, Any]:
    mod = _settings_mod()
    settings = load_deep_settings()
    if override:
        try:
            data = json.loads(override) if isinstance(override, str) else override
        except ValueError:
            raise BadRequest("settings_override must be a JSON object") from None
        if not isinstance(data, dict):
            raise BadRequest("settings_override must be a JSON object")
        try:
            settings = mod.apply_overrides(settings, data)
        except mod.SettingsError as exc:
            raise BadRequest(_settings_problems(exc)) from None
    template = template or None
    costs = _past_live_costs(template)
    b = settings.budget
    out: dict[str, Any] = {
        "template": template,
        "cap_usd": b.per_task_usd, "reserve_for_report_usd": b.report_reserve_usd,
        "per_subagent_max_usd": b.per_subagent_max_usd, "per_day_usd": b.per_day_usd,
        "day": _day_view(settings),
        "run": {"n": len(costs),
                "estimated_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
                "low_usd": round(min(costs), 4) if costs else None,
                "high_usd": round(min(max(costs), b.per_task_usd), 4) if costs else None,
                "note": f"{ESTIMATE_NOTE}: mean of {len(costs)} run(s)" if costs else ESTIMATE_NO_DATA_NOTE},
        "roles": settings.models.roles if hasattr(settings.models, "roles") else None,
        "mock_note": "mock runs replay a script and spend nothing",
    }
    try:
        from tfl_deep_agent.orchestration.model_policy import model_cost_table

        out["models"] = model_cost_table(settings)
    except Exception:  # noqa: BLE001 - the table is extra
        out["models"] = None
    if hasattr(out["roles"], "items"):
        out["roles"] = dict(out["roles"])
    return out


# ---------------------------------------------------------------------------
# Restore on server start
# ---------------------------------------------------------------------------

def restore_runs(*, adopt: bool = True) -> list[str]:
    """Reload the deep runs from ``<DEEP_RUNS_DIR>/*/lab.json``. A run recorded
    as queued / running whose process is gone is settled from the agent's
    ``run.json`` (a HITL stop stays ``awaiting_human`` and can be resumed);
    one whose process is still alive is watched (``adopt``) and settled when it
    ends. Returns the ids of the runs that still have a live process."""
    lab = _lab()
    live_ids: list[str] = []
    for run_id, run_dir in sorted(_run_dirs().items()):
        with _lock:
            if run_id in _runs:
                continue
        rec = _read_json(run_dir / LAB_FILE)
        if not isinstance(rec, dict):
            continue
        run = _new_entry(run_id, run_dir, live=bool(rec.get("live")), template=rec.get("template"),
                         input_info=rec.get("input") or {}, auto_approve=bool(rec.get("auto_approve")))
        for key in _RECORD_KEYS:
            if key in rec and key != "run_id":
                run[key] = rec[key]
        run["segments"] = list(run.get("segments") or [])
        try:
            run["lines"] = (run_dir / LOG_FILE).read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES_KEPT:]
        except OSError:
            pass
        if run["status"] not in ACTIVE_STATUSES | TERMINAL_STATUSES | {WAITING_STATUS}:
            run["status"] = "interrupted"
        active = run["status"] in ACTIVE_STATUSES
        alive = active and lab._pid_matches(run.get("pid"), run.get("pid_identity"))
        with _lock:
            _runs[run_id] = run
            if alive:
                run["orphan"] = True
                live_ids.append(run_id)
        if active and not alive:
            _settle_process_end(run, reason="server restarted while the run was in progress")
    if adopt:
        for run_id in live_ids:
            threading.Thread(target=_watch_orphan, args=(run_id,), daemon=True).start()
    return live_ids


def _watch_orphan(run_id: str) -> None:
    lab = _lab()
    while True:
        with _lock:
            run = _runs.get(run_id)
            if run is None or run["status"] not in ACTIVE_STATUSES or not run.get("orphan"):
                return
            pid, ident = run.get("pid"), run.get("pid_identity")
            cancel = bool(run.get("cancel_requested"))
        alive = lab._pid_matches(pid, ident)
        if cancel and alive and pid:
            lab._kill_pid(pid, ident)
        if not alive:
            run = _runs.get(run_id)
            if run is None or run["status"] not in ACTIVE_STATUSES:
                return
            if cancel and _followup_record_gone(run):
                # as in _worker: the cancel reached a follow-up that had already finished;
                # what the process wrote is the truth (its exit code is unknown here)
                with _lock:
                    run["cancel_requested"] = False
                _settle_process_end(run, reason="process ended while the server was restarted")
            elif cancel:
                _settle_cancelled(run, "cancelled by user")
            else:
                _settle_process_end(run, reason="process ended while the server was restarted")
            return
        time.sleep(_ORPHAN_POLL_SECONDS)


# ---------------------------------------------------------------------------
# Dispatch (called by server.Handler for every /api/deep/ path)
# ---------------------------------------------------------------------------

def handles(path: str) -> bool:
    return path.startswith(PREFIX) or path == PREFIX.rstrip("/")


def _route(method: str, path: str, query: dict[str, list[str]], payload: Any) -> Any:
    rest = path[len(PREFIX):] if path.startswith(PREFIX) else ""
    parts = [p for p in rest.split("/") if p != ""]
    q = lambda k: (query.get(k) or [None])[0]  # noqa: E731

    if parts == ["runs"]:
        if method == "GET":
            return api_runs()
        if method == "POST":
            return api_create(payload)
    elif len(parts) >= 2 and parts[0] == "runs":
        run_id = parts[1]
        if len(parts) == 2 and method == "GET":
            return api_detail(run_id, after=q("after"), limit=q("limit"))
        if len(parts) == 3 and parts[2] == "resume" and method == "POST":
            return api_resume(run_id, payload)
        if len(parts) == 3 and parts[2] == "message" and method == "POST":
            return api_message(run_id, payload)
        if len(parts) == 3 and parts[2] == "abandon" and method == "POST":
            return api_abandon(run_id)
        if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
            return api_cancel(run_id)
        if len(parts) == 3 and parts[2] == "render" and method == "POST":
            return api_render(run_id, payload)
        if len(parts) >= 4 and parts[2] == "files" and method == "GET":
            return api_file(run_id, "/".join(parts[3:]))
    elif parts == ["estimate"] and method == "GET":
        return api_estimate(q("template"), q("settings_override"))
    elif parts == ["settings"]:
        if method == "GET":
            return api_get_settings()
        if method == "PUT":
            return api_put_settings(payload)
    elif parts == ["mock-scripts"] and method == "GET":
        return api_mock_scripts()
    raise NotFound(f"no route for {method} {path}")


def dispatch(method: str, path: str, query: str = "", payload: Any = None) -> tuple[int, Any]:
    """``(HTTP status, JSON object | FileResponse)`` for one request."""
    try:
        return 200, _route(method, path, parse_qs(query or ""), payload)
    except NotFound as exc:
        return 404, {"error": str(exc)}
    except Conflict as exc:
        return 409, {"error": str(exc)}
    except ImportError as exc:
        return 503, {"error": f"the deep agent is not available (pip install -e .[deep]): {exc}"}
    except (BadRequest, ValueError) as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s %s failed", method, path)
        return 500, {"error": f"internal: {exc}"}


__all__ = ["DEEP_RUNS_DIR", "FileResponse", "PREFIX", "api_abandon", "api_cancel", "api_create", "api_detail", "api_estimate",
           "api_get_settings", "api_message", "api_put_settings", "api_render", "api_resume",
           "build_followup_command", "build_resume_command", "build_start_command",
           "dispatch", "fold_events", "handles", "max_body_bytes", "restore_runs"]
