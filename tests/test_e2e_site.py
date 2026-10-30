"""A new person, end to end, on the real built site: sign up, open the emailed link, the three
onboarding screens, their jobs with why each one is there, and a CV that downloads as a PDF.

Headless Chromium drives `web/dist`, served by the real API on a real MongoDB, with the real docgen
worker beside it rendering through the real Chrome. Only the outside world is faked: the email is
printed instead of sent, and nothing here can reach Telegram or an AI — an instant CV needs neither.

And the same build with no API behind it, which needs no database: the first screen anybody sees.
"""
from __future__ import annotations

import os
import pathlib
import re
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

import pytest

DIST = pathlib.Path(__file__).resolve().parents[1] / "web" / "dist"
CHROME = os.environ.get("CHROME_BIN", "")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(CHROME) and (DIST / "index.html").exists()),
    reason="needs CHROME_BIN and the built site (npm --prefix web run build)")
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

# Two jobs their CV's title matches, and one it does not.
TITLES = ("Backend Engineer", "Senior Backend Engineer", "Registered Nurse")
CV = """Jordan Ellery
jordan.ellery@example.com | Berlin, Germany | linkedin.com/in/jordanellery

EXPERIENCE
Backend Engineer, Northwind    2021 - Present
- Built the billing service in Python and Postgres
- Cut checkout latency to 40ms at p99

Software Engineer, Contoso    2018 - 2021
- Moved the payments API to Go

LANGUAGES
English (native), German (B2)
"""
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


@pytest.fixture()
def built() -> pathlib.Path:
    """`web/dist`, refused when older than its sources. A walk of an old build tests code that is not
    there, and CLAUDE.md §7 runs pytest before the build: so that fails here, loudly."""
    web, built_at = DIST.parent, (DIST / "index.html").stat().st_mtime
    newer = [path for path in (*(web / "src").rglob("*"), *(web / "public").rglob("*"), web / "index.html")
             if path.is_file() and path.stat().st_mtime > built_at]
    assert not newer, f"web/dist is older than {newer[0]} — run `npm --prefix web run build` first"
    return DIST


@pytest.fixture()
def site(monkeypatch, built, db):
    """The API and the site on a free port, the docgen worker on a thread, and the three jobs."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    from fastapi.staticfiles import StaticFiles

    from libs.mail import ConsoleSender
    from services.api import deps
    from services.api.app import app

    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    run = uuid.uuid4().hex[:8]
    ids = _jobs(db, run, TITLES)
    app.dependency_overrides[deps.get_mailer] = ConsoleSender
    app.mount("/", StaticFiles(directory=built, html=True), name="e2e-site")
    base, stop = _serve(app, monkeypatch)
    try:
        yield base, f"jordan.{run}@example.com", ids
    finally:
        stop()
        app.router.routes[:] = [route for route in app.router.routes
                                if getattr(route, "name", None) != "e2e-site"]
        app.dependency_overrides.clear()


def test_a_new_person_goes_from_sign_up_to_a_downloaded_cv(site, capsys, tmp_path):
    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        # A phone first: the site is built for one.
        page = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        # Their jobs: the two that match, each saying why, and not the one that does not.
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        cards = page.locator("article.job-card")
        expect(cards).to_have_count(2)
        for title in TITLES[:2]:
            card = cards.filter(has=page.get_by_role("link", name=title, exact=True))
            expect(card.locator(".why")).to_have_text("Why: backend engineer")
        expect(page.get_by_text(TITLES[2])).to_have_count(0)

        # Make my CV: it says it is working, then hands over a PDF.
        cards.first.get_by_role("button", name="Make my CV").click()
        expect(cards.first.get_by_role("button", name="Making your CV…")).to_be_visible()
        ready = cards.first.get_by_role("link", name="Download your CV")
        expect(ready).to_be_visible(timeout=60_000)
        with page.expect_download() as download:
            ready.click()
        saved = tmp_path / download.value.suggested_filename
        download.value.save_as(saved)
        assert saved.name.endswith(".pdf")
        assert saved.read_bytes().startswith(b"%PDF"), "a real PDF, rendered by Chrome"

        _marks(page)
        _alert_links(page, base, ids)
        _settings(browser, page, base)
        _more(page, base)
        browser.close()


def test_a_server_that_does_not_answer_is_said_out_loud_and_tried_again(built):
    """The site's first question is `/dashboard`. Any answer but a 401 used to be dropped, so the page
    said "Loading…" forever: what running the site with no API behind it looked like (2026-10-29)."""
    status = {"/dashboard": 500}

    def answer(route) -> None:
        path = urlparse(route.request.url).path
        if path in ("/dashboard", "/auth/refresh"):
            route.fulfill(status=status.get(path, 401), json={"detail": "no"})
        elif (file := built / (path.lstrip("/") or "index.html")).is_file():
            route.fulfill(path=file)
        else:
            route.fulfill(status=404)

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        page.route("http://site.test/**", answer)
        page.goto("http://site.test/")
        expect(page.get_by_text("Mahoraga can't be reached right now.")).to_be_visible()
        expect(page.get_by_text("The server answered 500.")).to_be_visible()

        status["/dashboard"] = 401  # back, and signed out
        page.get_by_role("button", name="Try again").click()
        expect(page.get_by_text("The job search that runs itself.")).to_be_visible()
        browser.close()


def _sign_up(page, base: str, email: str, capsys) -> None:
    """Sign up, and open the link from the email, which is printed rather than sent."""
    page.goto(f"{base}/#/signup")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("correct-horse-battery-9")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
    link = re.search(r"http://\S+#/verify\?token=\S+", capsys.readouterr().out)
    assert link, "the confirmation email carries the link"
    page.goto(link.group(0))


def _onboard(page) -> None:
    """The three screens: the CV, what was read off it, done."""
    expect(page.get_by_role("heading", name="Your CV")).to_be_visible()
    page.locator("label.drop", has_text="Upload your CV").locator("input").set_input_files(
        files=[{"name": "cv.txt", "mimeType": "text/plain", "buffer": CV.encode()}])
    expect(page.get_by_text("cv.txt")).to_be_visible()
    page.get_by_role("button", name="Next").click()

    # Read off the CV by rules: their name, and the job they do as a green word.
    expect(page.get_by_role("heading", name="What we found")).to_be_visible()
    expect(page.get_by_label("Your name")).to_have_value("Jordan Ellery")
    expect(page.locator(".word.green", has_text="backend engineer").first).to_be_visible()
    page.get_by_role("button", name="Looks good").click()

    expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
    page.get_by_role("button", name="See my jobs").click()


def _marks(page) -> None:
    """"I applied" shows as done and is counted; "Hide" leaves an Undo that brings the job back."""
    def card(title):
        return page.locator("article.job-card").filter(has=page.get_by_role("link", name=title, exact=True))

    card(TITLES[0]).get_by_role("button", name="I applied").click()
    expect(card(TITLES[0]).get_by_role("button", name="I applied ✓")).to_be_visible()
    expect(page.get_by_text("You have applied to 1 job.")).to_be_visible()
    card(TITLES[1]).get_by_role("button", name="Hide").click()
    expect(page.get_by_text(f"Hidden: {TITLES[1]}")).to_be_visible()
    page.get_by_role("button", name="Undo").click()
    expect(card(TITLES[1])).to_be_visible()


def _alert_links(page, base: str, ids: dict[str, int]) -> None:
    """Where an alert lands: its job first and marked, or a job no longer theirs said plainly — and
    every in-app notification opens the page it names, not a route that no longer exists."""
    from libs import notify

    cards = page.locator("article.job-card")
    page.goto(f"{base}/#/feed?job={ids[TITLES[1]]}")
    expect(cards.first.get_by_role("link", name=TITLES[1], exact=True)).to_be_visible()
    expect(cards.first).to_have_class(re.compile(r"\bfocus\b"))
    page.goto(f"{base}/#/feed?job={ids[TITLES[2]]}")
    expect(page.get_by_text("This job is not in your list any more")).to_be_visible()
    expect(cards.first.get_by_role("button", name="Make my CV")).to_be_visible()

    for event in vars(notify).values():
        if isinstance(event, notify.Event) and event.url:
            page.goto(f"{base}/{event.url}")
            expect(page.locator("nav a[aria-current=page]")).to_have_attribute("href", event.url)


def _settings(browser, page, base: str) -> None:
    """Alerts pause and resume; an iPhone outside its home screen is told what to do, not failed."""
    page.goto(f"{base}/#/settings")
    page.get_by_role("button", name="Pause alerts").click()
    expect(page.get_by_text("Alerts are paused")).to_be_visible()
    page.get_by_role("button", name="Turn alerts back on").click()
    expect(page.get_by_text("Alerts are on")).to_be_visible()

    iphone = browser.new_context(storage_state=page.context.storage_state(), user_agent=IPHONE)
    iphone.add_init_script("delete window.PushManager")
    settings = iphone.new_page()
    settings.goto(f"{base}/#/settings")
    expect(settings.get_by_text("Add this site to your home screen first")).to_be_visible()


def _more(page, base: str) -> None:
    """More than a page of matches: the first page, then the rest on "Show more jobs", none twice."""
    from libs.db import database

    _jobs(database(), "more", [f"Backend Engineer {n}" for n in range(30)])
    page.goto(f"{base}/#/")
    cards = page.locator("article.job-card")
    expect(page.get_by_role("heading", name="32 jobs match your words")).to_be_visible()
    expect(cards).to_have_count(25)
    page.get_by_role("button", name="Show more jobs").click()
    expect(cards).to_have_count(32)
    expect(page.get_by_role("button", name="Show more jobs")).to_have_count(0)


def _jobs(db, run: str, titles) -> dict[str, int]:
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    return {title: insert(db, CorpusJob(
                url=f"https://jobs.example.com/{run}/{n}", source="test", title=title, company="Acme",
                location="Berlin, Germany", is_remote=True, language="en", first_seen_at=now(),
                description=f"{title}. Python, Postgres, on call one week in six.")).id
            for n, title in enumerate(titles)}


def _serve(app, monkeypatch):
    """Uvicorn and the docgen worker, each on a thread. Returns the address and a way to stop both."""
    import uvicorn

    from libs.db import database
    from libs.queue.worker import WorkerStats, run_once
    from services.docgen.__main__ import handle

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://localhost:{port}"
    monkeypatch.setenv("PUBLIC_URL", base)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    finished = threading.Event()

    def docgen() -> None:
        while not finished.is_set():
            if not run_once(database(), "docgen", "e2e-docgen", handle, WorkerStats()):
                time.sleep(0.2)

    threads = [threading.Thread(target=server.run, daemon=True), threading.Thread(target=docgen, daemon=True)]
    for thread in threads:
        thread.start()
    while not server.started:
        time.sleep(0.05)

    def stop() -> None:
        finished.set()
        server.should_exit = True
        for thread in threads:
            thread.join(timeout=10)

    return base, stop
