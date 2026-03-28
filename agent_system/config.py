"""
TFL Agent System — configuration.

Model assignments, timeouts, and pipeline settings.
Edit this file to change models or behavior.
"""

# ---------------------------------------------------------------------------
# Model assignments per agent (§4 spec table)
# ---------------------------------------------------------------------------
# Available: "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"

MODELS = {
    # Fast / structured tasks → Sonnet
    "input_parser":     "claude-sonnet-4-6",
    "classifier":       "claude-sonnet-4-6",

    # Deep reasoning → Opus
    "re_builder":       "claude-opus-4-6",
    "dfa_builder":      "claude-opus-4-6",
    "pumping_agent":    "claude-opus-4-6",
    "nerode_agent":     "claude-opus-4-6",
    "closure_agent":    "claude-opus-4-6",
    "grammar_analyzer": "claude-opus-4-6",
    "reasoning_agent":  "claude-opus-4-6",
    "formalizer":       "claude-opus-4-6",

    # Retry planning → Sonnet (fast + smart)
    "retry_planner":    "claude-sonnet-4-6",

    # Quick validation / summarization → Haiku (logs & translation only)
    "validator":        "claude-haiku-4-5-20251001",
    "summarizer":       "claude-haiku-4-5-20251001",
}

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------

# Max tokens per LLM call
MAX_TOKENS = 4096

# Temperature (0 = deterministic)
TEMPERATURE = 0.0

# Lean 4 type check timeout (seconds)
LEAN_TIMEOUT = 120

# Oracle test: max word length for exhaustive enumeration
ORACLE_MAX_EXHAUSTIVE = 7

# Retry counts
LLM_JSON_RETRIES = 1        # retry if LLM returns non-JSON
FORMALIZER_RETRIES = 2       # retry if Lean type check fails (§5.2 Level 4)
