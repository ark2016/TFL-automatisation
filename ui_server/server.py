"""TFL Lab — local UI server for running agent pipelines.

Minimal HTTP server (stdlib only) that serves the single-page UI and
exposes a small API:

  GET  /                          → index.html
  GET  /static/<file>             → static asset
  GET  /api/projects              → {"projects": [{id, label}, ...]}
  GET  /api/examples/<project>    → {"examples": ["task_x.json", ...]}
  GET  /api/example/<project>/<f> → {"ir": {...}}
  POST /api/run                   → {"run_id", "result_html_url", ...}
                                     (runs the pipeline in a thread,
                                      streams stderr to a log buffer)
  GET  /api/log/<run_id>          → {"status", "lines", "elapsed", ...}
  GET  /runs/<run_id>/<file>      → rendered result artifact

Usage:
    .venv/Scripts/python -m ui_server.server [--port 8000]

Security note: binds to 127.0.0.1 only. No auth. Do not expose externally.
Requests are only served for a loopback Host header (blocks DNS rebinding),
POST /api/run additionally requires a same-origin JSON request (blocks
cross-site "no-cors" form posts from other pages starting paid live runs),
and every file route is confined to its directory.
"""

from __future__ import annotations

import argparse
import io
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
from urllib.parse import unquote, urlparse

logger = logging.getLogger("tfl_lab")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = ROOT / ".tfl_lab_runs"
RUNS_DIR.mkdir(exist_ok=True)

# Project registry. Each project has an id (filesystem dir), a short label,
# and the name of its orchestrator module (-m target).
PROJECTS: list[dict] = [
    {"id": "agent_system", "label": "REG",  "module": "agent_system.orchestrator"},
    {"id": "cfl_system",   "label": "CFL",  "module": "cfl_system.orchestrator"},
    {"id": "dcfl_system",  "label": "DCFL", "module": "dcfl_system.orchestrator"},
    {"id": "ll_system",    "label": "LL",   "module": "ll_system.orchestrator"},
]
PROJECT_IDS = {p["id"] for p in PROJECTS}
# Examples directory per project, built from the registry — request data is
# only ever used as a lookup key, never joined into a filesystem path.
EXAMPLE_DIRS: dict[str, Path] = {p["id"]: ROOT / p["id"] / "examples" for p in PROJECTS}

RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_BODY_BYTES = 1_000_000


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
    return {d.name: d for d in RUNS_DIR.iterdir() if d.is_dir() and RUN_ID_RE.fullmatch(d.name)}

# In-memory run registry. Threading lock protects concurrent access.
# {run_id: {"status", "lines" (list[str]), "started", "elapsed",
#           "result_html_url", "result_json_url", "result_md_url",
#           "verdict", "error"}}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------

def api_projects() -> dict:
    return {"projects": [{"id": p["id"], "label": p["label"]} for p in PROJECTS]}


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


def api_log(run_id: str) -> dict:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        # Snapshot copy
        return {
            "run_id": run_id,
            "status": run["status"],
            "lines": list(run["lines"]),
            "elapsed": run.get("elapsed"),
            "started": run.get("started"),
            "verdict": run.get("verdict"),
            "error": run.get("error"),
            "result_html_url": run.get("result_html_url"),
            "result_json_url": run.get("result_json_url"),
            "result_md_url":   run.get("result_md_url"),
        }


def api_run(payload: dict) -> dict:
    project = payload.get("project")
    ir = payload.get("ir")
    live = bool(payload.get("live"))
    verbose = bool(payload.get("verbose", True))
    if project not in PROJECT_IDS:
        raise ValueError(f"unknown project: {project}")
    if not isinstance(ir, dict):
        raise ValueError("ir must be a JSON object")

    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(exist_ok=True)
    # Write the IR to a file the orchestrator can read
    ir_path = run_dir / "input.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")

    with _runs_lock:
        _runs[run_id] = {
            "status": "running",
            "project": project,
            "lines": [],
            "started": time.time(),
            "elapsed": None,
            "run_dir": str(run_dir),
            "result_html_url": None,
            "result_json_url": None,
            "result_md_url": None,
            "verdict": None,
            "error": None,
        }

    thread = threading.Thread(
        target=_run_pipeline_worker,
        args=(run_id, project, ir_path, run_dir, live, verbose),
        daemon=True,
    )
    thread.start()

    return {
        "run_id": run_id,
        "status": "running",
        "log_url": f"/api/log/{run_id}",
    }


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


def _run_pipeline_worker(run_id: str, project: str, ir_path: Path,
                         run_dir: Path, live: bool, verbose: bool) -> None:
    """Run the project's orchestrator (see build_command), stream stderr
    into the run's log buffer."""
    cmd = build_command(project, ir_path, run_dir, live, verbose)

    started = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
        )
    except Exception as exc:
        _mark_error(run_id, f"failed to launch: {exc}")
        return

    # Stream both stdout (result JSON) and stderr (log) in parallel.
    stdout_buf: list[str] = []

    stderr_tail: list[str] = []

    def pump_stderr() -> None:
        for line in proc.stderr:
            line = line.rstrip("\n")
            _append_log(run_id, line)
            stderr_tail.append(line)
            del stderr_tail[:-20]

    def pump_stdout() -> None:
        for line in proc.stdout:
            stdout_buf.append(line)

    t_err = threading.Thread(target=pump_stderr, daemon=True)
    t_out = threading.Thread(target=pump_stdout, daemon=True)
    t_err.start(); t_out.start()

    rc = proc.wait()
    t_err.join(timeout=2.0)
    t_out.join(timeout=2.0)
    elapsed = time.time() - started

    # Figure out which artifacts were produced.
    # Orchestrators --save writes <task_stem>_result.{json,md,html}
    stem = ir_path.stem  # always "input"
    # But some orchestrators derive task_name from the input filename; ours is "input"
    result_json = run_dir / f"{stem}_result.json"
    result_html = run_dir / f"{stem}_result.html"
    result_md   = run_dir / f"{stem}_result.md"

    verdict = None
    if result_json.exists():
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
            verdict = data.get("verdict")
        except Exception:
            pass

    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run["elapsed"] = elapsed
        run["verdict"] = verdict
        if result_html.exists():
            run["result_html_url"] = f"/runs/{run_id}/{result_html.name}"
        if result_json.exists():
            run["result_json_url"] = f"/runs/{run_id}/{result_json.name}"
        if result_md.exists():
            run["result_md_url"] = f"/runs/{run_id}/{result_md.name}"
        if rc == 0 and result_json.exists():
            run["status"] = "completed"
        else:
            # Exit codes: 0=ok, 1=failure, 2=inconclusive (per orchestrator CLI)
            run["status"] = "completed" if rc in (0, 2) and result_json.exists() else "error"
            if not result_json.exists():
                tail = "\n".join(stderr_tail[-10:])
                run["error"] = f"exit code {rc}, no result JSON produced" + (
                    f"\n{tail}" if tail else ""
                )


def _append_log(run_id: str, line: str) -> None:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is not None:
            run["lines"].append(line)


def _mark_error(run_id: str, msg: str) -> None:
    with _runs_lock:
        run = _runs.get(run_id)
        if run is not None:
            run["status"] = "error"
            run["error"] = msg
            run["lines"].append(f"ERROR: {msg}")


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
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:
            logger.exception("GET %s failed", self.path)
            self._send_error_json(500, f"internal: {exc}")

    def do_POST(self):  # noqa: N802
        if not self._host_ok() or not self._post_ok():
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

            if path == "/api/run":
                self._send_json(api_run(payload))
                return

            self._send_error_json(404, f"no route for POST {path}")

        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:
            logger.exception("POST %s failed", self.path)
            self._send_error_json(500, f"internal: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="TFL Lab local UI server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default: 127.0.0.1, localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(
            f"WARNING: binding to {args.host} — TFL Lab has no auth; "
            "requests are still only answered for a loopback Host header.\n"
        )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    sys.stderr.write(f"TFL Lab UI → {url}\n")
    sys.stderr.write(f"  static:  {STATIC_DIR}\n")
    sys.stderr.write(f"  runs:    {RUNS_DIR}\n")
    sys.stderr.write(f"  projects: {', '.join(p['id'] for p in PROJECTS)}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
        server.shutdown()


if __name__ == "__main__":
    main()
