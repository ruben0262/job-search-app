"""The daily email of new jobs: one a day per person who turned it on, at their own morning.

Independent of Telegram and the browser: it lists what was found since the last one, whether or not
those jobs also went to a phone. Scheduled by the notifier's sweep (`libs.queue.dispatch_due_digests`),
not by a clock of its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pymongo.database import Database

from libs import limits
from libs.corpus.boards import board_name
from libs.db import now
from libs.db.models import User
from libs.mail import DigestJob, EmailSender, MailError, digest_email, send_within_cap
from libs.matching import matching_jobs
from libs.notify import site_url

logger = logging.getLogger(__name__)

# The most jobs one email lists. The rest are a tap away on the site, which the email links.
DIGEST_LIMIT = 20


def due(db: Database, user: User, hour: int, *, moment: datetime | None = None) -> bool:
    """Whether this person's email is due: turned on, alerts not paused, past `hour` in their day,
    and none sent yet today."""
    if not (user.email_alerts and user.active):
        return False
    local = (moment or now()).astimezone(ZoneInfo(user.timezone or "UTC"))
    if local.hour < hour:
        return False
    return user.email_digest_at is None or user.email_digest_at < limits.their_midnight(db, user.id)


def send_digest(db: Database, mailer: EmailSender, user: User) -> int:
    """Email the jobs found since the last one (a day, the first time); how many it listed. None
    found, nothing is sent. Marked sent either way, so a failure costs one day, not a retry a minute."""
    since = user.email_digest_at or now() - timedelta(days=1)
    # Described since, not found since: a job is in the email once it has its whole text.
    jobs = matching_jobs(db, user, described_since=since)
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"email_digest_at": now()}})
    if not jobs:
        logger.info("digest: nothing new for %s since %s", user.id, since.isoformat())
        return 0
    shown = [DigestJob(title=match.job.title or "A new job",
                       where=" · ".join(part for part in (match.job.company, match.job.location) if part),
                       board=board_name(match.job.source or ""), words=match.words,
                       url=site_url(f"/#/feed?job={match.job.id}"))
             for match in jobs[:DIGEST_LIMIT]]
    subject, text, html = digest_email(shown, total=len(jobs), site=site_url(),
                                       settings=site_url("/#/settings"))
    try:
        send_within_cap(db, mailer, user.email, subject, text, purpose="digest", html=html)
    except MailError:
        logger.warning("digest: the email to %s did not go; tomorrow's will try again", user.id,
                       exc_info=True)
        return 0
    logger.info("digest: emailed %s %d job(s)", user.id, len(shown))
    return len(shown)
