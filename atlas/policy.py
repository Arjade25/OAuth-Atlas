"""Check 3 of 3: deterministic policy and consistency rules.

These run on every guide, curated or generated, and need no network or model.
Errors block publishing; warnings are shown on the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .schema import CheckResult, Claim, ClaimKind, ClaimStatus, Guide, ReviewStatus, ScenarioState, SourceType
from .scoring import scenario_score

# How old evidence may be before a guide is stale, by scenario score.
# Harder paths change more often and cost more when wrong.
FRESHNESS_DAYS = {1: 90, 2: 90, 3: 60, 4: 30, 5: 30}
MAX_QUOTE_WORDS = 40


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "error" | "warning"
    message: str
    ref: Optional[str] = None  # claim or scenario id

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", text)) or bool(
        re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b", text, re.I)
    )


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {m.group().replace(",", "") for m in _NUM.finditer(text)}


# Words that turn a claim into an obligation or a hard limit, and the words a
# quote must contain to back one. An instruction ("Insert a link to...") is
# not evidence that something is required.
_STRONG_CLAIM = re.compile(r"\b(required|requires|requirement|mandatory|must|only|always|never|cannot)\b")
_STRONG_QUOTE = re.compile(
    r"\b(must|required|requires?|requirement|mandatory|need|needs|needed|only|always|never|cannot|can't|"
    r"not allowed|make sure|have to|has to|limited|at most|up to)\b")


def claim_issues(c: Claim, today: date) -> list[Issue]:
    """Rules that judge one claim against its own evidence. Used both by
    check_guide and by the pipeline to set a claim's policy check."""
    out: list[Issue] = []
    ev = c.evidence
    if ev is None:
        return out

    # Over-precision: a numeric wait needs a number in its source.
    if c.kind == ClaimKind.wait_time and c.wait_days is not None and not _has_number(ev.quote):
        out.append(Issue("over_precise_wait", "error",
                         f"Claim '{c.id}' states a numeric wait but its quote contains no number", c.id))

    # Every number the claim states must appear in its quote ("21 days" vs "several weeks").
    missing = _numbers(c.text) - _numbers(ev.quote)
    if missing:
        out.append(Issue("number_not_in_quote", "error",
                         f"Claim '{c.id}' states {', '.join(sorted(missing))}, which its quote does not contain", c.id))

    # Obligation: "X is required" needs a quote that says so, not just an instruction.
    strong = _STRONG_CLAIM.findall(c.text.lower())
    if strong and not _STRONG_QUOTE.search(ev.quote.lower()):
        out.append(Issue("obligation_not_in_quote", "error",
                         f"Claim '{c.id}' says '{strong[0]}', but its quote does not state an obligation or limit", c.id))

    if ev.source_type == SourceType.third_party:
        out.append(Issue("third_party_source", "warning",
                         f"Claim '{c.id}' rests on a third-party source; show it as reported, not fact", c.id))
    if len(ev.quote.split()) > MAX_QUOTE_WORDS:
        out.append(Issue("quote_too_long", "warning",
                         f"Claim '{c.id}' quote exceeds {MAX_QUOTE_WORDS} words; keep evidence to the passage", c.id))
    if ev.retrieved_at > today:
        out.append(Issue("future_evidence", "error", f"Claim '{c.id}' evidence is dated in the future", c.id))
    return out


def check_guide(g: Guide, today: Optional[date] = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    add = lambda code, sev, msg, ref=None: issues.append(Issue(code, sev, msg, ref))  # noqa: E731

    claim_ids = [c.id for c in g.claims]
    scenario_ids = [s.id for s in g.scenarios]
    for dup in {i for i in claim_ids if claim_ids.count(i) > 1}:
        add("duplicate_id", "error", f"Claim id '{dup}' is used more than once", dup)
    for dup in {i for i in scenario_ids if scenario_ids.count(i) > 1}:
        add("duplicate_id", "error", f"Scenario id '{dup}' is used more than once", dup)

    claims, scenarios = set(claim_ids), set(scenario_ids)

    # --- references -----------------------------------------------------------
    for c in g.claims:
        for sid in c.applies_to:
            if sid not in scenarios:
                add("bad_reference", "error", f"Claim '{c.id}' applies to unknown scenario '{sid}'", c.id)
    for s in g.scenarios:
        for cid in s.blocked_by + s.claims:
            if cid not in claims:
                add("bad_reference", "error", f"Scenario '{s.id}' references unknown claim '{cid}'", s.id)
    for st in g.steps:
        for cid in st.claims:
            if cid not in claims:
                add("bad_reference", "error", f"Step {st.n} references unknown claim '{cid}'", cid)
        for sid in st.scenarios:
            if sid not in scenarios:
                add("bad_reference", "error", f"Step {st.n} references unknown scenario '{sid}'", sid)
    for b in g.blockers:
        for cid in b.claims:
            if cid not in claims:
                add("bad_reference", "error", f"Blocker '{b.symptom[:40]}' references unknown claim '{cid}'", cid)
    numbers = [st.n for st in g.steps]
    if numbers != sorted(set(numbers)):
        add("step_order", "error", "Step numbers must be unique and ascending")

    # --- claims ---------------------------------------------------------------
    for c in g.claims:
        ev = c.evidence
        if ev is None and c.status == ClaimStatus.verified:
            add("verified_without_evidence", "error", f"Claim '{c.id}' is verified but has no evidence", c.id)
        if c.status == ClaimStatus.verified and not all(
            r == CheckResult.passed for r in (c.checks.exists, c.checks.supports, c.checks.policy)
        ):
            add("verified_without_checks", "error", f"Claim '{c.id}' is verified but not every check passed", c.id)
        if c.status != ClaimStatus.flagged and CheckResult.failed in (c.checks.exists, c.checks.supports, c.checks.policy):
            add("failed_check_not_flagged", "error", f"Claim '{c.id}' failed a check but is not flagged", c.id)

        issues.extend(claim_issues(c, today))

    # Conflicts: claims on the same topic must agree.
    by_topic: dict[str, set[str]] = {}
    for c in g.claims:
        if c.topic and c.value is not None:
            by_topic.setdefault(c.topic, set()).add(c.value)
    for topic, values in by_topic.items():
        if len(values) > 1:
            add("conflicting_claims", "error", f"Claims on '{topic}' disagree: {sorted(values)}", topic)

    # --- scenarios ------------------------------------------------------------
    by_id = {c.id: c for c in g.claims}
    for s in g.scenarios:
        if s.state == ScenarioState.production_blocked and not s.blocked_by:
            add("blocked_without_reason", "error", f"Scenario '{s.id}' is production_blocked but names no blocking claim", s.id)
        if s.state != ScenarioState.production_blocked and s.blocked_by:
            add("reason_without_block", "error", f"Scenario '{s.id}' lists blocking claims but is not production_blocked", s.id)
        if not s.claims and not s.blocked_by:
            add("unjustified_state", "warning", f"Scenario '{s.id}' state cites no claims", s.id)

        cited = [by_id[cid] for cid in s.claims + s.blocked_by if cid in by_id]
        if s.state == ScenarioState.available_now and any(c.status != ClaimStatus.verified for c in cited):
            add("available_on_unverified", "error",
                f"Scenario '{s.id}' is available_now but rests on a claim that is not verified", s.id)

        if s.wait_days is not None and s.wait_days.max > 0 and not any(c.kind == ClaimKind.wait_time for c in cited):
            add("unsourced_wait", "error", f"Scenario '{s.id}' gives a wait time but cites no wait_time claim", s.id)

        window = FRESHNESS_DAYS[scenario_score(s)]
        for c in cited:
            if c.evidence and (today - c.evidence.retrieved_at).days > window:
                add("stale_evidence", "warning",
                    f"Claim '{c.id}' is older than the {window}-day window for scenario '{s.id}'", c.id)

    if g.review_status == ReviewStatus.approved and any(c.status == ClaimStatus.flagged for c in g.claims):
        add("approved_with_flags", "error", "Guide is approved while it still has flagged claims")

    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.is_error]
