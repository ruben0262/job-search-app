"""Embed corpus rows in bulk, once, for everyone.

**This is the pass that makes the shared-corpus economics work.** A vector is a deterministic
function of the text, so the same posting embedded for fifty users is fifty times the CPU for
identical output. Embedding it once onto the shared corpus row is the whole scaling argument
(`HARVESTER_PLAN.md` §7), and a regression here is invisible until the CPU bill — which is why T8
asserts the backend is called once rather than trusting that it is.

Nothing about this pass is per-user. It takes no `user_id`, and could not usefully be given one.

Three properties worth stating, because each is a silent failure if lost:

1. **Compatibility is asserted before anything is written.** A vector from the wrong model is not a
   worse vector, it is a meaningless one, and no downstream check would catch it — the dimensions
   match, the numbers look like numbers. The embedder's `/health` is backend-verified, so the assert
   checks a probed fact.
2. **The digest is re-checked at write time.** Bulk embedding a cold-start batch takes minutes while
   `refresh.py` mutates descriptions concurrently. `set_embedding` refuses a vector whose
   `text_sha256` no longer matches, and the row is simply picked up on the next pass.
3. **A failed batch does not poison the queue.** Rows stay unembedded and are retried; there is no
   dead-letter state for embedding because there is nothing to lose — the text is still in the
   corpus.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from libs.contracts.embedder import EmbedderClient, EmbedderVersion, pack_vector
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# How many rows to embed per pass. The gateway chunks internally to whatever TEI accepts, so this
# bounds the *transaction*, not the request: a smaller number commits progress more often, which
# matters because a cold-start batch of 2,000 takes ~5 minutes and a crash at minute four should not
# discard four minutes of work.
EMBED_BATCH_SIZE = 200


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
    """Embed one batch of corpus rows that have text but no vector.

    `expected` is the vector space the platform's trained artifacts assume. Passing it turns a
    silent mismatch into a refusal; omitting it accepts whatever the embedder currently serves,
    which is only correct before any classifier exists.
    """
    version = client.version()
    if expected is not None:
        client.assert_compatible(expected)

    pending = corpus.awaiting_embedding(limit=batch_size)
    if not pending:
        return EmbedResult(0, 0, 0)

    # Digests are captured BEFORE the call, so the write can tell whether the text moved underneath
    # a vector that took minutes to compute.
    digests = {job.id: job.text_sha256 for job in pending}
    texts = [job.description or "" for job in pending]

    vectors = client.embed_bulk(texts)

    embedded = skipped = 0
    for job, values in zip(pending, vectors, strict=True):
        if len(values) != version.dim:
            raise ValueError(
                f"embedder returned a {len(values)}-dim vector while reporting dim={version.dim}")
        accepted = corpus.set_embedding(
            job.id, vector=pack_vector(values), text_sha256=digests[job.id],
            model=version.model, dim=version.dim, version=version.version)
        if accepted:
            embedded += 1
        else:
            # The description changed while this batch was in flight. Not an error: the row stays
            # unembedded and is picked up next pass with its new text.
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
    """Drain the backlog, bounded.

    `max_batches` exists so a cold start cannot occupy the harvester indefinitely — the shared loop
    runs every 15 minutes and must return. Whatever is left is picked up next cycle, which is why
    there is no completion requirement here.
    """
    total_embedded = total_skipped = 0
    for _ in range(max_batches):
        result = embed_pending(corpus, client, expected=expected, batch_size=batch_size)
        total_embedded += result.embedded
        total_skipped += result.skipped_stale
        if not result.did_work:
            break
    remaining = len(corpus.awaiting_embedding(limit=1))
    return EmbedResult(total_embedded, total_skipped, remaining)
