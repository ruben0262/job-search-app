"""The `notify` lane worker, its two transports (Telegram and Web Push), and the sweep that feeds it."""
from __future__ import annotations

import json
import logging
import os
import sys

from pywebpush import WebPushException, webpush

from libs.db import database, ensure_schema, get
from libs.db.models import PushSubscription, QueueJob, User
from libs.mail import sender_from_env
from libs.notify.digest import send_digest
from libs.obs import configure
from libs.queue.queue import dispatch_due_digests, dispatch_due_notifications
from libs.queue.worker import run_worker
from libs.secrets import SecretStore, install
from libs.telegram import SEND_TIMEOUT_SEC, send
from services.notifier.deliver import deliver

# How long a push service holds an alert for a phone that is asleep or offline: a day, because a job
# alert is still worth reading the next morning.
PUSH_TTL_SEC = 24 * 3600

# How often the sweep asks "has the corpus grown for anybody since their last run"
# (`libs.queue.queue`). A posting stored now reaches a person within about this long.
DISPATCH_EVERY_SEC = 60.0

logger = logging.getLogger(__name__)


def push(subscription: PushSubscription, payload: dict) -> bool:
    """One alert to one browser. False when its push service says the subscription is gone."""
    try:
        webpush({"endpoint": subscription.endpoint,
                 "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                json.dumps(payload),
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                # A fresh dict every call: `webpush` writes the audience and expiry into it.
                vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
                timeout=SEND_TIMEOUT_SEC, ttl=PUSH_TTL_SEC)
    except WebPushException as exc:
        # 404 and 410 are an unsubscribed or expired browser. Anything else is a fault, and raised.
        if exc.status_code in (404, 410):
            return False
        raise
    return True


def _dispatch(db) -> None:
    """The sweep: anybody the corpus has grown for since their last run, and anybody whose daily
    email is due. Silent when it finds none."""
    enqueued, emails = dispatch_due_notifications(db), dispatch_due_digests(db)
    if enqueued or emails:
        logger.info("sweep: enqueued %d notify job(s) and %d daily email(s)", enqueued, emails)


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
    vapid = os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_SUBJECT")
    with SecretStore(db).use(job.user_id, "telegram_bot") as secret:
        token = secret.reveal() if secret else ""
        deliver(db, job.user_id,
                telegram=(lambda chat, text, buttons: send(token, chat, text, buttons)) if token else None,
                push=push if vapid else None)


def main(argv: list[str] | None = None) -> int:
    configure("notifier")
    install()
    ensure_schema()

    if "--tick" in (argv if argv is not None else sys.argv[1:]):
        db = database()
        enqueued, emails = dispatch_due_notifications(db), dispatch_due_digests(db)
        logger.info("dispatcher: enqueued %d notify job(s) and %d daily email(s)", enqueued, emails)
        return 0

    # The dispatcher runs inside the worker, not on its own timer.
    run_worker(database(), "notify", os.environ.get("WORKER_NAME", "notifier-1"), handle,
               periodic=_dispatch, periodic_every=DISPATCH_EVERY_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
