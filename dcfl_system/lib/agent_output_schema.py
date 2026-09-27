"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output format" section
(``dcfl_system/prompts/*.md``) — the 5 specialists' shared set mirrors
``dcfl_system/tests/test_prompt_contracts.py``'s ``REQUIRED_OUTPUT_KEYS``.
:func:`build_agent_output_schema` (in ``agent_system.lib.llm_client``) turns
it into a **closed** schema (``additionalProperties: False``, every key
required): the API constrains generation to match it, so this must stay
exhaustive, not just "at least these" — leaving a key out would silently
make the model drop it. Nested content (``evidence``, ``proof_sketch``, ...)
stays completely free-form; only the envelope shape is fixed.
"""

from __future__ import annotations

from agent_system.lib.llm_client import build_agent_output_schema

# Mirrors dcfl_system.orchestrator.DCFL_SPECIALIST_NAMES -- not imported
# from there to avoid a circular import (orchestrator imports schema_for
# from this module).
_SPECIALISTS = ("stack_strategy", "closure_reduction", "dcfl_pumping", "shallit", "inh_ambiguity")

# All 5 specialists share the same output shape.
_SPECIALIST_KEYS = frozenset({
    "agent_name", "status", "verdict", "proof_sketch", "evidence", "confidence", "errors",
})

REQUIRED_KEYS: dict[str, frozenset[str]] = {
    **{name: _SPECIALIST_KEYS for name in _SPECIALISTS},
    "classifier": frozenset({"verdict", "confidence", "reasoning", "suggested_methods"}),
    "reasoning": frozenset({
        "action", "verdict", "confidence", "primary_evidence", "summary",
        "retry_plan", "hints_for_human", "errors",
    }),
}

# input_parser's output shape depends on the task IR it produces -- no
# single fixed key set, so it never gets a closed output_config schema.
_NO_FIXED_CONTRACT = frozenset({"input_parser"})


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract (caller keeps the
    legacy "extract JSON from prose" path)."""
    keys = REQUIRED_KEYS.get(agent_name)
    if not keys:
        return None
    return build_agent_output_schema(keys)
