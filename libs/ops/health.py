"""What the platform looks like right now, as numbers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import (
    CorpusJob,
    ProxyCredential,
    QueueJob,
    User,
    UserConfig,
    UserJobState,
    UserSecret,
)
from libs.db.models.queue import LANES
from libs.scoring.thresholds import cutoff_for
from libs.tenant.filters import (
    DEFAULT_MAX_AGE_HOURS,
    UserFilters,
    candidate_conditions,
)

# What a lane's oldest waiting job is *expected* to be, in minutes.
EXPECTED_WAIT_MIN = {"notify": 5, "train": 30, "docgen": 10, "label": 60, "process": 15,
                     "score": 10}

# The steady-state corpus projection from `PLATFORM_ARCHITECTURE.md` §4, in rows.
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
        """Four times the lane's normal wait."""
        return (self.oldest_ready_min or 0) > self.expected_wait_min * 4


@dataclass
class UserHealth:
    user_id: str
    email: str
    telegram_status: str
    model_version: int
    # Jobs above the notify cutoff that have been sitting undelivered.
    pending_matches: int
    hours_since_delivery: float | None
    proxy_status: str | None


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
    """Depth, dead letters, and the oldest thing waiting, per lane."""
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


def deliverable_window(config: dict | None) -> int:
    """How old a posting may be and still be worth sending to this account, in hours."""
    windows = [int(section.get("posted_within_hours") or 0)
               for section in (migrate_config(config or {})).get("sections") or []]
    return max([window for window in windows if window > 0], default=DEFAULT_MAX_AGE_HOURS)


def _proxy_status(session: Session, user_id, proxy) -> str | None:
    """What the panel should say about this account's proxy."""
    if proxy is None:
        return None
    stored = session.scalar(
        select(func.count()).select_from(UserSecret)
        .where(UserSecret.user_id == user_id, UserSecret.kind == "proxy")) or 0
    return proxy.status if stored else "no_secret"


def pending_matches(session: Session, user: User) -> int:
    """How many jobs this account is actually waiting on — the same pool the notifier would draw."""
    cutoff = cutoff_for(session, user.id)
    filters = UserFilters.from_config(
        session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id)))
    return session.scalar(
        select(func.count()).select_from(UserJobState)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user.id,
               UserJobState.notified_at.is_(None),
               UserJobState.score.is_not(None),
               UserJobState.score >= cutoff,
               *candidate_conditions(filters))) or 0


def user_health(session: Session) -> list[UserHealth]:
    """Per-account state, for the alerts that name a user."""
    out = []
    for user in session.scalars(select(User).where(User.status == "active").order_by(User.email)):
        pending = pending_matches(session, user)
        last_delivery = session.scalar(
            select(func.max(UserJobState.notified_at))
            .where(UserJobState.user_id == user.id))
        proxy = session.scalar(
            select(ProxyCredential).where(ProxyCredential.user_id == user.id))

        out.append(UserHealth(
            user_id=str(user.id),
            email=user.email,
            telegram_status=user.telegram_status,
            model_version=user.model_version,
            pending_matches=pending,
            hours_since_delivery=(
                round((datetime.now(UTC) - last_delivery).total_seconds() / 3600, 1)
                if last_delivery else None),
            # A row is not a credential.
            proxy_status=_proxy_status(session, user.id, proxy),
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
        # `pg_database_size` rather than a table sum: indexes, bloat and WAL are what fill a disk.
        postgres_bytes=int(session.scalar(
            select(cast(func.pg_database_size(func.current_database()), Float))) or 0),
        # A view, not a function.
        connections=int(session.scalar(text("SELECT count(*) FROM pg_stat_activity")) or 0),
        users=user_health(session) if include_users else [],
    )


def since(moment: datetime | None) -> timedelta | None:
    """Small helper, exported because both callers of this module want the same rounding."""
    return None if moment is None else datetime.now(UTC) - moment
