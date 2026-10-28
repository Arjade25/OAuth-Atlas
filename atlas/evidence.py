"""Check 1 of 3: does the quoted evidence actually appear on the stored page?

Deterministic and offline: it compares the claim's quote with page text that
was fetched and stored earlier. It proves the agent found the passage. It does
NOT prove the passage supports the claim; that is check 2 (atlas.support).
"""

from __future__ import annotations

import re
import unicodedata

from .schema import CheckResult

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def normalize(text: str) -> str:
    """Fold the differences page-to-text conversion introduces: case,
    whitespace, curly quotes, dashes, markdown emphasis, trailing punctuation."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip(".,;:").lower()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def _contains_words(haystack: list[str], needle: list[str], start: int = 0) -> int:
    """Index just past the first run of `needle` in `haystack` at or after
    `start`, or -1. Words must match exactly and in order; punctuation,
    formatting and table or list markup between them are ignored."""
    n = len(needle)
    for i in range(start, len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return i + n
    return -1


def quote_in_page(quote: str, page_text: str) -> CheckResult:
    """The quote's words appear in the page, in order. A quote that joins
    fragments with '...' passes only if every fragment appears, in that order,
    in the same page; a changed or added word still fails."""
    fragments = [f for f in re.split(r"\.\.\.|\u2026", quote)]
    fragments = [_words(f) for f in fragments]
    fragments = [f for f in fragments if f]
    # Each fragment must be long enough to identify its place on the page.
    if not fragments or any(len(f) < (3 if len(fragments) == 1 else 4) for f in fragments):
        return CheckResult.failed
    page = _words(page_text)
    pos = 0
    for f in fragments:
        pos = _contains_words(page, f, pos)
        if pos == -1:
            return CheckResult.failed
    return CheckResult.passed
