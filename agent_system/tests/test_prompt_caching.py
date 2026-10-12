"""Prompt caching for pipeline LLM calls (mock / fake client only).

The big per-agent system prompt is the stable cached prefix: ``system`` is a
list of text blocks, the prompt block carries ``cache_control`` (5-minute
TTL), everything task-specific sits after the last breakpoint (user message,
or an unmarked second system block). The cache is a byte-exact prefix match,
so the same agent must send identical system bytes for two different tasks.
Cache-aware cost math (write 1.25x, read 0.1x) is checked here too.
"""

from __future__ import annotations

import importlib
import json
import re
from types import SimpleNamespace

import pytest

from agent_system.lib.llm_client import (
    MODEL_PRICING, AnthropicClient, LLMRunner, UsageTracker, build_system_blocks,
    estimate_cost_usd, system_text,
)
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, text_turn

EPHEMERAL = {"type": "ephemeral"}


def _breakpoints(kwargs: dict) -> int:
    """Number of cache_control markers anywhere in the request."""
    return json.dumps(kwargs, ensure_ascii=False).count('"cache_control"')


def _assert_cached_prefix(system, *, volatile_marker: str | None = None):
    assert isinstance(system, list) and system
    first = system[0]
    assert first["type"] == "text" and first["cache_control"] == EPHEMERAL
    assert "ttl" not in first["cache_control"]          # default 5-minute TTL
    for later in system[1:]:                            # nothing cached after the volatile part
        assert "cache_control" not in later
    if volatile_marker is not None:
        assert volatile_marker not in first["text"]


def _attach(runner, client):
    """Swap the SDK client of a LiveRunner / LLMRunner for a fake."""
    setattr(runner, "_client" if isinstance(runner, LLMRunner) else "client", client)


# ---------------------------------------------------------------------------
# build_request_kwargs / build_system_blocks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"])
def test_stable_system_block_carries_the_breakpoint(model):
    kw = AnthropicClient().build_request_kwargs(model, 1000, "STABLE PROMPT", "task json")
    assert kw["system"] == [{"type": "text", "text": "STABLE PROMPT", "cache_control": EPHEMERAL}]
    assert kw["messages"] == [{"role": "user", "content": "task json"}]
    assert _breakpoints(kw) == 1


def test_volatile_system_part_is_a_second_unmarked_block():
    kw = AnthropicClient().build_request_kwargs(
        "claude-sonnet-5-5", 1000, "STABLE", "u", system_volatile="NOTES of this task",
    )
    assert [b["text"] for b in kw["system"]] == ["STABLE", "NOTES of this task"]
    _assert_cached_prefix(kw["system"], volatile_marker="NOTES")
    assert _breakpoints(kw) == 1
    assert system_text(kw["system"]) == "STABLENOTES of this task"


def test_cache_system_false_keeps_a_plain_string_for_throwaway_calls():
    kw = AnthropicClient().build_request_kwargs(
        "claude-haiku-4-5", 256, "repair json", "u", cache_system=False,
    )
    assert kw["system"] == "repair json"
    assert _breakpoints(kw) == 0
    assert build_system_blocks("x", cache=False) == "x"
    assert build_system_blocks("x", volatile="v", cache=False) == [
        {"type": "text", "text": "x"}, {"type": "text", "text": "v"},
    ]


def test_caching_does_not_touch_structured_output_or_thinking_params():
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    kw = AnthropicClient().build_request_kwargs(
        "claude-opus-5-5", 64000, "S", "u", effort="high", output_schema=schema,
    )
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": schema}}
    assert _breakpoints(kw) == 1
    # a schema-less (fallback) request is still cached, with its own prefix
    kw = AnthropicClient().build_request_kwargs("claude-opus-5-5", 64000, "S", "u", effort="high")
    assert _breakpoints(kw) == 1


def test_call_passes_the_cached_blocks_to_stream():
    fake = FakeAnthropic([text_turn('{"a": 1}')])
    AnthropicClient().call(
        fake, model="claude-sonnet-5-5", max_tokens=1000, system="STABLE", user="u",
        system_volatile="VOL",
    )
    sent = fake.stream_calls[0]["system"]
    _assert_cached_prefix(sent, volatile_marker="VOL")
    assert sent[1]["text"] == "VOL"


# ---------------------------------------------------------------------------
# Prefix stability per pipeline: same agent, two different tasks
# ---------------------------------------------------------------------------

def _runner_cases():
    cases = [("reg", lambda: LLMRunner(api_key="sk-ant-test-not-used"))]
    for name in ("cfl", "dcfl", "ll"):
        def make(name=name):
            return importlib.import_module(f"{name}_system.orchestrator").LiveRunner(
                api_key="sk-ant-test-not-used")
        cases.append((name, make))
    return [pytest.param(make, id=name) for name, make in cases]


@pytest.mark.parametrize("make_runner", _runner_cases())
def test_system_prompt_bytes_identical_across_tasks_and_task_data_after_breakpoint(make_runner):
    runner = make_runner()
    client = FakeAnthropic([text_turn('{"status": "success"}')] * 2)
    _attach(runner, client)
    runner.run_agent("classifier", {"language": "a^n b^n", "ir": {"task": "FIRST-TASK-MARKER"}})
    runner.run_agent("classifier", {"language": "ww", "ir": {"task": "SECOND-TASK-MARKER"}})
    first, second = client.stream_calls

    for kw in (first, second):
        _assert_cached_prefix(kw["system"])
        assert _breakpoints(kw) == 1
        assert len(kw["system"][0]["text"]) > 2000          # the real, large agent prompt
    # byte-identical prefix: same agent => same cached system block; the task comes after it
    assert json.dumps(first["system"], sort_keys=True) == json.dumps(second["system"], sort_keys=True)
    assert first["system"][0]["text"].encode() == second["system"][0]["text"].encode()
    for key in ("model", "thinking", "output_config", "extra_body"):
        assert first.get(key) == second.get(key)

    for kw, marker in ((first, "FIRST-TASK-MARKER"), (second, "SECOND-TASK-MARKER")):
        assert marker not in system_text(kw["system"])
        assert marker in kw["messages"][0]["content"]


@pytest.mark.parametrize("make_runner", _runner_cases())
def test_no_volatile_values_in_the_cached_system_block(make_runner):
    runner = make_runner()
    client = FakeAnthropic([text_turn('{"status": "success"}')])
    _attach(runner, client)
    runner.run_agent("classifier", {"language": "a*"})
    text = client.stream_calls[0]["system"][0]["text"]
    assert not re.search(r"\b20\d\d-\d\d-\d\d", text)                                    # no dates
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)  # no uuids


@pytest.mark.parametrize("module", ["cfl_system", "dcfl_system", "ll_system"])
def test_json_retry_context_stays_out_of_the_cached_prefix(module):
    """A bad first reply triggers the retry attempt; its error context goes in the user message only."""
    runner = importlib.import_module(f"{module}.orchestrator").LiveRunner(api_key="sk-ant-test-not-used")
    # bad reply -> Haiku repair (a .create() call) also fails -> retry attempt succeeds
    client = FakeAnthropic([
        text_turn("not json {"), text_turn("still not json {", model="claude-haiku-4-5"),
        text_turn('{"status": "success"}'),
    ])
    _attach(runner, client)
    runner.run_agent("classifier", {"language": "a*"})
    assert len(client.stream_calls) == 2 and len(client.create_calls) == 1
    first, second = client.stream_calls
    assert first["system"] == second["system"]
    _assert_cached_prefix(second["system"])
    assert "RETRY" in second["messages"][0]["content"]
    assert "RETRY" not in system_text(second["system"])


def test_distinct_agents_have_distinct_prefixes():
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    client = FakeAnthropic([text_turn('{"status": "success"}')] * 2)
    _attach(runner, client)
    runner.run_agent("classifier", {"x": 1})
    runner.run_agent("closure", {"x": 1})
    a, b = client.stream_calls
    assert a["system"][0]["text"] != b["system"][0]["text"]


# ---------------------------------------------------------------------------
# REG student notes: volatile, after the breakpoint
# ---------------------------------------------------------------------------

def test_reg_student_notes_are_an_uncached_second_system_block():
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    client = FakeAnthropic([text_turn('{"verdict": "regular"}')] * 3)
    _attach(runner, client)

    runner.run_agent("classifier", {"language": "a*", "student_notes": "NOTE-ONE"})
    runner.run_agent("classifier", {"language": "a*", "student_notes": "NOTE-TWO"})
    runner.run_agent("classifier", {"language": "a*"})
    one, two, none = client.stream_calls

    for kw, note in ((one, "NOTE-ONE"), (two, "NOTE-TWO")):
        _assert_cached_prefix(kw["system"], volatile_marker=note)
        assert len(kw["system"]) == 2
        assert kw["system"][1]["text"].startswith("## Student Notes")
        assert note in kw["system"][1]["text"]
        assert note not in kw["messages"][0]["content"]
    # the cached block is the same with, without and with different notes
    assert one["system"][0] == two["system"][0] == none["system"][0]
    assert len(none["system"]) == 1


def test_reg_quick_validate_is_not_cached():
    runner = LLMRunner(api_key="sk-ant-test-not-used")
    client = FakeAnthropic([text_turn("ok", model="claude-haiku-4-5")])
    _attach(runner, client)
    runner.quick_validate("q?", {"a": 1})
    kw = client.create_calls[0]
    assert isinstance(kw["system"], str)
    assert _breakpoints(kw) == 0


@pytest.mark.parametrize("module", ["cfl_system", "dcfl_system", "ll_system"])
def test_haiku_json_repair_call_is_not_cached(module):
    runner = importlib.import_module(f"{module}.orchestrator").LiveRunner(api_key="sk-ant-test-not-used")
    client = FakeAnthropic([text_turn('{"fixed": true}', model="claude-haiku-4-5")])
    _attach(runner, client)
    assert runner._repair_json_with_haiku("classifier", '{"fixed": true,', False) == {"fixed": True}
    kw = client.create_calls[0]
    assert isinstance(kw["system"], str) and _breakpoints(kw) == 0
    assert kw["extra_body"] == {"temperature": 0.0}


# ---------------------------------------------------------------------------
# Lean formalizer path (agent_system.lib.formalize_run.LiveSource)
# ---------------------------------------------------------------------------

def test_formalizer_prover_calls_use_the_cached_system_prompt():
    from agent_system.lib.formalize_run import LiveSource

    tracker = UsageTracker()
    fake = FakeAnthropic([
        text_turn('{"proof_body": "simp", "notes": ""}', cache_creation_input_tokens=6000),
        text_turn('{"proof_body": "simp", "notes": ""}', cache_read_input_tokens=6000),
    ])
    source = LiveSource(fake, tracker, refusal_fallback=False)
    system = "LEAN PROVER INSTRUCTIONS " * 400
    props = []
    for user in ('{"statement": "T1"}', '{"statement": "T2"}'):
        props.append(source.propose(
            agent="lean_formalizer", model="claude-sonnet-5-5", effort="high", max_tokens=1000,
            system=system, user=user, temperature=0.0, schema=None, attempt=1,
        ))
    assert [p.body for p in props] == ["simp", "simp"]
    first, second = fake.stream_calls
    assert first["system"] == second["system"] == [
        {"type": "text", "text": system, "cache_control": EPHEMERAL},
    ]
    assert first["messages"][0]["content"] != second["messages"][0]["content"]
    assert (props[0].cache_creation_input_tokens, props[1].cache_read_input_tokens) == (6000, 6000)
    d = tracker.as_dict()
    assert d["cache_creation_input_tokens"] == 6000 and d["cache_read_input_tokens"] == 6000


# ---------------------------------------------------------------------------
# Cost accounting with cache tokens
# ---------------------------------------------------------------------------

def _usage(**kw):
    base = dict(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                cache_creation_input_tokens=0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("model, read_mult", [
    ("claude-haiku-4-5", 0.1), ("claude-sonnet-5-5", 0.1), ("claude-sonnet-5", 0.1),
    ("claude-opus-5-5", 0.05),     # Opus 5.5 reads at 0.05x (docs: prompt-caching Economics)
])
def test_pricing_table_uses_the_documented_cache_multipliers(model, read_mult):
    p = MODEL_PRICING[model]
    assert p["cache_write"] == pytest.approx(1.25 * p["input"])      # 5-minute TTL write
    assert p["cache_read"] == pytest.approx(read_mult * p["input"])


def test_cost_of_a_cache_write_then_a_cache_read_on_sonnet_5_5():
    model = "claude-sonnet-5-5"
    write = estimate_cost_usd(model, _usage(input_tokens=500, cache_creation_input_tokens=10_000, output_tokens=1000))
    read = estimate_cost_usd(model, _usage(input_tokens=500, cache_read_input_tokens=10_000, output_tokens=1000))
    uncached = estimate_cost_usd(model, _usage(input_tokens=10_500, output_tokens=1000))
    assert write == pytest.approx((500 * 2.00 + 10_000 * 2.50 + 1000 * 10.00) / 1e6)
    assert read == pytest.approx((500 * 2.00 + 10_000 * 0.20 + 1000 * 10.00) / 1e6)
    assert read < uncached < write
    # the cached prefix pays off from the second call on (1.25x + 0.1x < 2x)
    assert write + read < 2 * uncached


def test_tracker_sums_cache_buckets_across_calls_and_agents():
    t = UsageTracker()
    t.record("claude-opus-5-5", _usage(input_tokens=100, output_tokens=10, cache_creation_input_tokens=8000), agent="a")
    t.record("claude-opus-5-5", _usage(input_tokens=100, output_tokens=10, cache_read_input_tokens=8000), agent="a")
    d = t.as_dict()
    assert d["cache_creation_input_tokens"] == 8000 and d["cache_read_input_tokens"] == 8000
    assert d["per_agent"]["a"]["by_model"]["claude-opus-5-5"]["cache_read_input_tokens"] == 8000
    expected = (200 * 4.00 + 20 * 20.00 + 8000 * 5.00 + 8000 * 0.20) / 1e6
    assert d["estimated_cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert "cache_read=8000" in t.summary_line() and "cache_write=8000" in t.summary_line()


def test_usage_without_cache_fields_still_works():
    """Older results / fake usage objects: no cache_* attributes at all, or None."""
    legacy = SimpleNamespace(input_tokens=1000, output_tokens=100)
    assert estimate_cost_usd("claude-sonnet-5-5", legacy) == pytest.approx((1000 * 2 + 100 * 10) / 1e6)
    none_fields = SimpleNamespace(input_tokens=1000, output_tokens=100,
                                  cache_read_input_tokens=None, cache_creation_input_tokens=None)
    assert estimate_cost_usd("claude-sonnet-5-5", none_fields) == estimate_cost_usd("claude-sonnet-5-5", legacy)
    t = UsageTracker()
    t.record("claude-sonnet-5-5", legacy, agent="x")
    d = t.as_dict()
    assert d["cache_read_input_tokens"] == 0 and d["cache_creation_input_tokens"] == 0
    assert "cache_" not in t.summary_line()


def test_progress_running_total_includes_cache_buckets(tmp_path):
    from agent_system.lib.progress import ProgressWriter

    tracker = UsageTracker()
    pw = ProgressWriter(tmp_path, system="agent_system")
    pw.attach_tracker(tracker)
    tracker.record("claude-sonnet-5-5", _usage(input_tokens=50, output_tokens=5, cache_creation_input_tokens=3000), agent="x")
    tracker.record("claude-sonnet-5-5", _usage(input_tokens=50, output_tokens=5, cache_read_input_tokens=3000), agent="x")
    snap = pw.usage_snapshot()
    assert snap["cache_creation_input_tokens"] == 3000 and snap["cache_read_input_tokens"] == 3000
    assert snap["estimated_cost_usd"] == pytest.approx(
        (100 * 2.00 + 10 * 10.00 + 3000 * 2.50 + 3000 * 0.20) / 1e6, abs=1e-6)


def test_progress_append_to_an_old_run_without_cache_fields(tmp_path):
    """A progress.jsonl written before prompt caching has no cache_* keys in its usage."""
    from agent_system.lib.progress import ProgressWriter

    (tmp_path / "progress.jsonl").write_text(json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "done", "node": "",
        "payload": {}, "usage": {"calls": 2, "input_tokens": 10, "output_tokens": 5,
                                 "estimated_cost_usd": 0.01},
    }) + "\n", encoding="utf-8")
    pw = ProgressWriter(tmp_path, system="agent_system", append=True)
    snap = pw.usage_snapshot()
    assert snap["calls"] == 2 and snap["cache_read_input_tokens"] == 0
    assert snap["estimated_cost_usd"] == pytest.approx(0.01)
