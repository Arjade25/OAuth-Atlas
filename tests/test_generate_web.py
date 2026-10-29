"""The Generate page and its event stream, using a recorded run (no keys)."""

import json

import pytest
from fastapi.testclient import TestClient

from atlas import app as webapp
from atlas import library, pipeline

from test_pipeline import TODAY, FakeModel, FakeSources, FakeSupport


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(library, "GENERATED_DIR", tmp_path / "generated")
    done = next(e for e in pipeline.run("Acme", FakeSources(), FakeModel(), FakeSupport(), today=TODAY)
                if e.type == "done")
    pipeline.RUNS_DIR.mkdir()
    (pipeline.RUNS_DIR / "acme.json").write_text(json.dumps(done.run), encoding="utf-8")
    return TestClient(webapp.app)


def events(client, url):
    body = client.get(url).text
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_generate_page_offers_recorded_runs(recorded):
    r = recorded.get("/generate")
    assert r.status_code == 200
    assert 'data-app="Acme"' in r.text


def test_replay_stream_ends_done_and_guide_is_published(recorded):
    evs = events(recorded, "/generate/stream?app=Acme&replay=1")
    assert evs[0]["type"] == "step"
    assert evs[-1]["type"] == "done" and evs[-1]["slug"] == "acme"
    assert any(e["type"] == "warn" for e in evs)       # the draft's corrections are reported

    page = recorded.get("/guides/acme")
    assert page.status_code == 200
    assert "What the checks found" in page.text
    assert "Flagged" in page.text
    assert "quote not found in any passage" in page.text

    lib = recorded.get("/")
    assert "Written by the agent, not yet reviewed" in lib.text


def test_unknown_recording_is_an_error_event(recorded):
    evs = events(recorded, "/generate/stream?app=Nothing&replay=1")
    assert evs == [{"type": "error", "message": "No recording for Nothing."}]


def test_empty_app_is_an_error_event(recorded):
    assert events(recorded, "/generate/stream?app=%20")[0]["type"] == "error"


def test_curated_guides_have_no_review_panel(recorded):
    assert "What the checks found" not in recorded.get("/guides/gmail").text


def test_live_run_without_keys_points_to_replay(recorded, monkeypatch):
    monkeypatch.setattr(webapp, "_keys_ready", lambda: (False, "One or both are missing."))
    evs = events(recorded, "/generate/stream?app=Acme")
    assert [e["type"] for e in evs] == ["error"] and "Replay" in evs[0]["message"]
    assert "disabled" in recorded.get("/generate").text


def test_live_runs_are_capped_per_hour(recorded, monkeypatch):
    monkeypatch.setattr(webapp, "_keys_ready", lambda: (True, ""))
    monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "0")
    monkeypatch.setattr(webapp, "_live_starts", webapp.deque())
    evs = events(recorded, "/generate/stream?app=Acme")
    assert [e["type"] for e in evs] == ["error"] and "live runs an hour" in evs[0]["message"]


def test_replay_is_never_capped(recorded, monkeypatch):
    monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "0")
    assert events(recorded, "/generate/stream?app=Acme&replay=1")[-1]["type"] == "done"


def test_unconfirmed_path_renders_with_its_explanation(recorded):
    g = library.load_library()["acme"] if "acme" in library.load_library() else None
    if g is None:
        events(recorded, "/generate/stream?app=Acme&replay=1")
        g = library.load_library()["acme"]
    g.scenarios[1].state = g.scenarios[1].state.__class__("unconfirmed")
    g.scenarios[1].blocked_by = []
    pipeline.save(g, None)
    r = recorded.get("/guides/acme")
    assert r.status_code == 200
    assert "Not confirmed open" in r.text and "never say whether" in r.text
