"""Anthropic SDK wrapper for LLM calls."""
import os
import json
from typing import Optional, List, Dict, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None


class LLMClient:
    """Wrapper around Anthropic Claude API for language analysis."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-sonnet-4-5"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if Anthropic is None:
                raise ImportError("anthropic package required: pip install anthropic")
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 temperature: float = 0.3) -> str:
        """Send a completion request to Claude."""
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        response = self.client.messages.create(**kwargs)
        return response.content[0].text

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096) -> Dict:
        """Send request expecting JSON response."""
        full_prompt = prompt + "\n\nОтветь ТОЛЬКО валидным JSON, без markdown."
        text = self.complete(full_prompt, system=system, max_tokens=max_tokens)
        # Try to extract JSON from response
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(text)

    def is_available(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key)
