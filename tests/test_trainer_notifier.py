"""Trainer and notifier: the invariants that are silent when broken."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import struct
import time
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("numpy")
pytest.importorskip("cryptography")

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState, UserLabel, UserModel
from libs.secrets import KeyRing, SecretStore, generate_key, install_on_handler
from services.notifier.deliver import BotRevoked, deliver, pending_feed
from services.trainer.train import NotEnoughData, build_training_set, should_retrain, train_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

DIM = 8
VERSION = "BAAI/bge-base-en-v1.5|dim=768|norm=1"
BOT_TOKEN = "7123456789:AAF" + "q7Kd2mNp" * 4
PROFILE = {"identity": {"name": "Alice"}, "skills": {"languages": ["Go"]},
           "not_list": ["Java"], "confirmed": True}


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


def _user(s, *, generation: int = 1, setup: bool = True, **kw) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                profile=PROFILE, profile_generation=generation, **kw)
    s.add(user)
    s.flush()
    if setup:
        # Training refuses on an unfinished profile.
        _complete_setup(s, user)
    return user


def _complete_setup(s, user) -> None:
    """Fill every file the setup agent owns, so this account may be trained."""
    from libs.profiles import setup_files
    from libs.profiles.repository import ProfileFiles

    files = ProfileFiles(s, user.id)
    files.seed()
    for name in setup_files():
        files.write(name, f"# {name}\n\nReal content for this account.")
    s.flush()


def _job(s, *, embedded: bool = True, version: str = VERSION) -> CorpusJob:
    job = CorpusJob(url=f"https://boards.greenhouse.io/x/{uuid.uuid4().hex}", source="greenhouse",
                    title="Backend Engineer", company="Acme",
                    description="Go, gRPC, Postgres. " * 20)
    if embedded:
        # `text_sha256` and `embedding_dim` are not optional.
        job.embedding = struct.pack(f"<{DIM}f", *([0.1] * DIM))
        job.text_sha256 = hashlib.sha256((job.description or "").encode()).hexdigest()
        job.embedding_model = "BAAI/bge-base-en-v1.5"
        job.embedding_dim = DIM
        job.embedding_version = version
    s.add(job)
    s.flush()
    return job


def _label(s, user, job, *, score: int = 80, generation: int = 1) -> None:
    s.add(UserLabel(user_id=user.id, job_id=job.id, label=score, weight=1.0,
                    profile_generation=generation))


def _labelled(s, user, n: int, *, generation: int = 1, version: str = VERSION) -> None:
    for _ in range(n):
        _label(s, user, _job(s, version=version), generation=generation)
    s.flush()


class StubEstimator:
    """Module level, not nested in `_fit`: the artifact is pickled."""

    def predict(self, matrix):
        return [70.0] * matrix.shape[0]


def _fit(data):
    return {"model": StubEstimator(), "task": "regress"}


# ── the training set ──────────────────────────────────────────────────────────────────────────

def test_only_labels_at_the_current_generation_are_trained_on(ctx):
    """A label written under an older rubric answers a different question."""
    user = _user(ctx, generation=2)
    _labelled(ctx, user, 30, generation=2)
    _labelled(ctx, user, 30, generation=1)

    data = build_training_set(ctx, user.id)
    assert len(data.targets) == 30


def test_a_mixture_of_embedder_versions_is_refused(ctx):
    """Two vector spaces in one matrix produce a model that is wrong in a way no metric reveals."""
    user = _user(ctx)
    _labelled(ctx, user, 20)
    _labelled(ctx, user, 20, version="BAAI/bge-small-en-v1.5|dim=384|norm=1")

    with pytest.raises(NotEnoughData, match="embedder versions"):
        build_training_set(ctx, user.id)


def test_unembedded_rows_are_dropped_not_zero_filled(ctx):
    """A zero vector is a confident statement that the posting means nothing, and the model would learn it."""
    user = _user(ctx)
    _labelled(ctx, user, 30)
    for _ in range(5):
        _label(ctx, user, _job(ctx, embedded=False))
    ctx.flush()

    assert len(build_training_set(ctx, user.id).targets) == 30


def test_too_few_labels_is_not_an_error(ctx):
    """A new account is the normal case, not a failure — cold start covers it."""
    user = _user(ctx)
    _labelled(ctx, user, 3)
    with pytest.raises(NotEnoughData, match="usable labels"):
        build_training_set(ctx, user.id)


# ── publishing ────────────────────────────────────────────────────────────────────────────────

def test_the_artifact_row_and_the_version_bump_are_one_transaction(ctx, tmp_path):
    """The scorer decides what to serve from `users.model_version`."""
    user = _user(ctx)
    _labelled(ctx, user, 30)

    version = train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()

    assert user.model_version == version == 1
    artifact = ctx.scalar(
        text("SELECT path FROM models WHERE user_id = :u AND version = :v")
        .bindparams(u=user.id, v=version))
    assert artifact and os.path.exists(artifact)


def test_the_artifact_records_what_it_was_trained_against(ctx, tmp_path):
    """Everything the scorer fails closed on: generation, feature schema, and the embedder identity."""
    user = _user(ctx, generation=3)
    _labelled(ctx, user, 30, generation=3)

    train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()
    row = ctx.scalar(text("SELECT id FROM models WHERE user_id = :u").bindparams(u=user.id))
    model = ctx.get(UserModel, row)

    assert model.profile_generation == 3
    assert model.embedder_version == VERSION
    assert model.embedder_dim == DIM
    assert model.n_labels == 30


def test_a_retrain_supersedes_the_previous_artifact(ctx, tmp_path):
    """Exactly one active artifact per user is a partial unique index."""
    user = _user(ctx)
    _labelled(ctx, user, 30)

    train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()
    second = train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()

    active = ctx.execute(
        text("SELECT version FROM models WHERE user_id = :u AND is_active").bindparams(u=user.id)
    ).scalars().all()
    assert active == [second] == [2]


def test_a_trivial_change_does_not_earn_a_retrain(ctx, tmp_path):
    """The queue collapses a burst into one job; this is the other half."""
    user = _user(ctx)
    _labelled(ctx, user, 30)
    train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()

    assert should_retrain(ctx, user.id) is False
    _labelled(ctx, user, 5)
    assert should_retrain(ctx, user.id) is True


def test_training_is_refused_while_setup_is_unfinished(ctx, tmp_path):
    """A model must not be built from a half-written profile."""
    from libs.profiles.repository import ProfileFiles

    user = _user(ctx, setup=False)
    _labelled(ctx, user, 30)
    files = ProfileFiles(ctx, user.id)
    files.seed()
    ctx.flush()

    with pytest.raises(NotEnoughData, match="setup is unfinished"):
        train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)

    _complete_setup(ctx, user)

    assert files.missing_setup() == []
    assert train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit) == 1


def test_a_flag_that_drifted_from_its_content_is_repairable(ctx, tmp_path):
    """`filled` is derived data living in a column, so it can disagree with what it describes."""
    from libs.profiles.repository import ProfileFiles

    user = _user(ctx)
    files = ProfileFiles(ctx, user.id)
    assert all(row["filled"] for row in files.listing() if row["name"] == "context.md")

    ctx.execute(text("UPDATE user_profile_files SET filled = false "
                     "WHERE user_id = :u AND name = 'context.md'").bindparams(u=user.id))
    ctx.flush()
    listed = {row["name"]: row["filled"] for row in files.listing()}
    assert listed["context.md"] is False, "the agent is now told a finished file is empty"
    assert files.missing_setup() == [], "the content-based checks are unaffected by the flag"

    assert files.repair_filled_flags() == ["context.md"]
    ctx.flush()
    assert {row["name"]: row["filled"] for row in files.listing()}["context.md"] is True


def test_a_re_embedded_corpus_earns_a_retrain_with_no_new_labels(ctx, tmp_path):
    """The label count is not the only thing that can go stale — the coordinates can too."""
    user = _user(ctx)
    _labelled(ctx, user, 30)
    train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()
    assert should_retrain(ctx, user.id) is False

    ctx.execute(text("UPDATE corpus SET embedding_version = :v WHERE embedding IS NOT NULL")
                .bindparams(v="BAAI/bge-base-en-v1.5|dim=768|norm=1|text=title_desc"))
    ctx.flush()

    assert should_retrain(ctx, user.id) is True, (
        "a model fitted in a vector space the corpus has left must be retrained")


# ── delivery ──────────────────────────────────────────────────────────────────────────────────

def _scored(s, user, *, score: float = 90.0) -> CorpusJob:
    job = _job(s)
    s.add(UserJobState(user_id=user.id, job_id=job.id, score=score, scored_model_version=1))
    s.flush()
    return job


def _store(s) -> SecretStore:
    return SecretStore(s, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))


def _connected(s) -> User:
    return _user(s, telegram_status="connected", telegram_chat_id="12345", active=True,
                 status="active")


def test_a_delivered_job_is_never_delivered_again(ctx):
    """The queue is at-least-once, so the dedup is what makes a retry safe."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    ctx.flush()

    sent = []
    assert deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m)) == 1
    jobs_sent = [m for m in sent if "http" in m]
    ctx.flush()
    assert deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m)) == 0
    assert len(jobs_sent) == 1, "the job goes out once and never again"


def test_only_scores_above_the_cutoff_are_sent(ctx):
    user = _connected(ctx)
    _scored(ctx, user, score=90.0)
    _scored(ctx, user, score=20.0)
    ctx.flush()
    items = pending_feed(ctx, user.id, cutoff=60.0, limit=10)
    assert len(items) == 1


def test_a_revoked_bot_is_terminal_and_flags_the_account(ctx):
    """Telegram answers 403 and it will never succeed."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    ctx.flush()

    def blocked(token, chat_id, message, keyboard=None):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    with pytest.raises(BotRevoked):
        deliver(ctx, user.id, store=store, send=blocked)
    assert user.telegram_status == "broken"


def test_a_transient_failure_is_not_terminal(ctx):
    """A timeout must stay retryable, or one bad minute permanently disconnects a working account."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    ctx.flush()

    def timeout(token, chat_id, message, keyboard=None):
        raise TimeoutError("read timeout")

    with pytest.raises(TimeoutError):
        deliver(ctx, user.id, store=store, send=timeout)
    assert user.telegram_status == "connected"


def test_the_daily_cap_defers_rather_than_dropping(ctx):
    """Over cap, items keep `notified_at IS NULL` and go out in the next window."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    job = _scored(ctx, user)
    ctx.flush()

    from libs import limits

    limits.set_limit(ctx, user.id, "notifications_per_day", 1)
    ctx.flush()
    # Already at their ceiling for today, so this run has nothing left to spend.
    ctx.get(UserJobState, (user.id, job.id))
    assert deliver(ctx, user.id, store=store, send=lambda *a: None) == 1
    ctx.flush()
    second = _scored(ctx, user)
    ctx.flush()
    assert deliver(ctx, user.id, store=store, send=lambda *a: None) == 0
    assert ctx.get(UserJobState, (user.id, second.id)).notified_at is None


def test_the_token_reaches_the_transport_and_nothing_else(ctx):
    """Decrypted for the call and scrubbed from anything logged in that window."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    ctx.flush()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_on_handler(handler)
    logger = logging.getLogger("notifier-test")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    seen = {}

    def send(token, chat_id, message, keyboard=None):
        seen["token"] = token
        logger.info("posting to telegram with %s", token)

    try:
        deliver(ctx, user.id, store=store, send=send)
    finally:
        logger.removeHandler(handler)

    assert seen["token"] == BOT_TOKEN, "the transport gets the real value"
    assert BOT_TOKEN not in stream.getvalue(), "and the log does not"


def test_a_delivered_job_is_never_shown_as_borderline():
    """The dot and the decision to send were two different numbers."""
    from services.notifier.card import FeedItem
    from services.notifier.deliver import DEFAULT_CUTOFF, render_job

    item = FeedItem(job_id=1, url="https://example.com/1", title="Backend Engineer",
                    company="Acme", score=68.0)
    assert "🟢" in render_job(item, cutoff=DEFAULT_CUTOFF)
    assert "🟡" not in render_job(item, cutoff=DEFAULT_CUTOFF)


def test_a_job_below_the_cutoff_still_reads_as_borderline():
    """The band has to mean something. Everything green is the same as everything yellow."""
    from services.notifier.card import FeedItem
    from services.notifier.deliver import render_job

    item = FeedItem(job_id=1, url="https://example.com/1", title="Backend Engineer",
                    company="Acme", score=55.0)
    assert "🟡" in render_job(item, cutoff=60.0)


def test_the_operators_ceiling_is_what_is_enforced(ctx):
    """The cap moved owner on 2026-08-01."""
    from libs import limits

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    limits.set_limit(ctx, user.id, "notifications_per_day", 1)
    for _ in range(3):
        _scored(ctx, user)
    ctx.flush()

    sent: list[str] = []
    assert deliver(ctx, user.id, store=store,
                   send=lambda t, c, m, k=None: sent.append(m)) == 1, "the ceiling set for them"
    ctx.flush()
    assert deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m)) == 0


def test_unlimited_delivery_sends_everything_waiting(ctx):
    """`null` is a real setting, not an absent one."""
    from libs import limits

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    limits.set_limit(ctx, user.id, "notifications_per_day", None)
    limits.set_limit(ctx, user.id, "items_per_run", 5)
    # No gap in the test: the pacing is Telegram's concern and a real sleep would just be slow.
    limits.set_limit(ctx, user.id, "send_gap_seconds", 1)
    for _ in range(5):
        _scored(ctx, user)
    ctx.flush()

    sent: list[str] = []
    assert deliver(ctx, user.id, store=store,
                   send=lambda t, c, m, k=None: sent.append(m)) == 5


def test_a_stale_high_scorer_is_ranked_behind_a_fresh_one_rather_than_filtered(ctx):
    """Delivery ranks; the age window is the pre-score filter's job and runs before scoring."""
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob, UserJobState
    from services.notifier.deliver import pending_feed

    user = _connected(ctx)
    now = datetime.now(UTC)

    # A month-old backlog that scores well, and one fresh job that scores less.
    for index in range(12):
        stale = CorpusJob(url=f"https://example.com/stale-{uuid.uuid4()}", source="linkedin",
                          title=f"Stale {index}", description="x" * 400,
                          posted_at=now - timedelta(days=30))
        ctx.add(stale)
        ctx.flush()
        ctx.add(UserJobState(user_id=user.id, job_id=stale.id, score=95.0))

    fresh = CorpusJob(url=f"https://example.com/fresh-{uuid.uuid4()}", source="linkedin",
                      title="Fresh Backend Engineer", description="x" * 400,
                      posted_at=now - timedelta(hours=1))
    ctx.add(fresh)
    ctx.flush()
    ctx.add(UserJobState(user_id=user.id, job_id=fresh.id, score=65.0))
    ctx.flush()

    items = pending_feed(ctx, user.id, cutoff=60.0, limit=5)

    assert items[0].title == "Fresh Backend Engineer", (
        "a month-old posting that scores 95 must not lead a digest over an hour-old one at 65")


# ── declining to retrain is a decision, not a failure (ROADMAP 6ah) ───────────────────────────


def test_declining_to_retrain_completes_the_job_rather_than_dead_lettering(ctx, monkeypatch):
    """"1 job(s) dead-lettered in the train lane" was the trainer working correctly."""
    from libs.db.models import QueueJob
    from libs.queue.worker import Terminal
    from services.trainer import __main__ as trainer

    user = _user(ctx)
    monkeypatch.setattr(trainer, "should_retrain", lambda *_a, **_kw: False)

    job = QueueJob(lane="train", user_id=user.id, payload={}, state="claimed")
    ctx.add(job)
    ctx.flush()

    try:
        trainer.handle(ctx, job)
    except Terminal as exc:                                     # pragma: no cover - the regression
        pytest.fail(f"declining to retrain must not dead-letter: {exc}")


def test_declining_says_so_in_the_log(ctx, monkeypatch, caplog):
    """There is no debugger on the box, so the answer to "why did nothing retrain" has to be a line."""
    import logging

    from libs.db.models import QueueJob
    from services.trainer import __main__ as trainer

    user = _user(ctx)
    monkeypatch.setattr(trainer, "should_retrain", lambda *_a, **_kw: False)
    job = QueueJob(lane="train", user_id=user.id, payload={}, state="claimed")
    ctx.add(job)
    ctx.flush()

    with caplog.at_level(logging.INFO):
        trainer.handle(ctx, job)

    assert any("no new labels" in record.getMessage() for record in caplog.records)


# ── the flood-then-silence failure (ROADMAP 6ai) ──────────────────────────────────────────────


def test_a_send_that_fails_partway_does_not_resend_what_already_went(ctx, monkeypatch):
    """The duplicates-then-silence failure, in one test."""
    from services.notifier import deliver as deliver_module

    # The pacing gap is Telegram's rule and is tested elsewhere; here it would only make the suite slow.
    monkeypatch.setattr(deliver_module, "sleep", lambda _seconds: None)

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    for _ in range(5):
        _scored(ctx, user)
    ctx.flush()

    sent: list[str] = []

    def fail_on_the_fourth(_token, _chat, message, _keyboard=None):
        if len(sent) == 3:
            raise RuntimeError("Bad Gateway")
        sent.append(message)

    with pytest.raises(RuntimeError):
        deliver(ctx, user.id, store=store, send=fail_on_the_fourth)

    # The three that went are recorded, so the retry picks up where it stopped.
    delivered = ctx.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.user_id == user.id, UserJobState.notified_at.is_not(None)))
    assert delivered == 3

    resent: list[str] = []
    deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: resent.append(m))
    assert len(resent) == 2, "only the two that never went are sent"


def test_a_run_stops_before_its_claim_expires(ctx, monkeypatch):
    """15 of 319 notify jobs overran their two-minute claim, the worst at 638 seconds."""
    from services.notifier import deliver as deliver_module

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    for _ in range(10):
        _scored(ctx, user)
    ctx.flush()

    # A budget that expires part-way through, with no real waiting.
    monkeypatch.setattr(deliver_module, "SEND_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(deliver_module, "sleep", lambda _seconds: None)

    sent: list[str] = []

    def slow(_token, _chat, message, _keyboard=None):
        sent.append(message)
        time.sleep(0.04)

    count = deliver(ctx, user.id, store=store, send=slow)

    assert count < 10, "the run must stop rather than overrun its claim"
    assert count == len(sent)
    # And what was not sent is still pending, not lost.
    pending = ctx.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.user_id == user.id, UserJobState.notified_at.is_(None)))
    assert pending == 10 - count


def test_the_send_budget_fits_inside_the_notify_claim():
    """Derived rather than chosen, so the claim timeout cannot be raised without this following."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.deliver import SEND_BUDGET_SECONDS

    claim = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim > SEND_BUDGET_SECONDS, (
        "the send has to finish inside one claim, or the job is swept back to ready while it is "
        "still working")


def test_an_undated_row_too_old_to_send_is_stopped_before_scoring_not_at_delivery(ctx):
    """One filter, once. Delivery sends what scoring admitted, so scoring is where the age is read."""
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob, UserJobState
    from libs.tenant.filters import UserFilters, candidate_conditions

    user = _connected(ctx)
    stale = _scored(ctx, user)
    stale.posted_at = None
    stale.first_seen_at = datetime.now(UTC) - timedelta(days=3)
    ctx.flush()

    # Delivery no longer judges: the row is above the cutoff, so it is in the pool.
    assert stale.id in {item.job_id for item in pending_feed(ctx, user.id, cutoff=0.0, limit=10)}

    # And the pre-score filter is what keeps it from ever getting there.
    admitted = ctx.scalars(
        select(CorpusJob.id).join(UserJobState, UserJobState.job_id == CorpusJob.id)
        .where(UserJobState.user_id == user.id,
               *candidate_conditions(UserFilters.from_config({}, max_age_hours=24)))).all()
    assert stale.id not in admitted


# ── nothing stands between a scored match and Telegram ──────────────────────────────────────────


def test_nothing_caps_a_delivery_run_by_default(ctx):
    """The default is Unlimited, and it has to be the default rather than a setting somebody remembers."""
    from libs.limits import limit_default

    assert limit_default("items_per_run") is None


def test_a_run_sends_every_match_it_has_time_for(ctx):
    """Not ten. The old default stopped at a number unrelated to anything the run could do."""
    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    for _ in range(14):
        _scored(ctx, user)
    ctx.flush()

    sent = []
    delivered = deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m))
    assert delivered == 14, "a cap unrelated to the send budget withheld four of these"


def test_the_rest_of_the_feed_is_queued_immediately_rather_than_on_a_clock(ctx):
    """There is no cadence to wait for: a run that fills its limit queues its own continuation."""
    from libs import limits
    from libs.db.models import QueueJob

    user = _connected(ctx)
    limits.set_limit(ctx, user.id, "items_per_run", 1)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    _scored(ctx, user)
    ctx.flush()

    assert deliver(ctx, user.id, store=store, send=lambda *a, **k: None) == 1
    ctx.flush()

    waiting = ctx.scalars(
        select(QueueJob).where(QueueJob.lane == "notify", QueueJob.user_id == user.id,
                               QueueJob.state == "ready")).all()
    assert len(waiting) == 1, (
        "up to 60 seconds of every continuation was spent idle waiting for the dispatcher tick")
