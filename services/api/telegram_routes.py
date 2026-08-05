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
from contextlib import contextmanager

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs import limits
from libs.corpus import ondemand
from libs.corpus.ondemand import NotAJobLink
from libs.db.models import CorpusJob, User
from libs.feedback import ACTIONS, next_to_rate, record
from libs.obs import audit
from libs.queue import enqueue, jobs_today
from libs.secrets import SecretsError, SecretStore
from services.api.deps import get_db

logger = logging.getLogger(__name__)


class OverBuildQuota(RuntimeError):
    """This account has had its documents for today. Carries what to say to the user."""


def _check_build_quota(db, user) -> None:
    """Refuse a build once the account is at its daily ceiling.

    **Checked at the admission point**, never inside the worker (`TENANT_SAFETY.md` §3): a limit
    checked halfway through a build has already spent the Claude call it was protecting.

    Reads `libs.limits`, which is the one per-user limits system. It used to read a second one —
    plan quotas with a `users.quota_overrides` column that nothing ever wrote, so the exception it
    existed to grant could not be granted (ROADMAP 6as).
    """
    allowed = limits.resolve(user, "cv_builds_per_day")
    if allowed is None:
        return                                    # the catalogue forbids unlimited here, but honour
    used = jobs_today(db, "docgen", user.id)      # it rather than inventing a number if it changes
    if used >= allowed:
        raise OverBuildQuota(
            f"you have had your {allowed} documents for today — the count resets at midnight")
router = APIRouter(prefix="/telegram", tags=["telegram"])

# A retrain is debounced so a burst of taps produces one fit, ten minutes after the last
# (`SCHEDULER_PLAN.md` §5). Below this many new feedback labels there is nothing worth refitting —
# a model rebuilt on one extra label is not a different model, it is the same CPU spent twice.
TRAIN_DEBOUNCE_MIN = 10
# **Removed 2026-08-05, and the removal is the fix.** This gate asked "has this account produced
# five feedback labels at the current profile generation" and the trainer's `should_retrain` asks
# "are there five *more* labels than the active model was fitted on". Two different questions with
# one name and one number, so once an account passed five feedback labels the gate was permanently
# true, every subsequent tap enqueued a retrain, and the trainer correctly declined each one — onto
# the operator's health page as a dead letter (ROADMAP 6ah).
#
# The trainer is the authority now and this side does not second-guess it. Nothing is lost: the
# debounce below and the `retrain` dedup key already collapse a burst into one job, which is all
# this was contributing when it happened to be right.

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
        # **This 404 was silent, and it hid a dead feature for every account but one.** A bot whose
        # webhook is registered but whose row carries no secret sends taps that match nobody: the
        # user presses "Build my CV", Telegram gets a 404, and nothing anywhere records that the
        # button does not work. Logged at WARNING with the path secret's prefix so it can be tied
        # back to a bot without writing the whole credential-shaped value to a log (ROADMAP 6af).
        logger.warning("telegram: a tap arrived for webhook %s… and matched no account — that "
                       "bot's owner has no telegram_webhook_secret, so their buttons are dead",
                       secret[:8])
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
        return _command(db, user, update.get("message") or {})

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
            _check_build_quota(db, user)
        except OverBuildQuota as exc:
            audit(logger, "telegram.build_over_quota", user=user.id, job=job_id, kind=kind)
            return {"status": "over_quota", "answer": str(exc)}

        # **A build is the positive label** (decided 2026-07-29). It spends the user's own credit,
        # so nobody presses it on a job they are not going to apply to — which makes it a stronger
        # signal than a tap on "Applied", backed by spending rather than by intention. The feed
        # carries no Applied button any more, so this is where the label comes from.
        result = record(db, user.id, job_id, "applied")
        if result.changed:
            enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

        enqueue(db, "docgen", user.id, payload={"job_id": job_id, "kind": kind},
                dedup_key=f"{kind}:{job_id}")
        audit(logger, "telegram.build", user=user.id, job=job_id, kind=kind)
        document = "cover letter" if kind == "cover" else "CV"
        return {"status": "queued",
                "answer": f"Building your {document} — it will arrive here shortly."}

    result = record(db, user.id, job_id, action)
    if result.changed:
        # Debounced: `run_after` in ten minutes, collapsed on the dedup key, so twenty taps in a
        # burst produce exactly one retrain ten minutes after the last of them.
        enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

    return {"status": "recorded" if result.changed else "already",
            "action": result.action,
            "answer": _confirmation(result.action, result.changed)}


def _command(db: Session, user: User, message: dict) -> dict:
    """A typed command rather than a button tap.

    Anything unrecognised is acknowledged rather than refused: a plain message, a channel post, a
    sticker. Telegram resends whatever is not answered with a 2xx, and a bot that argues with its
    own channel is worse than one that says nothing.
    """
    parts = (message.get("text") or "").strip().split()
    command = parts[0].split("@")[0].lower() if parts else ""
    if command not in ("/rate", "/cv", "/cover"):
        return {"status": "ignored"}

    # The same ownership check a button tap gets. The bot may sit in a channel, and a member of it
    # must not be able to train somebody else's model — or spend its owner's credit — by typing a
    # command.
    if not _is_owner(user, str((message.get("from") or {}).get("id") or "")):
        audit(logger, "telegram.foreign_command", user=user.id)
        return {"status": "not_yours"}

    if command == "/rate":
        return _rate(db, user)
    return _build_from_link(db, user, kind="cover" if command == "/cover" else "cv",
                            link=parts[1] if len(parts) > 1 else "")


@contextmanager
def _fetching_as(db: Session, user: User):
    """An HTTP client on this user's own proxy, or an unproxied one if they have no credential.

    Their proxy or nothing — never another user's, and never a shared one. Unproxied still works
    for most boards; it is LinkedIn specifically that refuses datacenter addresses, so a user
    without a proxy can still paste a company careers page and get a CV.

    `KeyUnavailable` is caught rather than propagated: the secret store being unreachable means
    nothing can be decrypted, and refusing the whole command over an *optional* credential would
    turn a degraded key service into a broken feature.
    """
    try:
        with SecretStore(db).use(user.id, "proxy") as secret:
            proxy = secret.reveal() if secret is not None else None
            with httpx.Client(timeout=25, follow_redirects=True, proxy=proxy) as client:
                yield client
        return
    except SecretsError:
        logger.warning("telegram: no proxy available for %s, fetching unproxied", user.id)

    with httpx.Client(timeout=25, follow_redirects=True) as client:
        yield client


def _build_from_link(db: Session, user: User, *, kind: str, link: str) -> dict:
    """`/cv <link>` — build a document for a job the harvest never saw.

    The link is resolved into an ordinary corpus row first (`libs.corpus.ondemand`), so the build
    is the ordinary build: same prompts, same grounding, same record on the applications page. A
    second code path for pasted jobs would be a second code path nobody exercises.

    The quota is checked here, at the admission point, for the same reason the button checks it:
    a limit checked inside the worker has already spent the call it was protecting.
    """
    if not link:
        return {"status": "no_link",
                "message": f"Send it as {kind} followed by the job's link."}

    try:
        _check_build_quota(db, user)
    except OverBuildQuota as exc:
        return {"status": "over_quota", "message": str(exc)}

    try:
        with _fetching_as(db, user) as client:
            resolved = ondemand.resolve(db, link, client)
    except NotAJobLink as exc:
        return {"status": "bad_link", "message": str(exc)}

    # A build is the positive label, whether the job came from the feed or from a paste.
    #
    # `create_missing` because this user was never shown the job — they pasted it. Without it
    # `record` refuses, correctly for a tap on a trimmed old message and wrongly here, and the
    # paste would silently produce no training signal at all.
    result = record(db, user.id, resolved.job_id, "applied", create_missing=True)
    if result.changed:
        enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

    enqueue(db, "docgen", user.id, payload={"job_id": resolved.job_id, "kind": kind},
            dedup_key=f"{kind}:{resolved.job_id}")
    audit(logger, "telegram.build_from_link", user=user.id, job=resolved.job_id, kind=kind,
          existing=resolved.existing)
    document = "cover letter" if kind == "cover" else "CV"
    return {"status": "queued", "job_id": resolved.job_id,
            "message": f"Building your {document} for {resolved.title or 'that job'} — "
                       "it will arrive here shortly."}


def _rate(db: Session, user: User) -> dict:
    """Hand back the job whose answer would teach the model most.

    **Not the highest-scoring job, and not one from the feed.** The pool is ordered by distance
    from the delivery threshold, and about half of it sits below that threshold and was therefore
    never sent — which is the only way the model hears that something it rejected was worth
    sending (`libs/feedback/rating.py`).
    """
    job_id = next_to_rate(db, user.id)
    if job_id is None:
        return {"status": "nothing_to_rate",
                "message": "Nothing to rate right now — every scored job has an answer already."}

    job = db.get(CorpusJob, job_id)
    audit(logger, "telegram.rate", user=user.id, job=job_id)
    return {"status": "rate", "job_id": job_id,
            "message": f"{job.title} — {job.company or 'unknown company'}\n{job.url}",
            "keyboard": rating_keyboard(job_id)}


def rating_keyboard(job_id: int) -> dict:
    """Two answers and nothing else. `/rate` asks one question: would you have applied?"""
    return {
        "inline_keyboard": [[
            {"text": "👍 Would apply", "callback_data": f"ap:{job_id}"},
            {"text": "👎 Not for me", "callback_data": f"sk:{job_id}"},
        ]],
    }


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


__all__ = ["ACTIONS", "CALLBACK_PREFIX", "TRAIN_DEBOUNCE_MIN",
           "keyboard", "rating_keyboard", "router"]
