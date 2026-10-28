"""Difficulty scoring.

The score belongs to a scenario (an app plus an audience and distribution
path), not to an app. Rule: the score is the highest band any dimension
reaches, floored at 1. Simple on purpose, so anyone can reproduce it.
"""

from __future__ import annotations

from .schema import Dimensions, Scenario

LABELS = {
    1: "Self-serve",
    2: "Light review",
    3: "Reviewed",
    4: "Gated",
    5: "Audited",
}

DIMENSION_LABELS = {
    "technical": "Technical effort",
    "prerequisites": "Prerequisite burden",
    "approval": "Approval burden",
    "uncertainty": "External uncertainty",
    "maintenance": "Maintenance burden",
}


def score(d: Dimensions) -> int:
    return max(1, d.technical, d.prerequisites, d.approval, d.uncertainty, d.maintenance)


def driver(d: Dimensions) -> str:
    """Name of the dimension that sets the score (first one, on ties)."""
    values = d.model_dump()
    top = max(values.values())
    return next(k for k, v in values.items() if v == top)


def label(n: int) -> str:
    return LABELS[n]


def scenario_score(s: Scenario) -> int:
    return score(s.dimensions)
