import pytest
from pydantic import ValidationError

from atlas.schema import Guide
from atlas.scoring import driver, label, scenario_score


def test_base_guide_is_valid(raw):
    g = Guide.model_validate(raw)
    assert len(g.scenarios) == 2


@pytest.mark.parametrize("field", ["app", "slug", "scenarios", "claims", "steps", "origin", "review_status"])
def test_missing_required_field_is_rejected(raw, field):
    del raw[field]
    with pytest.raises(ValidationError):
        Guide.model_validate(raw)


def test_dimension_out_of_range_is_rejected(raw):
    raw["scenarios"][0]["dimensions"]["approval"] = 6
    with pytest.raises(ValidationError):
        Guide.model_validate(raw)


def test_inverted_range_is_rejected(raw):
    raw["scenarios"][1]["wait_days"] = {"min": 30, "max": 10}
    with pytest.raises(ValidationError):
        Guide.model_validate(raw)


def test_unknown_state_is_rejected(raw):
    raw["scenarios"][0]["state"] = "kind_of_works"
    with pytest.raises(ValidationError):
        Guide.model_validate(raw)


def test_bad_evidence_url_is_rejected(raw):
    raw["claims"][0]["evidence"]["url"] = "not a url"
    with pytest.raises(ValidationError):
        Guide.model_validate(raw)


def test_score_is_highest_dimension_and_floored_at_one(raw):
    g = Guide.model_validate(raw)
    assert scenario_score(g.scenario("testing")) == 1
    assert scenario_score(g.scenario("public")) == 5
    assert label(5) == "Audited"
    assert driver(g.scenario("public").dimensions) == "approval"

    raw["scenarios"][0]["dimensions"] = {k: 0 for k in raw["scenarios"][0]["dimensions"]}
    assert scenario_score(Guide.model_validate(raw).scenario("testing")) == 1


def test_same_app_scores_differently_by_scenario(raw):
    g = Guide.model_validate(raw)
    assert {scenario_score(s) for s in g.scenarios} == {1, 5}
