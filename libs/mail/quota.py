"""The platform-wide sending cap."""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import MailSend
from libs.mail.sender import EmailSender, MailError

logger = logging.getLogger(__name__)

# Below the provider's own limit: Zoho allows a few hundred a day, Gmail nearer 500.
DEFAULT_DAILY_CAP = 400
WINDOW = timedelta(hours=24)


class MailCapReached(MailError):
    """The platform has sent its daily allowance."""


def daily_cap(env: dict[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get("MAIL_DAILY_CAP")
    return int(raw) if raw else DEFAULT_DAILY_CAP


def sent_last_24h(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(MailSend)
        .where(MailSend.sent_at >= datetime.now(UTC) - WINDOW)) or 0


def send_within_cap(session: Session, mailer: EmailSender, to: str, subject: str, body: str,
                    *, purpose: str, cap: int | None = None) -> None:
    """Send, unless the platform is at its cap."""
    limit = cap if cap is not None else daily_cap()
    already = sent_last_24h(session)
    if already >= limit:
        logger.error("mail: platform cap reached (%d in 24h, limit %d) — not sending %s",
                     already, limit, purpose)
        raise MailCapReached(f"daily sending cap of {limit} reached")

    session.add(MailSend(purpose=purpose))
    session.flush()
    mailer.send(to, subject, body)
