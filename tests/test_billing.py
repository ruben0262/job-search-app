"""The membership (owner, 2026-11-11 and 2026-11-12): what it adds over the free plan, and what Stripe's
answers do to that.

Stripe is faked at the network, under the real SDK, so what is asserted is what the site sends Stripe and
what it makes of Stripe's answers, in the shapes Stripe's API has now: a subscription's period end on its
item (2025-03-31), the `elements` form drawn by the site itself. Webhooks are signed as Stripe signs them,
with the endpoint's secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest

from libs.db import get, insert, now
from libs.db.models import CorpusJob, QueueJob, User, UserConfig, UserNotification

stripe = pytest.importorskip("stripe")
pytest.importorskip("fastapi")

ADMIN = "operator@example.com"
SECRET = "whsec_test_endpoint"
DAY = 86_400


class FakeStripe(stripe.HTTPClient):
    """Stripe's API as this site uses it, in memory: everything it was sent, and answers in Stripe's
    shapes."""

    name = "fake-stripe"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, str, dict]] = []
        self.customers: dict[str, dict] = {}
        self.subscriptions: list[dict] = []
        self.invoices: list[dict] = []
        self.sessions: dict[str, dict] = {}
        # Customers these keys never made: test mode's, asked with live keys.
        self.gone: set[str] = set()
        self.down = False

    def subscribe(self, customer: str, status: str, *, period_end: int | None = None,
                  trial_end: int | None = None, cancel_at: int | None = None, ended_at: int | None = None,
                  invoice: dict | None = None, created: int | None = None) -> dict:
        """A subscription as Stripe holds it once a payment form has finished."""
        subscription = {
            "id": f"sub_{len(self.subscriptions) + 1}", "object": "subscription", "customer": customer,
            "status": status, "created": created or int(time.time()) + len(self.subscriptions),
            "currency": "eur",
            "trial_end": trial_end, "cancel_at": cancel_at, "cancel_at_period_end": cancel_at is not None,
            "ended_at": ended_at, "latest_invoice": invoice, "pause_collection": None,
            "items": {"object": "list", "url": "/v1/subscription_items", "has_more": False, "data": [
                {"id": "si_1", "object": "subscription_item", "current_period_end": period_end}]},
        }
        self.subscriptions.append(subscription)
        return subscription

    def bill(self, subscription: dict, status: str = "open") -> dict:
        """An invoice of theirs: "open" is one Stripe is still trying to take."""
        invoice = {"id": f"in_{len(self.invoices) + 1}", "object": "invoice", "status": status,
                   "customer": subscription["customer"], "subscription": subscription["id"],
                   "amount_due": 999}
        self.invoices.append(invoice)
        return invoice

    def request(self, method, url, headers, post_data=None, *, _usage=None):  # noqa: PLR0911, PLR0912 - one per endpoint
        parts = urlsplit(url)
        params = dict(parse_qsl(post_data or parts.query, keep_blank_values=True))
        method, path = method.upper(), parts.path
        self.sent.append((method, path, params))
        if self.down:
            return _answer(500, {"error": {"type": "api_error", "message": "Stripe is having a moment"}})
        if params.get("customer") in self.gone:
            return _answer(400, {"error": {"type": "invalid_request_error", "code": "resource_missing",
                                           "param": "customer",
                                           "message": f"No such customer: '{params['customer']}'"}})
        if (method, path) == ("POST", "/v1/customers"):
            made = {"id": f"cus_{len(self.customers) + 1}", "object": "customer",
                    "email": params.get("email"), "metadata": {"user_id": params.get("metadata[user_id]")}}
            self.customers[made["id"]] = made
            return _answer(200, made)
        if method == "DELETE" and (found := re.fullmatch(r"/v1/customers/(\w+)", path)):
            if found[1] not in self.customers:
                return _answer(404, {"error": {"type": "invalid_request_error", "code": "resource_missing",
                                               "message": f"No such customer: '{found[1]}'"}})
            del self.customers[found[1]]
            for each in self.subscriptions:
                if each["customer"] == found[1] and each["status"] not in ("canceled", "incomplete_expired"):
                    each.update(status="canceled", ended_at=int(time.time()))
            return _answer(200, {"id": found[1], "object": "customer", "deleted": True})
        if (method, path) == ("GET", "/v1/subscriptions"):
            return _list("/v1/subscriptions", [each for each in self.subscriptions
                                               if each["customer"] == params.get("customer")])
        if method == "POST" and (found := re.fullmatch(r"/v1/subscriptions/(\w+)", path)):
            [subscription] = [each for each in self.subscriptions if each["id"] == found[1]]
            # Stripe's own encoding: a nested value as `[behavior]`, and "" to unset it.
            if "pause_collection[behavior]" in params:
                subscription["pause_collection"] = {"behavior": params["pause_collection[behavior]"]}
            elif params.get("pause_collection") == "":
                subscription["pause_collection"] = None
            return _answer(200, subscription)
        if (method, path) == ("GET", "/v1/invoices"):
            return _list("/v1/invoices", [each for each in self.invoices
                                          if each["subscription"] == params.get("subscription")
                                          and each["status"] == params.get("status")])
        if method == "POST" and (found := re.fullmatch(r"/v1/invoices/(\w+)/void", path)):
            [invoice] = [each for each in self.invoices if each["id"] == found[1]]
            invoice["status"] = "void"
            return _answer(200, invoice)
        if (method, path) == ("GET", "/v1/checkout/sessions"):
            return _list("/v1/checkout/sessions", [
                each for each in self.sessions.values()
                if each["customer"] == params.get("customer") and each["status"] == params.get("status")])
        if method == "POST" and (found := re.fullmatch(r"/v1/checkout/sessions/(\w+)/expire", path)):
            self.sessions[found[1]]["status"] = "expired"
            return _answer(200, self.sessions[found[1]])
        if (method, path) == ("POST", "/v1/checkout/sessions"):
            made = {"id": f"cs_{len(self.sessions) + 1}", "object": "checkout.session", "status": "open",
                    "customer": params.get("customer"), "ui_mode": params.get("ui_mode")}
            made["client_secret"] = f"{made['id']}_secret"
            self.sessions[made["id"]] = made
            return _answer(200, made)
        if (method, path) == ("POST", "/v1/billing_portal/sessions"):
            return _answer(200, {"id": "bps_1", "object": "billing_portal.session",
                                 "customer": params.get("customer"), "return_url": params.get("return_url"),
                                 "url": "https://billing.stripe.com/p/session/test_1"})
        if method == "GET" and re.fullmatch(r"/v1/prices/\w+", path):
            return _answer(200, {"id": path.rsplit("/", 1)[1], "object": "price", "unit_amount": 999,
                                 "currency": "eur", "recurring": {"interval": "month", "interval_count": 1}})
        raise AssertionError(f"the site asked Stripe for something this fake does not know: {method} {path}")

    def close(self) -> None:
        pass

    def calls(self, method: str, path: str) -> list[dict]:
        return [params for sent_method, sent_path, params in self.sent
                if (sent_method, sent_path) == (method, path)]


def _answer(code: int, body: dict):
    return json.dumps(body), code, {"request-id": "req_test"}


def _list(url: str, rows: list[dict]):
    return _answer(200, {"object": "list", "url": url, "has_more": False, "data": rows})


def paid(amount: int, at: int) -> dict:
    """A subscription's latest invoice, paid."""
    return {"id": f"in_{at}", "object": "invoice", "status": "paid", "amount_paid": amount, "currency": "eur",
            "status_transitions": {"paid_at": at, "finalized_at": at}}


@pytest.fixture()
def fake(monkeypatch):
    from services.api import billing_routes

    fake = FakeStripe()
    client = stripe.StripeClient("sk_test_fake", http_client=fake, max_network_retries=0)
    monkeypatch.setattr(billing_routes, "_stripe", lambda: client)
    billing_routes._price.cache_clear()
    for name, value in {"STRIPE_SECRET_KEY": "sk_test_fake", "STRIPE_PUBLISHABLE_KEY": "pk_test_fake",
                        "STRIPE_PRICE_ID": "price_test", "STRIPE_WEBHOOK_SECRET": SECRET}.items():
        monkeypatch.setenv(name, value)
    yield fake
    billing_routes._price.cache_clear()


@pytest.fixture()
def http(db, fake, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example")

    from fastapi.testclient import TestClient

    from services.api.app import app
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def person(db, **fields) -> User:
    """Somebody who signed up and confirmed their email: no membership unless one is given."""
    return insert(db, User(email=fields.pop("email", f"{uuid.uuid4().hex[:12]}@example.com"),
                           password_hash=fields.pop("password_hash", "x"),
                           status=fields.pop("status", "active"), active=True, **fields))


def sign_in(http, user: User) -> None:
    from libs.auth.access import issue_access_token
    http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")


def deliver(http, kind: str, obj: dict, *, secret: str = SECRET, at: int | None = None):
    """An event as Stripe sends one: signed over the timestamp and the exact body, with the endpoint's
    secret."""
    body = json.dumps({"id": f"evt_{uuid.uuid4().hex[:12]}", "object": "event", "type": kind,
                       "api_version": "2026-10-28.endive", "created": int(time.time()), "livemode": False,
                       "data": {"object": obj}, "pending_webhooks": 1})
    stamp = at or int(time.time())
    signature = hmac.new(secret.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return http.post("/billing/webhook", content=body,
                     headers={"Stripe-Signature": f"t={stamp},v1={signature}",
                              "Content-Type": "application/json"})


# A stored config as accounts have them: their words, and a country, which a posting naming none passes.
CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer", "golang", "postgres"],
                        "locations": ["germany"]}], "languages": ["en"]}


def searching(db, **fields) -> User:
    """Somebody whose search is set up, so the Jobs page lists their jobs."""
    user = person(db, full_name="Alice Example", onboarding_completed_at=now(), **fields)
    insert(db, UserConfig(user_id=user.id, config=CONFIG, schema_version=2))
    return user


def posting(days_ago: float) -> CorpusJob:
    """A job for those words, found and read that long ago."""
    at = now() - timedelta(days=days_ago)
    return CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer",
                     description="we build things", first_seen_at=at, described_at=at)


def listed(http, query: str = "") -> set[int]:
    return {job["id"] for job in http.get(f"/feed{query}").json()["jobs"]}


def is_member(http) -> bool:
    return http.get("/billing").json()["member"]


def test_a_free_account_reaches_every_page_of_the_site(db, http, monkeypatch):
    """The site is everybody's (owner, 2026-11-12: "users get to see the whole site for free"), checked
    across the whole API: no page answers a free account 402 but making a document, which needs a body to be
    asked at all and is asked in the next test."""
    from libs.secrets import generate_key
    from services.api.app import app

    # The pages that hold a credential build their store first, and it needs the platform key.
    monkeypatch.setenv("SECRETS_KEK", generate_key())
    sign_in(http, searching(db))
    asked, closed = 0, []
    for path, operations in app.openapi()["paths"].items():
        # Signing in and out would end the session the walk is made in.
        if path.startswith("/auth/"):
            continue
        concrete = re.sub(r"\{(\w+)\}", lambda name: str(uuid.uuid4()) if "user" in name[1]
                          else {"kind": "cv", "mark": "applied"}.get(name[1], "1"), path)
        for method in operations:
            asked += 1
            if http.request(method.upper(), concrete).status_code == 402:
                closed.append(f"{method.upper()} {path}")
    assert not closed, f"closed to a free account: {closed}"
    # The walk reached the site, not a handful of it.
    assert asked >= 60


def test_a_free_account_makes_no_documents_and_is_told_where_the_membership_is(db, http):
    """Owner, 2026-11-12: "no option to make a cv or cover either". The bell says it too, linking to the
    membership; a member's press goes on to the document's own checks."""
    job = insert(db, posting(8))
    free = searching(db)
    sign_in(http, free)

    answer = http.post("/documents/build", json={"job_id": job.id, "kind": "cv", "mode": "ai"})
    assert answer.status_code == 402
    assert answer.json()["detail"] == "Making CVs and cover letters comes with a membership."
    [note] = db[UserNotification.COLLECTION].find({"user_id": free.id})
    assert (note["event"], note["url"]) == ("document.needs_membership", "#/upgrade")

    sign_in(http, searching(db, subscription_status="trialing"))
    pressed = http.post("/documents/build", json={"job_id": job.id, "kind": "cv", "mode": "ai"})
    assert pressed.status_code != 402


def test_a_free_account_sees_each_job_a_week_late_until_it_leaves_everybodys_list(db, http):
    """Owner, 2026-11-12: "only jobs that have passed the 7 day mark should appear for them", and jobs stay
    30 days for everybody. A newer job's text and its marks are not theirs yet either, asked by its number."""
    new, week_old, month_old, gone = (insert(db, posting(days)).id for days in (0.1, 8, 29, 31))

    sign_in(http, searching(db, subscription_status="active"))
    body = http.get("/feed").json()
    assert (body["delay_hours"], body["shelf_hours"]) == (0, 720)
    assert {new, week_old, month_old} <= listed(http) and gone not in listed(http)

    sign_in(http, searching(db))
    assert http.get("/feed").json()["delay_hours"] == 168
    assert listed(http) & {new, week_old, month_old, gone} == {week_old, month_old}
    # "Posted" shorter than their wait would be empty, and is all of their list; a longer one narrows it.
    assert listed(http, "?since_hours=24") & {new, week_old, month_old} == {week_old, month_old}
    assert listed(http, "?since_hours=336") & {new, week_old, month_old} == {week_old}
    assert http.get(f"/jobs/{new}/description").status_code == 404
    assert http.put(f"/jobs/{new}/applied").status_code == 404
    assert http.get(f"/jobs/{week_old}/description").status_code == 200
    assert http.put(f"/jobs/{week_old}/applied").status_code == 200
    [banner] = http.get("/dashboard").json()["banners"]
    assert banner["key"] == "free"
    assert banner["text"].startswith("You see jobs 7 days after members see them.")


def test_the_operator_sets_how_far_behind_a_free_account_is(db, http):
    """`free_delay_hours`, in Admin: 0 shows a free account every job at once; the documents stay members'."""
    from libs import settings

    new = insert(db, posting(0.1)).id
    sign_in(http, searching(db))
    assert new not in listed(http)
    settings.put(db, "free_delay_hours", 0)
    assert new in listed(http) and http.get("/feed").json()["delay_hours"] == 0
    assert http.get("/dashboard").json()["banners"][0]["text"] == \
        "CVs, cover letters and alerts come with a membership."


def test_a_new_account_picks_its_plan_first_and_free_asks_for_no_card(db, http):
    """Owner, 2026-11-12: after the email link and before setting up, the plan, "not during the initial
    signup"; free goes on to setting up "without having to give their details", and the trial comes with the
    membership."""
    newcomer = person(db)
    sign_in(http, newcomer)

    board = http.get("/dashboard").json()
    assert (board["stage"], board["member"]) == ("plan", False)
    assert http.post("/billing/free").status_code == 200
    board = http.get("/dashboard").json()
    assert (board["stage"], board["member"]) == ("onboarding", False)
    assert get(db, User, newcomer.id).plan_chosen_at is not None
    membership = http.get("/billing").json()
    assert membership["member"] is False and membership["can_start"] is True and membership["trial_days"] == 7
    assert membership["publishable_key"] == "pk_test_fake"
    assert http.get("/billing/plan").json() == {"amount": 999, "currency": "eur", "interval": "month",
                                                "interval_count": 1, "trial_days": 7, "free_delay_hours": 168}


def test_only_a_new_account_is_asked_for_its_plan(db, http):
    """Asked once, before setting up: not a member already (free access, an admin), not somebody whose
    membership ended (free without asking again), and not an account set up before the choice began."""
    asked = {}
    for name, fields in (("new", {}), ("member", {"subscription_status": "trialing"}),
                         ("lapsed", {"subscription_status": "canceled"}),
                         ("free access", {"free_access_at": now()}), ("admin", {"is_admin": True}),
                         ("set up", {"onboarding_completed_at": now()}),
                         ("picked free", {"plan_chosen_at": now()})):
        sign_in(http, person(db, **fields))
        asked[name] = http.get("/dashboard").json()["stage"] == "plan"

    assert asked == {"new": True, "member": False, "lapsed": False, "free access": False, "admin": False,
                     "set up": False, "picked free": False}


def test_the_form_starts_the_trial_with_a_card_and_only_for_somebody_who_never_had_one(db, http, fake):
    newcomer = person(db)
    sign_in(http, newcomer)

    answer = http.post("/billing/checkout")
    assert answer.status_code == 200 and answer.json()["client_secret"] == "cs_1_secret"
    [customer] = fake.calls("POST", "/v1/customers")
    assert customer["email"] == newcomer.email and customer["metadata[user_id]"] == str(newcomer.id)
    assert get(db, User, newcomer.id).stripe_customer_id == "cus_1"
    [form] = fake.calls("POST", "/v1/checkout/sessions")
    # Stripe's own checkout inside the page (owner, 2026-11-12: "it needs to be stripes payment page"), a card
    # finishing there and only a bank's page coming back by the address.
    assert form["mode"] == "subscription" and form["ui_mode"] == "embedded_page"
    assert form["redirect_on_completion"] == "if_required"
    assert form["customer"] == "cus_1" and form["line_items[0][price]"] == "price_test"
    assert form["subscription_data[trial_period_days]"] == "7"
    # A card even for the free trial, so the first payment is taken by itself when it ends.
    assert form["payment_method_collection"] == "always"
    assert form["return_url"] == "https://jobs.example/?stripe=back#/"

    # Somebody whose membership ended: the same form, at the price, without a second free trial.
    fake.customers["cus_old"] = {"id": "cus_old", "object": "customer"}
    ended = fake.subscribe("cus_old", "canceled", ended_at=int(time.time()) - DAY)
    returning = person(db, stripe_customer_id="cus_old", subscription_id=ended["id"],
                       subscription_status="canceled")
    sign_in(http, returning)
    assert http.get("/billing").json()["trial_days"] == 0
    assert http.post("/billing/checkout").status_code == 200
    form = fake.calls("POST", "/v1/checkout/sessions")[-1]
    assert form["customer"] == "cus_old"
    assert "subscription_data[trial_period_days]" not in form
    assert len(fake.calls("POST", "/v1/customers")) == 1, "a returning member keeps their one customer"


def test_no_free_trial_when_the_operator_turns_it_off(db, http, fake):
    from libs import settings

    settings.put(db, "trial_days", 0)
    sign_in(http, person(db))
    assert http.get("/billing/plan").json()["trial_days"] == 0
    assert http.post("/billing/checkout").status_code == 200
    assert "subscription_data[trial_period_days]" not in fake.calls("POST", "/v1/checkout/sessions")[0]


def test_opening_the_form_again_ends_the_one_opened_before(db, http, fake):
    """Two tabs must not become two memberships, each billed."""
    sign_in(http, person(db))
    http.post("/billing/checkout")
    http.post("/billing/checkout")
    assert fake.sessions["cs_1"]["status"] == "expired"
    assert fake.sessions["cs_2"]["status"] == "open"


def test_somebody_with_a_running_membership_is_not_sold_a_second(db, http, fake):
    owing = person(db, stripe_customer_id="cus_9")
    fake.subscribe("cus_9", "past_due", period_end=int(time.time()) + 20 * DAY)
    sign_in(http, owing)

    answer = http.post("/billing/checkout")
    assert answer.status_code == 409
    assert not fake.calls("POST", "/v1/checkout/sessions")
    membership = http.get("/billing").json()
    assert membership["can_start"] is False and membership["status"] == "past_due"
    assert membership["member"] is False, "an unpaid membership gives nothing more than the free plan"


def test_coming_back_from_the_form_is_a_member_at_once(db, http, fake):
    """Before Stripe's webhook gets here: the person is read from Stripe the moment they are back, and the
    sign-up page's Member choice goes on to set up the search as a member."""
    newcomer = person(db)
    sign_in(http, newcomer)
    http.post("/billing/checkout")
    trial_end = int(time.time()) + 7 * DAY
    fake.subscribe("cus_1", "trialing", trial_end=trial_end, period_end=trial_end,
                   invoice=paid(0, int(time.time())))

    assert is_member(http) is False
    membership = http.post("/billing/refresh").json()
    assert membership["member"] is True and membership["status"] == "trialing"
    assert http.get("/dashboard").json() | {"onboarding": None} == {
        "stage": "onboarding", "is_admin": False, "member": True, "onboarding": None}
    stored = get(db, User, newcomer.id)
    assert stored.trial_ends_at.timestamp() == trial_end
    # Read off the subscription's item, where Stripe's API keeps it since 2025-03-31.
    assert stored.period_ends_at.timestamp() == trial_end
    # A trial's 0 is not a payment.
    assert stored.last_paid_at is None


def test_stripe_says_paid_failed_and_paid_again_and_the_membership_comes_goes_and_comes_back(db, http, fake):
    """A failed payment ends what the membership gives at once (owner, 2026-11-11): the free plan, until it
    is paid."""
    member = searching(db, stripe_customer_id="cus_5")
    fake.customers["cus_5"] = {"id": "cus_5", "object": "customer"}
    sign_in(http, member)
    period_end = int(time.time()) + 30 * DAY
    subscription = fake.subscribe("cus_5", "active", period_end=period_end,
                                  invoice=paid(999, int(time.time()) - 60))

    assert deliver(http, "invoice.paid", {"object": "invoice", "id": "in_1", "customer": "cus_5"}).json() == \
        {"status": "ok"}
    assert is_member(http) and http.get("/feed").json()["delay_hours"] == 0
    first = get(db, User, member.id)
    assert (first.subscription_status, first.last_paid_amount, first.currency) == ("active", 999, "eur")

    # The renewal's payment fails: closed at once.
    subscription.update(status="past_due",
                        latest_invoice={**paid(999, 0), "status": "open", "amount_paid": 0})
    deliver(http, "invoice.payment_failed", {"object": "invoice", "id": "in_2", "customer": "cus_5"})
    assert not is_member(http) and http.get("/feed").json()["delay_hours"] == 168
    assert get(db, User, member.id).last_paid_at == first.last_paid_at, "a failed payment is not a payment"

    # Paid at Stripe's portal: open again, and the payment is the last one.
    paid_at = int(time.time())
    subscription.update(status="active", latest_invoice=paid(999, paid_at))
    deliver(http, "invoice.paid", {"object": "invoice", "id": "in_2", "customer": "cus_5"})
    assert is_member(http)
    assert get(db, User, member.id).last_paid_at.timestamp() == paid_at


def test_what_stripe_has_now_wins_over_what_an_event_says(db, http, fake):
    """An old event arriving late, or twice, cannot open an ended membership: the event only says whose
    membership to read again (leadops wrote an older event over a newer one)."""
    member = person(db, stripe_customer_id="cus_7", subscription_status="active", subscription_id="sub_1")
    fake.subscribe("cus_7", "canceled", ended_at=int(time.time()) - 60)
    stale = {"object": "subscription", "id": "sub_1", "customer": "cus_7", "status": "active"}
    for _ in range(2):
        assert deliver(http, "customer.subscription.updated", stale).status_code == 200
    stored = get(db, User, member.id)
    assert stored.subscription_status == "canceled"
    sign_in(http, member)
    assert is_member(http) is False


def test_an_older_read_of_stripe_is_never_written_over_a_newer_one(db, fake):
    """Stripe sends the last failed payment and the cancel within a second (2026-11-11, test clock), and two
    syncs at once may finish in either order: the one that read Stripe first must not write last."""
    from datetime import timedelta

    from services.api.billing_routes import sync

    member = person(db, stripe_customer_id="cus_13", subscription_status="canceled",
                    billing_synced_at=now() + timedelta(seconds=5))
    fake.subscribe("cus_13", "past_due", period_end=int(time.time()) + DAY)

    assert sync(db, get(db, User, member.id)).subscription_status == "canceled"
    db[User.COLLECTION].update_one({"_id": member.id},
                                   {"$set": {"billing_synced_at": now() - timedelta(seconds=5)}})
    assert sync(db, get(db, User, member.id)).subscription_status == "past_due", "a newer read writes"


def test_a_cancelled_membership_runs_to_the_end_of_what_was_paid(db, http, fake):
    member = person(db, stripe_customer_id="cus_8")
    ends = int(time.time()) + 10 * DAY
    fake.subscribe("cus_8", "active", period_end=ends, cancel_at=ends)
    deliver(http, "customer.subscription.updated",
            {"object": "subscription", "id": "sub_1", "customer": "cus_8"})
    sign_in(http, member)
    membership = http.get("/billing").json()
    assert membership["member"] is True
    assert membership["cancels_at"] is not None and membership["status"] == "active"
    assert get(db, User, member.id).cancels_at.timestamp() == ends


def test_a_webhook_not_signed_with_the_endpoints_secret_changes_nothing(db, http, fake):
    member = person(db, stripe_customer_id="cus_3")
    fake.subscribe("cus_3", "active", period_end=int(time.time()) + DAY)
    event = {"object": "invoice", "id": "in_1", "customer": "cus_3"}

    assert deliver(http, "invoice.paid", event, secret="whsec_somebody_else").status_code == 400
    # A signature replayed from long ago.
    assert deliver(http, "invoice.paid", event, at=int(time.time()) - 3600).status_code == 400
    assert http.post("/billing/webhook", content=b"{}").status_code == 400
    assert get(db, User, member.id).subscription_status is None
    assert not fake.calls("GET", "/v1/subscriptions"), "nothing unsigned makes the site read Stripe"


def test_events_the_site_does_not_use_or_customers_it_does_not_know_are_acknowledged(db, http, fake):
    """Answered 200, so Stripe does not send them again for three days."""
    ignored = deliver(http, "customer.updated", {"object": "customer", "id": "cus_x"})
    assert ignored.json() == {"status": "ignored"}
    stranger = deliver(http, "invoice.paid", {"object": "invoice", "id": "in_1", "customer": "cus_nobody"})
    assert stranger.json() == {"status": "unknown customer"}


def test_stripe_down_says_try_again_and_a_webhook_is_sent_again(db, http, fake):
    member = person(db, stripe_customer_id="cus_4")
    sign_in(http, member)
    fake.down = True
    answer = http.post("/billing/checkout")
    assert answer.status_code == 503 and "Try again" in answer.json()["detail"]
    assert http.post("/billing/refresh").status_code == 503
    # 503: Stripe delivers it again later, when it can be read back.
    again = deliver(http, "invoice.paid", {"object": "invoice", "id": "in_1", "customer": "cus_4"})
    assert again.status_code == 503


def test_settings_say_when_they_last_paid_and_when_the_next_payment_is(db, http, fake):
    member = person(db, stripe_customer_id="cus_6")
    next_payment, paid_at = int(time.time()) + 20 * DAY, int(time.time()) - 10 * DAY
    fake.subscribe("cus_6", "active", period_end=next_payment, invoice=paid(1250, paid_at))
    sign_in(http, member)
    http.post("/billing/refresh")

    membership = http.get("/billing").json()
    assert membership["status"] == "active" and membership["member"] is True
    assert membership["last_paid_amount"] == 1250 and membership["currency"] == "eur"
    assert membership["period_ends_at"].startswith(time.strftime("%Y-%m-%d", time.gmtime(next_payment)))
    assert membership["last_paid_at"].startswith(time.strftime("%Y-%m-%d", time.gmtime(paid_at)))
    assert membership["can_manage"] is True


def test_the_portal_is_their_own_and_comes_back_to_settings(db, http, fake):
    member = person(db, stripe_customer_id="cus_2")
    sign_in(http, member)
    assert http.post("/billing/portal").json() == {"url": "https://billing.stripe.com/p/session/test_1"}
    [portal] = fake.calls("POST", "/v1/billing_portal/sessions")
    assert portal["customer"] == "cus_2"
    assert portal["return_url"] == "https://jobs.example/?stripe=back#/settings"
    assert "configuration" not in portal, "the account's own portal, as saved at Stripe"

    sign_in(http, person(db))
    assert http.post("/billing/portal").status_code == 409



def test_free_access_from_an_admin_gives_the_membership_whatever_stripe_says(db, http, fake):
    """Somebody with no membership, or one whose payment failed, is let in by the switch, and back to the
    free plan when it is off. Nothing to pause at Stripe for them: no payment is being taken."""
    operator = person(db, email=ADMIN)
    newcomer = person(db)

    sign_in(http, operator)
    assert http.put(f"/admin/users/{newcomer.id}/free-access", json={"on": True}).status_code == 200
    detail = http.get(f"/admin/users/{newcomer.id}").json()["user"]
    assert detail["member"] is True and detail["free_access_at"] is not None
    sign_in(http, newcomer)
    assert is_member(http) and http.get("/billing").json()["free_access"] is True

    sign_in(http, operator)
    http.put(f"/admin/users/{newcomer.id}/free-access", json={"on": False})
    sign_in(http, newcomer)
    assert is_member(http) is False
    assert {method for method, _path, _params in fake.sent} <= {"GET"}, "nothing at Stripe to change"


def test_free_access_holds_what_stripe_charges_and_ending_it_starts_it_again(db, http, fake):
    """Owner, 2026-11-12: "turn it on, make it free, and then turn it off and its activated again, and this
    can be in cycles". On: their membership's payments are paused at Stripe with each invoice voided, and one
    already owed voided too, which Stripe would otherwise keep trying to take. Off: they start again from the
    next one. Each asked twice, as a double click does."""
    operator = person(db, email=ADMIN)
    fake.customers["cus_10"] = {"id": "cus_10", "object": "customer"}
    subscription = fake.subscribe("cus_10", "past_due", period_end=int(time.time()) + 20 * DAY)
    owed = fake.bill(subscription)
    let_in = person(db, stripe_customer_id="cus_10")
    sign_in(http, operator)

    for cycle in range(2):
        for _ in range(2):
            assert http.put(f"/admin/users/{let_in.id}/free-access", json={"on": True}).status_code == 200
        assert subscription["pause_collection"] == {"behavior": "void"}
        assert owed["status"] == "void", "what was owed is not taken later either"
        assert get(db, User, let_in.id).free_access_at is not None
        for _ in range(2):
            assert http.put(f"/admin/users/{let_in.id}/free-access", json={"on": False}).status_code == 200
        assert subscription["pause_collection"] is None, f"cycle {cycle}: payments start again"
        assert get(db, User, let_in.id).free_access_at is None
        owed = fake.bill(subscription)

    # Stripe down: the switch does not move, so the account is never free while Stripe still charges it.
    fake.down = True
    answer = http.put(f"/admin/users/{let_in.id}/free-access", json={"on": True})
    assert answer.status_code == 503 and "Try again" in answer.json()["detail"]
    assert get(db, User, let_in.id).free_access_at is None


def test_free_access_keeps_the_day_it_was_first_given(db, http, fake):
    operator = person(db, email=ADMIN)
    let_in = person(db)
    sign_in(http, operator)
    http.put(f"/admin/users/{let_in.id}/free-access", json={"on": True})
    first = get(db, User, let_in.id).free_access_at
    http.put(f"/admin/users/{let_in.id}/free-access", json={"on": True})
    assert get(db, User, let_in.id).free_access_at == first


def test_an_admin_needs_no_membership_and_nobody_else_may_give_free_access(db, http):
    sign_in(http, person(db, email=ADMIN))
    assert is_member(http)
    member = person(db, subscription_status="active")
    sign_in(http, member)
    assert http.put(f"/admin/users/{member.id}/free-access", json={"on": True}).status_code == 404


def test_the_account_list_says_where_each_membership_stands(db, http):
    person(db, email="trial@example.com", subscription_status="trialing")
    person(db, email="free@example.com", free_access_at=now())
    sign_in(http, person(db, email=ADMIN))
    rows = {row["email"]: row for row in http.get("/admin/users").json()["users"]}
    assert rows["trial@example.com"]["membership"] == "trialing"
    assert rows["free@example.com"]["free_access"] is True


def test_deleting_an_account_ends_its_membership_at_stripe_first(db, http, fake):
    """An account deleted while Stripe still bills it is a charge nobody can stop."""
    from libs.auth import hash_password

    member = person(db, stripe_customer_id="cus_11", subscription_status="active",
                    password_hash=hash_password("a long enough password"))
    fake.customers["cus_11"] = {"id": "cus_11", "object": "customer"}
    fake.subscribe("cus_11", "active", period_end=int(time.time()) + DAY)
    sign_in(http, member)

    fake.down = True
    answer = http.request("DELETE", "/account", json={"password": "a long enough password"})
    assert answer.status_code == 503
    assert get(db, User, member.id) is not None, "not deleted while its membership could not be ended"

    fake.down = False
    assert http.request("DELETE", "/account", json={"password": "a long enough password"}).status_code == 200
    assert "cus_11" not in fake.customers
    assert fake.subscriptions[0]["status"] == "canceled"
    assert get(db, User, member.id) is None


def test_an_admin_deleting_an_account_ends_its_membership_too(db, http, fake):
    doomed = person(db, stripe_customer_id="cus_12", subscription_status="active")
    fake.customers["cus_12"] = {"id": "cus_12", "object": "customer"}
    sign_in(http, person(db, email=ADMIN))
    answer = http.request("DELETE", f"/admin/users/{doomed.id}", json={"email": doomed.email})
    assert answer.status_code == 200
    assert "cus_12" not in fake.customers
    # A customer Stripe no longer has (deleted there by hand, or made with other keys) does not stop it.
    gone = person(db, stripe_customer_id="cus_nowhere")
    assert http.request("DELETE", f"/admin/users/{gone.id}", json={"email": gone.email}).status_code == 200
    assert get(db, User, gone.id) is None


def test_alerts_and_the_daily_email_are_members_and_the_harvest_is_everybodys(db, monkeypatch):
    """Alerts come with a membership (owner, 2026-11-12: "no alerts on free"); a free account's words are
    still searched for, or its list a week behind would have nothing in it."""
    from libs.db.models import CorpusJob
    from libs.queue.queue import dispatch_due_digests, dispatch_due_notifications
    from libs.tenant.repository import TenantRepository

    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    # Something new in the corpus, so the sweep asks whom it is for.
    insert(db, CorpusJob(title="Engineer", company="Acme", url="https://example.com/1", source="greenhouse"))
    alerts = {"telegram_status": "connected", "email_alerts": True, "timezone": "UTC",
              "email_digest_at": None}
    paying = person(db, subscription_status="active", **alerts)
    lapsed = person(db, subscription_status="past_due", **alerts)
    let_in = person(db, free_access_at=now(), **alerts)
    operator = person(db, email=ADMIN, **alerts)
    for each in (paying, lapsed, let_in, operator):
        insert(db, UserConfig(user_id=each.id, config={"sections": []}))

    dispatch_due_notifications(db)
    notified = set(db[QueueJob.COLLECTION].distinct(
        "user_id", {"lane": "notify", "payload.digest": {"$ne": True}}))
    assert notified == {paying.id, let_in.id, operator.id}

    monkeypatch.setattr("libs.queue.queue.due", lambda *_args, **_kwargs: True)
    dispatch_due_digests(db)
    emailed = set(db[QueueJob.COLLECTION].distinct("user_id", {"lane": "notify", "payload.digest": True}))
    assert emailed == {paying.id, let_in.id, operator.id}

    assert set(TenantRepository(db).active_configs()) == {paying.id, lapsed.id, let_in.id, operator.id}


def test_setup_problems_are_named(monkeypatch):
    from services.api.billing_routes import setup_problems

    for name in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert len(setup_problems()) == 4
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    assert setup_problems() == [
        "STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY are from different modes (test, live)"]


def test_without_stripe_the_payment_form_says_so_and_no_price_is_made_up(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    for name in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    from fastapi.testclient import TestClient

    from services.api import billing_routes
    from services.api.app import app
    billing_routes._stripe.cache_clear()
    with TestClient(app, base_url="https://testserver") as http:
        sign_in(http, person(db))
        assert http.get("/billing/plan").json() is None
        membership = http.get("/billing").json()
        assert membership["plan"] is None and membership["publishable_key"] is None
        answer = http.post("/billing/checkout")
        assert answer.status_code == 503 and "aren't set up" in answer.json()["detail"]


def test_a_customer_stripe_does_not_have_is_forgotten_and_a_real_one_made(db, http, fake):
    """Test mode's customer, asked with live keys: what was read of it opens nothing, and the payment form
    makes a real customer rather than failing for good."""
    tested = person(db, stripe_customer_id="cus_testmode", subscription_id="sub_testmode",
                    subscription_status="trialing")
    fake.gone.add("cus_testmode")
    sign_in(http, tested)
    assert is_member(http), "a member until Stripe is asked"

    answer = http.post("/billing/checkout")
    assert answer.status_code == 200
    stored = get(db, User, tested.id)
    assert stored.stripe_customer_id == "cus_1" and stored.subscription_status is None
    form = fake.calls("POST", "/v1/checkout/sessions")[-1]
    assert form["customer"] == "cus_1"
    assert form["subscription_data[trial_period_days]"] == "7", "a trial in test mode was no trial"
    assert is_member(http) is False


def test_after_the_keys_change_every_account_is_read_again(db, fake):
    """`python -m services.api.billing_routes`, run once after moving between test and live keys."""
    from services.api.billing_routes import reread_all

    tested = person(db, stripe_customer_id="cus_testmode", subscription_status="active")
    paying = person(db, stripe_customer_id="cus_live")
    person(db)
    fake.gone.add("cus_testmode")
    fake.subscribe("cus_live", "active", period_end=int(time.time()) + DAY)

    assert reread_all(db) == {"none": 1, "active": 1}
    assert get(db, User, tested.id).subscription_status is None
    assert get(db, User, tested.id).stripe_customer_id is None
    assert get(db, User, paying.id).subscription_status == "active"


def test_the_deploy_lets_in_every_account_there_was_before_memberships_once(db, capsys):
    """Owner, 2026-11-11: "existing accounts get free access right now all of them". The deploy's schema
    step does it, and only the first time, or an admin's later Off would be undone and the next signup let
    in."""
    from libs.db.__main__ import main
    from libs.membership import is_member

    there = [person(db), person(db, subscription_status="past_due"),
             person(db, status="pending_verification")]
    main()
    assert "3 account(s) from before memberships given free access" in capsys.readouterr().out
    assert all(is_member(db, each.id) for each in there)

    later = person(db)
    db[User.COLLECTION].update_one({"_id": there[0].id}, {"$set": {"free_access_at": None}})
    main()
    assert "free access" not in capsys.readouterr().out
    assert not is_member(db, later.id), "an account made afterwards pays"
    assert not is_member(db, there[0].id), "an admin's Off stays off"
