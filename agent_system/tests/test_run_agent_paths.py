"""``LLMRunner.run_agent`` (agent_system/lib/llm_client.py) on every call
path, using the shared ``FakeAnthropic`` test double (TODO.md §5 M):

  - a thinking block emitted before the text block is read past, not
    mistaken for the answer;
  - a safety refusal becomes an ``agent_error`` dict with exactly one
    call -- no "please output valid JSON" retry after a decline;
  - a retryable error (network/5xx/...) on the first attempt is retried
    (with backoff) and succeeds on the second, inside one ``run_agent``
    call;
  - a retryable error that never clears becomes an ``agent_error`` dict
    once the shared client's own retry budget (3 attempts) is spent;
  - a fatal error (401/403/404/400) propagates as the original SDK
    exception, with no retry at all;
  - a stream that opens fine and then breaks reading the response is
    treated the same as any other retryable error;
  - invalid JSON on both the first call and the one JSON-only retry
    becomes an ``agent_error`` dict (``LLMRunner`` has no Haiku-repair
    step -- unlike cfl/dcfl/ll's ``LiveRunner`` -- its retry just re-asks
    the same model);
  - the process-wide concurrency semaphore (``TFL_MAX_CONCURRENCY``)
    bounds how many calls run in parallel.
"""

from __future__ import annotations

import json
import os
import threading
import time as _real_time_module

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.graph import extract_dfa
from agent_system.lib.llm_client import LLMRunner
from agent_system.lib.testing.fake_anthropic import (
    FakeAnthropic,
    fatal_error,
    raises_turn,
    refusal_turn,
    retryable_error,
    stream_breaks_turn,
    text_turn,
)


# Captured at import time, before the autouse fixture below ever runs, so
# the concurrency test can restore genuine timing for its artificial delay
# -- ``llm_client.time`` *is* the ``time`` module, so the autouse no-op
# would otherwise also swallow FakeAnthropic's own ``delay=`` sleep.
_REAL_SLEEP = _real_time_module.sleep


@pytest.fixture
def runner():
    return LLMRunner(api_key="sk-ant-test-not-used")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Every retry/backoff path in this file is exercised with a mocked
    clock -- the assertions are about attempt counts, not wall time."""
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(llm_client, "_load_env", lambda: None)


def test_thinking_block_read_past(runner):
    runner._client = FakeAnthropic([
        text_turn('{"status": "success", "verdict": "regular"}', thinking="let's see..."),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "success"
    assert out["evidence"]["verdict"] == "regular"
    assert len(runner._client.stream_calls) == 1


def test_refusal_returns_agent_error_with_one_call(runner):
    runner._client = FakeAnthropic([refusal_turn(category="bio", explanation="nope")])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["module"] == "classifier"
    assert out["status"] == "agent_error"
    assert out["evidence"] == {}
    assert out["confidence"] == 0.0
    assert "bio" in out["errors"][0] and "nope" in out["errors"][0]
    assert len(runner._client.stream_calls) == 1


def test_retryable_error_then_success(runner, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleeps.append(s))
    runner._client = FakeAnthropic([
        raises_turn(retryable_error("connection reset")),
        text_turn('{"status": "success"}'),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "success"
    assert len(runner._client.stream_calls) == 2
    assert len(sleeps) == 1  # backoff before the second (successful) attempt


def test_retryable_error_exhausts_retry_budget(runner, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleeps.append(s))
    runner._client = FakeAnthropic([
        raises_turn(retryable_error("boom 1")),
        raises_turn(retryable_error("boom 2")),
        raises_turn(retryable_error("boom 3")),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "agent_error"
    assert "boom 3" in out["errors"][0]
    # AnthropicClient.call's own budget: 3 attempts, backoff before each retry.
    assert len(runner._client.stream_calls) == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_fatal_error_propagates_without_retry(runner, status_code):
    """401/403/404 (dead key, no model access, unknown model) fail fast --
    the caller should not retry or treat these as one agent's problem."""
    runner._client = FakeAnthropic([raises_turn(fatal_error(status_code, "dead key"))])

    with pytest.raises(Exception) as excinfo:
        runner.run_agent("classifier", {"language": "a*"})

    assert getattr(excinfo.value, "status_code", None) == status_code
    assert len(runner._client.stream_calls) == 1


def test_400_bad_request_becomes_agent_error_not_raised(runner):
    """A 400 BadRequest that isn't a schema rejection (prompt too long, a
    rejected refusal-fallback header, ...) is a malformed request for THIS
    agent, not a dead key -- it must not abort the whole pipeline run."""
    runner._client = FakeAnthropic([raises_turn(fatal_error(400, "prompt is too long"))])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "agent_error"
    assert out["module"] == "classifier"
    assert "prompt is too long" in out["errors"][0]
    assert len(runner._client.stream_calls) == 1


def test_stream_breaks_mid_response_is_retried(runner):
    """A stream that opens but breaks reading the final message is a
    retryable error like any other -- not a fatal one, and not something
    that makes run_agent skip straight to JSON-repair logic."""
    runner._client = FakeAnthropic([
        stream_breaks_turn(retryable_error("dropped mid-stream")),
        text_turn('{"status": "success"}'),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "success"
    assert len(runner._client.stream_calls) == 2


def test_invalid_json_twice_becomes_agent_error(runner):
    """LLMRunner has no Haiku-repair step: one retry with the same model,
    then give up."""
    runner._client = FakeAnthropic([
        text_turn("this is not json at all"),
        text_turn("still not json"),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "agent_error"
    assert out["module"] == "classifier"
    assert "not valid JSON" in out["errors"][0]
    assert len(runner._client.stream_calls) == 2
    assert len(runner._client.create_calls) == 0  # no Haiku repair in this runner


def test_schema_shaped_dfa_builder_output_extract_dfa_still_works(runner):
    """A structured-output response for dfa_builder matches its closed
    schema exactly (module/status/dfa/... all at the top level -- no
    'evidence' key, since `agent_output_schema.REQUIRED_KEYS["dfa_builder"]`
    doesn't have one). `_wrap_output` must not treat that as "not yet
    wrapped" and nest it under a fresh 'evidence' (losing the real fields),
    and `extract_dfa` must still find the dfa either way."""
    dfa = {"states": ["q0"], "start": "q0", "accept": ["q0"], "transitions": {}}
    runner._client = FakeAnthropic([
        text_turn(json.dumps({
            "module": "dfa_builder", "status": "success", "dfa": dfa,
            "state_descriptions": {}, "explanation": "trivial",
            "confidence": 0.9, "errors": [],
        })),
    ])

    out = runner.run_agent("dfa_builder", {"ir": {}})

    assert out["status"] == "success"
    assert extract_dfa(out) == dfa


def test_schema_shaped_classifier_output_verdict_is_read(runner):
    """Same shape issue as dfa_builder, for the classifier: `verdict` and
    `dispatch` sit at the top level of a structured-output response, and
    `graph.run_classifier_node` must still be able to read them (falling
    back to the output itself when 'evidence' is absent)."""
    runner._client = FakeAnthropic([
        text_turn(json.dumps({
            "module": "classifier", "status": "success", "verdict": "regular",
            "dispatch": ["re_builder"], "hard_rule_applied": None,
            "reasoning": "matches a*b*", "confidence": 0.9,
        })),
    ])

    out = runner.run_agent("classifier", {"language": "a*"})

    assert out["status"] == "success"
    assert out.get("evidence", out).get("verdict") == "regular"
    assert out.get("evidence", out).get("dispatch") == ["re_builder"]


@pytest.mark.parametrize("fenced", [False, True])
def test_formalizer_json_body_reaches_graph_compiler(runner, monkeypatch, fenced):
    from agent_system import graph
    from agent_system.lib import lean_ir

    body = "trivial"
    response = json.dumps({"proof_body": body, "lemmas_used": [], "notes": "offline"})
    if fenced:
        response = f"```json\n{response}\n```"
    runner._client = FakeAnthropic([text_turn(response)])
    statement = {
        "imports": [], "alphabet_decl": "", "language_decl": "",
        "theorem_decl": "theorem tfl_main : True", "name": "tfl_main",
    }
    monkeypatch.setattr(lean_ir, "render_statement", lambda _ir, _direction: statement)
    compiled = []

    def check(text, **_kwargs):
        compiled.append(text)
        return {"status": "proved", "errors": [], "axioms": [], "elapsed": 0.0}

    monkeypatch.setattr(graph, "check_lean_file", check)
    state = {
        "formalize": True, "ir": {}, "agent_runner": runner,
        "reasoning_output": {"evidence": {
            "action": "proceed_to_formalizer", "verdict": "regular",
            "consolidated_proof": "offline plan",
        }},
    }

    result = graph.formalize_node(state)["formalization"]

    assert result["status"] == "proved" and result["proof_body"] == body
    assert len(compiled) == len(runner._client.stream_calls) == 1
    assert "  trivial" in compiled[0] and '"proof_body"' not in compiled[0]


def test_formalizer_invalid_json_is_an_agent_error(runner):
    runner._client = FakeAnthropic([text_turn("not json"), text_turn("still not json")])

    out = runner.run_agent("formalizer", {"statement": {}})

    assert out["status"] == "agent_error" and out["evidence"] == {}
    assert len(runner._client.stream_calls) == 2


def test_concurrency_semaphore_bounds_parallel_calls(monkeypatch):
    """The process-wide semaphore (agent_system.lib.llm_client) caps how
    many calls run at once, regardless of how many threads fan out."""
    monkeypatch.setattr(llm_client, "_concurrency_semaphore", None)
    monkeypatch.setenv("TFL_MAX_CONCURRENCY", "2")
    monkeypatch.setattr(llm_client.time, "sleep", _REAL_SLEEP)

    n_calls = 6
    fake = FakeAnthropic([
        text_turn('{"status": "success"}', delay=0.05) for _ in range(n_calls)
    ])
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    runner._client = fake

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
