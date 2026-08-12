"""The typed half of a profile, and that the setup agent always sees it."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.db.models import User
from libs.profiles import harddata
from libs.tenant.repository import TenantRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                timezone="Europe/Budapest", full_name="Alice Example",
                links=[{"url": "https://github.com/alice", "platform": "github"},
                       {"url": "https://alice.dev", "platform": "other"}])
    session.add(user)
    session.flush()
    try:
        yield session, user
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_it_carries_what_the_person_typed(ctx):
    """The values that were going stale: their name and every link, not a subset."""
    session, user = ctx
    TenantRepository(session).save_config(
        user.id, {"sections": [{"keywords": ["python developer", "backend engineer"],
                                "locations": ["london"], "exclude_keywords": ["php"]}],
                  "languages": ["en", "hu"], "seniority": ["senior"]})
    session.flush()

    text = harddata.render(session, user.id)

    assert "Alice Example" in text
    # **Every** link. The profile copy of one account held one of its three, which is the defect.
    assert "https://github.com/alice" in text
    assert "https://alice.dev" in text
    assert user.email in text
    assert "python developer" in text and "backend engineer" in text
    assert "php" in text and "london" in text
    assert "en" in text and "senior" in text


def test_it_says_which_source_wins(ctx):
    """A document and the form disagreed on one account's name — accented in one, not the other.

    The agent has to be told which is authoritative or it has no way to choose.
    """
    session, user = ctx
    text = harddata.render(session, user.id).lower()

    assert "typed themselves" in text
    assert "this is the one that is right" in text


def test_an_account_with_nothing_typed_yet_still_renders(ctx):
    """A brand-new account must not break the conversation that is about to fill it in."""
    session, _user = ctx
    fresh = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active")
    session.add(fresh)
    session.flush()

    text = harddata.render(session, fresh.id)

    assert "not given yet" in text
    assert "not set yet" in text


def test_a_config_stored_in_the_old_shape_is_read_in_the_current_one(ctx):
    """`migrate_config` exists because v1 had no `sections`; this must not show a user raw v1."""
    session, user = ctx
    session.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO user_config (user_id, config, schema_version) "
            "VALUES (:u, '{\"keywords\": [\"golang\"]}'::jsonb, 1)"),
        {"u": user.id})
    session.flush()

    assert "golang" in harddata.render(session, user.id)
