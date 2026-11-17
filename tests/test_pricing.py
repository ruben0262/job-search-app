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
from libs.membership import AREAS, PLANS

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"
CARD = {"name": "Mahoraga Plus", "highlight": "Yours for life", "button": "Try it free",
        "note": "Cancel any time."}
FREE = {**CARD, "name": "Free", "highlight": "", "button": "Start free"}
CARDS = {"free": FREE, "starter": {**CARD, "name": "First steps"}, "member": CARD}


LINES = settings.DEFAULT_PRICING_LINES
# Everything the operator words, as Admin saves it.
WORDS = {"cards": CARDS, "lines": LINES}


def said(area: str) -> list[dict]:
    """An area's lines as the cards say them: each its words, and what its question mark says."""
    return LINES["areas"][area]


EVERY = LINES["everyone"]
MOMENT = LINES["now"]
# A CV, a cover letter, and the ATS check every one of them gets: a line each (owner, 2026-11-16).
DOCUMENTS = [*said("cvs"), *said("covers"), *said("ats_checks")]


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
        "gives": [{"text": "See new jobs after 7 days",
                   "more": "A new job joins your list 7 days after we find it."}, *EVERY],
        "lacks": [MOMENT, *said("alerts"), *said("match_scores"), *DOCUMENTS, *said("add_job")]}
    assert lines["starter"] == {
        "everything_in": "free",
        "gives": [{"text": "See new jobs after 3 days",
                   "more": "A new job joins your list 3 days after we find it."},
                  *said("alerts"), *said("match_scores")],
        "lacks": [MOMENT, *DOCUMENTS, *said("add_job")]}
    assert lines["member"] == {
        "everything_in": "free",
        "gives": [MOMENT, *said("alerts"), *said("match_scores"), *DOCUMENTS, *said("add_job")],
        "lacks": []}
    # Owner, 2026-11-17: "we should offer more in the starter than the free". Each paid card ticks more than
    # the card before it, counting its "Everything in" line.
    ticks = [len(lines[plan]["gives"]) + (lines[plan]["everything_in"] is not None)
             for plan in ("free", "starter")]
    assert ticks[0] < ticks[1], ticks


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
    assert lines["member"]["gives"] == [MOMENT, *DOCUMENTS, *said("add_job")]
    assert len(lines["starter"]["gives"]) < len(lines["member"]["gives"]), "Member ticks the most"


def test_how_soon_each_card_says_follows_the_waits_set_in_admin(api):
    """The waits are the operator's, and the cards say them as they are set: never typed into the words."""
    http, db, *_ = api

    settings.put(db, "free_delay_hours", 120)
    settings.put(db, "starter_delay_hours", 24)
    lines = http.get("/pricing").json()["lines"]
    assert lines["free"]["gives"][0]["text"] == "See new jobs after 5 days"
    assert lines["starter"]["gives"][0] == {
        "text": "See new jobs after 24 hours", "more": "A new job joins your list 24 hours after we find it."}
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
    [chat] = said("team_access")
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
            if area == "early_jobs":
                assert (MOMENT in lacks) == (delay_hours(db, account) > 0), plan
                continue
            for words in said(area):
                if area == "team_access" and not channel:
                    assert words not in gives + lacks, plan
                else:
                    assert (words in gives, words in lacks) == (has(db, account.id, area),
                                                                not has(db, account.id, area)), (plan, area)
        assert all(line in gives for line in EVERY), f"every account has these, {plan} too"


def test_the_ats_check_is_promised_exactly_where_documents_are_made():
    """Owner, 2026-11-16: the ATS check is a line of its own, "because we do use them in the backend for cv
    and cover checks". Nothing asks for it by name: every CV and cover letter is checked as it is made
    (`services.docgen.pdf.inspect_pdf`). So a plan has it exactly when it makes one or the other, or a card
    would promise a check nobody gets, or cross out one everybody making a document gets."""
    for plan, areas in PLANS.items():
        assert ("ats_checks" in areas) == ("cvs" in areas or "covers" in areas), plan


def test_an_admin_changes_what_the_cards_say_and_the_landing_page_shows_it(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    padded = {**CARDS, "member": {**CARD, "name": "  Mahoraga Plus  "}}
    saved = http.put("/admin/pricing", json={"cards": padded, "lines": LINES})

    assert saved.status_code == 200 and saved.json()["cards"] == CARDS, "kept trimmed"
    http.cookies.clear()
    assert http.get("/pricing").json()["cards"] == CARDS


def test_nobody_else_can_change_it(api):
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.put("/admin/pricing", json=WORDS).status_code == 404
    assert http.get("/admin/pricing").status_code == 404
    assert http.post("/admin/pricing/preview", json=WORDS).status_code == 404
    assert http.get("/pricing").json()["cards"] == settings.DEFAULT_PRICING_CARDS


@pytest.mark.parametrize("change", [
    {"name": " "}, {"button": ""}, {"note": "x" * 281},
])
def test_a_card_the_page_has_no_room_for_is_refused_and_the_saved_ones_kept(api, change):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    unfit = {**CARDS, "free": {**FREE, **change}}
    assert http.put("/admin/pricing", json={**WORDS, "cards": unfit}).status_code == 422
    assert http.put("/admin/pricing", json={**WORDS, "cards": {"member": CARD}}).status_code == 422, (
        "every card, always")
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


def test_an_admin_words_the_lines_and_each_card_ticks_or_crosses_them_by_its_plan(api):
    """Owner, 2026-11-17: every line on the cards is in Admin, "so its not hardcoded". The words are the
    operator's; which card ticks a line and which crosses it out stays with the plans, so a line about alerts
    is ticked where alerts are given and crossed out where they are not, whatever it says."""
    http, _, operator, _, sign_in = api
    sign_in(http, operator)
    alerts = [{"text": "Alerts on Telegram", "more": "A message for each new job."},
              {"text": "A daily email", "more": "Every morning, the jobs that came in."}]
    everyone = [*EVERY, {"text": "Search in any language", "more": "Words in any language find jobs."}]
    wording = {**LINES, "everyone": everyone, "areas": {**LINES["areas"], "alerts": alerts}}

    assert http.put("/admin/pricing", json={"cards": CARDS, "lines": wording}).status_code == 200
    http.cookies.clear()
    lines = http.get("/pricing").json()["lines"]

    assert all(line in lines["starter"]["gives"] and line in lines["free"]["lacks"] for line in alerts)
    assert lines["free"]["gives"][1:] == everyone
    assert not any(line in lines["starter"]["gives"] for line in everyone), "Everything in Free says them"
    sign_in(http, operator)
    assert http.get("/admin/pricing").json()["lines"] == wording, "Admin shows what is saved"


def test_admin_shows_every_word_filled_in_and_where_each_area_is_ticked(api):
    """Nothing to type in from scratch (owner, 2026-11-17: "i dont have to type it in"): before anything is
    saved, the editor has every card's words and every line, and says which plans tick each area and what
    {wait} becomes on each card."""
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/pricing").json()

    assert (body["cards"], body["lines"]) == (settings.DEFAULT_PRICING_CARDS, LINES)
    assert body["ticked_on"]["alerts"] == ["starter", "member"]
    assert body["ticked_on"]["ats_checks"] == ["member"]
    assert "early_jobs" not in body["ticked_on"], "said by the wait and now lines"
    assert body["waits"] == {"free": "7 days", "starter": "3 days"}


@pytest.mark.parametrize("change", [
    {"wait": {"text": "See new jobs a week later", "more": "A week after we find it."}},
    {"everyone": []},
    {"now": {"text": "", "more": "Soon."}},
    {"areas": {**LINES["areas"], "alerts": []}},
    {"areas": {key: value for key, value in LINES["areas"].items() if key != "add_job"}},
    {"areas": {**LINES["areas"], "free_lunch": [{"text": "Lunch", "more": "Every day."}]}},
])
def test_lines_the_cards_cannot_say_truly_are_refused_and_nothing_saved(api, change):
    """A wait typed as a number would go stale when the wait changes, so its line must say {wait}; a kind of
    line left empty, or an area missing or unknown, would make a card say less or more than its plan."""
    http, db, operator, _, sign_in = api
    sign_in(http, operator)

    assert http.put("/admin/pricing", json={"cards": CARDS, "lines": {**LINES, **change}}).status_code == 422
    assert settings.get(db, "pricing_lines") == LINES


def test_the_preview_shows_the_words_and_saves_nothing(api):
    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    now = {"text": "Every job on day one", "more": "The moment we find it."}

    asked = {"cards": CARDS, "lines": {**LINES, "now": now}}
    preview = http.post("/admin/pricing/preview", json=asked).json()

    assert preview["lines"]["member"]["gives"][0] == now and preview["cards"] == CARDS
    assert http.get("/pricing").json()["lines"]["member"]["gives"][0] == MOMENT
    assert settings.get(db, "pricing_lines") == LINES


def test_saved_lines_that_no_longer_read_are_the_defaults_and_the_log_says_so(api, caplog):
    """Saved some other way: the page shows the defaults, never an error. An area there were no lines for
    when they were saved is said in its default words."""
    http, db, *_ = api
    settings.put(db, "pricing_lines", {**LINES, "wait": {"text": "No wait here", "more": "None."}})
    with caplog.at_level(logging.WARNING, logger="services.api.pricing_routes"):
        assert http.get("/pricing").json()["lines"]["free"]["gives"][0]["text"] == "See new jobs after 7 days"
    assert "the saved lines do not read" in caplog.text

    older = {**LINES, "areas": {key: value for key, value in LINES["areas"].items() if key != "ats_checks"}}
    settings.put(db, "pricing_lines", older)
    assert all(line in http.get("/pricing").json()["lines"]["member"]["gives"] for line in said("ats_checks"))
