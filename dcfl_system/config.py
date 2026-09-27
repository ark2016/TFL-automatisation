"""DCFL Agent System — configuration."""
import os

MODELS: dict[str, str] = {
    # Fast / structured → Sonnet
    "input_parser":       "claude-sonnet-5",
    "classifier":         "claude-sonnet-5",

    # Deep reasoning → Opus
    "stack_strategy":     "claude-opus-5-5",
    "closure_reduction":  "claude-opus-5-5",
    "dcfl_pumping":       "claude-opus-5-5",
    "shallit":            "claude-opus-5-5",
    "inh_ambiguity":      "claude-opus-5-5",
    "reasoning":          "claude-opus-5-5",
}

# Temperature per agent — only sent to legacy models (Haiku 4.5, pre-4.6).
# Adaptive-thinking models (Opus 4.7+, Sonnet 5, Opus 5.x) reject sampling
# parameters with a 400, so for them these values are ignored; see EFFORT.
TEMPERATURES: dict[str, float] = {
    "input_parser":       0.0,
    "classifier":         0.0,
    "stack_strategy":     0.2,
    "closure_reduction":  0.2,
    "dcfl_pumping":       0.1,
    "shallit":            0.1,
    "inh_ambiguity":      0.1,
    "reasoning":          0.0,
}

# Reasoning depth per agent (`output_config.effort`). Opus 5.5 always runs
# adaptive thinking (it can't be switched off) and Sonnet 5 runs it by default;
# effort is the knob for how much they think (and thus for latency and cost). Opus 5.5 defaults to "medium"
# when effort is omitted, so every agent gets an explicit value: proof-producing
# agents run at "high", structured parsing/classification at "medium".
# Levels: low | medium | high | xhigh | max. Agents not listed use DEFAULT_EFFORT.
EFFORT: dict[str, str] = {
    "input_parser":      "medium",
    "classifier":        "medium",
    "stack_strategy":    "high",
    "closure_reduction": "high",
    "dcfl_pumping":      "high",
    "shallit":           "high",
    "inh_ambiguity":     "high",
    "reasoning":         "high",
}
DEFAULT_EFFORT = "high"

# Server-side refusal fallback (beta server-side-fallback-2026-07-01): if a
# safety classifier declines an Opus 5.x request (stop_reason="refusal"),
# the API re-runs it on the model Anthropic recommends for that category.
REFUSAL_FALLBACK = True

# Output token budget.
#
# The Anthropic API requires max_tokens — it cannot be omitted. Key facts:
#   • Opus 5.5 / Sonnet 5 think adaptively on every call, and thinking tokens
#     count toward max_tokens even though their text is not returned — so the
#     limit must cover reasoning + the JSON answer.
#   • Opus 5.5 output ceiling is 128K per request; 64K is the recommended start
#     for long reasoning turns.
#   • You pay for REAL output_tokens, not max_tokens — a high ceiling with
#     a short reply costs the same as a tight ceiling with the same reply.
#   • LiveRunner always streams, so the SDK's 10-minute non-streaming guard
#     does not refuse large budgets.
MAX_TOKENS = 64000
MAX_TOKENS_PER_AGENT: dict[str, int] = {}
LLM_JSON_RETRIES = 2
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Cost ceiling (TODO.md backlog round C2): the retry planner must never call
# the same specialist more than this many times for one task -- precedent:
# live cfl-12 eval run, cfg_builder alone was called 6 times across retries,
# 130 706 output tokens / $0.80 for a single agent. run_specialist_node
# enforces the cap itself (skips the call, logs a note) as a last-resort
# backstop, but until round C5 the retry planner did not know about it: a
# plan made entirely of already-capped agents was still dispatched as a
# whole extra graph round that produced no new specialist output, and only
# MAX_RETRIES eventually ended it. Since round C5, retry_planner_node
# filters capped agents out of its own proposal first -- a proposal that is
# ENTIRELY capped agents ends retries immediately instead of spending that
# round, and each exclusion is logged as an "agent X call cap reached ...,
# excluded from retry plan" note in verdict_gate.downgrades (in addition to
# run_specialist_node's own backstop note, for an agent that still slips
# through some other path).
MAX_CALLS_PER_AGENT = 3

PROMPT_FILES: dict[str, str] = {
    "input_parser":       "input_parser.md",
    "classifier":         "classifier.md",
    "stack_strategy":     "stack_strategy.md",
    "closure_reduction":  "closure_reduction.md",
    "dcfl_pumping":       "dcfl_pumping.md",
    "shallit":            "shallit.md",
    "inh_ambiguity":      "inh_ambiguity.md",
    "reasoning":          "reasoning_agent.md",
}
