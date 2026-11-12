"""Who has a membership: paying or in its trial, let in by an admin, or an admin. One answer, asked wherever
the site gives more to members, so no page can give it to somebody another holds back from.

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
    """Whether this account has a membership, asked exactly as `members` asks it."""
    return db[User.COLLECTION].count_documents({"_id": user_id, **members()}, limit=1) > 0


def choosing(user: User, member: bool) -> bool:
    """Whether this account is still to pick its plan, Free or the membership, on the page between the email
    link and setting up (owner, 2026-11-12): not a member (an admin, free access or a subscription paying),
    never picked Free, never had a subscription (a lapsed member is free without being asked again), and not
    set up yet (an account from before the choice is not sent back to it)."""
    return (not member and user.plan_chosen_at is None and user.subscription_status is None
            and user.onboarding_completed_at is None)


def delay_hours(db: Database, user: User) -> int:
    """How long after a member this account sees a job, in hours: none for a member, `free_delay_hours` for a
    free account (owner, 2026-11-12: "only jobs that have passed the 7 day mark should appear for them")."""
    return 0 if is_member(db, user.id) else int(settings.get(db, "free_delay_hours"))


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
