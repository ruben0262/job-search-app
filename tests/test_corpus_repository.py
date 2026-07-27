"""The corpus repository's behaviour — the storage half of the `store.py` port.

T2 (`test_dedup_parity.py`) pins the dedup *key functions* against the reference. This file pins
what the repository does with them: the order the layers are applied, that a null key never matches,
that re-discovering a job does not make it look new, and that a vector can never outlive the text it
was computed from.

Needs a live Postgres; skips without `DATABASE_URL`, like the schema tests. Every test runs inside a
transaction that is rolled back.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs.corpus.repository import CorpusRepository, text_digest
from libs.db.models import CorpusJob

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def repo():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield CorpusRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _u(path: str = "") -> str:
    return f"https://boards.greenhouse.io/acme/jobs/{path or uuid.uuid4().hex}"


# ── ingest ────────────────────────────────────────────────────────────────────────────────────

def test_adding_the_same_url_twice_inserts_once(repo):
    r, s = repo
    url = _u()
    first = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    second = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()

    assert first is not None
    assert second is None, "second insert must be ignored, not raise and not duplicate"
    assert s.scalar(select(CorpusJob.id).where(CorpusJob.url == url).limit(1)) == first


def test_rediscovering_a_job_never_makes_it_look_new(repo):
    """`first_seen_at` drives recency ordering and the retention sweep.

    A source that re-lists an old posting every cycle would otherwise keep it permanently fresh and
    permanently un-trimmable — the exact reason the reference uses INSERT OR IGNORE rather than an
    upsert.
    """
    r, s = repo
    url = _u()
    job_id = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    original = s.get(CorpusJob, job_id).first_seen_at

    r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    r.touch_last_seen([job_id])
    s.flush()
    s.refresh(s.get(CorpusJob, job_id))

    job = s.get(CorpusJob, job_id)
    assert job.first_seen_at == original, "first_seen_at must never be overwritten"
    assert job.last_seen_at >= original


# ── dedup ─────────────────────────────────────────────────────────────────────────────────────

def test_dedup_matches_on_exact_url(repo):
    r, s = repo
    url = _u()
    r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()

    hit = r.find_duplicate(url, "Backend Engineer", "Acme")
    assert hit is not None
    assert hit.matched_on == "url"


def test_dedup_matches_across_tracking_noise_via_canonical(repo):
    """The same posting linked with `?gh_src=` is not a second job."""
    r, s = repo
    token = uuid.uuid4().hex
    r.add(url=_u(token), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()

    hit = r.find_duplicate(_u(token) + "?gh_src=abcd", "Backend Engineer", "Acme")
    assert hit is not None
    assert hit.matched_on == "canonical_url"


def test_dedup_matches_the_same_role_on_an_unrelated_board(repo):
    """LinkedIn and Greenhouse URLs share nothing, so only the content key can see this."""
    r, s = repo
    company = f"Acme{uuid.uuid4().hex[:8]}"
    r.add(url=_u(), source="greenhouse", title="Backend Engineer", company=company,
          location="London")
    s.flush()

    hit = r.find_duplicate("https://www.linkedin.com/jobs/view/4012345678/",
                           "Backend Engineer", company, "London")
    assert hit is not None
    assert hit.matched_on == "content_key"


def test_the_same_role_in_another_city_is_not_a_duplicate(repo):
    """Location is part of the content key on purpose — two cities are two jobs."""
    r, s = repo
    company = f"Acme{uuid.uuid4().hex[:8]}"
    r.add(url=_u(), source="greenhouse", title="Backend Engineer", company=company,
          location="London")
    s.flush()

    assert r.find_duplicate(_u(), "Backend Engineer", company, "Berlin") is None


def test_an_unusable_key_never_matches_another_unusable_key(repo):
    """Fail open. `None` means "no opinion", never a value to join on.

    If null keys matched each other, every posting with an unparseable company would collapse into
    one row and the rest would be silently discarded — the worst possible dedup bug, because the
    feed simply gets quieter and nothing errors.
    """
    r, s = repo
    r.add(url="https://example.test/" + uuid.uuid4().hex, source="board", title="Engineer",
          company="")            # no company -> content_key is None
    s.flush()

    # A different job, also with no usable content key, must not match the first.
    assert r.find_duplicate("https://example.test/" + uuid.uuid4().hex, "Engineer", "") is None
    assert r.seen_content_key(None) is False
    assert r.seen_canonical(None) is False


def test_a_genuinely_new_job_is_not_a_duplicate(repo):
    r, _ = repo
    assert r.find_duplicate(_u(), "Staff Platform Engineer", f"New{uuid.uuid4().hex[:8]}") is None


# ── the apply-URL layer: what canonicalisation actually exists for ────────────────────────────
# The same job arrives as linkedin.com/jobs/view/… from the LinkedIn scrape and as
# boards.greenhouse.io/… from the direct ATS poll. Nothing about those URLs is comparable, and the
# content key only helps when title AND company both normalise identically — which aggregators
# routinely break by decorating titles or abbreviating company names. Resolving LinkedIn's Apply
# button to its external target is what makes them comparable.

def test_a_linkedin_listing_resolving_to_a_known_ats_url_is_a_duplicate(repo):
    """LinkedIn arrives second; the Greenhouse row is already in the corpus."""
    r, s = repo
    token = uuid.uuid4().hex
    r.add(url=_u(token), source="greenhouse", title="Backend Engineer",
          company=f"Acme{uuid.uuid4().hex[:8]}")
    s.flush()

    hit = r.find_duplicate(
        "https://www.linkedin.com/jobs/view/4012345678/",
        title="Backend Engineer (Remote) - Growing Startup",   # decorated: content key will differ
        company="Acme Technologies International",             # abbreviated differently
        application_url=_u(token) + "?gh_src=linkedin")
    assert hit is not None
    assert hit.matched_on == "application_url"


def test_an_ats_row_arriving_after_the_linkedin_one_is_a_duplicate(repo):
    """The other direction: the ATS poll arrives second, beaten by a LinkedIn listing.

    Checking only one direction would leave half the collisions undetected, which is worse than
    checking neither — it looks like it works.
    """
    r, s = repo
    token = uuid.uuid4().hex
    ats_url = _u(token)

    linkedin_id = r.add(url="https://www.linkedin.com/jobs/view/" + uuid.uuid4().hex,
                        source="linkedin", title="Backend Engineer (Remote)",
                        company="Acme Technologies International")
    s.flush()
    r.set_application_url(linkedin_id, ats_url)
    s.flush()

    hit = r.find_duplicate(ats_url, title="Backend Engineer",
                           company=f"Acme{uuid.uuid4().hex[:8]}")
    assert hit is not None
    assert hit.matched_on == "application_url"
    assert hit.job_id == linkedin_id


def test_the_apply_url_layer_ignores_tracking_noise(repo):
    """It compares canonical forms, so `?gh_src=linkedin` is not a different apply target."""
    r, s = repo
    token = uuid.uuid4().hex
    job_id = r.add(url=_u(token), source="greenhouse", title="Backend Engineer",
                   company=f"Acme{uuid.uuid4().hex[:8]}")
    s.flush()

    hit = r.find_duplicate("https://www.linkedin.com/jobs/view/9999/",
                           title="Unrelated Title", company=f"Other{uuid.uuid4().hex[:8]}",
                           application_url=_u(token) + "?utm_source=x#apply")
    assert hit is not None and hit.job_id == job_id


def test_an_unresolvable_apply_url_does_not_match_anything(repo):
    """Fail open, same contract as the other layers."""
    r, s = repo
    r.add(url=_u(), source="greenhouse", title="Backend Engineer",
          company=f"Acme{uuid.uuid4().hex[:8]}")
    s.flush()

    assert r.find_duplicate(_u(), "Totally Different Role",
                            f"Other{uuid.uuid4().hex[:8]}", application_url="") is None
    assert r.find_duplicate(_u(), "Totally Different Role",
                            f"Other{uuid.uuid4().hex[:8]}",
                            application_url="not-a-url") is None


# ── description and embedding ────────────────────────────────────────────────────────────────

def test_changing_the_description_invalidates_the_vector(repo):
    """A vector must never outlive the text it was computed from.

    `refresh.py` re-fetches live postings and their text does change. A stale vector raises nothing
    — it just scores confidently against content that no longer exists.
    """
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()

    r.set_description(job_id, "Go, gRPC, Postgres.")
    s.flush()
    digest = s.get(CorpusJob, job_id).text_sha256
    assert r.set_embedding(job_id, vector=b"\x00" * 8, text_sha256=digest,
                           model="BAAI/bge-base-en-v1.5", dim=768, version="1.9") is True
    s.flush()
    assert s.get(CorpusJob, job_id).embedding is not None

    changed = r.set_description(job_id, "Rust, Kafka, ClickHouse.")
    s.flush()
    job = s.get(CorpusJob, job_id)
    assert changed is True
    assert job.embedding is None, "the old vector must be dropped, not left attached"
    assert job.text_sha256 == text_digest("Rust, Kafka, ClickHouse.")


def test_rewriting_identical_text_is_not_a_change(repo):
    """Re-fetching a posting that did not change must not throw away a good vector."""
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    r.set_description(job_id, "Go, gRPC, Postgres.")
    s.flush()
    digest = s.get(CorpusJob, job_id).text_sha256
    r.set_embedding(job_id, vector=b"\x00" * 8, text_sha256=digest,
                    model="m", dim=768, version="1")
    s.flush()

    assert r.set_description(job_id, "Go, gRPC, Postgres.") is False
    s.flush()
    assert s.get(CorpusJob, job_id).embedding is not None


def test_a_vector_computed_from_superseded_text_is_refused(repo):
    """The real race: bulk embedding takes minutes while refresh.py runs concurrently.

    The `(model, dim, version)` assert downstream cannot catch this — the vector space is correct,
    the *content* is wrong. So the write is refused here and the job simply gets picked up again.
    """
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    r.set_description(job_id, "Original text.")
    s.flush()
    stale_digest = s.get(CorpusJob, job_id).text_sha256

    r.set_description(job_id, "Text was updated while the embedder was working.")
    s.flush()

    accepted = r.set_embedding(job_id, vector=b"\x11" * 8, text_sha256=stale_digest,
                               model="m", dim=768, version="1")
    s.flush()
    assert accepted is False
    assert s.get(CorpusJob, job_id).embedding is None


def test_awaiting_embedding_finds_text_without_a_vector(repo):
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    assert job_id not in [j.id for j in r.awaiting_embedding()]   # no description yet

    r.set_description(job_id, "Go, gRPC, Postgres.")
    s.flush()
    assert job_id in [j.id for j in r.awaiting_embedding()]


# ── retention ────────────────────────────────────────────────────────────────────────────────

def test_trimming_drops_text_but_keeps_the_dedup_key_forever(repo):
    """Deleting the row instead would make the corpus re-acquire and re-embed the same job forever."""
    r, s = repo
    url = _u()
    job_id = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    r.set_description(job_id, "Go, gRPC, Postgres.")
    s.flush()
    s.get(CorpusJob, job_id).first_seen_at = datetime.now(UTC) - timedelta(days=200)
    s.flush()

    assert r.trim_cold() >= 1
    s.flush()
    s.expire_all()

    job = s.get(CorpusJob, job_id)
    assert job is not None, "the row must survive — it is the dedup key"
    assert job.description is None
    assert job.embedding is None
    assert job.trimmed_at is not None
    assert job.url == url and job.canonical_url is not None


def test_a_job_a_user_acted_on_is_never_trimmed(repo):
    """Losing the text under a label leaves the label meaningless; under an applied-record it
    destroys the thing the user most wants kept."""
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    s.flush()
    r.set_description(job_id, "Go, gRPC, Postgres.")
    r.pin(job_id)
    s.flush()
    s.get(CorpusJob, job_id).first_seen_at = datetime.now(UTC) - timedelta(days=200)
    s.flush()

    r.trim_cold()
    s.flush()
    s.expire_all()

    assert s.get(CorpusJob, job_id).description is not None
