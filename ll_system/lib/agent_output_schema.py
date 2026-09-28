"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output Format" section
(``ll_system/prompts/ll_*.md``) — a superset of the "at least these" subset
``ll_system/tests/test_prompt_contracts.py``'s ``_REQUIRED_OUTPUT_KEYS``
checks (that dict is keyed by prompt file *stem*, e.g. ``"ll_marker_analyzer"``;
this one is keyed by the orchestrator's own ``agent_name``, e.g.
``"marker_analyzer"`` — see ``ll_system/config.py``'s ``PROMPT_FILES``, the
two key spaces differ).

:data:`_FIELD_SCHEMAS` gives the **real** per-key subschema for every agent
whose full contract is actually representable in ``output_config.format``'s
JSON-schema subset (built with ``schema_string`` / ``schema_object`` / etc.
from ``agent_system.lib.llm_client`` — see that module for exactly what the
API accepts and rejects: an empty ``{}`` subschema is rejected outright, and
``additionalProperties: false`` is required at *every* nesting level, not
just the top). Two specialists in :data:`REQUIRED_KEYS` have no entry there
and so get no schema at all (``schema_for`` returns ``None``, same as
``input_parser``): ``ll_grammar_builder`` / ``grammar_transformer`` are the
two specialists that actually *construct* a grammar, and both put a
FIRST/FOLLOW table in their ``artifacts`` (and, for ``grammar_transformer``,
also a parse table in its own ``proof_sketch``) keyed by the grammar's own
nonterminal/terminal symbols — chosen at generation time, so
``additionalProperties: false`` can't express it. The other 4 specialists
never populate that table at all (every worked example shows it ``null``),
so their ``artifacts`` is fully representable.
"""

from __future__ import annotations

from agent_system.lib.llm_client import (
    build_agent_output_schema,
    nullable,
    schema_array,
    schema_boolean,
    schema_null,
    schema_number,
    schema_object,
    schema_string,
    schema_string_array,
)

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

# The specialists that never populate artifacts.first_follow_table /
# artifacts.ll_grammar (every worked example shows both `null` -- these 4
# don't construct a grammar, so there's nothing to put there) -- their
# artifacts field is fully representable.
_NO_GRAMMAR_ARTIFACT_SPECIALISTS = (
    "marker_analyzer", "ambiguity_detector", "prefix_classes_agent", "substitution_agent",
)

# ll_grammar_builder / grammar_transformer -- both DO populate
# artifacts.first_follow_table (keyed by the grammar's own nonterminals)
# and are exempted; see the module docstring.

_ARTIFACTS_NO_GRAMMAR = schema_object({
    "counterexample_words": schema_string_array(),
    "first_follow_table": schema_null(),
    "ll_grammar": schema_null(),
})

_PROOF_SKETCH_BY_SPECIALIST: dict[str, dict] = {
    "ambiguity_detector": schema_object({
        "method": schema_string(enum=["essential_ambiguity"]),
        "witness_word": nullable(schema_string()),
        "essentially_ambiguous": schema_boolean(),
        "two_parse_structures": schema_array(schema_object({
            "structure_id": schema_number(),
            "description": schema_string(),
            "derivation_sketch": schema_string(),
        })),
        "proof_explanation": schema_string(),
        "why_every_grammar_ambiguous": nullable(schema_string()),
        "ogden_used": schema_boolean(),
    }),
    "marker_analyzer": schema_object({
        "method": schema_string(enum=["marker_detection"]),
        "marker_found": schema_boolean(),
        "marker_symbol": nullable(schema_string()),
        "marker_type": nullable(schema_string()),
        "marker_position": nullable(schema_string()),
        "marker_description": schema_string(),
        "ll_usage": nullable(schema_string()),
        "suggested_k": nullable(schema_number()),
    }),
    "prefix_classes_agent": schema_object({
        "method": schema_string(enum=["prefix_classes"]),
        "theorem": schema_string(),
        "dead_class_finite": schema_string(),
        "distinguishing_suffix": nullable(schema_string()),
        "separation_argument": nullable(schema_string()),
        "proof_explanation": schema_string(),
        "for_all_k": schema_boolean(),
        "conclusion": schema_string(),
    }),
    "substitution_agent": schema_object({
        "method": schema_string(enum=["substitution"]),
        "branch_words": nullable(schema_object({
            "common_prefix": schema_string(),
            "word_1": schema_string(),
            "word_2": schema_string(),
            "lookahead_equal_because": schema_string(),
        })),
        "pigeonhole_argument": nullable(schema_string()),
        "proof_explanation": schema_string(),
        "for_all_k": schema_boolean(),
        "common_form_argument": nullable(schema_string()),
        "deciding_nonterminal_argument": nullable(schema_string()),
    }),
}

# Each specialist's own verdict value space -- the 3 "prove not-LL" agents
# (ambiguity_detector, prefix_classes_agent, substitution_agent) only ever
# claim "not_ll"; marker_analyzer (constructs a marker witnessing LL(k))
# only ever claims "ll". All 4 can fall back to "uncertain".
_VERDICT_BY_SPECIALIST = {
    "ambiguity_detector": ["not_ll", "uncertain"],
    "marker_analyzer": ["ll", "uncertain"],
    "prefix_classes_agent": ["not_ll", "uncertain"],
    "substitution_agent": ["not_ll", "uncertain"],
}

_SIX_SPECIALISTS = (
    "ll_grammar_builder", "marker_analyzer", "grammar_transformer",
    "substitution_agent", "ambiguity_detector", "prefix_classes_agent",
)


def _specialist_schema(agent_name: str) -> dict[str, dict]:
    return {
        "agent_name": schema_string(enum=[agent_name]),
        "verdict": schema_string(enum=_VERDICT_BY_SPECIALIST[agent_name]),
        "confidence": schema_number(),
        "proof_sketch": _PROOF_SKETCH_BY_SPECIALIST[agent_name],
        "artifacts": _ARTIFACTS_NO_GRAMMAR,
        "errors": schema_string_array(),
    }


_FIELD_SCHEMAS: dict[str, dict[str, dict]] = {
    **{name: _specialist_schema(name) for name in _NO_GRAMMAR_ARTIFACT_SPECIALISTS},
    "classifier": {
        "prediction": schema_string(enum=["ll", "not_ll", "uncertain"]),
        "confidence": schema_number(),
        "reasoning": schema_string(),
        "suggested_methods": schema_string_array(),
        "suggested_k": nullable(schema_number()),
        "advisory_only": schema_boolean(),
    },
    "reasoning_agent": {
        "verdict": schema_string(enum=["ll", "not_ll", "uncertain"]),
        "k": nullable(schema_number()),
        "confidence": schema_number(),
        "summary": schema_string(),
        "justification": schema_string(),
        "primary_method": schema_string(enum=[
            "substitution", "ll_grammar_construction", "marker_detection", "grammar_transformation",
        ]),
        "primary_agent": schema_string(enum=list(_SIX_SPECIALISTS)),
        "supporting_agents": schema_string_array(),
        "contradictions": schema_array(schema_object({
            "agents": schema_string_array(),
            "description": schema_string(),
            "resolution": schema_string(),
        })),
        "action": schema_string(enum=["done", "retry"]),
        "retry_plan": nullable(schema_object({
            "agents_to_retry": schema_string_array(),
            "hints": schema_object(
                {name: schema_string() for name in _SIX_SPECIALISTS}, required=[],
            ),
        })),
        "hints_for_human": schema_string_array(),
        "errors": schema_string_array(),
    },
    "formalizer": {
        "agent_name": schema_string(enum=["formalizer"]),
        "status": schema_string(enum=["success", "failure"]),
        "markdown_solution": schema_string(),
        # "Lean formalization is out of scope for this phase" -- the
        # prompt says to set this to null always.
        "lean_sketch": schema_null(),
        "confidence": schema_number(),
        "errors": schema_string_array(),
    },
}


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract, or its contract
    has a dynamically-keyed field the API's closed schema can't express
    (caller keeps the legacy "extract JSON from prose" path either way)."""
    properties = _FIELD_SCHEMAS.get(agent_name)
    if properties is None:
        return None
    return build_agent_output_schema(properties)
