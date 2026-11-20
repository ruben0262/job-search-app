"""What every request passes on its way in and every answer on its way out (`services.api.guard`)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from services.api.guard import BODY_LIMITS, MAX_BODY_BYTES, Guard


@pytest.fixture()
def http(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from services.api.app import app

    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_every_answer_tells_the_browser_what_to_hold_the_site_to(http):
    """A page, a refusal and a page that is not there alike. No answer said any of it: the site could be
    drawn inside somebody else's page, and a file's type guessed (2026-11-20)."""
    answers = (http.get("/health"), http.get("/account"), http.get("/no-such-page"),
               http.post("/auth/login", json={}))
    assert [answer.status_code for answer in answers] == [200, 401, 404, 422], "the premise: four kinds"
    for answer in answers:
        said = answer.headers
        assert said["x-content-type-options"] == "nosniff"
        assert said["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in said["content-security-policy"]
        assert said["strict-transport-security"].startswith("max-age=")
        assert said["referrer-policy"] == "strict-origin-when-cross-origin"
        assert said["cache-control"] == "no-store", "somebody's data, or an error about it, is kept nowhere"


def test_a_body_larger_than_the_site_takes_is_refused(http):
    """By the length it states, before any of it is read; and one that states none, as it arrives. A body of
    any size was read into memory before anybody was asked who they were (2026-11-20)."""
    too_much = b"x" * (MAX_BODY_BYTES + 1)
    json = {"content-type": "application/json"}

    stated = http.post("/auth/login", content=too_much, headers=json)
    in_pieces = http.post("/auth/login", content=iter([too_much[:1000], too_much[1000:]]), headers=json)

    assert (stated.status_code, in_pieces.status_code) == (413, 413)
    assert "MB" in stated.json()["detail"] and stated.headers["x-frame-options"] == "DENY"
    # What fits is read: a body of the size allowed is refused for what it says, not for its size.
    assert http.post("/auth/login", content=b"x" * MAX_BODY_BYTES, headers=json).status_code == 422


def test_an_upload_may_be_as_large_as_a_file_may(http):
    """Its own limit, a file's and the form round it, not the limit on everything else: refused for not being
    signed in, as any upload from nobody is."""
    assert BODY_LIMITS["/documents"] > MAX_BODY_BYTES
    form = {"file": ("cv.txt", b"x" * (MAX_BODY_BYTES + 1), "text/plain")}

    assert http.post("/documents", data={"kind": "cv"}, files=form).status_code == 401


def test_the_list_of_every_route_is_not_served(http):
    """`/openapi.json` named every route there is, the admin's among them, to anybody."""
    assert http.get("/openapi.json").status_code == 404


def test_the_sites_files_say_how_long_they_may_be_kept(tmp_path):
    """A file Vite named by its content is the same for good; the page that names those files is asked for
    again each time, or a browser goes on using one whose files a deploy has removed. Neither said."""
    from fastapi.staticfiles import StaticFiles
    from starlette.applications import Starlette
    from starlette.routing import Mount

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    (tmp_path / "sw.js").write_text("// worker")
    files = Starlette(routes=[Mount("/", StaticFiles(directory=tmp_path, html=True))])
    site = TestClient(Guard(files), base_url="https://testserver")

    kept_for_good = site.get("/assets/index-abc123.js").headers["cache-control"]
    assert kept_for_good == "public, max-age=31536000, immutable"
    assert site.get("/").headers["cache-control"] == "no-cache"
    assert site.get("/sw.js").headers["cache-control"] == "no-cache"
    assert "immutable" not in site.get("/assets/gone.js").headers.get("cache-control", "")


def _allowed(directive: str) -> list[str]:
    from services.api.guard import POLICY

    [found] = [part.split()[1:] for part in POLICY.split("; ") if part.split()[0] == directive]
    return found


def test_a_page_runs_no_script_that_is_not_a_file_of_the_site_or_stripes():
    """What makes somebody else's text on a page unable to run as a script: nothing written into a page
    runs, and nothing is built from a string. So the page itself writes none: its one script that was, the
    theme's, is a file now (`web/public/theme.js`). An inline script added to `index.html` would be refused
    by every browser, silently, in production alone; it fails here first."""
    import pathlib
    import re

    scripts = _allowed("script-src")
    assert "'self'" in scripts
    assert not {"'unsafe-inline'", "'unsafe-eval'", "*", "https:", "data:"} & set(scripts)

    shell = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text()
    written_in = [tag for tag in re.findall(r"<script\b[^>]*>", shell) if "src=" not in tag]
    assert not written_in, f"index.html writes a script into the page: {written_in}"
    assert not re.search(r"\son\w+\s*=", shell), "index.html has a handler written into a tag"


def test_the_payment_form_is_allowed_everything_stripe_says_it_needs():
    """The origins Stripe lists for Stripe.js, Checkout and Link (docs.stripe.com/security/guide, read
    2026-11-20), each under the directive it lists it for. One left out stops the payment form, and only for
    somebody about to pay."""
    needs = {
        "script-src": ["https://js.stripe.com", "https://*.js.stripe.com", "https://checkout.stripe.com"],
        "frame-src": ["https://js.stripe.com", "https://*.js.stripe.com", "https://hooks.stripe.com",
                      "https://checkout.stripe.com", "https://link.com", "https://*.link.com"],
        "connect-src": ["https://api.stripe.com", "https://checkout.stripe.com", "https://link.com",
                        "https://*.link.com"],
        "img-src": ["https://*.stripe.com", "https://*.link.com"],
    }
    for directive, origins in needs.items():
        assert not [origin for origin in origins if origin not in _allowed(directive)], directive
    assert _allowed("frame-ancestors") == ["'none'"] and _allowed("object-src") == ["'none'"]

