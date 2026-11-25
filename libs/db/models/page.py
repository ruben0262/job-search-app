"""A public page of the site as a browser draws it, for the clients that do not run the site's code
(`libs.pages`)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from libs.db import Document


@dataclass(kw_only=True)
class DrawnPage(Document):
    COLLECTION = "drawn_pages"
    OWNER = None
    # Drawn again within minutes of being lost, from the site itself.
    BACKED_UP = False

    id: str  # the page's address: `/`, `/terms`
    # The whole document once the site's code has drawn it, from `<!doctype html>`.
    html: str
    drawn_at: datetime
