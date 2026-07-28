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

from libs.contracts.profile import ProfileSchema
from libs.db.models import User, UserDocument
from libs.llm import LlmClient, LlmError
from libs.obs import audit
from services.capture.prompt import (
    CAPTURE_MODEL,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    build_messages,
    output_schema,
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

    schema = output_schema()
    # The model is never shown the flag it must not set. Belt to the braces below.
    schema.get("properties", {}).pop("confirmed", None)

    raw = client.complete_json(model=CAPTURE_MODEL, system=SYSTEM_PROMPT,
                               messages=build_messages(documents, full_name=full_name),
                               schema=schema, max_tokens=MAX_TOKENS)
    # Braces. Whatever came back, this profile is a proposal until a human says otherwise.
    raw["confirmed"] = False

    try:
        return ProfileSchema.model_validate(raw)
    except ValidationError as exc:
        # The error names fields, not values — the values are the candidate's own history.
        logger.error("processing produced a profile that failed validation: %s",
                     [error["loc"] for error in exc.errors()][:10])
        raise CaptureFailed("the profile the model produced did not match the schema") from exc


def process_user(session: Session, user_id: _uuid.UUID, client: LlmClient) -> ProfileSchema:
    """Read this user's documents, produce a draft, store it. Returns the draft.

    Stored on `users.profile_draft` rather than applied: applying it would bump the generation and
    invalidate every label the user has, on the strength of text nobody has read yet.
    """
    user = session.get(User, user_id)
    if user is None:
        raise CaptureFailed(f"no such user {user_id}")

    documents = documents_for(session, user_id)
    draft = capture_profile(client, documents, full_name=user.full_name)

    user.profile_draft = draft.model_dump()
    user.capture_source = "capture"
    audit(logger, "process.drafted", user=user_id, documents=len(documents),
          not_list=len(draft.not_list), conflicts=len(draft.conflicts))
    return draft
