"""The shared corpus repository — the storage half of the `store.py` port."""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.corpus import langfilter, workfilter
from libs.corpus.geo import read_country
from libs.corpus.keys import canonical_url, content_key
from libs.db.models import CorpusJob

# Below this, stored text is a teaser rather than a description and the job is re-fetched from its own page.
MIN_DESCRIPTION = 200

# Consecutive failed reads before a posting is abandoned: never described, embedded or scored.
#
# Two, because the second attempt is already the evidence. The first failure can be a rate limit
# or a blip and deserves another IP; a second says the page has nothing to give. Every attempt on
# the LinkedIn tier is a user's own metered residential bandwidth, and before this rule the same
# ids were retried every fifteen minutes for a week.
#
# A constant rather than a platform setting on purpose — the owner asked for one simple rule. If
# an operator ever needs it different it belongs in `libs.limits` with the rest.
DESCRIBE_MAX_FAILURES = 2


def _readable() -> tuple:
    """Rows we have not given up on reading. Shared by every stage downstream of describing."""
    return (CorpusJob.describe_failures < DESCRIBE_MAX_FAILURES,)


def awaiting_description_conditions(min_chars: int = MIN_DESCRIPTION,
                                    max_age_hours: int | None = None) -> tuple:
    """Rows the describer will actually fetch text for.

    A closed posting is not worth a fetch: on the proxied tier that fetch is the user's own
    metered bandwidth, and nothing downstream will ever read the text.

    Neither is a posting already too old to be delivered. The pre-score filter refuses anything
    past `job_max_age_hours`, so fetching its description spends a user's proxy on a row no account
    can ever be shown. Measured 2026-08-08: the describer was retrying **212** thin LinkedIn rows
    every pass, **156 of them already past the age cutoff**, the oldest first seen 167 hours
    earlier. Same LinkedIn ids, every fifteen minutes, for a week.

    `max_age_hours` is passed in rather than read here, because it is the operator's setting and
    this module is not where platform policy lives.
    """
    fresh: tuple = ()
    if max_age_hours:
        cutoff = datetime.now(UTC) - timedelta(hours=int(max_age_hours))
        # Dated rows against their posted time, undated ones against when we found them — the same
        # rule `UserFilters.fresh_since` applies, so the two cannot disagree about what is stale.
        fresh = (or_(CorpusJob.posted_at >= cutoff,
                     CorpusJob.posted_at.is_(None) & (CorpusJob.first_seen_at >= cutoff)),)

    return (CorpusJob.closed_at.is_(None),
            *_readable(),
            *fresh,
            or_(CorpusJob.description.is_(None),
                func.length(CorpusJob.description) < min_chars))


def awaiting_embedding_conditions() -> tuple:
    """Rows the embedder will actually compute a vector for.

    Same reason as above in CPU rather than bandwidth: the pre-score filter refuses a closed row,
    so embedding one is ~0.37s of the shared embedder spent on nothing.
    """
    return (CorpusJob.closed_at.is_(None),
            # An abandoned row's only "description" is its own title. Embedding that produces a
            # vector for a sentence rather than for a job, and scoring against it is worse than
            # not scoring at all.
            *_readable(),
            CorpusJob.description.is_not(None),
            CorpusJob.embedding.is_(None))


@dataclass(frozen=True)
class DuplicateMatch:
    """Why a candidate was considered already known — the layer that matched, and the row."""

    job_id: int
    matched_on: str      # "url" | "canonical_url" | "content_key"


def text_digest(text: str) -> str:
    """The hash an embedding is keyed on."""
    return sha256((text or "").encode("utf-8")).hexdigest()


class CorpusRepository:
    """Reads and writes the shared corpus. Holds no state beyond its session."""

    def __init__(self, session: Session):
        self._s = session

    # ── dedup ────────────────────────────────────────────────────────────────────────────────

    def find_duplicate(self, url: str, title: str = "", company: str = "", location: str = "",
                       application_url: str | None = None) -> DuplicateMatch | None:
        """The four-layer check, narrowing, in the order the harvest loop applies it."""
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
        """Match through the resolved Apply target."""
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
        """Record the resolved Apply target, discovered during description fetch."""
        self._s.execute(
            update(CorpusJob).where(CorpusJob.id == job_id)
            .values(application_url=application_url,
                    application_canonical_url=canonical_url(application_url)))

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
            fetched_at: datetime | None = None,
            discovered_via: str | None = None,
            discovered_by: uuid.UUID | None = None) -> int | None:
        """Insert if absent."""
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
                # Read once here, for everyone, rather than per user per query.
                country=read_country(location, title=title),
                is_remote=is_remote,
                posted_at=posted_at,
                # When the fetch found this row, so our latency is separable from the board's.
                fetched_at=fetched_at,
                # What the feed card reads.
                easy_apply=easy_apply,
                applicants=applicants,
                reposted=reposted,
                workplace=workplace,
                # How the row got here. `discovered_by` is set only by the per-user tiers; the
                # shared cycle folds one query across every account that wanted it, so there is no
                # single owner and NULL is the honest answer rather than a guess.
                discovered_via=discovered_via,
                discovered_by=discovered_by,
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
        """Attach fetched description text."""
        job = self._s.get(CorpusJob, job_id)
        if job is None:
            return False
        digest = text_digest(description)
        if job.text_sha256 == digest and job.description is not None:
            job.description_fetched_at = func.clock_timestamp()
            return False

        job.description = description
        job.text_sha256 = digest
        # The same clock as `embedded_at`, deliberately.
        job.description_fetched_at = func.clock_timestamp()
        job.embedding = None
        job.embedding_model = None
        job.embedding_dim = None
        job.embedding_version = None
        # The vector is gone, so the time it was written is no longer a fact about this row.
        job.embedded_at = None

        # Classify the working arrangement now the text exists, if nothing authoritative has.
        if job.workplace is None:
            job.workplace = workfilter.classify(
                job.title, job.location or "", description, job.is_remote)
        # Read once here rather than per reader per pass: the language is a fact about the posting.
        job.language = langfilter.detect_language(description)
        return True

    def fill_missing_languages(self, *, limit: int = 500) -> int:
        """Detect the language of rows described before the column existed. No network, bounded."""
        rows = list(self._s.scalars(
            select(CorpusJob)
            .where(CorpusJob.description.is_not(None), CorpusJob.language.is_(None))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(limit)))
        filled = 0
        for job in rows:
            code = langfilter.detect_language(job.description or "")
            if code is not None:
                job.language = code
                filled += 1
        return filled

    # ── what each stage is still waiting for ──────────────────────────────────────────────────
    #
    # Defined here, beside the workers that consume them, and imported by the operator dashboard
    # rather than restated there. The dashboard used to ask a different question from the workers, so
    # a **closed** posting with text and no vector counted as waiting to be embedded forever: the
    # embedder skips closed rows on purpose, so the backlog could never drain. Two rows sat there
    # for 25 hours reading as a stuck pipeline, and nothing was stuck.

    def awaiting_description(self, *, limit: int = 200, linkedin: bool | None = None,
                             min_chars: int = MIN_DESCRIPTION,
                             max_age_hours: int | None = None) -> list[CorpusJob]:
        """Jobs whose stored text is too thin to score on."""
        query = (
            select(CorpusJob)
            .where(*awaiting_description_conditions(min_chars, max_age_hours))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(limit))
        if linkedin is True:
            query = query.where(CorpusJob.url.like("%/jobs/view/%"))
        elif linkedin is False:
            query = query.where(CorpusJob.url.not_like("%/jobs/view/%"))
        return list(self._s.scalars(query))

    def awaiting_metadata(self, *, limit: int = 200) -> list[CorpusJob]:
        """LinkedIn rows whose description is fine but whose posting signals were never read."""
        return list(self._s.scalars(
            select(CorpusJob)
            .where(CorpusJob.closed_at.is_(None),
                   CorpusJob.url.like("%/jobs/view/%"),
                   CorpusJob.applicants.is_(None),
                   CorpusJob.posted_at.is_(None))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(limit)))

    def record_describe_failure(self, job_id: int) -> int:
        """One more failed read. Returns the new count, so the caller can log the abandonment."""
        return self._s.scalar(
            update(CorpusJob)
            .where(CorpusJob.id == job_id)
            .values(describe_failures=CorpusJob.describe_failures + 1)
            .returning(CorpusJob.describe_failures))

    def clear_describe_failures(self, job_id: int) -> None:
        """A read that worked. Consecutive means consecutive."""
        self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id, CorpusJob.describe_failures > 0)
            .values(describe_failures=0))

    def mark_closed(self, job_id: int) -> None:
        """Record that the posting is no longer accepting applications."""
        self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id, CorpusJob.closed_at.is_(None))
            .values(closed_at=datetime.now(UTC)))

    def set_posting_metadata(self, job_id: int, *, applicants: int | None = None,
                             applicants_caption: str | None = None,
                             easy_apply: bool | None = None, reposted: bool | None = None,
                             workplace: str | None = None,
                             posted_at: datetime | None = None) -> None:
        """Record what the posting's own page said."""
        values = {name: value for name, value in (
            ("applicants", applicants), ("applicants_caption", applicants_caption),
            ("easy_apply", easy_apply), ("reposted", reposted),
            ("workplace", workplace)) if value is not None}
        if values:
            self._s.execute(update(CorpusJob).where(CorpusJob.id == job_id).values(**values))
        if posted_at is not None:
            self._set_posted_at(job_id, posted_at)

    def _set_posted_at(self, job_id: int, posted_at: datetime) -> None:
        """Record when a posting went up, never later than the moment we first saw it."""
        self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id)
            .values(posted_at=func.least(posted_at, CorpusJob.first_seen_at)))

    # ── embedding ────────────────────────────────────────────────────────────────────────────

    def awaiting_embedding(self, limit: int = 500) -> list[CorpusJob]:
        """Jobs with text but no vector. Backed by the partial index on the same predicate."""
        return list(self._s.scalars(
            select(CorpusJob)
            .where(*awaiting_embedding_conditions())
            .order_by(CorpusJob.id)
            .limit(limit)))

    def set_embedding(self, job_id: int, *, vector: bytes, text_sha256: str,
                      model: str, dim: int, version: str) -> bool:
        """Store a vector, but only if the text it was computed from is still the current text."""
        result = self._s.execute(
            update(CorpusJob)
            .where(CorpusJob.id == job_id, CorpusJob.text_sha256 == text_sha256)
            .values(embedding=vector, embedding_model=model, embedding_dim=dim,
                    embedding_version=version,
                    # Stamped here: the moment the vector became readable by a scorer.
                    embedded_at=func.clock_timestamp()))
        return result.rowcount == 1

    # Retention lives in `libs.corpus.archive`. `trim_cold` and `pin` were removed on 2026-08-11:
    # trimming nulled the description and vector in place, which dropped the row out of every future
    # training set without raising, and `pinned` was a denormalised copy of "somebody labelled or
    # acted on this" that two of the three label writers never set.
