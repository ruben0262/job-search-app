"""The landing page's pricing: Free, Starter and Member (owner, 2026-11-12 and 2026-11-16). What each card
says of its plan is worked out from the plans fixed in code and the settings the site reads, so it is what an
account on that plan gets; the rest of the words are the operator's; a price is Stripe's and the free days
the trial setting."""
from __future__ import annotations

import logging
import os
import uuid

import pytest

from libs import settings
from libs.db import insert
from libs.db.models import User
from libs.membership import AREAS

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"
CARD = {"name": "Mahoraga Plus", "highlight": "Yours for life", "also": ["Every job that fits you"],
        "button": "Try it free", "note": "Cancel any time."}
FREE = {**CARD, "name": "Free", "highlight": "", "button": "Start free"}
CARDS = {"free": FREE, "starter": {**CARD, "name": "First steps"}, "member": CARD}
MOMENT = AREAS["early_jobs"]


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


def on(db, plan: str) -> User:
    """An account on this plan: nothing paid, or paying for Starter or the membership."""
    paid = {} if plan == "free" else {"subscription_status": "active", "subscription_plan": plan}
    return insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active", **paid))


def test_anybody_reads_the_cards_with_the_trial_before_they_have_an_account(api):
    """Public, as the landing page is. Without Stripe set up there is no price, rather than a wrong one."""
    http, db, *_ = api

    body = http.get("/pricing").json()

    assert body["cards"] == settings.DEFAULT_PRICING_CARDS
    assert (body["trial_days"], body["plan"], body["starter"]) == (7, None, None)
    settings.put(db, "trial_days", 14)
    assert http.get("/pricing").json()["trial_days"] == 14, "the setting, not a copy of it"


def test_each_card_says_how_soon_its_plan_sees_a_job_and_what_it_gives_and_lacks(api):
    """Owner, 2026-11-16: Free each job once it is 7 days old, Starter once it is 3 days old with alerts and
    match scores, Member the moment it is posted with everything; each card ticking what its plan gives and
    crossing out what it lacks, in words that say what it does for them."""
    http, *_ = api

    lines = http.get("/pricing").json()["lines"]

    assert lines["free"] == {
        "gives": ["See every new job 7 days after we find it"],
        "lacks": [MOMENT, AREAS["alerts"], AREAS["match_scores"], AREAS["cvs"], AREAS["covers"],
                  AREAS["add_job"]]}
    assert lines["starter"] == {
        "gives": ["See every new job 3 days after we find it, 4 days before Free accounts do",
                  AREAS["alerts"], AREAS["match_scores"]],
        "lacks": [MOMENT, AREAS["cvs"], AREAS["covers"], AREAS["add_job"]]}
    assert lines["member"] == {
        "gives": [f"{MOMENT}, 7 days before Free accounts do", AREAS["alerts"], AREAS["match_scores"],
                  AREAS["cvs"], AREAS["covers"], AREAS["add_job"]],
        "lacks": []}


def test_how_soon_each_card_says_follows_the_waits_set_in_admin(api):
    """The waits are the operator's, and the cards say them as they are set: never typed into the words."""
    http, db, *_ = api

    settings.put(db, "free_delay_hours", 120)
    settings.put(db, "starter_delay_hours", 24)
    lines = http.get("/pricing").json()["lines"]
    assert lines["free"]["gives"][0] == "See every new job 5 days after we find it"
    assert lines["starter"]["gives"][0] == \
        "See every new job 24 hours after we find it, 4 days before Free accounts do"
    assert lines["member"]["gives"][0] == f"{MOMENT}, 5 days before Free accounts do"

    # Nobody waits: no card crosses out what every plan has, and none says it is sooner than Free.
    settings.put(db, "starter_delay_hours", 0)
    settings.put(db, "free_delay_hours", 0)
    lines = http.get("/pricing").json()["lines"]
    assert [lines[plan]["gives"][0] for plan in ("free", "starter", "member")] == [MOMENT] * 3
    assert not any(MOMENT in lines[plan]["lacks"] for plan in ("free", "starter", "member"))


def test_the_member_card_promises_the_members_chat_only_while_it_has_a_link(api):
    """Owner, 2026-11-16: direct access to the people who build the site is a member's, and never promised
    while there is nothing to open."""
    http, db, *_ = api
    chat = AREAS["team_access"]
    said = http.get("/pricing").json()["lines"].values()
    assert not any(chat in lines["gives"] + lines["lacks"] for lines in said)

    settings.put(db, "members_channel_url", "https://chat.example.com/mahoraga-members")
    lines = http.get("/pricing").json()["lines"]
    assert chat in lines["member"]["gives"]
    assert chat in lines["starter"]["lacks"] and chat in lines["free"]["lacks"]


@pytest.mark.parametrize("channel", ["", "https://chat.example.com/mahoraga-members"])
@pytest.mark.parametrize("waits", [(168, 72), (120, 0), (0, 0)])
def test_what_each_card_promises_is_what_an_account_on_that_plan_gets(api, channel, waits):
    """The cards and the site ask the same plans: an account on each plan has every area its card ticks and
    none its card crosses out, and sees new jobs at once exactly when its card says so. A card that promised
    an area its plan does not give, or left out one it does, fails here."""
    from libs.membership import delay_hours, has

    http, db, *_ = api
    settings.put(db, "members_channel_url", channel)
    settings.put(db, "free_delay_hours", waits[0])
    settings.put(db, "starter_delay_hours", waits[1])
    lines = http.get("/pricing").json()["lines"]

    for plan in ("free", "starter", "member"):
        account, said = on(db, plan), lines[plan]
        for area, words in AREAS.items():
            if area == "early_jobs":
                assert (words in said["lacks"]) == (delay_hours(db, account) > 0), plan
            elif area == "team_access" and not channel:
                assert words not in said["gives"] + said["lacks"], plan
            else:
                assert (words in said["gives"], words in said["lacks"]) == (has(db, account.id, area),
                                                                          not has(db, account.id, area)), \
                    (plan, area)


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
    {"name": " "}, {"button": ""}, {"also": ["Alerts", "  "]}, {"also": [f"Line {n}" for n in range(13)]},
    {"note": "x" * 281},
])
def test_a_card_the_page_has_no_room_for_is_refused_and_the_saved_ones_kept(api, change):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    assert http.put("/admin/pricing", json={**CARDS, "free": {**FREE, **change}}).status_code == 422
    assert http.put("/admin/pricing", json={"member": CARD}).status_code == 422, "every card, always"
    assert http.get("/pricing").json()["cards"] == settings.DEFAULT_PRICING_CARDS


def test_saved_cards_that_no_longer_read_are_the_defaults_and_the_log_says_so(api, caplog):
    """Saved some other way: the page shows the defaults, never an error. (A card saved before a field was
    added is completed from the defaults: the last test.)"""
    http, db, *_ = api
    settings.put(db, "pricing_cards", {"free": {"name": "Old", "also": "not a list"}})

    with caplog.at_level(logging.WARNING, logger="services.api.pricing_routes"):
        body = http.get("/pricing").json()

    assert body["cards"] == settings.DEFAULT_PRICING_CARDS
    assert "the saved cards do not read" in caplog.text


def test_the_price_is_the_one_the_payment_form_charges(api, monkeypatch):
    """Stripe's, through the function the form reads it with, never a number typed into the card."""
    from services.api import billing_routes
    from services.api.billing_routes import Plan

    http, *_ = api
    stripe = Plan(amount=499, currency="gbp", interval="month", interval_count=1, trial_days=7)
    monkeypatch.setattr(billing_routes, "plan",
                        lambda db, which="member": stripe if which == "member" else None)

    body = http.get("/pricing").json()
    assert body["plan"] == stripe.model_dump()
    assert body["starter"] is None, "no Starter plan to offer until it has a price at Stripe"


def test_each_price_is_shown_reduced_from_the_operators_figure_and_nothing_else(api):
    """Owner, 2026-11-16: the crossed-out price is the operator's figure, and anything saved there that is not
    a price shows none rather than breaking the page."""
    http, db, *_ = api
    assert http.get("/pricing").json()["was"] == {"member": 2999, "starter": 1899}

    settings.put(db, "price_was", {"member": 0, "starter": "a lot"})
    assert http.get("/pricing").json()["was"] == {}


def test_cards_saved_before_the_plans_were_fixed_keep_their_own_words_and_lose_their_old_lists(api):
    """Saved on the live site as two cards, each with a line under its name and its own lists of what it
    gave and left out, written for the plans as they were: those lists would now say what the plans no
    longer are, beside the lines each card says by itself, so they are not read. The name, the button and
    the small print are the operator's and stay; Starter's card, and the field there was none of, are the
    defaults'."""
    http, db, *_ = api
    defaults = settings.DEFAULT_PRICING_CARDS

    def before(card: dict) -> dict:
        return {"name": card["name"], "tagline": "A line nobody reads now.", "button": card["button"],
                "note": card["note"], "benefits": ["New jobs the moment they're found, not a week later"],
                "left_out": ["Alerts on your phone"]}

    settings.put(db, "pricing_cards", {"free": before(FREE), "member": before(CARD)})

    cards = http.get("/pricing").json()["cards"]

    for name, own in (("free", FREE), ("member", CARD)):
        default = defaults[name]
        assert cards[name] == {**own, "highlight": default["highlight"], "also": default["also"]}
    assert cards["starter"] == defaults["starter"]
