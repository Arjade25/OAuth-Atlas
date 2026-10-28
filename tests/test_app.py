from fastapi.testclient import TestClient

from atlas.app import app

client = TestClient(app)


def test_library_lists_every_seed():
    r = client.get("/")
    assert r.status_code == 200
    for name in ("GitHub", "Slack", "Gmail"):
        assert name in r.text


def test_guide_page_renders_scenarios_and_evidence():
    r = client.get("/guides/gmail")
    assert r.status_code == 200
    assert "Blocked until approved" in r.text
    assert "Not published as a number" in r.text      # 'several weeks' stays unquantified
    assert "Reported by a third party" in r.text
    assert 'id="claim-casa-assessment"' in r.text


def test_unknown_guide_is_404():
    assert client.get("/guides/nope").status_code == 404


def test_api_includes_computed_scores():
    data = client.get("/api/guides/gmail").json()
    scores = {s["id"]: s["score"] for s in data["scenarios"]}
    assert scores["public-restricted"] == 5
    assert scores["testing"] == 2
