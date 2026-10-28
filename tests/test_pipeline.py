"""End-to-end pipeline tests with fake search, model and support judge. No network."""

import json
from datetime import date

import pytest

from atlas import pipeline
from atlas.evidence import normalize
from atlas.llm import ReplayModel, parse_json
from atlas.passages import chunk, select
from atlas.policy import check_guide
from atlas.schema import CheckResult, ClaimStatus, Guide, Origin, ReviewStatus
from atlas.sources import Page, urls_in
from atlas.support import Verdict, verdict_from_probs, window

TODAY = date(2026, 10, 28)

DOCS = """# Create an OAuth app

To build a public integration you register an OAuth app in the Acme Developer Hub. Each app gets a client ID and a client secret, and you can add up to 5 redirect URIs.

# Marketplace review

Apps listed in the Acme Marketplace must pass a security review before customers can install them. Review can take several weeks and the queue varies.

# Testing

Unlisted apps can be installed by up to 10 customer accounts without review. Use them for pilots."""

BLOG = """# My experience shipping on Acme

I found the Acme marketplace review took about six weeks for our app, mostly waiting on feedback from the review team about our OAuth scopes."""

PAGES = {
    "https://developers.acme.com/oauth": DOCS,
    "https://someblog.example/acme-review": BLOG,
}


class FakeSources:
    name = "fake"

    def search(self, query):
        return {"answer": "ignore this https://answer-only.example/never", "results": [
            {"url": "https://developers.acme.com/oauth"},
            {"url": "https://www.youtube.com/watch?v=1"},
            {"url": "https://someblog.example/acme-review"},
        ]}["results"] and ["https://developers.acme.com/oauth", "https://www.youtube.com/watch?v=1",
                           "https://someblog.example/acme-review"]

    def fetch(self, urls):
        return [Page(url=u, text=PAGES[u], fetched_at=TODAY) for u in urls if u in PAGES]


def reply_for(passages):
    pid = {p.url: p.pid for p in passages}
    docs = pid["https://developers.acme.com/oauth"]
    return json.dumps({
        "app": "Acme", "provider": "Acme Inc", "summary": "Unlisted works today; the Marketplace needs review.",
        "developer_portal": "https://developers.acme.com",
        "scenarios": [
            {"id": "Unlisted Pilots", "label": "Unlisted pilots", "audience": "Up to 10 customers",
             "state": "available_now",
             "dimensions": {"technical": 1, "prerequisites": 0, "approval": 0, "uncertainty": 0, "maintenance": 0},
             "effort_hours": {"min": 0.5, "max": 1}, "wait_days": {"min": 0, "max": 0},
             "claims": ["unlisted-cap", "register"]},
            {"id": "marketplace", "label": "Marketplace", "audience": "Any customer",
             "state": "available_now",  # contradicts blocked_by: pipeline must correct it
             "dimensions": {"technical": 2, "prerequisites": 2, "approval": 4, "uncertainty": 4, "maintenance": 1},
             "effort_hours": {"min": 4, "max": 10}, "wait_days": {"min": 21, "max": 21},  # invented
             "claims": ["review-weeks", "ghost-claim"], "blocked_by": ["security-review"]},
        ],
        "claims": [
            {"id": "register", "text": "You register an OAuth app in the Acme Developer Hub.", "kind": "fact",
             "applies_to": ["Unlisted Pilots", "marketplace"], "passage": docs,
             "quote": "you register an OAuth app in the Acme Developer Hub"},
            {"id": "unlisted-cap", "text": "Unlisted apps can be installed by up to 10 customer accounts without review.",
             "kind": "limit", "applies_to": ["Unlisted Pilots"], "passage": docs,
             "quote": "Unlisted apps can be installed by up to 10 customer accounts without review."},
            {"id": "security-review", "text": "Marketplace apps must pass a security review.", "kind": "requirement",
             "applies_to": ["marketplace"], "passage": docs,
             "quote": "must pass a security review before customers can install them"},
            # Over-precise: '21 days' from 'several weeks'.
            {"id": "review-weeks", "text": "Marketplace review takes 21 days.", "kind": "wait_time",
             "applies_to": ["marketplace"], "wait_days": {"min": 21, "max": 21}, "passage": docs,
             "quote": "Review can take several weeks and the queue varies."},
            # Fabricated quote.
            {"id": "redirect-limit", "text": "Each app can have up to 20 redirect URIs.", "kind": "limit",
             "applies_to": ["marketplace"], "passage": docs, "quote": "you can add up to 20 redirect URIs"},
            # Third-party, quote exists but cited passage is wrong.
            {"id": "six-weeks", "text": "One developer reported the review took about six weeks.", "kind": "fact",
             "applies_to": ["marketplace"], "passage": docs,
             "quote": "the Acme marketplace review took about six weeks"},
        ],
        "steps": [
            {"title": "Register the app", "owner": "you", "detail": "In the Developer Hub.", "claims": ["register"]},
            {"title": "Draft the review submission", "owner": "agent", "detail": "Scope justifications.",
             "scenarios": ["marketplace"], "agent_prompt": "Draft scope justifications."},
            {"title": "Wait for review", "owner": "provider", "detail": "Acme reviews it.", "scenarios": ["marketplace"]},
        ],
        "blockers": [{"symptom": "Install fails for an 11th customer", "fix": "List the app.", "claims": ["unlisted-cap"]}],
    })


class FakeModel:
    name = "fake-model"

    def __init__(self, bad_first=False):
        self.bad_first = bad_first
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if self.bad_first and self.calls == 1:
            return "Sure! Here is the guide, but I forgot the JSON."
        # Rebuild the passage list the model saw from the prompt text.
        text = messages[1]["content"]
        from atlas.passages import Passage
        import re
        ps = [Passage(pid=m.group(1), url=m.group(2), text="") for m in re.finditer(r"\[(P\d+)\] (\S+)", text)]
        return reply_for(ps)


class FakeSupport:
    name = "fake-judge"

    def check(self, claim, passage):
        if "reported" in claim:
            return verdict_from_probs({"supports": 0.55, "contradicts": 0.05, "unclear": 0.4})
        return verdict_from_probs({"supports": 0.92, "contradicts": 0.02, "unclear": 0.06})


def run_ok(**kw):
    events = list(pipeline.run("Acme", FakeSources(), kw.get("model", FakeModel()), FakeSupport(), today=TODAY))
    done = [e for e in events if e.type == "done"]
    assert done, [e.message for e in events]
    return done[0].guide, done[0].run, events


def claim(g, cid):
    return g.claim(cid)


def test_pipeline_produces_a_valid_generated_guide():
    g, _, _ = run_ok()
    assert isinstance(g, Guide)
    assert g.origin == Origin.generated and g.review_status == ReviewStatus.pending
    assert g.slug == "acme"
    assert g.generation.extraction_model == "fake-model"


def test_real_quote_with_support_is_verified():
    g, _, _ = run_ok()
    c = claim(g, "unlisted-cap")
    assert c.status == ClaimStatus.verified
    assert c.checks.supports_p == pytest.approx(0.92)
    assert str(c.evidence.url) == "https://developers.acme.com/oauth"


def test_fabricated_quote_is_flagged():
    c = claim(run_ok()[0], "redirect-limit")
    assert c.checks.exists == CheckResult.failed
    assert c.status == ClaimStatus.flagged


def test_over_precise_wait_is_flagged_and_removed_from_the_path():
    g, _, _ = run_ok()
    c = claim(g, "review-weeks")
    assert c.checks.policy == CheckResult.failed
    assert c.status == ClaimStatus.flagged
    assert g.scenario("marketplace").wait_days is None   # '21 days' never reaches the page


def test_third_party_quote_found_in_other_passage_and_low_support_is_unclear():
    c = claim(run_ok()[0], "six-weeks")
    assert c.checks.exists == CheckResult.passed
    assert str(c.evidence.url) == "https://someblog.example/acme-review"
    assert c.evidence.source_type.value == "third_party"
    assert c.checks.supports == CheckResult.unclear
    assert c.status == ClaimStatus.flagged


def test_blocked_by_forces_blocked_state_and_unknown_refs_are_dropped():
    g, _, events = run_ok()
    s = g.scenario("marketplace")
    assert s.state.value == "production_blocked"
    assert "ghost-claim" not in s.claims
    assert any("ghost-claim" in e.message for e in events if e.type == "warn")


def test_ids_are_slugified_and_references_follow():
    g, _, _ = run_ok()
    assert g.scenario("unlisted-pilots") is not None
    assert "unlisted-pilots" in claim(g, "unlisted-cap").applies_to


def test_agent_prompt_only_kept_on_agent_steps():
    g, _, _ = run_ok()
    assert [s.agent_prompt is not None for s in g.steps] == [False, True, False]


def test_assembled_guide_has_no_structural_errors():
    g, _, _ = run_ok()
    bad = {i.code for i in check_guide(g, TODAY) if i.is_error} - {"approved_with_flags"}
    assert "bad_reference" not in bad and "duplicate_id" not in bad and "unsourced_wait" not in bad


def test_bad_json_is_retried_once():
    model = FakeModel(bad_first=True)
    g, run, _ = run_ok(model=model)
    assert model.calls == 2 and len(run["replies"]) == 2


def test_search_answer_text_is_never_used_as_a_source():
    assert "https://answer-only.example/never" not in urls_in(
        {"answer": "see https://answer-only.example/never", "results": [{"url": "https://a.example/x"}]})


def test_video_sites_are_skipped():
    g, run, _ = run_ok()
    assert all("youtube" not in p["url"] for p in run["pages"])


def test_replay_reproduces_the_guide_without_live_parts():
    g, record, _ = run_ok()
    sources, model, support = pipeline.replay_parts(json.loads(json.dumps(record)))
    events = list(pipeline.run(record["query"], sources, model, support, today=TODAY, replayed=True))
    g2 = next(e.guide for e in events if e.type == "done")
    strip = lambda x: {k: v for k, v in x.model_dump(mode="json").items() if k != "generation"}  # noqa: E731
    assert strip(g2) == strip(g)
    assert g2.generation.replayed is True


def test_no_sources_is_an_explicit_error():
    class Empty(FakeSources):
        def search(self, q):
            return []
    events = list(pipeline.run("Acme", Empty(), FakeModel(), FakeSupport(), today=TODAY))
    assert events[-1].type == "error" and "No usable sources" in events[-1].message


def test_resolve_handles_websites():
    t = pipeline.resolve("https://www.pipedrive.com/en")
    assert t.app == "Pipedrive" and t.website_host == "www.pipedrive.com"
    assert pipeline.resolve("Google Calendar").official_tokens == ["google", "calendar"]


# --- smaller units ------------------------------------------------------------

def test_parse_json_handles_fences_and_reasoning():
    assert parse_json("<think>hmm</think>```json\n{\"a\": 1,}\n```") == {"a": 1}
    assert parse_json("Here you go: {\"a\": [1, 2]} hope that helps") == {"a": [1, 2]}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_passage_selection_prefers_review_text():
    pages = [Page(url="https://developers.acme.com/oauth", text=DOCS, fetched_at=TODAY)]
    chosen = select(pages, "Acme", total=1)
    assert "review" in chosen[0].text.lower()
    assert chosen[0].pid == "P1"


def test_chunking_keeps_small_paragraphs_together():
    text = "\n\n".join(["word " * 30] * 6)
    assert all(len(c.split()) <= 160 for c in chunk(text))


def test_support_thresholds():
    assert verdict_from_probs({"supports": 0.9, "contradicts": 0.05, "unclear": 0.05}, 0.75).result == CheckResult.passed
    assert verdict_from_probs({"supports": 0.6, "contradicts": 0.1, "unclear": 0.3}, 0.75).result == CheckResult.unclear
    assert verdict_from_probs({"supports": 0.2, "contradicts": 0.7, "unclear": 0.1}).result == CheckResult.failed


def test_support_window_centres_on_the_quote():
    w = window("security review", "a " * 200 + "must pass a security review before install " + "b " * 200, words=5)
    assert "security review" in w and len(w.split()) <= 13


def test_only_requirements_can_block_a_path():
    class BlocksWithAFact(FakeModel):
        def complete(self, messages):
            return super().complete(messages).replace('"blocked_by": ["security-review"]',
                                                      '"blocked_by": ["security-review", "register"]')

    g, _, events = run_ok(model=BlocksWithAFact())
    s = g.scenario("marketplace")
    assert s.blocked_by == ["security-review"] and "register" in s.claims
    assert any("not a requirement" in e.message for e in events if e.type == "warn")


def test_nli_label_mapping_follows_the_models_own_label_order():
    from atlas.support import nli_probs
    probs = nli_probs([0.0, 5.0, 0.0], {0: "contradiction", 1: "entailment", 2: "neutral"})
    assert probs["supports"] > 0.9 and abs(sum(probs.values()) - 1) < 1e-9
    probs = nli_probs([5.0, 0.0, 0.0], {0: "ENTAILMENT", 1: "NEUTRAL", 2: "CONTRADICTION"})
    assert max(probs, key=probs.get) == "supports"


def test_judge_eval_counts_false_passes_and_flags():
    import json
    from atlas.judge_eval import SET, score

    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    yes_man = type("Y", (), {"name": "always-yes",
                             "check": lambda self, c, p: verdict_from_probs({"supports": 1.0, "contradicts": 0, "unclear": 0})})()
    res = score(yes_man, items)
    assert res["false_flags"] == [] and len(res["false_passes"]) == 10   # every bad claim gets through


def test_judge_input_formats():
    from atlas.support import JUDGE_INPUTS, judge_input
    chunk = "Intro text. - Keep in mind that we allow only one callback URL per app 5. Click **Save** now."
    q = "we allow only one callback URL per app"
    assert judge_input(q, chunk, "Pipedrive's documentation", "sentence") == \
        "Keep in mind that we allow only one callback URL per app 5."
    assert judge_input(q, chunk, "Pipedrive's documentation", "sentence+source").startswith(
        "According to Pipedrive's documentation, Keep in mind")
    lower = judge_input(q, chunk, "x", "window-lower")
    assert lower == lower.lower() and "**" not in lower and "only one callback url" in lower
    assert set(JUDGE_INPUTS) >= {"window-lower", "window", "window+source", "sentence", "sentence+source"}


def test_compare_inputs_runs_every_format(capsys):
    import json
    from atlas.judge_eval import SET, compare
    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    yes = type("Y", (), {"name": "yes", "check": lambda s, c, p: verdict_from_probs(
        {"supports": 1.0, "contradicts": 0, "unclear": 0})})()
    assert compare(yes, items, "test") == 0
    out = capsys.readouterr().out
    assert "sentence+source" in out and "JUDGE_INPUT=" in out


def test_a_path_blocked_only_by_facts_keeps_them_as_blockers():
    class OnlyFactBlocks(FakeModel):
        def complete(self, messages):
            return super().complete(messages).replace('"blocked_by": ["security-review"]', '"blocked_by": ["register"]')

    g, _, _ = run_ok(model=OnlyFactBlocks())
    s = g.scenario("marketplace")
    assert s.state.value == "production_blocked" and s.blocked_by == ["register"]
