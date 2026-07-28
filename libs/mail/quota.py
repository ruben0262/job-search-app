"""The platform-wide sending cap.

Gmail caps a normal account near 500 recipients a day, and **the failure mode is silent rejection**
— messages stop arriving, nothing raises at send time, and the first symptom is a user saying they
never got a link. A cap the platform enforces itself turns that into a log line and a refusal we
can see.

Counted over a **rolling 24 hours** rather than a calendar day, because that is how the provider
actually meters it: sending 400 at 23:00 and 400 at 01:00 is 800 in two hours, and a midnight reset
would call it two compliant days.

**No recipient address is stored.** A cap needs a count, not a mailing list, and the per-address
protection already exists in the signup rate limiter. Keeping this table free of addresses means it
never becomes one more place a user's email lives.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import MailSend
from libs.mail.sender import EmailSender, MailError

logger = logging.getLogger(__name__)

# Below Gmail's own limit on purpose. The headroom absorbs a burst and leaves room for the provider
# counting something slightly differently from us — being refused by our own cap is recoverable,
# being silently throttled by Gmail is not.
DEFAULT_DAILY_CAP = 400
WINDOW = timedelta(hours=24)


class MailCapReached(MailError):
    """The platform has sent its daily allowance. Deliberately a `MailError`.

    Callers already treat a delivery failure as recoverable — the account still exists and the link
    can be requested again — so a cap needs no new handling anywhere, only a different message.
    """


def daily_cap(env: dict[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get("MAIL_DAILY_CAP")
    return int(raw) if raw else DEFAULT_DAILY_CAP


def sent_last_24h(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(MailSend)
        .where(MailSend.sent_at >= datetime.now(UTC) - WINDOW)) or 0


def send_within_cap(session: Session, mailer: EmailSender, to: str, subject: str, body: str,
                    *, purpose: str, cap: int | None = None) -> None:
    """Send, unless the platform is at its cap. Records the attempt either way.

    The row is written **before** the send and kept even when the send fails. A cap that only
    counted successes would let a provider outage burn the allowance twice — once failing, once
    retrying — and the provider counts attempts, not successes.
    """
    limit = cap if cap is not None else daily_cap()
    already = sent_last_24h(session)
    if already >= limit:
        logger.error("mail: platform cap reached (%d in 24h, limit %d) — not sending %s",
                     already, limit, purpose)
        raise MailCapReached(f"daily sending cap of {limit} reached")

    session.add(MailSend(purpose=purpose))
    session.flush()
    mailer.send(to, subject, body)
