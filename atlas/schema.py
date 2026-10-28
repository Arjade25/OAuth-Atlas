"""Guide schema.

One schema for every guide, curated or agent-generated. Evidence lives on each
claim; scenarios, steps and blockers point at claims by id. Scores are computed
from a scenario's dimensions, never written by hand or by a model.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class SourceType(str, Enum):
    official = "official"          # the provider's own docs or changelog
    third_party = "third_party"    # vendors, forums, operator blogs


class CheckResult(str, Enum):
    passed = "pass"
    failed = "fail"
    unclear = "unclear"
    not_run = "not_run"


class ClaimStatus(str, Enum):
    verified = "verified"      # all three checks passed
    unverified = "unverified"  # checks not run, or no evidence
    flagged = "flagged"        # at least one check failed or was unclear


class ClaimKind(str, Enum):
    requirement = "requirement"
    wait_time = "wait_time"
    limit = "limit"
    fact = "fact"


class ScenarioState(str, Enum):
    available_now = "available_now"
    available_for_testing = "available_for_testing"
    production_blocked = "production_blocked"


class Owner(str, Enum):
    agent = "agent"
    you = "you"
    provider = "provider"


class ReviewStatus(str, Enum):
    approved = "approved"
    pending = "pending"


class Origin(str, Enum):
    curated = "curated"
    generated = "generated"


class Evidence(BaseModel):
    url: HttpUrl
    quote: str = Field(min_length=1, description="Exact passage from the page; kept short")
    retrieved_at: date
    source_type: SourceType = SourceType.official


class Checks(BaseModel):
    exists: CheckResult = CheckResult.not_run    # quote appears in the stored page text
    supports: CheckResult = CheckResult.not_run  # the passage entails the claim
    policy: CheckResult = CheckResult.not_run    # deterministic rules in atlas.policy
    supports_p: Optional[float] = Field(None, ge=0, le=1, description="Support checker's probability for 'supports'")
    notes: list[str] = Field(default_factory=list, description="Why a check did not pass")


class Range(BaseModel):
    """A min-max range. Wait times stay as precise as their source, no more."""
    min: float = Field(ge=0)
    max: float = Field(ge=0)

    @field_validator("max")
    @classmethod
    def _ordered(cls, v, info):
        lo = info.data.get("min")
        if lo is not None and v < lo:
            raise ValueError("max must be >= min")
        return v

    @property
    def exact(self) -> bool:
        return self.min == self.max


class Claim(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    text: str
    kind: ClaimKind = ClaimKind.fact
    applies_to: list[str] = Field(default_factory=list, description="Scenario ids")
    topic: Optional[str] = Field(None, description="Claims on one topic must agree on value")
    value: Optional[str] = None
    wait_days: Optional[Range] = None
    evidence: Optional[Evidence] = None
    checks: Checks = Field(default_factory=Checks)
    status: ClaimStatus = ClaimStatus.unverified


class Dimensions(BaseModel):
    """0 = none, 5 = severe. The score is derived from these."""
    technical: int = Field(ge=0, le=5)
    prerequisites: int = Field(ge=0, le=5)
    approval: int = Field(ge=0, le=5)
    uncertainty: int = Field(ge=0, le=5)
    maintenance: int = Field(ge=0, le=5)


class Scenario(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str
    audience: str
    state: ScenarioState
    dimensions: Dimensions
    effort_hours: Range
    wait_days: Optional[Range] = Field(None, description="None means not published")
    blocked_by: list[str] = Field(default_factory=list, description="Claim ids")
    claims: list[str] = Field(default_factory=list, description="Claims that justify the state")


class Step(BaseModel):
    n: int = Field(ge=1)
    title: str
    owner: Owner
    detail: str
    scenarios: list[str] = Field(default_factory=list)
    minutes: Optional[int] = Field(None, ge=0)
    claims: list[str] = Field(default_factory=list)
    agent_prompt: Optional[str] = None


class Blocker(BaseModel):
    symptom: str
    fix: str
    escalate: Optional[str] = None
    claims: list[str] = Field(default_factory=list)


class ComposioInfo(BaseModel):
    managed_auth_available: Optional[bool] = None
    callback_url: Optional[str] = None
    notes: Optional[str] = None


class GenerationInfo(BaseModel):
    """How an agent-generated guide was produced, so a reviewer can reproduce it."""
    run_at: datetime
    query: str
    extraction_model: str
    support_backend: str
    searches: list[str] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    replayed: bool = False
    notes: list[str] = Field(default_factory=list)


class Guide(BaseModel):
    app: str
    provider: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    summary: str
    developer_portal: Optional[HttpUrl] = None
    scenarios: list[Scenario] = Field(min_length=1)
    claims: list[Claim]
    steps: list[Step]
    blockers: list[Blocker] = Field(default_factory=list)
    composio: ComposioInfo = Field(default_factory=ComposioInfo)
    origin: Origin
    review_status: ReviewStatus
    last_verified: date
    generation: Optional[GenerationInfo] = None

    def claim(self, claim_id: str) -> Optional[Claim]:
        return next((c for c in self.claims if c.id == claim_id), None)

    def scenario(self, scenario_id: str) -> Optional[Scenario]:
        return next((s for s in self.scenarios if s.id == scenario_id), None)
