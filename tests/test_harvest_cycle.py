"""One harvest cycle, end to end, with fake sources."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import insert
from libs.db.models import CorpusJob, User
from libs.tenant.repository import TenantRepository
from services.harvester import cycle
from services.harvester.cycle import harvest_lock, run_cycle
from services.harvester.sources import JobLink


@pytest.fixture()
def ctx(db):
    return db


def _active_user(s, keywords=("python",), **fields) -> User:
    fields = {"status": "active", "active": True, **fields}
    u = insert(s, User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                       subscription_status="active", timezone="UTC", **fields))
    TenantRepository(s).save_config(u.id, {"keywords": list(keywords), "locations": ["london"]})
    return u


def _link(title="Backend Engineer", company=None, snippet="Go, gRPC. " * 30, **kw) -> JobLink:
    return JobLink(
        url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
        title=title, company=company or f"Acme{uuid.uuid4().hex[:8]}",
        location="London", source="greenhouse", snippet=snippet, **kw)


def _sources(**by_key):
    """Enable named sources; every other source stays disabled."""
    return {key: {"enabled": True, **cfg} for key, cfg in by_key.items()}


# ── the happy path ────────────────────────────────────────────────────────────────────────────

def test_a_cycle_ingests_and_dedups(ctx):
    """The whole loop: demand → sources → dedup → upsert. Nothing is embedded (ROADMAP Phase 8)."""
    s = ctx
    _active_user(s)
    found = [_link(), _link()]

    report = run_cycle(s, source_configs=_sources(himalayas={}),
                       run_source=lambda source, cfg, repo: list(found))

    assert report.links_found == 2
    assert report.new_jobs == 2
    assert s[CorpusJob.COLLECTION].distinct("url") == sorted(link.url for link in found)


def test_a_board_keeps_only_jobs_in_a_country_somebody_picked_and_any_it_cannot_place(ctx):
    """Nobody's search shows a job in a country nobody picked, so it is not stored (owner, 2026-11-04).
    One whose country cannot be told, "Remote", is kept, as the jobs page keeps it."""
    s = ctx
    _active_user(s)  # the United Kingdom
    found = [JobLink(url=f"https://boards.greenhouse.io/acme/jobs/{n}", title="Nurse", company=f"Acme {n}",
                     location=where, source="greenhouse")
             for n, where in enumerate(["London", "Berlin, Germany", "Austin, TX", "Remote"])]

    report = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: list(found),
                          describe=False)

    assert sorted(s[CorpusJob.COLLECTION].distinct("location")) == ["London", "Remote"]
    assert (report.new_jobs, report.elsewhere) == (2, 2)


def test_the_same_job_twice_is_ingested_once(ctx):
    """Two sources listing one posting is the normal case, not the exception."""
    s = ctx
    _active_user(s)
    link = _link()

    first = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [link])
    second = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [link])

    assert first.new_jobs == 1
    assert second.new_jobs == 0
    assert second.duplicates.get("url") == 1


def test_a_snippet_saves_the_description_fetch(ctx):
    """Sources that supply usable text save a whole fetch per job."""
    s = ctx
    _active_user(s)
    run_cycle(s, source_configs=_sources(himalayas={}),
              run_source=lambda *a: [_link(snippet="A real description. " * 30)])

    job = s[CorpusJob.COLLECTION].find_one({})
    assert job is not None
    assert job["description"] == "A real description. " * 30, "the snippet is stored as the text"


# ── who is in the fold ────────────────────────────────────────────────────────────────────────

def test_only_active_accounts_countries_keep_jobs(ctx):
    """An account an operator has stopped, or a signup not yet verified, widens nothing: a job in only their
    country is not stored (TENANT_SAFETY §5: a signup flood must cost nothing)."""
    s = ctx
    _active_user(s)  # the United Kingdom
    for status in ("suspended", "pending_verification"):
        stopped = _active_user(s, status=status, active=False)
        TenantRepository(s).save_config(stopped.id, {"keywords": ["x"], "locations": ["germany"]})
    found = [JobLink(url=f"https://boards.greenhouse.io/acme/jobs/{n}", title="Nurse", company=f"Acme {n}",
                     location=where, source="greenhouse")
             for n, where in enumerate(["London", "Berlin, Germany"])]

    report = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: list(found),
                       describe=False)

    assert s[CorpusJob.COLLECTION].distinct("location") == ["London"]
    assert report.elsewhere == 1


# ── failure containment ───────────────────────────────────────────────────────────────────────

def test_one_dead_source_cannot_sink_the_cycle(ctx):
    """Sources break upstream constantly."""
    s = ctx
    _active_user(s)
    good = _link()

    def flaky(source, cfg, repo):
        if source.key == "himalayas":
            raise RuntimeError("upstream changed its JSON again")
        return [good]

    report = run_cycle(s, source_configs=_sources(himalayas={}, nofluffjobs={}),
                       run_source=flaky)

    assert "himalayas" in report.source_failures
    assert report.new_jobs == 1, "the healthy source still delivered"


# ── the budget ────────────────────────────────────────────────────────────────────────────────

def _cycle_lasting(monkeypatch, seconds: float) -> None:
    """Make the cycle's own clock report `seconds` between its first and last read."""
    reads = iter([0.0])
    monkeypatch.setattr(cycle, "monotonic", lambda: next(reads, seconds))


def test_a_cycle_close_to_its_budget_says_so_while_there_is_still_margin(ctx, monkeypatch, caplog):
    """A cycle that overruns is killed with the stages behind it skipped, and nothing says why.

    The margin has to be reported while it still exists. 8.4 minutes of an 11-minute budget is the
    peak that was actually measured on 2026-08-10, and the only evidence it happened was a corpus
    that grew.
    """
    s = ctx
    _active_user(s)
    _cycle_lasting(monkeypatch, 8.4 * 60)

    with caplog.at_level("WARNING", logger="services.harvester.cycle"):
        run_cycle(s, source_configs=_sources(himalayas={}),
                  run_source=lambda source, cfg, repo: [_link()])

    warned = [r.getMessage() for r in caplog.records
              if r.levelname == "WARNING" and "budget" in r.getMessage()]
    assert warned, "a cycle at 76% of its budget passed without a word"
    assert "8.4" in warned[0], f"the warning has to carry how long it took: {warned[0]}"


def test_an_ordinary_cycle_is_not_warned_about(ctx, monkeypatch, caplog):
    """A warning that fires on the routine case is one an operator learns to skip past."""
    s = ctx
    _active_user(s)
    _cycle_lasting(monkeypatch, 30.7)

    with caplog.at_level("WARNING", logger="services.harvester.cycle"):
        run_cycle(s, source_configs=_sources(himalayas={}),
                  run_source=lambda source, cfg, repo: [_link()])

    assert not [r for r in caplog.records
                if r.levelname == "WARNING" and "budget" in r.getMessage()]


# ── overlap ───────────────────────────────────────────────────────────────────────────────────

def test_two_cycles_cannot_run_at_once(ctx):
    """A timer firing while the previous run is still going must SKIP, not queue."""
    s = ctx
    _active_user(s)

    with harvest_lock(s) as held:
        assert held, "the fixture failed to take the lease"
        report = run_cycle(s, source_configs={}, run_source=lambda *a: [])

    assert report.skipped_locked is True
    assert report.links_found == 0


def test_the_lock_is_released_after_a_cycle(ctx):
    """Otherwise the first run wedges every subsequent one and harvesting stops silently."""
    s = ctx
    run_cycle(s, source_configs={}, run_source=lambda *a: [])

    with harvest_lock(s) as held:
        assert held, "the lease was not released"


def test_a_lease_let_go_is_free_in_the_same_millisecond(ctx, monkeypatch):
    """Dates are kept to the millisecond. A run that started in the millisecond the last one let go
    found the lease still held, so this file failed one run in three."""
    import libs.db

    instant = libs.db.now()
    monkeypatch.setattr(libs.db, "now", lambda: instant)
    with harvest_lock(ctx) as held:
        assert held
    with harvest_lock(ctx) as held:
        assert held, "let go, and still refused"


def test_a_cycle_that_died_holding_the_lock_does_not_stop_the_next_one(ctx):
    """A killed cycle cannot let go, so its lease runs out instead — after one cycle's budget."""
    from libs.db import now
    from libs.settings import HARVEST_CYCLE_BUDGET_SECONDS

    s = ctx
    with harvest_lock(s) as held:
        assert held
        lease = s.locks.find_one({"_id": "harvest"})
        assert lease["until"] - now() > timedelta(seconds=HARVEST_CYCLE_BUDGET_SECONDS - 5), \
            "held for as long as a cycle may run, so a slow live cycle is never overtaken"
    # What a killed holder leaves behind: a lease that was never ended, now past its time.
    s.locks.update_one({"_id": "harvest"}, {"$set": {"until": now() - timedelta(seconds=1),
                                                    "holder": "a cycle that was killed"}})

    report = run_cycle(s, source_configs={}, run_source=lambda *a: [])

    assert report.skipped_locked is False


# ── de-personalised source config ─────────────────────────────────────────────────────────────

def test_the_filter_reads_the_sections_a_real_config_actually_stores():
    """Every test here wrote a flat config and every real one stores sections."""
    from services.harvester.source_config import union_locations

    sectioned = {
        uuid.uuid4(): {"sections": [{"keywords": ["Rust", "golang"],
                                     "locations": ["Budapest"], "platform": "all"}]},
    }
    assert union_locations(sectioned) == ["hungary"], "a city stored before is read as its country"

    both = {uuid.uuid4(): {"locations": ["germany"], "sections": [{"locations": ["france"]}]}}
    assert union_locations(both) == ["france", "germany"], "neither shape may shadow the other"


def test_a_job_a_board_returned_survives_every_enabled_source_s_own_filtering():
    """The hole the ATS incident went through, closed for every source at once."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources.ats import gather_ats

    class Boards:
        def live_tokens(self, _ats):
            return ["acme"]

        def etags(self, _ats):
            return {}

        def names(self, _ats):
            return {}

        def record_poll(self, *_args, **_kwargs):
            pass

    # A job the platform exists to catch and no keyword list would name.
    ordinary = JobLink(
        url="https://boards.greenhouse.io/acme/jobs/1",
        title="Vehicle Dynamics Specialist",
        company="Acme", location="Berlin, Germany", source="greenhouse",
        date_posted=(datetime.now(UTC) - timedelta(hours=2)).date().isoformat())

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [
            {"keywords": ["software engineer"], "locations": ["europe"]}]}},
        env={})

    # Only the sources that route through `JobFilter` — the ATS family.
    routed = [name for name in built if name in
              {"greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
               "breezy", "workday", "personio", "workable"}]
    assert routed, "the ATS family should be enabled; this test is empty without it"

    rejected = [name for name in routed if not gather_ats(
        built[name], logging.getLogger("test"), ats=name, repo=Boards(),
        fetch_company=lambda _token, _etag: ([ordinary], None))]
    assert not rejected, (
        f"{rejected} would drop a real posting before it ever reached the corpus — the shape of "
        "the incident that zeroed eleven sources")


def test_no_board_is_handed_anybodys_words_to_ask_for_or_to_keep_jobs_by():
    """Every board is read whole and the words matched on what is stored (owner, 2026-11-04). Hiring Cafe
    could only be asked for words, and was removed with the query set on 2026-11-06."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [
            {"keywords": ["software engineer"], "locations": ["europe"]}]}},
        env={"ADZUNA_APP_ID": "x", "FINDWORK_API_KEY": "x"})

    handed = {name for name, cfg in built.items() if "keywords" in cfg or "location_keywords" in cfg}
    assert handed == set(), f"{handed} would keep only the jobs somebody's words name"
    assert [name for name, cfg in built.items() if "queries" in cfg] == [], "nobody's words are asked for"


def test_a_source_whose_window_an_operator_narrowed_actually_narrows():
    """A setting that reaches nothing is worse than no setting, because it looks answered."""
    from services.harvester.source_config import build_source_configs

    default = build_source_configs({}, env={})
    narrowed = build_source_configs({}, env={}, freshness={"greenhouse": 3})

    assert narrowed["greenhouse"]["max_age_hours"] == 3
    # `posted_within_hours` is what `ats.JobFilter` prefers.
    assert narrowed["greenhouse"]["posted_within_hours"] == 3
    # One source only. Narrowing Greenhouse must not quietly narrow everything else.
    assert narrowed["ashby"]["max_age_hours"] == default["ashby"]["max_age_hours"]


def test_an_operators_window_reaches_a_source_under_every_key_it_reads():
    """Setting the window in three places was not enough, because a fourth exists."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={}, freshness={"himalayas": 48, "greenhouse": 3})

    assert built["himalayas"]["max_age_hours"] == 48
    # `ats.JobFilter` reads `posted_within_hours` first, and the platform default sat there.
    assert (built["greenhouse"]["max_age_hours"], built["greenhouse"]["posted_within_hours"]) == (3, 3)


def test_the_union_is_asked_for_not_used_to_throw_away_what_a_board_sent():
    """Six hours of zero ingestion from eleven sources."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [{"keywords": ["golang"], "locations": ["europe"]}]}},
        env={})

    for source in ("greenhouse", "ashby", "lever", "workday"):
        assert "keywords" not in built[source], f"{source} would drop rows on title alone"
        assert "location_keywords" not in built[source], f"{source} would drop rows on a slug"


def test_with_no_users_at_all_sources_stay_enabled_and_unfiltered():
    """Cold start."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={})
    assert built, "sources must still run"
    assert "keywords" not in built["greenhouse"], "unfiltered rather than filtered to nothing"


def test_key_gated_sources_stay_off_without_their_credential():
    """A source that 401s every cycle is a failure count that means nothing and drowns real ones."""
    from services.harvester.source_config import build_source_configs

    assert "adzuna" not in build_source_configs({}, env={})
    assert "adzuna" in build_source_configs(
        {}, env={"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"})


def test_the_recency_window_is_the_widest_any_user_asked_for(ctx):
    """Same rule as the query-set fold: fetch once at the coarsest demand, filter down locally."""
    from services.harvester.source_config import (
        DEFAULT_HARVEST_WINDOW_HOURS,
        build_source_configs,
    )

    s = ctx
    # No operator freshness passed, so the fallback ladder ends at the module default.
    assert build_source_configs({}, env={})["lever"]["max_age_hours"] == DEFAULT_HARVEST_WINDOW_HOURS

    a = _active_user(s)
    b = _active_user(s)
    t = TenantRepository(s)
    t.save_config(a.id, {"keywords": ["x"], "posted_within_hours": 24})
    t.save_config(b.id, {"keywords": ["y"], "posted_within_hours": 168})

    built = build_source_configs(t.active_configs(), env={})
    assert built["himalayas"]["max_age_hours"] == 168


# ── the age window actually reaching the sources ──────────────────────────────────────────────

def test_every_board_is_given_the_age_window_the_accounts_asked_for():
    """The bug that put month-old, already-closed postings into live feeds."""
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "user-1": {"sections": [{"keywords": ["python"], "posted_within_hours": 4}]}})

    assert configs, "no sources enabled — the assertion below would pass vacuously"
    for source, cfg in configs.items():
        # The number the user asked for, not merely a number.
        assert cfg["max_age_hours"] == 4, f"{source} ignored the window the user asked for"


def test_the_window_is_the_widest_any_active_account_asked_for():
    """The corpus is shared, so one fetch serves everyone and it has to be the most generous ask."""
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "tight": {"sections": [{"keywords": ["python"], "posted_within_hours": 2}]},
        "wide": {"sections": [{"keywords": ["python"], "posted_within_hours": 72}]}})

    assert all(cfg["max_age_hours"] == 72 for cfg in configs.values()), "the tighter ask won"


def test_an_account_may_ask_for_a_window_as_tight_as_the_reference_watcher():
    """The reference's frequent loop runs at two hours."""
    from libs.contracts.config import SearchSection

    section = SearchSection(keywords=["python"], locations=["germany"], posted_within_hours=2)
    assert section.posted_within_hours == 2


def test_the_operators_result_and_company_ceilings_reach_the_adapters():
    """Every adapter spells "how many" differently, and a value under the wrong key is silent."""
    from services.harvester.source_config import RESULT_KEYS, build_source_configs

    built = build_source_configs(
        {}, env={},
        results={"jobicy": 25, "justjoinit": 40},
        ats_companies=750)

    assert built["jobicy"]["count"] == 25
    assert built["justjoinit"]["per_page"] == 40
    # One company ceiling across the whole ATS family, since they share a mechanism.
    for name in ("greenhouse", "ashby", "workday"):
        assert built[name]["max_companies"] == 750

    # A source with no result key must not silently acquire one.
    assert "themuse" not in RESULT_KEYS
    assert "workingnomads" not in RESULT_KEYS


def test_an_unset_ceiling_leaves_the_platform_default_alone():
    """An operator who has tuned nothing must get exactly the behaviour the code ships with."""
    from services.harvester.source_config import PLATFORM_DEFAULTS, build_source_configs

    built = build_source_configs({}, env={})
    assert built["greenhouse"]["max_companies"] == PLATFORM_DEFAULTS["greenhouse"]["max_companies"]
    assert "per_page" not in built["justjoinit"], "no result key until somebody sets one"


def test_a_removed_source_cannot_be_quietly_switched_back_on():
    """`DEFERRED_SOURCES` records what was removed and why."""
    from libs.settings import HARVEST_SOURCES
    from services.harvester.source_config import DEFERRED_SOURCES, build_source_configs

    deferred = set(DEFERRED_SOURCES)
    assert deferred & set(build_source_configs({}, env={})) == set()
    assert deferred & set(HARVEST_SOURCES) == set(), "the admin panel would offer it a control"


def test_the_boards_a_run_reads_are_the_boards_admin_lists():
    """One list (`HARVEST_SOURCES`), which Admin's per-source controls read: the run reads every board on it
    but LinkedIn, a pass of its own. A second, hand-kept list in the harvester agreed with it only by care
    until 2026-11-06, and a board on one list and not the other got controls that changed nothing."""
    from libs.settings import HARVEST_SOURCES
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={"ADZUNA_APP_ID": "x", "FINDWORK_API_KEY": "x"})

    assert list(built) == [name for name in HARVEST_SOURCES if name != "linkedin"]


def test_a_board_taken_off_the_list_stops_even_holding_its_key(monkeypatch):
    """Taken off the one list, a board is not read: a board needing a key was read whenever its key was
    set, which kept Jooble running after its block until the key map was edited too (2026-08)."""
    from libs import settings
    from services.harvester.source_config import build_source_configs

    listed = tuple(name for name in settings.HARVEST_SOURCES if name not in ("himalayas", "adzuna"))
    monkeypatch.setattr(settings, "HARVEST_SOURCES", listed)

    built = build_source_configs({}, env={"ADZUNA_APP_ID": "x", "FINDWORK_API_KEY": "x"})

    assert {"himalayas", "adzuna"} & set(built) == set()
    assert "findwork" in built, "and the rest are read as before"


# ── how deep a paginated source walks (ROADMAP 6ag) ───────────────────────────────────────────


def test_the_operators_page_depth_reaches_the_two_sources_that_paginate():
    """Both were constants inside their adapters and neither appeared anywhere an operator looks."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={}, pages={"himalayas": 8, "workday": 2})

    assert built["himalayas"]["max_pages"] == 8
    assert built["workday"]["max_pages"] == 2


def test_a_source_with_no_page_setting_keeps_its_own_default():
    """An operator who has tuned one source must not silently reset every other one."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={}, pages={"himalayas": 8})

    assert "max_pages" not in built.get("workday", {})


def test_workday_walks_the_number_of_pages_it_was_given():
    """The setting has to reach the request, not just the config dict."""
    from services.harvester.sources import ats

    seen: list[int] = []

    # A full page of fresh postings, so the loop's own early exits — an empty page, a short page.
    page = {"jobPostings": [
        {"externalPath": f"/job/{n}", "title": f"Engineer {n}", "postedOn": "Posted Today",
         "locationsText": "Berlin"}
        for n in range(20)]}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return page

    class _Client:
        def itself(self):
            return self

        def post(self, _url, json=None, **_kw):
            seen.append(json["offset"])
            return _Response()

    client = _Client()
    original = ats._http
    ats._http = client.itself
    try:
        ats.fetch_workday("tenant:wd3:site", pages=2)
    finally:
        ats._http = original

    # Two pages at 20 a page, and no third request.
    assert seen == [0, 20]


# ── no embedder, and no proxy (ROADMAP Phase 8) ─────────────────────────────────────────────

def test_a_harvest_runs_without_an_embedder(ctx, monkeypatch):
    """Nothing embeds any more. A missing `EMBEDDER_URL` used to refuse the whole cycle with exit 2."""
    from services.harvester import __main__ as entry

    monkeypatch.delenv("EMBEDDER_URL", raising=False)
    monkeypatch.setattr(entry, "database", lambda: ctx)
    monkeypatch.setattr(entry, "ensure_schema", lambda *a: None)
    monkeypatch.setattr(entry, "_configure_logging", lambda verbose: None)
    monkeypatch.setattr(entry, "_run_linkedin_pass", lambda: None)
    ran = []
    monkeypatch.setattr(entry, "run_cycle", lambda db, **kwargs: (
        ran.append(kwargs) or cycle.CycleReport()))

    assert entry.main([]) == 0
    assert ran, "the cycle itself ran, not just the entry point"


def test_linkedin_is_read_beside_the_boards_and_the_words_found_after_both(ctx, monkeypatch):
    """LinkedIn goes out through the site proxy and the boards go direct: read one after the other, a
    long LinkedIn pass pushed the boards and the words past the tick (`TimeoutStartSec`)."""
    import threading

    from services.harvester import __main__ as entry

    monkeypatch.setattr(entry, "database", lambda: ctx)
    monkeypatch.setattr(entry, "ensure_schema", lambda *a: None)
    monkeypatch.setattr(entry, "_configure_logging", lambda verbose: None)
    order: list[str] = []
    linkedin_started, boards_done = threading.Event(), threading.Event()

    def linkedin_pass():
        linkedin_started.set()
        boards_done.wait(5)
        order.append("linkedin")

    def boards(db, **kwargs):
        assert linkedin_started.wait(5), "the boards waited for LinkedIn to finish"
        order.append("boards")
        boards_done.set()
        return cycle.CycleReport()

    monkeypatch.setattr(entry, "_run_linkedin_pass", linkedin_pass)
    monkeypatch.setattr(entry, "run_cycle", boards)
    monkeypatch.setattr(entry, "_find_words", lambda: order.append("words"))

    assert entry.main([]) == 0
    assert order == ["boards", "linkedin", "words"]


def test_with_no_site_proxy_every_board_but_linkedin_is_harvested(ctx, monkeypatch):
    """The site proxy is what LinkedIn needs and nothing else does: unset, LinkedIn is skipped and no
    other board loses anything."""
    from services.harvester import linkedin
    from services.harvester.sources import Tier, sources_for

    monkeypatch.delenv("HARVEST_PROXY", raising=False)
    s = ctx
    _active_user(s, keywords=["welder"])
    shared = {source.key for source in sources_for(Tier.SHARED)}
    asked: set[str] = set()

    def board(source, cfg, repo):
        asked.add(source.key)
        return [_link(title="Welder")] if source.key == "himalayas" else []

    report = run_cycle(s, source_configs=_sources(**{key: {} for key in shared}),
                       run_source=board, describe=False)

    assert asked == shared, "every shared board was asked"
    assert report.new_jobs == 1

    def no_request(*args, **kwargs):
        raise AssertionError("LinkedIn was fetched with no proxy to fetch it through")

    passed = linkedin.run_pass(s, sweep=no_request)
    assert (passed.swept_places, passed.swept) == (0, 0)
