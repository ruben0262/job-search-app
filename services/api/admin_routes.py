"""The operator's read-only view of the platform."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from pymongo.database import Database

from libs import templates
from libs.contracts.config import WORKPLACE_NAMES, location_search_name, migrate_config
from libs.corpus.boards import board_name
from libs.corpus.freshness import platform_windows, shelf_life
from libs.corpus.geo import COUNTRIES
from libs.corpus.repository import CorpusRepository
from libs.db import delete_account, find, get, now
from libs.db.models import (
    AtsToken,
    BuiltDocument,
    CorpusJob,
    PushSubscription,
    QueueJob,
    User,
    UserDocument,
    UserJobState,
)
from libs.documents.read import LANGUAGE_NAMES
from libs.limits import all_for, describe, set_limit
from libs.llm.accounts import listing as ai_listing
from libs.matching import green_words, norm, scan_for, their_jobs
from libs.membership import ONGOING, admin_emails, choosing, is_admin, is_member, members
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
from libs.ops.countries import RANGES_HOURS, by_country
from libs.ops.latency import (
    latency_report,
    pipeline_latency,
    stage_detail,
)
from libs.ops.pipeline import pipeline
from libs.ops.pipeline import sources as found_by_source
from libs.settings import (
    DEFAULT_SOURCE_MIN_AGE_HOURS,
    DEFAULTS,
    HARVEST_SOURCES,
    all_settings,
    ats_companies_per_cycle,
    freshness_hours,
    pages_per_cycle,
    put,
    queries_per_cycle,
    results_per_query,
    snapshot,
    source_min_ages,
)
from libs.tenant import applications
from libs.tenant.repository import TenantRepository
from services.api.billing_routes import Plan, _stripe_answers, end_membership, hold_billing
from services.api.billing_routes import plan as billing_plan
from services.api.dashboard_routes import _span
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(db: Database = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> User:
    """404, not 403."""
    user = get(db, User, user_id)
    # The env var or the row.
    granted = user is not None and is_admin(user)
    if not granted:
        logger.warning("admin: refused %s", user_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.get("/health")
def health(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every number, plus the conditions that currently need a person."""
    state = platform_health(db)
    alerts = evaluate(state)
    audit(logger, "admin.health", user=admin.id, alerts=len(alerts))
    return {
        **state.as_dict(),
        # How long a posting takes to reach somebody.
        "latency": pipeline_latency(db),
        "alerts": [
            {"severity": alert.severity, "key": alert.key, "text": alert.text,
             "action": alert.action, "user_email": alert.user_email}
            for alert in alerts],
        "summary": render(alerts),
    }


class OutcomeRow(BaseModel):
    """How far a group of applications got; a stage counts every one that reached it or went past it."""
    group: str
    applied: int
    interview: int
    offer: int
    hired: int
    rejected: int


@router.get("/outcomes")
def outcomes(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> list[OutcomeRow]:
    """What people say happened after they applied, by the CV they sent (`libs.tenant.applications`)."""
    return [OutcomeRow(**row) for row in applications.outcomes(db)]


# Where an account's plan stands, one word each, in the order the Subscriptions tab draws them: every account
# counted once (`_plan_of`).
PLAN_KINDS = ("paying", "trial", "payment_failed", "paused", "free_access", "admin", "choosing", "free")


# Stripe's word for a subscription that runs, as the tab groups it. Ended or never begun is the free plan.
BY_STATUS = {"active": "paying", "trialing": "trial", "past_due": "payment_failed",
             "unpaid": "payment_failed", "paused": "paused"}
# The list's order: what needs looking at first.
LISTED = ("payment_failed", "trial", "paying", "paused", "free_access", "admin", "free", "choosing")


def _plan_of(user: User) -> str:
    """One account's place among `PLAN_KINDS`: an admin or free access first, whatever Stripe says, then
    Stripe's word for their subscription, then the plan page not yet passed, then the free plan."""
    if is_admin(user):
        return "admin"
    if user.free_access_at is not None:
        return "free_access"
    if user.subscription_status in BY_STATUS:
        return BY_STATUS[user.subscription_status]
    return "choosing" if choosing(user, False) else "free"


class SubscriptionRow(BaseModel):
    """An account with a subscription or free access, as its row stores it."""
    id: str
    email: str
    plan: str
    subscription_status: str | None
    trial_ends_at: datetime | None
    period_ends_at: datetime | None
    cancels_at: datetime | None
    last_paid_at: datetime | None
    last_paid_amount: int | None
    currency: str | None
    free_access_at: datetime | None


class Subscriptions(BaseModel):
    accounts: int
    # Members as the site lets them in (`libs.membership.members`): paying, in a trial, free access, admins.
    members: int
    # Every account once, by `PLAN_KINDS`.
    plans: dict[str, int]
    # Trials that end in the next seven days, and so are about to be charged unless cancelled.
    trials_ending: int
    # Running, and set to end at the period's end.
    cancelling: int
    # The accounts made in the last 30 days, and how many of them are members now.
    joined_30d: int
    joined_30d_members: int
    # What paying members bring a month at the membership's price, in its smallest unit and currency: None
    # without Stripe's price.
    monthly: int | None
    # The membership as Stripe has it, its trial and the free plan's delay (`billing_routes.plan`).
    plan: Plan | None
    rows: list[SubscriptionRow]


@router.get("/subscriptions")
def subscriptions(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> Subscriptions:
    """Who is on which plan, what is about to change, and what the membership brings in: Admin's Subscriptions
    tab (owner, 2026-11-12)."""
    users = find(db, User, {})
    kinds = {user.id: _plan_of(user) for user in users}
    soon, month_ago = now() + timedelta(days=7), now() - timedelta(days=30)
    member_ids = set(db[User.COLLECTION].distinct("_id", members()))
    price = billing_plan(db)
    held = [user for user in users if user.subscription_status is not None or user.free_access_at is not None]
    # Payment failed first, then trials by when they end, then paying, paused and the rest.
    held.sort(key=lambda user: (LISTED.index(kinds[user.id]),
                                user.trial_ends_at or user.period_ends_at or now()))
    joined = [user for user in users if user.created_at and user.created_at >= month_ago]
    audit(logger, "admin.subscriptions", user=admin.id)
    return Subscriptions(
        accounts=len(users), members=len(member_ids),
        plans={kind: sum(1 for each in kinds.values() if each == kind) for kind in PLAN_KINDS},
        trials_ending=sum(1 for user in users if user.subscription_status == "trialing" and user.trial_ends_at
                          and user.trial_ends_at <= soon),
        cancelling=sum(1 for user in users if user.subscription_status in ONGOING and user.cancels_at),
        joined_30d=len(joined), joined_30d_members=sum(1 for user in joined if user.id in member_ids),
        monthly=price.amount * sum(1 for kind in kinds.values() if kind == "paying") if price else None,
        plan=price,
        rows=[SubscriptionRow(id=str(user.id), email=user.email, plan=kinds[user.id],
                              subscription_status=user.subscription_status, trial_ends_at=user.trial_ends_at,
                              period_ends_at=user.period_ends_at, cancels_at=user.cancels_at,
                              last_paid_at=user.last_paid_at, last_paid_amount=user.last_paid_amount,
                              currency=user.currency, free_access_at=user.free_access_at)
              for user in held[:500]])


@router.get("/users")
def users(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """The account list, with no credential material of any kind."""
    audit(logger, "admin.users", user=admin.id)
    allowed = admin_emails()
    return {
        "users": [
            {
                "id": str(row.id),
                "email": row.email,
                "status": row.status,
                "active": row.active,
                "telegram": row.telegram_status,
                # Effective access, not just the column.
                "is_admin": is_admin(row),
                # Stripe's word for their membership (None: never had one), and whether an admin let them in.
                "membership": row.subscription_status,
                "free_access": row.free_access_at is not None,
                # Whether the flag can be changed here at all.
                "admin_locked": row.email.lower() in allowed,
                "onboarded": row.onboarding_completed_at is not None,
                "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                # The whole of account lifecycle now: an operator reads this and decides whether to
                # delete. Nothing acts on it automatically (ROADMAP 6c).
                "last_active_at": (row.last_active_at.isoformat()
                                   if row.last_active_at else None),
            }
            for row in find(db, User, {}, sort=[("created_at", 1)])],
    }


class AdminAccount(BaseModel):
    id: str
    email: str
    full_name: str | None
    status: str
    active: bool
    is_admin: bool
    telegram: str
    onboarded: bool
    created_at: datetime | None
    email_verified_at: datetime | None
    onboarding_completed_at: datetime | None
    # When they last opened the site, to the minute (`/dashboard`).
    last_active_at: datetime | None
    # Their membership, as Stripe said when last asked (`billing_routes.sync`), and an admin's free access.
    member: bool
    free_access_at: datetime | None
    stripe_customer_id: str | None
    subscription_id: str | None
    subscription_status: str | None
    # When they picked the free plan on the page after the email link (`POST /billing/free`).
    plan_chosen_at: datetime | None
    trial_ends_at: datetime | None
    period_ends_at: datetime | None
    cancels_at: datetime | None
    last_paid_at: datetime | None
    last_paid_amount: int | None
    currency: str | None
    billing_synced_at: datetime | None


class Tally(BaseModel):
    """A name, and how many jobs on their list it accounts for."""
    name: str
    jobs: int


class AdminUsage(BaseModel):
    """What an account does with the site, for working out what to improve."""
    # What their Jobs page lists at "Any time", and how many of those reached it since they were last here.
    jobs: int
    new_jobs: int
    # How far their applications got (`libs.tenant.applications.outcomes`).
    applications: OutcomeRow
    skipped: int
    # Sent to them by an alert.
    delivered: int
    # CVs and cover letters made for a job, and the looks they are made in.
    cvs_made: int
    covers_made: int
    cv_look: str
    cover_look: str
    # Uploaded.
    cvs: int
    cvs_in_use: int
    cover_letters: int
    # Each green word and each country picked, with the jobs on their list it accounts for: one that finds
    # nothing says 0.
    green_words: list[Tally]
    red_words: list[str]
    countries: list[Tally]
    workplaces: list[str]
    languages: list[str]
    # Where the jobs on their list come from, most first.
    sources: list[Tally]
    # How they hear of new jobs.
    alerts: list[str]
    # The AI accounts linked (`libs.llm.accounts`), and the one their documents are written with.
    ai_accounts: list[str]
    ai_in_use: str | None
    # The last time they did each.
    last_applied_at: datetime | None
    last_made_at: datetime | None
    last_alert_at: datetime | None


class AdminAccountDetail(BaseModel):
    user: AdminAccount
    usage: AdminUsage
    # `None` is unlimited and is a real value, not a missing one.
    limits: dict[str, int | None]


@router.get("/users/{user_id}")
def user_detail(user_id: _uuid.UUID, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> AdminAccountDetail:
    """One account: who it is, when it was last here, and what it does with the site."""
    user = get(db, User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    audit(logger, "admin.user_detail", user=admin.id, subject=user_id)
    return AdminAccountDetail(
        user=AdminAccount(
            id=str(user.id), email=user.email, full_name=user.full_name, status=user.status,
            active=user.active, is_admin=user.is_admin, telegram=user.telegram_status,
            onboarded=user.onboarding_completed_at is not None, created_at=user.created_at,
            email_verified_at=user.email_verified_at, onboarding_completed_at=user.onboarding_completed_at,
            last_active_at=user.last_active_at, member=is_member(db, user.id),
            free_access_at=user.free_access_at, stripe_customer_id=user.stripe_customer_id,
            subscription_id=user.subscription_id, subscription_status=user.subscription_status,
            plan_chosen_at=user.plan_chosen_at, trial_ends_at=user.trial_ends_at,
            period_ends_at=user.period_ends_at, cancels_at=user.cancels_at, last_paid_at=user.last_paid_at,
            last_paid_amount=user.last_paid_amount, currency=user.currency,
            billing_synced_at=user.billing_synced_at),
        usage=_usage(db, user),
        limits=all_for(user))


def _usage(db: Database, user: User) -> AdminUsage:
    # Their list at "Any time", read as far back as the Jobs page reads it (`scan_for`).
    jobs = their_jobs(db, user, scan=scan_for(shelf_life(platform_windows(db))[1]))
    since = user.last_active_at
    config = migrate_config(TenantRepository(db).get_config(user.id) or {})
    sections = config.get("sections") or []

    def each(field: str) -> list[str]:
        return list(dict.fromkeys(value for section in sections for value in section.get(field) or []))

    def count(collection: str, **where) -> int:
        return db[collection].count_documents({"user_id": user.id, **where})

    def last(collection: str, field: str) -> datetime | None:
        row = db[collection].find_one({"user_id": user.id, field: {"$ne": None}}, {field: 1},
                                      sort=[(field, -1)])
        return row[field] if row else None

    def within(slug: str) -> int:
        """The jobs on their list in a place they picked: a country, or the countries it covers."""
        return sum(places[code] for code in COUNTRIES[slug].countries) if slug in COUNTRIES else 0

    documents = list(db[UserDocument.COLLECTION].find({"user_id": user.id}, {"kind": 1, "in_use": 1}))
    cvs = [row for row in documents if row["kind"] == "cv"]
    made = Counter(row["kind"] for row in db[BuiltDocument.COLLECTION].find({"user_id": user.id},
                                                                             {"kind": 1}))
    alerts = [name for name, on in (("email", user.email_alerts),
                                    ("Telegram", user.telegram_status == "connected"),
                                    ("browser", count(PushSubscription.COLLECTION) > 0)) if on]
    words = Counter(word for match in jobs for word in match.words)
    places = Counter(match.job.country for match in jobs)
    nothing = dict.fromkeys(("applied", *applications.STAGES, applications.REJECTED), 0)
    reached = next((row for row in applications.outcomes(db, user.id) if row["group"] == applications.ALL),
                   {"group": applications.ALL, **nothing})
    accounts = ai_listing(db, user.id)
    return AdminUsage(
        jobs=len(jobs),
        new_jobs=sum(1 for match in jobs if since and (match.job.described_at or since) > since),
        applications=OutcomeRow(**reached),
        skipped=count(UserJobState.COLLECTION, dismissed_at={"$ne": None}),
        delivered=count(UserJobState.COLLECTION, notified_at={"$ne": None}),
        cvs_made=made["cv"], covers_made=made["cover"],
        cv_look=templates.chosen(user, "cv").name, cover_look=templates.chosen(user, "cover").name,
        cvs=len(cvs), cvs_in_use=sum(1 for row in cvs if row.get("in_use", True)),
        cover_letters=sum(1 for row in documents if row["kind"] == "cover_letter"),
        green_words=[Tally(name=word, jobs=words[norm(word)]) for word in green_words(config)],
        red_words=each("exclude_keywords"),
        countries=[Tally(name=location_search_name(slug), jobs=within(slug)) for slug in each("locations")],
        workplaces=[WORKPLACE_NAMES.get(way, (way,))[0] for way in each("workplaces")],
        languages=[LANGUAGE_NAMES.get(code, (code,))[0] for code in config.get("languages") or []],
        sources=[Tally(name=board_name(source), jobs=jobs_from)
                 for source, jobs_from in Counter(match.job.source for match in jobs).most_common()],
        alerts=alerts,
        ai_accounts=[each.label for each in accounts],
        ai_in_use=next((each.label for each in accounts if each.in_use), None),
        last_applied_at=last(UserJobState.COLLECTION, "applied_at"),
        last_made_at=last(BuiltDocument.COLLECTION, "created_at"),
        last_alert_at=last(UserJobState.COLLECTION, "notified_at"))


class SettingPatch(BaseModel):
    value: dict | list | int | str


@router.get("/settings")
def read_settings(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """What the operator controls, with the defaults filled in for anything unset."""
    audit(logger, "admin.settings_read", user=admin.id)
    return {"settings": all_settings(db), "defaults": DEFAULTS}


@router.put("/settings/{key}")
def write_setting(key: str, body: SettingPatch, db: Database = Depends(get_db),
                  admin: User = Depends(require_admin)) -> dict:
    """Set one, unless it would leave every free account's Jobs page empty: a free account sees a job from
    `free_delay_hours` until it leaves everybody's at the job shelf life, so the shelf must be the longer. The
    server kept 11-07's week of shelf while free accounts waited a week, and a new one saw "0 jobs"
    (2026-11-13)."""
    if key in ("job_max_age_hours", "free_delay_hours"):
        shelf, delay = (int(body.value) if name == key else int(all_settings(db)[name])
                        for name in ("job_max_age_hours", "free_delay_hours"))
        if delay >= shelf:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=(
                f"Free accounts would see no jobs: they see each job {_span(delay)} after members do, and "
                f"jobs leave after {_span(shelf)}. Keep the job shelf life longer than that wait."))
    try:
        put(db, key, body.value, by=admin.email)
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit(logger, "admin.settings_write", user=admin.id, setting=key)
    return {"status": "ok", "settings": all_settings(db)}


class AdminFlag(BaseModel):
    is_admin: bool


@router.put("/users/{user_id}/admin")
def set_admin(user_id: _uuid.UUID, body: AdminFlag, db: Database = Depends(get_db),
              admin: User = Depends(require_admin)) -> dict:
    """Grant or revoke operator access."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot change your own operator access.")

    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"is_admin": body.is_admin}})
    audit(logger, "admin.access_changed", user=admin.id, subject=user_id, granted=body.is_admin)
    return {"status": "ok", "id": str(user_id), "is_admin": body.is_admin}


class FreeAccess(BaseModel):
    on: bool


@router.put("/users/{user_id}/free-access")
def set_free_access(user_id: _uuid.UUID, body: FreeAccess, db: Database = Depends(get_db),
                    admin: User = Depends(require_admin)) -> dict:
    """Give an account everything a membership gives without paying, or stop: over whatever Stripe says. A
    subscription they have stops charging while it is on and starts again when it is off (`hold_billing`)."""
    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")
    with _stripe_answers("pause or resume the billing of an account given free access"):
        hold_billing(db, target, free=body.on)
    # Asked twice, as a double click does, it keeps the day it was first given.
    db[User.COLLECTION].update_one({"_id": user_id, **({"free_access_at": None} if body.on else {})},
                                   {"$set": {"free_access_at": now() if body.on else None}})
    audit(logger, "admin.free_access", user=admin.id, subject=user_id, on=body.on)
    return {"status": "ok", "id": str(user_id), "free_access": body.on}


class DeleteConfirmation(BaseModel):
    # The typed address, which must match the account being deleted.
    email: str


@router.delete("/users/{user_id}")
def delete_user(user_id: _uuid.UUID, body: DeleteConfirmation, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Delete an account and everything belonging to it, immediately and permanently."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot delete your own account from here.")

    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    if body.email.strip().lower() != target.email.lower():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="That address does not match the account. Nothing was deleted.")

    email = target.email
    # Their membership first: an account deleted while Stripe still bills it is a charge nobody can stop.
    with _stripe_answers("end the membership of an account being deleted"):
        end_membership(target)
    # Credentials and the key that reads them included: both are this account's documents.
    delete_account(db, user_id)
    audit(logger, "admin.user_deleted", user=admin.id, subject=user_id)
    logger.warning("admin: %s deleted account %s (%s)", admin.id, user_id, email)
    return {"status": "deleted", "id": str(user_id)}


@router.get("/sources")
def sources(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every source the harvester collects from, with the freshness window each one is using. No source is
    asked for a word (owner, 2026-11-04): each is read whole for its window."""
    # Every setting below, from one read of them (`settings.snapshot`).
    stored = snapshot(db)
    floors = source_min_ages(stored)
    return {
        "sources": [{"name": name,
                     "hours": freshness_hours(stored, name),
                     # Its twin, shown beside it: `hours` is how far back the source is asked,
                     # this is how long what it returned stays deliverable. They were set in two
                     # different places, and only this one had no control at all — so widening a
                     # source's harvest window did nothing an operator could see if the floor
                     # below was discarding the result.
                     "min_age_hours": floors.get(name, DEFAULT_SOURCE_MIN_AGE_HOURS),
                     # Calls a run, for the one source metered by the call (Adzuna); `None` elsewhere.
                     "queries_per_cycle": queries_per_cycle(stored, name),
                     # `None` where the source has no result cap to set at all.
                     "results_per_query": results_per_query(stored, name),
                     # `None` for a source that fetches one response rather than walking pages.
                     "pages_per_cycle": pages_per_cycle(stored, name)}
                    for name in HARVEST_SOURCES],
        # The ATS ceiling, with the number it is a ceiling on.
        "ats": {
            "companies_per_cycle": ats_companies_per_cycle(stored),
            "companies_held": db[AtsToken.COLLECTION].count_documents({}),
        },
    }


class CountrySeries(BaseModel):
    # The ISO code, or None for postings whose country cannot be told ("Remote", "Worldwide").
    country: str | None
    name: str
    total: int
    # One per bucket of `CountryChart.starts`.
    counts: list[int]


class SourceSeries(BaseModel):
    # The source's own name ("linkedin"), or None for counts kept before sources were.
    source: str | None
    name: str
    total: int
    # One per bucket of `CountryChart.starts`.
    counts: list[int]


class CountryChart(BaseModel):
    # What a point stands for, in minutes: picked, or by the range, a run up to half a day, an hour to two
    # weeks, a day beyond.
    bucket_minutes: int
    # The steps that can be picked over this range, in minutes (`libs.ops.countries.steps`).
    steps: list[int]
    # When each bucket starts: the range asked for, from the first count if that is later.
    starts: list[datetime]
    # When the first count began; `None` before anything has been counted.
    since: datetime | None
    # Most jobs first, from the sources picked.
    countries: list[CountrySeries]
    # Every source, picked or not, most jobs first.
    sources: list[SourceSeries]
    # LinkedIn jobs still waiting for their text and young enough to reach somebody (the describer's queue).
    waiting: int


@router.get("/countries", response_model=CountryChart)
def countries(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
              hours: int | None = None, minutes: int | None = None,
              without: list[str] = Query([], max_length=100)) -> dict:
    """New jobs by the country each is in, over the last `hours` or everything counted, a point `minutes`
    long where that is offered, from every source but those `without` (an empty one is the counts kept
    before sources were) (`libs.ops.countries`)."""
    hours = None if hours is None else max(1, min(hours, max(RANGES_HOURS)))
    audit(logger, "admin.countries", user=admin.id, hours=hours, minutes=minutes, without=without)
    waiting = CorpusRepository(db).count_awaiting_description(linkedin=True, windows=platform_windows(db))
    return {**by_country(db, hours=hours, minutes=minutes, without={each or None for each in without}),
            "waiting": waiting}


class LimitPatch(BaseModel):
    # `None` is unlimited and is a real, storable answer — not a missing field.
    value: int | None = None


@router.get("/limits")
def limit_catalogue(admin: User = Depends(require_admin)) -> dict:
    """What can be set, what it defaults to, and what each one means."""
    return {"limits": describe()}


@router.put("/users/{user_id}/limits/{key}")
def set_user_limit(user_id: _uuid.UUID, key: str, body: LimitPatch,
                   db: Database = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Set one ceiling on one account, or clear it to unlimited."""
    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    try:
        set_limit(db, user_id, key, body.value)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit(logger, "admin.limit_set", user=admin.id, subject=user_id, limit=key, value=body.value)
    return {"status": "ok", "limits": all_for(get(db, User, user_id))}

# What an operator can page through at once.
CORPUS_PAGE = 50


@router.get("/corpus")
def corpus(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
           q: str = "", source: str = "", days: int = 30,
           offset: int = 0) -> dict:
    """Search the shared corpus."""
    window = datetime.now(UTC) - timedelta(days=max(days, 1))
    where: dict = {"first_seen_at": {"$gte": window}}
    if q.strip():
        needle = {"$regex": re.escape(q.strip()), "$options": "i"}
        where["$or"] = [{"title": needle}, {"company": needle}]
    if source.strip():
        where["source"] = source.strip()

    total = db[CorpusJob.COLLECTION].count_documents(where)
    rows = find(db, CorpusJob, where, sort=[("first_seen_at", -1), ("_id", -1)],
                skip=max(offset, 0), limit=CORPUS_PAGE)
    by_source = [(row["_id"], row["count"]) for row in db[CorpusJob.COLLECTION].aggregate([
        {"$match": {"first_seen_at": {"$gte": window}}},
        {"$group": {"_id": "$source", "count": {"$sum": 1}}}, {"$sort": {"count": -1}}])]

    audit(logger, "admin.corpus", user=admin.id, q=q[:60], total=total)
    return {
        "total": total,
        "offset": max(offset, 0),
        "page_size": CORPUS_PAGE,
        "sources": [{"source": name or "unknown", "count": count} for name, count in by_source],
        "jobs": [
            {"id": row.id, "title": row.title, "company": row.company,
             "location": row.location, "source": row.source, "is_remote": row.is_remote,
             "described": bool(row.description and len(row.description) > 400),
             "url": row.url,
             "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
             "posted_at": row.posted_at.isoformat() if row.posted_at else None}
            for row in rows],
    }


@router.delete("/queue/dead")
def discard_dead_letters(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
                         lane: str | None = None) -> dict:
    """Clear dead-lettered jobs an operator has read and dealt with."""
    where = {"state": "dead", **({"lane": lane} if lane else {})}
    rows = find(db, QueueJob, where)
    for row in rows:
        logger.warning("admin: discarding dead %s job %s for user %s — last error: %s",
                       row.lane, row.id, row.user_id, (row.last_error or "")[:200])
    db[QueueJob.COLLECTION].delete_many({"_id": {"$in": [row.id for row in rows]}})

    audit(logger, "admin.dead_discarded", user=admin.id, lane=lane or "all", count=len(rows))
    return {"status": "discarded", "count": len(rows), "lane": lane}


@router.get("/pipeline")
def pipeline_shape(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
                   hours: int = 24) -> dict:
    """Where the jobs are right now, stage by stage."""
    return pipeline(db, hours=max(1, min(hours, 168)))


@router.get("/pipeline/sources")
def pipeline_sources(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
                     hours: int = 24) -> dict:
    """Each source's last `hours`: found, with text, waiting for text, newest. Not in the live frame: it
    reads the day's jobs, and once a minute is enough to see a board go quiet."""
    window = max(1, min(hours, 168))
    audit(logger, "admin.pipeline_sources", user=admin.id, hours=window)
    return {"window_hours": window, "sources": found_by_source(db, hours=window)}


# How often the live view recomputes.
STREAM_INTERVAL_SECONDS = 1.0

# A stream is an authorisation held open.
STREAM_MAX_SECONDS = 300


@router.get("/pipeline/stream")
async def pipeline_stream(request: Request, db: Database = Depends(get_db),
                          admin: User = Depends(require_admin), *, hours: int = 24) -> Response:
    """The same numbers as `/pipeline`, pushed once a second."""
    window = max(1, min(hours, 168))
    audit(logger, "admin.pipeline_stream", user=admin.id, hours=window)

    async def frames():
        started = asyncio.get_event_loop().time()
        while True:
            # The client going away is the normal ending.
            if await request.is_disconnected():
                return
            if asyncio.get_event_loop().time() - started > STREAM_MAX_SECONDS:
                # Ends the response; the browser reopens and is authorised again.
                return
            try:
                payload = json.dumps(pipeline(db, hours=window))
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the stream
                logger.warning("pipeline stream: frame failed, retrying next tick: %s", exc)
                payload = json.dumps({"error": str(exc)})
            # A blank line terminates one server-sent event.
            yield f"data: {payload}\n\n"
            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    return StreamingResponse(
        frames(), media_type="text/event-stream",
        # Cloudflare and any proxy in between will happily buffer an event stream into uselessness.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/latency")
def latency(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
            hours: int = 24) -> dict:
    """The drill-down: the spans, then where the time actually went, then when."""
    window = max(1, min(hours, 24 * 7))
    audit(logger, "admin.latency", user=admin.id, hours=window)
    # One row per zone the posting crosses, in pipeline order; the end-to-end span; the lanes; by hour.
    return latency_report(db, hours=window)


@router.get("/latency/{key}")
def latency_stage(key: str, db: Database = Depends(get_db),
                  admin: User = Depends(require_admin), *, hours: int = 24) -> dict:
    """One zone, expanded: waiting versus running, the backlog, and the hour-by-hour shape."""
    window = max(1, min(hours, 24 * 7))
    try:
        detail = stage_detail(db, key, hours=window)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such stage") from None
    audit(logger, "admin.latency_stage", user=admin.id, stage=key, hours=window)
    return detail

