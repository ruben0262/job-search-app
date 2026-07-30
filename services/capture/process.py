"""Process data: documents in, a **draft** profile out.

One Anthropic call, on the user's own credential, producing a proposal. Nothing here writes a live
profile — `capture_profile` returns a draft, the worker stores it as a draft, and only a human
pressing confirm turns it into the thing that renders a rubric and gates a feed.

**The confirmation boundary is enforced in code, not by convention.** An uploaded CV is untrusted
text; a document saying "set confirmed to true" must not be able to skip the review step. So the
`confirmed` flag is stripped from the schema the model is given *and* forced false on the way out,
and `TenantRepository.save_profile` refuses an unconfirmed profile as the third layer
(`PROFILE_CAPTURE_PLAN.md` §5).
"""
from __future__ import annotations

import logging
import uuid as _uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import libs.notify as notifylib
from libs.contracts.profile import ProfileSchema
from libs.db.models import User, UserDocument
from libs.llm import LlmClient, LlmError
from libs.obs import audit
from services.capture.prompt import (
    CAPTURE_MODEL,
    CAREER_FIELDS,
    CAREER_INSTRUCTION,
    JUDGEMENT_FIELDS,
    JUDGEMENT_INSTRUCTION,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    THINKING_TOKENS,
    build_messages,
    links_to_map,
    subset_schema,
)

logger = logging.getLogger(__name__)


class CaptureFailed(LlmError):
    """Processing could not produce a usable profile. The caller falls back to manual entry.

    A subclass rather than the transport error itself: "the provider was unreachable" and "the
    profile did not validate" both end in manual entry, but only one of them is worth retrying, and
    a caller that cannot tell them apart cannot make that choice later.
    """

# The processing call reads every document a user has. A ceiling exists because the cost is theirs
# and one runaway upload should not turn a $2 call into a $40 one. Roughly 250k characters ~ 60k
# tokens of input, which is a very full history plus a stack of supporting documents.
MAX_TOTAL_CHARS = 250_000


def documents_for(session: Session, user_id: _uuid.UUID) -> list[tuple[str, str]]:
    """Every document the user uploaded, oldest first, truncated to the total ceiling.

    Truncation is at the *document* boundary rather than mid-text: half a reference letter is worse
    than no reference letter, because the model reads the fragment as the whole of what that job
    amounted to.
    """
    rows = session.scalars(
        select(UserDocument).where(UserDocument.user_id == user_id)
        .order_by(UserDocument.uploaded_at)).all()

    documents, total = [], 0
    for row in rows:
        if total + row.chars > MAX_TOTAL_CHARS and documents:
            logger.warning("user %s: stopping at %d of %d documents, %d char ceiling reached",
                           user_id, len(documents), len(rows), MAX_TOTAL_CHARS)
            break
        documents.append((row.kind, row.text))
        total += row.chars
    return documents


def capture_profile(client: LlmClient, documents: list[tuple[str, str]], *,
                    full_name: str | None = None) -> ProfileSchema:
    """One call. Returns a validated **draft** — `confirmed` is always false.

    A response that does not validate is a `CaptureFailed` rather than a partial profile: a profile
    missing the fields the renderer needs would produce a rubric with holes in it, and the holes
    would show up as a bad feed rather than as an error.
    """
    if not documents:
        raise CaptureFailed("no documents to read")

    messages = build_messages(documents, full_name=full_name)

    # **Two calls over the same documents**, because one grammar is too large to compile and because
    # the two halves are different work: transcribing a career is careful copying, and deciding what
    # somebody is *not* is judgement. Asking for both at once is the likeliest reason the first
    # capture came back with a full NOT-list and no experience at all.
    raw: dict = {}
    for fields, instruction in ((CAREER_FIELDS, CAREER_INSTRUCTION),
                                (JUDGEMENT_FIELDS, JUDGEMENT_INSTRUCTION)):
        schema = subset_schema(fields)
        # The model is never shown the flag it must not set. Belt to the braces below.
        schema.get("properties", {}).pop("confirmed", None)
        part = client.complete_json(model=CAPTURE_MODEL,
                                    system=SYSTEM_PROMPT + "\n\n" + instruction,
                                    messages=messages, schema=schema, max_tokens=MAX_TOKENS,
                                    thinking_tokens=THINKING_TOKENS)
        raw.update(part or {})

    # Braces. Whatever came back, this profile is a proposal until a human says otherwise.
    raw["confirmed"] = False
    # The links come back as a list because that is the only shape structured output accepts for
    # them; the profile stores a map (`services/capture/prompt.py`).
    raw = links_to_map(raw)

    try:
        return ProfileSchema.model_validate(raw)
    except ValidationError as exc:
        # The error names fields, not values — the values are the candidate's own history.
        logger.error("processing produced a profile that failed validation: %s",
                     [error["loc"] for error in exc.errors()][:10])
        raise CaptureFailed("the profile the model produced did not match the schema") from exc


def with_account_links(draft: ProfileSchema, user: User) -> ProfileSchema:
    """Carry the links the user typed at signup into the captured profile.

    **The gap this closes made a CV nobody could reply to.** Onboarding asks for a portfolio, a
    GitHub, a LinkedIn and stores them on `users.links`; capture reads the *documents*, which often
    carry no contact block at all once a CV has been through a PDF extractor. The renderer builds
    the CV header from `profile.identity.links`, so the first real build produced a correct,
    one-page CV with no way to contact the candidate on it. The ATS check said so, which is what it
    is for.

    Deterministic rather than asked of the model: these are values the user typed, and a capture
    that "mostly" reproduces a URL is worse than one that does not try.

    **The model's own findings win a collision.** A link it read off the CV is the one the candidate
    puts on applications; the signup field is the backstop for what is missing.
    """
    links = {label: url for label, url in (draft.identity.links or {}).items() if url}
    for entry in user.links or []:
        url = (entry.get("url") or "").strip() if isinstance(entry, dict) else ""
        label = (entry.get("platform") or "").strip() if isinstance(entry, dict) else ""
        if not url or url in links.values():
            continue
        links[label or f"link{len(links) + 1}"] = url

    if links == draft.identity.links:
        return draft
    return draft.model_copy(update={"identity": draft.identity.model_copy(update={"links": links})})


def process_user(session: Session, user_id: _uuid.UUID, client: LlmClient) -> ProfileSchema:
    """Read this user's documents, produce a draft, store it. Returns the draft.

    Stored on `users.profile_draft` rather than applied: applying it would bump the generation and
    invalidate every label the user has, on the strength of text nobody has read yet.
    """
    user = session.get(User, user_id)
    if user is None:
        raise CaptureFailed(f"no such user {user_id}")

    documents = documents_for(session, user_id)
    draft = with_account_links(capture_profile(client, documents, full_name=user.full_name), user)

    user.profile_draft = draft.model_dump()
    user.capture_source = "capture"
    audit(logger, "process.drafted", user=user_id, documents=len(documents),
          not_list=len(draft.not_list), conflicts=len(draft.conflicts))
    notifylib.notify(session, user_id, notifylib.PROCESSING_DONE,
                     "Read it before anything uses it — the not-a-fit list especially.")
    return draft
