"""One harvest cycle, end to end, with fake sources."""
from __future__ import annotations

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
    u = insert(s, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC", **fields))
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

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}),
                          run_source=lambda source, cfg, repo: list(found))

    assert report.links_found == 2
    assert report.new_jobs == 2
    assert s[CorpusJob.COLLECTION].distinct("url") == sorted(link.url for link in found)


def test_the_same_job_twice_is_ingested_once(ctx):
    """Two sources listing one posting is the normal case, not the exception."""
    s = ctx
    _active_user(s)
    link = _link()

    first, _ = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [link])
    second, _ = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [link])

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


# ── the budget ────────────────────────────────────────────────────────────────────────────────

def test_the_fan_out_budget_is_respected(ctx):
    """Unbounded, 50 users × 5 terms × 3 sources is 750 queries a cycle — a ban and a bill."""
    s = ctx
    for i in range(10):
        _active_user(s, keywords=[f"u{i}term{j}" for j in range(5)])

    # A source config, because the query set is built for the sources that are enabled.
    report, deferred = run_cycle(s, source_configs=_sources(himalayas={}, nofluffjobs={}),
                                 limits={"himalayas": 7, "nofluffjobs": 7},
                                 run_source=lambda *a: [])
    # Per source, so two sources at seven each is fourteen -- not seven shared between them.
    assert report.queries_planned == 14
    assert report.queries_deferred > 0
    assert len(deferred) == report.queries_deferred


def test_deferred_queries_are_returned_for_the_next_cycle(ctx):
    """Advice for next time, not persisted state."""
    s = ctx
    for i in range(4):
        _active_user(s, keywords=[f"u{i}a", f"u{i}b"])

    asked = _sources(himalayas={}, nofluffjobs={})
    caps = {"himalayas": 3, "nofluffjobs": 3}
    _, deferred = run_cycle(s, source_configs=asked, limits=caps, run_source=lambda *a: [])
    assert deferred

    report2, _ = run_cycle(s, source_configs=asked, limits=caps,
                           carried_over=deferred, run_source=lambda *a: [])
    assert report2.queries_planned == 6


def test_one_sources_ceiling_does_not_starve_another(ctx):
    """The defect the global budget had, stated as behaviour."""
    s = ctx
    for i in range(20):
        _active_user(s, keywords=[f"u{i}term{j}" for j in range(3)])

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}, nofluffjobs={}),
                          limits={"himalayas": 2, "nofluffjobs": 5},
                          run_source=lambda *a: [])

    # Each source gets exactly its own ceiling, whatever the other one's demand is.
    assert report.queries_planned == 7


def test_a_source_the_operator_has_not_tuned_still_gets_a_ceiling(ctx):
    """Unbounded is not an option — 50 users × 5 terms is a ban and a cycle that takes an hour."""
    from services.harvester.query_set import DEFAULT_QUERIES_PER_CYCLE

    s = ctx
    for i in range(30):
        _active_user(s, keywords=[f"u{i}term{j}" for j in range(3)])

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}), limits={},
                          run_source=lambda *a: [])
    assert report.queries_planned == DEFAULT_QUERIES_PER_CYCLE
    assert report.queries_deferred > 0


# ── who is in the fold ────────────────────────────────────────────────────────────────────────

def test_a_switched_off_user_costs_nothing(ctx):
    """An account an operator has stopped must stop consuming harvest budget."""
    s = ctx
    _active_user(s, keywords=["activeterm"])
    _active_user(s, keywords=["stoppedterm"], status="suspended", active=False)
    asked: list[str] = []

    def board(source, cfg, repo):
        asked.append(str(cfg.get("queries")))
        return []

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}), run_source=board)
    assert report.queries_planned == 1
    assert "activeterm" in asked[0] and "stoppedterm" not in asked[0]


def test_an_unverified_signup_consumes_no_harvest_budget(ctx):
    """TENANT_SAFETY §5: a signup flood must cost nothing."""
    s = ctx
    _active_user(s, status="pending_verification", active=False)

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [])
    assert report.queries_planned == 0


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

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}, nofluffjobs={}),
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
        report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])

    assert report.skipped_locked is True
    assert report.queries_planned == 0


def test_the_lock_is_released_after_a_cycle(ctx):
    """Otherwise the first run wedges every subsequent one and harvesting stops silently."""
    s = ctx
    run_cycle(s, source_configs={}, run_source=lambda *a: [])

    with harvest_lock(s) as held:
        assert held, "the lease was not released"


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

    report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])

    assert report.skipped_locked is False


# ── de-personalised source config ─────────────────────────────────────────────────────────────

def test_source_filters_come_from_the_union_of_users_not_one_person(ctx):
    """The de-personalisation."""
    from services.harvester.source_config import build_source_configs, union_keywords

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust", "Golang"])

    configs = TenantRepository(s).active_configs()
    assert union_keywords(configs) == ["golang", "rust"], "deduplicated, lowercased, sorted"

    # Asserted on the queries, not on `keywords`.
    built = build_source_configs(configs, env={})
    assert built["himalayas"]["queries"] == [{"query": "golang"}, {"query": "rust"}]


def test_the_filter_reads_the_sections_a_real_config_actually_stores():
    """Every test here wrote a flat config and every real one stores sections."""
    from services.harvester.source_config import union_keywords, union_locations

    sectioned = {
        uuid.uuid4(): {"sections": [{"keywords": ["Rust", "golang"],
                                     "locations": ["Budapest"], "platform": "all"}]},
    }
    assert union_keywords(sectioned) == ["golang", "rust"]
    assert union_locations(sectioned) == ["hungary"], "a city stored before is read as its country"

    both = {uuid.uuid4(): {"keywords": ["flat"], "sections": [{"keywords": ["nested"]}]}}
    assert union_keywords(both) == ["flat", "nested"], "neither shape may shadow the other"


def test_each_board_is_asked_in_the_shape_it_reads():
    """A query in the wrong shape is silent: the board answers its unfiltered feed and reports success."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [{"keywords": ["golang"]}]}}, env={})

    assert built["himalayas"]["queries"] == [{"query": "golang"}]
    assert built["himalayas"]["queries"] == [{"query": "golang"}]
    assert built["nofluffjobs"]["queries"] == [{"keyword": "golang"}]
    assert built["hiringcafe"]["queries"] == [{"search_term": "golang"}]
    # Consider and Getro iterate strings, not dicts, and use each one as `searchText`.
    assert built["nofluffjobs"]["queries"] == [{"keyword": "golang"}]


def test_a_board_with_no_geography_is_asked_once_per_keyword():
    """This is the redundancy, stated exactly."""
    from services.harvester.query_set import build_query_set

    config = {uuid.uuid4(): {"sections": [
        {"keywords": ["golang"], "platform": "all",
         "locations": ["europe", "united_kingdom", "budapest"]}]}}

    # `himalayas` has no location parameter at all; `hiringcafe` has a real one.
    keys = build_query_set(config, sources=("himalayas", "hiringcafe"),
                           locating_sources=("hiringcafe",))
    per_source: dict[str, int] = {}
    for key in keys:
        per_source[key.source] = per_source.get(key.source, 0) + 1

    assert per_source["himalayas"] == 1, "one keyword, one query, however many places were asked for"
    assert per_source["hiringcafe"] == 3, "a source that can filter by place still gets all three"


def test_a_job_a_board_returned_survives_every_enabled_source_s_own_filtering():
    """The hole the ATS incident went through, closed for every source at once."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources.ats import JobFilter

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

    rejected = [name for name in routed if not JobFilter(built[name]).ok(ordinary)]
    assert not rejected, (
        f"{rejected} would drop a real posting before it ever reached the corpus — the shape of "
        "the incident that zeroed eleven sources")


def test_a_keyword_filtered_source_keeps_what_matches_and_judges_nothing_else():
    """The six sources that filter on keywords do it over the whole posting."""
    from services.harvester.source_config import (
        KEYWORD_FILTER_SOURCES,
        build_source_configs,
    )

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [
            {"keywords": ["software engineer"], "locations": ["europe"]}]}},
        env={})

    for name in KEYWORD_FILTER_SOURCES & set(built):
        cfg = built[name]
        assert cfg["keywords"] == ["software engineer"], f"{name} was asked for nothing"
        # The keyword is the only filter these carry.
        assert "location_keywords" not in cfg, f"{name} would also judge a posting on its location"


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

    built = build_source_configs({}, env={}, freshness={"hiringcafe": 48, "greenhouse": 3})

    assert built["hiringcafe"]["max_age_hours"] == 48
    assert built["hiringcafe"]["posted_within_days"] == 2, "48 hours is 2 days, rounded up"
    assert built["greenhouse"]["posted_within_hours"] == 3

    # Rounded up: a board that thinks in calendar days cannot honour "36 hours".
    day_and_a_half = build_source_configs({}, env={}, freshness={"hiringcafe": 36})
    assert day_and_a_half["hiringcafe"]["posted_within_days"] == 2
    # And never zero, which would be "nothing qualifies".
    assert build_source_configs(
        {}, env={}, freshness={"hiringcafe": 1})["hiringcafe"]["posted_within_days"] == 1


def test_the_union_is_asked_for_not_used_to_throw_away_what_a_board_sent():
    """Six hours of zero ingestion from eleven sources."""
    from services.harvester.source_config import PREFILTER_SOURCES, build_source_configs

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [{"keywords": ["golang"], "locations": ["europe"]}]}},
        env={})

    assert not PREFILTER_SOURCES, (
        "re-enabling one needs the before/after count of good matches lost that 6o.1 demands")
    for source in ("greenhouse", "ashby", "lever", "workday"):
        assert "keywords" not in built[source], f"{source} would drop rows on title alone"
        assert "location_keywords" not in built[source], f"{source} would drop rows on a slug"
    # And the half that is worth having is still there.
    assert built["himalayas"]["queries"] == [{"query": "golang"}]


def test_a_board_is_asked_for_the_phrase_the_user_wrote():
    """The fold key is not a search term, and sending it cost real quality on the first live cycle."""
    from services.harvester.query_set import normalise_keyword, original_terms

    configs = {uuid.uuid4(): {"sections": [
        {"keywords": ["stress engineer", "szoftverfejlesztő", "C++ developer"]}]}}
    terms = original_terms(configs)

    assert terms[normalise_keyword("stress engineer")] == "stress engineer"
    assert terms[normalise_keyword("szoftverfejlesztő")] == "szoftverfejlesztő"
    assert terms[normalise_keyword("C++ developer")] == "C++ developer"
    # The key itself still folds, or the whole point of the fold is lost.
    assert normalise_keyword("stress engineer") == normalise_keyword("Engineer, Stress")


def test_one_folded_key_emits_the_same_term_however_users_phrased_it():
    """Several users writing one search differently must not make the emitted term depend on which of."""
    from services.harvester.query_set import normalise_keyword, original_terms

    key = normalise_keyword("python developer")
    a = {uuid.uuid4(): {"sections": [{"keywords": ["python developer"]}]},
         uuid.uuid4(): {"sections": [{"keywords": ["Developer Python"]}]}}
    b = {uuid.uuid4(): {"sections": [{"keywords": ["Developer Python"]}]},
         uuid.uuid4(): {"sections": [{"keywords": ["python developer"]}]}}

    assert original_terms(a)[key] == original_terms(b)[key]


def test_no_locating_sources_is_not_the_same_as_not_saying():
    """An empty iterable means "nothing locates"; `None` means "everything does"."""
    from services.harvester.query_set import build_query_set

    config = {uuid.uuid4(): {"sections": [
        {"keywords": ["golang"], "platform": "all", "locations": ["london", "berlin"]}]}}

    assert len(build_query_set(config, sources=("linkedin",))) == 2
    assert len(build_query_set(config, sources=("linkedin",), locating_sources=())) == 1


def test_a_switched_off_users_keywords_leave_the_filter(ctx):
    """Their terms should stop widening the harvest the moment they stop costing anything."""
    from services.harvester.source_config import build_source_configs

    s = ctx
    _active_user(s, keywords=["stillhere"])
    _active_user(s, keywords=["goneaway"], status="suspended", active=False)

    built = build_source_configs(TenantRepository(s).active_configs(), env={})
    assert built["himalayas"]["queries"] == [{"query": "stillhere"}]


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


def test_a_source_blocked_at_the_network_stays_off_even_holding_its_key():
    """Holding a valid credential is not the same as being able to reach the board.

    Jooble answers every request from the VPS with an HTML 403 block page — probed 2026-08-10 with
    a working key, on the international host and both country editions. `KEY_GATED` is what
    actually enables a source, appending to the active list whenever the credential is present and
    regardless of `HARVEST_SOURCES`, so dropping it from that tuple alone left it running and
    failing every cycle.
    """
    from services.harvester.source_config import build_source_configs

    assert "jooble" not in build_source_configs({}, env={"JOOBLE_API_KEY": "x"})


def test_query_taking_sources_always_get_a_query():
    """Found on the first real cycle against live sources."""
    from services.harvester.source_config import QUERY_TAKING_SOURCES, build_source_configs

    built = build_source_configs({}, env={})
    for key in QUERY_TAKING_SOURCES & built.keys():
        assert built[key].get("queries"), f"{key} would make no request at all"


def test_with_no_users_the_query_is_a_broad_feed_read():
    """Not a placeholder: an empty search fetches the board's unfiltered recent feed."""
    from services.harvester.source_config import build_source_configs

    assert build_source_configs({}, env={})["himalayas"]["queries"] == [{"query": ""}]


def test_users_keywords_become_the_queries(ctx):
    from services.harvester.source_config import build_source_configs

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust"])

    built = build_source_configs(TenantRepository(s).active_configs(), env={})
    assert built["himalayas"]["queries"] == [{"query": "golang"}, {"query": "rust"}]


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

def test_the_jobspy_tier_is_given_the_age_window_under_the_key_it_reads():
    """The bug that put month-old, already-closed postings into live feeds."""
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "user-1": {"sections": [{"keywords": ["python"], "posted_within_hours": 4}]}})

    assert configs, "no sources enabled — the assertion below would pass vacuously"
    for source, cfg in configs.items():
        assert cfg.get("hours_old") == cfg.get("max_age_hours"), (
            f"{source} would filter on one key and scrape on the other")
        # The number the user asked for, not merely a number.
        assert cfg["hours_old"] == 4, f"{source} ignored the window the user asked for"


def test_the_window_is_the_widest_any_active_account_asked_for():
    """The corpus is shared, so one fetch serves everyone and it has to be the most generous ask."""
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "tight": {"sections": [{"keywords": ["python"], "posted_within_hours": 2}]},
        "wide": {"sections": [{"keywords": ["python"], "posted_within_hours": 72}]}})

    assert all(cfg["hours_old"] == 72 for cfg in configs.values())
    assert all(cfg["hours_old"] != 2 for cfg in configs.values()), "the tighter ask won"


def test_an_account_may_ask_for_a_window_as_tight_as_the_reference_watcher():
    """The reference's frequent loop runs at two hours."""
    from libs.contracts.config import SearchSection

    assert SearchSection(keywords=["python"], posted_within_hours=2).posted_within_hours == 2


def test_the_operators_result_and_company_ceilings_reach_the_adapters():
    """Every adapter spells "how many" differently, and a value under the wrong key is silent."""
    from services.harvester.source_config import RESULT_KEYS, build_source_configs

    built = build_source_configs(
        {}, env={},
        results={"himalayas": 25, "nofluffjobs": 25, "jobicy": 25, "hiringcafe": 25},
        ats_companies=750)

    assert built["himalayas"]["limit"] == 25
    assert built["nofluffjobs"]["page_size"] == 25
    assert built["jobicy"]["count"] == 25
    assert built["hiringcafe"]["results_wanted"] == 25
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
    assert "limit" not in built["himalayas"], "no result key until somebody sets one"


def test_a_removed_source_cannot_be_quietly_switched_back_on():
    """`DEFERRED_SOURCES` records what was removed and why."""
    from libs.settings import HARVEST_SOURCES, QUERY_TAKING_SOURCES
    from services.harvester.source_config import (
        DEFAULT_ENABLED,
        DEFERRED_SOURCES,
        KEYWORD_FILTER_SOURCES,
    )

    deferred = set(DEFERRED_SOURCES)
    assert deferred & set(DEFAULT_ENABLED) == set()
    assert deferred & set(HARVEST_SOURCES) == set(), "the admin panel would offer it a control"
    assert deferred & QUERY_TAKING_SOURCES == set()
    assert deferred & KEYWORD_FILTER_SOURCES == set()


# ── how deep a paginated source walks (ROADMAP 6ag) ───────────────────────────────────────────


def test_the_operators_page_depth_reaches_the_two_sources_that_paginate():
    """Both were constants inside their adapters and neither appeared anywhere an operator looks."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={}, pages={"hiringcafe": 8, "workday": 2})

    assert built["hiringcafe"]["max_pages"] == 8
    assert built["workday"]["max_pages"] == 2


def test_a_source_with_no_page_setting_keeps_its_own_default():
    """An operator who has tuned one source must not silently reset every other one."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={}, pages={"hiringcafe": 8})

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
        ran.append(kwargs) or (cycle.CycleReport(), [])))

    assert entry.main([]) == 0
    assert ran, "the cycle itself ran, not just the entry point"


def test_with_no_site_proxy_every_board_but_linkedin_is_harvested(ctx, monkeypatch):
    """The site proxy is what LinkedIn needs and nothing else does: unset, LinkedIn is skipped and no
    other board loses anything."""
    from services.harvester import linkedin
    from services.harvester.sources import Tier, sources_for

    monkeypatch.delenv("HARVEST_PROXY", raising=False)
    s = ctx
    _active_user(s, keywords=["welder"])
    shared = {source.key for source in sources_for(Tier.SHARED)}
    asked: dict[str, str] = {}

    def board(source, cfg, repo):
        asked[source.key] = str(cfg.get("queries", ""))
        return [_link(title="Welder")] if source.key == "himalayas" else []

    report, _ = run_cycle(s, source_configs=_sources(**{key: {} for key in shared}),
                          run_source=board, describe=False)

    assert set(asked) == shared, "every shared board was asked"
    assert all("welder" in asked[key] for key in ("himalayas", "hiringcafe", "jooble", "nofluffjobs"))
    assert report.new_jobs == 1

    def no_request(*args, **kwargs):
        raise AssertionError("LinkedIn was fetched with no proxy to fetch it through")

    passed = linkedin.run_for_due_users(s, gather=no_request)
    assert (passed.considered, passed.links_found) == (0, 0)
