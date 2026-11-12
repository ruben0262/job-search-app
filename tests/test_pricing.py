"""The landing page's pricing: Free and Member, the cards' words the operator's, the membership's price
Stripe's, its free days the trial setting and Free's delay its own setting (owner, 2026-11-12)."""
from __future__ import annotations

import logging
import os
import uuid

import pytest

from libs import settings
from libs.db import insert
from libs.db.models import User

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"
CARD = {"name": "Mahoraga Plus", "tagline": "All of it.", "benefits": ["Every job that fits you", "Alerts"],
        "left_out": [], "button": "Try it free", "note": "Cancel any time."}
FREE = {**CARD, "name": "Free", "left_out": ["Alerts"], "button": "Start free"}
CARDS = {"free": FREE, "member": CARD}


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    for name in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID"):
        monkeypatch.delenv(name, raising=False)

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    operator = insert(db, User(email=ADMIN, password_hash="x", status="active"))
    ordinary = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

    def sign_in(http, user):
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    with TestClient(app, base_url="https://testserver") as http:
        yield http, db, operator, ordinary, sign_in


def test_anybody_reads_both_cards_with_the_trial_and_the_delay_before_they_have_an_account(api):
    """Public, as the landing page is. Without Stripe set up there is no price, rather than a wrong one."""
    http, db, *_ = api

    body = http.get("/pricing").json()

    assert body["cards"] == settings.DEFAULT_PRICING_CARDS
    assert (body["trial_days"], body["free_delay_hours"], body["plan"]) == (7, 168, None)
    settings.put(db, "trial_days", 14)
    settings.put(db, "free_delay_hours", 72)
    again = http.get("/pricing").json()
    assert (again["trial_days"], again["free_delay_hours"]) == (14, 72), "the settings, not copies of them"


def test_an_admin_changes_what_the_cards_say_and_the_landing_page_shows_it(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    saved = http.put("/admin/pricing", json={**CARDS, "member": {**CARD, "name": "  Mahoraga Plus  "}})

    assert saved.status_code == 200 and saved.json()["cards"] == CARDS, "kept trimmed"
    http.cookies.clear()
    assert http.get("/pricing").json()["cards"] == CARDS


def test_nobody_else_can_change_it(api):
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.put("/admin/pricing", json=CARDS).status_code == 404
    assert http.get("/pricing").json()["cards"] == settings.DEFAULT_PRICING_CARDS


@pytest.mark.parametrize("change", [
    {"name": " "}, {"button": ""}, {"benefits": []}, {"benefits": ["Alerts", "  "]},
    {"benefits": [f"Line {n}" for n in range(13)]}, {"left_out": ["  "]}, {"tagline": "x" * 121},
])
def test_a_card_the_page_has_no_room_for_is_refused_and_the_saved_ones_kept(api, change):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    assert http.put("/admin/pricing", json={**CARDS, "free": {**FREE, **change}}).status_code == 422
    assert http.put("/admin/pricing", json={"member": CARD}).status_code == 422, "both cards, always"
    assert http.get("/pricing").json()["cards"] == settings.DEFAULT_PRICING_CARDS


def test_saved_cards_that_no_longer_read_are_the_defaults_and_the_log_says_so(api, caplog):
    """Saved some other way, or before a field was added: the page shows the defaults, never an error."""
    http, db, *_ = api
    settings.put(db, "pricing_cards", {"free": {"name": "Old"}})

    with caplog.at_level(logging.WARNING, logger="services.api.pricing_routes"):
        body = http.get("/pricing").json()

    assert body["cards"] == settings.DEFAULT_PRICING_CARDS
    assert "the saved cards do not read" in caplog.text


def test_the_price_is_the_one_the_payment_form_charges(api, monkeypatch):
    """Stripe's, through the function the form reads it with, never a number typed into the card."""
    from services.api import billing_routes
    from services.api.billing_routes import Plan

    http, *_ = api
    stripe = Plan(amount=499, currency="gbp", interval="month", interval_count=1, trial_days=7,
                  free_delay_hours=168)
    monkeypatch.setattr(billing_routes, "plan", lambda db: stripe)

    assert http.get("/pricing").json()["plan"] == stripe.model_dump()
