"""Check 2 of 3: does the passage actually support the claim?

Check 1 only proves the quote exists. This asks a separate judge whether the
passage entails the claim: supports, contradicts or unclear. Anything short of
a confident "supports" keeps the claim from being verified.

Backends, picked by SUPPORT_BACKEND (default "auto"):
  nli   - a natural-language-inference model run locally (default
          cross-encoder/nli-deberta-v3-base). Trained for exactly this question;
          returns entailment / neutral / contradiction probabilities.
  laya  - Laya, an open-weight decision model run locally (pip install "pydecide[laya]").
          Its model card warns base checkpoints are near chance without fine-tuning.
  llm   - a free chat model on OpenRouter (SUPPORT_MODELS, else a default list
          that starts with a different family from the extractor), asked the same question.
          Weaker: its probability is self-reported.
  none  - skip; claims stay unverified.
  auto  - nli, else laya, else llm if OPENROUTER_API_KEY is set, else none.
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


def _span(quote: str, passage: str):
    """Where the quote sits in the passage, tolerating punctuation and formatting."""
    import re

    toks = re.findall(r"\w+", quote)
    if not toks:
        return None
    return re.search(r"\W+".join(re.escape(t) for t in toks), passage, flags=re.I)


def window(quote: str, passage: str, words: int = 70) -> str:
    """The quote with up to `words` words of context each side, original wording."""
    m = _span(quote, passage)
    if not m:
        return " ".join(passage.split()[: words * 2])
    before = passage[:m.start()].split()[-words:]
    after = passage[m.end():].split()[:words]
    return " ".join(before + [passage[m.start():m.end()]] + after)


def window_lower(quote: str, passage: str, words: int = 70) -> str:
    """The first format tried: the same window, lowercased with markdown stripped."""
    norm, q = normalize(passage), normalize(quote)
    i = norm.find(q)
    if i == -1:
        return " ".join(norm.split()[: words * 2])
    return " ".join(norm[:i].split()[-words:] + [q] + norm[i + len(q):].split()[:words])


def sentence(quote: str, passage: str) -> str:
    """Just the sentence(s) containing the quote, cleaned of markdown and list markers."""
    import re

    m = _span(quote, passage)
    if not m:
        return quote
    starts = [x.end() for x in re.finditer(r"[.!?](?=\s)|\n|\?\s", passage[:m.start()])]
    start = starts[-1] if starts else 0
    end_m = re.search(r"[.!?](?=\s|$)|\n", passage[m.end():])
    end = m.end() + (end_m.end() if end_m else len(passage) - m.end())
    text = passage[start:end]
    text = re.sub(r"[`*_>#]|📘|^\s*[-•]\s*|^\s*\d+\.\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# How the judge's input is built. Chosen by measurement: python -m atlas.judge_eval --compare-inputs
JUDGE_INPUTS = {
    "window-lower": lambda q, p, src: window_lower(q, p),
    "window": lambda q, p, src: window(q, p),
    "window+source": lambda q, p, src: f"From {src}: {window(q, p)}",
    "sentence": lambda q, p, src: sentence(q, p),
    "sentence+source": lambda q, p, src: f"According to {src}, {sentence(q, p)}",
}
DEFAULT_JUDGE_INPUT = "window-lower"  # 17/22 in the first measured run; replace only on evidence


def judge_input(quote: str, passage: str, source: str, fmt: Optional[str] = None) -> str:
    """What the judge reads. `source` names the speaker, e.g. "Pipedrive's documentation"."""
    fmt = fmt or os.getenv("JUDGE_INPUT", DEFAULT_JUDGE_INPUT)
    return JUDGE_INPUTS.get(fmt, JUDGE_INPUTS[DEFAULT_JUDGE_INPUT])(quote, passage, source)


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


NLI_LABELS = {"entailment": "supports", "neutral": "unclear", "contradiction": "contradicts"}


def nli_probs(logits: list[float], id2label: dict) -> dict[str, float]:
    """Softmax over an NLI model's three labels, renamed to ours."""
    import math

    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    out = {k: 0.0 for k in OPTIONS}
    for i, e in enumerate(exps):
        name = NLI_LABELS.get(str(id2label[i]).lower())
        if name:
            out[name] += e / total
    return out


class NLISupport:
    """A natural-language-inference model: trained for exactly this question
    (does the premise entail the hypothesis?) and returns probabilities for
    entailment, neutral and contradiction. Runs locally on CPU.
    pip install torch transformers sentencepiece protobuf"""

    def __init__(self, model: Optional[str] = None):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model = model or os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-base")
        self._torch = torch
        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForSequenceClassification.from_pretrained(model).eval()
        self.name = f"nli ({model})"

    def check(self, claim: str, passage: str) -> Verdict:
        enc = self.tok(passage, claim, truncation="only_first", max_length=512, return_tensors="pt")
        with self._torch.no_grad():
            logits = self.model(**enc).logits[0].tolist()
        return verdict_from_probs(nli_probs(logits, self.model.config.id2label))


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
        self._by_claim = {r["claim"]: r for r in recorded}  # recordings made before the judge input changed
        self._name = name
        self.last_judge = None

    @property
    def name(self) -> str:
        # Replays report the judge recorded for the last verdict served.
        return self.last_judge or self._name

    def check(self, claim: str, passage: str) -> Verdict:
        r = self._by_key.get((claim, passage)) or self._by_claim.get(claim)
        if r is None:
            return Verdict(CheckResult.not_run, None, "not in the recorded run")
        self.last_judge = r.get("judge")
        return Verdict(CheckResult(r["result"]), r.get("p_supports"), r.get("note", ""))


def make_support(kind: Optional[str] = None) -> SupportChecker:
    """auto tries, in order: an NLI model, Laya, a chat model, nothing.
    NLI first because it is trained for this exact question; Laya's own model
    card says its base checkpoints are near chance on decisions without
    fine-tuning. Compare them on your data with: python -m atlas.judge_eval"""
    kind = (kind or os.getenv("SUPPORT_BACKEND", "auto")).lower()
    if kind in ("nli", "auto"):
        try:
            return NLISupport()
        except ImportError:
            if kind == "nli":
                raise RuntimeError("The NLI judge needs: pip install torch transformers sentencepiece protobuf")
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
