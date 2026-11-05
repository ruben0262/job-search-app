"""The steps a job takes, as both admin views judge them: how long each should take, and what waits at the
two that have a queue. One table, so Pipeline's colour for a step and Latency's cannot disagree.

    found and stored (each run) → text read (the describer, all the time) → words found (each run's end)
    → shown on Jobs pages (decided when read) → sent as an alert (the notifier)
"""
from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database

from libs.corpus.freshness import PlatformWindows, showable
from libs.corpus.repository import awaiting_description_conditions
from libs.db.models import CorpusJob
from libs.settings import PLATFORM_TICK_MIN

# Per step, on the p90 (Latency) or the oldest waiting (Pipeline), in minutes: slow after the first, too
# long after the second. From the one clock, as what each step takes when nothing is behind (2026-11-05):
THRESHOLDS: dict[str, tuple[int, int]] = {
    # Inside the run that listed it: the list's pages, then dedup and store. A run takes minutes.
    "fetched_to_stored": (5, PLATFORM_TICK_MIN),
    # The describer reads all the time, newest first: inside a tick unless it is behind.
    "found_to_described": (PLATFORM_TICK_MIN, 4 * PLATFORM_TICK_MIN),
    # The words step ends each run, so a text read just after one waits for the next run's end.
    "described_to_indexed": (2 * PLATFORM_TICK_MIN, 4 * PLATFORM_TICK_MIN),
    # Text, then words, then the notifier's next send.
    "found_to_delivered": (3 * PLATFORM_TICK_MIN, 6 * PLATFORM_TICK_MIN),
}


def text_queue(windows: PlatformWindows) -> list[dict]:
    """What the describer will read next: the describer's own question."""
    return awaiting_description_conditions(windows)


def words_queue(windows: PlatformWindows) -> list[dict]:
    """Open jobs still able to reach somebody whose words have not been searched for since their text came:
    the words step (`libs.corpus.terms.catch_up`) takes every job with none, these and older ones too."""
    return [{"terms_at": None}, {"closed_at": None}, showable(windows)]


# The steps with a queue: what waits at each, and when a job's wait there began.
QUEUES = {
    "found_to_described": (text_queue, "$first_seen_at"),
    "described_to_indexed": (words_queue, {"$ifNull": ["$description_fetched_at", "$first_seen_at"]}),
}


def waiting(db: Database, key: str, windows: PlatformWindows) -> dict:
    """How many jobs wait at the step, and how long the one waiting longest has, in minutes (`None` when
    none waits). Pipeline and Latency both read this, so the two cannot show different numbers."""
    conditions, began = QUEUES[key]
    where = {"$and": conditions(windows)}
    oldest = list(db[CorpusJob.COLLECTION].aggregate([
        {"$match": where}, {"$project": {"since": began}}, {"$sort": {"since": 1}}, {"$limit": 1}]))
    since = oldest[0]["since"] if oldest else None
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    waited = None if since is None else round((datetime.now(UTC) - since).total_seconds() / 60, 1)
    return {"waiting": db[CorpusJob.COLLECTION].count_documents(where), "oldest_min": waited}
