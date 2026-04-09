"""
LL Agent System — configuration.

Model assignments, temperatures, and pipeline settings for the LL(k) property checker.
Parallel to cfl_system/config.py; prefix for all prompt files is 'll_'.
"""

import os

# ---------------------------------------------------------------------------
# Model assignments per agent
# ---------------------------------------------------------------------------

MODELS: dict[str, str] = {
    # Fast / structured → Sonnet
    "input_parser":         "claude-sonnet-4-6",
    "classifier":           "claude-sonnet-4-6",
    "marker_analyzer":      "claude-sonnet-4-6",
    "grammar_transformer":  "claude-sonnet-4-6",
    "formalizer":           "claude-sonnet-4-6",

    # Deep reasoning → Opus
    "ll_grammar_builder":   "claude-opus-4-6",
    "substitution_agent":   "claude-opus-4-6",
    "ambiguity_detector":   "claude-opus-4-6",
    "prefix_classes_agent": "claude-opus-4-6",
    "reasoning_agent":      "claude-opus-4-6",
}

# Temperature per agent (0.0 = deterministic)
TEMPERATURES: dict[str, float] = {
    # System / structural agents: fully deterministic
    "input_parser":         0.0,
    "classifier":           0.0,
    "formalizer":           0.0,
    "reasoning_agent":      0.0,

    # Constructive specialists: slight creativity for grammar synthesis
    "ll_grammar_builder":   0.2,
    "marker_analyzer":      0.2,
    "grammar_transformer":  0.2,

    # Destructive specialists: low temperature, but allow exploration
    "substitution_agent":   0.2,
    "ambiguity_detector":   0.3,
    "prefix_classes_agent": 0.2,
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
# This eliminates truncation-mid-JSON bugs. The only downside is the
# runaway-loop scenario, which is capped at 32K × $0.015/1K ≈ $0.48.
MAX_TOKENS = 32000

# Kept as an escape hatch for per-agent tuning, but intentionally empty:
# every agent uses MAX_TOKENS unless a specific reason to lower it emerges.
MAX_TOKENS_PER_AGENT: dict[str, int] = {}

LLM_JSON_RETRIES = 2          # retry if LLM returns non-JSON
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Prompt file mapping: agent_name → prompt filename (without path)
PROMPT_FILES: dict[str, str] = {
    "input_parser":         "ll_input_parser.md",
    "classifier":           "ll_classifier.md",
    "ll_grammar_builder":   "ll_grammar_builder.md",
    "marker_analyzer":      "ll_marker_analyzer.md",
    "grammar_transformer":  "ll_grammar_transformer.md",
    "substitution_agent":   "ll_substitution_agent.md",
    "ambiguity_detector":   "ll_ambiguity_detector.md",
    "prefix_classes_agent": "ll_prefix_classes_agent.md",
    "reasoning_agent":      "ll_reasoning_agent.md",
    "formalizer":           "ll_formalizer.md",
}
