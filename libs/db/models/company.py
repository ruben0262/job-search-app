"""What a company says of itself, kept once a company for the writer of a cover letter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from libs.db import Document, now


@dataclass(kw_only=True)
class Company(Document):
    """A company's own words about itself, gathered from the postings it has put up
    (`libs.corpus.companies.about`) the first time somebody makes a letter for it, and read from here after:
    a letter says why this employer from something true about it, and no letter pays to learn it twice.
    Nobody's name is in it, and it is gathered again from the corpus if lost."""

    COLLECTION = "companies"
    OWNER = None
    BACKED_UP = False

    # The company's name, case and spacing aside.
    id: str
    name: str
    # What it says of itself, in its postings' words; empty when its postings say nothing.
    about: str = ""
    # How many of its postings were read for it.
    postings: int = 0
    read_at: datetime = field(default_factory=now)
