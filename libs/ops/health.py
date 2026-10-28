"""What the platform looks like right now, as numbers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, ProxyCredential, QueueJob, User, UserSecret
from libs.db.models.queue import LANES

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
    proxy_status: str | None


@dataclass
class PlatformHealth:
    checked_at: str
    lanes: list[LaneHealth]
    users_total: int
    users_active: int
    corpus_rows: int
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


def _proxy_statuses(session: Session) -> dict:
    """Every account's proxy state, as two queries for the platform rather than two per account."""
    proxies = {row.user_id: row for row in session.scalars(select(ProxyCredential))}
    with_secret = set(session.scalars(
        select(UserSecret.user_id).where(UserSecret.kind == "proxy")))
    return {user_id: (proxy.status if user_id in with_secret else "no_secret")
            for user_id, proxy in proxies.items()}


def user_health(session: Session) -> list[UserHealth]:
    """Per-account state, for the alerts that name a user.

    **Asked for the whole platform at once.** This was a loop of about seven queries an account; at
    fifty accounts and 2.65M state rows that measured 18.8 seconds against `health.service`'s
    60-second timeout. What it counted per account — matches scored above the account's cutoff and
    not yet sent — went with the scorer (ROADMAP Phase 8), and with it the last per-account query.
    """
    proxies = _proxy_statuses(session)
    return [UserHealth(user_id=str(user.id), email=user.email,
                       telegram_status=user.telegram_status,
                       # A row is not a credential.
                       proxy_status=proxies.get(user.id))
            for user in session.scalars(
                select(User).where(User.status == "active").order_by(User.email))]


def platform_health(session: Session, *, include_users: bool = True) -> PlatformHealth:
    """Everything, in one call. Serves the admin endpoint and the alert timer alike."""
    return PlatformHealth(
        checked_at=datetime.now(UTC).isoformat(),
        lanes=lane_health(session),
        users_total=session.scalar(select(func.count()).select_from(User)) or 0,
        users_active=session.scalar(
            select(func.count()).select_from(User).where(User.active)) or 0,
        corpus_rows=session.scalar(select(func.count()).select_from(CorpusJob)) or 0,
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
