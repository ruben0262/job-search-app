"""Which of the numbers need a person, and which are just numbers."""
from __future__ import annotations

from dataclasses import dataclass

from libs.ops.health import PENDING_MATCH_CEILING, PlatformHealth

# Disk headroom before a full disk stops being hypothetical.
POSTGRES_DISK_BUDGET_BYTES = 60 * 1024**3
POSTGRES_DISK_ALERT_FRACTION = 0.80

# No delivery in this long, *with matches waiting*, is the silent-feed failure. Delivery is
# event-driven, so any gap this size means the notify lane is not running rather than not due.
SILENT_FEED_HOURS = 1

# Corpus growth past this multiple of projection means the retention trim is not running.
CORPUS_GROWTH_ALERT_FACTOR = 1.5


@dataclass(frozen=True)
class Alert:
    # `page` needs somebody now; `warn` needs somebody today.
    severity: str
    key: str
    text: str
    # Written for whoever is woken up. "Look into it" is not an action; naming the likely cause is.
    action: str
    user_email: str | None = None

    @property
    def pages(self) -> bool:
        return self.severity == "page"


def evaluate(health: PlatformHealth) -> list[Alert]:
    """Every condition that currently holds, most severe first."""
    alerts: list[Alert] = []
    alerts += _queue_alerts(health)
    alerts += _capacity_alerts(health)
    alerts += _user_alerts(health)
    return sorted(alerts, key=lambda alert: 0 if alert.pages else 1)


def _queue_alerts(health: PlatformHealth) -> list[Alert]:
    alerts = []
    for lane in health.lanes:
        if lane.dead:
            # A dead letter is always a bug or a broken user credential, and both need looking at.
            alerts.append(Alert(
                "page", f"dead_letter:{lane.lane}",
                f"{lane.dead} job(s) dead-lettered in the {lane.lane} lane",
                "Read the failure on the row: a bug, or a user's credential stopped working."))
        if lane.stalled:
            alerts.append(Alert(
                "warn", f"stalled:{lane.lane}",
                f"the oldest ready {lane.lane} job has waited {lane.oldest_ready_min} minutes "
                f"(expected under {lane.expected_wait_min})",
                f"Check the {lane.lane} worker is running and claiming."))
    return alerts


def _capacity_alerts(health: PlatformHealth) -> list[Alert]:
    alerts = []
    used = health.postgres_bytes / POSTGRES_DISK_BUDGET_BYTES
    if used > POSTGRES_DISK_ALERT_FRACTION:
        alerts.append(Alert(
            "page", "postgres_disk",
            f"Postgres is using {health.postgres_bytes / 1024**3:.1f} GB, "
            f"{used:.0%} of its budget",
            "Everything is in one database by design, so this is the whole platform. Check the "
            "archive pass ran (`archive:` in the harvester log), then the disk."))

    if health.corpus_rows > health.corpus_projection_rows * CORPUS_GROWTH_ALERT_FACTOR:
        alerts.append(Alert(
            "warn", "corpus_growth",
            f"the corpus holds {health.corpus_rows:,} rows against a projection of "
            f"{health.corpus_projection_rows:,}",
            "The 30-day archive pass is probably not running — it moves aged-out postings to "
            "`ARCHIVE_DIR` and deletes them (`libs.corpus.archive`). Left alone this is a full disk "
            "in a few months, and nothing else will report it."))
    return alerts


def _user_alerts(health: PlatformHealth) -> list[Alert]:
    alerts = []
    for user in health.users:
        # The qualifier is the alert.
        silent = (user.pending_matches > 0
                  and (user.hours_since_delivery is None
                       or user.hours_since_delivery > SILENT_FEED_HOURS))
        if silent:
            waited = ("never" if user.hours_since_delivery is None
                      else f"{user.hours_since_delivery:.0f}h ago")
            # `pending_matches` stops counting at its ceiling so the health check cannot be made slow
            # by the outage it is reporting. Said as "200+" rather than "200", because a number that
            # is really a floor and does not say so is the kind of thing an operator plans against.
            waiting = (f"{PENDING_MATCH_CEILING}+" if user.pending_matches >= PENDING_MATCH_CEILING
                       else str(user.pending_matches))
            alerts.append(Alert(
                "page", "silent_feed",
                f"{waiting} matching job(s) waiting and last delivery was {waited}",
                # Where the answer actually is, in the order it is usually in.
                # Where the answer actually is. This used to name a `dropped {...}` log line that
                # does not exist anywhere in the notifier, so the first step of the remedy was a
                # dead end — which is most of why this alert went unactioned.
                "Grep the notifier log for this account's id: `notified <id> with N item(s)` is a "
                "delivery, and `sweep: retired` means matches were given up on. If neither appears, "
                "check the notify lane's depth and this account's Telegram status.", user.email))

        if user.telegram_status == "broken":
            alerts.append(Alert(
                "warn", "telegram_broken",
                "their bot can no longer message them",
                "Tell the user: they need to reconnect the bot. Nothing is being delivered until "
                "they do.", user.email))

        if user.proxy_status == "quarantined":
            alerts.append(Alert(
                "warn", "proxy_quarantined",
                "their proxy is quarantined",
                "LinkedIn is not being fetched for this account. Tell the user.", user.email))
    return alerts


def render(alerts: list[Alert]) -> str:
    """Plain text for an email or a terminal."""
    if not alerts:
        return "No alerts. Everything the platform can check is within bounds."

    lines = []
    for alert in alerts:
        marker = "PAGE" if alert.pages else "warn"
        who = f" [{alert.user_email}]" if alert.user_email else ""
        lines.append(f"{marker}{who} {alert.text}\n     -> {alert.action}")
    return "\n".join(lines)
