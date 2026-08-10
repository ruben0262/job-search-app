"""What each source is actually asked for, built from the active users' configs.

The failure this guards is a source that looks enabled and searches for the wrong thing. An
adapter carrying its own default keyword list answers every call, logs no error and fills the
corpus — with somebody else's demand.
"""
from __future__ import annotations

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


def test_a_rotated_source_is_asked_for_a_window_its_sweep_can_cover():
    """The defect that made Adzuna return nothing on its first live day.

    Adzuna is metered by the vendor, so the cycle asks a slice of the (country x keyword) grid and
    advances it. With 243 pairs at two a cycle the grid takes about thirty hours to sweep — and it
    was being asked for jobs posted in the last **six**. Every query wanted postings from a window
    that opened and closed while we were looking somewhere else, so three cycles produced one row.

    A source whose demand is rotated must be asked for at least as long a window as one full sweep
    takes, or the gap is silent and permanent. Nothing errors: the board answers, correctly, that
    it has nothing matching.
    """
    from libs.settings import (
        DEFAULT_SOURCE_FRESHNESS_HOURS,
        PLATFORM_TICK_MIN,
        queries_per_cycle_default,
    )

    # The live union on 2026-08-10. Two accounts is already this many; it does not shrink.
    pairs = 243
    per_cycle = queries_per_cycle_default("adzuna")
    cycles_per_sweep = -(-pairs // per_cycle)
    sweep_hours = cycles_per_sweep * PLATFORM_TICK_MIN / 60

    assert DEFAULT_SOURCE_FRESHNESS_HOURS["adzuna"] >= sweep_hours, (
        f"a sweep takes {sweep_hours:.0f}h and the window is "
        f"{DEFAULT_SOURCE_FRESHNESS_HOURS['adzuna']}h — the difference is invisible loss")


def test_the_delivery_window_does_not_discard_what_the_harvest_just_paid_for():
    """Two windows, one pipeline: fetching further back than we will deliver is spend for nothing.

    The floor is not the window. `libs.tenant.filters._fresh_enough` takes
    `max(floor, job_max_age_hours)`, so a floor of 1 means "an hour at least, or the operator's
    window if that is wider" — it exists to stop a tight global setting switching a source off,
    not to cap it. LinkedIn is the case that makes the difference concrete: harvested at four
    hours with a floor of one, and delivered on twenty-four, because the global window is wider
    than the floor.

    What must hold is that the *effective* window covers the harvest, and that is what this
    asserts, against the shipped global setting.
    """
    from libs.settings import (
        DEFAULT_JOB_MAX_AGE_HOURS,
        DEFAULT_SOURCE_FRESHNESS_HOURS,
        SOURCE_MIN_AGE_HOURS,
    )

    for source, harvested in DEFAULT_SOURCE_FRESHNESS_HOURS.items():
        floor = SOURCE_MIN_AGE_HOURS.get(source)
        if floor is None:
            continue
        effective = max(floor, DEFAULT_JOB_MAX_AGE_HOURS)
        assert effective >= harvested, (
            f"{source}: harvested at {harvested}h but deliverable for only {effective}h — the "
            "cycle pays to fetch rows the pre-score filter then drops")


def test_the_call_budget_has_one_definition():
    """A ceiling the panel shows and an adapter reads must be the same number, not two equal ones."""
    from libs.settings import queries_per_cycle_default
    from services.harvester.sources import http_apis

    assert not hasattr(http_apis, "DEFAULT_ADZUNA_QUERIES"), (
        "the adapter restated the ceiling; a second copy drifts silently")
    assert queries_per_cycle_default("adzuna") == 2
