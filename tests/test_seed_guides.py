"""The curated library must always pass every policy check."""

from atlas.library import load_all, validate_all
from atlas.schema import ClaimStatus, ScenarioState
from atlas.scoring import scenario_score


def test_seed_guides_have_no_policy_errors():
    for slug, issues in validate_all().items():
        assert [i for i in issues if i.is_error] == [], slug


def test_seed_library_spans_the_difficulty_range():
    scores = {scenario_score(s) for g in load_all().values() for s in g.scenarios}
    assert 1 in scores and 5 in scores


def test_every_seed_claim_has_evidence():
    for g in load_all().values():
        for c in g.claims:
            assert c.evidence is not None, f"{g.slug}:{c.id}"
            assert c.status == ClaimStatus.verified, f"{g.slug}:{c.id}"


def test_every_seed_guide_covers_more_than_one_state():
    for g in load_all().values():
        assert len({s.state for s in g.scenarios}) >= 2, g.slug
        assert any(s.state == ScenarioState.production_blocked for s in g.scenarios), g.slug
