"""TFL Lab server: request guards, path confinement, CLI contract."""

import http.client
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ui_server import server as srv


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
