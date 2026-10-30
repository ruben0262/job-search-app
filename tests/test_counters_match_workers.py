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


def _job(db, **kwargs):
    from libs.db.models import CorpusJob

    kwargs.setdefault("source", "himalayas")
    kwargs.setdefault("posted_at", datetime.now(UTC))
    return insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer", **kwargs))


# ── the pipeline page: counters that drove an operator, not a loop ────────────────────────────

def test_the_page_and_the_describer_count_the_same_rows(db):
    """Same shape, same guard: a closed row is never fetched and must not read as waiting, and neither
    is one too old to deliver. Only the row the describer will actually take is counted, by both."""
    from libs import settings
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    max_age = int(settings.get(db, "job_max_age_hours"))
    _job(db, description=None, closed_at=datetime.now(UTC))
    _job(db, description=None, first_seen_at=datetime.now(UTC) - timedelta(hours=max_age + 1),
         posted_at=datetime.now(UTC) - timedelta(hours=max_age + 1))
    waiting = _job(db, description=None)

    page = {s["key"]: s for s in pipeline(db, hours=24)["stages"]}["describe"]["waiting"]
    worker = CorpusRepository(db).awaiting_description(limit=5000, max_age_hours=max_age)

    assert [row.id for row in worker] == [waiting.id]
    assert page == len(worker)
