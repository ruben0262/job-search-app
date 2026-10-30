"""Retention: what leaves the corpus, what never does, and that it can be read back.

The failures these are written against are all silent ones. Losing a posting raises nothing, losing
a person's history raises nothing, and an archive file that cannot be parsed looks exactly like a
successful archive until somebody needs it.
"""
from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import timedelta

import pytest

from libs.corpus.archive import ACTED_ON, archive_expired, forget_stale_state, read_archive
from libs.corpus.repository import CorpusRepository
from libs.db import find_one, get, insert, now
from libs.db.models import CorpusJob, User, UserJobState
from libs.db.models.tenant import record_state


@pytest.fixture()
def ctx(db):
    return CorpusRepository(db), db


def _u() -> str:
    return f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}"


def _user(s) -> User:
    return insert(s, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC",
                          status="active", active=True))


def _aged(r, s, *, days: int, description: str = "Go, gRPC, Postgres. " * 20) -> int:
    """A described posting whose `first_seen_at` is `days` old."""
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer",
                   company=f"Acme{uuid.uuid4().hex[:8]}")
    r.set_description(job_id, description)
    s[CorpusJob.COLLECTION].update_one({"_id": job_id},
                                       {"$set": {"first_seen_at": now() - timedelta(days=days)}})
    return job_id


def _sent(s, user, job_id) -> None:
    """A row the platform wrote and the person did nothing with: the posting was sent to them."""
    record_state(s, user.id, job_id, notified_at=now())


def _state(s, user, job_id) -> UserJobState | None:
    return find_one(s, UserJobState, {"user_id": user.id, "job_id": job_id})


# ── what leaves ───────────────────────────────────────────────────────────────────────────────

def test_a_posting_past_the_window_leaves_the_corpus_whole(ctx, tmp_path):
    """The row is deleted, not hollowed out — and everything about it is in the file first."""
    r, s = ctx
    job_id = _aged(r, s, days=45)
    url = get(s, CorpusJob, job_id).url

    result = archive_expired(s, directory=tmp_path)

    assert result.jobs == 1
    assert get(s, CorpusJob, job_id) is None, "the row must be gone, not nulled in place"

    written = {row["url"]: row for row in read_archive(result.path)}
    assert url in written, "the posting was deleted without being archived"
    assert written[url]["description"], "the description is the part worth keeping"
    assert written[url]["title"] == "Backend Engineer"
    assert written[url]["id"] == job_id, "the id the site and the alerts carried"


def test_the_archive_keeps_every_field(ctx, tmp_path):
    """Nothing is dropped, so a field added to the model cannot be lost here unnoticed."""
    r, s = ctx
    _aged(r, s, days=45)
    declared = {field.name for field in fields(CorpusJob)}

    result = archive_expired(s, directory=tmp_path)
    assert result.jobs == 1

    archived = read_archive(result.path)[0]
    assert declared <= set(archived), f"these fields are not archived: {sorted(declared - set(archived))}"


def test_a_fresh_posting_is_left_alone(ctx, tmp_path):
    r, s = ctx
    job_id = _aged(r, s, days=3)

    archive_expired(s, directory=tmp_path)

    assert get(s, CorpusJob, job_id) is not None


# ── what never leaves ─────────────────────────────────────────────────────────────────────────

def test_a_posting_with_a_cover_letter_built_is_never_archived(ctx, tmp_path):
    """A cover letter is work a person asked for against this posting; its text must still be there.

    Before 2026-10-30 this test was about training labels, the rows the old `trim_cold` silently
    hollowed out. Labels went with the learned model; the cover letter is the action left standing in
    their place, and the one of the three nothing else here covered.
    """
    r, s = ctx
    job_id = _aged(r, s, days=200)
    record_state(s, _user(s).id, job_id, cover_built=True)

    archive_expired(s, directory=tmp_path)

    job = get(s, CorpusJob, job_id)
    assert job is not None, "a posting somebody built a cover letter for left the corpus"
    assert job.description is not None, "and its text must still be there"


def test_a_posting_the_user_applied_to_is_never_archived(ctx, tmp_path):
    """The applied log is the durable record of what somebody did. It outlives retention."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    record_state(s, user.id, job_id, applied_at=now())

    archive_expired(s, directory=tmp_path)

    assert get(s, CorpusJob, job_id) is not None
    assert s[UserJobState.COLLECTION].count_documents({"user_id": user.id, "applied_at": {"$ne": None}}) == 1


def test_a_dismissed_posting_is_never_archived(ctx, tmp_path):
    """Dismissing is a decision about a job, and re-delivering it would undo the decision."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    record_state(s, _user(s).id, job_id, dismissed_at=now())

    archive_expired(s, directory=tmp_path)

    assert get(s, CorpusJob, job_id) is not None


# ── the state that goes with it ────────────────────────────────────────────────────────────────

def test_a_sent_row_for_an_archived_posting_goes_with_it(ctx, tmp_path):
    """`user_job_state` is the collection that grows fastest, and a row for a deleted job is dead.

    Nothing in MongoDB refuses the posting's delete while rows still point at it, so leaving these
    behind would not fail: it would leave orphans, silently. This has to be deliberate.
    """
    r, s = ctx
    job_id = _aged(r, s, days=45)
    user = _user(s)
    _sent(s, user, job_id)

    result = archive_expired(s, directory=tmp_path)

    assert result.jobs == 1
    assert result.states == 1
    assert get(s, CorpusJob, job_id) is None
    assert _state(s, user, job_id) is None


# ── user_job_state, the collection that grows fastest (ROADMAP 6bd) ───────────────────────────

def test_a_sent_row_goes_even_when_the_posting_stays_for_somebody_else(ctx, tmp_path):
    """**The half that archiving the posting does not cover.**

    Alice applies to a posting, so it stays in the corpus forever — correctly. Bob's row for the same
    posting only says it was sent to him, and nothing ever removed it. That is how `user_job_state`
    reached 88,041 rows for two accounts: it grows with accounts × kept postings, not with the
    retention window.
    """
    r, s = ctx
    job_id = _aged(r, s, days=200)
    alice, bob = _user(s), _user(s)
    record_state(s, alice.id, job_id, applied_at=now(), notified_at=now())
    _sent(s, bob, job_id)

    archive_expired(s, directory=tmp_path)

    assert get(s, CorpusJob, job_id) is not None, "Alice's application must keep the posting"
    assert _state(s, alice, job_id) is not None, "and her own row with it"
    assert _state(s, bob, job_id) is None, (
        "Bob was sent it and did nothing with it — that row had no reason to outlive retention")


def test_an_applied_row_survives_the_sweep(ctx, tmp_path):
    """The sweep must not mistake a person's own history for our working out."""
    r, s = ctx
    job_id = _aged(r, s, days=200)
    user = _user(s)
    record_state(s, user.id, job_id, applied_at=now(), notified_at=now())

    archive_expired(s, directory=tmp_path)

    row = _state(s, user, job_id)
    assert row is not None and row.applied_at is not None


def test_a_sent_row_inside_the_window_is_left_alone(ctx, tmp_path):
    """Deleting a live posting's row makes the job come back as new and get re-delivered."""
    r, s = ctx
    job_id = _aged(r, s, days=3)
    user = _user(s)
    _sent(s, user, job_id)

    archive_expired(s, directory=tmp_path)

    assert _state(s, user, job_id) is not None


def test_no_state_row_outlives_retention_without_a_reason(ctx, tmp_path):
    """The invariant, asserted as a relationship rather than a count (CLAUDE.md §6).

    Whatever the mix of actions and sent rows, after a pass there must be no row left that records
    nothing and points at a posting that is past the window — or gone, which no foreign key prevents
    any more. A count would pass while the rule was wrong.
    """
    r, s = ctx
    alice, bob = _user(s), _user(s)
    covered, applied, plain, fresh = (_aged(r, s, days=200), _aged(r, s, days=100),
                                      _aged(r, s, days=90), _aged(r, s, days=2))
    for job_id in (covered, applied, plain, fresh):
        for user in (alice, bob):
            _sent(s, user, job_id)
    record_state(s, alice.id, covered, cover_built=True)
    record_state(s, bob.id, applied, applied_at=now())

    archive_expired(s, directory=tmp_path)

    live = s[CorpusJob.COLLECTION].distinct("_id", {"first_seen_at": {"$gte": now() - timedelta(days=30)}})
    assert live == [fresh]
    leftover = s[UserJobState.COLLECTION].count_documents({"job_id": {"$nin": live}, "$nor": [ACTED_ON]})
    assert leftover == 0, f"{leftover} state row(s) past retention record nothing and stayed anyway"
    assert s[UserJobState.COLLECTION].count_documents({}) == 4, "two actions and two fresh rows stay"


def test_the_state_sweep_is_bounded_like_the_rest_of_the_pass(ctx, tmp_path):
    """It shares the harvest tick's budget, so it cannot be the stage that overruns it."""
    r, s = ctx
    kept = _aged(r, s, days=200)
    record_state(s, _user(s).id, kept, applied_at=now())
    for _ in range(3):
        _sent(s, _user(s), kept)

    removed = forget_stale_state(s, cutoff=now() - timedelta(days=30), limit=2)

    assert removed == 2
    assert forget_stale_state(s, cutoff=now() - timedelta(days=30), limit=2) == 1, \
        "the next pass continues where this one stopped"


# ── the guarantees ────────────────────────────────────────────────────────────────────────────

def test_nothing_is_deleted_when_the_archive_cannot_be_written(ctx, tmp_path):
    """**Write, then delete.** A posting must never be lost because a disk was full.

    Directed at a path that cannot be created, which is what a bad `ARCHIVE_DIR` looks like.
    """
    r, s = ctx
    job_id = _aged(r, s, days=45)
    user = _user(s)
    _sent(s, user, job_id)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file", encoding="utf-8")

    with pytest.raises(OSError):
        archive_expired(s, directory=blocker / "inside")

    # There is no rollback to save it: every write is immediate, so the write has to fail before
    # any delete is issued. Anything else would mean the order was wrong.
    assert get(s, CorpusJob, job_id) is not None, (
        "the archive write failed and the posting was deleted anyway")
    assert _state(s, user, job_id) is not None, "and its rows must still be there with it"


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

    second_id = _aged(r, s, days=46)
    second = archive_expired(s, directory=tmp_path, limit=1)

    assert first.path == second.path, "same day, same file"
    ids = {row["id"] for row in read_archive(second.path)}
    assert ids == {first_id, second_id}, f"the first pass was overwritten: {ids}"


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

    assert result.jobs == 1
    assert get(s, CorpusJob, oldest) is None


def test_the_retention_window_is_thirty_days():
    """Set by the owner 2026-08-11. It had two values before: 90 in code against the stated 30."""
    from libs.settings import CORPUS_RETENTION_DAYS

    assert CORPUS_RETENTION_DAYS == 30
    assert not hasattr(__import__("libs.corpus.repository", fromlist=["x"]), "CORPUS_HOT_DAYS"), (
        "the 90-day constant is back, and it is the one the code would enforce")


def test_a_posting_and_its_row_count_agree_after_a_pass(ctx, tmp_path):
    """The count reported is the count deleted — an honest number or the sink looks closed."""
    r, s = ctx
    corpus = s[CorpusJob.COLLECTION]
    before = corpus.count_documents({})
    for _ in range(3):
        _aged(r, s, days=45)

    result = archive_expired(s, directory=tmp_path)

    assert result.jobs == 3
    assert before + 3 - result.jobs == corpus.count_documents({})
    assert len(read_archive(result.path)) == result.jobs
