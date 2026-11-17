"""Who has which plan, and what each plan lets an account do. One answer, asked wherever the site gives
something, so no page can give it to somebody another holds back from.

Three plans, fixed here (owner, 2026-11-16: "make sure this remains from now on"), each with everything the
one before it has (`PLANS`): Free, the whole of the site with each job a week after it is found; Starter, the
jobs sooner, alerts and match scores; and Member, every job the moment it is found, CVs and cover letters, a
job added by its link and the team's own chat. What an account may do is asked by area (`has`, `having`),
never by plan, so a page asks "does this account have alerts", not "which plan is it on". How long a job
takes to reach the list of a plan that waits for it is the operator's (`WAITS`).

The site is everybody's (owner, 2026-11-12): a free account signs up, sets up its search and uses every page.
A paid plan is a Stripe subscription, seven days free and then billed automatically, managed at Stripe; which
plan it is for is its price there, copied onto the account (`subscription_plan`). Stripe is the record of it;
an account holds Stripe's answer as last read (`services.api.billing_routes.sync`). Stripe moves a
subscription between its states itself — trialing to active when the first payment goes through, past_due
when one fails, canceled when a cancelled period runs out — so nothing here compares one of its dates with
this server's clock. Stripe's test clocks run ahead of it, and leadops' rule that did kept a cancelled account
open.
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timedelta

from pymongo.database import Database

from libs import settings
from libs.db import next_ids, now
from libs.db.models import User

# What pays for a plan: a free trial, or a subscription whose last payment went through. A failed payment
# ends what it gives at once (owner, 2026-11-11), and paying gives it back.
PAID = ("trialing", "active")
# A subscription still running, paid up or not. A second must not start beside it, so instead of the
# payment form they get Stripe's portal, to pay what is owed or change it.
ONGOING = (*PAID, "past_due", "unpaid", "paused")

FREE, STARTER, MEMBER = "free", "starter", "member"

# What the paid plans give, each asked for by name where the site gives it (`has`, `having`), in the order the
# pricing cards say them. Their words on the cards are the operator's, in Admin (`pricing_lines`, owner
# 2026-11-17); which card ticks which is `PLANS`, so no wording can promise what a plan does not give.
AREAS: tuple[str, ...] = (
    # A job reaches a member's list once its text is read, minutes after it is found (`libs.matching`).
    "early_jobs",
    # Sent as a job joins their list (`libs.notify.owed` reads `libs.matching`), so a plan that waits for a
    # job is alerted when its wait is over.
    "alerts",
    # Shown on the job cards to whoever has it, and a filter on the Jobs page (`libs.fit`).
    "match_scores",
    "cvs",
    "covers",
    # A line of its own on the cards (owner, 2026-11-16: "worth saying because its high value"), though
    # nothing asks for it by name: every document is checked as it is made
    # (`services.docgen.pdf.inspect_pdf`), so it comes with `cvs` and `covers` and with nothing else, which
    # `tests/test_pricing.py` holds the plans to. Not checked the same way: a CV's text, its headings in order
    # and its email read back as an ATS reads them; a letter's text alone, that it reads back whole and in
    # lines. Delivered either way, with the answer kept (`BuiltDocument.ats_ok`): "checked", never "passes".
    "ats_checks",
    # Add a job (`services.api.link_routes`): theirs to track, with a CV and a letter for it like any other.
    "add_job",
    # A chat the owner runs (`members_channel_url`), linked on the Help page: promised only while it is set.
    "team_access",
)

# What each plan has of them, fixed in code (owner, 2026-11-16), each with all the one before it has.
PLANS: dict[str, tuple[str, ...]] = {
    FREE: (),
    STARTER: ("alerts", "match_scores"),
    MEMBER: tuple(AREAS),
}

# The setting that says how long after it is found a job reaches the list of each plan without `early_jobs`:
# a week for Free (owner, 2026-11-12), three days for Starter (2026-11-16), each the operator's in Admin.
WAITS = {FREE: "free_delay_hours", STARTER: "starter_delay_hours"}


def admin_emails() -> set[str]:
    """`ADMIN_EMAILS`, read per call rather than at import: a restart is a slow way to fix an env var."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def is_admin(user: User) -> bool:
    """An operator, by the env var or by the row."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


def members() -> dict:
    """The accounts on a paid plan, or given one, as a filter on `users`: paying or in their trial, let in by
    an admin (`free_access_at`, whatever Stripe says), or an admin."""
    return {"$or": [{"subscription_status": {"$in": list(PAID)}}, {"free_access_at": {"$ne": None}},
                    {"is_admin": True}, {"email": {"$in": sorted(admin_emails())}}]}


def is_member(db: Database, user_id: _uuid.UUID) -> bool:
    """Whether this account is on a paid plan, or given one: asked exactly as `members` asks it. What that
    plan lets it do is `has`."""
    return db[User.COLLECTION].count_documents({"_id": user_id, **members()}, limit=1) > 0


def starters() -> dict:
    """The accounts on the Starter plan, as a filter on `users`: paying for it, and neither let in by an
    admin nor one, who have everything whatever they pay for."""
    return {"subscription_status": {"$in": list(PAID)}, "subscription_plan": STARTER,
            "free_access_at": None, "is_admin": {"$ne": True}, "email": {"$nin": sorted(admin_emails())}}


def having(area: str) -> dict:
    """The accounts that have this area, as a filter on `users`: every account on a plan that has it. Free has
    none, so an area of Starter's is every paid account's, and one of Member's alone every paid one's but
    Starter's."""
    if area not in AREAS:
        raise KeyError(f"unknown area {area!r}; known: {sorted(AREAS)}")
    return members() if area in PLANS[STARTER] else {"$and": [members(), {"$nor": [starters()]}]}


def has(db: Database, user_id: _uuid.UUID, area: str) -> bool:
    """Whether this account has this area, asked exactly as `having` asks it."""
    return db[User.COLLECTION].count_documents({"$and": [{"_id": user_id}, having(area)]}, limit=1) > 0


def tier(db: Database, user_id: _uuid.UUID) -> str:
    """The plan this account is on: "free", "starter" or "member"."""
    if not is_member(db, user_id):
        return FREE
    on_starter = db[User.COLLECTION].count_documents({"_id": user_id, **starters()}, limit=1) > 0
    return STARTER if on_starter else MEMBER


def areas_of(db: Database, user_id: _uuid.UUID) -> list[str]:
    """Every area this account has, for a page that shows or hides by it."""
    return list(PLANS[tier(db, user_id)])


def choosing(user: User, member: bool) -> bool:
    """Whether this account is still to pick its plan, Free or a paid one, on the page between the email link
    and setting up (owner, 2026-11-12): not on a paid plan (an admin, free access or a subscription paying),
    never picked Free, never had a subscription (a lapsed one is free without being asked again), and not set
    up yet (an account from before the choice is not sent back to it)."""
    return (not member and user.plan_chosen_at is None and user.subscription_status is None
            and user.onboarding_completed_at is None)


def wait_hours(db: Database | settings.Snapshot, plan: str) -> int:
    """How long after it is found a job reaches the list of an account on this plan, in hours: none with
    `early_jobs`, else the plan's own setting (`WAITS`)."""
    return 0 if "early_jobs" in PLANS[plan] else int(settings.get(db, WAITS[plan]))


def delay_hours(db: Database, user: User) -> int:
    """How long after it is found a job reaches this account's list, in hours (owner, 2026-11-12: "only jobs
    that have passed the 7 day mark should appear for them"; 2026-11-16: Starter "once they are 3 days
    old")."""
    return wait_hours(db, tier(db, user.id))


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
