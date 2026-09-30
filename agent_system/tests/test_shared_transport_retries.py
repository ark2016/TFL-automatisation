"""Exercise the real SDK over an in-memory HTTP transport; no network or key lookup."""

import httpx
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
