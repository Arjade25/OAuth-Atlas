"""Pick the passages worth reading.

Pages are split into chunks of roughly 60 to 160 words and ranked with BM25
against OAuth-setup vocabulary plus the app's name. Only the top chunks go to
the model, and the model may only quote from them. No model, no network.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .sources import Page

TOPIC_TERMS = (
    "oauth app application client id secret redirect callback url register create developer console portal "
    "review approval approve verification verify verified marketplace listing listed unlisted publish public "
    "distribution install installs limit scopes scope permission permissions restricted sensitive security "
    "assessment audit test testing users business privacy policy partner program days weeks months annual "
    "requirement required must submit submission"
).split()

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Passage:
    pid: str
    url: str
    text: str
    score: float = 0.0


def chunk(text: str, target: int = 120, hard_max: int = 160) -> list[str]:
    """Merge paragraphs up to about `target` words; split very long ones on sentences."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p.split()) <= hard_max:
            pieces.append(p)
            continue
        buf: list[str] = []
        for s in re.split(r"(?<=[.!?])\s+", p):
            if buf and len(" ".join(buf + [s]).split()) > hard_max:
                pieces.append(" ".join(buf))
                buf = []
            buf.append(s)
        if buf:
            pieces.append(" ".join(buf))

    chunks: list[str] = []
    buf = ""
    for p in pieces:
        if buf and len((buf + " " + p).split()) > target:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c.split()) >= 8]


def bm25(query: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs)
    if n == 0:
        return []
    avg = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    qset = set(query)
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in qset:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    return scores


def select(pages: list[Page], app: str, total: int = 28, per_page: int = 5) -> list[Passage]:
    """Top passages across all pages, at most `per_page` from any one page,
    returned grouped by page in their original order (easier for the model to read)."""
    candidates: list[Passage] = []
    for page in pages:
        texts = page.passages if page.passages is not None else chunk(page.text)
        for t in texts:
            candidates.append(Passage(pid="", url=page.url, text=t))
    if not candidates:
        return []

    query = TOPIC_TERMS + tokens(app) * 2
    for p, s in zip(candidates, bm25(query, [tokens(c.text) for c in candidates])):
        p.score = s

    taken: dict[str, int] = {}
    chosen: list[Passage] = []
    for p in sorted(candidates, key=lambda c: c.score, reverse=True):
        if len(chosen) >= total:
            break
        if taken.get(p.url, 0) >= per_page or p.score <= 0:
            continue
        taken[p.url] = taken.get(p.url, 0) + 1
        chosen.append(p)

    order = {id(c): i for i, c in enumerate(candidates)}
    chosen.sort(key=lambda c: order[id(c)])
    for i, p in enumerate(chosen, 1):
        p.pid = f"P{i}"
    return chosen
