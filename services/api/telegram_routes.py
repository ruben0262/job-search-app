"""Telegram button taps, arriving as a webhook."""
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
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue, jobs_today
from libs.secrets import SecretsError, SecretStore
from services.api.deps import get_db

logger = logging.getLogger(__name__)


class OverBuildQuota(RuntimeError):
    """This account has had its documents for today. Carries what to say to the user."""


def _check_build_quota(db, user) -> None:
    """Refuse a build once the account is at its daily ceiling."""
    allowed = limits.resolve(user, "cv_builds_per_day")
    if allowed is None:
        return                                    # the catalogue forbids unlimited here, but honour
    used = jobs_today(db, "docgen", user.id)      # it rather than inventing a number if it changes
    if used >= allowed:
        raise OverBuildQuota(
            f"you have had your {allowed} documents for today — the count resets at midnight")
router = APIRouter(prefix="/telegram", tags=["telegram"])

# A retrain is debounced so a burst of taps produces one fit (`SCHEDULER_PLAN.md` §5).
TRAIN_DEBOUNCE_MIN = 10
# `callback_data` is capped at 64 bytes, so it carries an action and a job id, nothing else.
CALLBACK_PREFIX = {"ap": "applied", "sk": "skipped", "bc": "build_cv", "co": "build_cover"}


def _authenticate(secret: str, db: Session, header_token: str | None) -> User:
    """Resolve the path secret to a user, and check Telegram's header."""
    user = db.scalar(select(User).where(User.telegram_webhook_secret == secret))
    if user is None:
        # This 404 was silent, and it hid a dead feature for every account but one.
        logger.warning("telegram: a tap arrived for webhook %s… and matched no account — that "
                       "bot's owner has no telegram_webhook_secret, so their buttons are dead",
                       secret[:8])
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    # `compare_digest`, not `==`.
    if not header_token or not hmac.compare_digest(header_token, secret):
        logger.warning("telegram: webhook for %s had a bad secret header", user.id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


def _build(db, user, action: str, job_id: int) -> dict:
    """A tap on Build my CV or Build my cover letter."""
    kind = "cover" if action == "build_cover" else "cv"

    # The quota is checked at the admission point, never inside the worker (`TENANT_SAFETY.md` §3).
    try:
        _check_build_quota(db, user)
    except OverBuildQuota as exc:
        audit(logger, "telegram.build_over_quota", user=user.id, job=job_id, kind=kind)
        return {"status": "over_quota", "answer": str(exc)}

    # Refused here rather than dead-lettered there — and refused for exactly the reasons the
    # worker would refuse for, which is the whole point of asking `build_blocker` rather than
    # `is_ready`. Checking a weaker condition here is how a build was promised and never sent.
    blocker = ProfileFiles(db, user.id).build_blocker()
    if blocker is not None:
        return {"status": "not_ready", "answer": blocker}

    # A build is the positive label (decided 2026-07-29).
    result = record(db, user.id, job_id, "applied")
    if result.changed:
        enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

    enqueue(db, "docgen", user.id, payload={"job_id": job_id, "kind": kind},
            dedup_key=f"{kind}:{job_id}")
    audit(logger, "telegram.build", user=user.id, job=job_id, kind=kind)
    document = "cover letter" if kind == "cover" else "CV"
    return {"status": "queued",
            "answer": f"Building your {document} — it will arrive here shortly."}


@router.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, db: Session = Depends(get_db),
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    """One button tap."""
    user = _authenticate(secret, db, x_telegram_bot_api_secret_token)
    update = await request.json()

    query = update.get("callback_query")
    if not query:
        return _command(db, user, update.get("message") or {})

    tapper = str((query.get("from") or {}).get("id") or "")
    if not _is_owner(user, tapper):
        # A channel member who is not the account owner.
        audit(logger, "telegram.foreign_tap", user=user.id)
        return {"status": "not_yours",
                "answer": "Only the owner of this feed can use these buttons."}

    action, job_id = _parse(str(query.get("data") or ""))
    if action is None:
        return {"status": "unknown_action"}

    if action in ("build_cv", "build_cover"):
        return _build(db, user, action, job_id)

    result = record(db, user.id, job_id, action)
    if result.changed:
        # Debounced on the dedup key, so a burst of taps produces exactly one retrain.
        enqueue(db, "train", user.id, dedup_key="retrain", delay_min=TRAIN_DEBOUNCE_MIN)

    return {"status": "recorded" if result.changed else "already",
            "action": result.action,
            "answer": _confirmation(result.action, result.changed)}


def _command(db: Session, user: User, message: dict) -> dict:
    """A typed command rather than a button tap."""
    parts = (message.get("text") or "").strip().split()
    command = parts[0].split("@")[0].lower() if parts else ""
    if command not in ("/rate", "/cv", "/cover"):
        return {"status": "ignored"}

    # The same ownership check a button tap gets.
    if not _is_owner(user, str((message.get("from") or {}).get("id") or "")):
        audit(logger, "telegram.foreign_command", user=user.id)
        return {"status": "not_yours"}

    if command == "/rate":
        return _rate(db, user)
    return _build_from_link(db, user, kind="cover" if command == "/cover" else "cv",
                            link=parts[1] if len(parts) > 1 else "")


@contextmanager
def _fetching_as(db: Session, user: User):
    """An HTTP client on this user's own proxy, or an unproxied one if they have no credential."""
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
    """`/cv <link>` — build a document for a job the harvest never saw."""
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

    # After the link is known to be a link, so somebody who typed nonsense is told that rather
    # than told to confirm their details — but before anything is written. This path had no
    # readiness check at all, so an account that cannot build still recorded an `applied` label
    # and queued a retrain for a document that was never going to arrive.
    blocker = ProfileFiles(db, user.id).build_blocker()
    if blocker is not None:
        return {"status": "not_ready", "message": blocker}

    # A build is the positive label, whether the job came from the feed or from a paste.
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
    """Hand back the job whose answer would teach the model most."""
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
    """The tap came from the person whose account this is."""
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
    """The inline keyboard attached to a delivered job."""
    return {
        "inline_keyboard": [[
            {"text": "✅ Applied", "callback_data": f"ap:{job_id}"},
            {"text": "🚫 Skip", "callback_data": f"sk:{job_id}"},
            {"text": "📄 Build CV", "callback_data": f"bc:{job_id}"},
        ]],
    }


__all__ = ["ACTIONS", "CALLBACK_PREFIX", "TRAIN_DEBOUNCE_MIN",
           "keyboard", "rating_keyboard", "router"]
