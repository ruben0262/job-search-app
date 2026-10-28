"""An account that finished setup must be able to build documents without pressing anything.

The state this exists for: setup complete, every profile file written, a trained model, a full
feed — and every CV and cover letter refused, because `users.profile` was NULL and documents are
built from the confirmed schema rather than from the files. The dashboard offered a Process button
and the account sat there. Measured 2026-08-09: one account, 320 labels, 385 jobs above cutoff,
zero documents buildable.

The second half is what makes it safe. Storing a profile used to overwrite `users.rubric_md` with
one rendered from the schema and bump `profile_generation` — which for that same account would
have thrown away a 13,809-character hand-tuned `label.md` and staled all 320 labels, buying them
again on the user's own credential. A first profile is free; a replacement is not, and keeps its
review step.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

PROFILE = {
    "identity": {"name": "Alice Example", "location": "Berlin, Germany",
                 "work_eligibility": ["EU"], "links": {"portfolio": "https://alice.dev"}},
    "skills": {"languages": ["Python"]},
    "constraints": {"languages": ["English (Native)"]},
}


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _account(db, *, rubric: str | None = None):
    from libs.db.models import User

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                active=True, rubric_md=rubric)
    db.add(user)
    db.flush()
    return user


def _label_file(db, user, content: str) -> None:
    """What the setup chat writes, and what `_sync_rubric` mirrors onto the row."""
    from libs.db.models import UserProfileFile

    db.add(UserProfileFile(user_id=user.id, name="label.md", kind="prompt", content=content))
    db.flush()


def test_a_setup_written_rubric_survives_storing_a_profile(db):
    """The expensive half. Overwriting it costs the account every label it has already paid for."""
    from libs.tenant.repository import TenantRepository

    tuned = "# Score jobs for this person\n" + "Their own rubric, at length.\n" * 40
    user = _account(db, rubric=tuned)
    _label_file(db, user, tuned)
    before = user.profile_generation

    TenantRepository(db).save_profile(user.id, {**PROFILE, "confirmed": True})
    db.flush()

    assert user.profile is not None, "the profile is still stored — that is what unblocks documents"
    assert user.rubric_md == tuned, "the account's own rubric was replaced by a rendered one"
    assert user.profile_generation == before, (
        "the generation moved, which stales every label this account has bought")


def test_storing_a_profile_never_writes_a_rubric(db):
    """No account gets a schema-rendered rubric any more, whether it has a file or not.

    `label.md` is in `SEED_FILES`, so every account is created with one and the setup agent mirrors
    it onto the row. A second renderer meant a profile save could silently replace it — and the
    guard against that was a condition, which is one bad branch away from doing the damage anyway.
    The renderer is gone instead.
    """
    from libs.tenant.repository import TenantRepository

    user = _account(db)

    TenantRepository(db).save_profile(user.id, {**PROFILE, "confirmed": True})
    db.flush()

    assert user.profile is not None, "the profile is stored — that is what unblocks documents"
    assert not user.rubric_md, "a rubric was rendered from the schema"
    assert user.profile_generation == 0, "storing a profile must not stale anybody's labels"


@pytest.mark.skip(reason="switched off (CLAUDE.md §3): `process_user` sends PROCESSING_DONE, an "
                         "in-app event deleted 2026-10-28 because it linked to the deleted #/process "
                         "page and nothing live sent it; capture that comes back brings its own event")
def test_a_first_profile_needs_no_confirmation(db):
    """`process_user` adopts it. Waiting for a click is what left an account unable to build."""
    from services.capture.process import process_user

    user = _account(db)
    _document(db, user)

    process_user(db, user.id, _FakeClient())
    db.flush()

    assert user.profile is not None
    assert user.profile_draft is None, "a first profile is applied, not parked as a draft"


@pytest.mark.skip(reason="switched off (CLAUDE.md §3): `process_user` sends PROCESSING_DONE, an "
                         "in-app event deleted 2026-10-28 because it linked to the deleted #/process "
                         "page and nothing live sent it; capture that comes back brings its own event")
def test_replacing_a_profile_still_waits_to_be_read(db):
    """Confirming a replacement stales every label and spends the user's money, so a person does it."""
    from services.capture.process import process_user

    user = _account(db)
    user.profile = {**PROFILE, "confirmed": True}
    db.flush()
    _document(db, user)
    original = dict(user.profile)

    process_user(db, user.id, _FakeClient())
    db.flush()

    assert user.profile_draft is not None, "a replacement must be reviewable"
    assert user.profile == original, "the live profile is untouched until it is confirmed"


def _document(db, user) -> None:
    import hashlib

    from libs.db.models import UserDocument

    text = "Alice Example. Backend engineer. Built the billing service."
    db.add(UserDocument(user_id=user.id, filename="cv.pdf", kind="cv", text=text,
                        text_sha256=hashlib.sha256(text.encode()).hexdigest(), chars=len(text)))
    db.flush()


class _FakeClient:
    """Answers with a valid profile, so the test is about what happens to it, not about parsing."""

    def complete_json(self, **kwargs) -> dict:
        return dict(PROFILE)
