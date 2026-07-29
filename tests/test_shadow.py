"""Shadow scoring — the sampling, and what a pair means.

**The sampling is the part that has to be right.** It decides what the user pays for, and two
properties are non-negotiable: the same pair is drawn every time (so a queue replay does not
re-spend), and every process draws the same sample (so a job is not shadowed twice by two workers).

The second one is easy to get wrong in a way tests catch and review does not: Python's built-in
`hash()` is salted per interpreter, so a sampler built on it would be stable within one process and
different in the next.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import uuid

import pytest

from libs.scoring.shadow import DEFAULT_SAMPLE_RATE, agrees, is_sampled

USER = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ── sampling ──────────────────────────────────────────────────────────────────────────────────

def test_the_same_pair_is_always_sampled_the_same_way():
    """A queue replay must not draw a new sample — the bill would grow with the retries."""
    drawn = [is_sampled(USER, 1234) for _ in range(50)]
    assert len(set(drawn)) == 1


def test_the_sample_is_stable_across_processes():
    """**The bug this test exists for.** `hash()` is salted per interpreter, so a sampler built on
    it agrees with itself and with nothing else. A fresh process must draw the same sample."""
    code = ("import uuid;from libs.scoring.shadow import is_sampled;"
            f"print([is_sampled(uuid.UUID('{USER}'), n) for n in range(40)])")
    first = subprocess.run([sys.executable, "-c", code],   # noqa: S603 - the code is right above
                           capture_output=True, text=True, check=True)
    second = subprocess.run([sys.executable, "-c", code],  # noqa: S603
                            capture_output=True, text=True, check=True)

    assert first.stdout == second.stdout
    assert [is_sampled(USER, n) for n in range(40)] == ast.literal_eval(first.stdout)


def test_roughly_the_requested_percentage_is_drawn():
    """Not exact — it is a hash, not a quota — but a rate that produced 2% or 40% would be a bug
    nobody would notice except in the bill."""
    drawn = sum(is_sampled(USER, n, rate=DEFAULT_SAMPLE_RATE) for n in range(5_000))
    assert 350 <= drawn <= 650          # 10% of 5,000, with room for hash variance


def test_two_users_do_not_get_the_same_sample():
    """The user id is in the digest, so one user's shadowed jobs are not another's. Otherwise every
    account would pay to grade the same postings."""
    other = uuid.UUID("99999999-8888-7777-6666-555555555555")
    mine = {n for n in range(2_000) if is_sampled(USER, n)}
    theirs = {n for n in range(2_000) if is_sampled(other, n)}
    assert mine != theirs
    assert len(mine & theirs) < len(mine)


@pytest.mark.parametrize("rate", [0, -5])
def test_a_rate_of_zero_disables_sampling(rate):
    assert not any(is_sampled(USER, n, rate=rate) for n in range(200))


def test_a_rate_of_a_hundred_shadows_everything():
    assert all(is_sampled(USER, n, rate=100) for n in range(200))


# ── agreement ─────────────────────────────────────────────────────────────────────────────────

def test_scores_within_tolerance_agree():
    assert agrees(70, 80) is True


def test_scores_further_apart_disagree():
    assert agrees(40, 90) is False


def test_a_missing_score_is_neither_agreement_nor_disagreement():
    """"We never asked" and "they disagreed" are different facts. Collapsing them would make a
    quiet outage look like a drifting model."""
    assert agrees(None, 80) is None
    assert agrees(80, None) is None


def test_the_tolerance_is_adjustable_without_touching_the_caller():
    assert agrees(70, 80, tolerance=5) is False


# ── the lane, against a live database ─────────────────────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


class FakeJudge:
    """Scores every job it is shown at a fixed number, and records what it was asked."""

    def __init__(self, score: int = 70):
        self.score = score
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> dict:
        import re
        self.calls.append(kwargs)
        urls = re.findall(r'"url": "([^"]+)"', kwargs["messages"][0]["content"])
        return {"scores": [{"url": url, "score": self.score, "fit_notes": "a backend role"}
                           for url in urls]}


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, User, UserJobState

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                model_version=3, profile_generation=2, profile_version="abc12345",
                rubric_md="RUBRIC\n\nJOBS:\n<JOBS_JSON>")
    session.add(user)
    session.flush()

    jobs = []
    for n in range(40):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title=f"Backend Engineer {n}", description="a real description " * 40)
        session.add(job)
        session.flush()
        session.add(UserJobState(user_id=user.id, job_id=job.id, score=68.0,
                                 scored_model_version=3))
        jobs.append(job)
    session.flush()

    try:
        yield session, user, jobs
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_only_the_sampled_jobs_are_compared(db):
    """The user pays per job. Shadowing everything scored would be ten times the bill."""
    from services.labeller.shadow_lane import candidates

    session, user, jobs = db
    selected = candidates(session, user.id, limit=100)

    # Not "some were selected": the sample is a digest of ids from an ever-advancing sequence, so
    # a run where none of these forty jobs happens to be drawn is legitimate and is not a bug. What
    # must always hold is that nothing outside the sample is ever graded, and that asking for
    # everything gets everything.
    assert len(selected) < len(jobs)
    assert all(is_sampled(user.id, job_id) for job_id in selected)
    assert len(candidates(session, user.id, limit=100, rate=100)) == len(jobs)


def test_a_job_with_no_real_description_is_never_shadowed(db):
    """The judge would be grading a title while the model scored an embedding of the same title.
    They would agree for reasons that say nothing about drift."""
    from services.labeller.shadow_lane import candidates

    session, user, jobs = db
    for job in jobs:
        job.description = "too short"
    session.flush()

    assert candidates(session, user.id, limit=100) == []


def test_a_pair_records_both_opinions_and_whether_they_agreed(db):
    from libs.db.models import ShadowScore
    from services.labeller.shadow_lane import run_shadow

    session, user, _ = db
    compared, disagreements = run_shadow(session, user.id, FakeJudge(70), rate=100)

    assert compared > 0 and disagreements == 0
    pair = session.query(ShadowScore).filter_by(user_id=user.id).first()
    assert (pair.model_score, pair.judge_score, pair.agreed) == (68.0, 70.0, True)
    assert pair.model_version == 3 and pair.profile_generation == 2


def test_a_judge_that_disagrees_is_recorded_as_such(db):
    from services.labeller.shadow_lane import run_shadow

    session, user, _ = db
    compared, disagreements = run_shadow(session, user.id, FakeJudge(10), rate=100)
    assert disagreements == compared


def test_a_shadow_run_also_produces_labels(db):
    """The judge's opinion *is* the training signal here, so discarding it would mean paying for
    the same answer twice."""
    from libs.db.models import UserLabel
    from services.labeller.shadow_lane import run_shadow

    session, user, _ = db
    compared, _ = run_shadow(session, user.id, FakeJudge(70), rate=100)

    labels = session.query(UserLabel).filter_by(user_id=user.id).count()
    assert labels == compared


def test_a_second_run_does_not_re_ask_about_the_same_pair(db):
    """A replayed queue job must not re-spend. The sampling is deterministic, so without this the
    same jobs would be re-graded on every run."""
    from services.labeller.shadow_lane import run_shadow

    session, user, jobs = db
    first, _ = run_shadow(session, user.id, FakeJudge(70), rate=100, limit=len(jobs))
    second, _ = run_shadow(session, user.id, FakeJudge(70), rate=100, limit=len(jobs))

    assert first == len(jobs) and second == 0


def test_the_agreement_rate_is_scoped_to_one_model_version(db):
    """A pair collected before a retrain says nothing about the model serving now. Averaging across
    versions would smear a fixed problem over the fix."""
    from services.labeller.shadow_lane import agreement_rate, run_shadow

    session, user, _ = db
    run_shadow(session, user.id, FakeJudge(70), rate=100)
    session.flush()

    pairs, rate = agreement_rate(session, user.id, model_version=3)
    assert pairs > 0 and rate == 1.0
    assert agreement_rate(session, user.id, model_version=99) == (0, None)
