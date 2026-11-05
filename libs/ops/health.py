"""What the platform looks like right now, as numbers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from pymongo.database import Database

from libs.corpus.archive import overdue
from libs.db.models import CorpusJob, QueueJob, User
from libs.db.models.queue import LANES

# What a lane's oldest waiting job is *expected* to be, in minutes.
EXPECTED_WAIT_MIN = {"notify": 5, "docgen": 10}


@dataclass
class LaneHealth:
    lane: str
    ready: int
    claimed: int
    dead: int
    oldest_ready_min: float | None
    expected_wait_min: int

    @property
    def stalled(self) -> bool:
        """Four times the lane's normal wait."""
        return (self.oldest_ready_min or 0) > self.expected_wait_min * 4


@dataclass
class UserHealth:
    user_id: str
    email: str
    telegram_status: str


@dataclass
class PlatformHealth:
    checked_at: str
    lanes: list[LaneHealth]
    users_total: int
    users_active: int
    corpus_rows: int
    # Postings a day past their keep and still here: the archive pass is behind or failing.
    archive_overdue: int
    # What this database takes on its disk, data and indexes, compressed as stored.
    database_bytes: int
    # The disk the database lives on, as MongoDB itself reports it (`dbStats`): server_db's data disk,
    # shared with every app on it. What fills, and what the alert is about. Atlas's 512 MB, an
    # operator's setting until 2026-11-05, stopped being the limit when the platform moved there.
    disk_used_bytes: int
    disk_total_bytes: int
    users: list[UserHealth] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def lane_health(db: Database) -> list[LaneHealth]:
    """Depth, dead letters, and the oldest thing waiting, per lane."""
    rows = db[QueueJob.COLLECTION].aggregate([{"$group": {
        "_id": {"lane": "$lane", "state": "$state"}, "count": {"$sum": 1},
        "first_run": {"$min": "$run_after"}}}])

    counts: dict[str, dict[str, int]] = {lane: {} for lane in LANES}
    oldest: dict[str, datetime | None] = dict.fromkeys(LANES)
    for row in rows:
        lane, state = row["_id"]["lane"], row["_id"]["state"]
        counts.setdefault(lane, {})[state] = row["count"]
        if state == "ready":
            oldest[lane] = row["first_run"]

    now = datetime.now(UTC)
    return [
        LaneHealth(
            lane=lane,
            ready=counts.get(lane, {}).get("ready", 0),
            claimed=counts.get(lane, {}).get("claimed", 0),
            dead=counts.get(lane, {}).get("dead", 0),
            oldest_ready_min=(
                round((now - oldest[lane]).total_seconds() / 60, 1) if oldest.get(lane) else None),
            expected_wait_min=EXPECTED_WAIT_MIN.get(lane, 30),
        )
        for lane in LANES
    ]


def user_health(db: Database) -> list[UserHealth]:
    """Per-account state, for the alerts that name a user.

    **Asked for the whole platform at once.** This was a loop of about seven queries an account; at
    fifty accounts and 2.65M state rows that measured 18.8 seconds against `health.service`'s
    60-second timeout. What it counted per account — matches scored above the account's cutoff and
    not yet sent — went with the scorer (ROADMAP Phase 8), and with it the last per-account query.
    """
    return [UserHealth(user_id=str(user["_id"]), email=user["email"],
                       telegram_status=user["telegram_status"])
            for user in db[User.COLLECTION].find({"status": "active"},
                                                 {"email": 1, "telegram_status": 1}, sort=[("email", 1)])]


def platform_health(db: Database, *, include_users: bool = True) -> PlatformHealth:
    """Everything, in one call. Serves the admin endpoint and the alert timer alike."""
    size = db.command("dbStats")
    return PlatformHealth(
        checked_at=datetime.now(UTC).isoformat(),
        lanes=lane_health(db),
        users_total=db[User.COLLECTION].count_documents({}),
        users_active=db[User.COLLECTION].count_documents({"active": True}),
        corpus_rows=db[CorpusJob.COLLECTION].estimated_document_count(),
        archive_overdue=overdue(db),
        database_bytes=int(size.get("storageSize", 0) + size.get("indexSize", 0)),
        disk_used_bytes=int(size.get("fsUsedSize", 0)),
        disk_total_bytes=int(size.get("fsTotalSize", 0)),
        users=user_health(db) if include_users else [],
    )


def since(moment: datetime | None) -> timedelta | None:
    """Small helper, exported because both callers of this module want the same rounding."""
    return None if moment is None else datetime.now(UTC) - moment
