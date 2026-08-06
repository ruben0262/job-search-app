"""Trainer and notifier: the invariants that are silent when broken.

Deliberately not a test per function. What is worth pinning here is narrow:

- the training set is scoped by `profile_generation`, and never mixes embedder versions
- the artifact row and the `model_version` bump are one transaction
- a delivery is never repeated, and a revoked bot is terminal rather than retried
- the token is decrypted for the call and scrubbed from anything logged in that window

The estimator and the Telegram transport are both injected. Whether scikit-learn fits a ridge
regression, and whether `httpx` posts correctly, are not this suite's business.
"""
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
        # Training refuses on an unfinished profile, so the default account here is one that has
        # finished setup. `setup=False` gets the other case, which its own test exercises.
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
        # `text_sha256` and `embedding_dim` are not optional: the corpus has a CHECK constraint
        # requiring a vector to carry the provenance that proves which text it was built from.
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
    """Module level, not nested in `_fit`: the artifact is pickled, and a class defined inside a
    function has no importable path for pickle to record."""

    def predict(self, matrix):
        return [70.0] * matrix.shape[0]


def _fit(data):
    return {"model": StubEstimator(), "task": "regress"}


# ── the training set ──────────────────────────────────────────────────────────────────────────

def test_only_labels_at_the_current_generation_are_trained_on(ctx):
    """A label written under an older rubric answers a different question. Mixing generations
    trains on two contradictory definitions of "good match" (`PROFILE_CAPTURE_PLAN.md` §6.4)."""
    user = _user(ctx, generation=2)
    _labelled(ctx, user, 30, generation=2)
    _labelled(ctx, user, 30, generation=1)

    data = build_training_set(ctx, user.id)
    assert len(data.targets) == 30


def test_a_mixture_of_embedder_versions_is_refused(ctx):
    """Two vector spaces in one matrix produce a model that is wrong in a way no metric reveals —
    the holdout is drawn from the same mixture."""
    user = _user(ctx)
    _labelled(ctx, user, 20)
    _labelled(ctx, user, 20, version="BAAI/bge-small-en-v1.5|dim=384|norm=1")

    with pytest.raises(NotEnoughData, match="embedder versions"):
        build_training_set(ctx, user.id)


def test_unembedded_rows_are_dropped_not_zero_filled(ctx):
    """A zero vector is a confident statement that the posting means nothing, and the model would
    learn it."""
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
    """The scorer decides what to serve from `users.model_version`. If that moved without its
    artifact row, every score for the user fails until somebody notices."""
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
    """Everything the scorer fails closed on: generation, feature schema, and the embedder identity.
    An artifact that did not record them could not be checked."""
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
    """Exactly one active artifact per user is a partial unique index, so the previous flag has to
    be cleared in the same write rather than trusted to be cleared later."""
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
    """The queue collapses a burst into one job; this is the other half. A job that fires anyway
    when one label arrived spends a training slot on nothing."""
    user = _user(ctx)
    _labelled(ctx, user, 30)
    train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
    ctx.flush()

    assert should_retrain(ctx, user.id) is False
    _labelled(ctx, user, 5)
    assert should_retrain(ctx, user.id) is True


def test_training_is_refused_while_setup_is_unfinished(ctx, tmp_path):
    """**A model must not be built from a half-written profile.**

    The failure is silent, which is why it needs a gate rather than care: the labels look like
    labels, the metrics look reasonable, and the feed is quietly wrong for as long as the artifact
    lives. Found in production on 2026-07-31 — an account with four unfilled profile files and five
    missing ones had 945 labels and an active model.
    """
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
    """`filled` is derived data living in a column, so it can disagree with what it describes.

    It did, in production on 2026-07-31: an account held four files of real, marker-free content —
    13k to 32k characters each — every one flagged unfilled. **What that breaks is the setup
    agent's own view of its work.** `grounding()` and `missing_setup()` read the content and are
    immune, but `listing()` reports the column, so the agent is told a finished file is "still the
    empty scaffold" and may set about writing it again. The content is the authority, so the flag
    is repairable rather than a support ticket.
    """
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
    """**The label count is not the only thing that can go stale — the coordinates can too.**

    Re-embedding moves every vector, and a model fitted in the old space cannot read the new one:
    its weights are indexed to axes that no longer mean the same thing. None of that arrives as a
    new label. On 2026-07-31 exactly this refused a retrain after a full corpus re-embed, as "not
    enough new labels", while the active artifact was already unusable and the scorer was failing
    closed on the mismatch.
    """
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
    """The queue is at-least-once, so the dedup is what makes a retry safe. Without it every
    retried job re-sends the same digest."""
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
    items, dropped = pending_feed(ctx, user.id, cutoff=60.0)
    assert len(items) == 1
    assert dropped == {}          # the low score never reached the filters — the cutoff took it


def test_a_revoked_bot_is_terminal_and_flags_the_account(ctx):
    """Telegram answers 403 and it will never succeed. Retrying wastes the attempts and then goes
    quiet anyway — the user's feed stops with no signal to anyone (G6)."""
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
    """Over cap, items keep `notified_at IS NULL` and go out in the next window. Dropping them
    would lose a match the user never learns existed (`TENANT_SAFETY.md` §3)."""
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
    """Decrypted for the call and scrubbed from anything logged in that window — including the
    traceback an HTTP client raises, which is exactly where a failed request puts a token."""
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
    """**The dot and the decision to send were two different numbers.** The card called anything
    under 80 a 🟡 while delivery sent everything at 60 and above, so a 68 — a job the platform
    decided was worth interrupting somebody for — arrived wearing the colour that means "do not get
    excited". The reference calls a job high at `min_score_for_cv`, the same score it will build a
    CV at."""
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
    """**The cap moved owner on 2026-08-01.** It was `max_notifications_per_day` in the user's own
    config, which is the wrong hands: somebody who can raise their own delivery ceiling can get
    their own Telegram bot rate-limited. It is an operator-set limit now (`libs.limits`), and
    `null` means unlimited because sending is paced rather than capped.
    """
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
    """`null` is a real setting, not an absent one. It is safe because the send loop paces itself
    to Telegram's 20-per-minute channel limit rather than relying on a daily cap to stay under it."""
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


def test_stale_high_scorers_do_not_crowd_fresh_jobs_out_of_the_pool(ctx):
    """**Fifty candidates, fifty dropped, zero sent — with twenty-five fresh matches waiting.**

    The pool is ordered by score, so a backlog of stale high-scoring rows filled every candidate
    slot and was then dropped for age. Filtering age only *after* selection means the fresh jobs
    one page further down are never looked at.
    """
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob, UserConfig, UserJobState
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

    # The window is read from the account's own stored config now, because there is only one
    # filter and it is the account's — nothing is passed in beside it.
    ctx.add(UserConfig(user_id=user.id, config={"sections": [{"posted_within_hours": 6}]}))
    ctx.flush()
    items, _dropped = pending_feed(ctx, user.id, cutoff=60.0, limit=5)

    assert [item.title for item in items] == ["Fresh Backend Engineer"]


# ── declining to retrain is a decision, not a failure (ROADMAP 6ah) ───────────────────────────


def test_declining_to_retrain_completes_the_job_rather_than_dead_lettering(ctx, monkeypatch):
    """**"1 job(s) dead-lettered in the train lane" was the trainer working correctly.**

    `handle` raised `Terminal` when `should_retrain` said no, and `Terminal` dead-letters — so an
    account that added a label after its model was already current produced a red banner on the
    operator's health page about nothing at all. A dead letter should mean work was lost.

    The job asked a question, got "no", and is complete.
    """
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
    """There is no debugger on the box, so the answer to "why did nothing retrain" has to be a
    line somebody can grep. Removing the dead letter without leaving one would trade a false alarm
    for silence (CLAUDE.md §7)."""
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
    """**The duplicates-then-silence failure, in one test.**

    `notified_at` was marked for every item only after the whole digest had gone. So a run that
    failed on message four rolled back the marks for one, two and three — which Telegram had
    already delivered — and the retry sent them again. Measured live as bursts of hundreds followed
    by hours of nothing.

    A sent message cannot be un-sent, so its record must not depend on the rest of the run.
    """
    from services.notifier import deliver as deliver_module

    # The pacing gap is Telegram's rule and is tested elsewhere; here it would only make the suite
    # slow, so the wait is a no-op and the clock below is what the test is about.
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
    """**15 of 319 notify jobs overran their two-minute claim, the worst at 638 seconds.**

    Sending is paced, so a large digest simply cannot finish inside the claim: at a three-second
    gap, 200 items is 600 seconds. An overrun is swept back to `ready` while the worker is still
    sending, a second worker starts the same digest from the top, and the account gets the flood.

    So the loop is bounded by the clock rather than by a count that cannot know the gap.
    """
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
    """Derived rather than chosen, so the claim timeout cannot be raised without this following.
    Two constants kept equal by hand is the defect this project ships most often (CLAUDE.md §5)."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.deliver import SEND_BUDGET_SECONDS
    from services.notifier.freshness import REFRESH_BUDGET_SECONDS

    claim = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim > SEND_BUDGET_SECONDS + REFRESH_BUDGET_SECONDS, (
        "the refresh and the send together have to finish inside one claim, or the job is swept "
        "back to ready while it is still working")


def test_an_undated_row_held_past_the_window_is_not_reconsidered_every_cycle(ctx):
    """**Work repeated forever, and a drop tally that lied about why.**

    Undated rows were admitted to the candidate pool unconditionally, on the reasoning that
    `date_candidates` would resolve them. That needs the account's own proxy, and an account without
    one can never date anything — so rows first seen days earlier were selected and dropped as
    `too_old` on every cycle, indefinitely. The live log showed the identical counts tick after
    tick: `{'applicant_glut': 4, 'workplace': 1, 'too_old': 11}` at 10:49 and again at 11:04
    (ROADMAP 6ay).

    They were always going to be dropped — `too_old` uses `first_seen_at` as its fallback and got
    the right answer. What was wrong is that SQL asked a *weaker* question than Python, so the rows
    were fetched, ranked and re-judged every fifteen minutes to reach a conclusion that could not
    change, and the operator's drop tally reported a filter that was really just history.
    """
    from datetime import UTC, datetime, timedelta

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)

    stale = _scored(ctx, user)
    stale.posted_at = None
    stale.first_seen_at = datetime.now(UTC) - timedelta(days=3)
    ctx.flush()

    items, dropped = pending_feed(ctx, user.id, cutoff=0.0)

    assert stale.id not in {item.job_id for item in items}
    assert "too_old" not in dropped, (
        "the row must not reach the pool at all — reporting it as dropped means it was fetched, "
        "ranked and judged again for an answer that cannot change")


# ── nothing stands between a scored match and Telegram ──────────────────────────────────────────
#
# The owner's instruction, and the measurement behind it: `items_per_run` defaulted to 10, which is
# 960 a day against 96 cycles. Measured on the live box 2026-08-06 — 931 delivered, and **576 matched
# postings that expired unsent**. The cap was the platform's largest single loss of work, and every
# posting it withheld had already passed the filter and the model.


def test_nothing_caps_a_delivery_run_by_default(ctx):
    """**The default is Unlimited, and it has to be the default rather than a setting somebody
    remembers.** Filtering and scoring already decided what is worth sending; a further ceiling
    only delays a posting the platform has judged, and the product's whole claim is being early to
    it."""
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


def test_a_run_that_could_not_finish_makes_the_account_due_again_now(ctx):
    """**The other half, and the one that actually cost the 576.**

    A run stops at its claim so a second worker cannot start the same digest — that stays, it is the
    fix for the flood-then-silence incident. What was wrong is what happened next: the remainder
    waited a full cadence, so a backlog drained at one run's worth every fifteen minutes while its
    tail aged past the freshness window and was never sent at all.

    Now the account is due immediately, and the dispatcher already running inside the worker picks
    it straight back up — no second mechanism, no new clock.
    """
    from datetime import UTC, datetime, timedelta

    from libs import limits

    user = _connected(ctx)
    # An explicit per-run ceiling is still honoured; it just no longer means "and then wait".
    limits.set_limit(ctx, user.id, "items_per_run", 1)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    _scored(ctx, user)
    _scored(ctx, user)
    ctx.flush()

    before = datetime.now(UTC)
    assert deliver(ctx, user.id, store=store, send=lambda *a, **k: None) == 1
    ctx.flush()
    ctx.refresh(user)

    assert user.next_run_at is not None
    due = user.next_run_at if user.next_run_at.tzinfo else user.next_run_at.replace(tzinfo=UTC)
    assert due <= before + timedelta(seconds=5), (
        "the rest of the queue waits a whole cadence, which is how a backlog's tail expires unsent")
