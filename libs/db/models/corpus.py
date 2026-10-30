"""The shared job corpus — the one collection that is not per-user, and must never become per-user."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pymongo import DESCENDING, IndexModel

from libs.db import Document, now

WORKPLACES = ("remote", "hybrid", "onsite", None)


@dataclass(kw_only=True)
class CorpusJob(Document):
    """One public posting. Its integer id is what the site, the alerts and Telegram's buttons carry."""

    COLLECTION = "corpus"
    BACKED_UP = False
    OWNER = None
    SEQUENCE = True
    CHOICES = {"workplace": WORKPLACES}  # noqa: RUF012
    INDEXES = (IndexModel("url", unique=True, name="url_unique"),
               IndexModel("canonical_url", name="canonical_url"),
               IndexModel("content_key", name="content_key"),
               IndexModel("application_canonical_url", name="application_canonical_url"),
               IndexModel([("source", 1), ("first_seen_at", 1)], name="source_first_seen"),
               # The jobs page and the alerts: newest first, open postings only.
               IndexModel([("first_seen_at", DESCENDING)], partialFilterExpression={"closed_at": None},
                          name="open_newest"),
               IndexModel("describe_failures", partialFilterExpression={"describe_failures": {"$gt": 0}},
                          name="describe_failures"))

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
    is_remote: bool | None = None
    language: str | None = None
    description: str | None = None
    description_fetched_at: datetime | None = None
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
