"""TFL Lab — local UI server for running agent pipelines.

Minimal HTTP server (stdlib only) that serves the single-page UI and
exposes a small API:

  GET  /                          → index.html
  GET  /static/<file>             → static asset
  GET  /api/projects              → {"projects": [{id, label, formalize}, ...]}
  GET  /api/examples/<project>    → {"examples": ["task_x.json", ...]}
  GET  /api/example/<project>/<f> → {"ir": {...}}
  POST /api/run                   → {"run_id", "log_url", ...}
                                     (runs the pipeline in a thread,
                                      streams stderr to a log buffer)
  GET  /api/log/<run_id>          → {"status", "lines", "elapsed", "cost", ...}
  GET  /api/runs                  → {"runs": [summary, ...]}  (newest first)
  GET  /api/runs/<run_id>[?after=N&limit=M]
                                  → {"run": {...}, "events": [...], "next": N',
                                     "total", "usage", "partial_result": {...}|null}
                                     events are the progress.jsonl lines from line
                                     index N (0-based) on; feed "next" back as
                                     `after` to poll.
  POST /api/runs/<run_id>/cancel  → {"run_id", "status"}
                                     (kills the run's subprocess, if any)
  POST /api/runs/<run_id>/formalize
                                  → {"run_id", "status": "formalizing"}
                                     body must carry {"confirm_spend": true}: runs
                                     `python -m <system>.formalize <run_dir> --live`
                                     over a finished run (status back to
                                     "completed" afterwards). A block that is already
                                     proved is only re-run with {"force": true}, and even
                                     then a non-proved outcome never replaces the proof.
  GET  /api/settings, PUT /api/settings
                                  → formalize_in_run / first_model /
                                     retry_model / retries / max_tokens
                                     (stored in ui_server/settings.json)
  GET  /api/estimate?project=<id>[&formalize=1]
                                  → {"run": {n, estimated_cost_usd, low_usd, high_usd, note}, ...}
                                     measured from the recorded live runs of the project
                                     (no static price table: "no data" until a run exists)
  GET  /api/runs/<run_id>/formalize/estimate
                                  → min / expected / max cost of formalizing that result with
                                     the current settings (`<system>.formalize --estimate`:
                                     no API, no Docker)
  GET  /runs/<run_id>/<file>      → rendered result artifact / progress files

Usage:
    .venv/Scripts/python -m ui_server.server [--port 8000]
                                              [--max-concurrent-runs 2]
                                              [--force]

Security note: binds to 127.0.0.1 only. No auth. Do not expose externally.
Requests are only served for a loopback Host header (blocks DNS rebinding),
POST requests additionally require a same-origin JSON request (blocks
cross-site "no-cors" form posts from other pages starting paid live runs),
and every file route is confined to its directory.

Run lifecycle: a run is "queued" until it gets one of at most
MAX_CONCURRENT_RUNS concurrency slots, then "running" until its subprocess
exits or is cancelled (POST .../cancel). There is deliberately NO run
timeout: manual cancellation is the only way to stop a run early.
"formalizing" is a completed run currently being formalized. Terminal
statuses: completed / error / cancelled / interrupted.

Persistence: every run keeps its state in <run_dir>/run.json (status,
project, started, finished, pid (+ pid_identity: creation time and image name, checked before a pid is trusted or killed), server_pid, verdict, urls, error, cost) and
its log in <run_dir>/run.log; on start `restore_runs()` reloads them. A run
that was queued/running/formalizing but has no live process any more becomes
"interrupted" (or "completed" if its result was already written). If a
recorded process (or the server that owned the run) is still alive, the
server refuses to start unless --force is given.

Pipeline progress contract (written by the pipelines, only read here):
<run_dir>/progress.jsonl (one JSON event per line, cumulative "usage") and
<run_dir>/partial_result.json (snapshot after each node).

Formalization: the pipeline subprocess never runs the Lean step itself
(TFL_FORMALIZATION=0 is forced). With the "formalize_in_run" setting on, a
completed *live* run is followed, in the same run, by `<system>.formalize`
(status "formalizing", after <stem>_result.* is written), exactly like the
Formalize button; models / retries / max_tokens of the settings go to that CLI
as flags: --first-model --retry-model --retries --max-tokens.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("tfl_lab")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = ROOT / ".tfl_lab_runs"
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

# Project registry. Each project has an id (filesystem dir), a short label,
# the name of its orchestrator module (-m target) and, where the pipeline has
# a Lean formalization step, its standalone formalize module.
PROJECTS: list[dict] = [
    {"id": "agent_system", "label": "REG",  "module": "agent_system.orchestrator",
     "formalize_module": "agent_system.formalize"},
    {"id": "cfl_system",   "label": "CFL",  "module": "cfl_system.orchestrator",
     "formalize_module": "cfl_system.formalize"},
    {"id": "dcfl_system",  "label": "DCFL", "module": "dcfl_system.orchestrator",
     "formalize_module": "dcfl_system.formalize"},
    {"id": "ll_system",    "label": "LL",   "module": "ll_system.orchestrator",
     "formalize_module": None},  # ll_system has no Lean formalization
]
PROJECT_IDS = {p["id"] for p in PROJECTS}
_PROJECT_ALIASES = {p["label"].lower(): p["id"] for p in PROJECTS}
# Examples directory per project, built from the registry — request data is
# only ever used as a lookup key, never joined into a filesystem path.
EXAMPLE_DIRS: dict[str, Path] = {p["id"]: ROOT / p["id"] / "examples" for p in PROJECTS}

RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_BODY_BYTES = 1_000_000
RESULT_STEM = "input"  # the IR is always written as input.json → input_result.*

# Concurrency — module globals so `main()` (CLI flags) and tests
# (monkeypatch) can both override them; read fresh on every acquire/wait
# rather than baked into a fixed-size object at import time.
DEFAULT_MAX_CONCURRENT_RUNS = 2
MAX_CONCURRENT_RUNS = int(os.environ.get("TFL_LAB_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS))

_SLOT_POLL_SECONDS = 0.2   # how often a queued run rechecks for a free slot
_WAIT_POLL_SECONDS = 0.5   # how often a running run rechecks for cancellation
_ORPHAN_POLL_SECONDS = 2.0  # how often an adopted foreign run is rechecked

ACTIVE_STATUSES = {"queued", "running", "formalizing"}
TERMINAL_STATUSES = {"completed", "error", "cancelled", "interrupted"}

# ---------------------------------------------------------------------------
# Cost estimate (before a run) and settings
# ---------------------------------------------------------------------------

# Pipeline-run cost estimates are MEASURED, not tabulated: the statistics of the
# recorded live runs of the project (see `_recorded_run_cost_stats`). Nothing is
# assumed; without a recorded run the estimate is "no data".
ESTIMATE_NOTE = "measured from recorded live runs of this project"
ESTIMATE_NO_DATA_NOTE = "no data: no recorded live run of this project yet"

DEFAULT_SETTINGS: dict = {
    "formalize_in_run": False,
    "first_model": "claude-opus-5-5",
    "retry_model": "claude-sonnet-5-5",
    "retries": 2,
    "max_tokens": 128000,
}
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}")
MAX_TOKENS_LIMIT = 128000
MIN_TOKENS_LIMIT = 1024
MAX_RETRIES_LIMIT = 10

_settings_lock = threading.Lock()


class ConflictError(ValueError):
    """The request is valid but not applicable to the run's current state (409)."""


def _validate_settings(data: dict, base: dict) -> dict:
    """Merge `data` (partial) over `base` with strict validation; ValueError on
    unknown keys or bad values. Returns a new complete settings dict."""
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")
    unknown = sorted(set(data) - set(DEFAULT_SETTINGS))
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(unknown)}")
    out = dict(base)
    for key, val in data.items():
        if key == "formalize_in_run":
            if not isinstance(val, bool):
                raise ValueError("formalize_in_run must be a boolean")
        elif key in ("first_model", "retry_model"):
            if not isinstance(val, str) or not _MODEL_RE.fullmatch(val.strip()):
                raise ValueError(f"{key} must be a model id string")
            val = val.strip()
        elif key == "retries":
            if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= MAX_RETRIES_LIMIT:
                raise ValueError(f"retries must be an integer 0..{MAX_RETRIES_LIMIT}")
        elif key == "max_tokens":
            if (isinstance(val, bool) or not isinstance(val, int)
                    or not MIN_TOKENS_LIMIT <= val <= MAX_TOKENS_LIMIT):
                raise ValueError(f"max_tokens must be an integer {MIN_TOKENS_LIMIT}..{MAX_TOKENS_LIMIT}")
        out[key] = val
    return out


def load_settings() -> dict:
    """Current settings: defaults overlaid with settings.json (a missing or
    corrupt file, or invalid stored values, fall back to the defaults)."""
    with _settings_lock:
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        for key, val in stored.items():
            try:
                settings = _validate_settings({key: val}, settings)
            except ValueError:
                continue
    return settings


def api_get_settings() -> dict:
    return {"settings": load_settings(), "defaults": dict(DEFAULT_SETTINGS)}


def api_put_settings(payload: dict) -> dict:
    settings = _validate_settings(payload, load_settings())
    with _settings_lock:
        _atomic_write_json(SETTINGS_PATH, settings)
    return {"settings": settings, "defaults": dict(DEFAULT_SETTINGS)}


def _resolve_project(name: object) -> str:
    if isinstance(name, str):
        key = name.strip()
        if key in PROJECT_IDS:
            return key
        if key.lower() in _PROJECT_ALIASES:
            return _PROJECT_ALIASES[key.lower()]
    raise ValueError(f"unknown project: {name}")


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _pipeline_cost_of_run(run_dir: Path) -> float | None:
    """Cost of the pipeline part of a recorded run: the cumulative usage at its
    first "done" event (written when the pipeline finished, i.e. before any
    formalization appended to the same progress file)."""
    for ev in _read_progress(run_dir, 0, 10**9)["events"]:
        if ev.get("event") == "done":
            usage = ev.get("usage")
            if isinstance(usage, dict) and (usage.get("calls") or 0) > 0:
                cost = usage.get("estimated_cost_usd")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                    return float(cost)
            return None
    return None


def _recorded_run_cost_stats(project: str) -> dict:
    """{"n", "avg_usd", "min_usd", "max_usd"} over the recorded live runs of
    `project` that finished with a result and a known cost."""
    with _runs_lock:
        candidates = [Path(r["run_dir"]) for r in _runs.values()
                      if r.get("project") == project and r.get("live") and r.get("run_dir")
                      and r["status"] in ("completed", "formalizing")]
    costs = [c for c in (_pipeline_cost_of_run(d) for d in candidates) if c is not None]
    if not costs:
        return {"n": 0, "avg_usd": None, "min_usd": None, "max_usd": None}
    return {"n": len(costs), "avg_usd": round(sum(costs) / len(costs), 4),
            "min_usd": round(min(costs), 4), "max_usd": round(max(costs), 4)}


def api_estimate(project: object = None, formalize: object = None) -> dict:
    """Cost estimate before a run, from the recorded live runs of the project
    (mean with min..max and the number of runs it rests on; all None and a
    "no data" note when there is none). `formalize` = the in-run Lean step is
    on (default: the settings' formalize_in_run); it is priced only after the
    run, from the finished result (`api_formalize_estimate`)."""
    out: dict = {"note": ESTIMATE_NOTE}
    if project is None:
        return out
    pid = _resolve_project(project)
    proj = next(p for p in PROJECTS if p["id"] == pid)
    with_formalize = load_settings()["formalize_in_run"] if formalize is None else _truthy(formalize)
    with_formalize = bool(with_formalize and proj["formalize_module"])
    stats = _recorded_run_cost_stats(pid)
    out.update({
        "project": pid,
        "with_formalize": with_formalize,
        "run": {
            "n": stats["n"],
            "estimated_cost_usd": stats["avg_usd"],
            "low_usd": stats["min_usd"],
            "high_usd": stats["max_usd"],
            "note": (f"{ESTIMATE_NOTE}: mean of {stats['n']} run(s), min..max shown"
                     if stats["n"] else ESTIMATE_NO_DATA_NOTE),
        },
    })
    if with_formalize:
        out["formalize_note"] = ("formalization after the run is not included; it is priced "
                                 "(min / expected / max) from the finished result")
    return out


def api_formalize_estimate(run_id: str) -> dict:
    """Min / expected / max cost of formalizing a finished run with the current
    settings: `<system>.formalize <run_dir> --estimate ...` (no API, no Docker,
    no writes). Computed from the result and the prices in the config."""
    settings = load_settings()
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        project = run.get("project")
        if project not in PROJECT_IDS:
            raise ConflictError("run has no known project, cannot formalize")
        if not next(p for p in PROJECTS if p["id"] == project)["formalize_module"]:
            raise ValueError(f"formalization is not available for {project}")
        if not run.get("result_json_url"):
            raise ConflictError("the run has no result to formalize yet")
        run_dir = Path(run["run_dir"])
    cmd = build_formalize_estimate_command(project, run_dir, settings)
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not compute the estimate: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise ValueError("could not compute the estimate: " + " | ".join(tail))
    try:
        est = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"unreadable estimate output: {exc}") from exc
    if not isinstance(est, dict):
        raise RuntimeError("unreadable estimate output")
    return {
        "run_id": run_id,
        "formalizable": bool(est.get("formalizable")),
        "reason": est.get("reason"),
        "min_usd": est.get("min_usd"),
        "expected_usd": est.get("expected_usd"),
        "max_usd": est.get("max_usd"),
        "calls_max": est.get("calls_max"),
        "assumptions": est.get("assumptions"),
        # aliases the frontend's generic estimate picker understands
        "estimated_cost_usd": est.get("expected_usd"),
        "low_usd": est.get("min_usd"),
        "high_usd": est.get("max_usd"),
        "settings": {k: settings[k] for k in ("first_model", "retry_model", "retries", "max_tokens")},
    }


def _settings_env(settings: dict, project: str) -> dict[str, str]:
    """Environment overrides for a subprocess. The pipeline never runs the Lean
    step inside its own graph here: the formalization is a separate entry
    (`<system>.formalize`, chained after the result when the setting is on), so
    an inherited TFL_FORMALIZATION=1 must not switch the in-graph step on."""
    # The child's stderr is decoded as UTF-8 by _spawn_and_wait; without these
    # Windows children write the ANSI code page and a Cyrillic path in the log
    # turns into mojibake.
    return {"TFL_FORMALIZATION": "0", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, obj: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _files_in(directory: Path, pattern: str = "*", recursive: bool = False) -> dict[str, Path]:
    """Index of the regular files under `directory`, keyed by their POSIX path
    relative to it. File routes look the requested name up in this index, so
    a request can only ever reach a file that actually lives in `directory`
    (no `..`, no absolute paths, no symlinks leading outside)."""
    if not directory.is_dir():
        return {}
    base = directory.resolve()
    walk = directory.rglob(pattern) if recursive else directory.glob(pattern)
    index: dict[str, Path] = {}
    for f in walk:
        if f.is_file() and f.resolve().is_relative_to(base):
            index[f.relative_to(directory).as_posix()] = f
    return index


def _run_dirs() -> dict[str, Path]:
    """Existing run directories (named by 12-hex run ids), keyed by run id."""
    if not RUNS_DIR.is_dir():
        return {}
    return {d.name: d for d in RUNS_DIR.iterdir() if d.is_dir() and RUN_ID_RE.fullmatch(d.name)}


# ---------------------------------------------------------------------------
# Progress files (written by the pipelines, only read here)
# ---------------------------------------------------------------------------

def _read_progress(run_dir: Path, after: int = 0, limit: int = 1000) -> dict:
    """Events of run_dir/progress.jsonl from line index `after` (0-based) on.

    Only complete (newline-terminated) lines count, so a line being written
    right now is picked up by the next poll. `next` is the cursor to pass as
    `after` next time; `total` the number of complete lines seen so far.
    """
    path = run_dir / "progress.jsonl"
    try:
        raw = path.read_bytes()
    except OSError:
        return {"events": [], "next": after, "total": 0}
    end = raw.rfind(b"\n")
    lines = raw[: end + 1].split(b"\n")[:-1] if end >= 0 else []
    events: list[dict] = []
    idx = max(0, after)
    while idx < len(lines) and len(events) < limit:
        text = lines[idx].strip()
        idx += 1
        if not text:
            continue
        try:
            ev = json.loads(text.decode("utf-8", errors="replace"))
        except ValueError:
            continue  # corrupt line — skipped, but the cursor moves past it
        if isinstance(ev, dict):
            events.append(ev)
    return {"events": events, "next": idx, "total": len(lines)}


def _read_usage(run_dir: Path) -> dict | None:
    """Latest cumulative "usage" from the tail of progress.jsonl (cheap: reads
    only the last chunk of the file)."""
    path = run_dir / "progress.jsonl"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            chunk = fh.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    if size > 262144 and lines:
        lines = lines[1:]  # first line of the chunk may be cut
    for text in reversed(lines):
        text = text.strip()
        if not text:
            continue
        try:
            ev = json.loads(text.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("usage"), dict):
            return ev["usage"]
    return None


def _read_partial(run_dir: Path) -> dict | None:
    try:
        data = json.loads((run_dir / "partial_result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# In-memory run registry, mirrored to <run_dir>/run.json. Threading lock
# protects concurrent access.
# {run_id: {"run_id", "status", "project", "lines" (list[str]), "started",
#           "finished", "elapsed", "run_dir", "live", "pid", "server_pid",
#           "result_html_url", "result_json_url", "result_md_url", "verdict",
#           "error", "formalize_error", "cost", "orphan",
#           "proc" (Popen | None), "cancel_requested"}}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()

# Concurrency gate: at most MAX_CONCURRENT_RUNS pipeline subprocesses run at
# once. Extra runs block in `_acquire_run_slot` (status "queued") instead of
# being rejected, so they still start automatically once a slot frees up.
_concurrency_lock = threading.Condition()
_active_runs = 0


def _acquire_run_slot(run_id: str) -> bool:
    """Block until a concurrency slot is free, or the run is cancelled while
    queued. Returns False if the run was cancelled before it got a slot."""
    global _active_runs
    with _concurrency_lock:
        while _active_runs >= MAX_CONCURRENT_RUNS:
            with _runs_lock:
                run = _runs.get(run_id)
                if run is not None and run.get("cancel_requested"):
                    return False
            _concurrency_lock.wait(timeout=_SLOT_POLL_SECONDS)
        _active_runs += 1
        return True


def _release_run_slot() -> None:
    global _active_runs
    with _concurrency_lock:
        _active_runs = max(0, _active_runs - 1)
        _concurrency_lock.notify_all()


def _terminate_process(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except Exception:
        pass


def _pid_alive(pid: int | None) -> bool:
    """Is a process with this pid running? (Never uses os.kill on Windows:
    there any signal terminates the process.)"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied → the process exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
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


def process_identity(pid: int | None) -> dict | None:
    """{"created": <process creation time, epoch seconds>, "image": <lower-case
    executable name>} for a live pid, else None. Saved next to a pid in run.json
    so a recycled pid (another process that got the same number) is not taken
    for the run's process."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                ft = [wintypes.FILETIME() for _ in range(4)]
                if not kernel32.GetProcessTimes(handle, *[ctypes.byref(f) for f in ft]):
                    return None
                raw = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
                created = raw / 1e7 - 11644473600.0
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(buf))
                image = ""
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    image = os.path.basename(buf.value).lower()
                return {"created": created, "image": image}
            finally:
                kernel32.CloseHandle(handle)
        stat = Path(f"/proc/{pid}/stat")
        if stat.is_file():
            tail = stat.read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
            ticks = int(tail[19])  # field 22: starttime (clock ticks since boot)
            btime = 0.0
            for line in Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("btime "):
                    btime = float(line.split()[1])
                    break
            created = btime + ticks / float(os.sysconf("SC_CLK_TCK"))
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
            return {"created": created, "image": comm.lower()}
        out = subprocess.run(["ps", "-o", "lstart=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            created = time.mktime(time.strptime(" ".join(out.split()[:5]), "%a %b %d %H:%M:%S %Y"))
            return {"created": created, "image": os.path.basename(out.split(None, 5)[-1]).lower()}
    except Exception:
        pass
    return None


_IDENTITY_TOLERANCE_S = 2.0


def _pid_matches(pid: int | None, identity: dict | None) -> bool:
    """Is `pid` alive AND still the process that was recorded? Without a saved
    identity (records from before it was stored) only liveness can be checked.
    A live pid whose identity cannot be read is trusted (nothing to compare)."""
    if not _pid_alive(pid):
        return False
    if not isinstance(identity, dict) or not identity:
        return True
    cur = process_identity(pid)
    if cur is None:
        return True
    try:
        if abs(float(cur["created"]) - float(identity["created"])) > _IDENTITY_TOLERANCE_S:
            return False
    except (KeyError, TypeError, ValueError):
        return True
    img_a, img_b = str(cur.get("image") or ""), str(identity.get("image") or "")
    return not (img_a and img_b and img_a != img_b)


def _kill_pid(pid: int, identity: dict | None = None) -> bool:
    """Kill a process (tree) we do not hold a Popen handle for. When an
    `identity` was recorded it must still match: a recycled pid is never
    killed. Returns whether a kill was attempted."""
    if identity is not None and not _pid_matches(pid, identity):
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception:
        return False
    return True


def _wait_for_process(run_id: str, proc: subprocess.Popen):
    """Poll proc.wait() in short slices so a run can be killed on on-demand
    cancellation instead of blocking forever (there is no timeout: a run ends
    when its process exits or the user cancels). Returns (returncode, outcome)
    where outcome is None or "cancelled"."""
    while True:
        try:
            rc = proc.wait(timeout=_WAIT_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            with _runs_lock:
                run = _runs.get(run_id)
                cancel_requested = bool(run and run.get("cancel_requested"))
            if cancel_requested:
                _terminate_process(proc)
                return proc.wait(), "cancelled"
            continue
        # The process exited within this poll slice — but that may be
        # because api_cancel (on another thread) just killed it directly for
        # promptness, racing this loop. Re-check so that case still reports
        # "cancelled" instead of being read as a normal exit.
        with _runs_lock:
            run = _runs.get(run_id)
            cancel_requested = bool(run and run.get("cancel_requested"))
        return rc, ("cancelled" if cancel_requested else None)


# ---------------------------------------------------------------------------
# Persistence: <run_dir>/run.json + run.log
# ---------------------------------------------------------------------------

_RECORD_KEYS = ("run_id", "status", "project", "started", "finished", "pid", "server_pid",
                "pid_identity", "server_identity", "live", "verdict", "confidence", "error", "formalize_error", "cost",
                "formalize_in_run", "result_status", "result_errors")


def _source_mode(run: dict) -> str:
    """"live" when the run's result came from real API calls, else "mock"."""
    return "live" if run.get("live") else "mock"


MOCK_FORMALIZE_WARNING = ("the result was obtained in mock mode; formalization will make "
                          "paid API calls")


def _run_record(run: dict) -> dict:
    """The persisted (JSON-safe) form of a registry entry."""
    rec = {k: run.get(k) for k in _RECORD_KEYS}
    rec["source_mode"] = _source_mode(run)
    rec["urls"] = {
        "html": run.get("result_html_url"),
        "json": run.get("result_json_url"),
        "md": run.get("result_md_url"),
    }
    return rec


def _persist_locked(run: dict) -> None:
    """Write run.json (call with _runs_lock held). Never raises: a failed
    write must not kill the run worker."""
    run_dir = run.get("run_dir")
    if not run_dir:
        return
    try:
        _atomic_write_json(Path(run_dir) / "run.json", _run_record(run))
    except OSError:
        logger.exception("could not write run.json for %s", run.get("run_id"))


def _persist(run_id: str) -> None:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is not None:
            _persist_locked(run)


def _refresh_cost_locked(run: dict) -> None:
    usage = _read_usage(Path(run["run_dir"])) if run.get("run_dir") else None
    if usage is not None:
        run["cost"] = usage


def _apply_results_locked(run: dict) -> None:
    """Fill urls / verdict from the result files in the run directory."""
    run_dir = Path(run["run_dir"])
    run_id = run["run_id"]
    result_json = run_dir / f"{RESULT_STEM}_result.json"
    for key, suffix in (("result_html_url", "html"), ("result_json_url", "json"), ("result_md_url", "md")):
        f = run_dir / f"{RESULT_STEM}_result.{suffix}"
        run[key] = f"/runs/{run_id}/{f.name}" if f.exists() else run.get(key)
    if result_json.exists():
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
            run["verdict"] = data.get("verdict")
            run["confidence"] = data.get("confidence")
            # The pipeline's own outcome, separate from the process status
            # ("completed" only says the subprocess produced a result file).
            run["result_status"] = data.get("status") if isinstance(data.get("status"), str) else None
            errs = data.get("errors")
            run["result_errors"] = [str(e) for e in errs] if isinstance(errs, list) else []
        except Exception:
            pass
    _refresh_cost_locked(run)


def _new_run_entry(run_id: str, project: str, run_dir: Path, live: bool) -> dict:
    return {
        "run_id": run_id,
        "status": "queued",
        "project": project,
        "lines": [],
        "started": time.time(),
        "finished": None,
        "elapsed": None,
        "run_dir": str(run_dir),
        "live": live,
        "pid": None,
        "pid_identity": None,
        "server_pid": os.getpid(),
        "server_identity": process_identity(os.getpid()),
        "result_html_url": None,
        "result_json_url": None,
        "result_md_url": None,
        "verdict": None,
        "confidence": None,
        "result_status": None,
        "result_errors": [],
        "error": None,
        "formalize_error": None,
        "formalize_in_run": False,
        "cost": None,
        "orphan": False,
        "proc": None,
        "cancel_requested": False,
    }


def _settle_dead_locked(run: dict, reason: str) -> None:
    """A run recorded as active whose process is gone: finish it from disk."""
    _apply_results_locked(run)
    has_result = bool(run.get("result_json_url"))
    status = run["status"]
    if status == "formalizing" and has_result:
        run["status"] = "completed"
        run["formalize_error"] = reason
    elif status == "running" and has_result:
        run["status"] = "completed"
    else:
        run["status"] = "interrupted"
        run["error"] = reason
    run["finished"] = run.get("finished") or time.time()
    run["pid"] = None
    run["pid_identity"] = None
    run["orphan"] = False
    if run.get("started"):
        run["elapsed"] = run["finished"] - run["started"]
    _persist_locked(run)


def _is_live_record(rec: dict) -> bool:
    """Does a recorded-active run still have a live process behind it (its own
    subprocess, or the other server that owns/queues it)?"""
    if _pid_matches(rec.get("pid"), rec.get("pid_identity")):
        return True
    owner = rec.get("server_pid")
    return bool(owner and owner != os.getpid()
                and _pid_matches(owner, rec.get("server_identity")))


def restore_runs() -> list[str]:
    """Reload the run registry from <RUNS_DIR>/*/run.json.

    Runs recorded as queued/running/formalizing without a live process become
    "interrupted" (or "completed" when the result was already written).
    Returns the ids of active runs that DO still have a live process — the
    caller decides whether to refuse to start (see main / --force); those are
    loaded with orphan=True.
    Directories without run.json (older runs) are loaded read-only from their
    result files.
    """
    live_ids: list[str] = []
    for run_id, run_dir in sorted(_run_dirs().items()):
        with _runs_lock:
            if run_id in _runs:
                continue
        rec_path = run_dir / "run.json"
        rec = None
        if rec_path.is_file():
            try:
                rec = json.loads(rec_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = None
            if not isinstance(rec, dict):
                rec = None
        legacy = rec is None
        if legacy:
            has_result = (run_dir / f"{RESULT_STEM}_result.json").exists()
            try:
                mtime = run_dir.stat().st_mtime
            except OSError:
                mtime = time.time()
            rec = {"status": "completed" if has_result else "interrupted",
                   "started": mtime, "finished": mtime}
        run = _new_run_entry(run_id, rec.get("project"), run_dir, bool(rec.get("live")))
        for key in ("status", "started", "finished", "pid", "pid_identity", "server_pid",
                    "server_identity", "live", "verdict",
                    "confidence", "error", "formalize_error", "cost", "formalize_in_run",
                    "result_status", "result_errors"):
            if key in rec:
                run[key] = rec[key]
        urls = rec.get("urls") if isinstance(rec.get("urls"), dict) else {}
        run["result_html_url"] = urls.get("html")
        run["result_json_url"] = urls.get("json")
        run["result_md_url"] = urls.get("md")
        if run["status"] not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            run["status"] = "interrupted"
        if run["started"] and run["finished"]:
            run["elapsed"] = run["finished"] - run["started"]
        log_path = run_dir / "run.log"
        try:
            run["lines"] = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        except OSError:
            pass
        if not legacy and run["status"] in ACTIVE_STATUSES:
            if _is_live_record(run):
                run["orphan"] = True
                live_ids.append(run_id)
            else:
                with _runs_lock:
                    _runs[run_id] = run
                    _settle_dead_locked(run, "server restarted while the run was in progress")
                continue
        elif legacy or ("result_status" not in rec and run.get("result_json_url")):
            _apply_results_locked(run)  # legacy dir / record from before result_status existed
        with _runs_lock:
            _runs[run_id] = run
    return live_ids


def adopt_orphans(run_ids: list[str]) -> None:
    """--force: keep watching runs owned by a process outside this server, and
    settle them from disk once that process is gone."""
    for run_id in run_ids:
        threading.Thread(target=_watch_orphan, args=(run_id,), daemon=True).start()


def _watch_orphan(run_id: str) -> None:
    while True:
        with _runs_lock:
            run = _runs.get(run_id)
            if run is None or run["status"] not in ACTIVE_STATUSES or not run.get("orphan"):
                return
            alive = _is_live_record(run)
            cancel = bool(run.get("cancel_requested"))
            pid = run.get("pid")
            pid_identity = run.get("pid_identity")
        if cancel and pid:
            _kill_pid(pid, pid_identity)
        if not alive:
            with _runs_lock:
                run = _runs.get(run_id)
                if run is not None and run["status"] in ACTIVE_STATUSES:
                    if cancel:
                        _apply_results_locked(run)
                        run["status"] = "cancelled"
                        run["error"] = "cancelled by user"
                        run["finished"] = time.time()
                        run["orphan"] = False
                        _persist_locked(run)
                    else:
                        _settle_dead_locked(run, "process ended while the server was restarted")
            return
        time.sleep(_ORPHAN_POLL_SECONDS)


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------

def api_projects() -> dict:
    return {"projects": [
        {"id": p["id"], "label": p["label"], "formalize": bool(p["formalize_module"]),
         "task_types": accepted_task_types(p["id"])}
        for p in PROJECTS
    ]}


def api_examples(project: str) -> dict:
    examples_dir = EXAMPLE_DIRS.get(project)
    if examples_dir is None:
        raise ValueError(f"unknown project: {project}")
    return {"examples": sorted(_files_in(examples_dir, "*.json"))}


def api_example(project: str, fname: str) -> dict:
    examples_dir = EXAMPLE_DIRS.get(project)
    if examples_dir is None:
        raise ValueError(f"unknown project: {project}")
    path = _files_in(examples_dir, "*.json").get(fname)
    if path is None:
        raise FileNotFoundError("example not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"ir": data, "name": path.name}


def _elapsed(run: dict) -> float | None:
    if run.get("elapsed") is not None:
        return run["elapsed"]
    if run["status"] in ACTIVE_STATUSES and run.get("started"):
        return time.time() - run["started"]
    return None


def _summary_locked(run: dict) -> dict:
    """Public run summary (call with _runs_lock held)."""
    cost = run.get("cost")
    if run["status"] in ACTIVE_STATUSES and run.get("run_dir"):
        cost = _read_usage(Path(run["run_dir"])) or cost  # running total
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "project": run.get("project"),
        "live": bool(run.get("live")),
        "source_mode": _source_mode(run),
        "started": run.get("started"),
        "finished": run.get("finished"),
        "elapsed": _elapsed(run),
        "verdict": run.get("verdict"),
        "confidence": run.get("confidence"),
        "result_status": run.get("result_status"),
        "result_errors": run.get("result_errors") or [],
        "error": run.get("error"),
        "formalize_error": run.get("formalize_error"),
        "cost": cost,
        "usage": cost,  # same object; cumulative {calls, input_tokens, output_tokens, estimated_cost_usd}
        "estimated_cost_usd": cost.get("estimated_cost_usd") if isinstance(cost, dict) else None,
        "result_html_url": run.get("result_html_url"),
        "result_json_url": run.get("result_json_url"),
        "result_md_url": run.get("result_md_url"),
        "urls": {"html": run.get("result_html_url"), "json": run.get("result_json_url"),
                 "md": run.get("result_md_url")},
    }


def api_log(run_id: str) -> dict:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        out = _summary_locked(run)
        out["lines"] = list(run["lines"])  # snapshot copy
        return out


def api_runs() -> dict:
    with _runs_lock:
        runs = [_summary_locked(r) for r in _runs.values()]
    runs.sort(key=lambda r: r.get("started") or 0, reverse=True)
    return {"runs": runs}


def _int_param(value: object, name: str, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        n = int(str(value))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    return max(lo, min(hi, n))


def api_run_detail(run_id: str, after: object = None, limit: object = None) -> dict:
    """run.json content + progress.jsonl events from cursor `after` +
    the latest partial_result.json snapshot."""
    after_n = _int_param(after, "after", 0, 0, 10**9)
    limit_n = _int_param(limit, "limit", 1000, 1, 5000)
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        summary = _summary_locked(run)
        run_dir = Path(run["run_dir"])
    progress = _read_progress(run_dir, after_n, limit_n)
    return {
        "run": summary,
        "events": progress["events"],   # progress.jsonl lines from index `after`
        "next": progress["next"],       # cursor: pass back as ?after=
        "total": progress["total"],
        "usage": summary["usage"],
        "partial_result": _read_partial(run_dir),
    }


# task_type values accepted per pipeline, read from each pipeline's own IR
# validator module so they cannot drift.
_TASK_TYPE_SOURCES = {
    "agent_system": ("agent_system.lib.ir_schema", "_VALID_TASK_TYPES"),
    "cfl_system": ("cfl_system.lib.cfl_ir_schema", "_CFL_TASK_TYPES"),
    "dcfl_system": ("dcfl_system.lib.dcfl_ir_schema", "_DCFL_TASK_TYPES"),
    "ll_system": ("ll_system.lib.ll_ir_schema", "_LL_TASK_TYPES"),
}


def accepted_task_types(project: str) -> list[str] | None:
    """Sorted task_type values a project accepts, or None if they cannot be
    determined (then no check is made)."""
    src = _TASK_TYPE_SOURCES.get(project)
    if src is None:
        return None
    module, attr = src
    try:
        return sorted(getattr(importlib.import_module(module), attr))
    except Exception:
        logger.warning("cannot read task types of %s", project, exc_info=True)
        return None


def check_task_type(project: str, ir: dict) -> str | None:
    """Error message when the IR's task_type does not belong to `project`
    (e.g. a CFL IR submitted in the REG tab), else None. An IR without
    task_type is never blocked here: dcfl accepts it, and the other pipelines
    report the missing field themselves."""
    allowed = accepted_task_types(project)
    if allowed is None or "task_type" not in ir:
        return None
    tt = ir.get("task_type")
    if isinstance(tt, str) and tt in allowed:
        return None
    label = next(p["label"] for p in PROJECTS if p["id"] == project)
    owners = [p["label"] for p in PROJECTS if p["id"] != project
              and isinstance(tt, str) and tt in (accepted_task_types(p["id"]) or [])]
    hint = f" (it belongs to the {'/'.join(owners)} pipeline)" if owners else ""
    return (f"task_type {tt!r} is not valid for the {label} pipeline{hint}; "
            f"expected one of: {', '.join(allowed)}")


def api_run(payload: dict) -> dict:
    project = payload.get("project")
    ir = payload.get("ir")
    live = bool(payload.get("live"))
    verbose = bool(payload.get("verbose", True))
    if project not in PROJECT_IDS:
        raise ValueError(f"unknown project: {project}")
    if not isinstance(ir, dict):
        raise ValueError("ir must be a JSON object")
    task_error = check_task_type(project, ir)
    if task_error:
        raise ValueError(task_error)

    settings = load_settings()  # snapshot: a queued run keeps the settings it was started with
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Write the IR to a file the orchestrator can read
    ir_path = run_dir / f"{RESULT_STEM}.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")

    with _runs_lock:
        run = _new_run_entry(run_id, project, run_dir, live)
        run["formalize_in_run"] = bool(settings["formalize_in_run"])
        _runs[run_id] = run
        _persist_locked(run)

    thread = threading.Thread(
        target=_run_pipeline_worker,
        args=(run_id, project, ir_path, run_dir, live, verbose, settings),
        daemon=True,
    )
    thread.start()

    return {
        "run_id": run_id,
        "status": "running",
        "log_url": f"/api/log/{run_id}",
    }


def api_cancel(run_id: str) -> dict:
    """Request cancellation of a queued, running or formalizing run.
    Idempotent: cancelling an already-terminal run is a no-op, not an error,
    since the client may race the run's own completion."""
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        if run["status"] in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": run["status"]}
        run["cancel_requested"] = True
        proc = run.get("proc")
        orphan_pid = run.get("pid") if run.get("orphan") else None
        orphan_identity = run.get("pid_identity")
    # Wake any thread blocked waiting for a concurrency slot (a queued run
    # notices cancel_requested there and never launches a subprocess).
    with _concurrency_lock:
        _concurrency_lock.notify_all()
    # Kill an already-running subprocess right away; the worker's own
    # `_wait_for_process` poll also checks cancel_requested and would reach
    # the same kill within _WAIT_POLL_SECONDS, this just avoids that wait.
    if proc is not None:
        _terminate_process(proc)
    elif orphan_pid:
        _kill_pid(orphan_pid, orphan_identity)  # adopted foreign run: settled by its watcher
    return {"run_id": run_id, "status": "cancelling"}


def build_command(project: str, ir_path: Path, run_dir: Path,
                  live: bool, verbose: bool) -> list[str]:
    """argv for `python -m <project>.orchestrator <ir> --save <run_dir>`.

    Every orchestrator accepts this contract and writes
    <ir stem>_result.{json,md,html} with a top-level "verdict".
    """
    proj_cfg = next(p for p in PROJECTS if p["id"] == project)
    cmd = [
        sys.executable, "-m", proj_cfg["module"],
        str(ir_path),
        "--save", str(run_dir),
    ]
    if live:
        cmd.append("--live")
    if verbose:
        cmd.append("--verbose")
    return cmd


def build_formalize_command(project: str, run_dir: Path, settings: dict | None = None,
                            force: bool = False) -> list[str]:
    """argv for `python -m <project>.formalize <run_dir> --live [settings flags]`.

    The formalize CLI takes its models / retries / output limit as flags
    (--first-model --retry-model --retries --max-tokens); `force` re-runs over
    an already proved block."""
    proj_cfg = next(p for p in PROJECTS if p["id"] == project)
    if not proj_cfg["formalize_module"]:
        raise ValueError(f"formalization is not available for {project}")
    cmd = [sys.executable, "-m", proj_cfg["formalize_module"], str(run_dir), "--live"]
    if settings:
        cmd += ["--first-model", str(settings["first_model"]),
                "--retry-model", str(settings["retry_model"]),
                "--retries", str(settings["retries"]),
                "--max-tokens", str(settings["max_tokens"])]
    if force:
        cmd.append("--force")
    return cmd


def build_formalize_estimate_command(project: str, run_dir: Path, settings: dict) -> list[str]:
    """argv for `python -m <project>.formalize <run_dir> --estimate [settings flags]`
    (prints the cost estimate as JSON; no API, no Docker, no writes)."""
    cmd = build_formalize_command(project, run_dir, settings)
    cmd[cmd.index("--live")] = "--estimate"
    return cmd


def _append_log_locked(run_id: str, line: str) -> None:
    """Append to memory and disk while the caller holds _runs_lock."""
    run = _runs.get(run_id)
    if run is not None:
        run["lines"].append(line)
        try:
            with open(Path(run["run_dir"]) / "run.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _append_log(run_id: str, line: str) -> None:
    with _runs_lock:
        _append_log_locked(run_id, line)


def _spawn_and_wait(run_id: str, cmd: list[str], env_overrides: dict[str, str]):
    """Start `cmd` for the run, stream its stderr into the run log and wait
    (cancellable, no timeout). Returns (rc, outcome, stderr_tail); rc is None
    if the process could not be launched (outcome then holds the message)."""
    env = dict(os.environ)
    env.update(env_overrides)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
        )
    except Exception as exc:
        return None, f"failed to launch: {exc}", []

    with _runs_lock:
        run = _runs.get(run_id)
        cancel_now = bool(run and run.get("cancel_requested"))
        if run is not None:
            run["proc"] = proc
            run["pid"] = proc.pid
            run["pid_identity"] = process_identity(proc.pid)
            _persist_locked(run)
    if cancel_now:
        _terminate_process(proc)

    stderr_tail: list[str] = []

    def pump_stderr() -> None:
        for line in proc.stderr:
            line = line.rstrip("\n")
            _append_log(run_id, line)
            stderr_tail.append(line)
            del stderr_tail[:-20]

    def pump_stdout() -> None:
        for _ in proc.stdout:  # drain so the child never blocks on a full pipe
            pass

    t_err = threading.Thread(target=pump_stderr, daemon=True)
    t_out = threading.Thread(target=pump_stdout, daemon=True)
    t_err.start(); t_out.start()

    rc, outcome = _wait_for_process(run_id, proc)
    t_err.join(timeout=2.0)
    t_out.join(timeout=2.0)
    with _runs_lock:
        run = _runs.get(run_id)
        if run is not None:
            run["proc"] = None
            run["pid"] = None
            run["pid_identity"] = None
    return rc, outcome, stderr_tail


def _run_pipeline_worker(run_id: str, project: str, ir_path: Path,
                         run_dir: Path, live: bool, verbose: bool,
                         settings: dict | None = None) -> None:
    """Run the project's orchestrator (see build_command), stream stderr
    into the run's log buffer.

    Blocks in "queued" status for a concurrency slot (at most
    MAX_CONCURRENT_RUNS subprocesses at once), then runs with cancellation
    support (see `_wait_for_process`) and no timeout; the slot is always
    released, so a cancelled run frees it up for the next queued one.
    """
    settings = settings or load_settings()
    cmd = build_command(project, ir_path, run_dir, live, verbose)
    env = _settings_env(settings, project)
    chain_formalize = False

    def cancelled_while_queued() -> None:
        with _runs_lock:
            run = _runs.get(run_id)
            if run is not None:
                run["status"] = "cancelled"
                run["error"] = "cancelled while queued"
                run["elapsed"] = 0.0
                run["finished"] = time.time()
                _persist_locked(run)

    if not _acquire_run_slot(run_id):
        cancelled_while_queued()
        return

    try:
        with _runs_lock:
            run = _runs.get(run_id)
            if run is None:
                return
            if run.get("cancel_requested"):
                pass
            else:
                run["status"] = "running"
                started = time.time()
                run["started"] = started
                _persist_locked(run)
                run = None
        if run is not None:
            cancelled_while_queued()
            return

        rc, outcome, stderr_tail = _spawn_and_wait(run_id, cmd, env)
        elapsed = time.time() - started

        if rc is None:
            _mark_error(run_id, outcome)
            return

        with _runs_lock:
            run = _runs.get(run_id)
            if run is None:
                return
            run["elapsed"] = elapsed
            run["finished"] = time.time()
            _apply_results_locked(run)
            has_result = bool(run.get("result_json_url"))

            if outcome == "cancelled":
                run["status"] = "cancelled"
                run["error"] = "cancelled by user"
            elif has_result and rc in (0, 1, 2):
                # Exit codes: 0=ok, 1=failure, 2=inconclusive (per orchestrator CLI)
                run["status"] = "completed"
                if settings.get("formalize_in_run") and next(
                        p for p in PROJECTS if p["id"] == project)["formalize_module"]:
                    chain_formalize = live
                    if chain_formalize:
                        _mark_formalizing_locked(run, keep_elapsed=True)
                    else:
                        _append_log_locked(
                            run_id, "in-run formalization skipped: a mock run makes no live API calls")
            else:
                run["status"] = "error"
                tail = "\n".join(stderr_tail[-10:])
                run["error"] = (f"exit code {rc}, no result JSON produced" if not has_result
                                else f"exit code {rc}") + (f"\n{tail}" if tail else "")
            _persist_locked(run)
    finally:
        _release_run_slot()

    if chain_formalize:
        # In-run formalization = the separate entry, chained after the result is
        # written (same settings as the button: models, corrections, max_tokens).
        # The run went straight from "running" to "formalizing" (no "completed"
        # flicker for pollers); the worker queues for its own slot.
        _append_log(run_id, "--- formalize (in-run setting) ---")
        _formalize_worker(run_id, project, run_dir, settings, False)


def _mark_formalizing_locked(run: dict, keep_elapsed: bool = False) -> None:
    """Completed (or just-finished) -> formalizing (call with _runs_lock held).
    `keep_elapsed`: the pipeline's own elapsed time stays the run's elapsed."""
    run["status"] = "formalizing"
    run["cancel_requested"] = False
    run["formalize_error"] = None
    run["finished"] = None
    if not keep_elapsed:
        run["elapsed"] = None
    run["server_pid"] = os.getpid()
    run["server_identity"] = process_identity(os.getpid())
    _persist_locked(run)


def _has_proved_formalization(run_dir: Path) -> bool:
    """Did the last recorded formalization end machine-checked (proved)?"""
    last = None
    for ev in _read_progress(run_dir, 0, 10**9)["events"]:
        if ev.get("event") == "formalization":
            last = ev.get("payload") or {}
    return bool(last) and (last.get("proved") is True or last.get("status") == "proved")


def api_formalize(run_id: str, payload: dict | None = None) -> dict:
    """Formalize a finished run (its own entry point, over an existing result).

    Idempotent like the CLI: a result whose block is already proved is not
    formalized again (409) unless the client sends {"force": true}; `--force`
    is passed to the CLI only for that explicit request, and the CLI itself
    never lets a non-proved re-run replace a proved block."""
    payload = payload or {}
    if payload.get("confirm_spend") is not True:
        raise ValueError("formalization makes live API calls: send {\"confirm_spend\": true}")
    force = payload.get("force") is True
    settings = load_settings()
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        project = run.get("project")
        if project not in PROJECT_IDS:
            raise ConflictError("run has no known project, cannot formalize")
        cfg = next(p for p in PROJECTS if p["id"] == project)
        if not cfg["formalize_module"]:
            raise ValueError(f"formalization is not available for {project}")
        if run["status"] != "completed" or not run.get("result_json_url"):
            raise ConflictError(
                f"only a completed run with a result can be formalized (status: {run['status']})")
        if run.get("result_status") == "failure":
            raise ValueError("the pipeline reported a failure for this run; there is nothing to formalize")
        if not run.get("verdict"):
            raise ValueError("the run has no verdict; there is nothing to formalize")
        run_dir = Path(run["run_dir"])
        if not force and _has_proved_formalization(run_dir):
            raise ConflictError(
                "the result is already formalized and machine-checked (proved); "
                "send {\"force\": true} to run it again (the proof is kept unless the new "
                "attempt proves it as well)")
        source_mode = _source_mode(run)
        _mark_formalizing_locked(run)

    threading.Thread(target=_formalize_worker, args=(run_id, project, run_dir, settings, force),
                     daemon=True).start()
    out = {"run_id": run_id, "status": "formalizing", "log_url": f"/api/log/{run_id}",
           "source_mode": source_mode}
    if source_mode == "mock":
        out["warning"] = MOCK_FORMALIZE_WARNING
    return out


def _formalize_worker(run_id: str, project: str, run_dir: Path, settings: dict,
                      force: bool = False) -> None:
    """Run `<system>.formalize` over the run; the run ends up "completed"
    again (a failed/cancelled formalization only sets formalize_error — the
    underlying result is still valid)."""
    def settle(error: str | None) -> None:
        with _runs_lock:
            run = _runs.get(run_id)
            if run is None:
                return
            _apply_results_locked(run)
            run["status"] = "completed"
            run["formalize_error"] = error
            run["finished"] = time.time()
            run["cancel_requested"] = False
            _persist_locked(run)

    _append_log(run_id, "--- formalize ---")
    if not _acquire_run_slot(run_id):
        settle("cancelled while queued")
        return
    try:
        cmd = build_formalize_command(project, run_dir, settings, force)
        rc, outcome, stderr_tail = _spawn_and_wait(run_id, cmd, _settings_env(settings, project))
        if rc is None:
            settle(outcome)
        elif outcome == "cancelled":
            settle("formalization cancelled by user")
        elif rc != 0:
            tail = "\n".join(stderr_tail[-10:])
            settle(f"formalize exit code {rc}" + (f"\n{tail}" if tail else ""))
        else:
            settle(None)
    finally:
        _release_run_slot()


def _mark_error(run_id: str, msg: str) -> None:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is not None:
            run["status"] = "error"
            run["error"] = msg
            run["lines"].append(f"ERROR: {msg}")
            run["finished"] = time.time()
            _persist_locked(run)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # Suppress default access log; we'll log our own, compact.
    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        sys.stderr.write(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}\n")

    # -- JSON helpers --
    def _send_json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, status: int = 200) -> None:
        if not path.is_file():
            self._send_text("not found", status=404)
            return
        ctype, _ = mimetypes.guess_type(path.name)
        if ctype is None:
            ctype = "application/octet-stream"
        data = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status=status)

    # -- Request guards --
    def _allowed_origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"http://{h}:{port}" for h in LOOPBACK_HOSTS}

    def _host_ok(self) -> bool:
        """Only answer requests addressed to a loopback host (anti DNS rebinding)."""
        host = (self.headers.get("Host") or "").lower()
        return f"http://{host}" in self._allowed_origins()

    def _post_ok(self) -> bool:
        """Same-origin JSON only: a cross-site page can send a "simple"
        (no-cors) POST, but not with Content-Type: application/json."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in self._allowed_origins()

    # -- Routing --
    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            self._send_error_json(403, "forbidden host")
            return
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html")
                return

            if path.startswith("/static/"):
                fpath = _files_in(STATIC_DIR, recursive=True).get(path[len("/static/"):])
                if fpath is None:
                    self._send_error_json(404, "not found")
                    return
                self._send_file(fpath)
                return

            if path == "/api/projects":
                self._send_json(api_projects())
                return

            if path.startswith("/api/examples/"):
                project = path[len("/api/examples/"):]
                self._send_json(api_examples(project))
                return

            if path.startswith("/api/example/"):
                parts = path[len("/api/example/"):].split("/", 1)
                if len(parts) != 2:
                    self._send_error_json(400, "expected /api/example/<project>/<file>")
                    return
                self._send_json(api_example(parts[0], parts[1]))
                return

            if path == "/api/runs":
                self._send_json(api_runs())
                return

            if path.startswith("/api/runs/") and path.endswith("/formalize/estimate"):
                run_id = path[len("/api/runs/"):-len("/formalize/estimate")]
                if not RUN_ID_RE.fullmatch(run_id):
                    self._send_error_json(404, f"run not found: {run_id}")
                    return
                self._send_json(api_formalize_estimate(run_id))
                return

            if path.startswith("/api/runs/"):
                run_id = path[len("/api/runs/"):]
                if not RUN_ID_RE.fullmatch(run_id):
                    self._send_error_json(404, f"run not found: {run_id}")
                    return
                query = parse_qs(parsed.query)
                self._send_json(api_run_detail(
                    run_id,
                    after=(query.get("after") or [None])[0],
                    limit=(query.get("limit") or [None])[0],
                ))
                return

            if path == "/api/settings":
                self._send_json(api_get_settings())
                return

            if path == "/api/estimate":
                query = parse_qs(parsed.query)
                self._send_json(api_estimate(
                    (query.get("project") or [None])[0],
                    (query.get("formalize") or [None])[0],
                ))
                return

            if path.startswith("/api/log/"):
                run_id = path[len("/api/log/"):]
                self._send_json(api_log(run_id))
                return

            if path.startswith("/runs/"):
                # /runs/<run_id>/<file>
                rest = path[len("/runs/"):]
                parts = rest.split("/", 1)
                if len(parts) != 2:
                    self._send_error_json(400, "bad run path")
                    return
                run_id, fname = parts
                run_dir = _run_dirs().get(run_id)
                fpath = _files_in(run_dir).get(fname) if run_dir else None
                if fpath is None:
                    self._send_error_json(404, "not found")
                    return
                self._send_file(fpath)
                return

            self._send_error_json(404, f"no route for GET {path}")

        except FileNotFoundError as exc:
            self._send_error_json(404, str(exc))
        except ConflictError as exc:
            self._send_error_json(409, str(exc))
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:
            logger.exception("GET %s failed", self.path)
            self._send_error_json(500, f"internal: {exc}")

    def do_POST(self):  # noqa: N802
        self._handle_body_request("POST")

    def do_PUT(self):  # noqa: N802
        self._handle_body_request("PUT")

    def _handle_body_request(self, method: str) -> None:
        if not self._host_ok() or not self._post_ok():
            # Drain the (bounded) unread body first: closing a socket with
            # unread data makes Windows reset the connection (WinError 10053)
            # and the client then loses the 403 response.
            try:
                pending = int(self.headers.get("Content-Length", "0"))
                if 0 < pending <= MAX_BODY_BYTES:
                    self.rfile.read(pending)
            except (ValueError, OSError):
                pass
            self._send_error_json(403, "forbidden: same-origin application/json requests only")
            return
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self._send_error_json(413, "request body too large")
                return
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError as exc:
                self._send_error_json(400, f"invalid JSON body: {exc}")
                return

            if method == "PUT":
                if path == "/api/settings":
                    self._send_json(api_put_settings(payload))
                    return
                self._send_error_json(404, f"no route for PUT {path}")
                return

            if path == "/api/run":
                self._send_json(api_run(payload))
                return

            if path.startswith("/api/runs/") and path.endswith("/cancel"):
                run_id = path[len("/api/runs/"):-len("/cancel")]
                self._send_json(api_cancel(run_id))
                return

            if path.startswith("/api/runs/") and path.endswith("/formalize"):
                run_id = path[len("/api/runs/"):-len("/formalize")]
                self._send_json(api_formalize(run_id, payload))
                return

            self._send_error_json(404, f"no route for POST {path}")

        except FileNotFoundError as exc:
            self._send_error_json(404, str(exc))
        except ConflictError as exc:
            self._send_error_json(409, str(exc))
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:
            logger.exception("%s %s failed", method, self.path)
            self._send_error_json(500, f"internal: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global MAX_CONCURRENT_RUNS

    parser = argparse.ArgumentParser(description="TFL Lab local UI server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default: 127.0.0.1, localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-concurrent-runs", type=int, default=MAX_CONCURRENT_RUNS,
                        help=f"max pipeline subprocesses running at once (default: {MAX_CONCURRENT_RUNS}; "
                             "env TFL_LAB_MAX_CONCURRENT_RUNS)")
    parser.add_argument("--force", action="store_true",
                        help="start even though recorded runs still have a live process "
                             "(they are adopted and settled when their process ends)")
    args = parser.parse_args()
    MAX_CONCURRENT_RUNS = args.max_concurrent_runs

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(
            f"WARNING: binding to {args.host} — TFL Lab has no auth; "
            "requests are still only answered for a loopback Host header.\n"
        )
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)  # bind first: fails fast if in use
    active = restore_runs()
    if active and not args.force:
        server.server_close()
        sys.stderr.write(
            "REFUSING TO START: runs are still active (live process): "
            f"{', '.join(active)}.\n"
            "Another TFL Lab server (or a pipeline it started) is still working on them; "
            "restarting over them would orphan the runs. Wait for them, cancel them, "
            "or start with --force to adopt them.\n"
        )
        sys.exit(1)
    if active:
        adopt_orphans(active)
        sys.stderr.write(f"--force: adopted {len(active)} active run(s): {', '.join(active)}\n")
    url = f"http://{args.host}:{args.port}/"
    sys.stderr.write(f"TFL Lab UI → {url}\n")
    sys.stderr.write(f"  static:  {STATIC_DIR}\n")
    sys.stderr.write(f"  runs:    {RUNS_DIR} ({len(_runs)} restored)\n")
    sys.stderr.write(f"  projects: {', '.join(p['id'] for p in PROJECTS)}\n")
    sys.stderr.write(f"  max concurrent runs: {MAX_CONCURRENT_RUNS}, no run timeout (cancel manually)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
        server.shutdown()


if __name__ == "__main__":
    main()
