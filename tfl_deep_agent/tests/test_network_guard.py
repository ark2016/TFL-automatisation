"""A chat model built in tests cannot reach the network.

Layers: the root conftest strips ANTHROPIC_API_KEY and stubs
anthropic.Anthropic / AsyncAnthropic; tfl_deep_agent/tests/conftest.py points
the anthropic.Client / AsyncClient aliases (the names langchain-anthropic
actually calls) at the same stub. Any model call then raises before a socket
is opened.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("langchain_anthropic")

import anthropic
import anthropic._client as real_client
from langchain_core.messages import HumanMessage

from tfl_deep_agent.models import make_chat_model

pytestmark = pytest.mark.skipif(
    os.environ.get("TFL_ALLOW_LIVE") == "1", reason="guards are off under TFL_ALLOW_LIVE=1",
)

BLOCKED = "disabled under pytest"


def test_api_key_is_stripped():
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_every_sdk_client_name_is_the_stub():
    for name in ("Anthropic", "AsyncAnthropic", "Client", "AsyncClient"):
        cls = getattr(anthropic, name)
        assert cls is not getattr(real_client, name), name
    assert anthropic.Client is anthropic.Anthropic
    assert anthropic.AsyncClient is anthropic.AsyncAnthropic


def test_sync_invoke_is_blocked():
    llm = make_chat_model(role="main", api_key="test")
    with pytest.raises(RuntimeError, match=BLOCKED):
        llm.invoke([HumanMessage("hi")])


def test_async_invoke_is_blocked():
    llm = make_chat_model(role="intake", api_key="test")
    with pytest.raises(RuntimeError, match=BLOCKED):
        asyncio.run(llm.ainvoke([HumanMessage("hi")]))


def test_model_without_explicit_key_is_blocked_too():
    llm = make_chat_model(role="coder")
    with pytest.raises(RuntimeError, match=BLOCKED):
        llm.invoke([HumanMessage("hi")])


def test_deep_agent_on_a_real_chat_model_is_blocked():
    """deepagents' own graph, given a ChatAnthropic, hits the stub on its first call."""
    deepagents = pytest.importorskip("deepagents")
    agent = deepagents.create_deep_agent(model=make_chat_model(role="main", api_key="test"))
    with pytest.raises(RuntimeError, match=BLOCKED):
        agent.invoke({"messages": [HumanMessage("hi")]})


def test_env_loaders_cannot_put_the_key_back():
    """llm_client._load_env (called by LLMRunner.__init__) copies agent_system/.env
    into os.environ; the per-test guard makes it, and dotenv.load_dotenv, no-ops."""
    from agent_system.lib import llm_client

    llm_client._load_env()
    assert "ANTHROPIC_API_KEY" not in os.environ
    try:
        import dotenv
    except ImportError:
        return
    from pathlib import Path

    dotenv.load_dotenv(Path(llm_client.__file__).resolve().parents[2] / ".env")
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_key_strip_runs_before_every_test(request):
    """The root conftest strips the key once per session; earlier tests in the
    same process (any agent_system test that builds LLMRunner) put it back. The
    function-scoped guard must be active for every test here, not just once.
    Repro of the original failure: run agent_system/tests/test_llm_request_params.py
    before this file in one process."""
    assert "_no_live_anthropic_key" in request.fixturenames
    defs = request._fixturemanager.getfixturedefs("_no_live_anthropic_key", request.node)
    assert defs and defs[-1].scope == "function"


def test_other_test_dirs_that_use_the_agent_import_the_guard():
    """Test directories outside the package (ui_server/tests in P7, tfl_eval/tests
    in P8, ...) that build a ChatAnthropic must load guard.py's fixtures: the root
    conftest does not stub anthropic.Client / AsyncClient."""
    import re
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    testpaths = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["pytest"]["ini_options"]["testpaths"]
    uses_agent = re.compile(r"\b(tfl_deep_agent|langchain_anthropic|ChatAnthropic)\b")
    missing = []
    for tp in testpaths:
        d = root / tp
        if tp == "tfl_deep_agent/tests" or not d.is_dir():
            continue
        users = [p for p in d.rglob("*.py") if p.name != "conftest.py"
                 and uses_agent.search(p.read_text(encoding="utf-8", errors="replace"))]
        if not users:
            continue
        conftest = d / "conftest.py"
        text = conftest.read_text(encoding="utf-8") if conftest.exists() else ""
        if "tfl_deep_agent.tests.guard" not in text:
            missing.append((tp, [p.name for p in users]))
    assert not missing, (
        "these test dirs use tfl_deep_agent / langchain-anthropic but their conftest.py "
        f"does not import tfl_deep_agent.tests.guard: {missing}")
