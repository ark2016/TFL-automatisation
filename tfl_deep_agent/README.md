# tfl_deep_agent

One autonomous [deepagents](https://pypi.org/project/deepagents/) agent for Theory of Formal Languages
tasks (REG / CFL / DCFL / LL(k)), next to the four pipelines. Contract: `PLAN.md`; architecture:
`docs/architecture.html`; environment and phase notes: `DEV_NOTES.md`.

Install: `pip install -e ".[deep,dev]"`. Tests: `python -m pytest -q tfl_deep_agent/tests` (no API calls).
