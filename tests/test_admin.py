"""The admin endpoints, and who is refused them.

The access rule is the whole of what is worth testing here: an operator view that leaks is worse
than no operator view, because it hands a stranger every account's state at once. So the tests are
about who *cannot* read it — including the case that looks like a bug and is not, the 404.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

ADMIN = "operator@example.com"


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"
    os.environ["ADMIN_EMAILS"] = ADMIN

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    operator = User(email=ADMIN, password_hash="x", status="active")
    ordinary = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add_all([operator, ordinary])
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session

    def sign_in(http, user):
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    try:
        with TestClient(app, base_url="https://testserver") as http:
            yield http, session, operator, ordinary, sign_in
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)
        os.environ.pop("ADMIN_EMAILS", None)


def test_an_operator_sees_the_platform_and_its_alerts(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/health").json()
    assert {"lanes", "corpus_rows", "alerts", "summary"} <= set(body)
    assert isinstance(body["alerts"], list)


def test_an_ordinary_user_gets_a_404_not_a_403(api):
    """A 403 confirms the endpoint exists and that somebody is worth phishing for access to it.
    There is no legitimate client that finds these routes by probing."""
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.get("/admin/health").status_code == 404
    assert http.get("/admin/users").status_code == 404


def test_admin_is_an_environment_variable_not_a_column(api):
    """Granting admin is a deploy, which is the friction it should have. A boolean column would be
    one UPDATE away from privilege escalation, audited in the database it grants access to."""
    http, session, operator, _, sign_in = api
    sign_in(http, operator)
    assert http.get("/admin/health").status_code == 200

    os.environ["ADMIN_EMAILS"] = "somebody-else@example.com"
    session.flush()
    assert http.get("/admin/health").status_code == 404


def test_no_admin_configured_means_nobody_is_an_admin(api):
    """An empty variable must not mean "everybody" — that is the misconfiguration that opens the
    console on the first deploy where somebody forgot to set it."""
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    os.environ["ADMIN_EMAILS"] = ""
    assert http.get("/admin/health").status_code == 404


def test_signed_out_is_refused(api):
    http, _, _, _, _ = api
    http.cookies.clear()
    assert http.get("/admin/health").status_code == 401


def test_the_user_list_carries_no_credential_material(api):
    """Not even `last_4`. An operator does not need it to answer any question they have, and a
    support view that shows a fragment of a key leaks one over somebody's shoulder."""
    from libs.db.models import UserSecret

    http, session, operator, ordinary, sign_in = api
    session.add(UserSecret(user_id=ordinary.id, kind="claude_api", ciphertext=b"x", nonce=b"n",
                           last_4="9999"))
    session.flush()
    sign_in(http, operator)

    body = http.get("/admin/users").text
    assert "9999" not in body
    assert "ciphertext" not in body and "last_4" not in body


def test_the_user_list_shows_what_an_operator_actually_needs(api):
    http, _, operator, ordinary, sign_in = api
    sign_in(http, operator)

    rows = {row["email"]: row for row in http.get("/admin/users").json()["users"]}
    assert ordinary.email in rows
    assert {"status", "telegram", "model_version", "onboarded"} <= set(rows[ordinary.email])
