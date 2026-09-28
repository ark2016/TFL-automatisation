"""``LiveRunner.run_agent`` (dcfl_system/orchestrator.py) on every call
path, using the shared ``FakeAnthropic`` test double (TODO.md §5 M) --
complements ``test_request_params.py`` (kwargs building) with the
retry/backoff/repair machinery. See cfl_system/tests/test_run_agent_paths.py
for the identical rationale; this file exercises dcfl's own copy of the
same ``LiveRunner`` shape.
"""

from __future__ import annotations

import threading
import time as _real_time_module

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.lib.testing.fake_anthropic import (
    FakeAnthropic,
    fatal_error,
    raises_turn,
    refusal_turn,
    retryable_error,
    stream_breaks_turn,
    text_turn,
)
from dcfl_system.orchestrator import LiveRunner

_REAL_SLEEP = _real_time_module.sleep


@pytest.fixture
def runner():
    return LiveRunner(api_key="sk-ant-test-not-used")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)


def test_thinking_block_read_past(runner):
    runner.client = FakeAnthropic([
        text_turn('{"status": "success", "verdict": "dcfl"}', thinking="hmm..."),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out == {"status": "success", "verdict": "dcfl"}
    assert len(runner.client.stream_calls) == 1


def test_refusal_returns_agent_error_with_one_call(runner):
    runner.client = FakeAnthropic([refusal_turn(category="bio")])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert "bio" in out["errors"][0]
    assert len(runner.client.stream_calls) == 1
    assert len(runner.client.create_calls) == 0


def test_retryable_error_then_success(runner, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleeps.append(s))
    runner.client = FakeAnthropic([
        raises_turn(retryable_error("connection reset")),
        text_turn('{"status": "success"}'),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out == {"status": "success"}
    assert len(runner.client.stream_calls) == 2
    assert len(sleeps) == 1


def test_retryable_error_exhausts_retry_budget(runner):
    runner.client = FakeAnthropic([
        raises_turn(retryable_error("boom 1")),
        raises_turn(retryable_error("boom 2")),
        raises_turn(retryable_error("boom 3")),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert "boom 3" in out["errors"][0]
    assert len(runner.client.stream_calls) == 3


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_fatal_error_propagates_without_retry(runner, status_code):
    """401/403/404 (dead key, no model access, unknown model) fail fast --
    the caller should not retry or treat these as one agent's problem."""
    runner.client = FakeAnthropic([raises_turn(fatal_error(status_code, "dead key"))])

    with pytest.raises(Exception) as excinfo:
        runner.run_agent("classifier", {"x": 1})

    assert getattr(excinfo.value, "status_code", None) == status_code
    assert len(runner.client.stream_calls) == 1


def test_400_bad_request_becomes_agent_error_not_raised(runner):
    """A 400 BadRequest that isn't a schema rejection (prompt too long, ...)
    is a malformed request for THIS agent, not a dead key -- it must not
    abort the whole pipeline run."""
    runner.client = FakeAnthropic([raises_turn(fatal_error(400, "prompt is too long"))])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert "prompt is too long" in out["errors"][0]
    assert len(runner.client.stream_calls) == 1


def test_stream_breaks_mid_response_is_retried(runner):
    runner.client = FakeAnthropic([
        stream_breaks_turn(retryable_error("dropped mid-stream")),
        text_turn('{"status": "success"}'),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out == {"status": "success"}
    assert len(runner.client.stream_calls) == 2


def test_invalid_json_repaired_by_haiku(runner):
    runner.client = FakeAnthropic([
        text_turn("oops, not json"),
        text_turn('{"status": "success"}'),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out == {"status": "success"}
    assert len(runner.client.stream_calls) == 1
    assert len(runner.client.create_calls) == 1


def test_truncated_response_haiku_repaired_becomes_inconclusive_capped(runner):
    runner.client = FakeAnthropic([
        text_turn('{"status": "succ', stop_reason="max_tokens"),
        text_turn('{"status": "success", "confidence": 0.95}'),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "inconclusive"
    assert out["_truncated"] is True
    assert out["_repaired"] is True
    assert out["confidence"] == pytest.approx(0.40)


def test_invalid_json_haiku_repair_fails_then_agent_error_after_retries(runner):
    runner.client = FakeAnthropic([
        text_turn("oops 1"), raises_turn(RuntimeError("repair down")),
        text_turn("oops 2"), raises_turn(RuntimeError("repair down")),
        text_turn("oops 3"), raises_turn(RuntimeError("repair down")),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert out["raw_response"] == "oops 3"
    assert len(runner.client.stream_calls) == 3
    assert len(runner.client.create_calls) == 3


def test_truncated_and_haiku_repair_fails_bails_out_immediately(runner):
    runner.client = FakeAnthropic([
        text_turn('{"status": "succ', stop_reason="max_tokens"),
        raises_turn(RuntimeError("repair down")),
    ])

    out = runner.run_agent("classifier", {"x": 1})

    assert out["status"] == "agent_error"
    assert "truncated" in out["errors"][0]
    assert len(runner.client.stream_calls) == 1
    assert len(runner.client.create_calls) == 1


def test_concurrency_semaphore_bounds_parallel_calls(monkeypatch):
    monkeypatch.setattr(llm_client, "_concurrency_semaphore", None)
    monkeypatch.setenv("TFL_MAX_CONCURRENCY", "2")
    monkeypatch.setattr(llm_client.time, "sleep", _REAL_SLEEP)

    n_calls = 6
    fake = FakeAnthropic([
        text_turn('{"status": "success"}', delay=0.05) for _ in range(n_calls)
    ])
    runner = LiveRunner(api_key="sk-ant-test-not-used")
    runner.client = fake

    threads = [
        threading.Thread(target=runner.run_agent, args=("classifier", {"i": i}))
        for i in range(n_calls)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(fake.stream_calls) == n_calls
    assert fake.peak_concurrent_calls == 2
