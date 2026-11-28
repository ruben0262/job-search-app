"""What a company says of itself, kept once a company for the writer of a cover letter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from libs.db import Document, now


@dataclass(kw_only=True)
class Company(Document):
    """A company's own words about itself, gathered the first time somebody makes a letter for it, from the
    postings it has put up (`libs.corpus.companies.about`) or, where they say nothing, from its own website
    by the writer of that letter (`learned`), and read from here after, for good (owner, 2026-11-28: "keep
    company data always ... we don't need to update it"): no letter pays to learn it twice. Nobody's name is
    in it. Backed up, since what was read from a website cost somebody's call to learn."""

    COLLECTION = "companies"
    OWNER = None

    # The company's name, case and spacing aside.
    id: str
    name: str
    # What it says of itself, in its postings' or its website's words; empty when nothing was found.
    about: str = ""
    # How many of its postings were read for it.
    postings: int = 0
    read_at: datetime = field(default_factory=now)
    # The page of its own website `about` was read from; empty when it came from its postings.
    source: str = ""
    # Whether its website has been looked for: once, whatever was found, so that no other letter asks again.
    searched: bool = False
