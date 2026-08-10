

# ── Adzuna: a general board that was searching only for software ──────────────────────────────

def test_adzuna_searches_for_what_the_users_actually_want():
    """The adapter had its own keyword list, so the credential alone would have changed nothing.

    `adzuna` is in none of the keyword-passing sets, so `cfg["keywords"]` was never set and the
    adapter fell back to `["python developer", "backend engineer"]` in `gb`. It was added to widen
    a corpus that was 97% software, and it would have searched for software.
    """
    import uuid

    from services.harvester.source_config import build_source_configs

    configs = {uuid.uuid4(): {"sections": [
        {"keywords": ["mechanical design engineer"], "locations": ["london"]}]}}

    cfg = build_source_configs(
        configs, env={"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"})["adzuna"]

    assert "mechanical design engineer" in cfg["keywords"]
    assert "python developer" not in cfg["keywords"]


def test_adzuna_asks_the_countries_the_users_named():
    """One place turns a location into countries, and Adzuna serves a subset of it."""
    import uuid

    from services.harvester.source_config import build_source_configs

    def countries(*places):
        configs = {uuid.uuid4(): {"sections": [{"keywords": ["engineer"],
                                                "locations": list(places)}]}}
        return build_source_configs(
            configs, env={"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"})["adzuna"]["countries"]

    assert countries("london") == ["gb"]
    assert "pl" in countries("warsaw")
    # Adzuna has no Hungarian edition, so a Budapest-only account gets nothing from it rather
    # than a 404 per cycle.
    assert countries("budapest") == []


def test_adzuna_is_not_asked_for_remote_work_on_everybody_s_behalf():
    """`where="remote"` was a search preference held by an adapter, and it removed every on-site
    role before the workplace filter or the user's own config could have an opinion."""
    import uuid

    from services.harvester.source_config import build_source_configs

    configs = {uuid.uuid4(): {"sections": [{"keywords": ["engineer"], "locations": ["london"]}]}}
    cfg = build_source_configs(
        configs, env={"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"})["adzuna"]

    assert cfg.get("where") in (None, ""), "the harvester must not pre-filter on workplace"
