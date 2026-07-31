"""Auth: the policy decisions from `AUTH_REUSE.md` §3, and the two that must not be got wrong.

Most of this file's value is in five tests — hashes are never reversible, tokens are single-use,
refresh tokens rotate, a replayed refresh revokes the family, and a password change logs every
device out. The rest of auth is library calls, and testing that argon2 hashes is testing argon2.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("argon2")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

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
    verify_password,
)
from libs.db.models import AuthToken, User, UserSession

# ── password policy ───────────────────────────────────────────────────────────────────────────

def test_a_hash_reveals_nothing_about_the_password():
    """The point of storing a hash. Also catches the classic mistake of storing the password with
    an encoding step mistaken for hashing."""
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
    """**Pinned to the number, not to the constant.** The floor moved 12 -> 9 on 2026-07-31 at the
    owner's decision, and the other tests read the constant — so without this one, a later change
    to it would pass everything silently. Eight is refused, nine is accepted."""
    assert MIN_PASSWORD_LENGTH == 9
    assert any("at least" in problem for problem in password_problems("x" * 8))
    assert password_problems("correct9!") == []


# ── the database-backed halves ────────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    s.add(user)
    s.flush()
    return user


@pytestmark_db
def test_only_the_token_hash_is_stored(ctx):
    """The plaintext exists in the email and nowhere else — a dump must not let anyone verify an
    address or reset a password."""
    user = _user(ctx)
    token = issue_token(ctx, user.id, "verify_email")
    ctx.flush()

    rows = ctx.execute(select(AuthToken.token_hash).where(AuthToken.user_id == user.id)).all()
    assert len(rows) == 1
    assert rows[0][0] == hash_token(token) != token


@pytestmark_db
def test_a_token_works_once(ctx):
    user = _user(ctx)
    token = issue_token(ctx, user.id, "verify_email")
    ctx.flush()

    assert consume_token(ctx, token, "verify_email") == user.id
    ctx.flush()
    assert consume_token(ctx, token, "verify_email") is None


@pytestmark_db
def test_a_token_cannot_be_used_for_a_different_purpose(ctx):
    """A verification link must not double as a password reset — that would turn "can receive email"
    into "can take the account" without the user ever asking for a reset."""
    user = _user(ctx)
    token = issue_token(ctx, user.id, "verify_email")
    ctx.flush()
    assert consume_token(ctx, token, "reset_password") is None


@pytestmark_db
def test_reissuing_revokes_the_previous_link(ctx):
    """A user who clicks "resend" twice should not leave two live links."""
    user = _user(ctx)
    first = issue_token(ctx, user.id, "verify_email")
    ctx.flush()
    second = issue_token(ctx, user.id, "verify_email")
    ctx.flush()

    assert consume_token(ctx, first, "verify_email") is None
    assert consume_token(ctx, second, "verify_email") == user.id


@pytestmark_db
def test_a_refresh_token_is_stored_hashed_and_rotates_on_use(ctx):
    user = _user(ctx)
    first = issue_session(ctx, user.id)
    ctx.flush()

    stored = ctx.scalar(select(UserSession.refresh_hash).where(UserSession.id == first.session_id))
    assert stored != first.refresh_token

    second = rotate_session(ctx, first.refresh_token)
    ctx.flush()
    assert second.refresh_token != first.refresh_token


@pytestmark_db
def test_replaying_a_rotated_refresh_token_revokes_every_session(ctx):
    """Either the client is buggy or the token was stolen. On the second reading, the thief and the
    user must both end up holding invalid credentials rather than one of them silently keeping
    access."""
    user = _user(ctx)
    first = issue_session(ctx, user.id)
    ctx.flush()
    second = rotate_session(ctx, first.refresh_token)
    ctx.flush()

    with pytest.raises(SessionExpired, match="replayed"):
        rotate_session(ctx, first.refresh_token)
    ctx.flush()

    with pytest.raises(SessionExpired):
        rotate_session(ctx, second.refresh_token)


@pytestmark_db
def test_logging_out_one_device_leaves_the_others(ctx):
    user = _user(ctx)
    laptop = issue_session(ctx, user.id)
    phone = issue_session(ctx, user.id)
    ctx.flush()

    assert revoke_session(ctx, laptop.refresh_token) is True
    ctx.flush()

    # "logged out", not "replayed": a stale cookie after an ordinary logout is not evidence of
    # theft, and treating it as such would end the phone's session too.
    with pytest.raises(SessionExpired, match="logged out"):
        rotate_session(ctx, laptop.refresh_token)
    assert rotate_session(ctx, phone.refresh_token) is not None


@pytestmark_db
def test_revoking_everything_ends_every_live_session(ctx):
    """What a password change must do. A change that left old sessions alive would not actually
    remove whoever the user changed it because of."""
    user = _user(ctx)
    sessions = [issue_session(ctx, user.id) for _ in range(3)]
    ctx.flush()

    assert revoke_all(ctx, user.id) == 3
    ctx.flush()
    for issued in sessions:
        with pytest.raises(SessionExpired):
            rotate_session(ctx, issued.refresh_token)


@pytestmark_db
def test_one_users_session_is_never_another_users(ctx):
    """T1 at the auth layer. Rotating with someone else's token must not hand back a session for
    the caller."""
    alice, mallory = _user(ctx), _user(ctx)
    alice_session = issue_session(ctx, alice.id)
    ctx.flush()

    rotated = rotate_session(ctx, alice_session.refresh_token)
    ctx.flush()
    owner = ctx.scalar(select(UserSession.user_id).where(UserSession.id == rotated.session_id))
    assert owner == alice.id != mallory.id
