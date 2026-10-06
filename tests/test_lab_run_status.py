"""TFL Lab: pipeline failure status, task_type pre-check, Formalize refusal,
UTF-8 child log. Mock only: pipeline subprocesses are tiny `python -c` stubs,
RUNS_DIR / SETTINGS_PATH live in tmp and nothing calls an API."""

import http.client
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from ui_server import server as srv

FAILURE_STUB = r"""
import json, os, sys
d = sys.argv[1]
json.dump({"status": "failure", "errors": ["Invalid task_type: 'classify_and_prove_cfl'"]},
          open(os.path.join(d, "input_result.json"), "w"))
sys.exit(1)
"""

OK_STUB = r"""
import json, os, sys
d = sys.argv[1]
json.dump({"status": "success", "verdict": "regular", "confidence": 0.9},
          open(os.path.join(d, "input_result.json"), "w"))
"""

NO_VERDICT_STUB = r"""
import json, os, sys
d = sys.argv[1]
json.dump({"status": "partial", "verdict": None}, open(os.path.join(d, "input_result.json"), "w"))
"""

CYRILLIC_STUB = r"""
import json, os, sys
d = sys.argv[1]
sys.stderr.write("cwd: C:\\programming\\бакалавриат\\TFL\n")
json.dump({"status": "success", "verdict": "regular"}, open(os.path.join(d, "input_result.json"), "w"))
"""


@pytest.fixture(autouse=True)
def lab_state(tmp_path, monkeypatch):
    runs = tmp_path / "lab_runs"
    runs.mkdir()
    monkeypatch.setattr(srv, "RUNS_DIR", runs)
    monkeypatch.setattr(srv, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(srv, "_runs", {})
    monkeypatch.setattr(srv, "MAX_CONCURRENT_RUNS", 2)


@pytest.fixture
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def post(port, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(payload),
                 headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def stub(monkeypatch, code):
    monkeypatch.setattr(
        srv, "build_command",
        lambda project, ir_path, run_dir, live, verbose: [sys.executable, "-c", code, str(run_dir)])


def run_to_end(project, ir):
    run_id = srv.api_run({"project": project, "ir": ir})["run_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        data = srv.api_log(run_id)
        if data["status"] not in srv.ACTIVE_STATUSES:
            return data
        time.sleep(0.03)
    raise AssertionError("run did not finish")


# --- item 1: pipeline result status -----------------------------------------

def test_failure_result_is_exposed_separately_from_process_status(monkeypatch):
    stub(monkeypatch, FAILURE_STUB)
    data = run_to_end("agent_system", {"task_type": "classify"})
    assert data["status"] == "completed"           # process produced a result
    assert data["result_status"] == "failure"      # the pipeline's own outcome
    assert data["result_errors"] == ["Invalid task_type: 'classify_and_prove_cfl'"]
    assert data["verdict"] is None
    rec = json.loads((srv.RUNS_DIR / data["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert rec["result_status"] == "failure" and rec["result_errors"]


def test_result_status_survives_restart_and_legacy_records(monkeypatch):
    stub(monkeypatch, FAILURE_STUB)
    data = run_to_end("agent_system", {"task_type": "classify"})
    run_dir = srv.RUNS_DIR / data["run_id"]
    # a record written before result_status existed: filled from the result file
    rec_path = run_dir / "run.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec.pop("result_status"); rec.pop("result_errors")
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    srv._runs.clear()
    srv.restore_runs()
    again = srv.api_log(data["run_id"])
    assert again["result_status"] == "failure"
    assert again["result_errors"]


def test_success_result_status(monkeypatch):
    stub(monkeypatch, OK_STUB)
    data = run_to_end("agent_system", {"task_type": "classify"})
    assert data["result_status"] == "success" and data["result_errors"] == []


# --- item 2: task_type pre-check ---------------------------------------------

def test_projects_listing_carries_task_types(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/projects", headers={"Host": f"127.0.0.1:{port}"})
    d = json.loads(conn.getresponse().read())
    by_id = {p["id"]: p for p in d["projects"]}
    assert "classify_and_prove_cfl" in by_id["cfl_system"]["task_types"]
    assert "classify_and_prove_cfl" not in by_id["agent_system"]["task_types"]
    assert "ll_check_grammar" in by_id["ll_system"]["task_types"]


@pytest.mark.parametrize("project,task_type", [
    ("agent_system", "classify_and_prove_cfl"),
    ("cfl_system", "classify"),
    ("dcfl_system", "classify_cfl"),
    ("ll_system", "classify_dcfl"),
    ("agent_system", "no_such_type"),
])
def test_mismatched_task_type_is_rejected_with_400(port, monkeypatch, project, task_type):
    spawned = []
    monkeypatch.setattr(srv, "build_command", lambda *a, **k: spawned.append(a) or [sys.executable, "-c", ""])
    status, d = post(port, "/api/run", {"project": project, "ir": {"task_type": task_type}})
    assert status == 400
    assert task_type in d["error"] and "expected one of" in d["error"]
    assert not spawned and not list(srv.RUNS_DIR.iterdir())  # nothing was started


def test_mismatch_message_names_the_owning_pipeline(port):
    status, d = post(port, "/api/run",
                     {"project": "agent_system", "ir": {"task_type": "classify_and_prove_cfl"}})
    assert status == 400 and "CFL" in d["error"]


@pytest.mark.parametrize("project", ["agent_system", "cfl_system", "dcfl_system", "ll_system"])
def test_ir_without_task_type_is_not_blocked_here(monkeypatch, project):
    stub(monkeypatch, OK_STUB)
    assert srv.check_task_type(project, {"source_text": "x"}) is None
    assert run_to_end(project, {"source_text": "x"})["status"] == "completed"


@pytest.mark.parametrize("project,task_type", [
    ("agent_system", "classify_and_prove"), ("cfl_system", "classify_and_prove_cfl"),
    ("dcfl_system", "prove_dcfl"), ("ll_system", "ll_check_grammar"),
])
def test_matching_task_type_passes(project, task_type):
    assert srv.check_task_type(project, {"task_type": task_type}) is None


# --- item 3: Formalize refused for failed / verdict-less runs -----------------

def _no_spawn(monkeypatch):
    calls = []
    monkeypatch.setattr(srv, "build_formalize_command", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(srv, "_formalize_worker", lambda *a, **k: calls.append("worker"))
    return calls


@pytest.mark.parametrize("code", [FAILURE_STUB, NO_VERDICT_STUB])
def test_formalize_refused_without_spending(port, monkeypatch, code):
    stub(monkeypatch, code)
    run_id = run_to_end("agent_system", {"task_type": "classify"})["run_id"]
    calls = _no_spawn(monkeypatch)
    status, d = post(port, f"/api/runs/{run_id}/formalize", {"confirm_spend": True})
    assert status == 400, d
    assert "nothing to formalize" in d["error"]
    assert calls == []
    assert srv.api_log(run_id)["status"] == "completed"  # not moved to "formalizing"


# --- item 4: UTF-8 child log --------------------------------------------------

def test_child_env_forces_utf8():
    env = srv._settings_env({}, "agent_system")
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"


def test_cyrillic_path_in_log_is_not_mojibake(monkeypatch):
    # hostile parent environment: the child must still write UTF-8
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    stub(monkeypatch, CYRILLIC_STUB)
    data = run_to_end("agent_system", {"task_type": "classify"})
    assert any("бакалавриат" in line for line in data["lines"]), data["lines"]
    log = (srv.RUNS_DIR / data["run_id"] / "run.log").read_bytes().decode("utf-8")
    assert "бакалавриат" in log
