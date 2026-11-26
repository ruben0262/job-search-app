"""The site's pages as a search engine reads them (`services.api.page_routes`, `libs.pages`): one file
for every address, told in its head which page it is, and the page as drawn for a client that does not
run the site's code. Before 2026-11-25 every address carried the title "Mahoraga", no canonical address
and no word on indexing, and bingbot was given a 404 for every page but the first (SEO audit)."""
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import struct
from urllib.parse import urlparse

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from libs import pages, settings
from libs.legal import DOCUMENTS, LEGAL_VERSION
from libs.notify import site_url

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
# A page as the browser drew it, of the deploy whose file names this code.
CODE = '<script type="module" crossorigin src="/assets/index-abc123.js"></script>'


@pytest.fixture()
def shell(tmp_path, monkeypatch):
    """The site's own `index.html`, as a deploy serves it: the code it names is Vite's file of that deploy."""
    # Loaded first, so the app mounts no test folder for the tests after this one.
    import services.api.app  # noqa: F401

    source = (WEB / "index.html").read_text(encoding="utf-8")
    built = re.sub(r'<script type="module" src="[^"]+"></script>', "", source)
    built = built.replace("</head>", CODE + "</head>")
    (tmp_path / "index.html").write_text(built, encoding="utf-8")
    # As a build has it: `web/public` copied beside the page.
    (tmp_path / "og-image.png").write_bytes((WEB / "public" / "og-image.png").read_bytes())
    monkeypatch.setenv("WEB_DIR", str(tmp_path))
    return built


@pytest.fixture()
def http(db, shell, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from services.api.app import app

    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _head(answer) -> str:
    return answer.text.split("</head>", 1)[0]


def test_each_public_page_says_which_it_is_and_every_other_address_is_for_no_index(http):
    home, terms = http.get("/", headers={"accept": "*/*"}), http.get("/terms", headers={"accept": "*/*"})
    assert home.status_code == terms.status_code == 200
    assert "<title>Mahoraga</title>" in home.text, "the landing page keeps the owner's title"
    assert "<title>Terms of Service · Mahoraga</title>" in terms.text
    assert f'<link rel="canonical" href="{site_url("/")}" />' in _head(home)
    assert f'<link rel="canonical" href="{site_url("/terms")}" />' in _head(terms)
    assert f'<meta property="og:image" content="{site_url("/og-image.png")}" />' in _head(terms)
    [described] = re.findall(r'<meta name="description" content="([^"]*)"', _head(terms))
    assert described not in _head(home), "a legal page says what it is itself, not what the landing page is"
    for answer in (home, terms):
        assert "x-robots-tag" not in answer.headers

    for elsewhere in ("/jobs", "/applied", "/wp-admin", "/terms/"):
        answer = http.get(elsewhere, headers={"accept": "text/html"})
        assert answer.status_code == 200, "a page of the site, drawn by its code, as before"
        assert answer.headers["x-robots-tag"] == "noindex", elsewhere
        assert 'rel="canonical"' not in answer.text, elsewhere


def test_the_landing_page_says_who_the_site_is_with_the_profiles_its_footer_links(http, db):
    settings.put(db, "socials", {"discord": "https://discord.gg/abc", "facebook": "", "tiktok": "",
                                 "instagram": "https://www.instagram.com/example/"})

    head = _head(http.get("/"))
    found = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.S).group(1))
    organization = next(node for node in found["@graph"] if node["@type"] == "Organization")
    assert organization["sameAs"] == [link for link in settings.social_links(db).values() if link]
    assert {"https://discord.gg/abc", "https://www.instagram.com/example/"} <= set(organization["sameAs"])
    assert organization["url"] == site_url("/") and organization["name"] == "Mahoraga"
    website = next(node for node in found["@graph"] if node["@type"] == "WebSite")
    assert website["alternateName"] == urlparse(site_url("/")).hostname, "the site's own address"


def test_a_crawler_is_given_the_page_as_drawn_and_a_browser_the_file(http, db, shell):
    heading = '<div id="root"><main><h1>Terms of Service</h1></main></div>'
    drawn = shell.replace('<div id="root"></div>', heading)
    pages.keep(db, "/terms", drawn)

    assert "<h1>Terms of Service</h1>" in http.get("/terms", headers={"accept": "*/*"}).text
    browser = http.get("/terms", headers={"accept": "text/html", "sec-fetch-dest": "document"})
    assert "<h1>" not in browser.text, "a browser is given the file its code draws, as before"
    assert "<title>Terms of Service · Mahoraga</title>" in browser.text

    pages.keep(db, "/terms", drawn.replace("index-abc123.js", "index-gone999.js"))
    assert "<h1>" not in http.get("/terms", headers={"accept": "*/*"}).text, \
        "a copy drawn by an older deploy names code that is gone: never served"


def test_robots_and_the_sitemap_name_the_public_pages_at_the_sites_address(http):
    robots = http.get("/robots.txt")
    assert robots.headers["content-type"].startswith("text/plain")
    assert f"Sitemap: {site_url('/sitemap.xml')}" in robots.text
    assert "Disallow" not in robots.text, "a page for no index says so itself; a Disallow would hide it"

    sitemap = http.get("/sitemap.xml").text
    listed = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert listed == [site_url(path) for path in pages.PUBLIC]
    assert site_url("/terms") in listed and len(listed) == 5
    dated = dict(re.findall(r"<loc>([^<]+)</loc><lastmod>([^<]+)</lastmod>", sitemap))
    assert dated == {site_url(f"/{name}"): LEGAL_VERSION for name in DOCUMENTS}, \
        "a legal page dated the day its version changed; the landing page, which has no such day, by nothing"


def test_each_legal_page_is_described_in_its_own_short_version(http, db):
    """A link to a legal page, or a result, showed its title and nothing else: no description (2026-11-25).
    Each now says what it is in its own words, the start of its short version, as much as a result shows
    whole, and the same in its link preview."""
    from libs.legal.documents import render
    from services.api.page_routes import DESCRIBED_IN

    said = {}
    for name in DOCUMENTS:
        head = _head(http.get(f"/{name}", headers={"accept": "*/*"}))
        [described] = re.findall(r'<meta name="description" content="([^"]*)"', head)
        [previewed] = re.findall(r'<meta property="og:description" content="([^"]*)"', head)
        short = " ".join(re.findall(r"^> - (.+)$", render(db, name)["markdown"], re.M))
        said[name] = html.unescape(described)
        assert said[name] and short.startswith(said[name]), f"{name}: its own words, from the top"
        assert len(said[name]) <= DESCRIBED_IN, f"{name}: a result shows it whole"
        assert previewed == described, f"{name}: a link to it says the same"
    assert len(set(said.values())) == len(DOCUMENTS), "each page its own"


def test_a_crawler_given_the_empty_file_is_said_once_a_page(http, db, caplog, monkeypatch):
    """With no copy of this deploy (after a deploy, or when docgen cannot reach the site) a crawler gets the
    empty file, and nothing said so (SEO re-audit, 2026-11-25). Said once a page and deploy, not each time."""
    from services.api import page_routes

    monkeypatch.setattr(page_routes, "_UNDRAWN", set())
    with caplog.at_level("WARNING", logger="services.api.page_routes"):
        for _ in range(3):
            http.get("/terms", headers={"accept": "*/*"})
        http.get("/privacy", headers={"accept": "*/*"})
    said = [record.getMessage() for record in caplog.records if "no copy of" in record.getMessage()]
    assert len(said) == 2 and "/terms" in said[0] and "/privacy" in said[1]


def test_every_public_address_answers_head_as_it_answers_get(http):
    """A link checker or an uptime monitor asks with HEAD, and every page but `/` was a JSON 404 to it; the
    sitemap still was on 2026-11-26, while Search Console said it "couldn't fetch" it."""
    for path in ("/", "/terms", "/jobs"):
        answer = http.head(path, headers={"accept": "*/*"})
        assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/html"), path
    for path, kind in (("/robots.txt", "text/plain"), ("/sitemap.xml", "application/xml")):
        answer = http.head(path)
        assert answer.status_code == 200 and answer.headers["content-type"].startswith(kind), path
    assert http.head("/.well-known/api-catalog").status_code == 404, "machines' addresses are never a page"


def test_a_link_to_a_public_page_shows_its_picture_whole_and_large(http):
    """A previewer told the picture's size draws the card the first time a link is shared; and a search
    engine may show the picture large only when the page says it may."""
    for path in ("/", "/terms"):
        head = _head(http.get(path, headers={"accept": "*/*"}))
        assert '<meta name="robots" content="max-image-preview:large" />' in head, path
        assert '<meta property="og:image:width" content="1200" />' in head, path
        assert '<meta property="og:image:height" content="630" />' in head, path
        assert '<meta property="og:image:alt" content="Mahoraga" />' in head, path


def test_every_file_the_pages_name_is_one_of_the_sites():
    """`page_routes` names a picture for link previews and the brand's mark, and `index.html` its icons. A
    name with no file is a preview with no picture, or a link unfurled without its icon, and nothing would
    say so: `/favicon.ico` was a 404 (2026-11-25)."""
    from services.api.page_routes import LOGO, PREVIEW

    shell = (WEB / "index.html").read_text(encoding="utf-8")
    named = {PREVIEW, LOGO, *re.findall(r'(?:href|src)="(/[^"]+)"', shell)}
    missing = [path for path in sorted(named)
               if not path.startswith("/src/") and not (WEB / "public" / path.lstrip("/")).is_file()]
    assert not missing, f"named, and not a file of the site: {missing}"
    assert "/favicon.ico" in named
    preview = (WEB / "public" / PREVIEW.lstrip("/")).read_bytes()
    assert struct.unpack(">II", preview[16:24]) == (1200, 630), "the size link previews draw at full width"
