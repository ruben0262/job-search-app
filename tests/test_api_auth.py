"""The auth endpoints, through the real app.

Narrow on purpose. What is worth pinning is the handful of things that are wrong in a way nobody
notices: the endpoint must not reveal which addresses have accounts, an unverified account must not
be able to log in, the refresh cookie must be scoped so an XSS elsewhere cannot reach it, and a
replayed refresh must end the session rather than quietly working.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs.db.models import AuthToken, User
from libs.mail import ConsoleSender

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture()
def client():
    """The real app, with the database session and mailer replaced.

    One session across every request in a test, rolled back at the end — so a signup is visible to
    the login that follows it without committing anything to the live database.
    """
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    mailer = ConsoleSender()

    app.dependency_overrides[deps.get_db] = lambda: session
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    try:
        with TestClient(app, base_url="https://testserver") as http:
            yield http, session, mailer
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def _email() -> str:
    # `example.com` rather than the `.test` the other suites use: `.test` is a reserved special-use
    # domain and `email_validator` rejects it, which is the validator doing its job. The rest of the
    # tree writes User rows directly and never passes an address through validation.
    return f"{uuid.uuid4()}@example.com"


def _signup(http, email: str, password: str = PASSWORD):
    return http.post("/auth/signup", json={"email": email, "password": password})


def _verify_token(session, email: str) -> str | None:
    """Pull the token's *hash* owner back out — the plaintext only exists in the email."""
    user_id = session.scalar(select(User.id).where(User.email == email))
    return session.scalar(select(AuthToken.token_hash).where(AuthToken.user_id == user_id))


def _link_token(mailer: ConsoleSender) -> str:
    (_, _, body) = mailer.sent[-1]
    return body.split("token=")[1].split()[0]


# ── the account-existence oracle ──────────────────────────────────────────────────────────────

def test_signup_answers_identically_for_a_new_and_an_existing_address(client):
    """Differing responses turn the endpoint into a way to test whether someone has an account
    here, which for an invite-only product is exactly what it must not be."""
    http, session, _ = client
    email = _email()

    first = _signup(http, email)
    session.flush()
    second = _signup(http, email)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_a_login_for_an_unknown_address_looks_like_a_wrong_password(client):
    http, _, _ = client
    unknown = http.post("/auth/login", json={"email": _email(), "password": PASSWORD})
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "email or password is incorrect"


def test_a_weak_password_is_refused_with_its_reasons(client):
    """The rules are about the value the caller just chose, not about anyone else's account — so
    unlike everything else here, they are told plainly."""
    http, _, _ = client
    response = _signup(http, _email(), "short")
    assert response.status_code in {400, 422}


# ── verification ──────────────────────────────────────────────────────────────────────────────

def test_an_unverified_account_cannot_log_in(client):
    """Otherwise the verification step is decorative: anyone could sign up with an address they do
    not control and use the account anyway."""
    http, session, _ = client
    email = _email()
    _signup(http, email)
    session.flush()

    response = http.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 403
    assert "confirm your email" in response.json()["detail"]


def test_verification_activates_the_account_and_works_once(client):
    http, session, mailer = client
    email = _email()
    _signup(http, email)
    session.flush()
    token = _link_token(mailer)

    assert http.post("/auth/verify", json={"token": token}).status_code == 200
    session.flush()
    assert http.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200

    replayed = http.post("/auth/verify", json={"token": token})
    assert replayed.status_code == 400


def test_verification_does_not_log_the_user_in(client):
    """A verification link travels through email and can be forwarded. Proving control of an
    address should not hand out a session — that still requires the password."""
    http, session, mailer = client
    _signup(http, _email())
    session.flush()

    response = http.post("/auth/verify", json={"token": _link_token(mailer)})
    assert "access_token" not in response.cookies
    assert "refresh_token" not in response.cookies


def test_the_emailed_token_is_not_what_is_stored(client):
    """A database dump must not let anyone verify an address."""
    http, session, mailer = client
    email = _email()
    _signup(http, email)
    session.flush()
    assert _link_token(mailer) != _verify_token(session, email)


# ── cookies ───────────────────────────────────────────────────────────────────────────────────

def _logged_in(http, session, mailer):
    email = _email()
    _signup(http, email)
    session.flush()
    http.post("/auth/verify", json={"token": _link_token(mailer)})
    session.flush()
    return email, http.post("/auth/login", json={"email": email, "password": PASSWORD})


def test_the_refresh_cookie_is_scoped_to_the_refresh_path(client):
    """The detail most implementations miss (`AUTH_REUSE.md` §3). Scoped to one path, the
    long-lived credential is absent from every ordinary request, so an XSS anywhere else cannot
    reach it."""
    http, session, mailer = client
    _, response = _logged_in(http, session, mailer)
    assert response.status_code == 200

    # Compared case-insensitively throughout: cookie attribute names are case-insensitive per
    # RFC 6265 and this client hands them back lowercased, so matching on capitalisation would be
    # asserting on the client's formatting rather than on the policy.
    cookies = "; ".join(response.headers.get_list("set-cookie")).lower()
    assert "refresh_token=" in cookies
    assert "path=/auth/refresh" in cookies
    assert "samesite=strict" in cookies


def test_both_cookies_are_httponly_and_secure(client):
    """Unconditionally, not depending on the environment — a development exception is how the
    production path ends up untested."""
    http, session, mailer = client
    _, response = _logged_in(http, session, mailer)
    for header in response.headers.get_list("set-cookie"):
        assert "httponly" in header.lower()
        assert "secure" in header.lower()


# ── sessions ──────────────────────────────────────────────────────────────────────────────────

def test_refresh_rotates_the_token(client):
    http, session, mailer = client
    _logged_in(http, session, mailer)
    session.flush()
    before = http.cookies.get("refresh_token", path="/auth/refresh")

    response = http.post("/auth/refresh")
    session.flush()
    assert response.status_code == 200
    assert http.cookies.get("refresh_token", path="/auth/refresh") != before


def test_a_replayed_refresh_token_ends_the_session_and_clears_the_cookies(client):
    """Either the client is buggy or the token was stolen. On the second reading both holders must
    end up with nothing, and the client must stop retrying a credential that will never work."""
    http, session, mailer = client
    _logged_in(http, session, mailer)
    session.flush()
    stolen = http.cookies.get("refresh_token", path="/auth/refresh")

    http.post("/auth/refresh")
    session.flush()

    replay = http.post("/auth/refresh", cookies={"refresh_token": stolen})
    assert replay.status_code == 401
    cleared = [h.lower() for h in replay.headers.get_list("set-cookie")]
    assert any("refresh_token=" in h and "max-age=0" in h for h in cleared)


def test_logout_ends_the_session(client):
    http, session, mailer = client
    _logged_in(http, session, mailer)
    session.flush()

    assert http.post("/auth/logout").status_code == 200
    session.flush()
    assert http.post("/auth/refresh").status_code == 401


def test_logging_out_twice_is_not_an_error(client):
    """A user who clicks logout on a stale tab should see a logged-out page, not an error."""
    http, session, mailer = client
    _logged_in(http, session, mailer)
    session.flush()
    http.post("/auth/logout")
    session.flush()
    assert http.post("/auth/logout").status_code == 200


# ── the sending cap ───────────────────────────────────────────────────────────────────────────

def test_the_daily_cap_stops_sending_without_breaking_signup(client, monkeypatch):
    """Gmail's own limit fails silently — messages stop arriving and nothing raises. A cap the
    platform enforces turns that into a refusal it can see.

    Signup still succeeds: the account exists and another link can be requested, and a provider
    limit must not become a closed front door.
    """
    http, session, mailer = client
    monkeypatch.setenv("MAIL_DAILY_CAP", "1")

    first = _signup(http, _email())
    session.flush()
    second = _signup(http, _email())
    session.flush()

    assert first.status_code == second.status_code == 202
    assert len(mailer.sent) == 1, "the second send was refused by the cap"


def test_the_cap_counts_attempts_not_successes(client, monkeypatch):
    """A provider outage would otherwise burn the allowance twice — once failing, once retrying —
    and the provider counts attempts."""
    from libs.mail import MailError, sent_last_24h

    http, session, mailer = client
    monkeypatch.setenv("MAIL_DAILY_CAP", "50")

    def failing(to, subject, body):
        raise MailError("smtp said no")

    monkeypatch.setattr(mailer, "send", failing)
    before = sent_last_24h(session)
    assert _signup(http, _email()).status_code == 202
    session.flush()
    assert sent_last_24h(session) == before + 1


def test_no_recipient_address_is_stored(client):
    """A cap needs a count, not a mailing list. This table must never become one more place a
    user's email lives, or one more thing an account deletion has to reach into."""
    from libs.db.models import MailSend

    columns = set(MailSend.__table__.columns.keys())
    assert not (columns & {"to_email", "recipient", "email", "address"})


# ── forgot / reset ────────────────────────────────────────────────────────────────────────────

def test_forgot_answers_identically_for_a_known_and_unknown_address(client):
    """The same no-oracle rule as login: a reset form must not confirm who has an account."""
    http, session, _mailer = client
    email = _email()
    _signup(http, email)
    session.flush()

    known = http.post("/auth/forgot", json={"email": email})
    unknown = http.post("/auth/forgot", json={"email": _email()})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_the_emailed_links_point_at_the_spa_routes_not_the_api(client):
    """`/auth/verify` is a POST-only API path; a mail client GETs, and would land on a 405. The
    first real signup would have found this."""
    http, session, mailer = client
    email = _email()
    _signup(http, email)
    assert "/#/verify?token=" in mailer.sent[-1][2]

    session.flush()
    http.post("/auth/forgot", json={"email": email})
    assert "/#/reset?token=" in mailer.sent[-1][2]


def test_reset_sets_the_password_ends_every_session_and_does_not_log_in(client):
    http, session, mailer = client
    email, _ = _logged_in(http, session, mailer)

    http.post("/auth/forgot", json={"email": email})
    token = _link_token(mailer)
    response = http.post("/auth/reset", json={"token": token,
                                              "new_password": "an entirely different phrase"})
    assert response.status_code == 200
    assert "set-cookie" not in {k.lower() for k in response.headers}

    from libs.db.models import UserSession
    user_id = session.scalar(select(User.id).where(User.email == email))
    live = session.query(UserSession).filter(UserSession.user_id == user_id,
                                             UserSession.revoked_at.is_(None)).count()
    assert live == 0

    old = http.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert old.status_code == 401
    new = http.post("/auth/login", json={"email": email,
                                         "password": "an entirely different phrase"})
    assert new.status_code == 200


def test_a_reset_token_works_once_and_a_verification_token_is_not_a_reset_token(client):
    """"Can receive email" must not become "can take the account" through the wrong door."""
    http, _session, mailer = client
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


def test_the_console_sender_shows_the_link_a_developer_must_click(capsys):
    """The auth routes send inside `redacting(token)`, which scrubs tokens from every log record —
    so the console fallback prints to stdout, or local signup is unfinishable."""
    from libs.mail.sender import ConsoleSender

    sender = ConsoleSender()
    sender.send("dev@example.com", "Confirm your email", "  http://localhost/#/verify?token=abc123")
    out = capsys.readouterr().out
    assert "token=abc123" in out
