"""The multi-tenant scorer: hot-swap, isolation, and the four ways it must fail closed.

Two things replace container-per-user here, and both are silent when broken: `users.model_version`
driving a reload instead of a restart (`PLATFORM_ARCHITECTURE.md` §7), and a version check that
refuses an artifact whose semantics no longer match. A scorer that serves a stale model does not
error — it returns numbers, and the numbers are wrong.

The estimator is a stub. What is worth testing is which model gets served to whom and when it is
refused, not whether scikit-learn multiplies correctly.
"""
from __future__ import annotations

import os
import struct
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("numpy")
pytest.importorskip("pydantic")

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.db.models import User, UserModel
from libs.scoring.features import FEATURE_SCHEMA_VERSION, N_FEATURES
from services.scorer.registry import ModelRegistry, ModelUnavailable
from services.scorer.serve import EmbeddingMismatch, score_rows

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

DIM = 8
EMBEDDER_VERSION = "BAAI/bge-base-en-v1.5|dim=768|norm=1"
PROFILE = {
    "identity": {"name": "Alice", "work_eligibility": ["EU citizen"]},
    "skills": {"languages": ["Go", "Python"]},
    "not_list": ["Java"],
    "confirmed": True,
}


class StubEstimator:
    """Returns a fixed score, and records the input width it was handed.

    `predict_proba` returns a real ndarray, not a list of lists: the serve path slices it as
    `[:, 1]`, exactly as it would a scikit-learn estimator's output, and a list would pass a
    unit test while failing on the shape the code actually uses.
    """

    def __init__(self, value: float = 77.0):
        self.value = value
        self.seen_width: int | None = None

    def predict(self, matrix):
        self.seen_width = matrix.shape[1]
        return np.array([self.value] * matrix.shape[0])

    def predict_proba(self, matrix):
        self.seen_width = matrix.shape[1]
        return np.array([[1 - self.value / 100, self.value / 100]] * matrix.shape[0])


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


def _user(s, *, model_version: int = 1, generation: int = 1, profile=PROFILE) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                model_version=model_version, profile_generation=generation, profile=profile)
    s.add(user)
    s.flush()
    return user


def _artifact(s, user, *, version: int = 1, generation: int = 1, path: str = "artifact.joblib",
              feature_schema: int = FEATURE_SCHEMA_VERSION, dim: int = DIM) -> UserModel:
    row = UserModel(user_id=user.id, version=version, path=path,
                    feature_schema_version=feature_schema, profile_generation=generation,
                    embedder_model="BAAI/bge-base-en-v1.5", embedder_dim=dim,
                    embedder_version=EMBEDDER_VERSION)
    s.add(row)
    s.flush()
    return row


def _registry(s, blobs: dict[str, dict], **kw) -> ModelRegistry:
    return ModelRegistry(s, load_artifact=lambda path: blobs[path], **kw)


def _blob(estimator=None, **over) -> dict:
    return {"model": estimator or StubEstimator(), "task": "regress",
            "decision_cutoff": 60, "keep_cutoff": 45} | over


def _row(job_id: int, *, values=None, title="Backend Engineer", description="Go and Postgres. " * 20):
    vector = values or [0.1] * DIM
    return {"id": job_id, "title": title, "description": description,
            "embedding": struct.pack(f"<{len(vector)}f", *vector), "embedding_dim": len(vector),
            "embedding_version": EMBEDDER_VERSION}


# ── hot-swap: a retrain must be visible without a restart ─────────────────────────────────────

def test_a_bumped_model_version_swaps_the_served_model(ctx):
    """The single-tenant instance reloads by recreating the container. This is the whole reason
    `users.model_version` exists."""
    user = _user(ctx)
    _artifact(ctx, user, version=1, path="v1")
    first, second = StubEstimator(50.0), StubEstimator(90.0)
    registry = _registry(ctx, {"v1": _blob(first), "v2": _blob(second)})

    assert registry.get(user.id).estimator is first

    _artifact(ctx, user, version=2, path="v2")
    user.model_version = 2
    ctx.flush()

    assert registry.get(user.id).estimator is second


def test_an_unchanged_version_is_served_from_cache(ctx):
    """The version check runs on every score, so it has to be cheaper than the reload it prevents.
    Loading again each call would make the check pointless."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    loads = []
    registry = ModelRegistry(ctx, load_artifact=lambda p: loads.append(p) or _blob())

    registry.get(user.id)
    registry.get(user.id)
    registry.get(user.id)
    assert loads == ["v1"]


# ── isolation ─────────────────────────────────────────────────────────────────────────────────

def test_each_user_is_served_their_own_model(ctx):
    """The failure this replaces container isolation for. Serving one user's model to another is
    not a crash — it is a feed of someone else's taste."""
    alice, bob = _user(ctx), _user(ctx)
    _artifact(ctx, alice, path="alice")
    _artifact(ctx, bob, path="bob")
    alice_model, bob_model = StubEstimator(10.0), StubEstimator(90.0)
    registry = _registry(ctx, {"alice": _blob(alice_model), "bob": _blob(bob_model)})

    assert registry.get(alice.id).estimator is alice_model
    assert registry.get(bob.id).estimator is bob_model


def test_the_cache_evicts_rather_than_growing(ctx):
    """A few MB each is fine resident; unbounded is a slow leak nobody attributes to the scorer."""
    registry = _registry(ctx, {}, capacity=2)
    registry._load = lambda path: _blob()

    for _ in range(4):
        user = _user(ctx)
        _artifact(ctx, user)
        registry.get(user.id)
    assert registry.resident == 2


# ── the four fail-closed cases ────────────────────────────────────────────────────────────────

def test_an_untrained_user_has_no_model(ctx):
    """Normal for a new account — cold start covers it. Flagged retrainable so the caller can tell
    it apart from a corruption."""
    user = _user(ctx, model_version=0)
    with pytest.raises(ModelUnavailable) as exc:
        _registry(ctx, {}).get(user.id)
    assert exc.value.retrainable is True


def test_a_missing_artifact_row_refuses_rather_than_serving_the_previous_version(ctx):
    """An interrupted trainer, or a pruned artifact. Serving the previous version would answer with
    a model the user's own dashboard says is superseded."""
    user = _user(ctx, model_version=3)
    _artifact(ctx, user, version=2, path="v2")
    with pytest.raises(ModelUnavailable, match="no artifact row"):
        _registry(ctx, {"v2": _blob()}).get(user.id)


def test_a_profile_generation_mismatch_refuses(ctx):
    """`core_stack_count` and `offstack_count` *mean* something different after a profile edit. The
    estimator still predicts; the weights were fit against other semantics
    (`PROFILE_CAPTURE_PLAN.md` §6.4)."""
    user = _user(ctx, generation=2)
    _artifact(ctx, user, generation=1, path="v1")
    with pytest.raises(ModelUnavailable, match="generation"):
        _registry(ctx, {"v1": _blob()}).get(user.id)


def test_a_profile_edit_invalidates_an_already_cached_model(ctx):
    """The dangerous ordering: the model was current when it was cached, and the profile moved
    afterwards. Nothing about `model_version` changes, so only the generation check catches it."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    registry = _registry(ctx, {"v1": _blob()})
    registry.get(user.id)

    user.profile_generation = 2
    ctx.flush()
    with pytest.raises(ModelUnavailable, match="generation"):
        registry.get(user.id)


def test_a_feature_schema_mismatch_refuses(ctx):
    """The column count still lines up, so nothing crashes — the weights are just attached to
    features that no longer mean what they meant."""
    user = _user(ctx)
    _artifact(ctx, user, feature_schema=FEATURE_SCHEMA_VERSION + 1, path="v1")
    with pytest.raises(ModelUnavailable, match="feature schema"):
        _registry(ctx, {"v1": _blob()}).get(user.id)


def test_a_user_without_a_confirmed_profile_cannot_be_scored(ctx):
    """The feature spec is derived from the profile, so there is nothing to score against."""
    user = _user(ctx, profile=None)
    _artifact(ctx, user, path="v1")
    with pytest.raises(ModelUnavailable, match="profile"):
        _registry(ctx, {"v1": _blob()}).get(user.id)


# ── scoring ───────────────────────────────────────────────────────────────────────────────────

def test_the_corpus_vector_is_used_rather_than_re_embedded(ctx):
    """The economic argument for the shared corpus: N users over one posting is N predictions, not
    N embeddings. It only holds if this path never embeds."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    estimator = StubEstimator()
    model = _registry(ctx, {"v1": _blob(estimator)}).get(user.id)

    scored = score_rows(model, [_row(1), _row(2)])
    assert len(scored) == 2
    assert estimator.seen_width == DIM + N_FEATURES


def test_rows_that_are_not_embedded_yet_are_skipped_not_failed(ctx):
    """A normal state between harvest and the embed pass, not an error."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    model = _registry(ctx, {"v1": _blob()}).get(user.id)

    rows = [_row(1), {"id": 2, "title": "x", "description": "y", "embedding": None}]
    assert [s.job_id for s in score_rows(model, rows)] == [1]


def test_a_vector_from_a_different_embedder_is_refused(ctx):
    """Fail closed, same as the embedder's own `(model, dim, version)` discipline. A dimension
    mismatch would otherwise be a numpy error deep in a batch, or worse, silently broadcast."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    model = _registry(ctx, {"v1": _blob()}).get(user.id)

    with pytest.raises(EmbeddingMismatch, match="dim"):
        score_rows(model, [_row(1, values=[0.1] * (DIM + 1))])

    stale = _row(1) | {"embedding_version": "BAAI/bge-small-en-v1.5|dim=384|norm=1"}
    with pytest.raises(EmbeddingMismatch, match="trained against"):
        score_rows(model, [stale])


@pytest.mark.parametrize("value,expected", [(95.0, "high"), (60.0, "high"),
                                            (50.0, "needs-review"), (10.0, "skip")])
def test_the_decision_bands(ctx, value, expected):
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    model = _registry(ctx, {"v1": _blob(StubEstimator(value))}).get(user.id)
    assert score_rows(model, [_row(1)])[0].decision == expected


def test_a_classifier_artifact_is_scaled_to_the_same_scale_as_a_regressor(ctx):
    """So one cutoff means the same thing regardless of which head the trainer picked."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    model = _registry(ctx, {"v1": _blob(StubEstimator(90.0), task="classify")}).get(user.id)
    assert score_rows(model, [_row(1)])[0].score == 90


def test_a_thin_description_is_flagged_not_dropped(ctx):
    """The score means much less than it looks like it does; the caller may want to fetch the full
    text first. Dropping it would hide a job that is probably fine."""
    user = _user(ctx)
    _artifact(ctx, user, path="v1")
    model = _registry(ctx, {"v1": _blob()}).get(user.id)

    (scored,) = score_rows(model, [_row(1, description="Apply on our site.")])
    assert scored.thin is True
    assert scored.score > 0


def test_two_users_score_the_same_posting_through_their_own_feature_spec(ctx):
    """The per-user extractor reaching the served vector. If the registry built one shared spec,
    every user would be scored against the same stack — the §6.3 bug, one layer up."""
    gopher = _user(ctx)
    javaist = _user(ctx, profile=PROFILE | {
        "skills": {"languages": ["Java"]}, "not_list": ["Go"]})
    _artifact(ctx, gopher, path="a")
    _artifact(ctx, javaist, path="b")
    registry = _registry(ctx, {"a": _blob(), "b": _blob()})

    assert registry.get(gopher.id).features.core != registry.get(javaist.id).features.core


# ── artifact location ─────────────────────────────────────────────────────────────────────────

def test_an_artifact_is_found_where_it_lives_now_not_where_it_was_written(monkeypatch, tmp_path):
    """`models.path` records where the trainer wrote the file. The reader is usually somewhere
    else — a container with the artifacts mounted at a different point, or a different host after a
    restore — so the filename is the identity and `MODEL_DIR` is the location.

    This is a real failure, not a hypothetical: the scorer container crash-looped on
    `FileNotFoundError` for a path that was correct on the host that trained it.
    """
    from services.scorer.registry import resolve_artifact

    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    artifact = tmp_path / "abc.v1.joblib"
    artifact.write_bytes(b"x")

    assert resolve_artifact("/somewhere/else/entirely/abc.v1.joblib") == str(artifact)


def test_a_path_that_does_exist_is_used_unchanged(monkeypatch, tmp_path):
    """So a hand-placed artifact, or a host-side script run outside the container, keeps working."""
    from services.scorer.registry import resolve_artifact

    monkeypatch.setenv("MODEL_DIR", "/var/lib/jobsearch/models")
    here = tmp_path / "present.joblib"
    here.write_bytes(b"x")

    assert resolve_artifact(str(here)) == str(here)
