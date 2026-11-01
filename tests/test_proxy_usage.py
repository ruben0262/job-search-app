"""Each account's share of the site proxy: a lease while its day's bandwidth lasts, and the meter."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import find_one, insert
from libs.db.models import ProxyUsage, User
from services.harvester.fetch import ProxyCapExceeded
from services.harvester.proxy_usage import ProxyUsageRepository

SITE_PROXY = "http://user:pass@proxy.example:8080"


@pytest.fixture()
def ctx(db):
    return ProxyUsageRepository(db), db


def _user(s, tz: str = "Europe/Budapest", **fields) -> User:
    return insert(s, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone=tz,
                          **fields))


def _meter(s, user, **kw) -> ProxyUsage:
    return insert(s, ProxyUsage(user_id=user.id, **kw))


def _stored(s, user) -> ProxyUsage:
    """The meter as it is now: what `_meter` returned is a snapshot."""
    return find_one(s, ProxyUsage, {"user_id": user.id})


# ── leasing ───────────────────────────────────────────────────────────────────────────────────

def test_anybody_gets_a_lease_on_the_site_proxy_and_the_meter_is_made_on_first_use(ctx):
    """Nobody brings a proxy of their own any more: the site's is everyone's, within their limit."""
    repo, s = ctx
    user = _user(s)
    assert _stored(s, user) is None

    lease = repo.acquire_lease(user.id, proxy_url=SITE_PROXY)

    assert (lease.user_id, lease.proxy_url, lease.remaining_mb) == (user.id, SITE_PROXY, pytest.approx(200))
    assert _stored(s, user) is not None


def test_a_lease_says_how_much_of_the_day_is_left(ctx):
    repo, s = ctx
    user = _user(s)
    _meter(s, user, mb_used_today=50)
    assert repo.acquire_lease(user.id, proxy_url=SITE_PROXY).remaining_mb == pytest.approx(150)


def test_a_spent_day_yields_no_lease(ctx):
    """Admission, not interruption: the loop never starts rather than stopping mid-way."""
    repo, s = ctx
    user = _user(s)
    _meter(s, user, mb_used_today=200)
    assert repo.acquire_lease(user.id, proxy_url=SITE_PROXY) is None


def test_the_operators_limit_is_the_cap(ctx):
    repo, s = ctx
    user = _user(s, limits={"proxy_mb_per_day": 100})
    _meter(s, user, mb_used_today=99)

    repo.check_admission(user.id)                      # still under
    with pytest.raises(ProxyCapExceeded, match="daily proxy cap"):
        repo.check_admission(user.id, projected_mb=5)  # this request would cross it


def test_unlimited_is_never_spent(ctx):
    repo, s = ctx
    user = _user(s, limits={"proxy_mb_per_day": None})
    _meter(s, user, mb_used_today=10_000)
    assert repo.acquire_lease(user.id, proxy_url=SITE_PROXY) is not None


# ── metering ──────────────────────────────────────────────────────────────────────────────────

def test_usage_accumulates(ctx):
    repo, s = ctx
    user = _user(s)
    _meter(s, user)

    assert repo.record_usage(user.id, 10.5) == pytest.approx(10.5)
    assert repo.record_usage(user.id, 4.5) == pytest.approx(15.0)
    cred = _stored(s, user)
    assert float(cred.mb_used_today) == pytest.approx(15.0)
    assert cred.last_used_at is not None


def test_negative_usage_cannot_refund_the_counter(ctx):
    """A miscomputed byte count must not become free bandwidth."""
    repo, s = ctx
    user = _user(s)
    _meter(s, user, mb_used_today=10)
    assert repo.record_usage(user.id, -50) == pytest.approx(10.0)


# ── daily reset, in the user's timezone ───────────────────────────────────────────────────────

def test_the_counter_resets_on_the_users_local_day_not_utc(ctx):
    """Users are spread across timezones."""
    repo, s = ctx
    user = _user(s, tz="Pacific/Auckland")
    # Auckland is UTC+12.
    _meter(s, user, mb_used_today=150, usage_reset_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC))

    # ...and 2026-07-27 00:30 UTC is 2026-07-27 12:30 local — the local day has turned.
    rolled = repo.roll_daily_window(user.id, now=datetime(2026, 7, 27, 0, 30, tzinfo=UTC))
    assert rolled is True
    assert float(_stored(s, user).mb_used_today) == 0


def test_a_utc_day_boundary_alone_does_not_reset_a_user_east_of_it(ctx):
    """The inverse, and the reason this is not just `date() != date()` on UTC."""
    repo, s = ctx
    user = _user(s, tz="Pacific/Auckland")
    _meter(s, user, mb_used_today=150, usage_reset_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC))

    rolled = repo.roll_daily_window(user.id, now=datetime(2026, 7, 27, 0, 30, tzinfo=UTC))
    assert rolled is False
    assert float(_stored(s, user).mb_used_today) == pytest.approx(150.0)


def test_a_first_lease_initialises_the_window_without_wiping_usage(ctx):
    """A null `usage_reset_at` is initialisation, not a day boundary."""
    repo, s = ctx
    user = _user(s)
    cred = _meter(s, user, mb_used_today=50)
    assert cred.usage_reset_at is None

    assert repo.roll_daily_window(user.id) is False
    cred = _stored(s, user)
    assert float(cred.mb_used_today) == pytest.approx(50.0), "first lease wiped real usage"
    assert cred.usage_reset_at is not None, "the window must still be initialised"


def test_the_counter_does_not_reset_twice_in_one_local_day(ctx):
    repo, s = ctx
    user = _user(s, tz="Europe/Budapest")
    _meter(s, user, mb_used_today=0)
    now = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
    repo.roll_daily_window(user.id, now=now)

    repo.record_usage(user.id, 25)
    assert repo.roll_daily_window(user.id, now=now + timedelta(hours=2)) is False
    assert float(_stored(s, user).mb_used_today) == pytest.approx(25.0), \
        "a same-day roll wiped real usage"


def test_an_invalid_timezone_does_not_take_the_harvest_down(ctx):
    """A bad tz string makes one user's reset happen at the wrong hour — a nuisance."""
    repo, s = ctx
    user = _user(s, tz="Mars/Olympus_Mons")
    _meter(s, user, mb_used_today=5)
    assert repo.roll_daily_window(user.id) in (True, False)     # must not raise


# ── isolation ─────────────────────────────────────────────────────────────────────────────────

def test_one_users_usage_never_touches_anothers(ctx):
    """The proxy is shared; the bandwidth each account spent is its own."""
    repo, s = ctx
    alice, bob = _user(s), _user(s)
    repo.acquire_lease(alice.id, proxy_url=SITE_PROXY)
    repo.acquire_lease(bob.id, proxy_url=SITE_PROXY)

    repo.record_usage(alice.id, 40)

    assert float(_stored(s, alice).mb_used_today) == pytest.approx(40.0)
    assert float(_stored(s, bob).mb_used_today) == 0
