"""The shared corpus repository — the storage half of the `store.py` port."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs.corpus import langfilter, workfilter
from libs.corpus.geo import posting_country, read_city
from libs.corpus.keys import canonical_url, content_key
from libs.db import insert, now
from libs.db.models import CorpusJob, CountryCount

# Below this, stored text is a teaser rather than a description and the job is re-fetched from its own page.
MIN_DESCRIPTION = 200

# The most of a description kept, applied here because every description is stored here. 4,000 cut
# a third of postings mid-sentence (389 of 1,209 locally); the whole LinkedIn text of 24 of those,
# fetched 2026-11-02, was a median 5,831 characters and at most 10,260, so this keeps 23 of 24 whole.
MAX_DESCRIPTION = 12_000

# The source of a job somebody wrote in themselves (Add a job): theirs alone, never anybody else's match
# (owner, 2026-11-03). Read by `libs.tenant.filters.candidate_conditions`, which every match asks.
TYPED = "typed"

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


def awaiting_description_conditions(min_chars: int = MIN_DESCRIPTION,
                                    max_age_hours: int | None = None) -> list[dict]:
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
    conditions = [{"closed_at": None}, {"describe_failures": {"$lt": DESCRIBE_MAX_FAILURES}},
                  {"$expr": {"$lt": [{"$strLenCP": {"$ifNull": ["$description", ""]}}, min_chars]}}]
    if max_age_hours:
        cutoff = now() - timedelta(hours=int(max_age_hours))
        # Dated rows against their posted time, undated ones against when we found them — the same
        # rule `UserFilters.fresh_since` applies, so the two cannot disagree about what is stale.
        conditions.append({"$or": [{"posted_at": {"$gte": cutoff}},
                                   {"posted_at": None, "first_seen_at": {"$gte": cutoff}}]})
    return conditions


def _awaiting(linkedin: bool | None, min_chars: int, max_age_hours: int | None) -> dict:
    """`awaiting_description`'s question, which its count asks too: LinkedIn's postings, the others',
    or both."""
    where = awaiting_description_conditions(min_chars, max_age_hours)
    if linkedin is True:
        where.append({"url": {"$regex": "/jobs/view/"}})
    elif linkedin is False:
        where.append({"url": {"$not": {"$regex": "/jobs/view/"}}})
    return {"$and": where}


@dataclass(frozen=True)
class DuplicateMatch:
    """Why a candidate was considered already known — the layer that matched, and the row."""

    job_id: int
    matched_on: str      # "url" | "canonical_url" | "content_key"


class CorpusRepository:
    """Reads and writes the shared corpus."""

    def __init__(self, db: Database):
        self._db = db
        self._corpus = db[CorpusJob.COLLECTION]

    def _first(self, where: dict) -> int | None:
        row = self._corpus.find_one(where, {"_id": 1})
        return row["_id"] if row else None

    # ── dedup ────────────────────────────────────────────────────────────────────────────────

    def find_duplicate(self, url: str, title: str = "", company: str = "", location: str = "",
                       application_url: str | None = None) -> DuplicateMatch | None:
        """The four-layer check, narrowing, in the order the harvest loop applies it."""
        exact = self._first({"url": url})
        if exact is not None:
            return DuplicateMatch(exact, "url")

        canon = canonical_url(url)
        if canon is not None:
            hit = self._first({"canonical_url": canon})
            if hit is not None:
                return DuplicateMatch(hit, "canonical_url")

        hit = self._apply_url_match(canon, application_url)
        if hit is not None:
            return hit

        key = content_key(title, company, location)
        if key is not None:
            hit = self._first({"content_key": key})
            if hit is not None:
                return DuplicateMatch(hit, "content_key")

        return None

    def _apply_url_match(self, canon: str | None,
                         application_url: str | None) -> DuplicateMatch | None:
        """Match through the resolved Apply target."""
        app_canon = canonical_url(application_url or "")

        if app_canon is not None:
            hit = self._first({"$or": [{"canonical_url": app_canon},
                                       {"application_canonical_url": app_canon}]})
            if hit is not None:
                return DuplicateMatch(hit, "application_url")

        if canon is not None:
            hit = self._first({"application_canonical_url": canon})
            if hit is not None:
                return DuplicateMatch(hit, "application_url")

        return None

    def set_application_url(self, job_id: int, application_url: str) -> None:
        """Record the resolved Apply target, discovered during description fetch."""
        self._corpus.update_one({"_id": job_id}, {"$set": {
            "application_url": application_url,
            "application_canonical_url": canonical_url(application_url)}})

    # ── ingest ───────────────────────────────────────────────────────────────────────────────

    def add(self, *, url: str, source: str, title: str, company: str = "", location: str = "",
            is_remote: bool | None = None, posted_at: datetime | None = None,
            easy_apply: bool | None = None, applicants: int | None = None,
            reposted: bool | None = None, workplace: str | None = None,
            fetched_at: datetime | None = None,
            discovered_via: str | None = None) -> int | None:
        """Insert if absent: the new id, or `None` when the URL is already stored."""
        job = CorpusJob(
            url=url,
            canonical_url=canonical_url(url),
            content_key=content_key(title, company, location),
            source=source,
            title=title,
            company=company or None,
            location=location or None,
            # Read once here, for everyone, rather than per user per query.
            country=posting_country(location, title=title),
            city=read_city(location),
            is_remote=is_remote,
            posted_at=posted_at,
            # When the fetch found this row, so our latency is separable from the board's.
            fetched_at=fetched_at,
            # What the feed card reads.
            easy_apply=easy_apply,
            applicants=applicants,
            reposted=reposted,
            workplace=workplace,
            # How the row got here.
            discovered_via=discovered_via,
        )
        try:
            job_id = insert(self._db, job).id
        except DuplicateKeyError:
            return None
        # One more in its hour and country, for the admin's chart.
        hour = job.first_seen_at.replace(minute=0, second=0, microsecond=0)
        self._db[CountryCount.COLLECTION].update_one(
            {"_id": f"{hour:%Y-%m-%dT%H}|{job.country or ''}"},
            {"$inc": {"jobs": 1}, "$setOnInsert": {"hour": hour, "country": job.country}}, upsert=True)
        return job_id

    def add_typed(self, *, url: str, title: str, company: str, location: str, description: str) -> int:
        """A job a person wrote in, kept for them alone: their own mark on it (`added_at`) is the only way
        to it. Not a public posting, so nothing about it may stand in for one:
        - no dedup key, or the harvest would take the real posting for it and never store it;
        - its own address, as an address is stored once and the real posting may come to have this one.
          The page's address with `#typed-…` after it, which still opens the page;
        - no candidate for anybody's words, alerts included (`TYPED`).
        It holds nobody's name: whose it is lives on their own row (TENANT_SAFETY §1, rule 2)."""
        job = CorpusJob(url=f"{url.split('#', 1)[0]}#typed-{secrets.token_hex(6)}", source=TYPED, title=title,
                        company=company or None, location=location or None,
                        country=posting_country(location, title=title), city=read_city(location),
                        discovered_via=TYPED)
        job_id = insert(self._db, job).id
        self.set_description(job_id, description)
        return job_id

    def set_description(self, job_id: int, description: str) -> bool:
        """Attach fetched description text, up to `MAX_DESCRIPTION` of it."""
        description = description[:MAX_DESCRIPTION]
        row = self._corpus.find_one({"_id": job_id}, {"description": 1, "title": 1, "location": 1,
                                                      "is_remote": 1, "workplace": 1, "described_at": 1})
        if row is None:
            return False
        if row["description"] == description:
            self._corpus.update_one({"_id": job_id}, {"$set": {"description_fetched_at": now()}})
            return False

        changes = {"description": description, "description_fetched_at": now(),
                   # Read once here rather than per reader per pass: the language is a fact about the posting.
                   "language": langfilter.detect_language(description),
                   # New text, so its words are found again (`libs.corpus.terms.catch_up`); until
                   # then matching reads it the slow way, and finds them all the same.
                   "terms_at": None}
        if len(description) >= MIN_DESCRIPTION and not row.get("described_at"):
            # A whole job from now: alerts wait for this, not for the posting to be found.
            changes["described_at"] = now()
        # Classify the working arrangement now the text exists, if nothing authoritative has.
        if row["workplace"] is None:
            changes["workplace"] = workfilter.classify(
                row["title"], row["location"] or "", description, row["is_remote"])
        self._corpus.update_one({"_id": job_id}, {"$set": changes})
        return True

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
        """Jobs whose stored text is too thin to match words against, newest first. A `limit` of 0
        is every one of them."""
        return self._jobs(_awaiting(linkedin, min_chars, max_age_hours), limit)

    def count_awaiting_description(self, *, linkedin: bool | None = None,
                                   max_age_hours: int | None = None) -> int:
        """How many `awaiting_description` would return with no limit: a backlog that grows run after run
        is a pass that cannot keep up."""
        return self._corpus.count_documents(_awaiting(linkedin, MIN_DESCRIPTION, max_age_hours))

    def _jobs(self, where: dict, limit: int) -> list[CorpusJob]:
        return [CorpusJob.from_doc(row) for row in
                self._corpus.find(where, sort=[("first_seen_at", -1)], limit=limit)]

    def record_describe_failure(self, job_id: int) -> int:
        """One more failed read. Returns the new count, so the caller can log the abandonment."""
        row = self._corpus.find_one_and_update({"_id": job_id}, {"$inc": {"describe_failures": 1}},
                                               {"describe_failures": 1},
                                               return_document=ReturnDocument.AFTER)
        return row["describe_failures"] if row else 0

    def clear_describe_failures(self, job_id: int) -> None:
        """A read that worked. Consecutive means consecutive."""
        self._corpus.update_one({"_id": job_id, "describe_failures": {"$gt": 0}},
                                {"$set": {"describe_failures": 0}})

    def mark_closed(self, job_id: int) -> None:
        """Record that the posting is no longer accepting applications."""
        self._corpus.update_one({"_id": job_id, "closed_at": None}, {"$set": {"closed_at": now()}})

    def set_posting_metadata(self, job_id: int, *, applicants: int | None = None,
                             applicants_caption: str | None = None,
                             easy_apply: bool | None = None, reposted: bool | None = None,
                             workplace: str | None = None,
                             posted_at: datetime | None = None) -> bool:
        """Record what the posting's own page said. True if that changed anything stored."""
        values = {name: value for name, value in (
            ("applicants", applicants), ("applicants_caption", applicants_caption),
            ("easy_apply", easy_apply), ("reposted", reposted),
            ("workplace", workplace)) if value is not None}
        changed = bool(values) and self._corpus.update_one(
            {"_id": job_id}, {"$set": values}).modified_count > 0
        if posted_at is not None:
            changed = self._set_posted_at(job_id, posted_at) or changed
        return changed

    def _set_posted_at(self, job_id: int, posted_at: datetime) -> bool:
        """Record when a posting went up, never later than the moment we first saw it."""
        return self._corpus.update_one(
            {"_id": job_id},
            [{"$set": {"posted_at": {"$min": [posted_at, "$first_seen_at"]}}}]).modified_count > 0

    # Retention lives in `libs.corpus.archive`. `trim_cold` and `pin` were removed on 2026-08-11:
    # trimming nulled the description and vector in place, which dropped the row out of every future
    # training set without raising, and `pinned` was a denormalised copy of "somebody labelled or
    # acted on this" that two of the three label writers never set.
