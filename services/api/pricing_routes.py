"""The pricing on the landing page (owner, 2026-11-12 and 2026-11-16): Free, the whole site with each job a
week after it is found; Starter, the jobs sooner, alerts and match scores; and Member, everything, with its
free trial.

Every word on the cards is the operator's, set in Admin: a card's name, band, button and small print
(`pricing_cards`), and the lines of what the plans give and lack (`pricing_lines`, owner 2026-11-17:
"appear in admin all the time"). Which card ticks a line and which crosses it out is worked out here, from
the plans fixed in code (`libs.membership.PLANS`) and the settings the site itself reads (how long each plan
waits for a job, the members' chat), so no wording can promise what the site does not keep. What a plan costs
is Stripe's, what it is shown as reduced from is `price_was` and how many days are free is `trial_days`, each
read where the site itself reads them (`billing_routes.plan`).
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator
from pymongo.database import Database

from libs import settings
from libs.db.models import User
from libs.membership import AREAS, FREE, MEMBER, PLANS, STARTER, trial_days, wait_hours
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
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    more: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)]


# The paid areas a card says in lines of their own; how soon a plan sees a job is `wait` and `now`.
LINED_AREAS = tuple(area for area in AREAS if area != "early_jobs")


class PricingLines(BaseModel):
    """The words of every line the cards can show (`pricing_lines`), whichever card ticks or crosses it."""
    # How soon a plan that waits sees a new job: "{wait}" is that plan's wait, so no number is typed.
    wait: Line
    # How soon a plan that does not wait sees one.
    now: Line
    # A paid card's first line, all of the card before it (owner, 2026-11-17: "everything in free plan"):
    # "{plan}" is that card's name.
    everything: Line
    # What every account has: Free's own lines, and "Everything in Free" on the cards after it.
    everyone: Annotated[list[Line], Field(min_length=1, max_length=8)]
    # What each paid area gives, a line or more each.
    areas: dict[str, Annotated[list[Line], Field(min_length=1, max_length=4)]]

    @field_validator("wait")
    @classmethod
    def _says_the_wait(cls, line: Line) -> Line:
        if "{wait}" not in line.text:
            raise ValueError("the line for a plan that waits must say {wait}, where its wait goes")
        return line

    @field_validator("everything")
    @classmethod
    def _says_the_plan(cls, line: Line) -> Line:
        if "{plan}" not in line.text:
            raise ValueError("the first line of a paid card must say {plan}, where the plan before it goes")
        return line

    @field_validator("areas")
    @classmethod
    def _every_area(cls, areas: dict[str, list[Line]]) -> dict[str, list[Line]]:
        if set(areas) != set(LINED_AREAS):
            raise ValueError(f"the lines must name each of {list(LINED_AREAS)}, and nothing else")
        return areas


class PricingWords(BaseModel):
    """Everything an operator words, saved together from Admin."""
    cards: PricingCards
    lines: PricingLines


class PricingEditor(PricingWords):
    """The words, and what Admin says beside them so it never guesses at the plans: which plans tick each
    area's lines (`libs.membership.PLANS`; the rest cross them out), and each waiting plan's wait as {wait}
    reads on its card ("7 days")."""
    ticked_on: dict[str, list[Literal["free", "starter", "member"]]]
    waits: dict[str, str]


class PlanLines(BaseModel):
    """What a card says of its plan: the operator's lines, ticked and crossed out by the plans
    (`lines_of`)."""
    # The plan whose card is before this one, all of which this one has. None on Free's.
    everything_in: Literal["free", "starter"] | None = None
    # That said, first on the card: "Everything in Free plan", in the operator's words with that card's name.
    everything: Line | None = None
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
    # The membership's free days for a new subscription (`libs.membership.TRIALS`); 0 is none. Starter has
    # none: its own are its `Plan.trial_days`.
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


def words_of(db: Database | settings.Snapshot) -> PricingLines:
    """The lines as saved, or the defaults where nothing is saved. An area there were no lines for when they
    were saved has its default ones, so the words saved keep and an area that came since shows; saved words
    that no longer read are the defaults, said in the log."""
    default = settings.DEFAULT_PRICING_LINES
    saved = settings.get(db, "pricing_lines")
    try:
        areas = {**default["areas"], **{area: lines for area, lines in (saved.get("areas") or {}).items()
                                         if area in LINED_AREAS}}
        return PricingLines.model_validate({**default, **saved, "areas": areas})
    except (ValidationError, TypeError, AttributeError) as exc:
        logger.warning("pricing: the saved lines do not read (%s); the cards show the defaults", exc)
        return PricingLines.model_validate(default)


def lines_of(db: Database | settings.Snapshot, *, starter_sold: bool, words: PricingLines,
             cards: PricingCards) -> PlansLines:
    """What each card says of its plan, in the operator's words (`words`). A paid card begins with everything
    in the card before it, by that card's name (`cards`), Starter's once it is sold (`starter_sold`), then
    ticks how soon it sees a new job (`libs.membership.wait_hours`) where that is sooner, and the lines of
    what it adds; Free's ticks how soon, and what every account has. Each crosses out the lines of what a plan
    after it gives. The members' chat is said only while it has a link (`members_channel_url`): what the site
    cannot give yet, no card promises."""
    waits = {plan: wait_hours(db, plan) for plan in PLANS}
    said = [area for area in LINED_AREAS if area != "team_access" or settings.get(db, "members_channel_url")]
    before = {FREE: None, STARTER: FREE, MEMBER: STARTER if starter_sold else FREE}

    def soon(hours: int) -> Line:
        """How soon, with this plan's wait where the words say {wait}: "See new jobs after 7 days"."""
        if not hours:
            return words.now
        return Line(text=words.wait.text.replace("{wait}", _span(hours)),
                    more=words.wait.more.replace("{wait}", _span(hours)))

    def card(plan: str) -> PlanLines:
        under = before[plan]
        has = [area for area in said if area in PLANS[plan] and (under is None or area not in PLANS[under])]
        sooner = under is None or waits[plan] < waits[under]
        everything = None if under is None else Line(
            text=words.everything.text.replace("{plan}", getattr(cards, under).name),
            more=words.everything.more.replace("{plan}", getattr(cards, under).name))
        return PlanLines(
            everything_in=under, everything=everything,
            gives=[*([soon(waits[plan])] if sooner else []), *(words.everyone if under is None else []),
                   *(line for area in has for line in words.areas[area])],
            lacks=[*([words.now] if waits[plan] else []),
                   *(line for area in said if area not in PLANS[plan] for line in words.areas[area])])

    return PlansLines(**{plan: card(plan) for plan in PLANS})


def _pricing(db: Database, cards: PricingCards | None = None, words: PricingLines | None = None) -> Pricing:
    """The pricing as a visitor sees it: in the words saved, or in these (Admin's preview, and what it
    saves)."""
    stored = settings.snapshot(db)
    was = settings.get(stored, "price_was")
    starter = billing_routes.plan(db, STARTER)
    cards = cards or cards_of(stored)
    return Pricing(cards=cards,
                   lines=lines_of(stored, starter_sold=starter is not None, words=words or words_of(stored),
                                  cards=cards),
                   trial_days=trial_days(stored, MEMBER),
                   plan=billing_routes.plan(db), starter=starter,
                   # A figure only: anything else saved there shows no reduced-from price, not an error.
                   was={name: amount for name, amount in (was if isinstance(was, dict) else {}).items()
                        if isinstance(amount, int) and amount > 0})


@router.get("/pricing")
def pricing(db: Database = Depends(get_db)) -> Pricing:
    """Public: what the landing page's pricing says, before anybody has an account."""
    return _pricing(db)


@router.get("/admin/pricing")
def pricing_words(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> PricingEditor:
    """Every word on the cards as it stands, saved or the defaults, for Admin to show and change: nothing
    there is ever blank to be typed in."""
    stored = settings.snapshot(db)
    ticked_on = {area: [plan for plan in PLANS if area in PLANS[plan]] for area in LINED_AREAS}
    return PricingEditor(cards=cards_of(stored), lines=words_of(stored), ticked_on=ticked_on,
                         waits={plan: _span(hours) for plan in PLANS if (hours := wait_hours(stored, plan))})


@router.post("/admin/pricing/preview")
def preview_pricing(body: PricingWords, db: Database = Depends(get_db),
                    admin: User = Depends(require_admin)) -> Pricing:
    """The cards as these words would make them, worked out as the landing page's are, and nothing saved."""
    return _pricing(db, body.cards, body.lines)


@router.put("/admin/pricing")
def set_pricing(body: PricingWords, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> Pricing:
    """Every word on the cards, saved together, as the landing page will show them."""
    settings.put(db, "pricing_cards", body.cards.model_dump(), by=admin.email)
    settings.put(db, "pricing_lines", body.lines.model_dump(), by=admin.email)
    audit(logger, "admin.pricing", user=admin.id)
    return _pricing(db)
