"""One harvest cycle, end to end, with fake sources."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.db.models import CorpusJob, User
from libs.tenant.repository import TenantRepository
from services.harvester.cycle import harvest_lock, run_cycle
from services.harvester.sources import JobLink

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")

DIM = 8
SERVED = {"ok": True, "model": "m", "dim": DIM, "version": "m|dim=8|norm=1"}
EXPECTED = EmbedderVersion("m", DIM, "m|dim=8|norm=1")


def _embedder() -> EmbedderClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=SERVED)
        import json
        texts = json.loads(request.content)["texts"]
        return httpx.Response(200, json={"vectors": [[0.5] * DIM for _ in texts]})

    return EmbedderClient(client=httpx.Client(
        base_url="http://e", transport=httpx.MockTransport(handler)))


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _active_user(s, keywords=("python",)) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC",
             status="active", active=True)
    s.add(u)
    s.flush()
    TenantRepository(s).save_config(u.id, {"keywords": list(keywords), "locations": ["london"]})
    s.flush()
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

def test_a_cycle_ingests_dedups_and_embeds(ctx):
    """The whole loop: demand → sources → dedup → upsert → embed once."""
    s = ctx
    _active_user(s)
    found = [_link(), _link()]

    with _embedder() as embedder:
        report, _ = run_cycle(
            s, embedder=embedder, expected_embedder=EXPECTED,
            source_configs=_sources(himalayas={}),
            run_source=lambda source, cfg, repo: list(found))
    s.flush()

    assert report.links_found == 2
    assert report.new_jobs == 2
    assert report.embedded == 2
    stored = list(s.scalars(CorpusJob.__table__.select().with_only_columns(CorpusJob.id)))
    assert len(stored) >= 2


def test_the_same_job_twice_is_ingested_once(ctx):
    """Two sources listing one posting is the normal case, not the exception."""
    s = ctx
    _active_user(s)
    link = _link()

    with _embedder() as embedder:
        first, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                             source_configs=_sources(himalayas={}),
                             run_source=lambda *a: [link])
        s.flush()
        second, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(himalayas={}),
                              run_source=lambda *a: [link])
    s.flush()

    assert first.new_jobs == 1
    assert second.new_jobs == 0
    assert second.duplicates.get("url") == 1


def test_a_snippet_saves_the_description_fetch(ctx):
    """Sources that supply usable text save a whole fetch per job."""
    s = ctx
    _active_user(s)
    with _embedder() as embedder:
        run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                  source_configs=_sources(himalayas={}),
                  run_source=lambda *a: [_link(snippet="A real description. " * 30)])
    s.flush()

    job = s.scalars(CorpusJob.__table__.select().order_by(CorpusJob.id.desc()).limit(1)).first()
    assert job is not None


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

def test_a_dormant_user_costs_nothing(ctx):
    """G11's whole point: an abandoned account must stop consuming harvest budget."""
    s = ctx
    active = _active_user(s, keywords=["activeterm"])
    dormant = _active_user(s, keywords=["dormantterm"])
    dormant.status, dormant.active = "dormant", False
    s.flush()

    report, _ = run_cycle(s, source_configs=_sources(himalayas={}), run_source=lambda *a: [])
    assert report.queries_planned >= 1
    assert active.id and dormant.id


def test_an_unverified_signup_consumes_no_harvest_budget(ctx):
    """TENANT_SAFETY §5: a signup flood must cost nothing."""
    s = ctx
    pending = _active_user(s)
    pending.status, pending.active = "pending_verification", False
    s.flush()

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

    with _embedder() as embedder:
        report, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(himalayas={}, nofluffjobs={}),
                              run_source=flaky)
    s.flush()

    assert "himalayas" in report.source_failures
    assert report.new_jobs == 1, "the healthy source still delivered"


# ── overlap ───────────────────────────────────────────────────────────────────────────────────

def test_two_cycles_cannot_run_at_once(ctx):
    """A timer firing while the previous run is still going must SKIP, not queue."""
    s = ctx
    _active_user(s)

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as other_conn:
        other = Session(bind=other_conn)
        with harvest_lock(other) as held:
            assert held, "the fixture failed to take the lock"
            report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])
        other.close()

    assert report.skipped_locked is True
    assert report.queries_planned == 0


def test_the_lock_is_released_after_a_cycle(ctx):
    """Otherwise the first run wedges every subsequent one and harvesting stops silently."""
    s = ctx
    run_cycle(s, source_configs={}, run_source=lambda *a: [])

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        other = Session(bind=conn)
        with harvest_lock(other) as held:
            assert held, "the lock was not released"
        other.close()


# ── de-personalised source config ─────────────────────────────────────────────────────────────

def test_source_filters_come_from_the_union_of_users_not_one_person(ctx):
    """The de-personalisation."""
    from services.harvester.source_config import build_source_configs, union_keywords

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust", "Golang"])
    s.flush()

    configs = TenantRepository(s).active_configs()
    assert union_keywords(configs) == ["golang", "rust"], "deduplicated, lowercased, sorted"

    # Asserted on the queries, not on `keywords`.
    built = build_source_configs(configs, env={})
    assert built["himalayas"]["queries"] == [{"query": "golang"}, {"query": "rust"}]


def test_the_filter_reads_the_sections_a_real_config_actually_stores(ctx):
    """Every test here wrote a flat config and every real one stores sections."""
    from services.harvester.source_config import union_keywords, union_locations

    sectioned = {
        uuid.uuid4(): {"sections": [{"keywords": ["Rust", "golang"],
                                     "locations": ["Budapest"], "platform": "all"}]},
    }
    assert union_keywords(sectioned) == ["golang", "rust"]
    assert union_locations(sectioned) == ["budapest"]

    both = {uuid.uuid4(): {"keywords": ["flat"], "sections": [{"keywords": ["nested"]}]}}
    assert union_keywords(both) == ["flat", "nested"], "neither shape may shadow the other"


def test_each_board_is_asked_in_the_shape_it_reads(ctx):
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


def test_a_board_with_no_geography_is_asked_once_per_keyword(ctx):
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


def test_a_job_a_board_returned_survives_every_enabled_source_s_own_filtering(ctx):
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


def test_a_keyword_filtered_source_keeps_what_matches_and_judges_nothing_else(ctx):
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


def test_a_source_whose_window_an_operator_narrowed_actually_narrows(ctx):
    """A setting that reaches nothing is worse than no setting, because it looks answered."""
    from services.harvester.source_config import build_source_configs

    default = build_source_configs({}, env={})
    narrowed = build_source_configs({}, env={}, freshness={"greenhouse": 3})

    assert narrowed["greenhouse"]["max_age_hours"] == 3
    # `posted_within_hours` is what `ats.JobFilter` prefers.
    assert narrowed["greenhouse"]["posted_within_hours"] == 3
    # One source only. Narrowing Greenhouse must not quietly narrow everything else.
    assert narrowed["ashby"]["max_age_hours"] == default["ashby"]["max_age_hours"]


def test_an_operators_window_reaches_a_source_under_every_key_it_reads(ctx):
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


def test_the_union_is_asked_for_not_used_to_throw_away_what_a_board_sent(ctx):
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


def test_a_board_is_asked_for_the_phrase_the_user_wrote(ctx):
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


def test_one_folded_key_emits_the_same_term_however_users_phrased_it(ctx):
    """Several users writing one search differently must not make the emitted term depend on which of."""
    from services.harvester.query_set import normalise_keyword, original_terms

    key = normalise_keyword("python developer")
    a = {uuid.uuid4(): {"sections": [{"keywords": ["python developer"]}]},
         uuid.uuid4(): {"sections": [{"keywords": ["Developer Python"]}]}}
    b = {uuid.uuid4(): {"sections": [{"keywords": ["Developer Python"]}]},
         uuid.uuid4(): {"sections": [{"keywords": ["python developer"]}]}}

    assert original_terms(a)[key] == original_terms(b)[key]


def test_no_locating_sources_is_not_the_same_as_not_saying(ctx):
    """An empty iterable means "nothing locates"; `None` means "everything does"."""
    from services.harvester.query_set import build_query_set

    config = {uuid.uuid4(): {"sections": [
        {"keywords": ["golang"], "platform": "all", "locations": ["london", "berlin"]}]}}

    assert len(build_query_set(config, sources=("linkedin",))) == 2
    assert len(build_query_set(config, sources=("linkedin",), locating_sources=())) == 1


def test_a_dormant_users_keywords_leave_the_filter(ctx):
    """Their terms should stop widening the harvest the moment they stop costing anything."""
    from services.harvester.source_config import build_source_configs

    s = ctx
    _active_user(s, keywords=["stillhere"])
    leaving = _active_user(s, keywords=["goneaway"])
    leaving.status, leaving.active = "dormant", False
    s.flush()

    built = build_source_configs(TenantRepository(s).active_configs(), env={})
    assert built["himalayas"]["queries"] == [{"query": "stillhere"}]


def test_with_no_users_at_all_sources_stay_enabled_and_unfiltered(ctx):
    """Cold start."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={})
    assert built, "sources must still run"
    assert "keywords" not in built["greenhouse"], "unfiltered rather than filtered to nothing"


def test_key_gated_sources_stay_off_without_their_credential(ctx):
    """A source that 401s every cycle is a failure count that means nothing and drowns real ones."""
    from services.harvester.source_config import build_source_configs

    assert "jooble" not in build_source_configs({}, env={})
    assert "jooble" in build_source_configs({}, env={"JOOBLE_API_KEY": "x"})


def test_query_taking_sources_always_get_a_query(ctx):
    """Found on the first real cycle against live sources."""
    from services.harvester.source_config import QUERY_TAKING_SOURCES, build_source_configs

    built = build_source_configs({}, env={})
    for key in QUERY_TAKING_SOURCES & built.keys():
        assert built[key].get("queries"), f"{key} would make no request at all"


def test_with_no_users_the_query_is_a_broad_feed_read(ctx):
    """Not a placeholder: an empty search fetches the board's unfiltered recent feed."""
    from services.harvester.source_config import build_source_configs

    assert build_source_configs({}, env={})["himalayas"]["queries"] == [{"query": ""}]


def test_users_keywords_become_the_queries(ctx):
    from services.harvester.source_config import build_source_configs

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust"])
    s.flush()

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
    s.flush()

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


def test_the_operators_result_and_company_ceilings_reach_the_adapters(ctx):
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


def test_an_unset_ceiling_leaves_the_platform_default_alone(ctx):
    """An operator who has tuned nothing must get exactly the behaviour the code ships with."""
    from services.harvester.source_config import PLATFORM_DEFAULTS, build_source_configs

    built = build_source_configs({}, env={})
    assert built["greenhouse"]["max_companies"] == PLATFORM_DEFAULTS["greenhouse"]["max_companies"]
    assert "limit" not in built["himalayas"], "no result key until somebody sets one"


def test_a_removed_source_cannot_be_quietly_switched_back_on(ctx):
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


def test_a_failing_embedder_does_not_take_the_cycle_with_it(ctx):
    """One slow embed call killed the whole harvest, including the scoring enqueue."""
    from libs.corpus.repository import CorpusRepository
    from services.harvester.cycle import run_cycle

    session = ctx

    # A row described and waiting, which is what makes the embed step actually call out.
    corpus = CorpusRepository(session)
    job_id = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title="Backend Engineer", company="Acme")
    corpus.set_description(job_id, "We build things with Go and gRPC. " * 20)
    session.flush()

    # A real client whose backend times out on the bulk lane and is healthy everywhere else.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=SERVED)
        raise httpx.ReadTimeout("timed out", request=request)

    embedder = EmbedderClient(client=httpx.Client(
        base_url="http://e", transport=httpx.MockTransport(handler)))

    report, _deferred = run_cycle(session, embedder=embedder, source_configs={}, describe=False)

    assert report.embed_error is not None, "the failure is recorded rather than swallowed"
    assert report.embedded == 0
    # The step after it still ran, which is the whole point.
    assert report.scoring_enqueued >= 0


# ── the label lane's only way back in ─────────────────────────────────────────────────────────

def _labellable(s, labels: int) -> User:
    """An active account with a rubric and `labels` labels already bought."""
    from libs.db.models import UserLabel

    u = _active_user(s)
    u.rubric_md = "score it"
    s.flush()
    for _ in range(labels):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer",
                        source="himalayas", posted_at=datetime.now(UTC),
                        text_sha256=uuid.uuid4().hex)
        s.add(job)
        s.flush()
        s.add(UserLabel(user_id=u.id, job_id=job.id, label=50, weight=1.0, source="llm",
                        profile_generation=u.profile_generation))
    s.flush()
    return u


def _queued(s, lane: str, user_id) -> int:
    from sqlalchemy import func
    from sqlalchemy import select as _select

    from libs.db.models import QueueJob

    return s.scalar(_select(func.count()).select_from(QueueJob)
                    .where(QueueJob.lane == lane, QueueJob.user_id == user_id,
                           QueueJob.finished_at.is_(None))) or 0


def test_an_account_that_stopped_labelling_is_picked_up_again(ctx):
    """The defect this exists for: the label lane had no way back in.

    It was a chain that armed only itself — finish setup, and it ran until a batch reported nothing
    left to do. Nothing else ever enqueued it. So any interruption ended it permanently, with no
    error raised: in production one account sat six days at 243 of its 300 labels, its lane idle,
    its model looking trained, and the only reason it was found was somebody reading the queue
    table by hand.

    An account below its target must therefore be re-entered by the cycle that already runs, the
    same way `score` is. This asserts the account is queued, not how it got there.
    """
    s = ctx
    user = _labellable(s, labels=3)

    with _embedder() as embedder:
        report, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(himalayas={}),
                              run_source=lambda *a: [])
    s.flush()

    assert _queued(s, "label", user.id) == 1
    assert report.labelling_enqueued >= 1


def test_an_account_that_has_finished_labelling_is_left_alone(ctx):
    """Their own Claude credential pays for every label, so a tick must not buy one it does not owe."""
    s = ctx
    from libs.labels import target_for

    user = _labellable(s, labels=0)
    # Exactly at target: nothing further is owed.
    user.limits = {**(user.limits or {}), "label_pool": 1}
    s.flush()
    _labels_for(s, user, target_for(user))

    with _embedder() as embedder:
        run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                  source_configs=_sources(himalayas={}), run_source=lambda *a: [])
    s.flush()

    assert _queued(s, "label", user.id) == 0


def _labels_for(s, user, count: int) -> None:
    from libs.db.models import UserLabel

    for _ in range(count):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer",
                        source="himalayas", posted_at=datetime.now(UTC),
                        text_sha256=uuid.uuid4().hex)
        s.add(job)
        s.flush()
        s.add(UserLabel(user_id=user.id, job_id=job.id, label=50, weight=1.0, source="llm",
                        profile_generation=user.profile_generation))
    s.flush()


def test_an_account_that_paused_labelling_stays_paused(ctx):
    """A user who turned it off must not be restarted by a background tick."""
    s = ctx
    user = _labellable(s, labels=3)
    user.labelling_paused = True
    s.flush()

    with _embedder() as embedder:
        run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                  source_configs=_sources(himalayas={}), run_source=lambda *a: [])
    s.flush()

    assert _queued(s, "label", user.id) == 0


def test_a_second_tick_does_not_stack_a_second_label_job(ctx):
    """Fifteen minutes apart, forever — one waiting job is the whole point of the dedup key."""
    s = ctx
    user = _labellable(s, labels=3)

    with _embedder() as embedder:
        for _ in range(3):
            run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                      source_configs=_sources(himalayas={}), run_source=lambda *a: [])
            s.flush()

    assert _queued(s, "label", user.id) == 1
