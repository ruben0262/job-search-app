"""Where the jobs are right now — the shape of the pipeline rather than its speed."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, QueueJob, User, UserJobState
from libs.scoring.thresholds import cutoff_for
from libs.settings import PLATFORM_TICK_MIN
from libs.tenant.filters import UserFilters, candidate_conditions

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
    # Matched, never sent, and now too old to send — work the platform did and threw away.
    expired: int | None = None

    @property
    def verdict(self) -> str:
        """green, amber or red."""
        if self.expired is not None:
            return "down" if self.expired else "ok"
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
    live = CorpusJob.trimmed_at.is_(None)

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
                  .where(live, CorpusJob.description.is_(None))) or 0,
              oldest_min=_age_minutes(session, CorpusJob.first_seen_at,
                                      live, CorpusJob.description.is_(None))),
        Stage("embed", "Embedded", "One vector per posting, computed once and shared by every "
                                   "account.",
              entered=_entered(session, CorpusJob.embedded_at, since),
              waiting=session.scalar(
                  select(func.count()).select_from(CorpusJob)
                  .where(live, CorpusJob.description.is_not(None),
                         CorpusJob.embedding.is_(None))) or 0,
              oldest_min=_age_minutes(session, CorpusJob.description_fetched_at,
                                      live, CorpusJob.description.is_not(None),
                                      CorpusJob.embedding.is_(None))),
    ]
    stages += _per_user_stages(session, since)
    return {"window_hours": hours, "stages": [stage.as_dict() for stage in stages],
            "lanes": lanes(session, since),
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


def _per_user_stages(session: Session, since) -> list[Stage]:
    """Scoring and delivery, summed across accounts."""
    scored_entered = session.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.scored_at.is_not(None), UserJobState.scored_at >= since)) or 0
    delivered_entered = session.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.notified_at.is_not(None), UserJobState.notified_at >= since)) or 0

    to_score = to_deliver = expired = 0
    oldest_to_score = oldest_to_deliver = None

    for user in session.scalars(select(User).where(User.active)):
        filters = UserFilters.from_config(_config(session, user.id))
        conditions = candidate_conditions(filters)
        cutoff = cutoff_for(session, user.id)

        # The count and the age must ask the same question.
        unscored = (CorpusJob.embedding.is_not(None),
                    UserJobState.scored_model_version.is_(None)
                    | (UserJobState.scored_model_version != user.model_version),
                    *conditions)
        scoring = (select(func.count()).select_from(CorpusJob)
                   .outerjoin(UserJobState, (UserJobState.job_id == CorpusJob.id)
                              & (UserJobState.user_id == user.id)))
        to_score += session.scalar(scoring.where(*unscored)) or 0
        # Was never computed, so this stage could not turn amber or red whatever was queued behind it.
        oldest_to_score = _oldest(oldest_to_score, session.scalar(
            select(func.min(CorpusJob.first_seen_at))
            .outerjoin(UserJobState, (UserJobState.job_id == CorpusJob.id)
                       & (UserJobState.user_id == user.id))
            .where(*unscored)))

        # What delivery would consider: above this account's cutoff, never sent, and a candidate row.
        waiting = (UserJobState.user_id == user.id,
                   UserJobState.notified_at.is_(None),
                   UserJobState.score.is_not(None),
                   UserJobState.score >= cutoff)
        pending = (select(func.count()).select_from(UserJobState)
                   .join(CorpusJob, CorpusJob.id == UserJobState.job_id))
        to_deliver += session.scalar(pending.where(*waiting, *conditions)) or 0
        oldest_to_deliver = _oldest(oldest_to_deliver, session.scalar(
            select(func.min(UserJobState.scored_at))
            .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
            .where(*waiting, *conditions)))

        # The same pool, minus the freshness condition, and that is the whole measurement.
        expired += session.scalar(
            pending.where(*waiting, CorpusJob.closed_at.is_(None),
                          func.coalesce(CorpusJob.posted_at, CorpusJob.first_seen_at)
                          < filters.fresh_since(),
                          UserJobState.scored_at >= since)) or 0

    return [
        Stage("score", "Scored", "Filtered against each account, then ranked by their own model.",
              entered=scored_entered, waiting=to_score,
              oldest_min=_minutes_since(oldest_to_score)),
        Stage("deliver", "Delivered",
              "Sent to Telegram as a card, up to the account's cap each cycle. A queue here is the "
              "cap working; postings that expired in it are not.",
              entered=delivered_entered, waiting=to_deliver,
              oldest_min=_minutes_since(oldest_to_deliver), expired=expired),
    ]


def _oldest(current: datetime | None, candidate: datetime | None) -> datetime | None:
    """The earlier of two timestamps, ignoring absent ones."""
    if candidate is None:
        return current
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=UTC)
    return candidate if current is None else min(current, candidate)


def _minutes_since(when: datetime | None) -> float | None:
    """How long ago, in minutes. `None` stays `None` — a dash, never a zero."""
    if when is None:
        return None
    return round((datetime.now(UTC) - when).total_seconds() / 60, 1)


def _config(session: Session, user_id) -> dict | None:
    from libs.db.models import UserConfig  # noqa: PLC0415 - avoids an import cycle at module load

    return session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id))
