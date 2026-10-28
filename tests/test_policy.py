from datetime import date

from atlas.policy import check_guide, errors
from atlas.schema import Guide

from conftest import TODAY


def codes(raw, today=TODAY):
    return {i.code for i in check_guide(Guide.model_validate(raw), today)}


def error_codes(raw, today=TODAY):
    return {i.code for i in errors(check_guide(Guide.model_validate(raw), today))}


def test_base_guide_has_no_errors(raw):
    assert error_codes(raw) == set()


def test_unknown_claim_reference(raw):
    raw["scenarios"][1]["blocked_by"] = ["does-not-exist"]
    assert "bad_reference" in error_codes(raw)


def test_unknown_scenario_reference(raw):
    raw["claims"][0]["applies_to"] = ["ghost"]
    assert "bad_reference" in error_codes(raw)


def test_duplicate_claim_id(raw):
    raw["claims"].append(dict(raw["claims"][0]))
    assert "duplicate_id" in error_codes(raw)


def test_steps_must_be_ascending(raw):
    raw["steps"][1]["n"] = 1
    assert "step_order" in error_codes(raw)


def test_blocked_scenario_must_name_its_blocker(raw):
    raw["scenarios"][1]["blocked_by"] = []
    assert "blocked_without_reason" in error_codes(raw)


def test_blocker_on_unblocked_scenario(raw):
    raw["scenarios"][0]["blocked_by"] = ["review-needed"]
    assert "reason_without_block" in error_codes(raw)


def test_over_precise_wait_is_caught(raw):
    """Source says 'several weeks'; claim says 21 days. Must not pass."""
    c = raw["claims"][2]
    c["text"] = "Review takes exactly 21 days."
    c["wait_days"] = {"min": 21, "max": 21}
    c["evidence"]["quote"] = "Verification can take several weeks."
    assert "over_precise_wait" in error_codes(raw)


def test_vague_wait_without_numbers_is_fine(raw):
    c = raw["claims"][2]
    c["text"] = "Review can take several weeks."
    c["wait_days"] = None
    c["evidence"]["quote"] = "Verification can take several weeks."
    raw["scenarios"][1]["wait_days"] = None
    assert error_codes(raw) == set()


def test_number_in_claim_must_be_in_quote(raw):
    raw["claims"][0]["text"] = "Apps for your own account need no review for 30 days."
    assert "number_not_in_quote" in error_codes(raw)


def test_numbers_match_despite_thousands_separator(raw):
    raw["claims"][0]["text"] = "Up to 1,000 installs need no review."
    raw["claims"][0]["evidence"]["quote"] = "No review is required below 1000 installs."
    assert "number_not_in_quote" not in error_codes(raw)


def test_wait_on_scenario_needs_a_wait_claim(raw):
    raw["scenarios"][1]["claims"] = []
    assert "unsourced_wait" in error_codes(raw)


def test_available_now_cannot_rest_on_unverified_claim(raw):
    c = raw["claims"][0]
    c["status"] = "unverified"
    c["checks"] = {"exists": "pass", "supports": "not_run", "policy": "pass"}
    raw["review_status"] = "pending"
    assert "available_on_unverified" in error_codes(raw)


def test_verified_requires_every_check(raw):
    raw["claims"][0]["checks"]["supports"] = "unclear"
    assert "verified_without_checks" in error_codes(raw)


def test_failed_check_must_be_flagged(raw):
    raw["claims"][1]["checks"]["exists"] = "fail"
    raw["claims"][1]["status"] = "unverified"
    assert "failed_check_not_flagged" in error_codes(raw)


def test_verified_without_evidence(raw):
    raw["claims"][0]["evidence"] = None
    assert "verified_without_evidence" in error_codes(raw)


def test_approved_guide_cannot_have_flagged_claims(raw):
    raw["claims"][1]["status"] = "flagged"
    raw["claims"][1]["checks"]["supports"] = "fail"
    assert "approved_with_flags" in error_codes(raw)


def test_conflicting_claims(raw):
    raw["claims"][0].update(topic="review-required", value="no")
    raw["claims"][1].update(topic="review-required", value="yes")
    assert "conflicting_claims" in error_codes(raw)


def test_third_party_is_a_warning_not_an_error(raw):
    raw["claims"][1]["evidence"]["source_type"] = "third_party"
    assert "third_party_source" in codes(raw)
    assert "third_party_source" not in error_codes(raw)


def test_stale_evidence_depends_on_score(raw):
    later = date(2027, 1, 15)  # 79 days on
    found = {i.ref for i in check_guide(Guide.model_validate(raw), later) if i.code == "stale_evidence"}
    assert "review-needed" in found      # score 5: 30-day window
    assert "no-review" not in found      # score 1: 90-day window


def test_future_dated_evidence(raw):
    raw["claims"][0]["evidence"]["retrieved_at"] = "2027-01-01"
    assert "future_evidence" in error_codes(raw)


def test_long_quote_warning(raw):
    raw["claims"][0]["evidence"]["quote"] = "word " * 50
    assert "quote_too_long" in codes(raw)


def test_required_claim_needs_an_obligation_in_its_quote(raw):
    """'Insert a link to your website' is an instruction, not a requirement."""
    c = raw["claims"][0]
    c["text"] = "A website URL is required."
    c["evidence"]["quote"] = "Insert a link to your app's website."
    assert "obligation_not_in_quote" in error_codes(raw)


def test_obligation_backed_by_its_quote_passes(raw):
    c = raw["claims"][0]
    c["text"] = "Public apps must pass a security review."
    c["evidence"]["quote"] = "Public apps must pass a security review."
    assert "obligation_not_in_quote" not in error_codes(raw)


def test_plain_claim_needs_no_obligation(raw):
    raw["claims"][0]["text"] = "You register the app in the console."
    assert "obligation_not_in_quote" not in error_codes(raw)


def test_requirement_kind_needs_an_obligation_in_its_quote(raw):
    """Copying an instruction and filing it as a requirement is still an overstatement."""
    c = raw["claims"][0]
    c["text"] = "Sign up for a developer sandbox account"
    c["evidence"]["quote"] = "Sign up for a developer sandbox account"
    c["kind"] = "requirement"
    assert "requirement_not_in_quote" in error_codes(raw)
    c["kind"] = "fact"
    assert "requirement_not_in_quote" not in error_codes(raw)


def test_claim_that_repeats_its_quote_is_a_warning(raw):
    c = raw["claims"][1]
    c["text"] = c["evidence"]["quote"]
    assert "claim_repeats_quote" in codes(raw) and "claim_repeats_quote" not in error_codes(raw)
