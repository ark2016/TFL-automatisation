"""ProgressWriter contract (agent_system/lib/progress.py) + REG pipeline wiring.

Mock mode only: no API calls. Covers the unit behaviour of the writer
(events, atomic snapshot, cumulative usage from a UsageTracker listener,
append mode, thread safety) and the REG graph/CLI integration.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_system.lib.llm_client import UsageTracker
from agent_system.lib.progress import (
    PARTIAL_FILENAME,
    PROGRESS_FILENAME,
    ProgressWriter,
    extract_verdict,
    instrument_node,
    read_events,
    read_partial,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TASK = "task1_palindrome_prefix_suffix"


def _usage(i=0, o=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


# ---------------------------------------------------------------------------
# writer unit tests
# ---------------------------------------------------------------------------

def test_emit_lines_have_contract_fields(tmp_path):
    pw = ProgressWriter(tmp_path, system="agent_system")
    pw.node_start("a_node")
    pw.node_done("a_node", {"hypothesis": {"hypothesis": "regular", "confidence": 0.5}}, elapsed=0.01)
    events = read_events(tmp_path)
    assert [e["event"] for e in events] == ["node_start", "node_done"]
    for e in events:
        assert set(e) == {"ts", "event", "node", "payload", "usage"}
        assert set(e["usage"]) == {"calls", "input_tokens", "output_tokens", "estimated_cost_usd"}
        assert e["usage"]["estimated_cost_usd"] == 0.0
    assert events[1]["payload"]["summary"]["hypothesis"]["hypothesis"] == "regular"


def test_partial_result_updates_after_each_node_done(tmp_path):
    pw = ProgressWriter(tmp_path, system="s", stem="t_result")
    assert read_partial(tmp_path) is None
    pw.node_done("n1", {"hypothesis": {"h": 1}, "errors": ["e1"], "oracle_fn": lambda x: x})
    snap = read_partial(tmp_path)
    assert snap["status"] == "running" and snap["last_node"] == "n1"
    assert snap["state"]["hypothesis"] == {"h": 1}
    assert "oracle_fn" not in snap["state"]
    pw.node_done("n2", {"errors": ["e2"], "specialist_outputs": [("a", {"x": 1})]})
    snap = read_partial(tmp_path)
    assert snap["nodes_done"] == ["n1", "n2"]
    assert snap["state"]["errors"] == ["e1", "e2"]          # reducer keys accumulate
    assert snap["state"]["specialist_outputs"] == [["a", {"x": 1}]]
    assert not list(tmp_path.glob("*.tmp"))


def test_usage_is_cumulative_and_monotonic_via_tracker_listener(tmp_path):
    tracker = UsageTracker()
    pw = ProgressWriter(tmp_path, tracker=tracker)
    pw.node_start("x")
    tracker.record("claude-haiku-4-5", _usage(1000, 500), agent="a1")
    tracker.record("claude-haiku-4-5", _usage(2000, 100), agent="a2")
    pw.node_done("x", {})
    events = read_events(tmp_path)
    calls = [e for e in events if e["event"] == "llm_call"]
    assert [c["node"] for c in calls] == ["a1", "a2"]
    assert calls[0]["payload"]["phase"] == "done"
    assert calls[0]["payload"]["input_tokens"] == 1000
    assert calls[0]["payload"]["estimated_cost_usd"] > 0
    series = [e["usage"] for e in events]
    for key in ("calls", "input_tokens", "output_tokens", "estimated_cost_usd"):
        vals = [u[key] for u in series]
        assert vals == sorted(vals), key
    assert series[-1]["calls"] == 2 and series[-1]["input_tokens"] == 3000
    assert series[-1]["estimated_cost_usd"] == pytest.approx(
        tracker.as_dict()["estimated_cost_usd"])
    pw.done({"status": "success"})


def test_tracker_listener_errors_never_break_record():
    tracker = UsageTracker()
    tracker.add_listener(lambda call: 1 / 0)
    tracker.record("claude-haiku-4-5", _usage(1, 1), agent="x")
    assert tracker.as_dict()["calls"] == 1


def test_append_mode_continues_usage_and_state(tmp_path):
    t1 = UsageTracker()
    pw1 = ProgressWriter(tmp_path, tracker=t1)
    t1.record("claude-haiku-4-5", _usage(1000, 1000), agent="a")
    pw1.node_done("n", {"hypothesis": {"h": 1}})
    pw1.done({"status": "success", "verdict": "regular"}, files=["x.json"])
    last = read_events(tmp_path)[-1]["usage"]

    t2 = UsageTracker()
    pw2 = ProgressWriter(tmp_path, tracker=t2, append=True)
    t2.record("claude-haiku-4-5", _usage(500, 500), agent="lean")
    pw2.formalization({"status": "proved", "direction": "regular"})
    events = read_events(tmp_path)
    assert events[-1]["event"] == "formalization"
    assert events[-1]["usage"]["calls"] == last["calls"] + 1
    assert events[-1]["usage"]["estimated_cost_usd"] > last["estimated_cost_usd"]
    assert read_partial(tmp_path)["state"]["hypothesis"] == {"h": 1}   # snapshot kept


def test_fresh_writer_truncates_previous_log(tmp_path):
    ProgressWriter(tmp_path).node_start("old")
    ProgressWriter(tmp_path)
    assert read_events(tmp_path) == []


def test_thread_safe_writes(tmp_path):
    pw = ProgressWriter(tmp_path)

    def work(i):
        for j in range(20):
            pw.node_start(f"n{i}")
            pw.node_done(f"n{i}", {"errors": [f"{i}-{j}"]})

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = (tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6 * 20 * 2
    assert all(json.loads(l)["event"] in ("node_start", "node_done") for l in lines)
    assert len(read_partial(tmp_path)["state"]["errors"]) == 120


def test_io_errors_are_swallowed(tmp_path):
    pw = ProgressWriter(tmp_path / "run")
    (tmp_path / "run" / PROGRESS_FILENAME).unlink()
    (tmp_path / "run" / PROGRESS_FILENAME).mkdir()           # not a file any more
    pw.node_start("x")                                       # must not raise
    pw.node_done("x", {})
    pw.error("boom")


def test_instrument_node_passthrough_without_writer():
    calls = []
    fn = instrument_node("n", lambda state: calls.append(1) or {"k": 1})
    assert fn({"a": 1}) == {"k": 1} and calls == [1]


def test_instrument_node_reports_error(tmp_path):
    pw = ProgressWriter(tmp_path)

    def bad(state):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        instrument_node("bad_node", bad)({"progress": pw})
    ev = read_events(tmp_path)
    assert [e["event"] for e in ev] == ["node_start", "error"]
    assert ev[1]["node"] == "bad_node" and "nope" in ev[1]["payload"]["message"]
    assert read_partial(tmp_path)["status"] == "error"


def test_extract_verdict_respects_contradiction_gate():
    r = {"status": "partial", "confidence": 0.3, "verdict_gate": {"contradiction": True},
         "evidence": {"reasoning": {"verdict": "regular"}}}
    assert extract_verdict(r)[0] is None
    r2 = {"status": "success", "evidence": {"reasoning": {"evidence": {"verdict": "non_regular"}}}}
    assert extract_verdict(r2)[0] == "non_regular"
    assert extract_verdict({"verdict": "cfl", "confidence": 0.9}) == ("cfl", 0.9)


# ---------------------------------------------------------------------------
# REG pipeline integration (mock)
# ---------------------------------------------------------------------------

class _MeteredMock:
    """agent_runner double: replays mock outputs but bills a UsageTracker per
    call, like a live runner would."""

    def __init__(self, inner):
        self.inner = inner
        self.usage_tracker = UsageTracker()

    def run_agent(self, agent_name, input_data=None):
        out = self.inner.run_agent(agent_name)
        if out is not None:
            self.usage_tracker.record("claude-haiku-4-5", _usage(1000, 300), agent=agent_name)
        return out


def _ir():
    return json.loads((EXAMPLES / f"{TASK}.json").read_text(encoding="utf-8"))


def test_reg_pipeline_progress_events_and_snapshot(tmp_path):
    from agent_system.orchestrator import MockRunner, Pipeline
    pw = ProgressWriter(tmp_path, system="agent_system")
    result = Pipeline().run_full_pipeline(
        _ir(), mock_runner=MockRunner(EXAMPLES, TASK), progress=pw)
    events = read_events(tmp_path)
    started = {e["node"] for e in events if e["event"] == "node_start"}
    done = {e["node"] for e in events if e["event"] == "node_done"}
    assert started == done
    for node in ("validate_ir_node", "analyze_hypothesis_node", "run_classifier_node",
                 "run_specialist_node", "collect_specialists_node", "build_oracle_node",
                 "oracle_test_node", "run_reasoning_node", "assemble_result_node"):
        assert node in done, node
    assert events[-1]["event"] == "verdict" and events[-1]["payload"]["final"] is True
    snap = read_partial(tmp_path)
    assert snap["last_node"] == "assemble_result_node"
    assert snap["result"]["status"] == result["status"]
    assert "hypothesis" in snap["state"]
    json.dumps(snap)                                     # fully serialisable


def test_reg_partial_result_grows_while_running(tmp_path):
    """partial_result.json is refreshed after every node, not only at the end."""
    from agent_system.orchestrator import MockRunner, Pipeline
    pw = ProgressWriter(tmp_path)
    seen: list[list[str]] = []
    orig = pw.write_partial

    def spy():
        orig()
        seen.append(list(read_partial(tmp_path)["nodes_done"]))

    pw.write_partial = spy
    Pipeline().run_full_pipeline(_ir(), mock_runner=MockRunner(EXAMPLES, TASK), progress=pw)
    lens = [len(s) for s in seen]
    assert lens == sorted(lens) and lens[0] < lens[-1]
    assert seen[0][0] == "validate_ir_node"


def test_reg_llm_events_and_monotonic_usage(tmp_path):
    from agent_system.orchestrator import MockRunner, Pipeline
    runner = _MeteredMock(MockRunner(EXAMPLES, TASK))
    pw = ProgressWriter(tmp_path)
    Pipeline().run_full_pipeline(_ir(), agent_runner=runner, progress=pw)
    events = read_events(tmp_path)
    llm = [e for e in events if e["event"] == "llm_call"]
    assert llm, "live-style agent calls must produce llm_call events"
    phases = {e["payload"]["phase"] for e in llm}
    assert phases == {"start", "done"}
    series = [e["usage"] for e in events]
    for key in ("calls", "input_tokens", "output_tokens", "estimated_cost_usd"):
        vals = [u[key] for u in series]
        assert vals == sorted(vals), key
    assert series[-1]["calls"] == runner.usage_tracker.as_dict()["calls"] > 0


def test_reg_cli_writes_progress_by_default_with_save(tmp_path, monkeypatch):
    from agent_system.orchestrator import main
    monkeypatch.setattr(sys, "argv", ["agent_system", str(EXAMPLES / f"{TASK}.json"),
                                      "--mock", str(EXAMPLES), "--save", str(tmp_path)])
    with pytest.raises(SystemExit):
        main()
    events = read_events(tmp_path)
    assert events[-1]["event"] == "done"
    assert f"{TASK}_result.json" in events[-1]["payload"]["files"]
    assert (tmp_path / f"{TASK}_result.json").exists()      # final files unchanged
    snap = read_partial(tmp_path)
    assert snap["status"] == "done" and "result" in snap


def test_reg_cli_no_progress_flag(tmp_path, monkeypatch):
    from agent_system.orchestrator import main
    monkeypatch.setattr(sys, "argv", ["agent_system", str(EXAMPLES / f"{TASK}.json"),
                                      "--mock", str(EXAMPLES), "--save", str(tmp_path),
                                      "--no-progress"])
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / PROGRESS_FILENAME).exists()
    assert not (tmp_path / PARTIAL_FILENAME).exists()
    assert (tmp_path / f"{TASK}_result.json").exists()


def test_reg_cli_plain_path_writes_progress(tmp_path, monkeypatch):
    """No --mock/--live (pipeline.run_from_ir, the UI's REG Mock Run): the
    progress log has node + verdict events, not only the final one."""
    from agent_system.orchestrator import main
    monkeypatch.setattr(sys, "argv", ["agent_system", str(EXAMPLES / f"{TASK}.json"),
                                      "--save", str(tmp_path)])
    with pytest.raises(SystemExit):
        main()
    events = read_events(tmp_path)
    kinds = [e["event"] for e in events]
    assert "node_start" in kinds and "node_done" in kinds
    assert any(e["event"] == "verdict" and e["payload"]["final"] for e in events)
    assert kinds[-1] == "done"
    snap = read_partial(tmp_path)
    assert snap["status"] == "done" and "run_from_ir" in snap["nodes_done"]
