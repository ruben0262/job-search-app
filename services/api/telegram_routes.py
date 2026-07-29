"""Telegram button taps, arriving as a webhook.

**A webhook rather than polling**, because the API is publicly reachable through the tunnel: one
request per tap, instead of a process asking "anything yet?" forever and a per-user `getUpdates`
loop that would need its own worker and its own offset bookkeeping.

## Three things stand between a stranger and somebody else's training data

1. **A per-user path secret.** Every user runs their own bot, so the URL is what says *which*
   account a tap belongs to — a single shared path could only answer that from the request body,
   which is attacker-supplied.
2. **Telegram's own `secret_token` header**, checked in constant time. The path can leak: into a
   proxy log, a browser history, a screenshot. The header is what makes a leaked path insufficient.
3. **The tapper must be the account owner.** A feed channel has other members. Without this check,
   anybody who joined the channel to browse jobs could teach somebody else's model — which is not a
   privacy leak but is a slow, invisible corruption of the one thing the product is built on. The
   reference instance learned this and checks it too.

Neither of the first two is sufficient alone, and the third is about a different attacker entirely:
one who is *supposed* to see the messages.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User
from libs.feedback import ACTIONS, record
from libs.obs import audit
from libs.queue import enqueue, jobs_today
from libs.tenant.quotas import QuotaExceeded, quotas_for
from services.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

# A retrain is debounced so a burst of taps produces one fit, ten minutes after the last
# (`SCHEDULER_PLAN.md` §5). Below this many new feedback labels there is nothing worth refitting —
# a model rebuilt on one extra label is not a different model, it is the same CPU spent twice.
TRAIN_DEBOUNCE_MIN = 10
TRAIN_MIN_NEW_LABELS = 5

# `callback_data` is capped at 64 bytes by Telegram, so it carries an action and a job id and
# nothing else. Everything about *who* comes from the URL and the header.
# `ap` and `sk` are still understood: cards delivered before the two-button change are sitting in
# channels with those buttons on them, and a tap months later must not read as an unknown action.
CALLBACK_PREFIX = {"ap": "applied", "sk": "skipped", "bc": "build_cv", "co": "build_cover"}


def _authenticate(secret: str, db: Session, header_token: str | None) -> User:
    """Resolve the path secret to a user, and check Telegram's header. 404 on any failure.

    404 rather than 401 throughout: a probe should not learn that a path exists, nor which of the
    two checks it failed.
    """
    user = db.scalar(select(User).where(User.telegram_webhook_secret == secret))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    # `compare_digest`, not `==`. The comparison is against a value an attacker supplies and can
    # vary freely, which is the textbook shape for a timing oracle.
    if not header_token or not hmac.compare_digest(header_token, secret):
        logger.warning("telegram: webhook for %s had a bad secret header", user.id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, db: Session = Depends(get_db),
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    """One button tap.

    **Always answers 200 once authenticated**, even when the tap does nothing. Telegram retries a
    non-2xx, and retrying a tap that was already recorded is pointless at best; the response body
    says what happened, for anybody reading a log.
    """
    user = _authenticate(secret, db, x_telegram_bot_api_secret_token)
    update = await request.json()

    query = update.get("callback_query")
    if not query:
        # A plain message, a channel post, whatever else the bot is subscribed to. Not an error:
        # acknowledging it is how Telegram stops resending it.
        return {"status": "ignored"}

    tapper = str((query.get("from") or {}).get("id") or "")
    if not _is_owner(user, tapper):
        # A channel member who is not the account owner. Silently ignoring would be worse than
        # refusing — the tap looks like it worked and teaches nothing — so it is logged and the
        # popup below tells them.
        audit(logger, "telegram.foreign_tap", user=user.id)
        return {"status": "not_yours",
                "answer": "Only the owner of this feed can use these buttons."}

    action, job_id = _parse(str(query.get("data") or ""))
    if action is None:
        return {"status": "unknown_action"}

    if action in ("build_cv", "build_cover"):
        kind = "cover" if action == "build_cover" else "cv"

        # **The quota is checked here, at the admission point**, never inside the worker
        # (`TENANT_SAFETY.md` §3): a limit checked halfway through a build has already spent the
        # Claude call it was protecting. The popup names the limit, because a button that silently
        # does nothing is how somebody concludes the product is broken.
        try:
            quotas_for(db, user.id).check("cv_builds_per_day", jobs_today(db, "docgen", user.id))
        except QuotaExceeded as exc:
            audit(logger, "telegram.build_over_quota", user=user.id, job=job_id, kind=kind)
            return {"status": "over_quota", "answer": str(exc)}

        # **A build is the positive label** (decided 2026-07-29). It spends the user's own credit,
        # so nobody presses it on a job they are not going to apply to — which makes it a stronger
        # signal than a tap on "Applied", backed by spending rather than by intention. The feed
        # carries no Applied button any more, so this is where the label comes from.
        result = record(db, user.id, job_id, "applied")
        if result.changed and result.labels_since_train >= TRAIN_MIN_NEW_LABELS:
            enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

        enqueue(db, "docgen", user.id, payload={"job_id": job_id, "kind": kind},
                dedup_key=f"{kind}:{job_id}")
        audit(logger, "telegram.build", user=user.id, job=job_id, kind=kind)
        document = "cover letter" if kind == "cover" else "CV"
        return {"status": "queued",
                "answer": f"Building your {document} — it will arrive here shortly."}

    result = record(db, user.id, job_id, action)
    if result.changed and result.labels_since_train >= TRAIN_MIN_NEW_LABELS:
        # Debounced: `run_after` in ten minutes, collapsed on the dedup key, so twenty taps in a
        # burst produce exactly one retrain ten minutes after the last of them.
        enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

    return {"status": "recorded" if result.changed else "already",
            "action": result.action,
            "answer": _confirmation(result.action, result.changed)}


def _is_owner(user: User, tapper_id: str) -> bool:
    """The tap came from the person whose account this is.

    Compared against the **personal** chat id, which is the account owner by construction: it was
    captured from the conversation they had with their own bot. A channel id would not do — that is
    the thing other people are in.
    """
    return bool(tapper_id) and tapper_id == (user.telegram_chat_id or "")


def _parse(data: str) -> tuple[str | None, int]:
    """`ap:123` -> ("applied", 123). Anything else -> (None, 0)."""
    prefix, _, raw = data.partition(":")
    action = CALLBACK_PREFIX.get(prefix)
    if action is None or not raw.isdigit():
        return None, 0
    return action, int(raw)


def _confirmation(action: str, changed: bool) -> str:
    if not changed:
        return "Already recorded."
    if action == "applied":
        return "Marked as applied — your model will learn from this."
    if action == "skipped":
        return "Skipped. You will see fewer like it."
    return "Done."


def keyboard(job_id: int) -> dict:
    """The inline keyboard attached to a delivered job.

    Three buttons and no more. Every extra one is a thing to read on a phone before deciding, and
    the whole value of the digest is that it can be triaged quickly.
    """
    return {
        "inline_keyboard": [[
            {"text": "✅ Applied", "callback_data": f"ap:{job_id}"},
            {"text": "🚫 Skip", "callback_data": f"sk:{job_id}"},
            {"text": "📄 Build CV", "callback_data": f"bc:{job_id}"},
        ]],
    }


__all__ = ["ACTIONS", "CALLBACK_PREFIX", "TRAIN_DEBOUNCE_MIN", "TRAIN_MIN_NEW_LABELS", "keyboard",
           "router"]
