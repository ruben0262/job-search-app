"""The pricing on the landing page (owner, 2026-11-12 and 2026-11-16): Free, a week behind and without
documents; Starter, the jobs at once and the alerts; and the membership, everything, with its free trial.

What the three cards say is the operator's, set in Admin (`pricing_cards`). What a plan costs is Stripe's,
what it is shown as reduced from is `price_was`, how many days are free is `trial_days`, how far behind a
free account is `free_delay_hours` and what Starter has of a membership is `starter_areas`, each read where
the site itself reads them (`billing_routes.plan`, `libs.membership`), so the page cannot promise a price, a
trial, a delay or an area the site does not keep.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from pymongo.database import Database

from libs import settings
from libs.db.models import User
from libs.membership import AREAS, STARTER, starter_areas
from libs.obs import audit
from services.api import billing_routes
from services.api.admin_routes import require_admin
from services.api.billing_routes import Plan
from services.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pricing"])

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class PricingCard(BaseModel):
    """One card's words, each with room for what the card has room for."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The band under the price, read at a glance: "Price locked for life". May be empty.
    highlight: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    # What it gives, a line each, ticked.
    benefits: list[Line] = Field(min_length=1, max_length=12)
    # What it does not, a line each, crossed out: what the membership adds, on the free card.
    left_out: list[Line] = Field(max_length=12)
    button: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The small print under the button; may be empty.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]


class PricingCards(BaseModel):
    free: PricingCard
    starter: PricingCard
    member: PricingCard


class Area(BaseModel):
    """One thing a membership gives (`libs.membership.AREAS`), in the words a card says it."""
    key: str
    words: str
    # Whether the Starter plan has it: ticked on its card, or crossed out.
    starter: bool


class Pricing(BaseModel):
    cards: PricingCards
    # A new subscription's free days; 0 is none.
    trial_days: int
    # How long after a member a free account sees a job.
    free_delay_hours: int
    # Stripe's price, or None while Stripe is not set up or cannot be read: the card then names no price.
    plan: Plan | None
    # The Starter plan's, or None while it has no price at Stripe: its card is then not shown to visitors.
    starter: Plan | None
    # What each plan is shown as reduced from, in its currency's smallest unit; 0 or missing shows none.
    was: dict[str, int]
    areas: list[Area]


def cards_of(db: Database) -> PricingCards:
    """The cards as saved, or the defaults if what is saved no longer reads. A card there was none of when
    they were saved (Starter's) is the default one, and a field there was none of (`highlight`) is the
    default's, so the words saved keep and what came since shows."""
    saved = settings.get(db, "pricing_cards")
    try:
        return PricingCards.model_validate({
            name: {**default, **(saved.get(name) or {})}
            for name, default in settings.DEFAULT_PRICING_CARDS.items()})
    except (ValidationError, TypeError) as exc:
        logger.warning("pricing: the saved cards do not read (%s); the landing page shows the defaults", exc)
        return PricingCards.model_validate(settings.DEFAULT_PRICING_CARDS)


@router.get("/pricing")
def pricing(db: Database = Depends(get_db)) -> Pricing:
    """Public: what the landing page's pricing says, before anybody has an account."""
    switched, was = starter_areas(db), settings.get(db, "price_was")
    return Pricing(cards=cards_of(db), trial_days=int(settings.get(db, "trial_days")),
                   free_delay_hours=int(settings.get(db, "free_delay_hours")), plan=billing_routes.plan(db),
                   starter=billing_routes.plan(db, STARTER),
                   # A figure only: anything else saved there shows no reduced-from price, not an error.
                   was={name: amount for name, amount in (was if isinstance(was, dict) else {}).items()
                        if isinstance(amount, int) and amount > 0},
                   areas=[Area(key=key, words=words, starter=key in switched)
                          for key, words in AREAS.items()])


@router.put("/admin/pricing")
def set_pricing(body: PricingCards, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> Pricing:
    """Every card's words, saved together, as the landing page will show them."""
    settings.put(db, "pricing_cards", body.model_dump(), by=admin.email)
    audit(logger, "admin.pricing", user=admin.id)
    return pricing(db)
