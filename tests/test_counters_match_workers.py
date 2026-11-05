"""A counter and the worker it drives must ask the same question.

Three separate faults in one week, all this shape, all invisible:

- The pipeline page counted rows "waiting to be embedded" that the embedder skips on purpose, so
  two closed postings sat in the Embedded stage for 25 hours reading as a stall while the embedder
  was idle and correct.
- The same page counted rows waiting to be described that the describer would never fetch.
- `unscored_count` omitted the pre-score filter while `_candidates` applied it, so the score lane
  read `remaining=2883`, re-enqueued itself, found two rows, and repeated — 2,326 completed jobs
  against a backlog that never moved.

The third is the one that shows why this deserves a guard rather than three fixes. A wrong count on
a page is a misleading number. A wrong count that drives a **decision** — re-enqueue, retry, alert —
is a loop, and it spends a user's own credential while looking healthy.

None of these raise. Every component is individually correct; the fault is between them. So the
test is not "does this function work" but "do these two agree", which is the only place it exists.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from libs.db import insert


def _job(db, *, url_tail: str | None = None, **kwargs):
    from libs.db.models import CorpusJob

    kwargs.setdefault("source", "himalayas")
    kwargs.setdefault("posted_at", datetime.now(UTC))
    kwargs.setdefault("title", "Nurse" if url_tail else "Engineer")
    url = f"https://www.linkedin.com/{url_tail}" if url_tail else f"https://example.com/{uuid.uuid4()}"
    return insert(db, CorpusJob(url=url, **kwargs))


# ── one rule for what can still be shown ─────────────────────────────────────────────────────

def test_the_jobs_page_the_text_queue_and_every_count_of_it_keep_and_drop_the_same_jobs(db, user):
    """LinkedIn's list dates a job, with no time: stored as midnight. The Jobs page judged such a job by
    when we found it; the text queue read the midnight, so every LinkedIn job still without its text left
    the queue at midnight UTC, one listed at 23:50 included, while the page went on showing it, text-less
    (2026-11-05). One rule now (`freshness.showable`): kept by all of them, or dropped by all at once."""
    from libs.corpus.freshness import platform_windows
    from libs.corpus.repository import CorpusRepository
    from libs.matching import matching_jobs
    from libs.ops.latency import stage_detail
    from libs.ops.pipeline import pipeline
    from libs.tenant.repository import TenantRepository

    TenantRepository(db).save_config(user.id, {"sections": [{"platform": "all", "keywords": ["nurse"]}]},
                                      schema_version=2)
    moment = datetime.now(UTC)
    yesterday = (moment - timedelta(hours=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    fresh = _job(db, source="linkedin", url_tail="jobs/view/1", posted_at=yesterday,
                 first_seen_at=moment - timedelta(hours=2))
    _job(db, source="linkedin", url_tail="jobs/view/2", posted_at=yesterday,
         first_seen_at=moment - timedelta(hours=25))

    queue = CorpusRepository(db).awaiting_description(limit=50, linkedin=True, windows=platform_windows(db))
    counted = CorpusRepository(db).count_awaiting_description(linkedin=True, windows=platform_windows(db))
    shown = [match.job.id for match in matching_jobs(db, user)]

    assert [row.id for row in queue] == shown == [fresh.id]
    assert counted == {row["key"]: row for row in pipeline(db)["stages"]}["describe"]["waiting"] == 1
    assert stage_detail(db, "found_to_described")["backlog"]["waiting"] == 1


# ── the pipeline page: counters that drove an operator, not a loop ────────────────────────────

def test_the_page_and_the_describer_count_the_same_rows(db):
    """Same shape, same guard: a closed row is never fetched and must not read as waiting, and neither
    is one too old to deliver. Only the row the describer will actually take is counted, by both."""
    from libs import settings
    from libs.corpus.freshness import platform_windows
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    max_age = int(settings.get(db, "job_max_age_hours"))
    _job(db, description=None, closed_at=datetime.now(UTC))
    _job(db, description=None, first_seen_at=datetime.now(UTC) - timedelta(hours=max_age + 1),
         posted_at=datetime.now(UTC) - timedelta(hours=max_age + 1))
    waiting = _job(db, description=None)

    page = {s["key"]: s for s in pipeline(db, hours=24)["stages"]}["describe"]["waiting"]
    worker = CorpusRepository(db).awaiting_description(limit=5000, windows=platform_windows(db))

    assert [row.id for row in worker] == [waiting.id]
    assert page == len(worker)
