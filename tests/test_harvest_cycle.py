"""One harvest cycle, end to end, with fake sources.

What is worth testing here is the **orchestration**: the order of stages, that the budget is
respected, that a dead source cannot sink the run, that overlapping runs cannot both proceed, and
that dormant users cost nothing. Whether Greenhouse's JSON parsed is not testable without a network
and is covered structurally instead (`tests/test_harvester_sources.py`).

No network, per `TEST_STRATEGY.md` §2.2 — sources are injected.
"""
from __future__ import annotations

import os
import uuid

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
            source_configs=_sources(remotive={}),
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
                             source_configs=_sources(remotive={}),
                             run_source=lambda *a: [link])
        s.flush()
        second, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(remotive={}),
                              run_source=lambda *a: [link])
    s.flush()

    assert first.new_jobs == 1
    assert second.new_jobs == 0
    assert second.duplicates.get("url") == 1


def test_a_snippet_saves_the_description_fetch(ctx):
    """Sources that supply usable text save a whole fetch per job — the difference between a
    15-minute cycle and an hour, and on the proxied tier a bandwidth saving too."""
    s = ctx
    _active_user(s)
    with _embedder() as embedder:
        run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                  source_configs=_sources(remotive={}),
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

    # **A source config, because the query set is built for the sources that are enabled.** It used
    # to be built for a hardcoded pair that was enabled nowhere, so the budget could be exercised
    # against `{}` -- which is precisely how the whole fold came to govern no requests at all.
    report, deferred = run_cycle(s, source_configs=_sources(remotive={}, himalayas={}),
                                 max_queries=7, run_source=lambda *a: [])
    assert report.queries_planned == 7
    assert report.queries_deferred > 0
    assert len(deferred) == report.queries_deferred


def test_deferred_queries_are_returned_for_the_next_cycle(ctx):
    """Advice for next time, not persisted state — a stored plan would have to be reconciled
    against configs that have since changed."""
    s = ctx
    for i in range(4):
        _active_user(s, keywords=[f"u{i}a", f"u{i}b"])

    asked = _sources(remotive={}, himalayas={})
    _, deferred = run_cycle(s, source_configs=asked, max_queries=3, run_source=lambda *a: [])
    assert deferred

    report2, _ = run_cycle(s, source_configs=asked, max_queries=3,
                           carried_over=deferred, run_source=lambda *a: [])
    assert report2.queries_planned == 3


# ── who is in the fold ────────────────────────────────────────────────────────────────────────

def test_a_dormant_user_costs_nothing(ctx):
    """G11's whole point: an abandoned account must stop consuming harvest budget."""
    s = ctx
    active = _active_user(s, keywords=["activeterm"])
    dormant = _active_user(s, keywords=["dormantterm"])
    dormant.status, dormant.active = "dormant", False
    s.flush()

    report, _ = run_cycle(s, source_configs=_sources(remotive={}), run_source=lambda *a: [])
    assert report.queries_planned >= 1
    assert active.id and dormant.id


def test_an_unverified_signup_consumes_no_harvest_budget(ctx):
    """TENANT_SAFETY §5: a signup flood must cost nothing."""
    s = ctx
    pending = _active_user(s)
    pending.status, pending.active = "pending_verification", False
    s.flush()

    report, _ = run_cycle(s, source_configs=_sources(remotive={}), run_source=lambda *a: [])
    assert report.queries_planned == 0


# ── failure containment ───────────────────────────────────────────────────────────────────────

def test_one_dead_source_cannot_sink_the_cycle(ctx):
    """Sources break upstream constantly. A cycle that aborts on the first failure delivers
    nothing to anybody."""
    s = ctx
    _active_user(s)
    good = _link()

    def flaky(source, cfg, repo):
        if source.key == "remotive":
            raise RuntimeError("upstream changed its JSON again")
        return [good]

    with _embedder() as embedder:
        report, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(remotive={}, himalayas={}),
                              run_source=flaky)
    s.flush()

    assert "remotive" in report.source_failures
    assert report.new_jobs == 1, "the healthy source still delivered"


# ── overlap ───────────────────────────────────────────────────────────────────────────────────

def test_two_cycles_cannot_run_at_once(ctx):
    """A timer firing while the previous run is still going must SKIP, not queue.

    Queueing turns one slow cycle into a backlog all doing the same work, and the fan-out budget is
    per cycle — two at once spends it twice, against sources that are already grey-area.
    """
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
    """**The de-personalisation.** The reference pre-filters feed sources on Ruben's keywords.

    Filtering a *shared* corpus on one person's terms would silently deny every other user the jobs
    they asked for — and they would never know, because the rows would simply never arrive.
    """
    from services.harvester.source_config import build_source_configs, union_keywords

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust", "Golang"])
    s.flush()

    configs = TenantRepository(s).active_configs()
    assert union_keywords(configs) == ["golang", "rust"], "deduplicated, lowercased, sorted"

    built = build_source_configs(configs, env={})
    assert built["remotive"]["keywords"] == ["golang", "rust"]


def test_the_filter_reads_the_sections_a_real_config_actually_stores(ctx):
    """**Every test here wrote a flat config and every real one stores sections.**

    That is the whole reason this survived: `union_keywords` and `union_locations` read
    `config["keywords"]` at the top level, where the schema never puts it, so against production
    they returned empty and every board on the free tier fetched its unfiltered recent feed while
    the platform behaved as though nobody had signed up. The same mistake had already been found
    and fixed for `posted_within_hours` four lines away.

    The flat shape stays supported — older fixtures pass it — so this asserts both.
    """
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
    """A query in the wrong shape is **silent**: the board answers its unfiltered feed and reports
    success, which from outside is indistinguishable from a deliberately broad search.

    One fixed dict used to go to all nine query-taking sources and only two of them read `search`.
    """
    from services.harvester.source_config import build_source_configs

    built = build_source_configs(
        {uuid.uuid4(): {"sections": [{"keywords": ["golang"]}]}}, env={})

    assert built["remotive"]["queries"] == [{"search": "golang"}]
    assert built["himalayas"]["queries"] == [{"query": "golang"}]
    assert built["nofluffjobs"]["queries"] == [{"keyword": "golang"}]
    assert built["hiringcafe"]["queries"] == [{"search_term": "golang"}]
    # Consider and Getro iterate strings, not dicts, and use each one as `searchText`.
    assert built["consider"]["queries"] == ["golang"]


def test_a_board_with_no_geography_is_asked_once_per_keyword(ctx):
    """**This is the redundancy, stated exactly.** Most boards take a search term and nothing else,
    so crossing their keywords with four location slugs produces four byte-identical requests, four
    times the budget, and one set of results.

    Collapsing the axis is not a narrowing: the user still asked for those locations, and the source
    was never going to apply them.
    """
    from services.harvester.query_set import build_query_set

    config = {uuid.uuid4(): {"sections": [
        {"keywords": ["golang"], "platform": "all",
         "locations": ["european_union", "united_kingdom", "budapest", "europe"]}]}}

    # `himalayas` has no location parameter at all; `hiringcafe` has a real one.
    keys = build_query_set(config, sources=("himalayas", "hiringcafe"),
                           locating_sources=("hiringcafe",))
    per_source: dict[str, int] = {}
    for key in keys:
        per_source[key.source] = per_source.get(key.source, 0) + 1

    assert per_source["himalayas"] == 1, "one keyword, one query, however many places were asked for"
    assert per_source["hiringcafe"] == 4, "a source that can filter by place still gets all four"


def test_no_locating_sources_is_not_the_same_as_not_saying(ctx):
    """An empty iterable means "nothing locates"; `None` means "everything does". A falsy check
    would collapse every axis on the caller that simply did not pass the argument — which is the
    per-user LinkedIn pass, the one source where location is the whole point."""
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
    assert built["remotive"]["keywords"] == ["stillhere"]


def test_with_no_users_at_all_sources_stay_enabled_and_unfiltered(ctx):
    """Cold start. The platform harvests broadly before anybody has configured anything, which is
    what makes a new user's first feed non-empty on day one rather than in week three."""
    from services.harvester.source_config import build_source_configs

    built = build_source_configs({}, env={})
    assert built, "sources must still run"
    assert "keywords" not in built["remotive"], "unfiltered rather than filtered to nothing"


def test_key_gated_sources_stay_off_without_their_credential(ctx):
    """A source that 401s every cycle is a failure count that means nothing and drowns real ones."""
    from services.harvester.source_config import build_source_configs

    assert "jooble" not in build_source_configs({}, env={})
    assert "jooble" in build_source_configs({}, env={"JOOBLE_API_KEY": "x"})


def test_query_taking_sources_always_get_a_query(ctx):
    """**Found on the first real cycle against live sources.**

    `remotive`, `himalayas` and `arbeitnow` loop over `cfg["queries"]` and return `[]` when it is
    absent — so they made no HTTP request, logged nothing, and the cycle reported success. An
    adapter returning `[]` is the registry's contract, which is exactly why the silence was total.
    """
    from services.harvester.source_config import QUERY_TAKING_SOURCES, build_source_configs

    built = build_source_configs({}, env={})
    for key in QUERY_TAKING_SOURCES & built.keys():
        assert built[key].get("queries"), f"{key} would make no request at all"


def test_with_no_users_the_query_is_a_broad_feed_read(ctx):
    """Not a placeholder: an empty search fetches the board's unfiltered recent feed, which is
    "harvest broadly, match per user" and is what gives a brand-new user a non-empty first feed."""
    from services.harvester.source_config import build_source_configs

    assert build_source_configs({}, env={})["remotive"]["queries"] == [{"search": ""}]


def test_users_keywords_become_the_queries(ctx):
    from services.harvester.source_config import build_source_configs

    s = ctx
    _active_user(s, keywords=["golang"])
    _active_user(s, keywords=["rust"])
    s.flush()

    built = build_source_configs(TenantRepository(s).active_configs(), env={})
    assert built["remotive"]["queries"] == [{"search": "golang"}, {"search": "rust"}]


def test_the_recency_window_is_the_widest_any_user_asked_for(ctx):
    """Same rule as the query-set fold: fetch once at the coarsest demand, filter down locally.

    The adapters default to 24h internally, which was fetching a far narrower slice than the config
    schema's own default of 72.
    """
    from services.harvester.source_config import DEFAULT_MAX_AGE_HOURS, build_source_configs

    s = ctx
    assert build_source_configs({}, env={})["remotive"]["max_age_hours"] == DEFAULT_MAX_AGE_HOURS

    a = _active_user(s)
    b = _active_user(s)
    t = TenantRepository(s)
    t.save_config(a.id, {"keywords": ["x"], "posted_within_hours": 24})
    t.save_config(b.id, {"keywords": ["y"], "posted_within_hours": 168})
    s.flush()

    built = build_source_configs(t.active_configs(), env={})
    assert built["remotive"]["max_age_hours"] == 168


# ── the age window actually reaching the sources ──────────────────────────────────────────────

def test_the_jobspy_tier_is_given_the_age_window_under_the_key_it_reads():
    """**The bug that put month-old, already-closed postings into live feeds.**

    The ATS and REST adapters filter on `max_age_hours`; the JobSpy tier — LinkedIn, Indeed,
    Google, which produce most of the volume — passes `hours_old` to the scraper and ignores
    anything else. Only the first was ever set, so the age filter was real for two thirds of the
    sources and absent for the ones that mattered. The reference's watcher sets both and its
    comment says exactly why.
    """
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "user-1": {"sections": [{"keywords": ["python"], "posted_within_hours": 4}]}})

    assert configs, "no sources enabled — the assertion below would pass vacuously"
    for source, cfg in configs.items():
        assert cfg.get("hours_old") == cfg.get("max_age_hours"), (
            f"{source} would filter on one key and scrape on the other")
        # **The number the user asked for, not merely a number.** The first version of this test
        # checked only that the two keys agreed, which they did — both at the platform default,
        # because the window was being read from the top level of the config where it has never
        # lived. A user asking for four hours was harvested at seventy-two and nothing said so.
        assert cfg["hours_old"] == 4, f"{source} ignored the window the user asked for"


def test_the_window_is_the_widest_any_active_account_asked_for():
    """The corpus is shared, so one fetch serves everyone and it has to be the most generous ask.
    Each user's own window is then applied at delivery, not here."""
    from services.harvester.source_config import build_source_configs

    configs = build_source_configs({
        "tight": {"sections": [{"keywords": ["python"], "posted_within_hours": 2}]},
        "wide": {"sections": [{"keywords": ["python"], "posted_within_hours": 72}]}})

    assert all(cfg["hours_old"] == 72 for cfg in configs.values())
    assert all(cfg["hours_old"] != 2 for cfg in configs.values()), "the tighter ask won"


def test_an_account_may_ask_for_a_window_as_tight_as_the_reference_watcher():
    """The reference's frequent loop runs at two hours, and being first to a posting is the single
    biggest thing a candidate controls. A six-hour floor made the platform's tightest setting three
    times looser than the instance it was ported from."""
    from libs.contracts.config import SearchSection

    assert SearchSection(keywords=["python"], posted_within_hours=2).posted_within_hours == 2
