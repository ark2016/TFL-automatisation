"""Exercise the real SDK over an in-memory HTTP transport; no network or key lookup."""

import httpx2 as httpx
import pytest
from anthropic import Anthropic as SDKAnthropic

import agent_system.lib.llm_client as llm_client
from agent_system.lib.llm_client import AnthropicClient, FatalAPIError, RetryableAPIError


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)


@pytest.mark.parametrize("sdk_retries", [0, 2, 5])
@pytest.mark.parametrize("status", [429, 503])
def test_shared_budget_bounds_actual_http_requests(monkeypatch, sdk_retries, status):
    requests, sleeps = [], []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={
            "type": "error", "error": {"type": "rate_limit_error", "message": "offline"},
        })

    monkeypatch.setattr(llm_client, "_sleep_with_backoff", lambda *args: sleeps.append(args))
    shared = AnthropicClient(max_retries=3)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        with SDKAnthropic(api_key="offline-not-a-secret", http_client=http_client,
                          max_retries=sdk_retries) as sdk:
            with pytest.raises(RetryableAPIError):
                shared.call(sdk, model="claude-haiku-4-5", max_tokens=10,
                            system="offline", user="offline")
            assert sdk.max_retries == sdk_retries
            assert not sdk.is_closed()

    assert len(requests) == 3 and len(sleeps) == 2
    assert shared.usage_tracker.as_dict()["calls"] == 0


def test_shared_fatal_http_error_is_not_retried(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={
            "type": "error", "error": {"type": "authentication_error", "message": "offline"},
        })

    def unexpected_backoff(*_args):
        pytest.fail("fatal errors must not back off")

    monkeypatch.setattr(llm_client, "_sleep_with_backoff", unexpected_backoff)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        with SDKAnthropic(api_key="offline-not-a-secret", http_client=http_client) as sdk:
            with pytest.raises(FatalAPIError) as exc:
                AnthropicClient().call(sdk, model="claude-haiku-4-5", max_tokens=10,
                                       system="offline", user="offline")

    assert exc.value.status_code == 401 and len(requests) == 1


GRAMMAR_BODY = {"type": "error", "error": {
    "type": "overloaded_error",
    "message": "Grammar compilation is temporarily unavailable. Please try again."}}
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
          "additionalProperties": False}


def _sdk_with(respond):
    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    return SDKAnthropic(api_key="offline-not-a-secret", http_client=http_client, max_retries=0)


def test_grammar_compilation_outage_has_clear_bounded_error(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503, json=GRAMMAR_BODY)

    monkeypatch.setattr(llm_client, "_sleep_with_backoff", lambda *a: None)
    with _sdk_with(respond) as sdk:
        with pytest.raises(RetryableAPIError) as exc:
            AnthropicClient(max_retries=3).call(
                sdk, model="claude-haiku-4-5", max_tokens=10, system="s", user="u",
                output_schema=SCHEMA)
    assert len(requests) == 3  # bounded, no schema-less fallback
    msg = str(exc.value)
    assert "grammar compilation" in msg and "Anthropic side" in msg and "not a local/Docker" in msg
    assert exc.value.status_code == 503
    assert exc.value.original is not None


def test_other_overloaded_messages_keep_generic_error(monkeypatch):
    def respond(request):
        return httpx.Response(529, json={"type": "error", "error": {
            "type": "overloaded_error", "message": "Overloaded"}})

    monkeypatch.setattr(llm_client, "_sleep_with_backoff", lambda *a: None)
    with _sdk_with(respond) as sdk:
        with pytest.raises(RetryableAPIError) as exc:
            AnthropicClient(max_retries=2).call(
                sdk, model="claude-haiku-4-5", max_tokens=10, system="s", user="u")
    assert "Overloaded" in str(exc.value) and "grammar" not in str(exc.value).lower()


def test_agent_error_carries_grammar_outage_message(monkeypatch):
    monkeypatch.setattr(llm_client, "_sleep_with_backoff", lambda *a: None)
    runner = llm_client.LLMRunner(api_key="sk-ant-test-not-used")
    with _sdk_with(lambda request: httpx.Response(503, json=GRAMMAR_BODY)) as sdk:
        runner._client = sdk
        out = runner.run_agent("classifier", {"language": "a*"})
    assert out["status"] == "agent_error"
    assert "grammar compilation is temporarily unavailable" in out["errors"][0]
    assert "retry the run later" in out["errors"][0]


def test_grammar_outage_as_sse_error_frame_on_a_200_stream(monkeypatch):
    """anthropic 1.x raises APIStatusError(status_code=200) for ``event: error``
    inside an already-open stream; the clear message must still apply."""
    requests = []
    frame = (
        "event: error\n"
        'data: {"type": "error", "error": {"type": "overloaded_error", '
        '"message": "Grammar compilation is temporarily unavailable. Please try again."}}\n\n'
    )

    def respond(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=frame.encode())

    monkeypatch.setattr(llm_client, "_sleep_with_backoff", lambda *a: None)
    with _sdk_with(respond) as sdk:
        with pytest.raises(RetryableAPIError) as exc:
            AnthropicClient(max_retries=3).call(
                sdk, model="claude-haiku-4-5", max_tokens=10, system="s", user="u",
                output_schema=SCHEMA)
    assert len(requests) == 3  # still bounded
    assert exc.value.status_code == 200
    assert "Anthropic side" in str(exc.value) and "not a local/Docker" in str(exc.value)
