"""Root pytest configuration.

Safety net so that a bare ``pytest`` invoked from the repository root never
makes a real, billed call to the Anthropic API. ``pyproject.toml``'s
``[tool.pytest.ini_options] testpaths`` already keeps collection to the four
pipelines' own ``tests/`` directories (and the top-level ``tests/``),
skipping ``pumping_lemma/tests`` (which calls the real API) — but a test
inside those directories could still end up constructing a live
``anthropic.Anthropic()`` client by accident (a missing ``--mock``, a stray
``--live`` code path, a new test that forgets to stub the runner). This
autouse fixture is the last line of defense: for the whole pytest session it
removes ``ANTHROPIC_API_KEY`` from the environment and replaces
``anthropic.Anthropic`` / ``anthropic.AsyncAnthropic`` with a stub that
accepts any constructor call (so code that only builds request kwargs, like
the ``test_request_params`` suites, keeps working) and raises
``RuntimeError`` the moment something actually tries to reach the network
via ``.messages.create`` / ``.messages.stream``.

Set ``TFL_ALLOW_LIVE=1`` to disable this fixture for an intentional live run
started through pytest (e.g. ``TFL_ALLOW_LIVE=1 pytest -k some_live_test``).
It has no effect on live runs started via the CLI (``--live``) — those never
import this file at all.
"""
from __future__ import annotations

import os

import pytest


class _LiveCallBlocked(RuntimeError):
    pass


def _refuse(*_args, **_kwargs):
    raise _LiveCallBlocked(
        "Real Anthropic API calls are disabled under pytest (see conftest.py). "
        "Use --mock / a scripted runner / MagicMock for the client, or set "
        "TFL_ALLOW_LIVE=1 for an intentional live run through pytest."
    )


class _StubAnthropicClient:
    """Drop-in for ``anthropic.Anthropic`` / ``anthropic.AsyncAnthropic``.

    Constructs without error on any args/kwargs (api_key, base_url, ...), so
    existing tests that build a real runner (``LiveRunner(api_key=...)``,
    ``LLMRunner(api_key=...)``) just to exercise ``_build_request_kwargs`` or
    similar pure logic keep passing. Only the two call sites that would reach
    the network — ``.messages.create`` and ``.messages.stream`` — raise.
    """

    def __init__(self, *args, **kwargs):
        pass

    @property
    def messages(self):
        return self

    create = staticmethod(_refuse)
    stream = staticmethod(_refuse)

    def __getattr__(self, name):
        # Any other attribute access (e.g. .beta) hands back another stub
        # instance rather than AttributeError, so chained access before the
        # terminal network call doesn't fail with a confusing error.
        return _StubAnthropicClient()


@pytest.fixture(scope="session", autouse=True)
def _block_live_anthropic_calls():
    if os.environ.get("TFL_ALLOW_LIVE") == "1":
        yield
        return

    import anthropic

    had_key = "ANTHROPIC_API_KEY" in os.environ
    old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    old_sync, old_async = anthropic.Anthropic, anthropic.AsyncAnthropic
    anthropic.Anthropic = _StubAnthropicClient
    anthropic.AsyncAnthropic = _StubAnthropicClient
    try:
        yield
    finally:
        anthropic.Anthropic, anthropic.AsyncAnthropic = old_sync, old_async
        if had_key:
            os.environ["ANTHROPIC_API_KEY"] = old_key
