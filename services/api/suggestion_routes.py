"""The green and red words suggested to a person: see them, take one, or say no to one."""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from libs import suggestions
from libs.contracts.config import UserConfigSchema, migrate_config
from libs.matching import green_words
from libs.obs import audit
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db
from services.api.onboarding_routes import WordSuggestions, save_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/suggestions", tags=["suggestions"])


class SuggestionPick(BaseModel):
    term: str = Field(min_length=2, max_length=40)
    kind: Literal["green", "red"]


class SuggestionDismissal(BaseModel):
    term: str = Field(min_length=2, max_length=40)


@router.get("", response_model=WordSuggestions)
def get_suggestions(db: Session = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The words worth adding to their search, each with the reason."""
    return suggestions.current(db, user_id)


@router.post("/accept", response_model=WordSuggestions)
def accept(body: SuggestionPick, db: Session = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Add a word to their search: green picks jobs, red hides them."""
    stored = TenantRepository(db).get_config(user_id)
    config = migrate_config(stored) if stored else {"sections": [{"platform": "all", "keywords": []}]}
    if body.kind == "red" and not green_words(config):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="pick a word for the jobs you want first — a red word only hides "
                                   "jobs your green words find")
    section = config["sections"][0]
    field = "keywords" if body.kind == "green" else "exclude_keywords"
    section[field] = [*(section.get(field) or []), body.term]
    try:
        search = UserConfigSchema.model_validate(config)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{body.term!r} cannot be added: {exc.errors()[0]['msg']}") from exc
    save_config(db, user_id, search)
    audit(logger, "suggestion.accepted", user=user_id, kind=body.kind)
    return suggestions.current(db, user_id)


@router.post("/dismiss", response_model=WordSuggestions)
def dismiss(body: SuggestionDismissal, db: Session = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Never suggest this word again."""
    audit(logger, "suggestion.dismissed", user=user_id)
    return suggestions.dismiss(db, user_id, body.term)
