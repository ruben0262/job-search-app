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

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.corpus.keys import canonical_url, content_key
from libs.db.models import CorpusJob

# A job keeps its description and vector for this long. After that, if nobody has acted on it, both
# are dropped and the ~258-byte dedup key is kept forever. This is what makes 0.87 GB a *steady*
# state rather than a number that grows until someone notices.
CORPUS_HOT_DAYS = 90

# Below this, stored text is a teaser rather than a description and the job is re-fetched from its
# own page. The reference's threshold, and it is a judgement about listing pages rather than a round
# number: at 200 characters a LinkedIn snippet is still the first paragraph of the advert.
MIN_DESCRIPTION = 200


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

    def find_duplicate(self, url: str, title: str = "", company: str = "", location: str = "",
                       application_url: str | None = None) -> DuplicateMatch | None:
        """The four-layer check, narrowing, in the order the harvest loop applies it.

        Returns `None` when the job is new — including when its keys are unusable, which is the
        fail-open case and deliberately indistinguishable from "genuinely new" to the caller.

        `application_url` is the resolved external Apply target, known only after description fetch
        for aggregator sources. Passing it enables the layer that the whole canonical scheme exists
        for — see `_apply_url_match`.
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

        hit = self._apply_url_match(canon, application_url)
        if hit is not None:
            return hit

        key = content_key(title, company, location)
        if key is not None:
            hit = self._s.scalar(
                select(CorpusJob.id).where(CorpusJob.content_key == key).limit(1))
            if hit is not None:
                return DuplicateMatch(hit, "content_key")

        return None

    def _apply_url_match(self, canon: str | None,
                         application_url: str | None) -> DuplicateMatch | None:
        """Match through the resolved Apply target. **This is the layer canonicalisation is for.**

        The same job arrives as `linkedin.com/jobs/view/…` from the LinkedIn scrape and as
        `boards.greenhouse.io/…` from the direct ATS poll. Nothing about those two URLs is
        comparable, and the content key only helps when title and company both normalise
        identically — which aggregators frequently break by decorating titles or abbreviating
        company names. Resolving LinkedIn's Apply button to its external target makes them
        comparable, and the reference matches on `canonical = ? OR app_canonical = ?` for exactly
        this reason.

        Both directions matter, so both are checked:

        - the candidate's **apply target** may equal a stored row's own URL (LinkedIn arrives
          second, the Greenhouse row is already there);
        - the candidate's **own URL** may equal a stored row's apply target (the ATS poll arrives
          second, having been beaten by a LinkedIn listing that resolved to it).
        """
        app_canon = canonical_url(application_url or "")

        if app_canon is not None:
            hit = self._s.scalar(
                select(CorpusJob.id)
                .where((CorpusJob.canonical_url == app_canon)
                       | (CorpusJob.application_canonical_url == app_canon))
                .limit(1))
            if hit is not None:
                return DuplicateMatch(hit, "application_url")

        if canon is not None:
            hit = self._s.scalar(
                select(CorpusJob.id)
                .where(CorpusJob.application_canonical_url == canon).limit(1))
            if hit is not None:
                return DuplicateMatch(hit, "application_url")

        return None

    def set_application_url(self, job_id: int, application_url: str) -> None:
        """Record the resolved Apply target, discovered during description fetch.

        Stored with its canonical form alongside so the dedup query stays an indexed lookup rather
        than a function call per row.
        """
        self._s.execute(
            update(CorpusJob).where(CorpusJob.id == job_id)
            .values(application_url=application_url,
                    application_canonical_url=canonical_url(application_url)))

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
            is_remote: bool | None = None, posted_at: datetime | None = None,
            easy_apply: bool | None = None, applicants: int | None = None,
            reposted: bool | None = None, workplace: str | None = None,
            fetched_at: datetime | None = None) -> int | None:
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
                # When the fetch that found this row produced it, so how long *we* took is separable
                # from how long the board took to publish (ROADMAP 6ab).
                fetched_at=fetched_at,
                # What the feed card reads. Opportunistic: a source that reports none of them still
                # produces a row, and the card omits the elements rather than the job.
                easy_apply=easy_apply,
                applicants=applicants,
                reposted=reposted,
                workplace=workplace,
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
            update(CorpusJob).where(CorpusJob.id.in_(job_ids))
            .values(last_seen_at=func.clock_timestamp()))

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
            job.description_fetched_at = func.clock_timestamp()
            return False

        job.description = description
        job.text_sha256 = digest
        # **The same clock as `embedded_at`, deliberately.** This read Python's clock while the
        # embed step read Postgres', so the two timestamps on one row came from two machines'
        # ideas of the time and the span between them measured their disagreement as much as the
        # pipeline. One clock, and it is the database's, because that is the one every row shares.
        job.description_fetched_at = func.clock_timestamp()
        job.embedding = None
        job.embedding_model = None
        job.embedding_dim = None
        job.embedding_version = None
        # The vector is gone, so the time it was written is no longer a fact about this row. Left
        # behind it would date the *next* embed to before its own text arrived, and the latency
        # page would read that as a negative span.
        job.embedded_at = None
        return True

    def awaiting_description(self, *, limit: int = 200, linkedin: bool | None = None,
                             min_chars: int = MIN_DESCRIPTION) -> list[CorpusJob]:
        """Jobs whose stored text is too thin to score on. Newest first.

        A row can be here for two reasons and they need the same treatment: the source gave no
        snippet at all, or it gave a teaser. `min_chars` is what separates a description from an
        advertisement — below it the labeller is reading the listing page's furniture.

        `linkedin` splits the work by **who can pay for it**. LinkedIn's guest endpoint blocks
        datacenter IPs, so those rows can only be filled by a caller holding a user's residential
        proxy lease; everything else is free on the shared client. Passing None takes both.

        Trimmed rows are excluded: retention deliberately dropped that text, and re-fetching it
        would undo the sweep on every cycle.
        """
        query = (
            select(CorpusJob)
            .where(CorpusJob.trimmed_at.is_(None),
                   or_(CorpusJob.description.is_(None),
                       func.length(CorpusJob.description) < min_chars))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(limit))
        if linkedin is True:
            query = query.where(CorpusJob.url.like("%/jobs/view/%"))
        elif linkedin is False:
            query = query.where(CorpusJob.url.not_like("%/jobs/view/%"))
        return list(self._s.scalars(query))

    def awaiting_metadata(self, *, limit: int = 200) -> list[CorpusJob]:
        """LinkedIn rows whose description is fine but whose posting signals were never read.

        **A different question from `awaiting_description`, and the corpus is full of the
        difference.** JobSpy returns a usable description for most LinkedIn postings, so those rows
        never look thin — and on 2026-07-29 all 3,494 of them still had a null applicant count,
        posted timestamp, repost flag and workplace tag, because nothing had ever opened the guest
        page. Four of the five inputs to opportunity ranking, absent for the largest source in the
        corpus.

        `applicants IS NULL AND posted_at IS NULL` is the "never looked" test rather than a
        timestamp: a row that was refreshed and genuinely has no applicant caption keeps its posted
        date, so it drops out of this set and is not re-fetched every hour forever.

        Newest first, because these are the rows about to be delivered and an applicant count on a
        three-week-old posting changes nobody's decision.
        """
        return list(self._s.scalars(
            select(CorpusJob)
            .where(CorpusJob.trimmed_at.is_(None),
                   CorpusJob.closed_at.is_(None),
                   CorpusJob.url.like("%/jobs/view/%"),
                   CorpusJob.applicants.is_(None),
                   CorpusJob.posted_at.is_(None))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(limit)))

    def mark_closed(self, job_id: int) -> None:
        """Record that the posting is no longer accepting applications. Idempotent.

        Not a deletion and not a trim: the row, its description and any CV already built against it
        all stay. What stops is delivery. `closed_at` is set once and never moved, because a
        listing that reopens is rare and a timestamp that drifts is worse than a stale one.
        """
        self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id, CorpusJob.closed_at.is_(None))
            .values(closed_at=datetime.now(UTC)))

    def set_posting_metadata(self, job_id: int, *, applicants: int | None = None,
                             easy_apply: bool | None = None, reposted: bool | None = None,
                             workplace: str | None = None,
                             posted_at: datetime | None = None) -> None:
        """Record what the posting's own page said. **A None never overwrites a known value.**

        That rule is the whole design. These signals come off pages that answer inconsistently —
        the guest fragment carries the applicant count but not the apply button, the full page the
        reverse — so a fetch that simply did not see a field must not erase what an earlier one
        did. Clearing `workplace` this way would silently un-filter a job the user had excluded.
        """
        values = {name: value for name, value in (
            ("applicants", applicants), ("easy_apply", easy_apply), ("reposted", reposted),
            ("workplace", workplace), ("posted_at", posted_at)) if value is not None}
        if values:
            self._s.execute(update(CorpusJob).where(CorpusJob.id == job_id).values(**values))

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
                    embedding_version=version,
                    # Stamped here rather than at the gateway: this is the moment the vector became
                    # readable by a scorer, and the gateway's own clock would count a write that
                    # lost the `text_sha256` race as an embed that never landed.
                    #
                    # **`clock_timestamp()`, not `now()`.** Postgres' `now()` is the *transaction*
                    # start, so every row embedded in one transaction was stamped with the moment
                    # that transaction opened — before the describe pass inside it had run. Measured
                    # on the live box, `embedded_at - description_fetched_at` came out **negative**
                    # for most rows, which the latency query then discarded as a clock change. The
                    # queue had this exact defect in `finished_at` and it is the same fix.
                    embedded_at=func.clock_timestamp()))
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
                    embedding_dim=None, embedding_version=None, embedded_at=None,
                    trimmed_at=func.clock_timestamp()))
        return result.rowcount
