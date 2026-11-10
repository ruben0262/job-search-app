"""The three GDPR obligations, as behaviour rather than as claims in a document."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ.setdefault("SECRETS_KEK", "k1:" + "A" * 43 + "=")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.auth.passwords import hash_password
    from libs.db import insert
    from libs.db.models import User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash=hash_password(PASSWORD),
                           status="active", full_name="Alice Example"))

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


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

def test_consent_to_a_superseded_disclosure_is_refused(api):
    """A policy naming a new processor is a new disclosure, and an old agreement is not an answer to it."""
    http, _, _ = api
    response = http.post("/account/consent", json={"version": "1999-01-01"})
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_adding_a_processor_re_asks_everyone(api):
    """The mechanism, not the intention: consent is version-compared."""
    from libs.db.models import User

    http, db, user = api
    http.post("/account/consent",
              json={"version": http.get("/account/consent").json()["current_version"]})
    assert http.get("/account/consent").json()["granted"] is True

    # What a bumped disclosure leaves behind.
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"processing_consent_version": "2020-01-01"}})
    assert http.get("/account/consent").json()["granted"] is False


def test_withdrawing_says_what_it_does_not_undo(api):
    """A profile already built stays built, and what Anthropic processed cannot be recalled."""
    http, _db, _ = api
    http.post("/account/consent",
              json={"version": http.get("/account/consent").json()["current_version"]})

    body = http.delete("/account/consent").json()
    assert body["status"] == "withdrawn"
    assert "unchanged" in body["detail"]
    assert http.get("/account/consent").json()["granted"] is False


# ── erasure ───────────────────────────────────────────────────────────────────────────────────

def test_deleting_an_account_leaves_no_row_anywhere(api):
    """The obligation, proved rather than asserted: a document in every collection an account owns,
    and none of them left afterwards."""
    from datetime import timedelta

    from libs.db import get, insert, now
    from libs.db.models import (
        MODELS,
        AiRequest,
        AuthToken,
        BuiltDocument,
        ContactMessage,
        CorpusJob,
        PushSubscription,
        QueueJob,
        SetupConversation,
        SetupMessage,
        UploadedFile,
        User,
        UserConfig,
        UserDocument,
        UserJobState,
        UserNotification,
        UserProfileFile,
        UserSecret,
        UserSession,
    )

    http, db, user = api
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer"))
    someone_else = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))
    insert(db, UserConfig(user_id=someone_else.id, config={"sections": [{"keywords": ["y"]}]}))

    conversation = insert(db, SetupConversation(user_id=user.id))
    build = insert(db, QueueJob(lane="docgen", user_id=user.id, payload={}))
    for document in (
            UserConfig(user_id=user.id, config={"sections": [{"keywords": ["x"]}]}),
            UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text="x",
                         text_sha256=uuid.uuid4().hex * 2, chars=1),
            UserSecret(user_id=user.id, kind="claude_api", ciphertext=b"x", nonce=b"n"),
            UserSession(user_id=user.id, refresh_hash=uuid.uuid4().hex, expires_at=now() + timedelta(days=1)),
            AuthToken(id=uuid.uuid4().hex, user_id=user.id, purpose="verify_email",
                      expires_at=now() + timedelta(days=1)),
            UserJobState(user_id=user.id, job_id=job.id),
            SetupMessage(user_id=user.id, conversation_id=conversation.id, role="user", content="hi"),
            UserNotification(user_id=user.id, event="test", title="hello"),
            PushSubscription(user_id=user.id, endpoint=f"https://push.example.com/{uuid.uuid4()}",
                             p256dh="k", auth="a"),
            UserProfileFile(user_id=user.id, name="context.md", content="x", kind="context"),
            AiRequest(user_id=user.id),
            BuiltDocument(id=build.id, user_id=user.id, job_id=job.id, kind="cv", mode="instant",
                          filename="cv.pdf", content=b"%PDF", size=4, saved=True),
            ContactMessage(user_id=user.id, email=user.email, subject="Hello",
                           message="A question for you."),
            UploadedFile(id=build.id, user_id=user.id, content=b"the CV as uploaded")):
        insert(db, document)

    owned = [model for model in MODELS if model.OWNER]
    unseeded = [model.COLLECTION for model in owned
                if not db[model.COLLECTION].count_documents({model.OWNER: user.id})]
    assert not unseeded, f"seed these too, or the erasure below proves nothing about them: {unseeded}"

    assert http.request("DELETE", "/account", json={"password": PASSWORD}).status_code == 200

    for model in owned:
        remaining = db[model.COLLECTION].count_documents({model.OWNER: user.id})
        assert remaining == 0, f"{model.COLLECTION} still holds {remaining} documents for a deleted account"
    assert get(db, User, user.id) is None
    assert get(db, BuiltDocument, build.id) is None, "a built CV goes with the account that asked for it"

    # The shared corpus is untouched: one user leaving must not remove another user's job.
    assert get(db, CorpusJob, job.id) is not None
    assert get(db, User, someone_else.id) is not None
    assert db[UserConfig.COLLECTION].count_documents({"user_id": someone_else.id}) == 1


def test_deleting_requires_the_password_not_just_a_session(api):
    """Irreversible, and it unwinds an account somebody spent real money building."""
    from libs.db import get
    from libs.db.models import User

    http, db, user = api
    response = http.request("DELETE", "/account", json={"password": "not the password"})

    assert response.status_code == 401
    assert get(db, User, user.id) is not None


def test_changing_the_password_needs_the_old_one_and_ends_every_session(api):
    """The reference's semantics (`security/handler.go`)."""
    from libs.auth.passwords import verify_password
    from libs.auth.sessions import issue_session
    from libs.db import get
    from libs.db.models import User, UserSession

    http, db, user = api
    live = issue_session(db, user.id)

    response = http.put("/account/password", json={
        "current_password": PASSWORD, "new_password": "an entirely different phrase"})
    assert response.status_code == 200
    assert response.json()["force_relogin"] is True
    assert verify_password(get(db, User, user.id).password_hash, "an entirely different phrase")
    assert get(db, UserSession, live.session_id).revoked_at is not None


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
    from libs.db.models import User

    http, db, user = api
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"active": True}})

    # Ignored, not honoured: the field is not part of the request model any more.
    assert http.patch("/account", json={"notify_interval_min": 30}).status_code == 200
    assert "notify_interval_min" not in db[User.COLLECTION].find_one({"_id": user.id})

    assert http.patch("/account", json={"active": False}).status_code == 200
    assert db[User.COLLECTION].find_one({"_id": user.id})["active"] is False


def test_the_settings_page_never_returns_a_credential(api):
    """It shows everything onboarding collected, and the credentials are write-only everywhere."""
    http, _, _ = api
    body = http.get("/account").json()
    assert "password_hash" not in body
    assert not any("secret" in key or "token" in key for key in body)
