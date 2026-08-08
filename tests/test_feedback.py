"""The feedback loop: what a tap records, and who is allowed to tap."""
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

    # A finished profile, because the build button now refuses without one. Both halves are
    # needed and they are different things: the files are what the setup chat writes, and
    # `user.profile` is the schema the onboarding form confirms. An account with the first and
    # not the second is the state that promised a document and never sent one.
    from libs.db.models import UserProfileFile
    from libs.profiles import GENERATION_FILES

    for name in GENERATION_FILES:
        session.add(UserProfileFile(user_id=user.id, name=name, kind="context",
                                    content=f"Real content for {name}, with no placeholders."))
    user.profile = {"identity": {"name": "Alice Example"}, "confirmed": True}
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
    """Why there are two secrets."""
    http, _, _, job = api
    assert tap(http, job.id, header=None).status_code == 404
    assert tap(http, job.id, header="wrong").status_code == 404


def test_a_channel_member_who_is_not_the_owner_teaches_nothing(api):
    """A feed channel has other members."""
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
    """`source='feedback'` and `weight=5` have been in the schema since Phase 2 with nothing writing them."""
    from libs.db.models import UserLabel
    from libs.feedback import APPLIED_LABEL, FEEDBACK_WEIGHT

    http, session, user, job = api
    assert tap(http, job.id).json()["status"] == "recorded"
    session.flush()

    label = session.get(UserLabel, (user.id, job.id))
    assert (label.label, label.source, label.weight) == (APPLIED_LABEL, "feedback", FEEDBACK_WEIGHT)
    assert label.profile_generation == user.profile_generation


def test_applying_is_ninety_not_one_hundred(api):
    """People apply to things they are unsure about."""
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
    """`applied_at` is the durable record of a real event."""
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
    """Delivery is Telegram, so a user who reads their feed every morning may never open the web app again."""
    http, session, user, job = api
    assert user.last_active_at is None

    tap(http, job.id)
    session.flush()
    assert user.last_active_at is not None


# ── the retrain trigger ───────────────────────────────────────────────────────────────────────

def test_a_burst_of_taps_produces_exactly_one_retrain(api):
    """Twenty taps is one fit, ten minutes after the last."""
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


def test_the_enqueue_is_a_request_and_the_trainer_is_the_authority(api):
    """Two definitions of "enough new labels" put a red banner on the operator's health page."""
    from sqlalchemy import func, select

    from libs.db.models import QueueJob

    http, session, user, job = api
    tap(http, job.id)
    session.flush()

    assert session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.user_id == user.id, QueueJob.lane == "train")) == 1


def test_nothing_in_the_api_second_guesses_should_retrain():
    """The decision lives in one place."""
    from pathlib import Path

    body = Path("services/api/telegram_routes.py").read_text(encoding="utf-8")

    assert "labels_since_train" not in body
    assert "TRAIN_MIN_NEW_LABELS = " not in body


def test_build_cv_queues_a_docgen_job_carrying_the_posting(api):
    """The tap is the whole contract between the button and the worker."""
    from sqlalchemy import select

    from libs.db.models import QueueJob

    http, session, _, job = api
    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    queued = session.scalar(select(QueueJob).where(QueueJob.lane == "docgen"))
    assert queued is not None
    assert queued.payload["job_id"] == job.id


def test_a_cv_build_over_the_daily_limit_is_refused_at_the_tap(api):
    """The admission point, not the worker."""
    from sqlalchemy import select

    from libs.db.models import QueueJob

    http, session, user, job = api
    # The ceiling comes from `libs.limits` — the one per-user limits system.
    user.limits = {"cv_builds_per_day": 1}
    session.flush()

    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    second = tap(http, job.id, action="bc").json()
    assert second["status"] == "over_quota"
    assert "1 documents for today" in second["answer"] and "resets" in second["answer"]

    assert len(session.scalars(select(QueueJob).where(QueueJob.lane == "docgen")).all()) == 1
    # The per-account override is what the test drove.
    from libs.limits import BY_KEY

    assert BY_KEY["cv_builds_per_day"].default == 10


def test_the_keyboard_carries_three_buttons_within_telegrams_limit(api):
    """`callback_data` is capped at 64 bytes."""
    from services.api.telegram_routes import keyboard

    buttons = keyboard(123)["inline_keyboard"][0]
    assert len(buttons) == 3
    assert all(len(button["callback_data"].encode()) <= 64 for button in buttons)


# ── the two sides agree ───────────────────────────────────────────────────────────────────────

def test_the_buttons_the_notifier_sends_are_the_ones_the_webhook_understands():
    """The notifier renders the keyboard and the API parses the taps, and they may not import each other."""
    from services.api.telegram_routes import CALLBACK_PREFIX, _parse
    from services.notifier.buttons import ACTION_CODES, keyboard

    for action, code in ACTION_CODES.items():
        assert CALLBACK_PREFIX[code] == action

    for button in keyboard(4242)["inline_keyboard"][0]:
        parsed, job_id = _parse(button["callback_data"])
        assert parsed in CALLBACK_PREFIX.values()
        assert job_id == 4242


def test_a_malformed_callback_is_ignored_rather_than_guessed():
    """It is attacker-supplied text."""
    from services.api.telegram_routes import _parse

    for junk in ("", "ap", "ap:", "ap:abc", "xx:1", "ap:1:2", "'; DROP TABLE users--"):
        assert _parse(junk) == (None, 0)


# ── /rate: where the negatives come from ──────────────────────────────────────────────────────

def say(http, text, *, secret=SECRET, from_id=OWNER_CHAT):
    return http.post(
        f"/telegram/webhook/{secret}",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json={"message": {"text": text, "from": {"id": int(from_id)}}})


def _scored(session, user, *, score, description="a real description " * 30, model_version=None):
    from libs.db.models import CorpusJob, UserJobState

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                    title="Engineer", description=description)
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=score,
                             scored_model_version=user.model_version
                             if model_version is None else model_version))
    session.flush()
    return job


def test_rate_offers_the_job_closest_to_the_delivery_threshold(api):
    """The jobs where an answer teaches most are the ones the model is least sure about."""
    http, session, user, _ = api
    _scored(session, user, score=98)
    boundary = _scored(session, user, score=59)
    _scored(session, user, score=12)

    body = say(http, "/rate").json()
    assert body["status"] == "rate"
    assert body["job_id"] == boundary.id


def test_the_pool_includes_jobs_that_were_never_delivered(api):
    """The property that makes `/rate` worth building."""
    from libs.feedback import rating_pool

    _http, session, user, _ = api
    below = _scored(session, user, score=55)
    above = _scored(session, user, score=65)

    pool = rating_pool(session, user.id)
    assert below.id in pool and above.id in pool


def test_a_job_with_no_real_description_is_never_offered(api):
    """The user would be judging a job title, and so would the model they are teaching."""
    from libs.feedback import rating_pool

    _http, session, user, _ = api
    thin = _scored(session, user, score=60, description="Engineer wanted")
    assert thin.id not in rating_pool(session, user.id)


def test_a_job_scored_by_an_older_model_is_not_offered(api):
    """A score from a previous model says nothing about where the current model's boundary is."""
    from libs.feedback import rating_pool

    _http, session, user, _ = api
    stale = _scored(session, user, score=60, model_version=(user.model_version or 0) + 5)
    assert stale.id not in rating_pool(session, user.id)


def test_a_job_already_answered_is_not_asked_about_again(api):
    """Asking twice reads as the platform not listening."""
    from libs.feedback import rating_pool

    http, session, user, _ = api
    job = _scored(session, user, score=60)
    assert job.id in rating_pool(session, user.id)

    tap(http, job.id, action="sk")
    assert job.id not in rating_pool(session, user.id)


def test_rate_says_so_when_there_is_nothing_left(api):
    http, *_ = api
    assert say(http, "/rate").json()["status"] == "nothing_to_rate"


def test_rate_from_a_channel_member_who_is_not_the_owner_is_refused(api):
    """A command is not a lesser thing than a button."""
    http, session, user, _ = api
    _scored(session, user, score=60)
    assert say(http, "/rate", from_id="9999999").json()["status"] == "not_yours"


def test_an_ordinary_message_is_acknowledged_rather_than_refused(api):
    """Telegram resends whatever is not answered with a 2xx."""
    http, *_ = api
    response = say(http, "hello")
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_the_rate_buttons_are_ones_the_webhook_understands():
    """A button whose callback the webhook does not parse is a tap that silently does nothing."""
    from services.api.telegram_routes import CALLBACK_PREFIX, rating_keyboard

    for button in rating_keyboard(1)["inline_keyboard"][0]:
        assert button["callback_data"].split(":")[0] in CALLBACK_PREFIX


# ── /cv <link>: a job the harvest never saw ───────────────────────────────────────────────────

def test_a_pasted_link_becomes_an_ordinary_corpus_row(api, monkeypatch):
    """Not a side table."""
    from libs.corpus import ondemand
    from libs.db.models import CorpusJob

    http, session, _user, _ = api
    url = f"https://acme.example.com/jobs/{uuid.uuid4()}"
    monkeypatch.setattr(ondemand, "fetch_posting", lambda *_a, **_k: {
        "title": "Staff Engineer", "company": "Acme", "location": "Berlin",
        "description": "A real description of a real job. " * 20, "source": "acme.example.com"})

    body = say(http, f"/cv {url}").json()
    assert body["status"] == "queued"

    job = session.get(CorpusJob, body["job_id"])
    assert (job.title, job.company) == ("Staff Engineer", "Acme")
    assert len(job.description) > 200


def test_a_pasted_link_already_in_the_corpus_is_not_added_twice(api, monkeypatch):
    """A second row would give the user two feed entries for one job and split its history."""
    from libs.corpus import ondemand

    http, _session, _user, job = api
    monkeypatch.setattr(ondemand, "fetch_posting",
                        lambda *_a, **_k: pytest.fail("should not have fetched a known job"))

    body = say(http, f"/cv {job.url}").json()
    assert body["status"] == "queued" and body["job_id"] == job.id


def test_a_page_that_yields_nothing_is_reported_rather_than_built(api, monkeypatch):
    """A build against a job with no title and no description would cost the user to find out it was."""
    from libs.corpus import ondemand

    http, *_ = api
    monkeypatch.setattr(ondemand, "fetch_posting", lambda *_a, **_k: {
        "title": "", "company": "", "location": "", "description": "", "source": "x"})

    body = say(http, f"/cv https://blocked.example.com/{uuid.uuid4()}").json()
    assert body["status"] == "bad_link"


def test_a_command_with_no_link_says_what_to_send(api):
    http, *_ = api
    assert say(http, "/cv").json()["status"] == "no_link"


def test_something_that_is_not_a_url_is_refused(api):
    http, *_ = api
    assert say(http, "/cv the one at acme").json()["status"] == "bad_link"


def test_a_build_from_a_link_is_still_the_positive_label(api, monkeypatch):
    """It spends the user's credit whether the job came from the feed or from a paste."""
    from libs.corpus import ondemand
    from libs.db.models import UserLabel

    http, session, user, _ = api
    monkeypatch.setattr(ondemand, "fetch_posting", lambda *_a, **_k: {
        "title": "Staff Engineer", "company": "Acme", "location": "Berlin",
        "description": "A real description of a real job. " * 20, "source": "acme.example.com"})

    body = say(http, f"/cv https://acme.example.com/jobs/{uuid.uuid4()}").json()
    label = session.get(UserLabel, (user.id, body["job_id"]))
    assert label is not None and label.source == "feedback"


def test_a_pasted_link_from_a_channel_member_is_refused(api):
    """It would spend the account owner's credit."""
    http, *_ = api
    assert say(http, "/cv https://acme.example.com/jobs/1",
               from_id="9999999").json()["status"] == "not_yours"


def test_a_build_is_refused_at_the_tap_when_setup_is_unfinished(api):
    """A dead letter about a state only the user can fix."""
    from sqlalchemy import delete, func, select

    from libs.db.models import QueueJob, UserProfileFile

    http, session, user, job = api
    session.execute(delete(UserProfileFile).where(UserProfileFile.user_id == user.id))
    session.flush()

    answer = tap(http, job.id, action="bc").json()

    assert answer["status"] == "not_ready"
    assert "setup" in answer["answer"].lower(), "the popup has to say what to do about it"
    assert session.scalar(
        select(func.count()).select_from(QueueJob).where(QueueJob.lane == "docgen")) == 0, (
        "nothing is queued that could only fail")


def test_a_build_is_refused_when_the_profile_was_never_confirmed(api):
    """The state a real account was stuck in, and the reason nothing arrived.

    The setup chat had written every profile file, so the Telegram handler's `is_ready()` check
    passed and answered "Building your CV — it will arrive here shortly". The worker then asked a
    different question, `user.profile`, found nothing, and refused. Both checks were individually
    correct; between them the platform promised a document it could not build, every time, and
    the queue retried the refusal every sixty seconds.
    """
    http, session, user, job = api
    user.profile = None
    session.flush()

    # `bc` is the wire prefix for Build my CV (`CALLBACK_PREFIX`).
    body = tap(http, job.id, action="bc").json()

    assert body["status"] == "not_ready"
    assert "confirm" in body["answer"].lower()
    # And nothing was queued for a build that cannot happen.
    from libs.db.models import QueueJob
    lanes = [row.lane for row in session.query(QueueJob).filter_by(user_id=user.id).all()]
    assert "docgen" not in lanes


def test_the_admission_check_and_the_worker_ask_the_same_question(api):
    """One definition. Two would drift again, and drifting is what caused the outage."""
    from libs.profiles.repository import ProfileFiles

    _http, session, user, job = api
    user.profile = None
    session.flush()

    blocker = ProfileFiles(session, user.id).build_blocker()

    assert blocker is not None
    # The worker's own refusal path must agree with what the handler told the user.
    from services.docgen import run
    with pytest.raises(run.NotBuildable):
        run._load(session, user.id, job.id)
