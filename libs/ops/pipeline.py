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

**Delivery is the exception, and has to be.** It is capped at `items_per_run` a cycle on purpose, so
a queue behind it is the design working rather than a fault, and an age-based colour there would be
red every day of the platform's life. What is genuinely wrong is a posting the model matched, that
nobody was ever shown, that is now too old to send — work the platform did and threw away. That is
what colours the delivery row, and nothing else does.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, QueueJob, User, UserJobState
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
    # How many are sitting in it, waiting for the next step to take them. **`None`, not 0, where the
    # stage has no queue at all**: gathering and storing are one statement, so there is no moment at
    # which a posting is between them, and a confident zero there is the same lie as a confident
    # zero anywhere else on this page.
    waiting: int | None
    # How long the oldest of those has waited, in minutes.
    oldest_min: float | None
    # **Matched, never sent, and now too old to send** — work the platform did and threw away. Only
    # delivery can produce this, because it is the only stage with a deliberate rate cap in front of
    # it. `None` everywhere else, and that is a different statement from zero.
    expired: int | None = None

    @property
    def verdict(self) -> str:
        """green, amber or red.

        **Age, not size**, for every stage that drains as fast as it can — a thousand rows moving
        through is healthy and forty stuck for a day is not.

        Delivery is judged differently and has to be: `items_per_run` caps it at ten a cycle *on
        purpose*, so a queue behind it is the design working, and colouring that red would cry wolf
        every day of the platform's life. What is not the design working is a posting the model
        matched, that nobody was ever shown, that is now too old to send. That is work lost, which
        is the same bar `ERROR` is held to (CLAUDE.md §8), so it is the only thing that colours this
        row.
        """
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
        # **One row, because it is one statement.** Gathering and storing were drawn as two stages
        # and they are not: `fetched_at` and `first_seen_at` are set by the same `INSERT`, so no
        # posting is ever between them and the two counts can only ever be equal — except across
        # 2026-08-04, before which `fetched_at` did not exist, where the page showed *fewer
        # gathered than stored*. An impossible ordering, presented without comment, produced
        # entirely by a row that was measuring its neighbour (CLAUDE.md §6: can I not add it).
        #
        # Dedup is what would make them genuinely different, and it leaves no row behind to count —
        # a rejected duplicate is visible in the harvest log and nowhere else.
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
    """What the queue is doing right now, per lane.

    **The stages say where postings are; this says whether anything is turning the handle.** They
    fail independently: a stage can be empty because the work is done or because the worker feeding
    it died, and only the lane tells them apart. It is also the one place a *failure* is visible —
    a dead-lettered job is work abandoned, and the stage counts alone would show it as a queue that
    simply never drains.

    `running` is the number that moves second to second, which is what makes the fifteen-minute tick
    watchable rather than inferred from a count that jumps.
    """
    rows = session.execute(
        select(QueueJob.lane,
               func.count().filter(QueueJob.state == "ready").label("ready"),
               func.count().filter(QueueJob.state == "running").label("running"),
               func.count().filter(QueueJob.state == "dead").label("dead"),
               func.count().filter((QueueJob.state == "done")
                                   & (QueueJob.finished_at >= since)).label("done"))
        .group_by(QueueJob.lane).order_by(QueueJob.lane)).all()
    return [{"lane": row.lane, "ready": row.ready, "running": row.running,
             "dead": row.dead, "done": row.done} for row in rows]


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

    to_score = to_deliver = expired = 0
    oldest_to_score = oldest_to_deliver = None

    for user in session.scalars(select(User).where(User.active)):
        filters = UserFilters.from_config(_config(session, user.id))
        conditions = candidate_conditions(filters)
        cutoff = cutoff_for(session, user.id)

        # **The count and the age must ask the same question.** They did not: the count carried the
        # account's filters and the age did not, so the page reported 142 waiting and an oldest of
        # 138 hours — a number belonging to a row the count had excluded. Two answers to one
        # question, which is the defect this project has shipped more than any other (CLAUDE.md §5).
        # They now share a condition list for exactly that reason.
        unscored = (CorpusJob.embedding.is_not(None),
                    UserJobState.scored_model_version.is_(None)
                    | (UserJobState.scored_model_version != user.model_version),
                    *conditions)
        scoring = (select(func.count()).select_from(CorpusJob)
                   .outerjoin(UserJobState, (UserJobState.job_id == CorpusJob.id)
                              & (UserJobState.user_id == user.id)))
        to_score += session.scalar(scoring.where(*unscored)) or 0
        # **Was never computed at all**, so this stage could not turn amber or red whatever was
        # queued behind it — the one colour that cannot fire is the one nobody notices is missing.
        oldest_to_score = _oldest(oldest_to_score, session.scalar(
            select(func.min(CorpusJob.first_seen_at))
            .outerjoin(UserJobState, (UserJobState.job_id == CorpusJob.id)
                       & (UserJobState.user_id == user.id))
            .where(*unscored)))

        # **What delivery would actually consider**, which is the same pool `pending_feed` draws
        # from — score above this account's own tuned cutoff, never sent, and a candidate row.
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

        # **The same pool, minus the freshness condition, and that is the whole measurement.** A
        # posting this account matched, that was never sent, and that is now too old to send. The
        # platform embedded it, scored it and ranked it, and nobody was ever shown it.
        #
        # Delivery is capped at `items_per_run` a cycle deliberately, so a queue behind it is
        # normal and says nothing. This says whether the cap is *lower than the account's own match
        # rate*, which is the condition under which the queue never drains and the tail dies of old
        # age — measured live on 2026-08-06 at 576 postings in 48 hours for one account.
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
    """The earlier of two timestamps, ignoring absent ones.

    Kept as timestamps and converted to an age once at the end. Converting per account and then
    taking a maximum works, but only while every account is measured in the same instant — and the
    loop is exactly the place where that stops being true as accounts are added.
    """
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
