"""The Help page's contact form, and the Applied tab: what a person sends and what they get back."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from tests.conftest import needs_db


class Inbox:
    """The mail server, as far as these tests care: what was sent, to whom, and who a reply reaches."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    def send(self, to, subject, body, *, reply_to=None, html=None):
        from libs.mail import MailError

        if self.fail:
            raise MailError("the mail server said no")
        self.sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active"))
    inbox = Inbox()
    app.dependency_overrides[deps.get_mailer] = lambda: inbox

    def as_user(person):
        http.cookies.set("access_token", issue_access_token(person.id, uuid.uuid4()), path="/")

    with TestClient(app, base_url="https://testserver") as http:
        as_user(user)
        yield http, db, user, inbox, as_user
    app.dependency_overrides.clear()


def _admin(db):
    from libs.db import insert
    from libs.db.models import User

    return insert(db, User(email=f"admin-{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active",
                           is_admin=True))


# ── contact ───────────────────────────────────────────────────────────────────────────────────

@needs_db
def test_a_message_reaches_the_support_inbox_and_a_reply_reaches_the_person(api):
    http, db, user, inbox, as_user = api

    sent = http.post("/contact", json={"subject": "My CV has the wrong name",
                                       "message": "It says Jane but I am Janet. How do I change it?"})

    assert sent.status_code == 202
    receipt = sent.json()
    assert receipt["email"] == user.email and receipt["reference"]
    (mail,) = inbox.sent
    assert mail["to"] == http.get("/contact").json()["email"] == "hello@flow-solutions.xyz"
    assert mail["reply_to"] == user.email, "the reply goes to the person, not to no-reply"
    assert receipt["reference"] in mail["subject"] and "I am Janet" in mail["body"]

    as_user(_admin(db))
    (row,) = http.get("/admin/contact").json()
    assert (row["reference"], row["email"], row["emailed"]) == (receipt["reference"], user.email, True)


@needs_db
def test_a_message_the_email_could_not_carry_is_still_kept_for_the_admin(api):
    http, db, _user, inbox, as_user = api
    inbox.fail = True

    sent = http.post("/contact", json={"subject": "Hello", "message": "Is anybody reading these?"})

    assert sent.status_code == 202, "the person is not told to try again: their message is kept"
    as_user(_admin(db))
    (row,) = http.get("/admin/contact").json()
    assert row["subject"] == "Hello" and row["emailed"] is False


@needs_db
def test_the_support_address_is_the_operators_to_change(api):
    from libs import settings

    http, db, _user, inbox, _as_user = api
    settings.put(db, "support_email", "support@example.org")

    assert http.get("/contact").json()["email"] == "support@example.org"
    http.post("/contact", json={"subject": "Hello", "message": "Where does this one go?"})
    assert inbox.sent[0]["to"] == "support@example.org"


@needs_db
def test_only_an_admin_reads_the_messages(api):
    http, *_ = api
    http.post("/contact", json={"subject": "Private", "message": "Something only staff should read."})
    assert http.get("/admin/contact").status_code == 404


@needs_db
def test_an_empty_or_one_word_message_is_refused(api):
    http, *_ = api
    assert http.post("/contact", json={"subject": "Hi", "message": "help"}).status_code == 422


# ── applied ───────────────────────────────────────────────────────────────────────────────────

def _job(db, title: str):
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    return insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="test", title=title,
                                company="Acme", location="Berlin", first_seen_at=now()))


@needs_db
def test_applied_lists_every_job_applied_to_newest_first_until_taken_back(api):
    """Including one that no longer matches their words: an application does not stop existing."""
    from libs.db import now
    from libs.db.models import UserJobState

    http, db, _user, _inbox, _as_user = api
    older, newer = _job(db, "Night Nurse"), _job(db, "Backend Engineer")
    assert http.get("/applied").json() == []

    http.put(f"/jobs/{older.id}/applied")
    http.put(f"/jobs/{newer.id}/applied")
    db[UserJobState.COLLECTION].update_one({"job_id": older.id},
                                           {"$set": {"applied_at": now() - timedelta(days=2)}})

    listed = http.get("/applied").json()
    assert [job["title"] for job in listed] == ["Backend Engineer", "Night Nurse"]
    assert listed[0]["url"] == newer.url

    http.delete(f"/jobs/{newer.id}/applied")
    assert [job["title"] for job in http.get("/applied").json()] == ["Night Nurse"]


@needs_db
def test_another_persons_applications_are_not_listed(api):
    from libs.db import insert
    from libs.db.models import User

    http, db, _user, _inbox, as_user = api
    http.put(f"/jobs/{_job(db, 'Backend Engineer').id}/applied")
    as_user(insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                            subscription_status="active", status="active")))
    assert http.get("/applied").json() == []
