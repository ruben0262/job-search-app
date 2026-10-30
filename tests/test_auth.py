"""Auth: the policy decisions from `AUTH_REUSE.md` §3, and the two that must not be got wrong."""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("argon2")

from libs.auth import (
    MIN_PASSWORD_LENGTH,
    SessionExpired,
    consume_token,
    hash_password,
    hash_token,
    issue_session,
    issue_token,
    password_problems,
    revoke_all,
    revoke_session,
    rotate_session,
    token_owner,
    verify_password,
)
from libs.db import get, insert
from libs.db.models import AuthToken, User, UserSession
from tests.conftest import needs_db

# ── password policy ───────────────────────────────────────────────────────────────────────────

def test_a_hash_reveals_nothing_about_the_password():
    """The point of storing a hash."""
    password = "correct-horse-battery-staple"
    stored = hash_password(password)
    assert password not in stored
    assert verify_password(stored, password)
    assert not verify_password(stored, password + "x")


def test_the_same_password_hashes_differently_every_time():
    """Per-hash random salt. Identical hashes would let a dump reveal which users share a password."""
    assert hash_password("correct-horse-battery") != hash_password("correct-horse-battery")


def test_a_corrupt_stored_hash_is_a_failed_login_not_a_500():
    """A hand-edited or truncated row must not turn the login endpoint into an error page."""
    assert verify_password("not-an-argon2-hash", "anything") is False


def test_every_problem_is_reported_at_once():
    """A form that reveals one rule per attempt is how a user tries six times."""
    problems = password_problems("short", email="alice@example.com")
    assert any("at least" in p for p in problems)
    assert password_problems("x" * MIN_PASSWORD_LENGTH) == []


def test_a_password_containing_the_email_is_refused():
    assert password_problems("alice12345678", email="alice@example.com")


def test_the_minimum_is_nine_and_the_boundary_holds():
    """Pinned to the number, not to the constant."""
    assert MIN_PASSWORD_LENGTH == 9
    assert any("at least" in problem for problem in password_problems("x" * 8))
    assert password_problems("correct9!") == []


# ── the database-backed halves ────────────────────────────────────────────────────────────────

def _user(db) -> User:
    return insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x"))


@needs_db
def test_only_the_token_hash_is_stored(db):
    """The plaintext exists in the email and nowhere else."""
    user = _user(db)
    token = issue_token(db, user.id, "verify_email")

    rows = list(db[AuthToken.COLLECTION].find({"user_id": user.id}))
    assert len(rows) == 1
    assert rows[0]["_id"] == hash_token(token) != token
    assert token not in str(rows[0])


@needs_db
def test_a_token_works_once(db):
    user = _user(db)
    token = issue_token(db, user.id, "verify_email")

    assert consume_token(db, token, "verify_email") == user.id
    assert consume_token(db, token, "verify_email") is None


@needs_db
def test_a_token_cannot_be_used_for_a_different_purpose(db):
    """A verification link must not double as a password reset."""
    user = _user(db)
    token = issue_token(db, user.id, "verify_email")
    assert consume_token(db, token, "reset_password") is None


@needs_db
def test_reissuing_revokes_the_previous_link(db):
    """A user who clicks "resend" twice should not leave two live links."""
    user = _user(db)
    first = issue_token(db, user.id, "verify_email")
    second = issue_token(db, user.id, "verify_email")

    assert consume_token(db, first, "verify_email") is None
    assert consume_token(db, second, "verify_email") == user.id


@needs_db
def test_looking_up_a_token_does_not_spend_it(db):
    """A password reset checks the new password before it spends the link, so a refused password
    leaves the link usable: the lookup must not count as a use."""
    user = _user(db)
    token = issue_token(db, user.id, "reset_password")

    assert token_owner(db, token, "reset_password") == user.id
    assert token_owner(db, token, "verify_email") is None
    assert consume_token(db, token, "reset_password") == user.id
    assert token_owner(db, token, "reset_password") is None


@needs_db
def test_a_refresh_token_is_stored_hashed_and_rotates_on_use(db):
    user = _user(db)
    first = issue_session(db, user.id)

    stored = get(db, UserSession, first.session_id)
    assert stored is not None and stored.refresh_hash != first.refresh_token

    second = rotate_session(db, first.refresh_token)
    assert second.refresh_token != first.refresh_token


@needs_db
def test_replaying_a_rotated_refresh_token_revokes_every_session(db):
    """Either the client is buggy or the token was stolen."""
    user = _user(db)
    first = issue_session(db, user.id)
    second = rotate_session(db, first.refresh_token)

    with pytest.raises(SessionExpired, match="replayed"):
        rotate_session(db, first.refresh_token)

    with pytest.raises(SessionExpired):
        rotate_session(db, second.refresh_token)


@needs_db
def test_logging_out_one_device_leaves_the_others(db):
    user = _user(db)
    laptop = issue_session(db, user.id)
    phone = issue_session(db, user.id)

    assert revoke_session(db, laptop.refresh_token) is True

    # "logged out", not "replayed": a stale cookie after an ordinary logout is not evidence of theft.
    with pytest.raises(SessionExpired, match="logged out"):
        rotate_session(db, laptop.refresh_token)
    assert rotate_session(db, phone.refresh_token) is not None


@needs_db
def test_revoking_everything_ends_every_live_session(db):
    """What a password change must do."""
    user = _user(db)
    sessions = [issue_session(db, user.id) for _ in range(3)]

    assert revoke_all(db, user.id) == 3
    for issued in sessions:
        with pytest.raises(SessionExpired):
            rotate_session(db, issued.refresh_token)


@needs_db
def test_one_users_session_is_never_another_users(db):
    """T1 at the auth layer."""
    alice, mallory = _user(db), _user(db)
    alice_session = issue_session(db, alice.id)

    rotated = rotate_session(db, alice_session.refresh_token)
    assert get(db, UserSession, rotated.session_id).user_id == alice.id != mallory.id
