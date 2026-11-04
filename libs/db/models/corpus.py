"""The shared job corpus — the one collection that is not per-user, and must never become per-user."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pymongo import DESCENDING, IndexModel

from libs.db import Document, now

# How a posting is worked, and the switches My search offers for it. `None` on a posting is unknown.
WORKPLACES = ("remote", "hybrid", "onsite")


@dataclass(kw_only=True)
class CorpusJob(Document):
    """One public posting. Its integer id is what the site, the alerts and Telegram's buttons carry."""

    COLLECTION = "corpus"
    BACKED_UP = False
    OWNER = None
    SEQUENCE = True
    CHOICES = {"workplace": (*WORKPLACES, None)}  # noqa: RUF012
    INDEXES = (IndexModel("url", unique=True, name="url_unique"),
               IndexModel("canonical_url", name="canonical_url"),
               IndexModel("content_key", name="content_key"),
               IndexModel("application_canonical_url", name="application_canonical_url"),
               IndexModel([("source", 1), ("first_seen_at", 1)], name="source_first_seen"),
               # The oldest first, for the archive pass (`libs.corpus.archive`): without it, finding 1,000
               # postings to move read all 397,000 and sorted them, 2 s a run at 400,000 (2026-11-05).
               IndexModel([("first_seen_at", 1)], name="first_seen"),
               # The jobs page and the alerts: newest first, open postings only. With the id, the order the
               # match sorts by, so it is read in order and stops: without it, the match for a word not yet
               # searched for sorted 400,000 postings, 79 s at 1.5 million (2026-11-04).
               IndexModel([("first_seen_at", DESCENDING), ("_id", DESCENDING)],
                          partialFilterExpression={"closed_at": None}, name="open_newest_first"),
               IndexModel("describe_failures", partialFilterExpression={"describe_failures": {"$gt": 0}},
                          name="describe_failures"),
               # A person's jobs: the postings in their countries that have one of their words
               # (`libs.corpus.terms`), newest first in the order the match sorts by, so it reads the
               # newest and stops, rather than every posting with the word, in every country, and then
               # sorts them (`libs.matching`, `filters._fresh_enough`).
               IndexModel([("terms", 1), ("country", 1), ("first_seen_at", DESCENDING), ("_id", DESCENDING)],
                          partialFilterExpression={"closed_at": None}, name="open_terms_newest"),
               # The postings whose words are still to be found, which matching reads the slow way: newest
               # first too, so the two halves of a match merge in order.
               IndexModel([("terms_at", 1), ("first_seen_at", DESCENDING), ("_id", DESCENDING)],
                          name="terms_at_newest"))

    id: int | None = None
    url: str
    canonical_url: str | None = None
    content_key: str | None = None
    application_url: str | None = None
    application_canonical_url: str | None = None
    source: str
    title: str
    company: str | None = None
    location: str | None = None
    country: str | None = None
    # The city it is in, as a picked place names it (`libs.corpus.geo.CITIES`), or None.
    city: str | None = None
    is_remote: bool | None = None
    language: str | None = None
    description: str | None = None
    description_fetched_at: datetime | None = None
    # When it first had a whole description: a job is a whole job from then, and alerts wait for it.
    described_at: datetime | None = None
    # Which of everyone's green words it has, anywhere and in its title (`libs.corpus.terms`), and
    # when they were last found: None until they are, and again whenever its text changes.
    terms: list[str] = field(default_factory=list)
    title_terms: list[str] = field(default_factory=list)
    terms_at: datetime | None = None
    discovered_via: str | None = None
    describe_failures: int = 0
    applicants: int | None = None
    applicants_caption: str | None = None
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None
    closed_at: datetime | None = None
    posted_at: datetime | None = None
    fetched_at: datetime | None = None
    first_seen_at: datetime = field(default_factory=now)
    schema_version: int = 1


@dataclass(kw_only=True)
class CountryCount(Document):
    """How many new postings arrived in one hour in one country (`country`; None when it cannot be
    told): the admin's chart of what is found where (`CorpusRepository.add`). Kept when the postings
    themselves are archived, so the chart reaches back further than the corpus does."""

    COLLECTION = "country_counts"
    OWNER = None
    INDEXES = (IndexModel("hour", name="hour"),)

    id: str  # the hour and the country
    hour: datetime
    country: str | None = None
    jobs: int = 0


@dataclass(kw_only=True)
class SweptPlace(Document):
    """When the LinkedIn pass last read a place's new jobs to the end (`services.harvester.linkedin`), so
    the next sweep of it reaches back to there."""

    COLLECTION = "swept_places"
    OWNER = None
    BACKED_UP = False

    id: str  # LinkedIn's name for the place
    swept_at: datetime


@dataclass(kw_only=True)
class Word(Document):
    """A green word the postings have been searched for, so each one says whether it has it. Every
    account's words, without their accounts: what is searched for, not who searches."""

    COLLECTION = "vocabulary"
    OWNER = None
    BACKED_UP = False

    id: str  # the word, as words are compared (`libs.matching.norm`)
    # When every posting that can still be shown had been searched for it; None while that search is under
    # way, a stretch each harvest run (`libs.corpus.terms`), and until then it is matched the slow way.
    indexed_at: datetime | None = field(default_factory=now)
    # How far back, by when the postings were found, the search has reached: it goes on from here.
    searched_to: datetime | None = None
