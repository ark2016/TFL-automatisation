"""
CFL Agent System — configuration.

Model assignments, timeouts, and pipeline settings.
"""

import os

# ---------------------------------------------------------------------------
# Model assignments per agent
# ---------------------------------------------------------------------------

MODELS: dict[str, str] = {
    # Fast / structured → Sonnet
    "input_parser":       "claude-sonnet-5-5",
    "classifier":         "claude-sonnet-5-5",
    "retry_planner":      "claude-sonnet-5-5",

    # Deep reasoning → Opus
    "cfg_builder":        "claude-opus-5-5",
    "pda_builder":        "claude-opus-5-5",
    "decomposition":      "claude-opus-5-5",
    "parikh":             "claude-opus-5-5",
    "pumping_cfl":        "claude-opus-5-5",
    "ogden":              "claude-opus-5-5",
    "closure_reduction":  "claude-opus-5-5",
    "interchange":        "claude-opus-5-5",
    "morphism":           "claude-opus-5-5",
    "reasoning":          "claude-opus-5-5",
    "proof_checker":      "claude-opus-5-5",
    "formalizer":         "claude-opus-5-5",
    # R-Lean: writes only the Lean 4 proof body (the statement is generated
    # from the IR by code, cfl_system.lib.lean_ir) -- proof-producing, Opus.
    "lean_formalizer":    "claude-opus-5-5",
    # Separate Lean formalization entry (`python -m cfl_system.formalize`): the
    # corrections after the first (Opus) attempt run on Sonnet. Shares the
    # lean_formalizer prompt, hence no PROMPT_FILES slot.
    "lean_formalizer_retry": "claude-sonnet-5-5",
}

# Temperature per agent — only sent to legacy models (Haiku 4.5, pre-4.6).
# Adaptive-thinking models (Opus 4.7+, Sonnet 5 / 5.5, Opus 5.x) reject sampling
# parameters with a 400, so for them these values are ignored; see EFFORT.
TEMPERATURES: dict[str, float] = {
    "input_parser":       0.0,
    "classifier":         0.0,
    "retry_planner":      0.0,
    "reasoning":          0.0,
    "proof_checker":      0.0,
    "formalizer":         0.0,
    "lean_formalizer":    0.0,
    "lean_formalizer_retry": 0.0,
    "cfg_builder":        0.2,
    "pda_builder":        0.2,
    "decomposition":      0.3,
    "parikh":             0.2,
    "pumping_cfl":        0.1,
    "ogden":              0.1,
    "closure_reduction":  0.2,
    "interchange":        0.2,
    "morphism":           0.2,
}

# Reasoning depth per agent (`output_config.effort`). Opus 5.5 always runs
# adaptive thinking (it can't be switched off) and Sonnet 5.5 runs it by default;
# effort is the knob for how much they think (and thus for latency and cost). Opus 5.5 defaults to "medium"
# when effort is omitted, so every agent gets an explicit value: proof-producing
# agents run at "high", structured parsing/classification at "medium".
# Levels: low | medium | high | xhigh | max. Agents not listed use DEFAULT_EFFORT.
EFFORT: dict[str, str] = {
    "input_parser":      "medium",
    "classifier":        "medium",
    "retry_planner":     "medium",
    "cfg_builder":       "high",
    "pda_builder":       "high",
    "decomposition":     "high",
    "parikh":            "high",
    "pumping_cfl":       "high",
    "ogden":             "high",
    "closure_reduction": "high",
    "interchange":       "high",
    "morphism":          "high",
    "reasoning":         "high",
    "proof_checker":     "high",
    "formalizer":        "high",
    "lean_formalizer":   "high",
    "lean_formalizer_retry": "high",
}
DEFAULT_EFFORT = "high"

# Server-side refusal fallback (beta server-side-fallback-2026-07-01): if a
# safety classifier declines an Opus 5.x request (stop_reason="refusal"),
# the API re-runs it on the model Anthropic recommends for that category.
REFUSAL_FALLBACK = True

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------

# Output token budget.
#
# The Anthropic API requires max_tokens — it cannot be omitted. Key facts:
#   • Opus 5.5 / Sonnet 5.5 think adaptively on every call, and thinking tokens
#     count toward max_tokens even though their text is not returned — so the
#     limit must cover reasoning + the JSON answer.
#   • Opus 5.5 output ceiling is 128K per request; 64K is the recommended start
#     for long reasoning turns.
#   • You pay for REAL output_tokens, not max_tokens — a high ceiling with
#     a short reply costs the same as a tight ceiling with the same reply.
#   • LiveRunner always streams, so the SDK's 10-minute non-streaming guard
#     does not refuse large budgets.
MAX_TOKENS = 64000

# Per-agent overrides. Every agent uses MAX_TOKENS unless listed here.
# cfg_builder: live cfl-12 hit the 64000 ceiling mid-JSON (truncated output);
# 128000 is the API maximum for Opus 5.5. Safe with LiveRunner (always streams);
# you still pay only for real output tokens.
MAX_TOKENS_PER_AGENT: dict[str, int] = {"cfg_builder": 128000}

# Haiku JSON-repair call: output ceiling. A repair re-emits the whole object,
# so a truncated 64K-token answer cannot be repaired within 8000 tokens.
# Haiku 4.5 allows 64K output; 32000 leaves headroom. The call is
# non-streaming, so it passes an explicit timeout (JSON_REPAIR_TIMEOUT_S) --
# without one the SDK refuses non-streaming max_tokens > ~21333.
JSON_REPAIR_MAX_TOKENS = 32000
JSON_REPAIR_TIMEOUT_S = 600.0

LLM_JSON_RETRIES = 2          # retry if LLM returns non-JSON
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Cost ceiling (TODO.md backlog round C2): the retry planner must never call
# the same specialist more than this many times for one task -- precedent:
# live cfl-12 eval run, cfg_builder alone was called 6 times across retries,
# 130 706 output tokens / $0.80 for a single agent. run_specialist_node
# enforces the cap itself (skips the call, logs a note) as a last-resort
# backstop, but until round C5 run_retry_planner_node did not know about
# it: a plan made entirely of already-capped agents was still dispatched as
# a whole extra graph round that produced no new specialist output, and
# only MAX_RETRIES eventually ended it. Since round C5,
# run_retry_planner_node filters capped agents out of its own proposal
# first -- a proposal that is ENTIRELY capped agents ends retries
# immediately instead of spending that round, and each exclusion is logged
# as an "agent X call cap reached ..., excluded from retry plan" note in
# verdict_gate.downgrades (in addition to run_specialist_node's own
# backstop note, for an agent that still slips through some other path).
MAX_CALLS_PER_AGENT = 3

# ---------------------------------------------------------------------------
# Lean 4 formalization (docs/VERDICT_POLICY.md R-Lean)
# ---------------------------------------------------------------------------
#
# `orchestrator.lean_formalize_node` -- the Lean step that runs after the
# verdict; distinct from `formalize_node`, which only structures the
# informal proof as Markdown (agent "formalizer", no Lean).

# Max lean_formalizer <-> type_check.check_lean_file round trips for one
# statement. The statement never changes between attempts (it is generated
# from the IR by code); only the compiler errors are fed back to the agent.
# Every attempt is one Opus call, so this is also the per-task cost ceiling
# of the Lean step (mirrors agent_system.config.MAX_FORMALIZE_ITERATIONS).
MAX_FORMALIZE_ITERATIONS = 3

# Separate formalization entry (lib: agent_system/lib/formalize_run.py,
# `python -m cfl_system.formalize`; decision 2026-09-29): corrections after the first
# attempt, and the output limit per call (128000 = API maximum, both models).
FORMALIZE_RETRIES = 2
FORMALIZE_MAX_TOKENS = 128000

# Seconds allowed for one `lake env lean` check. CFL statements import
# langlib's pumping/Ogden modules on top of Mathlib, so this is a bit above
# agent_system's 120.
LEAN_TIMEOUT = 180


def formalization_enabled_default() -> bool:
    """Default for the Lean step: ``TFL_FORMALIZATION`` = 1/true/yes/on
    enables it for every run (same switch as agent_system); anything else
    keeps it opt-in per call (``run_pipeline(..., formalize=True)`` / the
    CLI's ``--formalize``). Enabling the step spends no API budget by
    itself -- a live run still needs ``--live`` (root CLAUDE.md)."""
    return os.environ.get("TFL_FORMALIZATION", "").strip().lower() in ("1", "true", "yes", "on")


FORMALIZATION_ENABLED = formalization_enabled_default()

# Prompt file mapping: agent_name → prompt filename (without path)
PROMPT_FILES: dict[str, str] = {
    "input_parser":       "cfl_input_parser.md",
    "classifier":         "cfl_classifier.md",
    "cfg_builder":        "cfl_cfg_builder.md",
    "pda_builder":        "cfl_pda_builder.md",
    "decomposition":      "cfl_decomposition.md",
    "parikh":             "cfl_parikh.md",
    "pumping_cfl":        "cfl_pumping.md",
    "ogden":              "cfl_ogden.md",
    "closure_reduction":  "cfl_closure_reduction.md",
    "interchange":        "cfl_interchange.md",
    "morphism":           "cfl_morphism.md",
    "reasoning":          "cfl_reasoning.md",
    "retry_planner":      "cfl_retry_planner.md",
    "proof_checker":      "cfl_proof_checker.md",
    "formalizer":         "cfl_formalizer.md",
    "lean_formalizer":    "cfl_lean_formalizer.md",
}
