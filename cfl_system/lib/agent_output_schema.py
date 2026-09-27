"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output Format" section
(``cfl_system/prompts/*.md``) — the canonical source
``cfl_system/tests/test_prompt_contracts.py`` also checks worked examples
against.

:data:`_FIELD_SCHEMAS` gives the **real** per-key subschema for every agent
whose full contract is actually representable in ``output_config.format``'s
JSON-schema subset (built with ``schema_string`` / ``schema_object`` / etc.
from ``agent_system.lib.llm_client`` — see that module for exactly what the
API accepts and rejects: an empty ``{}`` subschema is rejected outright, and
``additionalProperties: false`` is required at *every* nesting level, not
just the top). Three specialists in :data:`REQUIRED_KEYS` have no entry
there and so get no schema at all (``schema_for`` returns ``None``, same as
``input_parser``): each documents a nested field that's a map keyed by
something chosen at *generation* time, which a closed schema can't express
without forbidding the very keys the model needs to emit —

- ``pumping_cfl`` / ``ogden``: ``evidence.word_instances`` (and, for
  ``ogden``, ``evidence.marked_positions``) is keyed by the pumping
  multipliers the agent itself chooses (``"3"``, ``"4"``, ...);
- ``morphism``: ``evidence.morphism.mapping`` is keyed by the language's
  own alphabet symbols.
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

# The 9 specialists `retry_planner.hints` / `reasoning.retry_plan.hints` can
# target -- a fixed, known set, unlike the dynamic maps in the module
# docstring above.
_SPECIALISTS = (
    "cfg_builder", "pda_builder", "decomposition", "parikh", "pumping_cfl",
    "ogden", "closure_reduction", "interchange", "morphism",
)

_GRAMMAR = schema_object({
    "start": schema_string(),
    "nonterminals": schema_string_array(),
    "terminals": schema_string_array(),
    "rules": schema_array(schema_object({
        "lhs": schema_string(),
        "rhs": schema_string_array(),
    })),
})

_PDA = schema_object({
    "states": schema_string_array(),
    "input_alphabet": schema_string_array(),
    "stack_alphabet": schema_string_array(),
    "accept_states": schema_string_array(),
    "start_state": schema_string(),
    "start_stack": schema_string(),
    "transitions": schema_array(schema_object({
        "from": schema_string(),
        "stack_top": schema_string(),
        "input": nullable(schema_string()),
        "push": schema_string_array(),
        "to": schema_string(),
    })),
})

# retry_planner.hints / reasoning.retry_plan.hints: one per-specialist hint
# object, all fields optional since different specialists' hints carry
# different subsets (a suggested regex only makes sense for
# closure_reduction, a suggested word only for pumping_cfl, ...).
_RETRY_HINT = schema_object(
    {
        "hint": schema_string(),
        "strategy": schema_string(),
        "avoid": schema_string_array(),
        "suggested_regex": schema_string(),
        "suggested_word": schema_string(),
        "counterexample": schema_object({
            "word": schema_string(),
            "expected": schema_boolean(),
            "got": schema_boolean(),
        }),
    },
    required=[],
)
_RETRY_HINTS_MAP = schema_object(
    {name: _RETRY_HINT for name in _SPECIALISTS}, required=[],
)


def _specialist_schema(agent: str, verdict: dict, evidence_properties: dict, *,
                        evidence_required: list[str] | None = None) -> dict[str, dict]:
    """The common specialist envelope (docs/TODO.md §3/§6) with an
    agent-specific ``verdict`` value space and ``evidence`` shape.
    ``evidence`` itself is nullable — every specialist's failure/
    inconclusive worked example sets it to ``null``."""
    return {
        "agent": schema_string(enum=[agent]),
        "status": schema_string(enum=["success", "failure", "inconclusive"]),
        "verdict": verdict,
        "evidence": nullable(schema_object(evidence_properties, required=evidence_required)),
        "confidence": schema_number(),
        "errors": schema_string_array(),
    }


_CFL_VERDICT = nullable(schema_string(enum=["cfl"]))
_NON_CFL_VERDICT = nullable(schema_string(enum=["non_cfl"]))

_FIELD_SCHEMAS: dict[str, dict[str, dict]] = {
    "cfg_builder": _specialist_schema(
        "cfg_builder", _CFL_VERDICT,
        {
            "explanation": schema_string(),
            "grammar": _GRAMMAR,
            "sample_derivations": schema_array(schema_object({
                "word": schema_string(),
                "derivation": schema_string(),
            })),
            "strategy_used": schema_string(),
        },
        evidence_required=["explanation", "grammar"],
    ),
    "pda_builder": _specialist_schema(
        "pda_builder", _CFL_VERDICT,
        {
            "acceptance_mode": schema_string(),
            "explanation": schema_string(),
            "pda": _PDA,
            "sample_runs": schema_array(schema_object({
                "word": schema_string(),
                "trace": schema_string(),
            })),
        },
    ),
    "decomposition": _specialist_schema(
        "decomposition", _CFL_VERDICT,
        {
            "operation": schema_string(),
            "decomposition_type": schema_string(),
            "explanation": schema_string(),
            "conclusion": schema_string(),
            "components": schema_array(schema_object(
                {
                    "name": schema_string(),
                    "description": schema_string(),
                    "cfl_justification": schema_string(),
                    "is_cfl": schema_boolean(),
                    "grammar": _GRAMMAR,
                },
                required=["cfl_justification", "description", "is_cfl", "name"],
            )),
        },
    ),
    "parikh": _specialist_schema(
        "parikh", _NON_CFL_VERDICT,
        {
            "commutative_image": schema_string(),
            "is_semilinear": schema_boolean(),
            "semilinear_representation": nullable(schema_string()),
            "conclusion": schema_string(),
            "explanation": schema_string(),
        },
    ),
    "closure_reduction": _specialist_schema(
        "closure_reduction", _NON_CFL_VERDICT,
        {
            "regular_language": schema_string(),
            "regular_language_regex": schema_string(),
            "regular_justification": schema_string(),
            "intersection_description": schema_string(),
            "intersection_examples": schema_string_array(),
            "intersection_non_examples": schema_string_array(),
            "intersection_not_cfl_proof": schema_object({
                "method": schema_string(),
                "word_chosen": schema_string(),
                "all_cases_covered": schema_boolean(),
                "cases": schema_array(schema_object({
                    "case": schema_string(),
                    "pumped_word": schema_string(),
                    "pump_value": schema_number(),
                    "why_not_in_L": schema_string(),
                })),
            }),
            "conclusion": schema_string(),
        },
    ),
    "interchange": _specialist_schema(
        "interchange", _NON_CFL_VERDICT,
        {
            "method": schema_string(),
            "chosen_words": schema_string(),
            "word_length": schema_string(),
            "num_words": schema_string(),
            "interchange_analysis": schema_string(),
            "interchange_result": schema_string(),
            "contradiction": schema_string(),
            "conclusion": schema_string(),
        },
    ),
    "classifier": {
        "verdict": schema_string(enum=["cfl", "non_cfl", "uncertain"]),
        "confidence": schema_number(),
        "reasoning": schema_string(),
    },
    "reasoning": {
        "agent": schema_string(enum=["reasoning"]),
        "decision": schema_string(enum=["done", "invert", "retry"]),
        "verdict": nullable(schema_string(enum=["cfl", "non_cfl"])),
        "confidence": schema_number(),
        "primary_evidence": nullable(schema_string()),
        "supporting_evidence": schema_string_array(),
        "contradictions": schema_array(schema_object({
            "agents": schema_string_array(),
            "description": schema_string(),
            "resolution": schema_string(),
        })),
        "summary": schema_string(),
        "primary_justification": nullable(schema_string()),
        "retry_plan": nullable(schema_object({
            "agents_to_retry": schema_string_array(),
            "reason": schema_string(),
            "hints": _RETRY_HINTS_MAP,
            "max_retries_remaining": schema_number(),
        })),
        "hints_for_human": schema_string_array(),
        "errors": schema_string_array(),
    },
    "retry_planner": {
        "agent": schema_string(enum=["retry_planner"]),
        "agents_to_retry": schema_string_array(),
        "skip_agents": schema_string_array(),
        "hints": _RETRY_HINTS_MAP,
        "max_retries_remaining": schema_number(),
        "should_invert_hypothesis": schema_boolean(),
        "reasoning": schema_string(),
    },
    "proof_checker": {
        "agent": schema_string(enum=["proof_checker"]),
        "status": schema_string(enum=["verified", "issues_found"]),
        "checks": schema_array(schema_object({
            "agent": schema_string(),
            "claim": schema_string(),
            "details": schema_string(),
            "verdict": schema_string(),
            "severity": nullable(schema_string()),
            "error": nullable(schema_string()),
        })),
        "verified_proofs": schema_string_array(),
        "issues": schema_array(schema_object({
            "agent": schema_string(),
            "issue": schema_string(),
            "severity": schema_string(),
            "suggestion": schema_string(),
        })),
        "overall_assessment": schema_string(),
        "errors": schema_string_array(),
    },
    "formalizer": {
        "agent": schema_string(enum=["formalizer"]),
        "status": schema_string(enum=["success"]),
        "proof_document": schema_object({
            "title": schema_string(),
            "language": schema_string(),
            "method": schema_string(),
            "steps": schema_array(schema_object({
                "step_number": schema_number(),
                "title": schema_string(),
                "content": schema_string(),
                "justification": schema_string(),
            })),
            "conclusion": schema_string(),
            "verdict": schema_string(),
            "references": schema_string_array(),
        }),
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
