"""Which onboarding pages are satisfied, derived from the database rather than tracked.

Three screens (ROADMAP Phase 8): upload a CV, confirm what we found, done. Telegram, an AI credential
and a proxy are optional settings, not steps — none of them is needed to see a matching job.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from pymongo.database import Database

from libs.contracts.config import MIN_GREEN_WORDS, complete_search
from libs.db import get
from libs.db.models import User, UserDocument
from libs.tenant.repository import TenantRepository


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


def onboarding_state(db: Database, user_id: _uuid.UUID) -> OnboardingState:
    """The two pages before done, in order."""
    user = get(db, User, user_id)
    if user is None:
        raise LookupError(f"no such user {user_id}")

    has_cv = db[UserDocument.COLLECTION].count_documents({"user_id": user_id, "kind": "cv"}, limit=1) > 0
    config = TenantRepository(db).get_config(user_id)

    pages = (
        PageState(
            "documents", "Your CV", has_cv,
            "Upload your CV. A cover letter and a few words about yourself are optional."),
        PageState(
            # The schema's own rule, so a search the page would refuse to save is never "done" here.
            "review", "What we found", bool(user.full_name) and complete_search(config),
            f"Check your name. Pick at least {MIN_GREEN_WORDS} green words, a country, a way of working "
            "and a language."),
    )
    return OnboardingState(pages=pages, completed_at=user.onboarding_completed_at)
