"""Proxy leasing, metering, daily reset, and quarantine."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.db.models import ProxyCredential, User
from services.harvester.fetch import ProxyCapExceeded
from services.harvester.proxy_usage import QUARANTINE_AFTER_FAILURES, ProxyUsageRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")

RESOLVER = lambda ref: "http://user:pass@proxy.example:8080"        # noqa: E731


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield ProxyUsageRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s, tz: str = "Europe/Budapest") -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone=tz)
    s.add(u)
    s.flush()
    return u


def _credential(s, user, **kw) -> ProxyCredential:
    c = ProxyCredential(user_id=user.id, secret_ref="secret:proxy", **kw)
    s.add(c)
    s.flush()
    return c


# ── leasing ───────────────────────────────────────────────────────────────────────────────────

def test_a_healthy_credential_with_budget_yields_a_lease(ctx):
    repo, s = ctx
    user = _user(s)
    _credential(s, user, daily_cap_mb=200, mb_used_today=50)

    lease = repo.acquire_lease(user.id, secret_resolver=RESOLVER)
    assert lease is not None
    assert lease.user_id == user.id
    assert lease.remaining_mb == pytest.approx(150)


def test_no_credential_is_not_an_error(ctx):
    """The ordinary case."""
    repo, s = ctx
    user = _user(s)
    assert repo.acquire_lease(user.id, secret_resolver=RESOLVER) is None


@pytest.mark.parametrize("status", ["quarantined", "disabled"])
def test_an_unhealthy_credential_yields_no_lease(ctx, status):
    repo, s = ctx
    user = _user(s)
    _credential(s, user, status=status)
    assert repo.acquire_lease(user.id, secret_resolver=RESOLVER) is None


def test_a_spent_budget_yields_no_lease(ctx):
    """Admission, not interruption: the loop never starts rather than stopping mid-way."""
    repo, s = ctx
    user = _user(s)
    _credential(s, user, daily_cap_mb=200, mb_used_today=200)
    assert repo.acquire_lease(user.id, secret_resolver=RESOLVER) is None


def test_an_unresolvable_secret_counts_as_a_failure(ctx):
    """A credential row whose secret cannot be decrypted is broken, not merely idle."""
    repo, s = ctx
    user = _user(s)
    cred = _credential(s, user)

    assert repo.acquire_lease(user.id, secret_resolver=lambda ref: None) is None
    s.flush()
    assert cred.fail_streak == 1
    assert "resolve" in (cred.last_error or "")


# ── admission ─────────────────────────────────────────────────────────────────────────────────

def test_admission_raises_at_the_cap(ctx):
    """The operator's limit is the authority, not the column."""
    repo, s = ctx
    user = _user(s)
    user.limits = {"proxy_mb_per_day": 100}
    _credential(s, user, daily_cap_mb=100, mb_used_today=99)
    s.flush()

    repo.check_admission(user.id)                      # still under
    with pytest.raises(ProxyCapExceeded, match="daily proxy cap"):
        repo.check_admission(user.id, projected_mb=5)  # this request would cross it


def test_the_operators_cap_beats_the_column(ctx):
    """Two values for one ceiling is how they disagree."""
    repo, s = ctx
    user = _user(s)
    user.limits = {"proxy_mb_per_day": 10}
    _credential(s, user, daily_cap_mb=500, mb_used_today=12)
    s.flush()

    with pytest.raises(ProxyCapExceeded):
        repo.check_admission(user.id)


# ── metering ──────────────────────────────────────────────────────────────────────────────────

def test_usage_accumulates(ctx):
    repo, s = ctx
    user = _user(s)
    cred = _credential(s, user, daily_cap_mb=200)

    assert repo.record_usage(user.id, 10.5) == pytest.approx(10.5)
    assert repo.record_usage(user.id, 4.5) == pytest.approx(15.0)
    s.flush()
    assert float(cred.mb_used_today) == pytest.approx(15.0)
    assert cred.last_used_at is not None


def test_negative_usage_cannot_refund_the_counter(ctx):
    """A miscomputed byte count must not become free bandwidth."""
    repo, s = ctx
    user = _user(s)
    _credential(s, user, mb_used_today=10)
    assert repo.record_usage(user.id, -50) == pytest.approx(10.0)


# ── daily reset, in the user's timezone ───────────────────────────────────────────────────────

def test_the_counter_resets_on_the_users_local_day_not_utc(ctx):
    """Users are spread across timezones."""
    repo, s = ctx
    user = _user(s, tz="Pacific/Auckland")
    cred = _credential(s, user, mb_used_today=150)
    # Auckland is UTC+12.
    cred.usage_reset_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    s.flush()

    # ...and 2026-07-27 00:30 UTC is 2026-07-27 12:30 local — the local day has turned.
    rolled = repo.roll_daily_window(user.id, now=datetime(2026, 7, 27, 0, 30, tzinfo=UTC))
    s.flush()
    assert rolled is True
    assert float(cred.mb_used_today) == 0


def test_a_utc_day_boundary_alone_does_not_reset_a_user_east_of_it(ctx):
    """The inverse, and the reason this is not just `date() != date()` on UTC."""
    repo, s = ctx
    user = _user(s, tz="Pacific/Auckland")
    cred = _credential(s, user, mb_used_today=150)
    cred.usage_reset_at = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    s.flush()

    rolled = repo.roll_daily_window(user.id, now=datetime(2026, 7, 27, 0, 30, tzinfo=UTC))
    s.flush()
    assert rolled is False
    assert float(cred.mb_used_today) == pytest.approx(150.0)


def test_a_first_lease_initialises_the_window_without_wiping_usage(ctx):
    """A null `usage_reset_at` is initialisation, not a day boundary."""
    repo, s = ctx
    user = _user(s)
    cred = _credential(s, user, daily_cap_mb=200, mb_used_today=50)
    assert cred.usage_reset_at is None

    assert repo.roll_daily_window(user.id) is False
    s.flush()
    assert float(cred.mb_used_today) == pytest.approx(50.0), "first lease wiped real usage"
    assert cred.usage_reset_at is not None, "the window must still be initialised"


def test_the_counter_does_not_reset_twice_in_one_local_day(ctx):
    repo, s = ctx
    user = _user(s, tz="Europe/Budapest")
    cred = _credential(s, user, mb_used_today=0)
    now = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
    repo.roll_daily_window(user.id, now=now)
    s.flush()

    repo.record_usage(user.id, 25)
    s.flush()
    assert repo.roll_daily_window(user.id, now=now + timedelta(hours=2)) is False
    assert float(cred.mb_used_today) == pytest.approx(25.0), "a same-day roll wiped real usage"


def test_an_invalid_timezone_does_not_take_the_harvest_down(ctx):
    """A bad tz string makes one user's reset happen at the wrong hour — a nuisance."""
    repo, s = ctx
    user = _user(s, tz="Mars/Olympus_Mons")
    _credential(s, user, mb_used_today=5)
    assert repo.roll_daily_window(user.id) in (True, False)     # must not raise


# ── health ────────────────────────────────────────────────────────────────────────────────────

def test_quarantine_needs_consecutive_failures(ctx):
    """Cumulative counting would eventually quarantine every healthy user."""
    repo, s = ctx
    user = _user(s)
    cred = _credential(s, user)

    for _ in range(QUARANTINE_AFTER_FAILURES - 1):
        assert repo.record_failure(user.id, "407 proxy auth") is False
    repo.record_success(user.id)
    s.flush()
    assert cred.fail_streak == 0
    assert cred.status == "healthy", "an intermittent failure must not quarantine"

    for _ in range(QUARANTINE_AFTER_FAILURES - 1):
        repo.record_failure(user.id, "407 proxy auth")
    quarantined = repo.record_failure(user.id, "407 proxy auth")
    s.flush()
    assert quarantined is True, "the caller must learn to notify the owner"
    assert cred.status == "quarantined"


def test_quarantine_is_reported_once_not_on_every_subsequent_failure(ctx):
    """Otherwise the user is emailed on every cycle while their credential stays broken."""
    repo, s = ctx
    user = _user(s)
    _credential(s, user)
    for _ in range(QUARANTINE_AFTER_FAILURES):
        repo.record_failure(user.id, "boom")
    assert repo.record_failure(user.id, "boom") is False


def test_a_quarantined_credential_can_be_restored(ctx):
    repo, s = ctx
    user = _user(s)
    cred = _credential(s, user, status="quarantined", fail_streak=9, last_error="boom")

    repo.restore(user.id)
    s.flush()
    assert cred.status == "healthy"
    assert cred.fail_streak == 0
    assert cred.last_error is None
    assert repo.acquire_lease(user.id, secret_resolver=RESOLVER) is not None


# ── isolation ─────────────────────────────────────────────────────────────────────────────────

def test_one_users_usage_never_touches_anothers(ctx):
    """T13 at the database layer: there is only ever one candidate credential for a fetch."""
    repo, s = ctx
    alice, bob = _user(s), _user(s)
    a_cred = _credential(s, alice, daily_cap_mb=200)
    b_cred = _credential(s, bob, daily_cap_mb=200)

    repo.record_usage(alice.id, 40)
    repo.record_failure(bob.id, "bob's problem")
    s.flush()

    assert float(a_cred.mb_used_today) == pytest.approx(40.0)
    assert float(b_cred.mb_used_today) == 0
    assert a_cred.fail_streak == 0
    assert b_cred.fail_streak == 1


# ── the row that has to exist beside the secret ───────────────────────────────────────────────

def test_saving_a_proxy_url_also_creates_the_credential_row(ctx):
    """The gap that made the whole tier dead."""
    from libs.db.models import ProxyCredential
    from services.api.credential_routes import _ensure_proxy_credential

    _repo, session = ctx
    user = _user(session)
    assert session.get(ProxyCredential, user.id) is None

    _ensure_proxy_credential(session, user.id)
    credential = session.get(ProxyCredential, user.id)
    assert credential is not None
    assert credential.status == "healthy" and credential.daily_cap_mb > 0


def test_re_saving_a_proxy_url_does_not_un_quarantine_the_account(ctx):
    """A rotation is not a reset."""
    from libs.db.models import ProxyCredential
    from services.api.credential_routes import _ensure_proxy_credential

    _repo, session = ctx
    user = _user(session)
    _credential(session, user, status="quarantined")

    _ensure_proxy_credential(session, user.id)
    assert session.get(ProxyCredential, user.id).status == "quarantined"
