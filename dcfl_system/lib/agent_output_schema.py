"""Per-agent ``AgentOutput`` JSON schemas for ``output_config.format``
(TODO.md §3 M — structured outputs instead of "extract JSON from prose").

``REQUIRED_KEYS`` is the *exhaustive* top-level key set of each agent's own
prompt contract, taken from its "## Output format" section
(``dcfl_system/prompts/*.md``) — the 5 specialists' shared set mirrors
``dcfl_system/tests/test_prompt_contracts.py``'s ``REQUIRED_OUTPUT_KEYS``.

:data:`_FIELD_SCHEMAS` gives the **real** per-key subschema for every agent
(built with ``schema_string`` / ``schema_object`` / etc. from
``agent_system.lib.llm_client`` — see that module for exactly what the API
accepts and rejects: an empty ``{}`` subschema is rejected outright, and
``additionalProperties: false`` is required at *every* nesting level).
Unlike ``cfl_system`` / ``ll_system``, every dcfl agent's contract is fully
representable this way — no specialist's ``proof_sketch`` is keyed by
anything the agent picks at generation time (each is a small, fixed set of
string/array fields — see each ``## proof_sketch format (...)`` block in
its own prompt), so nothing here needs to be exempted from structured
outputs.
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

_STATUS = schema_string(enum=["success", "fail", "not_applicable", "uncertain"])

# Each specialist's own "## proof_sketch format (...Proof)" block --
# every field is a string/array/enum, none keyed by anything the agent
# picks at generation time.
_PROOF_SKETCH_BY_AGENT: dict[str, dict] = {
    "stack_strategy": schema_object({
        "kind": schema_string(enum=["stack_strategy"]),
        "phases": schema_array(schema_object({
            "name": schema_string(),
            "action": schema_string(),
            "what": schema_string(),
            "trigger": schema_string(),
        })),
        "separator": nullable(schema_string()),
        "finite_control": schema_string(),
        "determinism_argument": schema_string(),
        "regex_in_states": schema_string_array(),
        # REQUIRED whenever status == "success" (prompt: a word-level
        # phases/separator description is no longer, by itself, a DCFL
        # certificate -- docs/VERDICT_POLICY.md R2') -- an explicit DPDA
        # the oracle_verifier can mechanically check for determinism and
        # simulate. `transitions` is an ARRAY of transition objects (same
        # convention as cfl_pda_builder's PDA), not a map keyed by state,
        # so it's fully representable here.
        "dpda": schema_object({
            "states": schema_string_array(),
            "start": schema_string(),
            "accept_states": schema_string_array(),
            "accept_mode": schema_string(enum=["final_state", "empty_stack"]),
            "stack_alphabet": schema_string_array(),
            "initial_stack": schema_string_array(),
            "transitions": schema_array(schema_object({
                "from": schema_string(),
                "read": nullable(schema_string()),
                "top": schema_string(),
                "to": schema_string(),
                "push": schema_string_array(),
            })),
        }),
    }),
    "closure_reduction": schema_object({
        "kind": schema_string(enum=["closure_reduction"]),
        "operation": schema_string(enum=["complement", "inv_homomorphism", "reg_intersection"]),
        "direction": schema_string(enum=["constructive", "destructive"]),
        "source_language": schema_string(),
        "transformation": schema_string(),
        "result_argument": schema_string(),
    }),
    "dcfl_pumping": schema_object({
        "kind": schema_string(enum=["dcfl_pumping"]),
        "pumping_length": schema_string(),
        "word_w": schema_string(),
        "word_w_prime": schema_string(),
        "common_prefix_x": schema_string(),
        "suffix_y": schema_string(),
        "suffix_z": schema_string(),
        "first_letters_match": schema_string(),
        "condition1_argument": schema_string(),
        "condition2_argument": schema_string(),
    }),
    "shallit": schema_object({
        "kind": schema_string(enum=["shallit"]),
        "technique": schema_string(enum=["nerode_classes", "prefix_continuation"]),
        # exactly one technique's fields are populated per response; the
        # other technique's fields are null (prompt: "Ровно одна техника
        # используется за раз; поля другой техники — null").
        "dead_class_finite": nullable(schema_string()),
        "distinguishing_suffix": nullable(schema_string()),
        "separation_argument": nullable(schema_string()),
        "derived_language": nullable(schema_string()),
        "regular_filter": nullable(schema_string()),
        "non_cfl_argument": nullable(schema_string()),
        "argument": schema_string(),
    }),
    "inh_ambiguity": schema_object({
        "kind": schema_string(enum=["inh_ambiguity"]),
        "disjunction_identified": schema_string(),
        "overlap_words": schema_string(),
        "ambiguity_argument": schema_string(),
        "dcfl_implication": schema_string(),
    }),
}

# Each specialist's own verdict value space (docs/THEORY.md §1): the
# constructive ones (stack_strategy) only ever claim "dcfl"; the
# destructive ones (dcfl_pumping, shallit, inh_ambiguity) only ever claim
# "non_dcfl"; closure_reduction can go either way (Table 1 covers both
# constructive and destructive closure operations).
_VERDICT_BY_AGENT = {
    "stack_strategy": ["dcfl"],
    "closure_reduction": ["dcfl", "non_dcfl"],
    "dcfl_pumping": ["non_dcfl"],
    "shallit": ["non_dcfl"],
    "inh_ambiguity": ["non_dcfl"],
}


def _specialist_schema(agent_name: str) -> dict[str, dict]:
    return {
        "agent_name": schema_string(enum=[agent_name]),
        "status": _STATUS,
        "verdict": nullable(schema_string(enum=_VERDICT_BY_AGENT[agent_name])),
        "proof_sketch": nullable(_PROOF_SKETCH_BY_AGENT[agent_name]),
        "evidence": schema_string_array(),
        "confidence": schema_number(),
        "errors": schema_string_array(),
    }


_FIELD_SCHEMAS: dict[str, dict[str, dict]] = {
    **{name: _specialist_schema(name) for name in _SPECIALISTS},
    "classifier": {
        "verdict": schema_string(enum=["dcfl", "non_dcfl", "uncertain"]),
        "confidence": schema_number(),
        "reasoning": schema_string(),
        "suggested_methods": schema_string_array(),
    },
    "reasoning": {
        "action": schema_string(enum=["done", "retry"]),
        "verdict": nullable(schema_string(enum=["dcfl", "non_dcfl"])),
        "confidence": schema_number(),
        # which specialist provided the winning proof; null while no
        # agent has succeeded yet (action == "retry").
        "primary_evidence": nullable(schema_string(enum=list(_SPECIALISTS))),
        "summary": schema_string(),
        "retry_plan": nullable(schema_object({
            "agents_to_retry": schema_string_array(),
            "hints": schema_object(
                {name: schema_string() for name in _SPECIALISTS}, required=[],
            ),
        })),
        "hints_for_human": schema_string_array(),
        "errors": schema_string_array(),
    },
}


def schema_for(agent_name: str) -> dict | None:
    """The ``output_config.format`` JSON schema for `agent_name`, or
    ``None`` when it has no fixed single-object contract (caller keeps the
    legacy "extract JSON from prose" path)."""
    properties = _FIELD_SCHEMAS.get(agent_name)
    if properties is None:
        return None
    return build_agent_output_schema(properties)
