"""Where the jobs are right now — the shape of the pipeline rather than its speed.

**The question the latency page cannot answer.** That one says how long each handoff took for
postings that already made it through; this says how many are sitting at each stage *at this
moment*, and whether anything is piling up.

Every outage this platform has had was a stage quietly stopping while everything around it looked
fine: six hours of ingestion lost to a filter nobody could see running, LinkedIn dead for five hours
behind a timer that could not fire, a notify lane nothing fed, and — the day this was written — 229
rows stuck awaiting an embed because a batch that could never finish inside its timeout kept the
backlog that made it too big. In every case the number existed and reading it meant knowing which
query to run.

## Every number is a count of rows, not a derived estimate

That is the whole discipline of this module. `waiting` is `SELECT count(*) WHERE <the condition the
next stage actually reads>`, so it cannot say a stage is healthy while work sits in it. Where a
number is genuinely unavailable it is `None` and the page shows a dash — never a zero, because zero
is a claim and a dash is an admission.

## Verdicts are about backlog age, not size

A thousand rows moving through in a minute is fine and forty stuck for a day is not, so the colour
comes from **how long the oldest waiting row has waited**, against the tick that is supposed to
clear it. A stage with nothing waiting is green whatever its throughput.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState
from libs.scoring.thresholds import cutoff_for
from libs.settings import PLATFORM_TICK_MIN
from libs.tenant.filters import UserFilters, candidate_conditions

# How long a row may sit at a stage before the colour changes, as multiples of the tick that is
# meant to clear it. **Derived from the tick rather than chosen**: the harvest runs every
# `PLATFORM_TICK_MIN`, so anything still waiting after two of them was not picked up by a pass that
# should have taken it, and after eight the stage is not keeping up at all.
AMBER_TICKS = 2
RED_TICKS = 8


@dataclass(frozen=True)
class Stage:
    """One stage of the pipeline, as it stands right now."""

    key: str
    label: str
    what: str
    # How many rows entered this stage in the window. `None` where the stage keeps no timestamp of
    # its own — a dash on the page, never a zero.
    entered: int | None
    # How many are sitting in it, waiting for the next step to take them.
    waiting: int
    # How long the oldest of those has waited, in minutes.
    oldest_min: float | None

    @property
    def verdict(self) -> str:
        """green, amber or red. **Age, not size** — a big stage that is moving is healthy."""
        if self.waiting == 0 or self.oldest_min is None:
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
    """Every stage, with what is in it. One page's worth of truth.

    Bounded by the number of stages rather than by the corpus: every count below is an indexed
    predicate, and the two per-user ones are a loop over accounts, which is the point of an
    invite-only launch.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)

    # **Not trimmed.** A trimmed row has had its description and vector dropped deliberately after
    # 90 days; counting it as "awaiting describe" forever would make the retention sweep look like
    # a backlog.
    live = CorpusJob.trimmed_at.is_(None)

    stages = [
        Stage("gather", "Gathered", "A source answered with a link.",
              entered=_entered(session, CorpusJob.fetched_at, since),
              waiting=0, oldest_min=None),
        Stage("store", "Stored", "Deduplicated against four keys and written to the corpus.",
              entered=_entered(session, CorpusJob.first_seen_at, since),
              waiting=0, oldest_min=None),
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
            "amber_after_min": AMBER_TICKS * PLATFORM_TICK_MIN,
            "red_after_min": RED_TICKS * PLATFORM_TICK_MIN}


def _per_user_stages(session: Session, since) -> list[Stage]:
    """Scoring and delivery, summed across accounts.

    **Both are per user and the totals are sums, not counts of distinct postings.** One corpus row
    waiting to be scored for three accounts is three units of work, and presenting it as one would
    understate the queue by exactly the number of users — which is the number that matters as this
    grows.
    """
    scored_entered = session.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.scored_at.is_not(None), UserJobState.scored_at >= since)) or 0
    delivered_entered = session.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.notified_at.is_not(None), UserJobState.notified_at >= since)) or 0

    to_score = to_deliver = 0
    oldest_to_score = oldest_to_deliver = None

    for user in session.scalars(select(User).where(User.active)):
        filters = UserFilters.from_config(_config(session, user.id))
        conditions = candidate_conditions(filters)

        to_score += session.scalar(
            select(func.count()).select_from(CorpusJob)
            .outerjoin(UserJobState, (UserJobState.job_id == CorpusJob.id)
                       & (UserJobState.user_id == user.id))
            .where(CorpusJob.embedding.is_not(None),
                   UserJobState.scored_model_version.is_(None)
                   | (UserJobState.scored_model_version != user.model_version),
                   *conditions)) or 0

        # **What delivery would actually consider**, which is the same pool `pending_feed` draws
        # from — score above this account's own tuned cutoff, never sent, and a candidate row.
        to_deliver += session.scalar(
            select(func.count()).select_from(UserJobState)
            .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
            .where(UserJobState.user_id == user.id,
                   UserJobState.notified_at.is_(None),
                   UserJobState.score.is_not(None),
                   UserJobState.score >= cutoff_for(session, user.id),
                   *conditions)) or 0

        oldest_to_deliver = _oldest(oldest_to_deliver, _age_minutes(
            session, UserJobState.scored_at,
            UserJobState.user_id == user.id, UserJobState.notified_at.is_(None),
            UserJobState.score.is_not(None),
            UserJobState.score >= cutoff_for(session, user.id)))

    return [
        Stage("score", "Scored", "Filtered against each account, then ranked by their own model.",
              entered=scored_entered, waiting=to_score, oldest_min=oldest_to_score),
        Stage("deliver", "Delivered", "Sent to Telegram as a card.",
              entered=delivered_entered, waiting=to_deliver, oldest_min=oldest_to_deliver),
    ]


def _oldest(current: float | None, candidate: float | None) -> float | None:
    """The longer of two waits, ignoring absent ones."""
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)


def _config(session: Session, user_id) -> dict | None:
    from libs.db.models import UserConfig  # noqa: PLC0415 - avoids an import cycle at module load

    return session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id))
