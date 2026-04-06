"""DCFL Agent System — configuration."""
import os

MODELS: dict[str, str] = {
    # Fast / structured → Sonnet
    "input_parser":       "claude-sonnet-4-6",
    "classifier":         "claude-sonnet-4-6",

    # Deep reasoning → Opus
    "stack_strategy":     "claude-opus-4-6",
    "closure_reduction":  "claude-opus-4-6",
    "dcfl_pumping":       "claude-opus-4-6",
    "shallit":            "claude-opus-4-6",
    "inh_ambiguity":      "claude-opus-4-6",
    "reasoning":          "claude-opus-4-6",
}

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

MAX_TOKENS = 32000
MAX_TOKENS_PER_AGENT: dict[str, int] = {}
LLM_JSON_RETRIES = 2
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
