"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output Format" section
(``cfl_system/prompts/*.md``) — the canonical source
``cfl_system/tests/test_prompt_contracts.py`` also checks worked examples
against. :func:`build_agent_output_schema` (in
``agent_system.lib.llm_client``) turns it into a **closed** schema
(``additionalProperties: False``, every key required): the API constrains
generation to match it, so this must stay exhaustive, not just "at least
these" — leaving a key out would silently make the model drop it. Nested
content (``evidence``, ``proof_document``, ...) stays completely free-form;
only the envelope shape is fixed.
"""

from __future__ import annotations

from agent_system.lib.llm_client import build_agent_output_schema

# Every specialist shares the same output shape (docs/TODO.md §3/§6).
_SPECIALIST_KEYS = frozenset({"agent", "status", "verdict", "evidence", "confidence", "errors"})

REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "cfg_builder": _SPECIALIST_KEYS,
    "pda_builder": _SPECIALIST_KEYS,
    "decomposition": _SPECIALIST_KEYS,
    "parikh": _SPECIALIST_KEYS,
    "pumping_cfl": _SPECIALIST_KEYS,
    "ogden": _SPECIALIST_KEYS,
    "closure_reduction": _SPECIALIST_KEYS,
    "interchange": _SPECIALIST_KEYS,
    "morphism": _SPECIALIST_KEYS,
    "classifier": frozenset({"verdict", "confidence", "reasoning"}),
    "reasoning": frozenset({
        "agent", "decision", "verdict", "confidence", "primary_evidence",
        "supporting_evidence", "contradictions", "summary",
        "primary_justification", "retry_plan", "hints_for_human", "errors",
    }),
    "retry_planner": frozenset({
        "agent", "agents_to_retry", "skip_agents", "hints",
        "max_retries_remaining", "should_invert_hypothesis", "reasoning",
    }),
    "proof_checker": frozenset({
        "agent", "status", "checks", "verified_proofs", "issues",
        "overall_assessment", "errors",
    }),
    "formalizer": frozenset({"agent", "status", "proof_document", "confidence", "errors"}),
}

# input_parser's output shape depends on the IR "kind" (grammar / regex /
# set_builder / predicate / bounded / ...) -- no single fixed key set, so it
# never gets a closed output_config schema (matches
# cfl_system/tests/test_prompt_contracts.py's `_NO_FIXED_CONTRACT`).
_NO_FIXED_CONTRACT = frozenset({"input_parser"})


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract (caller keeps the
    legacy "extract JSON from prose" path)."""
    keys = REQUIRED_KEYS.get(agent_name)
    if not keys:
        return None
    return build_agent_output_schema(keys)
