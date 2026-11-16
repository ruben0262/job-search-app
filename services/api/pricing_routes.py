"""The pricing on the landing page (owner, 2026-11-12 and 2026-11-16): Free, the whole site with each job a
week after it is found; Starter, the jobs sooner, alerts and match scores; and Member, everything, with its
free trial.

What each card says of what its plan gives and lacks is worked out here, from the plans fixed in code
(`libs.membership.PLANS`, said in `AREAS`' and `EVERYONE`'s words) and the settings the site itself reads (how
long each plan waits for a job, the members' chat), so no card can promise what the site does not keep. A
card's name, band, button and small print are the operator's, set in Admin (`pricing_cards`). What a plan
costs is Stripe's, what it is shown as reduced from is `price_was` and how many days are free is `trial_days`,
each read where the site itself reads them (`billing_routes.plan`).
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints, ValidationError
from pymongo.database import Database

from libs import settings
from libs.db.models import User
from libs.membership import AREAS, EVERYONE, FREE, MEMBER, PLANS, STARTER, Said, wait_hours
from libs.obs import audit
from services.api import billing_routes
from services.api.admin_routes import require_admin
from services.api.billing_routes import Plan
from services.api.dashboard_routes import _span
from services.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pricing"])

class PricingCard(BaseModel):
    """One card's own words, each with room for what the card has room for."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The band under the price, read at a glance: "Price locked for life". May be empty.
    highlight: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    button: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    # The small print under the button; may be empty.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]


class PricingCards(BaseModel):
    free: PricingCard
    starter: PricingCard
    member: PricingCard


class Line(BaseModel):
    """One line of a card: a few words, and what its question mark says when it is pointed at or tapped."""
    text: str
    more: str


class PlanLines(BaseModel):
    """What a card says of its plan, never typed (`lines_of`)."""
    # The plan whose card is before this one, all of which this one has: "Everything in Free", first on the
    # card. None on Free's.
    everything_in: Literal["free", "starter"] | None = None
    # Ticked: how soon it sees a new job where that is sooner than the plan before it, then what it adds.
    gives: list[Line]
    # Crossed out: what a plan after it gives and it does not.
    lacks: list[Line]


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


def _line(said: Said) -> Line:
    return Line(text=said.short, more=said.more)


def _soon(hours: int, free: int) -> Line:
    """How soon a plan sees a new job, as its card says it (owner, 2026-11-16: "jobs that are 7 days or older
    appear to users on the free plan, and 3 days or older on the starter plan")."""
    if not hours:
        return _line(AREAS["early_jobs"])
    sooner = f", {_span(free - hours)} before Free accounts see it" if free > hours else ""
    return Line(text=f"Jobs that are {_span(hours)} or older",
                more=f"A new job joins your list {_span(hours)} after we find it{sooner}.")


def lines_of(db: Database | settings.Snapshot, *, starter_sold: bool) -> PlansLines:
    """What each card says of its plan. A paid card begins "Everything in" the card before it, Starter's once
    it is sold (`starter_sold`), then ticks how soon it sees a new job (`libs.membership.wait_hours`) where
    that is sooner, and what it adds; Free's ticks how soon, and what every account has (`EVERYONE`). Each
    crosses out what a plan after it gives. The members' chat is said only while it has a link
    (`members_channel_url`): what the site cannot give yet, no card promises."""
    waits = {plan: wait_hours(db, plan) for plan in PLANS}
    said = [area for area in AREAS if area != "early_jobs"
            and (area != "team_access" or settings.get(db, "members_channel_url"))]
    before = {FREE: None, STARTER: FREE, MEMBER: STARTER if starter_sold else FREE}

    def card(plan: str) -> PlanLines:
        under = before[plan]
        has = [area for area in said if area in PLANS[plan] and (under is None or area not in PLANS[under])]
        sooner = under is None or waits[plan] < waits[under]
        return PlanLines(
            everything_in=under,
            gives=[*([_soon(waits[plan], waits[FREE])] if sooner else []),
                   *(map(_line, EVERYONE) if under is None else []), *(_line(AREAS[area]) for area in has)],
            lacks=[*([_line(AREAS["early_jobs"])] if waits[plan] else []),
                   *(_line(AREAS[area]) for area in said if area not in PLANS[plan])])

    return PlansLines(**{plan: card(plan) for plan in PLANS})


@router.get("/pricing")
def pricing(db: Database = Depends(get_db)) -> Pricing:
    """Public: what the landing page's pricing says, before anybody has an account."""
    stored = settings.snapshot(db)
    was = settings.get(stored, "price_was")
    starter = billing_routes.plan(db, STARTER)
    return Pricing(cards=cards_of(stored), lines=lines_of(stored, starter_sold=starter is not None),
                   trial_days=int(settings.get(stored, "trial_days")),
                   plan=billing_routes.plan(db), starter=starter,
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
