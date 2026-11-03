"""The source registry, and the structural rules the adapter port had to preserve."""
from __future__ import annotations

import ast
import logging
import re
import uuid
from pathlib import Path

import httpx
import pytest

from services.harvester.sources import (
    OUTER_CONCURRENCY,
    PER_USER_SOURCES,
    REGISTRY,
    SHARED_SOURCES,
    Source,
    Tier,
    jobspy_source,
    sources_for,
)
from services.harvester.sources.base import JobLink

HARVESTER = Path(__file__).resolve().parents[1] / "services" / "harvester"
ADAPTERS = sorted((HARVESTER / "sources").glob("*.py"))


# ── the one-factory rule ──────────────────────────────────────────────────────────────────────

def test_no_adapter_builds_its_own_http_client():
    """The rule the whole fetch layer rests on."""
    offenders = [
        f"{path.relative_to(HARVESTER)}:{i}"
        for path in HARVESTER.rglob("*.py")
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

def test_linkedin_is_the_only_per_user_source():
    """It is the only source that needs a residential proxy."""
    assert PER_USER_SOURCES == ("jobspy_linkedin",)


def test_the_shared_tier_contains_no_proxied_source():
    """A proxied source mis-tagged shared would spend one user's metered bandwidth for everyone."""
    assert "jobspy_linkedin" not in SHARED_SOURCES
    assert set(SHARED_SOURCES) | set(PER_USER_SOURCES) == set(REGISTRY)


def test_sources_for_cannot_hand_the_shared_loop_a_proxied_source():
    shared = sources_for(Tier.SHARED)
    assert shared and all(s.tier is Tier.SHARED for s in shared)
    assert all(s.key != "jobspy_linkedin" for s in shared)


def test_only_the_ats_family_needs_the_token_and_etag_repository():
    """Everything else is stateless."""
    needing = {s.key for s in REGISTRY.values() if s.needs_ats_repo}
    assert needing == {"greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
                       "breezy", "workday", "personio", "workable"}


def test_every_registry_entry_is_callable_and_well_formed():
    for key, source in REGISTRY.items():
        assert isinstance(source, Source)
        assert source.key == key, "the registry key and the Source key must agree"
        assert callable(source.adapter)
        assert isinstance(source.tier, Tier)


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


def test_linkedin_is_only_gathered_through_the_proxy_it_is_handed():
    """The reference read `cfg["proxy"]`. The site's proxy is handed in by the pass, keyword-only and
    required, so nothing reaches LinkedIn from the box's own address or from a config."""
    import inspect

    sig = inspect.signature(jobspy_source.gather_linkedin)
    proxy = sig.parameters.get("proxy")
    assert proxy is not None, "gather_linkedin must be handed the proxy"
    assert proxy.kind is inspect.Parameter.KEYWORD_ONLY, \
        "keyword-only, so it cannot be positionally confused with cfg"
    assert proxy.default is inspect.Parameter.empty, "mandatory, so it cannot be omitted"

    with pytest.raises(TypeError):
        jobspy_source.gather_linkedin({}, logging.getLogger("test"))


def test_the_shared_jobspy_sources_take_no_lease():
    """Indeed and Google need no proxy. Accepting one would let them spend a user's bandwidth."""
    import inspect

    for adapter in (jobspy_source.gather_indeed, jobspy_source.gather_google):
        assert "lease" not in inspect.signature(adapter).parameters, adapter.__name__


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

    lock, now, most = threading.Lock(), [0], [0]

    def fetch(_token, _etag):
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


def test_each_linkedin_search_request_leaves_on_a_new_connection_so_a_new_address():
    """jobspy's search kept one connection for all of a query's pages: one proxy address, refused
    first. Three requests are three connections now, and the scraper builds the session that does it."""
    import socketserver
    import threading

    linkedin = pytest.importorskip("jobspy.linkedin")
    from services.harvester.sources.jobspy_source import new_connection_each_request

    connections: list[int] = []

    class Proxy(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            connections.append(1)
            reader = self.request.makefile("rb")
            while reader.readline():
                while reader.readline() not in (b"\r\n", b""):
                    pass
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    class Server(socketserver.ThreadingTCPServer):
        # A connection held open (the old way) must fail the count, not hang the shutdown.
        daemon_threads, block_on_close = True, False

    new_connection_each_request()
    assert "RequestsRotating" in linkedin.LinkedIn.scrape.__code__.co_names, "the scraper's own session"
    with Server(("127.0.0.1", 0), Proxy) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        session = linkedin.RequestsRotating([f"http://127.0.0.1:{server.server_address[1]}"])
        session.verify = True
        try:
            for _ in range(3):
                assert session.get("http://www.linkedin.com/jobs-guest/x").text == "ok"
        finally:
            session.close()
            server.shutdown()

    assert len(connections) == 3


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
    cfg = {"max_pages": 5, "per_page": 2, "max_age_hours": 24, "keywords": ["python"]}

    links = http_apis.gather_justjoinit(cfg, logging.getLogger("test"))

    assert [link.url.rsplit("-", 1)[1] for link in links] == ["1", "2", "3"], "within 24 hours, newest first"
    assert len(asked) == 2, "the second page reached past the window, so no third was asked for"
    assert links[0].is_remote is True and links[0].company == "Acme"

    pages.pop(2)
    kept = http_apis.gather_justjoinit(cfg, logging.getLogger("t"))
    assert [link.url.rsplit("-", 1)[1] for link in kept] == ["1", "2"], "a failed page keeps the ones before"


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

    def fetch(token, _etag):
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


# ── feeds keep every job; the words are matched here (2026-11-03) ─────────────────────────────

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
    if name == "themuse":
        return httpx.Response(200, json={"page_count": 308, "results": [{
            "refs": {"landing_page": url}, "name": "Registered Nurse", "company": {"name": "Mercy"},
            "publication_date": posted.isoformat(), "locations": [{"name": "Flexible / Remote"}],
            "contents": "<p>Ward nursing, nights.</p>"}]})
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


@pytest.mark.parametrize("name", ["jobicy", "themuse", "workingnomads", "profession"])
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


def test_the_muse_reads_the_pages_the_operator_set_in_every_job_category(monkeypatch):
    """It read a key no setting wrote, and asked for software and data jobs only."""
    from services.harvester.source_config import build_source_configs

    requests: list = []
    _feed(monkeypatch, "themuse", requests)(
        build_source_configs({}, env={}, pages={"themuse": 3})["themuse"], logging.getLogger("test"))
    assert len(requests) == 3
    assert not any("category" in request.url.params for request in requests)


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
def test_himalayas_is_read_as_its_newest_list_the_same_however_many_words(monkeypatch, words):
    """A search per word cost a request per word, 20 jobs each; its list, newest first, costs the
    pages the operator set, and keeps the jobs somebody's words name."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _himalayas(monkeypatch)
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": words}]}}, env={},
                               pages={"himalayas": 3})["himalayas"]
    links = http_apis.gather_himalayas(cfg, logging.getLogger("test"))

    assert len(requests) == 3
    assert {link.title for link in links} == {"Registered Nurse"}
    assert len(links) == 15, "five nurses a page, three pages"


def test_himalayas_stops_at_the_first_page_older_than_the_window(monkeypatch):
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    requests = _himalayas(monkeypatch, old_from_page=1)
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": ["welder"]}]}}, env={},
                               pages={"himalayas": 5})["himalayas"]
    links = http_apis.gather_himalayas(cfg, logging.getLogger("test"))

    assert len(requests) == 2 and len(links) == 5


def test_a_filtered_feed_keeps_a_job_only_for_a_whole_word_as_the_site_shows_it(monkeypatch):
    """A part of a word kept 53 of Himalayas' newest 100 for five words: "rest" inside "interest", "api"
    inside "rapid" (2026-11-03). Stored and never shown to anybody."""
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    _himalayas(monkeypatch, titles=("Interest Rate Analyst", "REST API Developer", "Rapid Response Nurse",
                                    "API Engineer"))
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": ["rest", "api"]}]}}, env={},
                               pages={"himalayas": 1})["himalayas"]
    kept = {link.title for link in http_apis.gather_himalayas(cfg, logging.getLogger("test"))}

    assert kept == {"REST API Developer", "API Engineer"}


def test_a_list_in_no_date_order_is_read_whole_over_a_round_of_runs(monkeypatch):
    """The Muse's 308 pages hold its ~38 new jobs a day anywhere among them. Each run reads its first page
    and the next slice of the rest, counted on the platform's own clock: one round of runs reads them all."""
    import math

    from libs.settings import PLATFORM_TICK_MIN
    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    cfg = build_source_configs({}, env={}, pages={"themuse": 26})["themuse"]
    requests: list = []
    gather = _feed(monkeypatch, "themuse", requests)
    runs = math.ceil(307 / 25)
    for run in range(runs):
        monkeypatch.setattr(http_apis.time, "time", lambda run=run: run * PLATFORM_TICK_MIN * 60.0)
        gather(cfg, logging.getLogger("test"))

    pages = [int(request.url.params["page"]) for request in requests]
    assert len(pages) == runs * 26 - (runs * 25 - 307), "26 a run, the last slice shorter"
    assert set(pages) == set(range(308)), f"{runs} runs read every page"


@pytest.mark.parametrize("words", [["python"], ["python", *[f"word{n}" for n in range(24)]]])
def test_no_fluff_jobs_is_read_as_its_list_and_matched_on_the_skills_each_job_requires(monkeypatch, words):
    """A search per word matched a job's required skills, the first ~246 matches each. Every entry of
    its list names them, so the list is read a slice a run and the words matched on them here."""
    from datetime import UTC, datetime, timedelta

    from services.harvester.source_config import build_source_configs
    from services.harvester.sources import http_apis

    posted = int((datetime.now(UTC) - timedelta(hours=1)).timestamp() * 1000)
    requests: list = []

    def answer(request):
        requests.append(request)
        page = request.url.params["page"]
        return httpx.Response(200, json={"totalPages": 131, "postings": [
            {"url": f"backend-{page}", "title": "Backend Developer", "name": "Acme", "posted": posted,
             "technology": "Java", "tiles": {"values": [{"value": "Python", "type": "requirement"}]}},
            {"url": f"frontend-{page}", "title": "Frontend Developer", "name": "Acme", "posted": posted,
             "technology": "JavaScript", "tiles": {"values": [{"value": "React", "type": "requirement"}]}}]})

    monkeypatch.setattr(http_apis, "make_shared_client", lambda **_: httpx.Client(
        transport=httpx.MockTransport(answer)))
    cfg = build_source_configs({uuid.uuid4(): {"sections": [{"keywords": words}]}}, env={},
                               pages={"nofluffjobs": 4})["nofluffjobs"]
    links = http_apis.gather_nofluffjobs(cfg, logging.getLogger("test"))

    assert len(requests) == 4, "the pages set, however many words"
    assert len({request.url.params["page"] for request in requests}) == 4, "the page is in the address"
    assert {link.title for link in links} == {"Backend Developer"}, "Python is required, not in the title"
