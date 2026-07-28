"""Which onboarding pages are satisfied, derived from the database rather than tracked.

**Nothing records "the user finished page 4".** Completion is computed from the rows each page
writes — a name on `users`, a `cv` document, a connected Telegram status, a stored credential, a
config row. A separate progress flag would be a second source of truth that goes stale the moment
somebody deletes a credential from the account page, and the user would be told they are set up
while their feed silently cannot run.

The same derivation drives the dashboard's health banners (`OPERATIONS_PLAN.md` §4b), which is why
it lives in `libs` rather than in the route: "you are missing a Telegram connection" is the same
statement before and after onboarding, and it must not be written twice.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User, UserConfig, UserDocument, UserSecret

# Either Claude credential satisfies page 5 (decision D2).
CLAUDE_KINDS = ("claude_api", "claude_oauth")


@dataclass(frozen=True)
class PageState:
    key: str
    title: str
    complete: bool
    # Written for the person at the form, not for a log — it is shown verbatim when a page is
    # incomplete, and "missing telegram_bot secret" is not something a user can act on.
    detail: str


@dataclass(frozen=True)
class OnboardingState:
    pages: tuple[PageState, ...]
    completed_at: object | None

    @property
    def complete(self) -> bool:
        return all(page.complete for page in self.pages)

    @property
    def next_page(self) -> str | None:
        """The first unsatisfied page, which is where the user is sent.

        First rather than "the one after the last they saw": a user who deletes their bot token from
        the account page should land back on Telegram, not on whatever came after it.
        """
        return next((page.key for page in self.pages if not page.complete), None)

    def as_dict(self) -> dict:
        return {
            "complete": self.complete,
            "next_page": self.next_page,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "pages": [
                {"key": page.key, "title": page.title, "complete": page.complete,
                 "detail": page.detail}
                for page in self.pages],
        }


def onboarding_state(session: Session, user_id: _uuid.UUID) -> OnboardingState:
    """The seven pages of `ONBOARDING_AND_PROCESSING.md` §2, in order.

    Every page is required. A half-configured account produces a bad feed, and the user concludes
    the product does not work rather than that they skipped a step — so there is no "skip for now".
    """
    user = session.get(User, user_id)
    if user is None:
        raise LookupError(f"no such user {user_id}")

    kinds = set(session.scalars(
        select(UserDocument.kind).where(UserDocument.user_id == user_id)).all())
    secrets = set(session.scalars(
        select(UserSecret.kind).where(UserSecret.user_id == user_id)).all())
    has_config = session.scalar(
        select(UserConfig.user_id).where(UserConfig.user_id == user_id)) is not None

    pages = (
        PageState(
            "identity", "Your name and links", bool(user.full_name),
            "Your full name as it should appear on a CV."),
        PageState(
            "documents", "Documents", "cv" in kinds,
            "Upload at least your CV. Cover letters, references, achievements and diplomas all "
            "make the profile better."),
        PageState(
            "history", "Your history", "history" in kinds,
            "One long-form write-up of what you have done. This is where the things a CV leaves "
            "out come from."),
        PageState(
            # `connected` rather than "a token is stored": a saved token with no chat id delivers
            # nothing, and the failure would look like a delivery bug months later.
            "telegram", "Telegram", user.telegram_status == "connected",
            "Save your bot token, then press Start in the bot so it can message you."),
        PageState(
            "claude", "Claude", bool(secrets & set(CLAUDE_KINDS)),
            "An API key or an OAuth token — either one works."),
        PageState(
            "proxy", "Proxy", "proxy" in secrets,
            "Required for LinkedIn, which is fetched through your own proxy and never a shared "
            "one."),
        PageState(
            "config", "What to search for", has_config,
            "Keywords, locations and how often you want to hear from us."),
    )
    return OnboardingState(pages=pages, completed_at=user.onboarding_completed_at)
