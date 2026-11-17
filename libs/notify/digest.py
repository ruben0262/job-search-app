"""The daily email: once a day per person with an alert that sends to it, at their own morning, listing what
each such alert caught since the last one (owner, 2026-11-17: email stays one a day, grouped by alert, or the
address is flagged for sending too much).

Independent of Telegram and the browser: it lists what was caught whether or not those jobs also went to a
phone. Scheduled by the notifier's sweep (`libs.queue.dispatch_due_digests`), not by a clock of its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pymongo.database import Database

from libs import limits
from libs.corpus.boards import board_name
from libs.db import find, now
from libs.db.models import Alert, CorpusJob, User, UserJobState
from libs.mail import DigestJob, EmailSender, MailError, digest_email, send_within_cap
from libs.matching import green_words
from libs.notify import site_url
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# The most jobs one alert lists in an email. The rest are a tap away on the site, which the email links.
DIGEST_LIMIT = 20


def due(db: Database, user: User, hour: int, *, moment: datetime | None = None) -> bool:
    """Whether this person's email is due: alerts not paused, past `hour` in their day, and none sent yet
    today. Whether an alert of theirs sends to it is the sweep's question."""
    if not user.active:
        return False
    local = (moment or now()).astimezone(ZoneInfo(user.timezone or "UTC"))
    if local.hour < hour:
        return False
    return user.email_digest_at is None or user.email_digest_at < limits.their_midnight(db, user.id)


def send_digest(db: Database, mailer: EmailSender, user: User) -> int:
    """Email what their emailing alerts caught since the last one (a day, the first time), each alert's under
    its name; how many jobs it listed. None caught, nothing is sent. Marked sent either way, so a failure
    costs one day, not a retry a minute."""
    since = user.email_digest_at or now() - timedelta(days=1)
    alerts = {alert.id: alert for alert in find(db, Alert, {"user_id": user.id, "channels": "email"},
                                                 sort=[("_id", 1)])}
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user.id, "alerted_at": {"$gte": since}, "alerts": {"$in": list(alerts)}},
        {"job_id": 1, "alerts": 1}, sort=[("alerted_at", -1)]))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"email_digest_at": now()}})
    if not rows:
        logger.info("digest: nothing new for %s since %s", user.id, since.isoformat())
        return 0
    jobs = {row["_id"]: row for row in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": [row["job_id"] for row in rows]}},
        {"title": 1, "company": 1, "location": 1, "source": 1, "terms": 1})}
    mine = set(green_words(TenantRepository(db).get_config(user.id)))
    shown: list[DigestJob] = []
    for alert in alerts.values():
        wanted = set(alert.keywords) | (mine if alert.search else set())
        found = [jobs[row["job_id"]] for row in rows if alert.id in row["alerts"] and row["job_id"] in jobs]
        shown += [DigestJob(title=job.get("title") or "A new job", alert=alert.name,
                            where=" · ".join(filter(None, (job.get("company"), job.get("location")))),
                            board=board_name(job.get("source") or ""),
                            words=tuple(sorted(wanted.intersection(job.get("terms") or ()))),
                            url=site_url(f"/#/feed?job={job['_id']}"))
                  for job in found[:DIGEST_LIMIT]]
    subject, text, html = digest_email(shown, total=len(jobs), site=site_url("/#/alerts"),
                                       settings=site_url("/#/alerts"))
    try:
        send_within_cap(db, mailer, user.email, subject, text, purpose="digest", html=html)
    except MailError:
        logger.warning("digest: the email to %s did not go; tomorrow's will try again", user.id,
                       exc_info=True)
        return 0
    logger.info("digest: emailed %s %d job(s) from %d alert(s)", user.id, len(shown),
                len({job.alert for job in shown}))
    return len(shown)
