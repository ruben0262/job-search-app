"""The pricing on the landing page (owner, 2026-11-12 and 2026-11-16): Free, the whole site with each job a
week after it is found; Starter, the jobs sooner, alerts and match scores; and Member, everything, with its
free trial.

What each card says of what its plan gives and lacks is worked out here, from the plans fixed in code
(`libs.membership.PLANS`, said in `AREAS`' words) and the settings the site itself reads (how long each plan
waits for a job, the members' chat), so no card can promise what the site does not keep. What else a card
says is the operator's, set in Admin (`pricing_cards`). What a plan costs is Stripe's, what it is shown as
reduced from is `price_was` and how many days are free is `trial_days`, each read where the site itself reads
them (`billing_routes.plan`).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from pymongo.database import Database

from libs import settings
from libs.db.models import User
from libs.membership import AREAS, FREE, PLANS, STARTER, wait_hours
from libs.obs import audit
from services.api import billing_routes
from services.api.admin_routes import require_admin
from services.api.billing_routes import Plan
from services.api.dashboard_routes import _span
from services.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pricing"])

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class PricingCard(BaseModel):
    """One card's own words, each with room for what the card has room for."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The band under the price, read at a glance: "Price locked for life". May be empty.
    highlight: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    # What else it ticks, a line each, after what its plan gives (`PlanLines`): what every account has, on
    # Free's.
    also: list[Line] = Field(max_length=12)
    button: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The small print under the button; may be empty.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]


class PricingCards(BaseModel):
    free: PricingCard
    starter: PricingCard
    member: PricingCard


class PlanLines(BaseModel):
    """What a card says of its plan, never typed (`lines_of`)."""
    # Ticked: how soon it sees a new job, then each thing it gives.
    gives: list[str]
    # Crossed out: each thing a plan above it gives and it does not.
    lacks: list[str]


class PlansLines(BaseModel):
    free: PlanLines
    starter: PlanLines
    member: PlanLines


class Pricing(BaseModel):
    cards: PricingCards
    lines: PlansLines
    # A new subscription's free days; 0 is none.
    trial_days: int
    # Stripe's price, or None while Stripe is not set up or cannot be read: the card then names no price.
    plan: Plan | None
    # The Starter plan's, or None while it has no price at Stripe: its card is then not shown to visitors.
    starter: Plan | None
    # What each plan is shown as reduced from, in its currency's smallest unit; 0 or missing shows none.
    was: dict[str, int]


def cards_of(db: Database | settings.Snapshot) -> PricingCards:
    """The cards as saved, or the defaults if what is saved no longer reads. A card there was none of when
    they were saved is the default one, and a field there was none of (`also`, `highlight`) is the default's,
    so the words saved keep and what came since shows."""
    saved = settings.get(db, "pricing_cards")
    try:
        return PricingCards.model_validate({
            name: {**default, **(saved.get(name) or {})}
            for name, default in settings.DEFAULT_PRICING_CARDS.items()})
    except (ValidationError, TypeError) as exc:
        logger.warning("pricing: the saved cards do not read (%s); the landing page shows the defaults", exc)
        return PricingCards.model_validate(settings.DEFAULT_PRICING_CARDS)


def _soon(plan: str, hours: int, free: int) -> str:
    """How soon a plan sees a new job, as its card says it: "See every new job 3 days after we find it, 4
    days before Free accounts do"."""
    when = AREAS["early_jobs"] if not hours else f"See every new job {_span(hours)} after we find it"
    sooner = 0 if plan == FREE else free - hours
    return f"{when}, {_span(sooner)} before Free accounts do" if sooner > 0 else when


def lines_of(db: Database | settings.Snapshot) -> PlansLines:
    """What each card says of its plan: how soon it sees a new job (`libs.membership.wait_hours`), then each
    area it has, ticked, and each it has not, crossed out, the jobs the moment they are found first among
    those. The members' chat is said only while it has a link (`members_channel_url`): what the site cannot
    give yet, no card promises."""
    waits = {plan: wait_hours(db, plan) for plan in PLANS}
    said = [area for area in AREAS if area != "early_jobs"
            and (area != "team_access" or settings.get(db, "members_channel_url"))]
    return PlansLines(**{plan: PlanLines(
        gives=[_soon(plan, waits[plan], waits[FREE]), *(AREAS[area] for area in said if area in areas)],
        lacks=[*([AREAS["early_jobs"]] if waits[plan] else []),
               *(AREAS[area] for area in said if area not in areas)]) for plan, areas in PLANS.items()})


@router.get("/pricing")
def pricing(db: Database = Depends(get_db)) -> Pricing:
    """Public: what the landing page's pricing says, before anybody has an account."""
    stored = settings.snapshot(db)
    was = settings.get(stored, "price_was")
    return Pricing(cards=cards_of(stored), lines=lines_of(stored),
                   trial_days=int(settings.get(stored, "trial_days")),
                   plan=billing_routes.plan(db), starter=billing_routes.plan(db, STARTER),
                   # A figure only: anything else saved there shows no reduced-from price, not an error.
                   was={name: amount for name, amount in (was if isinstance(was, dict) else {}).items()
                        if isinstance(amount, int) and amount > 0})


@router.put("/admin/pricing")
def set_pricing(body: PricingCards, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> Pricing:
    """Every card's words, saved together, as the landing page will show them."""
    settings.put(db, "pricing_cards", body.model_dump(), by=admin.email)
    audit(logger, "admin.pricing", user=admin.id)
    return pricing(db)
