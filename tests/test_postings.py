"""The posting fetcher: what it reads off a page, and what it refuses to guess."""
from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from libs.corpus import postings

# ── fixtures shaped like the real pages ───────────────────────────────────────────────────────

GUEST_FRAGMENT = """
<div class="top-card-layout">
  <h2 class="topcard__title">Senior Python Engineer</h2>
  <span class="posted-time-ago__text"> 2 days ago </span>
  <span class="num-applicants__caption"> Over 200 applicants </span>
  <a class="topcard__button" href="/jobs/apply/1234">Apply on company website</a>
</div>
<div class="show-more-less-html__markup">
  We are looking for a Python engineer to build data pipelines. You will work with Postgres,
  Airflow and dbt, own the ingestion layer end to end, and pair with the analytics team on the
  models they depend on. Five years of experience preferred.
</div>
"""

# Serialised rather than written out: a job description is too long to hand-write inline.
JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{}
</script>
</head><body><nav>Jobs Companies Salaries</nav></body></html>
""".format(json.dumps({
    "@type": "JobPosting",
    "title": "Data Engineer",
    "hiringOrganization": {"name": "Acme Ltd"},
    "jobLocationType": "TELECOMMUTE",
    "jobLocation": {"address": {"addressLocality": "Berlin"}},
    "description": "<p>Build and own the warehouse. Postgres, dbt, Airflow. You are the first "
                   "data hire and will set the standards everyone after you works to.</p>",
}))


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, url: str = "https://example.com"):
        self.text = text
        self.status_code = status_code
        self.url = url


class FakeClient:
    """Answers whatever the test queued, and records what was asked for."""

    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.requested: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.requested.append(url)
        return self.responses.pop(0) if self.responses else FakeResponse("", 404)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """The retry sleep is real seconds and there are six of them per failed fetch."""
    monkeypatch.setattr(postings.time, "sleep", lambda _seconds: None)


# ── what comes off a LinkedIn page ────────────────────────────────────────────────────────────

def test_the_applicant_count_is_the_number_in_the_caption():
    assert postings.parse_applicants(GUEST_FRAGMENT) == 200
    assert postings.applicants_caption(GUEST_FRAGMENT) == "Over 200 applicants"


def test_a_page_with_no_applicant_caption_reports_none_rather_than_zero():
    """Zero applicants and an unknown count are opposite signals."""
    assert postings.parse_applicants("<div>no caption here</div>") is None


def test_the_relative_posted_time_becomes_an_absolute_timestamp():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC)
    assert postings.parse_posted_at(GUEST_FRAGMENT, now=now) == now - dt.timedelta(days=2)


@pytest.mark.parametrize(("caption", "delta"), [
    ("30 minutes ago", dt.timedelta(minutes=30)),
    ("4 hours ago", dt.timedelta(hours=4)),
    ("3 weeks ago", dt.timedelta(weeks=3)),
    ("2 months ago", dt.timedelta(days=60)),
])
def test_every_unit_linkedin_uses_is_understood(caption, delta):
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC)
    html = f'<span class="posted-time-ago__text"> {caption} </span>'
    assert postings.parse_posted_at(html, now=now) == now - delta


def test_a_repost_is_distinguished_from_a_posting():
    """LinkedIn resurfaces stale listings with a fresh timestamp."""
    reposted = '<span class="posted-time-ago__text"> Reposted 1 day ago </span>'
    assert postings.parse_reposted(reposted) is True
    assert postings.parse_reposted(GUEST_FRAGMENT) is None
    assert postings.parse_reposted("<div>nothing</div>") is None


def test_a_closed_posting_is_recognised():
    assert postings.parse_accepting("<p>No longer accepting applications</p>") is False
    assert postings.parse_accepting(GUEST_FRAGMENT) is True


# ── workplace, the parser that can delete a good job ──────────────────────────────────────────

def test_the_schema_org_telecommute_tag_wins():
    """The W3C standard field, emitted by most ATS boards — the most reliable signal on a page."""
    assert postings.parse_workplace(JSONLD_PAGE) == "remote"


@pytest.mark.parametrize(("markup", "expected"), [
    ("<h1>Engineer (Remote)</h1>", "remote"),
    ("<h1>Engineer (Hybrid)</h1>", "hybrid"),
    ("<h1>Engineer (On-site)</h1>", "onsite"),
    ("<span>London, UK · Hybrid</span>", "hybrid"),
    ("<span>Berlin · On-site</span>", "onsite"),
])
def test_the_workplace_pill_is_read_in_its_bracketed_and_middot_forms(markup, expected):
    assert postings.parse_workplace(markup) == expected


def test_prose_about_being_onsite_never_classifies_a_job_as_onsite():
    """The test this parser exists for."""
    body = ("<div>This is a fully distributed team. Expect occasional onsite workshops and "
            "on-site client visits twice a year.</div>")
    assert postings.parse_workplace(body) is None


def test_a_pill_far_down_the_page_is_ignored():
    """Only the first 6KB is scanned, because that is where the top-card lives."""
    assert postings.parse_workplace("x" * 7000 + "<span>· Remote</span>") is None


def test_no_signal_means_none_so_the_filter_fails_open():
    assert postings.parse_workplace("<div>Senior Engineer at Acme</div>") is None
    assert postings.parse_workplace("") is None


# ── the description ───────────────────────────────────────────────────────────────────────────

def test_the_json_ld_description_is_preferred_over_the_whole_page():
    """The page strip would bring the nav and the cookie banner with it."""
    body = postings.jsonld_description(JSONLD_PAGE)
    assert body is not None
    assert "warehouse" in body
    assert "Salaries" not in body


def test_the_json_ld_posting_carries_title_company_and_location():
    posting = postings.jsonld_posting(JSONLD_PAGE)
    assert posting == {"title": "Data Engineer", "company": "Acme Ltd", "location": "Berlin",
                       "description": posting["description"]}


def test_a_page_without_json_ld_yields_none_rather_than_an_empty_posting():
    assert postings.jsonld_posting("<html><body>nothing structured</body></html>") is None


def test_a_generous_snippet_is_trusted_without_spending_a_fetch():
    """Tier-2 APIs return the whole posting. Re-fetching it would be a request for nothing."""
    client = FakeClient()
    result = postings.fetch_job("https://example.com/job/1", client=client,
                                snippet="x" * (postings.MIN_SNIPPET + 1))
    assert result.description == "x" * (postings.MIN_SNIPPET + 1)
    assert client.requested == []


def test_a_thin_snippet_triggers_a_fetch_of_the_page():
    client = FakeClient(FakeResponse(JSONLD_PAGE))
    result = postings.fetch_job("https://example.com/job/1", client=client, snippet="short")
    assert "warehouse" in result.description
    assert result["workplace"] == "remote"
    assert client.requested == ["https://example.com/job/1"]


def test_a_linkedin_url_goes_to_the_guest_endpoint():
    client = FakeClient(FakeResponse(GUEST_FRAGMENT))
    result = postings.fetch_job("https://www.linkedin.com/jobs/view/4321", client=client)

    assert "data pipelines" in result.description
    assert "topcard__title" not in result.description      # the JD element, not the page
    assert result["applicants"] == 200
    # Unknown, not denied: the marker is on no page we can fetch.
    assert result["reposted"] is None
    assert client.requested[0].endswith("/jobPosting/4321")


def test_a_description_keeps_its_paragraphs_and_its_list_items():
    """It was stored as one run-on line, and every card showed it that way (owner, 2026-11-02)."""
    page = ('<div class="show-more-less-html__markup"><strong>About us</strong><br><br>We build\n'
            '   things.<p>What you will do:</p><ul><li><p>Write Python</p></li><li>Ship <b>fast</b>'
            '</li></ul><p>Apply today: we answer every application within a week.</p></div>')
    result = postings.fetch_job("https://www.linkedin.com/jobs/view/1",
                                client=FakeClient(FakeResponse(page)))

    assert result.description == ("About us\n\nWe build things.\n\nWhat you will do:\n\n"
                                  "• Write Python\n• Ship fast\n\n"
                                  "Apply today: we answer every application within a week.")


@pytest.mark.parametrize(("given", "laid_out"), [
    # Plain text: its own line breaks are its layout, its runs of space are not.
    ("Line one\r\n\r\n\r\nLine   two", "Line one\n\nLine two"),
    # Escaped markup, as some boards put it in JSON-LD.
    ("&lt;p&gt;Escaped&lt;/p&gt;&lt;ul&gt;&lt;li&gt;one&lt;/li&gt;&lt;/ul&gt;", "Escaped\n\n• one"),
    # The page's own line breaks inside a paragraph are not breaks.
    ("<p>We are\n looking</p>", "We are looking"),
])
def test_every_kind_of_description_reads_as_lines(given, laid_out):
    assert postings.strip_html(given) == laid_out


# The fields of a real answer from No Fluff Jobs' posting API (2026-11-02), cut down.
NOFLUFF = json.dumps({
    "title": "Senior PHP Software Engineer",
    "details": {"description": "<p>Join our team and build <strong>backend</strong> services.</p>"
                               "<p>About the project</p><ul><li>PHP 8.4</li><li>Kafka</li></ul>"},
    "requirements": {"musts": [{"value": "PHP"}, {"value": "Symfony"}], "nices": [{"value": "MongoDB"}],
                     "description": "<ul><li>Five years with PHP</li></ul>"},
    "specs": {"dailyTasks": ["Designing backend services", "Code reviews"]},
    "essentials": {"originalSalary": {"currency": "PLN", "types": {
        "b2b": {"period": "Hour", "range": [100.0, 135.0]}}}},
})


def test_a_no_fluff_jobs_posting_is_read_from_its_api_not_its_script_built_page():
    """Its page held its menus, and they were stored as the text: "JavaScript is required…"."""
    from libs.corpus import salary

    client = FakeClient(FakeResponse(NOFLUFF))
    result = postings.fetch_job("https://nofluffjobs.com/job/senior-php-acme-wroclaw", client=client,
                                snippet="Senior PHP Software Engineer")

    assert client.requested == ["https://nofluffjobs.com/api/posting/senior-php-acme-wroclaw"]
    assert result.description == (
        "Salary: 100 - 135 PLN per hour (B2B)\n\n"
        "Join our team and build backend services.\n\nAbout the project\n\n• PHP 8.4\n• Kafka\n\n"
        "Requirements:\n\n• PHP\n• Symfony\n\n• Five years with PHP\n\n"
        "Nice to have:\n\n• MongoDB\n\nDaily tasks:\n\n• Designing backend services\n• Code reviews")
    assert salary.extract(result.description)["max"] == 135


def test_a_no_fluff_jobs_posting_that_is_gone_is_said_so_by_its_api():
    client = FakeClient(FakeResponse("", 404))
    result = postings.fetch_job("https://nofluffjobs.com/job/gone", client=client, snippet="Senior PHP")
    assert result["accepting"] is False
    assert client.requested == ["https://nofluffjobs.com/api/posting/gone"], "asked once, of the API"


def test_a_throttled_linkedin_fetch_is_retried_and_then_falls_back_to_the_snippet():
    """A job whose fetch fails still has to be scored, and it is said that no answer came."""
    client = FakeClient(*[FakeResponse("", 429) for _ in range(postings.LI_RETRIES)])
    stats: dict[str, int] = {}
    result = postings.fetch_job("https://www.linkedin.com/jobs/view/9", client=client,
                                snippet="teaser", stats=stats)

    assert result.description == "teaser"
    assert result.get("throttled") is True
    assert stats == {"li_http_429": postings.LI_RETRIES, "li_giveup": 1}


def test_a_two_hundred_with_an_empty_body_is_asked_once(monkeypatch):
    """The assumption this test used to encode was wrong, and the live log disproved it."""
    client = FakeClient(*[FakeResponse("<div></div>") for _ in range(postings.LI_RETRIES)])
    stats: dict[str, int] = {}
    result = postings.fetch_job("https://www.linkedin.com/jobs/view/9", client=client,
                                snippet="teaser", stats=stats)

    assert stats["li_empty"] == 1, "one request, not six, for a page that answered"
    assert result.description == "teaser", "the usable snippet still survives"


def test_a_posting_that_is_gone_is_not_asked_six_times():
    """404 is an answer a fresh IP cannot change."""
    client = FakeClient(*[FakeResponse("", 404) for _ in range(postings.LI_RETRIES)])
    stats: dict[str, int] = {}
    postings.fetch_job("https://www.linkedin.com/jobs/view/9", client=client,
                       snippet="teaser", stats=stats)

    assert stats["li_http_404"] == 1


def test_throttling_and_server_errors_keep_every_attempt():
    """The other half of the rule: 429 and 5xx are exactly what the IP rotation exists for."""
    client = FakeClient(*[FakeResponse("", 429) for _ in range(postings.LI_RETRIES)])
    stats: dict[str, int] = {}
    postings.fetch_job("https://www.linkedin.com/jobs/view/9", client=client,
                       snippet="teaser", stats=stats)
    assert stats["li_http_429"] == postings.LI_RETRIES

    client = FakeClient(*[FakeResponse("", 503) for _ in range(postings.LI_RETRIES)])
    stats = {}
    postings.fetch_job("https://www.linkedin.com/jobs/view/9", client=client,
                       snippet="teaser", stats=stats)
    assert stats["li_http_503"] == postings.LI_RETRIES


def test_a_transport_error_never_escapes():
    """The corpus row must survive a proxy that dropped the connection."""
    class Broken(FakeClient):
        def get(self, url, **_):
            raise httpx.ConnectError("proxy went away")

    stats: dict[str, int] = {}
    result = postings.fetch_job("https://example.com/job", client=Broken(), snippet="fallback",
                                stats=stats)
    assert result.description == "fallback"
    assert stats["generic_error"] == 1


# ── the apply target ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    '<a href="https://job-boards.greenhouse.io/acme/jobs/4567">Apply</a>',
    '<a href="https://jobs.lever.co/acme/0123456789abcdef0123">Apply</a>',
    '<a href="https://jobs.ashbyhq.com/acme/0123456789abcdef0123">Apply</a>',
    '<a href="https://apply.workable.com/acme/j/ABC123">Apply</a>',
])
def test_a_known_ats_link_is_extracted(html):
    assert postings.extract_ats_url(html) is not None


def test_an_escaped_href_is_unescaped():
    html = '<a href="https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x?src=1&amp;t=2">Apply</a>'
    assert "&amp;" not in (postings.extract_ats_url(html) or "")


def test_a_linkedin_redirect_resolves_to_the_external_target():
    href = "/jobs/apply?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1"
    assert postings.normalise_apply_url(href) == "https://boards.greenhouse.io/acme/jobs/1"


def test_a_redirect_that_stays_on_linkedin_is_not_an_apply_url():
    """It would be presented to the user as the way to apply, and it is not."""
    assert postings.normalise_apply_url("/jobs/view/1234") is None


def test_an_apply_link_that_does_not_resolve_is_dropped():
    client = FakeClient(FakeResponse("", 404))
    assert postings.resolve_apply_url("https://acme.com/careers/1", client) is None


def test_an_apply_link_that_bounces_back_to_linkedin_is_dropped():
    client = FakeClient(FakeResponse("ok", 200, url="https://www.linkedin.com/jobs/view/1"))
    assert postings.resolve_apply_url("https://acme.com/careers/1", client) is None


def test_the_easy_apply_signal_is_read_from_where_the_application_happens():
    """Not from the button text: the logged-out page rarely says "Easy Apply"."""
    assert postings.parse_easy_apply('<a class="apply-link-onsite">Apply</a>') is True
    assert postings.parse_easy_apply('<a class="apply-link-offsite">Apply</a>') is False
    assert postings.parse_easy_apply("<div>a job</div>") is None


def test_the_apply_signal_ranks_the_top_card_button_above_any_link_saying_apply():
    html = ('<a href="/help">How to apply</a>'
            '<a class="topcard__button" href="/jobs/apply?url=https%3A%2F%2Facme.com%2Fjob">'
            'Apply on company website</a>')
    easy, candidate = postings.parse_apply_signal(html)
    assert easy is False
    assert candidate == "https://acme.com/job"


# ── the metadata refresh ──────────────────────────────────────────────────────────────────────

def test_a_non_linkedin_url_yields_no_metadata_rather_than_a_fetch():
    """Nothing else exposes an applicant count or a repost flag, so the request would be waste."""
    client = FakeClient()
    stats: dict[str, int] = {}
    result = postings.fetch_meta("https://boards.greenhouse.io/acme/jobs/1", client=client,
                                 stats=stats)
    assert client.requested == []
    assert stats == {"non_li": 1}
    assert all(value is None for value in result.values())


# ── a dead proxy is not a dead job ────────────────────────────────────────────────────────────

class ProxyRefusing:
    def get(self, _url, **_):
        raise httpx.ProxyError("407 TRAFFIC_EXHAUSTED")

    def stream(self, *_a, **_k):
        raise httpx.ProxyError("407 TRAFFIC_EXHAUSTED")


def test_a_refusing_proxy_stops_immediately_rather_than_retrying_six_times():
    """"This posting would not load" is worth six retries on a rotating IP."""
    stats: dict[str, int] = {}
    with pytest.raises(postings.ProxyUnusable):
        postings.fetch_job("https://www.linkedin.com/jobs/view/1", client=ProxyRefusing(),
                           stats=stats)
    assert stats == {"li_proxy_error": 1}


def test_the_metadata_refresh_reports_a_refusing_proxy_the_same_way():
    stats: dict[str, int] = {}
    with pytest.raises(postings.ProxyUnusable):
        postings.fetch_meta("https://www.linkedin.com/jobs/view/1", client=ProxyRefusing(),
                            stats=stats)
    assert stats == {"li_proxy_error": 1}


class OneAddressRefusing(FakeClient):
    """A rotating proxy with one address that answers 503 to every connection: every `every`th try."""

    def __init__(self, every: int, *responses: FakeResponse):
        super().__init__(*responses)
        self.every, self.tries = every, 0

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.tries += 1
        if self.tries % self.every == 1 or self.every == 1:
            raise httpx.ProxyError("503 Service Unavailable")
        return super().get(url, **kwargs)


def test_one_proxy_address_refusing_is_tried_again_from_the_next():
    """Two of the proxy's six addresses answered 503 to every connection, and the first refusal stopped
    every LinkedIn text for the run: about four a run were read, 13,800 waited (2026-11-05). A refusal at
    one address says nothing about the next, which the next try leaves from."""
    stats: dict[str, int] = {}
    page = FakeResponse(f'<div class="show-more-less-html__markup">{"Build the payments API. " * 20}</div>')

    posting = postings.fetch_job("https://www.linkedin.com/jobs/view/1", client=OneAddressRefusing(3, page),
                                 stats=stats)
    meta = postings.fetch_meta("https://www.linkedin.com/jobs/view/1",
                               client=OneAddressRefusing(3, FakeResponse(GUEST_FRAGMENT)), stats={})

    assert "payments API" in posting.description and stats == {"li_proxy_error": 1, "li_ok": 1}
    assert meta["applicants"] == 200


def test_a_proxy_refusing_every_try_is_the_proxy_itself():
    """Not this address but all of them: the proxy is down, and no other job will fare better."""
    stats: dict[str, int] = {}
    with pytest.raises(postings.ProxyUnusable):
        postings.fetch_job("https://www.linkedin.com/jobs/view/1", client=OneAddressRefusing(1),
                           stats=stats)
    assert stats == {"li_proxy_error": postings.LI_RETRIES}


def test_an_ordinary_transport_failure_is_still_retried():
    """The distinction has to be narrow: a dropped connection is exactly what the retries exist for."""
    class Flaky:
        def get(self, _url, **_):
            raise httpx.ConnectError("reset by peer")

    stats: dict[str, int] = {}
    postings.fetch_job("https://www.linkedin.com/jobs/view/1", client=Flaky(), snippet="teaser",
                       stats=stats)
    assert stats["li_error"] == postings.LI_RETRIES


def test_a_missing_repost_marker_is_unknown_not_a_denial():
    """The word appears on no page a logged-out fetch can reach."""
    from libs.corpus import postings

    assert postings.parse_reposted('<span class="posted-time-ago__text">Posted 3 days ago</span>') \
        is None
    assert postings.parse_reposted('<span class="posted-time-ago__text">Reposted 2 days ago</span>') \
        is True
    assert postings.parse_reposted("<div>no caption here</div>") is None


def test_the_easy_apply_fetch_is_gone():
    """It streamed 96 KB of a user's paid proxy per posting to read one boolean that is not on the page."""
    from libs.corpus import postings

    assert not hasattr(postings, "fetch_easy_apply")
    assert hasattr(postings, "parse_easy_apply")
