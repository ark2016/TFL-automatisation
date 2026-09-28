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
API accepts and rejects: an empty ``{}`` subschema is rejected outright,
``additionalProperties: false`` is required at *every* nesting level, not
just the top, and — the actual live bug this round fixed (a live cfl eval
run: 7x 400 on ``reasoning``/``retry_planner``, zero successful reasoning
calls, no matching entry in ``llm_client._SCHEMA_REJECTION_MARKERS`` so the
schema-rejection fallback never fired) — the whole schema tree may not have
more than 24 **optional** properties (a property not in its own object's
``required`` list), counted recursively at every nesting level; see
``agent_system/lib/testing/schema_checks.py``'s module docstring for the
exact API error text and how the 24 is confirmed live to count
recursively). ``pumping_cfl`` / ``ogden`` were, until this round, wrongly
believed dynamically-keyed (``evidence.word_instances`` /
``evidence.marked_positions``) — every prompt worked example actually keys
them by the fixed literal strings ``"3"`` and ``"4"`` (the two pumping
multipliers *every* proof instantiates, per ``cfl_pumping.md`` /
``cfl_ogden.md``'s own "## Word choice strategies" — not a value chosen
per-task), and ``lib/claim_verifier.py``'s semantic check reads them the
same fixed way (``for p in {3, 4}``) — so both now get a real, closed
schema below.

``morphism`` was, for the same reason, also believed dynamically-keyed
(``evidence.morphism.mapping``, keyed by the language's own alphabet
symbols, which really do differ per task and can't be enumerated in a
closed schema) and kept no entry at all (``schema_for`` returned ``None``,
same as ``input_parser``). Same fix as the retry-hint map below: model the
mapping as an **array** of ``{"symbol": ..., "image": ...}`` objects
(``_MORPHISM_MAPPING_SCHEMA``) instead of an object keyed by the symbols
themselves — one shared, closed item schema, no per-task keys to
enumerate. This changes what a genuine ``output_config.format`` call
produces (an array) from what the prompt's own "## Output Format"
documents and what the legacy prose-extraction fallback path still
produces (a ``{symbol: image}`` object) — same tradeoff as the retry-hint
map, and the same fix: ``cfl_system.orchestrator``'s
``_normalize_morphism_mapping`` converts either shape back to the object
every downstream consumer (worked examples, the renderer) expects,
immediately after parsing.

The 9 specialists' shared retry-hint map (``reasoning.retry_plan.hints`` /
``retry_planner.hints``) is the schema that actually hit the API's
24-optional-property limit: modelling "only the retried specialists' keys
are present, and only the fields that specialist's hint actually uses" as
`required=[]` at both the map level (9 optional entries) and each entry's
own level (6 optional fields each) came to 9 + 9*6 = 63 optional
properties — confirmed live as the exact number the API's 400 named.

The first fix tried (required+nullable instead of `required=[]`
everywhere) traded that problem for a *second*, separate live-confirmed
limit: the API also caps how many **union-typed** (``anyOf`` /
``type: [..., "null"]``) parameters a schema may carry — "Schemas
contains too many parameters with union types (49 parameters with type
arrays or anyOf) ... (limit: 16 parameters with unions)" — turning every
one of those same 63 slots ``nullable(...)`` traded 9 + 9*6 optional
properties for 9 + 9*4 = 45 union-typed ones (the map's own 9 entries,
plus 4 of each entry's 6 fields — ``hint``/``strategy`` stayed plain
required strings, not nullable), still over the (different) limit.

Both limits share the same root cause: **one map with 9 property keys is
9 separate copies of `_RETRY_HINT`'s subschema** — the API walks the
whole tree once per *distinct* subschema occurrence, so anything
per-entry (optional or nullable) is multiplied by 9 regardless of which
axis it's expressed on. An **array** of one shared item schema does not
have this problem: ``schema_array(items)`` has exactly ONE `items`
subschema, walked once no matter how many elements the model actually
returns at runtime. `_RETRY_HINTS_SCHEMA` is that array — each item names
its own specialist explicitly (``"agent": "pumping_cfl"``) instead of
being keyed by it, with `hint`/`strategy` required and only
`avoid`/`suggested_regex`/`suggested_word`/`counterexample` optional (one
copy of 4 optional properties total, not 9 copies of 4 or their nullable
equivalent) — confirmed live (2026-09-27) to compile with
`used_structured_output=True`, no 400 on either axis.

This changes what a genuine ``output_config.format`` call produces (an
array of ``{"agent": ..., ...}`` objects) from what the prompt's own
"## Output Format" documents and what the *legacy* prose-extraction
fallback path (no schema) still produces unchanged (a
``{agent_name: {...}}`` map) — ``cfl_system.orchestrator``'s
``_normalize_retry_hints`` converts either shape back to the map every
downstream consumer expects immediately after parsing, so nothing past
that one point ever sees the array form.
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
    # R-Lean's proof-body agent (prompts/cfl_lean_formalizer.md) -- distinct
    # from `formalizer` above, which only structures the informal proof.
    "lean_formalizer": frozenset({"agent", "proof_body", "lemmas_used", "notes"}),
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

# pumping_cfl / ogden: instantiate the parametric word at the two fixed
# pumping multipliers every worked example uses (p=3, p=4 -- literally
# "3"/"4" as string keys, never a value the agent picks itself; see the
# module docstring and lib/claim_verifier.py's own `for p in {3, 4}`).
_PUMP_WORD_INSTANCES = schema_object({
    "3": schema_string(),
    "4": schema_string(),
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

# retry_planner.hints / reasoning.retry_plan.hints: an ARRAY of per-agent
# hint objects (not a map keyed by specialist name -- see the module
# docstring for why: one shared item schema avoids the 9x-duplication that
# blew past first the optional-property limit, then the union-typed-
# parameter limit). "always present" per the prompt's own field
# descriptions (`hint`, `strategy`) plus the specialist name itself stay
# plain required; "Optional fields" per the prompt (`avoid`,
# `suggested_word`, `suggested_regex`, `counterexample`) are genuinely
# optional here -- one copy of 4 optional properties, not 9.
_RETRY_HINT_ITEM = schema_object(
    {
        "agent": schema_string(enum=list(_SPECIALISTS)),
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
    required=["agent", "hint", "strategy"],
)
_RETRY_HINTS_SCHEMA = schema_array(_RETRY_HINT_ITEM)

# morphism's evidence.morphism.mapping: an ARRAY of {symbol, image} entries
# (not an object keyed by the language's own alphabet symbols -- see the
# module docstring for why: those keys differ per task and
# additionalProperties:false can't leave a per-task-variable key set open).
# One shared item schema, both fields always present per every worked
# example in cfl_morphism.md.
_MORPHISM_MAPPING_ITEM = schema_object(
    {"symbol": schema_string(), "image": schema_string()},
    required=["symbol", "image"],
)
_MORPHISM_MAPPING_SCHEMA = schema_array(_MORPHISM_MAPPING_ITEM)

_MORPHISM_SCHEMA = schema_object({
    "domain_alphabet": schema_string_array(),
    "codomain_alphabet": schema_string_array(),
    "mapping": _MORPHISM_MAPPING_SCHEMA,
})


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
    "pumping_cfl": _specialist_schema(
        "pumping_cfl", _NON_CFL_VERDICT,
        {
            "word_chosen": schema_string(),
            "word_parametric": schema_string(),
            # NOT in evidence_required below: cfl_pumping.md's own Example 2
            # (status="inconclusive", direct pumping unreliable -- defer to
            # closure_reduction) has every OTHER evidence field populated
            # but omits word_instances entirely, unlike ogden's own
            # inconclusive example (which uses evidence: null wholesale).
            "word_instances": _PUMP_WORD_INSTANCES,
            "membership_argument": schema_string(),
            "length_argument": schema_string(),
            "cases": schema_array(schema_object({
                "case": schema_string(),
                "vwx_region": schema_string(),
                "pump_value": schema_number(),
                "pumped_word": schema_string(),
                "why_not_in_L": schema_string(),
            })),
            "all_cases_covered": schema_boolean(),
            "conclusion": schema_string(),
        },
        evidence_required=[
            "all_cases_covered", "cases", "conclusion", "length_argument",
            "membership_argument", "word_chosen", "word_parametric",
        ],
    ),
    "ogden": _specialist_schema(
        "ogden", _NON_CFL_VERDICT,
        {
            "word_chosen": schema_string(),
            "word_parametric": schema_string(),
            "word_instances": _PUMP_WORD_INSTANCES,
            "membership_argument": schema_string(),
            # keyed the same fixed way as word_instances ("3"/"4"), plus a
            # "description" entry (cfl_ogden.md's own "## Output Format").
            "marked_positions": schema_object({
                "description": schema_string(),
                "3": schema_array(schema_number()),
                "4": schema_array(schema_number()),
            }),
            "num_marked": schema_string(),
            "marking_rationale": schema_string(),
            "cases": schema_array(schema_object({
                "case": schema_string(),
                "vwx_region": schema_string(),
                "marked_in_vwx": schema_string(),
                "pump_value": schema_number(),
                "pumped_word": schema_string(),
                "why_not_in_L": schema_string(),
            })),
            "all_cases_covered": schema_boolean(),
            "conclusion": schema_string(),
        },
        # word_instances/marked_positions are, per cfl_ogden.md, "REQUIRED
        # whenever status = success, even when all_cases_covered is true" --
        # ogden's own inconclusive worked example sets the whole evidence
        # object to null rather than omitting just these two, so (unlike
        # pumping_cfl) every evidence field here stays required.
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
    "morphism": _specialist_schema(
        "morphism", _NON_CFL_VERDICT,
        {
            "morphism_type": schema_string(),
            "morphism": _MORPHISM_SCHEMA,
            "image_language": schema_string(),
            "image_not_cfl_proof": nullable(schema_object({
                "method": schema_string(),
                "details": schema_string(),
            })),
            "explanation": schema_string(),
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
            "hints": _RETRY_HINTS_SCHEMA,
            "max_retries_remaining": schema_number(),
        })),
        "hints_for_human": schema_string_array(),
        "errors": schema_string_array(),
    },
    "retry_planner": {
        "agent": schema_string(enum=["retry_planner"]),
        "agents_to_retry": schema_string_array(),
        "skip_agents": schema_string_array(),
        "hints": _RETRY_HINTS_SCHEMA,
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
    "lean_formalizer": {
        "agent": schema_string(enum=["lean_formalizer"]),
        "proof_body": schema_string(),
        "lemmas_used": schema_string_array(),
        "notes": schema_string(),
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
