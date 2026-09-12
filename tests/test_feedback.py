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
    # Set here rather than relied on from another file: `setdefault` in a fixture leaks into the
    # process, so these four tests passed only when a file that sets it had already run. They are
    # early in the alphabet, so on a clean shell they errored and nothing else did.
    os.environ.setdefault("SECRETS_KEK", "k1:" + "A" * 43 + "=")
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


def test_every_callback_stays_inside_telegrams_sixty_four_bytes(api):
    """`callback_data` is capped at 64 bytes, and a job id grows."""
    from libs.notify.buttons import feed_keyboard, rating_keyboard

    for build in (feed_keyboard, rating_keyboard):
        for button in build(9_999_999_999)["inline_keyboard"][0]:
            assert len(button["callback_data"].encode()) <= 64


# ── the two sides agree ───────────────────────────────────────────────────────────────────────

def test_every_button_the_platform_sends_is_one_the_webhook_parses():
    """The emitter and the parser are different services, and they may not import each other.

    There were three copies of this agreement and one had drifted: the API kept a `keyboard()` still
    returning the pre-2026-07-29 buttons (✅ Applied / 🚫 Skip / 📄 Build CV), sent by nothing, and
    the test here asserted *that* copy carried three buttons while the feed was sending two. The codes
    now live in `libs/notify/buttons.py` and the keyboards are built from them, so a button cannot
    carry a prefix the parser does not know.
    """
    from libs.notify.buttons import feed_keyboard, rating_keyboard
    from services.api.telegram_routes import _parse

    for build in (feed_keyboard, rating_keyboard):
        for button in build(4242)["inline_keyboard"][0]:
            action, job_id = _parse(button["callback_data"])
            assert action is not None, f"{button['callback_data']} parses to nothing"
            assert job_id == 4242


def test_every_wire_code_is_emitted_by_a_button_somewhere():
    """A code no keyboard sends is a value with no consumer, and that is how a dead copy survives.

    `ACTION_CODES` in the notifier held four codes, two of which no keyboard in that module emitted;
    its only reader was the test asserting it matched the parser. Both are gone. This asks the
    question that would have found it: does every code the parser accepts correspond to a button a
    person can actually tap?
    """
    from libs.notify.buttons import CALLBACK_CODES, feed_keyboard, rating_keyboard

    emitted = {button["callback_data"].split(":")[0]
               for build in (feed_keyboard, rating_keyboard)
               for button in build(1)["inline_keyboard"][0]}

    assert emitted == set(CALLBACK_CODES.values()), (
        f"codes with no button: {set(CALLBACK_CODES.values()) - emitted}")


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


def test_the_admission_check_and_the_worker_ask_the_same_question(api):
    """One definition. Two would drift again, and drifting is what caused the outage.

    The condition is now "has the setup chat written this account's files", and nothing else. It
    used to be that plus a confirmed `user.profile`, asked in two places with different answers —
    the handler checked the files and promised a document, the worker checked the schema and
    refused. What retired the second question was finding that the only thing still reading the
    schema was the line that names the PDF.
    """
    from sqlalchemy import delete

    from libs.db.models import UserProfileFile
    from libs.profiles.repository import ProfileFiles
    from services.docgen import run

    _http, session, user, job = api
    session.execute(delete(UserProfileFile).where(UserProfileFile.user_id == user.id))
    session.flush()

    assert ProfileFiles(session, user.id).build_blocker() is not None
    # The worker's own refusal path must agree with what the handler told the user.
    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, job.id, kind="cv", client=None,
                      send_document=lambda *a, **k: None, token="x")


def test_building_records_the_label_but_does_not_start_a_fit(api):
    """A build is a training point saved for later, never a reason to train now.

    A build spends the account's own Claude credits, so choosing to spend them on a posting is the
    strongest signal the platform gets — and the rarest. Fitting a model on each one meant one fit
    per build, over a single new row, on the user's own credential. The label is what matters and
    it is written; when a fit eventually runs it is there waiting.
    """
    from libs.db.models import QueueJob, UserLabel
    from libs.feedback import APPLIED_LABEL

    http, session, user, job = api
    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    label = session.get(UserLabel, (user.id, job.id))
    assert label is not None and label.label == APPLIED_LABEL, "the training point is kept"

    lanes = [row.lane for row in session.query(QueueJob).filter_by(user_id=user.id).all()]
    assert "docgen" in lanes, "the document is still built"
    assert "train" not in lanes, "and nothing was fitted because of it"


def test_an_explicit_thumbs_up_still_starts_a_fit(api):
    """The taps are free and arrive in bursts, which is what makes them a good trigger."""
    from libs.db.models import QueueJob

    http, session, user, job = api
    tap(http, job.id)
    session.flush()

    lanes = [row.lane for row in session.query(QueueJob).filter_by(user_id=user.id).all()]
    assert "train" in lanes


# ── the reply nobody was getting ───────────────────────────────────────────────────────────────
#
# Every handler above composes a sentence for the person who tapped, and until `_reply` existed all
# of them went into the HTTP response body — which Telegram discards unless it names a Bot API
# method. So a tap spun and did nothing, `/rate` answered with a card that was never sent, and a
# refusal was never seen. These tests assert the sentence leaves the building.

@pytest.fixture()
def bot(api, monkeypatch):
    """The api fixture, with a stored bot token and every Bot API call captured."""
    from libs.notify import telegram
    from libs.secrets import SecretStore

    http, session, user, job = api
    SecretStore(session).put(user.id, "telegram_bot", "123456:AAtesttoken")
    session.flush()

    calls: list[tuple] = []
    monkeypatch.setattr(telegram, "answer_callback",
                        lambda token, query_id, text="": calls.append(("answer", text)) or True)
    monkeypatch.setattr(telegram, "send_message",
                        lambda token, chat_id, text, keyboard=None:
                        calls.append(("message", chat_id, text, keyboard)) or True)
    return http, session, user, job, calls


def test_a_build_tap_tells_the_person_it_started(bot):
    """The one acknowledgement that matters: a build takes minutes, so silence reads as broken."""
    http, _, _, job, calls = bot
    assert tap(http, job.id, action="bc").json()["status"] == "queued"

    assert calls, "the tap was answered"
    kind, text = calls[0]
    assert kind == "answer"
    assert "Building your CV" in text


def test_a_refusal_reaches_the_person_who_tapped(bot):
    """A refused build is the case with no other evidence — no document ever arrives."""
    from libs import limits
    from libs.queue import enqueue

    http, session, user, job, calls = bot
    # Nothing left for today.
    allowed = limits.resolve(user, "cv_builds_per_day")
    for index in range(allowed):
        enqueue(session, "docgen", user.id, payload={"job_id": 900_000 + index, "kind": "cv"},
                dedup_key=f"cv:{900_000 + index}")
    session.flush()
    calls.clear()

    body = tap(http, job.id, action="bc").json()
    assert body["status"] == "over_quota"
    assert calls and calls[0][0] == "answer", "the refusal was delivered, not swallowed"
    assert calls[0][1], "and it says something"


def test_rate_sends_the_card_it_composes(bot):
    """`/rate` built a keyboard and returned it to Telegram's discarded response body."""
    http, session, user, _, calls = bot
    _scored(session, user, score=50.0)
    session.flush()
    calls.clear()

    assert say(http, "/rate").json()["status"] == "rate"
    assert calls, "the card was sent"
    kind, _chat_id, _text, keyboard = calls[0]
    assert kind == "message"
    assert keyboard, "with its two answers attached"
    assert [b["callback_data"].split(":")[0] for b in keyboard["inline_keyboard"][0]] == ["ap", "sk"]


def test_nothing_is_said_when_nothing_happened(bot):
    """An ordinary message is acknowledged to Telegram and never answered to the person."""
    http, _, _, _, calls = bot
    assert say(http, "good morning").json()["status"] == "ignored"
    assert calls == []


def test_a_tap_is_answered_even_when_the_bot_token_has_gone(api):
    """A missing token loses the acknowledgement, never the build."""
    from libs.db.models import QueueJob

    http, session, user, job = api
    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    lanes = [row.lane for row in session.query(QueueJob).filter_by(user_id=user.id).all()]
    assert "docgen" in lanes


# ── the gate that refused every chat-onboarded account ─────────────────────────────────────────

def test_an_account_that_only_finished_the_setup_chat_can_build(api):
    """Guided setup writes the ten profile files and never writes `users.profile`.

    That was criterion three of eight, and it refused the normal onboarding path: the drill account
    finished every step of the setup chat, had ten filled files and a CV that built by hand, and was
    told "Confirm your details first". The schema was required in order to name a PDF.
    """
    from libs.db.models import QueueJob
    from libs.profiles.repository import ProfileFiles

    http, session, user, job = api
    user.profile = None
    user.full_name = "Dana Testerson"
    session.flush()

    assert ProfileFiles(session, user.id).build_blocker() is None
    assert tap(http, job.id, action="bc").json()["status"] == "queued"
    session.flush()

    lanes = [row.lane for row in session.query(QueueJob).filter_by(user_id=user.id).all()]
    assert "docgen" in lanes


def test_the_document_is_named_from_the_name_the_user_typed():
    """`users.full_name` is hard data; the capture schema was a second copy of it."""
    from services.docgen.run import _filename

    assert _filename("Andras Lekli") == "Andras_Lekli_CV.pdf"
    assert _filename("Andras Lekli", "cover") == "Andras_Lekli_Cover.pdf"
    # Nullable, and a build must not fail over a filename.
    assert _filename(None).endswith(".pdf")
