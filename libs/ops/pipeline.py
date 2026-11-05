"""Where the jobs are right now — the shape of the pipeline rather than its speed."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pymongo.database import Database

from libs.corpus.freshness import platform_windows
from libs.corpus.repository import awaiting_description_conditions
from libs.db.models import CorpusJob, QueueJob, User, UserJobState
from libs.settings import PLATFORM_TICK_MIN

# How long a row may sit at a stage before the colour changes, as multiples of its tick.
AMBER_TICKS = 2
RED_TICKS = 8


@dataclass(frozen=True)
class Stage:
    """One stage of the pipeline, as it stands right now."""

    key: str
    label: str
    what: str
    # How many rows entered this stage in the window.
    entered: int | None
    # How many are sitting in it, waiting for the next step to take them.
    waiting: int | None
    # How long the oldest of those has waited, in minutes.
    oldest_min: float | None

    @property
    def verdict(self) -> str:
        """green, amber or red."""
        if not self.waiting or self.oldest_min is None:
            return "ok"
        if self.oldest_min > RED_TICKS * PLATFORM_TICK_MIN:
            return "down"
        if self.oldest_min > AMBER_TICKS * PLATFORM_TICK_MIN:
            return "warn"
        return "ok"

    def as_dict(self) -> dict:
        return {**asdict(self), "verdict": self.verdict}


def _age_minutes(db: Database, field: str, conditions: list[dict]) -> float | None:
    """How long ago the oldest matching posting arrived, in minutes. `None` when there are none."""
    oldest = db[CorpusJob.COLLECTION].find_one({"$and": conditions}, {field: 1}, sort=[(field, 1)])
    if oldest is None:
        return None
    return round((datetime.now(UTC) - oldest[field]).total_seconds() / 60, 1)


def _entered(db: Database, field: str, since) -> int:
    return db[CorpusJob.COLLECTION].count_documents({field: {"$gte": since}})


def pipeline(db: Database, *, hours: int = 24) -> dict:
    """Every stage, with what is in it."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    # The describer's own question: a row it will never fetch is not waiting.
    describe_conditions = awaiting_description_conditions(platform_windows(db))

    stages = [
        # One row, because it is one statement.
        Stage("gather", "Gathered & stored",
              "A source answered with a link, and it survived dedup against four keys. One "
              "statement, so nothing is ever queued between the two.",
              entered=_entered(db, "first_seen_at", since),
              waiting=None, oldest_min=None),
        Stage("describe", "Described", "The posting's own text, fetched where the listing gave a "
                                       "teaser.",
              entered=_entered(db, "description_fetched_at", since),
              waiting=db[CorpusJob.COLLECTION].count_documents({"$and": describe_conditions}),
              oldest_min=_age_minutes(db, "first_seen_at", describe_conditions)),
        # No queue of its own to show: what is matched is decided when it is read (`libs.matching`),
        # so the step after a described posting is a message, not a stage a row can wait in.
        Stage("deliver", "Delivered", "Sent to the people whose words it matches, up to each "
                                      "account's daily cap.",
              entered=db[UserJobState.COLLECTION].count_documents({"notified_at": {"$gte": since}}),
              waiting=None, oldest_min=None),
    ]
    return {"window_hours": hours, "stages": [stage.as_dict() for stage in stages],
            "lanes": lanes(db, since),
            # The same lanes, split by account: "docgen 2 dead" is only actionable once you know
            # whose two, and which error.
            "lane_accounts": lane_accounts(db, since),
            "at": datetime.now(UTC).isoformat(),
            "amber_after_min": AMBER_TICKS * PLATFORM_TICK_MIN,
            "red_after_min": RED_TICKS * PLATFORM_TICK_MIN}


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
