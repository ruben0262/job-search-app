"""The relabeller: what it selects, how it maps scores back, and what it costs.

The Haiku call is faked. What matters here is the bookkeeping around it, because every mistake in
that bookkeeping produces a *plausible* training set rather than an error:

- a score attached to the wrong job by position instead of by URL;
- a batch that silently comes back short;
- relabelling jobs the user never had a label for, which spends their money on nothing;
- a continuation that collides with its own dedup key and stops the chain half-way, looking
  exactly like success.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from services.labeller.label import BATCH_SIZE, DESC_CHARS, score_batch, stale_job_ids, write_labels

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


class FakeClient:
    """Scores every job it is shown, or returns exactly what it was configured with."""

    def __init__(self, scores: list[dict] | None = None):
        self.scores = scores
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.scores is not None:
            return {"scores": self.scores}
        # Otherwise behave like a well-behaved model: one score per URL it was shown, echoed back
        # exactly as given.
        import re

        urls = re.findall(r'"url": "([^"]+)"', kwargs["messages"][0]["content"])
        return {"scores": [{"url": url, "score": 70} for url in urls]}


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
                profile_generation=2, profile_version="abc12345",
                rubric_md="RUBRIC\n\nJOBS:\n<JOBS_JSON>")
    session.add(user)
    session.flush()

    jobs = []
    for n in range(3):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title=f"Backend Engineer {n}", description="x" * 5_000)
        session.add(job)
        jobs.append(job)
    session.flush()

    try:
        yield session, user, jobs
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _label(session, user, job, generation):
    from libs.db.models import UserLabel

    session.add(UserLabel(user_id=user.id, job_id=job.id, label=50, source="llm",
                          profile_generation=generation))
    session.flush()


# ── selection ─────────────────────────────────────────────────────────────────────────────────

def test_only_jobs_labelled_under_an_older_rubric_are_selected(db):
    """A label written under an old rubric answers a different question. One written under the
    current one is fine and re-scoring it would be the user paying twice for the same answer."""
    session, user, jobs = db
    _label(session, user, jobs[0], generation=1)
    _label(session, user, jobs[1], generation=2)

    assert stale_job_ids(session, user.id) == [jobs[0].id]


def test_jobs_the_user_never_labelled_are_not_relabelled(db):
    """Labelling something new is cold start, which selects a pool deliberately. Sweeping the whole
    corpus in here would quietly spend a user's money on jobs nobody asked about."""
    session, user, jobs = db
    _label(session, user, jobs[0], generation=1)

    selected = stale_job_ids(session, user.id)
    assert jobs[1].id not in selected and jobs[2].id not in selected


def test_the_selection_is_capped_at_one_batch(db):
    """One queue job is one Haiku call, so an interrupted worker throws away one call rather than
    four."""
    session, user, jobs = db
    for job in jobs:
        _label(session, user, job, generation=1)

    assert len(stale_job_ids(session, user.id, limit=BATCH_SIZE)) <= BATCH_SIZE


# ── the call ──────────────────────────────────────────────────────────────────────────────────

def test_descriptions_are_capped_before_they_are_sent(db):
    """A straight multiplier on the cost of every label. Requirements and stack sit in the first
    couple of thousand characters; the tail is benefits boilerplate."""
    _, _, jobs = db
    client = FakeClient()
    score_batch(client, "JOBS:\n<JOBS_JSON>", jobs)

    prompt = client.calls[0]["messages"][0]["content"]
    assert "x" * DESC_CHARS in prompt
    assert "x" * (DESC_CHARS + 1) not in prompt


def test_the_rubric_is_sent_verbatim_with_the_jobs_substituted(db):
    """`profile_version` is the rubric's hash. If the labeller edited the rubric on the way out, the
    labels would be scoped to a version nothing else can reproduce."""
    _, user, jobs = db
    client = FakeClient()
    score_batch(client, user.rubric_md, jobs)

    prompt = client.calls[0]["messages"][0]["content"]
    assert prompt.startswith("RUBRIC")
    assert "<JOBS_JSON>" not in prompt


def test_a_short_batch_is_not_treated_as_a_full_one(db, caplog):
    """A model that returns 2 of 3 is a truncation problem. Silently accepting it looks like slow
    progress rather than a bug."""
    _, _, jobs = db
    scored = score_batch(FakeClient([{"url": jobs[0].url, "score": 80}]), "JOBS:\n<JOBS_JSON>", jobs)

    assert len(scored) == 1
    assert any("returned 1 scores for 3 jobs" in record.getMessage()
               for record in caplog.records if record.levelname == "WARNING")


# ── writing back ──────────────────────────────────────────────────────────────────────────────

def test_scores_are_matched_by_url_not_by_position(db):
    """A model that reorders its output would otherwise shift every label onto the wrong job — a
    training set that looks fine and teaches the opposite of what was intended."""
    from libs.db.models import UserLabel

    session, user, jobs = db
    reordered = [{"url": jobs[2].url, "score": 95}, {"url": jobs[0].url, "score": 10}]

    write_labels(session, user.id, {s["url"]: s for s in reordered}, jobs)
    session.flush()

    assert session.get(UserLabel, (user.id, jobs[2].id)).label == 95
    assert session.get(UserLabel, (user.id, jobs[0].id)).label == 10
    assert session.get(UserLabel, (user.id, jobs[1].id)) is None


def test_a_url_that_was_not_in_the_batch_is_dropped(db):
    """The model invented or mangled it. Attaching the score to a guessed job is worse than having
    one fewer label."""
    session, user, jobs = db
    written = write_labels(session, user.id,
                           {"https://example.com/invented": {"url": "x", "score": 90}}, jobs)
    assert written == 0


def test_a_new_label_replaces_the_stale_one_at_the_current_generation(db):
    """Upserted on `(user_id, job_id)`, so the old-generation row becomes the new one rather than
    accumulating — which is also what makes "how many are left" a true count."""
    from libs.db.models import UserLabel

    session, user, jobs = db
    _label(session, user, jobs[0], generation=1)

    write_labels(session, user.id, {jobs[0].url: {"url": jobs[0].url, "score": 88}}, jobs)
    session.flush()

    row = session.get(UserLabel, (user.id, jobs[0].id))
    assert (row.label, row.profile_generation, row.profile_version) == (88, 2, "abc12345")
    assert stale_job_ids(session, user.id) == []


def test_relabelling_without_a_rubric_is_refused(db):
    """No rubric means no confirmed profile. Scoring against nothing would produce labels that look
    real and mean nothing."""
    from libs.llm import LlmError
    from services.labeller.label import relabel_user

    session, user, _ = db
    user.rubric_md = None
    session.flush()

    with pytest.raises(LlmError, match="no rubric"):
        relabel_user(session, user.id, FakeClient())


def test_a_full_run_reports_what_is_left(db):
    """`remaining` is what the worker re-enqueues on. If it were wrong the chain would either stop
    early or never stop."""
    from services.labeller.label import relabel_user

    session, user, jobs = db
    for job in jobs:
        _label(session, user, job, generation=1)

    written, remaining = relabel_user(session, user.id, FakeClient(), limit=2)
    assert written == 2
    assert remaining == 1
