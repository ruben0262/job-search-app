"""The daily email of new jobs: on when a person turns it on, once a day at their morning, listing what
was found since the last one — and nothing at all when nothing was."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import get, insert, now
from tests.conftest import needs_db


class Inbox:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, *, reply_to=None):
        self.sent.append({"to": to, "subject": subject, "body": body})


def _person(db, **fields):
    from libs.db.models import User, UserConfig

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    user = insert(db, User(**{"email": f"{uuid.uuid4()}@example.com", "password_hash": "x",
                              "status": "active", "active": True, "email_alerts": True,
                              "created_at": now() - timedelta(days=3), **fields}))
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    return user, word


def _posting(db, title, *, minutes_ago=5):
    from libs.db.models import CorpusJob

    seen = now() - timedelta(minutes=minutes_ago)
    return insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="test", title=title,
                                company="Acme", location="Berlin", description="Build APIs",
                                first_seen_at=seen))


# ── when ──────────────────────────────────────────────────────────────────────────────────────

@needs_db
def test_it_is_due_after_the_hour_of_their_own_day_and_only_once_a_day(db):
    from libs.notify.digest import due

    user, _ = _person(db, timezone="Europe/Budapest")
    nine_in_budapest = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    seven_in_budapest = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)

    assert due(db, user, 8, moment=nine_in_budapest)
    assert not due(db, user, 8, moment=seven_in_budapest), "before their morning"

    user.email_digest_at = now()
    assert not due(db, user, 8, moment=nine_in_budapest), "already had today's"


@needs_db
def test_it_is_never_due_when_turned_off_or_alerts_are_paused(db):
    from libs.notify.digest import due

    off, _ = _person(db, email_alerts=False)
    paused, _ = _person(db, active=False)
    noon = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    assert not due(db, off, 8, moment=noon)
    assert not due(db, paused, 8, moment=noon)


@needs_db
def test_the_sweep_queues_one_email_each_and_the_notifier_sends_it(db, monkeypatch):
    """No clock of its own: the alerts' sweep queues it, and a second sweep before it is sent queues
    nothing more."""
    from libs import settings
    from libs.db.models import QueueJob
    from libs.queue.queue import dispatch_due_digests
    from services.notifier import __main__ as notifier

    settings.put(db, "email_digest_hour", 0)  # whatever the time now, it is past their hour
    user, word = _person(db)
    _posting(db, f"Senior {word}")
    inbox = Inbox()
    monkeypatch.setattr(notifier, "_mailer", lambda: inbox)

    assert dispatch_due_digests(db) == 1
    assert dispatch_due_digests(db) == 0, "one waiting is enough"
    job = notifier.QueueJob.from_doc(db[QueueJob.COLLECTION].find_one({"user_id": user.id}))
    notifier.handle(db, job)

    [mail] = inbox.sent
    assert mail["to"] == user.email and f"Senior {word}" in mail["body"]
    db[QueueJob.COLLECTION].delete_many({})
    assert dispatch_due_digests(db) == 0, "sent today, so not again until tomorrow"


# ── what ──────────────────────────────────────────────────────────────────────────────────────

@needs_db
def test_it_lists_the_jobs_found_since_the_last_one_with_a_link_to_each(db, monkeypatch):
    from libs.notify.digest import send_digest

    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com")
    user, word = _person(db, email_digest_at=now() - timedelta(hours=3))
    _posting(db, f"Old {word}", minutes_ago=4 * 60)
    new = _posting(db, f"New {word}", minutes_ago=30)
    inbox = Inbox()

    assert send_digest(db, inbox, user) == 1

    [mail] = inbox.sent
    assert mail["subject"] == "1 new job for you"
    assert f"New {word} — Acme · Berlin" in mail["body"] and f"Old {word}" not in mail["body"]
    assert f"https://jobs.example.com/#/feed?job={new.id}" in mail["body"]
    assert "https://jobs.example.com/#/settings" in mail["body"], "and how to turn it off"


@needs_db
def test_nothing_new_sends_nothing_but_counts_as_today(db):
    from libs.db.models import User
    from libs.notify.digest import send_digest

    user, _ = _person(db)
    inbox = Inbox()

    assert send_digest(db, inbox, user) == 0

    assert inbox.sent == [], "no 'no new jobs' email"
    assert get(db, User, user.id).email_digest_at is not None


# ── the switch ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    user, _ = _person(db, email_alerts=False, onboarding_completed_at=now())
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http


@needs_db
def test_turning_the_email_on_counts_as_having_alerts(api):
    """The jobs page stops offering alerts to somebody who gets the email."""
    def banners():
        return [banner["key"] for banner in api.get("/dashboard").json()["banners"]]

    assert api.get("/account").json()["email_alerts"] is False
    assert "no_alerts" in banners()

    assert api.patch("/account", json={"email_alerts": True}).json()["email_alerts"] is True

    assert api.get("/account").json()["email_alerts"] is True
    assert "no_alerts" not in banners()
