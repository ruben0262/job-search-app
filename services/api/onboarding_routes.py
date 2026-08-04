"""The seven onboarding pages, as endpoints.

**No LLM runs behind any of these** (`ONBOARDING_AND_PROCESSING.md` §1). Onboarding collects; the
dashboard's *process data* step is the only thing that spends the user's Claude credential. That is
what lets a user finish signing up while Anthropic is down, at no cost, in one sitting.

Three pages are already served elsewhere and are deliberately not duplicated here: documents by
`/documents`, and Telegram, Claude and the proxy by `/credentials`. This module owns identity,
history, config, and the completion gate — plus `GET /onboarding/state`, which is the single place
that answers "what is left", so the front end never re-derives the rule and gets it subtly wrong.

Nothing here is one-shot. Every field is editable afterwards from the account page, and re-saving
after completion is normal rather than an error — onboarding is the only place this is *sequenced*,
not the only place it can be set.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from libs import limits
from libs.contracts.config import UserConfigSchema, migrate_config
from libs.contracts.config import json_schema as config_schema
from libs.contracts.onboarding import IdentitySchema
from libs.contracts.onboarding import json_schema as identity_schema
from libs.db.models import CorpusJob, User, UserDocument
from libs.documents import UnsupportedDocument, extract_text
from libs.obs import audit
from libs.onboarding import onboarding_state
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# How far back the config preview counts. Not the section's own `posted_within_hours`: at 24 hours a
# correct config can legitimately show single digits, and a user reading that as "my keywords are
# wrong" would go and break a working config.
PREVIEW_WINDOW_DAYS = 30
PREVIEW_TITLE_LIMIT = 5


@router.get("/state")
def state(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What is done, what is left, and where to send the user next.

    Derived from the rows each page writes rather than from a stored progress marker, so deleting a
    credential later correctly reopens that page instead of leaving an account "complete" and
    unable to run (`libs.onboarding.state`).
    """
    return onboarding_state(db, user_id).as_dict()


# ── page 1: identity ──────────────────────────────────────────────────────────────────────────

@router.get("/identity")
def get_identity(db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    user = db.get(User, user_id)
    return {"full_name": user.full_name, "links": user.links or [],
            "schema": identity_schema()}


@router.put("/identity")
def put_identity(body: IdentitySchema, db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Store the name and links. Replaces both — the form submits the whole page.

    Editing the name does **not** bump `profile_generation`. The generation counter scopes labels
    and model artifacts to a rubric, and a name appears nowhere in a rubric; bumping it would
    invalidate every label a user has for a typo fix (`PROFILE_CAPTURE_PLAN.md` §6.4).
    """
    user = db.get(User, user_id)
    user.full_name = body.full_name
    user.links = [link.model_dump() for link in body.links]
    audit(logger, "onboarding.identity", user=user_id, links=len(user.links))
    return {"status": "saved", "full_name": user.full_name, "links": user.links}


# ── page 3: history ───────────────────────────────────────────────────────────────────────────

class HistoryBody(BaseModel):
    text: str = Field(min_length=200, max_length=400_000)


@router.put("/history")
def put_history(body: HistoryBody, request: Request, db: Session = Depends(get_db),
                user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The long-form write-up, typed rather than uploaded.

    It exists as an endpoint because nobody has a "history document" lying around — they write one
    in the box. Stored as a `history` document so there is exactly one path into the processing
    step: whether the text was pasted or extracted from a `.docx` is not a distinction anything
    downstream should have to know about.

    The minimum length is a real requirement rather than politeness. This page feeds the parts of
    the profile a CV omits, and two sentences produce a profile no better than the CV alone.
    """
    rate_limit(request, f"history:{user_id}", limit=60, per_seconds=3600)
    try:
        extracted = extract_text("history.txt", body.text.encode("utf-8"))
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    document = db.scalar(
        select(UserDocument).where(UserDocument.user_id == user_id,
                                   UserDocument.kind == "history"))
    if document is None:
        document = UserDocument(user_id=user_id, kind="history", filename="history.txt",
                                text=extracted.text, text_sha256=extracted.sha256,
                                chars=extracted.chars)
        db.add(document)
    else:
        # Replaced rather than appended: this page is one document the user edits, and keeping
        # every revision would feed the processing step the same history several times over.
        document.text = extracted.text
        document.text_sha256 = extracted.sha256
        document.chars = extracted.chars
    db.flush()
    # The history is a document like any other, so changing it makes the profile stale like any
    # other — and this is the page most likely to be edited after the fact.
    db.get(User, user_id).documents_changed_at = func.now()
    audit(logger, "onboarding.history", user=user_id, chars=extracted.chars)
    return {"status": "saved", "chars": extracted.chars, "truncated": extracted.truncated}


@router.get("/history")
def get_history(db: Session = Depends(get_db),
                user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Returned in full, unlike `/documents`, because this is an edit box the user types into."""
    document = db.scalar(
        select(UserDocument).where(UserDocument.user_id == user_id,
                                   UserDocument.kind == "history"))
    return {"text": document.text if document else "", "chars": document.chars if document else 0}


# ── page 7: config ────────────────────────────────────────────────────────────────────────────

@router.get("/config")
def get_config(db: Session = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The stored config in the *current* shape, plus the schema the form is generated from.

    A v1 row is upgraded on read rather than rewritten, so a user's stored config is never changed
    by a deploy they did not ask for — it upgrades the next time they save.
    """
    stored = TenantRepository(db).get_config(user_id)
    return {"config": migrate_config(stored) if stored else None, "schema": config_schema()}


@router.put("/config")
def put_config(body: UserConfigSchema, db: Session = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Validate and store. The server is the authority; the form is a convenience.

    Saving does not schedule anything. The harvester picks the config up on its next cycle by
    reading every active user's config — there is no per-user trigger to forget to fire.

    **The keyword ceiling is checked here rather than in the schema**, because it is per account.
    `UserConfigSchema` bounds each section at the platform maximum, which is the right hard stop for
    everyone; the operator's `keywords` limit is what an individual account is allowed, and it went
    unread until 2026-08-04 — the control existed, the panel rendered it, and the schema constant
    was the only thing anybody was measured against. Counted **across sections**, matching the
    harvester's own cap, or five sections would buy five times the allowance.
    """
    ceiling = limits.resolve(db.get(User, user_id), "keywords")
    total = sum(len(section.keywords) for section in body.sections)
    if ceiling is not None and total > ceiling:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"you have {total} search terms and the limit is {ceiling} — "
                   "remove a few, or ask us to raise it")

    TenantRepository(db).save_config(user_id, body.model_dump())
    audit(logger, "onboarding.config", user=user_id,
          sections=len(body.sections),
          keywords=sum(len(section.keywords) for section in body.sections))
    return {"status": "saved"}


@router.post("/config/preview")
def preview_config(body: UserConfigSchema, db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """How many jobs already in the corpus these terms would match, with a few example titles.

    **An approximation, and the response says so.** The harvester sends keywords to each source's
    own search, so the real feed is whatever LinkedIn and Indeed return — not a substring match over
    what has been collected so far. What this genuinely catches is the failure it exists for: terms
    that match *nothing at all*, which is a typo or a phrase nobody puts in a job title, and which
    would otherwise show up as an empty feed a day later with no explanation.

    Counted over `PREVIEW_WINDOW_DAYS` rather than the section's own window, because a correct
    config can legitimately return single digits over 24 hours and a user reading that as failure
    would go and break something that worked.

    Takes the config in the request body rather than reading the stored one, so the number updates
    as the user types and does not require saving a config to find out it is empty.
    """
    keywords = [keyword for section in body.sections for keyword in section.keywords]
    excluded = [keyword for section in body.sections for keyword in section.exclude_keywords]

    recent = CorpusJob.first_seen_at >= func.now() - func.make_interval(0, 0, 0, PREVIEW_WINDOW_DAYS)
    matches = or_(*[CorpusJob.title.ilike(f"%{keyword}%") for keyword in keywords])
    where = [recent, matches]
    where += [~CorpusJob.title.ilike(f"%{keyword}%") for keyword in excluded]

    total = db.scalar(select(func.count()).select_from(CorpusJob).where(*where)) or 0
    examples = db.scalars(
        select(CorpusJob.title).where(*where)
        .order_by(CorpusJob.first_seen_at.desc()).limit(PREVIEW_TITLE_LIMIT)).all()

    return {
        "matches": total,
        "window_days": PREVIEW_WINDOW_DAYS,
        "examples": list(examples),
        "approximate": True,
        "detail": ("Counted against jobs already collected. Your own searches run against each "
                   "site directly, so the real number is usually higher — but zero here almost "
                   "always means a keyword is misspelled."),
    }


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

@router.post("/complete")
def complete(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Finish onboarding, if every page is satisfied. 409 naming what is missing if not.

    **This does not set `users.active`.** Completing onboarding means the account is configured;
    `active` means the scheduler may dispatch notifications, and that is only safe once a model
    exists to rank with. Setting it here would start a notify loop for an account whose scorer has
    nothing to serve, and every cycle would fail identically for a reason no user could see.

    Idempotent: pressing it twice keeps the original timestamp rather than moving it.
    """
    current = onboarding_state(db, user_id)
    if not current.complete:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "some pages are not finished yet",
                    "missing": [page.key for page in current.pages if not page.complete],
                    "next_page": current.next_page})

    user = db.get(User, user_id)
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = func.now()
        audit(logger, "onboarding.complete", user=user_id)

    return {"status": "complete",
            # What the dashboard tells them to do next. Processing is the first thing that spends
            # their credential, so it is a decision they make rather than something that just runs.
            "next_action": "process_data"}


@router.get("/schema")
def schemas() -> dict:
    """Both JSON Schemas the front end generates its forms from, in one call.

    Exported from the models the server validates with, so a field cannot exist on one side and not
    the other (`CODE_STRUCTURE.md` §8).
    """
    return {"identity": identity_schema(), "config": config_schema()}
