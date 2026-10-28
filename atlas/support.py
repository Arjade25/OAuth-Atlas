"""Check 2 of 3: does the passage actually support the claim?

Check 1 only proves the quote exists. This asks a separate judge whether the
passage entails the claim: supports, contradicts or unclear. Anything short of
a confident "supports" keeps the claim from being verified.

Backends, picked by SUPPORT_BACKEND (default "auto"):
  laya  - Laya, an open-weight decision model run locally (pip install "pydecide[laya]").
          Returns probabilities over the three options. Different model family
          from the extractor, which is the point.
  llm   - a free chat model on OpenRouter (SUPPORT_MODELS, else a default list
          that starts with a different family from the extractor), asked the same question.
          Weaker: its probability is self-reported.
  none  - skip; claims stay unverified.
  auto  - laya if installed, else llm if OPENROUTER_API_KEY is set, else none.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol

from .evidence import normalize
from .schema import CheckResult

INSTRUCTIONS = (
    "Does the passage support the claim? Choose 'supports' only if the passage states everything the claim says, "
    "including every number, limit and condition. If the claim adds anything the passage does not say, choose 'unclear'. "
    "In particular: if the claim says something is required, must happen, or is the only option, the passage itself "
    "must say so. An instruction such as 'Insert a link to your website' does not show that a website is required. "
    "Ignore differences in quotation marks, capitalisation and formatting."
)
OPTIONS = {
    "supports": "the passage states the claim, adding nothing",
    "contradicts": "the passage says something incompatible with the claim",
    "unclear": "the passage is related but does not establish the claim, or the claim says more than the passage",
}


@dataclass
class Verdict:
    result: CheckResult
    p_supports: Optional[float]
    note: str = ""


def threshold() -> float:
    try:
        return float(os.getenv("SUPPORT_THRESHOLD", "0.75"))
    except ValueError:
        return 0.75


def verdict_from_probs(probs: dict[str, float], cutoff: Optional[float] = None) -> Verdict:
    cutoff = threshold() if cutoff is None else cutoff
    p = float(probs.get("supports", 0.0))
    top = max(probs, key=probs.get) if probs else "unclear"
    if top == "contradicts":
        return Verdict(CheckResult.failed, p, "the judge found the passage contradicts the claim")
    if top == "supports" and p >= cutoff:
        return Verdict(CheckResult.passed, p)
    return Verdict(CheckResult.unclear, p, f"support probability {p:.2f} is below {cutoff:.2f}")


def window(quote: str, passage: str, words: int = 70) -> str:
    """The quote with some context on each side, short enough for Laya's 512 tokens."""
    norm = normalize(passage)
    q = normalize(quote)
    i = norm.find(q)
    if i == -1:
        return " ".join(passage.split()[: words * 2])
    before = norm[:i].split()[-words:]
    after = norm[i + len(q):].split()[:words]
    return " ".join(before + [q] + after)


class SupportChecker(Protocol):
    name: str

    def check(self, claim: str, passage: str) -> Verdict: ...


class LayaSupport:
    def __init__(self, model: Optional[str] = None):
        from decide import Choice, Client  # pip install "pydecide[laya]"
        from decide.backends.laya import LayaBackend

        model = model or os.getenv("LAYA_MODEL", "convaiinnovations/laya")
        self._Choice = Choice
        self.client = Client([LayaBackend(model)])
        self.name = f"laya ({model})"

    def check(self, claim: str, passage: str) -> Verdict:
        r = self.client.decide(state={"claim": claim, "passage": passage},
                               questions={"verdict": self._Choice(INSTRUCTIONS, OPTIONS)})
        return verdict_from_probs(dict(r.choices["verdict"].probabilities))


class LLMSupport:
    def __init__(self, models=None):
        from .llm import DEFAULT_SUPPORT_CHAIN, OpenRouterModel, model_chain

        chain = models or model_chain("SUPPORT_MODELS", ("SUPPORT_MODEL",), DEFAULT_SUPPORT_CHAIN)
        self.model = OpenRouterModel(models=chain, max_tokens=4000, reasoning_tokens=1500)

    @property
    def name(self) -> str:
        return f"llm ({self.model.name})"

    def check(self, claim: str, passage: str) -> Verdict:
        from .llm import parse_json

        prompt = (f"{INSTRUCTIONS}\nOptions: {OPTIONS}\n\nClaim: {claim}\n\nPassage: {passage}\n\n"
                  'Reply with JSON only: {"supports": p, "contradicts": p, "unclear": p} with probabilities summing to 1.')
        try:
            probs = parse_json(self.model.complete([{"role": "user", "content": prompt}]))
            probs = {k: float(probs.get(k, 0)) for k in OPTIONS}
        except Exception as e:
            return Verdict(CheckResult.unclear, None, f"support check failed: {e}")
        return verdict_from_probs(probs)


class NoSupport:
    name = "none"

    def check(self, claim: str, passage: str) -> Verdict:
        return Verdict(CheckResult.not_run, None, "no support checker configured")


class ReplaySupport:
    """Returns the verdicts recorded for each (claim, passage) pair."""

    def __init__(self, recorded: list[dict], name: str = "replay"):
        self._by_key = {(r["claim"], r["passage"]): r for r in recorded}
        self._name = name
        self.last_judge = None

    @property
    def name(self) -> str:
        # Replays report the judge recorded for the last verdict served.
        return self.last_judge or self._name

    def check(self, claim: str, passage: str) -> Verdict:
        r = self._by_key.get((claim, passage))
        if r is None:
            return Verdict(CheckResult.not_run, None, "not in the recorded run")
        self.last_judge = r.get("judge")
        return Verdict(CheckResult(r["result"]), r.get("p_supports"), r.get("note", ""))


def make_support(kind: Optional[str] = None) -> SupportChecker:
    kind = (kind or os.getenv("SUPPORT_BACKEND", "auto")).lower()
    if kind in ("laya", "auto"):
        try:
            return LayaSupport()
        except ImportError:
            if kind == "laya":
                raise RuntimeError('Laya is not installed. Run: pip install "pydecide[laya]"')
    if kind in ("llm", "auto") and os.getenv("OPENROUTER_API_KEY"):
        return LLMSupport()
    if kind == "llm":
        raise RuntimeError("SUPPORT_BACKEND=llm needs OPENROUTER_API_KEY")
    return NoSupport()
