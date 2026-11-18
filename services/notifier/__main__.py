"""The `notify` lane worker, its two transports (Telegram and Web Push), and the sweep that feeds it: the
alerts' pass over new jobs (`libs.alerts.route`) and the daily emails that are due."""
from __future__ import annotations

import logging
import os
import sys

from libs.alerts import EVERY_SECONDS, route
from libs.db import database, ensure_schema, get
from libs.db.models import QueueJob, User
from libs.mail import sender_from_env
from libs.notify.digest import send_digest
from libs.notify.push import private_key, send
from libs.obs import configure
from libs.queue.queue import dispatch_due_digests
from libs.queue.worker import run_worker
from libs.secrets import SecretStore, install, redacting
from libs.telegram import send as telegram
from services.notifier.deliver import deliver

# How often the sweep runs: the alerts' own interval (`libs.alerts.EVERY_SECONDS`), which their lease is too.
DISPATCH_EVERY_SEC = EVERY_SECONDS

logger = logging.getLogger(__name__)


def _dispatch(db) -> None:
    """The sweep: the alerts' pass, which queues whoever it caught a job for, and anybody whose daily email is
    due. Silent when it finds none: the pass says what it caught itself."""
    route(db)
    if emails := dispatch_due_digests(db):
        logger.info("sweep: enqueued %d daily email(s)", emails)


_MAILER = None


def _mailer():
    """One sender per process, built on first use: the same SMTP settings as the API's."""
    global _MAILER  # noqa: PLW0603
    if _MAILER is None:
        _MAILER = sender_from_env()
    return _MAILER


def handle(db, job: QueueJob) -> None:
    """One person's run, through their own Telegram bot and every browser they subscribed — or their
    daily email, when that is what was queued."""
    if (job.payload or {}).get("digest"):
        user = get(db, User, job.user_id)
        if user is not None:
            send_digest(db, _mailer(), user)
        return
    key = private_key(db)
    with SecretStore(db).use(job.user_id, "telegram_bot") as secret, redacting(key):
        token = secret.reveal() if secret else ""
        chat = (lambda chat_id, text, buttons: telegram(token, chat_id, text, buttons)) if token else None
        deliver(db, job.user_id, telegram=chat, push=lambda browser, alert: send(browser, alert, key))


def main(argv: list[str] | None = None) -> int:
    configure("notifier")
    install()
    ensure_schema()

    if "--tick" in (argv if argv is not None else sys.argv[1:]):
        db = database()
        caught, emails = route(db), dispatch_due_digests(db)
        logger.info("dispatcher: %d job(s) caught by alerts, %d daily email(s) enqueued", caught, emails)
        return 0

    # The dispatcher runs inside the worker, not on its own timer.
    run_worker(database(), "notify", os.environ.get("WORKER_NAME", "notifier-1"), handle,
               periodic=_dispatch, periodic_every=DISPATCH_EVERY_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
