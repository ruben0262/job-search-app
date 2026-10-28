"""The status page: colours derived from the data, with a sentence attached."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import CorpusJob, User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                telegram_status="connected")
    session.add(user)
    # A fresh corpus row, so the harvester reads as collecting.
    session.add(CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                          title="Backend Engineer", first_seen_at=datetime.now(UTC)))
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


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
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState

    http, session, user = api

    def delivery() -> dict:
        return next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")

    never = delivery()
    assert never["state"] == "off"
    assert "Nothing sent yet" in never["detail"]
    assert "score" not in never["detail"].lower()

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer",
                    first_seen_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id,
                             notified_at=datetime.now(UTC) - timedelta(hours=2, minutes=5)))
    session.flush()

    sent = delivery()
    assert sent["state"] == "ok"
    assert "2 hours ago" in sent["detail"]


def test_a_broken_bot_is_red_with_the_fix_in_words(api):
    http, session, user = api
    user.telegram_status = "broken"
    session.flush()

    bot = next(c for c in http.get("/status").json()["components"] if c["key"] == "telegram_bot")
    assert bot["state"] == "down"
    assert "Connect it again" in bot["detail"]


def _green(session, user) -> None:
    from libs.db.models import UserConfig

    session.add(UserConfig(user_id=user.id, schema_version=2, config={"sections": [
        {"platform": "all", "keywords": ["backend engineer"]}]}))
    session.flush()


def test_a_match_left_unsent_for_over_an_hour_reads_as_stuck(api):
    """The stall signal, on the notifier's own definition of owed: matches since the newest one sent."""
    from datetime import timedelta

    from libs.db.models import CorpusJob

    http, session, user = api
    _green(session, user)
    user.created_at = datetime.now(UTC) - timedelta(hours=3)
    session.add(CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                          title="Backend Engineer", first_seen_at=datetime.now(UTC) - timedelta(hours=2)))
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "down", delivery
    # The fixture's fresh posting matches too: two owed, the oldest two hours old.
    assert "2 matching job(s) waiting, the oldest for 2 hour(s)" in delivery["detail"]


def test_a_match_found_minutes_ago_is_on_its_way_not_stuck(api):
    """The notifier picks a posting up within a minute of its being stored, so a fresh one is not a stall."""
    from datetime import timedelta

    http, session, user = api
    _green(session, user)
    # Before the fixture's posting: a database `now()` is the transaction's first statement, after it.
    user.created_at = datetime.now(UTC) - timedelta(hours=1)
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "ok", delivery
    assert "1 new matching job(s) on the way" in delivery["detail"]


def test_pausing_delivery_is_reported_as_paused_not_broken(api):
    """A pause is a decision, not a fault."""
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState

    http, session, user = api
    # A finished account that has had deliveries.
    user.active = True
    user.onboarding_completed_at = datetime.now(UTC)
    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer",
                    first_seen_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id,
                             notified_at=datetime.now(UTC) - timedelta(hours=48)))
    session.flush()
    assert next(c for c in http.get("/status").json()["components"]
                if c["key"] == "delivery")["state"] == "ok"

    user.active = False
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "off", "a paused feed is not a broken one"
    assert "Paused by you" in delivery["detail"]


def test_there_is_no_mirrored_daily_cap_left_to_drift(api):
    """The mirror is gone, because it never had to exist."""
    from services.notifier import deliver

    assert not hasattr(deliver, "MAX_NOTIFICATIONS_PER_DAY")


def test_spending_the_daily_allowance_is_not_a_stall(api):
    """Today's jobs having already arrived is the system working, not failing."""
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState

    http, session, user = api
    user.active = True
    user.onboarding_completed_at = datetime.now(UTC)
    # A cap has to exist for "the allowance is spent" to be a state at all.
    cap = 25
    user.limits = {"notifications_per_day": cap}
    session.flush()

    # The day's allowance, all delivered a few hours ago, with more still waiting behind it.
    for index in range(cap + 1):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title="Backend Engineer", first_seen_at=datetime.now(UTC))
        session.add(job)
        session.flush()
        session.add(UserJobState(
            user_id=user.id, job_id=job.id, score=95.0, scored_at=datetime.now(UTC),
            notified_at=(datetime.now(UTC) - timedelta(hours=4)
                         if index < cap else None)))
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "ok", delivery
    assert "Today's" in delivery["detail"]
