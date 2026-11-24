"""The source registry, and the structural rules the adapter port had to preserve."""
from __future__ import annotations

import ast
import logging
import os
import re
import uuid
from pathlib import Path

import httpx
import pytest

from services.harvester.sources import (
    OUTER_CONCURRENCY,
    REGISTRY,
    Source,
)
from services.harvester.sources.base import JobLink
from tests.conftest import needs_db

HARVESTER = Path(__file__).resolve().parents[1] / "services" / "harvester"
ADAPTERS = sorted((HARVESTER / "sources").glob("*.py"))


# ── the one-factory rule ──────────────────────────────────────────────────────────────────────

def test_no_adapter_builds_its_own_http_client():
    """The rule the whole fetch layer rests on: `fetch.py` builds every client, and nothing else does."""
    offenders = [
        f"{path.relative_to(HARVESTER)}:{i}"
        for path in HARVESTER.rglob("*.py") if path != HARVESTER / "fetch.py"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bhttpx\.Client\(", line)
    ]
    assert offenders == [], (
        "these construct an HTTP client directly instead of using services.harvester.fetch: "
        + ", ".join(offenders))


def _env_reads(path: Path) -> list[tuple[int, str]]:
    """Every environment-variable name a module actually reads, with its line number."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def _is_environ(node: ast.expr) -> bool:
        """`os.environ` or a bare `environ`. Not any object that happens to have `.get`."""
        return (isinstance(node, ast.Attribute) and node.attr == "environ") or \
            (isinstance(node, ast.Name) and node.id == "environ")

    for node in ast.walk(tree):
        # os.environ.get("X") / os.getenv("X") — but NOT some_dict.get("proxy").
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            is_env_get = node.func.attr == "get" and _is_environ(node.func.value)
            is_getenv = node.func.attr == "getenv"
            first = node.args[0]
            if (is_env_get or is_getenv) and isinstance(first, ast.Constant) \
                    and isinstance(first.value, str):
                found.append((node.lineno, first.value))
        # os.environ["X"]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ" and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            found.append((node.lineno, node.slice.value))
    return found


def test_the_fetch_layer_is_the_only_place_that_reads_a_proxy_setting():
    """A proxy read anywhere else is a proxy no lease governs."""
    offenders = [
        f"{path.relative_to(HARVESTER)}:{line} reads {name}"
        for path in HARVESTER.rglob("*.py") if path.name != "fetch.py"
        for line, name in _env_reads(path)
        if re.search(r"PROXY", name, re.IGNORECASE)
    ]
    assert offenders == [], "proxy configuration read outside fetch.py: " + ", ".join(offenders)


def test_the_check_above_would_actually_catch_one():
    """A guard that cannot fail is worse than none, so prove this one detects the thing it forbids."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        offender = Path(tmp) / "sneaky.py"
        offender.write_text(
            'import os\n'
            '"""A docstring mentioning HTTP_PROXY must NOT trigger this."""\n'
            'proxy = os.environ.get("PROXY_URL")\n', encoding="utf-8")
        names = [name for _, name in _env_reads(offender)]
        assert "PROXY_URL" in names, "the check would miss a real environment read"

        innocent = Path(tmp) / "prose.py"
        innocent.write_text('"""Mentions HTTP_PROXY and ALL_PROXY in prose only."""\n',
                            encoding="utf-8")
        assert _env_reads(innocent) == [], "the check flags documentation as a violation"


# ── the registry ──────────────────────────────────────────────────────────────────────────────

def test_linkedin_is_not_a_board_the_shared_loop_can_be_handed():
    """LinkedIn is read by its own pass, through the site proxy (`services.harvester.linkedin`); a board
    here goes out direct, so LinkedIn listed here would be read from the server's own address."""
    assert REGISTRY and not [key for key in REGISTRY if "linkedin" in key]


def test_every_board_with_an_adapter_is_one_a_run_can_be_told_to_read():
    """A board is read when it is on the one list (`HARVEST_SOURCES`) and has an adapter here. Two had an
    adapter and a place on no list, Hacker News' hiring thread and No Fluff Jobs' Hungarian site: code that
    read as live and that no setting could ever switch on (2026-11-20)."""
    from libs.settings import HARVEST_SOURCES

    assert set(REGISTRY) <= set(HARVEST_SOURCES)


def test_only_the_ats_family_needs_the_token_and_etag_repository():
    """Everything else is stateless."""
    needing = {s.key for s in REGISTRY.values() if s.needs_ats_repo}
    assert needing == {"greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
                       "breezy", "workday", "personio", "workable", "teamtailor"}


TEAMTAILOR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><title>Vardaga</title>
<item><title>Undersköterska</title><link>https://vardaga.teamtailor.com/jobs/1-underskoterska</link>
<pubDate>Mon, 23 Nov 2026 13:58:33 +0100</pubDate>
<description>&lt;p&gt;Vi söker en undersköterska.&lt;/p&gt;</description><remoteStatus>none</remoteStatus>
<tt:locations><tt:location><tt:name>Pilfinksgården</tt:name><tt:city>Västerås</tt:city>
<tt:country>Sweden </tt:country></tt:location></tt:locations></item>
<item><title>Support Agent</title><link>https://vardaga.teamtailor.com/jobs/2-support</link>
<pubDate>Sun, 22 Nov 2026 09:00:00 +0100</pubDate><description>Answer families by phone.</description>
<remoteStatus>fully</remoteStatus><tt:locations></tt:locations></item>
</channel></rss>"""


def test_a_teamtailor_board_is_read_from_its_feed_each_job_whole_with_its_places(monkeypatch):
    """Teamtailor publishes every company's jobs as a feed for job boards to read. Our postings named 17
    companies on it, care homes, restaurants and builders among them, and none was read (2026-11-23)."""
    from datetime import UTC, datetime

    from libs.corpus.filters import parse_date
    from libs.corpus.geo import posting_country
    from services.harvester.sources import ats

    def teamtailor(request: httpx.Request) -> httpx.Response:
        if request.url.host != "vardaga.teamtailor.com":
            return httpx.Response(404)
        if request.headers.get("if-none-match") == 'W/"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text=TEAMTAILOR_FEED, headers={"etag": 'W/"v1"'})

    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(teamtailor)))
    jobs, etag = ats.fetch_teamtailor("vardaga")

    assert [(job.title, job.company, job.location) for job in jobs] == [
        ("Undersköterska", "Vardaga", "Västerås, Sweden"), ("Support Agent", "Vardaga", "")]
    assert posting_country(jobs[0].location) == "SE"
    assert (jobs[0].is_remote, jobs[1].is_remote) == (None, True)
    assert parse_date(jobs[0].date_posted) == datetime(2026, 11, 23, 12, 58, 33, tzinfo=UTC)
    assert jobs[0].snippet == "Vi söker en undersköterska." and jobs[0].source == "teamtailor"
    assert ats.fetch_teamtailor("vardaga", etag)[0] is None, "unchanged since: not modified"
    assert ats.fetch_teamtailor("nosuchcompany")[0] is ats.GONE


def test_every_registry_entry_is_callable_and_well_formed():
    for key, source in REGISTRY.items():
        assert isinstance(source, Source)
        assert source.key == key, "the registry key and the Source key must agree"
        assert callable(source.adapter)


def test_the_outer_concurrency_cap_is_not_raised():
    """From the 2026-07-24 incident: JobSpy sources open their own nested pools."""
    assert OUTER_CONCURRENCY == 8


# ── the JobLink contract ──────────────────────────────────────────────────────────────────────

def test_a_joblink_maps_onto_a_corpus_row_and_carries_no_user():
    """A source adapter has no access to a user and must not acquire one."""
    row = JobLink(url="https://boards.greenhouse.io/acme/jobs/1", title="Backend Engineer",
                  company="Acme", location="London", source="greenhouse",
                  is_remote=True).as_corpus_row()

    assert row["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert row["source"] == "greenhouse" and row["title"] == "Backend Engineer"
    assert row["company"] == "Acme" and row["location"] == "London" and row["is_remote"] is True
    assert not any("user" in key for key in row)


def test_a_joblink_carries_the_posting_metadata_the_feed_card_reads():
    """Both of these were collected and then dropped."""
    row = JobLink(url="https://www.linkedin.com/jobs/view/1", title="Backend Engineer (Remote)",
                  company="Acme", location="London", source="linkedin",
                  date_posted="2026-07-29T08:00:00+00:00", easy_apply=True,
                  is_remote=True).as_corpus_row()

    assert row["posted_at"] is not None and row["posted_at"].year == 2026
    assert row["easy_apply"] is True
    # Classified from what the source gave us; LinkedIn's own pill overwrites it at fetch time.
    assert row["workplace"] == "remote"


def test_joblink_defaults_are_safe_for_an_unknown_field():
    """Sources disagree about what they report."""
    link = JobLink(url="https://example.test/1")
    assert link.is_remote is None
    assert link.date_posted is None
    assert link.application_url is None
    assert link.found_at


@pytest.mark.parametrize("path", ADAPTERS, ids=lambda p: p.name)
def test_every_adapter_module_imports(path):
    """A port that leaves a module unimportable is a source that silently never runs."""
    import importlib
    importlib.import_module(f"services.harvester.sources.{path.stem}")


# ── company boards that are slow, or that refuse a burst (2026-11-02) ─────────────────────────

def test_a_lever_board_slow_to_answer_is_read_where_other_boards_would_give_up(monkeypatch):
    """Lever answers a board it has not served lately in up to 41 s, measured; at the 15 s every
    board had, each such board failed every pass. Here the "server" takes 20 s."""
    import httpx

    from services.harvester.sources import ats

    def slow_server(request: httpx.Request) -> httpx.Response:
        if request.extensions["timeout"]["read"] < 20:
            raise httpx.ReadTimeout("gave up before the board answered", request=request)
        return httpx.Response(200, json=[{"hostedUrl": "https://jobs.lever.co/acme/1", "text": "Engineer",
                                          "categories": {"location": "Berlin"}, "createdAt": 0}])

    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(slow_server)))

    jobs, _etag = ats.fetch_lever("acme")
    assert [job.title for job in jobs] == ["Engineer"]
    with pytest.raises(httpx.ReadTimeout):
        ats.fetch_greenhouse("acme")


def test_workable_is_asked_two_boards_at_a_time():
    """Sixteen at once got 490 refusals in 555, and the harvest asked thirty."""
    import threading
    import time
    import uuid

    from services.harvester.source_config import build_source_configs
    from services.harvester.sources.ats import gather_ats

    class Boards:
        def live_tokens(self, _ats):
            return [f"company{n}" for n in range(8)]

        def etags(self, _ats):
            return {}

        def names(self, _ats):
            return {}

        def record_poll(self, *_args, **_kwargs):
            pass

        def record_asked(self, *_args, **_kwargs):
            pass

    lock, now, most = threading.Lock(), [0], [0]

    def fetch(_token, _etag, within=None):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        time.sleep(0.05)
        with lock:
            now[0] -= 1
        return [], None

    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": ["engineer"]}]}}, env={})["workable"]
    gather_ats(cfg, logging.getLogger("test"), ats="workable", repo=Boards(), fetch_company=fetch)

    assert most[0] == 2


def test_just_join_it_is_read_page_by_page_until_its_offers_are_older_than_the_window(monkeypatch):
    """Its old API answers 503 to everything since 2026-11-01; the site's own one lists newest first,
    a page at a time. A page that fails keeps what was read before it."""
    from datetime import UTC, datetime, timedelta

    import httpx

    from services.harvester.sources import http_apis

    def offer(n: int, hours_ago: float) -> dict:
        when = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        return {"slug": f"acme-dev-{n}", "title": f"Python Developer {n}", "companyName": "Acme",
                "city": "Kraków", "workplaceType": "remote", "publishedAt": when,
                "requiredSkills": [{"name": "Python"}]}

    pages = {0: [offer(1, 1), offer(2, 5)], 2: [offer(3, 20), offer(4, 30)], 4: [offer(5, 40), offer(6, 50)]}
    asked: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        assert request.url.host == "justjoin.it" and request.url.path == "/api/candidate-api/offers"
        start = int(request.url.params["from"])
        return httpx.Response(200, json={"data": pages[start]}) if start in pages else httpx.Response(503)

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))
    cfg = {"max_pages": 5, "per_page": 2, "max_age_hours": 24}

    links = http_apis.gather_justjoinit(cfg, logging.getLogger("test"))

    assert [link.url.rsplit("-", 1)[1] for link in links] == ["1", "2", "3"], "within 24 hours, newest first"
    assert len(asked) == 2, "the second page reached past the window, so no third was asked for"
    assert links[0].is_remote is True and links[0].company == "Acme"

    pages.pop(2)
    kept = http_apis.gather_justjoinit(cfg, logging.getLogger("t"))
    assert [link.url.rsplit("-", 1)[1] for link in kept] == ["1", "2"], "a failed page keeps the ones before"


def test_arbeitnow_is_read_pages_deep_because_its_pages_are_in_no_date_order(monkeypatch):
    """Page 2 held a job 0.9 hours old beside ones 83 hours old, and every job listed in the last hour was on
    the first two pages (2026-11-22). So a run reads the first `max_pages` whatever their dates say, and keeps
    each job listed inside the window."""
    from datetime import UTC, datetime, timedelta

    from services.harvester.sources import http_apis

    def job(name: str, hours_ago: float, remote: bool = False) -> dict:
        return {"url": f"https://www.arbeitnow.com/jobs/companies/acme/{name}", "title": "Pflegekraft",
                "company_name": "Acme Pflege GmbH", "location": "Munich", "remote": remote,
                "created_at": int((datetime.now(UTC) - timedelta(hours=hours_ago)).timestamp()),
                "description": "<p>Pflege im Nachtdienst.</p><ul><li>Vollzeit</li></ul>"}

    pages = {1: [job("new", 0.5, remote=True), job("old", 30)], 2: [job("newer", 0.2)],
             3: [job("two-hours", 2)], 4: [job("past-the-pages", 0.1)]}
    asked: list[int] = []

    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.arbeitnow.com" and request.url.path == "/api/job-board-api"
        page = int(request.url.params["page"])
        asked.append(page)
        return httpx.Response(200, json={"data": pages[page], "links": {"next": f"?page={page + 1}"}})

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))

    links = http_apis.gather_arbeitnow({"max_pages": 3, "max_age_hours": 24}, logging.getLogger("test"))

    assert asked == [1, 2, 3]
    assert [link.url.rsplit("/", 1)[1] for link in links] == ["new", "newer", "two-hours"]
    assert links[0].is_remote is True and links[1].is_remote is None, "not remote may be hybrid: unknown"
    assert links[0].source == "arbeitnow" and links[0].company == "Acme Pflege GmbH"
    assert "Pflege im Nachtdienst." in links[0].snippet and "• Vollzeit" in links[0].snippet


def test_remote_ok_is_read_whole_in_one_request_and_named_as_its_terms_ask(monkeypatch):
    """Its whole board, newest first, in one request. The first item is its terms: a link back to the job on
    Remote OK and its name shown, which a card's "Apply on Remote OK" is."""
    from datetime import UTC, datetime, timedelta

    from libs.corpus.boards import apply_on
    from services.harvester.sources import http_apis

    def job(slug: str, hours_ago: float, location: str = "") -> dict:
        return {"id": slug, "url": f"https://remoteOK.com/remote-jobs/{slug}", "position": "Support Agent",
                "company": "Acme", "location": location, "description": "<p>Answer customers by email.</p>",
                "date": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()}

    asked: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json=[{"legal": "API Terms of Service: Please link back ..."},
                                         job("fresh", 1), job("europe", 3, "Europe"), job("stale", 50)])

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))

    links = http_apis.gather_remoteok({"max_age_hours": 24}, logging.getLogger("test"))

    assert asked == ["https://remoteok.com/api"]
    assert [link.url.rsplit("/", 1)[1] for link in links] == ["fresh", "europe"]
    assert links[0].location == "Remote" and links[1].location == "Europe"
    assert all(link.is_remote and link.source == "remoteok" for link in links)
    assert apply_on("remoteok") == "Remote OK"


def test_reed_is_read_pages_deep_its_teaser_never_taken_for_the_text(monkeypatch):
    """Reed offers no filter by time and no sort, and its order is its own, so a run reads its first pages
    with no word and keeps what is dated inside the window. A date has no time: two days hold today's and
    yesterday's. Its search's text, a 450-character teaser ending "...", is left out so the whole is read."""
    from datetime import UTC, date, datetime, timedelta

    from libs.corpus.geo import posting_country
    from services.harvester.sources import http_apis

    def job(n: int, days_ago: int, where: str = "Glasgow") -> dict:
        day = (datetime.now(UTC).date() - timedelta(days=days_ago)).strftime("%d/%m/%Y")
        return {"jobId": n, "jobUrl": f"https://www.reed.co.uk/jobs/support-worker/{n}",
                "jobTitle": "Support Worker", "employerName": "Acme Care", "locationName": where, "date": day,
                "jobDescription": "Support adults with learning disabilities in their own homes..."}

    pages = {0: [job(1, 0), job(2, 1500)], 2: [job(3, 1, "Chippenham")], 4: [job(4, 0)]}
    asked: list[dict] = []

    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/1.0/search"
        asked.append(dict(request.url.params))
        return httpx.Response(200, json={"results": pages[int(request.url.params["resultsToSkip"])]})

    monkeypatch.setattr(http_apis, "REED_PAGE", 2)
    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))

    links = http_apis.gather_reed({"max_pages": 5, "max_age_hours": 48}, logging.getLogger("test"))

    assert [each["resultsToSkip"] for each in asked] == ["0", "2"], "a short page ends the walk"
    assert all("keywords" not in each for each in asked)
    assert [link.url.rsplit("/", 1)[1] for link in links] == ["1", "3"], "today's and yesterday's"
    assert links[1].location == "Chippenham, United Kingdom" and posting_country(links[1].location) == "GB"
    assert all(link.snippet == "" and link.source == "reed" for link in links)
    assert links[0].date_posted == date.today().isoformat()


def test_teaching_vacancies_is_read_newest_first_until_its_pages_pass_the_window(monkeypatch):
    """England's school jobs, the Department for Education's open API: 100 a page, newest first, each a
    schema.org JobPosting with its whole text (6,370 on 2026-11-23, its first two pages that day's). A date
    has no time, so two days hold today's and yesterday's; the walk ends at the page reaching past them."""
    from datetime import UTC, datetime, timedelta

    from libs.corpus.geo import posting_country
    from services.harvester.sources import http_apis

    def job(n: int, days_ago: int) -> dict:
        return {"url": f"https://teaching-vacancies.service.gov.uk/jobs/teacher-{n}",
                "title": "Science Teacher", "description": "<p>Teach science to Key Stage 3 and 4.</p>",
                "datePosted": (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat(),
                "hiringOrganization": {"name": "Harris Academy"},
                "jobLocation": {"address": {"addressLocality": "Royal Leamington Spa"}}}

    pages = {1: [job(1, 0), job(2, 0)], 2: [job(3, 1), job(4, 9)], 3: [job(5, 0)]}
    asked: list[int] = []

    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs.json"
        asked.append(int(request.url.params["page"]))
        return httpx.Response(200, json={"data": pages[asked[-1]], "meta": {"totalPages": 64}})

    monkeypatch.setattr(http_apis, "TEACHING_PAGE", 2)
    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))

    links = http_apis.gather_teachingvacancies({"max_pages": 10, "max_age_hours": 48}, logging.getLogger("t"))

    assert asked == [1, 2], "the second page reached past the window, so no third was asked for"
    assert [link.url.rsplit("-", 1)[1] for link in links] == ["1", "2", "3"]
    assert (links[0].company, links[0].location) == ("Harris Academy", "Royal Leamington Spa, United Kingdom")
    assert posting_country(links[0].location) == "GB"
    assert links[0].snippet == "Teach science to Key Stage 3 and 4."


def test_each_boards_key_goes_to_its_own_api_and_nowhere_else(monkeypatch):
    """Reed takes its key as a basic-auth user name, NAV its token as a bearer. Added where every client is
    built, so the page reader can ask each one's API without holding a key, and sent to no other site, the
    boards' own pages included."""
    import base64

    from services.harvester.fetch import make_shared_client

    heard: dict[str, str | None] = {}

    def record(request: httpx.Request) -> httpx.Response:
        heard[str(request.url)] = request.headers.get("Authorization")
        return httpx.Response(200)

    monkeypatch.setenv("REED_API_KEY", "a-reed-key")
    monkeypatch.setenv("NAV_API_TOKEN", "a-nav-token")
    with make_shared_client(transport=httpx.MockTransport(record)) as client:
        for url in ("https://www.reed.co.uk/api/1.0/jobs/1", "https://www.reed.co.uk/jobs/a/1",
                    "https://pam-stilling-feed.nav.no/api/v1/feed", "https://arbeidsplassen.nav.no/stillinger",
                    "https://example.com/api/1.0/jobs/1"):
            client.get(url)

    basic = "Basic " + base64.b64encode(b"a-reed-key:").decode()
    assert heard == {"https://www.reed.co.uk/api/1.0/jobs/1": basic, "https://www.reed.co.uk/jobs/a/1": None,
                     "https://pam-stilling-feed.nav.no/api/v1/feed": "Bearer a-nav-token",
                     "https://arbeidsplassen.nav.no/stillinger": None,
                     "https://example.com/api/1.0/jobs/1": None}


def test_nav_is_read_from_what_changed_inside_the_window_a_withdrawn_ad_passed_on_closed(monkeypatch):
    """NAV's official feed of Norway's job ads: everything changed since a moment (If-Modified-Since), page
    after page (86 changes in two hours of a Monday afternoon, 76 live; 2026-11-23). A withdrawn ad is passed
    on closed, as NAV's terms ask that it leave at once; a live one's text is read from its entry after. Each
    change is an entry, so an ad changed twice is listed twice (75 entries for 65 ads in two hours, measured
    live 2026-11-23): it is passed on once, as it stands last, or one withdrawn after it went up stays up."""
    from libs.corpus.geo import posting_country
    from services.harvester.sources import http_apis

    def entry(uuid: str, status: str) -> dict:
        return {"url": f"/api/v1/feedentry/{uuid}", "_feed_entry": {
            "uuid": uuid, "status": status, "title": "Sjåfør", "businessName": "Arnesen Transport",
            "municipal": "SØR-VARANGER", "sistEndret": "2026-11-23T15:38:16+01:00"}}

    pages = {"/api/v1/feed": {"items": [entry("a" * 36, "ACTIVE"), entry("b" * 36, "INACTIVE")],
                              "next_url": "/api/v1/feed/next"},
             "/api/v1/feed/next": {"items": [entry("a" * 36, "INACTIVE"), entry("c" * 36, "ACTIVE")],
                                   "next_url": "/api/v1/feed/last"},
             "/api/v1/feed/last": {"items": [], "next_url": "/api/v1/feed/last"}}
    asked: list[tuple[str, str | None]] = []

    def feed(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, request.headers.get("If-Modified-Since")))
        return httpx.Response(200, json=pages[request.url.path])

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(feed)))

    links = http_apis.gather_nav({"max_pages": 5, "max_age_hours": 2}, logging.getLogger("test"))

    assert [path for path, _since in asked] == ["/api/v1/feed", "/api/v1/feed/next", "/api/v1/feed/last"]
    assert asked[0][1] and asked[0][1].endswith("GMT"), "the first page is asked from the moment"
    assert asked[1][1] is None, "the next page's address carries its own place"
    assert [(link.url.rsplit("/", 1)[1][:1], link.closed) for link in links] == [
        ("a", True), ("b", True), ("c", False)], "an ad listed twice is passed on once, as it stands last"
    assert (links[2].company, links[2].location) == ("Arnesen Transport", "Sør-Varanger, Norway")
    assert posting_country(links[2].location) == "NO" and links[2].snippet == ""


def test_feina_activa_reads_catalonias_offers_inside_the_window_with_their_whole_text(monkeypatch):
    """Catalonia's public employment service (the SOC) publishes every offer in one XML file made as it is
    asked, in two lists, its board's and its offices', each dated without a time (4,949 offers, 116 dated
    that day by 17:00; 2026-11-23). One inside the window is passed on with its parts, in order, as its
    text, and its town in Spain as it is said ("Albi, L'" is L'Albi); an older one, or one no longer
    published, is not."""
    from datetime import UTC, datetime, timedelta

    from libs.corpus.geo import posting_country
    from services.harvester.sources import http_apis

    today, old = (f"{datetime.now(UTC) - timedelta(days=days):%d/%m/%Y}" for days in (0, 10))

    def ad(ad_id: str, day: str, town: str = "CALELLA", status: str = "PUBLISHED") -> str:
        return (f"<ad><id>{ad_id}</id><url>https://feinaactiva.gencat.cat/search/offers/detail/{ad_id}</url>"
                "<title>CAMBRER/A MENJADOR</title>"
                "<content>Retirar platos de las mesas, servir bebidas.</content>"
                "<company>Empresa de restauració</company><experience>Experiència 0 anys.</experience>"
                "<requirements>anglès (parlat Baix, escrit Baix)</requirements><studies></studies>"
                "<contract>Contracte laboral temporal (2 mesos)</contract>"
                "<workingHours>Jornada completa</workingHours>"
                f"<date>{day}</date>"
                f"<city>{town}</city><region>BARCELONA</region><status>{status}</status></ad>")

    board = f"{ad('FA1', today)}{ad('FA2', old)}{ad('FA3', today, status='CLOSED')}"
    offices = ad("0920", today, town="ALBI, L&apos;")
    body = f"<ofertes><feinaactiva>{board}</feinaactiva><sicas>{offices}</sicas></ofertes>"
    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=body))))

    links = http_apis.gather_feinaactiva({"max_age_hours": 48}, logging.getLogger("test"))

    assert [link.url.rsplit("/", 1)[1] for link in links] == ["FA1", "0920"]
    assert links[0].snippet.split("\n\n") == [
        "Retirar platos de las mesas, servir bebidas.", "Experiència 0 anys.",
        "anglès (parlat Baix, escrit Baix)", "Contracte laboral temporal (2 mesos)", "Jornada completa",
    ], "its parts in order, with contract and hours: half the thin offers were not thin with them"
    assert (links[0].company, links[0].location, links[1].location) == (
        "Empresa de restauració", "Calella, Spain", "L'Albi, Spain")
    assert posting_country(links[0].location) == "ES"
    assert links[0].date_posted == f"{datetime.now(UTC):%Y-%m-%d}", "the day it went up, in ISO"

def test_platsbanken_reads_what_went_up_inside_the_window_in_stockholm_time(monkeypatch):
    """Sweden's public employment service (JobTech's JobSearch, ads under CC0): what went up in the window's
    minutes, newest first, 100 a page until a short one. Its times are Stockholm's, written without an offset
    (an ad read at 19:51 UTC said 20:36, 2026-11-22): read as UTC, it would look an hour younger."""
    from datetime import UTC, datetime

    from libs.corpus.filters import parse_date
    from libs.corpus.geo import posting_country
    from services.harvester.sources import http_apis

    def ad(n: int) -> dict:
        return {"webpage_url": f"https://arbetsformedlingen.se/platsbanken/annonser/{n}",
                "headline": "Undersköterska", "employer": {"name": "Vara kommun"},
                "workplace_address": {"municipality": "Vara", "country": "Sverige"},
                "publication_date": "2026-11-22T20:36:58",
                "description": {"text": "Vi söker en undersköterska.\nHeltid, dagtid."},
                "application_details": {"url": f"https://jobs.example.se/apply/{n}" if n % 2 else None}}

    asked: list[dict] = []

    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "jobsearch.api.jobtechdev.se" and request.url.path == "/search"
        asked.append(dict(request.url.params))
        offset = int(request.url.params["offset"])
        count = 100 if offset == 0 else 1
        return httpx.Response(200, json={"hits": [ad(n) for n in range(offset, offset + count)]})

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(api)))

    links = http_apis.gather_platsbanken({"max_pages": 10, "max_age_hours": 2}, logging.getLogger("test"))

    assert [(each["offset"], each["published-after"], each["sort"]) for each in asked] == [
        ("0", "120", "pubdate-desc"), ("100", "120", "pubdate-desc")], "a short page ends the walk"
    assert len(links) == 101
    one = links[1]
    assert (one.title, one.company, one.source) == ("Undersköterska", "Vara kommun", "platsbanken")
    assert parse_date(one.date_posted) == datetime(2026, 11, 22, 19, 36, 58, tzinfo=UTC)
    assert posting_country(one.location) == "SE", "kept for the people who picked Sweden"
    assert one.application_url == "https://jobs.example.se/apply/1" and links[0].application_url is None
    assert one.snippet == "Vi söker en undersköterska.\nHeltid, dagtid."


def test_a_job_carries_its_companys_own_name_where_the_board_does_not_say_it(monkeypatch):
    """Ashby and Lever name no company, so cards read "Openai" and "Elevenlabs". The name the board was
    stored with fills it; a board found from a link has its address in words. SmartRecruiters says
    its name, and that is the one used, not the identifier in its address ("BoschGroup")."""
    import httpx

    from services.harvester.sources import ats

    class Boards:
        def live_tokens(self, _ats):
            return ["openai", "acme-robotics"]

        def etags(self, _ats):
            return {}

        def names(self, _ats):
            return {"openai": "OpenAI"}

        def record_poll(self, *_args, **_kwargs):
            pass

        def record_asked(self, *_args, **_kwargs):
            pass

    def fetch(token, _etag, within=None):
        return [JobLink(url=f"https://jobs.ashbyhq.com/{token}/1", title="Engineer", source="ashby")], None

    links = ats.gather_ats({"posted_within_hours": 72}, logging.getLogger("test"), ats="ashby", repo=Boards(),
                           fetch_company=fetch)
    assert sorted(link.company for link in links) == ["Acme Robotics", "OpenAI"]

    def smartrecruiters(_request: httpx.Request) -> httpx.Response:
        posting = {"id": "1", "name": "Engineer", "location": {"city": "Stuttgart", "country": "de"},
                   "company": {"identifier": "BoschGroup", "name": "Bosch Group"}}
        return httpx.Response(200, json={"content": [posting]})

    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(smartrecruiters)))
    jobs, _ = ats.fetch_smartrecruiters("BoschGroup")
    assert [job.company for job in jobs] == ["Bosch Group"]


# ── each board's text, laid out as it laid it out (2026-11-03) ────────────────────────────────

def test_a_lever_job_is_kept_whole_its_lists_as_lists(monkeypatch):
    """`descriptionPlain` is Lever's opening paragraph alone: what the job asks for, in `lists`, and the
    closing were never kept, and its `&nbsp;` lines were stored as they were."""
    import httpx

    from services.harvester.sources import ats

    posting = {"hostedUrl": "https://jobs.lever.co/acme/1", "text": "Engineer", "createdAt": 0,
               "categories": {"location": "Paris"},
               "descriptionPlain": "We build things.\n\u00a0\n",
               "description": "<div><span>We build things.</span></div><div><br></div>",
               "lists": [{"text": "What you will do", "content": "<li>Ship the ledger</li><li>Own it</li>"},
                         {"text": "What you bring", "content": "<li>Python &amp; Postgres</li>"}],
               "additional": "<div><b>Why join</b></div><div>Good people.</div>"}
    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=[posting]))))

    (job,), _etag = ats.fetch_lever("acme")

    assert job.snippet == ("We build things.\n\nWhat you will do\n\n• Ship the ledger\n• Own it\n\n"
                           "What you bring\n\n• Python & Postgres\n\nWhy join\n\nGood people.")


def test_an_ashby_jobs_lists_are_lists_not_a_paragraph_each(monkeypatch):
    import httpx

    from services.harvester.sources import ats

    job = {"jobUrl": "https://jobs.ashbyhq.com/acme/1", "title": "Engineer", "location": "London",
           "descriptionPlain": "About the role\n\n - Ship it\n\n - Own it",
           "descriptionHtml": "<p><strong>About the role</strong></p>"
                              "<ul><li>Ship it</li><li>Own it</li></ul>"}
    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"jobs": [job]}))))

    (link,), _etag = ats.fetch_ashby("acme")

    assert link.snippet == "About the role\n\n• Ship it\n• Own it"


def test_a_jobicy_job_is_kept_whole_not_its_teaser(monkeypatch):
    """The 150-character excerpt counted as described (past `MIN_DESCRIPTION`), so it was never read in
    full: every Jobicy card ended "industry leaders&hellip;"."""
    from datetime import UTC, datetime

    import httpx

    from services.harvester.sources import http_apis

    job = {"url": "https://jobicy.com/jobs/1-engineer", "jobTitle": "Go Engineer", "companyName": "Canonical",
           "jobGeo": "Anywhere", "pubDate": datetime.now(UTC).isoformat(),
           "jobExcerpt": "Canonical is a leading provider&hellip;",
           "jobDescription": "<p>Canonical is a leading provider of open source software.</p>"
                             "<ul><li>Go</li></ul>"}
    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"jobs": [job]}))))

    (link,) = http_apis.gather_jobicy({"max_age_hours": 24}, logging.getLogger("test"))

    assert link.snippet == "Canonical is a leading provider of open source software.\n\n• Go"


# ── one set of headers (2026-11-03) ───────────────────────────────────────────────────────────

def test_an_adapter_adds_to_the_page_headers_rather_than_replacing_them():
    """`headers=` used to throw the shared ones away, so adapters wrote out a User-Agent of their own,
    and Adzuna, passing only an `Accept`, went out as `python-httpx`."""
    from libs.corpus.postings import PAGE_HEADERS
    from services.harvester.fetch import make_shared_client

    with make_shared_client(headers={"Accept": "application/json"}) as client:
        assert client.headers["User-Agent"] == PAGE_HEADERS["User-Agent"]
        assert client.headers["Accept"] == "application/json"


def test_a_user_agent_is_written_in_one_place_and_profession_s_own_beside_it():
    """Five were written out across the services. Profession is the one source that must not look like
    a browser: it answers one with a 500 page, and the feed to anything else (measured 2026-11-03)."""
    root = HARVESTER.parents[1]
    written = {path.relative_to(root).as_posix() for base in ("libs", "services")
               for path in (root / base).rglob("*.py")
               if "__pycache__" not in path.parts
               and re.search(r"[\"']User-Agent[\"']\s*:", path.read_text(encoding="utf-8"))}
    assert written == {"libs/corpus/postings.py", "services/harvester/sources/http_apis.py"}


# ── every board read whole; the words are matched here (2026-11-03, 2026-11-04) ────────────────

def _feed_job(name: str, request) -> httpx.Response:
    """One "Registered Nurse" job, posted an hour ago, in the shape each feed answers with."""
    from datetime import UTC, datetime, timedelta

    posted = datetime.now(UTC) - timedelta(hours=1)
    page = dict(request.url.params).get("page", "1")
    url = f"https://example.com/{name}/nurse-{page}"
    if name == "jobicy":
        return httpx.Response(200, json={"jobs": [{
            "url": url, "jobTitle": "Registered Nurse", "companyName": "Mercy", "jobGeo": "USA",
            "pubDate": posted.isoformat(), "jobDescription": "<p>Ward nursing, nights.</p>"}]})
    if name == "workingnomads":
        return httpx.Response(200, json=[{
            "url": url, "title": "Registered Nurse", "company_name": "Mercy", "location": "Remote",
            "pub_date": posted.isoformat(), "description": "<p>Ward nursing, nights.</p>"}])
    return httpx.Response(200, headers={"Content-Type": "application/rss+xml"}, text=(
        "<rss><channel><item><title>Registered Nurse</title>"
        f"<guid>https://www.profession.hu/allas/apolo-mercy-budapest-12345</guid>"
        f"<pubDate>{posted.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        "<description>[\"Ward nursing\"]</description></item></channel></rss>"))


def _feed(monkeypatch, name: str, requests: list):
    from services.harvester.sources import http_apis

    def answer(request):
        requests.append(request)
        return _feed_job(name, request)

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    return getattr(http_apis, f"gather_{name}")


@pytest.mark.parametrize("name", ["jobicy", "workingnomads", "profession"])
def test_a_feed_keeps_a_job_nobody_has_a_word_for_yet(monkeypatch, name):
    """The word added tomorrow finds today's jobs: a feed no longer drops what today's words miss.
    A Jobicy job only open in the USA is kept too: where it may be done is each person's countries'
    to judge, not a list of regions the first account wanted."""
    from services.harvester.source_config import build_source_configs

    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": ["software engineer"]}]}},
                               env={})[name]
    links = _feed(monkeypatch, name, [])(cfg, logging.getLogger("test"))
    assert [link.title for link in links][:1] == ["Registered Nurse"]


def test_profession_is_read_in_one_request_however_many_words_people_have(monkeypatch):
    """Its newest fifteen reach back about three hours, and a run comes every fifteen minutes."""
    from services.harvester.source_config import build_source_configs

    words = [{"keywords": ["software engineer", "nurse", "könyvelő"]}]
    configs = {uuid.uuid4(): {"sections": words} for _ in range(3)}
    requests: list = []
    links = _feed(monkeypatch, "profession", requests)(
        build_source_configs(configs, env={})["profession"], logging.getLogger("test"))
    assert len(requests) == 1 and len(links) == 1


def _himalayas(monkeypatch, *, old_from_page: int = 99,
               titles=("Python Developer", "Registered Nurse", "Sales Manager", "Welder")) -> list:
    """Himalayas' list, newest first, 20 a page, every job posted an hour ago until `old_from_page`."""
    from datetime import UTC, datetime, timedelta

    from services.harvester.sources import http_apis

    requests: list = []

    def answer(request):
        requests.append(request)
        page = int(request.url.params["offset"]) // 20
        posted = datetime.now(UTC) - timedelta(hours=1 if page < old_from_page else 500)
        page_titles = list(titles) * (20 // len(titles))
        return httpx.Response(200, json={"jobs": [{
            "title": title, "companyName": f"Company {page}-{n}", "pubDate": int(posted.timestamp()),
            "guid": f"https://himalayas.app/jobs/{page}-{n}", "description": f"<p>{title} wanted.</p>",
            "locationRestrictions": ["Germany"]} for n, title in enumerate(page_titles)]})

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    return requests


@pytest.mark.parametrize("words", [["nurse"], ["nurse", *[f"word{n}" for n in range(24)]]])
def test_himalayas_is_read_as_its_newest_list_every_job_kept_however_many_words(monkeypatch, words):
    """A search per word cost a request per word, 20 jobs each; its list, newest first, costs the
    pages the operator set, and every job on them is kept: the words are matched on what is stored."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _himalayas(monkeypatch)
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": words}]}}, env={},
                               pages={"himalayas": 3})["himalayas"]
    links = http_apis.gather_himalayas(cfg, logging.getLogger("test"))

    assert len(requests) == 3
    assert len(links) == 60, "twenty a page, three pages, whoever has a word for them"
    assert {"Welder", "Sales Manager"} <= {link.title for link in links}


def test_himalayas_stops_at_the_first_page_older_than_the_window(monkeypatch):
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _himalayas(monkeypatch, old_from_page=1)
    cfg = build_source_configs({}, env={}, pages={"himalayas": 5})["himalayas"]
    links = http_apis.gather_himalayas(cfg, logging.getLogger("test"))

    assert len(requests) == 2 and len(links) == 20


def test_a_list_read_a_slice_a_run_is_read_whole_over_a_round_of_runs():
    """More company boards than a run may poll, or more countries than Adzuna's free calls allow, are read a
    slice a run, counted on the platform's own clock (`base.turn`): one round of runs reads each once. Held
    through The Muse's pages until its API went (2026-11-23)."""
    import math

    from libs.settings import PLATFORM_TICK_MIN
    from services.harvester.sources import base

    count, per_run = 99, 25
    runs = math.ceil(count / per_run)
    read = [index for run in range(runs)
            for index in base.turn(count, per_run, now=run * PLATFORM_TICK_MIN * 60.0)]

    assert sorted(read) == list(range(count)), f"{runs} runs read every one once"


def _nofluffjobs(monkeypatch, renewed_hours: dict[int, float], *, posted_days_ago: float = 0) -> list:
    """No Fluff Jobs' list, newest first by renewal: page N's postings renewed `renewed_hours[N]` ago.
    A posting renewed but first put up long before is `posted_days_ago` older."""
    from datetime import UTC, datetime, timedelta

    from services.harvester.sources import http_apis

    requests: list = []

    def at(hours: float) -> int:
        return int((datetime.now(UTC) - timedelta(hours=hours)).timestamp() * 1000)

    def answer(request):
        requests.append(request)
        page = int(request.url.params["page"])
        hours = renewed_hours.get(page, 500)
        return httpx.Response(200, json={"totalPages": 130, "postings": [
            {"url": f"job-{page}-{n}", "title": title, "name": "Acme", "renewed": at(hours),
             "posted": at(hours + (posted_days_ago * 24 if page > 1 else 0)), "fullyRemote": True}
            for n, title in enumerate(("Backend Developer", "Accountant", "Welder"))]})

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    return requests


def test_no_fluff_jobs_is_read_newest_first_until_a_page_has_nothing_new(monkeypatch):
    """Everything renewed in its last hour is on the first four of its 130 pages (measured 2026-11-04):
    a run stops at the first page with nothing inside the window, and keeps every posting that went up
    inside it, whoever has a word for it. A posting only renewed, first up a month ago, is not new."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _nofluffjobs(monkeypatch, {1: 0.2, 2: 1.0, 3: 30}, posted_days_ago=30)
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": ["python"]}]}}, env={},
                               freshness={"nofluffjobs": 2}, pages={"nofluffjobs": 10})["nofluffjobs"]
    links = http_apis.gather_nofluffjobs(cfg, logging.getLogger("test"))

    assert [int(request.url.params["page"]) for request in requests] == [1, 2, 3], "page 3 is all old"
    assert {link.title for link in links} == {"Backend Developer", "Accountant", "Welder"}
    assert {link.url for link in links} == {f"https://nofluffjobs.com/job/job-1-{n}" for n in range(3)}


def test_a_no_fluff_jobs_posting_is_where_its_first_town_is_or_at_least_in_its_country(monkeypatch):
    """Its first place is often a province alone, with no town, and its town comes after "Remote": read off
    the first place, 27 of 76 postings on its first page had no place, so passed every country filter
    (2026-11-24)."""
    from datetime import UTC, datetime

    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    province = {"country": {"code": "POL", "name": "Poland"}, "province": "lodz", "provinceOnly": True}
    places = {"job-1": [province, {"city": "Remote"}, {"country": {"name": "Poland"}, "city": "Warsaw"}],
              "job-2": [province, {"city": "Remote"}],
              "job-3": [{"city": "Kraków", "country": {"name": "Poland"}}]}
    now = int(datetime.now(UTC).timestamp() * 1000)

    def answer(request):
        return httpx.Response(200, json={"postings": [
            {"url": slug, "title": "Backend Developer", "name": "Acme", "renewed": now, "posted": now,
             "location": {"places": where}} for slug, where in places.items()]
            if int(request.url.params["page"]) == 1 else []})

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    cfg = build_source_configs({}, env={})["nofluffjobs"]
    links = http_apis.gather_nofluffjobs(cfg, logging.getLogger("test"))

    assert {link.url.rsplit("/", 1)[1]: link.location for link in links} == {
        "job-1": "Warsaw, Poland", "job-2": "Poland", "job-3": "Kraków, Poland"}


def test_no_fluff_jobs_walks_no_further_than_the_operator_set(monkeypatch):
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _nofluffjobs(monkeypatch, dict.fromkeys(range(1, 131), 0.1))
    cfg = build_source_configs({}, env={}, pages={"nofluffjobs": 4})["nofluffjobs"]
    http_apis.gather_nofluffjobs(cfg, logging.getLogger("test"))

    assert len(requests) == 4


def _boards(count: int):
    class Boards:
        def live_tokens(self, _ats):
            return [f"company{n:03d}" for n in range(count)]

        def etags(self, _ats):
            return {}

        def names(self, _ats):
            return {}

        def record_poll(self, *_args, **_kwargs):
            pass

        def record_asked(self, *_args, **_kwargs):
            pass
    return Boards()


def test_a_board_that_did_not_answer_is_asked_again_once_after_the_rest(caplog):
    """Of one run's boards that "did not answer", nearly all were the resolver giving up on a burst of new
    addresses (2026-11-23: Recruitee 26, Teamtailor 19, Lever 5, against one timeout and one error), and they
    answered a moment later. Asked again after the rest, their jobs come in this run, not the next; one that
    fails twice is named in one line with the reason, not a line a board."""
    from datetime import UTC, datetime

    from services.harvester.sources.ats import gather_ats

    asked: list[str] = []

    def fetch(token, _etag, within=None):
        asked.append(token)
        if (token == "company001" and asked.count(token) == 1) or token == "company002":
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return [JobLink(url=f"https://example.com/{token}", title="Nurse",
                        date_posted=datetime.now(UTC).isoformat())], None

    with caplog.at_level(logging.WARNING):
        links = gather_ats({}, logging.getLogger("test"), ats="greenhouse", repo=_boards(3),
                           fetch_company=fetch)

    assert sorted(link.url.rsplit("/", 1)[1] for link in links) == ["company000", "company001"]
    assert [asked.count(f"company{n:03d}") for n in range(3)] == [1, 2, 2]
    failures = [record.getMessage() for record in caplog.records if "did not answer" in record.getMessage()]
    assert len(failures) == 1 and "name resolution" in failures[0]


@needs_db
def test_every_company_board_is_polled_over_a_round_of_runs_not_only_the_first_few(db):
    """A run polls `max_companies` boards: it was always the first ones, and a board past them was never
    polled. Now the ones asked longest ago, each run, so a round of runs asks every board."""
    from libs.corpus.ats import AtsRepository
    from services.harvester.sources.ats import gather_ats

    repo = AtsRepository(db)
    repo.add_tokens([("greenhouse", f"company{n:03d}") for n in range(25)])
    polled: list[str] = []

    def fetch(token, _etag, within=None):
        polled.append(token)
        return [], None

    for _run in range(3):
        gather_ats({"max_companies": 10}, logging.getLogger("test"), ats="greenhouse", repo=repo,
                   fetch_company=fetch)

    assert set(polled) == {f"company{n:03d}" for n in range(25)} and len(polled) == 30

def test_never_more_boards_are_read_at_once_than_the_client_has_connections():
    """**A relationship, not a stopwatch.** Every ATS reads with workers of its own and all of them share
    one client. Read together, their workers outnumber its connections: a read then waits for a connection on
    its own clock, and is timed out by boards it never asked. So a worker takes a turn first, and there are
    as many turns as connections. Three families of thirty want to read here; sixty do, and thirty wait."""
    import threading

    from services.harvester.sources.ats import AT_ONCE, gather_ats

    reading, most, lock = 0, 0, threading.Lock()
    full, over, go = threading.Event(), threading.Event(), threading.Event()

    def fetch(_token, _etag, within=None):
        nonlocal reading, most
        with lock:
            reading += 1
            most = max(most, reading)
            (over if reading > AT_ONCE else full if reading == AT_ONCE else threading.Event()).set()
        go.wait(timeout=30)
        with lock:
            reading -= 1
        return [], None

    families = [threading.Thread(target=gather_ats, args=({"concurrency": 30}, logging.getLogger("test")),
                                 kwargs={"ats": name, "repo": _boards(30), "fetch_company": fetch})
                for name in ("greenhouse", "lever", "ashby")]
    for family in families:
        family.start()
    try:
        assert full.wait(timeout=30), "the premise: as many boards are being read as there are connections"
        # A moment in which the thirty that wait would have come in, were anything to let them.
        over.wait(timeout=0.3)
    finally:
        go.set()
        for family in families:
            family.join(timeout=30)

    assert most == AT_ONCE


def test_adzuna_is_asked_each_countrys_newest_with_no_word_two_countries_a_run(monkeypatch):
    """Its free tier allows ~250 calls a day: two a run, a country each, every country in turn."""
    from libs.settings import PLATFORM_TICK_MIN
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import base, http_apis

    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    asked: list[httpx.URL] = []

    def answer(request):
        asked.append(request.url)
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    cfg = build_source_configs(
        {uuid.uuid4(): {"sections": [{"keywords": ["nurse", "welder"],
                                      "locations": ["germany", "france", "spain"]}]}},
        env={"ADZUNA_APP_ID": "id"})["adzuna"]
    for run in range(2):
        monkeypatch.setattr(base.time, "time", lambda run=run: run * PLATFORM_TICK_MIN * 60.0)
        http_apis.gather_adzuna(cfg, logging.getLogger("test"))

    assert not [url for url in asked if "what" in url.params], "no word is asked"
    assert sorted(url.path.split("/")[4] for url in asked) == ["de", "es", "fr"], "two, then the third"
    assert {url.params["sort_by"] for url in asked} == {"date"}


# ── a board that did not answer, one with nothing open, one that is gone (2026-11-20) ─────────

def test_a_board_is_given_up_only_when_its_ats_says_it_is_not_there(db, monkeypatch):
    """One failed poll marked a company's board dead for good: a timeout, a 429, a 5xx or the resolver down
    read as an empty board, an empty board as a dead one, and nothing asks a dead board again. 542 of the
    dev copy's 565 boards had gone that way (2026-11-20). A board that did not answer says nothing about
    itself, and one with nothing open today has jobs another day: both are asked again."""
    from datetime import UTC, datetime

    from libs.corpus.ats import AtsRepository
    from services.harvester.sources import ats

    open_job = {"absolute_url": "https://boards.greenhouse.io/busy/jobs/1", "title": "Engineer",
                "first_published": datetime.now(UTC).isoformat()}
    answers = {"busy": (200, {"jobs": [open_job]}), "quiet": (200, {"jobs": []}),
               "refused": (429, {}), "broken": (502, {}), "gone": (404, {})}

    def board(request: httpx.Request) -> httpx.Response:
        token = request.url.path.split("/")[3]
        if token == "silent":
            raise httpx.ReadTimeout("no answer", request=request)
        status, body = answers[token]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(board)))
    repo = AtsRepository(db)
    repo.add_tokens(("greenhouse", token) for token in [*answers, "silent"])

    for _ in range(2):  # the second run asks every board the first left
        links = ats.gather_ats({"posted_within_hours": 72, "concurrency": 2}, logging.getLogger("test"),
                               ats="greenhouse", repo=repo, fetch_company=ats.fetch_greenhouse)

    assert sorted(repo.live_tokens("greenhouse")) == ["broken", "busy", "quiet", "refused", "silent"]
    assert [link.url for link in links] == [open_job["absolute_url"]]


@pytest.mark.parametrize(("board", "cfg"), [
    ("himalayas", {}), ("justjoinit", {}), ("nofluffjobs", {}), ("workingnomads", {}), ("jobicy", {}),
    ("findwork", {"api_key": "k"}),
    ("adzuna", {"app_id": "a", "app_key": "k", "countries": ["gb"]}),
])
def test_a_board_that_refuses_says_so_instead_of_having_no_jobs_today(board, cfg, monkeypatch, caplog):
    """A board that answers 403 or 429 with a body of its own (`{"error": ...}`) was read as a board with no
    jobs: the body has no `jobs`, so the list came back empty and not a line was logged, which is the silent
    kind of failure this project loses days to (CLAUDE.md §8). A refusal is logged by its status."""
    from services.harvester.sources import http_apis

    asked: list[str] = []

    def refuses(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        return httpx.Response(403, json={"error": "forbidden", "jobs": None})

    monkeypatch.setattr(http_apis, "make_shared_client",
                        lambda **_: httpx.Client(transport=httpx.MockTransport(refuses)))
    logger = logging.getLogger(f"test.boards.{board}")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        assert getattr(http_apis, f"gather_{board}")(cfg, logger) == []

    assert asked, "the premise: the board was asked"
    said = [record.getMessage() for record in caplog.records if record.name == logger.name]
    assert any("403" in line for line in said), said


@needs_db
def test_a_run_reads_the_platforms_settings_once_for_every_board(db):
    """**A relationship, not a stopwatch.** What each board is asked this cycle comes from five settings, and
    each board asked the database for each of them itself: about 115 questions a run for what one answers
    (2026-11-20). However many boards there are, the settings are read once, and what the operator stored
    still reaches the board it was stored for."""
    from collections import Counter

    from pymongo import MongoClient, monitoring

    from libs import settings
    from services.harvester.__main__ import source_configs_for

    settings.put(db, "source_freshness_hours", {"himalayas": 7})
    settings.put(db, "source_pages_per_cycle", {"himalayas": 3})
    asked: Counter = Counter()

    class Reads(monitoring.CommandListener):
        def started(self, event):
            if event.command_name in ("find", "aggregate", "count", "distinct"):
                asked[str(event.command.get(event.command_name))] += 1

        def succeeded(self, event): ...

        def failed(self, event): ...

    # A client of its own, made with the counter: a listener registered later never sees an existing one.
    counted = MongoClient(os.environ["MONGODB_URI"], uuidRepresentation="standard", tz_aware=True,
                          event_listeners=[Reads()])[db.name]
    try:
        configs = source_configs_for(counted)
    finally:
        counted.client.close()

    assert len(configs) > 5, "the premise: a run asks many boards"
    assert asked["platform_settings"] == 1
    assert (configs["himalayas"]["max_age_hours"], configs["himalayas"]["max_pages"]) == (7, 3)
    assert configs["jobicy"]["max_age_hours"] != 7, "and only that board"


def test_a_board_is_read_for_the_runs_window_and_an_older_jobs_text_is_never_cleaned(monkeypatch):
    """Each reader is given the run's window and leaves an older job before its text is cleaned: Recruitee's
    627 boards, every offer's text cleaned and most then dropped for their age, took 476 s for 172 jobs in the
    window, and 226 s with only those cleaned (2026-11-23)."""
    from datetime import UTC, datetime, timedelta

    from services.harvester.sources import ats

    def offer(n: int, days: int) -> dict:
        posted = f"{datetime.now(UTC) - timedelta(days=days):%Y-%m-%d %H:%M:%S} UTC"
        return {"careers_url": f"https://acme.recruitee.com/o/{n}", "title": f"Engineer {n}",
                "description": f"<p>Offer {n}</p>", "published_at": posted, "city": "Berlin"}

    offers = {"offers": [offer(1, 1), offer(2, 30), offer(3, 400)]}
    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=offers))))
    cleaned: list[str] = []
    monkeypatch.setattr(ats, "strip_html", lambda text, *_: cleaned.append(text) or text)

    jobs, _ = ats.fetch_recruitee("acme", None, within=72)

    assert [job.title for job in jobs] == ["Engineer 1"]
    assert cleaned == ["<p>Offer 1</p>"], "only the job a run keeps has its text cleaned"
    assert len(ats.fetch_recruitee("acme", None)[0]) == 3, "no window (the seed's probe): every job"


def test_workday_is_read_as_far_back_as_its_window_says_not_four_days_of_its_own():
    """Its reader stopped at four days of its own while the setting said seven (168 h), so a window set in
    Admin never reached it (2026-11-23). A job posted five days ago is read for a week's window, not three."""
    from services.harvester.sources import ats

    page = {"jobPostings": [{"externalPath": "/job/1", "title": "Engineer", "postedOn": "Posted 5 Days Ago",
                             "locationsText": "Berlin"}]}

    class _Client:
        def post(self, _url, json=None, **_kw):
            return httpx.Response(200, json=page)

    original = ats._http
    ats._http = _Client
    try:
        week, three_days = (ats.fetch_workday("tenant:wd3:site", within=hours)[0] for hours in (168, 72))
    finally:
        ats._http = original

    assert [job.title for job in week] == ["Engineer"]
    assert three_days == []


@needs_db
def test_a_run_asks_the_longest_unasked_boards_first_and_the_next_begins_where_it_stopped(db, monkeypatch):
    """With every board a crawl lists (14,294) a run cannot ask them all: Workable alone is 3,664 boards at
    two at a time (2026-11-23). So the company boards have their share of a run, the boards asked longest ago
    go first, and when the share is spent the rest are the first the next run asks: every board in turn, and
    no run past its budget."""
    from libs.corpus.ats import AtsRepository
    from services.harvester.sources import ats as ats_module
    from services.harvester.sources.ats import gather_ats

    repo = AtsRepository(db)
    repo.add_tokens([("greenhouse", f"board{n}") for n in range(5)])
    clock = [0.0]
    monkeypatch.setattr(ats_module, "monotonic", lambda: clock[0])
    asked: list[str] = []

    def fetch(token, _etag, within=None):
        asked.append(token)
        clock[0] += 1.0          # each board takes a second of the run
        return [], None

    def run(until: float) -> list[str]:
        asked.clear()
        gather_ats({"until": until, "max_age_hours": 72}, logging.getLogger("test"), ats="greenhouse",
                   repo=repo, fetch_company=fetch)
        return sorted(asked)

    clock[0] = 0.0
    first = run(until=3.0)
    second = run(until=clock[0] + 3.0)

    unasked = {f"board{n}" for n in range(5)} - set(first)
    assert len(first) == 3, "the share spent, no board more is started"
    assert len(unasked) == 2 and unasked <= set(second), "the boards one run did not reach, first in the next"


def test_an_answer_past_the_ceiling_is_not_read_and_a_board_under_it_is(monkeypatch):
    """One Recruitee board answered 89.5 MB on each first read, its parse alone most of the harvester's 1 GB,
    while the largest any board needed was 17.9 MB, Ashby's (2026-11-23). An answer past the ceiling is left
    unread, a board that did not answer, and every other board is read."""
    from datetime import UTC, datetime

    from services.harvester.sources import ats

    monkeypatch.setattr(ats, "_LARGEST_ANSWER", 2_000)

    def board(request: httpx.Request) -> httpx.Response:
        token = request.url.path.split("/")[3]
        jobs = [{"absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{n}", "title": "Engineer",
                 "first_published": datetime.now(UTC).isoformat()}
                for n in range(1 if token == "small" else 50)]
        return httpx.Response(200, json={"jobs": jobs}, headers={"etag": '"v1"'})

    monkeypatch.setattr(ats, "_CLIENT", httpx.Client(transport=httpx.MockTransport(board)))

    jobs, etag = ats.fetch_greenhouse("small")
    assert [job.url for job in jobs] == ["https://boards.greenhouse.io/small/jobs/0"] and etag == '"v1"'
    with pytest.raises(RuntimeError, match="over"):
        ats.fetch_greenhouse("huge")
