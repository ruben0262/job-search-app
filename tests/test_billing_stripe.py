"""The membership against Stripe's own test mode: real subscriptions on test clocks, read by the site's
`sync`, through every state an account goes through. What a fake cannot catch: Stripe moving a field between
API versions, as it moved the period's end onto the item in 2025 (leadops read 0 for it).

Skipped without `STRIPE_TEST_SECRET_KEY` (a full `sk_test_` key: test clocks need it) and `STRIPE_PRICE_ID` (a
monthly price in that account). With both, run it whenever `stripe` is upgraded:

    set -a; . certificates/stripe.env; set +a
    MONGODB_URI=mongodb://127.0.0.1:27017 pytest tests/test_billing_stripe.py

It makes customers on test clocks in that account and deletes the clocks, and so them, after. The payment
form and the portal were walked in a browser on 2026-11-11 and 2026-11-12 (CHANGELOG).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from libs.db import get, insert
from libs.db.models import User

stripe = pytest.importorskip("stripe")
KEY, PRICE = os.environ.get("STRIPE_TEST_SECRET_KEY", ""), os.environ.get("STRIPE_PRICE_ID", "")
pytestmark = pytest.mark.skipif(not (KEY.startswith("sk_test_") and PRICE),
                                reason="STRIPE_TEST_SECRET_KEY (sk_test_) and STRIPE_PRICE_ID not set")
DAY = 86_400


@pytest.fixture()
def stripe_test(monkeypatch):
    """The site reading Stripe's test mode, and test clocks that are deleted after, with their customers."""
    from services.api import billing_routes

    client = stripe.StripeClient(KEY)
    monkeypatch.setattr(billing_routes, "_stripe", lambda: client)
    clocks: list[str] = []
    yield client, clocks
    for clock in clocks:
        client.v1.test_helpers.test_clocks.delete(clock)


def member(db, client, clocks, card: str) -> tuple[User, str]:
    """An account whose card is saved and whose 7-day trial has begun, as the payment form leaves it."""
    clock = client.v1.test_helpers.test_clocks.create(params={"frozen_time": int(time.time())})
    clocks.append(clock.id)
    user = insert(db, User(email=f"{uuid.uuid4().hex[:10]}@example.com", password_hash="x", status="active"))
    customer = client.v1.customers.create(params={"email": user.email, "test_clock": clock.id,
                                                  "metadata": {"user_id": str(user.id)}})
    method = client.v1.payment_methods.attach(card, params={"customer": customer.id})
    client.v1.subscriptions.create(params={"customer": customer.id, "items": [{"price": PRICE}],
                                           "trial_period_days": 7, "default_payment_method": method.id})
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"stripe_customer_id": customer.id}})
    return get(db, User, user.id), clock.id


def advance(client, clock: str, days: int) -> None:
    now = client.v1.test_helpers.test_clocks.retrieve(clock).frozen_time
    client.v1.test_helpers.test_clocks.advance(clock, params={"frozen_time": now + days * DAY})
    for _ in range(120):
        time.sleep(1)
        if client.v1.test_helpers.test_clocks.retrieve(clock).status == "ready":
            return
    raise AssertionError(f"the test clock {clock} did not finish")


def read(db, user: User) -> User:
    from services.api.billing_routes import sync
    return sync(db, get(db, User, user.id))


def test_a_trial_turns_into_a_payment_by_itself(db, stripe_test):
    from libs.membership import is_member

    client, clocks = stripe_test
    user, clock = member(db, client, clocks, "pm_card_visa")
    trial = read(db, user)
    assert trial.subscription_status == "trialing" and is_member(db, user.id)
    assert trial.period_ends_at == trial.trial_ends_at, "the trial is the first period"

    advance(client, clock, 8)
    paid = read(db, user)
    assert paid.subscription_status == "active" and is_member(db, user.id)
    assert paid.last_paid_amount > 0 and paid.last_paid_at is not None
    assert 27 <= (paid.period_ends_at - paid.last_paid_at).days <= 31, "the next payment, a month on"


def test_cancelled_in_the_trial_it_ends_with_the_trial_and_charges_nothing(db, stripe_test):
    from libs.membership import is_member

    client, clocks = stripe_test
    user, clock = member(db, client, clocks, "pm_card_visa")
    subscription = read(db, user).subscription_id
    # What the portal does with the site's own configuration: at the end of the period, which is the trial.
    client.v1.subscriptions.update(subscription, params={"cancel_at_period_end": True})
    cancelled = read(db, user)
    assert cancelled.subscription_status == "trialing" and cancelled.cancels_at == cancelled.trial_ends_at
    assert is_member(db, user.id)

    advance(client, clock, 8)
    ended = read(db, user)
    assert ended.subscription_status == "canceled" and not is_member(db, user.id)
    assert ended.last_paid_at is None, "nothing was charged"


def test_a_failed_payment_closes_the_site_and_paying_it_opens_it(db, stripe_test):
    from libs.membership import is_member

    client, clocks = stripe_test
    user, clock = member(db, client, clocks, "pm_card_chargeCustomerFail")
    advance(client, clock, 8)
    failed = read(db, user)
    assert failed.subscription_status == "past_due" and not is_member(db, user.id)

    # What the portal's "Update payment method" does: another card, and the invoice owed paid with it.
    card = client.v1.payment_methods.attach("pm_card_visa", params={"customer": failed.stripe_customer_id})
    owed = client.v1.subscriptions.retrieve(failed.subscription_id).latest_invoice
    client.v1.invoices.pay(owed, params={"payment_method": card.id})
    paid = read(db, user)
    assert paid.subscription_status == "active" and is_member(db, user.id)
    assert paid.last_paid_amount > 0


def free(db, user: User, on: bool) -> User:
    """The admin's switch (`admin_routes.set_free_access`): Stripe first, then the account."""
    from libs.db import now
    from services.api.billing_routes import hold_billing

    hold_billing(db, get(db, User, user.id), free=on)
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"free_access_at": now() if on else None}})
    return read(db, user)


def invoices(client, subscription: str) -> list:
    return client.v1.invoices.list(params={"subscription": subscription, "limit": 20}).data


def test_free_access_in_the_trial_charges_nothing_and_off_again_the_next_payment_is_taken(db, stripe_test):
    """Owner, 2026-11-12: "what happens if we give free membership during the trial ... turn it on, make it
    free, and then turn it off and its activated again, and this can be in cycles". On and off and on in the
    trial: the trial ends with nothing charged and the one membership still running; off, the next month's
    payment is taken by itself, and the membership is theirs by paying again."""
    from libs.membership import is_member

    client, clocks = stripe_test
    user, clock = member(db, client, clocks, "pm_card_visa")
    for on in (True, False, True):
        held = free(db, user, on)
    subscription = client.v1.subscriptions.retrieve(held.subscription_id)
    assert subscription.pause_collection.behavior == "void" and is_member(db, user.id)

    advance(client, clock, 8)
    after_trial = read(db, user)
    assert after_trial.last_paid_at is None, "nothing was charged when the trial ended"
    assert {each.status for each in invoices(client, held.subscription_id) if each.amount_due} == {"void"}
    assert after_trial.subscription_status in ("active", "trialing") and is_member(db, user.id)

    resumed = free(db, user, False)
    assert client.v1.subscriptions.retrieve(held.subscription_id).pause_collection is None
    assert resumed.subscription_status == "active" and is_member(db, user.id), "theirs again by paying"

    advance(client, clock, 31)
    paying = read(db, user)
    assert paying.last_paid_amount > 0 and paying.last_paid_at is not None, "the next payment was taken"
    assert paying.subscription_status == "active"
    running = client.v1.subscriptions.list(params={"customer": user.stripe_customer_id, "status": "all"}).data
    assert len(running) == 1, "the one membership throughout, never a second"


def test_free_access_for_a_failed_payment_voids_what_is_owed(db, stripe_test):
    """A payment that failed is not taken later behind the team's back: what is owed is voided when free
    access starts, and Stripe tries nothing more while it lasts."""
    from libs.membership import is_member

    client, clocks = stripe_test
    user, clock = member(db, client, clocks, "pm_card_chargeCustomerFail")
    advance(client, clock, 8)
    owing = read(db, user)
    assert owing.subscription_status == "past_due" and not is_member(db, user.id)

    held = free(db, user, True)
    assert is_member(db, user.id)
    assert not [each for each in invoices(client, held.subscription_id) if each.status == "open"], \
        "nothing left owed for Stripe to retry"
    print("past due, then free access: Stripe says", held.subscription_status)
