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


def test_effort_is_labelled_as_an_estimate():
    assert "author&#39;s estimate" in client.get("/guides/gmail").text
    data = client.get("/api/guides/gmail").json()
    assert all(s["effort_basis"] == "author's estimate" for s in data["scenarios"])


def test_a_deadline_is_not_shown_as_a_wait():
    """LinkedIn gives the approver 30 days; that is not how long you wait."""
    data = client.get("/api/guides/linkedin").json()
    s = next(x for x in data["scenarios"] if x["id"] == "page-super-admin-approval")
    assert s["wait_days"] is None
    assert s["effort_basis"].startswith("model's estimate")
    assert "No verified duration" in client.get("/guides/linkedin").text
