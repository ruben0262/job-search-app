"""The bell: producing, collapsing, listing, marking read."""
from __future__ import annotations

import logging
import os
import uuid

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


def test_a_persistent_fact_collapses_onto_its_unread_row(api):
    """A broken bot re-detected every delivery cycle is one notification, not one per cycle."""
    from libs import notify

    http, db, user = api
    notify.notify(db, user.id, notify.TELEGRAM_BROKEN, "first detection")
    notify.notify(db, user.id, notify.TELEGRAM_BROKEN, "still broken an hour later")

    body = http.get("/notifications").json()
    assert body["unread"] == 1
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["body"] == "still broken an hour later"
    assert body["notifications"][0]["level"] == "error"


def test_a_read_notification_allows_a_fresh_unread_one(api):
    """"This is still true" collapses; "this happened again since you looked" does not."""
    from libs import notify

    http, db, user = api
    notify.notify(db, user.id, notify.DOCUMENT_NOT_READY, "the first one")
    first = http.get("/notifications").json()["notifications"][0]
    http.post("/notifications/read", json={"ids": [first["id"]]})

    notify.notify(db, user.id, notify.DOCUMENT_NOT_READY, "another one since")

    body = http.get("/notifications").json()
    assert body["unread"] == 1
    assert len(body["notifications"]) == 2


def test_mark_all_read_covers_what_scrolled_past_the_page(api):
    from libs import notify

    http, db, user = api
    for index in range(3):
        notify.notify(db, user.id,
                      notify.Event(f"test.{index}", "info", f"Event {index}"), "")

    assert http.get("/notifications").json()["unread"] == 3
    marked = http.post("/notifications/read", json={"all": True}).json()["marked"]
    assert marked == 3
    assert http.get("/notifications").json()["unread"] == 0


def test_the_bell_is_per_user(api):
    """One user's broken bot is not another's notification."""
    from libs import notify
    from libs.db import insert
    from libs.db.models import User, UserNotification

    http, db, _user = api
    stranger = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    notify.notify(db, stranger.id, notify.TELEGRAM_BROKEN, "their problem")
    assert db[UserNotification.COLLECTION].count_documents({"user_id": stranger.id}) == 1, "the premise"

    assert http.get("/notifications").json()["unread"] == 0


def test_notify_never_raises(api, caplog):
    """A notification about a failure must not become a second failure inside the recovery — and the
    write it could not make is logged rather than lost (CLAUDE.md §8)."""
    from libs import notify
    from libs.db.models import UserNotification

    _, db, user = api
    refused = notify.Event("test.refused", "catastrophic", "A level the collection does not allow")
    with caplog.at_level(logging.WARNING, logger="libs.notify"):
        notify.notify(db, user.id, refused, "should be swallowed")

    assert db[UserNotification.COLLECTION].count_documents({"user_id": user.id}) == 0, \
        "the premise: the database refused the write"
    assert "could not record notification test.refused" in caplog.text


def test_delivery_fetches_nothing_and_so_owns_its_whole_claim():
    """The pre-send liveness refresh moved to the harvester on 2026-08-07 (ROADMAP 6bb)."""
    from pathlib import Path

    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.deliver import SEND_BUDGET_SECONDS

    body = Path("services/notifier/deliver.py").read_text(encoding="utf-8")
    assert "httpx" not in body, "delivery opens no client: sending is the only network it does"

    claim = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim > SEND_BUDGET_SECONDS, (
        f"the send ({SEND_BUDGET_SECONDS}s) must finish inside the {claim}s claim, or the job is "
        "swept back to ready while its worker is still sending and a second worker starts again")


def test_unlimited_still_fits_inside_one_claim():
    """Unlimited means "withhold nothing", not "clear the backlog in one transaction"."""
    from libs.limits import DEFAULT_SEND_GAP_SECONDS
    from services.notifier.deliver import SEND_BUDGET_SECONDS, unlimited_items

    fetched = unlimited_items(DEFAULT_SEND_GAP_SECONDS)

    # Derived from the run's own budget rather than picked, so the two cannot drift.
    assert fetched == int(SEND_BUDGET_SECONDS / DEFAULT_SEND_GAP_SECONDS) + 1
    # Never withhold what the run had time for: fetching fewer than it can send is a cap by accident.
    assert fetched > SEND_BUDGET_SECONDS / DEFAULT_SEND_GAP_SECONDS


def test_the_card_says_what_linkedin_said_about_applicants():
    """The number alone says the opposite of the page."""
    from services.notifier.card import applicant_label

    assert applicant_label(25, "Be among the first 25 applicants").startswith("<25")
    assert applicant_label(200, "Over 200 applicants").startswith("200+")
    assert applicant_label(68, "68 applicants").startswith("68")
    # A row stored before the caption was kept still renders from the number alone.
    assert applicant_label(68).startswith("68")
    assert applicant_label(None) == ""
