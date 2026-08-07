"""T8 — a job's text is embedded once, for everyone, ever."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.contracts.embedder import (
    EmbedderClient,
    EmbedderError,
    EmbedderVersion,
    IncompatibleEmbedder,
    pack_vector,
    unpack_vector,
)
from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob, User
from libs.tenant.repository import TenantRepository
from services.harvester.embedding import embed_all_pending, embed_pending

DIM = 8
SERVED = {"ok": True, "model": "BAAI/bge-base-en-v1.5", "dim": DIM,
          "version": "BAAI/bge-base-en-v1.5|dim=8|norm=1"}
EXPECTED = EmbedderVersion(SERVED["model"], DIM, SERVED["version"])

db_required = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


class CountingEmbedder:
    """A stub gateway that counts how many texts it was ever asked to embed."""

    def __init__(self, health: dict | None = None, status: int = 200):
        self.health_payload = health if health is not None else SERVED
        self.status = status
        self.texts_embedded: list[str] = []
        self.bulk_calls = 0
        self.score_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=self.health_payload)
        if self.status >= 400:
            return httpx.Response(self.status, text="unavailable")
        texts = httpx.Response(200, json={}).json() or {}
        import json as _json
        texts = _json.loads(request.content)["texts"]
        if request.url.path == "/embed/bulk":
            self.bulk_calls += 1
        else:
            self.score_calls += 1
        self.texts_embedded.extend(texts)
        return httpx.Response(200, json={"vectors": [[float(len(t) % 7)] * DIM for t in texts]})

    def client(self) -> EmbedderClient:
        return EmbedderClient(
            client=httpx.Client(base_url="http://embedder", transport=httpx.MockTransport(self.handler)))


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield CorpusRepository(s), TenantRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC")
    s.add(u)
    s.flush()
    return u


def _job(corpus, s, text: str = "Go, gRPC, Postgres. " * 20) -> int:
    job_id = corpus.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
                        source="greenhouse", title="Backend Engineer",
                        company=f"Acme{uuid.uuid4().hex[:8]}")
    corpus.set_description(job_id, text)
    s.flush()
    return job_id


# ── serialisation ─────────────────────────────────────────────────────────────────────────────

def test_a_vector_round_trips_through_the_stored_format():
    values = [0.0, 1.5, -2.25, 3.125e-3]
    assert unpack_vector(pack_vector(values)) == pytest.approx(values)


def test_float32_storage_is_half_the_size_of_float64():
    """The reason for the choice: 768 floats per row, times the whole corpus."""
    assert len(pack_vector([1.0] * 768)) == 768 * 4


def test_a_truncated_blob_is_refused_rather_than_silently_reinterpreted():
    with pytest.raises(EmbedderError):
        unpack_vector(b"\x00\x00\x00")


# ── the compatibility assert ──────────────────────────────────────────────────────────────────

def test_a_mismatched_vector_space_is_refused():
    """A vector from the wrong model is not a worse vector, it is a meaningless one."""
    with CountingEmbedder().client() as client:
        client.assert_compatible(EXPECTED)
        with pytest.raises(IncompatibleEmbedder, match="retrain"):
            client.assert_compatible(EmbedderVersion("some/other-model", DIM, "x"))
        with pytest.raises(IncompatibleEmbedder):
            client.assert_compatible(EmbedderVersion(SERVED["model"], 384, "x"))


def test_an_unverified_embedder_is_refused():
    """`ok` stays false until the gateway has probed its live backend, and forever after a mismatch."""
    unverified = CountingEmbedder(health={"ok": False, "verify_detail": "backend unreachable"})
    with unverified.client() as client, pytest.raises(EmbedderError, match="not healthy"):
        client.version()


def test_a_short_response_is_refused_rather_than_misaligned():
    """Fewer vectors than texts means the mapping is unknown."""
    stub = CountingEmbedder()

    def short(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=SERVED)
        return httpx.Response(200, json={"vectors": [[0.0] * DIM]})

    with EmbedderClient(client=httpx.Client(
            base_url="http://e", transport=httpx.MockTransport(short))) as client, \
            pytest.raises(EmbedderError, match="refusing to guess"):
        client.embed_bulk(["a", "b", "c"])
    assert stub.bulk_calls == 0


def test_a_transport_failure_arrives_as_an_embedder_error():
    """A read timeout is an embedder failure and must be catchable as one."""
    def times_out(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=SERVED)
        raise httpx.ReadTimeout("timed out", request=request)

    with EmbedderClient(client=httpx.Client(
            base_url="http://e", transport=httpx.MockTransport(times_out))) as client, \
            pytest.raises(EmbedderError, match="ReadTimeout"):
        client.embed_bulk(["anything"])


def test_a_gateway_503_fails_closed():
    """503 is the gateway shedding load or refusing an unverified vector space."""
    stub = CountingEmbedder(status=503)
    with stub.client() as client, pytest.raises(EmbedderError, match="503"):
        client.embed_bulk(["anything"])


# ── T8: embedded once, for everyone ───────────────────────────────────────────────────────────

@db_required
def test_one_job_is_embedded_once_no_matter_how_many_users_want_it(ctx):
    """T8."""
    corpus, tenant, s = ctx
    alice, bob = _user(s), _user(s)
    job_id = _job(corpus, s)
    tenant.record_score(alice.id, job_id, score=0.9, model_version=1)
    tenant.record_score(bob.id, job_id, score=0.2, model_version=1)
    s.flush()

    stub = CountingEmbedder()
    with stub.client() as client:
        result = embed_pending(corpus, client, expected=EXPECTED)
    s.flush()

    assert result.embedded == 1
    assert stub.bulk_calls == 1
    assert len(stub.texts_embedded) == 1, "one text embedded, regardless of how many users want it"

    job = s.get(CorpusJob, job_id)
    assert job.embedding is not None
    assert len(unpack_vector(job.embedding)) == DIM


@db_required
def test_a_second_pass_re_embeds_nothing(ctx):
    """The vector is computed once *ever*, not once per cycle."""
    corpus, _, s = ctx
    _job(corpus, s)
    s.flush()

    stub = CountingEmbedder()
    with stub.client() as client:
        embed_pending(corpus, client, expected=EXPECTED)
        s.flush()
        again = embed_pending(corpus, client, expected=EXPECTED)
    assert again.embedded == 0
    assert len(stub.texts_embedded) == 1, "an already-embedded row must not be sent again"


@db_required
def test_the_bulk_lane_is_used_not_the_score_lane(ctx):
    """Separate worker pools, not a priority flag."""
    corpus, _, s = ctx
    _job(corpus, s)
    s.flush()

    stub = CountingEmbedder()
    with stub.client() as client:
        embed_pending(corpus, client, expected=EXPECTED)
    assert stub.bulk_calls == 1
    assert stub.score_calls == 0


@db_required
def test_provenance_is_recorded_with_every_vector(ctx):
    """A vector from the old space next to one from the new is indistinguishable by inspection."""
    corpus, _, s = ctx
    job_id = _job(corpus, s)
    s.flush()

    with CountingEmbedder().client() as client:
        embed_pending(corpus, client, expected=EXPECTED)
    s.flush()

    job = s.get(CorpusJob, job_id)
    assert job.embedding_model == SERVED["model"]
    assert job.embedding_dim == DIM
    # The text mode is folded in: provenance is more than which model produced the vector.
    from libs.corpus.embedtext import embedding_version

    assert job.embedding_version == embedding_version(SERVED["version"])
    assert job.text_sha256 is not None


@db_required
def test_text_changing_mid_flight_does_not_attach_a_stale_vector(ctx):
    """The real race: a cold-start batch takes minutes while refresh.py mutates descriptions."""
    corpus, _, s = ctx
    job_id = _job(corpus, s, text="Original text. " * 20)
    s.flush()

    stub = CountingEmbedder()
    original_handler = stub.handler

    def mutate_then_answer(request: httpx.Request) -> httpx.Response:
        response = original_handler(request)
        if request.url.path == "/embed/bulk":
            corpus.set_description(job_id, "Rewritten while the embedder was working. " * 20)
            s.flush()
        return response

    with EmbedderClient(client=httpx.Client(
            base_url="http://e", transport=httpx.MockTransport(mutate_then_answer))) as client:
        result = embed_pending(corpus, client, expected=EXPECTED)
    s.flush()

    assert result.embedded == 0
    assert result.skipped_stale == 1
    assert s.get(CorpusJob, job_id).embedding is None, "a vector was attached to superseded text"


@db_required
def test_rows_without_text_are_not_sent(ctx):
    """A row exists from discovery; its description arrives later."""
    corpus, _, s = ctx
    corpus.add(url=f"https://example.test/{uuid.uuid4().hex}", source="board",
               title="No description yet", company="Acme")
    s.flush()

    stub = CountingEmbedder()
    with stub.client() as client:
        result = embed_pending(corpus, client, expected=EXPECTED)
    assert result.embedded == 0
    assert stub.texts_embedded == []


@db_required
def test_draining_the_backlog_is_bounded(ctx):
    """A cold start must not occupy the 15-minute shared loop indefinitely."""
    corpus, _, s = ctx
    for _ in range(5):
        _job(corpus, s)
    s.flush()

    stub = CountingEmbedder()
    with stub.client() as client:
        result = embed_all_pending(corpus, client, expected=EXPECTED, batch_size=2, max_batches=2)
    s.flush()

    assert result.embedded == 4, "two batches of two, then it returns rather than draining"
    assert result.remaining >= 1


def test_embedding_stops_on_the_cycle_clock_rather_than_after_fifty_batches():
    """A step bounded by a row count can outlive the unit that runs it, and take the rest with it."""
    from time import monotonic

    from services.harvester.embedding import embed_all_pending

    class _Endless:
        """A corpus with more rows than any cycle could drain."""

        def awaiting_embedding(self, limit=500):
            return [object()] * limit

    calls = {"n": 0}

    def _never_finishes(corpus, client, *, expected=None, batch_size=60):
        calls["n"] += 1
        raise AssertionError("the deadline had already passed; no batch should have been started")

    import services.harvester.embedding as module

    original = module.embed_pending
    module.embed_pending = _never_finishes
    try:
        result = embed_all_pending(_Endless(), object(), deadline=monotonic() - 1)
    finally:
        module.embed_pending = original

    assert calls["n"] == 0
    assert result.embedded == 0
