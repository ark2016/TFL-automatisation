"""TFL Deep Agent: one autonomous deepagents (LangChain) agent for Theory of
Formal Languages tasks (REG / CFL / DCFL / LL(k)).

A separate approach next to the four pipelines (agent_system, cfl_system,
dcfl_system, ll_system); it imports from them and is never imported by them.
Binding contract: tfl_deep_agent/PLAN.md; architecture:
tfl_deep_agent/docs/architecture.html.
"""

__version__ = "0.0.1"
