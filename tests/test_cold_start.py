"""Cold start: the rules-only score, pool selection, and what the label lane does next.

The rules score is the whole of stage 0 — what a user sees in the seconds after saving a config,
before any model exists. It is crude by construction, so the tests assert the things that would
make it *wrong* rather than merely rough: that an exclusion beats a keyword match, that a missing
location is not treated as a mismatch, and that `full-stack` matches "Full Stack".

Pool selection is where a user's money goes. A pool drawn from the head of the ranking is 120
near-identical jobs, which is 120 labels' worth of spend for one label's worth of information — so
the striding and the banding are asserted, not assumed.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import select

from libs.scoring.rules import DESCRIPTION_WEIGHT, TITLE_WEIGHT, score_job

CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer", "full-stack"],
                        "exclude_keywords": ["unpaid"], "locations": ["remote_eu"],
                        "remote_policy": "remote_or_hybrid"}]}


# ── stage 0: the rules-only score ─────────────────────────────────────────────────────────────

def test_a_keyword_in_the_title_outranks_the_same_word_in_the_body():
    """"Backend Engineer" as the title is what the job *is*. In the description it is usually "you
    will work with our backend engineers", which is a different job."""
    titled = score_job(CONFIG, title="Backend Engineer", description="We build things")
    buried = score_job(CONFIG, title="Product Manager",
                       description="You will work with our backend engineer team")
    assert titled.score > buried.score


def test_a_hyphenated_keyword_matches_its_spaced_spelling():
    """The bug that shipped in the reference instance for months: `full-stack` matched nothing in
    "Full Stack Developer" and the feature silently read 0."""
    assert not score_job(CONFIG, title="Full Stack Developer").excluded


def test_an_exclusion_beats_a_keyword_match():
    """The user typed that term specifically to not see it. A strong title match must not outvote
    it."""
    result = score_job(CONFIG, title="Backend Engineer", description="This is an unpaid internship")
    assert result.excluded
    assert "unpaid" in result.reason


def test_a_job_with_none_of_the_keywords_is_excluded_with_a_reason():
    """"Why is this not in my feed?" has to be answerable, or people stop trusting the feed."""
    result = score_job(CONFIG, title="Registered Nurse")
    assert result.excluded
    assert "keywords" in result.reason


def test_remote_only_excludes_a_non_remote_job():
    config = {"sections": [{"keywords": ["engineer"], "remote_policy": "remote_only"}]}
    assert score_job(config, title="Engineer", is_remote=False).excluded
    assert not score_job(config, title="Engineer", is_remote=True).excluded


def test_a_missing_location_is_not_treated_as_a_mismatch():
    """Plenty of postings have no usable location string. Scoring those to zero buries them under
    worse jobs that happened to name a city."""
    unknown = score_job(CONFIG, title="Backend Engineer", location=None)
    wrong = score_job(CONFIG, title="Backend Engineer", location="Sydney, Australia")
    assert unknown.score > wrong.score


def test_a_remote_job_satisfies_a_remote_region():
    """`remote_eu` is a delivery region, not a place. A remote posting satisfies it without its
    location string having to say so."""
    remote = score_job(CONFIG, title="Backend Engineer", is_remote=True, location="Anywhere")
    assert remote.score == TITLE_WEIGHT + 25


def test_a_job_matching_any_section_is_included():
    """Sections express "different terms on LinkedIn than on the boards". They narrow the search,
    not the results."""
    config = {"sections": [{"platform": "linkedin", "keywords": ["quant researcher"]},
                           {"platform": "indeed", "keywords": ["backend engineer"]}]}
    assert not score_job(config, title="Backend Engineer").excluded


def test_the_score_never_exceeds_one_hundred():
    result = score_job(CONFIG, title="Backend Engineer",
                       description="backend engineer, full-stack", is_remote=True)
    assert result.score <= 100
    assert result.score >= TITLE_WEIGHT + DESCRIPTION_WEIGHT


# ── pool selection ────────────────────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, User

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                profile_generation=1, profile_version="abc12345", rubric_md="R\nJOBS:\n<JOBS_JSON>")
    session.add(user)
    session.flush()

    # A spread: strong title matches, description-only matches, and jobs that only scrape in.
    for n in range(60):
        title = "Backend Engineer" if n % 3 == 0 else f"Analyst {n}"
        description = "backend engineer wanted" if n % 3 == 1 else "unrelated work"
        session.add(CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source=f"src{n % 3}",
                              title=title, description=description, is_remote=n % 2 == 0))
    session.flush()

    try:
        yield session, user
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytestmark_db
def test_the_pool_spans_the_score_range_rather_than_the_top_of_it(db):
    """A pool drawn from the head of the ranking is near-identical jobs — a lot of money for one
    job's worth of information. The boundary is where a classifier learns."""
    from services.labeller.pool import select_pool

    session, user = db
    ids = select_pool(session, user.id, CONFIG, size=30)

    from libs.db.models import CorpusJob

    jobs = {job.id: job for job in
            session.scalars(select(CorpusJob).where(CorpusJob.id.in_(ids)))}
    assert len(ids) == 30
    scores = {score_job(CONFIG, title=jobs[i].title, description=jobs[i].description,
                        is_remote=jobs[i].is_remote).score for i in ids}
    assert len(scores) > 1, "every selected job scored the same — the bands are not being used"


@pytestmark_db
def test_jobs_the_user_has_already_labelled_are_not_bought_twice(db):
    from libs.db.models import CorpusJob, UserLabel
    from services.labeller.pool import select_pool

    session, user = db
    first = select_pool(session, user.id, CONFIG, size=5)
    for job_id in first:
        session.add(UserLabel(user_id=user.id, job_id=job_id, label=70, profile_generation=1))
    session.flush()

    assert not set(select_pool(session, user.id, CONFIG, size=20)) & set(first)
    assert session.get(CorpusJob, first[0]) is not None


@pytestmark_db
def test_a_config_matching_nothing_yields_an_empty_pool_rather_than_noise(db):
    """The `<150 jobs` failure mode. Returning arbitrary jobs would spend the user's money teaching
    the model about roles they never asked for."""
    from services.labeller.pool import select_pool

    session, user = db
    narrow = {"sections": [{"keywords": ["underwater basket weaving"]}]}
    assert select_pool(session, user.id, narrow) == []


# ── what the lane does next ───────────────────────────────────────────────────────────────────

@pytestmark_db
def test_a_confirmed_profile_with_no_labels_starts_cold(db):
    from services.labeller.coldstart import labelling_state

    session, user = db
    assert labelling_state(session, user.id)["action"] == "cold_start"


@pytestmark_db
def test_stale_labels_are_carried_forward_before_new_ones_are_bought(db):
    """Jobs the user already cared about are cheaper per unit of signal than opinions on new ones."""
    from libs.db.models import CorpusJob, UserLabel
    from services.labeller.coldstart import labelling_state

    session, user = db
    job = session.scalars(select(CorpusJob).limit(1)).first()
    session.add(UserLabel(user_id=user.id, job_id=job.id, label=70, profile_generation=0))
    user.profile_generation = 1
    session.flush()

    assert labelling_state(session, user.id)["action"] == "relabel"


@pytestmark_db
def test_an_account_without_a_rubric_is_sent_back_to_confirm(db):
    """Scoring against no rubric produces labels that look real and mean nothing."""
    from services.labeller.coldstart import labelling_state

    session, user = db
    user.rubric_md = None
    session.flush()

    assert labelling_state(session, user.id)["action"] == "confirm_profile"


@pytestmark_db
def test_a_partial_cold_start_still_trains(db):
    """A user with 280 labels and no model because the last 20 calls failed is the worst outcome
    available (`COLD_START_PLAN.md` §7)."""
    from libs.db.models import CorpusJob, UserLabel
    from services.labeller.coldstart import MIN_LABELS_TO_TRAIN, ready_to_train

    session, user = db
    assert ready_to_train(session, user.id) is False

    jobs = list(session.scalars(select(CorpusJob).limit(MIN_LABELS_TO_TRAIN)))
    for job in jobs:
        session.add(UserLabel(user_id=user.id, job_id=job.id, label=70, profile_generation=1))
    session.flush()

    # 60 corpus rows in the fixture, so this asserts the threshold is *checked*, not that it passes.
    assert ready_to_train(session, user.id) is (len(jobs) >= MIN_LABELS_TO_TRAIN)


@pytest.mark.skipif("DATABASE_URL" not in __import__("os").environ, reason="needs live Postgres")
def test_a_user_is_not_crowded_out_of_a_shared_corpus(db):
    """**The bug that stalled a live cold start at ten labels.**

    The candidate window was the newest rows on the platform, filtered against the config only
    afterwards. The corpus is shared, so whoever harvests most owns recency: an account searching
    for mechanical engineering roles had matching rows sitting in the corpus and saw none of them,
    because thousands of fresher software postings stood in front of them. It labelled ten jobs,
    found nothing more, and stopped at a tenth of what training needs.
    """
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob
    from services.labeller.pool import MAX_CANDIDATES, select_pool

    session, user = db
    now = datetime.now(UTC)
    # More fresh noise than the candidate budget, so the wanted rows cannot fit inside it.
    for i in range(MAX_CANDIDATES + 100):
        session.add(CorpusJob(
            content_key=f"noise-{i}", title="Senior Software Engineer", company="N",
            location="Remote", url=f"https://example.com/n{i}", source="test", is_remote=True,
            description="python go kubernetes", first_seen_at=now - timedelta(minutes=i)))
    # The user's own matches, older than every single one of them.
    for i in range(40):
        session.add(CorpusJob(
            content_key=f"wanted-{i}", title="Graduate Mechanical Engineer", company="M",
            location="United Kingdom", url=f"https://example.com/m{i}", source="test",
            is_remote=False, description="solidworks fea design",
            first_seen_at=now - timedelta(days=30, minutes=i)))
    session.flush()

    config = {"sections": [{"keywords": ["mechanical engineer"],
                            "locations": ["united_kingdom"], "platform": "all"}]}
    chosen = select_pool(session, user.id, config, size=50)
    titles = {row.title for row in
              session.query(CorpusJob).filter(CorpusJob.id.in_(chosen)).all()}

    assert any("Mechanical" in title for title in titles), (
        "a user's own matches must survive a corpus dominated by somebody else's search")
