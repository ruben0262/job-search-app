"""The three GDPR obligations, as behaviour rather than as claims in a document."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def password_hash():
    """The password as an account keeps it, hashed once for every test here: each hash takes 64 MiB and a
    thirtieth of a second by design (`libs.auth.passwords`)."""
    from libs.auth.passwords import hash_password

    return hash_password(PASSWORD)


@pytest.fixture(scope="module")
def running(_database):
    """The real app, started once for the tests here, not once a test: each start lays the whole schema
    again (`services.api.app.lifespan`), which was most of what a short test took."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ.setdefault("SECRETS_KEK", "k1:" + "A" * 43 + "=")

    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app, base_url="https://testserver") as http:
        yield http


@pytest.fixture()
def api(db, monkeypatch, password_hash, running):
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from libs.db import insert
    from libs.db.models import User
    from tests.conftest import access_cookie

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash=password_hash,
                           status="active", full_name="Alice Example"))

    # One browser for them all: whatever the test before left it holding is dropped first.
    running.cookies.clear()
    running.cookies.set("access_token", access_cookie(db, user.id), path="/")
    return running, db, user


# ── the legal pages ──────────────────────────────────────────────────────────────────────────

def test_the_privacy_policy_names_everyone_who_receives_a_persons_data(api):
    """By name, not as "trusted third parties": the entire requirement. Cloudflare carries every visit."""
    from libs.legal.disclosure import RECIPIENTS

    http, _, _ = api
    policy = http.get("/legal/privacy").json()["markdown"]

    assert {"Cloudflare", "Stripe", "Anthropic (Claude)", "Telegram"} <= {r.name for r in RECIPIENTS}
    for recipient in RECIPIENTS:
        assert recipient.name in policy
        assert recipient.purpose and recipient.data and recipient.terms


def test_every_legal_page_is_public(api):
    """Sign-up and the landing page link them before anybody has an account to sign in with."""
    http, _, _ = api
    http.cookies.clear()
    for name in ("terms", "privacy", "cookies", "data"):
        assert http.get(f"/legal/{name}").status_code == 200, name
    assert http.get("/legal/anything-else").status_code == 422


# ── the agreement, given at sign-up ───────────────────────────────────────────────────────────

def test_the_terms_are_agreed_to_at_sign_up_and_never_asked_for_again_inside_the_site(api):
    """Owner, 2026-11-18: "that's something users accept on sign up and that's it". An account that agreed to
    older terms, or has no agreement on record, goes straight to its own page, and nothing asks again."""
    from libs.db.models import User

    http, db, user = api
    for version in ("2020-01-01", None):
        db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"processing_consent_version": version}})
        assert http.get("/dashboard").json()["stage"] in ("plan", "onboarding", "ready"), version
    assert http.post("/account/consent", json={"version": "2026-11-18"}).status_code in (404, 405)


# ── erasure ───────────────────────────────────────────────────────────────────────────────────

def _a_row_in_every_collection(db, user):
    """A document in every collection an account owns, with the shared job and another person's account beside
    them: what erasing and exporting are each proved against. Fails when a collection is added unseeded."""
    from datetime import timedelta

    from libs.db import insert, now
    from libs.db.models import (
        MODELS,
        AiRequest,
        Alert,
        AuthToken,
        BuiltDocument,
        ContactMessage,
        CorpusJob,
        FitScore,
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
            UploadedFile(id=build.id, user_id=user.id, content=b"the CV as uploaded"),
            FitScore(user_id=user.id, job_id=job.id, fit=7.5, key="their profile", expires_at=now()),
            Alert(user_id=user.id, name="My search", search=True)):
        insert(db, document)

    unseeded = [model.COLLECTION for model in MODELS
                if model.OWNER and not db[model.COLLECTION].count_documents({model.OWNER: user.id})]
    assert not unseeded, f"seed these too, or what is proved below proves nothing about them: {unseeded}"
    return job, someone_else, build


def test_deleting_an_account_leaves_no_row_anywhere(api):
    """The obligation, proved rather than asserted: a document in every collection an account owns,
    and none of them left afterwards."""
    from libs.db import get
    from libs.db.models import MODELS, BuiltDocument, CorpusJob, User, UserConfig

    http, db, user = api
    job, someone_else, build = _a_row_in_every_collection(db, user)

    owned = [model for model in MODELS if model.OWNER]
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


def test_a_copy_of_an_account_holds_everything_deleting_would_erase_and_no_secret(api, capsys):
    """The copy the Privacy Policy and the Terms promise on request (GDPR Articles 15 and 20), made by the
    operator's script: every collection an account owns, as deleting walks them, only that account's rows, and
    nothing that would let anybody into it."""
    import importlib.util
    import json
    from pathlib import Path

    from libs.db.models import MODELS, User

    _http, db, user = api
    _a_row_in_every_collection(db, user)
    script = Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "export_account.py"
    spec = importlib.util.spec_from_file_location("export_account", script)
    export = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export)

    assert export.main([user.email.upper()]) == 0, "found by the address, however it is typed"
    copy = json.loads(capsys.readouterr().out)
    assert set(copy) == {model.COLLECTION for model in MODELS if model.OWNER} | {User.COLLECTION}
    (account,) = copy[User.COLLECTION]
    assert account["email"] == user.email and account["full_name"] == "Alice Example"
    assert not {"password_hash", "wrapped_dek"} & set(account), "nothing that lets anybody in"
    (secret,) = copy["user_secrets"]
    assert secret["kind"] == "claude_api" and not {"ciphertext", "nonce"} & set(secret)
    assert all(row["user_id"] == str(user.id) for name, rows in copy.items() if name != User.COLLECTION
               for row in rows), "only this account's rows"
    assert export.main(["nobody@example.com"]) == 1


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


def test_the_settings_page_never_returns_a_credential(api):
    """It shows everything onboarding collected, and the credentials are write-only everywhere."""
    http, _, _ = api
    body = http.get("/account").json()
    assert "password_hash" not in body
    assert not any("secret" in key or "token" in key for key in body)
