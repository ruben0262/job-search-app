"""Membership, paid through Stripe (owner, 2026-11-11): the payment form, Stripe's portal, and Stripe's word
on what changed.

Stripe is the record of every membership. `sync` copies a customer's subscription from Stripe onto their
account whole, never one event's slice of it, so it does not matter which events arrive, in what order or how
often. leadops fixed a bug from each of those: a status taken from an invoice event, an older event written
over a newer one, Stripe's times replaced with the server's. It runs when Stripe says something changed
(`/billing/webhook`) and when the person comes back from Stripe (`/billing/refresh`), so the site opens the
moment they have paid, not whenever the webhook arrives.

The payment form is Stripe's own, inside this site's page (CLAUDE.md §0: never leave the site to finish a
step). Changing, cancelling or paying what is owed happens on Stripe's portal, as the owner asked: Stripe
does not let the portal sit inside another page.
"""
from __future__ import annotations

import functools
import logging
import os
import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from pymongo.database import Database
from starlette.concurrency import run_in_threadpool

from libs import settings
from libs.db import find, find_one, get, now
from libs.db.models import User
from libs.membership import ONGOING, PAID, is_member
from libs.notify import site_url
from libs.obs import audit
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

# How long one call to Stripe may take before it counts as failed (the SDK's own default is 80 s), and how
# often it is tried again. The SDK sends each retried POST with the same idempotency key, so a retry never
# makes a second customer or charge.
STRIPE_TIMEOUT_SEC = 10
STRIPE_RETRIES = 2

# What Stripe tells this site about, every one read the same way: the customer's subscription, read again.
# The webhook endpoint at Stripe sends these (deploy/SERVER.md).
EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
    "customer.subscription.paused", "customer.subscription.resumed",
    "invoice.paid", "invoice.payment_failed",
})

# On the addresses Stripe sends a person back to: the app reads Stripe again before it draws (`/refresh`).
BACK = "stripe=back"

# What an account holds of Stripe's (`sync`): forgotten together when Stripe has no such customer.
STRIPE_COPY = ("stripe_customer_id", "subscription_id", "subscription_status", "trial_ends_at",
               "period_ends_at", "cancels_at", "last_paid_at", "last_paid_amount", "currency")

# What a membership needs from the environment (deploy/server.env.example).
STRIPE_ENV = ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET")


def setup_problems() -> list[str]:
    """What is wrong with the Stripe settings, said once as the API starts: a missing one, or keys from both
    test and live mode, which makes the payment form fail with nothing on this side to say why."""
    problems = [f"{name} is not set" for name in STRIPE_ENV if not os.environ.get(name, "").strip()]
    secret, public = (os.environ.get(name, "") for name in STRIPE_ENV[:2])
    if secret and public and ("_test_" in secret) != ("_test_" in public):
        problems.append("STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY are from different modes (test, live)")
    return problems


def _env(name: str) -> str:
    """A Stripe setting from the environment. Missing, the request fails where it can be seen, 503 and an
    error in the log, rather than a membership that silently cannot start."""
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("billing: %s is not set, so nobody can pay for a membership", name)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Payments aren't set up yet. "
                                                                        "Try again later.")
    return value


@functools.cache
def _stripe() -> stripe.StripeClient:
    """One client a process, so its connections are kept; each thread gets its own session."""
    return stripe.StripeClient(_env("STRIPE_SECRET_KEY"), max_network_retries=STRIPE_RETRIES,
                               http_client=stripe.RequestsClient(timeout=STRIPE_TIMEOUT_SEC))


@contextmanager
def _stripe_answers(doing: str) -> Iterator[None]:
    """Stripe, called for a person waiting on the answer: if it fails they are told to try again, and the
    log says what failed."""
    try:
        yield
    except stripe.StripeError as exc:
        logger.warning("billing: Stripe failed to %s: %s", doing, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Stripe didn't answer. Try again in a minute.") from exc


def _time(stamp: int | None) -> datetime | None:
    return datetime.fromtimestamp(stamp, UTC) if stamp else None


def _rank(subscription: stripe.Subscription) -> tuple[int, int]:
    """Which of a customer's subscriptions is theirs: one that pays, else one still owed for, else the
    newest."""
    order = 0 if subscription.status in PAID else 1 if subscription.status in ONGOING else 2
    return order, -subscription.created


def sync(db: Database, user: User) -> User:
    """Their membership as Stripe has it now, written onto their account; the account as written. Raises
    `stripe.StripeError` when Stripe cannot be read, and then writes nothing."""
    if not user.stripe_customer_id:
        return user
    started = now()
    try:
        found = _stripe().v1.subscriptions.list(params={
            "customer": user.stripe_customer_id, "status": "all", "limit": 20,
            "expand": ["data.latest_invoice"]}).data
    except stripe.InvalidRequestError as exc:
        if (exc.code, exc.param) != ("resource_missing", "customer"):
            raise
        # A customer these keys never made: made in test mode before the live keys, or deleted at Stripe.
        # Forgotten with all that was read of it, so a membership from test mode opens nothing, and the
        # next payment form makes a real customer.
        logger.warning("billing: Stripe has no customer %s with these keys; %s's copy of it is forgotten",
                       user.stripe_customer_id, user.id)
        db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
            **dict.fromkeys(STRIPE_COPY), "billing_synced_at": now()}})
        return get(db, User, user.id)
    running = [each.id for each in found if each.status in ONGOING]
    if len(running) > 1:
        # Two payment forms finished at the same moment (`checkout` ends the older ones it can see), and
        # both bill.
        logger.error("billing: %s has %d running subscriptions, %s: refund and cancel all but one at Stripe",
                     user.id, len(running), ", ".join(running))
    fields: dict = {"billing_synced_at": started}
    if current := min(found, key=_rank, default=None):
        items = current.items.data
        fields.update(
            subscription_id=current.id, subscription_status=current.status, currency=current.currency,
            trial_ends_at=_time(current.trial_end),
            # On the item since Stripe's 2025-03-31 API (leadops read it off the subscription and got 0).
            period_ends_at=_time(items[0].current_period_end if items else None),
            # Running: when it is set to stop. Ended: when it stopped.
            cancels_at=_time(current.cancel_at if current.status in ONGOING else current.ended_at))
        invoice = current.latest_invoice
        if (isinstance(invoice, stripe.Invoice) and invoice.status == "paid" and invoice.amount_paid > 0
                and (paid_at := _time(invoice.status_transitions.paid_at))
                and (user.last_paid_at is None or paid_at > user.last_paid_at)):
            fields.update(last_paid_at=paid_at, last_paid_amount=invoice.amount_paid)
    # Written only over an older read. Stripe sends several events at once, the last failed payment and the
    # cancel within a second (2026-11-11), and of two syncs the one that read Stripe first may finish last.
    was = db[User.COLLECTION].find_one_and_update(
        {"_id": user.id, "$or": [{"billing_synced_at": None}, {"billing_synced_at": {"$lte": started}}]},
        {"$set": fields}, projection={"subscription_status": 1})
    if was is None:
        logger.info("billing: %s was read from Stripe again meanwhile; this older read is not written",
                    user.id)
        return get(db, User, user.id)
    # What the write replaced, not what was read before it: each of several events at once read the same old
    # state, so each said the change (three lines for one trial, 2026-11-11).
    if "subscription_status" in fields and fields["subscription_status"] != was.get("subscription_status"):
        audit(logger, "billing.status_changed", user=user.id, was=was.get("subscription_status"),
              now=fields["subscription_status"])
    return get(db, User, user.id)


def end_membership(user: User) -> None:
    """Their customer at Stripe deleted, which cancels every subscription of theirs at once, so nothing more
    is charged: what deleting their account owes them. Stripe keeps the invoices it must. Raises
    `stripe.StripeError` if Stripe cannot be reached, so the account is never deleted while it still bills."""
    if not user.stripe_customer_id:
        return
    try:
        _stripe().v1.customers.delete(user.stripe_customer_id)
    except stripe.InvalidRequestError as exc:
        if exc.code != "resource_missing":
            raise
        logger.warning("billing: %s's Stripe customer %s was already gone", user.id, user.stripe_customer_id)
    audit(logger, "billing.customer_deleted", user=user.id, customer=user.stripe_customer_id)


def hold_billing(db: Database, user: User, *, free: bool) -> None:
    """Free access stops what Stripe charges, and ending it starts it again (owner, 2026-11-12: "turn it
    on, make it free, and then turn it off and it's activated again, and this can be in cycles"). On: their
    running subscription's payment collection paused with each invoice voided, Stripe's own way to give a
    service free for a while, and an invoice already owed voided too, which Stripe would otherwise keep
    retrying. Off: collection resumed, from the next payment. Either is safe to repeat. Nothing for somebody
    without a running subscription. Raises `stripe.StripeError`, so the switch does not move when Stripe did
    not."""
    user = sync(db, user)
    if user.subscription_status not in ONGOING:
        return
    client = _stripe()
    if free:
        client.v1.subscriptions.update(user.subscription_id,
                                       params={"pause_collection": {"behavior": "void"}})
        owed = client.v1.invoices.list(params={"subscription": user.subscription_id, "status": "open"}).data
        for invoice in owed:
            client.v1.invoices.void_invoice(invoice.id)
    else:
        client.v1.subscriptions.update(user.subscription_id, params={"pause_collection": ""})
    sync(db, user)
    audit(logger, "billing.collection_paused" if free else "billing.collection_resumed", user=user.id,
          subscription=user.subscription_id)


def _customer(db: Database, user: User) -> User:
    """Their customer at Stripe, made the first time: one for the life of the account, so every payment and
    the portal share one history. The idempotency key makes two clicks at once one customer."""
    if user.stripe_customer_id:
        return user
    customer = _stripe().v1.customers.create(
        params={"email": user.email, "metadata": {"user_id": str(user.id)}},
        options={"idempotency_key": f"customer-{user.id}"})
    db[User.COLLECTION].update_one({"_id": user.id, "stripe_customer_id": None},
                                   {"$set": {"stripe_customer_id": customer.id}})
    logger.info("billing: %s is Stripe customer %s", user.id, customer.id)
    return get(db, User, user.id)


def trial_for(db: Database, user: User) -> int:
    """The free days their payment form starts with: the trial, if they never had a membership."""
    return int(settings.get(db, "trial_days")) if user.subscription_id is None else 0


class Plan(BaseModel):
    """The one membership: its price as Stripe has it, and the free trial a new member gets; and, for the
    sign-up page's choice, how far behind a member the free plan sees each job (`libs.membership`)."""
    # In the smallest unit of `currency`: cents.
    amount: int
    currency: str
    # Every `interval_count` `interval`s: "month" and 1 is monthly.
    interval: str
    interval_count: int
    trial_days: int
    free_delay_hours: int


@functools.cache
def _price(price_id: str) -> stripe.Price:
    """Read once a process: a price at Stripe never changes. A new one is a new `STRIPE_PRICE_ID` and a
    restart."""
    return _stripe().v1.prices.retrieve(price_id)


def plan(db: Database) -> Plan | None:
    """What a membership costs, or None when Stripe is not set up or cannot be read: the page then says
    nothing of a price rather than a wrong one."""
    price_id, key = os.environ.get("STRIPE_PRICE_ID", "").strip(), os.environ.get("STRIPE_SECRET_KEY")
    if not (price_id and key):
        return None
    try:
        price = _price(price_id)
    except stripe.StripeError:
        logger.warning("billing: Stripe did not answer for the price %s; the page goes without it", price_id,
                       exc_info=True)
        return None
    return Plan(amount=price.unit_amount, currency=price.currency, interval=price.recurring.interval,
                interval_count=price.recurring.interval_count, trial_days=int(settings.get(db, "trial_days")),
                free_delay_hours=int(settings.get(db, "free_delay_hours")))


class Membership(BaseModel):
    """Where a person's membership stands, for the page that asks them to pay and for Settings."""
    # May use the site (`libs.membership`): paying, in the trial, let in by an admin, or an admin.
    member: bool
    free_access: bool
    # Stripe's word for their subscription; None if they never had one.
    status: str | None
    # No subscription is running, so the payment form starts one. False: Stripe's portal, to pay what is owed.
    can_start: bool
    # The free days the form starts with (`trial_for`): 0 for somebody who had a membership before.
    trial_days: int
    trial_ends_at: datetime | None
    period_ends_at: datetime | None
    cancels_at: datetime | None
    last_paid_at: datetime | None
    last_paid_amount: int | None
    currency: str | None
    # They have a customer at Stripe, so the portal opens for them.
    can_manage: bool
    plan: Plan | None
    # Stripe's public key, for the payment form; None until it is set.
    publishable_key: str | None


def membership_of(db: Database, user: User) -> Membership:
    return Membership(
        member=is_member(db, user.id), free_access=user.free_access_at is not None,
        status=user.subscription_status, can_start=user.subscription_status not in ONGOING,
        trial_days=trial_for(db, user), trial_ends_at=user.trial_ends_at, period_ends_at=user.period_ends_at,
        cancels_at=user.cancels_at, last_paid_at=user.last_paid_at, last_paid_amount=user.last_paid_amount,
        currency=user.currency, can_manage=user.stripe_customer_id is not None, plan=plan(db),
        publishable_key=os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip() or None)


@router.get("")
def membership(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> Membership:
    """Their membership as last read from Stripe: what Settings shows."""
    return membership_of(db, get(db, User, user_id))


@router.get("/plan")
def public_plan(db: Database = Depends(get_db)) -> Plan | None:
    """Public: the sign-up page says what a membership costs before anybody has an account."""
    return plan(db)


class Checkout(BaseModel):
    # What Stripe's form in the page opens with.
    client_secret: str


@router.post("/checkout")
def checkout(request: Request, db: Database = Depends(get_db),
             user_id: _uuid.UUID = Depends(current_user)) -> Checkout:
    """Open the payment form: a new membership, with the free trial if they never had one. Opening it ends
    any form opened before, so two tabs cannot become two memberships."""
    rate_limit(request, f"checkout:{user_id}", limit=30, per_seconds=3600)
    with _stripe_answers("open the payment form"):
        # Stripe read first: what it says decides the trial, and a customer it no longer has is replaced.
        user = _customer(db, sync(db, get(db, User, user_id)))
        if user.subscription_status in ONGOING:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="You already have a membership. Manage it in Settings.")
        client = _stripe()
        for older in client.v1.checkout.sessions.list(params={
                "customer": user.stripe_customer_id, "status": "open", "limit": 10}).data:
            client.v1.checkout.sessions.expire(older.id)
        days = trial_for(db, user)
        session = client.v1.checkout.sessions.create(params={
            # Stripe's own checkout, inside the site's page: official, its price large, its look Stripe's
            # (Dashboard → Settings → Branding), and it never leaves the site (owner, 2026-11-12, after the
            # site-drawn form "doesn't look official").
            "mode": "subscription", "ui_mode": "embedded_page",
            "customer": user.stripe_customer_id, "client_reference_id": str(user.id),
            "line_items": [{"price": _env("STRIPE_PRICE_ID"), "quantity": 1}],
            # A card even for a free trial, so the first payment is taken by itself when the trial ends.
            "payment_method_collection": "always",
            "subscription_data": {"metadata": {"user_id": str(user.id)},
                                  **({"trial_period_days": days} if days else {})},
            # A card finishes in the page (`onComplete`); a payment that goes to a bank's page comes back
            # here. The mark is in the query, not after the `#`, which is the app's own address.
            "redirect_on_completion": "if_required",
            "return_url": site_url(f"/?{BACK}#/"),
        })
    audit(logger, "billing.checkout", user=user.id, trial_days=days)
    return Checkout(client_secret=session.client_secret)


@router.post("/free")
def choose_free(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The free plan, picked on the page between the email link and setting up: on to setting up, no card
    asked (`libs.membership.choosing`). Picked once; picking it again changes nothing."""
    picked = db[User.COLLECTION].update_one({"_id": user_id, "plan_chosen_at": None},
                                            {"$set": {"plan_chosen_at": now()}}).modified_count
    if picked:
        audit(logger, "billing.free_plan", user=user_id)
    return {"status": "ok"}


class Link(BaseModel):
    url: str


@router.post("/portal")
def portal(request: Request, db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> Link:
    """Stripe's page for their membership: pay what is owed, change the card, read invoices, cancel. It
    comes back to Settings, which reads Stripe again."""
    rate_limit(request, f"portal:{user_id}", limit=30, per_seconds=3600)
    with _stripe_answers("open the membership portal"):
        # Read first, so a customer Stripe no longer has is a plain answer rather than Stripe's error.
        user = sync(db, get(db, User, user_id))
        if not user.stripe_customer_id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="There's no membership to manage yet.")
        # The account's own portal, as saved at Stripe (Settings → Billing → Customer portal), opened already
        # signed in: what its login link opens, without the code by email.
        session = _stripe().v1.billing_portal.sessions.create(params={
            "customer": user.stripe_customer_id, "return_url": site_url(f"/?{BACK}#/settings")})
    audit(logger, "billing.portal", user=user.id)
    return Link(url=session.url)


@router.post("/refresh")
def refresh(request: Request, db: Database = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> Membership:
    """Stripe read again for somebody back from its form or its portal, so what they did there shows at
    once."""
    rate_limit(request, f"billing-refresh:{user_id}", limit=60, per_seconds=3600)
    with _stripe_answers("read the membership"):
        user = sync(db, get(db, User, user_id))
    logger.info("billing: refreshed %s, %s", user.id, user.subscription_status)
    return membership_of(db, user)


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: Database = Depends(get_db)) -> dict:
    """Stripe saying a membership changed. The body is read as sent: its signature covers those bytes."""
    return await run_in_threadpool(receive, db, await request.body(), request.headers.get("stripe-signature"))


def receive(db: Database, payload: bytes, signature: str | None) -> dict:
    """One event from Stripe, checked against the endpoint's secret so nobody else can send one. 503 when
    Stripe cannot be read back, so Stripe sends it again later."""
    try:
        event = _stripe().construct_event(payload, signature, _env("STRIPE_WEBHOOK_SECRET"))
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning("billing: refused a webhook whose signature does not check out")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bad signature") from None
    if event.type not in EVENTS:
        logger.info("billing: %s %s needs nothing here", event.type, event.id)
        return {"status": "ignored"}
    customer = getattr(event.data.object, "customer", None)
    user = find_one(db, User, {"stripe_customer_id": customer}) if isinstance(customer, str) else None
    if user is None:
        # Normal: a customer deleted with its account (`end_membership`) still has events on their way, and
        # a Stripe account may sell other things too.
        logger.info("billing: %s %s is for %s, a customer no account here has", event.type, event.id,
                    customer)
        return {"status": "unknown customer"}
    try:
        user = sync(db, user)
    except stripe.StripeError as exc:
        logger.warning("billing: %s %s for %s: Stripe could not be read back (%s); it will send it again",
                       event.type, event.id, user.id, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again") from exc
    logger.info("billing: %s %s, %s is %s", event.type, event.id, user.id, user.subscription_status)
    return {"status": "ok"}


def reread_all(db: Database) -> dict[str, int]:
    """Every account with a Stripe customer read from Stripe again, by status: after the keys change between
    test and live, so nobody stays a member on a payment Stripe's other mode took (deploy/SERVER.md, step 10).
    An account Stripe cannot be read for now is counted, left as it was, and named in the log."""
    counted: dict[str, int] = {}
    for user in find(db, User, {"stripe_customer_id": {"$type": "string"}}):
        try:
            status_now = sync(db, user).subscription_status or "none"
        except stripe.StripeError:
            logger.warning("billing: could not read %s from Stripe; left as it was", user.id, exc_info=True)
            status_now = "unread"
        counted[status_now] = counted.get(status_now, 0) + 1
    logger.info("billing: read every customer again: %s", counted)
    return counted


if __name__ == "__main__":
    # deploy/compose.sh run --rm --no-deps --entrypoint python api -m services.api.billing_routes
    from libs.db import database
    from libs.obs import configure

    configure("api")
    print(reread_all(database()))
