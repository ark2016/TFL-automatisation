"""TFL Lab server: request guards, path confinement, CLI contract."""

import http.client
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ui_server import server as srv

TERMINAL_STATUSES = {"completed", "error", "cancelled", "interrupted"}


def _sleep_command(seconds: float):
    """A `build_command` replacement: a real subprocess (no orchestrator,
    no API key needed) that just sleeps, so timeout/cancel/concurrency
    tests control exactly how long a "run" stays alive."""
    return lambda *a, **k: [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _drain_run(run_id, timeout=5.0):
    """Poll api_log until the run reaches a terminal status."""
    deadline = time.time() + timeout
    data = srv.api_log(run_id)
    while time.time() < deadline and data["status"] not in TERMINAL_STATUSES:
        time.sleep(0.02)
        data = srv.api_log(run_id)
    assert data["status"] in TERMINAL_STATUSES, (
        f"run {run_id} stuck in {data['status']!r} after {timeout}s"
    )
    return data


def _wait_for_status(run_id, status, timeout=2.0):
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        seen = srv.api_log(run_id)["status"]
        if seen == status:
            return True
        time.sleep(0.02)
    return seen == status


@pytest.fixture(scope="module")
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_index_and_static_served(port):
    assert request(port, "GET", "/")[0] == 200
    assert request(port, "GET", "/static/index.html")[0] == 200


@pytest.mark.parametrize("path", [
    "/static/..%2fserver.py",
    "/static/..%2f..%2fpyproject.toml",
    "/static/../../pyproject.toml",
    "/static/C:/Windows/win.ini",
    "/static//etc/passwd",
])
def test_static_path_traversal_blocked(port, path):
    status, body = request(port, "GET", path)
    assert status == 404
    assert b"import" not in body and b"[project]" not in body


@pytest.mark.parametrize("path", [
    "/runs/..%2f..%2fpyproject.toml/x",
    "/runs/not-a-run-id/input.json",
    "/runs/../input.json",
])
def test_runs_path_validated(port, path):
    assert request(port, "GET", path)[0] in (400, 404)


@pytest.mark.parametrize("path", [
    "/api/example/cfl_system/..%2f..%2fpyproject.toml",
    "/api/example/cfl_system/../../pyproject.toml",
    "/api/example/cfl_system/C:/Windows/win.ini",
    "/api/example/..%2fcfl_system/task11_ai_bj_between.json",
])
def test_example_path_traversal_blocked(port, path):
    status, body = request(port, "GET", path)
    assert status in (400, 404)
    assert b"[project]" not in body


def test_example_and_listing_served(port):
    status, body = request(port, "GET", "/api/examples/cfl_system")
    assert status == 200 and "task11_ai_bj_between.json" in json.loads(body)["examples"]
    status, body = request(port, "GET", "/api/example/cfl_system/task11_ai_bj_between.json")
    assert status == 200 and "ir" in json.loads(body)


def test_run_artifact_served(port):
    run_dir = srv.RUNS_DIR / "0123456789ab"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "input_result.json").write_text('{"verdict": "cfl"}', encoding="utf-8")
    try:
        status, body = request(port, "GET", "/runs/0123456789ab/input_result.json")
        assert status == 200 and json.loads(body)["verdict"] == "cfl"
        assert request(port, "GET", "/runs/0123456789ab/missing.json")[0] == 404
    finally:
        shutil.rmtree(run_dir)


def test_foreign_host_rejected(port):
    status, _ = request(port, "GET", "/api/projects", headers={"Host": "evil.example:80"})
    assert status == 403


def test_post_requires_json_content_type(port):
    body = json.dumps({"project": "nope", "ir": {}})
    status, _ = request(port, "POST", "/api/run", body, {"Content-Type": "text/plain"})
    assert status == 403


def test_post_rejects_cross_origin(port):
    body = json.dumps({"project": "nope", "ir": {}})
    status, _ = request(port, "POST", "/api/run", body, {
        "Content-Type": "application/json", "Origin": "https://evil.example",
    })
    assert status == 403


def test_same_origin_json_post_passes_guards(port):
    # Unknown project -> 400 from api_run, i.e. the request got past the guards.
    body = json.dumps({"project": "nope", "ir": {}})
    status, _ = request(port, "POST", "/api/run", body, {
        "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}",
    })
    assert status == 400


EXAMPLES = {
    "agent_system": "task1_palindrome_prefix_suffix.json",
    "cfl_system": "task11_ai_bj_between.json",
    "dcfl_system": "task_anb_cnbn.json",
    "ll_system": "format3_simple_ll1.json",
}


@pytest.mark.parametrize("project", sorted(srv.PROJECT_IDS))
def test_every_project_accepts_server_cli_contract(project, tmp_path, monkeypatch):
    """The argv built by the server (offline, no --live) must produce
    input_result.json with a top-level verdict for every pipeline."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ir_path = tmp_path / "input.json"
    shutil.copy(srv.ROOT / project / "examples" / EXAMPLES[project], ir_path)

    cmd = srv.build_command(project, ir_path, tmp_path, live=False, verbose=False)
    proc = subprocess.run(cmd, cwd=srv.ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)

    result_json = tmp_path / "input_result.json"
    assert proc.returncode in (0, 1, 2), proc.stderr[-2000:]
    assert result_json.exists(), proc.stderr[-2000:]
    assert "verdict" in json.loads(result_json.read_text(encoding="utf-8"))
    assert (tmp_path / "input_result.html").exists()


# ---------------------------------------------------------------------------
# Concurrency limit, timeout, cancel (TODO §4)
#
# These drive `api_run`/`api_log`/`api_cancel` directly rather than through
# HTTP: `build_command` is monkeypatched to a plain `time.sleep` subprocess,
# so no orchestrator/API key is involved and run length is exact.
# ---------------------------------------------------------------------------

def _cleanup_runs(*run_ids):
    for run_id in run_ids:
        shutil.rmtree(srv.RUNS_DIR / run_id, ignore_errors=True)


def test_concurrency_limit_queues_extra_runs(monkeypatch):
    monkeypatch.setattr(srv, "MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(srv, "build_command", _sleep_command(0.6))

    r1 = srv.api_run({"project": "cfl_system", "ir": {}})
    r2 = srv.api_run({"project": "cfl_system", "ir": {}})
    id1, id2 = r1["run_id"], r2["run_id"]
    try:
        # With only one slot, run 1 must start and run 2 must queue behind it.
        assert _wait_for_status(id1, "running", timeout=2.0)
        assert srv.api_log(id2)["status"] == "queued"

        _drain_run(id1, timeout=5.0)
        # Freeing the slot lets the queued run start and finish too.
        _drain_run(id2, timeout=5.0)
    finally:
        _cleanup_runs(id1, id2)


def test_run_timeout_is_gone(monkeypatch):
    """Runs have no timeout at all: no constant, no CLI flag; a slow run is
    only ever ended by its own exit or a manual cancel."""
    assert not hasattr(srv, "RUN_TIMEOUT_SECONDS")
    assert not hasattr(srv, "DEFAULT_RUN_TIMEOUT_SECONDS")
    monkeypatch.setattr(sys, "argv", ["ui_server", "--run-timeout", "5", "--port", "0"])
    with pytest.raises(SystemExit) as exc:
        srv.main()
    assert exc.value.code == 2  # argparse: unrecognized argument


def test_slow_run_is_not_killed(monkeypatch):
    monkeypatch.setattr(srv, "build_command", _sleep_command(1.2))
    r = srv.api_run({"project": "cfl_system", "ir": {}})
    run_id = r["run_id"]
    try:
        time.sleep(0.8)
        assert srv.api_log(run_id)["status"] == "running"
        # ended by its own exit (the sleep stub writes no result → "error"),
        # never by a watchdog kill
        data = _drain_run(run_id, timeout=5.0)
        assert data["status"] == "error" and "cancelled" not in (data["error"] or "")
    finally:
        _cleanup_runs(run_id)


def test_cancel_running_run_via_http(port, monkeypatch):
    monkeypatch.setattr(srv, "build_command", _sleep_command(30))

    r = srv.api_run({"project": "cfl_system", "ir": {}})
    run_id = r["run_id"]
    try:
        assert _wait_for_status(run_id, "running", timeout=2.0)

        status, body = request(port, "POST", f"/api/runs/{run_id}/cancel", "{}", {
            "Content-Type": "application/json",
        })
        assert status == 200
        assert json.loads(body)["status"] in ("cancelling", "cancelled")

        data = _drain_run(run_id, timeout=5.0)
        assert data["status"] == "cancelled"
    finally:
        _cleanup_runs(run_id)


def test_cancel_queued_run_never_launches_subprocess(monkeypatch):
    monkeypatch.setattr(srv, "MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(srv, "build_command", _sleep_command(1.0))

    r1 = srv.api_run({"project": "cfl_system", "ir": {}})   # holds the only slot
    r2 = srv.api_run({"project": "cfl_system", "ir": {}})   # stays queued
    id1, id2 = r1["run_id"], r2["run_id"]
    try:
        assert _wait_for_status(id2, "queued", timeout=2.0)

        result = srv.api_cancel(id2)
        assert result["status"] in ("cancelling", "cancelled")

        data = _drain_run(id2, timeout=3.0)
        assert data["status"] == "cancelled"
        assert data["result_json_url"] is None  # never ran

        _drain_run(id1, timeout=3.0)
    finally:
        _cleanup_runs(id1, id2)


def test_cancel_unknown_run_is_404():
    with pytest.raises(FileNotFoundError):
        srv.api_cancel("0" * 12)


def test_cancel_already_finished_run_is_idempotent(monkeypatch):
    monkeypatch.setattr(srv, "build_command", _sleep_command(0))
    r = srv.api_run({"project": "cfl_system", "ir": {}})
    run_id = r["run_id"]
    try:
        _drain_run(run_id, timeout=5.0)
        result = srv.api_cancel(run_id)
        assert result["status"] == srv.api_log(run_id)["status"]
    finally:
        _cleanup_runs(run_id)


def test_cancel_requires_same_origin_json_guards(port):
    # Guarded the same way as /api/run (see test_post_requires_json_content_type).
    status, _ = request(port, "POST", "/api/runs/000000000000/cancel", "{}", {
        "Content-Type": "text/plain",
    })
    assert status == 403


# ---------------------------------------------------------------------------
# CSP meta / sandboxed report iframe (TODO §4)
# ---------------------------------------------------------------------------

def test_csp_meta_present(port):
    status, body = request(port, "GET", "/")
    assert status == 200
    assert b"Content-Security-Policy" in body
    assert b"default-src 'self'" in body
    assert b"script-src 'self' https://cdnjs.cloudflare.com" in body


def test_result_iframe_is_sandboxed(port):
    status, body = request(port, "GET", "/")
    assert status == 200
    assert b'id="result-iframe"' in body
    assert b'sandbox="allow-scripts"' in body
    # allow-same-origin would let a compromised report reach this page's DOM.
    assert b"allow-same-origin" not in body


def test_app_js_sanitizes_markdown_with_pinned_cdn(port):
    status, body = request(port, "GET", "/static/app.js")
    assert status == 200
    assert b"DOMPurify.sanitize" in body
    assert b"cdnjs.cloudflare.com" in body
    assert b"integrity" in body and b"sha384-" in body
