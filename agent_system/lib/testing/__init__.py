"""Test doubles shared across every pipeline's test suite (TODO.md §5 M).

Not a `tests/` directory (the root `conftest.py` / `pyproject.toml`
`testpaths` only look there for *collection*), so this package is an
ordinary import: ``from agent_system.lib.testing.fake_anthropic import ...``
works from `agent_system`, `cfl_system`, `dcfl_system` and `ll_system` alike.
"""
