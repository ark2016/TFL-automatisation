"""
LLM client — Anthropic API wrapper for TFL Agent System.

Reads system prompts from prompts/, sends structured requests to Claude,
and parses JSON responses. Model config lives in config.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Import config (with fallback defaults if missing)
try:
    from agent_system.config import (
        MODELS as _CFG_MODELS, MAX_TOKENS as _CFG_MAX_TOKENS, TEMPERATURE as _CFG_TEMP,
        EFFORT as _CFG_EFFORT, DEFAULT_EFFORT as _CFG_DEFAULT_EFFORT,
        REFUSAL_FALLBACK as _CFG_REFUSAL_FALLBACK,
    )
except ImportError:
    _CFG_MODELS = {}
    _CFG_MAX_TOKENS = 64000
    _CFG_TEMP = 0.0
    _CFG_EFFORT = {}
    _CFG_DEFAULT_EFFORT = "high"
    _CFG_REFUSAL_FALLBACK = True

# Legacy models — Haiku 4.5 and anything before the 4.6 family — take
# sampling parameters and have no adaptive thinking / effort. Opus/Sonnet
# 4.6+ and every 5.x model run adaptive thinking steered by `effort`, and
# Opus 4.7+, Sonnet 5 and Opus 5.x reject `temperature` with a 400.
_LEGACY_MODEL_RE = re.compile(r"^claude-3|haiku|-4(-[015])?(-\d{8})?$")
# Models that get the server-side refusal fallback (see REFUSAL_FALLBACK).
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5")


def _is_adaptive_model(model: str) -> bool:
    """Whether `model` runs adaptive thinking (and rejects `temperature`)."""
    return bool(model) and not _LEGACY_MODEL_RE.search(model)


def _response_text(message: Any) -> str:
    """Concatenate the text blocks of a response.

    With adaptive thinking the response may start with `thinking` blocks, so
    content[0] is not necessarily the answer — read blocks by type.
    """
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )

# Prompt file name → agent name (some have _agent suffix in file)
_PROMPT_ALIASES: dict[str, str] = {
    "pumping": "pumping_agent",
    "nerode": "nerode_agent",
    "closure": "closure_agent",
    "reasoning": "reasoning_agent",
    "grammar": "grammar_analyzer",
}


def _load_env() -> None:
    """Load .env file from agent_system/ root if it exists."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _extract_json(text: str) -> dict | None:
    """Extract first JSON object from LLM response text.

    Handles:
    - Raw JSON
    - JSON inside ```json ... ``` fences
    - JSON embedded in prose
    """
    # Try raw parse first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try fenced code block
    m = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None

    return None


class LLMRunner:
    """Run LLM agents via the Anthropic API.

    Reads system prompts from the prompts/ directory, sends input_data
    as a user message, and parses structured JSON from the response.
    """

    def __init__(
        self,
        prompt_dir: str | Path | None = None,
        model_map: dict[str, str] | None = None,
        api_key: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        effort_map: dict[str, str] | None = None,
    ) -> None:
        _load_env()

        self.prompt_dir = Path(
            prompt_dir
            or Path(__file__).resolve().parent.parent / "prompts"
        )
        self.model_map = model_map or dict(_CFG_MODELS)
        self.max_tokens = max_tokens if max_tokens is not None else _CFG_MAX_TOKENS
        self.temperature = temperature if temperature is not None else _CFG_TEMP
        self.effort_map = effort_map or dict(_CFG_EFFORT)
        self.default_effort = _CFG_DEFAULT_EFFORT
        self.refusal_fallback = _CFG_REFUSAL_FALLBACK

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. "
                "Put it in agent_system/.env or export it."
            )

        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        self._client = anthropic.Anthropic(api_key=resolved_key)

    # ---- prompt resolution ------------------------------------------------

    def _resolve_prompt_name(self, agent_name: str) -> str:
        """Map agent_name to prompt file name (without .md)."""
        return _PROMPT_ALIASES.get(agent_name, agent_name)

    def _load_prompt(self, agent_name: str) -> str:
        """Load system prompt for an agent."""
        prompt_name = self._resolve_prompt_name(agent_name)
        path = self.prompt_dir / f"{prompt_name}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {path}"
            )
        return path.read_text(encoding="utf-8")

    def _get_model(self, agent_name: str) -> str:
        """Get model ID for an agent. Checks: env override → model_map → default.

        TFL_MODEL_OVERRIDE forces every agent onto one model (cheap live test
        runs, e.g. claude-haiku-4-5); TFL_MODEL_FAST / TFL_MODEL_DEEP override
        the fast / deep tier only.
        """
        override = os.environ.get("TFL_MODEL_OVERRIDE", "").strip()
        if override:
            return override
        prompt_name = self._resolve_prompt_name(agent_name)
        # Env overrides (TFL_MODEL_DEEP, TFL_MODEL_FAST)
        if prompt_name in ("input_parser", "classifier"):
            env = os.environ.get("TFL_MODEL_FAST")
            if env:
                return env
        else:
            env = os.environ.get("TFL_MODEL_DEEP")
            if env:
                return env
        return self.model_map.get(
            prompt_name, self.model_map.get(agent_name, "claude-sonnet-5")
        )

    def _get_effort(self, agent_name: str) -> str:
        """Get effort level for an agent: effort_map → DEFAULT_EFFORT."""
        prompt_name = self._resolve_prompt_name(agent_name)
        return self.effort_map.get(
            prompt_name, self.effort_map.get(agent_name, self.default_effort)
        )

    def _build_request_kwargs(
        self, model: str, max_tokens: int, system: str, user: str,
        effort: str | None = None, temperature: float | None = None,
    ) -> dict[str, Any]:
        """Build kwargs for messages.stream / messages.create.

        Thinking models get adaptive thinking + an explicit effort (Opus 5.5
        would silently default to "medium"); legacy models get temperature.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if _is_adaptive_model(model):
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effort or self.default_effort}
            if self.refusal_fallback and model.startswith(_FALLBACK_MODEL_PREFIXES):
                kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
                kwargs["extra_body"] = {"fallbacks": "default"}
        else:
            kwargs["temperature"] = self.temperature if temperature is None else temperature
        return kwargs

    def _stream_text(self, model: str, system: str, user: str, effort: str) -> str | None:
        """Make one streamed API call and return the answer text.

        Streaming keeps a large max_tokens (thinking + answer) clear of the
        SDK's non-streaming timeout. Returns None on API errors and refusals.
        """
        try:
            kwargs = self._build_request_kwargs(model, self.max_tokens, system, user, effort)
            with self._client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except Exception as exc:
            print(f"[LLM] API error: {exc}", file=sys.stderr)
            return None
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            print(f"[LLM] {model} refused the request (category={category})", file=sys.stderr)
            return None
        if message.stop_reason == "max_tokens":
            print(f"[LLM] {model} hit max_tokens={self.max_tokens}; output truncated", file=sys.stderr)
        return _response_text(message)

    # ---- main API ---------------------------------------------------------

    # Agents that return plain text (not JSON)
    _RAW_TEXT_AGENTS = frozenset({"formalizer"})

    def run_agent(self, agent_name: str, input_data: Any = None) -> dict | None:
        """Call an LLM agent and return parsed output.

        For most agents: parses JSON from the response.
        For formalizer: returns raw text (Lean 4 code) wrapped in evidence.
        """
        system_prompt = self._load_prompt(agent_name)
        model = self._get_model(agent_name)
        effort = self._get_effort(agent_name)

        if isinstance(input_data, (dict, list)):
            user_msg = json.dumps(input_data, indent=2, ensure_ascii=False)
        elif input_data is not None:
            user_msg = str(input_data)
        else:
            user_msg = "{}"

        # Inject student notes into the prompt if present
        student_notes = ""
        if isinstance(input_data, dict):
            student_notes = input_data.get("student_notes", "")
        if student_notes:
            system_prompt += (
                "\n\n## Student Notes\n\n"
                "The student provided the following comments, ideas, or partial "
                "solutions. Take these into account — they may contain useful "
                "insights, hypotheses to verify, or mistakes to address:\n\n"
                f"{student_notes}\n"
            )

        # Formalizer: return raw text, not JSON
        if agent_name in self._RAW_TEXT_AGENTS:
            raw = self._call_raw(system_prompt, user_msg, model, effort)
            if raw is not None:
                # Strip markdown fences if present
                code = raw.strip()
                if code.startswith("```"):
                    lines = code.split("\n")
                    lines = lines[1:]  # remove opening fence
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    code = "\n".join(lines)
                return {
                    "module": agent_name,
                    "status": "success",
                    "evidence": {"lean_code": code, "output": code},
                    "confidence": 0.8,
                    "errors": [],
                }
            return None

        # Standard JSON agents
        parsed = self._call_and_parse(system_prompt, user_msg, model, effort)
        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        # Retry once with explicit JSON instruction
        retry_msg = (
            f"{user_msg}\n\n"
            "IMPORTANT: Your previous response was not valid JSON. "
            "Please respond with ONLY a valid JSON object, no other text."
        )
        parsed = self._call_and_parse(system_prompt, retry_msg, model, effort)
        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        return None

    def _call_raw(
        self, system: str, user: str, model: str, effort: str | None = None
    ) -> str | None:
        """Make one API call and return raw response text."""
        return self._stream_text(model, system, user, effort or self.default_effort)

    def _call_and_parse(
        self, system: str, user: str, model: str, effort: str | None = None
    ) -> dict | None:
        """Make one API call and try to parse JSON from response."""
        text = self._stream_text(model, system, user, effort or self.default_effort)
        return _extract_json(text) if text is not None else None

    def quick_validate(self, question: str, data: Any) -> str:
        """Fast validation / sanity check via Haiku.

        Returns a short natural-language assessment (1-3 sentences).
        Used for intermediate progress output.
        """
        model = self.model_map.get("validator", "claude-haiku-4-5")
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            data_str = str(data)

        try:
            response = self._client.messages.create(**self._build_request_kwargs(
                model,
                max_tokens=256,
                system="You are a formal language theory expert. Answer in 1-2 sentences, in Russian.",
                user=f"{question}\n\nДанные:\n{data_str[:2000]}",
                effort="low",
                temperature=0.0,
            ))
            return _response_text(response).strip()
        except Exception as exc:
            return f"(validation error: {exc})"

    @staticmethod
    def _wrap_output(agent_name: str, parsed: dict) -> dict:
        """Wrap parsed LLM output in §5.3 contract format."""
        # If already wrapped, return as-is
        if "module" in parsed and "status" in parsed:
            return parsed
        return {
            "module": agent_name,
            "status": parsed.get("status", "success"),
            "evidence": parsed,
            "confidence": parsed.get("confidence", 0.8),
            "errors": parsed.get("errors", []),
        }
