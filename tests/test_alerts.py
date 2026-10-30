"""Alerts: new matching jobs reach a person off the site, through one platform bot and their browsers.

Connecting either is one tap: a `t.me` link and Start, or allowing notifications. What is sent is
`libs.notify.owed` — the person's `libs.matching` matches first seen after the newest one already sent.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import timedelta

import pytest

from libs.db import get, insert, now
from tests.conftest import needs_db

pytest.importorskip("pywebpush")

SECRET = "webhook-secret-" + "w" * 24


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """The gap between Telegram messages is Telegram's rule; here it would only make the suite slow."""
    from services.notifier import deliver

    monkeypatch.setattr(deliver, "sleep", lambda _seconds: None)


def _person(db, *, telegram=True, browsers=0, joined_minutes_ago=24 * 60):
    """Somebody with one green word of their own, so no other test's posting can match them."""
    from libs.db.models import PushSubscription, User, UserConfig

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           active=True, telegram_status="connected" if telegram else "unconfigured",
                           telegram_chat_id="555" if telegram else None,
                           created_at=now() - timedelta(minutes=joined_minutes_ago)))
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    # Subscribed since they joined: when a browser subscribed is when its alerts start.
    for _ in range(browsers):
        insert(db, PushSubscription(user_id=user.id, endpoint=f"https://push.example.com/{uuid.uuid4()}",
                                    p256dh="k", auth="a", created_at=user.created_at))
    return user, word


def _posting(db, title, *, minutes_ago=5):
    from libs.db.models import CorpusJob

    seen = now() - timedelta(minutes=minutes_ago)
    return insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="test", title=title,
                                company="Acme", description="Build APIs", is_remote=True, posted_at=seen,
                                first_seen_at=seen))


def _notified(db, user) -> set[int]:
    from libs.db.models import UserJobState

    return set(db[UserJobState.COLLECTION].distinct(
        "job_id", {"user_id": user.id, "notified_at": {"$ne": None}}))


def _endpoints(db, user) -> list[str]:
    from libs.db.models import PushSubscription

    return sorted(db[PushSubscription.COLLECTION].distinct("endpoint", {"user_id": user.id}))


def _account(db, user):
    """The account as it is stored now: what a test changed through the API or the notifier."""
    from libs.db.models import User

    return get(db, User, user.id)


class Outbox:
    """Both transports, recording what each was asked to send."""

    def __init__(self, gone=()):
        self.telegram, self.browsers, self.gone = [], [], set(gone)

    def tg(self, chat, text, keyboard):
        self.telegram.append((chat, text, keyboard))

    def push(self, subscription, payload):
        if subscription.endpoint in self.gone:
            return False
        self.browsers.append((subscription.endpoint, payload))
        return True


def run(db, user, outbox, **transports):
    from services.notifier.deliver import deliver

    return deliver(db, user.id, telegram=transports.get("telegram", outbox.tg),
                   push=transports.get("push", outbox.push))


# ── what is sent ──────────────────────────────────────────────────────────────────────────────

@needs_db
def test_a_new_match_reaches_telegram_and_every_browser_once(db):
    from libs.notify import site_url

    user, word = _person(db, browsers=2)
    match = _posting(db, f"Senior {word}")
    _posting(db, "Registered Nurse")
    outbox = Outbox()

    assert run(db, user, outbox) == 1

    [(chat, text, keyboard)] = outbox.telegram
    assert chat == "555"
    assert f"Senior {word}" in text and f"Matches: {word}" in text
    # Plain links, so a tap opens something rather than waiting on a webhook.
    page = site_url(f"/#/feed?job={match.id}")
    assert [(b["text"], b["url"]) for b in keyboard["inline_keyboard"][0]] == [
        ("Open job", match.url), ("Make my CV", page)]
    assert [payload["url"] for _, payload in outbox.browsers] == [page, page]
    assert _notified(db, user) == {match.id}
    assert run(db, user, Outbox()) == 0, "sent once, and never again"


@needs_db
def test_only_jobs_first_seen_after_the_newest_one_sent_go_out(db):
    from libs.db.models.tenant import record_state

    user, word = _person(db)
    sent = _posting(db, word, minutes_ago=60)
    record_state(db, user.id, sent.id, notified_at=now())
    _posting(db, word, minutes_ago=90)
    newer = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())

    assert _notified(db, user) == {sent.id, newer.id}


@needs_db
def test_somebody_never_sent_anything_gets_what_was_found_since_they_joined(db):
    """Not the backlog the jobs page already shows them."""
    user, word = _person(db, joined_minutes_ago=30)
    _posting(db, word, minutes_ago=60)
    new = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())

    assert _notified(db, user) == {new.id}


@needs_db
def test_alerts_start_when_telegram_is_connected_not_when_they_joined(db):
    """Turning alerts on weeks after joining must not fire every match still in its window at once."""
    from libs.db.models import AuthToken

    user, word = _person(db, joined_minutes_ago=3 * 24 * 60)
    _posting(db, word, minutes_ago=5 * 60)  # inside every freshness window, before they connected
    insert(db, AuthToken(id=uuid.uuid4().hex, user_id=user.id, purpose="telegram_connect",
                         expires_at=now(), used_at=now() - timedelta(hours=1)))
    new = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())

    assert _notified(db, user) == {new.id}


@needs_db
def test_alerts_start_when_the_browser_subscribed(db):
    from libs.db.models import PushSubscription

    user, word = _person(db, telegram=False, joined_minutes_ago=3 * 24 * 60)
    _posting(db, word, minutes_ago=5 * 60)  # inside every freshness window, before they connected
    insert(db, PushSubscription(user_id=user.id, endpoint=f"https://push.example.com/{uuid.uuid4()}",
                                p256dh="k", auth="a", created_at=now() - timedelta(hours=1)))
    new = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())

    assert _notified(db, user) == {new.id}


@needs_db
def test_a_backlog_bigger_than_the_scan_is_skipped_out_loud_not_silently(db, monkeypatch, caplog):
    """`owed` sends oldest first from what one scan saw, then the watermark passes it; anything older
    the scan never reached is never alerted. After a long outage that is the right trade — the jobs
    page still lists them — but it must be said, with the account and the count, not happen quietly."""
    from libs import notify

    monkeypatch.setattr(notify, "NOTIFY_SCAN", 2)
    user, word = _person(db, joined_minutes_ago=60)
    for minutes in (30, 20, 10):
        _posting(db, word, minutes_ago=minutes)

    with caplog.at_level("WARNING"):
        assert len(notify.owed(db, user)) == 2

    assert any(str(user.id) in record.getMessage() and record.levelname == "WARNING"
               for record in caplog.records), "a backlog the scan cut off must be logged"


@needs_db
def test_the_daily_cap_holds_the_rest_for_tomorrow(db):
    from libs import limits
    from libs.notify import owed

    user, word = _person(db)
    limits.set_limit(db, user.id, "notifications_per_day", 1)
    first = _posting(db, word, minutes_ago=20)
    second = _posting(db, word, minutes_ago=10)

    assert run(db, user, Outbox()) == 1
    assert _notified(db, user) == {first.id}, "oldest first"
    assert run(db, user, Outbox()) == 0
    assert [match.job.id for match in owed(db, user)] == [second.id], "held, not dropped"


@needs_db
def test_with_no_cap_a_run_sends_everything_owed(db):
    user, word = _person(db)
    for index in range(14):
        _posting(db, word, minutes_ago=30 - index)

    assert run(db, user, Outbox()) == 14


# ── when a channel fails ──────────────────────────────────────────────────────────────────────

@needs_db
def test_a_browser_that_has_gone_is_deleted_and_the_others_still_get_the_job(db):
    user, word = _person(db, telegram=False, browsers=2)
    job = _posting(db, word)
    gone, kept = _endpoints(db, user)
    outbox = Outbox(gone={gone})

    assert run(db, user, outbox) == 1
    assert [endpoint for endpoint, _ in outbox.browsers] == [kept]
    assert _endpoints(db, user) == [kept]
    assert _notified(db, user) == {job.id}


@needs_db
def test_a_blocked_bot_marks_telegram_broken_and_the_browser_still_gets_the_job(db):
    from libs.db.models import UserNotification

    user, word = _person(db, browsers=1)
    job = _posting(db, word)
    outbox = Outbox()

    def blocked(chat, text, keyboard):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    assert run(db, user, outbox, telegram=blocked) == 1
    assert _account(db, user).telegram_status == "broken"
    assert db[UserNotification.COLLECTION].distinct("event", {"user_id": user.id}) == ["telegram.broken"]
    assert len(outbox.browsers) == 1
    assert _notified(db, user) == {job.id}


@needs_db
def test_a_telegram_outage_is_retried_rather_than_disconnecting(db):
    """A bad minute at Telegram must not cost somebody their connection."""
    user, word = _person(db)
    _posting(db, word)

    def down(chat, text, keyboard):
        raise RuntimeError("Telegram answered 3 server error(s) in a row")

    with pytest.raises(RuntimeError):
        run(db, user, Outbox(), telegram=down)
    assert _account(db, user).telegram_status == "connected"
    assert _notified(db, user) == set()


@needs_db
def test_a_run_that_fails_partway_sends_nothing_twice(db):
    user, word = _person(db)
    jobs = [_posting(db, word, minutes_ago=50 - index) for index in range(5)]
    outbox = Outbox()

    def fourth_fails(chat, text, keyboard):
        if len(outbox.telegram) == 3:
            raise RuntimeError("Bad Gateway")
        outbox.tg(chat, text, keyboard)

    with pytest.raises(RuntimeError):
        run(db, user, outbox, telegram=fourth_fails)
    # Each send was recorded the moment it went, so the failure keeps the three that did.
    assert _notified(db, user) == {job.id for job in jobs[:3]}

    assert run(db, user, Outbox()) == 2, "only the two that never went"
    assert _notified(db, user) == {job.id for job in jobs}


@needs_db
def test_a_run_stops_before_its_claim_expires_and_queues_the_rest(db, monkeypatch):
    import time

    from libs import limits
    from libs.db.models import QueueJob
    from libs.notify import owed
    from services.notifier import deliver as deliver_module

    user, word = _person(db)
    limits.set_limit(db, user.id, "items_per_run", 50)
    for index in range(10):
        _posting(db, word, minutes_ago=30 - index)
    monkeypatch.setattr(deliver_module, "SEND_BUDGET_SECONDS", 0.05)
    outbox = Outbox()

    def slow(chat, text, keyboard):
        outbox.tg(chat, text, keyboard)
        time.sleep(0.04)

    sent = run(db, user, outbox, telegram=slow)

    assert 0 < sent < 10
    assert len(owed(db, user)) == 10 - sent, "what did not go is still owed, not lost"
    assert db[QueueJob.COLLECTION].count_documents(
        {"lane": "notify", "user_id": user.id, "state": "ready"}) == 1


@needs_db
def test_a_notifier_without_the_bot_token_still_reaches_browsers_and_says_so(db, caplog):
    user, word = _person(db, browsers=1)
    _posting(db, word)
    outbox = Outbox()

    with caplog.at_level(logging.WARNING):
        assert run(db, user, outbox, telegram=None) == 1

    assert outbox.telegram == [] and len(outbox.browsers) == 1
    assert any("TELEGRAM_BOT_TOKEN" in record.getMessage() for record in caplog.records)


def test_the_push_transport_reads_404_and_410_as_gone(monkeypatch):
    """The only answer that deletes a subscription. Anything else is a fault and is raised."""
    from types import SimpleNamespace

    from pywebpush import WebPushException

    from services.notifier import __main__ as notifier

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "unused-by-the-stub")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:ops@example.com")
    browser = SimpleNamespace(endpoint="https://push.example.com/x", p256dh="k", auth="a")
    alert = {"title": "Backend Engineer", "body": "Acme", "url": "https://jobs.example.com/#/feed?job=1"}
    seen = {}

    def answering(status):
        def webpush(subscription_info, data, **_kwargs):
            seen.update(keys=subscription_info["keys"], alert=json.loads(data))
            if status >= 400:
                raise WebPushException("push failed", response=SimpleNamespace(status_code=status))
        return webpush

    monkeypatch.setattr(notifier, "webpush", answering(201))
    assert notifier.push(browser, alert) is True
    assert seen == {"keys": {"p256dh": "k", "auth": "a"}, "alert": alert}
    for status in (404, 410):
        monkeypatch.setattr(notifier, "webpush", answering(status))
        assert notifier.push(browser, alert) is False
    monkeypatch.setattr(notifier, "webpush", answering(500))
    with pytest.raises(WebPushException):
        notifier.push(browser, alert)


# ── connecting, through the API ───────────────────────────────────────────────────────────────

@pytest.fixture()
def api(monkeypatch, db):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "jobs_test_bot")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BPublicKey")
    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


def say(http, text, *, chat=4242, secret=SECRET):
    """A message to the bot, as Telegram posts it to the webhook."""
    return http.post("/telegram/webhook",
                     headers={"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {},
                     json={"update_id": 1, "message": {"message_id": 1, "text": text,
                                                       "chat": {"id": chat, "type": "private"}}})


def _code(http) -> str:
    return http.post("/telegram/link").json()["url"].split("start=")[1]


@needs_db
def test_telegram_connects_with_one_link_and_one_press_of_start(api):
    http, db, user = api
    link = http.post("/telegram/link").json()["url"]
    assert link.startswith("https://t.me/jobs_test_bot?start=")

    reply = say(http, "/start " + link.split("start=")[1]).json()

    account = _account(db, user)
    assert (account.telegram_status, account.telegram_chat_id) == ("connected", "4242")
    # Carried in the answer to Telegram's own call, which is how it reaches the chat.
    assert (reply["method"], reply["chat_id"]) == ("sendMessage", 4242)
    assert "Connected" in reply["text"]


@needs_db
def test_the_webhook_refuses_a_call_without_the_platforms_secret(api, caplog):
    http, db, user = api
    code = _code(http)

    with caplog.at_level(logging.WARNING):
        assert say(http, f"/start {code}", secret=None).status_code == 404
        assert say(http, f"/start {code}", secret="not-the-secret").status_code == 404

    assert _account(db, user).telegram_status == "unconfigured"
    assert any("secret header" in record.getMessage() for record in caplog.records)


@needs_db
def test_a_code_works_once_and_nobody_else_can_reuse_it(api):
    http, db, user = api
    code = _code(http)
    say(http, f"/start {code}")

    stranger = say(http, f"/start {code}", chat=9999).json()

    assert _account(db, user).telegram_chat_id == "4242"
    assert "expired" in stranger["text"]
    # Tapping your own link twice is not a failure, and is not told it is one.
    assert "already connected" in say(http, f"/start {code}").json()["text"]


@needs_db
def test_anything_else_sent_to_the_bot_points_at_the_site(api):
    http, _, _ = api
    assert "https://jobs.example.com" in say(http, "hello").json()["text"]


@needs_db
def test_disconnecting_stops_telegram(api):
    from libs.db.models import User

    http, db, user = api
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"telegram_status": "connected",
                                                                "telegram_chat_id": "4242"}})

    assert http.delete("/telegram/link").json() == {"status": "unconfigured"}
    account = _account(db, user)
    assert (account.telegram_status, account.telegram_chat_id) == ("unconfigured", None)


@needs_db
def test_connecting_says_so_when_the_site_has_no_bot(api, monkeypatch):
    http, _, _ = api
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME")

    response = http.post("/telegram/link")
    assert response.status_code == 503
    assert "not set up" in response.json()["detail"]


@needs_db
def test_a_browser_subscribes_once_and_can_unsubscribe(api):
    from libs.db.models import PushSubscription

    http, db, user = api
    assert http.get("/push/key").json() == {"key": "BPublicKey"}
    browser = {"endpoint": f"https://push.example.com/{uuid.uuid4()}", "expirationTime": None,
               "keys": {"p256dh": "BKey", "auth": "secret"}}

    # The same browser twice is one subscription.
    assert http.post("/push/subscribe", json=browser).status_code == 200
    assert http.post("/push/subscribe", json=browser).status_code == 200
    assert _endpoints(db, user) == [browser["endpoint"]]
    assert db[PushSubscription.COLLECTION].count_documents({"endpoint": browser["endpoint"]}) == 1

    assert http.request("DELETE", "/push/subscribe",
                        json={"endpoint": browser["endpoint"]}).status_code == 200
    assert _endpoints(db, user) == []


@needs_db
def test_a_push_endpoint_has_to_be_https(api):
    """The notifier posts to it, so an address inside the network is refused at the door."""
    http, _, _ = api
    response = http.post("/push/subscribe", json={"endpoint": "http://169.254.169.254/latest",
                                                  "keys": {"p256dh": "k", "auth": "a"}})
    assert response.status_code == 422


def test_the_webhook_script_points_telegram_at_the_route_the_api_serves(monkeypatch):
    """Registered once on the box. A wrong address here is a bot that never hears a /start."""
    import importlib.util
    from pathlib import Path

    import httpx

    path = Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "set_telegram_webhook.py"
    spec = importlib.util.spec_from_file_location("set_telegram_webhook", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com/")
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs["json"])
        return httpx.Response(200, json={"ok": True, "result": True})

    monkeypatch.setattr(script.httpx, "post", post)

    assert script.main() == 0
    assert sent == {"url": "https://jobs.example.com/telegram/webhook", "secret_token": SECRET,
                    "allowed_updates": ["message"]}
