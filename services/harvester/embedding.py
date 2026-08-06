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

from libs.contracts.embedder import DEFAULT_EMBED_TIMEOUT, EmbedderClient, EmbedderVersion, pack_vector
from libs.corpus.embedtext import build_text, embedding_version
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# **Roughly how long one text takes to embed**, measured on the live box against the real backend:
# 16 texts in 17.1s, 32 in 26.8s, 64 in 52.1s, 128 in 107.8s — flat at ~840 ms a text above a batch
# of 32, because TEI is CPU-bound here and takes 32 at a time whatever it is handed.
#
# Kept as a number rather than folded into the batch size so the relationship below is visible: the
# batch is derived from this and the client timeout, and a change to either has to move the other.
EMBED_SECONDS_PER_TEXT = 0.9

# How many rows to embed per pass. The gateway chunks internally to whatever TEI accepts, so this
# bounds the *transaction*, not the request: a smaller number commits progress more often, which
# matters because a cold-start batch of 2,000 takes ~5 minutes and a crash at minute four should not
# discard four minutes of work.
#
# **It was 200, and 200 could never succeed.** At the measured rate that is ~168 seconds against a
# client timeout of 120 — so a full batch always timed out, and because the backlog is what produces
# a full batch, the timeout preserved the condition that caused it. Live: 229 rows waiting, every
# cycle dying at the same step, and the exception taking the scoring enqueue down with it
# (ROADMAP 6ba).
#
# Derived from the timeout with room to spare rather than chosen: at 64 the same measurement gives
# ~52 seconds, which leaves the slow half of the distribution somewhere to go.
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
    # **Title first, then description.** Embedding the description alone was the reference's
    # non-default mode and it discarded the single strongest statement of what a job is — which
    # also happens to be the part that survives the encoder's 512-token truncation, but only if it
    # leads (`libs/corpus/embedtext.py`).
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
