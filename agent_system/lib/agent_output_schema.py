"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

Each entry in :data:`REQUIRED_KEYS` is the *exhaustive* top-level key set of
one agent's own prompt contract, read off its "## Output Format" worked
example in ``agent_system/prompts/<name>.md`` — not a partial "at least
these" subset. :func:`build_agent_output_schema` (in ``llm_client``) turns
that into a **closed** schema (``additionalProperties: False``, every key
required): the API constrains generation to match it, so an exhaustive key
set is required, not just convenient — leaving one out would silently make
the model drop it. Nested content (``dfa``, ``proof``, ``evidence``, ...)
stays completely free-form; only the envelope shape is fixed.

Keep this in sync by hand when a prompt's "## Output Format" section
changes its top-level keys — ``agent_system/tests/test_agent_output_schema.py``
checks every entry here against the real prompt file, so a drift fails loud
instead of silently forcing the model to drop a field.
"""

from __future__ import annotations

from agent_system.lib.llm_client import build_agent_output_schema

REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "classifier": frozenset({
        "module", "status", "verdict", "dispatch", "hard_rule_applied",
        "reasoning", "confidence",
    }),
    "closure_agent": frozenset({
        "module", "status", "conclusion", "confidence", "details", "errors", "method",
    }),
    "dfa_builder": frozenset({
        "module", "status", "dfa", "state_descriptions", "explanation",
        "confidence", "errors",
    }),
    "grammar_analyzer": frozenset({
        "module", "status", "is_regular", "analysis", "evidence", "confidence", "errors",
    }),
    "nerode_agent": frozenset({"module", "status", "proof", "confidence", "errors"}),
    "proof_checker": frozenset({"checks", "critical_errors", "overall_valid", "suggestions"}),
    "pumping_agent": frozenset({"module", "status", "proof", "confidence", "errors"}),
    "re_builder": frozenset({
        "module", "status", "regex", "explanation", "test_words", "confidence", "errors",
    }),
    "reasoning_agent": frozenset({
        "module", "status", "verdict", "action", "best_proof", "consolidated_proof",
        "issues_found", "oracle_validation", "retry_context", "hints_for_human",
        "confidence", "errors",
    }),
    "retry_planner": frozenset({
        "agents_to_retry", "feedback", "reasoning", "should_invert_hypothesis", "skip_agents",
    }),
}

# input_parser's output shape depends on the IR `kind` it produces (no
# single fixed key set); formalizer returns raw Lean text, not JSON
# (`LLMRunner._RAW_TEXT_AGENTS`). Neither ever gets a closed output_config
# schema -- `schema_for` returns None for both and the caller keeps the
# legacy prose-extraction path.
_NO_FIXED_CONTRACT = frozenset({"input_parser", "formalizer"})


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract (caller keeps the
    legacy "extract JSON from prose" path)."""
    keys = REQUIRED_KEYS.get(agent_name)
    if not keys:
        return None
    return build_agent_output_schema(keys)
