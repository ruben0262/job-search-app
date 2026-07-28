"""Process data, from the dashboard's side: state, the gate, and the confirmation.

The processing call itself is covered by `test_profile_capture.py` with a fake client. What is left
here is what the endpoints promise and nothing else does:

- the state derives from counters rather than a status column, so it cannot go stale;
- the warning names a real number, because a generic "are you sure?" is one nobody reads;
- a double-click buys one Opus call, not two;
- confirming forces `confirmed` server-side — posting the draft back untouched must not confirm on
  the user's behalf, which is the whole point of the step.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

DRAFT = {
    "identity": {"name": "Alice Example"},
    "experience": [{"role": "Backend Engineer", "org": "Acme"}],
    "not_list": ["mobile", "enterprise Java at depth"],
    "confirmed": False,
}


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User, UserDocument, UserSecret
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()
    session.add(UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text="Alice",
                             text_sha256=uuid.uuid4().hex * 2, chars=5))
    session.add(UserSecret(user_id=user.id, kind="claude_api", ciphertext=b"x", nonce=b"n"))
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


def test_an_account_with_documents_and_no_profile_is_told_to_process(api):
    http, _, _ = api
    body = http.get("/process/state").json()
    assert body["action"] == "process"
    assert body["documents"] == 1


def test_uploading_a_document_after_processing_reopens_the_action(api):
    """The reason `documents_changed_at` is a timestamp rather than a flag: a flag has to be cleared
    by whoever remembers, and the first thing to go stale would be the one saying what is stale."""
    from datetime import UTC, datetime, timedelta

    http, session, user = api
    # An explicit past instant rather than `func.now()`. Postgres's `now()` is *transaction* start
    # time, so the upload below — which happens in this same test transaction — would be stamped
    # identically and the comparison could never be true. Production has no such problem: the
    # processing worker and an upload request are always separate transactions.
    user.processed_at = datetime.now(UTC) - timedelta(hours=1)
    user.profile = dict(DRAFT)
    session.flush()
    assert http.get("/process/state").json()["action"] == "ready"

    http.post("/documents", data={"kind": "reference"},
              files={"file": ("ref.txt", b"A reference for Alice", "text/plain")})
    session.flush()
    assert http.get("/process/state").json()["action"] == "process"


def test_a_pending_draft_takes_priority_over_processing_again(api):
    """Telling a user to re-process while a draft waits would throw away the call they paid for."""
    http, session, user = api
    user.profile_draft = dict(DRAFT)
    session.flush()

    body = http.get("/process/state").json()
    assert body["action"] == "confirm"
    assert body["has_draft"] is True


def test_the_warning_names_what_it_will_cost(api):
    """A generic "are you sure?" is one nobody reads; a number they recognise is one they think
    about."""
    from libs.db.models import CorpusJob, UserLabel

    http, session, user = api
    for _ in range(3):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
        session.add(job)
        session.flush()
        session.add(UserLabel(user_id=user.id, job_id=job.id, label=80,
                              profile_generation=user.profile_generation))
    session.flush()

    warning = http.get("/process/state").json()["warning"]
    assert warning["labels_invalidated"] == 3
    assert "3 jobs" in warning["text"]
    assert warning["estimate_is_rough"] is True


def test_pressing_the_button_twice_buys_one_call(api):
    """The collapse is the queue's partial unique index, not an application check — a double-click
    races with itself and an application check cannot promise anything about that."""
    from sqlalchemy import func, select

    from libs.db.models import QueueJob

    http, session, user = api
    assert http.post("/process").json()["status"] == "queued"
    session.flush()
    assert http.post("/process").json()["status"] == "already queued"
    session.flush()

    assert session.scalar(select(func.count()).select_from(QueueJob)
                          .where(QueueJob.user_id == user.id, QueueJob.lane == "process")) == 1


def test_processing_without_a_claude_credential_says_so(api):
    """It runs on the user's own key. Queueing it anyway would fail in a worker, hours later,
    somewhere the user cannot see."""
    from libs.db.models import UserSecret

    http, session, user = api
    session.delete(session.get(UserSecret, (user.id, "claude_api")))
    session.flush()

    response = http.post("/process")
    assert response.status_code == 400
    assert "credential" in response.json()["detail"]


def test_confirming_forces_the_flag_server_side(api):
    """Posting the draft back untouched must not confirm on the user's behalf — that is the whole
    point of the step."""
    http, session, user = api
    user.profile_draft = dict(DRAFT)
    session.flush()

    body = http.post("/process/confirm", json=DRAFT).json()
    session.flush()

    assert body["profile_generation"] == 1
    assert user.profile["confirmed"] is True
    assert user.profile_draft is None
    assert user.rubric_md and user.facts_md, "confirming renders both artifacts in one write"


def test_an_edit_made_at_confirmation_is_what_gets_stored(api):
    """The confirmation form is where a wrong `not_list` entry is removed, and that entry would
    otherwise silently suppress jobs the user wanted for as long as the profile lasts."""
    http, session, user = api
    user.profile_draft = dict(DRAFT)
    session.flush()

    edited = {**DRAFT, "not_list": ["mobile"]}
    http.post("/process/confirm", json=edited)
    session.flush()

    assert user.profile["not_list"] == ["mobile"]
    assert "enterprise Java" not in user.rubric_md


def test_confirming_with_no_draft_is_refused(api):
    http, _, _ = api
    assert http.post("/process/confirm", json=DRAFT).status_code == 409


def test_discarding_a_draft_leaves_the_live_profile_alone(api):
    http, session, user = api
    user.profile = {**DRAFT, "confirmed": True}
    user.profile_draft = {**DRAFT, "identity": {"name": "Wrong Name"}}
    session.flush()

    http.delete("/process/draft")
    session.flush()

    assert user.profile_draft is None
    assert user.profile["identity"]["name"] == "Alice Example"


def test_process_requires_signing_in(api):
    http, _, _ = api
    http.cookies.clear()
    assert http.get("/process/state").status_code == 401
