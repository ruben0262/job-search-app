"""The description pass: which rows it picks up, what it writes, and what it refuses to erase."""
from __future__ import annotations

import uuid

import pytest

from libs.corpus import postings
from libs.corpus.repository import CorpusRepository
from libs.db import get
from libs.db.models import CorpusJob
from services.harvester.describe import describe_pending

JD = ('<div class="show-more-less-html__markup">' + "A real description. " * 30 + "</div>")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """LinkedIn's retry loop sleeps two seconds six times."""
    monkeypatch.setattr(postings.time, "sleep", lambda _seconds: None)


@pytest.fixture()
def repo(db):
    return CorpusRepository(db), db


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.url = "https://example.com"


class FakeClient:
    def __init__(self, text: str = JD, status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.requested: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(self.text, self.status_code)


def _linkedin(repo: CorpusRepository, **kwargs) -> int:
    return repo.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                    source="linkedin", title="Engineer", company="Acme", **kwargs)


def _board(repo: CorpusRepository, **kwargs) -> int:
    return repo.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
                    source="greenhouse", title="Engineer", company="Acme", **kwargs)


# ── which rows are picked up ──────────────────────────────────────────────────────────────────

def test_a_row_with_no_description_is_awaiting_one(repo):
    corpus, _session = repo
    job_id = _board(corpus)
    assert job_id in [job.id for job in corpus.awaiting_description()]


def test_a_row_whose_snippet_is_a_teaser_is_picked_up_again(repo):
    """The case the platform was silently getting wrong."""
    corpus, _session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "Join our fast-growing team! " * 5)
    assert job_id in [job.id for job in corpus.awaiting_description()]


def test_a_row_with_a_real_description_is_left_alone(repo):
    corpus, _session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    assert job_id not in [job.id for job in corpus.awaiting_description()]


# A trimmed row used to be excluded here, because retention nulled a description in place and the
# describer would otherwise fetch it again forever. Retention deletes the row now, so there is no
# trimmed state for this pass to skip.


def test_the_two_passes_do_not_take_each_other_s_work(repo):
    """LinkedIn rows can only be filled by a caller holding a proxy lease."""
    corpus, _session = repo
    linkedin_id, board_id = _linkedin(corpus), _board(corpus)

    free = [job.id for job in corpus.awaiting_description(linkedin=False)]
    proxied = [job.id for job in corpus.awaiting_description(linkedin=True)]

    assert board_id in free and linkedin_id not in free
    assert linkedin_id in proxied and board_id not in proxied


# ── what the pass writes ──────────────────────────────────────────────────────────────────────

def test_a_fetched_description_replaces_the_teaser(repo):
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "teaser")

    report = describe_pending(corpus, FakeClient(), linkedin=False)

    assert report.described == 1
    assert "A real description." in get(session, CorpusJob, job_id).description


def test_a_fetch_that_returns_nothing_leaves_the_row_for_the_next_pass(repo):
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "teaser")

    report = describe_pending(corpus, FakeClient("", 429), linkedin=False)

    assert report.described == 0 and report.still_thin == 1
    assert get(session, CorpusJob, job_id).description == "teaser"
    assert job_id in [job.id for job in corpus.awaiting_description(linkedin=False)]


def test_the_outcome_tally_says_why_a_pass_found_nothing(repo):
    """A run that fell back to snippets for everything looks identical from outside to one that worked."""
    corpus, _session = repo
    _board(corpus)

    report = describe_pending(corpus, FakeClient("", 403), linkedin=False)
    assert report.outcomes.get("generic_http_403") == 1


# ── the rule about not erasing ────────────────────────────────────────────────────────────────

def test_a_none_never_overwrites_a_known_value(repo):
    """Pages answer inconsistently — the guest fragment has the applicant count."""
    corpus, session = repo
    job_id = _board(corpus, workplace="onsite", applicants=42)

    corpus.set_posting_metadata(job_id, workplace=None, applicants=None, reposted=True)

    job = get(session, CorpusJob, job_id)
    assert (job.workplace, job.applicants, job.reposted) == ("onsite", 42, True)


def test_metadata_is_kept_even_when_the_description_came_back_thin(repo):
    """An applicant count and a workplace tag are what the card and the filter read."""
    corpus, session = repo
    job_id = _linkedin(corpus)

    fragment = ('<span class="num-applicants__caption"> 12 applicants </span>'
                '<h1>Engineer (Hybrid)</h1>')
    describe_pending(corpus, FakeClient(fragment), linkedin=True)

    job = get(session, CorpusJob, job_id)
    assert (job.applicants, job.workplace) == (12, "hybrid")


def _posting(repo):
    """One LinkedIn row to refresh."""
    corpus, session = repo
    return session, get(session, CorpusJob, _linkedin(corpus))



# ── the freshness check ───────────────────────────────────────────────────────────────────────

class FakeMetaResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.url = "https://www.linkedin.com/jobs/view/1"


class FakeMetaClient:
    """Answers every request with the same page, and records that it was asked."""

    def __init__(self, text: str, status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.requested: list[str] = []

    def get(self, url, **_):
        self.requested.append(url)
        return FakeMetaResponse(self.text, self.status_code)

    def stream(self, _method, url, **_):
        from contextlib import nullcontext
        self.requested.append(url)

        class Streamed(FakeMetaResponse):
            def iter_bytes(inner):
                yield inner.text.encode()

        return nullcontext(Streamed(self.text, self.status_code))


OPEN_POSTING = ('<span class="num-applicants__caption"> 12 applicants </span>'
                '<span class="posted-time-ago__text"> 3 hours ago </span>' + "x" * 300)
CLOSED_POSTING = "<p>No longer accepting applications</p>" + "x" * 300


def test_a_closed_posting_stops_the_build_before_anything_is_spent(repo):
    """The most annoying possible failure is the one where everything succeeds."""
    from services.docgen.refresh import PostingClosed, refresh_before_build

    session, job = _posting(repo)
    with pytest.raises(PostingClosed):
        refresh_before_build(session, job, FakeMetaClient(CLOSED_POSTING))


def test_a_live_posting_lets_the_build_proceed(repo):
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    meta = refresh_before_build(session, job, FakeMetaClient(OPEN_POSTING))
    assert meta["accepting"] is True


def test_a_failed_fetch_never_reads_as_a_closed_job(repo):
    """A proxy hiccup must not present to the user as "that job is gone"."""
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    assert refresh_before_build(session, job, FakeMetaClient("", 429))["accepting"] is None


def test_the_refreshed_metadata_goes_back_to_the_shared_corpus(repo):
    """The user paid for the request. Everyone gets the applicant count."""
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    refresh_before_build(session, job, FakeMetaClient(OPEN_POSTING))
    assert get(session, CorpusJob, job.id).applicants == 12


# ── metadata on rows that already have a description ──────────────────────────────────────────

GUEST = ('<span class="num-applicants__caption"> 42 applicants </span>'
         '<span class="posted-time-ago__text"> 3 hours ago </span>'
         '<h1>Engineer (Hybrid)</h1>' + "x" * 300)


def test_a_linkedin_row_with_a_description_but_no_signals_is_picked_up(repo):
    """A different question from "is it thin", and the corpus was full of the difference."""
    corpus, _session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)

    assert job_id not in [job.id for job in corpus.awaiting_description(linkedin=True)]
    assert job_id in [job.id for job in corpus.awaiting_metadata()]


def test_the_refresh_writes_every_signal_the_page_carries(repo):
    from services.harvester.describe import refresh_metadata

    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)

    report = refresh_metadata(corpus, FakeClient(GUEST), limit=10)

    job = get(session, CorpusJob, job_id)
    assert report.metadata_written >= 1
    assert (job.applicants, job.workplace) == (42, "hybrid")
    assert job.posted_at is not None


def test_a_row_that_was_refreshed_is_not_asked_again(repo):
    """The "never looked" test is the absence of both signals, not a timestamp."""
    from services.harvester.describe import refresh_metadata

    corpus, _session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)

    refresh_metadata(corpus, FakeClient(GUEST), limit=10)
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


def test_a_board_row_is_never_in_the_metadata_queue(repo):
    """Only LinkedIn exposes any of these."""
    corpus, _session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


# ── a closed posting must not reach a feed ────────────────────────────────────────────────────

CLOSED_GUEST = ('<p>No longer accepting applications</p>'
                '<span class="posted-time-ago__text"> 1 month ago </span>' + "x" * 300)


def test_a_closed_posting_is_marked_and_leaves_the_queue(repo):
    """The failure the user saw."""
    from services.harvester.describe import refresh_metadata

    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)

    report = refresh_metadata(corpus, FakeClient(CLOSED_GUEST), limit=10)

    assert report.closed == 1
    assert get(session, CorpusJob, job_id).closed_at is not None
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


def test_marking_closed_keeps_the_row_and_its_description(repo):
    """Not a deletion and not a trim."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)

    corpus.mark_closed(job_id)

    job = get(session, CorpusJob, job_id)
    assert job.closed_at is not None
    assert job.description, "closing a posting must not touch the text somebody may still read"


def test_closing_twice_does_not_move_the_timestamp(repo):
    """A listing that reopens is rare; a timestamp that drifts is worse than a stale one."""
    corpus, session = repo
    job_id = _linkedin(corpus)

    corpus.mark_closed(job_id)
    first = get(session, CorpusJob, job_id).closed_at

    corpus.mark_closed(job_id)
    assert get(session, CorpusJob, job_id).closed_at == first


def test_the_pass_stops_when_it_runs_out_of_time(repo, monkeypatch):
    """A run that outlives its timer stops the next one starting."""
    from services.harvester import describe as describe_module

    corpus, _session = repo
    for _ in range(8):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                   source="linkedin", title="Engineer", company="Acme")

    # A clock that advances a second per reading: the budget below is spent after two fetches.
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(describe_module, "monotonic", lambda: float(next(ticks)))

    class Fetcher:
        def get(self, url, **_kwargs):
            return FakeResponse("<div>" + "x" * 400 + "</div>")

    report = describe_pending(corpus, Fetcher(), linkedin=True, limit=8, budget_seconds=2.5)

    assert report.attempted < 8, "the pass must stop rather than outlive its timer"
    # And nothing was lost: whatever was not described is still waiting for the next pass.
    assert len(corpus.awaiting_description(limit=20, linkedin=True)) == 8 - report.described


def test_no_budget_means_the_pass_runs_to_completion(repo):
    """The shared cycle has its own scheduling and passes no budget."""
    corpus, _session = repo
    for _ in range(3):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                   source="linkedin", title="Engineer", company="Acme")

    class Fast:
        def get(self, url, **_kwargs):
            return FakeResponse("<div>" + "x" * 400 + "</div>")

    report = describe_pending(corpus, Fast(), linkedin=True, limit=3)
    assert report.attempted == 3


# ── a dead posting stops costing us anything (2026-08-07) ─────────────────────────────────────

GONE_BOARD = "<html><body>This job is no longer accepting applications.</body></html>"


def _board(repo: CorpusRepository, **kwargs) -> int:
    return repo.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().int % 10**9}",
                    source="greenhouse", title="Backend Engineer", company="Acme", **kwargs)


def test_a_board_posting_that_says_it_is_closed_is_marked_closed(repo):
    """The describe fetch was always going to happen — reading the answer off it is free."""
    corpus, session = repo
    job_id = _board(corpus)

    report = describe_pending(corpus, FakeClient(GONE_BOARD), limit=10)

    assert get(session, CorpusJob, job_id).closed_at is not None
    assert report.closed == 1


def test_a_board_refusing_us_is_not_a_posting_ending(repo):
    """404 and 410 mean gone; a 429 is the board rate-limiting us and must not close the row."""
    corpus, session = repo
    job_id = _board(corpus)

    describe_pending(corpus, FakeClient("", status_code=429), limit=10)

    assert get(session, CorpusJob, job_id).closed_at is None


def test_a_closed_posting_is_never_fetched_again(repo):
    """On the proxied tier the fetch is the user's own bandwidth."""
    corpus, _session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "x " * 30)
    assert job_id in [row.id for row in corpus.awaiting_description(limit=100)], "thin, so wanted"

    corpus.mark_closed(job_id)

    assert job_id not in [row.id for row in corpus.awaiting_description(limit=100)]
