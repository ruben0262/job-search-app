"""The green and red words suggested to a person, each with its reason.

Taking one or saying no to one is the help chat's (`support_routes`): the jobs page's suggestions and
the two routes that served them, `/suggestions/accept` and `/suggestions/dismiss`, were removed on
2026-11-02, the owner's decision. My documents still reads this list to count what a save found.
"""
from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends
from pymongo.database import Database

from libs import suggestions
from services.api.deps import current_user, get_db
from services.api.onboarding_routes import WordSuggestions

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


@router.get("", response_model=WordSuggestions)
def get_suggestions(db: Database = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The words worth adding to their search, each with the reason."""
    return suggestions.current(db, user_id)
