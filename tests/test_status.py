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

    assert set(components) == {"telegram_bot", "telegram_channel", "harvester", "scorer", "docgen"}
    for component in components.values():
        assert component["state"] in ("ok", "warn", "down", "off")
        assert component["detail"], "a dot with no words is a support request"

    assert components["telegram_bot"]["state"] == "ok"
    assert components["harvester"]["state"] == "ok"
    # No model yet is off, not broken — the honest state for a new account.
    assert components["scorer"]["state"] == "off"


def test_a_broken_bot_is_red_with_the_fix_in_words(api):
    http, session, user = api
    user.telegram_status = "broken"
    session.flush()

    bot = next(c for c in http.get("/status").json()["components"] if c["key"] == "telegram_bot")
    assert bot["state"] == "down"
    assert "token" in bot["detail"]
