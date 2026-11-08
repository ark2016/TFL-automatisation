"""TFL Lab: the "Deep Agent" routes (``tfl_deep_agent``, P7 backend).

    GET  /api/deep/runs                       -> {"runs": [summary, ...]} (newest first)
    POST /api/deep/runs                       -> {"run_id", "status", "detail_url"}
         {text | file_b64 + mime [+ name], template?, settings_override?,
          mock_script? (an examples/mock name or a script object; mock mode, the default),
          live?, confirm_spend? (required with live), auto_approve?}
    GET  /api/deep/runs/<id>[?after=N&limit=M]
                                              -> {"run", "events", "next", "total", "usage",
                                                  "todos", "tools", "subagents", "budget",
                                                  "hitl", "result", "log_tail"}
         events are the progress.jsonl lines from index N (0-based) on (the same
         polling contract as /api/runs/<id>); the other keys are folded from the
         whole file / the run directory, so a client that starts polling late
         still sees the plan, the subagent tree, the budget bar and the HITL card.
    GET  /api/deep/runs/<id>/files/<path>     -> result.{json,md,html}, hitl.md, evidence.jsonl,
                                                 progress.jsonl, run.log, input/*, upload/*, views/*
    POST /api/deep/runs/<id>/resume           -> {decision: approve | edit | reject, payload?,
                                                  comment?, confirm_spend? (live runs)}
    POST /api/deep/runs/<id>/cancel           -> {"run_id", "status"}
    POST /api/deep/runs/<id>/render           -> {template}: the finished, accepted report rendered
                                                 with another template into views/<t>.{md,html}
                                                 (a view; result.* is never rewritten)
    GET  /api/deep/estimate[?template=&settings_override=<json>]
                                              -> money limits from the settings, today's spend,
                                                 measured cost of past live deep runs
    GET  /api/deep/settings, PUT /api/deep/settings (a partial object merged over the current)
    GET  /api/deep/mock-scripts               -> the replay scripts of tfl_deep_agent/examples/mock

Runs execute in a subprocess, ``python -m tfl_deep_agent ... --save <run_dir>
--no-dialog``, like the pipelines. A HITL stop ends that process with exit code
3 and the agent's ``run.json`` status ``awaiting_human``; ``.../resume`` starts
a new process ``--resume <run_dir> --decision ...``. So a waiting run needs no
live process and survives a server restart.

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

``tfl_deep_agent`` (settings, intake, hitl) is imported lazily, so the rest of
TFL Lab works without the ``[deep]`` extra.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

logger = logging.getLogger("tfl_lab.deep")

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "/api/deep/"
DEEP_RUNS_DIR = ROOT / ".tfl_lab_runs" / "deep"
MOCK_DIR = ROOT / "tfl_deep_agent" / "examples" / "mock"
#: deep-agent settings file; None = ``tfl_deep_agent.settings.settings_path()``
#: (``$TFL_DEEP_SETTINGS`` or ``tfl_deep_agent/settings.json``)
SETTINGS_PATH: Path | None = None

LAB_FILE = "lab.json"
AGENT_RUN_FILE = "run.json"
PROGRESS_FILE = "progress.jsonl"
LOG_FILE = "run.log"
UPLOAD_DIR = "upload"
SETTINGS_SNAPSHOT = "ui_settings.json"
MOCK_SNAPSHOT = "ui_mock_script.json"
DECISIONS_DIR = "ui_decisions"
#: re-rendered views of a finished report in another template (POST runs/<id>/render)
VIEWS_DIR = "views"
SERVED_FILES = {"result.json", "result.md", "result.html", "hitl.md", "evidence.jsonl", PROGRESS_FILE, LOG_FILE}
SERVED_DIRS = ("input/", f"{UPLOAD_DIR}/", f"{VIEWS_DIR}/")

RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
MOCK_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\.json")

MB = 1024 * 1024
BODY_SLACK_BYTES = 1 * MB
MAX_BODY_CAP_BYTES = 64 * MB
LOG_LINES_KEPT = 2000

EXIT_SUBMITTED, EXIT_NO_REPORT, EXIT_USAGE, EXIT_AWAITING = 0, 1, 2, 3

MAX_CONCURRENT_RUNS = int(os.environ.get("TFL_LAB_MAX_DEEP_RUNS", "2"))
#: live deep runs at once (one: their per-day reservations are per process, see _acquire_slot)
MAX_CONCURRENT_LIVE_RUNS = 1
_SLOT_POLL_SECONDS = 0.2
_WAIT_POLL_SECONDS = 0.5
_ORPHAN_POLL_SECONDS = 2.0

ACTIVE_STATUSES = {"queued", "running"}
WAITING_STATUS = "awaiting_human"
TERMINAL_STATUSES = {"completed", "error", "cancelled", "interrupted"}
#: the agent's final statuses (run.json / result.json)
AGENT_FINAL = {"submitted", "incomplete", "budget_exhausted", "error"}

CREATE_KEYS = {"text", "file_b64", "mime", "name", "template", "settings_override", "mock_script", "live",
               "confirm_spend", "auto_approve"}
RESUME_KEYS = {"decision", "payload", "comment", "confirm_spend", "reviewer"}
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
                "auto_approve", "resume_from", "report_template")


def _new_entry(run_id: str, run_dir: Path, *, live: bool, template: str | None, input_info: dict[str, Any],
               auto_approve: bool) -> dict[str, Any]:
    lab = _lab()
    return {
        "run_id": run_id, "run_dir": str(run_dir), "status": "queued", "live": live,
        "mode": "live" if live else "mock", "template": template, "input": input_info,
        "created": _now(), "started": None, "finished": None, "pid": None, "pid_identity": None,
        "server_pid": os.getpid(), "server_identity": lab.process_identity(os.getpid()),
        "error": None, "result_status": None, "segments": [], "auto_approve": auto_approve,
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
    data = _read_json(run_dir / AGENT_RUN_FILE)
    return data if isinstance(data, dict) else None


def _agent_run_if_idle(run: dict[str, Any]) -> dict[str, Any] | None:
    """The agent's run.json, but only while no process of the run is alive:
    on Windows a reader holding the file open makes the agent's atomic
    replace fail (a sharing violation), so a live run is shown from
    progress.jsonl (append-only) instead."""
    if run.get("status") in ACTIVE_STATUSES:
        return None
    return _agent_run(Path(run["run_dir"]))


def _settle_from_disk_locked(run: dict[str, Any], *, rc: int | None = None, tail: str = "",
                             reason: str | None = None) -> None:
    """The lab status after the run's process ended (or vanished), read from
    the agent's ``run.json``: awaiting_human / completed (+ result_status) /
    error / interrupted."""
    agent = _agent_run(Path(run["run_dir"])) or {}
    st = agent.get("status")
    run["finished"] = _now()
    run["pid"] = None
    run["pid_identity"] = None
    run["orphan"] = False
    if st == WAITING_STATUS and agent.get("pending"):
        # still waiting, whatever the exit code: a resume that failed before it applied the
        # decision (a CLI refusal such as "--live needs ANTHROPIC_API_KEY", exit 2) leaves
        # run.json at the same stop (resume_run sets "running" before the agent runs), so
        # the run stays resumable; the failure is shown in `error`
        run["status"] = WAITING_STATUS
        if rc in (None, EXIT_AWAITING):
            run["error"] = None
        else:
            prev = run.get("resume_from")
            same = prev and prev == (agent.get("pending") or {}).get("interrupt_id")
            run["error"] = ((f"the resume process exited with code {rc} before applying the decision; "
                             "the run still waits at the same review stop and can be resumed again") if same
                            else (f"the process exited with code {rc}; the agent's record still waits at a "
                                  "review stop, so the run can be resumed")) + (f"\n{tail}" if tail else "")
    elif st in AGENT_FINAL and st != "error" and rc in (None, EXIT_SUBMITTED, EXIT_NO_REPORT):
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
    if run["status"] == "completed":
        run["report_template"] = rendered_template(_read_json(Path(run["run_dir"]) / "result.json"))
    if run["segments"]:
        seg = run["segments"][-1]
        seg.setdefault("finished", run["finished"])
        if rc is not None:
            seg["rc"] = rc
        seg["outcome"] = run["status"]
    _persist_locked(run)


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


def _worker(run_id: str, cmd: list[str], live: bool, action: str) -> None:
    if not _acquire_slot(run_id, live):
        with _lock:
            run = _runs.get(run_id)
            if run is not None:
                run["status"] = "cancelled"
                run["error"] = "cancelled while queued"
                run["finished"] = _now()
                _persist_locked(run)
        return
    try:
        with _lock:
            run = _runs.get(run_id)
            if run is None:
                return
            if run.get("cancel_requested"):
                run["status"] = "cancelled"
                run["error"] = "cancelled while queued"
                run["finished"] = _now()
                _persist_locked(run)
                return
            run["status"] = "running"
            run["started"] = run.get("started") or _now()
            run["finished"] = None
            run["segments"].append({"action": action, "started": _now(), "argv": cmd[3:]})
            _append_log_locked(run, f"--- {action}: {' '.join(cmd[3:])} ---")
            _persist_locked(run)
        rc, outcome, tail = _spawn_and_wait(run_id, cmd, _child_env(live))
        with _lock:
            run = _runs.get(run_id)
            if run is None:
                return
            if rc is None:
                agent = _agent_run(Path(run["run_dir"])) or {}
                waiting = agent.get("status") == WAITING_STATUS and bool(agent.get("pending"))
                run["status"] = WAITING_STATUS if waiting else "error"  # a failed resume launch keeps the stop
                run["error"] = outcome
                run["finished"] = _now()
                _append_log_locked(run, f"ERROR: {outcome}")
                _persist_locked(run)
            elif outcome == "cancelled":
                run["status"] = "cancelled"
                run["error"] = "cancelled by user"
                run["finished"] = _now()
                run["pid"] = None
                run["pid_identity"] = None
                if run["segments"]:
                    run["segments"][-1].update(finished=run["finished"], rc=rc, outcome="cancelled")
                _persist_locked(run)
            else:
                _settle_from_disk_locked(run, rc=rc, tail="\n".join(tail[-10:]))
    finally:
        _release_slot(live)


def _start_worker(run_id: str, cmd: list[str], live: bool, action: str) -> None:
    threading.Thread(target=_worker, args=(run_id, cmd, live, action), daemon=True).start()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def build_start_command(run_dir: Path, task_file: Path, *, settings_file: Path, template: str | None,
                        mock_script: Path | None, live: bool, auto_approve: bool) -> list[str]:
    """argv of ``python -m tfl_deep_agent --file ... --save RUN_DIR --no-dialog ...``."""
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
    return cmd


def build_resume_command(run_dir: Path, decision: str, payload_file: Path | None, *, live: bool) -> list[str]:
    """argv of ``python -m tfl_deep_agent --resume RUN_DIR --decision D [--payload @file] --no-dialog``."""
    cmd = [sys.executable, "-m", "tfl_deep_agent", "--resume", str(run_dir), "--decision", decision,
           "--no-dialog"]
    if payload_file is not None:
        cmd += ["--payload", "@" + str(payload_file)]
    if live:
        cmd.append("--live")
    return cmd


# ---------------------------------------------------------------------------
# POST /api/deep/runs
# ---------------------------------------------------------------------------

def _check_day_budget(settings: Any) -> None:
    from tfl_deep_agent.middleware import budget as budget_mod

    log = budget_mod.DailySpendLog(spend_dir() / budget_mod.SPEND_LOG_NAME)
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
                         "review points")

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
                              mock_script=mock_path, live=live, auto_approve=auto_approve)
    with _lock:
        run = _new_entry(run_id, run_dir, live=live, template=template, input_info=input_info,
                         auto_approve=auto_approve)
        _runs[run_id] = run
        _persist_locked(run)
    _start_worker(run_id, cmd, live, "start")
    return {"run_id": run_id, "status": "queued", "mode": "live" if live else "mock",
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


def _prevalidate(pending: dict[str, Any], decision: str, file_payload: Any) -> None:
    """Check the decision against the pending request in-process (the same
    checks the resumed process makes), so a misfit is a 400 now and the run
    keeps waiting. Skipped when tfl_deep_agent.hitl cannot be imported."""
    try:
        from tfl_deep_agent import hitl
    except Exception:  # noqa: BLE001 - the child process checks it as well
        return
    try:
        dec = hitl.parse_decision(decision, file_payload)
        hitl.resume_value(pending.get("request") or {}, dec)
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
    file_payload = _decision_file_payload(decision, payload.get("payload"), payload.get("comment"))
    if decision == "reject" and not (isinstance(file_payload, dict) and str(file_payload.get("comment") or "").strip()):
        raise BadRequest("reject needs a comment for the agent (comment, or payload {\"comment\": ...})")
    _prevalidate(pending, decision, file_payload)

    payload_file = None
    if file_payload is not None:
        ddir = run_dir / DECISIONS_DIR
        ddir.mkdir(parents=True, exist_ok=True)
        n = len(list(ddir.glob("*.json"))) + 1
        payload_file = ddir / f"{n:03d}-{pending.get('point') or 'stop'}-{decision}.json"
        _atomic_write_json(payload_file, file_payload)
    cmd = build_resume_command(run_dir, decision, payload_file, live=live)
    with _lock:
        run = _get_run(run_id)
        if run["status"] != WAITING_STATUS:  # raced with another resume / cancel
            raise Conflict(f"the run is {run['status']!r}, not {WAITING_STATUS!r}: nothing to resume")
        run["status"] = "queued"
        run["cancel_requested"] = False
        run["error"] = None
        run["resume_from"] = pending.get("interrupt_id")
        run["server_pid"] = os.getpid()
        run["server_identity"] = _lab().process_identity(os.getpid())
        _persist_locked(run)
    _start_worker(run_id, cmd, live, f"resume {pending.get('point')}: {decision}")
    return {"run_id": run_id, "status": "queued", "point": pending.get("point"), "decision": decision}


def api_cancel(run_id: str) -> dict[str, Any]:
    """Cancel a queued / running run (kills its process) or a run waiting for
    a human (it then cannot be resumed). Idempotent on terminal runs."""
    with _lock:
        run = _get_run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": run["status"]}
        if run["status"] == WAITING_STATUS:
            run["status"] = "cancelled"
            run["error"] = "cancelled by user while waiting for a human decision"
            run["finished"] = _now()
            _persist_locked(run)
            return {"run_id": run_id, "status": "cancelled"}
        run["cancel_requested"] = True
        proc = run.get("proc")
        orphan_pid = run.get("pid") if run.get("orphan") else None
        orphan_identity = run.get("pid_identity")
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
                                                     "interrupt_id", "ts", "segment")})
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


def _hitl_view(run: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any]:
    """The pending HITL card (from the agent's run.json; the authority), the
    points and the review history."""
    run_dir = Path(run["run_dir"])
    agent = agent or {}
    out: dict[str, Any] = {"points": agent.get("hitl"), "auto_approve": agent.get("auto_approve"),
                           "reviews": agent.get("reviews") or [], "pending": None}
    pending = agent.get("pending")
    if run["status"] == WAITING_STATUS and isinstance(pending, dict):
        request = pending.get("request") or {}
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
                "card": card, "card_url": _file_url(run["run_id"], "hitl.md")}
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


def _result_view(run: dict[str, Any]) -> dict[str, Any] | None:
    run_dir = Path(run["run_dir"])
    data = _read_json(run_dir / "result.json")
    if not isinstance(data, dict):
        return None
    report = data.get("report") if isinstance(data.get("report"), dict) else {}
    out = {"status": data.get("status"), "draft": data.get("status") == WAITING_STATUS,
           "verdict": report.get("verdict"), "language_class": report.get("language_class"),
           "trust": data.get("trust"), "confidence_cap": data.get("confidence_cap"),
           "human_review": data.get("human_review"), "template": rendered_template(data),
           "run_template": data.get("template"), "reason": data.get("reason"),
           "urls": {s: _file_url(run["run_id"], f"result.{s}") for s in ("json", "md", "html")
                    if (run_dir / f"result.{s}").is_file()}}
    out["can_render"] = _can_render(run, data)
    out["views"] = {t: {s: _file_url(run["run_id"], f"{VIEWS_DIR}/{t}.{s}") for s in ("md", "html")}
                    for t in _rendered_views(run_dir)}
    return out


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


def _summary_locked(run: dict[str, Any], agent: dict[str, Any] | None = None) -> dict[str, Any]:
    if agent is None:
        agent = _agent_run_if_idle(run) or {}
    budget = agent.get("budget") or {}
    pending = agent.get("pending") if isinstance(agent.get("pending"), dict) else None
    result = agent.get("result") if isinstance(agent.get("result"), dict) else {}
    started = run.get("started") or run.get("created")
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
        "pending_point": pending.get("point") if pending and run["status"] == WAITING_STATUS else None,
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


def _day_view(settings: Any = None) -> dict[str, Any] | None:
    try:
        from tfl_deep_agent.middleware import budget as budget_mod

        settings = settings or load_deep_settings()
        log = budget_mod.DailySpendLog(spend_dir() / budget_mod.SPEND_LOG_NAME)
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
    return {
        "run": _summary_locked(run, agent or {}),
        "events": progress["events"], "next": progress["next"], "total": progress["total"],
        "usage": view["usage"], "todos": view["todos"], "tools": view["tools"], "subagents": view["subagents"],
        "orchestration": view["orchestration"], "budget": budget, "budget_signals": view["budget_signals"],
        "hitl": {**_hitl_view(run, agent), "decisions": view["hitl_decisions"]},
        "result": None if run["status"] in ACTIVE_STATUSES else _result_view(run), "done": view["done"],
        "log_tail": run["lines"],
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
        if not agent or agent.get("mode") != "live" or agent.get("status") not in AGENT_FINAL:
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
        "cap_usd": b.per_task_usd, "reserve_for_report_usd": b.reserve_for_report_usd,
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
        with _lock:
            _runs[run_id] = run
            if run["status"] in ACTIVE_STATUSES:
                if lab._pid_matches(run.get("pid"), run.get("pid_identity")):
                    run["orphan"] = True
                    live_ids.append(run_id)
                else:
                    _settle_from_disk_locked(run, reason="server restarted while the run was in progress")
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
            with _lock:
                run = _runs.get(run_id)
                if run is not None and run["status"] in ACTIVE_STATUSES:
                    if cancel:
                        run.update(status="cancelled", error="cancelled by user", finished=_now(), pid=None,
                                   pid_identity=None, orphan=False)
                        _persist_locked(run)
                    else:
                        _settle_from_disk_locked(run, reason="process ended while the server was restarted")
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


__all__ = ["DEEP_RUNS_DIR", "FileResponse", "PREFIX", "api_cancel", "api_create", "api_detail", "api_estimate",
           "api_get_settings", "api_put_settings", "api_render", "api_resume", "build_resume_command", "build_start_command",
           "dispatch", "fold_events", "handles", "max_body_bytes", "restore_runs"]
