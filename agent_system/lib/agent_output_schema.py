"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

Each entry in :data:`REQUIRED_KEYS` is the *exhaustive* top-level key set of
one agent's own prompt contract, read off its "## Output Format" worked
example in ``agent_system/prompts/<name>.md`` — not a partial "at least
these" subset. ``agent_system/tests/test_agent_output_schema.py`` checks
every entry here against the real prompt file, so a drift fails loud
instead of silently forcing the model to drop a field.

:data:`_FIELD_SCHEMAS` gives the **real** per-key subschema for every agent
whose full contract is actually representable in ``output_config.format``'s
JSON-schema subset (built with ``schema_string`` / ``schema_object`` / etc.
from ``llm_client`` — see that module for exactly what the API accepts and
rejects). Two agents in :data:`REQUIRED_KEYS` — ``closure_agent`` and
``dfa_builder`` — have no entry there and so get no schema at all
(:func:`schema_for` returns ``None``, same as ``input_parser``): both
documate a nested field that's a map keyed by something chosen at
*generation* time —

- ``dfa_builder``'s ``dfa.transitions`` (and ``state_descriptions``) is
  keyed by the DFA's own state names (``q0``, ``q1``, ...), which differ
  per DFA;
- ``closure_agent``'s ``details`` is one of four method-specific shapes
  (picked by its own ``method`` field), and two of the four —
  ``erasing_homomorphism`` and ``inverse_homomorphism`` — nest a
  ``homomorphism.mapping`` keyed by the language's own alphabet symbols.

``additionalProperties: false`` is required at every nesting level (TODO.md
§3 M / ``llm_client.build_agent_output_schema``), so a map like that cannot
be expressed without either forbidding the very keys the model needs to
emit, or arbitrarily restricting which of the documented shapes the model
is allowed to produce (dropping 2 of ``closure_agent``'s 4 methods, or
inventing a fixed state-name vocabulary no real DFA is bound to use) —
either of which would be a silent behavior change, not a schema fix. Both
agents keep the legacy "extract JSON from prose" path instead.
"""

from __future__ import annotations

from agent_system.lib.llm_client import (
    build_agent_output_schema,
    nullable,
    schema_array,
    schema_boolean,
    schema_number,
    schema_object,
    schema_string,
    schema_string_array,
)

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

# The 6 specialist agents `classifier.dispatch` / `retry_planner.feedback`
# can name (graph.py's SPECIALIST_NAMES) -- a fixed, known set, unlike the
# dynamic maps in the module docstring above.
_SPECIALISTS = ("dfa_builder", "re_builder", "pumping", "nerode", "closure", "grammar_analyzer")

_GRAMMAR = schema_object({
    "start": schema_string(),
    "nonterminals": schema_string_array(),
    "terminals": schema_string_array(),
    "rules": schema_array(schema_object({
        "lhs": schema_string(),
        "rhs": schema_string_array(),
    })),
})

_FIELD_SCHEMAS: dict[str, dict[str, dict]] = {
    "classifier": {
        "module": schema_string(enum=["classifier"]),
        "status": schema_string(enum=["success"]),
        "verdict": schema_string(enum=["regular", "non_regular"]),
        "confidence": schema_number(),
        "reasoning": schema_string(),
        "dispatch": schema_object({name: schema_boolean() for name in _SPECIALISTS}),
        # if a hard rule triggered (e.g. "backreferences", "all_atoms_finite"),
        # its name; otherwise null.
        "hard_rule_applied": nullable(schema_string()),
    },
    "grammar_analyzer": {
        "module": schema_string(enum=["grammar_analyzer"]),
        "status": schema_string(enum=["success", "failure"]),
        "is_regular": schema_boolean(),
        "analysis": schema_object({
            "is_left_linear": schema_boolean(),
            "is_right_linear": schema_boolean(),
            "recursion_type": schema_string(),
            "recursion_details": schema_string(),
            "generated_words": schema_string_array(),
        }),
        # optional supporting hints when is_regular -- no single sub-key is
        # always present (a construction sketch, the RE "type", a regex).
        "evidence": schema_object(
            {
                "construction": schema_string(),
                "type": schema_string(),
                "regex": schema_string(),
            },
            required=[],
        ),
        "confidence": schema_number(),
        "errors": nullable(schema_string_array()),
    },
    "nerode_agent": {
        "module": schema_string(enum=["nerode_agent"]),
        "status": schema_string(enum=["success", "failure"]),
        "proof": nullable(schema_object({
            "word_sequence": schema_object({
                "family": schema_string(),
                "parameter": schema_string(),
                "domain": schema_string(),
                "examples": schema_string_array(),
            }),
            "distinguishing_contexts": schema_array(schema_object({
                "pair": schema_string_array(),
                "context": schema_string(),
                "which_in": schema_string(),
                "in_language": schema_string(),
                "not_in_language": schema_string(),
                "condition": schema_string(),
            })),
            "argument": schema_string(),
            "conclusion": schema_string(),
        })),
        "confidence": schema_number(),
        "errors": nullable(schema_string_array()),
    },
    "proof_checker": {
        "checks": schema_array(schema_object({
            "agent": schema_string(),
            "claim": schema_string(),
            "verdict": schema_string(),
            "severity": nullable(schema_string()),
            "error": nullable(schema_string()),
        })),
        "overall_valid": schema_boolean(),
        "critical_errors": schema_string_array(),
        "suggestions": schema_string_array(),
    },
    "pumping_agent": {
        "module": schema_string(enum=["pumping_agent"]),
        "status": schema_string(enum=["success", "failure"]),
        "proof": nullable(schema_object({
            "word_choice": schema_object({
                "word": schema_string(),
                "word_parameterized": schema_boolean(),
                "parameter": schema_string(),
                "membership_argument": schema_string(),
            }),
            "length_argument": schema_string(),
            "cut_analysis": schema_object({
                "method": schema_string(),
                "argument": schema_string(),
                "cases": schema_array(schema_object({
                    "case": schema_string(),
                    "pumped_word": schema_string(),
                    "pump_value": schema_number(),
                    "contradiction": schema_string(),
                })),
            }),
            "pump_value": schema_number(),
            "conclusion": schema_string(),
        })),
        "confidence": schema_number(),
        "errors": nullable(schema_string_array()),
    },
    "re_builder": {
        "module": schema_string(enum=["re_builder"]),
        "status": schema_string(enum=["success", "failure"]),
        "regex": nullable(schema_string()),
        "explanation": schema_string(),
        "test_words": nullable(schema_object({
            "accepted": schema_string_array(),
            "rejected": schema_string_array(),
        })),
        "confidence": schema_number(),
        "errors": nullable(schema_string_array()),
    },
    "reasoning_agent": {
        "module": schema_string(enum=["reasoning_agent"]),
        "status": schema_string(enum=["success", "partial"]),
        "verdict": schema_string(enum=["regular", "non_regular"]),
        "confidence": schema_number(),
        "best_proof": nullable(schema_string(
            enum=["pumping", "nerode", "closure", "re_builder", "dfa_builder", "grammar_analyzer"],
        )),
        "consolidated_proof": schema_string(),
        "oracle_validation": schema_string(),
        "issues_found": schema_string_array(),
        "action": schema_string(enum=[
            "proceed_to_formalizer", "retry_enriched", "invert_hypothesis", "escalate",
        ]),
        # the enriched context sent back to specialists on a
        # "retry_enriched" round; null otherwise.
        "retry_context": nullable(schema_object({
            "target_agents": schema_string_array(),
            "counterexample": schema_object({
                "word": schema_string(),
                "expected": schema_boolean(),
                "got": schema_boolean(),
            }),
            "message": schema_string(),
        })),
        "hints_for_human": schema_string_array(),
        "errors": nullable(schema_string_array()),
    },
    "retry_planner": {
        "agents_to_retry": schema_string_array(),
        "skip_agents": schema_string_array(),
        "feedback": schema_object(
            {name: schema_string() for name in _SPECIALISTS}, required=[],
        ),
        "should_invert_hypothesis": schema_boolean(),
        "reasoning": schema_string(),
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
