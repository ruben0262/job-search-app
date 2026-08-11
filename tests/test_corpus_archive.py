"""Retention: what leaves the corpus, what never does, and that it can be read back.

The failures these are written against are all silent ones. Losing a posting raises nothing, losing
a training label raises nothing, and an archive file that cannot be parsed looks exactly like a
successful archive until somebody needs it.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from libs.corpus.archive import archive_expired, forget_stale_state, read_archive
from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.tenant.repository import TenantRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield CorpusRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _u() -> str:
    return f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}"


def _user(s) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC",
             status="active", active=True)
    s.add(u)
    s.flush()
    return u


def _aged(r, s, *, days: int, description: str = "Go, gRPC, Postgres. " * 20) -> int:
    """A described posting whose `first_seen_at` is `days` old."""
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer",
                   company=f"Acme{uuid.uuid4().hex[:8]}")
    s.flush()
    r.set_description(job_id, description)
    s.flush()
    s.get(CorpusJob, job_id).first_seen_at = datetime.now(UTC) - timedelta(days=days)
    s.flush()
    return job_id


# ── what leaves ───────────────────────────────────────────────────────────────────────────────

def test_a_posting_past_the_window_leaves_the_corpus_whole(ctx, tmp_path):
    """The row is deleted, not hollowed out — and everything about it is in the file first."""
    r, s = ctx
    job_id = _aged(r, s, days=45)
    url = s.get(CorpusJob, job_id).url

    result = archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert result.jobs >= 1
    assert s.get(CorpusJob, job_id) is None, "the row must be gone, not nulled in place"

    written = {row["url"]: row for row in read_archive(result.path)}
    assert url in written, "the posting was deleted without being archived"
    assert written[url]["description"], "the description is the part worth keeping"
    assert written[url]["title"] == "Backend Engineer"


def test_the_archive_keeps_the_text_and_drops_only_the_vector(ctx, tmp_path):
    """The vector is the bulk of the size and is reproducible from the text. Nothing else goes."""
    r, s = ctx
    job_id = _aged(r, s, days=45)
    columns = {c.key for c in CorpusJob.__table__.columns}

    result = archive_expired(s, directory=tmp_path)
    s.flush()
    assert job_id and result.jobs >= 1

    archived = read_archive(result.path)[0]
    assert "embedding" not in archived
    # Every other column, so a column added to the model cannot be dropped here unnoticed.
    assert columns - {"embedding"} <= set(archived), (
        f"these columns are not archived: {sorted(columns - {'embedding'} - set(archived))}")


def test_a_fresh_posting_is_left_alone(ctx, tmp_path):
    r, s = ctx
    job_id = _aged(r, s, days=3)

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id) is not None


# ── what never leaves ─────────────────────────────────────────────────────────────────────────

def test_a_labelled_posting_is_never_archived(ctx, tmp_path):
    """**The defect this replaced.** A label whose posting is gone is a corrupted training set.

    `trim_cold` nulled the embedding of exactly these rows, and the trainer requires an embedding, so
    the label survived while silently leaving every future training set. `pinned` was supposed to
    prevent it and two of the three label writers never set it.
    """
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    # The bulk LLM labeller's path: a plain insert, which is what never set `pinned`.
    s.add(UserLabel(user_id=user.id, job_id=job_id, label=80, source="llm", weight=1.0))
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    job = s.get(CorpusJob, job_id)
    assert job is not None, "a labelled posting left the corpus and took a training row with it"
    assert job.description is not None, "and its text must still be there to retrain on"


def test_a_posting_the_user_applied_to_is_never_archived(ctx, tmp_path):
    """The applied log is the durable record of what somebody did. It outlives retention."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    TenantRepository(s).record_applied(user.id, job_id)
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id) is not None
    assert TenantRepository(s).applied_count(user.id) == 1


def test_a_dismissed_posting_is_never_archived(ctx, tmp_path):
    """Dismissing is a decision about a job, and re-delivering it would undo the decision."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    s.add(UserJobState(user_id=user.id, job_id=job_id, dismissed_at=func.now()))
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id) is not None


# ── the state that goes with it ────────────────────────────────────────────────────────────────

def test_a_score_for_an_archived_posting_goes_with_it(ctx, tmp_path):
    """`user_job_state` is the table that grows fastest, and a score for a deleted job is dead.

    The FK is RESTRICT, so leaving these behind does not corrupt anything — it makes the delete fail
    outright, which is why this has to be deliberate rather than left to the database.
    """
    r, s = ctx
    job_id = _aged(r, s, days=45)
    user = _user(s)
    TenantRepository(s).record_score(user.id, job_id, score=42.0, model_version=1)
    s.flush()

    result = archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert result.jobs >= 1
    assert result.states >= 1
    assert s.get(CorpusJob, job_id) is None
    assert s.get(UserJobState, (user.id, job_id)) is None


def test_the_skip_tombstones_do_not_keep_a_posting_alive(ctx, tmp_path):
    """A null score with `scored_at` set is the filter's tombstone, not a human action.

    30,442 of these existed on 2026-08-10. If they counted as "acted on", nothing would ever be
    archived and the sink would look closed while staying open.
    """
    r, s = ctx
    job_id = _aged(r, s, days=45)
    user = _user(s)
    s.add(UserJobState(user_id=user.id, job_id=job_id, score=None, scored_at=func.now()))
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id) is None


# ── user_job_state, the table that grows fastest (ROADMAP 6bd) ────────────────────────────────

def test_a_score_row_goes_even_when_the_posting_stays_for_somebody_else(ctx, tmp_path):
    """**The half that archiving the posting does not cover.**

    Alice labels a posting, so it stays in the corpus forever — correctly. Bob's row for the same
    posting is a score the platform computed and can compute again, and nothing ever removed it. That
    is how `user_job_state` reached 88,041 rows for two accounts: it grows with accounts × kept
    postings, not with the retention window.
    """
    r, s = ctx
    job_id = _aged(r, s, days=200)
    alice, bob = _user(s), _user(s)
    s.add(UserLabel(user_id=alice.id, job_id=job_id, label=80, source="llm", weight=1.0))
    TenantRepository(s).record_score(alice.id, job_id, score=90.0, model_version=1)
    TenantRepository(s).record_score(bob.id, job_id, score=42.0, model_version=1)
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id) is not None, "Alice's label must keep the posting"
    assert s.get(UserJobState, (alice.id, job_id)) is not None, "and her own row with it"
    assert s.get(UserJobState, (bob.id, job_id)) is None, (
        "Bob computed a score and did nothing with it — that row had no reason to outlive retention")


def test_an_applied_row_survives_on_a_posting_nobody_labelled(ctx, tmp_path):
    """The sweep must not mistake a person's own history for our working out."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    TenantRepository(s).record_applied(user.id, job_id)
    TenantRepository(s).record_score(user.id, job_id, score=77.0, model_version=1)
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    row = s.get(UserJobState, (user.id, job_id))
    assert row is not None and row.applied_at is not None


def test_a_score_row_inside_the_window_is_left_alone(ctx, tmp_path):
    """Deleting a live posting's score row makes the job come back as new and get re-delivered."""
    r, s = ctx
    job_id = _aged(r, s, days=3)
    user = _user(s)
    TenantRepository(s).record_score(user.id, job_id, score=42.0, model_version=1)
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()
    s.expire_all()

    assert s.get(UserJobState, (user.id, job_id)) is not None


def test_no_state_row_outlives_retention_without_a_reason(ctx, tmp_path):
    """The invariant, asserted as a relationship rather than a count (CLAUDE.md §6).

    Whatever the mix of labels, actions and bare scores, after a pass there must be no row left that
    is both past the window and records nothing. A count would pass while the rule was wrong.
    """
    r, s = ctx
    alice, bob = _user(s), _user(s)
    labelled, applied, plain, fresh = (_aged(r, s, days=200), _aged(r, s, days=100),
                                       _aged(r, s, days=90), _aged(r, s, days=2))
    s.add(UserLabel(user_id=alice.id, job_id=labelled, label=80, source="llm", weight=1.0))
    for job_id in (labelled, applied, plain, fresh):
        for user in (alice, bob):
            TenantRepository(s).record_score(user.id, job_id, score=50.0, model_version=1)
    TenantRepository(s).record_applied(bob.id, applied)
    s.flush()

    archive_expired(s, directory=tmp_path)
    s.flush()

    cutoff = datetime.now(UTC) - timedelta(days=30)
    leftover = s.execute(
        select(func.count())
        .select_from(UserJobState)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(CorpusJob.first_seen_at < cutoff,
               UserJobState.applied_at.is_(None),
               UserJobState.cover_built.is_(False),
               UserJobState.dismissed_at.is_(None),
               ~UserLabel.__table__.select().where(
                   (UserLabel.user_id == UserJobState.user_id)
                   & (UserLabel.job_id == UserJobState.job_id)).exists())).scalar()
    assert leftover == 0, f"{leftover} state row(s) past retention record nothing and stayed anyway"


def test_the_state_sweep_is_bounded_like_the_rest_of_the_pass(ctx, tmp_path):
    """It shares the harvest tick's budget, so it cannot be the stage that overruns it."""
    r, s = ctx
    alice = _user(s)
    kept = _aged(r, s, days=200)
    s.add(UserLabel(user_id=alice.id, job_id=kept, label=80, source="llm", weight=1.0))
    others = [_user(s) for _ in range(3)]
    for user in others:
        TenantRepository(s).record_score(user.id, kept, score=42.0, model_version=1)
    s.flush()

    removed = forget_stale_state(s, cutoff=datetime.now(UTC) - timedelta(days=30), limit=2)

    assert removed == 2


# ── the guarantees ────────────────────────────────────────────────────────────────────────────

def test_nothing_is_deleted_when_the_archive_cannot_be_written(ctx, tmp_path):
    """**Write, then delete.** A posting must never be lost because a disk was full.

    Directed at a path that cannot be created, which is what a bad `ARCHIVE_DIR` looks like.
    """
    r, s = ctx
    job_id = _aged(r, s, days=45)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file", encoding="utf-8")

    with pytest.raises(OSError):
        archive_expired(s, directory=blocker / "inside")

    # No rollback on purpose: the write fails before any DELETE is issued, so the row is still there
    # inside the same transaction. Needing a rollback to save it would mean the order was wrong.
    s.expire_all()
    assert s.get(CorpusJob, job_id) is not None, (
        "the archive write failed and the posting was deleted anyway")


def test_an_empty_pass_writes_no_file_and_reports_nothing(ctx, tmp_path):
    """A file per tick with nothing in it is noise, and every tick is a pass."""
    r, s = ctx
    _aged(r, s, days=1)

    result = archive_expired(s, directory=tmp_path)

    assert result.jobs == 0
    assert result.path is None
    assert list(tmp_path.iterdir()) == []


def test_two_passes_on_one_day_append_rather_than_replace(ctx, tmp_path):
    """The second pass must not overwrite what the first archived."""
    r, s = ctx
    first_id = _aged(r, s, days=45)
    first = archive_expired(s, directory=tmp_path, limit=1)
    s.flush()

    second_id = _aged(r, s, days=46)
    second = archive_expired(s, directory=tmp_path, limit=1)
    s.flush()

    assert first.path == second.path, "same day, same file"
    urls = {row["url"] for row in read_archive(second.path)}
    assert len(urls) == 2, f"the first pass was overwritten: {urls}"
    assert first_id and second_id


def test_the_batch_is_bounded_so_retention_cannot_own_the_cycle(ctx, tmp_path):
    """It runs on the harvest tick, which has an 11-minute budget for everything."""
    r, s = ctx
    for _ in range(3):
        _aged(r, s, days=45)

    result = archive_expired(s, directory=tmp_path, limit=2)

    assert result.jobs == 2


def test_the_oldest_postings_leave_first(ctx, tmp_path):
    """A bounded pass that picked arbitrarily would leave the oldest rows forever."""
    r, s = ctx
    oldest = _aged(r, s, days=300)
    _aged(r, s, days=40)

    result = archive_expired(s, directory=tmp_path, limit=1)
    s.flush()
    s.expire_all()

    assert result.jobs == 1
    assert s.get(CorpusJob, oldest) is None


def test_the_retention_window_is_thirty_days(ctx):
    """Set by the owner 2026-08-11. It had two values before: 90 in code against the stated 30."""
    from libs.settings import CORPUS_RETENTION_DAYS

    assert CORPUS_RETENTION_DAYS == 30
    assert not hasattr(__import__("libs.corpus.repository", fromlist=["x"]), "CORPUS_HOT_DAYS"), (
        "the 90-day constant is back, and it is the one the code would enforce")


def test_a_posting_and_its_row_count_agree_after_a_pass(ctx, tmp_path):
    """The count reported is the count deleted — an honest number or the sink looks closed."""
    r, s = ctx
    before = s.scalar(select(func.count()).select_from(CorpusJob)) or 0
    for _ in range(3):
        _aged(r, s, days=45)
    s.flush()

    result = archive_expired(s, directory=tmp_path)
    s.flush()

    after = s.scalar(select(func.count()).select_from(CorpusJob)) or 0
    assert before + 3 - result.jobs == after
    assert len(read_archive(result.path)) == result.jobs
