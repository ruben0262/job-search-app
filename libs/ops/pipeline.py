"""Where the jobs are right now, step by step, in the order a job moves through them (2026-11-05):

    found and stored (each run) → text read and its words found, in one write (the describer, all the time)
    → shown on Jobs pages, decided when read (`libs.matching`) → sent as an alert (the notifier)

Each step's numbers come from the same questions the code that does the step asks (`steps`), and a job
too old for anybody's Jobs page is in no queue (`freshness.showable`). How long each step should take is
`steps.THRESHOLDS`, the same limits the Latency tab judges by.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pymongo.database import Database

from libs.corpus.freshness import platform_windows, showable
from libs.corpus.repository import awaiting_description_conditions
from libs.db.models import CorpusJob, PushSubscription, QueueJob, User, UserJobState
from libs.ops.steps import THRESHOLDS, text_queue, waiting
from libs.settings import HARVEST_SOURCES


@dataclass(frozen=True)
class Stage:
    """One step, as it stands right now."""

    key: str
    label: str
    what: str
    # How many jobs went through this step in the window.
    entered: int | None
    # How many are sitting at it, waiting for the step to take them; `None` where nothing waits.
    waiting: int | None
    # How long the oldest of those has waited, in minutes.
    oldest_min: float | None
    # Waited too long: left the step's queue too old for anybody's Jobs page, in the window, never taken.
    missed: int | None = None
    # The step's limits (`steps.THRESHOLDS`): slow after, too long after, in minutes.
    slow_after_min: int | None = None
    too_long_after_min: int | None = None
    # One sentence the numbers need beside them.
    note: str | None = None

    @property
    def verdict(self) -> str:
        """`ok`, `warn` or `down`, by how long the oldest waiting job has waited."""
        if not self.waiting or self.oldest_min is None or self.slow_after_min is None:
            return "ok"
        if self.oldest_min > self.too_long_after_min:
            return "down"
        if self.oldest_min > self.slow_after_min:
            return "warn"
        return "ok"

    def as_dict(self) -> dict:
        return {**asdict(self), "verdict": self.verdict}


def _count(db: Database, conditions: list[dict] | dict) -> int:
    where = {"$and": conditions} if isinstance(conditions, list) else conditions
    return db[CorpusJob.COLLECTION].count_documents(where)


def _limits(key: str) -> dict:
    slow, too_long = THRESHOLDS[key]
    return {"slow_after_min": slow, "too_long_after_min": too_long}


def pipeline(db: Database, *, hours: int = 24) -> dict:
    """Every step, with what is in it."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    windows = platform_windows(db)
    text = waiting(db, "found_to_described", windows)
    # Without text, and between showable then and not now: aged out of the describer's queue untaken.
    missed = [*awaiting_description_conditions(), showable(windows, now=since),
              {"$nor": [showable(windows, now=now)]}]
    instant = len(set(db[User.COLLECTION].distinct("_id", {"telegram_status": "connected"}))
                  | set(db[PushSubscription.COLLECTION].distinct("user_id")))

    stages = [
        Stage("gather", "Found and stored",
              "A board or LinkedIn listed it, and it was stored unless already here (four checks). Each "
              "run, every 15 minutes.",
              entered=_count(db, {"first_seen_at": {"$gte": since}}), waiting=None, oldest_min=None),
        Stage("describe", "Text read",
              "The job's own text, read from its page: by the describer for LinkedIn, all the time, and by "
              "each run for the other boards, and everybody's words found in it in the same write. Newest "
              "first; a job too old for anybody's Jobs page leaves the queue and is never read. A job is on "
              "Jobs pages only from here.",
              entered=_count(db, {"description_fetched_at": {"$gte": since}}),
              **text, missed=_count(db, missed), **_limits("found_to_described")),
        # Shown on Jobs pages is decided when a page is read (`libs.matching`), so it is no queue a job
        # can wait in; the step after it is an alert.
        Stage("deliver", "Alert sent",
              "Sent to the people whose words it has, by Telegram or in their browser, up to each "
              "account's daily cap. The daily email is not counted here.",
              entered=db[UserJobState.COLLECTION].count_documents({"notified_at": {"$gte": since}}),
              waiting=None, oldest_min=None,
              note=f"{instant} account(s) with Telegram or browser alerts on."),
    ]
    return {"window_hours": hours, "stages": [stage.as_dict() for stage in stages],
            "lanes": lanes(db, since),
            # The same lanes, split by account: "docgen 2 dead" is only actionable once you know
            # whose two, and which error.
            "lane_accounts": lane_accounts(db, since),
            "at": now.isoformat()}


def sources(db: Database, *, hours: int = 24) -> list[dict]:
    """Each source's last `hours`: jobs found, how many of them have their text, how many wait for it,
    and when its newest was found. Every source the harvester reads is listed, a silent one as a zero:
    a board that stops answering raises nothing, and this is where it shows."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    found = {row["_id"]: row for row in db[CorpusJob.COLLECTION].aggregate([
        {"$match": {"first_seen_at": {"$gte": since}}},
        {"$group": {"_id": "$source", "found": {"$sum": 1}, "newest": {"$max": "$first_seen_at"},
                    "with_text": {"$sum": {"$cond": [{"$ifNull": ["$described_at", False]}, 1, 0]}}}}])}
    waiting = {row["_id"]: row["n"] for row in db[CorpusJob.COLLECTION].aggregate([
        {"$match": {"$and": text_queue(platform_windows(db))}},
        {"$group": {"_id": "$source", "n": {"$sum": 1}}}])}
    names = sorted(set(HARVEST_SOURCES) | set(found) | set(waiting), key=lambda name: (
        -(found.get(name) or {}).get("found", 0), name or ""))
    return [{"source": name or "unknown", "found": (found.get(name) or {}).get("found", 0),
             "with_text": (found.get(name) or {}).get("with_text", 0), "waiting": waiting.get(name, 0),
             "newest": (found.get(name) or {}).get("newest")} for name in names]


def _tally(since: datetime) -> dict:
    """Per group: how many jobs are ready, running (claimed), dead, and done inside the window."""
    def count(condition: dict) -> dict:
        return {"$sum": {"$cond": [condition, 1, 0]}}
    return {"ready": count({"$eq": ["$state", "ready"]}),
            # `claimed`, not `running`.
            "running": count({"$eq": ["$state", "claimed"]}),
            "dead": count({"$eq": ["$state", "dead"]}),
            "done": count({"$and": [{"$eq": ["$state", "done"]}, {"$gte": ["$finished_at", since]}]})}


def lanes(db: Database, since: datetime) -> list[dict]:
    """What the queue is doing right now, per lane."""
    rows = db[QueueJob.COLLECTION].aggregate([
        {"$group": {"_id": "$lane", **_tally(since)}}, {"$sort": {"_id": 1}}])
    return [{"lane": row["_id"], "ready": row["ready"], "running": row["running"],
             "dead": row["dead"], "done": row["done"]} for row in rows]


def lane_accounts(db: Database, since: datetime) -> list[dict]:
    """Which accounts each lane is working for, and how it is going for each of them.

    `lanes` above answers "docgen has two dead jobs". It does not answer "whose", and whose is the
    question an operator actually has: a lane is per-account work, and two dead docgen jobs are
    either one account in a broken state or two accounts with unrelated problems. Finding out meant
    opening a psql session against production, which is how the account that could not build a CV
    went unnoticed for three days.
    """
    rows = db[QueueJob.COLLECTION].aggregate([
        {"$group": {"_id": {"lane": "$lane", "user_id": "$user_id"}, **_tally(since),
                    # The newest failure on record for this lane and account. `$max` of the text alone
                    # would be the alphabetically last one; paired with the id, it is the latest.
                    "last_error": {"$max": {"$cond": [{"$ne": [{"$ifNull": ["$last_error", None]}, None]},
                                                      {"at": "$_id", "error": "$last_error"}, None]}}}},
        {"$lookup": {"from": User.COLLECTION, "localField": "_id.user_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"email": 1}}], "as": "user"}},
        {"$unwind": "$user"},
        {"$sort": {"_id.lane": 1, "user.email": 1}}])

    return [{"lane": row["_id"]["lane"],
             "user_id": str(row["_id"]["user_id"]),
             "email": row["user"]["email"],
             "ready": row["ready"], "running": row["running"],
             "dead": row["dead"], "done": row["done"],
             # Truncated: this is a panel, and a traceback belongs in the logs.
             "last_error": ((row["last_error"] or {}).get("error") or "")[:200] or None}
            for row in rows]
