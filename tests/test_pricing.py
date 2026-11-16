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
from libs.membership import AREAS, EVERYONE

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"
CARD = {"name": "Mahoraga Plus", "highlight": "Yours for life", "button": "Try it free",
        "note": "Cancel any time."}
FREE = {**CARD, "name": "Free", "highlight": "", "button": "Start free"}
CARDS = {"free": FREE, "starter": {**CARD, "name": "First steps"}, "member": CARD}


def said(name: str) -> dict:
    """A line as a card says it: its words, and what its question mark says."""
    return {"text": AREAS[name].short, "more": AREAS[name].more}


EVERY = [{"text": each.short, "more": each.more} for each in EVERYONE]
MOMENT = said("early_jobs")


def texts(lines: list[dict]) -> list[str]:
    return [line["text"] for line in lines]


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
    """Owner, 2026-11-16: Free each job once it is 7 days old, Starter 3 days or older with alerts and match
    scores, Member the moment it is found with everything; each paid card first saying it has everything in
    the card before it, then what it adds, ticked, and crossing out what it lacks, a few words a line with a
    question mark that says more. Starter has no price here, so Member's card follows Free's."""
    http, *_ = api

    lines = http.get("/pricing").json()["lines"]

    assert lines["free"] == {
        "everything_in": None,
        "gives": [{"text": "Jobs that are 7 days or older",
                   "more": "A new job joins your list 7 days after we find it."}, *EVERY],
        "lacks": [MOMENT, said("alerts"), said("match_scores"), said("cvs"), said("covers"), said("add_job")]}
    assert lines["starter"] == {
        "everything_in": "free",
        "gives": [{"text": "Jobs that are 3 days or older",
                   "more": "A new job joins your list 3 days after we find it, 4 days before Free accounts "
                           "see it."},
                  said("alerts"), said("match_scores")],
        "lacks": [MOMENT, said("cvs"), said("covers"), said("add_job")]}
    assert lines["member"] == {
        "everything_in": "free",
        "gives": [MOMENT, said("alerts"), said("match_scores"), said("cvs"), said("covers"), said("add_job")],
        "lacks": []}


def test_once_starter_is_sold_member_has_everything_in_it_and_says_only_what_it_adds(api, monkeypatch):
    """Owner, 2026-11-16: "the top of the starter and member has the 'Everything in Free/Starter plan' so
    people know what we are referring to", and Starter is sold."""
    from services.api import billing_routes
    from services.api.billing_routes import Plan

    http, *_ = api
    monkeypatch.setattr(billing_routes, "plan", lambda db, which="member": Plan(
        amount=299 if which == "starter" else 499, currency="gbp", interval="month", interval_count=1,
        trial_days=7))

    lines = http.get("/pricing").json()["lines"]

    assert (lines["starter"]["everything_in"], lines["member"]["everything_in"]) == ("free", "starter")
    assert lines["member"]["gives"] == [MOMENT, said("cvs"), said("covers"), said("add_job")]


def test_how_soon_each_card_says_follows_the_waits_set_in_admin(api):
    """The waits are the operator's, and the cards say them as they are set: never typed into the words."""
    http, db, *_ = api

    settings.put(db, "free_delay_hours", 120)
    settings.put(db, "starter_delay_hours", 24)
    lines = http.get("/pricing").json()["lines"]
    assert lines["free"]["gives"][0]["text"] == "Jobs that are 5 days or older"
    assert lines["starter"]["gives"][0] == {
        "text": "Jobs that are 24 hours or older",
        "more": "A new job joins your list 24 hours after we find it, 4 days before Free accounts see "
                "it."}
    assert lines["member"]["gives"][0] == MOMENT

    # Nobody waits: Free's card says every job comes the moment it is found, the cards after it have it in
    # "Everything in" and do not say it again, and none crosses it out.
    settings.put(db, "starter_delay_hours", 0)
    settings.put(db, "free_delay_hours", 0)
    lines = http.get("/pricing").json()["lines"]
    assert lines["free"]["gives"][0] == MOMENT
    assert MOMENT not in lines["starter"]["gives"] + lines["member"]["gives"]
    assert not any(MOMENT in lines[plan]["lacks"] for plan in ("free", "starter", "member"))


def test_the_member_card_promises_the_members_chat_only_while_it_has_a_link(api):
    """Owner, 2026-11-16: direct access to the people who build the site is a member's, and never promised
    while there is nothing to open."""
    http, db, *_ = api
    chat = said("team_access")
    said_now = http.get("/pricing").json()["lines"].values()
    assert not any(chat in lines["gives"] + lines["lacks"] for lines in said_now)

    settings.put(db, "members_channel_url", "https://chat.example.com/mahoraga-members")
    lines = http.get("/pricing").json()["lines"]
    assert chat in lines["member"]["gives"]
    assert chat in lines["starter"]["lacks"] and chat in lines["free"]["lacks"]


def ticked(lines: dict, plan: str) -> list[dict]:
    """Everything a card ticks, counting "Everything in" the card before it as that card's ticks."""
    before = lines[plan]["everything_in"]
    return [*(ticked(lines, before) if before else []), *lines[plan]["gives"]]


@pytest.mark.parametrize("sold", [False, True])
@pytest.mark.parametrize("channel", ["", "https://chat.example.com/mahoraga-members"])
@pytest.mark.parametrize("waits", [(168, 72), (120, 0), (0, 0)])
def test_what_each_card_promises_is_what_an_account_on_that_plan_gets(api, monkeypatch, channel, waits, sold):
    """The cards and the site ask the same plans: an account on each plan has every area its card ticks,
    "Everything in" included, and none its card crosses out, and sees new jobs at once exactly when its card
    says so. A card that promised an area its plan does not give, or left out one it does, fails here."""
    from libs.membership import delay_hours, has
    from services.api import billing_routes
    from services.api.billing_routes import Plan

    http, db, *_ = api
    if sold:
        monkeypatch.setattr(billing_routes, "plan", lambda db, which="member": Plan(
            amount=299, currency="gbp", interval="month", interval_count=1, trial_days=7))
    settings.put(db, "members_channel_url", channel)
    settings.put(db, "free_delay_hours", waits[0])
    settings.put(db, "starter_delay_hours", waits[1])
    lines = http.get("/pricing").json()["lines"]

    for plan in ("free", "starter", "member"):
        account, gives, lacks = on(db, plan), ticked(lines, plan), lines[plan]["lacks"]
        for area in AREAS:
            words = said(area)
            if area == "early_jobs":
                assert (words in lacks) == (delay_hours(db, account) > 0), plan
            elif area == "team_access" and not channel:
                assert words not in gives + lacks, plan
            else:
                assert (words in gives, words in lacks) == (has(db, account.id, area),
                                                            not has(db, account.id, area)), (plan, area)
        assert all(line in gives for line in EVERY), f"every account has these, {plan} too"


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
    {"name": " "}, {"button": ""}, {"note": "x" * 281},
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
    settings.put(db, "pricing_cards", {"free": {"name": "Old", "button": ["not a word"]}})

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
        assert cards[name] == {**own, "highlight": default["highlight"]}
    assert cards["starter"] == defaults["starter"]
