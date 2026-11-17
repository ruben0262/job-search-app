"""A person's alerts: what each one follows and where it sends what it finds (`libs.alerts`)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

# Where an alert can send a job: the bell on the site, this person's browsers and phones (Web Push), their
# Telegram chat, and the daily email, which lists what each alert with it found (owner, 2026-11-17: email
# stays one a day, grouped by alert, or the address is flagged).
CHANNELS = ("bell", "push", "telegram", "email")


@dataclass(kw_only=True)
class Alert(Document):
    """One named alert. Every field it is given must hold for a job, and any one of a field's values will
    do: "LEGO" and "Denmark" is LEGO's jobs in Denmark. A field left empty asks nothing."""

    COLLECTION = "alerts"
    SEQUENCE = True
    CHOICES = {"min_fit": (*range(1, 11), None)}  # noqa: RUF012
    INDEXES = (IndexModel("user_id", name="by_user"),)

    id: int | None = None
    user_id: uuid.UUID
    name: str
    # Only the jobs their Jobs list has: My search's words, red words, places, ways of working, languages
    # and salary (`libs.matching`). The alert every account starts with is this and nothing more.
    search: bool = False
    # Words in the title or the text, as My search's are matched (`libs.corpus.words`).
    keywords: list[str] = field(default_factory=list)
    # Whole words of the employer's name: "LEGO" is "The LEGO Group" too.
    companies: list[str] = field(default_factory=list)
    # Countries and cities, by the names My search uses (`libs.corpus.geo`).
    places: list[str] = field(default_factory=list)
    # Job sites, by the corpus's own names (`CorpusJob.source`).
    sources: list[str] = field(default_factory=list)
    # The lowest match score sent, as a card shows it (`libs.fit.shown`). None: any.
    min_fit: int | None = None
    channels: list[str] = field(default_factory=lambda: ["bell", "push", "telegram"])
    on: bool = True
    # Alerts announce what is new: a job reaches this alert only if it joined the person's list after this.
    created_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class AlertCursor(Document):
    """How far the alerts have read, per wait (`libs.alerts.route`): the newest `described_at` already
    looked at for the accounts whose jobs reach them `id` hours after that. Nothing else keeps it, so a
    stopped notifier carries on from here rather than from the moment it starts again."""

    COLLECTION = "alert_cursors"
    OWNER = None
    BACKED_UP = False

    id: str
    at: datetime
