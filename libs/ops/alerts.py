"""Which of the numbers need a person, and which are just numbers."""
from __future__ import annotations

from dataclasses import dataclass

from libs.ops.health import PlatformHealth

# The share of the database budget (`database_budget_mb`, a platform setting) that pages.
DATABASE_ALERT_FRACTION = 0.80


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
    used = health.database_bytes / health.database_budget_bytes
    if used > DATABASE_ALERT_FRACTION:
        alerts.append(Alert(
            "page", "database_size",
            f"MongoDB holds {health.database_bytes / 1024**2:.0f} MB, {used:.0%} of its "
            f"{health.database_budget_bytes // 1024**2} MB budget",
            "At the ceiling Atlas refuses every write, sign-ups included. Check the archive pass ran "
            "(`archive:` in the harvester log); if it did, keep jobs for fewer days in the admin "
            "panel, or move the cluster to a bigger tier and raise the budget there."))
    return alerts


def _user_alerts(health: PlatformHealth) -> list[Alert]:
    alerts = []
    for user in health.users:
        if user.telegram_status == "broken":
            alerts.append(Alert(
                "warn", "telegram_broken",
                "their bot can no longer message them",
                "Tell the user: they need to reconnect the bot. Nothing is being delivered until "
                "they do.", user.email))

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
