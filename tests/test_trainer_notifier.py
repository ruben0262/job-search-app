"""The trainer: the invariants that are silent when broken. Delivery is `test_alerts.py`."""
from __future__ import annotations

import hashlib
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

from libs.db.models import CorpusJob, User, UserLabel, UserModel
from services.trainer.train import NotEnoughData, build_training_set, should_retrain, train_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

DIM = 8
VERSION = "BAAI/bge-base-en-v1.5|dim=768|norm=1"
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


def test_retraining_deletes_the_artifacts_it_supersedes_from_disk(ctx, tmp_path):
    """**`prune_model_artifacts` had no caller at all** (ROADMAP 6bd), and it does not unlink.

    Two halves of one defect. Nothing invoked it, so every version ever trained stayed on the box —
    `v1`–`v4` for one account, 34 MB in a fortnight, growing per retrain forever. And it deletes the
    row while only *returning* the path, so a caller that ignores the return value frees no disk at
    all. This asserts the files are gone, not that a function was called.
    """
    user = _user(ctx)
    _labelled(ctx, user, 30)

    for _ in range(3):
        train_user(ctx, user.id, artifact_dir=tmp_path, fit=_fit)
        ctx.flush()
        _labelled(ctx, user, 10)

    on_disk = sorted(p.name for p in tmp_path.glob("*.joblib"))
    assert len(on_disk) == 2, (
        f"three retrains left {len(on_disk)} artifact(s) on disk: {on_disk} — the disk grows per "
        "retrain forever")
    assert on_disk == [f"{user.id}.v2.joblib", f"{user.id}.v3.joblib"], (
        "the current artifact and one previous, so a rollback is still possible")


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
