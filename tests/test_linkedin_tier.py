"""The per-user LinkedIn pass: who runs, whose credential, and when they run next.

**This tier had no runner for four days and nothing said so.** The adapter, the lease, the
governor and both scheduling columns all existed; the loop between them did not, so every LinkedIn
row in the corpus shared one `first_seen_at` and "my feed is quiet" looked like a scoring problem.

What is worth testing here is the orchestration, not the scraping: who is due, whose proxy is used,
what happens when a lease is refused, and whether the next run is always scheduled. `gather` is
injected, so none of it needs jobspy or a network.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.db.models import User, UserConfig
from services.harvester import linkedin

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

CONFIG = {"sections": [{"keywords": ["python developer", "backend engineer"],
                        "locations": ["budapest"], "platform": "all",
                        "remote_policy": "remote_or_hybrid", "posted_within_hours": 6}]}


@pytest.fixture(autouse=True)
def _kek():
    """A key, because `SecretStore` refuses to run without one — correctly, since the alternative
    is a platform that silently degrades to storing credentials it cannot protect."""
    from libs.secrets import generate_key
    os.environ.setdefault("SECRETS_KEK", f"k1:{generate_key()}")


@pytest.fixture()
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _user(session, **kwargs) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                active=True, **kwargs)
    session.add(user)
    session.flush()
    session.add(UserConfig(user_id=user.id, config=CONFIG))
    session.flush()
    return user


# ── who is due ────────────────────────────────────────────────────────────────────────────────

def test_a_user_who_has_never_run_is_due(db):
    """`NULL` means never, and never is exactly who should go next. Treating an unset column as
    "not yet" would leave every new account permanently unscheduled."""
    user = _user(db, linkedin_next_run_at=None)
    assert user.id in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_a_user_scheduled_for_later_is_not_due(db):
    user = _user(db, linkedin_next_run_at=NOW + timedelta(hours=2))
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_an_inactive_user_is_never_due(db):
    """A paused account must not spend its own proxy bandwidth."""
    user = _user(db, linkedin_next_run_at=None)
    user.active = False
    db.flush()
    assert user.id not in [u.id for u in linkedin.due_users(db, now=NOW, limit=10)]


def test_the_pass_is_bounded_by_the_concurrency_ceiling(db):
    """The governor's ceiling exists so a hundred accounts do not hit LinkedIn at once from a
    hundred residential IPs, which is what gets a proxy range blocked."""
    for _ in range(6):
        _user(db, linkedin_next_run_at=None)
    assert len(linkedin.due_users(db, now=NOW, limit=4)) == 4


# ── whose queries ─────────────────────────────────────────────────────────────────────────────

def test_the_queries_are_this_users_own(db):
    """The shared cycle folds everyone's terms because the corpus is shared. Here one person pays,
    so sending the folded set would spend their bandwidth on other users' jobs."""
    mine = _user(db)
    other = _user(db)
    db.execute(UserConfig.__table__.update()
               .where(UserConfig.user_id == other.id)
               .values(config={"sections": [{"keywords": ["welder"], "locations": ["oslo"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 24}]}))
    db.flush()

    terms = {q["search_term"] for q in linkedin.queries_for(db, mine)}
    assert terms and not any("welder" in term for term in terms)


def test_a_user_with_no_config_contributes_no_queries(db):
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                active=True)
    db.add(user)
    db.flush()
    assert linkedin.queries_for(db, user) == []


# ── what happens when it runs ─────────────────────────────────────────────────────────────────

def test_a_user_with_no_proxy_is_skipped_and_still_rescheduled(db):
    """**Always rescheduled.** Leaving the column unset would re-attempt them on every tick, and
    the tick is every fifteen minutes."""
    user = _user(db, linkedin_next_run_at=None)

    report = linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])
    db.flush()

    assert report.skipped_no_lease >= 1
    assert user.linkedin_next_run_at is not None
    assert user.linkedin_next_run_at > NOW


def test_the_next_run_respects_the_interval_floor(db):
    """A user on a fifteen-minute digest does not get LinkedIn read ninety-six times a day: the
    governor floors this tier at an hour because it is their bandwidth, not the platform's."""
    from services.harvester.governor import LINKEDIN_MIN_INTERVAL_MIN

    user = _user(db, linkedin_next_run_at=None, linkedin_interval_min=15)
    linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])
    db.flush()

    gap = (user.linkedin_next_run_at - NOW).total_seconds() / 60
    # Jitter is ±10%, so the floor is what the lower bound has to clear.
    assert gap >= LINKEDIN_MIN_INTERVAL_MIN * 0.85


def test_one_users_failure_does_not_stop_the_pass(db):
    """One account's LinkedIn breaking is not the tier breaking — the same contract every shared
    source adapter has."""
    _user(db, linkedin_next_run_at=None)
    _user(db, linkedin_next_run_at=None)

    def explode(*_args, **_kwargs):
        raise RuntimeError("linkedin said no")

    report = linkedin.run_for_due_users(db, now=NOW, gather=explode)
    assert report.considered == 2


def test_the_tier_can_be_killed_globally(db, monkeypatch):
    """The alternative during an incident is editing every user's config one at a time while
    LinkedIn is actively blocking."""
    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "1")
    _user(db, linkedin_next_run_at=None)

    report = linkedin.run_for_due_users(db, now=NOW, gather=lambda *a, **k: [])
    assert report.considered == 0


def test_every_keyword_a_user_has_is_searched(db):
    """**The location axis was costing this account twenty of its twenty-three searches.**

    23 keywords crossed with 4 locations is 92 queries against a ceiling of 12, so LinkedIn was
    asked for the alphabetically first three terms in four places each and never saw the rest — on
    the source that produced 337 of the founder's 348 jobs at or above the delivery cutoff.

    One query per keyword now, no location, matching the shared cycle: a posting's country is on the
    posting, so it is a filter against the corpus rather than a question multiplied into a query set.
    """
    user = _user(db)
    wanted = [f"term {i}" for i in range(20)]
    db.execute(UserConfig.__table__.update()
               .where(UserConfig.user_id == user.id)
               .values(config={"sections": [{"keywords": wanted,
                                             "locations": ["europe", "budapest",
                                                           "united_kingdom", "berlin"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]}))
    db.flush()

    queries = linkedin.queries_for(db, user)
    # Every term is searched, in every place the user asked for. The ceiling is sized to cover the
    # cross product rather than to truncate it — a cap below it drops whole locations, because
    # queries are taken in key order.
    assert {q["search_term"] for q in queries} == set(wanted), "every term reaches the board"
    assert len(queries) == len(wanted) * 4, "each term in each of the four places"


def test_a_board_gets_the_phrase_the_user_typed(db):
    """`keyword_norm` sorts tokens and strips accents so two users asking the same thing fold into
    one query. Typing that into a search box asks for `engineer stress` and truncates every accented
    term, which is the defect the shared cycle had first."""
    user = _user(db)
    db.execute(UserConfig.__table__.update()
               .where(UserConfig.user_id == user.id)
               .values(config={"sections": [{"keywords": ["stress engineer", "szoftverfejlesztő"],
                                             "locations": ["europe"], "platform": "all",
                                             "remote_policy": "any", "posted_within_hours": 6}]}))
    db.flush()

    terms = {q["search_term"] for q in linkedin.queries_for(db, user)}
    assert terms == {"stress engineer", "szoftverfejlesztő"}


def test_a_users_locations_reach_the_board_as_place_names(db):
    """**Per user, on their own proxy, and LinkedIn can genuinely filter by place.**

    The shared cycle drops the location axis because one query there serves everyone and most boards
    cannot apply a place anyway. Neither holds here: a filter the board applies costs nothing, and
    one applied afterwards is bandwidth already spent. Dropping it for a few hours put 384 of 1,165
    rows in the United States for an account that never asked for it.

    The board wants a name, not a slug — `europe` resolves to nothing, and LinkedIn answered one
    query with `Invalid country string` before the mapping existed.
    """
    user = _user(db)
    db.execute(UserConfig.__table__.update()
               .where(UserConfig.user_id == user.id)
               .values(config={"sections": [{"keywords": ["python developer"],
                                             "locations": ["europe", "budapest"],
                                             "platform": "all", "remote_policy": "any",
                                             "posted_within_hours": 6}]}))
    db.flush()

    queries = linkedin.queries_for(db, user)
    locations = {q["location"] for q in queries}

    assert locations == {"European Union", "Budapest"}
    assert not any("_" in (loc or "") for loc in locations), "a slug reached the board"
    # One query per keyword per place, so both places are actually searched.
    assert len(queries) == 2


def test_the_linkedin_ceiling_covers_the_cross_product(db):
    """A ceiling below keyword × location does not trim evenly — queries are taken in key order, so
    the last locations simply never run and a user silently loses whole places."""
    from libs.settings import DEFAULT_SOURCE_QUERIES_PER_CYCLE

    # The founder's shape: 23 terms across 3 places.
    assert DEFAULT_SOURCE_QUERIES_PER_CYCLE["linkedin"] >= 23 * 3
