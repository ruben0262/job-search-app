"""The pricing on the landing page (owner, 2026-11-12): Free, a week behind and without documents, and the
membership, everything at once, with its free trial.

What the two cards say is the operator's, set in Admin (`pricing_cards`). What the membership costs is
Stripe's, how many days of it are free is `trial_days` and how far behind a free account is
`free_delay_hours`, each read where the site itself reads them (`billing_routes.plan`, `libs.membership`), so
the page cannot promise a price, a trial or a delay the site does not keep.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from pymongo.database import Database

from libs import settings
from libs.db.models import User
from libs.obs import audit
from services.api.admin_routes import require_admin
from services.api.billing_routes import Plan, plan
from services.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pricing"])

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class PricingCard(BaseModel):
    """One card's words, each with room for what the card has room for."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The line under the name; may be empty.
    tagline: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
    # What it gives, a line each, ticked.
    benefits: list[Line] = Field(min_length=1, max_length=12)
    # What it does not, a line each, crossed out: what the membership adds, on the free card.
    left_out: list[Line] = Field(max_length=12)
    button: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The small print under the button; may be empty.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]


class PricingCards(BaseModel):
    free: PricingCard
    member: PricingCard


class Pricing(BaseModel):
    cards: PricingCards
    # The membership's free days; 0 is none.
    trial_days: int
    # How long after a member a free account sees a job.
    free_delay_hours: int
    # Stripe's price, or None while Stripe is not set up or cannot be read: the card then names no price.
    plan: Plan | None


def cards_of(db: Database) -> PricingCards:
    """The cards as saved, or the defaults if what is saved no longer reads (a field added since)."""
    try:
        return PricingCards.model_validate(settings.get(db, "pricing_cards"))
    except ValidationError as exc:
        logger.warning("pricing: the saved cards do not read (%s); the landing page shows the defaults",
                       exc.errors()[0]["msg"])
        return PricingCards.model_validate(settings.DEFAULT_PRICING_CARDS)


@router.get("/pricing")
def pricing(db: Database = Depends(get_db)) -> Pricing:
    """Public: what the landing page's pricing says, before anybody has an account."""
    return Pricing(cards=cards_of(db), trial_days=int(settings.get(db, "trial_days")),
                   free_delay_hours=int(settings.get(db, "free_delay_hours")), plan=plan(db))


@router.put("/admin/pricing")
def set_pricing(body: PricingCards, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> Pricing:
    """Both cards' words, saved together, as the landing page will show them."""
    settings.put(db, "pricing_cards", body.model_dump(), by=admin.email)
    audit(logger, "admin.pricing", user=admin.id)
    return pricing(db)
