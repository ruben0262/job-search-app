"""The site's public pages, and each one as a browser draws it.

The site is one file that its own code draws in the browser (`web/index.html`), so a client that does not
run that code read the same thing at every address: the word "Mahoraga" and one line under it. That is
bingbot most of the time, the AI search crawlers, and every link preview (SEO audit, 2026-11-25). The
docgen worker, the one process with a browser (`services.docgen.pages`), draws each public page as a
visitor sees it and keeps the copy here; the API gives the copy to the clients that do not draw pages
themselves (`services.api.page_routes`).
"""
from __future__ import annotations

import re

from pymongo.database import Database

from libs.db import get, now
from libs.db.models import DrawnPage
from libs.legal import DOCUMENTS

# The pages anybody may read and a search engine may list: the landing page and the legal pages. Every
# other address is somebody's own page, or none, and is said to be for no index.
PUBLIC = ("/", *(f"/{name}" for name in DOCUMENTS))

# The site's code, as a page names it. Vite names the file by what is in it, so it says which deploy a
# page was drawn by.
_CODE = re.compile(r'<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]+)"')


def code(html: str) -> str | None:
    """The site's code this page runs: the deploy it belongs to."""
    found = _CODE.search(html)
    return found.group(1) if found else None


def drawn(db: Database, path: str, shell: str) -> str | None:
    """The page as a browser drew it, while it belongs to the deploy serving `shell`. A copy from an older
    deploy names code that deploy removed, and would draw nothing for a person who opened it."""
    kept = get(db, DrawnPage, path)
    return kept.html if kept is not None and code(kept.html) == code(shell) else None


def keep(db: Database, path: str, html: str) -> None:
    """Keep a page as drawn, in place of the last copy of it."""
    page = DrawnPage(id=path, html=html, drawn_at=now())
    db[DrawnPage.COLLECTION].replace_one({"_id": path}, page.to_doc(), upsert=True)
