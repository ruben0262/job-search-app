"""The auth endpoints, through the real app."""
from __future__ import annotations

import os
import uuid
from dataclasses import fields

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient

from libs.db import find_one
from libs.db.models import AuthToken, User, UserSession
from libs.mail import ConsoleSender

PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture()
def client(db, monkeypatch):
    """The real app on the test database, with the mailer replaced."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from services.api import deps
    from services.api.app import app

    mailer = ConsoleSender()
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    try:
        with TestClient(app, base_url="https://testserver") as http:
            yield http, db, mailer
    finally:
        app.dependency_overrides.clear()


def _email() -> str:
    # `example.com` rather than the `.test` the other suites use.
    return f"{uuid.uuid4()}@example.com"


def _signup(http, email: str, password: str = PASSWORD):
    return http.post("/auth/signup", json={"email": email, "password": password})


def _verify_token(db, email: str) -> str | None:
    """Pull the token's *hash* owner back out — the plaintext only exists in the email."""
    user = find_one(db, User, {"email": email})
    token = find_one(db, AuthToken, {"user_id": user.id})
    return token.id if token else None


def _link_token(mailer: ConsoleSender) -> str:
    (_, _, body) = mailer.sent[-1]
    return body.split("token=")[1].split()[0]


# ── the account-existence oracle ──────────────────────────────────────────────────────────────

def test_signup_answers_identically_for_a_new_and_an_existing_address(client):
    """Differing responses turn the endpoint into a way to test whether someone has an account here."""
    http, _db, _ = client
    email = _email()

    first = _signup(http, email)
    second = _signup(http, email)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_a_login_for_an_unknown_address_looks_like_a_wrong_password(client):
    http, _, _ = client
    unknown = http.post("/auth/login", json={"email": _email(), "password": PASSWORD})
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "email or password is incorrect"


def test_a_weak_password_is_refused_with_its_reasons(client):
    """The rules are about the value the caller just chose, not about anyone else's account."""
    http, _, _ = client
    response = _signup(http, _email(), "short")
    assert response.status_code in {400, 422}


# ── verification ──────────────────────────────────────────────────────────────────────────────

def test_an_unverified_account_cannot_log_in(client):
    """Otherwise the verification step is decorative."""
    http, _db, _ = client
    email = _email()
    _signup(http, email)

    response = http.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 403
    assert "confirm your email" in response.json()["detail"]


def test_verification_activates_the_account_and_works_once(client):
    http, _db, mailer = client
    email = _email()
    _signup(http, email)
    token = _link_token(mailer)

    assert http.post("/auth/verify", json={"token": token}).status_code == 200
    assert http.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200

    replayed = http.post("/auth/verify", json={"token": token})
    assert replayed.status_code == 400


def test_signing_in_records_when_the_account_was_last_active(client):
    """The accounts list is the whole of account lifecycle now, and this is what fills it.

    Nothing pauses a quiet account any more (ROADMAP 6c) — an operator reads `last_active_at` and
    decides. If the login path stops stamping it, every account reads as never-used and the decision
    is made on a column that is always null.
    """
    http, db, mailer = client
    email = _email()
    _signup(http, email)
    user = find_one(db, User, {"email": email})
    assert user.last_active_at is None, "signing up is not using the product"

    # The link signs you in (2026-10-28), so it counts the way a sign-in does.
    assert http.post("/auth/verify", json={"token": _link_token(mailer)}).status_code == 200
    assert find_one(db, User, {"email": email}).last_active_at is not None

    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"last_active_at": None}})
    assert http.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert find_one(db, User, {"email": email}).last_active_at is not None


def test_verifying_signs_you_in(client):
    """The emailed link proves the inbox, as a password would, so it lands straight in the app.

    The opposite was tested until 2026-10-28, because a link can be forwarded. It is single-use and
    lives a day; making a new person sign in by hand straight after proving their address was the
    second dead end the browser audit found.
    """
    http, _db, mailer = client
    _signup(http, _email())
    assert http.get("/account").status_code == 401, "the premise: not signed in yet"

    response = http.post("/auth/verify", json={"token": _link_token(mailer)})

    assert response.status_code == 200
    assert {"access_token", "refresh_token"} <= {c.name for c in http.cookies.jar}
    assert http.get("/account").status_code == 200


def test_a_suspended_account_is_not_signed_in_by_its_link(client):
    """The same refusal login gives, or a link would be a way round a suspension."""
    http, db, mailer = client
    email = _email()
    _signup(http, email)
    db[User.COLLECTION].update_one({"email": email}, {"$set": {"status": "suspended"}})

    assert http.post("/auth/verify", json={"token": _link_token(mailer)}).status_code == 403
    assert "access_token" not in {c.name for c in http.cookies.jar}


def test_a_new_link_can_be_sent_and_only_the_newest_works(client):
    """"Check your email" had no way to send another, and its advice — sign up again — sent nothing."""
    http, _db, mailer = client
    email = _email()
    _signup(http, email)
    first = _link_token(mailer)

    response = http.post("/auth/resend", json={"email": email})

    assert response.status_code == 202
    assert len(mailer.sent) == 2, "a second email went out"
    assert http.post("/auth/verify", json={"token": first}).status_code == 400
    assert http.post("/auth/verify", json={"token": _link_token(mailer)}).status_code == 200


def test_resend_answers_the_same_whoever_asks(client):
    """No address oracle, and nothing sent to an address that is not waiting for a link."""
    http, _db, mailer = client
    verified = _email()
    _signup(http, verified)
    http.post("/auth/verify", json={"token": _link_token(mailer)})
    sent = len(mailer.sent)

    unknown = http.post("/auth/resend", json={"email": _email()})
    known = http.post("/auth/resend", json={"email": verified})

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert len(mailer.sent) == sent


def test_the_emailed_token_is_not_what_is_stored(client):
    """A database dump must not let anyone verify an address."""
    http, db, mailer = client
    email = _email()
    _signup(http, email)
    assert _link_token(mailer) != _verify_token(db, email)


# ── cookies ───────────────────────────────────────────────────────────────────────────────────

def _logged_in(http, mailer):
    email = _email()
    _signup(http, email)
    http.post("/auth/verify", json={"token": _link_token(mailer)})
    return email, http.post("/auth/login", json={"email": email, "password": PASSWORD})


def test_the_refresh_cookie_is_scoped_to_the_refresh_path(client):
    """The detail most implementations miss (`AUTH_REUSE.md` §3)."""
    http, _db, mailer = client
    _, response = _logged_in(http, mailer)
    assert response.status_code == 200

    # Compared case-insensitively throughout.
    cookies = "; ".join(response.headers.get_list("set-cookie")).lower()
    assert "refresh_token=" in cookies
    assert "path=/auth/refresh" in cookies
    assert "samesite=strict" in cookies


def test_both_cookies_are_httponly_and_secure(client):
    """Unconditionally, not depending on the environment."""
    http, _db, mailer = client
    _, response = _logged_in(http, mailer)
    for header in response.headers.get_list("set-cookie"):
        assert "httponly" in header.lower()
        assert "secure" in header.lower()


# ── sessions ──────────────────────────────────────────────────────────────────────────────────

def test_refresh_rotates_the_token(client):
    http, _db, mailer = client
    _logged_in(http, mailer)
    before = http.cookies.get("refresh_token", path="/auth/refresh")

    response = http.post("/auth/refresh")
    assert response.status_code == 200
    assert http.cookies.get("refresh_token", path="/auth/refresh") != before


def test_a_replayed_refresh_token_ends_the_session_and_clears_the_cookies(client):
    """Either the client is buggy or the token was stolen."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    stolen = http.cookies.get("refresh_token", path="/auth/refresh")

    http.post("/auth/refresh")

    replay = http.post("/auth/refresh", cookies={"refresh_token": stolen})
    assert replay.status_code == 401
    cleared = [h.lower() for h in replay.headers.get_list("set-cookie")]
    assert any("refresh_token=" in h and "max-age=0" in h for h in cleared)


def test_logout_ends_the_session(client):
    http, _db, mailer = client
    _logged_in(http, mailer)

    assert http.post("/auth/logout").status_code == 200
    assert http.post("/auth/refresh").status_code == 401


def test_logging_out_twice_is_not_an_error(client):
    """A user who clicks logout on a stale tab should see a logged-out page, not an error."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    http.post("/auth/logout")
    assert http.post("/auth/logout").status_code == 200


# ── the sending cap ───────────────────────────────────────────────────────────────────────────

def test_the_daily_cap_stops_sending_without_breaking_signup(client, monkeypatch):
    """Gmail's own limit fails silently — messages stop arriving and nothing raises."""
    http, _db, mailer = client
    monkeypatch.setenv("MAIL_DAILY_CAP", "1")

    first = _signup(http, _email())
    second = _signup(http, _email())

    assert first.status_code == second.status_code == 202
    assert len(mailer.sent) == 1, "the second send was refused by the cap"


def test_a_send_that_failed_is_not_counted_against_the_cap(client, monkeypatch):
    """The cap stands in for the provider's own allowance, and a send the provider refused did not use
    any of it. Changed by design on the move to MongoDB (it counted attempts before); signup still
    answers, and the account is still there to ask for another link."""
    from libs.mail import MailError, sent_last_24h

    http, db, mailer = client
    monkeypatch.setenv("MAIL_DAILY_CAP", "50")

    outage, send = [True], mailer.send

    def flaky(to, subject, body, **kwargs):
        if outage[0]:
            raise MailError("smtp said no")
        send(to, subject, body, **kwargs)

    monkeypatch.setattr(mailer, "send", flaky)
    before = sent_last_24h(db)
    email = _email()
    assert _signup(http, email).status_code == 202
    assert sent_last_24h(db) == before
    assert find_one(db, User, {"email": email}) is not None

    outage[0] = False
    assert http.post("/auth/resend", json={"email": email}).status_code == 202
    assert sent_last_24h(db) == before + 1, "a send that went out is counted"


def test_no_recipient_address_is_stored(client):
    """A cap needs a count, not a mailing list."""
    from libs.db.models import MailSend

    http, db, mailer = client
    email = _email()
    _signup(http, email)
    assert mailer.sent, "the premise: a mail went out"

    assert not ({field.name for field in fields(MailSend)} & {"to_email", "recipient", "email", "address"})
    stored = list(db[MailSend.COLLECTION].find())
    assert stored and not any(email in str(row) for row in stored)


# ── forgot / reset ────────────────────────────────────────────────────────────────────────────

def test_forgot_answers_identically_for_a_known_and_unknown_address(client):
    """The same no-oracle rule as login: a reset form must not confirm who has an account."""
    http, _db, _mailer = client
    email = _email()
    _signup(http, email)

    known = http.post("/auth/forgot", json={"email": email})
    unknown = http.post("/auth/forgot", json={"email": _email()})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_the_emailed_links_point_at_the_spa_routes_not_the_api(client):
    """`/auth/verify` is a POST-only API path; a mail client GETs, and would land on a 405."""
    http, _db, mailer = client
    email = _email()
    _signup(http, email)
    assert "/#/verify?token=" in mailer.sent[-1][2]

    http.post("/auth/forgot", json={"email": email})
    assert "/#/reset?token=" in mailer.sent[-1][2]


def test_reset_sets_the_password_ends_every_session_and_does_not_log_in(client):
    http, db, mailer = client
    email, _ = _logged_in(http, mailer)

    http.post("/auth/forgot", json={"email": email})
    token = _link_token(mailer)
    response = http.post("/auth/reset", json={"token": token,
                                              "new_password": "an entirely different phrase"})
    assert response.status_code == 200
    assert "set-cookie" not in {k.lower() for k in response.headers}

    user_id = find_one(db, User, {"email": email}).id
    assert db[UserSession.COLLECTION].count_documents({"user_id": user_id}) > 0, "the premise"
    live = db[UserSession.COLLECTION].count_documents({"user_id": user_id, "revoked_at": None})
    assert live == 0

    old = http.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert old.status_code == 401
    new = http.post("/auth/login", json={"email": email,
                                         "password": "an entirely different phrase"})
    assert new.status_code == 200


def test_a_reset_token_works_once_and_a_verification_token_is_not_a_reset_token(client):
    """"Can receive email" must not become "can take the account" through the wrong door."""
    http, _db, mailer = client
    email = _email()
    _signup(http, email)
    verify_token = _link_token(mailer)

    crossed = http.post("/auth/reset", json={"token": verify_token,
                                             "new_password": "an entirely different phrase"})
    assert crossed.status_code == 400

    http.post("/auth/verify", json={"token": verify_token})
    http.post("/auth/forgot", json={"email": email})
    reset_token = _link_token(mailer)
    first = http.post("/auth/reset", json={"token": reset_token,
                                           "new_password": "an entirely different phrase"})
    second = http.post("/auth/reset", json={"token": reset_token,
                                            "new_password": "yet another phrase entirely"})
    assert first.status_code == 200
    assert second.status_code == 400


def test_a_refused_new_password_leaves_the_reset_link_usable(client):
    """The password is checked before the link is spent: a person whose first choice is refused tries
    another on the same link, rather than being sent back to their inbox."""
    http, _db, mailer = client
    email, _ = _logged_in(http, mailer)
    http.post("/auth/forgot", json={"email": email})
    token = _link_token(mailer)

    refused = http.post("/auth/reset", json={"token": token, "new_password": "short"})
    assert refused.status_code == 400
    assert "password" in refused.json()["detail"]

    accepted = http.post("/auth/reset", json={"token": token,
                                              "new_password": "an entirely different phrase"})
    assert accepted.status_code == 200
    assert http.post("/auth/login", json={"email": email,
                                          "password": "an entirely different phrase"}).status_code == 200


def test_the_console_sender_shows_the_link_a_developer_must_click(capsys):
    """The auth routes send inside `redacting(token)`, which scrubs tokens from every log record."""
    from libs.mail.sender import ConsoleSender

    sender = ConsoleSender()
    sender.send("dev@example.com", "Confirm your email", "  http://localhost/#/verify?token=abc123")
    out = capsys.readouterr().out
    assert "token=abc123" in out
