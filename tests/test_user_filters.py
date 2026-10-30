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

    # The ops pages ask no candidate question any more: they counted scored matches (Phase 8).
    for name in ("libs/matching.py",):
        body = Path(name).read_text(encoding="utf-8")
        assert "candidate_conditions" in body, (
            f"{name} must ask `libs.tenant.filters.candidate_conditions` rather than spell the "
            "conditions out again")
        assert "posted_at >= " not in body.replace("candidate_conditions", ""), (
            f"{name} still has its own freshness comparison")


def test_a_closed_posting_is_never_a_candidate(db):
    """What the conditions are *for*, rather than how many of them there are.

    This used to assert `len(conditions) == 3`, which is a statement about the implementation and
    broke twice in one day for two correct changes. The behaviour each condition exists to produce
    is asserted directly instead, here and in the tests around it.
    """
    from datetime import UTC, datetime

    open_row = _row(db)
    closed = _row(db, closed_at=datetime.now(UTC))

    kept = _survivors(db, built(config()))
    assert open_row.id in kept
    assert closed.id not in kept


def test_a_posting_outside_the_wanted_countries_is_never_a_candidate(db):
    """And naming no country admits everywhere, rather than admitting nothing."""
    here = _row(db, country="DE")
    elsewhere = _row(db, country="US")

    wanted = _survivors(db, built(config(locations=["europe"])))
    assert here.id in wanted
    assert elsewhere.id not in wanted

    # No country named at all: an opinion nobody expressed must not filter everything out.
    assert {here.id, elsewhere.id} <= _survivors(db, built(config()))


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


# ── the corpus is shared, and every account reads all of it ───────────────────────────────────

def test_every_account_is_scored_on_the_whole_corpus_whoever_fetched_it(db):
    """No source is withheld from an account for how it was paid for.

    A read gate used to keep LinkedIn away from accounts with no proxy credential of their own, on
    the grounds that somebody else's proxy paid to fetch it. The owner's decision on 2026-08-09 is
    that the fetch cost is already sunk: the row is in the shared corpus, showing it to a second
    reader costs nothing further, and withholding it cost far more. It was 72% of the corpus, and
    the account it was withheld from had 8,918 rows to be scored against instead of 31,352.

    The split that remains is the one that actually spends money, and it lives in the harvester:
    an account with no proxy credential does not *cause* a LinkedIn fetch
    (`services/harvester/linkedin.py` asks the credential store directly). It just reads what is
    already there.
    """
    linkedin = _row(db, source="linkedin")
    open_board = _row(db, source="himalayas")

    # Through `filters_for`, because owning a proxy credential is what used to decide this and it
    # is a property of the account rather than of the config. Built by hand, the gate never fires.
    kept = _survivors(db, _filters_for_account_without_a_proxy(db))

    assert {linkedin.id, open_board.id} <= set(kept)


def test_no_source_is_a_reason_to_withhold_a_posting_already_scored(db):
    """The delivery-side half of the same rule.

    `candidate_conditions` keeps rows out of a fresh scoring pass; `unwanted` is what stops one
    already scored from being sent. Both used to reject on source, so both have to stop.
    """
    filters = _filters_for_account_without_a_proxy(db)

    assert filters.unwanted({"source": "linkedin", "title": "Engineer"}) is None
    assert filters.unwanted({"source": "himalayas", "title": "Engineer"}) is None


def _filters_for_account_without_a_proxy(db):
    """A real account with no `proxy` secret — the case the old gate silenced."""
    import uuid

    from libs.db.models import User
    from libs.tenant.filters import filters_for
    from libs.tenant.repository import TenantRepository

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                active=True)
    db.add(user)
    db.flush()
    TenantRepository(db).save_config(user.id, config())
    db.flush()
    return filters_for(db, user)


# ── freshness, per source and per timestamp quality ───────────────────────────────────────────

def test_a_slow_source_is_not_silenced_by_a_tight_global_window(db):
    """A window below what a source can serve switches it off rather than raising the bar.

    Measured on the live corpus 2026-08-09 with the operator's window at six hours: fifteen of
    seventeen sources returned nothing at all. `arbeitnow` held 2,691 open jobs and none inside 72
    hours — harvested every cycle and discarded in full.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    slow = _row(db, source="arbeitnow", posted_at=now - timedelta(hours=100))
    fast = _row(db, source="linkedin", posted_at=now - timedelta(hours=100))

    kept = _survivors(db, built(config(), max_age_hours=6))

    assert slow.id in kept, "arbeitnow's floor is a week; six hours must not delete it"
    assert fast.id not in kept, "linkedin sustains an hour, so 100 hours is genuinely stale"


def test_the_operator_can_widen_a_window_but_not_narrow_it_past_the_floor(db):
    """The global setting applies on top of each source's floor, never underneath it."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    recent_linkedin = _row(db, source="linkedin", posted_at=now - timedelta(hours=100))

    # A generous global admits it; a tight one does not, because linkedin's own floor is 1h.
    assert recent_linkedin.id in _survivors(db, built(config(), max_age_hours=168))
    assert recent_linkedin.id not in _survivors(db, built(config(), max_age_hours=1))


def test_a_date_only_posting_is_judged_on_when_we_found_it(db):
    """Some sources report a date rather than a time, so every posting reads as midnight.

    Workday reported one single time value across every row measured. Judged on `posted_at`, its
    whole catalogue looks half a day old by mid-morning however fresh it is. The harvest runs every
    fifteen minutes and polls every ATS token each cycle, so when we first saw a row is the better
    statement about its age.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Stamped midnight, but we watched the board and saw it appear minutes ago.
    row = _row(db, source="workday", posted_at=midnight_today)
    row.first_seen_at = now - timedelta(minutes=10)
    db.flush()

    assert row.id in _survivors(db, built(config(), max_age_hours=6)), (
        "a job we saw ten minutes ago is not stale because its source stamps dates")


def test_a_real_timestamp_is_still_judged_on_the_posting_time(db):
    """The date-only rule must not become a way for genuinely old jobs to slip through."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    old = _row(db, source="linkedin", posted_at=now - timedelta(hours=30))
    old.first_seen_at = now - timedelta(minutes=5)
    db.flush()

    assert old.id not in _survivors(db, built(config(), max_age_hours=6)), (
        "seeing an old posting recently does not make it fresh"
    )


def test_the_delivery_floor_is_the_operator_s_to_change(db):
    """It was a constant, beside a platform setting answering the adjacent question.

    `source_freshness_hours` says how far back a source is *asked* and has a panel control;
    `SOURCE_MIN_AGE_HOURS` said how long what it returned stays deliverable and had none. So an
    operator could widen a source's harvest window, watch rows arrive, and never learn that a floor
    they could not see was discarding them. Both are now set in the same place.
    """
    from datetime import UTC, datetime, timedelta

    from libs import settings
    from libs.db.models import User
    from libs.tenant.filters import filters_for
    from libs.tenant.repository import TenantRepository

    old = _row(db, source="greenhouse",
               posted_at=datetime.now(UTC) - timedelta(hours=100), first_seen_at=datetime.now(UTC))
    db.flush()

    user = User(email=f"{uuid_hex()}@example.test", password_hash="x", status="active", active=True)
    db.add(user)
    db.flush()
    TenantRepository(db).save_config(user.id, config())
    db.flush()

    # Measured default for greenhouse is 72 hours, so a 100-hour-old posting is out.
    assert old.id not in _survivors(db, filters_for(db, user))

    settings.put(db, "source_min_age_hours", {"greenhouse": 168})
    db.flush()

    assert old.id in _survivors(db, filters_for(db, user)), (
        "raising the floor in the panel must change what is actually delivered")


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex
