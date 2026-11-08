"""Network / credential guard fixtures for any test that may build a ChatAnthropic.

Two layers on top of the root ``conftest.py`` (which strips ANTHROPIC_API_KEY
once at session start and stubs ``anthropic.Anthropic`` / ``AsyncAnthropic``):

* ``_block_langchain_anthropic_clients`` (session, autouse): langchain-anthropic
  builds its SDK client through ``anthropic.Client`` / ``anthropic.AsyncClient``,
  module aliases that the root stub does not replace. This points them at the
  same stub.
* ``_no_live_anthropic_key`` (function, autouse): the key strip in the root
  conftest happens once, and code that runs later in the same process puts the
  key back -- ``LLMRunner.__init__`` calls ``llm_client._load_env()``, which
  copies the root ``.env`` into ``os.environ``; the pipeline orchestrators
  call ``dotenv.load_dotenv``. So every test starts with the key removed and
  both loaders turned into no-ops (monkeypatch restores them afterwards).

``TFL_ALLOW_LIVE=1`` disables both, as it does the root fixture.

``tfl_deep_agent/tests/conftest.py`` imports these. A test directory outside the
package that builds a ChatAnthropic (ui_server/tests in P7, tfl_eval/tests in
P8) must import them into its own conftest::

    from tfl_deep_agent.tests.guard import (  # noqa: F401  (autouse fixtures)
        _block_langchain_anthropic_clients, _no_live_anthropic_key,
    )

``test_network_guard.py::test_other_test_dirs_that_use_the_agent_import_the_guard``
fails until they do. (Adding the two aliases to the root conftest would be the
one-place fix; that file is outside the PLAN's allowed list and needs the
user's approval.)
"""

from __future__ import annotations

import os

import pytest


def _live_allowed() -> bool:
    return os.environ.get("TFL_ALLOW_LIVE") == "1"


@pytest.fixture(scope="session", autouse=True)
def _block_langchain_anthropic_clients(_block_live_anthropic_calls):
    if _live_allowed():
        yield
        return
    import anthropic

    old_client, old_async = anthropic.Client, anthropic.AsyncClient
    # After the root fixture these are the stub class.
    anthropic.Client = anthropic.Anthropic
    anthropic.AsyncClient = anthropic.AsyncAnthropic
    try:
        yield
    finally:
        anthropic.Client, anthropic.AsyncClient = old_client, old_async


@pytest.fixture(autouse=True)
def _no_live_anthropic_key(monkeypatch):
    if _live_allowed():
        return
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from agent_system.lib import llm_client

    monkeypatch.setattr(llm_client, "_load_env", lambda: None)
    try:
        import dotenv
    except ImportError:
        return
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
