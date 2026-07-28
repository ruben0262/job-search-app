"""The feedback loop: what a tap records, and who is allowed to tap.

Two clusters, and they fail in opposite ways.

**The security boundary fails loudly if it is too tight and silently if it is too loose.** A
stranger who can tap somebody else's buttons does not steal anything — they slowly corrupt the one
thing the product is built on, and nothing reports it. So the tests are about who is refused: a
wrong path, a wrong header, and a channel member who is not the account owner.

**The label semantics fail silently either way.** Applied is 90 and not 100 on purpose; a second tap
must not move `applied_at`, because that column is the date somebody actually applied.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

SECRET = "webhook-secret-" + "z" * 20
OWNER_CHAT = "555000111"


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, User, UserJobState
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                telegram_status="connected", telegram_chat_id=OWNER_CHAT,
                telegram_webhook_secret=SECRET, profile_generation=1, profile_version="abc12345")
    session.add(user)
    session.flush()

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=80.0))
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            yield http, session, user, job
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def tap(http, job_id, *, action="ap", secret=SECRET, header=SECRET, from_id=OWNER_CHAT):
    return http.post(
        f"/telegram/webhook/{secret}",
        headers={"X-Telegram-Bot-Api-Secret-Token": header} if header else {},
        json={"callback_query": {"id": "1", "from": {"id": int(from_id)},
                                 "data": f"{action}:{job_id}"}})


# ── who is refused ────────────────────────────────────────────────────────────────────────────

def test_a_wrong_path_is_a_404(api):
    """404 and not 401 throughout: a probe should not learn that a path exists."""
    http, _, _, job = api
    assert tap(http, job.id, secret="not-the-secret").status_code == 404


def test_a_leaked_path_without_the_header_is_still_refused(api):
    """**Why there are two secrets.** A path leaks — into a proxy log, a browser history, a
    screenshot — and the header is what makes a leaked path insufficient on its own."""
    http, _, _, job = api
    assert tap(http, job.id, header=None).status_code == 404
    assert tap(http, job.id, header="wrong").status_code == 404


def test_a_channel_member_who_is_not_the_owner_teaches_nothing(api):
    """A feed channel has other members. This is not a privacy leak — it is a slow, invisible
    corruption of the training data, which is worse because nothing reports it."""
    from libs.db.models import UserJobState

    http, session, user, job = api
    body = tap(http, job.id, from_id="999888777").json()
    session.flush()

    assert body["status"] == "not_yours"
    assert session.get(UserJobState, (user.id, job.id)).applied_at is None


def test_an_update_that_is_not_a_button_tap_is_acknowledged(api):
    """Telegram resends anything not answered with a 2xx, forever."""
    http, _, _, _ = api
    response = http.post(f"/telegram/webhook/{SECRET}",
                         headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                         json={"message": {"text": "hello"}})
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# ── what a tap records ────────────────────────────────────────────────────────────────────────

def test_applying_writes_a_feedback_label_worth_five_llm_labels(api):
    """`source='feedback'` and `weight=5` have been in the schema since Phase 2 with nothing writing
    them. What somebody did outranks what a model guessed."""
    from libs.db.models import UserLabel
    from libs.feedback import APPLIED_LABEL, FEEDBACK_WEIGHT

    http, session, user, job = api
    assert tap(http, job.id).json()["status"] == "recorded"
    session.flush()

    label = session.get(UserLabel, (user.id, job.id))
    assert (label.label, label.source, label.weight) == (APPLIED_LABEL, "feedback", FEEDBACK_WEIGHT)
    assert label.profile_generation == user.profile_generation


def test_applying_is_ninety_not_one_hundred(api):
    """People apply to things they are unsure about. Saturating the scale would teach the model that
    every applied job is identical, throwing away the ordering that makes a feed useful."""
    from libs.feedback import APPLIED_LABEL, SKIPPED_LABEL

    assert 0 < SKIPPED_LABEL < APPLIED_LABEL < 100


def test_feedback_overwrites_the_model_guess_for_that_job(api):
    """The whole point: an LLM label for this job almost certainly exists already."""
    from libs.db.models import UserLabel

    http, session, user, job = api
    session.add(UserLabel(user_id=user.id, job_id=job.id, label=45, source="llm", weight=1.0,
                          profile_generation=1))
    session.flush()

    tap(http, job.id)
    session.flush()

    label = session.get(UserLabel, (user.id, job.id))
    assert (label.label, label.source) == (90, "feedback")


def test_a_second_tap_does_not_move_the_date_somebody_applied(api):
    """`applied_at` is the durable record of a real event. A later tap is a person checking it
    registered, not a new application."""
    from libs.db.models import UserJobState

    http, session, user, job = api
    tap(http, job.id)
    session.flush()
    first = session.get(UserJobState, (user.id, job.id)).applied_at

    assert tap(http, job.id).json()["status"] == "already"
    session.flush()
    assert session.get(UserJobState, (user.id, job.id)).applied_at == first


def test_skipping_dismisses_the_job_and_labels_it_low(api):
    from libs.db.models import UserJobState, UserLabel

    http, session, user, job = api
    tap(http, job.id, action="sk")
    session.flush()

    assert session.get(UserJobState, (user.id, job.id)).dismissed_at is not None
    assert session.get(UserLabel, (user.id, job.id)).label == 10


def test_a_tap_on_a_job_the_user_never_had_is_not_an_error(api):
    """The message may be old and the row trimmed. Not worth raising at somebody, never silent."""
    http, _, _, _ = api
    assert tap(http, 99_999_999).json()["status"] == "already"


def test_any_tap_counts_as_engagement(api):
    """Delivery is Telegram, so a user who reads their feed every morning may never open the web app
    again. Marking them dormant on login alone would pause the most engaged users' feeds."""
    http, session, user, job = api
    assert user.last_active_at is None

    tap(http, job.id)
    session.flush()
    assert user.last_active_at is not None


# ── the retrain trigger ───────────────────────────────────────────────────────────────────────

def test_a_burst_of_taps_produces_exactly_one_retrain(api):
    """Twenty taps is one fit, ten minutes after the last. A model refit on one extra label is the
    same model and twice the CPU."""
    from sqlalchemy import func, select

    from libs.db.models import CorpusJob, QueueJob, UserJobState

    http, session, user, _ = api
    for _ in range(6):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
        session.add(job)
        session.flush()
        session.add(UserJobState(user_id=user.id, job_id=job.id, score=80.0))
        session.flush()
        tap(http, job.id)
        session.flush()

    assert session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.user_id == user.id, QueueJob.lane == "train")) == 1


def test_a_single_tap_does_not_trigger_a_retrain(api):
    """Below the floor there is nothing worth refitting."""
    from sqlalchemy import func, select

    from libs.db.models import QueueJob

    http, session, user, job = api
    tap(http, job.id)
    session.flush()

    assert session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.user_id == user.id, QueueJob.lane == "train")) == 0


def test_build_cv_queues_docgen_even_though_the_worker_does_not_exist_yet(api):
    """The lane exists, so a tap waits in it rather than disappearing. Losing somebody's request
    because the feature shipped in two halves would be the worst of both."""
    from sqlalchemy import select

    from libs.db.models import QueueJob

    http, session, _, job = api
    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    queued = session.scalar(select(QueueJob).where(QueueJob.lane == "docgen"))
    assert queued is not None
    assert queued.payload["job_id"] == job.id


def test_the_keyboard_carries_three_buttons_within_telegrams_limit(api):
    """`callback_data` is capped at 64 bytes, and every extra button is something to read on a phone
    before deciding."""
    from services.api.telegram_routes import keyboard

    buttons = keyboard(123)["inline_keyboard"][0]
    assert len(buttons) == 3
    assert all(len(button["callback_data"].encode()) <= 64 for button in buttons)


# ── the two sides agree ───────────────────────────────────────────────────────────────────────

def test_the_buttons_the_notifier_sends_are_the_ones_the_webhook_understands():
    """The notifier renders the keyboard and the API parses the taps, and they may not import each
    other. `callback_data` is the entire contract between them — so it is asserted here, reading
    both sides, rather than left to whoever edits one of them next."""
    from services.api.telegram_routes import CALLBACK_PREFIX, _parse
    from services.notifier.buttons import ACTION_CODES, keyboard

    for action, code in ACTION_CODES.items():
        assert CALLBACK_PREFIX[code] == action

    for button in keyboard(4242)["inline_keyboard"][0]:
        parsed, job_id = _parse(button["callback_data"])
        assert parsed in CALLBACK_PREFIX.values()
        assert job_id == 4242


def test_a_malformed_callback_is_ignored_rather_than_guessed():
    """It is attacker-supplied text. Guessing at it would be the one place a bad payload could
    reach a write."""
    from services.api.telegram_routes import _parse

    for junk in ("", "ap", "ap:", "ap:abc", "xx:1", "ap:1:2", "'; DROP TABLE users--"):
        assert _parse(junk) == (None, 0)
