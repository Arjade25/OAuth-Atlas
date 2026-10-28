"""Where research comes from.

LiveSources calls Composio's search toolkit (COMPOSIO_SEARCH_WEB and
COMPOSIO_SEARCH_FETCH_URL_CONTENT). ReplaySources serves a recorded run, so the
whole pipeline can be re-run with no keys and no network.

Search results include a model-written 'answer'. We never use it as evidence:
only the URLs are taken, and every quote must come from a page we fetched.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional, Protocol
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


@dataclass
class Page:
    url: str
    text: str
    fetched_at: date
    passages: Optional[list[str]] = None  # set in replay: the exact passages used


class Sources(Protocol):
    name: str

    def search(self, query: str) -> list[str]: ...

    def fetch(self, urls: list[str]) -> list[Page]: ...


def _get(obj: Any, *names: str) -> Any:
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def urls_in(obj: Any, skip_keys: Iterable[str] = ("answer",)) -> list[str]:
    """Collect URLs from a search response in document order, ignoring the
    model-written answer."""
    found: list[str] = []

    def walk(x: Any, key: str = "") -> None:
        if key in skip_keys:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, str(k))
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            for u in URL_RE.findall(x):
                u = u.rstrip(".,;")
                if u not in found:
                    found.append(u)

    walk(obj)
    return found


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class LiveSources:
    name = "composio"

    def __init__(self, api_key: Optional[str] = None, user_id: str = "oauth-atlas", max_chars: int = 30000):
        from composio import Composio  # imported lazily so tests and replay need no SDK

        key = (api_key or os.getenv("COMPOSIO_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("COMPOSIO_API_KEY is not set. Add it to .env (it starts with ak_).")
        if key.startswith("ck_"):
            raise RuntimeError("COMPOSIO_API_KEY starts with ck_ (a consumer key). Use a project key starting with ak_.")
        self.client = Composio(api_key=key)
        self.user_id = user_id
        self.max_chars = max_chars

    def _run(self, slug: str, arguments: dict) -> Any:
        result = self.client.tools.execute(
            slug=slug, user_id=self.user_id, arguments=arguments, dangerously_skip_version_check=True
        )
        if _get(result, "successful") is False:
            raise RuntimeError(f"{slug} failed: {_get(result, 'error')}")
        return _get(result, "data") or result

    def search(self, query: str) -> list[str]:
        return urls_in(self._run("COMPOSIO_SEARCH_WEB", {"query": query}))

    def fetch(self, urls: list[str]) -> list[Page]:
        try:
            data = self._run("COMPOSIO_SEARCH_FETCH_URL_CONTENT",
                             {"urls": urls, "text": True, "max_characters": self.max_chars})
        except Exception:
            data = self._run("COMPOSIO_SEARCH_FETCH_URL_CONTENT", {"urls": urls})
        pages = []
        for r in _get(data, "results") or []:
            url = _get(r, "url", "id")
            text = _get(r, "text") or ""
            if url and text.strip():
                pages.append(Page(url=url, text=text, fetched_at=date.today()))
        return pages


@dataclass
class ReplaySources:
    """Serves the searches and passages recorded in a run file."""
    run: dict
    name: str = "replay"
    _searches: dict = field(default_factory=dict)

    def __post_init__(self):
        self._searches = {s["query"]: s["urls"] for s in self.run.get("searches", [])}
        self._pages = {p["url"]: p for p in self.run.get("pages", [])}

    def search(self, query: str) -> list[str]:
        return list(self._searches.get(query, []))

    def fetch(self, urls: list[str]) -> list[Page]:
        wanted = set(urls)
        # Recorded order, so passage numbering matches the original run.
        return [Page(url=p["url"], text="\n\n".join(p["passages"]),
                     fetched_at=date.fromisoformat(p["fetched_at"]), passages=list(p["passages"]))
                for p in self.run.get("pages", []) if p["url"] in wanted]
