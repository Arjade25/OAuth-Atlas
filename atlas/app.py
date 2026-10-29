"""Web app: the guide library, guide pages and live guide generation.

Run:  uvicorn atlas.app:app --reload
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import pipeline
from .extract import slugify
from .library import load_library
from .managed import DOCS as MANAGED_DOCS
from .policy import check_guide
from .schema import Guide, Range, Scenario
from .scoring import DIMENSION_LABELS, driver, label, scenario_score

HERE = Path(__file__).resolve().parent
app = FastAPI(title="OAuth Atlas")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

STATE_TEXT = {
    "available_now": "Available now",
    "available_for_testing": "Testing only",
    "production_blocked": "Blocked until approved",
    "unconfirmed": "Not confirmed open",
}
OWNER_TEXT = {"agent": "Agent can do this", "you": "You do this", "provider": "Provider does this"}


def fmt_hours(r: Range) -> str:
    def one(x: float) -> str:
        if x < 1:
            return f"{round(x * 60)} min"
        return f"{x:g} h"
    return one(r.min) if r.exact else f"{one(r.min)} to {one(r.max)}"


def fmt_wait(s: Scenario, g: Guide) -> str:
    if s.wait_days is None:
        wait_claims = [g.claim(c) for c in s.claims if g.claim(c) and g.claim(c).kind.value == "wait_time"]
        # A claim may give a number that is not a wait (a deadline, an anecdote),
        # or one that failed its checks; neither is shown as a duration.
        if not wait_claims:
            return "Not published"
        return "No verified duration" if any(c.wait_days for c in wait_claims) else "Not published as a number"
    if s.wait_days.max == 0:
        return "None"
    r = s.wait_days
    return f"{r.min:g} days" if r.exact else f"{r.min:g} to {r.max:g} days"


def effort_basis(g: Guide) -> str:
    """Effort hours are estimates, never measurements: say whose."""
    return "author's estimate" if g.origin.value == "curated" else "model's estimate, unreviewed"


def scenario_view(s: Scenario, g: Guide) -> dict:
    n = scenario_score(s)
    return {
        "s": s,
        "score": n,
        "score_label": label(n),
        "driver": DIMENSION_LABELS[driver(s.dimensions)],
        "state_text": STATE_TEXT[s.state.value],
        "hours": fmt_hours(s.effort_hours),
        "hours_basis": effort_basis(g),
        "wait": fmt_wait(s, g),
        "dimensions": [(DIMENSION_LABELS[k], v) for k, v in s.dimensions.model_dump().items()],
        "blocked_by": [g.claim(c) for c in s.blocked_by if g.claim(c)],
    }


def managed_view(g: Guide) -> dict:
    """The first question on every guide: do I need my own OAuth app at all?"""
    c = g.composio
    if c.managed_auth_available:
        answer, kind = (f"Not to start. Composio manages an OAuth app for {g.app} "
                        f"({', '.join(c.managed_auth_schemes)}), so your users can connect today. You need your "
                        "own for your brand on the consent screen, custom scopes or your own rate limits."), "no"
    elif c.managed_auth_available is False:
        answer, kind = f"Yes. Composio has a {g.app} toolkit but doesn't manage an OAuth app for it.", "yes"
    elif c.checked_on:
        answer, kind = f"Probably. Composio's toolkits API has no toolkit for {g.app}.", "yes"
    else:
        answer, kind = "Not checked yet against Composio's managed OAuth directory.", "unknown"
    return {"answer": answer, "kind": kind, "checked_on": c.checked_on, "docs": MANAGED_DOCS}


def guide_view(g: Guide) -> dict:
    numbering = {c.id: i + 1 for i, c in enumerate(g.claims)}
    issues = check_guide(g)
    statuses = [c.status.value for c in g.claims]
    return {
        "g": g,
        "counts": {"total": len(statuses), "verified": statuses.count("verified"),
                   "flagged": statuses.count("flagged"), "unverified": statuses.count("unverified")},
        "errors": [i for i in issues if i.is_error],
        "scenarios": [scenario_view(s, g) for s in g.scenarios],
        "managed": managed_view(g),
        "numbering": numbering,
        "issues": issues,
        "warnings": {i.ref for i in issues if not i.is_error and i.ref},
        "owner_text": OWNER_TEXT,
        "max_score": max(scenario_score(s) for s in g.scenarios),
        "min_score": min(scenario_score(s) for s in g.scenarios),
    }


@app.get("/", response_class=HTMLResponse)
def library(request: Request):
    guides = [guide_view(g) for g in load_library().values()]
    guides.sort(key=lambda v: (v["min_score"], v["g"].app))
    return templates.TemplateResponse(request, "library.html", {"guides": guides})


@app.get("/guides/{slug}", response_class=HTMLResponse)
def guide_page(request: Request, slug: str):
    g = load_library().get(slug)
    if g is None:
        raise HTTPException(404, f"No guide for '{slug}' yet")
    return templates.TemplateResponse(request, "guide.html", guide_view(g))


@app.get("/api/guides")
def api_list():
    return [
        {"slug": g.slug, "app": g.app, "scenarios": [
            {"id": s.id, "state": s.state.value, "score": scenario_score(s)} for s in g.scenarios]}
        for g in load_library().values()
    ]


@app.get("/api/guides/{slug}")
def api_guide(slug: str):
    g = load_library().get(slug)
    if g is None:
        raise HTTPException(404, f"No guide for '{slug}' yet")
    data = g.model_dump(mode="json")
    for s in data["scenarios"]:
        s["score"] = scenario_score(g.scenario(s["id"]))
        s["effort_basis"] = effort_basis(g)
    return JSONResponse(data)


# --- live generation ---------------------------------------------------------

def _recordings() -> list[str]:
    if not pipeline.RUNS_DIR.exists():
        return []
    return sorted(json.loads(p.read_text(encoding="utf-8")).get("query", p.stem) for p in pipeline.RUNS_DIR.glob("*.json"))


def _keys_ready() -> tuple[bool, str]:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    c, o = os.getenv("COMPOSIO_API_KEY", "").strip(), os.getenv("OPENROUTER_API_KEY", "").strip()
    if not c or not o:
        return False, "One or both are missing."
    if c.startswith("ck_"):
        return False, "COMPOSIO_API_KEY is a consumer key (ck_); use the project key that starts with ak_."
    return True, ""


@app.get("/generate", response_class=HTMLResponse)
def generate_page(request: Request, app_name: str = ""):
    ready, msg = _keys_ready()
    return templates.TemplateResponse(request, "generate.html", {
        "ready": ready, "setup_message": msg, "recordings": _recordings(), "prefill": app_name})


# A public demo spends the host's free API quota, so live runs are capped and
# run one at a time. Replays are free and never capped.
_live_lock = threading.Lock()
_live_starts: deque[float] = deque()


def _live_slot() -> Optional[str]:
    """Reserve a live run, or say why not."""
    cap = int(os.getenv("LIVE_RUNS_PER_HOUR", "6"))
    now = time.monotonic()
    while _live_starts and now - _live_starts[0] > 3600:
        _live_starts.popleft()
    if len(_live_starts) >= cap:
        return f"This server allows {cap} live runs an hour and they are used up. Replay a recorded run instead."
    if not _live_lock.acquire(blocking=False):
        return "Another live run is in progress. Try again in a minute, or replay a recorded run."
    _live_starts.append(now)
    return None


def _events(query: str, replay: bool):
    def send(d: dict) -> str:
        return f"data: {json.dumps(d)}\n\n"

    query = query.strip()[:100]
    if not query:
        yield send({"type": "error", "message": "Enter an app name or website."})
        return
    if not replay:
        ready, _ = _keys_ready()
        if not ready:
            yield send({"type": "error", "message": "Live research is off on this server because no API keys are set. "
                                                    "Replay one of the recorded runs instead."})
            return
        why_not = _live_slot()
        if why_not:
            yield send({"type": "error", "message": why_not})
            return
        try:
            yield from _run(query, replay, send)
        finally:
            _live_lock.release()
        return
    yield from _run(query, replay, send)


def _run(query: str, replay: bool, send):
    try:
        if replay:
            path = pipeline.RUNS_DIR / f"{slugify(pipeline.resolve(query).app)}.json"
            if not path.exists():
                yield send({"type": "error", "message": f"No recording for {query}."})
                return
            record = json.loads(path.read_text(encoding="utf-8"))
            sources, model, support = pipeline.replay_parts(record)
            query = record["query"]
        else:
            from .generate import live_parts

            sources, model, support = live_parts()
    except Exception as e:
        yield send({"type": "error", "message": f"Setup failed: {e}"})
        return

    today = pipeline.run_date(record) if replay else None
    for ev in pipeline.run(query, sources, model, support, today=today, replayed=replay):
        if ev.type == "done":
            pipeline.save(ev.guide, None if replay else ev.run)
        yield send(ev.public())
        if ev.type in ("done", "error"):
            return


@app.get("/generate/stream")
def generate_stream(app: str, replay: int = 0):
    return StreamingResponse(_events(app, bool(replay)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
