"""What the platform looks like right now, as numbers.

**Every failure here is silent** (`OPERATIONS_PLAN.md` §1), and that is the whole reason this
module exists. A harvester that stops finding jobs, a queue that stalls, a retention trim that
quietly stopped running — none of them raise, none of them page anybody, and each one is discovered
weeks later by a user asking why their feed went quiet. Numbers make them visible; `libs.ops.alerts`
decides which ones need a person.

**Read-only, and cheap enough to serve in a request.** Every query here is a count or a max over an
indexed column. Nothing scans the corpus, because a health check that costs more than the thing it
watches is a health check somebody turns off.

In `libs` rather than in a service because two callers need identical answers: the admin endpoint,
and the timer that decides whether to wake somebody up. Two implementations would disagree exactly
when it mattered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, ProxyCredential, QueueJob, User, UserJobState
from libs.db.models.queue import LANES

# What a lane's oldest waiting job is *expected* to be, in minutes. Exceeded by 4x is a stall
# (`OPERATIONS_PLAN.md` §4). These are the lane's normal latency, not its timeout: `notify` runs on
# a 60-second dispatcher, `label` and `train` are queued bursts that legitimately wait.
EXPECTED_WAIT_MIN = {"notify": 5, "train": 30, "docgen": 10, "label": 60, "process": 15,
                     "score": 10}

# The steady-state corpus projection from `PLATFORM_ARCHITECTURE.md` §4, in rows. Growth past 1.5x
# means the 90-day retention trim is not running — a slow, silent failure that shows up as a full
# disk months later, which is precisely the class this file exists to catch.
CORPUS_PROJECTION_ROWS = 400_000


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
        """Four times the lane's normal wait. Not a fixed number: a `notify` job waiting an hour is
        broken, a `label` job waiting an hour is a queue doing its job."""
        return (self.oldest_ready_min or 0) > self.expected_wait_min * 4


@dataclass
class UserHealth:
    user_id: str
    email: str
    telegram_status: str
    model_version: int
    labels_stale: int
    # Jobs above the notify cutoff that have been sitting undelivered. The qualifier that makes
    # "no delivery in 24 h" a real alert rather than one that fires for every narrow config.
    pending_matches: int
    hours_since_delivery: float | None
    proxy_status: str | None
    proxy_mb_today: float


@dataclass
class PlatformHealth:
    checked_at: str
    lanes: list[LaneHealth]
    users_total: int
    users_active: int
    corpus_rows: int
    corpus_embedded: int
    corpus_projection_rows: int
    postgres_bytes: int
    connections: int
    users: list[UserHealth] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def lane_health(session: Session) -> list[LaneHealth]:
    """Depth, dead letters, and the oldest thing waiting, per lane.

    Every lane is reported even when empty. A lane missing from the output because it had no rows
    is indistinguishable from a lane whose worker died, and the second one matters.
    """
    rows = session.execute(
        select(QueueJob.lane, QueueJob.state, func.count(),
               func.min(QueueJob.run_after).filter(QueueJob.state == "ready"))
        .group_by(QueueJob.lane, QueueJob.state)).all()

    counts: dict[str, dict[str, int]] = {lane: {} for lane in LANES}
    oldest: dict[str, datetime | None] = dict.fromkeys(LANES)
    for lane, state, count, min_run_after in rows:
        counts.setdefault(lane, {})[state] = count
        if min_run_after and (oldest.get(lane) is None or min_run_after < oldest[lane]):
            oldest[lane] = min_run_after

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


def user_health(session: Session, *, cutoff: float = 60.0) -> list[UserHealth]:
    """Per-account state, for the alerts that name a user.

    Bounded by the number of accounts, which is the point of an invite-only launch — this becomes a
    single aggregate query well before it becomes slow, and pretending otherwise now would be
    optimising a list of five rows.
    """
    out = []
    for user in session.scalars(select(User).where(User.status == "active").order_by(User.email)):
        pending = session.scalar(
            select(func.count()).select_from(UserJobState)
            .where(UserJobState.user_id == user.id,
                   UserJobState.notified_at.is_(None),
                   UserJobState.score >= cutoff)) or 0
        last_delivery = session.scalar(
            select(func.max(UserJobState.notified_at))
            .where(UserJobState.user_id == user.id))
        stale = session.scalar(
            select(func.count()).select_from(UserJobState)
            .where(UserJobState.user_id == user.id,
                   UserJobState.scored_model_version < user.model_version)) or 0
        proxy = session.scalar(
            select(ProxyCredential).where(ProxyCredential.user_id == user.id))

        out.append(UserHealth(
            user_id=str(user.id),
            email=user.email,
            telegram_status=user.telegram_status,
            model_version=user.model_version,
            labels_stale=stale,
            pending_matches=pending,
            hours_since_delivery=(
                round((datetime.now(UTC) - last_delivery).total_seconds() / 3600, 1)
                if last_delivery else None),
            proxy_status=proxy.status if proxy else None,
            proxy_mb_today=float(proxy.mb_used_today) if proxy else 0.0,
        ))
    return out


def platform_health(session: Session, *, include_users: bool = True) -> PlatformHealth:
    """Everything, in one call. Serves the admin endpoint and the alert timer alike."""
    corpus_rows = session.scalar(select(func.count()).select_from(CorpusJob)) or 0
    embedded = session.scalar(
        select(func.count()).select_from(CorpusJob)
        .where(CorpusJob.embedding.is_not(None))) or 0

    return PlatformHealth(
        checked_at=datetime.now(UTC).isoformat(),
        lanes=lane_health(session),
        users_total=session.scalar(select(func.count()).select_from(User)) or 0,
        users_active=session.scalar(
            select(func.count()).select_from(User).where(User.active)) or 0,
        corpus_rows=corpus_rows,
        corpus_embedded=embedded,
        corpus_projection_rows=CORPUS_PROJECTION_ROWS,
        # `pg_database_size` rather than a sum over tables: indexes, bloat and WAL are what actually
        # fill a disk, and a table-sum reads reassuringly small right up until the disk is full.
        postgres_bytes=int(session.scalar(
            select(cast(func.pg_database_size(func.current_database()), Float))) or 0),
        # A view, not a function. Counted because connection exhaustion is a failure that presents
        # as unrelated things breaking — the test suite found exactly that on its own first run.
        connections=int(session.scalar(text("SELECT count(*) FROM pg_stat_activity")) or 0),
        users=user_health(session) if include_users else [],
    )


def since(moment: datetime | None) -> timedelta | None:
    """Small helper, exported because both callers of this module want the same rounding."""
    return None if moment is None else datetime.now(UTC) - moment
