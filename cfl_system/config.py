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
    "input_parser":       "claude-sonnet-4-6",
    "classifier":         "claude-sonnet-4-6",
    "retry_planner":      "claude-sonnet-4-6",

    # Deep reasoning → Opus
    "cfg_builder":        "claude-opus-4-6",
    "pda_builder":        "claude-opus-4-6",
    "decomposition":      "claude-opus-4-6",
    "parikh":             "claude-opus-4-6",
    "pumping_cfl":        "claude-opus-4-6",
    "ogden":              "claude-opus-4-6",
    "closure_reduction":  "claude-opus-4-6",
    "interchange":        "claude-opus-4-6",
    "morphism":           "claude-opus-4-6",
    "reasoning":          "claude-opus-4-6",
    "proof_checker":      "claude-opus-4-6",
    "formalizer":         "claude-opus-4-6",
}

# Temperature per agent (0 = deterministic)
TEMPERATURES: dict[str, float] = {
    "input_parser":       0.0,
    "classifier":         0.0,
    "retry_planner":      0.0,
    "reasoning":          0.0,
    "proof_checker":      0.0,
    "formalizer":         0.0,
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

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------

# Output token budget.
#
# The Anthropic API requires max_tokens — it cannot be omitted. So the
# question is only what value to pass. Key facts:
#   • Opus 4.6 physical ceiling is 32000 output tokens per request.
#   • You pay for REAL output_tokens, not max_tokens — a high ceiling with
#     a short reply costs the same as a tight ceiling with the same reply.
#   • With streaming (which LiveRunner uses), the 10-minute-request guard
#     no longer refuses large budgets.
#
# So the sensible default is: give every agent the full model ceiling.
# This eliminates truncation-mid-JSON bugs (formalizer was hitting 4K and
# 16K limits before streaming + this value). The only downside is the
# runaway-loop scenario, which is capped at 32K × $0.015/1K ≈ $0.48.
MAX_TOKENS = 32000

# Kept as an escape hatch for per-agent tuning, but intentionally empty:
# every agent uses MAX_TOKENS unless a specific reason to lower it emerges.
MAX_TOKENS_PER_AGENT: dict[str, int] = {}

LLM_JSON_RETRIES = 2          # retry if LLM returns non-JSON
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
}
