"""What one account will accept, before anything expensive runs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from libs.tenant.filters import UserFilters


def config(**section) -> dict:
    return {"sections": [{"platform": "all", "keywords": ["engineer"], **section}]}


# The operator's `job_max_age_hours`, passed by every caller. Not a default this module owns.
WINDOW = 24


def built(config_: dict | None, *, max_age_hours: int = WINDOW, **kwargs) -> UserFilters:
    return UserFilters.from_config(config_, max_age_hours=max_age_hours, **kwargs)


# ── excluded terms ────────────────────────────────────────────────────────────────────────────


def test_a_short_exclusion_matches_the_word_and_not_the_inside_of_another():
    """**The live defect.** `"sr"` is a reasonable thing to type and a disastrous substring."""
    filters = UserFilters(exclude=("sr",))

    assert filters.excluded("Sr Backend Engineer", "Acme") == "sr"
    assert filters.excluded("Engineer", "Sr. Consulting") == "sr"
    assert filters.excluded("Backend Engineer", "Israel Tech") is None
    assert filters.excluded("Engineer", "Disraeli Ltd") is None
    assert filters.excluded("SRE Platform Engineer", "Acme") is None


def test_a_multi_word_exclusion_still_works():
    """Word boundaries must not break the ordinary case — a recruiter's name is several words."""
    filters = UserFilters(exclude=("quick hire staffing", "jack & jill"))

    assert filters.excluded("Engineer", "Quick Hire Staffing") == "quick hire staffing"
    assert filters.excluded("Engineer", "Jack & Jill Ltd") == "jack & jill"
    assert filters.excluded("Engineer", "Quick Hire") is None


def test_the_term_is_returned_rather_than_a_boolean():
    """So the caller can log which word is emptying a feed."""
    assert UserFilters(exclude=("senior",)).excluded("Senior Engineer", None) == "senior"


def test_an_account_that_excluded_nothing_excludes_nothing():
    assert UserFilters().excluded("Senior Engineer", "Anything") is None


def test_only_the_title_and_company_are_searched():
    """Never the description."""
    filters = UserFilters(exclude=("agency",))
    assert filters.excluded("Backend Engineer", "Acme") is None


# ── countries ─────────────────────────────────────────────────────────────────────────────────


def test_locations_are_read_from_sections_not_the_top_level():
    """Where the first version of this looked, and found nothing for every real config."""
    filters = built(config(locations=["europe", "united_kingdom"]))

    assert filters.countries is not None
    assert "GB" in filters.countries and "DE" in filters.countries
    assert "US" not in filters.countries


def test_naming_no_location_filters_no_country():
    """`None`, never an empty set — a user who named nowhere has not excluded everywhere."""
    assert built(config()).countries is None
    assert built(None).countries is None


def test_the_widest_selection_across_sections_wins():
    """A posting cannot be attributed back to the section that found it."""
    filters = built(
        {"sections": [{"platform": "linkedin", "keywords": ["a"], "locations": ["united_kingdom"]},
                      {"platform": "all", "keywords": ["b"], "locations": ["europe"]}]})

    assert "GB" in filters.countries and "DE" in filters.countries


# ── freshness ─────────────────────────────────────────────────────────────────────────────────


def test_the_window_is_the_operators_and_arrives_from_the_caller():
    """`posted_within_hours` left the stored config on 2026-08-01; the window is platform-wide now."""
    assert built(config(), max_age_hours=6).max_age_hours == 6
    assert built(None, max_age_hours=72).max_age_hours == 72


def test_the_filter_never_invents_a_window_of_its_own():
    """A default here is a number no operator can reach, and this one decides what is sendable."""
    from libs.settings import DEFAULTS

    assert "job_max_age_hours" in DEFAULTS, "the window an operator sets, and the only one"
    with pytest.raises(TypeError):
        UserFilters.from_config(config())


def test_fresh_since_is_the_window_behind_now():
    filters = UserFilters(max_age_hours=24)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    assert filters.fresh_since(now=now) == now - timedelta(hours=24)


def test_a_v1_config_is_migrated_before_it_is_read():
    """A v1 config keeps these at the top level."""
    filters = built({"keywords": ["engineer"], "exclude_keywords": ["senior"]}, max_age_hours=72)

    assert filters.max_age_hours == 72
    assert filters.excluded("Senior Engineer", None) == "senior"


# ── one definition of "candidate", for three callers (ROADMAP 6az) ────────────────────────────


def test_every_caller_that_still_asks_uses_the_shared_conditions():
    """Delivery stopped asking on 2026-08-07: it sends what scoring admitted, and judges nothing."""
    from pathlib import Path

    assert "candidate_conditions" not in Path(
        "services/notifier/deliver.py").read_text(encoding="utf-8"), (
        "delivery must not filter — the pre-score filter is the one place that decides")

    for name in ("libs/ops/health.py",
                 "libs/ops/pipeline.py",
                 "services/scorer/score_lane.py"):
        body = Path(name).read_text(encoding="utf-8")
        assert "candidate_conditions" in body, (
            f"{name} must ask `libs.tenant.filters.candidate_conditions` rather than spell the "
            "conditions out again")
        assert "posted_at >= " not in body.replace("candidate_conditions", ""), (
            f"{name} still has its own freshness comparison")


def test_the_conditions_are_the_ones_the_callers_need():
    """The shape, so a caller can splat them into a `where`."""
    from libs.tenant.filters import candidate_conditions

    conditions = candidate_conditions(built(config(locations=["europe"])))

    assert len(conditions) == 4, "closed, fresh, in-country, proxied-source"
    rendered = " ".join(str(c) for c in conditions)
    assert "closed_at" in rendered
    assert "posted_at" in rendered and "first_seen_at" in rendered
    assert "country" in rendered


def test_naming_no_country_still_produces_a_usable_condition():
    """`true()` rather than a missing element."""
    from libs.tenant.filters import candidate_conditions

    conditions = candidate_conditions(built(config()))
    assert len(conditions) == 4


# ── the age window, against a real database ─────────────────────────────────────────────────────

@pytest.fixture()
def db():
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set (no live Postgres)")
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


def _row(db, **kwargs):
    import uuid

    from libs.db.models import CorpusJob

    kwargs.setdefault("source", "test")
    row = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer", **kwargs)
    db.add(row)
    db.flush()
    return row


def _survivors(db, filters):
    from sqlalchemy import select

    from libs.db.models import CorpusJob
    from libs.tenant.filters import candidate_conditions

    return set(db.scalars(select(CorpusJob.id).where(*candidate_conditions(filters))).all())


def test_a_posting_older_than_the_account_asked_for_never_reaches_the_model(db):
    """The harvester fetches at the widest window any account asked for."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fresh = _row(db, posted_at=now - timedelta(hours=2))
    stale = _row(db, posted_at=now - timedelta(hours=30))

    kept = _survivors(db, built(config(), max_age_hours=6))
    assert fresh.id in kept
    assert stale.id not in kept


def test_an_undated_posting_is_aged_from_when_we_found_it(db):
    """Most sources report no posted time."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    recent = _row(db, posted_at=None)
    old = _row(db, posted_at=None)
    old.first_seen_at = now - timedelta(hours=40)
    db.flush()

    kept = _survivors(db, built(config(), max_age_hours=6))
    assert recent.id in kept, "found a moment ago, so there is nothing to drop it on"
    assert old.id not in kept, "found forty hours ago and still undated, so it is not fresh"


# ── sources somebody else's proxy paid for ────────────────────────────────────────────────────

def test_an_account_with_no_proxy_is_not_scored_on_proxied_postings(db):
    """The leak this rule exists for.

    LinkedIn is fetched per account on that account's own residential proxy, and the harvester
    refuses to run it at all for an account with no proxy credential. The corpus is shared, so
    without a matching rule here every posting one account paid to fetch was scored for everybody.
    Measured in production on 2026-08-08: two accounts, 24,902 LinkedIn rows scored each, one
    proxy between them.
    """
    linkedin = _row(db, source="linkedin")
    open_board = _row(db, source="himalayas")

    kept = _survivors(db, built(config(), proxied_sources=False))

    assert open_board.id in kept
    assert linkedin.id not in kept


def test_an_account_with_a_proxy_still_sees_them(db):
    """The rule is about who pays, not about disliking a source."""
    linkedin = _row(db, source="linkedin")

    assert linkedin.id in _survivors(db, built(config(), proxied_sources=True))


def test_a_proxied_posting_already_scored_is_still_not_delivered():
    """`candidate_conditions` keeps them out of new scoring passes; this keeps the rows that were
    scored while the account still had a proxy from being sent after it stopped paying."""
    filters = built(config(), proxied_sources=False)

    assert filters.unwanted({"source": "linkedin", "title": "Engineer"}) == "proxied_source"
    assert filters.unwanted({"source": "himalayas", "title": "Engineer"}) is None


def test_which_sources_are_proxied_is_defined_once():
    """The harvest gate and the scoring gate must not disagree about the list."""
    from libs.settings import PROXIED_SOURCES

    assert "linkedin" in PROXIED_SOURCES
