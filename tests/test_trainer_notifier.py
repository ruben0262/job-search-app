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
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("numpy")
pytest.importorskip("cryptography")

from sqlalchemy import create_engine, text
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


def _user(s, *, generation: int = 1, **kw) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                profile=PROFILE, profile_generation=generation, **kw)
    s.add(user)
    s.flush()
    return user


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

    assert deliver(ctx, user.id, store=store, send=lambda *a: None, daily_cap=0) == 0
    assert ctx.get(UserJobState, (user.id, job.id)).notified_at is None


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


def test_the_users_own_cap_is_what_is_enforced(ctx):
    """**`max_notifications_per_day` was read by nothing.** It is in the config schema and on the
    dashboard ("At most 20 messages a day"), and `deliver` used its own 100 regardless — so an
    account that asked for 20 received 160 in sixteen hours (found on the box, 2026-07-30)."""
    from libs.db.models import UserConfig

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    ctx.add(UserConfig(user_id=user.id, schema_version=2,
                       config={"sections": [{"platform": "all", "keywords": ["engineer"]}],
                               "max_notifications_per_day": 1}))
    for _ in range(3):
        _scored(ctx, user)
    ctx.flush()

    sent: list[str] = []
    assert deliver(ctx, user.id, store=store,
                   send=lambda t, c, m, k=None: sent.append(m)) == 1, "their cap of 1, not our 100"
    ctx.flush()
    assert deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m)) == 0


def test_a_v1_config_cap_is_honoured_too(ctx):
    """A flat v1 row keeps the cap at the top level like everything else, and `migrate_config`
    is what makes both shapes readable — the same gap that swallowed the age window."""
    from libs.db.models import UserConfig

    user = _connected(ctx)
    store = _store(ctx)
    store.put(user.id, "telegram_bot", BOT_TOKEN)
    ctx.add(UserConfig(user_id=user.id, schema_version=1,
                       config={"keywords": ["engineer"], "max_notifications_per_day": 1}))
    for _ in range(2):
        _scored(ctx, user)
    ctx.flush()

    sent: list[str] = []
    assert deliver(ctx, user.id, store=store, send=lambda t, c, m, k=None: sent.append(m)) == 1


def test_stale_high_scorers_do_not_crowd_fresh_jobs_out_of_the_pool(ctx):
    """**Fifty candidates, fifty dropped, zero sent — with twenty-five fresh matches waiting.**

    The pool is ordered by score, so a backlog of stale high-scoring rows filled every candidate
    slot and was then dropped for age. Filtering age only *after* selection means the fresh jobs
    one page further down are never looked at.
    """
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob, UserJobState
    from services.notifier.deliver import pending_feed
    from services.notifier.eligibility import Eligibility

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

    rules = Eligibility.from_config({"sections": [{"posted_within_hours": 6}]})
    items, _dropped = pending_feed(ctx, user.id, cutoff=60.0, limit=5, rules=rules)

    assert [item.title for item in items] == ["Fresh Backend Engineer"]
