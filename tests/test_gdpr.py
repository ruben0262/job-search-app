"""The three GDPR obligations, as behaviour rather than as claims in a document."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ.setdefault("SECRETS_KEK", "k1:" + "A" * 43 + "=")
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.auth.passwords import hash_password
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash=hash_password(PASSWORD),
                status="active", full_name="Alice Example")
    session.add(user)
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


# ── the disclosure ────────────────────────────────────────────────────────────────────────────

def test_the_policy_names_each_processor_rather_than_trusted_third_parties(api):
    """The entire requirement."""
    http, _, _ = api
    body = http.get("/legal/privacy").json()

    names = {processor["name"] for processor in body["processors"]}
    assert {"Anthropic", "Telegram"} <= names
    assert any("proxy" in name.lower() for name in names)
    assert all(processor["purpose"] and processor["data"] for processor in body["processors"])


def test_the_policy_is_public(api):
    """It is linked from signup, before anybody has an account to sign in with."""
    http, _, _ = api
    http.cookies.clear()
    assert http.get("/legal/privacy").status_code == 200


def test_the_consent_screen_and_the_policy_come_from_one_source():
    """Two copies drift, and the drift is invisible."""
    from libs.legal import disclosure
    from services.api.account_routes import privacy

    assert privacy() == disclosure()


# ── consent, at the moment it is owed ─────────────────────────────────────────────────────────

def test_processing_is_refused_before_consent(api):
    """The only place document text crosses a processor boundary, so the only place it is checked."""
    from libs.db.models import UserDocument, UserSecret

    http, session, user = api
    session.add(UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text="x",
                             text_sha256=uuid.uuid4().hex * 2, chars=1))
    session.add(UserSecret(user_id=user.id, kind="claude_api", ciphertext=b"x", nonce=b"n"))
    session.flush()

    response = http.post("/process")
    assert response.status_code == 403
    assert "Anthropic" in response.json()["detail"]

    http.post("/account/consent", json={"version": http.get("/account/consent").json()[
        "current_version"]})
    session.flush()
    assert http.post("/process").status_code == 200


def test_consent_to_a_superseded_disclosure_is_refused(api):
    """A policy naming a new processor is a new disclosure, and an old agreement is not an answer to it."""
    http, _, _ = api
    response = http.post("/account/consent", json={"version": "1999-01-01"})
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_adding_a_processor_re_asks_everyone(api):
    """The mechanism, not the intention: consent is version-compared."""
    http, session, user = api
    http.post("/account/consent",
              json={"version": http.get("/account/consent").json()["current_version"]})
    session.flush()
    assert http.get("/account/consent").json()["granted"] is True

    user.processing_consent_version = "2020-01-01"  # what a bumped disclosure leaves behind
    session.flush()
    assert http.get("/account/consent").json()["granted"] is False


def test_withdrawing_says_what_it_does_not_undo(api):
    """A profile already built stays built, and what Anthropic processed cannot be recalled."""
    http, session, _ = api
    http.post("/account/consent",
              json={"version": http.get("/account/consent").json()["current_version"]})
    session.flush()

    body = http.delete("/account/consent").json()
    session.flush()
    assert body["status"] == "withdrawn"
    assert "unchanged" in body["detail"]
    assert http.get("/account/consent").json()["granted"] is False


# ── erasure ───────────────────────────────────────────────────────────────────────────────────

def test_deleting_an_account_leaves_no_row_anywhere(api):
    """The obligation, proved rather than asserted."""
    from sqlalchemy import func, select

    from libs.db.base import Base
    from libs.db.models import (
        CorpusJob,
        QueueJob,
        UserConfig,
        UserDocument,
        UserJobState,
        UserLabel,
        UserSecret,
    )

    http, session, user = api
    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
    session.add(job)
    session.flush()

    # A row in as many of the user's tables as can be created directly.
    session.add(UserConfig(user_id=user.id, config={"sections": [{"keywords": ["x"]}]}))
    session.add(UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text="x",
                             text_sha256=uuid.uuid4().hex * 2, chars=1))
    session.add(UserSecret(user_id=user.id, kind="claude_api", ciphertext=b"x", nonce=b"n"))
    session.add(UserLabel(user_id=user.id, job_id=job.id, label=70))
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=70.0))
    session.add(QueueJob(lane="label", user_id=user.id, payload={}))
    session.flush()

    assert http.request("DELETE", "/account", json={"password": PASSWORD}).status_code == 200
    session.flush()

    with_user = [table for table in Base.metadata.sorted_tables if "user_id" in table.c]
    assert len(with_user) >= 9, "the enumeration found suspiciously few tables"
    for table in with_user:
        remaining = session.scalar(
            select(func.count()).select_from(table).where(table.c.user_id == user.id))
        assert remaining == 0, f"{table.name} still holds {remaining} rows for a deleted account"

    # The shared corpus is untouched: one user leaving must not remove another user's job.
    assert session.get(CorpusJob, job.id) is not None


def test_deleting_requires_the_password_not_just_a_session(api):
    """Irreversible, and it unwinds an account somebody spent real money building."""
    from libs.db.models import User

    http, session, user = api
    response = http.request("DELETE", "/account", json={"password": "not the password"})

    assert response.status_code == 401
    session.flush()
    assert session.get(User, user.id) is not None


def test_changing_the_password_needs_the_old_one_and_ends_every_session(api):
    """The reference's semantics (`security/handler.go`)."""
    from libs.auth.passwords import verify_password
    from libs.auth.sessions import issue_session
    from libs.db.models import UserSession

    http, session, user = api
    live = issue_session(session, user.id)

    response = http.put("/account/password", json={
        "current_password": PASSWORD, "new_password": "an entirely different phrase"})
    assert response.status_code == 200
    assert response.json()["force_relogin"] is True
    session.refresh(user)
    assert verify_password(user.password_hash, "an entirely different phrase")
    assert session.get(UserSession, live.session_id).revoked_at is not None


def test_changing_the_password_refuses_a_wrong_current_or_a_weak_new_one(api):
    http, _, _ = api
    wrong = http.put("/account/password", json={
        "current_password": "not it", "new_password": "an entirely different phrase"})
    assert wrong.status_code == 401

    weak = http.put("/account/password", json={
        "current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 400

    same = http.put("/account/password", json={
        "current_password": PASSWORD, "new_password": PASSWORD})
    assert same.status_code == 400


def test_the_delivery_interval_is_not_a_user_setting(api):
    """The cadence belongs to the platform (owner, 2026-08-01)."""
    http, session, user = api

    # Ignored, not honoured: the field is not part of the request model any more.
    assert http.patch("/account", json={"notify_interval_min": 30}).status_code == 200
    assert not hasattr(user, "notify_interval_min")

    assert http.patch("/account", json={"active": False}).status_code == 200
    session.flush()
    assert user.active is False


def test_the_settings_page_never_returns_a_credential(api):
    """It shows everything onboarding collected, and the credentials are write-only everywhere."""
    http, _, _ = api
    body = http.get("/account").json()
    assert "password_hash" not in body
    assert not any("secret" in key or "token" in key for key in body)
