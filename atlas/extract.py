"""Turn selected passages into a draft guide.

The model proposes scenarios, claims, steps and blockers as JSON. It is told
to quote verbatim from the numbered passages; it is not trusted to have done
so. Everything it writes is checked afterwards in atlas.pipeline.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .llm import ChatModel, parse_json
from .passages import Passage


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


# --- Lenient draft models: accept what a model plausibly writes --------------

class DRange(BaseModel):
    min: float = 0
    max: float = 0


def _clamp(v: Any) -> int:
    try:
        return max(0, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


class DDims(BaseModel):
    technical: int = 0
    prerequisites: int = 0
    approval: int = 0
    uncertainty: int = 0
    maintenance: int = 0

    @field_validator("*", mode="before")
    @classmethod
    def _c(cls, v):
        return _clamp(v)


class DClaim(BaseModel):
    id: str
    text: str
    kind: str = "fact"
    applies_to: list[str] = Field(default_factory=list)
    topic: Optional[str] = None
    value: Optional[str] = None
    wait_days: Optional[DRange] = None
    passage: Optional[str] = None
    url: Optional[str] = None
    quote: Optional[str] = None


class DScenario(BaseModel):
    id: str
    label: str
    audience: str = ""
    state: str = "production_blocked"
    dimensions: DDims = Field(default_factory=DDims)
    effort_hours: DRange = Field(default_factory=lambda: DRange(min=0.5, max=2))
    wait_days: Optional[DRange] = None
    claims: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)


class DStep(BaseModel):
    title: str
    owner: str = "you"
    detail: str = ""
    scenarios: list[str] = Field(default_factory=list)
    minutes: Optional[int] = None
    claims: list[str] = Field(default_factory=list)
    agent_prompt: Optional[str] = None


class DBlocker(BaseModel):
    symptom: str
    fix: str
    escalate: Optional[str] = None
    claims: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    app: str
    provider: str = ""
    summary: str = ""
    developer_portal: Optional[str] = None
    scenarios: list[DScenario] = Field(min_length=1)
    claims: list[DClaim] = Field(default_factory=list)
    steps: list[DStep] = Field(default_factory=list)
    blockers: list[DBlocker] = Field(default_factory=list)


# --- Prompt -----------------------------------------------------------------

SYSTEM = """You write setup guides for developers who must create an OAuth app with a provider and get it approved.
You read numbered passages from web pages and return ONE JSON object, nothing else.

Hard rules:
1. Every claim must quote a short passage VERBATIM (5 to 35 words), copied character for character from ONE numbered passage. Give that passage id in "passage". Never paraphrase inside "quote".
2. The claim is a short, plain statement in your own words that a developer can act on ("Each app can have only one callback URL."). Do not copy the quote into the claim. A claim may only say what its quote says: never add numbers, durations, limits or conditions the quote does not contain.
2b. Use kind "requirement" only when the quote itself states an obligation (must, required, need to, a requirement). A step the docs tell you to do ("Sign up for an account") is a step, not a requirement; put it in "steps".
3. Wait times: if the passages give no number, do not invent one. Omit "wait_days" or set it to null. "Several weeks" stays "several weeks" in the text.
4. Prefer the provider's own documentation. If a claim comes from a third-party page, say "reported" in its text.
5. If the passages do not cover something, leave it out. Missing is better than invented.

Scenarios are the distinct paths a developer can take (examples: testing with your own account, internal to your organization, unlisted distribution to other customers, public listing or marketplace, access to sensitive or restricted scopes). Give 2 to 4.
- "state": "available_now" (usable today for that audience), "available_for_testing" (works only within testing limits), or "production_blocked" (a named requirement must be met first). A blocked scenario lists the claim ids that block it in "blocked_by". Mark a path that lists or publishes the app (a Marketplace, directory or store) "available_now" only if a claim you cite says it needs no review.
- "dimensions", each 0 to 5: technical (configuring credentials and scopes), prerequisites (domain, business documents, paid plan), approval (reviews, audits, partner approval), uncertainty (unpublished timelines, variable queues), maintenance (recurring assessments, ongoing compliance).
- "effort_hours": hands-on hours for the developer, as a min-max range. This is your estimate.
- "wait_days": only when a claim with a number backs it; otherwise null. Use {"min":0,"max":0} only when a claim you cite says there is no review or approval; if the passages never mention review, use null.
- "claims": the claim ids that justify the state.

Steps are in order. "owner" is "agent" (drafting text, justifications, checklists, configuration an AI agent can prepare), "you" (anything needing the developer's identity, money, legal standing or a click in a console) or "provider" (the provider's review). Agent steps include "agent_prompt", a ready instruction the developer can hand to their agent.

JSON shape:
{"app": str, "provider": str, "summary": "2 or 3 plain sentences: how hard, how long, what blocks",
 "developer_portal": url or null,
 "scenarios": [{"id": "kebab-case", "label": str, "audience": str, "state": str,
   "dimensions": {"technical": n, "prerequisites": n, "approval": n, "uncertainty": n, "maintenance": n},
   "effort_hours": {"min": n, "max": n}, "wait_days": {"min": n, "max": n} or null,
   "claims": [ids], "blocked_by": [ids]}],
 "claims": [{"id": "kebab-case", "text": str, "kind": "requirement|wait_time|limit|fact",
   "applies_to": [scenario ids], "wait_days": {"min": n, "max": n} or null,
   "passage": "P3", "quote": "verbatim words"}],
 "steps": [{"title": str, "owner": "agent|you|provider", "detail": str, "scenarios": [ids, empty means every path],
   "minutes": n or null, "claims": [ids], "agent_prompt": str or null}],
 "blockers": [{"symptom": str, "fix": str, "escalate": str or null, "claims": [ids]}]}"""


def build_messages(app: str, passages: list[Passage]) -> list[dict]:
    blocks = "\n\n".join(f"[{p.pid}] {p.url}\n{p.text}" for p in passages)
    user = (f"App: {app}\n\nWrite the guide from these passages only.\n\n{blocks}\n\n"
            "Return the JSON object now.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def extract(model: ChatModel, app: str, passages: list[Passage], replies: list[str]) -> Draft:
    """Ask once, and once more with the error if the reply is unusable.
    Every raw reply is appended to `replies` so a run can be replayed."""
    messages = build_messages(app, passages)
    last_error = ""
    for attempt in range(2):
        if attempt:
            messages = messages + [
                {"role": "assistant", "content": replies[-1]},
                {"role": "user", "content": f"That reply could not be used: {last_error}. "
                                            "Return only the corrected JSON object."},
            ]
        reply = model.complete(messages)
        replies.append(reply)
        try:
            return Draft.model_validate(parse_json(reply))
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            last_error = str(e)[:500]
    raise RuntimeError(f"The model did not return a usable guide: {last_error}")
