"""Sidebar contract checks without starting the web application."""


def test_sidebar_only_requests_retained_slices(tmp_path, monkeypatch):
    from hermes_cli.web_routers import profiles

    home = tmp_path / "profile"
    home.mkdir()
    (home / "state.db").touch()
    monkeypatch.setattr(profiles, "_profile_targets", lambda _route: [("work", home)])
    monkeypatch.setattr(profiles, "_corrupt_profile_stores", lambda _scanned: {})
    monkeypatch.setattr(profiles, "subagent_listing_scope", lambda *_args, **_kwargs: (False, None))
    calls = []

    class DB:
        def list_sessions_rich(self, **kwargs):
            calls.append(kwargs)
            return []

        def usage_totals(self):
            return {"tokens": 0, "cost_usd": 0}

    monkeypatch.setattr(profiles, "_read_profile_db", lambda _name, _home, _errors, read: read(DB()))
    profiles._sidebar_profile_cache_clear()
    monkeypatch.setattr(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 0)
    result = profiles.get_profiles_sessions_sidebar(recents_profile="work")

    assert set(result) == {"recents", "cron", "errors", "storage"}
    assert len(calls) == 2
    assert [call["source"] for call in calls] == [None, "cron"]
    assert result["recents"]["sessions"] == []
    assert result["cron"]["sessions"] == []
