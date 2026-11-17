"""The daily email: once a day at their morning for somebody with an alert that sends to it, listing what each
such alert found since the last one under its name (owner, 2026-11-17) — and nothing at all when nothing
was."""
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

    def send(self, to, subject, body, *, reply_to=None, html=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "html": html})


def _person(db, *, emailed=True, **fields):
    """Somebody with one green word of their own and My search, sending to the email unless `emailed` is
    False."""
    from libs.db.models import Alert, User, UserConfig

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    user = insert(db, User(**{"email": f"{uuid.uuid4()}@example.com", "password_hash": "x",
                              "subscription_status": "active", "status": "active", "active": True,
                              "created_at": now() - timedelta(days=3), **fields}))
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    insert(db, Alert(user_id=user.id, name="My search", search=True, created_at=user.created_at,
                     channels=["bell", *(["email"] if emailed else [])]))
    return user, word


def _alert(db, user, name, *, emailed=True, **fields):
    from libs.db.models import Alert

    return insert(db, Alert(user_id=user.id, name=name, channels=["bell", *(["email"] if emailed else [])],
                            created_at=user.created_at, **fields))


def _found(db, user, title, *, alerts=None, minutes_ago=5):
    """A job one or more of their alerts found `minutes_ago` (`libs.alerts.route`): My search unless said."""
    from libs.corpus.words import terms_of
    from libs.db.models import Alert, CorpusJob, UserJobState

    seen = now() - timedelta(minutes=minutes_ago)
    theirs = db[Alert.COLLECTION].find({"user_id": user.id})
    words = sorted({word for alert in theirs for word in alert["keywords"]} | {title.split()[-1]})
    job = insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="test", title=title,
                               company="Acme", location="Berlin", description="Build APIs",
                               first_seen_at=seen, described_at=seen, **terms_of(title, "Build APIs", words)))
    ids = alerts or db[Alert.COLLECTION].distinct("_id", {"user_id": user.id, "search": True})
    insert(db, UserJobState(user_id=user.id, job_id=job.id, alerted_at=seen, alerts=list(ids)))
    return job


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
def test_nobody_without_an_emailing_alert_or_with_alerts_paused_is_queued(db, monkeypatch):
    from libs import settings
    from libs.db.models import QueueJob
    from libs.queue.queue import dispatch_due_digests

    settings.put(db, "email_digest_hour", 0)
    _person(db, emailed=False)
    _person(db, active=False)
    emailed, _ = _person(db)

    assert dispatch_due_digests(db) == 1
    assert db[QueueJob.COLLECTION].distinct("user_id") == [emailed.id]


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
    _found(db, user, f"Senior {word}")
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
def test_it_lists_what_was_found_since_the_last_one_with_a_link_to_each(db, monkeypatch):
    from libs.notify.digest import send_digest

    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com")
    user, word = _person(db, email_digest_at=now() - timedelta(hours=3))
    _found(db, user, f"Old {word}", minutes_ago=4 * 60)
    new = _found(db, user, f"New {word}", minutes_ago=30)
    inbox = Inbox()

    assert send_digest(db, inbox, user) == 1

    [mail] = inbox.sent
    assert mail["subject"] == "1 new job for you"
    assert f"New {word} — Acme · Berlin" in mail["body"] and f"Old {word}" not in mail["body"]
    assert f"https://jobs.example.com/#/feed?job={new.id}" in mail["body"]
    assert "https://jobs.example.com/#/alerts" in mail["body"], "and where to change it"


@needs_db
def test_each_alert_lists_its_own_under_its_name_and_one_without_the_email_is_left_out(db):
    """Grouped by alert (owner, 2026-11-17): one email a day, each alert's jobs under its name."""
    from libs.notify.digest import send_digest

    user, word = _person(db)
    lego = _alert(db, user, "LEGO", companies=["LEGO"])
    quiet = _alert(db, user, "Quiet", emailed=False, keywords=[word])
    _found(db, user, f"Senior {word}")
    _found(db, user, "Brick Designer", alerts=[lego.id])
    _found(db, user, "Hidden", alerts=[quiet.id])
    inbox = Inbox()

    assert send_digest(db, inbox, user) == 2

    body = inbox.sent[0]["body"]
    assert body.index("MY SEARCH") < body.index(f"Senior {word}") < body.index("LEGO") < body.index("Brick")
    assert "Hidden" not in body and "QUIET" not in body


@needs_db
def test_its_designed_version_lists_each_job_linked_with_the_words_that_found_it(db, monkeypatch):
    """The email as most people see it: each job a link to it on the site under its alert, with the words of
    theirs it has, and a title that is markup shown as words, not run."""
    from libs.notify.digest import send_digest

    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com")
    user, word = _person(db)
    job = _found(db, user, f"<b>Lead</b> {word}")
    inbox = Inbox()

    assert send_digest(db, inbox, user) == 1

    [mail] = inbox.sent
    html = mail["html"]
    assert f'href="https://jobs.example.com/#/feed?job={job.id}"' in html
    assert f"&lt;b&gt;Lead&lt;/b&gt; {word}" in html and "<b>Lead</b>" not in html
    assert f">{word}</span>" in html, "the word that found it"
    assert ">My search</td>" in html, "under the alert that found it"
    assert "Acme · Berlin" in html and 'href="https://jobs.example.com/#/alerts"' in html


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
    from libs.db.models import UserConfig
    from services.api.app import app

    user, word = _person(db, emailed=False, onboarding_completed_at=now(), full_name="Ada Example")
    # A finished account's search: the jobs page waits for one missing a part.
    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": {
        "languages": ["en"], "sections": [{"platform": "all", "keywords": [word, "golang", "sql"],
                                           "locations": ["germany"]}]}}})
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http


@needs_db
def test_an_alert_with_the_email_counts_as_having_alerts(api):
    """The jobs page stops offering alerts to somebody whose alert sends the email."""
    def banners():
        return [banner["key"] for banner in api.get("/dashboard").json()["banners"]]

    assert "no_alerts" in banners()
    [mine] = api.get("/alerts").json()["alerts"]

    changed = {key: mine[key] for key in ("name", "search", "keywords", "companies", "places", "sources",
                                          "min_fit", "on")}
    emailed = {**changed, "channels": ["bell", "email"]}
    assert api.put(f"/alerts/{mine['id']}", json=emailed).status_code == 200

    assert "no_alerts" not in banners()
