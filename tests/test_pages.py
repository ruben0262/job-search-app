"""The site's pages as a search engine reads them (`services.api.page_routes`, `libs.pages`): one file
for every address, told in its head which page it is, and the page as drawn for a client that does not
run the site's code. Before 2026-11-25 every address carried the title "Mahoraga", no canonical address
and no word on indexing, and bingbot was given a 404 for every page but the first (SEO audit)."""
from __future__ import annotations

import json
import os
import pathlib
import re
import struct

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from libs import pages, settings
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
    assert 'name="description"' in _head(home) and 'name="description"' not in _head(terms), \
        "a legal page leaves the landing page's description out, so an engine quotes the page itself"
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

    listed = re.findall(r"<loc>([^<]+)</loc>", http.get("/sitemap.xml").text)
    assert listed == [site_url(path) for path in pages.PUBLIC]
    assert site_url("/terms") in listed and len(listed) == 5


def test_the_files_the_pages_name_are_the_sites_own():
    """`page_routes` names a picture for link previews and the brand's mark; a name with no file is a
    preview with no picture, and nothing would say so."""
    from services.api.page_routes import LOGO, PREVIEW

    preview = (WEB / "public" / PREVIEW.lstrip("/")).read_bytes()
    assert struct.unpack(">II", preview[16:24]) == (1200, 630), "the size link previews draw at full width"
    assert (WEB / "public" / LOGO.lstrip("/")).is_file()
