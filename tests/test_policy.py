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


def test_curly_apostrophe_counts_as_a_limit(raw):
    c = raw["claims"][0]
    c["text"] = "The installation scope cannot be changed afterward."
    c["evidence"]["quote"] = "Installation scope is set once, at creation time, and can’t be changed afterward."
    assert "obligation_not_in_quote" not in error_codes(raw)


def test_not_required_is_not_an_obligation(raw):
    c = raw["claims"][0]
    c["text"] = "Listing on the Marketplace is optional and not required to use the OAuth flow."
    c["evidence"]["quote"] = "Listing on the Marketplace is optional and helps your connection reach a wider audience"
    assert "obligation_not_in_quote" not in error_codes(raw)


def test_every_states_a_gate_for_a_requirement(raw):
    c = raw["claims"][1]
    c["text"] = "The Notion team reviews every Marketplace listing submission."
    c["evidence"]["quote"] = "The Notion team reviews every listing submission."
    assert "requirement_not_in_quote" not in error_codes(raw)


def test_evidence_one_day_ahead_is_a_time_zone_not_the_future(raw):
    raw["claims"][0]["evidence"]["retrieved_at"] = "2026-10-29"
    assert "future_evidence" not in error_codes(raw)          # TODAY is 2026-10-28
    raw["claims"][0]["evidence"]["retrieved_at"] = "2026-10-31"
    assert "future_evidence" in error_codes(raw)


def test_deadline_is_not_a_wait(raw):
    """'Has 30 days to verify' is the approver's deadline, not how long you wait."""
    c = raw["claims"][2]
    c["text"] = "The admin has up to 30 days to verify the app."
    c["wait_days"] = {"min": 0, "max": 30}
    c["evidence"]["quote"] = "Your admin has 30 days to verify the app with the unique URL."
    assert "deadline_as_wait" in error_codes(raw)


def test_expiry_is_not_a_wait(raw):
    c = raw["claims"][2]
    c["text"] = "The approval link expires after 14 days."
    c["wait_days"] = {"min": 14, "max": 14}
    c["evidence"]["quote"] = "The approval link expires after 14 days."
    assert "deadline_as_wait" in error_codes(raw)


def test_hearing_back_within_a_range_is_a_wait(raw):
    c = raw["claims"][2]
    c["text"] = "Expect to hear back within 5-10 business days."
    c["wait_days"] = {"min": 5, "max": 10}
    c["evidence"]["quote"] = "After submission, expect to hear back from our team within 5-10 business days via email."
    assert "deadline_as_wait" not in error_codes(raw)


def test_a_step_you_take_is_not_a_blocker(raw):
    """Agreeing to terms or sending something for review is your action, not a gate."""
    raw["claims"][1]["text"] = "Sending the app for review requires agreeing to the partner terms."
    assert "blocker_not_a_gate" in error_codes(raw)


def test_a_review_someone_else_decides_is_a_blocker(raw):
    for text in ("Public apps need a security review.",
                 "The Notion team reviews every listing submission.",
                 "A Page super admin must approve or deny the request.",
                 "Public apps must complete Google's restricted-scope verification."):
        raw["claims"][1]["text"] = text
        assert "blocker_not_a_gate" not in error_codes(raw), text


def test_no_wait_needs_a_claim_that_there_is_no_review(raw):
    """'None' is a claim too: a source that never mentions review does not back it."""
    raw["claims"][0]["text"] = "Apps for your own account are created in the console."
    raw["claims"][0]["evidence"]["quote"] = "Create the app in the console."
    raw["claims"][0]["kind"] = "fact"
    assert "unsourced_no_wait" in error_codes(raw)


def test_no_review_phrasings_back_a_zero_wait(raw):
    from atlas.policy import states_no_review
    from atlas.schema import Claim
    for text in ("Apps for your own account need no review.",
                 "you don't need to submit your app for a review",
                 "verification isn't required",
                 "Internal use is listed as an exception to restricted-scope verification.",
                 "Unlisted apps can be installed without review."):
        assert states_no_review(Claim(id="c", text=text)), text
    for text in ("Create the app in the console.", "Marketplace apps are reviewed by Acme."):
        assert not states_no_review(Claim(id="c", text=text)), text


def test_prerequisite_is_an_obligation_word(raw):
    """A list of setup steps does not make the first one a prerequisite."""
    c = raw["claims"][1]
    c["text"] = "A developer team is a prerequisite for creating an app."
    c["evidence"]["quote"] = "Create a developer team, then create your app, then install it."
    c["kind"] = "fact"
    assert "obligation_not_in_quote" in error_codes(raw)


def test_not_possible_states_a_limit(raw):
    c = raw["claims"][1]
    c["text"] = "A LAN app cannot register webhooks."
    c["evidence"]["quote"] = "It's not possible to register webhooks with a non public URL."
    c["kind"] = "limit"
    assert "obligation_not_in_quote" not in error_codes(raw)
