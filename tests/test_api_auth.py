"""The auth endpoints, through the real app."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient

from libs.auth.sessions import ROTATION_GRACE
from libs.db import find_one, insert, now
from libs.db.models import AuthToken, User, UserSession
from libs.legal import LEGAL_VERSION
from libs.mail import ConsoleSender

PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture()
def client(db, monkeypatch, running):
    """The real app on the test database, with the mailer replaced."""
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from services.api import deps
    from services.api.app import app

    mailer = ConsoleSender()
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    # One browser for them all: whatever the test before left it holding is dropped first.
    running.cookies.clear()
    try:
        yield running, db, mailer
    finally:
        app.dependency_overrides.clear()


def _email() -> str:
    # `example.com` rather than the `.test` the other suites use.
    return f"{uuid.uuid4()}@example.com"


def _signup(http, email: str, password: str = PASSWORD, **more):
    """Signing up as the page does: the box ticked beside the policy it showed."""
    return http.post("/auth/signup", json={"email": email, "password": password,
                                           "consent_version": LEGAL_VERSION, **more})


def _verify_token(db, email: str) -> str | None:
    """Pull the token's *hash* owner back out — the plaintext only exists in the email."""
    user = find_one(db, User, {"email": email})
    token = find_one(db, AuthToken, {"user_id": user.id})
    return token.id if token else None


def _link(mailer: ConsoleSender) -> str:
    (_, _, body) = mailer.sent[-1]
    return next(word for word in body.split() if "token=" in word)


def _link_token(mailer: ConsoleSender) -> str:
    return _link(mailer).split("token=")[1].split("&")[0]


def _browser() -> TestClient:
    """Another browser on the same site, holding no cookies: a second device, or a stranger's."""
    from services.api.app import app
    return TestClient(app, base_url="https://testserver")


def _refresh_token(http) -> str:
    """The refresh token this browser holds: one, under whichever path its cookie is scoped to."""
    [cookie] = [each for each in http.cookies.jar if each.name == "refresh_token"]
    return cookie.value


def _presenting(token: str) -> dict:
    """What a browser holding this refresh token sends with a request, as the header itself."""
    return {"headers": {"cookie": f"refresh_token={token}"}}


def _spent_long_ago(db, token: str) -> None:
    """As if this refresh token had been spent longer ago than two tabs or a lost answer explain."""
    aged = db[UserSession.COLLECTION].update_one(
        {"refresh_hash": hashlib.sha256(token.encode()).hexdigest(), "rotated_at": {"$ne": None}},
        {"$set": {"rotated_at": now() - ROTATION_GRACE - timedelta(seconds=1)}})
    assert aged.matched_count == 1, "the premise: this token was spent"


def _set_cookies(response) -> dict[tuple[str, str], str]:
    """Each cookie a response sets, by name and path, as its whole header in lower case."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        name, _, _ = header.partition("=")
        path = next((part.split("=", 1)[1] for part in header.split("; ")
                     if part.lower().startswith("path=")), "/")
        found[name, path] = header.lower()
    return found


@pytest.fixture()
def limited(client, monkeypatch):
    """The same app with its rate limits on, each counting from nothing, and the clock they count by stood
    still: a test that ran across the end of a window would see its count begin again."""
    from services.api import deps
    monkeypatch.delenv("DISABLE_RATE_LIMIT")
    monkeypatch.setattr(deps, "_clock", lambda: 1_800_000_000.0, raising=False)
    return client


# ── the account-existence oracle ──────────────────────────────────────────────────────────────

def test_signup_answers_identically_for_a_new_and_an_existing_address(client):
    """Differing responses turn the endpoint into a way to test whether someone has an account here."""
    http, _db, _ = client
    email = _email()

    first = _signup(http, email)
    second = _signup(http, email)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_no_account_is_made_without_the_box_ticked(client):
    """The Terms are agreed to at sign-up, with the policies read beside them, or there is no account (owner,
    2026-11-12; the Terms since 2026-11-18)."""
    http, db, mailer = client
    email = _email()

    refused = http.post("/auth/signup", json={"email": email, "password": PASSWORD})

    assert refused.status_code == 400
    assert find_one(db, User, {"email": email}) is None and not mailer.sent


def test_a_policy_changed_since_the_page_opened_is_read_again_first(client):
    """Agreeing to the words a page showed an hour ago is not agreeing to the ones in force."""
    http, db, _ = client
    email = _email()

    stale = _signup(http, email, consent_version="2020-01-01")

    assert stale.status_code == 409
    assert find_one(db, User, {"email": email}) is None


def test_the_account_holds_what_was_agreed_to_and_when(client):
    """The box ticked is the agreement the site runs on, recorded with its version and when: the evidence that
    the Terms bind this account."""
    http, db, _ = client
    email = _email()

    assert _signup(http, email).status_code == 202

    user = find_one(db, User, {"email": email})
    assert user.processing_consent_version == LEGAL_VERSION
    assert user.processing_consent_at is not None


def test_a_member_signing_up_is_sent_on_to_the_payment_form(client):
    """The Member choice is carried by the email link (owner, 2026-11-12: the card is asked for after); the
    free choice, and an old page that sends none, land on the site as before. A fresh link goes the same
    way."""
    http, _db, mailer = client
    member, free = _email(), _email()

    _signup(http, member, plan="member")
    assert _link(mailer).endswith("&plan=member")
    http.post("/auth/resend", json={"email": member, "plan": "member"})
    assert _link(mailer).endswith("&plan=member"), "the fresh link goes where the first did"

    _signup(http, free)
    assert "plan=" not in _link(mailer)
    assert _signup(http, _email(), plan="gold").status_code == 422


def test_a_login_for_an_unknown_address_looks_like_a_wrong_password(client):
    """The same answer as an account's own address with the wrong password gets, or signing in would say
    who has an account here."""
    http, _, mailer = client
    theirs = _email()
    _signup(http, theirs)
    http.post("/auth/verify", json={"token": _link_token(mailer)})

    wrong = http.post("/auth/login", json={"email": theirs, "password": "not-the-one-they-chose"})
    unknown = http.post("/auth/login", json={"email": _email(), "password": PASSWORD})

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


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


def test_a_new_link_can_be_sent_and_the_older_still_works_until_one_confirms(client):
    """"Check your email" had no way to send another, and its advice — sign up again — sent nothing. And a new
    link spent the older: an iPhone's Mail shows the emails of one subject as one conversation, the oldest
    first, and the link people opened was the spent one, over and over until the resend limit refused them
    (the live log, 2026-11-22). Any of them confirms; once one has, the rest are spent: each signs in."""
    http, _db, mailer = client
    email = _email()
    _signup(http, email)
    first = _link_token(mailer)

    response = http.post("/auth/resend", json={"email": email})

    assert response.status_code == 202
    assert len(mailer.sent) == 2, "a second email went out"
    newest = _link_token(mailer)
    assert http.post("/auth/verify", json={"token": first}).status_code == 200, "the oldest confirms"
    assert http.post("/auth/verify", json={"token": newest}).status_code == 400, "then the rest are spent"


def test_an_address_with_an_account_is_told_so_by_email_and_the_page_says_what_it_says_to_all(client):
    """Signing up again, or asking for a new link, with an address already confirmed sent nothing, while the
    page said "check your email" as it does for everybody: people who had confirmed waited for an email that
    never came (the live log, 2026-11-22). Their inbox is told, with the way in and a new password, and the
    answer is the same as for anybody, so it still says nothing about who has an account here."""
    http, _db, mailer = client
    confirmed = _email()
    _signup(http, confirmed)
    http.post("/auth/verify", json={"token": _link_token(mailer)})
    http.cookies.clear()

    def resend():
        return http.post("/auth/resend", json={"email": confirmed})

    for again in (lambda: _signup(http, confirmed), resend):
        before = len(mailer.sent)
        answer = again()
        assert (answer.status_code, answer.json()) == (202, {"status": "check your email"})
        assert len(mailer.sent) == before + 1
        to, _, body = mailer.sent[-1]
        assert to == confirmed and "/signin" in body, "the way in, to their own inbox"

    reset = _link_token(mailer)
    chosen = http.post("/auth/reset", json={"token": reset, "new_password": "a-brand-new-passphrase"})
    assert chosen.status_code == 200, "its new-password link works"


def test_resend_answers_the_same_whoever_asks(client):
    """No address oracle, and nothing sent to an address that is not waiting for a link."""
    http, _db, mailer = client
    verified = _email()
    _signup(http, verified)
    http.post("/auth/verify", json={"token": _link_token(mailer)})
    sent = len(mailer.sent)

    unknown = http.post("/auth/resend", json={"email": _email()})
    assert len(mailer.sent) == sent, "an address with no account is sent nothing"
    known = http.post("/auth/resend", json={"email": verified})

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [to for to, _, _ in mailer.sent[sent:]] == [verified], "a confirmed one is told it has an account"


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


def test_the_refresh_cookie_goes_to_the_session_routes_and_nowhere_else(client):
    """Scoped to `/auth`, where refresh and both sign-outs are, and sent with no other request the site makes.
    It was scoped to `/auth/refresh` alone, so signing out never received the token it was to end
    (2026-11-20)."""
    http, _db, mailer = client
    _, response = _logged_in(http, mailer)
    assert response.status_code == 200

    cookies = _set_cookies(response)
    refresh = cookies["refresh_token", "/auth"]
    assert "samesite=strict" in refresh and "max-age=0" not in refresh
    assert ("access_token", "/") in cookies
    # A browser that signed in before 2026-11-20 holds its token under the old path: taken away as the new
    # one is given, or it would send both, and the spent one would be read as a theft.
    assert "max-age=0" in cookies["refresh_token", "/auth/refresh"]


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
    before = _refresh_token(http)

    response = http.post("/auth/refresh")
    assert response.status_code == 200
    assert _refresh_token(http) != before


def test_a_replayed_refresh_token_ends_the_session_and_clears_the_cookies(client):
    """Either the client is buggy or the token was stolen: one spent longer ago than two tabs refreshing
    together, or an answer lost on the way, explain (`ROTATION_GRACE`)."""
    http, db, mailer = client
    _logged_in(http, mailer)
    stolen = _refresh_token(http)
    assert http.post("/auth/refresh").status_code == 200
    _spent_long_ago(db, stolen)

    replay = _browser().post("/auth/refresh", **_presenting(stolen))
    assert replay.status_code == 401
    cleared = _set_cookies(replay)
    assert "max-age=0" in cleared["refresh_token", "/auth"] and "max-age=0" in cleared["access_token", "/"]
    assert http.post("/auth/refresh").status_code == 401, "every session of theirs ended with it"


def test_two_tabs_refreshing_with_one_token_both_stay_signed_in(client):
    """Two tabs whose access token ran out together both present the refresh token one of them has just
    spent, as a laptop waking with two tabs open does. The second was read as a theft: every session ended,
    on every device, and the cookies were cleared (2026-11-20)."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    shared = _refresh_token(http)
    other_tab = _browser()

    assert http.post("/auth/refresh").status_code == 200
    second = other_tab.post("/auth/refresh", **_presenting(shared))

    assert second.status_code == 200
    assert "max-age=0" not in _set_cookies(second)["refresh_token", "/auth"]
    for tab in (http, other_tab):
        assert tab.get("/account").status_code == 200
        assert tab.post("/auth/refresh").status_code == 200, "and each goes on refreshing"


def test_logout_ends_the_session(client):
    """On the server, not only in this browser: a copy of the refresh token is refused afterwards. Its cookie
    was scoped to `/auth/refresh`, so sign-out never received it and ended nothing, and this test passed
    because the browser had dropped its cookie (2026-11-20)."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    copied = _refresh_token(http)

    assert http.post("/auth/logout").status_code == 200

    assert _browser().post("/auth/refresh", **_presenting(copied)).status_code == 401
    assert http.post("/auth/refresh").status_code == 401


def test_logging_out_twice_is_not_an_error(client):
    """A user who clicks logout on a stale tab should see a logged-out page, not an error."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    http.post("/auth/logout")
    assert http.post("/auth/logout").status_code == 200


def test_logging_out_everywhere_ends_every_session(client):
    """This browser's and every other's. It answered 401 to everybody: the cookie it reads was never sent
    to it (2026-11-20)."""
    http, db, mailer = client
    email, _ = _logged_in(http, mailer)
    phone = _browser()
    assert phone.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200

    assert http.post("/auth/logout-everywhere").status_code == 200

    assert phone.post("/auth/refresh").status_code == 401
    assert http.post("/auth/refresh").status_code == 401
    assert db[UserSession.COLLECTION].count_documents({"revoked_at": None}) == 0


def _access_token(http) -> str:
    """The access token this browser holds, as a copy of it would be."""
    [cookie] = [each for each in http.cookies.jar if each.name == "access_token"]
    return cookie.value


def _holding(token: str) -> dict:
    """What a request carrying a copy of this access token sends, as the header itself."""
    return {"headers": {"cookie": f"access_token={token}"}}


def test_a_session_that_was_ended_is_refused_at_once_not_fifteen_minutes_later(client):
    """Signing out, a new password and deleting the account each end the session; the access token it was
    given was good for its fifteen minutes regardless, for the billing portal and their documents, and an
    account that was gone answered 500 to most pages (2026-11-20). Every request asks whether its session
    was ended."""
    http, _db, mailer = client
    email, _ = _logged_in(http, mailer)
    phone = _browser()
    assert phone.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    on_the_laptop, on_the_phone = _access_token(http), _access_token(phone)

    def opens(token: str) -> int:
        return _browser().get("/account", **_holding(token)).status_code

    assert (opens(on_the_laptop), opens(on_the_phone)) == (200, 200), "the premise: both are signed in"

    assert http.post("/auth/logout").status_code == 200
    assert (opens(on_the_laptop), opens(on_the_phone)) == (401, 200), "signing out ends this browser's alone"

    changed = phone.put("/account/password", json={"current_password": PASSWORD,
                                                   "new_password": "another-perfectly-fine-passphrase"})
    assert changed.status_code == 200
    assert opens(on_the_phone) == 401, "a new password ends every session there was"


def test_a_deleted_account_is_signed_out_not_an_error(client):
    """Its sessions go with it, so the token a browser still holds is answered 401, as any ended session is.
    It was answered 500: the pages read an account that was not there."""
    http, _db, mailer = client
    _logged_in(http, mailer)
    held = _access_token(http)

    assert http.request("DELETE", "/account", json={"password": PASSWORD}).status_code == 200

    for page in ("/account", "/dashboard", "/feed", "/billing"):
        assert _browser().get(page, **_holding(held)).status_code == 401, page


def test_a_suspended_account_cannot_keep_its_session_by_refreshing(client):
    """The refusal login gives. Refreshing never looked at the account, so a browser signed in before the
    suspension stayed signed in for as long as it kept refreshing."""
    http, db, mailer = client
    email, _ = _logged_in(http, mailer)
    db[User.COLLECTION].update_one({"email": email}, {"$set": {"status": "suspended"}})

    refused = http.post("/auth/refresh")

    assert refused.status_code == 401
    assert "max-age=0" in _set_cookies(refused)["refresh_token", "/auth"]
    assert db[UserSession.COLLECTION].count_documents({"revoked_at": None}) == 0


# ── an address somebody else signed up with first ─────────────────────────────────────────────

def test_an_unconfirmed_address_is_whoever_signs_up_for_it_last_and_a_confirmed_one_stays_theirs(client):
    """Signing up with somebody else's address and waiting was a way into their account: their own sign-up
    was dropped without a word, the link they asked for again confirmed the stranger's password, and the
    stranger signed in with it (2026-11-20). Now the later sign-up is the one that counts, and is mailed a
    link of its own. Once the address is confirmed, no sign-up changes anything."""
    http, _db, mailer = client
    email, strangers = _email(), "the-password-a-stranger-chose"
    assert _signup(_browser(), email, strangers).status_code == 202
    first_link = _link_token(mailer)

    assert _signup(http, email, PASSWORD).status_code == 202
    assert len(mailer.sent) == 2, "the person signing up now is mailed a link"
    assert http.post("/auth/verify", json={"token": first_link}).status_code == 400, "only the newest opens"
    assert http.post("/auth/verify", json={"token": _link_token(mailer)}).status_code == 200

    def signs_in(password: str) -> int:
        return _browser().post("/auth/login", json={"email": email, "password": password}).status_code

    assert (signs_in(strangers), signs_in(PASSWORD)) == (401, 200)

    assert _signup(_browser(), email, strangers).status_code == 202
    # Its owner is told somebody tried, in their own inbox, with the way in; the account is as it was.
    assert [(to, "/signin" in body) for to, _, body in mailer.sent[2:]] == [(email, True)]
    assert (signs_in(strangers), signs_in(PASSWORD)) == (401, 200)


def test_signing_up_for_one_address_is_limited_the_same_with_an_account_or_without(limited):
    """Each sign-up for an unconfirmed address mails it a link, so one address is asked for only so often;
    and the limit answers alike for an address with an account and one with none, or it would say which."""
    from services.api.auth_routes import SIGNUPS_PER_EMAIL

    http, db, _ = limited
    theirs = insert(db, User(email=_email(), password_hash="x", status="active")).email
    for email in (theirs, _email()):
        answers = [_signup(http, email).status_code for _ in range(SIGNUPS_PER_EMAIL + 1)]
        assert answers == [202] * SIGNUPS_PER_EMAIL + [429]


def test_a_confirmed_account_is_one_the_harvester_reads_for(client):
    """An account made by signing up and opening the link, with its search saved, is among the accounts the
    harvest reads the countries and places of. It asked for a field only an older sign-up set, so nobody
    confirmed after 2026-11-19 was (`TenantRepository.active_configs`)."""
    from libs.tenant.repository import TenantRepository

    http, db, mailer = client
    email = _email()
    _signup(http, email)
    assert http.post("/auth/verify", json={"token": _link_token(mailer)}).status_code == 200
    user = find_one(db, User, {"email": email})
    TenantRepository(db).save_config(user.id, {"keywords": ["python"]})

    assert user.id in TenantRepository(db).active_configs()


# ── one address, many passwords ───────────────────────────────────────────────────────────────

def test_a_limit_is_one_count_for_every_process_the_site_runs_as(limited, monkeypatch):
    """Each of the API's four processes counted in its own memory, so every limit was four times what it
    said, and began again at each restart (2026-11-20). The count is the database's, under a name that keeps
    nobody's email or address."""
    from services.api import auth_routes, deps

    http, db, _ = limited
    monkeypatch.setattr(auth_routes, "verify_password", lambda *_args: False)
    email = _email()

    def tries() -> int:
        return http.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code

    assert [tries() for _ in range(auth_routes.LOGINS_PER_EMAIL)] == [401] * auth_routes.LOGINS_PER_EMAIL
    # Another of the processes, which has nothing of this one's memory.
    monkeypatch.setattr(deps, "_WINDOWS", {}, raising=False)

    assert tries() == 429
    kept = [str(row["_id"]) for row in db.rate_counts.find()]
    assert kept and not any(email in name or "testclient" in name for name in kept)


def test_the_password_is_asked_for_only_so_often_once_signed_in(limited, monkeypatch):
    """Changing the password and deleting the account each ask for the password again, with no limit on
    asking: a session somebody else got hold of could guess at it for as long as it lasted (2026-11-20)."""
    from services.api import account_routes

    http, _db, mailer = limited
    _logged_in(http, mailer)
    # Every guess here is wrong: said so at once, where checking each takes a thirtieth of a second.
    monkeypatch.setattr(account_routes, "verify_password", lambda *_args: False)

    def guess(at: int) -> int:
        asks = ((http.put, "/account/password", {"current_password": "not-it", "new_password": "x" * 12}),
                (http.request, "/account", {"password": "not-it"}))
        ask, path, body = asks[at % 2]
        return (ask(path, json=body) if ask == http.put else ask("DELETE", path, json=body)).status_code

    assert [guess(at) for at in range(11)] == [401] * 10 + [429]


def test_one_address_cannot_have_passwords_checked_without_end_by_changing_the_email(limited, monkeypatch):
    """Checking a password takes 64 MiB and a thirtieth of a second by design. The only limit counted each
    email apart, so a different email each time was never limited, and a flood of them could take the
    server's memory (2026-11-20). An address past its limit is refused before any password is checked."""
    from services.api import auth_routes

    http, _db, _ = limited
    checked = []
    monkeypatch.setattr(auth_routes, "verify_password", lambda *_args: checked.append(1) and False)
    # A handful where the site allows a hundred: what is shown is that an address is counted whatever email
    # it gives, and that takes no more requests than this.
    monkeypatch.setattr(auth_routes, "LOGINS_PER_ADDRESS", 5)

    answers = [http.post("/auth/login", json={"email": _email(), "password": PASSWORD}).status_code
               for _ in range(auth_routes.LOGINS_PER_ADDRESS + 1)]

    assert answers == [401] * auth_routes.LOGINS_PER_ADDRESS + [429]
    assert len(checked) == auth_routes.LOGINS_PER_ADDRESS


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
    assert "/verify#token=" in mailer.sent[-1][2]

    http.post("/auth/forgot", json={"email": email})
    assert "/reset#token=" in mailer.sent[-1][2]


def test_the_sign_up_link_is_on_the_address_the_site_says_it_is_at(client, monkeypatch):
    """`deploy/reach.sh` opens `/health` through `public_url` to prove the links work, so the link in
    the email must be built from that same address, and followed, it must verify the account."""
    import re

    http, _db, mailer = client
    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com/")
    _signup(http, email := _email())

    site = http.get("/health").json()["public_url"]
    assert site == "https://jobs.example.com"
    link = re.search(r"https://\S+", mailer.sent[-1][2]).group(0)
    assert link.startswith(f"{site}/verify#token=")
    assert http.post("/auth/verify", json={"token": link.split("token=")[1]}).status_code == 200
    assert http.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


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
    sender.send("dev@example.com", "Confirm your email", "  http://localhost/verify#token=abc123")
    out = capsys.readouterr().out
    assert "token=abc123" in out


def test_signup_and_forgot_answer_before_the_email_is_sent(db, monkeypatch):
    """Through a real server: a mail server that takes its time does not keep the page waiting, and
    an address with an account is not told apart by how long it takes (2026-11-02). The mail server here
    holds every email until it is let go, so an answer that came back came back before its email went."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from services.api import deps
    from services.api.app import app

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    let_go, gone = threading.Event(), threading.Semaphore(0)

    class Held(ConsoleSender):
        def send(self, *args, **kwargs) -> None:
            let_go.wait(60)
            super().send(*args, **kwargs)
            gone.release()

    mailer = Held()
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        up_by = time.monotonic() + 30
        while not server.started and time.monotonic() < up_by:
            time.sleep(0.01)
        email = _email()
        # An answer that waited for the mail server would not come at all: the browser gives up first.
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
            for path, body in (("/auth/signup", {"email": email, "password": PASSWORD,
                                                 "consent_version": LEGAL_VERSION}),
                               ("/auth/forgot", {"email": email})):
                assert http.post(path, json=body).status_code == 202
                assert not mailer.sent, f"{path} waited for the mail server"
        let_go.set()
        assert gone.acquire(timeout=10) and gone.acquire(timeout=10), "and both went"
        assert len(mailer.sent) == 2
    finally:
        let_go.set()
        server.should_exit = True
        app.dependency_overrides.clear()


_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko)"
IPHONE_SAFARI = f"{_IPHONE} Version/18.5 Mobile/15E148 Safari/604.1"
IPHONE_CHROME = f"{_IPHONE} CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1"


def test_each_sign_up_says_which_browser_asked_and_the_id_its_email_left_with(client, caplog):
    """Confirmations that never came, all on iPhones, from Safari and not from Chrome on the same phone
    (2026-11-22), and the log could not say which browser had asked or whether an email had left: it wrote a
    line for a send that failed and none for one that went. Each sign-up and each resend now says the device
    and browser it came from and what was done, and each confirmation the id it was handed to the mail server
    with, which finds it in that server's own log."""
    import logging

    from services.api import deps
    from services.api.app import app

    http, _db, _ = client
    caplog.set_level(logging.INFO, logger="services.api.auth_routes")

    class Handed:
        """Answers as the real mailer does: with the id it gave the email."""

        def send(self, to, subject, body, *, reply_to=None, html=None):
            return "<IQ6AA3RdXirFrFu0HEbmPhlL@flow-solutions.xyz>"

    app.dependency_overrides[deps.get_mailer] = Handed
    safari, chrome = f"{uuid.uuid4().hex[:10]}@icloud.com", f"{uuid.uuid4().hex[:10]}@gmail.com"
    for email, agent in ((safari, IPHONE_SAFARI), (chrome, IPHONE_CHROME)):
        answer = http.post("/auth/signup", headers={"user-agent": agent},
                           json={"email": email, "password": PASSWORD, "consent_version": LEGAL_VERSION})
        assert answer.status_code == 202
    http.post("/auth/resend", headers={"user-agent": IPHONE_SAFARI}, json={"email": _email()})

    said = caplog.messages
    assert "signup: an address at icloud.com, from iPhone Safari: a link is being sent" in said
    assert "signup: an address at gmail.com, from iPhone Chrome: a link is being sent" in said
    assert (f"verification email to {safari} handed to the mail server as "
            "<IQ6AA3RdXirFrFu0HEbmPhlL@flow-solutions.xyz>") in said
    assert "resend: an address at example.com, from iPhone Safari: nothing sent, no account" in said


def test_why_a_sign_up_a_link_or_a_sign_in_was_refused_is_in_the_log(client, caplog):
    """An evening's live log had eight refused sign-ups and a run of refused links, and could say why for none
    of them (2026-11-22): the visitor was told, the log was not. Each refusal now says its reason, with the
    address's domain and what asked, and never the address."""
    import logging

    http, _db, mailer = client
    caplog.set_level(logging.INFO, logger="services.api.auth_routes")
    email, phone = _email(), {"user-agent": IPHONE_SAFARI}

    def sign_up(password: str) -> int:
        return http.post("/auth/signup", headers=phone, json={
            "email": email, "password": password, "consent_version": LEGAL_VERSION}).status_code

    def verify(token: str) -> int:
        return http.post("/auth/verify", headers=phone, json={"token": token}).status_code

    assert (sign_up("password123"), sign_up(PASSWORD)) == (400, 202)
    link = _link_token(mailer)
    assert (verify("nothing-we-sent"), verify(link), verify(link)) == (400, 200, 400)
    http.cookies.clear()
    wrong = {"email": email, "password": "not-the-one-123"}
    assert http.post("/auth/login", headers=phone, json=wrong).status_code == 401

    said = [record.getMessage() for record in caplog.records if record.name == "services.api.auth_routes"]
    at = "an address at example.com, from iPhone Safari"
    for line in (f"signup refused: {at}: password is too common",
                 "verify refused, from iPhone Safari: a link unknown, or expired",
                 f"verify: {at}: confirmed and signed in",
                 "verify refused, from iPhone Safari: a used link, the account confirmed already",
                 f"login refused: {at}: wrong password"):
        assert line in said, line
    assert not [line for line in said if email in line], "the address itself is in none of them"


def test_a_limit_that_stops_somebody_is_said_once_a_window(limited, caplog):
    """A refusal for asking too often was in the request log as a bare 429 and nowhere else, saying not which
    limit (2026-11-22). The first refusal of a window names the limit; the rest of the window is quiet,
    or a script's every try would be a line."""
    import logging

    http, _db, _ = limited
    caplog.set_level(logging.WARNING, logger="services.api.deps")
    email = _email()

    answers = [http.post("/auth/resend", json={"email": email}).status_code for _ in range(5)]

    assert answers == [202, 202, 202, 429, 429]
    said = [record.getMessage() for record in caplog.records if record.name == "services.api.deps"]
    assert said == ["limit reached: resend, more than 3 in 3600 s"]
