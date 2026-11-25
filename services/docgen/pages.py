"""The site's public pages, drawn for the clients that do not run its code (`libs.pages`): bingbot, the AI
search crawlers, link previews. This worker has the platform's one browser (`pdf.Chrome`), so it draws them,
one page in each pass of its sweep (`run_worker`'s `periodic`, once a minute) and between documents: a CV
waits for one page at most, about a second."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import httpx
from pymongo.database import Database

from libs import pages
from libs.db import find, now
from libs.db.models import DrawnPage
from libs.settings import PLATFORM_TICK_MIN
from services.docgen import pdf

logger = logging.getLogger(__name__)

# Where this worker reaches the site from inside: the API's address on the compose network. Unset, as on a
# developer's machine, nothing is drawn, and the API sends every client the file the site's code draws.
INSIDE = os.environ.get("SITE_INSIDE_URL", "").rstrip("/")


def draw_due(db: Database) -> str | None:
    """Draw the public page most in need of it: one never drawn, or drawn by a deploy the site no longer
    serves, first; then one older than a platform tick, the time a changed price or profile link may take
    to reach a crawler. The address drawn, or None when every copy is current."""
    if not INSIDE:
        return None
    shell = httpx.get(f"{INSIDE}/", headers={"accept": "text/html", "sec-fetch-dest": "document"}, timeout=10)
    current = pages.code(shell.raise_for_status().text)
    kept = {page.id: page for page in find(db, DrawnPage, {})}
    stale = now() - timedelta(minutes=PLATFORM_TICK_MIN)
    due, new = [], set()
    for path in pages.PUBLIC:
        page = kept.get(path)
        if page is None or pages.code(page.html) != current:
            due.insert(0, path)
            new.add(path)
        elif page.drawn_at < stale:
            due.append(path)
    if not due:
        return None
    path = due[0]
    html = pdf.draw(f"{INSIDE}{path}", path)
    if "<h1" not in html or pages.code(html) != current:
        logger.warning("pages: %s drew no heading, or for another deploy; its last copy stays", path)
        return None
    pages.keep(db, path, html)
    # A deploy's first copies are news; the same page drawn again a tick later is not.
    (logger.info if path in new else logger.debug)("pages: drew %s (%d characters)", path, len(html))
    return path
