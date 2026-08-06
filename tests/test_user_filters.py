"""What one account will accept, before anything expensive runs.

**Two of these tests exist because the same rule was written twice and both copies were wrong.**
`services.notifier.eligibility` and the pre-score filter each matched excluded terms with
`term in haystack` — a plain substring test — and one live account excludes `"sr"`, which matched
*Israel* Tech and Di*sr*aeli Ltd and removed them from that feed with no signal to anyone
(ROADMAP 6av).

No database. These are decisions made from a config dict and two strings, and a test that skips
itself without Postgres is a test CI never runs.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from libs.tenant.filters import UserFilters


def config(**section) -> dict:
    return {"sections": [{"platform": "all", "keywords": ["engineer"], **section}]}


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
    """So the caller can log which word is emptying a feed. "dropped 400 for 'senior'" is a config
    problem somebody fixes in a minute; "dropped 400" is not."""
    assert UserFilters(exclude=("senior",)).excluded("Senior Engineer", None) == "senior"


def test_an_account_that_excluded_nothing_excludes_nothing():
    assert UserFilters().excluded("Senior Engineer", "Anything") is None


def test_only_the_title_and_company_are_searched():
    """Never the description. A posting that merely *mentions* a term in a benefits paragraph is not
    the job the user was excluding, and matching the body turned "no recruitment agencies" into a
    filter that removed most of a feed."""
    filters = UserFilters(exclude=("agency",))
    assert filters.excluded("Backend Engineer", "Acme") is None


# ── countries ─────────────────────────────────────────────────────────────────────────────────


def test_locations_are_read_from_sections_not_the_top_level():
    """**Where the first version of this looked, and found nothing for every real config.** The
    same shape as the union-keywords defect that had every board fetching its unfiltered feed while
    the platform behaved as though nobody had signed up (ROADMAP 6l)."""
    filters = UserFilters.from_config(config(locations=["europe", "united_kingdom"]))

    assert filters.countries is not None
    assert "GB" in filters.countries and "DE" in filters.countries
    assert "US" not in filters.countries


def test_naming_no_location_filters_no_country():
    """`None`, never an empty set — a user who named nowhere has not excluded everywhere, and the
    two must not look the same to the caller."""
    assert UserFilters.from_config(config()).countries is None
    assert UserFilters.from_config(None).countries is None


def test_the_widest_selection_across_sections_wins():
    """A posting cannot be attributed back to the section that found it, so the generous reading is
    the only one that cannot silently delete something the user asked for."""
    filters = UserFilters.from_config(
        {"sections": [{"platform": "linkedin", "keywords": ["a"], "locations": ["united_kingdom"]},
                      {"platform": "all", "keywords": ["b"], "locations": ["europe"]}]})

    assert "GB" in filters.countries and "DE" in filters.countries


# ── freshness ─────────────────────────────────────────────────────────────────────────────────


def test_the_window_is_the_widest_the_user_asked_for():
    filters = UserFilters.from_config(
        {"sections": [{"platform": "linkedin", "keywords": ["a"], "posted_within_hours": 6},
                      {"platform": "all", "keywords": ["b"], "posted_within_hours": 72}]})

    assert filters.max_age_hours == 72


def test_an_unanswered_window_falls_back_rather_than_to_zero():
    """Zero would filter everything, which is the failure this whole file is shaped around."""
    from libs.ops.health import DEFAULT_MAX_AGE_HOURS

    assert UserFilters.from_config(config()).max_age_hours == DEFAULT_MAX_AGE_HOURS
    assert UserFilters.from_config(None).max_age_hours == DEFAULT_MAX_AGE_HOURS


def test_fresh_since_is_the_window_behind_now():
    filters = UserFilters(max_age_hours=24)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    assert filters.fresh_since(now=now) == now - timedelta(hours=24)


def test_a_v1_config_is_migrated_before_it_is_read():
    """A v1 config keeps these at the top level. Without the migration a v1 user silently gets the
    defaults instead of their own answers — found live on 2026-07-30, where the account that asked
    for 72 hours and typed exclusions was getting 24 and none."""
    filters = UserFilters.from_config(
        {"keywords": ["engineer"], "posted_within_hours": 72, "exclude_keywords": ["senior"]})

    assert filters.max_age_hours == 72
    assert filters.excluded("Senior Engineer", None) == "senior"


# ── one definition of "candidate", for three callers (ROADMAP 6az) ────────────────────────────


def test_the_three_callers_all_use_the_shared_conditions():
    """**Three copies of one question, and fixing one made the others wrong.**

    `pending_feed` decides what to send, `pending_matches` decides whether to page an operator, and
    `score_lane` decides what to score. On 2026-08-06 the freshness rule was corrected in the first
    and not the other two, so within the hour the health page was counting 155 rows the notifier
    could not see and paging about every one of them.

    Pinned by grep rather than by behaviour on purpose: the failure is *a fourth copy appearing*,
    which no behavioural test of the existing three would notice.
    """
    from pathlib import Path

    for name in ("libs/ops/health.py",
                 "services/notifier/deliver.py",
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

    conditions = candidate_conditions(UserFilters.from_config(config(locations=["europe"])))

    assert len(conditions) == 3, "closed, fresh, in-country"
    rendered = " ".join(str(c) for c in conditions)
    assert "closed_at" in rendered
    assert "posted_at" in rendered and "first_seen_at" in rendered
    assert "country" in rendered


def test_naming_no_country_still_produces_a_usable_condition():
    """`true()` rather than a missing element, so every caller splats the same number of conditions
    and none of them has to branch."""
    from libs.tenant.filters import candidate_conditions

    conditions = candidate_conditions(UserFilters.from_config(config()))
    assert len(conditions) == 3


# ── the age window, against a real database ─────────────────────────────────────────────────────
#
# **These moved here from `test_ranking.py` on 2026-08-06**, with the filter itself. The window used
# to be tested per item at delivery; it is now a SQL condition applied before anything is scored, so
# the only test that proves it is one that runs the SQL.

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

    row = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer",
                    **kwargs)
    db.add(row)
    db.flush()
    return row


def _survivors(db, filters):
    from sqlalchemy import select

    from libs.db.models import CorpusJob
    from libs.tenant.filters import candidate_conditions

    return set(db.scalars(select(CorpusJob.id).where(*candidate_conditions(filters))).all())


def test_a_posting_older_than_the_account_asked_for_never_reaches_the_model(db):
    """**The harvester fetches at the widest window any account asked for**, because the corpus is
    shared and one fetch serves everyone. Without this a user who asked for six hours was being sent
    whatever the most patient account on the platform wanted."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fresh = _row(db, posted_at=now - timedelta(hours=2))
    stale = _row(db, posted_at=now - timedelta(hours=30))

    kept = _survivors(db, UserFilters.from_config(config(posted_within_hours=6)))
    assert fresh.id in kept
    assert stale.id not in kept


def test_an_undated_posting_is_aged_from_when_we_found_it(db):
    """Most sources report no posted time. **When we found it is a weaker answer than when it was
    posted, but it is a real one** — and admitting undated rows unconditionally is what let an
    account accumulate rows that were re-judged every cycle forever."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    recent = _row(db, posted_at=None)
    old = _row(db, posted_at=None)
    old.first_seen_at = now - timedelta(hours=40)
    db.flush()

    kept = _survivors(db, UserFilters.from_config(config(posted_within_hours=6)))
    assert recent.id in kept, "found a moment ago, so there is nothing to drop it on"
    assert old.id not in kept, "found forty hours ago and still undated, so it is not fresh"
