"""Embed corpus rows in bulk, once, for everyone."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from libs.contracts.embedder import DEFAULT_EMBED_TIMEOUT, EmbedderClient, EmbedderVersion, pack_vector
from libs.corpus.embedtext import build_text, embedding_version
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# Roughly how long one text takes to embed, measured on the live box: ~840 ms above a batch of 32.
EMBED_SECONDS_PER_TEXT = 0.9

# How many rows to embed per pass.
EMBED_BATCH_SIZE = int(DEFAULT_EMBED_TIMEOUT * 0.45 / EMBED_SECONDS_PER_TEXT)


@dataclass(frozen=True)
class EmbedResult:
    embedded: int
    skipped_stale: int
    remaining: int

    @property
    def did_work(self) -> bool:
        return self.embedded > 0


def embed_pending(
    corpus: CorpusRepository,
    client: EmbedderClient,
    *,
    expected: EmbedderVersion | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
) -> EmbedResult:
    """Embed one batch of corpus rows that have text but no vector."""
    version = client.version()
    if expected is not None:
        client.assert_compatible(expected)

    pending = corpus.awaiting_embedding(limit=batch_size)
    if not pending:
        return EmbedResult(0, 0, 0)

    # Digests are captured before the call, so the write can tell whether the text moved underneath.
    digests = {job.id: job.text_sha256 for job in pending}
    # Title first, then description.
    texts = [build_text(job.title, job.description) for job in pending]

    vectors = client.embed_bulk(texts)

    embedded = skipped = 0
    for job, values in zip(pending, vectors, strict=True):
        if len(values) != version.dim:
            raise ValueError(
                f"embedder returned a {len(values)}-dim vector while reporting dim={version.dim}")
        accepted = corpus.set_embedding(
            job.id, vector=pack_vector(values), text_sha256=digests[job.id],
            model=version.model, dim=version.dim, version=embedding_version(version))
        if accepted:
            embedded += 1
        else:
            # The description changed while this batch was in flight.
            skipped += 1

    remaining = len(corpus.awaiting_embedding(limit=1))
    if skipped:
        logger.info("embedded %d rows, %d superseded mid-flight", embedded, skipped)
    return EmbedResult(embedded=embedded, skipped_stale=skipped, remaining=remaining)


def embed_all_pending(
    corpus: CorpusRepository,
    client: EmbedderClient,
    *,
    expected: EmbedderVersion | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
    max_batches: int = 50,
) -> EmbedResult:
    """Drain the backlog, bounded."""
    total_embedded = total_skipped = 0
    for _ in range(max_batches):
        result = embed_pending(corpus, client, expected=expected, batch_size=batch_size)
        total_embedded += result.embedded
        total_skipped += result.skipped_stale
        if not result.did_work:
            break
    remaining = len(corpus.awaiting_embedding(limit=1))
    return EmbedResult(total_embedded, total_skipped, remaining)
