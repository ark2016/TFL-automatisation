"""
TFL Agent System — configuration.

Model assignments, timeouts, and pipeline settings.
Edit this file to change models or behavior.
"""

# ---------------------------------------------------------------------------
# Model assignments per agent (§4 spec table)
# ---------------------------------------------------------------------------
# Available: "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"

MODELS = {
    # Fast / structured tasks → Sonnet
    "input_parser":     "claude-sonnet-5",
    "classifier":       "claude-sonnet-5",

    # Deep reasoning → Opus
    "re_builder":       "claude-opus-5-5",
    "dfa_builder":      "claude-opus-5-5",
    "pumping_agent":    "claude-opus-5-5",
    "nerode_agent":     "claude-opus-5-5",
    "closure_agent":    "claude-opus-5-5",
    "grammar_analyzer": "claude-opus-5-5",
    "reasoning_agent":  "claude-opus-5-5",
    "formalizer":       "claude-opus-5-5",

    # Verification & retry planning
    "proof_checker":    "claude-opus-5-5",
    "retry_planner":    "claude-sonnet-5",

    # Quick validation / summarization → Haiku (logs & translation only)
    "validator":        "claude-haiku-4-5",
    "summarizer":       "claude-haiku-4-5",
}

# Reasoning depth per agent (`output_config.effort`). Opus 5.5 always runs
# adaptive thinking (it can't be switched off) and Sonnet 5 runs it by default;
# effort is the knob for how much they think (and thus for latency and cost).
# Opus 5.5 defaults to "medium" when effort is omitted, so every agent gets an
# explicit value: proof-producing agents run at "high", structured
# parsing/classification at "medium".
# Levels: low | medium | high | xhigh | max. Agents not listed use DEFAULT_EFFORT.
EFFORT = {
    "input_parser":     "medium",
    "classifier":       "medium",
    "re_builder":       "high",
    "dfa_builder":      "high",
    "pumping_agent":    "high",
    "nerode_agent":     "high",
    "closure_agent":    "high",
    "grammar_analyzer": "high",
    "reasoning_agent":  "high",
    "formalizer":       "high",
    "proof_checker":    "high",
    "retry_planner":    "medium",
}
DEFAULT_EFFORT = "high"

# Server-side refusal fallback (beta server-side-fallback-2026-07-01): if a
# safety classifier declines an Opus 5.x request (stop_reason="refusal"),
# the API re-runs it on the model Anthropic recommends for that category.
REFUSAL_FALLBACK = True

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------

# Max tokens per LLM call. Thinking tokens count toward this limit on
# Opus 5.5 / Sonnet 5, so it must cover reasoning + the answer. Calls are
# streamed, so a large ceiling doesn't hit the SDK's non-streaming timeout;
# you pay only for tokens actually generated.
MAX_TOKENS = 64000

# Temperature — only sent to legacy models (Haiku 4.5). Adaptive-thinking
# models (Opus 4.7+, Sonnet 5, Opus 5.x) reject sampling parameters.
TEMPERATURE = 0.0

# Lean 4 type check timeout (seconds)
LEAN_TIMEOUT = 120

# Oracle test: max word length for exhaustive enumeration
ORACLE_MAX_EXHAUSTIVE = 7

# Retry counts
LLM_JSON_RETRIES = 1        # retry if LLM returns non-JSON
FORMALIZER_RETRIES = 2       # retry if Lean type check fails (§5.2 Level 4)
