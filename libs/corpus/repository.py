"""The shared corpus repository — the storage half of the `store.py` port.

**Shared, not per-user, and the type signature says so.** Nothing here takes a `user_id`, because
the corpus holds public job postings and no user data may ever land on one of its rows. That is what
makes account deletion one cascading statement and what keeps the storage projection true
(`PLATFORM_ARCHITECTURE.md` §4, §8). Per-user reads and writes live in a separate repository whose
methods take `user_id` as a required first argument — the split is deliberate so that a
tenant-scoped query which forgets its tenant does not construct (`TENANT_SAFETY.md` §1.3).

**One access mode: indexed queries.** The reference exposes two — a materialised `.load()` that
mirrors the whole table into memory, and a live `.connect()` doing indexed lookups. The materialised
mode is an artifact of the JSON store that preceded SQLite, where the file had to be read wholesale
anyway; it was kept only so existing callers survived that migration. Carrying it to Postgres would
mean an O(history) memory pattern in a multi-tenant service reading a corpus that grows by ~1,500
rows a day for everyone at once. Dropped on purpose.

**Dedup order is url → canonical → content_key**, narrowing, exactly as the harvest loop does it
today (`HARVESTER_PLAN.md` §4 step 4). The key functions themselves are in `keys.py` and are pinned
byte-for-byte against the reference by T2; this module is only responsible for asking the database
the same questions in the same order.

**`None` keys never match.** `canonical_url()` and `content_key()` return `None` for input they
cannot use, and `None` means "no opinion". A query that treated it as a value would match every
other row with an unusable key and collapse them into one job — so every lookup here short-circuits
on a null key and reports "not seen", letting the job through undeduplicated. Admitting a possible
duplicate is recoverable; silently discarding a real posting is not.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.corpus.keys import canonical_url, content_key
from libs.db.models import CorpusJob

# A job keeps its description and vector for this long. After that, if nobody has acted on it, both
# are dropped and the ~258-byte dedup key is kept forever. This is what makes 0.87 GB a *steady*
# state rather than a number that grows until someone notices.
CORPUS_HOT_DAYS = 90


@dataclass(frozen=True)
class DuplicateMatch:
    """Why a candidate was considered already known — the layer that matched, and the row.

    The layer is recorded rather than discarded because "how did we decide this was a duplicate" is
    the first question asked when dedup misbehaves, and reconstructing it after the fact means
    re-running three queries against a corpus that has since changed.
    """

    job_id: int
    matched_on: str      # "url" | "canonical_url" | "content_key"


def text_digest(text: str) -> str:
    """The hash an embedding is keyed on.

    Vectors are keyed on the *content*, never on `job_id`: descriptions get re-fetched and mutated
    on live postings, so an id-keyed vector silently goes stale against text it no longer
    represents — and a stale vector does not error, it just scores wrong.
    """
    return sha256((text or "").encode("utf-8")).hexdigest()


class CorpusRepository:
    """Reads and writes the shared corpus. Holds no state beyond its session."""

    def __init__(self, session: Session):
        self._s = session

    # ── dedup ────────────────────────────────────────────────────────────────────────────────

    def find_duplicate(self, url: str, title: str = "", company: str = "",
                       location: str = "") -> DuplicateMatch | None:
        """The three-layer check, narrowing, in the order the harvest loop applies it.

        Returns `None` when the job is new — including when its keys are unusable, which is the
        fail-open case and deliberately indistinguishable from "genuinely new" to the caller.
        """
        exact = self._s.scalar(select(CorpusJob.id).where(CorpusJob.url == url).limit(1))
        if exact is not None:
            return DuplicateMatch(exact, "url")

        canon = canonical_url(url)
        if canon is not None:
            hit = self._s.scalar(
                select(CorpusJob.id).where(CorpusJob.canonical_url == canon).limit(1))
            if hit is not None:
                return DuplicateMatch(hit, "canonical_url")

        key = content_key(title, company, location)
        if key is not None:
            hit = self._s.scalar(
                select(CorpusJob.id).where(CorpusJob.content_key == key).limit(1))
            if hit is not None:
                return DuplicateMatch(hit, "content_key")

        return None

    def seen_url(self, url: str) -> bool:
        return self._s.scalar(select(CorpusJob.id).where(CorpusJob.url == url).limit(1)) is not None

    def seen_canonical(self, canon: str | None) -> bool:
        if not canon:                                   # None means no opinion — never a match
            return False
        return self._s.scalar(
            select(CorpusJob.id).where(CorpusJob.canonical_url == canon).limit(1)) is not None

    def seen_content_key(self, key: str | None) -> bool:
        if not key:                                     # ditto
            return False
        return self._s.scalar(
            select(CorpusJob.id).where(CorpusJob.content_key == key).limit(1)) is not None

    # ── ingest ───────────────────────────────────────────────────────────────────────────────

    def add(self, *, url: str, source: str, title: str, company: str = "", location: str = "",
            is_remote: bool | None = None, posted_at: datetime | None = None) -> int | None:
        """Insert if absent. Returns the new row's id, or `None` if the url was already present.

        `ON CONFLICT DO NOTHING` is the port of the reference's `INSERT OR IGNORE`, and the property
        that matters is the same one: **an existing `first_seen_at` is never overwritten.** That
        column is what recency ordering and the retention sweep both read, so re-discovering a job
        must not make it look new again — a source that re-lists an old posting every cycle would
        otherwise keep it permanently fresh and permanently un-trimmable.
        """
        stmt = (
            pg_insert(CorpusJob)
            .values(
                url=url,
                canonical_url=canonical_url(url),
                content_key=content_key(title, company, location),
                source=source,
                title=title,
                company=company or None,
                location=location or None,
                is_remote=is_remote,
                posted_at=posted_at,
            )
            .on_conflict_do_nothing(index_elements=[CorpusJob.url])
            .returning(CorpusJob.id)
        )
        return self._s.scalar(stmt)

    def touch_last_seen(self, job_ids: Sequence[int]) -> None:
        """Record that these jobs are still being listed, without disturbing `first_seen_at`."""
        if not job_ids:
            return
        self._s.execute(
            update(CorpusJob).where(CorpusJob.id.in_(job_ids)).values(last_seen_at=func.now()))

    def set_description(self, job_id: int, description: str) -> bool:
        """Attach fetched description text. Returns True if the text changed.

        **A changed description invalidates the vector.** `refresh.py` in the reference re-fetches
        live postings and their text does change, so the embedding is cleared here rather than left
        to be noticed later — a vector that no longer matches its text produces confidently wrong
        scores and raises nothing.
        """
        job = self._s.get(CorpusJob, job_id)
        if job is None:
            return False
        digest = text_digest(description)
        if job.text_sha256 == digest and job.description is not None:
            job.description_fetched_at = datetime.now(UTC)
            return False

        job.description = description
        job.text_sha256 = digest
        job.description_fetched_at = datetime.now(UTC)
        job.embedding = None
        job.embedding_model = None
        job.embedding_dim = None
        job.embedding_version = None
        return True

    # ── embedding ────────────────────────────────────────────────────────────────────────────

    def awaiting_embedding(self, limit: int = 500) -> list[CorpusJob]:
        """Jobs with text but no vector. Backed by the partial index on the same predicate."""
        return list(self._s.scalars(
            select(CorpusJob)
            .where(CorpusJob.embedding.is_(None), CorpusJob.description.is_not(None))
            .order_by(CorpusJob.id)
            .limit(limit)))

    def set_embedding(self, job_id: int, *, vector: bytes, text_sha256: str,
                      model: str, dim: int, version: str) -> bool:
        """Store a vector, but only if the text it was computed from is still the current text.

        Returns False when the description changed while the embedding was in flight — which is a
        real race, because bulk embedding a cold-start batch takes minutes and `refresh.py` runs
        concurrently. Writing anyway would attach a vector to text that no longer exists, and the
        `(model, dim, version)` assert downstream would not catch it: the vector space is right, the
        *content* is wrong. Failing here means the job is simply picked up again by
        `awaiting_embedding`.
        """
        result = self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id, CorpusJob.text_sha256 == text_sha256)
            .values(embedding=vector, embedding_model=model, embedding_dim=dim,
                    embedding_version=version))
        return result.rowcount == 1

    # ── retention ────────────────────────────────────────────────────────────────────────────

    def pin(self, job_id: int) -> None:
        """Mark a job as acted upon, so retention never trims it.

        Called whenever any user applies, builds a CV, or labels. Losing the text under a label
        would leave the label meaningless, and losing it under an applied-record would destroy the
        one thing the user most wants kept.
        """
        self._s.execute(update(CorpusJob).where(CorpusJob.id == job_id).values(pinned=True))

    def trim_cold(self, *, hot_days: int = CORPUS_HOT_DAYS, limit: int = 1000) -> int:
        """Drop description and vector from unpinned rows older than `hot_days`. Returns the count.

        **The row itself is kept forever** — the ~258-byte dedup key is what stops a job that aged
        out being re-ingested as new on the next harvest. Deleting rows instead would make the
        corpus re-acquire and re-embed the same postings indefinitely.
        """
        cutoff = datetime.now(UTC) - timedelta(days=hot_days)
        stale = (
            select(CorpusJob.id)
            .where(CorpusJob.pinned.is_(False),
                   CorpusJob.trimmed_at.is_(None),
                   CorpusJob.first_seen_at < cutoff)
            .limit(limit)
            .scalar_subquery())
        result = self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id.in_(stale))
            .values(description=None, embedding=None, text_sha256=None, embedding_model=None,
                    embedding_dim=None, embedding_version=None, trimmed_at=func.now()))
        return result.rowcount
