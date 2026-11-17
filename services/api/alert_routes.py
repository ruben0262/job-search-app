"""The Alerts page: a person's named alerts, made, changed, switched off and deleted, and what each one found
(owner, 2026-11-17). What an alert catches is `libs.alerts`; the notifier sends it."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymongo.database import Database

from libs import fit, limits
from libs.alerts import last_read, tidy_names, tidy_words
from libs.contracts.config import location_search_name
from libs.corpus.boards import board_name
from libs.corpus.geo import CITIES, COUNTRIES
from libs.corpus.terms import learn
from libs.db import find, find_one, get, insert, now
from libs.db.models import Alert, User, UserJobState
from libs.matching import green_words
from libs.membership import has
from libs.obs import audit
from libs.settings import HARVEST_SOURCES
from libs.tenant.repository import TenantRepository
from services.api.dashboard_routes import FeedJob
from services.api.deps import current_user, get_db, with_alerts
from services.api.link_routes import MARKED_SHOWN, job_cards

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])

# How far back an alert's count of what it found reaches, as the page says it.
FOUND_DAYS = 7

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class AlertIn(BaseModel):
    """An alert as the page sends it. Every field given must hold for a job; any one value of a field will
    do."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    # Only jobs on their Jobs list.
    search: bool = False
    keywords: list[Term] = Field(default_factory=list, max_length=20)
    companies: list[Name] = Field(default_factory=list, max_length=20)
    places: list[str] = Field(default_factory=list, max_length=40)
    sources: list[str] = Field(default_factory=list, max_length=40)
    min_fit: int | None = Field(None, ge=1, le=10)
    channels: list[Literal["bell", "push", "telegram", "email"]] = Field(
        default_factory=lambda: ["bell", "push", "telegram"])
    on: bool = True


class AlertOut(AlertIn):
    # Every field is there in an answer, defaults included, and the page's types say so.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: int
    created_at: datetime
    # How many jobs it found in the last `FOUND_DAYS` days, and when it last found one.
    found: int
    last_found_at: datetime | None


class Choice(BaseModel):
    value: str
    label: str


class AlertsPage(BaseModel):
    alerts: list[AlertOut]
    # Whether their plan has alerts: without, the page offers the plans instead.
    can: bool
    # The most they may have; None is unlimited.
    most: int | None
    # Whether a match score can be asked for: their plan has scores and their documents make one.
    scores: bool
    # When the alerts last read new jobs, which the page says to show they are running.
    checked_at: datetime | None
    found_days: int
    places: list[Choice]
    sources: list[Choice]


PLACE_CHOICES = [*(Choice(value=slug, label=location_search_name(slug)) for slug in sorted(
              COUNTRIES, key=location_search_name)),
          *(Choice(value=slug, label=place.label) for slug, place in sorted(CITIES.items(),
                                                                            key=lambda item: item[1].label))]
SOURCE_CHOICES = sorted((Choice(value=source, label=board_name(source)) for source in HARVEST_SOURCES),
                 key=lambda choice: choice.label.casefold())


@router.get("")
def alerts_page(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> AlertsPage:
    """Their alerts, each with how much it found lately, and what the page offers to make one with."""
    user = get(db, User, user_id)
    found = {row["_id"]: row for row in db[UserJobState.COLLECTION].aggregate([
        {"$match": {"user_id": user_id, "alerted_at": {"$gte": now() - timedelta(days=FOUND_DAYS)}}},
        {"$unwind": "$alerts"},
        {"$group": {"_id": "$alerts", "n": {"$sum": 1}, "last": {"$max": "$alerted_at"}}}])}
    alerts = [_out(alert, found.get(alert.id))
              for alert in find(db, Alert, {"user_id": user_id}, sort=[("_id", 1)])]
    scored = has(db, user_id, "match_scores") and fit.profile(db, user) is not None
    most = limits.resolve(user, "alerts_per_account")
    # None is unlimited, and the page says so.
    return AlertsPage(alerts=alerts, can=has(db, user_id, "alerts"), most=most, scores=scored,
                      checked_at=last_read(db), found_days=FOUND_DAYS, places=PLACE_CHOICES,
                      sources=SOURCE_CHOICES)


@router.post("")
def make(body: AlertIn, db: Database = Depends(get_db),
         user_id: _uuid.UUID = Depends(with_alerts)) -> AlertOut:
    """A new alert, from the next job that joins their list."""
    most = limits.resolve(get(db, User, user_id), "alerts_per_account")
    if most is not None and db[Alert.COLLECTION].count_documents({"user_id": user_id}) >= most:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"You have {most} alerts, the most you can have. Delete one to make "
                                   "another.")
    alert = insert(db, Alert(user_id=user_id, **_tidy(db, user_id, body)))
    audit(logger, "alerts.made", user=user_id, alert=alert.id, channels=",".join(alert.channels))
    return _out(alert, None)


@router.put("/{alert_id}")
def change(alert_id: int, body: AlertIn, db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> AlertOut:
    """An alert changed. Switching one on needs a plan with alerts; switching it off never does."""
    if body.on:
        with_alerts(db, user_id)
    _theirs(db, user_id, alert_id)
    db[Alert.COLLECTION].update_one({"_id": alert_id}, {"$set": _tidy(db, user_id, body)})
    audit(logger, "alerts.changed", user=user_id, alert=alert_id, on=body.on)
    return _out(_theirs(db, user_id, alert_id), _found(db, user_id, alert_id))


@router.delete("/{alert_id}")
def delete(alert_id: int, db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """An alert gone. What it found stays found; nothing more is sent for it."""
    _theirs(db, user_id, alert_id)
    db[Alert.COLLECTION].delete_one({"_id": alert_id, "user_id": user_id})
    audit(logger, "alerts.deleted", user=user_id, alert=alert_id)
    return {"status": "deleted"}


@router.get("/{alert_id}/jobs")
def found_jobs(alert_id: int, db: Database = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> list[FeedJob]:
    """What this alert found, the latest first, as the Jobs page shows a job: less a job they skipped or
    applied to, and one that closed."""
    alert = _theirs(db, user_id, alert_id)
    rows = db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "alerts": alert_id, "applied_at": None, "dismissed_at": None},
        {"job_id": 1}, sort=[("alerted_at", -1)], limit=MARKED_SHOWN)
    words = list(alert.keywords)
    if alert.search:
        words += green_words(TenantRepository(db).get_config(user_id))
    return job_cards(db, user_id, [row["job_id"] for row in rows], open_only=True, why=True, words=words)


def _tidy(db: Database, user_id: _uuid.UUID, body: AlertIn) -> dict:
    """What an alert stores, checked: places and job sites the page offers, and something to look for."""
    keywords, companies = tidy_words(body.keywords), tidy_names(body.companies)
    if unknown := [place for place in body.places if place not in COUNTRIES and place not in CITIES]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unknown places: {', '.join(unknown)}.")
    if unknown := [source for source in body.sources if source not in HARVEST_SOURCES]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unknown job sites: {', '.join(unknown)}.")
    if not (body.search or keywords or companies or body.min_fit):
        # A place or a job site alone is every job there: thousands a day, which nobody reads.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Add a word or a company, pick a match score, or follow My search, so the "
                                   "alert knows what to look for.")
    if body.min_fit and not has(db, user_id, "match_scores"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail="Match scores come with a paid plan.")
    learn(db, keywords)
    return {"name": body.name, "search": body.search, "keywords": keywords, "companies": companies,
            "places": list(dict.fromkeys(body.places)), "sources": list(dict.fromkeys(body.sources)),
            "min_fit": body.min_fit, "channels": list(dict.fromkeys(body.channels)), "on": body.on}


def _theirs(db: Database, user_id: _uuid.UUID, alert_id: int) -> Alert:
    alert = find_one(db, Alert, {"_id": alert_id, "user_id": user_id})
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such alert")
    return alert


def _found(db: Database, user_id: _uuid.UUID, alert_id: int) -> dict | None:
    rows = list(db[UserJobState.COLLECTION].aggregate([
        {"$match": {"user_id": user_id, "alerts": alert_id,
                    "alerted_at": {"$gte": now() - timedelta(days=FOUND_DAYS)}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "last": {"$max": "$alerted_at"}}}]))
    return rows[0] if rows else None


def _out(alert: Alert, found: dict | None) -> AlertOut:
    found = found or {"n": 0, "last": None}
    return AlertOut(**{key: value for key, value in alert.to_doc().items() if key not in ("_id", "user_id")},
                    id=alert.id, found=found["n"], last_found_at=found["last"])
