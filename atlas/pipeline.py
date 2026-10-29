"""Research to checked guide, end to end.

    resolve -> search -> fetch -> select passages -> extract draft
      -> check every claim (exists, policy, supports) -> assemble -> policy review

`run()` yields progress events and finishes with a 'done' event holding the
Guide and a run record. The run record holds the searches, the passages the
model saw, its raw replies and the support verdicts: enough to replay the
whole thing with no keys (see replay_parts).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from pydantic import HttpUrl, TypeAdapter, ValidationError

from .evidence import normalize, quote_in_page
from .extract import Draft, extract, slugify
from .llm import ChatModel, ReplayModel
from .passages import Passage, select
from .policy import check_guide, claim_issues, is_gate, is_listing_path, states_no_review
from .schema import (Blocker, CheckResult, Checks, Claim, ClaimKind, ClaimStatus, ComposioInfo, Dimensions,
                     Evidence, GenerationInfo, Guide, Origin, Owner, Range, ReviewStatus, Scenario, ScenarioState,
                     SourceType, Step)
from .sources import Page, ReplaySources, Sources, host
from .support import ReplaySupport, SupportChecker, judge_input

ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIR = ROOT / "generated"
RUNS_DIR = ROOT / "runs"

SKIP_HOSTS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com",
              "pinterest.com")
MAX_PAGES = 8
MAX_THIRD_PARTY = 3


@dataclass
class Target:
    app: str
    official_tokens: list[str]
    website_host: Optional[str] = None


def resolve(query: str) -> Target:
    """'Pipedrive' -> app Pipedrive. 'pipedrive.com' or a URL -> app from the domain."""
    q = query.strip()
    website = None
    if re.match(r"^(https?://)?[\w-]+(\.[\w-]+)+(/.*)?$", q) and " " not in q:
        website = host(q if q.startswith("http") else f"https://{q}")
        label = [p for p in website.split(".") if p not in ("www", "app", "developers", "developer", "docs", "api")]
        app = label[0].capitalize() if label else q
    else:
        app = q
    tokens = [t for t in re.findall(r"[a-z0-9]+", app.lower()) if len(t) >= 3]
    return Target(app=app, official_tokens=tokens, website_host=website)


def queries(app: str) -> list[str]:
    return [
        f"{app} OAuth app create client ID redirect URI developer documentation",
        f"{app} OAuth app review approval requirements",
        f"{app} app marketplace listing publish requirements review",
        f"{app} OAuth scopes permissions verification",
    ]


def is_official(url: str, t: Target) -> bool:
    h = host(url)
    if t.website_host and (h == t.website_host or h.endswith("." + t.website_host.removeprefix("www."))):
        return True
    return any(tok in h for tok in t.official_tokens)


def pick_urls(found: list[str], t: Target) -> list[str]:
    seen, official, other = set(), [], []
    for u in found:
        key = u.split("#")[0].rstrip("/")
        if key in seen or any(h in host(u) for h in SKIP_HOSTS) or u.lower().endswith(".pdf"):
            continue
        seen.add(key)
        (official if is_official(u, t) else other).append(u)
    return (official + other[:MAX_THIRD_PARTY])[:MAX_PAGES]


@dataclass
class Event:
    type: str  # step | detail | warn | error | done
    message: str
    guide: Optional[Guide] = None
    run: Optional[dict] = None

    def public(self) -> dict:
        d = {"type": self.type, "message": self.message}
        if self.guide:
            d["slug"] = self.guide.slug
        return d


@dataclass
class _Ctx:
    t: Target
    passages: list[Passage]
    fetched: dict[str, date]
    support: SupportChecker
    run_support: list[dict] = field(default_factory=list)


# --- assembly -----------------------------------------------------------------

_KINDS = {k.value for k in ClaimKind}
_STATES = {s.value for s in ScenarioState}
_OWNERS = {o.value for o in Owner}
_URL = TypeAdapter(HttpUrl)


def _range(r, default: Optional[Range] = None) -> Optional[Range]:
    if r is None:
        return default
    lo, hi = sorted((max(0.0, float(r.min)), max(0.0, float(r.max))))
    return Range(min=lo, max=hi)


def _url_ok(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    try:
        _URL.validate_python(u)
        return u
    except ValidationError:
        return None


def _locate(quote: str, named: Optional[Passage], passages: list[Passage]) -> Optional[Passage]:
    """The passage that contains the quote: the one the model named, else any."""
    if named and quote_in_page(quote, named.text) == CheckResult.passed:
        return named
    return next((p for p in passages if quote_in_page(quote, p.text) == CheckResult.passed), None)


def _check_claim(dc, cid: str, applies: list[str], ctx: _Ctx, today: date, notes: list[str]) -> Claim:
    by_pid = {p.pid: p for p in ctx.passages}
    named = by_pid.get((dc.passage or "").strip().strip("[]"))
    if named is None and dc.url:
        named = next((p for p in ctx.passages if p.url == dc.url), None)
    quote = (dc.quote or "").strip().strip('"').strip()
    kind = dc.kind if dc.kind in _KINDS else "fact"

    found = _locate(quote, named, ctx.passages) if quote else None
    checks = Checks()
    evidence = None
    if found:
        checks.exists = CheckResult.passed
        if named and found is not named:
            checks.notes.append(f"quote was found in {found.pid}, not the cited {named.pid}")
        evidence = Evidence(
            url=found.url, quote=quote, retrieved_at=ctx.fetched.get(found.url, today),
            source_type=SourceType.official if is_official(found.url, ctx.t) else SourceType.third_party)
    else:
        checks.exists = CheckResult.failed
        checks.notes.append("quote not found in any passage the model was given" if quote else "no quote given")
        src = named or (ctx.passages[0] if ctx.passages else None)
        if quote and src and _url_ok(src.url):
            evidence = Evidence(url=src.url, quote=quote, retrieved_at=ctx.fetched.get(src.url, today),
                                source_type=SourceType.official if is_official(src.url, ctx.t) else SourceType.third_party)

    claim = Claim(id=cid, text=dc.text.strip(), kind=ClaimKind(kind), applies_to=applies,
                  topic=dc.topic, value=dc.value, wait_days=_range(dc.wait_days) if kind == "wait_time" else None,
                  evidence=evidence, checks=checks)

    # Check 3 (claim-level rules) before spending a support call.
    errs = [i for i in claim_issues(claim, today) if i.is_error]
    checks.policy = CheckResult.failed if errs else CheckResult.passed
    checks.notes += [i.message for i in errs]

    # Check 2, only for claims whose quote is real.
    if found:
        official = is_official(found.url, ctx.t)
        source = f"{ctx.t.app}'s documentation" if official else f"a third-party page on {host(found.url)}"
        passage = judge_input(quote, found.text, source)
        v = ctx.support.check(claim.text, passage)
        checks.supports, checks.supports_p = v.result, v.p_supports
        if v.note:
            checks.notes.append(v.note)
        judge = ctx.support.name  # read after the call: a fallback may have answered
        ctx.run_support.append({"claim": claim.text, "passage": passage, "result": v.result.value,
                                "p_supports": v.p_supports, "note": v.note, "judge": judge})

    results = (checks.exists, checks.supports, checks.policy)
    if all(r == CheckResult.passed for r in results):
        claim.status = ClaimStatus.verified
    elif any(r in (CheckResult.failed, CheckResult.unclear) for r in results):
        claim.status = ClaimStatus.flagged
    else:
        claim.status = ClaimStatus.unverified
    return claim


def assemble(draft: Draft, ctx: _Ctx, query: str, today: date, notes: list[str]) -> Guide:
    # Stable, unique ids, and a map from whatever the model wrote to them.
    def uniq(raw_ids: list[str]) -> dict[str, str]:
        out, used = {}, set()
        for r in raw_ids:
            base = slugify(r)
            s, n = base, 2
            while s in used:
                s, n = f"{base}-{n}", n + 1
            used.add(s)
            out.setdefault(r, s)
        return out

    sid = uniq([s.id for s in draft.scenarios])
    cid = uniq([c.id for c in draft.claims])

    def keep(refs: list[str], table: dict[str, str], where: str) -> list[str]:
        out = []
        for r in refs:
            if r in table:
                out.append(table[r])
            else:
                notes.append(f"dropped unknown reference '{r}' in {where}")
        return list(dict.fromkeys(out))

    claims = [
        _check_claim(dc, cid[dc.id], keep(dc.applies_to, sid, f"claim {dc.id}"), ctx, today, notes)
        for dc in draft.claims
    ]
    by_id = {c.id: c for c in claims}

    scenarios = []
    for ds in draft.scenarios:
        blocked = keep(ds.blocked_by, cid, f"scenario {ds.id}")
        cited = keep(ds.claims, cid, f"scenario {ds.id}")
        # Only a requirement can block a path; a fact or limit is context.
        not_req = [c for c in blocked if by_id[c].kind != ClaimKind.requirement]
        # ...but only while a stated requirement remains to explain the block.
        if not_req and len(not_req) < len(blocked):
            notes.append(f"'{', '.join(not_req)}' on '{ds.id}' is not a requirement, so it no longer counts as a blocker")
            blocked = [c for c in blocked if c not in not_req]
            cited = list(dict.fromkeys(cited + not_req))
        # A step you take ("agree to the terms") is not a blocker; a review or
        # approval someone else decides is. Drop the former while one remains.
        not_gate = [c for c in blocked if not is_gate(by_id[c])]
        if not_gate and len(not_gate) < len(blocked):
            notes.append(f"'{', '.join(not_gate)}' on '{ds.id}' is a step you take, not a review or approval, "
                         "so it no longer counts as a blocker")
            blocked = [c for c in blocked if c not in not_gate]
            cited = list(dict.fromkeys(cited + not_gate))
        state = ds.state if ds.state in _STATES else "production_blocked"
        if blocked and state != "production_blocked":
            notes.append(f"scenario '{ds.id}' names blockers, so it is marked blocked")
            state = "production_blocked"

        # A listing path is open only if a source says it is not reviewed.
        if (state == "available_now" and is_listing_path(ds.label, sid[ds.id])
                and not any(states_no_review(by_id[c]) for c in cited)):
            notes.append(f"'{ds.id}' lists the app but no claim says it needs no review, so it is not confirmed open")
            state = "unconfirmed"

        # A wait time survives only if a verified wait claim with a number backs it.
        wait = _range(ds.wait_days)
        if wait is not None and wait.max > 0:
            backed = [by_id[c] for c in cited + blocked
                      if by_id[c].kind == ClaimKind.wait_time and by_id[c].wait_days is not None
                      and by_id[c].status == ClaimStatus.verified]
            if not backed:
                notes.append(f"removed the wait time on '{ds.id}': no verified claim with a number backs it")
                wait = None
        elif wait is not None and not any(states_no_review(by_id[c]) for c in cited):
            notes.append(f"removed 'no wait' on '{ds.id}': no claim says there is no review, so the wait is unknown")
            wait = None

        scenarios.append(Scenario(
            id=sid[ds.id], label=ds.label, audience=ds.audience or ds.label, state=ScenarioState(state),
            dimensions=Dimensions(**ds.dimensions.model_dump()),
            effort_hours=_range(ds.effort_hours, Range(min=0.5, max=2)), wait_days=wait,
            blocked_by=blocked, claims=cited))

    steps = []
    for i, st in enumerate(draft.steps, 1):
        steps.append(Step(
            n=i, title=st.title, owner=Owner(st.owner if st.owner in _OWNERS else "you"), detail=st.detail,
            scenarios=keep(st.scenarios, sid, f"step {i}"), minutes=st.minutes if (st.minutes or 0) >= 0 else None,
            claims=keep(st.claims, cid, f"step {i}"),
            agent_prompt=st.agent_prompt if st.owner == "agent" else None))

    blockers = [Blocker(symptom=b.symptom, fix=b.fix, escalate=b.escalate, claims=keep(b.claims, cid, "a blocker"))
                for b in draft.blockers]

    app = draft.app or ctx.t.app
    return Guide(
        app=app, provider=draft.provider or app, slug=slugify(ctx.t.app), summary=draft.summary or "",
        developer_portal=_url_ok(draft.developer_portal), scenarios=scenarios, claims=claims, steps=steps,
        blockers=blockers,
        composio=ComposioInfo(notes="Check the toolkit page for managed auth. To bring your own app, register the "
                                    "redirect URI shown when you create a custom auth config."),
        origin=Origin.generated, review_status=ReviewStatus.pending, last_verified=today)


# --- the run --------------------------------------------------------------------

def run(query: str, sources: Sources, model: ChatModel, support: SupportChecker,
        today: Optional[date] = None, replayed: bool = False) -> Iterator[Event]:
    today = today or date.today()
    t = resolve(query)
    record: dict = {"version": 1, "query": query, "app": t.app,
                    "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "searches": [], "pages": [], "replies": []}
    notes: list[str] = []

    try:
        # 1. Search
        found: list[str] = []
        for q in queries(t.app):
            yield Event("step", f"Searching: {q}")
            try:
                urls = sources.search(q)
            except Exception as e:
                yield Event("warn", f"Search failed, continuing: {e}")
                urls = []
            record["searches"].append({"query": q, "urls": urls})
            found += urls
        chosen = pick_urls(found, t)
        if not chosen:
            yield Event("error", f"No usable sources found for {t.app}. Try the app's website, like {slugify(t.app)}.com.")
            return
        n_off = sum(is_official(u, t) for u in chosen)
        yield Event("detail", f"Reading {len(chosen)} pages ({n_off} from {t.app}'s own sites)")

        # 2. Fetch
        pages: list[Page] = []
        for i in range(0, len(chosen), 4):
            try:
                pages += sources.fetch(chosen[i:i + 4])
            except Exception as e:
                yield Event("warn", f"Could not read some pages: {e}")
        if not pages:
            yield Event("error", "Every page fetch failed, so there is nothing to quote from.")
            return

        # 3. Passages
        passages = select(pages, t.app)
        if not passages:
            yield Event("error", "The pages had no passages about OAuth setup or review.")
            return
        fetched = {p.url: p.fetched_at for p in pages}
        by_url: dict[str, list[str]] = {}
        for p in passages:
            by_url.setdefault(p.url, []).append(p.text)
        record["pages"] = [{"url": p.url, "fetched_at": p.fetched_at.isoformat(), "passages": by_url[p.url]}
                           for p in pages if p.url in by_url]
        yield Event("detail", f"Kept {len(passages)} relevant passages from {len(by_url)} pages")

        # 4. Extract
        yield Event("step", f"Drafting the guide with {model.name}")
        draft = extract(model, t.app, passages, record["replies"])
        record["model"] = model.name
        yield Event("detail", f"Draft has {len(draft.scenarios)} paths and {len(draft.claims)} claims")

        # 5. Check and assemble
        yield Event("step", f"Checking each claim against its source (support judge: {support.name})")
        ctx = _Ctx(t=t, passages=passages, fetched=fetched, support=support)
        guide = assemble(draft, ctx, query, today, notes)
        judges = list(dict.fromkeys(r.get("judge", support.name) for r in ctx.run_support)) or [support.name]
        record["support_backend"] = ", ".join(judges)
        record["support"] = ctx.run_support
        if len(judges) > 1:
            notes.append(f"claims were judged by more than one model ({', '.join(judges)}) after a fallback")

        verified = sum(c.status == ClaimStatus.verified for c in guide.claims)
        flagged = sum(c.status == ClaimStatus.flagged for c in guide.claims)
        yield Event("detail", f"{verified} of {len(guide.claims)} claims verified, {flagged} flagged")
        for n in notes:
            yield Event("warn", n)

        guide.generation = GenerationInfo(
            run_at=datetime.fromisoformat(record["run_at"]), query=query, extraction_model=model.name,
            support_backend=record["support_backend"], searches=[s["query"] for s in record["searches"]],
            pages=[p["url"] for p in record["pages"]], replayed=replayed, notes=notes)

        errs = [i for i in check_guide(guide, today) if i.is_error]
        if errs:
            yield Event("warn", f"Policy review found {len(errs)} problems; they are listed on the guide")
        yield Event("done", f"Guide ready: {guide.app}", guide=guide, run=record)
    except Exception as e:
        yield Event("error", f"Generation stopped: {e}")


def save(guide: Guide, record: Optional[dict]) -> Path:
    GENERATED_DIR.mkdir(exist_ok=True)
    path = GENERATED_DIR / f"{guide.slug}.json"
    path.write_text(guide.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    if record is not None:
        RUNS_DIR.mkdir(exist_ok=True)
        (RUNS_DIR / f"{guide.slug}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_date(record: dict) -> date:
    """The day a recorded run fetched its pages, so a replay judges evidence as of then."""
    days = [date.fromisoformat(p["fetched_at"]) for p in record.get("pages", [])]
    return max(days) if days else date.today()


def replay_parts(record: dict):
    """Sources, model and support judge that reproduce a recorded run offline."""
    return (ReplaySources(record), ReplayModel(record.get("replies", []), name=record.get("model", "replay")),
            ReplaySupport(record.get("support", []), name=record.get("support_backend", "replay")))
