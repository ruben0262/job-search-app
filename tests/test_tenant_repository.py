"""Per-user repository, and the isolation guarantee it exists to enforce.

The last three tests are **T1** proper (`TEST_STRATEGY.md`): two users, overlapping jobs, every read
path returns only the caller's rows. Container-level isolation was traded away deliberately
(`PLATFORM_ARCHITECTURE.md` §2) for a shared corpus, and this is the other side of that trade — a
release blocker, not a nice-to-have.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.tenant.repository import APPLIED_SCORE, FEEDBACK_WEIGHT, SKIPPED_SCORE, TenantRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield TenantRepository(s), CorpusRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="Europe/Budapest")
    s.add(u)
    s.flush()
    return u


def _job(corpus, s, description: str = "Go, gRPC, Postgres. " * 20) -> int:
    job_id = corpus.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
                        source="greenhouse", title="Backend Engineer",
                        company=f"Acme{uuid.uuid4().hex[:8]}")
    corpus.set_description(job_id, description)
    s.flush()
    return job_id


# ── applied log ───────────────────────────────────────────────────────────────────────────────

def test_applied_at_is_set_once_and_never_moved(ctx):
    """A later cover build supersedes the row but must not rewrite the date they applied."""
    t, c, s = ctx
    u, job_id = _user(s), _job(c, s)

    assert t.record_applied(u.id, job_id) is True
    s.flush()
    first = t.applied(u.id)[0].applied_at

    assert t.record_applied(u.id, job_id, cover_built=True) is False
    s.flush()

    row = t.applied(u.id)[0]
    assert row.applied_at == first
    assert row.cover_built is True


def test_applying_pins_the_job_against_retention(ctx):
    """The one place a per-user action reaches into shared state, and the reason `pin()` exists.

    Without it, retention would remove the JD from under the applied record after 90 days.
    """
    t, c, s = ctx
    u, job_id = _user(s), _job(c, s)
    t.record_applied(u.id, job_id)
    s.flush()
    assert s.get(CorpusJob, job_id).pinned is True


def test_the_applied_log_stores_no_job_text(ctx):
    """~2,937 bytes per row becomes ~200. This is the whole storage argument for the shared design."""
    t, c, s = ctx
    u, job_id = _user(s), _job(c, s)
    t.record_applied(u.id, job_id)
    s.flush()

    state = s.get(UserJobState, (u.id, job_id))
    assert not hasattr(state, "description")
    assert t.applied(u.id)[0].job_id == job_id      # text is reached through the corpus, by id


# ── labels and feedback ───────────────────────────────────────────────────────────────────────

def test_relabelling_supersedes_rather_than_accumulating(ctx):
    """Otherwise the trainer sees one job twice with two contradictory verdicts."""
    t, c, s = ctx
    u, job_id = _user(s), _job(c, s)

    t.upsert_label(u.id, job_id, label=30, source="llm")
    s.flush()
    t.upsert_label(u.id, job_id, label=80, source="llm")
    s.flush()

    labels = list(s.scalars(select(UserLabel).where(UserLabel.user_id == u.id)))
    assert len(labels) == 1
    assert labels[0].label == 80


def test_feedback_is_a_label_with_higher_weight(ctx):
    """A user's explicit decision outweighs the LLM's guess at their taste."""
    t, c, s = ctx
    u = _user(s)
    applied_job, skipped_job = _job(c, s), _job(c, s)

    t.record_feedback(u.id, applied_job, applied=True)
    t.record_feedback(u.id, skipped_job, applied=False)
    s.flush()

    rows = {r.job_id: r for r in s.scalars(
        select(UserLabel).where(UserLabel.user_id == u.id))}
    assert rows[applied_job].label == APPLIED_SCORE
    assert rows[skipped_job].label == SKIPPED_SCORE
    assert rows[applied_job].source == "feedback"
    assert rows[applied_job].weight == FEEDBACK_WEIGHT
    assert rows[skipped_job].weight == FEEDBACK_WEIGHT


def test_training_set_excludes_other_generations_and_thin_descriptions(ctx):
    """Two filters from the reference's load_clean(), both silent if dropped.

    Labels from an older rubric answer a different question. A description under ~100 chars means
    the fetch failed, so the label describes a page nobody actually read.
    """
    t, c, s = ctx
    u = _user(s)
    current = _job(c, s)
    old_generation = _job(c, s)
    thin = _job(c, s, description="too short")

    t.upsert_label(u.id, current, label=80, profile_generation=2)
    t.upsert_label(u.id, old_generation, label=80, profile_generation=1)
    t.upsert_label(u.id, thin, label=80, profile_generation=2)
    s.flush()

    got = [row.job_id for row in t.training_labels(u.id, profile_generation=2)]
    assert got == [current]


# ── config, secrets, queue ────────────────────────────────────────────────────────────────────

def test_config_round_trips_and_records_its_schema_version(ctx):
    """`schema_version` is what makes a future shape change migratable rather than guesswork."""
    from libs.contracts.config import CONFIG_SCHEMA_VERSION
    from libs.db.models import UserConfig

    t, _, s = ctx
    u = _user(s)
    t.save_config(u.id, {"keywords": ["python"], "locations": ["london"]})
    s.flush()

    assert t.get_config(u.id) == {"keywords": ["python"], "locations": ["london"]}
    assert s.get(UserConfig, u.id).schema_version == CONFIG_SCHEMA_VERSION

    t.save_config(u.id, {"keywords": ["golang"]})
    s.flush()
    assert t.get_config(u.id) == {"keywords": ["golang"]}, "save must replace, not accumulate"


def test_a_user_without_a_config_reads_as_none_not_as_empty(ctx):
    """`None` and `{}` mean different things: not onboarded yet, versus onboarded with nothing."""
    t, _, s = ctx
    assert t.get_config(_user(s).id) is None


def test_only_active_users_configs_reach_the_harvest_fold(ctx):
    """A suspended or unverified account must not consume harvest budget.

    `TENANT_SAFETY.md` §5: nothing before email verification touches a paid resource, and a signup
    flood must cost nothing.
    """
    t, _, s = ctx
    active = _user(s)
    active.active, active.status = True, "active"
    inactive = _user(s)
    inactive.active, inactive.status = False, "pending_verification"
    suspended = _user(s)
    suspended.active, suspended.status = True, "suspended"

    for u in (active, inactive, suspended):
        t.save_config(u.id, {"keywords": ["python"]})
    s.flush()

    configs = t.active_configs()
    assert active.id in configs
    assert inactive.id not in configs
    assert suspended.id not in configs


def test_secret_metadata_never_exposes_the_value(ctx):
    """Secrets are write-only through the API (`TENANT_SAFETY.md` §2).

    The response carries enough to recognise which key was saved and nothing that could reconstruct
    it. There is deliberately no repository method returning ciphertext.
    """
    from libs.db.models import UserSecret

    t, _, s = ctx
    u = _user(s)
    s.add(UserSecret(user_id=u.id, kind="claude_api", ciphertext=b"SECRET-CIPHERTEXT",
                     nonce=b"nonce", last_4="ab12"))
    s.flush()

    (meta,) = t.secret_metadata(u.id)
    assert meta.kind == "claude_api"
    assert meta.last_4 == "ab12"

    rendered = repr(meta) + str(meta)
    assert "SECRET-CIPHERTEXT" not in rendered
    assert not hasattr(meta, "ciphertext")
    assert not hasattr(meta, "nonce")


def test_pending_jobs_lists_only_live_queue_rows(ctx):
    """Done and dead jobs are history, not pending work."""
    from libs.db.models import QueueJob

    t, _, s = ctx
    u = _user(s)
    s.add_all([
        QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}),
        QueueJob(lane="notify", user_id=u.id, dedup_key="due", payload={}, state="done"),
        QueueJob(lane="docgen", user_id=u.id, dedup_key="cv", payload={}, state="dead"),
    ])
    s.flush()

    assert [j.lane for j in t.pending_jobs(u.id)] == ["train"]
    assert t.pending_jobs(u.id, lane="notify") == []


# ── T1: tenant isolation. Release blocker. ────────────────────────────────────────────────────

def test_one_users_applied_log_never_contains_anothers(ctx):
    t, c, s = ctx
    alice, bob = _user(s), _user(s)
    shared_job, bob_only = _job(c, s), _job(c, s)

    t.record_applied(alice.id, shared_job)
    t.record_applied(bob.id, shared_job)
    t.record_applied(bob.id, bob_only)
    s.flush()

    assert [r.job_id for r in t.applied(alice.id)] == [shared_job]
    assert sorted(r.job_id for r in t.applied(bob.id)) == sorted([shared_job, bob_only])
    assert t.applied_count(alice.id) == 1
    assert t.applied_count(bob.id) == 2


def test_one_users_labels_never_reach_anothers_training_set(ctx):
    """Two users can label the same shared job differently. Neither may see the other's verdict."""
    t, c, s = ctx
    alice, bob = _user(s), _user(s)
    shared_job = _job(c, s)

    t.upsert_label(alice.id, shared_job, label=90, profile_generation=0)
    t.upsert_label(bob.id, shared_job, label=10, profile_generation=0)
    s.flush()

    alice_labels = t.training_labels(alice.id, profile_generation=0)
    bob_labels = t.training_labels(bob.id, profile_generation=0)
    assert [r.label for r in alice_labels] == [90]
    assert [r.label for r in bob_labels] == [10]


def test_scores_are_per_user_on_the_same_shared_job(ctx):
    """The same posting is a great match for one person and a bad one for another.

    That is the product. It is also the thing a leaked filter would destroy most visibly.
    """
    t, c, s = ctx
    alice, bob = _user(s), _user(s)
    shared_job = _job(c, s)

    t.record_score(alice.id, shared_job, score=0.95, model_version=3)
    t.record_score(bob.id, shared_job, score=0.10, model_version=7)
    s.flush()

    assert s.get(UserJobState, (alice.id, shared_job)).score == pytest.approx(0.95)
    assert s.get(UserJobState, (bob.id, shared_job)).score == pytest.approx(0.10)
    assert s.get(UserJobState, (alice.id, shared_job)).scored_model_version == 3


def test_one_users_config_is_never_readable_by_another(ctx):
    """T1: config read path."""
    t, _, s = ctx
    alice, bob = _user(s), _user(s)
    t.save_config(alice.id, {"keywords": ["alice-secret-term"]})
    t.save_config(bob.id, {"keywords": ["bob-term"]})
    s.flush()

    assert t.get_config(alice.id) == {"keywords": ["alice-secret-term"]}
    assert t.get_config(bob.id) == {"keywords": ["bob-term"]}


def test_one_users_secrets_are_never_listed_for_another(ctx):
    """T1: secrets read path. These are strangers' live credentials."""
    from libs.db.models import UserSecret

    t, _, s = ctx
    alice, bob = _user(s), _user(s)
    s.add_all([
        UserSecret(user_id=alice.id, kind="claude_api", ciphertext=b"a", nonce=b"n", last_4="aaaa"),
        UserSecret(user_id=bob.id, kind="claude_api", ciphertext=b"b", nonce=b"n", last_4="bbbb"),
        UserSecret(user_id=bob.id, kind="proxy", ciphertext=b"b2", nonce=b"n", last_4="cccc"),
    ])
    s.flush()

    assert [m.last_4 for m in t.secret_metadata(alice.id)] == ["aaaa"]
    assert sorted(m.last_4 for m in t.secret_metadata(bob.id)) == ["bbbb", "cccc"]


def test_one_users_queue_is_never_visible_to_another(ctx):
    """T1: queue read path. Queue rows carry user_id and payloads can name their work."""
    from libs.db.models import QueueJob

    t, _, s = ctx
    alice, bob = _user(s), _user(s)
    s.add_all([
        QueueJob(lane="train", user_id=alice.id, dedup_key="retrain", payload={"who": "alice"}),
        QueueJob(lane="train", user_id=bob.id, dedup_key="retrain", payload={"who": "bob"}),
        QueueJob(lane="docgen", user_id=bob.id, dedup_key="cv-1", payload={"who": "bob"}),
    ])
    s.flush()

    assert [j.payload["who"] for j in t.pending_jobs(alice.id)] == ["alice"]
    assert {j.payload["who"] for j in t.pending_jobs(bob.id)} == {"bob"}
    assert len(t.pending_jobs(bob.id)) == 2
