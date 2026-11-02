"""The per-user LinkedIn pass: who runs, through the site proxy, within whose bandwidth, and when
they run next."""
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
    """The site's one proxy, set as it is on the server (`HARVEST_PROXY`)."""
    monkeypatch.setenv("HARVEST_PROXY", SITE_PROXY)


def _user(db, **kwargs) -> User:
    kwargs = {"status": "active", "active": True, **kwargs}
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", **kwargs))
    insert(db, UserConfig(user_id=user.id, config=CONFIG))
    return user


def _configure(db, user, config: dict) -> None:
    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": config}})


# ── who is due ────────────────────────────────────────────────────────────────────────────────

def test_a_user_who_has_never_run_is_due(db):
    """`NULL` means never, and never is exactly who should go next."""
    user = _user(db, linkedin_next_run_at=None)
    assert user.id in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_a_user_scheduled_for_later_is_not_due(db):
    user = _user(db, linkedin_next_run_at=NOW + timedelta(hours=2))
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_an_inactive_user_is_never_due(db):
    """A paused account must not spend its own proxy bandwidth."""
    user = _user(db, linkedin_next_run_at=None, active=False)
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_the_pass_is_bounded_by_the_concurrency_ceiling(db):
    """The governor's ceiling exists so a hundred accounts do not hit LinkedIn at once from a hundred."""
    for _ in range(6):
        _user(db, linkedin_next_run_at=None)
    assert len(linkedin.due_users(db, now=NOW, limit=4)) == 4


# ── whose queries ─────────────────────────────────────────────────────────────────────────────

def test_the_queries_are_this_users_own(db):
    """The shared cycle folds everyone's terms because the corpus is shared."""
    mine = _user(db)
    other = _user(db)
    _configure(db, other, {"sections": [{"keywords": ["welder"], "locations": ["oslo"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 24}]})

    terms = {q["search_term"] for q in linkedin.queries_for(db, mine)}
    assert terms and not any("welder" in term for term in terms)


def test_a_user_with_no_config_contributes_no_queries(db):
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                           active=True))
    assert linkedin.queries_for(db, user) == []


# ── what happens when it runs ─────────────────────────────────────────────────────────────────

def test_everyone_due_runs_through_the_site_proxy_with_none_of_their_own(db):
    """Nobody brings a proxy any more (owner's decision, 2026-11-01): the site's carries everyone's."""
    from libs.db.models import UserSecret

    users = [_user(db, linkedin_next_run_at=None) for _ in range(2)]
    leases = []

    report = linkedin.run_for_due_users(db, now=NOW,
                                        gather=lambda cfg, log, *, lease: leases.append(lease) or [])

    assert report.ran == 2
    assert sorted((str(lease.user_id), lease.proxy_url) for lease in leases) == sorted(
        (str(user.id), SITE_PROXY) for user in users)
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


def test_one_users_failure_does_not_stop_the_pass(db):
    """One account's LinkedIn breaking is not the tier breaking."""
    for _ in range(2):
        _user(db, linkedin_next_run_at=None)

    def explode(*_args, **_kwargs):
        raise RuntimeError("linkedin said no")

    report = linkedin.run_for_due_users(db, now=NOW, gather=explode)
    assert report.considered == 2
    assert len(report.failures) == 2, "the second account ran after the first one failed"


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

    queries = linkedin.queries_for(db, user)
    # Every term is searched, in every place the user asked for.
    assert {q["search_term"] for q in queries} == set(wanted), "every term reaches the board"
    assert len(queries) == len(wanted) * 4, "each term in each of the four places"


def test_a_board_gets_the_phrase_the_user_typed(db):
    """`keyword_norm` sorts tokens and strips accents so two users asking the same thing fold into one."""
    user = _user(db)
    _configure(db, user, {"sections": [{"keywords": ["stress engineer", "szoftverfejlesztő"],
                                             "locations": ["europe"], "platform": "all",
                                             "remote_policy": "any", "posted_within_hours": 6}]})

    terms = {q["search_term"] for q in linkedin.queries_for(db, user)}
    assert terms == {"stress engineer", "szoftverfejlesztő"}


def test_a_users_locations_reach_the_board_as_place_names(db):
    """Per user, on their own proxy, and LinkedIn can genuinely filter by place."""
    user = _user(db)
    _configure(db, user, {"sections": [{"keywords": ["python developer"],
                                             "locations": ["europe", "hungary"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]})

    queries = linkedin.queries_for(db, user)
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
    places = [q["location"] for q in linkedin.queries_for(db, user)]
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
    assert len(linkedin.queries_for(db, user)) == 2


# ── how far back a search reaches (2026-11-02) ────────────────────────────────────────────────

def test_a_search_reaches_back_to_the_last_one_and_reads_it_whole(db):
    """Over 4 hours LinkedIn listed 200+ matches by relevance, and the first 30 held 1 of the 23 posted
    in the last hour. On time, a search covers the last hour, which a read of 50 takes whole; after a
    gap it reaches back over the gap, up to the operator's window; a first search takes the window."""
    asked: dict[uuid.UUID, dict] = {}

    def gather(cfg, _log, *, lease):
        asked[lease.user_id] = cfg
        return []

    on_time = _user(db, linkedin_next_run_at=NOW)
    late = _user(db, linkedin_next_run_at=NOW - timedelta(hours=2))
    days_late = _user(db, linkedin_next_run_at=NOW - timedelta(days=2))
    first = _user(db, linkedin_next_run_at=None)

    linkedin.run_for_due_users(db, now=NOW, gather=gather)

    hours = [asked[user.id]["hours_old"] for user in (on_time, late, days_late, first)]
    assert hours == [1, 3, 4, 4], "on time 1h; 2h late plus the 15-minute interval is 3; capped at 4"
    assert {cfg["results_wanted"] for cfg in asked.values()} == {50}
