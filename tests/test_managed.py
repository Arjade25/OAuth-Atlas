"""The agent's one setup step for anyone: does Composio manage an OAuth app here?"""

from types import SimpleNamespace

from atlas import pipeline
from atlas.managed import composio_info
from atlas.sources import LiveSources

from test_pipeline import TODAY, FakeModel, FakeSources, FakeSupport


def test_managed_toolkit_means_no_app_needed_to_start():
    info = composio_info({"checked": True, "found": True, "toolkit": "gmail", "auth_modes": ["OAUTH2"],
                          "managed_schemes": ["OAUTH2"], "checked_on": "2026-10-29"}, "Gmail")
    assert info.managed_auth_available is True and info.managed_auth_schemes == ["OAUTH2"]
    assert "without you registering one" in info.notes and str(info.checked_on) == "2026-10-29"


def test_unmanaged_toolkit_means_bring_your_own():
    info = composio_info({"checked": True, "found": True, "toolkit": "acme", "auth_modes": ["OAUTH2"],
                          "managed_schemes": [], "checked_on": "2026-10-29"}, "Acme")
    assert info.managed_auth_available is False and "you need your own" in info.notes


def test_missing_toolkit_and_unchecked_stay_unknown():
    missing = composio_info({"checked": True, "found": False, "toolkit": "acme", "checked_on": "2026-10-29"}, "Acme")
    assert missing.managed_auth_available is None and missing.checked_on is not None
    unchecked = composio_info({"checked": False}, "Acme")
    assert unchecked.managed_auth_available is None and unchecked.checked_on is None


def test_live_lookup_reads_composio_and_tries_other_slugs():
    calls = []

    def get(slug):
        calls.append(slug)
        if slug == "google-calendar":
            raise RuntimeError("404 Toolkit not found")
        return SimpleNamespace(slug="googlecalendar", name="Google Calendar",
                               auth_config_details=[SimpleNamespace(mode="OAUTH2"), SimpleNamespace(mode="API_KEY")],
                               composio_managed_auth_schemes=["OAUTH2"])

    src = LiveSources.__new__(LiveSources)
    src.client = SimpleNamespace(toolkits=SimpleNamespace(get=get))
    r = src.managed_auth("google-calendar")
    assert calls == ["google-calendar", "googlecalendar"]
    assert r["found"] and r["toolkit"] == "googlecalendar" and r["auth_modes"] == ["OAUTH2", "API_KEY"]
    assert r["managed_schemes"] == ["OAUTH2"]


class ManagedSources(FakeSources):
    def managed_auth(self, toolkit):
        return {"checked": True, "found": True, "toolkit": toolkit, "auth_modes": ["OAUTH2"],
                "managed_schemes": [], "checked_on": TODAY.isoformat()}


def test_pipeline_checks_composio_first_and_replay_keeps_it():
    events = list(pipeline.run("Acme", ManagedSources(), FakeModel(), FakeSupport(), today=TODAY))
    assert events[0].message.startswith("Checking whether Composio manages")
    done = events[-1]
    assert done.guide.composio.managed_auth_available is False
    assert done.run["managed_auth"]["toolkit"] == "acme"

    replayed = list(pipeline.run("Acme", *pipeline.replay_parts(done.run), today=TODAY, replayed=True))[-1]
    assert replayed.guide.composio.managed_auth_available is False


def test_replay_of_an_older_recording_says_not_checked():
    done = list(pipeline.run("Acme", FakeSources(), FakeModel(), FakeSupport(), today=TODAY))[-1]
    record = dict(done.run)
    record.pop("managed_auth", None)
    g = list(pipeline.run("Acme", *pipeline.replay_parts(record), today=TODAY, replayed=True))[-1].guide
    assert g.composio.managed_auth_available is None and "Not checked yet" in g.composio.notes
