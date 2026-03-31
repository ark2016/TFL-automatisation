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
    from agent_system.config import MODELS as _CFG_MODELS, MAX_TOKENS as _CFG_MAX_TOKENS, TEMPERATURE as _CFG_TEMP
except ImportError:
    _CFG_MODELS = {}
    _CFG_MAX_TOKENS = 4096
    _CFG_TEMP = 0.0

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
    ) -> None:
        _load_env()

        self.prompt_dir = Path(
            prompt_dir
            or Path(__file__).resolve().parent.parent / "prompts"
        )
        self.model_map = model_map or dict(_CFG_MODELS)
        self.max_tokens = max_tokens if max_tokens is not None else _CFG_MAX_TOKENS
        self.temperature = temperature if temperature is not None else _CFG_TEMP

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
        """Get model ID for an agent. Checks: env override → model_map → default."""
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
            prompt_name, self.model_map.get(agent_name, "claude-sonnet-4-6")
        )

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
            raw = self._call_raw(system_prompt, user_msg, model)
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
        parsed = self._call_and_parse(system_prompt, user_msg, model)
        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        # Retry once with explicit JSON instruction
        retry_msg = (
            f"{user_msg}\n\n"
            "IMPORTANT: Your previous response was not valid JSON. "
            "Please respond with ONLY a valid JSON object, no other text."
        )
        parsed = self._call_and_parse(system_prompt, retry_msg, model)
        if parsed is not None:
            return self._wrap_output(agent_name, parsed)

        return None

    def _call_raw(
        self, system: str, user: str, model: str
    ) -> str | None:
        """Make one API call and return raw response text."""
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=8192,  # formalizer needs more tokens
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text
        except Exception as exc:
            print(f"[LLM] API error: {exc}", file=sys.stderr)
            return None

    def _call_and_parse(
        self, system: str, user: str, model: str
    ) -> dict | None:
        """Make one API call and try to parse JSON from response."""
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = response.content[0].text
            return _extract_json(text)
        except Exception as exc:
            print(f"[LLM] API error: {exc}", file=sys.stderr)
            return None

    def quick_validate(self, question: str, data: Any) -> str:
        """Fast validation / sanity check via Haiku.

        Returns a short natural-language assessment (1-3 sentences).
        Used for intermediate progress output.
        """
        model = self.model_map.get("validator", "claude-haiku-4-5-20251001")
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            data_str = str(data)

        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=256,
                temperature=0.0,
                system="You are a formal language theory expert. Answer in 1-2 sentences, in Russian.",
                messages=[{
                    "role": "user",
                    "content": f"{question}\n\nДанные:\n{data_str[:2000]}",
                }],
            )
            return response.content[0].text.strip()
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
