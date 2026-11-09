"""tfl_eval tests: test_deep_adapter.py runs tfl_deep_agent (scripted chat
models only). The guard also blocks langchain-anthropic's anthropic.Client /
AsyncClient and strips ANTHROPIC_API_KEY before every test
(tfl_deep_agent/tests/guard.py)."""

from tfl_deep_agent.tests.guard import (  # noqa: F401  (autouse fixtures)
    _block_langchain_anthropic_clients,
    _no_live_anthropic_key,
)
