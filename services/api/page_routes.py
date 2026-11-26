"""The site's pages. The site is one file, `index.html`, that its own code draws into the page an address
names (`web/src/App.tsx`), and every page address is sent here (`guard.page_asked`). What the file cannot
know is the address it was asked at, so this says it in the head, where a search engine reads it without
running anything: the page's title, the address to keep for it (`canonical`), whether it is for an index at
all, and what a link to it shows (Open Graph). The landing page also says who the site is (JSON-LD), with
the profiles its footer links (`settings.social_links`), which is how an engine tells this Mahoraga from
the others. All of it was missing: one title, "Mahoraga", on every page, and every address a copy of the
landing page (SEO audit, 2026-11-25).

A client that does not run the site's code is given the page as a browser drew it (`libs.pages`), when
there is a copy from this deploy. A browser going to a page says it is one (`Sec-Fetch-Dest: document`)
and is given the file its code draws, as before.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import struct
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pymongo.database import Database
from pymongo.errors import PyMongoError

from libs import pages, settings
from libs.legal import DOCUMENTS, LEGAL_VERSION
from libs.legal.documents import render
from libs.notify import site_url
from services.api.deps import get_db

logger = logging.getLogger(__name__)

# What a link to the site shows, and the brand's square mark: `branding/build.py` writes both there.
PREVIEW, LOGO = "/og-image.png", "/icon-512.png"

# About how much of a description a search result shows before it cuts it off.
DESCRIBED_IN = 160

_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_DESCRIPTION = re.compile(r'\s*<meta\s+name="description"\s+content="([^"]*)"\s*/?>', re.S)
# A legal page's "short version", the lines at its head that say it in plain words.
_SHORT = re.compile(r"^> - (.+)$", re.M)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

router = APIRouter()


def web() -> Path:
    """The built site, `/app/web` in the image (`app.py` mounts its files). Read when asked, so an app
    reloaded with another `WEB_DIR`, as a test does, serves the site it was given."""
    return Path(os.environ.get("WEB_DIR", "/app/web"))


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def page(request: Request, db: Database = Depends(get_db)) -> Response:
    """The page at the address the guard was asked for (`request.state.page`), `/` when it was `/`."""
    path = getattr(request.state, "page", "/")
    try:
        shell = (web() / "index.html").read_text(encoding="utf-8")
    except FileNotFoundError:
        # No web build: the API's own routes work, and `app.py` said so when it started.
        raise HTTPException(status_code=404, detail="Not Found") from None
    if path not in pages.PUBLIC:
        # Somebody's own page, or none: drawn by the site's code as before, and for no index.
        return HTMLResponse(shell, headers={"x-robots-tag": "noindex"})
    if request.headers.get("sec-fetch-dest") != "document" and (copy := _drawn(db, path, shell)):
        return HTMLResponse(copy)
    return HTMLResponse(_head(db, shell, path))


@router.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots() -> PlainTextResponse:
    """Every address may be read; the ones for no index say so themselves, which a `Disallow` would hide."""
    return PlainTextResponse(f"User-agent: *\nAllow: /\n\nSitemap: {site_url('/sitemap.xml')}\n")


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap() -> Response:
    """The public pages, at the address the site is reached at. A legal page dated by its version, the day it
    last changed; the landing page by nothing, since a guessed date teaches an engine to ignore them all."""
    dated = {path: f"<lastmod>{LEGAL_VERSION}</lastmod>" for path in pages.PUBLIC if path != "/"}
    listed = "".join(f"<url><loc>{html.escape(site_url(path))}</loc>{dated.get(path, '')}</url>"
                     for path in pages.PUBLIC)
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{listed}</urlset>\n',
                    media_type="application/xml")


# The pages a crawler was given the empty file for, as there was no copy of this deploy: said once each.
_UNDRAWN: set[tuple[str, str | None]] = set()


def _drawn(db: Database, path: str, shell: str) -> str | None:
    try:
        copy = pages.drawn(db, path, shell)
    except PyMongoError:
        logger.warning("pages: could not read the drawn copy of %s; sending the file its code draws", path,
                       exc_info=True)
        return None
    if copy is None and (path, pages.code(shell)) not in _UNDRAWN:
        # Minutes after a deploy, until docgen draws it; for good if docgen cannot reach the site.
        _UNDRAWN.add((path, pages.code(shell)))
        logger.warning("pages: no copy of %s drawn by this deploy yet; a crawler got the empty file", path)
    return copy


def _head(db: Database, shell: str, path: str) -> str:
    """`index.html` saying which public page it is. The brand is the file's own title. The landing page's
    description is the file's own; a legal page's is the start of its "short version", in its own words."""
    brand = _TITLE.search(shell).group(1)
    name = path.strip("/")
    title = f"{DOCUMENTS[name]} · {brand}" if name else brand
    if name:
        description = _short_version(db, name)
        own = f'\n    <meta name="description" content="{html.escape(description)}" />' if description else ""
        shell = _DESCRIPTION.sub(lambda _: own, shell, count=1)
        shell = shell.replace(f"<title>{brand}</title>", f"<title>{title}</title>", 1)
    else:
        described = _DESCRIPTION.search(shell)
        description = html.unescape(described.group(1)) if described else None
    said = {"og:type": "website", "og:site_name": brand, "og:title": title, "og:url": site_url(path),
            **({"og:description": description} if description else {}), **_preview(brand)}
    tags = [f'<link rel="canonical" href="{html.escape(site_url(path))}" />',
            # A search engine may show the page's picture large, in results and Discover.
            '<meta name="robots" content="max-image-preview:large" />',
            *(f'<meta property="{key}" content="{html.escape(str(said[key]))}" />' for key in said),
            '<meta name="twitter:card" content="summary_large_image" />']
    if not name:
        tags.append(_who(db, brand))
    return shell.replace("</head>", "\n".join(tags) + "\n</head>", 1)


def _short_version(db: Database, name: str) -> str | None:
    """As many lines of a legal page's short version as a search result shows whole, the first always."""
    try:
        lines = _SHORT.findall(render(db, name)["markdown"])
    except PyMongoError:
        logger.warning("pages: could not read the %s page; it goes out without a description", name,
                       exc_info=True)
        return None
    said = ""
    for line in lines:
        plain = _MARKDOWN_LINK.sub(r"\1", line).replace("**", "").strip()
        if said and len(said) + 1 + len(plain) > DESCRIBED_IN:
            break
        said = f"{said} {plain}".strip()
    return said or None


def _preview(brand: str) -> dict[str, object]:
    """The picture a link shows, with its size read from the file itself: a link previewer that knows the
    size draws the card at once, the first time the link is shared, instead of after fetching it."""
    said: dict[str, object] = {"og:image": site_url(PREVIEW), "og:image:alt": brand}
    try:
        header = (web() / PREVIEW.lstrip("/")).read_bytes()[:24]
    except OSError:
        logger.warning("pages: %s is not in the web build; link previews show no picture", PREVIEW)
        return {}
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", header[16:24])
        said |= {"og:image:type": "image/png", "og:image:width": width, "og:image:height": height}
    return said


def _who(db: Database, brand: str) -> str:
    """Who the site is, for an engine: its name, address and mark, and the profiles the footer links."""
    try:
        profiles = [link for link in settings.social_links(db).values() if link]
    except PyMongoError:
        logger.warning("pages: could not read the social links; the landing page names none", exc_info=True)
        profiles = []
    home = site_url("/")
    graph = [{"@type": "Organization", "@id": f"{home}#organization", "name": brand, "url": home,
              "logo": site_url(LOGO), **({"sameAs": profiles} if profiles else {})},
             # The address as a second name: what Google may call the site when "Mahoraga" is somebody else's.
             {"@type": "WebSite", "@id": f"{home}#website", "name": brand,
              "alternateName": urlparse(home).hostname, "url": home,
              "publisher": {"@id": f"{home}#organization"}}]
    # `</` closed the script early in a link that held it.
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}).replace("</", "<\\/")
    return f'<script type="application/ld+json">{data}</script>'
