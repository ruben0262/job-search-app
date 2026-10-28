"""Where the jobs are right now — the shape of the pipeline rather than its speed."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.corpus.repository import awaiting_description_conditions
from libs.db.models import CorpusJob, QueueJob, User, UserJobState
from libs.settings import PLATFORM_TICK_MIN
from libs.settings import get as settings_get

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


def _age_minutes(session: Session, column, *conditions) -> float | None:
    """How long ago the oldest matching row arrived, in minutes. `None` when there are none."""
    oldest = session.scalar(select(func.min(column)).where(*conditions))
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - oldest).total_seconds() / 60, 1)


def _entered(session: Session, column, since) -> int:
    return session.scalar(select(func.count()).select_from(CorpusJob)
                          .where(column.is_not(None), column >= since)) or 0


def pipeline(session: Session, *, hours: int = 24) -> dict:
    """Every stage, with what is in it."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    # Not trimmed.
    # The describer skips rows past the delivery window, so the page must too — counting them
    # would rebuild exactly the phantom backlog the closed-row fix removed, one setting later.
    describe_conditions = awaiting_description_conditions(
        max_age_hours=int(settings_get(session, "job_max_age_hours")))

    stages = [
        # One row, because it is one statement.
        Stage("gather", "Gathered & stored",
              "A source answered with a link, and it survived dedup against four keys. One "
              "statement, so nothing is ever queued between the two.",
              entered=_entered(session, CorpusJob.first_seen_at, since),
              waiting=None, oldest_min=None),
        Stage("describe", "Described", "The posting's own text, fetched where the listing gave a "
                                       "teaser.",
              entered=_entered(session, CorpusJob.description_fetched_at, since),
              waiting=session.scalar(
                  select(func.count()).select_from(CorpusJob)
                  .where(*describe_conditions)) or 0,
              oldest_min=_age_minutes(session, CorpusJob.first_seen_at, *describe_conditions)),
        # No queue of its own to show: what is matched is decided when it is read (`libs.matching`),
        # so the step after a described posting is a message, not a stage a row can wait in.
        Stage("deliver", "Delivered", "Sent to the people whose words it matches, up to each "
                                      "account's daily cap.",
              entered=session.scalar(select(func.count()).select_from(UserJobState)
                                     .where(UserJobState.notified_at >= since)) or 0,
              waiting=None, oldest_min=None),
    ]
    return {"window_hours": hours, "stages": [stage.as_dict() for stage in stages],
            "lanes": lanes(session, since),
            # The same lanes, split by account: "docgen 2 dead" is only actionable once you know
            # whose two, and which error.
            "lane_accounts": lane_accounts(session, since),
            "at": datetime.now(UTC).isoformat(),
            "amber_after_min": AMBER_TICKS * PLATFORM_TICK_MIN,
            "red_after_min": RED_TICKS * PLATFORM_TICK_MIN}


def lanes(session: Session, since: datetime) -> list[dict]:
    """What the queue is doing right now, per lane."""
    rows = session.execute(
        select(QueueJob.lane,
               func.count().filter(QueueJob.state == "ready").label("ready"),
               # `claimed`, not `running`.
               func.count().filter(QueueJob.state == "claimed").label("running"),
               func.count().filter(QueueJob.state == "dead").label("dead"),
               func.count().filter((QueueJob.state == "done")
                                   & (QueueJob.finished_at >= since)).label("done"))
        .group_by(QueueJob.lane).order_by(QueueJob.lane)).all()
    return [{"lane": row.lane, "ready": row.ready, "running": row.running,
             "dead": row.dead, "done": row.done} for row in rows]


def lane_accounts(session: Session, since: datetime) -> list[dict]:
    """Which accounts each lane is working for, and how it is going for each of them.

    `lanes` above answers "docgen has two dead jobs". It does not answer "whose", and whose is the
    question an operator actually has: a lane is per-account work, and two dead docgen jobs are
    either one account in a broken state or two accounts with unrelated problems. Finding out meant
    opening a psql session against production, which is how the account that could not build a CV
    went unnoticed for three days.
    """
    rows = session.execute(
        select(QueueJob.lane, QueueJob.user_id, User.email,
               func.count().filter(QueueJob.state == "ready").label("ready"),
               func.count().filter(QueueJob.state == "claimed").label("running"),
               func.count().filter(QueueJob.state == "dead").label("dead"),
               func.count().filter((QueueJob.state == "done")
                                   & (QueueJob.finished_at >= since)).label("done"),
               # The newest failure on this lane for this account, whatever its state now: a job
               # that failed four times and then succeeded is still worth seeing.
               func.max(QueueJob.last_error).label("last_error"))
        .join(User, User.id == QueueJob.user_id)
        .group_by(QueueJob.lane, QueueJob.user_id, User.email)
        .order_by(QueueJob.lane, User.email)).all()

    return [{"lane": row.lane,
             "user_id": str(row.user_id),
             "email": row.email,
             "ready": row.ready, "running": row.running,
             "dead": row.dead, "done": row.done,
             # Truncated: this is a panel, and a traceback belongs in the logs.
             "last_error": (row.last_error or "")[:200] or None}
            for row in rows]
