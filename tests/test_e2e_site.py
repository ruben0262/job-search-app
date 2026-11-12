"""A new person, end to end, on the real built site: sign up, open the emailed link, the three onboarding
screens, their jobs with why each one is there, and a CV that downloads as a PDF; and the free plan, a week
behind with the way to a membership wherever it is missed.

Headless Chromium drives `web/dist`, served by the real API on a real MongoDB, with the real docgen
worker beside it rendering through the real Chrome. Only the outside world is faked: the email is
printed instead of sent, nothing here can reach Telegram or an AI — an instant CV needs neither — and
Stripe's part is played by writing what its webhook would (`tests/test_billing.py` tests that part, and
`tests/test_billing_stripe.py` Stripe itself).

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
from datetime import UTC, datetime, timedelta
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
# What a Make button answers, pressed with no AI account in use (2026-11-10): the CV is up, from onboarding.
NO_ACCOUNT = "To make your CV, first link your AI account in Settings."
# How long a toast stays (`web/src/toast.tsx`).
TOAST_MS = 8000
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
    # Never Stripe from here: its form would be fetched from Stripe for every walk.
    for name in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
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


def test_a_new_person_goes_from_sign_up_to_their_jobs(site, capsys):
    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        # A phone first: the site is built for one.
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        # Their jobs: the two that match, each saying why, and not the one that does not.
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        # And how long a job stays, as Admin sets it: thirty days, unless Admin says otherwise.
        expect(page.get_by_text("Jobs stay here for 30 days.")).to_be_visible()
        cards = page.locator("article.job-card")
        expect(cards).to_have_count(2)
        for title in TITLES[:2]:
            card = cards.filter(has=page.get_by_role("button", name=title, exact=True))
            expect(card.locator(".why")).to_have_text("Why: backend engineer")
        expect(page.get_by_text(TITLES[2])).to_have_count(0)

        # No AI account of their own yet: the buttons are there all the same, and a toast says what is
        # missing, with the way to each (owner, 2026-11-10).
        expect(page.get_by_role("button", name="Make my CV")).to_have_count(2)
        _what_is_missing(page, cards.first)

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
        check(page, "onboarding")
        page.goto(f"{base}/#/upgrade")
        expect(page.get_by_role("heading", name="You're a member")).to_be_visible()
        check(page, "membership")
        page.goto(f"{base}/#/")
        _onboard(page)
        check(page, "jobs")
        browser.close()


def test_the_landing_page_counts_from_the_server_and_a_phone_shows_each_place_in_a_picture_and_a_word(
        site, capsys):
    """The landing page's numbers are the server's (`/stats`): typed into it, they said 25 sources long
    after that was true. A big one is said as the round number it is at least, so it reads at a glance
    and stays true (owner, 2026-11-07). The selling points come second, each figure behind them linked to
    its source (owner, 2026-11-12). Sign in and sign up follow the address, going back included. And
    on a phone each tab is a picture over a word (owner, 2026-11-07; pictures alone, 2026-11-06, were not
    enough), each opening its page. Its card says where people apply: on the job's own site, never here
    (owner, 2026-11-06)."""
    from libs.db import database, insert
    from libs.db.models import AtsToken, CountryCount
    from libs.settings import HARVEST_SOURCES

    base, email, _ = site
    # 43 company boards answering, and 72,345 new jobs found an hour ago.
    for n in range(43):
        insert(database(), AtsToken(ats="greenhouse", token=f"company-{n}"))
    hour = datetime.now(UTC) - timedelta(hours=1)
    insert(database(), CountryCount(id=f"{hour:%Y-%m-%dT%H:%M}|DE", hour=hour, country="DE", jobs=72_345))
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(f"{base}/#/")
        reach = page.locator(".reach div")
        expect(reach).to_have_text([f"{len(HARVEST_SOURCES)}Job sources", "40+Company career pages",
                                    "72,000+New jobs this week", "24/7Rapid checks"])
        demo = page.locator(".demo-card")
        demo.get_by_role("button", name="Apply on LinkedIn").click()
        expect(demo).to_contain_text("opens it on LinkedIn, and you apply there")
        # The sections at the foot of a phone: a picture over each one's word (owner, 2026-11-07).
        expect(page.locator(".dock a").filter(has=page.locator(".short"))).to_have_text(
            ["Why", "What", "How", "Jobs", "Price", "Start"], use_inner_text=True)
        # Each section says what it is in a big title of its own, where a small label stood (owner,
        # 2026-11-07).
        expect(page.locator("main section h2")).to_have_text([
            "Be first. Be seen.", "Every job site. One list for you.", "From sign\u2011up to applied.",
            "See it working.", "Start free. Upgrade to be first.", "Start in a few minutes."])
        # Each figure behind a selling point says where it comes from, and links there (owner, 2026-11-12).
        figures = page.locator("#edge .figures > div")
        expect(figures).to_have_count(5)
        for figure in figures.all():
            expect(figure.locator("a")).to_have_attribute("href", re.compile(r"^https://"))

        page.goto(f"{base}/#/signin")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        page.goto(f"{base}/#/signup")
        expect(page.get_by_role("heading", name="Create your account")).to_be_visible()
        page.go_back()
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()

        _sign_up(page, base, email, capsys)
        _onboard(page)
        places = page.locator("nav .places a")
        expect(places).to_have_text(["Jobs", "Applied", "Search", "Docs", "Settings"], use_inner_text=True)
        for place in places.all():
            expect(place.locator("svg")).to_be_visible()
        page.get_by_role("link", name="Search", exact=True).click()
        expect(page.get_by_role("heading", name="The jobs you want", level=1)).to_be_visible()
        expect(page.get_by_role("link", name="Search", exact=True)).to_have_attribute("aria-current", "page")
        browser.close()


def test_the_landing_page_offers_free_and_member_and_an_admin_words_both(site, capsys):
    """Two cards: Free, a week behind, with what it leaves out crossed out and read out as not included, and
    no card asked for; and Member, its free days on a tab, which signs up for the membership. The price is
    Stripe's: none here, where Stripe is not set up, rather than a wrong one. An admin changes the words in
    Settings, watching them as a visitor will see them, and the landing page says them, on the smallest phone
    too (owner, 2026-11-12)."""
    from libs.db import database
    from libs.settings import DEFAULT_PRICING_CARDS

    base, email, _ = site
    free, member = DEFAULT_PRICING_CARDS["free"], DEFAULT_PRICING_CARDS["member"]
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"{base}/#/")
        cards = page.locator(".price-card")
        expect(cards.locator(".price-name")).to_have_text([free["name"], member["name"]])
        expect(cards.first.locator(".price-after")).to_have_text("Jobs 7 days after members see them")
        expect(cards.first.locator(".price-list li.out")).to_have_text(
            [f"Not included: {line}" for line in free["left_out"]])
        expect(cards.last.locator(".price-badge")).to_have_text("7 days free")
        expect(cards.last.locator(".price-list li")).to_have_text(member["benefits"])
        expect(page.locator(".price-amount")).to_have_count(0)
        expect(cards.first.get_by_role("link", name=free["button"])).to_have_attribute("href", "#/signup")
        expect(cards.last.get_by_role("link", name=member["button"])).to_have_attribute(
            "href", "#/signup?plan=member")

        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.goto(f"{base}/#/admin/settings")
        editor = page.locator(".pricing-admin")
        editor.get_by_role("button", name="Member", exact=True).click()
        editor.get_by_label("Name", exact=True).fill("Mahoraga Plus")
        editor.get_by_role("button", name="Add a line").first.click()
        editor.get_by_label(f"Included, line {len(member['benefits']) + 1}").fill("Help from a person")
        # The preview follows the typing, before anything is saved.
        expect(editor.locator(".price-name").last).to_have_text("Mahoraga Plus")
        editor.get_by_role("button", name="Save both cards").click()
        expect(editor.get_by_role("status")).to_have_text("Saved. The landing page shows it now.")

        visitor = browser.new_context(viewport={"width": 320, "height": 640}).new_page()
        visitor.goto(f"{base}/#/")
        expect(visitor.locator(".price-name")).to_have_text([free["name"], "Mahoraga Plus"])
        added = visitor.locator(".price-card").last.locator(".price-list li").last
        expect(added).to_have_text("Help from a person")
        assert visitor.evaluate("document.documentElement.scrollWidth") <= 320, "past a small phone's edge"
        assert visitor.evaluate("document.querySelector('.dock').getBoundingClientRect().width") <= 320 - 16
        browser.close()


# What a screen of the site gets wrong at one size: anything past the screen's edge that nothing clips or
# scrolls, and anything in a top bar sitting on something else there. The phone's tab bar is fixed to the
# foot of the screen, a layer of its own, and is left out of the second.
FAULTS = r"""() => {
  const named = (el) => `${el.tagName.toLowerCase()} "${(el.textContent || "").trim().slice(0, 30)}"`;
  const clipped = (el) => {
    for (let up = el.parentElement; up && up !== document.body; up = up.parentElement) {
      if (/(hidden|clip|auto|scroll)/.test(getComputedStyle(up).overflowX)) return true;
    }
    return false;
  };
  const off = [...document.querySelectorAll("body *")]
    .filter((el) => !el.closest("svg, [aria-hidden=true]") && el.getClientRects().length)
    .filter((el) => {
      const box = el.getBoundingClientRect();
      return (box.right > innerWidth + 1 || box.left < -1) && !clipped(el);
    })
    .map(named);
  const fixed = (el) => getComputedStyle(el.closest(".places") ?? el).position === "fixed";
  const inBar = [...document.querySelectorAll(".app > nav :is(a, button), .lp-top :is(a, button)")]
    .filter((el) => el.getClientRects().length && !fixed(el));
  const across = (p, q) => Math.min(p.right, q.right) - Math.max(p.left, q.left);
  const down = (p, q) => Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
  const over = [];
  inBar.forEach((a, i) => inBar.slice(i + 1).forEach((b) => {
    if (a.contains(b) || b.contains(a)) return;
    const p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
    if (across(p, q) > 1 && down(p, q) > 1) over.push(`${named(a)} over ${named(b)}`);
  }));
  return {off, over};
}"""


def test_nothing_runs_off_the_screen_or_over_something_else_at_any_size(site, capsys):
    """A phone, a tablet and a laptop: on the landing page and the Jobs page, nothing past the screen's
    edge, and nothing in the top bar on top of anything else in it. A tablet held upright had the laptop's
    bar of words, which ran under its own buttons at 768px and 820px (2026-11-06)."""
    base, email, _ = site
    sizes = (320, 390, 768, 820, 1024, 1440)
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"{base}/#/")
        expect(page.get_by_text("The job search that runs itself.")).to_be_visible()
        for width in sizes:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"landing at {width}px"

        _sign_up(page, base, email, capsys, member=False)
        page.goto(f"{base}/#/upgrade")
        expect(page.get_by_role("heading", name="Become a member")).to_be_visible()
        for width in sizes:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the membership's page at {width}px"
        _member(page, email)
        page.goto(f"{base}/#/")
        _onboard(page)
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        for width in sizes:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"jobs at {width}px"
        browser.close()


def test_step_two_says_what_it_found_with_its_words_ready_and_a_plain_way_to_add_more(site, capsys):
    """The review screen: said to be setting up their profile, three titled parts, two of their own
    words already filtering, a "+ Add" that always answers, places as buttons, ways of working as
    switches — and all of it saved."""
    from libs.db import database
    from libs.db.models import User, UserConfig

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _upload(page)

        # Said to be setting up their profile, with the steps left before their jobs.
        steps = page.get_by_role("list", name="Set up your profile")
        expect(steps.get_by_role("listitem")).to_have_text(["Your CV", "Your search", "Done"])
        expect(steps.locator("[aria-current=step]")).to_have_text("Your search")
        for title in ("About you", "The jobs you want", "Where and how you work"):
            expect(page.get_by_role("heading", name=title)).to_be_visible()
        # Two of their own job titles are filters already, so the list reads as one they can add to.
        for word in ("backend engineer", "software engineer"):
            expect(page.locator(".word.green", has_text=word)).to_be_visible()

        _one_word_short(page)

        # "+ Add" is never greyed out: empty, it puts the cursor in the box; with a word, it adds it.
        add = page.get_by_label("Add to: Show me jobs with these words").locator("..").get_by_role(
            "button", name="+ Add")
        expect(add).to_be_enabled()
        add.click()
        expect(page.get_by_label("Add to: Show me jobs with these words")).to_be_focused()
        page.get_by_label("Add to: Show me jobs with these words").fill("golang")
        add.click()
        expect(page.locator(".word.green", has_text="golang")).to_be_visible()

        # One or two words work best, and a long phrase is said to be one.
        expect(page.get_by_text("At least 3, more is better. Like “nurse” or “python”.")).to_be_visible()
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
        _readable_under_the_pointer(countries.get_by_role("button", name="France"))
        countries.get_by_role("button", name="Germany").click()
        expect(countries.get_by_role("button", name="Germany")).to_have_attribute("aria-pressed", "false")
        expect(page.get_by_label("Add a city")).to_have_count(0)
        _the_last_one_stays_on(page, countries)

        # Remote and hybrid are on to begin with; one more switch, and on-site jobs are theirs too.
        expect(page.get_by_role("switch", name=re.compile("^Hybrid"))).to_be_checked()
        page.get_by_role("switch", name=re.compile("^On-site")).click()

        page.get_by_role("button", name="Looks good").click()
        expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
        browser.close()

    db = database()
    user = db[User.COLLECTION].find_one({"email": email})
    section = db[UserConfig.COLLECTION].find_one({"user_id": user["_id"]})["config"]["sections"][0]
    assert {"backend engineer", "software engineer", "golang"} <= set(section["keywords"])
    assert section["locations"] == ["france"], "what the buttons said is what was saved"
    assert section["workplaces"] == ["remote", "hybrid", "onsite"]
    assert db[UserConfig.COLLECTION].find_one({"user_id": user["_id"]})["config"]["languages"] == ["en"]


def _readable_under_the_pointer(button) -> None:
    """A pressed button's label is the page's background colour on the ink: the hover painted it in ink as
    well, and the label vanished (2026-11-04)."""
    button.hover()
    ink, ground = button.evaluate("b => [getComputedStyle(b).color, getComputedStyle(b).backgroundColor]")
    assert ink != ground, f"the label is {ink} on {ground}"


def _the_last_one_stays_on(page, countries) -> None:
    """The last country picked stays picked: a search always has one. So does the last language."""
    expect(countries.get_by_role("button", name="France")).to_be_disabled()
    page.get_by_role("switch", name="German").click()
    expect(page.get_by_role("switch", name="English")).to_be_disabled()


def _one_word_short(page) -> None:
    """Two words is one short of a search, and the page says so before Save does."""
    expect(page.get_by_role("button", name="Looks good")).to_be_disabled()
    expect(page.get_by_text("To go on: add 1 more green word.")).to_be_visible()


def test_the_admin_charts_the_jobs_found_in_each_country(site, capsys):
    """Where the jobs come from through time, all countries and each, and how many LinkedIn jobs still
    wait for their text (owner, 2026-11-04). Lines on a time axis, a hue a country: the busiest five
    drawn, any other ticked in, and a country keeps its hue when another is taken away (2026-11-05). The
    countries fold under one button and open over the chart, not above it (2026-11-06)."""
    from libs.db import database

    busiest, an_hour_ago = _counted_by_country(database())
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = _admin_corpus(browser, site, capsys)

        expect(page.get_by_text("LinkedIn jobs waiting for their text: 8.")).to_be_visible()
        chart = page.get_by_role("img", name="Jobs found: new jobs per hour")
        expect(chart).to_be_visible()
        legend = page.get_by_role("list", name="Lines drawn").get_by_role("listitem")
        expect(legend).to_have_text(["All countries", *busiest[:5]])

        table = page.locator(".country-table").first  # the countries; the sources are the second
        expect(table).to_be_hidden()
        where = chart.bounding_box()["y"]
        page.locator("summary", has_text="6 drawn").click()
        expect(table.locator("tbody label")).to_have_text(["All countries", *busiest, "Not known"])
        assert chart.bounding_box()["y"] == where, "opened over the chart, which stays where it was"
        france = table.locator("label", has_text="France").locator(".line-key")
        hue = france.evaluate("key => getComputedStyle(key).backgroundColor")
        page.get_by_role("checkbox", name="Germany").uncheck()
        expect(legend).to_have_text(["All countries", *[name for name in busiest[:5] if name != "Germany"]])
        assert france.evaluate("key => getComputedStyle(key).backgroundColor") == hue, "France kept its hue"
        page.get_by_role("checkbox", name="Italy").check()
        expect(legend).to_contain_text(["Italy"])
        # All countries together is a line like the others: off, and on again.
        page.get_by_role("checkbox", name="All countries").uncheck()
        expect(legend.first).not_to_have_text("All countries")
        page.get_by_role("checkbox", name="All countries").check()
        # Shut by a tap anywhere else.
        page.get_by_role("heading", name="Jobs found by country").click()
        expect(table).to_be_hidden()

        # Read without a pointer: the arrow keys walk the hours, every series' number at each.
        page.get_by_label("Jobs found, new jobs per hour: arrow keys read each in turn").focus()
        tip = page.locator(".trend-tip")
        expect(tip).to_contain_text("so far")
        expect(tip).to_contain_text("8All countries")
        page.keyboard.press("ArrowLeft")
        expect(tip).to_contain_text(f"{an_hour_ago:,}All countries")

        # A phone does not scroll sideways, the countries open included. Waited for: the chart is drawn at the
        # width it measures, measured again once the window changes, and read at once it was still the
        # desktop's 1,104 px one run in two.
        page.set_viewport_size({"width": 390, "height": 900})
        page.locator("summary", has_text="6 drawn").click()
        expect(table).to_be_visible()
        page.wait_for_function("document.documentElement.scrollWidth <= 390", timeout=5000)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.locator("h2", has_text="Jobs found by country").locator("..").screenshot(
            path=os.environ.get("E2E_SHOT", "/dev/null") if os.environ.get("E2E_SHOT") else None)
        browser.close()


def test_the_admin_chart_picks_how_far_back_and_what_one_point_is_apart_and_its_scale(site, capsys):
    """How far back it shows and what one point stands for are picked apart, everything counted included,
    and a step that would draw too many points over a range is not offered. A log scale shows a small
    country's changes beside a big one's (owner, 2026-11-06)."""
    from libs.db import database

    _counted_by_country(database())
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = _admin_corpus(browser, site, capsys)
        show, point = page.get_by_label("Show", exact=True), page.get_by_label("One point is", exact=True)
        chart = page.get_by_role("img", name=re.compile("^Jobs found: new jobs per"))

        # The hours' counts are hundreds: an even axis steps by a hundred, a log one by ten times.
        ten = page.locator("text.trend-axis", has_text=re.compile(r"^10$"))
        expect(chart).to_have_accessible_name("Jobs found: new jobs per hour")
        expect(ten).to_have_count(0)
        page.get_by_role("button", name="Log", exact=True).click()
        expect(chart).to_have_accessible_name("Jobs found: new jobs per hour, on a log scale")
        expect(ten).to_have_count(1)
        page.get_by_role("button", name="Even", exact=True).click()

        point.select_option(label="15 minutes")
        expect(chart).to_have_accessible_name("Jobs found: new jobs per 15 minutes")
        expect(show).to_have_value("24")
        show.select_option(label="30 days")
        expect(chart).to_have_accessible_name("Jobs found: new jobs per day")
        expect(point.locator("option")).to_have_text(
            ["1 hour", "2 hours", "4 hours", "8 hours", "12 hours", "1 day"])
        # Everything counted: thirty hours, so a run a point again.
        show.select_option(label="All time")
        expect(chart).to_have_accessible_name("Jobs found: new jobs per 15 minutes")
        expect(page.get_by_text("Counting began")).to_have_count(0)
        browser.close()


def test_the_admin_chart_counts_every_job_board_at_first_and_only_those_ticked(site, capsys):
    """Every source counted when the page opens, each listed with its own jobs, the counts kept before
    sources were among them; one left out takes its jobs out of the lines. Its dropdown and the countries'
    are the size of the selects and buttons beside them (owner, 2026-11-10)."""
    from libs.db import database

    _counted_by_country(database())
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = _admin_corpus(browser, site, capsys)
        # Measured once all six are drawn: two come with the chart's own answer, after its heading, and
        # measuring at once failed one run in three (2026-11-11).
        expect(page.locator(".chart-controls :is(select, summary, button.toggle)")).to_have_count(6)
        heights = page.evaluate("""() => [...document.querySelectorAll(
            '.chart-controls :is(select, summary, button.toggle)')].map((box) => box.offsetHeight)""")
        assert len(heights) == 6 and len(set(heights)) == 1, f"one size for every box in the row: {heights}"

        total = page.locator(".trend-head strong")
        everything = int(total.text_content().replace(",", ""))
        page.locator("summary", has_text="All 20").click()
        sources = page.locator(".country-table").filter(has_text="Source")
        expect(sources.get_by_role("checkbox")).to_have_count(21)
        for box in sources.get_by_role("checkbox").all():
            expect(box).to_be_checked()
        expect(sources.locator("tr", has_text="LinkedIn")).to_contain_text("8")
        sources.get_by_role("checkbox", name="Not recorded").uncheck()
        expect(total).to_have_text("8")
        expect(page.locator("summary", has_text="19 of 20")).to_be_visible()
        sources.get_by_role("checkbox", name="All sources").check()
        expect(total).to_have_text(f"{everything:,}")
        sources.get_by_role("checkbox", name="All sources").uncheck()
        expect(page.get_by_text("No source is ticked.")).to_be_visible()
        browser.close()


def test_an_account_page_goes_back_to_all_accounts(site, capsys):
    """Its back link, and the browser's, land on Admin's Accounts, not on its first tab: each tab has its
    own address (owner, 2026-11-10)."""
    from libs.db import database

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.reload()
        page.locator("nav .places a", has_text="Admin").click()
        page.get_by_role("button", name="Accounts").click()
        accounts = page.get_by_role("heading", name="Accounts")
        page.locator("tr.row-link", has_text=email).click()
        expect(page.get_by_role("link", name="← All accounts")).to_be_visible()

        page.get_by_role("link", name="← All accounts").click()
        expect(accounts).to_be_visible()
        expect(page.locator(".tab.on")).to_have_text("Accounts")
        page.locator("tr.row-link", has_text=email).click()
        expect(page.get_by_role("link", name="← All accounts")).to_be_visible()
        page.go_back()
        expect(accounts).to_be_visible()
        # A reload stays on the tab it was on.
        page.reload()
        expect(page.locator(".tab.on")).to_have_text("Accounts")
        browser.close()


def _admin_corpus(browser, site, capsys):
    """A new account made an admin, on Admin → Corpus, at a laptop's width."""
    from libs.db import database

    base, email, _ = site
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    _sign_up(page, base, email, capsys)
    _onboard(page)
    database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
    page.reload()
    page.locator("nav .places a", has_text="Admin").click()
    page.get_by_role("button", name="Corpus").click()
    expect(page.get_by_role("heading", name="Jobs found by country")).to_be_visible()
    return page


def _counted_by_country(db) -> tuple[list[str], int]:
    """Eight LinkedIn jobs stored this hour, five in Germany, two in France, one remote; and the thirty
    hours before it counted, each country with its own rhythm. The countries busiest first, and every
    country's count an hour ago."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CountryCount

    corpus = CorpusRepository(db)
    for n, where in enumerate(["Berlin, Germany"] * 5 + ["Paris, France"] * 2 + ["Remote"]):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{900 + n}", source="linkedin", title="Nurse",
                   location=where)
    rhythm = {"GB": (60, 11, 50), "DE": (50, 7, 40), "FR": (30, 5, 25), "NL": (20, 3, 15), "ES": (10, 2, 9),
              "IT": (8, 1, 5), "PL": (5, 1, 4)}
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    db[CountryCount.COLLECTION].insert_many([
        {"_id": f"seeded {back} {code}", "hour": hour - timedelta(hours=back), "country": code,
         "jobs": low + (back * step) % spread}
        for back in range(1, 31) for code, (low, step, spread) in rhythm.items()])
    totals = {code: sum(low + (back * step) % spread for back in range(1, 31))
              for code, (low, step, spread) in rhythm.items()}
    totals["DE"], totals["FR"] = totals["DE"] + 5, totals["FR"] + 2
    names = {"GB": "United Kingdom", "DE": "Germany", "FR": "France", "NL": "Netherlands", "ES": "Spain",
             "IT": "Italy", "PL": "Poland"}
    return ([names[code] for code in sorted(totals, key=lambda code: -totals[code])],
            sum(low + step % spread for low, step, spread in rhythm.values()))


def test_a_job_found_somewhere_else_is_brought_in_by_its_link(site, capsys):
    """Add a job, on a phone: a link to a job already here shows it ready for a CV, though it has none
    of their words, and it stays listed there; a link into this server's own network is refused in
    words, and never requested."""
    from libs.db import database
    from libs.db.models import CorpusJob

    base, email, ids = site
    nurse = database()[CorpusJob.COLLECTION].find_one({"_id": ids[TITLES[2]]})["url"]
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        # A tab of Jobs, not a place of its own: Jobs stays the place lit in the bar.
        page.get_by_role("navigation", name="Jobs").get_by_role("link", name="Add a job").click()
        expect(page.get_by_role("heading", name="Found a job somewhere else?")).to_be_visible()
        expect(page.locator("nav .places a[aria-current=page]")).to_have_text("Jobs", use_inner_text=True)

        page.get_by_label("The job's link").fill(nurse)
        page.get_by_role("button", name="Find this job").click()
        expect(page.get_by_text("We already have this job. Here it is.")).to_be_visible()
        card = page.locator("article.job-card").filter(
            has=page.get_by_role("button", name=TITLES[2], exact=True))
        expect(card).to_be_visible()
        expect(card.locator(".why")).to_have_count(0)

        page.get_by_label("The job's link").fill("http://127.0.0.1:9/jobs/1")
        page.get_by_role("button", name="Find this job").click()
        expect(page.get_by_text("That link is not to a public web page.")).to_be_visible()

        page.reload()
        expect(page.get_by_role("heading", name="Jobs you added")).to_be_visible()
        expect(page.get_by_role("button", name=TITLES[2], exact=True)).to_be_visible()
        browser.close()


def test_every_tab_is_drawn_whole_the_moment_it_is_opened(site, capsys):
    """Switching tabs showed nothing for a moment, then the page (owner, 2026-11-03): each began
    invisible and faded in, and read its data only once it was open. Here the server takes 400 ms to
    answer, and each page is looked at two frames after its tab is pressed."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        page.wait_for_load_state("networkidle")

        def slow(route):
            time.sleep(0.4)
            route.continue_()

        page.route(re.compile(r"/(applied|jobs/added|onboarding/\w+|documents|saved|templates|account|ai-accounts|feed)"),
                   slow)
        for tab, drawn in (("Applied", "You applied to 0 jobs"), ("My search", "The jobs you want"),
                           ("My documents", "Each one you make is kept here"), ("Settings", email),
                           ("Jobs", "2 jobs match your words"), ("Add a job", "Found a job somewhere else?")):
            seen = page.evaluate("""(tab) => new Promise((done) => {
                const links = [...document.querySelectorAll("nav a")];
                links.find((link) => link.innerText.trim() === tab).click();
                const look = () => {
                    const main = document.querySelector("main");
                    done({text: main.innerText, opacity: getComputedStyle(main).opacity});
                };
                requestAnimationFrame(() => requestAnimationFrame(look));
            })""", tab)
            assert seen["opacity"] == "1", f"{tab} faded in"
            assert drawn in seen["text"], f"{tab} was not drawn at once: {seen['text'][:120]!r}"
            for waiting in ("Loading…", "Reading what we found…", "Your applications…", "Finding your jobs…"):
                assert waiting not in seen["text"], f"{tab} drew {waiting!r} first"
        browser.close()


def test_the_notifications_open_over_the_jobs_tabs(site, capsys):
    """The Jobs tab row is a `nav`, and the top bar's rule (sticky, opaque, over the page) caught it, so
    it sat across the open notifications (owner's screenshot, 2026-11-03). What is on top at the panel's
    middle row must be the panel."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1490, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        expect(page.get_by_role("navigation", name="Jobs")).to_be_visible()
        page.get_by_role("button", name="Notifications").click()
        panel = page.get_by_role("dialog", name="Notifications")
        expect(panel).to_be_visible()
        covered = page.evaluate("""() => {
            const box = document.querySelector(".bell-panel").getBoundingClientRect();
            const y = box.top + box.height / 2;
            return [0.1, 0.3, 0.5, 0.7, 0.9].map((at) => box.left + box.width * at)
              .filter((x) => !document.elementFromPoint(x, y).closest(".bell-panel"))
              .map((x) => document.elementFromPoint(x, y).outerHTML.slice(0, 80));
        }""")
        assert covered == [], f"over the notifications: {covered}"
        browser.close()


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
        expect(tabs).to_have_text(["Jobs", "Applied", "My search", "My documents", "Settings"],
                                  use_inner_text=True)

        card = page.locator("article.job-card").filter(
            has=page.get_by_role("button", name=TITLES[0], exact=True))
        card.get_by_role("button", name="Mark as applied").click()
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
        expect(tabs).to_have_text(["Jobs", "Applied", "My search", "My documents", "Admin", "Settings"],
                                  use_inner_text=True)
        tabs.filter(has_text="Admin").click()
        page.get_by_role("button", name="Messages").click()
        expect(page.get_by_text("My CV shows the wrong city")).to_be_visible()

        # Their own account, as an operator sees it: online now, and what they do with the site.
        page.get_by_role("button", name="Accounts").click()
        page.get_by_role("cell", name=email, exact=True).click()
        expect(page.locator(".account-seen")).to_contain_text("Last here just now")
        expect(page.get_by_role("heading", name="What they look for")).to_be_visible()
        expect(page.get_by_role("heading", name="Setup")).to_have_count(0)
        if shot := os.environ.get("E2E_SHOT"):
            page.screenshot(path=shot, full_page=True)

        # The smallest phone: an admin's six tabs still fit, each a picture over a whole word, and the bar is
        # at the very top of the screen (owner, 2026-11-07). Admin was hidden there, and a phone's page began
        # 16px down.
        page.set_viewport_size({"width": 320, "height": 640})
        page.goto(f"{base}/#/")
        expect(tabs).to_have_text(["Jobs", "Applied", "Search", "Docs", "Admin", "Settings"],
                                  use_inner_text=True)
        expect(tabs.filter(has_text="Admin")).to_be_visible()
        assert page.evaluate("""() => [...document.querySelectorAll("nav .places a")].every((tab) => {
            const word = tab.querySelector(".place-short");
            return word.scrollWidth <= word.clientWidth && tab.getBoundingClientRect().right <= innerWidth;
        })"""), "a word cut short, or a tab past the screen's edge"
        page.evaluate("scrollTo(0, 0)")
        assert page.locator(".app > nav").bounding_box()["y"] == 0, "space above the bar"
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
    offers it, and My documents lists it with the room it takes until it is cleared. Making new ones is
    paused (owner, 2026-11-08), so this one was made before: written straight into the database."""
    from libs.db import database, insert, now
    from libs.db.models import BuiltDocument

    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        def card():
            return page.locator("article.job-card").filter(
                has=page.get_by_role("button", name=TITLES[0], exact=True))

        expect(card().get_by_text("CV made")).to_have_count(0)
        user = database().users.find_one({"email": email})
        insert(database(), BuiltDocument(id=900_101, user_id=user["_id"], job_id=ids[TITLES[0]], kind="cv",
                                         mode="ai", filename="cv.pdf", content=b"%PDF-1.4 kept", size=13,
                                         saved=True, created_at=now()))

        page.reload()
        expect(card().get_by_role("link", name="Download your CV")).to_be_visible()
        expect(card().get_by_text("CV made")).to_be_visible()
        expect(card().get_by_role("link", name="Download your cover letter")).to_have_count(0)

        card().get_by_role("button", name="Mark as applied").click()
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


def test_a_person_picks_how_their_cvs_and_letters_look_and_it_stays_picked(site, capsys):
    """My documents shows the look of each kind in use; Change opens the shelf, the most popular first and
    then every sector, each look a picture of itself; a tap picks one, and it stays picked (owner,
    2026-11-07). On a phone, and nothing runs off its screen. That the next document is made in the pick
    is `test_document_builds`: making documents on the site is paused (2026-11-08)."""
    from libs import templates

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        page.goto(f"{base}/#/documents")
        looks = page.locator("section.card").filter(
            has=page.get_by_role("heading", name="How your CVs and cover letters look"))
        expect(looks.locator(".look-name")).to_have_text(["Meridian", "Meridian"])
        _documents_in_order(page)
        _the_sheet_opens_drawn(page, templates)

        sheet = page.get_by_role("dialog", name="Pick how your CVs look")
        expect(sheet.locator(".look-group h3")).to_have_text(["Most popular", *templates.SECTORS])
        popular = sheet.get_by_role("region", name="Most popular").locator(".look-card")
        expect(popular).to_have_count(len(templates.POPULAR["cv"]))
        expect(popular.first).to_have_attribute("aria-pressed", "true")
        # The picture is the look in abstract shapes, the name a block in the look's blue, nothing written.
        picture = popular.first.locator(".picture")
        expect(picture.locator(".cv-name i")).to_be_visible()
        assert picture.locator(".cv-name i").evaluate("shape => getComputedStyle(shape).backgroundColor") \
            == "rgb(0, 51, 153)", "Meridian's name is its blue"
        assert picture.locator("body").inner_text().strip() == "", "a picture has nothing written in it"
        expect(popular.first).to_have_text("Meridian · In use")
        for width in (320, 390):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the shelf at {width}px"

        sheet.get_by_role("region", name="Finance and accounting").get_by_role(
            "button", name="Ledger", exact=True).click()
        said = page.get_by_role("status").filter(has_text="Your CVs will now look like Ledger.")
        expect(said).to_be_visible()
        expect(sheet).to_have_count(0)
        expect(looks.locator(".look-name")).to_have_text(["Ledger", "Meridian"])
        page.reload()
        expect(looks.locator(".look-name")).to_have_text(["Ledger", "Meridian"])
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, "My documents at 390px"
        browser.close()


def _documents_in_order(page) -> None:
    """Who you are first, then how your documents look, what was made, and the files (owner, 2026-11-08)."""
    firsts = page.locator("main section.card").evaluate_all("""cards => cards.map((card) =>
        (card.querySelector(':scope > h2') ?? card.querySelector('label')).innerText.split('\\n')[0])""")
    firsts = [first.strip() for first in firsts]
    assert firsts == ["Your name and links", "About you", "How your CVs and cover letters look",
                      "CVs and cover letters we made for you", "Your CV", "Cover letters and other files"]


def _the_sheet_opens_drawn(page, templates) -> None:
    """The sheet opens with every picture in it already drawn, and asks the server for nothing: as frames,
    one request and one document each, ~40 of them took 5-10 s (owner, 2026-11-08)."""
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "/templates" in request.url else None)
    drawn = page.evaluate("""() => new Promise((done) => {
        document.querySelector('[aria-label="Change how your CVs look"]').click();
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const hosts = [...document.querySelectorAll("dialog .picture")];
            done([hosts.length, hosts.filter((host) =>
                host.shadowRoot?.querySelector(".cv-name i")?.getBoundingClientRect().width > 0).length]);
        }));
    })""")
    cards = sum(len(ids) for _, ids in templates.groups("cv"))
    assert drawn == [cards, cards], f"of {cards} pictures, [there, drawn] two frames after the tap: {drawn}"
    assert asked == [], f"opening the sheet asked the server for {asked}"


def test_an_ai_account_linked_in_settings_is_what_the_make_buttons_were_missing(site, capsys, monkeypatch):
    """CVs and cover letters are written with the person's own AI account (owner, 2026-11-09). Linked in
    Settings, each way in by its provider's mark, in use at once, named and never shown back. A switch
    is seen to move, and the rows stay where they are (owner, 2026-11-10). Once linked, a Make button
    no longer asks for one. The provider's check of the key is a stand-in."""
    from libs.credentials import ValidationResult
    from libs.secrets import generate_key

    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
    monkeypatch.setattr("services.api.ai_account_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    key = "sk-ant-api03-" + "Zx9" * 20
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        cards = page.locator("article.job-card")
        toasts = page.locator(".toast")
        cards.first.get_by_role("button", name="Make my CV").click()
        expect(toasts.get_by_text(NO_ACCOUNT)).to_be_visible()
        toasts.get_by_role("link", name="Open Settings").click()

        accounts = page.locator("section.card").filter(
            has=page.get_by_role("heading", name="Your AI accounts"))
        for provider in ("Claude", "ChatGPT", "Google Gemini"):
            tile = accounts.get_by_role("button", name=f"Sign in with {provider}")
            expect(tile).to_be_visible()
            expect(tile.locator(".ai-mark svg path")).to_have_count(1)
        rows = accounts.locator(".ai-accounts li")
        _add_key(accounts, "Claude API key", key)
        expect(rows).to_have_text([re.compile("^Claude API key")])
        expect(rows.first.locator(".ai-mark svg path")).to_have_count(1)
        expect(rows.first).to_contain_text("Writes with claude-haiku-5-5")  # the model Admin picked for it
        expect(accounts.get_by_role("switch", name="Use Claude API key")).to_be_checked()
        assert key not in page.content() and key[-4:] not in accounts.inner_text(), "never shown back"
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, "Settings at 390px"

        # A second goes in use at once. Switching back is seen moving, and moves no row.
        _add_key(accounts, "Gemini API key", "AIza" + "Qw7" * 12)
        expect(rows).to_have_text([re.compile("^Gemini API key.*In use"), re.compile("^Claude API key.*Off")])
        accounts.get_by_role("switch", name="Use Claude API key").click()
        expect(accounts.get_by_text("Turning on…")).to_be_visible()
        expect(rows).to_have_text([re.compile("^Gemini API key.*Off"), re.compile("^Claude API key.*In use")])

        # Their account is no longer what is missing. What is sent was agreed to with the box at sign-up,
        # and taken back, the next press asks for it again; and a cover letter still needs one of theirs,
        # said with the one way to it.
        _withdraw(page)
        page.goto(f"{base}/#/")
        cards.first.get_by_role("button", name="Make my CV").click()
        expect(cards.first.get_by_text("needs your agreement first")).to_be_visible()
        expect(toasts).to_have_count(0)
        cards.first.get_by_role("button", name="Make my cover letter").click()
        expect(toasts).to_have_text([re.compile(
            "^To make your cover letter, first upload a cover letter in My documents.")])
        expect(toasts.get_by_role("link", name="Open Settings")).to_have_count(0)
        expect(toasts.get_by_role("link", name="Open My documents")).to_be_visible()

        page.goto(f"{base}/#/settings")
        accounts.get_by_role("switch", name="Use Claude API key").uncheck()
        expect(accounts.get_by_text("Turning off…")).to_be_visible()
        expect(rows).to_have_text([re.compile("^Gemini API key.*Off"), re.compile("^Claude API key.*Off")])
        browser.close()


def _withdraw(page) -> None:
    """Settings says the box at sign-up agreed to what an AI-written document sends (owner, 2026-11-12), and
    takes it back."""
    agreed = page.get_by_text("You agreed that AI-written documents may use your profile.")
    expect(agreed).to_be_visible()
    page.get_by_role("button", name="Withdraw").click()
    expect(agreed).to_have_count(0)


def _said_over_the_open_job(view) -> None:
    """The open job offers the documents too, and what one is missing is said over it, not behind it: in
    the dialog's own layer, so its toast takes a click."""
    view.get_by_role("button", name="Make my CV").click()
    said = view.locator(".toast")
    expect(said).to_have_text([re.compile(f"^{re.escape(NO_ACCOUNT)}")])
    said.get_by_role("button", name="Close").click()
    expect(said).to_have_count(0)


def _what_is_missing(page, card) -> None:
    """Without an AI account and without a cover letter, each button says what it lacks in a toast, with a
    way to each; closed, it goes, and left alone, it goes by itself (owner, 2026-11-10)."""
    toasts = page.locator(".toast")
    card.get_by_role("button", name="Make my cover letter").click()
    expect(toasts).to_have_text([re.compile(
        "^To make your cover letter, first link your AI account in Settings and upload a cover letter in "
        "My documents.")])
    expect(toasts.get_by_role("link", name="Open Settings")).to_have_attribute("href", "#/settings")
    expect(toasts.get_by_role("link", name="Open My documents")).to_have_attribute("href", "#/documents")
    toasts.get_by_role("button", name="Close").click()
    expect(toasts).to_have_count(0)
    # In the bell at once too, not at its next look a minute on: opening it reloads nothing, so it is
    # there only if the page told the bell (owner, 2026-11-10).
    page.locator(".bell-wrap > button.bell").click()
    expect(page.get_by_role("dialog", name="Notifications")).to_contain_text(
        "Link your AI account to make documents")
    page.locator(".bell-wrap > button.bell").click()

    card.get_by_role("button", name="Make my CV").click()
    expect(toasts).to_have_text([re.compile(f"^{re.escape(NO_ACCOUNT)}")])
    expect(toasts.get_by_role("link", name="Open My documents")).to_have_count(0)
    expect(toasts).to_have_count(0, timeout=TOAST_MS + 3000)

def test_the_jobs_filters_narrow_the_list_stay_as_they_were_left_and_clear_back(site, capsys):
    """The page opens on "Last 24 hours", and the filters stay as they were left, a reload or another
    device later (owner, 2026-11-07): reloaded the moment the last one changed, before its save would
    otherwise go. Clearing them keeps the time chosen."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        posted = page.get_by_label("Posted")
        expect(posted).to_have_value("24")

        page.get_by_label("Job board").select_option(label="Test (2)")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        posted.select_option(label="Last 3 days")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        page.get_by_role("button", name="Remote only").click()
        page.reload()
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        expect(posted).to_have_value("72")
        expect(page.get_by_label("Job board")).to_have_value("test")
        expect(page.get_by_role("button", name="Remote only")).to_have_attribute("aria-pressed", "true")

        page.get_by_role("button", name="Clear filters").click()
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)
        expect(posted).to_have_value("72")
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
        expect(view).to_be_visible()
        _said_over_the_open_job(view)
        view.get_by_role("button", name="Close").click()
        _closes_for_good(page, card(TITLES[0]), view)

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

        # One arrives as one leaves, past its shelf life: the button says both, so the list a refresh brings
        # is no longer than it says (owner, 2026-11-06).
        _jobs(database(), "later", ["Backend Engineer Late"])
        database().corpus.update_one({"_id": ids[TITLES[0]]},
                                     {"$set": {"first_seen_at": datetime.now(UTC) - timedelta(days=30)}})
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(cards).to_have_count(1)
        page.get_by_role("button", name="1 new job · 1 older one gone · Show it", exact=True).click()
        expect(card("Backend Engineer Late").get_by_text("New", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
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
        expect(page.get_by_role("heading", name="The jobs you want", level=1)).to_be_visible()

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
        card.get_by_role("button", name="Mark as applied").click()
        expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
        tabs.filter(has_text="Applied").click()
        page.get_by_role("button", name=TITLES[0], exact=True).click()
        view = page.get_by_role("dialog", name=TITLES[0])
        expect(view.locator(".description")).to_contain_text("Python, Postgres")
        view.get_by_role("button", name="Close").click()
        _how_far_it_got(page)

        tabs.filter(has_text="My documents").click()
        _switched_off_and_on(page, user["_id"])
        expect(page.get_by_text("Add another CV")).to_be_visible()
        _made_documents(page, user["_id"], ids[TITLES[0]])
        browser.close()


def _add_key(accounts, name: str, key: str) -> None:
    """An API key, through the fold-out beneath the sign-ins."""
    accounts.get_by_text("Use an API key instead").click()
    accounts.get_by_label("Which key").select_option(label=name)
    accounts.get_by_role("textbox", name=re.compile(f"^{name}")).fill(key)
    accounts.get_by_role("button", name="Add this key").click()
    expect(accounts.get_by_role("switch", name=f"Use {name}")).to_be_checked()


def _switched_off_and_on(page, user_id) -> None:
    """An uploaded CV is in use; switched off it stays listed and says it is not used, in the
    database too; switched on again, it is in use."""
    from libs.db import database

    uploaded = page.get_by_role("list", name="Uploaded")
    expect(uploaded.get_by_text("cv.txt")).to_be_visible()
    expect(uploaded.get_by_text("In use")).to_be_visible()
    uploaded.get_by_role("switch", name="Use cv.txt").click()
    expect(uploaded.get_by_text("Not used")).to_be_visible()
    assert database().user_documents.find_one({"user_id": user_id})["in_use"] is False
    uploaded.get_by_role("switch", name="Use cv.txt").click()
    expect(uploaded.get_by_text("In use")).to_be_visible()
    # And downloaded again, as it was uploaded (owner, 2026-11-10).
    with page.expect_download() as downloaded:
        uploaded.get_by_role("link", name="Download cv.txt").click()
    assert downloaded.value.suggested_filename == "cv.txt"
    assert pathlib.Path(downloaded.value.path()).read_bytes() == CV.encode()


def _where_and_when(page, card) -> None:
    """The company and the country stand out, and a page left open keeps its times right: two hours
    on, the card says so with no refresh (five minutes past the hour: the clock reads to the minute)."""
    expect(card.locator(".company")).to_have_text("Acme")
    expect(card.locator(".country")).to_have_text("Germany")
    expect(card.locator(".job-meta").first).to_contain_text("Berlin · Remote · Found")
    page.clock.fast_forward("02:05:00")
    expect(card.locator(".job-meta").first).to_contain_text("Found 2h ago")


def _how_far_it_got(page) -> None:
    """On Applied, each job says how far it got, in the person's words (owner, 2026-11-03): one question
    and two answers at a time, the steps lit as far as it went, and Undo for a mistaken tap."""
    card = page.locator("article.job-card").first
    steps = card.get_by_role("list", name="Where this application stands")
    expect(steps.locator("li.done")).to_have_text(["Applied"])
    expect(card.get_by_text("Heard back?")).to_be_visible()

    card.get_by_role("button", name="I got an interview").click()
    expect(steps.locator("li.done")).to_have_text(["Applied", "Interview"])
    expect(page.get_by_text("1 interview")).to_be_visible()
    card.get_by_role("button", name="Not this time").click()
    expect(card.get_by_text("Not this time. On to the next one.")).to_be_visible()
    expect(card.get_by_role("button", name="I got an offer")).to_have_count(0)

    card.get_by_role("button", name="Undo").click()
    expect(card.get_by_role("button", name="I got an offer")).to_be_visible()
    page.reload()  # and it is kept on the server
    expect(steps.locator("li.done")).to_have_text(["Applied", "Interview"])


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


PASSWORD = "correct-horse-battery-9"


def _fill_sign_up(page, email: str, password: str = PASSWORD) -> None:
    """The sign-up form as a person fills it: the password twice, and the box ticked that agrees to the
    privacy policy and its cookies, without which there is no account (owner, 2026-11-12)."""
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_label("Type it again").fill(password)
    page.get_by_label("I agree to the privacy policy and cookies.").check()
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()


def _sign_up(page, base: str, email: str, capsys, *, member: bool = True) -> None:
    """Sign up, open the link from the email, which is printed rather than sent, pick the free plan on the
    page between it and setting up, and land on setting up's first step. `member`: with a membership begun,
    as Stripe's webhook writes one."""
    page.goto(f"{base}/#/signup")
    _fill_sign_up(page, email)
    page.goto(_emailed(capsys, "verify"))
    _pick_free(page)
    if member:
        _member(page, email)


def _pick_free(page) -> None:
    """The page between the email link and setting up: the free plan, and on to setting up."""
    expect(page.get_by_role("heading", name="Choose your plan")).to_be_visible()
    page.get_by_role("button", name="Start free").click()
    expect(page.get_by_role("heading", name="Your CV")).to_be_visible()


def _member(page, email: str) -> None:
    """Stripe's part: a trial begun, as its webhook writes it; the page read again."""
    from libs.db import database
    from libs.db.models import User

    database()[User.COLLECTION].update_one({"email": email}, {"$set": {"subscription_status": "trialing"}})
    page.reload()


def test_a_free_account_sees_each_job_a_week_late_and_the_way_to_a_membership_where_it_is_missed(
        site, db, capsys, monkeypatch):
    """Owner, 2026-11-12: a free account, with no card asked, sees the jobs a week after members do; the
    documents and the alerts are a member's, and each place they are missed says so with the way to the
    membership."""
    from libs.db import insert, now
    from libs.db.models import CorpusJob
    from services.api import billing_routes

    base, email, _ = site
    monkeypatch.setattr(billing_routes, "plan", lambda db: billing_routes.Plan(
        amount=499, currency="gbp", interval="month", interval_count=1, trial_days=7, free_delay_hours=168))
    week_old = now() - timedelta(days=8)
    insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="test", company="Acme",
                         title="Backend Engineer, a week ago", location="Berlin, Germany", country="DE",
                         language="en", first_seen_at=week_old, described_at=week_old,
                         description="Backend Engineer. Python, Postgres."))
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys, member=False)
        _onboard(page)

        # The week-old job, and not the three found today.
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
        expect(page.get_by_text("You see jobs 7 days after members see them.")).to_be_visible()
        expect(page.get_by_text("You see each job 7 days after members do. Jobs stay here for 30 days.")) \
            .to_be_visible()
        expect(page.get_by_label("Posted").locator("option")).to_have_text(["Any time", "Last 2 weeks"])
        page.get_by_role("button", name="Make my CV").click()
        notice = page.locator(".toast").filter(has_text="CVs and cover letters comes with a membership.")
        expect(notice).to_be_visible()
        notice.get_by_role("link", name="Become a member").click()
        expect(page.get_by_role("heading", name="Become a member")).to_be_visible()
        expect(page.get_by_text("Payments aren't set up yet.")).to_be_visible()
        page.get_by_role("link", name="Not now").click()

        page.goto(f"{base}/#/settings")
        expect(page.get_by_text("Alerts come with a membership.")).to_be_visible()
        expect(page.get_by_role("button", name="Pause alerts")).to_have_count(0)
        expect(page.get_by_text("The free plan.")).to_be_visible()

        browser.close()


def test_the_plan_is_picked_after_the_email_link_on_the_landing_pages_cards(site, capsys, monkeypatch):
    """Owner, 2026-11-12: signing up asks for no plan; after the email link and before setting up, the
    landing page's two cards, and nothing past them by the address alone. The landing page's Member card
    opens that page on Stripe's checkout, its price large over it, which says plainly here, where Stripe has
    no keys, that payments aren't set up; the way back is to the cards, not past them."""
    from services.api import billing_routes

    base, email, _ = site
    monkeypatch.setattr(billing_routes, "plan", lambda db: billing_routes.Plan(
        amount=499, currency="gbp", interval="month", interval_count=1, trial_days=7, free_delay_hours=168))
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(f"{base}/#/signup")
        expect(page.get_by_text("Your plan")).to_have_count(0)
        _fill_sign_up(page, email)
        expect(page.get_by_text("to choose your plan")).to_be_visible()
        page.goto(_emailed(capsys, "verify"))
        expect(page.get_by_role("heading", name="Choose your plan")).to_be_visible()
        cards = page.locator(".price-card")
        expect(cards.locator(".price-name")).to_have_text(["Free", "Member"])
        expect(cards.first).to_contain_text("Jobs 7 days after members see them")
        expect(cards.last).to_contain_text("£4.99")
        expect(cards.last.locator(".price-badge")).to_have_text("7 days free")
        page.goto(f"{base}/#/settings")
        expect(page.get_by_role("heading", name="Choose your plan")).to_be_visible()
        _pick_free(page)

        # The Member card's link from the landing page: the email's link opens the plan page on the checkout,
        # its price large over it, and the way back is to the cards.
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(f"{base}/#/signup?plan=member")
        _fill_sign_up(page, f"member.{email}")
        link = _emailed(capsys, "verify")
        assert link.endswith("&plan=member")
        page.goto(link)
        expect(page.get_by_role("heading", name="Start your membership")).to_be_visible()
        expect(page.locator(".plan-price")).to_have_text("£4.99 a month, after 7 days free")
        expect(page.get_by_text("Payments aren't set up yet. Pick Free for now.")).to_be_visible()
        page.get_by_role("button", name="← Back to the plans").click()
        _pick_free(page)
        browser.close()


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
        step("sign up", lambda: _fill_sign_up(page, email, old),
             page.get_by_role("heading", name="Check your email"))
        step("confirm", lambda: page.goto(_emailed(capsys, "verify")),
             page.get_by_role("heading", name="Choose your plan"))
        step("plan", lambda: page.get_by_role("button", name="Start free").click(), mine)

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
        page.get_by_label("Type it again").fill(new)
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
    # A third green word: a search takes at least three.
    page.get_by_label("Add to: Show me jobs with these words").fill("golang")
    page.get_by_label("Add to: Show me jobs with these words").press("Enter")
    page.get_by_role("button", name="Looks good").click()

    expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
    page.get_by_role("button", name="See my jobs").click()


def _marks(page) -> None:
    """Nobody applies here: a job opens on the site it was posted on, in a tab of its own (owner,
    2026-11-06). "Mark as applied" takes the job away with an Undo, and is counted; "Skip" asks first. A
    job applied to stays away after a refresh; one taken back, or not skipped after all, stays."""
    def card(title):
        return page.locator("article.job-card").filter(has=page.get_by_role("button", name=title, exact=True))

    apply = card(TITLES[0]).get_by_role("link", name="Apply on Test")
    expect(apply).to_have_attribute("href", re.compile(r"^https://jobs\.example\.com/"))
    expect(apply).to_have_attribute("target", "_blank")
    card(TITLES[0]).get_by_role("button", name="Mark as applied").click()
    expect(page.get_by_text(f"Moved to Applied: {TITLES[0]}")).to_be_visible()
    expect(page.get_by_text("You have applied to 1 job.")).to_be_visible()
    expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
    card(TITLES[1]).get_by_role("button", name="Skip").click()
    asked = page.get_by_role("dialog", name="Skip this job?")
    expect(asked).to_contain_text(f"{TITLES[1]} leaves your list")
    asked.get_by_role("button", name="Keep it").click()
    expect(asked).to_have_count(0)
    card(TITLES[1]).get_by_role("button", name="Mark as applied").click()
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
    gone = page.locator("article.job-card.focus")
    expect(gone).to_contain_text("This job is not in your list any more")
    expect(gone.get_by_role("button", name="Make my CV")).to_be_visible()

    for event in vars(notify).values():
        if isinstance(event, notify.Event) and event.url:
            page.goto(f"{base}/{event.url}")
            if event.url == "#/upgrade":
                # The membership's own page, apart from the places in the bar.
                expect(page.get_by_role("heading", name=re.compile(r"\bmember\b"))).to_be_visible()
            else:
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


def _closes_for_good(page, card, view) -> None:
    """Closed, not shut and left in the page: the job opens again when tapped (2026-11-03)."""
    expect(page.locator("dialog.sheet")).to_have_count(0)
    card.locator("h3 button.title").click()
    expect(view).to_be_visible()
    view.get_by_role("button", name="Close").click()
    expect(page.locator("dialog.sheet")).to_have_count(0)


def _jobs(db, run: str, titles) -> dict[str, int]:
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    return {title: insert(db, CorpusJob(
                url=f"https://jobs.example.com/{run}/{n}", source="test", title=title, company="Acme",
                location="Berlin, Germany", country="DE", city="berlin", is_remote=True, language="en",
                first_seen_at=now(), described_at=now(),
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
