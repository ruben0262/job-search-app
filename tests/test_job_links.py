"""Add a job by its link (owner, 2026-11-03): found if it is here, else read from its page and kept for
everyone it suits, else written in by the person. Reading is code: JSON-LD, LinkedIn's job card, No
Fluff Jobs' API; a guess is shown to the person, never kept as it is."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import httpx
import pytest

from libs.corpus.postings import job_link, read_link
from libs.db import insert, now
from libs.db.models import CorpusJob, User, UserConfig, UserJobState
from tests.conftest import needs_db

TEXT = ("We are looking for a backend engineer to build our payment systems in Python and Postgres. "
        "You will own the ledger service end to end and pair with the data team. ") * 3

LINKEDIN = f"""
<h2 class="top-card-layout__title topcard__title">Backend Engineer</h2>
<a class="topcard__org-name-link" href="/company/acme">Acme</a>
<span class="topcard__flavor topcard__flavor--bullet">Berlin, Germany</span>
<span class="posted-time-ago__text">3 days ago</span>
<div class="show-more-less-html__markup"><p>{TEXT}</p><ul><li>Python</li><li>Postgres</li></ul></div>
"""

JSONLD = """<html><head><script type="application/ld+json">{}</script></head>
<body><nav>Jobs Companies Log in</nav></body></html>""".format(json.dumps({
    "@type": "JobPosting", "title": "Platform Engineer", "hiringOrganization": {"name": "Globex"},
    "jobLocation": {"address": {"addressLocality": "Lisbon"}}, "description": f"<p>{TEXT}</p>"}))

NO_JSONLD = f"""<html><head><title>ignored</title>
<meta property="og:title" content="Data Engineer | Initech Careers">
<meta property="og:site_name" content="Initech"></head>
<body><nav>Home About Careers Contact Log in</nav><main><h1>Data Engineer</h1><p>{TEXT}</p></main>
<footer>Cookie settings</footer></body></html>"""


def client(pages: dict[str, httpx.Response]) -> httpx.Client:
    """A web where each address answers what `pages` says, and anything else 404."""
    def answer(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))
    return httpx.Client(transport=httpx.MockTransport(answer))


def html(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/html"})


GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"


# ── the address ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pasted", [
    "https://www.linkedin.com/jobs/view/4465730645",
    "https://www.linkedin.com/jobs/view/backend-engineer-at-acme-4465730645/?trk=public_jobs",
    "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4465730645",
    "https://pl.linkedin.com/jobs/search/?keywords=python&currentJobId=4465730645",
    "  linkedin.com/jobs/view/4465730645  ",
])
def test_a_linkedin_job_is_one_address_however_it_was_copied(pasted):
    """The harvest stores `/jobs/view/<number>`; a link from a search or the feed names the same job."""
    assert job_link(pasted) == "https://www.linkedin.com/jobs/view/4465730645"


def test_an_address_without_its_scheme_is_read_as_https_and_anything_else_is_refused():
    assert job_link("jobs.lever.co/acme/1234") == "https://jobs.lever.co/acme/1234"
    assert job_link("https://boards.greenhouse.io/acme/jobs/1") == "https://boards.greenhouse.io/acme/jobs/1"
    for not_a_link in ("", "a job I saw", "javascript:alert(1)", "ftp://example.com/job", "localhost"):
        assert job_link(not_a_link) is None, not_a_link


# ── the page ──────────────────────────────────────────────────────────────────────────────────

def test_a_linkedin_job_is_read_whole_off_its_job_card():
    """The guest page has no JSON-LD: before this, a pasted LinkedIn link had its text and no title."""
    found = read_link("https://www.linkedin.com/jobs/view/42", client({GUEST + "42": html(LINKEDIN)}))

    assert (found["title"], found["company"]) == ("Backend Engineer", "Acme")
    assert found["location"] == "Berlin, Germany"
    assert "payment systems" in found["description"] and "• Python" in found["description"]
    assert found["posted_at"] is not None
    assert found["complete"] and not found["closed"]


def test_a_refused_linkedin_page_is_asked_again_from_the_next_address():
    tries = []

    def refuse_twice(request: httpx.Request) -> httpx.Response:
        tries.append(request)
        return html(LINKEDIN) if len(tries) == 3 else httpx.Response(429)

    found = read_link("https://www.linkedin.com/jobs/view/42",
                      httpx.Client(transport=httpx.MockTransport(refuse_twice)))
    assert found["complete"] and len(tries) == 3


def test_a_page_with_json_ld_is_read_whole():
    url = "https://jobs.example.com/platform-engineer"
    found = read_link(url, client({url: html(JSONLD)}))

    assert (found["title"], found["company"], found["location"]) == ("Platform Engineer", "Globex", "Lisbon")
    assert found["complete"] and "Jobs Companies" not in found["description"]


def test_a_page_without_one_is_a_guess_for_the_person_to_check():
    """Its title less the site's name, the site's own name, and its main part's text, not its menus."""
    url = "https://careers.initech.com/data-engineer"
    found = read_link(url, client({url: html(NO_JSONLD)}))

    assert (found["title"], found["company"]) == ("Data Engineer", "Initech")
    assert "payment systems" in found["description"]
    assert "Log in" not in found["description"] and "Cookie" not in found["description"]
    assert not found["complete"], "a guess is never kept as it is"


def test_a_no_fluff_jobs_link_is_read_from_its_api_not_its_script_page():
    api = "https://nofluffjobs.com/api/posting/backend-engineer-acme-warsaw"
    posting = {"title": "Backend Engineer", "company": {"name": "Acme"}, "status": "PUBLISHED",
               "location": {"places": [{"city": "Warsaw", "country": {"name": "Poland"}}]},
               "details": {"description": f"<p>{TEXT}</p>"}}
    found = read_link("https://nofluffjobs.com/pl/job/backend-engineer-acme-warsaw",
                      client({api: httpx.Response(200, json=posting)}))

    assert (found["title"], found["company"]) == ("Backend Engineer", "Acme")
    assert found["location"] == "Warsaw, Poland"
    assert found["complete"] and "payment systems" in found["description"]


def test_a_page_that_refuses_us_leaves_everything_to_the_person_and_a_gone_one_is_closed():
    url = "https://himalayas.app/jobs/1"
    refused = read_link(url, client({url: html("", 403)}))
    assert not refused["complete"] and not refused["closed"] and refused["title"] == ""

    gone = read_link("https://jobs.example.com/gone", client({}))
    assert gone["closed"]

    url = "https://www.linkedin.com/jobs/view/7"
    ended = read_link(url, client({GUEST + "7": html(LINKEDIN + "No longer accepting applications")}))
    assert ended["closed"]


def test_a_link_into_a_private_network_is_never_requested():
    """Or anybody could have this server read its own network back to them as a job's text."""
    from services.api.link_routes import NotPublic, _client

    for url in ("http://127.0.0.1:27017/", "http://localhost:8000/health", "http://10.0.0.1/",
                "http://169.254.169.254/latest/meta-data/", "http://[::1]/"):
        with _client(url) as http, pytest.raises(NotPublic):
            http.get(url)


# ── the routes ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api import link_routes
    from services.api.app import app

    pages: dict[str, httpx.Response] = {}

    @contextmanager
    def fake_web(_url):
        yield client(pages)

    fake_web.real = link_routes._client  # type: ignore[attr-defined]
    monkeypatch.setattr(link_routes, "_client", fake_web)
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active",
                           active=True, onboarding_completed_at=now()))
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user, pages


def _added(http) -> list[int]:
    return [job["id"] for job in http.get("/jobs/added").json()]


@needs_db
def test_a_job_already_here_is_found_by_its_link_without_reading_its_page(api):
    http, db, _user, pages = api
    known = insert(db, CorpusJob(url="https://www.linkedin.com/jobs/view/4465730645", source="linkedin",
                                 title="Backend Engineer", company="Acme", description=TEXT))

    answer = http.post("/jobs/link", json={
        "url": "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4465730645"}).json()

    assert answer["outcome"] == "known" and answer["job"]["id"] == known.id
    assert answer["job"]["summary"], "the card the jobs page shows, with its text"
    assert _added(http) == [known.id], "listed for them, though it may not have their words"
    assert not pages, "nothing was read"


@needs_db
def test_a_new_job_is_read_kept_for_everyone_and_shown_to_whoever_has_its_words(api):
    """The point of keeping it in the corpus: a job one person found reaches the next one it suits."""
    from libs.auth.access import issue_access_token

    http, db, _user, pages = api
    url = "https://jobs.example.com/platform-engineer"
    pages[url] = html(JSONLD.replace("Platform Engineer", "Backend Engineer"))

    answer = http.post("/jobs/link", json={"url": url}).json()

    assert answer["outcome"] == "added"
    job = CorpusJob.from_doc(db[CorpusJob.COLLECTION].find_one({"_id": answer["job"]["id"]}))
    assert (job.source, job.company, job.discovered_via) == ("link", "Globex", "link")
    assert answer["job"]["apply_on"] == "the job's site", "its board is wherever its link goes"
    assert job.described_at is not None, "a whole job: alerts and CVs read it"
    assert _added(http) == [job.id]

    other = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                            subscription_status="active", status="active",
                            active=True, onboarding_completed_at=now()))
    insert(db, UserConfig(user_id=other.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": ["backend engineer"]}]}))
    http.cookies.set("access_token", issue_access_token(other.id, uuid.uuid4()), path="/")
    assert job.id in [row["id"] for row in http.get("/feed").json()["jobs"]]


@needs_db
def test_the_same_job_under_another_address_is_the_one_already_here(api):
    """A company's own board, pasted, for a job we have from LinkedIn: one job, not two."""
    http, db, _user, pages = api
    known = insert(db, CorpusJob(url="https://www.linkedin.com/jobs/view/1", source="linkedin",
                                 title="Platform Engineer", company="Globex", location="Lisbon",
                                 content_key="globex|platform engineer|lisbon", description=TEXT))
    url = "https://jobs.example.com/platform-engineer"
    pages[url] = html(JSONLD)

    answer = http.post("/jobs/link", json={"url": url}).json()

    assert (answer["outcome"], answer["job"]["id"]) == ("known", known.id)
    assert db[CorpusJob.COLLECTION].count_documents({}) == 1


@needs_db
def test_a_page_that_says_part_of_it_is_finished_by_the_person_and_then_kept(api):
    http, db, _user, pages = api
    url = "https://careers.initech.com/data-engineer"
    pages[url] = html(NO_JSONLD)

    answer = http.post("/jobs/link", json={"url": url}).json()

    assert answer["outcome"] == "draft" and answer["job"] is None
    assert answer["draft"]["title"] == "Data Engineer" and answer["draft"]["company"] == "Initech"
    assert db[CorpusJob.COLLECTION].count_documents({}) == 0, "a guess is not kept"

    short = http.post("/jobs/link/save", json={**answer["draft"], "description": "Python."})
    assert short.status_code == 422 and "whole description" in short.json()["detail"]

    saved = http.post("/jobs/link/save", json={**answer["draft"], "location": "Remote"}).json()
    assert saved["outcome"] == "added" and saved["job"]["location"] == "Remote"
    assert _added(http) == [saved["job"]["id"]]
    assert http.post("/jobs/link", json={"url": url}).json()["job"]["id"] == saved["job"]["id"], \
        "their own, when they bring the link again"


@needs_db
def test_a_job_written_in_is_theirs_alone_and_stands_in_for_no_real_posting(api):
    """Nobody checked a typed job (owner, 2026-11-03): it reaches nobody else, whatever their words,
    and the harvest storing the real posting at that address is not stopped by it."""
    from libs.auth.access import issue_access_token
    from libs.corpus.repository import CorpusRepository

    http, db, _user, pages = api
    url = "https://careers.initech.com/backend-engineer"
    typed = {"url": url, "title": "Backend Engineer", "company": "Initech", "location": "Berlin",
             "description": TEXT}
    saved = http.post("/jobs/link/save", json=typed).json()["job"]
    assert saved["apply_on"] == "the job's site"
    mine = saved["id"]

    other = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                            subscription_status="active", status="active",
                            active=True, onboarding_completed_at=now()))
    insert(db, UserConfig(user_id=other.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": ["backend engineer"]}]}))
    http.cookies.set("access_token", issue_access_token(other.id, uuid.uuid4()), path="/")
    assert mine not in [row["id"] for row in http.get("/feed").json()["jobs"]], "not in their jobs"
    pages[url] = html(NO_JSONLD)
    assert http.post("/jobs/link", json={"url": url}).json()["outcome"] == "draft", "nor found by its link"

    corpus = CorpusRepository(db)
    assert corpus.find_duplicate(url, "Backend Engineer", "Initech", "Berlin") is None
    assert corpus.add(url=url, source="greenhouse", title="Backend Engineer", company="Initech",
                      location="Berlin") is not None, "the real posting is stored when the harvest finds it"


@needs_db
def test_a_job_written_in_for_a_page_that_would_not_answer(api):
    http, _db, _user, pages = api
    answer = http.post("/jobs/link", json={"url": "https://himalayas.app/jobs/1"}).json()
    assert answer["outcome"] == "closed", "our fake web answers 404 for it: gone"

    pages["https://himalayas.app/jobs/2"] = html("", 403)
    answer = http.post("/jobs/link", json={"url": "https://himalayas.app/jobs/2"}).json()
    assert answer["outcome"] == "draft" and answer["draft"]["url"] == "https://himalayas.app/jobs/2"

    typed = {"url": answer["draft"]["url"], "title": "Azure Architect", "company": "Bright Vision",
             "location": "", "description": TEXT}
    assert http.post("/jobs/link/save", json=typed).json()["outcome"] == "added"
    assert http.post("/jobs/link/save", json=typed).json()["outcome"] == "known", "saved twice, kept once"


@needs_db
def test_a_link_that_is_not_one_or_leads_inside_is_refused_in_words(api, monkeypatch):
    from services.api import link_routes

    http, _db, _user, _pages = api
    assert "not a link" in http.post("/jobs/link", json={"url": "a job I saw"}).json()["detail"]

    monkeypatch.setattr(link_routes, "_client", link_routes._client.real)
    inside = http.post("/jobs/link", json={"url": "http://127.0.0.1:27017/"})
    assert inside.status_code == 422 and "public" in inside.json()["detail"]


@needs_db
def test_a_job_skipped_comes_back_when_its_link_is_brought_and_leaves_the_list_when_applied(api):
    http, db, user, _pages = api
    job = insert(db, CorpusJob(url="https://jobs.example.com/1", source="test", title="Engineer",
                               description=TEXT))
    http.put(f"/jobs/{job.id}/hidden")

    http.post("/jobs/link", json={"url": job.url})
    state = db[UserJobState.COLLECTION].find_one({"user_id": user.id, "job_id": job.id})
    assert state["dismissed_at"] is None and state["added_at"] is not None
    assert _added(http) == [job.id]

    http.put(f"/jobs/{job.id}/applied")
    assert _added(http) == [], "on Applied now"
