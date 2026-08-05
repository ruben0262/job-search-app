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

    assert set(components) == {"telegram_bot", "telegram_channel", "harvester", "scorer",
                               "delivery", "docgen"}
    for component in components.values():
        assert component["state"] in ("ok", "warn", "down", "off")
        assert component["detail"], "a dot with no words is a support request"

    assert components["telegram_bot"]["state"] == "ok"
    assert components["harvester"]["state"] == "ok"
    # No model yet is off, not broken — the honest state for a new account.
    assert components["scorer"]["state"] == "off"


def test_matches_waiting_and_nothing_sent_is_reported_as_broken(api):
    """**The shape of the outage this page slept through** (2026-07-31).

    Collection was running, scoring was running, the bot and channel were connected, and the
    notifier was completing its jobs while delivering nothing — it selected fifty candidates a run
    and dropped all fifty. Every component was green for six hours and the product was not working.

    Neither fact alone means anything: an account with nothing to send is quiet for good reasons,
    and a backlog right after a harvest is normal. The conjunction is the signal.
    """
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState
    from services.api.status_routes import DELIVERY_DOWN_HOURS

    http, session, user = api

    # Nothing to deliver: quiet is honest, not alarming.
    assert next(c for c in http.get("/status").json()["components"]
                if c["key"] == "delivery")["state"] == "off"

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer",
                    first_seen_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=95.0,
                             scored_at=datetime.now(UTC), notified_at=None))
    session.flush()

    # A match is waiting and nothing has ever been sent.
    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "down"
    assert "waiting" in delivery["detail"]

    # Delivered recently, with the backlog behind it: working, not stalled.
    session.query(UserJobState).filter(UserJobState.user_id == user.id).update(
        {"notified_at": datetime.now(UTC)}, synchronize_session=False)
    session.flush()
    assert next(c for c in http.get("/status").json()["components"]
                if c["key"] == "delivery")["state"] == "ok"

    # A second match arrives, and the last delivery recedes past the threshold. Same components
    # green as before — this is the conjunction the check exists for.
    second = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                       title="Platform Engineer", first_seen_at=datetime.now(UTC))
    session.add(second)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=second.id, score=95.0,
                             scored_at=datetime.now(UTC), notified_at=None))
    session.query(UserJobState).filter(UserJobState.job_id == job.id).update(
        {"notified_at": datetime.now(UTC) - timedelta(hours=DELIVERY_DOWN_HOURS + 1)},
        synchronize_session=False)
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "down"
    assert "nothing sent" in delivery["detail"]


def test_a_broken_bot_is_red_with_the_fix_in_words(api):
    http, session, user = api
    user.telegram_status = "broken"
    session.flush()

    bot = next(c for c in http.get("/status").json()["components"] if c["key"] == "telegram_bot")
    assert bot["state"] == "down"
    assert "token" in bot["detail"]


def test_pausing_delivery_is_reported_as_paused_not_broken(api):
    """**A pause is a decision, not a fault.**

    With messages off, matches pile up and nothing is sent — which is exactly the shape the stall
    check looks for. Without knowing about the switch, the page would show a red light for the
    thing the user themselves turned off, on the one screen that exists to say what is genuinely
    wrong. `dispatch_due_notifications` filters on the same flag, so the page and the pipeline
    cannot disagree about whether anything is being sent.
    """
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState

    http, session, user = api
    # A finished account with messages on. `active` starts false and means "not started yet" until
    # onboarding completes — which is why the check reads both.
    user.active = True
    user.onboarding_completed_at = datetime.now(UTC)
    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer",
                    first_seen_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=95.0,
                             scored_at=datetime.now(UTC),
                             notified_at=datetime.now(UTC) - timedelta(hours=48)))
    # A match still waiting behind it — without one, two days of quiet is an account with nothing
    # to send rather than a stall, which is the distinction the check is built on.
    waiting = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title="Platform Engineer", first_seen_at=datetime.now(UTC))
    session.add(waiting)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=waiting.id, score=95.0,
                             scored_at=datetime.now(UTC), notified_at=None))
    session.flush()

    # Running: a backlog with nothing sent for two days is a stall, and says so.
    assert next(c for c in http.get("/status").json()["components"]
                if c["key"] == "delivery")["state"] == "down"

    user.active = False
    session.flush()

    delivery = next(c for c in http.get("/status").json()["components"] if c["key"] == "delivery")
    assert delivery["state"] == "off", "a paused feed is not a broken one"
    assert "Paused by you" in delivery["detail"]


def test_there_is_no_mirrored_daily_cap_left_to_drift(api):
    """**The mirror is gone, because it never had to exist.**

    This used to assert that `status_routes.DEFAULT_NOTIFICATIONS_PER_DAY` equalled
    `deliver.MAX_NOTIFICATIONS_PER_DAY` — two constants kept equal by a test, on the reasoning that
    `services.api` may not import `services.notifier`. Both were 100 and both were fiction: the
    notifier reads `libs.limits`, whose default is unlimited, and neither constant governed a single
    delivery. `libs` is exactly where a value two services share belongs, so there is nothing left
    to mirror (ROADMAP 6af).
    """
    from services.notifier import deliver

    assert not hasattr(deliver, "MAX_NOTIFICATIONS_PER_DAY")


def test_spending_the_daily_allowance_is_not_a_stall(api):
    """**Today's jobs having already arrived is the system working, not failing.**

    The notifier defers once an account is at its cap and logs exactly that. Without knowing it, the
    stall check sees matches piling up with nothing sent and reports a warning: observed live on
    2026-08-01 as "802 matches waiting; last delivery 4 hours ago", where the real answer was that
    the day's jobs had been delivered hours earlier.
    """
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserJobState

    http, session, user = api
    user.active = True
    user.onboarding_completed_at = datetime.now(UTC)
    # **A cap has to exist for "the allowance is spent" to be a state at all.** The catalogue
    # default is unlimited, and reporting a spent allowance against no limit is what told an
    # account "today's 20 jobs have been sent" on a day it received 908.
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
