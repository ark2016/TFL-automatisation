"""Structured outputs (output_config.format) + fallback (TODO.md §3 M).

Exercises ``AnthropicClient`` directly with ``FakeAnthropic`` -- this is the
one place every pipeline's LiveRunner/LLMRunner funnels through, so a test
here covers cfl/dcfl/ll/agent_system at once:

1. When a schema is given, the request kwargs carry ``output_config.format``
   and the (guaranteed-clean) response text parses via plain ``json.loads``
   -- no brace-scanning ever needed.
2. When the API rejects ``output_config`` itself (schema/model
   incompatibility), the client transparently retries once *without* the
   schema instead of raising -- structured outputs unavailable falls back
   to the legacy extraction path, not a hard failure.
3. An unrelated 400 (bad key, bad request body for a reason that has
   nothing to do with the schema) is never mistaken for a schema rejection
   -- it still propagates immediately, with no silent retry.
"""

from __future__ import annotations

import json

import pytest

import agent_system.lib.llm_client as llm_client
from agent_system.lib.agent_output_schema import schema_for
from agent_system.lib.llm_client import (
    AnthropicClient, FatalAPIError, build_agent_output_schema,
    format_structured_output_flag,
    nullable, schema_number, schema_string, schema_string_array,
)
from agent_system.lib.testing.fake_anthropic import (
    FakeAnthropic, fatal_error, raises_turn, text_turn,
)

SCHEMA = build_agent_output_schema({
    "status": schema_string(),
    "confidence": schema_number(),
    "errors": nullable(schema_string_array()),
})


def _client() -> AnthropicClient:
    return AnthropicClient(default_effort="high", refusal_fallback=False)


@pytest.fixture(autouse=True)
def _clear_schema_rejection_memory():
    """`_schema_rejected_models` is process-global (TODO.md §3 M: remembered
    across calls on purpose) -- reset it around every test so one test's
    rejection can't leak into another's assertions."""
    llm_client._schema_rejected_models.clear()
    yield
    llm_client._schema_rejected_models.clear()


def test_request_carries_output_config_format_when_schema_given():
    fake = FakeAnthropic([text_turn('{"status": "success", "confidence": 0.9, "errors": []}')])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert result.used_structured_output is True
    assert json.loads(result.text) == {"status": "success", "confidence": 0.9, "errors": []}

    kwargs = fake.stream_calls[0]
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


def test_no_schema_no_output_config_format_backward_compatible():
    fake = FakeAnthropic([text_turn('{"status": "success"}')])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000, system="sys", user="u",
    )
    assert result.used_structured_output is False
    kwargs = fake.stream_calls[0]
    assert "format" not in kwargs.get("output_config", {})


def test_schema_rejection_falls_back_to_plain_call():
    """A 400 whose message names output_config/schema -> retried once
    without the schema, not raised."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(400, "output_config.format is not supported for this model")),
        text_turn('Sure, here you go:\n```json\n{"status": "success", "confidence": 0.5, "errors": []}\n```'),
    ])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert result.used_structured_output is False
    assert len(fake.stream_calls) == 2
    assert fake.stream_calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "format" not in fake.stream_calls[1].get("output_config", {})
    # The fallback call's prose-wrapped response still needs the legacy
    # extraction path (extract_json), which is exercised at the run_agent
    # level in each pipeline's own tests -- here we only check the request
    # sent for the retry and that the client didn't raise.
    assert "```json" in result.text


def test_schema_rejection_is_remembered_for_later_calls_to_the_same_model():
    """After one call's schema gets rejected, a LATER call to the SAME model
    must not pay for the doomed request again -- it should skip straight to
    the no-schema call (one stream call, not two)."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(400, "output_config.format is not supported for this model")),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
        text_turn('{"status": "success", "confidence": 0.6, "errors": []}'),
    ])
    client = _client()

    first = client.call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert first.used_structured_output is False
    assert len(fake.stream_calls) == 2  # schema attempt + fallback

    second = client.call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u2", output_schema=SCHEMA,
    )
    assert second.used_structured_output is False
    assert len(fake.stream_calls) == 3  # no repeated doomed schema attempt
    assert "format" not in fake.stream_calls[2].get("output_config", {})


def test_schema_rejection_memory_is_per_model():
    """A rejection remembered for one model must not suppress the schema
    for a DIFFERENT model that might support it fine."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(400, "output_config.format is not supported for this model")),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
        text_turn('{"status": "success", "confidence": 0.6, "errors": []}'),
    ])
    client = _client()

    client.call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert len(fake.stream_calls) == 2

    result = client.call(
        fake, model="claude-haiku-4-5", max_tokens=1000,
        system="sys", user="u2", output_schema=SCHEMA,
    )
    assert result.used_structured_output is True
    assert fake.stream_calls[2]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


def test_too_many_optional_parameters_falls_back_to_plain_call():
    """A 400 naming "too many optional parameters" (the actual live bug this
    round fixed: cfl's `reasoning`/`retry_planner` schemas had 63 optional
    properties in their shared retry-hint map -- see
    cfl_system/lib/agent_output_schema.py's module docstring) must be
    recognised as a schema rejection and retried once without the schema,
    same as any other output_config rejection -- not fall through as a
    hard FatalAPIError. Message text reproduced live 2026-09-27 (see
    agent_system/lib/testing/schema_checks.py's module docstring)."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(
            400,
            "Schemas contains too many optional parameters (63), which "
            "would make grammar compilation inefficient. Reduce the "
            "number of optional parameters in your tool schemas "
            "(limit: 24).",
        )),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
    ])
    result = _client().call(
        fake, model="claude-haiku-4-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert result.used_structured_output is False
    assert result.requested_structured_output is True
    assert len(fake.stream_calls) == 2
    assert fake.stream_calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "format" not in fake.stream_calls[1].get("output_config", {})
    assert result.schema_fallback_reason is not None
    assert "too many optional parameters" in result.schema_fallback_reason
    assert json.loads(result.text) == {"status": "success", "confidence": 0.5, "errors": []}


def test_too_many_union_typed_parameters_falls_back_to_plain_call():
    """A SEPARATE 400 from the "too many optional parameters" one above --
    hit live while fixing that one (converting cfl's 63 optional
    properties straight to required+nullable traded that rejection for
    this one instead, at 49 vs. a limit of 16). Must also be recognised
    as a schema rejection, not fall through as a hard FatalAPIError."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(
            400,
            "Schemas contains too many parameters with union types (49 "
            "parameters with type arrays or anyOf). This causes "
            "exponential compilation cost. Reduce the number of nullable "
            "or union-typed parameters (limit: 16 parameters with "
            "unions).",
        )),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
    ])
    result = _client().call(
        fake, model="claude-haiku-4-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert result.used_structured_output is False
    assert result.requested_structured_output is True
    assert len(fake.stream_calls) == 2
    assert fake.stream_calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "format" not in fake.stream_calls[1].get("output_config", {})
    assert result.schema_fallback_reason is not None
    assert "union types" in result.schema_fallback_reason
    assert json.loads(result.text) == {"status": "success", "confidence": 0.5, "errors": []}


def test_unrelated_fatal_error_is_not_treated_as_schema_rejection():
    fake = FakeAnthropic([raises_turn(fatal_error(400, "invalid task IR: missing 'alphabet'"))])
    with pytest.raises(FatalAPIError):
        _client().call(
            fake, model="claude-sonnet-5", max_tokens=1000,
            system="sys", user="u", output_schema=SCHEMA,
        )
    assert len(fake.stream_calls) == 1


def test_too_many_requests_rate_limit_is_not_treated_as_schema_rejection():
    """A 400 that happens to contain the words "too many" but is actually an
    unrelated rate-limit error ("too many requests, please try again later")
    must NOT be recognised as a schema rejection -- a bare "too many" marker
    was tried once and matched this too, silently disabling structured
    outputs for the model for the rest of the process (see
    ``_schema_rejected_models``); it was removed in favour of the specific
    phrasings ("too many optional parameters", "grammar compilation", ...)."""
    fake = FakeAnthropic([raises_turn(fatal_error(
        400, "too many requests, please try again later",
    ))])
    with pytest.raises(FatalAPIError):
        _client().call(
            fake, model="claude-sonnet-5", max_tokens=1000,
            system="sys", user="u", output_schema=SCHEMA,
        )
    assert len(fake.stream_calls) == 1
    assert not llm_client._model_schema_known_rejected("claude-sonnet-5")


def test_no_schema_requested_verbose_flag_is_plain_so_no():
    """No schema was ever requested (e.g. input_parser) -- the verbose
    flag has nothing to explain, unlike a real fallback."""
    fake = FakeAnthropic([text_turn('{"status": "success"}')])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000, system="sys", user="u",
    )
    assert result.requested_structured_output is False
    assert result.schema_fallback_reason is None
    assert format_structured_output_flag(result) == " so=no"


def test_schema_rejection_verbose_flag_names_the_reason():
    """A schema that gets rejected this call -- the verbose flag must say
    *why* it fell back (TODO.md §3 M: distinguish "never had a schema"
    from "requested but the API rejected it"), not just ``so=no``."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(400, "output_config.format is not supported for this model")),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
    ])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert result.requested_structured_output is True
    assert result.schema_fallback_reason is not None
    assert "rejected" in result.schema_fallback_reason
    flag = format_structured_output_flag(result)
    assert flag.startswith(" so=no (fallback:")
    assert "rejected" in flag


def test_schema_known_rejected_upfront_verbose_flag_names_the_reason():
    """The *second* call to a model whose schema was already rejected this
    process skips the schema up front (no doomed request) -- the verbose
    flag must still explain why, not just say ``so=no``."""
    fake = FakeAnthropic([
        raises_turn(fatal_error(400, "output_config.format is not supported for this model")),
        text_turn('{"status": "success", "confidence": 0.5, "errors": []}'),
        text_turn('{"status": "success", "confidence": 0.6, "errors": []}'),
    ])
    client = _client()
    client.call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    result = client.call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u2", output_schema=SCHEMA,
    )
    assert result.requested_structured_output is True
    assert result.used_structured_output is False
    assert result.schema_fallback_reason is not None
    assert "previously rejected" in result.schema_fallback_reason
    assert format_structured_output_flag(result).startswith(" so=no (fallback:")


def test_successful_structured_call_verbose_flag_is_so_yes():
    fake = FakeAnthropic([text_turn('{"status": "success", "confidence": 0.9, "errors": []}')])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=SCHEMA,
    )
    assert format_structured_output_flag(result) == " so=yes"


def test_agent_output_schema_end_to_end_for_a_real_agent():
    """schema_for("classifier") (agent_system) plugged straight into a call
    -- confirms the wiring, not just the generic helper."""
    schema = schema_for("classifier")
    assert schema is not None
    fake = FakeAnthropic([text_turn(
        '{"module": "classifier", "status": "success", "verdict": "regular", '
        '"dispatch": {}, "hard_rule_applied": null, "reasoning": "...", "confidence": 0.9}'
    )])
    result = _client().call(
        fake, model="claude-sonnet-5", max_tokens=1000,
        system="sys", user="u", output_schema=schema,
    )
    parsed = json.loads(result.text)
    assert parsed["module"] == "classifier"
    assert fake.stream_calls[0]["output_config"]["format"]["schema"] == schema
