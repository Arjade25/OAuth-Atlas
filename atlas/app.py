"""Web app: the guide library and guide pages.

Run:  uvicorn atlas.app:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .library import load_all
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
        return "Not published" if not wait_claims else "Not published as a number"
    if s.wait_days.max == 0:
        return "None"
    r = s.wait_days
    return f"{r.min:g} days" if r.exact else f"{r.min:g} to {r.max:g} days"


def scenario_view(s: Scenario, g: Guide) -> dict:
    n = scenario_score(s)
    return {
        "s": s,
        "score": n,
        "score_label": label(n),
        "driver": DIMENSION_LABELS[driver(s.dimensions)],
        "state_text": STATE_TEXT[s.state.value],
        "hours": fmt_hours(s.effort_hours),
        "wait": fmt_wait(s, g),
        "dimensions": [(DIMENSION_LABELS[k], v) for k, v in s.dimensions.model_dump().items()],
        "blocked_by": [g.claim(c) for c in s.blocked_by if g.claim(c)],
    }


def guide_view(g: Guide) -> dict:
    numbering = {c.id: i + 1 for i, c in enumerate(g.claims)}
    issues = check_guide(g)
    return {
        "g": g,
        "scenarios": [scenario_view(s, g) for s in g.scenarios],
        "numbering": numbering,
        "issues": issues,
        "warnings": {i.ref for i in issues if not i.is_error and i.ref},
        "owner_text": OWNER_TEXT,
        "max_score": max(scenario_score(s) for s in g.scenarios),
        "min_score": min(scenario_score(s) for s in g.scenarios),
    }


@app.get("/", response_class=HTMLResponse)
def library(request: Request):
    guides = [guide_view(g) for g in load_all().values()]
    guides.sort(key=lambda v: (v["min_score"], v["g"].app))
    return templates.TemplateResponse(request, "library.html", {"guides": guides})


@app.get("/guides/{slug}", response_class=HTMLResponse)
def guide_page(request: Request, slug: str):
    g = load_all().get(slug)
    if g is None:
        raise HTTPException(404, f"No guide for '{slug}' yet")
    return templates.TemplateResponse(request, "guide.html", guide_view(g))


@app.get("/api/guides")
def api_list():
    return [
        {"slug": g.slug, "app": g.app, "scenarios": [
            {"id": s.id, "state": s.state.value, "score": scenario_score(s)} for s in g.scenarios]}
        for g in load_all().values()
    ]


@app.get("/api/guides/{slug}")
def api_guide(slug: str):
    g = load_all().get(slug)
    if g is None:
        raise HTTPException(404, f"No guide for '{slug}' yet")
    data = g.model_dump(mode="json")
    for s in data["scenarios"]:
        s["score"] = scenario_score(g.scenario(s["id"]))
    return JSONResponse(data)
