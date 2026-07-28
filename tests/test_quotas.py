"""Plans and quota resolution.

Short on purpose. The valuable assertions are that the limits live in a **row** rather than in code,
that a support grant is per-key rather than wholesale, and that an unknown quota name is an error —
because each of those, done the other way, fails quietly.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs.db.models import Plan, User
from libs.tenant.quotas import FREE_LIMITS, QuotaExceeded, Quotas, quotas_for

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s, **kw) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", **kw)
    s.add(user)
    s.flush()
    return user


def test_the_free_plan_row_matches_the_code_that_documents_it(ctx):
    """The migration seeds the row and `FREE_LIMITS` describes it. If they drift, a database built
    from migrations and one built from the models disagree about what `free` means — and only one
    of them is what production runs."""
    stored = ctx.scalar(select(Plan.limits).where(Plan.id == "free"))
    assert stored == pytest.approx(FREE_LIMITS)


def test_every_account_resolves_to_a_plan(ctx):
    """An account with no plan would resolve to no limits at all, which is the opposite of the
    intended failure direction."""
    user = _user(ctx)
    resolved = quotas_for(ctx, user.id)
    assert resolved.plan_id == "free"
    assert resolved.limit("cv_builds_per_day") == FREE_LIMITS["cv_builds_per_day"]


def test_a_support_grant_raises_one_limit_and_leaves_the_rest(ctx):
    """Per key, not wholesale. Granting one person extra CV builds must not silently reset every
    other limit to whatever the grant happened to include."""
    user = _user(ctx, quota_overrides={"cv_builds_per_day": 50})
    resolved = quotas_for(ctx, user.id)

    assert resolved.limit("cv_builds_per_day") == 50
    assert resolved.limit("notifications_per_day") == FREE_LIMITS["notifications_per_day"]


def test_going_over_names_the_quota_and_the_reset(ctx):
    """Refuse loudly (`TENANT_SAFETY.md` §3): silent truncation is how a user concludes the product
    is broken rather than that they hit a limit."""
    resolved = Quotas("free", dict(FREE_LIMITS))
    with pytest.raises(QuotaExceeded) as exc:
        resolved.check("cv_builds_per_day", used=10)

    assert exc.value.quota == "cv_builds_per_day"
    assert exc.value.limit == 10
    assert "resets" in str(exc.value)


def test_the_boundary_itself_is_allowed():
    """Off-by-one on a quota means the tenth CV build of ten fails."""
    resolved = Quotas("free", dict(FREE_LIMITS))
    resolved.check("cv_builds_per_day", used=9)          # the tenth is fine
    with pytest.raises(QuotaExceeded):
        resolved.check("cv_builds_per_day", used=10)     # the eleventh is not


def test_an_unknown_quota_name_is_an_error_not_unlimited():
    """A typo'd name defaulting to "no limit" silently removes a limit somebody believed was being
    enforced — the one failure mode a quota system must not have."""
    resolved = Quotas("free", dict(FREE_LIMITS))
    with pytest.raises(KeyError):
        resolved.limit("cv_builds_per_week")
