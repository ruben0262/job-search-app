"""Platform health, and the alerts derived from it.

Two failure modes are worth guarding, and they pull in opposite directions:

- **an alert that does not fire** — the silent failure the whole module exists to catch;
- **an alert that fires when nothing is wrong** — which trains its reader to ignore the next one,
  and the next one is the outage.

The silent-feed condition is where both meet, so it gets the most tests: it must fire for an
account with matches waiting and nothing delivered, and stay quiet for an account whose config
simply matched nothing this week.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from libs.ops.alerts import Alert, evaluate, render
from libs.ops.health import CORPUS_PROJECTION_ROWS, LaneHealth, PlatformHealth, UserHealth

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


def lane(name="notify", *, ready=0, claimed=0, dead=0, oldest=None, expected=5) -> LaneHealth:
    return LaneHealth(name, ready, claimed, dead, oldest, expected)


def user(**kwargs) -> UserHealth:
    base = {"user_id": str(uuid.uuid4()), "email": "a@example.com",
            "telegram_status": "connected", "model_version": 1, "labels_stale": 0,
            "pending_matches": 0, "hours_since_delivery": 1.0, "proxy_status": "healthy",
            "proxy_mb_today": 0.0}
    return UserHealth(**{**base, **kwargs})


def health(**kwargs) -> PlatformHealth:
    base = {"checked_at": datetime.now(UTC).isoformat(), "lanes": [lane()], "users_total": 1,
            "users_active": 1, "corpus_rows": 1000, "corpus_embedded": 1000,
            "corpus_projection_rows": CORPUS_PROJECTION_ROWS, "postgres_bytes": 1024**3,
            "connections": 5, "users": []}
    return PlatformHealth(**{**base, **kwargs})


def keys(alerts: list[Alert]) -> set[str]:
    return {alert.key for alert in alerts}


# ── nothing wrong ─────────────────────────────────────────────────────────────────────────────

def test_a_healthy_platform_produces_no_alerts():
    """The state the operator should see almost always, so it has to be genuinely quiet."""
    assert evaluate(health(users=[user()])) == []


def test_an_idle_queue_is_not_a_problem():
    """Empty lanes are what a small platform looks like between cycles."""
    assert evaluate(health(lanes=[lane("label", ready=0), lane("train", ready=0)])) == []


def test_a_deep_but_moving_queue_is_not_an_alert():
    """A relabel is eighty queued jobs by design. Depth alone says nothing."""
    assert evaluate(health(lanes=[lane("label", ready=80, oldest=10, expected=60)])) == []


# ── the silent feed ───────────────────────────────────────────────────────────────────────────

def test_matches_waiting_and_nothing_delivered_pages():
    """The failure this module exists for: the feed runs, the scoring works, and nothing arrives."""
    alerts = evaluate(health(users=[user(pending_matches=12, hours_since_delivery=30.0)]))
    assert keys(alerts) == {"silent_feed"}
    assert alerts[0].pages


def test_a_narrow_config_with_nothing_to_send_is_silent():
    """**The qualifier that makes the alert usable.** Without "while jobs matched" this fires for
    every user whose search is specific, who is not broken."""
    assert evaluate(health(users=[user(pending_matches=0, hours_since_delivery=None)])) == []


def test_a_brand_new_account_with_matches_pages_rather_than_waiting_a_day():
    """`hours_since_delivery` is None because nothing has ever been sent. An account that has
    matches and has never received anything is the same failure on its first day."""
    alerts = evaluate(health(users=[user(pending_matches=5, hours_since_delivery=None)]))
    assert keys(alerts) == {"silent_feed"}


def test_a_recent_delivery_with_matches_pending_is_normal():
    """Matches accumulate between digests. That is the digest working, not failing."""
    assert evaluate(health(users=[user(pending_matches=9, hours_since_delivery=0.5)])) == []


# ── the queue ─────────────────────────────────────────────────────────────────────────────────

def test_one_dead_lettered_job_pages():
    """The queue retries five times before giving up, so one dead job means five failures nobody
    saw."""
    alerts = evaluate(health(lanes=[lane("train", dead=1)]))
    assert keys(alerts) == {"dead_letter:train"}
    assert alerts[0].pages


def test_a_stall_is_measured_against_the_lane_not_a_fixed_number():
    """A `notify` job waiting an hour is broken; a `label` job waiting an hour is a queue doing its
    job."""
    stalled = evaluate(health(lanes=[lane("notify", ready=1, oldest=25, expected=5)]))
    fine = evaluate(health(lanes=[lane("label", ready=1, oldest=25, expected=60)]))

    assert keys(stalled) == {"stalled:notify"}
    assert fine == []


# ── capacity ──────────────────────────────────────────────────────────────────────────────────

def test_a_nearly_full_database_pages():
    """Everything is in one database by design, so this is the whole platform rather than a
    component."""
    alerts = evaluate(health(postgres_bytes=(55 * 1024**3)))
    assert keys(alerts) == {"postgres_disk"}
    assert alerts[0].pages


def test_corpus_growth_past_projection_warns_that_the_trim_is_broken():
    """A slow, silent failure that otherwise shows up as a full disk months later."""
    alerts = evaluate(health(corpus_rows=CORPUS_PROJECTION_ROWS * 2))
    assert keys(alerts) == {"corpus_growth"}
    assert not alerts[0].pages


# ── per-user conditions ───────────────────────────────────────────────────────────────────────

def test_a_broken_bot_warns_and_names_the_user():
    """An operator cannot act on "somebody's Telegram broke"."""
    alerts = evaluate(health(users=[user(telegram_status="broken")]))
    assert alerts[0].user_email == "a@example.com"
    assert alerts[0].key == "telegram_broken"


def test_a_quarantined_proxy_warns():
    alerts = evaluate(health(users=[user(proxy_status="quarantined")]))
    assert keys(alerts) == {"proxy_quarantined"}


def test_pages_sort_above_warnings():
    """Read at three in the morning on a phone, where only the top line is read."""
    alerts = evaluate(health(lanes=[lane("train", dead=1)],
                             users=[user(telegram_status="broken")]))
    assert [alert.severity for alert in alerts] == ["page", "warn"]


def test_every_alert_says_what_to_do():
    """"Look into it" is not an action. Each alert names the likely cause."""
    alerts = evaluate(health(lanes=[lane("train", dead=1)],
                             users=[user(pending_matches=3, hours_since_delivery=99.0)],
                             postgres_bytes=(55 * 1024**3),
                             corpus_rows=CORPUS_PROJECTION_ROWS * 2))
    assert len(alerts) == 4
    assert all(len(alert.action) > 20 for alert in alerts)


def test_the_summary_is_readable_when_nothing_is_wrong():
    assert "No alerts" in render([])


# ── against a real database ───────────────────────────────────────────────────────────────────

@pytestmark_db
def test_health_reads_a_live_database_without_scanning_it():
    """The queries have to work against the real schema, and a health check that costs more than
    the thing it watches is one somebody turns off."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, QueueJob, User
    from libs.ops import platform_health

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        account = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                       active=True)
        session.add(account)
        session.flush()
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
        session.add(job)
        session.add(QueueJob(lane="label", user_id=account.id, payload={}, state="dead",
                             run_after=datetime.now(UTC) - timedelta(hours=3)))
        session.flush()

        state = platform_health(session)

        # Every lane, including the empty ones: a lane missing because it had no rows is
        # indistinguishable from a lane whose worker died.
        from libs.db.models.queue import LANES

        assert {row.lane for row in state.lanes} == set(LANES)
        assert next(row for row in state.lanes if row.lane == "label").dead == 1
        assert state.postgres_bytes > 0
        assert any(row.email == account.email for row in state.users)
        assert "dead_letter:label" in keys(evaluate(state))
    finally:
        session.close()
        trans.rollback()
        conn.close()


# ── the embedder probe (G10) ──────────────────────────────────────────────────────────────────

def test_an_unreachable_embedder_is_a_page_not_a_shrug(monkeypatch):
    """**The second single point of failure, and the only one nothing else reports.** The alerts in
    `libs/ops` are computed from the database and cannot see a dead embedder; without one, nothing
    gets a vector, nothing is scored, and from inside the application that reads as a quiet day."""
    import httpx

    from deploy.scripts.check_health import embedder_down

    monkeypatch.setenv("EMBEDDER_URL", "http://embedder-gateway:8080")

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", refuse)
    reason = embedder_down()
    assert reason is not None
    assert "ConnectError" in reason


def test_a_healthy_embedder_says_nothing(monkeypatch):
    import httpx

    from deploy.scripts.check_health import embedder_down

    monkeypatch.setenv("EMBEDDER_URL", "http://embedder-gateway:8080")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json={"ok": True}))
    assert embedder_down() is None


def test_no_embedder_url_is_skipped_rather_than_failed(monkeypatch):
    """This script is also run by hand on a laptop, where an unset URL is not a platform alert."""
    from deploy.scripts.check_health import embedder_down

    monkeypatch.delenv("EMBEDDER_URL", raising=False)
    assert embedder_down() is None
