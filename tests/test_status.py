"""The status page: colours derived from the data, with a sentence attached."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from libs.db import insert, now
from libs.db.models import CorpusJob, User, UserJobState

pytest.importorskip("fastapi")


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           telegram_status="connected", telegram_chat_id="555"))
    # A fresh corpus row, so the harvester reads as collecting.
    _posting(db)

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


def _posting(db, *, hours_ago: float = 0) -> CorpusJob:
    return insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                title="Backend Engineer", first_seen_at=now() - timedelta(hours=hours_ago)))


def _change(db, user, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": fields})


def _component(http, key: str) -> dict:
    return next(c for c in http.get("/status").json()["components"] if c["key"] == key)


def test_every_component_answers_with_a_state_and_a_sentence(api):
    http, _, _ = api
    body = http.get("/status").json()
    components = {c["key"]: c for c in body["components"]}

    # No "Scoring": nothing scores, and a component that can only ever say "off" is not news. No
    # "Telegram channel" either: one platform bot sends to the person's own chat.
    assert set(components) == {"telegram_bot", "harvester", "delivery", "docgen"}
    for component in components.values():
        assert component["state"] in ("ok", "warn", "down", "off")
        assert component["detail"], "a dot with no words is a support request"

    assert components["telegram_bot"]["state"] == "ok"
    assert components["harvester"]["state"] == "ok"


def test_delivery_says_when_it_last_sent_in_words_a_person_has(api):
    """There is no score cutoff to explain any more, so the sentence must not mention one."""
    http, db, user = api

    def delivery() -> dict:
        return _component(http, "delivery")

    never = delivery()
    assert never["state"] == "off"
    assert "Nothing sent yet" in never["detail"]
    assert "score" not in never["detail"].lower()

    job = _posting(db)
    insert(db, UserJobState(user_id=user.id, job_id=job.id,
                            notified_at=now() - timedelta(hours=2, minutes=5)))

    sent = delivery()
    assert sent["state"] == "ok"
    assert "2 hours ago" in sent["detail"]


def test_a_broken_bot_is_red_with_the_fix_in_words(api):
    http, db, user = api
    _change(db, user, telegram_status="broken")

    bot = _component(http, "telegram_bot")
    assert bot["state"] == "down"
    assert "Connect it again" in bot["detail"]


def _green(db, user) -> None:
    from libs.db.models import UserConfig

    insert(db, UserConfig(user_id=user.id, schema_version=2, config={"sections": [
        {"platform": "all", "keywords": ["backend engineer"]}]}))


def test_a_match_left_unsent_for_over_an_hour_reads_as_stuck(api):
    """The stall signal, on the notifier's own definition of owed: matches since the newest one sent."""
    http, db, user = api
    _green(db, user)
    _change(db, user, created_at=now() - timedelta(hours=3))
    _posting(db, hours_ago=2)

    delivery = _component(http, "delivery")
    assert delivery["state"] == "down", delivery
    # The fixture's fresh posting matches too: two owed, the oldest two hours old.
    assert "2 matching job(s) waiting, the oldest for 2 hour(s)" in delivery["detail"]


def test_a_match_found_minutes_ago_is_on_its_way_not_stuck(api):
    """The notifier picks a posting up within a minute of its being stored, so a fresh one is not a stall."""
    http, db, user = api
    _green(db, user)
    # Before the fixture's posting, so that posting is new to them.
    _change(db, user, created_at=now() - timedelta(hours=1))

    delivery = _component(http, "delivery")
    assert delivery["state"] == "ok", delivery
    assert "1 new matching job(s) on the way" in delivery["detail"]


def test_pausing_delivery_is_reported_as_paused_not_broken(api):
    """A pause is a decision, not a fault."""
    http, db, user = api
    # A finished account that has had deliveries.
    _change(db, user, active=True, onboarding_completed_at=now())
    job = _posting(db)
    insert(db, UserJobState(user_id=user.id, job_id=job.id, notified_at=now() - timedelta(hours=48)))
    assert _component(http, "delivery")["state"] == "ok"

    _change(db, user, active=False)

    delivery = _component(http, "delivery")
    assert delivery["state"] == "off", "a paused feed is not a broken one"
    assert "Paused by you" in delivery["detail"]


def test_there_is_no_mirrored_daily_cap_left_to_drift(api):
    """The mirror is gone, because it never had to exist."""
    from services.notifier import deliver

    assert not hasattr(deliver, "MAX_NOTIFICATIONS_PER_DAY")


def test_spending_the_daily_allowance_is_not_a_stall(api):
    """Today's jobs having already arrived is the system working, not failing."""
    from libs.limits import their_midnight

    http, db, user = api
    _green(db, user)
    # A cap has to exist for "the allowance is spent" to be a state at all.
    cap = 25
    _change(db, user, active=True, onboarding_completed_at=now(), limits={"notifications_per_day": cap},
            created_at=now() - timedelta(days=2))

    # The day's allowance, all delivered earlier today, with more still waiting behind it.
    sent_at = max(their_midnight(db, user.id), now() - timedelta(hours=4)) + timedelta(seconds=1)
    for _ in range(cap):
        insert(db, UserJobState(user_id=user.id, job_id=_posting(db, hours_ago=6).id, notified_at=sent_at))
    _posting(db, hours_ago=3)

    delivery = _component(http, "delivery")
    assert delivery["state"] == "ok", delivery
    assert "Today's" in delivery["detail"]
