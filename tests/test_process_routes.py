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
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

from libs.legal import DISCLOSURE_VERSION

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

    # Consent granted: sending document text to Anthropic needs it, and `test_gdpr.py` owns the
    # test that it is actually required. Without it every test here would 403 on a rule it is not
    # about.
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                processing_consent_at=datetime.now(UTC),
                processing_consent_version=DISCLOSURE_VERSION)
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


def test_a_profile_built_by_the_conversation_is_not_offered_the_old_capture_flow(api):
    """**Two definitions of "profile ready" that disagreed.**

    `may_train` reads the setup files; this read `processed_at`, a column belonging to the capture
    flow the setup conversation replaced and which nothing sets any more. So an account that had
    just finished setup was told to "process your documents to build your profile" -- which would
    have run the superseded flow over a finished profile and replaced it.
    """
    from libs.profiles import setup_files
    from libs.profiles.repository import ProfileFiles
    from services.api.process_routes import state

    _http, session, user = api
    files = ProfileFiles(session, user.id)
    for name in setup_files():
        files.write(name, f"real content for {name}", kind="context")
    session.flush()

    answer = state(session, user.id)
    assert answer["action"] != "process", answer["message"]
    assert answer["action"] == "ready"


def test_training_state_reports_what_is_running_and_why(api):
    """**The work never lived in the browser**, and nothing said so. Pressing the button writes
    queue rows that the worker pools drain, so closing the tab changes nothing about whether it
    finishes -- but the page said "Starting…" once and then looked identical whether the run was
    halfway through or had died an hour ago."""
    from libs.queue import enqueue

    http, session, user = api
    idle = http.get("/process/training").json()
    assert idle["running"] is False

    enqueue(session, "label", user.id, payload={"kind": "cold_start"})
    session.flush()
    busy = http.get("/process/training").json()
    assert busy["running"] is True
    assert busy["lanes"]["label"]["ready"] == 1
    assert busy["target"] > 0


def test_stopping_cancels_queued_work_and_keeps_the_labels(api):
    """**Queued only.** A row a worker has claimed is mid-call on the user's own credential;
    deleting it here would not stop that call, only lose the record of it. Labels already written
    are kept -- they were paid for and are still true, and throwing them away would make stopping
    expensive enough that nobody would risk it."""
    from libs.db.models import QueueJob
    from libs.queue import enqueue

    http, session, user = api
    enqueue(session, "label", user.id, payload={"kind": "cold_start"})
    claimed = enqueue(session, "train", user.id, payload={})
    session.flush()
    claimed_row = session.get(QueueJob, claimed) if isinstance(claimed, int) else None
    if claimed_row is not None:
        claimed_row.state = "claimed"
        session.flush()

    answer = http.post("/process/training/stop").json()
    assert answer["cancelled"] >= 1
    session.flush()

    left = session.query(QueueJob).filter(QueueJob.user_id == user.id).all()
    assert all(row.state != "ready" for row in left), "nothing queued should survive a stop"


def test_a_run_between_batches_still_reads_as_running(api):
    """**The gap that made the page look idle mid-run.**

    Labelling goes in batches: a worker takes a job, writes a batch, finishes it, and the dispatcher
    enqueues the next. Between those two moments this account has nothing queued, and reading only
    `ready`/`claimed` flipped the page back to its start button while the run was plainly alive --
    which is what a user saw after navigating away and returning.
    """
    from datetime import UTC, datetime

    from libs.db.models import QueueJob
    from libs.queue import enqueue

    http, session, user = api
    user.rubric_md = "score jobs like this"
    job = enqueue(session, "label", user.id, payload={"kind": "cold_start"})
    session.flush()

    row = session.query(QueueJob).filter(QueueJob.user_id == user.id).one()
    row.state = "done"
    row.finished_at = datetime.now(UTC)
    session.flush()
    assert job is not None

    body = http.get("/process/training").json()
    assert body["lanes"]["label"].get("ready", 0) == 0, "nothing is queued, which is the premise"
    assert body["running"] is True, "a batch that just finished is a run still in progress"


def test_an_abandoned_run_stops_claiming_to_be_alive(api):
    """The other half: a finish long enough ago is a run nobody resumed, and saying "working" for
    ever is worse than saying nothing."""
    from datetime import UTC, datetime, timedelta

    from libs.db.models import QueueJob
    from libs.queue import enqueue

    http, session, user = api
    enqueue(session, "label", user.id, payload={"kind": "cold_start"})
    session.flush()
    row = session.query(QueueJob).filter(QueueJob.user_id == user.id).one()
    row.state = "done"
    row.finished_at = datetime.now(UTC) - timedelta(hours=3)
    session.flush()

    assert http.get("/process/training").json()["running"] is False


def test_stopping_actually_stops_a_loop_that_re_enqueues_itself(api):
    """**Cancelling queued rows could never have stopped this on its own.**

    The labeller re-enqueues itself while work remains, so a deleted row came straight back and the
    run carried on spending the user's own credential while the page reported it stopped. The flag
    is what a self-perpetuating loop can be ended by from outside it.
    """
    from libs.queue import enqueue

    http, session, user = api
    enqueue(session, "label", user.id, payload={"kind": "cold_start"})
    session.flush()
    assert http.get("/process/training").json()["running"] is True

    answer = http.post("/process/training/stop").json()
    assert answer["stopped"] is True
    session.flush()
    assert user.labelling_paused is True

    body = http.get("/process/training").json()
    assert body["paused"] is True
    assert body["running"] is False, "a paused account is not running, whatever is still draining"


def test_starting_again_clears_a_previous_stop(api):
    """Otherwise Stop is permanent, and a user who paused to think has bricked their own account."""
    from libs.profiles import setup_files
    from libs.profiles.repository import ProfileFiles

    http, session, user = api
    # `/setup/start` refuses an unfinished profile, which is the right refusal and not what this
    # test is about, so the account is given the files it would have had.
    files = ProfileFiles(session, user.id)
    for name in setup_files():
        files.write(name, f"real content for {name}", kind="context")
    user.rubric_md = "score jobs like this"
    user.labelling_paused = True
    session.flush()

    started = http.post("/setup/start")
    assert started.status_code < 400, started.text
    session.flush()
    assert user.labelling_paused is False


def test_a_trained_account_is_not_permanently_training(api):
    """**What made a finished account show "Fitting your model" and a Stop button.**

    Scoring runs continuously for every account with a model -- it is how new corpus rows reach an
    existing feed -- and the score lane was counted as training work. Combined with `train` being a
    phase an account with enough labels sits in for ever, a routine score finish was enough to make
    the page claim a run was in progress. Nothing was leaking between accounts; the data was the
    user's own and the reading of it was wrong.
    """
    from datetime import UTC, datetime

    from libs.db.models import QueueJob
    from libs.queue import enqueue

    http, session, user = api
    user.rubric_md = "score jobs like this"
    enqueue(session, "score", user.id, payload={})
    session.flush()
    row = session.query(QueueJob).filter(QueueJob.user_id == user.id).one()
    row.state = "done"
    row.finished_at = datetime.now(UTC)
    session.flush()

    body = http.get("/process/training").json()
    assert body["running"] is False, "routine scoring is not a training run"
    assert "score" not in body["lanes"], "the score lane is not part of what a user calls training"
