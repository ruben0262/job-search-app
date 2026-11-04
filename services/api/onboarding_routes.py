"""The onboarding pages, as endpoints: upload a CV, confirm what we found, done."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs import limits, suggestions
from libs.contracts.config import (
    MAX_LOCATIONS_PER_USER,
    MIN_GREEN_WORDS,
    Language,
    Location,
    SearchSection,
    UserConfigSchema,
    Workplace,
    location_search_name,
    migrate_config,
)
from libs.contracts.onboarding import IdentitySchema, ProfileLink
from libs.db import find_one, get, insert, now
from libs.db.models import User, UserDocument
from libs.documents import UnsupportedDocument, extract_text
from libs.documents.read import LANGUAGE_NAMES, Facts, read
from libs.obs import audit
from libs.onboarding import onboarding_state
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# How many green words a new search starts with, from their CV: one alone did not read as a list
# anybody could add to (owner, 2026-11-03). One fewer than a search takes (`MIN_GREEN_WORDS`), so the
# first thing a person does is add a word of their own.
PREFILLED_GREEN = 2

# What each way of working is called on the page. Job sites' own names for them are
# `libs.corpus.workfilter.NAMES`.
_WORKPLACES = {"remote": ("Remote", "From home, every day"),
               "hybrid": ("Hybrid", "Some days at home, some in"),
               "onsite": ("On-site", "In the workplace every day")}


class OnboardingPage(BaseModel):
    key: str
    title: str
    complete: bool
    detail: str


class OnboardingSteps(BaseModel):
    complete: bool
    next_page: str | None
    completed_at: str | None
    pages: list[OnboardingPage]


class SavedLink(BaseModel):
    url: str
    platform: str | None = None


class NameAndLinks(BaseModel):
    full_name: str | None
    links: list[SavedLink]


class ConfigChoice(BaseModel):
    value: str
    label: str
    hint: str | None = None


class ConfigChoices(BaseModel):
    """What the pickers offer, labelled. Served, so a place cannot exist on one side only."""
    # The countries, the three people pick most first.
    locations: list[ConfigChoice]
    # How many places may be picked, and how few green words: the schema's bounds, served so the page
    # cannot hold a copy.
    max_locations: int
    min_words: int
    languages: list[ConfigChoice]
    workplaces: list[ConfigChoice]


class WordSuggestion(BaseModel):
    term: str
    reason: str


class WordSuggestions(BaseModel):
    green: list[WordSuggestion]
    red: list[WordSuggestion]


class FoundInDocuments(BaseModel):
    """What their documents say, read by rules (`libs.documents.read`)."""
    name: str | None
    email: str | None
    phone: str | None
    links: list[str]
    location: str | None
    places: list[str]
    languages: list[str]
    titles: list[str]
    skills: list[str]
    years: int | None


class ReviewPage(BaseModel):
    identity: NameAndLinks
    config: dict
    found: FoundInDocuments
    suggestions: WordSuggestions
    choices: ConfigChoices


class SaveResult(BaseModel):
    status: str


class HistorySaved(SaveResult):
    chars: int
    truncated: bool


class HistoryText(BaseModel):
    text: str
    chars: int


# ── documents: the CV, and the "about you" text ───────────────────────────────────────────────

class HistoryBody(BaseModel):
    # Optional and theirs: a sentence is as welcome as a memoir.
    text: str = Field(min_length=1, max_length=400_000)


@router.put("/history", response_model=HistorySaved)
def put_history(body: HistoryBody, request: Request, db: Database = Depends(get_db),
                user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Their own words about themselves, typed rather than uploaded."""
    rate_limit(request, f"history:{user_id}", limit=60, per_seconds=3600)
    try:
        extracted = extract_text("history.txt", body.text.encode("utf-8"))
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    document = find_one(db, UserDocument, {"user_id": user_id, "kind": "history"})
    if document is None:
        insert(db, UserDocument(user_id=user_id, kind="history", filename="history.txt",
                                text=extracted.text, text_sha256=extracted.sha256, chars=extracted.chars))
    else:
        # Replaced rather than appended: this page is one document the user edits.
        db[UserDocument.COLLECTION].update_one({"_id": document.id}, {"$set": {
            "text": extracted.text, "text_sha256": extracted.sha256, "chars": extracted.chars}})
    # The history is a document like any other, so changing it makes the profile stale.
    _stamp(db, user_id, documents_changed_at=now())
    suggestions.refresh(db, user_id)
    audit(logger, "onboarding.history", user=user_id, chars=extracted.chars)
    return {"status": "saved", "chars": extracted.chars, "truncated": extracted.truncated}


@router.get("/history", response_model=HistoryText)
def get_history(db: Database = Depends(get_db),
                user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Returned in full, unlike `/documents`, because this is an edit box the user types into."""
    document = find_one(db, UserDocument, {"user_id": user_id, "kind": "history"})
    return {"text": document.text if document else "", "chars": document.chars if document else 0}


# ── review: what we found, confirmed ──────────────────────────────────────────────────────────

@router.get("/review", response_model=ReviewPage)
def review(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Everything the review page opens with: what they told us, else what their documents say."""
    facts = read(suggestions.documents_of(db, user_id))
    found = suggestions.current(db, user_id)
    stored = TenantRepository(db).get_config(user_id)
    return {"identity": _identity(get(db, User, user_id), facts),
            "config": migrate_config(stored) if stored is not None else _prefill(facts, found),
            "found": {"name": facts.name, "email": facts.email, "phone": facts.phone,
                      "links": list(facts.links), "location": facts.location,
                      "places": list(facts.places), "languages": list(facts.languages),
                      "titles": list(facts.titles), "skills": list(facts.skills),
                      "years": facts.years},
            "suggestions": found, "choices": _choices()}


@router.get("/identity", response_model=NameAndLinks)
def get_identity(db: Database = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Their name and links; read off their CV until they have saved their own."""
    return _identity(get(db, User, user_id), read(suggestions.documents_of(db, user_id)))


@router.put("/identity")
def put_identity(body: IdentitySchema, db: Database = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Store the name and links."""
    links = [link.model_dump() for link in body.links]
    # Their name and links are hard data: typed, theirs, and read by the prompts the setup agents
    # wrote. Stamped so those files can be found stale rather than silently describing an old you.
    _stamp(db, user_id, full_name=body.full_name, links=links, hard_data_changed_at=now())
    audit(logger, "onboarding.identity", user=user_id, links=len(links))
    return {"status": "saved", "full_name": body.full_name, "links": links}


@router.put("/config", response_model=SaveResult)
def put_config(body: UserConfigSchema, db: Database = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Validate and store."""
    save_config(db, user_id, body)
    return {"status": "saved"}


def save_config(db: Database, user_id: _uuid.UUID, body: UserConfigSchema) -> None:
    """The one way a search is saved: under the account's ceiling, stamped, its suggestions redone."""
    ceiling = limits.resolve(get(db, User, user_id), "keywords")
    total = sum(len(section.keywords) for section in body.sections)
    if ceiling is not None and total > ceiling:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"you have {total} search terms and the limit is {ceiling} — "
                   "remove a few, or ask us to raise it")

    TenantRepository(db).save_config(user_id, body.model_dump())
    # Same reason as identity: what they search for is hard data, and `label.md` and the role angles
    # were written against the previous answer.
    _stamp(db, user_id, hard_data_changed_at=now())
    suggestions.refresh(db, user_id)
    audit(logger, "onboarding.config", user=user_id,
          sections=len(body.sections),
          keywords=sum(len(section.keywords) for section in body.sections))


def _identity(user: User, facts: Facts) -> dict:
    """What they saved, else what their CV says. Links they saved as none stay none."""
    links = user.links
    if links is None:
        links = []
        for url in facts.links:
            try:
                links.append(ProfileLink(url=url).model_dump())
            except ValueError:
                logger.warning("onboarding: a link read off user %s's CV is not usable; left out",
                               user.id)
    return {"full_name": user.full_name or facts.name, "links": links}


def _prefill(facts: Facts, found: dict) -> dict:
    """A search filled in from their documents, for them to correct before saving: two green words
    from their CV (their latest job title first), so the list is plainly theirs to change; the red
    words suggested; and the places and languages their CV names.

    Built with the schema's own defaults, not a copy of them; validated when they save it.
    """
    section = SearchSection.model_construct(
        keywords=[each["term"] for each in found.get("green", [])[:PREFILLED_GREEN]],
        exclude_keywords=[each["term"] for each in found.get("red", [])],
        locations=list(facts.places)[:MAX_LOCATIONS_PER_USER])
    return UserConfigSchema.model_construct(
        sections=[section], languages=list(facts.languages)).model_dump(mode="json")


def _choices() -> dict:
    return {
        "locations": [{"value": place.value, "label": location_search_name(place.value)}
                      for place in Location],
        "max_locations": MAX_LOCATIONS_PER_USER,
        "min_words": MIN_GREEN_WORDS,
        "languages": [{"value": code.value, "label": LANGUAGE_NAMES[code.value][0]}
                      for code in Language],
        "workplaces": [{"value": way.value, "label": _WORKPLACES[way.value][0],
                        "hint": _WORKPLACES[way.value][1]} for way in Workplace],
    }


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

@router.post("/complete", response_model=SaveResult)
def complete(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Finish onboarding, if every page is satisfied."""
    current = onboarding_state(db, user_id)
    if not current.complete:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "some pages are not finished yet",
                    "missing": [page.key for page in current.pages if not page.complete],
                    "next_page": current.next_page})

    if db[User.COLLECTION].update_one({"_id": user_id, "onboarding_completed_at": None},
                                      {"$set": {"onboarding_completed_at": now()}}).modified_count:
        audit(logger, "onboarding.complete", user=user_id)
    return {"status": "complete"}


def _stamp(db: Database, user_id: _uuid.UUID, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {**fields, "updated_at": now()}})


# `GET /onboarding/schema` was here, and served both JSON Schemas "the front end generates its forms
# from". It generated nothing: the forms are hand-written TSX against the types in `api-types.ts`, so
# the payload was a second copy of every field list, kept in step by hand. Removed 2026-08-11 with
# the same key on `/onboarding/identity`, `/onboarding/config` and `/process/draft`.
#
# That removal emptied the location and language pickers for two months: ConfigStep still read the
# schema's enums, from a response typed by hand. The lists are back as `choices`, typed, with labels.
