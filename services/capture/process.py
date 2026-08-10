"""Process data: documents in, a draft profile out."""
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
from libs.tenant.repository import TenantRepository
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
    """Processing could not produce a usable profile."""

# The processing call reads every document a user has.
MAX_TOTAL_CHARS = 250_000


def documents_for(session: Session, user_id: _uuid.UUID) -> list[tuple[str, str]]:
    """Every document the user uploaded, oldest first, truncated to the total ceiling."""
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
    """One call."""
    if not documents:
        raise CaptureFailed("no documents to read")

    messages = build_messages(documents, full_name=full_name)

    # Two calls over the same documents: one grammar is too large, and the halves are different work.
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
    # Links come back as a list because structured output takes no other shape; the profile stores a map.
    raw = links_to_map(raw)

    try:
        return ProfileSchema.model_validate(raw)
    except ValidationError as exc:
        # The error names fields, not values — the values are the candidate's own history.
        logger.error("processing produced a profile that failed validation: %s",
                     [error["loc"] for error in exc.errors()][:10])
        raise CaptureFailed("the profile the model produced did not match the schema") from exc


def with_account_links(draft: ProfileSchema, user: User) -> ProfileSchema:
    """Carry the links the user typed at signup into the captured profile."""
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
    """Read this user's documents, produce a draft, store it."""
    user = session.get(User, user_id)
    if user is None:
        raise CaptureFailed(f"no such user {user_id}")

    documents = documents_for(session, user_id)
    draft = with_account_links(capture_profile(client, documents, full_name=user.full_name), user)

    user.capture_source = "capture"
    audit(logger, "process.drafted", user=user_id, documents=len(documents),
          not_list=len(draft.not_list), conflicts=len(draft.conflicts))

    if user.profile:
        # A *replacement* keeps its review step, and should: confirming one stales every label the
        # account owns and buys them again on their own credential. That is a decision with a
        # price, so a person makes it.
        user.profile_draft = draft.model_dump()
        notifylib.notify(session, user_id, notifylib.PROCESSING_DONE,
                         "Read it before anything uses it — the not-a-fit list especially.")
        return draft

    # A *first* profile adopts itself. There is nothing to invalidate and nothing to spend, and
    # waiting for a click is what left an account with a finished setup, a trained model and a
    # full corpus unable to build a single CV — every build refused on one NULL column while the
    # dashboard showed a button nobody had pressed.
    adopted = draft.model_dump()
    adopted["confirmed"] = True
    generation = TenantRepository(session).save_profile(user_id, adopted)
    user.profile_draft = None
    audit(logger, "process.adopted", user=user_id, generation=generation, first=True)
    notifylib.notify(session, user_id, notifylib.PROCESSING_DONE,
                     "Your profile is built and your CV and cover letters can be generated. "
                     "Read it when you get a chance — the not-a-fit list especially.")
    return draft
