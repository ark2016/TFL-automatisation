"""TFL Lab: run persistence, progress API, settings, estimate, formalize.

No external calls: pipeline / formalize subprocesses are replaced by tiny
stub scripts (`python -c`) that write progress.jsonl / result files, the HTTP
server binds port 0, and RUNS_DIR / SETTINGS_PATH live in tmp (conftest.py).
"""

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ui_server import server as srv

JSON_HDR = {"Content-Type": "application/json"}

# Stub pipeline: writes 3 progress events (cumulative usage), the result files
# and a dump of the TFL_* environment it was started with.
PIPELINE_STUB = r"""
import json, os, sys, time
run_dir = sys.argv[1]
def ev(event, node, calls, cost):
    with open(os.path.join(run_dir, "progress.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "t", "event": event, "node": node, "payload": {},
                            "usage": {"calls": calls, "input_tokens": calls * 10,
                                      "output_tokens": calls, "estimated_cost_usd": cost}}) + "\n")
ev("node_start", "n1", 0, 0.0)
json.dump({"status": "running", "last_node": "n1"}, open(os.path.join(run_dir, "partial_result.json"), "w"))
ev("node_done", "n1", 2, 0.25)
ev("llm_call", "n2", 3, 0.5)
env = {k: v for k, v in os.environ.items() if k.startswith("TFL_FORMALIZ")}
json.dump(env, open(os.path.join(run_dir, "env_dump.json"), "w"))
json.dump({"verdict": "regular", "confidence": 0.9}, open(os.path.join(run_dir, "input_result.json"), "w"))
open(os.path.join(run_dir, "input_result.html"), "w").write("<html></html>")
print("stub pipeline done", file=sys.stderr)
"""

FORMALIZE_STUB = r"""
import json, os, sys, time
run_dir = sys.argv[1]
mode = os.environ.get("STUB_MODE", "ok")
env = {k: v for k, v in os.environ.items() if k.startswith("TFL_FORMALIZ")}
env["argv"] = sys.argv[2:]
json.dump(env, open(os.path.join(run_dir, "formalize_env.json"), "w"))
print("formalizing...", file=sys.stderr, flush=True)
if mode == "sleep":
    time.sleep(60)
if mode == "fail":
    print("lean exploded", file=sys.stderr)
    sys.exit(3)
with open(os.path.join(run_dir, "progress.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": "t", "event": "formalization", "node": "formalizer",
                        "payload": {"status": "proved"},
                        "usage": {"calls": 5, "input_tokens": 1, "output_tokens": 1,
                                  "estimated_cost_usd": 1.5}}) + "\n")
"""


def stub_pipeline(monkeypatch, code=PIPELINE_STUB):
    monkeypatch.setattr(
        srv, "build_command",
        lambda project, ir_path, run_dir, live, verbose: [sys.executable, "-c", code, str(run_dir)])


FORMALIZE_CALLS = []


def stub_formalize(monkeypatch, code=FORMALIZE_STUB):
    FORMALIZE_CALLS.clear()
    monkeypatch.setattr(
        srv, "build_formalize_command",
        lambda project, run_dir, settings=None, force=False: (
            FORMALIZE_CALLS.append((project, dict(settings or {}), force))
            or [sys.executable, "-c", code, str(run_dir), "--live"]))


def wait_status(run_id, wanted, timeout=8.0):
    deadline = time.time() + timeout
    data = srv.api_log(run_id)
    while time.time() < deadline and data["status"] not in wanted:
        time.sleep(0.03)
        data = srv.api_log(run_id)
    assert data["status"] in wanted, f"{run_id}: {data['status']!r} (wanted {wanted}): {data.get('error')}"
    return data


def start_run(project="cfl_system"):
    return srv.api_run({"project": project, "ir": {"task": "x"}})["run_id"]


def read_record(run_id):
    return json.loads((srv.RUNS_DIR / run_id / "run.json").read_text(encoding="utf-8"))


@pytest.fixture
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def http_req(port, method, path, payload=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    body = None
    if payload is not None:
        body = json.dumps(payload)
        hdrs.update(JSON_HDR)
    hdrs.update(headers or {})
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except ValueError:
        return resp.status, data


# ---------------------------------------------------------------------------
# Progress + persistence of a run
# ---------------------------------------------------------------------------

def test_run_writes_record_progress_and_cost(monkeypatch):
    stub_pipeline(monkeypatch)
    run_id = start_run()
    data = wait_status(run_id, {"completed", "error"})
    assert data["status"] == "completed", data
    assert data["verdict"] == "regular" and data["confidence"] == 0.9
    assert data["result_html_url"] == f"/runs/{run_id}/input_result.html"
    assert data["result_json_url"].endswith("input_result.json")
    assert data["cost"]["estimated_cost_usd"] == 0.5 and data["cost"]["calls"] == 3
    assert any("stub pipeline done" in line for line in data["lines"])

    rec = read_record(run_id)
    assert rec["status"] == "completed" and rec["project"] == "cfl_system"
    assert rec["verdict"] == "regular" and rec["error"] is None
    assert rec["started"] and rec["finished"] and rec["finished"] >= rec["started"]
    assert rec["pid"] is None and rec["server_pid"] == os.getpid()
    assert rec["urls"]["html"] == f"/runs/{run_id}/input_result.html"
    assert rec["cost"]["estimated_cost_usd"] == 0.5
    assert "stub pipeline done" in (srv.RUNS_DIR / run_id / "run.log").read_text(encoding="utf-8")


def test_running_record_carries_pid_and_running_cost(monkeypatch):
    slow = PIPELINE_STUB.replace('print("stub pipeline done"', 'time.sleep(60); print("x"')
    stub_pipeline(monkeypatch, slow)
    run_id = start_run()
    try:
        wait_status(run_id, {"running"})
        deadline = time.time() + 8
        while time.time() < deadline and not (srv.RUNS_DIR / run_id / "progress.jsonl").exists():
            time.sleep(0.05)
        deadline = time.time() + 8
        rec = read_record(run_id)
        while time.time() < deadline and not rec.get("pid"):
            time.sleep(0.05)
            rec = read_record(run_id)
        assert rec["status"] == "running" and isinstance(rec["pid"], int)
        assert srv._pid_alive(rec["pid"])
        # running total from progress.jsonl while the run is still going
        deadline = time.time() + 8
        listed = srv.api_runs()["runs"][0]
        while time.time() < deadline and not listed["cost"]:
            time.sleep(0.05)
            listed = srv.api_runs()["runs"][0]
        assert listed["status"] == "running" and listed["cost"]["estimated_cost_usd"] == 0.5
        assert listed["elapsed"] is not None
    finally:
        srv.api_cancel(run_id)
        wait_status(run_id, {"cancelled"})


def test_cancelled_run_is_persisted(monkeypatch):
    monkeypatch.setattr(srv, "build_command",
                        lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(60)"])
    run_id = start_run()
    wait_status(run_id, {"running"})
    srv.api_cancel(run_id)
    wait_status(run_id, {"cancelled"})
    rec = read_record(run_id)
    assert rec["status"] == "cancelled" and rec["error"] == "cancelled by user" and rec["finished"]


# ---------------------------------------------------------------------------
# GET /api/runs, GET /api/runs/<id> with cursor
# ---------------------------------------------------------------------------

def test_runs_list_and_detail_with_cursor(port, monkeypatch):
    stub_pipeline(monkeypatch)
    first = start_run()
    wait_status(first, {"completed"})
    second = start_run("dcfl_system")
    wait_status(second, {"completed"})

    status, body = http_req(port, "GET", "/api/runs")
    assert status == 200
    ids = [r["run_id"] for r in body["runs"]]
    assert ids == [second, first]  # newest first
    assert body["runs"][0]["project"] == "dcfl_system" and body["runs"][0]["status"] == "completed"

    status, d = http_req(port, "GET", f"/api/runs/{first}")
    assert status == 200
    assert d["run"]["run_id"] == first and d["run"]["verdict"] == "regular"
    assert [e["event"] for e in d["events"]] == ["node_start", "node_done", "llm_call"]
    assert d["next"] == 3 and d["total"] == 3
    assert d["partial_result"] == {"status": "running", "last_node": "n1"}
    assert d["usage"]["estimated_cost_usd"] == 0.5

    status, d2 = http_req(port, "GET", f"/api/runs/{first}?after=2")
    assert [e["event"] for e in d2["events"]] == ["llm_call"] and d2["next"] == 3
    status, d3 = http_req(port, "GET", f"/api/runs/{first}?after=3")
    assert d3["events"] == [] and d3["next"] == 3
    status, d4 = http_req(port, "GET", f"/api/runs/{first}?after=0&limit=2")
    assert len(d4["events"]) == 2 and d4["next"] == 2

    assert http_req(port, "GET", "/api/runs/" + "0" * 12)[0] == 404
    assert http_req(port, "GET", "/api/runs/not-an-id")[0] == 404
    assert http_req(port, "GET", f"/api/runs/{first}?after=abc")[0] == 400


def test_progress_reader_ignores_partial_and_corrupt_lines(tmp_path):
    f = tmp_path / "progress.jsonl"
    f.write_bytes(b'{"event": "a"}\nnot json\n{"event": "b"}\n{"event": "c"')  # last: no newline yet
    out = srv._read_progress(tmp_path, 0)
    assert [e["event"] for e in out["events"]] == ["a", "b"]
    assert out["next"] == 3 and out["total"] == 3
    with open(f, "ab") as fh:
        fh.write(b'}\n')  # the writer finishes the line
    out2 = srv._read_progress(tmp_path, out["next"])
    assert [e["event"] for e in out2["events"]] == ["c"] and out2["next"] == 4
    assert srv._read_progress(tmp_path / "missing", 5) == {"events": [], "next": 5, "total": 0}


def test_usage_read_from_tail_only(tmp_path):
    f = tmp_path / "progress.jsonl"
    filler = json.dumps({"event": "x", "payload": {"blob": "y" * 5000}, "usage": {"calls": 1}}) + "\n"
    last = json.dumps({"event": "done", "usage": {"calls": 9, "estimated_cost_usd": 2.0}}) + "\n"
    f.write_text(filler * 100 + last + '{"event": "half"', encoding="utf-8")
    assert srv._read_usage(tmp_path)["calls"] == 9
    assert srv._read_usage(tmp_path / "nothing") is None


# ---------------------------------------------------------------------------
# Restore on start
# ---------------------------------------------------------------------------

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _write_record(run_id, **fields):
    run_dir = srv.RUNS_DIR / run_id
    run_dir.mkdir(exist_ok=True)
    rec = {"run_id": run_id, "status": "completed", "project": "cfl_system", "started": 1000.0,
           "finished": 1010.0, "pid": None, "server_pid": _dead_pid(), "verdict": None,
           "urls": {}, "error": None, "cost": None}
    rec.update(fields)
    (run_dir / "run.json").write_text(json.dumps(rec), encoding="utf-8")
    return run_dir


def test_restore_marks_dead_active_runs_interrupted():
    dead = _dead_pid()
    _write_record("a" * 12, status="running", pid=dead)
    _write_record("b" * 12, status="queued")
    d = _write_record("c" * 12, status="running", pid=dead)
    (d / "input_result.json").write_text('{"verdict": "cfl", "confidence": 0.8}', encoding="utf-8")
    _write_record("d" * 12, status="completed", verdict="dcfl",
                  urls={"html": "/runs/dddddddddddd/input_result.html", "json": None, "md": None},
                  cost={"calls": 2, "estimated_cost_usd": 0.1})
    (srv.RUNS_DIR / ("d" * 12) / "run.log").write_text("line1\nline2\n", encoding="utf-8")
    e = _write_record("e" * 12, status="formalizing", pid=dead)
    (e / "input_result.json").write_text('{"verdict": "cfl"}', encoding="utf-8")

    assert srv.restore_runs() == []

    assert srv.api_log("a" * 12)["status"] == "interrupted"
    assert "in progress" in srv.api_log("a" * 12)["error"]
    assert srv.api_log("b" * 12)["status"] == "interrupted"
    assert srv.api_log("c" * 12)["status"] == "completed"      # result was already written
    assert srv.api_log("c" * 12)["verdict"] == "cfl"
    done = srv.api_log("d" * 12)
    assert done["status"] == "completed" and done["verdict"] == "dcfl"
    assert done["lines"] == ["line1", "line2"] and done["cost"]["estimated_cost_usd"] == 0.1
    assert done["result_html_url"].endswith("input_result.html")
    fz = srv.api_log("e" * 12)
    assert fz["status"] == "completed" and "in progress" in fz["formalize_error"]
    # persisted, not just in memory
    assert read_record("a" * 12)["status"] == "interrupted"
    assert read_record("e" * 12)["status"] == "completed"
    assert [r["run_id"] for r in srv.api_runs()["runs"]][:1]  # listing works after restore


def test_restore_reports_live_runs_and_leaves_them_alone():
    # The "other live server" is our own long-lived child (os.getppid() may be 0/1 or
    # invisible in containers and under xdist).
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        _write_record("a" * 12, status="running", pid=os.getpid())   # a live process
        _write_record("b" * 12, status="queued", server_pid=other.pid,
                      server_identity=srv.process_identity(other.pid))  # owned by a live other server
        live = srv.restore_runs()
        assert sorted(live) == ["a" * 12, "b" * 12]
        assert srv.api_log("a" * 12)["status"] == "running"
        assert read_record("a" * 12)["status"] == "running"  # untouched on disk
    finally:
        other.kill()
        other.wait()


def test_restore_loads_legacy_dirs_without_run_json():
    run_dir = srv.RUNS_DIR / ("f" * 12)
    run_dir.mkdir()
    (run_dir / "input_result.json").write_text('{"verdict": "regular"}', encoding="utf-8")
    (srv.RUNS_DIR / ("1" * 12)).mkdir()
    (srv.RUNS_DIR / "not-a-run").mkdir()
    assert srv.restore_runs() == []
    assert srv.api_log("f" * 12)["status"] == "completed" and srv.api_log("f" * 12)["verdict"] == "regular"
    assert srv.api_log("1" * 12)["status"] == "interrupted"
    assert not (run_dir / "run.json").exists()


def test_pid_alive_helper():
    assert srv._pid_alive(os.getpid())
    assert not srv._pid_alive(_dead_pid())
    assert not srv._pid_alive(None) and not srv._pid_alive(0) and not srv._pid_alive(-5)


class _FakeHttpd:
    closed = False

    def __init__(self, *a, **k):
        pass

    def serve_forever(self):
        raise KeyboardInterrupt

    def shutdown(self):
        pass

    def server_close(self):
        _FakeHttpd.closed = True


def test_main_refuses_to_start_over_live_runs(monkeypatch, capsys):
    _write_record("a" * 12, status="running", pid=os.getpid())
    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeHttpd)
    monkeypatch.setattr(sys, "argv", ["ui_server", "--port", "0"])
    with pytest.raises(SystemExit) as exc:
        srv.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "REFUSING TO START" in err and "a" * 12 in err and "--force" in err
    assert read_record("a" * 12)["status"] == "running"


def test_main_force_adopts_live_runs(monkeypatch, capsys):
    _write_record("a" * 12, status="running", pid=os.getpid())
    adopted = []
    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeHttpd)
    monkeypatch.setattr(srv, "adopt_orphans", adopted.extend)
    monkeypatch.setattr(sys, "argv", ["ui_server", "--port", "0", "--force"])
    srv.main()  # serve_forever raises KeyboardInterrupt → clean shutdown
    assert adopted == ["a" * 12]
    assert "adopted" in capsys.readouterr().err


def test_main_starts_normally_and_restores(monkeypatch):
    _write_record("a" * 12, status="running", pid=_dead_pid())
    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeHttpd)
    monkeypatch.setattr(sys, "argv", ["ui_server", "--port", "0"])
    srv.main()
    assert srv.api_log("a" * 12)["status"] == "interrupted"


def test_adopted_run_is_settled_when_process_ends(monkeypatch):
    monkeypatch.setattr(srv, "_ORPHAN_POLL_SECONDS", 0.05)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        run_dir = _write_record("a" * 12, status="running", pid=proc.pid)
        assert srv.restore_runs() == ["a" * 12]
        srv.adopt_orphans(["a" * 12])
        (run_dir / "input_result.json").write_text('{"verdict": "cfl"}', encoding="utf-8")
        srv.api_cancel("a" * 12)  # kills by pid, watcher settles it
        proc.wait(timeout=10)
        data = wait_status("a" * 12, {"cancelled", "completed", "interrupted"})
        assert data["status"] == "cancelled"
    finally:
        proc.kill()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_defaults_and_roundtrip(port):
    status, d = http_req(port, "GET", "/api/settings")
    assert status == 200
    assert d["settings"] == {"formalize_in_run": False, "first_model": "claude-opus-5-5",
                             "retry_model": "claude-sonnet-5-5", "retries": 2, "max_tokens": 128000}
    status, d = http_req(port, "PUT", "/api/settings", {"formalize_in_run": True, "retries": 4,
                                                        "first_model": "claude-haiku-4-5"})
    assert status == 200 and d["settings"]["formalize_in_run"] is True
    assert d["settings"]["retries"] == 4 and d["settings"]["retry_model"] == "claude-sonnet-5-5"
    stored = json.loads(srv.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert stored == d["settings"]
    assert http_req(port, "GET", "/api/settings")[1]["settings"] == stored


@pytest.mark.parametrize("payload", [
    {"retries": 11}, {"retries": -1}, {"retries": True}, {"retries": "2"}, {"retries": 1.5},
    {"max_tokens": 128001}, {"max_tokens": 10}, {"max_tokens": "big"},
    {"formalize_in_run": "yes"}, {"formalize_in_run": 1},
    {"first_model": ""}, {"first_model": "bad model"}, {"retry_model": 5}, {"first_model": "x" * 200},
    {"nonsense": 1},
])
def test_settings_validation_rejects(port, payload):
    status, d = http_req(port, "PUT", "/api/settings", payload)
    assert status == 400 and "error" in d
    assert not srv.SETTINGS_PATH.exists()  # nothing stored


def test_settings_put_requires_json_guards(port):
    status, _ = http_req(port, "PUT", "/api/settings", None,
                         {"Content-Type": "text/plain"})
    assert status == 403
    status, _ = http_req(port, "PUT", "/api/settings", {"retries": 1},
                         {"Origin": "https://evil.example"})
    assert status == 403
    assert http_req(port, "PUT", "/api/nothing", {})[0] == 404


def test_corrupt_or_invalid_settings_file_falls_back_to_defaults():
    srv.SETTINGS_PATH.write_text("{not json", encoding="utf-8")
    assert srv.load_settings() == srv.DEFAULT_SETTINGS
    srv.SETTINGS_PATH.write_text(json.dumps({"retries": 99, "max_tokens": 5000, "junk": 1}), encoding="utf-8")
    s = srv.load_settings()
    assert s["retries"] == 2 and s["max_tokens"] == 5000  # bad value dropped, good one kept


def test_settings_json_is_git_ignored_with_example():
    root = srv.ROOT
    assert "ui_server/settings.json" in (root / ".gitignore").read_text(encoding="utf-8")
    example = json.loads((root / "ui_server" / "settings.example.json").read_text(encoding="utf-8"))
    assert srv._validate_settings(example, srv.DEFAULT_SETTINGS) == example


def test_pipeline_subprocess_never_formalizes_in_graph(monkeypatch):
    """The Lean step is the separate entry, never the in-graph one: the
    pipeline subprocess always gets TFL_FORMALIZATION=0 (an inherited 1 must not
    win) and no dead TFL_FORMALIZE_* variables."""
    stub_pipeline(monkeypatch)
    monkeypatch.setenv("TFL_FORMALIZATION", "1")
    for on in (False, True):
        srv.api_put_settings({"formalize_in_run": on, "retries": 3, "max_tokens": 64000})
        run_id = start_run()
        wait_status(run_id, {"completed"})
        env = json.loads((srv.RUNS_DIR / run_id / "env_dump.json").read_text(encoding="utf-8"))
        assert env == {"TFL_FORMALIZATION": "0"}


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

def _recorded_live_run(project, cost, calls=4, *, formalization_cost=None, live=True,
                       status="completed"):
    """A finished run on disk + in the registry: progress.jsonl with a pipeline
    "done" event and, optionally, a later formalization that adds to the total."""
    run_id = os.urandom(6).hex()
    run_dir = srv.RUNS_DIR / run_id
    run_dir.mkdir()
    lines = [{"event": "node_done", "usage": {"calls": 1, "estimated_cost_usd": cost / 2}},
             {"event": "done", "usage": {"calls": calls, "estimated_cost_usd": cost}}]
    if formalization_cost is not None:
        lines.append({"event": "done", "usage": {"calls": calls + 3,
                                                 "estimated_cost_usd": cost + formalization_cost}})
    (run_dir / "progress.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    entry = srv._new_run_entry(run_id, project, run_dir, live)
    entry["status"] = status
    srv._runs[run_id] = entry
    return run_id


def test_estimate_without_recorded_runs_is_no_data_never_invented(port):
    for project in ("agent_system", "cfl_system", "dcfl_system", "ll_system"):
        status, d = http_req(port, "GET", f"/api/estimate?project={project}&formalize=0")
        assert status == 200
        assert d["run"] == {"n": 0, "estimated_cost_usd": None, "low_usd": None, "high_usd": None,
                            "note": srv.ESTIMATE_NO_DATA_NOTE}
        assert "estimate_usd" not in d and "formalize" not in d


def test_estimate_is_measured_from_recorded_live_runs(port):
    _recorded_live_run("cfl_system", 1.0)
    _recorded_live_run("cfl_system", 2.0, formalization_cost=5.0)   # formalization is not the run
    _recorded_live_run("cfl_system", 9.0, live=False)                # mock runs do not count
    _recorded_live_run("cfl_system", 9.0, status="error")            # nor do failed ones
    _recorded_live_run("dcfl_system", 0.5)
    d = http_req(port, "GET", "/api/estimate?project=cfl_system")[1]
    assert d["run"]["n"] == 2 and d["run"]["estimated_cost_usd"] == 1.5
    assert d["run"]["low_usd"] == 1.0 and d["run"]["high_usd"] == 2.0
    assert d["run"]["note"].startswith(srv.ESTIMATE_NOTE)
    assert http_req(port, "GET", "/api/estimate?project=dcfl_system")[1]["run"]["estimated_cost_usd"] == 0.5
    assert http_req(port, "GET", "/api/estimate?project=ll_system")[1]["run"]["n"] == 0


def test_estimate_formalize_flag_and_validation(port):
    srv.api_put_settings({"formalize_in_run": True})
    d = http_req(port, "GET", "/api/estimate?project=cfl_system")[1]
    assert d["with_formalize"] is True and "formalize_note" in d
    assert http_req(port, "GET", "/api/estimate?project=cfl_system&formalize=0")[1]["with_formalize"] is False
    assert http_req(port, "GET", "/api/estimate?project=ll_system")[1]["with_formalize"] is False  # no Lean step
    assert http_req(port, "GET", "/api/estimate?project=nope")[0] == 400
    assert "run" not in http_req(port, "GET", "/api/estimate")[1]


ESTIMATE_STUB = r"""
import json, sys
print(json.dumps({"formalizable": True, "min_usd": 0.5, "expected_usd": 1.25, "max_usd": 6.4,
                  "calls_max": 4, "assumptions": "stub", "argv": sys.argv[2:]}))
"""


def test_formalize_estimate_endpoint_uses_the_cli_with_current_settings(port, monkeypatch):
    seen = []

    def fake(project, run_dir, settings):
        seen.append((project, dict(settings)))
        return [sys.executable, "-c", ESTIMATE_STUB, str(run_dir), "--estimate"]

    monkeypatch.setattr(srv, "build_formalize_estimate_command", fake)
    srv.api_put_settings({"retries": 1, "max_tokens": 50000})
    run_id = _completed_run(monkeypatch)
    status, d = http_req(port, "GET", f"/api/runs/{run_id}/formalize/estimate")
    assert status == 200 and d["formalizable"] is True
    assert (d["min_usd"], d["expected_usd"], d["max_usd"]) == (0.5, 1.25, 6.4)
    assert (d["low_usd"], d["estimated_cost_usd"], d["high_usd"]) == (0.5, 1.25, 6.4)
    assert seen[0][0] == "cfl_system" and seen[0][1]["retries"] == 1 and seen[0][1]["max_tokens"] == 50000
    assert d["settings"]["retries"] == 1
    assert http_req(port, "GET", f"/api/runs/{'0' * 12}/formalize/estimate")[0] == 404
    ll = start_run("ll_system")
    wait_status(ll, {"completed"})
    assert http_req(port, "GET", f"/api/runs/{ll}/formalize/estimate")[0] == 400


def test_build_formalize_estimate_command_shape():
    cmd = srv.build_formalize_estimate_command("agent_system", Path("/x/run"), srv.DEFAULT_SETTINGS)
    assert cmd[:5] == [sys.executable, "-m", "agent_system.formalize", str(Path("/x/run")), "--estimate"]
    assert "--live" not in cmd and "--force" not in cmd
    assert cmd[5:] == ["--first-model", "claude-opus-5-5", "--retry-model", "claude-sonnet-5-5",
                       "--retries", "2", "--max-tokens", "128000"]


# ---------------------------------------------------------------------------
# Formalize
# ---------------------------------------------------------------------------

def _completed_run(monkeypatch, project="cfl_system"):
    stub_pipeline(monkeypatch)
    run_id = start_run(project)
    wait_status(run_id, {"completed"})
    return run_id


def test_formalize_requires_confirmation_and_completed_run(port, monkeypatch):
    stub_formalize(monkeypatch)
    run_id = _completed_run(monkeypatch)
    status, d = http_req(port, "POST", f"/api/runs/{run_id}/formalize", {})
    assert status == 400 and "confirm_spend" in d["error"]
    assert http_req(port, "POST", f"/api/runs/{'0' * 12}/formalize", {"confirm_spend": True})[0] == 404
    assert http_req(port, "POST", f"/api/runs/{run_id}/formalize", None,
                    {"Content-Type": "text/plain"})[0] == 403

    # ll_system has no formalizer
    ll = start_run("ll_system")
    wait_status(ll, {"completed"})
    status, d = http_req(port, "POST", f"/api/runs/{ll}/formalize", {"confirm_spend": True})
    assert status == 400 and "not available" in d["error"]

    # a run that did not complete cannot be formalized
    monkeypatch.setattr(srv, "build_command",
                        lambda *a, **k: [sys.executable, "-c", "import sys; sys.exit(1)"])
    bad = start_run()
    wait_status(bad, {"error"})
    status, d = http_req(port, "POST", f"/api/runs/{bad}/formalize", {"confirm_spend": True})
    assert status == 409


def test_formalize_runs_subprocess_and_returns_to_completed(port, monkeypatch):
    stub_formalize(monkeypatch)
    srv.api_put_settings({"retries": 5, "max_tokens": 100000, "first_model": "claude-opus-5-5",
                          "retry_model": "claude-sonnet-5-5"})
    run_id = _completed_run(monkeypatch)
    before = srv.api_log(run_id)["cost"]["estimated_cost_usd"]

    status, d = http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})
    assert status == 200 and d["status"] == "formalizing"
    data = wait_status(run_id, {"completed"})
    assert data["formalize_error"] is None
    assert data["cost"]["estimated_cost_usd"] == 1.5 and before == 0.5   # running total moved on
    assert "--- formalize ---" in data["lines"] and "formalizing..." in data["lines"]

    env = json.loads((srv.RUNS_DIR / run_id / "formalize_env.json").read_text(encoding="utf-8"))
    assert env["argv"] == ["--live"]
    # the CLI flags come from the settings; the request is not forced
    assert FORMALIZE_CALLS == [("cfl_system", {"formalize_in_run": False, "first_model": "claude-opus-5-5",
                                                "retry_model": "claude-sonnet-5-5", "retries": 5,
                                                "max_tokens": 100000}, False)]
    assert read_record(run_id)["status"] == "completed"
    _, detail = http_req(port, "GET", f"/api/runs/{run_id}")
    assert detail["events"][-1]["event"] == "formalization"


def test_second_formalize_click_over_a_proved_block_is_not_forced(port, monkeypatch):
    """The stub's event says "proved": asking again is refused (409, no spend,
    no subprocess) unless the client explicitly sends force:true; a block that is
    not proved is simply re-run, still without --force."""
    stub_formalize(monkeypatch)
    run_id = _completed_run(monkeypatch)
    assert http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})[0] == 200
    wait_status(run_id, {"completed"})
    assert len(FORMALIZE_CALLS) == 1 and FORMALIZE_CALLS[-1][2] is False

    status, d = http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})
    assert status == 409 and "already" in d["error"] and "force" in d["error"]
    assert len(FORMALIZE_CALLS) == 1 and srv.api_log(run_id)["status"] == "completed"

    status, _ = http_req(port, "POST", f"/api/runs/{run_id}/formalize",
                         {"confirm_spend": True, "force": True})
    assert status == 200
    wait_status(run_id, {"completed"})
    assert len(FORMALIZE_CALLS) == 2 and FORMALIZE_CALLS[-1][2] is True


def test_formalize_after_a_failed_block_is_rerun_without_force(port, monkeypatch):
    stub_formalize(monkeypatch)
    run_id = _completed_run(monkeypatch)
    failed = {"ts": "t", "event": "formalization", "node": "formalize",
              "payload": {"status": "has_sorry", "proved": False},
              "usage": {"calls": 3, "estimated_cost_usd": 0.7}}
    with open(srv.RUNS_DIR / run_id / "progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(failed) + "\n")
    assert http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})[0] == 200
    wait_status(run_id, {"completed"})
    assert [c[2] for c in FORMALIZE_CALLS] == [False]


def _wait_formalize_env(run_id, timeout=8.0):
    deadline = time.time() + timeout
    path = srv.RUNS_DIR / run_id / "formalize_env.json"
    while time.time() < deadline and not path.exists():
        time.sleep(0.03)
    assert path.exists()


def _run_live(monkeypatch, project="cfl_system", live=True):
    stub_pipeline(monkeypatch)
    return srv.api_run({"project": project, "ir": {"task": "x"}, "live": live})["run_id"]


def test_in_run_setting_chains_the_separate_formalize_after_the_result(monkeypatch):
    """formalize_in_run=on: the run goes running -> formalizing (never through a
    'completed' flicker), the pipeline itself is started with TFL_FORMALIZATION=0
    and the settings (models / corrections / max_tokens) reach `<system>.formalize`
    as flags."""
    stub_formalize(monkeypatch)
    srv.api_put_settings({"formalize_in_run": True, "retries": 4, "max_tokens": 90000})
    run_id = _run_live(monkeypatch)
    _wait_formalize_env(run_id)
    data = wait_status(run_id, {"completed"})
    assert data["formalize_error"] is None and data["verdict"] == "regular"
    assert data["cost"]["estimated_cost_usd"] == 1.5           # formalization cost is in the total
    assert data["result_json_url"]
    assert FORMALIZE_CALLS == [("cfl_system", {"formalize_in_run": True, "first_model": "claude-opus-5-5",
                                                "retry_model": "claude-sonnet-5-5", "retries": 4,
                                                "max_tokens": 90000}, False)]
    env = json.loads((srv.RUNS_DIR / run_id / "env_dump.json").read_text(encoding="utf-8"))
    assert env == {"TFL_FORMALIZATION": "0"}
    assert "--- formalize (in-run setting) ---" in data["lines"]
    assert read_record(run_id)["status"] == "completed"


def test_in_run_setting_is_off_by_default_and_never_for_mock_or_ll(monkeypatch):
    stub_formalize(monkeypatch)
    rid = _run_live(monkeypatch)                              # setting off (default)
    wait_status(rid, {"completed"})
    srv.api_put_settings({"formalize_in_run": True})
    mock = _run_live(monkeypatch, live=False)                 # a mock run makes no live calls
    ll = _run_live(monkeypatch, "ll_system")                  # no Lean step for LL
    data = wait_status(mock, {"completed"})
    wait_status(ll, {"completed"})
    assert FORMALIZE_CALLS == []
    assert any("skipped" in line for line in data["lines"])


@pytest.mark.parametrize("project", ["agent_system", "cfl_system", "dcfl_system"])
def test_completed_mock_run_includes_skip_log_before_releasing_slot(monkeypatch, project):
    run_id = "final_skip_log"
    run_dir = srv.RUNS_DIR / run_id
    run_dir.mkdir()
    ir_path = run_dir / "input.json"
    ir_path.write_text("{}", encoding="utf-8")
    settings = {**srv.DEFAULT_SETTINGS, "formalize_in_run": True}
    with srv._runs_lock:
        srv._runs[run_id] = srv._new_run_entry(run_id, project, run_dir, live=False)

    def finish_pipeline(*_args):
        (run_dir / "input_result.json").write_text(
            json.dumps({"verdict": "regular", "confidence": 0.9}), encoding="utf-8")
        return 0, "finished", []

    release_entered = threading.Event()
    resume_release = threading.Event()
    release_slot = srv._release_run_slot

    def paused_release():
        release_entered.set()
        resume_release.wait(timeout=5)
        release_slot()

    monkeypatch.setattr(srv, "_spawn_and_wait", finish_pipeline)
    monkeypatch.setattr(srv, "_release_run_slot", paused_release)
    worker = threading.Thread(
        target=srv._run_pipeline_worker,
        args=(run_id, project, ir_path, run_dir, False, False, settings), daemon=True,
    )
    worker.start()
    try:
        assert release_entered.wait(timeout=5), "worker did not reach slot release"
        data = srv.api_log(run_id)
        assert data["status"] == read_record(run_id)["status"] == "completed"
        skip_line = "in-run formalization skipped: a mock run makes no live API calls"
        assert data["lines"].count(skip_line) == 1
        assert (run_dir / "run.log").read_text(encoding="utf-8").splitlines() == [skip_line]
    finally:
        resume_release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert srv.api_log(run_id)["lines"] == data["lines"]


def test_in_run_formalize_can_be_cancelled(monkeypatch):
    stub_formalize(monkeypatch)
    monkeypatch.setenv("STUB_MODE", "sleep")
    srv.api_put_settings({"formalize_in_run": True})
    run_id = _run_live(monkeypatch)
    try:
        _wait_formalize_env(run_id)
        assert srv.api_log(run_id)["status"] == "formalizing"
        srv.api_cancel(run_id)
        data = wait_status(run_id, {"completed"})
        assert data["verdict"] == "regular" and "formalization cancelled" in data["formalize_error"]
    finally:
        srv.api_cancel(run_id)


def test_formalize_status_is_formalizing_meanwhile(monkeypatch):
    stub_formalize(monkeypatch)
    monkeypatch.setenv("STUB_MODE", "sleep")
    run_id = _completed_run(monkeypatch)
    srv.api_formalize(run_id, {"confirm_spend": True})
    try:
        assert srv.api_log(run_id)["status"] == "formalizing"
        assert read_record(run_id)["status"] == "formalizing"
        with pytest.raises(srv.ConflictError):   # not twice at once
            srv.api_formalize(run_id, {"confirm_spend": True})
        deadline = time.time() + 8
        while time.time() < deadline and not (srv.RUNS_DIR / run_id / "formalize_env.json").exists():
            time.sleep(0.05)
        srv.api_cancel(run_id)
        data = wait_status(run_id, {"completed"})
        # the underlying result is still valid; only the formalization is marked
        assert data["verdict"] == "regular"
        assert "formalization cancelled" in data["formalize_error"]
    finally:
        srv.api_cancel(run_id)


def test_formalize_failure_keeps_run_completed(monkeypatch):
    stub_formalize(monkeypatch)
    monkeypatch.setenv("STUB_MODE", "fail")
    run_id = _completed_run(monkeypatch)
    srv.api_formalize(run_id, {"confirm_spend": True})
    deadline = time.time() + 8
    data = srv.api_log(run_id)
    while time.time() < deadline and data["formalize_error"] is None:
        time.sleep(0.05)
        data = srv.api_log(run_id)
    assert data["status"] == "completed" and data["verdict"] == "regular"
    assert "exit code 3" in data["formalize_error"] and "lean exploded" in data["formalize_error"]
    assert read_record(run_id)["formalize_error"].startswith("formalize exit code 3")


def test_build_formalize_command_shape():
    run_dir = Path("/x/run")
    cmd = srv.build_formalize_command("dcfl_system", run_dir)
    assert cmd == [sys.executable, "-m", "dcfl_system.formalize", str(run_dir), "--live"]
    full = srv.build_formalize_command("dcfl_system", run_dir, srv.DEFAULT_SETTINGS, force=True)
    assert full[5:] == ["--first-model", "claude-opus-5-5", "--retry-model", "claude-sonnet-5-5",
                        "--retries", "2", "--max-tokens", "128000", "--force"]
    assert srv.build_formalize_command("agent_system", run_dir)[2] == "agent_system.formalize"
    with pytest.raises(ValueError):
        srv.build_formalize_command("ll_system", run_dir)


def test_projects_listing_flags_formalize_support(port):
    _, d = http_req(port, "GET", "/api/projects")
    flags = {p["id"]: p["formalize"] for p in d["projects"]}
    assert flags == {"agent_system": True, "cfl_system": True, "dcfl_system": True, "ll_system": False}


# ---------------------------------------------------------------------------
# pid identity (recycled pids are neither trusted nor killed)
# ---------------------------------------------------------------------------

def test_process_identity_of_self_and_dead():
    ident = srv.process_identity(os.getpid())
    assert ident and ident["image"] and abs(ident["created"] - time.time()) < 10 ** 9
    # A short-lived child: record its identity while it is alive, then let it
    # exit. Its pid may be recycled by an unrelated process under parallel load,
    # so "dead" is asserted by identity (creation time), not by the pid being free.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        child_ident = None
        for _ in range(100):
            child_ident = srv.process_identity(child.pid)
            if child_ident:
                break
            time.sleep(0.05)
        assert child_ident and child_ident["image"]
        assert srv._pid_matches(child.pid, child_ident)
    finally:
        child.kill()
        child.wait()
        # On Windows the Popen handle keeps the exited process object (and its
        # creation time) queryable; release it so the pid is truly free.
        handle = getattr(child, "_handle", None)
        if handle is not None and hasattr(handle, "Close"):
            handle.Close()
    # The recorded process must no longer count as "our live process": either
    # the pid is dead (even if some inherited handle keeps the exited process
    # object queryable) or it was recycled by another process, whose creation
    # time differs. PID reuse therefore cannot make this flaky.
    assert not (srv._pid_alive(child.pid) and srv._pid_matches(child.pid, child_ident))
    after = srv.process_identity(child.pid)
    if after is not None and srv._pid_alive(child.pid):
        assert abs(after["created"] - child_ident["created"]) > srv._IDENTITY_TOLERANCE_S
    assert srv._pid_matches(os.getpid(), ident)
    assert srv._pid_matches(os.getpid(), None)          # legacy record: liveness only
    assert not srv._pid_matches(os.getpid(), {"created": ident["created"] - 3600, "image": ident["image"]})
    assert not srv._pid_matches(os.getpid(), {"created": ident["created"], "image": "definitely-not-this.exe"})


def test_restore_recycled_pid_is_interrupted_not_live():
    ident = srv.process_identity(os.getpid())
    stale = {"created": ident["created"] - 5000, "image": ident["image"]}
    _write_record("a" * 12, status="running", pid=os.getpid(), pid_identity=stale)
    _write_record("b" * 12, status="queued", server_pid=os.getppid(),
                  server_identity={"created": 1.0, "image": "x"})
    assert srv.restore_runs() == []
    assert srv.api_log("a" * 12)["status"] == "interrupted"
    assert srv.api_log("b" * 12)["status"] == "interrupted"
    assert read_record("a" * 12)["pid"] is None


def test_restore_matching_identity_is_live():
    _write_record("a" * 12, status="running", pid=os.getpid(),
                  pid_identity=srv.process_identity(os.getpid()))
    assert srv.restore_runs() == ["a" * 12]


def test_kill_pid_refuses_on_identity_mismatch():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ident = srv.process_identity(proc.pid)
        bad = {"created": ident["created"] - 5000, "image": ident["image"]}
        assert srv._kill_pid(proc.pid, bad) is False
        time.sleep(0.3)
        assert proc.poll() is None                      # still alive
        assert srv._kill_pid(proc.pid, ident) is True
        proc.wait(timeout=15)
    finally:
        proc.kill()


def test_cancel_orphan_with_recycled_pid_does_not_kill(monkeypatch):
    monkeypatch.setattr(srv, "_ORPHAN_POLL_SECONDS", 0.05)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ident = srv.process_identity(proc.pid)
        _write_record("a" * 12, status="running", pid=proc.pid, pid_identity=ident)
        assert srv.restore_runs() == ["a" * 12]
        # the pid gets "recycled": stored identity no longer matches
        with srv._runs_lock:
            srv._runs["a" * 12]["pid_identity"] = {"created": ident["created"] - 5000,
                                                   "image": ident["image"]}
        srv.adopt_orphans(["a" * 12])
        srv.api_cancel("a" * 12)
        wait_status("a" * 12, {"cancelled", "completed", "interrupted"})
        assert proc.poll() is None                      # never killed
    finally:
        proc.kill()


def test_formalize_of_a_mock_run_reports_source_mode_and_warning(port, monkeypatch):
    stub_formalize(monkeypatch)
    run_id = _completed_run(monkeypatch)            # api_run default: live=False -> mock
    assert read_record(run_id)["source_mode"] == "mock"
    assert srv.api_log(run_id)["source_mode"] == "mock"
    status, d = http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})
    assert status == 200 and d["source_mode"] == "mock"
    assert "mock mode" in d["warning"] and "paid" in d["warning"]
    wait_status(run_id, {"completed"})
    # confirmation stays mandatory
    assert http_req(port, "POST", f"/api/runs/{run_id}/formalize", {})[0] == 400


def test_formalize_of_a_live_run_has_no_mock_warning(port, monkeypatch):
    stub_formalize(monkeypatch)
    run_id = _completed_run(monkeypatch)
    with srv._runs_lock:
        srv._runs[run_id]["live"] = True
    status, d = http_req(port, "POST", f"/api/runs/{run_id}/formalize", {"confirm_spend": True})
    assert status == 200 and d["source_mode"] == "live" and "warning" not in d
    wait_status(run_id, {"completed"})
    assert read_record(run_id)["source_mode"] == "live"


FAILED_PIPELINE_STUB = PIPELINE_STUB.replace('{"verdict": "regular", "confidence": 0.9}',
                                             '{"verdict": "failure", "confidence": 0.0}')
NO_VERDICT_PIPELINE_STUB = PIPELINE_STUB.replace('{"verdict": "regular", "confidence": 0.9}',
                                                 '{"confidence": 0.0}')


def test_verdict_failure_result_sets_result_status_and_blocks_formalize(monkeypatch):
    """cfl/dcfl report failure as verdict "failure": treated as a failed result."""
    stub_pipeline(monkeypatch, FAILED_PIPELINE_STUB)
    run_id = start_run("cfl_system")
    data = wait_status(run_id, {"completed"})
    assert srv.api_log(run_id)["status"] == "completed"
    assert srv._runs[run_id]["result_status"] == "failure"
    with pytest.raises(ValueError, match="failure"):
        srv.api_formalize(run_id, {"confirm_spend": True})


@pytest.mark.parametrize("stub", [FAILED_PIPELINE_STUB, NO_VERDICT_PIPELINE_STUB])
def test_in_run_chain_skips_failed_or_verdictless_result(monkeypatch, stub):
    stub_formalize(monkeypatch)
    srv.api_put_settings({"formalize_in_run": True})
    stub_pipeline(monkeypatch, stub)
    run_id = srv.api_run({"project": "cfl_system", "ir": {"task": "x"}, "live": True})["run_id"]
    data = wait_status(run_id, {"completed"})
    assert FORMALIZE_CALLS == []
    assert "--- formalize (in-run setting) ---" not in data["lines"]
