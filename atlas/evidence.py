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


def quote_in_page(quote: str, page_text: str) -> CheckResult:
    q = normalize(quote)
    if not q:
        return CheckResult.failed
    return CheckResult.passed if q in normalize(page_text) else CheckResult.failed
