"""Who has a membership: paying or in its trial, let in by an admin, or an admin. One answer, asked wherever
the site gives more to members, so no page can give it to somebody another holds back from.

There are two plans to pay for (owner, 2026-11-16): the membership, which gives everything (`AREAS`), and
Starter, which gives the areas the operator switched on for it (`starter_areas`). Which one a subscription is
for is its price at Stripe, copied onto the account (`subscription_plan`). What an account may do is asked
by area (`has`, `having`), never by plan, so a switch flipped in Admin is the whole of the change.

The site is everybody's (owner, 2026-11-12): a free account signs up, sets up its search and uses every page,
seeing the jobs a week after a member does (`jobs_as_of`) and making no CVs or cover letters. A membership
is a Stripe subscription, seven days free and then billed automatically, managed at Stripe; it gives the jobs
the moment they are posted, the documents, and alerts. Stripe is the record of it; an account holds Stripe's
answer as last read (`services.api.billing_routes.sync`). Stripe moves a subscription between its states
itself — trialing to active when the first payment goes through, past_due when one fails, canceled when a
cancelled period runs out — so nothing here compares one of its dates with this server's clock. Stripe's
test clocks run ahead of it, and leadops' rule that did kept a cancelled account open.
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timedelta

from pymongo.database import Database

from libs import settings
from libs.db import next_ids, now
from libs.db.models import User

# What pays for a membership: a free trial, or a subscription whose last payment went through. A failed
# payment ends what it gives at once (owner, 2026-11-11), and paying gives it back.
PAID = ("trialing", "active")
# A subscription still running, paid up or not. A second must not start beside it, so instead of the
# payment form they get Stripe's portal, to pay what is owed or change it.
ONGOING = (*PAID, "past_due", "unpaid", "paused")

STARTER = "starter"
# What a membership gives, each asked for by name where the site gives it, and each said on the pricing
# cards in these words (`pricing_routes`): one list, so a plan's card cannot promise an area the site holds
# back from it. The membership has all of them.
AREAS: dict[str, str] = {
    "early_jobs": "New jobs the moment they're found, not a week later",
    "alerts": "Alerts on Telegram, in your browser or by email",
    "cvs": "A CV for any job, in one tap, with your own AI account",
    "covers": "A cover letter for any job, in one tap",
    "add_job": "Any job you find elsewhere, added by its link",
}


def admin_emails() -> set[str]:
    """`ADMIN_EMAILS`, read per call rather than at import: a restart is a slow way to fix an env var."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def is_admin(user: User) -> bool:
    """An operator, by the env var or by the row."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


def members() -> dict:
    """The accounts with a membership, as a filter on `users`: paying or in their trial, let in by an admin
    (`free_access_at`, whatever Stripe says), or an admin."""
    return {"$or": [{"subscription_status": {"$in": list(PAID)}}, {"free_access_at": {"$ne": None}},
                    {"is_admin": True}, {"email": {"$in": sorted(admin_emails())}}]}


def is_member(db: Database, user_id: _uuid.UUID) -> bool:
    """Whether this account has a plan it pays for, or the like: asked exactly as `members` asks it. What
    that plan lets it do is `has`."""
    return db[User.COLLECTION].count_documents({"_id": user_id, **members()}, limit=1) > 0


def starters() -> dict:
    """The accounts on the Starter plan, as a filter on `users`: paying for it, and neither let in by an
    admin nor one, who have everything whatever they pay for."""
    return {"subscription_status": {"$in": list(PAID)}, "subscription_plan": STARTER,
            "free_access_at": None, "is_admin": {"$ne": True}, "email": {"$nin": sorted(admin_emails())}}


def starter_areas(db: Database) -> list[str]:
    """The areas switched on for the Starter plan, in `AREAS`' order. A name that is no area is none."""
    switched = settings.get(db, "starter_areas")
    return [area for area in AREAS if isinstance(switched, list) and area in switched]


def having(db: Database, area: str) -> dict:
    """The accounts that have this area of a membership, as a filter on `users`: every member, less the ones
    on Starter while the area is switched off for it."""
    if area not in AREAS:
        raise KeyError(f"unknown area {area!r}; known: {sorted(AREAS)}")
    return members() if area in starter_areas(db) else {"$and": [members(), {"$nor": [starters()]}]}


def has(db: Database, user_id: _uuid.UUID, area: str) -> bool:
    """Whether this account has this area, asked exactly as `having` asks it."""
    return db[User.COLLECTION].count_documents({"$and": [{"_id": user_id}, having(db, area)]}, limit=1) > 0


def tier(db: Database, user_id: _uuid.UUID) -> str:
    """The plan this account is on, as the site says it: "free", "starter" or "member"."""
    if not is_member(db, user_id):
        return "free"
    on_starter = db[User.COLLECTION].count_documents({"_id": user_id, **starters()}, limit=1) > 0
    return STARTER if on_starter else "member"


def areas_of(db: Database, user_id: _uuid.UUID) -> list[str]:
    """Every area this account has, for a page that shows or hides by it."""
    return {"free": [], STARTER: starter_areas(db)}.get(tier(db, user_id), list(AREAS))


def choosing(user: User, member: bool) -> bool:
    """Whether this account is still to pick its plan, Free or the membership, on the page between the email
    link and setting up (owner, 2026-11-12): not a member (an admin, free access or a subscription paying),
    never picked Free, never had a subscription (a lapsed member is free without being asked again), and not
    set up yet (an account from before the choice is not sent back to it)."""
    return (not member and user.plan_chosen_at is None and user.subscription_status is None
            and user.onboarding_completed_at is None)


def delay_hours(db: Database, user: User) -> int:
    """How long after a member this account sees a job, in hours: none for one that has the jobs at once,
    `free_delay_hours` for the rest (owner, 2026-11-12: "only jobs that have passed the 7 day mark should
    appear for them")."""
    return 0 if has(db, user.id, "early_jobs") else int(settings.get(db, "free_delay_hours"))


def jobs_as_of(db: Database, user: User, delay: int | None = None) -> datetime | None:
    """The newest a job can have reached a member's list and be in this account's (`libs.matching`): None, no
    limit, for a member. `delay` is `delay_hours`, for a caller that has it already."""
    delay = delay_hours(db, user) if delay is None else delay
    return now() - timedelta(hours=delay) if delay else None


def free_for_existing(db: Database) -> int:
    """Free access for every account there was before memberships began (owner, 2026-11-11: "existing
    accounts get free access right now all of them"); how many. Once: on the first schema step that has it
    (`python -m libs.db`, the deploy's), its counter (`next_ids`) saying so after, so an admin's later Off is
    never undone and nobody who signs up afterwards gets in without paying."""
    if next_ids(db, "membership.free_for_existing")[0] != 1:
        return 0
    return db[User.COLLECTION].update_many({"free_access_at": None},
                                           {"$set": {"free_access_at": now()}}).modified_count
