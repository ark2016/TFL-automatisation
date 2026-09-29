"""Progress contract wiring for cfl_system (mock mode; agent_system/lib/progress.py).

progress.jsonl carries node events, partial_result.json is refreshed after
every node, usage grows monotonically, and the CLI writes both by default
with --save (and nothing with --no-progress). No API calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_system.lib.llm_client import UsageTracker
from agent_system.lib.progress import (
    PARTIAL_FILENAME,
    PROGRESS_FILENAME,
    ProgressWriter,
    read_events,
    read_partial,
)
from cfl_system.orchestrator import MockRunner, run_pipeline, main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES / "mock"
TASK = "task_w1w2w1w3"
EXPECTED_NODES = ['validate_ir_node', 'analyze_hypothesis_node', 'run_classifier_node', 'run_specialist_node', 'collect_specialists_node', 'build_oracle_node', 'oracle_test_node', 'run_reasoning_node', 'verdict_gate_node', 'assemble_result_node']


def _ir():
    return json.loads((EXAMPLES / f"{TASK}.json").read_text(encoding="utf-8"))


def _mock():
    return MockRunner(str(MOCK_DIR), TASK)


class _MeteredMock:
    """agent_runner double: replays mock outputs, bills a tracker per call."""

    def __init__(self, inner):
        self.inner = inner
        self.usage_tracker = UsageTracker()

    def run_agent(self, agent_name, input_data=None):
        out = self.inner.run_agent(agent_name, input_data)
        if out is not None:
            self.usage_tracker.record(
                "claude-haiku-4-5",
                SimpleNamespace(input_tokens=1000, output_tokens=300,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0),
                agent=agent_name,
            )
        return out


def test_progress_jsonl_has_events_for_all_nodes(tmp_path):
    pw = ProgressWriter(tmp_path, system="cfl_system")
    result = run_pipeline(_ir(), mock_runner=_mock(), progress=pw)
    events = read_events(tmp_path)
    started = {e["node"] for e in events if e["event"] == "node_start"}
    done = {e["node"] for e in events if e["event"] == "node_done"}
    assert started == done
    for node in EXPECTED_NODES:
        assert node in done, node
    assert events[-1]["event"] == "verdict" and events[-1]["payload"]["final"] is True
    for e in events:
        assert set(e) == {"ts", "event", "node", "payload", "usage"}
    assert result["usage"]["calls"] == 0


def test_partial_result_refreshed_after_every_node(tmp_path):
    pw = ProgressWriter(tmp_path)
    seen: list[int] = []
    orig = pw.write_partial

    def spy():
        orig()
        seen.append(len(read_partial(tmp_path)["nodes_done"]))

    pw.write_partial = spy
    run_pipeline(_ir(), mock_runner=_mock(), progress=pw)
    assert seen == sorted(seen) and seen[0] < seen[-1]
    snap = read_partial(tmp_path)
    assert snap["last_node"] == "assemble_result_node"
    assert isinstance(snap["result"], dict) and snap["result"]
    assert snap["state"]
    json.dumps(snap)


def test_usage_grows_monotonically_with_llm_events(tmp_path):
    runner = _MeteredMock(_mock())
    pw = ProgressWriter(tmp_path)
    run_pipeline(_ir(), agent_runner=runner, progress=pw)
    events = read_events(tmp_path)
    llm = [e for e in events if e["event"] == "llm_call"]
    assert {e["payload"]["phase"] for e in llm} == {"start", "done"}
    for key in ("calls", "input_tokens", "output_tokens", "estimated_cost_usd"):
        vals = [e["usage"][key] for e in events]
        assert vals == sorted(vals), key
    assert events[-1]["usage"]["calls"] == runner.usage_tracker.as_dict()["calls"] > 0


def test_no_writer_no_files(tmp_path):
    run_pipeline(_ir(), mock_runner=_mock())
    assert not list(tmp_path.iterdir())


def _argv(tmp_path, *extra):
    return ["cfl_system", str(EXAMPLES / f"{TASK}.json"), "--mock", str(MOCK_DIR),
            "--save", str(tmp_path), *extra]


def test_cli_progress_on_by_default_with_save(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv(tmp_path))
    with pytest.raises(SystemExit):
        main()
    events = read_events(tmp_path)
    assert events[-1]["event"] == "done"
    assert f"{TASK}_result.json" in events[-1]["payload"]["files"]
    assert (tmp_path / f"{TASK}_result.json").exists()
    snap = read_partial(tmp_path)
    assert snap["status"] == "done" and snap["result"]


def test_cli_no_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "--no-progress"))
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / PROGRESS_FILENAME).exists()
    assert not (tmp_path / PARTIAL_FILENAME).exists()
    assert (tmp_path / f"{TASK}_result.json").exists()
