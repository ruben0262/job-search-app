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
from urllib.parse import parse_qs, urlparse, urlsplit

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
# An iPad's Safari, which says it is a Mac, and is told apart by its touch screen.
IPAD = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Safari/605.1.15")


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
    from libs.secrets import generate_key
    from services.api import deps
    from services.api.app import app

    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    # As the deployed API has it (compose will not start one without): it seals the push key.
    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
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
        # A member waits for nothing and has everything: no bar asking them to move up (owner, 2026-11-23).
        expect(page.get_by_role("complementary", name="Your plan")).to_have_count(0)
        # And how long a job stays, as Admin sets it: thirty days, unless Admin says otherwise.
        expect(page.get_by_text("Jobs stay here for 30 days.")).to_be_visible()
        cards = page.locator("article.job-card")
        expect(cards).to_have_count(2)
        for title in TITLES[:2]:
            card = cards.filter(has=page.get_by_role("button", name=title, exact=True))
            expect(card.locator(".why")).to_have_text("Your words: backend engineer")
        expect(page.get_by_text(TITLES[2])).to_have_count(0)

        # No AI account of their own yet: the buttons are there all the same, and a toast says what is
        # missing, with the way to each (owner, 2026-11-10).
        expect(page.get_by_role("button", name="Make my CV")).to_have_count(2)
        _what_is_missing(page, cards.first)

        _bookmarks(page)
        _marks(page)
        _alert_links(page, base, ids)
        _alerts_on_an_iphone(browser, page, base)
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
        # Read in the one call that waits for it: a page drawing its bar again (Jobs, just after setting up)
        # left nothing for a second look to measure when four browsers shared the machine (2026-11-17).
        width, height = button.evaluate("el => [el.offsetWidth, el.offsetHeight]")
        assert abs(width - height) < 1, f"{where}: {width}x{height}, not a circle"
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
        # Signed in on a phone, the theme is a switch in More, on for dark (owner, 2026-11-17), and the
        # phone's own bars take the page's colour with it.
        page.get_by_role("button", name="More", exact=True).click()
        switch = page.get_by_role("dialog", name="More").get_by_role("switch", name="Dark mode")
        expect(switch).to_have_attribute("aria-checked", "true")
        expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#0f1013")
        switch.click()
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(switch).to_have_attribute("aria-checked", "false")
        expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#f5f6f8")
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 1280, "height": 800})
        check(page, "jobs")
        browser.close()


# Endless loops the compositor draws alone: anything else is laid out or painted again on every frame.
COMPOSITED = {"opacity", "transform", "translate", "scale", "rotate", "offset", "easing", "composite",
              "computedOffset"}


def test_what_moves_on_a_phone_is_drawn_by_the_compositor_and_only_while_it_is_seen(site, capsys):
    """Owner, 2026-11-18: "lets also optimise all animations so they are super smooth on mobile as well
    everywhere". Measured on a phone at a quarter of a laptop's speed, the landing page at rest kept the page
    busy on loops nobody could see: the step dots far below were half its work, their colour and glow painted
    on every frame, and a bookmark's colour with them. Each endless loop now animates only what the compositor
    draws, and a part's loops wait until it is on the screen. Switching the theme, the colours' fades
    repainted the whole page under the growing circle: during it nothing fades."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                   is_mobile=True).new_page()
        page.goto(f"{base}/")
        expect(page.locator("h1")).to_have_text("Get seen first.")
        page.wait_for_timeout(500)
        loops = page.evaluate("""() => document.getAnimations()
          .filter((a) => a.effect.getTiming().iterations === Infinity)
          .map((a) => [a.animationName, a.playState, Boolean(a.effect.target.closest('#top')),
                       Boolean(a.effect.target.closest('#how')),
                       [...new Set(a.effect.getKeyframes().flatMap(Object.keys))]])""")
        assert loops, "no endless loop on the landing page: this proves nothing"
        painted = {name: sorted(set(keys) - COMPOSITED) for name, *_, keys in loops if set(keys) - COMPOSITED}
        assert painted == {}, f"painted on every frame: {painted}"
        assert {state for _, state, top, _, _ in loops if top} == {"running"}, "the hero stands still"
        assert {state for _, state, _, how, _ in loops if how} == {"paused"}, "How it works runs out of sight"
        page.evaluate("document.getElementById('how').scrollIntoView()")
        for _ in range(30):
            states = page.evaluate("""() => [...new Set(document.getAnimations()
              .filter((a) => a.effect.target.closest('#how')).map((a) => a.playState))]""")
            if states == ["running"]:
                break
            page.wait_for_timeout(100)
        assert states == ["running"], f"How it works, on the screen: {states}"

        _sign_up(page, base, email, capsys)
        _onboard(page)
        page.get_by_role("dialog", name="Get the Mahoraga app").get_by_role("button", name="Got it").click()
        page.get_by_role("button", name="More", exact=True).click()
        page.evaluate("""() => { window.fading = new Promise((done) => new MutationObserver((_, watch) => {
            watch.disconnect();
            requestAnimationFrame(() => done(document.getAnimations()
              .filter((a) => a instanceof CSSTransition).map((a) => a.transitionProperty)));
          }).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]})); }""")
        page.get_by_role("dialog", name="More").get_by_role("switch", name="Dark mode").click()
        assert page.evaluate("window.fading") == [], "the colours faded under the growing circle"
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(page.locator("html")).not_to_have_class(re.compile("theming"))
        browser.close()


def test_the_discord_is_under_the_landing_pages_question_and_at_its_foot_and_in_help_and_more(site, capsys):
    """Owner, 2026-11-18: "we need to add socials to the site in general, on the landing page where its
    appropriate like at the bottom, and somewhere in the app as well, so users can join and share their
    thoughts as well as their requests to the devs". The owner's Discord: under the landing page's question
    and in its foot, and in the app in More and in Help, for every plan, Free too. Each opens in a tab of its
    own. The site's Facebook, TikTok and Instagram are in the foot and nowhere else (owner, 2026-11-23: "they
    don't need to be mentioned anywhere else")."""
    from libs.settings import DEFAULT_SOCIALS

    base, email, _ = site
    discord = DEFAULT_SOCIALS["discord"]
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                   is_mobile=True).new_page()
        page.goto(f"{base}/")
        foot = page.get_by_role("contentinfo")
        for link, href in ((page.locator("#ask").get_by_role("link", name="Join our Discord"), discord),
                           *((foot.get_by_role("link", name=name, exact=True), DEFAULT_SOCIALS[name.lower()])
                             for name in ("Discord", "Facebook", "TikTok", "Instagram"))):
            expect(link).to_have_attribute("href", href)
            expect(link).to_have_attribute("target", "_blank")
        expect(page.locator("#ask .tile.community")).to_have_count(1)
        elsewhere = "a[href*='facebook'], a[href*='tiktok'], a[href*='instagram']"
        expect(page.locator("main").locator(elsewhere)).to_have_count(0)
        for width in (320, 390):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the landing page at {width}px"

        _sign_up(page, base, email, capsys, member=False)
        _onboard(page)
        # The bar's Discord button on every page; where linking is not set up, joining alone behind it.
        page.get_by_role("button", name="Discord", exact=True).click()
        sheet = page.get_by_role("dialog", name="Join us on Discord")
        expect(sheet.get_by_role("link", name="Join our Discord")).to_have_attribute("href", discord)
        page.keyboard.press("Escape")
        page.get_by_role("button", name="More", exact=True).click()
        page.get_by_role("dialog", name="More").get_by_role("button", name="Help and contact").click()
        help_sheet = page.get_by_role("dialog", name="Help and contact")
        expect(help_sheet.get_by_role("link", name="Join our Discord")).to_have_attribute("href", discord)
        expect(page.locator(elsewhere)).to_have_count(0)
        browser.close()


def test_a_person_links_their_discord_from_the_bar_and_then_joins(site, capsys, monkeypatch):
    """Owner, 2026-11-19: "a solid way to make sure people do not miss the fact that there is a discord on
    the app ... link their discord first through the app, and then they can join the group after, so we can
    assign proper roles for them". The Discord button is in a phone's bar, fitting the smallest; behind it,
    step one links on Discord's own page (here a stand-in that says yes), and they are back in Settings, told
    it worked, with joining the next step."""
    import httpx

    from services.api import discord_routes

    monkeypatch.setenv("DISCORD_CLIENT_ID", "discord-client-1")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "discord-secret-1")

    def discord_api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-1", "token_type": "Bearer"})
        return httpx.Response(200, json={"id": "111111111111111111", "username": "jordan_e",
                                         "global_name": "Jordan E"})

    monkeypatch.setattr(discord_routes, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(discord_api)))
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                   is_mobile=True).new_page()
        _sign_up(page, base, email, capsys, member=False)
        _onboard(page)
        bar = page.locator(".app > nav")
        expect(bar.get_by_role("button", name="Discord", exact=True)).to_be_visible()
        for width in (320, 360, 390):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the bar with Discord at {width}px"

        # Discord's page, saying yes: straight back to the address it was given, with a code and the state.
        def says_yes(route) -> None:
            asked = parse_qs(urlsplit(route.request.url).query)
            assert asked["scope"] == ["identify"]
            route.fulfill(status=302, headers={"location": asked["redirect_uri"][0]
                                               + f"?code=code-1&state={asked['state'][0]}"})

        page.route("https://discord.com/oauth2/authorize**", says_yes)
        bar.get_by_role("button", name="Discord", exact=True).click()
        sheet = page.get_by_role("dialog", name="Join us on Discord")
        expect(sheet.get_by_role("link", name="Join our Discord")).to_be_visible()
        sheet.get_by_role("button", name="Link my Discord").click()

        expect(page.get_by_text("Your Discord is linked.")).to_be_visible()
        card = page.locator("section.card").filter(has=page.get_by_role("heading", name="Discord"))
        expect(card).to_contain_text("Linked as Jordan E")
        expect(card.get_by_role("link", name="Join our Discord")).to_have_class(re.compile(r"\bmain\b"))
        assert "discord=" not in page.url, "said once: a reload says nothing"
        browser.close()


def test_the_landing_page_counts_from_the_server_and_a_phone_shows_each_place_in_a_picture_and_a_word(
        site, capsys):
    """The landing page's numbers are the server's (`/stats`): typed into it, they said 25 sources long
    after that was true. A big one is said as the round number it is at least, so it reads at a glance
    and stays true (owner, 2026-11-07). The promise is the biggest words on the page, and How it works says
    what the app does for them rather than how they sign up; the five market figures are gone (owner,
    2026-11-17). Sign in and sign up follow the address, going back included. And on a phone the bar is an
    app's (owner, 2026-11-17): four places and More, each a picture over its word, the rest in More's sheet,
    and a tap shows no box of the phone's own. Its card says where people apply: on the job's own site, never
    here (owner, 2026-11-06)."""
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
        # The promise first, in the biggest words, then what it is; and the main button says what starting
        # costs (owner, 2026-11-17).
        expect(page.locator("h1")).to_have_text("Get seen first.")
        expect(page.locator(".hero")).to_contain_text("The job search that runs itself.")
        expect(page.locator(".hero")).to_contain_text("No card needed.")
        # No plan named outside the cards, no way an alert is sent, and never how often the sites are read
        # (owner, 2026-11-17 late night: "people can tell by the offers", "confidential").
        said = page.evaluate("[...document.querySelectorAll('main section:not(#price)')]"
                             ".map((part) => part.textContent).join(' ').toLowerCase()")
        for unsaid in ("paid plan", "as a member", "free plan", "telegram", "every 15 minutes"):
            assert unsaid not in said, f"{unsaid!r} is said outside the cards"
        # Its examples are work anyone does: every one was a software job until 2026-11-14 (owner).
        software = re.compile("python|backend|engineer|kubernetes", re.I)
        expect(page.locator("main")).not_to_contain_text(software)
        demo = page.locator(".demo-card")
        demo.get_by_role("button", name="Apply on LinkedIn").click()
        expect(demo).to_contain_text("opens it on LinkedIn, and you apply there")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # Sign in and Start free up top on a phone too, inside the smallest phone's bar (owner, 2026-11-18:
        # "on the phone we have a sign in button on the top but no 'start free' button").
        top = page.get_by_role("banner")
        expect(top.get_by_role("link", name="Start free")).to_be_visible()
        expect(top.get_by_role("link", name="Sign in")).to_be_visible()
        for width in (320, 360, 390):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the bar at {width}px"
        # The sections at the foot of a phone: a picture over each one's word (owner, 2026-11-07).
        expect(page.locator(".dock a").filter(has=page.locator(".short"))).to_have_text(
            ["What", "How", "See", "Price", "Start"], use_inner_text=True)
        # Each section says what it is in a big title of its own, where a small label stood (owner,
        # 2026-11-07). What it is, then how it works for them: what it does, not the sign-up (owner,
        # 2026-11-17).
        expect(page.locator("main section h2")).to_have_text([
            "Every job site. One list for you.", "You apply. We do the rest.", "See it working.",
            "Search smarter. Beat the crowd. Join now.", "Start in a few minutes.",
            "Got a question? Ask us."])
        expect(page.locator("#how h3")).to_have_text([
            "New jobs in minutes, not days", "Instant alerts on your phone", "A match score on every job",
            "Save jobs for later", "A CV made for each job, in seconds", "Every application tracked"])
        # The offer is said on the cards, not again under the title (owner, 2026-11-16: "only keep them inside
        # the offers"): the title stands alone.
        expect(page.locator("#price .head")).to_have_text("Search smarter. Beat the crowd. Join now.")
        # Our own figure on the CV it is about, alone (owner, 2026-11-17 night), and whose AI writes it.
        expect(page.locator("#how")).to_contain_text("99% passed all official ATS checks.")
        expect(page.locator("#how")).not_to_contain_text("our own tests")
        # What an alert follows, as the Alerts page makes one (owner, 2026-11-17: "something specific on the
        # landing page of what we just made").
        expect(page.locator("#how")).to_contain_text(
            "Follow a company, a city or a kind of job, and know the moment one is posted.")
        expect(page.locator("#how")).to_contain_text(
            "CVs and cover letters are written with your own ChatGPT or Claude account.")
        # A review nobody wrote never reaches a visitor: the section waits for the first real one
        # (`web/src/landing/reviews.ts`).
        expect(page.locator("[data-sample]")).to_have_count(0)
        expect(page.locator(".reviews")).to_have_count(0)

        page.goto(f"{base}/#/signin")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        page.goto(f"{base}/#/signup")
        expect(page.get_by_role("heading", name="Create your account")).to_be_visible()
        page.go_back()
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()

        _sign_up(page, base, email, capsys)
        _onboard(page)
        _a_phones_bar(page)
        browser.close()


def _a_phones_bar(page) -> None:
    """An app's (owner, 2026-11-17): four places and More, each a picture over its word, the rest in More's
    sheet, More lit on one of them, and no box of the phone's own where a tab is tapped."""
    bar = page.locator("nav .places > :is(a, button):visible")
    expect(bar).to_have_text(["Jobs", "Applied", "Docs", "Alerts", "More"], use_inner_text=True)
    expect(bar.locator("svg")).to_have_count(5)
    assert bar.evaluate_all("tabs => tabs.map((tab) => getComputedStyle(tab).webkitTapHighlightColor)") \
        == ["rgba(0, 0, 0, 0)"] * 5, "a tap draws the phone's own box"
    page.get_by_role("button", name="More", exact=True).click()
    more = page.get_by_role("dialog", name="More")
    expect(more.locator(".more-row")).to_have_text(
        ["My search", "Settings", "Help and contact", "Dark mode"], use_inner_text=True)
    more.get_by_role("link", name="My search").click()
    expect(page.get_by_role("heading", name="The jobs you want", level=1)).to_be_visible()
    expect(more).to_have_count(0)
    expect(page.get_by_role("button", name="More", exact=True)).to_have_class(re.compile(r"\bon\b"))


def test_a_visitor_asks_a_person_a_question_from_the_foot_of_the_landing_page(site, capsys):
    """Signed out, a question goes where the Help sheet's form sends one: kept, and in the support inbox with
    the address to reply to (owner, 2026-11-17 night: "to the same place where we have inside the app"). The
    way there is the footer's last link, where the owner's own site was linked."""
    from libs.db import database

    base, email, _ = site
    asker = email.replace("jordan.", "sam.")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(f"{base}/#/")
        expect(page.locator(".foot-row a")).to_have_text(
            ["Sign in", "Terms", "Privacy", "Cookies", "Data use", "Contact"])
        page.locator(".foot-row").get_by_role("link", name="Contact").click()
        ask = page.locator("#ask")
        expect(ask.get_by_role("heading", name="Got a question? Ask us.")).to_be_in_viewport()
        send = ask.get_by_role("button", name="Send my question")
        expect(send).to_be_disabled()
        ask.get_by_label("Your email").fill(asker)
        ask.get_by_label("Your question").fill("Do I need a paid AI account to make a CV?")
        send.click()
        expect(ask).to_contain_text("Message received")
        expect(ask).to_contain_text(f"We'll reply to {asker}.")
        browser.close()
    assert "Do I need a paid AI account" in capsys.readouterr().out, "the support inbox has it"
    assert database()["contact_messages"].find_one({"email": asker})["user_id"] is None


def test_the_legal_pages_are_plain_text_from_the_foot_and_say_who_runs_the_site(site):
    """Terms, Privacy, Cookies and Data use, each a page of its own from the landing page's foot, the
    operator's details as Admin has them. Plain documents (owner, 2026-11-18: "just really simple text on the
    page"): no link to the other legal pages, no contents, no buttons, only the way back; and nothing off a
    phone's screen, tables included."""
    from libs import settings
    from libs.db import database

    settings.put(database(), "operator", {"name": "Example Kft.", "address": "1111 Budapest, Példa utca 1."})
    base, _email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        for width, height in ((390, 844), (1280, 900)):
            page = browser.new_context(viewport={"width": width, "height": height}).new_page()
            for link, title, shown in (("Terms", "Terms of Service", "Name: Example Kft."),
                                       ("Privacy", "Privacy Policy", "Cloudflare"),
                                       ("Cookies", "Cookie Policy", "install-later"),
                                       ("Data use", "Data Use Policy", "Your data is yours")):
                page.goto(f"{base}/#/")
                page.locator(".foot-row").get_by_role("link", name=link).click()
                doc = page.locator("main.legal")
                expect(doc.get_by_role("heading", level=1)).to_have_text(title)
                expect(doc).to_contain_text(shown)
                expect(doc.locator('a[href="/"]')).to_have_text(["Back"])
                expect(page.get_by_role("button")).to_have_count(0)
                expect(page.locator("nav")).to_have_count(0)
                assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"{title} at {width}px"
            page.goto(f"{base}/#/terms")
            expect(page.locator("main.legal")).to_contain_text("Registration number: [not filled in yet]")
            page.locator("main.legal").get_by_role("link", name="Back").click()
            expect(page.locator(".foot-row")).to_be_attached()
        browser.close()


def test_the_phone_goes_through_the_site_by_itself_until_a_visitor_touches_it(site):
    """Owner, 2026-11-12: in "See it working" the phone makes a CV on the Jobs page, then shows Applied, and
    new alerts arrive on top, pushing the older ones down. It goes as somebody using the site does (owner,
    2026-11-17: "more animations to it ... so users understand the app more"): the job bookmarked, marked as
    applied, and heard back from on Applied, scrolling its own page to each, never the visitor's. The
    visitor's own touch ends the tour and the phone is theirs; with reduced motion nothing moves."""
    base, _, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"{base}/#/")
        page.locator(".phone").scroll_into_view_if_needed()
        on = page.locator(".phone-tabs .on")
        expect(page.locator(".phone .paper")).to_be_visible(timeout=8000)
        where = page.evaluate("scrollY")
        expect(page.locator(".phone .demo-card .bookmark")).to_have_attribute("aria-pressed", "true",
                                                                               timeout=8000)
        expect(page.locator(".phone .job-card.gone")).to_contain_text("Moved to Applied", timeout=8000)
        expect(on).to_have_attribute("data-tab", "applied", timeout=8000)
        # It hears back on Applied, and the first application moves along its track (owner, 2026-11-17).
        track = page.locator(".phone .progress ol").first
        expect(track.locator("li.done")).to_have_text(["Applied", "Interview"], timeout=6000)
        # My search, which is under More, as in the app (owner, 2026-11-17).
        expect(on).to_have_attribute("data-tab", "more", timeout=8000)
        expect(page.locator(".phone").get_by_text("The jobs you want")).to_be_visible()
        assert page.evaluate("scrollY") == where, "the tour moved the visitor's page"
        titles = page.locator(".alert-title")
        newest = titles.first.inner_text()
        expect(titles.nth(1)).to_have_text(newest, timeout=6000)

        page.locator(".phone-tabs [data-tab=more]").click()
        expect(on).to_have_attribute("data-tab", "more")
        page.wait_for_timeout(5000)
        expect(on).to_have_attribute("data-tab", "more")

        still = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        page = still.new_page()
        page.goto(f"{base}/#/")
        page.locator(".phone").scroll_into_view_if_needed()
        page.wait_for_timeout(5000)
        expect(page.locator(".phone-tabs .on")).to_have_attribute("data-tab", "jobs")
        expect(page.locator(".phone .paper")).to_have_count(0)
        expect(page.locator(".alert-slot")).to_have_count(3)
        browser.close()


def _says_more(line, words: str, *, tap: bool = False) -> None:
    """A card's line says more when its question mark is pointed at, or tapped on a phone, which has nothing
    to point with: `words`, unseen until then."""
    said = line.locator(".explain-text")
    expect(said).to_be_hidden()
    (line.locator(".explain-mark").click if tap else line.locator(".explain-mark").hover)()
    expect(said).to_be_visible()
    expect(said).to_have_text(words)


# What Free's card crosses out as the defaults pick it, four of the most telling things it lacks (owner,
# 2026-11-17: "3-4 negatives to each offer except member, make them powerful and the most important").
FREE_CROSSED = ["See new jobs right away", "Job alerts sent to you", "Match score on every job",
                "A CV made for each job"]


def _one_line_each(page) -> None:
    """Every line of every pricing card on one line, its question mark beside it (owner, 2026-11-16: "so it
    doesn't bump on the lines"): its words in one box, and they and the mark no taller than one line. A
    bonus's tag has a row of its own above them."""
    page.evaluate("document.fonts.ready.then(() => true)")
    broken = page.evaluate("""() => [...document.querySelectorAll('.price-list li .line')].filter((words) => {
        const tall = parseFloat(getComputedStyle(words.closest('li')).lineHeight) * 1.5;
        return words.getClientRects().length > 1 || words.parentElement.getBoundingClientRect().height > tall;
    }).map((words) => words.textContent)""")
    assert broken == [], f"on two lines at {page.viewport_size['width']}px: {broken}"


def _an_admin_words_the_cards(page, lines: dict) -> list[str]:
    """Admin's editor: a card's own words, a line of what the plans give and what a card crosses out, every
    one of them there to change and never typed from nothing (owner, 2026-11-17), the preview following
    before anything is saved. What Free's card crosses out then."""
    expect(page.get_by_role("heading", name="Who is on which plan")).to_be_visible()
    editor = page.locator(".pricing-admin")
    editor.get_by_role("button", name="Member", exact=True).click()
    editor.get_by_label("Name", exact=True).fill("Mahoraga Plus")
    editor.get_by_label("Small print under the button").fill("Help from a person, any time.")
    expect(editor.locator(".price-name").last).to_have_text("Mahoraga Plus")
    alerts = editor.get_by_role("group", name="Alerts").get_by_label("Line")
    expect(alerts).to_have_count(len(lines["areas"]["alerts"]))
    expect(alerts.first).to_have_value(lines["areas"]["alerts"][0]["text"])
    alerts.first.fill("Alerts on your phone")
    # Crossed out on Free, whose plan has no alerts, as the server works it out.
    expect(editor.locator(".price-card").first.locator(".price-list li.out .line").nth(1)).to_have_text(
        "Alerts on your phone")
    bonus = editor.get_by_role("group", name="Given as a bonus on this card")
    expect(bonus.get_by_role("switch", name=lines["areas"]["ats_checks"][0]["text"])).to_be_checked()
    # What a card crosses out is picked here, four at most (owner, 2026-11-17: "3-4 negatives").
    editor.get_by_role("group", name="Card").get_by_role("button", name="Free", exact=True).click()
    crosses = editor.get_by_role("group", name="Crossed out on this card")
    covers = crosses.get_by_role("switch", name=lines["areas"]["covers"][0]["text"])
    expect(covers).to_be_disabled()
    crosses.get_by_role("switch", name=lines["areas"]["cvs"][0]["text"]).uncheck()
    covers.check()
    crossed = [FREE_CROSSED[0], "Alerts on your phone", FREE_CROSSED[2], lines["areas"]["covers"][0]["text"]]
    expect(editor.locator(".price-card").first.locator(".price-list li.out .line")).to_have_text(crossed)
    editor.get_by_role("button", name="Save the cards").click()
    expect(editor.get_by_role("status")).to_have_text("Saved. The landing page shows it now.")
    return crossed


def test_the_landing_page_offers_free_and_member_and_an_admin_words_both(site, capsys):
    """Two cards while Starter has no price: Free, each job once it is a week old, with four of the most
    telling things it lacks crossed out and read out as not included, and no card asked for; and Member,
    everything in Free and what it adds, the ATS check apart in a box as a bonus, its free days on a tab,
    which signs up for the membership. Each line a few words, with a question mark that says more when
    pointed at or tapped. The price is Stripe's: none here, where Stripe is not set up, rather than a wrong
    one. An admin changes the card's own words and what it crosses out, watching them as a visitor will see
    them, and the landing page says them, on the smallest phone too (owner, 2026-11-12 to 2026-11-17)."""
    from libs.db import database
    from libs.settings import DEFAULT_PRICING_CARDS, DEFAULT_PRICING_LINES

    base, email, _ = site
    free, member = DEFAULT_PRICING_CARDS["free"], DEFAULT_PRICING_CARDS["member"]
    lines = DEFAULT_PRICING_LINES
    # Ticked, every line of what a plan gives but its bonus, which is said apart; crossed out, the few things
    # picked for the card, each by its first line (owner, 2026-11-17: "3-4 negatives to each offer").
    paid = [line["text"] for area in ("alerts", "match_scores", "cvs", "covers", "add_job")
            for line in lines["areas"][area]]
    bonus = [line["text"] for area in member["bonus"] for line in lines["areas"][area]]
    lacked = [lines["areas"][area][0]["text"] for area in free["crossed"] if area != "early_jobs"]
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"{base}/#/")
        cards = page.locator(".price-card")
        expect(cards.locator(".price-name")).to_have_text([free["name"], member["name"]])
        expect(cards.first.locator(".price-list li:not(.out) .line")).to_have_text(
            ["See new jobs after 7 days", *(line["text"] for line in lines["everyone"])])
        crossed = cards.first.locator(".price-list li.out")
        expect(crossed.locator(".line")).to_have_text([lines["now"]["text"], *lacked])
        expect(crossed.locator(".unseen")).to_have_text(["Not included:"] * (1 + len(lacked)))
        expect(cards.last.locator(".price-badge")).to_have_text("7 days free")
        expect(cards.last.locator(".price-list li .line")).to_have_text(
            ["Everything in Free plan", lines["now"]["text"], *paid, *bonus])
        # The bonus in a box of its own that says it is one (owner, 2026-11-17: "show that properly").
        given = cards.last.locator(".price-list li.bonus")
        expect(given.locator(".bonus-tag")).to_have_text(["Bonus"] * len(bonus))
        expect(given.locator(".line")).to_have_text(bonus)
        _says_more(cards.last.locator(".price-list li").nth(2), lines["areas"]["alerts"][0]["more"])
        expect(page.locator(".price-amount")).to_have_count(0)
        _one_line_each(page)
        expect(cards.first.get_by_role("link", name=free["button"])).to_have_attribute("href", "/signup")
        expect(cards.last.get_by_role("link", name=member["button"])).to_have_attribute(
            "href", "/signup?plan=member")

        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.goto(f"{base}/#/admin/subscriptions")
        crossed = _an_admin_words_the_cards(page, lines)

        visitor = browser.new_context(viewport={"width": 320, "height": 640}).new_page()
        visitor.goto(f"{base}/#/")
        expect(visitor.locator(".price-name")).to_have_text([free["name"], "Mahoraga Plus"])
        expect(visitor.locator(".price-card").last.locator(".price-note")).to_have_text(
            "Help from a person, any time.")
        expect(visitor.locator(".price-card").last.locator(".price-list li .line").nth(2)).to_have_text(
            "Alerts on your phone")
        expect(visitor.locator(".price-card").first.locator(".price-list li.out .line")).to_have_text(crossed)
        tapped = visitor.locator(".price-card").last.locator(".price-list li").nth(1)
        _says_more(tapped, lines["now"]["more"], tap=True)
        box = tapped.locator(".explain-text").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, f"past a small phone's edge: {box}"
        assert visitor.evaluate("document.documentElement.scrollWidth") <= 320, "past a small phone's edge"
        _one_line_each(visitor)
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
        expect(steps.get_by_role("listitem")).to_have_text(["Your CV", "About you", "Your search", "Done"])
        expect(steps.locator("[aria-current=step]")).to_have_text("Your search")
        for title in ("Your name and links", "The jobs you want", "Where and how you work"):
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
        expect(page.get_by_text("At least 3, more is better. Like “nurse” or “excel”.")).to_be_visible()
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


def test_setting_up_shows_the_cover_letter_and_asks_about_you_on_a_screen_of_its_own(site, db, capsys):
    """Owner, 2026-11-17: the cover letter is offered on the CV's screen, not folded away, and About you is a
    screen of its own that says what it is for, which can be skipped (asked: "can skip"). What is written
    there is read at once: a job title boards use, written in it, is offered on the very next screen."""
    from libs.suggestions import DRIFT_MIN

    base, email, _ = site
    # A title enough postings here use to be offered when it is written (`libs.suggestions._written`).
    _jobs(db, uuid.uuid4().hex[:8], ["Registered Nurse"] * DRIFT_MIN)
    wrote = "Next I want to work as a registered nurse, nights included."
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)

        expect(page.locator("label.drop", has_text="Upload a cover letter")).to_be_visible()
        page.locator("label.drop", has_text="Upload your CV").locator("input").set_input_files(
            files=[{"name": "cv.txt", "mimeType": "text/plain", "buffer": CV.encode()}])
        expect(page.get_by_text("cv.txt")).to_be_visible()
        page.get_by_role("button", name="Next").click()

        expect(page.get_by_role("heading", name="About you")).to_be_visible()
        steps = page.get_by_role("list", name="Set up your profile")
        expect(steps.locator("[aria-current=step]")).to_have_text("About you")
        tops = steps.get_by_role("listitem").evaluate_all("items => items.map((item) => item.offsetTop)")
        assert len(tops) == 4 and len(set(tops)) == 1, f"the four steps are not on one line: {tops}"
        # What it is for, naming only what their plan gives: a member's, everything (owner, 2026-11-22).
        expect(page.get_by_text("We use it to score every job for you, find more jobs that fit you and write "
                                "your CVs and cover letters.")).to_be_visible()
        expect(page.locator(".word-meter")).to_have_text("Write more than 150 words.")
        save = page.get_by_role("button", name="Save and continue")
        expect(save).to_be_disabled()
        page.get_by_label("In your own words").fill(wrote)
        save.click()

        expect(page.get_by_role("heading", name="What we found")).to_be_visible()
        expect(page.locator(".suggestion", has_text="registered nurse")).to_be_visible()
        page.get_by_role("button", name="← About you").click()
        expect(page.get_by_label("In your own words")).to_have_value(wrote)
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, "About you at 390px"
        browser.close()


def test_about_you_is_asked_for_until_it_says_enough(site, capsys):
    """Owner, 2026-11-22: About you empty, or 150 words or fewer, and the site keeps asking, saying why: a
    red dot on My documents, a note pinned at the top of the bell that takes them to the box, ready to type
    in, and one over the box. The box counts the words as they are typed. Saved past 150, all of them go."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        dot = page.locator("nav .places a", has_text="My documents").locator(".place-dot")
        expect(dot).to_be_visible()

        # The bell's note: unread, with no × to put it away, and a link to the box.
        page.locator(".bell-wrap > button.bell").click()
        note = page.get_by_role("dialog", name="Notifications").locator(".bell-row.pinned")
        expect(note).to_contain_text("Tell us about yourself")
        expect(note).to_contain_text("We use it to score every job for you, find more jobs that fit you and")
        expect(note.locator(".bell-x")).to_have_count(0)
        note.get_by_role("link", name=re.compile("Write it now")).click()
        box = page.get_by_role("textbox", name="About you")
        expect(box).to_be_focused()
        expect(page.locator(".about-nudge")).to_contain_text("Tell us about yourself")

        form, meter = page.locator("form", has=box), page.locator(".word-meter")
        box.fill("I run night shifts in a warehouse. " * 10)
        expect(meter).to_have_text("70 words. Write more than 150.")
        form.get_by_role("button", name="Save").click()
        expect(page.locator(".about-nudge")).to_contain_text("You have written 70 words.")
        expect(dot).to_be_visible()

        box.fill("I run night shifts in a warehouse. " * 25)
        expect(meter).to_contain_text("175 words. That is enough to work with.")
        form.get_by_role("button", name="Save").click()
        expect(page.locator(".about-nudge")).to_have_count(0)
        expect(dot).to_have_count(0)
        page.locator(".bell-wrap > button.bell").click()
        expect(page.get_by_role("dialog", name="Notifications").locator(".bell-row.pinned")).to_have_count(0)
        browser.close()


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
    from libs.settings import HARVEST_SOURCES

    # Every source on the one list, and the jobs whose source was never recorded: 20 when it was typed here,
    # and wrong the day three sources were added (2026-11-22).
    boards = len(HARVEST_SOURCES) + 1
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
        page.locator("summary", has_text=f"All {boards}").click()
        sources = page.locator(".country-table").filter(has_text="Source")
        expect(sources.get_by_role("checkbox")).to_have_count(boards + 1)
        for box in sources.get_by_role("checkbox").all():
            expect(box).to_be_checked()
        expect(sources.locator("tr", has_text="LinkedIn")).to_contain_text("8")
        sources.get_by_role("checkbox", name="Not recorded").uncheck()
        expect(total).to_have_text("8")
        expect(page.locator("summary", has_text=f"{boards - 1} of {boards}")).to_be_visible()
        sources.get_by_role("checkbox", name="All sources").check()
        expect(total).to_have_text(f"{everything:,}")
        sources.get_by_role("checkbox", name="All sources").uncheck()
        expect(page.get_by_text("No source is ticked.")).to_be_visible()
        browser.close()


def test_admin_shows_every_sources_controls_at_the_values_the_harvest_reads(site, capsys):
    """Every source has its row in Admin → Settings, each control at the value a run reads. The choices for
    pages went to 20 and no further, so Platsbanken's and No Fluff Jobs' 10 were shown as something the
    harvest does not read (2026-11-23)."""
    from libs.settings import DEFAULT_SOURCE_FRESHNESS_HOURS, DEFAULT_SOURCE_PAGES_PER_CYCLE

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = _admin_corpus(browser, site, capsys)
        page.get_by_role("button", name="Settings").click()
        for name in ("arbeitnow", "remoteok", "platsbanken", "reed"):
            expect(page.get_by_label(f"{name} freshness")).to_have_value(
                str(DEFAULT_SOURCE_FRESHNESS_HOURS[name]))
        for name in ("platsbanken", "nofluffjobs", "arbeitnow", "reed"):
            expect(page.get_by_label(f"{name} pages per cycle")).to_have_value(
                str(DEFAULT_SOURCE_PAGES_PER_CYCLE[name]))
        browser.close()


def test_no_page_of_the_site_asks_for_anything_its_own_policy_refuses(site, capsys):
    """The site tells every browser what a page of it may load and run (`services/api/guard.py`), and a
    browser refuses the rest without a word to anybody but its console: a script the page wrote into itself,
    a picture from somewhere not named. Every place is walked, signed out and in, a look's picture drawn and
    an admin's tabs opened, and no browser was given cause to refuse anything (2026-11-20)."""
    from libs.db import database

    base, email, _ = site
    refused: list[str] = []
    asking: set = set()

    def heard(message) -> None:
        if "Content Security Policy" in message.text or "Refused to" in message.text:
            refused.append(message.text)

    def asked(request) -> None:
        # Not the Pipeline tab's stream, which is held open for as long as the tab is.
        if not request.url.endswith("/admin/pipeline/stream"):
            asking.add(request)

    def answered(request) -> None:
        asking.discard(request)

    def left(frame) -> None:
        if frame.parent_frame is None:
            asking.clear()

    def drawn(page) -> None:
        """Until the page has asked for everything it is going to and has had it: nothing on its way, three
        looks running, a twentieth of a second apart. At most ten seconds, which fails here and not later."""
        quiet = 0
        for _ in range(200):
            page.wait_for_timeout(50)
            quiet = 0 if asking else quiet + 1
            if quiet == 3:
                return
        raise AssertionError(f"still waiting on {sorted(request.url for request in asking)}")

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("console", heard)
        page.on("request", asked)
        page.on("requestfinished", answered)
        page.on("requestfailed", answered)
        # A page loaded again drops what the last one was waiting for, and nothing says so of each.
        page.on("framenavigated", left)
        page.goto(f"{base}/")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        for place in ("signin", "privacy", "terms"):
            page.goto(f"{base}/#/{place}")
            drawn(page)
        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.reload()
        for place in ("", "applied", "words", "alerts", "documents", "settings", "upgrade", "add",
                      "bookmarks", "admin/health", "admin/settings", "admin/corpus", "admin/latency",
                      "admin/accounts", "admin/subscriptions", "admin/outcomes", "admin/messages",
                      "admin/pipeline"):
            page.goto(f"{base}/#/{place}")
            drawn(page)
        expect(page.get_by_text("Found and stored")).to_be_visible()
        browser.close()

    assert refused == []


def test_admins_accounts_come_a_page_at_a_time_and_an_address_finds_one(site, capsys):
    """Every account was read and drawn on each open of Admin, whichever tab was asked for. The Accounts tab
    reads a page of them, says how many there are, and finds one by a piece of its address (2026-11-20)."""
    from datetime import timedelta

    from libs.db import database, insert, now
    from libs.db.models import User
    from services.api.admin_routes import USERS_PAGE

    base, email, _ = site
    for at in range(USERS_PAGE):  # joined after the admin, who is the first on the first page
        insert(database(), User(email=f"member{at:03}@example.org", password_hash="x", status="active",
                                created_at=now() + timedelta(days=1, seconds=at)))
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.goto(f"{base}/#/admin/accounts")
        rows = page.locator("tr.row-link")

        expect(page.get_by_text(f"{USERS_PAGE + 1} accounts.")).to_be_visible()
        expect(rows).to_have_count(USERS_PAGE)
        expect(rows.first).to_contain_text(email)

        page.get_by_role("button", name="Next").click()
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text(f"member{USERS_PAGE - 1:03}@example.org")

        page.get_by_role("searchbox", name="Find an account by its email address").fill("member007")
        expect(page.get_by_text("1 account with this in their address.")).to_be_visible()
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("member007@example.org")
        browser.close()


def test_two_parts_of_a_page_that_read_one_address_ask_for_it_once(site, capsys):
    """The Subscriptions tab's figures and its pricing editor both draw the prices as visitors see them, and
    each asked the server for them, as a page and the read-ahead of the same address did. Whoever asks for a
    read already on its way gets its answer (`kept.ts`, 2026-11-20)."""
    from urllib.parse import urlparse

    from libs.db import database

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.reload()
        expect(page.locator("nav .places a", has_text="Admin")).to_be_visible()
        asked: list[str] = []
        page.on("request", lambda request: asked.append(urlparse(request.url).path))

        page.goto(f"{base}/#/admin/subscriptions")
        expect(page.locator(".pricing-admin")).to_be_visible()

        assert asked.count("/pricing") == 1, asked
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

        page.route(re.compile(
            r"/(applied|jobs/added|onboarding/\w+|documents|saved|templates|account|ai-accounts|feed|alerts)"),
            slow)
        for tab, drawn in (("Applied", "You applied to 0 jobs"), ("My search", "The jobs you want"),
                           ("Alerts", "Hear about a job the moment it appears"),
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
        # My documents before Alerts: the owner's order (2026-11-22).
        expect(tabs).to_have_text(["Jobs", "Applied", "My search", "My documents", "Alerts", "Settings"],
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
        _help_with_the_members_chat(page)
        page.get_by_text("How are my jobs chosen?").click()
        expect(page.get_by_text("A job shows up when one of your green words is in it")).to_be_visible()
        page.get_by_label("Subject").fill("My CV shows the wrong city")
        page.get_by_label("Message").fill("It says Berlin, but I moved to Paris last month.")
        page.get_by_role("button", name="Send message").click()
        expect(page.get_by_role("heading", name="Message received")).to_be_visible()
        expect(page.get_by_text(f"We will reply to {email}.")).to_be_visible()

        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.reload()
        expect(tabs).to_have_text(
            ["Jobs", "Applied", "My search", "My documents", "Alerts", "Admin", "Settings"],
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

        # The smallest phone: the four tabs and More fit, each a whole word, an admin's Admin is in More
        # (owner, 2026-11-17), and the bar up top is at the very top of the screen (owner, 2026-11-07: a
        # phone's page began 16px down).
        page.set_viewport_size({"width": 320, "height": 640})
        page.goto(f"{base}/#/")
        expect(page.locator("nav .places > :is(a, button):visible")).to_have_text(
            ["Jobs", "Applied", "Docs", "Alerts", "More"], use_inner_text=True)
        assert page.evaluate("""() => [...document.querySelectorAll("nav .places > :is(a, button)")]
            .filter((tab) => tab.getClientRects().length).every((tab) => {
                const word = tab.querySelector(".place-short");
                return word.scrollWidth <= word.clientWidth
                    && tab.getBoundingClientRect().right <= innerWidth;
            })"""), "a word cut short, or a tab past the screen's edge"
        page.get_by_role("button", name="More", exact=True).click()
        expect(page.get_by_role("dialog", name="More").locator(".more-row")).to_have_text(
            ["My search", "Admin", "Settings", "Help and contact", "Dark mode"],
            use_inner_text=True)
        page.keyboard.press("Escape")
        page.evaluate("scrollTo(0, 0)")
        assert page.locator(".app > nav").bounding_box()["y"] == 0, "space above the bar"
        browser.close()


def test_job_cards_carry_none_of_the_description_and_sit_two_to_a_row_on_a_laptop(site, capsys):
    """One column on a phone, two on a laptop, and none of a job's text on its card (owner, 2026-11-21:
    "remove the jobs descriptions, that would make the jobs appear cleaner too")."""
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
        expect(cards.first).not_to_contain_text("Python, Postgres")

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

        expect(card()).not_to_have_class(re.compile(r"\bmade-cv\b"))
        user = database().users.find_one({"email": email})
        insert(database(), BuiltDocument(id=900_101, user_id=user["_id"], job_id=ids[TITLES[0]], kind="cv",
                                         mode="ai", filename="cv.pdf", content=b"%PDF-1.4 kept", size=13,
                                         saved=True, created_at=now()))

        page.reload()
        expect(card().get_by_role("link", name="Download CV")).to_be_visible()
        expect(card()).to_have_class(re.compile(r"\bmade-cv\b"))
        expect(card().get_by_role("link", name="Download cover letter")).to_have_count(0)

        card().get_by_role("button", name="Mark as applied").click()
        expect(page.get_by_text(f"Applied: {TITLES[0]}")).to_be_visible()
        page.goto(f"{base}/#/applied")
        expect(page.get_by_role("link", name="Download CV")).to_be_visible()

        page.goto(f"{base}/#/documents")
        expect(page.get_by_text(re.compile(r"MB of 5\.0 MB used"))).to_be_visible()
        page.get_by_text("Show your 1 document").click()
        expect(page.get_by_text(f"for {TITLES[0]}")).to_be_visible()
        page.get_by_role("button", name="Delete this one").click()
        page.get_by_role("dialog", name="Delete this one?").get_by_role("button", name="Delete").click()
        expect(page.get_by_text("None yet.")).to_be_visible()
        browser.close()


def test_a_cv_being_made_is_said_on_every_page_after_a_refresh_and_on_applied_until_it_is_ready(
        site, capsys, monkeypatch):
    """Owner, 2026-11-19: a CV being made was seen only on its own button, so a refresh, or its job moving to
    Applied, made it vanish while it was still being written, and people press again. It is said above every
    page until it is ready, Applied says it too and has the Make buttons Jobs has, and ready, a notice hands
    it over with what the AI account has left, read then: a ChatGPT plan's limits as a Claude one's are."""
    import json

    from libs.db import database, insert, now
    from libs.db.models import BuiltDocument, QueueJob
    from libs.llm import accounts, limits
    from libs.secrets import SecretStore

    monkeypatch.setattr(limits, "read", lambda kind, blob, **kw: [
        limits.Window("five_hour", "5-hour limit", 37.0, None)] if kind == "codex_login" else [])
    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        db = database()
        user = db.users.find_one({"email": email})["_id"]
        chatgpt = SecretStore(db).add(user, "codex_login", json.dumps({"auth.json": "{}"}),
                                      label="ChatGPT Plus · jordan@example.com")
        accounts.use(db, user, str(chatgpt))
        # Asked for and being written, as the worker holds it: claimed just now, so this test's own worker
        # leaves it be.
        job = ids[TITLES[0]]
        build = insert(db, QueueJob(lane="docgen", user_id=user, dedup_key=f"ai:cv:{job}", state="claimed",
                                    payload={"job_id": job, "kind": "cv", "mode": "ai"},
                                    claimed_at=now(), claimed_by="elsewhere"))

        page.reload()
        making = page.locator(".making-now")
        expect(making).to_have_text(f"Making your CV for {TITLES[0]}…")
        card = page.locator("article.job-card").filter(
            has=page.get_by_role("button", name=TITLES[0], exact=True))
        expect(card.get_by_role("button", name="Making your CV…")).to_be_disabled()

        card.get_by_role("button", name="Mark as applied").click()
        page.goto(f"{base}/#/documents")
        expect(making).to_be_visible()
        page.goto(f"{base}/#/applied")
        applied = page.locator("article.job-card")
        expect(applied.get_by_role("button", name="Making your CV…")).to_be_disabled()
        expect(making).to_be_visible()
        # A job marked applied can still have its documents made, as on Jobs (owner, 2026-11-19).
        expect(applied.get_by_role("button", name="Make my cover letter")).to_be_enabled()

        # Made: what the worker writes.
        insert(db, BuiltDocument(id=build.id, user_id=user, job_id=job, kind="cv", mode="ai",
                                 filename="cv.pdf", content=b"%PDF-1.4 made", size=13, saved=True,
                                 tokens=5400))
        db[QueueJob.COLLECTION].update_one({"_id": build.id},
                                           {"$set": {"state": "done", "finished_at": now()}})

        expect(making).to_have_count(0)
        expect(applied.get_by_role("link", name="Download CV")).to_be_visible()
        expect(applied.get_by_role("button", name="Make it again")).to_be_enabled()
        notice = page.locator(".toast")
        expect(notice).to_contain_text(f"Your CV for {TITLES[0]} is ready.")
        expect(notice).to_contain_text("ChatGPT 5-hour limit: 37% used")
        expect(notice.get_by_role("link", name="Download CV")).to_have_attribute(
            "href", f"/documents/build/{build.id}/file")
        page.goto(f"{base}/#/settings")
        expect(page.locator(".ai-accounts li")).to_contain_text("5-hour limit: 37% used")
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

        # One colour for both, beside the looks (owner, 2026-11-17): a tap draws both pictures in it, the
        # looks stay as they were, and it stays picked.
        colours = looks.get_by_role("radiogroup")
        expect(colours.get_by_role("radio", name="Each look's own")).to_have_attribute("aria-checked", "true")
        colours.get_by_role("radio", name="Wine").click()
        expect(looks).to_contain_text("Colour: Wine")
        names = looks.locator(".look .picture .cv-name i")
        expect(names).to_have_count(2)
        expect(names.first).to_have_css("background-color", "rgb(159, 18, 57)")
        expect(names.last).to_have_css("background-color", "rgb(159, 18, 57)")
        page.reload()
        expect(colours.get_by_role("radio", name="Wine")).to_have_attribute("aria-checked", "true")
        expect(looks.locator(".look-name")).to_have_text(["Ledger", "Meridian"])
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


def test_a_claude_sign_in_shows_its_plans_limits_in_settings(site, capsys, monkeypatch):
    """Owner, 2026-11-14: a Claude plan's 5-hour and weekly limits, under the account in Settings, each
    with how much is used and when it starts again; nearly spent, its bar says so. What Anthropic answers
    is a stand-in here (`tests/test_ai_accounts.py` reads a real answer's shape)."""
    import json

    from libs.db import database
    from libs.llm import limits
    from libs.secrets import SecretStore, generate_key

    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
    monkeypatch.setattr(limits, "read", lambda kind, blob, **kw: [
        limits.Window("five_hour", "5-hour limit", 37.2, datetime(2026, 11, 15, 3, 0, tzinfo=UTC)),
        limits.Window("seven_day", "Weekly limit", 91.0, None)])
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        db = database()
        SecretStore(db).add(db.users.find_one({"email": email})["_id"], "claude_login",
                            json.dumps({".credentials.json": "{}"}), label="Claude Pro · jordan@example.com")

        page.goto(f"{base}/#/settings")
        row = page.locator(".ai-accounts li")
        expect(row).to_contain_text("5-hour limit: 37% used, starts again")
        expect(row).to_contain_text("Weekly limit: 91% used")
        expect(row.locator(".ai-limit .storage-bar span")).to_have_count(2)
        expect(row.locator(".ai-limit .storage-bar span.full")).to_have_count(1)
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, "Settings at 390px"
        browser.close()


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
        for provider in ("Claude", "ChatGPT"):
            tile = accounts.get_by_role("button", name=f"Sign in with {provider}")
            expect(tile).to_be_visible()
            expect(tile.locator(".ai-mark svg path")).to_have_count(1)
        # Not Gemini, by a sign-in or by a key (owner, 2026-11-22: "its not configured").
        expect(accounts.locator(".ai-provider")).to_have_count(2)
        accounts.get_by_text("Use an API key instead").click()
        expect(accounts.get_by_label("Which key").locator("option")).to_have_text(["Claude API key",
                                                                                  "OpenAI API key"])
        accounts.get_by_text("Use an API key instead").click()
        expect(accounts.get_by_text("Gemini")).to_have_count(0)
        rows = accounts.locator(".ai-accounts li")
        _add_key(accounts, "Claude API key", key)
        expect(rows).to_have_text([re.compile("^Claude API key")])
        expect(rows.first.locator(".ai-mark svg path")).to_have_count(1)
        expect(rows.first).to_contain_text("Writes with claude-haiku-5-5")  # the model Admin picked for it
        expect(accounts.get_by_role("switch", name="Use Claude API key")).to_be_checked()
        assert key not in page.content() and key[-4:] not in accounts.inner_text(), "never shown back"
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, "Settings at 390px"

        # A second goes in use at once. Switching back is seen moving, and moves no row.
        _add_key(accounts, "OpenAI API key", "sk-proj-" + "Qw7" * 12)
        expect(rows).to_have_text([re.compile("^OpenAI API key.*In use"), re.compile("^Claude API key.*Off")])
        accounts.get_by_role("switch", name="Use Claude API key").click()
        expect(accounts.get_by_text("Turning on…")).to_be_visible()
        expect(rows).to_have_text([re.compile("^OpenAI API key.*Off"), re.compile("^Claude API key.*In use")])

        # Their account is no longer what is missing, and a cover letter still needs one of theirs, said with
        # the one way to it.
        page.goto(f"{base}/#/")
        cards.first.get_by_role("button", name="Make my cover letter").click()
        expect(toasts).to_have_text([re.compile(
            "^To make your cover letter, first upload a cover letter in My documents.")])
        expect(toasts.get_by_role("link", name="Open Settings")).to_have_count(0)
        expect(toasts.get_by_role("link", name="Open My documents")).to_be_visible()

        page.goto(f"{base}/#/settings")
        accounts.get_by_role("switch", name="Use Claude API key").uncheck()
        expect(accounts.get_by_text("Turning off…")).to_be_visible()
        expect(rows).to_have_text([re.compile("^OpenAI API key.*Off"), re.compile("^Claude API key.*Off")])
        browser.close()


def _help_with_the_members_chat(page) -> None:
    """Help, the "?" beside the bell, over the page; for a member, with the members' chat on it once the owner
    has given its link (owner, 2026-11-16)."""
    from libs import settings
    from libs.db import database

    chat = "https://chat.example.com/mahoraga-members"
    settings.put(database(), "members_channel_url", chat)
    page.get_by_role("button", name="Help and contact").click()
    expect(page.get_by_role("dialog", name="Help and contact")).to_be_visible()
    expect(page.get_by_role("link", name="Open the members' chat")).to_have_attribute("href", chat)


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
    expect(toasts.get_by_role("link", name="Open Settings")).to_have_attribute("href", "/settings")
    expect(toasts.get_by_role("link", name="Open My documents")).to_have_attribute("href", "/documents")
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
    # Left alone: the pointer off it, as a finger lifts. One resting where it lands holds it, on purpose
    # (`toast.tsx`), and on a phone the toast came to rest under the button just pressed (2026-11-18).
    page.mouse.move(1, 1)
    expect(toasts).to_have_count(0, timeout=TOAST_MS + 3000)

def test_the_jobs_filters_narrow_the_list_stay_as_they_were_left_and_clear_back(site, capsys):
    """The page opens on "Last 24 hours" beside the search, and every other filter is behind one button, each
    under a heading that says what it is (owner, 2026-11-16: "simplify it"): the list follows each one as it
    is set, the way back to it says how many jobs it holds, and each one that is on is a chip under the search
    that takes it off. They stay as they were left, a reload or another device later (owner, 2026-11-07):
    reloaded the moment the last one changed, before its save would otherwise go. Clearing them keeps the time
    chosen."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        posted = page.get_by_label("Posted")
        expect(posted).to_have_value("24")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

        filters = page.get_by_role("dialog", name="Filters")
        page.get_by_role("button", name="Filters").click()
        filters.get_by_label("Job site").select_option(label="Test (2)")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        filters.get_by_role("switch", name="Remote jobs only").check()
        filters.get_by_role("button", name="Show 2 jobs").click()
        expect(filters).to_have_count(0)
        on = page.get_by_role("group", name="Filters on")
        chips = on.get_by_role("button", name=re.compile("^Take off: "))
        expect(chips).to_have_count(2)
        expect(on.get_by_role("button", name="Take off: Remote only")).to_be_visible()
        expect(on.get_by_role("button", name="Take off: Test")).to_be_visible()
        expect(page.get_by_role("button", name="Filters, 2 on")).to_be_visible()

        posted.select_option(label="Last 3 days")
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name="2 jobs match your search")).to_be_visible()
        expect(posted).to_have_value("72")
        page.get_by_role("button", name="Filters").click()
        expect(filters.get_by_label("Job site")).to_have_value("test")
        expect(filters.get_by_role("switch", name="Remote jobs only")).to_be_checked()
        filters.get_by_role("button", name="Close").click()

        on.get_by_role("button", name="Take off: Remote only").click()
        expect(chips).to_have_count(1)
        on.get_by_role("button", name="Clear all").click()
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        expect(on).to_have_count(0)
        expect(posted).to_have_value("72")
        browser.close()


def test_a_box_over_the_page_scrolls_between_its_rounded_corners(site, capsys):
    """Owner, 2026-11-18: "the popups have a weird scroll part, because each popup or modal has rounded
    edges". A sheet scrolled whole, so its scrollbar ran down its side and on into its curved corners, and on
    a phone the Filters' foot was wider than the sheet, so it scrolled sideways too. The part that scrolls
    stops a corner's height short of each end, does not scroll sideways, and the round × is still in its
    corner at the end."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 560}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        filters = page.get_by_role("dialog", name="Filters")
        for width, height in ((1280, 560), (390, 600)):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_role("button", name="Filters").click()
            expect(filters).to_be_visible()
            filters.evaluate("(box) => Promise.all(box.getAnimations().map((a) => a.finished))")
            seen = filters.evaluate("""(box) => {
                const scroller = [box, ...box.querySelectorAll("*")].find((e) =>
                  e.scrollHeight > e.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(e).overflowY));
                if (!scroller) return null;
                scroller.scrollTo({top: scroller.scrollHeight, behavior: "instant"});
                const outer = box.getBoundingClientRect(), inner = scroller.getBoundingClientRect();
                const close = box.querySelector(".close").getBoundingClientRect();
                const hit = document.elementFromPoint(close.x + close.width / 2, close.y + close.height / 2);
                return {round: parseFloat(getComputedStyle(box).borderTopRightRadius),
                        top: inner.top + scroller.clientTop - outer.top,
                        bottom: outer.bottom - (inner.top + scroller.clientTop + scroller.clientHeight),
                        sideways: scroller.scrollWidth - scroller.clientWidth,
                        close: hit?.getAttribute("aria-label"), closeSides: [close.width, close.height]};
            }""")
            assert seen, f"{width}px: the Filters do not scroll {height}px tall, so this proves nothing"
            assert seen["top"] >= seen["round"] and seen["bottom"] >= seen["round"], f"{width}px: {seen}"
            assert seen["sideways"] <= 0, f"{width}px: {seen}"
            assert seen["close"] == "Close", f"{width}px: {seen}"
            # Round: a page's thumb-height rule drew it 36 by 44 on Jobs, round only opened outside a page.
            assert seen["closeSides"][0] == seen["closeSides"][1], f"{width}px: {seen}"
            filters.get_by_role("button", name="Close").click()
            expect(filters).to_have_count(0)
        browser.close()


def test_the_match_score_filter_is_a_row_of_numbers_that_keeps_the_jobs_scored_that_or_more(site, db, capsys):
    """Owner, 2026-11-16: a filter of its own, numbers rather than words, "for a score or above it". Each
    card's badge says its score out of 10; a number picked leaves the jobs whose badge shows it or more, and
    "Any" brings the rest back."""
    from time import monotonic

    from libs import fit
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    base, email, _ = site
    # Their words are in it, so it is on their list, but it is not their job.
    insert(db, CorpusJob(
        url="https://jobs.example.com/sales", source="test", title="Sales Development Representative",
        company="Acme", location="Berlin, Germany", country="DE", city="berlin", is_remote=True,
        language="en", first_seen_at=now(), described_at=now(),
        description="Book meetings for our backend engineer recruiting platform: outbound calls, Salesforce, "
                    "a target every quarter."))
    fit.catch_up(db, deadline=monotonic() + 30)  # what the harvest gives a posting with its text
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        badges = page.locator("article.job-card .tag.match")
        expect(badges).to_have_count(3)
        shown = [int(text.split()[1].split("/")[0]) for text in badges.all_inner_texts()]
        page.get_by_role("button", name="Filters").click()
        filters = page.get_by_role("dialog", name="Filters")
        steps = filters.get_by_role("group", name="Match score")
        expect(steps.get_by_role("button")).to_have_text(["Any", "5+", "6+", "7+", "8+", "9+"])

        at = max(5, min(9, max(shown)))
        kept = [score for score in shown if score >= at]
        assert 0 < len(kept) < len(shown), f"a number that leaves some out: {shown}"
        steps.get_by_role("button", name=f"Match score {at} or more").click()
        expect(badges).to_have_count(len(kept))
        assert all(int(text.split()[1].split("/")[0]) >= at for text in badges.all_inner_texts())
        expect(steps.get_by_role("button", name=f"Match score {at} or more")).to_have_attribute(
            "aria-pressed", "true")
        steps.get_by_role("button", name="Any match score").click()
        expect(badges).to_have_count(3)
        # Picked and the filters closed, it is a chip under the search, which takes it off again.
        steps.get_by_role("button", name=f"Match score {at} or more").click()
        filters.get_by_role("button", name="Close").click()
        expect(badges).to_have_count(len(kept))
        on = page.get_by_role("group", name="Filters on")
        on.get_by_role("button", name=f"Take off: Match {at}+").click()
        expect(badges).to_have_count(3)
        browser.close()


def test_a_free_account_buys_starter_or_member_from_the_upgrade_page(site, capsys, monkeypatch):
    """Owner, 2026-11-16: "we are also selling starter". Once Starter has a price, the Upgrade page offers it
    beside the membership, each with its price and what it adds to Free. Stripe is not set up here, so the
    server is given the prices Stripe would answer, and works the pricing out from them as it does live: the
    membership's card is then "Everything in Starter plan", and the page still lists what the membership adds
    to Free, never Starter's wait beside its own (owner, 2026-11-22: "i get jobs 3 days after they are posted
    which is not true but it appears")."""
    from libs.db import database
    from services.api import billing_routes
    from services.api.billing_routes import Plan

    base, email, _ = site
    # As Stripe would answer: the membership with its 7 free days, Starter with none (owner, 2026-11-17).
    month = {"currency": "gbp", "interval": "month", "interval_count": 1}
    prices = {"member": Plan(amount=499, trial_days=7, **month),
              "starter": Plan(amount=299, trial_days=0, **month)}
    monkeypatch.setattr(billing_routes, "plan", lambda db, which="member": prices[which])

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys, member=False)
        _onboard(page)
        page.goto(f"{base}/#/upgrade")
        plans = page.get_by_role("group", name="Plan")
        adds = page.locator(".adds li")
        expect(plans.get_by_role("button")).to_have_text(["Starter", "Member"])
        expect(page.get_by_role("heading", name="Become a member")).to_be_visible()
        expect(page.locator(".plan-price")).to_have_text("£4.99 a month, after 7 days free")
        expect(adds).to_have_count(8)
        expect(adds.first).to_contain_text("See new jobs right away")
        expect(adds.filter(has_text="See new jobs after")).to_have_count(0)
        expect(adds.last).to_contain_text("Official ATS checks")

        plans.get_by_role("button", name="Starter").click()
        expect(page.get_by_role("heading", name="Start with Starter")).to_be_visible()
        expect(page.locator(".plan-price")).to_have_text("£2.99 a month")
        expect(page.get_by_text("Paid securely through Stripe. Cancel any time.")).to_be_visible()
        expect(adds).to_contain_text(["See new jobs after 3 days", "Job alerts sent to you",
                                      "Match score on every job", "Filter by match score"])
        expect(adds).to_have_count(4)

        # On Starter: the way up says everything the membership adds to it, not the few its card crosses out.
        database().users.update_one({"email": email}, {"$set": {
            "subscription_status": "active", "subscription_plan": "starter"}})
        page.reload()
        expect(page.get_by_role("heading", name="Move up to Member")).to_be_visible()
        expect(page.locator(".adds li")).to_have_count(5)
        expect(page.locator(".adds li")).to_contain_text([
            "See new jobs right away", "A CV made for each job", "A cover letter for each job",
            "Add jobs from any site", "Official ATS checks"])
        page.goto(f"{base}/jobs")
        _the_way_up(page, "3 days", "Move up to Member")
        browser.close()


def _the_way_up(page, late: str, button: str) -> None:
    """Across the top of every page for a plan that is not the membership (owner, 2026-11-23: "they are seeing
    x old posts but if they upgrade they get to see it instantly"): how late, how many of theirs a member sees
    now (the two jobs the site found today), and the plan's own way up."""
    bar = page.get_by_role("complementary", name="Your plan")
    expect(bar).to_contain_text(f"You see new jobs {late} late.")
    expect(bar.locator(".upgrade-bar-count")).to_have_text("2 new jobs waiting")
    expect(bar.get_by_role("link", name=button)).to_have_attribute("href", "/upgrade")


def test_the_buttons_of_jobs_side_by_side_sit_along_one_line(site, capsys):
    """Two columns on a laptop, each card as tall as the tallest in its row: its CV and cover letter buttons
    and its foot sit at its bottom, so a row reads straight across however much each card says (owner,
    2026-11-22: "larger containers with jobs might be bigger and in terms of design it looks weird"). The
    first card is given a title over several lines here, and the second card's buttons go down with it."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        cards = page.locator(".jobs > article.job-card")
        expect(cards).to_have_count(2)
        before = cards.nth(1).locator(".job-docs").bounding_box()
        cards.first.locator("h3 .title").evaluate(
            "(title) => { title.textContent += ', ' + 'with a far longer title than the other '.repeat(6); }")
        long, short = (cards.nth(index).bounding_box() for index in (0, 1))
        assert abs(long["y"] - short["y"]) < 1, "one row"
        assert abs(long["height"] - short["height"]) < 1, "as tall as each other"
        for part in (".job-docs", ".job-bar"):
            tops = [cards.nth(index).locator(part).bounding_box()["y"] for index in (0, 1)]
            assert abs(tops[0] - tops[1]) < 1, f"{part}: along one line, not under the shorter card's text"
        assert cards.nth(1).locator(".job-docs").bounding_box()["y"] > before["y"] + 40, "down with the row"
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
        card(TITLES[0]).locator(".why").click()
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

    # Taken off Applied only once they say so (owner, 2026-11-16), and then back among their jobs.
    asked = page.get_by_role("dialog", name="Take this off Applied?")
    card.get_by_role("button", name="I did not apply").click()
    asked.get_by_role("button", name="Keep it").click()
    expect(asked).to_have_count(0)
    expect(steps.locator("li.done")).to_have_text(["Applied", "Interview"])
    title = card.locator("h3").inner_text()
    card.get_by_role("button", name="I did not apply").click()
    asked.get_by_role("button", name="Take it off").click()
    expect(page.get_by_role("heading", name="You applied to 0 jobs")).to_be_visible()
    page.locator("nav .places a").filter(has_text="Jobs").click()
    expect(page.get_by_role("button", name=title, exact=True)).to_be_visible()


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


def test_telegram_is_three_steps_with_no_chat_id_and_the_morning_email_is_one_tick(site, capsys,
                                                                                    monkeypatch):
    """Their own bot, set up inside the alert that sends to Telegram (owner, 2026-11-19): paste its token,
    press Start in it, and the form says it is connected by itself; until then the alert's card says Telegram
    is not connected. Reached from setting up's last screen. Telegram's Bot API is the stand-in from the
    alerts tests; everything else is the real site. The morning email is an alert's own (owner, 2026-11-17):
    one tick on My search."""
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
        _onboard(page, then="Set up alerts")
        expect(page.get_by_role("heading", name="Alerts", exact=True)).to_be_visible()

        # My search sends to Telegram from the start, and its card says Telegram is not set up.
        mine = page.locator("li.alert-card").filter(has=page.get_by_role("link", name="My search"))
        unset = "Telegram is not connected yet. Tap Change to connect it."
        expect(mine).to_contain_text(unset)
        expect(page.get_by_role("button", name="Pause alerts")).to_have_count(0)
        mine.get_by_role("button", name="Change").click()
        form = page.get_by_role("dialog", name="Change “My search”")
        expect(form.get_by_text("Make your bot.")).to_be_visible()
        # Enter in the token box saves the token, not the alert around it.
        form.get_by_label("Bot token").fill(token)
        form.get_by_label("Bot token").press("Enter")
        expect(form.get_by_text("Saved: @alice_jobs_bot.")).to_be_visible()
        expect(form.get_by_role("link", name="Open @alice_jobs_bot")).to_have_attribute(
            "href", "https://t.me/alice_jobs_bot")
        expect(form.get_by_text("Waiting for you to press Start…")).to_be_visible()

        fake.press_start(token)
        expect(form.get_by_text("Connected to @alice_jobs_bot. Every alert that sends to Telegram uses it.")
               ).to_be_visible(timeout=10_000)
        form.get_by_role("button", name="Send a test").click()
        expect(form.get_by_text("Sent. Check Telegram.")).to_be_visible()
        assert [chat for _, chat, _ in fake.sent] == ["4242", "4242"], "the welcome, then the test"
        # Unticked, Telegram's setup goes; ticked again, it says where it stands.
        form.get_by_label("Telegram").uncheck()
        expect(form.get_by_text("Connected to @alice_jobs_bot.", exact=False)).to_have_count(0)
        form.get_by_label("Telegram").check()
        expect(form.get_by_role("button", name="Disconnect")).to_be_visible()

        expect(mine).not_to_contain_text("a morning email")
        form.get_by_label("Morning email").check()
        form.get_by_role("button", name="Save", exact=True).click()
        expect(mine).to_contain_text("a morning email")
        expect(mine).not_to_contain_text(unset)
        page.reload()
        expect(mine).to_contain_text("a morning email")
        browser.close()


def _emailed(capsys, route: str) -> str:
    """The link in the email just printed. The email goes after the page has its answer, so it may be
    a moment behind the screen that says it was sent."""
    printed = ""
    for _ in range(50):
        printed += capsys.readouterr().out
        if found := re.search(rf"http://\S+/{route}#token=\S+", printed):
            return found.group(0)
        time.sleep(0.1)
    pytest.fail(f"no {route} link was emailed")


PASSWORD = "correct-horse-battery-9"


def _fill_sign_up(page, email: str, password: str = PASSWORD) -> None:
    """The sign-up form as a person fills it: the password twice, and the box ticked that agrees to the Terms,
    naming the limits on our liability, with the policies beside them; without it there is no account (owner,
    2026-11-12)."""
    page.get_by_label("Email").fill(email)
    page.get_by_label(re.compile(r"^Password")).fill(password)
    page.get_by_label("Confirm password").fill(password)
    page.get_by_label(re.compile(r"^I agree to the Terms, including the limits on our liability")).check()
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
    monkeypatch.setattr(billing_routes, "plan", lambda db, which="member": billing_routes.Plan(
        amount=499, currency="gbp", interval="month", interval_count=1, trial_days=7)
        if which == "member" else None)
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
        _the_way_up(page, "7 days", "Become a member")
        expect(page.get_by_text("You see new jobs after 7 days. Jobs stay here for 30 days.")).to_be_visible()
        expect(page.get_by_label("Posted").locator("option")).to_have_text(["Any time", "Last 2 weeks"])
        page.get_by_role("button", name="Make my CV").click()
        notice = page.locator(".toast").filter(has_text="CVs and cover letters comes with a membership.")
        expect(notice).to_be_visible()
        notice.get_by_role("link", name="Become a member").click()
        expect(page.get_by_role("heading", name="Become a member")).to_be_visible()
        expect(page.get_by_text("Payments aren't set up yet.")).to_be_visible()
        # Free, then Member with what it adds ticked, and back to Free, over and over until they touch the
        # page, when it rests on Member; a tap on a side of the switch shows that side (owner, 2026-11-13).
        switch = page.locator(".upgrade-switch")
        expect(switch).to_have_class(re.compile(r"\bmember\b"), timeout=5000)
        expect(switch).to_have_class(re.compile(r"\bfree\b"), timeout=12000)
        page.get_by_role("heading", name="Become a member").click()
        expect(switch).to_have_class(re.compile(r"\bmember\b"))
        page.wait_for_timeout(4000)
        expect(switch).to_have_class(re.compile(r"\bmember\b"))
        switch.get_by_text("Free").click()
        expect(switch).to_have_class(re.compile(r"\bfree\b"))
        # Less motion asked for: Member, ticked, from the start, and still.
        page.emulate_media(reduced_motion="reduce")
        page.reload()
        expect(switch).to_have_class(re.compile(r"\bmember\b"))
        page.wait_for_timeout(3000)
        expect(switch).to_have_class(re.compile(r"\bmember\b"))
        # The way back at the top, to the page they came from (owner, 2026-11-12), not a link at the foot.
        page.get_by_role("button", name="← Back").click()
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()

        page.goto(f"{base}/#/documents")
        expect(page.get_by_text("CVs and cover letters for each job come with a membership.")).to_be_visible()
        page.goto(f"{base}/#/alerts")
        expect(page.get_by_text("Alerts come with a membership.")).to_be_visible()
        # And so does bringing a job in by its link (owner, 2026-11-16): the tab says so, with the way there.
        page.goto(f"{base}/#/add")
        expect(page.get_by_text("Adding a job by its link comes with a membership.")).to_be_visible()
        expect(page.get_by_label("The job's link")).to_have_count(0)
        page.goto(f"{base}/#/settings")
        expect(page.get_by_text("The free plan.")).to_be_visible()

        browser.close()


def test_the_plan_is_picked_after_the_email_link_on_the_landing_pages_cards(site, capsys, monkeypatch):
    """Owner, 2026-11-12: signing up asks for no plan; after the email link and before setting up, the
    landing page's two cards, and nothing past them by the address alone. The landing page's Member card
    opens that page on Stripe's checkout, its price large over it, which says plainly here, where Stripe has
    no keys, that payments aren't set up; the way back is to the cards, not past them."""
    from libs.membership import trial_days
    from services.api import billing_routes

    base, email, _ = site
    # Both paid plans have a price at Stripe: 4.99 and 2.99, shown as reduced from the operator's 29.99 and
    # 18.99 with the saving worked out (owner, 2026-11-16); each with its own free days, by the site's rule:
    # the membership's, and none on Starter (owner, 2026-11-17).
    monkeypatch.setattr(billing_routes, "plan", lambda db, which="member": billing_routes.Plan(
        amount={"member": 499, "starter": 299}[which], currency="gbp", interval="month", interval_count=1,
        trial_days=trial_days(db, which)))
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
        expect(cards.locator(".price-name")).to_have_text(["Free", "Starter", "Member"])
        _priced(cards)
        # Each paid card begins with everything in the card before it, then how soon its plan sees a job, read
        # across.
        expect(cards.locator(".price-list li:first-child .line")).to_have_text(
            ["See new jobs after 7 days", "Everything in Free plan", "Everything in Starter plan"])
        expect(cards.locator(".price-list li:nth-child(2) .line")).to_have_text([
            "Every job site in one list", "See new jobs after 3 days", "See new jobs right away"])
        _crossed_out_and_the_bonus(cards)
        _one_line_each(page)
        expect(cards.last.locator(".price-note")).to_have_text("Card needed. Free to cancel in the trial.")
        # The price kept for life, in a band on each paid card that is read without reading.
        expect(cards.locator(".price-band")).to_have_text(["Price locked for life", "Price locked for life"])
        # On a phone the offer comes first: Member, then Starter, then Free.
        tops = cards.evaluate_all("cards => cards.map((card) => card.getBoundingClientRect().top)")
        assert tops[2] < tops[1] < tops[0], tops
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # Three to a row on a small laptop, the narrowest each card gets, and still a line each.
        page.set_viewport_size({"width": 1024, "height": 844})
        tops = cards.evaluate_all("cards => cards.map((card) => card.getBoundingClientRect().top)")
        assert abs(tops[0] - tops[2]) < 40, f"three to a row: {tops}"
        _one_line_each(page)
        page.set_viewport_size({"width": 390, "height": 844})
        _starter_without_a_trial(page, cards)
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


def _crossed_out_and_the_bonus(cards) -> None:
    """Free gives a line for each thing every account has; Free and Starter each cross out four of the most
    telling things they lack (owner, 2026-11-17: "3-4 negatives to each offer except member"), and the
    membership none, with its bonus."""
    expect(cards.first.locator(".price-list li:not(.out)")).to_have_count(7)
    expect(cards.first.locator(".price-list li.out .line")).to_have_text(FREE_CROSSED)
    expect(cards.nth(1).locator(".price-list li.out .line")).to_have_text(
        ["See new jobs right away", "A CV made for each job", "A cover letter for each job",
         "Add jobs from any site"])
    expect(cards.last.locator(".price-list li.out")).to_have_count(0)
    expect(cards.last.locator(".price-list li.bonus .line")).to_have_text("Official ATS checks")


def _priced(cards) -> None:
    """Each paid card's price as Stripe has it, crossed out from the operator's figure with the saving worked
    out, and the membership's free days on a tab at its top."""
    expect(cards.last).to_contain_text("£4.99")
    expect(cards.last.locator(".price-badge")).to_have_text("7 days free")
    expect(cards.last.locator(".price-was s")).to_have_text("£29.99")
    expect(cards.last.locator(".price-was em")).to_have_text("83% off")
    expect(cards.nth(1).locator(".price-amount")).to_contain_text("£2.99")
    expect(cards.nth(1).locator(".price-was")).to_have_text("Was £18.9984% off")


def _starter_without_a_trial(page, cards) -> None:
    """A free trial on the membership alone: Starter is billed from its first day (owner, 2026-11-17), so
    its card says no free days, and neither does its own payment page."""
    expect(cards.last.locator(".price-after")).to_have_text("after your 7-day free trial")
    expect(cards.nth(1).locator(".price-after")).to_have_count(0)
    expect(cards.nth(1).locator(".price-badge")).to_have_count(0)
    expect(cards.nth(1).locator(".price-note")).to_have_text("Card needed. Cancel any time.")
    cards.nth(1).get_by_role("button", name="Start with Starter").click()
    expect(page.get_by_role("heading", name="Start with Starter")).to_be_visible()
    expect(page.locator(".plan-price")).to_have_text("£2.99 a month")
    expect(page.get_by_text("Paid securely through Stripe. Cancel any time.")).to_be_visible()
    page.get_by_role("button", name="← Back to the plans").click()


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
        page.get_by_label(re.compile(r"^New password")).fill(new)
        page.get_by_label("Confirm new password").fill(new)
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
    """The first screen: the CV, About you skipped, then on to what was read off it."""
    expect(page.get_by_role("heading", name="Your CV")).to_be_visible()
    page.locator("label.drop", has_text="Upload your CV").locator("input").set_input_files(
        files=[{"name": "cv.txt", "mimeType": "text/plain", "buffer": CV.encode()}])
    expect(page.get_by_text("cv.txt")).to_be_visible()
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("heading", name="About you")).to_be_visible()
    page.get_by_role("button", name="Skip for now").click()
    expect(page.get_by_role("heading", name="What we found")).to_be_visible()


def _onboard(page, then: str = "See my jobs") -> None:
    """The screens: the CV, About you, what was read off them, done, and out by `then`: "See my jobs", or
    "Set up alerts", which finishes them as well."""
    _upload(page)
    # Read off the CV by rules: their name, and the job they do as a green word.
    expect(page.get_by_label("Your name")).to_have_value("Jordan Ellery")
    expect(page.locator(".word.green", has_text="backend engineer").first).to_be_visible()
    # A third green word: a search takes at least three.
    page.get_by_label("Add to: Show me jobs with these words").fill("golang")
    page.get_by_label("Add to: Show me jobs with these words").press("Enter")
    page.get_by_role("button", name="Looks good").click()

    expect(page.get_by_role("heading", name="You're all set")).to_be_visible()
    page.get_by_role("button", name=then).click()


def _bookmarks(page) -> None:
    """A little mark in the corner of a job (owner, 2026-11-15): tapped, the job stays on the Jobs page and
    is under Bookmarks too, shown as it is there. Taken off under Bookmarks, it can be put straight back;
    left off, it is gone from there the next time. A refresh loses nothing, and three tabs fit a phone."""
    def card(title):
        return page.locator("article.job-card").filter(has=page.get_by_role("button", name=title, exact=True))

    tabs = page.get_by_role("navigation", name="Jobs")
    tabs.get_by_role("link", name="Bookmarks").click()
    expect(page.get_by_role("heading", name="No bookmarks yet")).to_be_visible()
    expect(page.locator("nav .places a[aria-current=page]")).to_have_text("Jobs", use_inner_text=True)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), (
        "the three tabs are wider than a phone")
    tabs.get_by_role("link", name="Your jobs").click()

    card(TITLES[0]).get_by_role("button", name="Bookmark this job").click()
    on = card(TITLES[0]).get_by_role("button", name="Remove bookmark")
    expect(on).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.locator("article.job-card")).to_have_count(2)

    tabs.get_by_role("link", name="Bookmarks").click()
    expect(page.get_by_role("heading", name="1 bookmarked job")).to_be_visible()
    expect(card(TITLES[0]).locator(".why")).to_have_text("Your words: backend engineer")
    expect(card(TITLES[0]).get_by_role("button", name="Make my CV")).to_be_visible()
    card(TITLES[0]).get_by_role("button", name="Remove bookmark").click()
    card(TITLES[0]).get_by_role("button", name="Bookmark this job").click()
    page.reload()
    expect(page.get_by_role("heading", name="1 bookmarked job")).to_be_visible()

    card(TITLES[0]).get_by_role("button", name="Remove bookmark").click()
    page.get_by_role("navigation", name="Jobs").get_by_role("link", name="Your jobs").click()
    expect(card(TITLES[0]).get_by_role("button", name="Bookmark this job")).to_be_visible()
    page.get_by_role("navigation", name="Jobs").get_by_role("link", name="Bookmarks").click()
    expect(page.get_by_role("heading", name="No bookmarks yet")).to_be_visible()
    page.get_by_role("navigation", name="Jobs").get_by_role("link", name="Your jobs").click()
    expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()


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
            page.goto(f"{base}{event.url}")
            if event.url == "/upgrade":
                # The membership's own page, apart from the places in the bar.
                expect(page.get_by_role("heading", name=re.compile(r"\bmember\b"))).to_be_visible()
            else:
                expect(page.locator("nav a[aria-current=page]")).to_have_attribute("href", event.url)


def _alerts_on_an_iphone(browser, page, base: str) -> None:
    """An iPhone or iPad outside its home screen is told what to do, not failed: on the card of an alert that
    sends to the phone, and inside it, where the phone is set up (owner, 2026-11-19)."""
    for agent in (IPHONE, IPAD):
        apple = browser.new_context(storage_state=page.context.storage_state(), user_agent=agent)
        apple.add_init_script("delete window.PushManager;"
                              "Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5})")
        settings = apple.new_page()
        settings.goto(f"{base}/#/alerts")
        mine = settings.locator("li.alert-card").filter(has=settings.get_by_role("link", name="My search"))
        expect(mine).to_contain_text("No phone gets these yet. Open this alert on your phone and save it.")
        mine.get_by_role("button", name="Change").click()
        expect(settings.get_by_text("On an iPhone, add this site to your Home Screen first")).to_be_visible()


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


def test_an_alert_is_made_for_a_company_and_shows_what_it_found(site, capsys):
    """Alerts (owner, 2026-11-17), on a phone: every account starts with My search; a new alert follows a
    company in a country, made from one form; and once a job of theirs is read, the alert's own page lists
    it."""
    from libs.alerts import route
    from libs.db import database, insert, now
    from libs.db.models import Alert, AlertCursor, CorpusJob, UserJobState

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        database().users.update_one({"email": email},
                                    {"$set": {"subscription_status": "active", "active": True}})
        page.goto(f"{base}/#/alerts")
        expect(page.get_by_role("heading", name="Alerts", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="My search", exact=True)).to_be_visible()

        page.get_by_role("button", name="New alert").click()
        form = page.get_by_role("dialog", name="New alert")
        form.get_by_label("Name").fill("LEGO in Denmark")
        form.get_by_label("Add to: Companies").fill("LEGO")
        form.get_by_label("Add to: Companies").press("Enter")
        form.get_by_label("Add to: Places").fill("Denmark")
        form.get_by_label("Add to: Places").press("Enter")
        form.get_by_role("button", name="Make this alert").click()
        expect(form).to_have_count(0)
        said = "“LEGO in Denmark” would have found nothing in the last 7 days."
        expect(page.get_by_text(said)).to_be_visible()
        card = page.locator("li.alert-card").filter(has=page.get_by_role("link", name="LEGO in Denmark"))
        expect(card).to_contain_text("LEGO · Denmark")
        expect(card).to_contain_text("Nothing found yet")
        _fits(page)

        db = database()
        read = now() - timedelta(minutes=5)
        # Made before the job was read: an alert announces what is new after it.
        db[Alert.COLLECTION].update_many({}, {"$set": {"created_at": read - timedelta(hours=1)}})
        brick = insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}", source="linkedin",
                                     country="DK", title="Brick Designer", company="The LEGO Group",
                                     description="Design bricks. " * 30,
                                     first_seen_at=read, described_at=read, posted_at=read))
        db[AlertCursor.COLLECTION].update_one({"_id": "0"}, {"$set": {"at": read - timedelta(minutes=1)}},
                                             upsert=True)
        route(db)
        # What the LEGO alert caught, not how many were caught in all: My search catches the page's own
        # Backend Engineer jobs too once they are `SETTLE` old, which a slower run reaches first.
        lego = db[Alert.COLLECTION].find_one({"name": "LEGO in Denmark"})["_id"]
        assert db[UserJobState.COLLECTION].distinct("job_id", {"alerts": lego}) == [brick.id]

        page.reload()
        card.get_by_role("link", name="See what it found").click()
        expect(page.get_by_role("heading", name="LEGO in Denmark")).to_be_visible()
        expect(page.get_by_role("button", name="Brick Designer", exact=True)).to_be_visible()
        page.get_by_role("link", name="← All alerts").click()
        card.get_by_role("switch").uncheck()
        expect(card.get_by_text("Off", exact=True)).to_be_visible()
        browser.close()



def test_a_phone_is_asked_to_install_the_app_once_a_visit_until_it_is_and_told_why(site, capsys):
    """Owner, 2026-11-17: installing the app pushed on a phone for Starter and Member, "in front of users
    until they do install it", with the benefits. On an iPhone: the sheet rises by itself once a visit, saying
    why and how (Share, Add to Home Screen); "Get the app" stays up top and first in More; a new visit asks
    again; and opened from the home screen, the app asks nothing."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=IPHONE,
                                      has_touch=True, is_mobile=True)
        page = context.new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)

        sheet = page.get_by_role("dialog", name="Get the Mahoraga app")
        expect(sheet).to_be_visible()
        for why in ("Alerts on your lock screen", "One tap to your jobs", "Full screen"):
            expect(sheet.get_by_text(why)).to_be_visible()
        expect(sheet).to_contain_text("On an iPhone, they only arrive through the app.")
        expect(sheet.get_by_role("list", name="How to add it").get_by_role("listitem")).to_have_count(3)
        expect(sheet).to_contain_text("Tap Add to Home Screen")
        sheet.get_by_role("button", name="Got it").click()
        expect(sheet).to_have_count(0)
        _asked_again_only_by_hand(page, sheet)

        again = context.new_page()
        again.goto(f"{base}/#/")
        expect(again.get_by_role("dialog", name="Get the Mahoraga app")).to_be_visible()
        again.close()
        _opened_as_the_app(context, base)
        browser.close()


def _asked_again_only_by_hand(page, sheet) -> None:
    """Said "not now", the same visit is not asked by itself again; "Get the app" is up top, fitting the bar
    of the smallest phone, and first in More."""
    page.reload()
    expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
    page.wait_for_timeout(1500)
    expect(sheet).to_have_count(0)
    for width in (320, 360, 390):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate(FAULTS) == {"off": [], "over": []}, f"the bar with Get the app at {width}px"
    page.get_by_role("button", name="Get the app").click()
    expect(sheet).to_be_visible()
    sheet.get_by_role("button", name="Got it").click()
    page.get_by_role("button", name="More", exact=True).click()
    expect(page.get_by_role("dialog", name="More").locator(".more-row").first).to_have_text("Get the app")
    page.keyboard.press("Escape")


def _opened_as_the_app(context, base: str) -> None:
    """Opened from the home screen (an iPhone's `navigator.standalone`), the app asks nothing. The phone's
    browser asks on, the app opened or not: an iPhone's cannot tell whether it is still there, and one
    installed and deleted is asked for again (owner, 2026-11-18: "it appears if the app is not on a device,
    even after its been installed once but deleted"). It was asked never again, on any phone of theirs."""
    app = context.new_page()
    app.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true})")
    app.goto(f"{base}/#/")
    expect(app.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
    app.wait_for_timeout(1500)
    expect(app.get_by_role("dialog", name="Get the Mahoraga app")).to_have_count(0)
    expect(app.get_by_role("button", name="Get the app")).to_have_count(0)
    browser_tab = context.new_page()
    browser_tab.goto(f"{base}/#/")
    expect(browser_tab.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
    expect(browser_tab.get_by_role("button", name="Get the app")).to_be_visible()


def test_the_browsers_offer_installs_the_app_and_a_laptop_or_a_free_plan_is_not_asked(site, capsys):
    """Where the browser offers to install (`beforeinstallprompt`, Chrome on Android), the sheet's own button
    asks it, and installed, it is said and asked no more. A phone whose browser says the app is on it is not
    asked, by the manifest naming the app; a laptop is not asked, nor a plan without alerts."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        phone = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = phone.new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        sheet = page.get_by_role("dialog", name="Get the Mahoraga app")
        expect(sheet.get_by_role("list", name="How to add it")).to_be_visible()

        laptop = browser.new_context(viewport={"width": 1280, "height": 800})
        laptop.add_cookies(phone.cookies())
        desk = laptop.new_page()
        desk.goto(f"{base}/#/")
        expect(desk.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        desk.wait_for_timeout(1500)
        expect(desk.get_by_role("dialog", name="Get the Mahoraga app")).to_have_count(0)
        expect(desk.get_by_role("button", name="Get the app")).to_have_count(0)

        # The browser's offer, as Chrome makes it once the site can be installed.
        page.evaluate("""() => {
            const offer = new Event("beforeinstallprompt", {cancelable: true});
            offer.prompt = async () => { window.asked = true; };
            offer.userChoice = Promise.resolve({outcome: "accepted"});
            window.dispatchEvent(offer);
        }""")
        sheet.get_by_role("button", name="Install the app").click()
        assert page.evaluate("window.asked") is True, "the browser's own question was not asked"
        page.evaluate("window.dispatchEvent(new Event('appinstalled'))")
        expect(sheet).to_have_count(0)
        expect(page.get_by_text("Mahoraga is on your home screen.")).to_be_visible()
        expect(page.get_by_role("button", name="Get the app")).to_have_count(0)
        _a_phone_with_the_app_is_not_asked(browser, phone, base)
        _a_free_phone_is_not_asked(browser, base, f"free.{email}", capsys)
        browser.close()


def _a_phone_with_the_app_is_not_asked(browser, phone, base: str) -> None:
    """Chrome on Android tells a page whether its own app is on the phone, if the manifest names it."""
    import json

    manifest = json.loads(phone.request.get(f"{base}/manifest.webmanifest").text())
    assert {"platform": "webapp", "url": "/manifest.webmanifest"} in manifest.get("related_applications", [])
    other = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    other.add_cookies(phone.cookies())
    other.add_init_script("navigator.getInstalledRelatedApps = async () => [{platform: 'webapp'}]")
    page = other.new_page()
    page.goto(f"{base}/#/")
    expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
    page.wait_for_timeout(1500)
    expect(page.get_by_role("dialog", name="Get the Mahoraga app")).to_have_count(0)
    expect(page.get_by_role("button", name="Get the app")).to_have_count(0)
    other.close()


def _a_free_phone_is_not_asked(browser, base: str, email: str, capsys) -> None:
    """The app carries alerts, which a free plan has none of (owner: "users that are starter or member")."""
    phone = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page = phone.new_page()
    _sign_up(page, base, email, capsys, member=False)
    _onboard(page)
    page.wait_for_timeout(1500)
    expect(page.get_by_role("dialog", name="Get the Mahoraga app")).to_have_count(0)
    expect(page.get_by_role("button", name="Get the app")).to_have_count(0)

def test_the_site_installs_as_an_app_and_a_job_it_pushes_shows_the_wheel(site, capsys, tmp_path, monkeypatch):
    """Alerts on the phone without an app store (owner, 2026-11-17: "download it through chrome as a mini
    app"): Chrome finds nothing missing to install the site, and an alert that sends to the phone offers it; a
    job pushed to the phone shows the app's icon, and the wheel in the status bar; and the tab that turned
    notifications on is the service worker's at once, so tapping one can take that tab to the job. Before: two
    icons missing, so never offered, a notification with neither, and a tab the worker could not move.
    Notifications are asked for by saving an alert that sends to the phone (owner, 2026-11-19), with no button
    of their own."""
    import json

    base, email, ids = site
    with sync_api.sync_playwright() as playwright:
        # A profile of its own: Chrome offers no install to a private window, which a new context is.
        context = playwright.chromium.launch_persistent_context(
            tmp_path, executable_path=CHROME, headless=True, viewport={"width": 390, "height": 844})
        context.grant_permissions(["notifications"], origin=base)
        page = context.new_page()
        _sign_up(page, base, email, capsys)
        _onboard(page)
        cdp = context.new_cdp_session(page)
        assert cdp.send("Page.getInstallabilityErrors")["installabilityErrors"] == []
        page.goto(f"{base}/#/alerts")
        mine = page.locator("li.alert-card").filter(has=page.get_by_role("link", name="My search"))
        mine.get_by_role("button", name="Change").click()
        form = page.get_by_role("dialog", name="Change “My search”")
        expect(form.get_by_role("button", name="Install the app")).to_be_visible()
        _fits(page)
        form.get_by_role("button", name="Close").click()

        workers: list[dict] = []
        cdp.on("ServiceWorker.workerRegistrationUpdated",
               lambda event: workers.extend(event["registrations"]))
        cdp.send("ServiceWorker.enable")
        # What turning notifications on registers (`src/push.ts`); the subscription itself needs a push
        # service, which a headless browser has none of.
        assert page.evaluate("""async () => {
          await navigator.serviceWorker.register("/sw.js");
          await navigator.serviceWorker.ready;
          if (!navigator.serviceWorker.controller) await new Promise((taken) => {
            navigator.serviceWorker.addEventListener("controllerchange", taken);
            setTimeout(taken, 3000);
          });
          return navigator.serviceWorker.controller !== null;
        }"""), "the tab that turned notifications on is not the worker's, so a tap cannot take it to the job"
        worker = next(each for each in workers if not each.get("isDeleted"))
        job = ids[TITLES[0]]
        # What the notifier sends (`services.notifier.card.notification`), as a push service delivers it.
        sent = {"title": "Backend Engineer", "body": "My search · Acme", "url": f"/jobs?job={job}"}
        cdp.send("ServiceWorker.deliverPushMessage", {
            "origin": base, "registrationId": worker["registrationId"], "data": json.dumps(sent)})
        shown = page.evaluate("""async () => {
          const worker = await navigator.serviceWorker.ready;
          for (let tries = 0; tries < 50; tries++) {
            const [shown] = await worker.getNotifications();
            if (shown) return {title: shown.title, body: shown.body, icon: shown.icon, badge: shown.badge,
                               url: shown.data.url};
            await new Promise((later) => setTimeout(later, 100));
          }
          return null;
        }""")
        assert shown == {**sent, "icon": f"{base}/icon-192.png", "badge": f"{base}/badge-96.png"}
        for picture in (shown["icon"], shown["badge"]):
            assert page.request.get(picture).headers["content-type"] == "image/png", picture

        _notifications_asked_by_the_alert(page, mine, form, monkeypatch)
        context.close()


def _notifications_asked_by_the_alert(page, mine, form, monkeypatch) -> None:
    """No button of their own (owner, 2026-11-19): an alert that sends to the phone asks this one as it is
    switched on or saved, and then sends a test from inside it."""
    # Asked for by saving the alert, on a site given no push settings, as the live one was: it ran a night
    # ending in "Notifications are not set up on this site yet" (2026-11-18). Only the browser's push
    # service is stood in for, and it is handed the site's key as Chrome's would be.
    page.add_init_script("""PushManager.prototype.subscribe = async function (options) {
      window.subscribedWith = new Uint8Array(options.applicationServerKey).length;
      window.subscription = {endpoint: "https://push.example.com/e2e", toJSON: () => ({
        endpoint: "https://push.example.com/e2e", keys: {p256dh: "BKey", auth: "secret"}})};
      return window.subscription;
    };
    PushManager.prototype.getSubscription = async () => window.subscription ?? null;""")
    page.reload()
    expect(page.get_by_role("button", name="Turn on notifications")).to_have_count(0)
    expect(mine).to_contain_text("No phone gets these yet.")
    # Its switch turned on asks this phone too, as the owner asked of the switch.
    switch = mine.get_by_role("switch", name="My search on")
    switch.uncheck()
    switch.check()
    expect(mine).not_to_contain_text("No phone gets these yet.")
    assert page.evaluate("window.subscribedWith") == 65, "asked as the switch went on"
    # And saving asks, which the same phone, forgotten, shows.
    from libs.db import database

    database().push_subscriptions.delete_many({})
    page.evaluate("delete window.subscription; window.subscribedWith = 0")
    page.reload()
    expect(mine).to_contain_text("No phone gets these yet.")
    mine.get_by_role("button", name="Change").click()
    expect(form.get_by_text("When you save, this phone asks to allow notifications.")).to_be_visible()
    form.get_by_role("button", name="Save", exact=True).click()
    expect(form).to_have_count(0)
    assert page.evaluate("window.subscribedWith") == 65, "a P-256 point, as `subscribe` takes"
    expect(mine).not_to_contain_text("No phone gets these yet.")
    mine.get_by_role("button", name="Change").click()
    expect(form.get_by_text("On for this phone.")).to_be_visible()

    # And a test, to see one arrive without waiting for a job: sent by the server to this browser alone.
    from libs.notify import push

    pushed = []
    monkeypatch.setattr(push, "webpush", lambda browser, *_, **__: pushed.append(browser["endpoint"]))
    form.get_by_role("button", name="Send a test").click()
    expect(form.get_by_role("status").filter(has_text="Sent.")).to_be_visible()
    assert pushed == ["https://push.example.com/e2e"]


def _fits(page) -> None:
    """Nothing wider than the screen: a phone scrolls down, never across."""
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "the page scrolls sideways"


# ── what the review of 2026-11-20 found in the pages themselves ─────────────────────────────────────────────

def _path(url: str) -> str:
    return urlparse(url).path


def _drawn(page) -> None:
    """Two frames on: whatever an answer changed has been drawn."""
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")


def _someone(playwright, site, capsys, *, width: int = 1280, height: int = 900):
    """Somebody new, set up and on their Jobs page, in a browser of their own."""
    base, email, _ = site
    browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    _sign_up(page, base, email, capsys)
    _onboard(page)
    expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
    return browser, context, page


def _holding(held: list):
    """A request kept unanswered, to be answered when the test says. A function of our own: the browser's
    driver refuses a builtin (`list.append`) as a handler."""
    def hold(route) -> None:
        held.append(route)
    return hold


def _within(page, path: str) -> None:
    """To another page of the site without loading the site again, as a link or the back button does: what
    the pages have read stays read. (`page.goto` to another address is a new load, starting from nothing.)"""
    page.evaluate(
        "(to) => { history.pushState(null, '', to); dispatchEvent(new PopStateEvent('popstate')); }", path)


def _card(page, title: str):
    return page.locator("article.job-card").filter(has=page.get_by_role("button", name=title, exact=True))


def _skip(page, title: str) -> None:
    _card(page, title).get_by_role("button", name="Skip").click()
    page.get_by_role("dialog", name="Skip this job?").get_by_role("button", name="Skip it").click()


def test_a_visitor_is_shown_the_landing_page_before_the_server_has_said_a_word(site):
    """The page that sells was drawn only after two answers in a row, `/dashboard` refused and then the
    sign-in renewed and refused: two round trips of "Loading…" for somebody who has never signed in. A browser
    nobody was signed in on is shown it at once, and the server is asked behind it (`session.ts`)."""
    base, _, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        held = []
        page.route("**/dashboard", _holding(held))

        page.goto(base)

        expect(page.locator(".landing")).to_be_visible()
        assert held, "the premise: the server was asked who this is, and has not answered"
        browser.close()


def test_a_session_that_cannot_be_renewed_just_now_is_not_read_as_signed_out(site, capsys):
    """Only the server's own "no" is signed out. With the connection gone as the 15-minute pass ran out, the
    renewal failed, the failure was read as that "no", and somebody signed in was shown the landing page,
    their marks kept for a lost connection thrown away with the session. It is said as what it is, and tried
    again.
    And while the server has not answered, somebody who was signed in is not shown the landing page."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, context, page = _someone(playwright, site, capsys)
        page.goto(f"{base}/#/settings")
        expect(page.get_by_text(email)).to_be_visible()

        context.clear_cookies(name="access_token")
        page.route("**/auth/refresh", lambda route: route.abort())
        page.reload()

        expect(page.get_by_text("Mahoraga can't be reached right now.")).to_be_visible()
        expect(page.locator(".landing")).to_have_count(0)

        page.unroute("**/auth/refresh")
        page.get_by_role("button", name="Try again").click()
        expect(page.get_by_text(email)).to_be_visible()

        held = []
        page.route("**/dashboard", _holding(held))
        page.reload()
        expect(page.get_by_text("Loading…")).to_be_visible()
        assert held, "the premise: the server has not answered"
        expect(page.locator(".landing")).to_have_count(0)
        browser.close()


def test_a_tab_follows_whoever_is_signed_in_from_another_tab(site, capsys):
    """One browser, two tabs. Signing out in one left the other drawing the account's pages, and signing in as
    somebody else left it drawing the first account's, from what it had kept, until it was next looked at and
    then for a moment more. A tab is told when another signs out or in, forgets what it kept, and asks the
    server who it is for now. This one is never touched after it opens."""
    base, email, _ = site
    other = f"sam.{uuid.uuid4().hex[:8]}@example.com"
    with sync_api.sync_playwright() as playwright:
        browser, context, first = _someone(playwright, site, capsys)
        first.goto(f"{base}/#/settings")
        expect(first.get_by_text(email)).to_be_visible()

        second = context.new_page()
        second.goto(f"{base}/#/settings")
        second.get_by_role("button", name="Sign out").click()
        expect(second.locator(".landing")).to_be_visible()

        expect(first.locator(".landing")).to_be_visible()
        expect(first.get_by_text(email)).to_have_count(0)

        second.goto(f"{base}/#/signup")
        _fill_sign_up(second, other)
        second.goto(_emailed(capsys, "verify"))
        expect(second.get_by_role("heading", name="Choose your plan")).to_be_visible()

        expect(first.get_by_role("heading", name="Choose your plan")).to_be_visible()
        browser.close()


def test_a_slow_answer_to_an_earlier_search_is_not_drawn_over_a_newer_one(site, capsys):
    """Two reads under way, and whichever answered last was drawn: the list of the search before, under the
    words of the search now, with its count. The newest read is the one drawn, however late an older one
    answers."""
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        held = []
        page.route(lambda url: _path(url) == "/feed" and "q=nurse" in url, _holding(held))
        search = page.get_by_label("Search your jobs")

        with page.expect_request(lambda request: "q=nurse" in request.url):
            search.fill("nurse")
        with page.expect_response(lambda response: "q=senior" in response.url):
            search.fill("senior")
        expect(page.locator("article.job-card")).to_have_count(1)
        expect(_card(page, TITLES[1])).to_be_visible()

        assert held, "the premise: the earlier search is still unanswered"
        with page.expect_response(lambda response: "q=nurse" in response.url):
            held[0].continue_()
        _drawn(page)

        expect(_card(page, TITLES[1])).to_be_visible()
        expect(page.locator("article.job-card")).to_have_count(1)
        browser.close()


def test_a_job_skipped_is_not_drawn_again_from_the_list_read_before_it_was(site, capsys):
    """Jobs draws at once from what it read last, and that list still had the job just skipped: back on Jobs
    it was there again until a fresh answer came, and with no connection for good. Marked, a job leaves the
    kept list too. The server is kept from answering here, so what is drawn is what was kept."""
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        with page.expect_response(lambda response: "/hidden" in response.url):
            _skip(page, TITLES[1])
        expect(page.locator("article.job-card")).to_have_count(1)
        held = []
        page.route(lambda url: _path(url) == "/feed", _holding(held))

        _within(page, "/settings")
        expect(page.get_by_role("button", name="Sign out")).to_be_visible()
        with page.expect_request(lambda request: _path(request.url) == "/feed"):
            _within(page, "/jobs")

        expect(_card(page, TITLES[0])).to_be_visible()
        assert held, "the premise: the list on the page is the one kept, the server has not answered"
        expect(_card(page, TITLES[1])).to_have_count(0)
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
        browser.close()


def test_another_page_opens_at_its_top_and_back_returns_to_where_the_last_one_was(site, capsys):
    """An address here names a page, not a part of one, so the browser left a new page scrolled as far down
    as the last one was: Settings opened at its middle. Another page starts at its top. Going back is the
    browser's own to put right, and is left to it."""
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys, width=1280, height=320)
        page.evaluate("window.scrollTo(0, 140)")
        assert page.evaluate("scrollY") == 140, "the premise: Jobs is long enough to be scrolled"

        page.locator("nav .places a", has_text="Settings").click()
        expect(page.get_by_role("button", name="Sign out")).to_be_attached()
        page.wait_for_function("scrollY === 0", timeout=5000)

        page.go_back()
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        page.wait_for_function("scrollY === 140", timeout=5000)
        browser.close()


def test_the_other_tabs_are_read_ahead_after_the_jobs_list_has_its_answer(site, capsys):
    """Jobs is where everybody lands, and its list was the last of some eighteen requests to leave, behind
    reads that open a hundred postings or parse a CV. What the other tabs read ahead waits for the list's
    first answer, and is still all there: each tab is drawn whole the moment it is opened."""
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        sent: list[str] = []
        ahead = {"/applied", "/alerts", "/documents", "/templates", "/account", "/jobs/bookmarked"}
        page.on("request", lambda request: sent.append(_path(request.url)))
        held = []
        page.route(lambda url: _path(url) == "/feed", _holding(held))

        with page.expect_response(lambda response: _path(response.url) == "/notifications"):
            page.reload()
        _drawn(page)
        assert held, "the premise: the list has been asked for and not answered"
        assert not ahead & set(sent), sorted(ahead & set(sent))

        with page.expect_response(lambda response: _path(response.url) == "/templates"):
            for route in held:
                route.continue_()
        assert ahead <= set(sent), sorted(ahead - set(sent))
        browser.close()


def test_a_link_from_an_email_is_out_of_the_address_once_it_is_used(site, capsys):
    """Both links work once. The confirmation link stayed in the history, so Back led to it again and told
    somebody it had just signed in "That link did not work"; the reset link stayed in the address bar after
    the password was set, for a bookmark or a shared screen to keep."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        page.goto(f"{base}/#/signup")
        _fill_sign_up(page, email)
        page.goto(_emailed(capsys, "verify"))
        expect(page.get_by_role("heading", name="Choose your plan")).to_be_visible()
        assert "token=" not in page.url

        page.go_back()
        _drawn(page)
        assert "token=" not in page.url
        expect(page.get_by_role("heading", name="That link did not work")).to_have_count(0)

        page.goto(f"{base}/#/forgot")
        page.get_by_label("Email").fill(email)
        page.get_by_role("button", name="Send reset link").click()
        page.goto(_emailed(capsys, "reset"))
        page.get_by_label(re.compile(r"^New password")).fill("a-new-pass-phrase-7")
        page.get_by_label("Confirm new password").fill("a-new-pass-phrase-7")
        page.get_by_role("button", name="Set password").click()
        expect(page.get_by_role("heading", name="Password reset")).to_be_visible()
        assert "token=" not in page.url
        browser.close()


# A browser that will not store: what a private window may be.
REFUSES_TO_STORE = ("Storage.prototype.setItem = () => "
                    "{ throw new DOMException('refused', 'QuotaExceededError'); };")


def test_a_browser_that_will_not_store_anything_still_sends_a_skipped_job(site, capsys):
    """A private window can refuse to store. A skipped job was put in storage and sent from storage, so there
    it was never sent at all, and the job came back with the next read. It is sent all the same, and kept for
    as long as the tab is."""
    with sync_api.sync_playwright() as playwright:
        browser, context, page = _someone(playwright, site, capsys)
        context.add_init_script(REFUSES_TO_STORE)
        page.reload()
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()

        with page.expect_response(lambda response: "/hidden" in response.url, timeout=5000) as sent:
            _skip(page, TITLES[1])
        assert sent.value.ok
        page.reload()
        expect(page.get_by_role("heading", name="1 job matches your words")).to_be_visible()
        browser.close()


def test_a_browser_that_will_not_store_anything_still_switches_the_theme(site):
    """And the theme: storing the choice threw in the middle of the switch, which stopped half made, the page
    in the new theme and its button still offering it. It is switched for the visit."""
    base, _, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(REFUSES_TO_STORE)
        page = context.new_page()
        page.goto(base)

        page.get_by_role("button", name="Switch to light mode").first.click()

        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(page.get_by_role("button", name="Switch to dark mode").first).to_be_visible()
        browser.close()


def test_a_tab_nobody_is_looking_at_asks_the_server_nothing(site, capsys):
    """The bell asked every minute and a document being made every 800 ms, in every tab left open, looked at
    or not, all day. Neither asks while its tab is hidden, and each asks at once when it is looked at again.
    The clock is the test's, so five minutes pass in a moment and none is waited for."""
    from libs.db import database, insert, now
    from libs.db.models import QueueJob

    _, email, ids = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        db = database()
        user = db.users.find_one({"email": email})["_id"]
        # A CV being written, as a worker elsewhere holds it: this test's own worker leaves it be.
        job = ids[TITLES[0]]
        insert(db, QueueJob(lane="docgen", user_id=user, dedup_key=f"ai:cv:{job}", state="claimed",
                            payload={"job_id": job, "kind": "cv", "mode": "ai"}, claimed_at=now(),
                            claimed_by="elsewhere"))
        page.clock.install()
        page.reload()
        expect(page.locator(".making-now")).to_be_visible()
        with page.expect_request(lambda request: "/documents/build/" in request.url):
            page.clock.run_for(1000)

        page.evaluate("""() => {
          Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
          document.dispatchEvent(new Event("visibilitychange"));
        }""")
        page.clock.run_for(2000)  # whatever was already on its way is answered and done with
        asked: list[str] = []
        page.on("request", lambda request: asked.append(_path(request.url)))
        page.clock.run_for(5 * 60 * 1000)
        _drawn(page)
        assert not [path for path in asked if path == "/notifications" or "/documents/build/" in path], asked

        with page.expect_request(lambda request: _path(request.url) == "/notifications"):
            page.evaluate("""() => {
              delete document.visibilityState;
              document.dispatchEvent(new Event("visibilitychange"));
            }""")
        with page.expect_request(lambda request: "/documents/build/" in request.url):
            page.clock.run_for(1000)
        browser.close()


def test_a_look_just_picked_is_not_undone_by_an_answer_asked_for_before_it(site, capsys):
    """A page asks for its read as it opens and may be changed before the answer comes. The answer, older than
    the change, was drawn over it: the look just picked went back to the one before, though it was saved."""
    base, _, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        # Read ahead first, so the page below draws from it while its own read is held.
        with page.expect_response(lambda response: _path(response.url) == "/templates"):
            page.reload()
        # What the server says before the change, kept to be handed to the page late: the page's own read is
        # held, and answered after the change with what was true before it.
        before = page.request.get(f"{base}/templates").json()
        held = []

        def late(route) -> None:
            if route.request.method == "GET":
                held.append(route)
            else:
                route.continue_()

        page.route(lambda url: _path(url) == "/templates", late)
        with page.expect_request(lambda request: _path(request.url) == "/templates"):
            _within(page, "/documents")
        looks = page.locator("section.card").filter(
            has=page.get_by_role("heading", name="How your CVs and cover letters look"))
        expect(looks.locator(".look-name")).to_have_text(["Meridian", "Meridian"])
        assert held, "the premise: the page's own read is under way"

        looks.get_by_role("button", name="Change").first.click()
        sheet = page.get_by_role("dialog", name="Pick how your CVs look")
        finance = sheet.get_by_role("region", name="Finance and accounting")
        finance.get_by_role("button", name="Ledger").click()
        expect(looks.locator(".look-name").first).to_have_text("Ledger")

        with page.expect_response(
                lambda response: _path(response.url) == "/templates" and response.request.method == "GET"):
            for route in held:
                route.fulfill(json=before)
        _drawn(page)
        expect(looks.locator(".look-name").first).to_have_text("Ledger")
        browser.close()


def test_admins_reply_link_is_to_the_whole_address_a_visitor_gave(site, capsys):
    """A visitor's address goes into Admin's Reply link as it was typed, and `?`, `&` and `=` are allowed in
    one: `a?cc=boss@…` addressed the reply to `a` and copied in whoever the visitor named."""
    from urllib.parse import unquote

    from libs.db import database, insert
    from libs.db.models import ContactMessage

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        db = database()
        db.users.update_one({"email": email}, {"$set": {"is_admin": True}})
        sender = "a?cc=boss@example.com"
        insert(db, ContactMessage(user_id=None, email=sender, subject="Hello", message="A question."))

        page.goto(f"{base}/#/admin/messages")
        page.reload()
        page.get_by_text("Hello").click()
        link = page.get_by_role("link", name=f"Reply to {sender}").get_attribute("href")

        to = urlsplit(link)
        assert (to.scheme, unquote(to.path)) == ("mailto", sender), link
        assert set(parse_qs(to.query)) == {"subject"}, link
        browser.close()


def test_an_account_page_asked_for_another_account_never_draws_the_one_before(site, capsys):
    """The page of one account, asked for another, went on drawing the first under the second's address, with
    switches that acted on the second, until its read landed. The second's read is kept from landing here, so
    what is drawn is what the page had."""
    from libs.db import database, insert
    from libs.db.models import User

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        db = database()
        db.users.update_one({"email": email}, {"$set": {"is_admin": True}})
        mine = db.users.find_one({"email": email})["_id"]
        theirs = insert(db, User(email=f"other.{uuid.uuid4().hex[:8]}@example.com", password_hash="x",
                                 status="active"))
        page.goto(f"{base}/#/admin/users/{mine}")
        page.reload()
        expect(page.get_by_role("heading", name="Jordan Ellery")).to_be_visible()
        held = []
        page.route(lambda url: _path(url) == f"/admin/users/{theirs.id}", _holding(held))

        with page.expect_request(lambda request: _path(request.url) == f"/admin/users/{theirs.id}"):
            _within(page, f"/admin/users/{theirs.id}")

        expect(page.get_by_text("Loading…")).to_be_visible()
        assert held, "the premise: the second account has not been read yet"
        expect(page.get_by_role("heading", name="Jordan Ellery")).to_have_count(0)
        browser.close()


def test_admins_pages_are_sent_only_to_somebody_who_opens_admin(site, capsys):
    """Everybody downloaded Admin: a sixth of the site's script, 24 kB of 144 zipped, for pages a visitor or a
    member has no way into. It is a file of its own, asked for when an admin opens Admin, or has the pointer
    on its link, and by nobody else; and Admin still opens."""
    from libs.db import database

    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        scripts: list[str] = []
        page.on("request", lambda request: scripts.append(_path(request.url)))
        page.reload()
        for place in ("settings", "documents", "applied", ""):
            page.goto(f"{base}/#/{place}")
        expect(page.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        members = {path for path in scripts if path.endswith(".js")}
        assert members, "the premise: the site's script was asked for"

        database().users.update_one({"email": email}, {"$set": {"is_admin": True}})
        page.goto(f"{base}/#/admin")
        page.reload()
        expect(page.locator(".tab.on")).to_be_visible()
        assert {path for path in scripts if path.endswith(".js")} - members, "Admin is in everybody's file"
        browser.close()


def test_the_built_site_holds_no_review_nobody_wrote(built):
    """The stand-in reviews are for the development site. On the built one they were never shown and were all
    there, in the file every visitor downloads: words nobody wrote, under names, one view-source away."""
    source = (built.parent / "src" / "landing" / "reviews.ts").read_text(encoding="utf-8")
    names = re.findall(r'\{ name: "([^"]+)", role: "[^"]+", stars: \d, sample: true', source)
    assert len(names) > 5, "the premise: the stand-ins are still written in the source"

    shipped = "".join(path.read_text(encoding="utf-8") for path in (built / "assets").glob("*.js"))

    assert not [name for name in names if name in shipped]


def test_a_job_opens_with_its_whole_text_at_once_on_jobs_bookmarks_and_applied(site, capsys):
    """Owner, 2026-11-21: "when we click the job, we should get an instant fetch of the description, there
    should be no waiting to open a job, not on the job board, and not on applied or bookmarked either". Each
    page asks for the texts of the jobs it lists as soon as it has the list, in one request. Here that request
    is let through and every later one for a job's text is refused: a job that still opens with its text had
    it before it was opened. Each page is loaded afresh, so each is shown to read ahead for itself."""
    base, _, _ = site
    text = "Python, Postgres, on call one week in six."

    def texts(response) -> bool:
        return _path(response.url) == "/jobs/descriptions"

    def refuse_more(page) -> None:
        page.route(lambda url: _path(url) == "/jobs/descriptions" or _path(url).endswith("/description"),
                   lambda route: route.abort())

    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        _card(page, TITLES[0]).get_by_role("button", name="Bookmark this job").click()
        with page.expect_response(lambda response: "/applied" in response.url):
            _card(page, TITLES[1]).get_by_role("button", name="Mark as applied").click()

        for place, title in (("", TITLES[0]), ("bookmarks", TITLES[0]), ("applied", TITLES[1])):
            page.unroute_all()
            page.goto(f"{base}/#/{place}")
            with page.expect_response(texts):
                page.reload()
            refuse_more(page)
            page.locator("article.job-card").get_by_role("button", name=title, exact=True).click()
            view = page.get_by_role("dialog", name=title)
            expect(view.locator(".description")).to_contain_text(text)
            expect(view.get_by_text("Loading the whole job…")).to_have_count(0)
            view.get_by_role("button", name="Close").click()
        browser.close()


def test_every_page_has_an_address_without_a_hash_and_an_old_address_still_lands_on_it(site, capsys):
    """Owner, 2026-11-21: "instead of /#/ we can add something that is more appropriate like /jobs or
    /applied". A link goes to `/applied`; that address opened by itself, or loaded again, is that page, though
    the same words are where the page's own data is read from; and an address from before, in an email or a
    bookmark, lands on the page it meant."""
    base, _, _ = site
    places = {"Applied": "/applied", "My search": "/words", "Alerts": "/alerts", "My documents": "/documents",
              "Settings": "/settings", "Jobs": "/jobs"}
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        here = page.locator("nav .places a[aria-current=page]")
        asks = "fetch(location.pathname).then((answer) => answer.headers.get('content-type'))"

        for name, address in places.items():
            page.locator("nav .places a", has_text=name).click()
            expect(here).to_have_attribute("href", address)
            assert urlparse(page.url).path == address and "#" not in page.url, page.url
            page.reload()
            expect(here).to_have_attribute("href", address)
            if address in ("/applied", "/alerts", "/documents"):
                assert page.evaluate(asks) == "application/json", "the page's own code is given the data"

        for old, address in (("/#/applied", "/applied"), ("/#/feed?job=1", "/jobs?job=1"),
                             ("/#/alerts?alert=1", "/alerts?alert=1")):
            page.goto("about:blank")
            page.goto(f"{base}{old}")
            page.wait_for_function("(to) => location.pathname + location.search === to", arg=address)
            expect(here).to_have_attribute("href", address.split("?")[0])
        browser.close()


def test_the_token_in_an_emailed_link_is_never_part_of_a_request(site, capsys):
    """The link in a confirmation email is `/verify#token=…`: what follows the `#` stays in the browser, so
    the token is in no address a server is asked for, and so in no log on the way. It is sent once, in the
    body of the request that spends it. And a link emailed before the addresses changed still confirms."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        asked: list[str] = []
        page.on("request", lambda request: asked.append(request.url))
        page.goto(f"{base}/signup")
        _fill_sign_up(page, email)
        link = _emailed(capsys, "verify")
        token = parse_qs(urlsplit(link).fragment)["token"][0]

        page.goto(link)
        expect(page.get_by_role("heading", name="Choose your plan")).to_be_visible()
        assert asked and not [url for url in asked if token in url]

        other = browser.new_context().new_page()
        other.goto(f"{base}/signup")
        _fill_sign_up(other, f"sam.{uuid.uuid4().hex[:8]}@example.com")
        new = urlsplit(_emailed(capsys, "verify"))
        other.goto(f"{base}/#/verify?{new.fragment}")
        expect(other.get_by_role("heading", name="Choose your plan")).to_be_visible()
        browser.close()


def test_the_landing_page_is_a_file_a_visitor_is_sent_at_once_and_somebody_signed_in_is_not(site, capsys):
    """The landing page came in the one file everybody downloads (owner, 2026-11-21: "we can make the landing
    page its own file"). A visitor's browser asks for it from the page itself, before the site's script has
    run, so it costs no round trip (measured: 3.04 s either way on a slow connection); somebody signed in is
    never sent it."""
    base, _, _ = site

    def scripts(page) -> list[str]:
        asked: list[str] = []
        page.on("request", lambda request: asked.append(_path(request.url)))
        return asked

    with sync_api.sync_playwright() as playwright:
        browser, _, member = _someone(playwright, site, capsys)
        asked = scripts(member)
        member.reload()
        expect(member.get_by_role("heading", name="2 jobs match your words")).to_be_visible()
        assert [path for path in asked if path.endswith(".js")], "the premise: the site's script was asked"
        assert not [path for path in asked if "/assets/Landing-" in path]

        visitor = browser.new_context().new_page()
        asked = scripts(visitor)
        visitor.goto(base)
        expect(visitor.locator(".landing")).to_be_visible()
        named = visitor.locator('meta[name="landing"]').get_attribute("content")
        assert named and asked.index(named) < asked.index("/dashboard"), "asked before the site's script ran"
        assert asked.count(named) == 1
        browser.close()


def test_more_bookmarks_than_a_page_say_how_many_and_show_more_brings_the_rest(site, db, capsys):
    """Bookmarks drew the first hundred it was sent and counted those: with more, the heading said a smaller
    number and nothing led to the others. It says how many there are, shows a page as Jobs does, and "Show
    more jobs" brings the rest, each once, and then is gone. One skipped is one fewer in the heading."""
    from libs.db.models import User
    from libs.db.models.tenant import record_state
    from services.api.dashboard_routes import FEED_PAGE

    _, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser, _, page = _someone(playwright, site, capsys)
        user_id = db[User.COLLECTION].find_one({"email": email})["_id"]
        kept = _jobs(db, uuid.uuid4().hex[:8], [f"Bookkeeper {n}" for n in range(FEED_PAGE + 2)])
        for n, job_id in enumerate(kept.values()):
            record_state(db, user_id, job_id, bookmarked_at=datetime.now(UTC) - timedelta(minutes=n))

        page.get_by_role("navigation", name="Jobs").get_by_role("link", name="Bookmarks").click()
        expect(page.get_by_role("heading", name=f"{FEED_PAGE + 2} bookmarked jobs")).to_be_visible()
        expect(page.locator("article.job-card")).to_have_count(FEED_PAGE)
        _skip(page, "Bookkeeper 0")
        expect(page.get_by_role("heading", name=f"{FEED_PAGE + 1} bookmarked jobs")).to_be_visible()

        page.get_by_role("button", name="Show more jobs").click()
        expect(page.locator("article.job-card")).to_have_count(FEED_PAGE + 1)
        titles = page.locator("article.job-card button.title").all_inner_texts()
        assert len(set(titles)) == len(titles) == FEED_PAGE + 1, "each once, the skipped one not among them"
        expect(page.get_by_role("button", name="Show more jobs")).to_have_count(0)
        expect(page.get_by_role("heading", name=f"{FEED_PAGE + 1} bookmarked jobs")).to_be_visible()
        browser.close()


def test_the_fonts_the_site_is_set_in_are_asked_for_from_the_page_itself(site):
    """A font was asked for only when the first words were drawn, so they were drawn in another font and
    again in their own a moment later, and the page moved (layout shift 0.021 on a slow connection). The two
    that Latin text is set in are named in the page, asked for before the site's script has run, each once."""
    base, _, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        asked: list[str] = []
        page.on("request", lambda request: asked.append(_path(request.url)))
        page.goto(base)
        expect(page.locator(".landing .hero-title")).to_be_visible()
        page.evaluate("() => document.fonts.ready")

        named = page.locator('link[rel="preload"][as="font"]').evaluate_all(
            "(links) => links.map((link) => new URL(link.href).pathname)")
        assert sorted(name.split("/")[-1].split("-")[0] for name in named) == ["inter", "sora"], named
        assert sorted(path for path in asked if path.endswith(".woff2")) == sorted(named), (
            "the two the page is drawn in, each asked for once, and no other")
        assert all(asked.index(font) < asked.index("/dashboard") for font in named), (
            "before the script that draws the page had asked for anything")
        browser.close()


def test_a_sign_up_refused_by_something_other_than_the_site_says_so_and_what_to_do(site):
    """An answer that is not the server's own, such as Cloudflare's, carries no message of ours, and the form
    showed the status's words: none at all over HTTP/2 and 3, as Safari reaches the site. Its button stopped,
    nothing was said, and no email came (2026-11-22: iPhone Safari users, whose requests come through iCloud
    Private Relay). A check Cloudflare wants the browser to pass says to reload, where a page load can pass
    it; anything else says what went wrong, with its code; and the same form then signs up as ever."""
    base, email, _ = site

    def answered(status: int, headers: dict):
        def answer(route) -> None:
            route.fulfill(status=status, headers=headers, content_type="text/html",
                          body="<!DOCTYPE html><title>Just a moment...</title>")
        return answer

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        page.goto(f"{base}/signup")
        page.get_by_label("Email").fill(email)
        page.get_by_label(re.compile(r"^Password")).fill(PASSWORD)
        page.get_by_label("Confirm password").fill(PASSWORD)
        page.get_by_label(re.compile(r"^I agree to the Terms, including the limits on our liability")).check()
        create = page.get_by_role("button", name="Create account")

        page.route("**/auth/signup", answered(403, {"cf-mitigated": "challenge"}))
        create.click()
        expect(page.locator("p.error")).to_have_text(
            "The site needs to check this browser first. Reload the page, then try again.")

        page.unroute("**/auth/signup")
        page.route("**/auth/signup", answered(502, {}))
        create.click()
        expect(page.locator("p.error")).to_have_text(
            "Something went wrong (error 502). Reload the page and try again.")

        page.unroute("**/auth/signup")
        create.click()
        expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
        browser.close()


def test_a_refused_password_says_why_and_a_spent_link_offers_the_way_in(site):
    """The password rules answer with what is wrong (`{"password": [...]}`), which the sign-up form could not
    say: nothing showed, the button stopped, and the live log had eight refused sign-ups in an evening, each
    with no email to wait for (2026-11-22). And a link opened after the address was confirmed is spent, so its
    page offers signing in, where it offered only another link."""
    base, email, _ = site
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        page.goto(f"{base}/signup")
        # Under 20 characters with the address's own name in it, which the rules refuse.
        _fill_password_and_terms(page, email, email.split("@")[0] + "1")
        page.get_by_role("button", name="Create account").click()
        expect(page.locator("p.error")).to_have_text("Password must not contain your email address.")

        page.goto(f"{base}/verify#token=spent")
        expect(page.get_by_role("heading", name="That link did not work")).to_be_visible()
        page.get_by_role("link", name="Sign in").click()
        expect(page).to_have_url(f"{base}/signin")
        browser.close()


def _fill_password_and_terms(page, email: str, password: str) -> None:
    page.get_by_label("Email").fill(email)
    page.get_by_label(re.compile(r"^Password")).fill(password)
    page.get_by_label("Confirm password").fill(password)
    page.get_by_label(re.compile(r"^I agree to the Terms, including the limits on our liability")).check()
