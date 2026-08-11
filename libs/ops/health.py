"""What the platform looks like right now, as numbers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, literal, select, text
from sqlalchemy.orm import Session

from libs.db.models import (
    CorpusJob,
    ProxyCredential,
    QueueJob,
    User,
    UserJobState,
    UserSecret,
)
from libs.db.models.queue import LANES
from libs.scoring.thresholds import cutoff_for
from libs.tenant.filters import (
    PlatformWindows,
    candidate_conditions,
    filters_for,
    pending_match_conditions,
    platform_windows,
)

# What a lane's oldest waiting job is *expected* to be, in minutes.
EXPECTED_WAIT_MIN = {"notify": 5, "train": 30, "docgen": 10, "label": 60, "process": 15,
                     "score": 10}

# The steady-state corpus projection from `PLATFORM_ARCHITECTURE.md` §4, in rows.
CORPUS_PROJECTION_ROWS = 400_000

# How many owed matches are worth counting per account before the answer stops changing what an
# operator does. `pending_matches` has exactly two consumers, both in `libs.ops.alerts`: a `> 0`
# test and the number in the message. Nobody acts differently on 400 than on 4,000.
#
# **Bounded because the cost is otherwise proportional to how badly delivery is broken.** Counting
# every owed row makes the health check slowest at precisely the moment it matters most — an outage
# that stops delivery for a day leaves thousands of rows owed per account, and the monitor that
# should report it is the thing that then times out. Measured at fifty accounts and 2.65M state
# rows: 121 ms per account counting them all.
PENDING_MATCH_CEILING = 200


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


def pending_matches(session: Session, user: User, *,
                    windows: PlatformWindows | None = None) -> int:
    """How many jobs this account is actually waiting on — the same pool the notifier would draw.

    **`pending_match_conditions` is that pool, and this asked its own question until 2026-08-11.**
    It omitted `retired_at IS NULL`, which is the exact clause added on 2026-08-08 after an account
    showed "99 matches waiting" with all 99 retired. The notifier was switched to the shared
    definition then and this caller was not, so the phantom stayed alive on the page that pages an
    operator — reporting work the notifier had correctly abandoned, which is a `silent_feed` alert
    for a healthy account.

    `candidate_conditions` stays on top of it deliberately, and is the one thing this asks that the
    feed does not: it answers "could this account still be shown this posting", so a row the filter
    has since aged out is not counted as owed. The feed does not need that because the sweep retires
    those rows first, by the same rule.
    """
    cutoff = cutoff_for(session, user.id)
    filters = filters_for(session, user, windows=windows)
    # Counted through a bounded subquery rather than over the whole set: Postgres stops reading at
    # the ceiling, so a broken feed costs the same as a healthy one. `ix_ujs_owed` is what makes the
    # far more common answer — nothing owed — a seek that touches no corpus rows at all.
    owed = (
        select(literal(1))
        .select_from(UserJobState)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user.id,
               *pending_match_conditions(cutoff),
               *candidate_conditions(filters))
        .limit(PENDING_MATCH_CEILING)
        .subquery())
    return session.scalar(select(func.count()).select_from(owed)) or 0


def _last_deliveries(session: Session) -> dict:
    """Every account's most recent delivery, in one grouped query rather than one query each."""
    return dict(session.execute(
        select(UserJobState.user_id, func.max(UserJobState.notified_at))
        .where(UserJobState.notified_at.is_not(None))
        .group_by(UserJobState.user_id)).all())


def _proxy_statuses(session: Session) -> dict:
    """Every account's proxy state, as two queries for the platform rather than two per account."""
    proxies = {row.user_id: row for row in session.scalars(select(ProxyCredential))}
    with_secret = set(session.scalars(
        select(UserSecret.user_id).where(UserSecret.kind == "proxy")))
    return {user_id: (proxy.status if user_id in with_secret else "no_secret")
            for user_id, proxy in proxies.items()}


def user_health(session: Session) -> list[UserHealth]:
    """Per-account state, for the alerts that name a user.

    **Everything that can be asked for the whole platform at once is.** This was a loop of about
    seven queries an account, three of which returned the same platform-wide answer every time, and
    one of which sequentially scanned `user_job_state` in full. At fifty accounts and 2.65M state
    rows that measured **18.8 seconds and 351 queries**, against `health.service`'s 60-second
    timeout — the monitor would have been the first thing to break as the platform grew, which is
    the failure `OPERATIONS_PLAN.md` §1 is about.

    What is left in the loop is per-account by nature: this account's config and its own cutoff.
    """
    windows = platform_windows(session)
    deliveries = _last_deliveries(session)
    proxies = _proxy_statuses(session)

    out = []
    for user in session.scalars(select(User).where(User.status == "active").order_by(User.email)):
        last_delivery = deliveries.get(user.id)
        out.append(UserHealth(
            user_id=str(user.id),
            email=user.email,
            telegram_status=user.telegram_status,
            model_version=user.model_version,
            pending_matches=pending_matches(session, user, windows=windows),
            hours_since_delivery=(
                round((datetime.now(UTC) - last_delivery).total_seconds() / 3600, 1)
                if last_delivery else None),
            # A row is not a credential.
            proxy_status=proxies.get(user.id),
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
