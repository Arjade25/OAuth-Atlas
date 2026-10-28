"""Chat model access through OpenRouter, plus a replay double.

The model is a config value (EXTRACTION_MODEL, with FALLBACK_MODEL used when
the first one errors). Output must be JSON; parsing is forgiving about code
fences and reasoning text, and the caller decides whether to retry.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional, Protocol

OPENROUTER_URL = "https://openrouter.ai/api/v1"


class ChatModel(Protocol):
    name: str

    def complete(self, messages: list[dict]) -> str: ...


def parse_json(text: str) -> dict:
    """Pull the JSON object out of a model reply."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if m:
        candidate = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("The model reply contains no JSON object")
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))  # trailing commas


class OpenRouterModel:
    def __init__(self, model: Optional[str] = None, fallback: Optional[str] = None,
                 api_key: Optional[str] = None, max_tokens: int = 16000, timeout: float = 240):
        from openai import OpenAI  # lazy: replay and tests need no SDK

        key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set. Add it to .env.")
        self.client = OpenAI(base_url=OPENROUTER_URL, api_key=key, timeout=timeout)
        self.models = [m for m in (model or os.getenv("EXTRACTION_MODEL") or "nvidia/nemotron-3.5-super-vl:free",
                                   fallback or os.getenv("FALLBACK_MODEL")) if m]
        self.max_tokens = max_tokens
        self.name = self.models[0]

    def complete(self, messages: list[dict]) -> str:
        last: Exception | None = None
        for m in self.models:
            try:
                resp = self.client.chat.completions.create(
                    model=m, messages=messages, max_tokens=self.max_tokens, temperature=0.1)
                content = (resp.choices[0].message.content or "").strip()
                if content:
                    self.name = m
                    return content
                last = RuntimeError(f"{m} returned an empty reply")
            except Exception as e:  # rate limit, outage, bad request
                last = e
        raise RuntimeError(f"Every model failed. Last error: {last}")


class ReplayModel:
    """Returns the recorded reply(s) in order."""

    def __init__(self, replies: list[str], name: str = "replay"):
        self.replies = list(replies)
        self.name = name

    def complete(self, messages: list[dict]) -> str:
        if not self.replies:
            raise RuntimeError("The recorded run has no more model replies")
        return self.replies.pop(0)
