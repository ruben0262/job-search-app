"""Platform health, and the alerts derived from it."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from libs.db import insert
from libs.ops.alerts import DATABASE_ALERT_FRACTION, Alert, evaluate, render
from libs.ops.health import LaneHealth, PlatformHealth, UserHealth

# Atlas's free tier; comfortably inside it, and past the point it pages at.
DATABASE_BUDGET_BYTES = 512 * 1024**2
ROOMY = DATABASE_BUDGET_BYTES // 4
NEARLY_FULL = int(DATABASE_BUDGET_BYTES * (DATABASE_ALERT_FRACTION + 0.05))


def lane(name="notify", *, ready=0, claimed=0, dead=0, oldest=None, expected=5) -> LaneHealth:
    return LaneHealth(name, ready, claimed, dead, oldest, expected)


def user(**kwargs) -> UserHealth:
    base = {"user_id": str(uuid.uuid4()), "email": "a@example.com",
            "telegram_status": "connected", "proxy_status": "healthy"}
    return UserHealth(**{**base, **kwargs})


def health(**kwargs) -> PlatformHealth:
    base = {"checked_at": datetime.now(UTC).isoformat(), "lanes": [lane()], "users_total": 1,
            "users_active": 1, "corpus_rows": 1000, "database_bytes": ROOMY,
            "database_budget_bytes": DATABASE_BUDGET_BYTES, "connections": 5, "users": []}
    return PlatformHealth(**{**base, **kwargs})


def keys(alerts: list[Alert]) -> set[str]:
    return {alert.key for alert in alerts}


# ── nothing wrong ─────────────────────────────────────────────────────────────────────────────

def test_a_healthy_platform_produces_no_alerts():
    """The state the operator should see almost always, so it has to be genuinely quiet."""
    assert evaluate(health(users=[user()])) == []


def test_an_idle_queue_is_not_a_problem():
    """Empty lanes are what a small platform looks like between cycles."""
    assert evaluate(health(lanes=[lane("notify", ready=0), lane("docgen", ready=0)])) == []


def test_a_deep_but_moving_queue_is_not_an_alert():
    """A harvest that finds work for every account queues one run each. Depth alone says nothing."""
    assert evaluate(health(lanes=[lane("notify", ready=80, oldest=3, expected=5)])) == []


# ── the queue ─────────────────────────────────────────────────────────────────────────────────

def test_one_dead_lettered_job_pages():
    """The queue retries five times before giving up, so one dead job means five failures nobody saw."""
    alerts = evaluate(health(lanes=[lane("docgen", dead=1)]))
    assert keys(alerts) == {"dead_letter:docgen"}
    assert alerts[0].pages


def test_a_stall_is_measured_against_the_lane_not_a_fixed_number():
    """The same wait stalls a lane that should drain in minutes and not one that is allowed longer."""
    stalled = evaluate(health(lanes=[lane("notify", ready=1, oldest=25, expected=5)]))
    fine = evaluate(health(lanes=[lane("docgen", ready=1, oldest=25, expected=10)]))

    assert keys(stalled) == {"stalled:notify"}
    assert fine == []


def test_every_lane_has_an_expected_wait_of_its_own():
    """A lane missing from the map is judged against a 30-minute default nobody chose for it."""
    from libs.db.models.queue import LANES
    from libs.ops.health import EXPECTED_WAIT_MIN

    assert set(EXPECTED_WAIT_MIN) == set(LANES)


# ── capacity ──────────────────────────────────────────────────────────────────────────────────

def test_a_nearly_full_database_pages():
    """Everything is in one database by design, so this is the whole platform rather than a component.
    At the free tier's ceiling Atlas refuses every write, sign-ups included."""
    alerts = evaluate(health(database_bytes=NEARLY_FULL))
    assert keys(alerts) == {"database_size"}
    assert alerts[0].pages
    assert "512 MB" in alerts[0].text


def test_the_budget_the_alert_measures_against_is_the_operators_setting(db):
    """A bigger tier is a panel change: the same size that pages on the free tier is quiet on 2 GB."""
    from libs import settings
    from libs.ops.health import platform_health

    assert platform_health(db).database_budget_bytes == DATABASE_BUDGET_BYTES, "the free tier by default"
    settings.put(db, "database_budget_mb", 2048)
    assert platform_health(db).database_budget_bytes == 2048 * 1024**2
    assert evaluate(health(database_bytes=NEARLY_FULL, database_budget_bytes=2048 * 1024**2)) == []


def test_the_database_alert_comes_before_the_ceiling_not_at_it():
    """A page at 100% is a page after the outage. Below the threshold is quiet."""
    just_under = int(DATABASE_BUDGET_BYTES * DATABASE_ALERT_FRACTION) - 1
    assert evaluate(health(database_bytes=just_under)) == []
    assert DATABASE_ALERT_FRACTION < 1


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
    alerts = evaluate(health(lanes=[lane("docgen", dead=1)],
                             users=[user(telegram_status="broken")]))
    assert [alert.severity for alert in alerts] == ["page", "warn"]


def test_every_alert_says_what_to_do():
    """"Look into it" is not an action. Each alert names the likely cause."""
    alerts = evaluate(health(lanes=[lane("notify", dead=1, ready=1, oldest=60)],
                             users=[user(telegram_status="broken", proxy_status="quarantined")],
                             database_bytes=NEARLY_FULL))
    assert len(alerts) == 5
    assert all(len(alert.action) > 20 for alert in alerts)


def test_the_summary_is_readable_when_nothing_is_wrong():
    assert "No alerts" in render([])


# ── against a real database ───────────────────────────────────────────────────────────────────

def test_health_reads_a_live_database_without_scanning_it(db):
    """The queries have to work against the real schema."""
    from libs.db.models import CorpusJob, QueueJob, User
    from libs.db.models.queue import LANES
    from libs.ops import platform_health

    account = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                              active=True))
    insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer"))
    insert(db, QueueJob(lane="docgen", user_id=account.id, payload={}, state="dead",
                        run_after=datetime.now(UTC) - timedelta(hours=3)))
    insert(db, QueueJob(lane="notify", user_id=account.id, payload={},
                        run_after=datetime.now(UTC) - timedelta(hours=1)))

    state = platform_health(db)

    # Every lane, including the empty ones.
    assert {row.lane for row in state.lanes} == set(LANES)
    lanes = {row.lane: row for row in state.lanes}
    assert lanes["docgen"].dead == 1
    assert lanes["notify"].ready == 1 and lanes["notify"].oldest_ready_min >= 59
    assert (state.users_total, state.users_active, state.corpus_rows) == (1, 1, 1)
    assert state.database_bytes > 0
    assert state.connections > 0
    assert any(row.email == account.email for row in state.users)
    assert {"dead_letter:docgen", "stalled:notify"} <= keys(evaluate(state))


# ── the proxy is opt-in (ROADMAP 6aw) ─────────────────────────────────────────────────────────


def test_an_account_with_no_proxy_is_not_an_alert():
    """LinkedIn is opt-in, so having no proxy is a configuration rather than a fault."""
    assert "proxy_missing" not in keys(evaluate(health(users=[user(proxy_status="no_secret")])))
    assert "proxy_missing" not in keys(evaluate(health(users=[user(proxy_status=None)])))


def test_the_status_reads_the_secret_not_just_the_row(db):
    """The distinction the panel got wrong: a row saying `healthy` beside no secret at all."""
    from libs.db.models import ProxyCredential, User, UserSecret
    from libs.ops.health import user_health

    bare, kept = (insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                                  status="active", active=True)) for _ in range(2))
    # The row `_ensure_proxy_credential` writes, with its default status and no secret behind it.
    for account in (bare, kept):
        insert(db, ProxyCredential(user_id=account.id, secret_ref=f"{account.id}:proxy"))
    insert(db, UserSecret(user_id=kept.id, kind="proxy", ciphertext=b"x", nonce=b"n"))

    rows = {row.email: row for row in user_health(db)}
    assert rows[bare.email].proxy_status == "no_secret", (
        "a row with no secret behind it must not report the row's own status")
    assert rows[kept.email].proxy_status == "healthy", "a row with its secret reports its own status"


# ── what the health check costs as the platform grows (ROADMAP 6be) ───────────────────────────

def test_the_health_check_does_not_ask_the_platform_a_question_per_account(db):
    """**A relationship, not a stopwatch.** The count of queries must not scale with accounts.

    `user_health` was a loop of about seven queries an account, three of which returned the same
    platform-wide answer every time. Measured against a scratch database of fifty accounts and 2.65M
    `user_job_state` rows: **18.83 seconds and 351 queries**, against `health.service`'s 60-second
    timeout. The monitor would have been the first thing to break as the platform grew.

    Timing here would be flaky and would not say what went wrong. The number of queries is the thing
    that was actually wrong, so that is what this pins: adding an account may add a bounded number of
    queries, never a new platform-wide read.
    """
    from libs.db.models import User
    from libs.ops.health import user_health

    class Counted:
        """The database, counting every query sent through any collection of it."""

        def __init__(self):
            self.queries = 0

        def __getitem__(self, name):
            collection, counted = db[name], self

            class Collection:
                def __getattr__(self, method):
                    counted.queries += 1
                    return getattr(collection, method)

            return Collection()

    def queries_for(accounts: int) -> int:
        for _ in range(accounts):
            insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                            status="active", active=True))
        counted = Counted()
        user_health(counted)
        return counted.queries

    few, many = queries_for(2), queries_for(10)
    assert few > 0, "the premise: the counter sees the queries"
    # Ten more accounts may cost a bounded number of per-account queries; what must not happen is
    # the platform-wide reads being repeated for each of them.
    per_account = (many - few) / 10
    assert per_account <= 4, (
        f"each extra account costs {per_account:.1f} queries. Anything the whole platform shares — "
        "`job_max_age_hours`, the source floors — must be read once per pass, not once per account")
