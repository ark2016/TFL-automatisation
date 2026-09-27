"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output Format" section
(``ll_system/prompts/ll_*.md``) — a superset of the "at least these" subset
``ll_system/tests/test_prompt_contracts.py``'s ``_REQUIRED_OUTPUT_KEYS``
checks (that dict is keyed by prompt file *stem*, e.g. ``"ll_marker_analyzer"``;
this one is keyed by the orchestrator's own ``agent_name``, e.g.
``"marker_analyzer"`` — see ``ll_system/config.py``'s ``PROMPT_FILES``, the
two key spaces differ). :func:`build_agent_output_schema` (in
``agent_system.lib.llm_client``) turns it into a **closed** schema
(``additionalProperties: False``, every key required): the API constrains
generation to match it, so this must stay exhaustive, not just "at least
these" — leaving a key out would silently make the model drop it. Nested
content (``proof_sketch``, ``artifacts``, ...) stays completely free-form;
only the envelope shape is fixed.
"""

from __future__ import annotations

from agent_system.lib.llm_client import build_agent_output_schema

# The 6 specialists (ll_grammar_builder, marker_analyzer, grammar_transformer,
# substitution_agent, ambiguity_detector, prefix_classes_agent) share the
# same output shape.
_SPECIALIST_KEYS = frozenset({
    "agent_name", "verdict", "confidence", "proof_sketch", "artifacts", "errors",
})

REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "ll_grammar_builder": _SPECIALIST_KEYS,
    "marker_analyzer": _SPECIALIST_KEYS,
    "grammar_transformer": _SPECIALIST_KEYS,
    "substitution_agent": _SPECIALIST_KEYS,
    "ambiguity_detector": _SPECIALIST_KEYS,
    "prefix_classes_agent": _SPECIALIST_KEYS,
    "classifier": frozenset({
        "prediction", "confidence", "reasoning", "suggested_methods",
        "suggested_k", "advisory_only",
    }),
    "reasoning_agent": frozenset({
        "verdict", "k", "confidence", "summary", "justification",
        "primary_method", "primary_agent", "supporting_agents",
        "contradictions", "action", "retry_plan", "hints_for_human", "errors",
    }),
    "formalizer": frozenset({
        "agent_name", "status", "markdown_solution", "lean_sketch", "confidence", "errors",
    }),
}

# input_parser's output shape depends on which of the 3 input formats it
# detects -- no single fixed key set, so it never gets a closed
# output_config schema.
_NO_FIXED_CONTRACT = frozenset({"input_parser"})


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract (caller keeps the
    legacy "extract JSON from prose" path)."""
    keys = REQUIRED_KEYS.get(agent_name)
    if not keys:
        return None
    return build_agent_output_schema(keys)
