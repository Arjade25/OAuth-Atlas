"""Chat model access through OpenRouter, plus a replay double.

Models are tried in order: EXTRACTION_MODELS (comma-separated) if set, else
EXTRACTION_MODEL, FALLBACK_MODEL, then a default list of free models. A model
that is refused, times out or replies empty is skipped; a rate-limited one is
retried once after a pause. Reasoning is capped (REASONING_MAX_TOKENS) so it
can't crowd out the reply. Output must be JSON; parsing is forgiving about
code fences and reasoning text.
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


# Free models on OpenRouter come and go. These are tried in order after any
# models named in .env; a model that is refused or rate-limited is skipped.
DEFAULT_EXTRACTION_CHAIN = [
    "nvidia/nemotron-3.5-super-vl:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "thinkingmachines/inkling:free",
    "dots-studio/dots-3-note-preview:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
]
# The support judge prefers a different family from the extractor.
DEFAULT_SUPPORT_CHAIN = [
    "thinkingmachines/inkling:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]


def model_chain(env_list: str, singles: tuple[str, ...], defaults: list[str]) -> list[str]:
    """Models from a comma-separated env var if set, else the single-model env
    vars followed by the defaults. Order kept, duplicates dropped."""
    listed = [m.strip() for m in os.getenv(env_list, "").split(",") if m.strip()]
    chain = listed or [os.getenv(s, "").strip() for s in singles] + defaults
    return list(dict.fromkeys(m for m in chain if m))


def _short(e: Exception) -> str:
    text = str(e)
    m = re.search(r"Error code: (\d+)", text)
    if "unavailable for free" in text.lower():
        return "no longer free"
    if m:
        return {"401": "key rejected", "402": "needs credits", "404": "not found or not free",
                "429": "rate-limited"}.get(m.group(1), f"HTTP {m.group(1)}")
    if "timed out" in text.lower() or type(e).__name__ in ("APITimeoutError", "TimeoutError"):
        return "timed out"
    return f"{type(e).__name__}: {text[:120]}"


class OpenRouterModel:
    def __init__(self, models: Optional[list[str]] = None, api_key: Optional[str] = None,
                 max_tokens: int = 16000, timeout: float = 300, client=None,
                 reasoning_tokens: Optional[int] = None, retry_wait: float = 20, sleep=None):
        import time

        self.reasoning_tokens = reasoning_tokens or int(os.getenv("REASONING_MAX_TOKENS", "4000"))
        self.retry_wait = retry_wait
        self.sleep = sleep or time.sleep
        if client is None:
            from openai import OpenAI  # lazy: replay and tests need no SDK

            key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
            if not key:
                raise RuntimeError("OPENROUTER_API_KEY is not set. Add it to .env.")
            client = OpenAI(base_url=OPENROUTER_URL, api_key=key, timeout=timeout)
        self.client = client
        self.models = models or model_chain("EXTRACTION_MODELS", ("EXTRACTION_MODEL", "FALLBACK_MODEL"),
                                            DEFAULT_EXTRACTION_CHAIN)
        self.max_tokens = max_tokens
        self.name = self.models[0]
        self.failures: dict[str, str] = {}  # model -> why it was skipped

    def _call(self, m: str, messages: list[dict]):
        return self.client.chat.completions.create(
            model=m, messages=messages, max_tokens=self.max_tokens, temperature=0.1,
            # Cap thinking so it can't use up the whole allowance and leave the reply empty.
            extra_body={"reasoning": {"max_tokens": self.reasoning_tokens}})

    def complete(self, messages: list[dict]) -> str:
        for m in self.models:
            if self.failures.get(m) in ("no longer free", "not found or not free"):
                continue  # don't retry a model that is gone
            for attempt in range(2):
                try:
                    resp = self._call(m, messages)
                    choice = resp.choices[0]
                    content = (choice.message.content or "").strip()
                    if content:
                        self.name = m
                        self.failures.pop(m, None)
                        return content
                    finish = getattr(choice, "finish_reason", None)
                    self.failures[m] = ("ran out of tokens before replying (finish_reason=length)"
                                        if finish == "length" else f"empty reply (finish_reason={finish})")
                    break
                except Exception as e:  # refused, rate limit, outage, timeout
                    why = _short(e)
                    if why == "key rejected":
                        raise RuntimeError("OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env.")
                    self.failures[m] = why
                    if why == "rate-limited" and attempt == 0:
                        self.sleep(self.retry_wait)  # free tiers allow a few requests a minute
                        continue
                    break
        tried = "; ".join(f"{m}: {why}" for m, why in self.failures.items())
        raise RuntimeError(f"No model answered. {tried}")


class ReplayModel:
    """Returns the recorded reply(s) in order."""

    def __init__(self, replies: list[str], name: str = "replay"):
        self.replies = list(replies)
        self.name = name

    def complete(self, messages: list[dict]) -> str:
        if not self.replies:
            raise RuntimeError("The recorded run has no more model replies")
        return self.replies.pop(0)
