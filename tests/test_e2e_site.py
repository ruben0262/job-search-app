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
from datetime import timedelta
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
            card = cards.filter(has=page.get_by_role("button", name=title, exact=True))
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


def test_the_theme_button_is_a_circle_on_every_page_and_switches_the_theme(site, capsys):
    """Asked for on every page, the signed-out ones and onboarding included (2026-11-01). It was a tall
    pill on sign-in and onboarding, and the landing page had none."""
    base, email, _ = site

    def check(page, where: str) -> None:
        button = page.get_by_role("button", name=re.compile(r"^Switch to (light|dark) mode$"))
        expect(button).to_be_visible()
        box = button.bounding_box()
        assert abs(box["width"] - box["height"]) < 1, f"{where}: {box['width']}x{box['height']}, not a circle"
        theme = page.evaluate("document.documentElement.dataset.theme")
        button.click()
        expect(page.locator("html")).not_to_have_attribute("data-theme", theme or "")

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        for viewport in ({"width": 1280, "height": 800}, {"width": 390, "height": 844}):
            page = browser.new_context(viewport=viewport).new_page()
            for route in ("", "signin", "signup", "forgot"):
                page.goto(f"{base}/#/{route}")
                check(page, route or "landing")
            page.close()

        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        expect(page.get_by_role("heading", name="Your CV")).to_be_visible()
        check(page, "onboarding")
        _onboard(page)
        check(page, "jobs")
        browser.close()


def test_step_two_says_what_it_found_with_switches_suggestions_and_round_choices(site, capsys):
    """The review screen: three titled parts, places and languages as switches that turn on and off,
    suggestions that say they are suggestions, round choices for going in — and all of it saved."""
    from libs.db import database
    from libs.db.models import User, UserConfig

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _upload(page)

        for title in ("About you", "The jobs you want", "Where and how you work"):
            expect(page.get_by_role("heading", name=title)).to_be_visible()
        expect(page.get_by_text("Suggested from your CV — tap one to add it")).to_be_visible()
        page.get_by_role("button", name="+ software engineer").click()
        expect(page.locator(".word.green", has_text="software engineer")).to_be_visible()

        # One or two words work best, and a long phrase is said to be one.
        expect(page.get_by_text("One or two words work best, like “python” or “nurse”")).to_be_visible()
        expect(page.get_by_text("Long phrases find fewer jobs")).to_have_count(0)
        page.get_by_label("Add to: Show me jobs with these words").fill("team coordinator and co lead")
        page.get_by_label("Add to: Show me jobs with these words").press("Enter")
        expect(page.get_by_text("Long phrases find fewer jobs")).to_contain_text(
            "“team coordinator and co lead”")

        # Read off the CV: Berlin, offered as its country. Every country a button, most folded away.
        countries = page.get_by_role("group", name="Which countries?")
        expect(countries.get_by_role("button", name="Germany")).to_have_attribute("aria-pressed", "true")
        expect(countries.get_by_role("button", name="France")).to_have_count(0)
        countries.get_by_role("button", name=re.compile(r"^Show \d+ more countries$")).click()
        countries.get_by_role("button", name="France").click()
        expect(countries.get_by_role("button", name="France")).to_have_attribute("aria-pressed", "true")
        countries.get_by_role("button", name="Germany").click()
        expect(countries.get_by_role("button", name="Germany")).to_have_attribute("aria-pressed", "false")
        expect(page.get_by_label("Add a city")).to_have_count(0)

        for choice in page.get_by_role("radio").all():
            box = choice.bounding_box()
            assert abs(box["width"] - box["height"]) < 1, f"{box['width']}x{box['height']}, not round"
        page.get_by_role("radio", name=re.compile("^Anything")).click()

        page.get_by_role("button", name="Looks good").click()
        expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
        browser.close()

    db = database()
    user = db[User.COLLECTION].find_one({"email": email})
    section = db[UserConfig.COLLECTION].find_one({"user_id": user["_id"]})["config"]["sections"][0]
    assert "software engineer" in section["keywords"]
    assert section["locations"] == ["france"], "what the buttons said is what was saved"
    assert section["remote_policy"] == "any"


def test_the_tabs_in_order_and_what_each_one_is_for(site, capsys):
    """Settings last; Applied keeps what was applied to; help and the contact form are in Settings;
    Admin is there for an admin only, and shows what was sent from Settings."""
    from libs.db import database

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        tabs = page.locator("nav .places a")
        expect(tabs).to_have_text(["Jobs", "Applied", "My search", "My documents", "Settings"])

        card = page.locator("article.job-card").filter(
            has=page.get_by_role("button", name=TITLES[0], exact=True))
        card.get_by_role("button", name="I applied").click()
        expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
        tabs.filter(has_text="Applied").click()
        expect(page.get_by_role("heading", name="You applied to 1 job")).to_be_visible()
        expect(page.get_by_role("button", name=TITLES[0], exact=True)).to_be_visible()

        # Help is the "?" beside the bell, over whatever page is open, and no longer in Settings.
        tabs.filter(has_text="Settings").click()
        expect(page.get_by_role("heading", name="Help and contact")).to_have_count(0)
        page.get_by_role("button", name="Help and contact").click()
        expect(page.get_by_role("dialog", name="Help and contact")).to_be_visible()
        page.get_by_text("How are my jobs chosen?").click()
        expect(page.get_by_text("A job shows up when one of your green words is in it")).to_be_visible()
        page.get_by_label("Subject").fill("My CV shows the wrong city")
        page.get_by_label("Message").fill("It says Berlin, but I moved to Paris last month.")
        page.get_by_role("button", name="Send message").click()
        expect(page.get_by_role("heading", name="Message received")).to_be_visible()
        expect(page.get_by_text(f"We will reply to {email}.")).to_be_visible()

        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.reload()
        expect(tabs).to_have_text(["Jobs", "Applied", "My search", "My documents", "Admin", "Settings"])
        tabs.filter(has_text="Admin").click()
        page.get_by_role("button", name="Messages").click()
        expect(page.get_by_text("My CV shows the wrong city")).to_be_visible()
        browser.close()


def test_job_cards_show_the_start_of_the_description_and_sit_two_to_a_row_on_a_laptop(site, capsys):
    """One column on a phone, two on a laptop, each with the start of its description."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        cards = page.locator(".jobs article.job-card")
        expect(cards).to_have_count(2)

        first, second = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
        assert abs(first["y"] - second["y"]) < 1 and second["x"] > first["x"] + first["width"] / 2, (
            "two to a row")
        expect(cards.first.locator(".summary")).to_contain_text("Python, Postgres")

        page.set_viewport_size({"width": 390, "height": 844})
        first, second = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
        assert abs(first["x"] - second["x"]) < 1 and second["y"] > first["y"], "one to a row"
        browser.close()


def test_a_made_cv_is_still_there_after_a_refresh_in_applied_and_in_my_documents(site, capsys):
    """Made once, downloadable again: the card keeps its download and says a CV was made, Applied
    offers it, and My documents lists it with the room it takes until it is cleared."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        def card():
            return page.locator("article.job-card").filter(
                has=page.get_by_role("button", name=TITLES[0], exact=True))

        expect(card().get_by_text("CV made")).to_have_count(0)
        card().get_by_role("button", name="Make my CV").click()
        expect(card().get_by_role("link", name="Download your CV")).to_be_visible(timeout=60_000)
        expect(card().get_by_text("CV made")).to_be_visible()

        page.reload()
        expect(card().get_by_role("link", name="Download your CV")).to_be_visible()
        expect(card().get_by_text("CV made")).to_be_visible()
        expect(card().get_by_role("button", name="Make my cover letter")).to_be_visible()

        card().get_by_role("button", name="I applied").click()
        expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
        page.goto(f"{base}/#/applied")
        expect(page.get_by_role("link", name="Download your CV")).to_be_visible()

        page.goto(f"{base}/#/documents")
        expect(page.get_by_text(re.compile(r"MB of 5\.0 MB used"))).to_be_visible()
        page.get_by_text("Show your 1 document").click()
        expect(page.get_by_text(f"for {TITLES[0]}")).to_be_visible()
        page.get_by_role("button", name="Delete this one").click()
        page.get_by_role("dialog", name="Delete this one?").get_by_role("button", name="Delete").click()
        expect(page.get_by_text("None yet.")).to_be_visible()
        browser.close()


def test_the_jobs_filters_narrow_the_list_and_clear_back(site, capsys):
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()

        page.get_by_label("Job board").select_option(label="Test (2)")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        page.get_by_label("Posted").select_option(label="Last 24 hours")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        page.get_by_role("button", name="Remote only").click()
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()

        page.get_by_role("button", name="Clear filters").click()
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)
        browser.close()


def test_a_job_opens_whole_and_skipping_survives_a_lost_connection_and_new_jobs_wait_for_a_tap(site, capsys):
    """The company and the country stand out, and how long ago a job was found keeps moving on a page
    left open; a tap on a job shows all of it, and its × or a tap outside closes it, with a CV made
    inside it ready on the card; a Skip made offline is sent when the connection is back and holds
    after a refresh; a job found meanwhile is counted, not pushed into the list, until it is asked
    for (owner, 2026-11-02)."""
    from libs.db import database

    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        page.clock.install()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        cards = page.locator("article.job-card")

        def card(title):
            return cards.filter(has=page.get_by_role("button", name=title, exact=True))

        _where_and_when(page, card(TITLES[0]))

        view = page.get_by_role("dialog", name=TITLES[0])
        card(TITLES[0]).locator(".summary").click()
        expect(view.locator(".description")).to_contain_text("Python, Postgres")
        view.get_by_role("button", name="Close").click()
        expect(view).to_have_count(0)
        card(TITLES[0]).get_by_role("button", name=TITLES[0]).click()
        expect(view).to_be_visible()
        page.mouse.click(5, 5)
        expect(view).to_have_count(0)

        card(TITLES[0]).get_by_role("button", name=TITLES[0]).click()
        view.get_by_role("button", name="Make my CV").click()
        expect(view.get_by_role("link", name="Download your CV")).to_be_visible(timeout=60_000)
        view.get_by_role("button", name="Close").click()
        expect(card(TITLES[0]).get_by_role("link", name="Download your CV")).to_be_visible()
        expect(card(TITLES[0]).get_by_text("CV made")).to_be_visible()

        page.context.set_offline(True)
        card(TITLES[1]).get_by_role("button", name="Skip").click()
        page.get_by_role("dialog", name="Skip this job?").get_by_role("button", name="Skip it").click()
        expect(card(TITLES[1])).to_have_count(0)
        page.context.set_offline(False)
        state = database().user_job_state
        for _ in range(100):
            if state.count_documents({"job_id": ids[TITLES[1]], "dismissed_at": {"$ne": None}}):
                break
            time.sleep(0.1)
        else:
            pytest.fail("the Skip made offline never reached the server")
        page.reload()
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
        expect(card(TITLES[1])).to_have_count(0)

        _jobs(database(), "later", ["Backend Engineer Late"])
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.get_by_role("button", name=re.compile(r"^1 new job"))).to_be_visible()
        expect(cards).to_have_count(1)
        page.get_by_role("button", name=re.compile(r"^1 new job")).click()
        expect(card("Backend Engineer Late").get_by_text("New", exact=True)).to_be_visible()
        expect(cards).to_have_count(2)
        browser.close()


def test_my_search_asks_before_leaving_unsaved_and_applied_opens_the_whole_job(site, capsys):
    """Changes not saved are not lost to a stray tap: leaving asks, and can save on the way out.
    A job applied to opens whole from Applied too, an uploaded CV is plainly not the place to upload
    one, and the CVs made are folded, found by a search and the old ones deleted (owner, 2026-11-02)."""
    from libs.db import database

    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        tabs = page.locator("nav .places a")

        tabs.filter(has_text="My search").click()
        countries = page.get_by_role("group", name="Which countries?")
        expect(countries.get_by_role("button", name="European Union")).to_be_visible()
        countries.get_by_role("button", name=re.compile(r"^Show \d+ more countries$")).click()
        countries.get_by_role("button", name="Austria").click()

        tabs.filter(has_text="Applied").click()
        prompt = page.get_by_role("dialog", name="Save your changes?")
        expect(prompt).to_be_visible()
        prompt.get_by_role("button", name="Stay here").click()
        expect(page.get_by_role("heading", name="What we look for")).to_be_visible()

        tabs.filter(has_text="Applied").click()
        prompt.get_by_role("button", name="Save and go").click()
        expect(page.get_by_role("heading", name=re.compile("You applied to"))).to_be_visible()
        user = database().users.find_one({"email": email})
        saved = database().user_config.find_one({"user_id": user["_id"]})["config"]["sections"][0]
        assert "austria" in saved["locations"], "saved on the way out"

        tabs.filter(has_text="My search").click()
        page.get_by_role("group", name="Which countries?").get_by_role("button", name="Austria").click()
        tabs.filter(has_text="Jobs").click()
        page.get_by_role("dialog", name="Save your changes?").get_by_role(
            "button", name="Leave without saving").click()
        expect(page.get_by_role("heading", name=re.compile("match your"))).to_be_visible()
        saved = database().user_config.find_one({"user_id": user["_id"]})["config"]["sections"][0]
        assert "austria" in saved["locations"], "left, and nothing saved"

        card = page.locator("article.job-card").filter(
            has=page.get_by_role("button", name=TITLES[0], exact=True))
        card.get_by_role("button", name="I applied").click()
        expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
        tabs.filter(has_text="Applied").click()
        page.get_by_role("button", name=TITLES[0], exact=True).click()
        view = page.get_by_role("dialog", name=TITLES[0])
        expect(view.locator(".description")).to_contain_text("Python, Postgres")
        view.get_by_role("button", name="Close").click()

        tabs.filter(has_text="My documents").click()
        uploaded = page.get_by_role("list", name="Uploaded")
        expect(uploaded.get_by_text("cv.txt")).to_be_visible()
        expect(uploaded.get_by_text("In use")).to_be_visible()
        expect(page.get_by_text("Add another CV")).to_be_visible()
        _made_documents(page, user["_id"], ids[TITLES[0]])
        browser.close()


def _where_and_when(page, card) -> None:
    """The company and the country stand out, and a page left open keeps its times right: two hours
    on, the card says so with no refresh (five minutes past the hour: the clock reads to the minute)."""
    expect(card.locator(".company")).to_have_text("Acme")
    expect(card.locator(".country")).to_have_text("Germany")
    expect(card.locator(".job-meta").first).to_contain_text("Berlin · Remote · Found")
    page.clock.fast_forward("02:05:00")
    expect(card.locator(".job-meta").first).to_contain_text("Found 2h ago")


def _made_documents(page, user_id, job_id: int) -> None:
    """Two kept, one made 40 days ago: folded until asked for, found by a search, and the old one
    picked out by its age and deleted while the new one stays."""
    from libs.db import database, insert, now
    from libs.db.models import BuiltDocument

    for number, (kind, age) in enumerate((("cv", 40), ("cover", 0)), start=900_001):
        insert(database(), BuiltDocument(id=number, user_id=user_id, job_id=job_id, kind=kind, mode="instant",
                                         filename=f"{kind}.pdf", content=b"%PDF-", size=5, saved=True,
                                         created_at=now() - timedelta(days=age)))
    page.reload()
    made = page.locator(".made-list li")
    expect(made).to_have_count(0)
    page.get_by_text("Show your 2 documents").click()
    expect(made).to_have_count(2)
    expect(made.first).to_contain_text("Made ")
    page.get_by_label("Search your documents").fill("no such job")
    expect(page.get_by_text("None of yours match.")).to_be_visible()
    page.get_by_label("Search your documents").fill(TITLES[0][:7])
    expect(made).to_have_count(2)
    page.get_by_label("Made", exact=True).select_option(label="Made over a month ago")
    expect(made).to_have_count(1)
    page.get_by_role("button", name="Delete this one").click()
    page.get_by_role("dialog", name="Delete this one?").get_by_role("button", name="Delete").click()
    page.get_by_label("Made", exact=True).select_option(label="Made any time")
    expect(made).to_have_count(1)
    expect(made.first).to_contain_text("Cover letter")
    assert database().built_documents.count_documents({"user_id": user_id}) == 1, "the old one went"


def test_telegram_is_three_steps_with_no_chat_id_and_the_email_is_one_switch(site, capsys, monkeypatch):
    """Their own bot: paste its token, press Start in it, and the page says it is connected by itself.
    Telegram's Bot API is the stand-in from the alerts tests; everything else is the real site."""
    from libs import telegram as bot_api
    from libs.secrets import generate_key
    from tests.test_alerts import FakeTelegram

    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
    fake = FakeTelegram()
    monkeypatch.setattr(bot_api.httpx, "post", fake.post)
    token = fake.make_bot("alice_jobs_bot")
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        page.goto(f"{base}/#/settings")

        # Folded until opened, saying where it stands.
        expect(page.get_by_text("Make your bot.")).not_to_be_visible()
        page.locator("summary", has_text="Telegram").click()
        expect(page.get_by_text("Make your bot.")).to_be_visible()
        page.get_by_label("Bot token").fill(token)
        page.get_by_role("button", name="Save token").click()
        expect(page.get_by_text("Saved: @alice_jobs_bot.")).to_be_visible()
        expect(page.get_by_role("link", name="Open @alice_jobs_bot")).to_have_attribute(
            "href", "https://t.me/alice_jobs_bot")
        expect(page.get_by_text("Waiting for you to press Start…")).to_be_visible()

        fake.press_start(token)
        expect(page.get_by_text("Connected to @alice_jobs_bot. New jobs arrive there.")).to_be_visible(
            timeout=10_000)
        page.get_by_role("button", name="Send a test").click()
        expect(page.get_by_text("Sent. Check Telegram.")).to_be_visible()
        assert [chat for _, chat, _ in fake.sent] == ["4242", "4242"], "the welcome, then the test"

        summary = page.get_by_role("switch", name="A summary of new jobs each morning")
        expect(summary).not_to_be_checked()
        summary.click()
        expect(summary).to_be_checked()
        page.reload()
        expect(page.get_by_role("switch", name="A summary of new jobs each morning")).to_be_checked()
        browser.close()


def _emailed(capsys, route: str) -> str:
    """The link in the email just printed. The email goes after the page has its answer, so it may be
    a moment behind the screen that says it was sent."""
    printed = ""
    for _ in range(50):
        printed += capsys.readouterr().out
        if found := re.search(rf"http://\S+#/{route}\?token=\S+", printed):
            return found.group(0)
        time.sleep(0.1)
    pytest.fail(f"no {route} link was emailed")


def _sign_up(page, base: str, email: str, capsys) -> None:
    """Sign up, and open the link from the email, which is printed rather than sent."""
    page.goto(f"{base}/#/signup")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("correct-horse-battery-9")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
    page.goto(_emailed(capsys, "verify"))


def test_sign_up_confirm_sign_in_and_reset_the_password_each_step_at_once(site, capsys):
    """The whole way in, as a new person goes: sign up, open the emailed link, sign out and back in,
    forget the password, set a new one from the emailed link, and sign in with it while the old one is
    refused. Each step answers within a second and a half (owner, 2026-11-02: "make sure it's fast")."""
    base, _, _ = site
    email = f"jordan.{uuid.uuid4().hex[:8]}@example.com"
    old, new = "correct-horse-battery-9", "a-new-pass-phrase-7"
    took: dict[str, float] = {}

    def step(name: str, act, then) -> None:
        started = time.monotonic()
        act()
        expect(then).to_be_visible()
        took[name] = time.monotonic() - started

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        mine = page.get_by_role("heading", name="Your CV")

        def sign_in(password: str) -> None:
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Sign in").click()

        page.goto(f"{base}/#/signup")
        page.get_by_label("Email").fill(email)
        page.get_by_label("Password").fill(old)
        step("sign up", lambda: page.get_by_role("button", name="Create account").click(),
             page.get_by_role("heading", name="Check your email"))
        step("confirm", lambda: page.goto(_emailed(capsys, "verify")), mine)

        # Signed out as a new visit is: no cookies, and the page loaded afresh.
        def fresh(route: str) -> None:
            context.clear_cookies()
            page.goto("about:blank")
            page.goto(f"{base}/#/{route}")

        fresh("signin")
        step("sign in", lambda: sign_in(old), mine)

        fresh("forgot")
        page.get_by_label("Email").fill(email)
        step("forgot", lambda: page.get_by_role("button", name="Send reset link").click(),
             page.get_by_text("a reset link is on its way"))
        page.goto(_emailed(capsys, "reset"))
        page.get_by_label("New password").fill(new)
        step("reset", lambda: page.get_by_role("button", name="Set password").click(),
             page.get_by_role("heading", name="Password reset"))

        fresh("signin")
        sign_in(old)
        expect(page.get_by_text("email or password is incorrect")).to_be_visible()
        page.get_by_label("Password").fill(new)
        step("sign in again", lambda: page.get_by_role("button", name="Sign in").click(), mine)
        browser.close()

    print("auth steps:", {name: round(seconds, 2) for name, seconds in took.items()})
    assert all(seconds < 1.5 for seconds in took.values()), took


def _upload(page) -> None:
    """The first screen: the CV, then on to what was read off it."""
    expect(page.get_by_role("heading", name="Your CV")).to_be_visible()
    page.locator("label.drop", has_text="Upload your CV").locator("input").set_input_files(
        files=[{"name": "cv.txt", "mimeType": "text/plain", "buffer": CV.encode()}])
    expect(page.get_by_text("cv.txt")).to_be_visible()
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("heading", name="What we found")).to_be_visible()


def _onboard(page) -> None:
    """The three screens: the CV, what was read off it, done."""
    _upload(page)
    # Read off the CV by rules: their name, and the job they do as a green word.
    expect(page.get_by_label("Your name")).to_have_value("Jordan Ellery")
    expect(page.locator(".word.green", has_text="backend engineer").first).to_be_visible()
    page.get_by_role("button", name="Looks good").click()

    expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
    page.get_by_role("button", name="See my jobs").click()


def _marks(page) -> None:
    """"I applied" takes the job away with an Undo, and is counted; "Skip" asks first. A job applied
    to stays away after a refresh; one taken back, or not skipped after all, stays."""
    def card(title):
        return page.locator("article.job-card").filter(has=page.get_by_role("button", name=title, exact=True))

    card(TITLES[0]).get_by_role("button", name="I applied").click()
    expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
    expect(page.get_by_text("You have applied to 1 job.")).to_be_visible()
    expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
    card(TITLES[1]).get_by_role("button", name="Skip").click()
    asked = page.get_by_role("dialog", name="Skip this job?")
    expect(asked).to_contain_text(f"{TITLES[1]} leaves your list")
    asked.get_by_role("button", name="Keep it").click()
    expect(asked).to_have_count(0)
    card(TITLES[1]).get_by_role("button", name="I applied").click()
    page.locator("article.gone", has_text=f"Applied: {TITLES[1]}").get_by_role("button", name="Undo").click()
    expect(card(TITLES[1])).to_be_visible()

    page.reload()
    expect(card(TITLES[1])).to_be_visible()
    expect(card(TITLES[0])).to_have_count(0)


def _alert_links(page, base: str, ids: dict[str, int]) -> None:
    """Where an alert lands: its job first and marked, or a job no longer theirs said plainly — and
    every in-app notification opens the page it names, not a route that no longer exists."""
    from libs import notify

    cards = page.locator("article.job-card")
    page.goto(f"{base}/#/feed?job={ids[TITLES[1]]}")
    expect(cards.first.get_by_role("button", name=TITLES[1], exact=True)).to_be_visible()
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
    # The two they matched, less the one they applied to.
    expect(page.get_by_role("heading", name="31 jobs match your words")).to_be_visible()
    expect(cards).to_have_count(25)
    page.get_by_role("button", name="Show more jobs").click()
    expect(cards).to_have_count(31)
    expect(page.get_by_role("button", name="Show more jobs")).to_have_count(0)


def _jobs(db, run: str, titles) -> dict[str, int]:
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    return {title: insert(db, CorpusJob(
                url=f"https://jobs.example.com/{run}/{n}", source="test", title=title, company="Acme",
                location="Berlin, Germany", country="DE", city="berlin", is_remote=True, language="en",
                first_seen_at=now(),
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
