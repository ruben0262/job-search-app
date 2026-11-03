"""The LinkedIn pass: who is due, their searches pooled into distinct ones asked once, through the
site proxy, and when each account runs next."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import get, insert
from libs.db.models import User, UserConfig
from services.harvester import linkedin

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

CONFIG = {"sections": [{"keywords": ["python developer", "backend engineer"],
                        "locations": ["budapest"], "platform": "all",
                        "remote_policy": "remote_or_hybrid", "posted_within_hours": 6}]}


SITE_PROXY = "http://user:pass@proxy.example:8080"


@pytest.fixture(autouse=True)
def _site_proxy(monkeypatch):
    """The site's one proxy, set as it is on the server (`HARVEST_PROXY`), and no sweep reaching
    LinkedIn: these tests are about the word searches. The sweep's own are in `test_linkedin_sweep.py`."""
    monkeypatch.setenv("HARVEST_PROXY", SITE_PROXY)
    monkeypatch.setattr(linkedin, "sweep_places", lambda *_args, **_kwargs: [])


def _user(db, **kwargs) -> User:
    kwargs = {"status": "active", "active": True, **kwargs}
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", **kwargs))
    insert(db, UserConfig(user_id=user.id, config=CONFIG))
    return user


def _configure(db, user, config: dict) -> None:
    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": config}})


def _searched(db, now=NOW) -> list[dict]:
    """The searches one pass asks LinkedIn, in the order it asks them."""
    asked: list[dict] = []

    def gather(cfg, _log, *, proxy):
        asked.extend(cfg["queries"])
        return []

    linkedin.run_for_due_users(db, now=now, gather=gather)
    return asked


# ── who is due ────────────────────────────────────────────────────────────────────────────────

def test_a_user_who_has_never_run_is_due(db):
    """`NULL` means never, and never is exactly who should go next."""
    user = _user(db, linkedin_next_run_at=None)
    assert user.id in [u.id for u in linkedin.due_users(db, now=NOW)]


def test_a_user_scheduled_for_later_is_not_due(db):
    user = _user(db, linkedin_next_run_at=NOW + timedelta(hours=2))
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW)]


def test_an_inactive_user_is_never_due(db):
    """A paused account must not spend its own proxy bandwidth."""
    user = _user(db, linkedin_next_run_at=None, active=False)
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW)]


# ── pooled searches (owner, 2026-11-03) ───────────────────────────────────────────────────────

def test_a_pass_asks_each_distinct_search_once_however_many_people_share_it(db):
    """The cost of a pass follows the distinct searches, not the accounts: three people with the same
    two words in the same place are two searches, not six. The most-shared go first."""
    for _ in range(3):
        _user(db, linkedin_next_run_at=None)
    welder = _user(db, linkedin_next_run_at=None)
    _configure(db, welder, {"sections": [{"keywords": ["welder"], "locations": ["norway"],
                                          "platform": "all", "remote_policy": "any",
                                          "posted_within_hours": 24}]})

    asked = _searched(db)

    assert sorted((q["search_term"], q["location"], q["people"]) for q in asked) == [
        ("backend engineer", "Hungary", 3), ("python developer", "Hungary", 3), ("welder", "Norway", 1)]
    assert asked[-1]["search_term"] == "welder", "the shared searches first"


def test_the_searches_a_pass_asks_are_bounded_by_the_query_ceiling_shared_first(db):
    """`queries_per_cycle` bounds a pass now, not a count of accounts."""
    from libs import settings

    settings.put(db, "source_queries_per_cycle", {"linkedin": 2})
    for _ in range(2):
        _user(db, linkedin_next_run_at=None)
    loner = _user(db, linkedin_next_run_at=None)
    _configure(db, loner, {"sections": [{"keywords": ["welder"], "platform": "all"}]})

    assert {q["search_term"] for q in _searched(db)} == {"backend engineer", "python developer"}


def test_a_user_with_no_config_contributes_no_searches(db):
    insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                    active=True))
    assert _searched(db) == []


# ── what happens when it runs ─────────────────────────────────────────────────────────────────

def test_everyone_due_runs_through_the_site_proxy_with_none_of_their_own(db):
    """Nobody brings a proxy any more (owner's decision, 2026-11-01): the site's carries everyone's."""
    from libs.db.models import UserSecret

    for _ in range(2):
        _user(db, linkedin_next_run_at=None)
    proxies = []

    report = linkedin.run_for_due_users(db, now=NOW,
                                        gather=lambda cfg, log, *, proxy: proxies.append(proxy) or [])

    assert report.ran == 2
    assert proxies == [SITE_PROXY], "one pooled search, through the site's proxy"
    assert db[UserSecret.COLLECTION].count_documents({}) == 0, "and no credential of anybody's was read"


def test_without_the_site_proxy_nobodys_linkedin_runs_and_it_is_said(db, monkeypatch, caplog):
    import logging

    monkeypatch.delenv("HARVEST_PROXY")
    _user(db, linkedin_next_run_at=None)

    def no_request(*_args, **_kwargs):
        raise AssertionError("LinkedIn was fetched without the proxy")

    with caplog.at_level(logging.WARNING):
        report = linkedin.run_for_due_users(db, now=NOW, gather=no_request)

    assert (report.considered, report.links_found) == (0, 0)
    assert any("HARVEST_PROXY is not set" in record.getMessage() for record in caplog.records)


class _Pages:
    """The site proxy's client, answering every LinkedIn page with a description."""

    megabytes_downloaded = 0.0

    def __init__(self):
        self.asked: list[str] = []

    def __call__(self, _proxy):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, **_kwargs):
        from types import SimpleNamespace

        self.asked.append(url)
        return SimpleNamespace(status_code=200, url=url, text=(
            '<div class="show-more-less-html__markup">' + "Build the payments API. " * 20 + "</div>"))


def _found(count: int) -> list:
    from services.harvester.sources.base import JobLink

    return [JobLink(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**12}",
                    title="Python Developer", company=f"Acme {n}", location="Budapest, Hungary",
                    source="linkedin") for n in range(count)]


def test_every_posting_a_pass_finds_is_described_in_that_pass(db, monkeypatch):
    """No ceiling on how many: there is no bandwidth limit to keep under, and a posting without its
    text matches on its title only. It stopped at 60 a run, and the rest waited for a pass that never ran."""
    from libs.db.models import CorpusJob

    pages = _Pages()
    monkeypatch.setattr(linkedin, "make_site_client", pages)
    _user(db, linkedin_next_run_at=None)

    report = linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: _found(80))

    assert (report.new_jobs, report.described) == (80, 80)
    assert db[CorpusJob.COLLECTION].count_documents({"description": {"$regex": "payments API"}}) == 80


def test_postings_an_earlier_pass_left_are_described_when_nobody_is_due(db, monkeypatch):
    """What the unscheduled catch-up pass was for: every pass describes whatever is still waiting."""
    from libs.corpus.repository import CorpusRepository

    pages = _Pages()
    monkeypatch.setattr(linkedin, "make_site_client", pages)
    corpus = CorpusRepository(db)
    waiting = [corpus.add(**link.as_corpus_row()) for link in _found(3)]
    _user(db, linkedin_next_run_at=NOW + timedelta(hours=1))

    report = linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])

    assert (report.considered, report.described) == (0, 3)
    assert not [job for job in corpus.awaiting_description(linkedin=True) if job.id in waiting]


def test_the_next_run_is_the_accounts_own_interval(db):
    """The `linkedin_interval_min` limit, and nothing else, decides when an account runs next."""
    default, slower = _user(db, linkedin_next_run_at=None), _user(db, linkedin_next_run_at=None,
                                                                 limits={"linkedin_interval_min": 120})
    linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])

    def gap(user):
        return (get(db, User, user.id).linkedin_next_run_at - NOW).total_seconds() / 60

    # Jitter is ±10%.
    assert 15 * 0.9 <= gap(default) <= 15 * 1.1
    assert 120 * 0.9 <= gap(slower) <= 120 * 1.1


def test_a_failed_search_is_reported_and_the_pass_goes_on_to_describe(db, monkeypatch):
    """LinkedIn refusing the search is not the tier breaking: what earlier passes stored still waits."""
    described = []
    monkeypatch.setattr(linkedin, "_describe", lambda *a, **k: described.append(True))
    for _ in range(2):
        _user(db, linkedin_next_run_at=None)

    def explode(*_args, **_kwargs):
        raise RuntimeError("linkedin said no")

    report = linkedin.run_for_due_users(db, now=NOW, gather=explode)
    assert report.considered == 2
    assert list(report.failures) == ["search"] and described == [True]


def test_the_tier_can_be_killed_globally(db, monkeypatch):
    """The alternative during an incident is editing every user's config one at a time while LinkedIn is."""
    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "1")
    _user(db, linkedin_next_run_at=None)

    report = linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])
    assert report.considered == 0


def test_every_keyword_a_user_has_is_searched(db):
    """The location axis was costing this account twenty of its twenty-three searches."""
    user = _user(db)
    wanted = [f"term {i}" for i in range(20)]
    _configure(db, user, {"sections": [{"keywords": wanted,
                                             "locations": ["europe", "budapest",
                                                           "united_kingdom", "berlin"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]})

    queries = _searched(db)
    # Every term is searched, in every place the user asked for.
    assert {q["search_term"] for q in queries} == set(wanted), "every term reaches the board"
    assert len(queries) == len(wanted) * 4, "each term in each of the four places"


def test_a_board_gets_the_phrase_the_user_typed(db):
    """`keyword_norm` sorts tokens and strips accents so two users asking the same thing fold into one."""
    user = _user(db)
    _configure(db, user, {"sections": [{"keywords": ["stress engineer", "szoftverfejlesztő"],
                                             "locations": ["europe"], "platform": "all",
                                             "remote_policy": "any", "posted_within_hours": 6}]})

    terms = {q["search_term"] for q in _searched(db)}
    assert terms == {"stress engineer", "szoftverfejlesztő"}


def test_a_users_locations_reach_the_board_as_place_names(db):
    """LinkedIn can genuinely filter by place, so it is asked by place name."""
    user = _user(db)
    _configure(db, user, {"sections": [{"keywords": ["python developer"],
                                             "locations": ["europe", "hungary"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]})

    queries = _searched(db)
    locations = {q["location"] for q in queries}

    assert locations == {"European Union", "Hungary"}
    assert not any("_" in (loc or "") for loc in locations), "a slug reached the board"
    # One query per keyword per place, so both places are actually searched.
    assert len(queries) == 2


def test_the_linkedin_ceiling_covers_the_cross_product():
    """A ceiling below keyword × location does not trim evenly — queries are taken in key order."""
    from libs.settings import DEFAULT_SOURCE_QUERIES_PER_CYCLE

    # The founder's shape: 23 terms across 3 places.
    assert DEFAULT_SOURCE_QUERIES_PER_CYCLE["linkedin"] >= 23 * 3


def test_choosing_the_eu_and_a_country_searches_both_without_repeating_one(db):
    """The owner's rule, end to end."""
    from libs.corpus.geo import EU_COUNTRIES, allows, read_country, wanted_countries

    user = _user(db)
    chosen = ["europe", "united_kingdom", "switzerland"]
    _configure(db, user, {"sections": [{"keywords": ["ai engineer"], "locations": chosen,
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]})

    # One query per place, none repeated.
    places = [q["location"] for q in _searched(db)]
    assert sorted(places) == ["European Union", "Switzerland", "United Kingdom"]
    assert len(places) == len(set(places)), "the same place asked for twice is a wasted fetch"

    # And the countries those choices admit, which is what filtering will read.
    wanted = wanted_countries(chosen)
    assert wanted >= EU_COUNTRIES, "every member state, from the one Europe choice"
    assert {"GB", "CH"} <= wanted, "and the countries named individually"
    assert allows(read_country("Warsaw, Poland"), wanted)
    assert allows(read_country("London, United Kingdom"), wanted)
    assert allows(read_country("Zurich, Switzerland"), wanted)
    assert not allows(read_country("Austin, Texas"), wanted), "never asked for, never allowed"


def test_a_country_inside_europe_does_not_widen_or_narrow_the_set(db):
    """Adding Ireland to a Europe search changes nothing — it is already a member."""
    from libs.corpus.geo import wanted_countries

    assert wanted_countries(["europe"]) == wanted_countries(["europe", "ireland"])

    user = _user(db)
    _configure(db, user, {"sections": [{"keywords": ["ai engineer"],
                                             "locations": ["europe", "ireland"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]})
    # Both are still asked for: LinkedIn's "European Union" geo and its "Ireland" geo differ.
    assert len(_searched(db)) == 2


# ── how far back a search reaches (2026-11-02) ────────────────────────────────────────────────

def test_a_search_reaches_back_to_the_last_one_and_reads_it_whole(db):
    """Over 4 hours LinkedIn listed 200+ matches by relevance, and the first 30 held 1 of the 23 posted
    in the last hour. On time, a search covers the last hour, which a read of 50 takes whole; after a
    gap it reaches back over the gap, up to the operator's window; a first search takes the window."""
    configs: list = []

    def gather(cfg, _log, *, proxy):
        configs.append(cfg)
        return []

    people = {"on time": NOW, "late": NOW - timedelta(hours=2), "days late": NOW - timedelta(days=2),
              "first": None}
    for word, due in people.items():
        user = _user(db, linkedin_next_run_at=due)
        _configure(db, user, {"sections": [{"keywords": [word], "platform": "all"}]})

    linkedin.run_for_due_users(db, now=NOW, gather=gather)

    hours = {q["search_term"]: q["hours_old"] for q in configs[0]["queries"]}
    assert hours == {"on time": 1, "late": 3, "days late": 4, "first": 4}, "2h late + the 15-minute interval"
    assert configs[0]["results_wanted"] == 50


def test_a_shared_search_reaches_back_as_far_as_its_furthest_person(db):
    """Pooled, a search covers the widest gap among the people who share it: nobody's is left unread."""
    _user(db, linkedin_next_run_at=NOW)
    _user(db, linkedin_next_run_at=NOW - timedelta(hours=2))

    assert {q["hours_old"] for q in _searched(db)} == {3}
