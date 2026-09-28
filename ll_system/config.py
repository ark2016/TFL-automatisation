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
    "input_parser":         "claude-sonnet-5-5",
    "classifier":           "claude-sonnet-5-5",
    "marker_analyzer":      "claude-sonnet-5-5",
    "grammar_transformer":  "claude-sonnet-5-5",
    "formalizer":           "claude-sonnet-5-5",

    # Deep reasoning → Opus
    "ll_grammar_builder":   "claude-opus-5-5",
    "substitution_agent":   "claude-opus-5-5",
    "ambiguity_detector":   "claude-opus-5-5",
    "prefix_classes_agent": "claude-opus-5-5",
    "reasoning_agent":      "claude-opus-5-5",
}

# Temperature per agent — only sent to legacy models (Haiku 4.5, pre-4.6).
# Adaptive-thinking models (Opus 4.7+, Sonnet 5 / 5.5, Opus 5.x) reject sampling
# parameters with a 400, so for them these values are ignored; see EFFORT.
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

# Reasoning depth per agent (`output_config.effort`). Opus 5.5 always runs
# adaptive thinking (it can't be switched off) and Sonnet 5.5 runs it by default;
# effort is the knob for how much they think (and thus for latency and cost). Opus 5.5 defaults to "medium"
# when effort is omitted, so every agent gets an explicit value: proof-producing
# agents run at "high", structured parsing/classification at "medium".
# Levels: low | medium | high | xhigh | max. Agents not listed use DEFAULT_EFFORT.
EFFORT: dict[str, str] = {
    "input_parser":         "medium",
    "classifier":           "medium",
    "marker_analyzer":      "medium",
    "grammar_transformer":  "medium",
    "formalizer":           "medium",
    "ll_grammar_builder":   "high",
    "substitution_agent":   "high",
    "ambiguity_detector":   "high",
    "prefix_classes_agent": "high",
    "reasoning_agent":      "high",
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

# Kept as an escape hatch for per-agent tuning, but intentionally empty:
# every agent uses MAX_TOKENS unless a specific reason to lower it emerges.
MAX_TOKENS_PER_AGENT: dict[str, int] = {}

LLM_JSON_RETRIES = 2          # retry if LLM returns non-JSON
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Cost ceiling (TODO.md backlog round C2): the retry planner must never call
# the same specialist more than this many times for one task -- precedent:
# live cfl-12 eval run, cfg_builder alone was called 6 times across retries,
# 130 706 output tokens / $0.80 for a single agent. run_specialist_node
# enforces the cap itself (skips the call, logs a note) as a last-resort
# backstop, but until round C5 apply_verdict_gate's retry proposals did not
# know about it: a proposal made entirely of already-capped agents was
# still dispatched as a whole extra graph round that produced no new
# specialist output, and only MAX_RETRIES eventually ended it. Since round
# C5, apply_verdict_gate filters capped agents out of every retry proposal
# it builds (the reasoning agent's own retry_plan, and every
# `_apply_downgrade` call) first -- a proposal that is ENTIRELY capped
# agents ends retries immediately instead of spending that round, and each
# exclusion is logged as an "agent X call cap reached ..., excluded from
# retry plan" note in verdict_gate.downgrades (in addition to
# run_specialist_node's own backstop note, for an agent that still slips
# through some other path).
MAX_CALLS_PER_AGENT = 3

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
