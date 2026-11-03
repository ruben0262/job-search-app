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
               # The jobs page and the alerts: newest first, open postings only.
               IndexModel([("first_seen_at", DESCENDING)], partialFilterExpression={"closed_at": None},
                          name="open_newest"),
               IndexModel("describe_failures", partialFilterExpression={"describe_failures": {"$gt": 0}},
                          name="describe_failures"),
               # A person's jobs: the postings that have one of their words (`libs.corpus.terms`).
               IndexModel("terms", partialFilterExpression={"closed_at": None}, name="open_terms"),
               # The postings whose words are still to be found, which matching reads the slow way.
               IndexModel("terms_at", name="terms_at"))

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
class Word(Document):
    """A green word the postings have been searched for, so each one says whether it has it. Every
    account's words, without their accounts: what is searched for, not who searches."""

    COLLECTION = "vocabulary"
    OWNER = None
    BACKED_UP = False

    id: str  # the word, as words are compared (`libs.matching.norm`)
    indexed_at: datetime = field(default_factory=now)
