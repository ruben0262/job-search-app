"""Which onboarding pages are satisfied, derived from the database rather than tracked.

Three screens (ROADMAP Phase 8): upload a CV, confirm what we found, done. Telegram, an AI credential
and a proxy are optional settings, not steps — none of them is needed to see a matching job.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User, UserConfig, UserDocument
from libs.matching import green_words


@dataclass(frozen=True)
class PageState:
    key: str
    title: str
    complete: bool
    # Written for the person at the form, not for a log: it is shown verbatim on an incomplete page.
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
        """The first unsatisfied page, which is where the user is sent. `None` is the done screen."""
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
    """The two pages before done, in order."""
    user = session.get(User, user_id)
    if user is None:
        raise LookupError(f"no such user {user_id}")

    has_cv = session.scalar(select(UserDocument.id).where(
        UserDocument.user_id == user_id, UserDocument.kind == "cv").limit(1)) is not None
    config = session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id))

    pages = (
        PageState(
            "documents", "Your CV", has_cv,
            "Upload your CV. A cover letter and a few words about yourself are optional."),
        PageState(
            # One green word is what makes a job appear (`libs.matching`); without one there are none.
            "review", "What we found", bool(user.full_name) and bool(green_words(config)),
            "Check your name and pick at least one word for the jobs you want."),
    )
    return OnboardingState(pages=pages, completed_at=user.onboarding_completed_at)
