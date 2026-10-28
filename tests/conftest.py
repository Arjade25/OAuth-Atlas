import copy
from datetime import date

import pytest

TODAY = date(2026, 10, 28)

BASE = {
    "app": "Example",
    "provider": "Example Inc",
    "slug": "example",
    "summary": "A minimal valid guide used by the tests.",
    "scenarios": [
        {
            "id": "testing",
            "label": "Testing",
            "audience": "Your own account",
            "state": "available_now",
            "dimensions": {"technical": 1, "prerequisites": 0, "approval": 0, "uncertainty": 0, "maintenance": 0},
            "effort_hours": {"min": 0.25, "max": 0.5},
            "wait_days": {"min": 0, "max": 0},
            "claims": ["no-review"],
        },
        {
            "id": "public",
            "label": "Public",
            "audience": "Any user",
            "state": "production_blocked",
            "dimensions": {"technical": 2, "prerequisites": 3, "approval": 5, "uncertainty": 4, "maintenance": 4},
            "effort_hours": {"min": 3, "max": 8},
            "wait_days": {"min": 14, "max": 60},
            "blocked_by": ["review-needed"],
            "claims": ["review-wait"],
        },
    ],
    "claims": [
        {
            "id": "no-review",
            "text": "Apps for your own account need no review.",
            "kind": "requirement",
            "applies_to": ["testing"],
            "evidence": {"url": "https://example.com/docs", "quote": "No review is required for your own account.",
                         "retrieved_at": "2026-10-28", "source_type": "official"},
            "checks": {"exists": "pass", "supports": "pass", "policy": "pass"},
            "status": "verified",
        },
        {
            "id": "review-needed",
            "text": "Public apps need a security review.",
            "kind": "requirement",
            "applies_to": ["public"],
            "evidence": {"url": "https://example.com/review", "quote": "Public apps must pass a security review.",
                         "retrieved_at": "2026-10-28", "source_type": "official"},
            "checks": {"exists": "pass", "supports": "pass", "policy": "pass"},
            "status": "verified",
        },
        {
            "id": "review-wait",
            "text": "Review takes between 2 and 8 weeks.",
            "kind": "wait_time",
            "applies_to": ["public"],
            "wait_days": {"min": 14, "max": 60},
            "evidence": {"url": "https://example.com/review", "quote": "Review takes 2 to 8 weeks.",
                         "retrieved_at": "2026-10-28", "source_type": "official"},
            "checks": {"exists": "pass", "supports": "pass", "policy": "pass"},
            "status": "verified",
        },
    ],
    "steps": [
        {"n": 1, "title": "Create the app", "owner": "you", "detail": "In the console.", "claims": ["no-review"]},
        {"n": 2, "title": "Submit for review", "owner": "you", "detail": "Fill the form.", "scenarios": ["public"],
         "claims": ["review-needed"]},
    ],
    "blockers": [],
    "origin": "curated",
    "review_status": "approved",
    "last_verified": "2026-10-28",
}


@pytest.fixture
def raw():
    return copy.deepcopy(BASE)
